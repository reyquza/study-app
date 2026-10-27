"""Graded attempts and the learner's reflections on them. Grading itself happens in
app.services.grading_jobs."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Problem, Submission


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
