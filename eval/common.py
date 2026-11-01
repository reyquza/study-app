"""Paths, problem set, manifest and result files shared by the evaluation scripts.

Scripts run in the backend's environment so they use the production grading code:

    cd backend && uv run python ../eval/<script>.py
"""

import csv
import json
import re
import sys
import threading
from pathlib import Path

import yaml

EVAL_DIR = Path(__file__).resolve().parent
ROOT = EVAL_DIR.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.grading.rubric import Rubric, RubricStep  # noqa: E402
from app.services.storage import FilePart  # noqa: E402

# Everything under data/ is participants' material and stays out of git.
DATA_DIR = EVAL_DIR / "data"
ANSWERS_DIR = DATA_DIR / "answers"
MANIFEST = DATA_DIR / "manifest.csv"
LABELS_DIR = DATA_DIR / "labels"
RUNS_DIR = DATA_DIR / "runs"
# Aggregated, anonymous results; these are committed.
RESULTS_DIR = EVAL_DIR / "results"

MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".pdf": "application/pdf",
}

MANIFEST_FIELDS = ["answer_id", "problem_id", "source", "pages"]


def load_problems() -> list[dict]:
    with open(EVAL_DIR / "problems.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)["problems"]


def load_rubrics() -> dict[str, Rubric]:
    rubrics = {}
    for problem in load_problems():
        rubrics[problem["id"]] = Rubric(
            problem_id=problem["id"],
            # Both languages, as on the sheet participants saw.
            statement=problem["statement_ja"].strip() + "\n\n" + problem["statement_en"].strip(),
            answer_key=problem["answer"],
            steps=tuple(
                RubricStep(
                    id=step["id"],
                    points=step["points"],
                    criterion=step["criterion"],
                    requires=tuple(step.get("requires", ())),
                )
                for step in problem["rubric"]
            ),
            alternatives=tuple(problem.get("alternatives", ())),
            caps=tuple(problem.get("caps", ())),
        )
    return rubrics


def step_part(step: RubricStep) -> str:
    """The sub-question a step belongs to, from its criterion's "(1)" prefix."""
    match = re.match(r"\((\d)\)", step.criterion)
    return f"({match.group(1)})" if match else "(all)"


def read_manifest() -> list[dict]:
    with open(MANIFEST, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_manifest(rows: list[dict]) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def answer_pages(answer_id: str) -> list[FilePart]:
    paths = sorted((ANSWERS_DIR / answer_id).glob("page-*"))
    return [FilePart(data=p.read_bytes(), mime_type=MIME_TYPES[p.suffix.lower()]) for p in paths]


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


_append_lock = threading.Lock()


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _append_lock, open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
