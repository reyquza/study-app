"""
Flask⇒  「グローバルに用意された便利な箱(request や session、g)」をどこからでも引っ張り出して使うスタイル
FastAPI⇒「必要なものは全て関数の引数として受け取る(Depends(大元の関数を実行する前にDepends内の関数を実行する)を使う)」スタイル
    1. リクエストデータは変数として受け取る  username: str = Form(...)
    2. GET, POSTなどはメソッドごとに分ける  @app.get("/login")
    3. デコレータではなくDepends           current_user: User = Depends(get_current_user) もしログインしていなければ、get_current_user の中でエラーを弾く。
"""
"""
データの取得 (SELECT)
    SQLite: cursor.execute("SELECT * FROM users WHERE username = ?", (name,)) して fetchone()
    SQLAlchemy: db.query(User).filter(User.username == name).first()

データの追加 (INSERT)
    SQLite: cursor.execute("INSERT INTO users (username) VALUES (?)", (name,)) して db.commit()
    SQLAlchemy: new_user = User(username=name) でインスタンスを作り、db.add(new_user) して db.commit()

データの削除 (DELETE)
    SQLite: cursor.execute("DELETE FROM grades WHERE id = ?", (id,))
    SQLAlchemy: grade = db.query(Grade).filter(Grade.id == id).first() で探して、db.delete(grade) して db.commit()
"""

import calendar
import json
import os
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, date
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Depends, Form, status, UploadFile, File
from fastapi.responses import StreamingResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker, Session

import models


""" 環境変数 """
load_dotenv()
DATABASE_URL = os.getenv('DATABASE_URL')
SECRETKEY = os.getenv('SECRETKEY')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

""" 初期設定 """
app = FastAPI()
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.add_middleware(SessionMiddleware, secret_key=SECRETKEY)

client = genai.Client(api_key=GEMINI_API_KEY)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

""" 動作確認用 """
models.Base.metadata.create_all(bind=engine)

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

""" バックエンド処理 """
""" ホーム画面 ToDoとMenuとして進捗確認・問題登録・成績確認などを選択 """
@app.get('/')
def home(request: Request, db: Session = Depends(get_db)):
    today = datetime.today()
    
    plans = db.scalars(
        select(models.StudyPlan)
        .where(models.StudyPlan.scheduled_date <= today+timedelta(3))
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
            "calendar_data": dict(calendar_data)      
        }
    )
   
   
""" show all the log and schedule of the user. """
@app.get('/schedule')
def schedule(
    request: Request,
    year: Optional[int] = None,
    month: Optional[int] = None,
    db: Session = Depends(get_db)
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
    db: Session = Depends(get_db)
):
    try:
        date_object = datetime.strptime(target_date, "%Y-%m-%d").date()
    except ValueError:
        return RedirectResponse(url="/schedule", status_code=status.HTTP_303_SEE_OTHER)
        
    plans = db.query(models.StudyPlan).filter(
        models.StudyPlan.scheduled_date == date_object
    ).all()
    
    completed_plans = [p for p in plans if p.is_completed]
    incomplete_plans = [p for p in plans if not p.is_completed]
    
    return templates.TemplateResponse(
        "daily_todo.html",
        {
            "request": request,
            "target_date": date_object,
            "completed_plans": completed_plans,
            "incomplete_plans": incomplete_plans
        }
    )
    


@app.get('/study')
def study(request: Request, db: Session = Depends(get_db)):
    pass


@app.get("/register-problem")
def register_problem(request: Request):
    return templates.TemplateResponse("register_problem.html", {"request": request})


@app.post("/register-problem")
def register_post(
    request: Request,
    db: Session = Depends(get_db),
    image: UploadFile = File(...), 
    text: Optional[str] = Form(None),
    source: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
    scheduled_date: Optional[str] = Form(None),
):
    problem_image_path = None
    if image:
        os.makedirs("static/images/problems", exist_ok=True)
        _, ext = os.path.splitext(image.filename)
        unique_filename = f"{uuid.uuid4()}{ext}"
        problem_image_path = f"static/images/problems/{unique_filename}"
        with open(problem_image_path, "wb") as f:
            f.write(image.file.read()) 
            
    if source:
        source = source.strip()

    new_problem = models.Problem(
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
    db: Session = Depends(get_db)
    ):
    problem = db.query(models.Problem).filter(models.Problem.id == problem_id).first()
    if problem:
        return templates.TemplateResponse(
            "solve.html",
            {"problem": problem,
             "request": request}
        )
        
    else: RedirectResponse(url="/home", status_code=status.HTTP_404_NOT_FOUND)
    

@app.post('/submit-answer/{problem_id}')
async def submit_answer(
    request: Request,
    problem_id: int,
    db: Session = Depends(get_db),
    answer_image: UploadFile = File(...)
    ):
    
    """ Send to LLM """
    # Get the extension and generate a random file name that will never overlap
    os.makedirs("static/images/answers", exist_ok=True)
    _, ext = os.path.splitext(answer_image.filename)
    unique_filename = f"{uuid.uuid4()}{ext}"
    save_path = f"static/images/answers/{unique_filename}"
    
    # contents which will send to the LLM
    contents = []
    
    answer_bytes = await answer_image.read()
    with open(save_path, "wb") as f:
        f.write(answer_bytes)
    
    problem = db.query(models.Problem).filter(models.Problem.id==problem_id).one()
        
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
        with open(path, 'rb') as f:
            image_bytes = f.read()
            
        image_content = types.Part.from_bytes(
            data=image_bytes, mime_type="image/jpeg"
        )
        contents.append(image_content)
    
    else: 
        return RedirectResponse(
        url="/solve/{problem_id}",
        status_code=status.HTTP_404_NOT_FOUND
        )
    
    # Input answer paper
    answer_content = types.Part.from_bytes(
        data=answer_bytes, mime_type=answer_image.content_type
    )
    contents.append(answer_content)
    
    
    response = client.models.generate_content(
        model='gemini-3-flash-preview',
        contents=contents,
        config={
            "response_mime_type": "application/json",
            "response_json_schema": Reply.model_json_schema(),
        },
    )
    
    reply = Reply.model_validate_json(response.text)

    new_submission = models.Submission(
        problem_id=problem_id,
        answer_image_path=save_path,
        score=reply.score,
        ai_feedback=reply.ai_feedback,
        explanation=reply.explanation,
    )
    db.add(new_submission)
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
    db: Session = Depends(get_db)
    ):
    
    submission = db.get(models.Submission, submission_id)
    studyplan = db.query(models.StudyPlan).filter(models.StudyPlan.problem_id == submission.problem_id).one()
    studyplan.is_completed = True
    db.commit()
    
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
    user_memo: str=Form(...)
    ):
    submission = db.get(models.Submission, submission_id)
    if submission:
        submission.mistake = mistake
        submission.user_memo = user_memo
        db.commit()
        
        return RedirectResponse(
            url="/", 
            status_code=status.HTTP_303_SEE_OTHER
        )
    
      
    
    
    
    

