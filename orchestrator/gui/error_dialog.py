#!/usr/bin/env python3
"""
The error box for the person (wave 6, f-words; DECISIONS round 10, Q7).

What happened, in the person's language and in plain words first; why, when it is known; ONE next step: catalogue
steps (chain a0's StepsRow with its [Open] buttons), a plain sentence, or one button. The English technical detail
(the framework's message, a traceback) is folded away under "Technical details", can be expanded and copied, with
secrets redacted and the home folder written as "~" (utils/wording.technical_details): the person can paste it into a
bug report or ask Ditto, who reads English and explains it in German. Every text is plain text, never HTML from data,
and no text of the box shows a secret (the summary, the reason and the steps are redacted too: they are often made
from an error's message). A catalogue step is shown in the GUI's words (a mouse path), never as a command or object.

    show_error(parent, ui_text("build.failed.summary"), why=..., next_step=..., details=str(e))
    show_exception(parent, e, ui_text("deploy.failed"))      # details = message + traceback, steps = e.steps
    plain.plain_error(parent, what, why, steps=None, action=None)   # the same box, the design's name
"""

import traceback
from typing import Any, Callable, List, Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPlainTextEdit,
                               QPushButton, QStyle, QToolButton, QVBoxLayout, QWidget)

from orchestrator.gui.plain import button_text, mnemonic_text, plain_label
from orchestrator.utils.helpers import redact_secrets
from orchestrator.utils.localization import current_language, ui_text
from orchestrator.utils.wording import technical_details

DETAILS_LIMIT = 20000   # the end of a longer detail is kept (a traceback's last lines matter most)
ICONS = {"critical": QStyle.SP_MessageBoxCritical, "warning": QStyle.SP_MessageBoxWarning,
         "information": QStyle.SP_MessageBoxInformation}


def _shown(text: Any) -> str:
    """A text of the box as the person sees it: never a secret, even when it was made from an error's message."""
    return redact_secrets(str(text or ""))


def _known_steps() -> Any:
    """The step ids of chain a0's catalogue (empty without it)."""
    try:
        from orchestrator.utils.next_steps import catalogue
        return catalogue().steps
    except Exception:  # noqa: BLE001 - without the catalogue every text is a sentence
        return {}


def _as_list(steps: Any) -> List[Any]:
    if isinstance(steps, tuple) and len(steps) == 2 and isinstance(steps[0], str) and isinstance(steps[1], dict):
        return [steps]   # one (id, values) pair, not two steps
    return list(steps) if isinstance(steps, (list, tuple)) else [steps]


def _is_catalogue_step(step: Any, known: Any) -> bool:
    """A step of chain a0's catalogue: a NextStep, an (id, values) pair, or a plain str that is exactly a known step
    id. A sentence (a0's Line included: it is its text) is not one."""
    if isinstance(step, str):
        return type(step) is str and step in known
    return step is not None


def _step_lines(steps: Any, lang: str) -> List[str]:
    """Each step as one line for the person, in the order given: a plain sentence as it is (a0's Line is text too,
    and its own steps follow it); a catalogue step (chain a0's NextStep, or its id) in the GUI's words through a0's
    renderer (a mouse path, never a command). A step that no renderer can show is left out rather than shown as its
    Python repr or its id."""
    try:
        from orchestrator.utils.next_steps import rendered
    except ImportError:
        rendered = None
    known = _known_steps()
    lines: List[str] = []

    def add(text: str) -> None:
        if text.strip():
            lines.append(_shown(text.strip()))

    def add_rendered(step: Any) -> None:
        if rendered is None:
            return
        try:
            add("\n".join(r.text for r in rendered([step], "gui", lang)))
        except Exception:  # noqa: BLE001 - an error box never fails because of its next step
            pass

    for step in _as_list(steps):
        if _is_catalogue_step(step, known):
            add_rendered(step)
        elif isinstance(step, str):
            add(step)
            for carried in getattr(step, "steps", ()) or ():
                add_rendered(carried)
    return lines


def _steps_widget(steps: Any, parent: QWidget, lang: str) -> Optional[QWidget]:
    """The next step part of the box. With chain a0's StepsRow: the plain sentences as "Next step: ..." lines, then
    the catalogue steps (a Line's own steps too) under a "Next step:" heading in a StepsRow, each with its mouse path
    and an [Open] button. StepsRow reads step ids only, so a sentence never goes there (it would vanish). Without
    StepsRow, or when it shows nothing, every step is a plain line in the order given."""
    known = _known_steps()
    items = _as_list(steps)
    sentences = [s for s in items if isinstance(s, str) and not _is_catalogue_step(s, known)]
    found = [s for s in items if _is_catalogue_step(s, known)]
    found += [carried for s in sentences for carried in (getattr(s, "steps", ()) or ())]
    try:
        from orchestrator.gui.next_step_widgets import StepsRow
    except ImportError:
        StepsRow = None
    row = None
    if found and StepsRow is not None:
        try:
            row = StepsRow(found, parent=parent, lang=lang)
        except Exception:  # noqa: BLE001 - an error box never fails because of its next step
            row = None
        if row is not None and not row.rows:   # no step the catalogue knows (an older or newer id)
            row.deleteLater()
            row = None
    lines = _step_lines([str(s) for s in sentences] if row is not None else items, lang)
    text = "\n".join(ui_text("words.error.next", lang, step=line) for line in lines)
    if row is None:
        return plain_label(text, parent) if lines else None
    part = QWidget(parent)
    layout = QVBoxLayout(part)
    layout.setContentsMargins(0, 0, 0, 0)
    part.lines = None
    if lines:
        part.lines = plain_label(text, part)
        part.lines.setObjectName("error_next_lines")
        layout.addWidget(part.lines)
    part.heading = plain_label(ui_text("words.error.next_heading", lang), part)
    part.heading.setObjectName("error_next_heading")
    layout.addWidget(part.heading)
    part.row = row
    row.setParent(part)
    layout.addWidget(row)
    return part


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
        self.setWindowTitle(_shown(title) or ui_text("words.error.title", self.lang))
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        icon = QLabel(self)
        icon.setPixmap(self.style().standardIcon(ICONS.get(kind, ICONS["critical"])).pixmap(32, 32))
        icon.setAlignment(Qt.AlignTop)
        top.addWidget(icon)
        self.summary = plain_label(_shown(summary) or ui_text("words.error.title", self.lang), self)
        self.summary.setObjectName("error_summary")
        font = QFont(self.summary.font())
        font.setBold(True)
        self.summary.setFont(font)
        top.addWidget(self.summary, 1)
        layout.addLayout(top)
        self.why = plain_label(_shown(why), self)
        self.why.setObjectName("error_why")
        self.why.setVisible(bool(why))
        layout.addWidget(self.why)
        self.next = None
        if steps:
            self.next = _steps_widget(steps, self, self.lang)
        elif next_step:
            self.next = plain_label(ui_text("words.error.next", self.lang, step=_shown(next_step)), self)
        if self.next is not None:
            self.next.setObjectName("error_next")
            layout.addWidget(self.next)
        self.btn_action = None
        if action is not None:
            label, slot = action
            self.btn_action = QPushButton(mnemonic_text(_shown(label)), self)
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
