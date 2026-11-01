#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Miracle repair `runtime_layer.restore`: the runtime layer from the shipped copy

A changed runtime layer (runtime_layer.layer_check: "changed") refuses every build. This repair puts each changed
file of the assembled layer back from where the framework assembles it from: a script from the shipped copy
(orchestrator/runtime_layer/, itself checked against its manifest), bin/busybox and bin/setpriv from the toolbox
(checked against its record). One step per file (kind restore_file), each a compare-and-swap: the file is replaced
only while it is still as the plan saw it, and only by a source that still has the digest the layer must hold. The
replaced file is kept aside in the cache folder (runtime-layer/replaced/<time>/), so a step can be undone; nothing is
deleted. A layer folder that holds files that are not the layer's is not planned (the person removes them).
"""

import os
import shutil
import stat
import time
from pathlib import Path
from typing import Optional

from orchestrator.Core.mojo.findings import Finding
from orchestrator.Core.mojo.repairs.model import RepairPlan, RepairStep
from orchestrator.Core.mojo.repairs.state import StepChanged

REPAIR_ID = "runtime_layer.restore"
CODE = "runtime_layer.changed"
KEPT_DIR = "replaced"


def _cache(framework) -> Path:
    from orchestrator.Core.mojo.checks.runtime_layer import layer_cache_dir
    cache = layer_cache_dir(framework)
    if cache is None:
        raise StepChanged("the framework's cache folder is not known here")
    return cache


def _state(path: Path) -> str:
    """A file's state in framework words: the start of its sha256 and its mode, 'missing' or 'not a file'."""
    from orchestrator.Core.runtime_layer import sha256_file
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return "missing"
    if not stat.S_ISREG(st.st_mode):
        return "not a file"
    return f"{sha256_file(path)[:16]} {stat.S_IMODE(st.st_mode):04o}"


def plan(framework, _finding: Finding) -> Optional[RepairPlan]:
    from orchestrator.Core import runtime_layer as layer
    cache = _cache(framework)
    check = layer.layer_check(cache)
    if check.verdict != "changed":
        return None
    expected = layer.expected_files(cache)
    if any(name not in expected for name in check.names):
        return None   # a file that is not the layer's: removing it is the person's (no repair deletes)
    folder = layer.layer_dir(cache)
    steps = []
    for name in check.names:
        if layer.sha256_file(layer.shipped_source(cache, name)) != expected[name]:
            return None   # the source itself is not whole: layer_check reports that first, nothing to restore from
        steps.append(RepairStep(kind="restore_file", target_label=f"the runtime layer: {name}",
                                full_path=str(folder / name), before=_state(folder / name),
                                after=f"{expected[name][:16]} {layer.layer_mode(name):04o}", undoable=True))
    if not steps:
        return None
    count = f"{len(steps)} file" + ("s" if len(steps) != 1 else "")
    return RepairPlan(repair_id=REPAIR_ID, finding_code=CODE,
                      summary=f"the runtime layer's changed files ({count}) are put back from the framework's shipped "
                              "copy; each replaced file is kept aside in the cache folder; can be undone",
                      steps=tuple(steps))


def apply_step(framework, step: RepairStep) -> None:
    """Puts one file back while it is still as the plan saw it, from a source that still has the layer's digest; the
    file it replaces is moved aside first (runtime-layer/replaced/<time>/)."""
    from orchestrator.Core import runtime_layer as layer
    if step.kind != "restore_file":
        raise StepChanged("this is not a step of the runtime layer's restore")
    cache = _cache(framework)
    folder = layer.layer_dir(cache)
    path = Path(step.full_path)
    try:
        name = path.relative_to(folder).as_posix()
    except ValueError:
        raise StepChanged("the file is not in the runtime layer the plan named") from None
    expected = layer.expected_files(cache)
    if name not in expected:
        raise StepChanged("the file is not one of the runtime layer's")
    if _state(path) != step.before:
        raise StepChanged("the file changed since the plan was made")
    source = layer.shipped_source(cache, name)
    if layer.sha256_file(source) != expected[name]:
        raise StepChanged("the framework's own copy of the file is not whole any more")
    parent = path.parent
    for d in (folder, parent):
        if d.is_symlink():
            raise StepChanged("the runtime layer's folder was replaced by a link")
    kept = cache / "runtime-layer" / KEPT_DIR / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    modes = {d: stat.S_IMODE(os.lstat(d).st_mode) for d in {folder, parent}}
    try:
        for d in modes:
            os.chmod(d, 0o755)
        if os.path.lexists(path):
            kept.mkdir(parents=True, exist_ok=True)
            os.replace(path, kept / name.replace("/", "_"))
        fresh = parent / f".{path.name}.restore"
        shutil.copyfile(source, fresh)
        os.chmod(fresh, layer.layer_mode(name))
        os.replace(fresh, path)
    finally:
        for d, mode in modes.items():
            try:
                os.chmod(d, mode)
            except OSError:
                pass
