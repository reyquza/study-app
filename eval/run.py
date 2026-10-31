"""Grade every answer in the manifest with one grader version, possibly several times.

    cd backend
    uv run python ../eval/run.py --grader v3 --samples 5

Grader versions:
  v1  the original holistic prompt (score out of 100, scaled to 10)
  v2  rubric steps judged from the image
  v3  transcribe first, then judge rubric steps
(v4, the median of v3's samples, is computed in analyze.py.)

Results are appended to eval/data/runs/<grader>.jsonl as they finish, so an interrupted
run resumes where it stopped. Failed calls are recorded and retried on the next run.
"""

import argparse
import io
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from PIL import Image

from common import RUNS_DIR, answer_pages, append_jsonl, load_rubrics, read_jsonl, read_manifest

from app import config
from app.grading.base import GradingFailed, GradingRequest
from app.grading.gemini import GeminiGrader
from app.grading.rubric import GeminiRubricGrader
from app.services.storage import FilePart


def _single_file(pages: list[FilePart]) -> FilePart:
    """v1 takes one file: stack photos vertically into one JPEG (a PDF is already one file)."""
    if len(pages) == 1:
        return pages[0]
    images = [Image.open(io.BytesIO(page.data)).convert("RGB") for page in pages]
    width = max(image.width for image in images)
    canvas = Image.new("RGB", (width, sum(image.height for image in images)), "white")
    y = 0
    for image in images:
        canvas.paste(image, (0, y))
        y += image.height
    buffer = io.BytesIO()
    canvas.save(buffer, format="JPEG", quality=90)
    return FilePart(data=buffer.getvalue(), mime_type="image/jpeg")


def grade_once(grader_name: str, grader, rubric, answer_id: str) -> dict:
    pages = answer_pages(answer_id)
    if grader_name == "v1":
        started = time.monotonic()
        result = grader.grade(GradingRequest(answer=_single_file(pages), problem_text=rubric.statement))
        return {"total": result.score / 10, "latency_seconds": time.monotonic() - started}

    grade = grader.grade(rubric, pages)
    return {
        "total": grade.total,
        "awarded": grade.awarded,
        "reasons": grade.reasons,
        "transcription": grade.transcription,
        "missing_steps": grade.missing_steps,
        "latency_seconds": grade.latency_seconds,
        "usage": grade.usage,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--grader", choices=["v1", "v2", "v3"], required=True)
    parser.add_argument("--samples", type=int, default=1, help="gradings per answer, for consistency")
    parser.add_argument("--model", default=config.GEMINI_MODEL)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--only", nargs="*", help="answer ids to grade (default: all)")
    args = parser.parse_args()

    if args.grader == "v1":
        grader = GeminiGrader(api_key=config.GEMINI_API_KEY, model_name=args.model)
    else:
        grader = GeminiRubricGrader(api_key=config.GEMINI_API_KEY, model_name=args.model, transcribe=args.grader == "v3")

    rubrics = load_rubrics()
    out_path = RUNS_DIR / f"{args.grader}.jsonl"
    done = {
        (r["answer_id"], r["sample"])
        for r in read_jsonl(out_path)
        if r.get("error") is None and r["model"] == args.model
    }
    answers = [row for row in read_manifest() if not args.only or row["answer_id"] in args.only]
    todo = [(row, sample) for row in answers for sample in range(args.samples) if (row["answer_id"], sample) not in done]
    print(f"{args.grader} on {args.model}: {len(todo)} gradings to run, {len(done)} already done")

    def task(row, sample):
        record = {
            "answer_id": row["answer_id"],
            "grader": args.grader,
            "model": args.model,
            "sample": sample,
            "at": datetime.now(timezone.utc).isoformat(),
            "error": None,
        }
        try:
            record |= grade_once(args.grader, grader, rubrics[row["problem_id"]], row["answer_id"])
        except GradingFailed as exc:
            record["error"] = str(exc)[:500]
        append_jsonl(out_path, record)
        return record

    failures = 0
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(task, row, sample) for row, sample in todo]
        for i, future in enumerate(as_completed(futures), 1):
            record = future.result()
            status = f"total={record.get('total')}" if record["error"] is None else f"ERROR {record['error'][:80]}"
            failures += record["error"] is not None
            print(f"[{i}/{len(todo)}] {record['answer_id']} #{record['sample']}: {status}")
    if failures:
        print(f"{failures} failed; run the same command again to retry them")


if __name__ == "__main__":
    main()
