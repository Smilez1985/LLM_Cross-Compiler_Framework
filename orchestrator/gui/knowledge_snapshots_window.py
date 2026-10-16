#!/usr/bin/env python3
"""
The knowledge base's snapshots as the person sees them (Tools > Knowledge Snapshots...).

Every snapshot with its name, when it was taken, its size, what it is and its full path, each with a check box;
nothing is ticked. There is no limit and the framework deletes no snapshot by itself (DECISIONS, sixth round; round
5 f). The person frees space in two ways:

- [Delete ticked...] opens the deletion dialog with exactly the ticked snapshots (full path, file name, size, what
  it is; "No" is the default; the trash or for good) and deletes them only after the person's yes;
- [Export ticked...] copies them to a folder the person picks (this computer, a USB stick, a network share), checks
  every copy by sha256 and changes nothing here.

Every text is plain text.
"""

from pathlib import Path
from typing import Any, List, Optional

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog, QHeaderView, QLabel,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from orchestrator.Core.knowledge_snapshots import (delete_snapshots, export_snapshots, list_snapshots, size_text,
                                                   snapshot_folder, snapshot_plan)
from orchestrator.utils.localization import current_language, ui_text

COLUMNS = ("snapshots.col.name", "snapshots.col.taken", "snapshots.col.size", "snapshots.col.what",
           "snapshots.col.path")


def _plain(text: str) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return label


class KnowledgeSnapshotsDialog(QDialog):
    """Lists the snapshots in `state_dir`/knowledge_snapshots. `framework`: for deleting (its trash and audit log);
    without it the window lists and exports only."""

    def __init__(self, state_dir, parent=None, framework: Any = None, lang: Optional[str] = None):
        super().__init__(parent)
        self.framework = framework
        self.lang = lang or current_language()
        self.setWindowTitle(self._t("snapshots.title"))
        self.resize(1000, 420)
        self.folder = snapshot_folder(state_dir)
        layout = QVBoxLayout(self)
        self.intro = _plain("")
        layout.addWidget(self.intro)
        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels([self._t(c) for c in COLUMNS])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        self.note = _plain(self._t("snapshots.note"))
        layout.addWidget(self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        self.btn_delete = QPushButton(self._t("snapshots.delete").replace("&", "&&"), self)
        self.btn_export = QPushButton(self._t("snapshots.export").replace("&", "&&"), self)
        self.open_folder = QPushButton(self._t("snapshots.open").replace("&", "&&"), self)
        for button, slot in ((self.btn_delete, self._delete), (self.btn_export, self._export),
                             (self.open_folder, self._open_folder)):
            button.setAutoDefault(False)
            button.clicked.connect(slot)
            buttons.addButton(button, QDialogButtonBox.ActionRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.snapshots = []
        self.reload()

    def _t(self, key: str, **values) -> str:
        return ui_text(key, self.lang, **values)

    def reload(self) -> None:
        self.snapshots = list_snapshots(self.folder)
        total = sum(s.size_bytes for s in self.snapshots)
        self.intro.setText(self._t("snapshots.intro", n=len(self.snapshots), size=size_text(total),
                                   folder=str(self.folder)))
        self.table.setRowCount(len(self.snapshots))
        for row, snap in enumerate(self.snapshots):
            for column, value in enumerate((snap.name, snap.taken, size_text(snap.size_bytes), snap.what,
                                            str(snap.path))):
                cell = QTableWidgetItem(value)   # plain text, never rich text
                if column == 0:
                    cell.setFlags(cell.flags() | Qt.ItemIsUserCheckable)
                    cell.setCheckState(Qt.Unchecked)   # nothing is preselected
                self.table.setItem(row, column, cell)
        has = bool(self.snapshots)
        self.btn_delete.setEnabled(has and self.framework is not None)
        self.btn_export.setEnabled(has)
        self.open_folder.setEnabled(self.folder.is_dir())

    def ticked(self) -> List[str]:
        return [self.snapshots[r].name for r in range(self.table.rowCount())
                if self.table.item(r, 0).checkState() == Qt.Checked]

    def _box(self, kind: str, text: str) -> None:
        from orchestrator.gui.plain import plain_box
        plain_box(self, kind, self._t("snapshots.title"), text)

    def _delete(self) -> None:
        from orchestrator.Core.deletion import PlanChanged
        from orchestrator.Core.trash import Trash
        from orchestrator.gui.deletion_dialog import ask_deletion
        from orchestrator.gui.plain import one_line
        names = self.ticked()
        if not names:
            self._box("information", self._t("trash.nothing_ticked"))
            return
        if self.framework is None:
            self._box("information", self._t("snapshots.no_delete"))
            return
        try:
            plan = snapshot_plan(self.folder, names)
        except ValueError:
            plan = None
        if plan is None:
            self._box("warning", self._t("deletion.changed"))
            self.reload()
            return
        accepted = ask_deletion(self, plan, title=self._t("snapshots.title"), lang=self.lang)
        if accepted is None:
            return
        audit = getattr(getattr(self.framework, "tool_registry", None), "audit", None)
        try:
            result = delete_snapshots(self.folder, accepted, trash=Trash.for_framework(self.framework), audit=audit)
        except PlanChanged:
            self._box("warning", self._t("deletion.changed"))
            self.reload()
            return
        except (PermissionError, RuntimeError) as e:
            self._box("warning", self._t("deletion.failed", reason=one_line(str(e))))
            return
        done = "deletion.done.trash" if accepted.mode == "trash" else "deletion.done.permanent"
        lines = [self._t(done, name=one_line(n)) for n in result.done]
        lines += [self._t("deletion.kept", name=one_line(n), reason=one_line(r)) for n, r in result.failed]
        self._box("information", "\n".join(lines))
        self.reload()

    def _export(self) -> None:
        from orchestrator.gui.plain import one_line
        names = self.ticked()
        if not names:
            self._box("information", self._t("trash.nothing_ticked"))
            return
        target = QFileDialog.getExistingDirectory(self, self._t("snapshots.export_to"))
        if not target:
            return
        try:
            results = export_snapshots(self.folder, names, Path(target))
        except ValueError as e:
            self._box("warning", one_line(str(e)))
            return
        lines = [self._t("snapshots.exported", name=one_line(r.name), path=one_line(r.copy)) if r.ok else
                 self._t("snapshots.not_exported", name=one_line(r.name), reason=one_line(r.problem))
                 for r in results]
        self._box("information", "\n".join(lines + [self._t("snapshots.export_note")]))

    def _open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.folder))))
