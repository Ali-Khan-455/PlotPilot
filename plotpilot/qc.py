"""Stage 4: per-chunk QC — Prompt 6 audit, Prompt 7 fact-check (gate), Prompt 8 texture,
Prompt 9 TTS normalization, deterministic TTS check. Resumes from chunks.status."""

import getpass
import re
from datetime import datetime, timezone
from pathlib import Path

import anthropic

from plotpilot import config, db
from plotpilot.llm import LLMError, log_error
from plotpilot.parse import ParseError, check_rewrite, parse_audit, parse_factcheck
from plotpilot.pipeline import narration_state
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


def _print_gate(c):
    fc = parse_factcheck(_latest_after_draft(c, "factcheck")["output_text"])
    log_path = Path(config.LOG_DIR) / c.slug / f"chunk-{c.chunk['idx']:02d}-factcheck.md"
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


def _override(c, reason):
    fc = parse_factcheck(_latest_after_draft(c, "factcheck")["output_text"])
    flags = " || ".join(fc.flags) if fc.flags else "(no flags parsed)"
    Path(config.LOG_DIR).mkdir(parents=True, exist_ok=True)
    with open(Path(config.LOG_DIR) / "factcheck-overrides.log", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat()} | {c.slug} | chunk {c.chunk['idx']} | "
                f"{getpass.getuser()} | {reason} | {flags}\n")
    db.add_pass(c.conn, c.novel_id, c.chunk["id"], "factcheck_override", None, "", flags, note=reason,
                new_status="checked")
    print(f"Fact-check FAIL accepted by operator: {reason}")


def _malformed(c, what, e):
    print(f"Chunk {c.chunk['idx']} {what} output was {e}; raw outputs are stored. Re-run to try again.")
    return 1


def run(c, *, accept=None, edited=False) -> int:
    P, src = c.P, c.chunk["source_text"]
    if accept is not None and c.status != "factcheck_failed":
        print("Note: --accept-factcheck ignored; your edit to chunk-01.txt will be fact-checked first."
              if edited else "Note: --accept-factcheck ignored; chunk 1 has no failed fact-check.")
        accept = None
    texture_failed = False
    while True:
        status = c.status
        body = narration_state(c.conn, c.chunk["id"]).body

        if status == "drafted":
            c.llm.check_models([c.qc_model])
            user = fill(P["6"].text, {SOURCE: src, NARRATION: body, TRACKER: config.EMPTY_TRACKER})
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
            gaps = parse_audit(_latest_after_draft(c, "audit")["output_text"])
            if gaps and not texture_failed and not _latest_after_draft(c, "texture"):
                c.llm.check_models([c.gen_model])
                user = fill(P["8"].text, {"[Paste flat lines here]": body, TRACKER: config.EMPTY_TRACKER})
                try:
                    c.attempt("texture", c.gen_model, user, lambda t: check_rewrite(t, body),
                              max_tokens=config.GEN_MAX_TOKENS, status_for=lambda _: "audited")
                    continue  # the textured body goes back through Prompt 7 (R4)
                except (ParseError, LLMError, anthropic.AnthropicError) as e:
                    if not isinstance(e, ParseError):
                        log_error(c.llm.log_dir, f"texture {type(e).__name__}: {e}")
                    print(f"WARNING: texture repair failed ({e}); keeping the current narration.")
                    texture_failed = True
            c.llm.check_models([c.qc_model])
            user = fill(P["9"].text, {"[Paste narration here]": body})
            try:
                new = c.attempt("tts", c.qc_model, user, lambda t: check_rewrite(t, body),
                                max_tokens=config.NORMALIZE_MAX_TOKENS, status_for=lambda _: "normalized")
            except ParseError as e:
                return _malformed(c, "TTS normalization", e)
            c.write()
            hazards = tts_hazards(new)
            for line, kind, excerpt in hazards[:MAX_TTS_WARNINGS]:
                print(f"WARNING: TTS hazard line {line} ({kind}): …{excerpt}…")
            if len(hazards) > MAX_TTS_WARNINGS:
                print(f"… and {len(hazards) - MAX_TTS_WARNINGS} more")

        elif status == "normalized":
            c.write()
            print(READ_ALOUD)
            print(f"Chunk {c.chunk['idx']} QC complete → {c.path}. Tracker update is not built yet (Phase 4).")
            return 0
        else:
            raise RuntimeError(f"unknown chunk status {status!r}")
