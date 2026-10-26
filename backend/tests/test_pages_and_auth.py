import pytest
from firebase_admin import auth as firebase_auth

from app import clock, config
from app.services import auth as auth_service
from tests.factories import make_problem, make_submission


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
    assert client.get("/api/users/me").json() == {"id": "alice-uid", "email": "alice@example.com"}


def test_session_endpoint_sets_httponly_cookie(anonymous_client, monkeypatch):
    monkeypatch.setattr(auth_service, "create_session_cookie", lambda id_token: f"session-for-{id_token}")

    response = anonymous_client.post("/api/auth/session", json={"id_token": "abc"})

    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "token=session-for-abc" in cookie
    assert "HttpOnly" in cookie
    assert f"Max-Age={14 * 24 * 3600}" in cookie


def test_session_endpoint_rejects_bad_token(anonymous_client, monkeypatch):
    def reject(id_token):
        raise auth_service.AuthError("bad")

    monkeypatch.setattr(auth_service, "create_session_cookie", reject)

    assert anonymous_client.post("/api/auth/session", json={"id_token": "x"}).status_code == 401


def test_logout_clears_cookie(anonymous_client):
    response = anonymous_client.get("/logout", follow_redirects=False)

    assert response.headers["location"] == "/login?logged_out=1"
    assert 'token=""' in response.headers["set-cookie"]


@pytest.mark.parametrize(
    "claims, allowed",
    [
        ({"uid": "u", "email": "Alice@Example.com", "email_verified": True}, True),
        ({"uid": "u", "email": "alice@example.com", "email_verified": False}, False),
        ({"uid": "u", "email": "mallory@example.com", "email_verified": True}, False),
        ({"uid": "u", "email_verified": True}, False),
    ],
)
def test_session_cookie_requires_verified_allowlisted_email(monkeypatch, claims, allowed):
    monkeypatch.setattr(config, "ALLOWED_EMAILS", frozenset({"alice@example.com"}))
    monkeypatch.setattr(firebase_auth, "verify_session_cookie", lambda cookie: claims)

    if allowed:
        assert auth_service.verify_session_cookie("cookie").email == "alice@example.com"
    else:
        with pytest.raises(auth_service.AuthError):
            auth_service.verify_session_cookie("cookie")


def test_expired_session_cookie_is_an_auth_error(monkeypatch):
    def expired(cookie):
        raise firebase_auth.ExpiredSessionCookieError("expired", cause=None)

    monkeypatch.setattr(firebase_auth, "verify_session_cookie", expired)

    with pytest.raises(auth_service.AuthError):
        auth_service.verify_session_cookie("cookie")


def test_health(anonymous_client):
    assert anonymous_client.get("/api/health").json() == {"status": "ok"}
