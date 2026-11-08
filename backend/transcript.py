"""Transcript writer: Markdown with section headings, host lines, and source refs."""

from __future__ import annotations

from pathlib import Path

from config import settings
from contracts import CoverageReport, DialogueTurn, ProductionPlan


def render_transcript(
    plan: ProductionPlan, turns: list[DialogueTurn], coverage: CoverageReport, tts_provider: str
) -> str:
    lines: list[str] = [f"# {plan.title}", ""]
    lines.append(
        "_PodcastFactory transcript. An AI-generated guide to the reading; check important "
        "details against the source._"
    )
    lines += ["", f"**About the reading:** {plan.source_summary}", ""]
    lines.append(
        f"**Length:** {coverage.total_words} words, about {coverage.estimated_minutes:.1f} min "
        f"(target {plan.target_minutes} min)  "
    )
    lines.append(
        f"**Coverage:** {coverage.must_cover_hit} of {coverage.must_cover_total} must-cover ideas addressed  "
    )
    if tts_provider == "stub":
        lines.append("**Audio:** prototype, silent placeholder voices")
    lines.append("")

    by_section: dict[str, list[DialogueTurn]] = {}
    for t in turns:
        by_section.setdefault(t.section_id, []).append(t)

    names = {"A": settings.HOST_A_NAME, "B": settings.HOST_B_NAME}
    for section in plan.sections:
        lines += [f"## {section.order}. {section.title}", ""]
        for t in by_section.get(section.id, []):
            label = f"Host {t.host}" + (f" ({names[t.host]})" if names[t.host] else "")
            lines += [f"{label}: {t.text}", ""]
        refs = list(dict.fromkeys(k.source_ref for k in section.key_points if k.source_ref))
        if refs:
            lines.append("Source references:")
            lines += [f"- {r}" for r in refs]
            lines.append("")

    if coverage.missing_ids or coverage.warnings:
        lines += ["---", "", "## Production notes", ""]
        for kid in coverage.missing_ids:
            lines.append(f"- Not covered: {coverage.missing_points.get(kid, kid)}")
        for w in coverage.warnings:
            lines.append(f"- {w}")
        lines.append("")
    return "\n".join(lines)


def transcript_path(job_id: str) -> Path:
    return settings.OUTPUT_DIR / f"{job_id}.md"


def write_transcript(
    plan: ProductionPlan,
    turns: list[DialogueTurn],
    coverage: CoverageReport,
    job_id: str,
    tts_provider: str,
) -> Path:
    path = transcript_path(job_id)
    path.write_text(render_transcript(plan, turns, coverage, tts_provider), encoding="utf-8")
    return path
