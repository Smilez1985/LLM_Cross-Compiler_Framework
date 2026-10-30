#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - What Mojo Miracle can do about each finding (round 7 items 11', M' and T)

Every warning and error gets exactly one verdict, so no report skips a finding:

- miracle: Mojo Miracle repairs it (a harmless plan: it only narrows access or switches to an intact build).
- miracle_yes: Mojo Miracle does it only after the person's own yes to its plan (shown with what it costs), at a
  terminal or in the framework's Mojo window; an AI cannot start it (a repair of class person_yes).
- deletion_list: freeing it means deleting: the framework deletes only what the person ticks and confirms in its own
  window (WP7); Mojo Miracle at a terminal opens that list when one exists for the finding.
- person: only the person does it, in the framework or at a terminal (keys, tool permissions, the audit log, the
  person's settings and files).
- admin: it needs an administrator (start Docker, install system packages or a keyring).
- not_automatic: nothing in the framework can repair it automatically; the fix hint says what helps.

OK and INFO findings get no verdict: they are no problem.
"""

from dataclasses import dataclass
from typing import List, Optional

from orchestrator.Core.mojo.findings import Finding, MojoReport, Severity
from orchestrator.Core.mojo.repairs import REPAIRS
from orchestrator.Core.mojo.repairs.model import RepairPlan

VERDICTS = ("miracle", "miracle_yes", "deletion_list", "person", "admin", "not_automatic")
# Why Mojo Miracle does not repair something, in the words the person and an AI read.
REASONS = {
    "miracle_yes": "needs your yes: Mojo Miracle does it only after you agreed to its plan, at a terminal or in the "
                   "framework's Mojo window",
    "deletion_list": "deleting needs your list: the framework deletes only what you tick and confirm in its own window",
    "person": "needs you: only you can do this, in the framework or at a terminal (your keys, tool permissions, the "
              "audit log, your settings and files)",
    "admin": "needs an administrator: it changes the system outside the framework",
    "not_automatic": "not repairable automatically",
}
# The person's lists Mojo Miracle opens at a terminal for a deletion finding (the framework's build images, WP7), and
# where the person finds that list otherwise.
DELETION_LISTS = {"disk.cache": "framework_images", "disk.output": "framework_images",
                  "disk.builds": "framework_images"}
DELETION_WHERE = ("you choose what is deleted: 'llmcf mojo --miracle' in a terminal, or Tools > Framework Build "
                  "Images... in the GUI")
# Deletion findings Mojo Miracle opens no list for, and the person's own list that frees them (named to the person and
# to an AI): the knowledge snapshots (eighth round, answer 2) and the old knowledge base storage after its conversion
# (DECISIONS RG5), which Mojo never deletes.
OTHER_LISTS = {"knowledge.storage": "Tools > Knowledge Snapshots... > Delete the old storage... in the GUI, or "
                                    "'llm-cli knowledge storage delete-old' in a terminal",
               "knowledge.snapshots": "Tools > Knowledge Snapshots... in the GUI, or 'llm-cli knowledge snapshots "
                                      "prune' or 'llm-cli knowledge snapshots export NAME --to FOLDER' in a terminal"}
_CLASS_VERDICT = {"delegable": "miracle", "person_yes": "miracle_yes", "person_deletion": "deletion_list",
                  "manual": "admin"}


@dataclass
class Offer:
    finding: Finding
    verdict: str
    plan: Optional[RepairPlan] = None
    reason: str = ""   # why Miracle does not repair it ("" for a repair it makes)

    @property
    def summary(self) -> str:
        """What Miracle would do, or why it does not."""
        return (self.plan.summary if self.plan is not None and self.verdict in ("miracle", "miracle_yes")
                else self.reason)


def offer_for(framework, finding: Finding) -> Offer:
    """The verdict for one warning or error, with the dry-run plan of a repair."""
    repair = REPAIRS.get(finding.repair_id) if finding.repair_id else None
    if repair is not None:
        try:
            plan = repair.plan(framework, finding)
        except Exception as e:  # noqa: BLE001 - a plan that cannot be made is reported, never a crash
            return Offer(finding, "not_automatic", None, f"{REASONS['not_automatic']}: its repair could not be "
                                                         f"planned ({e})")
        if plan is None:
            return Offer(finding, "not_automatic", None, f"{REASONS['not_automatic']}: {repair.no_plan}")
        verdict = _CLASS_VERDICT.get(plan.repair_class(), "person")
        return Offer(finding, verdict, plan, "" if verdict == "miracle" else REASONS[verdict])
    verdict = finding.cannot if finding.cannot in REASONS else "not_automatic"
    return Offer(finding, verdict, None, REASONS[verdict])


def offers(framework, report: MojoReport) -> List[Offer]:
    """One offer per warning and error of the report, in its order."""
    return [offer_for(framework, f) for f in report.findings if f.severity in (Severity.WARN, Severity.ERROR)]


__all__ = ["DELETION_LISTS", "DELETION_WHERE", "OTHER_LISTS", "Offer", "REASONS", "VERDICTS", "offer_for", "offers"]
