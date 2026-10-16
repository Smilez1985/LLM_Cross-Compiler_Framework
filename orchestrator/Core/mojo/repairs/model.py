#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo Miracle's repair plans (round 6 design 7.4)

A repair is a dry-run plan before it is anything else: the steps it would take, each with what it changes (a label an
AI may read, the full path for the person, the state before and after) and whether it can be undone. The plan's
sha256 binds an answer to exactly these steps. Its class is computed from the steps, never declared by a repair:

- delegable: every step only narrows access or switches to an intact build and can be undone (chmod, acl_narrow,
  switch_ref, restore_file). Only these run without a terminal (round 6 Q11) and through run_mojo_miracle.
- person: a step widens access or cannot be undone.
- person_deletion: a step deletes or overwrites (deleting goes only through the person's list, WP7).
- person_here: a step touches the vault, the tool permissions, the audit log or oversight (hard rules: the person, in
  the framework).
- manual: a step needs the system (root, packages, daemons, other accounts): an administrator does it.
"""

import hashlib
import json
from typing import Any, Dict, Literal, Tuple

from pydantic import BaseModel, ConfigDict, Field

RepairClass = Literal["delegable", "person", "person_deletion", "person_here", "manual"]
StepKind = Literal["chmod", "acl_narrow", "switch_ref", "restore_file",   # narrowing or neutral
                   "delete", "overwrite",                                 # -> person_deletion
                   "widen",                                               # -> person
                   "vault", "policy", "audit", "oversight",               # -> person_here
                   "system"]                                              # -> manual
# A finding code as the checks write it (codes contain upper case: targets.Rockchip).
CODE_RE = r"^[A-Za-z0-9_.+-]{1,120}$"
_LABEL = r"^[^\x00-\x1f\x7f]{1,200}$"


class RepairStep(BaseModel):
    """One step of a plan. `target_label` is what an AI may read ("the state folder"); `full_path` is shown to the
    person only; `before` and `after` are states in framework words (a mode, a build id), never a path."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: StepKind
    target_label: str = Field(pattern=_LABEL)
    full_path: str = Field(default="", max_length=4096)
    before: str = Field(default="", max_length=200)
    after: str = Field(default="", max_length=200)
    undoable: bool


class RepairPlan(BaseModel):
    """What one Miracle repair of one finding would do. `summary` says it in plain words with labels only (the person
    and an AI read it)."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    repair_id: str = Field(pattern=r"^[a-z][a-z0-9_.]{1,63}$")
    finding_code: str = Field(pattern=CODE_RE)
    summary: str = Field(pattern=_LABEL)
    steps: Tuple[RepairStep, ...] = Field(min_length=1, max_length=50)

    def repair_class(self) -> RepairClass:
        return classify(self)

    def sha256(self) -> str:
        text = json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()

    def ai_view(self) -> Dict[str, Any]:
        """What an AI may read of the plan: labels and states, never a path."""
        return {"repair_id": self.repair_id, "finding_code": self.finding_code, "summary": self.summary,
                "repair_class": self.repair_class(),
                "steps": [{"kind": s.kind, "target": s.target_label, "before": s.before, "after": s.after,
                           "undoable": s.undoable} for s in self.steps]}


def classify(plan: RepairPlan) -> RepairClass:
    """The class of a plan, from its steps alone (the strongest step decides)."""
    kinds = {step.kind for step in plan.steps}
    if "system" in kinds:
        return "manual"
    if kinds & {"vault", "policy", "audit", "oversight"}:
        return "person_here"
    if kinds & {"delete", "overwrite"}:
        return "person_deletion"
    if "widen" in kinds or any(not step.undoable for step in plan.steps):
        return "person"
    return "delegable"


__all__ = ["CODE_RE", "RepairClass", "RepairPlan", "RepairStep", "StepKind", "classify"]
