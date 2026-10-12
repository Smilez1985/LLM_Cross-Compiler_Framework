#!/usr/bin/env python3
"""
The shared vocabulary of delegations (design "Delegation, chat panel and hybrid", section 3.6; work package WP1).

A delegation ("Vollmacht") is the person's release that lets an AI answer some of its own routine questions itself.
Only the person creates or revokes one, in the GUI or at a terminal, never over MCP. This module holds what the other
parts already rely on before the delegation engine exists: the id format the audit log, the feed and the approvals
share, how a tool's scope is declared, and the tools no delegation ever covers. The delegation record, its groups,
durations and the scope of every tool follow with the delegation work package (WP11); nothing here lets an AI answer
anything yet.
"""

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, FrozenSet, Literal, Optional

# d_<16 hex>: the id of a delegation, as the audit log (`confirm_channel: delegation:<id>`, field `delegation`), the
# feed's references and the approval records name it.
DELEGATION_ID_RE = r"^d_[0-9a-f]{16}$"
_DELEGATION_ID = re.compile(DELEGATION_ID_RE)

# Who may answer a question about a tool: "delegable" (an AI under a live delegation, only without a security or legal
# warning), "person" (always the person), "no_question" (the tool never asks, e.g. reading or stopping one's own work).
WHO = ("delegable", "person", "no_question")


def delegation_id_ok(value: Any) -> bool:
    """Whether `value` is a delegation id (d_<16 hex>)."""
    return isinstance(value, str) and bool(_DELEGATION_ID.fullmatch(value))


@dataclass(frozen=True)
class ToolScope:
    """How a delegation may cover one tool. `when(input, facts)` returns a reason when this call is not covered (for
    example "not when its facts carry a deletion"), None when it is; `reason` says in plain words why a tool stays with
    the person (the "Always stays with you" list)."""
    who: Literal["delegable", "person", "no_question"]
    group: str = ""
    shared_ok: bool = False      # may the shared stdio caller "mcp" get it
    when: Optional[Callable[[Any, Any], Optional[str]]] = None
    reason: str = ""

    def __post_init__(self):
        if self.who not in WHO:
            raise ValueError(f"unknown scope {self.who!r} (one of {', '.join(WHO)})")
        if self.who == "delegable" and not self.group:
            raise ValueError("a delegable tool belongs to a delegation group")


# Never covered by a delegation, whatever a delegation lists: answering a question (an AI never answers its own
# delegation away), signing a pack with the person's key, deleting (round 5 f: the person confirms every deletion in
# the framework's own window) and installing a target transferred from another computer (never reviewed).
NEVER_DELEGATE: FrozenSet[str] = frozenset({"answer_approval", "export_pack", "delete_artifact",
                                            "install_transferred_pack"})

# The scope of each tool (WP11 fills it for every tool that is not a read tool, and the registry refuses a tool without
# one from then on). Until then an entry is only read to check that a tool which deletes is the person's alone.
DELEGATION_SCOPE: Dict[str, ToolScope] = {
    "delete_artifact": ToolScope("person", reason="deleting: you confirm every deletion in the framework's own "
                                                  "window, with every item listed"),
}


__all__ = ["DELEGATION_ID_RE", "DELEGATION_SCOPE", "NEVER_DELEGATE", "ToolScope", "WHO", "delegation_id_ok"]
