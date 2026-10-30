#!/usr/bin/env python3
"""
What the knowledge base holds, as the person sees it (Tools > Knowledge Sources...; DECISIONS W1', round 8 Q-1/Q-1').

Every source with its kind (a web page, a page of an installed knowledge pack, the framework's own documents, other),
how much hidden text was left out of it before it was stored (knowledge_sources.removed_text, recon P1), the licence
and copyright notice it shows, what a knowledge pack would carry of it (its full text, only its address
and short facts, or nothing) and why. The kind filter lists only web pages, pack pages, documents or other sources.
The list comes from knowledge_sources.sources (RAGManager.list_sources, the same list as `llm-cli knowledge sources
--kind ...`); it opens the knowledge base, so it is read in a worker thread that reports through the one WorkerRelay.
Nothing is changed here. Addresses, titles and notices are site text: shown as plain text only.
"""

import threading
from typing import Any, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
                               QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from orchestrator.gui.plain import button_text, one_line, plain_label, plain_tooltip
from orchestrator.utils.localization import current_language, ui_text

# the order of knowledge_sources.COLUMNS (the CLI's list): the topic or target is the third column on both
COLUMNS = ("sources.col.source", "sources.col.kind", "sources.col.topic", "sources.col.removed", "sources.col.licence",
           "sources.col.copyright", "sources.col.carried", "sources.col.why")
KIND_FILTER = ("any", "web", "pack", "docs", "other")   # knowledge_sources.KIND_CHOICES, "any" first


class KnowledgeSourcesDialog(QDialog):
    """Lists the sources of the framework's knowledge base (`framework.ensure_rag_manager`), filtered by kind."""

    def __init__(self, framework: Any, parent: Optional[QWidget] = None, lang: Optional[str] = None):
        super().__init__(parent)
        self.framework = framework
        self.lang = lang or current_language()
        self.setObjectName("dlg_knowledge_sources")
        self.setWindowTitle(self._t("sources.title"))
        self.resize(1100, 480)
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label(self._t("sources.intro")))
        row = QHBoxLayout()
        row.addWidget(plain_label(self._t("sources.kind"), wrap=False, selectable=False))
        self.kind = QComboBox(self)
        self.kind.setObjectName("cmb_knowledge_sources_kind")
        self.kind.setAccessibleName(self._t("sources.kind"))
        for value in KIND_FILTER:
            self.kind.addItem(self._t(f"sources.filter.{value}"), value)
        self.kind.currentIndexChanged.connect(lambda _index: self.reload())
        row.addWidget(self.kind)
        # the topic or target a source belongs to (`llm-cli knowledge sources --topic`; DECISIONS round 23 item 1)
        row.addWidget(plain_label(self._t("sources.topic"), wrap=False, selectable=False))
        self.topic = QLineEdit(self)
        self.topic.setObjectName("edt_knowledge_sources_topic")
        self.topic.setAccessibleName(self._t("sources.topic"))
        self.topic.setPlaceholderText(self._t("sources.topic.hint"))
        self.topic.setClearButtonEnabled(True)
        self.topic.editingFinished.connect(self.reload)
        row.addWidget(self.topic)
        row.addStretch()
        self.btn_refresh = QPushButton(button_text(self._t("sources.refresh")), self)
        self.btn_refresh.setObjectName("btn_knowledge_sources_refresh")
        self.btn_refresh.setAutoDefault(False)
        self.btn_refresh.clicked.connect(self.reload)
        row.addWidget(self.btn_refresh)
        layout.addLayout(row)
        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setObjectName("tbl_knowledge_sources")
        self.table.setHorizontalHeaderLabels([self._t(c) for c in COLUMNS])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        self.summary = plain_label("")
        layout.addWidget(self.summary)
        self.status = plain_label("")
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.entries: List[dict] = []
        self.shown_kind = ""
        self._token: Optional[object] = None   # the newest read; an older one that arrives late is dropped
        from orchestrator.gui.worker_relay import worker_relay
        worker_relay().done.connect(self._loaded, Qt.QueuedConnection)
        self.reload()

    def _t(self, key: str, **values) -> str:
        return ui_text(key, self.lang, **values)

    def reload(self) -> None:
        """Reads the sources of the chosen kind in a worker thread (knowledge_sources.sources, the core function of
        `llm-cli knowledge sources --kind KIND`)."""
        from orchestrator.gui.worker_relay import worker_relay
        kind = str(self.kind.currentData() or "any")
        topic = self.topic.text().strip() or None
        token, relay, framework = object(), worker_relay(), self.framework
        self._token = token
        self.status.setText(self._t("sources.loading"))
        self.btn_refresh.setEnabled(False)

        def work() -> None:
            from orchestrator.Core import knowledge_sources
            from orchestrator.rag.errors import KnowledgeBaseError
            try:
                result = ("ok", knowledge_sources.sources(knowledge_sources.knowledge_base(framework), kind,
                                                          topic=topic))
            except knowledge_sources.KnowledgeOff:
                result = ("off", "")
            except KnowledgeBaseError as e:
                result = ("unreadable", str(e))
            except Exception as e:  # noqa: BLE001 - shown to the person; nothing was changed
                result = ("unreadable", f"{type(e).__name__}: {e}")
            relay.done.emit(token, (kind, result))

        threading.Thread(target=work, name="knowledge-sources", daemon=True).start()

    def _loaded(self, token: object, payload: Any) -> None:
        if token is not self._token:
            return
        self._token = None
        self.btn_refresh.setEnabled(True)
        kind, (outcome, value) = payload
        self.shown_kind = kind
        self.entries = list(value) if outcome == "ok" else []
        if outcome == "off":
            self.status.setText(self._t("sources.off"))
        elif outcome == "unreadable":
            self.status.setText(self._t("sources.unreadable", reason=one_line(value)))
        else:
            self.status.setText("")
        self._fill(kind, outcome == "ok")

    def _fill(self, kind: str, read: bool) -> None:
        from orchestrator.Core import knowledge_sources
        self.table.setRowCount(len(self.entries))
        for row, entry in enumerate(self.entries):
            share = str(entry.get("pack_share") or "")
            removed = knowledge_sources.removed_text(entry, lambda kind: self._t(f"sources.removed.{kind}"),
                                                     self._t("sources.removed.none"))
            values = (str(entry.get("source") or ""), self._kind_text(str(entry.get("kind") or "")),
                      self._topic_text(entry), removed, str(entry.get("licence") or "-"),
                      str(entry.get("copyright") or "-"),
                      self._t(f"sources.carried.{share}") if share in knowledge_sources.CARRIED else "-",
                      str(entry.get("share_reason") or ""))
            for column, value in enumerate(values):
                cell = QTableWidgetItem(one_line(value))   # plain text, never rich text
                if column == 0 and entry.get("title"):
                    cell.setToolTip(plain_tooltip(entry["title"]))
                self.table.setItem(row, column, cell)
        if not read:
            self.summary.setText("")
        elif not self.entries:
            self.summary.setText(self._t("sources.empty") if kind == "any" else
                                 self._t("sources.empty.kind", kind=self._kind_text(kind)))
        else:
            full, facts = knowledge_sources.totals(self.entries)
            self.summary.setText(self._t("sources.totals", n=len(self.entries), full=full, facts=facts))

    def _topic_text(self, entry: dict) -> str:
        """The topic or target a source belongs to, in the window's language ("-" for your own pages)."""
        if entry.get("target"):
            return self._t("sources.topic.target", name=str(entry["target"]))
        if entry.get("topic"):
            return self._t("sources.topic.topic", name=str(entry["topic"]))
        return "-"

    def _kind_text(self, kind: str) -> str:
        return self._t(f"sources.filter.{kind}") if kind in KIND_FILTER else kind


def open_knowledge_sources(window: Any) -> KnowledgeSourcesDialog:
    """Shows the Knowledge Sources window of the main window (fresh data each time it opens)."""
    old = getattr(window, "knowledge_sources_window", None)
    if old is not None:
        old.close()
    dialog = KnowledgeSourcesDialog(getattr(window, "framework", None), window)
    window.knowledge_sources_window = dialog
    dialog.show()
    dialog.raise_()
    return dialog


__all__ = ["COLUMNS", "KIND_FILTER", "KnowledgeSourcesDialog", "open_knowledge_sources"]
