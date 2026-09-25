import sqlite3

import pytest

import plotpilot.config as config
from plotpilot.cli import main

BODY = " ".join(["word"] * 100)


def write_novel(path, n=7, body=BODY):
    path.write_text("\n\n".join(f"Chapter {i}\n\n{body}" for i in range(1, n + 1)) + "\n")
    return path


@pytest.fixture
def cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def rows(table):
    with sqlite3.connect(config.DB_PATH) as conn:
        return conn.execute(f"select count(*) from {table}").fetchone()[0]


def test_first_run_stores_plan_and_prints_manifest(cwd, capsys):
    write_novel(cwd / "My Novel.txt")
    assert main(["--novel", "My Novel.txt"]) == 0
    out = capsys.readouterr().out
    assert "Novel: My Novel — 7 chapters, 700 words, 2 chunks" in out
    assert "Ch 1–5" in out and "Ch 6–7" in out
    assert rows("novels") == 1 and rows("chunks") == 2


def test_second_run_is_idempotent(cwd, capsys):
    write_novel(cwd / "n.txt")
    main(["--novel", "n.txt"])
    first = capsys.readouterr().out
    assert main(["--novel", "n.txt"]) == 0
    assert capsys.readouterr().out == first
    assert rows("novels") == 1 and rows("chunks") == 2


def test_changed_source_is_refused(cwd, capsys):
    novel = write_novel(cwd / "n.txt")
    main(["--novel", "n.txt"])
    write_novel(novel, n=3)
    assert main(["--novel", "n.txt"]) == 1
    assert "already planned from" in capsys.readouterr().out
    assert rows("chunks") == 2


def test_missing_file_and_directory(cwd, capsys):
    assert main(["--novel", "nope.txt"]) == 1
    (cwd / "adir").mkdir()
    assert main(["--novel", "adir"]) == 1
    assert "not a regular file" in capsys.readouterr().out


def test_no_headings(cwd, capsys):
    (cwd / "n.txt").write_text("just prose\n\nmore prose\n")
    assert main(["--novel", "n.txt"]) == 1
    assert "No chapter headings found" in capsys.readouterr().out


def test_warnings_are_printed(cwd, capsys):
    long_body = " ".join(["w"] * 13_000)
    text = ("Front matter words\n*** START OF THE PROJECT GUTENBERG EBOOK X ***\n\n"
            "Chapter 1\n\nshort\n\nChapter 3\n\n" + BODY + "\n\nChapter 2\n\n" + long_body
            + "\n*** END OF THE PROJECT GUTENBERG EBOOK X ***\nlicence\n")
    (cwd / "n.txt").write_text(text)
    assert main(["--novel", "n.txt"]) == 0
    out = capsys.readouterr().out
    assert "dropped 12 words of front matter" in out
    assert "dropped 10 words of trailing matter" in out
    assert "chapter(s) 1 have fewer than 50 words" in out
    assert "goes backwards or repeats at line" in out
    assert "is 13,000 words with no scene break" in out


def test_empty_slug(cwd, capsys):
    write_novel(cwd / "!!!.txt")
    assert main(["--novel", "!!!.txt"]) == 1
    assert "Cannot derive a name" in capsys.readouterr().out


def test_bad_encoding(cwd, capsys):
    (cwd / "n.txt").write_bytes(b"Chapter 1\n\n\xff\xfe bad bytes\n")
    assert main(["--novel", "n.txt"]) == 1
    assert "UTF-8" in capsys.readouterr().out


def test_context_warning(cwd, capsys, monkeypatch):
    monkeypatch.setattr(config, "GEN_CONTEXT_TOKENS", 100)
    write_novel(cwd / "n.txt")
    assert main(["--novel", "n.txt"]) == 0
    assert "may exceed claude-sonnet-5's 100-token context" in capsys.readouterr().out
