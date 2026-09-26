import json
import sqlite3

import pytest

import imagesync.config as is_config
import plotpilot.config as pp_config
from imagesync.cli import main
from tests.helpers import draft1, finish_novel, run


def im(*args):
    return main(["--novel", "book.txt", *args])


def versions():
    with sqlite3.connect(is_config.DB_PATH) as conn:
        return conn.execute("SELECT chunk_idx, stage, json FROM bible_versions ORDER BY id").fetchall()


def counts():
    with sqlite3.connect(is_config.DB_PATH) as conn:
        return [conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                for t in ("novels", "chunks", "passes", "bible_versions")]


def test_unfinished_novel_exits_1(cwd, capsys):
    run("--module", "A", replies=[draft1()])
    assert im() == 1
    assert "chunk 1 is 'tracker_pending'" in capsys.readouterr().err


def test_first_run_prints_manifest_and_suggestion(cwd, capsys):
    finish_novel(cwd)
    capsys.readouterr()
    assert im() == 0
    out = capsys.readouterr().out
    assert "Novel: book — 2 chunks, 2 scenes" in out
    assert "  1  Ch 1–5" in out and "00:00–" in out
    assert ("Suggested sub-style: (c) Fantasy adventure manhwa (from dominant module A). Confirm with "
            "--sub-style c, or pick another. Aspect: 16:9 (change with --aspect).") in out
    assert versions() == []


@pytest.mark.parametrize("chapters, modules, letter, module", [
    (6, ("A", "B"), "c", "A"),          # tie: earliest chunk wins
    (6, ("B", "A"), "b", "B"),
    (11, ("B", "A", "A"), "c", "A"),    # dominant beats chunk 1's
])
def test_dominant_module(cwd, capsys, chapters, modules, letter, module):
    finish_novel(cwd, chapters, modules)
    capsys.readouterr()
    im()
    line = capsys.readouterr().out.split("Suggested sub-style: ")[1].split("\n")[0]
    assert line.startswith(f"({letter}) ") and f"(from dominant module {module})" in line


def test_style_lock_stored(cwd, capsys):
    finish_novel(cwd)
    assert im("--sub-style", "c") == 0
    [(chunk_idx, stage, js)] = versions()
    assert chunk_idx is None and stage == "style_lock"
    bible = json.loads(js)
    assert bible["style_lock"] == {"sub_style": "c", "aspect": "16:9", "genre_color_default": "A",
                                   "anchor_image": "-"}
    assert bible["slots"] == {"characters": [], "objects": []}
    assert all(bible[k] == [] for k in ("characters", "locations", "objects", "continuity_log", "revision_log"))
    assert "Style locked" in capsys.readouterr().out
    before = counts()
    assert im("--sub-style", "c") == 0 and counts() == before  # idempotent


def test_aspect_and_locked_note(cwd, capsys):
    finish_novel(cwd, modules=("B", "A"))
    im("--sub-style", "b", "--aspect", "9:16")
    assert json.loads(versions()[0][2])["style_lock"]["aspect"] == "9:16"
    assert json.loads(versions()[0][2])["style_lock"]["genre_color_default"] == "B"
    capsys.readouterr()
    assert im("--sub-style", "a") == 0
    assert "Note: --sub-style a ignored; the style is locked at chunk 1 (b, 9:16)." in capsys.readouterr().out
    assert len(versions()) == 1


def test_invalid_aspect_refused(cwd):
    finish_novel(cwd)
    with pytest.raises(SystemExit):
        im("--aspect", "3:2")


@pytest.mark.parametrize("tamper, which", [
    ("UPDATE novels SET source_sha256 = 'x'", "(source file hash)"),
    ("INSERT INTO passes (novel_id, chunk_id, kind, model, input_text, output_text, created_at) "
     "VALUES (1, 2, 'scenes', 'm', '', '{\"scenes\": [{\"first_sentence\": \"Gone.\", \"description\": \"z\"}]}',"
     " 'now')", "(script hash)"),
])
def test_hash_mismatch_refused(cwd, capsys, tamper, which):
    finish_novel(cwd)
    im("--sub-style", "c")
    with sqlite3.connect(pp_config.DB_PATH) as conn:
        conn.execute(tamper)
    capsys.readouterr()
    assert im() == 1
    err = capsys.readouterr().err
    assert "PlotPilot's data for 'book' changed since image sync started" in err and which in err


def test_real_client_is_guarded():
    import imagesync.cli as cli
    with pytest.raises(AssertionError, match="real Anthropic client"):
        cli.make_client()
