"""Command-line entry point for Image-Sync. IS-1: read a finished PlotPilot novel, bind to it, print the
manifest, and run the style-lock gate. No LLM calls yet."""

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import anthropic

from imagesync import config, db
from imagesync.source import SourceError, load_novel, open_plotpilot
from imagesync.spec import load_spec
from plotpilot import config as pp_config
from plotpilot.cli import slug_for
from plotpilot.llm import log_error

MISMATCH = ("PlotPilot's data for '{slug}' changed since image sync started ({which}). It changes when (1) the "
            "novel is rebuilt in PlotPilot from a different source file, (2) the script or scenes change (e.g. "
            "a later scenes pass), or (3) PlotPilot's assemble code or WORDS_PER_MINUTE changes. Image sync "
            "can't reuse beats built from other narration.")


def make_client():
    """Not used before IS-2; defined so tests can guard it (conftest)."""
    return anthropic.Anthropic(max_retries=3)


def fail(msg: str) -> int:
    print(msg, file=sys.stderr)
    log_error(config.LOG_DIR, msg)
    return 1


def _stamp(sec: int) -> str:
    return f"{sec // 60:02d}:{sec % 60:02d}"


def _seconds(timecode: str) -> int:
    m, s = timecode.split("-")
    return int(m) * 60 + int(s)


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


def _style_gate(conn, novel_id, src, args, spec) -> int:
    locked = db.latest_bible(conn, novel_id)
    if locked:
        lock = json.loads(locked["json"])["style_lock"]
        where = f"{lock['sub_style']}, {lock['aspect']}"
        if args.sub_style and args.sub_style != lock["sub_style"]:
            print(f"Note: --sub-style {args.sub_style} ignored; the style is locked at chunk 1 ({where}).")
        if args.aspect and args.aspect != lock["aspect"]:
            print(f"Note: --aspect {args.aspect} ignored; the style is locked at chunk 1 ({where}).")
        print(f"Style locked: ({lock['sub_style']}) {_name(spec, lock['sub_style'])}, {lock['aspect']}. "
              "Stage 0 (beats) is not built yet (IS-2).")
        return 0
    module = dominant_module(src)
    if not args.sub_style:
        letter = config.MODULE_TO_SUBSTYLE[module]
        print(f"Suggested sub-style: ({letter}) {_name(spec, letter)} (from dominant module {module}). "
              f"Confirm with --sub-style {letter}, or pick another. "
              f"Aspect: {args.aspect or config.DEFAULT_ASPECT} (change with --aspect).")
        return 0
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
    except sqlite3.IntegrityError:  # a concurrent run locked it first
        print("Style already locked (another run got there first); re-run to see it.")
        return 0
    print(f"Style locked: ({args.sub_style}) {_name(spec, args.sub_style)}, {aspect}. "
          "Stage 0 (beats) is not built yet (IS-2).")
    return 0


def _name(spec, letter) -> str:
    return spec.sub_styles[letter].split(" — ")[0]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="imagesync", description="Turn a PlotPilot script into image prompts.")
    ap.add_argument("--novel", required=True, type=Path, help="The novel .txt file PlotPilot converted.")
    ap.add_argument("--plotpilot-db", default=config.PLOTPILOT_DB, help="PlotPilot's database (read only).")
    ap.add_argument("--sub-style", choices=["a", "b", "c", "d"], help="Lock the novel's sub-style (chunk 1).")
    ap.add_argument("--aspect", choices=config.ASPECTS, help=f"Aspect ratio (default {config.DEFAULT_ASPECT}).")
    args = ap.parse_args(argv)
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
        return _style_gate(conn, novel_id, src, args, spec)
    finally:
        conn.close()
