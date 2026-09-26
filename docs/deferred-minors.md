# Deferred minor issues

Minor findings from each phase's final code review. They were deliberately left out of that phase's fix pass: none of them corrupts output or loses data. Fix them whenever it's convenient, write a failing test first, and delete the entry once it's fixed.

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
