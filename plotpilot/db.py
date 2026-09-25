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
CREATE TABLE IF NOT EXISTS passes (
  id INTEGER PRIMARY KEY, novel_id INTEGER NOT NULL REFERENCES novels(id),
  chunk_id INTEGER REFERENCES chunks(id), kind TEXT NOT NULL, model TEXT, module TEXT,
  input_text TEXT NOT NULL, output_text TEXT NOT NULL, verdict TEXT, note TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tracker_versions (
  id INTEGER PRIMARY KEY, novel_id INTEGER NOT NULL REFERENCES novels(id),
  chunk_id INTEGER NOT NULL UNIQUE REFERENCES chunks(id), json TEXT NOT NULL, delta TEXT NOT NULL,
  accepted_at TEXT NOT NULL);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
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
            (slug, title, source_path, sha256, _now()),
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def first_chunk(conn, novel_id):
    return conn.execute(
        "SELECT id, idx, status, source_text FROM chunks WHERE novel_id = ? ORDER BY idx LIMIT 1", (novel_id,)
    ).fetchone()


def set_status(conn, chunk_id, status):
    with conn:
        conn.execute("UPDATE chunks SET status = ? WHERE id = ?", (status, chunk_id))


def add_pass(conn, novel_id, chunk_id, kind, model, input_text, output_text,
             *, module=None, verdict=None, note=None, new_status=None) -> int:
    """Insert one pass row, and optionally set the chunk's status, in ONE transaction.
    Rows are never updated: the verdict is known before insert."""
    with conn:
        pass_id = conn.execute(
            "INSERT INTO passes (novel_id, chunk_id, kind, model, module, input_text, output_text,"
            " verdict, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (novel_id, chunk_id, kind, model, module, input_text, output_text, verdict, note, _now()),
        ).lastrowid
        if new_status:
            conn.execute("UPDATE chunks SET status = ? WHERE id = ?", (new_status, chunk_id))
        return pass_id


def ok_passes(conn, chunk_id, kinds):
    """All ok passes (verdict IS NULL) of the given kinds, oldest first."""
    marks = ",".join("?" * len(kinds))
    return conn.execute(
        f"SELECT id, kind, output_text FROM passes WHERE chunk_id = ? AND verdict IS NULL"
        f" AND kind IN ({marks}) ORDER BY id", (chunk_id, *kinds),
    ).fetchall()


def latest_pass(conn, chunk_id, kind, after_id=None):
    """Newest ok pass (verdict IS NULL — the only definition of ok), optionally newer than after_id."""
    return conn.execute(
        "SELECT id, module, output_text, note FROM passes WHERE chunk_id = ? AND kind = ?"
        " AND verdict IS NULL AND id > ? ORDER BY id DESC LIMIT 1",
        (chunk_id, kind, after_id or 0),
    ).fetchone()


def chunks(conn, novel_id):
    return conn.execute(
        "SELECT id, idx, label, status, source_text FROM chunks WHERE novel_id = ? ORDER BY idx", (novel_id,)
    ).fetchall()


def chunk(conn, chunk_id):
    return conn.execute(
        "SELECT id, idx, label, status, source_text FROM chunks WHERE id = ?", (chunk_id,)).fetchone()


def add_tracker_version(conn, novel_id, chunk_id, tracker_json, delta_json, new_status="done") -> int:
    """Insert the accepted tracker version and set the chunk's status in ONE transaction.
    UNIQUE(chunk_id) makes a second accept for the same chunk impossible."""
    with conn:
        vid = conn.execute(
            "INSERT INTO tracker_versions (novel_id, chunk_id, json, delta, accepted_at) VALUES (?, ?, ?, ?, ?)",
            (novel_id, chunk_id, tracker_json, delta_json, _now()),
        ).lastrowid
        conn.execute("UPDATE chunks SET status = ? WHERE id = ?", (new_status, chunk_id))
        return vid


def latest_tracker(conn, novel_id):
    """The newest accepted tracker JSON string, or None."""
    row = conn.execute("SELECT json FROM tracker_versions WHERE novel_id = ? ORDER BY id DESC LIMIT 1",
                       (novel_id,)).fetchone()
    return row["json"] if row else None
