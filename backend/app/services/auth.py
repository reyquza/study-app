"""Accounts: email + password sign-up and login, and guest accounts that need neither.

Abuse limits live in the database, so they hold across restarts and processes: failed
logins are counted on the account, and new accounts and guests are counted by created_at.
"""

from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import config
from app.database import lock_limits
from app.models import User
from app.security import DUMMY_PASSWORD_HASH, hash_password, verify_password


class EmailTaken(Exception):
    pass


class InvalidCredentials(Exception):
    pass


class TooManyAttempts(Exception):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(moment: datetime) -> datetime:
    # SQLite returns naive datetimes; they are stored in UTC.
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _check_creation_rate(db: Session, guests: bool) -> None:
    """Raise TooManyAttempts if too many guests (or accounts) were created just now. Holds the
    limits lock until the caller commits, so concurrent requests cannot all pass."""
    limit, window = config.NEW_GUEST_LIMIT if guests else config.NEW_ACCOUNT_LIMIT
    lock_limits(db)
    recent = db.scalar(
        select(func.count())
        .select_from(User)
        .where(User.is_guest.is_(guests), User.created_at >= _utcnow() - window)
    )
    if (recent or 0) >= limit:
        db.rollback()
        raise TooManyAttempts()


def sign_up(db: Session, email: str, password: str, guest: User | None = None) -> User:
    """Create an account. A signed-in guest is upgraded in place, so their problems and
    results carry over. Raises EmailTaken or TooManyAttempts."""
    email = normalize_email(email)
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise EmailTaken()

    password_hash = hash_password(password)  # slow, so done before taking the lock
    _check_creation_rate(db, guests=False)
    user = guest if guest is not None and guest.is_guest else User()
    user.email = email
    user.password_hash = password_hash
    user.is_guest = False
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:  # a concurrent sign-up took the email after our check
        db.rollback()
        raise EmailTaken() from exc
    return user


def authenticate(db: Session, email: str, password: str) -> User:
    """Raises InvalidCredentials, or TooManyAttempts while the account is locked after
    repeated failures (checked first, so a locked account costs no password hashing)."""
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    now = _utcnow()
    if user is not None and user.locked_until is not None and _as_utc(user.locked_until) > now:
        raise TooManyAttempts()
    if user is None or user.password_hash is None:
        verify_password(password, DUMMY_PASSWORD_HASH)
        raise InvalidCredentials()
    if not verify_password(password, user.password_hash):
        _record_failed_login(db, user.id, now)
        raise InvalidCredentials()
    if user.failed_logins or user.locked_until is not None:
        user.failed_logins = 0
        user.locked_until = None
        db.commit()
    return user


def _record_failed_login(db: Session, user_id: str, now: datetime) -> None:
    max_failures, lock_for = config.LOGIN_FAILURE_LIMIT
    # Incremented in SQL, so concurrent failures are all counted.
    failures = db.scalar(
        update(User)
        .where(User.id == user_id)
        .values(failed_logins=User.failed_logins + 1)
        .returning(User.failed_logins)
    )
    if failures is not None and failures >= max_failures:
        db.execute(update(User).where(User.id == user_id).values(failed_logins=0, locked_until=now + lock_for))
    db.commit()


def create_guest(db: Session) -> User:
    """Raises TooManyAttempts when guests are being created too fast."""
    _check_creation_rate(db, guests=True)
    user = User(is_guest=True)
    db.add(user)
    db.commit()
    return user
