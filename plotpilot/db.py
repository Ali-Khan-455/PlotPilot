"""SQLite storage. Append-only history; later phases add tables with IF NOT EXISTS."""

import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS novels (
  id INTEGER PRIMARY KEY, slug TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
  source_path TEXT NOT NULL, source_sha256 TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY, novel_id INTEGER NOT NULL REFERENCES novels(id),
  idx INTEGER NOT NULL, label TEXT NOT NULL, chapter_start INTEGER NOT NULL, chapter_end INTEGER NOT NULL,
  word_count INTEGER NOT NULL, source_text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'planned',
  UNIQUE (novel_id, idx));
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


def find_novel(conn, slug):
    """Return (id, source_path, source_sha256) or None."""
    return conn.execute(
        "SELECT id, source_path, source_sha256 FROM novels WHERE slug = ?", (slug,)
    ).fetchone()


def save_plan(conn, slug, title, source_path, sha256, chunks) -> int:
    with conn:  # one transaction: the stored plan is all-or-nothing
        novel_id = conn.execute(
            "INSERT INTO novels (slug, title, source_path, source_sha256, created_at) VALUES (?, ?, ?, ?, ?)",
            (slug, title, source_path, sha256, datetime.now(timezone.utc).isoformat()),
        ).lastrowid
        conn.executemany(
            "INSERT INTO chunks (novel_id, idx, label, chapter_start, chapter_end, word_count, source_text)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(novel_id, c.idx, c.label, c.chapter_start, c.chapter_end, c.words, c.text) for c in chunks],
        )
    return novel_id


def load_chunks(conn, novel_id):
    """Return [(idx, label, word_count)] in order."""
    return conn.execute(
        "SELECT idx, label, word_count FROM chunks WHERE novel_id = ? ORDER BY idx", (novel_id,)
    ).fetchall()
