"""Local page for grading answers by hand against the rubric: the ground truth.

    cd backend
    uv run python ../eval/label.py               # grader A (the author)
    uv run python ../eval/label.py --labeler B   # a second grader, for human-human agreement

Open http://127.0.0.1:8100. Answers come in a fixed random order, and AI results are never
shown, so the human grade is blind. Labels go to eval/data/labels/human-<labeler>.jsonl;
saving an answer again replaces the earlier label.
"""

import argparse
import html
import random
from datetime import datetime, timezone

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from common import ANSWERS_DIR, LABELS_DIR, append_jsonl, load_problems, load_rubrics, read_jsonl, read_manifest

app = FastAPI()
LABELER = "A"


def _labels_path():
    return LABELS_DIR / f"human-{LABELER}.jsonl"


def _latest_labels() -> dict[str, dict]:
    return {record["answer_id"]: record for record in read_jsonl(_labels_path())}


def _order() -> list[dict]:
    rows = read_manifest()
    random.Random(f"order-{LABELER}").shuffle(rows)
    return rows


@app.get("/")
def next_unlabeled():
    labels = _latest_labels()
    for row in _order():
        if row["answer_id"] not in labels:
            return RedirectResponse(f"/answer/{row['answer_id']}", status_code=303)
    return HTMLResponse(f"<p style='font:18px sans-serif;padding:40px'>All {len(labels)} answers are labeled. "
                        f"<a href='/list'>Review the list</a></p>")


@app.get("/list")
def answer_list():
    labels = _latest_labels()
    items = "".join(
        f"<li><a href='/answer/{r['answer_id']}'>{r['answer_id']}</a> "
        f"{'— ' + str(labels[r['answer_id']]['total']) + ' pt' if r['answer_id'] in labels else '(not labeled)'}</li>"
        for r in _order()
    )
    return HTMLResponse(f"<ul style='font:16px sans-serif'>{items}</ul>")


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
        return RedirectResponse("/", status_code=303)
    problem = next(p for p in load_problems() if p["id"] == row["problem_id"])
    rubric = load_rubrics()[row["problem_id"]]
    previous = _latest_labels().get(answer_id, {})
    order = [r["answer_id"] for r in _order()]
    position = order.index(answer_id) + 1

    pages = "".join(
        f"<embed src='/files/{answer_id}/{p.name}' type='application/pdf' class='page pdf'>"
        if p.suffix.lower() == ".pdf"
        else f"<a href='/files/{answer_id}/{p.name}' target='_blank'><img src='/files/{answer_id}/{p.name}' class='page'></a>"
        for p in sorted((ANSWERS_DIR / answer_id).glob("page-*"))
    )
    steps = "".join(
        f"<tr><td><code>{s.id}</code></td><td class='crit'>{html.escape(s.criterion)}</td>"
        f"<td><input type='number' name='step:{s.id}' min='0' max='{s.points}' required "
        f"value='{previous.get('awarded', {}).get(s.id, '')}'> / {s.points}</td></tr>"
        for s in rubric.steps
    )
    extras = "".join(f"<li>{html.escape(x)}</li>" for x in (*rubric.alternatives, *rubric.caps))
    checked = lambda flag: "checked" if flag in previous.get("flags", []) else ""  # noqa: E731

    return HTMLResponse(f"""<!doctype html><html><head><meta charset="utf-8">
<title>{answer_id}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js"
  onload="renderMathInElement(document.body,{{delimiters:[{{left:'$',right:'$',display:false}}]}})"></script>
<style>
 body {{ font: 15px/1.5 sans-serif; margin: 0; display: flex; height: 100vh; }}
 .left {{ flex: 1.3; overflow: auto; background: #333; padding: 8px; }}
 .right {{ flex: 1; overflow: auto; padding: 16px 20px; }}
 .page {{ width: 100%; margin-bottom: 8px; background: white; }}
 .pdf {{ height: 95vh; }}
 table {{ border-collapse: collapse; width: 100%; }}
 td {{ border-top: 1px solid #ddd; padding: 6px 4px; vertical-align: top; }}
 input[type=number] {{ width: 3.5em; font-size: 16px; }}
 .crit {{ font-size: 14px; }}
 .meta {{ color: #666; }}
 button {{ font-size: 16px; padding: 8px 20px; margin-top: 12px; }}
 pre {{ white-space: pre-wrap; background: #f6f6f6; padding: 8px; }}
</style></head><body>
<div class="left">{pages}</div>
<div class="right">
 <div class="meta">{position} / {len(order)} · {answer_id} · labeler {LABELER} · <a href="/list">list</a></div>
 <h3>{row['problem_id']}</h3>
 <pre>{html.escape(problem['statement_ja'])}</pre>
 <p><b>Answer:</b> {html.escape(problem['answer'])}</p>
 <form method="post">
  <table>{steps}</table>
  <ul class="crit">{extras}</ul>
  <label><input type="checkbox" name="flag:blank" {checked('blank')}> blank / no attempt</label><br>
  <label><input type="checkbox" name="flag:illegible" {checked('illegible')}> hard to read</label><br>
  <textarea name="note" rows="2" style="width:100%" placeholder="note">{html.escape(previous.get('note', ''))}</textarea>
  <label for="ai_caution"><b>AIの採点で注意すべき点</b>（任意。例：(3)で根拠のない公式利用に点を与えそう／薄い字を読み違えそう）</label>
  <textarea id="ai_caution" name="ai_caution" rows="3" style="width:100%">{html.escape(previous.get('ai_caution', ''))}</textarea>
  <button>Save and next</button>
 </form>
</div></body></html>""")


@app.post("/answer/{answer_id}")
async def save_label(answer_id: str, request: Request):
    form = await request.form()
    rubric = load_rubrics()[next(r["problem_id"] for r in read_manifest() if r["answer_id"] == answer_id)]
    awarded = {s.id: min(max(int(form.get(f"step:{s.id}") or 0), 0), s.points) for s in rubric.steps}
    append_jsonl(_labels_path(), {
        "answer_id": answer_id,
        "labeler": LABELER,
        "awarded": awarded,
        "total": sum(awarded.values()),
        "flags": [key.removeprefix("flag:") for key in form if key.startswith("flag:")],
        "note": form.get("note", ""),
        # What the human grader expects an AI grader to get wrong on this answer; compared
        # with the AI's actual mistakes after the AI results are opened.
        "ai_caution": form.get("ai_caution", ""),
        "at": datetime.now(timezone.utc).isoformat(),
    })
    return RedirectResponse("/", status_code=303)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--labeler", default="A")
    parser.add_argument("--port", type=int, default=8100)
    LABELER = parser.parse_args().labeler
    uvicorn.run(app, host="127.0.0.1", port=parser.parse_args().port)
