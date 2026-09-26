import pytest

from imagesync import config
from imagesync.spec import SpecError, load_spec

V3 = config.SPEC_PATH


def test_real_spec_loads():
    spec = load_spec()
    assert set(spec.stages) == {"STAGE 0", "STAGE 1", "STAGE 2"}
    assert spec.suffix.startswith("digital manhwa/webtoon illustration style,")
    assert "[sub-style descriptor]" in spec.suffix and "\n" not in spec.suffix
    assert set(spec.sub_styles) == {"a", "b", "c", "d"}
    assert spec.sub_styles["c"].startswith("Fantasy adventure manhwa — vivid palette")
    assert set(spec.colors) == set(config.MODULE_TO_COLOR.values())
    assert spec.colors["Romance/drama"] == "soft, warm, slightly desaturated pastels"
    assert "Ignore the markdown format described above" in spec.contracts.override
    assert '"timecode": "04-15"' in spec.contracts.stage0 and '"new_references"' in spec.contracts.stage1
    assert '"prompts"' in spec.contracts.stage2
    assert '"continuity_log_entries"' in spec.contracts.bible_update
    assert spec.mode_a_line == "INPUT MODE: PLOTPILOT"
    assert spec.continuity_log_header == "CONTINUITY LOG (append-only)"


def _damaged(tmp_path, old, new):
    text = V3.read_text()
    assert old in text
    p = tmp_path / "v3.md"
    p.write_text(text.replace(old, new, 1))
    return p


@pytest.mark.parametrize("old, new, what", [
    ("```\ndigital manhwa/webtoon", "digital manhwa/webtoon", "suffix"),
    ("- **(d) Comedy slice-of-life**", "- Comedy slice-of-life", "sub-style"),
    ("- Comedy/slice of life: bright", "- Comedy and slice of life: bright", "Comedy/slice of life"),
    ("## TOOL OUTPUT CONTRACTS (appendix)", "## CONTRACTS", "TOOL OUTPUT CONTRACTS"),
    ("### How the model uses these contracts", "### How the model uses contracts", "How the model uses"),
    ("**Stage 1 output:**", "**Stage One output:**", "Stage 1 output"),
    ("```\nINPUT MODE: PLOTPILOT", "INPUT MODE: PLOTPILOT", "Mode A"),
    ("CONTINUITY LOG (append-only)", "CONTINUITY LOG (appends only)", "CONTINUITY LOG"),
])
def test_structure_changes_fail_closed(tmp_path, old, new, what):
    with pytest.raises((SpecError, ValueError), match=what):
        load_spec(_damaged(tmp_path, old, new))
