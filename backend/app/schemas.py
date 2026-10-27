"""Request/response bodies of the JSON API (/api/*). These are the contract a separate
frontend codes against."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.config import PASSWORD_MIN_LENGTH
from app.models import JobStatus


class SignUpRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)


class StatusResponse(BaseModel):
    status: str


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str | None
    is_guest: bool


class GradingJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    problem_id: int
    status: JobStatus
    attempts: int
    # Set once the job has succeeded; the graded result is /answer/{submission_id}.
    submission_id: int | None
    created_at: datetime
    finished_at: datetime | None
