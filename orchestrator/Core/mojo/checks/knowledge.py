#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo check: the knowledge-base snapshots
"""

from typing import List

from orchestrator.Core.mojo.findings import Finding, Severity, _ok

SNAPSHOT_WHY = ("A snapshot is taken before every Deep Ingest, restore, index of the framework's own documents and "
                "knowledge pack import, so the change can be undone; when it does not fit on the disk, that change "
                "does not run (there is no \"continue anyway\").")


def check_knowledge(framework) -> List[Finding]:
    """Knowledge-base snapshots: how many there are, their size against the free space of their disk and where, for
    the person's overview. There is no limit (DECISIONS, sixth round: as many as the disk holds), so no count of them
    is a finding, a reminder or a warning, and none is deleted by itself (round 5 f). What matters is the disk (eighth
    round, answer 2): when the next snapshot (about the size of the newest) would not fit, Deep Ingest, restores and
    knowledge pack imports are refused until there is room, which is a warning with the person's list; when it fits
    but leaves little free space, that is information. Reads the folder only, never opens the knowledge base."""
    from orchestrator.Core.knowledge_snapshots import (FREE_SPACE_STEP, list_snapshots, room_before, size_text,
                                                       snapshot_folder)
    config = getattr(framework, "config", None)
    state_dir = getattr(config, "user_state_dir", None)
    if state_dir is None:
        return []
    folder = snapshot_folder(state_dir)
    snapshots = list_snapshots(folder)
    if not snapshots:
        return []
    total = size_text(sum(s.size_bytes for s in snapshots))
    try:
        room = room_before(state_dir)
    except OSError:
        room = None
    free = f"; {size_text(room.free)} free on that disk" if room is not None and room.check is not None else ""
    line = (f"{len(snapshots)} knowledge snapshots ({total}) in {folder}{free}; no limit, none is deleted by itself "
            "('llm-cli knowledge snapshots' lists them)")
    if room is None or room.verdict == "enough":
        return [_ok("knowledge", "knowledge.snapshots", line)]
    if room.verdict == "tight":
        return [Finding("knowledge", "knowledge.snapshots", Severity.INFO,
                        f"{line}. The next snapshot (about {size_text(room.estimate)}) still fits, but leaves little "
                        "free space.", FREE_SPACE_STEP, where=str(folder), why=SNAPSHOT_WHY)]
    # Freeing the room means deleting or exporting snapshots: only what the person ticks in the snapshot list.
    return [Finding("knowledge", "knowledge.snapshots", Severity.WARN,
                    f"{line}. The next snapshot (about {size_text(room.estimate)}) does not fit: Deep Ingest, "
                    "restoring a snapshot and importing a knowledge pack are refused until there is room.",
                    FREE_SPACE_STEP, where=str(folder), why=SNAPSHOT_WHY, cannot="deletion_list")]
