# Image-Sync Engine — plan

**Status:** IS-1 (foundation) built and merged; see `docs/image-sync-audit.md`. C4 is resolved. IS-2 is unblocked.

## Context

PlotPilot turns a novel into a narration script (`<out>/<slug>/script.txt`, per-chunk files) plus scene metadata (`metadata/<slug>.txt`, `[mm:ss] SCENE: …` at 150 wpm). The **Image-Sync Prompt Engine v3** is a markdown prompt doc that an operator pastes into Claude chunk by chunk. It turns PlotPilot's output into image prompts for Google Flow and keeps a hand-pasted **Visual Bible** for continuity. The work has three stages:

- **Stage 0:** beats.
- **Stage 1:** character, location and object references, then a reference approval gate.
- **Stage 2:** batches of about 30 image prompts, a manifest, and a Bible update.

This plan turns that doc into a tool in the same style as PlotPilot:
- an argparse CLI;
- SQLite state, with history append-only;
- prompts loaded from a spec file by heading;
- gates that stop the run and a next run that resumes;
- TDD against a fake client;
- phases, each reviewed with `plan-reviewer` before implementation.

**Fixed (not revisited here):**
- beat-based segmentation;
- reference-driven consistency;
- continuity-locked state;
- portability to SDXL+LoRA;
- no summarization fallback for context limits.

**Blocked on you before IS-1 can start:**
- the v3 spec text (you're pasting it next);
- confirmation of C1–C3 (see below). Q0–Q9 are answered.

## Decisions on the seven questions

### 1. Prompt or program → **hybrid**

A program owns the stages, the gates, the state and every rule that can be checked mechanically. Claude does only the judgment work:
- segmenting beats and recovering visual detail;
- writing reference descriptions;
- composing each scene.

Several v3 rules are mechanical, so code guarantees them instead of trusting the model to follow them:

| v3 rule | Enforced by |
|---|---|
| Never invent a timestamp. Split beats share a timestamp with a/b suffixes. Chronological order. | code: validates every beat against the chunk's scene list |
| Source tag required on recovered detail | code: parse check, retry once |
| Cadence warning (outside 3–20 s for more than 3 beats in a row) | code: computed from the timecodes |
| Slot policy (5 characters / 14 objects, locked, fallback) | code: the Bible merge assigns slots and the LLM never does |
| Unique CamelCase `#Tag`s | code |
| Batch pre-check (every `@Name` approved) | code: runs before any Stage 2 call, so no partial batch |
| Locked style suffix, word for word | code: appends it **as text loaded from the spec by heading**, never as a code literal |
| Shot cadence (no more than 3 of the same shot in a row, a wide shot every 5–7 beats, 9:16 avoids wide shots) | code: QA check and one retry of that batch; warns after that |
| `@Name` used only for approved references present in the beat, never stacked | code: QA check |
| Manifest CSV, `beat_<M-SS>.png` names | code: generated from stored prompts |
| Continuity log and revision log are append-only | code: the Bible merge |

**Why hybrid:**
- A pure prompt doc can't enforce any of these rules, and the drift they cause is exactly what v3 fixes by hand.
- A pure program can't do the creative work.
- The hybrid mirrors PlotPilot, where Claude drafts and code does the parsing, gates and merges.

### 2. Where it lives → **same repo, separate package**

Add a new top-level package, `imagesync/`, with its own CLI (`imagesync.py`) and its own database (`imagesync.db`).

**What imagesync uses from PlotPilot:**
- `plotpilot.llm` (the client wrapper, model check, `count_tokens`, `context_limit` and the usage log);
- `plotpilot.prompts.fill` and a generalized loader (see IS-1);
- `tests/fakes.py`;
- **two new read-only PlotPilot functions, `plotpilot.final.derive_inputs` and `derive_outputs`.** `derive_outputs(conn, novel_id)` returns `(hook, bodies, scenes_per_chunk, script, metadata_lines)`, exactly as `run_final` computes them (see IS-1 for the split).

**How `derive_outputs` works:**
- Today `run_final` builds these inputs inline:
  - `narration_state` for the bodies;
  - the hook, from the `hook` pass followed by the newer `hook_tts` pass;
  - the target, from the tracker;
  - the scenes, from each chunk's latest ok `scenes` pass.
- IS-1 extracts that derivation. `run_final` uses `derive_inputs` before the hook passes exist and `derive_outputs` after them. Both tools therefore share one source of the script and timestamps, with no reimplementation to drift apart.
- `scene_lines` is called **once, novel-wide**, and its output is split by each chunk's scene count. Calling it per chunk would stamp every chunk's first scene `[00:00]` and reset the cursor.

**How imagesync reads PlotPilot:**
- `plotpilot.db` is opened read-only (`file:…?mode=ro`, `row_factory=Row`) through one `imagesync/source.py` module.
- A missing database gives a clear error.
- PlotPilot never imports imagesync, and each database has one owner.

**Alternatives considered:**
- **Sibling repo or fork:** both would duplicate `llm.py`, `prompts.py`, the fakes and CI, and the derivation would drift.
- **Inside `plotpilot/`:** it would mix two pipelines with different gates and state in one package.

**Prompt text:**
- It lives in `prompts/image-sync-v3.md`: the v3 spec verbatim, plus the tool output contracts (Q1).
- That includes the locked style suffix, the sub-style descriptors and the colour treatments. All of them are loaded by heading, and none is a code constant.
- CLAUDE.md currently names `prompts/v4-spec.md` as the only prompt source. A second spec file therefore needs **your approval (Q0)**, and IS-1 amends CLAUDE.md and `docs/architecture-audit.md` (which lists image sync as deferred).

### 3. Visual Bible storage → **JSON in SQLite (source of truth) + a markdown mirror**

This follows the `tracker_versions` precedent (R21).

- **Storage:** `bible_versions(id, novel_id, chunk_idx, stage, source_pass_id, json, delta, accepted_at)`, where `stage` is one of `style_lock`, `refs` or `continuity`.
- **Replay protection:** `source_pass_id INTEGER NOT NULL UNIQUE REFERENCES passes(id)` is the refs or delta pass being accepted, as R21's pending file is bound to `delta-<pass_id>`.
  - A replayed accept of the same pass is refused.
  - A new pass after `--regenerate` or `--revise-beat` can be accepted, so a chunk may legitimately hold several `refs` or `continuity` versions.
  - The newest version wins (`ORDER BY id DESC`, like `db.latest_tracker_row`).
- **Revisions:** they are passes. The render folds any revision passes newer than the latest version into the revision log, so a revision to the last chunk after `done` still reaches the Bible and its mirror. The next accepted version then carries them.
- **Atomic write:** `db.add_bible_version(..., new_status=)` inserts the version row, a pass row and the chunk status in **one transaction**, as `add_tracker_version` does.
- **Merge:** deterministic, in a pure module `imagesync/bible.py`. It never deletes. It appends a superseded state as a new continuity entry. It assigns slots in order of first appearance and marks overflow as `fallback`. It rejects a duplicate `#Tag` and keeps both the original and transliterated names.
- **Mirror:** `bibles/<slug>.md`, rendered in exactly v3's `=== VISUAL BIBLE ===` block layout and regenerated on every run. It is read-only. The layout mirrors the spec's headings, like R21's tracker render; the field labels come from the spec block.
- **Review surface:** a bound pending JSON file, as with the tracker gate.

### 4. Flow integration → **manual paste, tracked by gates**

The plan assumes no Flow API (Q3).

**What the tool writes:**
- **Reference prompts:** `images/<slug>/chunk-NN/refs.txt`.
- **Batches:** `images/<slug>/chunk-NN/batch-K.txt`.
- **Manifest:** `manifest.csv`.
- **All derived:** these files are rebuilt from the database on every run, so a crash between an insert and a file write heals itself (R22 precedent).

**How it records the operator's decisions:**
- **Approval:** `--approve-refs` writes the `refs` Bible version, marking each reference `reference generated: yes`, together with the status, atomically.
- **Regeneration:** `--regenerate "#Name: reason"` re-runs Stage 1 for that one reference with the reason and returns to `refs_pending`. It's allowed until the chunk's first Stage 2 batch is stored; after that, use `--revise-beat`.
- **Optional file check:** with images saved to `images/<slug>/refs/<Tag>.png`, the tool warns about missing ones before approval.

**Portability:** everything Flow-specific (`#Name` / `@Name` syntax, the 5/14 limits) sits in one `imagesync/target.py`. Swapping that module is the SDXL+LoRA path.

### 5. Batch orchestration → **automatic batches, operator-controlled gates**

The chat-era "next" only existed because of chat output limits. The tool loops through batches of about 30 beats by itself:
- each batch is one Stage 2 call, persisted as soon as it's done, so a crash resumes at the next batch;
- the batch size used is stored on the chunk, so changing the config can't reshuffle batches on resume;
- the operator pastes `batch-1.txt`, `batch-2.txt` and so on at their own pace.

Operator control stays where a decision is made:
- **Style lock gate** (chunk 1 only): `--sub-style a|b|c|d --aspect 16:9|9:16|1:1|4:5`. The tool suggests a sub-style from PlotPilot's dominant module and never picks one silently (Q7).
- **Reference approval gate** (per chunk, when new references exist).
- **Bible review gate** (end of a chunk): `--accept-bible` (Q8).
- **Beat revision:** `--revise-beat 04-15 "new description"` is stored as an appended pass that is folded over the beats (R20-style), never as an update.

### 6. Context window → **selected inputs + a hard pre-check, no summarization**

**Stage 0:** one PlotPilot chunk's chapters, its narration and its scene list. This is normally at most 12k words, but a single chapter over 12k words is its own chunk and can be larger. So only the hard pre-check truly bounds it.

**Stage 1:** the chunk's beats plus a **tag index** of the Bible: every entry's name, tag, slot and locked descriptor. It excludes the continuity and revision logs.

**Stage 2, one batch:**
- that batch's beats, plus the last 3 beats of the previous batch for shot-cadence context;
- the style lock;
- the full entries for the elements named in those beats, each with its newest continuity state;
- for a `CONTINUES` beat, the previous chunk's last beat and shot.

These are **deterministic selections of verbatim records**, not summaries. No text is rewritten.

**Hard pre-check before every call:** `count_tokens + max_tokens` is compared with the model's `max_input_tokens`, as D17 does. A 400 from `count_tokens` for an oversize request is handled as in `final.py`. Over the limit is a clear error, with no fallback. The selection deviates from v3's "paste the whole Bible" (Q2).

### 7. Validation → **yes, with stdlib validators in the `tracker.validate_delta` style**

- **What is checked:**
  - every model output (beats, reference proposals and Bible delta, batch scenes);
  - every accepted Bible version;
  - every hand-edited pending file.
- **How:** exact keys, exact types and enumerated values. A failure means retry once, then fail clearly (D18).
- **Why not Pydantic:** it's installed transitively through `anthropic`, but not declared (Q4).

## Design

**Data flow per chunk:**

```
plotpilot.db (read-only) → plotpilot.final.derive_outputs → source.py: per-chunk narration,
        chapters, scene list (novel-wide scene_lines, split by chunk), module, tracker
      ↓
[style lock gate, chunk 1] → Stage 0 (beats JSON) → validate → beats pass
      ↓
Stage 1 (new refs + Bible delta JSON) → slots in code → refs.txt → [approval gate]
      ↓
batch pre-check (code) → Stage 2 per batch (scene JSON) → code composes prompt + spec suffix → QA → batch-K.txt
      ↓
manifest.csv (code) → continuity delta → [Bible review gate] → next chunk
```

**When a novel is ready.** The novel must be finished in PlotPilot, judged from the database alone, never from `script.txt`, whose path depends on `--out`:
- every chunk has status `done`;
- chunk 1 has an ok `hook_tts` pass newer than its latest ok `hook` pass;
- `assemble.splice_check` passes on the recomputed script.

**Binding to the PlotPilot data.** When an imagesync novel is created, it stores PlotPilot's `source_sha256` and a sha256 of the recomputed script and metadata lines. Every run recomputes both and refuses on a mismatch with a clear message. This covers a re-planned slug or a rebuilt `plotpilot.db`, so imagesync never works from stale narration.

**Beat identity.**
- Beats are keyed internally by `(chunk_idx, scene_index, suffix)`.
- `beat_<M-SS>.png` and `--revise-beat M-SS` rely on timecodes being unique. R22's unfound-scene fallback can produce duplicates. **Pending Q9:** either `source.py` refuses them with an error naming both scenes and the remedy (keeping v3's naming exact), or it disambiguates filenames (the recommendation).

**Chunk states and transitions.** Each transition is one atomic write: a pass, a status, and a Bible version where noted.

| From | Event | To | Writes |
|---|---|---|---|
| `ready` | Stage 0 ok | `beats` | beats pass |
| `beats` | Stage 1 ok with new references | `refs_pending` | refs pass |
| `beats` | Stage 1 ok with no new references | `refs_approved` | refs pass + `refs` Bible version |
| `refs_pending` | `--approve-refs` | `refs_approved` | `refs` Bible version |
| `refs_pending` / `refs_approved` (no batch stored) | `--regenerate "#Name: r"` | `refs_pending` | refs pass |
| `refs_approved` | first batch stored | `batching` | batch pass |
| `batching` | all batches stored | `bible_pending` | continuity delta pass |
| `bible_pending` | `--accept-bible` | `done` | `continuity` Bible version |
| any state after `beats` | `--revise-beat` with no new `@Name` | unchanged | revision pass; affected prompt re-emitted if already batched |
| any state after `beats` | `--revise-beat` introducing a new element | `beats` | revision pass. Stage 1 re-runs for the new element only, so the chunk re-enters the reference gate rather than dead-ending at the batch pre-check. |

**Revision ordering.** Chunks run strictly in order, as in PlotPilot, and chunk N+1's Stage 1 reads chunk N's Bible. So a `--revise-beat` on chunk N:
- that needs no new element is allowed at any time;
- that introduces a new element is **refused once chunk N+1 has left `ready`**. The error says so and suggests revising the beat in the later chunk instead.

**Stage 2 composition** is split between the model and code:
- **The model returns** `{timecode, shot_type, scene, refs[], genre_override|null}`.
- **Code builds the final prompt:** `"{shot}, {scene}, {@refs}, {suffix}"`. The suffix is the spec's locked-suffix section, with the sub-style descriptor and colour treatment (also loaded from the spec) and the aspect from the Bible.
- **QA:** code rejects a `@ref` that isn't approved, isn't in the beat, or is stacked.

## Phases

| Phase | Scope | Done when |
|---|---|---|
| **IS-1 Foundation** | See the IS-1 detail below the table. | A manifest of chunks prints. The style lock gate works. The recomputed metadata lines equal PlotPilot's file byte for byte. `run_final` is unchanged in behaviour (its tests pass). |
| **IS-2 Stage 0** | The beats call, beat validation (the scene-list subset, a/b suffixes, order, source tags, `CONTINUES` only on beat 1 and only when the previous chunk's final continuity entry is an open scene), the cadence warning, and `--revise-beat` before Stage 2. | Chunk 1 beats are stored and a rerun makes no calls. Every hard rule has a failing-then-passing test. |
| **IS-3 Bible + Stage 1** | `bible.py` (schema, merge, render in exact v3 layout), slots and fallback, unique tags with transliteration, `add_bible_version`, the Stage 1 call, `refs.txt`, the approval gate, `--regenerate`, and the mirror. | Two-chunk test: chunk 2 doesn't re-create chunk 1's references, overflow gets `fallback`, the render matches the v3 block, and a replayed accept of the same pass is refused by `UNIQUE(source_pass_id)`. `--regenerate` after approval re-approves cleanly. |
| **IS-4 Stage 2** | The batch pre-check, per-batch calls, composition in code with the spec-loaded suffix, QA (shot cadence, aspect, refs; red-X and text rules only as far as they can be checked), a retry per batch, batch files, `manifest.csv`, the continuity delta, the review gate, and `CONTINUES` framing. | A two-chunk run ends at `done`. The suffix is byte-equal to the spec section on every prompt. A crash between batches resumes at the next one. |
| **IS-5 Revisions + image check** | `--revise-beat` after Stage 2 (re-emit one prompt, or re-enter the reference gate for a new element). `--check-images` compares `beat_*.png` against the manifest. | Every transition in the table has a test, including a revise-beat with a new element that reaches `done` a second time. The image report is correct. |

**IS-1 in detail:**
- **Docs:** amend CLAUDE.md and `docs/architecture-audit.md` (Q0).
- **Prompt loader:** generalize `load_prompts(path, heading_re, key=…)`. Sections without COPY/END markers must **fail closed** for the required keys, not be skipped silently.
- **PlotPilot extraction:** move `plotpilot.final.derive_outputs` out of `run_final`. `run_final` needs the bodies and the target *before* any hook pass exists, so the derivation is split in two:
  - `derive_inputs(conn, novel_id) -> (states, bodies, target)`, used by `run_final` from the start;
  - `derive_outputs(conn, novel_id)`, which calls `derive_inputs` and then adds the hook, script and metadata lines. It is only valid once the `hook_tts` pass exists; `source.py`'s readiness check guarantees that.

  `run_final` keeps its exact order of calls and gates, so its behaviour doesn't change.
- **`source.py`:** read-only access, the readiness check, the sha binding, and the duplicate-timestamp refusal.
- **`imagesync.db` schema:** includes `add_bible_version` and `UNIQUE(source_pass_id)`.
- **CLI and style lock gate:** add the CLI (`--novel path.txt`, with the slug derived as PlotPilot derives it) and the style lock gate.
- **Tests:** shared test helpers, and a conftest client guard for `imagesync.cli`.

Each phase follows PlotPilot's workflow: a plan in plan mode, `plan-reviewer`, approval, TDD, a final review, and minor findings into `docs/deferred-minors.md`.

## Files (target)

```
imagesync.py                 shim → imagesync.cli
imagesync/cli.py             argparse (--novel path.txt, slug as PlotPilot), gates, stdout/stderr as PlotPilot
imagesync/source.py          read-only plotpilot.db access, readiness, sha binding, per-chunk split
imagesync/db.py              imagesync.db schema, add_pass / add_bible_version (atomic), UNIQUE constraints
imagesync/beats.py           Stage 0 contract: validate, cadence, revision fold (pure)
imagesync/bible.py           Visual Bible schema, merge, slots, render (pure)
imagesync/target.py          Flow specifics: #/@ syntax, slot limits (pure; no prompt text)
imagesync/compose.py         Stage 2 composition + QA + manifest (pure; suffix passed in from the spec)
imagesync/pipeline.py        stage order, gates, resume
imagesync/config.py          models, batch size, paths
prompts/image-sync-v3.md     the spec, incl. suffix/sub-style/colour sections and output contracts
plotpilot/final.py           + derive_inputs / derive_outputs; run_final uses them in its existing order (no behaviour change)
plotpilot/prompts.py         load_prompts(path, heading_re, key) — backward compatible, fail closed
tests/helpers.py             shared cwd/run/all_done/hook_reply (moved from test_final.py)
tests/conftest.py            + guard imagesync.cli.make_client
tests/test_imagesync_*.py    one file per pure module + pipeline tests with FakeClient
CLAUDE.md, docs/architecture-audit.md   amended for the second spec and the imagesync package (Q0)
docs/image-sync-audit.md     imagesync decisions and rulings (IS-D1…)
```

## TDD steps (IS-1, the first review target)

1. **`derive_outputs`:**
   - all existing `test_final.py` tests pass unchanged after `run_final` is refactored onto it;
   - a new test shows `derive_outputs(...)[4]` (the metadata lines) equals the `metadata/<slug>.txt` written by `run_final`, byte for byte.
2. **`load_prompts(path, heading_re, key)`:**
   - PlotPilot's prompt tests pass unchanged;
   - a fixture v3 spec loads every required key (Stage 0/1/2, contracts, suffix, sub-styles, colours);
   - a required section missing its markers raises, instead of being skipped.
3. **`source.py`**, on a finished PlotPilot database built through shared helpers:
   - it returns per-chunk narration, chapters and scene lists;
   - it refuses an unfinished novel, a missing database, and duplicate scene timestamps;
   - it never writes (a checksum of the file before and after);
   - after PlotPilot's database is rebuilt, it refuses on the sha mismatch.
4. **Schema and atomicity:** `add_pass(new_status=)` and `add_bible_version(new_status=)` are atomic. `UNIQUE(source_pass_id)` rejects a replayed accept of the same pass, and a second `refs` version for the same chunk from a new pass is accepted.
5. **Style lock gate:**
   - without flags, it prints the suggestion and exits 0;
   - with flags, it stores the `style_lock` version;
   - invalid values are refused;
   - a rerun is idempotent.
6. **Verify:**
   - `pytest` and `ruff check .` (pinned rules);
   - a fresh final review;
   - minor findings into `docs/deferred-minors.md`.

## Out of scope

- Flow or any image API calls.
- Automatic image download.
- The SDXL+LoRA backend, beyond keeping `target.py` swappable.
- Mode B (TurboScribe input): deferred until a real user needs it (Q5 decision).
- A video editor.
- A web UI.
- Summarization of any kind.
- Prompt caching.
- Packaging with `pip install .` (the project installs dependencies only, as the README and CI do).

## Verification (whole project)

- The full suite runs against `FakeClient` with no network.
- A two-chunk end-to-end fixture goes from a finished PlotPilot database to `done`.
- The suffix is byte-equal to the spec section on every prompt.
- The Bible render matches the v3 layout.
- There is a crash or resume test for every gate and transition.
- One real run on the same public-domain novel PlotPilot is first run on. It is operator-side, because there's no API key or Flow access here.

## Review log

`plan-reviewer`, three rounds. The final one-line fix (TDD step 4 asserting `UNIQUE(source_pass_id)`) was applied without a fourth pass, at your instruction.

## Decisions (user, answers to Q0–Q9)

- **Q0 (spec file): approved.** `prompts/image-sync-v3.md` is added verbatim once you paste the text. IS-1 amends CLAUDE.md and `docs/architecture-audit.md` to say: "Each system owns its own prompt spec file under `prompts/`." The rule that no prompt text lives in code is unchanged.
- **Q1 (contracts): approved.** A "TOOL OUTPUT CONTRACTS" appendix is added to the v3 spec for Stage 0, Stage 1, each Stage 2 batch and the Bible update. The v3 prompt text itself is unchanged. The exact shapes are subject to C1 below.
- **Q2 (Bible entries): approved.** Stage 2 receives only the Bible entries named by `@Name` in its batch, each with its current state. This rule is stated in the contracts appendix.
- **Q3 (Flow): no API.** Manual paste. `target.py` is the swap point for SDXL+LoRA (a ComfyUI API later).
- **Q4 (validation): hand-written validators.** No Pydantic.
- **Q5 (TurboScribe): deferred.** Mode B ships when a user needs it (see Out of scope).
- **Q6 (models):**
  - Stage 0 and Stage 2 use GEN (Sonnet).
  - Stage 1 defaults to GEN, overridable with `--stage1-model`.
  - The Bible update defaults to QC (Haiku), overridable with `--bible-model`. See C2 for which call that is.
- **Q7 (sub-style): suggest, then confirm.**
  - The tool counts the module on each PlotPilot chunk's latest ok draft pass and maps the dominant one to a sub-style.
  - It prints `Suggested sub-style: (x) … (from dominant module M). Confirm with --sub-style x, or pick another.`
  - It refuses to run Stage 0 until confirmed. Once locked, the sub-style stays locked. The mapping is subject to C3.
- **Q8 (Bible gate): mandatory.** `--accept-bible` has the same shape as `--accept-tracker`: explicit, one-shot per chunk, no auto-advance. Every accept is logged to `logs/bible-accepts.log`.
- **Q9 (duplicate timestamps): disambiguate filenames.**
  - First `04:15` → `beat_04-15.png`, second → `beat_04-15_2.png`, third → `beat_04-15_3.png`.
  - The manifest and the beat list keep the raw timecode.
  - v3's naming section is amended to say so.
  - `--revise-beat` takes `04-15_2` to name the second occurrence.
  - Later, sub-second or per-scene offsets in PlotPilot could remove the collision at its source. That is out of scope now.

## Conflicts (C1–C4, all confirmed by you)

- **C1 — Contract fields versus the "code enforces" design (Q1).** As literally specified, three contract fields would move work from code to the model. The proposal keeps each field but gives code the final say, like PlotPilot's tracker merge forcing a term's `chunk`.
  - **Stage 2 `prompt`:** the model returns the scene composition only. Code appends the `@Name` references and the locked suffix loaded from the spec. If the model wrote the whole prompt, the suffix would no longer be guaranteed word for word, which is v3's hardest rule.
  - **Stage 2 `manifest_rows`:** code derives the manifest from the stored prompts: timecode, shot type and the first 5 words of the scene. Any `manifest_rows` the model returns are ignored, and the field is dropped from the contract so there aren't two sources for the same data.
  - **Stage 1 `slot` and Stage 0 `cadence_warning`:** code assigns slots, applying the lock, the 5/14 limits and `fallback`. Code also computes cadence from the timecodes. The model's values are ignored, and the fields are dropped from the contracts.
- **C2 — What "the Bible update" call is (Q6).** Stage 1's Bible additions come from the Stage 1 call itself, which is the new references plus their descriptors. So `--bible-model` governs one separate, cheaper call at the end of Stage 2. That call reads the chunk's beats and delivered prompts and returns the **continuity-log delta** (state changes and superseded entries) as JSON. Code merges it, then the mandatory `--accept-bible` gate follows.
- **C3 — Sub-style mapping (Q7).** The answer mapped module A to (a) Dark action. But module A is isekai, system and power fantasy, which v3's sub-style (c) Fantasy adventure (Tower of God, *The Beginning After the End*) describes. The proposed mapping is:
  - A → (c) Fantasy adventure;
  - B → (b) Soft romance;
  - C → (a) Dark action;
  - D → (d) Comedy slice of life.

  **Confirmed.**

**Resolutions (confirmed):**
- **C1:** the contracts shed `prompt`, `manifest_rows`, `slot` and `cadence_warning`. Code:
  - formats the `@Name` stack from `refs_used`;
  - appends the suffix from `prompts/image-sync-v3.md`;
  - builds the manifest from the stored prompts;
  - assigns slots from Stage 1's `new_references` order at chunk 1, then locks them;
  - computes cadence from timecode deltas.

  The v3 prompt text is unchanged: any prose cadence warning is ignored.
- **C2 (Amendment B):** `--bible-model` (default QC/Haiku) controls only the end-of-Stage-2 continuity-log call, which returns `{"continuity_log_entries": [...]}`. Stage 1's Bible additions come from the Stage 1 call itself (`--stage1-model`, default GEN/Sonnet).
- **C3:** the mapping is A → (c), B → (b), C → (a), D → (d).
- **Spec:** `prompts/image-sync-v3.md` is committed verbatim (4a39d1b), with the Q9 filename rule already in HOW TO USE step 5. The TOOL OUTPUT CONTRACTS appendix is appended (9964b56).

**C4 — How the model learns the JSON shape. Confirmed.**
- The appendix used to say the contracts "are not part of the prompt the model sees". That's no longer true: IS-2 appends each stage's contract to that stage's user message.
- **Resolution (with two refinements over the original proposal):**
  - Each stage's user message is: that stage's v3 prompt text (unchanged), then the fixed override paragraph now in the appendix under "### How the model uses these contracts", then that stage's JSON contract block.
  - **Refinement 1:** the override paragraph explicitly says to ignore the markdown format described above, not just to "output JSON" — so the model doesn't emit both.
  - **Refinement 2:** it explicitly says fields not in the schema are ignored and must not be included — so the model doesn't re-add `cadence_warning`, `slot`, or `manifest_rows`, which C1 already has code recompute.
  - The v3 prompt text itself is unchanged. The appendix's own wording is the only edit (applied directly to `prompts/image-sync-v3.md`, since it's spec content).
- **Known risk, not yet mitigated:** the model sees two format instructions in one message (the v3 prompt's markdown format, then the override to JSON). An override instruction usually wins, but a model can still produce both. If IS-2's first real runs show mixed markdown-plus-JSON output, the fallback is a per-stage "format-strip": send the v3 prompt with its own `Output format:` section removed programmatically at call time (not edited in the spec file) and replaced by the override plus the contract. This is recorded in `docs/image-sync-audit.md` and deferred until real runs show it's needed.
- **Alternatives rejected:** parsing v3's markdown output in code (defeats the point of having a schema, and is fragile); rewriting v3's prompt text to natively ask for JSON (breaks "each system's spec is a versioned, verbatim artifact").

## Open questions (answered — kept for reference)

- **Q0 — Second spec file.** CLAUDE.md names `prompts/v4-spec.md` as the only prompt source. Do you approve `prompts/image-sync-v3.md` as a second one? CLAUDE.md and the architecture audit would be amended in IS-1. Can you also commit the v3 text, or paste it for me to add verbatim? IS-1 is blocked until then.
- **Q1 — Output contracts.** v3's stages emit free-text blocks. For code to validate them, Stages 0, 1 and 2 need JSON output contracts. The proposal is to append a "TOOL OUTPUT CONTRACTS" section to the v3 spec and change nothing else. That is a spec edit, which needs your approval. The alternative is to parse v3's free text, which is fragile.
- **Q2 — Selected Bible entries in Stage 2.** v3 says to paste the whole Bible into every stage. The plan sends each Stage 2 batch only the entries for the elements it names, as verbatim records. Is that acceptable, or must every call carry the full Bible, with the hard token check as the only guard?
- **Q3 — Flow access.** Do you have, or plan to use, any Flow API or automation? The plan assumes manual paste.
- **Q4 — Pydantic.** It's installed transitively through `anthropic`. Is a direct dependency acceptable, or do we keep hand-written validators (recommended)?
- **Q5 — Mode B (TurboScribe).** Is it needed in the first release? It's skipped for now, because the PlotPilot input is authoritative.
- **Q6 — Models.** Stage 0 and Stage 2 need judgment, so the plan uses GEN (Sonnet). Could Stage 1 or the Bible delta use QC (Haiku)? This only affects cost.
- **Q7 — Sub-style choice.** Is a suggestion from PlotPilot's dominant module, which you confirm, the right approach? Or do you always pick the sub-style yourself?
- **Q8 — Bible review gate.** Should the end-of-chunk `--accept-bible` gate be mandatory, as with the tracker gate? Or can the continuity delta merge automatically, since its entries come from beats you already reviewed at the reference gate?
- **Q9 — Duplicate scene timestamps.** They come from R22's fallback for an unfound scene. The plan refuses them, but that refusal has **no in-place remedy**: done PlotPilot chunks are frozen, scenes don't re-run without a body change, and Prompt 5 has no redo. The only way out would be rebuilding `plotpilot.db`, and the error would say so. The alternative is to disambiguate filenames (`beat_04-15.png`, `beat_04-15_2.png`), which keeps such novels usable but departs from v3's naming convention. Which do you prefer? (Recommendation: disambiguate, since refusing strands a finished novel.)
