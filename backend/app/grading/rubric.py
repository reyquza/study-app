"""Rubric-based grading: the model judges each rubric step, and code computes the score.

Splitting the work this way makes every point traceable to a step and a reason, keeps
arithmetic out of the model, and lets evaluation compare graders step by step.
"""

import time
from dataclasses import dataclass, field

import httpx
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field, ValidationError

from app import config
from app.grading.base import GradingFailed
from app.grading.prompts import RUBRIC_GRADER_V1, TRANSCRIBE_FIRST
from app.services.storage import FilePart


@dataclass(frozen=True)
class RubricStep:
    id: str
    points: int
    criterion: str
    # Steps that must earn full points for this one to count (e.g. a final combination).
    requires: tuple[str, ...] = ()


@dataclass(frozen=True)
class Rubric:
    problem_id: str
    statement: str
    answer_key: str
    steps: tuple[RubricStep, ...]
    alternatives: tuple[str, ...] = ()
    caps: tuple[str, ...] = ()

    @property
    def max_points(self) -> int:
        return sum(step.points for step in self.steps)


class StepJudgment(BaseModel):
    step_id: str
    awarded: int = Field(ge=0, description="Points awarded for this step, from 0 to its maximum.")
    reason: str = Field(description="One sentence pointing to what the student wrote.")


class RubricJudgment(BaseModel):
    steps: list[StepJudgment]


class TranscribedRubricJudgment(BaseModel):
    # Declared first so the model writes the transcription before it judges.
    transcription: str = Field(description="The student's answer, transcribed in full, with LaTeX for math.")
    steps: list[StepJudgment]


@dataclass
class RubricGrade:
    total: int
    awarded: dict[str, int]
    reasons: dict[str, str]
    transcription: str | None = None
    # Step ids the model left out; they score 0. Non-empty means the output was incomplete.
    missing_steps: list[str] = field(default_factory=list)
    latency_seconds: float = 0.0
    usage: dict[str, int] = field(default_factory=dict)


def score(rubric: Rubric, judgments: list[StepJudgment]) -> tuple[dict[str, int], dict[str, str], list[str]]:
    """Turn the model's step judgments into points: clamp to each step's maximum, apply
    `requires`, and score missing steps as 0."""
    by_id = {judgment.step_id: judgment for judgment in judgments}
    awarded: dict[str, int] = {}
    reasons: dict[str, str] = {}
    missing: list[str] = []
    for step in rubric.steps:
        judgment = by_id.get(step.id)
        if judgment is None:
            missing.append(step.id)
            awarded[step.id] = 0
            continue
        awarded[step.id] = min(max(judgment.awarded, 0), step.points)
        reasons[step.id] = judgment.reason

    points = {step.id: step.points for step in rubric.steps}
    for step in rubric.steps:
        if any(awarded.get(required, 0) < points.get(required, 0) for required in step.requires):
            awarded[step.id] = 0
    return awarded, reasons, missing


def build_prompt(rubric: Rubric, transcribe: bool) -> str:
    def bullets(items) -> str:
        return "\n".join(f"- {item}" for item in items) or "- (none)"

    steps = "\n".join(
        f"- `{step.id}` ({step.points} pt): {step.criterion}"
        + (f" Counts only if {', '.join(step.requires)} earn full points." if step.requires else "")
        for step in rubric.steps
    )
    prompt = RUBRIC_GRADER_V1.format(
        problem=rubric.statement.strip(),
        answer_key=rubric.answer_key,
        rubric=steps,
        alternatives=bullets(rubric.alternatives),
        caps=bullets(rubric.caps),
    )
    return prompt + (TRANSCRIBE_FIRST if transcribe else "")


class GeminiRubricGrader:
    """v2 grades straight from the image; v3 (`transcribe=True`) transcribes first."""

    def __init__(self, api_key: str | None, model_name: str, transcribe: bool):
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=int(config.GRADING_TIMEOUT.total_seconds() * 1000)),
        )
        self.model_name = model_name
        self.transcribe = transcribe
        self._schema = TranscribedRubricJudgment if transcribe else RubricJudgment

    def grade(self, rubric: Rubric, pages: list[FilePart]) -> RubricGrade:
        """Grade an answer given as one or more pages (photos or a PDF)."""
        started = time.monotonic()
        try:
            response = self._client.models.generate_content(
                model=self.model_name,
                contents=[
                    build_prompt(rubric, self.transcribe),
                    *(types.Part.from_bytes(data=page.data, mime_type=page.mime_type) for page in pages),
                ],
                config={
                    "response_mime_type": "application/json",
                    "response_json_schema": self._schema.model_json_schema(),
                },
            )
            judgment = self._schema.model_validate_json(response.text or "")
        except (errors.APIError, httpx.HTTPError, ValidationError) as exc:
            raise GradingFailed(f"{self.model_name}: {exc}") from exc

        awarded, reasons, missing = score(rubric, judgment.steps)
        return RubricGrade(
            total=sum(awarded.values()),
            awarded=awarded,
            reasons=reasons,
            transcription=getattr(judgment, "transcription", None),
            missing_steps=missing,
            latency_seconds=time.monotonic() - started,
            usage=_usage(response),
        )


def _usage(response) -> dict[str, int]:
    metadata = getattr(response, "usage_metadata", None)
    if metadata is None:
        return {}
    fields = {
        "input_tokens": "prompt_token_count",
        "output_tokens": "candidates_token_count",
        "thinking_tokens": "thoughts_token_count",
    }
    return {name: getattr(metadata, attr) or 0 for name, attr in fields.items()}
