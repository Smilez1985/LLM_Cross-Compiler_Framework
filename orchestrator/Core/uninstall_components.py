#!/usr/bin/env python3
"""
What the uninstaller removes besides the installation's folder: the entries of the install manifests
(utils/install_manifest.py), and only those (decided by the maintainer on 2026-11-18).

What is not written down was not installed by the installer and is never removed: a Python or a 7-Zip that was there before
stays, because other programs may need it. What is written down is shown first. What only the framework uses (`own`) is asked
once for all, and every system program (`shared`) is asked on its own, with a warning and with No as the answer.

`python -m orchestrator.Core.uninstall_components list|uninstall [--dry-run] --machine FILE --person FILE` is what
scripts/Uninstall-LLM-Cross-Compiler-Framework.bat calls. It reads and writes UTF-8 itself, so a path with an umlaut is no
problem for a batch file. The calls into the system (`run`, `which`, `ask`) are parameters, so the tests need none.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional, Sequence

from orchestrator.utils import install_manifest as manifest
from orchestrator.utils.install_manifest import Entry

WINGET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,80}$")
SHARED_WARNING = ("Andere Programme koennen es benutzen. Wird es entfernt, laufen sie vielleicht nicht mehr. "
                  "Der Installer hat es nur installiert, weil es vorher nicht da war.")


def _inside(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except (ValueError, OSError):
        return False


def remove_entry(entry: Entry, manifest_file: Path, run: Callable = subprocess.run, which: Callable = shutil.which) -> str:
    """Removes what one entry names and says in one plain line what happened. Nothing outside what the manifest names."""
    if entry.kind == "winget":
        if not WINGET_ID.match(entry.ref):
            return "uebersprungen: der Name im Manifest ist ungueltig."
        exe = which("winget")
        if exe is None:
            return f"nicht entfernt: winget fehlt. Selbst: winget uninstall -e --id {entry.ref}"
        done = run([exe, "uninstall", "-e", "--id", entry.ref], capture_output=True, text=True)
        return "entfernt." if done.returncode == 0 else f"nicht entfernt (winget Code {done.returncode}). Selbst: winget uninstall -e --id {entry.ref}"
    if entry.kind == "searxng":
        from orchestrator.Core import searxng_engine
        notes = searxng_engine.remove_files(manifest_file.parent)
        return " ".join(notes)
    if entry.kind == "folder":
        folder = Path(entry.ref)
        if not folder.is_absolute() or not _inside(folder, manifest_file.parent) or folder.resolve() == manifest_file.parent.resolve():
            return "uebersprungen: der Ordner liegt nicht unter dem Ordner dieses Manifests."
        shutil.rmtree(folder, ignore_errors=True)
        return "entfernt."
    return f"nicht von hier entfernt: {entry.kind} {entry.ref} (mit der Paketverwaltung)."


def uninstall(machine: Optional[Path], person: Optional[Path], ask: Callable[[str], str] = input,
              say: Callable[[str], None] = print, run: Callable = subprocess.run, which: Callable = shutil.which,
              dry_run: bool = False) -> int:
    """The uninstaller's questions and removals. Own entries are asked once for all; every shared entry is asked on its own
    with its warning. Anything but a yes leaves it. With `dry_run` it only shows what it would ask about. Returns how many
    entries were removed."""
    items = [(e, p) for p in (person, machine) if p is not None for e in manifest.read(p)]
    if not items:
        say("Der Installer hat nichts aufgeschrieben, was zusaetzlich entfernt werden koennte.")
        return 0
    say("Das hat der Installer selbst installiert (nur was vorher nicht da war):")
    for entry, _ in items:
        say(f"  - {manifest.describe(entry)}" + ("   [wird vielleicht von anderen Programmen benutzt]" if entry.scope == "shared" else ""))
    removed = 0
    if dry_run:
        say("(Nur gezeigt: nichts wird entfernt.)")
        return 0

    def yes(question: str) -> bool:
        try:
            return ask(question).strip().lower() in ("j", "ja", "y", "yes")
        except EOFError:
            return False

    own = [(e, p) for e, p in items if e.scope == "own"]
    if own and yes(f"{len(own)} Teil(e), die nur das Framework nutzt, entfernen? [j/N]: "):
        for entry, path in own:
            say(f"  {entry.title}: {remove_entry(entry, path, run, which)}")
            manifest.forget(path, entry.id)
            removed += 1
    for entry, path in [(e, p) for e, p in items if e.scope == "shared"]:
        say(f"  {entry.title}: {SHARED_WARNING}")
        if yes(f"  {entry.title} trotzdem entfernen? [j/N]: "):
            say(f"  {entry.title}: {remove_entry(entry, path, run, which)}")
            manifest.forget(path, entry.id)
            removed += 1
        else:
            say(f"  {entry.title}: bleibt.")
    return removed


def main(argv: Sequence[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="uninstall_components", description="what the installer installed itself")
    parser.add_argument("command", choices=("list", "uninstall"))
    parser.add_argument("--machine", help="the installation's manifest (install-manifest.json)")
    parser.add_argument("--person", help="the person's manifest (install-manifest.json in the state folder)")
    parser.add_argument("--dry-run", action="store_true", help="show what would be asked about; remove nothing")
    args = parser.parse_args(list(argv))
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    machine, person = (Path(a) if a else None for a in (args.machine, args.person))
    if args.command == "list":
        items = [e for p in (person, machine) if p is not None for e in manifest.read(p)]
        for entry in items:
            print(f"{entry.id}\t{entry.scope}\t{entry.kind}\t{entry.ref}\t{entry.title}")
        return 0
    uninstall(machine, person, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
__all__ = ["Entry", "FILE_NAME", "forget", "machine_path", "parse", "person_path", "read", "record", "remove_entry",
           "uninstall"]


__all__ = ["SHARED_WARNING", "main", "remove_entry", "uninstall"]
