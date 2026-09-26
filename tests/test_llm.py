import csv
from types import SimpleNamespace

import pytest

import plotpilot.config as config
from plotpilot.llm import LLM, LLMError
from tests.fakes import FakeClient


def test_call_returns_text_and_logs_usage(tmp_path):
    llm = LLM(FakeClient(["hello there", "again"]), tmp_path / "logs")
    assert llm.call("draft", "m", "user", system="sys", max_tokens=10, slug="nov", chunk_idx=1) == "hello there"
    llm.call("classify", "q", "user", max_tokens=10, slug="nov", chunk_idx=1)
    rows = list(csv.reader(open(tmp_path / "logs" / "usage.csv")))
    assert rows[0][:6] == ["timestamp", "novel", "chunk", "kind", "model", "stop_reason"]
    assert [r[3] for r in rows[1:]] == ["draft", "classify"]
    assert rows[1][5:] == ["end_turn", "100", "2", "0", "0"]


def test_system_is_sent_only_when_given(tmp_path):
    client = FakeClient(["a", "b"])
    llm = LLM(client, tmp_path)
    llm.call("draft", "m", "u", system="s", max_tokens=5, slug="n", chunk_idx=1)
    llm.call("classify", "m", "u", max_tokens=5, slug="n", chunk_idx=1)
    assert client.calls[0]["system"] == "s" and "system" not in client.calls[1]
    assert client.calls[0]["messages"] == [{"role": "user", "content": "u"}]


@pytest.mark.parametrize("stop", ["max_tokens", "refusal", "pause_turn"])
def test_non_end_turn_raises_with_text_and_still_logs(tmp_path, stop):
    llm = LLM(FakeClient([("partial", stop)]), tmp_path)
    with pytest.raises(LLMError) as e:
        llm.call("draft", "m", "u", max_tokens=5, slug="n", chunk_idx=1)
    assert e.value.text == "partial" and e.value.stop_reason == stop
    assert len(list(csv.reader(open(tmp_path / "usage.csv")))) == 2


def test_check_models_caches_and_maps_not_found(tmp_path):
    client = FakeClient(unknown_models={"bad"})
    llm = LLM(client, tmp_path)
    llm.check_models(["good"])
    llm.check_models(["good"])
    assert client.retrieved == ["good"]
    with pytest.raises(LLMError, match="Model ID 'bad' not found"):
        llm.check_models(["bad"])


def test_client_is_created_lazily(tmp_path):
    made = []
    llm = LLM(lambda: made.append(1) or FakeClient(["x"]), tmp_path)
    assert made == []
    llm.call("draft", "m", "u", max_tokens=5, slug="n", chunk_idx=1)
    assert made == [1]


def test_context_limit_from_retrieve_cached(tmp_path):
    client = FakeClient(context=123_456)
    llm = LLM(client, tmp_path)
    llm.check_models(["m"])
    assert llm.context_limit("m") == 123_456 and client.retrieved == ["m"]


@pytest.mark.parametrize("info", [SimpleNamespace(id="m"), SimpleNamespace(id="m", max_input_tokens=None)])
def test_context_limit_falls_back(tmp_path, info):
    client = FakeClient()
    client.models.retrieve = lambda _id: info
    llm = LLM(client, tmp_path)
    assert llm.context_limit("m") == config.GEN_CONTEXT_TOKENS


def test_count_tokens_passes_system_and_messages(tmp_path):
    client = FakeClient(token_count=42)
    assert LLM(client, tmp_path).count_tokens("m", "u", "s") == 42
    assert client.counted == [{"model": "m", "system": "s", "messages": [{"role": "user", "content": "u"}]}]


def test_count_tokens_auth_error(tmp_path):
    client = FakeClient()
    auth = TypeError("Could not resolve authentication method")
    client.messages.count_tokens = lambda **_: (_ for _ in ()).throw(auth)
    with pytest.raises(LLMError, match="credentials"):
        LLM(client, tmp_path).count_tokens("m", "u", "s")


def test_failed_stream_still_logs_a_usage_row(tmp_path):
    from tests.fakes import connection_error
    llm = LLM(FakeClient([connection_error()]), tmp_path)
    with pytest.raises(Exception):
        llm.call("draft", "m", "u", max_tokens=5, slug="n", chunk_idx=1)
    rows = list(csv.reader(open(tmp_path / "usage.csv")))
    assert rows[1][3:6] == ["draft", "m", "error:APIConnectionError"] and rows[1][6:] == ["", "", "", ""]


def test_context_limit_zero_is_not_missing_and_override_fallback_warns(tmp_path, capsys):
    client = FakeClient(context=0)
    assert LLM(client, tmp_path).context_limit("m") == 0
    client = FakeClient()
    client.models.retrieve = lambda _id: SimpleNamespace(id=_id)
    LLM(client, tmp_path).context_limit("claude-other")
    assert "context window of claude-other is unknown" in capsys.readouterr().out
