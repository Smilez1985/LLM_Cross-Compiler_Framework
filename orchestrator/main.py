#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - GUI entry point (llm-gui, llm-framework, the desktop shortcut).

The GUI's start-up lives in orchestrator/gui/app.py. This module stays where the launchers look for it (install.sh,
install.bat, scripts/start-llm_cross_compiler_framework.bat, `llm-gui` in pyproject.toml) and is the GUI module itself
once it is imported (`import orchestrator.main` gives orchestrator.gui.app, so existing callers and tests keep working).

PySide6 is the optional 'gui' extra (DESIGN round 11, A3). Without it this prints how to install it instead of a
traceback; the command line (llmcf) and the MCP server work without it.
"""

import importlib.util
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:   # started as a script: python orchestrator/main.py
    sys.path.insert(0, str(_ROOT))

# Only the hash-pinned way: the framework is installed from its checkout, not from the package index, so a
# `pip install <project>[gui]` would fetch whatever carries that name there, unpinned (review of chain qt).
GUI_EXTRA_MISSING = (
    "The graphical interface needs PySide6, the optional 'gui' extra, and this installation does not have it. "
    "Run the installer again without --headless, or install the hash-pinned list from the framework's folder with "
    "the pip of this environment: pip install --require-hashes --no-deps -r requirements-gui.txt. The command line "
    "(llmcf) and its MCP server work without it.")


def qt_installed() -> bool:
    """Whether PySide6 can be found (a broken one, such as a missing system library, still counts as installed)."""
    try:
        return importlib.util.find_spec("PySide6") is not None
    except (ImportError, ValueError):
        return False


from orchestrator.utils.crash_report import (guard_streams, has_console, native_message,  # noqa: E402
                                             report_early_crash, windowless_children)

# Started from the menu entry or the Windows shortcut (pythonw.exe), there may be no console: sys.stdout and
# sys.stderr are then None, and a console program the window starts would open a console window of its own. Both are
# taken care of before anything else is imported (docs/DESIGN.md, starting without a terminal).
if __name__ == "__main__":
    guard_streams()
    windowless_children()

# A crash before Qt exists (a broken installation, a missing system library): started as the GUI, it goes into a
# crash file in the log folder (and a native message box on Windows without a console) instead of vanishing.
_early_error = None
try:
    from orchestrator.gui import app as _app
except ImportError as _error:
    if (_error.name or "").split(".")[0] in ("PySide6", "shiboken6") and not qt_installed():
        _app = None
    elif __name__ == "__main__":
        _app, _early_error = None, _error
    else:
        raise   # anything else than the missing extra keeps its own message
except Exception as _error:  # noqa: BLE001 - every crash before Qt is reported, see above
    if __name__ != "__main__":
        raise
    _app, _early_error = None, _error


def main():
    """Starts the GUI; without the 'gui' extra prints how to install it and returns 1."""
    guard_streams()
    if _early_error is not None:
        report_early_crash(_early_error, _settings())
        return 1
    if _app is None:
        print(GUI_EXTRA_MISSING, file=sys.stderr)
        if os.name == "nt" and not has_console():
            native_message("LLM Cross-Compiler Framework", GUI_EXTRA_MISSING)
        return 1
    try:
        return _app.main()
    except Exception as error:  # noqa: BLE001 - an error before the window's own crash dialog exists
        report_early_crash(error, _settings())
        return 1


def _settings():
    """The settings for the log folder and the language of a crash report; None when even they cannot be read."""
    try:
        from orchestrator.Core.config_manager import ConfigManager
        return ConfigManager()
    except Exception:  # noqa: BLE001 - the crash may be in the settings themselves
        return None


if _app is not None and __name__ != "__main__":
    sys.modules[__name__] = _app

if __name__ == "__main__":
    sys.exit(main())
