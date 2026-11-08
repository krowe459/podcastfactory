"""PodcastFactory API.

Run from backend/:  uvicorn main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import uuid
from typing import AsyncIterator, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from config import settings
from contracts import ErrorData, StreamEvent
from parsing import InputError, build_source_text
from pipeline import Cancelled, JobInput, run_job
from transcript import transcript_path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("podcastfactory")

app = FastAPI(title="PodcastFactory", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.mount("/audio", StaticFiles(directory=settings.OUTPUT_DIR), name="audio")

HEARTBEAT_SECONDS = 15
_JOB_ID = re.compile(r"^[0-9a-f]{32}$")


def _sse(event: StreamEvent) -> str:
    return f"data: {event.model_dump_json()}\n\n"


def _error_event(detail: str) -> StreamEvent:
    return StreamEvent(status="error", message=detail, progress=100, data=ErrorData(detail=detail).model_dump())


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "model": settings.ANTHROPIC_MODEL,
        "tts_provider": settings.TTS_PROVIDER,
    }


@app.post("/api/generate-podcast")
async def generate_podcast(
    file: Optional[UploadFile] = File(None),
    text: Optional[str] = Form(None),
    topic_focus: Optional[str] = Form(None),
    target_minutes: Optional[str] = Form("10"),
    course_name: Optional[str] = Form(None),
) -> StreamingResponse:
    job_id = uuid.uuid4().hex
    # Read and parse the upload inside the stream so that every failure is an "error" event.
    filename = file.filename if file is not None and file.filename else None
    data = await file.read() if filename else None

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Optional[StreamEvent]] = asyncio.Queue()
    cancelled = threading.Event()

    def sink(event: StreamEvent) -> None:
        if cancelled.is_set():
            raise Cancelled()
        loop.call_soon_threadsafe(queue.put_nowait, event)

    def work() -> None:
        try:
            try:
                if data is not None and len(data) > settings.MAX_UPLOAD_MB * 1024 * 1024:
                    raise InputError(f"The file is larger than {settings.MAX_UPLOAD_MB} MB.")
                try:
                    minutes = int(str(target_minutes or "10").strip())
                except ValueError as exc:
                    raise InputError("target_minutes must be a whole number of minutes.") from exc
                source = build_source_text(filename, data, text)
            except InputError as exc:
                sink(_error_event(str(exc)))
                return
            log.info("Job %s: %d chars, %d min, focus=%r", job_id, len(source), minutes, topic_focus)
            run_job(
                JobInput(
                    job_id=job_id,
                    source_text=source,
                    target_minutes=minutes,
                    topic_focus=topic_focus or "",
                    course_name=course_name or "",
                ),
                sink,
            )
        except Cancelled:
            pass
        except Exception as exc:  # noqa: BLE001
            log.exception("Job %s crashed before the pipeline started", job_id)
            try:
                sink(_error_event(f"Unexpected error: {type(exc).__name__}: {exc}"))
            except Cancelled:
                pass
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    async def stream() -> AsyncIterator[str]:
        worker = loop.run_in_executor(None, work)
        terminal = False
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"  # SSE comment; ignored by the client parser
                    continue
                if event is None:
                    break
                terminal = terminal or event.status in ("complete", "error")
                yield _sse(event)
            if not terminal:  # defensive: never close without a terminal event
                yield _sse(_error_event("The job ended without a result."))
            await worker
        finally:
            cancelled.set()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/transcript/{job_id}")
def get_transcript(job_id: str, download: bool = False) -> PlainTextResponse:
    if not _JOB_ID.match(job_id):
        raise HTTPException(status_code=404, detail="Transcript not found")
    path = transcript_path(job_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Transcript not found")
    headers = {}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="podcast-transcript-{job_id[:8]}.md"'
    return PlainTextResponse(
        path.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8", headers=headers
    )
