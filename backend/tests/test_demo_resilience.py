"""What visitors see when grading cannot run: the model was shut down, or they open the
public sample result instead of grading their own answer."""

import json

import pytest
from google.genai import errors

from app import samples
from app.grading import availability
from app.grading.base import GradingRequest, GradingResult, ModelUnavailable
from app.grading.gemini import GeminiGrader, as_grading_failure
from app.models import GradingJob, JobStatus
from tests.factories import ANSWER_FILE, make_job, make_problem

SAMPLE = {
    "title": "放物線と接線",
    "note": "見本用の答案です。",
    "problem_text": "放物線 $y=x^2$ について…",
    "answer_image": "/static/samples/answer.jpg",
    "score": 85,
    "ai_feedback": "最小値の確認が抜けています。",
    "explanation": "模範解答…",
    "model": "test-model",
    "graded_at": "2026-11-02",
}


@pytest.fixture(autouse=True)
def model_available():
    availability.mark_available()
    yield
    availability.mark_available()


@pytest.fixture
def sample_file(tmp_path, monkeypatch):
    path = tmp_path / "sample.json"
    path.write_text(json.dumps(SAMPLE, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(samples, "SAMPLE_FILE", path)
    return path


@pytest.fixture
def no_sample(tmp_path, monkeypatch):
    monkeypatch.setattr(samples, "SAMPLE_FILE", tmp_path / "missing.json")


# --- the model is shut down -------------------------------------------------------------

def _not_found() -> errors.ClientError:
    return errors.ClientError(404, {"error": {"message": "models/x is not found", "status": "NOT_FOUND"}})


def test_404_from_the_model_api_means_the_model_is_gone():
    failure = as_grading_failure("gemini-x", _not_found())

    assert isinstance(failure, ModelUnavailable)
    assert failure.retryable is False


class GoneGrader:
    model_name = "gemini-x"

    def grade(self, request: GradingRequest) -> GradingResult:
        raise ModelUnavailable("gemini-x: 404 NOT_FOUND")


def test_a_shutdown_fails_the_job_once_and_stops_new_submissions(client, db, users, session_factory, no_sample):
    import asyncio

    from app.worker import GradingWorker

    problem = make_problem(db, users["alice"].id)
    job_id = client.post(f"/api/problems/{problem.id}/grading-jobs", files=ANSWER_FILE).json()["id"]

    assert asyncio.run(GradingWorker(session_factory, GoneGrader(), concurrency=1).run_once())

    db.expire_all()
    job = db.get(GradingJob, job_id)
    assert (job.status, job.attempts) == (JobStatus.FAILED, 1)
    assert client.get(f"/api/grading-jobs/{job_id}").json()["failure_reason"] == "model_unavailable"
    assert availability.is_unavailable()

    page = client.get(f"/grading/{job_id}").text
    assert "AIモデルが使えなくなりました" in page
    assert "提供がGoogleによって終了" in page

    # The banner is on every page, the submit button is disabled, and nothing is uploaded.
    assert "AI採点は現在停止中です" in client.get("/").text
    assert "disabled" in client.get(f"/solve/{problem.id}").text
    web = client.post(f"/submit-answer/{problem.id}", files=ANSWER_FILE, follow_redirects=False)
    api = client.post(f"/api/problems/{problem.id}/grading-jobs", files=ANSWER_FILE)
    assert web.headers["location"] == "/?error=model_unavailable"
    assert api.status_code == 503
    assert db.query(GradingJob).count() == 1


def test_ordinary_failures_keep_the_retry_message_and_offer_the_sample(client, db, users, grader, run_jobs, sample_file):
    grader.failures, grader.retryable = 1, False
    problem = make_problem(db, users["alice"].id)
    job_id = client.post(f"/api/problems/{problem.id}/grading-jobs", files=ANSWER_FILE).json()["id"]
    run_jobs()

    assert client.get(f"/api/grading-jobs/{job_id}").json()["failure_reason"] == "error"
    page = client.get(f"/grading/{job_id}").text
    assert "採点できませんでした" in page
    assert 'id="grading-sample" class="grading-note">' in page  # shown, not hidden
    assert not availability.is_unavailable()


def test_limit_messages_offer_the_sample(client, sample_file):
    page = client.get("/?error=limit_reached").text

    assert "上限" in page and "/sample" in page


def test_a_successful_grading_clears_the_shutdown_notice(client, db, users, run_jobs):
    # e.g. the operator switched GEMINI_MODEL to a model that exists
    availability.mark_unavailable()
    make_job(db, make_problem(db, users["alice"].id))

    assert run_jobs() == 1
    assert not availability.is_unavailable()


@pytest.mark.parametrize("raised, expected", [
    (None, True),
    (_not_found(), False),
    (errors.ServerError(503, {"error": {"message": "busy", "status": "UNAVAILABLE"}}), None),
])
def test_model_exists_check(raised, expected):
    class FakeModels:
        def get(self, model):
            if raised is not None:
                raise raised
            return object()

    grader = GeminiGrader(api_key="test-key", model_name="test-model")
    grader._client = type("Client", (), {"models": FakeModels()})()

    assert grader.model_exists() is expected


def test_watcher_marks_the_model_unavailable(session_factory):
    import asyncio

    from app.worker import GradingWorker

    class Grader(GoneGrader):
        def model_exists(self):
            return False

    async def run():
        stop = asyncio.Event()
        task = asyncio.create_task(GradingWorker(session_factory, Grader(), concurrency=1)._watch_model(stop))
        await asyncio.sleep(0.05)
        stop.set()
        await task

    asyncio.run(run())
    assert availability.is_unavailable()


# --- the public sample ------------------------------------------------------------------

def test_sample_page_is_public_and_linked_from_login(anonymous_client, sample_file):
    page = anonymous_client.get("/sample")

    assert page.status_code == 200
    assert "85" in page.text and "最小値の確認が抜けています" in page.text
    assert "/sample" in anonymous_client.get("/login").text


def test_without_a_sample_file_the_page_and_links_are_hidden(anonymous_client, no_sample):
    assert anonymous_client.get("/sample", follow_redirects=False).status_code == 303
    assert "/sample" not in anonymous_client.get("/login").text


def test_sample_file_must_be_complete(sample_file):
    sample_file.write_text(json.dumps({"title": "x"}), encoding="utf-8")

    with pytest.raises(ValueError):
        samples.load()

