#!/usr/bin/env python3
"""
Pending approvals in the GUI (MCP design 6.9): calls of AI clients that could not ask you themselves.

The main window shows a notice in its status bar (and, where the system offers it, a desktop notification) while
calls wait; the approval window shows each call's full question with the caller class first and the client's own
name marked as self-reported, the time left, and "Approve once" and "Decline". The framework then runs an approved
call itself. Every decision is recorded in the audit log before it is stored; when that fails, nothing is decided.
"""

from typing import List, Optional

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout)

from orchestrator.utils.logging import get_logger

logger = get_logger("ApprovalsWindow")
POLL_MS = 2000


class ApprovalNotifier(QObject):
    """Looks at the pending approvals every two seconds (GUI thread; a small file) and reports how many wait."""

    changed = Signal(int)

    def __init__(self, store, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.store = store
        self.count = 0
        self._seen: set = set()
        self.new_ids: List[str] = []
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check)
        self.timer.start(POLL_MS)

    def check(self) -> int:
        try:
            pending = self.store.pending()
        except Exception as e:  # never break the window over a notice
            logger.warning(f"Could not read the pending approvals: {e}")
            return self.count
        ids = {r.id for r in pending}
        self.new_ids = sorted(ids - self._seen)
        self._seen = ids
        if len(pending) != self.count or self.new_ids:
            self.count = len(pending)
            self.changed.emit(self.count)
        return self.count


class ApprovalsDialog(QDialog):
    """The framework's own approval window."""

    def __init__(self, registry, parent=None):
        super().__init__(parent)
        self.registry = registry
        self.store = registry.approvals.store
        self.records = []
        self.setWindowTitle("Calls waiting for your approval")
        self.resize(860, 600)
        layout = QVBoxLayout(self)
        intro = QLabel("These AI clients could not ask you themselves. Read each call; approve it once, or decline it. "
                       "The framework then runs an approved call for the client. Values come from the AI and are shown "
                       "exactly as they will run.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        row = QHBoxLayout()
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._show)
        row.addWidget(self.list, 1)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)  # plain text: values come from the AI, never rich text
        row.addWidget(self.text, 3)
        layout.addLayout(row)
        buttons = QHBoxLayout()
        self.approve_button = QPushButton("Approve once")
        self.approve_button.clicked.connect(lambda: self.decide(True))
        self.decline_button = QPushButton("Decline")
        self.decline_button.clicked.connect(lambda: self.decide(False))
        buttons.addWidget(self.approve_button)
        buttons.addWidget(self.decline_button)
        buttons.addStretch()
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(POLL_MS)
        self.refresh()

    def refresh(self) -> None:
        current = self.selected()
        self.records = self.store.pending()
        self.list.blockSignals(True)
        self.list.clear()
        for record in self.records:
            item = QListWidgetItem(f"{record.title}\n{record.caller_class}")
            item.setData(Qt.UserRole, record.id)
            self.list.addItem(item)
        self.list.blockSignals(False)
        ids = [r.id for r in self.records]
        self.list.setCurrentRow(ids.index(current.id) if current is not None and current.id in ids else
                                (0 if ids else -1))
        self._show(self.list.currentRow())

    def selected(self):
        row = self.list.currentRow()
        return self.records[row] if 0 <= row < len(self.records) else None

    def _show(self, row: int) -> None:
        record = self.records[row] if 0 <= row < len(self.records) else None
        enabled = record is not None
        self.approve_button.setEnabled(enabled)
        self.decline_button.setEnabled(enabled)
        if record is None:
            self.text.setPlainText("No call is waiting for your approval.")
            return
        left = record.seconds_left()
        self.text.setPlainText(f"{record.question}\n\nTime left: {left // 60} min {left % 60:02d} s")

    def decide(self, approve: bool) -> bool:
        from orchestrator.tools.approvals import ApprovalStoreError, audit_decision
        record = self.selected()
        if record is None:
            return False
        try:
            self.store.decide(record.id, approve, "gui", expected_question=record.question_sha256,
                              record_decision=lambda decided: audit_decision(self.registry, decided, "gui"))
        except ApprovalStoreError as e:
            QMessageBox.warning(self, "Approvals", str(e))
            self.refresh()
            return False
        except Exception as e:  # the audit log cannot record it
            QMessageBox.critical(self, "Approvals", f"The audit log could not record your decision ({e}); nothing "
                                                    "was decided.")
            return False
        self.refresh()
        return True
