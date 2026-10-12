#!/usr/bin/env python3
"""
Confirmation of Ditto's tool calls in the GUI.

Ditto's tool loop runs in a worker thread, but dialogs belong to the GUI thread. ToolConfirmation.confirm()
can be called from any thread: the question is shown as a modal dialog on the GUI thread while the calling
thread waits. Closing the owner answers every open and later question with "no", so a worker never hangs.
"""

import threading
from typing import Any, Optional

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QMessageBox, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget)

from orchestrator.tools.confirmation import MAX_FRAMEWORK, CallTooLong, describe_call
from orchestrator.tools.registry import current_grant_offer, current_question
from orchestrator.utils.logging import get_logger

logger = get_logger("ToolConfirmation")

LONG_QUESTION = 1500  # characters: a longer question (up to 16,000) is shown in a scrollable dialog, whole
ALWAYS = 2            # the long dialog's result code for "Allow and do not ask again..."
STOP_ASKING = 3       # the long dialog's result code for a remote framework's "Allow and ask it to stop asking..."


def stop_asking_warning(server: str) -> str:
    """What "Allow and ask it to stop asking" means for a question of a framework Ditto uses in MCP-client mode."""
    return (f"This allows the call once and sends the framework at {server} a REQUEST to stop asking for calls like "
            "this one. Nothing changes until a person confirms that request in that framework's own approval window "
            "(its GUI, or 'llm-cli tools approvals' on that machine), where it shows what exactly would no longer be "
            "asked. Once confirmed there, Ditto may run such calls on that framework without asking you, until it is "
            "revoked there.\n\nSend the request?")


def long_question_dialog(parent: Optional[QWidget], title: str, text: str, offer=None, remote: Optional[str] = None):
    """(dialog, allow button) for a long question: the whole text read-only and scrollable (plain text, never rich
    text), "Deny" the default and the Escape answer. With an offer the dialog also has "Allow and do not ask
    again..." (dialog.always_button), which ends it with the result code ALWAYS. With `remote` (the name of a
    framework whose question it is, offering "stop asking") it has "Allow and ask it to stop asking..."
    (dialog.stop_asking_button), which ends it with STOP_ASKING."""
    dialog = QDialog(parent)
    dialog.setWindowTitle(f"Confirm: {title}")
    layout = QVBoxLayout(dialog)
    view = QPlainTextEdit(dialog)
    view.setReadOnly(True)
    view.setPlainText(text)
    view.setMinimumSize(640, 420)
    layout.addWidget(view)
    layout.addWidget(QLabel("Allow this?", dialog))
    buttons = QDialogButtonBox(dialog)
    allow = QPushButton("Allow", dialog)
    deny = QPushButton("Deny", dialog)
    buttons.addButton(allow, QDialogButtonBox.AcceptRole)
    buttons.addButton(deny, QDialogButtonBox.RejectRole)
    allow.setAutoDefault(False)
    deny.setDefault(True)
    dialog.always_button = None
    if offer is not None:
        always = QPushButton("Allow and do not ask again...", dialog)
        always.setAutoDefault(False)
        buttons.addButton(always, QDialogButtonBox.ActionRole)
        always.clicked.connect(lambda: dialog.done(ALWAYS))
        dialog.always_button = always
    dialog.stop_asking_button = None
    if remote:
        stop = QPushButton("Allow and ask it to stop asking...", dialog)
        stop.setAutoDefault(False)
        stop.setToolTip(f"Allows this call and sends the framework at {remote} a request to stop asking; a person "
                        "confirms it there.")
        buttons.addButton(stop, QDialogButtonBox.ActionRole)
        stop.clicked.connect(lambda: dialog.done(STOP_ASKING))
        dialog.stop_asking_button = stop
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    return dialog, allow


class _Question:
    def __init__(self, title: str, text: str, offer=None, remote: Optional[str] = None):
        self.title = title
        self.text = text
        self.offer = offer  # "do not ask again" the framework may offer for this call (grants.GrantOffer)
        self.remote = remote  # a remote framework's question that offers "stop asking": the framework's name
        self.answer = False
        self.done = threading.Event()


class ToolConfirmation(QObject):
    """Create it on the GUI thread; pass its confirm method as the ToolContext's confirm callback."""

    _asked = Signal(object)

    def __init__(self, parent: Optional[QObject] = None, actor: str = "Ditto"):
        super().__init__(parent)
        self.actor = actor
        self._gui_thread = threading.get_ident()
        self._closed = threading.Event()
        self._asked.connect(self._show, Qt.QueuedConnection)

    def confirm(self, spec: Any, inp: Any):
        """The user's answer for one tool call: title and every argument are shown. Blocks until answered. True,
        False, or grants.GrantChoice for "allow and do not ask again" (offered where the framework can)."""
        if self._closed.is_set():
            return False
        try:
            # The registry's question (with repaired values marked) when it asks; it is what the audit log hashes.
            text = current_question() or describe_call(spec, inp.model_dump(mode="json"), actor=self.actor,
                                                       limit=MAX_FRAMEWORK)
        except CallTooLong as e:
            logger.warning(str(e))
            return False
        offer = current_grant_offer()
        # A question of the framework Ditto uses in MCP-client mode (remote_tools.RemoteQuestion): only when that
        # server's form offers it, "stop asking" can be requested there (never a standing approval here).
        from orchestrator.llm.remote_tools import RemoteQuestion
        remote = (spec.server or "server") if isinstance(spec, RemoteQuestion) and spec.offers_request else None
        if threading.get_ident() == self._gui_thread:
            return self._ask(spec.title, text, offer, remote)
        question = _Question(spec.title, text, offer, remote)
        self._asked.emit(question)
        while not question.done.wait(0.1):
            if self._closed.is_set():
                return False
        return question.answer

    def _show(self, question: _Question) -> None:  # GUI thread
        try:
            if not self._closed.is_set():
                question.answer = self._ask(question.title, question.text, question.offer, question.remote)
        except Exception as e:
            logger.error(f"Confirmation dialog failed: {e}")
            question.answer = False
        finally:
            question.done.set()

    def _ask(self, title: str, text: str, offer=None, remote: Optional[str] = None):
        if remote:
            return self.ask_user(title, text, remote=remote)
        return self.ask_user(title, text, offer) if offer is not None else self.ask_user(title, text)

    def ask_user(self, title: str, text: str, offer=None, remote: Optional[str] = None):
        """The modal question; "Deny" is the default and the Escape answer. A long question is shown whole in a
        scrollable dialog. With an offer, "Allow and do not ask again..." shows its warning and asks how wide and
        for how long. With `remote` (a remote framework's question whose form offers it), "Allow and ask it to stop
        asking..." shows what that means and answers RemoteAnswer(approve, stop_asking=True): only a request, which a
        person confirms in that framework's own approval window."""
        parent = self.parent() if isinstance(self.parent(), QWidget) else None
        if len(text) > LONG_QUESTION:
            dialog, _ = long_question_dialog(parent, title, text, offer, remote)
            result = dialog.exec()
            if offer is not None and result == ALWAYS:
                return self._always(title, text, offer)
            if remote and result == STOP_ASKING:
                return self._stop_asking(title, text, remote)
            return result == QDialog.Accepted
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(f"Confirm: {title}")
        box.setTextFormat(Qt.PlainText)  # arguments come from the model: never rich text
        box.setText(text + "\n\nAllow this?")
        box.addButton("Allow", QMessageBox.AcceptRole)
        always = box.addButton("Allow and do not ask again...", QMessageBox.YesRole) if offer is not None else None
        stop = box.addButton("Allow and ask it to stop asking...", QMessageBox.YesRole) if remote else None
        deny = box.addButton("Deny", QMessageBox.RejectRole)
        box.setDefaultButton(deny)
        box.setEscapeButton(deny)
        box.exec()
        clicked = box.clickedButton()
        if always is not None and clicked is always:
            return self._always(title, text, offer)
        if stop is not None and clicked is stop:
            return self._stop_asking(title, text, remote)
        return clicked is not None and box.buttonRole(clicked) == QMessageBox.AcceptRole

    def _stop_asking(self, title: str, text: str, remote: str):
        """"Allow and ask it to stop asking...": the warning first ("No" the default); backing out asks once more,
        without the option."""
        from orchestrator.llm.remote_tools import RemoteAnswer
        parent = self.parent() if isinstance(self.parent(), QWidget) else None
        box = QMessageBox(QMessageBox.Warning, f"Ask the framework at {remote} to stop asking?",
                          stop_asking_warning(remote), QMessageBox.Yes | QMessageBox.No, parent)
        box.setTextFormat(Qt.PlainText)  # the server's name is the person's label or the server's address
        box.setDefaultButton(QMessageBox.No)
        box.setEscapeButton(QMessageBox.No)
        if box.exec() == QMessageBox.Yes:
            return RemoteAnswer(True, stop_asking=True)
        return self.ask_user(title, text)

    def _always(self, title: str, text: str, offer):
        """"Allow and do not ask again...": the width and duration with the warning; backing out asks once more,
        without the offer."""
        from orchestrator.gui.approvals_window import choose_grant
        from orchestrator.tools.grants import GrantChoice
        parent = self.parent() if isinstance(self.parent(), QWidget) else None
        choice = choose_grant(offer, parent)
        return GrantChoice(*choice) if choice else self.ask_user(title, text, None)

    def close(self) -> None:
        """Answers open and later questions with "no" (e.g. when the window closes)."""
        self._closed.set()


# The audit log records which channel a confirmation came from (ToolRegistry reads this attribute).
ToolConfirmation.confirm.confirm_channel = "framework-gui"
