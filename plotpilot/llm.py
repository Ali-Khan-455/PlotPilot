"""Thin wrapper over the Anthropic client: model-ID check, streaming calls, usage logging."""

import csv
from datetime import datetime, timezone
from pathlib import Path

import anthropic

USAGE_HEADER = ["timestamp", "novel", "chunk", "kind", "model", "stop_reason", "input_tokens",
                "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"]


class LLMError(Exception):
    def __init__(self, message, text="", stop_reason=None):
        super().__init__(message)
        self.text = text
        self.stop_reason = stop_reason


class LLM:
    def __init__(self, client, log_dir):
        """client: an Anthropic client, or a zero-arg factory called on first use."""
        self._make = client if callable(client) else (lambda: client)
        self._client = None
        self.log_dir = Path(log_dir)
        self._verified: set[str] = set()

    @property
    def client(self):
        if self._client is None:
            self._client = self._make()
        return self._client

    def check_models(self, ids):
        for model_id in ids:
            if model_id in self._verified:
                continue
            try:
                self.client.models.retrieve(model_id)
            except anthropic.NotFoundError:
                raise LLMError(f"Model ID '{model_id}' not found — check config.py or "
                               "--gen-model/--qc-model.") from None
            self._verified.add(model_id)

    def call(self, kind, model, user, *, system=None, max_tokens, slug, chunk_idx) -> str:
        kwargs = dict(model=model, max_tokens=max_tokens, messages=[{"role": "user", "content": user}])
        if system is not None:
            kwargs["system"] = system
        with self.client.messages.stream(**kwargs) as stream:
            msg = stream.get_final_message()
        self._log_usage(slug, chunk_idx, kind, model, msg)
        text = "".join(b.text for b in msg.content if b.type == "text")
        if msg.stop_reason != "end_turn":
            detail = ""
            if msg.stop_reason == "refusal" and getattr(msg, "stop_details", None):
                detail = f" (category: {msg.stop_details.category})"
            raise LLMError(f"{kind} call stopped: {msg.stop_reason}{detail}", text, msg.stop_reason)
        return text

    def _log_usage(self, slug, chunk_idx, kind, model, msg):
        self.log_dir.mkdir(parents=True, exist_ok=True)
        path = self.log_dir / "usage.csv"
        new = not path.exists()
        u = msg.usage
        with open(path, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(USAGE_HEADER)
            w.writerow([datetime.now(timezone.utc).isoformat(), slug, chunk_idx, kind, model,
                        msg.stop_reason, u.input_tokens, u.output_tokens,
                        u.cache_creation_input_tokens or 0, u.cache_read_input_tokens or 0])
