#!/usr/bin/env python3
"""
What the knowledge base holds, as the person sees it (Tools > Knowledge Sources...; DESIGN W1', round 8 Q-1/Q-1').

Every source with its kind (a web page, a page of an installed knowledge pack, the framework's own documents, other),
how much hidden text was left out of it before it was stored (knowledge_sources.removed_text, recon P1), the licence
and copyright notice it shows, what a knowledge pack would carry of it (its full text, only its address
and short facts, or nothing) and why. The kind filter lists only web pages, pack pages, documents or other sources.
The list comes from knowledge_sources.sources (RAGManager.list_sources, the same list as `llm-cli knowledge sources
--kind ...`); it opens the knowledge base, so it is read in a worker thread that reports through the one WorkerRelay.
Addresses, titles and notices are site text: shown as plain text only.

Pages that could not be crawled (DESIGN, Knowledge base and Deep Ingest): the person saves such a page as a PDF or as
HTML and adds the file here, with "Add a Saved Page..." or by dragging it onto the window (`llm-cli knowledge
add-private`, with the page's address in its field as `--source`). It becomes a private page, kind private, marked
private wherever it is listed, kept on this computer and never shared (rag/private.py). "Remove Private Page..." takes
the selected one out again after a question (No by default); the file the person saved is never touched. Adding and
removing run in a worker thread and report through the same WorkerRelay.
"""

import threading
from typing import Any, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
                               QHeaderView, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from orchestrator.gui.plain import ask, button_text, one_line, plain_box, plain_label, plain_tooltip
from orchestrator.utils.localization import current_language, ui_text

# the order of knowledge_sources.COLUMNS (the CLI's list): the topic or target is the third column on both
COLUMNS = ("sources.col.source", "sources.col.kind", "sources.col.topic", "sources.col.removed", "sources.col.licence",
           "sources.col.copyright", "sources.col.carried", "sources.col.why")
KIND_FILTER = ("any", "web", "pack", "docs", "private", "other")   # knowledge_sources.KIND_CHOICES, "any" first


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
        # the topic or target a source belongs to (`llm-cli knowledge sources --topic`; DESIGN round 23 item 1)
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
        # private pages: a page that could not be crawled, saved by the person (drag the file here, or choose it)
        self.setAcceptDrops(True)
        layout.addWidget(plain_label(self._t("sources.private.drop")))
        private = QHBoxLayout()
        self.address = QLineEdit(self)
        self.address.setObjectName("edt_knowledge_private_source")
        self.address.setAccessibleName(self._t("sources.private.address"))
        self.address.setPlaceholderText(self._t("sources.private.address.hint"))
        self.address.setClearButtonEnabled(True)
        private.addWidget(plain_label(self._t("sources.private.address"), wrap=False, selectable=False))
        private.addWidget(self.address, stretch=1)
        self.btn_add = QPushButton(button_text(self._t("sources.private.add")), self)
        self.btn_add.setObjectName("btn_knowledge_private_add")
        self.btn_add.setAutoDefault(False)
        self.btn_add.clicked.connect(self.choose_file)
        private.addWidget(self.btn_add)
        self.btn_remove = QPushButton(button_text(self._t("sources.private.remove")), self)
        self.btn_remove.setObjectName("btn_knowledge_private_remove")
        self.btn_remove.setAutoDefault(False)
        self.btn_remove.clicked.connect(self.remove_selected)
        private.addWidget(self.btn_remove)
        layout.addLayout(private)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.entries: List[dict] = []
        self._note = ""                        # the sentence shown once the list is read again
        self._jobs: dict = {}                  # token -> what to do with the result of adding or removing a page
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
        job = self._jobs.pop(token, None)
        if job is not None:   # the result of adding or removing a private page, not of a read
            ok, value = payload[1]
            job(ok, value)
            return
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
            self.status.setText(self._note)   # what adding or removing a page told, kept after the list is read again
        self._note = ""
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


    # --- private pages (`llm-cli knowledge add-private`, `knowledge remove-private`) -----------------------------------

    def dragEnterEvent(self, event: Any) -> None:  # noqa: N802 - Qt's name
        if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event: Any) -> None:  # noqa: N802 - Qt's name
        files = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        event.acceptProposedAction()
        for path in files:
            self.add_file(path)

    def choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self._t("sources.private.pick"), "",
                                              self._t("sources.private.filter"))
        if path:
            self.add_file(path)

    def _tell(self, kind: str, text: str) -> None:
        plain_box(self, kind, self._t("sources.title"), text)

    def _in_worker(self, work: Any, then: Any) -> None:
        """Runs `work` in a worker thread; `then(ok, value)` runs in the GUI thread with its result or error."""
        from orchestrator.gui.worker_relay import worker_relay
        token, relay = object(), worker_relay()
        self._jobs[token] = then

        def run() -> None:
            try:
                result: Any = (True, work())
            except BaseException as e:  # noqa: BLE001 - shown to the person as text
                result = (False, e)
            relay.done.emit(token, ("job", result))

        threading.Thread(target=run, name="knowledge-private", daemon=True).start()

    def add_file(self, path: str) -> None:
        """Adds the saved file as a private page (the page's address from its field, when the person gave one)."""
        address = self.address.text().strip()
        framework = self.framework

        def work() -> Any:
            from orchestrator.Core import knowledge_sources
            from orchestrator.Core.private_pages import add_private_page
            return add_private_page(knowledge_sources.knowledge_base(framework), path, address)

        def then(ok: bool, value: Any) -> None:
            if ok:
                note = self._t("sources.private.added", source=one_line(value.source))
                self.reload()
                self._note = note
            else:
                self._tell("warning", self._t("sources.private.failed", reason=one_line(value)))
        self._in_worker(work, then)

    def selected_private(self) -> Optional[dict]:
        row = self.table.currentRow()
        entry = self.entries[row] if 0 <= row < len(self.entries) else None
        return entry if entry is not None and entry.get("kind") == "private" else None

    def remove_selected(self) -> None:
        """Removes the selected private page after a question (No by default); the saved file is not touched."""
        entry = self.selected_private()
        if entry is None:
            self._tell("information", self._t("sources.private.select"))
            return
        label = str(entry["source"])
        if not ask(self, self._t("sources.title"), self._t("sources.private.remove.ask", source=one_line(label))):
            return
        framework = self.framework

        def work() -> Any:
            from orchestrator.Core import knowledge_sources
            from orchestrator.Core.rag_manager import OWN_HOLDER
            rag = knowledge_sources.knowledge_base(framework)
            if not rag.release_holder(OWN_HOLDER, sources=lambda name: name == label):
                raise RuntimeError(str(rag.last_error or "It could not be removed."))
            return label

        def then(ok: bool, value: Any) -> None:
            if ok:
                note = self._t("sources.private.removed", source=one_line(label))
                self.reload()
                self._note = note
            else:
                self._tell("warning", self._t("sources.private.failed", reason=one_line(value)))
        self._in_worker(work, then)


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
