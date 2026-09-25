"""Pure text parsing and chunk planning. No I/O."""

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
CHAPTER_RE = re.compile(
    r"^[ \t]*(?:chapter|ch\.)[ \t]*"
    rf"(?P<num>\d+|[ivxlcdm]+(?=[ \t]*(?:$|[:.\-—]))|(?:{_NW})(?:[- ](?:{_NW}))?)"
    r"\b[^\n]{0,80}$",
    re.I,
)
SIDE_RE = re.compile(r"^[ \t]*(?P<kw>prologue|epilogue)(?:[ \t]*[:.\-—][^\n]{0,80})?[ \t]*$", re.I)
GUT_START_RE = re.compile(r"^\*\*\* ?START OF (THE|THIS) PROJECT GUTENBERG", re.I)
GUT_END_RE = re.compile(r"^\*\*\* ?END OF (THE|THIS) PROJECT GUTENBERG", re.I)
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
    all_folded: int = 0  # headings found but every chapter folded as contents


def is_heading_line(line: str) -> bool:
    return bool(CHAPTER_RE.match(line) or SIDE_RE.match(line))


def roman_to_int(s: str) -> int:
    vals = [ROMAN[c] for c in s.lower()]
    return sum(-v if i + 1 < len(vals) and v < vals[i + 1] else v for i, v in enumerate(vals))


def words_to_int(s: str) -> int:
    total = 0
    for tok in re.split(r"[- ]+", s.lower().strip()):
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


def _looks_like_contents(ch: Chapter) -> bool:
    if ch.words < config.MIN_CHAPTER_WORDS:
        return True
    lines = [ln for ln in ch.body.split("\n") if ln.strip()]
    return sum(is_heading_line(ln) for ln in lines) / len(lines) >= config.TOC_LINE_RATIO


def parse_novel(text: str) -> Parsed:
    lines = text.split("\n")
    start = next((i + 1 for i, ln in enumerate(lines) if GUT_START_RE.match(ln)), 0)
    end = next((i for i in range(start, len(lines)) if GUT_END_RE.match(lines[i])), len(lines))
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

    # Fold the leading run of table-of-contents entries into front matter.
    k = 0
    while k < len(raw) and _looks_like_contents(raw[k]):
        side = SIDE_RE.match(raw[k].heading)
        if side and not any(
            h.heading.lower().startswith(side["kw"].lower()) for h in raw[k + 1:]
        ):
            break  # a real (short) prologue/epilogue, not a contents entry
        front_words += count_words(raw[k].heading) + raw[k].words
        k += 1
    if k == len(raw):
        return Parsed([], front_words, trailing_words, all_folded=len(raw))

    chapters = [replace(c, idx=n) for n, c in enumerate(raw[k:], 1)]
    short = [c.idx for c in chapters if c.words < config.MIN_CHAPTER_WORDS]

    warnings, prev = [], None
    for c in chapters:
        n = heading_number(c.heading)
        if n is None:
            continue
        if prev and n < prev[0]:
            warnings.append(
                f"WARNING: chapter sequence goes backwards at line {c.line_no} "
                f"('{c.heading}' after '{prev[1]}'); possible false-positive heading."
            )
        prev = (n, c.heading)

    return Parsed(chapters, front_words, trailing_words, short, warnings)
