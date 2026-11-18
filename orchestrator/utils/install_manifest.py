"""
The install manifest: what the framework installed itself, written down so that the uninstaller removes exactly that
(DESIGN, the installer's rows; decided by the maintainer on 2026-11-18, after the way rag4ll does it).

The rule is the maintainer's: an installer that installs "if not exist" writes down only what it installed because it was
not there. What was there before, a Python or a 7-Zip that another program may need, is never written down and therefore
never removed. Even what the installer installed itself may be used by other programs afterwards, so such a system program is
a `shared` entry: the uninstaller shows it and removes it only after its own yes, with a warning, and No is the default. What
only the framework uses (`own`: its search engine in the person's folder) is asked once for all.

The shape is rag4ll's: a JSON map from a component's name to its details, one file `install-manifest.json`, the component's
details with `registered_at`; PowerShell reads and writes JSON itself, so `install.bat` writes the same file as the framework.
- the person's manifest `<state folder>/install-manifest.json`: written by the framework itself when the person sets
  something up (the search engine);
- the installation's manifest `<installation folder>/install-manifest.json`: written by `install.bat` for the programs it
  installed with winget (Python, 7-Zip, a container engine).
A component is `{"scope": "own"|"shared", "kind": "winget"|"searxng"|"folder"|"apt", "ref": ..., "title": ...}`.

What the uninstaller does with these entries (the questions and the removals) is orchestrator/Core/uninstall_components.py.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List

FILE_NAME = "install-manifest.json"
SCOPES = ("own", "shared")
KINDS = ("winget", "searxng", "folder", "apt")
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class Entry:
    id: str
    scope: str       # own: only the framework uses it; shared: another program may use it too
    kind: str        # winget, searxng, folder, apt
    ref: str         # the winget id, the folder, the package name
    title: str       # what the person reads


def person_path(state_dir: object) -> Path:
    return Path(str(state_dir)) / FILE_NAME


def machine_path(install_dir: object) -> Path:
    return Path(str(install_dir)) / FILE_NAME


def _valid(entry: Entry) -> bool:
    return bool(ID_PATTERN.match(entry.id) and entry.scope in SCOPES and entry.kind in KINDS and entry.ref and entry.title)


def parse(text: str) -> List[Entry]:
    """The entries of a manifest. Anything that is not a component of the right shape is skipped, never an error: a
    manifest written by an older or damaged run still lists what can be read."""
    try:
        data = json.loads(text.lstrip("\ufeff") or "{}")
    except ValueError:
        return []
    found: List[Entry] = []
    for name, details in (data.items() if isinstance(data, dict) else []):
        if isinstance(details, dict):
            entry = Entry(str(name), str(details.get("scope", "")), str(details.get("kind", "")),
                          str(details.get("ref", "")), str(details.get("title", "")))
            if _valid(entry):
                found.append(entry)
    return found


def read(path: object) -> List[Entry]:
    try:
        return parse(Path(str(path)).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return []


def _raw(path: Path) -> Dict[str, dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig") or "{}")
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(path: Path, data: Dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def record(path: object, entry: Entry) -> bool:
    """Writes `entry` down (one per id: an earlier one of the same id is replaced; other components, also those another
    program wrote into the file, are kept as they are). False when the file cannot be written; the caller carries on: a
    missing entry only means the uninstaller leaves that item alone."""
    if not _valid(entry):
        raise ValueError(f"Not a manifest entry: {entry}")
    target = Path(str(path))
    data = _raw(target)
    data[entry.id] = {"scope": entry.scope, "kind": entry.kind, "ref": entry.ref, "title": entry.title,
                      "registered_at": datetime.now().isoformat(timespec="seconds")}
    try:
        _write(target, data)
    except OSError:
        return False
    return True


def forget(path: object, entry_id: str) -> bool:
    """Takes the component out (the thing was removed); True when there was one. An empty manifest is removed."""
    target = Path(str(path))
    data = _raw(target)
    if entry_id not in data:
        return False
    del data[entry_id]
    try:
        if data:
            _write(target, data)
        else:
            target.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def describe(entry: Entry) -> str:
    where = {"winget": "ein Programm (winget: " + entry.ref + ")", "searxng": "die Suchmaschine im eigenen Ordner",
             "folder": "ein Ordner (" + entry.ref + ")", "apt": "ein Paket (" + entry.ref + ")"}[entry.kind]
    return f"{entry.title}: {where}"


__all__ = ["Entry", "FILE_NAME", "forget", "machine_path", "parse", "person_path", "read", "record", "describe"]
