"""Phase 4: tracker gate, --accept-tracker, chunks 2..N, flag scoping, pending-file binding."""
import json
import sqlite3

import pytest

import plotpilot.config as config
from plotpilot.cli import main
from tests.fakes import FakeClient

WORDS = " ".join(["word"] * 100)
MARGIN = "I am a farmer's son in a poor village."
TARGET = "Nobody expected much from me."
BODY1 = f"{TARGET} Then the guard came. I ran for the hills."
BODY2 = "I kept walking for days. The road was long. I reached the city at dusk."
HOOK = "<<<HOOK_START>>>\nI got reborn as a farmer's son with zero talent.\n<<<HOOK_END>>>"  # Phase 5 P5


def draft1(margin=MARGIN):
    return (f"<<<MARGIN_START>>>\n{margin}\n<<<MARGIN_END>>>\n"
            f"<<<TARGET_SENTENCE_START>>>\n{TARGET}\n<<<TARGET_SENTENCE_END>>>\n"
            f"margin is 1 sentence.\n\n{BODY1}")


def delta(chars=(("Aria Vale", "the captain"),), state="I ran for the hills.", collisions=()):
    return json.dumps({"new_characters": [{"name": n, "standin": s} for n, s in chars],
                       "new_terms": [], "new_comparisons": [], "new_texture_motifs": [],
                       "chunk_end_state": state, "nickname_collisions": list(collisions)})


@pytest.fixture
def cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "book.txt").write_text("\n\n".join(f"Chapter {i}\n\n{WORDS}" for i in range(1, 7)) + "\n")
    return tmp_path


def run(*args, replies=()):
    client = FakeClient(replies, auto_qc=True)
    return main(["--novel", "book.txt", *args], client=client), client


def q(sql, *a):
    with sqlite3.connect(config.DB_PATH) as conn:
        return conn.execute(sql, a).fetchall()


def status(idx):
    return q("SELECT status FROM chunks WHERE idx = ?", idx)[0][0]


def pending(cwd):
    return sorted((cwd / "trackers").glob("book.chunk-*.delta-*.pending.json"))


def _run_with_delta(delta_reply):
    # draft, then auto P6/P7/P9, then scripted P10 — the fake consumes scripted replies first, so
    # interleave: script draft + P6 + P7 + P9 explicitly, then P10.
    from tests.fakes import AUTO_AUDIT, AUTO_PASS
    return run("--module", "A", replies=[draft1(), AUTO_AUDIT, AUTO_PASS, BODY1, delta_reply])


# --- chunk 1 gate and accept ------------------------------------------------------

def test_chunk1_reaches_tracker_gate(cwd, capsys):
    code, client = _run_with_delta(delta())
    assert code == 0 and status(1) == "tracker_pending"
    out = capsys.readouterr().out
    [p] = pending(cwd)
    assert p.name.startswith("book.chunk-01.delta-") and f"Review/edit trackers/{p.name}" in out
    assert "Chunk complete. Recommended next step" in out
    code, client = run()
    assert code == 0 and client.calls == [] and f"Review/edit trackers/{p.name}" in capsys.readouterr().out


def test_accept_then_chunk2_gate_and_draft(cwd, capsys):
    _run_with_delta(delta())
    code, client = run("--accept-tracker", replies=["B"])
    assert code == 0 and status(1) == "done" and status(2) == "planned"
    assert len(q("SELECT id FROM tracker_versions")) == 1 and pending(cwd) == []
    mirror = (cwd / "trackers" / "book.md").read_text()
    assert "Aria Vale → the captain" in mirror and f'"{TARGET}"' in mirror
    assert "Suggested module for chunk 2" in capsys.readouterr().out
    assert "Chapter 6" in client.calls[0]["messages"][0]["content"]

    code, client = run("--module", "B", replies=[BODY2])
    assert code == 0 and status(2) == "tracker_pending"
    draft_call = client.calls[0]
    assert "Aria Vale → the captain" in draft_call["messages"][0]["content"]
    assert "Chapter 6" in draft_call["messages"][0]["content"]
    assert "Chapter 1" not in draft_call["messages"][0]["content"]
    assert "This batch is romance" in draft_call["system"]
    audit_user = client.calls[1]["messages"][0]["content"]
    assert "Aria Vale → the captain" in audit_user and config.EMPTY_TRACKER not in audit_user
    assert (cwd / "scripts" / "book" / "chunk-02.txt").read_text() == BODY2 + "\n"

    code, _ = run("--accept-tracker", replies=[HOOK])
    assert code == 0 and status(2) == "done"
    assert "All 2 chunks done" in capsys.readouterr().out
    assert (cwd / "scripts" / "book" / "script.txt").read_text().endswith(BODY2 + "\n")


def test_edited_pending_json_is_merged(cwd):
    _run_with_delta(delta())
    [p] = pending(cwd)
    d = json.loads(p.read_text())
    d["new_characters"].append({"name": "Bo", "standin": "the rookie"})
    p.write_text(json.dumps(d))
    run("--accept-tracker", replies=["B"])
    merged = json.loads(q("SELECT json FROM tracker_versions")[0][0])
    assert [c["name"] for c in merged["characters"]] == ["Aria Vale", "Bo"]
    assert "Bo" in json.loads(q("SELECT delta FROM tracker_versions")[0][0])["new_characters"][1]["name"]


def test_invalid_pending_json_refused(cwd, capsys):
    _run_with_delta(delta())
    [p] = pending(cwd)
    p.write_text("{broken")
    code, _ = run("--accept-tracker")
    assert code == 1 and status(1) == "tracker_pending" and "invalid" in capsys.readouterr().out


def test_missing_pending_file_rewritten_at_gate(cwd):
    _run_with_delta(delta())
    [p] = pending(cwd)
    p.unlink()
    run()
    assert pending(cwd) == [p]


def test_tracker_malformed_twice(cwd, capsys):
    from tests.fakes import AUTO_AUDIT, AUTO_PASS
    code, _ = run("--module", "A", replies=[draft1(), AUTO_AUDIT, AUTO_PASS, BODY1, "junk", "junk"])
    assert code == 1 and status(1) == "normalized"
    assert "tracker output was malformed twice" in capsys.readouterr().out
    # no bypass (a): --accept-tracker on normalized is ignored; P10/P11 rerun; gate; no merge
    code, _ = run("--accept-tracker", replies=[delta()])
    assert code == 0 and status(1) == "tracker_pending" and q("SELECT id FROM tracker_versions") == []


def test_crash_between_p10_and_p11_runs_only_p11(cwd):
    from tests.fakes import AUTO_AUDIT, AUTO_PASS, connection_error
    code, _ = run("--module", "A", replies=[draft1(), AUTO_AUDIT, AUTO_PASS, BODY1, delta(), connection_error()])
    assert code == 1 and status(1) == "normalized"
    code, client = run()
    assert code == 0 and len(client.calls) == 1 and status(1) == "tracker_pending"


def test_edit_at_tracker_pending_with_accept_is_not_merged(cwd, capsys):
    _run_with_delta(delta())
    [old] = pending(cwd)
    f = cwd / "scripts" / "book" / "chunk-01.txt"
    f.write_text(f.read_text().rstrip() + " An edit.\n")
    capsys.readouterr()
    code, _ = run("--accept-tracker")
    out = capsys.readouterr().out
    assert code == 0 and status(1) == "tracker_pending" and q("SELECT id FROM tracker_versions") == []
    assert "will be re-checked" in out
    [new] = pending(cwd)
    assert new != old and not old.exists()


def test_done_chunk_edit_is_warned_and_ignored(cwd, capsys):
    _run_with_delta(delta())
    run("--accept-tracker", replies=["B"])
    f = cwd / "scripts" / "book" / "chunk-01.txt"
    f.write_text("hand edit\n")
    capsys.readouterr()
    before = q("SELECT * FROM passes")
    run()
    out = capsys.readouterr().out
    assert "was edited after its tracker was merged" in out and "Delete the file" in out
    assert q("SELECT * FROM passes") == before and f.read_text() == "hand edit\n"
    f.unlink()
    run()
    assert f.read_text().startswith(MARGIN) and "was edited" not in capsys.readouterr().out


def test_flags_do_not_carry_to_next_chunk(cwd):
    _run_with_delta(delta())
    code, client = run("--module", "B", "--accept-tracker", replies=["C"])
    assert code == 0 and status(1) == "done" and status(2) == "planned"
    assert [c["max_tokens"] for c in client.calls] == [config.CLASSIFY_MAX_TOKENS]  # classify only, no P4


def test_redraft_with_accept_tracker_refused(cwd, capsys):
    code, _ = run("--redraft", "--accept-tracker")
    assert code == 1 and "can't be combined" in capsys.readouterr().err


def test_stale_pending_file_from_previous_chunk(cwd):
    _run_with_delta(delta())
    [p1] = pending(cwd)
    saved = p1.read_text()
    run("--accept-tracker", replies=["B"])
    p1.write_text(saved)  # simulate a crash before deletion
    run("--module", "B", replies=[BODY2, *_qc_for(BODY2), delta(chars=(), state="Reached the city.")])
    files = pending(cwd)
    assert len(files) == 1 and files[0].name.startswith("book.chunk-02.")
    run("--accept-tracker", replies=[HOOK])
    latest = json.loads(q("SELECT json FROM tracker_versions ORDER BY id DESC")[0][0])
    assert latest["last_state"] == "Reached the city."


def _qc_for(body):
    from tests.fakes import AUTO_AUDIT, AUTO_PASS
    return [AUTO_AUDIT, AUTO_PASS, body]


def test_superseded_note_uses_structure(cwd, capsys):
    _run_with_delta(delta())
    [p] = pending(cwd)
    p.write_text(json.dumps(json.loads(p.read_text()), indent=4))  # reformatted only, same structure
    f = cwd / "scripts" / "book" / "chunk-01.txt"
    f.write_text(f.read_text().rstrip() + " Edit one.\n")
    capsys.readouterr()
    run()
    assert "superseded" not in capsys.readouterr().out
    [p] = pending(cwd)
    d = json.loads(p.read_text())
    d["new_characters"].append({"name": "Bo", "standin": "x"})
    p.write_text(json.dumps(d))
    f.write_text(f.read_text().rstrip() + " Edit two.\n")
    run()
    assert "superseded" in capsys.readouterr().out


def test_collisions_printed(cwd, capsys):
    _run_with_delta(delta(collisions=["the captain"]))
    assert "WARNING: nickname collision: the captain" in capsys.readouterr().out
    run("--accept-tracker", replies=["B"])
    run("--module", "B", replies=[BODY2, *_qc_for(BODY2), delta(chars=(("Bo", "The Captain"),))])
    assert "'The Captain' (Bo) is already used for Aria Vale" in capsys.readouterr().out


def test_override_in_mirror_not_in_prompt(cwd):
    from tests.fakes import AUTO_AUDIT
    fail = "- x: MISSING\nFinal verdict: FAIL"
    run("--module", "A", replies=[draft1(), AUTO_AUDIT, fail])
    run("--accept-factcheck=paraphrase ok", replies=[BODY1, delta()])
    run("--accept-tracker", replies=["B"])
    assert "paraphrase ok" in (cwd / "trackers" / "book.md").read_text()
    _, client = run("--module", "B", replies=[BODY2])
    assert all("paraphrase ok" not in c["messages"][0]["content"] for c in client.calls)


def test_chunk1_target_is_first_sentence_of_final_body(cwd):
    from tests.fakes import AUTO_AUDIT, AUTO_PASS
    final = "Nobody expected much of me at all. Then the guard came. I ran for the hills."
    run("--module", "A", replies=[draft1(), AUTO_AUDIT, AUTO_PASS, final, delta()])
    run("--accept-tracker", replies=["B"])
    chunk1 = json.loads(q("SELECT json FROM tracker_versions")[0][0])["chunk1"]
    assert chunk1 == {"margin": MARGIN, "margin_sentences": 1, "target": "Nobody expected much of me at all."}


def test_accept_tracker_ignored_when_nothing_pending(cwd, capsys):
    run("--accept-tracker", replies=["A"])
    assert "no pending tracker update" in capsys.readouterr().out


def test_repair_margin_frozen_after_chunk1_done(cwd, capsys):
    _run_with_delta(delta())
    run("--accept-tracker", replies=["B"])
    capsys.readouterr()
    code, client = run("--repair-margin")
    assert code == 0 and "chunk 1 is frozen" in capsys.readouterr().out
    assert all(c["max_tokens"] != config.REPAIR_MAX_TOKENS for c in client.calls)


def test_redraft_on_planned_chunk_is_ignored(cwd, capsys):
    code, _ = run("--redraft", replies=["A"])
    assert code == 0 and "--redraft ignored; no drafted chunk to redraft" in capsys.readouterr().out
    assert status(1) == "planned"


def test_module_ignored_at_tracker_pending_and_redraft_with_module(cwd, capsys):
    _run_with_delta(delta())
    capsys.readouterr()
    code, client = run("--module", "C")
    assert code == 0 and client.calls == [] and "--module is ignored" in capsys.readouterr().out
    code, client = run("--module", "C", "--redraft", replies=[draft1(), *_qc_for(BODY1), delta()])
    assert code == 0 and "This batch is dark action" in client.calls[0]["system"]
    assert status(1) == "tracker_pending" and len(pending(cwd)) == 1


def test_repair_margin_at_tracker_pending_does_not_rerun_p10(cwd):
    _run_with_delta(delta())
    code, client = run("--repair-margin", replies=["<<<MARGIN_START>>>\nPlain.\n<<<MARGIN_END>>>"])
    assert code == 0 and len(client.calls) == 1 and status(1) == "tracker_pending"


def test_chunk2_draft_with_delimiters_retried(cwd):
    _run_with_delta(delta())
    run("--accept-tracker", replies=["B"])
    code, _ = run("--module", "B", replies=["<<<MARGIN_START>>> no", "<<<MARGIN_START>>> no"])
    assert code == 1 and status(2) == "planned"


def test_trailing_newline_rule(cwd):
    from tests.fakes import AUTO_AUDIT, AUTO_PASS
    run("--module", "A", replies=[draft1(margin="Line one.\n\nLine two."), AUTO_AUDIT, AUTO_PASS,
                                  BODY1 + "\n\n\n", delta()])
    t = (cwd / "scripts" / "book" / "chunk-01.txt").read_bytes()
    assert t.startswith(b"Line one. Line two.\n\n") and t.endswith(b".\n") and not t.endswith(b"\n\n")
    run("--accept-tracker", replies=["B"])
    run("--module", "B", replies=[BODY2 + "\n\n"])
    t2 = (cwd / "scripts" / "book" / "chunk-02.txt").read_bytes()
    assert t2.endswith(b".\n") and not t2.endswith(b"\n\n")


def test_unique_tracker_version_per_chunk(cwd):
    _run_with_delta(delta())
    run("--accept-tracker", replies=["B"])
    from plotpilot import db
    conn = db.connect(config.DB_PATH)
    novel_id, chunk_id = conn.execute("SELECT novel_id, id FROM chunks WHERE idx = 1").fetchone()
    with pytest.raises(sqlite3.IntegrityError):
        db.add_tracker_version(conn, novel_id, chunk_id, "{}", "{}")


def test_history_is_append_only_across_chunks(cwd):
    _run_with_delta(delta())
    p_before = q("SELECT * FROM passes")
    run("--accept-tracker", replies=["B"])
    run("--module", "B", replies=[BODY2])
    run("--accept-tracker", replies=[HOOK])
    assert q("SELECT * FROM passes")[:len(p_before)] == p_before
    # tracker_versions prefix: covered by test_tracker_versions_prefix_unchanged (t_before is empty here)


def test_redraft_chunk2_uses_prompt4(cwd):
    _run_with_delta(delta())
    run("--accept-tracker", replies=["B"])
    run("--module", "B", replies=[BODY2])
    code, client = run("--redraft", replies=["A fresh second chunk. It goes on and on."])
    assert code == 0 and status(2) == "tracker_pending"
    assert "This is a continuation" in client.calls[0]["messages"][0]["content"]
    assert "This batch is romance" in client.calls[0]["system"]  # stored module reused
    assert (cwd / "scripts" / "book" / "chunk-02.txt").read_text().startswith("A fresh second chunk.")


def test_repair_margin_with_accept_tracker_refused(cwd, capsys):
    _run_with_delta(delta())
    code, client = run("--repair-margin", "--accept-tracker")
    assert code == 1 and "can't be combined" in capsys.readouterr().err
    assert client.calls == [] and status(1) == "tracker_pending"


def test_tracker_versions_prefix_unchanged(cwd):
    _run_with_delta(delta())
    run("--accept-tracker", replies=["B"])
    t_before = q("SELECT * FROM tracker_versions")
    assert len(t_before) == 1
    run("--module", "B", replies=[BODY2])
    run("--accept-tracker", replies=[HOOK])
    assert q("SELECT * FROM tracker_versions")[:1] == t_before and len(q("SELECT * FROM tracker_versions")) == 2


def test_tracker_only_auto_mode_rejects_extra_qc_calls(cwd):
    from tests.fakes import FakeClient
    client = FakeClient([draft1()], auto_qc="tracker")
    with pytest.raises(AssertionError, match="ran out"):
        main(["--novel", "book.txt", "--module", "A"], client=client)  # P6 is not auto-answered
