"""Command-line entry point. Phase 1: ingest the novel and plan chunks."""

import argparse
import hashlib
import re
from pathlib import Path

from plotpilot import config, db
from plotpilot.ingest import estimate_tokens, parse_novel, plan_chunks


def fail(msg: str) -> int:
    print(msg)
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="plotpilot", description="Convert a novel into a narration script.")
    ap.add_argument("--novel", required=True, type=Path, help="Path to the novel .txt file.")
    ap.add_argument("--out", default="./scripts",
                    help="Output directory for narration (ignored in Phase 1; used by later phases).")
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
        if parsed.all_folded:
            return fail(f"All {parsed.all_folded} detected chapters look like a table of contents "
                        f"(under {config.MIN_CHAPTER_WORDS} words, or mostly heading lines).")
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
    finally:
        conn.close()

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
    print(f"Plan stored in {config.DB_PATH}. Drafting is not built yet (Phase 2).")
    return 0
