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
    lambda d: d["new_terms"][0].update(chunk="7"),
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
