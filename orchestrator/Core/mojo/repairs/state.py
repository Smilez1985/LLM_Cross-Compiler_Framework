#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Miracle repair `state.tighten`: the per-user state folder to "only you"

The released tier 1 of `doctor --fix`: the folder that holds the tool permissions, the audit log and the chat loses
the access of other users (group and other bits removed, the owner's bits kept). The step is a compare-and-swap on
the open folder: it is opened without following a link, and its mode is changed only while it is still the mode the
plan showed. The installer's intended-modes manifest (per file, design M7) comes later; until then only the folder
itself is narrowed.
"""

import os
import stat
from pathlib import Path
from typing import Optional

from orchestrator.Core.mojo.findings import Finding
from orchestrator.Core.mojo.repairs.model import RepairPlan, RepairStep

REPAIR_ID = "state.tighten"


class StepChanged(RuntimeError):
    """What a step would change is no longer as the plan saw it; nothing was changed by this step."""


def state_dir(framework) -> Path:
    """The folder check_user_state looks at."""
    config = getattr(framework, "config", None)
    return Path(getattr(config, "user_state_dir", Path.home() / ".llm-framework"))


def plan(framework, _finding: Finding) -> Optional[RepairPlan]:
    """The dry run, or None when there is nothing to narrow (the folder is private, is a link, or this is not a POSIX
    system: Windows ACLs come with M7)."""
    if os.name != "posix":
        return None
    path = state_dir(framework)
    try:
        st = os.lstat(path)
    except OSError:
        return None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        return None   # a link is never followed: its target could be any folder
    mode = stat.S_IMODE(st.st_mode)
    if not mode & 0o077:
        return None
    return RepairPlan(repair_id=REPAIR_ID, finding_code="state.permissions",
                      summary="only you can open the state folder then (other users of this computer lose access "
                              "to it); can be undone",
                      steps=(RepairStep(kind="chmod", target_label="the state folder", full_path=str(path),
                                        before=f"{mode:04o}", after=f"{mode & 0o700:04o}", undoable=True),))


def apply_step(framework, step: RepairStep) -> None:
    """Narrows the folder's mode from exactly `before` to `after`, on the folder opened without following a link."""
    if step.kind != "chmod" or step.full_path != str(state_dir(framework)):
        raise StepChanged("the state folder is not the one the plan named")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(step.full_path, flags)
    try:
        now = stat.S_IMODE(os.fstat(fd).st_mode)
        if now != int(step.before, 8):
            raise StepChanged(f"its mode changed to {now:04o} since the plan was made")
        os.fchmod(fd, int(step.after, 8))
    finally:
        os.close(fd)
