#!/usr/bin/env python3
"""
Pending approvals in the GUI (MCP design 6.9): calls of AI clients that could not ask you themselves.

The main window shows a notice in its status bar (and, where the system offers it, a desktop notification) while
calls wait; the approval window shows each call's full question with the caller class first and the client's own
name marked as self-reported, the time left, and "Approve once", "Approve and do not ask again" (where the framework
can offer a standing approval for the call, with its warning, width and duration) and "Decline". The framework then
runs an approved call itself. Every decision is recorded in the audit log before it is stored; when that fails,
nothing is decided. A use of a standing approval is shown in the status bar with a "Revoke" button.
"""

from typing import List, Optional

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QRadioButton, QVBoxLayout)

from orchestrator.utils.logging import get_logger

logger = get_logger("ApprovalsWindow")
POLL_MS = 2000
REREAD_MS = 1500  # after the call you read changed (asked again with new facts), approving waits this long


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


class GrantUseWatcher(QObject):
    """Tells the window about every use of a standing approval (read from the audit log; the policy file never
    changes on a use): `used(tool, grant id, actor, detail)`."""

    used = Signal(str, str, str, str)

    def __init__(self, audit, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.audit = audit
        self.last_id = self._newest()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check)
        self.timer.start(POLL_MS)

    def _newest(self) -> int:
        try:
            rows = self.audit.entries(1)
        except Exception:
            return 0
        return int(rows[0]["id"]) if rows else 0

    def check(self) -> int:
        try:
            rows = self.audit.entries(50)
        except Exception as e:
            logger.warning(f"Could not read the audit log for standing-approval notices: {e}")
            return 0
        seen = 0
        for row in sorted(rows, key=lambda r: r["id"]):
            if row["id"] <= self.last_id:
                continue
            self.last_id = row["id"]
            channel = str(row.get("confirm_channel") or "")
            if channel.startswith("grant:") and row.get("outcome") in ("ok", "error"):
                repairs = row.get("repairs") or []
                detail = ("; values the framework repaired or filled in: "
                          + ", ".join(str(r.get("field") or r.get("kind")) for r in repairs if isinstance(r, dict))
                          if repairs else "")
                self.used.emit(str(row["tool"]), channel[len("grant:"):], str(row["actor"]), detail)
                seen += 1
        return seen


class GrantChoiceDialog(QDialog):
    """Before "do not ask again": the warning, how wide (this model or repository, or any) and for how long."""

    def __init__(self, offer, parent=None):
        super().__init__(parent)
        from orchestrator.tools.grants import DURATION_LABELS
        self.setWindowTitle("Do not ask again?")
        self.resize(640, 360)
        layout = QVBoxLayout(self)
        warning = QLabel(offer.warning)
        warning.setWordWrap(True)
        warning.setTextFormat(Qt.PlainText)
        layout.addWidget(warning)
        self.widths = {}
        for width, summary in offer.summaries.items():
            button = QRadioButton(summary)
            button.setChecked(width == offer.default_width())
            self.widths[width] = button
            layout.addWidget(button)
        self.duration = QComboBox()
        for key in offer.durations:
            self.duration.addItem(DURATION_LABELS[key], key)
        layout.addWidget(self.duration)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Do not ask again")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choice(self):
        width = next((w for w, b in self.widths.items() if b.isChecked()), None)
        return (width, self.duration.currentData()) if width else None


def choose_grant(offer, parent=None):
    """The person's width and duration for "do not ask again", or None when they cancel."""
    dialog = GrantChoiceDialog(offer, parent)
    return dialog.choice() if dialog.exec() else None


class ApprovalsDialog(QDialog):
    """The framework's own approval window."""

    def __init__(self, registry, parent=None):
        super().__init__(parent)
        self.registry = registry
        self.store = registry.approvals.store
        self.records = []
        self._shown = None    # (id, question hash) of the call whose question the window shows
        self._armed = True    # False for a moment after that question changed under your eyes
        self._vanished = ""   # the call you were reading is no longer waiting
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
        self.always_button = QPushButton("Approve and do not ask again...")
        self.always_button.clicked.connect(self.decide_always)
        self.decline_button = QPushButton("Decline")
        self.decline_button.clicked.connect(lambda: self.decide(False))
        buttons.addWidget(self.approve_button)
        buttons.addWidget(self.always_button)
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
        ids = [r.id for r in self.records]
        if current is not None:
            # Never another call in place of the one you were reading: a click meant for it must not approve a call
            # you never read. When it is gone, nothing is selected until you choose one.
            row = ids.index(current.id) if current.id in ids else -1
            self._vanished = "" if row >= 0 else current.title
        else:
            row = 0 if ids and not self._vanished else -1
        self.list.setCurrentRow(row)
        self.list.blockSignals(False)
        self._show(self.list.currentRow())

    def selected(self):
        row = self.list.currentRow()
        return self.records[row] if 0 <= row < len(self.records) else None

    def _show(self, row: int) -> None:
        record = self.records[row] if 0 <= row < len(self.records) else None
        if record is not None:
            self._vanished = ""
        shown = (record.id, record.question_sha256) if record is not None else None
        changed = shown is not None and self._shown is not None and shown[0] == self._shown[0] \
            and shown != self._shown
        self._shown = shown
        if changed:  # the question of the call you read was asked again with new facts: read it before approving
            self._armed = False
            QTimer.singleShot(REREAD_MS, self._arm)
        self._buttons(record)
        if record is None:
            self.text.setPlainText(f"The call you were reading ('{self._vanished}') is no longer waiting for you: the "
                                   "client withdrew it, it expired or it was decided elsewhere. Choose another one "
                                   "from the list." if self._vanished else "No call is waiting for your approval.")
            return
        left = record.seconds_left()
        note = ("The facts behind this call changed and it is asked again; read it before you decide.\n\n"
                if changed or not self._armed else "")
        self.text.setPlainText(f"{note}{record.question}\n\nTime left: {left // 60} min {left % 60:02d} s")

    def _buttons(self, record) -> None:
        enabled = record is not None
        self.approve_button.setEnabled(enabled and self._armed and record.kind == "call")
        self.always_button.setEnabled(enabled and self._armed and record.grant is not None)
        self.decline_button.setEnabled(enabled)

    def _arm(self) -> None:
        """Approving is possible again (after the pause that follows a changed question)."""
        self._armed = True
        self._buttons(self.selected())

    def decide_always(self, choice=None) -> bool:
        """"Approve and do not ask again": the warning first, then how wide and for how long."""
        record = self.selected()
        if record is None or record.grant is None or not self._armed:
            return False
        choice = choice or choose_grant(record.grant, self)
        if not choice:
            return False
        return self.decide(True, choice)

    def decide(self, approve: bool, choice=None) -> bool:
        from orchestrator.tools.approvals import ApprovalStoreError, apply_decision
        record = self.selected()
        if record is None or (approve and record.kind == "grant_request" and choice is None):
            return False
        if approve and (not self._armed or self._shown != (record.id, record.question_sha256)):
            return False  # only what the window shows, and not in the moment after it changed
        try:
            self.store.decide(record.id, approve, "gui", expected_question=record.question_sha256,
                              record_decision=lambda decided: apply_decision(self.registry, decided, "gui"),
                              choice=choice)
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
