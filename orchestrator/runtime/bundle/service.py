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


def component_pin(project: Optional[str] = None):
    """The pinned source of a project of the self-build path (configs/llama_runtime.yml: llama_cpp.source,
    whisper_cpp.source); gen.BundleError when the installation lacks it."""
    from orchestrator.runtime.build.projects import pin_of
    from orchestrator.runtime.catalog import load_catalog
    pin = pin_of(load_catalog(), project)
    if pin is None:
        raise gen.BundleError(f"configs/llama_runtime.yml names no pinned {project or 'llama.cpp'} source; this "
                              "installation cannot build it")
    return pin


def runtime_for(state_dir: Path, device_id: str, profile: Any, source: Optional[Callable[[], Any]] = None,
                project: Optional[str] = None) -> gen.RuntimePackage:
    """The board's package of a project of the self-build path, never built here: llama.cpp's runtime tarball of
    deploy way 2 (steps.stored_runtime), or with `project` whisper.cpp's board package the same way; or why there
    is none."""
    from orchestrator.runtime.device import steps
    name = project or "llama.cpp"
    if source is None:
        if project is None:
            from orchestrator.runtime.device.service import pinned_source
            source = pinned_source
        else:
            def source():
                return component_pin(project)
    try:
        stored = steps.stored_runtime(Path(state_dir), device_id, profile, source) if project is None else \
            steps.stored_runtime(Path(state_dir), device_id, profile, source, project)
    except Exception as e:  # noqa: BLE001 - PlanError, NotBuilt, PackageNeedsSource, PackageError: the plan says so
        text = str(e) or e.__class__.__name__
        if name == "llama.cpp":
            return gen.RuntimePackage(missing=f"the board's llama.cpp runtime package is not ready ({text[:200]}); "
                                              "build the board's runtime first")
        return gen.RuntimePackage(project=name, missing=f"the board's {name} package is not ready ({text[:200]}); "
                                                        "the framework builds it for the board when you build the "
                                                        "bundle, after your yes")
    packaged, plan = stored.packaged, stored.build.plan
    return gen.RuntimePackage(Path(packaged.path), packaged.sha256, stored.build.build_id, project=name,
                              arch=str((plan.get("target") or {}).get("arch") or ""),
                              cpu_features=tuple((plan.get("cpu_features") or {}).get("used") or ()))


def needed_projects(catalog: blk.BlockCatalog, selection: gen.Selection) -> List[str]:
    """The projects of the self-build path whose board package the chosen blocks take (llama.cpp first)."""
    found: List[str] = []
    for block_id in selection.blocks:
        for need in getattr(catalog.get(block_id), "files", ()):
            if need.source == "runtime" and need.project not in found:
                found.append(need.project)
    return sorted(found, key=lambda p: (p != "llama.cpp", p))


# The person's question before the framework builds a board package the bundle needs (the CLI and the Board bundle
# window ask exactly these words; "No" is the default). {projects}: the project names, {board}: the board.
COMPONENT_QUESTION = ("Build {projects} for board {board} now? It is built from the pinned source for the board's "
                      "probe (architecture, C library, CPU features) in the builder container without network; the "
                      "pinned source is fetched first when it is not cached (in offline mode only from your own "
                      "network).")


def component_question(projects: Iterable[str], board: str) -> str:
    return COMPONENT_QUESTION.format(projects=" and ".join(projects), board=board)


def build_components(state_dir: Path, device_id: str, profile: Any, projects: Iterable[str], *,
                     settings: Any = None, mirror: Optional[str] = None,
                     say: Callable[[str], None] = lambda _m: None, cancelled: Callable[[], bool] = lambda: False,
                     ai_started: bool = False) -> Dict[str, gen.RuntimePackage]:
    """Builds the board packages of `projects` for the stored board, after the person's yes (COMPONENT_QUESTION):
    each through the one self-build path of llama.cpp's device runtime (steps.plan_runtime and steps.build_runtime:
    the pinned source fetched by commit and checked, the cross build in the builder container without network, the
    format check, the store with its record, the board package). Returns each project's RuntimePackage
    (runtime_for). gen.BundleError with the person's words when one cannot be built."""
    from orchestrator.runtime.build.recipe import PlanError
    from orchestrator.runtime.build.source import SourceError
    from orchestrator.runtime.build.store import BuildFailed, StoreError
    from orchestrator.runtime.device import steps
    from orchestrator.runtime.device.package import PackageError
    out: Dict[str, gen.RuntimePackage] = {}
    for project in projects:
        pin = component_pin(None if project == "llama.cpp" else project)
        try:
            plan = steps.plan_runtime(profile) if project == "llama.cpp" else steps.plan_runtime(profile, project)
            say(f"building {project} for board {device_id} in the builder container")
            steps.build_runtime(Path(state_dir), device_id, profile, plan, pin, mirror=mirror, log=say,
                                cancelled=cancelled, reuse_package=True, settings=settings, ai_started=ai_started,
                                on_step=lambda kind, _b, p=project: say(f"fetching the pinned {p} source")
                                if kind == "fetch" else None)
        except PlanError as e:
            raise gen.BundleError(f"{project} cannot be planned for this board: {e}") from None
        except SourceError as e:
            raise gen.BundleError(f"the pinned {project} source: {e}") from None
        except (BuildFailed, StoreError) as e:
            raise gen.BundleError(f"{project} could not be built for the board: {e}") from None
        except PackageError as e:
            raise gen.BundleError(f"{project} could not be packaged for the board: {e}") from None
        except steps.BuildCancelled:
            raise gen.BundleError(f"the build of {project} was cancelled; nothing was built") from None
        out[project] = runtime_for(state_dir, device_id, profile,
                                   project=None if project == "llama.cpp" else project)
    return out


def accept_terms(catalog: blk.BlockCatalog, selection: gen.Selection, block_ids: Iterable[str]) -> List[str]:
    """Records in `selection` that the person read and accepted the terms of `block_ids` as the catalog holds them
    now (bound to Block.terms_sha256, so changed terms need a new yes). Only the person's channels call this, after
    showing Block.terms_text(); the AI's tools never do. Returns the blocks accepted (a block without terms is
    left out)."""
    done = []
    for block_id in block_ids:
        block = catalog.get(block_id)
        if block is not None and block.terms:
            selection.accepted_terms[block_id] = block.terms_sha256
            done.append(block_id)
    return done


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
    projects = needed_projects(catalog, selection)
    if runtime is None and "llama.cpp" in projects:
        runtime = runtime_for(state_dir, device_id, profile)
    others = {p: runtime_for(state_dir, device_id, profile, project=p) for p in projects if p != "llama.cpp"}
    return gen.plan_bundle(selection, catalog=catalog, profile=profile, device_id=device_id,
                           runtime=runtime, config=config, mode=mode, allow_own=allow_own, runtimes=others)


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
                   unprivileged_only: bool = False, accepted_terms: Optional[Dict[str, str]] = None
                   ) -> gen.Selection:
    """The person's choice from the command line's options (the window builds its Selection directly)."""
    chosen = list(blocks)
    if preset:
        selection = gen.Selection.from_preset(preset, unprivileged_only=unprivileged_only)
        selection.blocks += [b for b in chosen if b not in selection.blocks]
    elif chosen:
        selection = gen.Selection(blocks=chosen, unprivileged_only=unprivileged_only)
    else:
        raise gen.BundleError("choose a preset or at least one block")
    selection.accepted_terms.update(accepted_terms or {})
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
                   "the unprivileged steps; root actions stop before they run (they run through llmcf-install only "
                   "after a second yes for them).")
UNINSTALL_QUESTION = ("Uninstall bundle {bundle} from board {board}? The installer the board kept removes only the "
                      "bundle's own steps, newest first.")
ROLLBACK_QUESTION = ("Roll back the last run of bundle {bundle} on board {board}? The installer the board kept undoes "
                     "the steps of its last apply.")
# What changes a board, and which installer action shows it first without changing anything.
CHANGING_ACTIONS = {"apply": "plan", "uninstall": "plan-uninstall", "rollback": "status"}
# The person's question before llmcf-install changes the board as root ("No" is the default; the CLI and the Board
# bundle window ask exactly these words, after its own plan of that action). {count}: the root actions listed.
ROOT_QUESTION = ("Run the {count} root action(s) of bundle {bundle} on board {board} as root now, through "
                 "llmcf-install (the framework's signed root helper)? It does only what the signed manifest lists "
                 "above and keeps a journal for rollback and uninstall.")
ROOT_UNINSTALL_QUESTION = ("Remove what llmcf-install made for bundle {bundle} on board {board} (the {count} entries "
                           "listed above), as root? Only what its journal says it made is removed.")
ROOT_ROLLBACK_QUESTION = ("Roll back the last run of llmcf-install for bundle {bundle} on board {board}, as root? Its "
                          "journal says exactly what that run made or changed.")
ROOT_CHANGING_ACTIONS = {"apply": "plan", "uninstall": "plan-uninstall", "rollback": "status"}
# What a person is told when sudo on the board asks for a password: private mode may type it (sent once, never kept),
# enterprise mode runs root actions with sudo -n only.
PASSWORD_PROMPT = "The board's sudo asks for a password. Type the board account's password for this run (never kept)"
ENTERPRISE_NO_PASSWORD = ("The board's sudo asks for a password, and enterprise mode runs root actions only with sudo "
                          "-n (no password): allow the board account llmcf-install's run with sudo -n, then deploy "
                          "again. Nothing was changed.")


def password_allowed(mode: Any) -> bool:
    """Whether the person may type the board's sudo password for llmcf-install (private mode only)."""
    from orchestrator.remote.key_tiers import MODE_ENTERPRISE, normal_mode
    return normal_mode(mode) != MODE_ENTERPRISE


def helper_for(framework: Any, arch: Optional[str], say: Callable[[str], None] = lambda _m: None):
    """llmcf-install for the board's architecture (root_helper.ensure_helper): the cached one, else built with cargo
    on this computer or in the builder image over the framework's container engine; offline only the cached one
    (the one offline rule, utils/locality.offline_mode). gen.BundleError with the plain reason otherwise."""
    from orchestrator.runtime.bundle import root_helper
    from orchestrator.utils.locality import offline_mode
    config = getattr(framework, "config", None)
    try:
        from orchestrator.Core.runtime_layer import cache_dir_of
        cache = cache_dir_of(config)
    except Exception:  # noqa: BLE001 - no settings: the framework's default cache folder
        cache = root_helper.RUST_DIR.parent / "cache"
    client = getattr(getattr(getattr(framework, "docker_manager", None), "builder", None), "docker_client", None)
    try:
        return root_helper.ensure_helper(cache, arch, offline=bool(offline_mode(config)),
                                         build=root_helper.default_builder(client, log=say), log=say)
    except root_helper.HelperMissing as e:
        raise gen.BundleError(str(e)) from None


def build_for(plan: gen.BundlePlan, store: gen.BundleStore, signer: Any, *, framework: Any = None, ledger: Any = None,
              say: Callable[[str], None] = lambda _m: None) -> gen.BuiltBundle:
    """build_bundle for every channel: a bundle with root actions gets llmcf-install for its board first
    (helper_for), the others none."""
    helper = None
    if plan.ok and gen.needs_root(plan.manifest):
        helper = helper_for(framework, plan.manifest.board.arch, say)
    return gen.build_bundle(plan, store, signer, ledger=ledger, helper=helper)


def run_root_on_board(open_session: Callable[[], Any], built: gen.BuiltBundle, action: str, *, password: str = "",
                      say: Callable[[str], None] = lambda _m: None):
    """llmcf-install `action` on the board through the caller's login (board.run_root), the session closed in every
    case. Only the person's channels call this, after the person's yes."""
    from orchestrator.runtime.bundle.board import run_root
    from orchestrator.runtime.device.remote import RemoteShell
    session = open_session()
    try:
        return run_root(RemoteShell(session), built, action, password=password, say=say)
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


def root_question(action: str, built: gen.BuiltBundle, board: str, count: int) -> str:
    """The person's question before llmcf-install's `action` (apply, uninstall, rollback) changes the board."""
    text = {"apply": ROOT_QUESTION, "uninstall": ROOT_UNINSTALL_QUESTION, "rollback": ROOT_ROLLBACK_QUESTION}[action]
    return text.format(bundle=built.bundle_id, board=board, count=count)


def root_count(run: Any) -> int:
    """How many root actions (or journal entries) llmcf-install's plan listed."""
    return sum(1 for line in run.report.lines if line["state"] in ("would", "would-remove", "already", "pending"))


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


__all__ = ["CHANGING_ACTIONS", "COMPONENT_QUESTION", "DEPLOY_QUESTION", "ENTERPRISE_NO_PASSWORD", "PASSWORD_PROMPT",
           "ROLLBACK_QUESTION", "ROOT_CHANGING_ACTIONS", "ROOT_QUESTION", "ROOT_ROLLBACK_QUESTION",
           "ROOT_UNINSTALL_QUESTION", "UNINSTALL_QUESTION", "accept_terms", "assignments", "board_contact",
           "build_components", "build_for", "bundle_rows", "catalog_sources", "component_pin", "component_question",
           "device_profile", "helper_for", "needed_projects", "password_allowed", "plan_for_device", "question",
           "revoked_blocks", "root_count", "root_question", "run_on_board", "run_root_on_board", "runtime_for",
           "selection_from"]
