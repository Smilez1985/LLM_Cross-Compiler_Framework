#!/usr/bin/env python3
"""
Build > F16 Intermediates...: the kept F16 intermediates of GGUF builds (orchestrator/Core/f16_cache.py) by mouse, as
`llm-cli build f16 list | delete`. Every kept file with its model, commit, llama.cpp pin, size and when a build last
used it; Delete... with the full list of the deletion dialog (Core/deletion.py: nothing ticked, No is the default).
"""

from typing import Any, Optional

from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout)

from orchestrator.gui.plain import plain_box
from orchestrator.utils.localization import ui_text


class F16IntermediatesDialog(QDialog):
    def __init__(self, framework: Any, parent: Optional[Any] = None):
        super().__init__(parent)
        from orchestrator.Core.f16_cache import F16Cache
        self.framework = framework
        self.cache = F16Cache.for_framework(framework)
        self.setWindowTitle(ui_text("bld.f16.title"))
        self.setObjectName("dlg_build_f16")
        layout = QVBoxLayout(self)
        intro = QLabel(ui_text("bld.f16.intro"))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.table = QTableWidget(0, 6)
        self.table.setObjectName("tbl_build_f16")
        self.table.setHorizontalHeaderLabels([ui_text(f"bld.f16.col.{c}") for c in
                                              ("model", "commit", "pin", "size", "kept", "used")])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.table)
        self.status = QLabel("")
        self.status.setObjectName("lbl_build_f16_status")
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.btn_delete = QPushButton(ui_text("bld.f16.delete"))
        self.btn_delete.setObjectName("btn_f16_delete")
        self.btn_delete.clicked.connect(self.delete_entries)
        close = QPushButton(ui_text("bld.f16.close"))
        close.clicked.connect(self.accept)
        buttons.addWidget(self.btn_delete)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.resize(900, 320)
        self.reload()

    def reload(self) -> None:
        from orchestrator.Core.deletion import size_text
        from orchestrator.Core.f16_cache import setting_on
        entries = self.cache.entries()
        self.table.setRowCount(len(entries))
        for row, e in enumerate(entries):
            values = (e.repo, e.commit[:12], e.pin[:12], size_text(e.size_bytes), e.kept[:10], e.last_used[:10])
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.table.resizeColumnsToContents()
        text = ui_text("bld.f16.total", count=len(entries), size=size_text(self.cache.total_bytes())) if entries \
            else ui_text("bld.f16.none")
        if not setting_on(getattr(self.framework, "config", None)):
            text += " " + ui_text("bld.f16.off")
        self.status.setText(text)
        self.btn_delete.setEnabled(bool(entries))

    def delete_entries(self) -> Optional[Any]:
        """Delete...: the kept files through the deletion dialog; the trash or for good, as the CLI."""
        from orchestrator.Core.deletion import PlanChanged
        from orchestrator.Core.trash import Trash
        from orchestrator.gui.deletion_dialog import ask_deletion
        plan = self.cache.deletion_plan()
        if plan is None:
            plain_box(self, "information", ui_text("bld.f16.title"), ui_text("bld.f16.none"))
            return None
        accepted = ask_deletion(self, plan, multiple=True, title=ui_text("bld.f16.title"))
        if accepted is None:
            return None
        try:
            result = self.cache.delete(accepted, trash=Trash.for_framework(self.framework),
                                       audit=getattr(self.framework, "audit", None)
                                       or getattr(getattr(self.framework, "tool_registry", None), "audit", None))
        except (PlanChanged, PermissionError, RuntimeError) as e:
            plain_box(self, "warning", ui_text("bld.f16.title"), str(e))
            return None
        self.reload()
        self.status.setText(ui_text("bld.f16.deleted", count=len(result.done)))
        return result


__all__ = ["F16IntermediatesDialog"]
