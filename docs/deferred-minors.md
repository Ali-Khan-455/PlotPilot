# Deferred minor issues

Minor findings from each phase's final code review. They were deliberately left out of that phase's fix pass: none of them corrupts output or loses data. Fix them whenever it's convenient, write a failing test first, and delete the entry once it's fixed.

## Open decisions (need the user)

- **Digit / number-word headings have no prose guard** (Phase 1, `plotpilot/ingest.py` `CHAPTER_RE`). A wrapped prose line after a blank line, such as `Chapter one of my life was over.` or `Chapter 12 of the regulations forbade it.`, is taken as a chapter heading. The sequence warning catches it only when the number goes backwards or repeats. The options are a guard like the roman-numeral one (the number must end the line or be followed by punctuation), or leaving it as is. It is also listed in `docs/architecture-audit.md`, risk 1.

## Phase 1 — Ingest + chunk plan

- [ ] **Chapter numbers above 100 are misread** (`ingest.py`, the `CHAPTER_RE` number group allows at most two words). `Chapter One Hundred One` parses as 100. Only the sequence warning is affected.
- [ ] **Words made only of roman-numeral letters are read as roman numerals** (`ingest.py`, `CHAPTER_RE`). `Chapter mix.` is a heading, numbered 1009. Words like `did` and `civil` behave the same way.
- [ ] **A short real Prologue is folded as contents if a later heading also starts with "Prologue"** (`ingest.py`, `heading_key`). A 20-word `Prologue` is dropped when a later `Prologue: Part Two` exists.
- [ ] **Gutenberg leftovers** (`ingest.py`, `parse_novel`).
  - The `End of the Project Gutenberg EBook of X` line just before `*** END …` stays in the last chapter and reaches the LLM.
  - Old-style `*END*THE SMALL PRINT!` headers aren't recognised by name. They are still dropped as front matter; only the warning's wording is affected.
- [ ] **CLI error paths** (`cli.py`).
  - `PermissionError` on the novel file, or a read-only working directory for `plotpilot.db`, ends in a traceback.
  - The file is read twice (once for the sha, once for the text). A file that changes between the two reads could store a sha that doesn't match the parsed text.
  - Error messages go to stdout, not stderr.
- [ ] **The stored `source_path` is relative** (`cli.py`, `save_plan`). The "already planned from <path>" message is ambiguous from another directory. Use `path.resolve()`.
- [ ] **The manifest prints "1 chunks"** (`cli.py`, `_print_manifest`).
- [ ] **Header and rows can disagree on a rerun after a parser change** (`cli.py`). The header totals come from a fresh parse, but the rows come from the stored plan.
- [ ] **Labels and warnings use sequential chapter indices, not the book's own numbering** (`ingest.py` / `cli.py`). This matters when there's a Prologue, or when the contents fold drops entries. Consider showing the heading text in warnings.

## Phase 2 — Chunk 1 draft

- [ ] **A classification that stops at `max_tokens` is not retried** (`pipeline.py`, `_attempt`, `CLASSIFY_MAX_TOKENS=16`). A chatty reply exits 1 on the first try. Treat `max_tokens` on classify as a parse failure so retry-once applies.
- [ ] **The model-caching test at pipeline level is vacuous** (`tests/test_pipeline.py::test_classify_then_draft_retrieves_each_model_once`). Each `main()` builds a fresh `LLM`. The unit test in `tests/test_llm.py` does cover caching.
- [ ] **The classify `input_text` assertion is weak** (`tests/test_pipeline.py::test_gate_classifies_once_and_reuses`). Assert `"=====" not in input_text` instead of checking only the start.
- [ ] **`FakeClient` never returns a non-text block** (`tests/fakes.py`). The text-only join in `llm.py` is untested against thinking blocks, which Sonnet 5 emits by default.
- [ ] **`check_margin` misses quoted sentence openings** (`parse.py`). `"But I was poor," I said.` does not trigger the `But/And` rule.
- [ ] **The margin count cap is loose** (`parse.py`). The cap is `> 9`, but Prompt 3 allows at most 2 sentences. Nothing checks the length of a repaired margin either.
- [ ] **A forced repair ignores `--module` silently** (`pipeline.py`, the `drafted and repair` branch). The plain drafted path prints a note.
- [ ] **"Malformed twice" failures are not written to `errors.log`** (`pipeline.py`, draft and classify). They exit 1 with a message only.
- [ ] **A spec section with a COPY line but no END line crashes with a bare `StopIteration`** (`prompts.py`, `load_prompts`). Raise a clear error naming the section instead.
- [ ] **No usage row is written when a call fails mid-stream** (`llm.py`, `call`). If `get_final_message` raises, nothing is logged. This may be unavoidable.
