"""Phase 5: D17 context check, Prompt 5 hook, Prompt 9 on the hook, splice (D20), script, metadata (R5)."""
import json
import sqlite3

import pytest

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


@pytest.fixture
def cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "book.txt").write_text("\n\n".join(f"Chapter {i}\n\n{WORDS}" for i in range(1, 7)) + "\n")
    return tmp_path


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


def script(cwd):
    return (cwd / "scripts" / "book" / "script.txt").read_text()


def metadata(cwd):
    return (cwd / "metadata" / "book.txt").read_text()


def kinds(*ks):
    marks = ",".join("?" * len(ks))
    return q(f"SELECT kind, model, module, verdict FROM passes WHERE kind IN ({marks}) ORDER BY id", *ks)


def test_happy_path(cwd, capsys):
    assert not (cwd / "metadata").exists()
    capsys.readouterr()
    code, client = all_done([hook_reply()])
    assert code == 0
    p5, p9 = client.calls
    assert p5["model"] == config.GEN_MODEL and p5["max_tokens"] == config.HOOK_MAX_TOKENS
    assert "You are converting a novel" in p5["system"] and "This batch is isekai" in p5["system"]
    user = p5["messages"][0]["content"]
    assert f'"{TARGET}"' in user and MARGIN in user and BODY1 in user and BODY2 in user
    assert p9["model"] == config.QC_MODEL and HOOK in p9["messages"][0]["content"]
    assert len(client.counted) == 1 and client.counted[0]["system"] == p5["system"]
    assert script(cwd) == SCRIPT
    assert metadata(cwd) == "[00:00] SCENE: scene\n[00:09] SCENE: scene\n"
    assert (cwd / "scripts" / "book" / "chunk-01.txt").read_text().startswith(MARGIN + "\n\n")
    assert kinds("hook", "hook_tts") == [("hook", config.GEN_MODEL, "A", None),
                                         ("hook_tts", config.QC_MODEL, None, None)]
    usage = (cwd / "logs" / "usage.csv").read_text().splitlines()
    assert usage[-2].split(",")[3] == "hook" and usage[-1].split(",")[3] == "hook_tts"
    out = capsys.readouterr().out
    assert "All 2 chunks done" in out and f"Hook: {HOOK}" in out
    assert "script.txt (39 words, ~1 minute at 150 wpm)." in out
    assert "Metadata → metadata/book.txt (2 scenes)." in out


def test_rerun_makes_no_calls_and_same_files(cwd):
    all_done([hook_reply()])
    before = (script(cwd), metadata(cwd))
    code, client = run()
    assert code == 0 and client.calls == [] and client.counted == [] and client.retrieved == []
    assert (script(cwd), metadata(cwd)) == before


def test_d17_context_exceeded(cwd, capsys):
    capsys.readouterr()
    code, client = all_done(token_count=195_000)
    assert code == 1 and client.calls == [] and kinds("hook") == []
    msg = ("Part 1 assembled script is 38 words (~195,000 tokens). Prompt 5 requires the full script as "
           f"context. Exceeds {config.GEN_MODEL}'s context window. Split the novel into explicit Parts or "
           "reduce chunk count.")
    assert msg in capsys.readouterr().out
    assert msg in (cwd / "logs" / "errors.log").read_text()
    code, client = run(replies=[hook_reply()], context=1_000_000, token_count=195_000)
    assert code == 0 and script(cwd) == SCRIPT


def test_hook_malformed_then_good(cwd):
    code, _ = all_done(["no delimiters", hook_reply()])
    assert code == 0 and [k[3] for k in kinds("hook")] == ["PARSE_FAILED", None]


def test_hook_malformed_twice_then_rerun(cwd, capsys):
    capsys.readouterr()
    code, _ = all_done(["bad", "bad"])
    assert code == 1 and "Hook output was malformed twice" in capsys.readouterr().out
    assert not (cwd / "scripts" / "book" / "script.txt").exists()
    code, client = run(replies=[hook_reply()])
    assert code == 0 and len(client.calls) == 2 and script(cwd) == SCRIPT


def test_hook_stopped(cwd):
    code, _ = all_done([("<<<HOOK_START>>>\nI got", "max_tokens")])
    assert code == 1 and kinds("hook")[0][3] == "STOPPED:max_tokens"


def test_hook_tts_malformed_twice_reruns_only_p9(cwd, capsys):
    hook3 = "I was 3 years old and already a farmer's son."
    code, _ = all_done([hook_reply(hook3), "junk", "junk"])
    assert code == 1 and "Hook TTS normalization output was malformed twice" in capsys.readouterr().out
    fixed = hook3.replace("3", "three")
    code, client = run(replies=[fixed])
    assert code == 0 and len(client.calls) == 1 and client.counted == []
    assert client.calls[0]["model"] == config.QC_MODEL
    assert script(cwd).startswith(fixed + "\n\n" + TARGET)
    assert [k[0] for k in kinds("hook", "hook_tts")] == ["hook", "hook_tts", "hook_tts", "hook_tts"]


def test_splice_precheck_blocks_before_p5(cwd, capsys):
    all_done(token_count=10**9)  # stop at D17, before the corruption, so both chunks are done
    [(vid, raw)] = q("SELECT id, json FROM tracker_versions ORDER BY id DESC LIMIT 1")
    tr = json.loads(raw)
    tr["chunk1"]["target"] = "Something else entirely."
    with sqlite3.connect(config.DB_PATH) as conn:  # test-only corruption
        conn.execute("UPDATE tracker_versions SET json = ? WHERE id = ?", (json.dumps(tr), vid))
    capsys.readouterr()
    code, client = run(replies=[hook_reply()])
    assert code == 1 and client.calls == [] and client.counted == []
    assert "hook splice check failed" in capsys.readouterr().out
    assert "hook splice check failed" in (cwd / "logs" / "errors.log").read_text()
    assert not (cwd / "scripts" / "book" / "script.txt").exists()


def test_missing_scene_warns_and_uses_previous_timestamp(cwd, capsys):
    all_done([hook_reply()])
    with sqlite3.connect(config.DB_PATH) as conn:  # a later ok scenes pass for chunk 2 (test-only)
        cid = conn.execute("SELECT id FROM chunks WHERE idx = 2").fetchone()[0]
        conn.execute("INSERT INTO passes (novel_id, chunk_id, kind, model, input_text, output_text, created_at)"
                     " VALUES (1, ?, 'scenes', 'm', '', ?, 'now')",
                     (cid, json.dumps({"scenes": [{"first_sentence": "Not in the text at all.",
                                                   "description": "ghost"}]})))
    capsys.readouterr()
    code, _ = run()
    assert code == 0 and metadata(cwd) == "[00:00] SCENE: scene\n[00:00] SCENE: ghost\n"
    assert "WARNING: scene \"Not in the text at all.…\" (chunk 2) not found" in capsys.readouterr().out
    log = cwd / "logs" / "errors.log"
    assert not log.exists() or "ghost" not in log.read_text()  # printed only, never logged


def test_flag_notes_on_all_done(cwd, capsys):
    all_done([hook_reply()])
    capsys.readouterr()
    code, client = run("--redraft", "--module", "C")
    out = capsys.readouterr().out
    assert code == 0 and client.calls == []
    assert "--module is ignored; every chunk is done" in out and "--redraft ignored" in out


def test_edited_done_chunk_uses_stored_text(cwd, capsys):
    all_done([hook_reply()])
    (cwd / "scripts" / "book" / "chunk-02.txt").write_text("Totally rewritten by hand.\n")
    capsys.readouterr()
    code, _ = run()
    assert code == 0 and "chunk-02.txt was edited after its tracker was merged" in capsys.readouterr().out
    assert script(cwd) == SCRIPT


def test_history_is_append_only(cwd):
    run("--module", "A", replies=[draft1()])
    run("--accept-tracker", replies=["B"])
    run("--module", "B", replies=[BODY2])
    before = q("SELECT * FROM passes ORDER BY id")
    run("--accept-tracker", replies=["bad", hook_reply()])
    run()
    after = q("SELECT * FROM passes ORDER BY id")
    assert after[:len(before)] == before and len(after) == len(before) + 3


# --- deferred minors (Phase 5) --------------------------------------------------

def test_edited_script_is_regenerated_with_a_warning(cwd, capsys):
    all_done([hook_reply()])
    (cwd / "scripts" / "book" / "script.txt").write_text("hand fix\n")
    (cwd / "metadata" / "book.txt").write_text("[00:00] SCENE: hand fix\n")
    capsys.readouterr()
    run()
    out = capsys.readouterr().out
    assert "script.txt differed from the stored narration" in out and "book.txt differed from the stored scenes" in out
    assert script(cwd) == SCRIPT


def test_count_tokens_bad_request_is_d17(cwd, capsys, monkeypatch):
    import anthropic
    import httpx2
    req = httpx2.Request("POST", "https://api.anthropic.com")
    err = anthropic.BadRequestError("prompt is too long", response=httpx2.Response(400, request=req), body=None)
    monkeypatch.setattr(FakeClient, "_count_tokens", lambda self, **k: (_ for _ in ()).throw(err))
    capsys.readouterr()
    code, client = all_done()
    out = capsys.readouterr().out
    assert code == 1 and client.calls == []
    assert "Prompt 5 requires the full script as context" in out and "prompt is too long" in out


def test_other_count_tokens_400_is_surfaced(cwd, capsys, monkeypatch):
    import anthropic
    import httpx2
    req = httpx2.Request("POST", "https://api.anthropic.com")
    err = anthropic.BadRequestError("messages: invalid content block", response=httpx2.Response(400, request=req),
                                    body=None)
    monkeypatch.setattr(FakeClient, "_count_tokens", lambda self, **k: (_ for _ in ()).throw(err))
    capsys.readouterr()
    code, _ = all_done()
    out, errout = capsys.readouterr()
    assert code == 1 and "context window" not in out and "invalid content block" in errout
