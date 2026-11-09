"""Agents 2 and 3: Host A (the Guide) and Host B (the Analyst). One LLM call = one turn."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import llm
import prompts
from config import settings
from contracts import DialogueTurn, HostTurnOutput, KeyPoint, ProductionPlan, Section
from textutil import count_words

log = logging.getLogger("podcastfactory.hosts")

# Short turns keep the dialogue quick and let each section's length be adjusted in small steps.
# (Sections alternate A/B, so their length moves two turns at a time.)
MIN_TURN_WORDS = 15
MAX_TURN_WORDS = 65
TYPICAL_TURN_WORDS = "25-45"
MAX_LENGTH_ATTEMPTS = 3


@dataclass
class TurnRequest:
    speaker: str  # "A" | "B"
    section: Section
    next_section: Optional[Section]
    words_used: int
    covered_ids: set[str]
    recent_turns: list[DialogueTurn]
    instruction: str
    allowed_cover_ids: set[str] = field(default_factory=set)
    extra_key_points: list[KeyPoint] = field(default_factory=list)


def system_prompt(speaker: str, charter: str) -> str:
    host_block = prompts.HOST_A_GUIDE if speaker == "A" else prompts.HOST_B_ANALYST
    rules = prompts.fill(
        prompts.SCRIPT_RULES,
        min_turn_words=MIN_TURN_WORDS,
        max_turn_words=MAX_TURN_WORDS,
        typical_turn_words=TYPICAL_TURN_WORDS,
    )
    parts = [charter, rules, host_block]
    if settings.HOST_A_NAME and settings.HOST_B_NAME:
        parts.append(
            prompts.fill(
                prompts.HOST_NAMES_NOTE,
                host_a_name=settings.HOST_A_NAME,
                host_b_name=settings.HOST_B_NAME,
            )
        )
    return "\n\n".join(parts)


def _speaker_label(host: str) -> str:
    name = settings.HOST_A_NAME if host == "A" else settings.HOST_B_NAME
    return f"Host {host} ({name})" if name else f"Host {host}"


def format_turns(turns: list[DialogueTurn]) -> str:
    if not turns:
        return "(This is the start of the episode.)"
    return "\n".join(f"{_speaker_label(t.host)}: {t.text}" for t in turns)


def format_key_points(points: list[KeyPoint], covered: set[str]) -> str:
    if not points:
        return "(none)"
    return "\n".join(
        prompts.fill(
            prompts.KEY_POINT_LINE,
            id=k.id,
            kind=k.kind,
            priority=k.priority,
            state="covered" if k.id in covered else "uncovered",
            text=k.text,
            source_ref=k.source_ref,
        )
        for k in points
    )


def role_note(section: Section) -> str:
    if section.role == "intro":
        return "\n\n" + prompts.INTRO_NOTE
    if section.role == "outro":
        note = prompts.OUTRO_NOTE
        if settings.INCLUDE_CLOSING_DISCLAIMER:
            note += " " + prompts.OUTRO_DISCLAIMER_NOTE
        return "\n\n" + note
    return ""


def build_user_prompt(req: TurnRequest, source_included: bool) -> str:
    points = list(req.section.key_points)
    known = {k.id for k in points}
    points += [k for k in req.extra_key_points if k.id not in known]
    remaining = max(req.section.word_budget - req.words_used, 0)
    return prompts.fill(
        prompts.TURN_TEMPLATE,
        speaker=_speaker_label(req.speaker).removeprefix("Host "),
        section__title=req.section.title,
        section__role=req.section.role,
        section__pedagogical_goal=req.section.pedagogical_goal,
        section__word_budget=req.section.word_budget,
        words_used=req.words_used,
        words_remaining=remaining,
        key_points=format_key_points(points, req.covered_ids),
        turns=format_turns(req.recent_turns),
        source_text=prompts.SOURCE_IN_PREFIX if source_included else prompts.SOURCE_NOT_INCLUDED,
        instruction=req.instruction,
        role_note=role_note(req.section),
    )


def generate_turn(
    req: TurnRequest, charter: str, source_prefix: Optional[str]
) -> tuple[DialogueTurn, Optional[str]]:
    """Generate exactly one turn for req.speaker. Returns (turn, warning-or-None)."""
    system = system_prompt(req.speaker, charter)
    base_user = build_user_prompt(req, source_prefix is not None)
    user = base_user
    output: Optional[HostTurnOutput] = None
    n_words = 0
    for attempt in range(MAX_LENGTH_ATTEMPTS):
        output = llm.call_structured(
            system,
            user,
            HostTurnOutput,
            cached_prefix=source_prefix,
            effort=settings.HOST_EFFORT,
            max_tokens=16000,
        )
        n_words = count_words(output.text)
        if MIN_TURN_WORDS <= n_words <= MAX_TURN_WORDS:
            break
        log.info("Turn rejected: %d words (attempt %d)", n_words, attempt + 1)
        user = (
            f"{base_user}\n\nREJECTED: your previous turn was {n_words} words. A turn must be "
            f"{MIN_TURN_WORDS}-{MAX_TURN_WORDS} words. Rewrite it within that range.\n"
            f"Previous turn: {output.text}"
        )
    assert output is not None
    warning = None
    if not (MIN_TURN_WORDS <= n_words <= MAX_TURN_WORDS):
        warning = (
            f"A turn in '{req.section.title}' stayed at {n_words} words after "
            f"{MAX_LENGTH_ATTEMPTS} attempts (target {MIN_TURN_WORDS}-{MAX_TURN_WORDS})."
        )

    allowed = req.allowed_cover_ids or {k.id for k in req.section.key_points}
    covers = [c for c in dict.fromkeys(output.covers) if c in allowed]
    turn = DialogueTurn(
        section_id=req.section.id,
        host=req.speaker,  # the orchestrator decides the speaker, not the model
        text=output.text.strip(),
        delivery_instruction=output.delivery_instruction.strip() or "lighter, conversational",
        covers=covers,
    )
    return turn, warning


__all__ = ["TurnRequest", "generate_turn", "format_turns", "ProductionPlan"]
