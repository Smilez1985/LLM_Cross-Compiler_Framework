#!/usr/bin/env python3
"""
The audit log of tool calls as people read it: one code path for `llm-cli tools audit` and the GUI's
Security > Audit Log... window.

* `audit_rows()`    one row per entry: time, who (the caller in words, the client as it named itself), the tool by its
                    title (and the name the AI wrote, when that was a spelling variant), the decision, whether it was
                    confirmed, the outcome, the reason code, how the person was asked and what they answered; with
                    `details` also the repairs, duration, result hash, links and session.
* `audit_summary()` counts per caller: calls, refusals, and the reports that a task cannot be done by how the
                    framework checked them (design 6.11 D14: the honest "cannot" in use).

The column names are the CLI's; the GUI shows its own translated headers over the same rows.
"""

from typing import Any, Dict, Iterable, Iterator, List

ENTRY_COLUMNS = ("Time", "Who", "Tool", "Decision", "Confirmed", "Outcome", "Reason", "Asked / answer")
DETAIL_COLUMNS = ("Repairs", "ms", "Result", "Links", "Session")
CANNOT_CHECKS = ("confirmed", "contradicted", "premature", "unchecked")
REFUSED_OUTCOMES = ("denied", "not_confirmed", "rate_limited", "repeat_refused", "invalid_input", "unknown_tool")
SUMMARY_COLUMNS = ("Who", "Calls", "Refused", "Cannot reported", *CANNOT_CHECKS)
SUMMARY_ENTRIES = 100000   # how many of the newest entries the summary counts (the CLI's --summary)


def audit_rows(registry, entries: Iterable[Dict[str, Any]], details: bool = False) -> Iterator[List[str]]:
    """The rows of `entries` (newest first, as AuditLog.entries gives them), each a list of texts in the order of
    ENTRY_COLUMNS (+ DETAIL_COLUMNS with `details`)."""
    from orchestrator.tools.registry import ToolError, caller_label

    def title(name: str) -> str:
        try:
            return registry.get(name).title
        except ToolError:
            return name

    for entry in entries:
        who = caller_label(str(entry.get("actor") or ""))
        if entry.get("client"):
            who += f"\n{entry['client']} (self-reported)"
        tool = str(entry.get("tool") or "")
        shown = title(tool) + (f" ({tool})" if title(tool) != tool else "")
        if entry.get("name_requested") and entry["name_requested"] != tool:
            shown += f"\nthe AI wrote {entry['name_requested']}"
        asked = entry.get("confirm_channel") or ""
        if entry.get("answer"):
            asked = f"{asked}: {entry['answer']}" if asked else str(entry["answer"])
        row = [str(entry.get("ts", "")), who, shown, str(entry.get("decision", "")),
               {None: "-", 0: "no", 1: "yes"}.get(entry.get("approved"), "?"), str(entry.get("outcome", "")),
               str(entry.get("reason_code") or ""), asked or "-"]
        if details:
            repairs = entry.get("repairs") if isinstance(entry.get("repairs"), list) else []
            result = entry.get("result") if isinstance(entry.get("result"), dict) else {}
            row += [", ".join(f"{r.get('kind')}:{r.get('field') or '-'}" for r in repairs if isinstance(r, dict)),
                    str(entry.get("duration_ms") or ""),
                    (f"{str(result.get('sha256', ''))[:12]} {result.get('bytes', '')} B" if result else ""),
                    ", ".join(str(link) for link in entry.get("links") or []), str(entry.get("session") or "")]
        yield row


def audit_summary(entries: Iterable[Dict[str, Any]]) -> list:
    """Rows per caller in the order of SUMMARY_COLUMNS: calls (end entries), refusals, report_cannot_do reports and
    how they were checked."""
    per: dict = {}
    for entry in entries:
        actor = str(entry.get("actor") or "?")
        row = per.setdefault(actor, {"calls": 0, "refused": 0, "cannot": 0, **{c: 0 for c in CANNOT_CHECKS}})
        outcome = str(entry.get("outcome") or "")
        if outcome == "cannot_reported":
            row["cannot"] += 1
            check = str(entry.get("detail") or "").split(":")[-1].split("\n")[0].strip()
            if check in CANNOT_CHECKS:
                row[check] += 1
        elif outcome in REFUSED_OUTCOMES:
            row["refused"] += 1
        elif outcome in ("ok", "error", "cancelled"):
            row["calls"] += 1
    return [[actor, r["calls"], r["refused"], r["cannot"], *(r[c] for c in CANNOT_CHECKS)]
            for actor, r in sorted(per.items())]


__all__ = ["CANNOT_CHECKS", "DETAIL_COLUMNS", "ENTRY_COLUMNS", "REFUSED_OUTCOMES", "SUMMARY_COLUMNS", "SUMMARY_ENTRIES",
           "audit_rows", "audit_summary"]
