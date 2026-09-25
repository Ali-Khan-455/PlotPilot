import pytest

from plotpilot.parse import ParseError, check_margin, parse_draft, parse_module, parse_repair

MARGIN = "I am a farmer's son in a poor village."
TARGET = "Nobody expected much from me."


def out(margin=MARGIN, target=TARGET, after="margin is 1 sentences.\n\n" + TARGET + " Rest of story."):
    return (f"<<<MARGIN_START>>>\n{margin}\n<<<MARGIN_END>>>\n"
            f"<<<TARGET_SENTENCE_START>>>\n{target}\n<<<TARGET_SENTENCE_END>>>\n{after}")


def test_parse_draft_happy():
    d = parse_draft(out())
    assert (d.margin, d.target, d.margin_sentences) == (MARGIN, TARGET, 1)
    assert d.body == TARGET + " Rest of story."
    assert d.narration == MARGIN + " " + TARGET + " Rest of story."


def test_target_absent_is_prepended():
    d = parse_draft(out(after="margin is 1 sentence.\nRest of story."))
    assert d.body == TARGET + " Rest of story."


def test_target_matches_after_normalizing():
    d = parse_draft(out(target="Nobody said “hi”.", after="Nobody  said \"hi\". Rest."))
    assert d.body.count("said") == 1


def test_target_only_mid_body_is_error():
    with pytest.raises(ParseError):
        parse_draft(out(after="Paraphrased opening. " + TARGET + " More."))


def test_count_line_removed_anywhere_and_derived_when_absent():
    d = parse_draft(out(after=TARGET + " A.\nmargin is 2 sentences\nB."))
    assert "margin is" not in d.body and d.margin_sentences == 2
    d = parse_draft(out(margin="One. Two.", after=TARGET))
    assert d.margin_sentences == 2


def test_two_digit_count_is_error():
    with pytest.raises(ParseError):
        parse_draft(out(after="margin is 10 sentences\n" + TARGET))


@pytest.mark.parametrize("bad", [
    out().replace("<<<MARGIN_END>>>\n", ""),
    out() + "\n<<<MARGIN_START>>>",
    out().replace("<<<MARGIN_START>>>", "<<<X>>>").replace("<<<MARGIN_END>>>", "<<<MARGIN_START>>>")
         .replace("<<<X>>>", "<<<MARGIN_END>>>"),
    out(margin="  "),
])
def test_marker_errors(bad):
    with pytest.raises(ParseError):
        parse_draft(bad)


def test_parse_repair():
    assert parse_repair("<<<MARGIN_START>>>\nNew margin.\n<<<MARGIN_END>>>") == "New margin."
    with pytest.raises(ParseError):
        parse_repair("New margin.")


@pytest.mark.parametrize("margin,reason", [
    ("Who am I?", "question mark"),
    ("Little did I know it was me.", "phrase 'little did'"),
    ("I was born. And then it rained.", "sentence starts with 'And'"),
    ("But I was born poor.", "sentence starts with 'But'"),
    (" ".join(["word"] * 30) + ".", "average sentence length 30 > 25 words"),
])
def test_check_margin_triggers(margin, reason):
    assert check_margin(margin) == reason


def test_check_margin_clean():
    assert check_margin(MARGIN) is None
    assert check_margin("Andrew is my name.") is None


def test_parse_module():
    assert parse_module("B") == "B" and parse_module("B.") == "B" and parse_module(" c ") == "C"
    with pytest.raises(ParseError):
        parse_module("Module B")
