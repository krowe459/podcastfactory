"""Runtime configuration. Every value can be overridden by an environment variable."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent
load_dotenv(BACKEND_DIR / ".env")


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw not in (None, "") else default


def _str(name: str, default: str) -> str:
    raw = os.getenv(name)
    return raw if raw not in (None, "") else default


def _voice_map() -> dict[str, str]:
    raw = os.getenv("VOICE_MAP")
    if raw:
        parsed = json.loads(raw)
        return {"A": str(parsed["A"]), "B": str(parsed["B"])}
    if _str("TTS_PROVIDER", "stub").lower() == "openai":
        return {"A": "coral", "B": "verse"}
    return {"A": "stub-voice-a", "B": "stub-voice-b"}


_HOST_B_STYLE = (
    "Sound like a real person talking with a friend, not reading a script: genuinely enthusiastic "
    "and energized by the ideas, with natural pitch variation, lively pacing, a smile in the voice, "
    "and relaxed, conversational phrasing. Lean into the surprising points."
)


def _voice_style() -> dict[str, str]:
    """A standing speaking style per host, put in front of every line's delivery note."""
    raw = os.getenv("VOICE_STYLE")
    if raw:
        parsed = json.loads(raw)
        return {"A": str(parsed.get("A", "")), "B": str(parsed.get("B", ""))}
    return {"A": "", "B": _HOST_B_STYLE}


@dataclass
class Settings:
    # Pipeline
    WORDS_PER_MINUTE: int = field(default_factory=lambda: _int("WORDS_PER_MINUTE", 135))
    DURATION_TOLERANCE: float = field(default_factory=lambda: _float("DURATION_TOLERANCE", 0.10))
    MAX_REVISIONS: int = field(default_factory=lambda: _int("MAX_REVISIONS", 2))
    MAX_SOURCE_CHARS: int = field(default_factory=lambda: _int("MAX_SOURCE_CHARS", 120000))
    CHUNK_CHARS: int = field(default_factory=lambda: _int("CHUNK_CHARS", 30000))
    MAX_TURNS_PER_SECTION: int = field(default_factory=lambda: _int("MAX_TURNS_PER_SECTION", 30))
    INCLUDE_SOURCE_IN_HOST_CONTEXT: bool = field(
        default_factory=lambda: _bool("INCLUDE_SOURCE_IN_HOST_CONTEXT", True)
    )
    MIN_TARGET_MINUTES: int = 5
    MAX_TARGET_MINUTES: int = 30
    MAX_UPLOAD_MB: int = field(default_factory=lambda: _int("MAX_UPLOAD_MB", 25))

    # LLM
    ANTHROPIC_MODEL: str = field(default_factory=lambda: _str("ANTHROPIC_MODEL", "claude-opus-5-5"))
    PRODUCER_EFFORT: str = field(default_factory=lambda: _str("PRODUCER_EFFORT", "high"))
    HOST_EFFORT: str = field(default_factory=lambda: _str("HOST_EFFORT", "medium"))
    # Server-side refusal fallback ("default" lets the API pick the fallback
    # model by refusal category). Set to "off" to disable.
    ANTHROPIC_FALLBACKS: str = field(default_factory=lambda: _str("ANTHROPIC_FALLBACKS", "default"))
    LLM_VALIDATION_RETRIES: int = 2

    # Content decisions
    COURSE_NAME: str = field(default_factory=lambda: _str("COURSE_NAME", "Executive MBA program"))
    HOST_A_NAME: str = field(default_factory=lambda: os.getenv("HOST_A_NAME", ""))
    HOST_B_NAME: str = field(default_factory=lambda: os.getenv("HOST_B_NAME", ""))
    INCLUDE_CLOSING_DISCLAIMER: bool = field(
        default_factory=lambda: _bool("INCLUDE_CLOSING_DISCLAIMER", True)
    )

    # Audio
    TTS_PROVIDER: str = field(default_factory=lambda: _str("TTS_PROVIDER", "stub"))
    VOICE_MAP: dict[str, str] = field(default_factory=_voice_map)
    VOICE_STYLE: dict[str, str] = field(default_factory=_voice_style)
    OPENAI_TTS_MODEL: str = field(default_factory=lambda: _str("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"))
    TURN_PAUSE_MS: int = 350
    TTS_CHUNK_CHARS: int = 400

    # Server
    OUTPUT_DIR: Path = field(
        default_factory=lambda: Path(_str("OUTPUT_DIR", str(BACKEND_DIR / "output"))).resolve()
    )
    CORS_ORIGINS: list[str] = field(
        default_factory=lambda: [
            o.strip() for o in _str("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()
        ]
    )


settings = Settings()
settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
