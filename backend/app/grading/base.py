from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, Field

from app.services.storage import FilePart


@dataclass(frozen=True)
class GradingRequest:
    answer: FilePart
    problem_text: str | None = None
    problem_file: FilePart | None = None
    # Language of the written feedback. "ja" sends the evaluated prompt unchanged.
    language: str = "ja"


class GradingResult(BaseModel):
    # Field descriptions are part of the prompt: Gemini receives them through the JSON schema.
    score: int = Field(ge=0, le=100, description="100点満点での採点結果(部分点考慮)")
    ai_feedback: str = Field(
        description="生徒への具体的なアドバイス。どこで間違えたか、どう考えればよかったのかを解説してください。数式を書く際は必ずLaTeX形式とし、インライン数式は $ で、ブロック数式は $$ で囲んでください。",
        # The model still sees maxLength in the schema, but a longer reply is accepted: rejecting
        # it would throw away a billed answer and pay for a retry.
        json_schema_extra={"maxLength": 500},
    )
    explanation: str = Field(
        description="問題の模範解答と、本番での解法戦略(部分点の狙い方など)の解説。数式を書く際は必ずLaTeX形式とし、インライン数式は $ で、ブロック数式は $$ で囲んでください。",
    )


class GradingFailed(Exception):
    """The model call failed. `retryable` is False when the failed call was probably billed
    (a timeout, or a reply that was cut off or malformed), so retrying would pay again."""

    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


class ModelUnavailable(GradingFailed):
    """The model API says the configured model does not exist (e.g. a preview model was shut
    down). Not retryable, and not billed."""

    def __init__(self, message: str):
        super().__init__(message, retryable=False)


class Grader(Protocol):
    """Anything that can grade a handwritten answer. Implementations are blocking;
    callers run them in a worker thread."""

    model_name: str

    def grade(self, request: GradingRequest) -> GradingResult: ...
