#!/usr/bin/env python3
"""
Build > Benchmark... and the Models tab's Benchmark...: llama-bench on a GGUF model of the model library, on this
computer or on a paired board, with the results as a table (DESIGN "GUI and the person's path", the row "The Models tab
is one list").

- This computer: runtime/bench.BenchRunner.run off the GUI thread, with the rules of the tool run_benchmark: Ditto's
  catalogue models natively, every other GGUF only in the Docker sandbox with the llama-bench built in the builder
  container (a model made for a board, measured natively here, would say how fast this computer is). The model file
  is hashed in the worker; the runner checks it again through the file it opens. Cancel sets the run's stop event.
- A paired board: the Deployment tab's BoardBenchWorker (device/service.benchmark): the model's path on the board, the
  question about a model that is not a catalogue model (No by default), a passed run recorded as "tested on".
- The results: one row per llama-bench test (pp512, tg128) with tokens per second and their deviation, threads,
  backend, where, model and time; the runs kept in <state>/benchmarks/runs/ are listed below.

The command line does the same: `llm-cli models bench NAME` and `llm-cli models bench-results` on this computer,
`llm-cli device runtime verify DEVICE --bench-model PATH` on a board.
"""

import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QComboBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
                               QProgressBar, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from orchestrator.Core.model_library import record_rows
from orchestrator.gui.plain import ask, button_text, plain_box, plain_label
from orchestrator.utils.localization import ui_text

MAX_EARLIER = 20


class LocalBenchWorker(QThread):
    """One llama-bench run on this computer through BenchRunner (off the GUI thread)."""
    done = Signal(object)    # the BenchRun as a dict
    failed = Signal(str)
    stopped = Signal()

    def __init__(self, framework: Any, model: Path, label: str, threads: Optional[int]):
        super().__init__()
        self.framework, self.model, self.label, self.threads = framework, Path(model), label, threads
        self.stop = threading.Event()

    def cancel(self) -> None:
        self.stop.set()

    def run(self) -> None:
        from orchestrator.Core.build_plan import folder
        from orchestrator.runtime.bench import BenchError, BenchRunner, BenchStopped
        from orchestrator.utils.helpers import sha256_file
        state = getattr(getattr(self.framework, "config", None), "user_state_dir", None)
        try:
            runner = BenchRunner(Path(state), cache_dir=folder(self.framework, "cache_dir", "cache"))
            digest = sha256_file(self.model)
            trusted = digest in runner.trusted_sha256()
            run = runner.run(self.model, kind="tier_speed" if trusted else "artifact_speed", label=self.label,
                             trusted=trusted, sha256=digest, sandbox=not trusted, stop=self.stop,
                             threads=self.threads)
        except BenchStopped:
            self.stopped.emit()
            return
        except BenchError as e:
            self.failed.emit(" ".join(x for x in (str(e), e.next) if x))
            return
        except Exception as e:  # noqa: BLE001 - a failure no step expected: shown as plain text
            self.failed.emit(str(e))
            return
        self.done.emit(asdict(run))


class BenchmarkWindow(QMainWindow):
    """The Benchmark window (not modal; a second open shows the same window)."""

    def __init__(self, framework_manager: Any, parent: Optional[QWidget] = None, model_key: Optional[str] = None):
        super().__init__(parent)
        self.framework = framework_manager
        self.worker: Optional[QThread] = None
        self.rows: Dict[str, Any] = {}
        self.setObjectName("win_benchmark")
        self.setWindowTitle(ui_text("bench.title"))
        self.resize(900, 620)
        self._init_ui()
        self.reload(select=model_key)

    # --- the window ---

    def _init_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.addWidget(plain_label(ui_text("bench.intro")))
        form = QFormLayout()
        self.cb_model = QComboBox()
        self.cb_model.setObjectName("cb_bench_model")
        self.cb_model.setAccessibleName(ui_text("bench.model").rstrip(":"))
        self.cb_model.currentIndexChanged.connect(lambda *_: self._update_note())
        form.addRow(ui_text("bench.model"), self.cb_model)
        self.cb_where = QComboBox()
        self.cb_where.setObjectName("cb_bench_where")
        self.cb_where.setAccessibleName(ui_text("bench.where").rstrip(":"))
        self.cb_where.addItem(ui_text("bench.where.here"), "here")
        self.cb_where.addItem(ui_text("bench.where.board"), "board")
        self.cb_where.currentIndexChanged.connect(lambda *_: self._update_note())
        form.addRow(ui_text("bench.where"), self.cb_where)
        self.cb_board = QComboBox()
        self.cb_board.setObjectName("cb_bench_board")
        self.cb_board.setAccessibleName(ui_text("bench.board").rstrip(":"))
        form.addRow(ui_text("bench.board"), self.cb_board)
        self.txt_board_model = QLineEdit()
        self.txt_board_model.setObjectName("txt_bench_board_model")
        self.txt_board_model.setPlaceholderText(ui_text("bench.board_model.hint"))
        self.txt_board_model.setAccessibleName(ui_text("bench.board_model").rstrip(":"))
        form.addRow(ui_text("bench.board_model"), self.txt_board_model)
        self.spin_threads = QSpinBox()
        self.spin_threads.setObjectName("spin_bench_threads")
        self.spin_threads.setRange(0, 256)
        self.spin_threads.setSpecialValueText(ui_text("bench.threads.auto"))
        self.spin_threads.setAccessibleName(ui_text("bench.threads").rstrip(":"))
        form.addRow(ui_text("bench.threads"), self.spin_threads)
        layout.addLayout(form)
        self.lbl_note = plain_label("")
        self.lbl_note.setObjectName("lbl_bench_note")
        layout.addWidget(self.lbl_note)

        buttons = QHBoxLayout()
        self.btn_run = QPushButton(button_text(ui_text("bench.run")))
        self.btn_run.setObjectName("btn_bench_run")
        self.btn_run.clicked.connect(self.start)
        self.btn_cancel = QPushButton(button_text(ui_text("bench.cancel")))
        self.btn_cancel.setObjectName("btn_bench_cancel")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel)
        buttons.addWidget(self.btn_run)
        buttons.addWidget(self.btn_cancel)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 1)
        layout.addWidget(self.progress)
        self.lbl_status = plain_label("")
        self.lbl_status.setObjectName("lbl_bench_status")
        layout.addWidget(self.lbl_status)

        columns = ("test", "speed", "threads", "backend", "where", "model", "time")
        self.table = QTableWidget(0, len(columns))
        self.table.setObjectName("tbl_bench_results")
        self.table.setHorizontalHeaderLabels([ui_text(f"bench.col.{c}") for c in columns])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 2)
        layout.addWidget(QLabel(ui_text("bench.earlier")))
        earlier = ("model", "where", "test", "speed", "time")
        self.table_earlier = QTableWidget(0, len(earlier))
        self.table_earlier.setObjectName("tbl_bench_earlier")
        self.table_earlier.setHorizontalHeaderLabels([ui_text(f"bench.col.{c}") for c in earlier])
        self.table_earlier.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table_earlier.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table_earlier.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table_earlier, 1)

    # --- data ---

    def _state(self) -> Optional[Path]:
        value = getattr(getattr(self.framework, "config", None), "user_state_dir", None)
        return Path(value) if isinstance(value, (str, Path)) else None

    def reload(self, select: Optional[str] = None) -> None:
        """The GGUF models of the library, the paired boards and the kept runs."""
        from orchestrator.Core.model_library import library
        keep = select or self.cb_model.currentData()
        try:
            rows = [r for r in library(self.framework, with_bench=False) if r.gguf is not None]
        except Exception:  # noqa: BLE001 - nothing readable: an empty choice
            rows = []
        self.rows = {r.key: r for r in rows}
        self.cb_model.blockSignals(True)
        self.cb_model.clear()
        for row in rows:
            text = " · ".join(x for x in (row.name, row.quant, row.kind_text()) if x)
            self.cb_model.addItem(text, row.key)
        self.cb_model.blockSignals(False)
        index = self.cb_model.findData(keep) if keep else -1
        self.cb_model.setCurrentIndex(index if index >= 0 else (0 if rows else -1))
        self._fill_boards()
        self._fill_earlier()
        self._update_note()

    def select_model(self, key: str) -> None:
        index = self.cb_model.findData(key)
        if index >= 0:
            self.cb_model.setCurrentIndex(index)

    def _fill_boards(self) -> None:
        from orchestrator.runtime.device.trusted import DeviceStoreError, TrustedDevices
        state = self._state()
        try:
            devices = [d for d in TrustedDevices(state).list() if d.paired] if state else []
        except (DeviceStoreError, OSError, ValueError):
            devices = []
        self.cb_board.clear()
        for device in devices:
            self.cb_board.addItem(device.label, device.device_id)

    def _fill_earlier(self) -> None:
        from orchestrator.Core.model_library import bench_runs, speed
        runs = bench_runs(self.framework, MAX_EARLIER)
        lines = []
        for run in runs:
            for test, key in (("pp512", "prompt_ts"), ("tg128", "gen_ts")):
                lines.append((str(run.get("model") or "-"), self._where_text(run.get("where")), test,
                              speed(run.get(key)), str(run.get("at") or "")))
        self.table_earlier.setRowCount(len(lines))
        for i, line in enumerate(lines):
            for j, value in enumerate(line):
                self.table_earlier.setItem(i, j, QTableWidgetItem(value))

    @staticmethod
    def _where_text(where: Any) -> str:
        return ui_text(f"bench.where.{where}") if where in ("native", "sandbox") else str(where or "-")

    def _row(self):
        return self.rows.get(self.cb_model.currentData())

    def _update_note(self) -> None:
        board = self.cb_where.currentData() == "board"
        self.cb_board.setEnabled(board)
        self.txt_board_model.setEnabled(board)
        self.spin_threads.setEnabled(not board)
        row = self._row()
        if board:
            if row is not None and row.gguf is not None and not self.txt_board_model.text().strip():
                self.txt_board_model.setText(f"models/{row.gguf.name}")
            self.lbl_note.setText("" if self.cb_board.count() else ui_text("bench.no_board"))
        else:
            self.lbl_note.setText(ui_text("bench.note.native") if row is not None and row.kind == "ditto"
                                  else ui_text("bench.note.sandbox") if row is not None else "")

    # --- running ---

    def busy(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def start(self) -> None:
        if self.busy():
            return
        row = self._row()
        if self.cb_where.currentData() == "board":
            self._start_board(row)
            return
        if row is None or row.gguf is None:
            plain_box(self, "warning", ui_text("bench.title"), ui_text("bench.no_model"))
            return
        threads = self.spin_threads.value() or None
        worker = LocalBenchWorker(self.framework, row.gguf, row.name, threads)
        worker.done.connect(self._on_local_done)
        worker.failed.connect(self._on_failed)
        worker.stopped.connect(lambda: self._finished(ui_text("bench.stopped")))
        self._started(worker, row.name)

    def _start_board(self, row) -> None:
        from orchestrator.gui.deployment_window import BoardBenchWorker
        device_id = self.cb_board.currentData()
        model = self.txt_board_model.text().strip()
        if not device_id or not model:
            plain_box(self, "warning", ui_text("bench.title"), ui_text("device.bench.missing"))
            return
        # asked before anything runs, No preselected: a model that is not a catalog model loads outside a sandbox
        allowed = ask(self, ui_text("device.bench.title"),
                      ui_text("device.bench.untrusted", board=self.cb_board.currentText(), model=model))
        worker = BoardBenchWorker(self.framework, device_id, model, allowed)
        worker.measured.connect(lambda out: self._on_board_measured(out, model))
        worker.finished.connect(self._on_board_done)
        self._started(worker, model)

    def _started(self, worker: QThread, name: str) -> None:
        self.worker = worker
        self.btn_run.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress.setRange(0, 0)
        self.lbl_status.setText(ui_text("bench.running", model=name))
        worker.start()

    def cancel(self) -> None:
        if self.worker is not None and hasattr(self.worker, "cancel"):
            self.worker.cancel()

    def _finished(self, text: str) -> None:
        self.btn_run.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.progress.setRange(0, 1)
        self.lbl_status.setText(text)

    def _add_rows(self, rows: List[Dict[str, Any]], where: str, model: str, when: str) -> None:
        for line in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            for column, value in enumerate((line["test"], line["speed"], line["threads"], line["backend"], where,
                                            model, when)):
                self.table.setItem(r, column, QTableWidgetItem(value))

    def _on_local_done(self, run: Dict[str, Any]) -> None:
        self._add_rows(record_rows(run.get("records") or []), self._where_text(run.get("where")),
                       str(run.get("model") or ""), str(run.get("at") or ""))
        self._finished(ui_text("bench.done", seconds=run.get("duration_s")))
        self._fill_earlier()

    def _on_failed(self, text: str) -> None:
        self._finished(ui_text("bench.failed", reason=text))

    def _on_board_measured(self, out: Dict[str, Any], model: str) -> None:
        from orchestrator.Core.model_library import speed
        board = self.cb_board.currentText()
        lines = [{"test": "pp512", "speed": speed(out.get("prompt_ts")).replace(" t/s", ""), "threads": "-",
                  "backend": "-"},
                 {"test": "tg128", "speed": speed(out.get("gen_ts")).replace(" t/s", ""), "threads": "-",
                  "backend": "-"}]
        self._add_rows(lines, board, model, "")

    def _on_board_done(self, ok: bool, text: str) -> None:
        self._finished(text)
        if not ok:
            plain_box(self, "warning", ui_text("device.bench.title"), text)

    def closeEvent(self, event) -> None:   # noqa: N802 - Qt's name
        """Closing stops a local run (its process or container ends) and keeps the window's results."""
        self.cancel()
        super().closeEvent(event)


__all__ = ["BenchmarkWindow", "LocalBenchWorker", "record_rows"]
