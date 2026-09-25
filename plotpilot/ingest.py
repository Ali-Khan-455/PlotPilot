"""Pure text parsing and chunk planning. No I/O."""

import re

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
