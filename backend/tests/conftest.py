import asyncio
from collections.abc import Callable, Generator
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app import config
from app.config import SESSION_COOKIE_NAME
from app.database import Base, get_db
from app.grading.base import GradingFailed, GradingRequest, GradingResult
from app.main import app
from app.models import User
from app.security import create_session_token
from app.services import auth as auth_service
from app.services import storage
from app.worker import GradingWorker


@dataclass
class FakeGrader:
    model_name: str = "fake-grader"
    score: int = 70
    # The next `failures` calls raise GradingFailed, like a model outage.
    failures: int = 0
    retryable: bool = True
    requests: list[GradingRequest] = field(default_factory=list)

    def grade(self, request: GradingRequest) -> GradingResult:
        self.requests.append(request)
        if self.failures > 0:
            self.failures -= 1
            raise GradingFailed("fake outage", retryable=self.retryable)
        return GradingResult(score=self.score, ai_feedback="feedback", explanation="model answer")


@pytest.fixture(autouse=True)
def reset_account_limits():
    """The account limiters keep state in memory; start every test from zero."""
    auth_service.reset_limits()
    yield
    auth_service.reset_limits()


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
def stored_files(monkeypatch) -> dict[str, bytes]:
    """Replaces Cloudinary with a dict of URL -> bytes. Files not uploaded in the test
    (e.g. problem images made by factories) read back as b"problem-image"."""
    files: dict[str, bytes] = {}

    async def fake_store(upload: storage.ValidatedUpload) -> str:
        url = f"https://res.cloudinary.com/demo/image/upload/{len(files)}.jpg"
        files[url] = upload.file.data
        return url

    async def fake_fetch(url: str) -> storage.FilePart:
        return storage.FilePart(data=files.get(url, b"problem-image"), mime_type="image/jpeg")

    monkeypatch.setattr(storage, "store_upload", fake_store)
    monkeypatch.setattr(storage, "fetch_stored_file", fake_fetch)
    return files


@pytest.fixture
def anonymous_client(session_factory, monkeypatch) -> Generator[TestClient, None, None]:
    monkeypatch.setattr(storage, "init_storage", lambda: None)
    # Tests drive the worker themselves through `run_jobs`.
    monkeypatch.setattr(config, "RUN_WORKER_IN_PROCESS", False)

    def override_get_db() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def client(anonymous_client, users, stored_files) -> TestClient:
    """A client signed in as alice. Call `login_as(client, "bob")` to switch users."""
    login_as(anonymous_client, "alice")
    return anonymous_client


@pytest.fixture
def run_jobs(session_factory, grader) -> Callable[[], int]:
    """Process every runnable grading job, as the background worker would. Returns how many ran."""
    worker = GradingWorker(session_factory, grader, concurrency=1)

    def run() -> int:
        count = 0
        while asyncio.run(worker.run_once()):
            count += 1
        return count

    return run


def login_as(client: TestClient, name: str) -> None:
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token(f"{name}-uid"))
