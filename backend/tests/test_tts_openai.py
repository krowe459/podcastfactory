"""OpenAI TTS provider: request shape and error mapping, with no network."""

import io

import httpx
import pytest
from pydub import AudioSegment

from agents import audio


def _mp3_bytes() -> bytes:
    buf = io.BytesIO()
    AudioSegment.silent(duration=200).export(buf, format="mp3")
    return buf.getvalue()


def _provider(monkeypatch, handler) -> audio.OpenAITTSProvider:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(audio.time, "sleep", lambda s: None)
    p = audio.OpenAITTSProvider()
    p._client = httpx.Client(transport=httpx.MockTransport(handler))
    return p


def test_sends_voice_and_instructions(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(__import__("json").loads(request.content))
        return httpx.Response(200, content=_mp3_bytes())

    seg = _provider(monkeypatch, handler).synthesize("Hello there.", "coral", "Upbeat.")
    assert len(seg) > 0
    assert seen["voice"] == "coral" and seen["instructions"] == "Upbeat."
    assert seen["input"] == "Hello there."


def test_no_credits_is_readable(monkeypatch):
    def handler(request):
        return httpx.Response(429, json={"error": {"type": "insufficient_quota", "message": "x"}})

    with pytest.raises(audio.TTSError, match="no credits"):
        _provider(monkeypatch, handler).synthesize("Hi.", "ash", "")


def test_retries_rate_limit_then_succeeds(monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, json={"error": {"type": "rate_limit_exceeded"}})
        return httpx.Response(200, content=_mp3_bytes())

    _provider(monkeypatch, handler).synthesize("Hi.", "ash", "")
    assert len(calls) == 3


def test_missing_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(audio.TTSError, match="OPENAI_API_KEY"):
        audio.OpenAITTSProvider()


def test_combine_delivery():
    assert audio.combine_delivery("Enthusiastic.", "measured") == "Enthusiastic. For this line: measured"
    assert audio.combine_delivery("", "measured") == "measured"
    assert audio.combine_delivery("Lively", "") == "Lively"


def test_speed_sent_only_when_set(monkeypatch):
    bodies = []

    def handler(request):
        bodies.append(__import__("json").loads(request.content))
        return httpx.Response(200, content=_mp3_bytes())

    p = _provider(monkeypatch, handler)
    monkeypatch.setattr(audio.settings, "OPENAI_TTS_SPEED", 1.0)
    p.synthesize("Hi.", "ash", "")
    monkeypatch.setattr(audio.settings, "OPENAI_TTS_SPEED", 1.13)
    p.synthesize("Hi.", "ash", "")
    assert "speed" not in bodies[0] and bodies[1]["speed"] == 1.13


def test_default_pace_is_150():
    from config import Settings
    s = Settings()
    assert s.WORDS_PER_MINUTE == 150 and s.OPENAI_TTS_SPEED == 1.13
