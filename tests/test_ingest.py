from plotpilot.ingest import count_words, split_scenes


# --- count_words / split_scenes ---------------------------------------------

def test_split_scenes_recognises_break_forms():
    for brk in ["***", "* * *", "---", "###", "  ~ ~ ~  ", "====="]:
        assert split_scenes(f"one two\n\n{brk}\n\nthree") == ["one two", "three"]


def test_split_scenes_without_breaks_is_one_segment():
    assert split_scenes("a b c\nd e") == ["a b c\nd e"]


def test_split_scenes_drops_blank_segments():
    assert split_scenes("***\n\na\n***\n\n***\nb\n***") == ["a", "b"]


def test_count_words_ignores_break_lines():
    assert count_words("one two\n***\n* * *\nthree") == 3


def test_two_dashes_are_not_a_break():
    assert split_scenes("a\n--\nb") == ["a\n--\nb"]
