"""Accounts: email + password sign-up and login, and guest accounts that need neither."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import config
from app.models import User
from app.ratelimit import SlidingWindowLimiter
from app.security import DUMMY_PASSWORD_HASH, hash_password, verify_password


class EmailTaken(Exception):
    pass


class InvalidCredentials(Exception):
    pass


class TooManyAttempts(Exception):
    pass


# Failed logins per email, which stops password guessing against one account. New guests and
# new accounts across everyone, which stops scripts from filling the database.
login_failures = SlidingWindowLimiter(config.LOGIN_FAILURE_LIMIT)
new_guests = SlidingWindowLimiter(config.NEW_GUEST_LIMIT)
new_accounts = SlidingWindowLimiter(config.NEW_ACCOUNT_LIMIT)


def reset_limits() -> None:
    for limiter in (login_failures, new_guests, new_accounts):
        limiter.clear()


def normalize_email(email: str) -> str:
    return email.strip().lower()


def sign_up(db: Session, email: str, password: str, guest: User | None = None) -> User:
    """Create an account. A signed-in guest is upgraded in place, so their problems and
    results carry over. Raises EmailTaken or TooManyAttempts."""
    email = normalize_email(email)
    if not new_accounts.try_hit():
        raise TooManyAttempts()
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise EmailTaken()

    user = guest if guest is not None and guest.is_guest else User()
    user.email = email
    user.password_hash = hash_password(password)
    user.is_guest = False
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:  # a concurrent sign-up took the email after our check
        db.rollback()
        raise EmailTaken() from exc
    return user


def authenticate(db: Session, email: str, password: str) -> User:
    """Raises InvalidCredentials, or TooManyAttempts while the email is locked after repeated
    failures (checked first, so a locked account costs no password hashing)."""
    email = normalize_email(email)
    if login_failures.is_limited(email):
        raise TooManyAttempts()
    user = db.scalar(select(User).where(User.email == email))
    if user is None or user.password_hash is None:
        verify_password(password, DUMMY_PASSWORD_HASH)
        login_failures.hit(email)
        raise InvalidCredentials()
    if not verify_password(password, user.password_hash):
        login_failures.hit(email)
        raise InvalidCredentials()
    return user


def create_guest(db: Session) -> User:
    """Raises TooManyAttempts when guests are being created too fast."""
    if not new_guests.try_hit():
        raise TooManyAttempts()
    user = User(is_guest=True)
    db.add(user)
    db.commit()
    return user
