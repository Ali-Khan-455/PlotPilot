"""Stages 6–8 once every chunk is done: the D17 context check, Prompt 5 (hook) and Prompt 9 on the hook,
the splice check (D20), the assembled script, and the scene metadata file (R5)."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

import anthropic

from plotpilot import assemble, config, db, tracker
from plotpilot.ingest import estimate_tokens
from plotpilot.llm import eprint, log_error
from plotpilot.parse import ParseError, check_hook_tts, parse_hook
from plotpilot.pipeline import ChunkRun, narration_state
from plotpilot.prompts import fill

P5_TARGET = "[paste that exact saved sentence here]"
P5_NARRATION = "[Paste the full assembled Part 1 narration here]"


def _fail(c, msg) -> int:
    eprint(msg)
    log_error(c.llm.log_dir, msg)
    return 1


class NotReady(Exception):
    """The hook, or its Prompt 9 pass, is not stored yet."""


@dataclass(frozen=True)
class Inputs:
    rows: list
    states: list
    bodies: list
    target: str


@dataclass(frozen=True)
class Outputs:
    hook: str
    bodies: list
    scenes_per_chunk: list      # per chunk: [(first_sentence, description)]
    script: str
    lines: list                 # the metadata lines, novel-wide
    warnings: list
    chunk_lines: list           # `lines` split by each chunk's scene count


def derive_inputs(conn, novel_id) -> Inputs:
    """What the final stage needs before any hook exists: the chunk bodies and the D20 target."""
    rows = db.chunks(conn, novel_id)
    states = [narration_state(conn, r["id"]) for r in rows]
    target = json.loads(db.latest_tracker(conn, novel_id))["chunk1"]["target"]
    return Inputs(rows, states, [s.body for s in states], target)


def derive_outputs(conn, novel_id, inputs: Inputs | None = None) -> Outputs:
    """The final script and scene metadata exactly as run_final builds them, read-only from stored passes.
    Shared with Image-Sync. Note: this does NOT run the D20 splice check; callers run it separately."""
    inp = inputs or derive_inputs(conn, novel_id)
    cid = inp.rows[0]["id"]
    hook_pass = db.latest_pass(conn, cid, "hook")
    if not hook_pass:
        raise NotReady("no hook pass is stored")
    hook = parse_hook(hook_pass["output_text"], inp.target)
    tts_pass = db.latest_pass(conn, cid, "hook_tts", after_id=hook_pass["id"])
    if not tts_pass:
        raise NotReady("the hook has no TTS (Prompt 9) pass")
    hook = check_hook_tts(tts_pass["output_text"], hook, inp.target)
    script = assemble.join(hook, inp.bodies)
    scenes = []
    for r in inp.rows:
        sc = tracker.validate_scenes(tracker.extract_json(db.latest_pass(conn, r["id"], "scenes")["output_text"]))
        scenes.append([(s["first_sentence"], s["description"]) for s in sc["scenes"]])
    # scene_lines runs once, novel-wide (per chunk it would restart at [00:00]); then split per chunk.
    lines, warnings = assemble.scene_lines(script, assemble.chunk_starts(hook, inp.bodies), scenes)
    chunk_lines, pos = [], 0
    for sc in scenes:
        chunk_lines.append(lines[pos:pos + len(sc)])
        pos += len(sc)
    return Outputs(hook, inp.bodies, scenes, script, lines, warnings, chunk_lines)


def run_final(conn, llm, prompts, novel_id, slug, title, *, gen_model, qc_model, out_dir) -> int:
    inp = derive_inputs(conn, novel_id)
    rows, states, bodies, target = inp.rows, inp.states, inp.bodies, inp.target
    c = ChunkRun(conn, llm, prompts, novel_id, slug, title, rows[0],
                 gen_model=gen_model, qc_model=qc_model, out_dir=out_dir)
    cid = rows[0]["id"]

    # D20 precondition, before the one-time Prompt 5 call.
    try:
        assemble.target_check(bodies[0], target)
    except assemble.SpliceError as e:
        return _fail(c, f"ERROR: hook splice check failed: {e}")

    hook_pass = db.latest_pass(conn, cid, "hook")
    if not hook_pass:
        llm.check_models([gen_model])
        module = db.latest_pass(conn, cid, "draft")["module"]
        system = f"{prompts['1'].text}\n\n{prompts[f'MODULE {module}'].text}"
        pre_hook = assemble.join(states[0].margin, bodies)
        user = fill(prompts["5"].text, {P5_TARGET: target, P5_NARRATION: pre_hook})
        limit, reason = llm.context_limit(gen_model), ""
        try:
            tokens = llm.count_tokens(gen_model, user, system)
        except anthropic.BadRequestError as e:
            # The counting endpoint may refuse an oversize request itself; any other 400 is surfaced as is.
            if not re.search(r"(?i)prompt is too long|context (?:window|length)|exceeds? the (?:maximum|context)",
                             str(e)):
                raise
            tokens, reason = estimate_tokens(len(user.split()) + len(system.split())), f" (API: {e})"
            limit = -1
        if tokens + config.HOOK_MAX_TOKENS > limit:  # D17
            return _fail(c, f"Part 1 assembled script is {len(pre_hook.split()):,} words (~{tokens:,} tokens). "
                            "Prompt 5 requires the full script as context. Exceeds "
                            f"{gen_model}'s context window. Split the novel into explicit Parts or reduce "
                            f"chunk count.{reason}")
        try:
            c.attempt("hook", gen_model, user, lambda t: parse_hook(t, target), system=system,
                      max_tokens=config.HOOK_MAX_TOKENS, module=module)
        except ParseError as e:
            return _fail(c, f"Hook output was {e}; raw outputs are stored. Re-run to try again.")
        hook_pass = db.latest_pass(conn, cid, "hook")
    hook = parse_hook(hook_pass["output_text"], target)

    # R9: Prompt 9 on the hook. hook_tts is its own kind: "tts" would enter chunk 1's narration fold.
    tts_pass = db.latest_pass(conn, cid, "hook_tts", after_id=hook_pass["id"])
    if not tts_pass:
        llm.check_models([qc_model])
        user = fill(prompts["9"].text, {"[Paste narration here]": hook})
        try:
            c.attempt("hook_tts", qc_model, user, lambda t: check_hook_tts(t, hook, target),
                      max_tokens=config.QC_MAX_TOKENS)
        except ParseError as e:
            return _fail(c, f"Hook TTS normalization output was {e}; raw outputs are stored. Re-run to try again.")
    out = derive_outputs(conn, novel_id, inp)
    hook, script = out.hook, out.script
    try:
        assemble.splice_check(script, target)  # D20
    except assemble.SpliceError as e:
        return _fail(c, f"ERROR: hook splice check failed: {e}")
    script_path = Path(out_dir) / slug / "script.txt"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    if script_path.exists() and script_path.read_text(encoding="utf-8") != script:
        print(f"WARNING: {script_path.name} differed from the stored narration and was regenerated. The script "
              "is rebuilt from the database on every run; edits to it are not kept.")
    script_path.write_text(script, encoding="utf-8")

    lines, warnings = out.lines, out.warnings
    for w in warnings:
        print(f"WARNING: {w}")  # printed only: the file is rebuilt every run (R22)
    meta_path = Path(config.METADATA_DIR) / f"{slug}.txt"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta = "".join(f"{line}\n" for line in lines)
    if meta_path.exists() and meta_path.read_text(encoding="utf-8") != meta:
        print(f"WARNING: {meta_path} differed from the stored scenes and was regenerated; edits to it are not kept.")
    meta_path.write_text(meta, encoding="utf-8")

    words = len(script.split())
    minutes = max(1, round(words / config.WORDS_PER_MINUTE))
    print(f"Hook: {hook}")
    print(f"Script → {script_path} ({words:,} words, ~{minutes} minute{'' if minutes == 1 else 's'} "
          f"at {config.WORDS_PER_MINUTE} wpm).")
    print(f"Metadata → {meta_path} ({len(lines)} scenes).")
    return 0
