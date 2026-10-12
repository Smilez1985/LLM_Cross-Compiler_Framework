#!/usr/bin/env python3
"""
Local model and dataset files an AI may use, and nothing else.

AI callers (Ditto and MCP clients) never name a path. They name a file by its id (`lf_` plus 16 hex characters,
recorded in an index in the per-user folder) or as `<label>:<relative path>` inside a labelled folder: the models
folder (`models`), the datasets folder (`datasets`), or a folder the person added to "folders the AI may use"
(`llm-cli tools folders add`, or the GUI). The resolved file must lie inside that folder and outside every denied
folder (SSH and GPG keys, credential stores, the Hugging Face token, the framework's own per-user folder, system
folders). "Inside" and "outside" are decided by file identity (`st_dev`, `st_ino` of every ancestor), never by
comparing strings, so a case-insensitive file system cannot hide `~/.SSH` from `~/.ssh`.

A resolved file is pinned by its identity, size and modification time; the build engine checks the pin again right
before a container starts and refuses when anything changed. A folder is mounted only when it lies strictly below
its label root, holds no denied folder and no file with more than one hard link.
"""

import hashlib
import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from orchestrator.utils.file_lock import file_lock
from orchestrator.utils.logging import get_logger

LABEL_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
FILE_ID_RE = re.compile(r"^lf_[0-9a-f]{16}$")
BUILTIN_LABELS = ("models", "datasets")
FOLDER_KINDS = ("model", "dataset", "any")
MAX_FOLDER_ENTRIES = 20000  # files and folders checked inside a folder that is mounted
MAX_LINKED_BINDS = 512      # files bound one by one from a folder of links (Hugging Face cache snapshots)
MAX_INDEX_ENTRIES = 5000
FOLDER_WARNING = ("Files in this folder can be mounted into builds that run community or AI-written code, and can "
                  "reach AI clients through build logs.")

Identity = Tuple[int, int]
FileIdentity = Tuple[int, int, int, int]

logger = get_logger("LocalFiles")


class LocalFileError(ValueError):
    """A local-file reference that is refused; the message says why and what to do next."""

    def __init__(self, message: str, code: str = "invalid_value", next_step: str = ""):
        self.code = code
        self.next_step = next_step
        super().__init__(f"{message} Next: {next_step}" if next_step else message)


# --- file identity ------------------------------------------------------------------------------------------

def _stat(path) -> os.stat_result:
    """stat() that follows links (tests replace it to simulate a case-insensitive file system)."""
    return os.stat(path)


def _identity(path) -> Optional[Identity]:
    try:
        st = _stat(path)
    except (OSError, ValueError):
        return None
    return st.st_dev, st.st_ino


def _ancestor_ids(path: Path) -> set:
    """Identities of `path` and of every folder above it."""
    ids = set()
    for p in (path, *path.parents):
        ident = _identity(p)
        if ident is not None:
            ids.add(ident)
    return ids


def _file_identity(path: Path) -> FileIdentity:
    st = _stat(path)
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns


# --- denied folders ----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Denied:
    path: Path
    reason: str
    # Folders strictly below `path` that stay usable when the label root lies inside them too (the data folders
    # inside the per-user folder of a read-only installation; the Hugging Face cache's hub folder).
    exceptions: Tuple[Path, ...] = ()


def huggingface_paths(env=None) -> Tuple[Path, Path, Path]:
    """The Hugging Face home folder and its token files, the way huggingface_hub finds them."""
    env = os.environ if env is None else env
    home = Path(os.path.expanduser("~"))
    cache = env.get("XDG_CACHE_HOME") or str(home / ".cache")
    hf_home = Path(os.path.expanduser(os.path.expandvars(env.get("HF_HOME") or os.path.join(cache, "huggingface"))))
    token = Path(os.path.expanduser(os.path.expandvars(env.get("HF_TOKEN_PATH") or str(hf_home / "token"))))
    return hf_home, token, token.parent / "stored_tokens"


def denied_folders(state_dir: Optional[Path], data_folders: Sequence[Path] = (), env=None) -> List[Denied]:
    """Folders and files no AI caller may read or mount, even inside a folder the AI may use."""
    env = os.environ if env is None else env
    home = Path(os.path.expanduser("~"))
    entries: List[Denied] = []

    def add(path, reason: str, exceptions: Iterable[Path] = ()):
        entries.append(Denied(Path(path), reason, tuple(exceptions)))

    if state_dir is not None:
        add(state_dir, "it is the framework's own folder (permissions, audit log, credentials)", data_folders)
    for name, reason in ((".ssh", "it holds SSH keys"), (".gnupg", "it holds GPG keys"),
                         (".config", "it holds application settings and credentials"),
                         (".local/share/keyrings", "it holds the keyring"),
                         (".password-store", "it holds passwords"), (".netrc", "it holds passwords"),
                         (".git-credentials", "it holds passwords"), (".docker", "it holds registry credentials"),
                         (".aws", "it holds cloud credentials"), (".kube", "it holds cluster credentials"),
                         ("Library/Keychains", "it holds the keychain"),
                         (".mozilla", "it holds a browser profile"), ("snap/firefox", "it holds a browser profile"),
                         ("snap/chromium", "it holds a browser profile"),
                         ("Library/Application Support/Google/Chrome", "it holds a browser profile"),
                         ("Library/Application Support/Chromium", "it holds a browser profile"),
                         ("Library/Application Support/Firefox", "it holds a browser profile"),
                         ("Library/Application Support/Microsoft Edge", "it holds a browser profile"),
                         ("Library/Application Support/BraveSoftware", "it holds a browser profile"),
                         ("Library/Safari", "it holds a browser profile"), ("Library/Cookies", "it holds cookies"),
                         # AI clients' own sign-ins and settings (the framework never reads them, principle 5)
                         (".claude", "it holds an AI client's sign-in"), (".claude.json", "it holds an AI client's "
                                                                                          "settings"),
                         (".codex", "it holds an AI client's sign-in"), (".gemini", "it holds an AI client's sign-in"),
                         (".cursor", "it holds an AI client's settings")):
        add(home / name, reason)
    for var, reason in (("XDG_CONFIG_HOME", "it holds application settings and credentials"),
                        ("GNUPGHOME", "it holds GPG keys")):
        if env.get(var):
            add(Path(os.path.expanduser(env[var])), reason)
    hf_home, hf_token, hf_stored = huggingface_paths(env)
    add(hf_home, "that folder holds your Hugging Face token", [hf_home / "hub"])
    add(hf_token, "it is your Hugging Face token")
    add(hf_stored, "it holds your Hugging Face tokens")
    for system in ("/etc", "/proc", "/sys", "/dev"):
        add(system, "it is a system folder")
    if _identity("/root") != _identity(home):  # another user's home; your own home is covered entry by entry
        add("/root", "it is the administrator's home folder")
    for var, parts, reason in (("APPDATA", ("Microsoft", "Credentials"), "it holds Windows credentials"),
                               ("APPDATA", ("Microsoft", "Protect"), "it holds Windows credential keys"),
                               ("LOCALAPPDATA", ("Microsoft", "Credentials"), "it holds Windows credentials"),
                               ("APPDATA", ("Mozilla", "Firefox"), "it holds a browser profile"),
                               ("LOCALAPPDATA", ("Google", "Chrome", "User Data"), "it holds a browser profile"),
                               ("LOCALAPPDATA", ("Microsoft", "Edge", "User Data"), "it holds a browser profile"),
                               ("LOCALAPPDATA", ("BraveSoftware",), "it holds a browser profile")):
        base = env.get(var)
        if base:
            add(Path(base).joinpath(*parts), reason)
    return entries


def _exception_applies(exception: Path, denied_id: Identity, path_ids: set, root_ids: set) -> bool:
    """An exception folder counts only when it lies strictly below the denied folder and both the file and the
    label root lie inside it (so a label above it cannot reach the denied rest through it)."""
    exception_id = _identity(exception)
    if exception_id is None or exception_id == denied_id:
        return False
    try:
        if denied_id not in _ancestor_ids(Path(exception).resolve()):
            return False
    except (OSError, RuntimeError):
        return False
    return exception_id in path_ids and exception_id in root_ids


def denied_reason(path: Path, denied: Sequence[Denied], root: Optional[Path] = None) -> Optional[Denied]:
    """The denied folder `path` lies in (by identity), or None."""
    path_ids = _ancestor_ids(path)
    root_ids = _ancestor_ids(root) if root is not None else set()
    for entry in denied:
        denied_id = _identity(entry.path)
        if denied_id is None or denied_id not in path_ids:
            continue
        if any(_exception_applies(e, denied_id, path_ids, root_ids) for e in entry.exceptions):
            continue
        return entry
    return None


def contained_denied(folder: Path, denied: Sequence[Denied]) -> Optional[Denied]:
    """A denied folder that lies inside `folder` (by identity), or None."""
    folder_id = _identity(folder)
    if folder_id is None:
        return None
    for entry in denied:
        if _identity(entry.path) is None:
            continue
        try:
            if folder_id in _ancestor_ids(entry.path.resolve()):
                return entry
        except (OSError, RuntimeError):
            continue
    return None


# --- references ---------------------------------------------------------------------------------------------

def labels_hint(labels: Sequence[str]) -> str:
    return (f"name the file as <label>:<relative path> inside one of these folders: {', '.join(labels) or 'none'}. "
            "The user can add folders with 'llm-cli tools folders add' or in the GUI (Tools > Folders the AI may "
            "use).")


def split_reference(reference: str, labels: Sequence[str] = ()) -> Tuple[str, str]:
    """`<label>:<relative path>` -> (label, relative path). Refuses absolute and home paths, `..`, `.`, backslashes,
    NUL and anything that is not a label followed by a clean relative path. Paths are never repaired."""
    hint = labels_hint(labels)
    if not isinstance(reference, str) or not reference:
        raise LocalFileError("A local file reference is empty.", next_step=hint)
    if "\x00" in reference:
        raise LocalFileError("A local file reference contains a NUL character.", next_step=hint)
    if reference.startswith(("/", "~", "\\", "./", ".\\", "../", "..\\")) or re.match(r"^[A-Za-z]:[\\/]", reference):
        raise LocalFileError(f"'{reference}' is a path; AI callers name local files by label, never by path.",
                             next_step=hint)
    label, sep, relative = reference.partition(":")
    if not sep:
        raise LocalFileError(f"'{reference}' is not a local file reference.", next_step=hint)
    if not LABEL_RE.fullmatch(label):
        raise LocalFileError(f"'{label}' is not a folder label.", next_step=hint)
    if not relative or "\\" in relative or relative.startswith("/"):
        raise LocalFileError(f"'{reference}' needs a relative path with '/' after the label.", next_step=hint)
    parts = relative.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise LocalFileError(f"'{reference}' contains an empty, '.' or '..' part; give the plain relative path.",
                             next_step=hint)
    return label, relative


def is_path(value: str) -> bool:
    """An absolute, home-relative, drive or dot-relative path (never accepted from an AI caller)."""
    value = value or ""
    return (value.startswith(("/", "~", "\\", "./", ".\\")) or bool(re.match(r"^[A-Za-z]:[\\/]", value))
            or ".." in re.split(r"[\\/]", value) or "\x00" in value)


def looks_like_reference(value: str) -> bool:
    """True for a file id or a `<label>:...` reference, False for a Hugging Face repo id (owner/name)."""
    return bool(FILE_ID_RE.fullmatch(value or "")) or ":" in (value or "")


# --- pins ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class LocalFilePin:
    """A resolved local file as a person's call saw it. Plain data (it travels with the build request)."""
    file_id: str
    ref: str                       # <label>:<relative path>
    label: str
    relative: str
    root: str                      # the label folder's real path
    root_id: Identity
    path: str                      # the file's real path
    identity: FileIdentity         # st_dev, st_ino, size, mtime_ns
    is_dir: bool
    state_dir: Optional[str] = None
    data_folders: Tuple[str, ...] = ()

    def _denied(self) -> List[Denied]:
        return denied_folders(Path(self.state_dir) if self.state_dir else None,
                              [Path(p) for p in self.data_folders])

    def verify(self) -> Path:
        """Resolves the reference again and refuses when the folder, the file or its confinement changed."""
        root = Path(self.root)
        if _identity(root) != tuple(self.root_id):
            raise LocalFileError(f"The folder '{self.label}' changed since {self.ref} was chosen; nothing was run.",
                                 code="invalid_value", next_step="choose the file again.")
        try:
            resolved = (root / self.relative).resolve(strict=True)
        except (OSError, RuntimeError):
            raise LocalFileError(f"{self.ref} no longer exists; nothing was run.", code="unknown_id",
                                 next_step="choose the file again.") from None
        _confine(resolved, root, self._denied(), self.ref)
        if resolved.is_dir() != self.is_dir or _current_identity(resolved, self.is_dir) != tuple(self.identity):
            raise LocalFileError(f"{self.ref} was replaced or changed since it was chosen; nothing was run.",
                                 code="unknown_id", next_step="choose the file again.")
        return resolved


def _current_identity(path: Path, is_dir: bool) -> FileIdentity:
    dev, ino, size, mtime = _file_identity(path)
    return (dev, ino, 0, 0) if is_dir else (dev, ino, size, mtime)


def _confine(resolved: Path, root: Path, denied: Sequence[Denied], ref: str) -> None:
    """The checks every resolved file passes: inside its label folder, outside every denied folder, a regular file
    with one link or a folder strictly below the label root."""
    root_id = _identity(root)
    ids = _ancestor_ids(resolved)
    if root_id is None or root_id not in ids:
        raise LocalFileError(f"{ref} leads outside its folder (through a link); it cannot be used.",
                             next_step="copy the file into the folder instead of linking to it.")
    entry = denied_reason(resolved, denied, root)
    if entry is not None:
        raise LocalFileError(f"{ref} cannot be used: {entry.reason}.",
                             code="permission_denied",
                             next_step="copy the model into the models folder, or download it with the framework.")
    st = _stat(resolved)
    if stat.S_ISREG(st.st_mode):
        if st.st_nlink > 1:
            raise LocalFileError(f"{ref} has more than one hard link, so it could be any other file.",
                                 next_step="copy the file instead of linking it.")
    elif stat.S_ISDIR(st.st_mode):
        if _identity(resolved) == root_id:
            raise LocalFileError(f"{ref} is the folder '{ref.partition(':')[0]}' itself; a folder is used only "
                                 "strictly below its label folder.",
                                 next_step="put the model (or the dataset with its files) in its own subfolder.")
        inner = contained_denied(resolved, denied)
        if inner is not None:
            raise LocalFileError(f"{ref} contains a folder that is never shared: {inner.reason}.",
                                 code="permission_denied", next_step="use a subfolder that holds only the model.")
    else:
        raise LocalFileError(f"{ref} is neither a regular file nor a folder.", next_step="name a file or folder.")


def folder_entries(folder: Path) -> Tuple[List[Path], bool]:
    """Every entry below `folder` (not following links) and whether any of them is a link. Refuses files with more
    than one hard link and folders with too many entries."""
    entries: List[Path] = []
    has_links = False
    for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            entries.append(path)
            if len(entries) > MAX_FOLDER_ENTRIES:
                raise LocalFileError(f"The folder {folder.name} has more than {MAX_FOLDER_ENTRIES} entries.",
                                     next_step="use a folder that holds only the model or the dataset.")
            st = os.lstat(path)
            rel = path.relative_to(folder).as_posix()
            if stat.S_ISLNK(st.st_mode):
                has_links = True
            elif stat.S_ISREG(st.st_mode):
                if st.st_nlink > 1:
                    raise LocalFileError(f"The folder {folder.name} contains a file with more than one hard link "
                                         f"({rel}), so it could be any other file.",
                                         next_step="copy that file instead of linking it.")
            elif not stat.S_ISDIR(st.st_mode):
                # A socket (a Docker socket), a device or a pipe would hand the build's code what is behind it.
                raise LocalFileError(f"The folder {folder.name} contains an entry that is neither a regular file nor "
                                     f"a folder ({rel}).", next_step="use a folder that holds only the model or the "
                                                                     "dataset.")
    return entries, has_links


def mount_plan(pin: LocalFilePin, inner: str) -> Tuple[str, Dict[str, Dict[str, str]]]:
    """Docker volumes that make exactly the pinned file or folder visible at `inner` (read-only), never its parent.
    A folder of links (a Hugging Face cache snapshot) is bound file by file, each link resolved and confined."""
    path = pin.verify()
    if not pin.is_dir:
        target = f"{inner}/{path.name}"
        return target, {str(path): {"bind": target, "mode": "ro"}}
    entries, has_links = folder_entries(path)
    if not has_links:
        return inner, {str(path): {"bind": inner, "mode": "ro"}}
    root = Path(pin.root)
    denied = pin._denied()
    binds: Dict[str, Dict[str, str]] = {}
    for entry in entries:
        rel = entry.relative_to(path).as_posix()
        if entry.is_symlink() and entry.is_dir():
            raise LocalFileError(f"{pin.ref} contains a link to a folder ({rel}); folder links are not followed.",
                                 next_step="copy the model into its own folder.")
        if entry.is_dir():
            continue
        try:
            target = entry.resolve(strict=True)
        except (OSError, RuntimeError):
            raise LocalFileError(f"{pin.ref} contains a broken link ({rel}).",
                                 next_step="download the model again or copy it into its own folder.") from None
        _confine(target, root, denied, f"{pin.ref}/{rel}")
        if str(target) in binds:
            raise LocalFileError(f"{pin.ref} contains two names for one file ({rel}).",
                                 next_step="copy the model into its own folder.")
        binds[str(target)] = {"bind": f"{inner}/{rel}", "mode": "ro"}
        if len(binds) > MAX_LINKED_BINDS:
            raise LocalFileError(f"{pin.ref} has more than {MAX_LINKED_BINDS} linked files.",
                                 next_step="copy the model into its own folder.")
    return inner, binds


def dataset_folder(pin: LocalFilePin) -> Path:
    """The folder of a dataset file that lists sample files relative to itself (RKNN): only when it lies strictly
    below the label root, holds no denied folder and no hard-linked file."""
    path = pin.verify()
    folder = path.parent
    if _identity(folder) == tuple(pin.root_id):
        raise LocalFileError(f"{pin.ref} lies directly in the folder '{pin.label}'; its sample files would expose "
                             "the whole folder.", next_step="put the sample list and its files in their own subfolder.")
    inner = contained_denied(folder, pin._denied())
    if inner is not None:
        raise LocalFileError(f"The folder of {pin.ref} contains a folder that is never shared: {inner.reason}.",
                             code="permission_denied", next_step="put the sample list and its files in their own "
                                                                 "subfolder.")
    folder_entries(folder)
    return folder


# --- folders the AI may use ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class AllowedFolder:
    label: str
    path: Path            # as configured or entered
    kind: str
    builtin: bool
    real_path: Optional[Path] = None
    problem: str = ""     # set when the folder cannot be used right now


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


UNSAFE_ROOTS = {
    "root": "The root folder cannot be shared with AI clients.",
    "home": "Your home folder itself cannot be shared with AI clients; add a subfolder.",
    "state": "This folder contains the framework's own per-user folder; add a subfolder.",
}


def unsafe_root(real: Path, state_dir: Optional[Path]) -> Optional[str]:
    """Why a folder can never be a label root, as a key of UNSAFE_ROOTS (the root folder, the home folder, a folder
    holding the per-user folder): it would reach every file the denied list does not name. None when it may be."""
    real_id = _identity(real)
    if real_id == _identity(Path(real.anchor or "/")) or real.parent == real:
        return "root"
    if real_id == _identity(Path(os.path.expanduser("~"))):
        return "home"
    try:
        if state_dir is not None and Path(state_dir).exists() and real_id in _ancestor_ids(Path(state_dir).resolve()):
            return "state"
    except (OSError, RuntimeError):
        return "state"
    return None


def _write_private_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class FolderStore:
    """The person's "folders the AI may use" (`ai_folders.json` in the per-user folder, mode 0600). Changed only
    by `llm-cli tools folders` (terminal required) and the GUI, never by a tool."""

    FILE_NAME = "ai_folders.json"

    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / self.FILE_NAME
        self.problem: Optional[str] = None

    def _lock(self):
        return file_lock(self.state_dir / ".ai_folders.lock", timeout=10)

    def load(self) -> List[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            folders = data.get("folders") if isinstance(data, dict) else None
            if not isinstance(folders, list):
                raise ValueError("no folder list")
            self.problem = None
            return [f for f in folders if isinstance(f, dict) and LABEL_RE.fullmatch(str(f.get("label", "")))]
        except FileNotFoundError:
            self.problem = None
            return []
        except (OSError, ValueError) as e:
            # Fail closed: an unreadable list means no added folder (the built-in ones still work).
            self.problem = f"{self.path} cannot be used ({str(e)[:200]}); no added folder is available."
            return []

    def _save(self, folders: List[dict]) -> None:
        _write_private_json(self.path, {"version": 1, "folders": folders})

    def check_new(self, path: Path, data_folders: Sequence[Path] = ()) -> Path:
        """The real path of a folder the person wants to add, or LocalFileError with the reason."""
        given = Path(os.path.expanduser(str(path)))
        if not given.is_absolute():
            given = Path.cwd() / given
        try:
            real = given.resolve(strict=True)
        except (OSError, RuntimeError):
            raise LocalFileError(f"{given} does not exist.") from None
        if not real.is_dir():
            raise LocalFileError(f"{given} is not a folder.")
        problem = unsafe_root(real, self.state_dir)
        if problem:
            raise LocalFileError(UNSAFE_ROOTS[problem])
        denied = denied_folders(self.state_dir, data_folders)
        entry = denied_reason(real, denied, real)
        if entry is not None:
            raise LocalFileError(f"{given} cannot be shared: {entry.reason}.")
        inner = contained_denied(real, denied)
        if inner is not None:
            raise LocalFileError(f"{given} contains a folder that is never shared ({inner.path}: {inner.reason}).")
        return real

    def add(self, path: Path, label: str, kind: str = "any", data_folders: Sequence[Path] = (),
            expected_real: Optional[Path] = None) -> dict:
        """Adds the folder. `expected_real` is the real path the person was shown and agreed to: when the folder
        resolves elsewhere now (a link changed while they were asked), nothing is added."""
        if not LABEL_RE.fullmatch(label or ""):
            raise LocalFileError(f"'{label}' is not a valid label (lowercase letters, digits, '-' and '_', starting "
                                 "with a letter, at most 32 characters).")
        if label in BUILTIN_LABELS:
            raise LocalFileError(f"'{label}' is the built-in label of the {label} folder setting.")
        if kind not in FOLDER_KINDS:
            raise LocalFileError(f"Kind must be one of {', '.join(FOLDER_KINDS)}.")
        real = self.check_new(path, data_folders)
        if expected_real is not None and (real != Path(expected_real) or _identity(real) != _identity(expected_real)):
            raise LocalFileError(f"{path} changed while you were asked (it now leads to {real}); nothing was added. "
                                 "Run the command again.")
        st = _stat(real)
        entry = {"label": label, "path": str(Path(os.path.expanduser(str(path))).absolute()), "real_path": str(real),
                 "dev": st.st_dev, "ino": st.st_ino, "kind": kind, "added": _now()}
        with self._lock():
            folders = [f for f in self.load() if f.get("label") != label]
            if self.problem:
                raise LocalFileError(self.problem)
            folders.append(entry)
            self._save(folders)
        return entry

    def remove(self, label: str) -> bool:
        with self._lock():
            folders = self.load()
            if self.problem:
                raise LocalFileError(self.problem)
            kept = [f for f in folders if f.get("label") != label]
            if len(kept) == len(folders):
                return False
            self._save(kept)
        return True


def record_folder_change(audit, actor: str, action: str, entry: dict) -> None:
    """Audits a change of the folders the AI may use (actor `cli` or `gui`); raises when it cannot be written, also
    when there is no audit log at all (what widens an AI's reach is recorded, or it does not happen)."""
    if audit is None:
        raise RuntimeError("the audit log is not available")
    audit.record(actor, "ai_folders", "framework", "person", f"folder_{action}",
                 arguments={"label": entry.get("label"), "path": entry.get("real_path") or entry.get("path")},
                 approved=True)


# --- the resolver -------------------------------------------------------------------------------------------

class LocalFiles:
    """Resolves local-file references of AI callers inside the folders they may use."""

    INDEX_FILE = "local_files.json"

    def __init__(self, state_dir: Optional[Path], builtin: Dict[str, Path]):
        self.state_dir = Path(state_dir) if state_dir is not None else None
        self.builtin = {label: Path(p) for label, p in builtin.items() if p is not None}
        self.store = FolderStore(self.state_dir) if self.state_dir is not None else None

    @classmethod
    def for_framework(cls, framework) -> "LocalFiles":
        config = getattr(framework, "config", None)
        get = getattr(config, "get", None)
        state_dir = getattr(config, "user_state_dir", None)
        if not isinstance(state_dir, (str, Path)):
            raise LocalFileError("Local files are not available here (no per-user folder).", code="not_supported")
        resolve = getattr(framework, "resolve_path", Path)
        models = resolve((get("models_dir", "models") if get else None) or "models")
        datasets = (get("datasets_dir", None) if get else None) or str(Path(str(models)).parent / "datasets")
        return cls(Path(state_dir), {"models": Path(models), "datasets": Path(resolve(datasets))})

    # folders

    def data_folders(self) -> List[Path]:
        """The built-in folders, used as exceptions when they lie inside the per-user folder."""
        found = []
        for path in self.builtin.values():
            try:
                found.append(path.resolve())
            except (OSError, RuntimeError):
                continue
        return found

    def folders(self) -> List[AllowedFolder]:
        """The labelled folders. A `problem` (a folder that cannot be used now) never names a path: it reaches AI
        callers in refusals, and the CLI and the GUI show the folder next to it."""
        result = []
        unsafe_text = {"root": "is the root folder", "home": "is the home folder",
                       "state": "contains the framework's own per-user folder"}
        for label, path in self.builtin.items():
            problem = "" if path.is_dir() else "the folder does not exist yet"
            real = path.resolve() if not problem else None
            unsafe = unsafe_root(real, self.state_dir) if real is not None else None
            if unsafe:  # the setting points at '/', the home folder or around the per-user folder
                problem = (f"the folder of the setting {label}_dir {unsafe_text[unsafe]} and cannot be shared with "
                           "AI clients; the user sets it to a subfolder")
            result.append(AllowedFolder(label, path, "model" if label == "models" else "dataset", True, real, problem))
        for entry in (self.store.load() if self.store is not None else []):
            path = Path(str(entry.get("path", "")))
            problem = ""
            real = None
            try:
                real = path.resolve(strict=True)
                if str(real) != str(entry.get("real_path")) or _identity(real) != (entry.get("dev"), entry.get("ino")):
                    problem = ("it changed since it was added (it now leads to another folder); the user confirms "
                               f"it again with 'llm-cli tools folders add <its folder> --label {entry['label']}'")
                elif unsafe_root(real, self.state_dir):  # e.g. the per-user folder moved into it since
                    problem = (f"the folder {unsafe_text[unsafe_root(real, self.state_dir)]} and cannot be shared "
                               "with AI clients any more")
            except (OSError, RuntimeError):
                problem = "it does not exist any more"
            result.append(AllowedFolder(str(entry["label"]), path, str(entry.get("kind", "any")), False, real,
                                        problem))
        return result

    def _folder(self, label: str) -> AllowedFolder:
        folders = {f.label: f for f in self.folders()}
        hint = labels_hint([f.label for f in folders.values() if not f.problem])
        folder = folders.get(label)
        if folder is None:
            raise LocalFileError(f"There is no folder labelled '{label}'.", next_step=hint)
        if folder.problem:
            raise LocalFileError(f"The folder '{label}' cannot be used: {folder.problem}.", next_step=hint)
        return folder

    def denied(self) -> List[Denied]:
        return denied_folders(self.state_dir, self.data_folders())

    # references

    def resolve(self, reference: str) -> LocalFilePin:
        """A file id or `<label>:<relative path>` -> a pinned, confined local file."""
        reference = (reference or "").strip()
        if FILE_ID_RE.fullmatch(reference):
            return self._resolve_id(reference)
        labels = [f.label for f in self.folders() if not f.problem]
        label, relative = split_reference(reference, labels)
        folder = self._folder(label)
        root = folder.real_path
        ref = f"{label}:{relative}"
        try:
            resolved = (root / relative).resolve(strict=True)
        except (OSError, RuntimeError):
            raise LocalFileError(f"{ref} does not exist.", next_step=labels_hint(labels)) from None
        _confine(resolved, root, self.denied(), ref)
        is_dir = resolved.is_dir()
        identity = _current_identity(resolved, is_dir)
        digest = hashlib.sha256(f"{ref}\0{identity}".encode("utf-8", "surrogatepass")).hexdigest()[:16]
        pin = LocalFilePin(f"lf_{digest}", ref, label, relative, str(root), _identity(root), str(resolved), identity,
                           is_dir, str(self.state_dir) if self.state_dir else None,
                           tuple(str(p) for p in self.data_folders()))
        self._remember(pin)
        return pin

    def ref_of(self, file_id: str) -> Optional[str]:
        """The `<label>:<relative path>` a file id was listed as (for a person's question), without resolving it;
        None for an unknown id."""
        entry = self._load_index().get(file_id) if FILE_ID_RE.fullmatch(file_id or "") else None
        ref = entry.get("ref") if isinstance(entry, dict) else None
        return ref if isinstance(ref, str) else None

    def label_for(self, path: str) -> Optional[str]:
        """A host path as `<label>:<relative path>` when it lies in a usable labelled folder (for showing a path an
        older manifest recorded; a display name only, never a decision about access); None otherwise."""
        try:
            real = Path(path).resolve()
        except (OSError, RuntimeError, ValueError):
            return None
        for folder in self.folders():
            if folder.problem or folder.real_path is None:
                continue
            try:
                relative = real.relative_to(folder.real_path)
            except ValueError:
                continue
            if relative.parts:
                return f"{folder.label}:{relative.as_posix()}"
        return None

    def _index_path(self) -> Optional[Path]:
        return self.state_dir / self.INDEX_FILE if self.state_dir is not None else None

    def _load_index(self) -> Dict[str, dict]:
        path = self._index_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path else {}
            files = data.get("files") if isinstance(data, dict) else None
            return files if isinstance(files, dict) else {}
        except (OSError, ValueError):
            return {}

    def _remember(self, pin: LocalFilePin) -> None:
        path = self._index_path()
        if path is None:
            return
        try:
            with file_lock(self.state_dir / ".local_files.lock", timeout=10):
                files = self._load_index()
                files[pin.file_id] = {"ref": pin.ref, "identity": list(pin.identity), "seen": _now()}
                if len(files) > MAX_INDEX_ENTRIES:
                    for old in sorted(files, key=lambda k: str(files[k].get("seen", "")))[:len(files) - MAX_INDEX_ENTRIES]:
                        files.pop(old, None)
                _write_private_json(path, {"version": 1, "files": files})
        except (OSError, TimeoutError) as e:  # the reference still works; only its id is not kept
            logger.warning(f"Could not record the local file id {pin.file_id}: {e}")

    def _resolve_id(self, file_id: str) -> LocalFilePin:
        entry = self._load_index().get(file_id)
        labels = [f.label for f in self.folders() if not f.problem]
        if not isinstance(entry, dict) or not isinstance(entry.get("ref"), str):
            raise LocalFileError(f"Unknown local file id '{file_id}'.", code="unknown_id",
                                 next_step="do not guess ids; " + labels_hint(labels))
        try:
            pin = self.resolve(entry["ref"])
        except LocalFileError as e:
            raise LocalFileError(f"The file behind '{file_id}' cannot be used any more ({e})", code="unknown_id",
                                 next_step=labels_hint(labels)) from None
        if pin.file_id != file_id:
            raise LocalFileError(f"The file behind '{file_id}' was moved, replaced or changed since it was listed.",
                                 code="unknown_id", next_step="list the files again or use " + entry["ref"] + ".")
        return pin


@dataclass(frozen=True)
class LocalInputs:
    """The pinned local inputs of one build that an AI caller started (plain data, travels with the request). Every
    AI caller's build carries one, also without pins: the build engine then never looks the model source up on disk
    (a Hugging Face repo id such as 'Documents/private' is downloaded, never mounted from the working directory)."""
    model: Optional[LocalFilePin] = None
    dataset: Optional[LocalFilePin] = None
