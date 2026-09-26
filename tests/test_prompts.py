import pytest

from plotpilot.prompts import fill, load_prompts

P = load_prompts()

REPAIR_TARGET = ("[paste the target sentence — the one immediately following the margin, "
                 "already logged in the Continuity Tracker]")
REPAIR_MARGIN = "[paste the flawed margin here]"


def test_all_prompts_load():
    keys = ["1", "3", "3-REPAIR"] + [str(n) for n in range(4, 13)] + [f"MODULE {x}" for x in "ABCD"]
    for k in keys:
        assert P[k].text, k
        assert "COPY EVERYTHING" not in P[k].text and "**END OF" not in P[k].text
    assert "2" not in P


def test_module_heading_and_text():
    assert P["MODULE A"].heading == "MODULE A — Isekai, System, Power Fantasy"
    assert P["MODULE A"].text.startswith("This batch is isekai")


def test_fill_replaces_and_rejects_unknown():
    t = "a [paste x] b"
    assert fill(t, {"[paste x]": "Y"}) == "a Y b"
    with pytest.raises(KeyError):
        fill(t, {"[paste nope]": "Y"})


def test_pipeline_placeholders_exist():
    fill(P["3-REPAIR"].text, {REPAIR_TARGET: "t", REPAIR_MARGIN: "m"})
    fill(P["12"].text, {"[Paste the four modules]": "m", "[Paste the opening of this chunk]": "o"})


def test_phase4_placeholders_exist():
    fill(P["4"].text, {"[Paste your filled-in Continuity Tracker here]": "t"})
    fill(P["10"].text, {"[paste chunk number]": "2", "[Paste Continuity Tracker]": "t",
                        "[Paste finished narration for this chunk]": "n"})
    fill(P["11"].text, {"[Paste finished narration for this chunk]": "n"})


def test_phase5_placeholders_exist():
    fill(P["5"].text, {"[paste that exact saved sentence here]": "t",
                       "[Paste the full assembled Part 1 narration here]": "n"})


def test_copy_without_end_is_a_clear_error(tmp_path):
    spec = tmp_path / "spec.md"
    spec.write_text("## PROMPT 1 — Broken\n\n**COPY EVERYTHING BELOW**\n\ntext with no end\n")
    with pytest.raises(ValueError, match="PROMPT 1 — Broken.*no \\*\\*END OF"):
        load_prompts(spec)


# --- IS-1: generalized loader (Image-Sync v3 spec) --------------------------------------

from plotpilot import config as pp_config  # noqa: E402
from plotpilot.prompts import load_section  # noqa: E402

V3 = pp_config.SPEC_PATH.parent / "image-sync-v3.md"
V3_HEADING = r"^## (STAGE \d) — "
V3_REQUIRED = ("STAGE 0", "STAGE 1", "STAGE 2")


def _v3(path=V3):
    return load_prompts(path, heading_re=V3_HEADING, key=lambda m: m[1], required=V3_REQUIRED)


def test_v3_stages_load():
    stages = _v3()
    assert set(stages) == set(V3_REQUIRED)
    assert stages["STAGE 0"].text.startswith("You are preparing a narration for image generation.")
    assert all(p.text for p in stages.values())


def test_v4_loading_is_unchanged():
    assert load_prompts() == load_prompts(pp_config.SPEC_PATH)


@pytest.mark.parametrize("damage", [
    lambda t: t.replace("**END OF STAGE 1**", ""),
    lambda t: t.replace("## STAGE 1 — CHARACTER / LOCATION / OBJECT REFERENCES", "## SOMETHING ELSE"),
    lambda t: t.replace("**COPY EVERYTHING BELOW**\n\nYou are a visual continuity", "You are a visual continuity"),
])
def test_v3_missing_required_section_fails_closed(tmp_path, damage):
    spec = tmp_path / "v3.md"
    spec.write_text(damage(V3.read_text()))
    with pytest.raises(ValueError, match="STAGE 1"):
        _v3(spec)


def test_load_section():
    text = load_section(V3, "MANHWA STYLE SPECIFICATION")
    assert "**Locked style suffix" in text and "## TOOL PORTABILITY" not in text
    with pytest.raises(ValueError, match="NOPE"):
        load_section(V3, "NOPE")
