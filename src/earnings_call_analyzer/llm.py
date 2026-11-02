"""Call Claude to extract the brief, or replay a recorded response.

Design choices:

* **Structured output.** The request carries the JSON schema of :class:`Brief`
  (``output_config.format``), so the API constrains the reply to valid JSON of that
  shape. Pydantic then validates it again locally; we never trust unvalidated JSON.
* **API key from the environment only.** ``ANTHROPIC_API_KEY`` is read from the
  environment and never written anywhere. Missing key -> a clear error, not a stack trace.
* **Replay.** ``--replay file.json`` loads a recorded response instead of calling the
  API. That makes the example in this repository reproducible without a key, and lets
  tests run offline. ``--record file.json`` saves a real response in the same format.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import anthropic
from pydantic import ValidationError

from .prompt import SYSTEM_PROMPT, build_user_message
from .schema import Brief
from .transcript import Transcript

DEFAULT_MODEL = "claude-opus-5-5"
MODEL_ENV_VAR = "ECA_MODEL"
API_KEY_ENV_VAR = "ANTHROPIC_API_KEY"
MAX_TOKENS = 32_000
# If the model declines a request, the API retries it on a fallback model chosen by
# Anthropic ("default" mode) instead of returning an empty answer.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
REPLAY_FORMAT = "earnings-call-analyzer/replay-v1"

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class MissingApiKeyError(RuntimeError):
    pass


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class LLMResult:
    brief: Brief
    model: str
    usage: Usage | None
    source: Literal["api", "replay"]
    note: str = ""


def resolve_model(cli_value: str | None = None) -> str:
    """Model from the CLI flag, else the ECA_MODEL environment variable, else the default."""
    return cli_value or os.environ.get(MODEL_ENV_VAR) or DEFAULT_MODEL


def brief_json_schema() -> dict[str, Any]:
    """JSON schema of the brief in the form the structured-output API accepts."""
    return anthropic.transform_schema(Brief)


def build_request(transcript: Transcript, model: str, effort: Effort = "high") -> dict[str, Any]:
    """Keyword arguments for ``client.beta.messages.stream``. Pure, so it can be tested."""
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": build_user_message(transcript)}],
        "output_config": {
            "effort": effort,
            "format": {"type": "json_schema", "schema": brief_json_schema()},
        },
        "betas": [FALLBACK_BETA],
        "fallbacks": "default",
    }


def parse_brief(text: str) -> Brief:
    try:
        return Brief.model_validate_json(text)
    except ValidationError as exc:
        raise LLMError(f"The model's reply does not match the brief schema:\n{exc}") from exc


def _make_client() -> anthropic.Anthropic:
    api_key = os.environ.get(API_KEY_ENV_VAR, "").strip()
    if not api_key:
        raise MissingApiKeyError(
            f"{API_KEY_ENV_VAR} is not set. Export your Anthropic API key "
            f"(export {API_KEY_ENV_VAR}=...) or use --replay with a recorded response."
        )
    return anthropic.Anthropic(api_key=api_key)


def call_claude(
    transcript: Transcript,
    model: str,
    effort: Effort = "high",
    client: Any | None = None,
) -> tuple[LLMResult, str]:
    """Send the transcript to Claude and return the validated brief and the raw JSON text."""
    client = client or _make_client()
    request = build_request(transcript, model, effort)
    try:
        # Streaming avoids HTTP timeouts on long transcripts; we only need the final message.
        with client.beta.messages.stream(**request) as stream:
            message = stream.get_final_message()
    except anthropic.AuthenticationError as exc:
        raise LLMError(f"Authentication failed - check {API_KEY_ENV_VAR}.") from exc
    except anthropic.NotFoundError as exc:
        raise LLMError(f"Model '{model}' was not found. Check --model / {MODEL_ENV_VAR}.") from exc
    except anthropic.RateLimitError as exc:
        raise LLMError("Rate limited by the API. Wait a moment and try again.") from exc
    except anthropic.APIStatusError as exc:
        raise LLMError(f"API error {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise LLMError("Could not reach the Anthropic API. Check your connection.") from exc

    if message.stop_reason == "refusal":
        raise LLMError("The model declined to process this transcript.")
    if message.stop_reason == "max_tokens":
        raise LLMError(f"The reply hit max_tokens ({MAX_TOKENS}) and is incomplete.")

    text = "".join(block.text for block in message.content if block.type == "text")
    brief = parse_brief(text)
    usage = Usage(message.usage.input_tokens, message.usage.output_tokens)
    return LLMResult(brief=brief, model=message.model, usage=usage, source="api"), text


# --- Replay -----------------------------------------------------------------------------


def save_recording(path: str | Path, result: LLMResult, raw_text: str) -> None:
    data = {
        "format": REPLAY_FORMAT,
        "note": "Recorded response from the Anthropic API.",
        "model": result.model,
        "usage": None
        if result.usage is None
        else {
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
        },
        "response": json.loads(raw_text),
    }
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_replay(path: str | Path) -> LLMResult:
    """Load a recorded (or hand-authored) model response."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("format") != REPLAY_FORMAT:
        raise LLMError(f"{path} is not a replay file (expected format '{REPLAY_FORMAT}').")
    usage = data.get("usage")
    return LLMResult(
        brief=parse_brief(json.dumps(data["response"])),
        model=str(data.get("model", "unknown")),
        usage=Usage(**usage) if usage else None,
        source="replay",
        note=str(data.get("note", "")),
    )
