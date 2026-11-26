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
- `log_folder`, `add_gui_log`, `add_framework_log`: the log folder (the setting logs_dir), gui.log in it (the GUI's
  readable log, rotated) and framework.log (one JSON object per line, from every process: the CLI, the GUI, the MCP
  server; rotated when a process starts), both private and through the same redaction as every log.
- `crash_details`: the text "Copy details" copies and a crash file holds: the traceback, llmcf's version, the Python
  version and the operating system, with secrets redacted and the home folder written as "~".
- `issue_link`, `telemetry_enabled`: with the setting enable_telemetry (off by default), the crash dialog's "Report on
  GitHub…" opens GitHub's form for a new issue filled with crash_details; nothing is sent until the person submits it.
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
FRAMEWORK_LOG = "framework.log"
FRAMEWORK_LOG_BYTES = 5 * 1024 * 1024
FRAMEWORK_LOG_BACKUPS = 3
FRAMEWORK_LOG_HANDLER = "llmcf-framework-log"
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


def _private_folder(folder: Path) -> None:
    """`folder` made when it is missing, private to its owner where the system has modes (an existing folder, maybe
    one the person chose for the setting logs_dir, keeps its modes); OSError when it cannot be made."""
    if not folder.is_dir():
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)


def _on_root(handler: logging.Handler, name: str, level: int, formatter: logging.Formatter) -> logging.Handler:
    """`handler` named, levelled and formatted on the root logger, with the redaction of every log handler
    (utils/logging.install_log_redaction)."""
    handler.set_name(name)
    handler.setLevel(level)
    handler.setFormatter(formatter)
    try:
        from orchestrator.utils.logging import install_log_redaction
        logging.getLogger().addHandler(handler)
        install_log_redaction()
    except Exception:  # noqa: BLE001 - without the redaction module the handler still gets the root's filters
        logging.getLogger().addHandler(handler)
    return handler


def _named_handler(name: str) -> Optional[logging.Handler]:
    return next((h for h in logging.getLogger().handlers if h.get_name() == name), None)


def framework_log_folder(config: Any = None) -> Path:
    """Where framework.log goes: the setting logs_dir when it is an absolute folder (the person chose it), else the
    per-user folder's logs (the config's user_state_dir, else fallback_log_folder). Never the working folder: a
    relative logs_dir (a developer checkout's default) resolves against it, and a start of the framework writes
    nothing there (DESIGN, the data folders are made by what uses them). Never raises; nothing is created."""
    try:
        value = str(config.get("logs_dir") or "").strip() if config is not None else ""
        if value and Path(os.path.expanduser(value)).is_absolute():
            return Path(os.path.expanduser(value))
        state = getattr(config, "user_state_dir", None) if config is not None else None
        if isinstance(state, (str, Path)) and str(state):
            return Path(state) / "logs"
    except Exception:  # noqa: BLE001 - a broken setting must not stop the log
        pass
    return fallback_log_folder()


def add_gui_log(folder: Path, level: int = logging.INFO) -> Optional[logging.Handler]:
    """gui.log in `folder` (created, private where the system has modes) on the root logger, rotated at 5 MB with
    three old files; the redaction filter of every log handler applies (utils/logging.install_log_redaction). None
    when the folder cannot be written: the GUI starts anyway."""
    try:
        _private_folder(folder)
        handler = logging.handlers.RotatingFileHandler(folder / GUI_LOG, maxBytes=GUI_LOG_BYTES,
                                                       backupCount=GUI_LOG_BACKUPS, encoding="utf-8", delay=True)
    except OSError:
        return None
    return _on_root(handler, GUI_LOG_HANDLER, level,
                    logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))


class _ProgramFilter(logging.Filter):
    """Marks each record of framework.log with the program that wrote it (cli, gui, mcp, ...: utils/program.py)
    and its process id, since several processes write to the one file."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            from orchestrator.utils.program import program
            record.program = program()
        except Exception:  # noqa: BLE001 - a record is never dropped for its label
            record.program = "framework"
        record.pid = os.getpid()
        return True


def _rotate_framework_log(path: Path) -> None:
    """framework.log moved to framework.log.1 (and on, the oldest of FRAMEWORK_LOG_BACKUPS dropped) when it is over
    FRAMEWORK_LOG_BYTES: when a process starts, under a lock of the folder, since several processes append to the
    one file and a rotation while they write would lose lines. A file another process holds open (Windows) or a lock
    that is not free is left: the next start rotates it."""
    try:
        if not path.is_file() or path.stat().st_size <= FRAMEWORK_LOG_BYTES:
            return
        from orchestrator.utils.file_lock import file_lock
        with file_lock(path.with_name(path.name + ".lock"), timeout=2):
            if path.stat().st_size <= FRAMEWORK_LOG_BYTES:
                return   # another process rotated it meanwhile
            for n in range(FRAMEWORK_LOG_BACKUPS - 1, 0, -1):
                older = path.with_name(f"{path.name}.{n}")
                if older.exists():
                    os.replace(older, path.with_name(f"{path.name}.{n + 1}"))
            os.replace(path, path.with_name(f"{path.name}.1"))
    except Exception:  # noqa: BLE001 - a log that cannot be rotated is still written to
        pass


def add_framework_log(folder: Path, level: int = logging.INFO) -> Optional[logging.Handler]:
    """framework.log in `folder` (created, private) on the root logger: one JSON object per line (the structured
    formatter of utils/logging, with the program and the process id), appended by every process (the CLI, the GUI,
    the MCP server) and rotated when a process starts (_rotate_framework_log); the redaction of every log applies.
    Once per process: a second call returns the handler there is. None when the folder cannot be written."""
    existing = _named_handler(FRAMEWORK_LOG_HANDLER)
    if existing is not None:
        return existing
    path = Path(folder) / FRAMEWORK_LOG
    try:
        _private_folder(Path(folder))
        _rotate_framework_log(path)
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600))   # private from its first byte
        if os.name == "posix":
            os.chmod(path, 0o600)
        handler = logging.FileHandler(path, mode="a", encoding="utf-8")
    except OSError:
        return None
    from orchestrator.utils.logging import LogFormat, StructuredFormatter
    handler.addFilter(_ProgramFilter())
    return _on_root(handler, FRAMEWORK_LOG_HANDLER, level, StructuredFormatter(LogFormat.JSON))


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


# GitHub's form for a new issue of the framework, and the most of the details it is filled with (an address longer than
# about 8000 characters is refused by browsers and by GitHub).
ISSUE_URL = "https://github.com/Smilez1985/LLM_Cross-Compiler_Framework/issues/new"
ISSUE_BODY_MAX = 6000


def telemetry_enabled(config: Any) -> bool:
    """The setting enable_telemetry (the person's opt-in to error reports as GitHub issues): off by default and when it
    cannot be read."""
    try:
        get = getattr(config, "get", None)
        return bool(get("enable_telemetry", False)) if callable(get) else False
    except Exception:  # noqa: BLE001 - an unreadable setting is off
        return False


def hide_home(text: str, marker: str = "~USER") -> str:
    """This computer's home folder as `marker` in every spelling a text can name it (orchestrator/security/paths:
    HOME, USERPROFILE and Path.home() with their real paths, either separator, a Windows path in any case, and doubled
    as Python quotes a Windows path), and the account's name after a path separator as USER (names of three
    characters or more, so a short one like "pi" stays elsewhere). Without that module the text is returned as it
    is (scrub already wrote the home folder as "~")."""
    if not text:
        return ""
    try:
        from orchestrator.security.paths import home_folders, replace_folder, undouble
        text = undouble(text)
        for home in home_folders():
            text = replace_folder(text, home, marker)
    except Exception:  # noqa: BLE001 - the crash may be in the very module that scrubs
        pass
    username = os.getenv("USERNAME") or os.getenv("USER")
    if username and len(username) > 2:
        text = text.replace(f"/{username}", "/USER").replace(f"\\{username}", "\\USER")
    return text


def issue_link(details: str) -> str:
    """GitHub's form for a new issue, filled with `details` (crash_details: already scrubbed, scrubbed again here
    and the home folder hidden in every spelling, hide_home): its
    first line as the title, the details in a code block, cut from the front to ISSUE_BODY_MAX so the end of the
    traceback stays. Nothing is sent: the person reads the form in the browser and submits it or not."""
    from urllib.parse import urlencode
    text = hide_home(scrub(details or "")).strip()
    title = (text.splitlines() or ["unexpected error"])[0][:120]
    if len(text) > ISSUE_BODY_MAX:
        text = "…\n" + text[-ISSUE_BODY_MAX:]
    body = "\n".join(["**What happened**", "", "(Please describe what you were doing.)", "",
                      "**Details** (from the crash dialog)", "", "```text", text, "```"])
    return f"{ISSUE_URL}?{urlencode({'title': f'[Bug]: {title}', 'body': body, 'labels': 'bug,automated-report'})}"


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
