import sqlite3

import pytest

import plotpilot.config as config
from plotpilot.cli import main
from tests.fakes import FakeClient, connection_error

BODY = " ".join(["word"] * 100)
MARGIN = "I am a farmer's son in a poor village."
TARGET = "Nobody expected much from me."


def draft(margin=MARGIN, target=TARGET, rest="Then the story went on."):
    return (f"<<<MARGIN_START>>>\n{margin}\n<<<MARGIN_END>>>\n"
            f"<<<TARGET_SENTENCE_START>>>\n{target}\n<<<TARGET_SENTENCE_END>>>\n"
            f"margin is 1 sentences.\n\n{target} {rest}")


def repair(margin):
    return f"<<<MARGIN_START>>>\n{margin}\n<<<MARGIN_END>>>"


@pytest.fixture
def cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "book.txt").write_text(
        "\n\n".join(f"Chapter {i}\n\n{BODY}" for i in range(1, 4)) + "\n")
    return tmp_path


def run(*args, replies=(), client=None):
    # Drafting runs continue into QC (Phase 3); auto_qc answers Prompts 6, 7 and 9 cleanly.
    client = client or FakeClient(replies, auto_qc=True)
    code = main(["--novel", "book.txt", *args], client=client)
    return code, client


def passes(kinds=("classify", "draft", "margin_repair")):
    """Stage-2 passes only (QC passes are covered in test_qc.py)."""
    marks = ",".join("?" * len(kinds))
    with sqlite3.connect(config.DB_PATH) as conn:
        return conn.execute(
            "SELECT kind, model, module, verdict, note, input_text, output_text FROM passes"
            f" WHERE kind IN ({marks}) ORDER BY id", kinds).fetchall()


def status():
    with sqlite3.connect(config.DB_PATH) as conn:
        return conn.execute("SELECT status FROM chunks WHERE idx = 1").fetchone()[0]


def chunk_file(cwd, out="scripts"):
    return (cwd / out / "book" / "chunk-01.txt").read_text()


# --- module gate ---------------------------------------------------------------

def test_gate_classifies_once_and_reuses(cwd, capsys):
    code, client = run(replies=["C"])
    assert code == 0 and status() == "planned"
    assert "Suggested module for chunk 1: C (Dark Action, Revenge, Thriller)" in capsys.readouterr().out
    assert client.retrieved == [config.QC_MODEL]
    user = client.calls[0]["messages"][0]["content"]
    for heading in ["MODULE A — Isekai", "MODULE B — Romance", "MODULE C — Dark Action", "MODULE D — Comedy"]:
        assert heading in user
    assert "system" not in client.calls[0]
    row = passes()[0]
    assert row[0] == "classify" and not row[5].startswith("\n\n=====")

    code, client = run(replies=[])
    assert code == 0 and client.calls == [] and client.retrieved == []


def test_gate_malformed_twice_fails(cwd):
    code, _ = run(replies=["Module C", "I think C"])
    assert code == 1 and [p[3] for p in passes()] == ["PARSE_FAILED", "PARSE_FAILED"]


# --- draft -------------------------------------------------------------------

def test_draft_writes_clean_narration(cwd):
    code, client = run("--module", "B", replies=[draft()])
    assert code == 0 and status() == "tracker_pending"
    assert client.retrieved == [config.GEN_MODEL, config.QC_MODEL]  # the draft checks only gen_model
    text = chunk_file(cwd)
    assert "<<<" not in text and "margin is" not in text
    assert text.count(MARGIN) == 1 and text.count(TARGET) == 1
    assert text.startswith(MARGIN + "\n\n" + TARGET)
    kind, model, module, verdict, note, input_text, _ = passes()[0]
    assert (kind, model, module, verdict) == ("draft", config.GEN_MODEL, "B", None)
    assert "Universal" not in input_text  # the heading is not part of the prompt text
    assert "You are converting a novel" in input_text and "This batch is romance" in input_text
    assert "\n\n=====\n\n" in input_text
    assert client.calls[0]["max_tokens"] == config.GEN_MAX_TOKENS


def test_classify_then_draft_retrieves_each_model_once(cwd):
    client = FakeClient(["B", draft()], auto_qc=True)
    main(["--novel", "book.txt"], client=client)
    main(["--novel", "book.txt", "--module", "B"], client=client)
    # run 1: classify (qc); run 2: draft (gen) then QC (qc) — each model once per run, never twice
    assert client.retrieved == [config.QC_MODEL, config.GEN_MODEL, config.QC_MODEL]


def test_hooky_margin_triggers_repair(cwd, capsys):
    code, client = run("--module", "A", replies=[draft(margin="Who am I?"), repair("I am a boy.")])
    assert code == 0
    out = capsys.readouterr().out
    assert "Margin check fired: question mark" in out
    assert chunk_file(cwd).startswith("I am a boy.\n\n" + TARGET)
    assert passes()[1][0] == "margin_repair" and passes()[1][4] == "question mark"
    assert client.calls[1]["max_tokens"] == config.REPAIR_MAX_TOKENS


def test_malformed_then_good_draft(cwd):
    code, _ = run("--module", "A", replies=["no markers here", draft()])
    assert code == 0 and status() == "tracker_pending"
    assert [p[3] for p in passes()] == ["PARSE_FAILED", None]


def test_malformed_twice_fails(cwd, capsys):
    code, _ = run("--module", "A", replies=["bad", "still bad"])
    assert code == 1 and status() == "planned" and len(passes()) == 2
    assert "malformed twice" in capsys.readouterr().out


def test_target_mid_body_twice_fails(cwd):
    bad = draft().replace(f"\n\n{TARGET} Then", f"\n\nOpening. {TARGET} Then")
    code, _ = run("--module", "A", replies=[bad, bad])
    assert code == 1 and status() == "planned"


def test_truncated_draft_is_stored(cwd):
    code, _ = run("--module", "A", replies=[("half a draft", "max_tokens")])
    assert code == 1 and status() == "planned"
    assert passes()[0][3] == "STOPPED:max_tokens" and passes()[0][6] == "half a draft"
    assert len((cwd / "logs" / "usage.csv").read_text().splitlines()) == 2


def test_crash_then_redraft_ignores_stale_repair(cwd):
    run("--module", "A", replies=[draft(margin="Who am I?"), repair("Stale repaired margin.")])
    with sqlite3.connect(config.DB_PATH) as conn:  # simulate a crash before the status update
        conn.execute("UPDATE chunks SET status = 'planned'")
    code, _ = run("--module", "A", replies=[draft(margin="Fresh plain margin.")])
    assert code == 0 and chunk_file(cwd).startswith("Fresh plain margin.\n\n")


def test_forced_repair_with_module(cwd):
    code, client = run("--module", "A", "--repair-margin", replies=[draft(), repair("Forced margin.")])
    assert code == 0 and [c["model"] for c in client.calls[:2]] == [config.GEN_MODEL] * 2
    assert passes()[1][4] == "forced by operator"
    assert chunk_file(cwd).startswith("Forced margin.\n\n")


def test_repair_failure_keeps_draft_margin(cwd, capsys):
    code, _ = run("--module", "A", replies=[draft(margin="Who am I?"), "junk", "junk"])
    assert code == 0 and status() == "tracker_pending"
    assert "margin repair failed" in capsys.readouterr().out
    assert chunk_file(cwd).startswith("Who am I?\n\n")


def test_stopped_repair_is_ignored_on_rerun(cwd, capsys):
    run("--module", "A", replies=[draft(margin="Who am I?"), ("<<<MARGIN_START>>>\nhal", "max_tokens")])
    capsys.readouterr()
    code, client = run(replies=[])
    assert code == 0 and client.calls == []
    assert chunk_file(cwd).startswith("Who am I?\n\n")


# --- drafted chunk ------------------------------------------------------------

def test_rerun_on_drafted_chunk_makes_no_calls(cwd, capsys):
    run("--module", "A", replies=[draft()])
    snapshot = passes()
    capsys.readouterr()
    code, client = run("--module", "B", replies=[])
    assert code == 0 and client.calls == [] and client.retrieved == []
    out = capsys.readouterr().out
    assert "--module is ignored" in out and "Review/edit trackers/" in out
    assert passes() == snapshot  # history is never rewritten


def test_repair_margin_on_drafted_chunk(cwd):
    run("--module", "A", replies=[draft()])
    code, client = run("--repair-margin", replies=[repair("Operator margin.")])
    assert code == 0 and len(client.calls) == 1 and status() == "tracker_pending"
    assert passes()[-1][0] == "margin_repair" and passes()[-1][4] == "forced by operator"
    assert chunk_file(cwd).startswith("Operator margin.\n\n")


def test_redraft_with_new_module(cwd):
    run("--module", "A", replies=[draft()])
    code, _ = run("--redraft", "--module", "C", replies=[draft(margin="Second margin.")])
    assert code == 0 and status() == "tracker_pending"
    kind, _, module, _, note, _, _ = passes()[-1]
    assert (kind, module, note) == ("draft", "C", "--redraft requested")
    assert chunk_file(cwd).startswith("Second margin.\n\n")


def test_redraft_reuses_stored_module(cwd):
    run("--module", "D", replies=[draft()])
    code, client = run("--redraft", replies=[draft(margin="Again.")])
    assert code == 0 and passes()[-1][2] == "D"
    assert "This batch is comedy" in client.calls[0]["system"]


def test_failed_redraft_keeps_previous_file(cwd):
    run("--module", "A", replies=[draft()])
    before = chunk_file(cwd)
    code, _ = run("--redraft", replies=["bad", "bad"])
    assert code == 1 and status() == "tracker_pending" and chunk_file(cwd) == before


# --- output and errors -----------------------------------------------------------

def test_out_directory_is_created(cwd):
    code, _ = run("--module", "A", "--out", "deep/nested/out", replies=[draft()])
    assert code == 0 and chunk_file(cwd, "deep/nested/out").startswith(MARGIN)


def test_api_error_is_logged(cwd, capsys):
    code, _ = run("--module", "A", replies=[connection_error()])
    assert code == 1 and status() == "planned"
    assert "Connection error" in (cwd / "logs" / "errors.log").read_text()
    assert "ERROR" in capsys.readouterr().out


def test_unknown_model_fails(cwd, capsys):
    client = FakeClient([], unknown_models={"claude-nope"})
    code = main(["--novel", "book.txt", "--module", "A", "--gen-model", "claude-nope"], client=client)
    assert code == 1 and "Model ID 'claude-nope' not found" in capsys.readouterr().out


def test_usage_row_per_call(cwd):
    _, client = run("--module", "A", replies=[draft(margin="Who am I?"), repair("Plain.")])
    assert len((cwd / "logs" / "usage.csv").read_text().splitlines()) == 1 + len(client.calls)


# --- final-review fixes --------------------------------------------------------

def test_api_error_during_repair_keeps_draft(cwd, capsys):
    code, _ = run("--module", "A", replies=[draft(margin="Who am I?"), connection_error()])
    assert code == 0 and status() == "tracker_pending"
    assert chunk_file(cwd).startswith("Who am I?\n\n")
    assert "margin repair failed" in capsys.readouterr().out
    assert "Connection error" in (cwd / "logs" / "errors.log").read_text()


def test_missing_credentials_is_a_clean_error(cwd, capsys):
    auth = TypeError('"Could not resolve authentication method. Expected either api_key or auth_token"')
    client = FakeClient([])
    client.models.retrieve = lambda _id: (_ for _ in ()).throw(auth)
    code = main(["--novel", "book.txt", "--module", "A"], client=client)
    assert code == 1 and "No Anthropic credentials found" in capsys.readouterr().out
    assert "credentials" in (cwd / "logs" / "errors.log").read_text()


def test_mutating_runs_never_rewrite_earlier_rows(cwd):
    run("--module", "A", replies=[draft()])
    before = passes()
    run("--repair-margin", replies=[repair("New margin.")])
    run("--redraft", replies=[draft(margin="Third.")])
    after = passes()
    assert len(after) == len(before) + 2 and after[:len(before)] == before
