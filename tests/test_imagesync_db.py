import json
import sqlite3

import pytest

from imagesync import db


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "i.db"))
    yield c
    c.close()


def novel(conn, slug="book", chunks=2):
    return db.create_novel(conn, slug, "Book", "srcsha", "scriptsha", chunks)


def rows(conn, table):
    return conn.execute(f"SELECT * FROM {table}").fetchall()


LOCK = {"kind": "style_lock", "model": None, "input_text": "", "output_text": "{}", "note": "sub-style c"}


def test_create_novel_is_atomic(conn, monkeypatch):
    nid = novel(conn)
    assert [tuple(r) for r in conn.execute("SELECT idx, status FROM chunks WHERE novel_id=?", (nid,))] == \
        [(1, "ready"), (2, "ready")]
    with pytest.raises(sqlite3.IntegrityError):
        novel(conn)  # duplicate slug: nothing half-written
    assert len(rows(conn, "novels")) == 1 and len(rows(conn, "chunks")) == 2


def test_add_bible_version_writes_pass_and_version(conn):
    nid = novel(conn)
    pid, vid = db.add_bible_version(conn, nid, None, "style_lock", json.dumps({"a": 1}), "{}", pass_fields=LOCK)
    v = db.latest_bible(conn, nid)
    assert v["id"] == vid and v["source_pass_id"] == pid and v["chunk_idx"] is None
    assert json.loads(v["json"]) == {"a": 1}
    assert conn.execute("SELECT kind, model FROM passes WHERE id=?", (pid,)).fetchone()[:] == ("style_lock", None)


def test_add_bible_version_is_atomic(conn):
    nid = novel(conn)
    cid = conn.execute("SELECT id FROM chunks WHERE idx=1").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):  # stage NOT NULL fails on the version insert
        db.add_bible_version(conn, nid, 1, None, "{}", "{}", pass_fields=LOCK, chunk_id=cid, new_status="beats")
    assert rows(conn, "passes") == [] and rows(conn, "bible_versions") == []
    assert conn.execute("SELECT status FROM chunks WHERE id=?", (cid,)).fetchone()[0] == "ready"


def test_one_style_lock_per_novel_and_unique_source_pass(conn):
    nid, other = novel(conn), novel(conn, "other")
    db.add_bible_version(conn, nid, None, "style_lock", "{}", "{}", pass_fields=LOCK)
    db.add_bible_version(conn, other, None, "style_lock", "{}", "{}", pass_fields=LOCK)  # other novel: fine
    with pytest.raises(sqlite3.IntegrityError):
        db.add_bible_version(conn, nid, None, "style_lock", "{}", "{}", pass_fields=LOCK)
    pid = db.add_pass(conn, nid, None, "refs", "m", "", "{}")
    db.add_bible_version(conn, nid, 1, "refs", "{}", "{}", source_pass_id=pid)
    with pytest.raises(sqlite3.IntegrityError):  # replaying the same accept
        db.add_bible_version(conn, nid, 1, "refs", "{}", "{}", source_pass_id=pid)
    assert len(rows(conn, "passes")) == 3 and len(rows(conn, "bible_versions")) == 3
