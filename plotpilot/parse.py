"""Pure parsers for LLM output: chunk 1 delimiters, margin repair, module letter; margin check."""

import re
from dataclasses import dataclass

MARKERS = ["<<<MARGIN_START>>>", "<<<MARGIN_END>>>",
           "<<<TARGET_SENTENCE_START>>>", "<<<TARGET_SENTENCE_END>>>"]
COUNT_LINE_RE = re.compile(r"^[\s*_(]*margin is (\w+) sentences?[\s.*_)]*$", re.I)
LEFTOVER_COUNT_RE = re.compile(r"\bmargin is\b[^\n]*\bsentences?\b", re.I)
COUNT_WORDS = {w: n for n, w in enumerate(
    ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine"], 1)}
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
HOOKY_PHRASES = ["little did", "what happened next", "unbeknownst", "before long"]


class ParseError(ValueError):
    pass


@dataclass(frozen=True)
class Draft:
    margin: str
    target: str
    body: str  # starts with the target sentence, exactly once
    margin_sentences: int

    @property
    def narration(self) -> str:
        return f"{self.margin} {self.body}"


def _normalize(s: str) -> str:
    s = s.translate(str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"}))
    return " ".join(s.split())


def _between(text: str, start: str, end: str) -> str:
    inner = text[text.index(start) + len(start):text.index(end)].strip()
    if not inner:
        raise ParseError(f"empty content between {start} and {end}")
    return inner


def parse_draft(text: str) -> Draft:
    for m in MARKERS:
        if text.count(m) != 1:
            raise ParseError(f"{m} appears {text.count(m)} times, expected once")
    positions = [text.index(m) for m in MARKERS]
    if positions != sorted(positions):
        raise ParseError("delimiters out of order")
    margin = _between(text, MARKERS[0], MARKERS[1])
    target = _between(text, MARKERS[2], MARKERS[3])

    count, kept = None, []
    for line in text[positions[3] + len(MARKERS[3]):].split("\n"):
        m = COUNT_LINE_RE.match(line)
        if m:
            n = int(m[1]) if m[1].isdigit() else COUNT_WORDS.get(m[1].lower())
            if n is None or n > 9:
                raise ParseError(f"implausible margin length: {line.strip()!r}")
            count = count if count is not None else n
        else:
            kept.append(line)
    raw_body = "\n".join(kept).strip()
    if LEFTOVER_COUNT_RE.search(raw_body):
        raise ParseError("margin-count text left in the narration body")
    # Some outputs restate the margin before the target; drop that copy.
    k = len(margin.split())
    parts = raw_body.split(None, k)
    if len(parts) >= k and _normalize(" ".join(parts[:k])) == _normalize(margin):
        raw_body = parts[k] if len(parts) > k else ""
    if count is None:
        count = len(re.findall(r"[.!?](?=\s|$)", margin)) or 1

    nbody, ntarget = _normalize(raw_body), _normalize(target).strip("\"'")
    if nbody.lstrip("\"'").startswith(ntarget):
        body = raw_body
    elif ntarget in nbody:
        raise ParseError("target sentence appears mid-body, not right after the margin")
    else:
        body = f"{target} {raw_body}".strip()
    return Draft(margin, target, body, count)


def parse_repair(text: str) -> str:
    for m in MARKERS[:2]:
        if text.count(m) != 1:
            raise ParseError(f"{m} appears {text.count(m)} times, expected once")
    if text.index(MARKERS[0]) > text.index(MARKERS[1]):
        raise ParseError("delimiters out of order")
    return _between(text, MARKERS[0], MARKERS[1])


def check_margin(margin: str) -> str | None:
    """D9: return why the margin reads hook-flavoured, or None if it is plain."""
    if "?" in margin:
        return "question mark"
    low = margin.lower()
    for phrase in HOOKY_PHRASES:
        if phrase in low:
            return f"phrase '{phrase}'"
    sentences = [s for s in SENTENCE_SPLIT_RE.split(margin.strip()) if s]
    for s in sentences:
        m = re.match(r"(But|And)\b", s)
        if m:
            return f"sentence starts with '{m[1]}'"
    avg = sum(len(s.split()) for s in sentences) / len(sentences)
    if avg > 25:
        return f"average sentence length {avg:g} > 25 words"
    return None


def parse_module(text: str) -> str:
    letter = text.strip().rstrip(".").strip().upper()
    if letter not in ("A", "B", "C", "D"):
        raise ParseError(f"expected one of A, B, C, D; got {text.strip()[:40]!r}")
    return letter
