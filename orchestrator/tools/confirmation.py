#!/usr/bin/env python3
"""
The text a person sees before confirming a tool call (MCP elicitation, Ditto's GUI dialog and CLI prompt).

Every argument is shown, complete and with invisible characters escaped, so a confirmation can only ever
approve exactly the values the tool will run with. Arguments too long to show cannot be confirmed.
"""

import json
import unicodedata
from typing import Any, Dict

# Shown first in a confirmation, because they decide what a call touches.
PRIORITY_FIELDS = ("model", "dataset_path", "use_gpu", "target", "format", "board", "quantization",
                   "request_id", "approve")
MAX_DESCRIPTION = 4000


class CallTooLong(ValueError):
    """The arguments cannot be shown completely, so they cannot be confirmed."""


def visible(value: Any) -> str:
    """A value as the user sees it: control, format and bidi characters escaped, nothing cut off."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    out = []
    for ch in text:
        if unicodedata.category(ch) in ("Cc", "Cf", "Co", "Cs", "Cn", "Zl", "Zp"):
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out)


def describe_call(spec: Any, arguments: Dict[str, Any], actor: str = "The AI client") -> str:
    """Every argument the tool will run with, complete. For MCP the question text is part of the elicitation's
    identity, so an answer can only ever confirm exactly these values."""
    shown = {k: v for k, v in arguments.items() if v is not None}
    order = [k for k in PRIORITY_FIELDS if k in shown] + [k for k in shown if k not in PRIORITY_FIELDS]
    lines = [f"  {visible(k)}: {visible(shown[k])}" for k in order]
    text = (f"{actor} wants to run '{spec.title}' in the LLM Cross-Compiler Framework with:\n"
            + ("\n".join(lines) if lines else "  (no arguments)"))
    if len(text) > MAX_DESCRIPTION:
        raise CallTooLong(f"The arguments for '{spec.title}' are too long to show for a confirmation "
                          f"({len(text)} characters); the call was not run.")
    return text
