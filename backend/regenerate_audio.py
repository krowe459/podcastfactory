"""Re-voice an existing episode without calling the LLM again.

    python regenerate_audio.py <job_id> [<job_id> ...]

Reads output/<job_id>.script.json when present, otherwise parses the host lines
out of output/<job_id>.md (older jobs). Overwrites output/<job_id>.mp3 using the
configured TTS_PROVIDER, and drops the "silent placeholder" note from the transcript.
"""

from __future__ import annotations

import json
import re
import sys

from agents import audio
from config import settings
from contracts import DialogueTurn

_HOST_LINE = re.compile(r"^Host (?P<host>[AB])(?: \([^)]*\))?: (?P<text>.+)$")
_DEFAULT_DELIVERY = "Conversational podcast host: warm, clear, natural pace."


def load_turns(job_id: str) -> list[DialogueTurn]:
    script = settings.OUTPUT_DIR / f"{job_id}.script.json"
    if script.exists():
        return [DialogueTurn.model_validate(t) for t in json.loads(script.read_text("utf-8"))]
    md = settings.OUTPUT_DIR / f"{job_id}.md"
    turns, section = [], "s0"
    for line in md.read_text("utf-8").splitlines():
        if line.startswith("## "):
            section = line[3:]
        m = _HOST_LINE.match(line)
        if m:
            turns.append(
                DialogueTurn(
                    section_id=section, host=m["host"], text=m["text"],
                    delivery_instruction=_DEFAULT_DELIVERY,
                )
            )
    if not turns:
        raise SystemExit(f"No host lines found in {md}")
    return turns


def main(job_ids: list[str]) -> None:
    provider = audio.get_provider()
    for job_id in job_ids:
        turns = load_turns(job_id)
        path = audio.produce_audio(turns, job_id, provider)
        md = settings.OUTPUT_DIR / f"{job_id}.md"
        if md.exists() and provider.name != "stub":
            md.write_text(
                md.read_text("utf-8").replace("**Audio:** prototype, silent placeholder voices\n", ""),
                encoding="utf-8",
            )
        print(f"{job_id}: {len(turns)} turns -> {path}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    main(sys.argv[1:])
