"""Command-line entry point: plan chunks (stage 1), then drive chunk 1 (stage 2)."""

import argparse
import hashlib
import re
from pathlib import Path

import anthropic

from plotpilot import config, db
from plotpilot.ingest import estimate_tokens, parse_novel, plan_chunks
from plotpilot.llm import LLM, LLMError, log_error
from plotpilot.pipeline import run_chunk1
from plotpilot.prompts import load_prompts


def fail(msg: str) -> int:
    print(msg)
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
    args = ap.parse_args(argv)
    path: Path = args.novel

    if not path.is_file():
        return fail(f"Cannot read '{path}': not a regular file.")
    slug = re.sub(r"[^a-z0-9]+", "-", path.stem.lower()).strip("-")
    if not slug:
        return fail(f"Cannot derive a name from '{path.name}'; rename it with letters or digits.")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        text = path.read_text(encoding="utf-8-sig")  # text mode normalizes newlines
    except UnicodeDecodeError as e:
        return fail(f"Cannot read '{path}': not valid UTF-8 ({e.reason} at byte {e.start}).")

    parsed = parse_novel(text)
    if not parsed.chapters:
        return fail("No chapter headings found (expected 'Chapter N', 'Chapter IV', 'Chapter One', "
                    "'Prologue', 'Epilogue' on their own line after a blank line).")

    conn = db.connect(config.DB_PATH)
    try:
        existing = db.find_novel(conn, slug)
        if existing and existing[2] != sha:
            return fail(f"'{slug}' is already planned from {existing[1]} with different content. "
                        "History is append-only; rename the file to plan it as a new novel.")
        novel_id = existing[0] if existing else db.save_plan(
            conn, slug, path.stem, str(path), sha, plan_chunks(parsed.chapters))
        rows = db.load_chunks(conn, novel_id)
        _print_manifest(path, parsed, rows)
        llm = LLM(client if client is not None else (lambda: make_client()), config.LOG_DIR)
        try:
            return run_chunk1(conn, llm, load_prompts(), novel_id, slug, module=args.module,
                              redraft=args.redraft, repair=args.repair_margin, gen_model=args.gen_model,
                              qc_model=args.qc_model, out_dir=args.out)
        except (LLMError, anthropic.AnthropicError) as e:
            log_error(config.LOG_DIR, f"{type(e).__name__}: {e}")
            print(f"ERROR: {e}")
            return 1
    finally:
        conn.close()


def _print_manifest(path, parsed, rows):

    total = sum(c.words for c in parsed.chapters)
    print(f"Novel: {path.stem} — {len(parsed.chapters)} chapters, {total:,} words, {len(rows)} chunks")
    print(f"{'#':>3}  {'Chapters':<20}{'Words':>8}")
    for idx, label, words in rows:
        print(f"{idx:>3}  {label:<20}{words:>8,}")

    if parsed.front_words:
        print(f"WARNING: dropped {parsed.front_words:,} words of front matter "
              "(before the first chapter / table of contents / Gutenberg header).")
    if parsed.trailing_words:
        print(f"WARNING: dropped {parsed.trailing_words:,} words of trailing matter (Gutenberg licence).")
    if parsed.short_chapters:
        print(f"WARNING: chapter(s) {', '.join(map(str, parsed.short_chapters))} have fewer than "
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
