"""Accounts: email + password sign-up and login, and guest accounts that need neither."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import User
from app.security import DUMMY_PASSWORD_HASH, hash_password, verify_password


class EmailTaken(Exception):
    pass


class InvalidCredentials(Exception):
    pass


def normalize_email(email: str) -> str:
    return email.strip().lower()


def sign_up(db: Session, email: str, password: str, guest: User | None = None) -> User:
    """Create an account. A signed-in guest is upgraded in place, so their problems and
    results carry over."""
    email = normalize_email(email)
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
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None or user.password_hash is None:
        verify_password(password, DUMMY_PASSWORD_HASH)
        raise InvalidCredentials()
    if not verify_password(password, user.password_hash):
        raise InvalidCredentials()
    return user


def create_guest(db: Session) -> User:
    user = User(is_guest=True)
    db.add(user)
    db.commit()
    return user
