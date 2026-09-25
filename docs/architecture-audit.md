# PlotPilot — architecture audit

Greenfield. The only prior artifact is the v4 prompt spec (`prompts/v4-spec.md`). This document records every design decision, the architecture that follows from them, known risks, and the phase breakdown. `CLAUDE.md` is the short operating contract; this is the why behind it.

## 1. Purpose

Convert a full novel (`.txt`) into:

1. One continuous first-person-MC narration script, TTS-ready (`scripts/`).
2. A parallel metadata file of `[mm:ss] SCENE: …` lines for Phase 2 image sync (`metadata/`).

It is for solo YouTube recap creators who want consistent, textured, continuity-safe scripts at scale. Success means a novel goes in and a script plus metadata come out, and every chunk has passed fact-check. The operator intervenes only at the defined gates.

## 2. Decisions log

### 2.1 Decided by the user

| # | Topic | Decision |
|---|---|---|
| D1 | Parts | MVP treats the whole novel as one Part, so Prompt 5 runs once at the very end. Later version: detect `Part N` / `Volume N` / `Book N` headings and fall back to one Part if there are none. No fixed-size Parts. |
| D2 | Module A/B/C/D | `--module A\|B\|C\|D` per chunk. If omitted, one classification call on the chunk's first 2,000 words. Print the suggestion and stop for confirmation. No silent auto-selection. |
| D3 | Fact-check FAIL | Stop. Print the flagged lines with line numbers, MISSING/INVENTED, and the source excerpt behind each flag. The operator edits, and the next run re-checks the edited chunk. No auto-regeneration and no auto-repair. |
| D4 | Tracker update | New **Prompt 10 — Tracker Extraction** outputs a delta: new characters with stand-ins, terms, comparisons, texture motifs, chunk-end state, and nickname collisions. Written back only after operator review. No auto-merge. |
| D5 | Metadata | New **Prompt 11 — Metadata Extraction**. Timestamps are estimated at 150 words per minute. Output goes to a separate file, never into the narration. |
| D6 | Punctuation | Prompt 9's set wins: `. , ? ! : "`. No em-dashes, semicolons, ellipses, or parentheses. Prompt 1 is reworded to match. |
| D7 | Chunk size | 5 chapters maximum. "5–8" is removed from the spec. |
| D8 | Splitting | A chunk over 12k words splits at the nearest scene break. A single chapter over 12k words is its own chunk. |
| D9 | Margin repair | Automatic check on chunk 1's margin. It fires on any of: `?`; the phrases "little did", "what happened next", "unbeknownst", "before long"; a sentence starting with "But" or "And"; an average sentence length over 25 words. Log the reason. `--repair-margin` forces a repair. |
| D10 | Read-aloud | Never blocks. Print this notice after each chunk: "Chunk complete. Recommended next step: read aloud at 2x for tone and texture drift before continuing." |
| D11 | Paths | `scripts/` narration, `trackers/` one tracker per novel, `metadata/` SCENE lines, `logs/` audit output and `usage.csv`. |
| D12 | Models | Sonnet for chunk generation, Haiku for audit and fact-check. The operator can override. |
| D13 | API errors | 3 retries with exponential backoff, then a hard fail with a log entry. |
| D14 | Cost | Log token usage per pass per chunk to `logs/usage.csv`. No hard cap for the MVP. |
| D16 | Fact-check override | `--accept-factcheck="<reason>"`. An empty reason is refused. Each use appends to `logs/factcheck-overrides.log`: chunk ID, the exact MISSING/INVENTED flags overridden, the reason, and the operator. The chunk is marked `factcheck_overridden: true` with the reason. Default stays FAIL = stop. |
| D17 | Prompt 5 context limit | Before calling Prompt 5, if the assembled script exceeds the model's context window, stop. Print: "Part 1 assembled script is [N] words (~[M] tokens). Prompt 5 requires the full script as context. Exceeds [model]'s context window. Split the novel into explicit Parts or reduce chunk count." Log the word count and token estimate to `logs/errors.log`. No summarization fallback. |
| D18 | JSON schemas (Gap A) | The exact schemas live in the spec. Prompt 10: `{"new_characters": [{"name", "standin"}], "new_terms": [{"term", "meaning", "chunk": int}], "new_comparisons": [str], "new_texture_motifs": [str], "chunk_end_state": str, "nickname_collisions": [str]}`. Prompt 11: `{"scenes": [{"first_sentence", "description"}]}`. Code validates before use. On a mismatch: retry once, then fail with a clear error. |
| D19 | Target sentence (Gap B) | The CLI extracts chunk 1's target sentence from the delimiters and writes it to the tracker. If a delimiter is missing: retry once with the same prompt, then fail with a clear error. Never a manual copy. |
| D20 | Hook splice (Gap C) | Automatic. The hook block from Prompt 5 replaces chunk 1's margin block. Verify the target sentence immediately follows the spliced hook. On failure: stop and log. |
| D21 | Prompt loading (Gap D) | Parse by heading, never by line number. New prompts following the heading convention load without code changes. |
| D22 | Model IDs | Verify live model IDs before relying on them, and flag any mismatch. |
| D15 | Tests / lint / run | `pytest` covers chunking, tracker updates, delimiter splicing, and TTS rules. `ruff check .` for lint. `python plotpilot.py --novel … --out ./scripts` to run. No build step. |

Spec edits applied for D4–D8: Prompts 10 and 11 were added, and the wording in "How to use", Prompt 1, Chunking Strategy, and the QC Workflow was updated. Git history shows the verbatim original.

### 2.2 Rulings made while writing this doc (overturn any of them)

| # | Gap | Ruling | Cost if wrong |
|---|---|---|---|
| R1 | "Spec as system prompt" | The system prompt is **Prompt 1 + the selected module**. Everything else goes in the user message. The whole spec is **not** sent as a system prompt: it holds instructions for every pass, which would confuse single-pass calls. Prompt 1 + module stays stable across chunks, so it is cacheable. | Small refactor in the prompt assembler. |
| R2 | D12 / D22 model IDs | `claude-sonnet-5` (1M context, 128K max output): drafts, Prompt 3-REPAIR, Prompt 8, Prompt 5. `claude-haiku-4-5` (200K context): classification, Prompts 6, 7, 9, 10, 11. These are the current IDs per Anthropic's model table (the user's note said `claude-haiku-4.5`; the API ID uses a hyphen, `claude-haiku-4-5`). They could not be checked live from the build environment (no API key there). So at startup, before the first LLM call, `llm.py` calls `client.models.retrieve(id)` for both configured models. It fails with a clear message if either ID is unknown, and reads `max_input_tokens` for D17. IDs live in one config dict. `--gen-model` / `--qc-model` override. | Config change only. |
| R3 | When does Prompt 8 fire? | When Prompt 6's section 5 (texture gaps) is non-empty. Prompt 8 gets the whole chunk narration, and its output replaces the chunk. | Prompt 8 input scoping changes. |
| R4 | Prompt 8 runs after the fact-check | After Prompt 8, **Prompt 7 runs again**, because texture repair can introduce invention after the check. The D3 gate applies again. | One extra Haiku call on flat chunks only. |
| R5 | Scene timestamps | Prompt 11 returns `{"scenes": [{first_sentence, description}]}` (D18). The LLM does not count words. Code finds each `first_sentence` in the **assembled** script (after the hook splice) and computes offset words / 150 → `mm:ss`. The first scene is always `[00:00]`. If a sentence isn't found after whitespace/punctuation-normalized matching, it takes the previous timestamp and a warning is logged. | Timestamp logic only. |
| R6 | Tracker storage | Stored as JSON in SQLite, append-only (one row per accepted version). Rendered to `trackers/<slug>.md` in the spec's template for prompts and for reading. The Prompt 10 delta merges deterministically: append new items, dedupe exact matches. | Storage format change. |
| R7 | D2 each chunk | Taken literally: every chunk needs `--module` or a confirmed classification. The module does not carry forward. | Operator re-passes `--module` each chunk. |
| R8 | Operator edit on FAIL | The CLI writes the current narration to `scripts/<slug>/chunk-NN.txt`. On the next run, if the file's hash differs from the last stored output, the edit is recorded as an `operator_edit` pass and Prompt 7 re-runs. If it is unchanged, the CLI re-prints the FAIL and stops. | UX only. |
| R9 | Hook normalization | Prompt 9 also runs on the Prompt 5 hook before the splice. | One small Haiku call. |
| R10 | Deterministic TTS check | After Prompt 9, a pure-Python check flags digits, `;`, `—`/`–`, `...`/`…`, `(`/`)`. It warns but does not gate. It is the unit-tested half of "TTS normalization rules". | Promote to a gate if Prompt 9 proves unreliable. |
| R11 | LLM output that won't parse | Superseded by D18/D19: save the raw output, retry once with the same prompt, then fail. The chunk stays at its previous status, so a later rerun regenerates. | None. |
| R12 | Chunk 1 body vs target sentence | The parser accepts the Prompt 3 output whether or not the target sentence is repeated after the delimiter block. It is never duplicated in the stored narration. Covered by tests. | Parser only. |
| R13 | Long outputs | All LLM calls stream (`messages.stream` → final message). `max_tokens` is in config. The SDK's `max_retries=3` provides D13's backoff. | Config. |
| R15 | Where `factcheck_overridden` lives (D16) | Stored on the chunk's `factcheck` pass (`verdict = 'OVERRIDDEN'`, reason in `note`). Shown in the tracker's markdown mirror (`trackers/<slug>.md`) under "Fact-check overrides". It is **not** included in the tracker text sent to LLM prompts, because it is operator metadata, not story continuity. The operator is `getpass.getuser()`. | Also send it to prompts if wanted. |
| R16 | Gap D heading regex | The given regex `^## PROMPT (\d+) — ` misses `## PROMPT 3-REPAIR —` and the `### MODULE A —` headings. The regex used is `^## PROMPT (\d+(?:-[A-Z]+)?) — ` for prompts and `^### MODULE ([A-Z]) — ` for modules. The section runs from its heading to the next `##`/`###` heading. Inside it, the prompt text is between the `**COPY EVERYTHING BELOW…**` line and the `**END OF …**` line. | Regex only. |
| R17 | Target sentence "in the tracker" (D19) | Stored in the tracker JSON's chunk-1 margin fields (margin, margin length, target sentence), matching the spec's tracker template. | None. |
| R18 | Phase 1 ingest rules | **Packing:** D8's "a chunk over 12k splits at the nearest scene break" can only happen for a single long chapter. Greedy packing (≤5 chapters, ≤12k words) never builds a multi-chapter chunk over 12k, because it flushes at the chapter boundary first. A chapter over 12k words is split at scene breaks into parts of ≤12k, labelled `Ch N (part k/n)`, with the heading in part 1 only. **Word counts:** a single `count_words` rule applies everywhere. Scene-break lines and headings are not counted, so the sum of chunk words equals the sum of chapter words. **Contents fold:** the leading run of chapters under 50 words, or with ≥50% heading-shaped lines, is folded into front matter. A Prologue/Epilogue folds only if a later heading repeats its keyword. **Gutenberg frame:** text up to `*** START OF … PROJECT GUTENBERG` and from `*** END OF …` onward is dropped. Every drop is counted and warned about. | Parser only. |
| R14 | Tests never hit the API | The LLM client is injected. Tests use a fake that returns canned outputs. | None. |

## 3. Architecture

### 3.1 Files

```
plotpilot.py            CLI entry (argparse); calls pipeline.run()
plotpilot/
  ingest.py             chapter + scene-break detection, chunk planning (pure)
  prompts.py            extract Prompt N / Module X text from prompts/v4-spec.md; fill placeholders
  llm.py                Anthropic client wrapper: streaming, retries, usage.csv logging
  parse.py              delimiter parsing (margin, target, hook), JSON extraction, margin check (pure)
  tracker.py            tracker JSON: merge delta, render markdown (pure)
  tts_check.py          deterministic TTS hazard check (pure)
  assemble.py           hook splice, script assembly, scene timestamps (pure)
  db.py                 SQLite schema + queries
  pipeline.py           stage order, gates, resume
tests/                  one test file per pure module + pipeline tests with a fake LLM
prompts/v4-spec.md      the spec (prompt source of truth)
```

Pure modules have no I/O, which keeps them unit-testable. Only `llm.py`, `db.py`, and `pipeline.py` touch the outside world.

### 3.2 Prompt loading

Prompts are located by heading (D21, R16), never by line number. Each prompt is the text between its `**COPY EVERYTHING BELOW…**` line and its `**END OF PROMPT N**` / `**END OF MODULE X**` line. `[Paste …]` and `[paste …]` placeholders (case-insensitive) are replaced by code. A test asserts every prompt 1–11, 3-REPAIR, and modules A–D extract as non-empty and that every placeholder the code fills exists. Editing the spec therefore can't silently break the pipeline.

### 3.3 Data model (SQLite, append-only history)

- `novels(id, slug, title, source_path, source_sha256, created_at)`
- `chunks(id, novel_id, idx, label, chapter_start, chapter_end, word_count, source_text, status)`. `label` is the display range, e.g. `Ch 1–5` or `Ch 7 (part 2/3)`.
- `passes(id, novel_id, chunk_id NULL, kind, model, module NULL, input_text, output_text, verdict NULL, note NULL, created_at)`
  `kind` ∈ `classify, draft, margin_repair, audit, factcheck, operator_edit, texture, tts, tracker_delta, scenes, hook`
- `tracker_versions(id, novel_id, json, accepted_at)`

Tables are created with `CREATE TABLE IF NOT EXISTS`, which only adds tables. Phase 2+ cannot alter existing `novels` or `chunks` columns without a migration step.

Nothing is updated in place except `chunks.status`. This preserves the fair-use record: every draft and every pass is kept.

### 3.4 Chunk state machine

```
planned ──draft──▶ drafted ──P6──▶ audited ──P7 PASS──▶ checked ──(P8 → P7 again if flat)──▶ P9 ──▶ normalized
                                        │ P7 FAIL                                                     │
                                        ▼                                                             ▼
                               GATE factcheck_failed ──operator edit──▶ P7 again   (or --accept-factcheck="<reason>" ──▶ checked)         P10 + P11 ──▶ GATE tracker_pending
                                                                                                          │ --accept-tracker
                                                                                                          ▼
                                                                                                        done
Before draft: GATE needs_module (no --module) ──classify, print, stop.
After all chunks are done: context check (D17) ──▶ hook (P5 → P9) ──▶ splice + verify (D20) ──▶ assemble ──▶ metadata.
```

One `python plotpilot.py …` invocation advances the first unfinished chunk as far as it can. It stops at the first gate or at the end of the chunk and prints what to do next. Chunks run strictly in order: chunk N+1 needs chunk N's accepted tracker.

### 3.5 LLM calls

| Pass | Prompt | Model | Input | Output parsed as |
|---|---|---|---|---|
| classify | fixed short prompt: "Which module A/B/C/D fits…" | Haiku | first 2,000 words | single letter |
| draft (chunk 1) | system P1+module; user P3 + text | Sonnet | chapters | margin / target / body delimiters |
| margin_repair | P3-REPAIR | Sonnet | margin + target | margin delimiters |
| draft (2–N) | system P1+module; user P4 + tracker + text | Sonnet | chapters | plain text |
| audit | P6 | Haiku | source + narration + tracker | 7 sections (section 5 is read) |
| factcheck | P7 | Haiku | source + narration | `PASS`/`FAIL` + flagged lines |
| texture | P8 | Sonnet | narration + tracker | plain text |
| tts | P9 | Haiku | narration | plain text |
| tracker_delta | P10 | Haiku | tracker + narration | JSON |
| scenes | P11 | Haiku | narration | JSON array |
| hook | P5 | Sonnet | assembled script + target | hook delimiters |

## 4. Risks and open items

1. **Chapter detection on messy `.txt`.** A heading is a line that is either the first line or follows a blank line, **and** is one of:
   - `Chapter`/`Ch.` followed by a numeral, a roman numeral (which must end the line or be followed by `: . - —`), or a number word such as `Twenty-One`, with the rest of the line ≤80 chars;
   - `Prologue`/`Epilogue`, optionally followed by punctuation and a title.

   If no chapter is found, the run fails with a clear message. A chapter sequence that goes backwards triggers a warning, not a failure. A `--chapter-regex` flag is deferred until a real novel needs it.

   Known limits, none fixed in Phase 1:
   - A roman-numeral heading whose title follows with no punctuation (`CHAPTER IV THE FALL`) is not detected. That chapter merges into the one before it.
   - Bare-number headings (`1.`, `1`) are not detected.
   - `Ch 5` without a period is not detected.
   - `Book One` / `Part One` are deliberately treated as body text, since Part handling is deferred (D1). Books that restart at `Chapter 1` produce a harmless sequence warning at each restart.
   - A heading directly after a scene-break line, with no blank line between them, is not detected.
2. **Scene-break detection.** Lines of three or more `*`, `#`, `~`, `-`, or `=` characters (spaces allowed). If a chunk over 12k words has no scene break, it stays one oversize chunk and a warning is logged.
3. **Prompt 5 context size.** D1 makes the whole novel one Part, so Prompt 5's input is the whole script. Handled by D17: stop with a clear error. Phase 1 also warns early, using a deterministic estimate (source words × 1.35 tokens per word, as an upper bound on narration size, against the configured generation model's context size). Real Part detection (D1's later version) fixes it properly.
4. **Haiku fact-check strictness** on ~12k-word chunks may over-flag paraphrases. Handled by D16 (logged, reasoned override).
5. **Delimiter compliance.** If the model skips delimiters, R11 makes the run fail and a rerun regenerates. Monitor how often this happens.
6. **Fair use.** Out of scope for code, but everything is kept (§3.3).

## 5. Phases

Each phase leaves a runnable CLI and a green `pytest` + `ruff check .`.

| Phase | Scope | Done when |
|---|---|---|
| **1 — Ingest + plan** | Repo skeleton, `db.py`, `ingest.py`, CLI that plans chunks, stores them, prints a manifest (chapter ranges, word counts), and exits. Flags any chunk still over 12k words (no scene break to split at) and warns if the novel likely exceeds Prompt 5's context (D17 estimate). No LLM calls, no network. | Chunk-packing, scene-split, and chapter-detection tests pass. Rerunning on the same novel is idempotent. |
| **2 — Chunk 1 draft** | `prompts.py`, `llm.py` (startup model-ID check, streaming, retries, usage.csv), `parse.py`, module gate + classification, Prompt 3 draft, margin check + 3-REPAIR (auto and `--repair-margin`). | Chunk 1 draft stored with margin and target. Prompt extraction, delimiter parsing, and margin-check tests pass with the fake LLM. |
| **3 — QC chain** | Prompts 6 → 7 (FAIL gate + operator-edit re-check + `--accept-factcheck`) → 8 → 7 again → 9, plus `tts_check.py` and the read-aloud notice. | Gate and resume paths are tested with the fake LLM. TTS check tests pass. |
| **4 — Tracker + chunks 2–N** | `tracker.py`, Prompt 10 + review gate + `--accept-tracker`, Prompt 11, Prompt 4 drafting for chunks 2–N. | A multi-chunk run on the fake LLM completes to all chunks `done`. Tracker merge and render tests pass. |
| **5 — Hook + assemble + metadata** | D17 context check, Prompt 5 → 9 on the hook, splice + verify, full script assembly, metadata timestamps. | Script + metadata files are written. Splice and timestamp tests pass. One real run on a short public-domain novel completes. |

Deferred until a real need exists: Part detection, a web UI, a cost cap, `--chapter-regex`, and Phase 2 image sync.
