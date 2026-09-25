"""A scripted stand-in for anthropic.Anthropic. Tests never reach the real API."""

from types import SimpleNamespace

import anthropic
import httpx2

_REQ = httpx2.Request("GET", "https://api.anthropic.com")


def not_found(msg="model not found"):
    return anthropic.NotFoundError(msg, response=httpx2.Response(404, request=_REQ), body=None)


def connection_error():
    return anthropic.APIConnectionError(request=_REQ)


class _Stream:
    def __init__(self, message):
        self._message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._message


class FakeClient:
    """replies: str (end_turn) or (text, stop_reason) or an exception to raise."""

    def __init__(self, replies=(), unknown_models=(), auto_qc=False):
        self.replies = list(replies)
        self.auto_qc = auto_qc
        self.unknown = set(unknown_models)
        self.calls = []       # kwargs of each messages.stream call
        self.retrieved = []   # model ids passed to models.retrieve
        self.messages = SimpleNamespace(stream=self._stream)
        self.models = SimpleNamespace(retrieve=self._retrieve)

    @staticmethod
    def _auto_qc(user):
        """Clean answers for Prompts 6, 7 and 9 (echoing the narration for 9)."""
        from plotpilot.prompts import load_prompts
        P = load_prompts()
        if user.startswith(P["6"].text.split("[Paste")[0]):
            return "5. **Texture gaps**\n- None\n\n6. **TTS hazards**\n- None"
        if user.startswith(P["7"].text.split("[Paste")[0]):
            return "Step 4: verdict PASS"
        prefix = P["9"].text.split("[Paste narration here]")[0]
        if user.startswith(prefix):
            return user[len(prefix):]
        return None

    def _retrieve(self, model_id):
        self.retrieved.append(model_id)
        if model_id in self.unknown:
            raise not_found()
        return SimpleNamespace(id=model_id, max_input_tokens=200_000)

    def _stream(self, **kwargs):
        self.calls.append(kwargs)
        if not self.replies:
            reply = self._auto_qc(kwargs["messages"][0]["content"]) if self.auto_qc else None
            if reply is None:
                raise AssertionError("FakeClient ran out of scripted replies")
        else:
            reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        text, stop = (reply, "end_turn") if isinstance(reply, str) else reply
        return _Stream(SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
            stop_reason=stop,
            stop_details=SimpleNamespace(category="cyber") if stop == "refusal" else None,
            usage=SimpleNamespace(input_tokens=100, output_tokens=len(text.split()),
                                  cache_creation_input_tokens=None, cache_read_input_tokens=None),
        ))
