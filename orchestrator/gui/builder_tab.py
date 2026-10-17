#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Builder Tab (v2.4.0)
DIREKTIVE: Goldstandard GUI Component. IMatrix Enabled.

Der Haupt-Tab für die Konfiguration und den Start von Builds.
Integriert Hardware-Probing, AI-Optimierung und Smart Calibration.

Updates v2.4.0:
- Added Smart Calibration (IMatrix) Controls.
- Updated BuildRequest handling for IMatrix parameters.
"""

import importlib.util
import platform
from pathlib import Path
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QComboBox, QPushButton, QGroupBox, QCheckBox, 
    QMessageBox, QFileDialog, QStackedWidget,
    QPlainTextEdit, QFormLayout, QMenu, QDialog, QLineEdit, QDialogButtonBox
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QRect
from PySide6.QtGui import QPixmap, QAction

from orchestrator.gui.builder_parts import (BoardAdvice, BuildProgressPanel, after_probe, apply_ditto_settings,
                                            bind_builder_form, board_confirmed, person_target, read_probe)
from orchestrator.gui.dialogs import confirm_host_key, fill_recent, select_recent
from orchestrator.gui.explain import term_label
from orchestrator.gui.plain import button_text, plain_label, plain_tooltip
from orchestrator.utils import input_history
from orchestrator.utils.localization import ui_text

# Optional: Paramiko for SSH (used through orchestrator.utils.ssh_trust)
PARAMIKO_AVAILABLE = importlib.util.find_spec("paramiko") is not None

# Core Imports fallback
try:
    from orchestrator.Core.orchestrator import BuildRequest, WorkflowType, PriorityLevel, OptimizationLevel, ModelFormat
except ImportError:
    class BuildRequest: pass
    class WorkflowType: SIMPLE_CONVERSION = "simple"
    class PriorityLevel: NORMAL = "normal"
    class OptimizationLevel: BALANCED = "balanced"
    class ModelFormat: GGUF = "gguf"

# --- DIALOGS ---


# Formats with a working pipeline, and quantization suggestions per format (free text is allowed).
BUILD_FORMATS = [
    ("GGUF (llama.cpp, CPU/GPU)", "gguf"),
    ("RKLLM (Rockchip NPU, LLM)", "rkllm"),
    ("RKNN (Rockchip NPU, ONNX models)", "rknn"),
]


def _unchecked_formats():
    """What `llm-cli build start --format` offers (every ModelFormat but hf), the three with a working pipeline
    first: a target without capabilities is not checked against any of them, by the CLI or here."""
    from orchestrator.Core.build_formats import ModelFormat as Formats
    known = {value for _, value in BUILD_FORMATS}
    return BUILD_FORMATS + [(f.value.upper(), f.value) for f in Formats
                            if f is not Formats.HUGGINGFACE and f.value not in known]


UNCHECKED_FORMATS = _unchecked_formats()
# The build priorities of `llm-cli build start --priority` (PriorityLevel names), normal first as the default.
PRIORITIES = ("NORMAL", "LOW", "HIGH", "URGENT", "CRITICAL")
QUANT_SUGGESTIONS = {
    "gguf": ["Q4_K_M", "Q5_K_M", "Q6_K", "Q8_0", "IQ4_XS", "Q3_K_M", "F16", "BF16"],
    "rkllm": ["w4a16", "w4a16_g128", "w8a8", "w8a8_g256"],
    "rknn": ["fp16", "i8"],
}
# Ungated models that build without a Hugging Face token (offered after the user's recent models)
MODEL_SUGGESTIONS = ["Qwen/Qwen3.5-0.8B", "Qwen/Qwen3.5-2B", "Qwen/Qwen3.5-4B", "HuggingFaceTB/SmolLM2-135M-Instruct"]

class SSHConnectDialog(QDialog):
    """Simple Dialog to get SSH Credentials. recent: devices used before (newest first)."""
    def __init__(self, parent=None, recent=()):
        super().__init__(parent)
        self.setWindowTitle("Remote Hardware Probe (SSH)")
        self.setFixedWidth(300)
        
        layout = QFormLayout(self)
        self.ip_input = QComboBox()
        self.ip_input.setEditable(True)
        self.ip_input.setInsertPolicy(QComboBox.NoInsert)
        fill_recent(self.ip_input, recent)
        self.ip_input.lineEdit().setPlaceholderText("192.168.1.X")
        self.user_input = QLineEdit()
        self.user_input.setPlaceholderText("root / pi / rock")
        self.pass_input = QLineEdit()
        self.pass_input.setEchoMode(QLineEdit.Password)
        
        layout.addRow("Target IP:", self.ip_input)
        layout.addRow("Username:", self.user_input)
        self.pass_input.setPlaceholderText("first connection only - never stored")
        layout.addRow("Password:", self.pass_input)
        self._add_port_and_label(layout)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _add_port_and_label(self, layout):
        """The board's SSH port and the person's name for it (`llmcf scan --device ... --port --label`)."""
        from PySide6.QtWidgets import QSpinBox
        from orchestrator.gui.plain import plain_tooltip
        self.setFixedWidth(360)
        self.port_input = QSpinBox()
        self.port_input.setObjectName("spin_scan_port")
        self.port_input.setRange(1, 65535)
        self.port_input.setValue(22)
        self.port_input.setToolTip(plain_tooltip(ui_text("deploy.port.tip")))
        layout.addRow(ui_text("deploy.port"), self.port_input)
        self.label_input = QLineEdit()
        self.label_input.setObjectName("txt_scan_label")
        self.label_input.setMaxLength(64)
        self.label_input.setPlaceholderText(ui_text("deploy.label.hint"))
        self.label_input.setToolTip(plain_tooltip(ui_text("deploy.label.tip")))
        layout.addRow(ui_text("deploy.label"), self.label_input)

    def get_data(self):
        return self.ip_input.currentText().strip(), self.user_input.text(), self.pass_input.text()

    def get_options(self):
        """(port, the person's name for the board or None)."""
        return int(self.port_input.value()), " ".join(self.label_input.text().split()) or None

# --- WORKERS ---

LOCAL_PROBE_FILE = "target_hardware_config_local.txt"  # the probe of this computer, in probe_result_dir()


def choose_save_file(parent, title: str, start: str, filters: str):
    """The operating system's own save dialog: (path, chosen filter); ("", "") when the person cancels (tests
    replace it)."""
    return QFileDialog.getSaveFileName(parent, title, start, filters)


def probe_result_dir(framework) -> Path:
    """Where the GUI keeps hardware probe results: <cache_dir>/probes, with cache_dir as configured (a relative value
    is relative to the installation, never the working directory)."""
    from orchestrator.Core.framework import resolve_install_path
    get = getattr(getattr(framework, "config", None), "get", None)
    cache = (get("cache_dir", "cache") if callable(get) else None) or "cache"
    resolve = getattr(framework, "resolve_path", None) or resolve_install_path
    return Path(resolve(cache)) / "probes"


class LocalScanWorker(QThread):
    """Runs the hardware probe on this computer the way `llmcf scan` does (orchestrator/runtime/scan_steps.scan_local:
    the Python probe, or the fallback script with `use_script`), stores it as this computer's probe (probe id local,
    the one AI clients and Ditto name) and writes the profile file (`--output`, with `as_json` the JSON view). The tab
    loads a KEY=VALUE copy in probe_result_dir (`gui_copy`) for the target match and the module wizard."""
    finished = Signal(bool, str, str)  # success, message, the file the tab loads

    def __init__(self, framework, output_file, *, use_script=False, as_json=False, gui_copy=None):
        super().__init__()
        self.framework = framework
        self.output_file = Path(output_file)
        self.use_script, self.as_json = bool(use_script), bool(as_json)
        self.gui_copy = Path(gui_copy) if gui_copy else None

    def run(self):
        from orchestrator.runtime.probe_service import HardwareProbes, ProbeError
        from orchestrator.runtime.scan_steps import ProfileWriteError, scan_local, write_profile_file
        try:
            scan = scan_local(HardwareProbes.for_framework(self.framework), self.output_file, as_json=self.as_json,
                              use_script=self.use_script)
            load = self.output_file
            if self.gui_copy is not None and (self.as_json or self.gui_copy != self.output_file):
                write_profile_file(scan.profile, self.gui_copy)
                load = self.gui_copy
        except ProbeError as e:
            self.finished.emit(False, "\n".join(p for p in (ui_text("deploy.scan.failed", reason=str(e)), e.detail,
                                                              e.next) if p), "")
            return
        except ProfileWriteError as e:
            self.finished.emit(False, ui_text("deploy.scan.write_failed", path=str(e.path), reason=str(e)), "")
            return
        except Exception as e:  # noqa: BLE001 - shown to the person
            self.finished.emit(False, ui_text("deploy.scan.failed", reason=str(e)), "")
            return
        how = ui_text("deploy.scan.how.script" if scan.how == "fallback script" else "deploy.scan.how.python")
        lines = [ui_text("deploy.scan.done", how=how, path=str(scan.output_file))]
        lines += [ui_text("deploy.scan.note", note=note) for note in scan.notes]
        lines.append(ui_text("deploy.scan.stored", path=str(scan.local_path)))
        if scan.needs_reprobe:
            lines.append(ui_text("deploy.scan.reprobe"))
        self.finished.emit(True, "\n".join(lines), str(load))


class SSHProbeWorker(QThread):
    """Runs the hardware probe on a trusted device over SSH, the way `llm-cli scan --device` does (the same core step,
    orchestrator/runtime/scan_steps.scan_device): the probe script travels on the command's stdin (the Python probe
    with python3 -I when the device has Python 3.8 or newer, else the shell probe), so nothing is written on the
    device, also where /tmp is mounted noexec. The profile is kept under the device's ID (from its host key) with the
    person's label, written to result_dir for the GUI, the SSH user becomes the board's deploy user and the board's
    trusted, paired record is written (audited as the person's decision in the GUI)."""
    finished = Signal(bool, str, str) # success, message, file_path

    def __init__(self, ip, user, password, state_dir, result_dir, port=22, *, label=None, audit=None):
        super().__init__()
        self.ip = ip
        self.user = user
        self.password = password
        self.port = port
        self.label = label
        self.audit = audit
        self.state_dir = Path(state_dir)
        self.result_dir = Path(result_dir)

    def run(self):
        password, self.password = self.password, None   # used once for this scan, never kept
        if not PARAMIKO_AVAILABLE:
            self.finished.emit(False, "Paramiko library missing. Cannot use SSH.", "")
            return
        from orchestrator.runtime.device import reasons
        from orchestrator.runtime.remote_probe import valid_host
        from orchestrator.runtime.scan_steps import DeviceScanError, scan_device
        if not valid_host(self.ip):
            self.finished.emit(False, "Enter a host name or an IP address (an IPv6 address without brackets, e.g. "
                                      "fe80::1).", "")
            return
        # valid_host() allows no path separators, so the name stays inside result_dir.
        local_output = self.result_dir / f"target_hardware_config_{self.ip.replace(':', '_')}.txt"
        try:
            scan = scan_device(self.state_dir, self.ip, int(self.port), self.user, None, password=password,
                               label=self.label, output_file=local_output, via="gui", actor="gui", audit=self.audit)
        except DeviceScanError as e:
            if e.kind == "login":
                text = (reasons.person_text(e.reason, address=self.ip, port=int(self.port))
                        if e.reason in reasons.REASONS else ui_text("deploy.remote.login_failed", reason=str(e.error)))
            elif e.kind == "probe":
                text = ui_text("deploy.remote.probe_failed", address=self.ip, reason=str(e.error))
            else:
                text = ui_text("deploy.remote.save_failed", reason=str(e.error))
            self.finished.emit(False, text, "")
            return
        except Exception as e:
            self.finished.emit(False, f"SSH Error: {e}", "")
            return
        tool = ui_text(f"deploy.remote.tool.{'python' if scan.tool == 'python' else 'sh'}")
        lines = [ui_text("deploy.remote.done", address=self.ip, tool=tool, device_id=scan.device_id)]
        lines += [ui_text("deploy.scan.note", note=note) for note in scan.notes]
        if scan.trusted is not None:
            lines.append(ui_text("deploy.trusted", device_id=scan.trusted.device_id, label=scan.trusted.label))
        self.finished.emit(True, "\n".join(lines), str(local_output))


class CommitLookupWorker(QThread):
    """Asks Hugging Face for the commit of a model's default branch (BuildService.model_commit: once, without the
    file list, never in offline mode) off the GUI thread, for the identical-build check of the Start button."""
    found = Signal(object)  # the commit, or None when it could not be told

    def __init__(self, service, model):
        super().__init__()
        self.service = service
        self.model = model

    def run(self):
        try:
            commit = self.service.model_commit(self.model)
        except Exception:  # noqa: BLE001 - no answer: the earlier build is shown as "not compared"
            commit = None
        self.found.emit(commit)


class CancelBuildWorker(QThread):
    """Cancels a build through the build service and waits until it has stopped (off the GUI thread)."""
    finished = Signal(str, str)  # outcome ("cancelled", "finished", "running" or "error"), message

    def __init__(self, service, request_id, wait_seconds=120):
        super().__init__()
        self.service = service
        self.request_id = request_id
        self.wait_seconds = wait_seconds

    def run(self):
        try:
            self.service.cancel(self.request_id)
            outcome = self.service.wait_stopped(self.request_id, self.wait_seconds)
            warnings = self.service.get_status(self.request_id).warnings
            self.finished.emit(outcome, " ".join(warnings))
        except Exception as e:  # BuildServiceError (it ended meanwhile) or the service is unavailable
            self.finished.emit("error", str(e))


class AIOptimizerWorker(QThread):
    finished = Signal(bool, dict, str)
    
    def __init__(self, ditto_manager, model_info, target_info, use_rag, options=None, board=None):
        super().__init__()
        self.ditto = ditto_manager
        self.model = model_info
        self.target = target_info
        self.use_rag = use_rag
        self.board = board
        # format -> quantization types the target offers (Ditto may only pick from these)
        self.options = options or dict(QUANT_SUGGESTIONS)
        
    def run(self):
        if not self.ditto:
            self.finished.emit(False, {}, "Ditto Manager not initialized.")
            return
        from orchestrator.Core.ditto_manager import DittoError
        try:
            recommendation = self.ditto.recommend_build_settings(self.model, self.target, self.options,
                                                                 board=self.board, use_knowledge=self.use_rag)
            self.finished.emit(True, recommendation.model_dump(), recommendation.explanation)
        except DittoError as e:
            self.finished.emit(False, {}, str(e))
        except Exception as e:
            self.finished.emit(False, {}, f"AI Error: {e}")

# --- WIDGETS ---

class SpriteAnimationWidget(QLabel):
    def __init__(self, image_path: Path, frame_count: int, interval: int = 150, parent=None):
        super().__init__(parent)
        self.frame_count = frame_count
        self.interval = interval
        self.current_frame = 0
        self.setFixedSize(120, 120)
        self.setAlignment(Qt.AlignCenter)
        if image_path.exists():
            self.sprite_sheet = QPixmap(str(image_path))
        else:
            self.setText("IMG MISSING")
            self.sprite_sheet = QPixmap(1,1) 
        self.total_width = self.sprite_sheet.width()
        self.frame_width = self.total_width // frame_count if frame_count > 0 else 1
        self.frame_height = self.sprite_sheet.height()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._update_frame)
        self._update_frame()

    def start(self):
        if not self.timer.isActive(): self.timer.start(self.interval)

    def stop(self):
        self.timer.stop()

    def _update_frame(self):
        if self.frame_width <= 1: return
        x = self.current_frame * self.frame_width
        frame = self.sprite_sheet.copy(QRect(x, 0, self.frame_width, self.frame_height))
        self.setPixmap(frame.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.current_frame = (self.current_frame + 1) % self.frame_count

class BuilderTab(QWidget):
    create_target_requested = Signal(str)  # no target fits the probe: the wizard with this probe file
    ask_ditto_requested = Signal(str)      # a question for the chat panel, sent only by the person

    def __init__(self, framework_manager, parent=None):
        super().__init__(parent)
        self.framework = framework_manager
        self.history = input_history.for_config(getattr(framework_manager, "config", None))
        
        if hasattr(framework_manager, 'info') and hasattr(framework_manager.info, 'installation_path'):
             self.app_root = Path(framework_manager.info.installation_path)
        else:
             self.app_root = Path(".").resolve()

        self.assets_dir = self.app_root / "assets"
        self._recipe = None  # community recipe the settings were filled from (apply_recipe)
        self._init_ui()
        
    def _init_ui(self):
        main_layout = QHBoxLayout(self)
        
        # --- LEFT COLUMN: CONTROLS ---
        left_col = QVBoxLayout()
        
        # 1. SOURCE
        src_group = QGroupBox("1. Source Model")
        src_layout = QFormLayout()
        
        self.model_input = QComboBox()
        self.model_input.setObjectName("combo_builder_model")
        self.model_input.setEditable(True)
        self.model_input.setInsertPolicy(QComboBox.NoInsert)
        self.model_input.setPlaceholderText("HuggingFace ID or Path")
        self.model_input.lineEdit().setPlaceholderText("HuggingFace ID or Path")
        recent = self._recent("model")
        fill_recent(self.model_input, recent + MODEL_SUGGESTIONS, text=recent[0] if recent else "")
        
        browse_btn = QPushButton("📂")
        browse_btn.setFixedWidth(40)
        browse_btn.clicked.connect(self._browse_model)
        
        row_model = QHBoxLayout()
        row_model.addWidget(self.model_input)
        row_model.addWidget(browse_btn)
        
        src_layout.addRow("Model:", row_model)
        src_group.setLayout(src_layout)
        left_col.addWidget(src_group)
        
        # 2. TARGET
        tgt_group = QGroupBox("2. Target Hardware")
        tgt_layout = QVBoxLayout()
        
        self.target_combo = QComboBox()
        self.target_combo.setObjectName("combo_builder_target")
        self._refresh_targets()
        
        h_layout = QHBoxLayout()
        h_layout.addWidget(QLabel("Profile:"))
        h_layout.addWidget(self.target_combo, 1)
        # Every target with its ID, boards, formats and support level, and the check of the selected one (the CLI's
        # `targets list` and `targets validate`)
        self.btn_targets = QPushButton(button_text(ui_text("bld.builder.targets")))
        self.btn_targets.setObjectName("btn_builder_targets")
        self.btn_targets.setToolTip(plain_tooltip(ui_text("bld.builder.targets.tip")))
        self.btn_targets.clicked.connect(self._open_targets)
        h_layout.addWidget(self.btn_targets)
        self.btn_check_target = QPushButton(button_text(ui_text("bld.builder.check")))
        self.btn_check_target.setObjectName("btn_builder_check_target")
        self.btn_check_target.setToolTip(plain_tooltip(ui_text("bld.builder.check.tip")))
        self.btn_check_target.clicked.connect(self._check_target)
        h_layout.addWidget(self.btn_check_target)
        
        # --- NEW SCAN MENU LOGIC ---
        self.scan_btn = QPushButton("🔍 " + ui_text("main.builder.scan"))
        self.scan_menu = QMenu(self)
        
        # Action 1: Import File (USB/HDD)
        act_import = QAction("📂 " + ui_text("main.builder.scan.import"), self)
        act_import.triggered.connect(self._import_probe_file)
        self.scan_menu.addAction(act_import)
        
        # Action 2: Remote SSH
        act_ssh = QAction("📡 " + ui_text("main.builder.scan.remote"), self)
        act_ssh.triggered.connect(self._open_ssh_dialog)
        self.scan_menu.addAction(act_ssh)
        
        self.scan_menu.addSeparator()
        
        # Action 3: Local Host
        act_local = QAction("💻 " + ui_text("main.builder.scan.local"), self)
        act_local.triggered.connect(self._run_local_probe)
        self.scan_menu.addAction(act_local)
        self._add_scan_entries(act_import, act_ssh, act_local)  # GUI parity: scan --script, --output, port, label
        
        self.scan_btn.setMenu(self.scan_menu)
        h_layout.addWidget(self.scan_btn)
        
        tgt_layout.addLayout(h_layout)
        # A target without capabilities: every format is offered and nothing is checked; said, never silent
        self.lbl_no_caps = plain_label(ui_text("bld.builder.no_caps"), self)
        self.lbl_no_caps.setObjectName("lbl_builder_no_caps")
        self.lbl_no_caps.setVisible(False)
        tgt_layout.addWidget(self.lbl_no_caps)
        # The probe's board as a recommendation (source, how sure), or the offer when no target fits it
        self.board_advice = BoardAdvice(self)
        self.board_advice.create_target.connect(
            lambda: self.create_target_requested.emit(str(getattr(self, "_last_probe_path", "") or "")))
        self.board_advice.ask_ditto.connect(self.ask_ditto_requested.emit)
        tgt_layout.addWidget(self.board_advice)
        tgt_group.setLayout(tgt_layout)
        left_col.addWidget(tgt_group)
        
        # 3. AI OPTIMIZER
        ai_group = QGroupBox("3. AI Optimization (Ditto)")
        ai_layout = QVBoxLayout()
        self.chk_ai = QCheckBox("Enable Ditto AI Optimization")
        self.chk_rag = QCheckBox("Use RAG Knowledge Base")
        self.chk_rag.setEnabled(False)
        self.chk_ai.toggled.connect(self.chk_rag.setEnabled)
        self.btn_optimize = QPushButton("✨ Ask Ditto for Config")
        self.btn_optimize.setEnabled(False)
        self.chk_ai.toggled.connect(self.btn_optimize.setEnabled)
        self.btn_optimize.clicked.connect(self._run_ai_optimizer)
        ai_layout.addWidget(self.chk_ai)
        ai_layout.addWidget(self.chk_rag)
        ai_layout.addWidget(self.btn_optimize)
        ai_group.setLayout(ai_layout)
        left_col.addWidget(ai_group)
        
        # 4. BUILD CONFIG
        build_group = QGroupBox("4. Build Configuration")
        build_layout = QFormLayout()
        self.format_combo = QComboBox()
        self.format_combo.setObjectName("combo_builder_format")
        for label, value in BUILD_FORMATS:
            self.format_combo.addItem(label, value)
        self.quant_combo = QComboBox()
        self.quant_combo.setObjectName("combo_builder_quantization")
        self.quant_combo.setEditable(True)
        # Only a type the user picked or typed survives a board/format change; defaults never count as a choice.
        self._quant_user_set = False
        self.quant_combo.activated.connect(self._mark_quant_user_set)
        self.quant_combo.lineEdit().textEdited.connect(self._mark_quant_user_set)
        # Empty: the board's default type, or for a target without capabilities its scripts' own default.
        self.quant_combo.lineEdit().setPlaceholderText("default")
        # Filled from the selected target's capabilities (_apply_target_capabilities).
        self.board_combo = QComboBox()
        self.board_combo.setObjectName("combo_builder_board")
        self.format_combo.currentIndexChanged.connect(self._update_boards)
        self.board_combo.currentIndexChanged.connect(self._update_quant_options)
        self._update_quant_options()
        self.format_combo.currentIndexChanged.connect(self._update_calibration_options)
        self.opt_combo = QComboBox()
        self.opt_combo.setObjectName("combo_builder_optimization")
        self.opt_combo.addItems(["BALANCED", "SPEED", "SIZE", "AGGRESSIVE", "FAST"])  # every --optimization level
        # The queue orders by it; a failed critical build stops the pipeline (`build start --priority`)
        self.priority_combo = QComboBox()
        self.priority_combo.setObjectName("combo_builder_priority")
        for name in PRIORITIES:
            self.priority_combo.addItem(ui_text(f"bld.builder.priority.{name.lower()}"), name)
        self.priority_combo.setToolTip(plain_tooltip(ui_text("bld.builder.priority.tip")))
        self.chk_gpu = QCheckBox("Use Host GPU for Build Process")
        self.chk_gpu.setObjectName("chk_builder_gpu")
        if platform.system() == "Linux": self.chk_gpu.setChecked(True)
        
        # Calibration: the dataset is used by every format (GGUF imatrix, RKLLM calibration, RKNN INT8);
        # the importance matrix itself exists only for GGUF.
        self.chk_imatrix = QCheckBox("Importance matrix (IMatrix, GGUF)")
        self.chk_imatrix.setObjectName("chk_builder_imatrix")
        self.chk_imatrix.setToolTip("Computes an importance matrix from the calibration dataset to improve "
                                    "low-bit GGUF quantization (required for IQ1/IQ2 types).")
        # Recent datasets are offered, never preselected: a dataset changes the quantization.
        self.dataset_combo = QComboBox()
        self.dataset_combo.setObjectName("combo_builder_dataset")
        self.dataset_combo.setEditable(True)
        self.dataset_combo.setInsertPolicy(QComboBox.NoInsert)
        self.dataset_combo.lineEdit().setPlaceholderText("Calibration dataset (none)")
        self.dataset_combo.setAccessibleName("Calibration dataset")
        fill_recent(self.dataset_combo, self._recent("dataset"), text="")
        self.btn_dataset = QPushButton("...")
        self.btn_dataset.setFixedWidth(40)
        self.btn_dataset.setAccessibleName("Browse for a calibration dataset")
        self.btn_dataset.clicked.connect(self._browse_dataset)
        
        row_dataset = QHBoxLayout()
        row_dataset.addWidget(self.chk_imatrix)
        row_dataset.addWidget(self.dataset_combo, 1)
        row_dataset.addWidget(self.btn_dataset)

        # Technical terms explained on mouse-over (F1 on the field), with a link to the page that explains them
        self.lbl_format = term_label("Format:", "model_format", buddy=self.format_combo)
        self.lbl_format.setObjectName("lbl_builder_format")
        self.lbl_quant = term_label("Quantization:", "quantization", buddy=self.quant_combo)
        self.lbl_quant.setObjectName("lbl_builder_quantization")
        build_layout.addRow(self.lbl_format, self.format_combo)
        build_layout.addRow("Board:", self.board_combo)
        build_layout.addRow(self.lbl_quant, self.quant_combo)
        build_layout.addRow("Optimization:", self.opt_combo)
        build_layout.addRow(ui_text("bld.builder.priority"), self.priority_combo)
        build_layout.addRow("Calibration:", row_dataset)
        self._update_calibration_options()
        # Formats, boards and quantizations follow the selected target's declared capabilities.
        self.target_combo.currentIndexChanged.connect(self._apply_target_capabilities)
        self._apply_target_capabilities()
        build_layout.addRow("", self.chk_gpu)
        
        build_group.setLayout(build_layout)
        left_col.addWidget(build_group)
        self._select_recent_target()
        
        # START BUTTON
        self.btn_start = QPushButton("🚀 Start Build Pipeline")
        self.btn_start.setObjectName("btn_builder_start")
        self.btn_start.setMinimumHeight(50)
        self.btn_start.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold; font-size: 14px;")
        self.btn_start.clicked.connect(self._submit_build)
        left_col.addWidget(self.btn_start)
        # Cancels the build this tab started (through the build service); enabled only while it runs.
        self.btn_cancel = QPushButton("Cancel Build")
        self.btn_cancel.setObjectName("btn_builder_cancel")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setToolTip("Stops the running build and its container; nothing is written to the output "
                                   "folder.")
        self.btn_cancel.clicked.connect(self._cancel_build)
        left_col.addWidget(self.btn_cancel)
        self._active_request = None
        self._save_workers = []  # running saves of finished builds (kept until they end)
        self._build_timer = QTimer(self)
        self._build_timer.setInterval(1500)
        self._build_timer.timeout.connect(self._poll_build)
        left_col.addStretch()
        bind_builder_form(self)  # the build form for Ditto's GUI tools, with the banner of what Ditto filled
        left_col.insertWidget(0, self.fill_banner)
        main_layout.addLayout(left_col, 2)
        
        # RIGHT COLUMN
        right_col = QVBoxLayout()
        self.avatar_stack = QStackedWidget()
        self.avatar_stack.setFixedSize(120, 120)
        self._init_avatars()
        av_container = QWidget()
        av_layout = QHBoxLayout(av_container)
        av_layout.addStretch()
        av_layout.addWidget(self.avatar_stack)
        av_layout.addStretch()
        right_col.addWidget(av_container)
        right_col.addWidget(QLabel("Builder Log:"))
        # Plain text: build output, Ditto's answers and names from community packs never render as HTML.
        self.log_area = QPlainTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setStyleSheet("font-family: Consolas; background: #222; color: #0f0;")
        self.log_area.setContextMenuPolicy(Qt.CustomContextMenu)
        self.log_area.customContextMenuRequested.connect(self._log_context_menu)
        right_col.addWidget(self.log_area, 1)
        # The running build: stage and percent, and its live log with search and a level filter
        right_col.addWidget(plain_label(ui_text("gui.log.title"), self))
        self.progress_panel = BuildProgressPanel(self)
        self.progress_panel.setObjectName("panel_builder_progress")
        right_col.addWidget(self.progress_panel, 2)
        main_layout.addLayout(right_col, 3)

    def _init_avatars(self):
        states = {
            "default": ("Ditto.png", False, 1),
            "think": ("ditto_think.png", True, 4),
            "read": ("ditto_read.png", True, 6),
            "success": ("ditto_success.png", False, 1),
            "fail": ("ditto_fail.png", False, 1)
        }
        self.avatars = {}
        for state, (fname, is_anim, frames) in states.items():
            path = self.assets_dir / fname
            if is_anim:
                wid = SpriteAnimationWidget(path, frames)
            else:
                wid = QLabel()
                if path.exists():
                    wid.setPixmap(QPixmap(str(path)).scaled(100, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                else:
                    wid.setText(state)
                    wid.setAlignment(Qt.AlignCenter)
            self.avatar_stack.addWidget(wid)
            self.avatars[state] = wid
        self.avatar_stack.setCurrentWidget(self.avatars["default"])

    def _set_state(self, state):
        if state in self.avatars:
            curr = self.avatar_stack.currentWidget()
            if isinstance(curr, SpriteAnimationWidget): curr.stop()
            new_wid = self.avatars[state]
            self.avatar_stack.setCurrentWidget(new_wid)
            if isinstance(new_wid, SpriteAnimationWidget): new_wid.start()

    def _log(self, msg):
        self.log_area.appendPlainText(f"> {msg}")

    # --- NEW SCAN LOGIC ---

    def _add_scan_entries(self, act_import, act_ssh, act_local):
        """GUI parity (W1'): names the scan menu's entries for the CLI paths they do (`llmcf scan --import`,
        `scan --device`, `scan`) and adds the fallback script (`scan --script`) and Save the Profile As...
        (`scan --output FILE`, `--json`). The host key of a board is read off the GUI thread (gui/host_key.py)."""
        from orchestrator.gui.host_key import HostKeyCheck
        self.scan_btn.setObjectName("btn_deploy_scan")
        for action, name in ((act_import, "act_deploy_scan_import"), (act_ssh, "act_deploy_scan_remote"),
                             (act_local, "act_deploy_scan_local")):
            action.setObjectName(name)
        self.act_scan_import, self.act_scan_remote, self.act_scan_local = act_import, act_ssh, act_local
        self.act_scan_script = QAction(ui_text("deploy.scan.script"), self)
        self.act_scan_script.setObjectName("act_deploy_scan_script")
        self.act_scan_script.triggered.connect(lambda _=False: self._run_local_scan(use_script=True))
        self.scan_menu.addAction(self.act_scan_script)
        self.act_scan_save = QAction(ui_text("deploy.scan.save"), self)
        self.act_scan_save.setObjectName("act_deploy_scan_save")
        self.act_scan_save.triggered.connect(lambda _=False: self._scan_local_save_as())
        self.scan_menu.addAction(self.act_scan_save)
        # `scan --script --output FILE`: the script writes the chosen file itself (a .json file gets the JSON view)
        self.act_scan_script_save = QAction(ui_text("deploy.scan.script_save"), self)
        self.act_scan_script_save.setObjectName("act_deploy_scan_script_save")
        self.act_scan_script_save.triggered.connect(lambda _=False: self._scan_local_save_as(use_script=True))
        self.scan_menu.addAction(self.act_scan_script_save)
        self.host_keys = HostKeyCheck(self, self.framework)

    def _import_probe_file(self):
        """Option 1: Import existing file (USB/Network), with the person's name for the board (`llmcf scan --import
        FILE --label`, the same core step scan_steps.import_probe)."""
        from PySide6.QtWidgets import QInputDialog
        from orchestrator.gui.plain import plain_box
        from orchestrator.runtime.scan_steps import import_probe, label_problem
        path, _ = QFileDialog.getOpenFileName(self, "Import Hardware Config", "", "Config Files (*.txt);;All Files (*)")
        if not path:
            return
        text, ok = QInputDialog.getText(self, ui_text("deploy.import.label_title"), ui_text("deploy.import.label_text"))
        if not ok:
            self._log(ui_text("deploy.import.cancelled"))
            return
        label = " ".join(str(text or "").split()) or None
        if label_problem(label):
            plain_box(self, "warning", ui_text("deploy.import.label_title"), ui_text("deploy.label.bad"))
            return
        self._log(f"Importing config from {path}...")
        # A copy in the probe store (validated, no host name): AI clients and Ditto name it by its id (design T5).
        try:
            from orchestrator.runtime.probe_service import HardwareProbes
            tm = self.framework.get_component("target_manager") if hasattr(self.framework, "get_component") else None
            imported = import_probe(HardwareProbes.for_framework(self.framework), path, label, tm)
            self._log(ui_text("deploy.import.done", probe_id=imported.probe_id, label=imported.info.label))
            if imported.profile.needs_reprobe:
                self._log(ui_text("deploy.import.reprobe"))
        except Exception as e:  # not a probe file the framework can read: the match below still runs
            self._log(ui_text("deploy.import.failed", reason=str(e)))
            if getattr(e, "next", ""):   # the next step, as `llmcf scan --import` prints it
                self._log(str(e.next))
        self._load_probe_config(Path(path))

    def _open_ssh_dialog(self):
        """Option 2: Remote SSH Scan (`llmcf scan --device HOST --user U --port --label`): the host key is read off
        the GUI thread and trusted only after the person's "Yes"; the probe runs in SSHProbeWorker."""
        from orchestrator.gui.plain import plain_box
        from orchestrator.runtime.remote_probe import valid_host
        from orchestrator.runtime.scan_steps import label_problem
        dlg = SSHConnectDialog(self, recent=self._recent("device"))
        if not dlg.exec():
            return
        ip, user, password = dlg.get_data()
        port, label = dlg.get_options()
        if not ip or not user:
            return
        if not valid_host(ip) or label_problem(label):
            plain_box(self, "warning", ui_text("deploy.remote.title"),
                      ui_text("deploy.bad_address" if not valid_host(ip) else "deploy.label.bad"))
            return
        self.scan_btn.setEnabled(False)
        self._log(ui_text("deploy.reading_key", address=ip, port=port))

        def trusted(ok: bool) -> None:
            if not ok:
                self.scan_btn.setEnabled(True)
                return
            if self.history:
                self.history.add("device", ip)  # a trusted, reachable device
            self._set_state("read")
            self._log(ui_text("deploy.remote.running", user=user, address=ip, port=port))
            config = self.framework.config
            state_dir = getattr(config, "user_state_dir", Path.home() / ".llm-framework")
            result_dir = probe_result_dir(self.framework)
            audit = getattr(getattr(self.framework, "tool_registry", None), "audit", None)
            self.probe_worker = SSHProbeWorker(ip, user, password or None, state_dir, result_dir, port=port,
                                               label=label, audit=audit)
            self.probe_worker.finished.connect(self._on_probe_finished)
            self.probe_worker.start()

        self.host_keys.start(ip, port, trusted, step="probe")

    def _run_local_probe(self):
        """Option 3: Local Scan: the Python probe, as `llmcf scan` runs it by default."""
        self._run_local_scan()

    def _run_local_scan(self, use_script=False, output=None, as_json=False):
        """The scan of this computer (`llmcf scan`, `--script`, `--output FILE`, `--json`) in LocalScanWorker; the
        profile file goes to `output` (default: the GUI's copy in probe_result_dir, which the tab also loads)."""
        self._set_state("read")
        how = ui_text("deploy.scan.how.script" if use_script else "deploy.scan.how.python")
        self._log(ui_text("deploy.scan.running", how=how))
        self.scan_btn.setEnabled(False)
        gui_copy = probe_result_dir(self.framework) / LOCAL_PROBE_FILE
        self.probe_worker = LocalScanWorker(self.framework, Path(output) if output else gui_copy,
                                            use_script=use_script, as_json=as_json, gui_copy=gui_copy)
        self.probe_worker.finished.connect(self._on_probe_finished)
        self.probe_worker.start()

    def _scan_local_save_as(self, use_script=False):
        """Scan Local Host (with the Fallback Script) and Save the Profile As...: the save dialog for the profile file
        (`llmcf scan [--script] --output FILE`; a .json file gets the JSON view, `--json`), then the Python probe or,
        with `use_script`, the fallback script."""
        start = str(Path.home() / "target_hardware_config.txt")
        path, chosen = choose_save_file(self, ui_text("deploy.scan.save_title"), start,
                                        ui_text("deploy.scan.save_filter"))
        if not path:
            return
        target = Path(path)
        if not target.suffix:
            target = target.with_suffix(".json" if "json" in str(chosen).lower() else ".txt")
        self._run_local_scan(use_script=use_script, output=target, as_json=target.suffix.lower() == ".json")

    def _on_probe_finished(self, success, msg, output_path):
        self.scan_btn.setEnabled(True)
        if success:
            self._log(f"Scan finished: {msg}")
            if output_path:
                self._load_probe_config(Path(output_path))
        else:
            self._set_state("fail")
            self._log(f"Probe Error: {msg}")

    def _load_probe_config(self, config_path: Path):
        """Loads and processes the hardware config file."""
        if not config_path.exists():
            self._set_state("fail")
            self._log(f"Error: Config file not found at {config_path}")
            return
            
        tm = self.framework.get_component("target_manager")
        if tm:
            data = tm.import_hardware_profile(config_path)
            match = tm.find_matching_target(data)
            self._last_probe_path = config_path
            profile = read_probe(config_path)
            if not match:  # a schema-2 probe: its SoC, vendor and architecture against the targets
                try:
                    from orchestrator.runtime.probe_service import match_target
                    found = match_target(profile, tm)
                    match = found.target if found else None
                except Exception:
                    match = None
            
            cpu_model = data.get('CPU_MODEL', 'Unknown')
            self._log(f"Detected Hardware: {cpu_model}")
            
            if match:
                idx = self.target_combo.findData(match)
                if idx >= 0 and person_target(self) not in (None, match):  # N9'': after_probe offers it
                    self._log(ui_text("gui.target.kept", target=match, chosen=person_target(self)))
                elif idx >= 0:
                    self.target_combo.setCurrentIndex(idx)
                    self._set_state("success")
                    self._log(f"✅ Auto-Selected Profile: {match}")
                else:
                    self._log(f"⚠️ Matched ID {match} but not in list.")
                after_probe(self, profile, match if idx >= 0 else None)
            else:
                self._set_state("default")
                self._log("No matching profile found in database.")
                after_probe(self, profile, None)

    # --- STANDARD LOGIC ---

    def _capabilities(self):
        tm = self.framework.get_component("target_manager") if self.framework and hasattr(self.framework, "get_component") else None
        target = self.target_combo.currentData() if hasattr(self, "target_combo") else None
        return tm.get_capabilities(target) if tm and target and hasattr(tm, "get_capabilities") else None

    def _declares_capabilities(self, target) -> bool:
        """Whether the target's target.yml has a capabilities block (valid or not), from the loaded targets."""
        from orchestrator.gui.targets_window import declares_capabilities
        return bool(self.framework) and declares_capabilities(self.framework, target)

    def _apply_target_capabilities(self):
        """Offers exactly the formats the selected target declares. A target that declares none gets what the CLI
        allows (every format), and the note that its boards, formats and quantizations are not checked."""
        caps = self._capabilities()
        current = self.format_combo.currentData()
        self.format_combo.blockSignals(True)
        self.format_combo.clear()
        if caps:
            for fid, cap in caps.formats.items():
                self.format_combo.addItem(cap.label, fid)
        else:
            for label, value in UNCHECKED_FORMATS:
                self.format_combo.addItem(label, value)
        if hasattr(self, "lbl_no_caps"):
            target = self.target_combo.currentData()
            if caps is None and target is not None:
                # A block that fails the schema counts as none for the build, but it is not "none declared"
                self.lbl_no_caps.setText(ui_text("bld.builder.invalid_caps" if self._declares_capabilities(target)
                                                 else "bld.builder.no_caps"))
            self.lbl_no_caps.setVisible(caps is None and target is not None)
        idx = self.format_combo.findData(current)
        self.format_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.format_combo.blockSignals(False)
        self._update_boards()
        self._update_calibration_options()

    def _update_boards(self):
        caps = self._capabilities()
        target = self.target_combo.currentData()
        current = self.board_combo.currentData() or self.board_combo.currentText().strip()
        # Free text survives only for the target it was typed for: another target's board id is never
        # carried into a target without capabilities.
        keep_typed = self.board_combo.isEditable() and getattr(self, "_boards_target", None) == target
        self._boards_target = target
        self.board_combo.blockSignals(True)
        self.board_combo.clear()
        if caps:
            self.board_combo.setEditable(False)
            for board in caps.boards_for(self.format_combo.currentData()):
                self.board_combo.addItem(caps.boards[board].label, board)
            idx = self.board_combo.findData(current)
            self.board_combo.setCurrentIndex(idx if idx >= 0 else 0)
        else:
            # A target without capabilities (e.g. from the module wizard): no board, or one the user types.
            self.board_combo.setEditable(True)
            self.board_combo.setInsertPolicy(QComboBox.NoInsert)
            self.board_combo.addItem("(none)", None)
            for board in self._recent("board"):
                self.board_combo.addItem(board, board)
            self.board_combo.setCurrentIndex(0)
            if keep_typed and current and current != "(none)":
                idx = self.board_combo.findText(current)
                if idx > 0:
                    self.board_combo.setCurrentIndex(idx)
                else:
                    self.board_combo.setEditText(current)
        self.board_combo.blockSignals(False)
        self._update_quant_options()

    def _selected_board(self):
        data = self.board_combo.currentData()
        if data:
            return data
        text = self.board_combo.currentText().strip() if self.board_combo.isEditable() else ""
        return text if text and text != "(none)" else None

    def _mark_quant_user_set(self, *_):
        self._quant_user_set = True

    def _update_quant_options(self):
        current = self.quant_combo.currentText().strip()
        caps = self._capabilities()
        cap = caps.formats.get(self.format_combo.currentData()) if caps else None
        board = self._selected_board()
        self.quant_combo.blockSignals(True)
        self.quant_combo.clear()
        if cap:
            self.quant_combo.addItems(cap.quantizations_for(board))
            default = cap.default_for(board)
        else:
            self.quant_combo.addItems(QUANT_SUGGESTIONS.get(self.format_combo.currentData(), []))
            default = None
        # Keep a type the user picked if the new board/format allows it (free text for a target without
        # capabilities); otherwise the new default. Without capabilities that is the scripts' own: empty.
        if self._quant_user_set and current and (not cap or self.quant_combo.findText(current) >= 0):
            self.quant_combo.setCurrentText(current)
        elif default:
            self.quant_combo.setCurrentText(default)
            self._quant_user_set = False
        else:
            self.quant_combo.setCurrentIndex(-1)
            self.quant_combo.setEditText("")
            self._quant_user_set = False
        self.quant_combo.blockSignals(False)
        self._last_default_quant = default

    DATASET_HINTS = {
        "gguf": "Plain text; used to compute the importance matrix.",
        "rkllm": "Plain text (one sample per paragraph) or an RKLLM JSON dataset; improves NPU quantization.",
        "rknn": "Text file listing sample inputs (paths relative to the list file); required for i8.",
    }

    def _update_calibration_options(self):
        if not hasattr(self, "chk_imatrix"):
            return
        fmt = self.format_combo.currentData()
        caps = self._capabilities()
        cap = caps.formats.get(fmt) if caps else None
        uses_imatrix = cap.imatrix if cap else fmt == "gguf"
        self.chk_imatrix.setEnabled(uses_imatrix)
        if not uses_imatrix:
            self.chk_imatrix.setChecked(False)
        uses_dataset = cap is None or cap.dataset != "none" or cap.imatrix
        hint = self.DATASET_HINTS.get(fmt, cap.notes if cap and cap.notes else "")
        for widget in (self.dataset_combo, self.btn_dataset):
            widget.setEnabled(uses_dataset)
            widget.setToolTip(hint)

    @property
    def dataset_path(self):
        """The calibration dataset typed, picked or browsed (None when the field is empty)."""
        return self.dataset_combo.currentText().strip() or None

    @dataset_path.setter
    def dataset_path(self, path):
        self.dataset_combo.setEditText(path or "")

    # --- RECENT INPUTS (per user, see orchestrator/utils/input_history.py) ---

    def _recent(self, field):
        return self.history.get(field) if self.history else []

    def _select_recent_target(self):
        """At startup: the most recently built target and board that are still offered."""
        if select_recent(self.target_combo, self._recent("target")) and not self.board_combo.isEditable():
            select_recent(self.board_combo, self._recent("board"))

    def _remember_build(self, model, target, board, dataset):
        if not self.history:
            return
        for field, value in (("model", model), ("target", target), ("board", board), ("dataset", dataset)):
            self.history.add(field, value)
        self.reload_history()

    def reload_history(self):
        """Refills the recent lists (after a build or after the history was cleared), keeping what is typed."""
        fill_recent(self.model_input, self._recent("model") + MODEL_SUGGESTIONS, text=self.model_input.currentText())
        fill_recent(self.dataset_combo, self._recent("dataset"), text=self.dataset_combo.currentText())
        if self.board_combo.isEditable():
            self._update_boards()

    def _refresh_targets(self):
        previous = self.target_combo.currentData()
        # Repopulate silently and apply the capabilities once: the intermediate empty list would
        # otherwise run the no-capabilities fallback and leave its values behind.
        self.target_combo.blockSignals(True)
        try:
            self.target_combo.clear()
            tm = self.framework.get_component("target_manager") \
                if self.framework and hasattr(self.framework, 'get_component') else None
            if tm:
                for t in tm.list_targets():
                    arch = t.get('architecture') or t.get('architecture_family') or "?"
                    self.target_combo.addItem(f"{t.get('name')} ({arch})", t.get('id'))
            idx = self.target_combo.findData(previous)
            if idx >= 0:
                self.target_combo.setCurrentIndex(idx)
        finally:
            self.target_combo.blockSignals(False)
        if hasattr(self, "format_combo"):
            self._apply_target_capabilities()

    def _log_context_menu(self, pos):
        menu = self.log_area.createStandardContextMenu()
        menu.addSeparator()
        menu.addAction("Export a finished build for the community...", self._export_build_for_community)
        menu.exec(self.log_area.mapToGlobal(pos))

    def _export_build_for_community(self):
        """A finished build becomes a recipe pack (scrubbed, signed) for a pull request."""
        from orchestrator.gui.community_hub import CommunityExportDialog
        get_cfg = getattr(self.framework.config, 'get', lambda k, d=None: d)
        start = get_cfg("output_dir", "output")
        if hasattr(self.framework, "resolve_path"):
            start = self.framework.resolve_path(start)
        path, _ = QFileDialog.getOpenFileName(self, "Choose the build_info.json of a finished build", str(start),
                                              "Build manifest (build_info.json)")
        if path:
            CommunityExportDialog(self.framework, "recipe", path, self).exec()

    def apply_recipe(self, recipe):
        """Fills the build settings from a community recipe (the model at its pinned commit, target, format,
        board, quantization, IMatrix); the user reviews them and starts the build."""
        idx = next((i for i in range(self.target_combo.count())
                    if str(self.target_combo.itemData(i)).casefold() == recipe.target.id.casefold()), -1)
        if idx < 0:
            QMessageBox.warning(self, "Recipe", f"Target {recipe.target.id} is not installed; install it from the "
                                                "Community Hub first.")
            return
        self._recipe = recipe
        self.model_input.setCurrentText(recipe.model.repo)
        self.target_combo.setCurrentIndex(idx)
        for combo, value in ((self.format_combo, recipe.format), (self.board_combo, recipe.board)):
            found = combo.findData(value) if value else -1
            if found >= 0:
                combo.setCurrentIndex(found)
        if recipe.quantization:
            self.quant_combo.setCurrentText(recipe.quantization)
            self._quant_user_set = True
        self.chk_imatrix.setChecked(recipe.options.use_imatrix)
        # The recipe the person chose to build: its values are the person's (an AI never overwrites them)
        self.gui_form.person_chose(["model", "target", "format", "board", "quantization", "imatrix"])
        self._log(f"Recipe {recipe.id}: {recipe.model.repo} at commit {recipe.model.revision[:12]}.")
        if recipe.options.dataset:
            self._log(f"The recipe was calibrated with {recipe.options.dataset.name} (sha256 "
                      f"{recipe.options.dataset.sha256[:16]}...); choose that file as calibration dataset.")

    def _browse_model(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Model", "", "Models (*.bin *.gguf *.safetensors);;All Files (*)")
        if path:
            self.model_input.setEditText(path)
            self.gui_form.person_edited("model")  # the person's pick: never overwritten by an AI

    def _browse_dataset(self):
        """NEW: Browse for custom calibration dataset"""
        path, _ = QFileDialog.getOpenFileName(self, "Select Calibration Dataset", "",
                                              "Datasets (*.txt *.json);;All Files (*)")
        if path:
            self.dataset_path = path
            self.gui_form.person_edited("dataset")
            self._log(f"Selected Dataset: {Path(path).name}")

    def _run_ai_optimizer(self):
        ditto = self.framework.get_component("ditto_manager")
        if not ditto:
            QMessageBox.critical(self, "Error", "Ditto Manager not loaded.")
            return
            
        self._set_state("think")
        self._log("Asking Ditto for optimal settings...")
        self.btn_optimize.setEnabled(False)
        
        self.ai_worker = AIOptimizerWorker(
            ditto, 
            self.model_input.currentText(),
            self.target_combo.currentText(),
            self.chk_rag.isChecked(),
            options=self._optimizer_options(),
            board=self._selected_board(),
        )
        self.ai_worker.finished.connect(self._on_ai_finished)
        self.ai_worker.start()

    def _optimizer_options(self):
        """Format -> quantization types the selected target (and board) can build, as offered in the combos."""
        caps = self._capabilities()
        if not caps:
            return {fmt: list(QUANT_SUGGESTIONS.get(fmt, [])) for _, fmt in BUILD_FORMATS}
        board = self._selected_board()
        options = {}
        for fid, cap in caps.formats.items():
            options[fid] = cap.quantizations_for(board if board in caps.boards_for(fid) else None)
        return options

    def _on_ai_finished(self, success, config, msg):
        self.btn_optimize.setEnabled(True)
        if success:
            self._set_state("success")
            self._log(f"Ditto Recommendation:\n{msg}")
            # Through the build form: marked and undoable, and the person's own values stay (N9'')
            apply_ditto_settings(self, config)
        else:
            self._set_state("fail")
            self._log(f"AI Failed: {msg}")

    def _submit_build(self):
        if not board_confirmed(self):  # a probe that was not sure about the board: the person confirms it (N9')
            return
        from orchestrator.Core.artifact_output import build_folder
        # A build the person starts here waits in the framework's build folder; when it has finished, the save dialog
        # opens at the output folder (eighth round, answer 3). The deployment window lists both folders.
        output_dir = str(build_folder(self.framework))

        # Check the request against the target's capabilities before starting a long build, and submit
        # exactly the values that were checked (an empty field means the board's default type).
        board = self._selected_board()
        quant = self.quant_combo.currentText().strip() or None
        caps = self._capabilities()
        if caps:
            _, board, quant = caps.normalize(self.format_combo.currentData(), board, quant)
            problems = caps.check_request(self.format_combo.currentData(), board, quant,
                                          dataset=bool(self.dataset_path), imatrix=self.chk_imatrix.isChecked())
            if problems:
                QMessageBox.warning(self, "Build settings", "\n".join(problems))
                return
        
        recipe = self._recipe if self._recipe and self.model_input.currentText().strip() == self._recipe.model.repo \
            else None
        try:
             target_fmt = ModelFormat(self.format_combo.currentData())
             opt_lvl = OptimizationLevel[self.opt_combo.currentText()]
        except (KeyError, ValueError):
             self._log("Error: Invalid enum selection.")
             return

        # Exactly the values checked above are compared with earlier builds and submitted, also when the person
        # changes the form while the model's commit is looked up.
        values = {"model": self.model_input.currentText().strip(), "target": self.target_combo.currentData(),
                  "target_fmt": target_fmt, "opt_lvl": opt_lvl, "quant": quant, "board": board,
                  "dataset": self.dataset_path, "recipe": recipe, "output_dir": output_dir,
                  "use_gpu": self.chk_gpu.isChecked(), "use_imatrix": self.chk_imatrix.isChecked(),
                  "priority": self._priority()}
        found, lookup = self._earlier_builds(values)
        if lookup is not None:
            self._look_up_commit(values, found, lookup)
            return
        self._start_or_reuse(values, found)

    def _start_or_reuse(self, values, found):
        """Asks about earlier builds with the same options (if any), then submits exactly `values`."""
        if found and not self._decide_about_earlier(found):
            return
        recipe = values["recipe"]
        req = BuildRequest(
            request_id="",
            workflow_type=WorkflowType.SIMPLE_CONVERSION,
            priority=values.get("priority", PriorityLevel.NORMAL),
            models=[values["model"]],
            targets=[values["target"]],
            target_formats=[values["target_fmt"]],
            optimization_level=values["opt_lvl"],
            quantization_options=[values["quant"]] if values["quant"] else [],
            parallel_builds=True,
            output_base_dir=values["output_dir"],
            description="Builder Tab Job",
            use_gpu=values["use_gpu"],
            # NEW v2.4.0: Pass IMatrix config
            use_imatrix=values["use_imatrix"],
            dataset_path=values["dataset"],
            target_board=values["board"],
            # A recipe pins the model commit (and context length) while its model is still selected.
            model_revision=recipe.model.revision if recipe else None,
            max_context_length=recipe.options.max_context_length if recipe else None,
        )

        try:
            # The build keeps running on the framework's background loop after this slot returns.
            rid = self.framework.run_coroutine(self.framework.orchestrator.submit_build_request(req), timeout=30)
            self._set_state("success")
            self._log(f"Job Submitted! ID: {rid}")
            self._track_build(rid)
            self._remember_build(values["model"], values["target"], values["board"], values["dataset"])
            QMessageBox.information(self, "Success", f"Job {rid} started.")
        except Exception as e:
            self._set_state("fail")
            self._log(f"Submission Error: {e}")

    def _earlier_builds(self, values):
        """(finished builds with 100 % the same options (eighth round, N11), the commit look-up still to do or None).
        The commit of a Hugging Face model's default branch is looked up (once, without the file list, never in
        offline mode) only when an earlier build could be identical; that look-up asks the network, so it runs in a
        worker (_look_up_commit), never on the GUI thread. ([], None) when nothing can be told."""
        service = self._build_service()
        if service is None or not hasattr(service, "earlier_builds"):
            return [], None
        from orchestrator.schemas.build import BuildRequestIn
        recipe = values["recipe"]
        try:
            request = BuildRequestIn(model=values["model"], revision=recipe.model.revision if recipe else None,
                                     target=values["target"], format=values["target_fmt"].value,
                                     board=values["board"], quantization=values["quant"],
                                     use_imatrix=values["use_imatrix"], dataset_path=values["dataset"],
                                     use_gpu=values["use_gpu"],
                                     max_context_length=recipe.options.max_context_length if recipe else None)
            level = getattr(values["opt_lvl"], "value", "balanced")
            found = service.earlier_builds(request, optimization=level, local_paths=True)
            if found and not request.revision and all(b.match == "commit_unchecked" for b in found):
                return found, (service, request, level)
            return found, None
        except Exception as e:  # noqa: BLE001 - the look-up never stops a build; the person builds as asked
            self._log(f"Earlier builds not checked: {e}")
            return [], None

    def _look_up_commit(self, values, found, lookup):
        """Looks the model's commit up in a worker (up to 10 s on a slow network) while the window stays usable; the
        Start button waits meanwhile. The answer continues in _on_commit_found on the GUI thread."""
        service = lookup[0]
        self.btn_start.setEnabled(False)
        self._log("Looking up the model's current commit on Hugging Face, to compare it with an earlier build ...")
        self._pending_build = (values, found, lookup)
        self._commit_worker = CommitLookupWorker(service, values["model"])
        self._commit_worker.found.connect(self._on_commit_found)
        self._commit_worker.start()

    def _on_commit_found(self, commit):
        pending, self._pending_build = getattr(self, "_pending_build", None), None
        self.btn_start.setEnabled(True)
        if pending is None:
            return
        values, found, (service, request, level) = pending
        if commit:
            try:
                found = service.earlier_builds(request, revision=commit, optimization=level, local_paths=True)
            except Exception as e:  # noqa: BLE001 - the look-up never stops a build
                self._log(f"Earlier builds not checked: {e}")
                found = []
        self._start_or_reuse(values, found)

    def _decide_about_earlier(self, found) -> bool:
        """True when the build is to start: no earlier build with the same options, or the person chose to build
        again. "Use the existing build" starts nothing and names it; nothing is skipped without the person."""
        if not found:
            return True
        from orchestrator.gui import artifact_save
        from orchestrator.utils.localization import ui_text
        choice = artifact_save.ask_earlier(self, found)
        if choice == "use":
            self._log(ui_text("earlier.used", path=str(found[0].info.get("path") or found[0].label)))
            return False
        return choice == "again"

    # --- SAVING A FINISHED BUILD (eighth round, answer 3) ---

    def _priority(self):
        """The chosen build priority (PriorityLevel; normal when the name is unknown)."""
        name = self.priority_combo.currentData() if hasattr(self, "priority_combo") else None
        return getattr(PriorityLevel, str(name or "NORMAL"), PriorityLevel.NORMAL)

    # --- TARGETS (the CLI's `targets list` / `targets validate`) ---

    def _open_targets(self):
        """Targets...: every target with its ID, boards, formats and support level; the window selects this one."""
        from orchestrator.gui.targets_window import open_targets_dialog
        return open_targets_dialog(self, self.framework, select=self.target_combo.currentData(),
                                   on_reload=self._refresh_targets)

    def _check_target(self):
        """Check target: TargetManager.validate on the selected target (its target.yml read again)."""
        from orchestrator.gui.plain import plain_box
        from orchestrator.gui.targets_window import show_target_check
        target = self.target_combo.currentData()
        if not target:
            plain_box(self, "information", ui_text("bld.check.title"), ui_text("bld.builder.no_target"))
            return None
        return show_target_check(self, self.framework, target)

    def _offer_save(self, request_id):
        """For every golden artifact of a finished build the person started here: the save dialog at the output
        folder (artifact_save.ask_and_save). Cancel keeps it in the build folder; nothing is deleted."""
        from orchestrator.gui import artifact_save
        orch = getattr(self.framework, "orchestrator", None)
        state = getattr(orch, "_workflows", {}).get(request_id) if orch is not None else None
        for folder in list(getattr(state, "artifacts", []) or []):
            worker = artifact_save.ask_and_save(self, self.framework, Path(folder), self._on_saved)
            if worker is not None:
                self._save_workers.append(worker)
                worker.finished.connect(lambda w=worker: self._save_workers.remove(w)
                                        if w in self._save_workers else None)

    def _on_saved(self, ok: bool, text: str):
        self._log(text)
        if ok:
            self._set_state("success")

    # --- RUNNING BUILD (Cancel button) ---

    def _build_service(self):
        return getattr(self.framework, "build_service", None)

    def _track_build(self, request_id):
        """Follows the submitted build until it ends; Cancel is enabled meanwhile."""
        self._active_request = request_id
        self.btn_cancel.setEnabled(True)
        self.progress_panel.start(request_id)
        self._build_timer.start()

    def _poll_build(self):
        rid, service = self._active_request, self._build_service()
        if rid is None or service is None:
            self._build_timer.stop()
            return
        # Stage, percent and the new log lines; None when the service does not know the build any more
        status = self.progress_panel.poll(service, rid)
        if status in (None, "completed", "error", "cancelled"):
            self._build_timer.stop()
            self._active_request = None
            self.btn_cancel.setEnabled(False)
            if status:
                self._log(f"Build {rid} ended: {status}.")
            if status == "completed":
                self._offer_save(rid)

    def _cancel_build(self):
        rid, service = self._active_request, self._build_service()
        if rid is None or service is None:
            return
        answer = QMessageBox.question(
            self, "Cancel build",
            f"Cancel build {rid}? Its container is stopped and nothing is written to the output folder.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        self.btn_cancel.setEnabled(False)
        self._log(f"Cancelling build {rid} ...")
        self.cancel_worker = CancelBuildWorker(service, rid)
        self.cancel_worker.finished.connect(self._on_cancel_finished)
        self.cancel_worker.start()

    def _on_cancel_finished(self, outcome, message):
        rid = self._active_request
        if outcome == "cancelled":
            self._set_state("default")
            self._log(f"Build {rid} cancelled; its container was stopped.")
        elif outcome == "finished":
            self._log(f"Build {rid} could no longer be cancelled and has ended on its own. {message}".strip())
        elif outcome == "running":
            self._log(f"Build {rid} is cancelled but has not stopped yet.")
        else:
            self._log(f"Cancel failed: {message}")
        self._poll_build()
