#!/usr/bin/env python3
"""
Verification of a llama.cpp build before it is stored or used (design 3.8). A build that fails is never activated.

1. Format: every binary is an ELF file for the planned architecture; every library it needs is in the build or a
   system library of the planned backend; builds that leave their userland (device and container builds) need no
   libstdc++/libgcc_s and no newer glibc symbol version than the machine that runs them has (LIBC_VERSION). The ELF
   file is read here in Python, so the check also works for another architecture without a cross readelf.
2. Version: `llama-bench --version` (and `llama-server --version`) report the pinned build number and commit.
3. Devices: `llama-bench --list-devices` shows every planned device (CUDA0, Vulkan0, ...); a GPU build that silently
   fell back to the CPU fails here.
4. The server refuses --tools: it was built without subprocess support (LLAMA_SUBPROCESS=OFF, decision D22).
5. Smoke run and short benchmark: `llama-bench -p 64 -n 16 -r 1 -o json` on the pinned smoke-test model (decision
   D17). Until the catalog pins one, both are skipped with a note. Only that catalog model (checked by sha256) is
   ever loaded here: it runs natively because it is no untrusted model.

Binaries are only ever taken from the build's own bin/ folder, by name, as regular files: never from PATH, never
through a link that leaves the folder (design P1). They run with an argument list, an empty stdin, a time limit, a
temporary working folder and a cleared environment (server_policy.cleared_env). Builds for another machine (cross
builds) get step 1 here; steps 2-5 run on that machine.
"""

import hashlib
import json
import os
import re
import secrets
import socket
import struct
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from orchestrator.runtime.server_policy import (SERVER_TOOLS_REFUSAL, check_command, cleared_env,
                                                server_tools_check_command)

VERSION_TIMEOUT = 60
DEVICES_TIMEOUT = 180
BENCH_TIMEOUT = 900
MAX_OUTPUT = 1024 * 1024

_RE_BINARY = re.compile(r"^llama-[a-z0-9-]{1,40}(\.exe)?$", re.ASCII)
# version: 0.6.0 (build 11429, commit d8123504)   -- common/build-info.cpp.in at the pin
_RE_VERSION = re.compile(r"version:\s*(\S+)\s*\(build\s+(\d+),\s*commit\s+([0-9a-f]{4,40}|unknown)\)", re.ASCII)
_RE_VERSION_OLD = re.compile(r"version:\s*(\d+)\s*\(([0-9a-f]{4,40}|unknown)\)", re.ASCII)
#   CUDA0: NVIDIA GeForce RTX 3090 (24135 MiB, 23700 MiB free)   -- common/arg.cpp common_print_available_devices
_RE_DEVICE = re.compile(r"^\s{2}([A-Za-z][A-Za-z0-9_-]{0,31}):\s(.{0,200}?)\s\((\d+) MiB, (\d+) MiB free\)\s*$",
                        re.ASCII)

# ELF machines (e_machine) by the recipe's architecture names.
ELF_MACHINES = {62: "x86_64", 183: "arm64", 3: "x86", 40: "arm"}
# Libraries a Linux build may need from the system, besides its own (in bin/). Device and container builds link the
# C++ runtime statically, so for them libstdc++ and libgcc_s are not allowed (_STATIC_RUNTIME_LIBS).
_SYSTEM_LIBS = re.compile(r"^(libc\.so\.6|libm\.so\.6|libpthread\.so\.0|libdl\.so\.2|librt\.so\.1|libgcc_s\.so\.1|"
                          r"libstdc\+\+\.so\.6|libgomp\.so\.1|libatomic\.so\.1|ld-linux-x86-64\.so\.2|"
                          r"ld-linux-aarch64\.so\.1|libc\.musl-[a-z0-9_]+\.so\.1|ld-musl-[a-z0-9_]+\.so\.1)$", re.ASCII)
_STATIC_RUNTIME_LIBS = ("libstdc++.so.6", "libgcc_s.so.1")
_BACKEND_LIBS = {
    "cuda": re.compile(r"^(libcuda\.so\.1|libcudart\.so\.\d+|libcublas\.so\.\d+|libcublasLt\.so\.\d+)$", re.ASCII),
    "vulkan": re.compile(r"^libvulkan\.so\.1$", re.ASCII),
    "rocm": re.compile(r"^(libamdhip64\.so\.\d+|libhipblas\.so\.\d+|librocblas\.so\.\d+)$", re.ASCII),
}

# (argv, timeout, cwd, env) -> (returncode, stdout, stderr); returncode -1: could not start or timed out.
BinaryRunner = Callable[[Sequence[str], float, str, Mapping[str, str]], Tuple[int, str, str]]


class VerifyError(RuntimeError):
    """A build cannot be verified at all (a binary is missing or outside the build); the message says why."""


@dataclass
class Check:
    name: str
    ok: Optional[bool]          # None: skipped (the detail says why)
    detail: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"name": self.name, "result": {True: "passed", False: "failed", None: "skipped"}[self.ok],
                               "detail": self.detail}
        if self.data:
            out["data"] = self.data
        return out


@dataclass
class VerifyReport:
    checks: List[Check] = field(default_factory=list)
    ran_binaries: bool = False   # steps 2-5 ran on this machine (not for cross builds)

    @property
    def ok(self) -> bool:
        return all(c.ok is not False for c in self.checks) and any(c.ok for c in self.checks)

    @property
    def failures(self) -> List[Check]:
        return [c for c in self.checks if c.ok is False]

    def check(self, name: str) -> Optional[Check]:
        return next((c for c in self.checks if c.name == name), None)

    def as_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "ran_binaries": self.ran_binaries, "checks": [c.as_dict() for c in self.checks]}

    def summary(self) -> str:
        failed = self.failures
        if failed:
            return "; ".join(f"{c.name}: {c.detail}" for c in failed)
        return "verified" if self.ran_binaries else "format checked (the programs run on the target machine)"


# --- ELF ----------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ElfInfo:
    machine: str
    bits: int
    needed: Tuple[str, ...]
    runpath: Optional[str]
    versions: Tuple[str, ...]   # every symbol version the file needs (GLIBC_2.34, GLIBCXX_3.4.30, ...)

    @property
    def glibc_max(self) -> Optional[str]:
        found = [v[len("GLIBC_"):] for v in self.versions if re.fullmatch(r"GLIBC_\d+(\.\d+){1,3}", v)]
        return max(found, key=_vt) if found else None


def _vt(text: str) -> Tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", text))


def read_elf(path: Path) -> ElfInfo:
    """Machine, NEEDED libraries, RUNPATH and needed symbol versions of an ELF file. Raises VerifyError when the file
    is no ELF file or is damaged. Reads only the headers and the dynamic sections."""
    try:
        with open(path, "rb") as f:
            return _read_elf(f, os.fstat(f.fileno()).st_size)
    except (OSError, struct.error, IndexError, ValueError) as e:
        raise VerifyError(f"{Path(path).name} is not a readable ELF file ({e})") from None


def _read_elf(f, size: int) -> ElfInfo:
    ident = f.read(16)
    if len(ident) < 16 or ident[:4] != b"\x7fELF" or ident[4] not in (1, 2) or ident[5] not in (1, 2):
        raise ValueError("no ELF header")
    bits = 64 if ident[4] == 2 else 32
    end = "<" if ident[5] == 1 else ">"

    def read_at(offset: int, length: int) -> bytes:
        if offset < 0 or length < 0 or offset + length > size:
            raise ValueError("offset outside the file")
        f.seek(offset)
        data = f.read(length)
        if len(data) != length:
            raise ValueError("short read")
        return data

    if bits == 64:
        machine, = struct.unpack(end + "H", read_at(18, 2))
        shoff, = struct.unpack(end + "Q", read_at(0x28, 8))
        shentsize, shnum, shstrndx = struct.unpack(end + "HHH", read_at(0x3A, 6))
        sh_fmt, dyn_fmt = end + "IIQQQQIIQQ", end + "qQ"
    else:
        machine, = struct.unpack(end + "H", read_at(18, 2))
        shoff, = struct.unpack(end + "I", read_at(0x20, 4))
        shentsize, shnum, shstrndx = struct.unpack(end + "HHH", read_at(0x2E, 6))
        sh_fmt, dyn_fmt = end + "IIIIIIIIII", end + "iI"
    if shnum == 0 or shnum > 4096 or shentsize < struct.calcsize(sh_fmt):
        raise ValueError("no section headers")
    sections = []
    for i in range(shnum):
        fields = struct.unpack(sh_fmt, read_at(shoff + i * shentsize, struct.calcsize(sh_fmt)))
        # name, type, flags, addr, offset, size, link, info, addralign, entsize
        sections.append(fields)

    def strtab(index: int) -> bytes:
        sec = sections[index]
        if sec[5] > 64 * 1024 * 1024:
            raise ValueError("string table too large")
        return read_at(sec[4], sec[5])

    def string(table: bytes, offset: int) -> str:
        end_at = table.find(b"\0", offset)
        return table[offset:end_at if end_at >= 0 else len(table)].decode("ascii", "replace")

    needed: List[str] = []
    runpath: Optional[str] = None
    versions: List[str] = []
    for sec in sections:
        sh_type, sh_offset, sh_size, sh_link, sh_info = sec[1], sec[4], sec[5], sec[6], sec[7]
        if sh_type == 6:  # SHT_DYNAMIC
            names = strtab(sh_link)
            entry = struct.calcsize(dyn_fmt)
            data = read_at(sh_offset, min(sh_size, 1024 * 1024))
            for pos in range(0, len(data) - entry + 1, entry):
                tag, val = struct.unpack(dyn_fmt, data[pos:pos + entry])
                if tag == 0:
                    break
                if tag == 1:
                    needed.append(string(names, val))
                elif tag in (15, 29):  # DT_RPATH, DT_RUNPATH
                    runpath = string(names, val)
        elif sh_type == 0x6FFFFFFE:  # SHT_GNU_verneed
            names = strtab(sh_link)
            data = read_at(sh_offset, min(sh_size, 1024 * 1024))
            pos = 0
            for _ in range(min(sh_info, 512)):
                _vn_version, vn_cnt, _vn_file, vn_aux, vn_next = struct.unpack(end + "HHIII", data[pos:pos + 16])
                aux = pos + vn_aux
                for _ in range(min(vn_cnt, 512)):
                    _hash, _flags, _other, vna_name, vna_next = struct.unpack(end + "IHHII", data[aux:aux + 16])
                    versions.append(string(names, vna_name))
                    if vna_next == 0:
                        break
                    aux += vna_next
                if vn_next == 0:
                    break
                pos += vn_next
    return ElfInfo(machine=ELF_MACHINES.get(machine, f"elf-{machine}"), bits=bits, needed=tuple(needed),
                   runpath=runpath, versions=tuple(sorted(set(versions))))


# --- output parsers -----------------------------------------------------------------------------------------------

def parse_version(text: str) -> Optional[Tuple[int, str]]:
    """(build number, commit) from `--version` output (stdout or stderr), None when there is no version line."""
    match = _RE_VERSION.search(text or "")
    if match:
        return int(match.group(2)), match.group(3)
    match = _RE_VERSION_OLD.search(text or "")
    if match:
        return int(match.group(1)), match.group(2)
    return None


@dataclass(frozen=True)
class Device:
    name: str
    description: str
    total_mib: int
    free_mib: int


def parse_devices(text: str) -> Optional[List[Device]]:
    """The devices of `--list-devices` ("Available devices:" then one line per non-CPU device, or "(none)"). None
    when the output has no device list at all."""
    lines = (text or "").splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == "Available devices:")
    except StopIteration:
        return None
    devices: List[Device] = []
    for line in lines[start + 1:start + 65]:
        if line.strip() == "(none)":
            break
        match = _RE_DEVICE.match(line)
        if not match:
            if line.startswith("  "):
                continue
            break
        devices.append(Device(match.group(1), match.group(2).strip(), int(match.group(3)), int(match.group(4))))
    return devices


def parse_bench_json(text: str) -> List[Dict[str, Any]]:
    """The test records of `llama-bench -o json` (a JSON array), with the fields the build check keeps."""
    start = (text or "").find("[")
    if start < 0:
        raise ValueError("no JSON array in the llama-bench output")
    data = json.loads(text[start:text.rfind("]") + 1])
    keep = ("n_prompt", "n_gen", "avg_ts", "stddev_ts", "backends", "n_threads", "model_size", "model_n_params")
    if not isinstance(data, list):
        raise ValueError("the llama-bench output is not a list")
    return [{k: rec.get(k) for k in keep if k in rec} for rec in data if isinstance(rec, dict)]


# --- running binaries ---------------------------------------------------------------------------------------------

def binary_path(bin_dir: Path, name: str) -> Path:
    """The program `name` of a build: a regular, executable file directly in its bin/ folder. Never a lookup on PATH,
    never a link out of the folder."""
    if not _RE_BINARY.fullmatch(name or ""):
        raise VerifyError(f"{name!r} is not the name of a llama.cpp program")
    try:
        folder = Path(bin_dir).resolve(strict=True)
    except OSError:
        raise VerifyError(f"the build folder {bin_dir} does not exist") from None
    path = folder / name
    if path.is_symlink() or not path.is_file() or path.resolve().parent != folder:
        raise VerifyError(f"{name} is missing from the build ({folder}); a {name} found elsewhere, for example on "
                          "PATH, is never used")
    if os.name != "nt" and not os.access(path, os.X_OK):
        raise VerifyError(f"{name} in {folder} is not executable")
    return path


def default_binary_runner(argv: Sequence[str], timeout: float, cwd: str,
                          env: Mapping[str, str]) -> Tuple[int, str, str]:
    try:
        res = subprocess.run(list(argv), capture_output=True, timeout=timeout, check=False, cwd=cwd, env=dict(env),
                             stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return -1, "", f"did not finish within {timeout:.0f} s"
    except (OSError, ValueError) as e:
        return -1, "", str(e)
    decode = lambda b: (b or b"")[:MAX_OUTPUT].decode("utf-8", "replace")  # noqa: E731
    return res.returncode, decode(res.stdout), decode(res.stderr)


def _tail(text: str, limit: int = 300) -> str:
    clean = " ".join("".join(ch if ch.isprintable() else " " for ch in (text or "")).split())
    return clean[-limit:] if clean else "no output"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --- the verifier -------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SmokeModelFile:
    """The downloaded smoke-test model of the catalog (decision D17) with its pinned sha256."""
    path: Path
    sha256: str


class Verifier:
    """Runs the checks of design 3.8 on a build folder. `runner` starts a program (replaceable in tests)."""

    def __init__(self, runner: Optional[BinaryRunner] = None, *, threads: Optional[int] = None):
        self.runner = runner or default_binary_runner
        self.threads = threads

    def verify(self, plan: Any, bin_dir: Path, *, run_binaries: Optional[bool] = None,
               smoke_model: Optional[SmokeModelFile] = None, host_arch: Optional[str] = None) -> VerifyReport:
        """`plan` is the BuildPlan (or its as_dict()). run_binaries defaults to: the binaries are for this machine
        (not a cross build)."""
        data = plan.as_dict() if hasattr(plan, "as_dict") else dict(plan)
        report = VerifyReport()
        expected = data.get("verify") or {}
        target = data.get("target") or {}
        binaries = list(expected.get("binaries") or [])
        if run_binaries is None:
            run_binaries = data.get("placement") != "cross" and (host_arch is None or host_arch == target.get("arch"))
        paths: Dict[str, Path] = {}
        missing = []
        for name in binaries:
            try:
                paths[name] = binary_path(bin_dir, name)
            except VerifyError as e:
                missing.append(str(e))
        if missing:
            report.checks.append(Check("binaries", False, "; ".join(missing)))
            return report
        report.checks.append(Check("binaries", True, ", ".join(binaries)))
        report.checks.append(self._format(data, paths, Path(bin_dir)))
        if not run_binaries:
            for name in ("version", "devices", "server-tools", "smoke"):
                report.checks.append(Check(name, None, "runs on the machine the build is for"))
            return report
        report.ran_binaries = True
        with tempfile.TemporaryDirectory(prefix="llm-verify-") as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            env = cleared_env(home)
            report.checks.append(self._version(expected, paths, tmp, env))
            report.checks.append(self._devices(expected, paths, tmp, env))
            report.checks.append(self._server_tools(paths, tmp, env))
            report.checks.append(self._smoke(paths, smoke_model, tmp, env))
        return report

    # --- 1. format ---
    def _format(self, plan: Mapping[str, Any], paths: Mapping[str, Path], bin_dir: Path) -> Check:
        target = plan.get("target") or {}
        expected = plan.get("verify") or {}
        if target.get("os") != "linux":
            return Check("format", None, f"the ELF check covers Linux builds; {target.get('os')} builds are checked "
                         "by starting them")
        backend = plan.get("backend") or "cpu"
        static_runtime = bool(expected.get("static_libstdcxx"))
        glibc_max = expected.get("glibc_max")
        own = {p.name for p in bin_dir.iterdir()}
        problems: List[str] = []
        highest: Optional[str] = None
        files = sorted(set(paths.values()) | {p for p in bin_dir.iterdir()
                                             if p.is_file() and not p.is_symlink() and ".so" in p.name})
        for path in files:
            try:
                elf = read_elf(path)
            except VerifyError as e:
                problems.append(str(e))
                continue
            if elf.machine != target.get("arch"):
                problems.append(f"{path.name} is built for {elf.machine}, not {target.get('arch')}")
            for lib in elf.needed:
                if lib in own:
                    continue
                if static_runtime and lib in _STATIC_RUNTIME_LIBS:
                    problems.append(f"{path.name} needs {lib}; device and container builds link the C++ runtime "
                                    "statically")
                elif not (_SYSTEM_LIBS.match(lib) or (backend in _BACKEND_LIBS and _BACKEND_LIBS[backend].match(lib))):
                    problems.append(f"{path.name} needs {lib}, which is neither in the build nor a system library")
            if elf.glibc_max and (highest is None or _vt(elf.glibc_max) > _vt(highest)):
                highest = elf.glibc_max
        if glibc_max and highest and _vt(highest) > _vt(str(glibc_max)):
            problems.append(f"the binaries need glibc {highest}, but the machine they are for has {glibc_max}")
        data = {"glibc_required": highest} if highest else {}
        if problems:
            return Check("format", False, "; ".join(problems[:8]), data)
        return Check("format", True, f"ELF {target.get('arch')}" + (f", glibc {highest} or newer" if highest else ""),
                     data)

    def _run(self, argv: Sequence[str], timeout: float, cwd: str, env: Mapping[str, str]) -> Tuple[int, str, str]:
        check_command(argv)  # the shared forbidden-flag list (D22); the --tools check below is the one exception
        return self.runner(list(argv), timeout, cwd, env)

    # --- 2. version ---
    def _version(self, expected: Mapping[str, Any], paths: Mapping[str, Path], cwd: str,
                 env: Mapping[str, str]) -> Check:
        want = expected.get("version") or {}
        found = {}
        problems = []
        for name in ("llama-bench", "llama-server"):
            if name not in paths:
                continue
            rc, out, err = self._run([str(paths[name]), "--version"], VERSION_TIMEOUT, cwd, env)
            parsed = parse_version(err + "\n" + out)
            if rc != 0 or parsed is None:
                problems.append(f"{name} --version failed (exit {rc}): {_tail(err or out)}")
                continue
            build, commit = parsed
            found[name] = f"b{build} ({commit})"
            if build != want.get("build_number") or not str(want.get("commit", "")).startswith(commit[:8]) \
                    or len(commit) < 8:
                problems.append(f"{name} reports build {build}, commit {commit}; the pin is build "
                                f"{want.get('build_number')}, commit {want.get('commit')}")
        if not found and not problems:
            return Check("version", False, "the build has neither llama-bench nor llama-server")
        if problems:
            return Check("version", False, "; ".join(problems), {"found": found})
        return Check("version", True, ", ".join(f"{k} {v}" for k, v in found.items()), {"found": found})

    # --- 3. devices ---
    def _devices(self, expected: Mapping[str, Any], paths: Mapping[str, Path], cwd: str,
                 env: Mapping[str, str]) -> Check:
        if "llama-bench" not in paths:
            return Check("devices", None, "no llama-bench in this build")
        rc, out, err = self._run([str(paths["llama-bench"]), "--list-devices"], DEVICES_TIMEOUT, cwd, env)
        devices = parse_devices(out + "\n" + err)
        if rc != 0 or devices is None:
            return Check("devices", False, f"llama-bench --list-devices failed (exit {rc}): {_tail(err or out)}")
        names = [d.name for d in devices]
        want = list(expected.get("devices") or [])
        lost = [d for d in want if d not in names]
        data = {"devices": [{"name": d.name, "description": d.description[:80], "total_mib": d.total_mib}
                            for d in devices]}
        if lost:
            return Check("devices", False, f"planned {', '.join(want)}, found {', '.join(names) or 'only the CPU'}: "
                         "the build fell back to the CPU or the device is not usable", data)
        return Check("devices", True, ", ".join(names) or "CPU only", data)

    # --- 4. the server refuses --tools ---
    def _server_tools(self, paths: Mapping[str, Path], cwd: str, env: Mapping[str, str]) -> Check:
        if "llama-server" not in paths:
            return Check("server-tools", None, "no llama-server in this build")
        key = Path(cwd) / "check.key"
        fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(secrets.token_hex(32))
        missing_model = Path(cwd) / f"no-model-{secrets.token_hex(4)}.gguf"
        argv = server_tools_check_command(paths["llama-server"], missing_model, _free_port(), key)
        rc, out, err = self.runner(argv, VERSION_TIMEOUT, cwd, env)  # deliberately not through check_command
        text = err + "\n" + out
        if rc != 0 and SERVER_TOOLS_REFUSAL in text:
            return Check("server-tools", True, "llama-server refuses --tools (built without subprocess support)")
        return Check("server-tools", False, f"llama-server did not refuse --tools (exit {rc}): {_tail(text)}")

    # --- 5. smoke run and short benchmark ---
    def _smoke(self, paths: Mapping[str, Path], smoke: Optional[SmokeModelFile], cwd: str,
               env: Mapping[str, str]) -> Check:
        if smoke is None:
            return Check("smoke", None, "no smoke-test model is pinned yet (decision D17): smoke run and short "
                         "benchmark skipped")
        if "llama-bench" not in paths:
            return Check("smoke", None, "no llama-bench in this build")
        try:
            if sha256_file(smoke.path) != smoke.sha256:
                return Check("smoke", False, "the smoke-test model does not match its pinned sha256; not loaded")
        except OSError as e:
            return Check("smoke", False, f"the smoke-test model cannot be read: {e.strerror or e}")
        argv = [str(paths["llama-bench"]), "-m", str(smoke.path), "-p", "64", "-n", "16", "-r", "1", "-o", "json"]
        if self.threads:
            argv += ["-t", str(int(self.threads))]
        rc, out, err = self._run(argv, BENCH_TIMEOUT, cwd, env)
        if rc != 0:
            sigill = rc in (-4, 132) or "Illegal instruction" in err
            hint = "; SIGILL: the CPU baseline of the build is wrong for this CPU" if sigill else ""
            return Check("smoke", False, f"llama-bench on the smoke-test model failed (exit {rc}){hint}: "
                         f"{_tail(err or out)}")
        try:
            records = parse_bench_json(out)
        except ValueError as e:
            return Check("smoke", False, f"llama-bench wrote no usable JSON: {e}")
        speeds = {("pp" if r.get("n_prompt") else "tg"): r.get("avg_ts") for r in records}
        return Check("smoke", True, ", ".join(f"{k} {v:.1f} t/s" for k, v in speeds.items()
                                              if isinstance(v, (int, float))) or "ran", {"bench": records})
