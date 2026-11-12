#!/usr/bin/env python3
"""
Build > Targets... > Vendor Files...: the guided fetch of the vendor files a target's formats name and that may not be
passed on (Core/vendor_files.py, the same steps as `llm-cli vendor fetch|add|list`).

The window lists each file with whether its licence is accepted and whether it is in the person's own vendor store.
Show the Licence and Fetch... shows the whole licence text (LicenceDialog): Accept stays off until the person ticks
that they read and accept it themselves; only that click is the acceptance (actor "gui"), recorded with the text's
sha256. Then Add the File... takes the file the person downloaded on the vendor's page (checked by its sha256; without
a published one the person confirms the file's own), or Download It Now fetches a pinned file that the variables mark
as public and direct. No AI tool reaches these buttons: the window is no form of the GUI tools. Every text is plain.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QHeaderView,
                               QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from orchestrator.Core import vendor_files as vf
from orchestrator.gui.plain import ask, button_text, one_line, plain_box, plain_label
from orchestrator.utils.localization import current_language, ui_text

COLUMNS = ("bld.vendor.col.name", "bld.vendor.col.format", "bld.vendor.col.for", "bld.vendor.col.sha",
           "bld.vendor.col.accepted", "bld.vendor.col.store")


class LicenceDialog(QDialog):
    """The licence text in full and the file's facts; Accept only after the person's own tick."""

    def __init__(self, asked: vf.LicenceQuestion, parent: Optional[QWidget] = None, lang: Optional[str] = None):
        super().__init__(parent)
        self.lang = lang or current_language()
        self.setWindowTitle(ui_text("bld.vendor.licence_title", self.lang, name=one_line(asked.entry.file.name)))
        self.resize(760, 560)
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label("\n".join(one_line(line) for line in asked.facts())))
        self.text = QPlainTextEdit(self)
        self.text.setObjectName("text_vendor_licence")
        self.text.setReadOnly(True)
        self.text.setPlainText(asked.text)   # the vendor's text: plain text only
        layout.addWidget(self.text)
        self.tick = QCheckBox(ui_text("bld.vendor.accept_tick", self.lang), self)
        self.tick.setObjectName("chk_vendor_accept")
        layout.addWidget(self.tick)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel, self)
        self.btn_accept = QPushButton(button_text(ui_text("bld.vendor.accept", self.lang)), self)
        self.btn_accept.setObjectName("btn_vendor_accept")
        self.btn_accept.setEnabled(False)
        self.btn_accept.setAutoDefault(False)
        buttons.addButton(self.btn_accept, QDialogButtonBox.AcceptRole)
        self.tick.toggled.connect(self.btn_accept.setEnabled)
        self.btn_accept.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self) -> None:
        if not self.tick.isChecked():   # never without the person's own tick
            return
        super().accept()


class VendorFilesDialog(QDialog):
    """The vendor files of one target (see the module text)."""

    def __init__(self, folder: Path, state_dir: Any, parent: Optional[QWidget] = None, lang: Optional[str] = None,
                 fetch_text: Optional[Callable[[str], str]] = None, downloader: Any = None,
                 open_url: Optional[Callable[[str], Any]] = None):
        super().__init__(parent)
        self.folder, self.state_dir = Path(folder), state_dir
        self.lang = lang or current_language()
        self.fetch_text = fetch_text if fetch_text is not None else vf.default_fetch_text
        self.downloader = downloader
        self.open_url = open_url or (lambda url: QDesktopServices.openUrl(QUrl(url)))
        self.target = vf.target_id(self.folder)
        self.setWindowTitle(self._t("bld.vendor.title", target=one_line(self.target)))
        self.resize(900, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label(self._t("bld.vendor.intro")))
        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setObjectName("table_vendor_files")
        self.table.setHorizontalHeaderLabels([self._t(c) for c in COLUMNS])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        self.result = plain_label("")
        layout.addWidget(self.result)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        self.btn_fetch = QPushButton(button_text(self._t("bld.vendor.fetch")), self)
        self.btn_fetch.setObjectName("btn_vendor_fetch")
        self.btn_page = QPushButton(button_text(self._t("bld.vendor.open_page")), self)
        self.btn_page.setObjectName("btn_vendor_page")
        self.btn_add = QPushButton(button_text(self._t("bld.vendor.add")), self)
        self.btn_add.setObjectName("btn_vendor_add")
        self.btn_download = QPushButton(button_text(self._t("bld.vendor.download")), self)
        self.btn_download.setObjectName("btn_vendor_download")
        for button, slot in ((self.btn_fetch, self.fetch), (self.btn_page, self.open_page),
                             (self.btn_add, self.add_file), (self.btn_download, self.download)):
            button.setAutoDefault(False)
            button.clicked.connect(lambda *_a, s=slot: s())
            buttons.addButton(button, QDialogButtonBox.ActionRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.table.itemSelectionChanged.connect(self._enable)
        self.refresh()

    def _t(self, key: str, **values) -> str:
        return ui_text(key, self.lang, **values)

    def refresh(self) -> None:
        rows = vf.status(self.state_dir, self.folder)
        self.rows = rows
        self.table.setRowCount(len(rows))
        yes, no = self._t("bld.vendor.yes"), self._t("bld.vendor.no")
        for r, row in enumerate(rows):
            values = (row["name"], row["format"], row["for"] + (f" ({row['dest']})" if row["dest"] else ""),
                      yes if row["sha256_published"] else no, row["accepted"] or no, yes if row["in_store"] else no)
            for c, value in enumerate(values):
                self.table.setItem(r, c, QTableWidgetItem(one_line(value)))
        if rows and not self.table.selectionModel().selectedRows():
            self.table.selectRow(0)
        if not rows:
            self.result.setText(self._t("bld.vendor.none", target=one_line(self.target)))
        self._enable()

    def selected(self) -> Optional[str]:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return self.rows[rows[0].row()]["name"] if rows and rows[0].row() < len(self.rows) else None

    def _row(self) -> Optional[dict]:
        name = self.selected()
        return next((r for r in self.rows if r["name"] == name), None)

    def _enable(self) -> None:
        row = self._row()
        self.btn_fetch.setEnabled(row is not None)
        self.btn_page.setEnabled(row is not None)
        self.btn_add.setEnabled(bool(row and row["accepted"]))
        self.btn_download.setEnabled(bool(row and row["accepted"] and row["direct"]))

    def _problem(self, e: Exception) -> None:
        plain_box(self, "warning", self.windowTitle(), self._t("bld.vendor.refused", reason=one_line(str(e))))

    # --- the person's steps --------------------------------------------------------------------------------------
    def fetch(self) -> bool:
        """Shows the licence; the person's tick and Accept record the acceptance. True when it was accepted."""
        name = self.selected()
        if not name:
            plain_box(self, "information", self.windowTitle(), self._t("bld.vendor.pick"))
            return False
        try:
            asked = vf.question(self.folder, name, self.fetch_text)
        except vf.VendorProblem as e:
            self._problem(e)
            return False
        dialog = LicenceDialog(asked, self, self.lang)
        accepted = dialog.exec() == QDialog.Accepted and dialog.tick.isChecked()
        if not accepted:
            return False
        vf.accept(self.state_dir, asked, actor="gui", accepted=True)
        self.result.setText(self._t("bld.vendor.accepted_direct" if asked.entry.file.direct else
                                    "bld.vendor.accepted", name=one_line(name)))
        self.refresh()
        return True

    def open_page(self) -> None:
        row = self._row()
        if row is not None:
            self.open_url(row["url"])

    def add_file(self, path: Optional[str] = None) -> bool:
        """The file the person downloaded: checked by its sha256 and kept in their vendor store."""
        name = self.selected()
        if not name:
            return False
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, self._t("bld.vendor.add"), "", "All Files (*)")
            if not path:
                return False

        def confirm(digest: str) -> bool:
            return ask(self, self.windowTitle(), self._t("bld.vendor.confirm_sha", name=one_line(name), sha256=digest))
        try:
            kept = vf.add_file(self.state_dir, self.folder, name, Path(path), actor="gui", confirm_sha256=confirm)
        except (vf.VendorProblem, PermissionError, OSError) as e:
            self._problem(e)
            self.refresh()
            return False
        self.result.setText(self._t("bld.vendor.added", name=one_line(name), sha256=kept.sha256))
        self.refresh()
        return True

    def download(self) -> bool:
        name = self.selected()
        if not name:
            return False
        try:
            kept = vf.download(self.state_dir, self.folder, name, actor="gui", downloader=self.downloader)
        except (vf.VendorProblem, PermissionError, OSError) as e:
            self._problem(e)
            return False
        self.result.setText(self._t("bld.vendor.added", name=one_line(name), sha256=kept.sha256))
        self.refresh()
        return True


def open_vendor_files(parent: Optional[QWidget], folder: Path, state_dir: Any) -> Optional[VendorFilesDialog]:
    dialog = VendorFilesDialog(folder, state_dir, parent)
    dialog.setAttribute(Qt.WA_DeleteOnClose, False)
    dialog.exec()
    return dialog
