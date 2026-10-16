"""Running, verifying and storing a llama.cpp build (orchestrator/runtime/build/: toolchain.py, executor.py,
verify.py, store.py), the shared launch policy (orchestrator/runtime/server_policy.py) and the doctor's runtime check.

No real compiler run and no network: the executor is driven with a fake `cmake` (a small script that records its
arguments and environment) and a fake container engine; the verifier with a fake program runner and copies of
system ELF files. A real build at the pin belongs in a nightly CI job (design 10.2, not set up yet), not in this
suite.
"""

import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tarfile
from dataclasses import replace
from pathlib import Path
from typing import List

import pytest

from orchestrator.runtime import server_policy
from orchestrator.runtime.build import toolchain as tc
from orchestrator.runtime.build.executor import (DOWNLOAD_MARKERS, BuildError, BuildExecutor, BuildResult,
                                                 ContainerEngine, builder_image_tag, check_configure_log)
from orchestrator.runtime.build.recipe import BuildStep, ToolchainInfo, build_id_of, plan_build
from orchestrator.runtime.build.store import BuildFailed, BuildStore, StoreError, smoke_model_file
from orchestrator.runtime.build.verify import (Check, SmokeModelFile, Verifier, VerifyError, VerifyReport,
                                               binary_path, parse_bench_json, parse_devices, parse_version,
                                               read_elf)
from orchestrator.runtime.catalog import load_catalog
from orchestrator.runtime.profile import parse_profile, read_profile

ROOT = Path(__file__).resolve().parents[1]
PROBES = ROOT / "tests" / "fixtures" / "hardware_probe"
CATALOG = load_catalog()
PIN = CATALOG.llama_cpp.source
PC = read_profile(PROBES / "schema2_pc_two_nvidia.txt")          # Linux x86_64, gcc-13.3.0, glibc 2.39
RK3566 = read_profile(PROBES / "schema2_rk3566_dietpi.txt")      # arm64 board, glibc 2.36
GCC = ToolchainInfo(host_os="linux", host_arch="x86_64", compiler="gcc-13.3.0", cmake="3.28.3", openmp=True)
POSIX = pytest.mark.skipif(os.name != "posix", reason="the fake cmake is a script with a shebang")
# Anything that would accept a licence on the user's behalf (decision D5, design 10: reviewed in every package).
LICENCE_FLAGS = re.compile(r"accept|agree|eula|licen[cs]e", re.IGNORECASE)


def licence_flags(argv) -> List[str]:
    """Options of a command line (not its paths) that would accept a licence."""
    return [a for a in argv if str(a).startswith("-") and LICENCE_FLAGS.search(str(a))]


def native_plan(**kw):
    return plan_build(PC, purpose="host-runtime", placement="native", backend="cpu", catalog=CATALOG,
                      toolchain=kw.pop("toolchain", GCC), **kw)


def with_targets(plan, targets):
    step = plan.steps[0]
    plan = replace(plan, steps=(BuildStep(step.name, step.compiler, step.generator, step.options, tuple(targets),
                                          step.toolchain_file),), verify={**plan.verify, "binaries": list(targets)})
    return replace(plan, build_id=build_id_of(plan.id_inputs()))


# --- fake cmake ---------------------------------------------------------------------------------------------------

FAKE_CMAKE = '''#!{python}
import json, os, pathlib, sys
here = pathlib.Path(__file__).resolve().parent
with open(here / "calls.jsonl", "a") as f:
    f.write(json.dumps({{"argv": sys.argv, "env": dict(os.environ)}}) + "\\n")
control = json.loads((here / "control.json").read_text()) if (here / "control.json").exists() else {{}}
args = sys.argv[1:]
if args[:1] == ["--version"]:
    print("cmake version 3.28.3")
    sys.exit(0)
if "--build" in args:
    out = pathlib.Path(args[args.index("--build") + 1]) / "bin"
    out.mkdir(parents=True, exist_ok=True)
    for name in args[args.index("--target") + 1:]:
        if name in control.get("skip", []):
            continue
        p = out / name
        p.write_text("#!/bin/sh\\necho fake " + name + "\\n")
        p.chmod(0o755)
    for name in control.get("libs", []):
        (out / name).write_bytes(b"lib " + name.encode())
    for name, target in control.get("links", {{}}).items():
        os.symlink(target, out / name)
    (out / "test-something").write_text("not collected")
    print("[100%] Built target")
    sys.exit(control.get("build_rc", 0))
print("-- The C compiler identification is GNU 13.3.0")
for line in control.get("configure_lines", []):
    print(line)
if "cache" in control:
    (pathlib.Path(args[args.index("-B") + 1]) / "CMakeCache.txt").write_text(control["cache"])
sys.exit(control.get("configure_rc", 0))
'''


@pytest.fixture
def fake_tools(tmp_path):
    """A folder with a fake cmake and ninja, and the DetectedToolchain that points at them."""
    tools = tmp_path / "tools"
    tools.mkdir()
    cmake = tools / "cmake"
    cmake.write_text(FAKE_CMAKE.format(python=sys.executable), encoding="utf-8")
    cmake.chmod(0o755)
    ninja = tools / "ninja"
    ninja.write_text("#!/bin/sh\necho 1.11.1\n")
    ninja.chmod(0o755)
    detected = tc.DetectedToolchain(info=GCC, tools={"cmake": str(cmake), "ninja": str(ninja), "cc": "/usr/bin/cc",
                                                     "cxx": "/usr/bin/c++"}, cpus=4, ram_available_mib=8192)
    return tools, detected


def control(tools: Path, **values):
    (tools / "control.json").write_text(json.dumps(values), encoding="utf-8")


def calls(tools: Path) -> List[dict]:
    path = tools / "calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.fixture
def source(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "CMakeLists.txt").write_text("project(llama.cpp)\n")
    return src


# --- toolchain detection ------------------------------------------------------------------------------------------

GCC_V = """Using built-in specs.
COLLECT_GCC=cc
Target: x86_64-linux-gnu
 /usr/libexec/gcc/x86_64-linux-gnu/13/cc1 -E -quiet -v -imultiarch x86_64-linux-gnu empty.c -march=skylake -mmmx \
-mpopcnt -msse -msse2 -mavx2 -mno-avx512f --param=l1-cache-size=32 --param=l2-cache-size=8192 -mtune=skylake \
-fasynchronous-unwind-tables -o /dev/null
"""
CLANG_V = """Ubuntu clang version 18.1.3 (1ubuntu1)
 "/usr/lib/llvm-18/bin/clang" -cc1 -triple x86_64-pc-linux-gnu -E -mrelocation-model pic -target-cpu znver4 \
-target-feature +avx512f -target-feature +avx2 -o /dev/null -x c empty.c
clang -cc1 version 18.1.3 based upon LLVM 18.1.3 default target x86_64-pc-linux-gnu
"""
ARM_V = " /usr/libexec/gcc/aarch64-linux-gnu/12/cc1 -E -quiet -v empty.c -mcpu=cortex-a55+crypto -mlittle-endian\n"


class TestToolchain:

    def test_native_expansion_of_gcc_clang_and_arm(self):
        gcc = tc.parse_native_expansion(GCC_V)
        assert gcc.startswith("march=skylake mtune=skylake features=")
        assert tc.parse_native_expansion(GCC_V.replace("-mavx2 ", "")) != gcc, "another feature set, another value"
        assert tc.parse_native_expansion(CLANG_V).startswith("cpu=znver4 features=")
        assert tc.parse_native_expansion(ARM_V).startswith("mcpu=cortex-a55+crypto features=")
        assert tc.parse_native_expansion("no compiler line") is None
        for text in (GCC_V, CLANG_V, ARM_V):
            ToolchainInfo(host_os="linux", host_arch="x86_64", native_expansion=tc.parse_native_expansion(text))

    def test_versions(self):
        assert tc.parse_cmake_version("cmake version 3.28.3\n\nCMake suite maintained") == "3.28.3"
        assert tc.parse_plain_version("1.11.1\n") == "1.11.1"
        assert tc.parse_cmake_version("garbage") is None

    @pytest.mark.parametrize("cpus, ram, per_job, limit, expected", [
        (4, 15000, 1024, None, 4), (16, 6000, 1024, None, 5), (16, 6000, 3072, None, 1), (8, 0, 1024, None, 8),
        (8, 64000, 1024, 2, 2), (1, 100, 1024, None, 1)])
    def test_jobs_from_memory(self, cpus, ram, per_job, limit, expected):
        assert tc.parallel_jobs(cpus, ram, per_job, limit) == expected

    def test_catalog_has_job_memory_per_backend(self):
        assert CATALOG.build_jobs.per_job_mib("cuda") > CATALOG.build_jobs.per_job_mib("cpu") >= 512
        assert CATALOG.smoke_model is None, "update the verification notes and docs when D17 pins a model"

    def fake_run(self, answers):
        seen = []

        def run(argv, timeout, cwd):
            seen.append(list(argv))
            name = Path(argv[0]).name
            key = (name, argv[1] if len(argv) > 1 else "")
            return answers.get(key, answers.get(name, (-1, "", "not found")))
        return run, seen

    def test_detection_reads_versions_and_checks(self):
        tools = {"cmake": "/usr/bin/cmake", "ninja": "/usr/bin/ninja", "cc": "/usr/bin/cc", "c++": "/usr/bin/c++"}
        run, seen = self.fake_run({
            ("cmake", "--version"): (0, "cmake version 3.25.1\n", ""),
            ("ninja", "--version"): (0, "1.11.1\n", ""),
            ("cc", "--version"): (0, "cc (Debian 12.2.0-14) 12.2.0\nCopyright (C) 2022 Free Software Foundation", ""),
            ("c++", "--version"): (0, "c++ (Debian 12.2.0-14) 12.2.0\nFree Software Foundation", ""),
            ("cc", "-march=native"): (0, "", GCC_V),
            ("cc", "-fopenmp"): (0, "", ""),
        })
        d = tc.detect_toolchain(run=run, which=tools.get, system="Linux", machine="x86_64", resources=(4, 8000))
        assert d.native_ready and d.problems == ()
        assert d.info.compiler == "gcc-12.2.0" and d.info.cmake == "3.25.1" and d.info.openmp is True
        assert d.info.native_expansion.startswith("march=skylake")
        assert (d.cpus, d.ram_available_mib) == (4, 8000)
        assert not any(licence_flags(argv) for argv in seen)

    def test_missing_tools_are_named_and_clang_without_libomp_is_noted(self):
        tools = {"cmake": "/usr/bin/cmake", "cc": "/usr/bin/cc", "c++": "/usr/bin/c++"}
        run, _ = self.fake_run({
            ("cmake", "--version"): (0, "cmake version 3.13.4\n", ""),
            ("cc", "--version"): (0, "Ubuntu clang version 18.1.3", ""),
            ("c++", "--version"): (0, "Ubuntu clang version 18.1.3", ""),
            ("cc", "-march=native"): (0, "", CLANG_V),
            ("cc", "-fopenmp"): (1, "", "fatal error: 'omp.h' file not found"),
        })
        d = tc.detect_toolchain(run=run, which=tools.get, system="Linux", machine="x86_64", resources=(2, 4000))
        assert not d.native_ready
        assert any("CMake 3.13.4 is too old" in p for p in d.problems)
        assert any("Ninja" in p for p in d.problems)
        assert d.info.compiler == "clang-18.1.3" and d.info.openmp is False
        assert any("libomp" in n for n in d.notes)
        plan = plan_build(PC, purpose="host-runtime", placement="native", backend="cpu", catalog=CATALOG,
                          toolchain=d.info)
        assert plan.options()["GGML_OPENMP"] == "OFF", "the detection result reaches the plan and its build ID"

    def test_no_compiler_and_quick_mode(self):
        run, seen = self.fake_run({("cmake", "--version"): (0, "cmake version 3.28.3", "")})
        d = tc.detect_toolchain(run=run, which={"cmake": "/usr/bin/cmake"}.get, system="Linux", machine="x86_64",
                                resources=(1, 1), full=False)
        assert any("no C and C++ compiler" in p for p in d.problems)
        assert not any("-fopenmp" in argv for argv in seen)

    def test_unsupported_platform(self):
        with pytest.raises(tc.ToolchainError, match="no llama.cpp build plan"):
            tc.detect_toolchain(run=lambda *a: (-1, "", ""), which=lambda n: None, system="FreeBSD",
                                machine="riscv64", resources=(1, 1))

    def test_the_real_machine(self):
        d = tc.detect_toolchain(full=False)
        assert d.cpus >= 1 and isinstance(d.problems, tuple)


# --- the executor: native --------------------------------------------------------------------------------------------

@POSIX
class TestNativeExecutor:

    def test_runs_configure_and_build_with_the_plan_and_a_cleared_environment(self, tmp_path, fake_tools, source,
                                                                             monkeypatch):
        tools, detected = fake_tools
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
        monkeypatch.setenv("LLAMA_ARG_TOOLS", "all")
        monkeypatch.setenv("GGML_CUDA", "ON")
        plan = native_plan()
        result = BuildExecutor().build(plan, source=source, work=tmp_path / "work", jobs=3, toolchain=detected)
        configure, build = calls(tools)
        argv = configure["argv"]
        assert argv[1:7] == ["-S", str(source.resolve()), "-B", str(tmp_path / "work" / "build"), "-G", "Ninja"]
        assert argv[7:] == plan.steps[0].cmake_args(), "exactly the plan's options, nothing added"
        assert build["argv"][1:] == ["--build", str(tmp_path / "work" / "build"), "--parallel", "3", "--target",
                                     *plan.targets]
        env = configure["env"]
        assert env["CC"] == "/usr/bin/cc" and env["CXX"] == "/usr/bin/c++"
        assert env["PATH"].split(os.pathsep)[0] == str(tools)
        assert not any(k.upper().startswith(("LLAMA_", "GGML_")) or "PROXY" in k.upper() for k in env), env
        assert env["HOME"] == str(tmp_path / "work" / "home")
        assert sorted(result.files) == sorted(plan.targets), "test binaries of the build tree are not collected"
        for name, digest in result.files.items():
            assert hashlib.sha256((result.bin_dir / name).read_bytes()).hexdigest() == digest
        assert result.jobs == 3 and result.placement == "native" and result.builder_image is None
        assert {"configure", "build"} <= set(result.logs)

    def test_no_licence_flag_in_any_command(self, tmp_path, fake_tools, source):
        tools, detected = fake_tools
        BuildExecutor().build(native_plan(), source=source, work=tmp_path / "w", jobs=1, toolchain=detected)
        for call in calls(tools):
            assert licence_flags(call["argv"]) == [], call["argv"]

    @pytest.mark.parametrize("line", [
        "-- Fetching BoringSSL version 0.20250101", "-- UI: downloading from hf: https://huggingface.co/x/dist",
        "-- Downloading ggml-vocab from ggml-org/models...", "-- Populating kleidiai", "-- UI: running npm ci",
        "-- UI: HF download failed"])
    def test_configure_log_with_a_download_fails_the_build(self, tmp_path, fake_tools, source, line):
        tools, detected = fake_tools
        control(tools, configure_lines=[line])
        with pytest.raises(BuildError, match="fetch or download") as e:
            BuildExecutor().build(native_plan(), source=source, work=tmp_path / "w", jobs=1, toolchain=detected)
        assert e.value.log and e.value.log.name == "configure.log"
        assert [c["argv"][1] for c in calls(tools)] == ["-S"], "the build step never runs"

    def test_the_real_configure_lines_pass(self):
        real = ["-- Found Git: /usr/bin/git (found version \"2.43.0\")", "-- Found OpenMP: TRUE (found version 4.5)",
                "-- Adding CPU backend variant ggml-cpu: -march=native", "-- Disabling MTMD_VIDEO because "
                "LLAMA_SUBPROCESS is OFF", "Manually-specified variables were not used by the project:",
                "    FETCHCONTENT_FULLY_DISCONNECTED"]
        assert check_configure_log("\n".join(real)) == []
        assert DOWNLOAD_MARKERS.search("Fetching") and not DOWNLOAD_MARKERS.search("Found Threads: TRUE")

    def test_failures_name_the_step_and_keep_the_log(self, tmp_path, fake_tools, source):
        tools, detected = fake_tools
        control(tools, configure_rc=1, configure_lines=["CMake Error at CMakeLists.txt:1: boom"])
        with pytest.raises(BuildError, match="configure failed") as e:
            BuildExecutor().build(native_plan(), source=source, work=tmp_path / "w", jobs=1, toolchain=detected)
        assert "boom" in str(e.value)
        control(tools, skip=["llama-bench"])
        with pytest.raises(BuildError, match="did not produce llama-bench"):
            BuildExecutor().build(native_plan(), source=source, work=tmp_path / "w2", jobs=1, toolchain=detected)

    def test_shared_libraries_and_their_links_are_collected(self, tmp_path, fake_tools, source):
        tools, detected = fake_tools
        control(tools, libs=["libggml-base.so.0.26.0"], links={"libggml-base.so.0": "libggml-base.so.0.26.0"})
        result = BuildExecutor().build(native_plan(), source=source, work=tmp_path / "w", jobs=1, toolchain=detected)
        assert "libggml-base.so.0.26.0" in result.files and result.links == {"libggml-base.so.0":
                                                                             "libggml-base.so.0.26.0"}
        control(tools, links={"libevil.so": "../../../etc/passwd"})
        with pytest.raises(BuildError, match="links out of the build folder"):
            BuildExecutor().build(native_plan(), source=source, work=tmp_path / "w2", jobs=1, toolchain=detected)

    def test_plans_that_do_not_fit_the_machine_are_refused(self, tmp_path, fake_tools, source):
        tools, detected = fake_tools
        ex = BuildExecutor()
        other = native_plan(toolchain=replace(GCC, compiler="gcc-14.2.0"))
        with pytest.raises(BuildError, match="plan the build again"):
            ex.build(other, source=source, work=tmp_path / "a", jobs=1, toolchain=detected)
        expanded = native_plan(toolchain=replace(GCC, native_expansion="march=skylake features=00"))
        with pytest.raises(BuildError, match="expands differently"):
            ex.build(expanded, source=source, work=tmp_path / "b", jobs=1, toolchain=detected)
        broken = replace(detected, problems=("Ninja was not found (install ninja-build)",))
        with pytest.raises(BuildError, match="Ninja was not found"):
            ex.build(native_plan(), source=source, work=tmp_path / "c", jobs=1, toolchain=broken)
        cuda = plan_build(PC, purpose="host-runtime", placement="native", backend="vulkan", catalog=CATALOG,
                          toolchain=GCC)
        assert ex.build(cuda, source=source, work=tmp_path / "d", jobs=1, toolchain=detected).files
        with pytest.raises(BuildError, match="build the CPU variant now"):
            ex.build(replace(cuda, backend="cuda"), source=source, work=tmp_path / "e", jobs=1, toolchain=detected)
        with pytest.raises(BuildError, match="not a llama.cpp source tree"):
            ex.build(native_plan(), source=tmp_path, work=tmp_path / "f", jobs=1, toolchain=detected)
        assert calls(tools) and all("-DGGML_CUDA=ON" not in c["argv"] for c in calls(tools))

    def test_a_plan_with_a_download_option_is_refused(self, tmp_path, fake_tools, source):
        tools, detected = fake_tools
        plan = native_plan()
        step = plan.steps[0]
        bad = replace(plan, steps=(replace(step, options=tuple((k, "ON" if k == "LLAMA_USE_PREBUILT_UI" else v)
                                                               for k, v in step.options)),))
        with pytest.raises(BuildError, match="LLAMA_USE_PREBUILT_UI"):
            BuildExecutor().build(bad, source=source, work=tmp_path / "w", jobs=1, toolchain=detected)
        assert calls(tools) == []

    def test_other_targets_and_placements_are_refused(self, tmp_path, source):
        ex = BuildExecutor()
        windows = plan_build(read_profile(PROBES / "schema2_windows_apu.txt"), purpose="host-runtime",
                             placement="native", backend="cpu", catalog=CATALOG,
                             toolchain=ToolchainInfo(host_os="windows", host_arch="x86_64", compiler="clang-19.1.5"))
        with pytest.raises(BuildError, match="later work package"):
            ex.build(windows, source=source, work=tmp_path / "w", jobs=1)
        tools_plan = plan_build(PC, purpose="host-tools", placement="container", catalog=CATALOG,
                                toolchain=ToolchainInfo(host_os="linux", host_arch="x86_64",
                                                        base_image="debian:bookworm-slim"))
        with pytest.raises(BuildError, match="toolchain image"):
            ex.build(tools_plan, source=source, work=tmp_path / "w2", jobs=1)

    def test_every_build_starts_in_an_empty_folder(self, tmp_path, fake_tools, source):
        _tools, detected = fake_tools
        (tmp_path / "w" / "bin").mkdir(parents=True)
        (tmp_path / "w" / "bin" / "llama-server").write_text("old")
        with pytest.raises(BuildError, match="not empty"):
            BuildExecutor().build(native_plan(), source=source, work=tmp_path / "w", jobs=1, toolchain=detected)


# --- the executor: builder container ---------------------------------------------------------------------------------

class FakeEngine(ContainerEngine):
    def __init__(self, gcc="12.2.0", reason=None, have_image=False, configure_lines=(), fail=None, tamper=None,
                 libdir="/usr/lib/gcc-cross/aarch64-linux-gnu/12/../../../../aarch64-linux-gnu/lib/"):
        self.gcc, self.reason, self.have_image = gcc, reason, have_image
        self.libdir = libdir  # None: the toolchain has no runtime library (gcc prints the bare name back)
        self.configure_lines, self.fail = configure_lines, fail
        self.tamper = tamper  # what a hostile build does with the folders it may write (mounts by target)
        self.built: List[dict] = []
        self.runs: List[dict] = []

    def unavailable(self):
        return self.reason

    def image_exists(self, tag):
        return self.have_image

    def build_image(self, context, tag, buildargs, labels, log):
        self.built.append({"tag": tag, "args": dict(buildargs), "labels": dict(labels),
                           "dockerfile": (Path(context) / "Dockerfile").read_text()})

    def run(self, image, command, *, mounts, env, options, log, timeout):
        self.runs.append({"image": image, "command": list(command), "mounts": list(mounts), "env": dict(env),
                          "options": dict(options)})
        build = next(m.source for m in mounts if m.target == "/work/build")
        if command[-1] == "-dumpfullversion":
            Path(log).write_text(self.gcc + "\n")
            return 0
        if command[-1].startswith("-print-file-name="):  # where the toolchain keeps a runtime library
            lib = command[-1].split("=", 1)[1]
            Path(log).write_text((self.libdir + lib if self.libdir else lib) + "\n")
            return 0
        if command[0] == "cp":  # copying it into the build's bin/ (a fake ELF file of the target)
            assert command[1] == "-L" and command[3].startswith("/work/build/bin/")
            (Path(build) / "bin" / command[3].rsplit("/", 1)[1]).write_bytes(b"\x7fELF fake runtime library")
            return 0
        if self.fail and self.fail in command:
            Path(log).write_text("error: boom\n")
            return 2
        if "--build" in command:
            out = Path(build) / "bin"
            out.mkdir(parents=True, exist_ok=True)
            for name in command[command.index("--target") + 1:]:
                (out / name).write_bytes(b"\x7fELF fake")
            if self.tamper:
                self.tamper({m.target: Path(m.source) for m in mounts if not m.read_only})
            return 0
        Path(log).write_text("\n".join(["-- configured", *self.configure_lines]) + "\n")
        return 0


class TestContainerExecutor:

    def cross_plan(self):
        return plan_build(RK3566, purpose="device-runtime", placement="cross", catalog=CATALOG,
                          toolchain=ToolchainInfo(host_os="linux", host_arch="x86_64", cmake="3.25.1"))

    def test_cross_build_runs_without_network_in_the_sandbox(self, tmp_path, source):
        engine = FakeEngine()
        plan = self.cross_plan()
        result = BuildExecutor(engine=engine).build(plan, source=source, work=tmp_path / "w", jobs=2)
        (image,) = engine.built
        assert image["args"]["BASE"] == plan.builder["image"] and "@sha256:" in image["args"]["BASE"]
        assert image["args"]["CROSS_TRIPLE"] == "aarch64-linux-gnu" and image["args"]["LIBC"] == "glibc"
        assert image["tag"] == builder_image_tag(plan) and not image["tag"].endswith(":latest")
        assert image["dockerfile"].startswith("# Builder image for self-built llama.cpp")
        assert [r["command"][0] for r in engine.runs] == ["aarch64-linux-gnu-gcc", "cmake", "cmake",
                                                         "aarch64-linux-gnu-gcc", "cp"]
        for run in engine.runs:
            o = run["options"]
            assert o["network_mode"] == "none" and o["cap_drop"] == ["ALL"] and o["read_only"] is True
            assert o["security_opt"] == ["no-new-privileges:true"] and o["pids_limit"] > 0
            assert re.fullmatch(r"\d+:\d+", o["user"]) and o["user"] != "0:0"
            src = next(m for m in run["mounts"] if m.target == "/src")
            assert src.read_only and src.source == source.resolve()
            assert licence_flags(run["command"]) == []
            # Only the folders the build writes are writable; bin/ and logs/ of the work folder are not mounted.
            writable = {m.target: m.source for m in run["mounts"] if not m.read_only}
            assert writable == {"/work/build": tmp_path / "w" / "build", "/work/home": tmp_path / "w" / "home",
                                "/work/tmp": tmp_path / "w" / "tmp"}
            assert {m.target for m in run["mounts"] if m.read_only} == {"/src", "/work/config"}
        configure = engine.runs[1]["command"]
        assert configure[:7] == ["cmake", "-S", "/src", "-B", "/work/build", "-G", "Ninja"]
        assert configure[-1] == "-DCMAKE_TOOLCHAIN_FILE=/work/config/toolchain.cmake"
        assert (tmp_path / "w" / "config" / "toolchain.cmake").read_text() == plan.steps[0].toolchain_file
        assert engine.runs[2]["command"][-len(plan.targets):] == list(plan.targets)
        # The OpenMP runtime of the board comes from the cross toolchain that linked the programs (decision D7).
        assert engine.runs[3]["command"] == ["aarch64-linux-gnu-gcc", "-print-file-name=libgomp.so.1"]
        assert engine.runs[4]["command"][:2] == ["cp", "-L"]
        assert engine.runs[4]["command"][2].endswith("/aarch64-linux-gnu/lib/libgomp.so.1")
        assert engine.runs[4]["command"][3] == "/work/build/bin/libgomp.so.1"
        assert result.builder_image == image["tag"]
        assert sorted(result.files) == sorted([*plan.targets, "libgomp.so.1"])

    def test_a_toolchain_without_the_runtime_library_fails_the_build(self, tmp_path, source):
        engine = FakeEngine(libdir=None)  # gcc prints the bare name back when it has no such library
        with pytest.raises(BuildError, match="has no libgomp.so.1"):
            BuildExecutor(engine=engine).build(self.cross_plan(), source=source, work=tmp_path / "w", jobs=1)
        assert engine.runs[-1]["command"][-1] == "-print-file-name=libgomp.so.1"

    def test_only_known_runtime_libraries_are_copied(self, tmp_path, source):
        plan = self.cross_plan()
        hostile = replace(plan, verify={**plan.verify, "runtime_libs": ["../../etc/shadow"]})
        with pytest.raises(BuildError, match="not one this executor copies"):
            BuildExecutor(engine=FakeEngine()).build(hostile, source=source, work=tmp_path / "w", jobs=1)

    def test_a_native_architecture_container_build_takes_the_library_from_gcc(self, tmp_path, source):
        plan = plan_build(RK3566, purpose="device-runtime", placement="cross", catalog=CATALOG,
                          toolchain=ToolchainInfo(host_os="linux", host_arch="arm64"))
        assert plan.steps[0].toolchain_file is None and plan.verify["runtime_libs"] == ["libgomp.so.1"]
        engine = FakeEngine(libdir="/usr/lib/aarch64-linux-gnu/")
        result = BuildExecutor(engine=engine).build(plan, source=source, work=tmp_path / "w", jobs=1)
        assert ["gcc", "-print-file-name=libgomp.so.1"] in [r["command"] for r in engine.runs]
        assert "libgomp.so.1" in result.files

    def test_dittos_container_build_carries_its_openmp_runtime(self, tmp_path, source):
        """Ditto's server built in the builder container (a host without a compiler) runs on the host: the executor
        copies the builder's libgomp.so.1 next to the programs, which find it through $ORIGIN, so the host needs no
        GCC runtime of its own (the same copy as for the device runtime)."""
        plan = plan_build(PC, purpose="host-runtime", placement="container", backend="cpu", catalog=CATALOG,
                          toolchain=ToolchainInfo(host_os="linux", host_arch="x86_64"))
        assert plan.verify["runtime_libs"] == ["libgomp.so.1"] and "$ORIGIN" in plan.options()["CMAKE_INSTALL_RPATH"]
        engine = FakeEngine(gcc=plan.builder["gcc"], libdir="/usr/lib/gcc/x86_64-linux-gnu/13/")
        result = BuildExecutor(engine=engine).build(plan, source=source, work=tmp_path / "w", jobs=1)
        assert ["gcc", "-print-file-name=libgomp.so.1"] in [r["command"] for r in engine.runs]
        assert "libgomp.so.1" in result.files and (result.bin_dir / "libgomp.so.1").is_file()

    @pytest.mark.skipif(os.name != "posix", reason="symlinks")
    def test_a_hostile_build_cannot_hand_the_host_files_from_outside_its_folder(self, tmp_path, source):
        """The build runs upstream's code; after it, the host reads only what lies inside the build folder."""
        outside = tmp_path / "outside"  # for example a folder on PATH with llama.cpp programs
        outside.mkdir()
        for name in self.cross_plan().targets:
            (outside / name).write_text("a program found elsewhere")

        def swap_bin(writable):
            shutil.rmtree(writable["/work/build"] / "bin")
            (writable["/work/build"] / "bin").symlink_to(outside)

        with pytest.raises(BuildError, match="outside"):
            BuildExecutor(engine=FakeEngine(tamper=swap_bin)).build(self.cross_plan(), source=source,
                                                                    work=tmp_path / "w", jobs=1)
        assert list((tmp_path / "w" / "bin").iterdir()) == []

    @pytest.mark.skipif(os.name != "posix", reason="symlinks")
    def test_cleaning_up_after_a_hostile_build_never_follows_its_links(self, tmp_path, source):
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("mine")
        outside.chmod(0o755)

        def link_home(writable):
            shutil.rmtree(writable["/work/home"])
            writable["/work/home"].symlink_to(outside)

        store = BuildStore(tmp_path / "state")
        executor = BuildExecutor(engine=FakeEngine(tamper=link_home))
        build = store.build(self.cross_plan(), source=source, executor=executor, verifier=FakeVerifier(ran=False),
                            jobs=1, host=("linux", "x86_64"))
        assert not (build.path / "home").exists() and not (build.path / "home").is_symlink()
        assert (outside / "keep.txt").read_text() == "mine" and stat.S_IMODE(outside.stat().st_mode) == 0o755

    def test_an_existing_builder_image_is_reused(self, tmp_path, source):
        engine = FakeEngine(have_image=True)
        BuildExecutor(engine=engine).build(self.cross_plan(), source=source, work=tmp_path / "w", jobs=1)
        assert engine.built == []

    def test_the_builder_gcc_must_match_the_catalog(self, tmp_path, source):
        with pytest.raises(BuildError, match="catalog names GCC 12.2"):
            BuildExecutor(engine=FakeEngine(gcc="13.3.0")).build(self.cross_plan(), source=source,
                                                                 work=tmp_path / "w", jobs=1)

    def test_no_docker_is_said_plainly(self, tmp_path, source):
        with pytest.raises(BuildError, match="Start Docker, or build natively"):
            BuildExecutor(engine=FakeEngine(reason="the Docker daemon is not reachable")).build(
                self.cross_plan(), source=source, work=tmp_path / "w", jobs=1)
        with pytest.raises(BuildError, match="needs Docker"):
            BuildExecutor().build(self.cross_plan(), source=source, work=tmp_path / "w2", jobs=1)

    def test_a_download_in_the_container_configure_fails(self, tmp_path, source):
        engine = FakeEngine(configure_lines=["-- Fetching KleidiAI"])
        with pytest.raises(BuildError, match="fetch or download"):
            BuildExecutor(engine=engine).build(self.cross_plan(), source=source, work=tmp_path / "w", jobs=1)
        assert len(engine.runs) == 2, "no build after the configure"

    def test_the_dockerfile_is_a_recipe_without_downloads(self):
        text = (ROOT / "orchestrator" / "runtime" / "build" / "builder.Dockerfile").read_text()
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        assert re.search(r"^ARG BASE$", code, re.M) and re.search(r"^FROM \$\{BASE\}$", code, re.M)
        assert not re.search(r"\b(curl|wget)\b|^ADD\s+https?:", code, re.M | re.I)
        assert "llama" not in code.split("LABEL")[0].lower(), "no llama.cpp in the image: it is built later"
        assert not LICENCE_FLAGS.search(code)
        for base in CATALOG.build_bases:
            assert re.search(r"@sha256:[0-9a-f]{64}$", base.image)


# --- verification ---------------------------------------------------------------------------------------------------

VERSION_NEW = "version: 0.6.0 (build 11429, commit d8123504)\nbuilt with GNU 13.3.0 for Linux x86_64\n"
DEVICES_CUDA = """load_backend: loaded CUDA backend
Available devices:
  CUDA0: NVIDIA GeForce RTX 3090 (24135 MiB, 23700 MiB free)
  CUDA1: NVIDIA GeForce RTX 2080 Ti (11003 MiB, 10800 MiB free)
"""
BENCH_JSON = """[
  {"build_commit": "d8123504", "n_prompt": 64, "n_gen": 0, "avg_ts": 210.5, "stddev_ts": 1.2, "backends": "CPU"},
  {"build_commit": "d8123504", "n_prompt": 0, "n_gen": 16, "avg_ts": 31.4, "stddev_ts": 0.3, "backends": "CPU"}
]
"""


def system_elf(*needs: str) -> Path:
    """A system program that needs the given libraries (None when this machine has none)."""
    for name in ("true", "ls", "cmake", "ninja", "c++filt", "gdb", "python3"):
        path = shutil.which(name)
        if not path:
            continue
        try:
            elf = read_elf(Path(path).resolve())
        except VerifyError:
            continue
        if all(n in elf.needed for n in needs):
            return Path(path).resolve()
    return None


class FakeRunner:
    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls: List[dict] = []

    def __call__(self, argv, timeout, cwd, env):
        self.calls.append({"argv": list(argv), "env": dict(env), "cwd": cwd})
        name = Path(argv[0]).name
        key = argv[1] if len(argv) > 1 else ""
        return self.answers.get((name, key), (0, "", ""))


def make_bin(tmp_path, names=("llama-server", "llama-bench"), elf=None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        if elf:
            shutil.copy2(elf, bin_dir / name)
        else:
            (bin_dir / name).write_text("#!/bin/sh\n")
            (bin_dir / name).chmod(0o755)
    return bin_dir


GOOD = {("llama-bench", "--version"): (0, "", VERSION_NEW), ("llama-server", "--version"): (0, "", VERSION_NEW),
        ("llama-bench", "--list-devices"): (0, "Available devices:\n  (none)\n", ""),
        ("llama-server", "--tools"): (1, "", "srv main: tools setup failed: subprocess is not enabled on this build")}


class TestParsers:

    def test_version(self):
        assert parse_version(VERSION_NEW) == (11429, "d8123504")
        assert parse_version("version: 11429 (d8123504)\nbuilt with cc") == (11429, "d8123504")
        assert parse_version("nothing") is None

    def test_devices(self):
        cuda = parse_devices(DEVICES_CUDA)
        assert [d.name for d in cuda] == ["CUDA0", "CUDA1"] and cuda[0].total_mib == 24135
        assert parse_devices("Available devices:\n  (none)\n") == []
        assert parse_devices("error: unknown argument") is None

    def test_bench_json(self):
        records = parse_bench_json("main: loading\n" + BENCH_JSON)
        assert records[1]["n_gen"] == 16 and records[1]["avg_ts"] == 31.4 and "build_commit" not in records[0]
        with pytest.raises(ValueError):
            parse_bench_json("no json")


class TestVerifier:

    def test_cpu_build_passes_every_check_with_a_cleared_environment(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LLAMA_ARG_TOOLS", "all")
        monkeypatch.setenv("HF_TOKEN", "hf_not_a_real_token")
        elf = system_elf("libc.so.6")
        if elf is None:
            pytest.skip("no system ELF program")
        runner = FakeRunner(GOOD)
        plan = with_targets(native_plan(), ["llama-server", "llama-bench"])
        report = Verifier(runner).verify(plan, make_bin(tmp_path, elf=elf))
        assert report.ok and report.ran_binaries, report.as_dict()
        assert report.check("format").ok and report.check("version").ok and report.check("devices").ok
        assert report.check("server-tools").ok
        assert report.check("smoke").ok is None and "D17" in report.check("smoke").detail
        for call in runner.calls:
            assert set(call["env"]) <= {"HOME", "PATH", "LANG", "LC_ALL", "USERPROFILE", "SYSTEMROOT", "SystemRoot",
                                        "WINDIR", "TEMP", "TMP"}
            assert Path(call["argv"][0]).parent == (tmp_path / "bin").resolve()
        tools_call = next(c for c in runner.calls if "--tools" in c["argv"])
        argv = tools_call["argv"]
        assert argv[argv.index("--host") + 1] == "127.0.0.1" and "--api-key-file" in argv
        assert not Path(argv[argv.index("-m") + 1]).exists()

    def test_wrong_commit_fails(self, tmp_path):
        answers = dict(GOOD)
        answers[("llama-bench", "--version")] = (0, "", VERSION_NEW.replace("d8123504", "0badc0de"))
        report = Verifier(FakeRunner(answers)).verify(native_plan(), make_bin(tmp_path, ["llama-server", "llama-cli",
                                                                                         "llama-bench",
                                                                                         "llama-perplexity"]),
                                                      run_binaries=True)
        assert not report.ok and "0badc0de" in report.check("version").detail

    def test_a_server_that_accepts_tools_fails(self, tmp_path):
        answers = dict(GOOD)
        answers[("llama-server", "--tools")] = (1, "", "error: failed to open model file")
        plan = with_targets(native_plan(), ["llama-server", "llama-bench"])
        report = Verifier(FakeRunner(answers)).verify(plan, make_bin(tmp_path))
        assert report.check("server-tools").ok is False and not report.ok

    def test_a_gpu_build_that_fell_back_to_the_cpu_fails(self, tmp_path):
        plan = with_targets(native_plan(), ["llama-server", "llama-bench"])
        plan = replace(plan, verify={**plan.verify, "devices": ["CUDA0"]})
        report = Verifier(FakeRunner(GOOD)).verify(plan, make_bin(tmp_path))
        assert "fell back to the CPU" in report.check("devices").detail
        answers = dict(GOOD)
        answers[("llama-bench", "--list-devices")] = (0, DEVICES_CUDA, "")
        assert Verifier(FakeRunner(answers)).verify(plan, make_bin(tmp_path)).check("devices").ok

    def test_a_binary_on_path_is_never_used(self, tmp_path, monkeypatch):
        decoy_dir = tmp_path / "decoy"
        decoy_dir.mkdir()
        marker = tmp_path / "decoy-ran"
        for name in ("llama-server", "llama-bench"):
            decoy = decoy_dir / name
            decoy.write_text(f"#!/bin/sh\ntouch {marker}\n")
            decoy.chmod(0o755)
        monkeypatch.setenv("PATH", f"{decoy_dir}{os.pathsep}{os.environ.get('PATH', '')}")
        empty = tmp_path / "bin"
        empty.mkdir()
        plan = with_targets(native_plan(), ["llama-server", "llama-bench"])
        report = Verifier().verify(plan, empty)
        assert not report.ok and "never used" in report.check("binaries").detail
        assert not marker.exists()
        with pytest.raises(VerifyError, match="never used"):
            binary_path(empty, "llama-server")
        with pytest.raises(VerifyError, match="not the name"):
            binary_path(empty, "../decoy/llama-server")

    @pytest.mark.skipif(os.name != "posix", reason="symlinks")
    def test_a_link_out_of_the_build_is_refused(self, tmp_path):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        outside = tmp_path / "elsewhere"
        outside.write_text("#!/bin/sh\n")
        outside.chmod(0o755)
        (bin_dir / "llama-server").symlink_to(outside)
        with pytest.raises(VerifyError):
            binary_path(bin_dir, "llama-server")

    def test_elf_reader_and_format_checks(self, tmp_path):
        elf = system_elf("libc.so.6")
        if elf is None:
            pytest.skip("no system ELF program")
        info = read_elf(elf)
        assert info.machine in ("x86_64", "arm64") and "libc.so.6" in info.needed and info.glibc_max
        with pytest.raises(VerifyError, match="not a readable ELF"):
            read_elf(Path(__file__))
        plan = with_targets(native_plan(), ["llama-server", "llama-bench"])
        if info.machine != plan.target["arch"]:
            pytest.skip("the fixture plan is for x86_64")
        too_old = replace(plan, verify={**plan.verify, "glibc_max": "2.17"})
        report = Verifier(FakeRunner(GOOD)).verify(too_old, make_bin(tmp_path / "a", elf=elf))
        assert "glibc" in report.check("format").detail and not report.ok
        arm = replace(plan, target={**plan.target, "arch": "arm64"})
        report = Verifier(FakeRunner(GOOD)).verify(arm, make_bin(tmp_path / "b", elf=elf), run_binaries=False)
        assert "not arm64" in report.check("format").detail

    def test_device_builds_must_not_need_libstdcxx(self, tmp_path):
        cxx = system_elf("libstdc++.so.6")
        if cxx is None:
            pytest.skip("no system program that needs libstdc++")
        plan = with_targets(native_plan(), ["llama-server", "llama-bench"])
        if read_elf(cxx).machine != plan.target["arch"]:
            pytest.skip("the fixture plan is for x86_64")
        static = replace(plan, verify={**plan.verify, "static_libstdcxx": True})
        report = Verifier(FakeRunner(GOOD)).verify(static, make_bin(tmp_path, elf=cxx))
        assert "statically" in report.check("format").detail

    def test_cross_builds_get_the_format_check_only(self, tmp_path):
        plan = plan_build(RK3566, purpose="device-runtime", placement="cross", catalog=CATALOG,
                          toolchain=ToolchainInfo(host_os="linux", host_arch="x86_64", cmake="3.25.1"))
        runner = FakeRunner(GOOD)
        report = Verifier(runner).verify(plan, make_bin(tmp_path, plan.verify["binaries"]))
        assert runner.calls == [] and not report.ran_binaries
        assert report.check("version").ok is None and "machine the build is for" in report.check("version").detail

    def test_smoke_model_is_checked_by_sha256_before_it_is_loaded(self, tmp_path):
        model = tmp_path / "smoke.gguf"
        model.write_bytes(b"GGUF tiny")
        digest = hashlib.sha256(b"GGUF tiny").hexdigest()
        answers = dict(GOOD)
        answers[("llama-bench", "-m")] = (0, BENCH_JSON, "")
        plan = with_targets(native_plan(), ["llama-server", "llama-bench"])
        runner = FakeRunner(answers)
        bad = Verifier(runner, smoke_sha256="0" * 64).verify(plan, make_bin(tmp_path / "a"),
                                                             smoke_model=SmokeModelFile(model, "0" * 64))
        assert "not loaded" in bad.check("smoke").detail
        assert not any(str(model) in c["argv"] for c in runner.calls)
        good = Verifier(FakeRunner(answers), smoke_sha256=digest).verify(plan, make_bin(tmp_path / "b"),
                                                                         smoke_model=SmokeModelFile(model, digest))
        assert good.check("smoke").ok and "tg 31.4 t/s" in good.check("smoke").detail
        answers[("llama-bench", "-m")] = (-4, "", "Illegal instruction")
        sigill = Verifier(FakeRunner(answers), smoke_sha256=digest).verify(plan, make_bin(tmp_path / "c"),
                                                                           smoke_model=SmokeModelFile(model, digest))
        assert "SIGILL" in sigill.check("smoke").detail

    def test_only_the_catalog_smoke_model_ever_runs(self, tmp_path):
        """Decision D23: a model that is not the catalog's smoke-test model never runs natively here, even when its
        file matches the sha256 the caller passes along with it."""
        model = tmp_path / "someones-conversion.gguf"
        model.write_bytes(b"GGUF not from the catalog")
        digest = hashlib.sha256(model.read_bytes()).hexdigest()
        plan = with_targets(native_plan(), ["llama-server", "llama-bench"])
        for verifier_sha in (None, "f" * 64):  # no smoke model pinned / another one pinned
            runner = FakeRunner({**GOOD, ("llama-bench", "-m"): (0, BENCH_JSON, "")})
            report = Verifier(runner, smoke_sha256=verifier_sha).verify(
                plan, make_bin(tmp_path / str(verifier_sha)[:4]), smoke_model=SmokeModelFile(model, digest))
            assert report.check("smoke").ok is False and "D23" in report.check("smoke").detail
            assert not any(str(model) in c["argv"] for c in runner.calls)
        assert CATALOG.smoke_model is None or CATALOG.smoke_model.sha256 != digest
        runner = FakeRunner({**GOOD, ("llama-bench", "-m"): (0, BENCH_JSON, "")})
        report = Verifier(runner).verify(plan, make_bin(tmp_path / "cat"), smoke_model=SmokeModelFile(model, digest))
        assert report.check("smoke").ok is False, "the default reads the catalog's pin"

    def test_smoke_model_file_needs_a_pinned_downloaded_model(self, tmp_path):
        assert smoke_model_file(None, tmp_path) is None


# --- the launch policy -----------------------------------------------------------------------------------------------

class TestServerPolicy:

    def test_forbidden_flags_in_both_spellings(self):
        assert server_policy.forbidden_in(["llama-server", "--tools=all", "-m", "x", "--agent"]) == ["--agent",
                                                                                                     "--tools"]
        assert server_policy.forbidden_in(["llama-server", "-m", "model.gguf", "--no-ui"]) == []
        with pytest.raises(server_policy.PolicyError):
            server_policy.check_command(["llama-bench", "--rpc", "host:1"])

    def test_one_list_for_every_launcher(self):
        from orchestrator.runtime import llama_server
        assert llama_server.FORBIDDEN_FLAGS is server_policy.FORBIDDEN_FLAGS
        assert llama_server.server_env is server_policy.server_env
        assert llama_server.forbidden_in is server_policy.forbidden_in
        with pytest.raises(llama_server.LocalRuntimeError):  # a value that is a forbidden option is refused too
            llama_server.build_server_command(Path("s"), Path("m"), 1, Path("k"), 4096, "--tools", False, 0)
        verify_source = (ROOT / "orchestrator" / "runtime" / "build" / "verify.py").read_text()
        assert "check_command" in verify_source and "FORBIDDEN" not in verify_source.replace("forbidden", "")

    def test_cleared_environment_inherits_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LLAMA_ARG_TOOLS", "all")
        monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid")
        env = server_policy.cleared_env(tmp_path)
        assert env["HOME"] == str(tmp_path) and "LLAMA_ARG_TOOLS" not in env and "HTTPS_PROXY" not in env

    def test_the_tools_check_needs_a_missing_model(self, tmp_path):
        model = tmp_path / "m.gguf"
        model.write_text("x")
        with pytest.raises(server_policy.PolicyError):
            server_policy.server_tools_check_command(Path("srv"), model, 1, tmp_path / "k")

    def test_the_tools_check_is_one_command_here_and_on_a_board(self, tmp_path):
        """The build verification here and the checks on a board (device/remote.py) run the same --tools check:
        `--tools all` and a missing model, and otherwise exactly the rules every llama-server command keeps."""
        here = server_policy.server_tools_check_command(Path("srv"), tmp_path / "none.gguf", 8081, tmp_path / "k")
        board = server_policy.tools_check_args("srv", str(tmp_path / "none.gguf"), 8081, str(tmp_path / "k"))
        assert here == board and here[1:3] == list(server_policy.SERVER_TOOLS_CHECK)
        server_policy.check_server_command([here[0], *here[3:]])
        assert set(server_policy.SERVER_FIXED) <= set(here)


# --- the store -------------------------------------------------------------------------------------------------------

class FakeExecutor:
    def __init__(self, fail=None, extra=()):
        self.fail, self.extra, self.calls = fail, extra, 0

    def build(self, plan, *, source, work, jobs, toolchain=None, ccache_dir=None):
        self.calls += 1
        dirs = BuildExecutor._prepare(work)
        (dirs["logs"] / "build.log").write_text("built\n")
        (dirs["build"] / "CMakeCache.txt").write_text("cache")
        if self.fail:
            raise BuildError(self.fail, dirs["logs"] / "build.log")
        files = {}
        for name in plan.targets + tuple(self.extra):
            (dirs["bin"] / name).write_text(f"#!/bin/sh\necho {name}\n")
            (dirs["bin"] / name).chmod(0o755)
            files[name] = hashlib.sha256((dirs["bin"] / name).read_bytes()).hexdigest()
        return BuildResult(build_id=plan.build_id, work=Path(work), bin_dir=dirs["bin"], files=files, links={},
                           duration_s=1.0, jobs=jobs, placement=plan.placement)


class FakeVerifier:
    def __init__(self, ok=True, ran=True):
        self.ok, self.ran, self.calls = ok, ran, []

    def verify(self, plan, bin_dir, **kw):
        self.calls.append((plan, Path(bin_dir), kw))
        return VerifyReport([Check("format", True), Check("version", True if self.ok else False, "pin")],
                            ran_binaries=self.ran)


def store_build(store, tmp_path, plan=None, **kw):
    plan = plan or native_plan()
    return store.build(plan, source=tmp_path, executor=kw.pop("executor", FakeExecutor()),
                       verifier=kw.pop("verifier", FakeVerifier()), jobs=2, host=("linux", "x86_64"), **kw)


class TestStore:

    def test_a_build_is_stamped_and_reused_never_rebuilt(self, tmp_path):
        store = BuildStore(tmp_path / "state")
        ex = FakeExecutor()
        build = store_build(store, tmp_path, executor=ex, perf_key="p" * 64)
        assert build.path == store.builds_dir / build.build_id[:16] and build.verified
        assert sorted(p.name for p in build.path.iterdir()) == ["bin", "build.json", "logs", "manifest.json"]
        assert build.build["plan"]["build_id"] == build.build_id and build.build["perf_key"] == "p" * 64
        assert build.manifest["origin"] == "self-built" and build.manifest["build_host"] == {"os": "linux",
                                                                                          "arch": "x86_64"}
        assert set(build.manifest["files"]) == set(native_plan().targets) and build.commit == PIN.commit
        stamp = (build.path / "manifest.json").read_text()
        again = store_build(store, tmp_path, executor=ex)
        assert ex.calls == 1 and again.build_id == build.build_id
        assert (build.path / "manifest.json").read_text() == stamp, "a reused build is never stamped again"
        assert list(store.builds_dir.glob(".staging-*")) == []

    def test_a_failed_build_is_not_stored_and_keeps_its_logs(self, tmp_path):
        store = BuildStore(tmp_path / "state")
        with pytest.raises(BuildFailed) as e:
            store_build(store, tmp_path, executor=FakeExecutor(fail="build failed (exit 1)"))
        assert e.value.log and (e.value.log / "build.log").read_text() == "built\n"
        with pytest.raises(BuildFailed, match="verification") as e:
            store_build(store, tmp_path, verifier=FakeVerifier(ok=False))
        assert e.value.report is not None
        assert store.list_builds() == [] and list(store.builds_dir.glob(".staging-*")) == []

    def test_changed_files_are_found_and_the_build_is_rebuilt(self, tmp_path):
        store = BuildStore(tmp_path / "state")
        build = store_build(store, tmp_path)
        (build.bin_dir / "llama-server").write_text("tampered")
        (build.bin_dir / "extra").write_text("x")
        problems = store.check_files(build)
        assert "llama-server was changed" in problems and "extra is not part of the build" in problems
        assert store.find(native_plan()) is None
        with pytest.raises(StoreError, match="does not match its manifest"):
            store.activate(build.build_id)
        ex = FakeExecutor()
        store_build(store, tmp_path, executor=ex)
        assert ex.calls == 1 and store.check_files(store.get(build.build_id)) == []

    def test_only_verified_builds_are_activated(self, tmp_path):
        store = BuildStore(tmp_path / "state")
        (store.root).mkdir(parents=True)
        store.active_file.write_text(json.dumps({"variant": "cpu", "tier": "small"}))
        build = store_build(store, tmp_path)
        store.activate(build.build_id)
        store.activate(build.build_id, role="fallback")
        data = json.loads(store.active_file.read_text())
        assert data["build_id"] == data["fallback_build_id"] == build.build_id
        assert data["variant"] == "cpu" and data["tier"] == "small", "Ditto's keys stay"
        if os.name == "posix":
            assert stat.S_IMODE(store.active_file.stat().st_mode) == 0o600
        cross = plan_build(RK3566, purpose="device-runtime", placement="cross", catalog=CATALOG,
                           toolchain=ToolchainInfo(host_os="linux", host_arch="x86_64", cmake="3.25.1"))
        other = store_build(store, tmp_path, plan=cross, verifier=FakeVerifier(ran=False))
        with pytest.raises(StoreError, match="has not passed its verification on this machine"):
            store.activate(other.build_id)
        with pytest.raises(StoreError, match="no stored build"):
            store.activate("0" * 64)

    def test_the_list_never_offers_active_fallback_running_or_referenced_builds(self, tmp_path, monkeypatch):
        store = BuildStore(tmp_path / "state")
        builds = []
        for n in range(7):
            plan = native_plan(toolchain=replace(GCC, cmake=f"3.28.{n}"))
            build = store_build(store, tmp_path, plan=plan)
            manifest = dict(build.manifest)
            manifest["built_at"] = f"2026-10-0{n + 1}T00:00:00+00:00"
            (build.path / "manifest.json").write_text(json.dumps(manifest))
            builds.append(build.build_id)
        oldest, old2, old3, old4, mid, newer, newest = builds
        store.activate(oldest)
        store.activate(old2, role="fallback")
        monkeypatch.setattr(BuildStore, "running_build_ids", lambda self: {old3})
        (store.builds_dir / ".staging-0123456789abcdef-dead").mkdir()
        # The framework deletes no build by itself (round 5 f): the person picks from this list.
        assert [b.build_id for b in store.removable(keep=[old4])] == [newest, newer, mid]
        done, kept = store.remove([mid, oldest, old3])
        assert done == [mid] and [b for b, _ in kept] == [oldest, old3], "in use: kept, and said why"
        left = {b.build_id for b in store.list_builds()}
        assert left == {oldest, old2, old3, old4, newer, newest}
        assert list(store.builds_dir.glob(".staging-*")) != [], "removing builds never sweeps anything else"
        store.sweep_leftovers()
        assert list(store.builds_dir.glob(".staging-*")) == [], "leftovers of interrupted builds go"

    def test_running_server_protects_its_build(self, tmp_path, monkeypatch):
        from orchestrator.runtime import llama_server
        store = BuildStore(tmp_path / "state")
        build = store_build(store, tmp_path)
        proc = object()
        monkeypatch.setattr(llama_server, "owned_process", lambda state: proc)
        monkeypatch.setattr(llama_server.LocalLLMRuntime, "_uses",
                            staticmethod(lambda p, folder: p is proc and folder == build.path))
        assert store.running_build_ids() == {build.build_id}
        assert store.removable() == [] and store.remove([build.build_id])[0] == []
        assert build.path.is_dir()

    def test_export_and_import_round_trip_with_confirmation(self, tmp_path):
        source_store = BuildStore(tmp_path / "pc")
        build = store_build(source_store, tmp_path)
        archive = source_store.export_build(build.build_id, tmp_path / "out" / "llama-build.tar.gz")
        with tarfile.open(archive) as tar:
            names = sorted(tar.getnames())
        assert names == ["bin", *sorted(f"bin/{n}" for n in build.manifest["files"]), "build.json", "manifest.json"]
        target = BuildStore(tmp_path / "laptop")
        verifier = FakeVerifier()
        asked = []
        with pytest.raises(StoreError, match="not confirmed"):
            target.import_build(archive, pin=PIN, verifier=verifier, confirm=lambda info: asked.append(info) or False,
                                host=("linux", "x86_64"))
        assert verifier.calls == [], "no program of an unconfirmed import runs"
        assert asked[0]["origin"] == "imported" and asked[0]["built_on"] == {"os": "linux", "arch": "x86_64"}
        imported = target.import_build(archive, pin=PIN, verifier=verifier, confirm=lambda info: True,
                                       host=("linux", "x86_64"))
        assert imported.origin == "imported" and imported.manifest["imported"]["confirmed"] is True
        assert imported.verified and target.check_files(imported) == [] and len(verifier.calls) == 1

    def _repack(self, tmp_path, archive, change):
        work = tmp_path / "repack"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir()
        with tarfile.open(archive) as tar:
            tar.extractall(work, filter="data")
        change(work)
        out = tmp_path / "changed.tar.gz"
        with tarfile.open(out, "w:gz") as tar:
            for item in sorted(work.iterdir()):
                tar.add(item, arcname=item.name)
        return out

    @pytest.mark.parametrize("change, message", [
        (lambda w: (w / "bin" / "llama-server").write_text("evil"), "does not match the manifest"),
        (lambda w: (w / "bin" / "llama-extra").write_text("x"), "not exactly those of the manifest"),
        (lambda w: (w / "notes.txt").write_text("x"), "more than a build"),
        (lambda w: (w / "build.json").write_text(
            (w / "build.json").read_text().replace(load_catalog().llama_cpp.source.commit, "f" * 40)),
         "pinned to"),
        (lambda w: (w / "manifest.json").unlink(), "no valid build.json"),
    ])
    def test_imports_that_do_not_match_are_refused_before_anything_runs(self, tmp_path, change, message):
        source_store = BuildStore(tmp_path / "pc")
        build = store_build(source_store, tmp_path)
        archive = source_store.export_build(build.build_id, tmp_path / "b.tar.gz")
        changed = self._repack(tmp_path, archive, change)
        verifier = FakeVerifier()
        with pytest.raises(StoreError, match=message):
            BuildStore(tmp_path / "laptop").import_build(changed, pin=PIN, verifier=verifier,
                                                         confirm=lambda info: True, host=("linux", "x86_64"))
        assert verifier.calls == []

    def test_an_archive_that_unpacks_to_more_than_the_limit_is_refused_before_extraction(self, tmp_path, monkeypatch):
        from orchestrator.runtime.build import store as store_module
        monkeypatch.setattr(store_module, "MAX_IMPORT_BYTES", 100_000)
        big = tmp_path / "zeros"
        big.write_bytes(b"\0" * 1_000_000)  # packs to a few KiB
        archive = tmp_path / "bomb.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(big, arcname="bin/llama-server")
        assert archive.stat().st_size < 100_000
        store = BuildStore(tmp_path / "laptop")
        with pytest.raises(StoreError, match="unpack"):
            store.import_build(archive, pin=PIN, verifier=FakeVerifier(), confirm=lambda i: True,
                               host=("linux", "x86_64"))
        assert not any(store.builds_dir.rglob("llama-server")), "nothing was extracted"

    def test_an_import_for_another_machine_is_refused(self, tmp_path):
        source_store = BuildStore(tmp_path / "pc")
        build = store_build(source_store, tmp_path)
        archive = source_store.export_build(build.build_id, tmp_path / "b.tar.gz")
        with pytest.raises(StoreError, match="this machine is linux/arm64"):
            BuildStore(tmp_path / "board").import_build(archive, pin=PIN, verifier=FakeVerifier(),
                                                        confirm=lambda info: True, host=("linux", "arm64"))

    def test_build_ids_are_validated(self, tmp_path):
        store = BuildStore(tmp_path / "state")
        for bad in ("../x", "ABCDEF0123456789", "1234"):
            with pytest.raises(StoreError):
                store.get(bad)


# --- doctor ----------------------------------------------------------------------------------------------------------

class TestDoctor:

    def framework(self, tmp_path):
        class Config:
            user_state_dir = tmp_path / "state"
        return type("Framework", (), {"config": Config()})()

    def run_check(self, tmp_path, monkeypatch, native_ready=True):
        from orchestrator.Core import doctor
        problems = () if native_ready else ("Ninja was not found (install ninja-build)",)
        detected = tc.DetectedToolchain(info=GCC, tools={"cmake": "/usr/bin/cmake", "docker": "/usr/bin/docker"},
                                        problems=problems)
        monkeypatch.setattr(tc, "detect_toolchain", lambda full=True: detected)
        return {f.code: f for f in doctor.check_runtime(self.framework(tmp_path))}

    def test_nothing_built_yet(self, tmp_path, monkeypatch):
        from orchestrator.Core.doctor import Severity
        found = self.run_check(tmp_path, monkeypatch, native_ready=False)
        assert found["runtime.build.none"].severity == Severity.INFO
        tool = found["runtime.toolchain"]
        assert tool.severity == Severity.INFO and "Ninja" in tool.message and "builder container" in tool.fix_hint
        assert "never installs" in tool.fix_hint

    def test_active_build_states(self, tmp_path, monkeypatch):
        from orchestrator.Core.doctor import Severity
        store = BuildStore(tmp_path / "state")
        build = store_build(store, tmp_path)
        store.activate(build.build_id)
        found = self.run_check(tmp_path, monkeypatch)
        assert found["runtime.build"].severity == Severity.OK and "self-built build" in found["runtime.build"].message
        assert found["runtime.toolchain"].severity == Severity.OK
        stamp = json.loads((build.path / "build.json").read_text())
        stamp["plan"]["source"]["commit"] = "a" * 40
        (build.path / "build.json").write_text(json.dumps(stamp))
        assert self.run_check(tmp_path, monkeypatch)["runtime.build.outdated"].severity == Severity.WARN
        (build.bin_dir / "llama-bench").write_text("changed")
        assert self.run_check(tmp_path, monkeypatch)["runtime.build.damaged"].severity == Severity.ERROR

    def test_prebuilt_runtime_is_labelled_not_self_built(self, tmp_path, monkeypatch):
        prebuilt = tmp_path / "state" / "runtime" / "llama.cpp" / "b11429-cpu"
        prebuilt.mkdir(parents=True)
        (prebuilt / "runtime.json").write_text("{}")
        found = self.run_check(tmp_path, monkeypatch)
        assert "not self-built" in found["runtime.prebuilt"].message

    def test_the_check_is_a_default_check(self):
        from orchestrator.Core import doctor
        assert doctor.check_runtime in doctor.DEFAULT_CHECKS


def test_container_sandbox_is_shared_with_the_conversion_stages():
    from orchestrator.utils import sandbox
    options = sandbox.container_sandbox((1234, 1234), working_dir="/work", mem_limit="8g")
    assert options == {"user": "1234:1234", "network_mode": "none", "cap_drop": ["ALL"],
                       "security_opt": ["no-new-privileges:true"], "read_only": True,
                       "tmpfs": {"/tmp": "rw,nosuid,nodev,size=1g"}, "pids_limit": 4096, "working_dir": "/work",
                       "mem_limit": "8g"}
    builder = (ROOT / "orchestrator" / "Core" / "builder.py").read_text()
    assert "container_sandbox(" in builder, "the conversion stages use the same sandbox"


def test_probe_profile_reaches_toolchain_info():
    profile = parse_profile((PROBES / "schema2_pc_two_nvidia.txt").read_text())
    info = ToolchainInfo.from_profile(profile)
    assert info.compiler == "gcc-13.3.0" and info.openmp is None


# --- review findings (regressions) -----------------------------------------------------------------------------------

class ElfExecutor(FakeExecutor):
    """Like FakeExecutor, but every program is a real ELF program of this machine, so the format check can pass."""

    def __init__(self, elf):
        super().__init__()
        self.elf = elf

    def build(self, plan, **kw):
        result = super().build(plan, **kw)
        for name in plan.targets:
            shutil.copy2(self.elf, result.bin_dir / name)
            result.files[name] = hashlib.sha256((result.bin_dir / name).read_bytes()).hexdigest()
        return result


def _edit_stamp(work, change, name="build.json"):
    data = json.loads((work / name).read_text())
    change(data)
    (work / name).write_text(json.dumps(data))


class TestImportIsVerifiedAgainstThePin:
    """An imported archive is unsigned: what it says about itself (its expected version, its list of programs, its
    build ID) must not decide how it is checked."""

    def archive(self, tmp_path):
        elf = system_elf("libc.so.6")
        if elf is None or read_elf(elf).machine != "x86_64":
            pytest.skip("needs an x86_64 system ELF program (the fixture plan is for x86_64)")
        store = BuildStore(tmp_path / "pc")
        build = store_build(store, tmp_path, executor=ElfExecutor(elf))
        return store.export_build(build.build_id, tmp_path / "b.tar.gz")

    def do_import(self, tmp_path, archive, answers):
        return BuildStore(tmp_path / "laptop").import_build(archive, pin=PIN, verifier=Verifier(FakeRunner(answers)),
                                                            confirm=lambda info: True, host=("linux", "x86_64"))

    def test_the_untouched_archive_passes_the_real_verifier(self, tmp_path):
        imported = self.do_import(tmp_path, self.archive(tmp_path), GOOD)
        assert imported.verified and imported.origin == "imported"

    def test_a_forged_version_expectation_does_not_let_another_commit_in(self, tmp_path):
        archive = self.archive(tmp_path)
        forged = TestStore()._repack(tmp_path, archive, lambda w: _edit_stamp(
            w, lambda d: d["plan"]["verify"].update(version={"build_number": 99, "commit": "0badc0de"})))
        other = "version: 0.0.1 (build 99, commit 0badc0de)\n"
        answers = {**GOOD, ("llama-bench", "--version"): (0, "", other), ("llama-server", "--version"): (0, "", other)}
        with pytest.raises(StoreError, match="verification") as e:
            self.do_import(tmp_path, forged, answers)
        assert "11429" in str(e.value) and PIN.short_commit in str(e.value)

    def test_a_server_left_out_of_the_program_list_is_still_checked(self, tmp_path):
        archive = self.archive(tmp_path)
        forged = TestStore()._repack(tmp_path, archive, lambda w: _edit_stamp(
            w, lambda d: d["plan"]["verify"].update(binaries=["llama-bench"])))
        answers = {**GOOD, ("llama-server", "--tools"): (1, "", "error: failed to open model file")}
        with pytest.raises(StoreError, match="--tools"):
            self.do_import(tmp_path, forged, answers)

    def test_the_build_id_must_be_the_hash_of_the_plan(self, tmp_path):
        archive = self.archive(tmp_path)
        forged = TestStore()._repack(tmp_path, archive, lambda w: _edit_stamp(
            w, lambda d: d["plan"]["steps"][0]["options"].update(GGML_NATIVE="OFF")))
        verifier = FakeVerifier()
        with pytest.raises(StoreError, match="build ID"):
            BuildStore(tmp_path / "laptop").import_build(forged, pin=PIN, verifier=verifier, confirm=lambda i: True,
                                                         host=("linux", "x86_64"))
        assert verifier.calls == []

    def test_a_plan_with_subprocess_support_is_refused_even_with_a_matching_id(self, tmp_path):
        archive = self.archive(tmp_path)
        plan = native_plan()
        step = plan.steps[0]
        options = dict(step.options, LLAMA_SUBPROCESS="ON")
        bad = replace(plan, steps=(replace(step, options=tuple(sorted(options.items()))),))
        bad = replace(bad, build_id=build_id_of(bad.id_inputs()))

        def forge(work):
            _edit_stamp(work, lambda d: d.update(build_id=bad.build_id, plan=bad.as_dict()))
            _edit_stamp(work, lambda d: d.update(build_id=bad.build_id), "manifest.json")

        forged = TestStore()._repack(tmp_path, archive, forge)
        verifier = FakeVerifier()
        with pytest.raises(StoreError, match="LLAMA_SUBPROCESS"):
            BuildStore(tmp_path / "laptop").import_build(forged, pin=PIN, verifier=verifier, confirm=lambda i: True,
                                                         host=("linux", "x86_64"))
        assert verifier.calls == []


class TestVerifierChecksWhatIsThere:

    def test_every_program_in_the_folder_is_checked_not_only_the_listed_ones(self, tmp_path):
        plan = with_targets(native_plan(), ["llama-bench"])
        answers = {**GOOD, ("llama-server", "--tools"): (1, "", "error: failed to open model file")}
        report = Verifier(FakeRunner(answers)).verify(plan, make_bin(tmp_path), run_binaries=True)
        assert report.check("server-tools").ok is False and not report.ok

    def test_a_gpu_plan_without_a_device_list_still_needs_its_device(self, tmp_path):
        plan = with_targets(native_plan(), ["llama-server", "llama-bench"])
        plan = replace(plan, backend="vulkan", verify={**plan.verify, "devices": []})
        report = Verifier(FakeRunner(GOOD)).verify(plan, make_bin(tmp_path), run_binaries=True)
        assert report.check("devices").ok is False and "Vulkan0" in report.check("devices").detail


class TestLauncherEnvironment:
    """Decision D22: every launcher starts llama-server with a cleared environment, Ditto's too."""

    def test_ditto_server_environment_is_built_from_nothing(self):
        caller = {"PATH": "/opt/other/bin:/usr/bin", "HOME": "/home/u", "CUDA_VISIBLE_DEVICES": "1",
                  "GGML_VK_VISIBLE_DEVICES": "0", "LLAMA_ARG_TOOLS": "all",
                  "GGML_BACKEND_PATH": "/tmp/backend.so",          # loads a library into the server
                  "AIP_MODE": "PREDICTION", "AIP_HTTP_PORT": "80",  # moves the server's port
                  "HTTPS_PROXY": "http://proxy.invalid", "LD_PRELOAD": "/tmp/x.so", "OPENAI_API_KEY": "sk-x"}
        env = server_policy.server_env(caller)
        assert env["CUDA_VISIBLE_DEVICES"] == "1" and env["GGML_VK_VISIBLE_DEVICES"] == "0"
        for name in ("LLAMA_ARG_TOOLS", "GGML_BACKEND_PATH", "AIP_MODE", "AIP_HTTP_PORT", "HTTPS_PROXY",
                     "LD_PRELOAD", "OPENAI_API_KEY"):
            assert name not in env, name
        # HOME as a path of this computer (on Windows '/home/u' is '\\home\\u' of the current drive)
        assert env["PATH"] == server_policy.CLEARED_PATH and env["HOME"] == str(Path("/home/u"))
        assert set(env) <= set(server_policy.cleared_env(Path("/home/u"))) | server_policy.SERVER_ENV_PASS


@pytest.mark.skipif(os.name != "posix", reason="symlinks")
def test_removing_a_tree_never_follows_a_link(tmp_path):
    from orchestrator.runtime.build.source import remove_tree
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("mine")
    outside.chmod(0o755)
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "home").symlink_to(outside)
    remove_tree(staging / "home")
    assert not (staging / "home").is_symlink() and not (staging / "home").exists()
    assert (outside / "keep.txt").read_text() == "mine" and stat.S_IMODE(outside.stat().st_mode) == 0o755
    ro = staging / "ro"
    (ro / "sub").mkdir(parents=True)
    (ro / "sub" / "link").symlink_to(outside)
    (ro / "sub").chmod(0o500)
    remove_tree(ro)  # read-only folders are still removed, the link inside them without following it
    assert not ro.exists() and stat.S_IMODE(outside.stat().st_mode) == 0o755


@POSIX
def test_a_plan_with_openmp_fails_when_cmake_finds_none(tmp_path, fake_tools, source):
    """ggml only warns "OpenMP not found" and builds without it: the binaries would not be the ones the build ID
    names (GGML_OPENMP=ON), so the build stops after configure."""
    tools, detected = fake_tools
    plan = native_plan()
    assert plan.options()["GGML_OPENMP"] == "ON"
    control(tools, configure_lines=["CMake Warning at ggml/src/CMakeLists.txt:347 (message):", "  OpenMP not found"],
            cache="CMAKE_BUILD_TYPE:STRING=Release\nGGML_OPENMP_ENABLED:INTERNAL=OFF\n")
    with pytest.raises(BuildError, match="OpenMP"):
        BuildExecutor().build(plan, source=source, work=tmp_path / "w", jobs=1, toolchain=detected)
    assert [c["argv"][1] for c in calls(tools)] == ["-S"], "the build step never runs"
    control(tools, cache="GGML_OPENMP_ENABLED:INTERNAL=ON\n")
    BuildExecutor().build(plan, source=source, work=tmp_path / "w2", jobs=1, toolchain=detected)


@POSIX
def test_parallel_jobs_follow_the_memory_when_the_caller_names_none(tmp_path, fake_tools, source):
    tools, detected = fake_tools
    small = replace(detected, cpus=8, ram_available_mib=2 * CATALOG.build_jobs.per_job_mib("cpu") + 100)
    result = BuildExecutor().build(native_plan(), source=source, work=tmp_path / "w", jobs=None, toolchain=small)
    build = calls(tools)[-1]["argv"]
    assert build[build.index("--parallel") + 1] == "2" and result.jobs == 2
