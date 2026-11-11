#!/usr/bin/env python3
"""
The allow-to-run question of a command (V1, Features "Allow a command (human in the loop)"; DESIGN "GUI and the
person's path"): where a next step of the catalogue reaches the person (a StepsRow under a message, a record in the
chat), a step the framework may run gets [Run...], and the question asks before anything runs.

* `ask_to_run(window, step)` builds the proposal from the catalogue (next_steps.proposal: closed values only, never
  free text from an AI) and acts by its kind: "run" opens AllowCommandDialog; "deletion" goes to the place whose
  own deletion dialog lists everything first ("No" is the default there); "shown" (a board's, an administrator's or
  another program's command) is never run here; "open" (a command that needs a terminal or does more than the
  question says) opens its place in the window (open_object).
* `AllowCommandDialog` shows "Allow `llmcf ...`? What it does. Why now.", a masked field for each secret the command
  needs, and Yes / No with No as the default (Escape and closing the window are a No). No runs nothing. Yes runs
  exactly the proposal through next_steps.run_proposal on a worker thread (never the GUI thread); a secret goes only
  through run_proposal's `secrets` (stdin or the environment), its field is emptied at once, and every output line
  has its value replaced before it is shown, logged or kept. The redacted output streams into the question's own
  output area and into the Ditto console beside the chat, with Cancel (run_proposal's `cancel`); the end says the
  exit status. A proposal that no longer matches the catalogue is refused, as run_proposal refuses it. Each decision
  is recorded in the activity record (next_steps.audit_proposal, actor gui).
* Only the person's click answers: the dialog is not a form of the GUI tools (gui_surface), no AI can see, fill or
  press it, and no key but Escape (a No) does anything.
"""

import queue
import threading
import uuid
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (QDialog, QFormLayout, QHBoxLayout, QLineEdit, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget)

from orchestrator.gui.plain import button_text, plain_label
from orchestrator.utils.logging import get_logger

logger = get_logger("AllowCommand")
POLL_MS = 100
HIDDEN = "[hidden]"


def _t(key: str, lang: str, **values: Any) -> str:
    from orchestrator.utils.localization import ui_text
    return ui_text(f"console.{key}", lang, **values)


def _console_of(window: Any) -> Any:
    return getattr(window, "ditto_console", None)


def _audit_of(window: Any) -> Any:
    registry = getattr(getattr(window, "framework", None), "tool_registry", None)
    return getattr(registry, "audit", None)


def _config_path(window: Any) -> Optional[str]:
    config = getattr(getattr(window, "framework", None), "config", None)
    return getattr(config, "_explicit_file", None)   # the file the GUI was started with, if any (llm-cli -c)


class AllowCommandDialog(QDialog):
    """The question for one "run" proposal (see the module text). `window`: the main window (its Ditto console, its
    audit log, its config file); `runner`: next_steps.run_proposal (tests pass a wrapper)."""

    def __init__(self, prop: Any, window: Any = None, parent: Optional[QWidget] = None,
                 runner: Optional[Callable[..., Any]] = None):
        super().__init__(parent or window)
        from orchestrator.utils.next_steps import _secret_name, get, run_proposal
        self.prop = prop
        self.lang = prop.lang or "en"
        self.window_ = window
        self.runner = runner or run_proposal
        self.run_id = uuid.uuid4().hex[:12]
        self.number = 0
        self.result: Any = None
        self.decided: Optional[bool] = None
        self.cancel_event = threading.Event()
        self.lines: "queue.Queue[Any]" = queue.Queue()
        self.thread: Optional[threading.Thread] = None
        self._secret_values: List[str] = []
        self.setObjectName("dlg_allow_command")
        self.setWindowTitle(_t("allow.title", self.lang))
        self.setModal(True)
        layout = QVBoxLayout(self)
        self.lbl_intro = plain_label(_t("allow.intro", self.lang))
        layout.addWidget(self.lbl_intro)
        self.lbl_question = plain_label(prop.question())
        self.lbl_question.setObjectName("lbl_allow_question")
        layout.addWidget(self.lbl_question)
        self.secret_fields: Dict[str, QLineEdit] = {}
        if prop.secrets:
            form = QFormLayout()
            step = get(prop.step_id)
            for name in prop.secrets:
                field = QLineEdit(self)
                field.setObjectName(f"txt_allow_secret_{name}")
                field.setEchoMode(QLineEdit.Password)
                field.setAttribute(Qt.WA_InputMethodEnabled, False)
                field.textChanged.connect(self._update_buttons)
                form.addRow(plain_label(_t("allow.secret", self.lang,
                                           secret=_secret_name(step.params[name], self.lang)), wrap=False), field)
                self.secret_fields[name] = field
            layout.addLayout(form)
        self.lbl_output = plain_label(_t("allow.output", self.lang), wrap=False)
        self.lbl_output.setVisible(False)
        layout.addWidget(self.lbl_output)
        self.output = QPlainTextEdit(self)
        self.output.setObjectName("txt_allow_output")
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(500)
        self.output.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.output.setVisible(False)
        layout.addWidget(self.output, stretch=1)
        self.lbl_status = plain_label("")
        self.lbl_status.setObjectName("lbl_allow_status")
        layout.addWidget(self.lbl_status)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.btn_yes = QPushButton(button_text(_t("allow.yes", self.lang)), self)
        self.btn_yes.setObjectName("btn_allow_yes")
        self.btn_no = QPushButton(button_text(_t("allow.no", self.lang)), self)
        self.btn_no.setObjectName("btn_allow_no")
        self.btn_cancel = QPushButton(button_text(_t("allow.cancel", self.lang)), self)
        self.btn_cancel.setObjectName("btn_allow_cancel")
        self.btn_cancel.setVisible(False)
        self.btn_close = QPushButton(button_text(_t("allow.close", self.lang)), self)
        self.btn_close.setObjectName("btn_allow_close")
        self.btn_close.setVisible(False)
        for button in (self.btn_yes, self.btn_no, self.btn_cancel, self.btn_close):
            button.setAutoDefault(False)
            buttons.addWidget(button)
        self.btn_no.setDefault(True)   # Enter is a No; only a click on Yes runs anything
        self.btn_no.setFocus(Qt.OtherFocusReason)
        layout.addLayout(buttons)
        self.btn_yes.clicked.connect(self.allow)
        self.btn_no.clicked.connect(self.decline)
        self.btn_cancel.clicked.connect(self.cancel)
        self.btn_close.clicked.connect(self.accept)
        self.timer = QTimer(self)
        self.timer.setInterval(POLL_MS)
        self.timer.timeout.connect(self._drain)
        self._update_buttons()

    # --- the person's answer ---------------------------------------------------------------------------------------

    def _update_buttons(self, *_args: Any) -> None:
        if self.decided is None:
            self.btn_yes.setEnabled(all(field.text() for field in self.secret_fields.values()))

    def _console(self, kind: str, words: str, level: str = "info") -> None:
        from orchestrator.Core.ditto_console import command_line_entry
        console = _console_of(self.window_)
        if console is None:
            return
        self.number += 1
        console.follower.push([command_line_entry(self.run_id, self.number, kind, self._hide(words), level=level,
                                                  lang=self.lang)])

    def _hide(self, line: str) -> str:
        for value in self._secret_values:
            if value:
                line = line.replace(value, HIDDEN)
        return line

    def decline(self) -> None:
        """No: nothing runs; the decision is recorded."""
        if self.decided is not None:
            return
        from orchestrator.utils.next_steps import audit_proposal
        self.decided = False
        for field in self.secret_fields.values():
            field.clear()
        audit_proposal(_audit_of(self.window_), self.prop, False)
        self._console("did", _t("allow.decided_no", self.lang, command=self.prop.command))
        self.lbl_status.setText(_t("allow.declined", self.lang))
        self.reject()

    def reject(self) -> None:   # Escape and closing the window are a No
        if self.decided is None:
            self.decline()
            return
        if self.thread is not None and self.thread.is_alive():
            self.cancel()
            return
        super().reject()

    def allow(self) -> None:
        """Yes: runs exactly the proposal on a worker thread; the secrets leave their fields at once."""
        if self.decided is not None or not self.btn_yes.isEnabled():
            return
        self.decided = True
        secrets = {name: field.text() for name, field in self.secret_fields.items()}
        self._secret_values = sorted((v for v in secrets.values() if v), key=len, reverse=True)
        for field in self.secret_fields.values():
            field.clear()
            field.setEnabled(False)
        self.btn_yes.setEnabled(False)
        self.btn_no.setVisible(False)
        self.btn_cancel.setVisible(True)
        self.lbl_output.setVisible(True)
        self.output.setVisible(True)
        self.lbl_status.setText(_t("allow.running", self.lang))
        self._console("runs", _t("allow.decided_yes", self.lang, command=self.prop.command))
        self.thread = threading.Thread(target=self._work, args=(secrets,), name="allow-command", daemon=True)
        self.thread.start()
        self.timer.start()

    def cancel(self) -> None:
        self.cancel_event.set()
        self.btn_cancel.setEnabled(False)

    # --- the worker and its output ---------------------------------------------------------------------------------

    def _work(self, secrets: Dict[str, str]) -> None:
        """Runs on the worker thread: never touches Qt; hands everything over through the queue."""
        from orchestrator.utils.next_steps import ProposalRefused
        try:
            result = self.runner(self.prop, lambda line: self.lines.put(("line", line)), secrets=secrets,
                                 config_path=_config_path(self.window_), cancel=self.cancel_event)
            self.lines.put(("result", result))
        except ProposalRefused as e:
            self.lines.put(("refused", str(e)))
        except Exception as e:  # noqa: BLE001 - shown as a refusal; the GUI keeps running
            self.lines.put(("refused", type(e).__name__))
        finally:
            secrets.clear()

    def _drain(self) -> int:
        """Takes what the worker handed over (on the GUI thread); returns how many output lines it added."""
        added: List[str] = []
        while True:
            try:
                kind, value = self.lines.get_nowait()
            except queue.Empty:
                break
            if kind == "line":
                added.append(self._hide(str(value)))
            elif kind == "result":
                self._flush(added)
                added = []
                self._finished(value)
            else:
                self._flush(added)
                added = []
                self._refused(str(value))
        self._flush(added)
        return len(added)

    def _flush(self, lines: List[str]) -> None:
        if not lines:
            return
        self.output.appendPlainText("\n".join(lines))
        for line in lines:
            self._console("output", f"{self.prop.command}: {line}")

    def _finished(self, result: Any) -> None:
        from orchestrator.utils.next_steps import audit_proposal
        self.timer.stop()
        safe = result.__class__(result.state, result.exit_code, tuple(self._hide(x) for x in result.lines),
                                self._hide(result.summary))
        self.result = safe
        audit_proposal(_audit_of(self.window_), self.prop, True, safe)
        status = safe.summary
        if safe.exit_code is not None:
            status += " " + _t("allow.exit", self.lang, code=safe.exit_code)
        self.lbl_status.setText(status)
        self._console("did", f"{self.prop.command}: {status}", level="info" if safe.state == "done" else "warning")
        self._ended()

    def _refused(self, reason: str) -> None:
        from orchestrator.utils.next_steps import audit_proposal
        self.timer.stop()
        audit_proposal(_audit_of(self.window_), self.prop, True, None)
        self.lbl_status.setText(_t("allow.refused", self.lang, reason=self._hide(reason)))
        self._console("did", f"{self.prop.command}: {self.lbl_status.text()}", level="warning")
        self._ended()

    def _ended(self) -> None:
        self._secret_values = []
        self.btn_cancel.setVisible(False)
        self.btn_close.setVisible(True)
        self.btn_close.setDefault(True)

    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def closeEvent(self, event: Any) -> None:
        if self.running():   # the command is stopped first; the window closes when it has ended
            self.cancel()
            event.ignore()
            return
        if self.decided is None:
            self.decline()
        super().closeEvent(event)


def ask_to_run(window: Any, step: Any, parent: Optional[QWidget] = None, lang: Optional[str] = None,
               runner: Optional[Callable[..., Any]] = None) -> Optional[AllowCommandDialog]:
    """What [Run...] of a step does (see the module text). Returns the question's dialog for a "run" proposal (shown,
    not waited for), None otherwise (the place was opened, or nothing could be offered)."""
    from orchestrator.gui.next_step_widgets import main_window, open_object
    from orchestrator.utils.localization import current_language
    from orchestrator.utils.next_steps import ProposalRefused, proposal
    window = window or main_window()
    try:
        prop = proposal(step, lang=lang or current_language())
    except ProposalRefused as e:
        logger.warning(f"No command proposal for this step: {e}")
        return None
    if prop.kind != "run":   # deletion: its own list first; open: its place; shown: never run here
        if prop.target:
            open_object(window, prop.target)
        return None
    dialog = AllowCommandDialog(prop, window, parent=parent, runner=runner)
    dialog.setAttribute(Qt.WA_DeleteOnClose, False)
    if window is not None:
        window.allow_command_dialog = dialog   # kept alive while it runs
    dialog.show()
    dialog.raise_()
    return dialog


__all__ = ["AllowCommandDialog", "ask_to_run"]
