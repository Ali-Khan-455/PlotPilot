import pytest

from plotpilot.assemble import SpliceError, chunk_starts, join, scene_lines, splice_check, target_check

T = "Nobody expected much from me."


def words(n, w="word"):
    return " ".join([w] * n)


def test_join_paragraphs_and_trailing_newline():
    assert join("A hook.\nStill hook.", ["  B one.\n\n", "C two.\n\n\n"]) == "A hook. Still hook.\n\nB one.\n\nC two.\n"


def test_chunk_starts():
    assert chunk_starts("one two three four five.", ["a b c", "d e"]) == [5, 8]


def test_target_check():
    target_check(f"“{T}” Then more.", T)
    with pytest.raises(SpliceError):
        target_check(f"Then more. {T}", T)
    with pytest.raises(SpliceError):
        target_check("Anything.", "—")


def test_splice_check():
    splice_check(join("I was poor.", [f"{T} More.", "Later."]), T)
    with pytest.raises(SpliceError):
        splice_check(join("I was poor.", ["More.", T]), T)
    with pytest.raises(SpliceError):
        splice_check(join(f"I was poor. {T}", [f"{T} More."]), T)


def test_first_scene_is_zero_after_hook():
    hook = words(30, "hook") + "."
    script = join(hook, [f"{T} " + words(200) + " The guard came.", "The city at dusk."])
    starts = chunk_starts(hook, [f"{T} " + words(200) + " The guard came.", "The city at dusk."])
    lines, warns = scene_lines(script, starts, [[(T, "start"), ("The guard came.", "guard")],
                                                [("The city at dusk.", "city")]])
    assert lines[0] == "[00:00] SCENE: start"
    # "The" of the guard sentence is at 30 + 5 + 200 = 235 → 94 s
    assert lines[1] == "[01:34] SCENE: guard" and lines[2].endswith("SCENE: city") and warns == []


@pytest.mark.parametrize("offset, stamp", [(150, "[01:00]"), (225, "[01:30]"), (15000, "[100:00]")])
def test_offsets(offset, stamp):
    body = "Start here. " + words(offset - 2) + " Target line."
    lines, _ = scene_lines(body, [0], [[("Start here.", "a"), ("Target line.", "b")]])
    assert lines[1] == f"{stamp} SCENE: b"


def test_matching_ignores_punctuation_quotes_case():
    body = "Start. “Don’t RUN,” she said — twice."
    lines, warns = scene_lines(body, [0], [[("Start.", "a"), ('"don\'t run" she said twice', "b")]])
    assert warns == [] and lines[1] == "[00:00] SCENE: b"


def test_same_sentence_goes_to_right_chunk_and_distinct_offsets():
    b1 = "Go. " + words(150) + " Go. " + words(150)
    b2 = "Go. " + words(10)
    script = b1 + "\n\n" + b2
    starts = [0, len(b1.split())]
    lines, warns = scene_lines(script, starts, [[("Go.", "a"), ("Go.", "b")], [("Go.", "c")]])
    assert warns == []
    assert lines == ["[00:00] SCENE: a", "[01:00] SCENE: b", "[02:00] SCENE: c"]


def test_bounded_search_and_missing_scene_warns():
    b1 = "Start. " + words(150)
    b2 = "Only in two. " + words(150) + " Later."
    script = b1 + "\n\n" + b2
    starts = [0, len(b1.split())]
    lines, warns = scene_lines(script, starts, [[("Start.", "a"), ("Only in two.", "x")],
                                                [("Only in two.", "b"), ("Later.", "c")]])
    assert lines[1] == "[00:00] SCENE: x" and len(warns) == 1 and "(chunk 1)" in warns[0]
    assert lines[2] == "[01:00] SCENE: b" and lines[3] == "[02:01] SCENE: c"


def test_timestamps_never_go_backwards():
    body = "Alpha. " + words(300) + " Beta."
    lines, warns = scene_lines(body, [0], [[("Alpha.", "a"), ("Beta.", "b"), ("Alpha.", "c")]])
    assert lines[2].startswith("[02:00]") and len(warns) == 1


def test_description_is_one_line():
    lines, _ = scene_lines("Alpha.", [0], [[("Alpha.", "two\nlines  here")]])
    assert lines == ["[00:00] SCENE: two lines here"]


def test_first_scene_found_mid_body_warns():
    lines, warns = scene_lines("Start here. Later line.", [0], [[("Later line.", "a")]])
    assert lines == ["[00:00] SCENE: a"] and len(warns) == 1 and "not at the start" in warns[0]
