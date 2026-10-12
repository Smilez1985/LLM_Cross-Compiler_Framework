#!/usr/bin/env python3
"""
The dock that holds the chat panel (delegation design 5.1): on the right of the main window by default, movable to
the left, floatable and closable, 380 px wide at first and never narrower than 300 px. F9 shows or hides it (its
toggle action, placed in the View menu by the main window). A question that arrives shows a hidden dock without
taking the keyboard focus away from what the person is doing.
"""

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QDockWidget, QWidget

from orchestrator.gui.chat_panel.bubbles import text

OBJECT_NAME = "chatDock"
DEFAULT_WIDTH = 380
MIN_WIDTH = 300
TOGGLE_SHORTCUT = "F9"


class ChatDock(QDockWidget):
    def __init__(self, panel: QWidget, parent: Optional[QWidget] = None, lang: str = "en"):
        super().__init__(text("chat.panel.title", lang), parent)
        self.setObjectName(OBJECT_NAME)
        self.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable
                         | QDockWidget.DockWidgetClosable)
        panel.setMinimumWidth(MIN_WIDTH)
        self.setWidget(panel)
        self.panel = panel
        toggle = self.toggleViewAction()
        toggle.setText(text("chat.view.toggle", lang))
        toggle.setShortcut(QKeySequence(TOGGLE_SHORTCUT))
        toggle.setShortcutContext(Qt.ApplicationShortcut)

    def show_without_focus(self) -> bool:
        """Shows a hidden dock without activating it (a new question); False when it was shown already."""
        if not self.isHidden():
            return False
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.show()
        self.raise_()
        return True

    def retranslate(self, lang: str) -> None:
        self.setWindowTitle(text("chat.panel.title", lang))
        self.toggleViewAction().setText(text("chat.view.toggle", lang))


__all__ = ["ChatDock", "OBJECT_NAME", "DEFAULT_WIDTH", "MIN_WIDTH", "TOGGLE_SHORTCUT"]
