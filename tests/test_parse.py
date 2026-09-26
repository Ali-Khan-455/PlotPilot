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

from plotpilot import config  # noqa: E402
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
    fc = parse_factcheck('- Guard reveals map: MISSING\n- "I stabbed him." INVENTED\n- X: PRESENT\nStep 4: FAIL')
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


# --- Phase 3 final-review fixes --------------------------------------------------

@pytest.mark.parametrize("text", [
    "**Step 4: Final verdict (PASS only if nothing is MISSING or INVENTED)**\nFAIL — two plot points are MISSING.",
    "**Step 4 — Final verdict:** PASS requires no MISSING items.\n**Result: FAIL**",
    "Step 4: FAIL",
    "**Step 4: Final verdict**\n\nFAIL — two plot points are MISSING.",
    "Final verdict: Fail",
])
def test_factcheck_fail_formats(text):
    assert parse_factcheck(text).passed is False


@pytest.mark.parametrize("text", ["Step 4: PASS", "## Step 4: Final Verdict\n**PASS** — no MISSING and no INVENTED items."])
def test_factcheck_pass_formats(text):
    assert parse_factcheck(text).passed is True


def test_factcheck_quoted_lowercase_pass_is_not_a_verdict():
    assert parse_factcheck('- "I pass the guard." PRESENT\nVerdict: PASS').passed


@pytest.mark.parametrize("neg", ["- None. Every stretch has an aside.", "- None — texture is consistent throughout.",
                                 "- No stretches longer than 4 sentences without an aside.",
                                 "No flat stretches detected."])
def test_audit_realistic_negatives(neg):
    assert parse_audit(AUDIT.format(gaps=neg)) == []


def test_audit_inline_header_item():
    text = "5. **Texture gaps:** Sentences 12-18 have no aside.\n\n6. **TTS hazards**\n- None"
    assert parse_audit(text) == ["Sentences 12-18 have no aside."]


def test_audit_item_mentioning_tone_drift_is_kept():
    gaps = parse_audit(AUDIT.format(gaps="1. Sentences 4-9 flat, plus some tone drift."))
    assert gaps == ["Sentences 4-9 flat, plus some tone drift."]


def test_audit_renamed_following_sections_end_it():
    text = "5. Texture gaps\n- Flat middle\n6. TTS issues\n- digits\n7. Tone\n- ok"
    assert parse_audit(text) == ["Flat middle"]


def test_rewrite_custom_ratio():
    old = " ".join(["word"] * 100)
    check_rewrite(" ".join(["word"] * 85), old)  # fine at the default ratio (Prompt 8)
    with pytest.raises(ParseError):
        check_rewrite(" ".join(["word"] * 85), old, min_ratio=config.TTS_MIN_RATIO)  # not for Prompt 9


# --- Phase 4: continuation drafts and sentence helpers ------------------------

from plotpilot.parse import count_sentences, first_sentence, parse_continuation  # noqa: E402


def test_parse_continuation():
    assert parse_continuation("  I kept walking. The road was long.\n") == "I kept walking. The road was long."
    for bad in ["", "<<<MARGIN_START>>> x", "Here is the narration:\nI walked."]:
        with pytest.raises(ParseError):
            parse_continuation(bad)


@pytest.mark.parametrize("text,first", [
    ('"Run!" she said. Then more.', '"Run!" she said.'),
    ('He said "Run." and left.', 'He said "Run." and left.'),
    ('He said "Run!" and left. Next.', 'He said "Run!" and left.'),
    ("One. Two.", "One."),
    ("No terminal punctuation", "No terminal punctuation"),
])
def test_first_sentence(text, first):
    assert first_sentence(text) == first


def test_count_sentences():
    assert count_sentences("I am Kai. I farm.") == 2
    assert count_sentences("Just one") == 1


# --- Phase 5: hook (Prompt 5) and hook TTS ----------------------------------------

from plotpilot.parse import check_hook_tts, ends_with_target, norm_words, parse_hook  # noqa: E402

HT = "Nobody expected much from me."


def hook(inner, tail=""):
    return f"<<<HOOK_START>>>\n{inner}\n<<<HOOK_END>>>\n{tail}"


def test_parse_hook_accepts_and_collapses():
    assert parse_hook(hook("I got reborn   a peasant.\nClassic."), HT) == "I got reborn a peasant. Classic."
    assert parse_hook(hook("I was poor.", "hook length: 3 sentences"), HT) == "I was poor."
    assert parse_hook("Sure.\n" + hook('I said "fine."'), HT) == 'I said "fine."'


@pytest.mark.parametrize("text", [
    "I was poor.",
    hook("I was poor.") + hook("Again."),
    "<<<HOOK_END>>>\nI was poor.\n<<<HOOK_START>>>",
    hook("   "),
    hook("I was poor. Nobody expected much from me."),
    hook("I was poor. “Nobody expected much from me.”"),
    hook("I was poor.\nhook length: 2 sentences"),
    hook("I was poor and"),
    hook("I was <<<poor>>>."),
    hook("."),
    hook("“.”"),
    hook("…."),
])
def test_parse_hook_rejects(text):
    with pytest.raises(ParseError):
        parse_hook(text, HT)


def test_hook_target_rule_is_end_only():
    assert parse_hook(hook("I ran for my life as the dragon breathed fire."), "I ran.")
    with pytest.raises(ParseError):
        parse_hook(hook("The dragon attacked. I ran."), "I ran.")


def test_norm_words_and_ends_with_target():
    assert norm_words("“Don’t” — RUN!") == ["dont", "run"]
    assert ends_with_target("The dragon attacked. I ran!", "I ran.")
    assert not ends_with_target("I ran home.", "I ran.")
    assert not ends_with_target("Anything.", "—")


def test_check_hook_tts():
    assert check_hook_tts("I was 3 years old.", "I was 3 years old.", HT) == "I was 3 years old."
    assert check_hook_tts("I was three years old.", "I was 3 years old.", HT)
    for bad in ["I was old.", "Here is the normalized narration:\nI was three years old.",
                "I was three years old. Nobody expected much from me.",
                "I was three years old.\nhook length: 1 sentence"]:
        with pytest.raises(ParseError):
            check_hook_tts(bad, "I was 3 years old.", HT)


# --- deferred minors (Phase 2) --------------------------------------------------

def test_check_margin_quoted_opening():
    assert check_margin('"But I was poor," I said.') == "sentence starts with 'But'"
    assert check_margin("“And so I left.”") == "sentence starts with 'And'"


def test_check_margin_more_than_two_sentences():
    assert check_margin("I am a boy. I live here. I farm.") == "more than two sentences"
    assert check_margin("I am a boy. I live here.") is None


# --- deferred minors (Phase 3) --------------------------------------------------

W50 = " ".join(["word"] * 50)


@pytest.mark.parametrize("bad", [
    "Sure! Here is the normalized narration:\n" + W50,
    "Normalized narration:\n" + W50,
    W50 + "\n\nI hope this helps!",
    W50 + "\n\nEnd of normalized narration.",
    W50 + "\n\nNote: I kept the phonetic hint for Kael unchanged as instructed.",
    W50 + "\n\n(Note: numbers were converted to words throughout the whole narration above.)",
])
def test_rewrite_rejects_more_wrappers(bad):
    with pytest.raises(ParseError):
        check_rewrite(bad, W50)


def test_rewrite_accepts_dash_cliffhanger():
    check_rewrite(W50 + "\n\nThen the door opened—", W50)


def test_factcheck_step_headers_are_not_flags():
    text = ("**Step 2: PRESENT / MISSING check**\n- The guard reveals the map: MISSING\n"
            "## Step 3 — INVENTED lines\n- \"I flew.\" INVENTED\nFinal verdict: FAIL")
    assert parse_factcheck(text).flags == ["- The guard reveals the map: MISSING", '- "I flew." INVENTED']


def test_hook_tts_may_drop_one_word():
    old = "I was the kid who read every book in the whole village library."
    assert check_hook_tts("I was the kid who read each book in the village library.", old, HT)
    with pytest.raises(ParseError):
        check_hook_tts("I was the kid who read books in the village library.", old, HT)
