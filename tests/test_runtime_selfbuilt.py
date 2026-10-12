"""
Ditto on the self-built llama-server (WP8): `ditto runtime install` builds through the executor and the build store
(CPU first, then the GPU build), `--prebuilt` stays the explicit opt-in labelled "not self-built", builds of another
machine are imported and verified, start() runs only verified builds, retention never removes the running build, and
every launcher shares one launch policy (forbidden flags, cleared environment, loopback, key file).

No real build, no network and no Docker: a fake executor writes stand-in programs, a fake verifier passes or fails,
the toolchain and the container engine are fakes, and the source lookup is replaced.
"""

import hashlib
import json
import os
import stat
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from orchestrator.runtime import llama_server as ls
from orchestrator.runtime import server_policy
from orchestrator.runtime.build import host as host_module
from orchestrator.runtime.build import source as source_module
from orchestrator.runtime.build.executor import BuildError, BuildExecutor, BuildResult, ContainerEngine
from orchestrator.runtime.build.recipe import ToolchainInfo, plan_build
from orchestrator.runtime.build.store import BuildStore
from orchestrator.runtime.build.toolchain import DetectedToolchain
from orchestrator.runtime.build.verify import Check, VerifyReport
from orchestrator.runtime.catalog import load_catalog
from orchestrator.runtime.hardware import GIB, GpuInfo, HardwareInfo, backend_choice, select_backend
from orchestrator.runtime.llama_server import PREBUILT_LABEL, LocalLLMRuntime, LocalRuntimeError
from orchestrator.runtime.profile import read_profile

ROOT = Path(__file__).resolve().parents[1]
PROBES = ROOT / "tests" / "fixtures" / "hardware_probe"
CATALOG = load_catalog()
PIN = CATALOG.llama_cpp.source
PC = read_profile(PROBES / "schema2_pc_two_nvidia.txt")            # two NVIDIA GPUs, glibc 2.39
Z1 = read_profile(PROBES / "schema2_linux_z1_extreme.txt")         # AMD Radeon 780M APU, Vulkan, glibc 2.41
IVY = read_profile(PROBES / "schema2_linux_ivybridge.txt")         # Intel iGPU, no Vulkan, glibc 2.31
GCC = ToolchainInfo(host_os="linux", host_arch="x86_64", compiler="gcc-13.3.0", cmake="3.28.3", openmp=True,
                    native_expansion="march=skylake mtune=skylake features=0123456789abcdef")
POSIX_ONLY = pytest.mark.skipif(os.name != "posix", reason="shebang scripts and process groups are POSIX")


def toolchain(native=True, info=GCC):
    problems = () if native else ("CMake was not found (install cmake, 3.15 or newer)",
                                  "no C and C++ compiler found (install build-essential, or gcc and g++)")
    return DetectedToolchain(info=info if native else ToolchainInfo(host_os="linux", host_arch="x86_64"),
                             tools={"cmake": "/usr/bin/cmake", "ninja": "/usr/bin/ninja"} if native else {},
                             cpus=4, ram_available_mib=8192, problems=problems)


class Engine(ContainerEngine):
    def __init__(self, reason="the Docker daemon is not reachable (FileNotFoundError)"):
        self.reason = reason

    def unavailable(self):
        return self.reason


SERVER = r'''
import json, os, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
here = Path(__file__).resolve().parent
args = sys.argv[1:]
opt = lambda name: args[args.index(name) + 1] if name in args else None
(here.parent / "argv.json").write_text(json.dumps(args))
(here.parent / "env.json").write_text(json.dumps(sorted(os.environ)))
keys = [l.strip() for l in open(opt("--api-key-file")) if l.strip()]
class H(BaseHTTPRequestHandler):
    def reply(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        if self.path == "/health":
            return self.reply(200, {"status": "ok"})
        if self.headers.get("Authorization", "").removeprefix("Bearer ") not in keys:
            return self.reply(401, {})
        return self.reply(200, {"data": [{"id": opt("--alias")}]})
    def log_message(self, *a):
        pass
ThreadingHTTPServer((opt("--host"), int(opt("--port"))), H).serve_forever()
'''


class FakeExecutor:
    """Writes stand-in programs for the plan's targets (llama-server is a small HTTP server); records the builds."""

    def __init__(self, fail=()):
        self.fail = set(fail)
        self.built = []

    def build(self, plan, *, source, work, jobs, toolchain=None, ccache_dir=None):
        self.built.append(plan.backend)
        dirs = BuildExecutor._prepare(work)
        (dirs["logs"] / "build.log").write_text("built\n")
        if plan.backend in self.fail:
            raise BuildError(f"{plan.backend} build failed (exit 2)", dirs["logs"] / "build.log")
        python = sys.executable if len(sys.executable) < 120 else "/usr/bin/env python3"
        files = {}
        for name in plan.targets:
            text = f"#!{python}\n{SERVER}" if name == "llama-server" else f"#!/bin/sh\necho {name}\n"
            (dirs["bin"] / name).write_text(text)
            (dirs["bin"] / name).chmod(0o755)
            files[name] = hashlib.sha256((dirs["bin"] / name).read_bytes()).hexdigest()
        return BuildResult(build_id=plan.build_id, work=Path(work), bin_dir=dirs["bin"], files=files, links={},
                           duration_s=42.0, jobs=jobs or 1, placement=plan.placement)


class FakeVerifier:
    def __init__(self, ok=True, ran=True):
        self.ok, self.ran, self.calls = ok, ran, 0

    def verify(self, plan, bin_dir, **kw):
        self.calls += 1
        data = plan.as_dict() if hasattr(plan, "as_dict") else plan
        devices = [{"name": d, "description": "GPU", "total_mib": 4096} for d in (data.get("verify") or {}).get(
            "devices", [])]
        return VerifyReport([Check("format", True), Check("version", self.ok, "pin"),
                             Check("devices", True, "", {"devices": devices})], ran_binaries=self.ran)


def machine(gpus=(), ram_gib=16.0):
    return HardwareInfo(os="linux", arch="x86_64", ram_total=int(ram_gib * GIB), ram_available=int(ram_gib * 0.7 * GIB),
                        gpus=list(gpus), vulkan=True, libc="glibc", libc_version="2.39")


APU = GpuInfo(vendor="amd", name="AMD Radeon 780M Graphics", discrete=False, unified=True, vram_mib=512)


@pytest.fixture
def no_source(monkeypatch, tmp_path):
    """The pinned source as a plain folder (the fake executor never reads it); counts the lookups."""
    calls = []
    folder = tmp_path / "llama-src"
    folder.mkdir()
    monkeypatch.setattr(source_module, "cached_source", lambda pin, state, **kw: calls.append(state) or folder)
    return calls


def runtime(tmp_path, profile=Z1, native=True, engine=None, executor=None, verifier=None, hw=None, **kw):
    return LocalLLMRuntime(data_dir=tmp_path / "state", models_dir=tmp_path / "models" / "ditto", catalog=CATALOG,
                           hardware=hw or machine([APU]), profile=profile, toolchain=toolchain(native),
                           engine=engine or Engine(), executor=executor or FakeExecutor(),
                           verifier=verifier or FakeVerifier(), platform=("linux", "x86_64"), **kw)


# --- install --------------------------------------------------------------------------------------------------------

class TestInstall:

    def test_cpu_first_then_the_gpu_build_of_the_recipe(self, tmp_path, no_source):
        ex = FakeExecutor()
        rt = runtime(tmp_path, executor=ex)
        plan = rt.plan_install()
        assert [b.backend for b in plan.builds] == ["cpu", "vulkan"] and plan.placement == "native"
        assert all(b.to_build for b in plan.builds) and plan.needs_source
        result = rt.install(plan)
        assert ex.built == ["cpu", "vulkan"], "the CPU build comes first"
        assert result.fallback.backend == "cpu" and result.active.backend == "vulkan"
        assert sorted(result.built) == sorted([result.fallback.build_id, result.active.build_id])
        active = json.loads(rt.active_file.read_text())
        assert active["build_id"] == result.active.build_id and active["fallback_build_id"] == result.fallback.build_id
        assert active["use"] == "self-built"
        inst = rt.installed()
        assert inst.origin == "self-built" and inst.label == "self-built" and inst.backend == "vulkan"
        assert inst.build_id == result.active.build_id and inst.commit == PIN.commit and inst.devices == ["Vulkan0"]
        assert inst.server == result.active.bin_dir / "llama-server" and inst.role == "active"
        assert not list((rt.root).glob("b11429-*")), "nothing was downloaded"

    def test_install_from_a_filled_store_builds_nothing(self, tmp_path, monkeypatch, no_source):
        rt = runtime(tmp_path)
        rt.install()
        lookups = len(no_source)

        class Refuses(FakeExecutor):
            def build(self, plan, **kw):
                raise AssertionError("a stored build is never built again")

        again = runtime(tmp_path, executor=Refuses())
        plan = again.plan_install()
        assert all(b.stored is not None for b in plan.builds) and not plan.needs_source
        result = again.install(plan)
        assert result.built == [] and len(result.reused) == 2 and len(no_source) == lookups, "no source needed"
        assert again.installed().backend == "vulkan"

    def test_a_failed_gpu_build_leaves_the_cpu_build_active(self, tmp_path, no_source):
        rt = runtime(tmp_path, executor=FakeExecutor(fail={"vulkan"}))
        result = rt.install()
        assert result.active.backend == "cpu" and result.active.build_id == result.fallback.build_id
        assert any("vulkan build failed" in p and "CPU build stays active" in p for p in result.problems)
        assert rt.installed().backend == "cpu"

    def test_a_failed_cpu_build_activates_nothing(self, tmp_path, no_source):
        rt = runtime(tmp_path, executor=FakeExecutor(fail={"cpu"}))
        with pytest.raises(LocalRuntimeError, match="cpu build failed"):
            rt.install()
        assert rt.store.references() == {} and rt.installed() is None

    def test_cuda_waits_for_its_work_package_and_the_cpu_build_runs(self, tmp_path, no_source):
        ex = FakeExecutor()
        rt = runtime(tmp_path, profile=PC, executor=ex, hw=machine([]))
        plan = rt.plan_install()
        gpu = plan.gpu
        assert gpu.backend == "cuda" and gpu.refusal and "toolkit provisioning" in gpu.refusal
        assert "--backend vulkan" in gpu.refusal, "NVIDIA machines with Vulkan get the hint"
        result = rt.install(plan)
        assert ex.built == ["cpu"] and result.active.backend == "cpu"
        assert any(p.startswith("cuda:") for p in result.problems) and not result.requested_missing
        explicit = runtime(tmp_path / "b", profile=PC, hw=machine([])).install(backend="cuda")
        assert explicit.requested_missing, "an explicitly requested backend that is not active is reported"

    def test_cpu_only(self, tmp_path, no_source):
        ex = FakeExecutor()
        rt = runtime(tmp_path, executor=ex)
        assert [b.backend for b in rt.plan_install(backend="cpu").builds] == ["cpu"]
        rt.install(backend="cpu")
        assert ex.built == ["cpu"]

    def test_without_toolchain_and_docker_the_ways_out_are_named(self, tmp_path):
        rt = runtime(tmp_path, native=False)
        with pytest.raises(LocalRuntimeError) as e:
            rt.plan_install()
        text = str(e.value)
        for part in ("CMake was not found", "Docker daemon is not reachable", "ditto runtime import", "--prebuilt",
                     "not self-built"):
            assert part in text, part

    def test_the_builder_container_when_there_is_no_compiler(self, tmp_path):
        rt = runtime(tmp_path, native=False, engine=Engine(reason=None))
        plan = rt.plan_install()
        assert plan.placement == "container" and plan.cpu.plan.builder["kind"] == "container"
        assert "compiler" not in plan.cpu.plan.toolchain, "the host's compiler is not the container's"

    def test_a_missing_source_is_fetched_only_with_consent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(source_module, "cached_source", lambda pin, state, **kw: None)
        fetched = []
        monkeypatch.setattr(source_module, "fetch_source", lambda pin, state, **kw: fetched.append(1))
        rt = runtime(tmp_path)
        with pytest.raises(LocalRuntimeError, match="import-source"):
            rt.install(fetch_source=lambda: False)
        assert fetched == [] and rt.installed() is None

    def test_a_profile_of_another_machine_is_refused(self, tmp_path):
        rk = read_profile(PROBES / "schema2_rk3566_dietpi.txt")
        with pytest.raises(LocalRuntimeError, match="probe this machine"):
            runtime(tmp_path, profile=rk).plan_install()

    def test_the_recipe_chooses_the_backend_not_the_prebuilt_catalog(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ls, "select_backend", lambda *a, **k: pytest.fail("select_backend is the prebuilt opt-in"))
        assert runtime(tmp_path, profile=Z1).recommend_backend()[0] == "vulkan"
        assert runtime(tmp_path, profile=IVY, hw=machine([])).recommend_backend()[0] == "cpu"
        backend, why = runtime(tmp_path, profile=PC, hw=machine([])).recommend_backend()
        assert backend == "cpu" and "cuda" in why, "CUDA would fit, but is not built here yet"


# --- start ----------------------------------------------------------------------------------------------------------

def _stored(rt, verifier=None, backend="cpu", profile=Z1):
    plan = plan_build(profile, purpose="host-runtime", placement="native", backend=backend, catalog=CATALOG,
                      toolchain=GCC)
    return rt.store.build(plan, source=rt.root, executor=FakeExecutor(), verifier=verifier or FakeVerifier(),
                          host=("linux", "x86_64"))


class TestStartOnlyVerified:

    def _refused(self, rt, match):
        started = []
        rt._popen = lambda *a, **k: started.append(a) or pytest.fail("an unverified build was started")
        with pytest.raises(LocalRuntimeError, match=match):
            rt.start(tier="small")
        assert started == []

    def test_an_unverified_build_is_never_started(self, tmp_path):
        rt = runtime(tmp_path)
        build = _stored(rt, verifier=FakeVerifier(ran=False))  # a format check only (as for a cross build)
        with pytest.raises(Exception, match="verification"):
            rt.store.activate(build.build_id)
        rt._update_active(build_id=build.build_id, use="self-built")  # written by hand: still refused
        self._refused(rt, "has not passed its verification on this machine")

    def test_a_changed_program_is_never_started(self, tmp_path):
        rt = runtime(tmp_path)
        build = _stored(rt)
        rt.store.activate(build.build_id)
        (build.bin_dir / "llama-server").write_text("#!/bin/sh\necho changed\n")
        self._refused(rt, "does not match its manifest")

    def test_a_build_of_another_commit_is_never_started(self, tmp_path):
        rt = runtime(tmp_path)
        build = _stored(rt)
        rt.store.activate(build.build_id)
        data = json.loads((build.path / "build.json").read_text())
        data["plan"]["source"]["commit"] = "f" * 40
        (build.path / "build.json").write_text(json.dumps(data))
        self._refused(rt, "is of llama.cpp commit ffffffff")

    def test_the_cpu_fallback_is_used_when_the_active_build_cannot_be(self, tmp_path):
        rt = runtime(tmp_path)
        cpu = _stored(rt)
        gpu = _stored(rt, backend="vulkan")
        rt.store.activate(cpu.build_id, role="fallback")
        rt.store.activate(gpu.build_id)
        (gpu.bin_dir / "llama-bench").unlink()
        inst, problems = rt.resolve_installed()
        assert inst.build_id == cpu.build_id and inst.role == "fallback" and "missing" in problems[0]
        assert "the CPU fallback build is used" in inst.notes

    def test_never_falls_back_to_the_prebuilt_archive_on_its_own(self, tmp_path):
        rt = runtime(tmp_path)
        folder = rt.root / f"{rt.build}-cpu"
        folder.mkdir(parents=True)
        (folder / "llama-server").write_text("#!/bin/sh\n")
        (folder / "runtime.json").write_text(json.dumps({"build": rt.build, "asset_id": "linux-x64-cpu",
                                                         "variant": "cpu", "backend": "cpu",
                                                         "server": "llama-server", "files": {}}))
        rt._update_active(variant="cpu", asset_id="linux-x64-cpu", use="prebuilt")  # what `install --prebuilt` writes
        opted_in = rt.installed()
        assert opted_in.origin == "prebuilt" and opted_in.label == PREBUILT_LABEL
        build = _stored(rt)
        rt.store.activate(build.build_id)
        rt._update_active(use="self-built")
        (build.bin_dir / "llama-server").unlink()
        inst, problems = rt.resolve_installed()
        assert inst is None and problems, "a broken self-built build never switches to the prebuilt archive"
        rt._update_active(use="prebuilt")  # what `install --prebuilt` writes
        assert rt.installed().origin == "prebuilt"


@POSIX_ONLY
class TestServerFromTheStore:

    def _ready(self, tmp_path, monkeypatch, no_source):
        rt = runtime(tmp_path, hw=machine([], ram_gib=16))
        rt.install(backend="cpu")
        model = rt.catalog.model("small")
        rt.models_dir.mkdir(parents=True, exist_ok=True)
        rt.model_path(model).write_bytes(b"GGUF")
        monkeypatch.setattr(rt, "_ensure_model_verified", lambda m: rt.model_path(m))
        return rt

    def test_start_uses_the_shared_policy_and_a_cleared_environment(self, tmp_path, monkeypatch, no_source):
        monkeypatch.setenv("LLAMA_ARG_TOOLS", "all")
        monkeypatch.setenv("LLAMA_API_KEY", "x")
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid")
        checked = []
        real = server_policy.check_server_command
        monkeypatch.setattr(ls, "check_server_command", lambda cmd: checked.append(list(cmd)) or real(cmd))
        rt = self._ready(tmp_path, monkeypatch, no_source)
        try:
            ep = rt.start(tier="small")
            build = rt.installed()
            argv = json.loads((build.path / "argv.json").read_text())
            env = json.loads((build.path / "env.json").read_text())
            assert checked and checked[0][0] == str(build.server)
            assert argv[argv.index("--host") + 1] == "127.0.0.1" and server_policy.forbidden_in(["x", *argv]) == []
            key = Path(argv[argv.index("--api-key-file") + 1])
            assert key.parent == rt.root and stat.S_IMODE(key.stat().st_mode) == 0o600
            assert ep.api_key not in " ".join(argv)
            for name in ("LLAMA_ARG_TOOLS", "LLAMA_API_KEY", "HTTPS_PROXY"):
                assert name not in env, name
            state = json.loads(rt.state_file.read_text())
            assert state["origin"] == "self-built" and state["build_id"] == build.build_id
        finally:
            rt.stop(timeout=5)

    def test_prune_never_deletes_the_running_build(self, tmp_path, monkeypatch, no_source):
        rt = self._ready(tmp_path, monkeypatch, no_source)
        try:
            rt.start(tier="small")
            running = rt.installed()
            # A newer build becomes active (another toolchain version gives another build ID).
            other = runtime(tmp_path, hw=machine([]))
            other._toolchain = toolchain(info=replace(GCC, cmake="3.31.0"))
            other.install(backend="cpu")
            assert other.installed().build_id != running.build_id
            assert rt.store.running_build_ids() == {running.build_id}
            assert running.build_id not in rt.prune(keep_last=0)
            assert running.path.is_dir() and running.server.is_file(), "the running server's build stays"
        finally:
            rt.stop(timeout=5)
        assert running.build_id in rt.prune(keep_last=0), "once it stopped, retention may remove it"


class TestTheStoreIsSharedWithTheDevices:
    """The device runtime (llm-cli device runtime build) keeps its cross builds in the same build store: one recipe,
    one executor, one store. Ditto's retention keeps to Ditto's own builds."""

    def _device_builds(self, store, tmp_path):
        """Three boards with three different cross builds: an RK3566 on DietPi (glibc), an Alpine board (musl), and
        an RK3566 whose probe reports no dotprod (plain armv8-a)."""
        from orchestrator.runtime.device.package import plan_device_runtime
        from orchestrator.runtime.profile import parse_profile
        dietpi = (PROBES / "schema2_rk3566_dietpi.txt").read_text(encoding="utf-8")
        boards = [parse_profile(dietpi), read_profile(PROBES / "schema2_rk3566_alpine_musl.txt"),
                  parse_profile(dietpi.replace("SUPPORTS_DOTPROD=ON", "SUPPORTS_DOTPROD=OFF"))]
        builds = []
        for profile in boards:
            plan = plan_device_runtime(profile, catalog=CATALOG, host=("linux", "x86_64"))
            builds.append(store.build(plan, source=tmp_path, executor=FakeExecutor(), verifier=FakeVerifier(ran=False),
                                      jobs=1, host=("linux", "x86_64")))
        assert len({b.build_id for b in builds}) == 3 and all(b.purpose == "device-runtime" for b in builds)
        return builds

    def test_installing_dittos_runtime_keeps_every_device_build(self, tmp_path, no_source):
        rt = runtime(tmp_path)
        devices = self._device_builds(rt.store, tmp_path)
        result = rt.install()  # two host builds (CPU and Vulkan), then retention with its default of 2 others
        assert result.removed == []
        assert all(rt.store.get(b.build_id) is not None for b in devices), "a device's build stays for its deployment"

    def test_dittos_prune_removes_only_dittos_builds(self, tmp_path, no_source):
        rt = runtime(tmp_path)
        devices = self._device_builds(rt.store, tmp_path)
        first = rt.install(backend="cpu").active
        other = runtime(tmp_path, hw=machine([]))
        other._toolchain = toolchain(info=replace(GCC, cmake="3.31.0"))
        second = other.install(backend="cpu").active
        assert first.build_id != second.build_id
        removed = rt.prune(keep_last=0)
        assert removed == [first.build_id], "the old host build goes; the active one and the device builds stay"
        assert all(rt.store.get(b.build_id) is not None for b in devices)


# --- import ---------------------------------------------------------------------------------------------------------

class TestImport:

    def test_a_build_of_another_machine_is_checked_confirmed_and_verified(self, tmp_path, no_source):
        made = runtime(tmp_path / "pc")
        made.install(backend="cpu")
        archive = made.export_build(tmp_path / "llama-build.tar.gz")
        verifier = FakeVerifier()
        laptop = runtime(tmp_path / "laptop", verifier=verifier)
        with pytest.raises(LocalRuntimeError, match="not confirmed"):
            laptop.import_build(archive, confirm=lambda info: False)
        assert verifier.calls == 0 and laptop.installed() is None, "nothing runs before the user confirms"
        asked = []
        inst = laptop.import_build(archive, confirm=lambda info: asked.append(info) or True)
        assert asked[0]["commit"] == PIN.commit and "llama-server" in asked[0]["files"]
        assert inst.origin == "imported" and "imported" in inst.label and verifier.calls == 1
        refs = laptop.store.references()
        assert refs["active"] == refs["fallback"] == inst.build_id

    def test_a_build_without_server_is_refused(self, tmp_path):
        store = BuildStore(tmp_path / "pc")
        plan = plan_build(Z1, purpose="host-tools", placement="container", backend="cpu", catalog=CATALOG,
                          toolchain=ToolchainInfo(host_os="linux", host_arch="x86_64",
                                                  base_image="llm-framework/rockchip:1"))
        built = store.build(plan, source=tmp_path, executor=FakeExecutor(), verifier=FakeVerifier(),
                            host=("linux", "x86_64"))
        archive = store.export_build(built.build_id, tmp_path / "tools.tar.gz")
        with pytest.raises(LocalRuntimeError, match="no llama-server"):
            runtime(tmp_path / "laptop").import_build(archive, confirm=lambda info: True)

    def test_export_needs_a_self_built_build(self, tmp_path):
        with pytest.raises(LocalRuntimeError, match="no self-built build"):
            runtime(tmp_path).export_build(tmp_path / "x.tar.gz")


# --- the prebuilt opt-in --------------------------------------------------------------------------------------------

class TestPrebuiltOptIn:

    def nvidia(self):
        return GpuInfo(vendor="nvidia", name="NVIDIA GeForce RTX 3090", discrete=True, driver_version="615.20",
                       compute_cap=8.6, vram_mib=24576)

    def test_musl_and_old_glibc_get_no_prebuilt_archive(self):
        musl = HardwareInfo(os="linux", arch="x86_64", ram_total=8 * GIB, ram_available=6 * GIB, libc="musl",
                            libc_version="1.2.5")
        choice = select_backend(musl, CATALOG)
        assert choice.asset is None and "musl" in choice.reason and "ditto runtime install" in choice.reason
        old = musl.model_copy(update={"libc": "glibc", "libc_version": "2.31"})
        choice = select_backend(old, CATALOG)
        assert choice.asset is None and "needs glibc 2.35" in choice.reason
        unknown = musl.model_copy(update={"libc": None, "libc_version": None})
        assert select_backend(unknown, CATALOG).asset is None

    def test_the_glibc_of_each_archive_counts(self):
        jammy = HardwareInfo(os="linux", arch="x86_64", ram_total=32 * GIB, ram_available=24 * GIB,
                             gpus=[self.nvidia()], libc="glibc", libc_version="2.35")
        choice = select_backend(jammy, CATALOG)
        assert choice.asset.id == "linux-x64-cpu" and "glibc 2.39" in choice.reason, \
            "the CUDA archives need Ubuntu 24.04's glibc"
        assert "glibc 2.39" in select_backend(jammy, CATALOG, "cuda").reason
        noble = jammy.model_copy(update={"libc_version": "2.39"})
        assert select_backend(noble, CATALOG).asset.backend == "cuda"

    def test_every_linux_archive_names_its_glibc_and_rocm_is_opt_in(self):
        for asset in CATALOG.llama_cpp.assets:
            if asset.os == "linux":
                assert asset.glibc_min, asset.id
        amd = HardwareInfo(os="linux", arch="x86_64", ram_total=32 * GIB, ram_available=24 * GIB, vulkan=False,
                           hip_runtime=True, libc="glibc", libc_version="2.39",
                           gpus=[GpuInfo(vendor="amd", name="Radeon RX 7900", discrete=True, gfx_target="gfx1100")])
        assert select_backend(amd, CATALOG).backend == "cpu"
        assert select_backend(amd, CATALOG, "rocm").backend == "rocm"


# --- status and CLI -------------------------------------------------------------------------------------------------

class TestStatusAndCli:

    def invoke(self, monkeypatch, rt, *args, **kw):
        from click.testing import CliRunner
        from orchestrator import cli as cli_module
        monkeypatch.setattr(cli_module, "_local_runtime", lambda ctx: rt)
        return CliRunner().invoke(cli_module.cli, ["-q", "ditto", *args], **kw)

    def test_install_status_and_rebuild_advice(self, tmp_path, monkeypatch, no_source):
        rt = runtime(tmp_path)
        result = self.invoke(monkeypatch, rt, "runtime", "install", "--yes")
        assert result.exit_code == 0, result.output
        assert "CPU build (the fallback, built first)" in result.output and "Self-built llama-server ready" in \
            result.output
        data = json.loads(self.invoke(monkeypatch, rt, "runtime", "status", "--json").output)
        inst = data["installed"]
        assert inst["origin"] == "self-built" and inst["label"] == "self-built" and inst["backend"] == "vulkan"
        assert inst["commit"] == PIN.commit and inst["devices"] == ["Vulkan0"] and inst["duration_s"] == 42.0
        assert data["rebuild"] is None and data["recommended_backend"] == "vulkan"
        rt._toolchain = toolchain(info=replace(GCC, cmake="3.31.0"))
        data = json.loads(self.invoke(monkeypatch, rt, "runtime", "status", "--json").output)
        assert data["rebuild"].startswith("recommended"), "another CMake gives another build ID"
        table = self.invoke(monkeypatch, rt, "runtime", "status").output
        assert "self-built" in table and "Rebuild" in table

    def test_status_labels_the_prebuilt_archive(self, tmp_path, monkeypatch):
        rt = runtime(tmp_path, hw=machine([]))
        folder = rt.root / f"{rt.build}-cpu"
        folder.mkdir(parents=True)
        (folder / "llama-server").write_text("#!/bin/sh\n")
        (folder / "runtime.json").write_text(json.dumps({"build": rt.build, "asset_id": "linux-x64-cpu",
                                                         "variant": "cpu", "backend": "cpu",
                                                         "server": "llama-server", "files": {}}))
        rt._update_active(variant="cpu", asset_id="linux-x64-cpu", use="prebuilt")
        data = json.loads(self.invoke(monkeypatch, rt, "runtime", "status", "--json").output)
        assert data["installed"]["origin"] == "prebuilt" and data["installed"]["label"] == PREBUILT_LABEL
        table = self.invoke(monkeypatch, rt, "runtime", "status").output
        assert "prebuilt, not self-built" in table

    def test_install_without_a_way_to_build_exits_with_1(self, tmp_path, monkeypatch):
        rt = runtime(tmp_path, native=False)
        result = self.invoke(monkeypatch, rt, "runtime", "install", "--yes")
        assert result.exit_code == 1 and "--prebuilt" in result.output

    def test_import_export_and_prune_commands(self, tmp_path, monkeypatch, no_source):
        made = runtime(tmp_path / "pc")
        made.install(backend="cpu")
        out = tmp_path / "b.tar.gz"
        assert self.invoke(monkeypatch, made, "runtime", "export", str(out)).exit_code == 0 and out.is_file()
        assert self.invoke(monkeypatch, made, "runtime", "export", str(tmp_path / "b.zip")).exit_code == 2
        laptop = runtime(tmp_path / "laptop")
        declined = self.invoke(monkeypatch, laptop, "runtime", "import", str(out), input="n\n")
        assert declined.exit_code == 1 and laptop.installed() is None
        result = self.invoke(monkeypatch, laptop, "runtime", "import", str(out), input="y\n")
        assert result.exit_code == 0 and "Imported and verified" in result.output, result.output
        assert self.invoke(monkeypatch, laptop, "runtime", "prune").output.strip() == "Nothing to remove."

    def test_doctor_says_whether_the_prebuilt_archive_is_in_use(self, tmp_path, monkeypatch, no_source):
        from orchestrator.Core import doctor
        from orchestrator.runtime.build import toolchain as tc
        monkeypatch.setattr(tc, "detect_toolchain", lambda full=True: toolchain())
        rt = runtime(tmp_path)
        folder = rt.root / f"{rt.build}-cpu"
        folder.mkdir(parents=True)
        (folder / "runtime.json").write_text("{}")
        rt._update_active(use="prebuilt")  # what `install --prebuilt` writes
        framework = type("F", (), {"config": type("C", (), {"user_state_dir": rt.data_dir})()})()
        found = {f.code: f for f in doctor.check_runtime(framework)}
        assert "Ditto's local model server is the official prebuilt" in found["runtime.prebuilt"].message
        rt.install(backend="cpu")
        found = {f.code: f for f in doctor.check_runtime(framework)}
        assert "not in use" in found["runtime.prebuilt"].message and found["runtime.build"].message

    def test_the_tier_follows_the_build_that_will_run(self, tmp_path):
        nv = GpuInfo(vendor="nvidia", name="NVIDIA GeForce RTX 3090", discrete=True, driver_version="615.20",
                     compute_cap=8.6, vram_mib=24576)
        rt = runtime(tmp_path, profile=PC, hw=machine([nv], ram_gib=32))
        model, why = rt.choose_tier()
        assert model.tier == "default", "until CUDA is built here, the server runs on the CPU"
        assert backend_choice(machine([nv]), "cuda").gpu.vendor == "nvidia"
        assert rt.choose_tier(prefer="cuda")[0].tier == "large"


# --- one launch policy ----------------------------------------------------------------------------------------------

class TestOneLaunchPolicy:

    def test_every_launcher_takes_the_lists_from_server_policy(self):
        from orchestrator.Core import dataset_manager
        from orchestrator.runtime.build import verify
        assert ls.FORBIDDEN_FLAGS is server_policy.FORBIDDEN_FLAGS
        assert ls.check_server_command is server_policy.check_server_command
        assert ls.server_env is server_policy.server_env
        for module in (ls, verify, dataset_manager, host_module):
            text = Path(module.__file__).read_text(encoding="utf-8")
            assert "frozenset({" not in text and '"--tools"' not in text and '"--api-key"' not in text, \
                f"{module.__name__} keeps its own option list"
            assert "env=os.environ" not in text and "os.environ.copy()" not in text, module.__name__

    def test_server_commands_keep_loopback_key_file_and_fixed_options(self, tmp_path):
        cmd = server_policy.server_command(Path("/b/llama-server"), Path("/m/x.gguf"), port=4000,
                                           key_file=tmp_path / "server-a.key", alias="x", extra=["-c", "4096"])
        assert {"--no-ui", "--no-slots", "--jinja", "--offline"} <= set(cmd)
        for bad in (["--host", "0.0.0.0"], ["--host=0.0.0.0"], ["--api-key", "k"], ["--tools", "all"],
                    ["--api-key-file", "other"]):
            with pytest.raises(server_policy.PolicyError):
                server_policy.check_server_command(cmd + bad)
        with pytest.raises(server_policy.PolicyError, match="--no-ui"):
            server_policy.check_server_command([c for c in cmd if c != "--no-ui"])
        with pytest.raises(server_policy.PolicyError, match="api-key-file"):
            i = cmd.index("--api-key-file")
            server_policy.check_server_command(cmd[:i] + cmd[i + 2:])

    @POSIX_ONLY
    def test_key_files_are_private_from_the_start(self, tmp_path):
        path, key = server_policy.write_key_file(tmp_path / "rt")
        assert path.name.startswith("server-") and stat.S_IMODE(path.stat().st_mode) == 0o600
        assert path.read_text().strip() == key and len(key) >= 40
        second, other = server_policy.write_key_file(tmp_path / "rt")
        assert second != path and other != key


# --- review: the prebuilt archive of an earlier version, every spelling of a forbidden option, the CPU of a build ---

def _legacy_archive(rt, asset_id="linux-x64-cpu", variant="cpu"):
    """An official archive as an earlier version installed it: `runtime install` downloaded it without --prebuilt,
    so active.json names its variant but has no "use"."""
    asset = next(a for a in rt.catalog.llama_cpp.assets if a.id == asset_id)
    folder = rt.root / f"{rt.build}-{variant}"
    folder.mkdir(parents=True)
    (folder / "llama-server").write_text("#!/bin/sh\n")
    (folder / "runtime.json").write_text(json.dumps({"build": rt.build, "asset_id": asset.id, "variant": variant,
                                                     "backend": asset.backend, "server": "llama-server",
                                                     "files": {f.name: f.sha256 for f in asset.files}}))
    rt._update_active(variant=variant, asset_id=asset.id)
    return asset


class NoNetwork:
    """A requests session that fails the test on any request."""
    trust_env = False

    def get(self, *a, **k):
        raise AssertionError("nothing may be downloaded")

    head = request = get


class TestPrebuiltOnlyAfterTheOptIn:

    def test_an_archive_an_earlier_version_downloaded_is_not_started_without_the_opt_in(self, tmp_path):
        rt = runtime(tmp_path, hw=machine([]))
        _legacy_archive(rt)
        inst, problems = rt.resolve_installed()
        assert inst is None, "decision D1: the prebuilt archive is never chosen without --prebuilt"
        assert any(PREBUILT_LABEL in p and "install --prebuilt" in p for p in problems), problems
        rt._popen = lambda *a, **k: pytest.fail("the prebuilt archive was started without the opt-in")
        with pytest.raises(LocalRuntimeError, match="install --prebuilt"):
            rt.start(tier="small")

    def test_the_opt_in_takes_the_installed_archive_without_downloading_it_again(self, tmp_path):
        rt = runtime(tmp_path, hw=machine([]), session=NoNetwork())
        asset = _legacy_archive(rt)
        assert rt.select_backend().asset.id == asset.id and rt.prebuilt_installed(asset)
        inst = rt.install_prebuilt()
        assert inst.origin == "prebuilt" and inst.label == PREBUILT_LABEL
        assert json.loads(rt.active_file.read_text())["use"] == "prebuilt" and rt.installed().origin == "prebuilt"

    def test_the_cli_asks_to_use_an_installed_archive_instead_of_offering_a_download(self, tmp_path, monkeypatch):
        from click.testing import CliRunner
        from orchestrator import cli as cli_module
        rt = runtime(tmp_path, hw=machine([]), session=NoNetwork())
        _legacy_archive(rt)
        monkeypatch.setattr(cli_module, "_local_runtime", lambda ctx: rt)
        result = CliRunner().invoke(cli_module.cli, ["-q", "ditto", "runtime", "install", "--prebuilt"], input="y\n")
        assert result.exit_code == 0, result.output
        assert "Download" not in result.output and "already installed" in result.output
        assert rt.installed().origin == "prebuilt"

    def test_doctor_does_not_call_an_archive_without_the_opt_in_the_server_in_use(self, tmp_path, monkeypatch):
        from orchestrator.Core import doctor
        from orchestrator.runtime.build import toolchain as tc
        monkeypatch.setattr(tc, "detect_toolchain", lambda full=True: toolchain())
        rt = runtime(tmp_path, hw=machine([]))
        _legacy_archive(rt)
        framework = type("F", (), {"config": type("C", (), {"user_state_dir": rt.data_dir})()})()
        finding = {f.code: f for f in doctor.check_runtime(framework)}["runtime.prebuilt"]
        assert "not in use" in finding.message and "--prebuilt" in finding.fix_hint


class TestEverySpellingOfAForbiddenOption:

    @pytest.mark.parametrize("args", [
        ["-hfr", "user/model"],              # the third spelling of --hf-repo
        ["--hf_repo", "user/model"],         # llama.cpp reads --a_b as --a-b
        ["--api_key", "k"], ["--tools_runtime", "x"], ["--mcp_servers_json", "{}"],
        ["-hff", "x.gguf"], ["--hf-file", "x.gguf"],
        ["-hft", "t"], ["--hf-token", "t"],  # a token on the command line
        ["-mmu", "https://x.invalid/p.gguf"], ["--mmproj-url", "https://x.invalid/p.gguf"],
        ["--spec-draft-hf", "u/m"], ["-hfd", "u/m"], ["-hfrd", "u/m"], ["--hf-repo-draft", "u/m"],
        ["--log-prompts-dir", "prompts"],    # writes every prompt to disk
        ["--video-ffmpeg-dir", "/opt/x"],    # runs programs from that folder
    ])
    def test_refused(self, args):
        assert server_policy.forbidden_in(["llama-server", *args]), args
        with pytest.raises(server_policy.PolicyError):
            server_policy.check_command(["llama-server", *args])

    def test_a_second_port_or_key_file_in_another_spelling_is_refused(self, tmp_path):
        cmd = server_policy.server_command(Path("/b/llama-server"), Path("/m/x.gguf"), port=4000,
                                           key_file=tmp_path / "server-a.key", alias="x")
        for extra in (["--api_key_file", str(tmp_path / "other.key")], ["--port", "4001"]):
            with pytest.raises(server_policy.PolicyError):
                server_policy.check_server_command(cmd + extra)


def _z1_without(*keys):
    """The Z1 Extreme profile with CPU features turned off: another, older CPU of the same vendor."""
    from orchestrator.runtime.profile import parse_profile
    text = (PROBES / "schema2_linux_z1_extreme.txt").read_text(encoding="utf-8")
    for key in keys:
        assert f"{key}=ON" in text, key
        text = text.replace(f"{key}=ON", f"{key}=OFF")
    return parse_profile(text)


class TestTheCpuOfANativeBuild:

    def test_a_build_that_needs_instructions_this_cpu_lacks_is_not_imported(self, tmp_path, no_source):
        made = runtime(tmp_path / "pc")
        made.install(backend="cpu")
        archive = made.export_build(tmp_path / "llama-build.tar.gz")
        older = _z1_without("SUPPORTS_AVX512", "SUPPORTS_AVX512_VBMI", "SUPPORTS_AVX512_VNNI", "SUPPORTS_AVX512_BF16")
        verifier = FakeVerifier()
        laptop = runtime(tmp_path / "laptop", profile=older, verifier=verifier)
        asked = []
        with pytest.raises(LocalRuntimeError, match="AVX512"):
            laptop.import_build(archive, confirm=lambda info: asked.append(info) or True)
        assert asked == [] and verifier.calls == 0, "refused before the user is asked and before anything runs"
        assert laptop.installed() is None
        with pytest.raises(LocalRuntimeError, match="CPU vendor"):
            runtime(tmp_path / "intel", profile=IVY).import_build(archive, confirm=lambda info: True)

    def test_what_an_archive_says_about_its_build_host_is_shown_only_as_plain_values(self, tmp_path, no_source):
        import tarfile
        made = runtime(tmp_path / "pc")
        made.install(backend="cpu")
        archive = made.export_build(tmp_path / "llama-build.tar.gz")
        unpacked = tmp_path / "unpacked"
        with tarfile.open(archive) as tar:
            tar.extractall(unpacked, filter="data")
        manifest = json.loads((unpacked / "manifest.json").read_text())
        manifest["build_host"] = {"os": "linux\x1b[2J\nFAKE", "arch": "x86_64"}  # not part of the build ID
        manifest["built_at"] = "\x1b]8;;evil\x07now"
        (unpacked / "manifest.json").write_text(json.dumps(manifest))
        forged = tmp_path / "forged.tar.gz"
        with tarfile.open(forged, "w:gz") as tar:
            for item in ("build.json", "manifest.json", "bin"):
                tar.add(unpacked / item, arcname=item)
        asked = []
        inst = runtime(tmp_path / "laptop").import_build(forged, confirm=lambda info: asked.append(info) or True)
        assert asked[0]["built_on"] == {"os": "unknown", "arch": "x86_64"} and asked[0]["built_at"] is None
        assert "plan" not in asked[0]
        assert inst.built_on == "unknown/x86_64" and "\x1b" not in inst.label

    def test_a_stored_build_for_another_cpu_is_not_started(self, tmp_path):
        rt = runtime(tmp_path)
        build = _stored(rt)  # built with -march=native on the Z1 Extreme (AVX-512)
        rt.store.activate(build.build_id)
        rt._update_active(use="self-built")
        assert rt.installed() is not None
        moved = runtime(tmp_path, profile=_z1_without("SUPPORTS_AVX512"))  # e.g. the same home folder on another PC
        started = []
        moved._popen = lambda *a, **k: started.append(a) or pytest.fail("started on a CPU it was not built for")
        with pytest.raises(LocalRuntimeError, match="AVX512"):
            moved.start(tier="small")
        assert started == []
