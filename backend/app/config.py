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

# Signs the session tokens. Must be set to a long random string in production:
#   python -c "import secrets; print(secrets.token_urlsafe(48))"
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY") or ""
if not JWT_SECRET_KEY:
    if IS_PRODUCTION:
        raise RuntimeError("JWT_SECRET_KEY must be set in production")
    JWT_SECRET_KEY = "development-only-change-this-secret-before-deployment"
JWT_ALGORITHM = "HS256"

SESSION_COOKIE_NAME = "token"
SESSION_LIFETIME = timedelta(days=14)
PASSWORD_MIN_LENGTH = 8

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
DAILY_GRADING_LIMIT = int(os.getenv("DAILY_GRADING_LIMIT", "5"))
# Cap across all users, so open sign-up cannot run up the model bill.
GLOBAL_DAILY_GRADING_LIMIT = int(os.getenv("GLOBAL_DAILY_GRADING_LIMIT", "20"))
# Caps shared by all guest users together: anyone can create guests, so a per-user limit
# alone does not bound what guests can spend.
GUEST_GRADING_LIMITS = (
    (timedelta(minutes=1), int(os.getenv("GUEST_GRADING_LIMIT_PER_MINUTE", "5"))),
    (timedelta(hours=1), int(os.getenv("GUEST_GRADING_LIMIT_PER_HOUR", "30"))),
)
GUEST_DAILY_GRADING_LIMIT = int(os.getenv("GUEST_DAILY_GRADING_LIMIT", "50"))
# A model call must finish well within the job lease, or another worker may pick the job up again.
GRADING_TIMEOUT = timedelta(seconds=90)
# Caps thinking plus visible output per call, which bounds what one call can cost. Measured
# gradings use 3k-16k; a call that hits the cap fails without a retry.
GRADING_MAX_OUTPUT_TOKENS = int(os.getenv("GRADING_MAX_OUTPUT_TOKENS", "20000"))

# Problem registration stores an upload; cap it so open sign-up cannot exhaust file storage.
DAILY_PROBLEM_LIMIT = int(os.getenv("DAILY_PROBLEM_LIMIT", "20"))
GUEST_DAILY_PROBLEM_LIMIT = int(os.getenv("GUEST_DAILY_PROBLEM_LIMIT", "100"))
# Text that reaches the model or the database. The model is billed per token.
MAX_PROBLEM_TEXT_CHARS = 2000
MAX_SOURCE_CHARS = 200
MAX_TAGS = 10
MAX_TAG_CHARS = 50
MAX_MEMO_CHARS = 2000

# Abuse limits on account endpoints, kept in the database so restarts do not reset them.
LOGIN_FAILURE_LIMIT = (10, timedelta(minutes=15))  # consecutive failures per account, lock duration
NEW_GUEST_LIMIT = (30, timedelta(minutes=1))  # across everyone
NEW_ACCOUNT_LIMIT = (30, timedelta(minutes=1))  # across everyone
# Argon2 uses 64 MiB per hash; bound concurrent hashes so a burst of logins cannot exhaust memory.
MAX_CONCURRENT_PASSWORD_HASHES = 2

# Background grading worker (app.worker). On a single server it runs inside the web
# process; set RUN_WORKER_IN_PROCESS=false and run `python -m app.worker` to scale it separately.
RUN_WORKER_IN_PROCESS = os.getenv("RUN_WORKER_IN_PROCESS", "true").lower() == "true"
WORKER_CONCURRENCY = int(os.getenv("WORKER_CONCURRENCY", "4"))
WORKER_POLL_INTERVAL_SECONDS = 1.0
JOB_LEASE = timedelta(minutes=3)
GRADING_MAX_ATTEMPTS = 3

CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME")
CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY")
CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
# The model bills every PDF page as input, so a long PDF would make one grading expensive.
MAX_PDF_PAGES = 5

RECENT_PROBLEMS_LIMIT = 30

# Origins allowed to call the JSON API from a separate frontend (the planned Next.js app).
FRONTEND_ORIGINS = _split_csv(
    os.getenv("FRONTEND_ORIGINS", "http://localhost:3000")
)
