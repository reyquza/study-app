from fastapi import APIRouter, HTTPException, Response, status

from app.config import IS_PRODUCTION, SESSION_COOKIE_NAME, SESSION_LIFETIME
from app.schemas import SessionRequest, StatusResponse
from app.services import auth as auth_service


router = APIRouter(
    prefix="/api/auth",
    tags=["auth"],
)


def set_session_cookie(response: Response, session_cookie: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_cookie,
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


@router.post("/session", response_model=StatusResponse)
def create_session(body: SessionRequest, response: Response) -> StatusResponse:
    try:
        session_cookie = auth_service.create_session_cookie(body.id_token)
    except auth_service.AuthError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid ID token",
        )

    set_session_cookie(response, session_cookie)
    return StatusResponse(status="ok")


@router.post("/logout", response_model=StatusResponse)
def logout(response: Response) -> StatusResponse:
    clear_session_cookie(response)
    return StatusResponse(status="ok")
