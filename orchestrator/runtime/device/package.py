#!/usr/bin/env python3
"""
The device runtime as a build plan and as a tarball (design 4.4 steps 2 and 4).

- plan_device_runtime(): the device's own probe profile (decision D14: the probe ran on the device) becomes a cross
  build plan, purpose device-runtime: the builder container's base is the newest one whose C library is not newer
  than the device's (glibc, or musl on Alpine; decision 8), and the C++ runtime is linked statically. A device whose
  C library is older than every base gets no plan (the message says so); building on the device itself is not
  offered yet.
- package_runtime(): a verified build of the store becomes llama-runtime-<arch>-<build id>.tar.gz with one folder
  <build id[:16]>/ holding bin/ (llama-server, llama-cli, llama-bench, llama-perplexity), lib/libgomp.so.1 when the
  build uses OpenMP, the start script, build.json and manifest.json of the store, llama.cpp's MIT licence text from
  the verified source and SHA256SUMS, which the deployment checks on the device. The archive is deterministic (fixed
  owners, modes and times) and has a .sha256 file next to it.
- DeviceRuntimeStore keeps a device's tarballs and the record of its last deployment under
  <state dir>/runtime/llama.cpp/device/<device id>/.

The framework shares this recipe, never the tarball (decision D21): it is built on the user's machine for the user's
device.
"""

import gzip
import hashlib
import io
import json
import os
import re
import secrets
import tarfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

from orchestrator.runtime.build.recipe import BuildPlan, PlanError, ToolchainInfo, plan_build
from orchestrator.runtime.build.store import BuildStore, StoredBuild
from orchestrator.runtime.catalog import RuntimeCatalog
from orchestrator.runtime.device.launcher import START_SCRIPT, render_start_script
from orchestrator.runtime.profile import DEVICE_ID, HardwareProfile

LICENCE_NAME = "LICENSE-llama.cpp"
SUMS_NAME = "SHA256SUMS"
MAX_LICENCE_BYTES = 64 * 1024
RECORD_FORMAT = 1
MAX_RECORD_BYTES = 1024 * 1024
_RE_PROGRAM = re.compile(r"^llama-[a-z0-9-]{1,40}$", re.ASCII)
_RE_ID = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_RE_TARBALL = re.compile(r"^llama-runtime-(x86_64|arm64)-([0-9a-f]{16})\.tar\.gz$", re.ASCII)


class PackageError(RuntimeError):
    """The device runtime cannot be packaged; the message says why and what to do."""


def device_toolchain(host: Tuple[str, str]) -> ToolchainInfo:
    """The build host of a cross build: its OS and architecture only. The compiler, CMake and Ninja come from the
    builder image (its base is pinned by digest and part of the build ID), not from this machine."""
    return ToolchainInfo(host_os=host[0], host_arch=host[1])


def plan_device_runtime(profile: HardwareProfile, *, catalog: RuntimeCatalog, host: Tuple[str, str],
                        backend: str = "cpu") -> BuildPlan:
    """The cross build plan of the device runtime for the device `profile` describes. Raises PlanError.

    The CPU build is the device runtime for now: Vulkan on a board's Mali GPU only after llama-bench shows that it
    beats the CPU build (decision D8), and CUDA cross builds are not offered."""
    if not isinstance(profile, HardwareProfile):
        raise PlanError("expected a parsed HardwareProfile of the device")
    if profile.os != "linux":
        raise PlanError(f"the device runtime is deployed to Linux devices; this profile describes {profile.os}. A "
                        "Windows PC builds its own runtime (native build on that PC)")
    if backend != "cpu":
        raise PlanError("the device runtime is a CPU build for now (Vulkan on a board only after llama-bench shows "
                        "it beats the CPU build, decision D8; no CUDA cross builds)")
    return plan_build(profile, purpose="device-runtime", placement="cross", backend=backend, catalog=catalog,
                      toolchain=device_toolchain(host))


def tarball_name(build: StoredBuild) -> str:
    arch = str((build.plan.get("target") or {}).get("arch") or "")
    if arch not in ("x86_64", "arm64"):
        raise PackageError(f"the build is for {arch or 'an unknown architecture'}, not a device architecture")
    return f"llama-runtime-{arch}-{build.build_id[:16]}.tar.gz"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _mtime(build: StoredBuild) -> int:
    try:
        return max(0, int(datetime.fromisoformat(build.built_at).timestamp()))
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class PackagedRuntime:
    path: Path
    sha256: str
    build_id: str
    members: Tuple[str, ...]


def _check_build(store: BuildStore, build: StoredBuild) -> Dict[str, Any]:
    plan = build.plan
    if plan.get("purpose") != "device-runtime":
        raise PackageError(f"build {build.build_id[:16]} is a {plan.get('purpose')} build, not a device runtime")
    if (plan.get("target") or {}).get("os") != "linux":
        raise PackageError("device tarballs hold Linux builds")
    report = build.manifest.get("verification") or {}
    if not report.get("ok"):
        raise PackageError(f"build {build.build_id[:16]} did not pass its checks; it is never packaged")
    problems = store.check_files(build)
    if problems:
        raise PackageError(f"build {build.build_id[:16]} does not match its manifest: {'; '.join(problems[:3])}")
    for name in (plan.get("verify") or {}).get("binaries") or []:
        if name not in (build.manifest.get("files") or {}):
            raise PackageError(f"build {build.build_id[:16]} lacks {name}")
    return plan


def package_runtime(store: BuildStore, build: StoredBuild, *, licence: Path, out_dir: Path) -> PackagedRuntime:
    """Writes the device tarball of a stored, checked device build into out_dir (see the module docstring)."""
    plan = _check_build(store, build)
    try:
        licence_text = Path(licence).read_bytes()
    except OSError as e:
        raise PackageError(f"llama.cpp's licence text cannot be read from the pinned source ({e.strerror or e}); "
                           "fetch the source again") from None
    if not licence_text or len(licence_text) > MAX_LICENCE_BYTES or b"MIT License" not in licence_text:
        raise PackageError("the licence file of the source is not llama.cpp's MIT licence text")
    runtime_libs = set((plan.get("verify") or {}).get("runtime_libs") or [])
    top = build.build_id[:16]
    files: Mapping[str, str] = build.manifest.get("files") or {}
    links: Mapping[str, str] = build.manifest.get("links") or {}
    missing_libs = runtime_libs - set(files)
    if missing_libs:
        raise PackageError(f"the build lacks {', '.join(sorted(missing_libs))}, which its programs need on the "
                           "device; build it again")

    entries: List[Tuple[str, Union[bytes, Path, str], int]] = []  # (path in the folder, content or link target, mode)
    for name in sorted(files):
        where = "lib" if name in runtime_libs else "bin"
        mode = 0o755 if _RE_PROGRAM.fullmatch(name) else 0o644
        entries.append((f"{where}/{name}", build.bin_dir / name, mode))
    entries.append((START_SCRIPT, render_start_script().encode("utf-8"), 0o755))
    for name in ("build.json", "manifest.json"):
        entries.append((name, build.path / name, 0o644))
    entries.append((LICENCE_NAME, licence_text, 0o644))
    sums = []
    for rel, content, _mode in entries:
        digest = _sha256_bytes(content) if isinstance(content, bytes) else _sha256_file(content)
        sums.append(f"{digest}  {rel}\n")
    entries.append((SUMS_NAME, "".join(sums).encode("ascii"), 0o644))

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / tarball_name(build)
    tmp = out_dir / f".{target.name}.{secrets.token_hex(4)}.tmp"
    mtime = _mtime(build)
    members: List[str] = []
    try:
        with open(tmp, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=mtime) as gz, \
                tarfile.open(fileobj=gz, mode="w", format=tarfile.USTAR_FORMAT) as tar:
            def add_dir(rel: str) -> None:
                info = tarfile.TarInfo(f"{top}/{rel}".rstrip("/"))
                info.type, info.mode, info.mtime = tarfile.DIRTYPE, 0o755, mtime
                tar.addfile(info)
                members.append(info.name + "/")

            add_dir("")
            add_dir("bin")
            if runtime_libs:
                add_dir("lib")
            for rel, content, mode in entries:
                data = content if isinstance(content, bytes) else Path(content).read_bytes()
                info = tarfile.TarInfo(f"{top}/{rel}")
                info.size, info.mode, info.mtime = len(data), mode, mtime
                tar.addfile(info, io.BytesIO(data))
                members.append(info.name)
            for name, link_target in sorted(links.items()):
                info = tarfile.TarInfo(f"{top}/bin/{name}")
                info.type, info.linkname, info.mode, info.mtime = tarfile.SYMTYPE, link_target, 0o777, mtime
                tar.addfile(info)
                members.append(info.name)
        digest = _sha256_file(tmp)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()
    target.with_name(target.name + ".sha256").write_text(f"{digest}  {target.name}\n", encoding="ascii")
    return PackagedRuntime(path=target, sha256=digest, build_id=build.build_id, members=tuple(members))


class DeviceRuntimeStore:
    """<state dir>/runtime/llama.cpp/device/<device id>/: the device's tarballs (with .sha256) and deployed.json, the
    record of the last deployment and its checks on the device."""

    def __init__(self, state_dir: Union[str, Path], device_id: str):
        if not isinstance(device_id, str) or not DEVICE_ID.fullmatch(device_id):
            raise PackageError("a device ID is 16 lower-case hex characters")
        self.device_id = device_id
        self.root = Path(state_dir) / "runtime" / "llama.cpp" / "device" / device_id
        self.record_file = self.root / "deployed.json"

    def tarball(self, build: StoredBuild) -> Optional[PackagedRuntime]:
        """The packaged tarball of this build when it is there and matches its .sha256 file."""
        path = self.root / tarball_name(build)
        sums = path.with_name(path.name + ".sha256")
        try:
            want = sums.read_text(encoding="ascii").split()[0]
            got = _sha256_file(path)
        except (OSError, IndexError, UnicodeDecodeError):
            return None
        if want != got:
            return None
        return PackagedRuntime(path=path, sha256=got, build_id=build.build_id, members=())

    def tarballs(self) -> List[Path]:
        if not self.root.is_dir():
            return []
        return sorted(p for p in self.root.iterdir() if _RE_TARBALL.fullmatch(p.name))

    def prune(self, keep: Tuple[str, ...]) -> List[str]:
        """Removes the tarballs of builds other than `keep` (build IDs or their 16-character prefixes)."""
        prefixes = {k[:16] for k in keep}
        removed = []
        for path in self.tarballs():
            match = _RE_TARBALL.fullmatch(path.name)
            if match and match.group(2) not in prefixes:
                path.unlink(missing_ok=True)
                path.with_name(path.name + ".sha256").unlink(missing_ok=True)
                removed.append(path.name)
        return removed

    def record(self) -> Dict[str, Any]:
        """The record of the last deployment (empty when there is none or it is damaged)."""
        try:
            with open(self.record_file, "rb") as f:
                raw = f.read(MAX_RECORD_BYTES + 1)
            data = json.loads(raw) if len(raw) <= MAX_RECORD_BYTES else None
        except (OSError, ValueError, RecursionError):
            return {}
        if not isinstance(data, dict) or data.get("format") != RECORD_FORMAT:
            return {}
        if not isinstance(data.get("build_id"), str) or not _RE_ID.fullmatch(data["build_id"]):
            return {}
        return data

    def save_record(self, data: Mapping[str, Any]) -> Path:
        from orchestrator.Core.vault_key import write_private
        record = {"format": RECORD_FORMAT, **dict(data)}
        if not _RE_ID.fullmatch(str(record.get("build_id") or "")):
            raise PackageError("a deployment record needs the build ID")
        self.root.mkdir(parents=True, exist_ok=True)
        write_private(self.record_file, json.dumps(record, indent=2, sort_keys=True) + "\n")
        return self.record_file


__all__ = ["DeviceRuntimeStore", "PackageError", "PackagedRuntime", "device_toolchain", "package_runtime",
           "plan_device_runtime", "tarball_name"]
