import csv

import pytest

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
