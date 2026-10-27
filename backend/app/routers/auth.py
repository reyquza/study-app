from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_optional_user
from app.models import User
from app.schemas import LoginRequest, SignUpRequest, StatusResponse, UserResponse
from app.security import clear_session_cookie, set_session_cookie
from app.services import auth as auth_service


router = APIRouter(
    prefix="/api/auth",
    tags=["auth"],
)


@router.post("/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def sign_up(
    body: SignUpRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_optional_user),
) -> User:
    try:
        user = auth_service.sign_up(db, body.email, body.password, guest=current_user)
    except auth_service.EmailTaken:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists",
        )

    set_session_cookie(response, user.id)
    return user


@router.post("/login", response_model=UserResponse)
def login(body: LoginRequest, response: Response, db: Session = Depends(get_db)) -> User:
    try:
        user = auth_service.authenticate(db, body.email, body.password)
    except auth_service.InvalidCredentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )

    set_session_cookie(response, user.id)
    return user


@router.post("/guest", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def start_guest_session(response: Response, db: Session = Depends(get_db)) -> User:
    user = auth_service.create_guest(db)
    set_session_cookie(response, user.id)
    return user


@router.post("/logout", response_model=StatusResponse)
def logout(response: Response) -> StatusResponse:
    clear_session_cookie(response)
    return StatusResponse(status="ok")
