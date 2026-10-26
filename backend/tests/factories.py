from datetime import date

from sqlalchemy.orm import Session

from app.models import Problem, StudyPlan, Submission


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


ANSWER_FILE = {"answer_image": ("answer.jpg", b"handwritten-answer", "image/jpeg")}
