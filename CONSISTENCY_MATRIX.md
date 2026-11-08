# System Consistency Matrix

Every row is enforced in code: by Pydantic validation, by TypeScript types, or by
`backend/tests/test_contracts.py` (which parses `frontend/lib/contracts.ts` and fails if
either side changes alone). There is no self-declared "proof" line. The contract test is the proof.

| # | Contract item | Producer (file and function) | Consumer (file and function) | Exact fields or values | Aligned? |
|---|---|---|---|---|---|
| 1 | Agent 1 `ProductionPlan` → host prompt builders | `backend/agents/producer.py` `produce_plan()` / `revise_plan()` (validated as `contracts.ProductionPlan`, then `plan_problems()`) | `backend/agents/hosts.py` `build_user_prompt()`, `format_key_points()`, `role_note()`; `backend/pipeline.py` `Orchestrator.generate_section()` | Plan: `title`, `source_summary`, `target_minutes`, `total_word_budget`, `capacity_warning`, `sections`. Section: `id`, `order`, `role` (`intro`/`body`/`outro`), `title`, `pedagogical_goal`, `key_points`, `word_budget`. KeyPoint: `id`, `text`, `kind`, `priority`, `source_ref`. Template placeholders filled: `{{section.title}}`, `{{section.role}}`, `{{section.pedagogical_goal}}`, `{{section.word_budget}}`, `{{next_section.title}}`; per key point `[id] (kind, priority, covered/uncovered) text - source_ref` | Yes |
| 2 | `DialogueTurn` → Agent 4 (audio) and transcript writer | `backend/agents/hosts.py` `generate_turn()` (builds `contracts.DialogueTurn` from `HostTurnOutput` + orchestrator `section_id`) | `backend/agents/audio.py` `produce_audio()` (uses `host` → `VOICE_MAP`, `text` → `normalize_for_speech()` → `chunk_for_tts()`, `delivery_instruction` → `TTSProvider.synthesize()`); `backend/transcript.py` `render_transcript()` (uses `section_id`, `host`, `text`) | `section_id`, `host` (`A`/`B`), `text`, `delivery_instruction`, `covers` | Yes |
| 3 | `DialogueTurn.covers` → coverage checker | `backend/agents/hosts.py` `generate_turn()` (filters `covers` to ids that exist in the section) | `backend/pipeline.py` `Orchestrator.missing_must_cover()`, `repair_coverage()`, `coverage_report()` | `covers: list[str]` of KeyPoint ids `s{order}-k{n}`, compared to every KeyPoint with `priority == "must_cover"`. Output `CoverageReport`: `must_cover_total`, `must_cover_hit`, `missing_ids`, `missing_points`, `per_section_words`, `total_words`, `estimated_minutes`, `warnings` | Yes |
| 4 | `StreamEvent` → frontend stream parser | `backend/pipeline.py` `Emitter.__call__()`; `backend/main.py` `generate_podcast()` → `_sse()` (`data: <json>\n\n`) | `frontend/app/page.tsx` `generate()` → `parseEventBlock()` → `handleEvent()` | `status`, `message`, `progress` (int 0-100), `data`. On `complete`, `data` = `CompleteData`: `audio_url`, `transcript_url`, `coverage`, `tts_provider`. On `error`, `data` = `ErrorData`: `detail`. Keep-alive SSE comments (`: keep-alive`) are ignored by the parser | Yes |
| 5 | Status enum → button-label map and progress values | `backend/contracts.py` `Status`, `STATUS_VALUES`, `STATUS_PROGRESS`, `IN_PROGRESS_STATUSES` | `frontend/lib/contracts.ts` (mirror); `frontend/app/page.tsx` `BUTTON_LABELS` (keys destructured from `STATUS_VALUES`, checked with `satisfies Record<UiStatus, string>`), progress bar | Statuses in order: `extracting_facts`, `generating_script`, `revising_outline`, `synthesizing_audio`, `complete`, `error`. Progress floors: 5, 20, 75, 90, 100, 100. Progress is monotonic within a job | Yes |

## Status sequence

```
extracting_facts → generating_script → [revising_outline → generating_script] × 0..MAX_REVISIONS
                 → [generating_script (coverage repair)] × 0..1 → synthesizing_audio → complete
any step → error
```

## Design corrections made while building the matrix

- **Row 3:** the UI must list missing ideas "in plain words", but `missing_ids` alone are opaque.
  Added `CoverageReport.missing_points` (id → key point text) on both sides.
- **Row 1:** the C6 template's `{{for each key point ...}}` and `{{one of the following ...}}` are not
  fillable names. They are `{{key_points}}` and `{{instruction}}` in `prompts.py`, and the instruction
  options are the verbatim `INSTRUCTION_*` constants.
- **Row 2:** the host model's JSON has no `section_id` (C3: "the orchestrator fills in section_id").
  The LLM returns `HostTurnOutput`, and the orchestrator converts it to `DialogueTurn`. The orchestrator
  also sets `host`, so the model can't skip a speaker.
