# Production image for the API, the server-rendered pages and the in-process grading worker.
FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.21 /uv /bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, so code changes don't reinstall them.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project

COPY . .
ENV PATH="/app/.venv/bin:$PATH"     FORWARDED_ALLOW_IPS="*"

# Migrate, then serve. --proxy-headers (trusting FORWARDED_ALLOW_IPS) makes the app see the
# https:// URL the host's TLS proxy received, so generated links are not downgraded to http.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers"]
