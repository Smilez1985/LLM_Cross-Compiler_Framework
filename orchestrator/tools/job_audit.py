#!/usr/bin/env python3
"""
The end of a job in the audit log (MCP design 6.7: `links` from follow-up entries to the starting entry).

A tool that starts a job returns at once, so the registry's own end entry only says that the job started. The work that
goes on afterwards (a deploy, a package, a board probe, a runtime build, a benchmark) writes its own end entry through
`audited_job`, linked to the call's `started` entry (ToolRegistry.record_job_end): ok, error with the failure's reason
code, or cancelled. The entry is written on every way out of the work, also when a step after the main action fails
(the main action happened and must be on record) or the work raises something unexpected. It names the work by speaking
names (device label, package name, build id); an error by its type and reason code only, never its text (which can
hold host paths or a device's words).
"""

from typing import Any, Callable, Dict, Optional, Tuple

from orchestrator.utils.logging import get_logger

logger = get_logger("JobAudit")

Done = Callable[[Dict[str, Any]], Tuple[str, str, str]]


def _reason_code(error: BaseException) -> str:
    from orchestrator.tools.normalize import CODES
    code = str(getattr(error, "code", "") or "")
    return code if code in CODES else "failed"


def audited_job(registry: Any, name: str, arguments: Any, ctx: Any,
                work: Callable[[Any], Optional[Dict[str, Any]]], what: str, *,
                done: Optional[Done] = None, cancelled: Optional[Callable[[], str]] = None
                ) -> Callable[[Any], Optional[Dict[str, Any]]]:
    """`work` for JobService.start, wrapped so that its end is always written to the audit log, linked to the call's
    started entry. `what` names the work ("device Living room board, package deploy_x.zip"); `done(result)` gives
    (outcome, detail, reason_code) for a run that finished (default: ok, "done"); `cancelled()` the detail of a
    cancelled run (default: "cancelled")."""

    def end(handle: Any, outcome: str, detail: str, reason_code: str = "") -> None:
        job_id = getattr(getattr(handle, "job", None), "job_id", "") or "?"
        try:
            registry.record_job_end(name, arguments, ctx, outcome, f"job {job_id}, {what}: {detail}",
                                    reason_code=reason_code)
        except Exception as e:  # noqa: BLE001 - the work happened; a failed write is only logged
            logger.warning(f"The end of the {name} job could not be written to the audit log: {type(e).__name__}")

    def cancel_text() -> str:
        try:
            return (cancelled() if cancelled is not None else "") or "cancelled"
        except Exception:  # noqa: BLE001
            return "cancelled"

    def run(handle: Any) -> Optional[Dict[str, Any]]:
        from orchestrator.Core.job_service import JobCancelled
        try:
            result = work(handle)
        except BaseException as e:
            if isinstance(e, JobCancelled) or getattr(handle, "cancelled", False):
                end(handle, "cancelled", cancel_text())
            else:
                end(handle, "error", f"failed ({type(e).__name__})", _reason_code(e))
            raise
        if getattr(handle, "cancelled", False):
            end(handle, "cancelled", cancel_text() + "; its result is not used")
            return result
        outcome, detail, code = "ok", "done", ""
        if done is not None:
            try:
                outcome, detail, code = done(result or {})
            except Exception as e:  # noqa: BLE001 - the summary failed, the work did not
                outcome, detail, code = "ok", f"done (summary not available: {type(e).__name__})", ""
        end(handle, outcome, detail, code)
        return result

    return run


__all__ = ["audited_job"]
