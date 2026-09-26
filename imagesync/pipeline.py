"""Stage 0: the beats call, the retry-once pattern, and the CLI-facing run functions.

Resolved facts a fresh validation computes — each beat's (scene_index, suffix) identity and its final
`continues` value — are persisted once, at validation time, into the pass's existing `note` column as
JSON, and every later read (`current_beats`) comes from that JSON, never from re-validating: reloading
would otherwise mean re-running validation against freshly recomputed continuity context, and a stored
revision would have no way to recover which duplicate-timecode occurrence it targeted."""

import json
import re
import sys

import anthropic

from imagesync import config, db
from imagesync.beats import (REVISE_RE, Beat, ParseError, cadence_warnings, extract_json, fold,
                             validate_fresh_beats, validate_revision)
from plotpilot import config as pp_config
from plotpilot.ingest import estimate_tokens
from plotpilot.llm import LLMError, log_error
from plotpilot.prompts import fill

BADREQUEST_RE = re.compile(r"(?i)prompt is too long|context (?:window|length)|exceeds? the (?:maximum|context)")


def _fail(msg: str) -> int:
    print(msg, file=sys.stderr)
    log_error(config.LOG_DIR, msg)
    return 1


def _stamp(sec: int) -> str:
    return f"{sec // 60:02d}:{sec % 60:02d}"


def _seconds(timecode: str) -> int:
    m, s = timecode.split("-")
    return int(m) * 60 + int(s)


def _metadata_block(chunk) -> str:
    return "\n".join(f"[{_stamp(_seconds(tc))}] SCENE: {desc}" for tc, desc in chunk.scenes)


def _identity_note(beats) -> str:
    return json.dumps({"identity": [{"scene_index": b.scene_index, "suffix": b.suffix,
                                     "continues": b.continues} for b in beats]})


def _beat_from_row(item, idn, chunk) -> Beat:
    return Beat(idn["scene_index"], idn["suffix"], chunk.scenes[idn["scene_index"]][0], item["narration"],
               [(d["clause"], d["chapter"]) for d in item["detail"]], idn["continues"])


def current_beats(conn, chunk_row, chunk) -> list[Beat] | None:
    """Reconstructs the chunk's beats straight from stored passes — a pure read, never a re-validation."""
    p = db.latest_pass(conn, chunk_row["id"], "beats")
    if p is None:
        return None
    raw_items = extract_json(p["output_text"])["beats"]
    identity = json.loads(p["note"])["identity"]
    if len(raw_items) != len(identity):
        raise ParseError("stored beats output_text and note disagree in length (corrupted pass)")
    base = [_beat_from_row(item, idn, chunk) for item, idn in zip(raw_items, identity)]
    revisions = []
    for r in db.ok_passes(conn, chunk_row["id"], ["beat_revision"], after_id=p["id"]):
        r_item = extract_json(r["output_text"])["beats"][0]
        revisions.append(_beat_from_row(r_item, json.loads(r["note"]), chunk))
    return fold(base, revisions)


def _continues_context(conn, novel_id, prev_idx) -> tuple[str | None, bool]:
    """The previous chunk's last continuity-log entry as one pre-rendered line, and whether it exists."""
    bible = db.latest_bible(conn, novel_id)
    if bible is None or bible["chunk_idx"] != prev_idx:
        return None, False
    log = json.loads(bible["json"]).get("continuity_log", [])
    if not log:
        return None, False
    return log[-1], True


def _previous_last_ref(conn, prev_row, prev_chunk) -> str | None:
    beats = current_beats(conn, prev_row, prev_chunk)
    return (beats[-1].timecode + beats[-1].suffix) if beats else None


def _context_for(conn, novel_id, src, chunk):
    """(entry, has_continuity, previous_last_ref) for this chunk's Stage 0 / revision call."""
    if chunk.idx == 1:
        return None, False, None
    rows = db.chunks(conn, novel_id)
    # chunk.idx is 1-based; idx - 2 is the previous chunk's 0-based position. Only reached when
    # chunk.idx > 1 — for chunk 1 this would be -1 (Python's last-row wraparound), which is why chunk 1
    # is handled above and this line must never execute when chunk.idx == 1.
    prev_row, prev_chunk = rows[chunk.idx - 2], src.chunks[chunk.idx - 2]
    entry, has_continuity = _continues_context(conn, novel_id, chunk.idx - 1)
    previous_last_ref = _previous_last_ref(conn, prev_row, prev_chunk)
    return entry, has_continuity, previous_last_ref


def _check_context(llm, gen_model, user, *, max_tokens, chunk_idx) -> str | None:
    limit, reason = llm.context_limit(gen_model), ""
    try:
        tokens = llm.count_tokens(gen_model, user, None)
    except anthropic.BadRequestError as e:
        if not BADREQUEST_RE.search(str(e)):
            raise
        tokens, reason = estimate_tokens(len(user.split())), f" (API: {e})"
        limit = -1
    if tokens + max_tokens > limit:
        return (f"Chunk {chunk_idx}'s Stage 0 input is ~{tokens:,} tokens, exceeding {gen_model}'s context "
                f"window ({limit:,} tokens). Try a larger-context --gen-model.{reason}")
    return None


def attempt(llm, conn, novel_id, chunk_id, kind, model, user, parse, *, max_tokens, slug, chunk_idx,
           note=None, note_from=None, status_for=None):
    """Call, parse, insert the pass once with its verdict. Retry once on ParseError; store STOPPED:<reason>
    on a non-end_turn stop and re-raise; raise ParseError("malformed twice ...") on a second failure."""
    last = None
    for _ in range(2):
        try:
            text = llm.call(kind, model, user, max_tokens=max_tokens, slug=slug, chunk_idx=chunk_idx)
        except LLMError as e:
            if e.stop_reason:
                db.add_pass(conn, novel_id, chunk_id, kind, model, user, e.text,
                            verdict=f"STOPPED:{e.stop_reason}", note=note)
            raise
        try:
            parsed = parse(text)
        except ParseError as e:
            db.add_pass(conn, novel_id, chunk_id, kind, model, user, text, verdict="PARSE_FAILED", note=note)
            last = e
            continue
        db.add_pass(conn, novel_id, chunk_id, kind, model, user, text,
                    note=note_from(parsed) if note_from else note,
                    new_status=status_for(parsed) if status_for else None)
        return parsed
    raise ParseError(f"malformed twice ({last})")


def _boundary_seconds(src, chunk):
    if chunk.idx < len(src.chunks):
        return _seconds(src.chunks[chunk.idx].scenes[0][0])
    return src.script_words * 60 // pp_config.WORDS_PER_MINUTE


def _data_sections(spec, chunk, src, entry, has_continuity, extra=None) -> str:
    """extra, when given, is inserted right after spec.mode_a_line — used by run_revise for the
    target beat's own current JSON, so the model has something unambiguous to anchor a revision to."""
    sections = [spec.mode_a_line]
    if extra is not None:
        sections.append(extra)
    if has_continuity:
        sections.append(spec.continuity_log_header + "\n" + entry)
    sections.append(chunk.chapters_text)
    sections.append(_metadata_block(chunk))
    sections.append((src.hook + "\n\n" if chunk.idx == 1 else "") + chunk.narration)
    return "\n\n---\n\n".join(sections)


def run_stage0(conn, llm, spec, src, novel_id, chunk_row, chunk, *, gen_model, slug) -> int:
    existing = current_beats(conn, chunk_row, chunk)
    if existing is None:
        entry, has_continuity, previous_last_ref = _context_for(conn, novel_id, src, chunk)
        stage0 = "\n\n".join([spec.stages["STAGE 0"].text, spec.contracts.override, spec.contracts.stage0])
        user = stage0 + "\n\n---\n\n" + _data_sections(spec, chunk, src, entry, has_continuity)
        err = _check_context(llm, gen_model, user, max_tokens=config.BEATS_MAX_TOKENS, chunk_idx=chunk.idx)
        if err:
            return _fail(err)
        try:
            existing = attempt(
                llm, conn, novel_id, chunk_row["id"], "beats", gen_model, user,
                lambda t: validate_fresh_beats(extract_json(t), chunk_scenes=chunk.scenes,
                                               has_continuity=has_continuity,
                                               previous_last_ref=previous_last_ref),
                max_tokens=config.BEATS_MAX_TOKENS, slug=slug, chunk_idx=chunk.idx,
                note_from=_identity_note, status_for=lambda _: "beats")
        except ParseError as e:
            return _fail(f"Chunk {chunk.idx}'s beats output was {e}; raw outputs are stored. "
                        "Re-run to try again.")
    scene_seconds = [_seconds(tc) for tc, _ in chunk.scenes]
    for w in cadence_warnings(existing, scene_seconds, _boundary_seconds(src, chunk)):
        print(w)
    print(f"Chunk {chunk.idx} beats stored ({len(existing)} beats). Stage 1 (references) is not built yet (IS-3).")
    return 0


def run_revise(conn, llm, spec, src, novel_id, chunk_row, chunk, revise_arg: str, description: str, *,
              gen_model, slug) -> int:
    m = REVISE_RE.match(revise_arg)
    if not m:
        return _fail(f"--revise-beat: {revise_arg!r} doesn't look like a timecode (04-15, 04-15_2, 04-15a).")
    base, occurrence, suffix = m[1], int(m[2] or 1), m[3] or ""
    occurrences = [i for i, (tc, _) in enumerate(chunk.scenes) if tc == base]
    if occurrence > len(occurrences):
        return _fail(f"--revise-beat: chunk {chunk.idx} has no occurrence {occurrence} of {base}.")
    scene_index = occurrences[occurrence - 1]
    beats = current_beats(conn, chunk_row, chunk)
    if beats is None:
        return _fail(f"--revise-beat: chunk {chunk.idx} has no beats yet; run Stage 0 first.")
    target = next((b for b in beats if (b.scene_index, b.suffix) == (scene_index, suffix)), None)
    if target is None:
        return _fail(f"--revise-beat: no beat {revise_arg!r} in chunk {chunk.idx}.")
    entry, has_continuity, previous_last_ref = _context_for(conn, novel_id, src, chunk)
    is_first_beat = (target.scene_index, target.suffix) == (beats[0].scene_index, beats[0].suffix)
    stage0_text = fill(spec.stages["STAGE 0"].text,
                       {"[timecode]": target.timecode + target.suffix, "[new description]": description})
    anchor = json.dumps({"timecode": target.timecode + target.suffix, "narration": target.narration,
                        "detail": [{"clause": c, "chapter": ch} for c, ch in target.detail],
                        "continues": target.continues})
    prompt = "\n\n".join([stage0_text, spec.contracts.override, spec.contracts.stage0])
    user = prompt + "\n\n---\n\n" + _data_sections(spec, chunk, src, entry, has_continuity, extra=anchor)
    err = _check_context(llm, gen_model, user, max_tokens=config.BEATS_MAX_TOKENS, chunk_idx=chunk.idx)
    if err:
        return _fail(err)
    try:
        attempt(llm, conn, novel_id, chunk_row["id"], "beat_revision", gen_model, user,
               lambda t: validate_revision(extract_json(t), target=target, has_continuity=has_continuity,
                                          previous_last_ref=previous_last_ref, is_first_beat=is_first_beat),
               max_tokens=config.BEATS_MAX_TOKENS, slug=slug, chunk_idx=chunk.idx,
               note_from=lambda b: json.dumps({"scene_index": b.scene_index, "suffix": b.suffix,
                                              "continues": b.continues}))
    except ParseError as e:
        return _fail(f"Chunk {chunk.idx}'s revision output was {e}; raw outputs are stored. "
                    "Re-run to try again.")
    print(f"Beat {revise_arg} revised. Stage 1 (references) is not built yet (IS-3).")
    return 0
