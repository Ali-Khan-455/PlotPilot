"""Read-only access to PlotPilot's finished output in plotpilot.db. Never writes (mode=ro), and derives the
script and scene timestamps through plotpilot.final.derive_outputs, the same code run_final uses."""

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from plotpilot import assemble, db
from plotpilot.final import NotReady, derive_inputs, derive_outputs

LINE_RE = re.compile(r"^\[(\d+):(\d\d)\] SCENE: (.*)$")


class SourceError(Exception):
    pass


@dataclass(frozen=True)
class SourceChunk:
    idx: int
    label: str
    chapters_text: str
    narration: str
    scenes: list          # [(timecode "mm-ss", description)], in order; timecodes may repeat (Q9)
    module: str


@dataclass(frozen=True)
class Source:
    novel_id: int
    title: str
    plotpilot_source_sha: str   # copied verbatim from PlotPilot's novels.source_sha256
    script_sha: str             # sha256 of the derived script + metadata lines
    script_words: int
    chunks: list


def open_plotpilot(path) -> sqlite3.Connection:
    p = Path(path)
    if not p.is_file():
        raise SourceError(f"PlotPilot database not found: {p}")
    conn = sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _timecode(line: str) -> tuple[str, str]:
    m = LINE_RE.match(line)
    return f"{m[1]}-{m[2]}", m[3]


def load_novel(conn, slug) -> Source:
    try:
        return _load(conn, slug)
    except sqlite3.OperationalError as e:  # raised when a query runs, not when the connection opens
        if "locked" in str(e) or "busy" in str(e):
            raise SourceError("PlotPilot's database is busy; try again when PlotPilot has finished.") from None
        raise


def _load(conn, slug) -> Source:
    novel = conn.execute("SELECT id, title, source_sha256 FROM novels WHERE slug = ?", (slug,)).fetchone()
    if not novel:
        raise SourceError(f"PlotPilot has no novel '{slug}'; run PlotPilot on it first.")
    rows = db.chunks(conn, novel["id"])
    for r in rows:
        if r["status"] != "done":
            raise SourceError(f"chunk {r['idx']} is '{r['status']}'; finish it in PlotPilot first.")
    inp = derive_inputs(conn, novel["id"])
    try:
        out = derive_outputs(conn, novel["id"], inp)  # note: derive_outputs does not splice-check
    except NotReady as e:
        raise SourceError(f"The hook, or its TTS pass, is missing for '{slug}' ({e}); "
                          "run PlotPilot to completion first.") from None
    try:
        assemble.splice_check(out.script, inp.target)  # D20, separate from derive_outputs by design
    except assemble.SpliceError as e:
        raise SourceError(f"PlotPilot's hook splice check fails for '{slug}': {e}") from None
    chunks = [SourceChunk(r["idx"], r["label"], r["source_text"], body, [_timecode(ln) for ln in lines],
                          db.latest_pass(conn, r["id"], "draft")["module"])
              for r, body, lines in zip(rows, out.bodies, out.chunk_lines)]
    script_sha = hashlib.sha256((out.script + "\0" + "\n".join(out.lines)).encode("utf-8")).hexdigest()
    return Source(novel["id"], novel["title"], novel["source_sha256"], script_sha, len(out.script.split()),
                  chunks)
