"""Stage 2 for chunk 1 (module gate, draft, margin check/repair) plus the narration state and
file sync that QC (stage 4, qc.py) builds on."""

from dataclasses import dataclass, replace
from pathlib import Path

import anthropic

from plotpilot import config, db
from plotpilot.llm import LLMError, log_error
from plotpilot.parse import ParseError, check_margin, parse_draft, parse_module, parse_repair
from plotpilot.prompts import fill

REPAIR_TARGET = ("[paste the target sentence — the one immediately following the margin, "
                 "already logged in the Continuity Tracker]")
REPAIR_MARGIN = "[paste the flawed margin here]"
FORCED = "forced by operator"
TEXT_KINDS = ("draft", "margin_repair", "operator_edit", "texture", "tts")


class FileFormatError(Exception):
    pass


@dataclass(frozen=True)
class State:
    margin: str
    target: str
    body: str


def render(margin: str, body: str) -> str:
    """Chunk file text: the margin as its own first paragraph, then the body."""
    return f"{' '.join(margin.split())}\n\n{body.strip()}\n"


def norm_ws(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.strip().split("\n"))


def split_file(text: str) -> tuple[str, str]:
    parts = text.strip().split("\n\n", 1)
    if len(parts) < 2 or not parts[0].strip() or not parts[1].strip():
        raise FileFormatError("ERROR: keep the margin as its own first paragraph (blank line after it).")
    return " ".join(parts[0].split()), parts[1].strip()


def _apply(state, kind, output):
    if kind == "draft":
        d = parse_draft(output)
        return State(d.margin, d.target, d.body)
    if state is None:
        return None
    if kind == "margin_repair":
        return replace(state, margin=parse_repair(output))
    if kind == "operator_edit":
        margin, body = split_file(output)
        return replace(state, margin=margin, body=body)
    return replace(state, body=output.strip())  # texture, tts


def narration_state(conn, chunk_id):
    """Fold the ok text-changing passes from the latest ok draft onward; None without an ok draft."""
    rows = db.ok_passes(conn, chunk_id, TEXT_KINDS)
    drafts = [i for i, r in enumerate(rows) if r["kind"] == "draft"]
    if not drafts:
        return None
    state = None
    for r in rows[drafts[-1]:]:
        state = _apply(state, r["kind"], r["output_text"])
    return state


def stale_renderings(conn, chunk_id) -> set[str]:
    """Every rendering the chunk file has ever legitimately held, across all draft series,
    including Phase 2's single-space form (margin + " " + body)."""
    seen, state = set(), None
    for r in db.ok_passes(conn, chunk_id, TEXT_KINDS):
        state = _apply(state, r["kind"], r["output_text"])
        if state:
            seen.add(norm_ws(render(state.margin, state.body)))
            if r["kind"] in ("draft", "margin_repair"):
                seen.add(norm_ws(f"{state.margin} {state.body}"))
    return seen


class Chunk1:
    def __init__(self, conn, llm, prompts, novel_id, slug, *, gen_model, qc_model, out_dir):
        self.conn, self.llm, self.P = conn, llm, prompts
        self.novel_id, self.slug = novel_id, slug
        self.gen_model, self.qc_model, self.out_dir = gen_model, qc_model, out_dir
        self.chunk = db.first_chunk(conn, novel_id)
        self.path = Path(out_dir) / slug / f"chunk-{self.chunk['idx']:02d}.txt"

    @property
    def status(self) -> str:
        return db.first_chunk(self.conn, self.novel_id)["status"]

    def attempt(self, kind, model, user, parse, *, system=None, max_tokens, module=None, note=None,
                status_for=None):
        """Call, parse in memory, insert the pass once with its verdict (and, atomically, the
        status from status_for(parsed)). Retry once on ParseError."""
        input_text = f"{system}\n\n=====\n\n{user}" if system is not None else user
        last = None
        for _ in range(2):
            try:
                text = self.llm.call(kind, model, user, system=system, max_tokens=max_tokens,
                                     slug=self.slug, chunk_idx=self.chunk["idx"])
            except LLMError as e:
                if e.stop_reason:
                    db.add_pass(self.conn, self.novel_id, self.chunk["id"], kind, model, input_text,
                                e.text, module=module, verdict=f"STOPPED:{e.stop_reason}", note=note)
                raise
            try:
                parsed = parse(text)
            except ParseError as e:
                db.add_pass(self.conn, self.novel_id, self.chunk["id"], kind, model, input_text, text,
                            module=module, verdict="PARSE_FAILED", note=note)
                last = e
                continue
            db.add_pass(self.conn, self.novel_id, self.chunk["id"], kind, model, input_text, text,
                        module=module, note=note, new_status=status_for(parsed) if status_for else None)
            return parsed
        raise ParseError(f"malformed twice ({last})")

    def gate(self) -> int:
        stored = db.latest_pass(self.conn, self.chunk["id"], "classify")
        if stored:
            letter = parse_module(stored["output_text"])
        else:
            self.llm.check_models([self.qc_model])
            modules = "\n\n".join(f"{self.P[f'MODULE {x}'].heading}\n{self.P[f'MODULE {x}'].text}"
                                  for x in "ABCD")
            opening = " ".join(self.chunk["source_text"].split()[:config.CLASSIFY_WORDS])
            user = fill(self.P["12"].text, {"[Paste the four modules]": modules,
                                            "[Paste the opening of this chunk]": opening})
            try:
                letter = self.attempt("classify", self.qc_model, user, parse_module,
                                      max_tokens=config.CLASSIFY_MAX_TOKENS)
            except ParseError as e:
                print(f"Module classification for chunk 1 was {e}; raw outputs are stored. "
                      "Re-run, or pass --module.")
                return 1
        title = self.P[f"MODULE {letter}"].heading.split(" — ", 1)[1]
        print(f"Suggested module for chunk 1: {letter} ({title}). "
              f"Re-run with --module {letter} to confirm, or pick another.")
        return 0

    def draft(self, module, note=None):
        self.llm.check_models([self.gen_model])
        system = f"{self.P['1'].text}\n\n{self.P[f'MODULE {module}'].text}"
        user = f"{self.P['3'].text}\n\n---\n\n{self.chunk['source_text']}"
        return self.attempt("draft", self.gen_model, user, parse_draft, system=system,
                            max_tokens=config.GEN_MAX_TOKENS, module=module, note=note,
                            status_for=lambda _: "drafted")

    def repair(self, margin, target, reason):
        """Run Prompt 3-REPAIR. A failed repair keeps the current margin and warns."""
        self.llm.check_models([self.gen_model])
        user = fill(self.P["3-REPAIR"].text, {REPAIR_TARGET: target, REPAIR_MARGIN: margin})
        try:
            new = self.attempt("margin_repair", self.gen_model, user, parse_repair,
                               max_tokens=config.REPAIR_MAX_TOKENS, note=reason)
        except (ParseError, LLMError, anthropic.AnthropicError) as e:
            if not isinstance(e, ParseError):
                log_error(self.llm.log_dir, f"margin_repair {type(e).__name__}: {e}")
            print(f"WARNING: margin repair failed ({e}); keeping the draft margin. "
                  "Use --repair-margin to try again.")
            return
        still = check_margin(new)
        if still:
            print(f"WARNING: repaired margin still flagged ({still}); use --repair-margin to try again.")

    def write(self):
        state = narration_state(self.conn, self.chunk["id"])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(render(state.margin, state.body), encoding="utf-8")

    def sync_file(self):
        """Reconcile chunk-01.txt with the stored state. Returns 'edit', 'stale', 'missing' or None."""
        state = narration_state(self.conn, self.chunk["id"])
        if state is None:
            return None
        if not self.path.exists():
            self.write()
            return "missing"
        raw = self.path.read_text(encoding="utf-8")
        have = norm_ws(raw)
        if have == norm_ws(render(state.margin, state.body)):
            return None
        if have in stale_renderings(self.conn, self.chunk["id"]):
            self.write()
            print(f"Note: {self.path.name} matched an earlier stored version and was rewritten from the "
                  "current state. To restore earlier text on purpose, edit it (any change beyond "
                  "whitespace counts as an edit).")
            return "stale"
        split_file(raw)  # raises FileFormatError
        new_status = "drafted" if self.status == "drafted" else "audited"
        db.add_pass(self.conn, self.novel_id, self.chunk["id"], "operator_edit", None, "", raw,
                    new_status=new_status)
        self.write()  # canonical form, so an unchanged file never re-triggers
        print(f"Recorded your edit to {self.path.name}; it will be fact-checked.")
        return "edit"


def run_chunk1(conn, llm, prompts, novel_id, slug, *, module, redraft, repair,
               gen_model, qc_model, out_dir, accept=None) -> int:
    from plotpilot import qc

    c = Chunk1(conn, llm, prompts, novel_id, slug, gen_model=gen_model, qc_model=qc_model, out_dir=out_dir)
    started = c.status != "planned"
    edited = False
    if started:
        try:
            edited = c.sync_file() == "edit"
        except FileFormatError as e:
            print(e)
            return 1

    if started and not redraft and not repair:
        if module:
            print("Note: --module is ignored; chunk 1 is already drafted (use --redraft to redo it).")
        return qc.run(c, accept=accept, edited=edited)

    if started and repair and not redraft:
        state = narration_state(conn, c.chunk["id"])
        c.repair(state.margin, state.target, FORCED)
        c.write()
        return qc.run(c, accept=accept, edited=edited)

    if redraft and started and not module:
        last = db.latest_pass(conn, c.chunk["id"], "draft")
        module = last["module"] if last else None
    if not module:
        return c.gate()

    try:
        d = c.draft(module, note="--redraft requested" if started else None)
    except ParseError as e:
        print(f"Chunk 1 draft output was {e}; raw outputs are stored. Re-run to try again.")
        return 1

    reason = FORCED if repair else check_margin(d.margin)
    if reason:
        if reason != FORCED:
            print(f"Margin check fired: {reason}. Running Prompt 3-REPAIR.")
        c.repair(d.margin, d.target, reason)
    else:
        print("Margin check: clean.")
    c.write()
    print(f"Chunk 1 drafted → {c.path}.")
    return qc.run(c, accept=accept, edited=False)
