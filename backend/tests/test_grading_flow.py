from datetime import timedelta

from sqlalchemy import select

from app import clock, config
from app.models import StudyPlan, Submission
from tests.factories import ANSWER_FILE, make_problem, make_submission


def _plans(db, problem_id):
    db.expire_all()
    return list(
        db.scalars(
            select(StudyPlan).where(StudyPlan.problem_id == problem_id).order_by(StudyPlan.scheduled_date)
        )
    )


def test_submit_grades_and_shows_result_for_problem_without_plan(client, db, users, grader):
    # Regression: the result page used StudyPlan.one() and crashed when a problem had no plan.
    problem = make_problem(db, users["alice"].id)

    response = client.post(f"/submit-answer/{problem.id}", files=ANSWER_FILE, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/answer/")
    result_page = client.get(response.headers["location"])
    assert result_page.status_code == 200
    assert "70<span" in result_page.text  # the score circle
    assert grader.requests[0].problem_text == problem.problem_text
    assert grader.requests[0].answer.data == b"handwritten-answer"


def test_submit_completes_plans_due_by_today_only(client, db, users):
    today = clock.local_today()
    problem = make_problem(
        db,
        users["alice"].id,
        plan_days=[today - timedelta(days=1), today, today + timedelta(days=5)],
    )

    client.post(f"/submit-answer/{problem.id}", files=ANSWER_FILE)

    assert [plan.is_completed for plan in _plans(db, problem.id)] == [True, True, False]


def test_submit_ahead_of_schedule_completes_nearest_plan(client, db, users):
    today = clock.local_today()
    problem = make_problem(
        db,
        users["alice"].id,
        plan_days=[today + timedelta(days=2), today + timedelta(days=9)],
    )

    client.post(f"/submit-answer/{problem.id}", files=ANSWER_FILE)

    assert [plan.is_completed for plan in _plans(db, problem.id)] == [True, False]


def test_viewing_result_does_not_change_plans(client, db, users):
    today = clock.local_today()
    problem = make_problem(db, users["alice"].id, plan_days=[today])
    submission = make_submission(db, problem)

    client.get(f"/answer/{submission.id}")

    assert _plans(db, problem.id)[0].is_completed is False


def test_problem_image_is_sent_to_grader(client, db, users, grader):
    problem = make_problem(db, users["alice"].id, text=None, image_url="https://res.cloudinary.com/demo/p.jpg")

    client.post(f"/submit-answer/{problem.id}", files=ANSWER_FILE)

    assert grader.requests[0].problem_file.data == b"problem-image"


def test_heic_sent_as_octet_stream_is_accepted(client, db, users, grader):
    # Regression: iPhones can label HEIC as application/octet-stream.
    problem = make_problem(db, users["alice"].id)
    heic = {"answer_image": ("IMG_0001.HEIC", b"heic-bytes", "application/octet-stream")}

    response = client.post(f"/submit-answer/{problem.id}", files=heic, follow_redirects=False)

    assert response.status_code == 303
    assert grader.requests[0].answer.mime_type == "image/heic"


def test_unsupported_file_type_is_rejected(client, db, users, grader):
    problem = make_problem(db, users["alice"].id)

    response = client.post(
        f"/submit-answer/{problem.id}",
        files={"answer_image": ("notes.txt", b"text", "text/plain")},
    )

    assert response.status_code == 400
    assert grader.requests == []


def test_grader_failure_returns_502_and_saves_nothing(client, db, users, grader):
    grader.fail = True
    problem = make_problem(db, users["alice"].id)

    response = client.post(f"/submit-answer/{problem.id}", files=ANSWER_FILE)

    assert response.status_code == 502
    assert db.scalar(select(Submission.id)) is None


def test_daily_limit_redirects_home_with_message(client, db, users, grader):
    problem = make_problem(db, users["alice"].id)
    for _ in range(config.DAILY_GRADING_LIMIT):
        make_submission(db, problem)

    response = client.post(f"/submit-answer/{problem.id}", files=ANSWER_FILE, follow_redirects=False)

    assert response.headers["location"] == "/?error=limit_reached"
    assert grader.requests == []
    assert "上限" in client.get("/?error=limit_reached").text


def test_cannot_submit_to_another_users_problem(client, db, users, grader):
    problem = make_problem(db, users["bob"].id)

    response = client.post(f"/submit-answer/{problem.id}", files=ANSWER_FILE, follow_redirects=False)

    assert response.headers["location"] == "/"
    assert grader.requests == []


def test_cannot_view_another_users_result(client, db, users):
    submission = make_submission(db, make_problem(db, users["bob"].id))

    response = client.get(f"/answer/{submission.id}", follow_redirects=False)

    assert response.status_code == 303


def test_reflection_is_saved(client, db, users):
    submission = make_submission(db, make_problem(db, users["alice"].id))

    client.post(f"/update-memo/{submission.id}", data={"mistake": "sign error", "user_memo": "check signs"})

    db.expire_all()
    saved = db.get(Submission, submission.id)
    assert (saved.mistake, saved.user_memo) == ("sign error", "check signs")
