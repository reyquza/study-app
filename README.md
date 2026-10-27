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
| ✅ | Email + password accounts (Argon2) and one-tap guest accounts that can sign up later and keep their data |
| ✅ | Grading as background jobs: submitting returns in about a second, a worker grades, the page follows along |
| ✅ | v2 layout: thin routes over a service layer, grading engine behind a `Grader` interface, tests |
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
    security.py           password hashing, signed session cookie
    worker.py             background grading worker
    routers/              JSON API under /api (auth, users, grading jobs)
    services/             business logic: problems, plans, grading jobs, submissions, storage, auth
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

## Grading pipeline

A model call takes 20 to 60 seconds, too long to hold a request open. So grading is a job:

1. `POST /submit-answer/{id}` (or `POST /api/problems/{id}/grading-jobs`, which returns `202`)
   validates and stores the photo, inserts a `grading_jobs` row, and returns at once.
2. A worker claims the row with `SELECT ... FOR UPDATE SKIP LOCKED`, so any number of workers
   can share the table without grading a job twice. The claim is a lease: if a worker dies
   mid-grading, the job becomes claimable again when the lease runs out.
3. Model errors are retried with backoff, up to three attempts. A result from an attempt that
   lost its lease is discarded, so a slow worker cannot overwrite a newer one.
4. The browser polls `GET /api/grading-jobs/{id}` and moves to the result when it is ready.

The worker runs inside the web process by default, which is enough for one server. Set
`RUN_WORKER_IN_PROCESS=false` and run `uv run python -m app.worker` to scale it on its own.
The Postgres queue avoids running Redis or a broker just for this, and a job and its result
are written in the same transaction.

## Running locally

```bash
cd backend
cp .env.example .env              # then fill in the Gemini and Cloudinary keys
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
