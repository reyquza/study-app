"""Password hashing (Argon2) and the signed session token kept in an httpOnly cookie."""

import threading
from datetime import datetime, timezone

import jwt
from fastapi import Response
from jwt.exceptions import InvalidTokenError
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError

from app.config import (
    IS_PRODUCTION,
    JWT_ALGORITHM,
    JWT_SECRET_KEY,
    MAX_CONCURRENT_PASSWORD_HASHES,
    SESSION_COOKIE_NAME,
    SESSION_LIFETIME,
)


password_hasher = PasswordHash.recommended()
# Each Argon2 hash takes 64 MiB. Sync routes run in a thread pool of up to 40 threads, so
# without a bound a burst of logins could take gigabytes on a 512 MB server.
_hash_slots = threading.BoundedSemaphore(MAX_CONCURRENT_PASSWORD_HASHES)
# Verified against when the email is unknown, so a failed login takes the same time
# whether or not the account exists.
DUMMY_PASSWORD_HASH = password_hasher.hash("dummy-password-for-timing-safety")


def hash_password(password: str) -> str:
    with _hash_slots:
        return password_hasher.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    try:
        with _hash_slots:
            return password_hasher.verify(password, hashed_password)
    except UnknownHashError:
        return False


def create_session_token(user_id: str) -> str:
    issued_at = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": user_id, "iat": issued_at, "exp": issued_at + SESSION_LIFETIME},
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )


def decode_session_token(token: str) -> str | None:
    """Return the user ID, or None if the token is invalid or expired."""
    try:
        payload = jwt.decode(
            token,
            JWT_SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "exp"]},
        )
    except InvalidTokenError:
        return None
    subject = payload.get("sub")
    return subject if isinstance(subject, str) and subject else None


def set_session_cookie(response: Response, user_id: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=create_session_token(user_id),
        max_age=int(SESSION_LIFETIME.total_seconds()),
        path="/",
        httponly=True,  # not readable from JavaScript, which limits the damage of XSS
        secure=IS_PRODUCTION,  # HTTPS only in production
        samesite="lax",  # not sent on cross-site POSTs, which blocks CSRF
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=IS_PRODUCTION,
        samesite="lax",
    )
