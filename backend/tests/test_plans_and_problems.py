from datetime import timedelta

from sqlalchemy import select

from app import clock
from app.models import Problem, StudyPlan, Tag
from app.services.problems import parse_tag_names
from tests.factories import make_problem, make_submission


def test_cannot_delete_another_users_plan(client, db, users):
    # Regression: /delete-task deleted any plan id.
    problem = make_problem(db, users["bob"].id, plan_days=[clock.local_today()])
    plan_id = problem.schedules[0].id

    client.post(f"/delete-task/{plan_id}")

    db.expire_all()
    assert db.get(StudyPlan, plan_id) is not None


def test_delete_own_plan_returns_to_previous_page(client, db, users):
    today = clock.local_today()
    problem = make_problem(db, users["alice"].id, plan_days=[today])
    plan_id = problem.schedules[0].id

    response = client.post(
        f"/delete-task/{plan_id}",
        headers={"referer": f"http://testserver/schedule/{today.isoformat()}"},
        follow_redirects=False,
    )

    assert response.headers["location"] == f"/schedule/{today.isoformat()}"
    db.expire_all()
    assert db.get(StudyPlan, plan_id) is None


def test_delete_ignores_offsite_referer(client, db, users):
    problem = make_problem(db, users["alice"].id, plan_days=[clock.local_today()])

    response = client.post(
        f"/delete-task/{problem.schedules[0].id}",
        headers={"referer": "https://evil.example//phish"},
        follow_redirects=False,
    )

    assert response.headers["location"] == "/"


def test_add_plans_skips_problems_of_other_users(client, db, users):
    day = clock.local_today() + timedelta(days=1)
    mine = make_problem(db, users["alice"].id)
    theirs = make_problem(db, users["bob"].id)

    client.post(f"/add-task/{day.isoformat()}", data={"problem_ids": [mine.id, theirs.id]})

    planned = set(db.scalars(select(StudyPlan.problem_id).where(StudyPlan.scheduled_date == day)))
    assert planned == {mine.id}


def test_add_plans_with_nothing_selected_is_not_an_error(client):
    day = clock.local_today().isoformat()

    response = client.post(f"/add-task/{day}", follow_redirects=False)

    assert response.headers["location"] == f"/schedule/{day}"


def test_day_view_shows_counts_and_latest_score(client, db, users):
    today = clock.local_today()
    problem = make_problem(db, users["alice"].id, plan_days=[today])
    make_submission(db, problem, score=40)
    make_submission(db, problem, score=85)

    page = client.get(f"/schedule/{today.isoformat()}")

    assert page.status_code == 200
    assert "2回" in page.text
    assert "85点" in page.text


def test_register_problem_reuses_tags_and_schedules(client, db, users, stored_files):
    db.add(Tag(name="数学"))
    db.commit()
    day = clock.local_today() + timedelta(days=2)

    response = client.post(
        "/register-problem",
        files={"image": ("problem.png", b"png", "image/png")},
        data={"source": "  Blue chart p.120 ", "tags": "数学, 微積分, 数学", "scheduled_date": day.isoformat()},
        follow_redirects=False,
    )

    assert response.status_code == 303
    problem = db.scalars(select(Problem)).one()
    assert problem.source == "Blue chart p.120"
    assert sorted(tag.name for tag in problem.tags) == ["微積分", "数学"]
    assert db.scalar(select(Tag.id).where(Tag.name == "数学")) is not None
    assert len(db.scalars(select(Tag)).all()) == 2
    assert [plan.scheduled_date for plan in problem.schedules] == [day]
    assert stored_files == [b"png"]


def test_parse_tag_names():
    assert parse_tag_names(None) == []
    assert parse_tag_names(" 数学, ,微積分,数学 ") == ["数学", "微積分"]
