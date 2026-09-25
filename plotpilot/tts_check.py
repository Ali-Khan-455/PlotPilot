"""Deterministic TTS hazard check (R10). Warns only; never gates."""

import re

HAZARD_RE = re.compile(r"(?P<digit>\d+)|(?P<semicolon>;)|(?P<em_dash>—)|(?P<en_dash>–)"
                       r"|(?P<ellipsis>\.\.\.|…)|(?P<parenthesis>\([^)\n]*\)?|\))")
HINT_RE = re.compile(r"\[[^\]\n]*\]")  # phonetic hints like [rhymes with 'kale'] are allowed


def tts_hazards(text: str) -> list[tuple[int, str, str]]:
    found = []
    for n, line in enumerate(text.split("\n"), 1):
        scan = HINT_RE.sub(lambda m: " " * len(m[0]), line)
        for m in HAZARD_RE.finditer(scan):
            kind = m.lastgroup.replace("_", " ")
            found.append((n, kind, line[max(0, m.start() - 15):m.end() + 15]))
    return found
