#!/usr/bin/env python3
"""
RemoteFailure: the base of every board step's failure, so one `except RemoteFailure` catches them all and each carries
the same three facts.

- `kind`: the case, for the person's localized text. The board contacts use no_paramiko, unreachable,
  host_key_changed, not_trusted, other_device, login_failed and key_install (the GUI's Boards window shows its own text
  for each, gui/boards_window.py); a scan uses login, probe and save; the AI path names none ("").
- `reason`: the SSH reason code (orchestrator/remote/reasons.py) when the board could not be reached or logged in to,
  else "".
- `error`: the exception behind it (None when there is none).

Its subclasses keep their own names and fields: BoardStepError (the person's board steps,
runtime/device/person_steps.py), DeviceScanError (a board scan, runtime/scan_steps.py) and DeviceRuntimeError (the AI
path, runtime/device/service.py, with the tool reason code and the next step).
"""

from typing import Optional


class RemoteFailure(RuntimeError):
    """A step on a board that cannot be done; the message is the framework's sentence (never the board's words)."""

    def __init__(self, message: str, kind: str = "", reason: str = "", error: Optional[BaseException] = None):
        super().__init__(message)
        self.kind, self.reason, self.error = kind, reason, error


__all__ = ["RemoteFailure"]
