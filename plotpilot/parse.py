"""Pure parsers for LLM output: chunk 1 delimiters, margin repair, module letter; margin check."""

import re
from dataclasses import dataclass

from plotpilot import config

MARKERS = ["<<<MARGIN_START>>>", "<<<MARGIN_END>>>",
           "<<<TARGET_SENTENCE_START>>>", "<<<TARGET_SENTENCE_END>>>"]
COUNT_LINE_RE = re.compile(r"^[\s*_(]*margin is (\w+) sentences?[\s.*_)]*$", re.I)
LEFTOVER_COUNT_RE = re.compile(r"\bmargin is\b[^\n]*\bsentences?\b", re.I)
COUNT_WORDS = {w: n for n, w in enumerate(
    ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine"], 1)}
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
HOOKY_PHRASES = ["little did", "what happened next", "unbeknownst", "before long"]


class ParseError(ValueError):
    pass


@dataclass(frozen=True)
class Draft:
    margin: str
    target: str
    body: str  # starts with the target sentence, exactly once
    margin_sentences: int

    @property
    def narration(self) -> str:
        return f"{self.margin} {self.body}"


def _normalize(s: str) -> str:
    s = s.translate(str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"}))
    return " ".join(s.split())


def _between(text: str, start: str, end: str) -> str:
    inner = text[text.index(start) + len(start):text.index(end)].strip()
    if not inner:
        raise ParseError(f"empty content between {start} and {end}")
    return inner


def parse_draft(text: str) -> Draft:
    for m in MARKERS:
        if text.count(m) != 1:
            raise ParseError(f"{m} appears {text.count(m)} times, expected once")
    positions = [text.index(m) for m in MARKERS]
    if positions != sorted(positions):
        raise ParseError("delimiters out of order")
    margin = _between(text, MARKERS[0], MARKERS[1])
    target = _between(text, MARKERS[2], MARKERS[3])

    count, kept = None, []
    for line in text[positions[3] + len(MARKERS[3]):].split("\n"):
        m = COUNT_LINE_RE.match(line)
        if m:
            n = int(m[1]) if m[1].isdigit() else COUNT_WORDS.get(m[1].lower())
            if n is None or n > 9:
                raise ParseError(f"implausible margin length: {line.strip()!r}")
            count = count if count is not None else n
        else:
            kept.append(line)
    raw_body = "\n".join(kept).strip()
    if LEFTOVER_COUNT_RE.search(raw_body):
        raise ParseError("margin-count text left in the narration body")
    # Some outputs restate the margin before the target; drop that copy.
    k = len(margin.split())
    parts = raw_body.split(None, k)
    if len(parts) >= k and _normalize(" ".join(parts[:k])) == _normalize(margin):
        raw_body = parts[k] if len(parts) > k else ""
    if count is None:
        count = len(re.findall(r"[.!?](?=\s|$)", margin)) or 1

    nbody, ntarget = _normalize(raw_body), _normalize(target).strip("\"'")
    if nbody.lstrip("\"'").startswith(ntarget):
        body = raw_body
    elif ntarget in nbody:
        raise ParseError("target sentence appears mid-body, not right after the margin")
    else:
        body = f"{target} {raw_body}".strip()
    return Draft(margin, target, body, count)


def parse_repair(text: str) -> str:
    for m in MARKERS[:2]:
        if text.count(m) != 1:
            raise ParseError(f"{m} appears {text.count(m)} times, expected once")
    if text.index(MARKERS[0]) > text.index(MARKERS[1]):
        raise ParseError("delimiters out of order")
    return _between(text, MARKERS[0], MARKERS[1])


def check_margin(margin: str) -> str | None:
    """D9: return why the margin reads hook-flavoured, or None if it is plain."""
    if "?" in margin:
        return "question mark"
    low = margin.lower()
    for phrase in HOOKY_PHRASES:
        if phrase in low:
            return f"phrase '{phrase}'"
    sentences = [s for s in SENTENCE_SPLIT_RE.split(margin.strip()) if s]
    if count_sentences(margin) > 2:  # Prompt 3: 1–2 sentences (also re-checks a repaired margin)
        return "more than two sentences"
    for s in sentences:
        m = re.match(r"[\"“‘']?(But|And)\b", s)
        if m:
            return f"sentence starts with '{m[1]}'"
    avg = sum(len(s.split()) for s in sentences) / len(sentences)
    if avg > 25:
        return f"average sentence length {avg:g} > 25 words"
    return None


def parse_module(text: str) -> str:
    letter = text.strip().rstrip(".").strip().upper()
    if letter not in ("A", "B", "C", "D"):
        raise ParseError(f"expected one of A, B, C, D; got {text.strip()[:40]!r}")
    return letter


# --- Phase 3: audit (Prompt 6), fact-check (Prompt 7), rewrite validation (Prompts 8/9) ---

AUDIT_NEGATIVE_RE = re.compile(
    r"^(?:none|n/a|nothing|no(?: texture)? gaps?)(?: (?:were |was )?(?:found|identified|detected|to report"
    r"|to note))*(?: here)?\.?$", re.I)
AUDIT_NEGATIVE_START_RE = re.compile(r"(?i)^(?:none|n/a|nothing)\b")
AUDIT_NO_RE = re.compile(r"(?i)^no\b.*\b(?:found|detected|identified|noted|observed|to report|without an aside)\b")
AUDIT_END_RE = re.compile(r"(?i)^(?:tts|tone)\b")
MD_HEADER_RE = re.compile(r"^\s*(?:#|\*\*|\d+[.)]\s*\*\*)")
BULLET_RE = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+")
STEP_ECHO_RE = re.compile(r'(?i)state "PRESENT"|list any|list every|give a final|PRESENT\s*/\s*MISSING')
_WRAP_NOUN = r"(?:narration|text|version|rewrite|script|lines)"
PREAMBLE_RE = re.compile(
    r"(?i)^(?:(?:sure|okay|ok|certainly|of course|absolutely)\b[!.,]*\s*)?"
    rf"(?:here(?: is|'s) (?:the|your) [^\n]*{_WRAP_NOUN}[^\n]*"
    rf"|(?:the |your )?(?:normalized |rewritten |revised |updated |textured |final )?{_WRAP_NOUN}):$")
SIGNOFF_RE = re.compile(
    r"(?i)^\s*(?:i )?(?:hope this helps|let me know(?: if[^.!?]*)?|feel free[^.!?]*|happy to help|anything else"
    rf"|end of (?:the )?(?:\w+ )?{_WRAP_NOUN})"
    r"[.!?]?\s*$")
NOTE_RE = re.compile(r"(?i)^[(\[]?\s*[*_]*note[*_]*\s*:")  # a trailing "Note: ..." paragraph; not "Note to self:"


def _strip_md(line: str) -> str:
    return re.sub(r"[*_`#>]", "", BULLET_RE.sub("", line)).strip()


def _is_texture_header(line: str) -> bool:
    if "texture gaps" not in line.lower():
        return False
    return bool(re.match(r"^\s*(?:\d+[.)]|#|\*\*)", line)
                or re.fullmatch(r"\s*texture gaps\s*:?\s*", _strip_md(line), re.I))


def parse_audit(text: str) -> list[str]:
    """Prompt 6 section 5: the texture-gap items (empty when the section says there are none)."""
    lines = text.split("\n")
    start = next((i for i, ln in enumerate(lines) if _is_texture_header(ln)), None)
    if start is None:
        raise ParseError("no 'Texture gaps' section in the audit output")
    inline = re.sub(r"(?i)^.*?texture gaps\W*", "", _strip_md(lines[start]))
    gaps = []
    for line in [inline] + lines[start + 1:]:
        item = _strip_md(line)
        if line is not inline and (AUDIT_END_RE.match(item) or (MD_HEADER_RE.match(line)
                                   and re.search(r"(?i)tts|tone|hazard|drift", item))):
            break  # the section 6/7 header (possibly renamed), never a gap item mentioning drift
        if (re.search(r"[A-Za-z]", item) and not AUDIT_NEGATIVE_RE.match(item)
                and not AUDIT_NEGATIVE_START_RE.match(item) and not AUDIT_NO_RE.match(item)):
            gaps.append(item)
    return gaps


@dataclass(frozen=True)
class Factcheck:
    passed: bool
    flags: list[str]


def parse_factcheck(text: str) -> Factcheck:
    """Prompt 7: the verdict (fail closed) and the MISSING/INVENTED lines."""
    verdicts, flags = [], []
    for line in text.split("\n"):
        c = re.sub(r"[*_`#>]", "", line).strip()
        has_pass, has_fail = re.search(r"\bPASS\b", c), re.search(r"\bFAIL\b", c)
        if has_pass and has_fail:
            continue  # an echoed rule sentence, never a verdict or a flag
        # Uppercase PASS/FAIL anywhere is a verdict word; any case counts on a verdict/result/step-4 line.
        found = re.findall(r"\b(PASS|FAIL)\b", c)
        if not found and re.search(r"(?i)verdict|result|^step 4", c):
            found = [w.upper() for w in re.findall(r"(?i)\b(pass|fail)\b", c)]
        if found:
            verdicts.extend(found)
            continue
        if re.search(r"\b(MISSING|INVENTED)\b", c):
            if re.match(r"(?i)^\s*step \d+", c) and STEP_ECHO_RE.search(c):
                continue  # an echoed instruction, not a flagged line
            flags.append(line.strip())
    if not verdicts:
        raise ParseError("no PASS/FAIL verdict in the fact-check output")
    return Factcheck("FAIL" not in verdicts, flags)


def check_rewrite(new: str, old: str, min_ratio: float | None = None) -> str:
    """Validate a Prompt 8/9 rewrite before it replaces the narration."""
    min_ratio = config.MIN_REWRITE_RATIO if min_ratio is None else min_ratio
    t = new.strip()
    if not t:
        raise ParseError("empty rewrite")
    if "<<<" in t:
        raise ParseError("rewrite contains delimiters")
    if len(t.split()) < min_ratio * len(old.split()):
        raise ParseError(f"rewrite has {len(t.split())} words, under the minimum for {len(old.split())}")
    lines = t.split("\n")
    if PREAMBLE_RE.match(lines[0].strip()):
        raise ParseError("rewrite starts with a preamble")
    if lines[0].strip().startswith("```") or lines[-1].strip().startswith("```"):
        raise ParseError("rewrite is wrapped in a code fence")
    paras = re.split(r"\n\s*\n", t)
    last = paras[-1].strip()
    if len(paras) > 1 and NOTE_RE.match(last) and _normalize(last) not in _normalize(old):
        raise ParseError(f"rewrite ends with a note: {last[:60]!r}")
    if len(paras) > 1 and len(last.split()) < 8 and _normalize(last) not in _normalize(old):
        ends_ok = last.endswith((".", "!", "?", '"', "”", "’", "'", "—", "–"))
        quoted = any(q in last for q in '"“”')
        if not ends_ok or (not quoted and SIGNOFF_RE.match(last)):
            raise ParseError(f"rewrite ends with a sign-off: {last!r}")
    return t


# --- Phase 4: continuation drafts (Prompt 4) and sentence helpers --------------

# A sentence ends at . ! ? (plus an optional closing quote) only when the next non-space character
# starts a new sentence (uppercase or an opening quote) or the text ends. So 'He said "Run." and left.'
# stays one sentence. Common abbreviations (Mr. Mrs. Ms. Dr. St. Jr. Sr. vs. etc.) never end one.
_ABBREV = "".join(rf"(?<!\b{a})" for a in ("Mr", "Mrs", "Ms", "Dr", "St", "Jr", "Sr", "vs", "etc"))
SENTENCE_END_RE = re.compile(rf"{_ABBREV}[.!?][\"”’']?(?=\s+[A-Z\"“‘]|\s*$)")


def parse_continuation(text: str) -> str:
    """Chunk 2+ draft output: plain narration, validated like a rewrite (no delimiters,
    preamble, fence or sign-off)."""
    return check_rewrite(text, "")


def first_sentence(text: str) -> str:
    t = text.strip()
    m = SENTENCE_END_RE.search(t)
    return t[:m.end()] if m else t


def count_sentences(text: str) -> int:
    return len(SENTENCE_END_RE.findall(text.strip())) or 1


# --- Phase 5: hook (Prompt 5) and its Prompt 9 pass --------------------------------

HOOK_MARKERS = ["<<<HOOK_START>>>", "<<<HOOK_END>>>"]
HOOK_LENGTH_RE = re.compile(r"(?i)\bhook length\b")
TERMINAL_RE = re.compile(r"[.!?][\"”’']?$")


def norm_token(token: str) -> str:
    """Lowercase, every non-alphanumeric character dropped (quotes and apostrophes included)."""
    return "".join(ch for ch in token.lower() if ch.isalnum())


def norm_words(text: str) -> list[str]:
    return [w for w in map(norm_token, text.split()) if w]


def ends_with_target(hook: str, target: str) -> bool:
    """The hook's last N normalized words equal the target's (a literal duplication at the splice)."""
    h, t = norm_words(hook), norm_words(target)
    if not t or len(h) < len(t):
        return False
    return h[len(h) - len(t):] == t


def _check_hook(hook: str, target: str) -> str:
    if not norm_words(hook):
        raise ParseError("hook has no words")
    if "<<<" in hook:
        raise ParseError("hook contains delimiters")
    if HOOK_LENGTH_RE.search(hook):
        raise ParseError("hook-length text inside the hook")
    if ends_with_target(hook, target):
        raise ParseError("hook ends with the target sentence")
    hook = " ".join(hook.split())
    if not TERMINAL_RE.search(hook):
        raise ParseError("hook does not end in terminal punctuation")
    return hook


def parse_hook(text: str, target: str) -> str:
    """Prompt 5 output: the text between the hook delimiters. Anything outside them (such as the
    "hook length" line) is ignored."""
    for m in HOOK_MARKERS:
        if text.count(m) != 1:
            raise ParseError(f"{m} appears {text.count(m)} times, expected once")
    if text.index(HOOK_MARKERS[0]) > text.index(HOOK_MARKERS[1]):
        raise ParseError("delimiters out of order")
    return _check_hook(_between(text, *HOOK_MARKERS), target)


def check_hook_tts(new: str, hook: str, target: str) -> str:
    """Prompt 9 on the hook: a rewrite check plus the hook checks. Prompt 9's ratio, but a short hook may
    always lose one word (a homograph rewrite, Prompt 9 item 3)."""
    n = len(hook.split())
    ratio = min(config.TTS_MIN_RATIO, (n - 1) / n) if n > 1 else config.TTS_MIN_RATIO
    return _check_hook(check_rewrite(new, hook, min_ratio=ratio), target)
