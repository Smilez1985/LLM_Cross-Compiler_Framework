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

The window also shows what the snapshots use against the free space of their disk. When a snapshot taken before a
change does not fit (eighth round, answer 2), `offer_snapshot_list` says so and opens this window on the person's
"Open"; nothing is ticked and nothing is deleted by itself.

It also says where the knowledge base is stored (DECISIONS RG5): in Qdrant Edge, converted from the storage of earlier
versions on first use, whose old folder is kept until the person deletes it here ([Delete the old storage...] opens
the deletion dialog with that folder's full path and size; "No" is the default), or still in the old storage when its
conversion failed, with the reason. [Try the conversion again] then converts it now (RAGManager.convert_storage, the
same as `llm-cli knowledge storage convert`; DECISIONS W1': the CLI is never the person's only way), in a worker thread
that reports through the one WorkerRelay, since a large knowledge base takes minutes.

Every text is plain text.
"""

import threading
from pathlib import Path
from typing import Any, List, Optional

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog, QHeaderView, QLabel,
                               QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

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
        self.state_dir = Path(state_dir)
        layout = QVBoxLayout(self)
        self.intro = _plain("")
        layout.addWidget(self.intro)
        self.disk = _plain("")
        layout.addWidget(self.disk)
        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels([self._t(c) for c in COLUMNS])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        self.note = _plain(self._t("snapshots.note"))
        layout.addWidget(self.note)
        self.storage = _plain("")
        layout.addWidget(self.storage)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        self.btn_delete = QPushButton(self._t("snapshots.delete").replace("&", "&&"), self)
        self.btn_export = QPushButton(self._t("snapshots.export").replace("&", "&&"), self)
        self.open_folder = QPushButton(self._t("snapshots.open").replace("&", "&&"), self)
        self.btn_old = QPushButton(self._t("storage.old.delete").replace("&", "&&"), self)
        self.btn_convert = QPushButton(self._t("storage.convert").replace("&", "&&"), self)
        for button, slot in ((self.btn_delete, self._delete), (self.btn_export, self._export),
                             (self.open_folder, self._open_folder), (self.btn_old, self._delete_old_store),
                             (self.btn_convert, self._convert)):
            button.setAutoDefault(False)
            button.clicked.connect(slot)
            buttons.addButton(button, QDialogButtonBox.ActionRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.snapshots = []
        self._convert_token: Optional[object] = None  # the running conversion's (one at a time)
        from orchestrator.gui.worker_relay import worker_relay
        worker_relay().done.connect(self._converted, Qt.QueuedConnection)
        self.reload()

    def _t(self, key: str, **values) -> str:
        return ui_text(key, self.lang, **values)

    def reload(self) -> None:
        self.snapshots = list_snapshots(self.folder)
        total = sum(s.size_bytes for s in self.snapshots)
        self.intro.setText(self._t("snapshots.intro", n=len(self.snapshots), size=size_text(total),
                                   folder=str(self.folder)))
        self.disk.setText(self._disk_line(total))
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
        self._storage_line()

    def _storage_line(self) -> None:
        """Where the knowledge base is stored, from the conversion record (nothing is opened): converted (with the old
        folder kept, and the button that lists it for deletion), or still in the old storage with the reason."""
        from orchestrator.rag import migration
        info = migration.summary(self.state_dir)
        text = ""
        if info.get("state") == "converted" and info.get("old_folder_kept"):
            text = self._t("storage.old.converted", when=str(info.get("converted_at", ""))[:16].replace("T", " "),
                           points=int(info.get("points") or 0), folder=str(info.get("old_folder", "")),
                           size=size_text(int(info.get("old_folder_bytes") or 0)))
        elif info.get("state") == "failed" and info.get("old_store"):
            # How it is tried again, in the window's language (the record's own words are English).
            retry = "storage.retry.start" if info.get("code") in migration.TRANSIENT else "storage.retry.person"
            text = self._t("storage.old.failed", reason=str(info.get("reason", "")), retry=self._t(retry))
        if self._convert_token is not None:
            text = self._t("storage.converting")
        self.storage.setText(text)
        self.storage.setVisible(bool(text))
        offered = self.framework is not None and migration.old_store_plan(self.state_dir) is not None
        self.btn_old.setVisible(offered)
        self.btn_old.setEnabled(offered)
        can_convert = (self.framework is not None and callable(getattr(self.framework, "ensure_rag_manager", None))
                       and info.get("state") == "failed" and bool(info.get("old_store"))
                       and not migration.edge_in_use(self.state_dir) and migration.engine_available())
        self.btn_convert.setVisible(can_convert or self._convert_token is not None)
        self.btn_convert.setEnabled(can_convert and self._convert_token is None)

    def _convert(self) -> None:
        """[Try the conversion again]: RAGManager.convert_storage in a worker thread (copy and check take minutes for a
        large knowledge base); the result arrives through the WorkerRelay (_converted). Nothing is deleted."""
        if self._convert_token is not None:
            return
        from orchestrator.gui.worker_relay import worker_relay
        token, relay, framework = object(), worker_relay(), self.framework
        self._convert_token = token

        def work() -> None:
            try:
                rag = framework.ensure_rag_manager()
                result = rag.convert_storage() if rag is not None else {"off": True}
            except Exception as e:  # noqa: BLE001 - shown to the person; the old storage keeps working
                result = {"error": f"{type(e).__name__}: {e}"}
            relay.done.emit(token, result)

        threading.Thread(target=work, name="knowledge-conversion", daemon=True).start()
        self._storage_line()

    def _converted(self, token: object, result: Any) -> None:
        if token is not self._convert_token:
            return
        from orchestrator.gui.plain import one_line
        self._convert_token = None
        result = result if isinstance(result, dict) else {}
        if result.get("off"):
            self._box("warning", self._t("storage.convert.off"))
        elif result.get("used") == "qdrant-edge":
            self._box("information", self._t("storage.convert.done", points=int(result.get("points") or 0)))
        else:
            reason = result.get("reason") or result.get("error") or ""
            self._box("warning", self._t("storage.convert.failed", reason=one_line(str(reason))))
        self.reload()

    def _disk_line(self, total: int) -> str:
        """What the snapshots use against the free space of their disk ("" when it cannot be read)."""
        from orchestrator.Core.knowledge_snapshots import snapshot_room
        try:
            room = snapshot_room(self.folder, 0)
        except OSError:
            return ""
        if room.check is None:
            return ""
        return self._t("snapshots.disk", size=size_text(total), free=size_text(room.free))

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

    def _delete_old_store(self) -> None:
        """The old storage folder after its conversion: the deletion dialog with its full path and size, then
        migration.delete_old_store (trash or for good, audited) only on the person's yes."""
        from orchestrator.Core.deletion import PlanChanged
        from orchestrator.Core.trash import Trash
        from orchestrator.gui.deletion_dialog import ask_deletion
        from orchestrator.gui.plain import one_line
        from orchestrator.rag import migration
        plan = migration.old_store_plan(self.state_dir) if self.framework is not None else None
        if plan is None:
            self._box("information", self._t("storage.old.none"))
            self.reload()
            return
        accepted = ask_deletion(self, plan, title=self._t("storage.old.title"), lang=self.lang)
        if accepted is None:
            return
        audit = getattr(getattr(self.framework, "tool_registry", None), "audit", None)
        rag = getattr(self.framework, "rag_manager", None)
        if callable(getattr(rag, "close", None)):
            rag.close()  # the GUI's own hold on the knowledge base (its lock) is released first
        try:
            result = migration.delete_old_store(self.state_dir, accepted, trash=Trash.for_framework(self.framework),
                                                audit=audit)
        except PlanChanged:
            self._box("warning", self._t("deletion.changed"))
            self.reload()
            return
        except (PermissionError, RuntimeError) as e:
            self._box("warning", self._t("deletion.failed", reason=one_line(str(e))))
            return
        if result.done:
            key = "storage.old.done.trash" if accepted.mode == "trash" else "storage.old.done.permanent"
            self._box("information", self._t(key))
        else:
            self._box("warning", self._t("storage.old.kept", reason=one_line(result.problem)))
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


def offer_snapshot_list(parent, framework: Any, text: str, lang: Optional[str] = None) -> bool:
    """A knowledge snapshot taken before a change did not fit on the disk (eighth round, answer 2): `text` says why in
    plain text, and the person may open the list of snapshots to delete or export the ones they choose (nothing is
    ticked, nothing is deleted by itself, "Close" is the default). True when the list was opened."""
    from orchestrator.gui.plain import plain_box
    lang = lang or current_language()
    answer = plain_box(parent, "warning", ui_text("snapshots.no_room.title", lang),
                       f"{text}\n\n{ui_text('snapshots.no_room.offer', lang)}",
                       buttons=QMessageBox.Open | QMessageBox.Close, default=QMessageBox.Close)
    if answer != QMessageBox.Open:
        return False
    state = getattr(getattr(framework, "config", None), "user_state_dir", None)
    if state is None:
        return False
    KnowledgeSnapshotsDialog(state, parent, framework=framework, lang=lang).exec()
    return True
