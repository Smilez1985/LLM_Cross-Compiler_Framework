#!/usr/bin/env python3
"""
The host's reading of the tokenizer and chat-template check (DESIGN "Build pipeline and target modules", the row
"Large models").

The check itself runs in the target stage at the end of every GGUF build (the targets' scripts/model_check.py
tokenizer) and writes its result into build_info.json ("tokenizer_check"): status pass or warn, what was checked,
the warnings with the first difference, and what could not be checked with the reason. The texts come from the build
container and are treated as untrusted: only the known keys are read, each text is cut and stripped of control
characters, and an AI sees them as reference data (the tool registry marks build output that way).
"""

from typing import Any, Dict, List, Mapping, Tuple

MAX_ITEMS = 20
MAX_TEXT = 300


def _clean(text: Any) -> str:
    value = "".join(ch if ch.isprintable() else " " for ch in str(text or ""))
    return value[:MAX_TEXT]


def read(report: Any) -> Dict[str, Any]:
    """The check's result in its known keys (status, checks, warnings, skipped, vocabulary, tokens, chat_template),
    cleaned; {} without one."""
    if not isinstance(report, Mapping):
        return {}
    status = report.get("status") if report.get("status") in ("pass", "warn") else "unknown"
    out: Dict[str, Any] = {"status": status}
    for key in ("checks", "warnings", "skipped"):
        items = report.get(key)
        out[key] = [_clean(i) for i in items[:MAX_ITEMS]] if isinstance(items, list) else []
    for key in ("vocabulary", "tokens"):
        if isinstance(report.get(key), int) and not isinstance(report.get(key), bool):
            out[key] = report[key]
    if report.get("chat_template") in ("present", "missing"):
        out["chat_template"] = report["chat_template"]
    return out


def summary(report: Any) -> Tuple[List[str], List[str]]:
    """(the log lines, the warnings) of a check; ([], []) when the build has none (not GGUF, or switched off)."""
    data = read(report)
    if not data:
        return [], []
    head = ("Tokenizer and chat template: passed" if data["status"] == "pass"
            else f"Tokenizer and chat template: {len(data['warnings'])} warning(s)")
    lines = [head] + [f"  checked {c}" for c in data["checks"]] + [f"  not checked: {s}" for s in data["skipped"]]
    return lines, [f"Tokenizer and chat template: {w}" for w in data["warnings"]]


def ai_facts(report: Any) -> Dict[str, Any]:
    """Numbers and words for an AI caller (the build's result): the verdict, the warnings, what was not checked."""
    data = read(report)
    return {k: v for k, v in data.items() if k != "checks"} if data else {}


__all__ = ["ai_facts", "read", "summary"]
