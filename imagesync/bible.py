"""The Visual Bible: schema, Stage 1 delta validation, deterministic merge, and rendering (pure).
Mirrors plotpilot/tracker.py's shape: a validated delta merges deterministically into the running
document. `merge`'s `replace_tags` parameter is the one addition tracker.py has no analog for — it is
how --regenerate's own code-verified target(s) may replace an existing entry; an ordinary Stage 1
delta may only ever append."""

import copy
import re

from imagesync import config
from imagesync.spec import SpecError
from plotpilot.parse import ParseError

TAG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")
TYPE_TO_CATEGORY = {"character": "characters", "location": "locations", "object": "objects"}
DEFAULT_CURRENT_STATE = {"character": "clean", "location": "intact", "object": "present"}
SLOTTED_CATEGORIES = ("characters", "objects")
SLOT_CAPS = {"characters": 5, "objects": 14}


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def _need(cond, msg):
    if not cond:
        raise ParseError(msg)


def _strip_hash(s: str) -> str:
    return s[1:] if s[:1] in ("#", "@") else s


def validate_stage1(obj) -> dict:
    """Stage 1's {"new_references", "bible_update"} contract: exact keys, a bijective cross-check
    between new_references and bible_update (both directions), tag format, and _norm-uniqueness within
    this one delta. Pure — knows nothing about an existing Bible."""
    _need(isinstance(obj, dict) and set(obj) == {"new_references", "bible_update"},
          "Stage 1 output needs exactly {'new_references', 'bible_update'}")
    refs = obj["new_references"]
    _need(isinstance(refs, list), "'new_references' must be a list")
    bu = obj["bible_update"]
    _need(isinstance(bu, dict) and set(bu) == {"characters", "locations", "objects"},
          "'bible_update' needs exactly {'characters', 'locations', 'objects'}")

    seen_tags = set()
    by_tag = {}
    for r in refs:
        _need(isinstance(r, dict) and set(r) == {"type", "tag", "descriptor"},
              "each 'new_references' item needs exactly {'type', 'tag', 'descriptor'}")
        _need(r["type"] in TYPE_TO_CATEGORY, f"unknown reference type {r['type']!r}")
        _need(isinstance(r["tag"], str) and TAG_RE.match(r["tag"]), f"bad tag format: {r['tag']!r}")
        norm = _norm(r["tag"])
        _need(norm not in seen_tags, f"tag {r['tag']!r} repeats (case/space-insensitively) in this delta")
        seen_tags.add(norm)
        by_tag[norm] = r

    matched = set()
    for category in ("characters", "locations", "objects"):
        want_type = next(t for t, c in TYPE_TO_CATEGORY.items() if c == category)
        for item in bu[category]:
            _need(isinstance(item, dict) and {"name", "tag", "descriptor"} <= set(item)
                  and set(item) <= {"name", "tag", "descriptor", "current_state"},
                  f"'bible_update.{category}' items need name/tag/descriptor (+ optional current_state)")
            norm = _norm(item["tag"])
            r = by_tag.get(norm)
            _need(r is not None, f"'bible_update.{category}' tag {item['tag']!r} has no new_references entry")
            _need(TYPE_TO_CATEGORY[r["type"]] == category,
                  f"tag {item['tag']!r} is type {r['type']!r}, filed under {category!r}")
            _need(norm not in matched, f"tag {item['tag']!r} appears twice across bible_update categories")
            matched.add(norm)
            if "current_state" not in item or not item["current_state"]:
                item["current_state"] = DEFAULT_CURRENT_STATE[want_type]

    _need(matched == seen_tags, "'new_references' and 'bible_update' don't match one-to-one")
    return obj


def check_no_collision(delta: dict, current_bible: dict) -> None:
    """Raises if any proposed tag _norm-matches an existing tag anywhere in the Bible. Called only on
    the ordinary (non-regenerate) Stage 1 path."""
    existing = {_norm(item["tag"]) for category in ("characters", "locations", "objects")
                for item in current_bible[category]}
    for r in delta["new_references"]:
        if _norm(r["tag"]) in existing:
            raise ParseError(f"tag {r['tag']!r} already exists in the Visual Bible")


def validate_continuity(obj, current_bible: dict, beats) -> dict:
    """The end-of-Stage-2 continuity call's {"continuity_log_entries"} contract: exact top-level key, a
    list (possibly empty) of dicts each needing exactly {"beat","element","from","to","reason"}, all
    five required to be non-empty strings. `element` (leading '#'/'@' stripped, _norm-compared) must
    resolve to an existing Bible tag; `beat` (leading '#'/'@' stripped) must be one of this chunk's own
    beats' display timecodes. Does NOT check that `element` actually appears in that specific beat's own
    narration -- v3's own prompt text already forbids an unsupported state change, but this validator
    doesn't independently enforce it (matches check_refs's own similarly-scoped deferral)."""
    _need(isinstance(obj, dict) and set(obj) == {"continuity_log_entries"},
         "Bible update output needs exactly {'continuity_log_entries'}")
    entries = obj["continuity_log_entries"]
    _need(isinstance(entries, list), "'continuity_log_entries' must be a list")
    existing_tags = {_norm(row["tag"]) for category in ("characters", "locations", "objects")
                     for row in current_bible[category]}
    beat_timecodes = {b.timecode + b.suffix for b in beats}
    for i, e in enumerate(entries):
        _need(isinstance(e, dict) and set(e) == {"beat", "element", "from", "to", "reason"},
             f"continuity_log_entries[{i}] needs exactly beat/element/from/to/reason")
        for key in ("beat", "element", "from", "to", "reason"):
            _need(isinstance(e[key], str) and e[key].strip(),
                 f"continuity_log_entries[{i}].{key} must be a non-empty string")
        e["element"] = _strip_hash(e["element"])
        e["beat"] = _strip_hash(e["beat"])
        _need(_norm(e["element"]) in existing_tags,
             f"continuity_log_entries[{i}].element {e['element']!r} isn't a known Bible tag")
        _need(e["beat"] in beat_timecodes,
             f"continuity_log_entries[{i}].beat {e['beat']!r} isn't a beat in this chunk")
    return obj


def merge(bible: dict, delta: dict, chunk_idx: int, *, replace_tags: frozenset[str] = frozenset()) -> dict:
    """Deterministic merge, in `new_references` array order (slots are assigned from that order, per
    the spec's own note). A tag in `replace_tags` replaces the matching-category existing row in place,
    keeping its `slot`/`first_appeared_chunk`; every other tag appends as new, defensively re-raising
    on an unexpected collision (a stale or edited pending file). Never mutates an existing row's `slot`
    or `first_appeared_chunk`, never removes a row."""
    b = copy.deepcopy(bible)
    replace_norm = {_norm(t) for t in replace_tags}
    for r in delta["new_references"]:
        category = TYPE_TO_CATEGORY[r["type"]]
        item = next(i for i in delta["bible_update"][category] if _norm(i["tag"]) == _norm(r["tag"]))
        norm_tag = _norm(r["tag"])
        if norm_tag in replace_norm:
            existing = next((row for row in b[category] if _norm(row["tag"]) == norm_tag), None)
            if existing is None:
                raise ParseError(f"replace_tags target {r['tag']!r} not found in {category!r}")
            # --regenerate only fixes a reference's appearance, never its story-state (the continuity
            # log, via merge_continuity, is the sole authority for current_state) -- so a replace here
            # never touches current_state, slot, or first_appeared_chunk.
            old_name, old_descriptor = existing["name"], existing["descriptor"]
            existing["name"] = item["name"]
            existing["descriptor"] = item["descriptor"]
            if old_name != item["name"] or old_descriptor != item["descriptor"]:
                # existing["tag"] (the Bible's own canonical spelling), not r["tag"] (the delta's own
                # spelling) -- matching merge_continuity's own precedent of always logging the Bible's
                # canonical form. No beat identity is available at merge time (a regenerate isn't
                # beat-scoped), so this omits the "| beat M-SS" segment merge_continuity's lines carry.
                b["revision_log"].append(
                    f"[chunk {chunk_idx}] #{existing['tag']} {old_name} ({old_descriptor}) "
                    f"→ {item['name']} ({item['descriptor']})")
            continue
        all_tags = {_norm(row["tag"]) for cat in ("characters", "locations", "objects") for row in b[cat]}
        if norm_tag in all_tags:
            raise ParseError(f"tag {r['tag']!r} already exists in the Visual Bible")
        new_row = {"name": item["name"], "tag": r["tag"], "descriptor": item["descriptor"],
                  "current_state": item["current_state"], "reference_generated": True,
                  "first_appeared_chunk": chunk_idx}
        if category in SLOTTED_CATEGORIES:
            slots = b["slots"][category]
            cap = SLOT_CAPS[category]
            if len(slots) < cap:
                new_row["slot"] = len(slots) + 1
                slots.append(r["tag"])
            else:
                new_row["slot"] = "fallback"
        b[category].append(new_row)
    return b


def merge_continuity(bible: dict, entries: list[dict], chunk_idx: int) -> dict:
    """Appends one rendered continuity-log line per entry, in list order, and updates the matching
    tag's own row's `current_state` to that entry's `to` value (last entry in the list wins per tag,
    matching `merge`'s own append order for a fresh Stage 1 delta). Append-only: never edits or removes
    an existing log line, and never touches `slot`/`first_appeared_chunk`/`name`/`descriptor`. Because
    Stage 2's own message always sends the full Bible entries (which include `current_state`), this is
    what makes a later chunk's Stage 2 call automatically see the newest continuity state with no
    separate continuity-log data section needed."""
    b = copy.deepcopy(bible)
    for entry in entries:
        tag = None
        for category in ("characters", "locations", "objects"):
            row = next((r for r in b[category] if _norm(r["tag"]) == _norm(entry["element"])), None)
            if row is not None:
                tag = row["tag"]
                row["current_state"] = entry["to"]
                break
        line = (f"[chunk {chunk_idx} | beat {entry['beat']}] #{tag or entry['element']} "
               f"{entry['from']} → {entry['to']} {entry['reason']}")
        b["continuity_log"].append(line)
    return b


def _items(lines):
    return [f"- {x}" for x in lines] or ["- (none yet)"]


def _slot_names(b, category):
    tags = b["slots"][category]
    if not tags:
        return "-"
    by_tag = {row["tag"]: row["name"] for row in b[category]}
    return ", ".join(by_tag[t] for t in tags)


def _entries(rows, *, slotted):
    if not rows:
        return ["- (none yet)"]
    out = []
    for r in rows:
        gen = "yes" if r["reference_generated"] else "no"
        head = f"- {r['name']} | #{r['tag']} | "
        if slotted:
            head += f"slot: {r['slot']} | "
        head += f"reference generated: {gen} |"
        out.append(head)
        out.append(f"  locked descriptor: {r['descriptor']} |")
        out.append(f"  first appeared: chunk {r['first_appeared_chunk']} |")
        out.append(f"  current state: {r['current_state']}")
    return out


def render(bible: dict, spec, *, title: str) -> str:
    """The exact `=== VISUAL BIBLE — [title] ===` block from v3's THE VISUAL BIBLE section,
    field-for-field. Called only from the two places the Bible actually merges: run_stage1's
    empty-delta-merge branch and approve_refs."""
    sl = bible["style_lock"]
    sub_style_name = spec.sub_styles[sl["sub_style"]].split(" — ")[0]
    genre_label = config.MODULE_TO_COLOR[sl["genre_color_default"]]
    lines = [
        f"=== VISUAL BIBLE — {title} ===",
        "",
        "STYLE LOCK",
        f"- Sub-style: ({sl['sub_style']}) {sub_style_name}",
        f"- Aspect ratio: {sl['aspect']}",
        f"- Genre color default: {genre_label}",
        f"- Style anchor image: {sl['anchor_image']}",
        "",
        "REFERENCE SLOTS (Flow limit: 5 characters, 14 objects)",
        f"- Character slots used: {_slot_names(bible, 'characters')}",
        f"- Object slots used: {_slot_names(bible, 'objects')}",
        "- Slot policy: locked at chunk 1. Never rotate mid-novel.",
        "",
        "CHARACTERS",
        *_entries(bible["characters"], slotted=True),
        "",
        "LOCATIONS",
        *_entries(bible["locations"], slotted=False),
        "",
        "OBJECTS",
        *_entries(bible["objects"], slotted=True),
        "",
        "CONTINUITY LOG (append-only)",
        *_items(bible["continuity_log"]),
        "",
        "REVISION LOG (append-only)",
        *_items(bible["revision_log"]),
    ]
    return "\n".join(lines)


def _render_suffix(spec, bible: dict, module: str) -> str:
    """Fills all three brackets in spec.suffix: the sub-style name (locked at chunk 1), the genre
    color treatment for the CURRENT chunk's own module (not the Bible's locked default), and the
    locked aspect ratio."""
    sl = bible["style_lock"]
    name = spec.sub_styles[sl["sub_style"]].split(" — ")[0]
    color = spec.colors[config.MODULE_TO_COLOR[module]]
    result = spec.suffix.replace("[sub-style descriptor]", name)
    result = result.replace("[genre color treatment]", color)
    result = result.replace("[aspect ratio]", sl["aspect"])
    if "[" in result:
        raise SpecError(f"locked style suffix still has an unfilled bracket: {result!r}")
    return result
