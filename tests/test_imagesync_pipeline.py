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
