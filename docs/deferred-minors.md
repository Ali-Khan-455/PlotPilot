# Deferred minor issues

Minor findings from each phase's final code review. They were deliberately left out of that phase's fix pass: none of them corrupts output or loses data. Fix them whenever it's convenient, write a failing test first, and delete the entry once it's fixed.

## Deferred-minor fixes — review findings (kept deferred)

- [ ] **Pipeline errors still go to stdout** (`pipeline.py`, `qc.py`, `final.py`). Only `cli.fail` and the CLI's top-level `ERROR:` go to stderr; mid-run errors, gates and `final._fail` print to stdout. Needs a broader output/logging change.
- [ ] **Audit and fact-check log files are written after the DB insert** (`qc.py`). `_sync_logs` rewrites them from the DB at the fact-check gate and at `normalized`, so they are eventually correct; only their timing relative to the insert differs.
- [ ] **Chunk labels and the progress line use sequential chapter indices, not the book's own numbering** (`ingest.py` / `tracker.py`). Only the short-chapter warning shows heading text.
- [ ] **CLI leftovers** (`cli.py`). A new novel with no headings creates an empty `plotpilot.db` before failing. An existing but read-only database still ends in a traceback at the first write (only `db.connect` is guarded). With a stored plan, the manifest's warnings still come from the fresh parse.
- [ ] **The deleted-margin check is narrow** (`pipeline.py`, `sync_file`). Deleting the margin and also editing the first body paragraph is not caught; that paragraph becomes the margin.
- [ ] **A failing usage-log write masks the API error** (`llm.py`, `call`). If `_log_row` raises (unwritable `logs/`), the original exception is lost.
- [ ] **`GUT_END_RE` matches any line starting "End of (the) Project Gutenberg"** (`ingest.py`), even mid-book. Negligible in real prose.

