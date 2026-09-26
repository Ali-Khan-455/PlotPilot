# Deferred minor issues

Minor findings from each phase's final code review. They were deliberately left out of that phase's fix pass: none of them corrupts output or loses data. Fix them whenever it's convenient, write a failing test first, and delete the entry once it's fixed.

## Phase 5 — Hook + assemble + metadata

- [ ] **Prompt 9 on the hook can't drop even one word** (`parse.py`, `check_hook_tts` at `TTS_MIN_RATIO` 0.97). On a hook under about 33 words, a legitimate one-word rewrite for a homograph (Prompt 9 item 3) is rejected, and the hook has no override. This was a deliberate plan choice. The alternative is an absolute slack, such as `len(new) >= len(old) - 1`.
- [ ] **`script.txt` and `metadata/<slug>.txt` are overwritten silently on every run** (`final.py`). A hand fix to the script after a finished run is lost with no warning. Chunk files do get a warning (`_check_done_chunks`). Warn when the existing file differs from the rebuilt text.
- [ ] **D17 at the real limit is unverified** (`final.py`, `count_tokens`). If `messages.count_tokens` returns a 400 for an oversize request, the operator sees a generic `ERROR:` instead of the D17 message. Check this during the first live run, or catch `anthropic.BadRequestError` there and treat it as D17.
- [ ] **A misquoted opening scene is placed at `[00:00]` silently** (`assemble.py`, `scene_lines`). If chunk 1's first scene sentence is found mid-body, it is still stamped `[00:00]` (per R5) and its real offset is dropped. Warn when the first hit isn't at `starts[0]`.
- [ ] **The `max_input_tokens` fallback can hide an override mistake** (`llm.py`, `context_limit`). An overridden `--gen-model` whose `retrieve` result lacks `max_input_tokens` is checked against `GEN_CONTEXT_TOKENS` (the default model's window). The API then rejects the call rather than D17 catching it, and the CLI handles that error. `or` also treats `0` as missing.
