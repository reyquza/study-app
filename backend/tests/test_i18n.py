"""English for English-language visitors (e.g. judges), Japanese for Japanese students."""

import re
from pathlib import Path

import pytest

from app.grading.base import GradingRequest
from app.grading.gemini import GeminiGrader
from app.grading.prompts import ENGLISH_FEEDBACK
from app.models import GradingJob
from app.services.storage import FilePart
from app.web import i18n
from app.web.routes import ERROR_MESSAGES
from tests.factories import ANSWER_FILE, make_problem, make_submission

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "web" / "templates"
STORAGE = Path(__file__).resolve().parents[1] / "app" / "services" / "storage.py"
CJK = re.compile(r"[぀-ヿ一-鿿]")
ENGLISH = {"Accept-Language": "en-US,en;q=0.9"}


def _texts_to_translate() -> set[str]:
    texts = set(ERROR_MESSAGES.values())
    for template in TEMPLATES.glob("*.html"):
        texts |= {m.group(2) for m in re.finditer(r"""_\(\s*(["'])(.+?)\1""", template.read_text(encoding="utf-8"))}
    texts |= set(re.findall(r'UploadRejected\(\s*"([^"]+)"', STORAGE.read_text(encoding="utf-8")))
    texts |= {"{label}は{limit}文字までです。", "問題文", "出典", "ミス", "メモ", "タグは{count}個まで、1つ{chars}文字までです。"}
    return texts


def test_every_text_has_an_english_translation():
    missing = sorted(text for text in _texts_to_translate() if text not in i18n.EN)

    assert missing == []


@pytest.mark.parametrize("header, expected", [
    ("en-US,en;q=0.9", "en"),
    ("ja,en-US;q=0.9", "ja"),
    ("ja-JP", "ja"),
    ("fr-FR,fr", "en"),
    ("", "ja"),
])
def test_language_follows_the_browser(anonymous_client, header, expected):
    page = anonymous_client.get("/login", headers={"Accept-Language": header}).text

    assert f'<html lang="{expected}">' in page


def test_english_login_page(anonymous_client):
    page = anonymous_client.get("/login", headers=ENGLISH).text

    for text in ("Log in", "Sign up", "Email", "Password", "Try it without an account (guest)"):
        assert text in page
    assert CJK.findall(page.replace("日本語", "")) == []


def test_switch_remembers_the_choice_over_the_browser(anonymous_client):
    response = anonymous_client.get("/lang/en", headers={"Accept-Language": "ja", "Referer": "http://x/login?mode=signup"},
                                    follow_redirects=False)

    assert response.headers["location"] == "/login?mode=signup"
    assert 'lang="en"' in anonymous_client.get("/login", headers={"Accept-Language": "ja"}).text
    anonymous_client.get("/lang/ja", follow_redirects=False)
    assert 'lang="ja"' in anonymous_client.get("/login", headers=ENGLISH).text


def test_switch_ignores_unknown_languages_and_foreign_referers(anonymous_client):
    response = anonymous_client.get("/lang/xx", headers={"Referer": "https://evil.example/"}, follow_redirects=False)

    assert response.headers["location"] == "/"
    assert "lang" not in response.cookies


def test_app_pages_have_no_japanese_left_in_english(client, db, users, run_jobs):
    problem = make_problem(db, users["alice"].id)
    submission = make_submission(db, problem)
    job_id = client.post(f"/api/problems/{problem.id}/grading-jobs", files=ANSWER_FILE).json()["id"]
    pages = ["/", "/schedule", "/schedule/2026-11-02", "/add-task/2026-11-02", "/register-problem",
             f"/solve/{problem.id}", f"/grading/{job_id}", f"/answer/{submission.id}",
             f"/confirm-problem/{problem.id}", "/?error=limit_reached", "/sample"]
    user_data = [problem.problem_text or "", problem.source or "", submission.ai_feedback or "",
                 submission.explanation or "", submission.mistake or "", submission.user_memo or ""]

    for path in pages:
        page = client.get(path, headers=ENGLISH, follow_redirects=False)
        if page.status_code == 303:  # /sample without a sample file
            continue
        text = page.text.replace("日本語", "")
        # The sample page keeps the AI's original Japanese output inside <details>.
        text = re.sub(r"<details.*?</details>", "", text, flags=re.S)
        # Code comments (CSS, HTML, JavaScript) are not shown to visitors.
        text = re.sub(r"/\*.*?\*/|<!--.*?-->", "", text, flags=re.S)
        text = re.sub(r"(?<![:\w])//[^\n]*", "", text)
        for data in user_data:
            if len(data) > 2:
                text = text.replace(data, "")
        assert CJK.findall(text) == [], f"Japanese left on {path}: {''.join(CJK.findall(text))[:40]}"


def test_english_visitors_get_feedback_in_english(client, db, users, grader, run_jobs):
    problem = make_problem(db, users["alice"].id)

    job_id = client.post(f"/api/problems/{problem.id}/grading-jobs", files=ANSWER_FILE, headers=ENGLISH).json()["id"]
    run_jobs()

    db.expire_all()
    assert db.get(GradingJob, job_id).language == "en"
    assert grader.requests[-1].language == "en"


def test_japanese_visitors_keep_the_evaluated_prompt(client, db, users, grader, run_jobs):
    problem = make_problem(db, users["alice"].id)

    client.post(f"/submit-answer/{problem.id}", files=ANSWER_FILE, follow_redirects=False)
    run_jobs()

    assert grader.requests[-1].language == "ja"


@pytest.mark.parametrize("language, has_instruction", [("ja", False), ("en", True)])
def test_english_instruction_is_added_only_for_english(language, has_instruction):
    seen = {}

    class FakeModels:
        def generate_content(self, model, contents, config):
            seen["prompt"] = contents[0]
            return type("Response", (), {"text": '{"score": 80, "ai_feedback": "f", "explanation": "e"}'})()

    grader = GeminiGrader(api_key="test-key", model_name="test-model")
    grader._client = type("Client", (), {"models": FakeModels()})()
    grader.grade(GradingRequest(answer=FilePart(data=b"x", mime_type="image/jpeg"), problem_text="p", language=language))

    assert (ENGLISH_FEEDBACK in seen["prompt"]) is has_instruction


def test_upload_errors_are_translated(client, db, users):
    problem = make_problem(db, users["alice"].id)
    bad = {"answer_image": ("answer.exe", b"x", "application/octet-stream")}

    response = client.post(f"/submit-answer/{problem.id}", files=bad, headers=ENGLISH, follow_redirects=False)

    assert response.status_code == 400
    assert "This file type is not allowed" in response.json()["detail"]
