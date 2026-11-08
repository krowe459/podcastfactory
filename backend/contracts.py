"""Single source of truth for every data shape that crosses a boundary.

The frontend mirrors this file in frontend/lib/contracts.ts, and
tests/test_contracts.py fails if the two drift apart.
"""

from __future__ import annotations

from typing import Any, Literal, Optional, get_args

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------
# Literal value sets
# --------------------------------------------------------------------------

Status = Literal[
    "extracting_facts",
    "generating_script",
    "revising_outline",
    "synthesizing_audio",
    "complete",
    "error",
]
STATUS_VALUES: tuple[str, ...] = get_args(Status)

Role = Literal["intro", "body", "outro"]
ROLE_VALUES: tuple[str, ...] = get_args(Role)

Host = Literal["A", "B"]
HOST_VALUES: tuple[str, ...] = get_args(Host)

Priority = Literal["must_cover", "should_cover", "nice_to_have"]
PRIORITY_VALUES: tuple[str, ...] = get_args(Priority)

Kind = Literal[
    "thesis",
    "claim",
    "evidence",
    "framework",
    "example",
    "counterargument",
    "implication",
]
KIND_VALUES: tuple[str, ...] = get_args(Kind)

# Progress floor emitted with each status. Within "generating_script" the
# backend advances progress between this floor and the next one as sections
# complete; progress never moves backwards within a job.
STATUS_PROGRESS: dict[str, int] = {
    "extracting_facts": 5,
    "generating_script": 20,
    "revising_outline": 75,
    "synthesizing_audio": 90,
    "complete": 100,
    "error": 100,
}

# Statuses during which work is in flight (the UI button is disabled).
IN_PROGRESS_STATUSES: tuple[str, ...] = (
    "extracting_facts",
    "generating_script",
    "revising_outline",
    "synthesizing_audio",
)

# --------------------------------------------------------------------------
# Plan models (Agent 1)
# --------------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KeyPoint(_Strict):
    id: str = Field(description='Stable id of the form "s{section order}-k{n}", e.g. "s2-k3".')
    text: str = Field(description="Self-contained, 1-3 sentences, exact figures and names.")
    kind: Kind
    priority: Priority
    source_ref: str = Field(description='Author, section, page, or slide; otherwise "unspecified".')


class Section(_Strict):
    id: str = Field(description='Section id of the form "s{order}", e.g. "s2".')
    order: int
    role: Role
    title: str
    pedagogical_goal: str
    key_points: list[KeyPoint]
    word_budget: int


class ProductionPlan(_Strict):
    title: str
    source_summary: str
    target_minutes: int
    total_word_budget: int
    capacity_warning: Optional[str] = None
    sections: list[Section]


# --------------------------------------------------------------------------
# Script models (Agents 2 and 3)
# --------------------------------------------------------------------------


class DialogueTurn(_Strict):
    section_id: str
    host: Host
    text: str
    delivery_instruction: str
    covers: list[str] = Field(default_factory=list)


class HostTurnOutput(_Strict):
    """What a host LLM call returns. The orchestrator adds section_id."""

    host: Host
    text: str
    delivery_instruction: str
    covers: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Reporting and streaming
# --------------------------------------------------------------------------


class CoverageReport(_Strict):
    must_cover_total: int
    must_cover_hit: int
    missing_ids: list[str]
    # id -> key point text, so the UI can list missing ideas in plain words.
    missing_points: dict[str, str] = Field(default_factory=dict)
    per_section_words: dict[str, int]
    total_words: int
    estimated_minutes: float
    warnings: list[str] = Field(default_factory=list)


class CompleteData(_Strict):
    audio_url: str
    transcript_url: str
    coverage: CoverageReport
    tts_provider: str


class ErrorData(_Strict):
    detail: str


class StreamEvent(_Strict):
    status: Status
    message: str
    progress: int = Field(ge=0, le=100)
    data: Optional[dict[str, Any]] = None


STREAM_EVENT_FIELDS: tuple[str, ...] = tuple(StreamEvent.model_fields.keys())
