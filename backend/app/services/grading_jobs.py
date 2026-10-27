"""Grading as background jobs.

Submitting an answer only stores the file and queues a job, so the request returns at
once. app.worker claims queued jobs, calls the model, and records the result here.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app import clock, config
from app.grading.base import GradingResult
from app.models import GradingJob, JobStatus, Problem, Submission
from app.services import storage
from app.services.plans import complete_plans_for_problem


logger = logging.getLogger(__name__)

# Wait before retry 1, 2, ... (the last value repeats).
RETRY_BACKOFF = (timedelta(seconds=5), timedelta(seconds=30))


class DailyLimitReached(Exception):
    pass


@dataclass(frozen=True)
class ClaimedJob:
    """What a worker needs to grade a job, copied out of the ORM so it can be used after
    the claiming session has closed."""

    id: int
    attempt: int
    answer_image_path: str
    problem_text: str | None
    problem_image_path: str | None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _count_jobs_since(db: Session, since: datetime, user_id: str | None = None) -> int:
    query = (
        select(func.count())
        .select_from(GradingJob)
        .where(GradingJob.created_at >= since, GradingJob.status != JobStatus.FAILED)
    )
    if user_id is not None:
        query = query.where(GradingJob.user_id == user_id)
    return db.scalar(query) or 0


async def enqueue_grading(
    db: Session,
    user_id: str,
    problem: Problem,
    upload: storage.ValidatedUpload,
) -> GradingJob:
    """Store the answer file and queue it for grading. Raises DailyLimitReached.

    Failed jobs do not count toward the limits, so an outage does not use up a learner's day.
    """
    day_start = clock.local_day_start_utc(clock.local_today())
    if _count_jobs_since(db, day_start, user_id) >= config.DAILY_GRADING_LIMIT:
        logger.info("Daily grading limit reached for user %s", user_id)
        raise DailyLimitReached()
    if _count_jobs_since(db, day_start) >= config.GLOBAL_DAILY_GRADING_LIMIT:
        logger.warning("Global daily grading limit reached")
        raise DailyLimitReached()

    answer_url = await storage.store_upload(upload)
    now = _utcnow()
    job = GradingJob(
        user_id=user_id,
        problem_id=problem.id,
        answer_image_path=answer_url,
        status=JobStatus.QUEUED,
        run_after=now,
        created_at=now,
    )
    db.add(job)
    db.commit()
    return job


def get_user_job(db: Session, user_id: str, job_id: int) -> GradingJob | None:
    return db.scalars(
        select(GradingJob).where(GradingJob.id == job_id, GradingJob.user_id == user_id)
    ).one_or_none()


def claim_next_job(db: Session) -> ClaimedJob | None:
    """Take the oldest runnable job: a queued one that is due, or a running one whose
    worker let its lease expire (crashed or was redeployed)."""
    now = _utcnow()
    runnable = or_(
        and_(GradingJob.status == JobStatus.QUEUED, GradingJob.run_after <= now),
        and_(GradingJob.status == JobStatus.RUNNING, GradingJob.lease_expires_at < now),
    )
    job = db.scalars(
        select(GradingJob)
        .where(runnable)
        .order_by(GradingJob.id)
        .limit(1)
        # Postgres: concurrent workers skip rows another worker is claiming instead of waiting.
        .with_for_update(skip_locked=True)
    ).first()
    if job is None:
        db.rollback()
        return None

    # On Postgres the row lock already makes this claim exclusive. SQLite has no row locks,
    # so the UPDATE also checks that nobody claimed the job since we read it.
    claimed = db.execute(
        update(GradingJob)
        .where(
            GradingJob.id == job.id,
            GradingJob.status == job.status,
            GradingJob.attempts == job.attempts,
        )
        .values(
            status=JobStatus.RUNNING,
            attempts=GradingJob.attempts + 1,
            started_at=now,
            lease_expires_at=now + config.JOB_LEASE,
        )
    ).rowcount
    db.commit()
    if claimed != 1:
        return None

    db.refresh(job)
    return ClaimedJob(
        id=job.id,
        attempt=job.attempts,
        answer_image_path=job.answer_image_path,
        problem_text=job.problem.problem_text,
        problem_image_path=job.problem.problem_image_path,
    )


def _lock_current_attempt(db: Session, claimed: ClaimedJob) -> GradingJob | None:
    """The job row, unless this attempt no longer owns it (its lease expired and another
    worker took over, or the problem was deleted). A stale attempt must not write."""
    job = db.get(GradingJob, claimed.id, with_for_update=True)
    if job is None or job.status != JobStatus.RUNNING or job.attempts != claimed.attempt:
        logger.warning("Dropping stale result for job %s attempt %s", claimed.id, claimed.attempt)
        return None
    return job


def complete_job(db: Session, claimed: ClaimedJob, result: GradingResult, grader_model: str) -> None:
    job = _lock_current_attempt(db, claimed)
    if job is None:
        db.rollback()
        return

    submission = Submission(
        problem_id=job.problem_id,
        answer_image_path=job.answer_image_path,
        score=result.score,
        ai_feedback=result.ai_feedback,
        explanation=result.explanation,
    )
    db.add(submission)
    db.flush()

    job.status = JobStatus.SUCCEEDED
    job.submission_id = submission.id
    job.grader_model = grader_model
    job.finished_at = _utcnow()
    job.lease_expires_at = None
    complete_plans_for_problem(db, job.problem_id, clock.local_today())
    db.commit()


def fail_attempt(db: Session, claimed: ClaimedJob, error: str) -> None:
    """Record a failed attempt: retry later, or give up after GRADING_MAX_ATTEMPTS."""
    job = _lock_current_attempt(db, claimed)
    if job is None:
        db.rollback()
        return

    now = _utcnow()
    job.last_error = error[:2000]
    job.lease_expires_at = None
    if job.attempts >= config.GRADING_MAX_ATTEMPTS:
        job.status = JobStatus.FAILED
        job.finished_at = now
        logger.error("Job %s failed after %s attempts: %s", job.id, job.attempts, error)
    else:
        job.status = JobStatus.QUEUED
        job.run_after = now + RETRY_BACKOFF[min(job.attempts, len(RETRY_BACKOFF)) - 1]
        logger.warning("Job %s attempt %s failed, will retry: %s", job.id, job.attempts, error)
    db.commit()
