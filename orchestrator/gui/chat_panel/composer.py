#!/usr/bin/env python3
"""
Where the person writes (delegation design 5.1): Enter sends, Shift+Enter adds a line, Escape removes the reply chip
("About: …"), Up in an empty field moves to the newest bubble. The recipient is Ditto ("To: Ditto"); tasks to
connected AI apps are a later decision (design Q5). When Ditto cannot answer, sending is off with a one-line reason.
`set_hint()` is the place for "Waiting for your decision above" once Ditto can leave a question to the person.
"""

from typing import Any, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QPlainTextEdit, QVBoxLayout, QWidget

from orchestrator.gui.chat_panel.bubbles import make_button, text, text_label
from orchestrator.gui.plain import button_text, one_line, plain_tooltip


class ComposerInput(QPlainTextEdit):
    """The text field. Tab moves the focus on (it never types a tab), so the panel stays usable by keyboard. Keeps
    the old line edit's text()/setText() for callers of the former chat tab."""

    submit = Signal()
    escape = Signal()
    to_feed = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setTabChangesFocus(True)
        self.setFixedHeight(self.fontMetrics().lineSpacing() * 3 + 14)

    def text(self) -> str:
        return self.toPlainText()

    def setText(self, value: str) -> None:
        self.setPlainText(value)

    def keyPressEvent(self, event: Any) -> None:
        key, mods = event.key(), event.modifiers()
        if key in (Qt.Key_Return, Qt.Key_Enter):
            if mods & Qt.ShiftModifier:
                self.insertPlainText("\n")
            else:
                self.submit.emit()
            event.accept()
            return
        if key == Qt.Key_Escape:
            self.escape.emit()
            event.accept()
            return
        if key == Qt.Key_Up and not self.toPlainText():
            self.to_feed.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class Composer(QWidget):
    send_requested = Signal()
    to_feed = Signal()

    def __init__(self, lang: str = "en", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.lang = lang
        self.reply_to: Optional[str] = None
        self.available = True
        self.busy = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        chips = QHBoxLayout()
        self.lbl_to = text_label(text("chat.composer.to", lang), lang, caption=True)
        chips.addWidget(self.lbl_to)
        self.lbl_about = text_label("", lang, caption=True)
        self.lbl_about.setVisible(False)
        chips.addWidget(self.lbl_about, stretch=1)
        self.btn_remove_about = make_button(text("chat.composer.remove_about", lang))
        self.btn_remove_about.setToolTip(plain_tooltip(text("chat.composer.remove_about_tip", lang)))
        self.btn_remove_about.setVisible(False)
        self.btn_remove_about.clicked.connect(self.clear_reply)
        chips.addWidget(self.btn_remove_about)
        chips.addStretch(1)
        layout.addLayout(chips)

        self.lbl_hint = text_label("", lang, caption=True)
        self.lbl_hint.setVisible(False)
        layout.addWidget(self.lbl_hint)

        row = QHBoxLayout()
        self.input = ComposerInput(self)
        self.input.setPlaceholderText(text("chat.composer.placeholder", lang))
        self.input.setAccessibleName(text("chat.composer.input_name", lang))
        self.input.submit.connect(self.send_requested.emit)
        self.input.escape.connect(self.clear_reply)
        self.input.to_feed.connect(self.to_feed.emit)
        row.addWidget(self.input, stretch=1)
        self.btn_send = make_button(text("chat.composer.send", lang))
        self.btn_send.clicked.connect(self.send_requested.emit)
        self.btn_send.setStyleSheet("background-color: #1565C0; color: white; font-weight: bold;")
        row.addWidget(self.btn_send, alignment=Qt.AlignBottom)
        layout.addLayout(row)

        self.lbl_reason = text_label("", lang, caption=True)
        self.lbl_reason.setVisible(False)
        layout.addWidget(self.lbl_reason)

    # --- the reply chip --------------------------------------------------------------------------------------------

    def set_reply_to(self, entry_id: str, about: str) -> None:
        """The message refers to a row ("About: …"); Escape or "Remove" takes the reference away."""
        self.reply_to = entry_id
        self.lbl_about.setText(text("chat.composer.about", self.lang, what=one_line(about)))
        self.lbl_about.setVisible(True)
        self.btn_remove_about.setVisible(True)

    def clear_reply(self) -> None:
        self.reply_to = None
        self.lbl_about.setText("")
        self.lbl_about.setVisible(False)
        self.btn_remove_about.setVisible(False)

    # --- state -----------------------------------------------------------------------------------------------------

    def set_available(self, available: bool, reason: str = "") -> None:
        """Whether Ditto can answer; when not, sending is off and the reason is shown in one line."""
        self.available = bool(available)
        self.lbl_reason.setText(one_line(reason) if not available else "")
        self.lbl_reason.setVisible(not available and bool(reason))
        self._update()

    def set_busy(self, busy: bool) -> None:
        self.busy = bool(busy)
        self._update()

    def set_hint(self, hint: str) -> None:
        self.lbl_hint.setText(one_line(hint))
        self.lbl_hint.setVisible(bool(hint))

    def _update(self) -> None:
        self.input.setEnabled(not self.busy and self.available)
        self.btn_send.setEnabled(not self.busy and self.available)

    def retranslate(self, lang: str) -> None:
        self.lang = lang
        self.lbl_to.setText(text("chat.composer.to", lang))
        self.input.setPlaceholderText(text("chat.composer.placeholder", lang))
        self.input.setAccessibleName(text("chat.composer.input_name", lang))
        self.btn_send.setText(button_text(text("chat.composer.send", lang)))
        self.btn_remove_about.setText(button_text(text("chat.composer.remove_about", lang)))
        self.btn_remove_about.setToolTip(plain_tooltip(text("chat.composer.remove_about_tip", lang)))


__all__ = ["ComposerInput", "Composer"]
