#!/usr/bin/env python3
"""
The knowledge base's snapshots as the person sees them (Tools > Knowledge Snapshots...).

Every snapshot with its name, when it was taken, its size, what it is and its full path. The window deletes nothing:
the framework deletes no snapshot by itself (round 5 f), and until the trash and the framework's deletion dialog
exist (design WP7) the person removes the files they no longer need themselves, for example in the file manager this
window opens. Every text is plain text.
"""

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QHeaderView, QLabel, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from orchestrator.Core.knowledge_snapshots import MAX_SNAPSHOTS, list_snapshots, size_text, snapshot_folder

COLUMNS = ("Snapshot", "Taken", "Size", "What it is", "Full path")


def _plain(text: str) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return label


class KnowledgeSnapshotsDialog(QDialog):
    """Lists the snapshots in `state_dir`/knowledge_snapshots; read only."""

    def __init__(self, state_dir, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Knowledge Snapshots")
        self.resize(1000, 420)
        self.folder = snapshot_folder(state_dir)
        self.snapshots = list_snapshots(self.folder)
        layout = QVBoxLayout(self)
        total = sum(s.size_bytes for s in self.snapshots)
        layout.addWidget(_plain(f"{len(self.snapshots)} snapshots, {size_text(total)}, oldest first, in "
                                f"{self.folder}. Before every Deep Ingest, every index of the framework's own code "
                                "and every restore, the knowledge base is saved as a snapshot, so the change can be "
                                "undone."))
        self.table = QTableWidget(len(self.snapshots), len(COLUMNS), self)
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        for row, snap in enumerate(self.snapshots):
            for column, value in enumerate((snap.name, snap.taken, size_text(snap.size_bytes), snap.what,
                                            str(snap.path))):
                self.table.setItem(row, column, QTableWidgetItem(value))  # plain text, never rich text
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        if len(self.snapshots) > MAX_SNAPSHOTS:
            note = (f"More than the usual {MAX_SNAPSHOTS}. The framework deletes none by itself: delete the files you "
                    "no longer need yourself (a trash and a deletion dialog in the framework follow).")
        else:
            note = "The framework deletes no snapshot by itself."
        self.note = _plain(note)
        layout.addWidget(self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        self.open_folder = QPushButton("Open the folder", self)
        self.open_folder.setEnabled(self.folder.is_dir())
        self.open_folder.clicked.connect(self._open_folder)
        buttons.addButton(self.open_folder, QDialogButtonBox.ActionRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.folder))))
