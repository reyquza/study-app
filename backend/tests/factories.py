from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import GradingJob, JobStatus, Problem, StudyPlan, Submission


def make_problem(
    db: Session,
    user_id: str,
    text: str | None = "Solve x^2 - 1 = 0.",
    image_url: str | None = None,
    plan_days: list[date] = (),
) -> Problem:
    problem = Problem(user_id=user_id, problem_text=text, problem_image_path=image_url, source="Test book p.1")
    problem.schedules = [StudyPlan(scheduled_date=day) for day in plan_days]
    db.add(problem)
    db.commit()
    return problem


def make_submission(db: Session, problem: Problem, score: int = 50) -> Submission:
    submission = Submission(problem_id=problem.id, score=score, ai_feedback="f", explanation="e")
    db.add(submission)
    db.commit()
    return submission


def make_job(
    db: Session,
    problem: Problem,
    status: JobStatus = JobStatus.QUEUED,
    attempts: int = 0,
    lease_expires_in: timedelta | None = None,
    run_after_in: timedelta = timedelta(0),
) -> GradingJob:
    now = datetime.now(timezone.utc)
    job = GradingJob(
        user_id=problem.user_id,
        problem_id=problem.id,
        answer_image_path="https://res.cloudinary.com/demo/image/upload/old.jpg",
        status=status,
        attempts=attempts,
        run_after=now + run_after_in,
        lease_expires_at=now + lease_expires_in if lease_expires_in is not None else None,
        created_at=now,
    )
    db.add(job)
    db.commit()
    return job


ANSWER_FILE = {"answer_image": ("answer.jpg", b"handwritten-answer", "image/jpeg")}
