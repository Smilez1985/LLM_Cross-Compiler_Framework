#!/usr/bin/env python3
"""
Ditto > Web Search...: `llm-cli ditto web status`, `use`, `search` and `setup` by mouse (GUI parity; DESIGN, Knowledge base
and Deep Ingest, the web search's rows).

The person chooses which ways Ditto's web search uses (the search engine on this computer, search APIs with their own keys,
a search page's HTML results), which providers to ask and where the search engine is; keys go into the secrets store, never
into a setting. "Set Up the Search Engine" copies the finished settings file, makes a secret and starts SearXNG on this
computer (it needs Docker or Podman and says so in plain words); "Stop the Search Engine" removes the container. A
test search shows addresses, titles and short texts of strangers as plain text, never a page.

Every action that reaches out or starts something runs in a plain worker thread whose result comes back on the
application's worker relay; the window stays usable. The choice is the person's alone: the same function writes it as the
command line does (web_search.configure), and no tool of an AI changes it.
"""

import threading
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QHBoxLayout, QHeaderView, QLineEdit,
                               QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from orchestrator.Core import web_search
from orchestrator.gui.plain import ask, button_text, one_line, plain_box, plain_label
from orchestrator.gui.worker_relay import worker_relay
from orchestrator.utils.localization import current_language, ui_text

HIT_COLUMNS = ("title", "origin", "address", "ways", "snippet")


class DittoWebWindow(QDialog):
    """Ditto's web search: the choice, the keys, the search engine and a test search."""

    def __init__(self, framework: Any, parent: Optional[QWidget] = None,
                 search_factory: Optional[Callable[[], Any]] = None, setup_runner: Optional[Callable[..., Any]] = None,
                 remove_runner: Optional[Callable[..., Any]] = None, lang: Optional[str] = None):
        super().__init__(parent)
        self.setObjectName("dlg_ditto_web")
        self.framework = framework
        self.lang = lang or current_language()
        self._factory = search_factory or (lambda: web_search.for_framework(framework))
        self._setup, self._remove = setup_runner, remove_runner
        self._jobs: Dict[object, Callable[[bool, Any], None]] = {}
        self.hits: List[Any] = []
        self.setWindowTitle(self._t("websearch.title"))
        self.resize(980, 700)

        layout = QVBoxLayout(self)
        layout.addWidget(plain_label(self._t("websearch.intro"), self))
        self.lbl_status = plain_label("", self)
        self.lbl_status.setObjectName("lbl_web_status")
        layout.addWidget(self.lbl_status)

        ways = QHBoxLayout()
        ways.addWidget(plain_label(self._t("websearch.ways"), self, wrap=False))
        self.way_boxes: Dict[str, QCheckBox] = {}
        for way in web_search.WAYS:
            box = QCheckBox(button_text(self._t(f"websearch.way.{way}")), self)
            box.setObjectName(f"chk_web_way_{way}")
            self.way_boxes[way] = box
            ways.addWidget(box)
        ways.addStretch(1)
        layout.addLayout(ways)

        layout.addWidget(plain_label(self._t("websearch.apis"), self))
        self.api_boxes: Dict[str, QCheckBox] = {}
        apis = QHBoxLayout()
        for name, provider in web_search.PROVIDERS.items():
            box = QCheckBox(button_text(provider.label), self)
            box.setObjectName(f"chk_web_api_{name}")
            self.api_boxes[name] = box
            apis.addWidget(box)
        apis.addStretch(1)
        layout.addLayout(apis)

        key = QHBoxLayout()
        key.addWidget(plain_label(self._t("websearch.key"), self, wrap=False))
        self.cb_key_provider = QComboBox(self)
        self.cb_key_provider.setObjectName("cb_web_key_provider")
        self.cb_key_provider.setAccessibleName(self._t("websearch.key"))
        for name, provider in web_search.PROVIDERS.items():
            self.cb_key_provider.addItem(provider.label, name)
        key.addWidget(self.cb_key_provider)
        self.edt_key = QLineEdit(self)
        self.edt_key.setObjectName("edt_web_key")
        self.edt_key.setEchoMode(QLineEdit.Password)
        self.edt_key.setPlaceholderText(self._t("websearch.key.placeholder"))
        self.edt_key.setAccessibleName(self._t("websearch.key.placeholder"))
        key.addWidget(self.edt_key, stretch=1)
        self.btn_key = self._button("websearch.key.save", "btn_web_key_save", self.save_key)
        key.addWidget(self.btn_key)
        layout.addLayout(key)

        where = QHBoxLayout()
        where.addWidget(plain_label(self._t("websearch.address"), self, wrap=False))
        self.edt_address = QLineEdit(self)
        self.edt_address.setObjectName("edt_web_address")
        self.edt_address.setAccessibleName(self._t("websearch.address"))
        where.addWidget(self.edt_address, stretch=1)
        where.addWidget(plain_label(self._t("websearch.zone"), self, wrap=False))
        self.edt_zone = QLineEdit(self)
        self.edt_zone.setObjectName("edt_web_zone")
        self.edt_zone.setAccessibleName(self._t("websearch.zone"))
        where.addWidget(self.edt_zone)
        self.btn_apply = self._button("websearch.apply", "btn_web_apply", self.apply)
        where.addWidget(self.btn_apply)
        layout.addLayout(where)

        engine = QHBoxLayout()
        engine.addWidget(plain_label(self._t("websearch.engine"), self, wrap=False))
        self.cb_engine_way = QComboBox(self)
        self.cb_engine_way.setObjectName("cb_web_engine_way")
        self.cb_engine_way.setAccessibleName(self._t("websearch.engine"))
        for way in ("own", "container"):       # the own Python folder first: no Docker unless the person wants it
            self.cb_engine_way.addItem(self._t(f"websearch.engine.{way}"), way)
        engine.addWidget(self.cb_engine_way)
        self.btn_setup = self._button("websearch.setup", "btn_web_setup", self.set_up_engine)
        engine.addWidget(self.btn_setup)
        self.btn_remove = self._button("websearch.remove", "btn_web_remove", self.remove_engine)
        engine.addWidget(self.btn_remove)
        engine.addStretch(1)
        layout.addLayout(engine)

        search = QHBoxLayout()
        self.edt_query = QLineEdit(self)
        self.edt_query.setObjectName("edt_web_query")
        self.edt_query.setPlaceholderText(self._t("websearch.query.placeholder"))
        self.edt_query.setAccessibleName(self._t("websearch.query.placeholder"))
        self.edt_query.returnPressed.connect(self.search)
        search.addWidget(self.edt_query, stretch=1)
        self.spn_count = QSpinBox(self)
        self.spn_count.setObjectName("spn_web_count")
        self.spn_count.setRange(1, web_search.MAX_COUNT)
        self.spn_count.setValue(web_search.DEFAULT_COUNT)
        self.spn_count.setAccessibleName(self._t("websearch.count"))
        search.addWidget(self.spn_count)
        self.btn_search = self._button("websearch.search", "btn_web_search", self.search)
        search.addWidget(self.btn_search)
        layout.addLayout(search)

        self.table = QTableWidget(0, len(HIT_COLUMNS), self)
        self.table.setObjectName("tbl_web_hits")
        self.table.setHorizontalHeaderLabels([self._t(f"websearch.col.{c}") for c in HIT_COLUMNS])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, stretch=1)
        self.lbl_result = plain_label("", self)
        self.lbl_result.setObjectName("lbl_web_result")
        layout.addWidget(self.lbl_result)

        worker_relay().done.connect(self._done, Qt.QueuedConnection)
        self.refresh()

    # --- helpers ------------------------------------------------------------------------------------------------

    def _t(self, key: str, **values) -> str:
        return ui_text(key, self.lang, **values)

    def _button(self, key: str, name: str, slot: Callable[[], None]) -> QPushButton:
        button = QPushButton(button_text(self._t(key)), self)
        button.setObjectName(name)
        button.setAutoDefault(False)
        button.clicked.connect(lambda _=False: slot())
        return button

    def _in_worker(self, work: Callable[[], Any], then: Callable[[bool, Any], None]) -> None:
        """Runs `work` in a worker thread; `then(ok, value)` runs in the GUI thread with its result or its error."""
        token, relay = object(), worker_relay()
        self._jobs[token] = then

        def run() -> None:
            try:
                result: Any = (True, work())
            except BaseException as e:  # noqa: BLE001 - shown to the person as text
                result = (False, e)
            relay.done.emit(token, result)

        threading.Thread(target=run, name="web-search-window", daemon=True).start()

    def _done(self, token: object, payload: Any) -> None:
        job = self._jobs.pop(token, None)
        if job is not None:
            ok, value = payload
            job(ok, value)

    def _tell(self, kind: str, text: str) -> None:
        plain_box(self, kind, self._t("websearch.title"), text)

    # --- the choice -----------------------------------------------------------------------------------------------

    def refresh(self) -> None:
        """Shows what is set now (web_search.status, the same as `ditto web status`)."""
        try:
            info = web_search.status(self._factory())
        except Exception as e:  # noqa: BLE001 - e.g. no settings; plain text
            self.lbl_status.setText(self._t("websearch.failed", reason=one_line(f"{type(e).__name__}: {e}")))
            return
        for way, box in self.way_boxes.items():
            box.setChecked(way in info["ways"])
        for api in info["apis"]:
            box = self.api_boxes[api["name"]]
            box.setChecked(api["listed"])
            box.setText(button_text(self._t("websearch.api.key" if api["key_saved"] else "websearch.api.nokey",
                                            name=api["label"])))
        self.edt_address.setText(info["address"])
        zone = getattr(self.framework, "config", None)
        try:
            self.edt_zone.setText(str(zone.get("web_search_brightdata_zone", "") or "") if zone is not None else "")
        except Exception:  # noqa: BLE001
            self.edt_zone.setText("")
        text = self._t("websearch.status.on", ways=", ".join(self._t(f"websearch.way.{w}") for w in info["ways"])) \
            if info["ways"] else self._t("websearch.status.off")
        if info["offline"]:
            text += " " + self._t("websearch.status.offline")
        self.lbl_status.setText(text)

    def apply(self) -> None:
        """Saves the choice (web_search.configure, the writer of `ditto web use`)."""
        ways = [w for w, box in self.way_boxes.items() if box.isChecked()]
        apis = [n for n, box in self.api_boxes.items() if box.isChecked()]
        try:
            saved = web_search.configure(getattr(self.framework, "config"), ways=ways or "off", apis=apis or "off",
                                         address=self.edt_address.text(), zone=self.edt_zone.text())
        except ValueError as e:
            self._tell("warning", one_line(e))
            return
        self.refresh()
        self.lbl_result.setText(self._t("websearch.saved" if saved else "websearch.notsaved"))

    def save_key(self) -> None:
        """Stores the key of the chosen provider in the secrets store (never in a setting, never shown again)."""
        provider = web_search.PROVIDERS[str(self.cb_key_provider.currentData())]
        value = self.edt_key.text().strip()
        if not value:
            self._tell("information", self._t("websearch.key.empty"))
            return
        manager = getattr(self.framework, "secrets_manager", None)
        try:
            from orchestrator.Core.secret_entries import store_secret
            if manager is None:
                raise RuntimeError(self._t("websearch.key.nostore"))
            store_secret(manager, provider.secret, value)
        except Exception as e:  # noqa: BLE001 - plain text; the key itself is never in a message
            self._tell("warning", self._t("websearch.key.failed", reason=one_line(type(e).__name__ if str(e) == "" else e)))
            return
        self.edt_key.clear()
        self.refresh()
        self.lbl_result.setText(self._t("websearch.key.saved", name=provider.label))

    # --- the search engine ------------------------------------------------------------------------------------------

    def _plain_errors(self) -> tuple:
        from orchestrator.Core import searxng_engine, searxng_native, searxng_setup
        return (searxng_engine.EngineError, searxng_native.NativeError, searxng_setup.SetupError)

    def _fail(self, value: Any) -> None:
        self.lbl_result.setText("")
        self._tell("warning", one_line(value) if isinstance(value, self._plain_errors())
                   else self._t("websearch.failed", reason=one_line(type(value).__name__)))

    def set_up_engine(self) -> None:
        """Sets up the search engine the chosen way, after it said what it installs and the person said yes (No is the
        default): `ditto web setup`."""
        from orchestrator.Core import searxng_engine, searxng_setup
        from orchestrator.Core.disk_plan import state_dir
        way = str(self.cb_engine_way.currentData() or "own")
        folder = state_dir(self.framework)
        shown = (searxng_setup.folder_of(folder) if folder is not None else "")
        text = self._t(f"websearch.explain.{way}", folder=str(shown), size=searxng_engine.SIZE_OWN,
                       image=searxng_setup.IMAGE, port=searxng_setup.HOST_PORT)
        if not ask(self, self._t("websearch.title"), text + "\n\n" + self._t("websearch.setup.ask")):
            return
        runner = self._setup or searxng_engine.setup
        config = getattr(self.framework, "config", None)
        self.lbl_result.setText(self._t("websearch.setup.running"))

        def work() -> Any:
            if folder is None:
                raise searxng_engine.EngineError(self._t("websearch.setup.nofolder"))
            return runner(way, folder, config)

        def then(ok: bool, value: Any) -> None:
            if not ok:
                self._fail(value)
                return
            self.refresh()
            self.lbl_result.setText(self._t("websearch.setup.done"))
        self._in_worker(work, then)

    def remove_engine(self) -> None:
        """Removes the search engine (the Python folder and the container) after a question (No by default):
        `ditto web setup --remove`."""
        from orchestrator.Core import searxng_engine
        from orchestrator.Core.disk_plan import state_dir
        if not ask(self, self._t("websearch.title"), self._t("websearch.remove.ask")):
            return
        folder = state_dir(self.framework)
        runner = self._remove or searxng_engine.remove
        config = getattr(self.framework, "config", None)

        def work() -> Any:
            if folder is None:
                raise searxng_engine.EngineError(self._t("websearch.setup.nofolder"))
            return runner(folder, config)

        def then(ok: bool, value: Any) -> None:
            if not ok:
                self._fail(value)
                return
            self.refresh()
            self.lbl_result.setText(self._t("websearch.remove.done"))
        self._in_worker(work, then)

    # --- a test search ---------------------------------------------------------------------------------------------

    def search(self) -> None:
        """Searches with every way that is on (`ditto web search`); the hits are plain text."""
        query, count = self.edt_query.text(), int(self.spn_count.value())
        self.lbl_result.setText(self._t("websearch.searching"))

        def then(ok: bool, value: Any) -> None:
            if not ok:
                self.hits = []
                self._fill()
                self.lbl_result.setText("")
                if isinstance(value, web_search.SearchRefused):
                    self._tell("warning", one_line(value))
                else:
                    self._tell("warning", self._t("websearch.failed", reason=one_line(type(value).__name__)))
                return
            self.hits = list(value.hits)
            self._fill()
            problems = [f"{w.way}: {w.reason}" for w in value.ways if w.status != "ok"]
            note = self._t("websearch.found", n=len(self.hits)) if self.hits else self._t("websearch.nohit")
            self.lbl_result.setText(note + ("\n" + "\n".join(one_line(p) for p in problems) if problems else ""))
        self._in_worker(lambda: self._factory().search(query, count), then)

    def _fill(self) -> None:
        self.table.setRowCount(len(self.hits))
        for row, hit in enumerate(self.hits):
            values = (hit.title, self._t(f"websearch.origin.{hit.origin}"), hit.address, ", ".join(hit.ways), hit.snippet)
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(one_line(value)))   # plain text, never rich text


__all__ = ["DittoWebWindow", "HIT_COLUMNS"]
