import sqlite3
from pathlib import Path

import pytest

import plotpilot.config as config
from plotpilot.cli import main
from tests.fakes import FakeClient

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
    assert main(["--novel", "My Novel.txt"], client=FakeClient(["B"])) == 0
    out = capsys.readouterr().out
    assert "Suggested module for chunk 1: B (Romance, Josei, Emotional Drama)" in out
    assert "Novel: My Novel — 7 chapters, 700 words, 2 chunks" in out
    assert "Ch 1–5" in out and "Ch 6–7" in out
    assert rows("novels") == 1 and rows("chunks") == 2


def test_second_run_is_idempotent(cwd, capsys):
    write_novel(cwd / "n.txt")
    main(["--novel", "n.txt"], client=FakeClient(["B"]))
    first = capsys.readouterr().out
    assert main(["--novel", "n.txt"], client=FakeClient([])) == 0
    assert capsys.readouterr().out == first
    assert rows("novels") == 1 and rows("chunks") == 2


def test_changed_source_is_refused(cwd, capsys):
    novel = write_novel(cwd / "n.txt")
    main(["--novel", "n.txt"], client=FakeClient(["B"]))
    write_novel(novel, n=3)
    assert main(["--novel", "n.txt"]) == 1
    assert "already planned from" in capsys.readouterr().err
    assert rows("chunks") == 2


def test_missing_file_and_directory(cwd, capsys):
    assert main(["--novel", "nope.txt"]) == 1
    (cwd / "adir").mkdir()
    assert main(["--novel", "adir"]) == 1
    assert "not a regular file" in capsys.readouterr().err


def test_no_headings(cwd, capsys):
    (cwd / "n.txt").write_text("just prose\n\nmore prose\n")
    assert main(["--novel", "n.txt"]) == 1
    assert "No chapter headings found" in capsys.readouterr().err


def test_warnings_are_printed(cwd, capsys):
    long_body = " ".join(["w"] * 13_000)
    text = ("Front matter words\n*** START OF THE PROJECT GUTENBERG EBOOK X ***\n\n"
            "Chapter 1\n\nshort\n\nChapter 3\n\n" + BODY + "\n\nChapter 2\n\n" + long_body
            + "\n*** END OF THE PROJECT GUTENBERG EBOOK X ***\nlicence\n")
    (cwd / "n.txt").write_text(text)
    assert main(["--novel", "n.txt"], client=FakeClient(["A"])) == 0
    out = capsys.readouterr().out
    assert "dropped 12 words of front matter" in out
    assert "dropped 10 words of trailing matter" in out
    assert "chapter(s) 1 ('Chapter 1') have fewer than 50 words" in out
    assert "goes backwards or repeats at line" in out
    assert "is 13,000 words with no scene break" in out


def test_empty_slug(cwd, capsys):
    write_novel(cwd / "!!!.txt")
    assert main(["--novel", "!!!.txt"]) == 1
    assert "Cannot derive a name" in capsys.readouterr().err


def test_bad_encoding(cwd, capsys):
    (cwd / "n.txt").write_bytes(b"Chapter 1\n\n\xff\xfe bad bytes\n")
    assert main(["--novel", "n.txt"]) == 1
    assert "UTF-8" in capsys.readouterr().err


def test_context_warning(cwd, capsys, monkeypatch):
    monkeypatch.setattr(config, "GEN_CONTEXT_TOKENS", 100)
    write_novel(cwd / "n.txt")
    assert main(["--novel", "n.txt"], client=FakeClient(["A"])) == 0
    assert "may exceed claude-sonnet-5's 100-token context" in capsys.readouterr().out


# --- deferred minors (Phase 1) --------------------------------------------------

def test_unreadable_novel_is_a_clean_error(cwd, capsys, monkeypatch):
    write_novel(cwd / "n.txt")
    monkeypatch.setattr(Path, "read_bytes", lambda self: (_ for _ in ()).throw(PermissionError(13, "denied")))
    assert main(["--novel", "n.txt"]) == 1
    assert "Cannot read 'n.txt': denied" in capsys.readouterr().err


def test_unwritable_database_is_a_clean_error(cwd, capsys, monkeypatch):
    write_novel(cwd / "n.txt")
    monkeypatch.setattr(config, "DB_PATH", str(cwd / "missing-dir" / "p.db"))
    assert main(["--novel", "n.txt"]) == 1
    assert "Cannot open the database" in capsys.readouterr().err


def test_novel_is_read_once(cwd, monkeypatch):
    write_novel(cwd / "n.txt")
    (cwd / "n.txt").write_bytes((cwd / "n.txt").read_bytes().replace(b"\n", b"\r\n"))
    reads = []
    real = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: reads.append(self) or real(self))
    real_text = Path.read_text

    def read_text(self, *a, **k):
        assert self.name != "n.txt", "the novel was read twice"
        return real_text(self, *a, **k)
    monkeypatch.setattr(Path, "read_text", read_text)
    assert main(["--novel", "n.txt"], client=FakeClient(["B"])) == 0
    assert len(reads) == 1


def test_errors_go_to_stderr(cwd, capsys):
    assert main(["--novel", "nope.txt"]) == 1
    out, err = capsys.readouterr()
    assert out == "" and "not a regular file" in err


def test_source_path_is_absolute(cwd, capsys):
    novel = write_novel(cwd / "n.txt")
    main(["--novel", "n.txt"], client=FakeClient(["B"]))
    write_novel(novel, n=3)
    assert main(["--novel", "n.txt"]) == 1
    assert f"already planned from {cwd / 'n.txt'} " in capsys.readouterr().err


def test_manifest_says_one_chunk(cwd, capsys):
    write_novel(cwd / "n.txt", n=2)
    main(["--novel", "n.txt"], client=FakeClient(["B"]))
    assert "2 chapters, 200 words, 1 chunk\n" in capsys.readouterr().out


def test_header_comes_from_stored_plan_after_parser_change(cwd, capsys, monkeypatch):
    write_novel(cwd / "n.txt")
    main(["--novel", "n.txt"], client=FakeClient(["B"]))
    capsys.readouterr()
    import plotpilot.cli as cli
    from plotpilot.ingest import Parsed
    monkeypatch.setattr(cli, "parse_novel", lambda text: Parsed([]))
    assert main(["--novel", "n.txt"], client=FakeClient([])) == 0
    out = capsys.readouterr().out
    assert "Novel: n — 7 chapters, 700 words, 2 chunks" in out
    assert "the stored plan is used" in out


# --- remaining deferred minors ------------------------------------------------------

def test_no_headings_creates_no_database(cwd):
    (cwd / "n.txt").write_text("just prose\n\nmore prose\n")
    assert main(["--novel", "n.txt"]) == 1
    assert not (cwd / config.DB_PATH).exists()


def test_database_write_error_is_clean(cwd, capsys, monkeypatch):
    import plotpilot.db as db
    write_novel(cwd / "n.txt")
    monkeypatch.setattr(db, "save_plan", lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError(
        "attempt to write a readonly database")))
    assert main(["--novel", "n.txt"]) == 1
    assert "Cannot write the database" in capsys.readouterr().err


def test_fresh_parse_warnings_suppressed_for_changed_parser(cwd, capsys, monkeypatch):
    write_novel(cwd / "n.txt")
    main(["--novel", "n.txt"], client=FakeClient(["B"]))
    capsys.readouterr()
    import plotpilot.cli as cli
    from plotpilot.ingest import Parsed
    monkeypatch.setattr(cli, "parse_novel", lambda text: Parsed([], short_chapters=[3], front_words=9))
    main(["--novel", "n.txt"], client=FakeClient([]))
    out = capsys.readouterr().out
    assert "the stored plan is used" in out and "WARNING: dropped" not in out and "fewer than" not in out



def test_other_sqlite_errors_are_not_swallowed(cwd, monkeypatch):
    import plotpilot.cli as cli
    write_novel(cwd / "n.txt")
    monkeypatch.setattr(cli, "run_novel", lambda *a, **k: (_ for _ in ()).throw(
        sqlite3.OperationalError("no such column: foo")))
    import pytest
    with pytest.raises(sqlite3.OperationalError):
        main(["--novel", "n.txt"], client=FakeClient([]))
