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
            existing["name"] = item["name"]
            existing["descriptor"] = item["descriptor"]
            existing["current_state"] = item["current_state"]
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
