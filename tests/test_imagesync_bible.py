import pytest

from imagesync import bible, config
from imagesync.spec import SpecError, load_spec
from plotpilot.parse import ParseError

SPEC = load_spec()


def empty_bible(module="A", sub_style="c", aspect="16:9"):
    return {
        "style_lock": {"sub_style": sub_style, "aspect": aspect, "genre_color_default": module,
                       "anchor_image": "-"},
        "slots": {"characters": [], "objects": []},
        "characters": [], "locations": [], "objects": [],
        "continuity_log": [], "revision_log": [],
    }


def ref(type_, tag, descriptor="a descriptor"):
    return {"type": type_, "tag": tag, "descriptor": descriptor}


def bu(tag, name=None, descriptor="a descriptor", current_state=None):
    item = {"name": name or tag, "tag": tag, "descriptor": descriptor}
    if current_state is not None:
        item["current_state"] = current_state
    return item


def delta(new_references, characters=(), locations=(), objects=()):
    return {"new_references": list(new_references),
            "bible_update": {"characters": list(characters), "locations": list(locations),
                             "objects": list(objects)}}


# ---- validate_stage1 ----

def test_empty_delta_round_trips():
    d = delta([])
    assert bible.validate_stage1(d) == d


def test_character_defaults_current_state_clean():
    d = delta([ref("character", "Kael")], characters=[bu("Kael")])
    out = bible.validate_stage1(d)
    assert out["bible_update"]["characters"][0]["current_state"] == "clean"


def test_location_defaults_current_state_intact():
    d = delta([ref("location", "Village")], locations=[bu("Village")])
    out = bible.validate_stage1(d)
    assert out["bible_update"]["locations"][0]["current_state"] == "intact"


def test_object_defaults_current_state_present():
    d = delta([ref("object", "Sword")], objects=[bu("Sword")])
    out = bible.validate_stage1(d)
    assert out["bible_update"]["objects"][0]["current_state"] == "present"


def test_explicit_current_state_kept():
    d = delta([ref("object", "Sword")], objects=[bu("Sword", current_state="drawn")])
    out = bible.validate_stage1(d)
    assert out["bible_update"]["objects"][0]["current_state"] == "drawn"


def test_wrong_top_level_keys_rejected():
    with pytest.raises(ParseError):
        bible.validate_stage1({"new_references": [], "bible_update": {"characters": [], "locations": [],
                                                                       "objects": []}, "extra": 1})


def test_wrong_bible_update_keys_rejected():
    with pytest.raises(ParseError):
        bible.validate_stage1({"new_references": [], "bible_update": {"characters": []}})


def test_tag_must_match_camelcase_format():
    with pytest.raises(ParseError):
        bible.validate_stage1(delta([ref("character", "kael-elder")], characters=[bu("kael-elder")]))


def test_tag_norm_uniqueness_within_delta():
    with pytest.raises(ParseError):
        bible.validate_stage1(delta([ref("character", "Kael"), ref("character", "kael")],
                                    characters=[bu("Kael"), bu("kael")]))


def test_bijection_missing_bible_update_counterpart_rejected():
    with pytest.raises(ParseError):
        bible.validate_stage1(delta([ref("character", "Kael")]))


def test_bijection_orphan_bible_update_entry_rejected():
    with pytest.raises(ParseError):
        bible.validate_stage1(delta([], characters=[bu("Kael")]))


def test_bijection_wrong_category_rejected():
    with pytest.raises(ParseError):
        bible.validate_stage1(delta([ref("character", "Kael")], locations=[bu("Kael")]))


# ---- check_no_collision ----

def test_check_no_collision_accepts_new_tag():
    b = empty_bible()
    bible.check_no_collision(delta([ref("character", "Kael")], characters=[bu("Kael")]), b)


def test_check_no_collision_rejects_existing_tag_any_category():
    b = empty_bible()
    b["locations"].append({"name": "Kael", "tag": "Kael", "descriptor": "x", "current_state": "intact",
                           "reference_generated": True, "first_appeared_chunk": 1})
    with pytest.raises(ParseError):
        bible.check_no_collision(delta([ref("character", "Kael")], characters=[bu("Kael")]), b)


# ---- merge ----

def _chars(n):
    return bible.validate_stage1(delta([ref("character", f"Char{i}") for i in range(1, n + 1)],
                                       characters=[bu(f"Char{i}") for i in range(1, n + 1)]))


def test_merge_assigns_slots_in_new_references_order_up_to_five_characters():
    b = empty_bible()
    merged = bible.merge(b, _chars(5), chunk_idx=1)
    assert [c["slot"] for c in merged["characters"]] == [1, 2, 3, 4, 5]
    assert merged["slots"]["characters"] == [f"Char{i}" for i in range(1, 6)]


def test_merge_sixth_character_gets_fallback_slot():
    b = empty_bible()
    merged = bible.merge(b, _chars(6), chunk_idx=1)
    assert merged["characters"][5]["slot"] == "fallback"
    assert len(merged["slots"]["characters"]) == 5


def test_merge_object_slots_cap_at_fourteen():
    d = bible.validate_stage1(delta([ref("object", f"Obj{i}") for i in range(1, 16)],
                                    objects=[bu(f"Obj{i}") for i in range(1, 16)]))
    merged = bible.merge(empty_bible(), d, chunk_idx=1)
    assert merged["objects"][13]["slot"] == 14
    assert merged["objects"][14]["slot"] == "fallback"


def test_merge_new_row_stamps_reference_generated_and_first_appeared_chunk():
    merged = bible.merge(empty_bible(), _chars(1), chunk_idx=3)
    row = merged["characters"][0]
    assert row["reference_generated"] is True
    assert row["first_appeared_chunk"] == 3


def test_merge_without_replace_tags_duplicate_tag_raises():
    b = bible.merge(empty_bible(), _chars(1), chunk_idx=1)
    with pytest.raises(ParseError):
        bible.merge(b, _chars(1), chunk_idx=2)


def test_merge_replace_tags_updates_matching_category_row_in_place():
    b = bible.merge(empty_bible(), _chars(1), chunk_idx=1)
    original_slot = b["characters"][0]["slot"]
    d = bible.validate_stage1(delta([ref("character", "Char1", "new descriptor")],
                                    characters=[bu("Char1", descriptor="new descriptor")]))
    merged = bible.merge(b, d, chunk_idx=2, replace_tags=frozenset({"Char1"}))
    row = merged["characters"][0]
    assert row["descriptor"] == "new descriptor"
    assert row["slot"] == original_slot
    assert row["first_appeared_chunk"] == 1
    assert len(merged["characters"]) == 1


def test_merge_replace_tags_wrong_category_raises():
    b = bible.merge(empty_bible(), _chars(1), chunk_idx=1)
    d = bible.validate_stage1(delta([ref("location", "Char1", "new descriptor")],
                                    locations=[bu("Char1", descriptor="new descriptor")]))
    with pytest.raises(ParseError):
        bible.merge(b, d, chunk_idx=2, replace_tags=frozenset({"Char1"}))


def test_merge_two_different_replace_tags_both_replace_independently():
    b = bible.merge(empty_bible(), _chars(2), chunk_idx=1)
    d = bible.validate_stage1(delta([ref("character", "Char1", "d1"), ref("character", "Char2", "d2")],
                                    characters=[bu("Char1", descriptor="d1"), bu("Char2", descriptor="d2")]))
    merged = bible.merge(b, d, chunk_idx=2, replace_tags=frozenset({"Char1", "Char2"}))
    assert merged["characters"][0]["descriptor"] == "d1"
    assert merged["characters"][1]["descriptor"] == "d2"
    assert len(merged["characters"]) == 2


def test_merge_never_reassigns_existing_slots_on_second_call():
    b = bible.merge(empty_bible(), _chars(2), chunk_idx=1)
    slots_before = [c["slot"] for c in b["characters"]]
    d = bible.validate_stage1(delta([ref("character", "Char3")], characters=[bu("Char3")]))
    merged = bible.merge(b, d, chunk_idx=2)
    assert [c["slot"] for c in merged["characters"][:2]] == slots_before


# ---- render ----

def test_render_empty_bible_shows_none_yet_placeholders():
    out = bible.render(empty_bible(), SPEC, title="Book Title")
    assert "=== VISUAL BIBLE — Book Title ===" in out
    assert "STYLE LOCK" in out and "CHARACTERS" in out and "LOCATIONS" in out and "OBJECTS" in out
    assert "CONTINUITY LOG (append-only)" in out and "REVISION LOG (append-only)" in out
    assert out.count("- (none yet)") >= 4  # characters, locations, objects, continuity log, revision log


def test_render_genre_color_resolves_letter_to_label():
    out = bible.render(empty_bible(module="A"), SPEC, title="Book")
    assert "Genre color default: Isekai/power fantasy" in out
    assert "Genre color default: A" not in out


def test_render_sub_style_shows_letter_and_name():
    out = bible.render(empty_bible(sub_style="c"), SPEC, title="Book")
    assert "Sub-style: (c) Fantasy adventure manhwa" in out


def test_render_populated_character_row():
    b = bible.merge(empty_bible(), _chars(1), chunk_idx=2)
    out = bible.render(b, SPEC, title="Book")
    assert "Char1" in out and "#Char1" in out and "slot: 1" in out
    assert "reference generated: yes" in out
    assert "first appeared: chunk 2" in out
    assert "current state: clean" in out


# ---- _render_suffix ----

def test_render_suffix_fills_all_three_brackets():
    b = empty_bible(module="A", sub_style="c", aspect="16:9")
    out = bible._render_suffix(SPEC, b, module="A")
    assert "[" not in out
    assert "Fantasy adventure manhwa" in out
    assert SPEC.colors[config.MODULE_TO_COLOR["A"]] in out
    assert "16:9" in out


def test_render_suffix_uses_current_chunk_module_not_locked_default():
    b = empty_bible(module="A", sub_style="c", aspect="16:9")
    out = bible._render_suffix(SPEC, b, module="B")
    assert SPEC.colors[config.MODULE_TO_COLOR["B"]] in out
    assert SPEC.colors[config.MODULE_TO_COLOR["A"]] not in out


def test_render_suffix_raises_on_leftover_bracket(monkeypatch):
    bad_spec = SPEC.__class__(**{**SPEC.__dict__, "suffix": SPEC.suffix + " [extra bracket]"})
    b = empty_bible()
    with pytest.raises(SpecError):
        bible._render_suffix(bad_spec, b, module="A")
