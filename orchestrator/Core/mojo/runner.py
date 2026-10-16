#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo's runner: Mojo Oracle looks, Mojo Miracle repairs

`run()` (Mojo Oracle) runs the checks in a fixed order and changes nothing; a check that crashes is itself a finding
(`<area>.check_crashed`), never a crash of Mojo. `miracle()` (Mojo Miracle) takes every warning and error and reports
what became of each one (round 7 items 11', M' and T): repaired (checked again: gone), still there, already gone, or
not repaired and why. It applies only harmless repairs (offers.py); a finding that needs a deletion is answered only by
the person's list (WP7), which it opens at a terminal when one exists, never by itself.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Dict, List, Optional, Tuple

from orchestrator.Core.mojo.checks import (check_calibration, check_chat_feed, check_community, check_disk,
                                           check_docker, check_knowledge, check_python, check_runtime, check_secrets,
                                           check_targets, check_tool_policy, check_user_state)
from orchestrator.Core.mojo.findings import Check, Finding, MojoReport, Severity

if TYPE_CHECKING:
    from orchestrator.Core.mojo.offers import Offer

DEFAULT_CHECKS: List[Check] = [check_python, check_docker, check_disk, check_targets, check_user_state,
                               check_secrets, check_tool_policy, check_chat_feed, check_calibration, check_community,
                               check_knowledge, check_runtime]
# What Mojo Miracle can do with a finding (outcomes of MiracleEntry); "not_done" carries the verdict's reason.
MIRACLE_OUTCOMES = ("repaired", "still_present", "already_fixed", "plan_changed", "failed", "not_done", "declined")


@dataclass
class MiracleEntry:
    """What Mojo Miracle did about one problem Mojo Oracle found. `summary`: what was done or planned (labels);
    `reason`: why it was not repaired (plain words; the person's output shows it as it is, an AI reads it labelled)."""
    finding: Finding
    verdict: str
    outcome: str
    summary: str = ""
    reason: str = ""


@dataclass
class MiracleReport:
    entries: List[MiracleEntry]
    after: MojoReport   # Mojo Oracle after Mojo Miracle

    @property
    def exit_code(self) -> int:
        return self.after.exit_code

    @property
    def repaired(self) -> List[MiracleEntry]:
        return [e for e in self.entries if e.outcome == "repaired"]

    @property
    def already_gone(self) -> List[MiracleEntry]:
        return [e for e in self.entries if e.outcome == "already_fixed"]

    @property
    def not_repaired(self) -> List[MiracleEntry]:
        return [e for e in self.entries if e.outcome not in ("repaired", "already_fixed")]

    @property
    def new_problems(self) -> List[Finding]:
        """Warnings and errors the check afterwards finds that were not there before (reported, never hidden)."""
        before = {(e.finding.code, e.finding.where) for e in self.entries}
        return [f for f in self.after.findings
                if f.severity in (Severity.WARN, Severity.ERROR) and (f.code, f.where) not in before]


class Mojo:
    def __init__(self, framework, checks: Optional[List[Check]] = None):
        self.framework = framework
        self.checks = checks if checks is not None else list(DEFAULT_CHECKS)

    def run(self) -> MojoReport:
        """Mojo Oracle: every check, nothing changed."""
        findings = []
        for check in self.checks:
            try:
                findings.extend(check(self.framework))
            except Exception as e:  # a broken check is itself a finding, never a crash of Mojo
                name = getattr(check, "__name__", "check").replace("check_", "")
                findings.append(Finding(name, f"{name}.check_crashed", Severity.ERROR, f"The check failed: {e}",
                                        where=f"the {name} check",
                                        why="This area was not checked, so a problem there would go unseen.",
                                        cannot="not_automatic"))
        return MojoReport(findings)

    oracle = run

    def miracle(self, *, actor: str, at_terminal: bool, before: Optional[MojoReport] = None,
                free_space: Optional[Callable[[], Tuple[int, str]]] = None, decision: str = "allow",
                answered_by: str = "", confirm_channel: str = "",
                offered: Optional[List["Offer"]] = None) -> MiracleReport:
        """Mojo Miracle on the problems of `before` (a fresh Oracle run when None). `offered`: the offers the person
        was shown (offers.offers of `before`); each repair applies exactly the plan shown, or nothing when the problem
        changed meanwhile (plan_changed). `actor`, `decision`, `answered_by` and `confirm_channel` go into the audit
        entries of each repair (decision "allow": harmless, without a question, e.g. the CLI without a terminal).
        `free_space`: the person's list of what to delete (at a terminal only; returns how many items were deleted
        and, when none was, why: "" when the person chose none), opened once for every finding that shares it."""
        from orchestrator.Core.mojo.offers import DELETION_LISTS, DELETION_WHERE, offers
        from orchestrator.Core.mojo.repairs.engine import apply_repair
        before = before if before is not None else self.run()
        entries: List[MiracleEntry] = []
        deleted: Dict[str, Tuple[int, str]] = {}
        for offer in (offered if offered is not None else offers(self.framework, before)):
            finding = offer.finding
            if offer.verdict == "miracle":
                result = apply_repair(self.framework, finding.code, actor=actor, decision=decision,
                                      answered_by=answered_by, confirm_channel=confirm_channel,
                                      expect_sha256=offer.plan.sha256())
                outcome = "not_done" if result.outcome == "refused" else result.outcome
                entries.append(MiracleEntry(finding, offer.verdict, outcome, result.summary or offer.summary,
                                            result.reason))
            elif offer.verdict == "deletion_list" and finding.code in DELETION_LISTS and at_terminal \
                    and free_space is not None:
                listing = DELETION_LISTS[finding.code]
                if listing not in deleted:
                    count, why = free_space()
                    deleted[listing] = (int(count or 0), str(why or ""))
                entries.append(MiracleEntry(finding, offer.verdict, "listed", listing))
            else:
                reason = offer.reason
                if offer.verdict == "deletion_list" and finding.code in DELETION_LISTS:
                    reason += f"; {DELETION_WHERE}"
                entries.append(MiracleEntry(finding, offer.verdict, "not_done", "", reason))
        after = self.run()
        problems = {f.code for f in after.findings if f.severity in (Severity.WARN, Severity.ERROR)}
        for entry in entries:
            if entry.outcome != "listed":
                continue
            count, why = deleted.get(entry.summary, (0, ""))
            if not count:
                entry.outcome, entry.summary = ("not_done" if why else "declined"), ""
                entry.reason = (f"nothing was deleted: {why}" if why else
                                "you chose nothing from the list, so nothing was deleted")
            elif entry.finding.code in problems:
                entry.outcome, entry.summary = "still_present", f"you deleted {count} item(s) from the list"
                entry.reason = f"you deleted {count} item(s) from the list, but Mojo Oracle still finds the problem"
            else:
                entry.outcome, entry.summary = "repaired", f"you deleted {count} item(s) from the list"
        return MiracleReport(entries, after)


__all__ = ["DEFAULT_CHECKS", "MIRACLE_OUTCOMES", "MiracleEntry", "MiracleReport", "Mojo"]
