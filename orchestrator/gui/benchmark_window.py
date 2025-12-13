#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Benchmark GUI
DIREKTIVE: Goldstandard, GUI, I18n.

Führt Performance-Tests (llama-bench) im Container aus und visualisiert Ergebnisse.
"""

import json
import re
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QPushButton, QTableWidget, QTableWidgetItem, 
    QHeaderView, QLabel, QProgressBar, QMessageBox,
    QGroupBox, QFormLayout, QSpinBox
)
from PySide6.QtCore import Qt, QThread, Signal

try:
    from orchestrator.utils.localization import tr
except ImportError:
    def tr(key): return key

class BenchmarkWorker(QThread):
    """Führt den Benchmark im Docker-Container aus."""
    finished = Signal(dict)
    progress = Signal(str)
    error = Signal(str)

    def __init__(self, docker_manager, config):
        super().__init__()
        self.docker_manager = docker_manager
        self.config = config

    def run(self):
        try:
            self.progress.emit("Initializing Benchmark Container...")
            
            # Wir nutzen den DockerManager, um einen temporären Container für den Benchmark zu starten
            # oder executen in einen laufenden.
            # Hier simulieren wir den Aufruf von 'llama-bench' via Docker Client
            
            client = self.docker_manager.builder.docker_client
            img = f"llm-framework/rockchip:latest" # Default, sollte dynamisch sein
            
            # Command bauen
            cmd = [
                "/app/modules/benchmark_module.sh", # Ruft llama-bench auf
                "--models", self.config.get("model_path", "/build-cache/models/default.gguf"),
                "--threads", str(self.config.get("threads", 4)),
                "--gpu", str(self.config.get("use_gpu", 0))
            ]
            
            self.progress.emit(f"Running: {' '.join(cmd)}")
            
            # Run container (synchron warten)
            container = client.containers.run(
                img, 
                command=cmd,
                volumes={'build_cache': {'bind': '/build-cache', 'mode': 'rw'}},
                remove=True,
                detach=False
            )
            
            # Parse Output (Beispielhaftes Parsing von llama-bench Output)
            # "llama_print_timings: prompt eval time = ... / 33.33 t/s"
            output = container.decode('utf-8')
            results = self._parse_results(output)
            
            self.finished.emit(results)
            
        except Exception as e:
            self.error.emit(str(e))

    def _parse_results(self, log_text):
        """Extrahiert PP (Prompt Processing) und TG (Text Generation) Speed."""
        result = {"pp": 0.0, "tg": 0.0, "raw": log_text}
        
        # Regex für llama.cpp Output
        pp_match = re.search(r'prompt eval time =.*?\(\s*(\d+\.\d+)\s*ms per token', log_text)
        tg_match = re.search(r'eval time =.*?\(\s*(\d+\.\d+)\s*ms per token', log_text)
        
        if pp_match: 
            ms = float(pp_match.group(1))
            if ms > 0: result["pp"] = 1000.0 / ms
            
        if tg_match: 
            ms = float(tg_match.group(1))
            if ms > 0: result["tg"] = 1000.0 / ms
            
        return result

class BenchmarkWindow(QMainWindow):
    def __init__(self, framework_manager, parent=None):
        super().__init__(parent)
        self.framework_manager = framework_manager
        # Zugriff auf DockerManager via Parent (MainOrchestrator)
        if parent and hasattr(parent, 'docker_manager'):
            self.docker_manager = parent.docker_manager
        else:
            # Fallback: Neuer Manager (nicht ideal, da keine Verbindung zum Daemon-Status)
            from orchestrator.Core.docker_manager import DockerManager
            self.docker_manager = DockerManager()
            self.docker_manager.initialize(framework_manager)

        self.setWindowTitle(tr("bench.title") if tr("bench.title") != "bench.title" else "System Benchmark")
        self.resize(800, 600)
        
        self._init_ui()

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        
        # Config
        grp_conf = QGroupBox(tr("grp.build_config"))
        form = QFormLayout(grp_conf)
        
        self.spin_threads = QSpinBox()
        self.spin_threads.setRange(1, 32)
        self.spin_threads.setValue(4)
        form.addRow(tr("lbl.threads") if tr("lbl.threads") != "lbl.threads" else "Threads:", self.spin_threads)
        
        layout.addWidget(grp_conf)
        
        # Actions
        hbox = QHBoxLayout()
        self.btn_run = QPushButton(tr("btn.start"))
        self.btn_run.setStyleSheet("background-color: #2ea043; color: white; font-weight: bold;")
        self.btn_run.clicked.connect(self.start_benchmark)
        hbox.addWidget(self.btn_run)
        
        layout.addLayout(hbox)
        
        # Status
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)
        
        self.lbl_status = QLabel(tr("status.ready"))
        self.lbl_status.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.lbl_status)
        
        # Results Table
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Metric", "Result"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.table)

    def start_benchmark(self):
        self.btn_run.setEnabled(False)
        self.progress_bar.setRange(0, 0) # Infinite loading
        self.lbl_status.setText(tr("bench.running") if tr("bench.running") != "bench.running" else "Running Benchmark...")
        
        cfg = {
            "threads": self.spin_threads.value(),
            "model_path": "/build-cache/models/default.gguf", # TODO: Dynamisch wählen
            "use_gpu": False # TODO: Aus Main Window übernehmen
        }
        
        self.worker = BenchmarkWorker(self.docker_manager, cfg)
        self.worker.progress.connect(self.lbl_status.setText)
        self.worker.finished.connect(self.on_finished)
        self.worker.error.connect(self.on_error)
        self.worker.start()

    def on_finished(self, result):
        self.btn_run.setEnabled(True)
        self.progress_bar.setRange(0, 100); self.progress_bar.setValue(100)
        self.lbl_status.setText(tr("msg.success"))
        
        self.table.setRowCount(0)
        
        self._add_row("Prompt Processing (PP)", f"{result['pp']:.2f} t/s")
        self._add_row("Token Generation (TG)", f"{result['tg']:.2f} t/s")
        self._add_row("Raw Output", "See Logs")

    def on_error(self, err):
        self.btn_run.setEnabled(True)
        self.progress_bar.setRange(0, 100); self.progress_bar.setValue(0)
        self.lbl_status.setText(tr("status.error"))
        QMessageBox.critical(self, "Error", str(err))

    def _add_row(self, key, value):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(str(key)))
        self.table.setItem(r, 1, QTableWidgetItem(str(value)))
