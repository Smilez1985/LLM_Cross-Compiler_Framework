#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Mojo check: the framework's runtime layer on this computer

The layer every build container runs (orchestrator/Core/runtime_layer.py): its verdict against the shipped copy
(layer_check), the shipped copy against its manifest, the toolbox (the static busybox and setpriv, and the way they
were made), and whether this host protects a build's end folder (Docker Desktop on Windows and macOS, and the legacy
mode without the build sandbox, do not: a build there is marked "end folder unprotected").

Every message is a fixed text of runtime_layer (VERDICT_TEXT, END_PROTECTED, END_UNPROTECTED) with the layer's own
file names only (label_names): a file someone put into the layer folder is counted, never named, so what an AI reads
of this check is the fixed vocabulary. A changed layer carries the Miracle repair `runtime_layer.restore`.
"""

from pathlib import Path
from typing import List, Optional

from orchestrator.Core.mojo.findings import Finding, Severity, _ok

AREA = "runtime_layer"
REPAIR_ID = "runtime_layer.restore"
WHY_CHANGED = ("Every build is refused until the layer matches the shipped copy: a changed stage runner or helper could "
               "give a stage module the rights the runner holds.")


def layer_cache_dir(framework) -> Optional[Path]:
    """The folder the builds keep the runtime layer in: the build engine's cache folder, else the setting cache_dir
    (runtime_layer.cache_dir_of, the engine's rule)."""
    engine = getattr(getattr(framework, "orchestrator", None), "build_engine", None)
    cache = getattr(engine, "cache_dir", None)
    if cache:
        return Path(cache)
    config = getattr(framework, "config", None)
    if config is None or not callable(getattr(config, "get", None)):
        return None
    from orchestrator.Core.runtime_layer import cache_dir_of
    resolve = getattr(framework, "resolve_path", None)
    return cache_dir_of(config, resolve if callable(resolve) else None)


def _sandbox_on(framework) -> bool:
    getter = getattr(getattr(framework, "config", None), "get", None)
    return not callable(getter) or getter("build_sandbox", True) is not False


def check_runtime_layer(framework, platform_name: Optional[str] = None) -> List[Finding]:
    from orchestrator.Core import runtime_layer as layer
    from orchestrator.Core.build_end import mounts_keep_owners
    cache = layer_cache_dir(framework)
    if cache is None:
        return [Finding(AREA, "runtime_layer.unknown", Severity.INFO,
                        "The runtime layer was not checked: the framework's cache folder is not known here.")]
    check = layer.layer_check(cache)
    findings: List[Finding] = []
    way = layer.toolbox_way(getattr(framework, "config", None)) if getattr(framework, "config", None) else ""
    if check.verdict == "intact":
        findings.append(_ok(AREA, "runtime_layer.intact", check.text))
    elif check.verdict in ("missing", "no_toolbox"):
        extra = f" {layer.TOOLBOX_WAY_TEXT}" if check.verdict == "no_toolbox" else ""
        findings.append(Finding(AREA, f"runtime_layer.{check.verdict}", Severity.INFO, check.text + extra))
    elif check.verdict == "changed":
        findings.append(Finding(AREA, "runtime_layer.changed", Severity.ERROR, check.text,
                                "Mojo Miracle puts the changed files back from the framework's shipped copy (each "
                                "replaced file is kept aside).", where=str(layer.layer_dir(cache)), why=WHY_CHANGED,
                                repair_id=REPAIR_ID, steps=(layer.RESTORE_STEP,)))
    elif check.verdict == "shipped_changed":
        findings.append(Finding(AREA, "runtime_layer.shipped_changed", Severity.ERROR, check.text,
                                "Install the framework again (or check its files out again): the layer is restored "
                                "only from an intact shipped copy.", where=str(layer.SHIPPED_DIR),
                                why="Without an intact shipped copy no build can check its runtime layer.",
                                cannot="not_automatic"))
    else:   # toolbox_changed
        findings.append(Finding(AREA, "runtime_layer.toolbox_changed", Severity.ERROR, check.text,
                                "Remove the runtime layer's cached tools in the framework's cache folder; the next "
                                f"build makes them again the way '{way or layer.DEFAULT_TOOLBOX_WAY}'.",
                                where=str(layer.toolbox_dir(cache)),
                                why="A changed busybox or setpriv runs in every build container as the stage runner.",
                                cannot="person"))
    record = layer.read_toolbox(layer.toolbox_dir(cache))
    if record is not None:
        findings.append(_ok(AREA, "runtime_layer.toolbox",
                            f"The runtime layer's tools were made the way '{record.get('way')}'; the setting "
                            f"runtime_layer_toolbox says '{way or layer.DEFAULT_TOOLBOX_WAY}'."))
    if mounts_keep_owners(platform_name) and _sandbox_on(framework):
        findings.append(_ok(AREA, "runtime_layer.end_folder", layer.END_PROTECTED))
    else:
        # Not a fault of this computer's setup the person can repair: a fact of the host every build records
        findings.append(Finding(AREA, "runtime_layer.end_folder", Severity.INFO, layer.END_UNPROTECTED))
    return findings


__all__ = ["AREA", "REPAIR_ID", "check_runtime_layer", "layer_cache_dir"]
