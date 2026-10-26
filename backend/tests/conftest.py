from collections.abc import Generator
from dataclasses import dataclass, field

import pytest
from fastapi import Depends, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base, get_db
from app.dependencies import get_current_user
from app.grading.base import GradingFailed, GradingRequest, GradingResult
from app.grading.gemini import get_grader
from app.main import app
from app.models import User
from app.services import auth as auth_service
from app.services import storage


STORED_URL = "https://res.cloudinary.com/demo/image/upload/answer.jpg"


@dataclass
class FakeGrader:
    model_name: str = "fake-grader"
    score: int = 70
    fail: bool = False
    requests: list[GradingRequest] = field(default_factory=list)

    def grade(self, request: GradingRequest) -> GradingResult:
        self.requests.append(request)
        if self.fail:
            raise GradingFailed("fake outage")
        return GradingResult(score=self.score, ai_feedback="feedback", explanation="model answer")


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def db(session_factory) -> Generator[Session, None, None]:
    with session_factory() as session:
        yield session


@pytest.fixture
def users(db) -> dict[str, User]:
    alice = User(id="alice-uid", email="alice@example.com")
    bob = User(id="bob-uid", email="bob@example.com")
    db.add_all([alice, bob])
    db.commit()
    return {"alice": alice, "bob": bob}


@pytest.fixture
def grader() -> FakeGrader:
    return FakeGrader()


@pytest.fixture
def stored_files(monkeypatch) -> list[bytes]:
    """Replaces Cloudinary: uploads are recorded instead of sent."""
    uploaded: list[bytes] = []

    async def fake_store(upload: storage.ValidatedUpload) -> str:
        uploaded.append(upload.file.data)
        return STORED_URL

    async def fake_fetch(url: str) -> storage.FilePart:
        return storage.FilePart(data=b"problem-image", mime_type="image/jpeg")

    monkeypatch.setattr(storage, "store_upload", fake_store)
    monkeypatch.setattr(storage, "fetch_stored_file", fake_fetch)
    return uploaded


@pytest.fixture
def anonymous_client(session_factory, monkeypatch) -> Generator[TestClient, None, None]:
    monkeypatch.setattr(auth_service, "init_firebase", lambda: None)
    monkeypatch.setattr(storage, "init_storage", lambda: None)

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def client(anonymous_client, session_factory, users, grader, stored_files) -> TestClient:
    """A client signed in as alice. Call `login_as(client, "bob")` to switch users."""
    login_as(anonymous_client, "alice")
    app.dependency_overrides[get_grader] = lambda: grader
    return anonymous_client


def login_as(client: TestClient, name: str) -> None:
    uid = f"{name}-uid"

    def override_current_user(request: Request, db: Session = Depends(get_db)) -> User:
        user = db.get(User, uid)
        request.state.user = user
        return user

    app.dependency_overrides[get_current_user] = override_current_user
