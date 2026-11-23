#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo Miracle's repairs

Each repair names the finding codes it repairs, the check that finds them (run again before and after), how it plans
(a dry run, model.RepairPlan) and how it applies one step. Two repairs are harmless (class delegable: they only narrow
access, switch to an intact build or put the framework's own files back, and can be undone): `state.tighten`,
`runtime.switch_intact` and `runtime_layer.restore`; two need the
person's own yes (class person_yes): `knowledge.reimport`, which imports installed packs again, and
`dependencies.add_steps`, which writes the missing steps of a module's dependency list into its Dockerfile. Nothing here deletes: a finding that needs a deletion is answered by the person's list
(WP7), never by a repair. Extension point (round 6 design 7.4, package M2): undo records, the person's repairs
(person, person_here), calibration and community repairs join this table.
"""

import re
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

from orchestrator.Core.mojo.checks import (check_community, check_dependencies, check_runtime, check_runtime_layer,
                                           check_user_state)
from orchestrator.Core.mojo.repairs import dependencies, knowledge, runtime, runtime_layer, state
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
    pattern: str = ""   # a regular expression for codes that carry a name (dependencies.<target>.steps)


REPAIRS: Dict[str, Repair] = {
    state.REPAIR_ID: Repair(state.REPAIR_ID, ("state.permissions",), check_user_state, state.plan, state.apply_step,
                            "the state folder is private already, is a link, or this system has no POSIX modes"),
    runtime.REPAIR_ID: Repair(runtime.REPAIR_ID, ("runtime.build.missing",), check_runtime, runtime.plan,
                              runtime.apply_step,
                              "no intact, verified build of the pinned llama.cpp version is stored to switch to; "
                              "'llm-cli ditto runtime install' builds one (that takes a while)"),
    runtime_layer.REPAIR_ID: Repair(runtime_layer.REPAIR_ID, (runtime_layer.CODE,), check_runtime_layer,
                                    runtime_layer.plan, runtime_layer.apply_step,
                                    "the runtime layer is not changed any more, its folder holds files that are not "
                                    "the layer's (only you remove them; the next build checks it again), or the "
                                    "framework's own copy is not whole (install the framework again)"),
    dependencies.REPAIR_ID: Repair(dependencies.REPAIR_ID, (), check_dependencies, dependencies.plan,
                                   dependencies.apply_step,
                                   "the Dockerfile lacks nothing any more, is not in your own targets folder, or has "
                                   "no place for the steps (the comment line above the module's own pip packages)",
                                   dependencies.CODE_PATTERN),
    knowledge.REPAIR_ID: Repair(knowledge.REPAIR_ID, (knowledge.CODE,), check_community, knowledge.plan,
                                knowledge.apply_step,
                                "no installed pack needs a new import, or the knowledge base is not open"),
}


def repair_for(code: str) -> Optional[Repair]:
    return next((r for r in REPAIRS.values()
                 if code in r.codes or (r.pattern and re.fullmatch(r.pattern, code))), None)


__all__ = ["CODE_RE", "REPAIRS", "Repair", "RepairClass", "RepairPlan", "RepairStep", "StepChanged", "classify",
           "repair_for"]
