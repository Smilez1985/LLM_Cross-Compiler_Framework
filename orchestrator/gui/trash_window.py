#!/usr/bin/env python3
"""
The trash as the person sees it (File > Trash...; delegation design 3.10).

Every entry with what it is, its size, when it was moved there, who asked to delete it, its reminder date and where
it came from (full path). Nothing is ticked. [Restore ticked] puts entries back; [Delete ticked for good...] opens the
deletion dialog with exactly the ticked entries ("No" is the default) and deletes them only after the person's yes;
[Remind me later] moves their reminder. Nothing leaves the trash by itself. Every text is plain text.
"""

from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QHeaderView, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from orchestrator.Core.deletion import PlanChanged, size_text
from orchestrator.Core.trash import Trash, TrashError, describe
from orchestrator.gui.deletion_dialog import ask_deletion
from orchestrator.gui.plain import button_text, one_line, plain_box, plain_label
from orchestrator.utils.localization import current_language, ui_text

COLUMNS = ("trash.col.item", "trash.col.what", "trash.col.size", "trash.col.deleted", "trash.col.by",
           "trash.col.remind", "trash.col.from")


class TrashDialog(QDialog):
    """Lists the trash of `trash` (a Trash); restores and empties only on the person's action."""

    def __init__(self, trash: Trash, parent: Optional[QWidget] = None, lang: Optional[str] = None):
        super().__init__(parent)
        self.trash = trash
        self.lang = lang or current_language()
        self.setWindowTitle(self._t("trash.title"))
        self.resize(1100, 440)
        layout = QVBoxLayout(self)
        self.intro = plain_label("")
        layout.addWidget(self.intro)
        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels([self._t(c) for c in COLUMNS])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        self.btn_restore = QPushButton(button_text(self._t("trash.restore")), self)
        self.btn_delete = QPushButton(button_text(self._t("trash.delete")), self)
        self.btn_keep = QPushButton(button_text(self._t("trash.keep")), self)
        for button, slot in ((self.btn_restore, self._restore), (self.btn_delete, self._delete),
                             (self.btn_keep, self._keep)):
            button.setAutoDefault(False)
            button.clicked.connect(slot)
            buttons.addButton(button, QDialogButtonBox.ActionRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.entries = []
        self.reload()

    def _t(self, key: str, **values) -> str:
        return ui_text(key, self.lang, **values)

    def reload(self) -> None:
        try:
            self.entries = self.trash.entries()
            due = {e.id for e in self.trash.due()}
        except TrashError as e:
            self.entries, due = [], set()
            self.intro.setText(self._t("trash.problem", reason=one_line(str(e))))
        else:
            total = sum(e.size for e in self.entries if Trash.present(e))
            self.intro.setText(self._t("trash.intro", n=len(self.entries), size=size_text(total))
                               if self.entries else self._t("trash.empty_note"))
        self.table.setRowCount(len(self.entries))
        for row, entry in enumerate(self.entries):
            shown = describe(entry)
            name = shown["name"] if Trash.present(entry) else self._t("trash.gone", name=shown["name"])
            remind = self._t("trash.due", date=shown["remind"]) if entry.id in due else shown["remind"]
            values = (name, shown["what"], shown["size"], shown["deleted"], shown["by"], remind, shown["from"])
            for column, value in enumerate(values):
                cell = QTableWidgetItem(one_line(value))   # plain text, never rich text
                if column == 0:
                    cell.setFlags(cell.flags() | Qt.ItemIsUserCheckable)
                    cell.setCheckState(Qt.Unchecked)   # nothing is preselected
                    cell.setData(Qt.UserRole, entry.id)
                self.table.setItem(row, column, cell)
        has = bool(self.entries)
        for button in (self.btn_restore, self.btn_delete, self.btn_keep):
            button.setEnabled(has)

    def ticked(self) -> List[str]:
        return [self.table.item(r, 0).data(Qt.UserRole) for r in range(self.table.rowCount())
                if self.table.item(r, 0).checkState() == Qt.Checked]

    def _need_ticks(self) -> Optional[List[str]]:
        ids = self.ticked()
        if not ids:
            plain_box(self, "information", self._t("trash.title"), self._t("trash.nothing_ticked"))
            return None
        return ids

    def _restore(self) -> None:
        ids = self._need_ticks()
        if not ids:
            return
        lines = []
        by_id = {e.id: e for e in self.entries}
        for entry_id in ids:
            name = by_id[entry_id].label if entry_id in by_id else entry_id
            try:
                dest = self.trash.restore(entry_id, "gui")
            except TrashError as e:
                lines.append(self._t("trash.restore_failed", name=one_line(name), reason=one_line(str(e))))
                continue
            lines.append(self._t("trash.restored", name=one_line(name), path=one_line(str(dest))))
        plain_box(self, "information", self._t("trash.title"), "\n".join(lines))
        self.reload()

    def _delete(self) -> None:
        ids = self._need_ticks()
        if not ids:
            return
        try:
            plan = self.trash.plan(ids)
        except TrashError as e:
            plain_box(self, "warning", self._t("trash.title"), self._t("deletion.failed", reason=one_line(str(e))))
            return
        if plan is None:
            self.reload()
            return
        accepted = ask_deletion(self, plan, title=self._t("trash.title"), lang=self.lang)
        if accepted is None:
            return
        try:
            result = self.trash.empty(accepted)
        except PlanChanged:
            plain_box(self, "warning", self._t("trash.title"), self._t("deletion.changed"))
            self.reload()
            return
        except (PermissionError, RuntimeError, TrashError) as e:
            plain_box(self, "warning", self._t("trash.title"), self._t("deletion.failed", reason=one_line(str(e))))
            return
        lines = [self._t("deletion.done.permanent", name=one_line(n)) for n in result.deleted]
        lines += [self._t("deletion.kept", name=one_line(n), reason=one_line(r)) for n, r in result.failed]
        plain_box(self, "information", self._t("trash.title"), "\n".join(lines))
        self.reload()

    def _keep(self) -> None:
        ids = self._need_ticks()
        if not ids:
            return
        try:
            self.trash.remind_later(ids, "gui")
        except (PermissionError, TrashError) as e:
            plain_box(self, "warning", self._t("trash.title"), one_line(str(e)))
        self.reload()


__all__ = ["COLUMNS", "TrashDialog"]
