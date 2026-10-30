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

Using an earlier identical build (DECISIONS round 23, item 9): "Use" before a build (the GUI's Builder tab and `llm-cli
build start`) copies the earlier build, with its golden ZIP, into the chosen folder (`plan_copy`, `copy_artifact`):
the earlier build stays where it is. The one disk rule is checked before the copy (with every process's
reservations); every copy is checked against its original by sha256. When the folder there holds the identical build
already, nothing is copied. When something else holds that name, it is replaced only after the person accepted the
full list of what is replaced ("No" is the default; moved to the trash or deleted for good, as the person chose), and
only once the copy is complete and checked.
"""

import errno
import json
import os
import secrets
import shutil
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from orchestrator.utils.file_lock import file_lock
from orchestrator.utils.helpers import copy_hashed
from orchestrator.utils.helpers import sha256_file as _sha256
from orchestrator.utils.atomic import remove_tree

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
# Windows: the folders the environment names (the system folder, the program folders, ProgramData with the computer's
# keys, credentials and installer caches) and everything below them ...
_WINDOWS_TREE_VARS = ("SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)", "ProgramFiles(Arm)", "ProgramW6432",
                      "ProgramData")
# ... the same folders where Windows puts them on the system drive (a process started with a reduced environment
# refuses them too), with the recovery, boot and update folders at its top (Windows deletes Windows.old and the
# update folders on its own, with whatever lies in them) ...
_WINDOWS_SYSTEM_DRIVE_TREES = ("Windows", "Program Files", "Program Files (x86)", "Program Files (Arm)", "ProgramData",
                               "Recovery", "Boot", "PerfLogs", "Config.Msi", "$WinREAgent", "$Windows.~BT",
                               "$Windows.~WS", "Windows.old")
# ... the folders at the top of every disk (the recycle bin, the restore points), and the template profiles next to
# the users' home folders (what lies there is given to every new user).
_WINDOWS_VOLUME_TREES = ("$Recycle.Bin", "System Volume Information")
_WINDOWS_PROFILE_TREES = ("Default", "Default User", "All Users")


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


def windows_plain_path(path: PureWindowsPath) -> PureWindowsPath:
    """`path` without Windows' device prefix: \\\\?\\C:\\x and \\\\.\\C:\\x are C:\\x, \\\\?\\UNC\\server\\share\\x is
    \\\\server\\share\\x. ntpath.realpath keeps such a prefix when the path was given with it, and the prefix makes
    the drive another one for every comparison. A prefixed path without a drive letter (a volume GUID) stays."""
    text = str(path)
    if text[:4] not in ("\\\\?\\", "\\\\.\\"):
        return path
    rest = text[4:]
    if rest[:4].casefold() == "unc\\":
        return PureWindowsPath("\\\\" + rest[4:])
    plain = PureWindowsPath(rest)
    return plain if plain.drive and plain.root else path


def windows_system_folder(path: PureWindowsPath, env: Mapping[str, str], users: Optional[PureWindowsPath] = None,
                          resolve: Callable[[PureWindowsPath], PureWindowsPath] = lambda p: p) -> str:
    """Why the resolved Windows path `path` is a system folder or lies inside one ("" when it is not): the system
    folder, the program folders and ProgramData (by the environment and at their usual places on the system drive),
    the recovery, boot and update folders, the recycle bin and the restore points of every disk, the template profiles
    in `users` (the folder that holds the home folders) and the system drive itself. Letter case and a device prefix
    (\\\\?\\) do not count, as on Windows. `env`: the environment (Windows' own mapping ignores the case of a name, a
    plain one does not, so the upper-case spelling is read too). `resolve` turns one of these folders into the form
    `path` has (links followed); both forms count. Pure, so it is tested on every system."""
    def get(name: str) -> str:
        return env.get(name) or env.get(name.upper()) or ""

    path = windows_plain_path(path)
    drive = PureWindowsPath(get("SystemDrive") or "C:").drive or "C:"
    root = PureWindowsPath(drive + "\\")
    trees = [PureWindowsPath(get(var)) for var in _WINDOWS_TREE_VARS if get(var)]
    trees += [root / name for name in _WINDOWS_SYSTEM_DRIVE_TREES]
    if users is not None:
        trees += [users / name for name in _WINDOWS_PROFILE_TREES]
    for folder in trees:
        for form in dict.fromkeys(windows_plain_path(f) for f in (folder, resolve(folder))):
            if _inside(path, form):
                return f"{path} is a system folder or lies inside one ({folder})"
    top = path.parts[1] if path.drive and len(path.parts) > 1 else ""
    for name in _WINDOWS_VOLUME_TREES:
        if top.casefold() == name.casefold():
            return f"{path} is a system folder or lies inside one ({PureWindowsPath(path.anchor, top)})"
    if path == root:
        return f"{path} is a system folder; choose a folder inside it"
    return ""


def refusal(framework, destination: Path) -> str:
    """Why `destination` may not hold golden artifacts ("" when it may): a system folder, the framework's program
    folder, its build folder or its per-user folder."""
    real = _real(destination)
    home = _real(Path.home())
    if os.name == "nt":
        real = Path(windows_plain_path(PureWindowsPath(real)))  # \\?\C:\Windows is C:\Windows for every check
        why = windows_system_folder(PureWindowsPath(real), os.environ, PureWindowsPath(home.parent),
                                    lambda folder: PureWindowsPath(_real(Path(folder))))
        if why:
            return why
    else:
        for folder in (Path(p) for p in _POSIX_TREES):
            if _inside(real, _real(folder)):
                return f"{real} is a system folder or lies inside one ({folder})"
        for folder in (Path(p) for p in _POSIX_EXACT):
            if real == _real(folder):
                return f"{real} is a system folder; choose a folder inside it"
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


def artifact_folders(framework) -> List[Path]:
    """Every folder that holds golden artifacts, each once: the output folder, the build folder, earlier output
    folders and the folders artifacts were saved to (what list_artifacts lists). A "tested on" record of a board
    benchmark (runtime/device/tested.py) looks for the build of its model in these."""
    found: List[Path] = []
    seen = set()

    def add(folder) -> None:
        folder = Path(folder)
        key = os.path.realpath(folder)
        if key not in seen:
            seen.add(key)
            found.append(folder)
    for _where, folder in roots(framework):
        add(folder)
    state = _state_dir(framework)
    if state is not None:
        index = SavedIndex(state)
        for folder in index.folders():
            add(folder)
        for entry in index.entries():
            add(entry["path"])
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
    already: bool = False   # "Use" of an earlier build: the identical build lies there already, nothing was copied


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


def _device(path: Path) -> str:
    """The disk `path` is on (its nearest existing folder): disk_plan.device_of."""
    from orchestrator.Core.disk_plan import device_of
    return device_of(path)


def _copy_file(source: Path, target: Path) -> str:
    """Copies one file (never through a link) and returns the sha256 of what was read."""
    digest = copy_hashed(source, target, fsync=True, chunk=COPY_CHUNK)
    shutil.copystat(source, target, follow_symlinks=False)
    return digest


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
            remove_tree(partial)
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


# --- using an earlier identical build: a copy into the chosen folder ------------------------------------------------

@dataclass
class CopyPlan:
    """What "Use" of an earlier build does (plan_copy): copy `source` (and its golden ZIP `zip`) to `target` below the
    checked folder `root`, `needed` bytes. `there`: the identical build lies there already (nothing to copy).
    `replaces`: what the copy would replace at `target` (the folder and the ZIP there, with their full paths) as the
    person's deletion list, None when the name is free."""
    source: Path
    zip: Optional[Path]
    root: Path
    target: Path
    needed: int
    there: bool = False
    replaces: Any = None


def _same_files(a: Path, b: Path, names) -> bool:
    """Whether every listed model file lies in `b` as a plain file of the size it has in `a` (a model file deleted or
    changed after the build is no identical build; no file is read, so this is quick also on the GUI thread)."""
    for name in names:
        if not name or Path(name).name != name:
            return False
        try:
            mine, theirs = os.lstat(a / name), os.lstat(b / name)
        except OSError:
            return False
        if not stat.S_ISREG(theirs.st_mode) or theirs.st_size != mine.st_size:
            return False
    return True


def _same_build(a: Path, b: Path) -> bool:
    """Whether two build folders hold the same build: the same recorded options and the same model files (sha256 in
    their manifests), each of them there in `b` with its size in `a` (_same_files)."""
    from orchestrator.Core.build_identity import MANIFEST_KEY
    try:
        first = json.loads((a / MANIFEST).read_text(encoding="utf-8"))
        second = json.loads((b / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(first, dict) or not isinstance(second, dict):
        return False

    def files(info):
        return sorted((str(x.get("file")), str(x.get("sha256"))) for x in info.get("artifacts") or []
                      if isinstance(x, dict))
    return bool(files(first)) and files(first) == files(second) and \
        first.get(MANIFEST_KEY) == second.get(MANIFEST_KEY) and first.get("primary") == second.get("primary") and \
        _same_files(a, b, [name for name, _ in files(first)])


def _replace_plan(framework, target: Path, modes=("trash", "permanent")):
    """The person's deletion list of what a copy to `target` replaces: the folder and the golden ZIP there (None when
    neither exists)."""
    from orchestrator.Core.deletion import plan_of
    from orchestrator.tools.spec import DeletionItem
    label = label_for(framework, target) or target.name
    items = []
    if os.path.lexists(target):
        items.append(DeletionItem(kind="artifact", speaking_name=label, full_path=str(target), file_name=target.name,
                                  size_bytes=_tree_size(target) if target.is_dir() and not target.is_symlink()
                                  else os.lstat(target).st_size,
                                  what="what lies there now under the earlier build's name (the copy replaces it)"))
    zip_file = target.parent / f"{target.name}.zip"
    if os.path.lexists(zip_file):
        items.append(DeletionItem(kind="artifact", speaking_name=f"{label} (golden ZIP)", full_path=str(zip_file),
                                  file_name=zip_file.name, size_bytes=os.lstat(zip_file).st_size,
                                  what="the golden ZIP there now (the copy's ZIP replaces it)"))
    return plan_of(items, modes) if items else None


def plan_copy(framework, folder: Any, destination: Any, *, relative: Optional[str] = None,
              create: bool = False) -> CopyPlan:
    """What "Use" of the earlier build `folder` (a finished build anywhere the listings show it) copies into the
    folder `destination` (checked as every destination: check_destination; `create` makes it): the build's folder
    with its golden ZIP at `relative` below it (its path below the folder it lies in, as its label names it:
    <target>/<model>/<quantization>; default its folder name). DestinationRefused when the build is gone, is no
    finished build, or the destination is refused or lies inside the build."""
    source = Path(str(folder))
    try:
        real = source.resolve(strict=True)
    except OSError:
        raise DestinationRefused(f"{source} is no longer there.", "Look at the list of builds again.",
                                 code="unknown_id") from None
    if source.is_symlink() or not (real / MANIFEST).is_file():
        raise DestinationRefused(f"{source} is not a finished build (no {MANIFEST}).", "Choose a finished build.",
                                 code="not_supported")
    root = check_destination(framework, destination, create=create)
    parts = Path(str(relative or real.name).replace("\\", "/")).parts
    if not parts or Path(*parts).is_absolute() or any(p in ("", ".", "..") for p in parts):
        parts = (real.name,)
    target = root.joinpath(*parts)
    zip_file = real.parent / f"{real.name}.zip"
    zip_file = zip_file if zip_file.is_file() and not zip_file.is_symlink() else None
    needed = _tree_size(real) + (zip_file.stat().st_size if zip_file else 0)
    plan = CopyPlan(source=real, zip=zip_file, root=root, target=target, needed=needed)
    real_target = _real(target)
    if real_target == real:
        plan.there = True
        return plan
    if _inside(real_target, real) or _inside(real, real_target):
        raise DestinationRefused(f"Not copied: {target} and the earlier build {real} lie inside each other.",
                                 "Choose another folder.", code="not_supported")
    if target.is_dir() and not target.is_symlink() and _same_build(real, target):
        plan.there = True
        return plan
    plan.replaces = _replace_plan(framework, target)
    return plan


def copy_artifact(framework, plan: CopyPlan, *, replace: Any = None, trash: Any = None,
                  audit: Any = None) -> SaveResult:
    """Copies the earlier build of `plan` (plan_copy) into its folder: the one disk rule first (check_disk with every
    process's reservations; nothing is copied when it does not fit), then every file is copied and checked against
    its original by sha256 (the earlier build stays where it is). When the name is taken (`plan.replaces`), `replace`
    must be the person's accepted deletion of exactly that list (orchestrator.Core.deletion; PermissionError without
    it, PlanChanged when what lies there changed since): the copy is made under a free name first, then what lay
    there goes to the trash (`trash`, a Trash) or is deleted for good, as the person chose (written to the `audit`
    log first; without it nothing is copied or replaced), and the copy takes its name. DestinationRefused with the
    reason when the copy cannot be made."""
    from orchestrator.Core.deletion import require_person
    from orchestrator.Core.disk_plan import DiskNeed, ReservationLedger, check_disk
    target = plan.target
    target_zip = target.parent / f"{target.name}.zip"
    if plan.there:
        return SaveResult(folder=target, zip=target_zip if target_zip.is_file() else None, already=True)
    accepted = None
    if plan.replaces is not None:
        if replace is None:
            raise DestinationRefused(f"{target} exists already; nothing was copied.",
                                     "Accept replacing it, or choose another folder.", code="needs_person")
        accepted = require_person(replace)
        if accepted.mode == "trash" and trash is None:
            raise DestinationRefused("The trash is not available; nothing was copied or replaced.",
                                     "Delete for good instead, or choose another folder.", code="failed")
        if audit is None or not callable(getattr(audit, "record", None)):
            raise DestinationRefused("The audit log is not available, so nothing was copied or replaced.",
                                     "Choose another folder, or try again once the framework has started.",
                                     code="failed")
    state = _state_dir(framework)
    ledger = ReservationLedger(state) if state is not None else None
    checks = check_disk([DiskNeed("destination", plan.root, plan.needed, "the copy of the earlier build")], ledger,
                        disk_usage=_disk_usage)
    short = [c for c in checks if not c.fits]
    if short:
        raise DestinationRefused("Not copied: not enough disk space there; the copy "
                                 + "; ".join(c.sentence() for c in short) + ".",
                                 "Free space there or choose a folder on another disk; the earlier build stays where "
                                 "it is.", code="insufficient_resources")
    try:
        manifest = json.loads((plan.source / MANIFEST).read_text(encoding="utf-8"))
        listed = {a["file"]: a["sha256"] for a in manifest.get("artifacts") or []
                  if isinstance(a, dict) and isinstance(a.get("file"), str) and isinstance(a.get("sha256"), str)}
    except (OSError, ValueError, AttributeError):
        listed = {}
    parent = target.parent
    copy = parent / _free_name(parent, target.name)
    try:
        files, size, skipped = _copy_verified(plan.source, plan.zip, copy, listed)
    except OSError as e:  # a disk that ran full or a file that cannot be read: only the unfinished copy went
        raise DestinationRefused(f"Not copied: the copy failed ({e.strerror or e}); the earlier build stays where it "
                                 "is.", "Check the disk there, then try again.", code="failed") from None
    result = SaveResult(folder=copy, zip=(copy.parent / f"{copy.name}.zip") if plan.zip else None, files=files,
                        bytes=size)
    if skipped:
        result.kept = True
        result.notes.append(f"Not copied: {', '.join(skipped)}.")
        if accepted is not None:   # only a complete copy replaces what lies under the name
            result.notes.append(f"The copy is not complete, so nothing was replaced: {target} stays as it is, and the "
                                f"copy is in {result.folder}.")
    elif accepted is not None:
        result.notes += _replace_with_copy(framework, plan, accepted, trash, audit, result)
    out = output_folder(framework)
    if state is not None and not _inside(_real(result.folder), _real(out)):
        try:
            SavedIndex(state).add(result.folder, plan.root)
        except (OSError, TimeoutError) as e:
            result.notes.append(f"It is not listed by the framework ({e}); it is in {result.folder}.")
    return result


def _replace_with_copy(framework, plan: CopyPlan, accepted: Any, trash: Any, audit: Any,
                       result: SaveResult) -> List[str]:
    """What lay at the plan's target goes as the person accepted it (the list must still be exactly that one; the
    audit log records it first), then the finished copy takes its name. Returns notes; the copy keeps its free name
    when anything stops this."""
    from orchestrator.Core.deletion import PlanChanged, require_same
    target = plan.target
    current = _replace_plan(framework, target, accepted.plan.mode_offered)
    try:
        require_same(accepted, current, f"what lies at {target}")
    except PlanChanged as e:
        return [f"{e} The copy is in {result.folder}."]
    paths = [Path(item.full_path) for item in current.items]
    try:
        audit.record(accepted.actor, "artifacts", "framework", "person", "earlier_build_replaced", approved=True,
                     arguments={"items": len(paths), "bytes": accepted.plan.total_bytes, "mode": accepted.mode},
                     detail="the copy of an earlier identical build replaces what lay under its name ("
                            + ("moved to the trash" if accepted.mode == "trash" else "deleted for good") + ")",
                     extra=accepted.audit_extra())
    except Exception:  # noqa: BLE001 - the reason could name a host path
        return [f"The audit log cannot record the replacement; nothing was replaced. The copy is in {result.folder}."]
    if accepted.mode == "trash":
        from orchestrator.Core.trash import TrashError
        where = OUTPUT if _inside(_real(plan.root), _real(output_folder(framework))) else SAVED
        for item, path in zip(current.items, paths):
            try:
                trash.move("artifact", path, plan.root, where, item.speaking_name, accepted, accepted.actor)
            except TrashError as e:
                return [f"{e} Nothing more was replaced; the copy is in {result.folder}."]
    else:
        for path in paths:
            try:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            except OSError as e:
                return [f"{path} could not be deleted ({e.strerror or e}); the copy is in {result.folder}."]
    try:
        os.rename(result.folder, target)
        if result.zip is not None:
            os.rename(result.zip, target.parent / f"{target.name}.zip")
    except OSError as e:
        return [f"The copy could not take the name {target.name} ({e.strerror or e}); it is in {result.folder}."]
    result.folder = target
    if result.zip is not None:
        result.zip = target.parent / f"{target.name}.zip"
    return []


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


__all__ = ["BUILDS", "CopyPlan", "DEFAULT_OUTPUT", "DestinationRefused", "INDEX_FILE", "ListedArtifact", "OUTPUT",
           "SAVED", "SaveResult", "SavedIndex", "WHERE_TEXT", "artifact_folders", "build_folder", "check_destination",
           "copy_artifact", "keep_listing", "label_for", "list_artifacts", "output_folder", "plan_copy", "refusal",
           "remember", "roots", "save_artifact", "set_output_folder", "windows_plain_path", "windows_system_folder"]
