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
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

Say = Callable[[str], None]


def utc_stamp() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- the plan ---------------------------------------------------------------------------------------------------------

def plan_runtime(profile: Any):
    """The cross build of llama.cpp planned from the board's own probe profile (a BuildPlan); PlanError when the board
    cannot get one (an old C library, not Linux, ...)."""
    from orchestrator.runtime.build.toolchain import host_platform
    from orchestrator.runtime.catalog import load_catalog
    from orchestrator.runtime.device.package import plan_device_runtime
    return plan_device_runtime(profile, catalog=load_catalog(), host=host_platform())


def plan_view(plan: Any, built: Any = None) -> Dict[str, Any]:
    """What `llmcf device runtime plan` shows of a plan, as data: the board, the builder container, the CPU options,
    the programs, the build ID, the notes and the stored build (`built`, a StoredBuild or None)."""
    target, builder, options = dict(plan.target), dict(plan.builder), plan.options()
    return {
        "target": {k: target.get(k) for k in ("os", "arch", "libc", "libc_version")},
        "builder": {k: builder.get(k) for k in ("base", "libc", "libc_version", "gcc")},
        "cpu_arm_arch": options.get("GGML_CPU_ARM_ARCH"),
        "cpu_all_variants": options.get("GGML_CPU_ALL_VARIANTS") == "ON",
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
            "deployed": record or None, "current": bool(record) and record.get("build_id") == plan.build_id}


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
                  reuse_package: bool = False) -> RuntimeBuild:
    """Builds the board's runtime as `llmcf device runtime build`: the pinned llama.cpp source from the cache, or
    fetched (from `mirror` when given) only after `may_fetch()` said yes; the cross build in the builder container
    without network (`jobs` parallel compile jobs; None: the CPUs, limited by the memory); then the tarball for the
    board. `on_step(kind, build)` tells the caller where it is: "fetch" and "fetched" around the download, "build"
    before the build, "stored" when the plan's build was stored already. `cancelled()` is asked between the steps
    (the build in the container itself runs to its end). With `reuse_package` a checked tarball of the build that is
    there already is used again."""
    from orchestrator.runtime.build.executor import BuildExecutor, DockerEngine
    from orchestrator.runtime.build.source import cached_source, fetch_source
    from orchestrator.runtime.build.store import BuildStore
    from orchestrator.runtime.build.toolchain import host_platform
    from orchestrator.runtime.build.verify import Verifier
    from orchestrator.runtime.device.package import DeviceRuntimeStore, package_runtime
    builds = BuildStore(state_dir)
    src = cached_source(source, state_dir)
    if src is None:
        if not may_fetch():
            raise SourceDeclined("the pinned llama.cpp source is not cached and fetching it was not allowed")
        on_step("fetch", None)
        src = fetch_source(source, state_dir, url=mirror, log=log)
        on_step("fetched", None)
    if cancelled():
        raise BuildCancelled("the build was cancelled")
    build = builds.find(plan)
    built_now = build is None
    if build is None:
        on_step("build", None)
        executor = BuildExecutor(engine=DockerEngine(), log=log)
        build = builds.build(plan, source=src, executor=executor, verifier=Verifier(), jobs=jobs,
                             perf_key=profile.perf_key(), host=host_platform())
    else:
        on_step("stored", build)
    if cancelled():
        raise BuildCancelled("the build was cancelled")
    devices = DeviceRuntimeStore(state_dir, device_id)
    packaged = devices.tarball(build) if reuse_package else None
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
    from orchestrator.runtime.device.package import DeviceRuntimeStore, package_runtime
    devices = DeviceRuntimeStore(state_dir, device_id)
    packaged = devices.tarball(build)
    if packaged is not None:
        return devices, packaged
    src = cached_source(source(), state_dir)
    if src is None:
        raise PackageNeedsSource("the tarball must be packaged again, which needs the pinned source")
    return devices, package_runtime(builds, build, licence=src / "LICENSE", out_dir=devices.root)


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
                    bench: Optional[BenchChoice] = None, say: Say = lambda _m: None) -> InstallOutcome:
    """Installs the packaged runtime on the board behind `shell` as the deploy settings say, checks the new build in
    its own folder (with the short llama-bench run when `bench` allows it) and activates it only when it passes
    (design 3.8, P6); then the deployment's record is saved. Every older build stays on the board (round 5 f)."""
    from orchestrator.runtime.device.package import DeviceRuntimeStore
    from orchestrator.runtime.device.remote import deploy_runtime, verify_on_device
    bench = bench or BenchChoice()

    def check_new_build(home: str, folder: str):
        return verify_on_device(shell, home=home, folder=folder, plan_verify=build.plan.get("verify") or {},
                                pin_build=source.build_number, pin_commit=source.commit, port=settings.port,
                                bench_model=bench.path, bench_allowed=bench.allowed)

    result = deploy_runtime(shell, tarball=packaged.path, tarball_sha256=packaged.sha256, build_id=build.build_id,
                            arch=arch, settings=settings, check=check_new_build, say=say, kv_cache=kv_cache)
    report = result.report
    if not result.activated or report is None or not report.ok:
        return InstallOutcome(result, report, False)
    now = utc_stamp()
    record = {"build_id": build.build_id, "deployed_at": now, "install": settings.install, "kv_cache": kv_cache,
              "ssh_user": settings.ssh_user, "previous": result.previous, "perf_key": profile.perf_key(),
              "verification": report.as_dict(), "verified_at": now, "unit_started": result.unit_started}
    DeviceRuntimeStore(state_dir, device_id).save_record(record)
    return InstallOutcome(result, report, True, record)


def deployed_verify_plan(state_dir: Path, record: Dict[str, Any], plan_of_profile: Callable[[], Any]) -> Dict:
    """The checks a deployed build is verified with: the ones of its stored build, else those of the plan the board's
    profile gives now (`plan_of_profile()`, called only then)."""
    from orchestrator.runtime.build.store import BuildStore
    stored = BuildStore(state_dir).get(record["build_id"])
    return (stored.plan.get("verify") if stored else None) or plan_of_profile().verify


def check_runtime(shell: Any, *, state_dir: Path, device_id: str, home: str, record: Dict[str, Any],
                  plan_verify: Dict[str, Any], source: Any, port: int,
                  bench: Optional[BenchChoice] = None):
    """`llmcf device runtime verify`: checks the active build on the board again as the unprivileged user (never with
    sudo), with the short llama-bench run when `bench` allows it, and keeps the result in the deployment's record.
    Returns the VerifyReport."""
    from orchestrator.runtime.device.package import DeviceRuntimeStore
    from orchestrator.runtime.device.remote import verify_on_device
    bench = bench or BenchChoice()
    report = verify_on_device(shell, home=home, plan_verify=plan_verify, pin_build=source.build_number,
                              pin_commit=source.commit, port=port, bench_model=bench.path,
                              bench_allowed=bench.allowed)
    record.update({"verification": report.as_dict(), "verified_at": utc_stamp()})
    record.pop("format", None)
    DeviceRuntimeStore(state_dir, device_id).save_record(record)
    return report


def check_lines(report: Any) -> List[Dict[str, Any]]:
    """The checks of a report as data (name, result, detail)."""
    return [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in (report.checks if report is not None else [])]


__all__ = ["BenchChoice", "BuildCancelled", "InstallOutcome", "PackageNeedsSource", "RuntimeBuild", "SourceDeclined",
           "build_runtime", "catalog_model_digests", "check_lines", "check_runtime", "choose_bench_model",
           "deployed_verify_plan", "ensure_package", "install_runtime", "older_package_count", "plan_runtime",
           "plan_view", "runtime_status", "utc_stamp"]
