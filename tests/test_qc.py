import getpass
import sqlite3

import pytest

import plotpilot.config as config
from plotpilot.cli import main
from tests.fakes import FakeClient, connection_error

BODY_WORDS = " ".join(["word"] * 100)
MARGIN = "I am a farmer's son in a poor village."
TARGET = "Nobody expected much from me."
REST = "Then the guard came and I ran for the hills with the map in my hand."
BODY = f"{TARGET} {REST}"

AUDIT_CLEAN = "5. **Texture gaps**\n- None\n\n6. **TTS hazards**\n- None"
AUDIT_GAPS = "5. **Texture gaps**\n1. Sentences 2-6 read flat.\n\n6. **TTS hazards**\n- None"
PASS = "Step 1: ...\nStep 4: verdict PASS"
FAIL = '- The guard reveals the map: MISSING\n- "I ran for the hills" INVENTED\nFinal verdict: FAIL'
TEXTURED = f"{TARGET} Classic. {REST} Living the dream."


def draft(margin=MARGIN):
    return (f"<<<MARGIN_START>>>\n{margin}\n<<<MARGIN_END>>>\n"
            f"<<<TARGET_SENTENCE_START>>>\n{TARGET}\n<<<TARGET_SENTENCE_END>>>\n"
            f"margin is 1 sentences.\n\n{BODY}")


@pytest.fixture
def cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "book.txt").write_text(
        "\n\n".join(f"Chapter {i}\n\n{BODY_WORDS}" for i in range(1, 4)) + "\n")
    return tmp_path


def run(*args, replies=(), auto_qc="tracker"):
    # Scripted replies are consumed first; only Prompts 10/11 (Phase 4) are auto-answered, so any
    # unexpected Prompt 6/7/8/9 call still fails with "ran out of scripted replies".
    client = FakeClient(replies, auto_qc=auto_qc)
    return main(["--novel", "book.txt", *args], client=client), client


def rows():
    with sqlite3.connect(config.DB_PATH) as conn:
        return conn.execute("SELECT kind, model, verdict, note, output_text FROM passes ORDER BY id").fetchall()


def kinds():
    return [r[0] for r in rows()]


def status():
    with sqlite3.connect(config.DB_PATH) as conn:
        return conn.execute("SELECT status FROM chunks WHERE idx = 1").fetchone()[0]


def chunk_path(cwd):
    return cwd / "scripts" / "book" / "chunk-01.txt"


def user_msg(client, i):
    return client.calls[i]["messages"][0]["content"]


# --- happy path and texture --------------------------------------------------

def test_happy_path(cwd, capsys):
    code, client = run("--module", "A", replies=[draft(), AUDIT_CLEAN, PASS, BODY])
    assert code == 0 and status() == "tracker_pending"
    assert [c["model"] for c in client.calls] == [config.GEN_MODEL] + [config.QC_MODEL] * 5  # + P10, P11
    audit_user, fc_user = user_msg(client, 1), user_msg(client, 2)
    for u in (audit_user, fc_user):
        assert BODY in u and "Chapter 1" in u and MARGIN not in u
    assert config.EMPTY_TRACKER in audit_user
    out = capsys.readouterr().out
    assert ("Chunk complete. Recommended next step: read aloud at 2x for tone and texture drift "
            "before continuing.") in out
    assert chunk_path(cwd).read_text() == f"{MARGIN}\n\n{BODY}\n"
    assert kinds() == ["draft", "audit", "factcheck", "tts", "tracker_delta", "scenes"]
    assert (cwd / "logs" / "book" / "chunk-01-audit.md").read_text() == AUDIT_CLEAN


def test_texture_then_recheck(cwd):
    code, client = run("--module", "A", replies=[draft(), AUDIT_GAPS, PASS, TEXTURED, PASS, TEXTURED])
    assert code == 0 and status() == "tracker_pending"
    assert kinds() == ["draft", "audit", "factcheck", "texture", "factcheck", "tts", "tracker_delta", "scenes"]
    assert client.calls[3]["model"] == config.GEN_MODEL
    assert TEXTURED in chunk_path(cwd).read_text()


def test_texture_failure_keeps_body(cwd, capsys):
    code, _ = run("--module", "A", replies=[draft(), AUDIT_GAPS, PASS, "short", "short", BODY])
    assert code == 0 and status() == "tracker_pending"
    assert "texture repair failed" in capsys.readouterr().out


# --- fact-check gate, edit, override --------------------------------------------

def test_fail_gate_then_rerun_makes_no_calls(cwd, capsys):
    code, _ = run("--module", "A", replies=[draft(), AUDIT_CLEAN, FAIL])
    assert code == 0 and status() == "factcheck_failed"
    out = capsys.readouterr().out
    assert "FACT-CHECK FAIL for chunk 1" in out and "MISSING" in out
    assert "[line 3]" in out  # the quoted INVENTED text located in the chunk file
    code, client = run()
    assert code == 0 and client.calls == [] and "FACT-CHECK FAIL" in capsys.readouterr().out


def test_operator_edit_after_fail(cwd):
    run("--module", "A", replies=[draft(), AUDIT_CLEAN, FAIL])
    edited = f"{MARGIN}\n\n{TARGET} Then the guard came and revealed the map.\n"
    chunk_path(cwd).write_text(edited)
    code, client = run(replies=[PASS, "Nobody expected much from me. Then the guard came and revealed the map."])
    assert code == 0 and status() == "tracker_pending"
    assert kinds()[-5:] == ["operator_edit", "factcheck", "tts", "tracker_delta", "scenes"]
    assert "revealed the map" in user_msg(client, 0)


def test_wrapped_margin_edit_is_canonicalised(cwd):
    run("--module", "A", replies=[draft(), AUDIT_CLEAN, FAIL])
    chunk_path(cwd).write_text("I am a farmer's son\nin a poor village.\n\n" + BODY + " Edited.\n")
    run(replies=[FAIL])
    assert kinds().count("operator_edit") == 1
    assert chunk_path(cwd).read_text().startswith(MARGIN + "\n\n")
    before = rows()
    code, client = run()
    assert code == 0 and client.calls == [] and rows() == before


def test_override(cwd, capsys):
    run("--module", "A", replies=[draft(), AUDIT_CLEAN, FAIL])
    code, _ = run("--accept-factcheck=false positive on paraphrase", replies=[BODY])
    assert code == 0 and status() == "tracker_pending"
    override = [r for r in rows() if r[0] == "factcheck_override"][0]
    assert override[3] == "false positive on paraphrase" and "MISSING" in override[4]
    log = (cwd / "logs" / "factcheck-overrides.log").read_text()
    assert "false positive on paraphrase" in log and "MISSING" in log and getpass.getuser() in log


def test_empty_override_reason_refused_before_planning(cwd, capsys):
    code, _ = run("--accept-factcheck= ")
    assert code == 1 and "non-empty reason" in capsys.readouterr().err
    assert not (cwd / config.DB_PATH).exists()


def test_override_ignored_when_not_failed(cwd, capsys):
    code, _ = run("--module", "A", "--accept-factcheck=why", replies=[draft(), AUDIT_CLEAN, PASS, BODY])
    assert code == 0 and "--accept-factcheck ignored" in capsys.readouterr().out


# --- failures ------------------------------------------------------------------------

def test_audit_malformed_twice(cwd, capsys):
    code, _ = run("--module", "A", replies=[draft(), "junk", "junk"])
    assert code == 1 and status() == "drafted"
    assert "audit output was malformed twice" in capsys.readouterr().err


def test_factcheck_malformed_twice(cwd, capsys):
    code, _ = run("--module", "A", replies=[draft(), AUDIT_CLEAN, "no verdict", "still none"])
    assert code == 1 and status() == "audited"
    assert "fact-check output was malformed twice" in capsys.readouterr().err


def test_truncated_tts_retried_then_fails(cwd):
    half = " ".join(BODY.split()[:5])
    code, _ = run("--module", "A", replies=[draft(), AUDIT_CLEAN, PASS, half, half])
    assert code == 1 and status() == "checked"
    code, _ = run(replies=[BODY])
    assert code == 0 and status() == "tracker_pending"


def test_tts_warning_printed(cwd, capsys):
    code, _ = run("--module", "A", replies=[draft(), AUDIT_CLEAN, PASS, BODY + " It was 1984."])
    assert code == 0 and status() == "tracker_pending"
    assert "WARNING: TTS hazard line 1 (digit)" in capsys.readouterr().out


# --- narration state, stale files, crash windows ------------------------------------

def test_rerun_on_normalized_makes_no_calls(cwd):
    run("--module", "A", replies=[draft(), AUDIT_CLEAN, PASS, BODY])
    code, client = run()
    assert code == 0 and client.calls == []


def test_missing_trailing_newline_is_not_an_edit(cwd):
    run("--module", "A", replies=[draft(), AUDIT_CLEAN, PASS, BODY])
    p = chunk_path(cwd)
    p.write_text(p.read_text().rstrip("\n"))
    run()
    assert "operator_edit" not in kinds()


def test_missing_file_is_rewritten(cwd):
    run("--module", "A", replies=[draft(), AUDIT_CLEAN, PASS, BODY])
    chunk_path(cwd).unlink()
    code, _ = run()
    assert code == 0 and chunk_path(cwd).read_text() == f"{MARGIN}\n\n{BODY}\n"


def test_file_without_blank_line_is_an_error(cwd, capsys):
    run("--module", "A", replies=[draft(), AUDIT_CLEAN, FAIL])
    chunk_path(cwd).write_text("one paragraph only, no margin separation, edited\n")
    code, _ = run()
    assert code == 1 and "start with the margin as its own paragraph" in capsys.readouterr().err


def test_legacy_single_space_file_is_stale(cwd, capsys):
    run("--module", "A", replies=[draft(), AUDIT_CLEAN, PASS, BODY])
    chunk_path(cwd).write_text(f"{MARGIN} {BODY}\n")
    code, client = run()
    assert code == 0 and client.calls == [] and "operator_edit" not in kinds()
    assert "matched an earlier stored version" in capsys.readouterr().out
    assert chunk_path(cwd).read_text() == f"{MARGIN}\n\n{BODY}\n"


def test_crash_after_redraft_stored(cwd):
    run("--module", "A", replies=[draft(), AUDIT_CLEAN, PASS, BODY])
    old_file = chunk_path(cwd).read_text()
    from plotpilot import db  # simulate: redraft pass stored atomically, then crash before the file write
    conn = db.connect(config.DB_PATH)
    novel_id, chunk_id = conn.execute("SELECT novel_id, id FROM chunks WHERE idx = 1").fetchone()
    db.add_pass(conn, novel_id, chunk_id, "draft", config.GEN_MODEL, "in", draft(margin="New margin."),
                module="A", new_status="drafted")
    conn.close()
    assert chunk_path(cwd).read_text() == old_file
    code, _ = run(replies=[AUDIT_CLEAN, PASS, BODY])
    assert code == 0 and "operator_edit" not in kinds()
    assert chunk_path(cwd).read_text().startswith("New margin.\n\n")


def test_crash_after_texture_rechecks(cwd):
    code, _ = run("--module", "A", replies=[draft(), AUDIT_GAPS, PASS, TEXTURED, connection_error()])
    assert status() == "audited"  # texture stored atomically with 'audited'
    code, client = run(replies=[PASS, TEXTURED])
    assert code == 0 and kinds()[-4:] == ["factcheck", "tts", "tracker_delta", "scenes"]


def test_no_ok_draft_means_no_state(cwd):
    from plotpilot import db
    from plotpilot.pipeline import narration_state
    run("--module", "A", replies=["bad", "bad"])
    conn = db.connect(config.DB_PATH)
    chunk_id = conn.execute("SELECT id FROM chunks WHERE idx = 1").fetchone()[0]
    assert narration_state(conn, chunk_id) is None


# --- Phase 2 flags on a QC'd chunk ------------------------------------------------------

def test_redraft_on_normalized_reruns_qc(cwd):
    run("--module", "A", replies=[draft(), AUDIT_CLEAN, PASS, BODY])
    code, _ = run("--redraft", replies=[draft(margin="Second."), AUDIT_CLEAN, PASS, BODY])
    assert code == 0 and status() == "tracker_pending"
    assert kinds()[-6:] == ["draft", "audit", "factcheck", "tts", "tracker_delta", "scenes"]


def test_repair_margin_on_normalized(cwd):
    run("--module", "A", replies=[draft(), AUDIT_CLEAN, PASS, BODY])
    code, client = run("--repair-margin", replies=["<<<MARGIN_START>>>\nPlain.\n<<<MARGIN_END>>>"])
    assert code == 0 and status() == "tracker_pending" and len(client.calls) == 1
    assert chunk_path(cwd).read_text() == f"Plain.\n\n{BODY}\n"


def test_history_prefix_unchanged(cwd):
    run("--module", "A", replies=[draft(), AUDIT_CLEAN, FAIL])
    before = rows()
    run("--accept-factcheck=ok", replies=[BODY])
    chunk_path(cwd).write_text(f"{MARGIN}\n\n{BODY} Edited line.\n")
    run(replies=[PASS, BODY + " Edited line."])
    assert rows()[:len(before)] == before


# --- Phase 3 final-review fixes --------------------------------------------------

def test_gate_after_texture_points_at_checked_text(cwd, capsys):
    fail = '- "Living the dream." INVENTED\nFinal verdict: FAIL'
    code, _ = run("--module", "A", replies=[draft(), AUDIT_GAPS, PASS, TEXTURED, fail])
    assert code == 0 and status() == "factcheck_failed"
    assert "Living the dream." in chunk_path(cwd).read_text()
    assert "[line 3]" in capsys.readouterr().out


def test_tts_dropping_words_is_rejected(cwd):
    long_body = " ".join(f"Sentence {i} happened." for i in range(40))
    dropped = " ".join(f"Sentence {i} happened." for i in range(37))
    d = draft().replace(f"\n\n{BODY}", f"\n\n{TARGET} {long_body}")
    code, _ = run("--module", "A", replies=[d, AUDIT_CLEAN, PASS, f"{TARGET} {dropped}", f"{TARGET} {dropped}"])
    assert code == 1 and status() == "checked"


def test_operator_edit_is_not_retextured(cwd):
    run("--module", "A", replies=[draft(), AUDIT_GAPS, PASS, "short", "short", BODY])  # texture failed
    chunk_path(cwd).write_text(f"{MARGIN}\n\n{BODY} OPERATOR FIX.\n")
    code, client = run(replies=[PASS, f"{BODY} OPERATOR FIX."])
    assert code == 0 and status() == "tracker_pending"
    assert [c["model"] for c in client.calls] == [config.QC_MODEL] * 4  # P7 + P9 + P10 + P11, no P8
    assert "OPERATOR FIX." in chunk_path(cwd).read_text()


# --- deferred minors (Phase 3) --------------------------------------------------

def draft_paras(margin=MARGIN):
    return draft(margin) + "\n\nA second paragraph follows here."


def test_deleted_margin_paragraph_is_refused(cwd, capsys):
    run("--module", "A", replies=[draft_paras(), AUDIT_CLEAN, PASS, BODY + "\n\nA second paragraph follows here."])
    before = rows()
    chunk_path(cwd).write_text(BODY + "\n\nA second paragraph, edited.\n")
    capsys.readouterr()
    code, client = run()
    assert code == 1 and client.calls == [] and rows() == before
    assert "margin paragraph seems to be deleted" in capsys.readouterr().err


def test_blank_line_with_spaces_splits_margin(cwd):
    run("--module", "A", replies=[draft(), AUDIT_CLEAN, PASS, BODY])
    chunk_path(cwd).write_text(f"{MARGIN}\n   \n{BODY} Edited.\n")
    code, _ = run(auto_qc=True)
    assert code == 0 and chunk_path(cwd).read_text() == f"{MARGIN}\n\n{BODY} Edited.\n"
    edits = [r for r in rows() if r[0] == "operator_edit"]
    assert len(edits) == 1


def test_crash_during_edit_rewrite_records_one_edit(cwd, monkeypatch):
    run("--module", "A", replies=[draft(), AUDIT_CLEAN, PASS, BODY])
    chunk_path(cwd).write_text(f"I am  a farmer's son.\n\n{BODY} Edited.\n")  # non-canonical margin
    import plotpilot.pipeline as pipeline
    real = pipeline.ChunkRun.write
    monkeypatch.setattr(pipeline.ChunkRun, "write", lambda self: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(RuntimeError):
        run(auto_qc=True)
    monkeypatch.setattr(pipeline.ChunkRun, "write", real)
    code, _ = run(auto_qc=True)
    assert code == 0 and kinds().count("operator_edit") == 1
    assert chunk_path(cwd).read_text().startswith("I am a farmer's son.\n\n")


def test_tts_warnings_are_shown_again_at_the_gate(cwd, capsys):
    run("--module", "A", replies=[draft(), AUDIT_CLEAN, PASS, BODY + " I saw 3 guards."])
    capsys.readouterr()
    code, client = run()
    assert code == 0 and client.calls == [] and "WARNING: TTS hazard" in capsys.readouterr().out


def test_repair_margin_after_qc_uses_current_first_sentence(cwd):
    new_first = "Nobody expected a single thing from me."
    run("--module", "A", replies=[draft(), AUDIT_CLEAN, PASS, BODY.replace(TARGET, new_first)])
    code, client = run("--repair-margin", replies=["<<<MARGIN_START>>>\nI farm.\n<<<MARGIN_END>>>"])
    assert code == 0 and f'"{new_first}"' in user_msg(client, 0)


def test_override_log_is_one_line_and_survives_getuser(cwd, monkeypatch):
    run("--module", "A", replies=[draft(), AUDIT_CLEAN, FAIL])
    monkeypatch.setattr(getpass, "getuser", lambda: (_ for _ in ()).throw(OSError("no user")))
    code, _ = run("--accept-factcheck=first line\nsecond line", replies=[BODY])
    assert code == 0
    log = (cwd / "logs" / "factcheck-overrides.log").read_text().splitlines()
    assert len(log) == 1 and "first line second line" in log[0] and "| unknown |" in log[0]


def test_override_log_written_before_db_row(cwd, monkeypatch):
    run("--module", "A", replies=[draft(), AUDIT_CLEAN, FAIL])
    import plotpilot.db as db
    real = db.add_pass

    def crash(*a, **k):
        if a[3] == "factcheck_override":
            raise RuntimeError("crash")
        return real(*a, **k)
    monkeypatch.setattr(db, "add_pass", crash)
    with pytest.raises(RuntimeError):
        run("--accept-factcheck=paraphrase")
    assert "paraphrase" in (cwd / "logs" / "factcheck-overrides.log").read_text()
    assert status() == "factcheck_failed"


def test_redraft_recovers_from_a_malformed_file(cwd, capsys):
    run("--module", "A", replies=[draft(), AUDIT_CLEAN, PASS, BODY])
    chunk_path(cwd).write_text(f"{MARGIN} {BODY} but edited without a blank line.\n")
    capsys.readouterr()
    code, _ = run()
    assert code == 1 and "delete the file to restore the stored text" in capsys.readouterr().err
    code, _ = run("--redraft", replies=[draft("Second margin.")], auto_qc=True)
    assert code == 0 and chunk_path(cwd).read_text().startswith("Second margin.\n\n")


def test_edit_at_drafted_stays_drafted(cwd):
    run("--module", "A", replies=[draft(), "garbage", "garbage"])
    assert status() == "drafted"
    chunk_path(cwd).write_text(f"{MARGIN}\n\n{BODY} Edited.\n")
    code, _ = run(replies=["garbage", "garbage"])
    assert code == 1 and status() == "drafted" and kinds().count("operator_edit") == 1


def test_accept_factcheck_after_edit_is_checked_first(cwd, capsys):
    run("--module", "A", replies=[draft(), AUDIT_CLEAN, FAIL])
    chunk_path(cwd).write_text(f"{MARGIN}\n\n{BODY} Fixed.\n")
    capsys.readouterr()
    code, _ = run("--accept-factcheck=because", auto_qc=True)
    assert code == 0 and "your edit to chunk-01.txt will be fact-checked first" in capsys.readouterr().out
    assert "factcheck_override" not in kinds()


@pytest.mark.parametrize("p8", [connection_error(), ("half a rewrite", "max_tokens")])
def test_failed_texture_continues_to_tts(cwd, capsys, p8):
    code, _ = run("--module", "A", replies=[draft(), AUDIT_GAPS, PASS, p8, BODY])
    assert code == 0 and status() == "tracker_pending"
    assert "texture repair failed" in capsys.readouterr().out


# --- review of the deferred-minor fixes ---------------------------------------------

def test_tts_warnings_print_once_per_run(cwd, capsys):
    capsys.readouterr()
    run("--module", "A", replies=[draft(), AUDIT_CLEAN, PASS, BODY + " I saw 3 guards."])
    assert capsys.readouterr().out.count("WARNING: TTS hazard") == 1


def test_redraft_records_a_malformed_file_first(cwd):
    run("--module", "A", replies=[draft(), AUDIT_CLEAN, PASS, BODY])
    chunk_path(cwd).write_text("my malformed but precious text\n")
    run("--redraft", replies=[draft("Second margin.")], auto_qc=True)
    edits = [r for r in rows() if r[0] == "operator_edit"]
    assert len(edits) == 1 and edits[0][2] == "PARSE_FAILED" and edits[0][4] == "my malformed but precious text\n"


# --- remaining deferred minors ------------------------------------------------------

def test_errors_go_to_stderr(cwd, capsys):
    run("--module", "A", replies=[draft(), "garbage", "garbage"])
    out, err = capsys.readouterr()
    assert "audit output was malformed twice" in err and "malformed twice" not in out


def test_audit_log_written_before_the_row(cwd, monkeypatch):
    import plotpilot.db as db
    real = db.add_pass

    def crash(*a, **k):
        if a[3] == "audit" and k.get("verdict") is None:
            raise RuntimeError("crash")
        return real(*a, **k)
    monkeypatch.setattr(db, "add_pass", crash)
    with pytest.raises(RuntimeError):
        run("--module", "A", replies=[draft(), AUDIT_CLEAN])
    assert (cwd / "logs" / "book" / "chunk-01-audit.md").read_text() == AUDIT_CLEAN


def test_deleted_margin_with_edited_first_paragraph_is_refused(cwd, capsys):
    second = "A second paragraph follows here."
    run("--module", "A", replies=[draft_paras(), AUDIT_CLEAN, PASS, BODY + "\n\n" + second])
    chunk_path(cwd).write_text(BODY.replace("guard", "captain") + "\n\n" + second + "\n")
    capsys.readouterr()
    code, client = run()
    assert code == 1 and client.calls == []
    assert "margin paragraph seems to be deleted" in capsys.readouterr().err


def test_repeated_failing_redraft_stores_the_file_once(cwd):
    run("--module", "A", replies=[draft(), AUDIT_CLEAN, PASS, BODY])
    chunk_path(cwd).write_text("malformed text\n")
    run("--redraft", replies=["bad", "bad"])
    run("--redraft", replies=["bad", "bad"])
    assert [r for r in rows() if r[0] == "operator_edit"].__len__() == 1


# --- review of the remaining-minors fix -------------------------------------------------

def test_failed_log_write_keeps_the_audit_row(cwd, capsys):
    (cwd / "logs").mkdir()
    (cwd / "logs" / "book").write_text("a file, not a directory")
    run("--module", "A", replies=[draft(), "garbage", "garbage"])  # stops before any audit is stored
    code, _ = run(replies=[AUDIT_CLEAN, PASS, BODY])
    assert "audit" in kinds() and "could not write" in capsys.readouterr().out


def test_legit_margin_rewrite_similar_to_body_is_accepted(cwd):
    second = "A second paragraph follows here."
    run("--module", "A", replies=[draft_paras(), AUDIT_CLEAN, PASS, BODY + "\n\n" + second])
    new_margin = "Nobody expected much from me when the guard came and I ran for the hills."
    chunk_path(cwd).write_text(f"{new_margin}\n\n{BODY}\n\n{second}\n")
    code, _ = run(auto_qc=True)
    assert code == 0 and kinds().count("operator_edit") == 1
