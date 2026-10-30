#!/usr/bin/env python3
"""
Build > Build Dependencies...: the dependency cache of module CMake options (orchestrator/runtime/build/deps.py) by
mouse, as `llm-cli build deps list | fetch | delete`. Every dependency llmcf can fetch for a build with its source,
pin, whether it is in the cache and its recorded sha256; Fetch (online only, in the background), and Delete... with
the full list of the deletion dialog (Core/deletion.py: nothing ticked, No is the default).
"""

from typing import Any, Optional

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout)

from orchestrator.gui.plain import plain_box
from orchestrator.utils.localization import ui_text


class _FetchWorker(QThread):
    done = Signal(bool, str)

    def __init__(self, cache: Any, dep: Any, offline: bool):
        super().__init__()
        self.cache, self.dep, self.offline = cache, dep, offline

    def run(self) -> None:
        from orchestrator.runtime.build.deps import DependencyError
        try:
            entry = self.cache.ensure(self.dep, offline=self.offline, owner="gui")
        except DependencyError as e:
            self.done.emit(False, str(e))
            return
        self.done.emit(True, ui_text("bld.deps.fetched", name=self.dep.name, sha=entry.sha256[:16]))


class BuildDependenciesDialog(QDialog):
    def __init__(self, framework: Any, parent: Optional[Any] = None):
        super().__init__(parent)
        from orchestrator.runtime.build.deps import DependencyCache
        self.framework = framework
        self.cache = DependencyCache.for_framework(framework)
        self.worker: Optional[_FetchWorker] = None
        self.setWindowTitle(ui_text("bld.deps.title"))
        self.setObjectName("dlg_build_deps")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(ui_text("bld.deps.intro")))
        self.table = QTableWidget(0, 6)
        self.table.setObjectName("tbl_build_deps")
        self.table.setHorizontalHeaderLabels([ui_text(f"bld.deps.col.{c}") for c in
                                              ("name", "needed", "source", "pin", "cache", "sha")])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.table)
        self.status = QLabel("")
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.btn_fetch = QPushButton(ui_text("bld.deps.fetch"))
        self.btn_fetch.setObjectName("btn_build_deps_fetch")
        self.btn_fetch.clicked.connect(self.fetch_selected)
        self.btn_delete = QPushButton(ui_text("bld.deps.delete"))
        self.btn_delete.setObjectName("btn_build_deps_delete")
        self.btn_delete.clicked.connect(self.delete_entries)
        close = QPushButton(ui_text("bld.deps.close"))
        close.clicked.connect(self.accept)
        for b in (self.btn_fetch, self.btn_delete):
            buttons.addWidget(b)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.resize(900, 320)
        self.reload()

    def reload(self) -> None:
        from orchestrator.Core.deletion import size_text
        from orchestrator.runtime.build.deps import DEPENDENCIES
        self.deps = list(DEPENDENCIES.values())
        self.table.setRowCount(len(self.deps))
        for row, dep in enumerate(self.deps):
            entry = self.cache.entry(dep)
            values = (dep.name, f"-D{dep.option}=ON", dep.source, dep.ref,
                      ui_text("bld.deps.yes", size=size_text(entry.size_bytes)) if entry else ui_text("bld.deps.no"),
                      entry.sha256[:16] + "..." if entry else "-")
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.table.resizeColumnsToContents()

    def selected(self) -> Optional[Any]:
        row = self.table.currentRow()
        return self.deps[row] if 0 <= row < len(self.deps) else None

    def fetch_selected(self) -> bool:
        """Fetch: the selected dependency, in the background (never in offline mode: the plain reason instead)."""
        from orchestrator.utils.locality import offline_mode
        dep = self.selected()
        if dep is None or self.worker is not None:
            return False
        self.status.setText(ui_text("bld.deps.fetching", name=dep.name, source=dep.source))
        self.btn_fetch.setEnabled(False)
        self.worker = _FetchWorker(self.cache, dep, offline_mode(getattr(self.framework, "config", None)))
        self.worker.done.connect(self._fetched)
        self.worker.start()
        return True

    def _fetched(self, ok: bool, text: str) -> None:
        if self.worker is not None:
            self.worker.wait()
        self.worker = None
        self.btn_fetch.setEnabled(True)
        self.status.setText(text)
        if not ok:
            plain_box(self, "warning", ui_text("bld.deps.title"), text)
        self.reload()

    def delete_entries(self) -> Optional[Any]:
        """Delete...: the cache's entries through the deletion dialog; the trash or for good, as the CLI."""
        from orchestrator.Core.deletion import PlanChanged
        from orchestrator.Core.trash import Trash
        from orchestrator.gui.deletion_dialog import ask_deletion
        plan = self.cache.deletion_plan()
        if plan is None:
            plain_box(self, "information", ui_text("bld.deps.title"), ui_text("bld.deps.none"))
            return None
        accepted = ask_deletion(self, plan, multiple=True, title=ui_text("bld.deps.title"))
        if accepted is None:
            return None
        try:
            result = self.cache.delete(accepted, trash=Trash.for_framework(self.framework),
                                       audit=getattr(self.framework, "audit", None)
                                       or getattr(getattr(self.framework, "tool_registry", None), "audit", None))
        except (PlanChanged, PermissionError, RuntimeError) as e:
            plain_box(self, "warning", ui_text("bld.deps.title"), str(e))
            return None
        self.status.setText(ui_text("bld.deps.deleted", count=len(result.done)))
        self.reload()
        return result


__all__ = ["BuildDependenciesDialog"]
