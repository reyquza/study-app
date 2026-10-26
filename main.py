import calendar
import os
from collections import defaultdict
from datetime import datetime, timedelta, date, time
from typing import Optional, List
from urllib.parse import urlparse

import cloudinary
import cloudinary.uploader
import requests
import firebase_admin
from firebase_admin import credentials, auth
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Depends, Form, status, UploadFile, File, Response, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker, Session

import models


""" ローカルか本番か """
IS_PRODUCTION = os.getenv("RENDER") is not None

""" 環境変数 """
load_dotenv()
DATABASE_URL = os.getenv('DATABASE_URL')

if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    
SECRETKEY = os.getenv('SECRETKEY')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

WHITELISTS = [
    email.strip().lower()
    for email in os.getenv("WHITELISTS", "").split(",")
    if email
]

ALLOWED_CONTENT_TYPES = {"image/png",
                         "image/jpeg",
                         "application/pdf",
                         "image/webp", 
                         "image/heic", 
                         "image/heif"
                         }

MAX_FILE_SIZE = 10 * 1024 * 1024

""" 初期設定 """
app = FastAPI()
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

origins = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "https://NextLearn.onrender.com"
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,       
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"], 
)

app.add_middleware(SessionMiddleware, secret_key=SECRETKEY, https_only=IS_PRODUCTION)

@app.middleware("http")
async def combined_middleware(request: Request, call_next):
    request.state.user = None

    response = await call_next(request)
    
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    
    return response

client = genai.Client(api_key=GEMINI_API_KEY)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

cred = credentials.Certificate("firebase-credentials.json")
firebase_admin.initialize_app(cred)

cloudinary.config(
  cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME"),
  api_key = os.getenv("CLOUDINARY_API_KEY"),
  api_secret = os.getenv("CLOUDINARY_API_SECRET")
)

DAILY_LIMIT = 5
fetch_limit = 30
SESSION_LIFETIME = timedelta(days=14)  # Firebaseのセッションクッキーの上限は2週間


# """ 動作確認用 """
# models.Base.metadata.create_all(bind=engine)

""" define the format """
class Reply(BaseModel):
    score: int = Field(description="100点満点での採点結果(部分点考慮)")
    ai_feedback: str = Field(
            description="生徒への具体的なアドバイス。どこで間違えたか、どう考えればよかったのかを解説してください。数式を書く際は必ずLaTeX形式とし、インライン数式は $ で、ブロック数式は $$ で囲んでください。",
            max_length=500,
        )
    explanation: str = Field(
        description="問題の模範解答と、本番での解法戦略(部分点の狙い方など)の解説。数式を書く際は必ずLaTeX形式とし、インライン数式は $ で、ブロック数式は $$ で囲んでください。",
    )
    
class TokenRequest(BaseModel):
    token: str
    
    
""" Dependency Functions """

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        
        
def get_prev_next_month(year, month):
    previous_year = year - 1 if month == 1 else year
    previous_month = 12 if month == 1 else month - 1
    next_year = year + 1 if month == 12 else year
    next_month = 1 if month == 12 else month + 1
    return previous_year, previous_month, next_year, next_month


class NotAuthenticatedException(Exception):
    pass

@app.exception_handler(NotAuthenticatedException)
def auth_exception_handler(request: Request, exc: NotAuthenticatedException):
    # 入場券がない（未ログイン）場合は、強制的にログイン画面に飛ばす
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)


# Cookieのトークンを確認する係
def get_current_user(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("token")
    if not token:
        raise NotAuthenticatedException()
    
    try:
        # IDトークン(1時間で失効)ではなく、/set-cookieで発行したセッションCookieを検証する
        decoded_token = auth.verify_session_cookie(token)
        uid = decoded_token.get('uid')
        email = decoded_token.get('email')
        email_verified = decoded_token.get('email_verified')


        # リストにないメールアドレスの場合はエラーにして弾く
        if not email or email.lower() not in WHITELISTS or not email_verified:
            print(f"不正なログイン試行をブロックしました: {email}")
            raise NotAuthenticatedException()
        
        user = db.get(models.User, uid)
        if not user:
            user = models.User(id=uid, email=email)
            db.add(user)
            db.commit()
            db.refresh(user)
        
        request.state.user = user
        
        return user
    except Exception as e:
        print(f"認証エラー詳細: {e}") # 確認用
        raise NotAuthenticatedException()


async def validate_upload_image(upload: UploadFile):
    # ファイルの拡張子を取得（例: ".heic", ".pdf"）
    ext = os.path.splitext(upload.filename)[1].lower()
    valid_extensions = {".jpg", ".jpeg", ".png", ".pdf", ".heic", ".heif", ".webp"}
    
    if upload.content_type not in ALLOWED_CONTENT_TYPES and ext not in valid_extensions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"許可されていないファイル形式です。({upload.content_type} / {ext})",
        )
        
    data = await upload.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="ファイルサイズが大きすぎます(上限10MB)。",
        )
    return data
    

def complete_plans_for_problem(db: Session, problem_id: int):
    """答案を提出したら、その問題の今日までの未完了タスクを完了にする。
       今日までのタスクがなければ（先取りで解いた場合）、一番近い未完了タスクを完了にする。"""
    pending_plans = db.scalars(
        select(models.StudyPlan)
        .where(models.StudyPlan.problem_id == problem_id)
        .where(models.StudyPlan.is_completed.is_(False))
        .order_by(models.StudyPlan.scheduled_date)
    ).all()

    today = date.today()
    due_plans = [plan for plan in pending_plans if plan.scheduled_date <= today]

    for plan in due_plans or pending_plans[:1]:
        plan.is_completed = True


def ensure_url(url: str):
    if not url:
         raise HTTPException(status_code=400, detail="URL is missing")
         
    parsed = urlparse(url)
    
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Invalid url scheme")
        
    is_valid_domain = parsed.netloc == "cloudinary.com" or parsed.netloc.endswith(".cloudinary.com")
    
    if not is_valid_domain:
        print(f"ブロックされた不正なホスト: {parsed.netloc}")
        raise HTTPException(status_code=400, detail="Not allowed host")
     

""" 開発用(危険!!!)"""
# データベース強制削除
# @app.get("/reset-db-secret")
# def reset_db_secret():
#     # 警告：データベースのテーブルを全て削除し、まっさらな状態で作り直します
#     models.Base.metadata.drop_all(bind=engine)
#     models.Base.metadata.create_all(bind=engine)
#     return {"message": "データベースを完全にリセットし、最新の設計図で作り直しました！ホーム画面に戻ってください。"}



""" バックエンド処理 """

""" login page """
@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

""" set cookie """
@app.post("/set-cookie")
def set_cookie(request: TokenRequest, response: Response):
    # verify the token sended from the frontend, then exchange it for a long-lived session cookie
    try:
        auth.verify_id_token(request.token)
        session_cookie = auth.create_session_cookie(request.token, expires_in=SESSION_LIFETIME)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid Token")

    response.set_cookie(
        key="token",
        value=session_cookie,
        httponly=True,             # prohibit access from JS to prevent XSS attack
        secure=IS_PRODUCTION,               # only transmit when the scheme is https
        samesite="lax",            # prevent CSRF
        max_age=int(SESSION_LIFETIME.total_seconds()),
        path="/"
    )
    
    return {"status": "success"}


""" logout system """
@app.get("/logout")
def logout():
    # logged_out=1 を付けてログイン画面にリダイレクト
    response = RedirectResponse(url="/login?logged_out=1", status_code=status.HTTP_303_SEE_OTHER)
    
    # 【超重要】ここでバックエンドのCookie（リストバンド）を確実に破棄する！
    response.delete_cookie(key="token", path="/", httponly=True, secure=IS_PRODUCTION, samesite="lax")
    
    return response


""" ホーム画面 ToDoとMenuとして進捗確認・問題登録・成績確認などを選択 """
@app.get('/')
def home(
    request: Request,
    error: Optional[str] = None,
    db: Session = Depends(get_db),
    user: dict = Depends(get_current_user)
):
    today = datetime.today().date()
    
    plans = db.scalars(
        select(models.StudyPlan)
        .join(models.Problem)
        .where(models.Problem.user_id == user.id)
        .where(models.StudyPlan.is_completed.is_(False))
        .where(models.StudyPlan.scheduled_date <= today+timedelta(days=3))
        .order_by(models.StudyPlan.scheduled_date)
    ).all()
    
    calendar_data = defaultdict(list)
    for plan in plans:
        calendar_data[plan.scheduled_date].append(plan)
    
    return templates.TemplateResponse(
        "home.html",
        {
            "request": request, 
            "today": today,
            "calendar_data": dict(calendar_data),
            "user": user,
            "error_message": "本日のAI採点の上限に達しました。また明日ご利用ください!" if error == "limit_reached" else None
        }
    )
   
   
""" show all the log and schedule of the user. """
@app.get('/schedule')
def schedule(
    request: Request,
    year: Optional[int] = None,
    month: Optional[int] = None,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user)
):
    today = date.today()
    if year is None or month is None:
        year = today.year
        month = today.month
    
    prev_y, prev_m, next_y, next_m = get_prev_next_month(year, month)
    cal = calendar.monthcalendar(year, month)
    
    first_day = date(year, month, 1)
    _, last_day_num = calendar.monthrange(year, month)
    last_day = date(year, month, last_day_num)
    
    plans = (db.query(models.StudyPlan)
            .join(models.Problem)
            .filter(models.Problem.user_id == user.id)
            .filter(models.StudyPlan.scheduled_date >= first_day)
            .filter(models.StudyPlan.scheduled_date <= last_day)
            .all())
    
    plans_by_date = defaultdict(list)
    for plan in plans:
        plans_by_date[plan.scheduled_date.day].append(plan)
    
    return templates.TemplateResponse(
        "schedule.html",
        {
            "request": request,
            "year": year,
            "month": month,
            "cal": cal,
            "plans_by_date": plans_by_date,
            "prev_y": prev_y, "prev_m": prev_m,
            "next_y": next_y, "next_m": next_m,
            "today": today
        }
    )


@app.get('/schedule/{target_date}')
def daily_todo(
    request: Request,
    target_date: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user)
):
    try:
        date_object = datetime.strptime(target_date, "%Y-%m-%d").date()
    except ValueError:
        return RedirectResponse(url="/schedule", status_code=status.HTTP_303_SEE_OTHER)
        
    plans = db.query(models.StudyPlan).join(models.Problem).filter(
        models.Problem.user_id == user.id,
        models.StudyPlan.scheduled_date == date_object
    ).all()
    
    completed_plans = [p for p in plans if p.is_completed]
    incomplete_plans = [p for p in plans if not p.is_completed]
    
    latest_scores = {}
    solve_counts = {}
    
    for plan in plans:
        pid = plan.problem_id
        
        submissions = db.query(models.Submission).filter(
            models.Submission.problem_id == pid
        ).order_by(models.Submission.id.desc()).all()
        
        solve_counts[pid] = len(submissions)
        
        if submissions and submissions[0].score is not None:
            latest_scores[pid] = f"{submissions[0].score}点"
        else:
            latest_scores[pid] = "-"
            
    
    return templates.TemplateResponse(
        "daily_todo.html",
        {
            "request": request,
            "target_date": date_object,
            "completed_plans": completed_plans,
            "incomplete_plans": incomplete_plans,
            "latest_scores": latest_scores,
            "solve_counts": solve_counts
        }
    )
    
    
@app.get('/confirm-problem/{problem_id}')
def confirm_task(
    request: Request,
    problem_id: int = None,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user)
):
    if problem_id is None:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    problem = db.query(models.Problem).filter(
        models.Problem.id == problem_id, 
        models.Problem.user_id == user.id
    ).first()
    
    if not problem:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    
    submissions = db.query(models.Submission).filter(
        models.Submission.problem_id==problem_id).all()
    
    return templates.TemplateResponse(
        "confirm-problem.html",
        {"request": request, "problem": problem, "submissions": submissions}
    )


@app.get('/add-task/{target_date}')
def add_task(
    request: Request,
    target_date: date = None,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user)
):
    problems = (db.query(models.Problem)
                .filter(models.Problem.user_id == user.id)
                .order_by(models.Problem.id.desc()).limit(fetch_limit).all())
    
    return templates.TemplateResponse(
        "add_task.html",
        {"request": request, "date": target_date, "problems": problems}
    ) 
    

@app.post('/add-task/{target_date}')
async def add_task_post(
    request: Request, 
    target_date: str, 
    problem_ids: List[int] = Form(...), 
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user)
):
    try:
        date_obj = datetime.strptime(target_date, "%Y-%m-%d").date()
    except ValueError:
        return RedirectResponse(url="/schedule", status_code=status.HTTP_303_SEE_OTHER)

    for pid in problem_ids:
        user_prob = db.query(models.Problem).filter(
            models.Problem.id == pid,
            models.Problem.user_id == user.id).first()
        
        if not user_prob:
            continue
       
        # existing_plan = db.query(models.StudyPlan).filter(
        #     models.StudyPlan.problem_id == pid,
        #     models.StudyPlan.scheduled_date == date_obj
        # ).first()
        
        # if not existing_plan:
        new_plan = models.StudyPlan(
            problem_id=pid,
            scheduled_date=date_obj,
            is_completed=False
        )
        db.add(new_plan)
            
    db.commit()

    return RedirectResponse(
        url=f"/schedule/{target_date}", 
        status_code=status.HTTP_303_SEE_OTHER
    )


@app.post("/delete-task/{plan_id}")
def delete_task(
    request: Request,
    plan_id: int = None,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user)
):
    if plan_id is None:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    # 自分の問題に紐づくタスクだけ削除できるようにする（他人のplan_idを指定されても消さない）
    task = db.query(models.StudyPlan).join(models.Problem).filter(
        models.StudyPlan.id == plan_id,
        models.Problem.user_id == user.id
    ).first()
    if task:
        db.delete(task)
        db.commit()
        
    referer = request.headers.get("referer", "/")
    return RedirectResponse(url=referer, status_code=303)
    


@app.get('/study')
def study(request: Request, db: Session = Depends(get_db)):
    pass


@app.get("/register-problem")
def register_problem(request: Request, user: models.User = Depends(get_current_user)):
    return templates.TemplateResponse("register_problem.html", {"request": request})


@app.post("/register-problem")
async def register_post(
    request: Request,
    db: Session = Depends(get_db),
    image: UploadFile = File(...), 
    text: Optional[str] = Form(None),
    source: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
    scheduled_date: Optional[str] = Form(None),
    user: models.User = Depends(get_current_user)
):
    problem_image_path = None
    if image:
        validated_image = await validate_upload_image(image)
        ext = os.path.splitext(image.filename)[1].lower()
        
        # アップロードは同期APIなので、スレッドプールで実行してイベントループを止めない
        if ext == ".pdf":
            # PDFの場合はそのまま（複数ページ）保存
            result = await run_in_threadpool(cloudinary.uploader.upload, validated_image, format="pdf")
        else:
            # 画像(HEIC含む)の場合は強制的にJPGに変換
            result = await run_in_threadpool(cloudinary.uploader.upload, validated_image, format="jpg")
            
        problem_image_path = result.get("secure_url")
            
    if source:
        source = source.strip()

    new_problem = models.Problem(
        user_id=user.id,
        problem_image_path=problem_image_path,
        problem_text=text,
        source=source,
    )
    db.add(new_problem)
    db.flush() 

    if tags:
        tag_list = tags.split(",")
        for tag_name in tag_list:
            tag_name = tag_name.strip()
            if not tag_name:
                continue
            
            search = db.query(models.Tag).filter(models.Tag.name == tag_name).first()
            if search:
                new_problem.tags.append(search)
            else:
                new_tag = models.Tag(name=tag_name)
                db.add(new_tag)
                new_problem.tags.append(new_tag)

    if scheduled_date:
        try:
            parsed_date = datetime.strptime(scheduled_date, "%Y-%m-%d").date()
            
            new_plan = models.StudyPlan(
                problem_id=new_problem.id,
                scheduled_date=parsed_date
            )
            db.add(new_plan)
        except ValueError:
            pass

    db.commit()
    
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
        

@app.get('/solve/{problem_id}')
def solve(
    request: Request,
    problem_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user)
    ):
    problem = db.query(models.Problem).filter(
        models.Problem.id == problem_id,
        models.Problem.user_id == user.id
    ).first()
    
    if problem:
        return templates.TemplateResponse(
            "solve.html",
            {"problem": problem,
             "request": request}
        )

    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    

@app.post('/submit-answer/{problem_id}')
async def submit_answer(
    request: Request,
    problem_id: int,
    db: Session = Depends(get_db),
    answer_image: UploadFile = File(...),
    user: models.User = Depends(get_current_user)
):
    # ファイル形式のチェックは validate_upload_image で行う
    # (iPhoneのHEICは content_type が application/octet-stream になることがあり、拡張子でも判定する必要がある)

    """ Add constraints of use of AI """

    today_start = datetime.combine(datetime.today(), time.min)
    tomorrow_start = today_start + timedelta(days=1)
    
    usage_count = db.query(models.Submission).join(models.Problem).filter(
        models.Problem.user_id == user.id,
        models.Submission.solved_at >= today_start,
        models.Submission.solved_at < tomorrow_start
    ).count()
    
    if usage_count >= DAILY_LIMIT:
        print(f"制限到達: {user.email} が1日の上限({DAILY_LIMIT}回)に達しました。")
        # 制限に引っかかったら、ホーム画面にエラー付きで追い返す
        return RedirectResponse(url="/?error=limit_reached", status_code=status.HTTP_303_SEE_OTHER)
    
    """ Send to LLM """
    
    problem = db.query(models.Problem).filter(
        models.Problem.id==problem_id,
        models.Problem.user_id == user.id
    ).first()

    if not problem:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    if not problem.problem_image_path and not problem.problem_text:
        return RedirectResponse(url=f"/solve/{problem_id}", status_code=status.HTTP_303_SEE_OTHER)

    answer_bytes = await validate_upload_image(answer_image)
    ext = os.path.splitext(answer_image.filename)[1].lower()
    if ext == ".pdf":
        result = await run_in_threadpool(cloudinary.uploader.upload, answer_bytes, format="pdf")
    else:
        result = await run_in_threadpool(cloudinary.uploader.upload, answer_bytes, format="jpg")
        
    save_path = result.get("secure_url")
    
    contents = []
     
    prompt = """
    あなたは九州大学文系数学の入試指導に熟練したプロの予備校講師です。
    生徒から問題と、その解答の画像が送られてきました。
    以下の点に注意して、厳しくも温かい採点と解説を行ってください。
    1. 単なる答え合わせではなく、論理展開や途中式に飛躍・誤りがないかを確認すること。
    2. 九州大学の採点基準を想定し、部分点を適切に与えること。
    3. 生徒が次に同じ問題を解くときに気をつけるべき「実践的なアドバイス」を含めること。
    """
    contents.append(prompt)
    
    # Input problem
    if problem.problem_text:
        contents.append(f"【問題文】\n{problem.problem_text}")
        
        
    if problem.problem_image_path:
        path = problem.problem_image_path
        
        ensure_url(path)
        
        try:
            response = await run_in_threadpool(requests.get, path, timeout=10)
            response.raise_for_status()
            image_bytes = response.content
            content_type = response.headers.get('Content-Type', 'image/jpeg')
            
            image_content = types.Part.from_bytes(
                data=image_bytes, mime_type=content_type
            )
            contents.append(image_content)

        except requests.exceptions.RequestException as e:
            print(f"画像取得エラー: {e}")
            raise HTTPException(status_code=400, detail="画像の取得に失敗しました。")

    # Input answer paper
    mime_type = answer_image.content_type
    if ext in [".heic", ".heif"]:
        mime_type = "image/heic"
    elif ext == ".pdf":
        mime_type = "application/pdf"
    elif mime_type not in ALLOWED_CONTENT_TYPES:
        mime_type = "image/jpeg" # どれにも当てはまらない場合はJPEGとみなす
        
    answer_content = types.Part.from_bytes(
        data=answer_bytes, mime_type=mime_type
    )
    contents.append(answer_content)
    
    
    # Gemini SDKの同期呼び出しは数十秒かかるため、スレッドプールで実行して他のリクエストを止めない
    try:
        response = await run_in_threadpool(
            client.models.generate_content,
            model='gemini-3-flash-preview',
            contents=contents,
            config={
                "response_mime_type": "application/json",
                "response_json_schema": Reply.model_json_schema(),
            },
        )
        reply = Reply.model_validate_json(response.text)
    except Exception as e:
        print(f"AI採点エラー: {e}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="AIによる採点に失敗しました。時間をおいて再度お試しください。",
        )

    new_submission = models.Submission(
        problem_id=problem_id,
        answer_image_path=save_path,
        score=reply.score,
        ai_feedback=reply.ai_feedback,
        explanation=reply.explanation,
    )
    db.add(new_submission)
    complete_plans_for_problem(db, problem_id)
    db.commit()
    db.refresh(new_submission)

    return RedirectResponse(
        url=f"/answer/{new_submission.id}",
        status_code=status.HTTP_303_SEE_OTHER
    )
    

@app.get("/answer/{submission_id}")
def answer(
    request: Request,
    submission_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user)
):
    
    submission = db.query(models.Submission).join(models.Problem).filter(
        models.Submission.id == submission_id,
        models.Problem.user_id == user.id
    ).first()
    if not submission:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    return templates.TemplateResponse(
        'answer.html',
        {
            "request": request, 
            "submission": submission,
            "problem": submission.problem,
            "explanation": submission.explanation
        }
    )


@app.post('/update-memo/{submission_id}')
async def update_memo(
    request: Request,
    submission_id: int,
    db: Session = Depends(get_db),
    mistake: str=Form(...),
    user_memo: str=Form(...),
    user: models.User = Depends(get_current_user)
):
    submission = db.query(models.Submission).join(models.Problem).filter(
        models.Submission.id == submission_id,
        models.Problem.user_id == user.id
    ).first()
    
    if submission:
        submission.mistake = mistake
        submission.user_memo = user_memo
        db.commit()

    return RedirectResponse(
        url="/",
        status_code=status.HTTP_303_SEE_OTHER
    )
    
      
    
    
    
    

