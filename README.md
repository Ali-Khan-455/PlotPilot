# PlotPilot

PlotPilot turns a full novel (`.txt`) into a TTS-ready YouTube narration script, told in the first person by the main character, plus a parallel scene-metadata file for syncing images later. It is a local Python CLI for solo recap-channel creators.

The pipeline follows the prompt system in [`prompts/v4-spec.md`](prompts/v4-spec.md). Design decisions are in [`docs/architecture-audit.md`](docs/architecture-audit.md).

## Requirements

- Python 3.11+
- An Anthropic API key

## Setup

```bash
git clone https://github.com/Ali-Khan-455/PlotPilot.git
cd PlotPilot
python -m pip install "anthropic>=1.8"
export ANTHROPIC_API_KEY=sk-ant-...
```

For development, also install the test and lint tools:

```bash
python -m pip install pytest ruff
```

## Quick start

```bash
python plotpilot.py --novel path/to/novel.txt
```

Each run advances the novel as far as it can and then stops, either at a **gate** that needs your decision or when everything is done. It always prints the next step. To continue, re-run the same command, adding the flag it asks for. Every stage is saved to `plotpilot.db` (SQLite) before the next one starts, so a crashed or interrupted run simply resumes.

## How a run goes

1. **Plan.** The first run detects chapters, splits the novel into chunks, and prints a manifest. A chunk holds at most 5 chapters and 12,000 words.

2. **Module gate.** For each chunk, PlotPilot suggests one of four niche modules and stops:

   | Module | Genre |
   |---|---|
   | `A` | Isekai, system, power fantasy |
   | `B` | Romance, josei, emotional drama |
   | `C` | Dark action, revenge, thriller |
   | `D` | Comedy, slice of life, wholesome |

   Confirm or override with `--module`:

   ```bash
   python plotpilot.py --novel path/to/novel.txt --module C
   ```

3. **Draft and QC.** The chunk is drafted, then checked in order:
   - a self-audit;
   - a fact-check against the source;
   - a texture repair, only if the audit found flat stretches;
   - TTS normalization;
   - a deterministic TTS hazard check.

   The narration is written to `scripts/<novel>/chunk-NN.txt`.

4. **Fact-check gate.** If the fact-check fails, PlotPilot prints the flagged lines and stops. You have two options:
   - edit `scripts/<novel>/chunk-NN.txt` and re-run, and the edited text is checked again; or
   - accept the result with a reason, which is logged to `logs/factcheck-overrides.log`:

   ```bash
   python plotpilot.py --novel path/to/novel.txt --accept-factcheck="paraphrase, meaning unchanged"
   ```

5. **Tracker gate.** After QC, PlotPilot extracts what the chunk added to the Continuity Tracker (characters, terms, comparisons, texture lines). It writes these to `trackers/<novel>.chunk-NN.delta-<id>.pending.json` and stops. Review or edit that file, then merge it:

   ```bash
   python plotpilot.py --novel path/to/novel.txt --accept-tracker
   ```

   The next chunk then starts at its own module gate. Steps 2–5 repeat for every chunk.

6. **Hook, script and metadata.** When every chunk is done, PlotPilot writes the opening hook once for the whole novel and splices it in. It then assembles the full script and writes the scene metadata.

After each chunk, PlotPilot recommends reading it aloud at 2x speed to catch tone and texture drift. This step never blocks the run.

## Output

`<novel>` is the file name in lowercase with spaces and punctuation turned into hyphens: `My Novel.txt` becomes `my-novel`.

| Path | Contents |
|---|---|
| `scripts/<novel>/chunk-NN.txt` | Each chunk's narration. Chunk 1 starts with a placeholder margin until the hook replaces it in the final script. |
| `scripts/<novel>/script.txt` | The final assembled script, narration only. |
| `metadata/<novel>.txt` | Scene lines such as `[mm:ss] SCENE: …`, timed at 150 words per minute. |
| `trackers/<novel>.md` | A read-only view of the Continuity Tracker, regenerated on every run. |
| `logs/usage.csv` | Token usage for every API call. |
| `logs/errors.log` | Errors and failed stages. |
| `logs/<novel>/chunk-NN-{audit,factcheck}.md` | The full audit and fact-check output for each chunk. |
| `plotpilot.db` | Every draft and pass, kept append-only as a record of the transformation. |

`script.txt` and the metadata file are rebuilt from the database on every run, so hand edits to them are overwritten. Edit the chunk files instead, before a chunk's tracker update is merged. After the merge, a chunk is frozen: edits to its file are warned about and ignored.

## Options

| Flag | Purpose |
|---|---|
| `--novel PATH` | The novel `.txt` file (required). |
| `--out DIR` | The output directory for narration files. Default `./scripts`. |
| `--module {A,B,C,D}` | The niche module for the chunk being drafted. |
| `--redraft` | Redraft the current chunk. Uses `--module` if given, otherwise the chunk's previous module. |
| `--repair-margin` | Force a repair of chunk 1's placeholder margin, until chunk 1's tracker is merged. |
| `--accept-factcheck="REASON"` | Accept a failed fact-check for the current chunk. An empty reason is refused. |
| `--accept-tracker` | Merge the reviewed tracker update for the current chunk. |
| `--gen-model ID` | The model for drafts, repairs, texture and the hook. Default `claude-sonnet-5`. |
| `--qc-model ID` | The model for classification, audits, fact-checks, TTS, tracker and scenes. Default `claude-haiku-4-5`. |

Flags apply only to the chunk that is current when the run starts. `--redraft` and `--repair-margin` can't be combined with `--accept-tracker`.

## Models

The default models are set in `plotpilot/config.py`. They are current aliases from Anthropic's model table, but they have not been tested against the live API yet. The first real run checks every model it uses and stops with a clear message if an ID is unknown. Override them with `--gen-model` and `--qc-model`.

## Development

```bash
python -m pytest -q   # tests, using a fake client; no API calls
ruff check .          # lint
```

CI runs both on every pull request. Contributor rules, including the pipeline order, the gates, and the rule that prompt text lives only in the spec, are in [`CLAUDE.md`](CLAUDE.md).

## Status

The full pipeline is built and tested against a fake client. A first end-to-end run on a real novel with a live API key is still to come.

Recap content sits in a legal gray area. See "Record-keeping for fair use" in the spec, and consult an IP lawyer before scaling.
