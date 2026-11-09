from functools import lru_cache

import httpx
from google import genai
from google.genai import errors, types
from pydantic import ValidationError

from app import config
from app.grading.base import GradingFailed, GradingRequest, GradingResult, ModelUnavailable
from app.grading.prompts import ENGLISH_FEEDBACK, KYUSHU_MATH_TUTOR_V1
from app.services.storage import FilePart


class GeminiGrader:
    def __init__(self, api_key: str | None, model_name: str):
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=int(config.GRADING_TIMEOUT.total_seconds() * 1000)),
        )
        self.model_name = model_name

    def grade(self, request: GradingRequest) -> GradingResult:
        prompt = KYUSHU_MATH_TUTOR_V1 + (ENGLISH_FEEDBACK if request.language == "en" else "")
        contents: list[str | types.Part] = [prompt]
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
                    "max_output_tokens": config.GRADING_MAX_OUTPUT_TOKENS,
                },
            )
            return GradingResult.model_validate_json(response.text or "")
        except (errors.APIError, httpx.HTTPError, ValidationError) as exc:
            raise as_grading_failure(self.model_name, exc) from exc

    def model_exists(self) -> bool | None:
        """Ask the API whether the model still exists. This reads model metadata and generates
        nothing, so it is not billed. None means the check itself failed (e.g. no network)."""
        try:
            self._client.models.get(model=self.model_name)
            return True
        except errors.APIError as exc:
            return False if exc.code == 404 else None
        except httpx.HTTPError:
            return None


# Rejected before generating anything (rate limit, overload), so a retry costs nothing extra.
_UNBILLED_STATUS_CODES = {429, 500, 503}


def as_grading_failure(model_name: str, exc: errors.APIError | httpx.HTTPError | ValidationError) -> GradingFailed:
    """Map a model-call error to GradingFailed, marking whether a retry is worth paying for."""
    if isinstance(exc, errors.APIError) and exc.code == 404:
        return ModelUnavailable(f"{model_name}: {exc}")
    if isinstance(exc, errors.APIError):
        retryable = exc.code in _UNBILLED_STATUS_CODES
    elif isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        retryable = True  # the request never reached the model
    else:
        # A read timeout may still be generating (and billed); a malformed or truncated reply was billed.
        retryable = False
    return GradingFailed(f"{model_name}: {exc}", retryable=retryable)


def _to_part(file: FilePart) -> types.Part:
    return types.Part.from_bytes(data=file.data, mime_type=file.mime_type)


@lru_cache
def get_grader() -> GeminiGrader:
    """FastAPI dependency; tests override it with a fake grader."""
    return GeminiGrader(api_key=config.GEMINI_API_KEY, model_name=config.GEMINI_MODEL)
