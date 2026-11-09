from datetime import timedelta

import pytest
from sqlalchemy import select

from app import clock, config
from app.models import GradingJob, JobStatus, StudyPlan, Submission, User
from app.services import grading_jobs
from tests.conftest import login_as
from tests.factories import ANSWER_FILE, make_job, make_problem, make_submission


def _plans(db, problem_id):
    db.expire_all()
    return list(
        db.scalars(
            select(StudyPlan).where(StudyPlan.problem_id == problem_id).order_by(StudyPlan.scheduled_date)
        )
    )


def _job(db, job_id) -> GradingJob:
    db.expire_all()
    return db.get(GradingJob, job_id)


def _submit(client, problem) -> int:
    response = client.post(f"/submit-answer/{problem.id}", files=ANSWER_FILE, follow_redirects=False)
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/grading/")
    return int(location.removeprefix("/grading/"))


@pytest.fixture
def no_backoff(monkeypatch):
    monkeypatch.setattr(grading_jobs, "RETRY_BACKOFF", (timedelta(0),))


def test_submit_returns_before_grading(client, db, users, grader):
    problem = make_problem(db, users["alice"].id)

    job_id = _submit(client, problem)

    assert grader.requests == []
    assert _job(db, job_id).status == JobStatus.QUEUED
    waiting_page = client.get(f"/grading/{job_id}")
    assert waiting_page.status_code == 200
    assert f"/api/grading-jobs/{job_id}" in waiting_page.text


def test_worker_grades_job_and_waiting_page_moves_to_result(client, db, users, grader, run_jobs):
    # Also a regression test: the result page used StudyPlan.one() and crashed when a
    # problem had no plan.
    problem = make_problem(db, users["alice"].id)
    job_id = _submit(client, problem)

    assert run_jobs() == 1

    job = _job(db, job_id)
    assert (job.status, job.attempts, job.grader_model) == (JobStatus.SUCCEEDED, 1, "fake-grader")
    assert grader.requests[0].problem_text == problem.problem_text
    assert grader.requests[0].answer.data == b"handwritten-answer"

    response = client.get(f"/grading/{job_id}", follow_redirects=False)
    assert response.headers["location"] == f"/answer/{job.submission_id}"
    assert "70<span" in client.get(response.headers["location"]).text  # the score circle


def test_api_submit_and_poll(client, db, users, run_jobs):
    problem = make_problem(db, users["alice"].id)

    response = client.post(f"/api/problems/{problem.id}/grading-jobs", files=ANSWER_FILE)

    assert response.status_code == 202
    job = response.json()
    assert (job["status"], job["submission_id"]) == ("queued", None)

    run_jobs()
    polled = client.get(f"/api/grading-jobs/{job['id']}").json()
    assert polled["status"] == "succeeded"
    assert client.get(f"/answer/{polled['submission_id']}").status_code == 200


def test_grading_completes_plans_due_by_today_only(client, db, users, run_jobs):
    today = clock.local_today()
    problem = make_problem(
        db,
        users["alice"].id,
        plan_days=[today - timedelta(days=1), today, today + timedelta(days=5)],
    )

    _submit(client, problem)
    assert [plan.is_completed for plan in _plans(db, problem.id)] == [False, False, False]
    run_jobs()

    assert [plan.is_completed for plan in _plans(db, problem.id)] == [True, True, False]


def test_grading_ahead_of_schedule_completes_nearest_plan(client, db, users, run_jobs):
    today = clock.local_today()
    problem = make_problem(
        db,
        users["alice"].id,
        plan_days=[today + timedelta(days=2), today + timedelta(days=9)],
    )

    _submit(client, problem)
    run_jobs()

    assert [plan.is_completed for plan in _plans(db, problem.id)] == [True, False]


def test_viewing_result_does_not_change_plans(client, db, users):
    today = clock.local_today()
    problem = make_problem(db, users["alice"].id, plan_days=[today])
    submission = make_submission(db, problem)

    client.get(f"/answer/{submission.id}")

    assert _plans(db, problem.id)[0].is_completed is False


def test_problem_image_is_sent_to_grader(client, db, users, grader, run_jobs):
    problem = make_problem(db, users["alice"].id, text=None, image_url="https://res.cloudinary.com/demo/p.jpg")

    _submit(client, problem)
    run_jobs()

    assert grader.requests[0].problem_file.data == b"problem-image"


def test_heic_sent_as_octet_stream_is_accepted(client, db, users, stored_files):
    # Regression: iPhones can label HEIC as application/octet-stream.
    problem = make_problem(db, users["alice"].id)
    heic = {"answer_image": ("IMG_0001.HEIC", b"heic-bytes", "application/octet-stream")}

    response = client.post(f"/submit-answer/{problem.id}", files=heic, follow_redirects=False)

    assert response.headers["location"].startswith("/grading/")
    assert list(stored_files.values()) == [b"heic-bytes"]


def test_unsupported_file_type_is_rejected(client, db, users):
    problem = make_problem(db, users["alice"].id)

    response = client.post(
        f"/submit-answer/{problem.id}",
        files={"answer_image": ("notes.txt", b"text", "text/plain")},
    )

    assert response.status_code == 400
    assert db.scalar(select(GradingJob.id)) is None


def test_model_outage_is_retried_then_succeeds(client, db, users, grader, run_jobs, no_backoff):
    grader.failures = 1
    job_id = _submit(client, make_problem(db, users["alice"].id))

    run_jobs()

    job = _job(db, job_id)
    assert (job.status, job.attempts) == (JobStatus.SUCCEEDED, 2)
    assert db.scalar(select(Submission.id)) is not None


def test_job_fails_after_max_attempts_and_saves_nothing(client, db, users, grader, run_jobs, no_backoff):
    grader.failures = 99
    job_id = _submit(client, make_problem(db, users["alice"].id))

    assert run_jobs() == config.GRADING_MAX_ATTEMPTS

    job = _job(db, job_id)
    assert (job.status, job.submission_id) == (JobStatus.FAILED, None)
    assert "fake outage" in job.last_error
    assert db.scalar(select(Submission.id)) is None
    assert "採点できませんでした" in client.get(f"/grading/{job_id}").text
    assert client.get(f"/api/grading-jobs/{job_id}").json()["status"] == "failed"


def test_retry_waits_for_backoff(client, db, users, grader, run_jobs):
    grader.failures = 1
    job_id = _submit(client, make_problem(db, users["alice"].id))

    assert run_jobs() == 1  # the retry is not due yet

    job = _job(db, job_id)
    assert (job.status, job.attempts) == (JobStatus.QUEUED, 1)


def test_daily_limit_redirects_home_with_message(client, db, users, grader):
    problem = make_problem(db, users["alice"].id)
    for _ in range(config.DAILY_GRADING_LIMIT):
        make_job(db, problem)

    response = client.post(f"/submit-answer/{problem.id}", files=ANSWER_FILE, follow_redirects=False)

    assert response.headers["location"] == "/?error=limit_reached"
    assert "上限" in client.get("/?error=limit_reached").text
    assert client.post(f"/api/problems/{problem.id}/grading-jobs", files=ANSWER_FILE).status_code == 429


def test_failed_jobs_do_not_count_toward_daily_limit(client, db, users):
    problem = make_problem(db, users["alice"].id)
    for _ in range(config.DAILY_GRADING_LIMIT):
        make_job(db, problem, status=JobStatus.FAILED)

    _submit(client, problem)


def test_global_daily_limit_applies_across_users(client, db, users, monkeypatch):
    monkeypatch.setattr(config, "GLOBAL_DAILY_GRADING_LIMIT", 1)
    make_job(db, make_problem(db, users["bob"].id))

    response = client.post(
        f"/submit-answer/{make_problem(db, users['alice'].id).id}", files=ANSWER_FILE, follow_redirects=False
    )

    assert response.headers["location"] == "/?error=limit_reached"


def _guest_with_jobs(db, name: str, ages: list[timedelta], status=JobStatus.QUEUED) -> User:
    """A guest who queued one job per entry in `ages`, that long ago."""
    guest = User(id=f"{name}-uid", is_guest=True)
    db.add(guest)
    db.commit()
    problem = make_problem(db, guest.id)
    for age in ages:
        job = make_job(db, problem, status=status)
        job.created_at = job.created_at - age
    db.commit()
    return guest


def _submit_as(client, db, name: str):
    login_as(client, name)
    problem = make_problem(db, f"{name}-uid")
    return client.post(f"/submit-answer/{problem.id}", files=ANSWER_FILE, follow_redirects=False)


def test_guest_minute_limit_is_shared_by_all_guests(client, db):
    _guest_with_jobs(db, "guest-a", [timedelta(0)] * 5)
    db.add(User(id="guest-b-uid", is_guest=True))
    db.commit()

    response = _submit_as(client, db, "guest-b")

    assert response.headers["location"] == "/?error=guest_limit"
    assert "アカウント登録" in client.get("/?error=guest_limit").text
    problem = make_problem(db, "guest-b-uid")
    assert client.post(f"/api/problems/{problem.id}/grading-jobs", files=ANSWER_FILE).status_code == 429


def test_guest_limits_do_not_apply_to_accounts(client, db, users):
    _guest_with_jobs(db, "guest-a", [timedelta(0)] * 5)

    assert _submit_as(client, db, "alice").headers["location"].startswith("/grading/")


def test_guest_hour_and_day_limits(client, db, monkeypatch):
    monkeypatch.setattr(config, "GUEST_GRADING_LIMITS", ((timedelta(minutes=1), 2), (timedelta(hours=1), 3)))
    monkeypatch.setattr(config, "GUEST_DAILY_GRADING_LIMIT", 4)
    _guest_with_jobs(db, "guest-a", [timedelta(seconds=61)] * 2)
    db.add(User(id="guest-b-uid", is_guest=True))
    db.commit()

    # 2 jobs this hour, none this minute: allowed, making 3 this hour.
    assert _submit_as(client, db, "guest-b").headers["location"].startswith("/grading/")
    # Now the hourly cap (3) is reached even though the minute cap (2) is not.
    assert _submit_as(client, db, "guest-b").headers["location"] == "/?error=guest_limit"

    # Jobs from earlier today still count toward the daily cap once the hour has passed.
    monkeypatch.setattr(config, "GUEST_GRADING_LIMITS", ())
    assert _submit_as(client, db, "guest-b").headers["location"].startswith("/grading/")  # 4th today
    assert _submit_as(client, db, "guest-b").headers["location"] == "/?error=guest_limit"


def test_failed_guest_jobs_count_toward_guest_limits(client, db):
    # A failed job still made model calls, so it counts against the shared guest budget.
    _guest_with_jobs(db, "guest-a", [timedelta(0)] * 5, status=JobStatus.FAILED)
    db.add(User(id="guest-b-uid", is_guest=True))
    db.commit()

    assert _submit_as(client, db, "guest-b").headers["location"] == "/?error=guest_limit"


def test_cannot_submit_to_another_users_problem(client, db, users):
    problem = make_problem(db, users["bob"].id)

    response = client.post(f"/submit-answer/{problem.id}", files=ANSWER_FILE, follow_redirects=False)

    assert response.headers["location"] == "/"
    assert client.post(f"/api/problems/{problem.id}/grading-jobs", files=ANSWER_FILE).status_code == 404
    assert db.scalar(select(GradingJob.id)) is None


def test_cannot_see_another_users_job(client, db, users):
    job = make_job(db, make_problem(db, users["bob"].id))

    assert client.get(f"/api/grading-jobs/{job.id}").status_code == 404
    assert client.get(f"/grading/{job.id}", follow_redirects=False).headers["location"] == "/"
    login_as(client, "bob")
    assert client.get(f"/api/grading-jobs/{job.id}").status_code == 200


def test_cannot_view_another_users_result(client, db, users):
    submission = make_submission(db, make_problem(db, users["bob"].id))

    response = client.get(f"/answer/{submission.id}", follow_redirects=False)

    assert response.status_code == 303


def test_reflection_is_saved(client, db, users):
    submission = make_submission(db, make_problem(db, users["alice"].id))

    client.post(f"/update-memo/{submission.id}", data={"mistake": "sign error", "user_memo": "check signs"})

    db.expire_all()
    saved = db.get(Submission, submission.id)
    assert (saved.mistake, saved.user_memo) == ("sign error", "check signs")
