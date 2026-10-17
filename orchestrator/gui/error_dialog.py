#!/usr/bin/env python3
"""
The error box for the person (wave 6, f-words; DECISIONS round 10, Q7).

What happened, in the person's language and in plain words first; why, when it is known; ONE next step: catalogue
steps (chain a0's StepsRow with its [Open] buttons), a plain sentence, or one button. The English technical detail
(the framework's message, a traceback) is folded away under "Technical details", can be expanded and copied, with
secrets redacted and the home folder written as "~" (utils/wording.technical_details): the person can paste it into a
bug report or ask Ditto, who reads English and explains it in German. Every text is plain text, never HTML from data.

    show_error(parent, ui_text("build.failed.summary"), why=..., next_step=..., details=str(e))
    show_exception(parent, e, ui_text("deploy.failed"))      # details = message + traceback, steps = e.steps
    plain.plain_error(parent, what, why, steps=None, action=None)   # the same box, the design's name
"""

import traceback
from typing import Any, Callable, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPlainTextEdit,
                               QPushButton, QStyle, QToolButton, QVBoxLayout, QWidget)

from orchestrator.gui.plain import button_text, mnemonic_text, plain_label
from orchestrator.utils.localization import current_language, ui_text
from orchestrator.utils.wording import technical_details

DETAILS_LIMIT = 20000   # the end of a longer detail is kept (a traceback's last lines matter most)
ICONS = {"critical": QStyle.SP_MessageBoxCritical, "warning": QStyle.SP_MessageBoxWarning,
         "information": QStyle.SP_MessageBoxInformation}


def _steps_widget(steps: Any, parent: QWidget, lang: str) -> Optional[QWidget]:
    """Chain a0's row of next steps ([Open] buttons) when it exists; else the steps as plain lines."""
    try:
        from orchestrator.gui.next_step_widgets import StepsRow
    except ImportError:
        StepsRow = None
    if StepsRow is not None:
        try:
            return StepsRow(steps, parent=parent, lang=lang)
        except Exception:  # noqa: BLE001 - an error box never fails because of its next step
            pass
    lines = [str(s) for s in (steps if isinstance(steps, (list, tuple)) else [steps]) if str(s).strip()]
    return plain_label("\n".join(ui_text("words.error.next", lang, step=line) for line in lines), parent) \
        if lines else None


class ErrorDialog(QDialog):
    """The box: `summary` (what happened, localized), `why`, the next step (`steps`, `next_step` or `action` =
    (label, callable)), and `details` (English, redacted) to expand and copy. `kind`: critical, warning or
    information."""

    def __init__(self, parent: Optional[QWidget], summary: str, *, why: str = "", next_step: str = "",
                 steps: Any = None, action: Optional[Tuple[str, Callable[[], Any]]] = None, details: str = "",
                 title: Optional[str] = None, kind: str = "critical", lang: Optional[str] = None,
                 details_open: bool = False):
        super().__init__(parent if isinstance(parent, QWidget) else None)
        self.lang = lang or current_language()
        self.setObjectName("error_dialog")
        self.setWindowTitle(title or ui_text("words.error.title", self.lang))
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        icon = QLabel(self)
        icon.setPixmap(self.style().standardIcon(ICONS.get(kind, ICONS["critical"])).pixmap(32, 32))
        icon.setAlignment(Qt.AlignTop)
        top.addWidget(icon)
        self.summary = plain_label(summary or ui_text("words.error.title", self.lang), self)
        self.summary.setObjectName("error_summary")
        font = QFont(self.summary.font())
        font.setBold(True)
        self.summary.setFont(font)
        top.addWidget(self.summary, 1)
        layout.addLayout(top)
        self.why = plain_label(why, self)
        self.why.setObjectName("error_why")
        self.why.setVisible(bool(why))
        layout.addWidget(self.why)
        self.next = None
        if steps:
            self.next = _steps_widget(steps, self, self.lang)
        elif next_step:
            self.next = plain_label(ui_text("words.error.next", self.lang, step=next_step), self)
        if self.next is not None:
            self.next.setObjectName("error_next")
            layout.addWidget(self.next)
        self.btn_action = None
        if action is not None:
            label, slot = action
            self.btn_action = QPushButton(mnemonic_text(label), self)
            self.btn_action.setObjectName("btn_error_action")
            self.btn_action.setAutoDefault(False)
            self.btn_action.clicked.connect(lambda _=False: (self.accept(), slot()))
            row = QHBoxLayout()
            row.addWidget(self.btn_action)
            row.addStretch(1)
            layout.addLayout(row)
        self.details_text = technical_details(details)[-DETAILS_LIMIT:] if details else ""
        self.btn_details = self.btn_copy = self.details = self.details_hint = self.copied = None
        if self.details_text:
            self._details_part(layout, details_open)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)
        close = buttons.button(QDialogButtonBox.Close)
        close.setDefault(True)
        close.setFocus()
        layout.addWidget(buttons)

    def _details_part(self, layout: QVBoxLayout, details_open: bool) -> None:
        row = QHBoxLayout()
        self.btn_details = QToolButton(self)
        self.btn_details.setObjectName("btn_error_details")
        self.btn_details.setText(button_text(ui_text("words.error.details", self.lang)))
        self.btn_details.setCheckable(True)
        self.btn_details.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.btn_details.setFocusPolicy(Qt.StrongFocus)
        self.btn_details.toggled.connect(self._toggle_details)
        row.addWidget(self.btn_details)
        row.addStretch(1)
        self.copied = plain_label("", self)
        row.addWidget(self.copied)
        self.btn_copy = QPushButton(button_text(ui_text("words.error.copy", self.lang)), self)
        self.btn_copy.setObjectName("btn_error_copy")
        self.btn_copy.setAutoDefault(False)
        self.btn_copy.clicked.connect(self.copy_details)
        row.addWidget(self.btn_copy)
        layout.addLayout(row)
        self.details_hint = plain_label(ui_text("words.error.details_hint", self.lang), self)
        layout.addWidget(self.details_hint)
        self.details = QPlainTextEdit(self)
        self.details.setObjectName("error_details")
        self.details.setReadOnly(True)
        self.details.setPlainText(self.details_text)
        self.details.setAccessibleName(ui_text("words.error.details", self.lang))
        self.details.setFont(QFont("monospace"))
        self.details.setMinimumHeight(160)
        layout.addWidget(self.details, 1)
        self.btn_details.setChecked(details_open)
        self._toggle_details(details_open)

    def _toggle_details(self, shown: bool) -> None:
        self.btn_details.setArrowType(Qt.DownArrow if shown else Qt.RightArrow)
        self.details.setVisible(shown)
        self.details_hint.setVisible(shown)
        self.adjustSize()

    def copy_details(self) -> None:
        QApplication.clipboard().setText(self.details_text)
        self.copied.setText(ui_text("words.error.copied", self.lang))


def _exec(dialog: QDialog) -> int:
    """Shows the box modally (tests replace this to answer without a window)."""
    return dialog.exec()


def show_error(parent: Optional[QWidget], summary: str, **options: Any) -> int:
    """Shows the error box (options: why, next_step, steps, action, details, title, kind, lang, details_open) and
    returns the dialog's result."""
    return _exec(ErrorDialog(parent, summary, **options))


def show_exception(parent: Optional[QWidget], error: BaseException, summary: str = "", **options: Any) -> int:
    """The error box for `error`: `summary` (localized) first, the exception's English message and traceback as the
    technical details, its catalogue steps (`error.steps`) as the next step. Without a summary the details are open,
    so the facts are visible at once."""
    details = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    options.setdefault("steps", getattr(error, "steps", None))
    options.setdefault("details_open", not summary)
    return show_error(parent, summary, details=details, **options)


__all__ = ["DETAILS_LIMIT", "ErrorDialog", "show_error", "show_exception"]
