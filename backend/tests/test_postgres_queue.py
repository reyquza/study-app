"""Concurrent claims against a real, migrated Postgres. SQLite has no row locks, so the
SKIP LOCKED path only runs here. Set POSTGRES_TEST_URL to run it (CI does)."""

import os
import threading
from collections import Counter

import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import sessionmaker

from app.models import GradingJob, Problem, User
from app.services import grading_jobs
from tests.factories import make_job


POSTGRES_TEST_URL = os.getenv("POSTGRES_TEST_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_TEST_URL, reason="POSTGRES_TEST_URL is not set")

WORKERS = 8
JOBS = 40


def _clear(factory) -> None:
    with factory() as db:
        db.execute(delete(GradingJob))
        db.execute(delete(Problem))
        db.execute(delete(User))
        db.commit()


@pytest.fixture
def pg_sessions():
    engine = create_engine(POSTGRES_TEST_URL)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    _clear(factory)
    yield factory
    _clear(factory)
    engine.dispose()


def test_concurrent_workers_claim_each_job_exactly_once(pg_sessions):
    with pg_sessions() as db:
        user = User(email="queue@example.com")
        db.add(user)
        db.commit()
        problem = Problem(user_id=user.id, problem_text="x")
        db.add(problem)
        db.commit()
        job_ids = {make_job(db, problem).id for _ in range(JOBS)}

    claims: Counter[int] = Counter()
    lock = threading.Lock()
    start = threading.Barrier(WORKERS)

    def worker() -> None:
        start.wait()
        while True:
            with pg_sessions() as db:
                claimed = grading_jobs.claim_next_job(db)
            if claimed is None:
                return
            with lock:
                claims[claimed.id] += 1

    threads = [threading.Thread(target=worker) for _ in range(WORKERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert set(claims) == job_ids
    assert max(claims.values()) == 1


def test_concurrent_guest_submissions_respect_the_minute_limit(pg_sessions, monkeypatch):
    """Twenty guests submit at once while each upload takes a moment: the advisory lock must
    let exactly the per-minute cap through, not everyone who passed the first check."""
    import asyncio

    from app import config
    from app.services import storage

    async def slow_upload(upload):
        await asyncio.sleep(0.2)
        return "https://res.cloudinary.com/demo/image/upload/answer.jpg"

    monkeypatch.setattr(storage, "store_upload", slow_upload)
    cap = config.GUEST_GRADING_LIMITS[0][1]
    submitters = 20

    with pg_sessions() as db:
        guests = [User(is_guest=True) for _ in range(submitters)]
        db.add_all(guests)
        db.commit()
        problems = [Problem(user_id=guest.id, problem_text="x") for guest in guests]
        db.add_all(problems)
        db.commit()
        pairs = [(guest.id, problem.id) for guest, problem in zip(guests, problems)]

    results: list[str] = []
    lock = threading.Lock()
    start = threading.Barrier(submitters)

    def submit(user_id: str, problem_id: int) -> None:
        start.wait()
        with pg_sessions() as db:
            try:
                asyncio.run(grading_jobs.enqueue_grading(
                    db, db.get(User, user_id), db.get(Problem, problem_id), upload=None))
                outcome = "queued"
            except grading_jobs.GradingLimitReached as exc:
                outcome = exc.scope
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=submit, args=pair) for pair in pairs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert Counter(results) == {"queued": cap, "guest": submitters - cap}
