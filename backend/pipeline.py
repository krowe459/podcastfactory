"""The orchestrator: plan -> turn-taking loop -> duration revisions -> coverage repair -> audio."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

import prompts
from agents import audio, hosts, producer
from config import settings
from contracts import (
    STATUS_PROGRESS,
    CompleteData,
    CoverageReport,
    DialogueTurn,
    ErrorData,
    KeyPoint,
    ProductionPlan,
    Section,
    StreamEvent,
)
from llm import LLMError
from parsing import InputError
from textutil import count_words
from transcript import write_transcript

log = logging.getLogger("podcastfactory.pipeline")

RECENT_TURNS = 8
BRIDGE_RESERVE_WORDS = 35  # leave room for the closing/bridge turn inside the budget
REPAIR_TURNS_PER_POINT = 2
SECTION_DEVIATION_FOR_REGEN = 0.10


class Cancelled(Exception):
    """The client disconnected; stop work quietly."""


@dataclass
class JobInput:
    job_id: str
    source_text: str
    target_minutes: int
    topic_focus: str = ""
    course_name: str = ""


class Emitter:
    """Builds StreamEvents and keeps progress monotonic."""

    def __init__(self, sink: Callable[[StreamEvent], None]):
        self._sink = sink
        self.progress = 0
        self.terminal_sent = False

    def __call__(
        self, status: str, message: str, progress: Optional[float] = None, data: Optional[dict] = None
    ) -> None:
        value = STATUS_PROGRESS[status] if progress is None else int(progress)
        if status != "error":
            value = max(value, self.progress)
        value = max(0, min(100, value))
        if status != "error":
            self.progress = value
        event = StreamEvent(status=status, message=message, progress=value, data=data)
        if status in ("complete", "error"):
            self.terminal_sent = True
        self._sink(event)


@dataclass
class _SectionState:
    turns: list[DialogueTurn] = field(default_factory=list)
    words: int = 0
    covered: set[str] = field(default_factory=set)


class Orchestrator:
    def __init__(self, job: JobInput, emit: Emitter):
        self.job = job
        self.emit = emit
        self.warnings: list[str] = []
        self.charter = prompts.fill(
            prompts.CHARTER,
            course_name=job.course_name or settings.COURSE_NAME,
            topic_focus=job.topic_focus.strip(),
        )
        fits = len(job.source_text) <= settings.MAX_SOURCE_CHARS
        self.source_prefix: Optional[str] = (
            f"SOURCE REFERENCE\n<<<\n{job.source_text}\n>>>"
            if settings.INCLUDE_SOURCE_IN_HOST_CONTEXT and fits
            else None
        )
        self.plan: ProductionPlan
        self.section_turns: dict[str, list[DialogueTurn]] = {}

    # ------------------------------------------------------------ helpers

    def script(self) -> list[DialogueTurn]:
        return [t for s in self.plan.sections for t in self.section_turns.get(s.id, [])]

    def _section_words(self, section_id: str) -> int:
        return sum(count_words(t.text) for t in self.section_turns.get(section_id, []))

    @staticmethod
    def _must_ids(section: Section) -> set[str]:
        return {k.id for k in section.key_points if k.priority == "must_cover"}

    @staticmethod
    def _next_speaker(turns: list[DialogueTurn]) -> str:
        """Hosts strictly alternate across the whole episode, so no host ever speaks twice in a
        row (not even across a section break). Host A speaks first; because Host B closes every
        non-final section, Host A also opens every section."""
        if not turns:
            return "A"
        return "B" if turns[-1].host == "A" else "A"

    @staticmethod
    def _closing_instruction(next_section: Optional[Section]) -> str:
        if next_section is not None:
            return prompts.fill(prompts.INSTRUCTION_BRIDGE, next_section__title=next_section.title)
        return prompts.INSTRUCTION_CLOSE_EPISODE

    def _turn(
        self,
        speaker: str,
        section: Section,
        next_section: Optional[Section],
        state: _SectionState,
        context: list[DialogueTurn],
        instruction: str,
        extra_points: Optional[list[KeyPoint]] = None,
    ) -> DialogueTurn:
        req = hosts.TurnRequest(
            speaker=speaker,
            section=section,
            next_section=next_section,
            words_used=state.words,
            covered_ids=state.covered,
            recent_turns=context[-RECENT_TURNS:],
            instruction=instruction,
            extra_key_points=extra_points or [],
        )
        turn, warning = hosts.generate_turn(req, self.charter, self.source_prefix)
        if warning:
            self.warnings.append(warning)
        return turn

    # ------------------------------------------------- turn-taking loop

    def generate_section(
        self, index: int, preceding: list[DialogueTurn], on_turn: Callable[[int], None]
    ) -> list[DialogueTurn]:
        sections = self.plan.sections
        section = sections[index]
        next_section = sections[index + 1] if index + 1 < len(sections) else None
        must = self._must_ids(section)
        budget = section.word_budget
        state = _SectionState()

        while True:
            n = len(state.turns)
            context = preceding + state.turns
            # A non-final section must close on Host B so Host A can open the next one without
            # speaking twice. If Host A would speak next, closing takes two more turns (a brief
            # Host A wrap-up, then Host B's bridge), so reserve room for both, each sized like
            # this section's turns so far.
            closer_ok = next_section is None or self._next_speaker(context) == "B"
            turns_to_close = 1 if closer_ok else 2
            per_turn = max(BRIDGE_RESERVE_WORDS, state.words / n) if n else BRIDGE_RESERVE_WORDS
            projected = state.words + turns_to_close * per_turn
            done = (must <= state.covered and projected >= 0.85 * budget) or projected >= 1.10 * budget
            last_slot = n >= settings.MAX_TURNS_PER_SECTION - 1
            # Both hosts must be heard by the end of the section; the closing turn counts.
            closer = self._next_speaker(context)
            both_heard = {t.host for t in state.turns} | {closer} == {"A", "B"}
            if n > 0 and ((done and both_heard) or last_slot) and closer_ok:
                speaker = closer
                instruction = self._closing_instruction(next_section)
                turn = self._turn(speaker, section, next_section, state, context, instruction)
                state.turns.append(turn)
                on_turn(len(state.turns))
                break

            if n == 0:
                instruction = (
                    prompts.INSTRUCTION_OPEN_BODY
                    if section.role == "body"
                    else prompts.INSTRUCTION_OPEN_SECTION
                )
            elif (must <= state.covered and (budget - state.words) < 0.15 * budget) or (
                (done and both_heard) or last_slot
            ):
                instruction = prompts.INSTRUCTION_WRAP_UP
            else:
                instruction = prompts.INSTRUCTION_DEFAULT
            speaker = self._next_speaker(context)
            turn = self._turn(speaker, section, next_section, state, context, instruction)
            state.turns.append(turn)
            state.words += count_words(turn.text)
            state.covered.update(turn.covers)
            on_turn(len(state.turns))
        return state.turns

    def generate_sections(self, indices: set[int], progress_lo: float, progress_hi: float) -> None:
        sections = self.plan.sections
        todo = sorted(indices)
        assembled: list[DialogueTurn] = []
        done_count = 0
        for i, section in enumerate(sections):
            if i not in indices:
                assembled += self.section_turns.get(section.id, [])
                continue
            span = (progress_hi - progress_lo) / max(len(todo), 1)
            base = progress_lo + span * done_count

            def on_turn(k: int, i=i, base=base, span=span, section=section) -> None:
                frac = min(k / 10, 0.95)
                self.emit(
                    "generating_script",
                    f"Writing section {i + 1} of {len(sections)}: {section.title} (turn {k})",
                    base + span * frac,
                )

            self.emit(
                "generating_script",
                f"Writing section {i + 1} of {len(sections)}: {section.title}",
                base,
            )
            turns = self.generate_section(i, assembled, on_turn)
            self.section_turns[section.id] = turns
            assembled += turns
            done_count += 1

    # ---------------------------------------------------------- revisions

    def revise_for_duration(self) -> None:
        tolerance = settings.DURATION_TOLERANCE
        for revision in range(1, settings.MAX_REVISIONS + 1):
            total = sum(count_words(t.text) for t in self.script())
            target = self.plan.total_word_budget
            deviation = (total - target) / target
            if abs(deviation) <= tolerance:
                return
            lo = 75 + (revision - 1) * 5
            self.emit(
                "revising_outline",
                f"Script is {total} words vs {target} target ({deviation:+.0%}); adjusting the plan "
                f"(pass {revision} of {settings.MAX_REVISIONS})...",
                lo,
            )
            measurements = [
                {"section_id": s.id, "word_budget": s.word_budget, "actual_words": self._section_words(s.id)}
                for s in self.plan.sections
            ]
            old = {s.id: s for s in self.plan.sections}
            new_plan = producer.revise_plan(
                self.plan, measurements, total, self.job.source_text, self.charter
            )
            regenerate: set[int] = set()
            for i, s in enumerate(new_plan.sections):
                prev = old.get(s.id)
                actual = self._section_words(s.id) if prev else 0
                nxt = new_plan.sections[i + 1] if i + 1 < len(new_plan.sections) else None
                nxt_prev = old.get(nxt.id) if nxt else None
                if (
                    prev is None
                    or s.word_budget != prev.word_budget
                    or abs(actual - s.word_budget) > SECTION_DEVIATION_FOR_REGEN * s.word_budget
                    or (nxt is not None and (nxt_prev is None or nxt_prev.title != nxt.title))
                ):
                    regenerate.add(i)
            self.plan = new_plan
            if new_plan.capacity_warning:
                self.emit("revising_outline", new_plan.capacity_warning, lo)
            if not regenerate:
                break
            self.generate_sections(regenerate, lo + 1, lo + 4)

        total = sum(count_words(t.text) for t in self.script())
        target = self.plan.total_word_budget
        deviation = (total - target) / target
        if abs(deviation) > tolerance:
            self.warnings.append(
                f"After {settings.MAX_REVISIONS} revision passes the script is {total} words vs a "
                f"{target}-word budget ({deviation:+.1%}, about "
                f"{total / settings.WORDS_PER_MINUTE:.1f} min vs {self.plan.target_minutes} min)."
            )

    # ----------------------------------------------------------- coverage

    def missing_must_cover(self) -> list[KeyPoint]:
        covered = {c for t in self.script() for c in t.covers}
        return [
            k
            for s in self.plan.sections
            for k in s.key_points
            if k.priority == "must_cover" and k.id not in covered
        ]

    def repair_coverage(self) -> None:
        missing = self.missing_must_cover()
        if not missing:
            return
        self.emit(
            "generating_script",
            f"Adding coverage for {len(missing)} missed must-cover idea(s)...",
            86,
        )
        by_section: dict[str, list[KeyPoint]] = {}
        owner = {k.id: s.id for s in self.plan.sections for k in s.key_points}
        for k in missing:
            by_section.setdefault(owner[k.id], []).append(k)

        sections = self.plan.sections
        for i, section in enumerate(sections):
            points = by_section.get(section.id)
            if not points:
                continue
            next_section = sections[i + 1] if i + 1 < len(sections) else None
            existing = self.section_turns.get(section.id, [])
            head, closing = (existing[:-1], existing[-1:]) if len(existing) > 1 else (existing, [])
            preceding = [t for s in sections[:i] for t in self.section_turns.get(s.id, [])]
            state = _SectionState(
                turns=[], words=sum(count_words(t.text) for t in head),
                covered={c for t in head for c in t.covers},
            )
            outstanding = {k.id for k in points}
            added: list[DialogueTurn] = []
            for _ in range(REPAIR_TURNS_PER_POINT * len(points)):
                if not outstanding:
                    break
                still = [k for k in points if k.id in outstanding]
                instruction = prompts.fill(
                    prompts.INSTRUCTION_REPAIR,
                    missing_key_points="; ".join(f"[{k.id}] {k.text}" for k in still),
                )
                context = preceding + head + added
                speaker = self._next_speaker(context)
                turn = self._turn(speaker, section, next_section, state, context, instruction)
                added.append(turn)
                state.words += count_words(turn.text)
                state.covered.update(turn.covers)
                outstanding -= set(turn.covers)
            if added and closing and added[-1].host == closing[0].host:
                # Keep strict alternation into the existing closing turn with a brief wrap-up.
                context = preceding + head + added
                speaker = self._next_speaker(context)
                turn = self._turn(
                    speaker, section, next_section, state, context, prompts.INSTRUCTION_WRAP_UP
                )
                added.append(turn)
                state.covered.update(turn.covers)
            self.section_turns[section.id] = head + added + closing

    def coverage_report(self) -> CoverageReport:
        script = self.script()
        valid_ids = {k.id for s in self.plan.sections for k in s.key_points}
        covered = {c for t in script for c in t.covers if c in valid_ids}
        must = [k for s in self.plan.sections for k in s.key_points if k.priority == "must_cover"]
        missing = [k for k in must if k.id not in covered]
        per_section = {s.id: self._section_words(s.id) for s in self.plan.sections}
        total = sum(per_section.values())
        warnings = list(self.warnings)
        if self.plan.capacity_warning:
            warnings.insert(0, self.plan.capacity_warning)
        if missing:
            warnings.append(
                f"{len(missing)} must-cover idea(s) were not addressed even after a repair pass."
            )
        return CoverageReport(
            must_cover_total=len(must),
            must_cover_hit=len(must) - len(missing),
            missing_ids=[k.id for k in missing],
            missing_points={k.id: k.text for k in missing},
            per_section_words=per_section,
            total_words=total,
            estimated_minutes=round(total / settings.WORDS_PER_MINUTE, 2),
            warnings=list(dict.fromkeys(warnings)),
        )

    # ---------------------------------------------------------------- run

    def run(self) -> None:
        job = self.job
        self.emit("extracting_facts", "Reading the source and extracting key points...")
        self.plan = producer.produce_plan(
            job.source_text,
            job.target_minutes,
            self.charter,
            on_progress=lambda msg: self.emit("extracting_facts", msg, 10),
        )
        n_must = sum(1 for s in self.plan.sections for k in s.key_points if k.priority == "must_cover")
        self.emit(
            "extracting_facts",
            f"Plan ready: {len(self.plan.sections)} sections, {n_must} must-cover ideas.",
            18,
        )

        self.generate_sections(set(range(len(self.plan.sections))), 20, 72)
        self.revise_for_duration()
        self.repair_coverage()
        for turn in self.script():  # drop covers that point at ids no longer in the plan
            valid = {k.id for s in self.plan.sections for k in s.key_points}
            turn.covers = [c for c in turn.covers if c in valid]
        coverage = self.coverage_report()

        # Save the script first so a failed voice pass can be redone without the LLM.
        script_path = settings.OUTPUT_DIR / f"{job.job_id}.script.json"
        script_path.write_text(
            json.dumps([t.model_dump() for t in self.script()], indent=1), encoding="utf-8"
        )
        provider = audio.get_provider()
        self.emit("synthesizing_audio", "Producing audio...")
        audio.produce_audio(self.script(), job.job_id, provider)
        write_transcript(self.plan, self.script(), coverage, job.job_id, provider.name)

        data = CompleteData(
            audio_url=f"/audio/{job.job_id}.mp3",
            transcript_url=f"/api/transcript/{job.job_id}",
            coverage=coverage,
            tts_provider=provider.name,
        )
        self.emit("complete", "Your podcast is ready.", data=data.model_dump())


def run_job(job: JobInput, sink: Callable[[StreamEvent], None]) -> None:
    """Run the whole pipeline. Always ends with exactly one 'complete' or 'error' event."""
    emit = Emitter(sink)
    try:
        if not (settings.MIN_TARGET_MINUTES <= job.target_minutes <= settings.MAX_TARGET_MINUTES):
            raise InputError(
                f"target_minutes must be between {settings.MIN_TARGET_MINUTES} and "
                f"{settings.MAX_TARGET_MINUTES}."
            )
        Orchestrator(job, emit).run()
    except Cancelled:
        log.info("Job %s cancelled by client disconnect", job.job_id)
    except (InputError, LLMError, audio.TTSError) as exc:
        emit("error", str(exc), data=ErrorData(detail=str(exc)).model_dump())
    except Exception as exc:  # noqa: BLE001 - every failure must become an error event
        log.exception("Job %s failed", job.job_id)
        detail = f"Unexpected error while producing the podcast: {type(exc).__name__}: {exc}"
        try:
            emit("error", "Something went wrong while producing the podcast.", data=ErrorData(detail=detail).model_dump())
        except Cancelled:
            pass
    finally:
        if not emit.terminal_sent:
            try:
                detail = "The job ended without a result."
                emit("error", detail, data=ErrorData(detail=detail).model_dump())
            except Cancelled:
                pass
