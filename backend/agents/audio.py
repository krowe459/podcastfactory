"""Agent 4: Audio post-production. Deterministic code, not an LLM.

Maps host -> voice, normalizes text for speech, chunks at sentence
boundaries, synthesizes through a swappable TTSProvider, joins turns with
short pauses, and exports an mp3.
"""

from __future__ import annotations

import io
import logging
import os
import re
import time
from abc import ABC, abstractmethod
from pathlib import Path

import httpx
from pydub import AudioSegment

from config import settings
from contracts import DialogueTurn
from textutil import count_words

log = logging.getLogger("podcastfactory.audio")


# ------------------------------------------------------------------ providers


class TTSError(RuntimeError):
    """A readable failure from the speech provider."""


class TTSProvider(ABC):
    name: str = "base"
    # Longest text sent in one synthesize() call; None means settings.TTS_CHUNK_CHARS.
    max_chunk_chars: int | None = None

    @abstractmethod
    def synthesize(self, text: str, voice_id: str, delivery_instruction: str) -> AudioSegment:
        """Return speech for text in the given voice, honoring delivery_instruction if supported."""


class StubTTSProvider(TTSProvider):
    """Prototype provider: silence lasting as long as the words would take to say."""

    name = "stub"

    def synthesize(self, text: str, voice_id: str, delivery_instruction: str) -> AudioSegment:
        seconds = count_words(text) / settings.WORDS_PER_MINUTE * 60
        log.debug("stub TTS voice=%s delivery=%r words=%d", voice_id, delivery_instruction, count_words(text))
        return AudioSegment.silent(duration=max(int(seconds * 1000), 1))


class OpenAITTSProvider(TTSProvider):
    """OpenAI speech endpoint. Honors delivery_instruction via the `instructions` field."""

    name = "openai"
    max_chunk_chars = 4000  # the endpoint accepts up to 4096 characters
    URL = "https://api.openai.com/v1/audio/speech"

    def __init__(self) -> None:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise TTSError("TTS_PROVIDER=openai needs OPENAI_API_KEY.")
        self._client = httpx.Client(
            timeout=httpx.Timeout(120.0, connect=15.0),
            headers={"Authorization": f"Bearer {key}"},
        )

    def synthesize(self, text: str, voice_id: str, delivery_instruction: str) -> AudioSegment:
        body = {
            "model": settings.OPENAI_TTS_MODEL,
            "voice": voice_id,
            "input": text,
            "response_format": "mp3",
        }
        if delivery_instruction:
            body["instructions"] = delivery_instruction
        for attempt in range(5):
            try:
                resp = self._client.post(self.URL, json=body)
            except httpx.TransportError as exc:
                if attempt == 4:
                    raise TTSError(f"Could not reach the speech provider: {exc}") from exc
                time.sleep(2**attempt)
                continue
            if resp.status_code == 200:
                return AudioSegment.from_file(io.BytesIO(resp.content), format="mp3")
            detail = _openai_error(resp)
            if resp.status_code == 429 and "insufficient_quota" in detail:
                raise TTSError("The OpenAI account has no credits left (insufficient_quota).")
            if resp.status_code in (401, 403):
                raise TTSError("OpenAI rejected the API key. Check OPENAI_API_KEY.")
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < 4:
                time.sleep(2**attempt)
                continue
            raise TTSError(f"OpenAI speech request failed ({resp.status_code}): {detail}")
        raise TTSError("OpenAI speech request failed after retries.")


def _openai_error(resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error", {})
        return f"{err.get('type') or err.get('code') or ''}: {err.get('message', '')}".strip(": ")
    except ValueError:
        return resp.text[:300]


PROVIDERS: dict[str, type[TTSProvider]] = {
    "stub": StubTTSProvider,
    "openai": OpenAITTSProvider,
}


def get_provider(name: str | None = None) -> TTSProvider:
    key = (name or settings.TTS_PROVIDER).lower()
    if key not in PROVIDERS:
        raise ValueError(f"Unknown TTS_PROVIDER '{key}'. Available: {', '.join(PROVIDERS)}")
    return PROVIDERS[key]()


# -------------------------------------------------------------- normalization

_ONES = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
    "nineteen",
]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_SCALES = [(10**12, "trillion"), (10**9, "billion"), (10**6, "million"), (1000, "thousand")]


def int_to_words(n: int) -> str:
    if n < 0:
        return "minus " + int_to_words(-n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return _TENS[tens] + (f"-{_ONES[ones]}" if ones else "")
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        return f"{_ONES[hundreds]} hundred" + (f" {int_to_words(rest)}" if rest else "")
    for value, word in _SCALES:
        if n >= value:
            head, rest = divmod(n, value)
            return f"{int_to_words(head)} {word}" + (f" {int_to_words(rest)}" if rest else "")
    return str(n)


def number_to_words(raw: str) -> str:
    raw = raw.replace(",", "")
    if "." in raw:
        whole, frac = raw.split(".", 1)
        whole_words = int_to_words(int(whole or "0"))
        return f"{whole_words} point " + " ".join(_ONES[int(d)] for d in frac if d.isdigit())
    return int_to_words(int(raw))


_MAGNITUDE = {
    "k": "thousand", "thousand": "thousand",
    "m": "million", "mm": "million", "mn": "million", "million": "million",
    "b": "billion", "bn": "billion", "billion": "billion",
    "t": "trillion", "tn": "trillion", "trillion": "trillion",
}
_CURRENCY = {"$": "dollars", "€": "euros", "£": "pounds", "¥": "yen"}

_CURRENCY_RE = re.compile(
    r"(?P<cur>[$€£¥])\s?(?P<num>\d[\d,]*(?:\.\d+)?)"
    r"(?:\s?(?P<mag>thousand|million|billion|trillion|bn|mn|mm|tn|[kKmMbBtT])\b)?"
)
_PERCENT_RE = re.compile(r"(?P<num>-?\d[\d,]*(?:\.\d+)?)\s?(?:%|percent\b)")

# Business abbreviations spelled out for the ear. Acronyms that are normally
# spoken as letters or as a word (EBITDA, ROI, CEO) are left unchanged.
_ABBREVIATIONS = [
    (r"\bYoY\b", "year over year"),
    (r"\bQoQ\b", "quarter over quarter"),
    (r"\bMoM\b", "month over month"),
    (r"\bYTD\b", "year to date"),
    (r"\bw/o\b", "without"),
    (r"\bw/\s", "with "),
    (r"\be\.g\.", "for example"),
    (r"\bi\.e\.", "that is"),
    (r"\betc\.", "and so on"),
    (r"\bvs\.?(?=\s)", "versus"),
    (r"\bapprox\.", "approximately"),
    (r"\bavg\.", "average"),
    (r"\best\.", "estimated"),
    (r"\bM&A\b", "M and A"),
    (r"\bR&D\b", "R and D"),
    (r"\bP&L\b", "P and L"),
    (r"\bB2B\b", "B to B"),
    (r"\bB2C\b", "B to C"),
    (r"\bFY\s?(\d{2,4})\b", r"fiscal year \1"),
    (r"\s&\s", " and "),
]


def _currency_sub(m: re.Match[str]) -> str:
    words = number_to_words(m.group("num"))
    mag = m.group("mag")
    unit = _CURRENCY[m.group("cur")]
    if mag:
        return f"{words} {_MAGNITUDE[mag.lower()]} {unit}"
    if m.group("num").replace(",", "") in {"1", "1.00"}:
        unit = unit.rstrip("s") if unit != "yen" else unit
    return f"{words} {unit}"


def strip_markdown(text: str) -> str:
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"`{1,3}([^`]*)`{1,3}", r"\1", text)
    text = re.sub(r"(\*\*|__)(.*?)\1", r"\2", text)
    text = re.sub(r"(?<!\w)[*_](\S(?:.*?\S)?)[*_](?!\w)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"^\s*(?:[-*+]|\d+\.)\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*>\s?", "", text, flags=re.M)
    return re.sub(r"\s+", " ", text).strip()


def normalize_for_speech(text: str) -> str:
    """Change how words are written for speech, never what they mean. Proper names are untouched."""
    text = strip_markdown(text)
    text = _CURRENCY_RE.sub(_currency_sub, text)
    text = _PERCENT_RE.sub(lambda m: f"{number_to_words(m.group('num'))} percent", text)
    for pattern, repl in _ABBREVIATIONS:
        text = re.sub(pattern, repl, text)
    return re.sub(r"\s+", " ", text).strip()


def chunk_for_tts(text: str, limit: int | None = None) -> list[str]:
    """Split into pieces of at most `limit` characters at sentence boundaries."""
    limit = limit or settings.TTS_CHUNK_CHARS
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text) if s]
    pieces: list[str] = []
    for sentence in sentences:
        while len(sentence) > limit:  # a single overlong sentence: split at a comma or space
            cut = max(sentence.rfind(", ", 0, limit), sentence.rfind(" ", 0, limit))
            cut = cut if cut > 0 else limit
            pieces.append(sentence[: cut + 1].strip())
            sentence = sentence[cut + 1 :].strip()
        pieces.append(sentence)
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current} {piece}".strip()
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = piece
    if current:
        chunks.append(current)
    return chunks


# ------------------------------------------------------------------- pipeline


def combine_delivery(style: str, note: str) -> str:
    """Host's standing style first, then this line's note."""
    style, note = style.strip(), note.strip()
    if style and note:
        return f"{style.rstrip('.')}. For this line: {note}"
    return style or note


def produce_audio(turns: list[DialogueTurn], job_id: str, provider: TTSProvider | None = None) -> Path:
    provider = provider or get_provider()
    pause = AudioSegment.silent(duration=settings.TURN_PAUSE_MS)
    episode = AudioSegment.silent(duration=0)
    for i, turn in enumerate(turns):
        voice = settings.VOICE_MAP[turn.host]
        delivery = combine_delivery(settings.VOICE_STYLE.get(turn.host, ""), turn.delivery_instruction)
        spoken = normalize_for_speech(turn.text)
        segment = AudioSegment.silent(duration=0)
        for chunk in chunk_for_tts(spoken, provider.max_chunk_chars):
            segment += provider.synthesize(chunk, voice, delivery)
        if i:
            episode += pause
        episode += segment
    out_path = settings.OUTPUT_DIR / f"{job_id}.mp3"
    episode.export(out_path, format="mp3", bitrate="64k", tags={"title": job_id, "artist": "PodcastFactory"})
    return out_path
