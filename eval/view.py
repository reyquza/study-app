"""Read-only page for looking at what the AI graders produced, next to the human labels.

    cd backend
    uv run python ../eval/view.py            # then open http://127.0.0.1:8200

It reads eval/data/ (answers, labels, runs) and calls no model. Open it only after
labeling: it shows the AI scores that label.py keeps hidden.
"""

import argparse
import html
import statistics

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse

from common import ANSWERS_DIR, LABELS_DIR, RUNS_DIR, load_problems, load_rubrics, read_jsonl, read_manifest

app = FastAPI()
GRADERS = ("v1", "v2", "v3")
LABELER = "A"

STYLE = """
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js"
  onload="renderMathInElement(document.body,{delimiters:[{left:'$$',right:'$$',display:true},{left:'$',right:'$',display:false}]})"></script>
<style>
 body { font: 14px/1.6 system-ui, sans-serif; margin: 0; color: #1f2933; }
 a { color: #1f8a4c; }
 table { border-collapse: collapse; }
 th, td { border: 1px solid #e3e8ec; padding: 4px 8px; vertical-align: top; text-align: left; }
 th { background: #f5f7f6; }
 .num { text-align: right; font-variant-numeric: tabular-nums; }
 .hi { background: #fdecec; } .lo { background: #e8f0fd; }
 .wrap { display: flex; height: 100vh; }
 .left { flex: 1; overflow: auto; background: #333; padding: 8px; }
 .left img, .left embed { width: 100%; background: #fff; margin-bottom: 8px; }
 .left embed { height: 95vh; }
 .right { flex: 1.2; overflow: auto; padding: 12px 18px; }
 pre { white-space: pre-wrap; background: #f5f7f6; padding: 8px; margin: 4px 0; }
 .muted { color: #5f6b76; }
 details { margin: 8px 0; }
</style>
"""


def _labels() -> dict[str, dict]:
    return {r["answer_id"]: r for r in read_jsonl(LABELS_DIR / f"human-{LABELER}.jsonl")}


def _runs(grader: str) -> dict[str, list[dict]]:
    """Successful runs per answer, in sample order."""
    runs: dict[str, list[dict]] = {}
    for record in read_jsonl(RUNS_DIR / f"{grader}.jsonl"):
        if record.get("error") is None:
            runs.setdefault(record["answer_id"], []).append(record)
    return {answer: sorted(records, key=lambda r: r["sample"]) for answer, records in runs.items()}


def _fmt(value) -> str:
    return "–" if value is None else (f"{value:g}" if isinstance(value, float) else str(value))


def _cell(ai, human) -> str:
    """A number, shaded when it differs from the human score (red: AI higher, blue: lower)."""
    css = "" if ai is None or human is None or ai == human else (" hi" if ai > human else " lo")
    return f'<td class="num{css}">{_fmt(ai)}</td>'


@app.get("/")
def index():
    labels, runs = _labels(), {g: _runs(g) for g in GRADERS}
    rows = []
    for row in read_manifest():
        answer = row["answer_id"]
        human = labels.get(answer, {}).get("total")
        cells = []
        for grader in GRADERS:
            totals = [r["total"] for r in runs[grader].get(answer, [])]
            first = totals[0] if totals else None
            spread = f"{min(totals):g}–{max(totals):g}" if totals else "–"
            cells.append(_cell(first, human) + f'<td class="num muted">{spread}</td>')
        rows.append(
            f"<tr><td><a href='/answer/{answer}'>{answer}</a></td><td>{row['source']}</td>"
            f"<td class='num'>{_fmt(human)}</td>{''.join(cells)}</tr>"
        )
    head = "".join(f"<th>{g} run 1</th><th>{g} range (5 runs)</th>" for g in GRADERS)
    return HTMLResponse(
        f"<!doctype html><meta charset='utf-8'><title>AI outputs</title>{STYLE}<div style='padding:16px'>"
        "<h2>AI outputs next to the human labels</h2>"
        "<p class='muted'>Scores out of 10. Red: AI higher than the human, blue: lower. "
        "v1's feedback text was not recorded in these runs.</p>"
        f"<table><tr><th>answer</th><th>source</th><th>human</th>{head}</tr>{''.join(rows)}</table></div>"
    )


@app.get("/files/{answer_id}/{name}")
def answer_file(answer_id: str, name: str):
    path = (ANSWERS_DIR / answer_id / name).resolve()
    if ANSWERS_DIR.resolve() not in path.parents or not path.is_file():
        return HTMLResponse("not found", status_code=404)
    return FileResponse(path)


@app.get("/answer/{answer_id}")
def answer_page(answer_id: str):
    row = next((r for r in read_manifest() if r["answer_id"] == answer_id), None)
    if row is None:
        return HTMLResponse("not found", status_code=404)
    problem = next(p for p in load_problems() if p["id"] == row["problem_id"])
    rubric = load_rubrics()[row["problem_id"]]
    label = _labels().get(answer_id, {})
    runs = {g: _runs(g).get(answer_id, []) for g in GRADERS}

    pages = "".join(
        f"<embed src='/files/{answer_id}/{p.name}' type='application/pdf'>" if p.suffix.lower() == ".pdf"
        else f"<a href='/files/{answer_id}/{p.name}' target='_blank'><img src='/files/{answer_id}/{p.name}'></a>"
        for p in sorted((ANSWERS_DIR / answer_id).glob("page-*"))
    )

    human_steps = label.get("awarded", {})
    step_rows = []
    for step in rubric.steps:
        human = human_steps.get(step.id)
        cells = "".join(
            "".join(_cell(r["awarded"].get(step.id), human) for r in runs[g]) or "<td>–</td>"
            for g in ("v2", "v3")
        )
        reasons = "".join(
            f"<div><b>{g}:</b> {html.escape(runs[g][0]['reasons'].get(step.id, ''))}</div>" for g in ("v2", "v3") if runs[g]
        )
        step_rows.append(
            f"<tr><td><code>{step.id}</code><div class='muted'>{html.escape(step.criterion)}</div></td>"
            f"<td class='num'>{_fmt(human)} / {step.points}</td>{cells}<td>{reasons}</td></tr>"
        )
    n2, n3 = len(runs["v2"]) or 1, len(runs["v3"]) or 1
    totals = "".join(
        f"<tr><td>{g}</td><td class='num'>{', '.join(_fmt(r['total']) for r in runs[g]) or '–'}</td>"
        f"<td class='num'>{statistics.pstdev([r['total'] for r in runs[g]]):.2f}</td></tr>"
        if len(runs[g]) > 1 else f"<tr><td>{g}</td><td>–</td><td>–</td></tr>"
        for g in GRADERS
    )
    transcriptions = "".join(
        f"<details><summary>v3 run {r['sample'] + 1} transcription (total {_fmt(r['total'])})</summary>"
        f"<pre>{html.escape(r.get('transcription') or '')}</pre></details>"
        for r in runs["v3"]
    )

    return HTMLResponse(f"""<!doctype html><meta charset="utf-8"><title>{answer_id}</title>{STYLE}
<div class="wrap"><div class="left">{pages}</div><div class="right">
 <p><a href="/">← all answers</a> · {answer_id} · {row['source']}</p>
 <pre>{html.escape(problem['statement_ja'])}</pre>
 <p><b>Answer key:</b> {html.escape(problem['answer'])}</p>

 <h3>Human label: {_fmt(label.get('total'))} / {rubric.max_points}</h3>
 <p><b>Note:</b> {html.escape(label.get('note', '') or '–')}</p>
 <p><b>AIの採点で注意すべき点:</b> {html.escape(label.get('ai_caution', '') or '–')}</p>

 <h3>Totals over the 5 runs</h3>
 <table><tr><th>grader</th><th>totals</th><th>SD</th></tr>{totals}</table>

 <h3>Steps</h3>
 <table><tr><th>step</th><th>human</th><th colspan="{n2}">v2 runs</th><th colspan="{n3}">v3 runs</th><th>reasons (run 1)</th></tr>
 {''.join(step_rows)}</table>

 <h3>v3 transcriptions</h3>{transcriptions or '<p>–</p>'}
</div></div>""")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--labeler", default="A")
    parser.add_argument("--port", type=int, default=8200)
    args = parser.parse_args()
    LABELER = args.labeler
    uvicorn.run(app, host="127.0.0.1", port=args.port)
