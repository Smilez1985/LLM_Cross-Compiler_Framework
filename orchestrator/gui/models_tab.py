#!/usr/bin/env python3
"""
The Models tab: one list of the models on this computer and the builds made from them (Core/model_library.py), as
`llm-cli models list` shows it (DESIGN "GUI and the person's path", the row "The Models tab is one list").

A table with name, kind, format, type, size, where, date, target and board, and what is known (tested on boards, the
tokenizer check, the newest llama-bench run here); a filter by kind, a search field and Refresh. The disk is read when
the tab is first shown and on Refresh, never at the program's start. The actions of the selected row: Open Folder,
Build Info..., Deploy... (the Deployment tab with the build selected), Benchmark... (the Benchmark window with the model
chosen) and Delete..., which asks with the deletion dialog of the row's owner (the exact list, No by default). With
nothing listed, an empty state with one button: the Hugging Face browser.
"""

from typing import Any, List, Optional

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from orchestrator.gui.empty_state import EmptyState, EmptyStateStack
from orchestrator.gui.plain import button_text, plain_box, plain_label
from orchestrator.utils.localization import ui_text

COLUMNS = ("name", "kind", "format", "type", "size", "where", "date", "target", "state")


class ModelsTab(QWidget):
    def __init__(self, framework: Any, parent: Optional[QWidget] = None, *, open_hf=None, open_benchmark=None,
                 show_deploy=None):
        """`open_hf()`: the Hugging Face browser; `open_benchmark(key)`: the Benchmark window with that model;
        `show_deploy(path)`: the Deployment tab with that build selected (the main window passes them)."""
        super().__init__(parent)
        self.framework = framework
        self._open_hf, self._open_benchmark, self._show_deploy = open_hf, open_benchmark, show_deploy
        self.rows: List[Any] = []
        self.shown: List[Any] = []
        self.loaded = False
        self.setObjectName("models_tab")
        layout = QVBoxLayout(self)

        bar = QHBoxLayout()
        bar.addWidget(QLabel(ui_text("models.filter")))
        self.cb_kind = QComboBox()
        self.cb_kind.setObjectName("cb_models_kind")
        self.cb_kind.setAccessibleName(ui_text("models.filter").rstrip(":"))
        self.cb_kind.addItem(ui_text("models.filter.all"), "")
        from orchestrator.Core.model_library import KINDS
        for kind in KINDS:
            self.cb_kind.addItem(ui_text(f"models.kind.{kind}"), kind)
        self.cb_kind.currentIndexChanged.connect(lambda *_: self.apply_filter())
        bar.addWidget(self.cb_kind)
        bar.addWidget(QLabel(ui_text("models.search")))
        self.txt_search = QLineEdit()
        self.txt_search.setObjectName("txt_models_search")
        self.txt_search.setPlaceholderText(ui_text("models.search.placeholder"))
        self.txt_search.setAccessibleName(ui_text("models.search").rstrip(":"))
        self.txt_search.textChanged.connect(lambda *_: self.apply_filter())
        bar.addWidget(self.txt_search, 1)
        self.btn_refresh = QPushButton(button_text(ui_text("models.refresh")))
        self.btn_refresh.setObjectName("btn_models_refresh")
        self.btn_refresh.clicked.connect(self.refresh)
        bar.addWidget(self.btn_refresh)
        layout.addLayout(bar)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setObjectName("tbl_models")
        self.table.setHorizontalHeaderLabels([ui_text(f"models.col.{c}") for c in COLUMNS])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSortingEnabled(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAccessibleName(ui_text("main.tab.models"))
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.cellDoubleClicked.connect(lambda *_: self.open_folder())
        self.empty = EmptyState(ui_text("models.empty"), ui_text("models.empty.button"), self._empty_action)
        self.stack = EmptyStateStack(self.table, self.empty)
        layout.addWidget(self.stack, 1)

        self.lbl_status = plain_label("")
        self.lbl_status.setObjectName("lbl_models_status")
        layout.addWidget(self.lbl_status)
        actions = QHBoxLayout()
        self.btn_open = self._button(actions, "btn_models_open_folder", "models.open_folder", self.open_folder)
        self.btn_info = self._button(actions, "btn_models_info", "models.info", self.show_info)
        self.btn_deploy = self._button(actions, "btn_models_deploy", "models.deploy", self.deploy)
        self.btn_bench = self._button(actions, "btn_models_bench", "models.bench", self.benchmark)
        actions.addStretch(1)
        self.btn_delete = self._button(actions, "btn_models_delete", "models.delete", self.delete)
        layout.addLayout(actions)
        self._update_buttons()

    def _button(self, layout, name: str, key: str, slot) -> QPushButton:
        button = QPushButton(button_text(ui_text(key)))
        button.setObjectName(name)
        button.setAutoDefault(False)
        button.clicked.connect(slot)
        layout.addWidget(button)
        return button

    # --- the list ---

    def showEvent(self, event) -> None:   # noqa: N802 - Qt's name
        """The disk is read the first time the tab is shown, never at the program's start."""
        super().showEvent(event)
        if not self.loaded:
            self.refresh()

    def refresh(self) -> None:
        from orchestrator.Core.model_library import library
        try:
            self.rows = library(self.framework)
        except Exception as e:  # noqa: BLE001 - a folder that cannot be read: nothing listed, the reason shown
            self.rows = []
            self.lbl_status.setText(str(e))
        self.loaded = True
        self.apply_filter()

    def apply_filter(self) -> None:
        from orchestrator.Core.deletion import size_text
        from orchestrator.Core.model_library import matching
        from orchestrator.utils.localization import current_language
        lang = current_language()
        self.shown = matching(self.rows, self.cb_kind.currentData() or "", self.txt_search.text())
        self.table.setRowCount(len(self.shown))
        for i, row in enumerate(self.shown):
            values = (row.name, row.kind_text(lang), row.format, row.quant,
                      size_text(row.size_bytes, lang) if row.size_bytes is not None else "?", row.where_text(lang),
                      row.date, " / ".join(x for x in (row.target, row.board) if x), row.state(lang))
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0 and row.path is not None:
                    item.setToolTip(str(row.path))
                self.table.setItem(i, column, item)
        self.table.resizeColumnsToContents()
        if not self.rows:
            self.empty.set_sentence(ui_text("models.empty"))
            self.empty.set_action(ui_text("models.empty.button"), self._empty_action)
        elif not self.shown:
            self.empty.set_sentence(ui_text("models.empty.filtered"))
            self.empty.set_action(None, None)
        self.stack.set_empty(not self.shown)
        total = sum(r.size_bytes or 0 for r in self.shown)
        self.lbl_status.setText(ui_text("models.count", shown=len(self.shown), total=len(self.rows),
                                        size=size_text(total, lang)) if self.rows else "")
        self._update_buttons()

    def _empty_action(self) -> None:
        if callable(self._open_hf):
            self._open_hf()

    def current(self):
        index = self.table.currentRow()
        selected = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not selected or index < 0 or index >= len(self.shown):
            return None
        return self.shown[index]

    def select_key(self, key: str) -> bool:
        for i, row in enumerate(self.shown):
            if row.key == key:
                self.table.selectRow(i)
                return True
        return False

    def _update_buttons(self) -> None:
        row = self.current()
        self.btn_open.setEnabled(row is not None and row.path is not None)
        self.btn_info.setEnabled(row is not None and row.kind == "build")
        self.btn_deploy.setEnabled(row is not None and row.kind == "build" and callable(self._show_deploy))
        self.btn_bench.setEnabled(row is not None and row.gguf is not None)
        self.btn_delete.setEnabled(row is not None)

    # --- the actions ---

    def open_folder(self) -> None:
        row = self.current()
        if row is None or row.path is None:
            return
        folder = row.path if row.path.is_dir() else row.path.parent
        if not folder.is_dir():
            plain_box(self, "warning", ui_text("main.tab.models"), ui_text("models.no_folder"))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def show_info(self) -> Optional[QDialog]:
        from orchestrator.Core.model_library import build_info_text
        row = self.current()
        if row is None or row.kind != "build":
            plain_box(self, "information", ui_text("models.info.title"), ui_text("models.info.none"))
            return None
        try:
            text = build_info_text(self.framework, row)
        except Exception as e:  # noqa: BLE001 - an unknown id meanwhile: plain text
            plain_box(self, "warning", ui_text("models.info.title"), str(e))
            return None
        dialog = QDialog(self)
        dialog.setObjectName("dlg_models_info")
        dialog.setWindowTitle(f"{ui_text('models.info.title')}: {row.name}")
        box = QVBoxLayout(dialog)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlainText(text)   # written by the build container: plain text, never rich text
        box.addWidget(view)
        close = QPushButton(button_text(ui_text("bld.f16.close")))
        close.clicked.connect(dialog.accept)
        box.addWidget(close)
        dialog.resize(760, 520)
        dialog.show()
        return dialog

    def deploy(self) -> None:
        row = self.current()
        if row is not None and row.kind == "build" and callable(self._show_deploy):
            self._show_deploy(str(row.path))

    def benchmark(self) -> None:
        row = self.current()
        if row is None or row.gguf is None:
            plain_box(self, "information", ui_text("bench.title"), ui_text("models.bench.not_gguf"))
            return
        if callable(self._open_benchmark):
            self._open_benchmark(row.key)

    def _audit(self):
        return getattr(self.framework, "audit", None) or \
            getattr(getattr(self.framework, "tool_registry", None), "audit", None)

    def delete(self) -> Optional[Any]:
        """Delete...: the row's own deletion, asked with the deletion dialog (the exact list, No by default)."""
        from orchestrator.Core import model_library as lib
        from orchestrator.Core.deletion import PlanChanged
        from orchestrator.Core.trash import Trash
        from orchestrator.gui.deletion_dialog import ask_deletion
        row = self.current()
        if row is None:
            return None
        title = ui_text("models.delete.title")
        if not row.deletable:
            plain_box(self, "information", title, ui_text("models.not_deletable.ditto" if row.kind == "ditto"
                                                          else "models.not_deletable.other"))
            return None
        try:
            trash = Trash.for_framework(self.framework)
        except Exception:  # noqa: BLE001 - no per-user folder: only deleting for good works then
            trash = None
        try:
            if row.kind == "build":
                target, plan = lib.build_deletion(self.framework, row)
                accepted = ask_deletion(self, plan, title=title)
                if accepted is None:
                    return None
                result = lib.delete_build(self.framework, row, target, accepted, trash=trash, audit=self._audit())
                done = 1
            elif row.kind == "f16":
                plan = lib.f16_deletion(self.framework, row)
                if plan is None:
                    raise PlanChanged("the list of F16 intermediates")
                accepted = ask_deletion(self, plan, title=title)
                if accepted is None:
                    return None
                from orchestrator.Core.f16_cache import F16Cache
                result = F16Cache.for_framework(self.framework).delete(accepted, trash=trash, audit=self._audit())
                done = len(result.done)
            else:
                plan = lib.download_plan(self.framework, [row])
                if plan is None:
                    raise PlanChanged("the list of downloaded models")
                accepted = ask_deletion(self, plan, title=title)
                if accepted is None:
                    return None
                result = lib.delete_downloads(self.framework, accepted, trash=trash, audit=self._audit())
                done = len(result.done)
        except Exception as e:  # noqa: BLE001 - refused, changed meanwhile, no trash or audit: plain text, nothing gone
            plain_box(self, "warning", title, str(e))
            self.refresh()
            return None
        from orchestrator.Core.deletion import kept_lines
        self.refresh()
        kept = kept_lines(result)
        self.lbl_status.setText(ui_text("models.deleted", count=done))
        if kept:   # what stayed, with the paths to delete by hand
            plain_box(self, "warning", title, "\n".join(kept))
        return result


__all__ = ["COLUMNS", "ModelsTab"]
