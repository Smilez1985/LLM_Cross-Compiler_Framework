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
from typing import Any, Callable, Dict, Optional

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
FRAMEWORK_PROGRAMS = ("llmcf-gateway",)
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


def target_of(arch: Optional[str]) -> str:
    """The Rust target for a board's machine; HelperMissing when llmcf-install is not built for it."""
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
        target = target_of(arch)
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
    if not is_static_elf_for(data, target):
        raise HelperMissing(f"the built {crate} is not a static program for {target}, so it was not kept. "
                            + _ways(crate))
    folder = cache_folder(cache_dir, target, crate)
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f".{crate}.tmp"
    shutil.copyfile(made, tmp)
    os.chmod(tmp, 0o755)
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


def host_builder(cargo: str, rust_dir: Path = RUST_DIR, crate: str = CRATE) -> Callable[[Path, str], Path]:
    """The way 'cargo': `cargo build --release --locked` of the crate for the target, into the work folder (never
    into the sources)."""
    def build(work: Path, target: str) -> Path:
        env = dict(os.environ)
        env["CARGO_TARGET_DIR"] = str(Path(work) / "target")
        env.update(cc_env(crate, target))
        done = subprocess.run(  # nosec B603 - a fixed argv, cargo from PATH (shutil.which), no shell
            [cargo, "build", "--release", "--locked", "-p", crate, "--target", target,
             "--manifest-path", str(rust_dir / "Cargo.toml")],
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
    """cargo on this computer first, else the builder image over `client` (docker-py); None when neither is there."""
    cargo = shutil.which("cargo")
    if cargo and rust_dir.is_dir():
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
    target = target_of(arch)
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


__all__ = ["BUILDER_DOCKERFILE", "CC_CRATES", "FRAMEWORK_PROGRAMS", "cc_env", "HelperBuild", "HelperMissing", "NO_BUILDER", "OFFLINE_REFUSAL", "PROGRAM", "RECORD",
           "RUST_DIR", "TARGETS", "cached", "default_builder", "engine_builder", "ensure_helper", "helper_version",
           "host_builder", "is_static_elf_for", "keep", "source_sha256", "target_of"]
