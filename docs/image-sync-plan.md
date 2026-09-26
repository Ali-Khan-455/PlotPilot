# Image-Sync Engine — plan (for review, nothing built yet)

## Context

PlotPilot turns a novel into a narration script (`scripts/<slug>/script.txt`, per-chunk files) plus scene metadata (`metadata/<slug>.txt`, `[mm:ss] SCENE: …` at 150 wpm). The **Image-Sync Prompt Engine v3** is a markdown prompt doc that an operator pastes into Claude chunk by chunk. It turns PlotPilot's output into image prompts for Google Flow and keeps a hand-pasted **Visual Bible** for continuity. The work has three stages:

- **Stage 0:** beats.
- **Stage 1:** character, location and object references, then a reference approval gate.
- **Stage 2:** batches of about 30 image prompts, a manifest, and a Bible update.

This plan turns that doc into a tool in the same style as PlotPilot:
- an argparse CLI;
- SQLite state, with history append-only;
- prompts loaded from a spec file by heading;
- gates that stop the run and a next run that resumes;
- TDD against a fake client;
- phases, each reviewed before implementation.

**Fixed (not revisited here):**
- beat-based segmentation;
- reference-driven consistency;
- continuity-locked state;
- portability to SDXL+LoRA;
- no summarization fallback for context limits.

## Decisions on the seven questions

### 1. Prompt or program → **hybrid**

A program owns the stages, the gates, the state and every rule that can be checked mechanically. Claude does only the judgment work:
- segmenting beats and recovering visual detail;
- writing reference descriptions;
- composing each scene.

Several v3 rules are mechanical, so code guarantees them instead of trusting the model to follow them:

| v3 rule | Enforced by |
|---|---|
| Never invent a timestamp. Split beats share a timestamp with a/b suffixes. Chronological order. | code: validates every beat timecode against the chunk's metadata scenes |
| Source tag required on recovered detail | code: parse check, retry once |
| Cadence warning (outside 3–20 s for more than 3 beats in a row) | code: computed from the timecodes, so it's never missed |
| Slot policy (5 characters / 14 objects, locked, fallback) | code: the Bible merge assigns slots and the LLM never does |
| Unique CamelCase `#Tag`s | code |
| Batch pre-check (every `@Name` approved) | code: runs before any Stage 2 call, so no partial batch |
| Locked style suffix, word for word | code: appends it, and the LLM never writes it |
| Shot cadence (no more than 3 of the same shot in a row, a wide shot every 5–7 beats, 9:16 avoids wide shots) | code: QA check and one retry of that batch; warns after that |
| `@Name` used only for approved references present in the beat, never stacked | code: QA check |
| Manifest CSV, `beat_<M-SS>.png` names | code: generated from stored prompts |
| Continuity log and revision log are append-only | code: the Bible merge |

**Why hybrid:**
- A pure prompt doc can't enforce any of these rules, and the drift they cause is exactly what v3 fixes by hand.
- A pure program can't do the creative work.
- The hybrid mirrors PlotPilot, where Claude drafts and code does the parsing, gates and merges.

### 2. Where it lives → **same repo, separate package**

Add a new top-level package, `imagesync/`, with its own CLI (`imagesync.py`) and its own database (`imagesync.db`). Details:
- **It imports only PlotPilot's stable pure parts:**
  - `plotpilot.llm` (the client wrapper, model check and usage log);
  - `plotpilot.prompts.fill` and a generalized heading loader;
  - `plotpilot.assemble` (to recompute per-chunk scene timestamps exactly as PlotPilot did);
  - `tests/fakes.py`.
- **It reads `plotpilot.db` read-only** (`sqlite3.connect("file:plotpilot.db?mode=ro", uri=True)`) through one `imagesync/source.py` module. PlotPilot never imports imagesync, so there is one job per package and one owner per database.
- **Why not a sibling repo or a fork:** both would duplicate `llm.py`, `prompts.py`, the fakes and the CI. The scene-timestamp logic would also drift from `assemble.py`.
- **Why not inside `plotpilot/`:** it would mix two pipelines with different gates and state in one package, and blur CLAUDE.md's "one job per module" rule.
- **Prompt text** lives in `prompts/image-sync-v3.md`. It is the v3 spec verbatim plus the tool output contracts (see open question Q1). Code never paraphrases it.

### 3. Visual Bible storage → **JSON in SQLite (source of truth) + a markdown mirror**

This follows the `tracker_versions` precedent (R21).

- **Storage:** a `bible_versions(id, novel, chunk_idx, stage, json, delta, accepted_at)` table. Each accepted update inserts a new row; rows are never updated.
- **Merge:** deterministic, in a pure module `imagesync/bible.py`. It never deletes. It appends a superseded state as a new continuity entry. It assigns slots in order of first appearance and marks overflow as `fallback`. It rejects a duplicate `#Tag`.
- **Mirror:** `bibles/<slug>.md`, rendered in **exactly** v3's `=== VISUAL BIBLE ===` block format and regenerated on every run. It is read-only, like `trackers/<slug>.md`.
- **What the prompts get:** a rendering from the same function, never a hand-edited file.
- **Review surface:** operator edits go through a bound pending JSON file, as with the tracker gate.

### 4. Flow integration → **manual paste, tracked by gates**

The plan assumes no Flow API (see Q3). The tool writes paste-ready files and records the operator's decisions:
- **Reference prompts:** `images/<slug>/chunk-NN/refs.txt`.
- **Batches:** `images/<slug>/chunk-NN/batch-K.txt`.
- **Approval:** `--approve-refs` records a `refs_approved` pass. After that, the Bible marks each reference `reference generated: yes`.
- **Regeneration:** `--regenerate "#Name: reason"` re-runs Stage 1 for that one reference, with the reason, and the gate is held again.
- **Optional file check:** if the operator saves reference images to `images/<slug>/refs/<Tag>.png`, the tool reports missing ones before approval, as a warning only.
- **Portability:** everything Flow-specific (`#Name` / `@Name` syntax, the 5/14 slot limits) sits in one `imagesync/target.py`. Adding SDXL+LoRA later means swapping that module, as v3's portability section intends.

### 5. Batch orchestration → **automatic batches, operator-controlled gates**

The chat-era "next" only existed because of chat output limits. The tool loops through batches of about 30 beats by itself:
- each batch is one Stage 2 call, persisted as soon as it's done, so a crash resumes at the next batch;
- all of a chunk's batches are written in one run;
- the operator pastes `batch-1.txt`, `batch-2.txt` and so on at their own pace.

Operator control stays where a decision is made:
- **Style lock gate** (chunk 1 only): `--sub-style a|b|c|d --aspect 16:9|9:16|1:1|4:5`. The tool suggests a sub-style from PlotPilot's dominant module and never picks one silently, like the module gate.
- **Reference approval gate** (per chunk, when new references exist).
- **Bible review gate** (end of a chunk): `--accept-bible`. The continuity-log delta is reviewed before the next chunk builds on it, as with PlotPilot's D4.
- **Beat revision** (any time): `--revise-beat 04-15 "new description"` changes only that beat, logs it in the revision log, and re-emits only that beat's Stage 2 prompt.

### 6. Context window → **bounded inputs by construction + a hard pre-check, no summarization**

The inputs are bounded as follows:
- **Stage 0:** one PlotPilot chunk (at most 12k words of source), its narration, and its scene lines. The chunk size limits this.
- **Stage 1:** the chunk's beat list plus a **tag index** of the Bible: every entry's name, tag, slot and locked descriptor. It excludes the continuity and revision logs, because Stage 1 only needs to know what already exists, not its history.
- **Stage 2, one batch:**
  - that batch's beats, plus the last 3 beats of the previous batch for shot-cadence context;
  - the style lock;
  - the full entries for **the references and elements named in those beats**, each with its current state (the newest continuity entry per element);
  - for a `CONTINUES` beat, the previous chunk's last beat and shot.

These are **deterministic selections of verbatim records**, not summaries. No text is rewritten or condensed, and whatever is sent is exact. Before every call, `llm.count_tokens` is compared with the model's `max_input_tokens`, as D17 did. If a call would exceed the limit, the run stops with a clear error and no fallback. Over the limit is only reachable if a single chunk's beats or Bible entries are enormous, which PlotPilot's chunk cap prevents in practice.

This deviates from v3's "paste the (whole) Visual Bible into every stage" (see Q2).

### 7. Validation → **yes, with stdlib validators in the `tracker.validate_delta` style**

- **What is checked:**
  - every model output (beats, reference proposals and Bible delta, batch scenes);
  - every accepted Bible version;
  - every hand-edited pending file.
- **How:** exact keys, exact types and enumerated values (shot types, sub-styles, aspects, states, genres). A failure means retry once, then fail clearly (D18).
- **Why not Pydantic:** it would add a direct runtime dependency, against CLAUDE.md's "anthropic only". It's installed transitively, but relying on that is fragile (see Q4). The Bible schema is small and flat enough for hand-written validators, as `tracker.py` shows.

## Design

**Data flow per chunk:**

```
plotpilot.db (read-only)  →  source.py: chunk narration, chapters, per-chunk scene timestamps
                                        (recomputed with plotpilot.assemble), module, tracker
      ↓
[style lock gate, chunk 1]  →  Stage 0 (beats JSON) → validate → beats stored
      ↓
Stage 1 (new refs + Bible delta JSON) → slot assignment in code → refs.txt → [approval gate]
      ↓
batch pre-check (code) → Stage 2 per batch (scene JSON) → code composes prompt + style suffix → QA → batch-K.txt
      ↓
manifest.csv (code) → Bible continuity delta → [Bible review gate] → next chunk
```

**Chunk states:**

```
ready → beats → refs_pending → refs_approved → batching → bible_pending → done
```

Every transition is one atomic pass-plus-status write, as with `add_pass(new_status=)`.

**Scene timestamps per chunk:** PlotPilot's metadata file is novel-wide. The tool recomputes each chunk's scene lines by calling `plotpilot.assemble.scene_lines` on PlotPilot's stored data. That gives exactly the same `[mm:ss]` values, plus the chunk boundary, so there's no need to parse ranges out of the text file.

**Stage 2 composition** is split between the model and code:
- **The model returns** `{timecode, shot_type, scene, refs[], genre_override|null}`.
- **Code builds the final prompt:** `"{shot}, {scene}, {@refs}, {style suffix}"`. The style suffix is `LOCKED_SUFFIX` filled with the sub-style descriptor, the colour treatment and the aspect from the Bible.
- **QA:** code rejects a `@ref` that isn't approved, isn't in the beat, or is stacked.

## Phases

| Phase | Scope | Done when |
|---|---|---|
| **IS-1 Foundation** | The `imagesync/` package and `imagesync.py` CLI (`--novel <slug>`). `source.py` reads `plotpilot.db` read-only and refuses unless PlotPilot has finished the novel (every chunk done, `script.txt` exists). `prompts/image-sync-v3.md` holds the spec verbatim, and the loader is generalized by passing a heading regex. The `imagesync.db` schema covers novels, chunks, passes and `bible_versions`. The style lock gate stores the first Bible version. There are no LLM calls. | A manifest of chunks with scene counts prints. The style lock gate works. Scene timestamps equal PlotPilot's `metadata/<slug>.txt`, checked in a test. |
| **IS-2 Stage 0** | The beats call, beat JSON validation (the timestamp subset, a/b suffixes, order, source tags, `CONTINUES` only on beat 1 and only when the previous chunk's final continuity entry is an open scene), the cadence warning, `--revise-beat` before Stage 2, and the error, usage and retry conventions. | Chunk 1 beats are stored and a rerun makes no calls. Every hard rule has a failing-then-passing test. |
| **IS-3 Bible + Stage 1** | The `bible.py` schema, merge and render (the exact v3 format), slot assignment and fallback, unique tags with transliteration kept, the Stage 1 call, `refs.txt`, the approval gate, `--regenerate`, and the mirror. | Two-chunk test: chunk 2 doesn't re-create chunk 1's references, overflow gets `fallback`, and the render matches the v3 block exactly. |
| **IS-4 Stage 2** | The batch pre-check, per-batch calls, composition in code, QA (shot cadence, aspect, refs, red-X and text rules checked only as far as they can be checked), a retry per batch, `batch-K.txt`, `manifest.csv`, the Bible continuity delta, the review gate, and `CONTINUES` framing across chunks. | A two-chunk run ends at `done`. The style suffix is byte-exact on every prompt. A crash between batches resumes at the next batch. |
| **IS-5 Revisions + image check** | `--revise-beat` after Stage 2 re-emits one prompt, with a revision log entry. `--check-images` compares `images/<slug>/chunk-NN/beat_*.png` against the manifest and reports missing or extra files. | The revision touches only that beat. The image report is correct in tests. |

Each phase follows PlotPilot's workflow: a plan in plan mode, `plan-reviewer`, approval, TDD, a final review, and minor findings into `docs/deferred-minors.md`.

## Files (target)

```
imagesync.py                 shim → imagesync.cli
imagesync/cli.py             argparse, gates, stdout/stderr split as PlotPilot
imagesync/source.py          read-only access to plotpilot.db + assemble reuse
imagesync/db.py              imagesync.db schema, append-only helpers
imagesync/beats.py           Stage 0 contract: validate, cadence (pure)
imagesync/bible.py           Visual Bible schema, merge, slots, render (pure)
imagesync/target.py          Flow specifics: #/@ syntax, slot limits, suffix (pure)
imagesync/compose.py         Stage 2 prompt composition + QA checks + manifest (pure)
imagesync/pipeline.py        stage order, gates, resume
imagesync/config.py          models, batch size (30), paths
prompts/image-sync-v3.md     the spec (source of truth for prompt text)
plotpilot/prompts.py         load_prompts(path, heading_re=…) generalization (backward compatible)
tests/test_imagesync_*.py    one file per pure module + pipeline tests with FakeClient
docs/image-sync-audit.md     decisions and rulings (IS-D1…), like architecture-audit.md
```

## TDD steps (IS-1, as the first review target)

1. `plotpilot.prompts.load_prompts(path, heading_re)`:
   - PlotPilot's existing prompt tests pass unchanged;
   - a fixture spec with `## STAGE 0 — …` headings loads by heading.
2. `source.py`, using a PlotPilot database built by the existing test helpers (`all_done`):
   - it returns every chunk's narration, chapter text and scene lines;
   - it refuses an unfinished novel with a clear error;
   - it never writes: a test compares a checksum of the database file before and after.
3. The per-chunk scene timestamps equal the lines in the `metadata/<slug>.txt` that PlotPilot wrote, in a byte-level comparison.
4. The `imagesync.db` schema and `add_pass(new_status=)` are atomic.
5. The style lock gate:
   - without flags, it prints the suggested sub-style (from the dominant module) and exits 0;
   - with flags, it stores Bible version 1;
   - invalid values are refused;
   - a rerun is idempotent.
6. Verify: `pytest`, `ruff check .` (with the pinned rules), a fresh final review, and the minor findings into `docs/deferred-minors.md`.

## Out of scope

- Flow or any image API calls.
- Automatic image download.
- The SDXL+LoRA backend, beyond keeping `target.py` swappable.
- Mode B (TurboScribe input) until a real need exists (see Q5).
- A video editor.
- A web UI.
- Summarization of any kind.
- Prompt caching.

## Verification (whole project)

- The full suite runs against `FakeClient` with no network.
- A two-chunk end-to-end fixture goes from a finished PlotPilot database to `done`.
- A byte-exact style suffix is checked on every prompt.
- The Bible render equals the v3 block format.
- A crash or resume test covers every gate.
- One real run on the same public-domain novel PlotPilot is first run on. It is operator-side, because there's no API key or Flow access here.

## Open questions (can't be decided from the repo or the spec)

- **Q1 — Output contracts.** v3's stages emit free-text blocks. For code to validate them, Stages 0, 1 and 2 need JSON output contracts. The proposal is to append a "TOOL OUTPUT CONTRACTS" section to `prompts/image-sync-v3.md` and change nothing else. That is a spec edit, which needs your approval. The alternative is to parse v3's free-text formats, which is fragile, especially Stage 2 scene text.
- **Q2 — Filtered Bible in Stage 2.** v3 says to paste the whole Bible into every stage. The plan sends Stage 2 only the entries relevant to the batch, as verbatim records. Is that acceptable, or must every call carry the full Bible, with the hard token check as the only guard?
- **Q3 — Flow access.** Is there any API or automation for Flow you have, or intend to use? The plan assumes manual paste.
- **Q4 — Pydantic.** It's installed transitively through `anthropic`. Is a direct dependency acceptable, or do we keep hand-written validators (the recommendation)?
- **Q5 — Mode B (TurboScribe).** Is it needed in the first release? It's skipped for now, because the PlotPilot input is authoritative.
- **Q6 — Models.** Stage 0 and Stage 2 need judgment, so the plan uses GEN (Sonnet). Could Stage 1 or the Bible delta use QC (Haiku)? This only affects cost.
- **Q7 — Sub-style choice.** Is a suggestion from PlotPilot's dominant module, which you confirm, the right approach? Or do you always pick the sub-style yourself?
- **Q8 — Bible review gate.** Should the end-of-chunk `--accept-bible` gate be mandatory, as with the tracker gate? Or can the continuity delta merge automatically, since its entries come from beats you already reviewed at the reference gate?
