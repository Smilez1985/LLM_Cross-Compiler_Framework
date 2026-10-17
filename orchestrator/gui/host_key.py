#!/usr/bin/env python3
"""
Trust on first use in the GUI, never silently (MCP design 2.7): the board's host key is read, its fingerprint shown
for the person to compare, and stored only after their "Yes" ("No" is the default); a changed key is refused.

`HostKeyCheck` reads the key in a plain worker thread (a board that does not answer holds the network for up to ten
seconds, never the window) and asks on the GUI thread through gui/worker_relay.py; `confirm_host_key` in
gui/dialogs.py does the same in one call where a window still reads the key itself. The question uses the words of
the CLI's (`llmcf deploy run`, `llmcf scan --device`): compare with ssh-keygen on the board, then trust it.

A board that cannot be reached says why and what to do (its SSH reason code, orchestrator/runtime/device/
reasons.py); that and a changed key are recorded for Mojo with the trusted board at that address (W1), as the CLI
records them. Every text is plain text.
"""

import threading
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

from PySide6.QtCore import QObject, Qt

from orchestrator.utils.localization import ui_text


def state_dir(framework: Any) -> Path:
    """The framework's per-user folder (where known_hosts lives)."""
    return Path(getattr(getattr(framework, "config", None), "user_state_dir", None) or Path.home() / ".llm-framework")


def read_host_key(folder: Path, host: str, port: int) -> Tuple[Any, str]:
    """(the key the board presents, its state in the framework's known_hosts: known, unknown or changed): one
    connection to the board's SSH port, no login. Network: call it off the GUI thread."""
    from orchestrator.utils import ssh_trust
    key = ssh_trust.fetch_host_key(host, port)
    return key, ssh_trust.check_host_key(Path(folder), host, port, key)


def _note(folder: Path, host: str, port: int, step: str, code: str) -> None:
    """Mojo's record of this contact (W1); never fails the question."""
    if not step or not code:
        return
    try:
        from orchestrator.runtime.device.trusted import TrustedDevices
        TrustedDevices(Path(folder)).note_reach_at(host, port, step, code)
    except Exception:  # noqa: BLE001 - bookkeeping only
        pass


def unreachable_text(host: str, port: int, error: BaseException, lang: Optional[str] = None) -> str:
    """Why the board's key could not be read: its reason code's title and next step, else the error itself."""
    from orchestrator.remote import reasons
    code = reasons.reach_code(error)
    if code in reasons.REASONS:
        return reasons.person_text(code, lang, address=host, port=port)
    return ui_text("deploy.hostkey.unreachable", lang, address=host, port=port, error=str(error))


def question_text(host: str, port: int, key: Any, lang: Optional[str] = None) -> str:
    from orchestrator.utils import ssh_trust
    return ui_text("deploy.hostkey.question", lang, address=host, port=port, key_type=key.get_name(),
                   fingerprint=ssh_trust.fingerprint(key))


def ask_trust(parent: Any, framework: Any, host: str, port: int, key: Any, status: str, *, step: str = "",
              lang: Optional[str] = None) -> bool:
    """The question part on the GUI thread: True for a known key, the person's "Yes" for an unknown one (then the key
    is stored), False for a changed key (refused, recorded for Mojo) or "No"."""
    from orchestrator.gui.plain import ask, plain_box
    from orchestrator.runtime.device import reasons
    from orchestrator.utils import ssh_trust
    if status == "known":
        return True
    folder = state_dir(framework)
    if status == "changed":
        _note(folder, host, port, step, reasons.HOST_KEY_CHANGED)
        plain_box(parent, "critical", ui_text("deploy.hostkey.changed_title", lang),
                  str(ssh_trust.HostKeyChanged(host, port)))
        return False
    if not ask(parent, ui_text("deploy.hostkey.title", lang), question_text(host, port, key, lang)):
        return False
    ssh_trust.trust_host_key(folder, host, port, key)
    return True


def show_unreachable(parent: Any, framework: Any, host: str, port: int, error: BaseException, *, step: str = "",
                     lang: Optional[str] = None) -> None:
    from orchestrator.gui.plain import plain_box
    from orchestrator.remote import reasons
    _note(state_dir(framework), host, port, step, reasons.reach_code(error))
    plain_box(parent, "critical", ui_text("deploy.hostkey.title", lang), unreachable_text(host, port, error, lang))


class HostKeyCheck(QObject):
    """The host-key step of one window: `start(host, port, then)` reads the key off the GUI thread, then asks on the
    GUI thread and calls `then(trusted)`. One check at a time; a result for an older check is dropped."""

    def __init__(self, parent: Any, framework: Any, lang: Optional[str] = None):
        super().__init__(parent)
        from orchestrator.gui.worker_relay import worker_relay
        self.window, self.framework, self.lang = parent, framework, lang
        self._token: Optional[object] = None
        self._pending: Optional[tuple] = None
        self.thread: Optional[threading.Thread] = None
        worker_relay().done.connect(self._done, Qt.QueuedConnection)

    @property
    def running(self) -> bool:
        return self._token is not None

    def start(self, host: str, port: int, then: Callable[[bool], None], *, step: str = "") -> None:
        from orchestrator.gui.worker_relay import worker_relay
        token, relay, folder = object(), worker_relay(), state_dir(self.framework)
        self._token, self._pending = token, (host, int(port), then, step)

        def run() -> None:
            try:
                result: Any = ("ok", read_host_key(folder, host, int(port)))
            except Exception as e:  # noqa: BLE001 - unreachable, no SSH server, a broken known_hosts file
                result = ("error", e)
            relay.done.emit(token, result)

        self.thread = threading.Thread(target=run, name="host-key", daemon=True)
        self.thread.start()

    def _done(self, token: object, result: Any) -> None:
        if token is not self._token or self._pending is None:
            return
        host, port, then, step = self._pending
        self._token, self._pending = None, None
        kind, value = result
        if kind == "error":
            show_unreachable(self.window, self.framework, host, port, value, step=step, lang=self.lang)
            then(False)
            return
        key, status = value
        then(ask_trust(self.window, self.framework, host, port, key, status, step=step, lang=self.lang))


__all__ = ["HostKeyCheck", "ask_trust", "question_text", "read_host_key", "show_unreachable", "state_dir",
           "unreachable_text"]
