#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Wizards (v2.3.0 Enterprise)
DIREKTIVE: Goldstandard, vollständige Implementierung.

Dieser Wizard führt den Benutzer durch die Erstellung eines neuen Hardware-Targets.
Er unterstützt zwei Modi:
1. Standard Import: Deterministisches Parsen von hardware_probe.sh/.ps1 Ausgaben.
2. AI Auto-Discovery: Intelligente Analyse und Optimierungsvorschläge durch Ditto (LLM).

Updates v2.3.0:
- Updated probe result filename to `target_hardware_config.txt`.
- Robust Framework/Config access.
- Improved asset handling for Avatar.
"""

import threading
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtWidgets import (
    QWizard, QWizardPage, QVBoxLayout, QLabel, QFormLayout, 
    QLineEdit, QComboBox, QRadioButton, QButtonGroup, QTextEdit, 
    QMessageBox, QGroupBox, QPushButton, QFileDialog, QProgressBar,
    QDialog, QHBoxLayout, QPlainTextEdit, QStackedWidget, QCheckBox
)
from PySide6.QtCore import Qt, Signal, QObject, QThread, QTimer, QRect
from PySide6.QtGui import QPixmap

from orchestrator.Core.module_generator import ModuleGenerator
from orchestrator.gui.dialogs import AIConfigurationDialog, URLInputDialog, show_crawl_notice_once
from orchestrator.tools.confirmation import visible
from orchestrator.utils import input_history
from orchestrator.utils.announce import announce
from orchestrator.utils.localization import current_language, tr, ui_text


def module_name_hint(raw: str) -> str:
    """A module name the generator accepts, derived from probe data or AI output (e.g. CPU model strings)."""
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(raw or "").strip())
    name = re.sub(r"_+", "_", name).strip("_.- ")[:64]
    return name or "Custom_Target"

class WizardSignals(QObject):
    """Signale für Thread-Kommunikation"""
    analysis_finished = Signal(dict)
    analysis_error = Signal(str)
    # Crawler Signals
    crawl_progress = Signal(str)
    crawl_finished = Signal(str)
    crawl_error = Signal(str)

# --- CUSTOM WIDGET: SPRITE ANIMATOR ---
class SpriteAnimationWidget(QLabel):
    """
    Spielt eine Animation basierend auf einem Sprite-Sheet (Horizontal Strip) ab.
    """
    def __init__(self, image_path: str, frame_count: int, interval: int = 150, parent=None):
        super().__init__(parent)
        self.frame_count = frame_count
        self.interval = interval
        self.current_frame = 0
        
        # Load full sprite sheet
        self.sprite_sheet = QPixmap(image_path)
        if self.sprite_sheet.isNull():
            # Fallback: Create dummy pixmap if image missing
            self.sprite_sheet = QPixmap(100, 100)
            self.sprite_sheet.fill(Qt.red)
            
        # Calculate dimensions
        self.total_width = self.sprite_sheet.width()
        self.frame_width = self.total_width // frame_count if frame_count > 0 else 1
        self.frame_height = self.sprite_sheet.height()
        
        self.setFixedSize(120, 120) # Fixed UI size
        self.setAlignment(Qt.AlignCenter)
        
        # Timer setup
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._update_frame)
        
        # Show first frame immediately
        self._update_frame()

    def start(self):
        self.timer.start(self.interval)

    def stop(self):
        self.timer.stop()

    def _update_frame(self):
        # Extract current frame
        x = self.current_frame * self.frame_width
        frame_pixmap = self.sprite_sheet.copy(QRect(x, 0, self.frame_width, self.frame_height))
        
        # Scale to widget size (keeping aspect ratio)
        scaled = frame_pixmap.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.setPixmap(scaled)
        
        # Next frame loop
        self.current_frame = (self.current_frame + 1) % self.frame_count

class CrawlWorker(QThread):
    """
    Runs Deep Ingest in the background. Each page is stored as soon as it is read; stop() ends the crawl after
    the current page and keeps what is stored. Like the rag4ll USB watcher's eject rule, a state is announced
    only once it is true: the outcome (success, partial or failure) is reported and spoken after the last write
    has returned.
    """
    progress = Signal(str)          # crawl log lines
    page = Signal(int, int, str)    # page n of at most m: url (before the request)
    done = Signal(str, str)         # outcome, message
    error = Signal(str)

    def __init__(self, rag_manager, urls: List[str], options: Dict[str, Any],
                 announcer: Optional[Callable[[str], Any]] = None, lang: Optional[str] = None):
        super().__init__()
        self.rag_manager = rag_manager
        self.urls = urls
        self.options = options
        self.announcer = announcer
        self.lang = lang or current_language()
        self._stop = threading.Event()

    def run(self):
        pages = chunks = 0
        outcomes, stopped = [], False
        try:
            for url in self.urls:
                if self._stop.is_set():
                    stopped = True
                    break
                self.progress.emit(f"Crawling root: {url}...")
                result = self.rag_manager.ingest_url(url, self.options, stop=self._stop, progress=self.page.emit)
                for item in result.get("skipped", []):
                    self.progress.emit(f"  -> Skipped {item['url']}: {item['reason']}")
                for item in result.get("failed", []):
                    self.progress.emit(f"  -> Failed {item['url']}: {item['reason']}")
                outcome = result.get("outcome") or ("success" if result.get("success") else "failure")
                outcomes.append(outcome)
                stopped = stopped or bool(result.get("stopped"))
                if outcome == "failure":
                    self.progress.emit(f"  -> Failed: {result.get('message')}")
                else:
                    pages += result.get("documents", 0)
                    chunks += result.get("count", 0)
                    self.progress.emit(f"  -> {result.get('message')}")
        except Exception as e:
            self._announce(ui_text("crawl.done.failure", self.lang))
            self.error.emit(str(e))
            return
        outcome, message = self.summary(outcomes, pages, chunks, stopped)
        self._announce(message)
        self.done.emit(outcome, message)

    def summary(self, outcomes: List[str], pages: int, chunks: int, stopped: bool):
        """(outcome, message) over all URLs: success only when every URL fully succeeded."""
        if not pages:
            return "failure", ui_text("crawl.done.stopped_empty" if stopped else "crawl.done.failure", self.lang)
        if stopped:
            return "partial", ui_text("crawl.done.stopped", self.lang, pages=pages, chunks=chunks)
        if len(outcomes) == len(self.urls) and all(o == "success" for o in outcomes):
            return "success", ui_text("crawl.done.success", self.lang, pages=pages, chunks=chunks)
        return "partial", ui_text("crawl.done.partial", self.lang, pages=pages, chunks=chunks)

    def _announce(self, text: str) -> None:
        if self.announcer:
            try:
                self.announcer(text)
            except Exception:
                pass  # audio is extra; the outcome is shown in any case

    def stop(self):
        """Ends the crawl after the current page; pages already stored stay."""
        self._stop.set()

class ModuleCreationWizard(QWizard):
    """
    5-Step Module Creation Wizard mit Dual-Mode (Standard & AI).
    """
    
    def __init__(self, targets_dir: Path, parent=None):
        super().__init__(parent)
        self.targets_dir = targets_dir
        self.framework_manager = None
        
        # Access assets & framework via parent app root if available
        self.assets_dir = None
        if parent:
            if hasattr(parent, 'app_root'):
                self.assets_dir = parent.app_root / "assets"
            self.framework_manager = getattr(parent, 'framework_manager', None) or getattr(parent, 'framework', None)
            
        self.setWindowTitle(tr("wiz.title"))
        self.setWizardStyle(QWizard.ModernStyle)
        self.setMinimumSize(900, 700)
        
        self.module_data = {}
        self.ai_config = {} 
        self.crawl_worker = None
        
        self.signals = WizardSignals()
        self.signals.analysis_finished.connect(self.on_ai_finished)
        self.signals.analysis_error.connect(self.on_ai_error)
        
        # Container for Avatar (Stacked to switch widgets)
        self.avatar_container = QStackedWidget()
        self.avatar_container.setFixedSize(120, 120)
        
        # Pages hinzufügen
        self.addPage(self.create_intro_page())
        self.addPage(self.create_hardware_page())
        self.addPage(self.create_docker_page())
        self.addPage(self.create_flags_page())
        self.addPage(self.create_summary_page())

    def create_intro_page(self):
        page = QWizardPage()
        page.setTitle(tr("wiz.intro.title"))
        page.setSubTitle(tr("wiz.intro.text"))
        
        layout = QVBoxLayout()
        
        # --- Import Section ---
        import_group = QGroupBox(tr("wiz.grp.import"))
        import_layout = QVBoxLayout()
        
        # Ditto Avatar & Info
        header_layout = QHBoxLayout()
        
        # Init Avatar (Default State)
        self._set_avatar_state("default")
        header_layout.addWidget(self.avatar_container)
        
        # Info Text
        info_lbl = QLabel(tr("wiz.lbl.ai_info") if tr("wiz.lbl.ai_info") != "wiz.lbl.ai_info" else "Let Ditto analyze your hardware probe.")
        info_lbl.setWordWrap(True)
        header_layout.addWidget(info_lbl)
        header_layout.addStretch()
        
        import_layout.addLayout(header_layout)

        # Buttons Row 1: Imports
        hbox_btns = QHBoxLayout()
        
        # Button 1: Standard Import (Hardcoded Logic)
        self.btn_import_std = QPushButton(tr("wiz.btn.import_std"))
        self.btn_import_std.clicked.connect(self.run_standard_import)
        self.btn_import_std.setMinimumHeight(40)
        hbox_btns.addWidget(self.btn_import_std)
        
        # Button 2: AI Import (Ditto)
        self.btn_import_ai = QPushButton(tr("wiz.btn.import_ai"))
        self.btn_import_ai.clicked.connect(self.run_ai_analysis)
        self.btn_import_ai.setStyleSheet("background-color: #6a0dad; color: white; font-weight: bold;")
        self.btn_import_ai.setMinimumHeight(40)
        hbox_btns.addWidget(self.btn_import_ai)
        
        import_layout.addLayout(hbox_btns)
        
        # Buttons Row 2: Knowledge Ingest (v1.6.0)
        hbox_know = QHBoxLayout()
        self.btn_deep_ingest = QPushButton("🧠 Deep Ingest (Add Docs)")
        self.btn_deep_ingest.setToolTip("Crawl external documentation URLs to teach Ditto about new hardware.")
        self.btn_deep_ingest.clicked.connect(self.run_deep_ingest)
        hbox_know.addWidget(self.btn_deep_ingest)
        self.btn_stop_crawl = QPushButton(ui_text("crawl.btn.stop"))
        self.btn_stop_crawl.setToolTip("End Deep Ingest after the current page; pages already stored stay.")
        self.btn_stop_crawl.setAccessibleName("Stop Deep Ingest")
        self.btn_stop_crawl.clicked.connect(self.stop_deep_ingest)
        self.btn_stop_crawl.setVisible(False)
        hbox_know.addWidget(self.btn_stop_crawl)
        
        hbox_know.addStretch()
        
        # AI Config Button
        self.btn_configure_ai = QPushButton(tr("wiz.btn.config_ai"))
        self.btn_configure_ai.clicked.connect(self.configure_ai)
        hbox_know.addWidget(self.btn_configure_ai)
        
        import_layout.addLayout(hbox_know)

        # Progress & Status Area
        self.status_label = QLabel(tr("wiz.status.waiting"))
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setTextFormat(Qt.PlainText)  # shows crawled URLs
        self.status_label.setWordWrap(True)
        import_layout.addWidget(self.status_label)
        
        self.ai_progress = QProgressBar()
        self.ai_progress.setRange(0, 0) 
        self.ai_progress.setVisible(False)
        import_layout.addWidget(self.ai_progress)
        
        # Crawler Log (New v1.6.0)
        self.crawl_log = QPlainTextEdit()
        self.crawl_log.setReadOnly(True)
        self.crawl_log.setMaximumHeight(100)
        self.crawl_log.setVisible(False)
        self.crawl_log.setStyleSheet("background-color: #222; color: #0f0; font-family: Consolas;")
        import_layout.addWidget(self.crawl_log)

        import_group.setLayout(import_layout)
        layout.addWidget(import_group)
        
        # --- Manual Section ---
        layout.addSpacing(20)
        layout.addWidget(QLabel(tr("wiz.lbl.manual")))
        
        page.setLayout(layout)
        return page

    def _set_avatar_state(self, state: str):
        """
        Switches the Avatar Widget based on state.
        """
        if not self.assets_dir: return
        
        # Cleanup current widget
        while self.avatar_container.count():
            widget = self.avatar_container.widget(0)
            if isinstance(widget, SpriteAnimationWidget):
                widget.stop()
            self.avatar_container.removeWidget(widget)
            widget.deleteLater()

        mapping = {
            "default": ("ditto.png", False, 1),
            "think": ("ditto_think.png", True, 4),
            "read": ("ditto_read.png", True, 6),
            "success": ("ditto_success.png", False, 1),
            "fail": ("ditto_fail.png", False, 1)
        }
        
        config = mapping.get(state, mapping["default"])
        filename, is_sprite, frames = config
        path = self.assets_dir / filename
        
        # Fallback check
        if not path.exists():
            path_jpg = path.with_suffix(".jpg")
            if path_jpg.exists(): path = path_jpg
        
        if not path.exists():
            lbl = QLabel("Ditto")
            lbl.setAlignment(Qt.AlignCenter)
            self.avatar_container.addWidget(lbl)
            return

        if is_sprite:
            widget = SpriteAnimationWidget(str(path), frame_count=frames, interval=200)
            widget.start()
            self.avatar_container.addWidget(widget)
        else:
            lbl = QLabel()
            lbl.setPixmap(QPixmap(str(path)).scaled(100, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            lbl.setAlignment(Qt.AlignCenter)
            self.avatar_container.addWidget(lbl)

    def create_hardware_page(self):
        page = QWizardPage()
        page.setTitle(tr("wiz.page.hardware"))
        page.setSubTitle(tr("wiz.page.hardware.sub"))
        layout = QFormLayout()
        
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. NVIDIA Jetson Orin / Rockchip RK3588")
        layout.addRow(tr("wiz.lbl.name"), self.name_edit)
        
        self.arch_combo = QComboBox()
        self.arch_combo.addItems(["aarch64", "x86_64", "armv7l", "riscv64"])
        layout.addRow(tr("wiz.lbl.arch"), self.arch_combo)
        
        self.sdk_edit = QLineEdit()
        self.sdk_edit.setPlaceholderText("e.g. CUDA, RKNN, OpenVINO")
        layout.addRow(tr("wiz.lbl.sdk"), self.sdk_edit)
        
        # Auto-Detect Button
        self.detect_cb = QCheckBox("Auto-fill from Probe Result")
        self.detect_cb.stateChanged.connect(self.load_probe_data)
        layout.addRow("", self.detect_cb)
        self.status_lbl = QLabel("")
        layout.addRow("", self.status_lbl)

        page.setLayout(layout)
        page.registerField("name*", self.name_edit)
        return page

    def load_probe_data(self):
        if not self.detect_cb.isChecked(): return
        
        # Use standard probe filename
        probe_file = Path("target_hardware_config.txt") 
        
        if probe_file.exists():
            try:
                data = {}
                with open(probe_file, 'r', encoding='utf-8', errors='ignore') as f:
                    for line in f:
                        if "=" in line and not line.startswith("#"):
                            k, v = line.strip().split("=", 1)
                            data[k.strip()] = v.strip()
                
                # Auto-fill
                if "CPU_MODEL" in data:
                    self.name_edit.setText(module_name_hint(data["CPU_MODEL"]))
                if "ARCH" in data:
                    idx = self.arch_combo.findText(data["ARCH"])
                    if idx >= 0: self.arch_combo.setCurrentIndex(idx)
                    
                self.status_lbl.setText("✅ Data loaded from probe.")
            except Exception as e:
                self.status_lbl.setText(f"❌ Error parsing probe: {e}")
        else:
            self.status_lbl.setText("⚠️ No probe file found. Run 'Hardware Probe' script first.")

    def create_docker_page(self):
        page = QWizardPage()
        page.setTitle(tr("wiz.page.docker"))
        page.setSubTitle(tr("wiz.page.docker.sub"))
        layout = QVBoxLayout()
        
        self.os_group = QButtonGroup(page)
        self.rad_debian = QRadioButton(tr("wiz.rad.debian"))
        self.rad_ubuntu = QRadioButton(tr("wiz.rad.ubuntu"))
        self.rad_custom = QRadioButton(tr("wiz.rad.custom"))
        self.rad_debian.setChecked(True)
        
        self.os_group.addButton(self.rad_debian)
        self.os_group.addButton(self.rad_ubuntu)
        self.os_group.addButton(self.rad_custom)
        
        layout.addWidget(QLabel(tr("wiz.lbl.base_os")))
        layout.addWidget(self.rad_debian)
        layout.addWidget(self.rad_ubuntu)
        layout.addWidget(self.rad_custom)
        
        self.custom_os_edit = QLineEdit()
        self.custom_os_edit.setPlaceholderText("e.g. nvidia/cuda:12.2-devel-ubuntu22.04")
        self.custom_os_edit.setEnabled(False)
        self.rad_custom.toggled.connect(lambda: self.custom_os_edit.setEnabled(self.rad_custom.isChecked()))
        layout.addWidget(self.custom_os_edit)
        
        layout.addWidget(QLabel(tr("wiz.lbl.packages")))
        self.packages_edit = QLineEdit()
        self.packages_edit.setText("build-essential cmake git python3-pip")
        layout.addWidget(self.packages_edit)
        
        page.setLayout(layout)
        return page

    def create_flags_page(self):
        page = QWizardPage()
        page.setTitle(tr("wiz.page.flags"))
        page.setSubTitle(tr("wiz.page.flags.sub"))
        layout = QFormLayout()
        
        self.cpu_flags = QLineEdit()
        self.cpu_flags.setPlaceholderText("-mcpu=cortex-a76 -mtune=cortex-a76")
        layout.addRow(tr("wiz.lbl.cpu_flags"), self.cpu_flags)
        
        self.cmake_flags = QLineEdit()
        self.cmake_flags.setPlaceholderText("-DGGML_CUDA=ON")
        layout.addRow(tr("wiz.lbl.cmake_flags"), self.cmake_flags)
        
        # Shell code that ends up in the generated build scripts: always visible and editable for review.
        mono = "font-family: Consolas, Monospace;"
        self.quant_logic = QTextEdit()
        self.quant_logic.setStyleSheet(mono)
        self.quant_logic.setPlaceholderText("Optional: bash 'case' branches for build.sh (review AI output carefully)")
        layout.addRow("Quantization logic (build.sh):", self.quant_logic)

        self.setup_commands = QTextEdit()
        self.setup_commands.setStyleSheet(mono)
        self.setup_commands.setPlaceholderText("Optional: commands build.sh runs before converting "
                                               "(as the unprivileged build user, without network), e.g. exporting SDK variables")
        layout.addRow("Build-time setup:", self.setup_commands)
        
        page.setLayout(layout)
        return page

    def create_summary_page(self):
        page = QWizardPage()
        page.setTitle(tr("wiz.page.summary"))
        page.setSubTitle(tr("wiz.page.summary.sub"))
        layout = QVBoxLayout()
        
        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        self.summary_text.setStyleSheet("font-family: Consolas, Monospace;")
        layout.addWidget(self.summary_text)
        
        page.setLayout(layout)
        return page

    def initializePage(self, page_id):
        if self.nextId() == -1: # Last page
            self.update_summary()

    def update_summary(self):
        if self.rad_debian.isChecked(): base_os = "debian:bookworm-slim"
        elif self.rad_ubuntu.isChecked(): base_os = "ubuntu:22.04"
        else: base_os = self.custom_os_edit.text()
        
        summary = f"""
[CONFIGURATION SUMMARY]
=======================
Module Name:  {self.name_edit.text()}
Architecture: {self.arch_combo.currentText()}
SDK Backend:  {self.sdk_edit.text()}

[DOCKER]
Base Image:   {base_os}
Packages:     {self.packages_edit.text()}
+ Auto-Added: Cross-Compiler for {self.arch_combo.currentText()}

[COMPILER]
CPU Flags:    {self.cpu_flags.text()}
CMake Flags:  {self.cmake_flags.text()}

[LOGIC] (becomes part of the generated scripts - review it)
Quantization logic:
{self.quant_logic.toPlainText() or '(default)'}

Setup commands:
{self.setup_commands.toPlainText() or '(none)'}
"""
        self.summary_text.setText(summary)

    # --- DEEP INGEST LOGIC (v1.6.0) ---
    def run_deep_ingest(self):
        """Starts the Deep Ingest Process."""
        if not self.framework_manager:
            QMessageBox.critical(self, "Error", "Framework Manager not linked.")
            return

        rag = self.framework_manager.get_component("rag_manager")
        if not rag:
            QMessageBox.critical(self, "Error", "Local Knowledge Base (RAG) is not active.\nPlease enable it in 'Configure AI Agent' first.")
            return

        # Show Input Dialog
        if not URLInputDialog:
             QMessageBox.critical(self, "Error", "URLInputDialog not available.")
             return

        cfg = self.framework_manager.config
        lang = current_language()

        def speak(text: str):
            announce(text, lang=lang, config=cfg)

        show_crawl_notice_once(self, cfg, announcer=speak)  # before the first crawl, again when its text changes
        history = input_history.for_config(cfg)
        dlg = URLInputDialog(self, recent=history.get("url") if history else ())
        if dlg.exec() == QDialog.Accepted:
            urls = dlg.get_urls()
            options = dlg.get_options()

            if not urls:
                return
            if history:
                history.add("url", *urls)

            self._set_avatar_state("read")
            self.crawl_log.setVisible(True)
            self.crawl_log.clear()
            self.crawl_log.appendPlainText(f"Initializing Crawler for {len(urls)} URLs...")
            self.btn_deep_ingest.setEnabled(False)
            self.btn_stop_crawl.setEnabled(True)
            self.btn_stop_crawl.setVisible(True)
            self.ai_progress.setRange(0, 1)  # "page N of M" replaces the endless spinner
            self.ai_progress.setValue(0)
            self.ai_progress.setVisible(True)
            self.status_label.setText(ui_text("crawl.progress.start", lang))

            # Start Worker
            self.crawl_worker = CrawlWorker(rag, urls, options, announcer=speak, lang=lang)
            self.crawl_worker.progress.connect(self.on_crawl_progress)
            self.crawl_worker.page.connect(self.on_crawl_page)
            self.crawl_worker.done.connect(self.on_crawl_done)
            self.crawl_worker.error.connect(self.on_crawl_error)
            self.crawl_worker.start()

    def stop_deep_ingest(self):
        """Stop button: the crawl ends after the current page and keeps what is stored."""
        if self.crawl_worker is not None and self.crawl_worker.isRunning():
            self.crawl_worker.stop()
            self.btn_stop_crawl.setEnabled(False)
            self.status_label.setText(ui_text("crawl.stopping"))
            self.on_crawl_progress(ui_text("crawl.stopping"))

    def on_crawl_progress(self, msg):
        self.crawl_log.appendPlainText(msg)
        self.crawl_log.verticalScrollBar().setValue(self.crawl_log.verticalScrollBar().maximum())

    def on_crawl_page(self, number: int, maximum: int, url: str):
        self.ai_progress.setRange(0, max(maximum, 1))
        self.ai_progress.setValue(min(number, maximum))
        shown = visible(url)
        shown = shown if len(shown) <= 120 else shown[:117] + "..."
        self.status_label.setText(ui_text("crawl.progress.page", n=number, m=maximum, url=shown))

    def _crawl_controls_idle(self):
        self.btn_deep_ingest.setEnabled(True)
        self.btn_stop_crawl.setVisible(False)
        self.ai_progress.setVisible(False)

    def on_crawl_done(self, outcome: str, msg: str):
        self._set_avatar_state("fail" if outcome == "failure" else "success")
        self._crawl_controls_idle()
        self.status_label.setText(msg)
        icon = {"success": "✅", "partial": "⚠️"}.get(outcome, "❌")
        self.crawl_log.appendPlainText(f"\n{icon} {msg}")
        show = {"success": QMessageBox.information, "partial": QMessageBox.warning}.get(outcome, QMessageBox.critical)
        show(self, ui_text(f"crawl.title.{outcome}"), msg)

    def on_crawl_error(self, err):
        self._set_avatar_state("fail")
        self._crawl_controls_idle()
        self.crawl_log.appendPlainText(f"\n❌ Error: {err}")
        QMessageBox.critical(self, "Crawl Error", str(err))

    def done(self, result):
        """Closing the wizard stops a running crawl cleanly (stored pages stay)."""
        worker = self.crawl_worker
        if worker is not None and worker.isRunning():
            worker.stop()
            worker.wait(15000)
        super().done(result)

    # --- STANDARD IMPORT LOGIC (Rule Based) ---

    def run_standard_import(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("menu.import_profile"), "", "Config (*.txt);;All Files (*)")
        if not path: return
        
        self.status_label.setText("Parsing probe data...")
        
        try:
            config = {}
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"): continue
                    if "=" in line:
                        key, val = line.split("=", 1)
                        config[key.strip()] = val.strip()
            
            if not config:
                raise ValueError("File format invalid or empty.")

            # 1. Architecture Detection
            arch_map = {
                "aarch64": "aarch64", "arm64": "aarch64", 
                "x86_64": "x86_64", "amd64": "x86_64",
                "armv7l": "armv7l"
            }
            detected_arch = config.get("Architecture", "").lower()
            if not detected_arch:
                 detected_arch = config.get("ARCH", "").lower() # Fallback for new probe script
                 
            if detected_arch in arch_map:
                self.arch_combo.setCurrentText(arch_map[detected_arch])
            
            # 2. Name & SDK Recommendation
            sdk = "None"
            name_hint = "Generic_Target"
            
            if config.get("SUPPORTS_CUDA") == "ON": 
                sdk = "CUDA"
                name_hint = f"NVIDIA_{config.get('GPU_MODEL', 'GPU').replace(' ', '_')}"
            elif config.get("SUPPORTS_RKNN") == "ON": 
                sdk = "RKNN"
                name_hint = f"Rockchip_{config.get('NPU_MODEL', 'NPU')}"
                if config.get("SUPPORTS_RKLLM") == "ON":
                    sdk = "RKLLM / RKNN"
            elif config.get("SUPPORTS_HAILO") == "ON": 
                sdk = "Hailo"
                name_hint = "Hailo_AI_Kit"
            elif config.get("SUPPORTS_INTEL_NPU") == "ON": 
                sdk = "OpenVINO"
                name_hint = "Intel_NPU"

            self.sdk_edit.setText(sdk)
            self.name_edit.setText(module_name_hint(name_hint))
            
            # 3. Docker Base Image Logic
            if sdk == "CUDA":
                self.rad_custom.setChecked(True)
                self.custom_os_edit.setText("nvidia/cuda:12.2.2-devel-ubuntu22.04")
            elif sdk == "OpenVINO":
                self.rad_ubuntu.setChecked(True) 
            else:
                self.rad_debian.setChecked(True) 

            # 4. Flags (CPU & CMake)
            cpu_flags = []
            cmake_flags = []
            
            if config.get("SUPPORTS_NEON") == "ON": cmake_flags.append("-DGGML_NEON=ON")
            if config.get("SUPPORTS_FP16") == "ON":
                cmake_flags.append("-DGGML_FP16=ON")
                if detected_arch in ["aarch64", "arm64"]:
                    cpu_flags.append("-march=armv8.2-a+fp16")
                
            if config.get("SUPPORTS_AVX") == "ON": cmake_flags.append("-DGGML_AVX=ON")
            if config.get("SUPPORTS_AVX2") == "ON": 
                cmake_flags.append("-DGGML_AVX2=ON")
                cpu_flags.append("-mavx2")
            if config.get("SUPPORTS_AVX512") == "ON": 
                cmake_flags.append("-DGGML_AVX512=ON")
                cpu_flags.append("-mavx512f")
            if config.get("SUPPORTS_F16C") == "ON":
                cmake_flags.append("-DGGML_F16C=ON")
                cpu_flags.append("-mf16c")

            self.cpu_flags.setText(" ".join(cpu_flags))
            self.cmake_flags.setText(" ".join(cmake_flags))
            
            self._set_avatar_state("success")
            self.status_label.setText(tr("msg.import_success"))
            QMessageBox.information(self, tr("msg.import_success"), 
                                  f"Hardware profile loaded.\nDetected Arch: {detected_arch}\nSuggested SDK: {sdk}")
            self.next()
            
        except Exception as e:
            self._set_avatar_state("fail")
            self.status_label.setText(tr("status.error"))
            QMessageBox.critical(self, tr("status.error"), f"Failed to parse probe file:\n{str(e)}")

    # --- AI LOGIC (Ditto) ---

    def configure_ai(self):
        if not AIConfigurationDialog:
             QMessageBox.critical(self, "Error", "AIConfigurationDialog not available.")
             return
             
        dialog = AIConfigurationDialog(self.framework_manager, self)
        if dialog.exec() == QDialog.Accepted:
            self.ai_config = dialog.get_config()
            self.btn_import_ai.setEnabled(True)
            self.status_label.setText(f"{tr('msg.ai_ready')}: {self.ai_config.get('provider')}")

    def _ditto(self):
        return getattr(self.framework_manager, "ditto_manager", None) if self.framework_manager else None

    def run_ai_analysis(self):
        ditto = self._ditto()
        if ditto is None:
            QMessageBox.critical(self, tr("status.error"), "The AI assistant (Ditto) is not available.")
            return
        if not ditto.is_configured("json"):
            # Provider, model and key come from the AI settings; ask for them once when they are missing.
            self.configure_ai()
            if not ditto.is_configured("json"):
                return

        path, _ = QFileDialog.getOpenFileName(self, tr("menu.import_profile"), "", "Config (*.txt);;All Files (*)")
        if not path: return
        
        self._set_avatar_state("think") # ANIMATED SPRITE
        self.btn_import_ai.setEnabled(False)
        self.btn_import_std.setEnabled(False)
        self.ai_progress.setVisible(True)
        self.status_label.setText(tr("msg.ai_thinking"))
        
        threading.Thread(target=self._ai_worker, args=(ditto, Path(path)), daemon=True).start()

    def _ai_worker(self, ditto, path):
        from orchestrator.Core.ditto_manager import DittoError
        try:
            definition = ditto.generate_module(path)
            self.signals.analysis_finished.emit(definition.model_dump())
        except (DittoError, OSError) as e:
            self.signals.analysis_error.emit(str(e))
        except Exception as e:
            self.signals.analysis_error.emit(f"Unexpected error: {e}")

    def on_ai_error(self, err):
        self._set_avatar_state("fail")
        self.btn_import_ai.setEnabled(True)
        self.btn_import_std.setEnabled(True)
        self.ai_progress.setVisible(False)
        self.status_label.setText(f"❌ {tr('status.error')}")
        QMessageBox.critical(self, "AI Error", err)

    def on_ai_finished(self, config):
        self._set_avatar_state("success")
        self.btn_import_ai.setEnabled(True)
        self.btn_import_std.setEnabled(True)
        self.ai_progress.setVisible(False)
        self.status_label.setText(tr("msg.ai_complete"))
        
        if not isinstance(config, dict):
            self.on_ai_error("The AI answer is not a JSON object.")
            return

        def text(key):
            value = config.get(key, "")
            if isinstance(value, (list, tuple)):
                return " ".join(str(v) for v in value)
            return "" if value is None else str(value)

        warnings = []
        if "module_name" in config: self.name_edit.setText(module_name_hint(text("module_name")))
        if "architecture" in config:
            arch = text("architecture")
            if self.arch_combo.findText(arch) >= 0:
                self.arch_combo.setCurrentText(arch)
            else:
                warnings.append(f"Unknown architecture '{arch}' - please choose one.")
        if "sdk" in config: self.sdk_edit.setText(text("sdk"))
        if "base_os" in config:
            base_os = text("base_os")
            if "debian" in base_os: self.rad_debian.setChecked(True)
            elif "ubuntu" in base_os: self.rad_ubuntu.setChecked(True)
            else:
                self.rad_custom.setChecked(True)
                self.custom_os_edit.setText(base_os)
        if "packages" in config: self.packages_edit.setText(text("packages"))
        if "cpu_flags" in config: self.cpu_flags.setText(text("cpu_flags"))
        if "cmake_flags" in config: self.cmake_flags.setText(text("cmake_flags"))
        # setPlainText: AI output is code, not rich text.
        if "quantization_logic" in config: self.quant_logic.setPlainText(text("quantization_logic"))
        if "setup_commands" in config: self.setup_commands.setPlainText(text("setup_commands"))
        
        message = "Values filled from AI analysis.\nReview them on the next pages, especially the shell code."
        if warnings:
            message += "\n\n" + "\n".join(warnings)
        QMessageBox.information(self, "Ditto", message)
        self.next()

    def accept(self):
        # --- CROSS-COMPILER AUTO-INJECTION ---
        packages_list = self.packages_edit.text().split()
        arch = self.arch_combo.currentText()
        
        if arch == "aarch64":
            if "gcc-aarch64-linux-gnu" not in packages_list:
                packages_list.append("gcc-aarch64-linux-gnu")
            if "g++-aarch64-linux-gnu" not in packages_list:
                packages_list.append("g++-aarch64-linux-gnu")
                
        elif arch == "armv7l":
             if "gcc-arm-linux-gnueabihf" not in packages_list:
                 packages_list.append("gcc-arm-linux-gnueabihf")
             if "g++-arm-linux-gnueabihf" not in packages_list:
                 packages_list.append("g++-arm-linux-gnueabihf")

        elif arch == "riscv64":
             if "gcc-riscv64-linux-gnu" not in packages_list:
                 packages_list.append("gcc-riscv64-linux-gnu")
             if "g++-riscv64-linux-gnu" not in packages_list:
                 packages_list.append("g++-riscv64-linux-gnu")

        self.module_data = {
            "module_name": self.name_edit.text(),
            "architecture": arch,
            "sdk": self.sdk_edit.text(),
            "description": f"Target for {self.name_edit.text()}",
            "base_os": self.custom_os_edit.text() if self.rad_custom.isChecked() else ("debian:bookworm-slim" if self.rad_debian.isChecked() else "ubuntu:22.04"),
            "packages": packages_list,
            "cpu_flags": self.cpu_flags.text(),
            "supported_boards": [],
            "setup_commands": self.setup_commands.toPlainText(),
            "cmake_flags": self.cmake_flags.text(),
            "quantization_logic": self.quant_logic.toPlainText(),
            "detection_commands": "lscpu"
        }
        
        try:
            if not self.targets_dir.exists(): self.targets_dir.mkdir(parents=True, exist_ok=True)
            generator = ModuleGenerator(self.targets_dir)
            if self.framework_manager:
                output_path = generator.generate_module(self.module_data, self.framework_manager)
            else:
                output_path = generator.generate_module(self.module_data)
                
            QMessageBox.information(self, tr("msg.success"), f"{tr('msg.module_created')}\n{output_path}")
            super().accept()
        except Exception as e:
            QMessageBox.critical(self, tr("status.error"), f"Failed to generate module:\n{e}")
