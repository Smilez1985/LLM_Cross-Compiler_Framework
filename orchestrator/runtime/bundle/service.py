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
                    index: Any = None, programs: Any = None, framework: Any = None,
                    unmeasured_ok: bool = False) -> gen.BundlePlan:
    """plan_bundle for a stored board: its probe, the block catalog of this framework (`index`: the verified cached
    community index, CommunityManager.cached_index(revocations=True), so a revoked block is not used), its runtime
    package. `programs`: the resolver of a block's program "from: framework"; None builds it for the person
    (framework_programs over `framework`'s container engine when cargo is not here)."""
    profile = profile or device_profile(state_dir, device_id)
    catalog = blk.load_catalog(app_root, index=index)
    projects = needed_projects(catalog, selection)
    if runtime is None and "llama.cpp" in projects:
        runtime = runtime_for(state_dir, device_id, profile)
    others = {p: runtime_for(state_dir, device_id, profile, project=p) for p in projects if p != "llama.cpp"}
    from orchestrator.runtime.device.steps import board_limits_of
    return gen.plan_bundle(selection, catalog=catalog, profile=profile, device_id=device_id,
                           runtime=runtime, config=config, mode=mode, allow_own=allow_own, runtimes=others,
                           programs=programs or framework_programs(config, client=engine_client(framework)),
                           limits=board_limits_of(state_dir, device_id), unmeasured_ok=unmeasured_ok)


KEPT_ONLY = ("{name} for the board's architecture is not built on this computer yet; the person's plan in the "
             "framework's Board bundle window builds it once (an AI's plan only reads)")


def engine_client(framework: Any) -> Any:
    """The framework's container engine client (docker-py) for the builder image, None without one."""
    return getattr(getattr(getattr(framework, "docker_manager", None), "builder", None), "docker_client", None)


def _cache_of(config: Any) -> Path:
    from orchestrator.runtime.bundle import root_helper
    try:
        from orchestrator.Core.runtime_layer import cache_dir_of
        return cache_dir_of(config)
    except Exception:  # noqa: BLE001 - no settings: the framework's default cache folder
        return root_helper.RUST_DIR.parent / "cache"


def framework_programs(config: Any = None, client: Any = None, say: Callable[[str], None] = lambda _m: None, *,
                       build: bool = True):
    """The resolver of a block's program "from: framework" (llmcf-gateway): the framework's own Rust program for the
    board's architecture, the cached one or built once now (root_helper.ensure_helper with its crate: cargo here or
    the builder image over `client`; offline only a kept one, through the one offline rule). `build` False (an AI's
    read-only plan): only a kept one, nothing is built, fetched or written (KEPT_ONLY otherwise). (path, sha256)."""
    from orchestrator.runtime.bundle import root_helper
    from orchestrator.utils.locality import offline_mode

    def resolve(name: str, arch: Optional[str]):
        cache = _cache_of(config)
        if not build:
            if name not in root_helper.FRAMEWORK_PROGRAMS:
                raise root_helper.HelperMissing(f"{name} is no program of the framework's Rust workspace")
            kept = root_helper.cached(cache, arch, crate=name)
            if kept is None:
                raise root_helper.HelperMissing(KEPT_ONLY.format(name=name))
            return kept.path, kept.sha256
        made = root_helper.ensure_helper(cache, arch, offline=bool(offline_mode(config)), crate=name,
                                         build=root_helper.default_builder(client, log=say, crate=name), log=say)
        return made.path, made.sha256
    return resolve


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
                 say: Callable[[str], None] = lambda _m: None, gateway: str = "complete"):
    """The installer `action` on the board through the caller's login (`open_session()` gives the board session),
    the session closed in every case (board.run_bundle; `gateway`: what the status checks of a gateway component,
    complete or bench)."""
    from orchestrator.runtime.bundle.board import run_bundle
    from orchestrator.runtime.device.remote import RemoteShell
    session = open_session()
    try:
        return run_bundle(RemoteShell(session), built, action, store=store, device_id=device_id, say=say,
                          gateway=gateway)
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
                          "-n (no password): set up the board's root readiness again (Prepare root in the Board bundle "
                          "window, or the CLI's bundle prepare-root), whose sudo rule lets the board account run "
                          "llmcf-install with sudo -n, then deploy again. Nothing was changed.")


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
    cache = _cache_of(config)
    client = engine_client(framework)
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


def _on_board(open_session: Callable[[], Any], step: Callable[[Any], Any]):
    """`step(shell)` over the caller's login (`open_session()`), the session closed in every case."""
    from orchestrator.runtime.device.remote import RemoteShell
    session = open_session()
    try:
        return step(RemoteShell(session))
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()


def readiness_on_board(open_session: Callable[[], Any], built: gen.BuiltBundle):
    """The board's root readiness for the bundle's llmcf-install (readiness.check, read without root): every channel
    asks this, the MCP tool bundle_deploy too (it only reads it)."""
    from orchestrator.runtime.bundle import readiness
    from orchestrator.runtime.bundle.board import helper_sha256
    sha = helper_sha256(built)
    return _on_board(open_session, lambda shell: readiness.check(shell.run, sha))


def ready_setup_on_board(open_session: Callable[[], Any], built: gen.BuiltBundle, found: Any, *, mode: Any,
                         password: str = ""):
    """readiness.setup on the bundle's board, only from the person's channels and after the person's yes (an AI
    never calls this: no tool module names it)."""
    from orchestrator.runtime.bundle import readiness
    from orchestrator.runtime.bundle.board import helper_sha256
    sha = helper_sha256(built)
    return _on_board(open_session, lambda shell: readiness.setup(
        lambda script, stdin=None, timeout=None: shell.run(script, stdin=stdin, timeout=timeout or 1800.0,
                                                           step="bundle"),
        built.bundle_id, sha, found, mode=mode, password=password))


def ready_remove_on_board(open_session: Callable[[], Any], found: Any, *, mode: Any, password: str = ""):
    """readiness.remove on a board, only from the person's channels and after the person's separate yes."""
    from orchestrator.runtime.bundle import readiness
    return _on_board(open_session, lambda shell: readiness.remove(
        lambda script, stdin=None: shell.run(script, stdin=stdin, step="bundle"), found, mode=mode,
        password=password))


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
                     "boards": {d: {"last": str((e or {}).get("last", "")), "at": str((e or {}).get("at", "")),
                                    "gateway": gateway_text((e or {}).get("gateway"))}
                                for d, e in sorted(store.boards(m.bundle_id).items())}})
    return rows


def gateway_text(found: Any) -> str:
    """The last gateway check of a board as a short label ("" when none ran): per gateway component its state, the
    model server, TLS with its pin, and the bench's tokens per second (fixed words and numbers only)."""
    if not isinstance(found, dict):
        return ""
    out = []
    for comp, facts in sorted(found.items()):
        if not isinstance(facts, dict):
            continue
        word = str(facts.get("state", "unknown"))
        parts = [word]
        if facts.get("upstream"):
            parts.append(f"model {facts['upstream']}")
        if facts.get("tls") == "yes":
            parts.append("TLS, pinned" if facts.get("pinned") else "TLS")
        if facts.get("tokens_per_s"):
            parts.append(f"{facts['tokens_per_s']} tokens/s")
        out.append(f"gateway {comp}: " + ", ".join(parts))
    return "; ".join(out)


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


__all__ = ["CHANGING_ACTIONS", "COMPONENT_QUESTION", "DEPLOY_QUESTION", "ENTERPRISE_NO_PASSWORD", "KEPT_ONLY",
           "PASSWORD_PROMPT", "ROLLBACK_QUESTION", "ROOT_CHANGING_ACTIONS", "ROOT_QUESTION",
           "ROOT_ROLLBACK_QUESTION", "ROOT_UNINSTALL_QUESTION", "UNINSTALL_QUESTION", "accept_terms", "assignments",
           "board_contact", "build_components", "build_for", "bundle_rows", "catalog_sources", "component_pin",
           "component_question", "device_profile", "engine_client", "framework_programs", "gateway_text",
           "helper_for", "needed_projects", "password_allowed", "plan_for_device", "question", "readiness_on_board",
           "ready_remove_on_board", "ready_setup_on_board", "revoked_blocks", "root_count", "root_question",
           "run_on_board", "run_root_on_board", "runtime_for", "selection_from"]
