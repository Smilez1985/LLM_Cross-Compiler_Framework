#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo check: the errors in the framework's log (step 4 of "One log the person and Ditto
read")

Mojo Oracle reads the errors of the last day in framework.log (utils/framework_log, the reader of `logs show`, the
Framework Log window and read_framework_log). An error the self-healing pattern library knows
(self_healing_manager.known_issue: a full disk, the container engine's permission, memory, the network, a base
image's package sources) becomes one finding per cause, a warning with how often it came, the library's step and who
does it (the person, the person's deletion list, an administrator); Mojo Miracle repairs none of them (it only
narrows access or switches to an intact build). Every other error is counted in one piece of information with the
newest one. The check only reads: no marker of what was seen is written, so the window is the last day.
"""

from datetime import datetime, timedelta
from typing import Dict, List

from orchestrator.Core.mojo.findings import Finding, Severity, _ok

WINDOW_HOURS = 24
WHY = ("The same error comes back until its cause is fixed, and a build or a request of an AI app may have failed "
       "with it.")


def _code(cause: str) -> str:
    """A finding code of a known cause: logs.known.<the cause in words, lower case>."""
    from orchestrator.Core.self_healing_manager import cause_code
    return f"logs.known.{cause_code(cause)}"


def _who(strategy_value: str, cause: str) -> str:
    """Who does the library's step: a full disk is the person's deletion list, the engine's permission an
    administrator's, everything else the person's (the framework's own automatic healing acts only in a build)."""
    if strategy_value == "docker_prune":
        return "deletion_list"
    if "docker daemon" in cause.lower():
        return "admin"
    return "person"


def check_logs(framework) -> List[Finding]:
    from orchestrator.Core.self_healing_manager import known_issue
    from orchestrator.utils.crash_report import framework_log_folder
    from orchestrator.utils.framework_log import read_entries
    since = datetime.now() - timedelta(hours=WINDOW_HOURS)
    errors = read_entries(framework_log_folder(getattr(framework, "config", None)), level="error", since=since)
    if not errors:
        return [_ok("logs", "logs.errors", f"No error in the framework's log of the last {WINDOW_HOURS} hours")]
    known: Dict[str, dict] = {}
    other = []
    for entry in errors:
        text = f"{entry.get('message') or ''}\n{entry.get('stack_trace') or ''}"
        issue = known_issue(text)
        if issue is None:
            other.append(entry)
            continue
        _pattern, cause, fix, strategy, _scope = issue
        seen = known.setdefault(cause, {"count": 0, "fix": fix, "strategy": getattr(strategy, "value", str(strategy))})
        seen["count"] += 1
    findings: List[Finding] = []
    for cause, seen in known.items():
        times = "once" if seen["count"] == 1 else f"{seen['count']} times"
        findings.append(Finding("logs", _code(cause), Severity.WARN,
                                f"The framework's log shows {times} in the last {WINDOW_HOURS} hours: {cause}",
                                seen["fix"], where="the framework's log", why=WHY,
                                cannot=_who(seen["strategy"], cause)))
    if other:
        newest = other[-1]
        message = " ".join(str(newest.get("message") or "").split())[:300]
        findings.append(Finding("logs", "logs.errors", Severity.INFO,
                                f"{len(other)} other error(s) in the framework's log of the last {WINDOW_HOURS} "
                                f"hours; the newest, from {newest.get('logger_name', '?')}: {message}",
                                "The framework's log names each with its time and the part that wrote it; an AI app "
                                "reads it with read_framework_log once you enable that tool.",
                                where="the framework's log"))
    return findings
