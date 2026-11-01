from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import clock, config
from app.models import Problem, StudyPlan, Tag, User


class ProblemLimitReached(Exception):
    pass


def check_problem_limit(db: Session, user: User) -> None:
    """Raise ProblemLimitReached if the user may not register another problem today.
    Guests also share one daily cap, since anyone can create guests."""
    day_start = clock.local_day_start_utc(clock.local_today())
    today = select(func.count()).select_from(Problem).where(Problem.created_at >= day_start)
    if (db.scalar(today.where(Problem.user_id == user.id)) or 0) >= config.DAILY_PROBLEM_LIMIT:
        raise ProblemLimitReached()
    if user.is_guest:
        guests_today = today.join(User, User.id == Problem.user_id).where(User.is_guest.is_(True))
        if (db.scalar(guests_today) or 0) >= config.GUEST_DAILY_PROBLEM_LIMIT:
            raise ProblemLimitReached()


def parse_tag_names(raw: str | None) -> list[str]:
    """'数学, 微積分,数学' -> ['数学', '微積分'] (order kept, duplicates and blanks dropped)."""
    if not raw:
        return []
    names = (name.strip() for name in raw.split(","))
    return list(dict.fromkeys(name for name in names if name))


def get_user_problem(db: Session, user_id: str, problem_id: int) -> Problem | None:
    return db.scalars(
        select(Problem).where(Problem.id == problem_id, Problem.user_id == user_id)
    ).one_or_none()


def list_recent_problems(db: Session, user_id: str, limit: int) -> list[Problem]:
    return list(
        db.scalars(
            select(Problem)
            .where(Problem.user_id == user_id)
            .order_by(Problem.id.desc())
            .limit(limit)
        )
    )


def create_problem(
    db: Session,
    user_id: str,
    image_url: str | None,
    text: str | None,
    source: str | None,
    tag_names: list[str],
    scheduled_date: date | None,
) -> Problem:
    problem = Problem(
        user_id=user_id,
        problem_image_path=image_url,
        problem_text=text or None,
        source=source.strip() if source and source.strip() else None,
    )

    existing_tags = {
        tag.name: tag
        for tag in db.scalars(select(Tag).where(Tag.name.in_(tag_names)))
    } if tag_names else {}
    problem.tags = [existing_tags.get(name) or Tag(name=name) for name in tag_names]

    if scheduled_date is not None:
        problem.schedules.append(StudyPlan(scheduled_date=scheduled_date))

    db.add(problem)
    db.commit()
    return problem
