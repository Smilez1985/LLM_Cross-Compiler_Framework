#!/usr/bin/env python3
"""
The store of self-built (and imported) llama.cpp builds (design 3.7, 3.9).

    <state dir>/runtime/llama.cpp/
      src/<commit>/                  the verified source (source.py)
      builds/<build_id[:16]>/
        bin/                         the programs (and shared libraries of multi-variant builds)
        build.json                   inputs: the plan, the profile's perf_key, purpose, commit
        manifest.json                outputs: sha256 per file, duration, build host, time, verification results
        logs/                        configure and build logs (never exported: they hold local paths)
      builds/.staging-<id16>-<rand>/ a build in progress
      logs/failed-<id16>-<time>/     the logs of a build that failed
      ccache/                        compiler cache of native builds that use ccache
      active.json                    shared with llama_server.py: build_id and fallback_build_id (the CPU build)

A build ID that is already stored is reused and never stamped again. Only a build whose verification passed on this
machine can be activated, and retention never deletes the active build, the CPU fallback, a build another caller
references, or the build a running server was started from.

Imported builds (built on another machine of the user) come as a tar.gz of build.json, manifest.json and bin/. They
carry no signature, so the import extracts them safely, checks every file against the manifest, the build ID against
the hash of the plan, the plan against the download and subprocess rules and the commit against the pin, asks the
user to confirm before any of their programs runs, and then verifies them like a local build, with the version
expected from this framework's pin (not from the archive) and every program in bin/ checked.
"""

import json
import os
import re
import secrets
import shutil
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Set, Tuple, Union

from orchestrator import __version__
from orchestrator.runtime.build.executor import BuildError, BuildExecutor, BuildResult
from orchestrator.runtime.build.recipe import BuildPlan, PlanError, build_id_of, check_download_options
from orchestrator.runtime.build.source import remove_tree
from orchestrator.runtime.build.toolchain import DetectedToolchain
from orchestrator.runtime.build.verify import SmokeModelFile, Verifier, VerifyReport, binary_path, sha256_file
from orchestrator.runtime.catalog import LlamaCppSource
from orchestrator.utils.download import FileLock
from orchestrator.utils.helpers import safe_extract_archive

STORE_FORMAT = 1
MAX_STAMP_BYTES = 1024 * 1024
MAX_IMPORT_BYTES = 4 * 1024 ** 3   # packed and unpacked
MAX_IMPORT_MEMBERS = 1024
DEFAULT_KEEP = 2

_RE_ID = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_RE_ID16 = re.compile(r"^[0-9a-f]{16}$", re.ASCII)
_RE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$", re.ASCII)


class StoreError(RuntimeError):
    """A build cannot be stored, found, imported or activated; the message says why."""


class BuildFailed(StoreError):
    """The build ran but failed its verification (report) or did not complete (log)."""

    def __init__(self, message: str, report: Optional[VerifyReport] = None, log: Optional[Path] = None):
        super().__init__(message)
        self.report = report
        self.log = log


class _StoreLock(FileLock):
    error = StoreError
    busy = "Another llama.cpp build or runtime change is running ({path}); try again when it has finished."


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_stamp(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "rb") as f:
            raw = f.read(MAX_STAMP_BYTES + 1)
    except OSError:
        return None
    if len(raw) > MAX_STAMP_BYTES:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) and data.get("format") == STORE_FORMAT else None


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


@dataclass(frozen=True)
class StoredBuild:
    path: Path
    build: Mapping[str, Any]
    manifest: Mapping[str, Any]

    @property
    def build_id(self) -> str:
        return str(self.build["build_id"])

    @property
    def bin_dir(self) -> Path:
        return self.path / "bin"

    @property
    def plan(self) -> Mapping[str, Any]:
        return self.build.get("plan") or {}

    @property
    def commit(self) -> str:
        return str((self.plan.get("source") or {}).get("commit", ""))

    @property
    def backend(self) -> str:
        return str(self.plan.get("backend", ""))

    @property
    def purpose(self) -> str:
        return str(self.plan.get("purpose", ""))

    @property
    def origin(self) -> str:
        return str(self.manifest.get("origin", "self-built"))

    @property
    def built_at(self) -> str:
        return str(self.manifest.get("built_at", ""))

    @property
    def verified(self) -> bool:
        """The verification passed and ran the programs on this machine."""
        report = self.manifest.get("verification") or {}
        return bool(report.get("ok")) and bool(report.get("ran_binaries"))

    def binary(self, name: str) -> Path:
        return binary_path(self.bin_dir, name)


class BuildStore:
    def __init__(self, state_dir: Union[str, Path]):
        self.root = Path(state_dir) / "runtime" / "llama.cpp"
        self.builds_dir = self.root / "builds"
        self.logs_dir = self.root / "logs"
        self.active_file = self.root / "active.json"

    # --- reading ---
    def path_for(self, build_id: str) -> Path:
        if not (_RE_ID.fullmatch(build_id or "") or _RE_ID16.fullmatch(build_id or "")):
            raise StoreError("a build ID is 64 (or its first 16) lower-case hex characters")
        return self.builds_dir / build_id[:16]

    def get(self, build_id: str) -> Optional[StoredBuild]:
        """The stored build with this ID (or 16-character prefix) when its stamps are complete, else None."""
        path = self.path_for(build_id)
        build = _read_stamp(path / "build.json")
        manifest = _read_stamp(path / "manifest.json")
        if build is None or manifest is None:
            return None
        bid = build.get("build_id")
        if not isinstance(bid, str) or not _RE_ID.fullmatch(bid) or bid[:16] != path.name \
                or manifest.get("build_id") != bid or not bid.startswith(build_id):
            return None
        if not isinstance(manifest.get("files"), dict):
            return None
        return StoredBuild(path=path, build=build, manifest=manifest)

    def list_builds(self) -> List[StoredBuild]:
        if not self.builds_dir.is_dir():
            return []
        found = [self.get(p.name) for p in self.builds_dir.iterdir() if _RE_ID16.fullmatch(p.name) and p.is_dir()]
        return sorted((b for b in found if b is not None), key=lambda b: b.built_at, reverse=True)

    def check_files(self, build: StoredBuild) -> List[str]:
        """What does not match the manifest: changed, missing or added files and links in bin/."""
        problems: List[str] = []
        files: Mapping[str, str] = build.manifest.get("files") or {}
        links: Mapping[str, str] = build.manifest.get("links") or {}
        present = {p.name: p for p in build.bin_dir.iterdir()} if build.bin_dir.is_dir() else {}
        for name, digest in files.items():
            path = present.get(name)
            if path is None or path.is_symlink() or not path.is_file():
                problems.append(f"{name} is missing")
            elif sha256_file(path) != digest:
                problems.append(f"{name} was changed")
        for name, target in links.items():
            path = present.get(name)
            if path is None or not path.is_symlink() or os.readlink(path) != target:
                problems.append(f"the link {name} is missing or changed")
        for name in sorted(set(present) - set(files) - set(links)):
            problems.append(f"{name} is not part of the build")
        return problems

    def find(self, plan: BuildPlan) -> Optional[StoredBuild]:
        """The stored build of this plan when it is intact (a build is reused, never built or stamped again)."""
        build = self.get(plan.build_id)
        if build is None or build.build_id != plan.build_id or self.check_files(build):
            return None
        return build

    # --- building ---
    def _lock(self, name: str = ".build.lock", timeout: float = 5) -> _StoreLock:
        return _StoreLock(self.root / name, timeout=timeout)

    def _keep_failed_logs(self, staging: Path, build_id: str) -> Optional[Path]:
        logs = staging / "logs"
        if not logs.is_dir():
            return None
        dest = self.logs_dir / f"failed-{build_id[:16]}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copytree(logs, dest, symlinks=True)  # a link is copied as a link, never followed
        except OSError:
            return None
        return dest

    def build(self, plan: BuildPlan, *, source: Path, executor: BuildExecutor, verifier: Verifier,
              jobs: Optional[int] = None,
              toolchain: Optional[DetectedToolchain] = None, smoke_model: Optional[SmokeModelFile] = None,
              perf_key: Optional[str] = None, host: Optional[Tuple[str, str]] = None) -> StoredBuild:
        """Builds the plan (or returns the stored build of the same ID), verifies it and stores it. A build that fails
        is not stored; its logs are kept in logs/failed-<id>-<time>/."""
        existing = self.find(plan)
        if existing is not None:
            return existing
        self.builds_dir.mkdir(parents=True, exist_ok=True)
        with self._lock():
            existing = self.find(plan)
            if existing is not None:
                return existing
            staging = self.builds_dir / f".staging-{plan.build_id[:16]}-{secrets.token_hex(4)}"
            try:
                try:
                    result = executor.build(plan, source=source, work=staging, jobs=jobs, toolchain=toolchain,
                                            ccache_dir=self.root / "ccache")
                except BuildError as e:
                    kept = self._keep_failed_logs(staging, plan.build_id)
                    raise BuildFailed(str(e), log=kept) from None
                host_arch = host[1] if host else (toolchain.info.host_arch if toolchain else None)
                report = verifier.verify(plan, result.bin_dir, smoke_model=smoke_model, host_arch=host_arch)
                if not report.ok:
                    kept = self._keep_failed_logs(staging, plan.build_id)
                    raise BuildFailed(f"the build failed its verification: {report.summary()}", report=report,
                                      log=kept)
                return self._commit(plan, result, report, staging, perf_key=perf_key, host=host or (
                    (toolchain.info.host_os, toolchain.info.host_arch) if toolchain else None))
            finally:
                if staging.exists():
                    remove_tree(staging)

    def _commit(self, plan: BuildPlan, result: BuildResult, report: VerifyReport, staging: Path, *,
                perf_key: Optional[str], host: Optional[Tuple[str, str]]) -> StoredBuild:
        for scratch in ("build", "home", "tmp", "config"):
            remove_tree(staging / scratch)  # never follows a link the build left there
        build = {"format": STORE_FORMAT, "build_id": plan.build_id, "plan": plan.as_dict(), "perf_key": perf_key,
                 "framework_version": __version__}
        manifest = {"format": STORE_FORMAT, "build_id": plan.build_id, "origin": "self-built",
                    "files": dict(result.files), "links": dict(result.links), "duration_s": result.duration_s,
                    "jobs": result.jobs, "placement": result.placement, "builder_image": result.builder_image,
                    "build_host": {"os": host[0], "arch": host[1]} if host else None, "built_at": _now(),
                    "verification": report.as_dict(), "verified_at": _now()}
        _write_json(staging / "build.json", build)
        _write_json(staging / "manifest.json", manifest)
        target = self.path_for(plan.build_id)
        if target.exists():
            remove_tree(target)  # incomplete or damaged (find() said so)
        os.replace(staging, target)
        stored = self.get(plan.build_id)
        if stored is None:  # pragma: no cover - just written
            raise StoreError(f"the build in {target} could not be read back")
        return stored

    # --- active build and references ---
    def _read_active(self) -> Dict[str, Any]:
        try:
            data = json.loads(self.active_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def references(self) -> Dict[str, str]:
        """{role: build_id} of active.json (active, fallback)."""
        data = self._read_active()
        refs = {}
        for role, key in (("active", "build_id"), ("fallback", "fallback_build_id")):
            value = data.get(key)
            if isinstance(value, str) and _RE_ID.fullmatch(value):
                refs[role] = value
        return refs

    def activate(self, build_id: str, *, role: str = "active") -> StoredBuild:
        """Makes a verified build the active one (or the CPU fallback). Unverified, changed or damaged builds are
        refused."""
        if role not in ("active", "fallback"):
            raise StoreError("role must be active or fallback")
        build = self.get(build_id)
        if build is None:
            raise StoreError(f"there is no stored build {build_id[:16]}")
        if not build.verified:
            raise StoreError(f"build {build.build_id[:16]} has not passed its verification on this machine")
        problems = self.check_files(build)
        if problems:
            raise StoreError(f"build {build.build_id[:16]} does not match its manifest: {'; '.join(problems[:3])}")
        from orchestrator.runtime.llama_server import _write_private
        with self._lock(".lock", timeout=60):
            data = self._read_active()
            data["build_id" if role == "active" else "fallback_build_id"] = build.build_id
            _write_private(self.active_file, json.dumps(data))
        return build

    def running_build_ids(self) -> Set[str]:
        """IDs of stored builds the managed server runs from (pid, start time and executable checked)."""
        from orchestrator.runtime.llama_server import LocalLLMRuntime, _read_json, owned_process
        proc = owned_process(_read_json(self.root / "server.json"))
        if proc is None:
            return set()
        return {b.build_id for b in self.list_builds() if LocalLLMRuntime._uses(proc, b.path)}

    def prune(self, keep_last: int = DEFAULT_KEEP, keep: Iterable[str] = ()) -> List[str]:
        """Removes old builds: keeps the active build, the CPU fallback, the builds in `keep`, the build a running
        server uses, and the `keep_last` newest others. Also removes leftovers of interrupted builds. Returns the
        removed IDs."""
        removed: List[str] = []
        with self._lock(".lock", timeout=60):
            protected = set(self.references().values()) | set(keep) | self.running_build_ids()
            others = [b for b in self.list_builds() if b.build_id not in protected]
            for build in others[max(0, int(keep_last)):]:
                remove_tree(build.path)
                removed.append(build.build_id)
        try:
            with self._lock(timeout=0):  # no build is running: its staging folders are leftovers
                for leftover in self.builds_dir.glob(".staging-*"):
                    remove_tree(leftover)
        except StoreError:
            pass
        return removed

    # --- moving builds between machines ---
    def export_build(self, build_id: str, out: Path) -> Path:
        """Writes a tar.gz of build.json, manifest.json and bin/ (no logs: they hold local paths)."""
        build = self.get(build_id)
        if build is None:
            raise StoreError(f"there is no stored build {build_id[:16]}")
        problems = self.check_files(build)
        if problems:
            raise StoreError(f"build {build.build_id[:16]} does not match its manifest: {'; '.join(problems[:3])}")
        out = Path(out).absolute()
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(f".{out.name}.{secrets.token_hex(4)}.tmp")
        try:
            with tarfile.open(tmp, "w:gz") as tar:
                for name in ("build.json", "manifest.json"):
                    tar.add(build.path / name, arcname=name, recursive=False)
                tar.add(build.bin_dir, arcname="bin", recursive=False)
                for item in sorted(build.bin_dir.iterdir()):
                    tar.add(item, arcname=f"bin/{item.name}", recursive=False)
            os.replace(tmp, out)
        finally:
            tmp.unlink(missing_ok=True)
        return out

    def import_build(self, archive: Path, *, pin: LlamaCppSource, verifier: Verifier,
                     confirm: Callable[[Mapping[str, Any]], bool], host: Tuple[str, str],
                     smoke_model: Optional[SmokeModelFile] = None) -> StoredBuild:
        """Imports a build made on another machine (design 3.9). confirm() gets what the user is asked about (origin,
        commit, build host, files) and must return True before any program of the build runs."""
        archive = Path(archive)
        try:
            size = archive.stat().st_size
        except OSError as e:
            raise StoreError(f"cannot read {archive.name}: {e.strerror or e}") from None
        if size == 0 or size > MAX_IMPORT_BYTES or not archive.name.endswith((".tar.gz", ".tgz")):
            raise StoreError(f"{archive.name} is not a build archive (a .tar.gz of at most 4 GiB)")
        _check_unpacked_size(archive)
        self.builds_dir.mkdir(parents=True, exist_ok=True)
        with self._lock():
            staging = self.builds_dir / f".staging-import-{secrets.token_hex(4)}"
            try:
                staging.mkdir()
                try:
                    safe_extract_archive(archive, staging)
                except Exception as e:
                    raise StoreError(f"{archive.name} is unsafe or damaged: {e}") from None
                build, manifest = self._check_import(staging, pin, host)
                existing = self.get(build["build_id"])
                if existing is not None and not self.check_files(existing):
                    return existing
                info = {"origin": "imported", "build_id": build["build_id"], "commit": pin.commit, "tag": pin.tag,
                        "built_on": manifest.get("build_host"), "built_at": manifest.get("built_at"),
                        "built_by_origin": manifest.get("origin"), "files": sorted(manifest["files"])}
                if not confirm(info):
                    raise StoreError("the import was not confirmed; nothing was run or stored")
                report = verifier.verify(_import_checks(build["plan"], pin), staging / "bin", run_binaries=True,
                                         smoke_model=smoke_model, host_arch=host[1])
                if not report.ok:
                    raise StoreError(f"the imported build failed its verification: {report.summary()}")
                manifest = dict(manifest)
                manifest.update({"origin": "imported", "verification": report.as_dict(), "verified_at": _now(),
                                 "imported": {"at": _now(), "built_on": manifest.get("build_host"),
                                              "built_by_origin": manifest.get("origin"), "confirmed": True}})
                _write_json(staging / "manifest.json", manifest)
                target = self.path_for(build["build_id"])
                if target.exists():
                    remove_tree(target)
                os.replace(staging, target)
                stored = self.get(build["build_id"])
                if stored is None:  # pragma: no cover - just written
                    raise StoreError(f"the build in {target} could not be read back")
                return stored
            finally:
                if staging.exists():
                    remove_tree(staging)

    @staticmethod
    def _check_import(staging: Path, pin: LlamaCppSource,
                      host: Tuple[str, str]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        build = _read_stamp(staging / "build.json")
        manifest = _read_stamp(staging / "manifest.json")
        if build is None or manifest is None:
            raise StoreError("the archive has no valid build.json and manifest.json of this framework")
        bid = build.get("build_id")
        if not isinstance(bid, str) or not _RE_ID.fullmatch(bid) or manifest.get("build_id") != bid:
            raise StoreError("the build ID of the archive is missing or inconsistent")
        plan = build.get("plan")
        if not isinstance(plan, dict):
            raise StoreError("build.json has no plan")
        try:
            parsed = BuildPlan.from_dict(plan)
        except PlanError as e:
            raise StoreError(f"build.json has no valid plan: {e}") from None
        source = plan.get("source") or {}
        if source.get("commit") != pin.commit:
            raise StoreError(f"the build is of commit {str(source.get('commit'))[:12]}, but this framework is pinned "
                             f"to {pin.commit[:12]}")
        target = plan.get("target") or {}
        if (target.get("os"), target.get("arch")) != tuple(host):
            raise StoreError(f"the build is for {target.get('os')}/{target.get('arch')}, this machine is "
                             f"{host[0]}/{host[1]}")
        # The archive is unsigned, so what it says about itself is checked, not believed: the build ID must be the
        # hash of its plan (find() hands a stored build out by that ID), and the plan must be one this framework
        # makes (no download option, no subprocess support).
        if build_id_of(parsed.id_inputs()) != bid:
            raise StoreError("the build ID of the archive is not the hash of its plan; the archive was changed")
        try:
            for index in range(len(parsed.steps)):
                check_download_options(parsed.options(index))
        except PlanError as e:
            raise StoreError(f"the plan of the build is not one this framework makes: {e}") from None
        files = manifest.get("files")
        links = manifest.get("links") or {}
        if not isinstance(files, dict) or not files or not isinstance(links, dict):
            raise StoreError("the manifest lists no files")
        allowed = {"build.json", "manifest.json", "bin"}
        top = {p.name for p in staging.iterdir()}
        if top - allowed:
            raise StoreError(f"the archive holds more than a build: {', '.join(sorted(top - allowed))}")
        bin_dir = staging / "bin"
        present = {p.name: p for p in bin_dir.iterdir()} if bin_dir.is_dir() else {}
        for name in list(files) + list(links):
            if not isinstance(name, str) or not _RE_FILE.fullmatch(name):
                raise StoreError(f"the manifest names an unsafe file {str(name)[:40]!r}")
        if set(present) != set(files) | set(links):
            raise StoreError("the files in bin/ are not exactly those of the manifest")
        for name, digest in files.items():
            path = present[name]
            if path.is_symlink() or not path.is_file() or not isinstance(digest, str) or sha256_file(path) != digest:
                raise StoreError(f"{name} does not match the manifest (sha256)")
        for name, target_name in links.items():
            path = present[name]
            if not path.is_symlink() or os.readlink(path) != target_name or target_name not in files:
                raise StoreError(f"the link {name} does not match the manifest")
        for name in plan.get("verify", {}).get("binaries") or []:
            if name not in files:
                raise StoreError(f"the build lacks {name}")
        return build, manifest


def _check_unpacked_size(archive: Path) -> None:
    """A build archive is unsigned data: what it unpacks to is limited before anything is extracted (a small archive
    of zeros could otherwise fill the disk)."""
    total = 0
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for count, member in enumerate(tar, start=1):
                total += max(0, member.size)
                if count > MAX_IMPORT_MEMBERS or total > MAX_IMPORT_BYTES:
                    raise StoreError(f"{archive.name} would unpack to more than a build (at most "
                                     f"{MAX_IMPORT_MEMBERS} files and {MAX_IMPORT_BYTES // 1024 ** 2} MiB)")
    except (tarfile.TarError, OSError, EOFError) as e:
        raise StoreError(f"{archive.name} is unsafe or damaged: {e}") from None


def _import_checks(plan: Mapping[str, Any], pin: LlamaCppSource) -> Dict[str, Any]:
    """The plan an imported build is verified with: the version it must report comes from this framework's pin, not
    from the archive (the verifier also checks every program the folder holds, not only the ones the archive lists)."""
    checked = dict(plan)
    checked["verify"] = {**(plan.get("verify") or {}),
                         "version": {"build_number": pin.build_number, "commit": pin.short_commit}}
    return checked


def smoke_model_file(catalog_model: Any, models_dir: Path) -> Optional[SmokeModelFile]:
    """The downloaded smoke-test model of the catalog when it is there and matches its size (sha256 is checked by
    the verification); None when none is pinned or it is not downloaded."""
    if catalog_model is None:
        return None
    path = Path(models_dir) / catalog_model.file
    try:
        if path.stat().st_size != catalog_model.size:
            return None
    except OSError:
        return None
    return SmokeModelFile(path=path, sha256=catalog_model.sha256)


def describe(build: StoredBuild) -> str:
    """One line for status output: origin, ID, backend, commit, verification."""
    state = "verified" if build.verified else "not verified on this machine"
    return (f"{build.origin} build {build.build_id[:16]} ({build.backend}, {build.purpose}, commit "
            f"{build.commit[:8]}), {state}")


__all__ = ["BuildFailed", "BuildStore", "StoreError", "StoredBuild", "describe", "smoke_model_file"]
