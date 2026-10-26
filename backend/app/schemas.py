"""Request/response bodies of the JSON API (/api/*). These are the contract a separate
frontend codes against."""

from pydantic import BaseModel, ConfigDict


class SessionRequest(BaseModel):
    # Firebase ID token obtained in the browser right after Google sign-in.
    id_token: str


class StatusResponse(BaseModel):
    status: str


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str | None
