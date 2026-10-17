#!/usr/bin/env python3
"""
Security > Audit Log... (wave 5, GUI parity: every `llmcf tools audit` action by mouse).

The audit log of tool calls as the CLI shows it (the same rows: orchestrator/tools/audit_view.py), a page at a time
(Entries per page, Newer / Older), with the detail columns on request (--details), the integrity check of the whole
chain with its result in plain words (--verify), Archive... after a question ("No" is the default; the log is moved
aside with a timestamp, nothing is deleted: --archive), and the counts per caller (--summary). The check, the
archive and the summary run off the GUI thread (they read the whole log); every text is plain text.
"""

import sqlite3
import threading
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
                               QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from orchestrator.gui.plain import ask, button_text, display_text, one_line, plain_box, plain_label
from orchestrator.gui.worker_relay import worker_relay
from orchestrator.utils.localization import current_language, ui_text

ENTRY_KEYS = ("time", "who", "tool", "decision", "confirmed", "outcome", "reason", "asked")
DETAIL_KEYS = ("repairs", "ms", "result", "links", "session")
SUMMARY_KEYS = ("who", "calls", "refused", "cannot", "check_confirmed", "check_contradicted", "check_premature",
                "check_unchecked")
DEFAULT_LIMIT = 30   # the CLI's --limit default


def check_words(result: Any, lang: Optional[str] = None) -> str:
    """The result of AuditLog.check() in plain words, in the person's language."""
    entry = getattr(result, "entry", None)
    if result.intact:
        key = "keyed" if result.keyed else "unkeyed_now" if result.key_available else "unkeyed"
        return ui_text(f"tmcp.check.{key}", lang, n=result.entries)
    problem = result.problem
    if problem == "modified":
        return ui_text("tmcp.check.modified", lang, entry=entry) if entry else ui_text("tmcp.check.modified_somewhere",
                                                                                      lang)
    if problem == "removed":
        return ui_text("tmcp.check.removed", lang, entry=entry or 1)
    if problem in ("key_mismatch", "anchor_missing"):
        return ui_text(f"tmcp.check.{problem}_at", lang, entry=entry) if entry else ui_text(f"tmcp.check.{problem}",
                                                                                           lang)
    return one_line(result.message)


class AuditLogWindow(QDialog):
    """The audit log of `registry` (its AuditLog); reads only, except Archive... after the person's yes."""

    def __init__(self, registry, parent: Optional[QWidget] = None, lang: Optional[str] = None):
        super().__init__(parent)
        self.registry = registry
        self.audit = getattr(registry, "audit", None)
        self.lang = lang or current_language()
        self.offset = 0
        self.total = 0
        self.running: Optional[str] = None   # the work running off the GUI thread: check, archive or summary
        self.threads: List[bool] = []        # per finished work: whether it ran on the GUI thread (tests)
        self._jobs: Dict[object, Callable[[bool, Any], None]] = {}
        worker_relay().done.connect(self._relay_done, Qt.QueuedConnection)
        self.setWindowTitle(self._t("tmcp.audit.title"))
        self.resize(1180, 620)
        layout = QVBoxLayout(self)
        path = str(getattr(self.audit, "path", "")) if self.audit is not None else ""
        self.intro = plain_label(self._t("tmcp.audit.intro", path=path) if self.audit is not None
                                 else self._t("tmcp.audit.none"))
        layout.addWidget(self.intro)

        self.tabs = QTabWidget(self)
        entries = QWidget()
        entries_layout = QVBoxLayout(entries)
        row = QHBoxLayout()
        row.addWidget(plain_label(self._t("tmcp.audit.limit"), wrap=False, selectable=False))
        self.spin_limit = QSpinBox()
        self.spin_limit.setObjectName("spin_audit_limit")
        self.spin_limit.setRange(1, 1000)
        self.spin_limit.setValue(DEFAULT_LIMIT)
        self.spin_limit.valueChanged.connect(lambda _v: self.load(reset=True))
        row.addWidget(self.spin_limit)
        self.chk_details = QCheckBox(button_text(self._t("tmcp.audit.details")))
        self.chk_details.setObjectName("chk_audit_details")
        self.chk_details.toggled.connect(lambda _on: self.load())
        row.addWidget(self.chk_details)
        row.addStretch()
        self.btn_newer = self._button("btn_audit_newer", "tmcp.audit.newer", self.newer)
        self.btn_older = self._button("btn_audit_older", "tmcp.audit.older", self.older)
        self.btn_refresh = self._button("btn_audit_refresh", "tmcp.audit.refresh", lambda: self.load())
        for button in (self.btn_newer, self.btn_older, self.btn_refresh):
            row.addWidget(button)
        entries_layout.addLayout(row)
        self.page = plain_label("")
        entries_layout.addWidget(self.page)
        self.table = self._table("table_audit_entries")
        entries_layout.addWidget(self.table)
        self.tabs.addTab(entries, self._t("tmcp.audit.tab.entries"))

        summary = QWidget()
        summary_layout = QVBoxLayout(summary)
        row = QHBoxLayout()
        self.btn_summary = self._button("btn_audit_summary", "tmcp.audit.summary.count", self.summarize)
        row.addWidget(self.btn_summary)
        row.addStretch()
        summary_layout.addLayout(row)
        self.summary_note = plain_label("")
        summary_layout.addWidget(self.summary_note)
        self.summary_table = self._table("table_audit_summary")
        self.summary_table.setColumnCount(len(SUMMARY_KEYS))
        self.summary_table.setHorizontalHeaderLabels([self._t(f"tmcp.audit.col.{k}") for k in SUMMARY_KEYS])
        summary_layout.addWidget(self.summary_table)
        self.tabs.addTab(summary, self._t("tmcp.audit.tab.summary"))
        self.summarized = False
        self.tabs.currentChanged.connect(lambda index: index == 1 and not self.summarized and self.summarize())
        layout.addWidget(self.tabs)

        self.check_result = plain_label("")
        self.check_result.setObjectName("lbl_audit_check")
        layout.addWidget(self.check_result)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        self.btn_check = self._button("btn_audit_check", "tmcp.audit.check", self.check)
        self.btn_archive = self._button("btn_audit_archive", "tmcp.audit.archive", self.archive)
        for button in (self.btn_check, self.btn_archive):
            buttons.addButton(button, QDialogButtonBox.ActionRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.load()

    # --- helpers ----------------------------------------------------------------------------------------------------

    def _t(self, key: str, **values) -> str:
        return ui_text(key, self.lang, **values)

    def _button(self, name: str, key: str, slot) -> QPushButton:
        button = QPushButton(button_text(self._t(key)), self)
        button.setObjectName(name)
        button.setAutoDefault(False)
        button.clicked.connect(lambda _=False: slot())
        return button

    def _table(self, name: str) -> QTableWidget:
        table = QTableWidget(0, 0, self)
        table.setObjectName(name)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def _busy(self, name: Optional[str]) -> None:
        self.running = name
        has = self.audit is not None
        for button in (self.btn_check, self.btn_archive, self.btn_summary):
            button.setEnabled(has and name is None)

    def _run(self, name: str, work: Callable[[], Any], done: Callable[[bool, Any], None]) -> bool:
        """Runs `work` in a plain thread; `done(ok, result or exception)` runs on the GUI thread afterwards."""
        if self.running is not None or self.audit is None:
            return False
        token = object()
        self._jobs[token] = done
        self._busy(name)
        relay = worker_relay()

        def run() -> None:
            on_gui = threading.current_thread() is threading.main_thread()
            try:
                outcome = (True, work(), on_gui)
            except Exception as e:  # noqa: BLE001 - shown to the person as plain text
                outcome = (False, e, on_gui)
            relay.done.emit(token, outcome)
        threading.Thread(target=run, name=f"audit-{name}", daemon=True).start()
        return True

    def _relay_done(self, token: object, outcome: Any) -> None:
        done = self._jobs.pop(token, None)
        if done is None:
            return
        ok, result, on_gui = outcome
        self.threads.append(on_gui)
        self._busy(None)
        done(ok, result)

    # --- entries (`tools audit --limit N [--details]`) ------------------------------------------------------------------

    def load(self, reset: bool = False) -> None:
        if reset:
            self.offset = 0
        if self.audit is None:
            self.page.setText(self._t("tmcp.audit.none"))
            for button in (self.btn_newer, self.btn_older, self.btn_refresh, self.btn_check, self.btn_archive,
                           self.btn_summary):
                button.setEnabled(False)
            return
        from orchestrator.tools.audit_view import audit_rows
        details = self.chk_details.isChecked()
        limit = self.spin_limit.value()
        keys = ENTRY_KEYS + (DETAIL_KEYS if details else ())
        self.table.setColumnCount(len(keys))
        self.table.setHorizontalHeaderLabels([self._t(f"tmcp.audit.col.{k}") for k in keys])
        try:
            self.total = self.audit.count()
            self.offset = min(self.offset, max(0, self.total - 1))
            rows = list(audit_rows(self.registry, self.audit.entries(limit, self.offset), details))
        except (sqlite3.Error, OSError) as e:
            self.total, rows = 0, []
            self.page.setText(self._t("tmcp.audit.unreadable", path=str(self.audit.path), reason=one_line(str(e))))
        else:
            self.page.setText(self._t("tmcp.audit.page", first=self.offset + 1, last=self.offset + len(rows),
                                      total=self.total) if rows else self._t("tmcp.audit.empty"))
        self.table.setRowCount(len(rows))
        for r, values in enumerate(rows):
            for c, value in enumerate(values):
                self.table.setItem(r, c, QTableWidgetItem(display_text(value)))   # plain text, line breaks kept
        self.table.resizeRowsToContents()
        self.btn_newer.setEnabled(self.offset > 0)
        self.btn_older.setEnabled(self.offset + limit < self.total)

    def newer(self) -> None:
        self.offset = max(0, self.offset - self.spin_limit.value())
        self.load()

    def older(self) -> None:
        if self.offset + self.spin_limit.value() < self.total:
            self.offset += self.spin_limit.value()
        self.load()

    # --- `tools audit --verify` -------------------------------------------------------------------------------------------

    def check(self) -> bool:
        started = self._run("check", self.audit.check, self._checked) if self.audit is not None else False
        if started:
            self.check_result.setText(self._t("tmcp.audit.checking"))
        return started

    def _checked(self, ok: bool, result: Any) -> None:
        if not ok:
            self.check_result.setText(self._t("tmcp.check.failed", reason=one_line(str(result))))
            self.check_result.setStyleSheet("color: #b00020;")
            return
        self.check_result.setText(check_words(result, self.lang))
        color = "#2e7d32" if result.intact else "#b26a00" if result.problem == "anchor_missing" else "#b00020"
        self.check_result.setStyleSheet(f"color: {color};")
        self.last_check = result

    # --- `tools audit --archive` ------------------------------------------------------------------------------------------

    def archive(self) -> bool:
        """Moves the log and its anchor aside after the person's yes ("No" is the default); nothing is deleted."""
        if self.audit is None or self.running is not None:
            return False
        if not ask(self, self.windowTitle(), self._t("tmcp.audit.archive.question", path=str(self.audit.path))):
            return False
        started = self._run("archive", self.audit.archive, self._archived)
        if started:
            self.check_result.setText(self._t("tmcp.audit.archiving"))
            self.check_result.setStyleSheet("")
        return started

    def _archived(self, ok: bool, result: Any) -> None:
        self.check_result.setText("")
        if not ok:
            plain_box(self, "warning", self.windowTitle(), self._t("tmcp.audit.archive.failed", path=str(self.audit.path),
                                                                   reason=one_line(str(result))))
            return
        if result is None:
            plain_box(self, "information", self.windowTitle(), self._t("tmcp.audit.archive.none"))
        else:
            plain_box(self, "information", self.windowTitle(), self._t("tmcp.audit.archive.done", path=str(result)))
        self.summarized = False
        self.summary_table.setRowCount(0)
        self.load(reset=True)

    # --- `tools audit --summary` ------------------------------------------------------------------------------------------

    def summarize(self) -> bool:
        from orchestrator.tools.audit_view import SUMMARY_ENTRIES, audit_summary
        audit = self.audit
        started = self._run("summary", lambda: audit_summary(audit.entries(SUMMARY_ENTRIES)), self._summarized) \
            if audit is not None else False
        if started:
            self.summary_note.setText(self._t("tmcp.audit.summary.counting", n=SUMMARY_ENTRIES))
        return started

    def _summarized(self, ok: bool, result: Any) -> None:
        from orchestrator.tools.audit_view import SUMMARY_ENTRIES
        from orchestrator.tools.registry import caller_label
        if not ok:
            self.summary_note.setText(self._t("tmcp.audit.unreadable", path=str(self.audit.path),
                                              reason=one_line(str(result))))
            return
        self.summarized = True
        self.summary_note.setText(self._t("tmcp.audit.summary.note", n=SUMMARY_ENTRIES))
        self.summary_table.setRowCount(len(result))
        for r, values in enumerate(result):
            for c, value in enumerate(values):
                text = f"{caller_label(str(value))} ({value})" if c == 0 and caller_label(str(value)) != value \
                    else str(value)
                self.summary_table.setItem(r, c, QTableWidgetItem(one_line(text)))


__all__ = ["AuditLogWindow", "check_words", "DEFAULT_LIMIT"]
