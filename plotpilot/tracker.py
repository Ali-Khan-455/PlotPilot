"""The Continuity Tracker: Prompt 10 delta and Prompt 11 scene validation, deterministic merge,
and rendering (pure). The render layout mirrors the spec's tracker template headings; it is data
formatting, not prompt text."""

import copy
import json
import re

from plotpilot.parse import ParseError

DELTA_KEYS = {"new_characters", "new_terms", "new_comparisons", "new_texture_motifs",
              "chunk_end_state", "nickname_collisions"}


def empty() -> dict:
    return {"characters": [], "terms": [], "comparisons": [], "texture_motifs": [],
            "last_state": "", "chunk1": None}


def extract_json(text: str):
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if fence:
        t = fence.group(1)
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end < start:
        raise ParseError("no JSON object in the output")
    try:
        return json.loads(t[start:end + 1])
    except json.JSONDecodeError as e:
        raise ParseError(f"invalid JSON ({e.msg})") from None


def _need(cond, msg):
    if not cond:
        raise ParseError(msg)


def _str_list(obj, key):
    _need(isinstance(obj.get(key), list) and all(isinstance(x, str) for x in obj[key]),
          f"'{key}' must be a list of strings")


def _obj_list(obj, key, fields):
    items = obj.get(key)
    _need(isinstance(items, list), f"'{key}' must be a list")
    for it in items:
        _need(isinstance(it, dict) and set(it) == set(fields), f"'{key}' items need exactly {sorted(fields)}")
        for f, typ in fields.items():
            name = " or ".join(t.__name__ for t in typ) if isinstance(typ, tuple) else typ.__name__
            _need(isinstance(it[f], typ) and not isinstance(it[f], bool), f"'{key}.{f}' must be {name}")


def validate_delta(obj) -> dict:
    """Prompt 10 schema (D18): exact keys, exact types."""
    _need(isinstance(obj, dict) and set(obj) == DELTA_KEYS, f"tracker delta needs exactly {sorted(DELTA_KEYS)}")
    _obj_list(obj, "new_characters", {"name": str, "standin": str})
    _obj_list(obj, "new_terms", {"term": str, "meaning": str, "chunk": (int, str)})  # merge sets chunk anyway
    for key in ("new_comparisons", "new_texture_motifs", "nickname_collisions"):
        _str_list(obj, key)
    _need(isinstance(obj["chunk_end_state"], str) and obj["chunk_end_state"].strip(),
          "'chunk_end_state' must be a non-empty string")
    return obj


def validate_scenes(obj) -> dict:
    """Prompt 11 schema (D18): {"scenes": [{"first_sentence", "description"}]}, non-empty."""
    _need(isinstance(obj, dict) and set(obj) == {"scenes"}, "scenes output needs exactly {'scenes'}")
    _obj_list(obj, "scenes", {"first_sentence": str, "description": str})
    _need(obj["scenes"], "'scenes' must not be empty")
    for s in obj["scenes"]:
        _need(s["first_sentence"].strip() and s["description"].strip(), "scene fields must be non-empty")
    return obj


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def merge(tracker: dict, delta: dict, chunk_idx: int) -> dict:
    """Append new items, dedupe, force term chunk numbers; collisions are never stored."""
    t = copy.deepcopy(tracker)
    names = {_norm(c["name"]) for c in t["characters"]}
    for c in delta["new_characters"]:
        if _norm(c["name"]) not in names:
            t["characters"].append({"name": c["name"], "standin": c["standin"]})
            names.add(_norm(c["name"]))
    terms = {_norm(x["term"]) for x in t["terms"]}
    for x in delta["new_terms"]:
        if _norm(x["term"]) not in terms:
            t["terms"].append({"term": x["term"], "meaning": x["meaning"], "chunk": chunk_idx})
            terms.add(_norm(x["term"]))
    for key, new in (("comparisons", "new_comparisons"), ("texture_motifs", "new_texture_motifs")):
        seen = {_norm(x) for x in t[key]}
        for x in delta[new]:
            if _norm(x) not in seen:
                t[key].append(x)
                seen.add(_norm(x))
    t["last_state"] = delta["chunk_end_state"]
    return t


def merge_collisions(tracker: dict, delta: dict) -> list[str]:
    """New stand-ins already assigned to a different character (in the tracker or earlier in this delta),
    and stand-in changes for a known character, which merge ignores (case-insensitive)."""
    used = {_norm(c["standin"]): c["name"] for c in tracker["characters"]}
    known = {_norm(c["name"]): c for c in tracker["characters"]}
    out = []
    for c in delta["new_characters"]:
        old = known.get(_norm(c["name"]))
        if old:
            if _norm(old["standin"]) != _norm(c["standin"]):
                out.append(f"{old['name']} already uses '{old['standin']}'; the new stand-in "
                           f"'{c['standin']}' is ignored")
            continue
        owner = used.get(_norm(c["standin"]))
        if owner and _norm(owner) != _norm(c["name"]):
            out.append(f"'{c['standin']}' ({c['name']}) is already used for {owner}")
        used.setdefault(_norm(c["standin"]), c["name"])
    return out


def progress(rows, idx: int) -> str:
    """The tracker's Progress line after chunk idx: the cumulative chapter range."""
    last = next(r for r in rows if r["idx"] == idx)
    start, end = rows[0]["chapter_start"], last["chapter_end"]
    span = f"Chapter {start}" if start == end else f"Chapters {start}–{end}"
    return f"Part 1, Chunk {idx} — {span} processed so far"


def _items(lines):
    return lines or ["- (none yet)"]


def render(tracker: dict, *, title: str, chunks, progress: str, overrides=None) -> str:
    """Tracker markdown. overrides=None gives the prompt form (R15: overrides never reach prompts)."""
    t = tracker
    out = [f"## Continuity Tracker — {title}", "", f"**Progress:** {progress}", "",
           '**POV:** First-person MC. Always "I / me / my."', "",
           "**MC voice notes:**",
           f"- Asides used so far: {'; '.join(t['texture_motifs']) if t['texture_motifs'] else '(none yet)'}", "",
           "**Characters (real name → casual stand-in in use):**",
           *_items([f"- {c['name']} → {c['standin']}" for c in t["characters"]]), "",
           "**Established terms/systems (don't re-explain):**",
           *_items([f"- {x['term']} — {x['meaning']} (established in chunk {x['chunk']})" for x in t["terms"]]), "",
           "**Comparisons/references already used (don't repeat):**",
           *_items([f"- {x}" for x in t["comparisons"]]), "",
           "**Texture motifs already used (vary these, don't reuse the exact line):**",
           *_items([f"- {x}" for x in t["texture_motifs"]]), "",
           "**Chunk boundaries planned:**",
           *[f"- Chunk {idx}: {label}" for idx, label in chunks], "",
           "**Where the last chunk left off (1–2 sentences):**",
           f"- {t['last_state'] or '(nothing yet)'}", ""]
    if t.get("chunk1"):
        m = t["chunk1"]
        out += ["**Chunk 1 placeholder margin (keep until Prompt 5 runs):**",
                f"- Margin length: {m['margin_sentences']} sentence(s)",
                f"- The sentence immediately following the margin, verbatim: \"{m['target']}\"", ""]
    if overrides is not None:
        out += ["**Fact-check overrides (operator):**",
                *_items([f"- Chunk {idx}: {reason}" for idx, reason in overrides]), ""]
    return "\n".join(out).rstrip() + "\n"
