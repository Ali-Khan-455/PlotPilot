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


# --- final-review fixes --------------------------------------------------------

@pytest.mark.parametrize("line", ["margin is one sentence.", "**margin is 1 sentence.**",
                                  "Margin is two sentences", "(margin is 2 sentences)"])
def test_count_line_variants_are_stripped(line):
    d = parse_draft(out(after=f"{line}\n\n{TARGET} Rest."))
    assert "margin is" not in d.body.lower() and d.body == TARGET + " Rest."


def test_leftover_count_text_is_error():
    with pytest.raises(ParseError):
        parse_draft(out(after=f"{TARGET} Rest. The margin is twelve sentences long."))


def test_body_restating_margin_is_accepted():
    d = parse_draft(out(after=f"{MARGIN}\n{TARGET} Rest."))
    assert d.body == TARGET + " Rest." and d.narration.count(MARGIN) == 1


def test_quoted_target_matches_unquoted_body():
    d = parse_draft(out(target=f'"{TARGET}"', after=f"margin is 1 sentence.\n\n{TARGET} Rest."))
    assert d.body.count("Nobody") == 1


# --- Phase 3: audit / fact-check / rewrite parsers ----------------------------

from plotpilot.parse import check_rewrite, parse_audit, parse_factcheck  # noqa: E402

AUDIT = """1. **Plot points missing**
- None

4. **POV violations**
- None

5. **Texture gaps**
{gaps}

6. **TTS hazards**
- "1984" not written as words

7. **Tone drift**
- None
"""


def test_audit_numbered_gaps_returned():
    gaps = parse_audit(AUDIT.format(gaps="1. Sentences 3-9 read flat.\n2. No MC aside across sentences 12-18."))
    assert gaps == ["Sentences 3-9 read flat.", "No MC aside across sentences 12-18."]


def test_audit_bold_colon_header_not_an_item():
    text = "Intro: I checked for texture gaps carefully.\n\n**Texture gaps:**\n- Gap one\n- Gap two\n\n**TTS hazards:**\n- None"
    assert parse_audit(text) == ["Gap one", "Gap two"]


def test_audit_markdown_header():
    assert parse_audit("## Texture gaps\n* Flat stretch in paragraph 2\n## Tone drift\n* None") == \
        ["Flat stretch in paragraph 2"]


@pytest.mark.parametrize("neg", ["- None", "- No gaps found.", "None identified.", "Nothing found to report.",
                                 "No gaps identified here.", "- N/A", "---"])
def test_audit_negatives(neg):
    assert parse_audit(AUDIT.format(gaps=neg)) == []


def test_audit_missing_header():
    with pytest.raises(ParseError):
        parse_audit("1. Plot points\n- None")


def test_factcheck_pass():
    assert parse_factcheck("Step 1...\n**Final verdict: PASS**").passed


def test_factcheck_fail_with_flags():
    fc = parse_factcheck('- Guard reveals map: MISSING\n- "I stabbed him." INVENTED\n- X: PRESENT\nStep 4: verdict FAIL')
    assert not fc.passed
    assert fc.flags == ["- Guard reveals map: MISSING", '- "I stabbed him." INVENTED']


def test_factcheck_echoed_rule_then_fail():
    text = ('**Step 4: Give a final verdict: "PASS" if no MISSING and no INVENTED; "FAIL" otherwise.**\n'
            '- "I flew." INVENTED\n\nFAIL')
    assert not parse_factcheck(text).passed


def test_factcheck_step2_echo_not_a_flag():
    text = ('Step 2: For each, state "PRESENT" or "MISSING" in the narration.\n'
            'Step 3: "I stabbed the guard." INVENTED\nFinal verdict: FAIL')
    assert parse_factcheck(text).flags == ['Step 3: "I stabbed the guard." INVENTED']


def test_factcheck_conflicting_candidates_fail_closed():
    assert not parse_factcheck("Verdict: PASS\n...\nVerdict: FAIL").passed


def test_factcheck_no_verdict():
    with pytest.raises(ParseError):
        parse_factcheck("Everything looks present.")


OLD = " ".join(["word"] * 50) + " I ran. \"Run.\" \"Let me know,\" she said."


@pytest.mark.parametrize("ending", ["I ran.", "“Run.”", '"Let me know," she said.'])
def test_rewrite_short_endings_in_input_accepted(ending):
    check_rewrite(" ".join(["word"] * 50) + "\n\n" + ending, OLD)


@pytest.mark.parametrize("ending", ["I fled.", "“Stop.”", '"Let me know," he said.',
                                    "She's happy to help.", "The guard let me know the way."])
def test_rewrite_short_endings_new_text_accepted(ending):
    check_rewrite(" ".join(["word"] * 50) + "\n\n" + ending, " ".join(["word"] * 50))


@pytest.mark.parametrize("bad", [
    "",
    "<<<MARGIN_START>>> " + " ".join(["word"] * 50),
    " ".join(["word"] * 25),
    "Here is the normalized narration:\n" + " ".join(["word"] * 50),
    "```\n" + " ".join(["word"] * 50) + "\n```",
    " ".join(["word"] * 50) + "\n\nHope this helps.",
    " ".join(["word"] * 50) + "\n\nLet me know if you need anything.",
])
def test_rewrite_rejects(bad):
    with pytest.raises(ParseError):
        check_rewrite(bad, " ".join(["word"] * 50))
