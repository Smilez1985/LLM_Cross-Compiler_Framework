#!/usr/bin/env python3
"""
This computer's audit entries for Ditto's calls to the framework on another computer (hybrid design 6.8, HF8).

The other computer's registry decides, asks and audits every call there, with the actor `mcp:<token>`. This computer
records its own side too, so its audit log shows everything Ditto did, wherever it ran:

- a write-ahead `started` entry before the call is sent, and an end entry linked to it (ok, error, denied,
  not_confirmed, cancelled);
- actor `ditto`, tool `<remote tool>`, tier and decision `remote` (this computer's policy did not decide it: the
  other computer's did);
- the extra fields `place` (`remote:<label>`), `remote_server` (the person's name for it and how it is reached,
  never the token and never a login), `reason_code`, `session` and `repairs` (how the router read `place`).

A call that changes something is not sent when its `started` entry cannot be written (fail closed): nothing runs
elsewhere that this computer has no record of. A read goes on and the failure is logged, as for local reads.
Arguments are recorded like a local call's (args_sha256 and a redacted preview); the other computer's text (its
results, its errors) never goes into an entry: only framework words and reason codes do.
"""

from typing import Any, Dict, List, Literal, Optional

from orchestrator.tools.registry import AuditUnavailable, ToolContext
from orchestrator.utils.helpers import redact_secrets
from orchestrator.utils.logging import get_logger

logger = get_logger("RemoteAudit")

REMOTE_TIER = "remote"
REMOTE_DECISION = "remote"
# How a remote call ended, from its reason code: the person's "no" there, its policy, a cancel, anything else.
END_OUTCOMES = {"declined": "not_confirmed", "permission_denied": "denied", "cancelled": "cancelled"}
MAX_LABEL = 120


def end_outcome(reason_code: str) -> str:
    """The end entry's outcome for a refused or failed remote call with this reason code."""
    return END_OUTCOMES.get(reason_code, "error")


def _label(text: str) -> str:
    one_line = " ".join(str(text or "").split())
    return one_line[:MAX_LABEL] or "the other computer"


def record_remote_call(audit: Any, ctx: ToolContext, tool: str, place_label: str, read_only: bool,
                       arguments: Optional[Dict[str, Any]], phase: Literal["started", "end"], outcome: str = "",
                       reason_code: str = "", detail: str = "", remote_server: str = "",
                       repairs: Optional[List[Dict[str, Any]]] = None, started: Optional[int] = None
                       ) -> Optional[int]:
    """Writes one entry of a remote call and returns its id.

    phase "started": the write-ahead entry. When it cannot be written, a call that is not `read_only` raises
    AuditUnavailable (the call must not be sent); a read gets None and goes on.
    phase "end": the end entry (`outcome` ok, error, denied, not_confirmed or cancelled), linked to `started`. A failed
    write is logged: the call has already happened there."""
    if audit is None:
        if phase == "started" and not read_only:
            raise AuditUnavailable(f"There is no audit log on this computer, so '{tool}' was not sent to "
                                   f"{_label(place_label)}: calls that change something run only when they are "
                                   "recorded. Run 'llm-cli doctor' to find the cause.", code="failed",
                                   next="Tell the person that this computer's audit log cannot be written.")
        return None
    extra: Dict[str, Any] = {"place": _label(f"remote:{place_label}"),
                             "remote_server": _label(remote_server or place_label),
                             "session": ctx.session, "client": ctx.client}
    if reason_code:
        extra["reason_code"] = reason_code
    if repairs:
        extra["repairs"] = list(repairs)
    taint = ctx.metadata.get("taint") if isinstance(ctx.metadata, dict) else None
    if taint:
        extra["taint"] = taint
    if phase == "started":
        try:
            return audit.record(ctx.actor, tool, REMOTE_TIER, REMOTE_DECISION, "started", arguments=arguments,
                                detail=f"sent to {_label(place_label)}", extra=extra)
        except Exception as e:  # noqa: BLE001 - a change is refused instead; a read goes on
            reason = redact_secrets(str(e)) or type(e).__name__
            if read_only:
                logger.warning(f"Audit write failed for a read on {_label(place_label)}: {reason}")
                return None
            logger.error(f"Audit write failed, '{tool}' not sent to {_label(place_label)}: {reason}")
            raise AuditUnavailable(
                f"This computer's audit log cannot be written ({reason}), so '{tool}' was not sent to "
                f"{_label(place_label)}: calls that change something run only when they are recorded. Run "
                "'llm-cli doctor' to find the cause.", code="failed",
                next="Tell the person that this computer's audit log cannot be written.") from None
    if started:
        extra["links"] = [started]
    try:
        return audit.record(ctx.actor, tool, REMOTE_TIER, REMOTE_DECISION, outcome or "ok", arguments=arguments,
                            approved=None, detail=detail[:1000], extra=extra)
    except Exception as e:  # noqa: BLE001 - the call has happened; the end entry is only logged as missing
        logger.warning(f"Audit end entry of '{tool}' on {_label(place_label)} not written: "
                       f"{redact_secrets(str(e)) or type(e).__name__}")
        return None


ROUTER_TIER = "hybrid"


def record_routing_refusal(audit: Any, ctx: ToolContext, tool: str, place: Any, arguments: Optional[Dict[str, Any]],
                           reason_code: str, detail: str) -> Optional[int]:
    """Records a call Ditto's router refused before it went anywhere (a place that contradicts where an id lives,
    objects on two computers, a missing place, a tool the chosen computer does not offer, a computer that cannot be
    reached). `place` is the hybrid_tools.Place the call was meant for. A failed write is only logged: nothing ran."""
    if audit is None:
        return None
    from orchestrator.tools.normalize import INPUT_CODES
    label = getattr(place, "label", "") or "this computer"
    where = "this_computer" if getattr(place, "kind", "local") == "local" else _label(f"remote:{label}")
    extra: Dict[str, Any] = {"place": where, "reason_code": reason_code, "session": ctx.session,
                             "client": ctx.client}
    if getattr(place, "kind", "local") != "local":
        extra["remote_server"] = _label(getattr(place, "server", "") or label)
    try:
        return audit.record(ctx.actor, tool, ROUTER_TIER, "none",
                            "invalid_input" if reason_code in INPUT_CODES else "error", arguments=arguments,
                            detail=f"not run: {detail}"[:1000], extra=extra)
    except Exception as e:  # noqa: BLE001 - nothing ran; the refusal itself reaches the model
        logger.warning(f"Audit entry of a refused routing for '{tool}' not written: "
                       f"{redact_secrets(str(e)) or type(e).__name__}")
        return None


__all__ = ["REMOTE_TIER", "REMOTE_DECISION", "ROUTER_TIER", "end_outcome", "record_remote_call",
           "record_routing_refusal"]
