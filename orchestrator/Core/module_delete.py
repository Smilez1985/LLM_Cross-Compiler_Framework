#!/usr/bin/env python3
"""
Deleting a module of your own (docs/DESIGN.md, Targets, hardware and templates: "A module of your own can be deleted").

`llm-cli module delete TARGET` and the button "Delete Module..." in Build > Targets... both come here. What a module is
made of on this computer: its folder in the person's own targets folder and, when it has any, the file of its private
values (Core/modules_env.py, `modules_env/<target>.env` of the state folder). Both go in one plan, so nothing of the
module stays behind; the plan names the values by their number, never by name or value.

Only a module of the own targets folder: a target the framework ships and a target installed from a pack are refused
(a pack leaves through `zoo uninstall`, which has its own list). Not while a running build uses it. The deletion is the
person's alone (Core/deletion.py: the accepted plan, "No" as the default, the plan built again before anything goes,
the audit log first, the trash or for good); no AI tool deletes a module.
"""

import os
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence, Tuple

from orchestrator.Core import modules_env
from orchestrator.Core.community_pack import PROVENANCE_NAME
from orchestrator.Core.deletion import DeletionResult, note_kept, plan_of, require_person, require_same
from orchestrator.Core.trash import tree_size
from orchestrator.tools.spec import DeletionItem, DeletionPlan
from orchestrator.utils.atomic import remove_tree

MODES = ("trash", "permanent")


class ModuleDeleteError(ValueError):
    """The module cannot be deleted; the message says why and what to do instead. Nothing was deleted."""


def _same(a: Path, b: Path) -> bool:
    try:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
    except (OSError, ValueError):
        return False


def _resolve(roots: Sequence[Path], name: str) -> Tuple[str, Optional[Path]]:
    """(the registered id, the folder) of a target given by id or folder name, read from the targets folders as the
    registry reads them (so the module is the one the Builder lists); (the name, None) when there is none."""
    from orchestrator.Core.target_manager import read_target_roots, resolve_target_name
    shown = str(name or "").strip()
    if not shown:
        return "", None
    folders = {tid: folder for tid, (folder, _data) in read_target_roots([Path(r) for r in roots]).items()}
    tid = resolve_target_name(shown, folders)
    return (tid, folders[tid]) if tid is not None else (shown, None)


def module_folder(roots: Sequence[Path], own: Path, name: str) -> Tuple[str, Path]:
    """(the id, the folder) of the person's own module `name` (its id or folder name) among the targets folders `roots`,
    `own` being the folder new modules go to; ModuleDeleteError: unknown, shipped with the framework, installed from a
    pack, a link, or not in the own targets folder."""
    from orchestrator.Core.community_manager import own_target_trust
    shown = str(name or "").strip()[:80]
    target_id, folder = _resolve(roots, shown)
    if folder is None:
        raise ModuleDeleteError(f"'{shown}' is no module here (the list of targets shows the ones there are).")
    if folder.is_symlink():
        raise ModuleDeleteError(f"The folder of {shown} is a link; delete the link by hand.")
    if (folder / PROVENANCE_NAME).exists():
        raise ModuleDeleteError(f"{shown} was installed from a community pack; it leaves by uninstalling the pack, "
                                "which has its own list.")
    if own_target_trust(folder) == "shipped":
        raise ModuleDeleteError(f"{shown} is a target the framework ships; only a module of your own can be deleted.")
    if not _same(folder.parent, Path(own)):
        raise ModuleDeleteError(f"{shown} is not in your own targets folder; only a module made on this computer can "
                                "be deleted.")
    return target_id, folder


def _busy(folder: Path, target_id: str, busy: Iterable[str]) -> bool:
    wanted = {folder.name.casefold(), str(target_id).casefold()}
    return any(str(b).casefold() in wanted for b in busy)


def module_plan(roots: Sequence[Path], own: Path, name: str, state_dir: Any, busy: Iterable[str] = (),
                mode_offered: Sequence[str] = MODES) -> DeletionPlan:
    """What deleting the module `name` removes, as the person sees it before anything goes: its folder and the file of
    its private values (the number of values, never a name or a value). `busy`: the targets a running build uses (the
    build engine's targets_in_use); the module of one of them is refused. Raises ModuleDeleteError."""
    target_id, folder = module_folder(roots, own, name)
    if _busy(folder, target_id, busy):
        raise ModuleDeleteError(f"A build is using {target_id} now; delete the module when it has ended, or cancel the "
                                "build first.")
    items: List[DeletionItem] = [DeletionItem(
        kind="module", speaking_name=f"module {target_id}", full_path=str(folder), file_name=folder.name,
        size_bytes=tree_size(folder), what="the module's folder: its target.yml, scripts and Dockerfile")]
    try:
        values_file = modules_env.path_for(state_dir, folder.name)
        values = modules_env.read(state_dir, folder.name) if values_file.is_file() else {}
    except (modules_env.LocalValuesError, OSError):
        values_file, values = None, {}
    if values_file is not None and values_file.is_file() and not values_file.is_symlink():
        items.append(DeletionItem(
            kind="module", speaking_name=f"private values of module {target_id}", full_path=str(values_file),
            file_name=values_file.name, size_bytes=tree_size(values_file),
            what=f"{len(values)} value(s) private to this computer (never shown here)"))
    plan = plan_of(items, tuple(mode_offered))
    assert plan is not None
    return plan


def delete_module(roots: Sequence[Path], own: Path, name: str, accepted: Any, state_dir: Any,
                  busy: Iterable[str] = (), trash: Any = None, audit: Any = None) -> DeletionResult:
    """Deletes exactly the plan the person accepted (module_plan): the plan is built again and must be the accepted one
    (PlanChanged otherwise), the audit log records the deletion first (RuntimeError, nothing deleted, without it), the
    module's folder and its values file go to the trash (Core/trash.py) or for good. The values file stays when the
    folder could not go, so the module is never left without its values. Only the person, in the GUI or at a terminal
    (PermissionError otherwise). The caller reads the target registry again. Returns a deletion.DeletionResult."""
    accepted = require_person(accepted)
    if accepted.actor not in ("gui", "cli"):
        raise PermissionError("Only the person deletes a module, in the GUI or at a terminal.")
    try:
        current: Optional[DeletionPlan] = module_plan(roots, own, name, state_dir, busy, accepted.plan.mode_offered)
    except ModuleDeleteError:
        current = None
    require_same(accepted, current, f"the list of what deleting the module {str(name)[:80]} removes")
    if accepted.mode == "trash" and trash is None:
        raise RuntimeError("The trash is not available; the module was not deleted.")
    if audit is None or not callable(getattr(audit, "record", None)):
        raise RuntimeError("The audit log is not available, so the module was not deleted.")
    target_id, folder = module_folder(roots, own, name)
    try:
        audit.record(accepted.actor, "modules", "framework", "person", "module_deleted", approved=True,
                     arguments={"target": target_id, "items": len(accepted.plan.items), "bytes": accepted.plan.total_bytes,
                                "mode": accepted.mode},
                     detail=f"module {target_id} " + ("moved to the trash" if accepted.mode == "trash"
                                                     else "deleted for good"),
                     extra=accepted.audit_extra())
    except Exception:  # noqa: BLE001 - the reason could name a host path
        raise RuntimeError("The audit log cannot record the deletion; the module was not deleted.") from None
    result = DeletionResult()
    folder_stays = False
    for item in accepted.plan.items:
        path = Path(item.full_path)
        is_folder = path == folder
        if not is_folder and folder_stays:
            result.failed.append((item.speaking_name, "kept: the module's folder stays, so its values stay with it"))
            continue
        root, label = (Path(own), "targets") if is_folder else (Path(state_dir), "state")
        try:
            if accepted.mode == "trash":
                entry = trash.move("module", path, root, label, item.speaking_name, accepted, accepted.actor)
                result.trash_ids.append(entry.id)
            else:
                if path.is_symlink():
                    raise OSError("a link")
                if note_kept(result, item.speaking_name, path, remove_tree(path)):
                    folder_stays = folder_stays or is_folder
                    continue
                result.freed_bytes += item.size_bytes
        except Exception as e:  # noqa: BLE001 - that item stays and is reported
            result.failed.append((item.speaking_name, type(e).__name__ if isinstance(e, OSError) else str(e)))
            folder_stays = folder_stays or is_folder
            continue
        result.done.append(item.speaking_name)
    return result


__all__ = ["MODES", "ModuleDeleteError", "delete_module", "module_folder", "module_plan"]
