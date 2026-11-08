"""call_structured: request shape, JSON validation, and error-feedback retries (SDK stubbed)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import llm
from contracts import HostTurnOutput


class _Stream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


class FakeClient:
    def __init__(self, replies, stop_reason="end_turn"):
        self.replies = list(replies)
        self.requests = []
        self.stop_reason = stop_reason
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs):
        self.requests.append(kwargs)
        text = self.replies.pop(0)
        content = [SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)]
        return _Stream(SimpleNamespace(content=content, stop_reason=self.stop_reason))


GOOD = '{"host": "A", "text": "Hello there listeners.", "delivery_instruction": "warm", "covers": []}'


def test_valid_json_first_try(monkeypatch):
    fake = FakeClient([GOOD])
    monkeypatch.setattr(llm, "_client", fake)
    out = llm.call_structured("sys", "user", HostTurnOutput, cached_prefix="SOURCE", effort="low")
    assert out.text == "Hello there listeners."
    req = fake.requests[0]
    assert req["output_config"] == {"effort": "low"}
    assert req["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert req["messages"][0]["content"][1]["text"] == "user"


def test_fenced_json_is_accepted(monkeypatch):
    monkeypatch.setattr(llm, "_client", FakeClient([f"```json\n{GOOD}\n```"]))
    assert llm.call_structured("s", "u", HostTurnOutput).host == "A"


def test_retry_feeds_validation_error_back(monkeypatch):
    fake = FakeClient(['{"host": "C", "text": "x"}', GOOD])
    monkeypatch.setattr(llm, "_client", fake)
    out = llm.call_structured("s", "u", HostTurnOutput)
    assert out.host == "A"
    second = fake.requests[1]["messages"][0]["content"][-1]["text"]
    assert "Validation error" in second and '"host": "C"' in second


def test_gives_up_after_two_retries(monkeypatch):
    fake = FakeClient(["nope", "still nope", "never"])
    monkeypatch.setattr(llm, "_client", fake)
    with pytest.raises(llm.LLMError):
        llm.call_structured("s", "u", HostTurnOutput)
    assert len(fake.requests) == 3


def test_refusal_is_readable_error(monkeypatch):
    monkeypatch.setattr(llm, "_client", FakeClient([""], stop_reason="refusal"))
    with pytest.raises(llm.LLMError, match="declined"):
        llm.call_structured("s", "u", HostTurnOutput)
