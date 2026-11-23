#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo check: base images of the person's own targets that the catalogue knows a newer
release of

Mojo Oracle reads the Dockerfiles of each target in the person's own targets folder (orchestrator/runtime/build/versions.py)
and reports, as information, a Debian or Ubuntu base image that is older than the newest base of the same distribution
in configs/llama_runtime.yml (build_bases). Offline: nothing is fetched (DESIGN "Installer, platforms and
dependencies": the freshness check is offline by default). A newer base must be built and tested on the board first,
so the change is the person's; nothing is repaired. The list of every pinned version of the repository is
`python scripts/versions_report.py`.
"""

from pathlib import Path
from typing import List

from orchestrator.Core.mojo.checks.dependencies import _own_folder
from orchestrator.Core.mojo.findings import Finding, Severity, _ok

WHY = ("A newer release of the base gets security updates longer; it must be built and tested on the board before a "
       "module moves to it.")


def check_versions(framework) -> List[Finding]:
    from orchestrator.runtime.build.versions import inventory
    from orchestrator.runtime.catalog import load_catalog

    tm = getattr(framework, "target_manager", None)
    if tm is None:
        return []   # the target registry's own finding says so (check_targets)
    try:
        catalog = load_catalog()
    except Exception as e:  # noqa: BLE001 - an unreadable catalogue: the runtime check reports it, this one says why it stops
        return [Finding("versions", "versions.catalogue", Severity.INFO,
                        f"The base images were not compared: the runtime catalogue cannot be read ({e}).")]
    findings: List[Finding] = []
    for meta in tm.list_targets():
        tid = str(meta.get("id") or "")
        folder = tm.folder_of(tid) if tid else None
        if folder is None or not _own_folder(framework, folder):
            continue
        folder = Path(folder)
        behind = sorted({(p.what, p.newer_in_catalogue) for p in inventory(folder, catalog)
                         if p.kind == "base-image" and p.newer_in_catalogue})
        for image, newer in behind:
            findings.append(Finding("versions", f"versions.{tid}.base", Severity.INFO,
                                    f"Target {tid} builds on {image}; the runtime catalogue has the newer base {newer}.",
                                    "Move the module's FROM lines to the newer base once a build of it has run on "
                                    "the board.", where=str(folder), why=WHY, cannot="person"))
    return findings or [_ok("versions", "versions", "No base image of your own targets is older than the catalogue's")]
