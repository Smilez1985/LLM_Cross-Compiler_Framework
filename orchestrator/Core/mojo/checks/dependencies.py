#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo check: what the targets' dependency lists still lack

Mojo Oracle reads each target's list (`template_variables.dependencies` of its target.yml, Core/target_dependencies.py)
and reports what is left to the person:

- a list that is not valid (a warning: no build can use it);
- a vendor file that waits for the person's yes, because its licence was not accepted or it is not in the person's
  vendor store (a warning: the guided fetch is the person's);
- a package of the list that the module's Dockerfile does not name pinned (a warning; Mojo Miracle writes the steps for a
  module of the person's own targets folder after the person's yes, repair `dependencies.add_steps`);
- an entry nobody read, a named gap (information).

It only reads (the target.yml and the Dockerfiles). It starts no program and no container: whether a package is in a
build image is what `llm-cli module deps TARGET --in-image IMAGE` shows, and a package of the system or of pip is
installed by the build image's own steps, never on this computer.
"""

from pathlib import Path
from typing import List

from orchestrator.Core.mojo.findings import Finding, Severity, _ok
from orchestrator.utils.next_steps import NextStep


def _state_dir(framework):
    state = getattr(getattr(framework, "config", None), "user_state_dir", None)
    if state is not None:
        return Path(state)
    from orchestrator.runtime.profile import default_state_dir
    return Path(default_state_dir())


REPAIR_ID = "dependencies.add_steps"
STEPS_CODE = "dependencies.{target}.steps"


def _own_folder(framework, folder: Path) -> bool:
    """Whether `folder` is in the person's own targets folder (the framework never writes to a shipped target)."""
    from orchestrator.Core.target_manager import own_targets_dir
    try:
        own = Path(own_targets_dir(getattr(framework, "config", None))).resolve()
        return Path(folder).resolve().is_relative_to(own)
    except (OSError, ValueError):
        return False


def check_dependencies(framework) -> List[Finding]:
    from orchestrator.Core import target_dependencies as deps
    tm = getattr(framework, "target_manager", None)
    if tm is None:
        return []   # the target registry's own finding says so (check_targets)
    listed = 0
    findings: List[Finding] = []
    for meta in tm.list_targets():
        tid = str(meta.get("id") or "")
        folder = tm.folder_of(tid) if tid else None
        if folder is None:
            continue
        try:
            entries = deps.load(folder)
        except ValueError as e:
            listed += 1
            findings.append(Finding("dependencies", f"dependencies.{tid}.invalid", Severity.WARN,
                                    f"The dependency list of target {tid} is not valid: {e}",
                                    "Correct template_variables.dependencies in its target.yml; the target's check "
                                    "shows the same problem.", where=str(folder),
                                    why="A build of it cannot use the list, so what it needs is not installed.",
                                    cannot="person", steps=(NextStep("targets.check", {"target": tid}),)))
            continue
        if not entries:
            continue
        listed += 1
        rows = deps.statuses(entries, run=lambda _argv: 0,
                             have_vendor_file=deps.vendor_checker(_state_dir(framework), folder))
        waiting = [r.entry.name for r in rows if r.state == "waiting"]
        gaps = [r.entry.name for r in rows if r.gap]
        missing = deps.dockerfile_gaps(folder, entries)
        if missing:
            names = sorted({n for g in missing for n in g.names})
            repairable = _own_folder(framework, folder) and all(g.has_place for g in missing)
            why = ("A build of the module does not install what its list names, so the SDK may be missing in the "
                   "image.")
            note = f"Target {tid}: its Dockerfile does not name these listed package(s) pinned: {', '.join(names)}."
            if repairable:
                findings.append(Finding("dependencies", STEPS_CODE.format(target=tid), Severity.WARN, note,
                                        "Mojo Miracle adds the steps to the module's Dockerfile after your yes.",
                                        where=str(folder), why=why, repair_id=REPAIR_ID))
            else:
                findings.append(Finding("dependencies", STEPS_CODE.format(target=tid), Severity.WARN, note,
                                        "Add the pinned steps to the Dockerfile yourself, or make the module again "
                                        "with its list (a Dockerfile of a shipped target, or one without the place "
                                        "of the module's own pip packages, is yours to change).",
                                        where=str(folder), why=why, cannot="person"))
        if waiting:
            findings.append(Finding("dependencies", f"dependencies.{tid}.vendor", Severity.WARN,
                                    f"Target {tid} needs vendor file(s) that wait for your yes: {', '.join(waiting)}.",
                                    "Fetch each one under your own licence acceptance (the guided fetch).",
                                    where=str(folder),
                                    why="The module's build cannot use a licence-bound file before you accepted its "
                                        "licence and it is in your vendor store.", cannot="person",
                                    steps=tuple(NextStep("build.vendor_fetch", {"target": tid, "name": name})
                                                for name in waiting)))
        if gaps:
            findings.append(Finding("dependencies", f"dependencies.{tid}.gaps", Severity.INFO,
                                    f"Target {tid} lists entries nobody read (no source): {', '.join(gaps)}."))
        if not waiting and not gaps and not missing:
            findings.append(_ok("dependencies", f"dependencies.{tid}", f"Dependencies of {tid}: {len(entries)} listed"))
    if not listed:
        return [_ok("dependencies", "dependencies", "No target lists dependencies")]
    return findings
