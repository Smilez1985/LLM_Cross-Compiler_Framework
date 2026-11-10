#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Taking over what an earlier version wrote into the installation folder

An installed copy is shared by everyone who uses llmcf on this computer and is never written to at runtime: settings,
builds, caches, models, logs, the person's own targets and the community packs live in the person's own folders
(ConfigManager.default_data_root, the per-user folder ~/.llm-framework or LLM_FRAMEWORK_HOME). Earlier versions wrote
them into the installation. On the first start after the change they are offered for moving into the person's folders:

- Nothing is moved without the person's yes. What belongs to another account of this computer, or whose owner cannot
  be told (Windows), is asked about on its own: it may be someone else's.
- Nothing is deleted. Every item is copied first; the original is removed only after the copy is complete, and stays
  where it is when the installation cannot be written (it is read-only now: an administrator may remove it).
- Nothing is overwritten. When the person's folder already holds an item of that name, the installation's copy goes to
  `from-installation/` in the per-user folder instead, for the person to compare.
- The answer is kept (installation_takeover.json in the per-user folder): declined items are not offered again at
  start; `llm-cli config take-over` lists and moves them at any time.

A developer checkout (a git checkout the person can write to) keeps its data inside, as always: nothing is offered.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, List, Optional

from orchestrator.utils.logging import get_logger

RECORD = "installation_takeover.json"
CONFLICTS = "from-installation"
INSTALLER_MANIFEST = ".lcf-installed-files"   # install.sh: the files it copied (NUL-separated, relative)

# configs/ entries a running framework wrote (the rest of configs/ is shipped): the settings, the old credential vault
# and per-user files of early versions, and the folders ConfigManager made.
CONFIG_FILES = ("config.yml", "framework.yml", "secrets.store", "chat_history.json", "known_hosts")
CONFIG_FOLDERS = ("secrets", "audit", "compliance", "backups", "environments", "templates")
# data folder in the installation -> the setting that names its new place
DATA_FOLDERS = (("output", "build_dir"), ("cache", "cache_dir"), ("models", "models_dir"),
                ("datasets", "datasets_dir"), ("logs", "logs_dir"))
# Target folders the framework ships (besides the template); install.sh's manifest names them exactly when it exists.
SHIPPED_TARGETS = frozenset({"_template", "ARM64", "Rockchip"})

YOU, OTHER, UNKNOWN = "you", "another account", "unknown"


@dataclass(frozen=True)
class LeftItem:
    """One thing an earlier version wrote into the installation, and where it goes."""
    source: Path        # in the installation
    destination: Path   # in the person's folders
    relative: str       # its path in the installation, with '/' (what the person reads)
    owner: str          # YOU, OTHER or UNKNOWN

    @property
    def mine(self) -> bool:
        return self.owner == YOU


@dataclass
class TakeOverReport:
    moved: List[str] = field(default_factory=list)       # copied, and the original removed
    copied: List[str] = field(default_factory=list)      # copied; the original stays (installation not writable)
    renamed: List[str] = field(default_factory=list)     # went to from-installation/ (the name was taken)
    failed: List[str] = field(default_factory=list)      # "<item>: <reason>": nothing of it changed

    def lines(self) -> List[str]:
        out = [f"moved: {r}" for r in self.moved]
        out += [f"copied (the original stays in the installation, which this account cannot change): {r}"
                for r in self.copied]
        out += [f"your folder already had it, so the installation's copy is in {CONFLICTS}/: {r}"
                for r in self.renamed]
        out += [f"not moved: {r}" for r in self.failed]
        return out


def installation_root() -> Path:
    from orchestrator.Core.config_manager import ConfigManager
    return ConfigManager.installation_config_dir().parent


def _owner(path: Path) -> str:
    """Who owns `path`: this account, another one, or UNKNOWN where the system does not tell (Windows)."""
    if os.name != "posix":
        return UNKNOWN
    try:
        uid = path.lstat().st_uid
    except OSError:
        return UNKNOWN
    return YOU if uid == os.geteuid() else OTHER


def _has_content(path: Path) -> bool:
    """A file, or a folder with something in it (ConfigManager's empty folders are nothing to take over)."""
    if path.is_symlink():
        return False
    if path.is_file():
        return True
    if not path.is_dir():
        return False
    try:
        return any(p for p in path.rglob("*") if p.is_file() and p.name != ".gitkeep")
    except OSError:
        return False


def _shipped_targets(root: Path) -> frozenset:
    """The target folders the installation ships: from install.sh's manifest of copied files when there is one, else
    the framework's own list."""
    manifest = root / INSTALLER_MANIFEST
    try:
        names = {entry.split("/")[1] for entry in manifest.read_text(encoding="utf-8").split("\0")
                 if entry.startswith("targets/") and entry.count("/") >= 2}
    except (OSError, UnicodeDecodeError):
        names = set()
    return frozenset(names | SHIPPED_TARGETS) if names else SHIPPED_TARGETS


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def found(config: Any, root: Optional[Path] = None) -> List[LeftItem]:
    """Everything an earlier version wrote into the installation and its new place. Empty for a developer checkout
    (its data stays inside) and when the place it would go to is the installation itself (a setting points there)."""
    from orchestrator.Core.config_manager import ConfigManager
    from orchestrator.Core.framework import resolve_install_path
    from orchestrator.Core.target_manager import own_targets_dir
    if root is None:
        if ConfigManager.developer_checkout():
            return []
        root = installation_root()
    root = Path(root)
    state = ConfigManager.user_state_path()
    config_dir = Path(getattr(config, "config_dir", None) or state)

    def resolve(value: Any) -> Path:
        return resolve_install_path(value, root)

    def setting(key: str, default: str) -> Path:
        get = getattr(config, "get", None)
        return resolve((get(key, None) if callable(get) else None) or default)

    pairs: List[tuple] = []
    configs = root / "configs"
    for name in CONFIG_FILES + CONFIG_FOLDERS:
        pairs.append((configs / name, (state if name == "secrets.store" else config_dir) / name))
    for name, key in DATA_FOLDERS:
        pairs.append((root / name, setting(key, str(state / name))))
    community = root / "community"
    if community.is_dir():
        for entry in sorted(community.iterdir()):
            pairs.append((entry, state / "community" / entry.name))
    own_targets = own_targets_dir(config, resolve)
    targets = root / "targets"
    if targets.is_dir():
        shipped = _shipped_targets(root)
        for entry in sorted(targets.iterdir()):
            if entry.name not in shipped and not entry.name.startswith("."):
                pairs.append((entry, own_targets / entry.name))

    items = []
    for source, destination in pairs:
        if not _has_content(source) or _same(source, destination) or _inside(destination, root):
            continue
        items.append(LeftItem(source, destination, source.relative_to(root).as_posix(), _owner(source)))
    return items


def _inside(path: Path, folder: Path) -> bool:
    try:
        Path(os.path.abspath(path)).relative_to(os.path.abspath(folder))
        return True
    except ValueError:
        return False


# --- the person's answer -------------------------------------------------------------------------------------------

def _record_file() -> Path:
    from orchestrator.Core.config_manager import ConfigManager
    return ConfigManager.user_state_path() / RECORD


def _read_record() -> dict:
    try:
        data = json.loads(_record_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_record(data: dict) -> None:
    from orchestrator.utils.atomic import write_private
    write_private(_record_file(), json.dumps(data, indent=2, sort_keys=True) + "\n")


def pending(config: Any, root: Optional[Path] = None) -> List[LeftItem]:
    """found() without the items the person declined before: what is offered at start."""
    declined = set(_read_record().get("declined", []))
    return [i for i in found(config, root) if str(i.source) not in declined]


def decline(items: Iterable[LeftItem]) -> None:
    """The person said no: these items are not offered at start again (`llm-cli config take-over` still moves them)."""
    data = _read_record()
    data["declined"] = sorted(set(data.get("declined", [])) | {str(i.source) for i in items})
    _write_record(data)


def offered_once() -> bool:
    """True the first time it is called for this person (and records it): the CLI names the take-over once."""
    data = _read_record()
    if data.get("notified"):
        return False
    data["notified"] = True
    try:
        _write_record(data)
    except OSError:
        return False
    return True


# --- moving ----------------------------------------------------------------------------------------------------------

def _free_place(path: Path) -> Path:
    if not path.exists() and not path.is_symlink():
        return path
    n = 2
    while True:
        candidate = path.with_name(f"{path.name}-{n}")
        if not candidate.exists() and not candidate.is_symlink():
            return candidate
        n += 1


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def take_over(items: Iterable[LeftItem]) -> TakeOverReport:
    """Copies each item to its new place (from-installation/ in the per-user folder when that name is taken), then
    removes the original; an original that cannot be removed stays (reported as copied). Never overwrites, never
    deletes what was not copied completely."""
    from orchestrator.Core.config_manager import ConfigManager
    logger = get_logger("InstallMigration")
    items = list(items)
    report = TakeOverReport()
    conflicts = ConfigManager.user_state_path() / CONFLICTS
    for item in items:
        destination = item.destination
        renamed = destination.exists() or destination.is_symlink()
        if renamed:
            destination = _free_place(conflicts / item.relative)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            partial = destination.with_name(f".{destination.name}.taking-over")
            if partial.exists():   # left by an interrupted take-over (the original is still in the installation)
                _remove(partial)
            if item.source.is_dir():
                shutil.copytree(item.source, partial, symlinks=True)
            else:
                shutil.copy2(item.source, partial, follow_symlinks=False)
            os.replace(partial, destination)
        except OSError as e:
            report.failed.append(f"{item.relative}: {e.strerror or e.__class__.__name__}")
            logger.warning(f"Not taken over from the installation: {item.relative} ({e})")
            continue
        if renamed:
            report.renamed.append(item.relative)
        try:
            _remove(item.source)
            report.moved.append(item.relative)
        except OSError:
            report.copied.append(item.relative)
    data = _read_record()
    done = {str(i.source) for i in items}
    data["declined"] = sorted(set(data.get("declined", [])) - done)
    try:
        _write_record(data)
    except OSError:
        pass
    return report


def summary(items: List[LeftItem]) -> List[str]:
    """One line per item, for the question: its place in the installation, where it goes, and whose it is."""
    whose = {YOU: "", OTHER: "  (owned by another account)", UNKNOWN: "  (may belong to another account)"}
    return [f"{i.relative} -> {i.destination}{whose[i.owner]}" for i in items]


__all__ = ["LeftItem", "TakeOverReport", "decline", "found", "offered_once", "pending", "summary", "take_over"]
