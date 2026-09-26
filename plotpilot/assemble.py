"""Hook splice, script assembly and scene timestamps (pure)."""

from plotpilot import config
from plotpilot.parse import ends_with_target, norm_token, norm_words


class SpliceError(ValueError):
    pass


def join(first: str, bodies) -> str:
    """The first paragraph (hook or margin, whitespace collapsed), then each body; one trailing newline."""
    return "\n\n".join([" ".join(first.split()), *(b.strip() for b in bodies)]) + "\n"


def chunk_starts(first: str, bodies) -> list[int]:
    """Word index (str.split() count) at which each body starts in join(first, bodies)."""
    starts, n = [], len(first.split())
    for b in bodies:
        starts.append(n)
        n += len(b.split())
    return starts


def target_check(body1: str, target: str):
    t = norm_words(target)
    if not t:
        raise SpliceError(f"the target sentence {target!r} is empty")
    if norm_words(body1)[:len(t)] != t:
        raise SpliceError(f"chunk 1's narration does not start with the target sentence {target!r}")


def splice_check(script: str, target: str):
    """D20 on the assembled script: the text right after the hook starts with the target, and the hook
    doesn't end with it."""
    parts = script.split("\n\n", 1)
    if len(parts) < 2:
        raise SpliceError("the script has no paragraph after the hook")
    target_check(parts[1], target)
    if ends_with_target(parts[0], target):
        raise SpliceError("the hook ends with the target sentence")


def _stamp(offset: int, wpm: int) -> str:
    sec = offset * 60 // wpm
    return f"[{sec // 60:02d}:{sec % 60:02d}]"


def scene_lines(script: str, starts, scenes_per_chunk, wpm: int = config.WORDS_PER_MINUTE):
    """R5: locate each scene's first sentence in the script (normalized words, forward cursor, bounded
    to its chunk) and turn its word offset into a timestamp. scenes_per_chunk: per chunk, a list of
    (first_sentence, description). Returns (lines, warnings)."""
    tokens = [(i, w) for i, w in enumerate(map(norm_token, script.split())) if w]
    total = len(script.split())
    lines, warnings, cursor, prev = [], [], 0, "[00:00]"
    for ci, scenes in enumerate(scenes_per_chunk):
        lo, hi = starts[ci], starts[ci + 1] if ci + 1 < len(starts) else total
        for sentence, description in scenes:
            needle = norm_words(sentence)
            begin = max(cursor, lo)
            hay = [(i, w) for i, w in tokens if begin <= i < hi]
            words = [w for _, w in hay]
            hit = next((hay[k][0] for k in range(len(hay) - len(needle) + 1)
                        if needle and words[k:k + len(needle)] == needle), None)
            if hit is None:
                warnings.append(f'scene "{sentence[:60]}…" (chunk {ci + 1}) not found in the script; '
                                "using the previous timestamp.")
                stamp = prev
            else:
                cursor = hit + 1
                stamp = "[00:00]" if not lines else _stamp(hit, wpm)
            prev = stamp
            lines.append(f"{stamp} SCENE: {' '.join(description.split())}")
    return lines, warnings
