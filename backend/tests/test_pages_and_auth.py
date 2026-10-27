from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy import select

from app import clock, config
from app.models import Problem, User
from tests.factories import make_problem, make_submission


def _sign_up(client, email="carol@example.com", password="correct-horse"):
    return client.post("/api/auth/signup", json={"email": email, "password": password})


def test_every_page_renders(client, db, users):
    today = clock.local_today()
    problem = make_problem(db, users["alice"].id, plan_days=[today])
    submission = make_submission(db, problem)

    for path in [
        "/",
        "/schedule",
        "/schedule?year=2026&month=12",
        f"/schedule/{today.isoformat()}",
        f"/add-task/{today.isoformat()}",
        f"/confirm-problem/{problem.id}",
        "/register-problem",
        f"/solve/{problem.id}",
        f"/answer/{submission.id}",
        "/login",
    ]:
        response = client.get(path)
        assert response.status_code == 200, path


def test_missing_problem_redirects_home(client):
    # Regression: /solve returned `null` because of a missing return.
    for path in ["/solve/999", "/confirm-problem/999"]:
        response = client.get(path, follow_redirects=False)
        assert (response.status_code, response.headers["location"]) == (303, "/"), path


def test_invalid_dates_redirect_to_calendar(client):
    for path in ["/schedule/not-a-date", "/add-task/2026-13-40"]:
        response = client.get(path, follow_redirects=False)
        assert response.headers["location"] == "/schedule", path


def test_signed_out_pages_redirect_to_login(anonymous_client):
    response = anonymous_client.get("/", follow_redirects=False)

    assert (response.status_code, response.headers["location"]) == (303, "/login")


def test_signed_out_api_returns_401(anonymous_client):
    response = anonymous_client.get("/api/users/me")

    assert response.status_code == 401


def test_current_user_api(client):
    assert client.get("/api/users/me").json() == {"id": "alice-uid", "email": "alice@example.com", "is_guest": False}


def test_sign_up_signs_in_with_httponly_cookie(anonymous_client):
    response = _sign_up(anonymous_client, email="  Carol@Example.com ")

    assert response.status_code == 201
    assert response.json()["email"] == "carol@example.com"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert f"Max-Age={14 * 24 * 3600}" in cookie
    assert anonymous_client.get("/").status_code == 200


def test_password_is_stored_hashed(anonymous_client, db):
    _sign_up(anonymous_client)

    stored = db.scalar(select(User.password_hash).where(User.email == "carol@example.com"))
    assert stored.startswith("$argon2")


def test_sign_up_rejects_taken_email_and_short_password(anonymous_client):
    _sign_up(anonymous_client)

    assert _sign_up(anonymous_client, email="CAROL@example.com").status_code == 409
    assert _sign_up(anonymous_client, email="dave@example.com", password="short").status_code == 422
    assert _sign_up(anonymous_client, email="not-an-email").status_code == 422


def test_login(anonymous_client):
    _sign_up(anonymous_client)
    anonymous_client.cookies.clear()

    wrong = anonymous_client.post("/api/auth/login", json={"email": "carol@example.com", "password": "wrong-password"})
    unknown = anonymous_client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "correct-horse"})
    assert (wrong.status_code, unknown.status_code) == (401, 401)
    assert anonymous_client.get("/api/users/me").status_code == 401

    ok = anonymous_client.post("/api/auth/login", json={"email": "Carol@example.com", "password": "correct-horse"})
    assert ok.status_code == 200
    assert anonymous_client.get("/api/users/me").json()["email"] == "carol@example.com"


def test_guest_can_use_the_app_then_keep_their_data_by_signing_up(anonymous_client, db):
    guest = anonymous_client.post("/api/auth/guest").json()
    assert guest["is_guest"] is True
    assert anonymous_client.get("/").status_code == 200
    make_problem(db, guest["id"])

    upgraded = _sign_up(anonymous_client).json()

    assert (upgraded["id"], upgraded["is_guest"]) == (guest["id"], False)
    assert db.scalar(select(Problem.user_id)) == guest["id"]


def test_login_page_redirects_signed_in_users_but_not_guests(client, anonymous_client):
    assert client.get("/login", follow_redirects=False).headers["location"] == "/"
    client.cookies.clear()
    client.post("/api/auth/guest")
    assert client.get("/login", follow_redirects=False).status_code == 200


def test_forged_or_expired_token_is_rejected(anonymous_client, users):
    now = datetime.now(timezone.utc)
    forged = jwt.encode({"sub": "alice-uid", "exp": now + timedelta(days=1)}, "a-different-secret-that-is-long-enough-for-hs256", algorithm="HS256")
    expired = jwt.encode({"sub": "alice-uid", "exp": now - timedelta(seconds=1)}, config.JWT_SECRET_KEY, algorithm="HS256")

    for token in [forged, expired, "garbage"]:
        anonymous_client.cookies.set(config.SESSION_COOKIE_NAME, token)
        assert anonymous_client.get("/api/users/me").status_code == 401


def test_logout_clears_cookie(anonymous_client):
    _sign_up(anonymous_client)

    response = anonymous_client.get("/logout", follow_redirects=False)

    assert response.headers["location"] == "/login"
    assert 'token=""' in response.headers["set-cookie"]
    assert anonymous_client.get("/", follow_redirects=False).headers["location"] == "/login"


def test_health(anonymous_client):
    assert anonymous_client.get("/api/health").json() == {"status": "ok"}
