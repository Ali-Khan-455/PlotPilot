"""Stage 0: the beats call, the retry-once pattern, and the CLI-facing run functions.

Resolved facts a fresh validation computes — each beat's (scene_index, suffix) identity and its final
`continues` value — are persisted once, at validation time, into the pass's existing `note` column as
JSON, and every later read (`current_beats`) comes from that JSON, never from re-validating: reloading
would otherwise mean re-running validation against freshly recomputed continuity context, and a stored
revision would have no way to recover which duplicate-timecode occurrence it targeted."""

import csv
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic

from imagesync import bible, config, db
from imagesync.beats import (REVISE_RE, Beat, ParseError, cadence_warnings, extract_json, fold,
                             validate_fresh_beats, validate_revision)
from imagesync.bible import TYPE_TO_CATEGORY, _norm
from imagesync.compose import (build_manifest_rows, check_refs, check_shot_cadence, check_wide_under_9_16,
                               compose_prompt, expected_filenames, validate_stage2)
from plotpilot import config as pp_config
from plotpilot.ingest import estimate_tokens
from plotpilot.llm import LLMError, log_error
from plotpilot.prompts import fill

EMPTY_DELTA = {"new_references": [], "bible_update": {"characters": [], "locations": [], "objects": []}}
REGENERATE_RE = re.compile(r"^#(?P<tag>[A-Za-z][A-Za-z0-9]*):\s*(?P<reason>.+)$", re.S)
PENDING_RE = re.compile(r"^(?P<slug>.+)\.chunk-(?P<idx>\d+)\.delta-(?P<pid>\d+)\.pending\.json$")

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


def _check_context(llm, gen_model, user, *, max_tokens, chunk_idx, stage_label="Stage 0",
                   flag_name="--gen-model") -> str | None:
    limit, reason = llm.context_limit(gen_model), ""
    try:
        tokens = llm.count_tokens(gen_model, user, None)
    except anthropic.BadRequestError as e:
        if not BADREQUEST_RE.search(str(e)):
            raise
        tokens, reason = estimate_tokens(len(user.split())), f" (API: {e})"
        limit = -1
    if tokens + max_tokens > limit:
        return (f"Chunk {chunk_idx}'s {stage_label} input is ~{tokens:,} tokens, exceeding {gen_model}'s context "
                f"window ({limit:,} tokens). Try a larger-context {flag_name}.{reason}")
    return None


def attempt(llm, conn, novel_id, chunk_id, kind, model, user, parse, *, max_tokens, slug, chunk_idx,
           note=None, note_from=None, status_for=None, post_validate=None):
    """Call, parse, insert the pass once with its verdict. Retry once on ParseError; store STOPPED:<reason>
    on a non-end_turn stop and re-raise; raise ParseError("malformed twice ...") on a second failure.

    post_validate(parsed), when given, is called only after a shape-valid parse; a non-None return becomes
    the pass's stored verdict instead of always None. This never changes what attempt() returns — still
    just parsed — so a caller that needs to know whether post_validate fired must recompute the same
    condition itself from the returned value, not re-fetch the verdict from the database (db.latest_pass
    filters verdict IS NULL and wouldn't even see a pass whose post_validate fired)."""
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
                    verdict=post_validate(parsed) if post_validate else None,
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
    print(f"Chunk {chunk.idx} beats stored ({len(existing)} beats).")
    return 0


def _resolve_revise_target(conn, chunk_row, chunk, revise_arg: str):
    """(target, None) on success, (None, error_message) otherwise. Pulled out of run_revise so
    run_revise_beat can resolve the same target identity without duplicating the parsing/occurrence-
    counting logic."""
    m = REVISE_RE.match(revise_arg)
    if not m:
        return None, f"--revise-beat: {revise_arg!r} doesn't look like a timecode (04-15, 04-15_2, 04-15a)."
    base, occurrence, suffix = m[1], int(m[2] or 1), m[3] or ""
    occurrences = [i for i, (tc, _) in enumerate(chunk.scenes) if tc == base]
    if occurrence > len(occurrences):
        return None, f"--revise-beat: chunk {chunk.idx} has no occurrence {occurrence} of {base}."
    scene_index = occurrences[occurrence - 1]
    beats = current_beats(conn, chunk_row, chunk)
    if beats is None:
        return None, f"--revise-beat: chunk {chunk.idx} has no beats yet; run Stage 0 first."
    target = next((b for b in beats if (b.scene_index, b.suffix) == (scene_index, suffix)), None)
    if target is None:
        return None, f"--revise-beat: no beat {revise_arg!r} in chunk {chunk.idx}."
    return target, None


def run_revise(conn, llm, spec, src, novel_id, chunk_row, chunk, revise_arg: str, description: str, *,
              gen_model, slug) -> int:
    target, err = _resolve_revise_target(conn, chunk_row, chunk, revise_arg)
    if err:
        return _fail(err)
    beats = current_beats(conn, chunk_row, chunk)
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
    print(f"Beat {revise_arg} revised.")
    return 0


def _current_bible(conn, novel_id) -> dict:
    return json.loads(db.latest_bible(conn, novel_id)["json"])


def _tag_index(current_bible, *, omit_tag=None) -> str:
    """Every existing Bible entry, one line each, omitting one tag (used by --regenerate to hide its
    own target so Stage 1's own "don't re-create" rule makes the model propose a fresh reference)."""
    omit_norm = _norm(omit_tag) if omit_tag else None
    lines = []
    for category in ("characters", "locations", "objects"):
        for row in current_bible[category]:
            if omit_norm is not None and _norm(row["tag"]) == omit_norm:
                continue
            slot = f"slot: {row['slot']} | " if "slot" in row else ""
            lines.append(f"- {row['name']} | #{row['tag']} | {slot}reference generated: yes | "
                        f"locked descriptor: {row['descriptor']}")
    return "\n".join(lines) if lines else "(no existing references yet)"


def _beats_block(beats) -> str:
    lines = []
    for b in beats:
        detail = "; ".join(f"{clause} ({chapter})" for clause, chapter in b.detail)
        tail = f" [detail: {detail}]" if detail else ""
        lines.append(f"#{b.timecode}{b.suffix}: {b.narration}{tail}")
    return "\n".join(lines)


def _pending_refs_pass(conn, chunk_id):
    """The latest ok `refs` pass, specifically — `None` if that pass is bound to a bible_versions row.
    Never "any unbound pass": that filter-first reading would resurrect an older, already-superseded
    pass once a later regenerate's pass gets approved."""
    p = db.latest_pass(conn, chunk_id, "refs")
    if p is None or db.pass_is_bound(conn, p["id"]):
        return None
    return p


def _pending_state(conn, chunk_id) -> tuple[dict, frozenset]:
    """(delta, replace_tags) for whatever's currently pending for this chunk, or the empty delta with
    no replace tags when nothing is pending. The single canonical way to read this fact."""
    p = _pending_refs_pass(conn, chunk_id)
    if p is None:
        return json.loads(json.dumps(EMPTY_DELTA)), frozenset()
    note = json.loads(p["note"])
    return note["delta"], frozenset(note["replace_tags"])


def _pending_path(slug, chunk_idx, pass_id) -> Path:
    return Path(config.REFS_PENDING_DIR) / f"{slug}.chunk-{chunk_idx:02d}.delta-{pass_id}.pending.json"


def _delta_for_pass(conn, pass_id):
    row = conn.execute("SELECT note FROM passes WHERE id = ?", (pass_id,)).fetchone()
    return json.loads(row["note"])["delta"] if row and row["note"] else None


def _write_pending(conn, slug, chunk_idx, pass_id, delta) -> Path:
    """Bind the pending file to this pass, clear any other stale pending file for this chunk (with the
    same 'your edits were superseded' notice qc._tracker_gate prints), write it only if missing."""
    bound = _pending_path(slug, chunk_idx, pass_id)
    for old in sorted(Path(config.REFS_PENDING_DIR).glob(f"{slug}.chunk-*.delta-*.pending.json")):
        if old == bound:
            continue
        m = PENDING_RE.match(old.name)
        if not m or int(m["idx"]) != chunk_idx:
            continue
        try:
            edited = json.loads(old.read_text(encoding="utf-8-sig")) != _delta_for_pass(conn, int(m["pid"]))
        except (ValueError, ParseError):
            edited = True
        if edited:
            print(f"Note: your edits to {old} were superseded by a new references delta.")
        old.unlink()
    if not bound.exists():
        bound.parent.mkdir(parents=True, exist_ok=True)
        bound.write_text(json.dumps(delta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return bound


def _write_refs_txt(spec, current_bible, chunk, delta, slug) -> Path:
    suffix = bible._render_suffix(spec, current_bible, chunk.module)
    groups = {"character": [], "location": [], "object": []}
    for r in delta["new_references"]:
        groups[r["type"]].append(f"{r['descriptor']}, {suffix} #{r['tag']}")
    blocks = [lines for lines in (groups["character"], groups["location"], groups["object"]) if lines]
    text = "\n\n".join("\n".join(b) for b in blocks) + "\n"
    path = Path(config.IMAGES_DIR) / slug / f"chunk-{chunk.idx:02d}" / "refs.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_bible_file(spec, merged_bible, slug, title) -> Path:
    path = Path(config.BIBLE_DIR) / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(bible.render(merged_bible, spec, title=title) + "\n", encoding="utf-8")
    return path


def run_stage1(conn, llm, spec, src, novel_id, chunk_row, chunk, *, stage1_model, slug,
              post_batch_revision: tuple[int, str] | None = None) -> int:
    current = _current_bible(conn, novel_id)
    pass_row = _pending_refs_pass(conn, chunk_row["id"])
    if pass_row is None:
        beats = current_beats(conn, chunk_row, chunk)
        prompt = "\n\n".join([spec.stages["STAGE 1"].text, spec.contracts.override, spec.contracts.stage1])
        user = prompt + "\n\n---\n\n" + "\n\n---\n\n".join([_tag_index(current), _beats_block(beats)])
        err = _check_context(llm, stage1_model, user, max_tokens=config.STAGE1_MAX_TOKENS, chunk_idx=chunk.idx,
                             stage_label="Stage 1", flag_name="--stage1-model")
        if err:
            return _fail(err)

        def _parse(text):
            d = bible.validate_stage1(extract_json(text))
            bible.check_no_collision(d, current)
            return d

        def _note_from(d):
            note = {"delta": d, "replace_tags": []}
            if post_batch_revision is not None:
                note["revise_beat"] = {"scene_index": post_batch_revision[0], "suffix": post_batch_revision[1]}
            return json.dumps(note)

        try:
            attempt(llm, conn, novel_id, chunk_row["id"], "refs", stage1_model, user, _parse,
                   max_tokens=config.STAGE1_MAX_TOKENS, slug=slug, chunk_idx=chunk.idx, note_from=_note_from)
        except ParseError as e:
            return _fail(f"Chunk {chunk.idx}'s Stage 1 output was {e}; raw outputs are stored. "
                        "Re-run to try again.")
        pass_row = db.latest_pass(conn, chunk_row["id"], "refs")
    delta, _ = _pending_state(conn, chunk_row["id"])
    if not delta["new_references"]:
        merged = bible.merge(current, delta, chunk.idx)
        db.add_bible_version(conn, novel_id, chunk.idx, "refs", json.dumps(merged, ensure_ascii=False),
                             json.dumps(delta), source_pass_id=pass_row["id"], chunk_id=chunk_row["id"],
                             new_status="refs_approved")
        _write_bible_file(spec, merged, slug, src.title)
        print("No new references.")
        return 0
    _write_refs_txt(spec, current, chunk, delta, slug)
    bound = _write_pending(conn, slug, chunk.idx, pass_row["id"], delta)
    # R1-3 fix: unconditional -- this line only ever runs inside the "new references found, must gate"
    # branch, where setting refs_pending is always correct regardless of the chunk's PRIOR status. The
    # pre-IS-5 `if chunk_row["status"] == "beats"` guard only ever fired from a fresh chunk's own natural
    # path; a post-batch revision reopening Stage 1 can start from refs_approved or bible_pending instead,
    # and the old guard would silently skip gating those.
    db.set_chunk_status(conn, chunk_row["id"], "refs_pending")
    print(f"Chunk {chunk.idx}: {len(delta['new_references'])} new reference(s) pending. "
         f"Generate images from {Path(config.IMAGES_DIR) / slug / f'chunk-{chunk.idx:02d}' / 'refs.txt'}, "
         f"review {bound}, then re-run with --approve-refs.")
    return 0


def approve_refs(conn, novel_id, chunk_row, *, spec, slug, title, llm=None, src=None, chunk=None,
                 gen_model=None) -> int:
    if chunk_row["status"] != "refs_pending":
        return _fail(f"--approve-refs: chunk {chunk_row['idx']} isn't at refs_pending.")
    pass_row = db.latest_pass(conn, chunk_row["id"], "refs")
    bound = _pending_path(slug, chunk_row["idx"], pass_row["id"])
    if not bound.exists():
        return _fail(f"Pending file {bound} not found; re-run without --approve-refs to regenerate it.")
    try:
        delta = bible.validate_stage1(json.loads(bound.read_text(encoding="utf-8-sig")))
    except (ValueError, ParseError) as e:
        return _fail(f"Pending file {bound} is invalid: {e}")
    note = json.loads(pass_row["note"])
    replace_tags = frozenset(note["replace_tags"])
    current = _current_bible(conn, novel_id)
    try:
        merged = bible.merge(current, delta, chunk_row["idx"], replace_tags=replace_tags)
    except ParseError as e:
        return _fail(f"--approve-refs: {e}")
    try:
        db.add_bible_version(conn, novel_id, chunk_row["idx"], "refs", json.dumps(merged, ensure_ascii=False),
                             json.dumps(delta), source_pass_id=pass_row["id"], chunk_id=chunk_row["id"],
                             new_status="refs_approved")
    except sqlite3.IntegrityError:
        return _fail("--approve-refs: this pass was already accepted (replayed accept).")
    bound.unlink()
    _write_bible_file(spec, merged, slug, title)
    print(f"Chunk {chunk_row['idx']}'s references approved and merged into the Visual Bible.")
    revise_beat = note.get("revise_beat")
    if revise_beat is None:
        return 0
    assert llm is not None and src is not None and chunk is not None and gen_model is not None, (
        "approve_refs: a revise_beat-marked pass needs llm/src/chunk/gen_model threaded through -- this "
        "is a programming error, not an operator-recoverable state.")
    scene_index, suffix = revise_beat["scene_index"], revise_beat["suffix"]
    row = db.chunks(conn, novel_id)[chunk_row["idx"] - 1]
    beats = current_beats(conn, row, chunk)
    beat_index = next(i for i, b in enumerate(beats) if (b.scene_index, b.suffix) == (scene_index, suffix))
    batch_note = _batch_containing(conn, chunk_row["id"], beat_index)
    try:
        code = run_revise_batch(conn, llm, spec, src, novel_id, row, chunk, batch_note, gen_model=gen_model,
                                slug=slug)
    except (LLMError, anthropic.AnthropicError):
        print(f"Batch {batch_note['batch_index']} re-emission failed; re-run your --revise-beat "
             f"command for the beat at scene index {scene_index}{suffix} to retry.")
        raise
    if code != 0:
        print(f"Batch {batch_note['batch_index']} re-emission failed; re-run your --revise-beat "
             f"command for the beat at scene index {scene_index}{suffix} to retry.")
    return code


def _resolve_regenerate_target(tag, current_bible, old_delta):
    """(type, tag, name, already_in_bible) for #tag, resolved against the Bible first, then the
    chunk's own pending delta. None if it resolves to neither."""
    norm_tag = _norm(tag)
    for category in ("characters", "locations", "objects"):
        for row in current_bible[category]:
            if _norm(row["tag"]) == norm_tag:
                type_ = next(t for t, c in TYPE_TO_CATEGORY.items() if c == category)
                return type_, row["tag"], row["name"], True
    for r in old_delta["new_references"]:
        if _norm(r["tag"]) == norm_tag:
            category = TYPE_TO_CATEGORY[r["type"]]
            bu_item = next((i for i in old_delta["bible_update"][category] if _norm(i["tag"]) == norm_tag), None)
            return r["type"], r["tag"], bu_item["name"] if bu_item else r["tag"], False
    return None


def _match_regenerate_target(delta, target_type, target_tag, target_name):
    """The one new_references entry (plus its bible_update counterpart) matching the regenerate target
    by tag or by its real NAME (not its tag string — the model may echo the target under a different
    tag while still naming it correctly), with its tag/category overwritten to the target's real ones —
    code has the final say. Returns a full one-entry delta (never a bare item), since validate_stage1's
    bijection means the two must travel together. Raises ParseError on zero or more than one match."""
    matches = []
    for r in delta["new_references"]:
        category = TYPE_TO_CATEGORY[r["type"]]
        bu_item = next(i for i in delta["bible_update"][category] if _norm(i["tag"]) == _norm(r["tag"]))
        if _norm(r["tag"]) == _norm(target_tag) or _norm(bu_item["name"]) == _norm(target_name):
            matches.append((r, bu_item))
    if len(matches) != 1:
        raise ParseError(f"expected exactly one regenerate match, found {len(matches)}")
    r, bu_item = matches[0]
    fixed_category = TYPE_TO_CATEGORY[target_type]
    fixed_r = {**r, "type": target_type, "tag": target_tag}
    fixed_bu = {**bu_item, "tag": target_tag}
    bu_full = {"characters": [], "locations": [], "objects": []}
    bu_full[fixed_category] = [fixed_bu]
    return {"new_references": [fixed_r], "bible_update": bu_full}


def _compose(old_delta, target_tag, matched_delta) -> dict:
    """Replaces target_tag's entry in old_delta at its existing index when still pending, else appends
    it (already-approved case, which takes the replace_tags path into the Bible instead, so its
    position here is moot). Never re-categorizes anything itself — that already happened inside
    _match_regenerate_target."""
    norm_target = _norm(target_tag)
    new_refs = list(old_delta["new_references"])
    matched_r = matched_delta["new_references"][0]
    idx = next((i for i, r in enumerate(new_refs) if _norm(r["tag"]) == norm_target), None)
    if idx is None:
        new_refs.append(matched_r)
    else:
        new_refs[idx] = matched_r
    bu = {cat: [i for i in old_delta["bible_update"][cat] if _norm(i["tag"]) != norm_target]
         for cat in ("characters", "locations", "objects")}
    matched_cat, matched_item = next((c, i[0]) for c, i in matched_delta["bible_update"].items() if i)
    bu[matched_cat].append(matched_item)
    return {"new_references": new_refs, "bible_update": bu}


def run_regenerate(conn, llm, spec, src, novel_id, chunk_row, chunk, tag_reason: str, *, stage1_model,
                  slug) -> int:
    m = REGENERATE_RE.match(tag_reason)
    if not m:
        return _fail(f"--regenerate: {tag_reason!r} doesn't look like '#Name: reason'.")
    reason = " ".join(m["reason"].split())
    if not reason:
        return _fail("--regenerate needs a non-empty reason.")
    if chunk_row["status"] not in ("refs_pending", "refs_approved"):
        return _fail(f"--regenerate: chunk {chunk_row['idx']} isn't at refs_pending or refs_approved.")
    if db.ok_passes(conn, chunk_row["id"], ["stage2"]):
        return _fail(f"--regenerate: chunk {chunk_row['idx']} already has Stage 2 batches stored; "
                    "use the IS-5 revision path instead.")
    current = _current_bible(conn, novel_id)
    old_delta, old_replace_tags = _pending_state(conn, chunk_row["id"])
    resolved = _resolve_regenerate_target(m["tag"], current, old_delta)
    if resolved is None:
        return _fail(f"--regenerate: no reference matching {m['tag']!r} in the Bible or the pending delta.")
    target_type, target_tag, target_name, target_already_in_bible = resolved
    beats = current_beats(conn, chunk_row, chunk)
    if not any(target_name.lower() in b.narration.lower() for b in beats):
        return _fail(f"--regenerate: {target_name!r} doesn't appear in chunk {chunk.idx}'s beats.")
    tag_index = _tag_index(current, omit_tag=target_tag)
    beats_block = _beats_block(beats)
    regen_line = fill(spec.regenerate_line, {"#Name": f"#{target_tag}", "[reason]": reason})
    prompt = "\n\n".join([spec.stages["STAGE 1"].text, spec.contracts.override, spec.contracts.stage1])
    user = prompt + "\n\n---\n\n" + "\n\n---\n\n".join([tag_index, beats_block, regen_line])
    err = _check_context(llm, stage1_model, user, max_tokens=config.STAGE1_MAX_TOKENS, chunk_idx=chunk.idx,
                         stage_label="Stage 1", flag_name="--stage1-model")
    if err:
        return _fail(err)

    def _parse(text):
        d = bible.validate_stage1(extract_json(text))
        return _match_regenerate_target(d, target_type, target_tag, target_name)

    try:
        attempt(llm, conn, novel_id, chunk_row["id"], "refs", stage1_model, user, _parse,
               max_tokens=config.STAGE1_MAX_TOKENS, slug=slug, chunk_idx=chunk.idx,
               note_from=lambda matched: json.dumps({
                   "delta": _compose(old_delta, target_tag, matched),
                   "replace_tags": sorted({*old_replace_tags, target_tag} if target_already_in_bible
                                         else old_replace_tags)}),
               status_for=lambda _: "refs_pending")
    except ParseError as e:
        return _fail(f"Chunk {chunk.idx}'s regenerate output was {e}; raw outputs are stored. "
                    "Re-run to try again.")
    new_delta, _ = _pending_state(conn, chunk_row["id"])
    _write_refs_txt(spec, current, chunk, new_delta, slug)
    new_pass_row = db.latest_pass(conn, chunk_row["id"], "refs")
    _write_pending(conn, slug, chunk.idx, new_pass_row["id"], new_delta)
    print(f"Reference '#{target_tag}' regenerating; review and re-run with --approve-refs.")
    return 0


# ---- Stage 2 (image prompts), the manifest, and the end-of-chunk continuity gate (IS-4) ----

def _batch_progress(conn, chunk_id) -> tuple[int, int, list[str]]:
    """(next_start, next_batch_index, previous_tail) from the ok stage2 pass with the HIGHEST batch_index
    -- never the newest-inserted pass, and never recomputed from config.BATCH_SIZE. IS-5's own re-emission
    mechanism is the first thing that can insert a stage2 pass out of batch_index order (redoing an
    earlier batch after later ones already exist); selecting by insertion order would then silently
    rewind the chunk's own reported progress (R1-2)."""
    by_batch = _ok_stage2_by_batch(conn, chunk_id)
    if not by_batch:
        return 0, 1, []
    note, _ = by_batch[max(by_batch)]
    return note["start"] + note["count"], note["batch_index"] + 1, note["tail"]


def _stage2_done(conn, chunk_id, beats) -> bool:
    next_start, _, _ = _batch_progress(conn, chunk_id)
    return next_start >= len(beats)


def _stage2_continues_seed(conn, novel_id, chunk) -> tuple[str | None, list[str]]:
    """For chunk.idx > 1, when this chunk's first beat has `continues` set: the previous chunk's last
    stored stage2 item's shot type, as both a CONTINUES framing line and a one-item previous_tail seed
    (which REPLACES previous_tail outright for batch 1 -- batch 1 has no "previous batch" of its own
    within this chunk). (None, []) when not applicable."""
    if chunk.idx == 1:
        return None, []
    rows = db.chunks(conn, novel_id)
    chunk_row = rows[chunk.idx - 1]
    beats = current_beats(conn, chunk_row, chunk)
    if not beats or beats[0].continues is None:
        return None, []
    prev_row = rows[chunk.idx - 2]
    by_batch = _ok_stage2_by_batch(conn, prev_row["id"])
    if not by_batch:
        return None, []
    note, _ = by_batch[max(by_batch)]
    if not note["tail"]:
        return None, []
    shot_type = note["tail"][-1]
    return (f"CONTINUES: previous chunk's last shot was #{beats[0].continues} ({shot_type}).", [shot_type])


def _bible_entries_for_beats(current_bible, beats) -> str:
    """The full Bible entries (current_state included) for every ref named across these beats' own
    narration, matched by #Tag/name substring -- the same technique run_regenerate's own
    name-in-narration precondition uses. Rendered via bible._entries' render()-style shape, never
    _tag_index's slimmer form, since the model needs current_state here."""
    narration = " ".join(b.narration for b in beats).lower()
    lines = []
    for category, slotted in (("characters", True), ("locations", False), ("objects", True)):
        matched = [row for row in current_bible[category]
                  if row["name"].lower() in narration or f"#{row['tag']}".lower() in narration]
        if matched:
            lines.extend(bible._entries(matched, slotted=slotted))
    return "\n".join(lines) if lines else "(no matching references)"


def _batch_containing(conn, chunk_id, beat_index: int):
    """The stored ok stage2 pass's note whose (start, start+count) range covers beat_index (the target
    beat's position in current_beats()'s flat list), or None if that beat hasn't been batched yet."""
    for note, _ in _ok_stage2_by_batch(conn, chunk_id).values():
        if note["start"] <= beat_index < note["start"] + note["count"]:
            return note
    return None


def run_revise_batch(conn, llm, spec, src, novel_id, chunk_row, chunk, batch_note: dict, *, gen_model,
                    slug) -> int:
    """Re-emits one already-stored batch (named by `batch_note`, from _batch_containing) after a
    post-batch revision. The new pass shares batch_note's own batch_index, so IS-4's own latest-pass-
    per-batch_index dedup (_write_all_batch_files, _rewrite_manifest) transparently makes it
    authoritative for that one batch file and those manifest rows -- no changes needed to either
    self-heal helper. Also rewrites manifest.csv itself: a re-emitted batch's own manifest rows must
    reflect the change immediately, and run_bible_update (the normal trigger for a manifest refresh) may
    not run again for a long time if the chunk was already past that point when the revision happened."""
    beats = current_beats(conn, chunk_row, chunk)
    batch = beats[batch_note["start"]:batch_note["start"] + batch_note["count"]]
    continues_text, previous_beats_text, previous_tail = _batch_context(
        conn, novel_id, spec, chunk_row, chunk, beats, batch_note["start"], batch_note["batch_index"])
    code = _compose_and_store_batch(conn, llm, spec, src, novel_id, chunk_row, chunk, batch,
                                    batch_note["start"], batch_note["batch_index"], continues_text,
                                    previous_beats_text, previous_tail, gen_model=gen_model, slug=slug)
    if code != 0:
        return code
    _rewrite_manifest(conn, novel_id, src, slug)
    return 0


def run_revise_beat(conn, llm, spec, src, novel_id, chunk_row, chunk, revise_arg: str, description: str, *,
                   gen_model, stage1_model, slug) -> int:
    """The --revise-beat CLI dispatch target: resolves the target beat, and, only if its batch is already
    stored, reopens Stage 1 (which naturally returns an empty delta -- immediate re-emission, no gate --
    or proposes a new element -- gate, then approve_refs's own auto-continue finishes the flow). A beat
    Stage 2 hasn't yet reached behaves exactly as run_revise already does, unchanged."""
    target, err = _resolve_revise_target(conn, chunk_row, chunk, revise_arg)
    if err:
        return _fail(err)
    beats = current_beats(conn, chunk_row, chunk)
    beat_index = next(i for i, b in enumerate(beats)
                      if (b.scene_index, b.suffix) == (target.scene_index, target.suffix))
    batch_note = _batch_containing(conn, chunk_row["id"], beat_index)
    if batch_note is not None and chunk_row["status"] == "refs_pending":
        return _fail(f"--revise-beat: chunk {chunk.idx} already has a pending reference approval; run "
                    "--approve-refs first, then retry this --revise-beat.")
    code = run_revise(conn, llm, spec, src, novel_id, chunk_row, chunk, revise_arg, description,
                      gen_model=gen_model, slug=slug)
    if code != 0:
        return code
    if batch_note is None:
        return 0
    was_bible_pending = chunk_row["status"] == "bible_pending"
    code = run_stage1(conn, llm, spec, src, novel_id, chunk_row, chunk, stage1_model=stage1_model, slug=slug,
                      post_batch_revision=(target.scene_index, target.suffix))
    if code != 0:
        return code
    row = db.chunks(conn, novel_id)[chunk_row["idx"] - 1]
    if row["status"] == "refs_pending":
        if was_bible_pending:
            _clear_stale_continuity_pending(conn, chunk_row, slug)
        return 0   # the gate; run_stage1 already printed it -- approve_refs's auto-continue finishes it
    if was_bible_pending:
        _clear_stale_continuity_pending(conn, chunk_row, slug)
    try:
        code = run_revise_batch(conn, llm, spec, src, novel_id, chunk_row, chunk, batch_note,
                                gen_model=gen_model, slug=slug)
    except (LLMError, anthropic.AnthropicError):
        # attempt() re-raises on a genuine API failure -- this must still print the recovery message
        # before the exception propagates to cli.py's own outer handler, not only on a returned non-zero
        # code (R2-1). revise_arg is the exact operator-typed argument, already disambiguated -- it is
        # printed verbatim, never reconstructed from target.timecode + target.suffix, which is not always
        # a valid --revise-beat argument when the beat's own base timecode is duplicated.
        print(f"Batch {batch_note['batch_index']} re-emission failed; re-run --revise-beat "
             f"{revise_arg} \"<description>\" to retry.")
        raise
    if code != 0:
        print(f"Batch {batch_note['batch_index']} re-emission failed; re-run --revise-beat "
             f"{revise_arg} \"<description>\" to retry.")
    return code


def _ok_stage2_by_batch(conn, chunk_id) -> dict:
    """{batch_index: (note, pass_row)}, deduplicated to the latest ok stage2 pass per batch_index (guards
    against two concurrent runs both storing a batch at the same index)."""
    latest = {}
    for p in db.ok_passes(conn, chunk_id, ["stage2"]):
        note = json.loads(p["note"])
        latest[note["batch_index"]] = (note, p)
    return latest


def _write_all_batch_files(conn, spec, current_bible, chunk_row, chunk, slug) -> None:
    """Rewrites every batch file for this chunk from every ok stage2 pass -- not just the one just
    written -- so a batch file deleted by hand, or a crash before this step on an earlier batch, both
    self-heal on the next successful run."""
    by_batch = _ok_stage2_by_batch(conn, chunk_row["id"])
    out_dir = Path(config.IMAGES_DIR) / slug / f"chunk-{chunk.idx:02d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    for batch_index in sorted(by_batch):
        note, _ = by_batch[batch_index]
        items = note["items"]
        lines = [f"#{item['timecode']}\n{compose_prompt(spec, current_bible, chunk, item)}" for item in items]
        (out_dir / f"batch-{batch_index}.txt").write_text("\n\n".join(lines) + "\n", encoding="utf-8")


def _continuity_pending_path(slug, chunk_idx, pass_id) -> Path:
    return Path(config.CONTINUITY_PENDING_DIR) / f"{slug}.chunk-{chunk_idx:02d}.delta-{pass_id}.pending.json"


def _write_continuity_pending_if_missing(conn, chunk_row, slug) -> Path:
    p = db.latest_pass(conn, chunk_row["id"], "continuity")
    bound = _continuity_pending_path(slug, chunk_row["idx"], p["id"])
    if not bound.exists():
        bound.parent.mkdir(parents=True, exist_ok=True)
        delta = json.loads(p["note"])["delta"]
        bound.write_text(json.dumps(delta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return bound


def _clear_stale_continuity_pending(conn, chunk_row, slug) -> None:
    """If the chunk currently has a continuity pending file (bound to the latest ok continuity pass),
    delete it with the same 'superseded' notice _write_pending prints elsewhere -- a post-batch revision
    that regresses a chunk away from bible_pending means the old continuity delta no longer reflects the
    (now stale) batch set it was derived from."""
    p = db.latest_pass(conn, chunk_row["id"], "continuity")
    if p is None:
        return
    bound = _continuity_pending_path(slug, chunk_row["idx"], p["id"])
    if bound.exists():
        bound.unlink()
        print(f"Note: {bound}'s continuity update is superseded by this revision; a fresh one will be "
             "derived once Stage 2 is complete again.")


def _rewrite_manifest(conn, novel_id, src, slug) -> None:
    """manifest.csv for the WHOLE novel, in chunk order, one row per stored batch -- always from the
    stored beats' own display timecodes, never the model's own timecode field."""
    all_rows = []
    for chunk_row, chunk in zip(db.chunks(conn, novel_id), src.chunks):
        beats = current_beats(conn, chunk_row, chunk)
        if beats is None:
            continue
        by_batch = _ok_stage2_by_batch(conn, chunk_row["id"])
        for batch_index in sorted(by_batch):
            note, _ = by_batch[batch_index]
            items = note["items"]
            batch_beats = beats[note["start"]:note["start"] + note["count"]]
            entries = [(b.timecode + b.suffix, item) for b, item in zip(batch_beats, items)]
            all_rows.extend(build_manifest_rows(entries))
    path = Path(config.IMAGES_DIR) / slug / "manifest.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["timecode", "shot_type", "first_5_words"])
        w.writerows(all_rows)


def _batch_context(conn, novel_id, spec, chunk_row, chunk, beats, start, batch_index):
    """(continues_text, previous_beats_text, previous_tail) for the batch starting at beat index `start`.
    For batch_index == 1: previous_tail is the CONTINUES seed (chunk 2+) or empty (chunk 1) -- the only
    tail data a chunk's own first batch has, since it has no earlier batch of its own. For batch_index >
    1: previous_beats_text is the previous 3 beats (labeled per spec.stage2_context_label), and
    previous_tail is looked up EXPLICITLY from the batch at batch_index - 1's own stored note["tail"] --
    never a live `_batch_progress` read, so this stays correct even after a re-emission stores a stage2
    pass out of batch_index order (R1-2)."""
    if batch_index == 1:
        continues_text, seed_tail = _stage2_continues_seed(conn, novel_id, chunk)
        return continues_text, None, seed_tail
    prev_slice = beats[max(0, start - 3):start]
    previous_beats_text = spec.stage2_context_label + "\n" + _beats_block(prev_slice)
    by_batch = _ok_stage2_by_batch(conn, chunk_row["id"])
    prev_note, _ = by_batch[batch_index - 1]
    return None, previous_beats_text, prev_note["tail"]


def _compose_and_store_batch(conn, llm, spec, src, novel_id, chunk_row, chunk, batch, start, batch_index,
                             continues_text, previous_beats_text, previous_tail, *, gen_model, slug) -> int:
    """Composes, sends, QA-retries, and stores one Stage 2 batch. Re-derives `current`/`aspect` fresh
    from chunk_row/novel_id, since both run_stage2 and run_revise_batch need them fresh anyway. Byte-
    identical logic to the pre-IS-5 run_stage2 body, just parameterized instead of reading
    _batch_progress's "next" values directly."""
    current = _current_bible(conn, novel_id)
    aspect = current["style_lock"]["aspect"]
    stage2_prompt = "\n\n".join([spec.stages["STAGE 2"].text, spec.contracts.override, spec.contracts.stage2])
    sections = [spec.mode_a_line]
    if continues_text:
        sections.append(continues_text)
    if previous_beats_text:
        sections.append(previous_beats_text)
    sections.append(_beats_block(batch))
    sections.append(json.dumps({"style_lock": current["style_lock"]}))
    sections.append(_bible_entries_for_beats(current, batch))
    user = stage2_prompt + "\n\n---\n\n" + "\n\n---\n\n".join(sections)
    err = _check_context(llm, gen_model, user, max_tokens=config.STAGE2_MAX_TOKENS, chunk_idx=chunk.idx,
                         stage_label="Stage 2", flag_name="--gen-model")
    if err:
        return _fail(err)

    def _qa(items):
        return check_refs(current, items) + check_shot_cadence(items, aspect, previous_tail=previous_tail)

    def _note_from(p):
        # `p["prompts"]` is the PARSED object -- validate_stage2 already normalized its `timecode`/
        # `refs_used` fields in place (stripped a leading '#'/'@'). The pass's own `output_text` stores
        # the model's raw, un-normalized reply, so every later reader (batch files, the manifest, the
        # continuity call) must read these already-normalized items back from the note, never re-parse
        # output_text -- otherwise a `#`/`@`-prefixed value that validate_stage2 tolerated crashes or
        # corrupts output the moment a stored pass is read back instead of used fresh.
        return json.dumps({"start": start, "count": len(batch), "batch_index": batch_index,
                          "tail": [i["shot_type"] for i in p["prompts"][-8:]], "items": p["prompts"]})

    try:
        parsed = attempt(
            llm, conn, novel_id, chunk_row["id"], "stage2", gen_model, user,
            lambda t: validate_stage2(extract_json(t), batch),
            max_tokens=config.STAGE2_MAX_TOKENS, slug=slug, chunk_idx=chunk.idx,
            post_validate=lambda p: "QA_RETRIED" if _qa(p["prompts"]) else None, note_from=_note_from)
    except ParseError as e:
        return _fail(f"Chunk {chunk.idx}'s batch {batch_index} output was {e}; raw outputs are stored. "
                    "Re-run to try again.")

    problems = _qa(parsed["prompts"])
    if problems:
        retry_user = user + "\n\n---\n\nYour previous batch had these problems:\n" + "\n".join(problems)
        try:
            parsed2 = attempt(
                llm, conn, novel_id, chunk_row["id"], "stage2", gen_model, retry_user,
                lambda t: validate_stage2(extract_json(t), batch),
                max_tokens=config.STAGE2_MAX_TOKENS, slug=slug, chunk_idx=chunk.idx,
                post_validate=lambda p: "QA_FAILED" if check_refs(current, p["prompts"]) else None,
                note_from=_note_from)
        except ParseError as e:
            return _fail(f"Chunk {chunk.idx}'s batch {batch_index} retry output was {e}; raw outputs are "
                        "stored. Re-run to try again.")
        ref_problems = check_refs(current, parsed2["prompts"])
        if ref_problems:
            return _fail(f"Chunk {chunk.idx}'s batch {batch_index} has unconfirmed reference(s): "
                        f"{'; '.join(ref_problems)} Re-run to try this batch again.")
        for w in check_shot_cadence(parsed2["prompts"], aspect, previous_tail=previous_tail):
            print(f"WARNING: {w}")
        parsed = parsed2

    for w in check_wide_under_9_16(parsed["prompts"], aspect):
        print(f"WARNING: {w}")
    _write_all_batch_files(conn, spec, current, chunk_row, chunk, slug)
    print(f"Batch {batch_index} stored -> {Path(config.IMAGES_DIR) / slug / f'chunk-{chunk.idx:02d}' / f'batch-{batch_index}.txt'}.")
    return 0


def run_stage2(conn, llm, spec, src, novel_id, chunk_row, chunk, *, gen_model, slug) -> int:
    beats = current_beats(conn, chunk_row, chunk)
    start, batch_index, _ = _batch_progress(conn, chunk_row["id"])
    if start >= len(beats):
        return 0
    batch = beats[start:start + config.BATCH_SIZE]
    continues_text, previous_beats_text, previous_tail = _batch_context(
        conn, novel_id, spec, chunk_row, chunk, beats, start, batch_index)
    return _compose_and_store_batch(conn, llm, spec, src, novel_id, chunk_row, chunk, batch, start,
                                    batch_index, continues_text, previous_beats_text, previous_tail,
                                    gen_model=gen_model, slug=slug)


def run_bible_update(conn, llm, spec, src, novel_id, chunk_row, chunk, *, bible_model, slug) -> int:
    current = _current_bible(conn, novel_id)
    beats = current_beats(conn, chunk_row, chunk)
    if not _stage2_done(conn, chunk_row["id"], beats):
        return _fail(f"Chunk {chunk.idx}: Stage 2 isn't complete yet; run it before the continuity update.")

    by_batch = _ok_stage2_by_batch(conn, chunk_row["id"])
    prompts_lines = []
    for batch_index in sorted(by_batch):
        note, _ = by_batch[batch_index]
        for item in note["items"]:
            prompts_lines.append(f"#{item['timecode']}: {compose_prompt(spec, current, chunk, item)}")

    prompt = "\n\n".join([spec.continuity_prompt, spec.contracts.override, spec.contracts.bible_update])
    user = prompt + "\n\n---\n\n" + "\n\n---\n\n".join([_beats_block(beats), "\n".join(prompts_lines)])
    err = _check_context(llm, bible_model, user, max_tokens=config.BIBLE_UPDATE_MAX_TOKENS, chunk_idx=chunk.idx,
                         stage_label="Bible update", flag_name="--bible-model")
    if err:
        return _fail(err)
    try:
        attempt(llm, conn, novel_id, chunk_row["id"], "continuity", bible_model, user,
               lambda t: bible.validate_continuity(extract_json(t), current, beats),
               max_tokens=config.BIBLE_UPDATE_MAX_TOKENS, slug=slug, chunk_idx=chunk.idx,
               note_from=lambda d: json.dumps({"delta": d}), status_for=lambda _: "bible_pending")
    except ParseError as e:
        return _fail(f"Chunk {chunk.idx}'s continuity output was {e}; raw outputs are stored. "
                    "Re-run to try again.")

    _write_all_batch_files(conn, spec, current, chunk_row, chunk, slug)
    bound = _write_continuity_pending_if_missing(conn, chunk_row, slug)
    _rewrite_manifest(conn, novel_id, src, slug)
    print(f"Chunk {chunk.idx}'s batches and continuity update are ready; review {bound}, "
         "then run --accept-bible to finish it.")
    return 0


def accept_bible(conn, novel_id, chunk_row, chunk, *, spec, slug, title) -> int:
    if chunk_row["status"] != "bible_pending":
        return _fail(f"--accept-bible: chunk {chunk_row['idx']} isn't at bible_pending.")
    pass_row = db.latest_pass(conn, chunk_row["id"], "continuity")
    bound = _continuity_pending_path(slug, chunk_row["idx"], pass_row["id"])
    if not bound.exists():
        return _fail(f"Pending file {bound} not found; re-run without --accept-bible to regenerate it.")
    current = _current_bible(conn, novel_id)
    beats = current_beats(conn, chunk_row, chunk)
    try:
        obj = bible.validate_continuity(json.loads(bound.read_text(encoding="utf-8-sig")), current, beats)
    except (ValueError, ParseError) as e:
        return _fail(f"Pending file {bound} is invalid: {e}")
    merged = bible.merge_continuity(current, obj["continuity_log_entries"], chunk.idx)
    try:
        db.add_bible_version(conn, novel_id, chunk.idx, "continuity", json.dumps(merged, ensure_ascii=False),
                             json.dumps(obj, ensure_ascii=False), source_pass_id=pass_row["id"],
                             chunk_id=chunk_row["id"], new_status="done")
    except sqlite3.IntegrityError:
        return _fail("--accept-bible: this pass was already accepted (replayed accept).")
    bound.unlink()
    _write_bible_file(spec, merged, slug, title)
    Path(config.LOG_DIR).mkdir(parents=True, exist_ok=True)
    with open(Path(config.LOG_DIR) / "bible-accepts.log", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat()} chunk {chunk.idx} accepted (pass {pass_row['id']})\n")
    print(f"Chunk {chunk.idx}'s continuity update accepted; chunk done.")
    return 0


def check_images(conn, novel_id, src, *, slug, images_dir: Path, chunk_filter: int | None = None) -> int:
    """Prints, per done chunk (all of them, or just chunk_filter), every expected filename missing from
    images_dir/slug/chunk-NN/, and every .png file present there that isn't expected -- built directly
    from the database, via the same current_beats/_ok_stage2_by_batch/build_manifest_rows path
    _rewrite_manifest already uses, never from the on-disk manifest.csv, so a hand-edited or deleted copy
    can't silently change what's reported. Never blocks or gates -- always returns 0."""
    pairs = list(zip(db.chunks(conn, novel_id), src.chunks))
    if chunk_filter is not None:
        match = next(((row, chunk) for row, chunk in pairs if chunk.idx == chunk_filter), None)
        if match is None:
            print(f"No chunk {chunk_filter} in this novel.")
            return 0
        if match[0]["status"] != "done":
            print(f"Chunk {chunk_filter} isn't done yet; nothing to check.")
            return 0
        pairs = [match]
    else:
        pairs = [(row, chunk) for row, chunk in pairs if row["status"] == "done"]

    any_findings = False
    for row, chunk in pairs:
        beats = current_beats(conn, row, chunk)
        by_batch = _ok_stage2_by_batch(conn, row["id"])
        entries = []
        for batch_index in sorted(by_batch):
            note, _ = by_batch[batch_index]
            batch_beats = beats[note["start"]:note["start"] + note["count"]]
            entries.extend((b.timecode + b.suffix, item) for b, item in zip(batch_beats, note["items"]))
        expected = expected_filenames(build_manifest_rows(entries))
        chunk_dir = images_dir / slug / f"chunk-{chunk.idx:02d}"
        present = {p.name for p in chunk_dir.glob("*.png")} if chunk_dir.is_dir() else set()
        missing = [f for f in expected if f not in present]
        extra = sorted(present - set(expected))
        if missing or extra:
            any_findings = True
            print(f"Chunk {chunk.idx}:")
            for f in missing:
                print(f"  missing: {f}")
            for f in extra:
                print(f"  extra: {f}")
    if not any_findings:
        print("No missing or extra images.")
    return 0
