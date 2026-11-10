#!/usr/bin/env python3
"""
llmcf-install, the board bundle's Rust root helper (rust/llmcf-install), built for a board's architecture and cached
here, the way the runtime layer's toolbox is (Core/runtime_layer.py ensure_toolbox: a record with the version and
the sha256 next to the program, the cached program used offline, nothing made offline).

Two ways, the first that is there: cargo on this computer (the pinned toolchain of rust/rust-toolchain.toml, a
static musl binary linked with the toolchain's own rust-lld, rust/.cargo/config.toml), or the framework's builder
image over the container engine (BUILDER_DOCKERFILE: the official Rust image pinned by digest, the same sources).
The result must be a static ELF program of the board's machine (no program interpreter), else it is not kept.

The cache is <cache>/llmcf-install/<target>/: the program and llmcf-install.json ({version, target, sha256,
source_sha256, way}); a cached program counts only while its sha256 and the sources' sha256 (rust/: the workspace
files and the crate, never target/) are the ones of its record, so a changed source is built again. Offline mode with
nothing cached: HelperMissing with OFFLINE_REFUSAL (nothing is fetched; cargo would fetch the pinned crates).

The same way builds the framework's other board programs of the workspace (`crate`, FRAMEWORK_PROGRAMS: the gateway
llmcf-gateway, a bundle block's program "from: framework"), each in its own cache folder <cache>/<crate>/<target>/
with its own record <crate>.json; a crate that compiles C (CC_CRATES: ring of the gateway's TLS) is cross-compiled
with clang for the board's target.

A target per program: the gateway and llmcf-install stay static musl programs; the vision engine llmcf-detect
(GNU_PROGRAMS) loads Rockchip's runtime, which exists only for glibc, so it is built for aarch64 with glibc 2.31 as its
baseline, linked by zig through cargo-zigbuild (both pinned: ZIG_VERSION, CARGO_ZIGBUILD_VERSION), the same pinned
tools in CI and in the builder image. Its ELF check (is_glibc_elf_for) accepts exactly this one program as dynamic:
the aarch64 interpreter, needed libraries only of the C library's own (GLIBC_NEEDED) and no glibc symbol version
above the baseline.
"""

import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import tarfile
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from orchestrator.utils.modes import SHARED_RUN

RUST_DIR = Path(__file__).resolve().parents[3] / "rust"
CRATE = "llmcf-install"
PROGRAM = "llmcf-install"
RECORD = "llmcf-install.json"
CACHE_REL = "llmcf-install"
BUILDER_DOCKERFILE = Path(__file__).resolve().parent / "llmcf_install" / "Dockerfile"
BUILDER_TAG = "llm-framework/llmcf-install-builder:1"
BUILD_TIMEOUT = 1800
# The board's machine (uname -m, the probe's words) -> the Rust target llmcf-install is built for.
TARGETS: Dict[str, str] = {
    "x86_64": "x86_64-unknown-linux-musl", "amd64": "x86_64-unknown-linux-musl",
    "aarch64": "aarch64-unknown-linux-musl", "arm64": "aarch64-unknown-linux-musl",
    "armv7l": "armv7-unknown-linux-musleabihf", "armv7": "armv7-unknown-linux-musleabihf",
    "armhf": "armv7-unknown-linux-musleabihf", "arm": "armv7-unknown-linux-musleabihf",
}
# target -> (ELF class: 1 32-bit, 2 64-bit; e_machine)
_ELF = {"x86_64-unknown-linux-musl": (2, 0x3E), "aarch64-unknown-linux-musl": (2, 0xB7),
        "armv7-unknown-linux-musleabihf": (1, 0x28)}
_SOURCE_FILES = ("Cargo.toml", "Cargo.lock", "rust-toolchain.toml", ".cargo/config.toml")
# The workspace's board programs a bundle block may take "from: framework" (its file name is the crate's name).
FRAMEWORK_PROGRAMS = ("llmcf-gateway", "llmcf-detect")
# The programs built for glibc (Rockchip's NPU runtime exists only for glibc): the board's machine -> (the cargo
# target, the glibc baseline cargo-zigbuild links against).
GNU_PROGRAMS: Dict[str, Dict[str, Tuple[str, str]]] = {
    "llmcf-detect": {"aarch64": ("aarch64-unknown-linux-gnu", "2.31"), "arm64": ("aarch64-unknown-linux-gnu", "2.31")},
}
GNU_INTERPRETER = {"aarch64-unknown-linux-gnu": b"/lib/ld-linux-aarch64.so.1"}
# What a glibc program of the workspace may need from the board: the C library's own libraries, nothing else (the
# NPU runtime and the vendor libraries are loaded at run time by their absolute paths).
GLIBC_NEEDED = ("libc.so.6", "libm.so.6", "libdl.so.2", "libpthread.so.0", "libgcc_s.so.1", "librt.so.1",
                "ld-linux-aarch64.so.1")
# The pinned zig and cargo-zigbuild (CI's Rust job and the builder image install exactly these).
ZIG_VERSION = "0.13.0"
CARGO_ZIGBUILD_VERSION = "0.19.8"
# Crates that compile a little C for the board (ring): clang cross-compiles it (CC_<target>, CFLAGS_<target>).
CC_CRATES = ("llmcf-gateway",)

WAYS_TEXT = ("llmcf-install is built on this computer with cargo (the pinned Rust toolchain of rust/"
             "rust-toolchain.toml) or in the framework's builder image over the container engine; it is kept and "
             "used offline afterwards.")
OFFLINE_REFUSAL = ("The bundle is not built: its root actions need llmcf-install for the board's architecture, and "
                   "offline mode is on with none kept on this computer (building it fetches the pinned Rust crates). "
                   + WAYS_TEXT + " Build it once online, or choose the unprivileged-only variant.")
NO_BUILDER = ("The bundle is not built: its root actions need llmcf-install for the board's architecture, and neither "
              "cargo nor a container engine is there to build it. " + WAYS_TEXT
              + " Or choose the unprivileged-only variant.")


def _ways(crate: str) -> str:
    return WAYS_TEXT if crate == CRATE else WAYS_TEXT.replace("llmcf-install", crate)


class HelperMissing(RuntimeError):
    """llmcf-install is not here for this architecture and cannot be made now: the plain reason."""


@dataclass(frozen=True)
class HelperBuild:
    """llmcf-install for one target: the cached program, its sha256, its version, the target and how it was made."""
    path: Path
    sha256: str
    version: str
    target: str
    way: str = "cargo"

    def record(self) -> Dict[str, str]:
        return {"version": self.version, "target": self.target, "sha256": self.sha256, "way": self.way}


def helper_version(rust_dir: Path = RUST_DIR, crate: str = CRATE) -> str:
    """The crate's version (rust/<crate>/Cargo.toml), "" when the sources are not there."""
    try:
        text = (rust_dir / crate / "Cargo.toml").read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(r'^version\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"', text, re.M)
    return m.group(1) if m else ""


def source_sha256(rust_dir: Path = RUST_DIR, crate: str = CRATE) -> str:
    """The sha256 over the helper's sources: the workspace files and the crate's files (not target/), each as its
    path and its sha256, sorted."""
    from orchestrator.utils.helpers import sha256_file
    entries = []
    for rel in _SOURCE_FILES:
        path = rust_dir / rel
        if path.is_file():
            entries.append(f"{rel} {sha256_file(path)}")
    folder = rust_dir / crate
    if folder.is_dir():
        for path in sorted(p for p in folder.rglob("*")
                           if p.is_file() and "target" not in p.relative_to(folder).parts):
            entries.append(f"{path.relative_to(rust_dir).as_posix()} {sha256_file(path)}")
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


def target_of(arch: Optional[str], crate: str = CRATE) -> str:
    """The Rust target of `crate` for a board's machine; HelperMissing when it is not built for it."""
    if crate in GNU_PROGRAMS:
        found = GNU_PROGRAMS[crate].get(str(arch or "").strip().lower())
        if not found:
            raise HelperMissing(f"{crate} is built for aarch64 boards with glibc (Rockchip's NPU runtime exists only "
                                f"there), not for {arch or 'an unknown architecture'}")
        return found[0]
    target = TARGETS.get(str(arch or "").strip().lower())
    if not target:
        raise HelperMissing(f"llmcf-install is built for x86_64, aarch64 and armv7 boards, not for "
                            f"{arch or 'an unknown architecture'}; choose the unprivileged-only variant")
    return target


def is_static_elf_for(data: bytes, target: str) -> bool:
    """Whether `data` is an ELF executable of `target`'s class and machine without a program interpreter."""
    want = _ELF.get(target)
    if want is None or len(data) < 0x34 or data[:4] != b"\x7fELF" or data[4] != want[0] or data[5] != 1:
        return False
    try:
        if struct.unpack_from("<H", data, 0x12)[0] != want[1]:
            return False
        if want[0] == 2:
            phoff = struct.unpack_from("<Q", data, 0x20)[0]
            phentsize, phnum = struct.unpack_from("<HH", data, 0x36)
        else:
            phoff = struct.unpack_from("<I", data, 0x1C)[0]
            phentsize, phnum = struct.unpack_from("<HH", data, 0x2A)
        if phnum == 0 or phentsize < 4:
            return False
        return not any(struct.unpack_from("<I", data, phoff + k * phentsize)[0] == 3 for k in range(phnum))
    except struct.error:
        return False


def _elf_dynamic(data: bytes):
    """(interpreter, needed libraries, glibc versions named) of a 64-bit little-endian ELF program; None when it is
    none."""
    if len(data) < 0x40 or data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        return None
    try:
        phoff = struct.unpack_from("<Q", data, 0x20)[0]
        phentsize, phnum = struct.unpack_from("<HH", data, 0x36)
        interp, dynamic, loads = b"", None, []
        for k in range(phnum):
            base = phoff + k * phentsize
            ptype = struct.unpack_from("<I", data, base)[0]
            offset, vaddr = struct.unpack_from("<QQ", data, base + 8)
            filesz = struct.unpack_from("<Q", data, base + 32)[0]
            if ptype == 3:
                interp = data[offset:offset + filesz].rstrip(b"\0")
            elif ptype == 2:
                dynamic = (offset, filesz)
            elif ptype == 1:
                loads.append((vaddr, offset, filesz))
        needed_offsets, strtab, strsz = [], None, 0
        if dynamic is not None:
            for i in range(0, dynamic[1] - 15, 16):
                tag, val = struct.unpack_from("<qQ", data, dynamic[0] + i)
                if tag == 0:
                    break
                if tag == 1:
                    needed_offsets.append(val)
                elif tag == 5:
                    strtab = val
                elif tag == 10:
                    strsz = val
        needed = []
        if strtab is not None:
            start = next((off + strtab - va for va, off, sz in loads if va <= strtab < va + sz), None)
            if start is None:
                return None
            table = data[start:start + strsz]
            for o in needed_offsets:
                needed.append(table[o:table.index(b"\0", o)].decode("ascii", "replace"))
            versions = sorted({m.decode() for m in re.findall(rb"GLIBC_([0-9]+\.[0-9]+(?:\.[0-9]+)?)", table)})
        else:
            versions = []
        return interp, needed, versions
    except (struct.error, ValueError):
        return None


def is_glibc_elf_for(data: bytes, target: str, baseline: str = "2.31") -> bool:
    """Whether `data` is a glibc program of `target` (only aarch64-unknown-linux-gnu): its machine, the target's
    interpreter, needed libraries only among GLIBC_NEEDED and no glibc symbol version above `baseline`."""
    if target not in GNU_INTERPRETER or len(data) < 0x40 or struct.unpack_from("<H", data, 0x12)[0] != 0xB7:
        return False
    found = _elf_dynamic(data)
    if found is None:
        return False
    interp, needed, versions = found
    limit = tuple(int(x) for x in baseline.split("."))
    return (interp == GNU_INTERPRETER[target] and bool(needed) and all(n in GLIBC_NEEDED for n in needed)
            and all(tuple(int(x) for x in v.split(".")) <= limit for v in versions))


def is_program_for(data: bytes, target: str, crate: str) -> bool:
    """The ELF check of `crate`'s program: static musl, or for a glibc program (GNU_PROGRAMS) is_glibc_elf_for."""
    if crate in GNU_PROGRAMS:
        baseline = next((b for t, b in GNU_PROGRAMS[crate].values() if t == target), "2.31")
        return is_glibc_elf_for(data, target, baseline)
    return is_static_elf_for(data, target)


def cache_folder(cache_dir: Path, target: str, crate: str = CRATE) -> Path:
    return Path(cache_dir) / (CACHE_REL if crate == CRATE else crate) / target


def _record(crate: str) -> str:
    return RECORD if crate == CRATE else f"{crate}.json"


def cached(cache_dir: Path, arch: Optional[str], rust_dir: Path = RUST_DIR, crate: str = CRATE
           ) -> Optional[HelperBuild]:
    """The cached program of `crate` (llmcf-install) for `arch` when its record, its sha256 and the current sources
    agree."""
    from orchestrator.utils.helpers import sha256_file
    try:
        target = target_of(arch, crate)
    except HelperMissing:
        return None
    folder = cache_folder(cache_dir, target, crate)
    try:
        record = json.loads((folder / _record(crate)).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    program = folder / crate
    if not isinstance(record, dict) or record.get("target") != target or not program.is_file():
        return None
    if not re.fullmatch(r"[0-9a-f]{64}", str(record.get("sha256", ""))) or sha256_file(program) != record["sha256"]:
        return None
    if record.get("way") != "test" and rust_dir.is_dir() and \
            record.get("source_sha256") != source_sha256(rust_dir, crate):
        return None   # made from other sources: made again
    return HelperBuild(program, record["sha256"], str(record.get("version", "")), target, str(record.get("way", "")))


def keep(cache_dir: Path, target: str, made: Path, way: str, rust_dir: Path = RUST_DIR,
         crate: str = CRATE) -> HelperBuild:
    """Puts a made program into the cache with its record (after the static-program check)."""
    from orchestrator.utils.helpers import sha256_file
    data = Path(made).read_bytes()
    if not is_program_for(data, target, crate):
        kind = "a glibc program within its baseline" if crate in GNU_PROGRAMS else "a static program"
        raise HelperMissing(f"the built {crate} is not {kind} for {target}, so it was not kept. " + _ways(crate))
    folder = cache_folder(cache_dir, target, crate)
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f".{crate}.tmp"
    shutil.copyfile(made, tmp)
    os.chmod(tmp, SHARED_RUN)
    os.replace(tmp, folder / crate)
    sha = sha256_file(folder / crate)
    record = {"version": helper_version(rust_dir, crate), "target": target, "sha256": sha, "way": way,
              "source_sha256": source_sha256(rust_dir, crate)}
    (folder / _record(crate)).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return HelperBuild(folder / crate, sha, record["version"], target, way)


def cc_env(crate: str, target: str) -> Dict[str, str]:
    """For a crate that compiles C (CC_CRATES): clang (and llvm-ar) cross-compiling for `target`; {} otherwise or
    without clang."""
    clang = shutil.which("clang")
    if crate not in CC_CRATES or not clang:
        return {}
    key = target.replace("-", "_")
    env = {f"CC_{key}": clang, f"CFLAGS_{key}": f"--target={target}"}
    ar = shutil.which("llvm-ar")
    if ar:
        env[f"AR_{key}"] = ar
    return env


def zigbuild_target(crate: str, target: str) -> str:
    """The target cargo-zigbuild is given for a glibc program: the cargo target with its glibc baseline."""
    baseline = next((b for t, b in GNU_PROGRAMS[crate].values() if t == target), "2.31")
    return f"{target}.{baseline}"


def host_builder(cargo: str, rust_dir: Path = RUST_DIR, crate: str = CRATE) -> Callable[[Path, str], Path]:
    """The way 'cargo': `cargo build --release --locked` of the crate for the target (a glibc program:
    `cargo zigbuild` with the target's glibc baseline, which needs zig and cargo-zigbuild here), into the work
    folder (never into the sources)."""
    def build(work: Path, target: str) -> Path:
        env = dict(os.environ)
        env["CARGO_TARGET_DIR"] = str(Path(work) / "target")
        env.update(cc_env(crate, target))
        if crate in GNU_PROGRAMS:
            if not (shutil.which("cargo-zigbuild") and shutil.which("zig")):
                raise HelperMissing(f"{crate} is linked for {zigbuild_target(crate, target)} by zig {ZIG_VERSION} "
                                    f"through cargo-zigbuild {CARGO_ZIGBUILD_VERSION}, and they are not on this "
                                    "computer; the framework's builder image has them")
            argv = [cargo, "zigbuild", "--release", "--locked", "-p", crate, "--target",
                    zigbuild_target(crate, target), "--manifest-path", str(rust_dir / "Cargo.toml")]
        else:
            argv = [cargo, "build", "--release", "--locked", "-p", crate, "--target", target,
                    "--manifest-path", str(rust_dir / "Cargo.toml")]
        done = subprocess.run(  # nosec B603 - a fixed argv, cargo from PATH (shutil.which), no shell
            argv,
            cwd=str(rust_dir), env=env, capture_output=True, text=True, timeout=BUILD_TIMEOUT)
        if done.returncode != 0:
            tail = (done.stderr or done.stdout or "").strip().splitlines()[-3:]
            raise HelperMissing(f"cargo could not build {crate}: " + " / ".join(tail)[:400])
        return Path(work) / "target" / target / "release" / crate
    return build


def engine_builder(client: Any, rust_dir: Path = RUST_DIR,
                   log: Callable[[str], None] = lambda _t: None, crate: str = CRATE) -> Callable[[Path, str], Path]:
    """The way 'image': the builder image (BUILDER_DOCKERFILE, the sources of rust/ without target/ as its context),
    then /out/llmcf-install copied out of a container of it that never runs."""
    def build(work: Path, target: str) -> Path:
        context = Path(work) / "context"
        shutil.copytree(rust_dir, context, ignore=shutil.ignore_patterns("target"))
        shutil.copyfile(BUILDER_DOCKERFILE, context / "Dockerfile")
        log(f"{crate}: building the builder image (pinned Rust image, the framework's sources) ...")
        tag = f"{BUILDER_TAG}-{target}" if crate == CRATE else f"llm-framework/{crate}-builder:1-{target}"
        image, _logs = client.images.build(path=str(context), tag=tag, rm=True, pull=False,
                                           buildargs={"TARGET": target, "CRATE": crate})
        from orchestrator.utils.docker_engine import create_container
        container = create_container(client, image=image.id, command=["true"], network_disabled=True)
        try:
            stream, _info = container.get_archive(f"/out/{crate}")
            with tarfile.open(fileobj=io.BytesIO(b"".join(stream))) as tar:
                handle = tar.extractfile(tar.getmember(crate))
                data = handle.read() if handle else b""
        finally:
            try:
                container.remove(force=True)
            except Exception:  # noqa: BLE001 - the copy is made; a left container is no harm
                pass
        out = Path(work) / crate
        out.write_bytes(data)
        return out
    return build


def default_builder(client: Any = None, rust_dir: Path = RUST_DIR,
                    log: Callable[[str], None] = lambda _t: None,
                    crate: str = CRATE) -> Optional[Callable[[Path, str], Path]]:
    """cargo on this computer first (for a glibc program only with zig and cargo-zigbuild next to it), else the
    builder image over `client` (docker-py); None when neither is there."""
    cargo = shutil.which("cargo")
    zig_ready = crate not in GNU_PROGRAMS or bool(shutil.which("cargo-zigbuild") and shutil.which("zig"))
    if cargo and rust_dir.is_dir() and zig_ready:
        return host_builder(cargo, rust_dir, crate)
    if client is not None and rust_dir.is_dir():
        return engine_builder(client, rust_dir, log, crate)
    return None


def ensure_helper(cache_dir: Path, arch: Optional[str], *, offline: bool,
                  build: Optional[Callable[[Path, str], Path]] = None, way: str = "cargo",
                  rust_dir: Path = RUST_DIR, log: Callable[[str], None] = lambda _t: None,
                  crate: str = CRATE) -> HelperBuild:
    """The program of `crate` (llmcf-install, or one of FRAMEWORK_PROGRAMS) for `arch`: the cached one, else made now
    (`build(work, target)` gives the built program; default_builder when None). Offline with nothing cached:
    HelperMissing (OFFLINE_REFUSAL for llmcf-install)."""
    if crate != CRATE and crate not in FRAMEWORK_PROGRAMS:
        raise HelperMissing(f"{crate} is no program of the framework's Rust workspace")
    target = target_of(arch, crate)
    found = cached(cache_dir, arch, rust_dir, crate)
    if found is not None:
        return found
    if offline:
        raise HelperMissing(OFFLINE_REFUSAL if crate == CRATE else
                            f"{crate} for the board's architecture is not kept on this computer, and offline mode is "
                            "on (building it fetches the pinned Rust crates). " + _ways(crate)
                            + " Build it once online.")
    if build is None:
        build = default_builder(rust_dir=rust_dir, log=log, crate=crate)
        if build is None:
            raise HelperMissing(NO_BUILDER if crate == CRATE else
                                f"{crate} is not built: neither cargo nor a container engine is there. "
                                + _ways(crate))
    from orchestrator.utils.atomic import remove_tree
    work = Path(cache_dir) / (CACHE_REL if crate == CRATE else crate) / "work"
    remove_tree(work, label=f"{crate}'s working folder")
    work.mkdir(parents=True, exist_ok=True)
    try:
        log(f"{crate}: building " + ("the root helper" if crate == CRATE else "it")
            + f" for {target} (once; kept for later bundles) ...")
        made = build(work, target)
        return keep(cache_dir, target, made, way, rust_dir, crate)
    except (OSError, subprocess.SubprocessError) as e:
        raise HelperMissing(f"{crate} could not be built: {type(e).__name__}. " + _ways(crate)) from None
    finally:
        remove_tree(work, label=f"{crate}'s working folder")


__all__ = ["BUILDER_DOCKERFILE", "CARGO_ZIGBUILD_VERSION", "CC_CRATES", "FRAMEWORK_PROGRAMS", "GLIBC_NEEDED",
           "GNU_PROGRAMS", "ZIG_VERSION", "is_glibc_elf_for", "is_program_for", "zigbuild_target", "cc_env", "HelperBuild", "HelperMissing", "NO_BUILDER", "OFFLINE_REFUSAL", "PROGRAM", "RECORD",
           "RUST_DIR", "TARGETS", "cached", "default_builder", "engine_builder", "ensure_helper", "helper_version",
           "host_builder", "is_static_elf_for", "keep", "source_sha256", "target_of"]
