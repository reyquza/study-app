from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.config import SESSION_COOKIE_NAME
from app.database import get_db
from app.models import User
from app.security import decode_session_token


class NotAuthenticated(Exception):
    """Handled in app.main: pages redirect to /login, /api/* routes return 401."""


def get_optional_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User | None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    user_id = decode_session_token(token) if token else None
    user = db.get(User, user_id) if user_id else None
    # Lets base.html show who is signed in.
    request.state.user = user
    return user


def get_current_user(user: User | None = Depends(get_optional_user)) -> User:
    if user is None:
        raise NotAuthenticated()
    return user
