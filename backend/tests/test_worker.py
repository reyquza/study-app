"""The grading queue: claiming, leases, and protection against double grading."""

from datetime import timedelta

from sqlalchemy import select

from app import config
from app.grading.base import GradingResult
from app.models import GradingJob, JobStatus, Submission
from app.services import grading_jobs
from tests.factories import make_job, make_problem


RESULT = GradingResult(score=80, ai_feedback="f", explanation="e")


def _job(db, job_id) -> GradingJob:
    db.expire_all()
    return db.get(GradingJob, job_id)


def test_claim_takes_oldest_job_once(db, users):
    problem = make_problem(db, users["alice"].id)
    first, second = make_job(db, problem), make_job(db, problem)

    claims = [grading_jobs.claim_next_job(db) for _ in range(3)]

    assert [claim and claim.id for claim in claims] == [first.id, second.id, None]
    assert _job(db, first.id).status == JobStatus.RUNNING
    assert claims[0].attempt == 1


def test_job_is_not_claimed_before_run_after(db, users):
    make_job(db, make_problem(db, users["alice"].id), run_after_in=timedelta(minutes=1))

    assert grading_jobs.claim_next_job(db) is None


def test_running_job_with_live_lease_is_left_alone(db, users):
    make_job(db, make_problem(db, users["alice"].id), status=JobStatus.RUNNING, attempts=1, lease_expires_in=timedelta(minutes=1))

    assert grading_jobs.claim_next_job(db) is None


def test_job_with_expired_lease_is_reclaimed_and_graded(db, users, stored_files, run_jobs):
    # The worker that claimed it died mid-grading.
    job = make_job(db, make_problem(db, users["alice"].id), status=JobStatus.RUNNING, attempts=1, lease_expires_in=timedelta(seconds=-1))

    assert run_jobs() == 1

    reclaimed = _job(db, job.id)
    assert (reclaimed.status, reclaimed.attempts) == (JobStatus.SUCCEEDED, 2)


def test_expired_lease_on_last_attempt_fails_the_job(db, users, grader, run_jobs):
    job = make_job(
        db,
        make_problem(db, users["alice"].id),
        status=JobStatus.RUNNING,
        attempts=config.GRADING_MAX_ATTEMPTS,
        lease_expires_in=timedelta(seconds=-1),
    )

    run_jobs()

    assert _job(db, job.id).status == JobStatus.FAILED
    assert grader.requests == []


def test_result_from_a_reclaimed_attempt_is_dropped(db, users):
    job = make_job(db, make_problem(db, users["alice"].id))
    stale = grading_jobs.claim_next_job(db)
    # Its lease ran out and another worker took the job over.
    job = _job(db, job.id)
    job.attempts += 1
    db.commit()

    grading_jobs.complete_job(db, stale, RESULT, "fake-grader")
    grading_jobs.fail_attempt(db, stale, "late failure")

    after = _job(db, job.id)
    assert (after.status, after.submission_id, after.last_error) == (JobStatus.RUNNING, None, None)
    assert db.scalar(select(Submission.id)) is None


def test_result_for_deleted_problem_is_dropped(db, users):
    problem = make_problem(db, users["alice"].id)
    make_job(db, problem)
    claimed = grading_jobs.claim_next_job(db)
    db.delete(problem)
    db.commit()

    grading_jobs.complete_job(db, claimed, RESULT, "fake-grader")

    assert db.scalar(select(Submission.id)) is None
