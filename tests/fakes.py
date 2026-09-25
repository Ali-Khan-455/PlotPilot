"""A scripted stand-in for anthropic.Anthropic. Tests never reach the real API."""

from types import SimpleNamespace

import anthropic
import httpx2

_REQ = httpx2.Request("GET", "https://api.anthropic.com")


def not_found(msg="model not found"):
    return anthropic.NotFoundError(msg, response=httpx2.Response(404, request=_REQ), body=None)


def connection_error():
    return anthropic.APIConnectionError(request=_REQ)


AUTO_AUDIT = "5. **Texture gaps**\n- None\n\n6. **TTS hazards**\n- None"
AUTO_PASS = "Step 4: verdict PASS"
AUTO_DELTA = ('{"new_characters": [], "new_terms": [], "new_comparisons": [], "new_texture_motifs": [],'
              ' "chunk_end_state": "It continues.", "nickname_collisions": []}')


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
        """auto_qc: False; True (auto-answer Prompts 6, 7, 9, 10, 11); or "tracker" (only 10 and 11,
        so any unexpected QC call still fails with "ran out of scripted replies")."""
        self.replies = list(replies)
        self.auto_qc = auto_qc
        self.unknown = set(unknown_models)
        self.calls = []       # kwargs of each messages.stream call
        self.retrieved = []   # model ids passed to models.retrieve
        self.messages = SimpleNamespace(stream=self._stream)
        self.models = SimpleNamespace(retrieve=self._retrieve)

    @staticmethod
    def _auto_qc(user, mode=True):
        """Clean answers for Prompts 6, 7, 9 (echo), 10 (empty delta) and 11 (one scene)."""
        from plotpilot.prompts import load_prompts
        P = load_prompts()
        qc = mode is True
        if qc and user.startswith(P["6"].text.split("[Paste")[0]):
            return AUTO_AUDIT
        if qc and user.startswith(P["7"].text.split("[Paste")[0]):
            return AUTO_PASS
        prefix = P["9"].text.split("[Paste narration here]")[0]
        if qc and user.startswith(prefix):
            return user[len(prefix):]
        if user.startswith(P["10"].text.split("Chunk number:")[0]):
            return AUTO_DELTA
        prefix = P["11"].text.split("[Paste finished narration for this chunk]")[0]
        if user.startswith(prefix):
            import json
            from plotpilot.parse import first_sentence
            return json.dumps({"scenes": [{"first_sentence": first_sentence(user[len(prefix):]),
                                           "description": "scene"}]})
        return None

    def _retrieve(self, model_id):
        self.retrieved.append(model_id)
        if model_id in self.unknown:
            raise not_found()
        return SimpleNamespace(id=model_id, max_input_tokens=200_000)

    def _stream(self, **kwargs):
        self.calls.append(kwargs)
        if not self.replies:
            reply = self._auto_qc(kwargs["messages"][0]["content"], self.auto_qc) if self.auto_qc else None
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
