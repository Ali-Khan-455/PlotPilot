"""Stage 0 output: beat identity, validation, cadence warnings, and the revision fold. Pure — no I/O.

Beat identity is (scene_index, suffix), not the raw timecode string: PlotPilot's own scene list can
repeat a timecode when a scene sentence isn't found (Q9), so two beats can share the same timecode text
while meaning two different scenes. `_assign_scenes` resolves each beat to a scene_index by walking the
chunk's scene list in order; two invariants make that walk correct:

1. A new group's match must be searched STRICTLY PAST the previous group's index, never merely "at or
   after" it. "At or after" lets two different groups both match the SAME chunk_scenes index when a
   timecode repeats (Q9) — the second group silently collides with the first instead of advancing to the
   next occurrence, and an illegal suffix restart (a, b, a) silently re-uses the first occurrence's index
   instead of raising.
2. An unlettered ('') beat is always a singleton: it can never be continued by anything, lettered or not,
   same base or not, and it can never continue anything itself. v3 only ever gives a SPLIT scene's beats
   letter suffixes, so an unlettered beat is by definition one complete, unsplit occurrence. Without this
   rule, an unsplit first occurrence of a repeated timecode (Q9) would wrongly absorb a fully split second
   occurrence into its own one-beat group.
"""

import re
from dataclasses import dataclass

from plotpilot.parse import ParseError
from plotpilot.tracker import extract_json

__all__ = ["ParseError", "extract_json", "Beat", "TIMECODE_RE", "REVISE_RE",
           "validate_fresh_beats", "validate_revision", "cadence_warnings", "fold"]

TIMECODE_RE = re.compile(r"^(\d{2,}-\d{2})([a-z]?)$")
REVISE_RE = re.compile(r"^(\d{2,}-\d{2})(?:_(\d+))?([a-z]?)$")

CADENCE_LOW, CADENCE_HIGH, CADENCE_RUN = 3, 20, 3


@dataclass(frozen=True)
class Beat:
    scene_index: int
    suffix: str                      # '', 'a', 'b', ...
    timecode: str                    # the base "mm-ss", cached for display/cadence/--revise-beat
    narration: str
    detail: list[tuple[str, str]]
    continues: str | None            # resolved display timecode; set only possibly on the chunk's first beat


def _strip_hash(s: str) -> str:
    return s[1:] if s.startswith("#") else s


@dataclass(frozen=True)
class _RawBeat:
    base: str
    suffix: str
    narration: str
    detail: list[tuple[str, str]]
    continues_raw: str | None


def _parse_beat_item(item) -> _RawBeat:
    if not isinstance(item, dict) or set(item) - {"continues"} != {"timecode", "narration", "detail"}:
        raise ParseError("a beat item needs 'timecode', 'narration', 'detail' and optionally 'continues'")
    timecode = item["timecode"]
    if not isinstance(timecode, str):
        raise ParseError("'timecode' must be a string")
    m = TIMECODE_RE.match(_strip_hash(timecode))
    if not m:
        raise ParseError(f"invalid timecode format: {timecode!r}")
    narration = item["narration"]
    if not isinstance(narration, str) or not narration.strip():
        raise ParseError("'narration' must be a non-empty string")
    detail_raw = item["detail"]
    if not isinstance(detail_raw, list):
        raise ParseError("'detail' must be a list")
    detail = []
    for d in detail_raw:
        if (not isinstance(d, dict) or set(d) != {"clause", "chapter"}
                or not isinstance(d["clause"], str) or not d["clause"].strip()
                or not isinstance(d["chapter"], str) or not d["chapter"].strip()):
            raise ParseError("each 'detail' item needs non-empty 'clause' and 'chapter'")
        detail.append((d["clause"], d["chapter"]))
    continues_raw = item.get("continues")
    if continues_raw is not None:
        if not isinstance(continues_raw, str):
            raise ParseError("'continues' must be a string or null")
        continues_raw = _strip_hash(continues_raw)
    return _RawBeat(m[1], m[2], narration, detail, continues_raw)


def _assign_scenes(items: list[_RawBeat], chunk_scenes: list[tuple[str, str]]) -> list[_RawBeat]:
    """Resolves each raw beat's (base, suffix) to a scene_index, returning items unchanged in content but
    validated for identity. See the module docstring for the two invariants this walk relies on."""
    last_matched = -1
    last_suffix = None
    last_scene_base = None
    assigned = []
    for item in items:
        suffix_idx = 0 if item.suffix == "" else (ord(item.suffix) - ord("a") + 1)
        last_suffix_idx = (None if last_suffix is None
                           else (0 if last_suffix == "" else (ord(last_suffix) - ord("a") + 1)))
        continues_group = (last_suffix is not None and last_suffix != ""
                          and last_scene_base == item.base and suffix_idx == last_suffix_idx + 1)
        if continues_group:
            scene_index = last_matched
        else:
            if suffix_idx not in (0, 1):
                raise ParseError(f"beat {item.base}{item.suffix} starts a new scene but doesn't begin at "
                                 "the base timecode or 'a'")
            found = next((i for i in range(last_matched + 1, len(chunk_scenes))
                         if chunk_scenes[i][0] == item.base), None)
            if found is None:
                raise ParseError(f"beat timecode {item.base} is not a scene in this chunk "
                                 "(never-invented-timestamp rule)")
            last_matched = found
            scene_index = found
        assigned.append((scene_index, item))
        last_suffix = item.suffix
        last_scene_base = item.base
    return assigned


def _check_invariants(assigned, *, has_continuity: bool, previous_last_ref: str | None) -> list[Beat]:
    beats = []
    for i, (scene_index, item) in enumerate(assigned):
        if item.continues_raw is not None and i != 0:
            raise ParseError("'continues' is set on a non-first beat")
        continues = None
        if i == 0 and item.continues_raw is not None:
            if not has_continuity:
                raise ParseError("'continues' is set but no continuity entry was shown to the model")
            continues = previous_last_ref
        beats.append(Beat(scene_index, item.suffix, item.base, item.narration, item.detail, continues))
    return beats


def validate_fresh_beats(obj, *, chunk_scenes, has_continuity: bool,
                         previous_last_ref: str | None) -> list[Beat]:
    if not isinstance(obj, dict) or set(obj) != {"beats"}:
        raise ParseError("output needs exactly {'beats'}")
    if not isinstance(obj["beats"], list) or not obj["beats"]:
        raise ParseError("'beats' must be a non-empty list")
    items = [_parse_beat_item(b) for b in obj["beats"]]
    assigned = _assign_scenes(items, chunk_scenes)
    return _check_invariants(assigned, has_continuity=has_continuity, previous_last_ref=previous_last_ref)


def validate_revision(obj, *, target: Beat, has_continuity: bool, previous_last_ref: str | None,
                      is_first_beat: bool) -> Beat:
    if not isinstance(obj, dict) or set(obj) != {"beats"}:
        raise ParseError("output needs exactly {'beats'}")
    if not isinstance(obj["beats"], list) or len(obj["beats"]) != 1:
        raise ParseError("a revision must return exactly one beat")
    item = _parse_beat_item(obj["beats"][0])
    if (item.base, item.suffix) != (target.timecode, target.suffix):
        raise ParseError(f"revision changed the beat's timecode ({item.base}{item.suffix} != "
                         f"{target.timecode}{target.suffix})")
    assigned = [(target.scene_index, item)]
    checked = _check_invariants(assigned, has_continuity=has_continuity if is_first_beat else False,
                                previous_last_ref=previous_last_ref if is_first_beat else None)
    return checked[0]


def cadence_warnings(beats: list[Beat], scene_seconds: list[int], boundary_seconds: int) -> list[str]:
    if not beats:
        return []
    # Group consecutive beats sharing one scene_index.
    groups = []  # list of (scene_index, [beats])
    for b in beats:
        if groups and groups[-1][0] == b.scene_index:
            groups[-1][1].append(b)
        else:
            groups.append((b.scene_index, [b]))
    per_beat = []  # list of (Beat, duration|None)
    for gi, (scene_index, group_beats) in enumerate(groups):
        next_scene_index = groups[gi + 1][0] if gi + 1 < len(groups) else None
        end = scene_seconds[next_scene_index] if next_scene_index is not None else boundary_seconds
        duration = end - scene_seconds[scene_index]
        if duration <= 0:
            per_beat.extend((b, None) for b in group_beats)  # uninformative (Q9 zero-duration scene)
        else:
            each = duration / len(group_beats)
            per_beat.extend((b, each) for b in group_beats)
    warnings = []
    run_start, run_len = None, 0
    for i, (b, d) in enumerate(per_beat):
        out_of_band = d is not None and not (CADENCE_LOW <= d <= CADENCE_HIGH)
        if out_of_band:
            if run_len == 0:
                run_start = b
            run_len += 1
        else:
            if run_len > CADENCE_RUN:
                warnings.append(f"WARNING: cadence — {run_len} consecutive beats outside the "
                                f"{CADENCE_LOW}-{CADENCE_HIGH}s target starting at #{run_start.timecode}"
                                f"{run_start.suffix}.")
            run_start, run_len = None, 0
    if run_len > CADENCE_RUN:
        warnings.append(f"WARNING: cadence — {run_len} consecutive beats outside the "
                        f"{CADENCE_LOW}-{CADENCE_HIGH}s target starting at #{run_start.timecode}"
                        f"{run_start.suffix}.")
    return warnings


def fold(base: list[Beat], revisions: list[Beat]) -> list[Beat]:
    by_id = {(b.scene_index, b.suffix): b for b in base}
    for r in revisions:
        key = (r.scene_index, r.suffix)
        if key not in by_id:
            raise ParseError(f"revision for scene_index={r.scene_index!r} suffix={r.suffix!r} "
                             "doesn't match any stored beat")
        by_id[key] = r
    return [by_id[(b.scene_index, b.suffix)] for b in base]
