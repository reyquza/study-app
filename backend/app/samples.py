"""The public sample grading result shown at /sample.

It is a real output of the production grader on the author's own answer, stored as a file so
the page needs no database, no upload and no model call: it keeps working when grading does
not (daily cap reached, model shut down, model budget used up). Until the file exists, the
page and the links to it are hidden.
"""

import json
from pathlib import Path

SAMPLE_FILE = Path(__file__).resolve().parent / "web" / "samples" / "sample.json"

# Keys sample.json must have; see docs for how the file is produced.
REQUIRED_KEYS = {"title", "note", "problem_text", "answer_image", "score", "ai_feedback", "explanation", "model", "graded_at"}


def is_available() -> bool:
    return SAMPLE_FILE.is_file()


def load() -> dict | None:
    if not SAMPLE_FILE.is_file():
        return None
    sample = json.loads(SAMPLE_FILE.read_text(encoding="utf-8"))
    missing = REQUIRED_KEYS - sample.keys()
    if missing:
        raise ValueError(f"{SAMPLE_FILE.name} is missing {sorted(missing)}")
    return sample
