import hashlib
import json
import sqlite3

import pytest

import plotpilot.config as pp_config
from imagesync.source import SourceError, load_novel, open_plotpilot
from tests.helpers import BODY1, all_done, draft1, finish_novel, hook_reply, run


def load(slug="book", path=None):
    return load_novel(open_plotpilot(path or pp_config.DB_PATH), slug)


def test_loads_a_finished_novel(cwd):
    finish_novel(cwd)
    src = load()
    assert src.title == "book" and len(src.chunks) == 2
    c1, c2 = src.chunks
    assert (c1.idx, c1.label, c1.module) == (1, "Ch 1–5", "A") and c2.module == "B"
    assert c1.narration == BODY1 and c1.chapters_text.startswith("Chapter 1")
    assert c1.scenes == [("00-00", "scene")]
    assert c2.scenes[0][0] != "00-00" and c2.chapters_text.startswith("Chapter 6")
    with sqlite3.connect(pp_config.DB_PATH) as conn:
        assert src.plotpilot_source_sha == conn.execute("SELECT source_sha256 FROM novels").fetchone()[0]
    assert len(src.script_sha) == 64 and load().script_sha == src.script_sha  # stable across runs


def test_never_writes_plotpilot_db(cwd):
    finish_novel(cwd)
    before = hashlib.sha256((cwd / pp_config.DB_PATH).read_bytes()).hexdigest()
    load()
    assert hashlib.sha256((cwd / pp_config.DB_PATH).read_bytes()).hexdigest() == before


def test_refuses_missing_db_and_unknown_slug(cwd):
    with pytest.raises(SourceError, match="not found"):
        open_plotpilot(cwd / "nope.db")
    finish_novel(cwd)
    with pytest.raises(SourceError, match="'other'"):
        load("other")


def test_refuses_unfinished_chunk(cwd):
    run("--module", "A", replies=[draft1()])
    with pytest.raises(SourceError, match="chunk 1 is 'tracker_pending'; finish it in PlotPilot first"):
        load()


def test_refuses_novel_without_hook_tts(cwd):
    all_done([hook_reply(), "junk", "junk"])
    with pytest.raises(SourceError, match="The hook, or its TTS pass, is missing for 'book'"):
        load()


def test_duplicate_timestamps_are_returned(cwd):
    finish_novel(cwd)
    with sqlite3.connect(pp_config.DB_PATH) as conn:  # a later ok scenes pass whose sentence isn't found
        cid = conn.execute("SELECT id FROM chunks WHERE idx = 2").fetchone()[0]
        conn.execute("INSERT INTO passes (novel_id, chunk_id, kind, model, input_text, output_text, created_at)"
                     " VALUES (1, ?, 'scenes', 'm', '', ?, 'now')",
                     (cid, json.dumps({"scenes": [{"first_sentence": "Not in the text.", "description": "a"},
                                                  {"first_sentence": "Also missing.", "description": "b"}]})))
    c2 = load().chunks[1]
    assert [s[1] for s in c2.scenes] == ["a", "b"] and c2.scenes[0][0] == c2.scenes[1][0]
