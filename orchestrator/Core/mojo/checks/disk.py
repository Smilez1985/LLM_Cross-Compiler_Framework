#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo check: free disk space for the cache and output folders
"""

import os
import shutil
from pathlib import Path
from typing import List

from orchestrator.Core.mojo.findings import GB, Finding, Severity, _ok


def check_disk(framework) -> List[Finding]:
    findings = []
    engine = getattr(getattr(framework, "orchestrator", None), "build_engine", None)
    seen = set()
    for label, path in (("cache", getattr(engine, "cache_dir", None)), ("output", getattr(engine, "output_dir", None))):
        if path is None:
            continue
        path = Path(path)
        # The folders are created by the first build, so the nearest existing parent holds their disk.
        probe = next((p for p in (path, *path.parents) if p.exists()), path)
        try:
            usage = shutil.disk_usage(probe)
        except OSError:
            continue
        key = (os.stat(probe).st_dev if probe.exists() else str(probe))
        if key in seen:
            continue
        seen.add(key)
        free = usage.free / GB
        # Tier 2 is a listing, not a fix (fix=None): nothing is deleted before the person confirmed the list.
        if free < 5:
            findings.append(Finding("disk", f"disk.{label}", Severity.ERROR,
                                    f"Only {free:.1f} GB free for the {label} folder ({path}).",
                                    "Free disk space; large models need 2-3x their size during conversion. "
                                    + FREE_SPACE_HINT, fix_tier=2, where=str(path), why=DISK_WHY,
                                    cannot="deletion_list"))
        elif free < 20:
            findings.append(Finding("disk", f"disk.{label}", Severity.WARN,
                                    f"{free:.1f} GB free for the {label} folder; large models need more.",
                                    "Free disk space before converting models larger than a few GB. "
                                    + FREE_SPACE_HINT, fix_tier=2, where=str(path), why=DISK_WHY,
                                    cannot="deletion_list"))
        else:
            findings.append(_ok("disk", f"disk.{label}", f"{free:.0f} GB free for {label}"))
    return findings


DISK_WHY = ("A conversion needs two to three times the model's size on this disk; a full disk stops a build half "
            "way.")
# What Mojo Miracle at a terminal (and `doctor --fix --tier 2`) does, as every disk finding says it.
FREE_SPACE_HINT = ("'llmcf mojo --miracle' (in a terminal) or Tools > Framework Build Images... in the GUI "
                   "lists the framework's build images (those left over from earlier builds and the named ones, each "
                   "with name, id, size and what it is) and deletes exactly those you tick and confirm; nothing is "
                   "ticked by default. Docker's build cache is not touched: it cannot be limited to the framework.")
