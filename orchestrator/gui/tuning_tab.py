#!/usr/bin/env python3
"""
The Auto-Tuning tab (Core/tuning.py, DESIGN "Targets, hardware and templates"): the same three steps as `llm-cli build
tune start|show|build`, through the same build service.

- Plan: the model, target and board (with more types, the importance-matrix choice and the chunks) become a tuning
  plan (BuildService.plan_tuning, in a worker thread: the model's size may be looked up on the Hub), shown as its lines:
  the candidates with their memory verdict, the reference, the evaluation and the disk.
- Start Tuning: the shown plan queued as one build (BuildService.start_tuning); its progress shows in the Builder's
  build list like any build.
- The finished runs (BuildService.tuning_reports) with their report (tuning.report_lines: per type the size, the memory
  verdict, the KL divergence, the top-1 agreement and the tokens per second measured on the board), and Build the
  Recommended (BuildService.build_tuned): the person decides, nothing is switched by itself.
"""

import threading
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from orchestrator.utils.localization import ui_text


class TuningTab(QWidget):
    def __init__(self, framework, parent=None):
        super().__init__(parent)
        self.framework = framework
        self._plan = None
        self._request_shown = None
        self._token: Optional[object] = None
        self._reports: List[Dict[str, Any]] = []
        self._build_ui()
        from orchestrator.gui.worker_relay import worker_relay
        worker_relay().done.connect(self._on_worker_done, Qt.QueuedConnection)
        self.refresh()

    # --- the service --------------------------------------------------------------------------------------------
    def _service(self):
        service = getattr(self.framework, "build_service", None)
        if service is None:
            from orchestrator.Core.build_service import BuildService
            service = BuildService(self.framework)
        return service

    # --- the form -----------------------------------------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)
        self.lbl_tuning_intro = QLabel(ui_text("tuning.intro"))
        self.lbl_tuning_intro.setWordWrap(True)
        layout.addWidget(self.lbl_tuning_intro)
        form = QFormLayout()
        self.txt_tuning_model = QLineEdit()
        self.txt_tuning_model.setObjectName("txt_tuning_model")
        self.txt_tuning_model.setToolTip(ui_text("tuning.model.tip"))
        form.addRow(ui_text("tuning.model"), self.txt_tuning_model)
        self.cmb_tuning_target = QComboBox()
        self.cmb_tuning_target.setObjectName("cmb_tuning_target")
        self.cmb_tuning_target.currentIndexChanged.connect(self._fill_boards)
        form.addRow(ui_text("tuning.target"), self.cmb_tuning_target)
        self.cmb_tuning_board = QComboBox()
        self.cmb_tuning_board.setObjectName("cmb_tuning_board")
        form.addRow(ui_text("tuning.board"), self.cmb_tuning_board)
        self.txt_tuning_types = QLineEdit()
        self.txt_tuning_types.setObjectName("txt_tuning_types")
        self.txt_tuning_types.setToolTip(ui_text("tuning.types.tip"))
        form.addRow(ui_text("tuning.types"), self.txt_tuning_types)
        self.cmb_tuning_imatrix = QComboBox()
        self.cmb_tuning_imatrix.setObjectName("cmb_tuning_imatrix")
        self.cmb_tuning_imatrix.addItem(ui_text("tuning.imatrix.auto"), "auto")
        self.cmb_tuning_imatrix.addItem(ui_text("tuning.imatrix.both"), "both")
        form.addRow(ui_text("tuning.imatrix"), self.cmb_tuning_imatrix)
        from orchestrator.Core import tuning
        self.spin_tuning_chunks = QSpinBox()
        self.spin_tuning_chunks.setObjectName("spin_tuning_chunks")
        self.spin_tuning_chunks.setRange(tuning.CHUNKS_MIN, tuning.CHUNKS_MAX)
        self.spin_tuning_chunks.setValue(tuning.CHUNKS_DEFAULT)
        self.spin_tuning_chunks.setToolTip(ui_text("tuning.chunks.tip"))
        form.addRow(ui_text("tuning.chunks"), self.spin_tuning_chunks)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        self.btn_tuning_plan = QPushButton(ui_text("tuning.plan"))
        self.btn_tuning_plan.setObjectName("btn_tuning_plan")
        self.btn_tuning_plan.clicked.connect(self.plan)
        self.btn_tuning_start = QPushButton(ui_text("tuning.start"))
        self.btn_tuning_start.setObjectName("btn_tuning_start")
        self.btn_tuning_start.setEnabled(False)
        self.btn_tuning_start.clicked.connect(self.start)
        buttons.addWidget(self.btn_tuning_plan)
        buttons.addWidget(self.btn_tuning_start)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.txt_tuning_plan = QPlainTextEdit()
        self.txt_tuning_plan.setObjectName("txt_tuning_plan")
        self.txt_tuning_plan.setReadOnly(True)
        self.txt_tuning_plan.setMaximumHeight(140)
        layout.addWidget(self.txt_tuning_plan)
        runs = QHBoxLayout()
        runs.addWidget(QLabel(ui_text("tuning.runs")))
        runs.addStretch(1)
        self.btn_tuning_refresh = QPushButton(ui_text("tuning.refresh"))
        self.btn_tuning_refresh.setObjectName("btn_tuning_refresh")
        self.btn_tuning_refresh.clicked.connect(self.refresh)
        self.btn_tuning_build = QPushButton(ui_text("tuning.build"))
        self.btn_tuning_build.setObjectName("btn_tuning_build")
        self.btn_tuning_build.setEnabled(False)
        self.btn_tuning_build.clicked.connect(self.build_recommended)
        runs.addWidget(self.btn_tuning_refresh)
        runs.addWidget(self.btn_tuning_build)
        layout.addLayout(runs)
        self.lst_tuning_runs = QListWidget()
        self.lst_tuning_runs.setObjectName("lst_tuning_runs")
        self.lst_tuning_runs.setMaximumHeight(120)
        self.lst_tuning_runs.currentRowChanged.connect(self._show_report)
        layout.addWidget(self.lst_tuning_runs)
        self.txt_tuning_report = QPlainTextEdit()
        self.txt_tuning_report.setObjectName("txt_tuning_report")
        self.txt_tuning_report.setReadOnly(True)
        layout.addWidget(self.txt_tuning_report, 1)
        self._fill_targets()

    def _fill_targets(self):
        self.cmb_tuning_target.blockSignals(True)
        self.cmb_tuning_target.clear()
        try:
            for target in self._service().list_targets().targets:
                if any(getattr(f, "format", None) == "gguf" for f in target.formats):
                    self.cmb_tuning_target.addItem(target.id, (target.id, list(target.boards)))
        except Exception:  # noqa: BLE001 - a framework without targets: an empty list
            pass
        self.cmb_tuning_target.blockSignals(False)
        self._fill_boards()

    def _fill_boards(self, *_):
        self.cmb_tuning_board.clear()
        data = self.cmb_tuning_target.currentData()
        for board in (data[1] if data else []):
            self.cmb_tuning_board.addItem(board, board)

    def _request(self):
        from orchestrator.Core.build_service import BuildService
        data = self.cmb_tuning_target.currentData()
        return BuildService.request_in(model=self.txt_tuning_model.text().strip(),
                                       target=data[0] if data else self.cmb_tuning_target.currentText(),
                                       format="gguf", board=self.cmb_tuning_board.currentData() or None)

    def _types(self) -> List[str]:
        return [t.strip() for t in self.txt_tuning_types.text().replace(";", ",").split(",") if t.strip()]

    # --- the actions --------------------------------------------------------------------------------------------
    def plan(self):
        if not self.txt_tuning_model.text().strip():
            self.txt_tuning_plan.setPlainText(ui_text("tuning.no_model"))
            return
        self._plan = None
        self.btn_tuning_start.setEnabled(False)
        self.txt_tuning_plan.setPlainText(ui_text("tuning.planning"))
        try:
            request = self._request()
        except Exception as e:  # noqa: BLE001 - a request the schema refuses: said plainly
            self.txt_tuning_plan.setPlainText(ui_text("tuning.failed", reason=str(e)))
            return
        token = self._token = object()
        types, imatrix, chunks = self._types(), self.cmb_tuning_imatrix.currentData(), self.spin_tuning_chunks.value()
        service = self._service()

        def work():
            from orchestrator.gui.worker_relay import worker_relay
            try:
                plan, _req = service.plan_tuning(request, types=types, imatrix=imatrix, chunks=chunks,
                                                 local_paths=True)
                result: Any = ("plan", plan, request)
            except Exception as e:  # noqa: BLE001 - every refusal is shown as it is
                result = ("error", str(e), None)
            worker_relay().done.emit(token, result)
        threading.Thread(target=work, name="tuning-plan", daemon=True).start()

    def _on_worker_done(self, token, result):
        if token is not self._token:
            return
        kind, value, request = result
        if kind == "plan":
            self._plan, self._request_shown = value, request
            self.txt_tuning_plan.setPlainText("\n".join(value.lines()))
            self.btn_tuning_start.setEnabled(True)
        else:
            self.txt_tuning_plan.setPlainText(ui_text("tuning.failed", reason=value))

    def start(self):
        if self._plan is None:
            return
        try:
            handle = self._service().start_tuning(self._request_shown, plan=self._plan, local_paths=True)
        except Exception as e:  # noqa: BLE001 - a refusal (disk, pre-flight) is shown as it is
            self.txt_tuning_plan.appendPlainText(ui_text("tuning.failed", reason=str(e)))
            return
        self.btn_tuning_start.setEnabled(False)
        self.txt_tuning_plan.appendPlainText(ui_text("tuning.started", request=handle.request_id))

    def refresh(self):
        try:
            self._reports = self._service().tuning_reports()
        except Exception:  # noqa: BLE001 - no output folder yet: no runs
            self._reports = []
        self.lst_tuning_runs.clear()
        for item in self._reports:
            report = item["report"]
            choice = (report.get("recommendation") or {}).get("label") or ui_text("tuning.none")
            row = QListWidgetItem(ui_text("tuning.run_row", model=str(report.get("model")),
                                          board=str(report.get("board") or report.get("target")), choice=choice,
                                          created=str(report.get("created") or "")))
            row.setData(Qt.UserRole, item["id"])
            self.lst_tuning_runs.addItem(row)
        if self._reports:
            self.lst_tuning_runs.setCurrentRow(0)
        else:
            self.txt_tuning_report.setPlainText(ui_text("tuning.no_runs"))
            self.btn_tuning_build.setEnabled(False)

    def _show_report(self, row: int):
        from orchestrator.Core.tuning import report_lines
        if not 0 <= row < len(self._reports):
            return
        report = self._reports[row]["report"]
        self.txt_tuning_report.setPlainText("\n".join(report_lines(report)))
        self.btn_tuning_build.setEnabled(bool((report.get("recommendation") or {}).get("label")))

    def build_recommended(self):
        row = self.lst_tuning_runs.currentRow()
        if not 0 <= row < len(self._reports):
            return
        try:
            handle = self._service().build_tuned(self._reports[row]["id"], local_paths=True)
        except Exception as e:  # noqa: BLE001 - shown as it is
            self.txt_tuning_report.appendPlainText(ui_text("tuning.failed", reason=str(e)))
            return
        self.txt_tuning_report.appendPlainText(ui_text("tuning.build_started", request=handle.request_id))
