import pytest

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

from plotpilot.ingest import heading_number, parse_novel, roman_to_int, words_to_int  # noqa: E402

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
    toc = "\n\n".join(f"CHAPTER {i}. Name{i}." for i in range(1, 3))
    p = parse_novel("CONTENTS\n\n" + toc + "\n\n" + novel("CHAPTER 1. Name1.", "CHAPTER 2. Name2."))
    assert [c.heading for c in p.chapters] == ["CHAPTER 1. Name1.", "CHAPTER 2. Name2."]
    assert p.front_words == 1 + 2 * 3  # "CONTENTS" + two 3-word headings


def test_single_spaced_contents_folds():
    toc = "\n".join(f"CHAPTER {i}. The Long Name Of Chapter {i}." for i in range(1, 21))
    text = "CONTENTS\n\n" + toc + "\n\n" + novel("CHAPTER 1. Real.", "CHAPTER 2. Real.")
    assert headings(text) == ["CHAPTER 1. Real.", "CHAPTER 2. Real."]


def test_all_short_chapters_are_kept():
    p = parse_novel(novel("Chapter 1", "Chapter 2", body="too short"))
    assert [c.heading for c in p.chapters] == ["Chapter 1", "Chapter 2"]
    assert p.short_chapters == [1, 2]


def test_no_headings():
    assert parse_novel("just some text\n\nmore text\n").chapters == []


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
    text = novel("Chapter 1", "Chapter 5: A misplaced heading", "Chapter 2")
    p = parse_novel(text)
    assert [c.heading for c in p.chapters] == ["Chapter 1", "Chapter 5: A misplaced heading", "Chapter 2"]
    assert len(p.sequence_warnings) == 1
    assert "'Chapter 2' after 'Chapter 5: A misplaced heading'" in p.sequence_warnings[0]
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


# --- plan_chunks / estimate_tokens ------------------------------------------

from plotpilot.ingest import Chapter, estimate_tokens, plan_chunks  # noqa: E402


def chap(idx, words, body=None):
    body = body if body is not None else " ".join(["w"] * words)
    return Chapter(idx, f"Chapter {idx}", idx, body, count_words(body))


def scened(n_segments, words_each):
    return "\n\n* * *\n\n".join(" ".join(["w"] * words_each) for _ in range(n_segments))


def test_small_chapters_pack_five_per_chunk():
    chunks = plan_chunks([chap(i, 100) for i in range(1, 13)])
    assert [(c.chapter_start, c.chapter_end) for c in chunks] == [(1, 5), (6, 10), (11, 12)]
    assert [c.label for c in chunks] == ["Ch 1–5", "Ch 6–10", "Ch 11–12"]


def test_word_cap_flushes_before_chapter_cap():
    chunks = plan_chunks([chap(i, 5000) for i in range(1, 5)])
    assert [(c.chapter_start, c.chapter_end) for c in chunks] == [(1, 2), (3, 4)]
    assert all(c.words <= 12_000 for c in chunks)


def test_long_chapter_splits_at_scene_breaks():
    ch = chap(1, 0, scened(10, 2000))  # 20k words
    chunks = plan_chunks([ch])
    assert [c.label for c in chunks] == ["Ch 1 (part 1/2)", "Ch 1 (part 2/2)"]
    assert all(c.words <= 12_000 for c in chunks)
    assert chunks[0].text.startswith("Chapter 1\n\n")
    assert "Chapter 1" not in chunks[1].text
    assert "* * *" in chunks[0].text


def test_long_chapter_without_breaks_is_one_oversize_chunk():
    chunks = plan_chunks([chap(1, 15_000)])
    assert len(chunks) == 1 and chunks[0].label == "Ch 1" and chunks[0].words == 15_000


def test_long_chapter_flushes_preceding_chunk():
    chunks = plan_chunks([chap(1, 100), chap(2, 0, scened(10, 2000)), chap(3, 100)])
    assert [c.label for c in chunks] == ["Ch 1", "Ch 2 (part 1/2)", "Ch 2 (part 2/2)", "Ch 3"]
    assert [c.idx for c in chunks] == [1, 2, 3, 4]


def test_word_conservation():
    chapters = [chap(1, 300), chap(2, 0, scened(13, 1500)), chap(3, 11_000), chap(4, 800)]
    chunks = plan_chunks(chapters)
    assert sum(c.words for c in chunks) == sum(c.words for c in chapters)
    assert all(c.words == count_words(c.text) - count_words(
        "\n".join(ch.heading for ch in chapters if ch.heading in c.text)) for c in chunks)


def test_estimate_tokens():
    assert estimate_tokens(1000) == 1350


# --- final-review fixes --------------------------------------------------------

def test_short_real_first_chapter_is_kept():
    text = "Chapter 1\n\nIt was a dark night.\n\n" + novel("Chapter 2", "Chapter 3")
    p = parse_novel(text)
    assert [c.heading for c in p.chapters] == ["Chapter 1", "Chapter 2", "Chapter 3"]
    assert p.short_chapters == [1]


def test_contents_then_foreword_warns_on_repeated_number():
    toc = "\n".join(f"Chapter {i}" for i in range(1, 21))
    foreword = "\n".join(["FOREWORD"] + [" ".join(["f"] * 10)] * 40)
    p = parse_novel(toc + "\n" + foreword + "\n\n" + novel("Chapter 1", "Chapter 2"))
    assert any("'Chapter 1' after 'Chapter 1'" in w for w in p.sequence_warnings)


def test_en_dash_and_comma_after_numeral():
    hs = ["CHAPTER IV – The Fall", "Chapter V, In Which Things Happen", "Prologue – Dawn"]
    assert headings(novel(*hs)) == hs


# --- deferred minors (Phase 1) --------------------------------------------------

def test_number_headings_need_a_terminator():
    hs = ["Chapter 12: The Fall", "Chapter 13. The Fall", "Chapter 14 - The Fall", "Chapter One",
          "Chapter Twenty-One", "Chapter 15"]
    assert headings(novel(*hs)) == hs
    text = novel("Chapter 1") + ("\nChapter one of my life was over.\n"
                                 "\nChapter 12 of the regulations forbade it.\n"
                                 "\nChapter Twenty-One of them came.\n")
    assert headings(text) == ["Chapter 1"]


def test_numbers_above_one_hundred():
    assert heading_number("Chapter One Hundred One") == 101
    assert heading_number("Chapter Two Hundred and Twenty-One: End") == 221


def test_roman_letter_words_are_not_numerals():
    text = novel("Chapter 1") + "\nChapter did.\n\nChapter civil, they said.\n"
    assert headings(text) == ["Chapter 1"]
    assert heading_number("Chapter XIV.") == 14 and heading_number("chapter xl") == 40


def test_short_prologue_kept_when_later_heading_starts_with_prologue():
    short = " ".join(["w"] * 20)
    text = novel("Prologue", body=short) + "\n" + novel("Chapter 1", "Prologue: Part Two")
    assert headings(text) == ["Prologue", "Chapter 1", "Prologue: Part Two"]


def test_gutenberg_end_of_line_and_old_small_print():
    text = ("*END*THE SMALL PRINT! FOR PUBLIC DOMAIN ETEXTS*Ver.04.29.93*END*\n\n" + novel("Chapter 1")
            + "\nEnd of the Project Gutenberg EBook of Moby Dick\n\n"
            "*** END OF THE PROJECT GUTENBERG EBOOK MOBY DICK ***\nlicence\n")
    p = parse_novel(text)
    assert p.front_words == 7 and p.chapters[0].words == 60 and p.trailing_words == 20
    assert "Gutenberg" not in p.chapters[0].body


# --- review of the deferred-minor fixes ---------------------------------------------

def test_titled_headings_without_punctuation():
    hs = ["Chapter 1 The Beginning", "CHAPTER 12 THE FALL", "Chapter 13 (continued)", 'Chapter 14 "The Fall"',
          "Chapter 15 [Draft]", "Chapter Sixteen Home Again", "CHAPTER XVII THE END", "Chapter 18\u00a0The Wait"]
    assert headings(novel(*hs)) == hs
    text = novel("Chapter 1") + ("\nChapter 12 of the regulations forbade it.\n"
                                 "\nChapter one of my life was over.\n\nChapter 12 the fall\n")
    assert headings(text) == ["Chapter 1"]


def test_small_print_at_end_of_file_is_not_a_start():
    text = (novel("Chapter 1", "Chapter 2") + "\nEnd of the Project Gutenberg EBook of X\n\n"
            "***START**THE SMALL PRINT!**FOR PUBLIC DOMAIN ETEXTS**START***\nlegal words\n"
            "*END*THE SMALL PRINT! FOR PUBLIC DOMAIN ETEXTS*Ver.04.29.93*END*\n")
    assert headings(text) == ["Chapter 1", "Chapter 2"]


@pytest.mark.parametrize("toc, real", [
    (["Prologue ..... ix", "Chapter 1 ..... 1", "Chapter 2 ..... 9"], "Prologue"),
    (["Prologue", "Chapter 1", "Chapter 2"], "Prologue: The Storm"),
])
def test_contents_fold_with_roman_pages_and_bare_entries(toc, real):
    text = "\n".join(toc) + "\n\n" + novel(real, "Chapter 1", "Chapter 2")
    assert headings(text) == [real, "Chapter 1", "Chapter 2"]


def test_same_heading_rules():
    from plotpilot.ingest import same_heading
    assert same_heading("Prologue: Part IX ..... 5", "Prologue: Part IX")
    assert not same_heading("Prologue: Part IX", "Prologue: Part X")
    assert same_heading("Prologue", "Prologue: The Storm")
    assert not same_heading("Prologue (V for Vendetta)", "Prologue (for Vendetta)")
    assert same_heading("Chapter 3 ..... 17", "CHAPTER III")


def test_possessive_prose_is_not_a_heading():
    text = novel("Chapter 1", "Chapter 2") + ("\nChapter 2's ending was sad, I thought.\n"
                                              "\nChapter Seven's rules were strict.\n")
    assert headings(text) == ["Chapter 1", "Chapter 2"]
