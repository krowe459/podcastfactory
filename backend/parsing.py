"""Turn uploads and pasted text into plain text, and split long sources."""

from __future__ import annotations

import io
import re
from pathlib import Path

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".txt", ".md"}


class InputError(ValueError):
    """Bad user input; the message is shown to the user as-is."""


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# Some PDFs (fonts with OpenType variants such as lining figures or small caps) make pypdf emit
# raw glyph names like "/two.lnum" or "/e.sc" instead of characters. Map them back.
_GLYPH_CHARS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "period": ".", "comma": ",", "colon": ":", "semicolon": ";", "hyphen": "-",
    "endash": "–", "emdash": "—", "percent": "%", "dollar": "$",
    "parenleft": "(", "parenright": ")", "bracketleft": "[", "bracketright": "]",
    "slash": "/", "ampersand": "&", "plus": "+", "numbersign": "#",
    "quoteright": "’", "quoteleft": "‘", "quotedblleft": "“",
    "quotedblright": "”", "exclam": "!", "question": "?", "space": " ",
}
_GLYPH_NAME = re.compile(
    r"/([A-Za-z]+)\.(?:lnum|onum|tnum|pnum|sc|smcp|c2sc|sups|subs|sinf|numr|dnom|case|alt|ss\d\d)"
)


def _fix_glyph_names(text: str) -> str:
    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name in _GLYPH_CHARS:
            return _GLYPH_CHARS[name]
        if len(name) == 1:
            return name
        return m.group(0)

    return _GLYPH_NAME.sub(repl, text)


# A printed page number sits alone in a header/footer line, or at either end of a running head
# ("2 MIT SLOAN MANAGEMENT REVIEW", "Module 1 · Value Realization 6", "3The state of AI ...").
_FOLIO_PATTERNS = (
    re.compile(r"^(\d{1,3})$"),
    re.compile(r"^(\d{1,3})(?=\s*[A-Za-z])"),
    re.compile(r"(?<=[A-Za-z.,;:)])\s+(\d{1,3})$"),
)
_MAX_FOLIO_OFFSET = 12


def _printed_page_offset(page_texts: list[str]) -> int:
    """How far printed page numbers sit from PDF page positions (printed = position + offset).

    Covers and front matter usually push printed numbers behind the PDF's own count, so a
    reference to "page 14" would not match the "13" printed on that page. Each page votes for
    the offsets its header/footer numbers imply; an offset is trusted only when enough pages
    agree, otherwise 0 (PDF positions) is kept."""
    votes: dict[int, int] = {}
    numbered = 0
    for position, text in enumerate(page_texts, start=1):
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        edge = lines[:2] + lines[-2:]
        offsets = set()
        for line in edge:
            for pattern in _FOLIO_PATTERNS:
                m = pattern.search(line)
                if m and int(m.group(1)) >= 1:
                    offset = int(m.group(1)) - position
                    if abs(offset) <= _MAX_FOLIO_OFFSET:
                        offsets.add(offset)
        numbered += bool(offsets)
        for offset in offsets:
            votes[offset] = votes.get(offset, 0) + 1
    if not votes:
        return 0
    best, count = max(votes.items(), key=lambda kv: (kv[1], kv[0] == 0))
    if count >= 3 and count >= 0.4 * len(page_texts):
        return best
    return 0


def _page_markers(reader, page_texts: list[str]) -> list[str]:
    """Marker per page, using the numbers printed on the pages so source references match them."""
    root = reader.trailer["/Root"]
    if "/PageLabels" in root:
        return [f"[Page {label}]" for label in reader.page_labels]
    offset = _printed_page_offset(page_texts)
    markers = []
    for position in range(1, len(page_texts) + 1):
        printed = position + offset
        markers.append(f"[Page {printed}]" if printed >= 1 else f"[Unnumbered page {position} of the PDF]")
    return markers


def _parse_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:  # noqa: BLE001
                raise InputError("This PDF is password-protected.") from exc
        texts = [_fix_glyph_names(page.extract_text() or "").strip() for page in reader.pages]
        markers = _page_markers(reader, texts)
        parts = [f"{marker}\n{text}" for marker, text in zip(markers, texts) if text]
    except InputError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise InputError(f"Could not read the PDF: {exc}") from exc
    text = "\n\n".join(parts)
    if not text.strip():
        raise InputError(
            "No text could be extracted from this PDF. It may be a scanned image; "
            "try a text-based PDF or paste the text instead."
        )
    return text


def _parse_docx(data: bytes) -> str:
    import docx

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise InputError(f"Could not read the DOCX file: {exc}") from exc
    parts: list[str] = []
    for para in document.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = (para.style.name or "").lower() if para.style is not None else ""
        if style.startswith("heading"):
            level = re.sub(r"\D", "", style) or "1"
            parts.append(f"{'#' * min(int(level), 6)} {text}")
        elif style == "title":
            parts.append(f"# {text}")
        else:
            parts.append(text)
    for table in document.tables:
        rows = []
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            rows.append(" | ".join(cells))
        if rows:
            parts.append("[Table]\n" + "\n".join(rows))
    return "\n\n".join(parts)


def _parse_pptx(data: bytes) -> str:
    from pptx import Presentation

    try:
        prs = Presentation(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise InputError(f"Could not read the PPTX file: {exc}") from exc
    parts: list[str] = []
    for i, slide in enumerate(prs.slides, start=1):
        lines: list[str] = []
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    t = "".join(run.text for run in para.runs).strip()
                    if t:
                        lines.append(t)
            if getattr(shape, "has_table", False) and shape.has_table:
                for row in shape.table.rows:
                    lines.append(" | ".join(c.text.strip() for c in row.cells))
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                lines.append(f"Speaker notes: {notes}")
        if lines:
            parts.append(f"[Slide {i}]\n" + "\n".join(lines))
    return "\n\n".join(parts)


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def parse_upload(filename: str, data: bytes) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise InputError(
            f"Unsupported file type '{ext or filename}'. Upload a PDF, DOCX, PPTX, TXT, or MD file."
        )
    if not data:
        raise InputError("The uploaded file is empty.")
    if ext == ".pdf":
        text = _parse_pdf(data)
    elif ext == ".docx":
        text = _parse_docx(data)
    elif ext == ".pptx":
        text = _parse_pptx(data)
    else:
        text = _decode(data)
    return _clean(text)


def build_source_text(filename: str | None, data: bytes | None, pasted: str | None) -> str:
    parts: list[str] = []
    if filename and data is not None:
        parts.append(parse_upload(filename, data))
    if pasted and pasted.strip():
        parts.append(_clean(pasted))
    text = "\n\n".join(p for p in parts if p)
    if not text.strip():
        raise InputError("Provide a reading: upload a file or paste text.")
    if len(text.split()) < 50:
        raise InputError("The reading is too short to make a podcast from (under 50 words).")
    return text


_HEADING = re.compile(r"^(#{1,6} |\[Page [^\]]+\]|\[Unnumbered page \d+[^\]]*\]|\[Slide \d+\])", re.M)


def split_into_chunks(text: str, chunk_chars: int) -> list[str]:
    """Split at heading boundaries, then paragraphs, then sentences, into ~chunk_chars pieces."""
    if len(text) <= chunk_chars:
        return [text]

    # Blocks start at headings/page markers; within a block, split on blank lines.
    starts = [m.start() for m in _HEADING.finditer(text)]
    if not starts or starts[0] != 0:
        starts = [0] + starts
    blocks = [text[a:b] for a, b in zip(starts, starts[1:] + [len(text)])]

    units: list[str] = []
    for block in blocks:
        if len(block) <= chunk_chars:
            units.append(block)
            continue
        for para in re.split(r"\n\s*\n", block):
            if len(para) <= chunk_chars:
                units.append(para + "\n\n")
            else:
                sentences = re.split(r"(?<=[.!?])\s+", para)
                buf = ""
                for s in sentences:
                    while len(s) > chunk_chars:  # pathological: no sentence breaks
                        units.append(s[:chunk_chars])
                        s = s[chunk_chars:]
                    if len(buf) + len(s) + 1 > chunk_chars and buf:
                        units.append(buf)
                        buf = ""
                    buf = f"{buf} {s}" if buf else s
                if buf:
                    units.append(buf + "\n\n")

    chunks: list[str] = []
    current = ""
    for unit in units:
        if current and len(current) + len(unit) > chunk_chars:
            chunks.append(current.strip())
            current = ""
        current += unit
    if current.strip():
        chunks.append(current.strip())
    return chunks
