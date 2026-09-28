import pytest

from imagesync.target import at_tag, canonical_tag, format_refs, is_confirmed

BIBLE = {
    "characters": [{"tag": "Kael", "reference_generated": True}],
    "locations": [{"tag": "OldTemple", "reference_generated": True}],
    "objects": [{"tag": "Sword", "reference_generated": False}],
}


def test_at_tag():
    assert at_tag("Kael") == "@Kael"


def test_canonical_tag_resolves_case_insensitively():
    assert canonical_tag(BIBLE, "kael") == "Kael"
    assert canonical_tag(BIBLE, "KAEL") == "Kael"
    assert canonical_tag(BIBLE, "oldtemple") == "OldTemple"


def test_canonical_tag_raises_for_unknown_tag():
    with pytest.raises(ValueError, match="Nobody"):
        canonical_tag(BIBLE, "Nobody")


def test_format_refs_emits_canonical_spelling_not_the_models():
    assert format_refs(BIBLE, ["kael", "OLDTEMPLE"]) == "@Kael @OldTemple"


def test_format_refs_empty_list():
    assert format_refs(BIBLE, []) == ""


def test_is_confirmed_true_false_norm_insensitive():
    assert is_confirmed(BIBLE, "kael") is True
    assert is_confirmed(BIBLE, "KAEL") is True
    assert is_confirmed(BIBLE, "Sword") is False   # reference_generated False
    assert is_confirmed(BIBLE, "Nobody") is False
