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


# --- parse_novel --------------------------------------------------------------

from plotpilot.ingest import parse_novel, roman_to_int, words_to_int  # noqa: E402

BODY = " ".join(["word"] * 60)  # comfortably above MIN_CHAPTER_WORDS


def novel(*headings, body=BODY):
    return "\n\n".join(f"{h}\n\n{body}" for h in headings) + "\n"


def headings(text):
    return [c.heading for c in parse_novel(text).chapters]


def test_heading_forms():
    hs = ["Chapter 1", "CHAPTER IV: Title", "Ch. 3", "Chapter Twenty-One", "Prologue",
          "Epilogue: After", "Chapter I"]
    assert headings(novel(*hs)) == hs


def test_wrapped_prose_is_not_a_heading():
    text = novel("Chapter 1") + (
        "\nchapter of his life had closed, and he walked out into the rain.\n"
        "\nprologue to the war had been fought long ago.\n"
        "\nChapter I read the letter twice.\n"
    )
    assert headings(text) == ["Chapter 1"]


def test_heading_needs_blank_line_before():
    text = f"Chapter 1\n\n{BODY}\nChapter 2\n{BODY}\n"
    assert headings(text) == ["Chapter 1"]


def test_front_matter_counted():
    p = parse_novel("Title Page by Someone\n\n" + novel("Chapter 1"))
    assert p.front_words == 4
    assert p.chapters[0].words == 60


def test_blank_separated_contents_folds():
    toc = "\n\n".join(f"CHAPTER {i}. Name{i}." for i in range(1, 4))
    p = parse_novel("CONTENTS\n\n" + toc + "\n\n" + novel("CHAPTER 1. Name1.", "CHAPTER 2. Name2."))
    assert [c.heading for c in p.chapters] == ["CHAPTER 1. Name1.", "CHAPTER 2. Name2."]
    assert p.front_words == 1 + 3 * 3  # "CONTENTS" + three 3-word headings


def test_single_spaced_contents_folds():
    toc = "\n".join(f"CHAPTER {i}. The Long Name Of Chapter {i}." for i in range(1, 21))
    text = "CONTENTS\n\n" + toc + "\n\n" + novel("CHAPTER 1. Real.", "CHAPTER 2. Real.")
    assert headings(text) == ["CHAPTER 1. Real.", "CHAPTER 2. Real."]


def test_all_folded_is_reported():
    p = parse_novel(novel("Chapter 1", "Chapter 2", body="too short"))
    assert p.chapters == [] and p.all_folded == 2


def test_no_headings():
    p = parse_novel("just some text\n\nmore text\n")
    assert p.chapters == [] and p.all_folded == 0


def test_short_real_prologue_is_kept():
    text = novel("Prologue", body=" ".join(["w"] * 30)) + "\n" + novel("Chapter 1", "Chapter 2")
    assert headings(text) == ["Prologue", "Chapter 1", "Chapter 2"]


def test_contents_with_prologue_epilogue_blank_separated():
    toc = "\n\n".join(["Prologue", "Chapter 1", "Chapter 2", "Epilogue"])
    text = toc + "\n\n" + novel("Prologue", "Chapter 1", "Chapter 2", "Epilogue")
    assert headings(text) == ["Prologue", "Chapter 1", "Chapter 2", "Epilogue"]


def test_contents_with_prologue_single_spaced():
    toc = "\n".join(["Prologue", "Chapter 1", "Chapter 2", "Chapter 3", "Epilogue"])
    text = toc + "\n\n" + novel("Prologue", "Chapter 1", "Chapter 2", "Chapter 3", "Epilogue")
    assert headings(text) == ["Prologue", "Chapter 1", "Chapter 2", "Chapter 3", "Epilogue"]


def test_contents_with_dot_leader_prologue():
    toc = "\n".join(["Prologue ........ 1"] + [f"Chapter {i} ........ {i * 10}" for i in range(1, 6)])
    text = toc + "\n\n" + novel("Prologue", "Chapter 1", "Chapter 2")
    assert headings(text) == ["Prologue", "Chapter 1", "Chapter 2"]


def test_sequence_warning_on_false_positive():
    text = novel("Chapter 1", "Chapter 5 was the best year of his life", "Chapter 2")
    p = parse_novel(text)
    assert [c.heading for c in p.chapters] == [
        "Chapter 1", "Chapter 5 was the best year of his life", "Chapter 2"]
    assert len(p.sequence_warnings) == 1
    assert "'Chapter 2' after 'Chapter 5 was the best year of his life'" in p.sequence_warnings[0]
    assert "at line 9" in p.sequence_warnings[0]


def test_number_conversion():
    assert roman_to_int("IV") == 4 and roman_to_int("xiv") == 14
    assert words_to_int("Twenty-One") == 21 and words_to_int("Seven") == 7
    assert words_to_int("One Hundred") == 100


def test_gutenberg_frame_dropped():
    text = ("Project Gutenberg header text here\n"
            "*** START OF THE PROJECT GUTENBERG EBOOK MOBY DICK ***\n\n"
            + novel("Chapter 1")
            + "\n*** END OF THE PROJECT GUTENBERG EBOOK MOBY DICK ***\nlicence words go here\n")
    p = parse_novel(text)
    assert headings(text) == ["Chapter 1"]
    assert p.chapters[0].words == 60
    assert p.front_words == 5 + 10
    assert p.trailing_words == 10 + 4


def test_short_chapter_mid_book_kept_and_flagged():
    text = novel("Chapter 1") + "\n" + novel("Chapter 2", body="short") + "\n" + novel("Chapter 3")
    p = parse_novel(text)
    assert [c.heading for c in p.chapters] == ["Chapter 1", "Chapter 2", "Chapter 3"]
    assert p.short_chapters == [2]
