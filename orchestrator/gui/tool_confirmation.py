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
from orchestrator.tools.registry import current_question
from orchestrator.utils.logging import get_logger

logger = get_logger("ToolConfirmation")

LONG_QUESTION = 1500  # characters: a longer question (up to 16,000) is shown in a scrollable dialog, whole


def long_question_dialog(parent: Optional[QWidget], title: str, text: str):
    """(dialog, allow button) for a long question: the whole text read-only and scrollable (plain text, never rich
    text), "Deny" the default and the Escape answer."""
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
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    return dialog, allow


class _Question:
    def __init__(self, title: str, text: str):
        self.title = title
        self.text = text
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

    def confirm(self, spec: Any, inp: Any) -> bool:
        """The user's answer for one tool call: title and every argument are shown. Blocks until answered."""
        if self._closed.is_set():
            return False
        try:
            # The registry's question (with repaired values marked) when it asks; it is what the audit log hashes.
            text = current_question() or describe_call(spec, inp.model_dump(mode="json"), actor=self.actor,
                                                       limit=MAX_FRAMEWORK)
        except CallTooLong as e:
            logger.warning(str(e))
            return False
        if threading.get_ident() == self._gui_thread:
            return self.ask_user(spec.title, text)
        question = _Question(spec.title, text)
        self._asked.emit(question)
        while not question.done.wait(0.1):
            if self._closed.is_set():
                return False
        return question.answer

    def _show(self, question: _Question) -> None:  # GUI thread
        try:
            if not self._closed.is_set():
                question.answer = bool(self.ask_user(question.title, question.text))
        except Exception as e:
            logger.error(f"Confirmation dialog failed: {e}")
            question.answer = False
        finally:
            question.done.set()

    def ask_user(self, title: str, text: str) -> bool:
        """The modal question; "Deny" is the default and the Escape answer. A long question is shown whole in a
        scrollable dialog."""
        parent = self.parent() if isinstance(self.parent(), QWidget) else None
        if len(text) > LONG_QUESTION:
            dialog, _ = long_question_dialog(parent, title, text)
            return dialog.exec() == QDialog.Accepted
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(f"Confirm: {title}")
        box.setTextFormat(Qt.PlainText)  # arguments come from the model: never rich text
        box.setText(text + "\n\nAllow this?")
        box.addButton("Allow", QMessageBox.AcceptRole)
        deny = box.addButton("Deny", QMessageBox.RejectRole)
        box.setDefaultButton(deny)
        box.setEscapeButton(deny)
        box.exec()
        clicked = box.clickedButton()
        return clicked is not None and box.buttonRole(clicked) == QMessageBox.AcceptRole

    def close(self) -> None:
        """Answers open and later questions with "no" (e.g. when the window closes)."""
        self._closed.set()


# The audit log records which channel a confirmation came from (ToolRegistry reads this attribute).
ToolConfirmation.confirm.confirm_channel = "framework-gui"
