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
INPUT_MODES = "INPUT MODES"
VISUAL_BIBLE = "THE VISUAL BIBLE"
MODE_A_MARKER = "**Mode A — PlotPilot (preferred):**"
CONTINUITY_LOG_LINE = "CONTINUITY LOG (append-only)"
SUB_STYLE_RE = re.compile(r"^- \*\*\(([a-z])\) ([^*]+)\*\* — (.+)$")
COLOR_RE = re.compile(r"^- ([^:]+): (.+)$")
CONTRACT_MARKERS = ("**Stage 0 output:**", "**Stage 1 output:**", "**Stage 2 batch output:**",
                    "**Bible update (end-of-Stage-2 continuity call):**")
OVERRIDE_HEADING = "### How the model uses these contracts"


class SpecError(ValueError):
    pass


@dataclass(frozen=True)
class Contracts:
    override: str   # the "How the model uses these contracts" paragraph
    stage0: str      # "**Stage 0 output:**" through its Note line
    stage1: str
    stage2: str
    bible_update: str


@dataclass(frozen=True)
class Spec:
    stages: dict[str, Prompt]
    suffix: str
    sub_styles: dict[str, str]   # letter → "Name — description"
    colors: dict[str, str]       # genre label → colour treatment
    contracts: Contracts
    mode_a_line: str
    continuity_log_header: str


def _split_contracts(text: str) -> Contracts:
    at = text.find(OVERRIDE_HEADING)
    if at == -1:
        raise SpecError(f"'{CONTRACTS}' has no {OVERRIDE_HEADING!r} subheading")
    positions = []
    cursor = at + len(OVERRIDE_HEADING)
    for marker in CONTRACT_MARKERS:
        idx = text.find(marker, cursor)
        if idx == -1 or idx < cursor:
            raise SpecError(f"'{CONTRACTS}' is missing marker {marker!r} in order")
        positions.append(idx)
        cursor = idx + len(marker)
    override = text[at + len(OVERRIDE_HEADING):positions[0]].strip()
    stage0 = text[positions[0]:positions[1]].strip()
    stage1 = text[positions[1]:positions[2]].strip()
    stage2 = text[positions[2]:positions[3]].strip()
    bible_update = text[positions[3]:].strip()
    return Contracts(override, stage0, stage1, stage2, bible_update)


def _first_fence_after(text: str, marker: str, *, allow_prefix: str | None = None) -> str:
    """The single-line body of the first ``` fence right after the first line starting with `marker`.
    Any non-blank line between the marker and the fence must start with `allow_prefix` when given."""
    lines = text.split("\n")
    at = next((i for i, ln in enumerate(lines) if ln.startswith(marker)), None)
    if at is None:
        raise SpecError(f"expected a line starting with {marker!r}")
    fence = [i for i in range(at + 1, len(lines)) if lines[i].strip() == "```"][:2]
    gap_ok = len(fence) == 2 and not any(
        ln.strip() for ln in lines[at + 1:fence[0]] if not (allow_prefix and ln.startswith(allow_prefix)))
    if not gap_ok:
        raise SpecError(f"expected a fenced block right after the {marker!r} line")
    body = "\n".join(lines[fence[0] + 1:fence[1]]).strip()
    if not body or "\n" in body:
        raise SpecError(f"the fenced block after {marker!r} must hold exactly one line")
    return body


def _suffix(style: str) -> str:
    return _first_fence_after(style, "**Locked style suffix", allow_prefix="Replace")


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


def _continuity_log_header(bible_section: str) -> str:
    if CONTINUITY_LOG_LINE not in bible_section.split("\n"):
        raise SpecError(f"'{VISUAL_BIBLE}' has no {CONTINUITY_LOG_LINE!r} line")
    return CONTINUITY_LOG_LINE


def load_spec(path: Path = config.SPEC_PATH) -> Spec:
    """Checks that config.MODULE_TO_COLOR's labels exist in the spec. That guards IS-4's later lookup
    colors[MODULE_TO_COLOR[bible genre_color_default]] (the Bible stores the module letter); it does not
    validate what the Bible stores."""
    stages = load_prompts(path, heading_re=r"^## (STAGE \d) — ", key=lambda m: m[1], required=STAGES)
    style = load_section(path, STYLE)
    contracts = _split_contracts(load_section(path, CONTRACTS))
    mode_a_line = _first_fence_after(load_section(path, INPUT_MODES), MODE_A_MARKER)
    continuity_log_header = _continuity_log_header(load_section(path, VISUAL_BIBLE))
    return Spec({k: stages[k] for k in STAGES}, _suffix(style), _sub_styles(style), _colors(style),
                contracts, mode_a_line, continuity_log_header)
