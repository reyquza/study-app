"""Compare every grader version with the human labels and write the results.

    cd backend
    uv run python ../eval/analyze.py

Reads eval/data/ (labels and runs) and writes only anonymous aggregates to eval/results/:
summary.md, summary.json, per_answer.csv and figures. Every number in the report comes
from here.

Versions: v1 holistic, v2 rubric from the image, v3 transcribe then rubric (first sample),
and v4, the per-step median of v3's samples, whose spread across samples is the
"needs review" signal.

Decision rules fixed before looking at results:
- Error is AI total minus human total, in points out of 10. A "large error" is |error| >= 2.
- v4 flags an answer for review when its sample totals span 2 points or more.
- Confidence intervals: 95% percentile bootstrap over answers, 5000 resamples, seed 0.
"""

import csv
import json
import statistics
from collections import defaultdict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from common import LABELS_DIR, RESULTS_DIR, RUNS_DIR, load_rubrics, read_jsonl, read_manifest, step_part

LARGE_ERROR = 2
REVIEW_SPREAD = 2
BOOTSTRAP = 5000
VERSIONS = ["v1", "v2", "v3", "v4"]
VERSION_NAMES = {
    "v1": "v1 holistic",
    "v2": "v2 rubric",
    "v3": "v3 transcribe + rubric",
    "v4": "v4 median of 5 (v3)",
}

# Reference palette (dataviz skill): one series, so one hue; text and grid stay neutral.
SURFACE, INK, INK_2, GRID, SERIES = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df", "#2a78d6"


def latest_labels(labeler: str) -> dict[str, dict]:
    return {r["answer_id"]: r for r in read_jsonl(LABELS_DIR / f"human-{labeler}.jsonl")}


def runs_by_answer(grader: str) -> dict[str, list[dict]]:
    grouped: dict[str, dict[int, dict]] = defaultdict(dict)
    for record in read_jsonl(RUNS_DIR / f"{grader}.jsonl"):
        if record.get("error") is None:
            grouped[record["answer_id"]][record["sample"]] = record  # a re-run replaces a sample
    return {answer: [samples[k] for k in sorted(samples)] for answer, samples in grouped.items()}


def build_predictions(rubrics, manifest) -> tuple[dict, dict]:
    """version -> answer_id -> {"total", "awarded"?}, plus v1-v3 sample totals for consistency."""
    problem_of = {row["answer_id"]: row["problem_id"] for row in manifest}
    predictions: dict[str, dict[str, dict]] = {v: {} for v in VERSIONS}
    samples: dict[str, dict[str, list[float]]] = {}
    for grader in ["v1", "v2", "v3"]:
        runs = runs_by_answer(grader)
        samples[grader] = {a: [r["total"] for r in records] for a, records in runs.items()}
        for answer_id, records in runs.items():
            predictions[grader][answer_id] = {"total": records[0]["total"], "awarded": records[0].get("awarded")}

    for answer_id, records in runs_by_answer("v3").items():
        if len(records) < 3:
            continue
        rubric = rubrics[problem_of[answer_id]]
        awarded = {s.id: statistics.median(r["awarded"][s.id] for r in records) for s in rubric.steps}
        totals = [r["total"] for r in records]
        predictions["v4"][answer_id] = {
            "total": sum(awarded.values()),
            "awarded": awarded,
            "spread": max(totals) - min(totals),
            "sd": statistics.pstdev(totals),
        }
    return predictions, samples


def bootstrap_ci(values: np.ndarray, stat=np.mean) -> list[float]:
    if len(values) < 2:
        return [float("nan"), float("nan")]
    rng = np.random.default_rng(0)
    resampled = [stat(values[rng.integers(0, len(values), len(values))]) for _ in range(BOOTSTRAP)]
    return [float(np.percentile(resampled, 2.5)), float(np.percentile(resampled, 97.5))]


def spearman(x: list[float], y: list[float]) -> float:
    def ranks(values):
        order = np.argsort(values, kind="stable")
        r = np.empty(len(values))
        r[order] = np.arange(len(values))
        for v in set(values):  # average ranks for ties
            idx = [i for i, value in enumerate(values) if value == v]
            r[idx] = np.mean(r[idx])
        return r

    if len(x) < 3 or len(set(x)) < 2 or len(set(y)) < 2:
        return float("nan")
    return float(np.corrcoef(ranks(x), ranks(y))[0, 1])


def accuracy(pairs: list[tuple[float, float]]) -> dict:
    ai = np.array([a for a, _ in pairs], dtype=float)
    human = np.array([h for _, h in pairs], dtype=float)
    errors = ai - human
    abs_errors = np.abs(errors)
    return {
        "n": len(pairs),
        "mae": float(abs_errors.mean()),
        "mae_ci95": bootstrap_ci(abs_errors),
        "bias": float(errors.mean()),
        "bias_ci95": bootstrap_ci(errors),
        "large_error_rate": float((abs_errors >= LARGE_ERROR).mean()),
        "max_abs_error": float(abs_errors.max()),
        "pearson_r": float(np.corrcoef(ai, human)[0, 1]) if len(pairs) > 2 and ai.std() and human.std() else float("nan"),
    }


def analyze() -> dict:
    rubrics = load_rubrics()
    manifest = read_manifest()
    source_of = {row["answer_id"]: row["source"] for row in manifest}
    problem_of = {row["answer_id"]: row["problem_id"] for row in manifest}
    human = latest_labels("A")
    second = latest_labels("B")
    predictions, samples = build_predictions(rubrics, manifest)

    summary: dict = {"answers": len(manifest), "labeled": len(human), "versions": {}}
    for version in VERSIONS:
        common_ids = sorted(set(predictions[version]) & set(human))
        if not common_ids:
            continue
        result = {"overall": accuracy([(predictions[version][a]["total"], human[a]["total"]) for a in common_ids])}

        for source in ["participant", "author"]:
            ids = [a for a in common_ids if source_of[a] == source]
            if ids:
                result[source] = accuracy([(predictions[version][a]["total"], human[a]["total"]) for a in ids])

        if version != "v1":
            # Error by sub-question: (1) textbook, (2) standard, (3) Kyushu level.
            by_part: dict[str, list[float]] = defaultdict(list)
            step_matches = []
            for a in common_ids:
                rubric = rubrics[problem_of[a]]
                ai_steps, human_steps = predictions[version][a]["awarded"], human[a]["awarded"]
                parts: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
                for step in rubric.steps:
                    parts[step_part(step)][0] += ai_steps[step.id]
                    parts[step_part(step)][1] += human_steps[step.id]
                    step_matches.append(ai_steps[step.id] == human_steps[step.id])
                for part, (ai_pts, human_pts) in parts.items():
                    by_part[part].append(abs(ai_pts - human_pts))
            result["mae_by_part"] = {p: float(np.mean(v)) for p, v in sorted(by_part.items())}
            result["step_agreement"] = float(np.mean(step_matches))

        if version in samples:
            spreads = [statistics.pstdev(t) for a, t in samples[version].items() if len(t) >= 2]
            if spreads:
                result["consistency"] = {
                    "answers": len(spreads),
                    "mean_sd": float(np.mean(spreads)),
                    "share_varying": float(np.mean([s > 0 for s in spreads])),
                }
        summary["versions"][version] = result

    # Can v4's disagreement between samples tell when its score is wrong?
    v4_ids = sorted(set(predictions["v4"]) & set(human))
    if v4_ids:
        abs_err = [abs(predictions["v4"][a]["total"] - human[a]["total"]) for a in v4_ids]
        spread = [predictions["v4"][a]["spread"] for a in v4_ids]
        flagged = [s >= REVIEW_SPREAD for s in spread]
        large = [e >= LARGE_ERROR for e in abs_err]
        kept = [e for e, f in zip(abs_err, flagged) if not f]
        summary["review_flag"] = {
            "rule": f"sample totals span >= {REVIEW_SPREAD} points",
            "spearman_spread_vs_abs_error": spearman(spread, abs_err),
            "flagged": int(sum(flagged)),
            "large_errors": int(sum(large)),
            "large_errors_flagged": int(sum(f and l for f, l in zip(flagged, large))),
            "mae_unflagged": float(np.mean(kept)) if kept else float("nan"),
            "mae_all": float(np.mean(abs_err)),
        }

    overlap = sorted(set(human) & set(second))
    if overlap:
        summary["human_vs_human"] = accuracy([(second[a]["total"], human[a]["total"]) for a in overlap])

    for version in VERSIONS:
        records = [r for rs in runs_by_answer(version).values() for r in rs] if version != "v4" else []
        if records:
            usage = [r.get("usage") or {} for r in records]
            summary["versions"][version]["cost"] = {
                "mean_latency_seconds": float(np.mean([r["latency_seconds"] for r in records])),
                "mean_input_tokens": float(np.mean([u.get("input_tokens", 0) for u in usage])),
                "mean_output_tokens": float(np.mean([u.get("output_tokens", 0) + u.get("thinking_tokens", 0) for u in usage])),
            }

    write_outputs(summary, manifest, human, predictions)
    return summary


def write_outputs(summary, manifest, human, predictions) -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    with open(RESULTS_DIR / "per_answer.csv", "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["answer_id", "problem_id", "source", "human", *VERSIONS, "v4_spread"])
        for row in manifest:
            a = row["answer_id"]
            writer.writerow([
                a, row["problem_id"], row["source"], human.get(a, {}).get("total", ""),
                *[predictions[v].get(a, {}).get("total", "") for v in VERSIONS],
                predictions["v4"].get(a, {}).get("spread", ""),
            ])

    (RESULTS_DIR / "summary.md").write_text(render_markdown(summary), encoding="utf-8")
    plot(summary, human, predictions)


def _fmt(x, digits=2) -> str:
    return "–" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{digits}f}"


def render_markdown(summary: dict) -> str:
    lines = [
        f"Answers: {summary['answers']}, human-labeled: {summary['labeled']}. "
        f"Scores are out of 10; error = AI − human.",
        "",
        "| Grader | n | MAE (95% CI) | Bias (95% CI) | abs error ≥ 2 | Step agreement | SD across 5 runs | Latency |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for version, result in summary["versions"].items():
        o = result["overall"]
        consistency = result.get("consistency", {})
        lines.append(
            f"| {VERSION_NAMES[version]} | {o['n']} "
            f"| {_fmt(o['mae'])} ({_fmt(o['mae_ci95'][0])}–{_fmt(o['mae_ci95'][1])}) "
            f"| {_fmt(o['bias'], 2)} ({_fmt(o['bias_ci95'][0])}–{_fmt(o['bias_ci95'][1])}) "
            f"| {o['large_error_rate']:.0%} | {_fmt(result.get('step_agreement'))} "
            f"| {_fmt(consistency.get('mean_sd'))} | {_fmt(result.get('cost', {}).get('mean_latency_seconds'), 0)} s |"
        )
    parts = {v: r["mae_by_part"] for v, r in summary["versions"].items() if "mae_by_part" in r}
    if parts:
        names = sorted({p for d in parts.values() for p in d})
        lines += ["", "MAE by sub-question (points):", "", "| Grader | " + " | ".join(names) + " |",
                  "|---|" + "---|" * len(names)]
        for v, d in parts.items():
            lines.append(f"| {VERSION_NAMES[v]} | " + " | ".join(_fmt(d.get(p)) for p in names) + " |")
    if "review_flag" in summary:
        r = summary["review_flag"]
        lines += ["", f"Review flag ({r['rule']}): flagged {r['flagged']}, caught {r['large_errors_flagged']} of "
                      f"{r['large_errors']} large errors; MAE {_fmt(r['mae_all'])} overall, "
                      f"{_fmt(r['mae_unflagged'])} on unflagged answers; Spearman(spread, |error|) = "
                      f"{_fmt(r['spearman_spread_vs_abs_error'])}."]
    if "human_vs_human" in summary:
        h = summary["human_vs_human"]
        lines += ["", f"Human vs human (n = {h['n']}): MAE {_fmt(h['mae'])} "
                      f"({_fmt(h['mae_ci95'][0])}–{_fmt(h['mae_ci95'][1])})."]
    return "\n".join(lines) + "\n"


def _style(ax) -> None:
    ax.set_facecolor(SURFACE)
    for side in ["top", "right"]:
        ax.spines[side].set_visible(False)
    for side in ["left", "bottom"]:
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=9)
    ax.grid(color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def plot(summary, human, predictions) -> None:
    versions = [v for v in VERSIONS if v in summary["versions"]]
    if not versions:
        return
    plt.rcParams.update({"font.family": "sans-serif", "text.color": INK, "axes.labelcolor": INK_2})

    # MAE with 95% CI per grader: one series, one hue.
    fig, ax = plt.subplots(figsize=(6.4, 0.6 + 0.55 * len(versions)), facecolor=SURFACE)
    _style(ax)
    for i, v in enumerate(reversed(versions)):
        o = summary["versions"][v]["overall"]
        lo, hi = o["mae_ci95"]
        ax.plot([lo, hi], [i, i], color=SERIES, linewidth=2, solid_capstyle="round")
        ax.plot(o["mae"], i, "o", color=SERIES, markersize=8, markeredgecolor=SURFACE, markeredgewidth=2)
        ax.annotate(f"{o['mae']:.2f}", (o["mae"], i), xytext=(0, 9), textcoords="offset points",
                    ha="center", fontsize=9, color=INK)
    ax.set_yticks(range(len(versions)), [VERSION_NAMES[v] for v in reversed(versions)], color=INK)
    ax.set_xlabel("Mean absolute error vs human (points out of 10), 95% CI")
    ax.set_xlim(left=-0.08)
    ax.set_ylim(-0.6, len(versions) - 0.4)
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    fig.savefig(RESULTS_DIR / "mae_by_grader.png", dpi=200)
    plt.close(fig)

    # AI vs human per grader, small multiples; on the diagonal means exact agreement.
    fig, axes = plt.subplots(1, len(versions), figsize=(2.6 * len(versions), 2.9), facecolor=SURFACE, sharey=True)
    axes = np.atleast_1d(axes)
    rng = np.random.default_rng(0)
    for ax, v in zip(axes, versions):
        _style(ax)
        ids = sorted(set(predictions[v]) & set(human))
        h = np.array([human[a]["total"] for a in ids], dtype=float)
        p = np.array([predictions[v][a]["total"] for a in ids], dtype=float)
        jitter = rng.uniform(-0.12, 0.12, size=(2, len(ids)))  # separates identical scores
        ax.plot([0, 10], [0, 10], color=INK_2, linewidth=1, linestyle=(0, (3, 3)))
        ax.scatter(h + jitter[0], p + jitter[1], s=36, color=SERIES, edgecolor=SURFACE, linewidth=1.5, zorder=3)
        ax.set_title(VERSION_NAMES[v], fontsize=10, color=INK)
        ax.set_xlim(-0.5, 10.5)
        ax.set_ylim(-0.5, 10.5)
        ax.set_xlabel("Human score")
        ax.set_aspect("equal")
    axes[0].set_ylabel("AI score")
    fig.tight_layout()
    fig.savefig(RESULTS_DIR / "ai_vs_human.png", dpi=200)
    plt.close(fig)


if __name__ == "__main__":
    summary = analyze()
    print((RESULTS_DIR / "summary.md").read_text(encoding="utf-8"))
