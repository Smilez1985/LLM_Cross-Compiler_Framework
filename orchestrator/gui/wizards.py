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
from orchestrator.gui.plain import plain_tooltip
from orchestrator.gui.worker_relay import worker_relay
from orchestrator.tools.confirmation import visible
from orchestrator.utils import input_history
from orchestrator.utils.announce import announce
from orchestrator.utils.localization import current_language, tr, ui_text

# The Docker page's packages before anything is imported (a probe import puts them back after Ditto's proposal).
DEFAULT_PACKAGES = "build-essential cmake git python3-pip"


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
                # the person's crawl (actor "gui"): in offline mode it may read the own network (round 26 Q5)
                result = self.rag_manager.ingest_url(url, self.options, stop=self._stop, progress=self.page.emit,
                                                     actor="gui")
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
        # The digest of a base image an AI filled in through the GUI tools is looked up the same way when the person
        # clicks Finish; the pinned image the person accepted is what the module is written with.
        self._pin_request: Optional[object] = None
        self._pinned_image: Optional[tuple] = None   # (the AI's image, its pinned reference)
        worker_relay().done.connect(self._on_pin_relay, Qt.QueuedConnection)
        
        # Container for Avatar (Stacked to switch widgets)
        self.avatar_container = QStackedWidget()
        self.avatar_container.setFixedSize(120, 120)
        
        # Pages hinzufügen
        self.addPage(self.create_intro_page())
        self.addPage(self.create_hardware_page())
        self.addPage(self.create_docker_page())
        self.addPage(self.create_flags_page())
        self.addPage(self.create_summary_page())
        self.gui_form = self._bind_gui_form()

    def _bind_gui_form(self):
        """The wizard's fields for Ditto's GUI tools, page by page (orchestrator/gui/form_surface.py). Finish is no field:
        only the person's click creates the module. Sharing (export, attached knowledge) stays the person's choice."""
        from orchestrator.gui.form_surface import FormBinding, QWizardPages, person_audit
        b = FormBinding("module_wizard", "gui.form.module_wizard")
        b.line("name", "gui.field.wizard.name", self.name_edit, page=1, hint="letters, digits, '_', '.', '-'")
        b.combo("arch", "gui.field.wizard.arch", self.arch_combo, by_data=False, page=1)
        b.line("sdk", "gui.field.wizard.sdk", self.sdk_edit, page=1, hint="e.g. RKNN, CUDA, OpenVINO, none")
        # Only the person types a description: the framework writes the untested note of a module an AI helps with
        b.line("description", "bld.field.wizard.description", self.desc_edit, page=1,
               hint="the person's own line about the target; empty: 'Target for <name>'",
               read_only=lambda: "only the person writes the description; for a module an AI helps with, the "
               "framework adds that it is AI-generated and untested")

        def base_image():
            if self.rad_debian.isChecked():
                return "debian:bookworm-slim"
            if self.rad_ubuntu.isChecked():
                return "ubuntu:22.04"
            return self.custom_os_edit.text().strip()

        def set_base_image(value):
            text = str(value).strip()
            if text == "debian:bookworm-slim":
                self.rad_debian.setChecked(True)
            elif text == "ubuntu:22.04":
                self.rad_ubuntu.setChecked(True)
            else:
                self.rad_custom.setChecked(True)
                self.custom_os_edit.setText(text)

        image_widgets = (self.rad_debian, self.rad_ubuntu, self.rad_custom, self.custom_os_edit)
        b.add("base_image", "gui.field.wizard.base_image", "text", base_image, set_base_image, image_widgets, page=2,
              hint="a Docker image as image:tag, best an official one pinned by digest (name@sha256:...)")
        for widget in image_widgets[:3]:
            widget.clicked.connect(lambda *_a: self.gui_form.person_edited("base_image"))
        self.custom_os_edit.textEdited.connect(lambda _t: self.gui_form.person_edited("base_image"))
        b.line("packages", "gui.field.wizard.packages", self.packages_edit, page=2, hint="Debian package names")
        b.line("cpu_flags", "gui.field.wizard.cpu_flags", self.cpu_flags, page=3)
        b.line("cmake_flags", "gui.field.wizard.cmake_flags", self.cmake_flags, page=3)
        b.text("quantization_logic", "gui.field.wizard.quantization_logic", self.quant_logic, page=3,
               hint="shell code (bash 'case' branches) for the convert stage (modules/convert_module.sh); the "
                    "person reviews it on the last page")
        b.text("setup_commands", "gui.field.wizard.setup_commands", self.setup_commands, page=3,
               hint="shell commands the source stage runs once before converting (no network, unprivileged); "
                    "what they export reaches the later stages")

        def person_only():
            return "only the person decides what a module shares"
        b.check("attach_knowledge", "gui.field.wizard.attach_knowledge", self.chk_knowledge, page=4,
                read_only=person_only)
        b.check("export", "gui.field.wizard.export", self.chk_export, page=4, read_only=person_only)

        def show():
            self.show()
            self.raise_()
            self.activateWindow()

        form = b.finish(self, show=show, pages=QWizardPages(self), is_open=self.isVisible)
        form.actions["deep_ingest"] = lambda: QTimer.singleShot(0, self.run_deep_ingest)
        b.close_with(self)
        registry = getattr(self.framework_manager, "tool_registry", None)
        for page_id in self.pageIds()[1:]:   # the banner of what Ditto filled, with its Undo, on every page
            layout = self.page(page_id).layout()
            if layout is not None:
                layout.addWidget(b.banner(self.page(page_id), audit=person_audit(registry)))
        b.refresh()
        self.gui_binding = b
        return form

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

        # Provider and model for this one analysis (`llm-cli module generate-ai --provider --model`): Ditto runs on
        # them through DittoManager.for_run, so the saved AI settings stay unchanged.
        hbox_run = QHBoxLayout()
        hbox_run.addWidget(QLabel(ui_text("bld.wizard.run_label")))
        self.combo_run_provider = QComboBox()
        self.combo_run_provider.setObjectName("combo_wizard_run_provider")
        self.combo_run_provider.setToolTip(plain_tooltip(ui_text("bld.wizard.run_tip")))
        self.combo_run_model = QComboBox()
        self.combo_run_model.setObjectName("combo_wizard_run_model")
        self.combo_run_model.setEditable(True)
        self.combo_run_model.setInsertPolicy(QComboBox.NoInsert)
        self.combo_run_model.lineEdit().setPlaceholderText(ui_text("bld.wizard.run_model_hint"))
        self.combo_run_model.setToolTip(plain_tooltip(ui_text("bld.wizard.run_tip")))
        self.combo_run_provider.currentIndexChanged.connect(lambda _i: self._fill_run_models())
        hbox_run.addWidget(self.combo_run_provider, 1)
        hbox_run.addWidget(self.combo_run_model, 1)
        import_layout.addLayout(hbox_run)
        self._fill_run_providers()

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

        # The module's description (`llm-cli module create` asks for it); empty: "Target for <name>"
        self.desc_edit = QLineEdit()
        self.desc_edit.setObjectName("edit_wizard_description")
        layout.addRow(ui_text("bld.wizard.description"), self.desc_edit)
        self.name_edit.textChanged.connect(lambda _text: self._update_description_hint())
        self._update_description_hint()
        
        # Auto-Detect Button
        self.detect_cb = QCheckBox("Auto-fill from Probe Result")
        self.detect_cb.stateChanged.connect(self.load_probe_data)
        layout.addRow("", self.detect_cb)
        self.status_lbl = QLabel("")
        layout.addRow("", self.status_lbl)

        page.setLayout(layout)
        page.registerField("name*", self.name_edit)
        return page

    def _update_description_hint(self):
        """Empty means "Target for <name>" (the CLI's default). A module made from Ditto's proposal gets the
        framework's own description (untested, with Ditto's guesses), so the field is read-only then."""
        if not hasattr(self, "desc_edit"):
            return
        ai = bool(self.ai_proposal)
        self.desc_edit.setReadOnly(ai)
        if ai:
            self.desc_edit.clear()
            self.desc_edit.setPlaceholderText(ui_text("bld.wizard.description_ai"))
        else:
            self.desc_edit.setPlaceholderText(ui_text("bld.wizard.description_hint",
                                                      name=self.name_edit.text().strip() or "..."))

    def description(self) -> str:
        """The description the person typed, or the CLI's default "Target for <name>"."""
        return self.desc_edit.text().strip() or f"Target for {self.name_edit.text()}"

    def person_description(self) -> str:
        """What the person typed into Description ("" for none); a module an AI helped with keeps it in front of the
        framework's untested note."""
        return "" if self.ai_proposal else self.desc_edit.text().strip()

    # --- provider and model for this one analysis ---

    def _fill_run_providers(self):
        """"(AI settings)" first, then every provider of Ditto's catalog (none without Ditto)."""
        current = self.combo_run_provider.currentData()
        self.combo_run_provider.blockSignals(True)
        self.combo_run_provider.clear()
        self.combo_run_provider.addItem(ui_text("bld.wizard.run_saved"), None)
        ditto = self._ditto()
        try:
            providers = ditto.client.catalog.providers if ditto is not None else {}
        except Exception:  # noqa: BLE001 - no catalog: only the AI settings
            providers = {}
        for pid, preset in providers.items():
            self.combo_run_provider.addItem(f"{getattr(preset, 'label', pid)} ({pid})", pid)
        index = self.combo_run_provider.findData(current)
        self.combo_run_provider.setCurrentIndex(index if index >= 0 else 0)
        self.combo_run_provider.blockSignals(False)
        self._run_providers_for = ditto
        self._fill_run_models()

    def _fill_run_models(self):
        """The chosen provider's suggested models; the field stays free text (empty: the AI settings' model)."""
        typed = self.combo_run_model.currentText()
        self.combo_run_model.clear()
        ditto, pid = self._ditto(), self.combo_run_provider.currentData()
        try:
            preset = ditto.client.catalog.get(pid) if ditto is not None and pid else None
        except Exception:  # noqa: BLE001
            preset = None
        for model in getattr(preset, "suggested_models", None) or []:
            self.combo_run_model.addItem(str(model))
        self.combo_run_model.setEditText(typed)

    def run_choice(self):
        """(provider, model) chosen for this analysis; None for each left at the AI settings."""
        return self.combo_run_provider.currentData() or None, self.combo_run_model.currentText().strip() or None

    def showEvent(self, event):
        if getattr(self, "_run_providers_for", None) is not self._ditto():   # the framework came after the pages
            self._fill_run_providers()
        super().showEvent(event)

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
        self.packages_edit.setText(DEFAULT_PACKAGES)
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
        self.quant_logic.setPlaceholderText("Optional: bash 'case' branches for the convert stage "
                                            "(modules/convert_module.sh; review AI output carefully)")
        layout.addRow("Quantization logic (convert stage):", self.quant_logic)

        self.setup_commands = QTextEdit()
        self.setup_commands.setStyleSheet(mono)
        self.setup_commands.setPlaceholderText("Optional: commands the source stage runs once before converting "
                                               "(as the unprivileged build user, without network), e.g. exporting SDK "
                                               "variables; what they export reaches the later stages")
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

        cfg = self.framework_manager.config
        lang = current_language()

        def speak(text: str):
            announce(text, lang=lang, config=cfg)

        # before the first crawl, again when its text changes; audited, since AI clients may crawl only after it. It
        # comes before the knowledge base check: the notice is information, readable also while the base is off
        # (and any time from Tools > Deep Ingest Notice...).
        show_crawl_notice_once(self, cfg, announcer=speak,
                               registry=getattr(self.framework_manager, "tool_registry", None))

        # Offline mode reaches only the person's own network: the person's crawl reads only sites there (round 26
        # Q5; every redirect and link checked again, never a cloud metadata service). Said once before the address
        # question; an AI's crawl_docs_site stays refused in offline mode.
        from orchestrator.utils.locality import offline_mode
        if offline_mode(cfg):
            QMessageBox.information(self, ui_text("net.crawl.title", lang), ui_text("net.crawl.offline", lang))

        rag = self.framework_manager.get_component("rag_manager")
        if not rag:
            QMessageBox.critical(self, "Error", "Local Knowledge Base (RAG) is not active.\nPlease enable it in 'Configure AI Agent' first.")
            return

        # Show Input Dialog
        if not URLInputDialog:
             QMessageBox.critical(self, "Error", "URLInputDialog not available.")
             return

        history = input_history.for_config(cfg)
        links = self.documentation_links()   # filled in for the person to check (N10)
        dlg = URLInputDialog(self, recent=history.get("url") if history else (), config=cfg,
                             **({"suggested": links} if links else {}))
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

    def documentation_links(self) -> List[str]:
        """The source catalogue's documentation links for this hardware (the probe's vendor, the SDK typed on the
        hardware page), offered in the Deep Ingest window; the person checks them and starts the crawl (N10)."""
        from orchestrator.Core.doc_sources import links_for
        try:
            return [link.url for link in links_for(self._target_profile(), self.sdk_edit.text())]
        except Exception:  # noqa: BLE001 - no links: the window opens empty, as before
            return []

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
        middle of a write. A digest lookup still running is dropped: nothing is asked or saved after the window
        closed."""
        self._pin_request = None
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
        self.import_probe_file(path)

    def import_probe_file(self, path):
        """The rule-based import of a probe file (also from the Builder's offer when no target fits a probe)."""
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
            # GGML_AVX512 only with every part of AVX-512 it compiles for (the build recipe's rule: a Xeon Phi has
            # AVX-512 F without BW, VL and DQ, and a probe without CPU flags cannot tell)
            from orchestrator.runtime.build.recipe import avx512_missing
            if config.get("SUPPORTS_AVX512") == "ON" and not avx512_missing(
                    re.split(r"[\s,]+", str(config.get("CPU_FLAGS_ALL") or ""))):
                cmake_flags.append("-DGGML_AVX512=ON")
                cpu_flags.append("-mavx512f")
            if config.get("SUPPORTS_F16C") == "ON":
                cmake_flags.append("-DGGML_F16C=ON")
                cpu_flags.append("-mf16c")

            self.cpu_flags.setText(" ".join(cpu_flags))
            self.cmake_flags.setText(" ".join(cmake_flags))
            if self.ai_proposal:
                # Ditto's values the probe does not set go too: none of them may end up in a module saved as the
                # person's (unmarked, not audited)
                self.packages_edit.setText(DEFAULT_PACKAGES)
                self.setup_commands.setPlainText("")
                self.quant_logic.setPlainText("")
                self.custom_os_edit.clear()

            self.ai_proposal = None  # the fields now come from the probe, not from Ditto
            self._update_description_hint()
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

    def _run_problem(self, text: str):
        box = QMessageBox(QMessageBox.Critical, "AI Error", text, QMessageBox.Ok, self)
        box.setTextFormat(Qt.PlainText)
        box.exec()

    def run_ai_analysis(self):
        ditto = self._ditto()
        if ditto is None:
            QMessageBox.critical(self, tr("status.error"), "The AI assistant (Ditto) is not available.")
            return
        provider, model = self.run_choice()
        if provider or model:
            # This analysis only (`generate-ai --provider --model`): the saved AI settings stay as they are
            from orchestrator.Core.ditto_manager import DittoError
            try:
                ditto = ditto.for_run(provider, model)
            except DittoError as e:
                self._run_problem(ui_text("bld.wizard.run_refused", reason=str(e)))
                return
            if not ditto.is_configured("json"):
                self._run_problem(ui_text("bld.wizard.run_not_ready", provider=self.combo_run_provider.currentText()
                                          if provider else ui_text("bld.wizard.run_saved")))
                return
        elif not ditto.is_configured("json"):
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
        offline = bool(getattr(ditto, "offline_mode", False))
        threading.Thread(target=self._ai_worker, args=(ditto, Path(path), token, worker_relay(), offline),
                         name="ai-import", daemon=True).start()

    @staticmethod
    def _ai_worker(ditto, path, token, relay, offline=False):
        """Runs in a worker thread: touches no Qt object of the wizard, answers on the relay only. The proposal's
        base image is pinned to its digest here, before the person sees it, as `llm-cli module generate-ai` does
        (pin_base_image; the registry is asked off the GUI thread)."""
        from orchestrator.Core.ditto_manager import DittoError, ModuleDefinition, pin_base_image
        from orchestrator.Core.ditto_manager import DittoCannot
        from orchestrator.utils.image_registry import ImageLookupError
        try:
            definition = ditto.generate_module(path)
            if isinstance(definition, ModuleDefinition):
                try:
                    definition = pin_base_image(definition, offline=offline)
                except ImageLookupError as e:
                    relay.done.emit(token, ("error", ui_text("bld.wizard.pin_failed", image=definition.base_os,
                                                             reason=str(e), next=e.next)))
                    return
            result = ("done", definition.model_dump())
        except DittoCannot as e:  # its reason code decides whether Deep Ingest is offered
            result = ("cannot", (str(e), e.cannot.reason))
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
        elif kind == "cannot" and isinstance(value, tuple) and len(value) == 2:
            self.on_ai_error(value[0], reason=value[1])
        else:
            self.on_ai_error(value)

    def on_ai_error(self, err, reason=""):
        self._set_avatar_state("fail")
        self.btn_import_ai.setEnabled(True)
        self.btn_import_std.setEnabled(True)
        self.ai_progress.setVisible(False)
        self.status_label.setText(f"❌ {tr('status.error')}")
        # The error can quote the model (Ditto's stated reason for "cannot"): AI text is never rendered as rich text.
        box = QMessageBox(QMessageBox.Critical, "AI Error", str(err), QMessageBox.Ok, self)
        box.setTextFormat(Qt.PlainText)
        box.exec()
        if reason == "missing_information":
            self.offer_deep_ingest()

    def offer_deep_ingest(self) -> bool:
        """Ditto lacks information about this hardware: Deep Ingest is offered with the framework's documentation links
        for it. Nothing is crawled unless the person checks the links and starts it."""
        from orchestrator.gui.plain import ask
        links = self.documentation_links()
        text = ui_text("gui.wizard.ingest_offer", n=len(links)) if links else ui_text("gui.wizard.ingest_offer_none")
        if not ask(self, ui_text("gui.wizard.ingest_title"), text):
            return False
        self.run_deep_ingest()
        return True

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
        self._update_description_hint()

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

    def _ai_chose_base_image(self) -> bool:
        """Whether the base image field holds an AI's value now (Ditto or an AI app through the GUI tools)."""
        form = getattr(self, "gui_form", None)
        field = form.field("base_image") if form is not None else None
        return bool(field is not None and field.filled_by_ai())

    def _unpinned_ai_image(self) -> Optional[str]:
        """The base image an AI filled in through the GUI tools while it has no digest yet (None otherwise, and once
        the person accepted its pinned form). Ditto's own proposal is pinned before the person sees it."""
        if not self._ai_chose_base_image():
            return None
        image = str(self.gui_form.field("base_image").get() or "").strip()
        if not image or "@sha256:" in image or (self._pinned_image and self._pinned_image[0] == image):
            return None
        return image

    def _offline(self) -> bool:
        ditto = self._ditto()
        if ditto is not None:
            return bool(getattr(ditto, "offline_mode", False))
        try:
            from orchestrator.utils.locality import offline_mode   # the one offline check
            return offline_mode(getattr(self.framework_manager, "config", None))
        except Exception:  # noqa: BLE001 - no settings: the registry is asked
            return False

    def _start_pin(self, image: str) -> None:
        """Looks up the digest the AI's base image points at now, as `llm-cli module generate-ai` pins Ditto's
        proposal (ditto_manager.pin_reference; the registry is asked off the GUI thread); the answer comes back to
        _on_pin_relay, which asks the person before anything is saved."""
        token = self._pin_request = object()
        relay, offline = worker_relay(), self._offline()
        finish = self.button(QWizard.FinishButton)
        if finish is not None:
            finish.setEnabled(False)   # one lookup at a time; Finish comes back with the answer

        def work() -> None:
            from orchestrator.Core.ditto_manager import pin_reference
            try:
                result = ("pinned", image, pin_reference(image, offline=offline), "")
            except Exception as e:  # noqa: BLE001 - ImageLookupError (offline, no registry, unknown tag) and others
                result = ("failed", image, str(e), str(getattr(e, "next", "") or ""))
            relay.done.emit(token, result)

        threading.Thread(target=work, name="ai-image-pin", daemon=True).start()

    def _on_pin_relay(self, token: object, result: Any) -> None:
        if token is not self._pin_request or not isinstance(result, tuple) or len(result) != 4:
            return
        from orchestrator.gui.plain import ask, plain_box
        self._pin_request = None
        finish = self.button(QWizard.FinishButton)
        if finish is not None:
            finish.setEnabled(True)
        kind, image, value, next_step = result
        title = ui_text("bld.wizard.pin_title")
        if kind != "pinned":   # as `module generate-ai` refuses a proposal whose image cannot be pinned
            plain_box(self, "critical", title, ui_text("bld.wizard.pin_not_saved", image=image, reason=value,
                                                       next=next_step))
            return
        if self._unpinned_ai_image() != image:   # the field changed meanwhile: Finish asks again
            return
        if not ask(self, title, ui_text("bld.wizard.pin_question", image=image, pinned=value)):
            return
        self._pinned_image = (image, value)
        self.accept()

    def _save_through_ditto(self, roots, profile) -> Path:
        """An AI-made module takes the path of `llm-cli module generate-ai`: DittoManager.save_module refuses a
        prebuilt nvidia/cuda image and an image that is not a Docker Official Image without the AI's reason, the
        module generator checks every value again, and the save is audited like the CLI's (record_module_saved). The
        values are the ones on the pages (the person may have changed Ditto's); the proposal's fields without a page
        of their own are passed on."""
        from orchestrator.Core.ditto_manager import DittoCoder
        from orchestrator.Core.module_generator import AI_DEFINITION_FIELDS, generator_input
        from orchestrator.Core.module_generator import record_module_saved
        definition = {k: v for k, v in (self.ai_proposal or {}).items() if k in AI_DEFINITION_FIELDS}
        definition.update({k: self.module_data[k] for k in ("architecture", "sdk", "base_os", "packages", "cpu_flags",
                                                            "cmake_flags", "setup_commands", "quantization_logic")})
        name = self.module_data["module_name"]
        ditto = self._ditto() or DittoCoder(self.framework_manager)
        lead = self.person_description()
        path = Path(ditto.save_module(definition, self.targets_dir, module_name=name, profile=profile,
                                      knowledge_roots=roots, generator=ModuleGenerator, description=lead))
        record_module_saved(self.framework_manager, "gui", generator_input(definition, name, description=lead), path,
                            "framework-gui")
        return path

    def _check_created(self, folder) -> str:
        """The check of the new target (TargetManager.validate, as `llm-cli targets validate`); "" without a target
        manager."""
        tm = getattr(self.framework_manager, "target_manager", None) if self.framework_manager else None
        if tm is None or not hasattr(tm, "validate"):
            return ""
        try:
            tm.initialize()   # the new folder is read
            from orchestrator.gui.targets_window import target_check_text
            return target_check_text(tm.validate(Path(folder).name, reread=True))
        except Exception:  # noqa: BLE001 - the module exists; the check is information
            return ""

    def accept(self):
        if self._crawl_running():  # Finish is disabled meanwhile; the snapshot must hold every crawled page
            return
        if self._pin_request is not None:   # the digest of the AI's base image is being looked up
            return
        unpinned = self._unpinned_ai_image()
        if unpinned:   # an AI's base image is written with its digest, as `module generate-ai` writes it
            self._start_pin(unpinned)
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
        # A module Ditto helped with (its proposal, or fields it filled through the GUI tools) is AI-made: marked
        # untested, and its first build is shown to the person once.
        ai_made = bool(self.ai_proposal) or bool(getattr(self, "gui_form", None) and self.gui_form.ai_touched())
        self.module_data = {
            "module_name": self.name_edit.text(),
            "architecture": arch,
            "sdk": self.sdk_edit.text(),
            # A module from Ditto's proposal says it is untested and names Ditto's guesses, as generate-ai's does.
            # A module an AI helped with keeps the person's own description in front of that note.
            "description": (ai_module_description(self.name_edit.text(),
                                                  (self.ai_proposal or {}).get("assumptions") or [],
                                                  lead=self.person_description())
                            if ai_made else self.description()),
            "base_os": self.custom_os_edit.text() if self.rad_custom.isChecked() else ("debian:bookworm-slim" if self.rad_debian.isChecked() else "ubuntu:22.04"),
            "packages": packages_list,
            "cpu_flags": self.cpu_flags.text(),
            "supported_boards": [],
            "setup_commands": self.setup_commands.toPlainText(),
            "cmake_flags": self.cmake_flags.text(),
            "quantization_logic": self.quant_logic.toPlainText(),
            "detection_commands": "lscpu"
        }
        if self._pinned_image and self._ai_chose_base_image() and \
                self.module_data["base_os"].strip() == self._pinned_image[0]:
            self.module_data["base_os"] = self._pinned_image[1]   # the pinned image the person accepted
        if ai_made:
            # The proposal's fields without a widget of their own are passed on, never dropped (the person saw them
            # in the proposal's facts), and the module is marked AI-generated and untested.
            from orchestrator.Core.module_generator import AI_DEFINITION_FIELDS
            for key in AI_DEFINITION_FIELDS:
                if key not in self.module_data and (self.ai_proposal or {}).get(key):
                    self.module_data[key] = self.ai_proposal[key]
            self.module_data["provenance"] = "ai-generated"

        try:
            if not self.targets_dir.exists(): self.targets_dir.mkdir(parents=True, exist_ok=True)
            generator = ModuleGenerator(self.targets_dir)
            roots = sorted(self.crawled_roots) if self.chk_knowledge.isChecked() else None
            profile = self._target_profile()
            if self.ai_proposal or self._ai_chose_base_image():
                # Ditto's proposal (or a base image an AI filled in) is saved as `llm-cli module generate-ai` saves it
                output_path = self._save_through_ditto(roots, profile)
            elif self.framework_manager:
                output_path = generator.generate_module(self.module_data, self.framework_manager, knowledge_roots=roots,
                                                        profile=profile)
            else:
                output_path = generator.generate_module(self.module_data, profile=profile)
            if ai_made and not (self.ai_proposal or self._ai_chose_base_image()):
                # fields an AI filled through the GUI tools, saved by the person: audited (design 6.7)
                from orchestrator.Core.module_generator import record_module_saved
                record_module_saved(self.framework_manager, "gui", self.module_data, Path(output_path),
                                    "framework-gui")
            message = f"{tr('msg.module_created')}\n{output_path}"
            if roots and not (Path(output_path) / KNOWLEDGE_SNAPSHOT).is_file():
                message += "\n\n" + ui_text("crawl.snapshot_missing")
            checked = self._check_created(output_path)   # `llm-cli targets validate` on the new target
            if checked:
                message += "\n\n" + ui_text("bld.check.after_create") + "\n" + checked
            QMessageBox.information(self, tr("msg.success"), message)
            if self.chk_export.isChecked():
                from orchestrator.gui.community_hub import CommunityExportDialog
                CommunityExportDialog(self.framework_manager, "target", output_path.name, self).exec()
            super().accept()
        except Exception as e:
            QMessageBox.critical(self, tr("status.error"), f"Failed to generate module:\n{e}")
