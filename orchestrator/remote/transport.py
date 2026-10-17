#!/usr/bin/env python3
"""
Whether SSH works on this computer, with paramiko imported on first use only (decision RG1: the CLI, the MCP server
and the GUI start without it).

- paramiko_module(): the paramiko module, imported the first time something needs it; None when it cannot be
  imported (not installed, or an installation whose import fails, for example a missing compiled dependency).
- available() -> (bool, reason): whether paramiko imports here, and why not ("" when it does). This is a real import,
  not a look for the package on disk: a broken installation counts as missing everywhere, so the deploy falls back to
  OpenSSH instead of failing on the first login.
- PARAMIKO_AVAILABLE: true while paramiko imports, asked only when it is first tested. DeploymentManager and the
  Builder tab keep it under their old module attribute PARAMIKO_AVAILABLE (tests set that to True or False).

The connection itself (login, commands, uploads, the system-ssh fallback) moves here in consolidation stage C; today
it lives in orchestrator/utils/ssh_trust.py and orchestrator/Core/deployment_manager.py.
"""

import threading
from typing import Any, Optional, Tuple

_lock = threading.Lock()
_loaded: Optional[Tuple[Any, str]] = None   # (the module or None, why not) once it was looked up


def paramiko_module() -> Any:
    """The paramiko module, imported on the first call; None when it cannot be imported."""
    global _loaded
    if _loaded is None:
        with _lock:
            if _loaded is None:
                try:
                    import paramiko
                except ModuleNotFoundError as e:
                    _loaded = (None, "the 'paramiko' package is not installed" if e.name == "paramiko" else
                               f"the 'paramiko' package cannot be loaded: {e.name} is missing")
                except Exception as e:  # noqa: BLE001 - an installation whose import fails is a missing one
                    _loaded = (None, f"the 'paramiko' package cannot be loaded ({e.__class__.__name__})")
                else:
                    _loaded = (paramiko, "")
    return _loaded[0]


def available() -> Tuple[bool, str]:
    """(True, "") when paramiko imports here, else (False, why not)."""
    module = paramiko_module()
    return module is not None, ("" if module is not None else _loaded[1])


class _Availability:
    """True while paramiko imports; the import happens when the value is first tested, never at import time."""

    def __bool__(self) -> bool:
        return available()[0]

    def __repr__(self) -> str:
        return repr(bool(self))


PARAMIKO_AVAILABLE = _Availability()


__all__ = ["PARAMIKO_AVAILABLE", "available", "paramiko_module"]
