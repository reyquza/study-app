"""Turn the model's written feedback into safe HTML.

The model writes plain text with some Markdown (### headings, **bold**, "- " lists) and LaTeX
in $...$ or $$...$$, and it often runs the parts of an answer together ("... 1. (2) From (1),
..."). This renders that subset and nothing more:

- Everything is HTML-escaped first; only the tags made here are added, so text from the
  model (or from an answer that tries to inject markup) cannot run as HTML.
- Math is set aside before any Markdown is applied and put back unchanged, so `*`, `_` and
  `#` inside formulas stay as written for KaTeX.
- A part label like "(2)" or "（2）" starts a new paragraph when it follows the end of a
  sentence, a colon or a 【heading】. References inside a sentence ("From (1), ...") are left alone.
"""

import html
import re

from markupsafe import Markup

_MATH = re.compile(r"\$\$.+?\$\$|\$[^$\n]+?\$", re.S)
_STASHED = re.compile("\x00(\\d+)\x00")
# A part label right after the end of a sentence, a colon or a 【heading】.
_PART_START = re.compile(r"(?:(?<=[。．.!?！？】:：])|(?<=[。．.!?！？】:：]\s))\s*(?=[（(][1-9][0-9]?[)）])")
_HEADING = re.compile(r"^#{1,6}\s+(.*)$")
_LIST_ITEM = re.compile(r"^(?:[-*・]|\d+\.)\s+(.*)$")
_BOLD = re.compile(r"\*\*(.+?)\*\*")


def render_ai_text(text: str | None) -> Markup:
    if not text:
        return Markup("")
    maths: list[str] = []

    def stash(match: re.Match) -> str:
        maths.append(match.group(0))
        return f"\x00{len(maths) - 1}\x00"

    text = _MATH.sub(stash, text.replace("\r\n", "\n").strip())
    text = html.escape(text, quote=False)
    text = _PART_START.sub("\n\n", text)

    blocks: list[str] = []
    for chunk in re.split(r"\n\s*\n", text):
        blocks.extend(_render_block([line.strip() for line in chunk.split("\n") if line.strip()]))

    rendered = "\n".join(blocks)
    rendered = _BOLD.sub(r"<strong>\1</strong>", rendered)
    rendered = _STASHED.sub(lambda m: html.escape(maths[int(m.group(1))], quote=False), rendered)
    return Markup(rendered)


def _render_block(lines: list[str]) -> list[str]:
    """Group a block's lines into headings, lists and paragraphs."""
    out: list[str] = []
    paragraph: list[str] = []
    items: list[str] = []

    def flush() -> None:
        if paragraph:
            out.append("<p>" + "<br>".join(paragraph) + "</p>")
            paragraph.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>")
            items.clear()

    for line in lines:
        heading = _HEADING.match(line)
        item = _LIST_ITEM.match(line)
        if heading:
            flush()
            out.append(f'<p class="ai-heading"><strong>{heading.group(1)}</strong></p>')
        elif item:
            if paragraph:
                flush()
            items.append(item.group(1))
        else:
            if items:
                flush()
            paragraph.append(line)
    flush()
    return out
