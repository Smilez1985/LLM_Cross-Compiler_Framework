#!/usr/bin/env python3
"""
Ditto > Ditto's Archive...: `llm-cli ditto archive list`, `search`, `show`, `confirm`, `unconfirm`, `delete`, `undo` and
`level` by mouse (GUI parity; DESIGN, Ditto, the archive's rows).

The table lists Ditto's archive entries (or, with "Show the copies of replaced entries", the copies kept of replaced,
person-confirmed entries) with their status, ground, independent sources, key and sentence; the search box and "Confirmed
only" search them like the CLI's search; the status filter narrows the list. The detail below the table shows the
selected entry in full with its evidence. Confirm, Take Confirmation Back, Delete... and Undo Replacement... are the
person's alone (the tools of an AI have no such action): Delete and Undo ask first ("No" is the default). The level
decides how many independent sources Ditto needs to confirm an entry by itself (low 3, mid 5, high 7); it is the
person's setting `ditto_archive_security`, written by the same function as the CLI's `ditto archive level`.

Reading the archive can load the embedding model, so every action runs in a plain worker thread whose result comes back
on the application's worker relay; the window stays usable. Every text is plain text; an entry's sentence and evidence
are shown as they are stored, on one line in the table, and nothing here goes to an AI.
"""

import threading
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QHBoxLayout, QHeaderView, QLineEdit,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from orchestrator.gui.plain import ask, button_text, one_line, plain_box, plain_label
from orchestrator.gui.worker_relay import worker_relay
from orchestrator.schemas.archive import LEVELS, SETTING, normalize_level
from orchestrator.utils.localization import current_language, ui_text

COLUMNS = ("status", "ground", "sources", "key", "text", "updated")
FOUND_LIMIT = 50   # the most entries a search shows


class DittoArchiveWindow(QDialog):
    """Ditto's archive: the list, the search, the person's actions and the level."""

    def __init__(self, framework: Any, parent: Optional[QWidget] = None,
                 archive_factory: Optional[Callable[[], Any]] = None, lang: Optional[str] = None):
        super().__init__(parent)
        self.setObjectName("dlg_ditto_archive")
        self.framework = framework
        self.lang = lang or current_language()
        self._factory = archive_factory or (lambda: getattr(framework, "ditto_archive", None))
        self.entries: List[Any] = []           # what the table shows now
        self.copies: List[Any] = []            # every copy kept (to know which entry can be undone)
        self._jobs: Dict[object, Callable[[bool, Any], None]] = {}
        self.setWindowTitle(self._t("archive.title"))
        self.resize(1040, 620)

        layout = QVBoxLayout(self)
        layout.addWidget(plain_label(self._t("archive.intro"), self))
        self.lbl_overview = plain_label("", self)
        self.lbl_overview.setObjectName("lbl_archive_overview")
        layout.addWidget(self.lbl_overview)

        search = QHBoxLayout()
        self.edt_search = QLineEdit(self)
        self.edt_search.setObjectName("edt_archive_search")
        self.edt_search.setPlaceholderText(self._t("archive.search.placeholder"))
        self.edt_search.setAccessibleName(self._t("archive.search.placeholder"))
        self.edt_search.returnPressed.connect(self.refresh)
        search.addWidget(self.edt_search, stretch=1)
        self.btn_search = self._button("archive.search", "btn_archive_search", self.refresh)
        search.addWidget(self.btn_search)
        self.chk_confirmed_only = QCheckBox(button_text(self._t("archive.confirmed_only")), self)
        self.chk_confirmed_only.setObjectName("chk_archive_confirmed_only")
        self.chk_confirmed_only.toggled.connect(lambda _=False: self.refresh())
        search.addWidget(self.chk_confirmed_only)
        layout.addLayout(search)

        filters = QHBoxLayout()
        self.cb_status = QComboBox(self)
        self.cb_status.setObjectName("cb_archive_status")
        self.cb_status.setAccessibleName(self._t("archive.col.status"))
        for value in ("", "unconfirmed", "confirmed"):
            self.cb_status.addItem(self._t(f"archive.status.{value or 'all'}"), value)
        self.cb_status.currentIndexChanged.connect(lambda _=0: self.refresh())
        filters.addWidget(self.cb_status)
        self.chk_copies = QCheckBox(button_text(self._t("archive.show_copies")), self)
        self.chk_copies.setObjectName("chk_archive_copies")
        self.chk_copies.toggled.connect(lambda _=False: self.refresh())
        filters.addWidget(self.chk_copies)
        filters.addStretch(1)
        layout.addLayout(filters)

        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setObjectName("tbl_archive_entries")
        self.table.setHorizontalHeaderLabels([self._t(f"archive.col.{c}") for c in COLUMNS])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._selected)
        layout.addWidget(self.table, stretch=1)
        self.lbl_detail = plain_label(self._t("archive.detail.empty"), self)
        self.lbl_detail.setObjectName("lbl_archive_detail")
        layout.addWidget(self.lbl_detail)
        self.lbl_state = plain_label("", self)
        self.lbl_state.setObjectName("lbl_archive_state")
        layout.addWidget(self.lbl_state)

        level = QHBoxLayout()
        level.addWidget(plain_label(self._t("archive.level.label"), self, wrap=False))
        self.cb_level = QComboBox(self)
        self.cb_level.setObjectName("cb_archive_level")
        self.cb_level.setAccessibleName(self._t("archive.level.label"))
        for value in LEVELS:
            self.cb_level.addItem(self._t(f"archive.level.{value}"), value)
        self.cb_level.setCurrentIndex(LEVELS.index(normalize_level(self._config_get(SETTING))))
        level.addWidget(self.cb_level)
        self.btn_level = self._button("archive.level.apply", "btn_archive_level_apply", self.apply_level)
        level.addWidget(self.btn_level)
        level.addStretch(1)
        layout.addLayout(level)

        row = QHBoxLayout()
        self.btn_confirm = self._button("archive.confirm", "btn_archive_confirm", self.confirm)
        self.btn_unconfirm = self._button("archive.unconfirm", "btn_archive_unconfirm", self.unconfirm)
        self.btn_delete = self._button("archive.delete", "btn_archive_delete", self.delete)
        self.btn_undo = self._button("archive.undo", "btn_archive_undo", self.undo)
        self.btn_refresh = self._button("archive.refresh", "btn_archive_refresh", self.refresh)
        self.btn_close = self._button("archive.close", "btn_archive_close", self.close)
        for button in (self.btn_confirm, self.btn_unconfirm, self.btn_delete, self.btn_undo, self.btn_refresh):
            row.addWidget(button)
        row.addStretch(1)
        row.addWidget(self.btn_close)
        layout.addLayout(row)
        self._enable()
        worker_relay().done.connect(self._on_relay, Qt.QueuedConnection)
        self.refresh()

    # --- building blocks ----------------------------------------------------------------------------------------------

    def _t(self, key: str, **values: Any) -> str:
        return ui_text(key, self.lang, **values)

    def _button(self, key: str, name: str, slot: Callable[[], Any]) -> QPushButton:
        button = QPushButton(button_text(self._t(key)), self)
        button.setObjectName(name)
        button.setAutoDefault(False)
        button.clicked.connect(lambda _=False: slot())
        return button

    def _config_get(self, key: str) -> Any:
        getter = getattr(getattr(self.framework, "config", None), "get", None)
        return getter(key) if callable(getter) else None

    def _tell(self, kind: str, text: str) -> None:
        plain_box(self, kind, self._t("archive.title"), text)

    def _start(self, work: Callable[[], Any], then: Callable[[bool, Any], None]) -> None:
        """Runs `work` in a worker thread; `then(ok, value_or_error)` runs in the GUI thread with its result."""
        token, relay = object(), worker_relay()
        self._jobs[token] = then

        def run() -> None:
            try:
                result: Any = (True, work())
            except BaseException as e:  # noqa: BLE001 - shown to the person as text
                result = (False, e)
            relay.done.emit(token, result)

        threading.Thread(target=run, name="ditto-archive", daemon=True).start()

    def _on_relay(self, token: object, result: Any) -> None:
        then = self._jobs.pop(token, None)
        if then is not None:
            ok, value = result
            then(ok, value)

    # --- the list and the search (`ditto archive list`, `ditto archive search`) --------------------------------------

    def refresh(self) -> None:
        """Reads the archive again with the search, the filter and the checkboxes as they are (in a worker thread)."""
        query = self.edt_search.text().strip()
        status = self.cb_status.currentData() or ""
        copies = self.chk_copies.isChecked()
        confirmed_only = self.chk_confirmed_only.isChecked()
        self.lbl_state.setText(self._t("archive.loading"))
        factory = self._factory

        def read():
            archive = factory()
            if archive is None:
                raise RuntimeError(self._t("archive.failed", reason="-"))
            overview, kept = archive.overview(), archive.entries(kind="copy")
            if query and not copies:
                found = [h.entry for h in archive.search(query, limit=FOUND_LIMIT, confirmed_only=confirmed_only)]
            else:
                found = archive.entries(status="confirmed" if confirmed_only and not status else status,
                                        kind="copy" if copies else "entry")
            return overview, found, kept

        self._start(read, self._show)

    def _show(self, ok: bool, value: Any) -> None:
        if not ok:
            self.lbl_state.setText(self._t("archive.failed", reason=one_line(value)))
            return
        overview, found, kept = value
        self.entries, self.copies = list(found), list(kept)
        self.lbl_overview.setText(self._t("archive.overview", entries=overview["entries"],
                                          confirmed=overview["confirmed"], copies=overview["copies"],
                                          level=self._t(f"archive.level.{overview['level']}"),
                                          needed=overview["needed"]))
        self.lbl_state.setText("" if self.entries else self._t("archive.empty"))
        self.table.setRowCount(len(self.entries))
        for index, e in enumerate(self.entries):
            cells = (self._state_text(e), self._t(f"archive.ground.{e.ground}"), str(e.independent_sources), e.key,
                     e.text, e.updated)
            for column, text in enumerate(cells):
                item = QTableWidgetItem(one_line(text))
                item.setData(Qt.UserRole, e.id)
                self.table.setItem(index, column, item)
        self._selected()

    def _state_text(self, entry: Any) -> str:
        if entry.kind == "copy":
            return self._t("archive.state.copy")
        if entry.status == "confirmed":
            return self._t("archive.state.confirmed", who=self._t(f"archive.who.{entry.confirmed_by or 'person'}"))
        return self._t("archive.state.unconfirmed")

    # --- the selected entry (`ditto archive show`) -------------------------------------------------------------------

    def selected_entry(self) -> Optional[Any]:
        items = self.table.selectedItems()
        wanted = items[0].data(Qt.UserRole) if items else None
        return next((e for e in self.entries if e.id == wanted), None)

    def _selected(self) -> None:
        entry = self.selected_entry()
        if entry is None:
            self.lbl_detail.setText(self._t("archive.detail.empty"))
        else:
            who = (self._t(f"archive.who.{entry.confirmed_by}") if entry.confirmed_by
                   else self._t("archive.detail.nothing"))
            evidence = "\n".join(one_line(item) for item in entry.evidence) or self._t("archive.detail.nothing")
            text = self._t("archive.detail", entry=one_line(entry.key), text=one_line(entry.text),
                           ground=self._t(f"archive.ground.{entry.ground}"), sources=entry.independent_sources,
                           who=who, evidence=evidence)
            if entry.kind == "copy":
                text += "\n" + self._t("archive.detail.copy", when=one_line(entry.copied_at or ""))
            self.lbl_detail.setText(text)
        self._enable()

    def _enable(self) -> None:
        entry = self.selected_entry()
        is_entry = entry is not None and entry.kind == "entry"
        self.btn_confirm.setEnabled(is_entry and entry.status != "confirmed")
        self.btn_unconfirm.setEnabled(is_entry and entry.status == "confirmed")
        self.btn_delete.setEnabled(entry is not None)
        self.btn_undo.setEnabled(entry is not None and any(c.key == entry.key for c in self.copies))

    # --- the person's actions (`ditto archive confirm`, `unconfirm`, `delete`, `undo`) -------------------------------

    def _act(self, work: Callable[[Any], Any], done_key: str, entry: Any) -> None:
        factory = self._factory

        def run():
            return work(factory())

        def finished(ok: bool, value: Any) -> None:
            if ok:
                self.lbl_state.setText(self._t(done_key, entry=one_line(entry.key)))
                self.refresh()
            else:
                self._tell("warning", self._t("archive.failed", reason=one_line(value)))
        self._start(run, finished)

    def confirm(self) -> None:
        entry = self.selected_entry()
        if entry is None:
            self._tell("information", self._t("archive.select_first"))
            return
        self._act(lambda archive: archive.confirm(entry.id), "archive.done", entry)

    def unconfirm(self) -> None:
        entry = self.selected_entry()
        if entry is None:
            self._tell("information", self._t("archive.select_first"))
            return
        self._act(lambda archive: archive.unconfirm(entry.id), "archive.done", entry)

    def delete(self) -> None:
        entry = self.selected_entry()
        if entry is None:
            self._tell("information", self._t("archive.select_first"))
            return
        if not ask(self, self._t("archive.title"), self._t("archive.delete.ask", entry=one_line(entry.key),
                                                          text=one_line(entry.text))):
            return
        self._act(lambda archive: archive.delete(entry.id), "archive.deleted", entry)

    def undo(self) -> None:
        entry = self.selected_entry()
        if entry is None:
            self._tell("information", self._t("archive.select_first"))
            return
        if not any(c.key == entry.key for c in self.copies):
            self._tell("information", self._t("archive.undo.none"))
            return
        if not ask(self, self._t("archive.title"), self._t("archive.undo.ask", entry=one_line(entry.key))):
            return
        self._act(lambda archive: archive.undo(entry.key), "archive.done", entry)

    # --- the level (`ditto archive level`) -----------------------------------------------------------------------------

    def apply_level(self) -> None:
        """Writes the chosen level with the one function the CLI uses (the person's setting, never an AI's)."""
        from orchestrator.Core.ditto_archive import set_level
        level = self.cb_level.currentData()
        try:
            saved = set_level(self.framework.config, level)
        except ValueError as e:
            self._tell("warning", one_line(e))
            return
        self.lbl_state.setText(self._t("archive.level.saved", level=self._t(f"archive.level.{level}")))
        if not saved:
            self._tell("warning", self._t("archive.level.not_saved"))
        self.refresh()
