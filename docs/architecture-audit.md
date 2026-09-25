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
| D15 | Tests / lint / run | `pytest` covers chunking, tracker updates, delimiter splicing, and TTS rules. `ruff check .` for lint. `python plotpilot.py --novel … --out ./scripts` to run. No build step. |

Spec edits applied for D4–D8: Prompts 10 and 11 were added, and the wording in "How to use", Prompt 1, Chunking Strategy, and the QC Workflow was updated. Git history shows the verbatim original.

### 2.2 Rulings made while writing this doc (overturn any of them)

| # | Gap | Ruling | Cost if wrong |
|---|---|---|---|
| R1 | "Spec as system prompt" | The system prompt is **Prompt 1 + the selected module**. Everything else goes in the user message. The whole spec is **not** sent as a system prompt: it holds instructions for every pass, which would confuse single-pass calls. Prompt 1 + module stays stable across chunks, so it is cacheable. | Small refactor in the prompt assembler. |
| R2 | D12 for the other passes | Sonnet: drafts, Prompt 3-REPAIR, Prompt 8, Prompt 5. Haiku: classification, Prompts 6, 7, 9, 10, 11. Model IDs live in one config dict. `--gen-model` / `--qc-model` override. | Config change only. |
| R3 | When does Prompt 8 fire? | When Prompt 6's section 5 (texture gaps) is non-empty. Prompt 8 gets the whole chunk narration, and its output replaces the chunk. | Prompt 8 input scoping changes. |
| R4 | Prompt 8 runs after the fact-check | After Prompt 8, **Prompt 7 runs again**, because texture repair can introduce invention after the check. The D3 gate applies again. | One extra Haiku call on flat chunks only. |
| R5 | Scene timestamps | Prompt 11 returns `{scene, first_sentence}` JSON. The LLM does not count words. Code finds each `first_sentence` in the **assembled** script (after the hook splice) and computes offset words / 150 → `mm:ss`. The first scene is always `[00:00]`. If a sentence isn't found after whitespace/punctuation-normalized matching, it takes the previous timestamp and a warning is logged. | Timestamp logic only. |
| R6 | Tracker storage | Stored as JSON in SQLite, append-only (one row per accepted version). Rendered to `trackers/<slug>.md` in the spec's template for prompts and for reading. The Prompt 10 delta merges deterministically: append new items, dedupe exact matches. | Storage format change. |
| R7 | D2 each chunk | Taken literally: every chunk needs `--module` or a confirmed classification. The module does not carry forward. | Operator re-passes `--module` each chunk. |
| R8 | Operator edit on FAIL | The CLI writes the current narration to `scripts/<slug>/chunk-NN.txt`. On the next run, if the file's hash differs from the last stored output, the edit is recorded as an `operator_edit` pass and Prompt 7 re-runs. If it is unchanged, the CLI re-prints the FAIL and stops. | UX only. |
| R9 | Hook normalization | Prompt 9 also runs on the Prompt 5 hook before the splice. | One small Haiku call. |
| R10 | Deterministic TTS check | After Prompt 9, a pure-Python check flags digits, `;`, `—`/`–`, `...`/`…`, `(`/`)`. It warns but does not gate. It is the unit-tested half of "TTS normalization rules". | Promote to a gate if Prompt 9 proves unreliable. |
| R11 | LLM output that won't parse | Missing delimiters or invalid JSON: save the raw output and fail the run. The chunk stays at its previous status, so a rerun regenerates. No extra flag. | Add auto-retry later if frequent. |
| R12 | Chunk 1 body vs target sentence | The parser accepts the Prompt 3 output whether or not the target sentence is repeated after the delimiter block. It is never duplicated in the stored narration. Covered by tests. | Parser only. |
| R13 | Long outputs | All LLM calls stream (`messages.stream` → final message). `max_tokens` is in config. The SDK's `max_retries=3` provides D13's backoff. | Config. |
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

Each prompt is the text between its `**COPY EVERYTHING BELOW…**` line and its `**END OF PROMPT N**` / `**END OF MODULE X**` line. `[Paste …]` and `[paste …]` placeholders (case-insensitive) are replaced by code. A test asserts every prompt 1–11, 3-REPAIR, and modules A–D extract as non-empty and that every placeholder the code fills exists. Editing the spec therefore can't silently break the pipeline.

### 3.3 Data model (SQLite, append-only history)

- `novels(id, slug, title, source_path, source_sha256, created_at)`
- `chunks(id, novel_id, idx, chapter_start, chapter_end, word_count, source_text, status)`
- `passes(id, novel_id, chunk_id NULL, kind, model, module NULL, input_text, output_text, verdict NULL, created_at)`
  `kind` ∈ `classify, draft, margin_repair, audit, factcheck, operator_edit, texture, tts, tracker_delta, scenes, hook`
- `tracker_versions(id, novel_id, json, accepted_at)`

Nothing is updated in place except `chunks.status`. This preserves the fair-use record: every draft and every pass is kept.

### 3.4 Chunk state machine

```
planned ──draft──▶ drafted ──P6──▶ audited ──P7 PASS──▶ checked ──(P8 → P7 again if flat)──▶ P9 ──▶ normalized
                                        │ P7 FAIL                                                     │
                                        ▼                                                             ▼
                               GATE factcheck_failed ──operator edit──▶ P7 again          P10 + P11 ──▶ GATE tracker_pending
                                                                                                          │ --accept-tracker
                                                                                                          ▼
                                                                                                        done
Before draft: GATE needs_module (no --module) ──classify, print, stop.
After all chunks are done: hook (P5 → P9) ──▶ splice ──▶ assemble ──▶ metadata.
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

1. **Chapter detection on messy `.txt`.** The default heading regex is `Chapter N` / `CHAPTER N` / `Ch. N` on its own line. If no chapter is found, fail with a clear message. A `--chapter-regex` flag is deferred until a real novel needs it.
2. **Scene-break detection.** Lines of three or more `*`, `#`, `~`, `-`, or `=` characters (spaces allowed). If a chunk over 12k words has no scene break, it stays one oversize chunk and a warning is logged.
3. **Prompt 5 context size.** D1 makes the whole novel one Part, so Prompt 5's input is the whole script. A long novel can exceed the context window. MVP: fail clearly if the input is too large. Real Part detection (D1's later version) fixes this.
4. **Haiku fact-check strictness** on ~12k-word chunks may over-flag paraphrases, and D3 has no override. **Open question for the user:** add an explicit `--accept-factcheck` operator override, or keep the edit-only path?
5. **Delimiter compliance.** If the model skips delimiters, R11 makes the run fail and a rerun regenerates. Monitor how often this happens.
6. **Fair use.** Out of scope for code, but everything is kept (§3.3).

## 5. Phases

Each phase leaves a runnable CLI and a green `pytest` + `ruff check .`.

| Phase | Scope | Done when |
|---|---|---|
| **1 — Ingest + plan** | Repo skeleton, `db.py`, `ingest.py`, CLI that plans chunks, stores them, prints the plan, and exits. No LLM. | Chunk-packing, scene-split, and chapter-detection tests pass. Rerunning on the same novel is idempotent. |
| **2 — Chunk 1 draft** | `prompts.py`, `llm.py` (streaming, retries, usage.csv), `parse.py`, module gate + classification, Prompt 3 draft, margin check + 3-REPAIR (auto and `--repair-margin`). | Chunk 1 draft stored with margin and target. Prompt extraction, delimiter parsing, and margin-check tests pass with the fake LLM. |
| **3 — QC chain** | Prompts 6 → 7 (FAIL gate + operator-edit re-check) → 8 → 7 again → 9, plus `tts_check.py` and the read-aloud notice. | Gate and resume paths are tested with the fake LLM. TTS check tests pass. |
| **4 — Tracker + chunks 2–N** | `tracker.py`, Prompt 10 + review gate + `--accept-tracker`, Prompt 11, Prompt 4 drafting for chunks 2–N. | A multi-chunk run on the fake LLM completes to all chunks `done`. Tracker merge and render tests pass. |
| **5 — Hook + assemble + metadata** | Prompt 5 → 9 on the hook, splice, full script assembly, metadata timestamps. | Script + metadata files are written. Splice and timestamp tests pass. One real run on a short public-domain novel completes. |

Deferred until a real need exists: Part detection, a web UI, a cost cap, `--chapter-regex`, and Phase 2 image sync.
