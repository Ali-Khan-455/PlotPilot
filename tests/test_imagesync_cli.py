import json
import sqlite3
from pathlib import Path

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


def lock_and_run_stage0(cwd, scenes=CHUNK1_SCENES, client=None, stage1_reply=None, beats_narration=None):
    """Lock the style and run chunk 1's Stage 0, then its Stage 1 (a non-empty delta by default, so the
    chunk lands at refs_pending rather than auto-merging into a second bible_versions row), returning
    the client used."""
    client = client or FakeClient([beats_reply(scenes, narration=beats_narration),
                                   stage1_reply or one_new_character()])
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


# --- Stage 1 (IS-3) ---------------------------------------------------------------------------------


def refs_txt():
    return (Path("images") / "book" / "chunk-01" / "refs.txt").read_text()


def bible_md():
    return Path("bibles") / "book.md"


def pending_files():
    return sorted(Path("refs").glob("book.chunk-*.delta-*.pending.json"))


def latest_bible():
    with is_conn() as conn:
        return json.loads(conn.execute("SELECT json FROM bible_versions ORDER BY id DESC LIMIT 1")
                          .fetchone()[0])


def test_regenerate_ignored_before_lock(cwd, capsys):
    finish_novel(cwd)
    capsys.readouterr()
    client = FakeClient([])
    assert im("--regenerate", "#Kael: reason", client=client) == 0
    assert client.calls == []
    assert "Note: --regenerate ignored; the style isn't locked yet." in capsys.readouterr().out


def test_approve_refs_ignored_before_lock(cwd, capsys):
    finish_novel(cwd)
    capsys.readouterr()
    client = FakeClient([])
    assert im("--approve-refs", client=client) == 0
    assert client.calls == []
    assert "Note: --approve-refs ignored; the style isn't locked yet." in capsys.readouterr().out


def test_combining_flags_refused_with_no_call(cwd):
    finish_novel(cwd)
    client = FakeClient([])
    assert im("--approve-refs", "--regenerate", "#Kael: reason", client=client) == 1
    assert client.calls == []
    assert im("--approve-refs", "--revise-beat", "00-00", "d", client=client) == 1
    assert client.calls == []


def test_stage1_no_new_references_merges_immediately(cwd, capsys):
    finish_novel(cwd)
    client = FakeClient([beats_reply(CHUNK1_SCENES), refs_reply()])
    assert im("--sub-style", "c", client=client) == 0
    assert chunk_status(1) == "refs_approved"
    chunk_idx, stage, _ = versions()[1]
    assert chunk_idx == 1 and stage == "refs"
    assert "No new references." in capsys.readouterr().out
    assert bible_md().read_text().startswith("=== VISUAL BIBLE — book ===")
    assert pending_files() == []


def test_stage1_new_references_writes_refs_txt_and_pending_file(cwd, capsys):
    finish_novel(cwd)
    lock_and_run_stage0(cwd, stage1_reply=one_new_character("Kael", "a tall elder"))
    assert chunk_status(1) == "refs_pending"
    txt = refs_txt()
    assert "a tall elder" in txt and "#Kael" in txt
    assert "digital manhwa/webtoon illustration style" in txt  # locked suffix appended
    [pending] = pending_files()
    delta = json.loads(pending.read_text())
    assert delta["new_references"][0]["tag"] == "Kael"
    assert not bible_md().exists()
    out = capsys.readouterr().out
    assert "1 new reference(s) pending" in out


def test_stage1_pending_file_edit_survives_a_rerun_with_zero_calls(cwd):
    finish_novel(cwd)
    lock_and_run_stage0(cwd, stage1_reply=one_new_character("Kael", "original descriptor"))
    [pending] = pending_files()
    delta = json.loads(pending.read_text())
    delta["bible_update"]["characters"][0]["descriptor"] = "operator-edited descriptor"
    pending.write_text(json.dumps(delta))
    client2 = FakeClient([])
    assert im(client=client2) == 0
    assert client2.calls == []
    assert json.loads(pending.read_text())["bible_update"]["characters"][0]["descriptor"] == \
        "operator-edited descriptor"


def test_stage1_reproposal_of_existing_tag_rejected(cwd):
    finish_novel(cwd)
    lock_and_run_stage0(cwd, stage1_reply=one_new_character("Kael"))
    assert im("--approve-refs", client=FakeClient([])) == 0
    assert chunk_status(1) == "refs_approved"
    _mark_chunk1_done_with_continuity(cwd, last_entry="- [chunk 1 | beat 00-00] Kael introduced")
    from imagesync.source import load_novel, open_plotpilot
    chunk2_scenes = load_novel(open_plotpilot(pp_config.DB_PATH), "book").chunks[1].scenes
    dup = one_new_character("Kael")
    client = FakeClient([beats_reply(chunk2_scenes), dup, dup])  # rejected, retried once, fails clearly
    assert im(client=client) == 1  # chunk 2's Stage 1 re-proposes an existing tag: rejected


def test_approve_refs_sets_reference_generated_and_advances_status(cwd):
    finish_novel(cwd)
    lock_and_run_stage0(cwd, stage1_reply=one_new_character("Kael"))
    assert im("--approve-refs", client=FakeClient([])) == 0
    assert chunk_status(1) == "refs_approved"
    b = latest_bible()
    assert b["characters"][0]["tag"] == "Kael"
    assert b["characters"][0]["reference_generated"] is True
    assert pending_files() == []
    assert bible_md().exists()


def test_approve_refs_refused_when_not_pending(cwd):
    finish_novel(cwd)
    client = FakeClient([beats_reply(CHUNK1_SCENES), refs_reply()])
    im("--sub-style", "c", client=client)  # merges immediately: refs_approved, not refs_pending
    assert im("--approve-refs", client=FakeClient([])) == 1


def test_approve_refs_missing_pending_file_refused(cwd):
    finish_novel(cwd)
    lock_and_run_stage0(cwd, stage1_reply=one_new_character("Kael"))
    [pending] = pending_files()
    pending.unlink()
    assert im("--approve-refs", client=FakeClient([])) == 1


def test_approve_refs_replayed_accept_refused(cwd):
    finish_novel(cwd)
    lock_and_run_stage0(cwd, stage1_reply=one_new_character("Kael"))
    assert im("--approve-refs", client=FakeClient([])) == 0
    # Force the chunk back to refs_pending and recreate a pending file bound to the now-bound pass, to
    # exercise the IntegrityError-catching replay refusal directly (through the CLI this state is
    # otherwise unreachable, since a real approval always advances past refs_pending).
    from imagesync.pipeline import _pending_path, approve_refs
    from imagesync.source import load_novel, open_plotpilot
    from imagesync.spec import load_spec
    conn = is_db.connect(is_config.DB_PATH)
    novel_id = conn.execute("SELECT id FROM novels").fetchone()[0]
    chunk_row = dict(is_db.chunks(conn, novel_id)[0])
    pass_row = conn.execute("SELECT id, note FROM passes WHERE kind='refs' ORDER BY id DESC LIMIT 1").fetchone()
    is_db.set_chunk_status(conn, chunk_row["id"], "refs_pending")
    chunk_row["status"] = "refs_pending"
    bound = _pending_path("book", 1, pass_row["id"])
    bound.parent.mkdir(parents=True, exist_ok=True)
    bound.write_text(json.dumps(json.loads(pass_row["note"])["delta"]))
    spec = load_spec()
    src = load_novel(open_plotpilot(pp_config.DB_PATH), "book")
    code = approve_refs(conn, novel_id, chunk_row, spec=spec, slug="book", title=src.title)
    assert code == 1
    conn.close()


def test_regenerate_pending_reference_hides_target_and_sends_reason(cwd):
    finish_novel(cwd)
    lock_and_run_stage0(cwd, stage1_reply=one_new_character("Kael", "original descriptor"),
                       beats_narration="Kael showed up.")
    client = FakeClient([one_new_character("Kael", "new descriptor")])
    assert im("--regenerate", "#Kael: eye color was wrong", client=client) == 0
    msg = client.calls[0]["messages"][0]["content"]
    assert "#Kael" not in msg.split("regenerate #Kael")[0]  # tag index omits the target
    assert "regenerate #Kael: eye color was wrong" in msg
    [pending] = pending_files()
    delta = json.loads(pending.read_text())
    assert delta["new_references"][0]["descriptor"] == "new descriptor"


def test_regenerate_matches_by_real_name_not_by_tag_string(cwd):
    """A regenerate response may echo the target under a different tag than the real one, as long as
    its bible_update name matches the target's real (spaced) name — the match must compare against
    the target's actual name, not its tag string."""
    finish_novel(cwd)
    original = refs_reply([{"type": "character", "tag": "OldManChen", "descriptor": "d0"}],
                          characters=[{"name": "Old Man Chen", "tag": "OldManChen", "descriptor": "d0"}])
    lock_and_run_stage0(cwd, stage1_reply=original, beats_narration="Old Man Chen showed up.")
    reworded = refs_reply([{"type": "character", "tag": "Chen", "descriptor": "regenerated"}],
                          characters=[{"name": "Old Man Chen", "tag": "Chen", "descriptor": "regenerated"}])
    client = FakeClient([reworded])
    assert im("--regenerate", "#OldManChen: reason", client=client) == 0
    [pending] = pending_files()
    delta = json.loads(pending.read_text())
    assert delta["new_references"][0]["tag"] == "OldManChen"  # code overwrote it back to the real tag
    assert delta["new_references"][0]["descriptor"] == "regenerated"


def test_regenerate_unresolvable_target_refused_with_no_call(cwd):
    finish_novel(cwd)
    lock_and_run_stage0(cwd, stage1_reply=one_new_character("Kael"))
    client = FakeClient([])
    assert im("--regenerate", "#Ghost: reason", client=client) == 1
    assert client.calls == []


def test_regenerate_empty_reason_refused_with_no_call(cwd):
    finish_novel(cwd)
    lock_and_run_stage0(cwd, stage1_reply=one_new_character("Kael"))
    client = FakeClient([])
    assert im("--regenerate", "#Kael:   ", client=client) == 1
    assert client.calls == []


def test_regenerate_from_ready_or_beats_refused(cwd):
    finish_novel(cwd)
    client = FakeClient([])
    assert im("--sub-style", "c", "--regenerate", "#Kael: reason", client=client) == 1
    assert client.calls == []


def test_regenerate_still_pending_then_approve_keeps_unrelated_entry_and_appends_normally(cwd):
    finish_novel(cwd)
    two = refs_reply(
        [{"type": "character", "tag": "Kael", "descriptor": "d1"}, {"type": "character", "tag": "Mira", "descriptor": "d2"}],
        characters=[{"name": "Kael", "tag": "Kael", "descriptor": "d1"},
                   {"name": "Mira", "tag": "Mira", "descriptor": "d2"}])
    lock_and_run_stage0(cwd, stage1_reply=two, beats_narration="Kael and Mira showed up.")
    client = FakeClient([one_new_character("Kael", "regenerated descriptor")])
    assert im("--regenerate", "#Kael: reason", client=client) == 0
    assert im("--approve-refs", client=FakeClient([])) == 0
    b = latest_bible()
    tags = {c["tag"]: c for c in b["characters"]}
    assert tags["Kael"]["descriptor"] == "regenerated descriptor"
    assert tags["Mira"]["descriptor"] == "d2"
    assert len(b["characters"]) == 2


def test_regenerate_already_approved_then_approve_replaces_only_that_entry(cwd):
    finish_novel(cwd)
    lock_and_run_stage0(cwd, stage1_reply=one_new_character("Kael", "original"), beats_narration="Kael showed up.")
    assert im("--approve-refs", client=FakeClient([])) == 0
    client = FakeClient([one_new_character("Kael", "regenerated")])
    assert im("--regenerate", "#Kael: reason", client=client) == 0
    assert im("--approve-refs", client=FakeClient([])) == 0
    b = latest_bible()
    assert len(b["characters"]) == 1
    assert b["characters"][0]["descriptor"] == "regenerated"
    assert b["characters"][0]["slot"] == 1


def test_regenerate_twice_before_one_approve_replaces_both(cwd):
    finish_novel(cwd)
    two = refs_reply(
        [{"type": "character", "tag": "Kael", "descriptor": "d1"}, {"type": "character", "tag": "Mira", "descriptor": "d2"}],
        characters=[{"name": "Kael", "tag": "Kael", "descriptor": "d1"},
                   {"name": "Mira", "tag": "Mira", "descriptor": "d2"}])
    lock_and_run_stage0(cwd, stage1_reply=two, beats_narration="Kael and Mira showed up.")
    assert im("--approve-refs", client=FakeClient([])) == 0
    assert im("--regenerate", "#Kael: r1", client=FakeClient([one_new_character("Kael", "kael2")])) == 0
    assert im("--regenerate", "#Mira: r2",
             client=FakeClient([one_new_character("Mira", "mira2")])) == 0
    assert im("--approve-refs", client=FakeClient([])) == 0
    b = latest_bible()
    tags = {c["tag"]: c for c in b["characters"]}
    assert tags["Kael"]["descriptor"] == "kael2" and tags["Mira"]["descriptor"] == "mira2"
    assert len(b["characters"]) == 2


def test_regenerate_pending_approve_regenerate_same_tag_again_approve_again(cwd):
    """Round-4 regression: _pending_refs_pass must not resurrect the original, now-superseded pass."""
    finish_novel(cwd)
    lock_and_run_stage0(cwd, stage1_reply=one_new_character("Kael", "d0"), beats_narration="Kael showed up.")
    assert im("--regenerate", "#Kael: r1", client=FakeClient([one_new_character("Kael", "d1")])) == 0
    assert im("--approve-refs", client=FakeClient([])) == 0
    assert im("--regenerate", "#Kael: r2", client=FakeClient([one_new_character("Kael", "d2")])) == 0
    assert im("--approve-refs", client=FakeClient([])) == 0
    b = latest_bible()
    assert len(b["characters"]) == 1
    assert b["characters"][0]["descriptor"] == "d2"


def test_regenerate_first_of_two_pending_keeps_slot_order(cwd):
    """Round-7 regression: regenerating a still-pending target must not reassign slots by moving it
    to the end of new_references."""
    finish_novel(cwd)
    two = refs_reply(
        [{"type": "character", "tag": "Char1", "descriptor": "d1"},
         {"type": "character", "tag": "Char2", "descriptor": "d2"}],
        characters=[{"name": "Char1", "tag": "Char1", "descriptor": "d1"},
                   {"name": "Char2", "tag": "Char2", "descriptor": "d2"}])
    lock_and_run_stage0(cwd, stage1_reply=two, beats_narration="Char1 and Char2 showed up.")
    client = FakeClient([one_new_character("Char1", "regenerated")])
    assert im("--regenerate", "#Char1: reason", client=client) == 0
    assert im("--approve-refs", client=FakeClient([])) == 0
    b = latest_bible()
    tags = {c["tag"]: c["slot"] for c in b["characters"]}
    assert tags["Char1"] == 1
    assert tags["Char2"] == 2


def test_regenerate_rewrites_refs_txt_in_same_invocation(cwd):
    finish_novel(cwd)
    lock_and_run_stage0(cwd, stage1_reply=one_new_character("Kael", "original"), beats_narration="Kael showed up.")
    client = FakeClient([one_new_character("Kael", "regenerated descriptor")])
    assert im("--regenerate", "#Kael: reason", client=client) == 0
    assert "regenerated descriptor" in refs_txt()


def test_chunk2_stage1_lists_chunk1_approved_references(cwd):
    finish_novel(cwd)
    lock_and_run_stage0(cwd, stage1_reply=one_new_character("Kael"))
    assert im("--approve-refs", client=FakeClient([])) == 0
    _mark_chunk1_done_with_continuity(cwd)
    from imagesync.source import load_novel, open_plotpilot
    chunk2_scenes = load_novel(open_plotpilot(pp_config.DB_PATH), "book").chunks[1].scenes
    client = FakeClient([beats_reply(chunk2_scenes), refs_reply()])
    assert im(client=client) == 0
    stage1_msg = client.calls[1]["messages"][0]["content"]
    assert "#Kael" in stage1_msg and "reference generated: yes" in stage1_msg
