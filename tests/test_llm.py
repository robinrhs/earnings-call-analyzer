"""The Anthropic API is never called in tests: a fake client stands in for it."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from earnings_call_analyzer.llm import (
    DEFAULT_MODEL,
    LLMError,
    MissingApiKeyError,
    brief_json_schema,
    build_request,
    call_claude,
    load_replay,
    resolve_model,
    save_recording,
)
from earnings_call_analyzer.pricing import (
    PRICES_PER_MILLION,
    cost_usd,
    estimate_run,
    estimate_tokens,
)
from earnings_call_analyzer.transcript import parse_transcript

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
CALL = "Jane Doe -- Chief Executive Officer\n\nRevenue grew 10% to $100 million this quarter.\n"


def replay_brief() -> dict:
    data = json.loads((EXAMPLES / "replay" / "fernhill_q3_fy2026.json").read_text())
    return data["response"]


class FakeStream:
    def __init__(self, message):
        self._message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._message


class FakeClient:
    """Mimics client.beta.messages.stream(...) and records the request."""

    def __init__(self, text: str, stop_reason: str = "end_turn"):
        self.requests: list[dict] = []
        message = SimpleNamespace(
            content=[
                SimpleNamespace(type="thinking", thinking=""),
                SimpleNamespace(type="text", text=text),
            ],
            stop_reason=stop_reason,
            model="claude-opus-5-5",
            usage=SimpleNamespace(input_tokens=12_000, output_tokens=5_000),
        )
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))
        self._message = message

    def _stream(self, **kwargs):
        self.requests.append(kwargs)
        return FakeStream(self._message)


def test_schema_is_strict_everywhere():
    """Structured outputs need additionalProperties: false and all fields required."""
    schema = brief_json_schema()
    objects = [schema, *schema["$defs"].values()]
    for obj in objects:
        assert obj["type"] == "object"
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])


def test_build_request_sends_paragraph_ids_schema_and_model():
    transcript = parse_transcript(CALL)
    request = build_request(transcript, "claude-sonnet-5-5", effort="medium")
    assert request["model"] == "claude-sonnet-5-5"
    content = request["messages"][0]["content"]
    assert "[P001] Jane Doe (management, prepared remarks): Revenue grew 10%" in content
    assert request["output_config"]["format"]["type"] == "json_schema"
    assert request["output_config"]["effort"] == "medium"
    assert request["fallbacks"] == "default"
    assert "thinking" not in request  # adaptive thinking is the model default


def test_resolve_model_precedence(monkeypatch):
    monkeypatch.delenv("ECA_MODEL", raising=False)
    assert resolve_model(None) == DEFAULT_MODEL
    monkeypatch.setenv("ECA_MODEL", "claude-sonnet-5-5")
    assert resolve_model(None) == "claude-sonnet-5-5"
    assert resolve_model("claude-haiku-5-5") == "claude-haiku-5-5"


def test_missing_api_key_gives_clear_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(MissingApiKeyError, match="ANTHROPIC_API_KEY is not set"):
        call_claude(parse_transcript(CALL), DEFAULT_MODEL)


def test_call_claude_validates_reply_with_mocked_client():
    client = FakeClient(json.dumps(replay_brief()))
    result, raw = call_claude(parse_transcript(CALL), DEFAULT_MODEL, client=client)
    assert result.brief.company.startswith("Fernhill")
    assert result.usage.input_tokens == 12_000
    assert result.source == "api"
    assert json.loads(raw)["period"] == "Q3 FY2026"
    assert client.requests[0]["model"] == DEFAULT_MODEL


def test_invalid_json_shape_raises_llm_error():
    client = FakeClient(json.dumps({"company": "X"}))
    with pytest.raises(LLMError, match="does not match the brief schema"):
        call_claude(parse_transcript(CALL), DEFAULT_MODEL, client=client)


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_unusable_stop_reasons_raise(stop_reason):
    client = FakeClient("{}", stop_reason=stop_reason)
    with pytest.raises(LLMError):
        call_claude(parse_transcript(CALL), DEFAULT_MODEL, client=client)


def test_record_then_replay_roundtrip(tmp_path):
    client = FakeClient(json.dumps(replay_brief()))
    result, raw = call_claude(parse_transcript(CALL), DEFAULT_MODEL, client=client)
    path = tmp_path / "rec.json"
    save_recording(path, result, raw)
    replayed = load_replay(path)
    assert replayed.brief == result.brief
    assert replayed.usage == result.usage
    assert replayed.source == "replay"


def test_replay_rejects_wrong_format(tmp_path):
    path = tmp_path / "x.json"
    path.write_text(json.dumps({"response": {}}))
    with pytest.raises(LLMError, match="not a replay file"):
        load_replay(path)


def test_example_replay_files_are_valid():
    for name in ("fernhill_q2_fy2026.json", "fernhill_q3_fy2026.json"):
        result = load_replay(EXAMPLES / "replay" / name)
        assert result.usage is None  # hand-authored: we do not pretend to have token counts
        assert "Hand-authored" in result.note


def test_cost_estimate():
    assert estimate_tokens("abcd" * 100) == 100
    assert cost_usd("claude-opus-5-5", 1_000_000, 1_000_000) == pytest.approx(24.0)
    assert cost_usd("unknown-model", 1, 1) is None
    estimate = estimate_run("claude-opus-5-5", "x" * 40_000)
    assert estimate.input_tokens == 10_000
    assert estimate.low_usd == pytest.approx(10_000 / 1e6 * 4 + 4_000 / 1e6 * 20)


def test_default_model_has_a_price():
    assert DEFAULT_MODEL in PRICES_PER_MILLION
