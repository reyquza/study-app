import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv


BACKEND_DIR = Path(__file__).resolve().parent.parent

# Real environment variables win over values in backend/.env.
load_dotenv(BACKEND_DIR / ".env")


def _split_csv(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())


IS_PRODUCTION = (
    os.getenv("APP_ENV") == "production"
    or os.getenv("RENDER") is not None
)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"sqlite:///{(BACKEND_DIR / 'nextlearn.db').as_posix()}",
)
# Render and Heroku hand out the deprecated "postgres://" scheme, which SQLAlchemy rejects.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# "Today" for schedules and the daily grading limit is the learner's day, not the server's (UTC on most hosts).
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Tokyo")

# Firebase Admin credentials: either the service-account JSON itself (handy on hosts
# without secret files) or a path to it.
FIREBASE_CREDENTIALS_JSON = os.getenv("FIREBASE_CREDENTIALS_JSON")
FIREBASE_CREDENTIALS_PATH = Path(
    os.getenv(
        "FIREBASE_CREDENTIALS_PATH",
        str(BACKEND_DIR / "firebase-credentials.json"),
    )
)

SESSION_COOKIE_NAME = "token"
# Firebase session cookies can live for at most two weeks.
SESSION_LIFETIME = timedelta(days=14)

# WHITELISTS is the variable name used before the v2 restructure.
ALLOWED_EMAILS = frozenset(
    email.lower()
    for email in _split_csv(
        os.getenv("ALLOWED_EMAILS") or os.getenv("WHITELISTS", "")
    )
)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
DAILY_GRADING_LIMIT = int(os.getenv("DAILY_GRADING_LIMIT", "5"))

CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME")
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

RECENT_PROBLEMS_LIMIT = 30

# Origins allowed to call the JSON API from a separate frontend (the planned Next.js app).
FRONTEND_ORIGINS = _split_csv(
    os.getenv("FRONTEND_ORIGINS", "http://localhost:3000")
)
