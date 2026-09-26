"""Command-line entry point: plan chunks (stage 1), then drive chunk 1 (stage 2)."""

import argparse
import hashlib
import re
import sqlite3
import sys
from pathlib import Path

import anthropic

from plotpilot import config, db
from plotpilot.ingest import Parsed, estimate_tokens, parse_novel, plan_chunks
from plotpilot.llm import LLM, LLMError, log_error
from plotpilot.pipeline import run_novel
from plotpilot.prompts import load_prompts


NO_HEADINGS = ("No chapter headings found (expected 'Chapter N', 'Chapter IV', 'Chapter One', "
               "'Prologue', 'Epilogue' on their own line after a blank line).")


def fail(msg: str) -> int:
    print(msg, file=sys.stderr)
    return 1


def make_client():
    return anthropic.Anthropic(max_retries=3)


def main(argv=None, client=None) -> int:
    ap = argparse.ArgumentParser(prog="plotpilot", description="Convert a novel into a narration script.")
    ap.add_argument("--novel", required=True, type=Path, help="Path to the novel .txt file.")
    ap.add_argument("--out", default="./scripts", help="Output directory for narration files.")
    ap.add_argument("--module", type=str.upper, choices=["A", "B", "C", "D"],
                    help="Niche module for the chunk being drafted (Prompt 2).")
    ap.add_argument("--redraft", action="store_true", help="Redraft chunk 1 even though it is drafted.")
    ap.add_argument("--repair-margin", action="store_true", help="Force Prompt 3-REPAIR on chunk 1's margin.")
    ap.add_argument("--gen-model", default=config.GEN_MODEL, help="Model for drafts and repairs.")
    ap.add_argument("--qc-model", default=config.QC_MODEL, help="Model for classification and QC passes.")
    ap.add_argument("--accept-factcheck", metavar="REASON",
                    help="Accept a failed fact-check for the current chunk; the reason is logged.")
    ap.add_argument("--accept-tracker", action="store_true",
                    help="Merge the reviewed tracker update for the current chunk.")
    args = ap.parse_args(argv)
    if args.accept_tracker and (args.redraft or args.repair_margin):
        flag = "--redraft" if args.redraft else "--repair-margin"
        return fail(f"{flag} and --accept-tracker can't be combined; review the result before accepting.")
    if args.accept_factcheck is not None and not args.accept_factcheck.strip():
        return fail("--accept-factcheck needs a non-empty reason.")
    path: Path = args.novel

    if not path.is_file():
        return fail(f"Cannot read '{path}': not a regular file.")
    slug = re.sub(r"[^a-z0-9]+", "-", path.stem.lower()).strip("-")
    if not slug:
        return fail(f"Cannot derive a name from '{path.name}'; rename it with letters or digits.")
    try:
        raw = path.read_bytes()  # read once: the sha and the parsed text come from the same bytes
    except OSError as e:
        return fail(f"Cannot read '{path}': {e.strerror or e}.")
    sha = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError as e:
        return fail(f"Cannot read '{path}': not valid UTF-8 ({e.reason} at byte {e.start}).")

    parsed = parse_novel(text)
    if not parsed.chapters and not Path(config.DB_PATH).exists():  # nothing stored yet: don't create the DB
        return fail(NO_HEADINGS)
    try:
        conn = db.connect(config.DB_PATH)
    except sqlite3.Error as e:
        return fail(f"Cannot open the database {config.DB_PATH}: {e}.")
    try:
        existing = db.find_novel(conn, slug)
        if existing and existing[2] != sha:
            return fail(f"'{slug}' is already planned from {existing[1]} with different content. "
                        "History is append-only; rename the file to plan it as a new novel.")
        if not existing and not parsed.chapters:
            return fail(NO_HEADINGS)
        novel_id = existing[0] if existing else db.save_plan(
            conn, slug, path.stem, str(path.resolve()), sha, plan_chunks(parsed.chapters))
        rows = db.load_chunks(conn, novel_id)
        _print_manifest(path, parsed, rows, db.plan_totals(conn, novel_id))
        llm = LLM(client if client is not None else (lambda: make_client()), config.LOG_DIR)
        try:
            return run_novel(conn, llm, load_prompts(), novel_id, slug, path.stem, module=args.module,
                              redraft=args.redraft, repair=args.repair_margin, gen_model=args.gen_model,
                              qc_model=args.qc_model, out_dir=args.out,
                              accept=args.accept_factcheck.strip() if args.accept_factcheck else None,
                              accept_tracker=args.accept_tracker)
        except (LLMError, anthropic.AnthropicError) as e:
            log_error(config.LOG_DIR, f"{type(e).__name__}: {e}")
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
    except sqlite3.OperationalError as e:
        # Only an unwritable or locked database is an operator problem; anything else is a bug, so re-raise.
        if not re.search(r"readonly|read-only|locked|unable to open|disk I/O", str(e), re.I):
            raise
        log_error(config.LOG_DIR, f"OperationalError: {e}")
        return fail(f"Cannot write the database {config.DB_PATH}: {e}. Check it is writable and not in use.")
    finally:
        conn.close()


def _print_manifest(path, parsed, rows, stored):
    """The header and rows come from the stored plan; warnings come from the fresh parse."""
    chapters, total = stored
    print(f"Novel: {path.stem} — {chapters} chapters, {total:,} words, "
          f"{len(rows)} chunk{'' if len(rows) == 1 else 's'}")
    fresh = (len(parsed.chapters), sum(c.words for c in parsed.chapters))
    changed = fresh != (chapters, total)
    if changed:
        print(f"Note: the parser now reads this file as {fresh[0]} chapters, {fresh[1]:,} words; "
              "the stored plan is used (its parse warnings are not shown).")
    print(f"{'#':>3}  {'Chapters':<20}{'Words':>8}")
    for idx, label, words in rows:
        print(f"{idx:>3}  {label:<20}{words:>8,}")

    if changed:
        parsed = Parsed([])  # warnings from a parse that doesn't match the stored plan would mislead
    if parsed.front_words:
        print(f"WARNING: dropped {parsed.front_words:,} words of front matter "
              "(before the first chapter / table of contents / Gutenberg header).")
    if parsed.trailing_words:
        print(f"WARNING: dropped {parsed.trailing_words:,} words of trailing matter (Gutenberg licence).")
    if parsed.short_chapters:
        short = ", ".join(f"{c.idx} ('{c.heading}')" for c in parsed.chapters if c.idx in parsed.short_chapters)
        print(f"WARNING: chapter(s) {short} have fewer than "
              f"{config.MIN_CHAPTER_WORDS} words; check they are real chapters.")
    for w in parsed.sequence_warnings:
        print(w)
    for idx, label, words in rows:
        if words > config.MAX_WORDS:
            print(f"WARNING: chunk {idx} ({label}) is {words:,} words with no scene break to split at.")
    tokens = estimate_tokens(total)
    if tokens > config.GEN_CONTEXT_TOKENS:
        print(f"WARNING: source is ~{tokens:,} tokens; Prompt 5 needs the full script and may exceed "
              f"{config.GEN_MODEL}'s {config.GEN_CONTEXT_TOKENS:,}-token context. "
              "Consider splitting the novel into Parts.")
    print(f"Plan stored in {config.DB_PATH}.")
