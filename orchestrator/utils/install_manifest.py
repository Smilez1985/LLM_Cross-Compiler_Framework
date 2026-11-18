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
  installed with winget (Python, 7-Zip, a container engine), and by `install.sh` for the packages it installed with the
  package manager of the Linux family (one `kind` per manager: apt, dnf, zypper, pacman, apk). `install.sh` records a
  package only when it was not installed before the call and is installed after it, so a package that was there before is
  never written down, also not when another package of the same set was missing. `python -m orchestrator.utils.install_manifest
  record` is how `install.sh` writes: it merges the package names into an entry of the same component and keeps every other
  entry as it is.
A component is `{"scope": "own"|"shared", "kind": "winget"|"searxng"|"folder"|"apt"|"dnf"|"zypper"|"pacman"|"apk", "ref": ...,
"title": ...}`; for a package manager `ref` is one or more package names separated by a space.

What the uninstaller does with these entries (the questions and the removals) is orchestrator/Core/uninstall_components.py.
"""

import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

FILE_NAME = "install-manifest.json"
SCOPES = ("own", "shared")
PACKAGE_KINDS = ("apt", "dnf", "zypper", "pacman", "apk")
KINDS = ("winget", "searxng", "folder", *PACKAGE_KINDS)
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
PACKAGE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+._:-]{0,100}$")


@dataclass(frozen=True)
class Entry:
    id: str
    scope: str       # own: only the framework uses it; shared: another program may use it too
    kind: str        # winget, searxng, folder, or the package manager (apt, dnf, zypper, pacman, apk)
    ref: str         # the winget id, the folder, the package name(s) separated by a space
    title: str       # what the person reads


def person_path(state_dir: object) -> Path:
    return Path(str(state_dir)) / FILE_NAME


def machine_path(install_dir: object) -> Path:
    return Path(str(install_dir)) / FILE_NAME


def package_names(entry: Entry) -> List[str]:
    """The package names of an entry of a package manager ([] for another kind or when one name is not a package name)."""
    if entry.kind not in PACKAGE_KINDS:
        return []
    names = entry.ref.split()
    return names if names and all(PACKAGE_NAME.match(n) for n in names) else []


def _valid(entry: Entry) -> bool:
    if not (ID_PATTERN.match(entry.id) and entry.scope in SCOPES and entry.kind in KINDS and entry.ref and entry.title):
        return False
    return entry.kind not in PACKAGE_KINDS or bool(package_names(entry))


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


def record_merge(path: object, entry: Entry) -> bool:
    """Like `record`, but the package names of an entry of the same id and kind that is there already are kept and the new
    ones are added (a second run that installs one more package of a component). False when the file cannot be written."""
    if not _valid(entry):
        raise ValueError(f"Not a manifest entry: {entry}")
    old = _raw(Path(str(path))).get(entry.id)
    if isinstance(old, dict) and old.get("kind") == entry.kind and entry.kind in PACKAGE_KINDS:
        names = str(old.get("ref", "")).split()
        for name in entry.ref.split():
            if name not in names:
                names.append(name)
        entry = Entry(entry.id, entry.scope, entry.kind, " ".join(names), entry.title)
        if not _valid(entry):
            raise ValueError(f"Not a manifest entry: {entry}")
    return record(path, entry)


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
    if entry.kind in PACKAGE_KINDS:
        where = ("ein Paket" if len(entry.ref.split()) == 1 else "Pakete") + f" ({entry.kind}: {entry.ref})"
    else:
        where = {"winget": "ein Programm (winget: " + entry.ref + ")", "searxng": "die Suchmaschine im eigenen Ordner",
                 "folder": "ein Ordner (" + entry.ref + ")"}[entry.kind]
    return f"{entry.title}: {where}"


def main(argv: Optional[Sequence[str]] = None) -> int:
    """`record`: what install.sh calls to write a package component down (see the module text). Exit code 0 when it was
    written, 1 when the file could not be written, 2 for a wrong call. It reads nothing from the system."""
    import argparse
    parser = argparse.ArgumentParser(prog="install_manifest", description="write a component into an install manifest")
    parser.add_argument("command", choices=("record",))
    parser.add_argument("--file", required=True, help="the manifest (install-manifest.json)")
    parser.add_argument("--id", required=True)
    parser.add_argument("--scope", default="shared", choices=SCOPES)
    parser.add_argument("--kind", required=True, choices=KINDS)
    parser.add_argument("--ref", required=True, help="the package names, separated by a space")
    parser.add_argument("--title", required=True)
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        entry = Entry(args.id, args.scope, args.kind, args.ref, args.title)
        return 0 if record_merge(args.file, entry) else 1
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["Entry", "FILE_NAME", "KINDS", "PACKAGE_KINDS", "forget", "machine_path", "main", "package_names", "parse",
           "person_path", "read", "record", "record_merge", "describe"]
