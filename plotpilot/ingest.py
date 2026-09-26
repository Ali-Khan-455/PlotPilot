"""Pure text parsing and chunk planning. No I/O."""

import math
import re
from dataclasses import dataclass, field, replace

from plotpilot import config

BREAK_RE = re.compile(r"^[ \t]*(?:[*#~=\-][ \t]*){3,}$")


def is_break(line: str) -> bool:
    return bool(BREAK_RE.match(line))


def count_words(text: str) -> int:
    return sum(len(line.split()) for line in text.split("\n") if not is_break(line))


def split_scenes(body: str) -> list[str]:
    segments, current = [], []
    for line in body.split("\n") + ["***"]:
        if is_break(line):
            seg = "\n".join(current).strip()
            if seg:
                segments.append(seg)
            current = []
        else:
            current.append(line)
    return segments


NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90, "hundred": 100,
}
_NW = "|".join(sorted(NUM_WORDS, key=len, reverse=True))
_ROMAN = r"(?=[ivxlcdm])m{0,4}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})"  # valid numerals only
# After the number (any of the three forms) the line must end, or continue with : . - – — , or with a
# space and then ( [ an opening quote or a word that starts with an uppercase letter ("Chapter 1 The
# Beginning"). The space keeps possessives ("Chapter 2's ending") out. So prose such
# as "Chapter 12 of the regulations forbade it." is not a heading. The group is atomic so "Twenty-One
# of them" can't backtrack to "Twenty" + "-One of them".
CHAPTER_RE = re.compile(
    r"^[ \t]*(?:chapter|ch\.)[ \t]*"
    rf"(?P<num>(?>\d+|{_ROMAN}|(?:{_NW})(?:[- ](?:and[- ])?(?:{_NW}))*))"
    r"""\b(?=[^\S\n]*(?:$|[:.\-—–,])|[^\S\n]+(?:[(\["'“‘]|(?-i:[A-Z])))[^\n]{0,80}$""",
    re.I,
)
SMALL_PRINT_RE = re.compile(r"^\*END\*THE SMALL PRINT", re.I)
SIDE_RE = re.compile(r"^[ \t]*(?P<kw>prologue|epilogue)(?:[ \t]*[:.,\-–—][^\n]{0,80})?[ \t]*$", re.I)
GUT_START_RE = re.compile(r"^\*\*\* ?START OF (THE|THIS) PROJECT GUTENBERG", re.I)
GUT_END_RE = re.compile(r"^\*\*\* ?END OF (THE|THIS) PROJECT GUTENBERG", re.I)
GUT_END_LINE_RE = re.compile(r"^[ \t]*END OF (?:THE )?PROJECT GUTENBERG", re.I)  # ends the text only after the last heading
ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}


@dataclass(frozen=True)
class Chapter:
    idx: int
    heading: str
    line_no: int
    body: str
    words: int


@dataclass
class Parsed:
    chapters: list[Chapter]
    front_words: int = 0
    trailing_words: int = 0
    short_chapters: list[int] = field(default_factory=list)
    sequence_warnings: list[str] = field(default_factory=list)


def is_heading_line(line: str) -> bool:
    return bool(CHAPTER_RE.match(line) or SIDE_RE.match(line))


def roman_to_int(s: str) -> int:
    vals = [ROMAN[c] for c in s.lower()]
    return sum(-v if i + 1 < len(vals) and v < vals[i + 1] else v for i, v in enumerate(vals))


def words_to_int(s: str) -> int:
    total = 0
    for tok in re.split(r"[- ]+", s.lower().strip()):
        if tok == "and":
            continue
        v = NUM_WORDS[tok]
        total = max(total, 1) * 100 if v == 100 else total + v
    return total


def heading_number(heading: str) -> int | None:
    m = CHAPTER_RE.match(heading)
    if not m:
        return None
    num = m["num"]
    if num.isdigit():
        return int(num)
    if re.fullmatch(r"[ivxlcdm]+", num, re.I) and num.lower() not in NUM_WORDS:
        return roman_to_int(num)
    return words_to_int(num)


def _side_title(heading: str, side) -> str:
    """A Prologue/Epilogue title without dot leaders or a page number (arabic, or roman after a leader)."""
    title = re.sub(r"[\s.\d]+$", "", heading.strip()[side.end("kw"):])
    title = re.sub(r"(?:\.{2,}|\s{2,}|\t)[\s.]*[ivxlcdm]+$", "", title, flags=re.I)
    return re.sub(r"[^a-z0-9]", "", title.lower())


def same_heading(a: str, b: str, bare_matches_any: bool = True) -> bool:
    """A contents entry and its real heading: the same chapter number, or the same Prologue/Epilogue
    keyword with the same title (a bare "Prologue" matches any titled one when bare_matches_any)."""
    sa, sb = SIDE_RE.match(a), SIDE_RE.match(b)
    if sa and sb:
        ta, tb = _side_title(a, sa), _side_title(b, sb)
        loose = bare_matches_any and (not ta or not tb)
        return sa["kw"].lower() == sb["kw"].lower() and (loose or ta == tb)
    if sa or sb:
        return False
    n = heading_number(a)
    return n is not None and n == heading_number(b)


def _heading_dense(ch: Chapter) -> bool:
    lines = [ln for ln in ch.body.split("\n") if ln.strip()]
    return bool(lines) and sum(is_heading_line(ln) for ln in lines) / len(lines) >= config.TOC_LINE_RATIO


def _looks_like_contents(ch: Chapter) -> bool:
    return ch.words < config.MIN_CHAPTER_WORDS or _heading_dense(ch)


def parse_novel(text: str) -> Parsed:
    lines = text.split("\n")
    start = next((i + 1 for i, ln in enumerate(lines) if GUT_START_RE.match(ln)), None)
    if start is None:
        # Old etexts end their header with "*END*THE SMALL PRINT"; others put the small print at the end
        # of the file, so it only counts as a start when it comes before the first heading.
        first_head = next((i for i, ln in enumerate(lines)
                           if (i == 0 or not lines[i - 1].strip()) and is_heading_line(ln)), len(lines))
        start = next((i + 1 for i, ln in enumerate(lines[:first_head]) if SMALL_PRINT_RE.match(ln)), 0)
    last_head = max((i for i in range(start, len(lines))
                     if (i == start or not lines[i - 1].strip()) and is_heading_line(lines[i])), default=-1)
    end = next((i for i in range(start, len(lines))
                if GUT_END_RE.match(lines[i]) or (i > last_head and GUT_END_LINE_RE.match(lines[i]))), len(lines))
    trailing_words = count_words("\n".join(lines[end:]))

    heads = [
        i for i in range(start, end)
        if (i == start or not lines[i - 1].strip()) and is_heading_line(lines[i])
    ]
    if not heads:
        return Parsed([], count_words("\n".join(lines[:end])), trailing_words)

    front_words = count_words("\n".join(lines[:heads[0]]))
    raw = []
    for i, nxt in zip(heads, heads[1:] + [end]):
        body = "\n".join(lines[i + 1:nxt]).strip()
        raw.append(Chapter(0, lines[i].strip(), i + 1, body, count_words(body)))

    # Fold the leading run of table-of-contents entries into front matter. An entry must
    # look like contents AND be repeated by a later real heading, so a short real chapter
    # is never dropped. The last chapter has no later heading, so something is always kept.
    k = 0
    while k < len(raw) and _looks_like_contents(raw[k]):
        # A bare "Prologue" entry matches a titled one only when it holds no prose (a real contents entry),
        # so a short real Prologue before a later "Prologue: Part Two" is kept.
        listing = raw[k].words == 0 or _heading_dense(raw[k])
        if not any(same_heading(raw[k].heading, h.heading, listing) for h in raw[k + 1:]):
            break
        front_words += count_words(raw[k].heading) + raw[k].words
        k += 1

    chapters = [replace(c, idx=n) for n, c in enumerate(raw[k:], 1)]
    short = [c.idx for c in chapters if c.words < config.MIN_CHAPTER_WORDS]

    warnings, prev = [], None
    for c in chapters:
        n = heading_number(c.heading)
        if n is None:
            continue
        if prev and n <= prev[0]:
            warnings.append(
                f"WARNING: chapter sequence goes backwards or repeats at line {c.line_no} "
                f"('{c.heading}' after '{prev[1]}'); possible false-positive heading."
            )
        prev = (n, c.heading)

    return Parsed(chapters, front_words, trailing_words, short, warnings)


@dataclass(frozen=True)
class Chunk:
    idx: int
    label: str
    chapter_start: int
    chapter_end: int
    text: str
    words: int


def _book_number(ch: Chapter) -> str:
    """The book's own name for a chapter: its number, or Prologue/Epilogue (else its position)."""
    n = heading_number(ch.heading)
    if n is not None:
        return str(n)
    side = SIDE_RE.match(ch.heading)
    return side["kw"].title() if side else str(ch.idx)


def _label(first: Chapter, last: Chapter) -> str:
    """'Ch 1–5', 'Ch 7', 'Prologue–Ch 4', 'Ch 5–Epilogue', using the book's own numbering."""
    a, b = _book_number(first), _book_number(last)
    if first is last or a == b:
        return f"Ch {a}" if a.isdigit() else a
    if a.isdigit() and b.isdigit():
        return f"Ch {a}–{b}"
    return "–".join(f"Ch {x}" if x.isdigit() else x for x in (a, b))


def plan_chunks(chapters: list[Chapter]) -> list[Chunk]:
    chunks: list[Chunk] = []
    group: list[Chapter] = []

    def add(label, start, end, text, words):
        chunks.append(Chunk(len(chunks) + 1, label, start, end, text, words))

    def flush():
        if group:
            a, b = group[0].idx, group[-1].idx
            add(_label(group[0], group[-1]), a, b,
                "\n\n".join(f"{c.heading}\n\n{c.body}" for c in group),
                sum(c.words for c in group))
            group.clear()

    for ch in chapters:
        if ch.words > config.MAX_WORDS:
            flush()
            # Pack scene segments greedily; one segment over the cap stays oversize.
            pieces: list[list[str]] = []
            for seg in split_scenes(ch.body):
                if pieces and sum(map(count_words, pieces[-1])) + count_words(seg) <= config.MAX_WORDS:
                    pieces[-1].append(seg)
                else:
                    pieces.append([seg])
            for k, piece in enumerate(pieces, 1):
                text = "\n\n* * *\n\n".join(piece)
                if k == 1:
                    text = f"{ch.heading}\n\n{text}"
                label = _label(ch, ch) if len(pieces) == 1 else f"{_label(ch, ch)} (part {k}/{len(pieces)})"
                add(label, ch.idx, ch.idx, text, sum(map(count_words, piece)))
            continue
        if len(group) == config.MAX_CHAPTERS or sum(c.words for c in group) + ch.words > config.MAX_WORDS:
            flush()
        group.append(ch)
    flush()
    return chunks


def estimate_tokens(words: int) -> int:
    return math.ceil(words * config.TOKENS_PER_WORD)
