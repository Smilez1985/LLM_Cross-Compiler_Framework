#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Deployment Window (v2.3.0 Functional)
DIREKTIVE: Goldstandard GUI & Threading.

Zweck:
Verbindet die GUI mit dem DeploymentManager.
Führt Paketierung und Deployment asynchron im Hintergrund aus (QThread).

Updates v2.3.0:
- Safe config access via ConfigManager.get().
- Improved file browsing for artifacts.
- Robust SecretsManager integration.
"""

from pathlib import Path
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QComboBox, QPushButton, QTableWidget, QTableWidgetItem,
    QGroupBox, QHeaderView, QMessageBox, QListWidget,
    QProgressBar, QInputDialog, QLineEdit, QListWidgetItem
)
from PySide6.QtCore import Qt, QThread, Signal
from orchestrator.utils.localization import get_instance as get_i18n
from orchestrator.gui.dialogs import confirm_host_key, fill_recent, select_recent
from orchestrator.utils import input_history

class DeploymentWorker(QThread):
    """
    Führt den Deployment-Prozess im Hintergrund aus, um GUI-Freeze zu verhindern.
    """
    progress = Signal(str)
    finished = Signal(bool, str)

    def __init__(self, framework, artifact_path, profile_name, docker_config, target_ip, user, password):
        super().__init__()
        self.framework = framework
        self.artifact_path = artifact_path
        self.profile_name = profile_name
        self.docker_config = docker_config
        self.target_ip = target_ip
        self.user = user
        self.password = password

    def run(self):
        dep_mgr = self.framework.get_component("deployment_manager")
        if not dep_mgr:
            self.finished.emit(False, "DeploymentManager not loaded.")
            return

        try:
            # 1. Paketierung
            self.progress.emit("📦 Generating Deployment Package...")
            
            # Safe config access
            get_cfg = getattr(self.framework.config, 'get', lambda k, d=None: getattr(self.framework.config, k, d))
            # The configured folder (relative to the installation), the one the artifact list shows; never the cwd.
            output_dir = Path(get_cfg("output_dir", "output"))
            if hasattr(self.framework, "resolve_path"):
                output_dir = self.framework.resolve_path(output_dir)

            pkg_path = dep_mgr.create_deployment_package(
                artifact_path=self.artifact_path,
                profile_name=self.profile_name,
                docker_config=self.docker_config,
                output_dir=output_dir
            )
            
            if not pkg_path:
                self.finished.emit(False, "Package generation failed.")
                return

            # 2. Deployment
            self.progress.emit(f"🚀 Deploying to {self.target_ip}...")
            success = dep_mgr.deploy_artifact(
                artifact_path=pkg_path,
                target_ip=self.target_ip,
                user=self.user,
                password=self.password
            )

            if success:
                self._remember_device()
                self.finished.emit(True, f"Deployment successful!\nPackage: {pkg_path.name}")
            else:
                self.finished.emit(False, "Deployment failed during transfer/execution.")

        except Exception as e:
            self.finished.emit(False, str(e))

    def _remember_device(self, port: int = 22) -> None:
        """After a deployment to a device whose host key the person confirmed in this window (confirm_host_key) and
        that the framework logged in to: its record, so AI tools may name it for deploy_to_device (each deploy is
        still confirmed). Written only here and in the CLI, never by a tool; it never fails the deployment."""
        from orchestrator.runtime.device.trusted import record_trusted_device
        from orchestrator.utils import ssh_trust
        state = getattr(getattr(self.framework, "config", None), "user_state_dir", None)
        if not state:
            return
        try:
            key = ssh_trust.fetch_host_key(self.target_ip, port)
            if ssh_trust.check_host_key(Path(state), self.target_ip, port, key) != "known":
                return
        except Exception:  # noqa: BLE001 - unreachable now: the next deployment records it
            return
        audit = getattr(getattr(self.framework, "tool_registry", None), "audit", None)
        record_trusted_device(Path(state), self.target_ip, port, self.user, key, via="gui", paired=True,
                              audit=audit, actor="gui")

class DeploymentWindow(QWidget):
    def __init__(self, framework_manager, parent=None):
        super().__init__(parent)
        self.framework = framework_manager
        
        # Access components safely
        self.target_manager = getattr(framework_manager, 'target_manager', None)
        self.secrets_manager = getattr(framework_manager, 'secrets_manager', None)
        self.history = input_history.for_config(getattr(framework_manager, "config", None))
        
        self.i18n = get_i18n()
        self.worker = None
        
        self._init_ui()
        self.refresh_data()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        
        # --- 1. Artifact Selection ---
        grp_artifact = QGroupBox("1. Select Golden Artifact")
        layout_art = QVBoxLayout()
        self.list_artifacts = QListWidget()
        layout_art.addWidget(self.list_artifacts)
        # Save...: a build still in the framework's build folder goes to the output folder or another folder
        # (eighth round, answer 3); the person's save dialog, never by itself
        from orchestrator.utils.localization import ui_text
        self.btn_save = QPushButton(ui_text("save.button").replace("&", "&&"))
        self.btn_save.clicked.connect(self._on_save)
        layout_art.addWidget(self.btn_save)
        self._save_workers = []
        grp_artifact.setLayout(layout_art)
        layout.addWidget(grp_artifact)
        
        # --- 2. Target Profile Selection ---
        grp_target = QGroupBox("2. Select Target Hardware Profile")
        layout_target = QHBoxLayout()
        self.cb_profiles = QComboBox()
        layout_target.addWidget(QLabel("Profile:"))
        layout_target.addWidget(self.cb_profiles, 1)
        
        # Target Connection Info (Quick Edit); devices deployed to before are offered first
        self.cb_device = QComboBox()
        self.cb_device.setEditable(True)
        self.cb_device.setInsertPolicy(QComboBox.NoInsert)
        self.cb_device.setMinimumWidth(160)
        self.cb_device.setAccessibleName("Target IP or host name")
        self.cb_device.lineEdit().setPlaceholderText("Target IP")
        recent = self._recent("device")
        fill_recent(self.cb_device, recent, text=None if recent else "192.168.1.100")
        layout_target.addWidget(QLabel("IP:"))
        layout_target.addWidget(self.cb_device)
        
        self.txt_user = QLineEdit("root")
        self.txt_user.setPlaceholderText("User")
        self.txt_user.setFixedWidth(80)
        layout_target.addWidget(QLabel("User:"))
        layout_target.addWidget(self.txt_user)
        
        btn_refresh = QPushButton("🔄 Refresh")
        btn_refresh.clicked.connect(self.refresh_data)
        layout_target.addWidget(btn_refresh)
        
        grp_target.setLayout(layout_target)
        layout.addWidget(grp_target)
        
        # --- 3. Docker Configuration ---
        self.grp_docker = QGroupBox("3. Docker Configuration")
        self.grp_docker.setCheckable(True)
        self.grp_docker.setChecked(False)
        self.grp_docker.setTitle("Use Docker Containerization?")
        
        layout_docker = QVBoxLayout()
        self.table_docker = QTableWidget()
        self.table_docker.setColumnCount(2)
        self.table_docker.setHorizontalHeaderLabels(["Component", "Container Type"])
        self.table_docker.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        
        # Mockup Rows
        self._add_docker_row("Inference Engine", ["Llama.cpp (CPU)", "RKLLM (NPU)", "TensorRT (GPU)"])
        self._add_docker_row("Knowledge Base", ["Qdrant Sidecar", "None"])
        
        layout_docker.addWidget(self.table_docker)
        self.grp_docker.setLayout(layout_docker)
        layout.addWidget(self.grp_docker)
        
        # --- Progress Bar ---
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)
        
        # --- 4. Action Buttons ---
        btn_layout = QHBoxLayout()
        
        self.btn_deploy = QPushButton("🚀 Deploy to Target")
        self.btn_deploy.clicked.connect(self._on_deploy)
        self.btn_deploy.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold; padding: 10px;")
        
        btn_layout.addWidget(self.btn_deploy)
        layout.addLayout(btn_layout)

    def _add_docker_row(self, name, options):
        row = self.table_docker.rowCount()
        self.table_docker.insertRow(row)
        self.table_docker.setItem(row, 0, QTableWidgetItem(name))
        cb = QComboBox()
        cb.addItems(options)
        self.table_docker.setCellWidget(row, 1, cb)

    def refresh_data(self):
        # Artifacts
        self.list_artifacts.clear()

        # Every finished build writes build_info.json; list builds, not raw folders or archives: in the output
        # folder, in the framework's build folder (not saved yet, earlier builds) and where the person saved them.
        from orchestrator.Core.artifact_output import BUILDS, list_artifacts
        from orchestrator.utils.localization import ui_text
        try:
            listed = list_artifacts(self.framework)
        except Exception:  # noqa: BLE001 - a folder that cannot be read lists nothing
            listed = []
        for item in listed:
            build = item.info
            if not build.get("primary"):
                continue
            label = " · ".join(str(x) for x in (
                build.get("model") or Path(str(build.get("model_source", "?"))).name,
                str(build.get("format", "?")).upper(),
                build.get("quantization") or "default",
                build.get("board") or build.get("target") or "?",
            ))
            if item.where == BUILDS:
                label += f" ({ui_text('save.not_saved')})"
            list_item = QListWidgetItem(f"📦 {label}")
            list_item.setData(Qt.UserRole, build["path"])
            list_item.setData(Qt.UserRole + 1, item.where)
            list_item.setToolTip(f"{build['path']}\n{build.get('primary')}\n{build.get('created', '')}")
            self.list_artifacts.addItem(list_item)

        # Profiles (target ids): keep the selection, else preselect the most recently used target
        previous = self.cb_profiles.currentText()
        self.cb_profiles.clear()
        if self.target_manager:
            profiles = self.target_manager.list_hardware_profiles()
            if profiles:
                self.cb_profiles.addItems(profiles)
            else:
                self.cb_profiles.addItem("Generic Profile (Default)")
        else:
            self.cb_profiles.addItem("Error: TargetManager not loaded")
        select_recent(self.cb_profiles, ([previous] if previous else []) + self._recent("target"))

    def _recent(self, field):
        return self.history.get(field) if self.history else []

    def _on_save(self):
        """Save... for the selected build: only one still in the framework's build folder; the person picks the folder
        (the output folder is preselected) in the operating system's own dialog."""
        from orchestrator.Core.artifact_output import BUILDS
        from orchestrator.gui import artifact_save
        from orchestrator.gui.plain import plain_box
        from orchestrator.utils.localization import ui_text
        item = self.list_artifacts.currentItem()
        if item is None or item.data(Qt.UserRole + 1) != BUILDS:
            plain_box(self, "information", ui_text("save.button").rstrip("…"), ui_text("save.only_build_folder"))
            return
        worker = artifact_save.ask_and_save(self, self.framework, Path(item.data(Qt.UserRole)), self._on_saved)
        if worker is not None:
            self._save_workers.append(worker)
            worker.finished.connect(lambda w=worker: self._save_workers.remove(w) if w in self._save_workers else None)

    def _on_saved(self, ok: bool, text: str):
        from orchestrator.gui.plain import plain_box
        from orchestrator.utils.localization import ui_text
        plain_box(self, "information" if ok else "warning", ui_text("save.button").rstrip("…"), text)
        self.refresh_data()

    def reload_history(self):
        """Refills the recent devices (after a deployment or after the history was cleared)."""
        fill_recent(self.cb_device, self._recent("device"), text=self.cb_device.currentText())

    def _on_deploy(self):
        # 1. Validation
        artifact_item = self.list_artifacts.currentItem()
        if not artifact_item:
            QMessageBox.warning(self, "Warning", "Please select an artifact first.")
            return
        
        profile_name = self.cb_profiles.currentText()
        if not profile_name:
            QMessageBox.warning(self, "Warning", "Please select a Hardware Profile.")
            return

        artifact_path = Path(artifact_item.data(Qt.UserRole))
        
        target_ip = self.cb_device.currentText().strip()
        user = self.txt_user.text().strip()

        # 2. Trust the device (host key fingerprint, first use only) and log in. The password is needed
        #    only until the framework's SSH key is installed; it is used once and never stored.
        if not confirm_host_key(self, self.framework, target_ip):
            return
        if self.history:
            self.history.add("device", target_ip)  # a trusted, reachable device
            if self.target_manager and profile_name in self.target_manager.list_hardware_profiles():
                self.history.add("target", profile_name)
            self.reload_history()
        pwd, ok = QInputDialog.getText(
            self, "SSH login",
            f"Password for {user}@{target_ip}\n(only the first time: used once to install the framework's SSH key, "
            "never stored; leave empty if the key is already installed)", QLineEdit.Password)
        if not ok:
            return
        password = pwd or None

        # 3. Docker Config gathering
        docker_config = {"use_docker": self.grp_docker.isChecked()}
        # TODO: Hier detaillierte Container-Auswahl aus der Tabelle auslesen

        # 4. Start Worker
        self.btn_deploy.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0) # Indeterminate animation
        
        self.worker = DeploymentWorker(
            self.framework, artifact_path, profile_name, 
            docker_config, target_ip, user, password
        )
        self.worker.progress.connect(self._update_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.start()

    def _update_progress(self, msg):
        self.btn_deploy.setText(msg)

    def _on_finished(self, success, msg):
        self.btn_deploy.setEnabled(True)
        self.btn_deploy.setText("🚀 Deploy to Target")
        self.progress_bar.setVisible(False)
        
        if success:
            QMessageBox.information(self, "Success", msg)
        else:
            QMessageBox.critical(self, "Error", msg)
