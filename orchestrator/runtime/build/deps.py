#!/usr/bin/env python3
"""
The dependency cache of module CMake options (DECISIONS round 25 Q6 addendum): what a download option of the pinned
llama.cpp would fetch while it is built (a FetchContent dependency: a TLS library, a backend's third-party source) is
fetched BEFORE the build, outside the build sandbox and never in offline mode, pinned exactly as the pinned llama.cpp
declares it (source address and tag or version; DEPENDENCIES names the CMake lines), and kept in a local cache below
the framework's cache folder. The build gets it read-only (a bind mount into the build container) with
FETCHCONTENT_SOURCE_DIR_<NAME> pointing at it and FETCHCONTENT_FULLY_DISCONNECTED=ON, so the build itself stays
without network (targets/*/modules/target_module.sh: LLMCF_DEPS, prefetched_dependency). An option of the recipe's
NEVER_SET or DOWNLOAD_OPTIONS_OFF becomes allowed only through this path (recipe.DEPENDENCY_OF_OPTION, the
`prefetched` names of the CMake flag verdict).

- Pinned: a git dependency is cloned at its tag (depth 1) and its commit recorded; an archive is checked against the
  hash llama.cpp declares (KleidiAI: MD5) and its sha256 recorded. On the first fetch the cache records the sha256 of
  the whole tree (tree_sha256) in pins.json; every later use and every later fetch must give exactly that one back
  (a source that changed under the same tag is refused).
- Offline mode (utils/locality.offline_mode, the one rule): only a dependency already in the cache is used; one that
  is missing stops the build in plain words, naming what is missing and how to get it when online.
- The disk rule: a fetch takes a reservation in the per-user ReservationLedger (Core/disk_plan.py) for its estimated
  size and releases it when the fetch has ended.
- The person lists the cache and deletes entries only through the deletion list of Core/deletion.py (exact items, the
  person's acceptance, the trash or for good): deletion_plan(), delete().

The cache folder holds no host path of the person in any file it writes; an AI sees a dependency by its name, source,
pin and size only (facts()).
"""

import hashlib
import io
import json
import os
import re
import shutil
import stat
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from orchestrator.runtime.build.recipe import DEPENDENCY_OF_OPTION
from orchestrator.utils.helpers import hash_stream, sha256_file

CACHE_FOLDER = "build-deps"          # below the framework's cache folder
PINS_FILE = "pins.json"
MOUNT_ROOT = "/opt/llmcf-deps"       # where the build container sees them (read-only)
MAX_ARCHIVE_BYTES = 512 * 1024 ** 2
_SAFE_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")
_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class DependencyError(RuntimeError):
    """A dependency could not be fetched, checked or used; the message says why and what to do (no host path)."""


class DependencyMissing(DependencyError):
    """Offline mode and the dependency is not in the cache: nothing is downloaded."""


@dataclass(frozen=True)
class Dependency:
    """One FetchContent dependency of the pinned llama.cpp. `name`: its FetchContent name in upper case (the
    FETCHCONTENT_SOURCE_DIR_<name> variable), `option`: the llama.cpp option that needs it, `kind`: git or archive,
    `source`: where llama.cpp fetches it, `ref`: the tag or version it declares, `declared`: the CMake lines at the pin,
    `declared_hash`: "md5:<hex>" when llama.cpp checks one, `size_estimate`: bytes before the first fetch."""
    name: str
    option: str
    kind: str
    source: str
    ref: str
    declared: str
    what: str
    declared_hash: str = ""
    size_estimate: int = 0

    @property
    def mount(self) -> str:
        return f"{MOUNT_ROOT}/{self.name.lower()}"


# Read from the pinned llama.cpp (configs/llama_runtime.yml, llama_cpp.source.commit d812350...); re-checked at every
# pin bump together with NEVER_SET and DOWNLOAD_OPTIONS_OFF (CONTRIBUTING.md, "Bumping the llama.cpp pin").
# GGML_CUDA_CCCL_VERSION (its tag is the option's value, not declared by llama.cpp), GGML_ZENDNN (ExternalProject with
# its own install root), LLAMA_LLGUIDANCE (cargo), GGML_VIRTGPU (an unpinned header file) and GGML_OPENMP_FETCH
# (a Windows installer) have no pinned FetchContent source: they stay refused.
DEPENDENCIES: Mapping[str, Dependency] = {
    "BORINGSSL": Dependency(
        "BORINGSSL", "LLAMA_BUILD_BORINGSSL", "git", "https://boringssl.googlesource.com/boringssl",
        "0.20260929.0", "vendor/cpp-httplib/CMakeLists.txt:44-59 (BORINGSSL_GIT, BORINGSSL_VERSION)",
        "BoringSSL, the TLS library llama-server can be built with", size_estimate=80 * 1024 ** 2),
    "LIBRESSL": Dependency(
        "LIBRESSL", "LLAMA_BUILD_LIBRESSL", "archive",
        "https://cdn.openbsd.org/pub/OpenBSD/LibreSSL/libressl-4.3.2.tar.gz", "4.3.2",
        "vendor/cpp-httplib/CMakeLists.txt:85-102 (LIBRESSL_VERSION, URL)",
        "LibreSSL, the TLS library llama-server can be built with", size_estimate=40 * 1024 ** 2),
    "KLEIDIAI": Dependency(
        "KLEIDIAI", "GGML_CPU_KLEIDIAI", "archive",
        "https://github.com/ARM-software/kleidiai/releases/download/v1.24.0/kleidiai-v1.24.0-src.tar.gz", "v1.24.0",
        "ggml/src/ggml-cpu/CMakeLists.txt:648-662 (KLEIDIAI_COMMIT_TAG, KLEIDIAI_DOWNLOAD_URL, URL_HASH MD5)",
        "KleidiAI, Arm's optimized CPU kernels", declared_hash="md5:2f02ebe29573d45813e671eb304f2a00",
        size_estimate=20 * 1024 ** 2),
}


def needed_for(words: Sequence[str]) -> List[Dependency]:
    """The dependencies a module's CMake flags switch on (-DLLAMA_BUILD_BORINGSSL=ON, -D GGML_CPU_KLEIDIAI=ON, ...)."""
    from orchestrator.runtime.build.recipe import cmake_flag_verdicts
    wanted: List[Dependency] = []
    for v in cmake_flag_verdicts(list(words), context="module", prefetched=tuple(DEPENDENCIES)):
        name = DEPENDENCY_OF_OPTION.get(v.name)
        value = v.word.partition("=")[2].strip("\"'").upper()
        if name and value not in ("OFF", "0", "FALSE", "NO", "") and DEPENDENCIES[name] not in wanted:
            wanted.append(DEPENDENCIES[name])
    return wanted


def tree_sha256(root: Path) -> str:
    """sha256 over every file of a tree (sorted relative paths, the executable bit, each file's sha256; a link's
    target): the content llmcf records on the first fetch and checks after."""
    root = Path(root)

    def lines():
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for name in sorted(filenames):
                path = Path(dirpath) / name
                rel = path.relative_to(root).as_posix()
                if path.is_symlink():
                    yield f"L {rel} {os.readlink(path)}\n".encode("utf-8", "replace")
                    continue
                executable = "x" if os.stat(path).st_mode & stat.S_IXUSR else "-"
                yield f"F {rel} {executable} {sha256_file(path)}\n".encode("utf-8", "replace")
    return hash_stream(lines())   # the one hashing implementation (utils/helpers.py)


def tree_bytes(root: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(dirpath, name)
            if not os.path.islink(path):
                total += os.path.getsize(path)
    return total


class Fetcher:
    """How a dependency reaches this computer: git over https (source.Git: no hooks, no credentials, a cleared
    environment) and an archive over https (the framework's pinned downloader, size-capped). `allow_local`: tests
    only, a local git repository and an http address on this computer (no network)."""

    def __init__(self, session: Any = None, git: Any = None, allow_local: bool = False):
        self._session = session
        self._git = git
        self.allow_local = allow_local

    def git_clone(self, url: str, ref: str, dest: Path) -> str:
        """Clones `ref` (a tag) at depth 1 into `dest` and returns its commit."""
        from orchestrator.runtime.build.source import Git, LOCAL_TIMEOUT
        git = self._git or Git()
        local = url.startswith("file://") or "://" not in url
        if local and not self.allow_local:
            raise DependencyError(f"{url} is not an https address; a dependency comes from where llama.cpp names it")
        if not local and not url.startswith("https://"):
            raise DependencyError("a dependency is fetched over https only")
        git(["clone", "--depth", "1", "--branch", ref, "--", url, str(dest)], allow_file=local,
            timeout=3600 if not local else LOCAL_TIMEOUT)
        commit = git(["rev-parse", "HEAD"], cwd=dest).stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise DependencyError("the fetched source has no valid commit")
        return commit

    def download(self, url: str, max_bytes: int) -> bytes:
        if url.startswith("http://127.0.0.1") and self.allow_local:
            import requests
            r = (self._session or requests).get(url, timeout=30)
            if r.status_code != 200:
                raise DependencyError(f"the download failed: HTTP {r.status_code}")
            if len(r.content) > max_bytes:
                raise DependencyError("the archive is larger than the cap; refusing it")
            return r.content
        from orchestrator.utils.download import DownloadError, PinnedDownloader
        try:
            return PinnedDownloader(self._session).get_bytes(url, max_bytes, url.rsplit("/", 1)[-1])
        except DownloadError as e:
            raise DependencyError(str(e)) from None


def _extract(data: bytes, dest: Path) -> None:
    """Unpacks a .tar.gz into `dest`, without its single top-level folder; links, devices and paths outside are
    refused (the tarfile data filter)."""
    staging = dest.with_name(dest.name + ".unpack")
    staging.mkdir(parents=True)
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
            for member in tar.getmembers():
                if member.issym() or member.islnk() or member.isdev():
                    raise DependencyError(f"the archive holds a link or device ({member.name}); refusing it")
                parts = Path(member.name).parts
                if member.name.startswith(("/", "\\")) or ".." in parts:
                    raise DependencyError(f"the archive holds a path outside its folder ({member.name}); refusing it")
            if hasattr(tarfile, "data_filter"):
                tar.extractall(staging, filter="data")
            else:  # pragma: no cover - Python 3.11 before 3.11.4: the members were checked above
                tar.extractall(staging)
        entries = list(staging.iterdir())
        top = entries[0] if len(entries) == 1 and entries[0].is_dir() else staging
        os.replace(top, dest)
    except (tarfile.TarError, OSError) as e:
        raise DependencyError(f"the archive could not be unpacked ({type(e).__name__})") from None
    finally:
        shutil.rmtree(staging, ignore_errors=True)


@dataclass(frozen=True)
class Entry:
    """A dependency in the cache: its folder, size, recorded tree sha256 and when it was fetched."""
    dependency: Dependency
    path: Path
    size_bytes: int
    sha256: str
    fetched: str
    commit: str = ""


class DependencyCache:
    """The dependency cache below the framework's cache folder (<cache>/build-deps/<NAME>/<ref>/)."""

    def __init__(self, cache_dir: Path, fetcher: Optional[Fetcher] = None, ledger: Any = None,
                 disk_usage: Optional[Callable] = None):
        self.root = Path(cache_dir) / CACHE_FOLDER
        self.fetcher = fetcher or Fetcher()
        self.ledger = ledger
        self.disk_usage = disk_usage

    @classmethod
    def for_framework(cls, framework: Any) -> "DependencyCache":
        from orchestrator.Core.disk_plan import folder, state_dir
        ledger = None
        state = state_dir(framework)
        if state is not None:
            from orchestrator.Core.disk_plan import ReservationLedger
            ledger = ReservationLedger(Path(state))
        return cls(folder(framework, "cache_dir", "cache"), ledger=ledger)

    # --- pins ---
    def _pins(self) -> Dict[str, Any]:
        try:
            data = json.loads((self.root / PINS_FILE).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_pins(self, pins: Dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / f".{PINS_FILE}.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(pins, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, self.root / PINS_FILE)

    @staticmethod
    def _key(dep: Dependency) -> str:
        return f"{dep.name}@{dep.ref}"

    def folder(self, dep: Dependency) -> Path:
        if not _SAFE_NAME.fullmatch(dep.name) or not _SAFE_REF.fullmatch(dep.ref):
            raise DependencyError("a dependency's name or pin is not a plain word")
        return self.root / dep.name / dep.ref

    # --- what is here ---
    def entry(self, dep: Dependency) -> Optional[Entry]:
        path = self.folder(dep) / "src"
        pin = self._pins().get(self._key(dep))
        if not path.is_dir() or not isinstance(pin, dict):
            return None
        return Entry(dep, path, int(pin.get("size_bytes") or 0), str(pin.get("sha256") or ""),
                     str(pin.get("fetched") or ""), str(pin.get("commit") or ""))

    def entries(self) -> List[Entry]:
        return [e for e in (self.entry(d) for d in DEPENDENCIES.values()) if e is not None] + \
            [e for e in self._other_entries()]

    def _other_entries(self) -> List[Entry]:
        """Entries of other pins (left after a pin bump): listed, so the person can delete them."""
        out: List[Entry] = []
        pins = self._pins()
        for key, pin in pins.items():
            name, _, ref = key.partition("@")
            current = DEPENDENCIES.get(name)
            if current is not None and current.ref == ref or not isinstance(pin, dict):
                continue
            base = current or Dependency(name, "", "", "", ref, "", name)
            dep = Dependency(base.name, base.option, base.kind, base.source, ref, base.declared, base.what)
            try:
                path = self.folder(dep) / "src"
            except DependencyError:
                continue
            if path.is_dir():
                out.append(Entry(dep, path, int(pin.get("size_bytes") or 0), str(pin.get("sha256") or ""),
                                 str(pin.get("fetched") or "")))
        return out

    def verify(self, dep: Dependency) -> Entry:
        """The entry, after its tree was checked against the sha256 recorded on its first fetch."""
        entry = self.entry(dep)
        if entry is None:
            raise DependencyMissing(self.missing_text(dep))
        if tree_sha256(entry.path) != entry.sha256:
            raise DependencyError(f"{dep.what} in the dependency cache does not match the sha256 recorded when it was "
                                  "fetched; it is not used. Delete it in the build dependencies list and fetch it "
                                  "again.")
        return entry

    def missing_text(self, dep: Dependency) -> str:
        return (f"The module needs {dep.what} ({dep.source}, {('tag' if dep.kind == 'git' else 'version')} "
                f"{dep.ref}), which is not in the dependency cache, and offline mode is on, so nothing is downloaded. "
                "Fetch it while online: switch offline mode off and fetch it in the build dependencies window, or "
                "start this build once online; then build again.")

    # --- fetching ---
    def ensure(self, dep: Dependency, *, offline: bool, owner: str = "", progress: Optional[Callable] = None) -> Entry:
        """The checked entry of `dep`: from the cache, or fetched now (never in offline mode)."""
        if self.entry(dep) is not None:
            return self.verify(dep)
        if offline:
            raise DependencyMissing(self.missing_text(dep))
        return self.fetch(dep, owner=owner, progress=progress)

    def _reserve(self, dep: Dependency, owner: str) -> str:
        if self.ledger is None:
            return ""
        from orchestrator.Core.disk_plan import DiskNeed
        self.root.mkdir(parents=True, exist_ok=True)
        estimate = max(int(dep.size_estimate or 0), 1)
        rid, _checks = self.ledger.reserve([DiskNeed("cache", self.root, estimate, f"the build dependency {dep.name}")],
                                           owner or "build-deps", f"build dependency {dep.name} {dep.ref}",
                                           disk_usage=self.disk_usage)
        return rid

    def fetch(self, dep: Dependency, *, owner: str = "", progress: Optional[Callable] = None) -> Entry:
        """Fetches `dep` (outside every sandbox), checks it against its declared hash and the sha256 recorded on an
        earlier fetch, makes it read-only and keeps it. Raises DependencyError (nothing kept) otherwise."""
        target = self.folder(dep)
        self.root.mkdir(parents=True, exist_ok=True)
        rid = self._reserve(dep, owner)
        work = Path(tempfile.mkdtemp(prefix=f".{dep.name.lower()}-", dir=str(self.root)))
        try:
            src = work / "src"
            commit, archive_sha = "", ""
            if progress:
                progress(f"Fetching {dep.what} ({dep.source}, {dep.ref}) before the build...")
            if dep.kind == "git":
                commit = self.fetcher.git_clone(dep.source, dep.ref, src)
                shutil.rmtree(src / ".git", ignore_errors=True)
            elif dep.kind == "archive":
                data = self.fetcher.download(dep.source, MAX_ARCHIVE_BYTES)
                algo, _, want = dep.declared_hash.partition(":")
                if algo and hashlib.new(algo, data).hexdigest() != want.lower():
                    raise DependencyError(f"{dep.what} does not have the {algo.upper()} the pinned llama.cpp declares; "
                                          "it was not kept.")
                archive_sha = hashlib.sha256(data).hexdigest()
                _extract(data, src)
            else:
                raise DependencyError(f"unknown dependency kind {dep.kind!r}")
            digest = tree_sha256(src)
            pins = self._pins()
            known = pins.get(self._key(dep))
            if isinstance(known, dict) and known.get("sha256") and known["sha256"] != digest:
                raise DependencyError(f"{dep.what} ({dep.ref}) is not what was fetched before: its sha256 differs from "
                                      "the one recorded on the first fetch, so the source changed under the same pin. "
                                      "It was not kept.")
            from orchestrator.runtime.build.source import _make_read_only, remove_tree
            if target.exists():
                remove_tree(target)
            target.mkdir(parents=True)
            os.replace(src, target / "src")
            _make_read_only(target / "src")
            pins[self._key(dep)] = {"source": dep.source, "ref": dep.ref, "sha256": digest, "commit": commit,
                                    "archive_sha256": archive_sha, "size_bytes": tree_bytes(target / "src"),
                                    "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            self._save_pins(pins)
            if rid and self.ledger is not None:
                self.ledger.release(rid)
                rid = ""
            return self.entry(dep)
        finally:
            shutil.rmtree(work, ignore_errors=True)
            if rid and self.ledger is not None:
                self.ledger.release(rid)

    # --- for the build and the question ---
    def mounts(self, entries: Sequence[Entry]) -> Dict[str, Dict[str, str]]:
        """docker-py volumes: each entry read-only at its mount point."""
        return {str(e.path): {"bind": e.dependency.mount, "mode": "ro"} for e in entries}

    @staticmethod
    def environment(entries: Sequence[Entry]) -> Dict[str, str]:
        return {"LLMCF_DEPS": " ".join(f"{e.dependency.name}={e.dependency.mount}" for e in entries)} if entries \
            else {}

    def facts(self, dep: Dependency) -> str:
        """One line for the question and the AI's facts: what, source, pin, size (no path)."""
        entry = self.entry(dep)
        if entry is not None:
            return (f"{dep.what}: {dep.source}, {dep.ref}, in the dependency cache ({entry.size_bytes} bytes, sha256 "
                    f"{entry.sha256[:16]}...)")
        return (f"{dep.what}: fetched before the build from {dep.source}, {dep.ref}, about "
                f"{dep.size_estimate // 1024 ** 2} MB (estimate)")

    # --- deletion (Core/deletion.py) ---
    def deletion_plan(self):
        from orchestrator.Core.deletion import plan_of
        from orchestrator.tools.spec import DeletionItem
        items = [DeletionItem(kind="build_dependency", speaking_name=f"build dependency {e.dependency.name} "
                                                                       f"{e.dependency.ref}",
                              full_path=str(e.path.parent), file_name=e.path.parent.name, size_bytes=e.size_bytes,
                              what=f"{e.dependency.what}, fetched {e.fetched[:10] or 'earlier'} from "
                                   f"{e.dependency.source or 'its source'}"[:300])
                 for e in self.entries()]
        return plan_of(items, ("trash", "permanent"))

    def delete(self, accepted: Any, trash: Any = None, audit: Any = None):
        """Deletes exactly the entries the person accepted (Core/deletion.py): into the trash or for good. Only the
        person; the list is checked again first; the deletion is audited before anything goes."""
        from orchestrator.Core.deletion import DeletionResult, require_person, require_same, same_items
        from orchestrator.runtime.build.source import remove_tree
        accepted = require_person(accepted)
        if accepted.actor not in ("gui", "cli"):
            raise PermissionError("Only the person deletes build dependencies, in the GUI or at a terminal.")
        require_same(accepted, same_items(self.deletion_plan(), accepted.plan), "the list of build dependencies")
        if accepted.mode == "trash" and trash is None:
            raise RuntimeError("The trash is not available; nothing was deleted.")
        if audit is None or not callable(getattr(audit, "record", None)):
            raise RuntimeError("The audit log is not available, so nothing was deleted.")
        names = [i.speaking_name for i in accepted.plan.items]
        try:
            audit.record(accepted.actor, "build_deps", "framework", "person", "started", approved=True,
                         arguments={"dependencies": names, "bytes": accepted.plan.total_bytes, "mode": accepted.mode},
                         detail=f"{len(names)} build dependencies "
                                + ("moved to the trash" if accepted.mode == "trash" else "deleted for good"),
                         extra=accepted.audit_extra())
        except Exception:  # noqa: BLE001 - the reason could name a host path
            raise RuntimeError("The audit log cannot record the deletion; nothing was deleted.") from None
        result = DeletionResult()
        pins = self._pins()
        for item in accepted.plan.items:
            path = Path(item.full_path)
            try:
                if accepted.mode == "trash":
                    for dirpath, _dirs, _files in os.walk(path):   # the trash moves it: writable again
                        os.chmod(dirpath, stat.S_IRWXU)
                    entry = trash.move("build_dependency", path, self.root, "cache", item.speaking_name, accepted,
                                       accepted.actor)
                    result.trash_ids.append(entry.id)
                else:
                    remove_tree(path)
                    result.freed_bytes += item.size_bytes
            except Exception as e:  # noqa: BLE001 - kept and reported
                result.failed.append((item.speaking_name, type(e).__name__ if isinstance(e, OSError) else str(e)))
                continue
            result.done.append(item.speaking_name)
        # the recorded sha256 stays (a later fetch of the same pin must give it back); only the folder goes
        self._save_pins(pins)
        return result


__all__ = ["CACHE_FOLDER", "DEPENDENCIES", "Dependency", "DependencyCache", "DependencyError", "DependencyMissing",
           "Entry", "Fetcher", "MOUNT_ROOT", "needed_for", "tree_sha256"]
