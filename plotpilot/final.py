"""Stages 6–8 once every chunk is done: the D17 context check, Prompt 5 (hook) and Prompt 9 on the hook,
the splice check (D20), the assembled script, and the scene metadata file (R5)."""

import json
import re
from pathlib import Path

import anthropic

from plotpilot import assemble, config, db, tracker
from plotpilot.ingest import estimate_tokens
from plotpilot.llm import log_error
from plotpilot.parse import ParseError, check_hook_tts, parse_hook
from plotpilot.pipeline import ChunkRun, narration_state
from plotpilot.prompts import fill

P5_TARGET = "[paste that exact saved sentence here]"
P5_NARRATION = "[Paste the full assembled Part 1 narration here]"


def _fail(c, msg) -> int:
    print(msg)
    log_error(c.llm.log_dir, msg)
    return 1


def run_final(conn, llm, prompts, novel_id, slug, title, *, gen_model, qc_model, out_dir) -> int:
    rows = db.chunks(conn, novel_id)
    c = ChunkRun(conn, llm, prompts, novel_id, slug, title, rows[0],
                 gen_model=gen_model, qc_model=qc_model, out_dir=out_dir)
    cid = rows[0]["id"]
    states = [narration_state(conn, r["id"]) for r in rows]
    bodies = [s.body for s in states]
    target = json.loads(db.latest_tracker(conn, novel_id))["chunk1"]["target"]

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
            if not re.search(r"(?i)too long|context|exceed", str(e)):
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
        tts_pass = db.latest_pass(conn, cid, "hook_tts", after_id=hook_pass["id"])
    hook = check_hook_tts(tts_pass["output_text"], hook, target)

    script = assemble.join(hook, bodies)
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

    scenes = []
    for r in rows:
        sc = tracker.validate_scenes(tracker.extract_json(db.latest_pass(conn, r["id"], "scenes")["output_text"]))
        scenes.append([(s["first_sentence"], s["description"]) for s in sc["scenes"]])
    lines, warnings = assemble.scene_lines(script, assemble.chunk_starts(hook, bodies), scenes)
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
