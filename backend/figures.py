"""Read the figures in a PDF: diagrams, charts, and tables that text extraction cannot see.

Pages that draw a figure (or carry almost no text) are copied into a smaller PDF and sent to
the model as a document block, so it sees each page as an image. Its transcription of each
page is merged into the source text under that page's marker, once, before planning starts;
the rest of the pipeline stays text-only.

Failures here never stop a job: the episode is made from the extracted text, with a warning.
"""

from __future__ import annotations

import base64
import io
import logging
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from config import settings
from parsing import PdfPage, pdf_pages

log = logging.getLogger("podcastfactory.figures")

# An image drawn on at least this share of pages is part of the slide template
# (background, logo, footer art), not content.
_TEMPLATE_SHARE = 0.5


class FigureNote(BaseModel):
    position: int = Field(description="1-based position of the page in the attached PDF")
    transcription: str = Field(
        description="Everything the figures on the page convey; empty if they are decorative only"
    )


class FigureNotes(BaseModel):
    pages: list[FigureNote]


FIGURES_SYSTEM = """You transcribe the figures in pages of a course reading so that a writer \
who cannot see the pages can use them as source material.

For each attached page, cover only content that lives in diagrams, charts, tables, \
screenshots, and other images; the page's ordinary body text has already been extracted, so \
do not repeat it.
- Copy every word, label, number, and caption in the figures exactly as written.
- Describe the structure in plain sentences: what is connected to what, the direction of \
arrows, axes and their labels, quadrants, rows and columns, layers, sequences, and which \
items are grouped or contrasted.
- For charts, give the values or trends that can be read off the chart, and say when a \
value is approximate.
- Do not interpret, evaluate, or add facts that are not on the page.
- If a page has no figures, or only decorative ones (backgrounds, logos, photos with no \
information), return an empty transcription for it.

Return ONLY a JSON object: {"pages": [{"position": <int>, "transcription": "<text>"}]}, \
with one entry per attached page, in order. No commentary, no markdown fences."""


@dataclass
class FigureResult:
    notes: dict[int, str] = field(default_factory=dict)  # 0-based PDF page index -> text
    pages_sent: int = 0
    warnings: list[str] = field(default_factory=list)


def select_figure_pages(pages: list[PdfPage]) -> list[PdfPage]:
    """Pages worth showing to the model: they draw a non-template image, or have little text."""
    counts: dict[int, int] = {}
    for page in pages:
        for image in page.image_ids:
            counts[image] = counts.get(image, 0) + 1
    threshold = max(2, _TEMPLATE_SHARE * len(pages))
    template = {image for image, n in counts.items() if n >= threshold}
    chosen = []
    for page in pages:
        has_figure = bool(page.image_ids - template)
        low_text = len(page.text.split()) < settings.FIGURE_LOW_TEXT_WORDS
        if has_figure or low_text:
            chosen.append(page)
    return chosen


def _sub_pdf(reader, indices: list[int]) -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for i in indices:
        writer.add_page(reader.pages[i])
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _call_model(prompt: str, pdf_bytes: bytes) -> FigureNotes:
    from llm import call_structured  # imported here so tests can run without the SDK

    document = {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": base64.standard_b64encode(pdf_bytes).decode("ascii"),
        },
    }
    return call_structured(
        FIGURES_SYSTEM,
        prompt,
        FigureNotes,
        attachments=[document],
        effort=settings.FIGURE_EFFORT,
        max_tokens=16000,
    )


def describe_pdf_figures(data: bytes) -> FigureResult:
    result = FigureResult()
    try:
        reader, pages = pdf_pages(data)
    except Exception as exc:  # noqa: BLE001  (parsing reports bad PDFs itself)
        log.info("Skipping figure reading: %s", exc)
        return result

    chosen = select_figure_pages(pages)
    if len(chosen) > settings.FIGURE_MAX_PAGES:
        result.warnings.append(
            f"Figures were read on the first {settings.FIGURE_MAX_PAGES} of {len(chosen)} pages "
            "that have them (FIGURE_MAX_PAGES); figures on later pages were not read."
        )
        chosen = chosen[: settings.FIGURE_MAX_PAGES]

    size = max(1, settings.FIGURE_PAGES_PER_CALL)
    failed: list[str] = []
    for start in range(0, len(chosen), size):
        batch = chosen[start : start + size]
        listing = "\n".join(f"{n}. {p.marker}" for n, p in enumerate(batch, start=1))
        prompt = (
            f"The attached PDF has {len(batch)} pages taken from a longer reading. "
            f"In order, they are:\n{listing}\n\nTranscribe the figures on each page."
        )
        try:
            notes = _call_model(prompt, _sub_pdf(reader, [p.index for p in batch]))
        except Exception as exc:  # noqa: BLE001
            log.warning("Figure reading failed for %d pages: %s", len(batch), exc)
            failed.extend(p.marker for p in batch)
            continue
        result.pages_sent += len(batch)
        for note in notes.pages:
            text = note.transcription.strip()
            if text and 1 <= note.position <= len(batch):
                result.notes[batch[note.position - 1].index] = text

    if failed:
        result.warnings.append(
            "Figures could not be read on "
            + ", ".join(m.strip("[]") for m in failed)
            + "; the episode uses only the text on those pages."
        )
    return result
