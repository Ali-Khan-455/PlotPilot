"""Load prompts/image-sync-v3.md by structure: the stage prompts, the locked style suffix, the sub-style
and colour texts, and the tool output contracts. Every piece fails closed if the spec's structure changes;
only structural markers live in code, never the text itself."""

import re
from dataclasses import dataclass
from pathlib import Path

from imagesync import config
from plotpilot.prompts import Prompt, load_prompts, load_section

STAGES = ("STAGE 0", "STAGE 1", "STAGE 2")
STYLE = "MANHWA STYLE SPECIFICATION"
CONTRACTS = "TOOL OUTPUT CONTRACTS (appendix)"
SUB_STYLE_RE = re.compile(r"^- \*\*\(([a-z])\) ([^*]+)\*\* — (.+)$")
COLOR_RE = re.compile(r"^- ([^:]+): (.+)$")


class SpecError(ValueError):
    pass


@dataclass(frozen=True)
class Spec:
    stages: dict[str, Prompt]
    suffix: str
    sub_styles: dict[str, str]   # letter → "Name — description"
    colors: dict[str, str]       # genre label → colour treatment
    contracts: str


def _suffix(style: str) -> str:
    lines = style.split("\n")
    at = next((i for i, ln in enumerate(lines) if ln.startswith("**Locked style suffix")), None)
    if at is None:
        raise SpecError(f"'{STYLE}' has no '**Locked style suffix' line")
    fence = [i for i in range(at + 1, len(lines)) if lines[i].strip() == "```"][:2]
    if len(fence) != 2 or any(ln.strip() for ln in lines[at + 1:fence[0]] if not ln.startswith("Replace")):
        raise SpecError("the locked style suffix must be the first fenced block after its heading line")
    body = "\n".join(lines[fence[0] + 1:fence[1]]).strip()
    if not body or "\n" in body:
        raise SpecError("the locked style suffix block must hold exactly one line")
    return body


def _sub_styles(style: str) -> dict[str, str]:
    found = {m[1]: f"{m[2]} — {m[3]}" for m in map(SUB_STYLE_RE.match, style.split("\n")) if m}
    if set(found) != {"a", "b", "c", "d"}:
        raise SpecError(f"expected sub-style bullets (a)–(d), found {sorted(found)}")
    return found


def _colors(style: str) -> dict[str, str]:
    lines = style.split("\n")
    at = next((i for i, ln in enumerate(lines) if ln.startswith("**Color, per chunk")), None)
    if at is None:
        raise SpecError(f"'{STYLE}' has no '**Color, per chunk' line")
    colors = {}
    for ln in lines[at + 1:]:
        m = COLOR_RE.match(ln)
        if not m:
            break
        colors[m[1].strip()] = m[2].strip()
    missing = [label for label in config.MODULE_TO_COLOR.values() if label not in colors]
    if missing:
        raise SpecError(f"colour treatment(s) missing from the spec: {', '.join(missing)}")
    return colors


def load_spec(path: Path = config.SPEC_PATH) -> Spec:
    """Checks that config.MODULE_TO_COLOR's labels exist in the spec. That guards IS-4's later lookup
    colors[MODULE_TO_COLOR[bible genre_color_default]] (the Bible stores the module letter); it does not
    validate what the Bible stores."""
    stages = load_prompts(path, heading_re=r"^## (STAGE \d) — ", key=lambda m: m[1], required=STAGES)
    style = load_section(path, STYLE)
    return Spec({k: stages[k] for k in STAGES}, _suffix(style), _sub_styles(style), _colors(style),
                load_section(path, CONTRACTS))
