#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - which kind of program this process is

Set once by the entry point (the GUI, the CLI, the MCP server, the API); read by the build state's summaries
(Core/request_store.py re-exports both names), the activity feed (Core/activity.py) and the framework's log
(utils/crash_report.add_framework_log marks each line with it). It lives here, in the bottom layer, so the log of
utils can name the program without importing the domain.
"""

PROGRAMS = ("gui", "cli", "mcp", "api", "framework")

_PROGRAM = {"kind": "framework"}


def set_program(kind: str) -> None:
    """Which kind of program this process is (gui, cli, mcp, api); set once by its entry point and written into
    every build summary, so a person sees where a build runs and where to stop it, and into every line of
    framework.log."""
    _PROGRAM["kind"] = kind if kind in PROGRAMS else "framework"


def program() -> str:
    return _PROGRAM["kind"]


__all__ = ["PROGRAMS", "program", "set_program"]
