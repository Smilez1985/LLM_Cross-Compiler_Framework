#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - GUI entry point (llm-gui, llm-framework, the desktop shortcut).

The GUI's start-up lives in orchestrator/gui/app.py. This module stays where the launchers look for it (install.sh,
install.bat, scripts/start-llm_cross_compiler_framework.bat, `llm-gui` in pyproject.toml) and is the GUI module itself
once it is imported (`import orchestrator.main` gives orchestrator.gui.app, so existing callers and tests keep working).

PySide6 is the optional 'gui' extra (DECISIONS round 11, A3). Without it this prints how to install it instead of a
traceback; the command line (llmcf) and the MCP server work without it.
"""

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:   # started as a script: python orchestrator/main.py
    sys.path.insert(0, str(_ROOT))

GUI_EXTRA_MISSING = (
    "The graphical interface needs PySide6, the optional 'gui' extra, and this installation does not have it. "
    "Install it into this environment with: pip install --require-hashes -r requirements-gui.txt (from the "
    "framework's folder; or pip install 'llm-cross-compiler-framework[gui]'), or run the installer again without "
    "--headless. The command line (llmcf) and its MCP server work without it.")


def qt_installed() -> bool:
    """Whether PySide6 can be found (a broken one, such as a missing system library, still counts as installed)."""
    try:
        return importlib.util.find_spec("PySide6") is not None
    except (ImportError, ValueError):
        return False


try:
    from orchestrator.gui import app as _app
except ImportError as _error:
    if (_error.name or "").split(".")[0] not in ("PySide6", "shiboken6") or qt_installed():
        raise   # anything else than the missing extra keeps its own message
    _app = None


def main():
    """Starts the GUI; without the 'gui' extra prints how to install it and returns 1."""
    if _app is None:
        print(GUI_EXTRA_MISSING, file=sys.stderr)
        return 1
    return _app.main()


if _app is not None and __name__ != "__main__":
    sys.modules[__name__] = _app

if __name__ == "__main__":
    sys.exit(main())
