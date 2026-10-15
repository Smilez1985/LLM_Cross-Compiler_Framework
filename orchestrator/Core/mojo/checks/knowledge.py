#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo check: the knowledge-base snapshots
"""

from typing import List

from orchestrator.Core.mojo.findings import Finding, _ok


def check_knowledge(framework) -> List[Finding]:
    """Knowledge-base snapshots: how many there are, their size and where, for the person's overview. There is no limit
    (DECISIONS, sixth round: as many as the disk holds), so no count of them is a finding, a reminder or a warning, and
    none is deleted by itself (round 5 f). Reads the folder only, never opens the knowledge base."""
    from orchestrator.Core.knowledge_snapshots import list_snapshots, size_text, snapshot_folder
    config = getattr(framework, "config", None)
    state_dir = getattr(config, "user_state_dir", None)
    if state_dir is None:
        return []
    folder = snapshot_folder(state_dir)
    snapshots = list_snapshots(folder)
    if not snapshots:
        return []
    total = size_text(sum(s.size_bytes for s in snapshots))
    return [_ok("knowledge", "knowledge.snapshots",
                f"{len(snapshots)} knowledge snapshots ({total}) in {folder}; no limit, none is deleted by itself "
                "('llm-cli knowledge snapshots' lists them)")]
