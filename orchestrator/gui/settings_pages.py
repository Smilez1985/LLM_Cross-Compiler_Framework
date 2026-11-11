#!/usr/bin/env python3
"""
The pieces of File > Settings... that make existing windows its pages (the one settings window, see
docs/DESIGN.md "GUI and the person's path").

* `PageList`        the window's pages: their names in a list on the left, the chosen page beside it. It answers the
                    part of QTabWidget's interface the window and its callers use (addTab, count, widget, tabText,
                    indexOf, currentIndex, currentWidget, setCurrentIndex, setCurrentWidget, currentChanged), so the
                    pages are found the same way they were when they were tabs.
* `LazyPage`        a page (or a section of one) built when it is first shown: opening the window for the language
                    reads no vault and calls no provider.
* `embed(dialog, on_done)`
                    makes an existing dialog a page, moved rather than copied: no frame of its own, its Close and
                    Cancel buttons hidden, and its accept or reject handed to `on_done(result)` instead of closing it
                    (Save saves and keeps the page open, Escape closes the window). A dialog class calls
                    `page_done(self, result)` first in its done().
* `release(dialog)` ends an embedded page when the window closes: the dialog finishes as it did as a window (what was
                    typed into a secret field and not stored is forgotten, a GUI form leaves the surface).
"""

from typing import Any, Callable, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QListWidget, QListWidgetItem, QStackedWidget,
                               QVBoxLayout, QWidget)

from orchestrator.gui.plain import one_line, plain_label

_ON_DONE = "_settings_page_done"


def embed(dialog: QDialog, on_done: Callable[[int], None]) -> QDialog:
    """`dialog` as a page of the settings window (see the module text); returns it."""
    dialog.setWindowFlags(Qt.Widget)
    setattr(dialog, _ON_DONE, on_done)
    for box in dialog.findChildren(QDialogButtonBox, options=Qt.FindDirectChildrenOnly):
        shown = 0
        for button in box.buttons():
            if box.buttonRole(button) == QDialogButtonBox.RejectRole:
                button.hide()
            else:
                shown += 1
        if not shown:
            box.hide()
    dialog.show()
    return dialog


def embedded(dialog: Any) -> bool:
    return callable(getattr(dialog, _ON_DONE, None))


def page_done(dialog: Any, result: int) -> bool:
    """Called first by an embeddable dialog's done(): True when the dialog is a page, which then stays as it is and
    the window decides (the caller returns without closing)."""
    on_done = getattr(dialog, _ON_DONE, None)
    if not callable(on_done):
        return False
    on_done(result)
    return True


def release(dialog: Any) -> None:
    """The window closes: the page stops being a page and finishes as its dialog did (Rejected)."""
    if not embedded(dialog):
        return
    setattr(dialog, _ON_DONE, None)
    try:
        dialog.done(QDialog.Rejected)
    except RuntimeError:   # already destroyed with its window
        pass


class LazyPage(QWidget):
    """A page whose content `build(page)` makes on the first ensure(); a failure is shown as plain text."""

    def __init__(self, name: str, build: Callable[["LazyPage"], QWidget], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName(name)
        self._build = build
        self.content: Optional[QWidget] = None
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)

    @property
    def built(self) -> bool:
        return self.content is not None

    def ensure(self) -> QWidget:
        if self.content is None:
            try:
                self.content = self._build(self)
            except Exception as e:  # noqa: BLE001 - e.g. no per-user folder or no tool registry; plain text (SEC-M2)
                self.content = plain_label(one_line(str(e)) or type(e).__name__, self)
                self.content.setWordWrap(True)
            self.layout_.addWidget(self.content)
        return self.content


class PageList(QWidget):
    """The page names in a list and the pages in a stack, with QTabWidget's interface (see the module text)."""

    currentChanged = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None, name: str = "list_settings_pages"):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget(self)
        self.list.setObjectName(name)
        self.list.setMaximumWidth(240)
        self.stack = QStackedWidget(self)
        layout.addWidget(self.list)
        layout.addWidget(self.stack, 1)
        self.list.currentRowChanged.connect(self._row_changed)

    def _row_changed(self, row: int) -> None:
        if 0 <= row < self.stack.count():
            self.stack.setCurrentIndex(row)
            self.currentChanged.emit(row)

    def addTab(self, widget: QWidget, text: str) -> int:
        index = self.stack.addWidget(widget)
        self.list.addItem(QListWidgetItem(text))
        if self.list.currentRow() < 0:
            self.list.setCurrentRow(0)
        return index

    def count(self) -> int:
        return self.stack.count()

    def widget(self, index: int) -> Optional[QWidget]:
        return self.stack.widget(index)

    def tabText(self, index: int) -> str:
        item = self.list.item(index)
        return item.text() if item is not None else ""

    def setTabText(self, index: int, text: str) -> None:
        item = self.list.item(index)
        if item is not None:
            item.setText(text)

    def indexOf(self, widget: QWidget) -> int:
        return self.stack.indexOf(widget)

    def currentIndex(self) -> int:
        return self.stack.currentIndex()

    def currentWidget(self) -> Optional[QWidget]:
        return self.stack.currentWidget()

    def setCurrentIndex(self, index: int) -> None:
        if 0 <= index < self.count():
            self.list.setCurrentRow(index)

    def setCurrentWidget(self, widget: QWidget) -> None:
        self.setCurrentIndex(self.indexOf(widget))

    def pages(self) -> List[QWidget]:
        return [self.stack.widget(i) for i in range(self.count())]


__all__ = ["LazyPage", "PageList", "embed", "embedded", "page_done", "release"]
