"""Server-rendered pages (Jinja2).

This is the interim UI until the Next.js frontend lands. Routes here only parse the
request, call app.services and render a template, so replacing them with JSON
endpoints for the frontend does not touch any business logic.
"""

from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import clock, config, worker
from app.database import get_db
from app.dependencies import get_current_user, get_optional_user
from app.models import JobStatus, User
from app.security import clear_session_cookie
from app.services import grading_jobs
from app.services import plans as plan_service
from app.services import problems as problem_service
from app.services import storage
from app.services import submissions as submission_service


WEB_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=WEB_DIR / "templates")

router = APIRouter(include_in_schema=False)


def _redirect(url: str) -> RedirectResponse:
    # 303 makes the browser follow with a GET, which is what we want after a form POST.
    return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)


def _parse_day(raw: str) -> date | None:
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


@router.get("/login")
def login_page(request: Request, user: User | None = Depends(get_optional_user)):
    # Guests may open it to create an account; everyone else signed in goes home.
    if user is not None and not user.is_guest:
        return _redirect("/")
    return templates.TemplateResponse(request, "login.html")


@router.get("/logout")
def logout():
    response = _redirect("/login")
    clear_session_cookie(response)
    return response


@router.get("/")
def home(
    request: Request,
    error: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    today = clock.local_today()
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "today": today,
            "calendar_data": plan_service.upcoming_incomplete_plans(db, user.id, today),
            "error_message": "本日のAI採点の上限に達しました。また明日ご利用ください!" if error == "limit_reached" else None,
        },
    )


@router.get("/schedule")
def schedule(
    request: Request,
    year: int | None = None,
    month: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    today = clock.local_today()
    if year is None or month is None or not 1 <= month <= 12:
        year, month = today.year, today.month

    return templates.TemplateResponse(
        request,
        "schedule.html",
        {"view": plan_service.month_view(db, user.id, year, month), "today": today},
    )


@router.get("/schedule/{day}")
def daily_todo(
    request: Request,
    day: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    target_day = _parse_day(day)
    if target_day is None:
        return _redirect("/schedule")

    return templates.TemplateResponse(
        request,
        "daily_todo.html",
        {"view": plan_service.day_view(db, user.id, target_day)},
    )


@router.get("/add-task/{day}")
def add_task_page(
    request: Request,
    day: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    target_day = _parse_day(day)
    if target_day is None:
        return _redirect("/schedule")

    return templates.TemplateResponse(
        request,
        "add_task.html",
        {
            "date": target_day,
            "problems": problem_service.list_recent_problems(db, user.id, config.RECENT_PROBLEMS_LIMIT),
        },
    )


@router.post("/add-task/{day}")
def add_task(
    day: str,
    problem_ids: list[int] = Form(default=[]),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    target_day = _parse_day(day)
    if target_day is None:
        return _redirect("/schedule")

    plan_service.add_plans(db, user.id, problem_ids, target_day)
    return _redirect(f"/schedule/{target_day.isoformat()}")


@router.post("/delete-task/{plan_id}")
def delete_task(
    request: Request,
    plan_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    plan_service.delete_plan(db, user.id, plan_id)
    # Go back to the page the delete button was on (home or a day view). Only the path is
    # kept so a forged Referer cannot redirect to another site.
    referer = urlsplit(request.headers.get("referer", "/"))
    back_to = referer.path + (f"?{referer.query}" if referer.query else "")
    return _redirect(back_to if back_to.startswith("/") and not back_to.startswith("//") else "/")


@router.get("/confirm-problem/{problem_id}")
def problem_detail(
    request: Request,
    problem_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    problem = problem_service.get_user_problem(db, user.id, problem_id)
    if problem is None:
        return _redirect("/")

    return templates.TemplateResponse(
        request,
        "confirm-problem.html",
        {
            "problem": problem,
            "submissions": submission_service.list_problem_submissions(db, problem.id),
        },
    )


@router.get("/register-problem")
def register_problem_page(
    request: Request,
    user: User = Depends(get_current_user),
):
    return templates.TemplateResponse(request, "register_problem.html")


@router.post("/register-problem")
async def register_problem(
    image: UploadFile = File(...),
    text: str | None = Form(None),
    source: str | None = Form(None),
    tags: str | None = Form(None),
    scheduled_date: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        upload = await storage.read_validated_upload(image)
    except storage.UploadRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)

    problem_service.create_problem(
        db,
        user_id=user.id,
        image_url=await storage.store_upload(upload),
        text=text,
        source=source,
        tag_names=problem_service.parse_tag_names(tags),
        scheduled_date=_parse_day(scheduled_date) if scheduled_date else None,
    )
    return _redirect("/")


@router.get("/solve/{problem_id}")
def solve_page(
    request: Request,
    problem_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    problem = problem_service.get_user_problem(db, user.id, problem_id)
    if problem is None:
        return _redirect("/")

    return templates.TemplateResponse(request, "solve.html", {"problem": problem})


@router.post("/submit-answer/{problem_id}")
async def submit_answer(
    problem_id: int,
    answer_image: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    problem = problem_service.get_user_problem(db, user.id, problem_id)
    if problem is None:
        return _redirect("/")
    if not problem.problem_image_path and not problem.problem_text:
        return _redirect(f"/solve/{problem_id}")

    try:
        upload = await storage.read_validated_upload(answer_image)
        job = await grading_jobs.enqueue_grading(db, user.id, problem, upload)
    except storage.UploadRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    except grading_jobs.DailyLimitReached:
        return _redirect("/?error=limit_reached")

    worker.notify_new_job()
    return _redirect(f"/grading/{job.id}")


@router.get("/grading/{job_id}")
def grading_page(
    request: Request,
    job_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Waiting page. It polls /api/grading-jobs/{id} and moves on to the result when ready."""
    job = grading_jobs.get_user_job(db, user.id, job_id)
    if job is None:
        return _redirect("/")
    if job.status == JobStatus.SUCCEEDED:
        return _redirect(f"/answer/{job.submission_id}")

    return templates.TemplateResponse(request, "grading.html", {"job": job, "problem": job.problem})


@router.get("/answer/{submission_id}")
def answer_page(
    request: Request,
    submission_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    submission = submission_service.get_user_submission(db, user.id, submission_id)
    if submission is None:
        return _redirect("/")

    return templates.TemplateResponse(
        request,
        "answer.html",
        {"submission": submission, "problem": submission.problem},
    )


@router.post("/update-memo/{submission_id}")
def update_memo(
    submission_id: int,
    mistake: str = Form(""),
    user_memo: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    submission_service.save_reflection(db, user.id, submission_id, mistake, user_memo)
    return _redirect("/")
