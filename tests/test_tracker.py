import copy

import pytest

from plotpilot.parse import ParseError
from plotpilot.tracker import empty, extract_json, merge, merge_collisions, render, validate_delta, validate_scenes

DELTA = {
    "new_characters": [{"name": "Aria Vale", "standin": "the captain"}],
    "new_terms": [{"term": "Mana Core", "meaning": "power source", "chunk": 7}],
    "new_comparisons": ["like a Rocky montage"],
    "new_texture_motifs": ["held together by hope and bad decisions"],
    "chunk_end_state": "I escaped the village.",
    "nickname_collisions": [],
}


def test_extract_json():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('Here:\n```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(ParseError):
        extract_json("no json here")


def test_validate_delta_accepts_spec_shape():
    assert validate_delta(copy.deepcopy(DELTA)) == DELTA


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("new_terms"),
    lambda d: d.update(extra=[]),
    lambda d: d["new_characters"][0].update(stand_in=d["new_characters"][0].pop("standin")),
    lambda d: d["new_terms"][0].update(chunk=7.5),
    lambda d: d.update(chunk_end_state=""),
    lambda d: d.update(new_comparisons="x"),
])
def test_validate_delta_rejects(mutate):
    d = copy.deepcopy(DELTA)
    mutate(d)
    with pytest.raises(ParseError):
        validate_delta(d)


def test_validate_scenes():
    good = {"scenes": [{"first_sentence": "I woke.", "description": "wake up"}]}
    assert validate_scenes(good) == good
    for bad in ([{"first_sentence": "I woke.", "description": "x"}], {"scenes": []},
                {"scenes": [{"first_sentence": "I woke."}]}):
        with pytest.raises(ParseError):
            validate_scenes(bad)


def test_merge_dedupes_forces_chunk_and_does_not_mutate():
    t = empty()
    t1 = merge(t, DELTA, 1)
    assert t == empty()
    d2 = copy.deepcopy(DELTA)
    d2["new_characters"] = [{"name": "ARIA VALE", "standin": "x"}, {"name": "Bo", "standin": "the rookie"}]
    d2["new_comparisons"] = ["Like  a Rocky montage", "new one"]
    d2["nickname_collisions"] = ["the captain"]
    d2["chunk_end_state"] = "Later."
    t2 = merge(t1, d2, 2)
    assert [c["name"] for c in t2["characters"]] == ["Aria Vale", "Bo"]
    assert t2["terms"] == [{"term": "Mana Core", "meaning": "power source", "chunk": 1}]
    assert t2["comparisons"] == ["like a Rocky montage", "new one"]
    assert t2["last_state"] == "Later."
    assert "nickname_collisions" not in t2


def test_merge_collisions():
    t = merge(empty(), DELTA, 1)
    d = copy.deepcopy(DELTA)
    d["new_characters"] = [{"name": "Bo", "standin": "The Captain"}]
    assert merge_collisions(t, d) == ["'The Captain' (Bo) is already used for Aria Vale"]


def test_render_prompt_and_mirror():
    t = merge(empty(), DELTA, 1)
    t["chunk1"] = {"margin": "I am Kai.", "margin_sentences": 1, "target": "Nobody cared."}
    chunks = [(1, "Ch 1–5"), (2, "Ch 6")]
    prompt = render(t, title="Book", chunks=chunks, progress="Chunk 1 — Ch 1–5")
    for heading in ["## Continuity Tracker — Book", "**Progress:**", "**POV:**", "**Characters",
                    "**Established terms", "**Comparisons", "**Texture motifs", "**Chunk boundaries planned:**",
                    "**Where the last chunk left off", "**Chunk 1 placeholder margin"]:
        assert heading in prompt
    assert "Aria Vale → the captain" in prompt and "Chunk 2: Ch 6" in prompt
    assert '"Nobody cared."' in prompt and "override" not in prompt.lower()
    mirror = render(t, title="Book", chunks=chunks, progress="x", overrides=[(1, "paraphrase ok")])
    assert "Fact-check overrides (operator)" in mirror and "Chunk 1: paraphrase ok" in mirror


# --- deferred minors (Phase 4) --------------------------------------------------

def _delta(chars, chunk=1):
    return {"new_characters": [{"name": n, "standin": s} for n, s in chars],
            "new_terms": [{"term": "Mana", "meaning": "magic", "chunk": chunk}], "new_comparisons": [],
            "new_texture_motifs": [], "chunk_end_state": "x", "nickname_collisions": []}


def test_collisions_within_one_delta():
    out = merge_collisions(empty(), _delta([("Bo", "the rookie"), ("Cy", "The Rookie")]))
    assert out == ["'The Rookie' (Cy) is already used for Bo"]


def test_standin_change_for_existing_character_is_reported():
    t = merge(empty(), _delta([("Aria Vale", "the captain")]), 1)
    out = merge_collisions(t, _delta([("aria vale", "my boss")]))
    assert out == ["Aria Vale already uses 'the captain'; the new stand-in 'my boss' is ignored"]


def test_term_chunk_as_string_is_accepted():
    assert validate_delta(_delta([], chunk="2"))


def test_same_name_twice_in_one_delta_with_different_standins():
    out = merge_collisions(empty(), _delta([("Bo", "the rookie"), ("Bo", "the kid")]))
    assert out == ["Bo appears twice in this delta ('the rookie', 'the kid'); the second is ignored"]


def test_progress_reflects_a_split_chapter():
    from plotpilot.tracker import progress
    rows = [{"idx": 1, "label": "Ch 1–5", "chapter_start": 1, "chapter_end": 5},
            {"idx": 2, "label": "Ch 6 (part 1/2)", "chapter_start": 6, "chapter_end": 6},
            {"idx": 3, "label": "Ch 6 (part 2/2)", "chapter_start": 6, "chapter_end": 6}]
    assert progress(rows, 1) == "Part 1, Chunk 1 — Chapters 1–5 processed so far"
    assert progress(rows, 2) == "Part 1, Chunk 2 — Chapters 1–5 and part 1/2 of Chapter 6 processed so far"
    assert progress(rows, 3) == "Part 1, Chunk 3 — Chapters 1–6 processed so far"
    solo = [{"idx": 1, "label": "Ch 1 (part 1/3)", "chapter_start": 1, "chapter_end": 1}]
    assert progress(solo, 1) == "Part 1, Chunk 1 — part 1/3 of Chapter 1 processed so far"


def test_duplicate_name_message_for_a_known_character():
    t = merge(empty(), _delta([("Bo", "the kid")]), 1)
    out = merge_collisions(t, _delta([("Bo", "the rookie"), ("Bo", "the boy")]))
    assert out == ["Bo already uses 'the kid'; the new stand-in 'the rookie' is ignored",
                   "Bo appears twice in this delta ('the rookie', 'the boy'); the second is ignored"]


def test_progress_uses_labels():
    from plotpilot.tracker import progress
    rows = [{"idx": 1, "label": "Prologue–Ch 4", "chapter_start": 1, "chapter_end": 5},
            {"idx": 2, "label": "Ch 5–Epilogue", "chapter_start": 6, "chapter_end": 7}]
    assert progress(rows, 1) == "Part 1, Chunk 1 — Prologue–Chapter 4 processed so far"
    assert progress(rows, 2) == "Part 1, Chunk 2 — Prologue–Epilogue processed so far"
    rows = [{"idx": 1, "label": "Ch 10–12", "chapter_start": 1, "chapter_end": 3}]
    assert progress(rows, 1) == "Part 1, Chunk 1 — Chapters 10–12 processed so far"
