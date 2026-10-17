#!/usr/bin/env python3
"""
Empty states (wave 6, f-words; design C.5): instead of an empty table, one plain sentence that says why it is empty
and ONE button for the next step ("No boards yet." + "Scan for a board...").

    empty = EmptyState(ui_text("boards.empty"), ui_text("boards.empty.scan"), self._scan)
    stack = EmptyStateStack(self.table, empty)       # shows the table, or the empty state
    stack.set_empty(not rows)

The button keeps the mnemonic of its label (plain.mnemonic_text); the sentence is plain text.
"""

from typing import Any, Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from orchestrator.gui.plain import mnemonic_text, plain_label


class EmptyState(QWidget):
    """One sentence and, when `button_text` and `slot` are given, one button."""

    def __init__(self, sentence: str, button_text: Optional[str] = None, slot: Optional[Callable[[], Any]] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("empty_state")
        layout = QVBoxLayout(self)
        layout.addStretch(1)
        self.sentence = plain_label(sentence, self)
        self.sentence.setObjectName("empty_state_sentence")
        self.sentence.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.sentence)
        row = QHBoxLayout()
        row.addStretch(1)
        self.button = QPushButton(self)
        self.button.setObjectName("btn_empty_state")
        self.button.setAutoDefault(False)
        row.addWidget(self.button)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)
        self._slot: Optional[Callable[[], Any]] = None
        self.button.clicked.connect(self._clicked)
        self.set_action(button_text, slot)

    def set_sentence(self, sentence: str) -> None:
        self.sentence.setText(sentence)

    def set_action(self, button_text: Optional[str], slot: Optional[Callable[[], Any]]) -> None:
        """The one next step: the button's label and what it does (None hides the button)."""
        self._slot = slot
        self.button.setText(mnemonic_text(button_text or ""))
        self.button.setVisible(bool(button_text) and slot is not None)

    def _clicked(self) -> None:
        if self._slot is not None:
            self._slot()


class EmptyStateStack(QStackedWidget):
    """`content` (a table, a list) or `empty` in the same place."""

    def __init__(self, content: QWidget, empty: EmptyState, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.content, self.empty = content, empty
        self.addWidget(content)
        self.addWidget(empty)

    def set_empty(self, empty: bool) -> None:
        self.setCurrentWidget(self.empty if empty else self.content)

    def is_empty(self) -> bool:
        return self.currentWidget() is self.empty


__all__ = ["EmptyState", "EmptyStateStack"]
