"""Grading a submitted answer and recording the result."""

import logging

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import clock, config
from app.grading.base import Grader, GradingRequest
from app.models import Problem, Submission
from app.services import storage
from app.services.plans import complete_plans_for_problem


logger = logging.getLogger(__name__)


class DailyLimitReached(Exception):
    pass


def count_gradings_today(db: Session, user_id: str) -> int:
    today = clock.local_today()
    return db.scalar(
        select(func.count())
        .select_from(Submission)
        .join(Submission.problem)
        .where(Problem.user_id == user_id)
        .where(Submission.solved_at >= clock.local_day_start_utc(today))
    ) or 0


async def grade_submission(
    db: Session,
    grader: Grader,
    user_id: str,
    problem: Problem,
    upload: storage.ValidatedUpload,
) -> Submission:
    """Store the answer, grade it, save the result and tick off the problem's plans.

    Raises DailyLimitReached, storage.FileFetchFailed or grading.base.GradingFailed.
    """
    if count_gradings_today(db, user_id) >= config.DAILY_GRADING_LIMIT:
        logger.info("Daily grading limit reached for user %s", user_id)
        raise DailyLimitReached()

    answer_url = await storage.store_upload(upload)
    problem_file = (
        await storage.fetch_stored_file(problem.problem_image_path)
        if problem.problem_image_path
        else None
    )

    # Grading takes tens of seconds; run it in a worker thread so other requests keep flowing.
    result = await run_in_threadpool(
        grader.grade,
        GradingRequest(
            answer=upload.file,
            problem_text=problem.problem_text,
            problem_file=problem_file,
        ),
    )

    submission = Submission(
        problem_id=problem.id,
        answer_image_path=answer_url,
        score=result.score,
        ai_feedback=result.ai_feedback,
        explanation=result.explanation,
    )
    db.add(submission)
    complete_plans_for_problem(db, problem.id, clock.local_today())
    db.commit()
    return submission


def get_user_submission(db: Session, user_id: str, submission_id: int) -> Submission | None:
    return db.scalars(
        select(Submission)
        .join(Submission.problem)
        .where(Submission.id == submission_id, Problem.user_id == user_id)
    ).one_or_none()


def list_problem_submissions(db: Session, problem_id: int) -> list[Submission]:
    return list(
        db.scalars(
            select(Submission)
            .where(Submission.problem_id == problem_id)
            .order_by(Submission.solved_at)
        )
    )


def save_reflection(
    db: Session, user_id: str, submission_id: int, mistake: str, memo: str
) -> bool:
    submission = get_user_submission(db, user_id, submission_id)
    if submission is None:
        return False
    submission.mistake = mistake
    submission.user_memo = memo
    db.commit()
    return True
