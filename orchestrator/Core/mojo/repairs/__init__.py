#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo Miracle's repairs

Each repair names the finding codes it repairs, the check that finds them (run again before and after), how it plans
(a dry run, model.RepairPlan) and how it applies one step. Two repairs are harmless (class delegable: they only narrow
access or switch to an intact build, and can be undone): `state.tighten` and `runtime.switch_intact`; one needs the
person's own yes (class person_yes): `knowledge.reimport`, which imports installed packs again. Nothing here deletes: a finding that needs a deletion is answered by the person's list
(WP7), never by a repair. Extension point (round 6 design 7.4, package M2): undo records, the person's repairs
(person, person_here), calibration and community repairs join this table.
"""

from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

from orchestrator.Core.mojo.checks import check_community, check_runtime, check_user_state
from orchestrator.Core.mojo.repairs import knowledge, runtime, state
from orchestrator.Core.mojo.repairs.model import CODE_RE, RepairClass, RepairPlan, RepairStep, classify
from orchestrator.Core.mojo.repairs.state import StepChanged


@dataclass(frozen=True)
class Repair:
    repair_id: str
    codes: Tuple[str, ...]
    check: Callable
    plan: Callable[..., Optional[RepairPlan]]
    apply_step: Callable[..., None]
    no_plan: str   # why there is no plan when plan() finds nothing to do, in plain words


REPAIRS: Dict[str, Repair] = {
    state.REPAIR_ID: Repair(state.REPAIR_ID, ("state.permissions",), check_user_state, state.plan, state.apply_step,
                            "the state folder is private already, is a link, or this system has no POSIX modes"),
    runtime.REPAIR_ID: Repair(runtime.REPAIR_ID, ("runtime.build.missing",), check_runtime, runtime.plan,
                              runtime.apply_step,
                              "no intact, verified build of the pinned llama.cpp version is stored to switch to; "
                              "'llm-cli ditto runtime install' builds one (that takes a while)"),
    knowledge.REPAIR_ID: Repair(knowledge.REPAIR_ID, (knowledge.CODE,), check_community, knowledge.plan,
                                knowledge.apply_step,
                                "no installed pack needs a new import, or the knowledge base is not open"),
}


def repair_for(code: str) -> Optional[Repair]:
    return next((r for r in REPAIRS.values() if code in r.codes), None)


__all__ = ["CODE_RE", "REPAIRS", "Repair", "RepairClass", "RepairPlan", "RepairStep", "StepChanged", "classify",
           "repair_for"]
