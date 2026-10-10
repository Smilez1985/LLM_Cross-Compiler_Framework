#!/usr/bin/env python3
"""
HTML to Markdown-like text for the knowledge base.

Keeps the structure the chunker needs: headings, paragraphs, lists, fenced code (with its whitespace) and
tables. Scripts and styles are never content; which page furniture (nav, footer, ...) to drop is the
caller's decision.
"""

import re
from typing import List, Tuple, Union

from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from bs4.element import Declaration, Doctype, ProcessingInstruction

_HEADINGS = {f"h{i}": i for i in range(1, 7)}
_BLOCK_TAGS = {"p", "div", "section", "article", "main", "blockquote", "figure", "figcaption", "dl", "dt", "dd",
               "details", "summary", "address", "body", "html", "center", "li", "form", "fieldset"}
_STRUCTURAL = set(_HEADINGS) | _BLOCK_TAGS | {"pre", "table", "ul", "ol", "hr"}
_SKIP_STRINGS = (Comment, Doctype, Declaration, ProcessingInstruction)
_LANG_CLASS = re.compile(r"^(?:language|lang|highlight-source|brush:?)-?(\w[\w+#-]*)$")


def html_to_markdown(html: Union[str, BeautifulSoup, Tag]) -> Tuple[str, str]:
    """Returns (title, markdown). Accepts markup or an already cleaned soup."""
    soup = html if isinstance(html, (BeautifulSoup, Tag)) else BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    title_tag = soup.find("title")
    title = _inline(title_tag).strip() if title_tag else ""
    if title_tag:
        title_tag.decompose()
    blocks: List[str] = []
    try:
        _render(soup.body or soup, blocks)
    except RecursionError:
        # Pathologically nested markup: plain text is better than nothing.
        blocks = [soup.get_text("\n", strip=True)]
    markdown = "\n\n".join(b for b in blocks if b.strip())
    if not title:
        h1 = re.search(r"^# (.+)$", markdown, re.MULTILINE)
        title = h1.group(1).strip() if h1 else ""
    return title, markdown


def _squash(text: str) -> str:
    """Collapses whitespace but keeps line breaks that came from <br>."""
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def _inline(node) -> str:
    if isinstance(node, NavigableString):
        return "" if isinstance(node, _SKIP_STRINGS) else re.sub(r"\s+", " ", str(node))
    if not isinstance(node, Tag):
        return ""
    if node.name == "br":
        return "\n"
    inner = "".join(_inline(child) for child in node.children)
    if node.name == "code":
        text = inner.strip()
        return f"`{text}`" if text and "`" not in text else text
    return inner


def _code_language(pre: Tag) -> str:
    for tag in [pre] + pre.find_all("code", limit=1):
        for cls in tag.get("class") or []:
            match = _LANG_CLASS.match(cls)
            if match:
                return match.group(1).lower()
    return ""


def _fenced(pre: Tag) -> str:
    code = pre.get_text().strip("\n")
    fence = "```"
    while fence in code:
        fence += "`"
    return f"{fence}{_code_language(pre)}\n{code}\n{fence}"


def _table(table: Tag) -> str:
    rows = []
    for tr in table.find_all("tr"):
        cells = [_squash(_inline(c)).replace("\n", " ").replace("|", "\\|") for c in tr.find_all(["th", "td"])]
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def _list(node: Tag, depth: int = 0) -> str:
    ordered = node.name == "ol"
    lines = []
    for number, li in enumerate(node.find_all("li", recursive=False), start=1):
        nested = [child for child in li.children if isinstance(child, Tag) and child.name in ("ul", "ol")]
        own = [child for child in li.children if all(child is not sub for sub in nested)]
        text = _squash("".join(_inline(child) for child in own)).replace("\n", " ")
        marker = f"{number}." if ordered else "-"
        if text:
            lines.append(f"{'  ' * depth}{marker} {text}")
        lines.extend(_list(sub, depth + 1) for sub in nested)
    return "\n".join(line for line in lines if line)


def _render(node: Tag, blocks: List[str]) -> None:
    buffer: List[str] = []

    def flush():
        text = _squash("".join(buffer))
        if text:
            blocks.append(text)
        buffer.clear()

    for child in node.children:
        if isinstance(child, NavigableString):
            if not isinstance(child, _SKIP_STRINGS):
                buffer.append(str(child))
            continue
        if not isinstance(child, Tag):
            continue
        name = child.name
        if name in _HEADINGS:
            flush()
            text = _squash(_inline(child)).replace("\n", " ")
            if text:
                blocks.append(f"{'#' * _HEADINGS[name]} {text}")
        elif name == "pre":
            flush()
            blocks.append(_fenced(child))
        elif name == "table":
            flush()
            blocks.append(_table(child))
        elif name in ("ul", "ol"):
            flush()
            blocks.append(_list(child))
        elif name == "hr":
            flush()
        elif name == "br":
            buffer.append("\n")
        elif name in _BLOCK_TAGS or child.find(_STRUCTURAL):
            flush()
            _render(child, blocks)
        else:
            buffer.append(_inline(child))
    flush()
