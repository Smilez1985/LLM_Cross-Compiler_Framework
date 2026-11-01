#!/usr/bin/env python3
"""
The device runtime steps every caller shares (W1' of 2026-10-16: one code path for every channel): the CLI
(`llmcf device runtime plan|build|deploy|verify|status`), the GUI's Boards > Boards and Devices... window and the AI's
runtime tools (service.py) call these same functions.

Each step takes what its caller decided before (the plan the person was shown, the deploy settings, the model of the
short benchmark and whether an untrusted one may run) and returns facts. Nothing here prints, asks, trusts a host key,
logs in or pairs a board: the caller connects (with its own trust rules) and hands over the shell. The library errors
(PlanError, SourceError, BuildFailed, StoreError, PackageError, RemoteError, SettingsError) reach the caller as they
are, so every caller keeps its own words for them.

The deploy of the runtime is one flow for all three callers (deploy_runtime_flow, consolidation c3-deploy-flow): the
caller hands over its login, and, in the person's channels only, the board's trusted record and the benchmark's
question, as hooks (DeployHooks); the flow runs the steps in the one order.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

Say = Callable[[str], None]


def utc_stamp() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- the plan ---------------------------------------------------------------------------------------------------------

def plan_runtime(profile: Any, project: Optional[str] = None):
    """The cross build of llama.cpp planned from the board's own probe profile (a BuildPlan); PlanError when the board
    cannot get one (an old C library, not Linux, ...). `project` (runtime/build/projects.py; None: llama.cpp):
    whisper.cpp for the bundle's speech recognition, from the same probe through the same recipe."""
    from orchestrator.runtime.build.toolchain import host_platform
    from orchestrator.runtime.catalog import load_catalog
    from orchestrator.runtime.device.package import plan_device_runtime
    if project is None:
        return plan_device_runtime(profile, catalog=load_catalog(), host=host_platform())
    return plan_device_runtime(profile, catalog=load_catalog(), host=host_platform(), project=project)


def plan_view(plan: Any, built: Any = None) -> Dict[str, Any]:
    """What `llmcf device runtime plan` shows of a plan, as data: the board, the builder container, the CPU options,
    the programs, the build ID, the notes and the stored build (`built`, a StoredBuild or None)."""
    target, builder, options = dict(plan.target), dict(plan.builder), plan.options()
    return {
        "target": {k: target.get(k) for k in ("os", "arch", "libc", "libc_version")},
        "builder": {k: builder.get(k) for k in ("base", "libc", "libc_version", "gcc")},
        "cpu_arm_arch": options.get("GGML_CPU_ARM_ARCH"),
        "cpu_all_variants": options.get("GGML_CPU_ALL_VARIANTS") == "ON",
        # the probe's CPU features the build uses, and those the board has that it cannot use (with the reason)
        "cpu_features_used": list(plan.cpu_features.get("used") or []),
        "cpu_features_unusable": [dict(u) for u in plan.cpu_features.get("unusable") or []],
        "openmp": options.get("GGML_OPENMP"),
        "static_libstdcxx": bool(plan.verify.get("static_libstdcxx")),
        "programs": list(plan.targets),
        "runtime_libs": list(plan.verify.get("runtime_libs") or []),
        "build_id": plan.build_id,
        "notes": list(plan.notes),
        "built": built is not None,
        "built_path": str(built.path) if built is not None else None,
    }


def runtime_status(state_dir: Path, device_id: str, plan: Any) -> Dict[str, Any]:
    """`llmcf device runtime status` as data: the planned build and whether it is built, the record of the last
    deployment (when, how installed, the checks on the board, the KV cache) and whether it is still the planned one."""
    from orchestrator.runtime.build.store import BuildStore
    from orchestrator.runtime.device.package import DeviceRuntimeStore
    record = DeviceRuntimeStore(state_dir, device_id).record()
    built = BuildStore(state_dir).find(plan)
    return {"device_id": device_id, "planned_build_id": plan.build_id, "built": built is not None,
            "deployed": record or None, "current": bool(record) and record.get("build_id") == plan.build_id,
            "limits": measured_limits_facts(state_dir, device_id)}


def board_limits_of(state_dir: Path, device_id: str):
    """The board's measured limits with the KV-cache type of its deploy settings (measure.board_limits; None: the
    board was not measured, or its settings cannot be read)."""
    from orchestrator.runtime.device import measure
    from orchestrator.runtime.device.settings import DeploySettings, SettingsError
    from orchestrator.runtime.profile import ProfileStore
    try:
        kv = DeploySettings.from_dict(ProfileStore(state_dir).load_deploy_settings(device_id)).kv_cache
    except (SettingsError, ValueError, OSError):
        kv = None
    return measure.board_limits(state_dir, device_id, kv)


def kv_choice_of(settings: Any, profile: Any, state_dir: Path, device_id: str):
    """The board server's KV-cache type (DeploySettings.kv_choice) with the board's measured limits when it was
    measured: the one place the deploy (CLI, Boards window, the AI's deploy_device_runtime) takes it from."""
    limits = board_limits_of(state_dir, device_id)
    return settings.kv_choice(profile, limits=limits) if limits is not None else settings.kv_choice(profile)


def measured_limits_facts(state_dir: Path, device_id: str) -> Dict[str, Any]:
    """board_limits_of as numbers and words (measure.ai_facts): for the listings, the status and an AI."""
    from orchestrator.runtime.device import measure
    return measure.ai_facts(board_limits_of(state_dir, device_id))


# --- the build ----------------------------------------------------------------------------------------------------------

class SourceDeclined(RuntimeError):
    """The pinned source is not cached and the caller (the person) did not allow fetching it."""


class BuildCancelled(RuntimeError):
    """The person cancelled the build between two of its steps."""


@dataclass
class RuntimeBuild:
    builds: Any          # BuildStore
    build: Any           # StoredBuild
    packaged: Any        # PackagedRuntime
    source_dir: Path     # the verified source tree (its LICENSE travels in the tarball)
    built_now: bool      # False: the build of this plan was stored already


def build_runtime(state_dir: Path, device_id: str, profile: Any, plan: Any, source: Any, *,
                  jobs: Optional[int] = None, mirror: Optional[str] = None,
                  may_fetch: Callable[[], bool] = lambda: True,
                  on_step: Callable[[str, Any], None] = lambda _kind, _build: None,
                  log: Say = lambda _m: None, cancelled: Callable[[], bool] = lambda: False,
                  reuse_package: bool = False, settings: Any = None, ai_started: bool = False) -> RuntimeBuild:
    """Builds the board's runtime as `llmcf device runtime build`: the pinned llama.cpp source from the cache, or
    fetched (from `mirror` when given) only after `may_fetch()` said yes; the cross build in the builder container
    without network (`jobs` parallel compile jobs; None: the CPUs, limited by the memory); then the tarball for the
    board. `on_step(kind, build)` tells the caller where it is: "fetch" and "fetched" around the download, "build"
    before the build, "stored" when the plan's build was stored already. `cancelled()` is asked between the steps
    (the build in the container itself runs to its end). With `reuse_package` a checked tarball of the build that is
    there already is used again. `settings`: the framework's settings; in offline mode the source comes only from the
    person's own network (a local mirror), at its checked address (runtime/build/source.offline_reach).
    `ai_started`: a tool caller (an AI) started it, so the soft caps of the build's steps stop it
    (utils/step_limits.py, DECISIONS round 25 Q8); the idle timeout of its steps is the settings' build_idle_timeout,
    as for the stage containers."""
    from orchestrator.runtime.build.executor import BuildExecutor, DockerEngine
    from orchestrator.runtime.build.source import cached_source, fetch_source
    from orchestrator.runtime.build.store import BuildStore
    from orchestrator.runtime.build.toolchain import host_platform
    from orchestrator.runtime.build.verify import Verifier
    from orchestrator.runtime.build.projects import project_of
    from orchestrator.runtime.device.package import DeviceRuntimeStore, package_runtime
    # the plan names its project (llama.cpp, or whisper.cpp for the bundle's speech recognition): the same steps,
    # each project's store and packages in its own folder
    project = project_of(plan).name
    builds = BuildStore(state_dir, project)
    src = cached_source(source, state_dir)
    if src is None:
        if not may_fetch():
            raise SourceDeclined("the pinned llama.cpp source is not cached and fetching it was not allowed")
        on_step("fetch", None)
        src = fetch_source(source, state_dir, url=mirror, log=log,
                           **({"settings": settings} if settings is not None else {}))
        on_step("fetched", None)
    if cancelled():
        raise BuildCancelled("the build was cancelled")
    build = builds.find(plan)
    built_now = build is None
    if build is None:
        on_step("build", None)
        executor = BuildExecutor(engine=DockerEngine(), log=log, ai_started=ai_started, settings=settings)
        build = builds.build(plan, source=src, executor=executor, verifier=Verifier(), jobs=jobs,
                             perf_key=profile.perf_key(), host=host_platform())
    else:
        on_step("stored", build)
    if cancelled():
        raise BuildCancelled("the build was cancelled")
    devices = DeviceRuntimeStore(state_dir, device_id, project)
    packaged = devices.tarball(build) if reuse_package else None
    if packaged is not None and _stale_start_script(packaged):
        packaged = None   # made by an older framework: its start script lacks options added since (--mmproj)
    if packaged is None:
        packaged = package_runtime(builds, build, licence=src / "LICENSE", out_dir=devices.root)
    return RuntimeBuild(builds=builds, build=build, packaged=packaged, source_dir=src, built_now=built_now)


class PackageNeedsSource(RuntimeError):
    """The build's tarball is gone and packaging it again needs the pinned source (its licence text), which is not
    cached."""


def ensure_package(state_dir: Path, device_id: str, builds: Any, build: Any, source: Callable[[], Any]):
    """(DeviceRuntimeStore, packaged tarball) of a stored build: the checked tarball that is there, or packaged again
    from the cached pinned source (`source()` names the pin; asked only then). PackageNeedsSource when the source is
    not cached; PackageError when packaging fails."""
    from orchestrator.runtime.build.source import cached_source
    from orchestrator.runtime.build.projects import project_of
    from orchestrator.runtime.device.package import DeviceRuntimeStore, package_runtime
    project = project_of(build).name
    devices = DeviceRuntimeStore(state_dir, device_id, project) if project != "llama.cpp" else \
        DeviceRuntimeStore(state_dir, device_id)
    packaged = devices.tarball(build)
    if packaged is not None:
        if not _stale_start_script(packaged):
            return devices, packaged
        src = cached_source(source(), state_dir)
        if src is None:
            return devices, packaged   # the older start script still runs; packaged again once the source is there
        return devices, package_runtime(builds, build, licence=src / "LICENSE", out_dir=devices.root)
    src = cached_source(source(), state_dir)
    if src is None:
        raise PackageNeedsSource("the tarball must be packaged again, which needs the pinned source")
    return devices, package_runtime(builds, build, licence=src / "LICENSE", out_dir=devices.root)


def _stale_start_script(packaged: Any) -> bool:
    """A llama.cpp package whose start script is older than this framework's (package.start_script_current); a
    package of another project has none and is never stale for it."""
    from orchestrator.runtime.device.package import start_script_current
    return start_script_current(Path(packaged.path)) is False


def older_package_count(devices: Any, build: Any) -> int:
    """How many older runtime packages of the board are kept next to `build`'s (the framework deletes none by
    itself; `llmcf device runtime prune` lists them)."""
    plan = devices.cleanup_plan()
    return len([i for i in (plan.items if plan is not None else []) if i.file_name.endswith(".tar.gz")
                and build.build_id[:16] not in i.file_name])


# --- the short benchmark's model ------------------------------------------------------------------------------------

def catalog_model_digests() -> Set[str]:
    """The sha256 of every catalog model (and the smoke model): these run on a board without a question."""
    from orchestrator.runtime.catalog import load_catalog
    catalog = load_catalog()
    pinned = {m.sha256 for m in catalog.models if m.sha256}
    if catalog.smoke_model is not None:
        pinned.add(catalog.smoke_model.sha256)
    return pinned


@dataclass(frozen=True)
class BenchChoice:
    """The model of the short llama-bench run on the board: its path there, whether it may run, its sha256, and
    `missing` (the path) when no readable model is there (the benchmark is skipped then)."""
    path: Optional[str] = None
    allowed: bool = False
    digest: Optional[str] = None
    missing: Optional[str] = None


def choose_bench_model(shell: Any, home: str, bench_model: Optional[str], *,
                       may_run_untrusted: Callable[[str], bool],
                       record_consent: Callable[[str], None]) -> BenchChoice:
    """A catalog model (by its sha256) runs; any other model only when `may_run_untrusted(path)` says yes (the person's
    answer or flag), and only after `record_consent(sha256)` wrote that consent to the audit log (decision D23; it
    raises when it cannot, and nothing runs then). A relative path is relative to the board user's home `home`.
    SettingsError for a path that is not a plain model path."""
    from orchestrator.runtime.device.remote import model_sha256
    from orchestrator.runtime.device.settings import check_model_path
    if bench_model is None:
        return BenchChoice()
    path = check_model_path(bench_model if bench_model.startswith("/") else f"{home}/{bench_model}")
    digest = model_sha256(shell, path)
    if digest is None:
        return BenchChoice(missing=path)
    if digest in catalog_model_digests():
        return BenchChoice(path, True, digest)
    if not may_run_untrusted(path):
        return BenchChoice(path, False, digest)
    record_consent(digest)
    return BenchChoice(path, True, digest)


# --- installing and checking on the board -------------------------------------------------------------------------

@dataclass
class InstallOutcome:
    result: Any                    # remote.DeployResult
    report: Any                    # the checks of the new build on the board (VerifyReport) or None
    ok: bool                       # it passed and is the active build now; its record is saved
    record: Optional[Dict[str, Any]] = None


def install_runtime(shell: Any, *, state_dir: Path, device_id: str, profile: Any, settings: Any, build: Any,
                    packaged: Any, source: Any, arch: str, kv_cache: Optional[str],
                    bench: Optional[BenchChoice] = None, say: Say = lambda _m: None,
                    signer: Any = None) -> InstallOutcome:
    """Installs the packaged runtime on the board behind `shell` as the deploy settings say, checks the new build in
    its own folder (with the short llama-bench run when `bench` allows it) and activates it only when it passes
    (design 3.8, P6); then the deployment's record is saved. Every older build stays on the board (round 5 f)."""
    from orchestrator.runtime.device.package import DeviceRuntimeStore
    from orchestrator.runtime.device.remote import deploy_runtime, verify_on_device
    bench = bench or BenchChoice()

    def check_new_build(home: str, folder: str):
        return verify_on_device(shell, home=home, folder=folder, plan_verify=build.plan.get("verify") or {},
                                pin_build=source.build_number, pin_commit=source.commit, port=settings.port,
                                bench_model=bench.path, bench_allowed=bench.allowed,
                                measure=measure_request(settings, build.build_id, bench.digest))

    result = deploy_runtime(shell, tarball=packaged.path, tarball_sha256=packaged.sha256, build_id=build.build_id,
                            arch=arch, settings=settings, check=check_new_build, say=say, kv_cache=kv_cache,
                            signer=signer)
    report = result.report
    record_heat(state_dir, device_id, getattr(report, "heat", None))   # for Mojo, also when the checks failed
    record_key_tier(state_dir, device_id, report)
    record_measurement(state_dir, device_id, report)
    if not result.activated or report is None or not report.ok:
        return InstallOutcome(result, report, False)
    now = utc_stamp()
    record = {"build_id": build.build_id, "deployed_at": now, "install": settings.install, "kv_cache": kv_cache,
              "ssh_user": settings.ssh_user, "previous": result.previous, "perf_key": profile.perf_key(),
              "verification": report.as_dict(), "verified_at": now, "unit_started": result.unit_started}
    DeviceRuntimeStore(state_dir, device_id).save_record(record)
    return InstallOutcome(result, report, True, record)


# --- the deploy of the runtime: one flow for every caller ------------------------------------------------------------

class NotBuilt(RuntimeError):
    """The runtime of the board's plan is not built yet (`llmcf device runtime build` builds it)."""


class RootUser(RuntimeError):
    """The SSH user on the board is root: the runtime never runs as root (refused before anything is asked or changed;
    deploy_runtime refuses it again on its own)."""


@dataclass
class StoredRuntime:
    plan: Any        # the BuildPlan of the board's profile
    builds: Any      # BuildStore
    build: Any       # the stored build of that plan
    devices: Any     # the board's DeviceRuntimeStore
    packaged: Any    # its checked tarball


def stored_runtime(state_dir: Path, device_id: str, profile: Any, source: Callable[[], Any],
                   project: Optional[str] = None) -> StoredRuntime:
    """The stored build of the board's plan and its checked tarball, found before any contact with the board (`llmcf
    device runtime deploy` and the Boards window; the AI's deploy builds instead, service.ensure_build): PlanError when
    the board gets no plan, NotBuilt when the plan's build is not stored, PackageNeedsSource or PackageError when its
    tarball must be packaged again and cannot be (`source()` names the pin, asked only then). `project`
    (runtime/build/projects.py; None: llama.cpp): the board's whisper.cpp build of the bundle the same way."""
    from orchestrator.runtime.build.store import BuildStore
    plan = plan_runtime(profile) if project is None else plan_runtime(profile, project)
    builds = BuildStore(state_dir) if project is None else BuildStore(state_dir, project)
    build = builds.find(plan)
    if build is None:
        raise NotBuilt("the runtime for this device is not built yet")
    devices, packaged = ensure_package(state_dir, device_id, builds, build, source)
    return StoredRuntime(plan=plan, builds=builds, build=build, devices=devices, packaged=packaged)


@dataclass
class DeployHooks:
    """What the caller of deploy_runtime_flow decides; the flow itself trusts, pairs, asks and prints nothing.

    - `open_board()` logs in with the caller's own trust rules and returns (board session, the host key it presented):
      the person's channels through person_steps.connect_stored (a one-time password may pair the board once), the
      AI's service.deploy through service.connect (the trusted key only, KeyOnly credentials).
    - `remember(key)` writes the board's trusted record right after that login: the person's channels only (D7,
      decision 2; no AI-reachable code passes it, tests/test_deploy_flow.py checks service.py).
    - `choose_bench(shell, home)` the short benchmark's model (choose_bench_model with the person's consent in the
      audit log before an untrusted model runs); None: no benchmark (the AI's deploy).
    - `tested_on(report, digest, build_id, kv_cache)` records a passed deploy whose benchmark ran as "tested on" the
      board (N3) with the builds of its model and returns what it recorded; `digest` is None when the model did not
      run. None: nothing is recorded.
    - `progress(text)` gets the install's sentences; `check_cancel()` raises the caller's own cancel right before the
      install starts.
    - `refuse_root`: the board's facts are read first and an SSH user that is root ends the flow with RootUser before
      any question (the person's channels; the AI's deploy leaves that refusal to deploy_runtime, whose words reach the
      AI as the device's text, as before).
    - `signer`: the caller's framework signer (board_ops.resolve_signer: a BoardSigner, or
      `lambda: board_ops.framework_signer(framework)`, looked up once when the flow starts); None only for a caller
      without any framework: the flow then takes it from the secrets store in the state folder and says plainly
      through `progress` when that store cannot give it. The tarball is signed with it right before it is sent, and
      the root step of an unrestricted key installs its public key on the board."""
    open_board: Callable[[], Any]
    remember: Optional[Callable[[Any], Any]] = None
    choose_bench: Optional[Callable[[Any, str], "BenchChoice"]] = None
    tested_on: Optional[Callable[[Any, Optional[str], str, Optional[str]], Any]] = None
    progress: Say = lambda _m: None
    check_cancel: Callable[[], None] = lambda: None
    refuse_root: bool = True
    signer: Any = None


# What a deploy says when the board has no measurement after it (no benchmark ran): the start script does not start
# the server then, and the systemd unit stays stopped (launcher.MEASURE_EXIT, RestartPreventExitStatus).
NEEDS_MEASUREMENT = ("The board needs its measurement first: run the benchmark on the board once (it measures the "
                     "board's memory with the model). Until then its server is not started; a systemd unit stays "
                     "stopped instead of trying again every 10 seconds.")


@dataclass
class RuntimeDeploy:
    """What deploy_runtime_flow did: the install's outcome (the record is saved when it passed), the short benchmark's
    model, the KV-cache choice of the deploy settings, what `tested_on` returned (None: nothing recorded), and
    `needs_measurement`: a passed deploy to a board that has no measurement after it (NEEDS_MEASUREMENT)."""
    outcome: InstallOutcome
    bench: BenchChoice
    kv: Any
    tested: Any = None
    needs_measurement: bool = False


def deploy_runtime_flow(state_dir: Path, device_id: str, profile: Any, settings: Any, *, hooks: DeployHooks,
                        build: Any, packaged: Any, source: Any, arch: Optional[str] = None) -> RuntimeDeploy:
    """The deploy of the board's self-built runtime, one flow for `llmcf device runtime deploy`, the Boards window's
    Deploy runtime... and the AI's deploy_device_runtime (service.deploy): the login (`hooks.open_board`), the board's
    trusted record (`hooks.remember`, the person's channels only), the board's facts with the refusal of root and the
    short benchmark's model (`hooks.choose_bench`), the caller's cancel, then install_runtime (the new build is checked
    in its own folder and activated only when it passes; its record is saved), the session closed in every case, and
    the "tested on" record of a passed deploy (`hooks.tested_on`). `build` and `packaged` are the stored build and its
    tarball (stored_runtime, or the AI's service.ensure_build); `arch` the plan's architecture (None: the build's).
    RootUser, the board session's errors (RemoteError, the session's SessionFailure and Stopped) and whatever a hook
    raises reach the caller as they are, so every caller keeps its own words."""
    from orchestrator.runtime.device.remote import RemoteShell, read_facts
    kv = kv_choice_of(settings, profile, state_dir, device_id)
    if arch is None:
        arch = str((build.plan.get("target") or {}).get("arch") or profile.arch)
    from orchestrator.runtime.device import board_ops
    signer, missing = board_ops.resolve_signer(hooks.signer, state_dir)
    if missing == board_ops.NO_FRAMEWORK_STORE:   # said plainly, never a silent unsigned tarball
        from orchestrator.remote.gate import SETUP_REASONS
        hooks.progress(f"The runtime is sent unsigned: {SETUP_REASONS[missing]}.")
    session, key = hooks.open_board()
    try:
        if hooks.remember is not None:
            hooks.remember(key)
        shell = RemoteShell(session)
        bench = BenchChoice()
        if hooks.refuse_root or hooks.choose_bench is not None:
            facts = read_facts(shell)
            if hooks.refuse_root and facts.uid == 0:
                raise RootUser("the SSH user on the device is root: the runtime never runs as root")
            if hooks.choose_bench is not None:   # asked before anything changes on the board (decision D23)
                bench = hooks.choose_bench(shell, facts.home)
        hooks.check_cancel()
        outcome = install_runtime(shell, state_dir=state_dir, device_id=device_id, profile=profile, settings=settings,
                                  build=build, packaged=packaged, source=source, arch=arch, kv_cache=kv.kind,
                                  bench=bench, say=hooks.progress, signer=signer)
    finally:
        session.close()
    tested = None
    if outcome.ok and hooks.tested_on is not None:
        tested = hooks.tested_on(outcome.report, bench.digest if bench.allowed else None, build.build_id, kv.kind)
    needs = bool(outcome.ok) and board_limits_of(state_dir, device_id) is None
    return RuntimeDeploy(outcome=outcome, bench=bench, kv=kv, tested=tested, needs_measurement=needs)


def deployed_verify_plan(state_dir: Path, record: Dict[str, Any], plan_of_profile: Callable[[], Any]) -> Dict:
    """The checks a deployed build is verified with: the ones of its stored build, else those of the plan the board's
    profile gives now (`plan_of_profile()`, called only then)."""
    from orchestrator.runtime.build.store import BuildStore
    stored = BuildStore(state_dir).get(record["build_id"])
    return (stored.plan.get("verify") if stored else None) or plan_of_profile().verify


def check_runtime(shell: Any, *, state_dir: Path, device_id: str, home: str, record: Dict[str, Any],
                  plan_verify: Dict[str, Any], source: Any, port: int,
                  bench: Optional[BenchChoice] = None, kv_chosen: Optional[str] = None):
    """`llmcf device runtime verify`: checks the active build on the board again as the unprivileged user (never with
    sudo), with the short llama-bench run when `bench` allows it, and keeps the result in the deployment's record.
    Returns the VerifyReport."""
    from orchestrator.runtime.device.package import DeviceRuntimeStore
    from orchestrator.runtime.device.remote import verify_on_device
    bench = bench or BenchChoice()
    from orchestrator.runtime.device.measure import MeasureRequest
    report = verify_on_device(shell, home=home, plan_verify=plan_verify, pin_build=source.build_number,
                              pin_commit=source.commit, port=port, bench_model=bench.path,
                              bench_allowed=bench.allowed,
                              measure=MeasureRequest(kv_chosen, record["build_id"], bench.digest or ""))
    record.update({"verification": report.as_dict(), "verified_at": utc_stamp()})
    record.pop("format", None)
    DeviceRuntimeStore(state_dir, device_id).save_record(record)
    record_heat(state_dir, device_id, report.heat)
    record_key_tier(state_dir, device_id, report)
    record_measurement(state_dir, device_id, report)
    return report


def measure_request(settings: Any, build_id: str, digest: Optional[str]):
    """The board measurement of a benchmark with the deploy settings' KV-cache type (None: automatic)."""
    from orchestrator.runtime.device.measure import MeasureRequest
    return MeasureRequest(getattr(settings, "kv_cache", None), build_id, digest or "")


def record_measurement(state_dir: Path, device_id: str, report: Any) -> bool:
    """Keeps the board measurement of `report`'s check "measure" with the board (measure.save, next to its probe);
    the one recorder for the CLI, the windows and the AI's benchmark. False when there is none; never fails the
    caller's work."""
    from orchestrator.runtime.device import measure
    check = next((c for c in getattr(report, "checks", None) or () if getattr(c, "name", "") == "measure"), None)
    data = getattr(check, "data", None) if check is not None else None
    if not isinstance(data, dict) or not isinstance(data.get("record"), dict):
        return False
    try:
        measure.save(state_dir, device_id, measure.Measurement.from_dict(data["record"]))
        return True
    except Exception:  # noqa: BLE001 - bookkeeping never changes what the step does
        return False


def limits_facts(report: Any) -> Dict[str, Any]:
    """The board limits the measurement of `report` derived, numbers and words only (measure.BoardLimits.ai_facts),
    or {"measured": False, "reason": ...} when the board was not measured."""
    from orchestrator.runtime.device.measure import NOT_MEASURED
    check = next((c for c in getattr(report, "checks", None) or () if getattr(c, "name", "") == "measure"), None)
    data = getattr(check, "data", None) if check is not None else None
    if isinstance(data, dict) and isinstance(data.get("limits"), dict):
        return dict(data["limits"])
    return {"measured": False, "reason": getattr(check, "detail", "") or NOT_MEASURED}


def record_heat(state_dir: Path, device_id: str, heat: Any) -> None:
    """The board's temperature at these checks, kept for Mojo Oracle until the next reading (trusted.record_heat)."""
    from orchestrator.runtime.device.trusted import record_heat as record
    record(state_dir, device_id, heat)


def record_key_tier(state_dir: Path, device_id: str, report: Any) -> None:
    """The tier of the framework's key the board-key check of `report` read (remote/key_tiers.py), kept for the
    listings and Mojo Oracle until the next pairing or verify (trusted.record_key_tier); nothing without the check."""
    from orchestrator.runtime.device.trusted import record_key_tier as record
    check = next((c for c in getattr(report, "checks", None) or () if getattr(c, "name", "") == "board-key"), None)
    data = getattr(check, "data", None) if check is not None else None
    if isinstance(data, dict) and data.get("tier"):
        record(state_dir, device_id, data["tier"])


def heat_lines(report: Any, lang: Optional[str] = None) -> List[str]:
    """The person's lines about the board's temperature at the checks of `report` (thermal.person_lines): the
    temperature, and for a hot board the warning, what helps and the Mojo line; none without a reading."""
    from orchestrator.runtime.device.thermal import person_lines
    return person_lines(getattr(report, "heat", None), lang)


def heat_facts(report: Any) -> Dict[str, Any]:
    """The reading for an AI or the GUI's benchmark window: {"temperature": numbers, zone type, family} and, for a hot
    board, {"temperature_note": thermal.ai_text}: no path, no account; {} without a reading."""
    from orchestrator.runtime.device.thermal import ai_text
    heat = getattr(report, "heat", None)
    if heat is None:
        return {}
    note = ai_text(heat)
    return {"temperature": heat.as_dict(), **({"temperature_note": note} if note else {})}


def check_lines(report: Any) -> List[Dict[str, Any]]:
    """The checks of a report as data (name, result, detail)."""
    return [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in (report.checks if report is not None else [])]


__all__ = ["BenchChoice", "BuildCancelled", "DeployHooks", "InstallOutcome", "NEEDS_MEASUREMENT", "NotBuilt",
           "PackageNeedsSource",
           "RootUser", "RuntimeBuild", "RuntimeDeploy", "SourceDeclined", "StoredRuntime", "build_runtime",
           "catalog_model_digests", "check_lines", "check_runtime", "choose_bench_model", "deploy_runtime_flow",
           "deployed_verify_plan", "ensure_package", "heat_facts", "heat_lines", "install_runtime",
           "older_package_count", "plan_runtime", "plan_view", "record_heat", "runtime_status", "stored_runtime",
           "utc_stamp"]
