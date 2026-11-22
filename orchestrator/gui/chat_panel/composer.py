#!/usr/bin/env python3
"""
Where the person writes (delegation design 5.1): Enter sends, Shift+Enter adds a line, Escape removes the reply chip
("About: …"), Up in an empty field moves to the newest bubble. The recipient is Ditto ("To: Ditto"); tasks to
connected AI apps are a later decision (design Q5). When Ditto cannot answer, sending is off with a one-line reason.
`set_hint()` is the place for "Waiting for your decision above" once Ditto can leave a question to the person.
"""

from typing import Any, Callable, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QPlainTextEdit, QStyle, QToolButton, QVBoxLayout, QWidget

from orchestrator.gui.chat_panel.bubbles import make_button, text, text_label
from orchestrator.gui.chat_panel import filter_hint
from orchestrator.gui.plain import button_text, one_line, plain_tooltip


PREVIEW_DELAY_MS = 400   # the preview runs this long after the last key
PREVIEW_MAX = 20000      # the preview reads at most this many characters of the field


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
    filter_cleared = Signal()   # the filter notice went away (the panel takes the marks out of the bubbles too)

    def __init__(self, lang: str = "en", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.lang = lang
        self.reply_to: Optional[str] = None
        self.available = True
        self.busy = False
        # The filter notice: `previewer(text)` gives the FilterReport of what would be replaced in the typed text (or
        # None); the panel sets it. The marks and the symbol live only while they are relevant (filter_hint.py).
        self.previewer: Optional[Callable[[str], Any]] = None
        self._report: Any = None
        self._previewing = False
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(PREVIEW_DELAY_MS)
        self._preview_timer.timeout.connect(self._preview)
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
        # the quiet filter symbol at the edge: hidden until the filter has something to tell
        self.btn_filter = QToolButton(self)
        self.btn_filter.setObjectName("btn_chat_filter")
        self.btn_filter.setIcon(self.style().standardIcon(QStyle.SP_MessageBoxInformation))
        self.btn_filter.setAutoRaise(True)
        self.btn_filter.setAccessibleName(text("chat.filter.name", lang))
        self.btn_filter.setVisible(False)
        self.btn_filter.clicked.connect(self.clear_filter)
        chips.addWidget(self.btn_filter)
        layout.addLayout(chips)

        self.lbl_hint = text_label("", lang, caption=True)
        self.lbl_hint.setVisible(False)
        layout.addWidget(self.lbl_hint)

        row = QHBoxLayout()
        self.input = ComposerInput(self)
        self.input.setObjectName("txt_chat_input")     # `llm-cli ditto ask QUESTION` by mouse (menus/ditto_models)
        self.input.setPlaceholderText(text("chat.composer.placeholder", lang))
        self.input.setAccessibleName(text("chat.composer.input_name", lang))
        self.input.submit.connect(self.send_requested.emit)
        self.input.escape.connect(self.clear_reply)
        self.input.to_feed.connect(self.to_feed.emit)
        self.input.textChanged.connect(self._typed)
        row.addWidget(self.input, stretch=1)
        self.btn_send = make_button(text("chat.composer.send", lang))
        self.btn_send.setObjectName("btn_chat_send")
        self.btn_send.clicked.connect(self.send_requested.emit)
        self.btn_send.setStyleSheet("background-color: #1565C0; color: white; font-weight: bold;")
        row.addWidget(self.btn_send, alignment=Qt.AlignBottom)
        layout.addLayout(row)

        self.lbl_reason = text_label("", lang, caption=True)
        self.lbl_reason.setVisible(False)
        layout.addWidget(self.lbl_reason)

    # --- the filter notice -----------------------------------------------------------------------------------------

    def _typed(self) -> None:
        """The person types: the preview runs a moment after the last key; an empty field has no marks."""
        if self.previewer is None:
            return
        if not self.input.toPlainText():
            self.clear_filter()
            return
        self._preview_timer.start()

    def _preview(self) -> None:
        """Marks, in the field, the stretches the filter will replace before this leaves the computer, and shows the
        symbol with the whole list; nothing when the provider is local or nothing would be replaced."""
        value = self.input.toPlainText()
        report = self.previewer(value[:PREVIEW_MAX]) if self.previewer is not None and value else None
        if report is None or report.empty:
            self.clear_filter()
            return
        self._show(report, preview=True)
        filter_hint.mark(self.input, report.stretches(value[:PREVIEW_MAX]))

    def _show(self, report: Any, *, preview: bool) -> None:
        self._report, self._previewing = report, preview
        self.btn_filter.setToolTip(plain_tooltip(filter_hint.notice_text(report, self.lang, preview=preview)))
        self.btn_filter.setVisible(True)

    def show_sent(self, report: Any) -> None:
        """After the answer: the symbol with what was replaced in the request, by place, and what was put back."""
        self._preview_timer.stop()
        filter_hint.unmark(self.input)
        if report is None or report.empty:
            self.clear_filter()
        else:
            self._show(report, preview=False)

    def clear_filter(self) -> None:
        """Symbol and marks go: with the next question, an empty field or a click on the symbol."""
        self._preview_timer.stop()
        had = self._report is not None
        self._report = None
        self.btn_filter.setVisible(False)
        filter_hint.unmark(self.input)
        if had:
            self.filter_cleared.emit()

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
        self.btn_filter.setAccessibleName(text("chat.filter.name", lang))
        if self._report is not None:
            self._show(self._report, preview=self._previewing)


__all__ = ["ComposerInput", "Composer"]
