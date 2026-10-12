#!/usr/bin/env python3
"""
The text a person sees before confirming a tool call (MCP elicitation, Ditto's GUI dialog and CLI prompt).

Every argument is shown, complete and with invisible characters escaped, so a confirmation can only ever
approve exactly the values the tool will run with. Arguments too long to show cannot be confirmed.
"""

import json
import unicodedata
from typing import Any, Dict, Optional

# Shown first in a confirmation, because they decide what a call touches.
PRIORITY_FIELDS = ("model", "dataset_path", "use_gpu", "target", "format", "board", "quantization",
                   "request_id", "approve")
MAX_DESCRIPTION = 4000              # an MCP form elicitation
MAX_FRAMEWORK_DESCRIPTION = 16000   # the framework's own windows (pending approvals)
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


def describe_call(spec: Any, arguments: Dict[str, Any], actor: str = "The AI client",
                  marks: Optional[Dict[str, str]] = None, renamed: str = "", limit: int = MAX_DESCRIPTION) -> str:
    """Every argument the tool will run with, complete. For MCP the question text is part of the elicitation's
    identity, so an answer can only ever confirm exactly these values. `marks`: values the framework repaired,
    with what the AI wrote; `renamed`: the tool name as the AI wrote it, when it was a spelling variant. `limit`:
    the most the channel shows (an elicitation 4,000 characters, the framework's own windows 16,000)."""
    marks = marks or {}
    shown = {k: v for k, v in arguments.items() if v is not None}
    order = [k for k in PRIORITY_FIELDS if k in shown] + [k for k in shown if k not in PRIORITY_FIELDS]
    lines = [f"  {visible(k)}: {visible(shown[k])}"
             + (f" (repaired; the AI wrote {visible(marks[k])})" if k in marks else "") for k in order]
    if renamed:
        lines.append(f"  (tool name repaired; the AI wrote {visible(renamed)})")
    text = (f"{actor} wants to run '{spec.title}' in the LLM Cross-Compiler Framework with:\n"
            + ("\n".join(lines) if lines else "  (no arguments)"))
    if len(text) > limit:
        raise CallTooLong(f"The arguments for '{spec.title}' are too long to show for a confirmation "
                          f"({len(text)} characters); the call was not run.")
    return text
