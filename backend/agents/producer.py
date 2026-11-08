"""Agent 1: Executive Producer and Fact-Extractor."""

from __future__ import annotations

import json
import logging
from typing import Callable, Optional

import llm
import prompts
from config import settings
from contracts import ProductionPlan
from parsing import split_into_chunks

log = logging.getLogger("podcastfactory.producer")

BUDGET_SUM_TOLERANCE = 0.03
MAX_PLAN_CORRECTIONS = 2


def _schema() -> str:
    return json.dumps(ProductionPlan.model_json_schema(), separators=(",", ":"))


def _system(charter: str) -> str:
    return f"{charter}\n\n{prompts.AGENT1_PRODUCER}"


def _fill_system(charter: str, target_minutes: int) -> str:
    return prompts.fill(
        _system(charter),
        target_minutes=target_minutes,
        total_word_budget=target_minutes * settings.WORDS_PER_MINUTE,
        words_per_minute=settings.WORDS_PER_MINUTE,
    )


def _call(system: str, user: str) -> ProductionPlan:
    return llm.call_structured(
        system, user, ProductionPlan, effort=settings.PRODUCER_EFFORT, max_tokens=48000
    )


# ----------------------------------------------------------------- validation


def plan_problems(plan: ProductionPlan, target_minutes: int) -> list[str]:
    problems: list[str] = []
    expected_total = target_minutes * settings.WORDS_PER_MINUTE
    if plan.target_minutes != target_minutes:
        problems.append(f"target_minutes is {plan.target_minutes}; it must be {target_minutes}.")
    if plan.total_word_budget != expected_total:
        problems.append(
            f"total_word_budget is {plan.total_word_budget}; it must be exactly {expected_total} "
            f"({target_minutes} x {settings.WORDS_PER_MINUTE})."
        )
    if not plan.sections:
        problems.append("The plan has no sections.")
        return problems
    budget_sum = sum(s.word_budget for s in plan.sections)
    if abs(budget_sum - expected_total) > BUDGET_SUM_TOLERANCE * expected_total:
        problems.append(
            f"Section word_budget values sum to {budget_sum}; they must sum to within 3% of "
            f"{expected_total}."
        )
    if any(s.word_budget <= 0 for s in plan.sections):
        problems.append("Every section needs a positive word_budget.")
    ordered = sorted(plan.sections, key=lambda s: s.order)
    roles = [s.role for s in ordered]
    if roles[0] != "intro" or roles.count("intro") != 1:
        problems.append("There must be exactly one intro section, and it must come first.")
    if roles[-1] != "outro" or roles.count("outro") != 1:
        problems.append("There must be exactly one outro section, and it must come last.")
    section_ids = [s.id for s in plan.sections]
    if len(set(section_ids)) != len(section_ids):
        problems.append("Section ids must be unique.")
    if len({s.order for s in plan.sections}) != len(plan.sections):
        problems.append("Section order values must be unique.")
    kp_ids = [k.id for s in plan.sections for k in s.key_points]
    dupes = sorted({i for i in kp_ids if kp_ids.count(i) > 1})
    if dupes:
        problems.append(f"Key point ids must be unique; duplicated: {', '.join(dupes)}.")
    if not any(k.priority == "must_cover" for s in plan.sections for k in s.key_points):
        problems.append("No key point is marked must_cover; the thesis at least must be.")
    return problems


def _deterministic_fix(plan: ProductionPlan, target_minutes: int) -> ProductionPlan:
    """Last resort after LLM corrections: fix arithmetic and ids in code."""
    plan = plan.model_copy(deep=True)
    total = target_minutes * settings.WORDS_PER_MINUTE
    plan.target_minutes = target_minutes
    plan.total_word_budget = total
    plan.sections.sort(key=lambda s: s.order)
    for i, s in enumerate(plan.sections, start=1):
        s.order = i
    current = sum(max(s.word_budget, 1) for s in plan.sections)
    for s in plan.sections:
        s.word_budget = max(30, round(max(s.word_budget, 1) * total / current))
    drift = total - sum(s.word_budget for s in plan.sections)
    max(plan.sections, key=lambda s: s.word_budget).word_budget += drift

    section_ids = [s.id for s in plan.sections]
    if len(set(section_ids)) != len(section_ids):
        for s in plan.sections:
            s.id = f"s{s.order}"
    kp_ids = [k.id for s in plan.sections for k in s.key_points]
    if len(set(kp_ids)) != len(kp_ids):
        for s in plan.sections:
            for n, k in enumerate(s.key_points, start=1):
                k.id = f"s{s.order}-k{n}"
    return plan


def _ensure_valid(
    plan: ProductionPlan, target_minutes: int, system: str
) -> ProductionPlan:
    for _ in range(MAX_PLAN_CORRECTIONS):
        problems = plan_problems(plan, target_minutes)
        if not problems:
            break
        log.info("Plan failed checks, asking Agent 1 to correct: %s", problems)
        user = prompts.fill(
            prompts.CORRECTION_REQUEST,
            problems="\n".join(f"- {p}" for p in problems),
            target_minutes=target_minutes,
            total_word_budget=target_minutes * settings.WORDS_PER_MINUTE,
            plan=plan.model_dump_json(indent=1),
            schema=_schema(),
        )
        plan = _call(system, user)
    if plan_problems(plan, target_minutes):
        plan = _deterministic_fix(plan, target_minutes)
    remaining = [
        p for p in plan_problems(plan, target_minutes) if "intro" in p or "outro" in p or "no sections" in p
    ]
    if remaining:
        raise llm.LLMError("The production plan is malformed: " + " ".join(remaining))
    plan.sections.sort(key=lambda s: s.order)
    return plan


# ------------------------------------------------------------------ public API


def produce_plan(
    source: str,
    target_minutes: int,
    charter: str,
    on_progress: Optional[Callable[[str], None]] = None,
) -> ProductionPlan:
    system = _fill_system(charter, target_minutes)
    total = target_minutes * settings.WORDS_PER_MINUTE

    if len(source) <= settings.MAX_SOURCE_CHARS:
        user = prompts.fill(
            prompts.PLAN_REQUEST,
            chunk_note="",
            target_minutes=target_minutes,
            total_word_budget=total,
            schema=_schema(),
            source=source,
        )
        return _ensure_valid(_call(system, user), target_minutes, system)

    chunks = split_into_chunks(source, settings.CHUNK_CHARS)
    partials: list[ProductionPlan] = []
    for i, chunk in enumerate(chunks, start=1):
        if on_progress:
            on_progress(f"Analyzing part {i} of {len(chunks)} of the reading...")
        user = prompts.fill(
            prompts.PLAN_REQUEST,
            chunk_note=(
                f"NOTE: This is chunk {i} of {len(chunks)} of a long source. Extract key points "
                "from this chunk only; budgets are provisional and will be re-allocated in a merge "
                "step.\n\n"
            ),
            target_minutes=target_minutes,
            total_word_budget=total,
            schema=_schema(),
            source=chunk,
        )
        partials.append(_call(system, user))

    if on_progress:
        on_progress("Merging the analysis of all parts...")
    user = prompts.fill(
        prompts.MERGE_REQUEST,
        n_chunks=len(chunks),
        target_minutes=target_minutes,
        total_word_budget=total,
        schema=_schema(),
        partials="\n\n".join(
            f"--- Partial plan {i} ---\n{p.model_dump_json()}" for i, p in enumerate(partials, 1)
        ),
    )
    return _ensure_valid(_call(system, user), target_minutes, system)


def revise_plan(
    plan: ProductionPlan,
    measurements: list[dict],
    measured_total: int,
    source: str,
    charter: str,
) -> ProductionPlan:
    system = _fill_system(charter, plan.target_minutes)
    direction = "over" if measured_total > plan.total_word_budget else "under"
    if len(source) <= settings.MAX_SOURCE_CHARS:
        source_block = source
    else:
        source_block = (
            "(The source is too long to include again. Draw any added points only from the "
            "existing plan; do not add new points.)"
        )
    user = prompts.fill(
        prompts.REVISION_REQUEST,
        direction=direction,
        measured_total=measured_total,
        total_word_budget=plan.total_word_budget,
        tolerance_pct=round(settings.DURATION_TOLERANCE * 100),
        measurements=json.dumps(measurements, indent=1),
        plan=plan.model_dump_json(indent=1),
        schema=_schema(),
        source=source_block,
    )
    revised = _call(system, user)

    # Never let a revision silently drop must-cover material.
    old_must = {k.id: (s.id, k) for s in plan.sections for k in s.key_points if k.priority == "must_cover"}
    new_ids = {k.id for s in revised.sections for k in s.key_points}
    by_section = {s.id: s for s in revised.sections}
    for kid, (sid, kp) in old_must.items():
        if kid not in new_ids and sid in by_section:
            by_section[sid].key_points.append(kp)
            log.warning("Revision dropped must_cover %s; restored it", kid)

    # Total budget should normally stay fixed; allow a change only if the model kept the
    # arithmetic coherent, otherwise validate against the original total.
    return _ensure_valid(revised, plan.target_minutes, system)
