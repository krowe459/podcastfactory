# PodcastFactory

[![CI](https://github.com/krowe459/podcastfactory/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/krowe459/podcastfactory/actions/workflows/ci.yml)

Turns an assigned reading (case study, article, book chapter, or slide deck) into a two-host
podcast for Executive MBA students. It also reports which must-cover ideas the episode addressed.

> **Prototype audio.** Speech synthesis is a stub behind a swappable `TTSProvider` interface. The
> generated mp3 is **silent** and lasts as long as the script would take to read aloud. The UI shows a
> "Prototype audio: silent placeholder voices" badge whenever the stub is in use. The script,
> transcript, and coverage report are real.

## How it works

| Agent | Where | Job |
|---|---|---|
| 1. Executive Producer | `backend/agents/producer.py` | Reads the source and produces a `ProductionPlan`: ranked key points, sections, and word budgets. Revises the plan when the script runs long or short. |
| 2. Host A, the Guide | `backend/agents/hosts.py` | Frames, asks, translates jargon, and bridges sections. |
| 3. Host B, the Analyst | `backend/agents/hosts.py` | Supplies the arguments, evidence, and exact figures. |
| 4. Audio post-production | `backend/agents/audio.py` | Deterministic code: normalizes text for speech, chunks it, synthesizes, adds pauses, exports the mp3. |

The orchestrator in `backend/pipeline.py` runs the steps in order:

1. Make the plan.
2. Run the turn-taking loop, one LLM call per turn.
3. Check the duration and revise the plan if needed, at most `MAX_REVISIONS` times.
4. Run one coverage repair pass.
5. Make the audio and transcript.

Progress streams to the browser as Server-Sent Events over a `POST` request.

The runtime prompts from the design document (Part C) are stored verbatim in `backend/prompts.py`.

## File tree

```
podcastfactory/
├── CONSISTENCY_MATRIX.md        # Stage 1 contract matrix
├── README.md
├── backend/
│   ├── main.py                  # FastAPI app: POST /api/generate-podcast, GET /api/transcript/{id}, /audio
│   ├── pipeline.py              # orchestrator (turn loop, revisions, coverage repair)
│   ├── contracts.py             # single source of truth for all shared shapes
│   ├── config.py                # settings, all overridable by env
│   ├── llm.py                   # call_structured(): the only LLM entry point
│   ├── parsing.py               # PDF / DOCX / PPTX / TXT / MD to text, chunking
│   ├── prompts.py               # runtime agent prompts (Part C), verbatim
│   ├── transcript.py            # Markdown transcript writer
│   ├── textutil.py              # word counting
│   ├── agents/
│   │   ├── producer.py          # Agent 1
│   │   ├── hosts.py             # Agents 2 and 3
│   │   └── audio.py             # Agent 4 + TTSProvider / StubTTSProvider
│   ├── tests/
│   │   ├── test_contracts.py    # Python <-> TypeScript contract test
│   │   ├── test_pipeline.py     # end-to-end with a fake LLM
│   │   └── test_llm.py          # JSON validation / retry logic with a stubbed SDK
│   ├── requirements.txt
│   ├── pytest.ini
│   └── .env.example
└── frontend/
    ├── app/page.tsx             # the single-page dashboard
    ├── app/layout.tsx, app/globals.css
    ├── lib/contracts.ts         # TypeScript mirror of contracts.py
    ├── package.json, tsconfig.json, tailwind.config.ts, postcss.config.mjs, next.config.mjs
    └── .env.local.example
```

## Prerequisites

- Python 3.11 or newer
- Node.js 20 or newer
- **ffmpeg**, which pydub needs to export mp3:
  - macOS: `brew install ffmpeg`
  - Ubuntu/Debian: `sudo apt-get install -y ffmpeg`
  - Windows: `winget install ffmpeg`, or download it from ffmpeg.org and add it to `PATH`
- An Anthropic API key

## Run the backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # then set ANTHROPIC_API_KEY
uvicorn main:app --reload --port 8000
```

Check that it is up: `curl http://localhost:8000/api/health`

## Run the frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local # NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
npm run dev
```

Open http://localhost:3000.

## Run the tests

```bash
cd backend
pytest                              # everything (no API key or network needed)
pytest tests/test_contracts.py      # just the contract test
```

`test_contracts.py` parses `frontend/lib/contracts.ts`. It asserts that these match `contracts.py`
exactly:

- status strings, in order
- `StreamEvent` field names, and the fields of every shared interface
- the role, host, priority, and kind literals
- the progress map

It also proves that a one-sided change on either side fails. `test_pipeline.py` runs the whole
pipeline through the real HTTP endpoint with a fake LLM. It covers:

- the status sequence and monotonic progress
- the revision cap
- coverage repair and missing-point reporting
- bad-input and failure paths (every stream ends in `complete` or `error`)
- DOCX and PPTX parsing
- speech normalization and chunking

## API

`POST /api/generate-podcast` (multipart/form-data)

| Field | Required | Notes |
|---|---|---|
| `file` | one of file/text | `.pdf` `.docx` `.pptx` `.txt` `.md`, up to 25 MB |
| `text` | one of file/text | Pasted reading |
| `topic_focus` | no | Shifts emphasis; never drops must-cover material |
| `target_minutes` | no | 5-30, default 10 |
| `course_name` | no | Overrides `COURSE_NAME` for the Charter |

The response is `text/event-stream`. Each event is a `data: <StreamEvent JSON>` line followed by a
blank line. Every stream ends with a `complete` or `error` event. During long LLM calls the server
sends `: keep-alive` comments every 15 s.

- `GET /api/transcript/{job_id}` returns the Markdown transcript. Add `?download=1` to download it as an attachment.
- `GET /audio/{job_id}.mp3` returns the episode audio.

## Configuration

All settings are in `backend/config.py` and can be overridden in `backend/.env` (see `.env.example`).

- **Pipeline:** `WORDS_PER_MINUTE=150`, `DURATION_TOLERANCE=0.10`, `MAX_REVISIONS=2`,
  `MAX_SOURCE_CHARS=120000`, `CHUNK_CHARS=30000`, `MAX_TURNS_PER_SECTION=30`,
  `INCLUDE_SOURCE_IN_HOST_CONTEXT=true`
- **LLM:** `ANTHROPIC_MODEL` (default `claude-opus-5-5`), `PRODUCER_EFFORT` (default `high`),
  `HOST_EFFORT` (default `medium`), `ANTHROPIC_FALLBACKS` (default `default`; server-side fallback
  if the model declines a request; set to `off` to disable)
- **Content:** `COURSE_NAME`, `HOST_A_NAME`, `HOST_B_NAME`, `INCLUDE_CLOSING_DISCLAIMER`
- **Audio:** `TTS_PROVIDER=stub`, `VOICE_MAP={"A": "...", "B": "..."}`

**Adding a real TTS vendor.** Subclass `TTSProvider` in `agents/audio.py`, implement
`synthesize(text, voice_id, delivery_instruction) -> AudioSegment`, and register it in `PROVIDERS`.
Then set `TTS_PROVIDER` and `VOICE_MAP`. The UI badge disappears automatically when the provider
isn't `stub`.

## Cost and speed

Each dialogue turn is one LLM call, so a 10-minute episode is roughly 30-45 calls and a 30-minute
episode is roughly 80-100. When the source is sent to the hosts, it goes in a cached content block.
Repeated turns over the same reading are then read from the prompt cache instead of billed as full
input. To trade quality for cost, lower `HOST_EFFORT`, or switch `ANTHROPIC_MODEL` to
`claude-sonnet-5-5`.

## Assumptions and choices made

1. **All three stages were delivered at once,** because the request was to build it, not to pause
   for review between stages.
2. **Team decisions left at neutral defaults:**
   - Provider: Anthropic.
   - Host names: unset, so the hosts are "Host A" and "Host B".
   - Closing disclaimer: on.
   - Voices: stub voice ids.
   - Course name: "Executive MBA program".
3. **`CoverageReport.missing_points`** (id → text) was added so the UI can list missed ideas in plain
   words. Both contract files include it.
4. **Speaker choice.**
   - Host A opens every section.
   - After that, hosts alternate: A frames or asks, B supplies substance, A replies with an
     implication, B refines.
   - A section can't close until both hosts have spoken.
   - The orchestrator decides the speaker, so neither host can be skipped. One host never speaks
     more than 3 turns in a row.
5. **Closing turns.** Every section ends with an explicit closing turn. In non-final sections it is a
   bridge to the next section's title. In the outro it closes the episode. Section end checks reserve
   about 35 words for that turn.
6. **Turn length.** A turn outside 15-90 words is regenerated up to 3 times. If it is still out of
   range, it is kept, and a warning is added to the coverage report instead of failing the episode.
7. **Plan arithmetic.** If Agent 1 still gets the word budgets wrong after 2 correction requests, the
   code rescales them proportionally. A revision can't drop a must-cover point; any dropped point is
   restored.
8. **Prompt placeholders** are filled with a small `{{name}}` regex substitution. The prompts contain
   literal JSON braces, which `str.format` would break on. Agent 1's "x 150" uses
   `{{words_per_minute}}` so it stays correct if the setting changes.
9. **Long sources.** Sources over `MAX_SOURCE_CHARS` are chunked for Agent 1 and are not sent to the
   hosts; the hosts rely on the key points. Shorter sources are sent to the hosts in a cached block
   at the top of each turn request, and the template's SOURCE REFERENCE slot points to it.
10. **Scanned (image-only) PDFs** are rejected with a clear message. There is no OCR.

## Before students hear an episode

Have a person check the episode against the source, using the test plan in the design document:

- coverage
- accuracy
- numbers and names
- length
- listenability
- usefulness

## License

[MIT](LICENSE)
