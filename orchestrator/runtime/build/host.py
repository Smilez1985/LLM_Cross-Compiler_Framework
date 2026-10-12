#!/usr/bin/env python3
"""
Ditto's host runtime: the self-built llama-server on this machine (design 4.2, decision D1).

plan_host_runtime() turns this machine's hardware profile and toolchain into the builds it gets: the CPU build first
(the fallback, so Ditto works after a few minutes), then at most one GPU build when the recipe chooses one. The
backend comes from the recipe (recipe.choose_backend), which follows the OS, the architecture and the C library of the
machine; hardware.select_backend only chooses among the official prebuilt archives, for the explicit opt-in
`ditto runtime install --prebuilt`.

install_host_runtime() builds the planned builds through the executor and the build store. Every build is verified on
this machine before the store keeps it (version and commit of the pin, devices, the server's refusal of --tools); the
CPU build becomes the fallback and the active build, and a verified GPU build then becomes the active one. A GPU
build that cannot be built here yet, or that fails, leaves the CPU build active and is reported. A build ID that is
already stored is reused, never built again.

Placement: native when this machine has the toolchain (GCC or Clang, CMake, Ninja), otherwise the builder container
(Docker, no network in the build). Without either, the error names the ways out: install the toolchain (the framework
never installs one or accepts a licence, decision D5), start Docker, or build on another machine of the same kind and
import the build (`ditto runtime export` / `ditto runtime import`).
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from orchestrator.runtime.build.executor import BuildExecutor, ContainerEngine
from orchestrator.runtime.build.recipe import BACKENDS, BuildPlan, PlanError, ToolchainInfo, choose_backend, plan_build
from orchestrator.runtime.build.store import DEFAULT_KEEP, BuildFailed, BuildStore, StoredBuild, StoreError
from orchestrator.runtime.build.toolchain import DetectedToolchain
from orchestrator.runtime.build.verify import SmokeModelFile, Verifier
from orchestrator.runtime.catalog import LlamaCppSource, RuntimeCatalog
from orchestrator.runtime.profile import HardwareProfile

PURPOSE = "host-runtime"
PLACEMENTS = ("auto", "native", "container")
WAYS_OUT = ("install the missing tools with your package manager (on Debian or Ubuntu: build-essential cmake "
            "ninja-build), or start Docker to build in the framework's builder container, or build on another "
            "machine of the same kind and import it (llm-cli ditto runtime export there, llm-cli ditto runtime import "
            "here). `llm-cli ditto runtime install --prebuilt` installs the official archive instead (not self-built)")


class HostBuildError(RuntimeError):
    """The host runtime cannot be planned or built; the message says why and what to do. `log` names the build log."""

    def __init__(self, message: str, log: Optional[Path] = None):
        super().__init__(message)
        self.log = log


@dataclass(frozen=True)
class PlannedBuild:
    role: str                               # "cpu" (the fallback, built first) or "gpu"
    plan: BuildPlan
    stored: Optional[StoredBuild] = None    # exactly this build is in the store already: reused, not built again
    refusal: Optional[str] = None           # why it is not built on this machine yet

    @property
    def backend(self) -> str:
        return self.plan.backend

    @property
    def to_build(self) -> bool:
        return self.stored is None and self.refusal is None


@dataclass
class HostPlan:
    placement: str
    builds: List[PlannedBuild]
    toolchain: DetectedToolchain
    profile: HardwareProfile
    requested: str = "auto"
    skipped: List[str] = field(default_factory=list)   # backends that get no build here, with the reason
    notes: List[str] = field(default_factory=list)

    @property
    def cpu(self) -> PlannedBuild:
        return self.builds[0]

    @property
    def gpu(self) -> Optional[PlannedBuild]:
        return next((b for b in self.builds if b.role == "gpu"), None)

    @property
    def needs_source(self) -> bool:
        return any(b.to_build for b in self.builds)

    def describe(self) -> List[str]:
        """What the install will do, one line per build (for the confirmation)."""
        where = ("natively with " + self.toolchain.summary() if self.placement == "native"
                 else "in the builder container (Docker, no network during the build)")
        source = self.cpu.plan.source
        lines = [f"Build llama.cpp {source.get('tag')} (commit {str(source.get('commit', ''))[:12]}) {where}:"]
        for build in self.builds:
            what = "CPU build (the fallback, built first)" if build.role == "cpu" else f"{build.backend} build"
            if build.refusal:
                state = f"not built here yet: {build.refusal}"
            elif build.stored is not None:
                state = f"already built ({build.plan.build_id[:16]}), reused"
            else:
                state = f"to build ({build.plan.build_id[:16]})"
            lines.append(f"  {what}: {state}")
        lines += [f"  not built: {why}" for why in self.skipped]
        return lines


@dataclass
class HostInstallResult:
    active: StoredBuild
    fallback: StoredBuild
    built: List[str] = field(default_factory=list)      # build IDs built now
    reused: List[str] = field(default_factory=list)     # build IDs taken from the store
    problems: List[str] = field(default_factory=list)   # GPU builds not built or failed (the CPU build is active)
    removed: List[str] = field(default_factory=list)    # old builds removed by retention
    requested_missing: bool = False                     # an explicitly requested GPU backend is not active


def choose_placement(toolchain: DetectedToolchain, engine: Optional[ContainerEngine],
                     placement: str = "auto") -> str:
    """native when the toolchain is complete, else container when Docker answers; raises HostBuildError with the ways
    out when neither works (or the asked-for placement does not)."""
    if placement not in PLACEMENTS:
        raise HostBuildError(f"placement must be one of {', '.join(PLACEMENTS)}")
    if toolchain.info.host_os != "linux":
        raise HostBuildError(f"self-built llama.cpp servers on {toolchain.info.host_os} come with a later work "
                             "package; until then `llm-cli ditto runtime install --prebuilt` installs the official "
                             "archive (not self-built)")
    if placement in ("auto", "native") and toolchain.native_ready:
        return "native"
    if placement == "native":
        raise HostBuildError("no native build on this machine: " + "; ".join(toolchain.problems) +
                             ". Install the missing tools with your package manager, or build in the builder "
                             "container (--placement container)")
    docker = engine.unavailable() if engine is not None else "no container engine"
    if docker is None:
        return "container"
    if placement == "container":
        raise HostBuildError(f"no container build: {docker}. Start Docker, or build natively")
    raise HostBuildError("llama.cpp cannot be built on this machine: " + "; ".join(toolchain.problems) +
                         f", and {docker}. Ways out: {WAYS_OUT}")


def _planning_toolchain(placement: str, detected: DetectedToolchain) -> ToolchainInfo:
    """What the recipe gets: the detected toolchain of a native build; for a container build only the host (the
    container's own compiler and CMake build it, so the host's versions must not enter the build ID)."""
    if placement == "native":
        return detected.info
    return ToolchainInfo(host_os=detected.info.host_os, host_arch=detected.info.host_arch)


def plan_host_runtime(profile: HardwareProfile, toolchain: DetectedToolchain, catalog: RuntimeCatalog,
                      store: BuildStore, *, backend: str = "auto", placement: str = "auto",
                      engine: Optional[ContainerEngine] = None) -> HostPlan:
    """The builds this machine gets for Ditto: the CPU build, then the GPU build of `backend` (auto: the recipe's
    choice). Raises HostBuildError when not even the CPU build can be planned."""
    if catalog.llama_cpp.source is None:
        raise HostBuildError("configs/llama_runtime.yml has no llama_cpp.source: there is no pinned commit to build")
    if backend not in BACKENDS:
        raise HostBuildError(f"backend must be one of {', '.join(BACKENDS)}")
    host = (toolchain.info.host_os, toolchain.info.host_arch)
    if (profile.os, profile.arch) != host:
        raise HostBuildError(f"the hardware profile describes {profile.os}/{profile.arch}, but this machine is "
                             f"{host[0]}/{host[1]}: probe this machine (llm-cli scan)")
    where = choose_placement(toolchain, engine, placement)
    info = _planning_toolchain(where, toolchain)

    def make(name: str) -> BuildPlan:
        return plan_build(profile, purpose=PURPOSE, placement=where, backend=name, catalog=catalog, toolchain=info)

    try:
        cpu = make("cpu")
    except PlanError as e:
        raise HostBuildError(f"no build plan for this machine: {e}") from None
    notes = [n for n in cpu.notes if n]
    skipped: List[str] = []
    gpu_backend: Optional[str] = None
    if backend == "auto":
        try:
            chosen, reason = choose_backend(profile, purpose=PURPOSE, placement=where, catalog=catalog, toolchain=info)
        except PlanError as e:
            chosen, reason = "cpu", str(e)
        if chosen != "cpu":
            gpu_backend = chosen
            notes.append(f"GPU backend {chosen}: {reason}")
        elif reason:
            notes.append(f"no GPU build: {reason}")
    elif backend != "cpu":
        gpu_backend = backend

    builds = [_planned("cpu", cpu, store)]
    if gpu_backend:
        try:
            gpu = make(gpu_backend)
        except PlanError as e:
            skipped.append(f"{gpu_backend}: {e}")
        else:
            planned = _planned("gpu", gpu, store)
            if planned.refusal and gpu_backend == "cuda" and backend == "auto" and profile.flag("VULKAN") == "ON":
                planned = PlannedBuild(planned.role, planned.plan, planned.stored,
                                       planned.refusal + "; until then `--backend vulkan` builds a Vulkan server, "
                                       "which also runs on NVIDIA GPUs")
            builds.append(planned)
    return HostPlan(placement=where, builds=builds, toolchain=toolchain, profile=profile, requested=backend,
                    skipped=skipped, notes=notes)


def _planned(role: str, plan: BuildPlan, store: BuildStore) -> PlannedBuild:
    refusal = BuildExecutor.refusal(plan)
    stored = store.find(plan)
    if stored is not None and not stored.verified:
        stored = None
    return PlannedBuild(role=role, plan=plan, stored=stored, refusal=None if stored is not None else refusal)


def install_host_runtime(plan: HostPlan, *, store: BuildStore, source: Optional[Path], executor: BuildExecutor,
                         verifier: Verifier, smoke_model: Optional[SmokeModelFile] = None, jobs: Optional[int] = None,
                         keep_last: int = DEFAULT_KEEP, log: Optional[Callable[[str], None]] = None
                         ) -> HostInstallResult:
    """Builds (or reuses) the CPU build, verifies it and makes it the fallback and the active build; then the GPU build,
    which becomes the active build once it is verified. Old builds are removed by retention (never the active build,
    the fallback or the build a running server uses). Raises HostBuildError when the CPU build fails; nothing is
    activated then and the previous builds stay as they were."""
    say = log or (lambda _msg: None)
    host = (plan.toolchain.info.host_os, plan.toolchain.info.host_arch)
    perf_key = plan.profile.perf_key()
    built: List[str] = []
    reused: List[str] = []

    def build(planned: PlannedBuild) -> StoredBuild:
        if planned.stored is not None:
            reused.append(planned.stored.build_id)
            say(f"The {planned.backend} build {planned.stored.build_id[:16]} is already built and verified; reused.")
            return planned.stored
        if source is None:
            raise HostBuildError("the pinned llama.cpp source is not available")
        say(f"Building the {planned.backend} build {planned.plan.build_id[:16]} ({plan.placement}) ...")
        try:
            stored = store.build(planned.plan, source=source, executor=executor, verifier=verifier, jobs=jobs,
                                 toolchain=plan.toolchain, smoke_model=smoke_model, perf_key=perf_key, host=host)
        except BuildFailed as e:
            raise HostBuildError(f"the {planned.backend} build failed: {e}", log=e.log) from None
        except StoreError as e:
            raise HostBuildError(f"the {planned.backend} build could not be stored: {e}") from None
        built.append(stored.build_id)
        say(f"The {planned.backend} build {stored.build_id[:16]} passed its verification.")
        return stored

    cpu = plan.cpu
    if cpu.refusal:
        raise HostBuildError(cpu.refusal)
    fallback = build(cpu)
    try:
        store.activate(fallback.build_id, role="fallback")
        active = store.activate(fallback.build_id)
    except StoreError as e:
        raise HostBuildError(str(e)) from None
    problems = list(plan.skipped)
    gpu = plan.gpu
    if gpu is not None:
        if gpu.refusal:
            problems.append(f"{gpu.backend}: {gpu.refusal}")
        else:
            try:
                active = store.activate(build(gpu).build_id)
            except HostBuildError as e:
                where = f" (log: {e.log})" if e.log else ""
                problems.append(f"{e}{where}; the CPU build stays active")
            except StoreError as e:
                problems.append(f"{gpu.backend}: {e}; the CPU build stays active")
    removed = store.prune(keep_last=keep_last)
    missing = plan.requested not in ("auto", "cpu") and active.backend != plan.requested
    return HostInstallResult(active=active, fallback=fallback, built=built, reused=reused, problems=problems,
                             removed=removed, requested_missing=missing)


def rebuild_advice(build: StoredBuild, plan: Optional[HostPlan], pin: Optional[LlamaCppSource]) -> Optional[str]:
    """Whether the build in use should be built again (decision D16), with the reason; None when it is current."""
    if pin is not None and build.commit != pin.commit:
        return (f"required: the build is of llama.cpp commit {build.commit[:8]}, the pin is {pin.tag} "
                f"({pin.commit[:8]})")
    if plan is None:
        return None
    same = next((b for b in plan.builds if b.backend == build.backend), None)
    if same is None:
        return None
    if same.plan.build_id != build.build_id:
        return ("recommended: this machine's build plan changed since the build (CPU, compiler, CMake or options); "
                "llm-cli ditto runtime install builds it again")
    return None


def planned_backend(profile: HardwareProfile, toolchain: ToolchainInfo, catalog: RuntimeCatalog,
                    placement: str) -> Tuple[str, str]:
    """(backend, reason) the install would make active on this machine: the recipe's GPU backend when this executor
    builds it, else the CPU. For the model tier before anything is built."""
    try:
        chosen, reason = choose_backend(profile, purpose=PURPOSE, placement=placement, catalog=catalog,
                                        toolchain=toolchain)
    except PlanError as e:
        return "cpu", str(e)
    if chosen == "cpu":
        return "cpu", reason
    try:
        refusal = BuildExecutor.refusal(plan_build(profile, purpose=PURPOSE, placement=placement, backend=chosen,
                                                   catalog=catalog, toolchain=toolchain))
    except PlanError as e:
        return "cpu", f"{chosen}: {e}"
    if refusal:
        return "cpu", f"{chosen} would fit ({reason}), but {refusal}"
    return chosen, reason


__all__ = ["HostBuildError", "HostInstallResult", "HostPlan", "PlannedBuild", "choose_placement",
           "install_host_runtime", "plan_host_runtime", "planned_backend", "rebuild_advice"]
