#!/usr/bin/env python3
"""
What the build host offers for a llama.cpp build: toolchain detection (the I/O half of the recipe).

plan_build() is pure; everything it needs to know about the build machine is gathered here into a ToolchainInfo
(versions only): the C/C++ compiler, CMake, ccache, what -march=native (x86) or -mcpu=native (ARM) expands to, and
whether the compiler builds OpenMP code (Clang needs libomp, and CMake drops OpenMP quietly without it). The paths of
the tools, the logical CPUs and the available memory for -j stay in DetectedToolchain, next to the ToolchainInfo:
they do not change the binaries.

Detection only looks: it never installs anything and never accepts a licence (decision D5). The tools it finds are
the user's toolchain for a native build; llama.cpp binaries found on PATH are never used for anything (design P1).
Every tool runs with an argument list, an empty stdin, a time limit and a cleared environment.
"""

import hashlib
import os
import platform
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from orchestrator.runtime.build.recipe import ToolchainInfo
from orchestrator.runtime.hardware import normalize_platform
from orchestrator.runtime.hardware_probe import compiler_id

MIB = 1024 ** 2
# The executor passes several targets to one `cmake --build --target a b` call (CMake 3.15); llama.cpp at the pin needs
# 3.14.
CMAKE_MIN = (3, 15)
TOOL_TIMEOUT = 30

# (argv, timeout, cwd) -> (returncode, stdout, stderr); returncode -1: not found, not runnable or timed out.
ToolRunner = Callable[[Sequence[str], float, Optional[str]], Tuple[int, str, str]]
Which = Callable[[str], Optional[str]]

_RE_CMAKE = re.compile(r"cmake version (\d+(?:\.\d+){1,3})", re.ASCII)
_RE_NUMBER = re.compile(r"(\d+(?:\.\d+){1,3})", re.ASCII)
_OPENMP_TEST = """#include <omp.h>
int main(void) {
    int n = 0;
#pragma omp parallel reduction(+:n)
    n += 1;
    return (n > 0 && omp_get_max_threads() > 0) ? 0 : 1;
}
"""


class ToolchainError(RuntimeError):
    """A tool the build needs is missing; the message names it and the way out."""


@dataclass(frozen=True)
class DetectedToolchain:
    """ToolchainInfo plus what the executor needs and what does not change the binaries."""
    info: ToolchainInfo
    tools: Mapping[str, str] = field(default_factory=dict)   # cmake, ninja, cc, cxx, ccache, git, docker
    cpus: int = 1
    ram_available_mib: int = 0
    problems: Tuple[str, ...] = ()   # why a native build is not possible here
    notes: Tuple[str, ...] = ()

    @property
    def native_ready(self) -> bool:
        return not self.problems

    def tool(self, name: str) -> str:
        path = self.tools.get(name)
        if not path:
            raise ToolchainError(f"{name} was not found on this machine")
        return path

    def summary(self) -> str:
        info = self.info
        parts = [info.compiler or "no C/C++ compiler", f"CMake {info.cmake}" if info.cmake else "no CMake",
                 "Ninja" if "ninja" in self.tools else "no Ninja"]
        return ", ".join(parts)


def default_tool_runner(argv: Sequence[str], timeout: float, cwd: Optional[str] = None) -> Tuple[int, str, str]:
    """Runs a detection command with a cleared environment; a missing tool or a hang gives -1."""
    env = {"PATH": os.environ.get("PATH", os.defpath), "LANG": "C", "LC_ALL": "C",
           "HOME": tempfile.gettempdir()}
    if os.name == "nt":  # pragma: no cover - Windows
        env.update({k: os.environ[k] for k in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP") if k in os.environ})
    from orchestrator.utils.helpers import execute_command
    return execute_command(argv, cwd=cwd, timeout=timeout, env=env)


# --- parsers ------------------------------------------------------------------------------------------------------

def parse_cmake_version(text: str) -> Optional[str]:
    match = _RE_CMAKE.search(text or "")
    return match.group(1) if match else None


def parse_plain_version(text: str) -> Optional[str]:
    """The first dotted number of a tool's first line (ninja --version: 1.11.1)."""
    first = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    match = _RE_NUMBER.search(first)
    return match.group(1) if match else None


def _version_tuple(text: Optional[str]) -> Tuple[int, ...]:
    match = _RE_NUMBER.search(text or "")
    return tuple(int(p) for p in match.group(1).split(".")) if match else ()


def parse_native_expansion(text: str) -> Optional[str]:
    """What -march=native / -mcpu=native expands to, from `cc -march=native -E -v -` (GCC's cc1 line or Clang's
    -cc1 line): the CPU name and a hash of the feature flags, for example
    "march=skylake mtune=skylake features=3f2a...". None when the output has no compiler line."""
    for line in (text or "").splitlines():
        if not re.search(r"(?:^|[/\s\"])cc1(?:plus)?(?:\s|\"|$)|(?:^|\s)\"?-cc1\"?(?:\s|$)", line):
            continue
        tokens = [t.strip('"') for t in line.split()]
        names: Dict[str, str] = {}
        features: List[str] = []
        for i, token in enumerate(tokens):
            for flag in ("-march=", "-mcpu=", "-mtune="):
                if token.startswith(flag):
                    names[flag[1:-1]] = token[len(flag):]
                    break
            else:
                if token == "-target-cpu" and i + 1 < len(tokens):
                    names["cpu"] = tokens[i + 1]
                elif token == "-target-feature" and i + 1 < len(tokens):
                    features.append(tokens[i + 1])
                elif token.startswith("-m") and token not in ("-mllvm",) and "=" not in token:
                    features.append(token)
                elif token.startswith("--param=") and "cache" in token:
                    features.append(token)
        if not names:
            continue
        parts = [f"{k}={re.sub(r'[^A-Za-z0-9._+-]', '', v)[:64]}" for k, v in sorted(names.items())]
        digest = hashlib.sha256("\n".join(sorted(set(features))).encode("utf-8")).hexdigest()[:16]
        return " ".join(parts + [f"features={digest}"])
    return None


# --- detection ----------------------------------------------------------------------------------------------------

def host_resources() -> Tuple[int, int]:
    """(logical CPUs this process may use, available RAM in MiB)."""
    try:
        cpus = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):  # pragma: no cover - macOS, Windows
        cpus = os.cpu_count() or 1
    try:
        import psutil
        ram = int(psutil.virtual_memory().available // MIB)
    except Exception:  # pragma: no cover - psutil is a dependency
        ram = 0
    return max(1, cpus), max(0, ram)


def parallel_jobs(cpus: int, ram_available_mib: int, per_job_mib: int, limit: Optional[int] = None) -> int:
    """-j for a build: min(logical CPUs, available RAM / estimated peak per job, the user's limit), at least 1."""
    jobs = max(1, int(cpus))
    if per_job_mib > 0 and ram_available_mib > 0:
        jobs = min(jobs, max(1, int(ram_available_mib) // int(per_job_mib)))
    if limit and int(limit) > 0:
        jobs = min(jobs, int(limit))
    return max(1, jobs)


def _find(which: Which, *names: str) -> Optional[str]:
    for name in names:
        path = which(name)
        if path:
            return str(Path(path).absolute())
    return None


def native_expansion(cc: str, arch: str, run: ToolRunner) -> Optional[str]:
    flag = "-mcpu=native" if arch == "arm64" else "-march=native"
    with tempfile.TemporaryDirectory(prefix="llm-native-") as tmp:
        empty = Path(tmp) / "empty.c"
        empty.write_text("", encoding="ascii")
        rc, out, err = run([cc, flag, "-E", "-v", str(empty), "-o", os.devnull], TOOL_TIMEOUT, tmp)
    if rc != 0:
        return None
    return parse_native_expansion(err + "\n" + out)


def check_openmp(cc: str, run: ToolRunner) -> bool:
    """True when `cc -fopenmp` compiles and links a small OpenMP program (Clang needs libomp for that)."""
    with tempfile.TemporaryDirectory(prefix="llm-openmp-") as tmp:
        source = Path(tmp) / "omp.c"
        source.write_text(_OPENMP_TEST, encoding="ascii")
        rc, _out, _err = run([cc, "-fopenmp", str(source), "-o", str(Path(tmp) / "omp")], TOOL_TIMEOUT, tmp)
    return rc == 0


def detect_toolchain(*, run: Optional[ToolRunner] = None, which: Optional[Which] = None,
                     system: Optional[str] = None, machine: Optional[str] = None,
                     resources: Optional[Tuple[int, int]] = None, full: bool = True) -> DetectedToolchain:
    """Looks at this machine's toolchain for a native build. full=False skips the compile checks (native expansion,
    OpenMP), for quick status output such as the doctor's."""
    run = run or default_tool_runner
    which = which or shutil.which
    os_name, arch = normalize_platform(system, machine)
    if os_name not in ("linux", "windows", "macos") or arch not in ("x86_64", "arm64"):
        raise ToolchainError(f"there is no llama.cpp build plan for {os_name}/{arch} (linux, windows, macos on "
                             "x86_64 or arm64)")
    cpus, ram = resources if resources is not None else host_resources()
    problems: List[str] = []
    notes: List[str] = []
    tools: Dict[str, str] = {}

    found = {
        "cmake": _find(which, "cmake"),
        "ninja": _find(which, "ninja", "ninja-build"),
        "cc": _find(which, "cc", "gcc", "clang"),
        "cxx": _find(which, "c++", "g++", "clang++"),
        "ccache": _find(which, "ccache"),
        "git": _find(which, "git"),
        "docker": _find(which, "docker"),
    }
    tools.update({k: v for k, v in found.items() if v})

    cmake = None
    if found["cmake"]:
        rc, out, _err = run([found["cmake"], "--version"], TOOL_TIMEOUT, None)
        cmake = parse_cmake_version(out) if rc == 0 else None
    if cmake is None:
        problems.append("CMake was not found (install cmake, 3.15 or newer)")
        tools.pop("cmake", None)
    elif _version_tuple(cmake) < CMAKE_MIN:
        problems.append(f"CMake {cmake} is too old; the build needs {'.'.join(map(str, CMAKE_MIN))} or newer")
    if found["ninja"]:
        rc, out, _err = run([found["ninja"], "--version"], TOOL_TIMEOUT, None)
        if rc != 0 or parse_plain_version(out) is None:
            tools.pop("ninja", None)
    if "ninja" not in tools:
        problems.append("Ninja was not found (install ninja-build)")

    compiler = None
    ids: List[Optional[str]] = []
    for key in ("cc", "cxx"):
        if found[key]:
            rc, out, err = run([found[key], "--version"], TOOL_TIMEOUT, None)
            ids.append(compiler_id(out + "\n" + err) if rc == 0 else None)
        else:
            ids.append(None)
    if ids[0] is None or ids[1] is None:
        problems.append("no C and C++ compiler found (install build-essential, or gcc and g++)")
        tools.pop("cc", None)
        tools.pop("cxx", None)
    elif ids[0].split("-")[0] != ids[1].split("-")[0]:
        problems.append(f"cc ({ids[0]}) and c++ ({ids[1]}) are different compilers; make them the same family")
    else:
        compiler = ids[1]
        if compiler.split("-")[0] not in ("gcc", "clang"):
            problems.append(f"{compiler} is not a compiler for a native Linux build (GCC or Clang)")
            compiler = None

    if os_name != "linux":
        problems.append(f"this machine runs {os_name}: native builds there come with later work packages (Windows, "
                        "macOS); build on Linux or import a build")

    expansion: Optional[str] = None
    openmp: Optional[bool] = None
    if full and compiler and "cc" in tools:
        expansion = native_expansion(tools["cc"], arch, run)
        if expansion is None:
            notes.append("what -march/-mcpu=native expands to could not be read")
        openmp = check_openmp(tools["cc"], run)
        if not openmp:
            notes.append("the compiler cannot build OpenMP code" +
                         (" (Clang needs libomp, for example libomp-dev)" if compiler.startswith("clang") else ""))

    info = ToolchainInfo(host_os=os_name, host_arch=arch, compiler=compiler, cmake=cmake,
                         native_expansion=expansion, ccache="ccache" in tools, openmp=openmp)
    return DetectedToolchain(info=info, tools=tools, cpus=cpus, ram_available_mib=ram, problems=tuple(problems),
                             notes=tuple(notes))


def host_platform() -> Tuple[str, str]:
    """(os, arch) of this machine in the recipe's words."""
    return normalize_platform(platform.system(), platform.machine())
