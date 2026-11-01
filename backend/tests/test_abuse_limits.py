"""Limits that keep the model bill, file storage and the server's memory bounded when anyone
can create an account."""

import io
from datetime import timedelta

import httpx
import pytest
from google.genai import errors
from pydantic import ValidationError
from pypdf import PdfWriter
from sqlalchemy import func, select

from app import config
from app.grading.base import GradingRequest, GradingResult
from app.grading.gemini import GeminiGrader, as_grading_failure
from app.models import GradingJob, JobStatus, Problem, User
from app.services.storage import FilePart
from tests.conftest import login_as
from tests.factories import ANSWER_FILE, make_problem, make_submission


def _pdf(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _register(client, file=("problem.jpg", b"problem-image", "image/jpeg"), **fields):
    return client.post("/register-problem", files={"image": file}, data=fields, follow_redirects=False)


def _problem_count(db) -> int:
    db.expire_all()
    return db.scalar(select(func.count()).select_from(Problem))


# --- accounts ---------------------------------------------------------------------------

def _login(client, email, password):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_login_locks_after_repeated_failures_even_with_the_right_password(anonymous_client, monkeypatch, db):
    monkeypatch.setattr(config, "LOGIN_FAILURE_LIMIT", (3, timedelta(minutes=15)))
    anonymous_client.post("/api/auth/signup", json={"email": "carol@example.com", "password": "correct-horse"})
    anonymous_client.post("/api/auth/signup", json={"email": "dave@example.com", "password": "dave-password"})
    anonymous_client.cookies.clear()

    codes = [_login(anonymous_client, "carol@example.com", "wrong-password").status_code for _ in range(3)]

    assert codes == [401, 401, 401]
    assert _login(anonymous_client, "Carol@example.com", "correct-horse").status_code == 429
    # The lock is stored on the account, so a restart does not lift it.
    db.expire_all()
    assert db.scalar(select(User).where(User.email == "carol@example.com")).locked_until is not None
    # Other accounts are not affected.
    assert _login(anonymous_client, "dave@example.com", "dave-password").status_code == 200


def test_lock_expires_and_a_successful_login_resets_the_count(anonymous_client, monkeypatch, db):
    monkeypatch.setattr(config, "LOGIN_FAILURE_LIMIT", (2, timedelta(minutes=15)))
    anonymous_client.post("/api/auth/signup", json={"email": "carol@example.com", "password": "correct-horse"})
    anonymous_client.cookies.clear()
    _login(anonymous_client, "carol@example.com", "wrong-password")
    _login(anonymous_client, "carol@example.com", "wrong-password")
    assert _login(anonymous_client, "carol@example.com", "correct-horse").status_code == 429

    carol = db.scalar(select(User).where(User.email == "carol@example.com"))
    carol.locked_until = carol.locked_until - timedelta(minutes=16)
    db.commit()

    assert _login(anonymous_client, "carol@example.com", "correct-horse").status_code == 200
    _login(anonymous_client, "carol@example.com", "wrong-password")
    db.expire_all()
    assert (carol.failed_logins, carol.locked_until) == (1, None)


def test_guest_creation_is_rate_limited(anonymous_client, monkeypatch, db):
    monkeypatch.setattr(config, "NEW_GUEST_LIMIT", (2, timedelta(minutes=1)))

    codes = [anonymous_client.post("/api/auth/guest").status_code for _ in range(3)]

    assert codes == [201, 201, 429]
    assert db.scalar(select(func.count()).select_from(User)) == 2


def test_guest_creation_counts_only_recent_guests(anonymous_client, monkeypatch, db):
    monkeypatch.setattr(config, "NEW_GUEST_LIMIT", (1, timedelta(minutes=1)))
    old = User(is_guest=True)
    db.add(old)
    db.commit()
    old.created_at = old.created_at - timedelta(minutes=2)
    db.commit()

    assert anonymous_client.post("/api/auth/guest").status_code == 201


def test_sign_up_is_rate_limited(anonymous_client, monkeypatch):
    monkeypatch.setattr(config, "NEW_ACCOUNT_LIMIT", (1, timedelta(minutes=1)))

    first = anonymous_client.post("/api/auth/signup", json={"email": "a@example.com", "password": "password-1"})
    anonymous_client.cookies.clear()
    second = anonymous_client.post("/api/auth/signup", json={"email": "b@example.com", "password": "password-2"})

    assert (first.status_code, second.status_code) == (201, 429)


# --- uploads and text -------------------------------------------------------------------

def test_pdf_over_the_page_limit_is_rejected_before_upload(client, db, stored_files):
    too_long = _register(client, file=("p.pdf", _pdf(config.MAX_PDF_PAGES + 1), "application/pdf"))
    broken = _register(client, file=("p.pdf", b"%PDF-1.4 not really", "application/pdf"))
    ok = _register(client, file=("p.pdf", _pdf(2), "application/pdf"))

    assert (too_long.status_code, broken.status_code, ok.status_code) == (400, 400, 303)
    assert len(stored_files) == 1
    assert _problem_count(db) == 1


def test_answer_pdf_over_the_page_limit_is_not_queued(client, db, users):
    problem = make_problem(db, users["alice"].id)
    pdf = {"answer_image": ("a.pdf", _pdf(config.MAX_PDF_PAGES + 1), "application/pdf")}

    response = client.post(f"/api/problems/{problem.id}/grading-jobs", files=pdf)

    assert response.status_code == 400
    assert db.scalar(select(GradingJob.id)) is None


@pytest.mark.parametrize("fields", [
    {"text": "x" * (config.MAX_PROBLEM_TEXT_CHARS + 1)},
    {"source": "x" * (config.MAX_SOURCE_CHARS + 1)},
    {"tags": ",".join(f"t{i}" for i in range(config.MAX_TAGS + 1))},
    {"tags": "x" * (config.MAX_TAG_CHARS + 1)},
])
def test_overlong_problem_fields_are_rejected(client, db, stored_files, fields):
    assert _register(client, **fields).status_code == 400
    assert not stored_files
    assert _problem_count(db) == 0


def test_overlong_memo_is_rejected(client, db, users):
    submission = make_submission(db, make_problem(db, users["alice"].id))

    response = client.post(
        f"/update-memo/{submission.id}",
        data={"mistake": "x" * (config.MAX_MEMO_CHARS + 1), "user_memo": ""},
        follow_redirects=False,
    )

    assert response.status_code == 400


def test_daily_problem_limit_per_user(client, db, monkeypatch, stored_files):
    monkeypatch.setattr(config, "DAILY_PROBLEM_LIMIT", 1)

    assert _register(client).headers["location"] == "/"
    assert _register(client).headers["location"] == "/?error=problem_limit"
    assert "問題登録の上限" in client.get("/?error=problem_limit").text
    assert len(stored_files) == 1


def test_daily_problem_limit_is_shared_by_guests(anonymous_client, db, monkeypatch, stored_files):
    monkeypatch.setattr(config, "GUEST_DAILY_PROBLEM_LIMIT", 1)
    db.add_all([
        User(id="guest-a-uid", is_guest=True),
        User(id="guest-b-uid", is_guest=True),
        User(id="alice-uid", email="alice@example.com"),
    ])
    db.commit()

    login_as(anonymous_client, "guest-a")
    assert _register(anonymous_client).headers["location"] == "/"
    login_as(anonymous_client, "guest-b")
    assert _register(anonymous_client).headers["location"] == "/?error=problem_limit"
    login_as(anonymous_client, "alice")
    assert _register(anonymous_client).headers["location"] == "/"


def test_back_redirect_refuses_backslash_hosts(client):
    response = client.post(
        "/delete-task/999",
        headers={"referer": "https://nextlearn.example/\\evil.example"},
        follow_redirects=False,
    )

    assert response.headers["location"] == "/"


# --- model calls ------------------------------------------------------------------------

def test_billed_failures_are_not_retried(client, db, users, grader, run_jobs):
    grader.failures, grader.retryable = 1, False
    problem = make_problem(db, users["alice"].id)
    job_id = client.post(f"/api/problems/{problem.id}/grading-jobs", files=ANSWER_FILE).json()["id"]

    assert run_jobs() == 1
    db.expire_all()
    job = db.get(GradingJob, job_id)
    assert (job.status, job.attempts) == (JobStatus.FAILED, 1)
    assert len(grader.requests) == 1


def _validation_error() -> ValidationError:
    try:
        GradingResult.model_validate_json("{")
    except ValidationError as exc:
        return exc
    raise AssertionError("expected a validation error")


@pytest.mark.parametrize("exc, retryable", [
    (errors.ClientError(429, {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED"}}), True),
    (errors.ServerError(503, {"error": {"message": "overloaded", "status": "UNAVAILABLE"}}), True),
    (errors.ClientError(400, {"error": {"message": "bad", "status": "INVALID_ARGUMENT"}}), False),
    (httpx.ConnectError("refused"), True),
    (httpx.ReadTimeout("slow"), False),
    (_validation_error(), False),
])
def test_only_unbilled_model_errors_are_retryable(exc, retryable):
    assert as_grading_failure("model", exc).retryable is retryable


def test_long_feedback_is_kept_and_the_model_still_sees_the_length_hint():
    result = GradingResult(score=50, ai_feedback="あ" * 800, explanation="e")

    assert len(result.ai_feedback) == 800
    assert GradingResult.model_json_schema()["properties"]["ai_feedback"]["maxLength"] == 500


def test_grader_caps_output_tokens():
    seen = {}

    class FakeModels:
        def generate_content(self, model, contents, config):
            seen.update(config)
            return type("Response", (), {"text": '{"score": 80, "ai_feedback": "f", "explanation": "e"}'})()

    grader = GeminiGrader(api_key="test-key", model_name="test-model")
    grader._client = type("Client", (), {"models": FakeModels()})()

    result = grader.grade(GradingRequest(answer=FilePart(data=b"x", mime_type="image/jpeg"), problem_text="p"))

    assert result.score == 80
    assert seen["max_output_tokens"] == config.GRADING_MAX_OUTPUT_TOKENS
