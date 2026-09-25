# PlotPilot — operating contract

PlotPilot converts a full novel (`.txt`) into a TTS-ready, first-person-MC YouTube narration script, plus a parallel scene-metadata file for later image sync. Local Python CLI. Solo recap-channel creators are the users.

- **Source of truth for pipeline logic and every LLM prompt:** `prompts/v4-spec.md` (imported below). Code loads prompt text from that file. Never paraphrase or inline prompt text in code.
- **Architecture, decisions, and phase scope:** `docs/architecture-audit.md`. Read it before planning any phase.

## Stack

- Python 3.11+, stdlib first. Runtime dependency: `anthropic` only. Dev: `pytest`, `ruff`.
- CLI: `argparse`. No web framework (FastAPI only if a web UI is ever needed).
- State: SQLite (`sqlite3` stdlib) — chunk plan, every pass output, tracker, gates.
- Artifacts on the local filesystem: `scripts/`, `trackers/`, `metadata/`, `logs/`.
- LLM: Anthropic API. Models are set in one config place, overridable by CLI flag.

## Commands

```bash
python plotpilot.py --novel path/to/novel.txt --out ./scripts   # run / resume
pytest                                                          # tests
ruff check .                                                    # lint
```

No build step.

## Pipeline order

Run strictly in this order. Every stage persists to SQLite before the next starts, so any run can resume.

1. **Ingest + plan chunks.** Detect chapters. Pack ≤5 chapters and ≤12k words per chunk. A chunk over 12k words splits at the nearest scene break. A single chapter over 12k words is its own chunk. Plan every boundary before any LLM call.
2. **Chunk 1 draft.** Prompt 1 + Prompt 2 (module) + Prompt 3 + chapter text. Parse `<<<MARGIN_…>>>` / `<<<TARGET_SENTENCE_…>>>` delimiters. Run the automatic margin check. If it fires, run Prompt 3-REPAIR.
3. **Chunks 2–N draft.** Prompt 1 + Prompt 2 + Prompt 4 + Continuity Tracker + chapter text.
4. **Per-chunk QC, in order:** Prompt 6 (self-audit) → Prompt 7 (fact-check; FAIL = gate) → Prompt 8 (texture repair, only if Prompt 6 reports texture gaps; then Prompt 7 again) → Prompt 9 (TTS normalization) → deterministic TTS check.
5. **Tracker update.** Prompt 10 produces a JSON delta. Operator reviews it, then it merges into the tracker. Prompt 11 extracts scenes for this chunk.
6. **Deferred hook.** After the last chunk: Prompt 5 runs once per novel. Splice the hook in place of chunk 1's margin.
7. **Assemble** all chunks into one continuous script.
8. **Emit metadata** `[mm:ss] SCENE: …`, with timestamps from word offsets at 150 words per minute.

MVP: the whole novel is one Part. The hook runs once, at the very end.

## Gates (the CLI stops and exits; the next run resumes)

- **Module not chosen:** without `--module A|B|C|D`, the CLI classifies the chunk's first 2,000 words, prints the suggestion, and stops. It never selects a module silently.
- **Fact-check FAIL:** print the flagged lines, MISSING/INVENTED, and the backing source excerpt. The operator edits the chunk file. Next run re-checks the edited text. Never auto-regenerate or auto-repair past a FAIL.
- **Tracker review:** write the pending delta to `trackers/`. Merge only on `--accept-tracker`.
- **Read-aloud:** never blocks. Print the notice after each chunk: "Chunk complete. Recommended next step: read aloud at 2x for tone and texture drift before continuing."

## Non-negotiable rules for code changes

- Do not change the pipeline order, the gates, or the prompt text without the user's explicit approval. Spec edits go in `prompts/v4-spec.md` only.
- Keep every draft and every pass output (fair-use record). Never overwrite history in SQLite. Append.
- Narration files contain only narration: no delimiters, no scene markers, no headers.
- Log token usage for every LLM call to `logs/usage.csv`.

## How we work (Claude Code sessions)

- **Planning a phase:** `caveman` + `ponytail`. Use `EnterPlanMode`. Run the `plan-reviewer` subagent on the draft plan and fix its findings before `ExitPlanMode`. Use `brainstorming` only if the phase's scope is not already clear from this file and `docs/architecture-audit.md`.
- **Implementing an approved phase:** `caveman` + `ponytail` + `executing-plans`. Implement the plan exactly. Make no silent architectural changes and add no scope.
- **Before declaring a phase done:** `verification-before-completion`. Run the full `pytest` suite and `ruff check .`, then walk the plan's step list against the shipped code line by line.
- **Debugging:** use `systematic-debugging` only when a test fails or a bug appears.

## Spec

@prompts/v4-spec.md
