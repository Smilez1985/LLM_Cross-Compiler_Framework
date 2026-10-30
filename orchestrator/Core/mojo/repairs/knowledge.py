#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Miracle repair `knowledge.reimport`: installed packs imported again

Excerpts an earlier version imported carry neither their pack's digest nor its topic or target, so
search_knowledge(topic=...) and the Topic or target column miss them. Importing the pack again stores them with both
(CommunityManager.import_again): its pages are read again, a knowledge snapshot is taken first (so it can be undone),
and it takes about as long as the pack's first import. That rewrites what the person keeps, so the plan's class is
person_yes: Mojo Miracle applies it only after the person's own yes to this plan, at a terminal or in the framework's
Mojo window, never for an AI. Nothing is deleted.
"""

from pathlib import Path
from typing import Optional

from orchestrator.Core.mojo.findings import Finding
from orchestrator.Core.mojo.repairs.model import RepairPlan, RepairStep
from orchestrator.Core.mojo.repairs.state import StepChanged

REPAIR_ID = "knowledge.reimport"
CODE = "community.knowledge.untagged"


def _parts(framework):
    return getattr(framework, "community_manager", None), getattr(framework, "rag_manager", None)


def plan(framework, _finding: Finding) -> Optional[RepairPlan]:
    from orchestrator.Core.knowledge_snapshots import size_text
    cm, rag = _parts(framework)
    found = (cm.untagged_imports(rag) if cm is not None and rag is not None else [])[:50]
    if not found:
        return None
    steps = tuple(RepairStep(kind="reimport", target_label=item.label[:200], full_path=str(item.folder),
                             before=f"{item.excerpts} excerpt(s) without topic or target",
                             after=(f"imported again under {item.belongs}: {item.pages} page(s), "
                                    f"{size_text(item.size)} to read")[:200],
                             undoable=True)
                  for item in found)
    pages, size = sum(item.pages for item in found), size_text(sum(item.size for item in found))
    return RepairPlan(repair_id=REPAIR_ID, finding_code=CODE,
                      summary=(f"import {len(steps)} installed pack(s) again: {pages} page(s), {size}, about as long "
                               "as their first import; a knowledge snapshot first")[:200],
                      steps=steps)


def apply_step(framework, step: RepairStep) -> None:
    from orchestrator.Core.community_pack import PackError
    cm, rag = _parts(framework)
    if cm is None or rag is None:
        raise StepChanged("the knowledge base is not open any more")
    folder = Path(step.full_path)
    if not any(item.folder == folder for item in cm.untagged_imports(rag)):
        raise StepChanged(f"{step.target_label} needs no new import any more")
    try:
        cm.import_again(folder, rag)
    except PackError as e:
        raise RuntimeError(str(e)) from None
