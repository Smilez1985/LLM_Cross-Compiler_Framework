#!/usr/bin/env python3
"""
The next steps of a message in a window (wave 6, design A.5 and A.7; chain a0).

StepsRow shows each step of the catalogue (orchestrator/utils/next_steps) in the person's language: what to do and
where (the menu path from the live labels, only the button's name inside its own window), with [Open], which goes
there; a board's, an administrator's or another program's command with [Copy] (the framework never runs those); a
page with [Open page] (opened only on the person's click). Every button is focusable, has an accessible name and
takes no focus when the row appears.

open_object(window, object_name) is what [Open] (and a chat row's [Show]) does: it triggers a menu entry, opens the
window a button lives in and focuses the button, switches to the main window's tab an object lives in. It never
clicks anything inside a window for the person: the person sees the place and clicks there.

A step the framework may run after the person's yes (the catalogue's `runnable`, a framework command that needs no
terminal) also gets [Run...]: it opens the allow-to-run question (gui/allow_command.py: "Allow `llmcf ...`? ...",
No as the default, the output in the question and in the Ditto console). A deleting step, a step that needs a
terminal and a board's or administrator's command never get [Run...].
"""

from typing import Any, List, Optional

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractButton, QApplication, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget)

from orchestrator.utils.localization import current_language, ui_text



def _t(key: str, lang: str, **values: Any) -> str:
    """The row's texts live in l10n_main (main.steps.*)."""
    return ui_text(f"main.{key}", lang, **values)


def main_window() -> Optional[QWidget]:
    """The framework's main window (the top-level window with the menu bar and the tabs), None when none is open."""
    for widget in QApplication.topLevelWidgets():
        if hasattr(widget, "menuBar") and hasattr(widget, "tabs") and hasattr(widget, "framework"):
            return widget
    return None


def open_object(window: Any, object_name: str) -> bool:
    """Goes to the place of `object_name` in the main window `window`: triggers its menu entry; for an object inside a
    window opens that window (through its entry) and focuses the object; for an object on a tab switches to the tab
    and focuses it (an entry of a button's menu: the button). True when the place was found."""
    from orchestrator.gui.menus import place
    from orchestrator.utils.next_steps import mouse_labels
    if window is None or not object_name:
        return False
    menubar = window.menuBar()
    entry = place.find_entry(menubar, object_name)
    if entry is not None:
        entry.trigger()
        return True
    labels = mouse_labels()
    chain: List[str] = []
    current: Optional[str] = object_name
    while current is not None and current in labels and current not in chain:
        chain.append(current)
        current = labels[current][0]
    tab = next((name for name in chain if name in place.TAB_TITLES), None)
    if tab is not None:
        widget = getattr(window, tab, None)
        if widget is None:
            return False
        window.tabs.setCurrentWidget(widget)
        found = widget.findChild(QObject, object_name)
        _focus(found if isinstance(found, QWidget) else _holder(widget, found, chain))
        return True
    opener = next((name for name in chain[1:] if place.find_entry(menubar, name) is not None), None)
    if opener is None:
        return False
    before = set(map(id, QApplication.topLevelWidgets()))
    place.find_entry(menubar, opener).trigger()
    QApplication.processEvents()
    candidates = [w for w in QApplication.topLevelWidgets() if w.isVisible()]
    candidates.sort(key=lambda w: id(w) in before)   # a window the entry just opened first
    for top in [*candidates, window]:
        found = top.findChild(QObject, object_name)
        if found is not None:
            _focus(found if isinstance(found, QWidget) else None)
            return True
    return True


def _holder(container: QWidget, found: Any, chain: List[str]) -> Optional[QWidget]:
    """The widget the person clicks for an action inside a tab: the button whose menu holds it."""
    for name in chain[1:]:
        widget = container.findChild(QWidget, name)
        if widget is not None:
            return widget
    return None


def _focus(widget: Optional[QWidget]) -> None:
    if widget is not None:
        top = widget.window()
        if top is not None and not top.isVisible():
            top.show()
        widget.setFocus(Qt.OtherFocusReason)


class StepsRow(QWidget):
    """The next steps of a message (see the module text). `here`: the window the row is shown in (a WINDOW_IDS id or
    the objectName that opens it), so a step there names only its button. `window`: the main window [Open] goes to
    (default: the open main window). Signals: open_requested(objectName) after [Open], copied(text) after [Copy]."""
    open_requested = Signal(str)
    copied = Signal(str)
    run_requested = Signal(str)   # the step id, after [Run...]

    def __init__(self, steps: Any, parent: Optional[QWidget] = None, lang: Optional[str] = None,
                 here: Optional[str] = None, window: Any = None):
        super().__init__(parent)
        from orchestrator.utils.next_steps import catalogue, rendered, steps_of
        self.lang = lang or current_language()
        self._window = window
        self.setObjectName("steps_row")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.rows = rendered(steps, "gui", self.lang, here=here)
        known = catalogue().steps
        self.next_steps = [s for s in steps_of(steps) if s.id in known]   # the same order as self.rows
        self.dialog: Any = None
        self.buttons: List[QAbstractButton] = []
        for row, next_step in zip(self.rows, self.next_steps):
            line = QHBoxLayout()
            label = QLabel(row.text, self)
            label.setTextFormat(Qt.PlainText)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            line.addWidget(label, 1)
            if row.target:
                line.addWidget(self._button("steps.open", row.step_id, row.text,
                                            lambda _=False, t=row.target: self._open(t)))
            if row.runnable and row.command and not row.person_only and not row.planned:
                button = self._button("steps.open", row.step_id, row.text,
                                      lambda _=False, s=next_step: self._run(s))
                button.setText(ui_text("console.steps.run", self.lang))
                button.setObjectName(f"btn_step_run_{row.step_id.replace('.', '_')}")
                button.setAccessibleName(ui_text("console.steps.run.name", self.lang, step=row.text))
                button.setToolTip(ui_text("console.steps.run.name", self.lang, step=row.text))
                line.addWidget(button)
            if row.copy:
                line.addWidget(self._button("steps.copy", row.step_id, row.text,
                                            lambda _=False, c=row.copy: self._copy(c)))
            if row.url:
                line.addWidget(self._button("steps.page", row.step_id, row.text,
                                            lambda _=False, u=row.url: QDesktopServices.openUrl(QUrl(u))))
            layout.addLayout(line)

    def _button(self, key: str, step_id: str, text: str, slot) -> QPushButton:
        button = QPushButton(_t(key, self.lang), self)
        button.setObjectName(f"btn_step_{key.split('.')[-1]}_{step_id.replace('.', '_')}")
        button.setAccessibleName(_t(f"{key}.name", self.lang, step=text))
        button.setToolTip(_t(f"{key}.name", self.lang, step=text))
        button.setAutoDefault(False)
        button.setFocusPolicy(Qt.StrongFocus)
        button.clicked.connect(slot)
        self.buttons.append(button)
        return button

    def _open(self, target: str) -> None:
        self.open_requested.emit(target)
        open_object(self._window or main_window(), target)

    def _run(self, next_step: Any) -> None:
        """[Run...]: the allow-to-run question; nothing runs before the person's Yes there."""
        from orchestrator.gui.allow_command import ask_to_run
        self.run_requested.emit(next_step.id)
        self.dialog = ask_to_run(self._window or main_window(), next_step, parent=self.window(), lang=self.lang)

    def _copy(self, text: str) -> None:
        QApplication.clipboard().setText(text)
        self.copied.emit(text)


__all__ = ["StepsRow", "main_window", "open_object"]
