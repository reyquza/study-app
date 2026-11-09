"""The model's feedback is shown as laid-out, safe HTML (bugs: raw Markdown on the page,
parts (1)(2)(3) run together, stray indentation)."""

from app.web.formatting import render_ai_text
from tests.factories import make_problem, make_submission


def test_markdown_headings_bold_and_lists_are_rendered():
    html = render_ai_text("### Model Solution\n\n**(1)**\nCheck all cases:\n- If $n \\equiv 0$, it is 1.\n- If $n \\equiv 1$, it is 0.")

    assert '<p class="ai-heading"><strong>Model Solution</strong></p>' in html
    assert "<strong>(1)</strong>" in html
    assert "<ul><li>If $n \\equiv 0$, it is 1.</li><li>If $n \\equiv 1$, it is 0.</li></ul>" in html
    assert "###" not in html and "**" not in html


def test_parts_start_new_paragraphs_but_references_do_not():
    html = render_ai_text(
        "Model Solution: (1) By checking, the remainder is 0 or 1. (2) From (1), it is a multiple of 3 "
        "only if $n = 3m + 1$. It is never a multiple of 9. (3) Given $n^2+n+1 = 3^k$."
    )

    assert html.count("<p>") == 4  # "Model Solution:" then (1), (2), (3)
    assert "<p>(1) By checking" in html
    assert "<p>(2) From (1), it is" in html
    assert "<p>(3) Given" in html


def test_japanese_parts_start_new_paragraphs():
    html = render_ai_text("(1) 接線は $y=6x-9$ です。(2) 面積は $16/3$ です。（3）最小値は $2\\sqrt{3}$。")

    assert html.count("<p>") == 3
    assert "<p>（3）最小値は" in html


def test_a_bracketed_heading_is_split_from_part_one():
    html = render_ai_text("【解法戦略】(1) 接線を求めます。")

    assert html == "<p>【解法戦略】</p>\n<p>(1) 接線を求めます。</p>"


def test_math_is_left_exactly_as_written():
    formula = "$$S = \\int_{-1}^{1} (x+1)^2 dx \\quad **not bold** \\; a_1 * b_2$$"

    html = render_ai_text(f"Area:\n{formula}")

    assert formula in html


def test_markup_from_the_model_is_escaped():
    html = render_ai_text('<script>alert(1)</script> **ok** <img src=x onerror=alert(1)>')

    assert "<script>" not in html and "<img" not in html
    assert "&lt;script&gt;" in html
    assert "<strong>ok</strong>" in html


def test_math_with_angle_brackets_is_escaped_too():
    html = render_ai_text("When $a<b$ and $b>c$, ...")

    assert "$a&lt;b$" in html and "$b&gt;c$" in html


def test_empty_and_whitespace_only():
    assert render_ai_text(None) == "" and render_ai_text("   \n  ") == ""


def test_result_page_has_no_stray_whitespace_or_raw_markdown(client, db, users):
    problem = make_problem(db, users["alice"].id)
    submission = make_submission(db, problem)
    submission.ai_feedback = "### 講評\n**よくできています。** (1) は正解です。(2) は惜しいです。"
    db.commit()

    page = client.get(f"/answer/{submission.id}").text

    assert '<div class="ai-text"><p class="ai-heading"><strong>講評</strong></p>' in page
    assert "<p>(2) は惜しいです。</p>" in page
    assert "###" not in page
