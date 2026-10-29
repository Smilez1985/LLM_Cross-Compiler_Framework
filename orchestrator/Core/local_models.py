#!/usr/bin/env python3
"""
Models already on this computer, for a build in offline mode (DECISIONS round 25 Q1).

Offline means nobody can load from Hugging Face. A build the person starts in offline mode never tries to download its
model: a model named by its Hugging Face repository id is used from a copy on this computer (local_copy), without any
connection; when there is none, the build is not started and the person chooses a model already here or a local file
or folder (the CLI's `build start --model`, the Builder tab's choice window). An AI caller picks only among models
already here, by label, never by path; a file pick is the person's only.

What is "already here" (models_here), each with the label a person types or an AI names:

- the models folder and the folders the AI may use (tools/local_files.py LocalFiles.list, the one lister of local model
  files): the Hugging Face browser's downloads (models/hf/<owner>--<name>/<commit[:12]>/), the old huggingface_hub
  cache the hf chain lists and keeps usable (models/models--<owner>--<name>/snapshots/<commit>/), sideloaded files;
  label: the local-file reference `<label>:<relative path>`;
- the Builder's own copies of Hugging Face models (<cache>/models/hf/<owner>--<name>/<commit>/, what an earlier build
  downloaded); label: the repository id (its newest complete copy);
- the inputs of earlier builds (their build records), when they still exist and lie outside those folders; label: the
  path itself, shown to the person only (an AI never sees a host path).

A copy counts only when it is complete: the Builder's copy has the record its staging writes once every file arrived
(COMPLETE_MARKER, next to the folder, never mounted); a folder without it (a copy of an earlier version, a cache
snapshot) needs its config.json, weights, every shard its index names, and no unfinished part.

The offline check is the framework's one (utils/locality.offline_mode). Nothing here asks the network.
"""

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
REPO_RE = re.compile(r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")
COMPLETE_MARKER = ".{name}.complete.json"     # next to a staged commit folder: <cache>/models/hf/<o>--<n>/.<commit>...
WEIGHT_SUFFIXES = (".safetensors", ".bin", ".pt", ".pth")
UNFINISHED = (".part", ".incomplete", ".lock", ".tmp")
INDEX_FILES = ("model.safetensors.index.json", "pytorch_model.bin.index.json")
MAX_LISTED = 200
WHERE = ("models_folder", "folder", "build_cache", "earlier_build")


@dataclass(frozen=True)
class LocalModel:
    """One model already on this computer. `label`: what the person types (`build start --model`) or an AI names;
    `path` never reaches an AI (`for_ai`: an AI may name it at all: inside a folder the AI may use, or a repository's
    copy named by its id)."""
    label: str
    where: str
    kind: str                       # file or folder
    path: Path
    format: str = ""
    repo: str = ""
    commit: str = ""
    size_bytes: Optional[int] = None
    for_ai: bool = True

    def where_text(self, lang: str = "en") -> str:
        """Where it is, in plain words (bld.offline.where.*)."""
        from orchestrator.utils.localization import ui_text
        return ui_text(f"bld.offline.where.{self.where}", lang, commit=self.commit[:12])

    def shown(self, for_ai: bool = False, lang: str = "en") -> str:
        """One line of the list: the label, what it is, and (for the person) where it lies."""
        extra = ", ".join(x for x in (self.format, _size(self.size_bytes)) if x)
        line = f"{self.label} ({self.where_text(lang)}{', ' + extra if extra else ''})"
        return line if for_ai or self.where == "earlier_build" else f"{line}: {self.path}"


@dataclass(frozen=True)
class LocalCopy:
    """A complete copy of a Hugging Face repository on this computer."""
    repo: str
    commit: str
    path: Path
    where: str                       # build_cache, hf_download, old_cache


def _size(value: Optional[int]) -> str:
    if not value:
        return ""
    for unit, scale in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if value >= scale:
            return f"{value / scale:.1f} {unit}"
    return f"{value} B"


def owner_name(repo: str) -> str:
    owner, _, name = repo.partition("/")
    return f"{owner}--{name}"


def marker_of(folder: Path) -> Path:
    """The record that the Builder's copy in `folder` is complete (beside it, so a build never sees it)."""
    return folder.parent / COMPLETE_MARKER.format(name=folder.name)


def write_complete(folder: Path, repo: str, commit: str, files: Dict[str, Optional[int]]) -> None:
    """Records that every file of `repo` at `commit` arrived in `folder` (the Builder's staging, after the last file):
    the names and sizes, so a later offline build uses the copy only while they are all there."""
    marker = marker_of(folder)
    temporary = marker.with_name(marker.name + ".tmp")
    temporary.write_text(json.dumps({"repo": repo, "commit": commit, "files": files}, sort_keys=True),
                         encoding="utf-8")
    os.replace(temporary, marker)


def _marker_complete(folder: Path) -> Optional[bool]:
    """True/False when the folder has the Builder's record (every file there with its size), None without one."""
    try:
        data = json.loads(marker_of(folder).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return False
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict) or not files:
        return False
    for name, size in files.items():
        path = folder / str(name)
        try:
            if not path.is_file() or (isinstance(size, int) and path.stat().st_size != size):
                return False
        except OSError:
            return False
    return True


def model_folder_complete(folder: Path) -> bool:
    """A Hugging Face model folder that a build can convert as it is: config.json, weights, every shard the weights'
    index names, and no unfinished download. The Builder's own copy is judged by its record (write_complete)."""
    if not folder.is_dir():
        return False
    marked = _marker_complete(folder)
    if marked is not None:
        return marked
    try:
        names = [p for p in folder.rglob("*") if p.is_file() or p.is_symlink()]
    except OSError:
        return False
    if not (folder / "config.json").is_file():
        return False
    if any(p.name.endswith(UNFINISHED) for p in names):
        return False
    if not any(p.name.lower().endswith(WEIGHT_SUFFIXES) and p.is_file() for p in names):
        return False
    for index in INDEX_FILES:
        if (folder / index).is_file():
            try:
                shards = set((json.loads((folder / index).read_text(encoding="utf-8")).get("weight_map") or {})
                             .values())
            except (OSError, ValueError, AttributeError):
                return False
            if not all((folder / str(s)).is_file() for s in shards):
                return False
    return True


def _newest(folders: Iterable[Path]) -> Optional[Path]:
    best, when = None, -1.0
    for folder in folders:
        try:
            mtime = max(folder.stat().st_mtime, marker_of(folder).stat().st_mtime if marker_of(folder).exists()
                        else 0.0)
        except OSError:
            continue
        if mtime > when:
            best, when = folder, mtime
    return best


def local_copy(cache_dir: Path, models_dir: Path, repo: str, revision: Optional[str] = None) -> Optional[LocalCopy]:
    """A complete copy of Hugging Face repository `repo` on this computer, used instead of a download in offline mode:
    the Builder's own copy (<cache>/models/hf/<owner>--<name>/<commit>/), a model folder the Hugging Face browser
    downloaded (<models>/hf/<owner>--<name>/<commit[:12]>/) or one in the old huggingface_hub cache
    (<models>/models--<owner>--<name>/snapshots/<commit>/). `revision` a 40-character commit: only that commit's copy;
    a branch name or none: the newest complete copy. None when there is none."""
    if not REPO_RE.match(repo or "") or ".." in repo:
        return None
    commit = revision if revision and COMMIT_RE.match(revision) else None
    places = [("build_cache", Path(cache_dir) / "models" / "hf" / owner_name(repo), lambda f: f.name),
              ("hf_download", Path(models_dir) / "hf" / owner_name(repo), lambda f: f.name),
              ("old_cache", Path(models_dir) / f"models--{owner_name(repo)}" / "snapshots", lambda f: f.name)]
    for where, base, commit_of in places:
        if not base.is_dir():
            continue
        try:
            candidates = [f for f in base.iterdir() if f.is_dir() and not f.name.startswith(".")]
        except OSError:
            continue
        if commit:
            candidates = [f for f in candidates if commit.startswith(f.name) or f.name == commit]
        candidates = [f for f in candidates if model_folder_complete(f)]
        best = _newest(candidates)
        if best is not None:
            found = commit_of(best)
            return LocalCopy(repo, commit or (found if COMMIT_RE.match(found) else _full_commit(best) or found),
                             best, where)
    return None


def _full_commit(folder: Path) -> str:
    """The full commit a copy folder named by its first 12 characters belongs to, when its record says (else "")."""
    try:
        data = json.loads(marker_of(folder).read_text(encoding="utf-8"))
        value = str(data.get("commit") or "")
    except (OSError, ValueError, AttributeError):
        return ""
    return value if COMMIT_RE.match(value) else ""


def _build_cache_copies(cache_dir: Path) -> List[LocalModel]:
    base = Path(cache_dir) / "models" / "hf"
    found: List[LocalModel] = []
    try:
        repos = sorted(d for d in base.iterdir() if d.is_dir() and "--" in d.name)
    except OSError:
        return found
    for folder in repos:
        owner, _, name = folder.name.partition("--")
        repo = f"{owner}/{name}"
        if not REPO_RE.match(repo):
            continue
        copy = local_copy(cache_dir, Path("/nonexistent-models-folder"), repo)
        if copy is not None and copy.where == "build_cache":
            found.append(LocalModel(repo, "build_cache", "folder", copy.path, "huggingface", repo, copy.commit,
                                    _folder_size(copy.path)))
    return found


def _folder_size(folder: Path) -> Optional[int]:
    total = 0
    try:
        for current, _dirs, files in os.walk(folder):
            for name in files:
                try:
                    total += os.stat(Path(current) / name).st_size
                except OSError:
                    continue
    except OSError:
        return None
    return total


def _labelled(framework) -> List[LocalModel]:
    """The model files and folders in the models folder and the folders the AI may use (LocalFiles.list)."""
    try:
        from orchestrator.tools.local_files import LocalFiles
        files = LocalFiles.for_framework(framework)
        entries, folders, _total = files.list("model", page_size=MAX_LISTED)
    except Exception:  # noqa: BLE001 - no per-user folder, an unreadable folder: nothing listed from there
        return []
    builtin = {f.label for f in folders if getattr(f, "builtin", False)}
    found = []
    for entry in entries:
        ref = str(entry.get("ref") or "")
        try:
            pin = files.resolve(ref)
        except Exception:  # noqa: BLE001 - refused meanwhile (moved, a link out of the folder): not listed
            continue
        label = ref.split(":", 1)[0]
        found.append(LocalModel(ref, "models_folder" if label in builtin or label == "models" else "folder",
                                "folder" if pin.is_dir else "file", Path(pin.path), str(entry.get("format") or ""),
                                size_bytes=entry.get("size_bytes")))
    return found


def _earlier_inputs(cache_dir: Path, known: Sequence[Path]) -> List[LocalModel]:
    """The local inputs of earlier builds (their records) that still exist and are not listed otherwise."""
    from orchestrator.Core import build_records
    try:
        records = build_records.read_records(build_records.records_dir(cache_dir))
    except Exception:  # noqa: BLE001 - no records: nothing to list
        return []
    seen = {str(p) for p in known}
    found = []
    for record in sorted(records, key=lambda r: str(r.get("started") or ""), reverse=True):
        value = str(record.get("model") or "")
        if not value or REPO_RE.match(value) and not os.path.isabs(value):
            continue
        path = Path(value).expanduser()
        try:
            real = path.resolve()
            exists = real.exists()
        except (OSError, RuntimeError):
            continue
        if not exists or str(real) in seen:
            continue
        seen.add(str(real))
        found.append(LocalModel(str(real), "earlier_build", "folder" if real.is_dir() else "file", real,
                                for_ai=False))
    return found


def models_here(framework, *, for_ai: bool = False) -> List[LocalModel]:
    """Every model already on this computer (see the module text); for an AI only those it may name (no path ever
    reaches it). Reads the disk only."""
    from orchestrator.Core.build_plan import folder
    cache_dir = folder(framework, "cache_dir", "cache")
    labelled = _labelled(framework)
    found = labelled + _build_cache_copies(cache_dir)
    if not for_ai:
        found += _earlier_inputs(cache_dir, [m.path for m in labelled])
    return [m for m in found if m.for_ai or not for_ai][:MAX_LISTED]


def choice_for(framework, text: str) -> Optional[str]:
    """The model value a build runs with for what the person typed or picked: a listed label (a local-file reference
    gives its file or folder, a repository id stays an id, its copy is used), or an existing local path; None when it
    is neither."""
    text = (text or "").strip()
    if not text:
        return None
    for model in models_here(framework):
        if text == model.label:
            return model.repo if model.where == "build_cache" else str(model.path)
    path = Path(text).expanduser()
    return str(path.resolve()) if path.exists() else None


def not_here_text(repo: str, choices: Sequence[LocalModel], *, for_ai: bool = False, lang: str = "en") -> str:
    """Why the build does not start, and the models already here (labels; for the person with their place). An AI
    reads it in English, a person in their language."""
    from orchestrator.utils.localization import ui_text
    lang = "en" if for_ai else lang
    who = "ai" if for_ai else "person"
    head = ui_text("bld.offline.not_here", lang, repo=repo)
    if not choices:
        return f"{head} {ui_text(f'bld.offline.none.{who}', lang)}"
    lines = "\n".join(f"  {m.shown(for_ai, lang)}" for m in choices)
    return f"{head} {ui_text('bld.offline.here', lang)}\n{lines}\n{ui_text(f'bld.offline.use.{who}', lang)}"


__all__ = ["COMPLETE_MARKER", "LocalCopy", "LocalModel", "choice_for", "local_copy", "marker_of",
           "model_folder_complete", "models_here", "not_here_text", "write_complete"]
