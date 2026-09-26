"""Stage 4: per-chunk QC — Prompt 6 audit, Prompt 7 fact-check (gate), Prompt 8 texture,
Prompt 9 TTS normalization, deterministic TTS check. Resumes from chunks.status."""

import getpass
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import anthropic

from plotpilot import config, db, tracker
from plotpilot.llm import LLMError, log_error
from plotpilot.parse import (ParseError, check_rewrite, count_sentences, first_sentence, parse_audit, parse_draft,
                             parse_factcheck)
from plotpilot.pipeline import TEXT_KINDS, narration_state
from plotpilot.prompts import fill
from plotpilot.tts_check import tts_hazards

SOURCE = "[Paste source text for this chunk]"
NARRATION = "[Paste narration for this chunk]"
TRACKER = "[Paste Continuity Tracker]"
READ_ALOUD = ("Chunk complete. Recommended next step: read aloud at 2x for tone and texture drift "
              "before continuing.")
MAX_TTS_WARNINGS = 20


def _write_log(c, name, text):
    folder = Path(config.LOG_DIR) / c.slug
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"chunk-{c.chunk['idx']:02d}-{name}.md").write_text(text, encoding="utf-8")
    return folder / f"chunk-{c.chunk['idx']:02d}-{name}.md"


def _latest_after_draft(c, kind):
    draft = db.latest_pass(c.conn, c.chunk["id"], "draft")
    return db.latest_pass(c.conn, c.chunk["id"], kind, after_id=draft["id"])


def _sync_logs(c):
    """Rewrite the audit and fact-check logs from the DB, so a crash after an insert can't leave them stale."""
    paths = {}
    for kind, name in (("audit", "audit"), ("factcheck", "factcheck")):
        row = _latest_after_draft(c, kind)
        if row:
            paths[name] = _write_log(c, name, row["output_text"])
    return paths


def _print_tts(text):
    hazards = tts_hazards(text)
    for line, kind, excerpt in hazards[:MAX_TTS_WARNINGS]:
        print(f"WARNING: TTS hazard line {line} ({kind}): …{excerpt}…")
    if len(hazards) > MAX_TTS_WARNINGS:
        print(f"… and {len(hazards) - MAX_TTS_WARNINGS} more")


def _print_gate(c):
    fc = parse_factcheck(_latest_after_draft(c, "factcheck")["output_text"])
    log_path = _sync_logs(c)["factcheck"]
    file_text = c.path.read_text(encoding="utf-8") if c.path.exists() else ""
    print(f"FACT-CHECK FAIL for chunk {c.chunk['idx']} (full output: {log_path})")
    for flag in fc.flags:
        where = ""
        for quoted in re.findall(r'["“]([^"”]{4,})["”]', flag):
            i = file_text.find(quoted)
            if i >= 0:
                where = f"[line {file_text[:i].count(chr(10)) + 1}] "
                break
        print(f"  {where}{flag}")
    if not fc.flags:
        print("  (no individual flags parsed — see the full output file)")
    print(f"Fix the flagged lines in {c.path} and re-run,\n"
          'or accept with --accept-factcheck="<reason>".')
    return fc.flags


def _user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # no user name in some containers
        return "unknown"


def _override(c, reason):
    fc = parse_factcheck(_latest_after_draft(c, "factcheck")["output_text"])
    flags = " || ".join(fc.flags) if fc.flags else "(no flags parsed)"
    Path(config.LOG_DIR).mkdir(parents=True, exist_ok=True)
    with open(Path(config.LOG_DIR) / "factcheck-overrides.log", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat()} | {c.slug} | chunk {c.idx} | "
                f"{_user()} | {' '.join(reason.split())} | {' '.join(flags.split())}\n")
    db.add_pass(c.conn, c.novel_id, c.chunk["id"], "factcheck_override", None, "", flags, note=reason,
                new_status="checked")
    print(f"Fact-check FAIL accepted by operator: {reason}")


def _malformed(c, what, e):
    msg = f"Chunk {c.idx} {what} output was {e}; raw outputs are stored. Re-run to try again."
    print(msg)
    log_error(c.llm.log_dir, msg)
    return 1


BODY_KINDS = tuple(k for k in TEXT_KINDS if k != "margin_repair")  # P10/P11 read the body only


def _latest_body_change(c):
    rows = [r for r in db.ok_passes(c.conn, c.chunk["id"], BODY_KINDS)]
    return rows[-1]["id"] if rows else 0


def _pending_path(c, delta_pass_id) -> Path:
    return Path(config.TRACKER_DIR) / f"{c.slug}.chunk-{c.idx:02d}.delta-{delta_pass_id}.pending.json"


def _pass_delta(c, pass_id):
    row = c.conn.execute("SELECT output_text FROM passes WHERE id = ?", (pass_id,)).fetchone()
    return tracker.validate_delta(tracker.extract_json(row["output_text"])) if row else None


def _current_tracker(c) -> dict:
    raw = db.latest_tracker(c.conn, c.novel_id)
    return json.loads(raw) if raw else tracker.empty()


def _tracker_gate(c):
    """Bind the pending file to the latest ok delta pass, clear any other pending file, print the summary."""
    d = db.latest_pass(c.conn, c.chunk["id"], "tracker_delta")
    bound = _pending_path(c, d["id"])
    for old in sorted(Path(config.TRACKER_DIR).glob(f"{c.slug}.chunk-*.delta-*.pending.json")):
        if old == bound:
            continue
        m = re.match(rf"{re.escape(c.slug)}\.chunk-(\d+)\.delta-(\d+)\.pending\.json$", old.name)
        if not m:
            continue  # not a file this pipeline wrote
        if int(m[1]) == c.idx:
            try:
                edited = json.loads(old.read_text(encoding="utf-8-sig")) != _pass_delta(c, int(m[2]))
            except (ValueError, ParseError):
                edited = True
            if edited:
                print(f"Note: your edits to {old} were superseded by a new tracker delta.")
        old.unlink()
    delta = tracker.validate_delta(tracker.extract_json(d["output_text"]))
    if not bound.exists():
        bound.parent.mkdir(parents=True, exist_ok=True)
        bound.write_text(json.dumps(delta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Tracker update for chunk {c.idx}: {len(delta['new_characters'])} new characters, "
          f"{len(delta['new_terms'])} terms, {len(delta['new_comparisons'])} comparisons, "
          f"{len(delta['new_texture_motifs'])} texture motifs.")
    print(f"Chunk end state: {delta['chunk_end_state']}")
    for col in delta["nickname_collisions"] + tracker.merge_collisions(_current_tracker(c), delta):
        print(f"WARNING: nickname collision: {col}")
    print(f"Review/edit {bound}, then re-run with --accept-tracker to merge.")


def _accept_tracker(c) -> bool:
    d = db.latest_pass(c.conn, c.chunk["id"], "tracker_delta")
    bound = _pending_path(c, d["id"])
    if not bound.exists():
        print(f"Pending file {bound} not found; re-run without --accept-tracker to regenerate it.")
        return False
    try:
        delta = tracker.validate_delta(json.loads(bound.read_text(encoding="utf-8-sig")))
    except (ValueError, ParseError) as e:
        print(f"Pending file {bound} is invalid: {e}")
        return False
    merged = tracker.merge(_current_tracker(c), delta, c.idx)
    if c.first:
        state = narration_state(c.conn, c.chunk["id"])
        merged["chunk1"] = {"margin": state.margin, "margin_sentences": _margin_sentences(c, state.margin),
                            "target": first_sentence(state.body)}
    db.add_tracker_version(c.conn, c.novel_id, c.chunk["id"], json.dumps(merged, ensure_ascii=False),
                           json.dumps(delta, ensure_ascii=False), new_status="done")
    bound.unlink()
    write_mirror(c.conn, c.novel_id, c.slug, c.title)
    print(f"Tracker updated for chunk {c.idx}.")
    return True


def _margin_sentences(c, margin) -> int:
    """The margin length as the AI wrote it (spec); counted only when a repair or edit changed the margin."""
    d = parse_draft(db.latest_pass(c.conn, c.chunk["id"], "draft")["output_text"])
    return d.margin_sentences if " ".join(d.margin.split()) == " ".join(margin.split()) else count_sentences(margin)


def write_mirror(conn, novel_id, slug, title):
    """Rewrite trackers/<slug>.md from the latest accepted version (a derived, read-only file)."""
    latest = db.latest_tracker_row(conn, novel_id)
    if not latest:
        return
    rows = db.chunks(conn, novel_id)
    overrides = conn.execute(
        "SELECT c.idx, p.note FROM passes p JOIN chunks c ON c.id = p.chunk_id"
        " WHERE p.novel_id = ? AND p.kind = 'factcheck_override' ORDER BY p.id", (novel_id,)).fetchall()
    text = tracker.render(json.loads(latest["json"]), title=title, chunks=[(r["idx"], r["label"]) for r in rows],
                          progress=tracker.progress(rows, latest["idx"]),
                          overrides=[(r["idx"], r["note"]) for r in overrides])
    path = Path(config.TRACKER_DIR) / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run(c, *, accept=None, edited=False, accept_tracker=False) -> int:
    P, src = c.P, c.chunk["source_text"]
    if accept is not None and c.status != "factcheck_failed":
        print(f"Note: --accept-factcheck ignored; your edit to {c.path.name} will be fact-checked first."
              if edited else f"Note: --accept-factcheck ignored; chunk {c.idx} has no failed fact-check.")
        accept = None
    texture_failed = False
    while True:
        status = c.status
        body = narration_state(c.conn, c.chunk["id"]).body

        if status == "drafted":
            c.llm.check_models([c.qc_model])
            user = fill(P["6"].text, {SOURCE: src, NARRATION: body, TRACKER: c.prompt_tracker()})
            try:
                out = c.attempt("audit", c.qc_model, user, lambda t: (parse_audit(t), t),
                                max_tokens=config.QC_MAX_TOKENS, status_for=lambda _: "audited")
            except ParseError as e:
                return _malformed(c, "audit", e)
            _write_log(c, "audit", out[1])

        elif status == "audited":
            c.llm.check_models([c.qc_model])
            user = fill(P["7"].text, {SOURCE: src, NARRATION: body})
            try:
                out = c.attempt("factcheck", c.qc_model, user, lambda t: (parse_factcheck(t), t),
                                max_tokens=config.QC_MAX_TOKENS,
                                status_for=lambda p: "checked" if p[0].passed else "factcheck_failed")
            except ParseError as e:
                return _malformed(c, "fact-check", e)
            _write_log(c, "factcheck", out[1])

        elif status == "factcheck_failed":
            if accept is None:
                _print_gate(c)
                return 0
            _override(c, accept)
            accept = None

        elif status == "checked":
            audit = _latest_after_draft(c, "audit")
            gaps = parse_audit(audit["output_text"])
            # An operator edit after the audit settles texture: never regenerate the operator's text.
            edited_since = db.latest_pass(c.conn, c.chunk["id"], "operator_edit", after_id=audit["id"])
            if gaps and not texture_failed and not edited_since and not _latest_after_draft(c, "texture"):
                c.llm.check_models([c.gen_model])
                user = fill(P["8"].text, {"[Paste flat lines here]": body, TRACKER: c.prompt_tracker()})
                try:
                    c.attempt("texture", c.gen_model, user, lambda t: check_rewrite(t, body),
                              max_tokens=config.GEN_MAX_TOKENS, status_for=lambda _: "audited")
                    c.write()  # the file always holds the text Prompt 7 is about to check
                    continue  # the textured body goes back through Prompt 7 (R4)
                except (ParseError, LLMError, anthropic.AnthropicError) as e:
                    if not isinstance(e, ParseError):
                        log_error(c.llm.log_dir, f"texture {type(e).__name__}: {e}")
                    print(f"WARNING: texture repair failed ({e}); keeping the current narration.")
                    texture_failed = True
            c.llm.check_models([c.qc_model])
            user = fill(P["9"].text, {"[Paste narration here]": body})
            try:
                new = c.attempt("tts", c.qc_model, user,
                                lambda t: check_rewrite(t, body, min_ratio=config.TTS_MIN_RATIO),
                                max_tokens=config.NORMALIZE_MAX_TOKENS, status_for=lambda _: "normalized")
            except ParseError as e:
                return _malformed(c, "TTS normalization", e)
            c.write()
            _print_tts(new)

        elif status == "normalized":
            c.write()
            _sync_logs(c)
            print(READ_ALOUD)
            print(f"Chunk {c.idx} QC complete → {c.path}.")
            changed = _latest_body_change(c)
            d = db.latest_pass(c.conn, c.chunk["id"], "tracker_delta")
            if not d or d["id"] < changed:
                c.llm.check_models([c.qc_model])
                user = fill(P["10"].text, {"[paste chunk number]": str(c.idx), TRACKER: c.prompt_tracker(),
                                           "[Paste finished narration for this chunk]": body})
                try:
                    c.attempt("tracker_delta", c.qc_model, user,
                              lambda t: tracker.validate_delta(tracker.extract_json(t)),
                              max_tokens=config.QC_MAX_TOKENS)
                except ParseError as e:
                    return _malformed(c, "tracker", e)
            sc = db.latest_pass(c.conn, c.chunk["id"], "scenes")
            if not sc or sc["id"] < changed:
                c.llm.check_models([c.qc_model])
                user = fill(P["11"].text, {"[Paste finished narration for this chunk]": body})
                try:
                    c.attempt("scenes", c.qc_model, user,
                              lambda t: tracker.validate_scenes(tracker.extract_json(t)),
                              max_tokens=config.QC_MAX_TOKENS)
                except ParseError as e:
                    return _malformed(c, "scenes", e)
            db.set_status(c.conn, c.chunk["id"], "tracker_pending")

        elif status == "tracker_pending":
            if not accept_tracker:
                _print_tts(body)  # shown again on every rerun at the gate
                _tracker_gate(c)
                return 0
            accept_tracker = False
            if not _accept_tracker(c):
                return 1

        elif status == "done":
            return 0
        else:
            raise RuntimeError(f"unknown chunk status {status!r}")
