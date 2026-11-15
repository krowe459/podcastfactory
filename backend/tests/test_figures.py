"""Figure reading for PDFs: page selection, merging transcriptions, and failure handling."""

from __future__ import annotations

import io

import pytest

import figures
from config import settings
from parsing import FIGURE_NOTE_HEADER, InputError, PdfPage, parse_upload


def _blank_pdf(n: int) -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(n):
        writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _page(i: int, words: int, images: set[int]) -> PdfPage:
    return PdfPage(i, f"[Page {i + 1}]", " ".join(["word"] * words), frozenset(images))


def test_template_images_are_ignored_and_figures_or_sparse_pages_selected():
    template = {900, 901}  # background and logo on every slide
    pages = [
        _page(0, 120, template),  # text slide
        _page(1, 120, template | {5}),  # text plus a diagram
        _page(2, 6, template),  # title-only slide: maybe a vector diagram
        _page(3, 120, template),
        _page(4, 120, template | {7}),
    ]
    chosen = [p.index for p in figures.select_figure_pages(pages)]
    assert chosen == [1, 2, 4]


def test_image_only_pdf_parses_when_figures_are_transcribed():
    data = _blank_pdf(2)
    with pytest.raises(InputError):
        parse_upload("deck.pdf", data)
    text = parse_upload("deck.pdf", data, {1: "Box A -> Box B"})
    assert "[Page 2]" in text and FIGURE_NOTE_HEADER in text and "Box A -> Box B" in text
    assert "[Page 1]" not in text


def test_transcriptions_map_back_to_pages_across_batches(monkeypatch):
    monkeypatch.setattr(settings, "FIGURE_PAGES_PER_CALL", 2)
    prompts = []

    def fake(prompt, pdf_bytes):
        from pypdf import PdfReader

        n = len(PdfReader(io.BytesIO(pdf_bytes)).pages)
        prompts.append(prompt)
        return figures.FigureNotes(
            pages=[figures.FigureNote(position=k, transcription=f"fig {len(prompts)}.{k}" if k == 1 else "")
                   for k in range(1, n + 1)]
        )

    monkeypatch.setattr(figures, "_call_model", fake)
    result = figures.describe_pdf_figures(_blank_pdf(3))
    assert len(prompts) == 2 and "1. [Page 1]" in prompts[0] and "1. [Page 3]" in prompts[1]
    assert result.notes == {0: "fig 1.1", 2: "fig 2.1"}
    assert result.pages_sent == 3 and result.warnings == []


def test_model_failure_keeps_the_job_going_with_a_warning(monkeypatch):
    def boom(prompt, pdf_bytes):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(figures, "_call_model", boom)
    result = figures.describe_pdf_figures(_blank_pdf(2))
    assert result.notes == {}
    assert result.warnings and "Page 1, Page 2" in result.warnings[0]


def test_page_cap_adds_warning(monkeypatch):
    monkeypatch.setattr(settings, "FIGURE_MAX_PAGES", 1)
    monkeypatch.setattr(figures, "_call_model", lambda p, b: figures.FigureNotes(pages=[]))
    result = figures.describe_pdf_figures(_blank_pdf(3))
    assert result.pages_sent == 1
    assert any("FIGURE_MAX_PAGES" in w for w in result.warnings)
