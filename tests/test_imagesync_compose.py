import pytest

from imagesync.beats import Beat
from imagesync.compose import (build_manifest_rows, check_refs, check_shot_cadence, check_wide_under_9_16,
                               compose_prompt, validate_stage2)
from plotpilot.parse import ParseError

BIBLE = {
    "characters": [{"tag": "Kael", "reference_generated": True}],
    "locations": [],
    "objects": [{"tag": "Sword", "reference_generated": True}, {"tag": "Unconfirmed", "reference_generated": False}],
}


def beat(scene_index=0, suffix="", timecode="04-15"):
    return Beat(scene_index, suffix, timecode, "narration", [], None)


def item(timecode="04-15", scene="s", shot_type="wide", refs=None, genre=None):
    return {"timecode": timecode, "scene": scene, "shot_type": shot_type, "refs_used": refs or [],
           "genre_override": genre}


# --- validate_stage2 -------------------------------------------------------

def test_validate_stage2_happy_path_strips_hashes():
    b = [beat(0, "", "04-15")]
    obj = {"prompts": [item(timecode="#04-15", refs=["#Kael", "@Sword"])]}
    out = validate_stage2(obj, b)
    assert out["prompts"][0]["timecode"] == "04-15"
    assert out["prompts"][0]["refs_used"] == ["Kael", "Sword"]


def test_validate_stage2_wrong_top_level_key():
    with pytest.raises(ParseError, match="prompts"):
        validate_stage2({"beats": []}, [])


def test_validate_stage2_length_mismatch():
    b = [beat(0, "", "04-15"), beat(1, "", "04-30")]
    obj = {"prompts": [item(timecode="04-15")]}
    with pytest.raises(ParseError, match="expected 2"):
        validate_stage2(obj, b)


def test_validate_stage2_timecode_mismatch_raises():
    b = [beat(0, "", "04-15")]
    obj = {"prompts": [item(timecode="04-99")]}
    with pytest.raises(ParseError, match="doesn't match beat"):
        validate_stage2(obj, b)


def test_validate_stage2_non_str_field_raises():
    b = [beat(0, "", "04-15")]
    obj = {"prompts": [item(timecode="04-15")]}
    obj["prompts"][0]["shot_type"] = 5
    with pytest.raises(ParseError, match="shot_type"):
        validate_stage2(obj, b)


def test_validate_stage2_non_str_refs_used_entry_raises():
    b = [beat(0, "", "04-15")]
    obj = {"prompts": [item(timecode="04-15", refs=[5])]}
    with pytest.raises(ParseError, match="refs_used"):
        validate_stage2(obj, b)


def test_validate_stage2_genre_override_enum():
    b = [beat(0, "", "04-15")]
    ok = validate_stage2({"prompts": [item(timecode="04-15", genre="A")]}, b)
    assert ok["prompts"][0]["genre_override"] == "A"
    with pytest.raises(ParseError, match="genre_override"):
        validate_stage2({"prompts": [item(timecode="04-15", genre="Dark action")]}, b)


# --- compose_prompt ---------------------------------------------------------

class FakeSpec:
    suffix = "style, [sub-style descriptor], [genre color treatment], [aspect ratio]"
    sub_styles = {"c": "Fantasy adventure manhwa — vivid"}
    colors = {"Isekai/power fantasy": "saturated"}


BIBLE_WITH_LOCK = {**BIBLE, "style_lock": {"sub_style": "c", "aspect": "16:9", "genre_color_default": "A",
                                          "anchor_image": "-"}}


class FakeChunk:
    module = "A"


def test_compose_prompt_joins_non_empty_parts_no_stray_comma():
    it = item(shot_type="wide", scene="a village square", refs=[])
    p = compose_prompt(FakeSpec(), BIBLE_WITH_LOCK, FakeChunk(), it)
    assert p.startswith("wide, a village square, style,")
    assert ", , " not in p


def test_compose_prompt_emits_canonical_tag_spelling():
    it = item(refs=["kael"])
    p = compose_prompt(FakeSpec(), BIBLE_WITH_LOCK, FakeChunk(), it)
    assert "@Kael" in p


def test_compose_prompt_genre_override_overrides_chunk_module():
    it = item(genre="D")
    # FakeSpec.colors only has module A's label, so a genre override to D that resolves to a
    # different colour key would KeyError if not respected -- add D's mapping to prove the override wins.
    it2 = dict(it)
    FakeSpec.colors["Comedy/slice of life"] = "bright"
    p = compose_prompt(FakeSpec(), BIBLE_WITH_LOCK, FakeChunk(), it2)
    assert "bright" in p


# --- check_refs --------------------------------------------------------------

def test_check_refs_flags_unconfirmed_tag():
    findings = check_refs(BIBLE, [item(refs=["Kael", "Unconfirmed"])])
    assert len(findings) == 1 and "Unconfirmed" in findings[0]


def test_check_refs_all_confirmed_is_empty():
    assert check_refs(BIBLE, [item(refs=["Kael", "Sword"])]) == []


# --- check_shot_cadence ------------------------------------------------------

def test_same_shot_run_of_4_flagged_not_3():
    items3 = [item(timecode=str(i), shot_type="close-up") for i in range(3)]
    assert check_shot_cadence(items3, "16:9") == []
    items4 = [item(timecode=str(i), shot_type="close-up") for i in range(4)]
    findings = check_shot_cadence(items4, "16:9")
    assert len(findings) == 1 and "4 times" in findings[0]


def test_same_shot_run_split_across_previous_tail_flagged_once():
    tail = ["close-up", "close-up", "close-up"]
    items = [item(timecode="0", shot_type="close-up"), item(timecode="1", shot_type="wide")]
    findings = check_shot_cadence(items, "16:9", previous_tail=tail)
    assert len(findings) == 1


def _alt(n, a="close-up", b="medium"):
    """n shots alternating between two non-wide types, so the same-shot-run rule never fires."""
    return [item(timecode=str(i), shot_type=(a if i % 2 == 0 else b)) for i in range(n)]


def test_missing_wide_shot_flagged_under_16_9_not_9_16():
    items = _alt(9)
    assert check_shot_cadence(items, "16:9") != []
    assert check_shot_cadence(items, "9:16") == []


def test_correct_9_16_batch_with_no_wide_shots_is_clean():
    items = _alt(20)
    assert check_shot_cadence(items, "9:16") == []


def test_gap_exceeding_7_produces_exactly_one_finding():
    items = _alt(12)
    findings = check_shot_cadence(items, "16:9")
    assert len(findings) == 1
    assert "8 beats" in findings[0]  # fires at the 8th non-wide beat


def test_gap_crossing_beat_entirely_inside_previous_tail_produces_no_finding():
    # 9 non-wide shots in the tail already exceed the 7-beat limit before this batch starts.
    tail = ["close-up", "medium"] * 4 + ["close-up"]
    items = _alt(3, a="medium", b="close-up")
    assert check_shot_cadence(items, "16:9", previous_tail=tail) == []


def test_gap_seeded_from_trailing_wide_shot_fires_8_positions_after_it():
    tail = ["close-up", "medium", "wide"]   # wide is the tail's last beat
    items = _alt(10)
    findings = check_shot_cadence(items, "16:9", previous_tail=tail)
    assert len(findings) == 1
    assert findings[0].endswith("beat #7.")   # 0-indexed: item index 7 is the 8th beat after the wide shot


# --- check_wide_under_9_16 ---------------------------------------------------

def test_check_wide_under_9_16_flags_only_under_9_16():
    items = [item(shot_type="wide establishing")]
    assert check_wide_under_9_16(items, "9:16") != []
    assert check_wide_under_9_16(items, "16:9") == []
    assert check_wide_under_9_16(items, "1:1") == []


def test_check_wide_under_9_16_never_gates_retry_findings_separate_from_cadence():
    items = [item(shot_type="wide establishing")]
    # check_shot_cadence itself never reports this as a cadence problem (it's a different function)
    assert check_shot_cadence(items, "9:16") == []


# --- build_manifest_rows ------------------------------------------------------

def test_build_manifest_rows_uses_beat_identity_timecode_and_truncates_to_5_words():
    entries = [("04-15", item(timecode="wrong-field-ignored", shot_type="wide",
                              scene="A very long scene description with many words here"))]
    rows = build_manifest_rows(entries)
    assert rows == [("04-15", "wide", "A very long scene description")]


def test_build_manifest_rows_preserves_order():
    entries = [("01-00", item(scene="first")), ("02-00", item(scene="second"))]
    rows = build_manifest_rows(entries)
    assert [r[0] for r in rows] == ["01-00", "02-00"]
