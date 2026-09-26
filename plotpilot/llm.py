"""Thin wrapper over the Anthropic client: model-ID check, streaming calls, usage logging."""

import csv
from datetime import datetime, timezone
from pathlib import Path

import anthropic

from plotpilot import config

USAGE_HEADER = ["timestamp", "novel", "chunk", "kind", "model", "stop_reason", "input_tokens",
                "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"]


CREDENTIALS_MSG = ("No Anthropic credentials found — set ANTHROPIC_API_KEY "
                   "(or ANTHROPIC_AUTH_TOKEN, or run `ant auth login`).")


def log_error(log_dir, msg: str):
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    with open(Path(log_dir) / "errors.log", "a") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat()} {msg}\n")


def _is_auth_error(e: Exception) -> bool:
    # anthropic 1.x raises TypeError at request time when no credentials resolve.
    return isinstance(e, TypeError) and "authentication" in str(e).lower()


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
        self._limits: dict[str, int | None] = {}

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
                info = self.client.models.retrieve(model_id)
            except anthropic.NotFoundError:
                raise LLMError(f"Model ID '{model_id}' not found — check config.py or "
                               "--gen-model/--qc-model.") from None
            except TypeError as e:
                if _is_auth_error(e):
                    raise LLMError(CREDENTIALS_MSG) from None
                raise
            self._verified.add(model_id)
            self._limits[model_id] = getattr(info, "max_input_tokens", None)

    def context_limit(self, model_id) -> int:
        """The model's input limit from models.retrieve; config.GEN_CONTEXT_TOKENS if it isn't given."""
        self.check_models([model_id])
        return self._limits.get(model_id) or config.GEN_CONTEXT_TOKENS

    def count_tokens(self, model, user, system) -> int:
        try:
            return self.client.messages.count_tokens(
                model=model, system=system, messages=[{"role": "user", "content": user}]).input_tokens
        except TypeError as e:
            if _is_auth_error(e):
                raise LLMError(CREDENTIALS_MSG) from None
            raise

    def call(self, kind, model, user, *, system=None, max_tokens, slug, chunk_idx) -> str:
        kwargs = dict(model=model, max_tokens=max_tokens, messages=[{"role": "user", "content": user}])
        if system is not None:
            kwargs["system"] = system
        try:
            with self.client.messages.stream(**kwargs) as stream:
                msg = stream.get_final_message()
        except Exception as e:
            self._log_row(slug, chunk_idx, kind, model, f"error:{type(e).__name__}", ["", "", "", ""])
            if _is_auth_error(e):
                raise LLMError(CREDENTIALS_MSG) from None
            raise
        self._log_usage(slug, chunk_idx, kind, model, msg)
        text = "".join(b.text for b in msg.content if b.type == "text")
        if msg.stop_reason != "end_turn":
            detail = ""
            if msg.stop_reason == "refusal" and getattr(msg, "stop_details", None):
                detail = f" (category: {msg.stop_details.category})"
            raise LLMError(f"{kind} call stopped: {msg.stop_reason}{detail}", text, msg.stop_reason)
        return text

    def _log_usage(self, slug, chunk_idx, kind, model, msg):
        u = msg.usage
        self._log_row(slug, chunk_idx, kind, model, msg.stop_reason,
                      [u.input_tokens, u.output_tokens, u.cache_creation_input_tokens or 0,
                       u.cache_read_input_tokens or 0])

    def _log_row(self, slug, chunk_idx, kind, model, stop_reason, tokens):
        """One usage.csv row per call; a call that fails mid-stream gets empty token fields."""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        path = self.log_dir / "usage.csv"
        new = not path.exists()
        with open(path, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(USAGE_HEADER)
            w.writerow([datetime.now(timezone.utc).isoformat(), slug, chunk_idx, kind, model, stop_reason, *tokens])
