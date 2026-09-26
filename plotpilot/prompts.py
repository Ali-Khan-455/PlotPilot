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


def _v4_key(m) -> str:
    return m[1] if m[1] else f"MODULE {m[2]}"


def load_prompts(path: Path = config.SPEC_PATH, heading_re=HEADING_RE, key=_v4_key,
                 required=()) -> dict[str, Prompt]:
    """Prompt text between each matching heading's COPY and END lines. `required` keys that are missing,
    including a section without COPY/END markers, raise instead of being skipped (fail closed)."""
    heading_re = re.compile(heading_re) if isinstance(heading_re, str) else heading_re
    lines = Path(path).read_text(encoding="utf-8").split("\n")
    starts = [i for i, ln in enumerate(lines) if ln.startswith(("## ", "### "))] + [len(lines)]
    prompts = {}
    for i, nxt in zip(starts, starts[1:]):
        m = heading_re.match(lines[i])
        if not m:
            continue
        section = lines[i + 1:nxt]
        begin = next((j for j, ln in enumerate(section) if ln.startswith("**COPY EVERYTHING BELOW")), None)
        if begin is None:
            continue  # e.g. the PROMPT 2 intro; its text lives in the modules
        end = next((j for j in range(begin + 1, len(section)) if section[j].startswith("**END OF ")), None)
        if end is None:
            raise ValueError(f"Spec section '{lines[i].lstrip('#').strip()}' has a COPY line but no **END OF line.")
        prompts[key(m)] = Prompt(lines[i].lstrip("#").strip(), "\n".join(section[begin + 1:end]).strip())
    for k in required:
        if not prompts.get(k) or not prompts[k].text:
            raise ValueError(f"Spec {Path(path).name} is missing required section '{k}' "
                             "(heading, COPY and END lines).")
    return prompts


def load_section(path: Path, heading: str) -> str:
    """Raw text under the exact `## heading` line, up to the next `## ` heading."""
    lines = Path(path).read_text(encoding="utf-8").split("\n")
    try:
        start = lines.index(f"## {heading}")
    except ValueError:
        raise ValueError(f"Spec {Path(path).name} has no '## {heading}' section.") from None
    end = next((j for j in range(start + 1, len(lines)) if lines[j].startswith("## ")), len(lines))
    return "\n".join(lines[start + 1:end]).strip()


def fill(template: str, values: dict[str, str]) -> str:
    for placeholder, value in values.items():
        if placeholder not in template:
            raise KeyError(f"placeholder not in prompt: {placeholder}")
        template = template.replace(placeholder, value)
    return template
