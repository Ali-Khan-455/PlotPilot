"""Command-line entry point for Image-Sync. Read a finished PlotPilot novel, bind to it, print the
manifest, run the style-lock gate, and — once locked — run Stage 0 (beats) for the current chunk."""

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import anthropic

from imagesync import config, db
from imagesync.pipeline import (accept_bible, approve_refs, current_beats, run_bible_update, run_regenerate,
                                run_revise, run_stage0, run_stage1, run_stage2, _batch_progress,
                                _current_bible, _seconds, _stage2_done, _stamp, _write_all_batch_files,
                                _write_continuity_pending_if_missing, _rewrite_manifest)
from imagesync.source import SourceError, load_novel, open_plotpilot
from imagesync.spec import load_spec
from plotpilot import config as pp_config
from plotpilot.cli import slug_for
from plotpilot.llm import LLM, LLMError, log_error

MISMATCH = ("PlotPilot's data for '{slug}' changed since image sync started ({which}). It changes when (1) the "
            "novel is rebuilt in PlotPilot from a different source file, (2) the script or scenes change (e.g. "
            "a later scenes pass), or (3) PlotPilot's assemble code or WORDS_PER_MINUTE changes. Image sync "
            "can't reuse beats built from other narration.")


def make_client():
    return anthropic.Anthropic(max_retries=3)


def fail(msg: str) -> int:
    print(msg, file=sys.stderr)
    log_error(config.LOG_DIR, msg)
    return 1


def _manifest(src):
    print(f"Novel: {src.title} — {len(src.chunks)} chunks, {sum(len(c.scenes) for c in src.chunks)} scenes")
    print(f"{'#':>3}  {'Chapters':<10}{'Scenes':>7}  Span")
    end_of_script = src.script_words * 60 // pp_config.WORDS_PER_MINUTE
    for i, c in enumerate(src.chunks):
        start = _seconds(c.scenes[0][0])
        end = _seconds(src.chunks[i + 1].scenes[0][0]) if i + 1 < len(src.chunks) else end_of_script
        print(f"{c.idx:>3}  {c.label:<10}{len(c.scenes):>7}  {_stamp(start)}–{_stamp(end)}")


def dominant_module(src) -> str:
    """The most common drafting module; a tie goes to the earliest chunk among the tied modules."""
    counts = Counter(c.module for c in src.chunks)
    top = max(counts.values())
    return next(c.module for c in src.chunks if counts[c.module] == top)


def _style_gate(conn, novel_id, src, args, spec) -> tuple[int, bool]:
    locked = db.latest_bible(conn, novel_id)
    if locked:
        lock = json.loads(locked["json"])["style_lock"]
        where = f"{lock['sub_style']}, {lock['aspect']}"
        if args.sub_style and args.sub_style != lock["sub_style"]:
            print(f"Note: --sub-style {args.sub_style} ignored; the style is locked at chunk 1 ({where}).")
        if args.aspect and args.aspect != lock["aspect"]:
            print(f"Note: --aspect {args.aspect} ignored; the style is locked at chunk 1 ({where}).")
        print(f"Style locked: ({lock['sub_style']}) {_name(spec, lock['sub_style'])}, {lock['aspect']}.")
        return 0, True
    module = dominant_module(src)
    if not args.sub_style:
        letter = config.MODULE_TO_SUBSTYLE[module]
        # The confirm command carries a non-default --aspect, so running it verbatim locks what was shown.
        confirm = f"--sub-style {letter}" + (f" --aspect {args.aspect}" if args.aspect else "")
        print(f"Suggested sub-style: ({letter}) {_name(spec, letter)} (from dominant module {module}). "
              f"Confirm with {confirm}, or pick another. "
              f"Aspect: {args.aspect or config.DEFAULT_ASPECT} (change with --aspect).")
        return 0, False
    aspect = args.aspect or config.DEFAULT_ASPECT
    bible = {"style_lock": {"sub_style": args.sub_style, "aspect": aspect, "genre_color_default": module,
                            "anchor_image": "-"},
             "slots": {"characters": [], "objects": []}, "characters": [], "locations": [], "objects": [],
             "continuity_log": [], "revision_log": []}
    text = json.dumps(bible, ensure_ascii=False)
    try:
        db.add_bible_version(conn, novel_id, None, "style_lock", text, text, pass_fields={
            "kind": "style_lock", "model": None, "input_text": "", "output_text": text,
            "note": f"sub-style {args.sub_style}, aspect {aspect}"})
    except sqlite3.IntegrityError:  # a concurrent run locked it first; it never loaded the winner's own
        print("Style already locked (another run got there first); re-run to see it.")  # values, so stop
        return 0, False
    print(f"Style locked: ({args.sub_style}) {_name(spec, args.sub_style)}, {aspect}.")
    return 0, True


def _name(spec, letter) -> str:
    return spec.sub_styles[letter].split(" — ")[0]


def main(argv=None, client=None) -> int:
    ap = argparse.ArgumentParser(prog="imagesync", description="Turn a PlotPilot script into image prompts.")
    ap.add_argument("--novel", required=True, type=Path, help="The novel .txt file PlotPilot converted.")
    ap.add_argument("--plotpilot-db", default=config.PLOTPILOT_DB, help="PlotPilot's database (read only).")
    ap.add_argument("--sub-style", choices=["a", "b", "c", "d"], help="Lock the novel's sub-style (chunk 1).")
    ap.add_argument("--aspect", choices=config.ASPECTS, help=f"Aspect ratio (default {config.DEFAULT_ASPECT}).")
    ap.add_argument("--gen-model", default=pp_config.GEN_MODEL, help="Model for Stage 0 (beats) calls.")
    ap.add_argument("--stage1-model", default=pp_config.GEN_MODEL, help="Model for Stage 1 (references) calls.")
    ap.add_argument("--revise-beat", nargs=2, metavar=("TIMECODE", "DESCRIPTION"),
                    help="Revise one beat: TIMECODE (e.g. 04-15, or 04-15_2 for the second occurrence of a "
                         "repeated timecode) and its new description.")
    ap.add_argument("--regenerate", metavar="#Name: reason",
                    help="Re-run Stage 1 scoped to one reference, e.g. '#Kael: eye color was wrong'.")
    ap.add_argument("--approve-refs", action="store_true",
                    help="Merge the current chunk's pending references into the Visual Bible.")
    ap.add_argument("--bible-model", default=pp_config.QC_MODEL,
                    help="Model for the end-of-Stage-2 continuity call.")
    ap.add_argument("--accept-bible", action="store_true",
                    help="Merge the current chunk's pending continuity update into the Visual Bible.")
    args = ap.parse_args(argv)
    if sum(bool(x) for x in (args.approve_refs, args.regenerate, args.revise_beat, args.accept_bible)) > 1:
        return fail("--approve-refs, --regenerate, --revise-beat and --accept-bible can't be combined.")
    if not args.novel.is_file():
        return fail(f"Cannot read '{args.novel}': not a regular file.")
    slug = slug_for(args.novel)
    if not slug:
        return fail(f"Cannot derive a name from '{args.novel.name}'; rename it with letters or digits.")
    try:
        spec = load_spec()
        src = load_novel(open_plotpilot(args.plotpilot_db), slug)
    except (SourceError, ValueError) as e:
        return fail(f"ERROR: {e}")
    conn = db.connect(config.DB_PATH)
    try:
        novel = db.find_novel(conn, slug)
        if novel is None:
            novel_id = db.create_novel(conn, slug, src.title, src.plotpilot_source_sha, src.script_sha,
                                       len(src.chunks))
        else:
            novel_id = novel["id"]
            which = [n for n, a, b in (("source file hash", novel["plotpilot_source_sha"], src.plotpilot_source_sha),
                                       ("script hash", novel["script_sha"], src.script_sha)) if a != b]
            if which:
                return fail(MISMATCH.format(slug=slug, which=", ".join(which)))
        _manifest(src)
        code, locked = _style_gate(conn, novel_id, src, args, spec)
        if not locked:
            if args.revise_beat:
                print("Note: --revise-beat ignored; the style isn't locked yet.")
            elif args.regenerate:
                print("Note: --regenerate ignored; the style isn't locked yet.")
            elif args.approve_refs:
                print("Note: --approve-refs ignored; the style isn't locked yet.")
            elif args.accept_bible:
                print("Note: --accept-bible ignored; the style isn't locked yet.")
            return code
        if args.revise_beat and not args.revise_beat[1].strip():
            return fail("--revise-beat needs a non-empty description.")
        rows = db.chunks(conn, novel_id)
        current = next(((r, c) for r, c in zip(rows, src.chunks) if r["status"] != "done"), None)
        if current is None:
            print("Every chunk is fully processed.")
            return 0
        row, chunk = current
        llm = LLM(client if client is not None else (lambda: make_client()), config.LOG_DIR)
        try:
            if args.approve_refs:
                return approve_refs(conn, novel_id, row, spec=spec, slug=slug, title=src.title)
            if args.accept_bible:
                return accept_bible(conn, novel_id, row, chunk, spec=spec, slug=slug, title=src.title)
            if args.regenerate:
                return run_regenerate(conn, llm, spec, src, novel_id, row, chunk, args.regenerate,
                                      stage1_model=args.stage1_model, slug=slug)
            if args.revise_beat:
                return run_revise(conn, llm, spec, src, novel_id, row, chunk, *args.revise_beat,
                                  gen_model=args.gen_model, slug=slug)
            OUTER_LOOP_CAP = 64   # generous fixed backstop; a real run needs at most a handful of
                                  # status transitions plus one iteration per Stage 2 batch
            for _ in range(OUTER_LOOP_CAP):
                if row["status"] == "ready":
                    code = run_stage0(conn, llm, spec, src, novel_id, row, chunk, gen_model=args.gen_model,
                                      slug=slug)
                    if code != 0:
                        return code
                    row = db.chunks(conn, novel_id)[chunk.idx - 1]
                    continue
                if row["status"] in ("beats", "refs_pending"):
                    code = run_stage1(conn, llm, spec, src, novel_id, row, chunk,
                                      stage1_model=args.stage1_model, slug=slug)
                    if code != 0:
                        return code
                    row = db.chunks(conn, novel_id)[chunk.idx - 1]
                    if row["status"] == "refs_pending":
                        return 0   # the gate; run_stage1 already printed it
                    continue        # refs_approved (no new refs), no gate -- keep going
                if row["status"] == "refs_approved":
                    beats = current_beats(conn, row, chunk)
                    if not _stage2_done(conn, row["id"], beats):
                        before, _, _ = _batch_progress(conn, row["id"])
                        code = run_stage2(conn, llm, spec, src, novel_id, row, chunk, gen_model=args.gen_model,
                                          slug=slug)
                        if code != 0:
                            return code
                        after, _, _ = _batch_progress(conn, row["id"])
                        if after == before:
                            return fail(f"Chunk {chunk.idx}'s Stage 2 made no progress on batch starting at "
                                       f"beat {before}; this is a bug (run_stage2 returned success without "
                                       "storing a pass), not an operator-fixable state. Inspect the database.")
                        row = db.chunks(conn, novel_id)[chunk.idx - 1]
                        continue
                    return run_bible_update(conn, llm, spec, src, novel_id, row, chunk,
                                            bible_model=args.bible_model, slug=slug)
                if row["status"] == "bible_pending":
                    _write_all_batch_files(conn, spec, _current_bible(conn, novel_id), row, chunk, slug)
                    bound = _write_continuity_pending_if_missing(conn, row, slug)
                    _rewrite_manifest(conn, novel_id, src, slug)
                    print(f"Chunk {chunk.idx}'s batches and continuity update are ready; review {bound}, "
                         "then run --accept-bible to finish it.")
                    return 0
                return fail(f"Chunk {chunk.idx} is in an unexpected status {row['status']!r}.")
            return fail(f"Chunk {chunk.idx}'s continuation loop exceeded {OUTER_LOOP_CAP} iterations "
                       "without reaching a gate or `done`; re-run, or inspect the database for a status "
                       "that never advances.")
        except (LLMError, anthropic.AnthropicError) as e:
            log_error(config.LOG_DIR, f"{type(e).__name__}: {e}")
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
    finally:
        conn.close()
