"""Study plans: which problem to solve on which day."""

import calendar
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Problem, StudyPlan, Submission


@dataclass
class MonthView:
    year: int
    month: int
    # calendar.monthcalendar layout: weeks of day numbers, 0 for days outside the month.
    weeks: list[list[int]]
    plans_by_day: dict[int, list[StudyPlan]]
    previous: tuple[int, int]
    next: tuple[int, int]


@dataclass
class DayView:
    day: date
    incomplete: list[StudyPlan]
    completed: list[StudyPlan]
    solve_counts: dict[int, int] = field(default_factory=dict)
    # Score of the most recent submission per problem; None when it has not been graded.
    latest_scores: dict[int, int | None] = field(default_factory=dict)


def _user_plans():
    # Ownership lives on Problem, so every plan query joins it and filters on Problem.user_id.
    return select(StudyPlan).join(StudyPlan.problem)


def get_user_plan(db: Session, user_id: str, plan_id: int) -> StudyPlan | None:
    return db.scalars(
        _user_plans().where(StudyPlan.id == plan_id, Problem.user_id == user_id)
    ).one_or_none()


def upcoming_incomplete_plans(
    db: Session, user_id: str, today: date, days_ahead: int = 3
) -> dict[date, list[StudyPlan]]:
    """Incomplete plans up to `days_ahead` days from today, overdue ones included, grouped by day."""
    plans = db.scalars(
        _user_plans()
        .where(Problem.user_id == user_id)
        .where(StudyPlan.is_completed.is_(False))
        .where(StudyPlan.scheduled_date <= today + timedelta(days=days_ahead))
        .order_by(StudyPlan.scheduled_date)
    )
    grouped: dict[date, list[StudyPlan]] = defaultdict(list)
    for plan in plans:
        grouped[plan.scheduled_date].append(plan)
    return dict(grouped)


def month_view(db: Session, user_id: str, year: int, month: int) -> MonthView:
    _, last_day = calendar.monthrange(year, month)
    plans = db.scalars(
        _user_plans()
        .where(Problem.user_id == user_id)
        .where(StudyPlan.scheduled_date.between(date(year, month, 1), date(year, month, last_day)))
        .order_by(StudyPlan.id)
    )
    plans_by_day: dict[int, list[StudyPlan]] = defaultdict(list)
    for plan in plans:
        plans_by_day[plan.scheduled_date.day].append(plan)

    return MonthView(
        year=year,
        month=month,
        weeks=calendar.monthcalendar(year, month),
        plans_by_day=plans_by_day,
        previous=(year - 1, 12) if month == 1 else (year, month - 1),
        next=(year + 1, 1) if month == 12 else (year, month + 1),
    )


def day_view(db: Session, user_id: str, day: date) -> DayView:
    plans = list(
        db.scalars(
            _user_plans()
            .where(Problem.user_id == user_id, StudyPlan.scheduled_date == day)
            .order_by(StudyPlan.id)
        )
    )
    view = DayView(
        day=day,
        incomplete=[plan for plan in plans if not plan.is_completed],
        completed=[plan for plan in plans if plan.is_completed],
    )

    problem_ids = {plan.problem_id for plan in plans}
    if not problem_ids:
        return view

    # Two queries for the whole day instead of one per plan.
    view.solve_counts = dict(
        db.execute(
            select(Submission.problem_id, func.count())
            .where(Submission.problem_id.in_(problem_ids))
            .group_by(Submission.problem_id)
        ).all()
    )
    latest_ids = (
        select(func.max(Submission.id))
        .where(Submission.problem_id.in_(problem_ids))
        .group_by(Submission.problem_id)
    )
    view.latest_scores = dict(
        db.execute(
            select(Submission.problem_id, Submission.score).where(Submission.id.in_(latest_ids))
        ).all()
    )
    return view


def add_plans(db: Session, user_id: str, problem_ids: list[int], day: date) -> None:
    """Schedule the given problems on `day`. Ids of problems the user does not own are ignored."""
    owned_ids = db.scalars(
        select(Problem.id).where(Problem.user_id == user_id, Problem.id.in_(problem_ids))
    )
    db.add_all(StudyPlan(problem_id=problem_id, scheduled_date=day) for problem_id in owned_ids)
    db.commit()


def delete_plan(db: Session, user_id: str, plan_id: int) -> bool:
    plan = get_user_plan(db, user_id, plan_id)
    if plan is None:
        return False
    db.delete(plan)
    db.commit()
    return True


def complete_plans_for_problem(db: Session, problem_id: int, today: date) -> None:
    """Mark the problem's plans due by today as done; if none are due (the learner worked
    ahead), mark the nearest upcoming one. Does not commit."""
    pending = list(
        db.scalars(
            select(StudyPlan)
            .where(StudyPlan.problem_id == problem_id, StudyPlan.is_completed.is_(False))
            .order_by(StudyPlan.scheduled_date)
        )
    )
    due = [plan for plan in pending if plan.scheduled_date <= today]
    for plan in due or pending[:1]:
        plan.is_completed = True
