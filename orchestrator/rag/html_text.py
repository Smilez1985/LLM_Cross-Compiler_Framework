#!/usr/bin/env python3
"""
HTML to Markdown-like text for the knowledge base.

Keeps the structure the chunker needs: headings, paragraphs, lists, fenced code (with its whitespace) and
tables. Scripts and styles are never content; which page furniture (nav, footer, ...) to drop is the
caller's decision.

Text a person reading the page does not see never reaches the knowledge base (hidden carriers of prompt injection,
recon P1). strip_boilerplate drops the page furniture and then, through strip_hidden, every element hidden by its
inline style (display:none, visibility:hidden, a zero or near-zero font size or opacity, off-screen positioning, a clip
that leaves no visible box, text in the colour of its own background), by the hidden attribute or aria-hidden="true",
and <template> and <noscript>. An inactive tab panel stays (role="tabpanel" on a page with tabs: the person opens it
with a click). strip_hidden_markup applies the same rule (hidden_kind) to HTML carried inside Markdown or plain text,
such as a knowledge pack's page. Rules in style sheets and classes (.sr-only, ...) are not seen: there is no CSS engine,
so the removal is partial. What was removed is counted by kind (HIDDEN_KINDS) and never kept.
"""

import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from bs4.element import Declaration, Doctype, ProcessingInstruction

_HEADINGS = {f"h{i}": i for i in range(1, 7)}
_BLOCK_TAGS = {"p", "div", "section", "article", "main", "blockquote", "figure", "figcaption", "dl", "dt", "dd",
               "details", "summary", "address", "body", "html", "center", "li", "form", "fieldset"}
_STRUCTURAL = set(_HEADINGS) | _BLOCK_TAGS | {"pre", "table", "ul", "ol", "hr"}
_SKIP_STRINGS = (Comment, Doctype, Declaration, ProcessingInstruction)
_LANG_CLASS = re.compile(r"^(?:language|lang|highlight-source|brush:?)-?(\w[\w+#-]*)$")
_LIST_BREAKERS = ("pre", "table")
# Rendered blocks that must not get a list marker in front: fences, tables, headings, lists.
_MARKDOWN_BLOCK_START = re.compile(r"^(?:`{3,}|\||#{1,6} |- |\d+\. )")


# --- what a person reading the page does not see -------------------------------------------------------------------

# Why an element was removed, in the order hidden_kind checks (and the person reads) them. Each counts the removed
# elements that held text; an element inside a removed one is not counted again.
HIDDEN_KINDS = ("template", "noscript", "hidden_attribute", "aria_hidden", "display_none", "visibility_hidden",
                "font_size_zero", "opacity_zero", "off_screen", "clipped", "same_colour")
# What the untrusted-text cleaning removes from outside text (utils/untrusted.clean_untrusted): characters, comments.
TEXT_KINDS = ("invisible_characters", "html_comments")
REMOVED_KINDS = HIDDEN_KINDS + TEXT_KINDS
# Page furniture strip_boilerplate drops first (its links are not followed either).
BOILERPLATE_TAGS = ("script", "style", "nav", "footer", "header", "aside", "svg")

_VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source",
                   "track", "wbr"})
# Hiding that a click toggles: an inactive tab panel stays when the page has tabs.
_TAB_TOGGLES = frozenset({"hidden_attribute", "aria_hidden", "display_none"})
_LENGTH = re.compile(r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+))(px|pt|pc|em|rem|ex|ch|cm|mm|in|q|vw|vh|vmin|vmax|%)?$")
# CSS pixels per unit: font-relative units at the default 16px, viewport units on a 1920x1080 screen.
_PX = {"px": 1.0, "pt": 4 / 3, "pc": 16.0, "em": 16.0, "rem": 16.0, "ex": 8.0, "ch": 8.0, "cm": 37.8, "mm": 3.78,
       "in": 96.0, "q": 0.945, "vw": 19.2, "vh": 10.8, "vmin": 10.8, "vmax": 19.2}
_NEAR_ZERO_FONT_PX = 2.0     # nobody reads text this small
_NEAR_ZERO_OPACITY = 0.05
_OFF_SCREEN_PX = -500.0      # left: -9999px, text-indent: -10000px, ...
_NAMED_COLOURS = {"white": "#ffffff", "black": "#000000", "red": "#ff0000", "lime": "#00ff00", "blue": "#0000ff",
                  "yellow": "#ffff00", "aqua": "#00ffff", "cyan": "#00ffff", "fuchsia": "#ff00ff",
                  "magenta": "#ff00ff", "silver": "#c0c0c0", "gray": "#808080", "grey": "#808080",
                  "maroon": "#800000", "olive": "#808000", "green": "#008000", "purple": "#800080",
                  "teal": "#008080", "navy": "#000080"}
_IMPORTANT = re.compile(r"\s*!\s*important\s*$")


def _declarations(style: Any) -> Dict[str, str]:
    """An inline style as {property: value}, lower case, without !important; a later declaration wins."""
    found: Dict[str, str] = {}
    for part in str(style or "").split(";"):
        name, colon, value = part.partition(":")
        if colon and name.strip():
            found[name.strip().lower()] = _IMPORTANT.sub("", value.strip().lower())
    return found


def _length(value: str) -> Tuple[Optional[float], str]:
    """(number, unit) of a CSS length ("" for a unitless number), (None, "") for anything else."""
    match = _LENGTH.match((value or "").strip())
    return (float(match.group(1)), match.group(2) or "") if match else (None, "")


def _px(value: str) -> Optional[float]:
    """A length in CSS pixels; None for a percentage, a unitless number other than 0, or anything else."""
    number, unit = _length(value)
    if number is None or unit == "%" or (unit == "" and number != 0):
        return None
    return number * _PX.get(unit, 1.0)


def _tiny_font(value: str) -> bool:
    """A font size (or the size of a font shorthand, before its "/line-height") nobody can read."""
    size = (value or "").split("/", 1)[0]
    number, unit = _length(size)
    if number is None:
        return False
    if unit == "%":
        return number <= 100 * _NEAR_ZERO_FONT_PX / 16
    px = _px(size)
    return px is not None and px <= _NEAR_ZERO_FONT_PX


def _off_screen(value: str) -> bool:
    number, unit = _length(value)
    if number is None:
        return False
    if unit in ("%", "vw", "vh", "vmin", "vmax"):
        return number <= -100
    px = _px(value)
    return px is not None and px <= _OFF_SCREEN_PX


def _clip_hides(value: str) -> bool:
    """clip: rect(top, right, bottom, left) that leaves no area."""
    match = re.fullmatch(r"rect\((.*)\)", (value or "").strip())
    parts = re.split(r"[\s,]+", match.group(1).strip()) if match else []
    if len(parts) != 4:
        return False
    top, right, bottom, left = (None if part == "auto" else _px(part) for part in parts)
    top, left = top or 0.0, left or 0.0   # auto: the edge of the box
    return (bottom is not None and bottom <= top) or (right is not None and right <= left)


def _clip_path_hides(value: str) -> bool:
    """clip-path: an inset() whose sides meet, a circle() or ellipse() of radius 0, a polygon() of one point."""
    match = re.fullmatch(r"(inset|circle|ellipse|polygon)\((.*)\)", (value or "").strip())
    if not match:
        return False
    shape, args = match.group(1), match.group(2).strip()
    if shape == "inset":
        sides = [_length(part) for part in args.split(" round ")[0].split()][:4]
        if not sides or any(n is None or (u != "%" and n != 0) for n, u in sides):
            return False   # only percentages meet whatever the size of the box
        top, right = sides[0], sides[1] if len(sides) > 1 else sides[0]
        bottom = sides[2] if len(sides) > 2 else top
        left = sides[3] if len(sides) > 3 else right
        return top[0] + bottom[0] >= 100 or left[0] + right[0] >= 100
    if shape in ("circle", "ellipse"):
        radii = args.split(" at ")[0].split()
        return any(_length(radius)[0] == 0 for radius in radii)
    return len({re.sub(r"\s+", " ", point.strip()) for point in args.split(",") if point.strip()}) <= 1


def _colour(value: str) -> Optional[str]:
    """An opaque colour as #rrggbb (hex, rgb() or rgba() with full alpha, a basic colour name), else None."""
    value = re.sub(r"\s+", "", value or "")
    if value in _NAMED_COLOURS:
        return _NAMED_COLOURS[value]
    match = re.fullmatch(r"#([0-9a-f]{3,8})", value)
    if match:
        digits = match.group(1)
        if len(digits) in (3, 4):
            digits = "".join(c * 2 for c in digits)
        if len(digits) == 8 and digits.endswith("ff"):
            digits = digits[:6]
        return "#" + digits if len(digits) == 6 else None
    match = re.fullmatch(r"rgba?\((\d{1,3}),(\d{1,3}),(\d{1,3})(?:,(?:1|1\.0*|100%))?\)", value)
    if match and all(int(c) <= 255 for c in match.groups()):
        return "#" + "".join(f"{int(c):02x}" for c in match.groups())
    return None


def _style_kind(style: Dict[str, str]) -> Optional[str]:
    """Why an inline style hides its element (one of HIDDEN_KINDS), or None."""
    if style.get("display") == "none":
        return "display_none"
    if style.get("visibility") in ("hidden", "collapse"):
        return "visibility_hidden"
    # In the font shorthand a unitless number is a weight (700), except 0, which can only be the size.
    sizes = [token for token in style.get("font", "").split() if token == "0" or not token.isdigit()]
    if _tiny_font(style.get("font-size", "")) or any(_tiny_font(token) for token in sizes):
        return "font_size_zero"
    opacity, unit = _length(style.get("opacity", ""))
    if opacity is not None and (opacity / 100 if unit == "%" else opacity) <= _NEAR_ZERO_OPACITY:
        return "opacity_zero"
    if (style.get("position") in ("absolute", "fixed", "relative")
            and any(_off_screen(style.get(side, "")) for side in ("left", "top", "right", "bottom"))) \
            or _off_screen(style.get("text-indent", "")):
        return "off_screen"
    tiny_box = style.get("overflow") == "hidden" and any(
        size is not None and size <= 1 for size in (_px(style.get("width", "")), _px(style.get("height", ""))))
    if _clip_hides(style.get("clip", "")) or _clip_path_hides(style.get("clip-path", "")) or tiny_box:
        return "clipped"
    colour = _colour(style.get("color", ""))
    if colour is not None and colour == (_colour(style.get("background-color", ""))
                                         or _colour(style.get("background", ""))):
        return "same_colour"
    return None


def hidden_kind(name: str, attrs: Mapping[str, Any], tabs: bool = False) -> Optional[str]:
    """Why an element (tag name, attributes) is hidden from a person reading the rendered page, as one of
    HIDDEN_KINDS, or None when it is shown. Only what the element says itself counts: its inline style, the hidden
    attribute (not hidden="until-found", which the browser's search reveals), aria-hidden="true", and the elements
    <template> and <noscript>. tabs: the page has tabs (role="tab"), so a tab panel hidden until its tab is clicked
    stays."""
    if name in ("template", "noscript"):
        return name
    if "hidden" in attrs and str(attrs.get("hidden") or "").strip().lower() != "until-found":
        kind: Optional[str] = "hidden_attribute"
    elif str(attrs.get("aria-hidden") or "").strip().lower() == "true":
        kind = "aria_hidden"
    else:
        kind = _style_kind(_declarations(attrs.get("style")))
    if kind in _TAB_TOGGLES and tabs and str(attrs.get("role") or "").strip().lower() == "tabpanel":
        return None
    return kind


def _count(removed: Dict[str, int], kind: str) -> None:
    removed[kind] = removed.get(kind, 0) + 1


def strip_hidden(soup: Union[BeautifulSoup, Tag]) -> Dict[str, int]:
    """Removes every element of a parsed page that hidden_kind finds hidden, with its content; returns how many
    elements with text were removed, by kind (HIDDEN_KINDS; a kind with none is left out)."""
    tabs = soup.find(attrs={"role": "tab"}) is not None
    removed: Dict[str, int] = {}
    for tag in soup.find_all(True):
        if tag.decomposed:   # inside an element removed before
            continue
        kind = hidden_kind(tag.name, tag.attrs, tabs)
        if kind is None:
            continue
        if tag.get_text().strip():
            _count(removed, kind)
        tag.decompose()
    return removed


def strip_boilerplate(soup: Union[BeautifulSoup, Tag]) -> Dict[str, int]:
    """Drops the page furniture (BOILERPLATE_TAGS: navigation, header, footer, side bars, scripts, styles, SVG
    drawings), then every hidden element (strip_hidden); returns what strip_hidden removed. The crawler calls it
    before it reads a page's links and text, so neither comes from what a person does not see."""
    for tag in soup(list(BOILERPLATE_TAGS)):
        tag.decompose()
    return strip_hidden(soup)


def page_markdown(html: str) -> Tuple[str, str, Dict[str, int]]:
    """(title, markdown, removed) of a whole HTML page, read the way Deep Ingest reads one: strip_boilerplate, then
    html_to_markdown."""
    soup = BeautifulSoup(html or "", "html.parser")
    removed = strip_boilerplate(soup)
    title, text = html_to_markdown(soup)
    return title, text, removed


_HTML_DOCUMENT = re.compile(r"\s*(?:<!--.*?-->\s*)*<(?:!doctype\s+html|html|head|body)[\s>]", re.IGNORECASE | re.DOTALL)
_CODE_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_CODE_SPAN = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)")


def is_html_document(text: str) -> bool:
    """Whether a text is a whole HTML page (a doctype, <html>, <head> or <body> first), not Markdown with some HTML."""
    return bool(_HTML_DOCUMENT.match(text or ""))


def _mask_code(text: str) -> str:
    """The text with fenced code blocks and code spans blanked out (same length, line breaks kept): HTML written there
    is shown as it is, never rendered."""
    lines, fence = text.split("\n"), ""
    for i, line in enumerate(lines):
        if not fence:
            opening = _CODE_FENCE.match(line)
            if opening:
                fence = opening.group(1)
            else:
                lines[i] = _CODE_SPAN.sub(lambda m: " " * len(m.group(0)), line)
                continue
        elif re.match(rf"^ {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}\s*$", line):
            fence = ""
        lines[i] = " " * len(line)
    return "\n".join(lines)


class _HiddenSpans(HTMLParser):
    """Where the hidden elements of a text start and end (character offsets); the text itself is not changed."""

    def __init__(self, text: str):
        super().__init__(convert_charrefs=False)
        self.text = text
        self._lines = [0] + [m.end() for m in re.finditer("\n", text)]
        self._open: List[str] = []
        self._hidden: Optional[Tuple[int, int, str]] = None   # (depth, start, kind) of the hidden element open now
        self._tabs = re.search(r"""\brole\s*=\s*["']?tab["'\s/>]""", text, re.IGNORECASE) is not None
        self.spans: List[Tuple[int, int, str]] = []

    def _offset(self) -> int:
        line, column = self.getpos()
        return self._lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        start = self._offset()
        if self._hidden is None:
            kind = hidden_kind(tag, {key: value or "" for key, value in attrs}, self._tabs)
            if kind is not None and tag in _VOID:
                self.spans.append((start, start + len(self.get_starttag_text() or ""), kind))
            elif kind is not None:
                self._hidden = (len(self._open), start, kind)
        if tag not in _VOID:
            self._open.append(tag)

    def handle_endtag(self, tag):
        if tag not in self._open:   # a stray end tag closes nothing
            return
        while self._open.pop() != tag:
            pass
        if self._hidden is not None and len(self._open) <= self._hidden[0]:
            _, start, kind = self._hidden
            end = self.text.find(">", self._offset())
            self.spans.append((start, len(self.text) if end < 0 else end + 1, kind))
            self._hidden = None

    def scan(self) -> List[Tuple[int, int, str]]:
        self.feed(self.text)
        self.close()
        if self._hidden is not None:   # never closed: hidden up to the end, as a browser renders it
            self.spans.append((self._hidden[1], len(self.text), self._hidden[2]))
            self._hidden = None
        return sorted(self.spans)


def strip_hidden_markup(text: str) -> Tuple[str, Dict[str, int]]:
    """Text that may carry HTML (Markdown renders it, as in a knowledge pack's page): every element hidden_kind finds
    hidden is cut out with its content, and every other character stays as it was. HTML inside code spans and fenced
    code blocks is shown, never rendered, so it stays too. Returns the text and how many elements with text were
    removed, by kind."""
    text = text or ""
    if "<" not in text:
        return text, {}
    removed: Dict[str, int] = {}
    kept, position = [], 0
    for start, end, kind in _HiddenSpans(_mask_code(text)).scan():
        if start < position:
            continue
        if re.sub(r"<[^>]*>", "", text[start:end]).strip():
            _count(removed, kind)
        kept.append(text[position:start])
        position = end
    kept.append(text[position:])
    return "".join(kept), removed


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


def _items(node: Tag) -> List[Tuple[str, Tag]]:
    ordered = node.name == "ol"
    return [(f"{number}." if ordered else "-", li)
            for number, li in enumerate(node.find_all("li", recursive=False), start=1)]


def _item_lines(li: Tag, marker: str, depth: int) -> List[str]:
    nested = [child for child in li.children if isinstance(child, Tag) and child.name in ("ul", "ol")]
    own = [child for child in li.children if all(child is not sub for sub in nested)]
    text = _squash("".join(_inline(child) for child in own)).replace("\n", " ")
    lines = [f"{'  ' * depth}{marker} {text}"] if text else []
    for sub in nested:
        for sub_marker, sub_li in _items(sub):
            lines.extend(_item_lines(sub_li, sub_marker, depth + 1))
    return lines


def _list(node: Tag, blocks: List[str]) -> None:
    """Appends a list. Items holding code or a table (common in install guides) become separate blocks:
    inline rendering would squash the code's lines and the table's cells into one line."""
    lines: List[str] = []
    for marker, li in _items(node):
        if not li.find(_LIST_BREAKERS):
            lines.extend(_item_lines(li, marker, 0))
            continue
        if lines:
            blocks.append("\n".join(lines))
            lines = []
        inner: List[str] = []
        _render(li, inner)
        if inner and not _MARKDOWN_BLOCK_START.match(inner[0]):
            inner[0] = f"{marker} {inner[0]}"
        blocks.extend(inner)
    if lines:
        blocks.append("\n".join(lines))


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
            _list(child, blocks)
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
