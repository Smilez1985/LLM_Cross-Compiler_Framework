#!/usr/bin/env python3
"""
Ditto > Local Models...: `llm-cli ditto models list` and `llm-cli ditto models download [TIER]` by mouse (W1').

The table shows every tier of Ditto's pinned model catalogue with its file, size, licence, whether it is downloaded,
and which one is recommended for this computer (and why), chosen earlier for the local server, only on request or not
pinned: runtime/ditto_models.models_overview, the same call as the CLI. "Download the recommended model..." and
"Download selected..." download at the pinned revision with LocalLLMRuntime.download_model (size and sha256 checked),
after the CLI's question with repo, file, licence, size and folder ("No" is the default button).

Reading the catalogue probes the hardware, and a download takes minutes: both run in a plain worker thread whose
progress and result come back on the application's worker relay. Cancel stops a download between two pieces and keeps
the `.part` file for a later resume. Closing the window hides it; a running download goes on and the window shows it
again when it is opened. Every text is plain text; the person sees the full models folder; nothing goes to an AI.
"""

import threading
import time
from typing import Any, Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QProgressBar, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from orchestrator.gui.plain import ask, button_text, one_line, plain_box, plain_label
from orchestrator.gui.worker_relay import worker_relay
from orchestrator.runtime.ditto_models import gib, model_to_download, models_overview
from orchestrator.utils.localization import current_language, ui_text

COLUMNS = ("tier", "file", "size", "license", "status", "note")
PROGRESS_EVERY = 0.2   # seconds between two progress updates on the GUI thread


class LocalModelsWindow(QDialog):
    """Ditto's model catalogue with Download (the recommended tier or the selected one)."""

    def __init__(self, framework: Any, parent: Optional[QWidget] = None,
                 runtime_factory: Optional[Callable[[], Any]] = None, lang: Optional[str] = None):
        super().__init__(parent)
        self.setObjectName("dlg_ditto_models")
        self.framework = framework
        self.lang = lang or current_language()
        self._factory = runtime_factory or self._runtime
        self.view: Any = None
        self._load_token: Optional[object] = None
        self._job: Optional[object] = None          # the token of the running download (one at a time)
        self._progress_token: Optional[object] = None
        self._stop = threading.Event()
        self.setWindowTitle(self._t("ditto_models.title"))
        self.resize(980, 460)

        layout = QVBoxLayout(self)
        self.lbl_intro = plain_label("", self)
        layout.addWidget(self.lbl_intro)
        self.lbl_recommended = plain_label("", self)
        self.lbl_recommended.setObjectName("lbl_ditto_models_recommended")
        layout.addWidget(self.lbl_recommended)
        self.table = QTableWidget(0, len(COLUMNS), self)
        self.table.setObjectName("tbl_ditto_models")
        self.table.setHorizontalHeaderLabels([self._t(f"ditto_models.col.{c}") for c in COLUMNS])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, stretch=1)

        self.progress = QProgressBar(self)
        self.progress.setObjectName("pb_ditto_models")
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        self.lbl_busy = plain_label("", self)
        self.lbl_busy.setObjectName("lbl_ditto_models_busy")
        layout.addWidget(self.lbl_busy)

        row = QHBoxLayout()
        self.btn_recommended = self._button("ditto_models.download_recommended",
                                            "btn_ditto_models_download_recommended", self.download_recommended)
        self.btn_download = self._button("ditto_models.download", "btn_ditto_models_download", self.download_selected)
        self.btn_refresh = self._button("ditto_models.refresh", "btn_ditto_models_refresh", self.refresh)
        self.btn_cancel = self._button("ditto_models.cancel", "btn_ditto_models_cancel", self.cancel)
        self.btn_cancel.setVisible(False)
        self.btn_close = self._button("ditto_models.close", "btn_ditto_models_close", self.close)
        for button in (self.btn_recommended, self.btn_download, self.btn_refresh):
            row.addWidget(button)
        row.addStretch(1)
        row.addWidget(self.btn_cancel)
        row.addWidget(self.btn_close)
        layout.addLayout(row)
        worker_relay().done.connect(self._on_relay, Qt.QueuedConnection)
        self.refresh()

    # --- building blocks --------------------------------------------------------------------------------------------

    def _t(self, key: str, **values: Any) -> str:
        return ui_text(key, self.lang, **values)

    def _button(self, key: str, name: str, slot: Callable[[], Any]) -> QPushButton:
        button = QPushButton(button_text(self._t(key)), self)
        button.setObjectName(name)
        button.setAutoDefault(False)
        button.clicked.connect(lambda _=False: slot())
        return button

    def _runtime(self) -> Any:
        from orchestrator.runtime.llama_server import LocalLLMRuntime
        return LocalLLMRuntime.from_framework(self.framework)   # the models folder the CLI and Ditto use

    def _tell(self, kind: str, text: str) -> None:
        plain_box(self, kind, self._t("ditto_models.title"), text)

    def _start(self, work: Callable[[], Any], name: str) -> object:
        """Runs `work` in a worker thread; its result (or the exception) comes back on the relay with the token."""
        token, relay = object(), worker_relay()

        def run() -> None:
            try:
                result: Any = (True, work())
            except BaseException as e:  # noqa: BLE001 - shown to the person as text
                result = (False, e)
            relay.done.emit(token, result)

        threading.Thread(target=run, name=name, daemon=True).start()
        return token

    # --- the list (`ditto models list`) -----------------------------------------------------------------------------

    def refresh(self) -> None:
        """Reads the catalogue and the recommendation again (in a worker thread: it probes the hardware)."""
        self.lbl_recommended.setText(self._t("ditto_models.loading"))
        factory = self._factory
        self._load_token = self._start(lambda: models_overview(factory()), "ditto-models-list")

    def _show(self, view: Any) -> None:
        self.view = view
        self.lbl_intro.setText(self._t("ditto_models.intro", folder=str(view.folder)))
        text = self._t("ditto_models.recommended", tier=one_line(view.recommended.tier), why=one_line(view.why))
        if view.preferred and view.preferred != view.recommended.tier:
            text += "\n" + self._t("ditto_models.chosen", tier=one_line(view.preferred))
        self.lbl_recommended.setText(text)
        selected = self.selected_tier() or view.recommended.tier
        self.table.setRowCount(len(view.rows))
        for index, row in enumerate(view.rows):
            m = row.model
            cells = (m.tier, m.file, gib(m.size), m.license, self._t(f"ditto_models.state.{row.state}"),
                     ", ".join(self._t(f"ditto_models.note.{note}") for note in row.notes()))
            for column, value in enumerate(cells):
                item = QTableWidgetItem(one_line(value))
                item.setData(Qt.UserRole, m.tier)
                self.table.setItem(index, column, item)
            if m.tier == selected:
                self.table.selectRow(index)

    def selected_tier(self) -> Optional[str]:
        items = self.table.selectedItems()
        return items[0].data(Qt.UserRole) if items else None

    # --- download (`ditto models download [TIER]`) ------------------------------------------------------------------

    def download_recommended(self) -> bool:
        return self._download(None)

    def download_selected(self) -> bool:
        tier = self.selected_tier()
        if tier is None:
            self._tell("information", self._t("ditto_models.select_first"))
            return False
        return self._download(tier)

    def _download(self, tier: Optional[str]) -> bool:
        """Asks the CLI's question (repo, file, licence, size, folder; No by default), then downloads in a worker
        thread. True when the download was started."""
        if self._job is not None:
            self._tell("information", self._t("ditto_models.busy"))
            return False
        try:
            rt = self._factory()
            model, why = model_to_download(rt, tier)
            ready = rt.model_ready(model)
        except KeyError as e:   # the catalogue changed since the list was read: the known tiers
            self._tell("warning", self._t("ditto_models.failed", reason=one_line(e.args[0] if e.args else e)))
            return False
        except Exception as e:  # noqa: BLE001 - e.g. no per-user folder: plain text
            self._tell("warning", self._t("ditto_models.failed", reason=one_line(e)))
            return False
        if ready:
            self._tell("information", self._t("ditto_models.ready", file=one_line(model.file)))
            return False
        if model.pinned:   # an unpinned model is refused by the download itself, before anything is fetched
            values = dict(repo=one_line(model.repo), file=one_line(model.file), license=one_line(model.license),
                          size=gib(model.size), folder=str(rt.models_dir))
            question = (self._t("ditto_models.question_recommended", why=one_line(why), **values) if why is not None
                        else self._t("ditto_models.question", **values))
            if not ask(self, self._t("ditto_models.title"), question):
                return False
        self._run_download(rt, model)
        return True

    def _run_download(self, rt: Any, model: Any) -> None:
        self._stop = threading.Event()
        stop, relay = self._stop, worker_relay()
        progress_token = self._progress_token = object()
        last = [0.0]

        def progress(name: str, done: int, total: Optional[int]) -> None:
            now = time.monotonic()
            if now - last[0] >= PROGRESS_EVERY or (total and done >= total):
                last[0] = now
                relay.done.emit(progress_token, (name, done, total))

        self._set_busy(True, self._t("ditto_models.downloading", file=one_line(model.file)))
        self._job = self._start(lambda: rt.download_model(model.tier, progress=progress, stop=stop),
                                "ditto-models-download")

    def cancel(self) -> None:
        if self._job is not None:
            self._stop.set()
            self.lbl_busy.setText(self._t("ditto_models.cancelling"))

    def _set_busy(self, busy: bool, text: str = "") -> None:
        for button in (self.btn_recommended, self.btn_download, self.btn_refresh):
            button.setEnabled(not busy)
        self.btn_cancel.setVisible(busy)
        self.progress.setVisible(busy)
        if busy:
            self.progress.setRange(0, 0)
        self.lbl_busy.setText(text)

    # --- results from the workers -----------------------------------------------------------------------------------

    def _on_relay(self, token: object, result: Any) -> None:
        if token is self._progress_token and self._job is not None:
            name, done, total = result
            if total:
                self.progress.setRange(0, 1000)
                self.progress.setValue(int(done * 1000 / total))
            self.lbl_busy.setText(self._t("ditto_models.progress", name=one_line(name), done=gib(done),
                                          total=gib(total)))
        elif token is self._load_token:
            self._load_token = None
            ok, value = result
            if ok:
                self._show(value)
            else:
                self.lbl_recommended.setText(self._t("ditto_models.load_failed", reason=one_line(value)))
        elif token is self._job:
            self._job = self._progress_token = None
            self._set_busy(False)
            ok, value = result
            from orchestrator.utils.download import DownloadStopped
            if ok:
                self.lbl_busy.setText(self._t("ditto_models.saved", path=str(value)))
            elif isinstance(value, DownloadStopped):   # the .part file stays for a resume
                self.lbl_busy.setText(self._t("ditto_models.cancelled"))
            else:
                self.lbl_busy.setText(self._t("ditto_models.failed", reason=one_line(value)))
                self._tell("warning", self.lbl_busy.text())
            self.refresh()

    @property
    def busy(self) -> bool:
        return self._job is not None


__all__ = ["COLUMNS", "LocalModelsWindow"]
