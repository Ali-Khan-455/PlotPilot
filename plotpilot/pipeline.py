"""Stage 2: chunk 1 module gate, draft, margin check and repair (Phase 2)."""

from pathlib import Path

from plotpilot import config, db
from plotpilot.llm import LLMError
from plotpilot.parse import ParseError, check_margin, parse_draft, parse_module, parse_repair
from plotpilot.prompts import fill

REPAIR_TARGET = ("[paste the target sentence — the one immediately following the margin, "
                 "already logged in the Continuity Tracker]")
REPAIR_MARGIN = "[paste the flawed margin here]"
FORCED = "forced by operator"


def current_margin(conn, chunk_id):
    """(margin, target, body) re-derived from stored raw outputs. Only a repair newer than the
    latest ok draft counts, so a repair from an abandoned draft never pairs with a newer one."""
    d = db.latest_pass(conn, chunk_id, "draft")
    draft = parse_draft(d["output_text"])
    r = db.latest_pass(conn, chunk_id, "margin_repair", after_id=d["id"])
    return (parse_repair(r["output_text"]) if r else draft.margin), draft.target, draft.body


class Chunk1:
    def __init__(self, conn, llm, prompts, novel_id, slug, *, gen_model, qc_model, out_dir):
        self.conn, self.llm, self.P = conn, llm, prompts
        self.novel_id, self.slug = novel_id, slug
        self.gen_model, self.qc_model, self.out_dir = gen_model, qc_model, out_dir
        self.chunk = db.first_chunk(conn, novel_id)

    def _attempt(self, kind, model, user, parse, *, system=None, max_tokens, module=None, note=None):
        """Call, parse in memory, insert the pass once with its verdict. Retry once on ParseError."""
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
                        module=module, note=note)
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
                letter = self._attempt("classify", self.qc_model, user, parse_module,
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
        return self._attempt("draft", self.gen_model, user, parse_draft, system=system,
                             max_tokens=config.GEN_MAX_TOKENS, module=module, note=note)

    def repair(self, margin, target, reason):
        """Run Prompt 3-REPAIR. A failed repair keeps the current margin and warns."""
        self.llm.check_models([self.gen_model])
        user = fill(self.P["3-REPAIR"].text, {REPAIR_TARGET: target, REPAIR_MARGIN: margin})
        try:
            new = self._attempt("margin_repair", self.gen_model, user, parse_repair,
                                max_tokens=config.REPAIR_MAX_TOKENS, note=reason)
        except (ParseError, LLMError) as e:
            print(f"WARNING: margin repair failed ({e}); keeping the draft margin. "
                  "Use --repair-margin to try again.")
            return
        still = check_margin(new)
        if still:
            print(f"WARNING: repaired margin still flagged ({still}); use --repair-margin to try again.")

    def write(self) -> Path:
        margin, _, body = current_margin(self.conn, self.chunk["id"])
        folder = Path(self.out_dir) / self.slug
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "chunk-01.txt"
        path.write_text(f"{margin} {body}\n", encoding="utf-8")
        return path

    def done(self, path):
        db.set_status(self.conn, self.chunk["id"], "drafted")
        print(f"Chunk 1 drafted → {path}. QC is not built yet (Phase 3).")


def run_chunk1(conn, llm, prompts, novel_id, slug, *, module, redraft, repair,
               gen_model, qc_model, out_dir) -> int:
    c = Chunk1(conn, llm, prompts, novel_id, slug, gen_model=gen_model, qc_model=qc_model, out_dir=out_dir)
    drafted = c.chunk["status"] == "drafted"

    if drafted and not redraft and not repair:
        if module:
            print("Note: --module is ignored; chunk 1 is already drafted (use --redraft to redo it).")
        margin, _, _ = current_margin(conn, c.chunk["id"])
        print(f'Chunk 1 is drafted (margin: "{margin}"). QC is not built yet (Phase 3).')
        return 0

    if drafted and repair and not redraft:
        margin, target, _ = current_margin(conn, c.chunk["id"])
        c.repair(margin, target, FORCED)
        c.done(c.write())
        return 0

    if redraft and drafted and not module:
        last = db.latest_pass(conn, c.chunk["id"], "draft")
        module = last["module"] if last else None
    if not module:
        return c.gate()

    try:
        d = c.draft(module, note="--redraft requested" if drafted else None)
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
    c.done(c.write())
    return 0
