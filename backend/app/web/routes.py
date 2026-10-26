"""Server-rendered pages (Jinja2).

This is the interim UI until the Next.js frontend lands. Routes here only parse the
request, call app.services and render a template, so replacing them with JSON
endpoints for the frontend does not touch any business logic.
"""

import logging
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import clock, config
from app.database import get_db
from app.dependencies import get_current_user
from app.grading.base import Grader, GradingFailed
from app.grading.gemini import get_grader
from app.models import User
from app.routers.auth import clear_session_cookie
from app.services import plans as plan_service
from app.services import problems as problem_service
from app.services import storage
from app.services import submissions as submission_service


logger = logging.getLogger(__name__)

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
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html")


@router.get("/logout")
def logout():
    # ?logged_out=1 tells the login page to sign out of Firebase in the browser too.
    response = _redirect("/login?logged_out=1")
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
    grader: Grader = Depends(get_grader),
):
    problem = problem_service.get_user_problem(db, user.id, problem_id)
    if problem is None:
        return _redirect("/")
    if not problem.problem_image_path and not problem.problem_text:
        return _redirect(f"/solve/{problem_id}")

    try:
        upload = await storage.read_validated_upload(answer_image)
        submission = await submission_service.grade_submission(db, grader, user.id, problem, upload)
    except storage.UploadRejected as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    except submission_service.DailyLimitReached:
        return _redirect("/?error=limit_reached")
    except storage.FileFetchFailed as exc:
        logger.warning("Could not fetch problem file for problem %s: %s", problem_id, exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="問題画像の取得に失敗しました。")
    except GradingFailed as exc:
        logger.warning("Grading failed for problem %s: %s", problem_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="AIによる採点に失敗しました。時間をおいて再度お試しください。",
        )

    return _redirect(f"/answer/{submission.id}")


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
