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
