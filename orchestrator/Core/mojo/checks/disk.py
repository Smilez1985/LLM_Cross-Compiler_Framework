#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo check: free disk space for the cache, output and build folders

The one disk rule (decision A4, Core/disk_plan.py): a disk is an error when less than its reserve stays free (2 GiB
or 2 % of the disk, whichever is larger) after what running builds and downloads of any process reserved there,
and a warning while less than twice the reserve stays free; the same verdict a build, a download or a snapshot gets.
"""

import os  # noqa: F401 - tests tell the disks apart through os.stat here (disk_plan.device_of reads it)
import shutil  # noqa: F401 - tests replace shutil.disk_usage through this module (disk_plan.free_space reads it)
from pathlib import Path
from typing import List

from orchestrator.Core.disk_plan import ReservationLedger, device_of, gb, nearest_existing, room
from orchestrator.Core.mojo.findings import Finding, Severity, _ok


def _ledger(framework):
    """The disk reservations of this user's builds and downloads (None without a per-user folder)."""
    from orchestrator.Core.build_plan import state_dir
    folder = state_dir(framework)
    return ReservationLedger(folder) if folder is not None else None


def _others(check) -> str:
    return (f" ({gb(check.reserved_by_others)} of it reserved by running builds or downloads)"
            if check.reserved_by_others else "")


def check_disk(framework) -> List[Finding]:
    findings = []
    engine = getattr(getattr(framework, "orchestrator", None), "build_engine", None)
    ledger = _ledger(framework)
    seen = set()
    for label, path in (("cache", getattr(engine, "cache_dir", None)), ("output", getattr(engine, "output_dir", None)),
                        ("builds", getattr(engine, "build_dir", None))):
        if path is None:
            continue
        path = Path(path)
        # The folders are created by the first build, so the nearest existing parent holds their disk.
        probe = nearest_existing(path)
        try:
            check = room(probe, 0, label=label, ledger=ledger)
        except OSError:
            continue
        key = device_of(probe)
        if key in seen:
            continue
        seen.add(key)
        # Tier 2 is a listing, not a fix (fix=None): nothing is deleted before the person confirmed the list.
        if check.verdict == "does_not_fit":
            findings.append(Finding("disk", f"disk.{label}", Severity.ERROR,
                                    f"The disk of the {label} folder ({path}) has only {gb(check.free)} free"
                                    f"{_others(check)}; {gb(check.reserve)} must stay free.",
                                    "Free disk space; large models need 2-3x their size during conversion. "
                                    + FREE_SPACE_HINT, fix_tier=2, where=str(path), why=DISK_WHY,
                                    cannot="deletion_list"))
        elif check.verdict == "tight":
            findings.append(Finding("disk", f"disk.{label}", Severity.WARN,
                                    f"The disk of the {label} folder has {gb(check.free)} free{_others(check)}: "
                                    f"less than twice the {gb(check.reserve)} that must stay free; large models need "
                                    "more.",
                                    "Free disk space before converting models larger than a few GB. "
                                    + FREE_SPACE_HINT, fix_tier=2, where=str(path), why=DISK_WHY,
                                    cannot="deletion_list"))
        else:
            findings.append(_ok("disk", f"disk.{label}", f"{gb(check.free)} free for {label}"))
    return findings


DISK_WHY = ("A conversion needs two to three times the model's size on this disk; a full disk stops a build half "
            "way.")
# What Mojo Miracle at a terminal (and `doctor --fix --tier 2`) does, as every disk finding says it.
FREE_SPACE_HINT = ("'llmcf mojo --miracle' (in a terminal) or Tools > Framework Build Images... in the GUI "
                   "lists the framework's build images (those left over from earlier builds and the named ones, each "
                   "with name, id, size and what it is) and deletes exactly those you tick and confirm; nothing is "
                   "ticked by default. Docker's build cache is not touched: it cannot be limited to the framework.")
