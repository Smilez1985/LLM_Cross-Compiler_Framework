#!/usr/bin/env python3
"""
The text a person sees before confirming a tool call (MCP elicitation, Ditto's GUI dialog and CLI prompt).

In this order (design 6.6): the consequence in plain words, from facts the framework looked up; who asks (an MCP
client's self-reported name, version and transport, and whose permissions apply); the facts the framework checked,
external text labelled as such; then every argument, complete and with invisible characters escaped, priority
fields first, repaired values marked with what the AI wrote and ids followed by their speaking name. A confirmation
can only ever approve exactly the values the tool will run with. A question longer than its channel shows (4,000
characters in an MCP elicitation, 16,000 in the framework's own windows) cannot be confirmed there.
"""

import json
import unicodedata
from typing import Any, Dict, Optional

# Shown first in a confirmation, because they decide what a call touches.
PRIORITY_FIELDS = ("model", "dataset_path", "use_gpu", "target", "format", "board", "quantization",
                   "request_id", "approve")
MAX_ELICITATION = 4000   # an MCP form elicitation (what clients show without cutting)
MAX_FRAMEWORK = 16000    # the framework's own windows: Ditto's GUI dialog, the CLI prompt (with a pager)
MAX_DESCRIPTION = MAX_ELICITATION
# The first line for a tool that looks nothing up: what its class of tools does, in plain words.
TIER_CONSEQUENCES = {
    "read": "Reads information in the framework: {title}.",
    "local_write": "Changes something on this computer: {title}.",
    "external": "Reaches another device, installs third-party code or deletes data: {title}.",
}
MAX_MARKS = 4  # combining marks shown on one letter; a longer stack draws over the lines around it

# Always escaped: control, format, private use, surrogate, unassigned, line and paragraph separators.
_ESCAPED_CATEGORIES = ("Cc", "Cf", "Co", "Cs", "Cn", "Zl", "Zp")
# Default_Ignorable_Code_Point (Unicode DerivedCoreProperties): drawn as nothing, whatever their category.
_IGNORABLE = ((0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5),
              (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F), (0x3164, 0x3164),
              (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3),
              (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF))
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


class CallTooLong(ValueError):
    """The arguments cannot be shown completely, so they cannot be confirmed."""


def _hidden(ch: str) -> bool:
    """Invisible in a dialog, or looks like an ordinary space or nothing at all."""
    code = ord(ch)
    category = unicodedata.category(ch)
    return (category in _ESCAPED_CATEGORIES or (category == "Zs" and ch != " ") or code == 0x2800
            or any(low <= code <= high for low, high in _IGNORABLE))


def visible(value: Any) -> str:
    """A value as the user sees it, nothing cut off: hidden characters and long combining-mark stacks become
    \\u escapes (all hex digits of the code point, at least four). A raw hex digit never follows an escape, and
    a literal backslash before 'u', 'U' plus a hex digit or an escape is escaped too, so different values never
    read the same."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    pieces, escaped, marks = [], [], 0
    for ch in text:
        mark = unicodedata.category(ch) in ("Mn", "Me")
        after_escape = bool(escaped) and escaped[-1]
        # A mark stays raw only on a raw letter of this value (not on the label or an escape's last digit).
        if (_hidden(ch) or (mark and (not pieces or after_escape or marks >= MAX_MARKS))
                or (after_escape and ch in _HEX_DIGITS)):
            pieces.append(f"\\u{ord(ch):04x}")
            escaped.append(True)
        else:
            pieces.append(ch)
            escaped.append(False)
        marks = marks + 1 if mark and not escaped[-1] else 0
    # Backwards, so a backslash before an escaped backslash is escaped as well ("C:\Users" stays as it is).
    for i in range(len(text) - 2, -1, -1):
        if text[i] == "\\" and (escaped[i + 1] or text[i + 1] == "u"
                                or (text[i + 1] == "U" and text[i + 2:i + 3] in _HEX_DIGITS)):
            pieces[i], escaped[i] = "\\u005c", True
    return "".join(pieces)


def _plain(text: str) -> str:
    """Framework text with an already escaped client label in it (ASCII only) as it is; anything else escaped."""
    return text if text.isascii() and text.isprintable() else visible(text)


def consequence_line(spec: Any) -> str:
    """The question's first line for a tool without looked-up facts: the tool's own `consequence` text, else what
    its class of tools does."""
    own = getattr(spec, "consequence", "") or ""
    if own:
        return own
    tier = getattr(getattr(spec, "tier", None), "value", "local_write")
    return TIER_CONSEQUENCES.get(tier, TIER_CONSEQUENCES["local_write"]).format(title=spec.title)


def describe_call(spec: Any, arguments: Dict[str, Any], actor: str = "The AI client",
                  marks: Optional[Dict[str, str]] = None, renamed: str = "", *, requester: str = "",
                  facts: Any = None, names: Optional[Dict[str, str]] = None, limit: int = MAX_ELICITATION,
                  person_view: bool = False) -> str:
    """The question for one call. For MCP the text is part of the elicitation's identity, so an answer can only
    ever confirm exactly these values. `marks`: values the framework repaired, with what the AI wrote; `renamed`:
    the tool name as the AI wrote it, when it was a spelling variant; `requester`: who asks, for people; `facts`:
    the tool's looked-up CallFacts (consequence and facts); `names`: speaking names of id values (field -> name);
    `limit`: what the channel shows (CallTooLong above it). `person_view`: the text is shown only in the framework's
    own window, so it holds the facts only the person may see and a deletion's full paths; without it (a client's
    form, which an AI may read) those are left out and a deletion is listed by label and size only."""
    marks, names = marks or {}, names or {}
    shown = {k: v for k, v in arguments.items() if v is not None}
    order = [k for k in PRIORITY_FIELDS if k in shown] + [k for k in shown if k not in PRIORITY_FIELDS]
    lines = [visible(getattr(facts, "consequence", "") or consequence_line(spec))]
    if requester:
        lines.append(f"Requested by {_plain(requester)}.")
    checked = [f for f in (getattr(facts, "facts", ()) or ()) if person_view or not getattr(f, "person_only", False)]
    if checked:
        lines.append("Checked by the framework:")
        for fact in checked:
            label = f"{fact.label} ({fact.source}, not checked)" if fact.source else fact.label
            lines.append(f"  {visible(label)}: {visible(fact.value)}")
    plan = getattr(facts, "deletion", None)
    if plan is not None:
        lines.append("Would be deleted:")
        for item in plan.items:
            lines.append(f"  {visible(item.speaking_name)}")
            if person_view and item.full_path:
                lines.append(f"    {visible(item.full_path)}")
            if person_view:
                lines.append(f"    {visible(item.file_name)}, {item.size_bytes} bytes: {visible(item.what)}")
            else:
                lines.append(f"    {item.kind}, {item.size_bytes} bytes")
    arguments_lines = [f"  {visible(k)}: {visible(shown[k])}"
                       + (f" (that is: {visible(names[k])})" if names.get(k) else "")
                       + (f" (repaired; the AI wrote {visible(marks[k])})" if k in marks else "") for k in order]
    if renamed:
        arguments_lines.append(f"  (tool name repaired; the AI wrote {visible(renamed)})")
    lines.append(f"{actor} wants to run '{spec.title}' in the LLM Cross-Compiler Framework with:")
    text = "\n".join(lines + (arguments_lines or ["  (no arguments)"]))
    if len(text) > limit:
        raise CallTooLong(f"The question for '{spec.title}' is too long to show for a confirmation here "
                          f"({len(text)} characters, at most {limit}); the call was not run.")
    return text


def argument_lines(text: str) -> list:
    """The argument lines of a question (after the line that ends with 'with:'), for checks and tests."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.endswith(" with:") and " wants to run '" in line:
            return lines[i + 1:]
    return []
