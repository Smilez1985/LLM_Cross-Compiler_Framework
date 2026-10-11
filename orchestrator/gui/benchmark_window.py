#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Benchmark GUI (v2.3.0)
DIREKTIVE: Goldstandard, GUI, I18n.

Führt Performance-Tests (llama-bench) im Container aus und visualisiert Ergebnisse.

Updates v2.3.0:
- Robust DockerManager access via Framework.
- Safe Config access.
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
    def tr(key, default=None): return default or key

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
            
            # Ensure Docker is ready
            if not self.docker_manager or not self.docker_manager.client:
                 self.error.emit("Docker Manager not initialized.")
                 return

            # Wir nutzen den DockerManager, um einen temporären Container für den Benchmark zu starten
            client = self.docker_manager.client
            
            # Image aus Config holen (v2.3)
            # Hier vereinfacht: Wir nehmen das Standard-Builder-Image oder ein spezialisiertes Benchmark-Image
            img = "ghcr.io/llm-framework/builder:latest" 
            
            # Command bauen
            # llama-bench ist typischerweise im Image vorinstalliert
            model_path = self.config.get("model_path", "/build-cache/models/default.gguf")
            threads = str(self.config.get("threads", 4))
            gpu_layers = str(self.config.get("gpu_layers", 0))
            
            cmd = [
                "/app/bin/llama-bench", 
                "-m", model_path,
                "-t", threads,
                "-ngl", gpu_layers,
                "-p", "512", # Prompt length
                "-n", "128", # Generation length
                "-r", "3"    # Repeats
            ]
            
            cmd_str = " ".join(cmd)
            self.progress.emit(f"Running: {cmd_str}")
            
            # Run container (synchron warten)
            # Wichtig: Volumes müssen korrekt gemountet sein, damit das Modell gefunden wird
            # Wir nutzen hier den DockerManager 'run_container' Wrapper falls verfügbar, sonst direkt client
            
            # Volume Mapping für Cache (wo Modelle liegen)
            # Annahme: Framework Cache ist lokal vorhanden
            cache_host = self.config.get("cache_dir", "./cache")
            
            container = client.containers.run(
                img, 
                command=cmd,
                volumes={
                    str(cache_host): {'bind': '/build-cache', 'mode': 'rw'}
                },
                remove=True,
                detach=False, # Wait for finish
                stdout=True,
                stderr=True
            )
            
            output = container.decode('utf-8')
            results = self._parse_results(output)
            self.finished.emit(results)
            
        except Exception as e:
            self.error.emit(str(e))

    def _parse_results(self, log_text):
        """Extrahiert PP (Prompt Processing) und TG (Text Generation) Speed."""
        result = {"pp": 0.0, "tg": 0.0, "raw": log_text}
        
        # Regex für llama-bench Output (Standard Format)
        # "| model | ... | t/s |"
        # Beispiel: | llama-2-7b | ... | 34.50 |
        
        # Einfache Heuristik für llama.cpp Logs
        pp_match = re.search(r'prompt eval time =.*?\(\s*(\d+\.\d+)\s*ms per token', log_text)
        tg_match = re.search(r'eval time =.*?\(\s*(\d+\.\d+)\s*ms per token', log_text)
        
        if pp_match: 
            ms = float(pp_match.group(1))
            if ms > 0: result["pp"] = 1000.0 / ms
            
        if tg_match: 
            ms = float(tg_match.group(1))
            if ms > 0: result["tg"] = 1000.0 / ms
            
        # Fallback parsing for tabular output
        if result["pp"] == 0 and result["tg"] == 0:
             # Try to find table rows
             # This is tricky without exact format, but we can look for numbers near "t/s"
             pass

        return result

class BenchmarkWindow(QMainWindow):
    def __init__(self, framework_manager, parent=None):
        super().__init__(parent)
        self.framework_manager = framework_manager
        
        # Zugriff auf DockerManager via Framework (v2.3 Standard)
        self.docker_manager = getattr(framework_manager, 'docker_manager', None)
        
        self.setWindowTitle(tr("bench.title", "System Benchmark"))
        self.resize(800, 600)
        
        self._init_ui()

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        
        # Config
        grp_conf = QGroupBox(tr("grp.build_config", "Configuration"))
        form = QFormLayout(grp_conf)
        
        self.spin_threads = QSpinBox()
        self.spin_threads.setRange(1, 32)
        self.spin_threads.setValue(4)
        form.addRow(tr("lbl.threads", "Threads:"), self.spin_threads)
        
        layout.addWidget(grp_conf)
        
        # Actions
        hbox = QHBoxLayout()
        self.btn_run = QPushButton(tr("btn.start", "Start Benchmark"))
        self.btn_run.setStyleSheet("background-color: #2ea043; color: white; font-weight: bold;")
        self.btn_run.clicked.connect(self.start_benchmark)
        hbox.addWidget(self.btn_run)
        
        layout.addLayout(hbox)
        
        # Status
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        layout.addWidget(self.progress_bar)
        
        self.lbl_status = QLabel(tr("status.ready", "Ready."))
        self.lbl_status.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.lbl_status)
        
        # Results Table
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Metric", "Result"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.table)

    def start_benchmark(self):
        if not self.docker_manager:
             QMessageBox.critical(self, "Error", "Docker Manager not available.")
             return

        self.btn_run.setEnabled(False)
        self.progress_bar.setRange(0, 0) # Infinite loading
        self.lbl_status.setText(tr("bench.running", "Running Benchmark..."))
        
        # Get Config safe
        get_cfg = getattr(self.framework_manager.config, 'get', lambda k, d=None: getattr(self.framework_manager.config, k, d))
        cache_dir = get_cfg("cache_dir", "cache")
        
        cfg = {
            "threads": self.spin_threads.value(),
            "model_path": "/build-cache/models/default.gguf", # Placeholder logic
            "use_gpu": False, # Placeholder
            "cache_dir": cache_dir
        }
        
        self.worker = BenchmarkWorker(self.docker_manager, cfg)
        self.worker.progress.connect(self.lbl_status.setText)
        self.worker.finished.connect(self.on_finished)
        self.worker.error.connect(self.on_error)
        self.worker.start()

    def on_finished(self, result):
        self.btn_run.setEnabled(True)
        self.progress_bar.setRange(0, 100); self.progress_bar.setValue(100)
        self.lbl_status.setText(tr("msg.success", "Success"))
        
        self.table.setRowCount(0)
        
        self._add_row("Prompt Processing (PP)", f"{result['pp']:.2f} t/s")
        self._add_row("Token Generation (TG)", f"{result['tg']:.2f} t/s")
        # self._add_row("Raw Output", result['raw'][:100] + "...")

    def on_error(self, err):
        self.btn_run.setEnabled(True)
        self.progress_bar.setRange(0, 100); self.progress_bar.setValue(0)
        self.lbl_status.setText(tr("status.error", "Error"))
        QMessageBox.critical(self, "Error", str(err))

    def _add_row(self, key, value):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(str(key)))
        self.table.setItem(r, 1, QTableWidgetItem(str(value)))
