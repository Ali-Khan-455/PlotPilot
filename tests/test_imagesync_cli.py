import json
import sqlite3

import pytest

import imagesync.config as is_config
import plotpilot.config as pp_config
from imagesync import db as is_db
from imagesync.cli import main
from tests.fakes import FakeClient
from tests.helpers import beats_reply, draft1, finish_novel, one_new_character, refs_reply, run


def im(*args, client=None):
    return main(["--novel", "book.txt", *args], client=client)


CHUNK1_SCENES = [("00-00", "scene")]


def is_conn():
    conn = sqlite3.connect(is_config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def is_passes(kind):
    with is_conn() as conn:
        return conn.execute("SELECT id, chunk_id, verdict, note, output_text FROM passes"
                            " WHERE kind = ? ORDER BY id", (kind,)).fetchall()


def chunk_status(idx):
    with is_conn() as conn:
        return conn.execute("SELECT status FROM chunks WHERE idx = ?", (idx,)).fetchone()[0]


def lock_and_run_stage0(cwd, scenes=CHUNK1_SCENES, client=None, stage1_reply=None):
    """Lock the style and run chunk 1's Stage 0, then its Stage 1 (a non-empty delta by default, so the
    chunk lands at refs_pending rather than auto-merging into a second bible_versions row), returning
    the client used."""
    client = client or FakeClient([beats_reply(scenes), stage1_reply or one_new_character()])
    assert im("--sub-style", "c", client=client) == 0
    return client


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
    client = FakeClient([beats_reply(CHUNK1_SCENES), one_new_character()])
    assert im("--sub-style", "c", client=client) == 0
    [(chunk_idx, stage, js)] = versions()
    assert chunk_idx is None and stage == "style_lock"
    bible = json.loads(js)
    assert bible["style_lock"] == {"sub_style": "c", "aspect": "16:9", "genre_color_default": "A",
                                   "anchor_image": "-"}
    assert bible["slots"] == {"characters": [], "objects": []}
    assert all(bible[k] == [] for k in ("characters", "locations", "objects", "continuity_log", "revision_log"))
    assert "Style locked" in capsys.readouterr().out
    before = counts()
    assert im("--sub-style", "c", client=client) == 0 and counts() == before  # idempotent, no more calls


def test_aspect_and_locked_note(cwd, capsys):
    finish_novel(cwd, modules=("B", "A"))
    client = FakeClient([beats_reply(CHUNK1_SCENES), one_new_character()])
    im("--sub-style", "b", "--aspect", "9:16", client=client)
    assert json.loads(versions()[0][2])["style_lock"]["aspect"] == "9:16"
    assert json.loads(versions()[0][2])["style_lock"]["genre_color_default"] == "B"
    capsys.readouterr()
    assert im("--sub-style", "a", client=client) == 0
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
    im("--sub-style", "c", client=FakeClient([beats_reply(CHUNK1_SCENES), one_new_character()]))
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


def test_suggested_confirm_command_keeps_a_non_default_aspect(cwd, capsys):
    finish_novel(cwd)
    capsys.readouterr()
    im("--aspect", "9:16")
    out = capsys.readouterr().out
    assert "Confirm with --sub-style c --aspect 9:16, or pick another." in out
    # exactly the suggested command
    im("--sub-style", "c", "--aspect", "9:16",
      client=FakeClient([beats_reply(CHUNK1_SCENES), one_new_character()]))
    assert json.loads(versions()[0][2])["style_lock"]["aspect"] == "9:16"


# --- Stage 0 (IS-2) ---------------------------------------------------------------------------------


def test_stage0_happy_path(cwd, capsys):
    finish_novel(cwd)
    capsys.readouterr()
    client = lock_and_run_stage0(cwd)
    assert len(client.calls) == 2  # Stage 0, then Stage 1 in the same invocation
    msg = client.calls[0]["messages"][0]["content"]
    assert "INPUT MODE: PLOTPILOT" in msg
    assert "Chapter 1" in msg  # chapters text
    assert "[00:00] SCENE: scene" in msg  # metadata block
    from tests.helpers import HOOK, BODY1
    assert HOOK in msg and BODY1 in msg  # hook prepended to chunk 1's narration
    assert "CONTINUITY LOG" not in msg  # chunk 1 has no continuity
    [p] = is_passes("beats")
    assert p["verdict"] is None
    assert json.loads(p["note"])["identity"] == [{"scene_index": 0, "suffix": "", "continues": None}]
    assert chunk_status(1) == "refs_pending"
    out = capsys.readouterr().out
    assert "Chunk 1 beats stored (1 beats)." in out
    assert "not built yet (IS-3)" not in out  # no stale IS-2 print


def test_stage1_pending_rerun_makes_zero_calls_and_reprints(cwd, capsys):
    """Once chunk 1 is at refs_pending (Stage 0 and Stage 1 both already ran), a further rerun makes
    zero calls and just reprints the gate summary."""
    finish_novel(cwd)
    lock_and_run_stage0(cwd)
    assert chunk_status(1) == "refs_pending"
    capsys.readouterr()
    client2 = FakeClient([])
    assert im(client=client2) == 0
    assert client2.calls == []
    assert "new reference(s) pending" in capsys.readouterr().out


def test_stage1_rerun_after_stage0_only_continues_into_stage1(cwd, capsys):
    """A rerun after Stage 0 alone (Stage 1 not yet reached, e.g. it failed to parse) continues into
    Stage 1 with one further call; a rerun after THAT makes zero further calls."""
    finish_novel(cwd)
    client = FakeClient([beats_reply(CHUNK1_SCENES), "not json", "not json"])
    assert im("--sub-style", "c", client=client) == 1  # Stage 1 malformed twice; fails clearly
    assert chunk_status(1) == "beats"
    client2 = FakeClient([one_new_character()])
    assert im(client=client2) == 0
    assert len(client2.calls) == 1
    assert chunk_status(1) == "refs_pending"


def test_stage0_malformed_once_then_good(cwd):
    finish_novel(cwd)
    client = FakeClient(["not json", beats_reply(CHUNK1_SCENES), one_new_character()])
    assert im("--sub-style", "c", client=client) == 0
    rows = is_passes("beats")
    assert [r["verdict"] for r in rows] == ["PARSE_FAILED", None]


def test_stage0_malformed_twice_fails(cwd, capsys):
    finish_novel(cwd)
    client = FakeClient(["not json", "still not json"])
    assert im("--sub-style", "c", client=client) == 1
    assert "malformed twice" in capsys.readouterr().err
    assert chunk_status(1) == "ready"


def test_stage0_max_tokens_stop(cwd):
    finish_novel(cwd)
    client = FakeClient([("truncated", "max_tokens")])
    assert im("--sub-style", "c", client=client) == 1
    [p] = is_passes("beats")
    assert p["verdict"] == "STOPPED:max_tokens"


def test_stage0_precheck_refuses_with_no_call(cwd, capsys):
    finish_novel(cwd)
    client = FakeClient(context=10, token_count=1_000_000)
    assert im("--sub-style", "c", client=client) == 1
    assert client.calls == []
    err = capsys.readouterr().err
    assert "chunk 1" in err.lower() and "--gen-model" in err


def _mark_chunk1_done_with_continuity(cwd, last_entry="- [chunk 1 | beat 00-00] Sword sheathed -> drawn (reason)"):
    lock_and_run_stage0(cwd)
    conn = is_conn()
    novel_id = conn.execute("SELECT id FROM novels").fetchone()[0]
    chunk1_id = conn.execute("SELECT id FROM chunks WHERE idx = 1").fetchone()[0]
    locked = is_db.latest_bible(conn, novel_id)
    bible = json.loads(locked["json"])
    bible["continuity_log"] = [last_entry]
    text = json.dumps(bible)
    is_db.add_bible_version(conn, novel_id, 1, "continuity", text, text,
                            pass_fields={"kind": "continuity", "model": "m", "input_text": "", "output_text": text,
                                        "note": None}, chunk_id=chunk1_id)
    conn.execute("UPDATE chunks SET status = 'done' WHERE id = ?", (chunk1_id,))
    conn.commit()
    conn.close()


def test_stage0_continues_accepted_and_resolved_by_code(cwd):
    finish_novel(cwd)
    _mark_chunk1_done_with_continuity(cwd)
    from imagesync.source import load_novel, open_plotpilot
    chunk2_scenes = load_novel(open_plotpilot(pp_config.DB_PATH), "book").chunks[1].scenes
    client = FakeClient([beats_reply(chunk2_scenes, continues="a totally different string the model wrote"),
                        refs_reply()])
    assert im(client=client) == 0
    chunk2_row = is_passes("beats")[-1]
    identity = json.loads(chunk2_row["note"])["identity"]
    assert identity[0]["continues"] == "00-00"  # chunk 1's real last beat, not the model's own string


def test_stage0_continues_rejected_with_no_continuity_available(cwd):
    finish_novel(cwd)
    bad = beats_reply(CHUNK1_SCENES, continues="00-00")  # chunk 1 has no previous chunk to continue from
    client = FakeClient([bad, bad])
    assert im("--sub-style", "c", client=client) == 1


def _duplicate_chunk1_scenes(scene_a="a", scene_b="b"):
    with sqlite3.connect(pp_config.DB_PATH) as conn:
        cid = conn.execute("SELECT id FROM chunks WHERE idx = 1").fetchone()[0]
        conn.execute("INSERT INTO passes (novel_id, chunk_id, kind, model, input_text, output_text, created_at)"
                     " VALUES (1, ?, 'scenes', 'm', '', ?, 'now')",
                     (cid, json.dumps({"scenes": [{"first_sentence": "Not in the text.", "description": scene_a},
                                                  {"first_sentence": "Also missing.", "description": scene_b}]})))


def test_revise_beat_resolves_duplicate_occurrence_and_anchors_the_model(cwd):
    finish_novel(cwd)
    _duplicate_chunk1_scenes()
    dup_scenes = [("00-00", "a"), ("00-00", "b")]
    lock_and_run_stage0(cwd, scenes=dup_scenes)
    revise_client = FakeClient([beats_reply([("00-00", "b")], continues=None)])
    # revise the second occurrence specifically
    assert im("--revise-beat", "00-00_2", "new description", client=revise_client) == 0
    msg = revise_client.calls[0]["messages"][0]["content"]
    anchor = json.loads(msg.split("---")[2].strip())
    assert anchor["timecode"] == "00-00" and anchor["narration"] == "narration for 00-00"
    rows = is_passes("beat_revision")
    assert len(rows) == 1
    note = json.loads(rows[0]["note"])
    assert note["scene_index"] == 1 and note["suffix"] == ""


def test_revise_beat_unknown_timecode_refused_with_no_call(cwd):
    finish_novel(cwd)
    lock_and_run_stage0(cwd)
    client = FakeClient([])
    assert im("--revise-beat", "09-00", "new description", client=client) == 1
    assert client.calls == []


def test_revise_beat_before_any_beats_pass_refused(cwd):
    finish_novel(cwd)
    client = FakeClient([])
    # locks the style and requests a revision in the same run, before Stage 0 ever produced beats
    assert im("--sub-style", "c", "--revise-beat", "00-00", "new description", client=client) == 1
    assert client.calls == []


def test_revise_beat_empty_description_refused(cwd):
    finish_novel(cwd)
    lock_and_run_stage0(cwd)
    client = FakeClient([])
    assert im("--revise-beat", "00-00", "  ", client=client) == 1
    assert client.calls == []


def test_revise_beat_ignored_before_lock(cwd, capsys):
    finish_novel(cwd)
    capsys.readouterr()
    client = FakeClient([])
    assert im("--revise-beat", "00-00", "new description", client=client) == 0
    assert client.calls == []
    assert "Note: --revise-beat ignored; the style isn't locked yet." in capsys.readouterr().out
