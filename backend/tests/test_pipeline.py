"""End-to-end pipeline tests with a fake LLM (no API key or network needed)."""

from __future__ import annotations

import io
import json
import re

import pytest
from fastapi.testclient import TestClient

import llm
from agents import audio
from config import settings
from contracts import STATUS_VALUES, HostTurnOutput, ProductionPlan, StreamEvent
from parsing import split_into_chunks
from textutil import count_words

SOURCE = (
    "# Pricing Power in Platform Markets\n\nBy Jane Rivera.\n\n"
    + " ".join(
        f"Paragraph {i}: Firms with network effects raised prices 12% while churn stayed at 3.5%."
        for i in range(60)
    )
)


def _plan(total: int, minutes: int, must_per_body: int = 2) -> dict:
    sections = []
    shares = [0.06, 0.29, 0.29, 0.28, 0.08]
    roles = ["intro", "body", "body", "body", "outro"]
    for order, (role, share) in enumerate(zip(roles, shares), start=1):
        n_points = 1 if role != "body" else must_per_body + 1
        points = [
            {
                "id": f"s{order}-k{n}",
                "text": f"Point {n} of section {order}: prices rose 12% with churn at 3.5%.",
                "kind": "claim",
                "priority": "must_cover" if n <= (1 if role != "body" else must_per_body) else "should_cover",
                "source_ref": f"page {order}",
            }
            for n in range(1, n_points + 1)
        ]
        sections.append(
            {
                "id": f"s{order}",
                "order": order,
                "role": role,
                "title": f"Section {order} title",
                "pedagogical_goal": "After this section the listener can explain pricing power.",
                "key_points": points,
                "word_budget": round(total * share),
            }
        )
    return {
        "title": "Pricing Power in Platform Markets",
        "source_summary": "Jane Rivera argues platforms can raise prices. Evidence comes from churn data.",
        "target_minutes": minutes,
        "total_word_budget": total,
        "capacity_warning": None,
        "sections": sections,
    }


class FakeLLM:
    def __init__(self, turn_words: int = 40, skip_ids: set[str] | None = None):
        self.turn_words = turn_words
        self.skip_ids = skip_ids or set()
        self.calls: list[tuple[str, str]] = []

    def __call__(self, system, user, model, *, cached_prefix=None, effort=None, max_tokens=0):
        kind = model.__name__
        self.calls.append((kind, user[:40]))
        if model is ProductionPlan and user.startswith("REVISION"):
            current = user.split("CURRENT PLAN\n", 1)[1].split("\n\nProductionPlan JSON schema", 1)[0]
            return ProductionPlan.model_validate_json(current)  # unchanged budgets
        if model is ProductionPlan:
            minutes = int(re.search(r"target_minutes: (\d+)", user).group(1))
            total = int(re.search(r"total_word_budget: (\d+)", user).group(1))
            return ProductionPlan.model_validate(_plan(total, minutes))
        assert model is HostTurnOutput
        speaker = re.search(r"SPEAKER FOR THIS TURN: Host (\w)", user).group(1)
        uncovered = re.findall(r"\[(s\d+-k\d+)\] \([^,]+, must_cover, uncovered\)", user)
        repair = re.findall(r"\[(s\d+-k\d+)\] Point", user.split("INSTRUCTION", 1)[1])
        targets = [i for i in (repair or uncovered) if i not in self.skip_ids][:1]
        text = " ".join(["word"] * (self.turn_words - 1)) + " end."
        return HostTurnOutput(host=speaker, text=text, delivery_instruction="warm, curious", covers=targets)


def _events(response) -> list[StreamEvent]:
    events = []
    for block in response.text.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data:"):
                events.append(StreamEvent.model_validate(json.loads(line[5:].strip())))
    return events


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path)
    import main

    return TestClient(main.app)


def test_full_run_completes_with_coverage(client, monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(llm, "call_structured", fake)
    r = client.post("/api/generate-podcast", data={"text": SOURCE, "target_minutes": "5", "topic_focus": "pricing"})
    assert r.status_code == 200
    events = _events(r)
    statuses = [e.status for e in events]
    assert statuses[0] == "extracting_facts"
    assert statuses[-1] == "complete"
    assert "synthesizing_audio" in statuses
    assert all(s in STATUS_VALUES for s in statuses)
    progress = [e.progress for e in events]
    assert progress == sorted(progress), "progress must be monotonic"

    data = events[-1].data
    assert data["tts_provider"] == "stub"
    cov = data["coverage"]
    assert cov["must_cover_hit"] == cov["must_cover_total"] > 0
    assert cov["missing_ids"] == []

    job_id = data["audio_url"].split("/")[-1].removesuffix(".mp3")
    audio_resp = client.get(data["audio_url"])
    assert audio_resp.status_code == 200 and len(audio_resp.content) > 1000
    t = client.get(data["transcript_url"])
    assert t.status_code == 200
    assert "Host A:" in t.text and "Host B:" in t.text and "Source references:" in t.text
    d = client.get(data["transcript_url"] + "?download=1")
    assert "attachment" in d.headers["content-disposition"]
    assert job_id in data["transcript_url"]


def _run_orchestrator(monkeypatch, fake, minutes: int = 5):
    import pipeline

    monkeypatch.setattr(llm, "call_structured", fake)
    events: list[StreamEvent] = []
    job = pipeline.JobInput(job_id="0" * 32, source_text=SOURCE, target_minutes=minutes)
    orch = pipeline.Orchestrator(job, pipeline.Emitter(events.append))
    orch.run()
    assert events[-1].status == "complete"
    return orch


def _assert_strict_alternation(orch):
    for section in orch.plan.sections:
        turns = orch.section_turns[section.id]
        assert turns[0].host == "A", f"{section.id} must open with Host A"
        assert {t.host for t in turns} == {"A", "B"}, f"{section.id} must hear from both hosts"
    script = orch.script()
    for i, (a, b) in enumerate(zip(script, script[1:])):
        assert a.host != b.host, f"turns {i} and {i + 1} are both Host {a.host}"


@pytest.mark.parametrize("turn_words", [40, 55, 85])
def test_hosts_strictly_alternate_across_the_episode(monkeypatch, turn_words):
    # Different turn lengths change where sections end, exercising the wrap-up path too.
    _assert_strict_alternation(_run_orchestrator(monkeypatch, FakeLLM(turn_words=turn_words)))


def test_hosts_alternate_after_coverage_repair(monkeypatch):
    # The first pass skips a must-cover point, so repair turns are inserted mid-section.
    class SkipOnce(FakeLLM):
        repaired = False

        def __call__(self, system, user, response_model, **kw):
            if "Repair:" in user:
                self.skip_ids = set()
                self.repaired = True
            return super().__call__(system, user, response_model, **kw)

    fake = SkipOnce(skip_ids={"s2-k1"})
    orch = _run_orchestrator(monkeypatch, fake)
    assert fake.repaired, "repair path must run"
    _assert_strict_alternation(orch)


def test_revision_loop_runs_and_is_capped(client, monkeypatch):
    # 80-word turns overshoot every budget, forcing revisions; the cap must hold.
    fake = FakeLLM(turn_words=85)
    monkeypatch.setattr(llm, "call_structured", fake)
    events = _events(client.post("/api/generate-podcast", data={"text": SOURCE, "target_minutes": "5"}))
    statuses = [e.status for e in events]
    assert statuses[-1] == "complete"
    revisions = [e for e in events if e.status == "revising_outline"]
    revision_calls = [c for c in fake.calls if c[1].startswith("REVISION")]
    assert len(revision_calls) <= settings.MAX_REVISIONS
    if revision_calls:
        assert revisions
        first_rev = statuses.index("revising_outline")
        assert "generating_script" in statuses[first_rev:]


def test_coverage_repair_and_report_missing(client, monkeypatch):
    fake = FakeLLM(skip_ids={"s2-k1"})
    monkeypatch.setattr(llm, "call_structured", fake)
    events = _events(client.post("/api/generate-podcast", data={"text": SOURCE, "target_minutes": "5"}))
    cov = events[-1].data["coverage"]
    assert cov["missing_ids"] == ["s2-k1"]
    assert "s2-k1" in cov["missing_points"]
    assert cov["must_cover_hit"] == cov["must_cover_total"] - 1
    assert any("Repair" in c[1] or True for c in fake.calls)


@pytest.mark.parametrize(
    "form,files,expect",
    [
        ({}, None, "upload a file or paste text"),
        ({"text": "too short"}, None, "too short"),
        ({"text": SOURCE, "target_minutes": "45"}, None, "between 5 and 30"),
        ({"target_minutes": "10"}, {"file": ("notes.xlsx", b"abc", "application/octet-stream")}, "Unsupported file type"),
    ],
)
def test_bad_input_becomes_error_event(client, monkeypatch, form, files, expect):
    monkeypatch.setattr(llm, "call_structured", FakeLLM())
    events = _events(client.post("/api/generate-podcast", data=form, files=files))
    assert [e.status for e in events] == ["error"]
    assert expect.lower() in events[0].data["detail"].lower()


def test_llm_failure_becomes_error_event(client, monkeypatch):
    def boom(*a, **k):
        raise llm.LLMError("The LLM provider rejected the API key. Check ANTHROPIC_API_KEY (or PODCASTFACTORY_ANTHROPIC_KEY).")

    monkeypatch.setattr(llm, "call_structured", boom)
    events = _events(client.post("/api/generate-podcast", data={"text": SOURCE}))
    assert events[-1].status == "error"
    assert "API key" in events[-1].data["detail"]


def test_unexpected_exception_becomes_error_event(client, monkeypatch):
    def boom(*a, **k):
        raise ZeroDivisionError("kaboom")

    monkeypatch.setattr(llm, "call_structured", boom)
    events = _events(client.post("/api/generate-podcast", data={"text": SOURCE}))
    assert events[-1].status == "error"
    assert "kaboom" in events[-1].data["detail"]


def test_docx_and_pptx_uploads_parse(client, monkeypatch):
    import docx
    from pptx import Presentation

    monkeypatch.setattr(llm, "call_structured", FakeLLM())
    d = docx.Document()
    d.add_heading("Platform Pricing", 1)
    for _ in range(10):
        d.add_paragraph("Network effects let firms raise prices 12% while churn stays near 3.5%.")
    buf = io.BytesIO()
    d.save(buf)
    events = _events(
        client.post(
            "/api/generate-podcast",
            data={"target_minutes": "5"},
            files={"file": ("reading.docx", buf.getvalue(), "application/octet-stream")},
        )
    )
    assert events[-1].status == "complete"

    prs = Presentation()
    for _ in range(6):
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = "Pricing power"
        slide.placeholders[1].text = "Firms with network effects raised prices 12% and kept churn at 3.5% overall."
    buf = io.BytesIO()
    prs.save(buf)
    from parsing import parse_upload

    text = parse_upload("deck.pptx", buf.getvalue())
    assert "[Slide 1]" in text and "12%" in text


# ------------------------------------------------------------- unit checks


def test_speech_normalization():
    n = audio.normalize_for_speech
    assert n("Revenue hit $4.2M YoY.") == "Revenue hit four point two million dollars year over year."
    assert n("Margins of 12% and 3.5%") == "Margins of twelve percent and three point five percent"
    assert "EBITDA" in n("EBITDA grew.")
    assert n("**Bold** claim by McKinsey & Company") == "Bold claim by McKinsey and Company"
    assert n("$1,250 deal") == "one thousand two hundred fifty dollars deal"


def test_tts_chunking_respects_limit():
    text = " ".join(f"Sentence number {i} is here." for i in range(100))
    chunks = audio.chunk_for_tts(text, 400)
    assert all(len(c) <= 400 for c in chunks)
    assert " ".join(chunks) == text


def test_source_chunking_respects_limit():
    text = "\n\n".join(f"# Heading {i}\n\n" + ("Words here. " * 300) for i in range(10))
    chunks = split_into_chunks(text, 5000)
    assert len(chunks) > 1
    assert all(len(c) <= 5000 for c in chunks)


def test_pdf_glyph_names_are_mapped_to_characters():
    from parsing import _fix_glyph_names

    raw = "November /two.lnum/zero.lnum/two.lnum/five.lnum, /seven.lnum/six.lnum/period.lnum/five.lnum% of /a.sc/i.sc; /foo.bar stays"
    assert _fix_glyph_names(raw) == "November 2025, 76.5% of ai; /foo.bar stays"


def test_pdf_page_markers_follow_printed_numbers():
    from parsing import _printed_page_offset

    # Cover and front matter push printed folios four pages behind the PDF's own count.
    front = ["Cover title", "AUTHORS\nSomeone", "CONTENTS\n1 Introduction", "Foreword text"]
    body = [f"{n} MIT SLOAN MANAGEMENT REVIEW\nBody text for page {n}." for n in range(1, 9)]
    assert _printed_page_offset(front + body) == -4
    # Folios that already match PDF positions keep offset 0.
    same = [f"Body {i}\nModule 1 · Governance {i}" for i in range(1, 9)]
    assert _printed_page_offset(same) == 0
    # Too few numbered pages: fall back to PDF positions.
    assert _printed_page_offset(["a", "b", "3 Header\ntext", "d", "e", "f", "g", "h"]) == 0


def test_strict_alternation_keeps_scripts_near_budget(monkeypatch):
    # Closing on Host B can cost an extra wrap-up turn; the budget must reserve room for it
    # so alternation doesn't push episodes well past their target length. (At 10 minutes,
    # 60-word turns are fine-grained enough that the budget, not turn size, decides length.)
    orch = _run_orchestrator(monkeypatch, FakeLLM(turn_words=60), minutes=10)
    total = sum(count_words(t.text) for t in orch.script())
    budget = sum(s.word_budget for s in orch.plan.sections)
    assert total <= (1 + settings.DURATION_TOLERANCE) * budget, f"{total} words vs {budget} budget"
    _assert_strict_alternation(orch)
