"""The single gateway for every LLM call in the app."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from config import settings

log = logging.getLogger("podcastfactory.llm")

T = TypeVar("T", bound=BaseModel)

_client: Optional[anthropic.Anthropic] = None


class LLMError(RuntimeError):
    """A readable failure from the LLM layer (shown to the user)."""


def _api_key() -> Optional[str]:
    """ANTHROPIC_API_KEY wins; PODCASTFACTORY_ANTHROPIC_KEY is the fallback."""
    return os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("PODCASTFACTORY_ANTHROPIC_KEY") or None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=_api_key(), max_retries=3)
    return _client


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.S)


def extract_json(text: str) -> object:
    """Parse a JSON object from model text, tolerating stray fences or prose."""
    text = text.strip()
    m = _FENCE.match(text)
    if m:
        text = m.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


def _request(
    system_prompt: str,
    content: list[dict],
    effort: str,
    max_tokens: int,
) -> str:
    client = _get_client()
    kwargs: dict = {
        "model": settings.ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "system": [{"type": "text", "text": system_prompt}],
        "messages": [{"role": "user", "content": content}],
        "output_config": {"effort": effort},
    }
    if settings.ANTHROPIC_FALLBACKS.lower() not in {"", "off", "none", "false"}:
        kwargs["betas"] = ["server-side-fallback-2026-07-01"]
        kwargs["fallbacks"] = settings.ANTHROPIC_FALLBACKS
    try:
        with client.beta.messages.stream(**kwargs) as stream:
            message = stream.get_final_message()
    except anthropic.AuthenticationError as exc:
        raise LLMError("The LLM provider rejected the API key. Check ANTHROPIC_API_KEY (or PODCASTFACTORY_ANTHROPIC_KEY).") from exc
    except anthropic.PermissionDeniedError as exc:
        raise LLMError(f"The LLM provider denied the request: {exc.message}") from exc
    except anthropic.NotFoundError as exc:
        raise LLMError(
            f"Model '{settings.ANTHROPIC_MODEL}' was not found. Check ANTHROPIC_MODEL."
        ) from exc
    except anthropic.RateLimitError as exc:
        raise LLMError("The LLM provider is rate limiting requests. Try again shortly.") from exc
    except anthropic.BadRequestError as exc:
        raise LLMError(f"The LLM request was rejected: {exc.message}") from exc
    except anthropic.APIStatusError as exc:
        raise LLMError(f"The LLM provider returned an error ({exc.status_code}).") from exc
    except anthropic.APIConnectionError as exc:
        raise LLMError("Could not reach the LLM provider. Check the network connection.") from exc

    if message.stop_reason == "refusal":
        raise LLMError("The model declined to process this content.")
    text = "".join(b.text for b in message.content if getattr(b, "type", None) == "text")
    if message.stop_reason == "max_tokens":
        log.warning("Response hit max_tokens (%s); output may be truncated", max_tokens)
    return text


def call_structured(
    system_prompt: str,
    user_prompt: str,
    response_model: type[T],
    *,
    cached_prefix: Optional[str] = None,
    effort: Optional[str] = None,
    max_tokens: int = 16000,
) -> T:
    """Request JSON only, validate with Pydantic, and retry with the error fed back.

    cached_prefix: optional large, stable text (e.g. the source reading) sent
    as the first user content block with a cache breakpoint, so repeated
    calls over the same source are served from the prompt cache.
    """
    effort = effort or settings.PRODUCER_EFFORT
    base: list[dict] = []
    if cached_prefix:
        base.append(
            {"type": "text", "text": cached_prefix, "cache_control": {"type": "ephemeral"}}
        )

    prompt = user_prompt
    last_error = ""
    for attempt in range(settings.LLM_VALIDATION_RETRIES + 1):
        text = _request(system_prompt, base + [{"type": "text", "text": prompt}], effort, max_tokens)
        try:
            return response_model.model_validate(extract_json(text))
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = str(exc)
            log.info("Validation failed (attempt %d): %s", attempt + 1, last_error[:500])
            prompt = (
                f"{user_prompt}\n\n"
                "YOUR PREVIOUS RESPONSE WAS INVALID\n"
                f"Previous response:\n{text[:20000]}\n\n"
                f"Validation error:\n{last_error[:4000]}\n\n"
                "Return ONLY a corrected JSON object that fixes every error above. "
                "No commentary, no markdown fences."
            )
    raise LLMError(
        f"The model did not return a valid {response_model.__name__} after "
        f"{settings.LLM_VALIDATION_RETRIES + 1} attempts: {last_error[:300]}"
    )
