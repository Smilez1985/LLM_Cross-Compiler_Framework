#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo check: the target registry and each target's capabilities
"""

from typing import List

from orchestrator.Core.mojo.findings import Finding, Severity, _ok


def check_targets(framework) -> List[Finding]:
    tm = getattr(framework, "target_manager", None)
    if tm is None:
        return [Finding("targets", "targets.registry", Severity.ERROR, "Target registry not loaded.",
                        where="the target registry", why="No build can be planned or checked without the targets.",
                        cannot="not_automatic")]
    findings = []
    targets = tm.list_targets()
    if not targets:
        return [Finding("targets", "targets.none", Severity.ERROR, f"No targets found in {tm.targets_dir}.",
                        "Check the setting targets_dir.", where=str(tm.targets_dir),
                        why="Every build needs a target (the hardware it is made for).", cannot="person")]
    for meta in targets:
        tid = meta.get("id")
        if tm.get_capabilities(tid) is None:
            findings.append(Finding("targets", f"targets.{tid}.capabilities", Severity.WARN,
                                    f"Target {tid} declares no valid capabilities; GUI, CLI and AI clients cannot "
                                    "check its build settings.", "Add a 'capabilities:' block to its target.yml "
                                    "(see targets/Rockchip/target.yml).",
                                    where=str(_folder(tm, tid) or f"the target {tid}"),
                                    why="Wrong build settings for it show only when a build fails.",
                                    cannot="person"))
        else:
            findings.append(_ok("targets", f"targets.{tid}", f"Target {tid}"))
    return findings


def _folder(tm, tid):
    """The target's folder, for the person's report (an AI reads its label)."""
    folder_of = getattr(tm, "folder_of", None)
    return folder_of(tid) if callable(folder_of) else None
