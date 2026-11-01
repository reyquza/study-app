from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app import config, worker
from app.database import get_db
from app.dependencies import get_current_user
from app.grading import availability
from app.models import GradingJob, User
from app.schemas import GradingJobResponse
from app.services import grading_jobs
from app.services import problems as problem_service
from app.services import storage


LIMIT_MESSAGES = {
    "user": "Daily grading limit reached",
    "guest": "Guest grading is busy. Try again later, or sign up to keep grading",
    "global": "Grading is at capacity for today",
}

router = APIRouter(
    prefix="/api",
    tags=["grading"],
)


@router.post(
    "/problems/{problem_id}/grading-jobs",
    response_model=GradingJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_answer(
    problem_id: int,
    answer_image: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> GradingJob:
    """Queue an answer for grading. Poll GET /api/grading-jobs/{id} for the result."""
    problem = problem_service.get_user_problem(db, user.id, problem_id)
    if problem is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Problem not found")
    if not problem.problem_image_path and not problem.problem_text:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Problem has no statement to grade against")
    if availability.is_unavailable():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"The grading model {config.GEMINI_MODEL} is no longer available",
        )

    try:
        upload = await storage.read_validated_upload(answer_image)
        job = await grading_jobs.enqueue_grading(db, user, problem, upload)
    except storage.UploadRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    except grading_jobs.GradingLimitReached as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=LIMIT_MESSAGES[exc.scope])

    worker.notify_new_job()
    return job


@router.get("/grading-jobs/{job_id}", response_model=GradingJobResponse)
def read_grading_job(
    job_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> GradingJob:
    job = grading_jobs.get_user_job(db, user.id, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grading job not found")
    return job
