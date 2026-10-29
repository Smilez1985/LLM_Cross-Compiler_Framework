#!/usr/bin/env python3
"""
The Builder tab's choice when offline mode is on and the model named by its Hugging Face id is not on this computer
(DECISIONS round 25 Q1): nothing is downloaded. The window lists the models already here (BuildService.models_here:
the models folder, the folders shared with the AI, the Builder's own copies of Hugging Face models, the inputs of
earlier builds) and offers an open dialog for a model file or folder of the person's own (a sideloaded model). What the
person picks becomes the Builder's model, and the build starts with it. A file pick is the person's only; an AI never
reaches this window.
"""

from typing import Optional

from PySide6.QtWidgets import (QAbstractItemView, QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from orchestrator.utils.localization import ui_text


class OfflineModelDialog(QDialog):
    """The models already on this computer and a file or folder of the person's own; `chosen` holds the model value
    the build runs with (a path, or the repository id of the Builder's own copy) once accepted."""

    def __init__(self, error, parent=None):
        super().__init__(parent)
        self.setObjectName("dlg_offline_model")
        self.setWindowTitle(ui_text("bld.offline.title"))
        self.chosen: Optional[str] = None
        self.choices = list(getattr(error, "choices", []) or [])
        layout = QVBoxLayout(self)
        intro = ui_text("bld.offline.not_here", repo=getattr(error, "repo", ""))
        intro += " " + (ui_text("bld.offline.here") if self.choices else ui_text("bld.offline.none.person"))
        self.intro = QLabel(intro)
        self.intro.setWordWrap(True)
        layout.addWidget(self.intro)
        self.table = QTableWidget(len(self.choices), 3)
        self.table.setHorizontalHeaderLabels([ui_text("bld.offline.col.label"), ui_text("bld.offline.col.where"),
                                              ui_text("bld.offline.col.path")])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for row, model in enumerate(self.choices):
            for column, text in enumerate((model.label, model.where_text(), str(model.path))):
                self.table.setItem(row, column, QTableWidgetItem(text))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.doubleClicked.connect(lambda *_: self.use_selected())
        self.table.setVisible(bool(self.choices))
        layout.addWidget(self.table)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        buttons = QHBoxLayout()
        self.btn_file = QPushButton(ui_text("bld.offline.file"))
        self.btn_file.clicked.connect(self.choose_file)
        self.btn_folder = QPushButton(ui_text("bld.offline.folder"))
        self.btn_folder.clicked.connect(self.choose_folder)
        self.btn_use = QPushButton(ui_text("bld.offline.use"))
        self.btn_use.setEnabled(bool(self.choices))
        self.btn_use.clicked.connect(self.use_selected)
        self.btn_cancel = QPushButton(ui_text("bld.offline.cancel"))
        self.btn_cancel.clicked.connect(self.reject)
        for button in (self.btn_file, self.btn_folder, self.btn_use, self.btn_cancel):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        if self.choices:
            self.table.selectRow(0)

    def use_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            self.note.setText(ui_text("bld.offline.pick"))
            return
        model = self.choices[rows[0].row()]
        self.chosen = model.repo if model.where == "build_cache" else str(model.path)
        self.accept()

    def choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, ui_text("bld.offline.file"), "",
                                              "Models (*.gguf *.safetensors *.bin *.onnx *.tflite *.pt *.pth);;"
                                              "All Files (*)")
        if path:
            self.chosen = path
            self.accept()

    def choose_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, ui_text("bld.offline.folder"))
        if path:
            self.chosen = path
            self.accept()

    @classmethod
    def choose(cls, parent, error) -> Optional[str]:
        """The person's choice, or None when they cancel."""
        dialog = cls(error, parent)
        return dialog.chosen if dialog.exec() == QDialog.DialogCode.Accepted else None
