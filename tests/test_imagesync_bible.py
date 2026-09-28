import pytest

from imagesync import bible
from plotpilot.parse import ParseError


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
