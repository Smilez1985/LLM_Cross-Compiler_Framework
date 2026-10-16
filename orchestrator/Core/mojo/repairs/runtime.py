#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Miracle repair `runtime.switch_intact`: Ditto's server starts an intact build

When the active self-built llama.cpp build is missing, Ditto's local model server has nothing to start. If an intact,
verified build of the pinned commit is stored, the active reference switches to the newest of them (the store checks
the build again before it switches). Nothing is built, downloaded or deleted; switching back is possible.
"""

from pathlib import Path
from typing import List, Optional

from orchestrator.Core.mojo.findings import Finding
from orchestrator.Core.mojo.repairs.model import RepairPlan, RepairStep
from orchestrator.Core.mojo.repairs.state import StepChanged

REPAIR_ID = "runtime.switch_intact"


def _store(framework):
    from orchestrator.runtime.build.store import BuildStore
    config = getattr(framework, "config", None)
    return BuildStore(Path(getattr(config, "user_state_dir", Path.home() / ".llm-framework")))


def _intact(store) -> List:
    """Stored builds of Ditto's server that may become the active one, newest first: verified on this machine, of
    the pinned commit, and still matching their manifest."""
    from orchestrator.runtime.build.host import PURPOSE
    from orchestrator.runtime.catalog import load_catalog
    pin = load_catalog().llama_cpp.source
    return [b for b in store.list_builds() if b.purpose == PURPOSE and b.verified
            and (pin is None or b.commit == pin.commit) and not store.check_files(b)]


def plan(framework, _finding: Finding) -> Optional[RepairPlan]:
    store = _store(framework)
    missing = store.references().get("active")
    if not missing or store.get(missing) is not None:
        return None
    builds = _intact(store)
    if not builds:
        return None
    new = builds[0]
    return RepairPlan(repair_id=REPAIR_ID, finding_code="runtime.build.missing",
                      summary=f"Ditto's local model server starts the intact stored build {new.build_id[:16]} instead "
                              "of the missing one; can be undone",
                      steps=(RepairStep(kind="switch_ref", target_label="the active llama.cpp build",
                                        full_path=str(store.active_file), before=missing[:16],
                                        after=new.build_id[:16], undoable=True),))


def apply_step(framework, step: RepairStep) -> None:
    """Switches the active reference only while it still names the missing build of the plan."""
    store = _store(framework)
    if step.kind != "switch_ref" or step.full_path != str(store.active_file):
        raise StepChanged("the runtime store is not the one the plan named")
    if store.references().get("active", "")[:16] != step.before:
        raise StepChanged("the active build changed since the plan was made")
    store.activate(step.after)   # checks the build again: verified, and every file as its manifest says
