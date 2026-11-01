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
                    runtime: Optional[gen.RuntimePackage] = None, profile: Any = None,
                    index: Any = None) -> gen.BundlePlan:
    """plan_bundle for a stored board: its probe, the block catalog of this framework (`index`: the verified cached
    community index, CommunityManager.cached_index(revocations=True), so a revoked block is not used), its runtime
    package."""
    profile = profile or device_profile(state_dir, device_id)
    catalog = blk.load_catalog(app_root, index=index)
    needs_runtime = any(need.source == "runtime" for bid in selection.blocks
                        for need in getattr(catalog.get(bid), "files", ()))
    if runtime is None and needs_runtime:
        runtime = runtime_for(state_dir, device_id, profile)
    return gen.plan_bundle(selection, catalog=catalog, profile=profile, device_id=device_id,
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


# The person's questions before a run changes a board (the CLI and the Board bundle window ask exactly these words;
# "No" is the default). {bundle}: the bundle id, {board}: the board's label or device ID, {size}: what is sent.
DEPLOY_QUESTION = ("Send bundle {bundle} ({size}) to board {board} and run its installer there (apply)? It installs "
                   "the unprivileged steps; root actions stop before they run (they need llmcf-install).")
UNINSTALL_QUESTION = ("Uninstall bundle {bundle} from board {board}? The installer the board kept removes only the "
                      "bundle's own steps, newest first.")
ROLLBACK_QUESTION = ("Roll back the last run of bundle {bundle} on board {board}? The installer the board kept undoes "
                     "the steps of its last apply.")
# What changes a board, and which installer action shows it first without changing anything.
CHANGING_ACTIONS = {"apply": "plan", "uninstall": "plan-uninstall", "rollback": "status"}


def catalog_sources(framework: Any) -> Tuple[Optional[Path], Any]:
    """(app_root, index) every caller of plan_for_device / load_catalog passes: the folder whose community/blocks/
    holds the installed and own blocks, and the verified cached community index with its revocations
    (CommunityManager.cached_index(revocations=True); None when there is none, so nothing counts as revoked)."""
    manager = getattr(framework, "community_manager", None)
    if manager is None and framework is not None:
        try:
            from orchestrator.Core.community_manager import CommunityManager
            manager = CommunityManager(framework)
        except Exception:  # noqa: BLE001 - no community manager: the framework's own blocks only
            manager = None
    if manager is None:
        return None, None
    try:
        index = manager.cached_index(revocations=True)
    except Exception:  # noqa: BLE001 - an unreadable cache: no revocations known, as the Community Hub shows it
        index = None
    root = getattr(manager, "app_root", None)
    return (Path(root) if root else None), index


def revoked_blocks(built: gen.BuiltBundle, app_root: Optional[Path], index: Any) -> List[str]:
    """Why a stored bundle may not be applied any more ([]: nothing): a community block of one of its components that
    the verified cached index (catalog_sources) revoked since the bundle was built (blocks.block_revoked). Every
    channel asks this before an apply sends anything (`llmcf bundle deploy`, the window's Deploy..., bundle_deploy);
    status, rollback and uninstall of what a board holds stay possible. The sentences name no path."""
    out = []
    for comp in built.manifest.components:
        if comp.block_trust != "framework" and blk.block_revoked(app_root, comp.block, comp.block_version, index):
            out.append(f"block {comp.block} {comp.block_version} of component {comp.id} was revoked in the signed "
                       "index since the bundle was built; nothing was sent (build the bundle again without it)")
    return out


def bundle_rows(store: gen.BundleStore) -> List[Dict[str, Any]]:
    """The stored bundles as `llmcf bundle list` and the window list them (labels and sizes; the person's views add
    the folder): id, board, mode, components with their blocks, root actions, bytes sent, created, and per board where
    its last run stands."""
    rows = []
    for built in store.list():
        m = built.manifest
        size = sum(f.size for comp in m.components for f in comp.files if f.source == "bundle")
        rows.append({"bundle_id": m.bundle_id, "device_id": m.board.device_id, "arch": m.board.arch, "mode": m.mode,
                     "created": m.created, "bytes": size,
                     "components": [{"id": c.id, "block": c.block, "kind": c.kind} for c in m.components],
                     "root_actions": [a.action for a in m.root_actions],
                     "boards": {d: {"last": str((e or {}).get("last", "")), "at": str((e or {}).get("at", ""))}
                                for d, e in sorted(store.boards(m.bundle_id).items())}})
    return rows


def board_contact(state_dir: Path, device_id: str) -> Tuple[str, int, str, str]:
    """(host, port, SSH user, label) of the stored board a bundle was made for, for the person's own login (the CLI
    and the Board bundle window log in with person_steps.connect_stored and the framework's key only; this module
    never logs in). gen.BundleError with the person's words when the board has no stored probe, no SSH user in its
    deploy settings or no usable address."""
    # devices.board_address, not person_steps: no module an AI can reach imports the person's trust writers
    from orchestrator.runtime.device.devices import board_address
    from orchestrator.runtime.device.settings import DeploySettings, SettingsError
    from orchestrator.runtime.profile import ProfileStore
    store = ProfileStore(Path(state_dir))
    if store.load_device(device_id) is None:
        raise gen.BundleError(f"board {device_id} has no stored probe; probe it first")
    try:
        settings = DeploySettings.from_dict(store.load_deploy_settings(device_id))
    except (SettingsError, ValueError) as e:
        raise gen.BundleError(f"the deploy settings of board {device_id} cannot be used: {e}") from None
    if not settings.ssh_user:
        raise gen.BundleError(f"No SSH user is set for board {device_id}: set the board's deploy user in its "
                              "deploy settings first.")
    try:
        host, port = board_address(store, device_id)
    except ValueError as e:
        raise gen.BundleError(str(e)) from None
    label = str(store.device_info(device_id).get("label") or device_id)
    return host, port, settings.ssh_user, label


def question(action: str, built: gen.BuiltBundle, board: str) -> str:
    """The person's question before `action` (apply, uninstall, rollback) changes the board."""
    size = sum(entry.size for _n, _p, entry in built.payload())
    text = {"apply": DEPLOY_QUESTION, "uninstall": UNINSTALL_QUESTION, "rollback": ROLLBACK_QUESTION}[action]
    return text.format(bundle=built.bundle_id, board=board, size=gen._mb(size))


__all__ = ["CHANGING_ACTIONS", "DEPLOY_QUESTION", "ROLLBACK_QUESTION", "UNINSTALL_QUESTION", "assignments",
           "board_contact", "bundle_rows", "catalog_sources", "device_profile", "plan_for_device", "question",
           "revoked_blocks", "run_on_board", "runtime_for", "selection_from"]
