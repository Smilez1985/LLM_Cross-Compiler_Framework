#!/usr/bin/env python3
"""
Build > Build History... (GUI parity W1': `llm-cli build status [--all]` and `llm-cli build status REQUEST_ID` by
mouse).

Every kept build and every request that still waits, wherever it was started (this GUI, a CLI call, an MCP server),
newest first and all of them (as --all): failed, cancelled, interrupted and queued ones included. The list comes from
build_history.read_history, the same query as the CLI. [Show details] (or a double click) shows one build as the CLI
does: its request summary with errors and notes, every attempt with its output folder and error, and the fix
proposals AI clients made for it (marked AI-generated; their texts are the AI's, shown as plain text and never run).
Reading the records is local file reading; nothing here starts, stops or changes a build. Every text is plain text.
"""

from typing import Any, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QHeaderView, QPlainTextEdit,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from orchestrator.gui.plain import button_text, display_text, one_line, plain_box, plain_label
from orchestrator.utils.localization import current_language, ui_text

COLUMNS = ("request", "build", "status", "progress", "target", "format", "quantization", "model", "started", "where")


def _settings(framework: Any):
    """The framework's settings and its path rule (relative paths: the installation), as the CLI reads them."""
    from orchestrator.Core.framework import resolve_install_path
    resolve = getattr(framework, "resolve_path", None)
    return framework.config, resolve if callable(resolve) else resolve_install_path


def details_text(history: Any, request_id: str, config: Any, lang: Optional[str] = None) -> str:
    """One build or request as the person reads it: the fields of `llm-cli build status REQUEST_ID` in the person's
    language (full output folder, errors, every attempt, notes), and the AI clients' fix proposals for it."""
    from orchestrator.Core.build_history import client_proposals, record_rows, request_rows
    from orchestrator.utils.untrusted import sanitize_untrusted

    def t(key: str, **values) -> str:
        return ui_text(key, lang, **values)

    details = history.details(request_id)
    if details is None:
        return t("bld.history.not_found", id=one_line(request_id), path=one_line(str(history.folder)))
    blocks: List[str] = []
    request = details.request
    if request is not None:
        lines = [t("bld.history.request_header")]
        for name, value in request_rows(request):
            if name == "owner":
                value = value or t("bld.history.owner_person")
            if value not in (None, ""):
                lines.append(f"{t('bld.field.' + name)}: {one_line(value)}")
        lines += [t("bld.history.error_line", error=display_text(e)) for e in request.get("errors") or []]
        if details.interrupted:
            lines.append(t("bld.history.interrupted_request"))
        elif details.awaiting_decision:
            lines.append(t("bld.history.awaiting", program=one_line(request.get("program"))))
        blocks.append("\n".join(lines))
    for number, attempt in enumerate(details.attempts, start=1):
        lines = [t("bld.history.attempt_header", n=number)]
        lines += [f"{t('bld.field.' + name)}: {one_line(value)}" for name, value in record_rows(attempt)
                  if value not in (None, "")]
        if attempt.get("status") == "interrupted":
            lines.append(t("bld.history.interrupted_build"))
        blocks.append("\n".join(lines))

    def ai(value: Any) -> str:   # the AI's text: invisible characters removed, plain text
        return sanitize_untrusted(str(value), 2000)

    for rid in details.proposal_requests:
        try:
            proposals = client_proposals(config, rid)
        except Exception:  # noqa: BLE001 - a damaged proposals file never hides the build
            proposals = []
        for p in proposals:
            lines = [t("bld.history.proposal_header", id=one_line(p.proposal_id), who=one_line(ai(p.who()))),
                     "  " + t("bld.history.proposal_summary", text=ai(p.summary))]
            if p.root_cause:
                lines.append("  " + t("bld.history.proposal_cause", text=ai(p.root_cause)))
            lines.append("  " + t("bld.history.proposal_step", where=one_line(p.where), text=ai(p.manual_step)))
            lines.append("  " + t("bld.history.proposal_confidence", value=f"{p.confidence:.0%}")
                         + (f", {one_line(p.created)}" if p.created else ""))
            blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


class BuildDetailsDialog(QDialog):
    """One build's details as plain text (read-only)."""

    def __init__(self, title: str, text: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(820, 560)
        layout = QVBoxLayout(self)
        self.text = QPlainTextEdit(self)   # plain text: an AI's proposal can never become markup or a link
        self.text.setObjectName("text_build_details")
        self.text.setReadOnly(True)
        self.text.setPlainText(text)
        layout.addWidget(self.text)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class BuildHistoryDialog(QDialog):
    """Every build of every framework process; [Show details] opens one, [Refresh] reads the records again."""

    def __init__(self, framework: Any, parent: Optional[QWidget] = None, lang: Optional[str] = None):
        super().__init__(parent)
        self.framework = framework
        self.lang = lang or current_language()
        self.history = None
        self.rows: List[Any] = []
        self.setWindowTitle(self._t("bld.history.title"))
        self.resize(1200, 480)
        layout = QVBoxLayout(self)
        self.intro = plain_label("")
        layout.addWidget(self.intro)
        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setObjectName("table_build_history")
        self.table.setHorizontalHeaderLabels([self._t(f"bld.history.col.{c}") for c in COLUMNS])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(lambda _index: self.show_details())
        layout.addWidget(self.table)
        self.notice = plain_label("")
        layout.addWidget(self.notice)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        self.btn_details = QPushButton(button_text(self._t("bld.history.details")), self)
        self.btn_details.setObjectName("btn_history_details")
        self.btn_refresh = QPushButton(button_text(self._t("bld.history.refresh")), self)
        self.btn_refresh.setObjectName("btn_history_refresh")
        # an interrupted build with finished stages kept: continue it, or delete what it left
        # (gui/interrupted_build_dialog.py; `llm-cli build interrupted` at a terminal)
        self.btn_interrupted = QPushButton(button_text(self._t("bld.history.interrupted")), self)
        self.btn_interrupted.setObjectName("btn_history_interrupted")
        for button, slot in ((self.btn_details, self.show_details), (self.btn_interrupted, self.decide_interrupted),
                             (self.btn_refresh, self.refresh)):
            button.setAutoDefault(False)
            button.clicked.connect(slot)
            buttons.addButton(button, QDialogButtonBox.ActionRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def _t(self, key: str, **values) -> str:
        return ui_text(key, self.lang, **values)

    def refresh(self) -> None:
        """Reads the build records and the shared build state again (build_history.read_history, as the CLI)."""
        from orchestrator.Core.build_history import client_proposals, read_history
        config, resolve = _settings(self.framework)
        self.history = read_history(config, resolve)
        self.rows = self.history.rows()   # every build (the CLI's --all)
        folder = one_line(str(self.history.folder))
        self.intro.setText(self._t("bld.history.intro", n=len(self.rows), path=folder) if self.rows
                           else self._t("bld.history.empty", path=folder))
        self.table.setRowCount(len(self.rows))
        for row, entry in enumerate(self.rows):
            values = entry.values()
            for column, name in enumerate(COLUMNS):
                cell = QTableWidgetItem(one_line(values[name]))   # plain text, never rich text
                if column == 0:
                    cell.setData(Qt.UserRole, entry.request_id if entry.kind == "request" else
                                 str(entry.data.get("request_id") or entry.build_id))
                    cell.setData(Qt.UserRole + 1, "" if entry.kind == "request" else str(entry.build_id or ""))
                self.table.setItem(row, column, cell)
        try:
            proposed = sorted({p.request_id for p in client_proposals(config)})
        except Exception:  # noqa: BLE001 - a damaged proposals file never hides the builds
            proposed = []
        self.notice.setText(self._t("bld.history.proposals", n=len(proposed), request=one_line(proposed[-1]))
                            if proposed else "")
        self.btn_details.setEnabled(bool(self.rows))

    def selected(self) -> Optional[str]:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 0)
        return item.data(Qt.UserRole) if item is not None else None

    def selected_build(self) -> Optional[str]:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        item = self.table.item(rows[0].row(), 0) if rows else None
        return (item.data(Qt.UserRole + 1) or None) if item is not None else None

    def decide_interrupted(self) -> str:
        """[Continue or delete...]: the person's question about the chosen build, when it stopped with finished
        stages kept (Core/interrupted_build.py)."""
        from orchestrator.gui.interrupted_build_dialog import ask_interrupted
        build_id = self.selected_build()
        if not build_id:
            plain_box(self, "information", self._t("bld.history.title"), self._t("bld.history.pick"))
            return "none"
        outcome = ask_interrupted(self, self.framework, build_id, self.lang)
        self.refresh()
        return outcome

    def details_for(self, request_id: str) -> str:
        return details_text(self.history, request_id, self.framework.config, self.lang)

    def show_details(self) -> None:
        request_id = self.selected()
        if not request_id:
            plain_box(self, "information", self._t("bld.history.title"), self._t("bld.history.pick"))
            return
        BuildDetailsDialog(self._t("bld.history.details_title", id=one_line(request_id)),
                           self.details_for(request_id), self).exec()


__all__ = ["BuildDetailsDialog", "BuildHistoryDialog", "COLUMNS", "details_text"]
