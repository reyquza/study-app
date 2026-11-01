"""Grading as background jobs.

Submitting an answer only stores the file and queues a job, so the request returns at
once. app.worker claims queued jobs, calls the model, and records the result here.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.orm import Session

from app import clock, config
from app.grading.base import GradingResult
from app.models import GradingJob, JobStatus, Problem, Submission, User
from app.services import storage
from app.services.plans import complete_plans_for_problem


logger = logging.getLogger(__name__)

# Wait before retry 1, 2, ... (the last value repeats).
RETRY_BACKOFF = (timedelta(seconds=5), timedelta(seconds=30))


class GradingLimitReached(Exception):
    """A grading limit is used up. `scope` says which: "user" (this learner's day),
    "guest" (all guests together) or "global" (everyone)."""

    def __init__(self, scope: str):
        super().__init__(scope)
        self.scope = scope


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


def _count_jobs_since(
    db: Session,
    since: datetime,
    user_id: str | None = None,
    guests_only: bool = False,
    include_failed: bool = True,
) -> int:
    query = select(func.count()).select_from(GradingJob).where(GradingJob.created_at >= since)
    if not include_failed:
        query = query.where(GradingJob.status != JobStatus.FAILED)
    if user_id is not None:
        query = query.where(GradingJob.user_id == user_id)
    if guests_only:
        query = query.join(User, User.id == GradingJob.user_id).where(User.is_guest.is_(True))
    return db.scalar(query) or 0


def _check_limits(db: Session, user: User) -> None:
    """Raise GradingLimitReached if this user may not queue another job now.

    The learner's own daily limit skips failed jobs, so an outage does not use up their day.
    The guest and global caps protect the model bill, so they count failed jobs too: a failed
    job still made model calls.
    """
    now = _utcnow()
    day_start = clock.local_day_start_utc(clock.local_today())
    if _count_jobs_since(db, day_start, user_id=user.id, include_failed=False) >= config.DAILY_GRADING_LIMIT:
        logger.info("Daily grading limit reached for user %s", user.id)
        raise GradingLimitReached("user")
    if user.is_guest:
        windows = (*((now - length, limit) for length, limit in config.GUEST_GRADING_LIMITS),
                   (day_start, config.GUEST_DAILY_GRADING_LIMIT))
        for since, limit in windows:
            if _count_jobs_since(db, since, guests_only=True) >= limit:
                logger.warning("Guest grading limit reached (%d since %s)", limit, since.isoformat())
                raise GradingLimitReached("guest")
    if _count_jobs_since(db, day_start) >= config.GLOBAL_DAILY_GRADING_LIMIT:
        logger.warning("Global daily grading limit reached")
        raise GradingLimitReached("global")


def _lock_limits(db: Session) -> None:
    """Serialize limit checks until this transaction ends, so concurrent submissions cannot
    all pass the same check. SQLite already allows only one writer at a time."""
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _LIMITS_LOCK_KEY})


# Any constant works; it only has to be the same for every process.
_LIMITS_LOCK_KEY = 4_812_001


async def enqueue_grading(
    db: Session,
    user: User,
    problem: Problem,
    upload: storage.ValidatedUpload,
) -> GradingJob:
    """Store the answer file and queue it for grading. Raises GradingLimitReached."""
    # Check before uploading, so a refused request costs no upload...
    _check_limits(db, user)
    answer_url = await storage.store_upload(upload)

    # ...and again under a lock, in the transaction that inserts the job, because the upload
    # takes seconds and other submissions may have been queued meanwhile.
    _lock_limits(db)
    _check_limits(db, user)
    now = _utcnow()
    job = GradingJob(
        user_id=user.id,
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


def fail_attempt(db: Session, claimed: ClaimedJob, error: str, retryable: bool = True) -> None:
    """Record a failed attempt: retry later, or give up after GRADING_MAX_ATTEMPTS or when
    the error is not worth retrying (see GradingFailed.retryable)."""
    job = _lock_current_attempt(db, claimed)
    if job is None:
        db.rollback()
        return

    now = _utcnow()
    job.last_error = error[:2000]
    job.lease_expires_at = None
    if not retryable or job.attempts >= config.GRADING_MAX_ATTEMPTS:
        job.status = JobStatus.FAILED
        job.finished_at = now
        logger.error("Job %s failed after %s attempts: %s", job.id, job.attempts, error)
    else:
        job.status = JobStatus.QUEUED
        job.run_after = now + RETRY_BACKOFF[min(job.attempts, len(RETRY_BACKOFF)) - 1]
        logger.warning("Job %s attempt %s failed, will retry: %s", job.id, job.attempts, error)
    db.commit()
