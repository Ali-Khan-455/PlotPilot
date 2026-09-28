"""Stage 2 batch output: validation against beat identity, prompt composition, and the mechanical QA
checks (shot cadence, reference confirmation, 9:16 wide-shot awareness). Pure — no I/O, no database.

check_refs and check_shot_cadence are the two checks that gate a retry (via run_stage2's own `_qa`);
check_wide_under_9_16 is always warn-only, since v3 allows a 9:16 wide shot when the source explicitly
depicts a landscape (image-sync-v3.md:313) — a judgment call code can't make from a shot_type string
alone."""

from imagesync import bible, target
from plotpilot.parse import ParseError

STAGE2_KEYS = {"timecode", "scene", "shot_type", "refs_used", "genre_override"}
GENRE_LETTERS = ("A", "B", "C", "D")
WIDE_GAP_LIMIT = 7
SAME_SHOT_RUN_LIMIT = 3


def _strip_ref(s: str) -> str:
    return s[1:] if s and s[0] in "#@" else s


def _need(cond, msg):
    if not cond:
        raise ParseError(msg)


def validate_stage2(obj, batch: list) -> dict:
    """Exact {"prompts"} top-level key, one item per beat in `batch` (in order), each item's timecode
    (leading '#' tolerated) matching that beat's own display timecode. `timecode`/`refs_used` entries are
    stripped of a leading '#'/'@' in place — code has the final say on the canonical spelling, resolved
    later via target.canonical_tag."""
    _need(isinstance(obj, dict) and set(obj) == {"prompts"}, "Stage 2 output needs exactly {'prompts'}")
    prompts = obj["prompts"]
    _need(isinstance(prompts, list), "'prompts' must be a list")
    _need(len(prompts) == len(batch),
         f"'prompts' has {len(prompts)} item(s), expected {len(batch)} (one per beat)")
    for i, (item, beat) in enumerate(zip(prompts, batch)):
        _need(isinstance(item, dict) and set(item) == STAGE2_KEYS,
             f"prompts[{i}] needs exactly {sorted(STAGE2_KEYS)}")
        for key in ("timecode", "scene", "shot_type"):
            _need(isinstance(item[key], str) and item[key].strip(), f"prompts[{i}].{key} must be a non-empty string")
        refs = item["refs_used"]
        _need(isinstance(refs, list) and all(isinstance(r, str) for r in refs),
             f"prompts[{i}].refs_used must be a list of strings")
        item["timecode"] = _strip_ref(item["timecode"])
        expected = beat.timecode + beat.suffix
        _need(item["timecode"] == expected,
             f"prompts[{i}].timecode {item['timecode']!r} doesn't match beat {expected!r}")
        item["refs_used"] = [_strip_ref(r) for r in refs]
        genre = item["genre_override"]
        _need(genre is None or genre in GENRE_LETTERS, f"prompts[{i}].genre_override must be A/B/C/D or null")
    return obj


def _is_wide(shot_type: str) -> bool:
    return "wide" in shot_type.lower()


def compose_prompt(spec, current_bible, chunk, item: dict) -> str:
    module = item["genre_override"] or chunk.module
    parts = [item["shot_type"], item["scene"], target.format_refs(current_bible, item["refs_used"])]
    parts = [p for p in parts if p]
    parts.append(bible._render_suffix(spec, current_bible, module))
    return ", ".join(parts)


def check_refs(current_bible, items: list[dict]) -> list[str]:
    findings = []
    for item in items:
        for tag in item["refs_used"]:
            if not target.is_confirmed(current_bible, tag):
                findings.append(f"#{tag} is not a confirmed reference (beat #{item['timecode']}).")
    return findings


def check_shot_cadence(items: list[dict], aspect: str, *, previous_tail: list[str] = ()) -> list[str]:
    findings = []
    shot_types = [it["shot_type"] for it in items]
    full = list(previous_tail) + shot_types
    n_prev = len(previous_tail)
    i = 0
    while i < len(full):
        j = i
        while j + 1 < len(full) and full[j + 1] == full[i]:
            j += 1
        run_len = j - i + 1
        if run_len > SAME_SHOT_RUN_LIMIT and j >= n_prev:
            end_idx = j - n_prev
            findings.append(f"same shot type '{full[i]}' used {run_len} times in a row, "
                            f"ending at beat #{items[end_idx]['timecode']}.")
        i = j + 1

    if aspect != "9:16":
        last_wide_idx = None
        for idx, st in enumerate(previous_tail):
            if _is_wide(st):
                last_wide_idx = idx
        since_wide = (n_prev - 1 - last_wide_idx) if last_wide_idx is not None else n_prev
        already_reported = since_wide > WIDE_GAP_LIMIT
        for it in items:
            since_wide += 1
            if _is_wide(it["shot_type"]):
                since_wide = 0
                already_reported = False
                continue
            if since_wide > WIDE_GAP_LIMIT and not already_reported:
                findings.append(f"no wide shot for {since_wide} beats, ending at beat #{it['timecode']}.")
                already_reported = True

    return findings


def check_wide_under_9_16(items: list[dict], aspect: str) -> list[str]:
    if aspect != "9:16":
        return []
    return [f"wide shot used under 9:16 (beat #{it['timecode']}); only acceptable for an explicit landscape."
           for it in items if _is_wide(it["shot_type"])]


def build_manifest_rows(entries: list[tuple[str, dict]]) -> list[tuple[str, str, str]]:
    return [(beat_timecode, item["shot_type"], " ".join(item["scene"].split()[:5])) for beat_timecode, item in entries]
