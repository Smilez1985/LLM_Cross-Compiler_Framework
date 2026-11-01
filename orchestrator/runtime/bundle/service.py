#!/usr/bin/env python3
"""
The bundle steps every channel shares (one code path: `llmcf bundle ...`, Boards > Board bundle... and the MCP tools
bundle_plan / bundle_deploy call these): the board's profile and runtime package, the plan, the build into the store
and the run on the board. Nothing here prints, asks, trusts a host key or logs in; the caller hands over its own
login (the person's channels: person_steps.connect_stored; the AI's deploy after the person's approval:
device/service.connect with the trusted key only).
"""

import re
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from orchestrator.runtime.bundle import blocks as blk
from orchestrator.runtime.bundle import generator as gen


def runtime_for(state_dir: Path, device_id: str, profile: Any, source: Optional[Callable[[], Any]] = None
                ) -> gen.RuntimePackage:
    """The board's runtime tarball of deploy way 2 (steps.stored_runtime), or why there is none (never built here)."""
    from orchestrator.runtime.device import steps
    if source is None:
        from orchestrator.runtime.device.service import pinned_source
        source = pinned_source
    try:
        stored = steps.stored_runtime(Path(state_dir), device_id, profile, source)
    except Exception as e:  # noqa: BLE001 - PlanError, NotBuilt, PackageNeedsSource, PackageError: the plan says so
        text = str(e) or e.__class__.__name__
        return gen.RuntimePackage(missing=f"the board's llama.cpp runtime package is not ready ({text[:200]}); "
                                          "build the board's runtime first")
    packaged = stored.packaged
    return gen.RuntimePackage(Path(packaged.path), packaged.sha256, stored.build.build_id)


def device_profile(state_dir: Path, device_id: str):
    from orchestrator.runtime.profile import ProfileStore
    profile = ProfileStore(Path(state_dir)).load_device(device_id)
    if profile is None:
        raise gen.BundleError(f"board {device_id} has no stored probe; probe it first")
    return profile


def plan_for_device(state_dir: Path, device_id: str, selection: gen.Selection, *, config: Any = None,
                    app_root: Optional[Path] = None, mode: str = "private", allow_own: bool = False,
                    runtime: Optional[gen.RuntimePackage] = None, profile: Any = None) -> gen.BundlePlan:
    """plan_bundle for a stored board: its probe, the block catalog of this framework, its runtime package."""
    profile = profile or device_profile(state_dir, device_id)
    needs_runtime = any(need.source == "runtime" for bid in selection.blocks
                        for need in getattr(blk.load_catalog(app_root).get(bid), "files", ()))
    if runtime is None and needs_runtime:
        runtime = runtime_for(state_dir, device_id, profile)
    return gen.plan_bundle(selection, catalog=blk.load_catalog(app_root), profile=profile, device_id=device_id,
                           runtime=runtime, config=config, mode=mode, allow_own=allow_own)


_ASSIGN = re.compile(r"^([a-z][a-z0-9-]{0,40})\.([a-z][a-z0-9_]{0,31})=(.*)$", re.ASCII | re.DOTALL)


def assignments(items: Iterable[str], what: str) -> List[Tuple[str, str, str]]:
    """COMPONENT.NAME=VALUE items of the command line (gen.BundleError naming the one that is not)."""
    out = []
    for item in items:
        m = _ASSIGN.fullmatch(item or "")
        if not m:
            raise gen.BundleError(f"{what} takes COMPONENT.NAME=VALUE, not {item!r}"[:200])
        out.append((m.group(1), m.group(2), m.group(3)))
    return out


def selection_from(*, preset: Optional[str] = None, blocks: Iterable[str] = (), sets: Iterable[str] = (),
                   files: Iterable[str] = (), online: Iterable[str] = (), sha256: Iterable[str] = (),
                   sizes: Iterable[str] = (), licences: Iterable[str] = (),
                   unprivileged_only: bool = False) -> gen.Selection:
    """The person's choice from the command line's options (the window builds its Selection directly)."""
    chosen = list(blocks)
    if preset:
        selection = gen.Selection.from_preset(preset, unprivileged_only=unprivileged_only)
        selection.blocks += [b for b in chosen if b not in selection.blocks]
    elif chosen:
        selection = gen.Selection(blocks=chosen, unprivileged_only=unprivileged_only)
    else:
        raise gen.BundleError("choose a preset or at least one block")
    for comp, name, value in assignments(sets, "--set"):
        selection.values.setdefault(comp, {})[name] = value
    choices: Dict[Tuple[str, str], gen.FileChoice] = {}

    def choice(comp: str, role: str) -> gen.FileChoice:
        found = choices.get((comp, role))
        if found is None:
            found = choices[(comp, role)] = gen.FileChoice()
            selection.files.setdefault(comp, {})[role] = found
        return found
    for comp, role, value in assignments(files, "--file"):
        choice(comp, role).path = value
    for comp, role, value in assignments(online, "--online"):
        choice(comp, role).url = value
    for comp, role, value in assignments(sha256, "--sha256"):
        choice(comp, role).sha256 = value.strip().lower()
    for comp, role, value in assignments(sizes, "--size"):
        if not value.isdigit():
            raise gen.BundleError("--size takes a number of bytes")
        choice(comp, role).size = int(value)
    for comp, role, value in assignments(licences, "--licence"):
        choice(comp, role).licence = value.strip()
    return selection


def run_on_board(open_session: Callable[[], Any], built: gen.BuiltBundle, action: str, *,
                 store: Optional[gen.BundleStore] = None, device_id: str = "",
                 say: Callable[[str], None] = lambda _m: None):
    """The installer `action` on the board through the caller's login (`open_session()` gives the board session),
    the session closed in every case (board.run_bundle)."""
    from orchestrator.runtime.bundle.board import run_bundle
    from orchestrator.runtime.device.remote import RemoteShell
    session = open_session()
    try:
        return run_bundle(RemoteShell(session), built, action, store=store, device_id=device_id, say=say)
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


__all__ = ["assignments", "device_profile", "plan_for_device", "run_on_board", "runtime_for", "selection_from"]
