#!/usr/bin/env python3
"""
The start entry of the GUI: on Linux a freedesktop desktop entry in the person's own application folder, on Windows a
Start-menu shortcut that starts pythonw.exe, so the window opens without a console (docs/DESIGN.md, GUI and the
person's path: starting without a terminal). The installers write it; `llm-cli launcher install|remove|status` and
File > Settings..., Update, "Start entry" create, repair, remove and show it.

Linux: `llm-cross-compiler-framework.desktop` in $XDG_DATA_HOME/applications (default ~/.local/share/applications).
Exec names the installation's Python in .venv and orchestrator/main.py by absolute, quoted paths with Path set to the
installation, so neither a shell, nor PATH, nor an activated environment is needed; TryExec names the same Python, so
a desktop hides the entry once the installation is gone. Every entry the framework writes carries
`X-LLMCF-Launcher=true`; only such a file is rewritten or removed, a file of the same name without it stays.

Windows: the installer puts the shortcut for all accounts into the common Start menu; this module writes and removes
only the person's own one (%APPDATA%, no administrator rights), recognised by its description. Shortcuts are read and
written with PowerShell's WScript.Shell, the values passed as environment variables, never pasted into the script.
"""

import ntpath
import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from orchestrator.utils.atomic import remove_file, write_file

ENTRY_ID = "llm-cross-compiler-framework"
DESKTOP_FILE = f"{ENTRY_ID}.desktop"
MARKER_KEY = "X-LLMCF-Launcher"
SHORTCUT_FILE = "LLM-Cross-Compiler-Framework.lnk"
SHORTCUT_DESCRIPTION = "LLM Cross-Compiler Framework"   # install.bat writes the same: it marks the framework's own
NAME = "LLM Cross-Compiler Framework"
ROOT = Path(__file__).resolve().parents[2]

# What a state means: missing (no entry), current (the framework's own, as this installation would write it),
# outdated (the framework's own, but for another installation or Python), foreign (a file of that name the
# framework did not write; never changed).
STATES = ("missing", "current", "outdated", "foreign")


@dataclass(frozen=True)
class Entry:
    """One start entry: where it is, its state, and whose it is ("person": this account's own; "everyone": the
    Windows Start menu of all accounts, written by the installer)."""
    path: str
    state: str
    scope: str = "person"


def system_name(system: Optional[str] = None) -> str:
    return system or platform.system()


def supported(system: Optional[str] = None) -> bool:
    """Whether this system has a start entry the framework writes (Linux and Windows; not macOS)."""
    return system_name(system) in ("Linux", "Windows")


# --- Linux: the desktop entry ---------------------------------------------------------------------------------------

def applications_dir(environ: Optional[Dict[str, str]] = None) -> Path:
    """The person's application folder: $XDG_DATA_HOME/applications when that is an absolute path (the
    specification ignores a relative one), else ~/.local/share/applications."""
    env = os.environ if environ is None else environ
    data = env.get("XDG_DATA_HOME", "")
    base = Path(data) if data and os.path.isabs(data) else Path(env.get("HOME") or Path.home()) / ".local" / "share"
    return base / "applications"


def desktop_path(environ: Optional[Dict[str, str]] = None) -> Path:
    return applications_dir(environ) / DESKTOP_FILE


def linux_python(root: Path = ROOT) -> str:
    """The installation's Python: .venv/bin/python of `root` when it is there, else this interpreter (never resolved:
    a link out of the environment would lose its packages)."""
    venv = Path(root) / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def _string_value(text: str) -> str:
    """A value of type string in a desktop entry: no control characters, the backslash escaped."""
    if any(ord(c) < 32 for c in text):
        raise ValueError("a path with a control character cannot go into a desktop entry")
    return text.replace("\\", "\\\\")


def exec_argument(arg: str) -> str:
    """One argument of the Exec key, always quoted: inside the quotes ", `, $ and \\ get a backslash, % is doubled
    (field codes), and the whole key is then a string value, which escapes each backslash once more."""
    quoted = "".join("\\" + c if c in '"`$\\' else c for c in arg).replace("%", "%%")
    return f'"{quoted}"'


def desktop_entry(root: Path = ROOT, python: Optional[str] = None) -> str:
    """The text of the framework's desktop entry for the installation at `root`."""
    root = Path(root)
    python = python or linux_python(root)
    command = " ".join(exec_argument(a) for a in (python, str(root / "orchestrator" / "main.py")))
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Version=1.5",
        f"Name={NAME}",
        "GenericName=Language model builder",
        "GenericName[de]=Sprachmodell-Builder",
        "Comment=Convert language models and build them for boards and devices",
        "Comment[de]=Sprachmodelle umwandeln und für Boards und Geräte bauen",
        f"Icon={_string_value(str(root / 'assets' / 'logo.png'))}",
        f"TryExec={_string_value(python)}",
        f"Exec={_string_value(command)}",
        f"Path={_string_value(str(root))}",
        "Terminal=false",
        "StartupNotify=true",
        "Categories=Development;",
        "Keywords=LLM;GGUF;llmcf;",
        f"{MARKER_KEY}=true",
    ]
    return "\n".join(lines) + "\n"


def _is_own_entry(text: str) -> bool:
    return any(line.strip() == f"{MARKER_KEY}=true" for line in text.splitlines())


def linux_status(root: Path = ROOT, environ: Optional[Dict[str, str]] = None) -> Entry:
    path = desktop_path(environ)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Entry(str(path), "missing")
    except (OSError, UnicodeDecodeError):
        return Entry(str(path), "foreign")
    if not _is_own_entry(text):
        return Entry(str(path), "foreign")
    return Entry(str(path), "current" if text == desktop_entry(root) else "outdated")


def linux_install(root: Path = ROOT, environ: Optional[Dict[str, str]] = None) -> str:
    """Writes (or repairs) the person's desktop entry: "written", "unchanged" or "foreign" (a file of that name the
    framework did not write stays as it is)."""
    entry = linux_status(root, environ)
    if entry.state == "foreign":
        return "foreign"
    if entry.state == "current":
        return "unchanged"
    path = Path(entry.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_file(path, desktop_entry(root))
    return "written"


def linux_remove(root: Path = ROOT, environ: Optional[Dict[str, str]] = None) -> str:
    """Removes the person's desktop entry when the framework wrote it: "removed", "missing" or "foreign"."""
    entry = linux_status(root, environ)
    if entry.state in ("missing", "foreign"):
        return entry.state
    remove_file(entry.path)
    return "removed"


# --- Windows: the Start-menu shortcut -------------------------------------------------------------------------------

Runner = Callable[..., subprocess.CompletedProcess]

READ_SCRIPT = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LCF_LNK); "
               "Write-Output $s.TargetPath; Write-Output $s.Arguments; Write-Output $s.Description")
WRITE_SCRIPT = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LCF_LNK); "
                "$s.TargetPath = $env:LCF_TARGET; $s.Arguments = $env:LCF_ARGS; "
                "$s.WorkingDirectory = $env:LCF_WORKDIR; $s.Description = $env:LCF_DESCRIPTION; "
                "if (Test-Path -LiteralPath $env:LCF_ICON) { $s.IconLocation = $env:LCF_ICON }; $s.Save()")


def start_menu_dirs(environ: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """The Programs folder of the Start menu: "everyone" (%ProgramData%, the installer's) and "person" (%APPDATA%)."""
    env = os.environ if environ is None else environ
    tail = ("Microsoft", "Windows", "Start Menu", "Programs")
    return {"everyone": ntpath.join(env.get("ProgramData") or "C:\\ProgramData", *tail),
            "person": ntpath.join(env.get("APPDATA") or ntpath.join(env.get("USERPROFILE") or "C:\\Users\\Default",
                                                                    "AppData", "Roaming"), *tail)}


def windows_shortcut(root: str) -> Dict[str, str]:
    """What the shortcut holds for the installation at `root`: pythonw.exe of its .venv, main.py, the folder, the
    icon and the description that marks it as the framework's."""
    return {"LCF_TARGET": ntpath.join(root, ".venv", "Scripts", "pythonw.exe"),
            "LCF_ARGS": '"' + ntpath.join(root, "orchestrator", "main.py") + '"',
            "LCF_WORKDIR": root, "LCF_ICON": ntpath.join(root, "assets", "LLM-Builder.ico"),
            "LCF_DESCRIPTION": SHORTCUT_DESCRIPTION}


def _powershell(script: str, values: Dict[str, str], run: Runner) -> subprocess.CompletedProcess:
    env = {**os.environ, **values}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    return run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], env=env, capture_output=True,
               text=True, timeout=60, check=False, creationflags=flags)


def _shortcut_state(path: str, root: str, run: Runner, exists: Callable[[str], bool]) -> str:
    if not exists(path):
        return "missing"
    try:
        result = _powershell(READ_SCRIPT, {"LCF_LNK": path}, run)
    except (OSError, subprocess.SubprocessError):
        return "foreign"
    lines = (result.stdout or "").splitlines() + ["", "", ""]
    target, args, description = (line.strip() for line in lines[:3])
    want = windows_shortcut(root)
    started_framework = target.lower().endswith(("\\pythonw.exe", "\\start_framework.bat"))
    if description != SHORTCUT_DESCRIPTION and not started_framework:
        return "foreign"
    same = target.lower() == want["LCF_TARGET"].lower() and args.lower() == want["LCF_ARGS"].lower()
    return "current" if same else "outdated"


def windows_status(root: Optional[str] = None, environ: Optional[Dict[str, str]] = None,
                   run: Runner = subprocess.run, exists: Callable[[str], bool] = os.path.exists) -> List[Entry]:
    root = root or str(ROOT)
    return [Entry(path, _shortcut_state(path, root, run, exists), scope)
            for scope, path in ((scope, ntpath.join(folder, SHORTCUT_FILE))
                                for scope, folder in start_menu_dirs(environ).items())]


def windows_install(root: Optional[str] = None, environ: Optional[Dict[str, str]] = None,
                    run: Runner = subprocess.run, exists: Callable[[str], bool] = os.path.exists) -> str:
    """The person's own Start-menu shortcut, unless the one for all accounts is right already: "covered",
    "unchanged", "written", "foreign" or "failed"."""
    root = root or str(ROOT)
    entries = {e.scope: e for e in windows_status(root, environ, run, exists)}
    if entries["everyone"].state == "current":
        return "covered"
    own = entries["person"]
    if own.state in ("foreign", "current"):
        return "foreign" if own.state == "foreign" else "unchanged"
    if os.name == "nt":
        os.makedirs(ntpath.dirname(own.path), exist_ok=True)
    try:
        result = _powershell(WRITE_SCRIPT, {"LCF_LNK": own.path, **windows_shortcut(root)}, run)
    except (OSError, subprocess.SubprocessError):
        return "failed"
    return "written" if result.returncode == 0 else "failed"


def windows_remove(root: Optional[str] = None, environ: Optional[Dict[str, str]] = None,
                   run: Runner = subprocess.run, exists: Callable[[str], bool] = os.path.exists) -> str:
    """Removes only the person's own shortcut the framework wrote: "removed", "missing" or "foreign" (the one for
    all accounts belongs to the installer and its uninstaller)."""
    own = next(e for e in windows_status(root, environ, run, exists) if e.scope == "person")
    if own.state in ("missing", "foreign"):
        return own.state
    remove_file(own.path)
    return "removed"


# --- one interface for both -----------------------------------------------------------------------------------------

def status(system: Optional[str] = None) -> List[Entry]:
    """The start entries of this system (empty where the framework writes none)."""
    system = system_name(system)
    if system == "Linux":
        return [linux_status()]
    if system == "Windows":
        return windows_status()
    return []


def install(system: Optional[str] = None) -> str:
    system = system_name(system)
    if system == "Linux":
        return linux_install()
    if system == "Windows":
        return windows_install()
    return "unsupported"


def remove(system: Optional[str] = None) -> str:
    system = system_name(system)
    if system == "Linux":
        return linux_remove()
    if system == "Windows":
        return windows_remove()
    return "unsupported"


def refresh(system: Optional[str] = None) -> bool:
    """At GUI start: rewrites the person's Linux entry when it is the framework's own but no longer what this
    installation would write; never creates a missing one (the person may have removed it). True when it wrote."""
    if system_name(system) != "Linux":
        return False
    try:
        return linux_status().state == "outdated" and linux_install() == "written"
    except (OSError, ValueError):
        return False


__all__ = ["DESKTOP_FILE", "ENTRY_ID", "Entry", "MARKER_KEY", "SHORTCUT_DESCRIPTION", "SHORTCUT_FILE", "STATES",
           "applications_dir", "desktop_entry", "desktop_path", "exec_argument", "install", "linux_install",
           "linux_remove", "linux_status", "refresh", "remove", "start_menu_dirs", "status", "supported",
           "windows_install", "windows_remove", "windows_shortcut", "windows_status"]
