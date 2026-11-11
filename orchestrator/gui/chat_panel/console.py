#!/usr/bin/env python3
"""
The Ditto console beside the chat (DESIGN "GUI and the person's path", the row "Beside the chat sits the Ditto
console"): what Ditto and external AI apps type and do, live and in order, each line marked with its source.

* `ConsoleDock` is a dock of its own (object name consoleDock), which the main window places beside the chat dock with
  splitDockWidget: Qt's separator between the two resizes them by dragging, and saveState() keeps the sizes in
  gui_state.json. Shift+F9 (its toggle action, in the View menu) and the toolbar's Console button show or hide it.
* `DittoConsole` reads through Core/ditto_console.py (the chat feed and the audit log, never writing to either) on a
  thread of its own (ConsoleFollower); this process's activity rows come at once through the activity producer's
  listener. A timer takes what arrived four times a second and shows it as one batch, so a burst never freezes the
  window; the console keeps the newest MAX_LINES lines (a ring buffer).
* Show: everything, Ditto, all AI apps or one app; Search: the lines that contain a text; Pause Scrolling stops
  following the newest line; Clear View empties the view and its buffer only (never a record); Copy copies the
  selected lines, or every shown line when nothing is selected.

Every text is plain text (a QPlainTextEdit, never rich text); buttons have no mnemonics (button_text).
"""

from collections import deque
from typing import Any, Deque, List, Optional

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QFontDatabase, QGuiApplication, QKeySequence, QTextCursor
from PySide6.QtWidgets import (QComboBox, QDockWidget, QHBoxLayout, QLineEdit, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget)

from orchestrator.Core.ditto_console import (FIRST_LOOK, MAX_LINES, POLL_SECONDS, SOURCES, ConsoleFollower,
                                             ConsoleLine, ConsoleReader, SeenKeys, line_of_feed, matches)
from orchestrator.Core.feed_words import text
from orchestrator.gui.plain import button_text, plain_label, plain_tooltip

OBJECT_NAME = "consoleDock"
DEFAULT_WIDTH = 420
MIN_WIDTH = 280
TOGGLE_SHORTCUT = "Shift+F9"
BATCH_MS = 250          # how often what arrived is shown, as one batch


class DittoConsole(QWidget):
    """The console's contents. `framework` gives the chat feed, the audit log and the activity producer; without one
    (tests) `reader` is used as given. `start` False builds it without the reader thread."""

    lines_shown = Signal(int)   # how many lines one batch added (tests, screen readers)

    def __init__(self, framework: Any = None, lang: str = "en", parent: Optional[QWidget] = None, *,
                 reader: Optional[ConsoleReader] = None, start: bool = True, interval: float = POLL_SECONDS):
        super().__init__(parent)
        self.setObjectName("ditto_console")
        self.lang = lang
        self.framework = framework
        self.buffer: Deque[ConsoleLine] = deque(maxlen=MAX_LINES)
        self.seen = SeenKeys()
        self.apps: List[str] = []      # caller ids of the apps seen, in the order they appeared
        self._remove_listener = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        bar = QHBoxLayout()
        self.lbl_source = plain_label(text("console.show", lang), wrap=False, selectable=False)
        bar.addWidget(self.lbl_source)
        self.cb_source = QComboBox()
        self.cb_source.setObjectName("cb_console_source")
        for source in SOURCES:
            self.cb_source.addItem(text(f"console.source.{source}", lang), source)
        self.cb_source.currentIndexChanged.connect(self.refilter)
        bar.addWidget(self.cb_source)
        self.lbl_search = plain_label(text("console.search", lang), wrap=False, selectable=False)
        bar.addWidget(self.lbl_search)
        self.txt_search = QLineEdit()
        self.txt_search.setObjectName("txt_console_search")
        self.txt_search.setPlaceholderText(text("console.search.placeholder", lang))
        self.txt_search.setClearButtonEnabled(True)
        self.txt_search.textChanged.connect(self.refilter)
        bar.addWidget(self.txt_search, stretch=1)
        layout.addLayout(bar)

        buttons = QHBoxLayout()
        self.btn_pause = QPushButton(button_text(text("console.pause", lang)))
        self.btn_pause.setObjectName("btn_console_pause")
        self.btn_pause.setCheckable(True)
        self.btn_pause.setToolTip(plain_tooltip(text("console.pause.tip", lang)))
        self.btn_pause.toggled.connect(self._pause_toggled)
        self.btn_clear = QPushButton(button_text(text("console.clear", lang)))
        self.btn_clear.setObjectName("btn_console_clear")
        self.btn_clear.setToolTip(plain_tooltip(text("console.clear.tip", lang)))
        self.btn_clear.clicked.connect(self.clear_view)
        self.btn_copy = QPushButton(button_text(text("console.copy", lang)))
        self.btn_copy.setObjectName("btn_console_copy")
        self.btn_copy.setToolTip(plain_tooltip(text("console.copy.tip", lang)))
        self.btn_copy.clicked.connect(self.copy_lines)
        for button in (self.btn_pause, self.btn_clear, self.btn_copy):
            button.setAutoDefault(False)
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.view = QPlainTextEdit()
        self.view.setObjectName("txt_console")
        self.view.setReadOnly(True)
        self.view.setUndoRedoEnabled(False)
        self.view.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.view.setMaximumBlockCount(MAX_LINES)
        self.view.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.view.setPlaceholderText(text("console.empty", lang))
        self.view.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        layout.addWidget(self.view, stretch=1)
        self.lbl_status = plain_label("", wrap=True, selectable=True)
        self.lbl_status.setObjectName("lbl_console_status")
        self.lbl_status.setVisible(False)
        layout.addWidget(self.lbl_status)

        if reader is None:
            reader = ConsoleReader.of_framework(framework, lang)
        self.follower = ConsoleFollower(reader, interval=interval, first=FIRST_LOOK)
        self.timer = QTimer(self)
        self.timer.setInterval(BATCH_MS)
        self.timer.timeout.connect(self.take)
        activity = getattr(framework, "activity", None)
        if activity is not None and callable(getattr(activity, "add_listener", None)):
            self._remove_listener = activity.add_listener(self._activity_row)
        if start:
            self.start()

    # --- reading ---------------------------------------------------------------------------------------------------

    def start(self) -> None:
        self.follower.start()
        self.timer.start()

    def stop(self) -> None:
        """Stops the reader thread, the timer and the activity listener (the main window calls it when it closes)."""
        self.timer.stop()
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None
        self.follower.stop()

    def _activity_row(self, stored: Any) -> None:
        """This process's activity row, on the producer's thread: queued for the next batch (never touches Qt)."""
        try:
            line = line_of_feed(stored, self.lang)
        except Exception:  # noqa: BLE001 - the feed brings it at the next look anyway
            return
        if line is not None:
            self.follower.push([line])

    def take(self) -> int:
        """Shows what arrived since the last batch; returns how many lines were added to the view."""
        self._show_problems()
        new = [line for line in self.follower.take() if self.seen.first_time(line.key)]
        if not new:
            return 0
        new = ConsoleReader.ordered(new)
        self.buffer.extend(new)
        for line in new:
            if line.source == "app" and line.caller not in self.apps:
                self.apps.append(line.caller)
                self.cb_source.addItem(line.who, line.caller)
        shown = [line for line in new if self._matches(line)]
        if shown:
            self._append([line.shown(self.lang) for line in shown])
        self.lines_shown.emit(len(shown))
        return len(shown)

    def _show_problems(self) -> None:
        problems = self.follower.problems
        self.lbl_status.setText("\n".join(problems))
        self.lbl_status.setVisible(bool(problems))

    # --- the view --------------------------------------------------------------------------------------------------

    def source(self) -> str:
        return str(self.cb_source.currentData() or "all")

    def _matches(self, line: ConsoleLine) -> bool:
        return matches(line, self.source(), self.txt_search.text().strip(), self.lang)

    def _append(self, texts: List[str]) -> None:
        bar = self.view.verticalScrollBar()
        keep = bar.value()
        cursor = QTextCursor(self.view.document())
        cursor.movePosition(QTextCursor.End)
        if not self.view.document().isEmpty():
            cursor.insertBlock()
        cursor.insertText("\n".join(texts))
        if self.paused():
            bar.setValue(keep)
        else:
            bar.setValue(bar.maximum())

    def refilter(self, *_args: Any) -> None:
        """Shows the buffer again under the current filters."""
        self.view.clear()
        shown = [line.shown(self.lang) for line in self.buffer if self._matches(line)]
        if shown:
            self._append(shown)

    def paused(self) -> bool:
        return self.btn_pause.isChecked()

    def _pause_toggled(self, paused: bool) -> None:
        if not paused:
            bar = self.view.verticalScrollBar()
            bar.setValue(bar.maximum())

    def clear_view(self) -> None:
        """Empties the view and its buffer; the chat feed and the audit log keep every record. Lines read already stay
        out (they are remembered as seen), new ones come in."""
        self.buffer.clear()
        self.view.clear()

    def shown_text(self) -> str:
        return self.view.toPlainText()

    def copy_lines(self) -> int:
        """Copies the selected lines, or every shown line when nothing is selected; returns how many lines."""
        selected = self.view.textCursor().selectedText().replace(" ", "\n")
        copied = selected if selected else self.view.toPlainText()
        QGuiApplication.clipboard().setText(copied)
        count = len([part for part in copied.split("\n") if part]) if copied else 0
        self.lbl_status.setText(text("console.copied", self.lang, n=count))
        self.lbl_status.setVisible(True)
        return count


class ConsoleDock(QDockWidget):
    """The dock that holds the console beside the chat dock; closable, movable to the left, floatable."""

    def __init__(self, console: DittoConsole, parent: Optional[QWidget] = None, lang: str = "en"):
        super().__init__(text("console.title", lang), parent)
        self.setObjectName(OBJECT_NAME)
        self.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable
                         | QDockWidget.DockWidgetClosable)
        console.setMinimumWidth(MIN_WIDTH)
        self.setWidget(console)
        self.console = console
        toggle = self.toggleViewAction()
        toggle.setObjectName("act_view_console")   # mouse paths name it (MENU_LAYOUT)
        toggle.setText(text("console.view.toggle", lang))
        toggle.setShortcut(QKeySequence(TOGGLE_SHORTCUT))
        toggle.setShortcutContext(Qt.ApplicationShortcut)

    def retranslate(self, lang: str) -> None:
        self.setWindowTitle(text("console.title", lang))
        self.toggleViewAction().setText(text("console.view.toggle", lang))


__all__ = ["ConsoleDock", "DittoConsole", "OBJECT_NAME", "DEFAULT_WIDTH", "MIN_WIDTH", "TOGGLE_SHORTCUT"]
