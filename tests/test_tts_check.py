from plotpilot.tts_check import tts_hazards


def kinds(text):
    return [(line, kind) for line, kind, _ in tts_hazards(text)]


def test_each_hazard_kind_and_line():
    text = "It was 1984.\nI paused; then left.\nWait — no.\nFrom A – B.\nWell... fine…\nHe (quietly) left."
    assert kinds(text) == [(1, "digit"), (2, "semicolon"), (3, "em dash"), (4, "en dash"),
                           (5, "ellipsis"), (5, "ellipsis"), (6, "parenthesis")]


def test_clean_narration():
    assert tts_hazards('I said, "Run!" It\'s over: done? Yes.') == []


def test_phonetic_hint_allowed():
    assert tts_hazards("Kael [rhymes with 'kale'] waved.") == []
