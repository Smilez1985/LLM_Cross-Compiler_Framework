#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - the framework as a stdio program another program starts

Two front ends are started by another program and speak a protocol over stdin/stdout: the MCP server (`llm-cli mcp
serve`, started by an AI app) and Ditto as an ACP agent (`llm-cli acp`, started by an editor). What they share lives
here, in the bottom layer, so neither adapter imports the other (tests/test_layering.py):

- launch_command(): how the other program starts the framework (this interpreter, -P, the installation on
  PYTHONPATH), for the copyable entries of both;
- shell_command(): a command line that survives copy and paste in a POSIX shell, cmd.exe and PowerShell;
- installation_root(): the folder that holds orchestrator/;
- no_programs_from_the_working_directory(): Windows does not run docker or git from the folder the program was
  started in;
- logging_to_stderr(): every log handler that writes to stdout writes to stderr (stdout belongs to the protocol);
- sigterm_ends_the_session(): SIGTERM interrupts the program like Ctrl+C, so a session's cleanup runs.

They were written for the MCP server (orchestrator/mcp_server.py), which keeps every name as a re-export.
Standard library only.
"""

import contextlib
import logging
import ntpath
import os
import posixpath
import shlex
import sys
import threading
import unicodedata
from pathlib import Path, PureWindowsPath
from typing import Any, Dict, Optional, Sequence

MCP_SERVE = ("mcp", "serve")   # the arguments after the CLI's options that start the MCP server over stdio
ACP_AGENT = ("acp",)           # ... and Ditto as an ACP agent


def launch_command(app_root: Path, windows: Optional[bool] = None,
                   command: Sequence[str] = MCP_SERVE) -> Dict[str, Any]:
    """How another program starts the framework over stdio (the current interpreter, so the venv is used): the MCP
    server by default, `command` for another front end (ACP_AGENT). -P keeps the folder the program starts it in off
    sys.path; the installation comes from PYTHONPATH. The POSIX variant follows POSIX path rules on every host: a path
    the caller made on Windows ('\\opt\\llm' from Path('/opt/llm')) is written with '/' again."""
    windows = (sys.platform == "win32") if windows is None else windows
    root = PureWindowsPath(app_root).as_posix() if not windows and isinstance(app_root, PureWindowsPath) \
        else str(app_root)
    separator = ";" if windows else ":"
    # A relative entry, or one that the separator splits, would be looked up in the working directory.
    if separator in root or not (ntpath if windows else posixpath).isabs(root):
        raise ValueError(f"The installation path {root!r} cannot be put on PYTHONPATH safely (it is relative or "
                         f"contains '{separator}'); install the framework to another folder.")
    return {"command": sys.executable, "args": ["-P", "-m", "orchestrator.cli", "-q", *command],
            "cwd": root, "env": {"PYTHONPATH": root}}


def installation_root() -> Path:
    """The framework's installation (the folder that holds orchestrator/): what the client entries put on
    PYTHONPATH, for the CLI and the GUI alike."""
    return Path(__file__).resolve().parents[2]


# cmd.exe and PowerShell both keep these literal inside double quotes (PowerShell reads smart single quotes as ').
_WINDOWS_QUOTED = frozenset("&|<>^'(),;@{}#‘’‚‛")
# No quoting protects these in both: % (cmd), ! (cmd with delayed expansion), $ and ` (PowerShell), double quotes.
_WINDOWS_UNSAFE = frozenset('%!$`"“”„')


def windows_argument(arg: str) -> str:
    if any(ch in _WINDOWS_UNSAFE or unicodedata.category(ch) == "Cc" for ch in arg):
        raise ValueError(f"{arg!r} contains a character that cmd.exe or PowerShell would change even in quotes.")
    if arg and not any(ch in _WINDOWS_QUOTED or ch.isspace() for ch in arg):
        return arg
    # C runtime rules: backslashes are literal except before a quote, so the closing quote needs them doubled.
    trailing = len(arg) - len(arg.rstrip("\\"))
    return '"' + arg + "\\" * trailing + '"'


def shell_command(argv, windows: Optional[bool] = None) -> str:
    """A command line that survives copy and paste: POSIX shell quoting, or on Windows double quotes that
    cmd.exe and PowerShell both keep literal. ValueError if an argument cannot be quoted safely for both."""
    windows = (sys.platform == "win32") if windows is None else windows
    return " ".join(windows_argument(a) for a in argv) if windows else shlex.join(argv)


ACP_AGENT_NAME = "llmcf"   # the entry's name in an editor's agent list


def acp_entries(app_root: Path, windows: Optional[bool] = None) -> Dict[str, str]:
    """How an editor adds Ditto as an ACP agent: "command" (the command line, for an editor that asks for one, such as
    Android Studio's or a JetBrains IDE's custom agent) and "json" (the `agent_servers` entry of Zed's settings.json and
    a JetBrains IDE's acp.json). The same launch as the MCP server's entries (this interpreter, -P, PYTHONPATH)."""
    import json
    launch = launch_command(app_root, windows, ACP_AGENT)
    entry = {"command": launch["command"], "args": launch["args"], "env": launch["env"]}
    try:
        command = shell_command([launch["command"], *launch["args"]], windows)
        windows_ = (sys.platform == "win32") if windows is None else windows
        prefix = (f"set PYTHONPATH={launch['env']['PYTHONPATH']} && " if windows_
                  else f"PYTHONPATH={shlex.quote(launch['env']['PYTHONPATH'])} ")
        command = prefix + command
    except ValueError:
        command = ("# Not shown: the command would contain a character that cmd.exe or PowerShell change even in "
                   "quotes. Use the JSON entry, or install Python and the framework to folders without it.")
    return {"command": command, "json": json.dumps({"agent_servers": {ACP_AGENT_NAME: entry}}, indent=2)}


def no_programs_from_the_working_directory() -> None:
    """Windows looks for a program such as docker or git in the working directory before PATH (CreateProcess,
    shutil.which), and clients start the framework inside a project. This turns that off for the framework and every
    process it starts, as -P does for imports. Call it before the framework starts (it checks Docker)."""
    if sys.platform == "win32":
        os.environ["NoDefaultCurrentDirectoryInExePath"] = "1"


def logging_to_stderr() -> None:
    """Points every logging handler that writes to stdout at stderr."""
    stdout = [s for s in (sys.stdout, sys.__stdout__) if s is not None]
    loggers = [logging.getLogger()] + [lg for lg in logging.Logger.manager.loggerDict.values()
                                       if isinstance(lg, logging.Logger)]
    for lg in loggers:
        for handler in lg.handlers:
            if isinstance(handler, logging.StreamHandler) and any(handler.stream is s for s in stdout):
                handler.setStream(sys.stderr)


@contextlib.contextmanager
def sigterm_ends_the_session():
    """Clients stop a stdio program by closing its input and, when it has not ended a few seconds later (some right
    away), with SIGTERM, then SIGKILL. Python's default for SIGTERM ends the process at once, so the session's cleanup
    would never run. Here SIGTERM interrupts the program like Ctrl+C: the main task is cancelled and the shielded
    cleanup runs; a second SIGTERM is ignored while it does (SIGKILL still ends it)."""
    import signal
    if threading.current_thread() is not threading.main_thread() or not hasattr(signal, "SIGTERM"):
        yield
        return

    def interrupt(signum, frame):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        handler = signal.getsignal(signal.SIGINT)
        if callable(handler) and handler is not interrupt:
            handler(signal.SIGINT, frame)  # asyncio's runner cancels its main task (default handler: raises)
        else:
            raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, interrupt)
    try:
        yield
    finally:
        with contextlib.suppress(TypeError, ValueError):
            signal.signal(signal.SIGTERM, previous)


__all__ = ["ACP_AGENT", "MCP_SERVE", "installation_root", "launch_command", "logging_to_stderr",
           "no_programs_from_the_working_directory", "shell_command", "sigterm_ends_the_session", "windows_argument"]
