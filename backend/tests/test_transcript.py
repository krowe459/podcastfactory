"""Transcript rendering: source references and coverage reflect what the hosts actually said."""

from __future__ import annotations

from contracts import CoverageReport, DialogueTurn, KeyPoint, ProductionPlan, Section
from transcript import render_transcript


def _kp(kid: str, priority: str, ref: str) -> KeyPoint:
    return KeyPoint(id=kid, text=f"text of {kid}", kind="claim", priority=priority, source_ref=ref)


def _plan() -> ProductionPlan:
    section = Section(
        id="s1",
        order=1,
        role="body",
        title="Architecture",
        pedagogical_goal="g",
        key_points=[
            _kp("s1-k1", "must_cover", "Module 4, p. 17"),
            _kp("s1-k2", "should_cover", "Module 4, p. 14"),  # planned, never discussed
            _kp("s1-k3", "should_cover", "Module 4, p. 18"),
            _kp("s1-k4", "nice_to_have", "unspecified"),
        ],
        word_budget=300,
    )
    return ProductionPlan(
        title="T", source_summary="S", target_minutes=10, total_word_budget=1500, sections=[section]
    )


def _turns() -> list[DialogueTurn]:
    return [
        DialogueTurn(section_id="s1", host="A", text="a", delivery_instruction="d", covers=["s1-k1"]),
        DialogueTurn(
            section_id="s1", host="B", text="b", delivery_instruction="d", covers=["s1-k3", "s1-k4"]
        ),
    ]


def _coverage(**extra) -> CoverageReport:
    return CoverageReport(
        must_cover_total=1,
        must_cover_hit=1,
        missing_ids=[],
        per_section_words={"s1": 2},
        total_words=2,
        estimated_minutes=0.1,
        **extra,
    )


def test_only_discussed_points_are_cited():
    md = render_transcript(_plan(), _turns(), _coverage(), "openai")
    refs = md.split("Source references:")[1]
    assert "p. 17" in refs and "p. 18" in refs
    assert "p. 14" not in refs  # the planned-but-skipped page is not cited
    assert "unspecified" not in refs


def test_skipped_should_cover_points_are_reported():
    cov = _coverage(should_cover_total=2, should_cover_hit=1, skipped_points={"s1-k2": "text of s1-k2"})
    md = render_transcript(_plan(), _turns(), cov, "openai")
    assert "1 of 1 must-cover ideas addressed; 1 of 2 should-cover" in md
    assert "Planned but not discussed (should-cover): text of s1-k2" in md


def test_old_reports_without_should_cover_fields_still_render():
    md = render_transcript(_plan(), _turns(), _coverage(), "openai")
    assert "1 of 1 must-cover ideas addressed  " in md
    assert "## Production notes" not in md
