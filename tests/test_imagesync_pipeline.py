"""Direct, non-CLI unit tests for imagesync.pipeline helpers that don't need a full novel fixture."""

import csv
import json
from pathlib import Path

import pytest

from imagesync import bible as bible_mod
from imagesync import config, db
from imagesync.compose import build_manifest_rows
from imagesync.pipeline import (_batch_progress, _rewrite_manifest, _stage2_continues_seed, _stage2_done,
                                _write_all_batch_files, _write_continuity_pending_if_missing, accept_bible,
                                attempt, current_beats, run_bible_update, run_regenerate, run_stage2)
from imagesync.source import Source, SourceChunk
from imagesync.spec import load_spec
from plotpilot.llm import LLM
from tests.fakes import FakeClient
from tests.helpers import continuity_reply, stage2_reply

SPEC = load_spec()


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "i.db"))
    yield c
    c.close()


@pytest.fixture
def chunk(conn):
    nid = db.create_novel(conn, "book", "Book", "srcsha", "scriptsha", 1)
    cid = conn.execute("SELECT id FROM chunks WHERE novel_id=? AND idx=1", (nid,)).fetchone()[0]
    return nid, cid


def test_post_validate_sets_verdict_only_on_the_pass_it_fires_for(conn, chunk, tmp_path):
    nid, cid = chunk
    client = FakeClient(replies=["bad", "good"])
    llm = LLM(client, str(tmp_path / "logs"))
    calls = []

    def parse(text):
        return text

    def post_validate(parsed):
        calls.append(parsed)
        return "QA_RETRIED" if parsed == "bad" else None

    result = attempt(llm, conn, nid, cid, "stage2", "m", "u", parse, max_tokens=100, slug="book",
                     chunk_idx=1, post_validate=post_validate)

    # attempt() still returns only the parsed value, never the verdict.
    assert result == "bad"
    assert calls == ["bad"]
    row = conn.execute("SELECT output_text, verdict FROM passes WHERE chunk_id=?", (cid,)).fetchone()
    assert (row["output_text"], row["verdict"]) == ("bad", "QA_RETRIED")

    # A second attempt, with a post_validate that returns None, stores a clean pass.
    result2 = attempt(llm, conn, nid, cid, "stage2", "m", "u2", parse, max_tokens=100, slug="book",
                      chunk_idx=1, post_validate=post_validate)
    assert result2 == "good"
    rows = conn.execute("SELECT output_text, verdict FROM passes WHERE chunk_id=? ORDER BY id",
                        (cid,)).fetchall()
    assert [(r["output_text"], r["verdict"]) for r in rows] == [("bad", "QA_RETRIED"), ("good", None)]


def test_post_validate_omitted_behaves_exactly_as_before(conn, chunk, tmp_path):
    nid, cid = chunk
    client = FakeClient(replies=["good"])
    llm = LLM(client, str(tmp_path / "logs"))
    result = attempt(llm, conn, nid, cid, "stage2", "m", "u", lambda t: t, max_tokens=100, slug="book",
                     chunk_idx=1)
    assert result == "good"
    row = conn.execute("SELECT verdict FROM passes WHERE chunk_id=?", (cid,)).fetchone()
    assert row["verdict"] is None


# ---- IS-4: Stage 2, the continuity gate, and the manifest ------------------------------------------
# Built directly against pipeline functions (no PlotPilot fixture, no CLI) so a multi-batch chunk
# doesn't need dozens of real chapters -- beats are inserted straight into the database, the same
# technique test_imagesync_cli.py's own _mark_chunk1_done_with_continuity uses for continuity state.

def _empty_bible(module="A", sub_style="c", aspect="16:9"):
    return {"style_lock": {"sub_style": sub_style, "aspect": aspect, "genre_color_default": module,
                           "anchor_image": "-"},
           "slots": {"characters": [], "objects": []}, "characters": [], "locations": [], "objects": [],
           "continuity_log": [], "revision_log": []}


def _lock_style(conn, novel_id, **kw):
    text = json.dumps(_empty_bible(**kw))
    db.add_bible_version(conn, novel_id, None, "style_lock", text, text,
                         pass_fields={"kind": "style_lock", "model": None, "input_text": "", "output_text": text,
                                     "note": "sub-style c, aspect 16:9"})


def _make_chunk(idx, n_beats, module="A", start=0):
    scenes = [(f"{start + i:02d}-00", f"scene {start + i}") for i in range(n_beats)]
    return SourceChunk(idx, f"Chunk {idx}", "chapters", "narration", scenes, module)


def _insert_beats(conn, novel_id, chunk_id, scenes, status="refs_approved"):
    output = json.dumps({"beats": [{"timecode": tc, "narration": f"narration for {tc}", "detail": [],
                                    "continues": None} for tc, _ in scenes]})
    note = json.dumps({"identity": [{"scene_index": i, "suffix": "", "continues": None}
                                    for i in range(len(scenes))]})
    db.add_pass(conn, novel_id, chunk_id, "beats", None, "", output, note=note, new_status=status)


def _setup(conn, beats_per_chunk, module="A"):
    """A locked-style novel whose chunks (one count per entry in `beats_per_chunk`) already have their
    beats stored (status refs_approved). Returns (novel_id, chunk_rows, chunks, src)."""
    n_chunks = len(beats_per_chunk)
    nid = db.create_novel(conn, "book", "Book", "srcsha", "scriptsha", n_chunks)
    _lock_style(conn, nid, module=module)
    rows = db.chunks(conn, nid)
    chunks, offset = [], 0
    for row, n in zip(rows, beats_per_chunk):
        chunk = _make_chunk(row["idx"], n, module=module, start=offset)
        _insert_beats(conn, nid, row["id"], chunk.scenes)
        chunks.append(chunk)
        offset += n
    src = Source(nid, "Book", "srcsha", "scriptsha", 100, chunks, "hook text")
    return nid, db.chunks(conn, nid), chunks, src


def test_batch_progress_selects_highest_batch_index_not_newest_insert(conn, chunk):
    """R1-2's own direct regression test: insert batch 2's stage2 pass BEFORE batch 1's (simulating what
    a later re-emission produces), and confirm _batch_progress/_stage2_continues_seed report the state a
    highest-batch_index read gives, not a newest-insert read."""
    nid, cid = chunk
    note2 = json.dumps({"start": 30, "count": 30, "batch_index": 2, "tail": ["wide"]})
    db.add_pass(conn, nid, cid, "stage2", "m", "u2", "o2", note=note2)
    note1 = json.dumps({"start": 0, "count": 30, "batch_index": 1, "tail": ["medium"]})
    db.add_pass(conn, nid, cid, "stage2", "m", "u1", "o1", note=note1)
    next_start, next_batch_index, tail = _batch_progress(conn, cid)
    assert (next_start, next_batch_index, tail) == (60, 3, ["wide"])


def test_batch_containing_before_inside_after_and_across_two_batches(conn, chunk):
    nid, cid = chunk
    note1 = json.dumps({"start": 0, "count": 30, "batch_index": 1, "tail": []})
    db.add_pass(conn, nid, cid, "stage2", "m", "u1", "o1", note=note1)
    note2 = json.dumps({"start": 30, "count": 10, "batch_index": 2, "tail": []})
    db.add_pass(conn, nid, cid, "stage2", "m", "u2", "o2", note=note2)
    from imagesync.pipeline import _batch_containing
    assert _batch_containing(conn, cid, 0)["batch_index"] == 1
    assert _batch_containing(conn, cid, 29)["batch_index"] == 1
    assert _batch_containing(conn, cid, 30)["batch_index"] == 2
    assert _batch_containing(conn, cid, 39)["batch_index"] == 2
    assert _batch_containing(conn, cid, 40) is None


def test_batch_containing_none_when_no_batches_stored(conn, chunk):
    nid, cid = chunk
    from imagesync.pipeline import _batch_containing
    assert _batch_containing(conn, cid, 0) is None


def test_run_stage1_gates_from_refs_approved_not_just_beats(cwd):
    """R1-3's own regression test: a post-batch revision calls run_stage1 on a chunk already at
    refs_approved (or bible_pending) -- the new-references gate must still fire; the pre-IS-5 code only
    ever set refs_pending from status == "beats"."""
    conn = db.connect("i.db")
    nid, rows, chunks, src = _setup(conn, [1])
    row, chunk = rows[0], chunks[0]
    from tests.helpers import one_new_character
    client = FakeClient([one_new_character("Kael")])
    llm = LLM(client, "logs")
    from imagesync.pipeline import run_stage1
    assert row["status"] == "refs_approved"
    assert run_stage1(conn, llm, SPEC, src, nid, row, chunk, stage1_model="m", slug="book") == 0
    new_status = db.chunks(conn, nid)[0]["status"]
    assert new_status == "refs_pending"
    conn.close()


def test_run_stage1_post_batch_revision_note_key(cwd):
    """post_batch_revision, when given, folds a "revise_beat" key into the stored refs pass's note;
    every existing call site (which passes nothing) gets a byte-identical note otherwise."""
    conn = db.connect("i.db")
    nid, rows, chunks, src = _setup(conn, [1])
    row, chunk = rows[0], chunks[0]
    from tests.helpers import one_new_character
    client = FakeClient([one_new_character("Kael")])
    llm = LLM(client, "logs")
    from imagesync.pipeline import run_stage1
    assert run_stage1(conn, llm, SPEC, src, nid, row, chunk, stage1_model="m", slug="book",
                      post_batch_revision=(0, "")) == 0
    note = json.loads(is_passes_note(conn, row["id"], "refs"))
    assert note["revise_beat"] == {"scene_index": 0, "suffix": ""}
    conn.close()


def is_passes_note(conn, chunk_id, kind):
    row = conn.execute("SELECT note FROM passes WHERE chunk_id=? AND kind=? ORDER BY id DESC LIMIT 1",
                       (chunk_id, kind)).fetchone()
    return row["note"]


def test_run_revise_batch_re_emits_only_its_own_batch(cwd):
    """run_revise_batch re-emits only the batch its batch_note names -- other stored batches' own files
    stay byte-unchanged -- and _batch_progress still reports the chunk's true (unchanged) completion
    state afterward, proving R1-2's fix and this new caller compose correctly."""
    from imagesync.pipeline import _batch_containing, run_revise_batch
    conn = db.connect("i.db")
    nid, rows, chunks, src = _setup(conn, [40])
    row, chunk = rows[0], chunks[0]
    batch1, batch2 = chunk.scenes[:30], chunk.scenes[30:]
    client = FakeClient([stage2_reply(batch1), stage2_reply(batch2)])
    llm = LLM(client, "logs")
    assert run_stage2(conn, llm, SPEC, src, nid, row, chunk, gen_model="m", slug="book") == 0
    assert run_stage2(conn, llm, SPEC, src, nid, row, chunk, gen_model="m", slug="book") == 0
    out_dir = Path("images") / "book" / "chunk-01"
    batch1_bytes_before = (out_dir / "batch-1.txt").read_bytes()
    batch2_bytes_before = (out_dir / "batch-2.txt").read_bytes()

    batch1_note = _batch_containing(conn, row["id"], 0)
    assert batch1_note["batch_index"] == 1
    revise_client = FakeClient([stage2_reply(batch1, genre_override="B")])
    llm2 = LLM(revise_client, "logs")
    assert run_revise_batch(conn, llm2, SPEC, src, nid, row, chunk, batch1_note, gen_model="m",
                            slug="book") == 0

    assert (out_dir / "batch-1.txt").read_bytes() != batch1_bytes_before
    assert (out_dir / "batch-2.txt").read_bytes() == batch2_bytes_before  # untouched
    start, batch_index, _ = _batch_progress(conn, row["id"])
    assert (start, batch_index) == (40, 3)  # unchanged true completion state
    conn.close()


def test_run_revise_batch_batch1_uses_continues_seed_tail(cwd):
    """A revise-batch call on batch 1 of chunk 2, whose first beat continues the previous chunk, still
    gets the CONTINUES seed tail -- the same derivation run_stage2's own batch 1 uses."""
    from imagesync.pipeline import _batch_containing, run_revise_batch
    conn = db.connect("i.db")
    nid, rows, chunks, src = _setup(conn, [1, 1])
    row1, chunk1 = rows[0], chunks[0]
    row2, chunk2 = rows[1], chunks[1]
    client = FakeClient([stage2_reply(chunk1.scenes)])
    llm = LLM(client, "logs")
    assert run_stage2(conn, llm, SPEC, src, nid, row1, chunk1, gen_model="m", slug="book") == 0
    # give chunk 2's stored beats a continues value pointing back at chunk 1's last beat
    conn.execute("UPDATE passes SET note = ? WHERE chunk_id = ? AND kind = 'beats'",
                (json.dumps({"identity": [{"scene_index": 0, "suffix": "", "continues": "01-00"}]}),
                 row2["id"]))
    client2 = FakeClient([stage2_reply(chunk2.scenes)])
    llm2 = LLM(client2, "logs")
    assert run_stage2(conn, llm2, SPEC, src, nid, row2, chunk2, gen_model="m", slug="book") == 0
    batch_note = _batch_containing(conn, row2["id"], 0)
    revise_client = FakeClient([stage2_reply(chunk2.scenes, genre_override="B")])
    llm3 = LLM(revise_client, "logs")
    assert run_revise_batch(conn, llm3, SPEC, src, nid, row2, chunk2, batch_note, gen_model="m",
                            slug="book") == 0
    msg = revise_client.calls[0]["messages"][0]["content"]
    assert "CONTINUES" in msg
    conn.close()


def test_run_stage2_multi_batch_and_previous_batch_context(cwd):
    conn = db.connect("i.db")
    nid, rows, chunks, src = _setup(conn, [35])
    row, chunk = rows[0], chunks[0]
    batch1, batch2 = chunk.scenes[:config.BATCH_SIZE], chunk.scenes[config.BATCH_SIZE:]
    client = FakeClient([stage2_reply(batch1), stage2_reply(batch2)])
    llm = LLM(client, "logs")

    assert run_stage2(conn, llm, SPEC, src, nid, row, chunk, gen_model="m", slug="book") == 0
    assert len(client.calls) == 1
    start, batch_index, _ = _batch_progress(conn, row["id"])
    assert (start, batch_index) == (30, 2)

    assert run_stage2(conn, llm, SPEC, src, nid, row, chunk, gen_model="m", slug="book") == 0
    assert len(client.calls) == 2
    msg2 = client.calls[1]["messages"][0]["content"]
    assert SPEC.stage2_context_label in msg2
    assert "#27-00" in msg2 and "#28-00" in msg2 and "#29-00" in msg2  # last 3 beats of batch 1
    start2, batch_index2, _ = _batch_progress(conn, row["id"])
    assert (start2, batch_index2) == (35, 3)
    assert _stage2_done(conn, row["id"], current_beats(conn, row, chunk))
    conn.close()


def test_run_stage2_qa_retry_then_good_stores_two_passes(cwd):
    conn = db.connect("i.db")
    nid, rows, chunks, src = _setup(conn, [1])
    row, chunk = rows[0], chunks[0]
    client = FakeClient([stage2_reply(chunk.scenes, refs_used=["Nobody"]), stage2_reply(chunk.scenes)])
    llm = LLM(client, "logs")
    assert run_stage2(conn, llm, SPEC, src, nid, row, chunk, gen_model="m", slug="book") == 0
    assert len(client.calls) == 2
    verdicts = [r["verdict"] for r in conn.execute("SELECT verdict FROM passes WHERE kind='stage2' ORDER BY id")]
    assert verdicts == ["QA_RETRIED", None]
    assert Path("images/book/chunk-01/batch-1.txt").exists()
    conn.close()


def test_run_stage2_check_refs_fails_both_attempts_refused(cwd, capsys):
    conn = db.connect("i.db")
    nid, rows, chunks, src = _setup(conn, [1])
    row, chunk = rows[0], chunks[0]
    bad = stage2_reply(chunk.scenes, refs_used=["Nobody"])
    client = FakeClient([bad, bad])
    llm = LLM(client, "logs")
    code = run_stage2(conn, llm, SPEC, src, nid, row, chunk, gen_model="m", slug="book")
    assert code == 1
    verdicts = [r["verdict"] for r in conn.execute("SELECT verdict FROM passes WHERE kind='stage2' ORDER BY id")]
    assert verdicts == ["QA_RETRIED", "QA_FAILED"]
    assert not Path("images/book/chunk-01/batch-1.txt").exists()
    err = capsys.readouterr().err
    assert "Nobody" in err
    conn.close()


def test_run_stage2_cadence_only_failure_on_retry_accepted_with_warning(cwd, capsys):
    conn = db.connect("i.db")
    nid, rows, chunks, src = _setup(conn, [4])
    row, chunk = rows[0], chunks[0]
    same_shot = stage2_reply(chunk.scenes, shot_type="close-up")   # 4 identical shots: a same-shot-run finding
    client = FakeClient([same_shot, same_shot])
    llm = LLM(client, "logs")
    code = run_stage2(conn, llm, SPEC, src, nid, row, chunk, gen_model="m", slug="book")
    assert code == 0
    assert len(client.calls) == 2
    verdicts = [r["verdict"] for r in conn.execute("SELECT verdict FROM passes WHERE kind='stage2' ORDER BY id")]
    assert verdicts == ["QA_RETRIED", None]   # the retry's own check_refs passes, so it's accepted
    assert "WARNING" in capsys.readouterr().out
    conn.close()


def test_run_stage2_malformed_twice_fails_clearly(cwd, capsys):
    conn = db.connect("i.db")
    nid, rows, chunks, src = _setup(conn, [1])
    row, chunk = rows[0], chunks[0]
    client = FakeClient(["not json", "still not json"])
    llm = LLM(client, "logs")
    assert run_stage2(conn, llm, SPEC, src, nid, row, chunk, gen_model="m", slug="book") == 1
    assert "malformed twice" in capsys.readouterr().err
    conn.close()


def test_run_stage2_crash_then_resume_uses_stored_note_not_config(cwd, monkeypatch):
    conn = db.connect("i.db")
    nid, rows, chunks, src = _setup(conn, [35])
    row, chunk = rows[0], chunks[0]
    batch1, batch2 = chunk.scenes[:30], chunk.scenes[30:]
    # Simulate a crash right after the first batch's pass was stored, by inserting it directly.
    items1 = json.loads(stage2_reply(batch1))["prompts"]
    note = json.dumps({"start": 0, "count": 30, "batch_index": 1, "tail": ["wide", "medium", "close-up"],
                      "items": items1})
    db.add_pass(conn, nid, row["id"], "stage2", "m", "u", stage2_reply(batch1), note=note)
    monkeypatch.setattr(config, "BATCH_SIZE", 5)   # config changes must not reshuffle the stored batch
    client = FakeClient([stage2_reply(batch2)])
    llm = LLM(client, "logs")
    assert run_stage2(conn, llm, SPEC, src, nid, row, chunk, gen_model="m", slug="book") == 0
    assert len(client.calls) == 1   # exactly one further call, for beats 30..34 (not a 5-sized re-slice)
    start, batch_index, _ = _batch_progress(conn, row["id"])
    assert (start, batch_index) == (35, 3)   # batch 2 was just stored; 3 is the next available index
    conn.close()


def test_write_all_batch_files_regenerates_deleted_file(cwd):
    conn = db.connect("i.db")
    nid, rows, chunks, src = _setup(conn, [1])
    row, chunk = rows[0], chunks[0]
    client = FakeClient([stage2_reply(chunk.scenes)])
    llm = LLM(client, "logs")
    assert run_stage2(conn, llm, SPEC, src, nid, row, chunk, gen_model="m", slug="book") == 0
    batch_file = Path("images/book/chunk-01/batch-1.txt")
    assert batch_file.exists()
    batch_file.unlink()
    current = json.loads(db.latest_bible(conn, nid)["json"])
    _write_all_batch_files(conn, SPEC, current, row, chunk, "book")
    assert batch_file.exists()
    conn.close()


def test_write_all_batch_files_normalizes_hash_prefixed_timecode_and_refs(cwd):
    """A model reply with a leading '#'/'@' on timecode/refs_used (tolerated by validate_stage2, which
    strips it from the PARSED object only) must still resolve correctly when the batch file is rebuilt
    from the STORED pass later -- reading the raw, un-normalized output_text back would crash
    (canonical_tag on '@Kael' has no match) or write a literal '##00-00' header."""
    conn = db.connect("i.db")
    nid, rows, chunks, src = _setup(conn, [1])
    row, chunk = rows[0], chunks[0]
    current = json.loads(db.latest_bible(conn, nid)["json"])
    merged = bible_mod.merge(current, {"new_references": [{"type": "character", "tag": "Kael",
                                                            "descriptor": "d"}],
                                       "bible_update": {"characters": [{"name": "Kael", "tag": "Kael",
                                                                        "descriptor": "d",
                                                                        "current_state": "clean"}],
                                                        "locations": [], "objects": []}}, 1)
    db.add_bible_version(conn, nid, 1, "refs", json.dumps(merged), "{}",
                         pass_fields={"kind": "refs", "model": "m", "input_text": "", "output_text": "{}",
                                     "note": None})
    reply = stage2_reply(chunk.scenes, refs_used=["@Kael"])
    reply = reply.replace('"timecode": "00-00"', '"timecode": "#00-00"')
    client = FakeClient([reply])
    llm = LLM(client, "logs")
    assert run_stage2(conn, llm, SPEC, src, nid, row, chunk, gen_model="m", slug="book") == 0

    batch_file = Path("images/book/chunk-01/batch-1.txt")
    assert batch_file.exists()
    text = batch_file.read_text()
    assert "##00-00" not in text and text.startswith("#00-00")
    assert "@Kael" in text

    batch_file.unlink()
    _write_all_batch_files(conn, SPEC, merged, row, chunk, "book")   # rebuild from the STORED pass
    text2 = batch_file.read_text()
    assert "##00-00" not in text2 and text2.startswith("#00-00")
    assert "@Kael" in text2
    conn.close()


def test_stage2_continues_seed_framing_and_tail(cwd):
    conn = db.connect("i.db")
    nid, rows, chunks, src = _setup(conn, [1, 1])
    row1, chunk1 = rows[0], chunks[0]
    row2, chunk2 = rows[1], chunks[1]
    client1 = FakeClient([stage2_reply(chunk1.scenes, shot_type="wide")])
    llm = LLM(client1, "logs")
    assert run_stage2(conn, llm, SPEC, src, nid, row1, chunk1, gen_model="m", slug="book") == 0

    # chunk 2's own first beat must be marked `continues` for the seed to fire.
    conn.execute("UPDATE passes SET note = ? WHERE chunk_id = ? AND kind = 'beats'",
                (json.dumps({"identity": [{"scene_index": 0, "suffix": "", "continues": "00-00"}]}), row2["id"]))
    conn.commit()

    text, tail = _stage2_continues_seed(conn, nid, chunk2)
    assert text == "CONTINUES: previous chunk's last shot was #00-00 (wide)."
    assert tail == ["wide"]

    client2 = FakeClient([stage2_reply(chunk2.scenes)])
    llm2 = LLM(client2, "logs")
    assert run_stage2(conn, llm2, SPEC, src, nid, row2, chunk2, gen_model="m", slug="book") == 0
    msg = client2.calls[0]["messages"][0]["content"]
    assert "CONTINUES: previous chunk's last shot was #00-00 (wide)." in msg
    conn.close()


def test_run_bible_update_refused_before_stage2_done(cwd, capsys):
    conn = db.connect("i.db")
    nid, rows, chunks, src = _setup(conn, [1])
    row, chunk = rows[0], chunks[0]
    llm = LLM(FakeClient([]), "logs")
    assert run_bible_update(conn, llm, SPEC, src, nid, row, chunk, bible_model="m", slug="book") == 1
    conn.close()


def test_run_bible_update_malformed_twice_fails_clearly(cwd, capsys):
    conn = db.connect("i.db")
    nid, rows, chunks, src = _setup(conn, [1])
    row, chunk = rows[0], chunks[0]
    llm1 = LLM(FakeClient([stage2_reply(chunk.scenes)]), "logs")
    assert run_stage2(conn, llm1, SPEC, src, nid, row, chunk, gen_model="m", slug="book") == 0
    row = db.chunks(conn, nid)[0]
    llm2 = LLM(FakeClient(["not json", "still not json"]), "logs")
    assert run_bible_update(conn, llm2, SPEC, src, nid, row, chunk, bible_model="m", slug="book") == 1
    assert "malformed twice" in capsys.readouterr().err
    assert row["status"] == "refs_approved"   # unchanged -- status_for never fired
    conn.close()


def _alternating_stage2_reply(scenes):
    """7 non-wide beats alternating close-up/medium, so the same-shot-run rule never fires -- isolates
    the wide-shot-gap rule as the only thing that could still trigger a QA retry."""
    types = ["close-up", "medium"]
    prompts = [{"timecode": tc, "scene": f"scene at {tc}", "shot_type": types[i % 2], "refs_used": [],
               "genre_override": None} for i, (tc, _) in enumerate(scenes)]
    return json.dumps({"prompts": prompts})


def _setup_chunk2_continues(conn, *, mark_continues):
    """chunk 1 (1 beat, ending on a non-wide shot) plus chunk 2 (7 alternating non-wide beats). Returns
    (nid, row2, chunk2, src) after chunk 1's own Stage 2 batch is stored and, when `mark_continues`,
    chunk 2's first beat is marked CONTINUES so _stage2_continues_seed's tail actually seeds batch 1's
    cadence check."""
    nid, rows, chunks, src = _setup(conn, [1, 7])
    row1, chunk1 = rows[0], chunks[0]
    row2, chunk2 = rows[1], chunks[1]
    llm1 = LLM(FakeClient([stage2_reply(chunk1.scenes, shot_type="close-up")]), "logs")
    assert run_stage2(conn, llm1, SPEC, src, nid, row1, chunk1, gen_model="m", slug="book") == 0
    if mark_continues:
        identity = [{"scene_index": i, "suffix": "", "continues": "00-00" if i == 0 else None}
                   for i in range(7)]
        conn.execute("UPDATE passes SET note = ? WHERE chunk_id = ? AND kind = 'beats'",
                    (json.dumps({"identity": identity}), row2["id"]))
        conn.commit()
    return nid, row2, chunk2, src


def test_continues_seed_actually_changes_a_cadence_finding(cwd):
    """Not just the framing text: the seeded previous_tail must change what check_shot_cadence reports
    for batch 1 of a chunk marked CONTINUES. 7 non-wide beats alone stay under the 7-beat gap limit; the
    one extra beat of context a genuine CONTINUES seed adds is what pushes the same 7 beats over it."""
    conn = db.connect("i.db")
    nid, row2, chunk2, src = _setup_chunk2_continues(conn, mark_continues=False)
    client = FakeClient([_alternating_stage2_reply(chunk2.scenes)])
    llm = LLM(client, "logs")
    assert run_stage2(conn, llm, SPEC, src, nid, row2, chunk2, gen_model="m", slug="book") == 0
    assert len(client.calls) == 1   # no CONTINUES seed: 7 beats alone never cross the gap threshold
    conn.close()


def test_continues_seed_pushes_a_borderline_gap_over_the_threshold(cwd):
    conn = db.connect("i.db")
    nid, row2, chunk2, src = _setup_chunk2_continues(conn, mark_continues=True)
    reply = _alternating_stage2_reply(chunk2.scenes)
    client = FakeClient([reply, reply])   # same shots on the retry too; only the warning differs
    llm = LLM(client, "logs")
    assert run_stage2(conn, llm, SPEC, src, nid, row2, chunk2, gen_model="m", slug="book") == 0
    assert len(client.calls) == 2   # the CONTINUES seed's extra beat of context tips the same 7 beats
                                    # over the gap threshold, triggering the QA retry the unseeded case above doesn't
    conn.close()


def _run_stage2_and_bible_update(conn, nid, row, chunk, src, *, stage2_client=None, cont_entries=()):
    client1 = stage2_client or FakeClient([stage2_reply(chunk.scenes)])
    llm1 = LLM(client1, "logs")
    assert run_stage2(conn, llm1, SPEC, src, nid, row, chunk, gen_model="m", slug="book") == 0
    client2 = FakeClient([continuity_reply(cont_entries)])
    llm2 = LLM(client2, "logs")
    assert run_bible_update(conn, llm2, SPEC, src, nid, row, chunk, bible_model="m", slug="book") == 0
    return client2


def test_accept_bible_merges_sets_current_state_and_reaches_done(cwd):
    conn = db.connect("i.db")
    nid, rows, chunks, src = _setup(conn, [1])
    row, chunk = rows[0], chunks[0]
    current = json.loads(db.latest_bible(conn, nid)["json"])
    merged = bible_mod.merge(current, {"new_references": [{"type": "object", "tag": "Sword",
                                                            "descriptor": "d"}],
                                       "bible_update": {"characters": [], "locations": [],
                                                        "objects": [{"name": "Sword", "tag": "Sword",
                                                                    "descriptor": "d",
                                                                    "current_state": "sheathed"}]}}, 1)
    db.add_bible_version(conn, nid, 1, "refs", json.dumps(merged), "{}",
                         pass_fields={"kind": "refs", "model": "m", "input_text": "", "output_text": "{}",
                                     "note": None})
    entry = {"beat": "00-00", "element": "Sword", "from": "sheathed", "to": "drawn", "reason": "drew it"}
    _run_stage2_and_bible_update(conn, nid, row, chunk, src, cont_entries=[entry])

    row = db.chunks(conn, nid)[0]
    assert row["status"] == "bible_pending"

    assert Path(f"continuity/book.chunk-01.delta-{db.latest_pass(conn, row['id'], 'continuity')['id']}"
               ".pending.json").exists()
    assert accept_bible(conn, nid, row, chunk, spec=SPEC, slug="book", title="Book") == 0
    row = db.chunks(conn, nid)[0]
    assert row["status"] == "done"
    b = json.loads(db.latest_bible(conn, nid)["json"])
    obj = next(o for o in b["objects"] if o["tag"] == "Sword")
    assert obj["current_state"] == "drawn"
    assert obj["reference_generated"] is True and obj["slot"] == 1   # untouched by the continuity merge
    assert any("Sword" in line for line in b["continuity_log"])
    assert Path("bibles/book.md").exists()
    assert Path("logs/bible-accepts.log").exists()
    assert "chunk 1" in Path("logs/bible-accepts.log").read_text()
    conn.close()


def test_accepted_current_state_reaches_a_later_chunks_stage2_message(cwd):
    """Round-2 Required #1's own cross-chunk regression: continuity state a chunk's --accept-bible sets
    must actually reach a LATER chunk's Stage 2 call, not just be stored -- Stage 2 never reads the
    continuity log itself, only each Bible entry's own current_state field."""
    conn = db.connect("i.db")
    nid, rows, chunks, src = _setup(conn, [1, 1])
    row1, chunk1 = rows[0], chunks[0]
    row2, chunk2 = rows[1], chunks[1]
    current = json.loads(db.latest_bible(conn, nid)["json"])
    merged = bible_mod.merge(current, {"new_references": [{"type": "object", "tag": "Sword",
                                                            "descriptor": "d"}],
                                       "bible_update": {"characters": [], "locations": [],
                                                        "objects": [{"name": "Sword", "tag": "Sword",
                                                                    "descriptor": "d",
                                                                    "current_state": "sheathed"}]}}, 1)
    db.add_bible_version(conn, nid, 1, "refs", json.dumps(merged), "{}",
                         pass_fields={"kind": "refs", "model": "m", "input_text": "", "output_text": "{}",
                                     "note": None})
    entry = {"beat": "00-00", "element": "Sword", "from": "sheathed", "to": "drawn", "reason": "drew it"}
    _run_stage2_and_bible_update(conn, nid, row1, chunk1, src, cont_entries=[entry])
    row1 = db.chunks(conn, nid)[0]
    assert accept_bible(conn, nid, row1, chunk1, spec=SPEC, slug="book", title="Book") == 0

    # chunk 2's own beat must NAME the element for _bible_entries_for_beats to include its entry.
    conn.execute("UPDATE passes SET output_text = ? WHERE chunk_id = ? AND kind = 'beats'",
                (json.dumps({"beats": [{"timecode": "00-00", "narration": "She held the Sword tightly.",
                                        "detail": [], "continues": None}]}), row2["id"]))
    conn.commit()

    client2 = FakeClient([stage2_reply(chunk2.scenes)])
    llm2 = LLM(client2, "logs")
    assert run_stage2(conn, llm2, SPEC, src, nid, row2, chunk2, gen_model="m", slug="book") == 0
    msg = client2.calls[0]["messages"][0]["content"]
    assert "current state: drawn" in msg
    conn.close()


def test_accept_bible_refused_when_not_bible_pending(cwd):
    conn = db.connect("i.db")
    nid, rows, chunks, src = _setup(conn, [1])
    row, chunk = rows[0], chunks[0]
    assert accept_bible(conn, nid, row, chunk, spec=SPEC, slug="book", title="Book") == 1
    conn.close()


def test_bible_pending_rerun_makes_zero_calls_and_self_heals(cwd):
    conn = db.connect("i.db")
    nid, rows, chunks, src = _setup(conn, [1])
    row, chunk = rows[0], chunks[0]
    _run_stage2_and_bible_update(conn, nid, row, chunk, src)
    row = db.chunks(conn, nid)[0]
    batch_file = Path("images/book/chunk-01/batch-1.txt")
    batch_file.unlink()
    assert not batch_file.exists()
    current = json.loads(db.latest_bible(conn, nid)["json"])
    _write_all_batch_files(conn, SPEC, current, row, chunk, "book")
    _write_continuity_pending_if_missing(conn, row, "book")
    _rewrite_manifest(conn, nid, src, "book")
    assert batch_file.exists()
    assert Path("images/book/manifest.csv").exists()
    conn.close()


def test_rewrite_manifest_matches_build_manifest_rows(cwd):
    conn = db.connect("i.db")
    nid, rows, chunks, src = _setup(conn, [2])
    row, chunk = rows[0], chunks[0]
    reply = stage2_reply(chunk.scenes)
    llm = LLM(FakeClient([reply]), "logs")
    assert run_stage2(conn, llm, SPEC, src, nid, row, chunk, gen_model="m", slug="book") == 0
    _rewrite_manifest(conn, nid, src, "book")
    with open("images/book/manifest.csv") as f:
        rows_csv = list(csv.reader(f))
    items = json.loads(reply)["prompts"]
    entries = [(b.timecode + b.suffix, item) for b, item in zip(current_beats(conn, row, chunk), items)]
    expected = build_manifest_rows(entries)
    assert rows_csv[0] == ["timecode", "shot_type", "first_5_words"]
    assert [tuple(r) for r in rows_csv[1:]] == expected
    conn.close()


def test_rewrite_manifest_spans_two_chunks_in_chunk_order(cwd):
    conn = db.connect("i.db")
    nid, rows, chunks, src = _setup(conn, [2, 3])
    row1, chunk1 = rows[0], chunks[0]
    row2, chunk2 = rows[1], chunks[1]
    reply1, reply2 = stage2_reply(chunk1.scenes), stage2_reply(chunk2.scenes)
    llm1 = LLM(FakeClient([reply1]), "logs")
    assert run_stage2(conn, llm1, SPEC, src, nid, row1, chunk1, gen_model="m", slug="book") == 0
    llm2 = LLM(FakeClient([reply2]), "logs")
    assert run_stage2(conn, llm2, SPEC, src, nid, row2, chunk2, gen_model="m", slug="book") == 0
    _rewrite_manifest(conn, nid, src, "book")
    with open("images/book/manifest.csv") as f:
        rows_csv = list(csv.reader(f))
    # 1 header + 2 rows for chunk 1 + 3 rows for chunk 2, chunk 1's rows all coming first
    assert len(rows_csv) == 1 + 2 + 3
    assert [r[0] for r in rows_csv[1:3]] == ["00-00", "01-00"]
    assert [r[0] for r in rows_csv[3:6]] == ["02-00", "03-00", "04-00"]
    conn.close()


def test_batch_file_suffix_is_byte_equal_to_spec_suffix_for_every_batch(cwd):
    conn = db.connect("i.db")
    nid, rows, chunks, src = _setup(conn, [2])
    row, chunk = rows[0], chunks[0]
    llm = LLM(FakeClient([stage2_reply(chunk.scenes)]), "logs")
    assert run_stage2(conn, llm, SPEC, src, nid, row, chunk, gen_model="m", slug="book") == 0
    current = json.loads(db.latest_bible(conn, nid)["json"])
    expected_suffix = bible_mod._render_suffix(SPEC, current, chunk.module)
    text = Path("images/book/chunk-01/batch-1.txt").read_text()
    for block in text.strip().split("\n\n"):
        assert block.endswith(expected_suffix)
    conn.close()


def test_run_regenerate_refused_once_stage2_batches_exist(cwd):
    conn = db.connect("i.db")
    nid, rows, chunks, src = _setup(conn, [1])
    row, chunk = rows[0], chunks[0]
    current = json.loads(db.latest_bible(conn, nid)["json"])
    merged = bible_mod.merge(current, {"new_references": [{"type": "character", "tag": "Kael",
                                                            "descriptor": "d"}],
                                       "bible_update": {"characters": [{"name": "Kael", "tag": "Kael",
                                                                        "descriptor": "d",
                                                                        "current_state": "clean"}],
                                                        "locations": [], "objects": []}}, 1)
    db.add_bible_version(conn, nid, 1, "refs", json.dumps(merged), "{}",
                         pass_fields={"kind": "refs", "model": "m", "input_text": "", "output_text": "{}",
                                     "note": None})
    llm = LLM(FakeClient([stage2_reply(chunk.scenes)]), "logs")
    assert run_stage2(conn, llm, SPEC, src, nid, row, chunk, gen_model="m", slug="book") == 0
    row = db.chunks(conn, nid)[0]
    code = run_regenerate(conn, LLM(FakeClient([]), "logs"), SPEC, src, nid, row, chunk, "#Kael: reason",
                          stage1_model="m", slug="book")
    assert code == 1
    conn.close()


# --- check_images (IS-5) ------------------------------------------------------

def _drive_stage2_and_mark_done(conn, nid, row, chunk, src):
    llm = LLM(FakeClient([stage2_reply(chunk.scenes)]), "logs")
    assert run_stage2(conn, llm, SPEC, src, nid, row, chunk, gen_model="m", slug="book") == 0
    db.set_chunk_status(conn, row["id"], "done")
    return db.chunks(conn, nid)[chunk.idx - 1]


def test_check_images_reports_missing_and_extra_files(cwd):
    from imagesync.pipeline import check_images
    conn = db.connect("i.db")
    nid, rows, chunks, src = _setup(conn, [2])
    row, chunk = rows[0], chunks[0]
    row = _drive_stage2_and_mark_done(conn, nid, row, chunk, src)
    out_dir = Path("images") / "book" / "chunk-01"
    for f in out_dir.glob("*.png"):
        f.unlink()
    (out_dir / "beat_00-00.png").write_bytes(b"")  # present, expected -- no finding
    (out_dir / "unexpected.png").write_bytes(b"")  # present, not expected -- extra

    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = check_images(conn, nid, src, slug="book", images_dir=Path("images"))
    assert code == 0
    out = buf.getvalue()
    assert "missing: beat_01-00.png" in out
    assert "extra: unexpected.png" in out
    assert "missing: beat_00-00.png" not in out
    conn.close()


def test_check_images_chunk_filter_narrows_to_one_chunk(cwd):
    from imagesync.pipeline import check_images
    conn = db.connect("i.db")
    nid, rows, chunks, src = _setup(conn, [1, 1])
    row1, chunk1 = rows[0], chunks[0]
    row2, chunk2 = rows[1], chunks[1]
    _drive_stage2_and_mark_done(conn, nid, row1, chunk1, src)
    _drive_stage2_and_mark_done(conn, nid, row2, chunk2, src)
    for f in (Path("images") / "book" / "chunk-01").glob("*.png"):
        f.unlink()
    for f in (Path("images") / "book" / "chunk-02").glob("*.png"):
        f.unlink()

    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = check_images(conn, nid, src, slug="book", images_dir=Path("images"), chunk_filter=1)
    assert code == 0
    out = buf.getvalue()
    assert "Chunk 1:" in out
    assert "Chunk 2:" not in out
    conn.close()


def test_check_images_chunk_filter_naming_non_done_chunk_reports_clearly(cwd):
    from imagesync.pipeline import check_images
    conn = db.connect("i.db")
    nid, rows, chunks, src = _setup(conn, [1])  # status "refs_approved", not "done"
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = check_images(conn, nid, src, slug="book", images_dir=Path("images"), chunk_filter=1)
    assert code == 0
    assert "isn't done yet" in buf.getvalue()

    buf2 = io.StringIO()
    with redirect_stdout(buf2):
        code2 = check_images(conn, nid, src, slug="book", images_dir=Path("images"), chunk_filter=99)
    assert code2 == 0
    assert "No chunk 99" in buf2.getvalue()
    conn.close()


def test_check_images_images_dir_override_is_honored(cwd):
    from imagesync.pipeline import check_images
    conn = db.connect("i.db")
    nid, rows, chunks, src = _setup(conn, [1])
    row, chunk = rows[0], chunks[0]
    _drive_stage2_and_mark_done(conn, nid, row, chunk, src)
    alt_dir = Path("alt-images")
    (alt_dir / "book" / "chunk-01").mkdir(parents=True)
    (alt_dir / "book" / "chunk-01" / "beat_00-00.png").write_bytes(b"")

    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = check_images(conn, nid, src, slug="book", images_dir=alt_dir)
    assert code == 0
    assert "No missing or extra images." in buf.getvalue()
    conn.close()


def test_check_images_default_scope_aggregates_every_done_chunk(cwd):
    from imagesync.pipeline import check_images
    conn = db.connect("i.db")
    nid, rows, chunks, src = _setup(conn, [1, 1])
    row1, chunk1 = rows[0], chunks[0]
    _drive_stage2_and_mark_done(conn, nid, row1, chunk1, src)
    # chunk 2 stays at refs_approved (not done) -- excluded from the default scope
    for f in (Path("images") / "book" / "chunk-01").glob("*.png"):
        f.unlink()

    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = check_images(conn, nid, src, slug="book", images_dir=Path("images"))
    assert code == 0
    out = buf.getvalue()
    assert "Chunk 1:" in out
    assert "Chunk 2:" not in out
    conn.close()
