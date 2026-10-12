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
from PySide6.QtWidgets import QMessageBox, QWidget

from orchestrator.tools.confirmation import CallTooLong, describe_call
from orchestrator.tools.registry import current_grant_offer, current_question
from orchestrator.utils.logging import get_logger

logger = get_logger("ToolConfirmation")


class _Question:
    def __init__(self, title: str, text: str, offer=None):
        self.title = title
        self.text = text
        self.offer = offer  # "do not ask again" the framework may offer for this call (grants.GrantOffer)
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
            text = current_question() or describe_call(spec, inp.model_dump(mode="json"), actor=self.actor)
        except CallTooLong as e:
            logger.warning(str(e))
            return False
        offer = current_grant_offer()
        if threading.get_ident() == self._gui_thread:
            return self.ask_user(spec.title, text, offer) if offer is not None else self.ask_user(spec.title, text)
        question = _Question(spec.title, text, offer)
        self._asked.emit(question)
        while not question.done.wait(0.1):
            if self._closed.is_set():
                return False
        return question.answer

    def _show(self, question: _Question) -> None:  # GUI thread
        try:
            if not self._closed.is_set():
                question.answer = (self.ask_user(question.title, question.text, question.offer)
                                   if question.offer is not None else self.ask_user(question.title, question.text))
        except Exception as e:
            logger.error(f"Confirmation dialog failed: {e}")
            question.answer = False
        finally:
            question.done.set()

    def ask_user(self, title: str, text: str, offer=None):
        """The modal question; "Deny" is the default and the Escape answer. With an offer, "Allow and do not ask
        again..." shows its warning and asks how wide and for how long."""
        parent = self.parent() if isinstance(self.parent(), QWidget) else None
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(f"Confirm: {title}")
        box.setTextFormat(Qt.PlainText)  # arguments come from the model: never rich text
        box.setText(text + "\n\nAllow this?")
        box.addButton("Allow", QMessageBox.AcceptRole)
        always = box.addButton("Allow and do not ask again...", QMessageBox.YesRole) if offer is not None else None
        deny = box.addButton("Deny", QMessageBox.RejectRole)
        box.setDefaultButton(deny)
        box.setEscapeButton(deny)
        box.exec()
        clicked = box.clickedButton()
        if always is not None and clicked is always:
            from orchestrator.gui.approvals_window import choose_grant
            from orchestrator.tools.grants import GrantChoice
            parent = self.parent() if isinstance(self.parent(), QWidget) else None
            choice = choose_grant(offer, parent)
            return GrantChoice(*choice) if choice else self.ask_user(title, text, None)
        return clicked is not None and box.buttonRole(clicked) == QMessageBox.AcceptRole

    def close(self) -> None:
        """Answers open and later questions with "no" (e.g. when the window closes)."""
        self._closed.set()


# The audit log records which channel a confirmation came from (ToolRegistry reads this attribute).
ToolConfirmation.confirm.confirm_channel = "framework-gui"
