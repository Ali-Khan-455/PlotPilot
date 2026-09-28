"""Flow's `#Name` / `@Name` reference syntax (pure). Slot bookkeeping stays entirely in bible.py — this
module only formats and confirms. Intended swap point for a later SDXL+LoRA backend (out of scope here)."""

from imagesync.bible import _norm


def at_tag(tag: str) -> str:
    return f"@{tag}"


def canonical_tag(bible: dict, tag: str) -> str:
    """The Bible's own tag spelling for a confirmed reference, regardless of the model's case/spacing —
    code has the final say on what's emitted. Only ever called on a tag is_confirmed has already
    accepted, so a match always exists; raises defensively if that invariant is ever violated."""
    norm = _norm(tag)
    for category in ("characters", "locations", "objects"):
        for row in bible[category]:
            if _norm(row["tag"]) == norm:
                return row["tag"]
    raise ValueError(f"no confirmed Bible entry for tag {tag!r}")


def format_refs(bible: dict, tags: list[str]) -> str:
    return " ".join(at_tag(canonical_tag(bible, t)) for t in tags)


def is_confirmed(bible: dict, tag: str) -> bool:
    norm = _norm(tag)
    return any(_norm(row["tag"]) == norm and row["reference_generated"]
              for category in ("characters", "locations", "objects") for row in bible[category])
