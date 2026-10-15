#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Deletions the person accepted (delegation design 3.10, round 5 f; round 6 10.4)

Every deletion of something the person can see (a built model, a knowledge snapshot, an entry of the trash, a build
image of the framework) is described by a DeletionPlan (orchestrator/tools/spec.py): each item with its speaking
name, full path or Docker id, file name, size and what it is. Nothing is deleted before the person accepted exactly
that plan in the framework's own window: the GUI's deletion dialog (orchestrator/gui/deletion_dialog.py) or the
command line's table at a terminal (cli.confirm_deletion). Never an AI, never under a delegation or a standing
approval; "No" is always the default.

The accepted plan travels as an AcceptedDeletion: the plan, the person's channel ("gui" or "cli") and the mode the
person chose ("trash": moved to the trash, restorable, the space stays used; "permanent": deleted for good). Each
deleting function builds the plan again from what is on disk right before it deletes and refuses (PlanChanged) when
it is not exactly the accepted one, so a list that changed while the person read it deletes nothing.

Multiple choice (round 6 10.4): the person ticks a subset of a listed plan; `choose()` rebuilds the plan from exactly
the ticked items, so its sha256 covers them and nothing else. Nothing is ticked by default.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from orchestrator.tools.spec import DeletionItem, DeletionPlan

# Who accepts a deletion: the person, in the GUI or at a terminal, or in the framework's own window the tool registry
# asked in for an AI's request (Ditto's dialog or prompt, a pending approval decided in the GUI or CLI: "framework").
# Every other actor (an MCP client, Ditto, a standing approval, a delegation) is refused.
PERSON_ACTORS = ("gui", "cli", "framework")
MODES = ("trash", "permanent")


class PlanChanged(RuntimeError):
    """What would be deleted changed since the person saw the list: nothing was deleted."""

    def __init__(self, what: str = "the list"):
        super().__init__(f"{what[:1].upper()}{what[1:]} changed since it was shown; nothing was deleted. Look at the "
                         "new list and confirm it again.")


def person_actor(actor: Any) -> bool:
    return isinstance(actor, str) and actor in PERSON_ACTORS


@dataclass(frozen=True)
class AcceptedDeletion:
    """A plan the person accepted: exactly these items, in this mode, in this window. `channel`: where the person
    answered, as the audit log names it (default framework-gui or framework-cli)."""
    plan: DeletionPlan
    mode: str
    actor: str
    channel: str = ""

    def __post_init__(self):
        from orchestrator.Core.confirmed_builds import person_confirmed
        if not isinstance(self.plan, DeletionPlan):
            raise TypeError("an accepted deletion carries a DeletionPlan")
        if not person_actor(self.actor):
            raise PermissionError("Only the person accepts a deletion, in the GUI or at a terminal.")
        if self.mode not in MODES or self.mode not in self.plan.mode_offered:
            raise ValueError(f"the mode {self.mode!r} was not offered for this plan")
        if not self.channel:
            object.__setattr__(self, "channel", "framework" if self.actor == "framework" else f"framework-{self.actor}")
        if not person_confirmed(self.channel):
            raise PermissionError("Only the person accepts a deletion, in the framework's own window.")

    @property
    def sha256(self) -> str:
        return self.plan.sha256()

    def audit_extra(self) -> Dict[str, str]:
        """The fields every deletion's audit entries carry (design 3.11): the plan's hash, who answered and where."""
        return {"deletion_sha256": self.sha256, "answered_by": "person", "confirm_channel": self.channel}


@dataclass
class DeletionResult:
    """What a deletion did, for the person's report: the items moved to the trash or deleted for good (by speaking
    name), the bytes freed now (0 for the trash: the space stays used until the person empties it), the trash entries
    made, and the items kept with the reason."""
    done: List[str] = field(default_factory=list)
    freed_bytes: int = 0
    trash_ids: List[str] = field(default_factory=list)
    failed: List[Tuple[str, str]] = field(default_factory=list)


def accept(plan: DeletionPlan, actor: str, mode: Optional[str] = None) -> AcceptedDeletion:
    """The person's yes to `plan` (in `mode`, default the first one offered: the trash when there is one)."""
    return AcceptedDeletion(plan, mode or plan.mode_offered[0], actor)


def accepted_through_registry(plan: DeletionPlan, ctx: Any, mode: Optional[str] = None) -> AcceptedDeletion:
    """The person's yes to an AI's request, as the tool registry got it: only when the person answered the question
    in the framework's own window (Ditto's dialog or prompt, a pending approval decided in the GUI or CLI); the
    registry refuses any other channel for a call that deletes, and so does this."""
    channel = str(getattr(ctx, "confirm_channel", "") or "")
    actor = {"framework-gui": "gui", "framework-cli": "cli"}.get(channel, "framework")
    return AcceptedDeletion(plan, mode or plan.mode_offered[0], actor, channel or "none")


def require_person(accepted: Any) -> AcceptedDeletion:
    """`accepted` when it is the person's accepted plan; PermissionError for anything else (an AI's request, a plain
    list, a plan nobody accepted)."""
    if not isinstance(accepted, AcceptedDeletion) or not person_actor(accepted.actor):
        raise PermissionError("Nothing is deleted without the person's yes to the full list in the framework's own "
                              "window.")
    return accepted


def require_same(accepted: AcceptedDeletion, current: Optional[DeletionPlan], what: str = "the list") -> None:
    """PlanChanged unless `current` (the plan built again from what is on disk now) is exactly the accepted plan."""
    if current is None or current.sha256() != accepted.sha256:
        raise PlanChanged(what)


def item_key(item: DeletionItem) -> tuple:
    """What identifies an item between two look-ups (its values may still differ: then the plan changed)."""
    return item.kind, item.full_path, item.file_name


def plan_of(items: Sequence[DeletionItem], mode_offered: Iterable[str]) -> Optional[DeletionPlan]:
    """A plan of exactly `items` (None when there are none)."""
    items = list(items)
    if not items:
        return None
    return DeletionPlan(items=items, total_bytes=sum(i.size_bytes for i in items), mode_offered=tuple(mode_offered))


def choose(plan: DeletionPlan, ticked: Iterable[int]) -> Optional[DeletionPlan]:
    """Multiple choice: the plan of exactly the items the person ticked (0-based positions in `plan`, in the listed
    order); None when nothing is ticked. A position outside the list is refused."""
    positions = sorted(set(int(i) for i in ticked))
    if any(i < 0 or i >= len(plan.items) for i in positions):
        raise ValueError("a ticked item is not on the list")
    return plan_of([plan.items[i] for i in positions], plan.mode_offered)


def same_items(current: Optional[DeletionPlan], chosen: DeletionPlan) -> Optional[DeletionPlan]:
    """The items of `chosen` as they are in `current` now (a full plan looked up again), as a plan with the chosen
    plan's modes; None when one of them is gone. Its sha256 equals the chosen plan's only when no chosen item
    changed."""
    if current is None:
        return None
    now = {item_key(i): i for i in current.items}
    items = [now.get(item_key(i)) for i in chosen.items]
    if any(i is None for i in items):
        return None
    return plan_of(items, chosen.mode_offered)


def size_text(n: int) -> str:
    """Bytes in decimal units, as the deletion lists show them."""
    if n >= 10 ** 9:
        return f"{n / 1000 ** 3:.1f} GB"
    if n >= 10 ** 6:
        return f"{n / 1000 ** 2:.1f} MB"
    if n >= 10 ** 3:
        return f"{n / 1000:.0f} kB"
    return f"{n} bytes"


def rows(plan: DeletionPlan) -> List[Dict[str, Any]]:
    """The plan as the person's lists show it (CLI table, GUI dialog): one row per item."""
    return [{"name": i.speaking_name, "where": i.full_path or i.file_name, "file": i.file_name,
             "size": size_text(i.size_bytes), "what": i.what, "kind": i.kind} for i in plan.items]


__all__ = ["AcceptedDeletion", "DeletionResult", "MODES", "PERSON_ACTORS", "PlanChanged", "accept",
           "accepted_through_registry", "choose", "item_key", "person_actor", "plan_of", "require_person",
           "require_same", "rows", "same_items", "size_text"]
