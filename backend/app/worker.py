"""Background grading worker.

Claims jobs from the grading_jobs table, grades them, and records the result. It runs
inside the web process by default (see app.main) and can also run on its own:

    uv run python -m app.worker

Any number of workers can run at once, in one process or many: claims go through
SELECT ... FOR UPDATE SKIP LOCKED, so no job is graded twice.
"""

import asyncio
import logging
import signal

from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session, sessionmaker

from app import config
from app.grading.base import Grader, GradingFailed, GradingRequest, GradingResult
from app.services import grading_jobs, storage
from app.services.grading_jobs import ClaimedJob


logger = logging.getLogger(__name__)

# Failures worth retrying quietly; anything else is a bug and gets a traceback in the log.
EXPECTED_ERRORS = (GradingFailed, storage.FileFetchFailed)

# The worker running in this process, if any, so a new job can wake it without waiting for the next poll.
_local_worker: "GradingWorker | None" = None


def notify_new_job() -> None:
    if _local_worker is not None:
        _local_worker.wake()


class GradingWorker:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        grader: Grader,
        concurrency: int = config.WORKER_CONCURRENCY,
        poll_interval: float = config.WORKER_POLL_INTERVAL_SECONDS,
    ):
        self._session_factory = session_factory
        self._grader = grader
        self._concurrency = concurrency
        self._poll_interval = poll_interval
        self._wakeup = asyncio.Event()

    def wake(self) -> None:
        self._wakeup.set()

    async def run(self, stop: asyncio.Event) -> None:
        logger.info("Grading worker started (%s slots, model %s)", self._concurrency, self._grader.model_name)
        await asyncio.gather(*(self._slot(stop) for _ in range(self._concurrency)))
        logger.info("Grading worker stopped")

    async def _slot(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                worked = await self.run_once()
            except Exception:
                # A database hiccup must not kill the worker; back off and try again.
                logger.exception("Worker iteration failed")
                worked = False
            if not worked:
                self._wakeup.clear()
                try:
                    await asyncio.wait_for(self._wakeup.wait(), timeout=self._poll_interval)
                except TimeoutError:
                    pass

    async def run_once(self) -> bool:
        """Claim and process one job. Returns False if there was nothing to do."""
        claimed = await run_in_threadpool(self._with_session, grading_jobs.claim_next_job)
        if claimed is None:
            return False

        if claimed.attempt > config.GRADING_MAX_ATTEMPTS:
            # Its earlier workers all stopped responding mid-grading.
            await run_in_threadpool(
                self._with_session, grading_jobs.fail_attempt, claimed, "lease expired on the last attempt"
            )
            return True

        try:
            result = await self._grade(claimed)
        except Exception as exc:
            if not isinstance(exc, EXPECTED_ERRORS):
                logger.exception("Unexpected error grading job %s", claimed.id)
            await run_in_threadpool(
                self._with_session, grading_jobs.fail_attempt, claimed, f"{type(exc).__name__}: {exc}"
            )
            return True

        await run_in_threadpool(
            self._with_session, grading_jobs.complete_job, claimed, result, self._grader.model_name
        )
        return True

    async def _grade(self, claimed: ClaimedJob) -> GradingResult:
        answer = await storage.fetch_stored_file(claimed.answer_image_path)
        problem_file = (
            await storage.fetch_stored_file(claimed.problem_image_path)
            if claimed.problem_image_path
            else None
        )
        # The model SDK is blocking; a thread keeps the event loop (and the web app) responsive.
        return await run_in_threadpool(
            self._grader.grade,
            GradingRequest(answer=answer, problem_text=claimed.problem_text, problem_file=problem_file),
        )

    def _with_session(self, fn, *args):
        with self._session_factory() as db:
            return fn(db, *args)


async def start_local_worker(session_factory: sessionmaker[Session], grader: Grader) -> tuple[asyncio.Event, asyncio.Task]:
    """Run a worker as a task in the current event loop (used by the web app's lifespan)."""
    global _local_worker
    _local_worker = GradingWorker(session_factory, grader)
    stop = asyncio.Event()
    return stop, asyncio.create_task(_local_worker.run(stop))


async def _main() -> None:
    from app.database import SessionLocal
    from app.grading.gemini import get_grader

    storage.init_storage()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows; Ctrl+C still raises KeyboardInterrupt
            pass
    await GradingWorker(SessionLocal, get_grader()).run(stop)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
