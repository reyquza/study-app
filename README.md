# NextLearn

AI grading for handwritten math answers. A student photographs a problem and their
handwritten solution; NextLearn scores it out of 100 with partial credit, explains
where the reasoning went wrong, and schedules the problem for review.

I built the first version in March 2026, shortly after I started programming, for my
sister, who was preparing for a Japanese university entrance exam. Version 2, in progress
on this branch, is about one question: **how far can an AI grader's score be trusted,
and can the app tell when it should not be?**

## Status

| | |
|---|---|
| ✅ | Problem registration (photo or PDF), study calendar, daily to-do list |
| ✅ | Grading with Gemini through structured output (score, feedback, model answer) |
| ✅ | Google sign-in (Firebase) with 14-day httpOnly session cookies, email allowlist |
| ✅ | v2 layout: thin routes over a service layer, grading engine behind a `Grader` interface, tests |
| 🚧 | Grading as background jobs, so a slow model call never blocks the server |
| 🚧 | Rubric-based, step-by-step grading with repeated sampling and a confidence signal |
| 🚧 | Evaluation harness and a public reliability report on human-graded answers |
| 🚧 | Mistake taxonomy, weakness view, spaced-repetition review |
| 🚧 | Next.js frontend, English UI, deployment |

## Repository layout

```
backend/                  FastAPI app (Python 3.11+, uv)
  app/
    main.py               app setup: middleware, error handling, routers
    config.py             all settings, read from environment variables
    database.py, models.py
    routers/              JSON API under /api (auth, users)
    services/             business logic: problems, plans, submissions, storage, auth
    grading/              grading engine: Grader interface, Gemini implementation, prompts
    web/                  interim server-rendered UI (Jinja2), to be replaced by frontend/
  alembic/                database migrations
  tests/                  pytest suite; Firebase, Cloudinary and Gemini are faked
  scripts/serve_lan.py    serve on the home network with a QR code for phones
docs/                     development log and the original (Japanese) plan
```

Routes only parse input, call `app.services`, and render or serialize the result. The
web pages and the JSON API therefore share the same logic, and moving the UI to Next.js
means adding API routes and a frontend, not rewriting the backend. The `grading` package
is the single entry point for grading, so the evaluation harness will measure exactly the
code that runs in production.

## Running locally

```bash
cd backend
cp .env.example .env              # then fill in the keys
# put the Firebase service-account key at backend/firebase-credentials.json
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

Open http://localhost:8000. API docs are at http://localhost:8000/docs.

## Tests

```bash
cd backend
uv run pytest
```
