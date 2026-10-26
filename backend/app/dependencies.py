import logging

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.config import SESSION_COOKIE_NAME
from app.database import get_db
from app.models import User
from app.services import auth as auth_service
from app.services.users import get_or_create_user


logger = logging.getLogger(__name__)


class NotAuthenticated(Exception):
    """Handled in app.main: pages redirect to /login, /api/* routes return 401."""


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    session_cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_cookie:
        raise NotAuthenticated()

    try:
        identity = auth_service.verify_session_cookie(session_cookie)
    except auth_service.AuthError as exc:
        logger.info("Rejected session: %s", exc)
        raise NotAuthenticated() from exc

    user = get_or_create_user(db, identity)
    # Lets base.html show who is signed in.
    request.state.user = user
    return user
