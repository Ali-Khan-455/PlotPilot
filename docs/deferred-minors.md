# Deferred minor issues

Minor findings from each phase's final code review. They were deliberately left out of that phase's fix pass: none of them corrupts output or loses data. Fix them whenever it's convenient, write a failing test first, and delete the entry once it's fixed.

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

## Phase 3 — QC chain

- [ ] **The `check_rewrite` preamble and sign-off heuristics are narrow** (`parse.py`). These leak into narration: `Sure! Here is the normalized narration:`, `Normalized narration:`, `I hope this helps!`, `End of normalized narration.`, and 8-word-plus notes such as `Note: I kept the phonetic hint for Kael unchanged as instructed.`. It also rejects a genuine Prompt 8 cliffhanger ending in `Then the door opened—`, which has no terminal punctuation.
- [ ] **`parse_factcheck` flags include step headers** (`parse.py`). `**Step 2: PRESENT / MISSING check**` is listed as a flag. `Step 4 — Verdict: FAIL. Reason: 1 MISSING, 1 INVENTED.` gives zero flags; the fallback message covers that case.
- [ ] **Deleting the margin paragraph silently moves narration into the margin** (`pipeline.py`, `split_file`). Body paragraph 1 becomes the "margin", which is excluded from QC and later overwritten by the hook.
- [ ] **`split_file` needs an exact `"\n\n"`** (`pipeline.py`). A blank line containing spaces, or an edited Phase 2 legacy single-space file, gets the misleading "keep the margin as its own first paragraph" error.
- [ ] **A crash after an `operator_edit` insert but before the canonical rewrite records a duplicate edit row** (`pipeline.py`, `sync_file`). Harmless.
- [ ] **TTS warnings are printed once and never persisted** (`qc.py`). A crash or a rerun doesn't show them again.
- [ ] **`--repair-margin` on a QC'd chunk passes the stored draft target** (`pipeline.py`). After Prompt 9 or an edit, that sentence may no longer open the body. This is the R20 Phase 5 note, but it applies now.
- [ ] **Override log robustness** (`qc.py`, `_override`). A reason containing a newline breaks the one-line format. `getpass.getuser()` can raise in a container with no user name.
- [ ] **An unrecoverable file-format error blocks every flag, including `--redraft`** (`pipeline.py`, `run_chunk1`). The message doesn't say that deleting the file restores it.
- [ ] **Missing tests:** an edit at `drafted` staying `drafted`; the "your edit … will be fact-checked first" note; a Prompt 8 API error or STOPPED continuing to Prompt 9; the override log being written before the DB row.
- [ ] **`db.set_status` is dead code** (`db.py`). Remove it, or use it.
- [ ] **Audit and fact-check log files are written after the DB insert** (`qc.py`). A crash leaves `logs/<slug>/chunk-NN-*.md` one version behind, while the gate header points at it.

## Phase 4 — Tracker + chunks 2–N

- [ ] **Stand-in collisions within one delta go undetected** (`tracker.py`, `merge_collisions`). `Bo → "the rookie"` and `Cy → "the rookie"` in the same delta give no warning and both are merged.
- [ ] **A stand-in change for an existing character is dropped silently** (`tracker.py`, `merge`). An operator edit such as `{"name": "aria vale", "standin": "my boss"}` is lost with no message.
- [ ] **The mirror can stay stale after a crash** (`qc.py`, `_accept_tracker`). A crash after `add_tracker_version` commits but before `_write_mirror` leaves `trackers/<slug>.md` out of date. For the last chunk, the all-done branch never regenerates it.
- [ ] **Missing or weak tests:**
  - Prompt 11 malformed twice.
  - `--accept-tracker` with the pending file missing. The behaviour is correct per the reviewer's probe; there's just no test.
  - `--redraft` at `tracker_pending` asserts only `len(pending) == 1`, not that the old bound file was replaced.
  - `--redraft` on a planned chunk with `--module` (the branch that drafts).
- [ ] **The progress line shows only the previous chunk's range** (`pipeline.py`, `prompt_tracker`; `qc.py`, `_write_mirror`). The spec's "Chapters [range] processed so far" implies the cumulative range.
- [ ] **The term `chunk` type is over-strict** (`tracker.py`, `validate_delta`). `"chunk": "2"` from the model is rejected, costing a retry or an exit 1, even though merge overwrites the value anyway.
- [ ] **A UTF-8 BOM breaks the pending file** (`qc.py`, `_accept_tracker`). Read it with `utf-8-sig`.
- [ ] **A stale `done`-chunk file is treated as an edit** (`pipeline.py`, `_check_done_chunks`). A file matching an earlier legitimate rendering gets the "edited after merge" warning instead of a quiet rewrite.
- [ ] **`--module` is dropped silently** on a started chunk 1 when `--repair-margin` is also given (`pipeline.py`, `_run_chunk`). The plain path prints a note.
- [ ] **Loose unlink at the gate** (`qc.py`, `_tracker_gate`). `old.unlink()` runs even when the regex doesn't match, so a glob-matched file with a non-numeric id is deleted. Negligible, given the slug character set.
- [ ] **`margin_sentences` is recomputed with `count_sentences`, not taken from the model** (`qc.py`, `_accept_tracker`). The spec says "as the AI wrote it", and the two can differ with abbreviations.

## Phase 5 — Hook + assemble + metadata

- [ ] **Prompt 9 on the hook can't drop even one word** (`parse.py`, `check_hook_tts` at `TTS_MIN_RATIO` 0.97). On a hook under about 33 words, a legitimate one-word rewrite for a homograph (Prompt 9 item 3) is rejected, and the hook has no override. This was a deliberate plan choice. The alternative is an absolute slack, such as `len(new) >= len(old) - 1`.
- [ ] **`script.txt` and `metadata/<slug>.txt` are overwritten silently on every run** (`final.py`). A hand fix to the script after a finished run is lost with no warning. Chunk files do get a warning (`_check_done_chunks`). Warn when the existing file differs from the rebuilt text.
- [ ] **D17 at the real limit is unverified** (`final.py`, `count_tokens`). If `messages.count_tokens` returns a 400 for an oversize request, the operator sees a generic `ERROR:` instead of the D17 message. Check this during the first live run, or catch `anthropic.BadRequestError` there and treat it as D17.
- [ ] **A misquoted opening scene is placed at `[00:00]` silently** (`assemble.py`, `scene_lines`). If chunk 1's first scene sentence is found mid-body, it is still stamped `[00:00]` (per R5) and its real offset is dropped. Warn when the first hit isn't at `starts[0]`.
- [ ] **The `max_input_tokens` fallback can hide an override mistake** (`llm.py`, `context_limit`). An overridden `--gen-model` whose `retrieve` result lacks `max_input_tokens` is checked against `GEN_CONTEXT_TOKENS` (the default model's window). The API then rejects the call rather than D17 catching it, and the CLI handles that error. `or` also treats `0` as missing.
