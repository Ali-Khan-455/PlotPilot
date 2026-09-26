"""imagesync.db: Image-Sync's own state. History is append-only; only chunks.status changes in place.
plotpilot.db is never written from here (see source.py)."""

import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS novels (
  id INTEGER PRIMARY KEY, slug TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
  plotpilot_source_sha TEXT NOT NULL, script_sha TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY, novel_id INTEGER NOT NULL REFERENCES novels(id), idx INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'ready', UNIQUE (novel_id, idx));
CREATE TABLE IF NOT EXISTS passes (
  id INTEGER PRIMARY KEY, novel_id INTEGER NOT NULL REFERENCES novels(id),
  chunk_id INTEGER REFERENCES chunks(id), kind TEXT NOT NULL, model TEXT,
  input_text TEXT NOT NULL, output_text TEXT NOT NULL, verdict TEXT, note TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bible_versions (
  id INTEGER PRIMARY KEY, novel_id INTEGER NOT NULL REFERENCES novels(id),
  chunk_idx INTEGER,                      -- NULL for novel-level versions (the style lock)
  stage TEXT NOT NULL,                    -- style_lock / refs / continuity
  source_pass_id INTEGER NOT NULL UNIQUE REFERENCES passes(id),   -- replay protection
  json TEXT NOT NULL, delta TEXT NOT NULL, accepted_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS one_style_lock ON bible_versions(novel_id) WHERE stage = 'style_lock';
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


def find_novel(conn, slug):
    return conn.execute("SELECT * FROM novels WHERE slug = ?", (slug,)).fetchone()


def create_novel(conn, slug, title, plotpilot_source_sha, script_sha, n_chunks) -> int:
    """The novel and its chunks (status 'ready') in one transaction."""
    with conn:
        nid = conn.execute(
            "INSERT INTO novels (slug, title, plotpilot_source_sha, script_sha, created_at) VALUES (?,?,?,?,?)",
            (slug, title, plotpilot_source_sha, script_sha, _now())).lastrowid
        conn.executemany("INSERT INTO chunks (novel_id, idx) VALUES (?, ?)",
                         [(nid, i) for i in range(1, n_chunks + 1)])
    return nid


def _insert_pass(conn, novel_id, chunk_id, kind, model, input_text, output_text, verdict=None, note=None):
    return conn.execute(
        "INSERT INTO passes (novel_id, chunk_id, kind, model, input_text, output_text, verdict, note, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (novel_id, chunk_id, kind, model, input_text, output_text, verdict, note, _now())).lastrowid


def add_pass(conn, novel_id, chunk_id, kind, model, input_text, output_text, *, verdict=None, note=None,
             new_status=None) -> int:
    """Insert a pass (and, atomically, the chunk's new status)."""
    with conn:
        pid = _insert_pass(conn, novel_id, chunk_id, kind, model, input_text, output_text, verdict, note)
        if new_status:
            conn.execute("UPDATE chunks SET status = ? WHERE id = ?", (new_status, chunk_id))
    return pid


def add_bible_version(conn, novel_id, chunk_idx, stage, bible_json, delta, *, source_pass_id=None,
                      pass_fields=None, chunk_id=None, new_status=None) -> tuple[int, int]:
    """Insert a Visual Bible version, bound to the pass it accepts, in ONE transaction. With pass_fields
    (kind, model, input_text, output_text, note) the pass is inserted here too; with source_pass_id an
    existing pass is bound. The optional chunk status change rides in the same transaction."""
    if (source_pass_id is None) == (pass_fields is None):
        raise ValueError("give exactly one of source_pass_id or pass_fields")
    with conn:
        pid = source_pass_id
        if pass_fields is not None:
            pid = _insert_pass(conn, novel_id, chunk_id, pass_fields["kind"], pass_fields["model"],
                               pass_fields["input_text"], pass_fields["output_text"],
                               note=pass_fields.get("note"))
        vid = conn.execute(
            "INSERT INTO bible_versions (novel_id, chunk_idx, stage, source_pass_id, json, delta, accepted_at)"
            " VALUES (?,?,?,?,?,?,?)", (novel_id, chunk_idx, stage, pid, bible_json, delta, _now())).lastrowid
        if new_status:
            conn.execute("UPDATE chunks SET status = ? WHERE id = ?", (new_status, chunk_id))
    return pid, vid


def latest_bible(conn, novel_id):
    """The newest accepted Visual Bible version, or None."""
    return conn.execute("SELECT * FROM bible_versions WHERE novel_id = ? ORDER BY id DESC LIMIT 1",
                        (novel_id,)).fetchone()
