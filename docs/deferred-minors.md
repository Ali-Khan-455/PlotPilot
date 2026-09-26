# Deferred minor issues

Minor findings from each phase's final code review. They were deliberately left out of that phase's fix pass: none of them corrupts output or loses data. Fix them whenever it's convenient, write a failing test first, and delete the entry once it's fixed.

## Image-Sync IS-1 — Foundation

- [ ] **A non-PlotPilot database or an unreadable spec crashes with a traceback** (`imagesync/source.py`, `imagesync/cli.py`). An empty file gives `no such table: novels`, a non-SQLite file gives `file is not a database` (for example `--plotpilot-db imagesync.db`), and a missing spec gives `FileNotFoundError`. Catch `sqlite3.DatabaseError` in `open_plotpilot`/`load_novel`, and `OSError` around `load_spec`, and turn them into clear errors.
- [ ] **The read-only test would pass without `mode=ro`** (`tests/test_imagesync_source.py`). Read queries don't change the file either way. Also assert that a write through `open_plotpilot(...)` raises "readonly".
- [ ] **The hashes are bound on the very first run** (`imagesync/cli.py`). A run that only prints the suggestion stores both hashes. A later PlotPilot scenes pass then blocks `--sub-style` with "can't reuse beats", although no lock or beats exist yet. Re-bind the hashes silently while no Bible version exists, or document this in IS-D4.
- [ ] **Concurrent first runs** (`imagesync/cli.py`). Two first runs at once hit an uncaught `IntegrityError` on the `slug` UNIQUE constraint (catch it and re-run `find_novel`). The style-lock `except IntegrityError` is broad: a foreign-key or NOT NULL failure would also read as "Style already locked". Narrow it to the `one_style_lock` violation.
- [ ] **`add_bible_version` misses two cases silently** (`imagesync/db.py`). `new_status` with no `chunk_id` updates nothing. `source_pass_id` isn't checked to belong to `novel_id`, which matters from IS-3 on.
- [ ] **Parts of the spec parser fail open** (`imagesync/spec.py`). A duplicate sub-style letter silently replaces the first, and a fifth colour bullet is accepted. The `startswith("Replace")` exemption in the suffix-fence check is dead code, because the Replace text sits on the heading line. An empty suffix gets the "exactly one line" message.
- [ ] **`load_section` is sensitive to whitespace** (`plotpilot/prompts.py`). Trailing whitespace on a heading reads as "no section" (it fails closed, with a misleading message). `required` passed as a bare string iterates over its characters. Match on `ln.rstrip()` and validate `required`.
- [ ] **Paths without tests:** the busy-database message, the locked `--aspect … ignored` note, the splice-check refusal in `load_novel`, and that `imagesync.db` is not created after an early failure.
- [ ] **The PlotPilot connection is never closed** (`imagesync/cli.py`). `open_plotpilot(...)` is passed inline to `load_novel`. Use `contextlib.closing`.
- [ ] **Style.**
  - Mid-function `import pytest` in `tests/test_final.py`, and `noqa: E402` imports in `tests/test_prompts.py`: move them to the top.
  - `ERROR:` prefixes are inconsistent across imagesync's messages.
  - The mismatch message's "can't reuse beats" is premature before IS-2.
