#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - the framework's runtime layer of every build container (host side)

A target must not be able to get around the stage-user switch at all. So the host bind-mounts ONE framework-owned
folder read-only into every build container (LAYER_MOUNT, /llmcf): the stage runner, the stage account's helper, the
entrypoint, what every module finds (orchestrator/runtime_layer/*.sh, the shipped copy) and bin/ with a static busybox
(the runner's sh and small tools) and a static util-linux setpriv (the uid switch: --reuid/--regid/--groups,
supplementary groups such as a GPU's render group, ambient SETUID/SETGID for the runner). The host sets the container's
entrypoint and user itself; the target image and its Dockerfile cannot change these files, and a target's own
modules/build.sh is no longer executed (own_runner_note: ignored, with a plain note). The order of the stages comes
from target.yml (stage_order): the four stages plus fixed slots (before convert, between convert and target = the
rotate slot, after target) that take any module name the target declares.

No key in the container: the end record is written by the runner into the end folder (END_MOUNT), which only the
runner's uid may write; the host trusts "done" only from that folder (end_folder_state: owned by the runner, not
writable for others, the record a plain file of the runner) plus the container's real exit status plus the inspect
check (utils/sandbox.sandbox_problem). A host whose mounts keep no owner or mode (Docker Desktop on Windows and macOS)
runs the build, marked plainly "end folder unprotected".

The toolbox (bin/busybox, bin/setpriv) is never a committed third-party binary. The person picks the way
(TOOLBOX_WAYS, setting runtime_layer_toolbox, default "build"): "build" makes both from pinned source tarballs
(BUILD_SOURCES, sha256 pinned here) in the framework's own builder image (orchestrator/runtime_layer/toolbox/
Dockerfile) and caches the result; "build-rust" builds busybox the same way and the framework's own Rust uid switch
llmcf-setpriv (rust/llmcf-setpriv: exactly the setpriv options the layer uses) from this repository in a pinned Rust
builder image (toolbox/Dockerfile.rust); "fetch" takes prebuilt static binaries from the one pinned table (PREBUILT):
busybox from snapshot.debian.org with the sha256 of the archive and of the extracted binary, setpriv only as
llmcf-setpriv, a release asset of this project built by its CI (a prebuilt C setpriv is never taken). The toolbox
record says which setpriv it holds (SETPRIV_KINDS). Offline with nothing cached the build is refused with a plain
message naming the ways (ToolboxMissing).

Before every build the assembled layer (<cache>/runtime-layer/current) is checked file by file against the shipped
copy (layer_check, a fixed vocabulary of verdicts); a changed layer refuses the build and Mojo Miracle restores it
from the shipped copy (repair runtime_layer.restore). The remaining limit: a bug of the kernel or of the container
engine.

Qt-free and import-light: the builder, Mojo, the CLI, the GUI and the MCP tools read it from here. No text here names a
host path or an account (they may reach an AI): labels only.
"""


import io
import json
import os
import platform
import re
import shutil
import stat
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# The end folder's trust lives with the end record (Core/build_end.py imports nothing of the framework, so the
# end record never pulls this module in): re-exported here, where the host side of the layer is read
from orchestrator.Core.build_end import end_folder_state, mounts_keep_owners
from orchestrator.utils.next_steps import WithSteps

SHIPPED_DIR = Path(__file__).resolve().parents[1] / "runtime_layer"
SCRIPTS = ("tools.sh", "entrypoint.sh", "runner.sh", "stage_user.sh", "stage_env.sh", "cleanup.sh")
TOOLS = ("busybox", "setpriv")
MANIFEST_NAME = "MANIFEST.sha256"
TOOLBOX_RECORD = "toolbox.json"
LAYER_MOUNT = "/llmcf"
END_MOUNT = "/llmcf-end"
ENTRYPOINT = [f"{LAYER_MOUNT}/bin/busybox", "sh", f"{LAYER_MOUNT}/entrypoint.sh"]
CLEANUP_ENTRYPOINT = [f"{LAYER_MOUNT}/bin/busybox", "sh", f"{LAYER_MOUNT}/cleanup.sh"]
MODULES_IN_IMAGE = "/app/modules"

# --- Where the toolbox comes from (the person picks, default the local build) ---------------------------------------
TOOLBOX_WAYS = ("build", "build-rust", "fetch")
DEFAULT_TOOLBOX_WAY = "build"
# Which setpriv a toolbox holds: util-linux's (C, only ever built on this computer) or the framework's own Rust
# llmcf-setpriv (built here, or fetched as a release asset of this project); the way decides it
SETPRIV_KINDS = ("util-linux", "llmcf-setpriv")
WAY_SETPRIV = {"build": "util-linux", "build-rust": "llmcf-setpriv", "fetch": "llmcf-setpriv"}
# The framework's builder image: a pinned base (the same digest as the framework's own image) and pinned sources
BUILDER_BASE = "debian:13-slim@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f"
BUILDER_DOCKERFILE = SHIPPED_DIR / "toolbox" / "Dockerfile"
BUILDER_TAG = "llm-framework/runtime-toolbox-builder:2"
# The builder image of llmcf-setpriv (way "build-rust"): the Rust toolchain of rust/rust-toolchain.toml on a pinned
# base, over the workspace rust/ of this repository (its crates checked by Cargo.lock's sha256)
RUST_BUILDER_BASE = ("rust:1.97.0-slim-trixie@sha256:"
                     "686a437ead83701e8f871e66e838c3ec55f46b5fc235b025756396ac823bdc51")
RUST_BUILDER_DOCKERFILE = SHIPPED_DIR / "toolbox" / "Dockerfile.rust"
RUST_BUILDER_TAG = "llm-framework/runtime-toolbox-rust-builder:1"
RUST_WORKSPACE = SHIPPED_DIR.parents[1] / "rust"
# The toolbox record's version: a cached toolbox of another version is made again (version 2: musl instead of glibc,
# whose static programs still loaded the host's NSS modules and crashed on Ubuntu; version 3: the record names its
# setpriv). A version-2 record is read as what it is, a util-linux setpriv of the musl recipe (read_toolbox), so an
# offline computer keeps its tools.
TOOLBOX_VERSION = 3
READABLE_OLD_VERSIONS = {2: "util-linux"}
BUILD_SOURCES: Dict[str, Dict[str, str]] = {
    "util-linux": {"url": "https://cdn.kernel.org/pub/linux/utils/util-linux/v2.40/util-linux-2.40.4.tar.xz",
                   "sha256": "5c1daf733b04e9859afdc3bd87cc481180ee0f88b5c0946b16fdec931975fb79",
                   "file": "util-linux-2.40.4.tar.xz"},
    "busybox": {"url": "https://busybox.net/downloads/busybox-1.37.0.tar.bz2",
                "sha256": "3311dff32e746499f4df0d5df04d7eb396382d7e108bb9250e7b519b837043a4",
                "file": "busybox-1.37.0.tar.bz2"},
    # setpriv's capability library, built static for musl (a static glibc setpriv loads the NSS modules of the
    # image's or the host's own glibc; see the Dockerfile)
    "libcap-ng": {"url": "https://people.redhat.com/sgrubb/libcap-ng/libcap-ng-0.8.5.tar.gz",
                  "sha256": "3ba5294d1cbdfa98afaacfbc00b6af9ed2b83e8a21817185dfd844cc8c7ac6ff",
                  "file": "libcap-ng-0.8.5.tar.gz"},
}
# Prebuilt static programs of the way "fetch": ONE pinned table in the repository (orchestrator/runtime_layer/toolbox/
# prebuilt.json), per tool and architecture its URL and sha256. Debian's busybox-static is static and pinned there.
# A prebuilt setpriv is taken only as the framework's own Rust llmcf-setpriv, a release asset of this project built by
# its CI (.github/workflows/release-tools.yml on a tag tools-v<version>): kind "file", named
# llmcf-setpriv-<version>-<arch> and fetched from this project's releases; util-linux setpriv is C and is only ever
# built on this computer. While the table holds no such entry (no release yet) the way "fetch" refuses and names the
# ways that build here (PREBUILT_MISSING). The table is read, never written, here.
PREBUILT_TABLE = SHIPPED_DIR / "toolbox" / "prebuilt.json"
PREBUILT_KINDS = ("deb", "file")
RELEASES = "https://github.com/Smilez1985/LLM_Cross-Compiler_Framework/releases/download/tools-v"
# The release assets' architecture names (Rust's) for the toolbox's (Debian's)
RELEASE_ARCH = {"amd64": "x86_64", "arm64": "aarch64"}


def release_asset(version: str, arch: str) -> str:
    """The name of llmcf-setpriv's release asset of `version` for the toolbox architecture `arch` (amd64, arm64)."""
    return f"llmcf-setpriv-{version}-{RELEASE_ARCH.get(arch, arch)}"


def _setpriv_pin_ok(arch: str, pin: Dict[str, str]) -> bool:
    """Whether a setpriv pin is llmcf-setpriv from this project's releases: the program itself (kind "file"), its
    asset name for this architecture, under the release of the same version."""
    m = re.fullmatch(r"llmcf-setpriv-([0-9]+\.[0-9]+\.[0-9]+)-([a-z0-9_]+)", pin.get("name", ""))
    return bool(m and pin.get("kind") == "file" and m.group(2) == RELEASE_ARCH.get(arch)
                and pin["url"] == f"{RELEASES}{m.group(1)}/{pin['name']}")


def read_prebuilt(path: Path = PREBUILT_TABLE) -> Dict[str, Dict[str, Dict[str, str]]]:
    """The pinned table of the way "fetch": {tool: {arch: pin}}; a pin that lacks its url, sha256 (and for a Debian
    package its archive_sha256 and member) or has an unknown kind is left out, so it is never fetched, and so is a
    setpriv pin that is not llmcf-setpriv from this project's releases (_setpriv_pin_ok)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    table: Dict[str, Dict[str, Dict[str, str]]] = {tool: {} for tool in TOOLS}
    for tool in TOOLS:
        for arch, pin in ((data.get(tool) or {}) if isinstance(data, dict) else {}).items():
            if not isinstance(pin, dict):
                continue
            pin = {k: str(v) for k, v in pin.items()}
            kind = pin.setdefault("kind", "deb")
            needed = ("url", "sha256", "name") + (("archive_sha256", "member") if kind == "deb" else ())
            if kind not in PREBUILT_KINDS or not all(pin.get(k) for k in needed) \
                    or not pin["url"].startswith("https://") or not re.fullmatch(r"[0-9a-f]{64}", pin["sha256"]):
                continue
            if tool == "setpriv" and not _setpriv_pin_ok(str(arch), pin):
                continue
            table[tool][str(arch)] = pin
    return table


PREBUILT: Dict[str, Dict[str, Dict[str, str]]] = read_prebuilt()
PREBUILT_MISSING = ("No release of llmcf-setpriv is pinned for this computer yet (no release yet): a prebuilt setpriv "
                    "comes only as the framework's own Rust program llmcf-setpriv from a release of this project built "
                    "by its CI, never as util-linux setpriv. So the way 'fetch' cannot make the runtime layer's tools. "
                    "Use the way 'build' (setting runtime_layer_toolbox 'build', the default) or 'build-rust': the "
                    "framework builds busybox and setpriv itself from pinned sources.")

# --- Fixed vocabulary of the layer check (what Mojo, the CLI, the GUI, the audit log and an AI read) -----------------
VERDICTS = ("intact", "missing", "changed", "shipped_changed", "no_toolbox", "toolbox_changed")
VERDICT_TEXT = {
    "intact": "The framework's runtime layer is intact: every file matches the shipped copy.",
    "missing": "The framework's runtime layer is not assembled yet; the next build assembles it.",
    "changed": "The framework's runtime layer was changed ({names}); builds are refused until it is restored from "
               "the shipped copy.",
    "shipped_changed": "The framework's own shipped copy of the runtime layer does not match its manifest ({names}); "
                       "builds are refused. Install the framework again.",
    "no_toolbox": "The runtime layer's tools (a static busybox and a static setpriv) are not on this computer yet.",
    "toolbox_changed": "The runtime layer's tools changed since they were made ({names}); builds are refused until "
                       "they are made again.",
}
RESTORE_WAY = "Mojo Miracle restores it from the shipped copy."
# The next step of a changed layer (orchestrator/utils/next_steps): Mojo Miracle, in the CLI, the GUI and for an AI
RESTORE_STEP = "mojo.miracle"
TOOLBOX_WAY_TEXT = ("Three ways: the framework builds them itself from pinned sources (setting runtime_layer_toolbox "
                    "'build', the default, with util-linux setpriv; or 'build-rust', with the framework's own Rust "
                    "llmcf-setpriv; both need the network once), or it fetches pinned prebuilt files (setting "
                    "runtime_layer_toolbox 'fetch': busybox from snapshot.debian.org, llmcf-setpriv from a pinned "
                    "release of this project once one is published). Then they are kept and used offline.")
OFFLINE_REFUSAL = ("The build is refused: offline mode is on and the runtime layer's tools (a static busybox and a "
                   "static setpriv) are not on this computer yet. " + TOOLBOX_WAY_TEXT)
OWN_RUNNER_NOTE = ("This target ships its own stage runner ({names}); it is ignored and the framework's stage runner "
                   "is used. The target's stage modules, target.yml and Dockerfile stay as they are.")
END_UNPROTECTED = ("End folder unprotected: this host's mounts keep no owner or mode (Docker Desktop on Windows or "
                   "macOS, or the build sandbox is off), so the build counts as done by the container's exit status "
                   "and its inspect check only.")
END_PROTECTED = "End folder protected: only the stage runner could write the end record."

# The stages and the fixed slots of target.yml (stages:)
FOUR_STAGES = ("source", "config", "convert", "target")
SLOTS = ("before_convert", "between_convert_and_target", "after_target")
MODULE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
OWN_RUNNER_FILES = ("build.sh", "stage_user.sh")
# Module names a slot may never take: benchmark_module.sh measures the build host's CPU, never a build's step (a model
# for a board is measured on the board), and the runner's own words
RESERVED_MODULES = ("benchmark",)


class ToolboxMissing(RuntimeError):
    """The toolbox is not here and cannot be made now (offline, or the chosen way failed): the plain reason."""


class LayerRefused(WithSteps, RuntimeError):
    """The runtime layer cannot be used for a build: `verdict` from VERDICTS, the plain text as the message; a
    changed layer carries the next step RESTORE_STEP (`steps`)."""

    def __init__(self, verdict: str, text: str):
        super().__init__(text)
        self.verdict = verdict


@dataclass
class LayerCheck:
    """The result of layer_check: the verdict, the names (labels: file names of the layer) that differ, and the text."""
    verdict: str
    names: List[str] = field(default_factory=list)
    toolbox_way: str = ""

    @property
    def ok(self) -> bool:
        return self.verdict == "intact"

    @property
    def text(self) -> str:
        return VERDICT_TEXT[self.verdict].format(names=", ".join(label_names(self.names)) or "-")

    def facts(self) -> Dict[str, Any]:
        return {"verdict": self.verdict, "names": label_names(self.names), "toolbox": self.toolbox_way or None}


def layer_names() -> Tuple[str, ...]:
    """Every name a file of the assembled layer has (the fixed vocabulary its checks speak in)."""
    return (*SCRIPTS, *(f"bin/{tool}" for tool in TOOLS), *TOOLS)


def label_names(names: List[str]) -> List[str]:
    """The names a check reports, in the fixed vocabulary only: the layer's own file names as they are, any other name
    (a file someone put into the layer folder: its name could be any text) counted, never quoted. What a person, the
    build log and an AI read of a layer check."""
    known = set(layer_names())
    shown = [n for n in names if n in known]
    others = len([n for n in names if n not in known])
    if others:
        shown.append("1 other file" if others == 1 else f"{others} other files")
    return shown


# --- The shipped copy ---------------------------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    from orchestrator.utils.helpers import sha256_file as _sha
    return _sha(Path(path))


def _sha_bytes(data: bytes) -> str:
    from orchestrator.utils.helpers import sha256_bytes
    return sha256_bytes(data)


def manifest_text(folder: Path = SHIPPED_DIR) -> str:
    """The manifest of the layer's scripts in `folder` as it must read (one 'sha256  name' line each)."""
    return "".join(f"{sha256_file(Path(folder) / name)}  {name}\n" for name in SCRIPTS)


def read_manifest(folder: Path = SHIPPED_DIR) -> Dict[str, str]:
    values: Dict[str, str] = {}
    try:
        text = (Path(folder) / MANIFEST_NAME).read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError):
        return values
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            values[parts[1]] = parts[0]
    return values


def shipped_problems(folder: Path = SHIPPED_DIR) -> List[str]:
    """The script names of the shipped copy that differ from its manifest (empty: the shipped copy is whole)."""
    manifest = read_manifest(folder)
    wrong = []
    for name in SCRIPTS:
        path = Path(folder) / name
        try:
            ok = path.is_file() and not path.is_symlink() and manifest.get(name) == sha256_file(path)
        except OSError:
            ok = False
        if not ok:
            wrong.append(name)
    return wrong


# --- The toolbox --------------------------------------------------------------------------------------------------

def machine() -> str:
    """The toolbox's architecture name (Debian's): amd64 or arm64; another machine as it is."""
    m = platform.machine().lower()
    return {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(m, m)


def toolbox_dir(cache_dir: Path, arch: Optional[str] = None) -> Path:
    return Path(cache_dir) / "runtime-layer" / "toolbox" / (arch or machine())


def layer_dir(cache_dir: Path) -> Path:
    return Path(cache_dir) / "runtime-layer" / "current"


def read_toolbox(folder: Path) -> Optional[Dict[str, Any]]:
    """The toolbox record ({way, arch, sha256: {tool: digest}, setpriv, version}) when the folder holds a complete
    toolbox of the current recipe. A record of version 2 (before the record named its setpriv) holds the musl
    util-linux setpriv of the way 'build' (the only setpriv any way could make then) and is read as such; any other
    version is not used, so the toolbox is made again."""
    try:
        record = json.loads((Path(folder) / TOOLBOX_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    digests = record.get("sha256") if isinstance(record, dict) else None
    if not isinstance(digests, dict) or record.get("way") not in TOOLBOX_WAYS + ("test",):
        return None
    if record.get("way") != "test":
        version = record.get("version")
        if version in READABLE_OLD_VERSIONS and "setpriv" not in record:
            record = {**record, "setpriv": READABLE_OLD_VERSIONS[version]}
        elif version != TOOLBOX_VERSION:
            return None   # made before the current recipe (e.g. glibc-linked): made again
        if record.get("setpriv") not in SETPRIV_KINDS:
            return None
    if any(not re.fullmatch(r"[0-9a-f]{64}", str(digests.get(t, ""))) or not (Path(folder) / t).is_file()
           for t in TOOLS):
        return None
    return record


def toolbox_setpriv(record: Optional[Dict[str, Any]]) -> Optional[str]:
    """Which setpriv a toolbox record holds (SETPRIV_KINDS), None without a record or for a test toolbox."""
    kind = (record or {}).get("setpriv")
    return kind if kind in SETPRIV_KINDS else None


def write_toolbox(folder: Path, way: str, files: Dict[str, Path], sources: Dict[str, Any],
                  setpriv: Optional[str] = None) -> Dict[str, Any]:
    """Puts the made toolbox `files` ({tool: path}) into `folder` with its record and returns the record; `setpriv`
    (SETPRIV_KINDS) is the setpriv it holds, by default the one the way makes (WAY_SETPRIV)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    digests = {}
    for tool in TOOLS:
        target = folder / tool
        tmp = folder / f".{tool}.tmp"
        shutil.copyfile(files[tool], tmp)
        os.chmod(tmp, 0o555)
        if os.name == "nt" and target.exists():
            # Windows refuses to replace a read-only file (WinError 5): the old tool of an earlier toolbox is made
            # writable first, so a toolbox made again replaces it as on POSIX.
            os.chmod(target, stat.S_IREAD | stat.S_IWRITE)
        os.replace(tmp, target)
        digests[tool] = sha256_file(target)
    record = {"way": way, "arch": folder.name, "sha256": digests, "sources": sources, "version": TOOLBOX_VERSION,
              "setpriv": setpriv or WAY_SETPRIV.get(way, "util-linux")}
    (folder / TOOLBOX_RECORD).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return record


def _ar_members(data: bytes):
    """The members of a Debian package (an ar archive): (name, bytes)."""
    if data[:8] != b"!<arch>\n":
        raise ValueError("not a Debian package")
    i = 8
    while i + 60 <= len(data):
        head = data[i:i + 60]
        name = head[:16].decode("ascii", "replace").strip().rstrip("/")
        size = int(head[48:58].decode("ascii").strip())
        i += 60
        yield name, data[i:i + size]
        i += size + (size & 1)


def is_static_elf(data: bytes) -> bool:
    """Whether `data` is an ELF executable without a program interpreter (statically linked)."""
    import struct
    if len(data) < 0x40 or data[:4] != b"\x7fELF" or data[4] != 2:   # 64-bit only (amd64, arm64)
        return False
    endian = "<" if data[5] == 1 else ">"
    try:
        phoff = struct.unpack_from(endian + "Q", data, 0x20)[0]
        phentsize, phnum = struct.unpack_from(endian + "HH", data, 0x36)
        if phnum == 0 or phentsize < 4:
            return False
        return not any(struct.unpack_from(endian + "I", data, phoff + k * phentsize)[0] == 3 for k in range(phnum))
    except struct.error:   # a cut-off file: its program headers are not all there
        return False


def extract_prebuilt(archive: bytes, pin: Dict[str, str]) -> bytes:
    """The pinned program out of a pinned download: a Debian package (kind "deb": the archive's sha256, then the
    member's sha256) or the program itself (kind "file": its sha256), and that it is a static ELF. ValueError with the
    plain reason otherwise. Nothing is executed."""
    if pin.get("kind", "deb") == "file":
        if _sha_bytes(archive) != pin["sha256"]:
            raise ValueError(f"the downloaded {pin['name']} does not have its pinned sha256")
        if not is_static_elf(archive):
            raise ValueError(f"the downloaded {pin['name']} is not statically linked")
        return archive
    if _sha_bytes(archive) != pin["archive_sha256"]:
        raise ValueError(f"the downloaded {pin['name']} does not have its pinned sha256")
    for name, body in _ar_members(archive):
        if name.startswith("data.tar"):
            with tarfile.open(fileobj=io.BytesIO(body)) as tar:
                member = tar.getmember(pin["member"])
                if not member.isfile():
                    break
                handle = tar.extractfile(member)
                data = handle.read() if handle else b""
            if _sha_bytes(data) != pin["sha256"]:
                raise ValueError(f"the binary in {pin['name']} does not have its pinned sha256")
            if not is_static_elf(data):
                raise ValueError(f"the binary in {pin['name']} is not statically linked")
            return data
    raise ValueError(f"{pin['name']} does not hold {pin['member']}")


def toolbox_way(config: Any) -> str:
    """The person's choice (setting runtime_layer_toolbox), default the local build."""
    getter = getattr(config, "get", None)
    try:
        way = str(getter("runtime_layer_toolbox", DEFAULT_TOOLBOX_WAY) if callable(getter)
                  else getattr(config, "runtime_layer_toolbox", DEFAULT_TOOLBOX_WAY) or DEFAULT_TOOLBOX_WAY)
    except Exception:  # noqa: BLE001 - an unreadable setting counts as the default
        way = DEFAULT_TOOLBOX_WAY
    return way if way in TOOLBOX_WAYS else DEFAULT_TOOLBOX_WAY


TOOLBOX_SETTING = "runtime_layer_toolbox"


class ToolboxWayError(ValueError):
    """A way of making the toolbox that does not exist (TOOLBOX_WAYS)."""


def set_toolbox_way(config: Any, way: str) -> str:
    """The person's choice of the toolbox way (TOOLBOX_WAYS), saved in the setting runtime_layer_toolbox (only the
    person sets it: llm/settings_change.PERSON_ONLY_SETTINGS; the CLI and the GUI call this). A toolbox made before
    stays until it is made again; the next build without one uses the chosen way. Returns the way."""
    way = str(way or "").strip().lower()
    if way not in TOOLBOX_WAYS:
        raise ToolboxWayError(f"'{way}' is no way of making the runtime layer's tools; the ways are "
                              f"{', '.join(TOOLBOX_WAYS)}.")
    before = config.get(TOOLBOX_SETTING, DEFAULT_TOOLBOX_WAY) if callable(getattr(config, "get", None)) else None
    saver = getattr(config, "save_user_config", None)
    if config.set(TOOLBOX_SETTING, way) is False or (callable(saver) and not saver()):
        config.set(TOOLBOX_SETTING, before)   # the setting stays as it was, also in this program
        raise ToolboxWayError("The choice could not be saved in the framework's settings; it stays as it was.")
    return way


def cache_dir_of(config: Any, resolve: Optional[Callable[[str], Any]] = None) -> Path:
    """The folder the builds keep the runtime layer in (the setting cache_dir, relative to the installation: the same
    rule as BuildEngine.cache_dir); `resolve` is the framework's path rule (Core/framework.resolve_install_path)."""
    getter = getattr(config, "get", None)
    value = str((getter("cache_dir", "cache") if callable(getter) else None) or "cache")
    if resolve is None:   # the framework's rule (Core/framework.resolve_install_path), without importing the kernel
        path = Path(value).expanduser()
        return path if path.is_absolute() else SHIPPED_DIR.parents[1] / path
    return Path(resolve(value))


def layer_status(cache_dir: Path, config: Any = None, platform_name: Optional[str] = None) -> Dict[str, Any]:
    """What the CLI, the GUI, Mojo and an AI read about the runtime layer here, labels only: the layer's verdict
    (VERDICTS) and its text, the chosen toolbox way and the way the cached toolbox was made, which setpriv it holds
    (SETPRIV_KINDS), whether the way 'fetch' has every pinned tool for this machine, and whether this host's mounts
    protect the end folder."""
    check = layer_check(cache_dir)
    record = read_toolbox(toolbox_dir(cache_dir))
    arch = machine()
    return {"verdict": check.verdict, "names": label_names(check.names), "text": check.text,
            "toolbox_way": toolbox_way(config) if config is not None else DEFAULT_TOOLBOX_WAY,
            "toolbox_made": (record or {}).get("way") or None,
            "toolbox_setpriv": toolbox_setpriv(record),
            "fetch_ready": all(PREBUILT.get(t, {}).get(arch) for t in TOOLS),
            "end_folder": "protected" if mounts_keep_owners(platform_name) else "unprotected"}


def ensure_toolbox(cache_dir: Path, way: str, *, offline: bool,
                   fetch: Optional[Callable[[str], bytes]] = None,
                   build: Optional[Callable[[Path], Dict[str, Path]]] = None,
                   log: Callable[[str], None] = lambda _t: None) -> Dict[str, Any]:
    """The toolbox record for this computer, making the toolbox the person's way when none is cached. `fetch(url)`
    returns the bytes of a pinned URL (through the framework's checked downloader); `build(work)` builds both tools
    in the builder image and returns their paths, `build(work, setpriv="llmcf-setpriv")` for the way "build-rust".
    Offline with nothing cached: ToolboxMissing (OFFLINE_REFUSAL). A toolbox made here replaces the tools an
    assembled layer still holds when that layer was whole for the old record (the shipped scripts and the tools it
    named): it is assembled again from the shipped copy and the new tools. Any other layer is left for ready_layer,
    which refuses it as 'changed' (never replaced silently)."""
    folder = toolbox_dir(cache_dir)
    record = read_toolbox(folder)
    if record is not None:
        return record
    if offline:
        raise ToolboxMissing(OFFLINE_REFUSAL)
    if way not in TOOLBOX_WAYS:
        way = DEFAULT_TOOLBOX_WAY
    was_whole = _layer_whole_for_old_record(cache_dir)
    record = _make_toolbox(cache_dir, way, fetch, build, log)
    if was_whole:
        # the assembled layer held exactly the shipped scripts and the tools of the old record (an older recipe):
        # it is assembled again with the new tools. Any other layer (changed by hand, or tools no record names) is
        # never replaced silently here: ready_layer refuses it as 'changed' and Mojo Miracle restores it.
        log("Runtime layer: assembling the layer again with the new tools ...")
        assemble(cache_dir)
    return record


def _layer_whole_for_old_record(cache_dir: Path) -> bool:
    """Whether the assembled layer is exactly the shipped scripts plus the tools the toolbox's present record names
    (also a record of an older recipe that read_toolbox no longer takes): only such a layer is assembled again after
    the toolbox is made anew."""
    folder = layer_dir(cache_dir)
    if not folder.is_dir() or folder.is_symlink() or shipped_problems():
        return False
    try:
        old = json.loads((toolbox_dir(cache_dir) / TOOLBOX_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    digests = old.get("sha256") if isinstance(old, dict) else None
    if not isinstance(digests, dict) or any(not re.fullmatch(r"[0-9a-f]{64}", str(digests.get(t, "")))
                                            for t in TOOLS):
        return False
    expected = {name: digest for name, digest in read_manifest().items() if name in SCRIPTS}
    expected.update({f"bin/{tool}": str(digests[tool]) for tool in TOOLS})
    return not _layer_changes(folder, expected)


def _make_toolbox(cache_dir: Path, way: str, fetch: Optional[Callable[[str], bytes]],
                  build: Optional[Callable[..., Dict[str, Path]]], log: Callable[[str], None]) -> Dict[str, Any]:
    """Makes the toolbox `way` (TOOLBOX_WAYS) in a fresh working folder and writes it with its record."""
    folder = toolbox_dir(cache_dir)
    setpriv = WAY_SETPRIV[way]
    work = Path(cache_dir) / "runtime-layer" / "work"
    _remove_work(work)
    work.mkdir(parents=True, exist_ok=True)
    try:
        files: Dict[str, Path] = {}
        sources: Dict[str, Any] = {}
        if way == "fetch":
            arch = machine()
            missing = [t for t in TOOLS if not PREBUILT.get(t, {}).get(arch)]
            if missing:
                # nothing is fetched while a tool has no pinned entry: the way "fetch" is all or nothing, and
                # "build" is named (never a silent switch to the other way)
                raise ToolboxMissing(PREBUILT_MISSING if "setpriv" in missing else
                                     f"No prebuilt {', '.join(missing)} is pinned for this computer. " +
                                     TOOLBOX_WAY_TEXT)
            for tool in TOOLS:
                pin = PREBUILT[tool][arch]
                if fetch is None:
                    raise ToolboxMissing("The framework's downloader is not available, so the runtime layer's tools "
                                         "cannot be fetched. " + TOOLBOX_WAY_TEXT)
                log(f"Runtime layer: fetching the pinned {pin['name']} ...")
                data = extract_prebuilt(fetch(pin["url"]), pin)
                path = work / tool
                path.write_bytes(data)
                files[tool] = path
                sources[tool] = {"way": "fetch", "url": pin["url"], "sha256": pin["sha256"]}
        else:
            if build is None:
                raise ToolboxMissing("The container engine is not available, so the runtime layer's tools cannot be "
                                     "built. " + TOOLBOX_WAY_TEXT)
            if setpriv == "llmcf-setpriv":
                log("Runtime layer: building a static busybox from pinned sources and the framework's static "
                    "llmcf-setpriv (once) ...")
                files = build(work, setpriv=setpriv)
            else:
                log("Runtime layer: building a static busybox and a static setpriv from pinned sources (once) ...")
                files = build(work)
            sources = {"busybox": {"way": way, "source": BUILD_SOURCES["busybox"]["url"]},
                       "setpriv": ({"way": way, "source": "rust/llmcf-setpriv (Cargo.lock)",
                                    "builder": RUST_BUILDER_BASE} if setpriv == "llmcf-setpriv"
                                   else {"way": way, "source": BUILD_SOURCES["util-linux"]["url"]})}
        for tool in TOOLS:
            data = Path(files[tool]).read_bytes()
            if not is_static_elf(data):
                raise ToolboxMissing(f"The made {tool} is not a static program, so it was not kept. " + TOOLBOX_WAY_TEXT)
        return write_toolbox(folder, way, files, sources, setpriv=setpriv)
    except ValueError as e:
        raise ToolboxMissing(f"The runtime layer's tools could not be made: {e}. " + TOOLBOX_WAY_TEXT) from e
    finally:
        _remove_work(work)


def _remove_work(work: Path) -> None:
    """The toolbox's own working folder (downloaded tarballs, the copied tools): removed through the one removal
    helper, which says what it could not remove."""
    from orchestrator.utils.atomic import remove_tree
    remove_tree(work, label="the runtime layer's working folder")


def builder_context(work: Path, fetch: Callable[[str], bytes]) -> Path:
    """The build context of the builder image: its Dockerfile and the pinned source tarballs, each checked by its
    sha256 before it is used (the Dockerfile checks them again)."""
    context = Path(work) / "context"
    context.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(BUILDER_DOCKERFILE, context / "Dockerfile")
    for pin in BUILD_SOURCES.values():
        data = fetch(pin["url"])
        if _sha_bytes(data) != pin["sha256"]:
            raise ValueError(f"the downloaded {pin['file']} does not have its pinned sha256")
        (context / pin["file"]).write_bytes(data)
    return context


def rust_context(work: Path) -> Path:
    """The build context of llmcf-setpriv's builder image (way "build-rust"): its Dockerfile and the framework's Cargo
    workspace rust/ (sources, Cargo.toml, Cargo.lock; never a build folder). The crates come in the image build by
    Cargo.lock's sha256 (cargo build --locked)."""
    if not (RUST_WORKSPACE / "Cargo.lock").is_file() or not (RUST_WORKSPACE / "llmcf-setpriv").is_dir():
        raise ValueError("the framework's Rust workspace (rust/ with llmcf-setpriv) is not part of this installation")
    context = Path(work) / "rust-context"
    context.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(RUST_BUILDER_DOCKERFILE, context / "Dockerfile")
    shutil.copytree(RUST_WORKSPACE, context / "rust", ignore=shutil.ignore_patterns("target", ".git"))
    return context


def engine_builder(client: Any, fetch: Callable[[str], bytes],
                   log: Callable[[str], None] = lambda _t: None) -> Callable[..., Dict[str, Path]]:
    """The ways 'build' and 'build-rust' over the container engine `client` (docker-py): the builder image from
    builder_context (and for setpriv="llmcf-setpriv" also llmcf-setpriv's builder image from rust_context), then
    /out/busybox and /out/setpriv copied out of a container of each that never runs."""
    def build(work: Path, setpriv: str = "util-linux") -> Dict[str, Path]:
        context = builder_context(work, fetch)
        log("Runtime layer: building the builder image (pinned base, pinned sources) ...")
        image, _logs = client.images.build(path=str(context), tag=BUILDER_TAG, rm=True, pull=False)
        made_in = {tool: image.id for tool in TOOLS}
        if setpriv == "llmcf-setpriv":
            rust = rust_context(work)
            log("Runtime layer: building llmcf-setpriv in its builder image (pinned base, Cargo.lock) ...")
            rust_image, _logs = client.images.build(path=str(rust), tag=RUST_BUILDER_TAG, rm=True, pull=False)
            made_in["setpriv"] = rust_image.id
        from orchestrator.utils.docker_engine import create_container
        files: Dict[str, Path] = {}
        for image_id in dict.fromkeys(made_in.values()):
            container = create_container(client, image=image_id, command=["true"], network_disabled=True)
            try:
                for tool in (t for t in TOOLS if made_in[t] == image_id):
                    stream, _info = container.get_archive(f"/out/{tool}")
                    with tarfile.open(fileobj=io.BytesIO(b"".join(stream))) as tar:
                        member = tar.getmember(tool)
                        handle = tar.extractfile(member)
                        data = handle.read() if handle else b""
                    path = Path(work) / tool
                    path.write_bytes(data)
                    files[tool] = path
            finally:
                try:
                    container.remove(force=True)
                except Exception:  # noqa: BLE001 - the copy is made; a left container is no harm
                    pass
        return files
    return build


# --- The assembled layer ------------------------------------------------------------------------------------------

def expected_files(cache_dir: Path) -> Dict[str, str]:
    """name -> sha256 of every file the assembled layer holds (Mojo Miracle's restore puts back exactly these)."""
    return _expected(cache_dir)[0]


def layer_mode(name: str) -> int:
    """The mode a file of the assembled layer has (assemble): the scripts read-only, the tools read and run."""
    return 0o555 if name.startswith("bin/") else 0o444


def shipped_source(cache_dir: Path, name: str) -> Path:
    """Where the layer's file `name` comes from: the shipped copy (a script) or the toolbox (bin/TOOL)."""
    if name.startswith("bin/"):
        return toolbox_dir(cache_dir) / name[len("bin/"):]
    return SHIPPED_DIR / name


def _expected(cache_dir: Path) -> Tuple[Dict[str, str], Optional[Dict[str, Any]]]:
    """name -> sha256 the assembled layer must hold: the shipped scripts' manifest and the toolbox's record."""
    expected = {name: digest for name, digest in read_manifest().items() if name in SCRIPTS}
    record = read_toolbox(toolbox_dir(cache_dir))
    if record is not None:
        for tool in TOOLS:
            expected[f"bin/{tool}"] = record["sha256"][tool]
    return expected, record


def layer_check(cache_dir: Path) -> LayerCheck:
    """The verdict of the assembled layer against the shipped copy (VERDICTS): the shipped copy against its
    manifest, the toolbox against its record, every file of the layer (and nothing else) against both."""
    wrong = shipped_problems()
    if wrong:
        return LayerCheck("shipped_changed", wrong)
    expected, record = _expected(cache_dir)
    if record is None:
        return LayerCheck("no_toolbox")
    box = toolbox_dir(cache_dir)
    changed_tools = [t for t in TOOLS if sha256_file(box / t) != record["sha256"][t]]
    if changed_tools:
        return LayerCheck("toolbox_changed", changed_tools, record.get("way", ""))
    folder = layer_dir(cache_dir)
    if not folder.is_dir() or folder.is_symlink():
        return LayerCheck("missing", toolbox_way=record.get("way", ""))
    changed = _layer_changes(folder, expected)
    if changed:
        return LayerCheck("changed", changed, record.get("way", ""))
    return LayerCheck("intact", toolbox_way=record.get("way", ""))


def _layer_changes(folder: Path, expected: Dict[str, str]) -> List[str]:
    """The names of the assembled layer `folder` that differ from `expected` (name -> sha256): changed, missing,
    extra, a link, or writable by others than the owner."""
    found: Dict[str, Path] = {}
    extra: List[str] = []
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder).as_posix()
        if path.is_symlink() or not (path.is_file() or (path.is_dir() and rel == "bin")):
            extra.append(rel)
        elif path.is_file():
            if rel in expected:
                found[rel] = path
            else:
                extra.append(rel)
    changed = sorted(set(extra) | {rel for rel in expected if rel not in found}
                     | {rel for rel, p in found.items() if sha256_file(p) != expected[rel]})
    if os.name == "posix":
        for rel, p in found.items():
            mode = stat.S_IMODE(os.lstat(p).st_mode)
            if mode & 0o022:
                changed.append(rel)
        changed = sorted(set(changed))
    return changed


def assemble(cache_dir: Path) -> LayerCheck:
    """Assembles the layer from the shipped copy and the toolbox (a fresh folder, then moved into place) and returns
    the check of the result. Refuses (LayerRefused) when the shipped copy or the toolbox is not whole."""
    wrong = shipped_problems()
    if wrong:
        raise LayerRefused("shipped_changed", VERDICT_TEXT["shipped_changed"].format(names=", ".join(wrong)))
    record = read_toolbox(toolbox_dir(cache_dir))
    if record is None:
        raise LayerRefused("no_toolbox", VERDICT_TEXT["no_toolbox"])
    folder = layer_dir(cache_dir)
    fresh = folder.with_name("current.new")
    if fresh.exists():
        _remove_layer_folder(fresh)
    (fresh / "bin").mkdir(parents=True)
    for name in SCRIPTS:
        shutil.copyfile(SHIPPED_DIR / name, fresh / name)
        os.chmod(fresh / name, 0o444)
    for tool in TOOLS:
        shutil.copyfile(toolbox_dir(cache_dir) / tool, fresh / "bin" / tool)
        os.chmod(fresh / "bin" / tool, 0o555)
    os.chmod(fresh / "bin", 0o555)
    os.chmod(fresh, 0o555)
    old = folder.with_name("current.old")
    if folder.exists() or folder.is_symlink():
        if old.exists():
            _remove_layer_folder(old)
        os.replace(folder, old)
    os.replace(fresh, folder)
    if old.exists():
        _remove_layer_folder(old)
    return layer_check(cache_dir)


def _remove_layer_folder(folder: Path) -> None:
    """The framework's own read-only layer folder (never the person's files): writable again, then removed."""
    from orchestrator.utils.atomic import remove_tree
    if Path(folder).is_symlink():
        Path(folder).unlink()
        return
    for path in [Path(folder), *Path(folder).rglob("*")]:
        try:
            if not path.is_symlink():
                os.chmod(path, 0o755 if path.is_dir() else 0o644)
        except OSError:
            pass
    remove_tree(folder, label="the runtime layer")


def ready_layer(cache_dir: Path) -> LayerCheck:
    """Before every build: the layer as it must be. Assembles it when it is missing (the first build); a changed
    layer is NOT replaced silently: LayerRefused("changed") names the files and the way (Mojo Miracle restores it)."""
    check = layer_check(cache_dir)
    if check.verdict == "missing":
        check = assemble(cache_dir)
    if not check.ok:
        if check.verdict == "changed":
            raise LayerRefused(check.verdict, check.text + " " + RESTORE_WAY).with_steps(RESTORE_STEP)
        raise LayerRefused(check.verdict, check.text)
    return check


# --- What the host gives the container ---------------------------------------------------------------------------

def layer_volumes(cache_dir: Path, end_dir: Optional[Path]) -> Dict[str, Dict[str, str]]:
    """The layer read-only at LAYER_MOUNT, the end folder (when there is one) at END_MOUNT."""
    vols = {str(layer_dir(cache_dir)): {"bind": LAYER_MOUNT, "mode": "ro"}}
    if end_dir is not None:
        vols[str(end_dir)] = {"bind": END_MOUNT, "mode": "rw"}
    return vols


def end_folder(build_temp: Path, job: str) -> Path:
    """The end folder of one job of a build (one per stage container: build, imatrix)."""
    return Path(build_temp) / f"end-{job}"


def prepare_end_folder(path: Path, owner: Optional[Tuple[int, int]]) -> None:
    """A fresh end folder: emptied of an earlier run, owned by the runner (a root host hands it over), 0755 (nobody
    but the runner may write it; the stage account shares the runner's group, so the group gets no write)."""
    path = Path(path)
    if path.is_symlink():
        path.unlink()
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    if os.name == "posix":
        if owner is not None and os.getuid() == 0:
            os.chown(path, *owner)
        os.chmod(path, 0o755)


def runner_owner_on_host(runner: Tuple[int, int], rootless: bool) -> Optional[int]:
    """The host uid that owns what the runner writes: the person under a rootless engine (container root is the
    person), the runner's uid itself under a rootful one; None on a host without POSIX ids."""
    if not hasattr(os, "getuid"):
        return None
    return os.getuid() if rootless else int(runner[0])


# --- Stage order from target.yml ----------------------------------------------------------------------------------

def stage_order(target_dir: Path, job: str = "build") -> Tuple[List[str], List[str]]:
    """(stages, problems) of a target: the four stages with the modules its target.yml declares in the fixed slots
    (stages: before_convert, between_convert_and_target, after_target), each a valid module name whose file
    modules/<name>_module.sh exists. A target without the block keeps the rotate slot as before (rotate_module.sh
    when it is there). The imatrix job runs the stages up to convert."""
    import yaml
    target_dir = Path(target_dir)
    modules = target_dir / "modules"
    problems: List[str] = []
    slots: Dict[str, List[str]] = {slot: [] for slot in SLOTS}
    try:
        meta = yaml.safe_load((target_dir / "target.yml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        meta = {}
    block = meta.get("stages") if isinstance(meta, dict) else None
    if block is None:
        if (modules / "rotate_module.sh").is_file():
            slots["between_convert_and_target"] = ["rotate"]
    elif not isinstance(block, dict):
        problems.append("target.yml: 'stages' must be a block with the slots " + ", ".join(SLOTS) + ".")
    else:
        for key, value in block.items():
            if key not in SLOTS:
                problems.append(f"target.yml: 'stages' has no slot '{key}' (the slots: {', '.join(SLOTS)}).")
                continue
            names = value if isinstance(value, list) else ([] if value in (None, "") else [value])
            for name in names:
                name = str(name)
                if not MODULE_NAME.fullmatch(name) or name in FOUR_STAGES:
                    problems.append(f"target.yml: '{name}' in the slot {key} is not a valid module name (lower case "
                                    "letters, digits and _, not one of the four stages).")
                elif name in RESERVED_MODULES:
                    problems.append(f"target.yml: '{name}' in the slot {key} is not allowed: benchmark_module.sh never "
                                    "runs in a build (it would measure this build computer; a model for a board is "
                                    "measured on the board).")
                elif not (modules / f"{name}_module.sh").is_file():
                    problems.append(f"target.yml: the slot {key} names {name}, but modules/{name}_module.sh is "
                                    "missing.")
                elif any(name in s for s in slots.values()):
                    problems.append(f"target.yml: {name} is named in more than one slot.")
                else:
                    slots[key].append(name)
    stages = ["source", "config", *slots["before_convert"], "convert"]
    if job != "imatrix":
        stages += [*slots["between_convert_and_target"], "target", *slots["after_target"]]
    return stages, problems


def own_runner_note(target_dir: Path) -> Optional[str]:
    """OWN_RUNNER_NOTE when the target (or the pack it came from) ships its own runner files, else None."""
    target_dir = Path(target_dir)
    try:
        in_framework = target_dir.resolve().parent == SHIPPED_DIR.parents[1].resolve() / "targets"
    except OSError:
        in_framework = False
    # The targets folder also holds installed packs and the person's (or an AI's) own targets: only a target the
    # framework ships there counts (rag_manager.shipped_target: the template, or an official target without a pack's
    # provenance file that no AI made)
    from orchestrator.Core.rag_manager import shipped_target
    if in_framework and shipped_target(target_dir):
        # the framework's own targets keep their old runner files for runs by hand only (the runner of every build
        # is the runtime layer's): no note for them
        return None
    names = [f"modules/{n}" for n in OWN_RUNNER_FILES if (target_dir / "modules" / n).exists()]
    return OWN_RUNNER_NOTE.format(names=", ".join(names)) if names else None


__all__ = ["BUILD_SOURCES", "BUILDER_BASE", "CLEANUP_ENTRYPOINT", "DEFAULT_TOOLBOX_WAY", "END_MOUNT",
           "END_PROTECTED", "END_UNPROTECTED", "ENTRYPOINT", "FOUR_STAGES", "LAYER_MOUNT", "LayerCheck",
           "LayerRefused", "MODULES_IN_IMAGE", "OFFLINE_REFUSAL", "OWN_RUNNER_NOTE", "PREBUILT", "RESERVED_MODULES", "RESTORE_STEP", "RESTORE_WAY",
           "SCRIPTS", "SHIPPED_DIR", "SLOTS", "TOOLBOX_WAYS", "TOOLS", "ToolboxMissing", "VERDICTS", "VERDICT_TEXT",
           "assemble", "builder_context", "end_folder", "layer_status", "read_prebuilt", "set_toolbox_way", "cache_dir_of",
           "PREBUILT_TABLE", "TOOLBOX_SETTING", "ToolboxWayError", "end_folder_state", "ensure_toolbox", "extract_prebuilt",
           "is_static_elf", "layer_check", "layer_dir", "layer_volumes", "manifest_text", "mounts_keep_owners",
           "own_runner_note", "prepare_end_folder", "expected_files", "label_names", "layer_mode", "layer_names",
           "shipped_source", "read_manifest", "read_toolbox", "ready_layer",
           "runner_owner_on_host", "shipped_problems", "stage_order", "toolbox_dir", "toolbox_way", "write_toolbox",
           "PREBUILT_MISSING", "RELEASES", "RELEASE_ARCH", "RUST_BUILDER_BASE", "RUST_WORKSPACE", "SETPRIV_KINDS",
           "TOOLBOX_VERSION", "WAY_SETPRIV", "release_asset", "rust_context", "toolbox_setpriv"]
