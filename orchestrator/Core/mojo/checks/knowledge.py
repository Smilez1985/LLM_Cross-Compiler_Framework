#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo check: the knowledge base's snapshots and its storage
"""

from typing import List

from orchestrator.Core.mojo.findings import Finding, Severity, _ok

SNAPSHOT_WHY = ("A snapshot is taken before every Deep Ingest, restore, index of the framework's own documents and "
                "knowledge pack import, so the change can be undone; when it does not fit on the disk, that change "
                "does not run (there is no \"continue anyway\").")
STORAGE_WHY = ("The knowledge base is stored in Qdrant Edge (the Rust engine of Qdrant in the framework's process). A "
               "knowledge base in the storage of earlier versions is converted on first use and checked; the old "
               "folder stays until you delete it, and while a conversion failed the old storage keeps working.")
OLD_FOLDER_LIST = ("'llm-cli knowledge storage delete-old' in a terminal, or Tools > Knowledge Snapshots... > Delete "
                   "the old storage... in the GUI; both list it with its full path and size and ask you first")


def check_storage(state_dir) -> List[Finding]:
    """The storage of the local knowledge base, from the conversion record only (nothing is opened): a conversion
    that failed (a warning: the old storage is still in use, with the reason and how to try again), or the old folder
    kept after a conversion (information: only the person deletes it, through the deletion list; round 5 f)."""
    from orchestrator.Core.knowledge_snapshots import size_text
    from orchestrator.rag import migration
    try:
        info = migration.summary(state_dir)
    except OSError:
        return []
    folder = str(info.get("old_folder") or migration.old_folder(state_dir))
    if not migration.engine_available() and migration.edge_in_use(state_dir):
        # The knowledge base is in Qdrant Edge, which cannot be loaded now: it cannot be opened, and the old folder
        # never stands in for it (it lacks what was added since the conversion).
        return [Finding("knowledge", "knowledge.storage", Severity.ERROR, migration.EDGE_UNAVAILABLE + ".",
                        "Install the framework's requirements again (run the installer, or pip install -r "
                        "requirements.txt); the knowledge base opens again as it was.",
                        where=str(migration.edge_folder(state_dir)), why=STORAGE_WHY, cannot="person")]
    if not migration.engine_available():
        return [Finding("knowledge", "knowledge.storage", Severity.INFO,
                        migration.ENGINE_MISSING + ". It works, but searches are slower and it needs more memory.",
                        "Nothing to do; it is converted to Qdrant Edge once the engine is available for this computer.",
                        where=folder, why=STORAGE_WHY)]
    if info.get("state") == "failed" and info.get("old_store"):
        return [Finding("knowledge", "knowledge.storage", Severity.WARN,
                        "The knowledge base still uses the storage of earlier versions: its conversion to Qdrant Edge "
                        f"failed ({info.get('reason', '')}). It keeps working.",
                        "Free what the reason names. " + migration.retry_sentence(str(info.get("code") or "")),
                        where=folder, why=STORAGE_WHY, cannot="person")]
    if info.get("state") == "converted" and info.get("old_folder_kept"):
        size = size_text(int(info.get("old_folder_bytes") or 0))
        return [Finding("knowledge", "knowledge.storage", Severity.INFO,
                        f"The knowledge base was converted to Qdrant Edge on {info.get('converted_at', '')}; the old "
                        f"storage folder {folder} ({size}) is kept until you delete it, nothing deletes it by itself.",
                        f"If you no longer need it: {OLD_FOLDER_LIST}.", where=folder, why=STORAGE_WHY,
                        cannot="deletion_list")]
    return []


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
    storage = check_storage(state_dir)
    folder = snapshot_folder(state_dir)
    snapshots = list_snapshots(folder)
    if not snapshots:
        return storage
    total = size_text(sum(s.size_bytes for s in snapshots))
    try:
        room = room_before(state_dir)
    except OSError:
        room = None
    free = f"; {size_text(room.free)} free on that disk" if room is not None and room.check is not None else ""
    line = (f"{len(snapshots)} knowledge snapshots ({total}) in {folder}{free}; no limit, none is deleted by itself "
            "('llm-cli knowledge snapshots' lists them)")
    if room is None or room.verdict == "enough":
        return storage + [_ok("knowledge", "knowledge.snapshots", line)]
    if room.verdict == "tight":
        return storage + [Finding(
            "knowledge", "knowledge.snapshots", Severity.INFO,
            f"{line}. The next snapshot (about {size_text(room.estimate)}) still fits, but leaves little free space.",
            FREE_SPACE_STEP, where=str(folder), why=SNAPSHOT_WHY)]
    # Freeing the room means deleting or exporting snapshots: only what the person ticks in the snapshot list.
    return storage + [Finding(
        "knowledge", "knowledge.snapshots", Severity.WARN,
        f"{line}. The next snapshot (about {size_text(room.estimate)}) does not fit: Deep Ingest, restoring a snapshot "
        "and importing a knowledge pack are refused until there is room.",
        FREE_SPACE_STEP, where=str(folder), why=SNAPSHOT_WHY, cannot="deletion_list")]
