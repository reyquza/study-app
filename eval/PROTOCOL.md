# Evaluation protocol

Question: **how close is the AI grader to a human grader on handwritten entrance-exam
answers, and can it tell when its own score should not be trusted?**

This file and the graders were committed before any grader was run on the collected
answers, and the graders were written without looking at those answers.

## Data
- Problems: four original problems in the style of the Kyushu University humanities-track
  exam ([problems.yaml](problems.yaml)), each in three parts of rising difficulty.
  The rubrics were fixed before collection.
- Answers: handwritten answers from volunteers (adults, with consent), collected through a
  Google Form between 2026-10-28 and 2026-10-31. Answers written by the author are kept
  separate (`source = author`) and reported apart from volunteers' answers.
- Ground truth: the author's rubric grades, given blind to all AI output
  ([label.py](label.py)). A second grader's labels, when available, give human-human
  agreement as the reference point.
- Personal data stays in `eval/data/` (not in git). Answers are identified by salted hashes.

## Graders
| Version | Method |
|---|---|
| v1 | The original holistic prompt; a score out of 100, divided by 10 |
| v2 | The model judges each rubric step from the image; code adds the points |
| v3 | As v2, but the model first transcribes the answer, then judges |
| v4 | Five v3 gradings; each step takes the median. Their spread is the review signal |

All use the model in `GEMINI_MODEL` (gemini-3-flash-preview), with default sampling.

## Metrics (fixed in advance)
- Mean absolute error and mean signed error (bias) of the total out of 10, with 95%
  percentile bootstrap intervals (5000 resamples over answers).
- Share of large errors, where the absolute error is 2 points or more.
- Error by sub-question (1)(2)(3) and agreement on individual rubric steps (v2 to v4).
- Consistency: standard deviation of the total across 5 runs of the same answer.
- Review flag: v4 flags an answer when its five totals span 2 points or more. Reported:
  how many large errors it catches, and the error on the answers it does not flag.

## Limits
The volunteer sample is small (13 answers), so the intervals are wide. This is a pilot
study. The point is a harness that runs the same way on more data, not a final number.

## Reproduce
```bash
cd backend
uv run python ../eval/ingest.py                    # Google Form folders -> anonymous answers
uv run python ../eval/label.py                     # human grades, at http://127.0.0.1:8100
uv run python ../eval/run.py --grader v1 --samples 5
uv run python ../eval/run.py --grader v2 --samples 5
uv run python ../eval/run.py --grader v3 --samples 5
uv run python ../eval/analyze.py                   # -> eval/results/
```
