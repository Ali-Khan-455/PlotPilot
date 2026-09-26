"""Stages 2–3 for every chunk (module gate, draft; chunk 1 also margin check/repair), the narration
state and file sync that QC (qc.py) builds on, and the chunk loop (run_novel)."""

import json
from dataclasses import dataclass, replace
from pathlib import Path

import anthropic

from plotpilot import config, db, tracker
from plotpilot.llm import LLMError, log_error
from plotpilot.parse import (ParseError, check_margin, parse_continuation, parse_draft, parse_module,
                             parse_repair)
from plotpilot.prompts import fill

REPAIR_TARGET = ("[paste the target sentence — the one immediately following the margin, "
                 "already logged in the Continuity Tracker]")
REPAIR_MARGIN = "[paste the flawed margin here]"
TRACKER4 = "[Paste your filled-in Continuity Tracker here]"
FORCED = "forced by operator"
TEXT_KINDS = ("draft", "margin_repair", "operator_edit", "texture", "tts")


class FileFormatError(Exception):
    pass


@dataclass(frozen=True)
class State:
    margin: str | None
    target: str | None
    body: str


def render(margin, body: str) -> str:
    """Chunk file text. Chunk 1: the margin (whitespace collapsed) as its own first paragraph, then the
    body. Chunks 2+: the body. Every file ends with exactly one "\\n"; the leading strip is incidental
    (switch to rstrip("\\n") if leading whitespace ever matters)."""
    if margin is None:
        return body.strip() + "\n"
    return f"{' '.join(margin.split())}\n\n{body.strip()}\n"


def norm_ws(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.strip().split("\n"))


def split_file(text: str, has_margin: bool = True):
    if not has_margin:
        if not text.strip():
            raise FileFormatError("ERROR: the chunk file is empty.")
        return None, text.strip()
    parts = text.strip().split("\n\n", 1)
    if len(parts) < 2 or not parts[0].strip() or not parts[1].strip():
        raise FileFormatError("ERROR: keep the margin as its own first paragraph (blank line after it).")
    return " ".join(parts[0].split()), parts[1].strip()


def _apply(state, kind, output, first):
    if kind == "draft":
        if first:
            d = parse_draft(output)
            return State(d.margin, d.target, d.body)
        return State(None, None, parse_continuation(output))
    if state is None:
        return None
    if kind == "margin_repair":
        return replace(state, margin=parse_repair(output))
    if kind == "operator_edit":
        margin, body = split_file(output, first)
        return replace(state, margin=margin, body=body)
    return replace(state, body=output.strip())  # texture, tts


def _is_first(conn, chunk_id) -> bool:
    return db.chunk(conn, chunk_id)["idx"] == 1


def narration_state(conn, chunk_id):
    """Fold the ok text-changing passes from the latest ok draft onward; None without an ok draft."""
    first = _is_first(conn, chunk_id)
    rows = db.ok_passes(conn, chunk_id, TEXT_KINDS)
    drafts = [i for i, r in enumerate(rows) if r["kind"] == "draft"]
    if not drafts:
        return None
    state = None
    for r in rows[drafts[-1]:]:
        state = _apply(state, r["kind"], r["output_text"], first)
    return state


def stale_renderings(conn, chunk_id) -> set[str]:
    """Every rendering the chunk file has ever legitimately held, across all draft series; for chunk 1
    also Phase 2's single-space form (margin + " " + body)."""
    first = _is_first(conn, chunk_id)
    seen, state = set(), None
    for r in db.ok_passes(conn, chunk_id, TEXT_KINDS):
        state = _apply(state, r["kind"], r["output_text"], first)
        if state:
            seen.add(norm_ws(render(state.margin, state.body)))
            if first and r["kind"] in ("draft", "margin_repair"):
                seen.add(norm_ws(f"{state.margin} {state.body}"))
    return seen


def chunk_path(out_dir, slug, idx) -> Path:
    return Path(out_dir) / slug / f"chunk-{idx:02d}.txt"


class ChunkRun:
    def __init__(self, conn, llm, prompts, novel_id, slug, title, chunk_row, *, gen_model, qc_model, out_dir):
        self.conn, self.llm, self.P = conn, llm, prompts
        self.novel_id, self.slug, self.title = novel_id, slug, title
        self.gen_model, self.qc_model, self.out_dir = gen_model, qc_model, out_dir
        self.chunk = chunk_row
        self.idx = chunk_row["idx"]
        self.first = self.idx == 1
        self.path = chunk_path(out_dir, slug, self.idx)

    @property
    def status(self) -> str:
        return db.chunk(self.conn, self.chunk["id"])["status"]

    def prompt_tracker(self) -> str:
        """Tracker text for prompts: empty for chunk 1; rendered (without overrides, R15) afterwards."""
        if self.first:
            return config.EMPTY_TRACKER
        raw = db.latest_tracker(self.conn, self.novel_id)
        rows = db.chunks(self.conn, self.novel_id)
        prev = rows[self.idx - 2]
        return tracker.render(json.loads(raw) if raw else tracker.empty(), title=self.title,
                              chunks=[(r["idx"], r["label"]) for r in rows],
                              progress=f"Part 1, Chunk {prev['idx']} — {prev['label']} processed so far")

    def attempt(self, kind, model, user, parse, *, system=None, max_tokens, module=None, note=None,
                status_for=None, retry_max_tokens=False):
        """Call, parse in memory, insert the pass once with its verdict (and, atomically, the
        status from status_for(parsed)). Retry once on ParseError (and, with retry_max_tokens, on a
        max_tokens stop, e.g. a chatty one-letter classification)."""
        input_text = f"{system}\n\n=====\n\n{user}" if system is not None else user
        last = None
        for _ in range(2):
            try:
                text = self.llm.call(kind, model, user, system=system, max_tokens=max_tokens,
                                     slug=self.slug, chunk_idx=self.idx)
            except LLMError as e:
                if e.stop_reason:
                    db.add_pass(self.conn, self.novel_id, self.chunk["id"], kind, model, input_text,
                                e.text, module=module, verdict=f"STOPPED:{e.stop_reason}", note=note)
                if retry_max_tokens and e.stop_reason == "max_tokens":
                    last = ParseError(f"stopped at max_tokens: {e.text[:40]!r}")
                    continue
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
                                      max_tokens=config.CLASSIFY_MAX_TOKENS, retry_max_tokens=True)
            except ParseError as e:
                msg = (f"Module classification for chunk {self.idx} was {e}; raw outputs are stored. "
                       "Re-run, or pass --module.")
                print(msg)
                log_error(self.llm.log_dir, msg)
                return 1
        title = self.P[f"MODULE {letter}"].heading.split(" — ", 1)[1]
        print(f"Suggested module for chunk {self.idx}: {letter} ({title}). "
              f"Re-run with --module {letter} to confirm, or pick another.")
        return 0

    def draft(self, module, note=None):
        self.llm.check_models([self.gen_model])
        system = f"{self.P['1'].text}\n\n{self.P[f'MODULE {module}'].text}"
        if self.first:
            user, parse = f"{self.P['3'].text}\n\n---\n\n{self.chunk['source_text']}", parse_draft
        else:
            user = (f"{fill(self.P['4'].text, {TRACKER4: self.prompt_tracker()})}\n\n---\n\n"
                    f"{self.chunk['source_text']}")
            parse = parse_continuation
        return self.attempt("draft", self.gen_model, user, parse, system=system,
                            max_tokens=config.GEN_MAX_TOKENS, module=module, note=note,
                            status_for=lambda _: "drafted")

    def repair(self, margin, target, reason):
        """Run Prompt 3-REPAIR (chunk 1). A failed repair keeps the current margin and warns."""
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
        """Reconcile the chunk file with the stored state. Returns 'edit', 'stale', 'missing' or None."""
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
        try:
            split_file(raw, self.first)
        except FileFormatError as e:
            raise FileFormatError(str(e).replace("the chunk file", self.path.name)) from None
        new_status = "drafted" if self.status == "drafted" else "audited"
        db.add_pass(self.conn, self.novel_id, self.chunk["id"], "operator_edit", None, "", raw,
                    new_status=new_status)
        self.write()  # canonical form, so an unchanged file never re-triggers
        print(f"Recorded your edit to {self.path.name}; it will be fact-checked.")
        return "edit"


def _check_done_chunks(conn, novel_id, slug, out_dir):
    """Done chunks are frozen (R21): a missing file is restored; an edited file is warned about."""
    for row in db.chunks(conn, novel_id):
        if row["status"] != "done":
            continue
        state = narration_state(conn, row["id"])
        path = chunk_path(out_dir, slug, row["idx"])
        want = render(state.margin, state.body)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(want, encoding="utf-8")
        elif norm_ws(path.read_text(encoding="utf-8")) != norm_ws(want):
            print(f"WARNING: {path.name} was edited after its tracker was merged; the edit is ignored and "
                  "the stored text is used for assembly. Delete the file to restore the stored text.")


def run_novel(conn, llm, prompts, novel_id, slug, title, *, module, redraft, repair, gen_model, qc_model,
              out_dir, accept=None, accept_tracker=False) -> int:
    from plotpilot import qc

    _check_done_chunks(conn, novel_id, slug, out_dir)
    rows = db.chunks(conn, novel_id)
    chunk1_done = rows[0]["status"] == "done"
    flags = dict(module=module, redraft=redraft, repair=repair, accept=accept, accept_tracker=accept_tracker)
    while True:
        current = next((r for r in db.chunks(conn, novel_id) if r["status"] != "done"), None)
        if current is None:
            if flags["module"]:
                print("Note: --module is ignored; every chunk is done.")
            if flags["redraft"]:
                print("Note: --redraft ignored; no drafted chunk to redraft.")
            if flags["repair"]:
                print("Note: --repair-margin ignored; chunk 1 is frozen because its tracker has already been merged.")
            if flags["accept_tracker"] or flags["accept"]:
                print("Note: accept flags ignored; every chunk is done.")
            from plotpilot import final

            print(f"All {len(rows)} chunks done → {Path(out_dir) / slug}/.")
            return final.run_final(conn, llm, prompts, novel_id, slug, title, gen_model=gen_model,
                                   qc_model=qc_model, out_dir=out_dir)
        if flags["repair"] and (chunk1_done or current["idx"] != 1):
            print("Note: --repair-margin ignored; chunk 1 is frozen because its tracker has already been merged.")
            flags["repair"] = False
        c = ChunkRun(conn, llm, prompts, novel_id, slug, title, current,
                     gen_model=gen_model, qc_model=qc_model, out_dir=out_dir)
        code = _run_chunk(c, qc, **flags)
        if code != 0 or c.status != "done":
            return code
        # Flags bind only to the run-start chunk (R7 no carry-over): the next chunk gets none.
        flags = dict(module=None, redraft=False, repair=False, accept=None, accept_tracker=False)


def _run_chunk(c, qc, *, module, redraft, repair, accept, accept_tracker) -> int:
    status_at_start = c.status
    started = status_at_start != "planned"
    if redraft and not started:
        print("Note: --redraft ignored; no drafted chunk to redraft.")
        redraft = False
    edited = False
    if started:
        try:
            edited = c.sync_file() == "edit"
        except FileFormatError as e:
            print(e)
            return 1
    if accept_tracker and (status_at_start != "tracker_pending" or edited):
        print(f"Note: --accept-tracker ignored; your edit to {c.path.name} will be re-checked and a new "
              "tracker delta produced first." if edited else
              f"Note: --accept-tracker ignored; chunk {c.idx} has no pending tracker update.")
        accept_tracker = False

    if started and not redraft and not repair:
        if module:
            print(f"Note: --module is ignored; chunk {c.idx} is already drafted (use --redraft to redo it).")
        return qc.run(c, accept=accept, edited=edited, accept_tracker=accept_tracker)

    if started and repair and not redraft:
        if module:
            print(f"Note: --module is ignored; chunk {c.idx} is already drafted (use --redraft to redo it).")
        state = narration_state(c.conn, c.chunk["id"])
        c.repair(state.margin, state.target, FORCED)
        c.write()
        return qc.run(c, accept=accept, edited=edited, accept_tracker=accept_tracker)

    if redraft and started and not module:
        last = db.latest_pass(c.conn, c.chunk["id"], "draft")
        module = last["module"] if last else None
    if not module:
        return c.gate()

    try:
        d = c.draft(module, note="--redraft requested" if started else None)
    except ParseError as e:
        msg = f"Chunk {c.idx} draft output was {e}; raw outputs are stored. Re-run to try again."
        print(msg)
        log_error(c.llm.log_dir, msg)
        return 1

    if c.first:
        reason = FORCED if repair else check_margin(d.margin)
        if reason:
            if reason != FORCED:
                print(f"Margin check fired: {reason}. Running Prompt 3-REPAIR.")
            c.repair(d.margin, d.target, reason)
        else:
            print("Margin check: clean.")
    c.write()
    print(f"Chunk {c.idx} drafted → {c.path}.")
    return qc.run(c, accept=accept, edited=False, accept_tracker=False)
