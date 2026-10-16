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
from PySide6.QtCore import Qt, Signal, QThread, QTimer, QRect
from PySide6.QtGui import QPixmap

from orchestrator.Core.module_generator import KNOWLEDGE_SNAPSHOT, ModuleGenerator
from orchestrator.gui.dialogs import AIConfigurationDialog, URLInputDialog, show_crawl_notice_once
from orchestrator.gui.worker_relay import worker_relay
from orchestrator.tools.confirmation import visible
from orchestrator.utils import input_history
from orchestrator.utils.announce import announce
from orchestrator.utils.localization import current_language, tr, ui_text


def module_name_hint(raw: str) -> str:
    """A module name the generator accepts, derived from probe data or AI output (e.g. CPU model strings)."""
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(raw or "").strip())
    name = re.sub(r"_+", "_", name).strip("_.- ")[:64]
    return name or "Custom_Target"

class SummaryPage(QWizardPage):
    """The last page. Finish stays disabled while a Deep Ingest runs: the knowledge snapshot would be partial."""

    def __init__(self, crawl_running: Callable[[], bool]):
        super().__init__()
        self._crawl_running = crawl_running

    def isComplete(self) -> bool:
        return super().isComplete() and not self._crawl_running()


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
    Runs Deep Ingest in the background. Each page is stored as soon as it is read; stop() ends the crawl: a page
    that is still arriving is abandoned, a page already read is stored first, and what is stored stays. Like the rag4ll USB watcher's eject rule, a state is announced
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
        # Why the crawl stopped when the knowledge snapshot taken first did not fit (eighth round, answer 2)
        self.no_room = ""

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
                if result.get("blocker") == "snapshot_no_room":  # no snapshot, no crawl: nothing more is read
                    self.no_room = str(result.get("message") or "")
                    outcomes.append("failure")
                    break
                room = getattr(self.rag_manager, "last_snapshot_room", None)
                if room is not None and getattr(room, "verdict", "") == "tight":
                    from orchestrator.Core.knowledge_snapshots import size_text
                    self.progress.emit("  -> " + ui_text("snapshots.tight", self.lang, size=size_text(room.estimate),
                                                         free=size_text(max(0, room.check.after))))
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
            # Pages of earlier sites are stored: say so instead of "nothing was stored".
            self._announce(self.summary(outcomes + ["failure"], pages, chunks, stopped)[1])
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
        """Ends the crawl: a page still arriving is abandoned (a waiting delay is cut short); stored pages stay."""
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
        self.ai_proposal = None  # Ditto's proposal the fields were filled from: the module is marked untested
        self.crawl_worker = None
        self._close_result = None   # closing waits for a running crawl to finish a write in progress
        self.crawled_roots = set()  # URLs crawled in this wizard: the only pages a knowledge snapshot may hold
        self.probe_path = None      # the target's probe file: the runtime: block is planned from it
        self.probe_id = None        # its copy in the probe store, by which AI clients and Ditto name it

        # Ditto's AI import answers on the application's one worker relay, queued to the GUI thread (a worker never
        # emits on, or frees, a Qt object this wizard owns; gui/worker_relay.py). Only the latest request counts.
        self._ai_request: Optional[object] = None
        worker_relay().done.connect(self._on_ai_relay, Qt.QueuedConnection)
        
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
        self.btn_stop_crawl.setToolTip("End Deep Ingest now: a page that is still arriving is abandoned; pages "
                                       "already stored stay.")
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
        
        # The Builder tab's local probe (in the probe folder), else the file `llm-cli scan` writes by default
        probe_file = Path("target_hardware_config.txt")
        if self.framework_manager is not None:
            from orchestrator.gui.builder_tab import LOCAL_PROBE_FILE, probe_result_dir
            local_probe = probe_result_dir(self.framework_manager) / LOCAL_PROBE_FILE
            if local_probe.exists():
                probe_file = local_probe

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
                    # Probe files of schema 2 write arm64/armv7 (schema 1 wrote uname -m: aarch64/armv7l).
                    arch = {"arm64": "aarch64", "armv7": "armv7l"}.get(data["ARCH"], data["ARCH"])
                    idx = self.arch_combo.findText(arch)
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
        self.custom_os_edit.setPlaceholderText("e.g. ubuntu:24.04, or an image pinned by digest (name@sha256:...)")
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
        page = self.summary_page = SummaryPage(self._crawl_running)
        page.setTitle(tr("wiz.page.summary"))
        page.setSubTitle(tr("wiz.page.summary.sub"))
        layout = QVBoxLayout()
        
        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        self.summary_text.setStyleSheet("font-family: Consolas, Monospace;")
        layout.addWidget(self.summary_text)
        # Both off by default: nothing of the knowledge base goes into the module, nothing is shared.
        self.chk_knowledge = QCheckBox("Attach the knowledge crawled for this target (Deep Ingest, privacy scrub applied)")
        self.chk_knowledge.setToolTip("Only pages crawled in this wizard are attached, never the rest of your "
                                      "knowledge base.")
        layout.addWidget(self.chk_knowledge)
        self.chk_export = QCheckBox("Export for community after creating (a scrubbed, signed pack for a pull request)")
        layout.addWidget(self.chk_export)
        self.lbl_crawl_running = QLabel(ui_text("crawl.finish_waits"))
        self.lbl_crawl_running.setWordWrap(True)
        self.lbl_crawl_running.setVisible(False)
        layout.addWidget(self.lbl_crawl_running)

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
        self.chk_knowledge.setEnabled(bool(self.crawled_roots))
        if not self.crawled_roots:
            self.chk_knowledge.setChecked(False)

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

        # before the first crawl, again when its text changes; audited, since AI clients may crawl only after it
        show_crawl_notice_once(self, cfg, announcer=speak,
                               registry=getattr(self.framework_manager, "tool_registry", None))
        history = input_history.for_config(cfg)
        dlg = URLInputDialog(self, recent=history.get("url") if history else (), config=cfg)
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
            self.crawled_roots.update(urls)
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
            self.crawl_worker.finished.connect(self._crawl_state_changed)
            self.crawl_worker.start()
            self._crawl_state_changed()

    def _crawl_running(self) -> bool:
        return self.crawl_worker is not None and self.crawl_worker.isRunning()

    def _crawl_state_changed(self):
        """Finish waits for a running crawl (SummaryPage.isComplete); the summary page says why."""
        self.lbl_crawl_running.setVisible(self._crawl_running())
        self.summary_page.completeChanged.emit()

    def stop_deep_ingest(self):
        """Stop button: the crawl ends, abandoning a page that is still arriving, and keeps what is stored."""
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
        no_room = getattr(self.crawl_worker, "no_room", "")
        if no_room:  # the snapshot taken first did not fit: the person's list to delete or export snapshots
            from orchestrator.gui.knowledge_snapshots_window import offer_snapshot_list
            self.crawl_log.appendPlainText(no_room)
            offer_snapshot_list(self, self.framework_manager, no_room)
            return
        show = {"success": QMessageBox.information, "partial": QMessageBox.warning}.get(outcome, QMessageBox.critical)
        show(self, "Deep Ingest", msg)  # the message and the kind of box tell the outcome

    def on_crawl_error(self, err):
        self._set_avatar_state("fail")
        self._crawl_controls_idle()
        self.crawl_log.appendPlainText(f"\n❌ Error: {err}")
        QMessageBox.critical(self, "Crawl Error", str(err))

    def done(self, result):
        """Closing the wizard stops a running crawl (a page still arriving is abandoned). It closes once a page
        that is being written is stored (without blocking the GUI), so the knowledge base is never shut down in the
        middle of a write."""
        worker = self.crawl_worker
        if worker is not None and worker.isRunning():
            if self._close_result is None:
                worker.finished.connect(self._close_after_crawl)
            self._close_result = result
            self.stop_deep_ingest()
            return
        super().done(result)

    def _close_after_crawl(self):
        result, self._close_result = self._close_result, None
        if result is not None:
            self.done(result)

    # --- STANDARD IMPORT LOGIC (Rule Based) ---

    def run_standard_import(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("menu.import_profile"), "", "Config (*.txt);;All Files (*)")
        if not path: return
        self.probe_path = path
        self._store_probe(path)

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
            
            # 3. Docker Base Image Logic. CUDA targets get the GPU template, which provisions CUDA from NVIDIA's
            # pinned redist archives; a prebuilt nvidia/cuda image is only an explicit opt-in pinned by digest.
            if sdk == "CUDA":
                self.rad_ubuntu.setChecked(True)
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
            
            self.ai_proposal = None  # the fields now come from the probe, not from Ditto
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

    def _store_probe(self, path):
        """Copies the chosen probe file, validated and without its host name, into the probe store, so AI clients and
        Ditto can name the board's probe by its id (design T5); a file that is no probe the framework reads stays a
        file the AI analysis reads."""
        try:
            from orchestrator.runtime.probe_service import HardwareProbes
            probes = HardwareProbes.for_framework(self.framework_manager)
            self.probe_id = probes.import_file(path)
        except Exception:
            self.probe_id = None
        return self.probe_id

    def _target_profile(self):
        """The imported probe file as a hardware profile of the target (None without one, or when it is not a probe
        profile): the module's runtime: block is the build recipe's plan from it."""
        if not self.probe_path:
            return None
        from orchestrator.runtime.profile import ProfileError, read_profile
        try:
            return read_profile(Path(self.probe_path))
        except (ProfileError, OSError, ValueError):
            return None

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
        self.probe_path = path
        self._store_probe(path)

        self._set_avatar_state("think") # ANIMATED SPRITE
        self.btn_import_ai.setEnabled(False)
        self.btn_import_std.setEnabled(False)
        self.ai_progress.setVisible(True)
        self.status_label.setText(tr("msg.ai_thinking"))
        
        token = self._ai_request = object()
        threading.Thread(target=self._ai_worker, args=(ditto, Path(path), token, worker_relay()), name="ai-import",
                         daemon=True).start()

    @staticmethod
    def _ai_worker(ditto, path, token, relay):
        """Runs in a worker thread: touches no Qt object of the wizard, answers on the relay only."""
        from orchestrator.Core.ditto_manager import DittoError
        try:
            result = ("done", ditto.generate_module(path).model_dump())
        except (DittoError, OSError) as e:
            result = ("error", str(e))
        except Exception as e:
            result = ("error", f"Unexpected error: {e}")
        relay.done.emit(token, result)

    def _on_ai_relay(self, token, result) -> None:
        """GUI thread: the answer of this wizard's latest AI import (other workers' results are not ours)."""
        if token is not self._ai_request or not isinstance(result, tuple) or len(result) != 2:
            return
        self._ai_request = None
        kind, value = result
        if kind == "done":
            self.on_ai_finished(value)
        else:
            self.on_ai_error(value)

    def on_ai_error(self, err):
        self._set_avatar_state("fail")
        self.btn_import_ai.setEnabled(True)
        self.btn_import_std.setEnabled(True)
        self.ai_progress.setVisible(False)
        self.status_label.setText(f"❌ {tr('status.error')}")
        # The error can quote the model (Ditto's stated reason for "cannot"): AI text is never rendered as rich text.
        box = QMessageBox(QMessageBox.Critical, "AI Error", str(err), QMessageBox.Ok, self)
        box.setTextFormat(Qt.PlainText)
        box.exec()

    def on_ai_finished(self, config):
        self._set_avatar_state("success")
        self.btn_import_ai.setEnabled(True)
        self.btn_import_std.setEnabled(True)
        self.ai_progress.setVisible(False)
        self.status_label.setText(tr("msg.ai_complete"))
        
        if not isinstance(config, dict):
            self.on_ai_error("The AI answer is not a JSON object.")
            return
        self.ai_proposal = dict(config)

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
            # Only the exact images of the two buttons select them; any other image (also one with "debian" in its
            # name, a tag or a digest) is kept as it is, so the image shown below is the one that is built.
            base_os = text("base_os")
            if base_os == "debian:bookworm-slim": self.rad_debian.setChecked(True)
            elif base_os == "ubuntu:22.04": self.rad_ubuntu.setChecked(True)
            else:
                self.rad_custom.setChecked(True)
                self.custom_os_edit.setText(base_os)
        if "packages" in config: self.packages_edit.setText(text("packages"))
        if "cpu_flags" in config: self.cpu_flags.setText(text("cpu_flags"))
        if "cmake_flags" in config: self.cmake_flags.setText(text("cmake_flags"))
        # setPlainText: AI output is code, not rich text.
        if "quantization_logic" in config: self.quant_logic.setPlainText(text("quantization_logic"))
        if "setup_commands" in config: self.setup_commands.setPlainText(text("setup_commands"))
        
        from orchestrator.Core.ditto_manager import proposal_facts
        message = ("Values filled from AI analysis.\nReview them on the next pages, especially the shell code.\n\n"
                   + proposal_facts(config))
        if warnings:
            message += "\n\n" + "\n".join(warnings)
        box = QMessageBox(QMessageBox.Information, "Ditto", message, QMessageBox.Ok, self)
        box.setTextFormat(Qt.PlainText)  # AI text is never rendered as rich text
        box.exec()
        self.next()

    def accept(self):
        if self._crawl_running():  # Finish is disabled meanwhile; the snapshot must hold every crawled page
            return
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

        from orchestrator.Core.ditto_manager import ai_module_description
        self.module_data = {
            "module_name": self.name_edit.text(),
            "architecture": arch,
            "sdk": self.sdk_edit.text(),
            # A module from Ditto's proposal says it is untested and names Ditto's guesses, as generate-ai's does.
            "description": (ai_module_description(self.name_edit.text(), self.ai_proposal.get("assumptions") or [])
                            if self.ai_proposal else f"Target for {self.name_edit.text()}"),
            "base_os": self.custom_os_edit.text() if self.rad_custom.isChecked() else ("debian:bookworm-slim" if self.rad_debian.isChecked() else "ubuntu:22.04"),
            "packages": packages_list,
            "cpu_flags": self.cpu_flags.text(),
            "supported_boards": [],
            "setup_commands": self.setup_commands.toPlainText(),
            "cmake_flags": self.cmake_flags.text(),
            "quantization_logic": self.quant_logic.toPlainText(),
            "detection_commands": "lscpu"
        }
        if self.ai_proposal:
            # The proposal's fields without a widget of their own are passed on, never dropped (the person saw them
            # in the proposal's facts), and the module is marked AI-generated and untested.
            from orchestrator.Core.module_generator import AI_DEFINITION_FIELDS
            for key in AI_DEFINITION_FIELDS:
                if key not in self.module_data and self.ai_proposal.get(key):
                    self.module_data[key] = self.ai_proposal[key]
            self.module_data["provenance"] = "ai-generated"

        try:
            if not self.targets_dir.exists(): self.targets_dir.mkdir(parents=True, exist_ok=True)
            generator = ModuleGenerator(self.targets_dir)
            roots = sorted(self.crawled_roots) if self.chk_knowledge.isChecked() else None
            profile = self._target_profile()
            if self.framework_manager:
                output_path = generator.generate_module(self.module_data, self.framework_manager, knowledge_roots=roots,
                                                        profile=profile)
            else:
                output_path = generator.generate_module(self.module_data, profile=profile)
            if self.ai_proposal:  # a module from an AI's proposal, saved by the person: audited (design 6.7)
                from orchestrator.tools.tools_targets import record_module_saved
                record_module_saved(self.framework_manager, "gui", self.module_data, Path(output_path),
                                    "framework-gui")
            message = f"{tr('msg.module_created')}\n{output_path}"
            if roots and not (Path(output_path) / KNOWLEDGE_SNAPSHOT).is_file():
                message += "\n\n" + ui_text("crawl.snapshot_missing")
            QMessageBox.information(self, tr("msg.success"), message)
            if self.chk_export.isChecked():
                from orchestrator.gui.community_hub import CommunityExportDialog
                CommunityExportDialog(self.framework_manager, "target", output_path.name, self).exec()
            super().accept()
        except Exception as e:
            QMessageBox.critical(self, tr("status.error"), f"Failed to generate module:\n{e}")
