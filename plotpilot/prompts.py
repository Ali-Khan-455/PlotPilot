"""Load prompt text from the spec by heading (never by line number) and fill placeholders."""

import re
from dataclasses import dataclass
from pathlib import Path

from plotpilot import config

HEADING_RE = re.compile(r"^## PROMPT (\d+(?:-[A-Z]+)?) — |^### MODULE ([A-Z]) — ")


@dataclass(frozen=True)
class Prompt:
    heading: str
    text: str


def load_prompts(path: Path = config.SPEC_PATH) -> dict[str, Prompt]:
    lines = Path(path).read_text(encoding="utf-8").split("\n")
    starts = [i for i, ln in enumerate(lines) if ln.startswith(("## ", "### "))] + [len(lines)]
    prompts = {}
    for i, nxt in zip(starts, starts[1:]):
        m = HEADING_RE.match(lines[i])
        if not m:
            continue
        section = lines[i + 1:nxt]
        begin = next((j for j, ln in enumerate(section) if ln.startswith("**COPY EVERYTHING BELOW")), None)
        if begin is None:
            continue  # e.g. the PROMPT 2 intro; its text lives in the modules
        end = next((j for j in range(begin + 1, len(section)) if section[j].startswith("**END OF ")), None)
        if end is None:
            raise ValueError(f"Spec section '{lines[i].lstrip('#').strip()}' has a COPY line but no **END OF line.")
        key = m[1] if m[1] else f"MODULE {m[2]}"
        prompts[key] = Prompt(lines[i].lstrip("#").strip(), "\n".join(section[begin + 1:end]).strip())
    return prompts


def fill(template: str, values: dict[str, str]) -> str:
    for placeholder, value in values.items():
        if placeholder not in template:
            raise KeyError(f"placeholder not in prompt: {placeholder}")
        template = template.replace(placeholder, value)
    return template
