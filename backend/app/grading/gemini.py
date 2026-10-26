from functools import lru_cache

import httpx
from google import genai
from google.genai import errors, types
from pydantic import ValidationError

from app import config
from app.grading.base import GradingFailed, GradingRequest, GradingResult
from app.grading.prompts import KYUSHU_MATH_TUTOR_V1
from app.services.storage import FilePart


class GeminiGrader:
    def __init__(self, api_key: str | None, model_name: str):
        self._client = genai.Client(api_key=api_key)
        self.model_name = model_name

    def grade(self, request: GradingRequest) -> GradingResult:
        contents: list[str | types.Part] = [KYUSHU_MATH_TUTOR_V1]
        if request.problem_text:
            contents.append(f"【問題文】\n{request.problem_text}")
        if request.problem_file:
            contents.append(_to_part(request.problem_file))
        contents.append(_to_part(request.answer))

        try:
            response = self._client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config={
                    "response_mime_type": "application/json",
                    "response_json_schema": GradingResult.model_json_schema(),
                },
            )
            return GradingResult.model_validate_json(response.text or "")
        except (errors.APIError, httpx.HTTPError, ValidationError) as exc:
            raise GradingFailed(f"{self.model_name}: {exc}") from exc


def _to_part(file: FilePart) -> types.Part:
    return types.Part.from_bytes(data=file.data, mime_type=file.mime_type)


@lru_cache
def get_grader() -> GeminiGrader:
    """FastAPI dependency; tests override it with a fake grader."""
    return GeminiGrader(api_key=config.GEMINI_API_KEY, model_name=config.GEMINI_MODEL)
