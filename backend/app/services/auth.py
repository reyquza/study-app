"""Firebase Authentication: Google sign-in happens in the browser; the backend only
verifies tokens and manages a long-lived, httpOnly session cookie."""

import json
from dataclasses import dataclass

import firebase_admin
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials
from firebase_admin import exceptions as firebase_exceptions

from app import config


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class Identity:
    uid: str
    email: str


def init_firebase() -> None:
    try:
        firebase_admin.get_app()
    except ValueError:
        firebase_admin.initialize_app(_load_credentials())


def _load_credentials() -> credentials.Certificate:
    if config.FIREBASE_CREDENTIALS_JSON:
        return credentials.Certificate(json.loads(config.FIREBASE_CREDENTIALS_JSON))
    return credentials.Certificate(str(config.FIREBASE_CREDENTIALS_PATH))


def create_session_cookie(id_token: str) -> str:
    """Exchange a fresh ID token from the browser (valid for 1 hour) for a session cookie."""
    try:
        firebase_auth.verify_id_token(id_token)
        return firebase_auth.create_session_cookie(id_token, expires_in=config.SESSION_LIFETIME)
    except (ValueError, firebase_exceptions.FirebaseError) as exc:
        raise AuthError("invalid ID token") from exc


def verify_session_cookie(session_cookie: str) -> Identity:
    try:
        claims = firebase_auth.verify_session_cookie(session_cookie)
    except (ValueError, firebase_exceptions.FirebaseError) as exc:
        raise AuthError("invalid or expired session cookie") from exc

    email = (claims.get("email") or "").lower()
    if not email or not claims.get("email_verified"):
        raise AuthError("account has no verified email")
    if email not in config.ALLOWED_EMAILS:
        raise AuthError(f"email is not on the allowlist: {email}")

    return Identity(uid=claims["uid"], email=email)
