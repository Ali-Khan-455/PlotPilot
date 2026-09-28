import pytest

from imagesync.beats import Beat, ParseError, cadence_warnings, fold, validate_fresh_beats, validate_revision


def item(tc, narration="n", detail=None, continues=None):
    d = {"timecode": tc, "narration": narration, "detail": detail or []}
    if continues is not None or True:
        d["continues"] = continues
    return d


def item_no_continues(tc, narration="n"):
    return {"timecode": tc, "narration": narration, "detail": []}


SCENES = [("00-00", "a"), ("04-15", "b")]


def test_good_object_round_trips_split_beats_sharing_scene_index():
    obj = {"beats": [item("04-15a"), item("04-15b")]}
    beats = validate_fresh_beats(obj, chunk_scenes=SCENES, has_continuity=False, previous_last_ref=None)
    assert [(b.scene_index, b.suffix) for b in beats] == [(1, "a"), (1, "b")]


@pytest.mark.parametrize("obj, match", [
    ({"beats": [item("04-15")], "extra": 1}, "beats"),
    ({"beats": [{"timecode": "04-15", "narration": "n"}]}, "beat item"),
    ({"beats": []}, "non-empty"),
])
def test_shape_rejections(obj, match):
    with pytest.raises(ParseError, match=match):
        validate_fresh_beats(obj, chunk_scenes=SCENES, has_continuity=False, previous_last_ref=None)


def test_rejects_timecode_failing_regex():
    with pytest.raises(ParseError, match="invalid timecode"):
        validate_fresh_beats({"beats": [item("4-15")]}, chunk_scenes=SCENES, has_continuity=False,
                             previous_last_ref=None)


def test_rejects_invented_timestamp():
    with pytest.raises(ParseError, match="not a scene"):
        validate_fresh_beats({"beats": [item("09-00")]}, chunk_scenes=SCENES, has_continuity=False,
                             previous_last_ref=None)


def test_rejects_jumping_backward_to_earlier_scene():
    obj = {"beats": [item("04-15"), item("00-00")]}
    with pytest.raises(ParseError, match="not a scene"):
        validate_fresh_beats(obj, chunk_scenes=SCENES, has_continuity=False, previous_last_ref=None)


def test_rejects_suffix_gap_and_start_not_a():
    obj = {"beats": [item("04-15a"), item("04-15c")]}
    with pytest.raises(ParseError, match="doesn't begin at"):
        validate_fresh_beats(obj, chunk_scenes=SCENES, has_continuity=False, previous_last_ref=None)


def test_a_missing_continues_key_is_treated_as_null():
    beats = validate_fresh_beats({"beats": [item_no_continues("04-15")]}, chunk_scenes=SCENES,
                                 has_continuity=False, previous_last_ref=None)
    assert beats[0].continues is None


DUP_SCENES = [("04-15", "first"), ("04-15", "second")]


def test_q9_duplicate_unsplit_beats_resolve_to_two_different_scene_indexes():
    obj = {"beats": [item("04-15"), item("04-15")]}
    beats = validate_fresh_beats(obj, chunk_scenes=DUP_SCENES, has_continuity=False, previous_last_ref=None)
    assert [b.scene_index for b in beats] == [0, 1]


def test_unsplit_first_occurrence_cannot_absorb_split_second_occurrence():
    obj = {"beats": [item("04-15"), item("04-15a"), item("04-15b")]}
    beats = validate_fresh_beats(obj, chunk_scenes=DUP_SCENES, has_continuity=False, previous_last_ref=None)
    assert [b.scene_index for b in beats] == [0, 1, 1]


def test_illegal_restart_with_no_further_occurrence_raises():
    obj = {"beats": [item("04-15a"), item("04-15b"), item("04-15a")]}
    with pytest.raises(ParseError, match="not a scene"):
        validate_fresh_beats(obj, chunk_scenes=[("04-15", "only")], has_continuity=False, previous_last_ref=None)


def test_leading_hash_is_tolerated():
    obj = {"beats": [item("#04-15")]}
    beats = validate_fresh_beats(obj, chunk_scenes=SCENES, has_continuity=False, previous_last_ref=None)
    assert beats[0].timecode == "04-15"


def test_continues_on_non_first_beat_raises():
    obj = {"beats": [item("00-00"), item("04-15", continues="00-00")]}
    with pytest.raises(ParseError, match="non-first"):
        validate_fresh_beats(obj, chunk_scenes=SCENES, has_continuity=True, previous_last_ref="00-00")


def test_continues_set_with_no_continuity_available_raises():
    obj = {"beats": [item("00-00", continues="99-99")]}
    with pytest.raises(ParseError, match="no continuity entry"):
        validate_fresh_beats(obj, chunk_scenes=SCENES, has_continuity=False, previous_last_ref=None)


def test_continues_accepted_and_substituted_not_compared():
    obj = {"beats": [item("00-00", continues="totally different string")]}
    beats = validate_fresh_beats(obj, chunk_scenes=SCENES, has_continuity=True, previous_last_ref="09-30")
    assert beats[0].continues == "09-30"


def test_validate_revision_matches_and_keeps_scene_index():
    target = Beat(scene_index=1, suffix="a", timecode="04-15", narration="old", detail=[], continues=None)
    obj = {"beats": [item("04-15a", narration="new")]}
    revised = validate_revision(obj, target=target, has_continuity=False, previous_last_ref=None,
                                is_first_beat=False)
    assert revised.scene_index == 1 and revised.narration == "new"


def test_validate_revision_rejects_mismatched_timecode():
    target = Beat(scene_index=1, suffix="a", timecode="04-15", narration="old", detail=[], continues=None)
    obj = {"beats": [item("09-00")]}
    with pytest.raises(ParseError, match="changed the beat's timecode"):
        validate_revision(obj, target=target, has_continuity=False, previous_last_ref=None, is_first_beat=False)


def test_validate_revision_rejects_more_than_one_beat():
    target = Beat(scene_index=0, suffix="", timecode="00-00", narration="old", detail=[], continues=None)
    obj = {"beats": [item("00-00"), item("00-00")]}
    with pytest.raises(ParseError, match="exactly one"):
        validate_revision(obj, target=target, has_continuity=False, previous_last_ref=None, is_first_beat=False)


def test_validate_revision_rejects_continues_when_not_first_beat():
    target = Beat(scene_index=1, suffix="", timecode="04-15", narration="old", detail=[], continues=None)
    obj = {"beats": [item("04-15", continues="00-00")]}
    with pytest.raises(ParseError, match="no continuity entry"):
        validate_revision(obj, target=target, has_continuity=True, previous_last_ref="00-00", is_first_beat=False)


def b(idx, suffix="", tc=None):
    return Beat(idx, suffix, tc or f"{idx:02d}-00", "n", [], None)


def test_cadence_no_warning_inside_band():
    beats = [b(0), b(1), b(2), b(3)]
    assert cadence_warnings(beats, [0, 10, 20, 30], 40) == []


def test_cadence_warns_at_four_consecutive_out_of_band_not_three():
    beats = [b(0), b(1), b(2), b(3)]
    # gaps of 1s each: outside [3,20]
    assert cadence_warnings(beats, [0, 1, 2, 3], 4) != []
    beats3 = [b(0), b(1), b(2)]
    assert cadence_warnings(beats3, [0, 1, 2], 3) == []


def test_cadence_same_scene_split_no_false_warning():
    beats = [b(0, "a"), b(0, "b"), b(0, "c")]
    warnings = cadence_warnings(beats, [0], 40)
    assert warnings == []  # 40/3 ~= 13.3s each, within [3,20]


def test_cadence_zero_duration_group_contributes_nothing():
    beats = [b(0), b(1), b(2), b(3), b(4)]
    # scene 0 and 1 share the same second (Q9 duplicate) -> zero duration for group 0
    warnings = cadence_warnings(beats, [0, 0, 10, 11, 12], 13)
    assert warnings == []


def test_fold_replaces_by_identity_and_preserves_order():
    base = [b(0), b(1, "a"), b(1, "b")]
    rev = b(1, "a", tc="04-15")
    result = fold(base, [rev])
    assert result[1] is rev
    assert [x.scene_index for x in result] == [0, 1, 1]


def test_fold_unknown_identity_raises():
    base = [b(0)]
    with pytest.raises(ParseError):
        fold(base, [b(5)])
