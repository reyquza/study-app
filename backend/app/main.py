import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import config, worker
from app.config import FRONTEND_ORIGINS
from app.database import SessionLocal
from app.dependencies import NotAuthenticated
from app.grading.gemini import get_grader
from app.routers import auth, grading, users
from app.services import storage
from app.web import routes as web_routes


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    storage.init_storage()
    if not config.RUN_WORKER_IN_PROCESS:
        yield
        return
    if not config.GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY is not set; answers will be queued but not graded")
        yield
        return

    stop, task = await worker.start_local_worker(SessionLocal, get_grader())
    yield
    stop.set()
    try:
        # Let in-flight gradings finish; anything cut off is retried once its lease expires.
        await asyncio.wait_for(task, timeout=30)
    except TimeoutError:
        pass  # wait_for has cancelled the worker


app = FastAPI(
    title="NextLearn API",
    description="AI grading of handwritten math answers, with study scheduling",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(FRONTEND_ORIGINS),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    # Templates read request.state.user; get_current_user fills it in on authenticated routes.
    request.state.user = None
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.exception_handler(NotAuthenticated)
async def handle_not_authenticated(request: Request, exc: NotAuthenticated):
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Not authenticated"},
        )
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/api/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}


app.mount("/static", StaticFiles(directory=web_routes.WEB_DIR / "static"), name="static")

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(grading.router)
app.include_router(web_routes.router)
