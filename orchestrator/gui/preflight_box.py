#!/usr/bin/env python3
"""
The question after the pre-flight check before a build stopped it (Core/consistency_manager.py Preflight): every
finding in plain text, then Build anyway (or Continue anyway) and Cancel, Cancel the default. Only the person answers
it, for one build; nothing remembers the answer. The Builder's Start Build and the Continue of an interrupted build
(Build > Build History...) ask it; the CLI asks the same at the terminal (build start, build interrupted continue).
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QWidget

from orchestrator.utils.localization import ui_text


def ask_preflight(parent, result, anyway_key: str = "bld.preflight.anyway") -> bool:
    """True only when the person goes on anyway, for this one build."""
    box = QMessageBox(parent if isinstance(parent, QWidget) else None)
    box.setWindowTitle(ui_text("bld.preflight.title"))
    box.setIcon(QMessageBox.Warning)
    box.setTextFormat(Qt.PlainText)
    box.setText(ui_text("bld.preflight.question", lines="\n".join(result.lines())))
    box.setObjectName("box_builder_preflight")
    anyway = box.addButton(ui_text(anyway_key), QMessageBox.DestructiveRole)
    cancel = box.addButton(QMessageBox.Cancel)
    box.setDefaultButton(cancel)
    box.exec()
    return box.clickedButton() is anyway
