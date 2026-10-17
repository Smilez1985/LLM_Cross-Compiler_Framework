#!/usr/bin/env python3
"""
HTML to Markdown-like text for the knowledge base.

Keeps the structure the chunker needs: headings, paragraphs, lists, fenced code (with its whitespace) and
tables. Scripts and styles are never content; which page furniture (nav, footer, ...) to drop is the
caller's decision.

Text a person reading the page does not see never reaches the knowledge base (hidden carriers of prompt injection,
recon P1). strip_boilerplate drops the page furniture and then, through strip_hidden, every element hidden by its
inline style (display:none, visibility:hidden, a zero or near-zero font size or opacity, off-screen positioning, a clip
that leaves no visible box, text in the colour of its own background or transparent), by the hidden attribute or
aria-hidden="true", <template> and <noscript>, and the elements a browser never shows the content of (iframe and media
fallbacks, a closed <dialog>, ...); and CDATA sections, which a browser shows as comments. An inactive tab panel stays
(role="tabpanel" on a page with tabs: the person opens it with a click). Markup is read the way a browser reads it
(parse_page): an inline style with its !important, comments and escapes, a self-closing hiding element that holds
what follows, the first of an attribute written twice. strip_hidden_markup applies the same rule (hidden_kind) to
HTML carried inside Markdown or plain text, such as a knowledge pack's page, and also cuts out comments, marked
sections and processing instructions there; a Markdown rendering of its result checks that nothing hidden is left.
Rules in style sheets and classes (.sr-only, ...) are not seen: there is no CSS engine, so the removal is partial.
What was removed is counted by kind (HIDDEN_KINDS) and never kept.
"""

import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from bs4.element import CData, Declaration, Doctype, ProcessingInstruction

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
# Elements a browser never shows the content of (its own style sheet hides them, or they show other content: an
# iframe its page, a canvas its drawing, a video or audio its player); counted as display_none. A <dialog> without
# the open attribute is hidden the same way. (<title> is the document's title, shown in the tab; one more <title> in
# the page is hidden: strip_hidden.)
_NEVER_SHOWN = frozenset({"script", "style", "datalist", "noembed", "noframes", "rp", "iframe", "canvas", "video",
                          "audio"})
# SVG and MathML honour a self-closing tag (<rect/>); everywhere else a browser ignores its slash.
_FOREIGN = ("svg", "math")
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
_CSS_COMMENT = re.compile(r"/\*.*?(?:\*/|$)", re.DOTALL)
_CSS_ESCAPE = re.compile(r"\\(?:([0-9a-fA-F]{1,6})(?:\r\n|[ \t\r\n\f])?|(.))", re.DOTALL)


def _css_unescape(text: str) -> str:
    """CSS escapes resolved (\\6e one is "none", disp\\lay is "display"), as a browser reads them."""
    def one(match: "re.Match[str]") -> str:
        if match.group(1):
            code = int(match.group(1), 16)
            return chr(code) if 0 < code <= 0x10FFFF and not 0xD800 <= code <= 0xDFFF else "\ufffd"
        return match.group(2)
    return _CSS_ESCAPE.sub(one, text) if "\\" in text else text


def _declarations(style: Any) -> Dict[str, str]:
    """An inline style as {property: value}, lower case, without !important, the way a browser reads it: comments
    and escapes resolved; a later declaration wins, except over an earlier !important one of the same property."""
    found: Dict[str, str] = {}
    important = set()
    for part in _CSS_COMMENT.sub(" ", str(style or "")).split(";"):
        name, colon, value = part.partition(":")
        name = _css_unescape(name.strip()).strip().lower()
        if not colon or not name:
            continue
        value = value.strip().lower()
        is_important = bool(_IMPORTANT.search(value))
        if name in important and not is_important:
            continue
        found[name] = _css_unescape(_IMPORTANT.sub("", value)).strip().lower()
        if is_important:
            important.add(name)
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


def _off_screen(value: str, percent: bool = True) -> bool:
    """A length that moves a box far off the screen to the left or the top. percent: a percentage counts (of the
    containing block: -100% and beyond); not for a translate(), whose percentage is of the element's own size."""
    number, unit = _length(value)
    if number is None:
        return False
    if unit in ("%", "vw", "vh", "vmin", "vmax"):
        return number <= -100 and (percent or unit != "%")
    px = _px(value)
    return px is not None and px <= _OFF_SCREEN_PX


def _transforms(style: Dict[str, str], name: str) -> List[Tuple[Optional[str], Optional[str]]]:
    """(x, y) of every transform function `name` in the transform property (translateX gives (x, None), translateY
    (None, y), scale(s) (s, s)), plus the individual property of that name (scale: 0, translate: -9999px)."""
    found: List[Tuple[Optional[str], Optional[str]]] = []
    calls = re.findall(rf"\b{name}(x|y|z|3d)?\(([^)]*)\)", style.get("transform", ""))
    if style.get(name) not in (None, "", "none"):
        calls.append(("", style[name]))
    for axis, args in calls:
        values = [value for value in re.split(r"[\s,]+", args.strip()) if value]
        if not values or axis == "z":
            continue
        if axis == "x":
            found.append((values[0], None))
        elif axis == "y":
            found.append((None, values[0]))
        else:   # one value: scale(s) is both axes, translate(x) only x
            found.append((values[0], values[1] if len(values) > 1 else (values[0] if name == "scale" else None)))
    return found


def _no_area(style: Dict[str, str]) -> bool:
    """A scale of (near) zero, or a box of 1px or less that cuts off what overflows it."""
    for axes in _transforms(style, "scale"):
        for arg in axes:
            number, unit = _length(arg or "")
            if number is not None and unit in ("", "%") and abs(number / 100 if unit == "%" else number) <= 0.01:
                return True
    overflow = " ".join(style.get(key, "") for key in ("overflow", "overflow-x", "overflow-y")).split()
    return any(value in ("hidden", "clip") for value in overflow) and any(
        size is not None and size <= 1
        for size in (_px(style.get(key, "")) for key in ("width", "height", "max-width", "max-height")))


def _moved_off_screen(style: Dict[str, str]) -> bool:
    """Positioned far off the screen, or shifted there by a large negative margin, text-indent or translation; not a
    box wider (or taller) than the shift, which still reaches into the screen (a carousel's track)."""
    width, height = _px(style.get("width", "")), _px(style.get("height", ""))

    def gone(value: str, extent: Optional[float], percent: bool = True) -> bool:
        px = _px(value)
        return _off_screen(value, percent) and (extent is None or px is None or extent < -px)

    shifts: List[Tuple[str, Optional[float], bool]] = []   # (length, the box's size along it, % counts)
    if style.get("position") in ("absolute", "fixed", "relative"):
        shifts += [(style.get(side, ""), width, True) for side in ("left", "right")]
        shifts += [(style.get(side, ""), height, True) for side in ("top", "bottom")]
    margin = style.get("margin", "").split()
    # margin: all | vertical horizontal | top horizontal bottom | top right bottom left; top and left move the box
    if margin:
        shifts += [(margin[0], height, True), (margin[1] if len(margin) in (2, 3) else margin[-1], width, True)]
    shifts += [(style.get("margin-top", ""), height, True), (style.get("margin-left", ""), width, True)]
    for x, y in _transforms(style, "translate"):
        shifts += [(x or "", width, False), (y or "", height, False)]
    return _off_screen(style.get("text-indent", "")) or any(gone(*shift) for shift in shifts)


def _alpha(value: str) -> Optional[float]:
    """The opacity of a colour (0 for transparent, 1 for an opaque colour), None when it is not a colour this reads."""
    value = re.sub(r"\s+", " ", (value or "").strip())
    if value == "transparent":
        return 0.0
    if _colour(value) is not None:
        return 1.0
    match = re.fullmatch(r"#([0-9a-f]{4}|[0-9a-f]{8})", value)
    if match:
        digits = match.group(1)
        return int(digits[3] * 2 if len(digits) == 4 else digits[6:], 16) / 255
    match = re.fullmatch(r"(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\((.*)\)", value)
    if not match:
        return None
    args = match.group(1)
    if "/" in args:
        alpha = args.rsplit("/", 1)[1].strip()
    else:
        parts = [part.strip() for part in args.split(",")]
        if len(parts) != 4:
            return 1.0
        alpha = parts[3]
    number, unit = _length(alpha)
    if number is None:
        return None
    return number / 100 if unit == "%" else number


def _fill(style: Dict[str, str]) -> str:
    """The colour the text is painted in: -webkit-text-fill-color wins over color."""
    fill = style.get("-webkit-text-fill-color", "")
    return style.get("color", "") if fill in ("", "currentcolor", "inherit", "initial", "unset", "revert") else fill


def _invisible_text(style: Dict[str, str]) -> bool:
    """Text painted in a (near) transparent colour, or in the colour of its own background; not when it is painted
    another way (a background clipped to the text, as gradient headings do, an outline or a shadow)."""
    fill = _fill(style)
    alpha = _alpha(fill)
    if alpha is not None and alpha <= _NEAR_ZERO_OPACITY:
        clips = re.split(r"[\s,]+", style.get("background-clip", "") + " " + style.get("-webkit-background-clip", ""))
        stroke = (style.get("-webkit-text-stroke") or style.get("-webkit-text-stroke-width") or "").split()
        stroked = bool(stroke) and _px(stroke[0]) != 0
        shadow = style.get("text-shadow", "") not in ("", "none")
        return "text" not in clips and not stroked and not shadow
    colour = _colour(fill)
    return colour is not None and colour == (_colour(style.get("background-color", ""))
                                             or _colour(style.get("background", "")))


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
    if style.get("visibility") in ("hidden", "collapse") or style.get("content-visibility") == "hidden":
        return "visibility_hidden"
    # In the font shorthand a unitless number is a weight (700), except 0, which can only be the size.
    sizes = [token for token in style.get("font", "").split() if token == "0" or not token.isdigit()]
    if _tiny_font(style.get("font-size", "")) or any(_tiny_font(token) for token in sizes):
        return "font_size_zero"
    opacities = [style.get("opacity", "")] + re.findall(r"\bopacity\(\s*([^)\s]*)\s*\)", style.get("filter", ""))
    for opacity, unit in (_length(value) for value in opacities):
        if opacity is not None and (opacity / 100 if unit == "%" else opacity) <= _NEAR_ZERO_OPACITY:
            return "opacity_zero"
    if _moved_off_screen(style):
        return "off_screen"
    if _clip_hides(style.get("clip", "")) or _clip_path_hides(style.get("clip-path", "")) or _no_area(style):
        return "clipped"
    if _invisible_text(style):
        return "same_colour"
    return None


def hidden_kind(name: str, attrs: Mapping[str, Any], tabs: bool = False) -> Optional[str]:
    """Why an element (tag name, attributes) is hidden from a person reading the rendered page, as one of
    HIDDEN_KINDS, or None when it is shown. Only what the element says itself counts: its inline style, the hidden
    attribute (not hidden="until-found", which the browser's search reveals), aria-hidden="true", the elements
    <template> and <noscript>, and the elements a browser never shows the content of (_NEVER_SHOWN, a <dialog> that
    is not open: display_none). tabs: the page has tabs (role="tab"), so a tab panel hidden until its tab is clicked
    stays."""
    if name in ("template", "noscript"):
        return name
    if name in _NEVER_SHOWN or (name == "dialog" and "open" not in attrs):
        return "display_none"
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


def strip_hidden(soup: Union[BeautifulSoup, Tag], document: bool = True) -> Dict[str, int]:
    """Removes every element of a parsed page that hidden_kind finds hidden, with its content; returns how many
    elements with text were removed, by kind (HIDDEN_KINDS; a kind with none is left out). A CDATA section outside
    MathML is a comment to a browser, so it goes too (counted as html_comments); comments themselves are never read.
    The first <title> is the document's title (document=True; html_to_markdown reads it as the title); a browser
    shows no other <title>, nor any in a rendered fragment (document=False), so they go as display_none. Parse the page
    with parse_page, which reads its markup the way a browser does."""
    tabs = soup.find(attrs={"role": "tab"}) is not None
    removed: Dict[str, int] = {}
    titles = soup.find_all("title")
    for tag in soup.find_all(True):
        if tag.decomposed:   # inside an element removed before
            continue
        kind = hidden_kind(tag.name, tag.attrs, tabs)
        if kind is None and tag.name == "title" and (not document or tag is not titles[0]):
            kind = "display_none"
        if kind is None:
            continue
        if tag.get_text().strip():
            _count(removed, kind)
        tag.decompose()
    for section in soup.find_all(string=lambda s: isinstance(s, CData)):
        if section.find_parent(["math", "svg"]) is None:
            if section.strip():
                _count(removed, "html_comments")
            section.extract()
    return removed


def strip_boilerplate(soup: Union[BeautifulSoup, Tag]) -> Dict[str, int]:
    """Drops the page furniture (BOILERPLATE_TAGS: navigation, header, footer, side bars, scripts, styles, SVG
    drawings), then every hidden element (strip_hidden); returns what strip_hidden removed. The crawler calls it
    before it reads a page's links and text, so neither comes from what a person does not see."""
    for tag in soup(list(BOILERPLATE_TAGS)):
        tag.decompose()
    return strip_hidden(soup)


def _first_attrs(attrs: List[Tuple[str, Optional[str]]]) -> Dict[str, str]:
    """A tag's attributes as a browser keeps them: of an attribute written twice, the first."""
    found: Dict[str, str] = {}
    for key, value in attrs:
        found.setdefault(key, value or "")
    return found


class _Offsets(HTMLParser):
    """An HTMLParser that knows the character offset in its text where the current markup starts."""

    def __init__(self, text: str):
        super().__init__(convert_charrefs=False)
        self.text = text
        self._lines = [0] + [m.end() for m in re.finditer("\n", text)]

    def _offset(self) -> int:
        line, column = self.getpos()
        return self._lines[line - 1] + column


class _SelfClosing(_Offsets):
    """Where a hiding element (hidden_kind) is written as a self-closing tag (<div hidden/>) outside SVG and MathML."""

    def __init__(self, text: str):
        super().__init__(text)
        self._foreign: List[str] = []
        self.slashes: List[Tuple[int, int]] = []   # (start, end) of each "/>" to read as ">"

    def handle_starttag(self, tag, attrs):
        if tag in _FOREIGN:
            self._foreign.append(tag)

    def handle_endtag(self, tag):
        if tag in self._foreign:
            while self._foreign.pop() != tag:
                pass

    def handle_startendtag(self, tag, attrs):
        if tag in _VOID or tag in _FOREIGN or self._foreign or hidden_kind(tag, _first_attrs(attrs)) is None:
            return   # (<svg/> and <math/> are closed by their slash, like everything inside them)
        raw = self.get_starttag_text() or ""
        slash = re.search(r"/\s*>$", raw)
        if slash:
            start = self._offset()
            self.slashes.append((start + slash.start(), start + len(raw)))


def browser_markup(html: str) -> str:
    """The markup with the slash dropped from each self-closing tag of a non-void element that hides its content
    (<div style="display:none"/>, <template/>): a browser ignores that slash outside SVG and MathML, so the element
    holds what follows, while Python's parser would close it at once and leave its text outside. Everything else stays
    as it was."""
    html = html or ""
    if not re.search(r"/\s*>", html):
        return html
    parser = _SelfClosing(html)
    parser.feed(html)
    parser.close()
    kept, position = [], 0
    for start, end in parser.slashes:
        kept += [html[position:start], ">"]
        position = end
    return "".join(kept + [html[position:]])


def parse_page(html: str) -> BeautifulSoup:
    """A page parsed the way a browser reads it, for strip_boilerplate and strip_hidden: self-closing hiding elements
    hold what follows (browser_markup), and of an attribute written twice the first counts (<p style="display:none"
    style="display:block"> is hidden)."""
    return BeautifulSoup(browser_markup(html), "html.parser", on_duplicate_attribute="ignore")


def page_markdown(html: str) -> Tuple[str, str, Dict[str, int]]:
    """(title, markdown, removed) of a whole HTML page, read the way Deep Ingest reads one: parse_page,
    strip_boilerplate, then html_to_markdown."""
    soup = parse_page(html)
    removed = strip_boilerplate(soup)
    title, text = html_to_markdown(soup)
    return title, text, removed


_HTML_START = re.compile(r"<(?:!doctype\s+html|html|head|body)[\s>]", re.IGNORECASE)
_CODE_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_CODE_SPAN = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)")


def is_html_document(text: str) -> bool:
    """Whether a text is a whole HTML page (a doctype, <html>, <head> or <body> first, after any comments), not Markdown
    with some HTML. One pass over the leading comments, so a page of many comments costs no more than its length."""
    text, position = text or "", 0
    while True:
        while position < len(text) and text[position].isspace():
            position += 1
        if not text.startswith("<!--", position):
            return bool(_HTML_START.match(text, position))
        end = text.find("-->", position + 4)
        if end < 0:
            return False
        position = end + 3


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


class _HiddenSpans(_Offsets):
    """Where the hidden elements of a text start and end (character offsets); the text itself is not changed. Markup
    is read as a browser reads it: of an attribute written twice the first counts, a self-closing tag of a non-void
    element outside SVG and MathML opens it, and a <title> in a page's body is never shown."""

    def __init__(self, text: str):
        super().__init__(text)
        self._open: List[str] = []
        self._hidden: Optional[Tuple[int, int, str]] = None   # (depth, start, kind) of the hidden element open now
        self._tabs = re.search(r"""\brole\s*=\s*["']?tab["'\s/>]""", text, re.IGNORECASE) is not None
        # (start, end, kind, the text a comment-like span holds; None for an element, whose tags are stripped)
        self.spans: List[Tuple[int, int, str, Optional[str]]] = []

    def handle_starttag(self, tag, attrs):
        start = self._offset()
        if self._hidden is None:
            kind = hidden_kind(tag, _first_attrs(attrs), self._tabs) or ("display_none" if tag == "title" else None)
            if kind is not None and tag in _VOID:
                self.spans.append((start, start + len(self.get_starttag_text() or ""), kind, None))
            elif kind is not None:
                self._hidden = (len(self._open), start, kind)
        if tag not in _VOID:
            self._open.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID and (tag in _FOREIGN or any(name in _FOREIGN for name in self._open[:-1])):
            self.handle_endtag(tag)   # SVG and MathML close it (<svg/> itself too); HTML keeps it open

    def _comment_like(self) -> None:
        """A comment, a CDATA or other marked section, a processing instruction or a bogus comment: a browser shows
        none of it. Read from the text itself, as Python versions report some of them differently."""
        if self._hidden is not None:
            return
        start = self._offset()
        if self.text.startswith("<!--", start):
            opener, close = "<!--", re.compile(r"--!?>")
        elif self.text[start:start + 9].upper() == "<![CDATA[":
            opener, close = "<![CDATA[", re.compile(re.escape("]]>"))
        elif self.text.startswith("<![", start):   # <![if mso]>: only its condition is hidden, not counted
            opener, close = None, re.compile(re.escape("]>"))
        else:   # <?...>, <!x ...>
            opener, close = self.text[start:start + 2], re.compile(">")
        found = close.search(self.text, start + 2)
        end = found.end() if found else len(self.text)
        held = self.text[start + len(opener):found.start() if found else end] if opener else ""
        self.spans.append((start, end, "html_comments", held))

    def handle_comment(self, data):   # <!-- ... --> and bogus comments such as <!x>, in newer Pythons <![if ...]>
        self._comment_like()

    def unknown_decl(self, data):   # <![CDATA[ ... ]]>, <![if ...]>
        self._comment_like()

    def handle_pi(self, data):   # <? ... >
        self._comment_like()

    def handle_endtag(self, tag):
        if tag not in self._open:   # a stray end tag closes nothing
            return
        while self._open.pop() != tag:
            pass
        if self._hidden is not None and len(self._open) <= self._hidden[0]:
            _, start, kind = self._hidden
            end = self.text.find(">", self._offset())
            self.spans.append((start, len(self.text) if end < 0 else end + 1, kind, None))
            self._hidden = None

    def scan(self) -> List[Tuple[int, int, str, Optional[str]]]:
        self.feed(self.text)
        self.close()
        if self._hidden is not None:   # never closed: hidden up to the end, as a browser renders it
            self.spans.append((self._hidden[1], len(self.text), self._hidden[2], None))
            self._hidden = None
        return sorted(self.spans, key=lambda span: span[:2])


def strip_hidden_markup(text: str) -> Tuple[str, Dict[str, int]]:
    """Text that may carry HTML (Markdown renders it, as in a knowledge pack's page): every element hidden_kind finds
    hidden is cut out with its content, and so is what a browser shows nothing of (comments, CDATA and other marked
    sections, processing instructions); every other character stays as it was. HTML inside code spans and fenced code
    blocks is shown, never rendered, so it stays too. Returns the text and how many pieces with text were removed, by
    kind (comment-like ones as html_comments).

    The cut text is then rendered as Markdown (CommonMark with tables, _markdown_html): where the cutter read code
    spans, code blocks or HTML blocks differently from a renderer and something hidden is still there, the page is
    read from its rendering instead (hidden elements and comments dropped, then html_to_markdown), so it never keeps
    hidden text, only its exact characters."""
    text = text or ""
    if "<" not in text:
        return text, {}
    removed: Dict[str, int] = {}
    kept, position = [], 0
    for start, end, kind, held in _HiddenSpans(_mask_code(text)).scan():
        if start < position:
            continue
        if (re.sub(r"<[^>]*>", "", text[start:end]) if held is None else held).strip():
            _count(removed, kind)
        kept.append(text[position:start])
        position = end
    kept.append(text[position:])
    cut = "".join(kept)
    rendered = _markdown_html(cut) if "<" in cut else None
    if rendered is not None and _hides_text(parse_page(rendered)):
        original = _markdown_html(text)
        if original is not None:
            return _rendered_text(parse_page(original))
    return cut, removed


_MARKDOWN: List[Any] = []   # the one markdown-it renderer, made on first use
_COMMENT_LIKE = (Comment, ProcessingInstruction, Declaration)   # what a browser never shows (not the doctype)


def _markdown_html(text: str) -> Optional[str]:
    """Markdown rendered to HTML the way a Markdown viewer shows it (CommonMark with tables, raw HTML passed through;
    markdown-it-py, which rich depends on), None when it cannot be rendered."""
    try:
        if not _MARKDOWN:
            from markdown_it import MarkdownIt
            _MARKDOWN.append(MarkdownIt("commonmark").enable("table"))
        return _MARKDOWN[0].render(text)
    except Exception:   # noqa: BLE001 - without a rendering the cutter's result stands
        return None


def _hides_text(soup: BeautifulSoup) -> bool:
    """Whether a rendered fragment still holds text a browser does not show: a hidden element (strip_hidden, which
    changes the soup), a comment, processing instruction or declaration with text."""
    return bool(strip_hidden(soup, document=False)) or any(
        str(node).strip() for node in soup.find_all(string=lambda s: isinstance(s, _COMMENT_LIKE)))


def _rendered_text(soup: BeautifulSoup) -> Tuple[str, Dict[str, int]]:
    """The visible text of a rendered fragment as Markdown (html_to_markdown) and what was left out, by kind."""
    removed = strip_hidden(soup, document=False)
    for node in soup.find_all(string=lambda s: isinstance(s, _COMMENT_LIKE)):
        if str(node).strip():
            _count(removed, "html_comments")
        node.extract()
    return html_to_markdown(soup)[1], removed


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
