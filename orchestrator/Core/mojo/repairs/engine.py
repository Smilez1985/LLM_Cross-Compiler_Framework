#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo Miracle's engine: one harmless repair, applied and checked again

`apply_repair` (round 6 design 7.5): (1) one Miracle at a time (a lock in the state folder; a second one is refused);
(2) the owning check runs again: a finding that is gone is `already_fixed`; the plan is made again, and when it is not
the plan the caller was shown (sha256) nothing is changed (`plan_changed`); (3) only a harmless plan (class
delegable) is applied here, whoever asks; (4) the audit log records the start first, or nothing is changed; (5) the
steps run in order, each a compare-and-swap, stopping at the first that fails; (6) the end is recorded; (7) the check
runs again: `repaired` when the finding is gone, else honestly `still_present`.
"""

import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence

from orchestrator.Core.mojo.findings import Finding, Severity
from orchestrator.Core.mojo.repairs import repair_for
from orchestrator.Core.mojo.repairs.state import StepChanged, state_dir

OUTCOMES = ("repaired", "still_present", "already_fixed", "plan_changed", "failed", "refused")


@dataclass
class RepairResult:
    """What one repair did. `summary` is the plan in plain words (labels only); `reason` says why it was not repaired
    (it can name a path: what an AI reads of it goes through views)."""
    outcome: str
    finding_code: str
    summary: str = ""
    reason: str = ""
    steps_done: int = 0
    plan_sha256: str = ""

    @property
    def repaired(self) -> bool:
        return self.outcome == "repaired"


def _problems(framework, check, code: str) -> List[Finding]:
    return [f for f in check(framework) if f.code == code and f.severity in (Severity.WARN, Severity.ERROR)]


def apply_repair(framework, code: str, *, actor: str, decision: str = "allow", answered_by: str = "",
                 confirm_channel: str = "", expect_sha256: str = "", links: Sequence[int] = ()) -> RepairResult:
    """Repairs the finding `code` when its repair is harmless. `actor` is who asked (cli, gui, mcp, ditto, ...),
    `decision` how the call was decided (person: answered at a terminal or in a window; ask: an AI's call the person
    confirmed; allow: harmless, without a question, the CLI without a terminal). `expect_sha256`: the plan the caller
    was shown; another plan changes nothing. Never raises for a refused or failed repair: the result says why."""
    from orchestrator.utils.file_lock import LockTimeout, file_lock
    repair = repair_for(code)
    if repair is None:
        return RepairResult("refused", code, reason="Mojo Miracle has no repair for this problem")
    held = contextlib.ExitStack()
    try:
        held.enter_context(file_lock(lock_path(framework), timeout=0))
    except LockTimeout:
        return RepairResult("refused", code, reason="another Mojo Miracle is running; nothing was changed (try again "
                                                    "when it has finished)")
    except OSError as e:
        return RepairResult("failed", code, reason=f"Mojo Miracle could not start: {e.strerror or e}")
    with held:
        try:
            return _apply(framework, repair, code, actor=actor, decision=decision, answered_by=answered_by,
                          confirm_channel=confirm_channel, expect_sha256=expect_sha256, links=links)
        except Exception as e:  # noqa: BLE001 - a check that crashed while planning: reported, never a crash
            return RepairResult("failed", code, reason=f"the check could not run again: {e}")


def _apply(framework, repair, code: str, *, actor: str, decision: str, answered_by: str, confirm_channel: str,
           expect_sha256: str, links: Sequence[int]) -> RepairResult:
    found = _problems(framework, repair.check, code)
    if not found:
        return RepairResult("already_fixed", code, reason="Mojo Oracle no longer finds this problem, so nothing was "
                                                          "changed")
    plan = repair.plan(framework, found[0])
    if plan is None:
        return RepairResult("refused", code, reason=repair.no_plan)
    sha = plan.sha256()
    if expect_sha256 and sha != expect_sha256:
        return RepairResult("plan_changed", code, plan.summary, plan_sha256=sha,
                            reason="nothing was changed: the problem changed since the plan was shown (look at the new "
                                   "plan)")
    if plan.repair_class() != "delegable":
        # The person's repairs (person, person_here) come with M2; a deletion is never a repair (WP7's list).
        return RepairResult("refused", code, plan.summary, plan_sha256=sha,
                            reason=f"this repair is not harmless (class {plan.repair_class()}): it needs you in the "
                                   "framework")
    audit = getattr(getattr(framework, "tool_registry", None), "audit", None)
    extra = {"finding_code": code, "repair_id": plan.repair_id, "plan_sha256": sha,
             "repair_class": plan.repair_class(), "answered_by": answered_by or None,
             "confirm_channel": confirm_channel or None, "links": list(links) or None}
    arguments = {"code": code}
    if audit is None or not callable(getattr(audit, "record", None)):
        return RepairResult("failed", code, plan.summary, plan_sha256=sha,
                            reason="the audit log is not available, so nothing was changed")
    try:
        audit.record(actor, "mojo", "local_write", decision, "started", approved=True, arguments=arguments,
                     detail=f"Mojo Miracle: {plan.summary}", extra=extra)
    except Exception:  # noqa: BLE001 - its words could name the log's path; the person sees the cause in Mojo Oracle
        return RepairResult("failed", code, plan.summary, plan_sha256=sha,
                            reason="the audit log cannot record the repair, so nothing was changed")
    done, failure = 0, ""
    for step in plan.steps:
        try:
            repair.apply_step(framework, step)
        except StepChanged as e:
            failure = f"stopped before changing {step.target_label}: {e}"
            break
        except OSError as e:
            failure = f"{step.target_label} could not be changed: {e.strerror or type(e).__name__}"
            break
        except Exception as e:  # noqa: BLE001 - e.g. the runtime store refused the build: reported, never a crash
            failure = f"{step.target_label} could not be changed: {e}"
            break
        done += 1
    try:
        audit.record(actor, "mojo", "local_write", decision, "error" if failure else "ok", approved=True,
                     arguments=arguments, detail=(f"Mojo Miracle failed: {failure}" if failure else
                                                  f"Mojo Miracle applied {done} step(s)")[:1000], extra=extra)
    except Exception:  # noqa: BLE001 - what happened is reported below; its start is recorded
        pass
    if failure:
        return RepairResult("failed", code, plan.summary, failure + (f" ({done} of {len(plan.steps)} steps were done)"
                                                                      if done else "; nothing was changed"),
                            done, sha)
    try:
        still = _problems(framework, repair.check, code)
    except Exception as e:  # noqa: BLE001
        return RepairResult("failed", code, plan.summary, f"the change was made, but the check could not run again "
                                                          f"to confirm it: {e}", done, sha)
    if still:
        return RepairResult("still_present", code, plan.summary,
                            f"it was changed, but Mojo Oracle still finds the problem: {still[0].message}", done, sha)
    return RepairResult("repaired", code, plan.summary, steps_done=done, plan_sha256=sha)


def lock_path(framework) -> Path:
    """One Mojo Miracle at a time, across processes (the CLI, the GUI, an MCP server)."""
    return state_dir(framework) / "mojo" / "mojo.lock"


__all__ = ["OUTCOMES", "RepairResult", "apply_repair", "lock_path"]
