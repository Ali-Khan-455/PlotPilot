"""Plain helpers shared by PlotPilot's final-stage tests and the Image-Sync tests (no fixtures here;
the `cwd` fixture lives in conftest.py so nothing imports it by name)."""
import sqlite3

import plotpilot.config as config
from plotpilot.cli import main
from tests.fakes import FakeClient

WORDS = " ".join(["word"] * 100)
MARGIN = "I am a farmer's son in a poor village."
TARGET = "Nobody expected much from me."
BODY1 = f"{TARGET} Then the guard came. I ran for the hills."
BODY2 = "I kept walking for days. The road was long. I reached the city at dusk."
HOOK = "I got reborn as a farmer's son with zero talent."
SCRIPT = f"{HOOK}\n\n{BODY1}\n\n{BODY2}\n"


def hook_reply(text=HOOK):
    return f"<<<HOOK_START>>>\n{text}\n<<<HOOK_END>>>\nhook length: 1 sentences"


def draft1():
    return (f"<<<MARGIN_START>>>\n{MARGIN}\n<<<MARGIN_END>>>\n"
            f"<<<TARGET_SENTENCE_START>>>\n{TARGET}\n<<<TARGET_SENTENCE_END>>>\n"
            f"margin is 1 sentence.\n\n{BODY1}")


def write_book(cwd, chapters=6):
    (cwd / "book.txt").write_text("\n\n".join(f"Chapter {i}\n\n{WORDS}" for i in range(1, chapters + 1)) + "\n")


def run(*args, replies=(), **kw):
    client = FakeClient(replies, auto_qc=True, **kw)
    return main(["--novel", "book.txt", *args], client=client), client


def q(sql, *a):
    with sqlite3.connect(config.DB_PATH) as conn:
        return conn.execute(sql, a).fetchall()


def all_done(replies=(), **kw):
    """Take both chunks to done; the last --accept-tracker run enters Phase 5 with `replies`."""
    run("--module", "A", replies=[draft1()])
    run("--accept-tracker", replies=["B"])
    run("--module", "B", replies=[BODY2])
    return run("--accept-tracker", replies=list(replies), **kw)


def finish_novel(cwd, chapters=6, modules=("A", "B")):
    """Write book.txt with `chapters` chapters and drive every chunk through PlotPilot to done, drafting
    chunk i with modules[i]; the last accept writes the hook. Returns the slug."""
    write_book(cwd, chapters)
    for i, module in enumerate(modules):
        body = draft1() if i == 0 else f"Chunk {i + 1} began. I kept walking for days. The road was long."
        code, _ = run("--module", module, replies=[body])
        assert code == 0, f"chunk {i + 1} did not draft"
        last = i == len(modules) - 1
        code, _ = run("--accept-tracker", replies=[hook_reply()] if last else [modules[i + 1]])
        assert code == 0, f"chunk {i + 1} did not accept"
    return "book"


def script(cwd):
    return (cwd / "scripts" / "book" / "script.txt").read_text()


def metadata(cwd):
    return (cwd / "metadata" / "book.txt").read_text()


def kinds(*ks):
    marks = ",".join("?" * len(ks))
    return q(f"SELECT kind, model, module, verdict FROM passes WHERE kind IN ({marks}) ORDER BY id", *ks)
