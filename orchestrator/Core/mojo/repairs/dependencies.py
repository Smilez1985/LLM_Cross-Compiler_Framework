#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Miracle repair `dependencies.add_steps`: the missing steps of a module's dependency list

A module's `template_variables.dependencies` names packages (a system package, a pip package) that its build image
installs by pinned, hash-checked steps of its Dockerfile. A module made before its list, or a Dockerfile edited by hand,
may not have them (Mojo Oracle: `dependencies.<target>.steps`). This repair writes the missing steps into the Dockerfile
of a module of the person's own targets folder: the pinned apt packages as one step, the pip packages as the wheel
step the module generator writes, after the comment line above the place of the module's own pip packages
(`with_steps` below). A Dockerfile without that place, or one outside the person's own folder,
gets no plan.

The plan is a step of kind `add_steps` (class person_yes: it rewrites a file the person keeps, and can be undone): it
runs only after the person's own yes to the plan shown, at a terminal or in the Mojo window, never for an AI. The old
file is kept first in the state folder (`mojo/backups/dependencies/`, named with its sha256), the write is a
compare-and-swap on the file's sha256 and replaces the file whole. Nothing is installed on this computer, no licence is
accepted, nothing is deleted.
"""

import hashlib
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

from orchestrator.Core.mojo.findings import Finding
from orchestrator.Core.mojo.repairs.model import RepairPlan, RepairStep
from orchestrator.Core.mojo.repairs.state import StepChanged, state_dir

REPAIR_ID = "dependencies.add_steps"
CODE_PATTERN = r"dependencies\.[A-Za-z0-9_.+-]+\.steps"
_CODE = re.compile(r"^dependencies\.(?P<target>.+)\.steps$")


def _deps():
    from orchestrator.Core import target_dependencies
    return target_dependencies


def steps_block(gap: Any) -> str:
    """The Dockerfile lines for what `gap` lacks: the pinned apt packages as one step and the pip packages as the
    hash-checked wheel step the module generator writes (`--no-deps --require-hashes`)."""
    from orchestrator.Core.module_generator import vendor_blocks
    lines: List[str] = []
    if gap.apt:
        lines += ["RUN apt-get update \\", "    && apt-get install -y --no-install-recommends \\"]
        lines += [f"        {p} \\" for p in gap.apt]
        lines += ["    && rm -rf /var/lib/apt/lists/*"]
    if gap.pip:
        block = vendor_blocks({"pip_packages": list(gap.pip), "pip_hashes": {n.lower(): h for n, h in gap.hashes if h}})
        lines += block["# [PIP_PACKAGES]"].split("\n")
    return "\n".join(lines)


def with_steps(text: str, gap: Any, today: str) -> Optional[str]:
    """The Dockerfile text with the steps of `gap` added right after the comment line above the place of the module's
    own pip packages (and the marker line that may still follow it); None when the file has no such place."""
    lines = text.split("\n")
    for number, line in enumerate(lines):
        if line.startswith(_deps().STEPS_ANCHOR):
            at = number + 1
            if at < len(lines) and re.fullmatch(r"#\s*\[PIP_PACKAGES\]", lines[at].strip()):
                at += 1
            note = f"# Dependencies of the module's list that this Dockerfile lacked (added by Mojo Miracle, {today}):"
            return "\n".join(lines[:at] + [note, steps_block(gap)] + lines[at:])
    return None


def backup_folder(framework) -> Path:
    return state_dir(framework) / "mojo" / "backups" / "dependencies"


def _folder_of(framework, finding: Finding) -> Optional[Path]:
    found = _CODE.match(finding.code)
    tm = getattr(framework, "target_manager", None)
    return tm.folder_of(found.group("target")) if found and tm is not None else None


def _gaps(framework, folder: Path):
    from orchestrator.Core import target_dependencies as deps
    from orchestrator.Core.mojo.checks.dependencies import _own_folder
    if not _own_folder(framework, folder):
        return []
    try:
        entries = deps.load(folder)
    except ValueError:
        return []
    return [g for g in deps.dockerfile_gaps(folder, entries) if g.has_place]


def plan(framework, finding: Finding) -> Optional[RepairPlan]:
    folder = _folder_of(framework, finding)
    gaps = _gaps(framework, folder) if folder is not None else []
    if not gaps:
        return None
    steps = tuple(RepairStep(kind="add_steps", target_label=f"{g.file.name} of the module"[:200],
                             full_path=str(g.file),
                             before=f"{', '.join(g.names)[:120]} not pinned in it (sha256 {g.sha256[:16]})"[:200],
                             after=(f"{len(g.apt)} apt and {len(g.pip)} pip package(s) added, pinned and "
                                    "hash-checked")[:200],
                             undoable=True) for g in gaps)
    count = sum(len(g.apt) + len(g.pip) for g in gaps)
    return RepairPlan(repair_id=REPAIR_ID, finding_code=finding.code,
                      summary=(f"add the pinned steps of {count} listed package(s) to {len(steps)} Dockerfile(s) of the "
                               "module; the old file is kept first; nothing is installed on this computer")[:200],
                      steps=steps)


def apply_step(framework, step: RepairStep) -> None:
    """Keeps the old file, then writes the new text whole, only while the file is still the one the plan saw."""
    path = Path(step.full_path)
    folder = path.parent
    gap = next((g for g in _gaps(framework, folder) if g.file == path), None)
    if gap is None:
        raise StepChanged(f"{step.target_label} lacks nothing any more, or is not a module of your own")
    if f"sha256 {gap.sha256[:16]}" not in step.before:
        raise StepChanged(f"{step.target_label} changed since the plan was made")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != gap.sha256:
        raise StepChanged(f"{step.target_label} changed while the plan was applied")
    text = with_steps(raw.decode("utf-8"), gap, datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    if text is None:
        raise StepChanged(f"{step.target_label} has no place for the steps any more")
    keep = backup_folder(framework)
    keep.mkdir(mode=0o700, parents=True, exist_ok=True)
    (keep / f"{folder.name}-{path.name}-{gap.sha256}").write_bytes(raw)
    fresh = folder / f".{path.name}.mojo-new"   # hidden and fixed: a failed try leaves nothing that piles up
    fresh.write_bytes(text.encode("utf-8"))
    shutil.copymode(path, fresh)
    os.replace(fresh, path)
