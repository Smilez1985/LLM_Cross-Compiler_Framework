#!/usr/bin/env python3
"""
What the GUI needs to run without a terminal, without Qt (docs/DESIGN.md, GUI and the person's path: starting without
a terminal).

- `guard_streams`: under pythonw.exe (the Windows shortcut) and some desktop launchers Python's sys.stdout and
  sys.stderr are None, and anything that writes to them, asks them for a file number or hands them to a logging
  handler fails. A missing stream becomes a writer to the null device, before anything else runs.
- `windowless_children`: in a process without a console on Windows, a console program started without creation flags
  of its own would open a console window that flashes up; it gets CREATE_NO_WINDOW instead. A program that asks for a
  console of its own (CREATE_NEW_CONSOLE) keeps it.
- `log_folder`, `add_gui_log`: the log folder (the setting logs_dir) and gui.log in it, rotated, through the same
  redaction as every log.
- `crash_details`: the text "Copy details" copies and a crash file holds: the traceback, llmcf's version, the Python
  version and the operating system, with secrets redacted and the home folder written as "~".
- `write_crash_file`, `native_message`: a crash before Qt exists goes into crash-<UTC time>.txt in the log folder and,
  on Windows without a console, into a native message box.
"""

import io
import logging
import logging.handlers
import os
import platform
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

GUI_LOG = "gui.log"
GUI_LOG_BYTES = 5 * 1024 * 1024
GUI_LOG_BACKUPS = 3
GUI_LOG_HANDLER = "llmcf-gui-log"
CRASH_PREFIX = "crash-"
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010
WINDOWS = os.name == "nt"   # tests set it to try the Windows way of report_early_crash


# --- standard streams and console windows ---------------------------------------------------------------------------

def guard_streams() -> list:
    """Replaces a missing sys.stdout or sys.stderr (None without a console) with a writer to the null device, so
    print, Rich, logging handlers, faulthandler and fileno() keep working. Returns the names it replaced."""
    replaced = []
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, _NullWriter(open(os.devnull, "wb"), encoding="utf-8", write_through=True))
            replaced.append(name)
    return replaced


class _NullWriter(io.TextIOWrapper):
    """A writer to the null device that never claims to be a terminal: on Windows the device NUL answers isatty()
    with True, and Rich or a progress bar would then draw for a console that does not exist."""

    def isatty(self) -> bool:
        return False


def has_console() -> bool:
    """Whether this process was started with a console (always True outside Windows: there a terminal is no window
    that a child could open). Without one means started by pythonw.exe, which Python marks by giving the process no
    standard streams at all (sys.__stdout__ and sys.__stderr__ are None, whatever guard_streams put in their place).
    A process that merely has no console window, such as one started with pipes by a CI runner or a service, has its
    streams and counts as having a console: it must never get a message box that nobody can close."""
    if not WINDOWS:
        return True
    return sys.__stdout__ is not None or sys.__stderr__ is not None


def windowless_flags(creationflags: int) -> int:
    """The creation flags a child gets in a process without a console: unchanged when the caller set any (its own
    console, a new process group), else CREATE_NO_WINDOW."""
    return creationflags if creationflags else CREATE_NO_WINDOW


_PATCHED = False


def windowless_children(force: bool = False) -> bool:
    """On Windows without a console (pythonw.exe), every subprocess.Popen started without creation flags gets
    CREATE_NO_WINDOW (windowless_flags). Does nothing elsewhere or with a console; `force` is for tests. Returns
    whether the default is in place."""
    global _PATCHED
    if _PATCHED:
        return True
    if not force and (os.name != "nt" or has_console()):
        return False
    original = subprocess.Popen.__init__

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if len(args) < 14:   # args[13] is creationflags; given by position it is the caller's own choice
            kwargs["creationflags"] = windowless_flags(int(kwargs.get("creationflags") or 0))
        original(self, *args, **kwargs)

    __init__.__wrapped__ = original   # type: ignore[attr-defined]
    subprocess.Popen.__init__ = __init__   # type: ignore[method-assign]
    _PATCHED = True
    return True


# --- the log folder and gui.log -------------------------------------------------------------------------------------

def fallback_log_folder() -> Path:
    """The per-person log folder without reading any setting (LLM_FRAMEWORK_HOME, else ~/.llm-framework, then logs):
    for a crash so early that the settings cannot be read."""
    return Path(os.environ.get("LLM_FRAMEWORK_HOME") or Path.home() / ".llm-framework") / "logs"


def log_folder(config: Any = None) -> Path:
    """The log folder: the setting logs_dir of `config` (a ConfigManager; without one, fallback_log_folder) (a relative one, in a developer checkout, against the working folder like
    every relative data folder of the framework; the start entries start in the installation), else
    fallback_log_folder. Never raises; nothing is created."""
    if config is None:
        return fallback_log_folder()
    try:
        value = str(config.get("logs_dir") or "").strip()
        if value:
            folder = Path(os.path.expanduser(value))
            return folder if folder.is_absolute() else Path.cwd() / folder
    except Exception:  # noqa: BLE001 - a broken setting must not hide the crash it is asked for
        pass
    return fallback_log_folder()


def add_gui_log(folder: Path, level: int = logging.INFO) -> Optional[logging.Handler]:
    """gui.log in `folder` (created, private where the system has modes) on the root logger, rotated at 5 MB with
    three old files; the redaction filter of every log handler applies (utils/logging.install_log_redaction). None
    when the folder cannot be written: the GUI starts anyway."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(folder / GUI_LOG, maxBytes=GUI_LOG_BYTES,
                                                       backupCount=GUI_LOG_BACKUPS, encoding="utf-8", delay=True)
    except OSError:
        return None
    handler.set_name(GUI_LOG_HANDLER)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    try:
        from orchestrator.utils.logging import install_log_redaction
        logging.getLogger().addHandler(handler)
        install_log_redaction()
    except Exception:  # noqa: BLE001 - without the redaction module the handler still gets the root's filters
        logging.getLogger().addHandler(handler)
    return handler


# --- the details of a crash -----------------------------------------------------------------------------------------

def version() -> str:
    try:
        from orchestrator import __version__
        return str(__version__)
    except Exception:  # noqa: BLE001 - a broken package still has a crash to report
        return "unknown"


def scrub(text: str) -> str:
    """Secrets redacted and the home folder written as "~" (the error box's technical_details); without that module
    at least the home folder is shortened."""
    try:
        from orchestrator.utils.wording import technical_details
        return technical_details(text)
    except Exception:  # noqa: BLE001 - the crash may be in the very module that scrubs
        home = str(Path.home())
        return text.replace(home, "~") if home and home not in ("/", "\\") else text


def crash_details(error: BaseException, where: str = "") -> str:
    """The text of "Copy details" and of a crash file: what failed, the traceback, llmcf's version, the Python version
    and the operating system (no host name, no account name), scrubbed."""
    trace = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    started = "pythonw (no console)" if os.name == "nt" and not has_console() else "with a console"
    lines = [f"llmcf {version()} - unexpected error" + (f" in {where}" if where else ""),
             f"Python {platform.python_version()} ({platform.python_implementation()}), {started}",
             f"System: {platform.platform(terse=True)} ({platform.machine() or 'unknown machine'})",
             "", trace.rstrip()]
    return scrub("\n".join(lines)) + "\n"


def write_crash_file(details: str, folder: Optional[Path] = None,
                     now: Optional[Callable[[], datetime]] = None) -> Optional[Path]:
    """Writes `details` into crash-<UTC time>.txt in the log folder (made when missing); None when it cannot."""
    folder = folder or fallback_log_folder()
    stamp = (now or (lambda: datetime.now(timezone.utc)))().strftime("%Y%m%dT%H%M%SZ")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{CRASH_PREFIX}{stamp}.txt"
        number = 1
        while path.exists():
            number += 1
            path = folder / f"{CRASH_PREFIX}{stamp}-{number}.txt"
        path.write_text(details, encoding="utf-8")
        return path
    except OSError:
        return None


def native_message(title: str, text: str) -> bool:
    """A native message box on Windows (MessageBoxW, an error icon): the one way to say something when neither Qt nor
    a console is there. False elsewhere or when it cannot be shown."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)   # type: ignore[attr-defined]
        return True
    except (AttributeError, OSError):
        return False


def report_early_crash(error: BaseException, config: Any = None, message: Optional[Callable[[str, str], bool]] = None,
                       folder: Optional[Path] = None) -> Optional[Path]:
    """A crash before Qt exists: the details go into a crash file in the log folder, to stderr, and, on Windows
    without a console, into a native message box naming the file. Returns the file (None when none was written)."""
    details = crash_details(error, "the start of the window")
    path = write_crash_file(details, folder or log_folder(config))
    where = scrub(str(path)) if path else "(the crash file could not be written)"
    try:
        print(details, file=sys.stderr)
        print(f"Saved in {where}", file=sys.stderr)
    except (OSError, ValueError):
        pass
    if WINDOWS and not has_console():
        (message or native_message)("LLM Cross-Compiler Framework", early_text(scrub(str(error))[:500], where, config))
    return path


def early_text(reason: str, where: str, config: Any = None) -> str:
    """The native message box's text in the person's language (English when not even that can be read)."""
    try:
        from orchestrator.utils.localization import ui_text
        lang = str(config.get("language") or "en") if config is not None else "en"
        return ui_text("crash.early", lang if lang in ("en", "de") else "en", reason=reason, file=where)
    except Exception:  # noqa: BLE001 - the crash may be in the text tables themselves
        return f"The framework could not start: {reason}\n\nThe details are in {where}"


__all__ = ["CRASH_PREFIX", "GUI_LOG", "add_gui_log", "crash_details", "fallback_log_folder", "guard_streams",
           "has_console", "log_folder", "native_message", "report_early_crash", "scrub", "windowless_children",
           "windowless_flags", "write_crash_file"]
