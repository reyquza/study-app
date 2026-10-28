"""Render each problem as a PNG (Japanese and English) for the answer-collection form.

    uv run --no-project --with matplotlib python eval/scripts/render_problem_sheets.py

Lines are broken by hand here: automatic wrapping would split math or Japanese badly.
The wording must match eval/problems.yaml.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

OUT_DIR = Path(__file__).resolve().parent.parent / "problem_sheets"
JA_FONT = Path("C:/Windows/Fonts/YuGothM.ttc")

SHEETS = {
    "P1": (
        ["放物線 $C: y = x^2$ と点 $A(1, -3)$ がある。",
         "(1) 点 $A$ を通る $C$ の接線の方程式をすべて求めよ。",
         "(2) (1) で求めた接線と $C$ で囲まれた部分の面積 $S$ を求めよ。",
         "(3) $a$ を実数とし、点 $(a, -3)$ を通る $C$ の2本の接線と $C$ で",
         "     囲まれた部分の面積を $S(a)$ とする。$S(a)$ の最小値を求めよ。"],
        ["Let $C$ be the parabola $y = x^2$ and let $A = (1, -3)$.",
         "(1) Find the equations of all tangent lines to $C$ that pass through $A$.",
         "(2) Find the area $S$ of the region bounded by the tangent lines from (1) and $C$.",
         "(3) For a real number $a$, let $S(a)$ be the area of the region bounded by $C$",
         "     and the two tangent lines to $C$ through $(a, -3)$. Find the minimum of $S(a)$."],
    ),
    "P2": (
        ["正三角形 $ABC$ の頂点上を動く点 $P$ があり、最初は頂点 $A$ にある。",
         "1回の試行で硬貨を1枚投げ、表が出れば時計回りに、裏が出れば反時計回りに、",
         "隣の頂点へ移る。$n$ 回の試行の後に $P$ が $A$ にある確率を $p_n$ とする。",
         "(1) $p_1,\\ p_2$ を求めよ。",
         "(2) $p_{n+1}$ を $p_n$ を用いて表し、$p_n$ を求めよ。",
         "(3) $n \\geq 2$ とする。$n$ 回目の試行で初めて $P$ が $A$ に戻る確率 $q_n$ を求めよ。"],
        ["A point $P$ moves among the vertices of an equilateral triangle $ABC$, starting at $A$.",
         "In each trial a coin is tossed: on heads $P$ moves to the next vertex clockwise,",
         "on tails counterclockwise. Let $p_n$ be the probability that $P$ is at $A$ after $n$ trials.",
         "(1) Find $p_1$ and $p_2$.",
         "(2) Express $p_{n+1}$ in terms of $p_n$, and find $p_n$.",
         "(3) Let $n \\geq 2$. Find the probability $q_n$ that $P$ returns to $A$",
         "     for the first time at the $n$-th trial."],
    ),
    "P3": (
        ["$n$ を整数とする。",
         "(1) $n^2 + n + 1$ を $3$ で割った余りは $0$ または $1$ であることを示せ。",
         "(2) $n^2 + n + 1$ は $9$ の倍数でないことを示せ。",
         "(3) $n^2 + n + 1 = 3^k$ を満たす整数 $n$ と $0$ 以上の整数 $k$ の",
         "     組 $(n, k)$ をすべて求めよ。"],
        ["Let $n$ be an integer.",
         "(1) Show that the remainder when $n^2 + n + 1$ is divided by $3$ is $0$ or $1$.",
         "(2) Show that $n^2 + n + 1$ is not a multiple of $9$.",
         "(3) Find all pairs $(n, k)$ of an integer $n$ and a non-negative integer $k$",
         "     with $n^2 + n + 1 = 3^k$."],
    ),
    "P4": (
        ["$k$ を定数とし、$f(x) = x^3 - 3x^2 - 9x + k$ とする。",
         "(1) $f(x)$ の極大値と極小値を $k$ を用いて表せ。",
         "(2) 方程式 $f(x) = 0$ が異なる3つの実数解をもつような $k$ の値の範囲を求めよ。",
         "(3) $k$ が (2) の範囲にあるとき、$f(x) = 0$ の3つの実数解を $\\alpha < \\beta < \\gamma$ とする。",
         "     $\\alpha$ のとりうる値の範囲を求めよ。"],
        ["Let $k$ be a constant and $f(x) = x^3 - 3x^2 - 9x + k$.",
         "(1) Express the local maximum and minimum values of $f(x)$ in terms of $k$.",
         "(2) Find all $k$ for which $f(x) = 0$ has three distinct real solutions.",
         "(3) For $k$ in the range from (2), let the solutions be $\\alpha < \\beta < \\gamma$.",
         "     Find the range of possible values of $\\alpha$."],
    ),
}

LINE_HEIGHT = 0.42  # inches
WIDTH = 10.0


def render(problem_id: str, ja: list[str], en: list[str]) -> Path:
    font_manager.fontManager.addfont(str(JA_FONT))
    ja_family = font_manager.FontProperties(fname=str(JA_FONT)).get_name()
    plt.rcParams.update({"font.family": ja_family, "mathtext.fontset": "cm"})

    rows = [("title", f"問題{problem_id[1:]}  /  Problem {problem_id[1:]}")]
    rows += [("ja", line) for line in ja] + [("gap", "")] + [("en", line) for line in en]
    height = 0.5 + LINE_HEIGHT * (len(rows) + 1)

    fig = plt.figure(figsize=(WIDTH, height), dpi=150)
    fig.patch.set_facecolor("white")
    y = height - 0.55
    for kind, text in rows:
        if kind == "title":
            fig.text(0.4 / WIDTH, y / height, text, fontsize=17, weight="bold", color="#1565C0")
            y -= LINE_HEIGHT * 1.35
        elif kind == "gap":
            y -= LINE_HEIGHT * 0.45
        else:
            fig.text(0.4 / WIDTH, y / height, text, fontsize=15 if kind == "ja" else 14,
                     color="#222" if kind == "ja" else "#555")
            y -= LINE_HEIGHT

    path = OUT_DIR / f"{problem_id}.png"
    fig.savefig(path, facecolor="white", bbox_inches="tight", pad_inches=0.35)
    plt.close(fig)
    return path


if __name__ == "__main__":
    OUT_DIR.mkdir(exist_ok=True)
    for problem_id, (ja, en) in SHEETS.items():
        print(render(problem_id, ja, en))
