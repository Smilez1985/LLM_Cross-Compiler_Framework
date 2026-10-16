#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Where golden artifacts go (DECISIONS, eighth round, answer 3)

A golden artifact is a finished build: its folder (the model files, build_info.json, the model card) and the ZIP next
to it. Two folders hold them:

- The output folder (setting `output_dir`, default `~/llmcf/output`: on Windows the user's own folder, e.g.
  C:\\Users\\<name>\\llmcf\\output). The person sets it once (File > Output Folder... in the GUI, `llm-cli config
  output-folder PATH`), for example to a folder on a big second disk. Only the person changes it; an AI cannot.
- The framework's build folder (setting `build_dir`, default the data folder's `output` folder, where builds went
  before this version): a build the person started in the GUI lands here; earlier builds stay here and stay listed.

Where a build goes:
- GUI: when a build the person started has finished, a native folder dialog opens at the output folder for every
  golden artifact. OK saves it there, or the person picks another folder. Cancel saves nothing elsewhere and deletes
  nothing: the artifact stays in the build folder, and "Save..." in the Deployment tab saves it later.
- CLI: `llm-cli build start --output PATH`; at a terminal without it, a prompt with the output folder as its default
  ('-' keeps it in the build folder, `llm-cli build save` saves it later); without a terminal the output folder.
- AI callers (MCP, Ditto): always the output folder, and the result names it by label ("output:<relative path>").

Saving (`save_artifact`) moves an artifact out of the build folder: on the same disk it is renamed (nothing is
copied); onto another disk every file is copied, each copy is checked against its original by sha256 (and a model
file against the hash its manifest lists), and only then is the build folder's copy removed. A link is never followed
and not copied; when anything was left out, the result names it and the build folder's copy stays. Before copying,
the free space of the destination is checked (with the reserve that always stays free). A failed check leaves the
artifact where it was and removes only the unfinished copy. Nothing is ever overwritten: a name that is taken gets
"-2", "-3".

A destination is refused when it is (or lies in) a system folder, the framework's program folder, its build folder or
its per-user folder. Artifacts saved outside the output folder are remembered (`saved_artifacts.json` in the per-user
folder) so the listings keep showing them.
"""

import errno
import hashlib
import json
import os
import secrets
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from orchestrator.utils.file_lock import file_lock

BUILDS, OUTPUT, SAVED = "builds", "output", "saved"
INDEX_FILE = "saved_artifacts.json"
DEFAULT_OUTPUT = "~/llmcf/output"
MANIFEST = "build_info.json"
COPY_CHUNK = 1 << 20
MAX_INDEX = 2000           # saved artifacts remembered outside the output folder
WHERE_TEXT = {BUILDS: "in the framework's build folder (not saved yet)", OUTPUT: "in the output folder",
              SAVED: "saved to another folder"}

# Folders a golden artifact never goes to: the folder itself and everything below it ...
_POSIX_TREES = ("/bin", "/boot", "/dev", "/etc", "/lib", "/lib32", "/lib64", "/libx32", "/proc", "/run", "/sbin",
                "/sys", "/usr", "/var/lib", "/var/log", "/var/cache", "/var/spool", "/var/run", "/System", "/Library",
                "/Applications", "/private/etc", "/private/var/db")
# ... and folders that are refused themselves (a folder below them is fine, e.g. /mnt/data or /home/<name>/models).
# The system's temporary folder is only compared with, never written to.
_SYSTEM_TMP = "/tmp"  # nosec B108
_POSIX_EXACT = ("/", "/home", "/Users", "/mnt", "/media", "/opt", "/srv", "/var", "/root", "/private", _SYSTEM_TMP)


class DestinationRefused(ValueError):
    """A folder a golden artifact may not go to, or a save that cannot be done; `next` says what to do."""

    def __init__(self, message: str, next: str = "", code: str = "invalid_value"):
        super().__init__(message)
        self.next, self.code = next, code


# --- the folders --------------------------------------------------------------------------------------------------

def _config_get(framework, key: str, default=None):
    config = getattr(framework, "config", None)
    get = getattr(config, "get", None)
    try:
        return get(key, default) if callable(get) else getattr(config, key, default)
    except Exception:  # noqa: BLE001 - a setting that cannot be read is its default
        return default


def _resolve(framework, value) -> Path:
    resolve = getattr(framework, "resolve_path", None)
    return Path(resolve(value)) if callable(resolve) else Path(str(value)).expanduser()


def output_folder(framework) -> Path:
    """The person's output folder (setting output_dir)."""
    return _resolve(framework, _config_get(framework, "output_dir", None) or DEFAULT_OUTPUT)


def build_folder(framework) -> Path:
    """The framework's build folder (setting build_dir; a stand-in configuration without it uses the output folder,
    so both are the same folder there)."""
    value = _config_get(framework, "build_dir", None) or _config_get(framework, "output_dir", None) or "output"
    return _resolve(framework, value)


def _state_dir(framework) -> Optional[Path]:
    try:
        value = getattr(getattr(framework, "config", None), "user_state_dir", None)
    except Exception:  # noqa: BLE001
        value = None
    return Path(value) if isinstance(value, (str, Path)) else None


def _program_folders(framework) -> List[Path]:
    """The framework's program folder: the folder that holds its code, and the installation path when it holds the
    code too."""
    folders = [Path(__file__).resolve().parents[2]]
    installation = getattr(getattr(framework, "info", None), "installation_path", None)
    if installation and (Path(installation) / "orchestrator").is_dir():
        folders.append(Path(installation))
    return folders


def _real(path: Path) -> Path:
    """The path with every existing part resolved (links followed), the missing tail kept."""
    path = Path(path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    existing, tail = path, []
    while not existing.exists() and existing.parent != existing:
        tail.append(existing.name)
        existing = existing.parent
    real = existing.resolve()
    for name in reversed(tail):  # parts that do not exist cannot be links: ".." is taken as written
        real = real.parent if name == ".." else real if name in ("", ".") else real / name
    return real


def _inside(path: Path, folder: Path) -> bool:
    return path == folder or folder in path.parents


def _windows_refusals() -> Tuple[List[Path], List[Path]]:
    trees, exact = [], []
    for var in ("SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData"):
        value = os.environ.get(var)
        if value:
            trees.append(Path(value))
    drive = os.environ.get("SystemDrive") or "C:"
    exact.append(Path(drive + "\\"))
    trees += [Path(drive + "\\") / "$Recycle.Bin", Path(drive + "\\") / "System Volume Information"]
    return trees, exact


def refusal(framework, destination: Path) -> str:
    """Why `destination` may not hold golden artifacts ("" when it may): a system folder, the framework's program
    folder, its build folder or its per-user folder."""
    real = _real(destination)
    if os.name == "nt":
        trees, exact = _windows_refusals()
    else:
        trees, exact = [Path(p) for p in _POSIX_TREES], [Path(p) for p in _POSIX_EXACT]
    for folder in trees:
        if _inside(real, _real(folder)):
            return f"{real} is a system folder or lies inside one ({folder})"
    for folder in exact:
        if real == _real(folder):
            return f"{real} is a system folder; choose a folder inside it"
    home = _real(Path.home())
    if real == home.parent:
        return f"{real} holds the home folders of every user; choose a folder inside your own"
    for program in _program_folders(framework):
        if _inside(real, _real(program)):
            return f"{real} lies inside the framework's program folder; keep programs and models apart"
    if _inside(real, _real(build_folder(framework))):
        return f"{real} lies inside the framework's build folder, where builds wait to be saved"
    state = _state_dir(framework)
    if state is not None and _inside(real, _real(state)):
        return f"{real} lies inside the framework's own per-user folder"
    from orchestrator.Core.trash import TRASH_FOLDER
    if TRASH_FOLDER in real.parts:
        return f"{real} lies inside a trash folder of the framework"
    return ""


def check_destination(framework, destination: Any, create: bool = False) -> Path:
    """The folder `destination` (a relative one is relative to the working directory) resolved, after the refusals of
    `refusal`; DestinationRefused otherwise. `create`: make the folder when it does not exist yet (the CLI's
    --output); it must be writable."""
    if destination is None or not str(destination).strip():
        raise DestinationRefused("No folder was given.", "Choose a folder for the finished model.")
    path = _real(Path(str(destination).strip()))
    why = refusal(framework, path)
    if why:
        raise DestinationRefused(f"Not saved there: {why}.", "Choose another folder (for example the output folder "
                                                             "or a folder on another disk).")
    if path.exists() and not path.is_dir():
        raise DestinationRefused(f"{path} is a file, not a folder.", "Choose a folder.")
    if not path.exists():
        if not create:
            raise DestinationRefused(f"{path} does not exist.", "Choose an existing folder.")
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise DestinationRefused(f"{path} cannot be created ({e.strerror or e}).", "Choose another folder.") \
                from None
    try:
        with tempfile.TemporaryFile(dir=path):
            pass
    except OSError:
        raise DestinationRefused(f"You cannot write to {path}.", "Choose a folder you can write to.") from None
    return path


# --- what is where ------------------------------------------------------------------------------------------------

class SavedIndex:
    """The golden artifacts saved outside the output folder (folder and ZIP), and the earlier output folders the person
    changed away from, so the listings keep showing them; in the per-user folder, written under a lock. Entries whose
    folder is gone are left out when read."""

    def __init__(self, state_dir: Path):
        self.path = Path(state_dir) / INDEX_FILE

    def _data(self) -> Dict[str, List[Any]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        items, folders = data.get("saved"), data.get("folders")
        return {"saved": [i for i in items if isinstance(i, dict) and isinstance(i.get("path"), str)]
                if isinstance(items, list) else [],
                "folders": [f for f in folders if isinstance(f, str)] if isinstance(folders, list) else []}

    def _read(self) -> List[Dict[str, Any]]:
        return self._data()["saved"]

    def entries(self) -> List[Dict[str, Any]]:
        return [i for i in self._read() if Path(i["path"], MANIFEST).is_file()]

    def folders(self) -> List[Path]:
        """The earlier output folders that still exist."""
        return [Path(f) for f in self._data()["folders"] if Path(f).is_dir()]

    def _write(self, change) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(self.path.with_name(f".{INDEX_FILE}.lock"), timeout=10):
            data = self._data()
            change(data)
            tmp = self.path.with_name(f".{INDEX_FILE}.{os.getpid()}.tmp")
            with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
                json.dump({"version": 1, "saved": data["saved"][-MAX_INDEX:], "folders": data["folders"][-MAX_INDEX:]},
                          f, indent=1)
            os.replace(tmp, self.path)

    def add(self, folder: Path, root: Path) -> None:
        def change(data):
            data["saved"] = [i for i in data["saved"] if i["path"] != str(folder)] + [
                {"path": str(folder), "root": str(root)}]
        self._write(change)

    def add_folder(self, folder: Path) -> None:
        def change(data):
            data["folders"] = [f for f in data["folders"] if f != str(folder)] + [str(folder)]
        self._write(change)


@dataclass(frozen=True)
class ListedArtifact:
    """One finished build: where it is (builds, output or saved), its label as an AI may read it (where:<relative
    path>, never a host path), its key for the artifact id and its manifest (with "path", the person's full path)."""
    where: str
    label: str
    key: str
    info: Dict[str, Any]

    @property
    def path(self) -> Path:
        return Path(self.info["path"])


def roots(framework) -> List[Tuple[str, Path]]:
    """(where, folder): the output folder first, then the build folder when it is another folder."""
    out, builds = output_folder(framework), build_folder(framework)
    found = [(OUTPUT, out)]
    try:
        same = _real(out) == _real(builds)
    except OSError:
        same = str(out) == str(builds)
    if not same:
        found.append((BUILDS, builds))
    return found


def list_artifacts(framework) -> List[ListedArtifact]:
    """Every finished build in the output folder, the build folder and the folders artifacts were saved to, each
    once (newest first within each folder)."""
    from orchestrator.Core.deployment_manager import list_builds
    listed: List[ListedArtifact] = []
    seen = set()
    for where, folder in roots(framework):
        if not folder.is_dir():
            continue
        for info in list_builds(folder):
            real = os.path.realpath(info["path"])
            if real in seen:
                continue
            seen.add(real)
            try:
                relative = Path(info["path"]).resolve().relative_to(folder.resolve()).as_posix()
            except (ValueError, OSError):
                relative = Path(info["path"]).name  # a linked subfolder elsewhere: its name only
            key = relative if where == OUTPUT else f"{where}:{relative}"
            listed.append(ListedArtifact(where, f"{where}:{relative}", key, info))
    state = _state_dir(framework)
    index = SavedIndex(state) if state is not None else None
    for folder in index.folders() if index is not None else []:  # earlier output folders: their builds stay listed
        for info in list_builds(folder):
            real = os.path.realpath(info["path"])
            if real in seen:
                continue
            seen.add(real)
            try:
                relative = Path(real).relative_to(os.path.realpath(folder)).as_posix()
            except ValueError:
                relative = Path(info["path"]).name
            listed.append(ListedArtifact(SAVED, f"{SAVED}:{relative}", f"{SAVED}:{real}", info))
    if index is not None:
        for entry in index.entries():
            real = os.path.realpath(entry["path"])
            if real in seen:
                continue
            for info in list_builds(Path(entry["path"])):
                if os.path.realpath(info["path"]) != real:
                    continue  # only the saved folder itself, never other builds below it
                seen.add(real)
                try:
                    relative = Path(entry["path"]).relative_to(Path(entry.get("root") or "")).as_posix()
                except ValueError:
                    relative = Path(entry["path"]).name
                listed.append(ListedArtifact(SAVED, f"{SAVED}:{relative}", f"{SAVED}:{real}", info))
    return listed


def label_for(framework, path: Any) -> str:
    """A build folder or ZIP as an AI may read it: where:<relative path> inside the output or the build folder, its
    file name elsewhere (never a host path)."""
    real = _real(Path(str(path)))
    for where, folder in roots(framework):
        try:
            return f"{where}:{real.relative_to(_real(folder)).as_posix()}"
        except ValueError:
            continue
    return real.name


# --- saving -------------------------------------------------------------------------------------------------------

@dataclass
class SaveResult:
    """What a save did: the artifact's new folder and ZIP (full paths, for the person), whether it was renamed on one
    disk or copied and checked, whether the build folder's copy stays (something was not copied), and how many files
    and bytes."""
    folder: Path
    zip: Optional[Path] = None
    renamed: bool = False
    kept: bool = False
    files: int = 0
    bytes: int = 0
    notes: List[str] = field(default_factory=list)


def _disk_usage(path: str):
    """The free space of a disk (tests replace it: the test disk's own free space must never decide a test)."""
    return shutil.disk_usage(path)


def _tree_size(folder: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(folder, followlinks=False):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                continue
    return total


def _free_name(parent: Path, name: str) -> str:
    """`name`, or name-2, name-3, ... when a folder or a ZIP of that name is there already (never overwritten)."""
    candidate, n = name, 1
    while os.path.lexists(parent / candidate) or os.path.lexists(parent / f"{candidate}.zip"):
        n += 1
        candidate = f"{name}-{n}"
    return candidate


def _device(path: Path) -> int:
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return os.stat(probe).st_dev


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(COPY_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_file(source: Path, target: Path) -> str:
    """Copies one file (never through a link) and returns the sha256 of what was read."""
    digest = hashlib.sha256()
    fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    with open(fd, "rb") as fin, open(target, "xb") as fout:
        for chunk in iter(lambda: fin.read(COPY_CHUNK), b""):
            digest.update(chunk)
            fout.write(chunk)
        fout.flush()
        os.fsync(fout.fileno())
    shutil.copystat(source, target, follow_symlinks=False)
    return digest.hexdigest()


def _copy_verified(folder: Path, zip_file: Optional[Path], target: Path,
                   listed: Dict[str, str]) -> Tuple[int, int, List[str]]:
    """Copies the artifact folder and its ZIP next to `target` as unfinished copies, checks every copy against its
    original by sha256 (and a model file against the hash the manifest lists), then gives them their names (the
    manifest last, so a listing never sees half an artifact). The unfinished copies are the save's own: they are
    removed again whatever went wrong. Returns (files, bytes, what was not copied and why: links are never followed,
    and anything that is no plain file or folder is left out)."""
    token = secrets.token_hex(4)
    partial = target.parent / f".{target.name}.{token}.partial"
    partial_zip = target.parent / f".{target.name}.zip.{token}.partial"
    files = size = 0
    skipped: List[str] = []
    try:
        partial.mkdir(parents=True)
        entries = sorted(folder.rglob("*"), key=lambda p: (p.name == MANIFEST, str(p)))
        for item in entries:
            dest = partial / item.relative_to(folder)
            if item.is_symlink():
                skipped.append(f"{item.relative_to(folder).as_posix()} (a link; links are never followed)")
                continue
            if item.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
                continue
            if not item.is_file():
                skipped.append(f"{item.relative_to(folder).as_posix()} (not a plain file)")
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            read = _copy_file(item, dest)
            written = _sha256(dest)
            expected = listed.get(item.name) if item.parent == folder else None
            if written != read or (expected and expected.lower() != read):
                raise DestinationRefused(f"The copy of {item.name} does not match its original (sha256); nothing "
                                         "was saved and the artifact stays where it was.",
                                         "Check the disks, then try again.", code="failed")
            files += 1
            size += dest.stat().st_size
        if zip_file is not None:
            read = _copy_file(zip_file, partial_zip)
            if _sha256(partial_zip) != read:
                raise DestinationRefused(f"The copy of {zip_file.name} does not match its original (sha256); nothing "
                                         "was saved and the artifact stays where it was.",
                                         "Check the disks, then try again.", code="failed")
            files += 1
            size += partial_zip.stat().st_size
        os.rename(partial, target)
        if zip_file is not None:
            os.rename(partial_zip, target.parent / f"{target.name}.zip")
        return files, size, skipped
    finally:
        if partial.exists():
            shutil.rmtree(partial, ignore_errors=True)
        if os.path.lexists(partial_zip):
            try:
                partial_zip.unlink()
            except OSError:
                pass


def _remove_moved_source(folder: Path, zip_file: Optional[Path]) -> List[str]:
    """After every file was copied and checked by sha256 (the artifact is at its new place), the build folder's copy
    of the moved artifact is removed: a move, never a deletion of the artifact. Returns what could not be removed."""
    problems = []
    try:
        shutil.rmtree(folder)
    except OSError as e:
        problems.append(f"{folder} ({e.strerror or e})")
    if zip_file is not None:
        try:
            zip_file.unlink()
        except OSError as e:
            problems.append(f"{zip_file} ({e.strerror or e})")
    return problems


def _rename(folder: Path, zip_file: Optional[Path], target: Path, target_zip: Optional[Path]) -> bool:
    """One disk: the artifact folder and its ZIP are renamed (nothing copied, nothing removed). False when the system
    cannot rename between these folders (two mounts of one disk): then they are copied and checked instead."""
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(folder, target)
    except OSError as e:
        if e.errno == errno.EXDEV:
            return False
        raise DestinationRefused(f"Not saved: {folder} could not be moved ({e.strerror or e}); it stays where it was.",
                                 "Choose another folder.", code="failed") from None
    if zip_file is not None:
        try:
            os.rename(zip_file, target_zip)
        except OSError as e:
            os.rename(target, folder)
            raise DestinationRefused(f"Not saved: {zip_file} could not be moved ({e.strerror or e}); the model stays "
                                     "where it was.", "Choose another folder.", code="failed") from None
    return True


def save_artifact(framework, folder: Any, destination: Any, *, create: bool = False) -> SaveResult:
    """Saves the finished build `folder` (in the framework's build folder) to `destination`, as its relative path
    there (<target>/<model>/<quantization>, "-2" when that name is taken), with its golden ZIP. See the module text
    for the rules; DestinationRefused (nothing moved) when a check fails."""
    from orchestrator.Core.disk_plan import DiskNeed, ReservationLedger, check_disk
    source = Path(str(folder))
    builds = build_folder(framework)
    real_builds = builds.resolve()
    try:
        real = source.resolve(strict=True)
    except OSError:
        raise DestinationRefused(f"{source} is no longer there.", "Look at the list of builds again.",
                                 code="unknown_id") from None
    if source.is_symlink() or real == real_builds or real_builds not in real.parents:
        raise DestinationRefused(f"{source} does not lie in the framework's build folder; only a build waiting "
                                 "there is saved.", "Choose a build that is not saved yet.", code="not_supported")
    if not (real / MANIFEST).is_file():
        raise DestinationRefused(f"{source} is not a finished build (no {MANIFEST}).", "Choose a finished build.",
                                 code="not_supported")
    root = check_destination(framework, destination, create=create)
    relative = real.relative_to(real_builds)
    parent = root / relative.parent
    target = parent / _free_name(parent, relative.name)
    zip_file = real.parent / f"{real.name}.zip"
    zip_file = zip_file if zip_file.is_file() and not zip_file.is_symlink() else None
    try:
        manifest = json.loads((real / MANIFEST).read_text(encoding="utf-8"))
        listed = {a["file"]: a["sha256"] for a in manifest.get("artifacts") or []
                  if isinstance(a, dict) and isinstance(a.get("file"), str) and isinstance(a.get("sha256"), str)}
    except (OSError, ValueError, AttributeError):
        listed = {}
    result = SaveResult(folder=target, zip=(target.parent / f"{target.name}.zip") if zip_file else None)
    if _device(real) == _device(parent):
        result.renamed = _rename(real, zip_file, target, result.zip)
    if result.renamed:
        result.files = sum(1 for p in target.rglob("*") if p.is_file()) + (1 if zip_file else 0)
        result.bytes = _tree_size(target) + (result.zip.stat().st_size if result.zip else 0)
    else:
        needed = _tree_size(real) + (zip_file.stat().st_size if zip_file else 0)
        state = _state_dir(framework)
        ledger = ReservationLedger(state) if state is not None else None
        checks = check_disk([DiskNeed("destination", root, needed, "the saved model")], ledger,
                            disk_usage=_disk_usage)
        short = [c for c in checks if not c.fits]
        if short:
            raise DestinationRefused("Not saved: not enough disk space there; the model "
                                     + "; ".join(c.sentence() for c in short) + ".",
                                     "Free space there or choose a folder on another disk; the model stays in the "
                                     "build folder.", code="insufficient_resources")
        try:
            result.files, result.bytes, skipped = _copy_verified(real, zip_file, target, listed)
        except OSError as e:  # a disk that ran full or a file that cannot be read: only the unfinished copy went
            raise DestinationRefused(f"Not saved: the copy failed ({e.strerror or e}); the model stays in the build "
                                     "folder.", "Check the disk there, then try again.", code="failed") from None
        if skipped:  # the new copy does not hold everything: the build folder's copy stays, nothing is removed
            result.kept = True
            result.notes += [f"Not copied: {', '.join(skipped)}. The build folder's copy stays in {real}, so the "
                             "model is in both places now."]
        else:
            result.notes += [f"Could not remove the build folder's copy of {p}; the model is in both places now."
                             for p in _remove_moved_source(real, zip_file)]
    out = output_folder(framework)
    state = _state_dir(framework)
    if state is not None and not _inside(_real(target), _real(out)):
        try:
            SavedIndex(state).add(target, root)
        except (OSError, TimeoutError) as e:
            result.notes.append(f"It is not listed by the framework ({e}); it is saved in {target}.")
    return result


def set_output_folder(framework, folder: Any) -> Path:
    """The person sets the output folder (File > Output Folder... in the GUI, `llm-cli config output-folder`; never an
    AI): checked like a destination and made when it does not exist, then saved in config.yml. The earlier output
    folder stays listed (keep_listing). DestinationRefused when the folder is refused; OSError when config.yml could
    not be written (the setting is unchanged then). Everything below the output folder is listed, so a folder that
    holds the framework's build folder, per-user folder or program folder (for example the home folder) is refused
    as the output folder; a single model may still be saved there."""
    holds = _holds_framework_folders(framework, folder)
    if holds:
        raise DestinationRefused(f"Not the output folder: {holds}.", "Choose a folder inside it that holds none of "
                                                                     "them, for example ~/llmcf/output.")
    chosen = check_destination(framework, folder, create=True)
    old_value, old = _config_get(framework, "output_dir", None), output_folder(framework)
    config = framework.config
    if not (config.set("output_dir", str(chosen)) and config.save_user_config()):
        if old_value is not None:
            config.set("output_dir", old_value)
        raise OSError("the settings file could not be written")
    if _real(old) != chosen:
        try:
            keep_listing(framework, old)
        except (OSError, TimeoutError):
            pass  # the earlier builds stay where they are; only the listing misses them
    return chosen


def _holds_framework_folders(framework, folder: Any) -> str:
    """Why `folder` cannot be the output folder ("" when it can): it holds the framework's build folder, its per-user
    folder or its program folder, which a listing of the output folder would walk through."""
    if folder is None or not str(folder).strip():
        return ""
    real = _real(Path(str(folder).strip()))
    own = [("the framework's build folder", build_folder(framework))]
    state = _state_dir(framework)
    if state is not None:
        own.append(("the framework's per-user folder", state))
    own += [("the framework's program folder", program) for program in _program_folders(framework)]
    for what, path in own:
        if real in _real(Path(path)).parents:
            return f"{real} holds {what}"
    return ""


def keep_listing(framework, old: Any) -> None:
    """The person changed the output folder: the earlier one is remembered, so its builds stay where they are and stay
    listed (a folder that does not exist, or the build folder, is not remembered)."""
    state = _state_dir(framework)
    folder = _real(Path(str(old)))
    if state is None or not folder.is_dir() or _inside(folder, _real(build_folder(framework))):
        return
    SavedIndex(state).add_folder(folder)


def remember(framework, folder: Any, root: Any) -> None:
    """Remembers a finished build a person sent outside the output folder (the CLI's --output), so the listings show
    it."""
    state = _state_dir(framework)
    if state is None:
        return
    target = _real(Path(str(folder)))
    if any(_inside(target, _real(f)) for _, f in roots(framework)):
        return
    SavedIndex(state).add(target, _real(Path(str(root))))


__all__ = ["BUILDS", "DEFAULT_OUTPUT", "DestinationRefused", "INDEX_FILE", "ListedArtifact", "OUTPUT", "SAVED",
           "SaveResult", "SavedIndex", "WHERE_TEXT", "build_folder", "check_destination", "keep_listing",
           "label_for", "list_artifacts", "output_folder", "refusal", "remember", "roots", "save_artifact",
           "set_output_folder"]
