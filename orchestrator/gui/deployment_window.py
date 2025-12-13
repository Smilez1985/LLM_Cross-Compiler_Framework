#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Deployment Window (v2.0 Functional)
DIREKTIVE: Goldstandard GUI & Threading.

Zweck:
Verbindet die GUI mit dem DeploymentManager.
Führt Paketierung und Deployment asynchron im Hintergrund aus (QThread).
"""

from pathlib import Path
import logging
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
    QComboBox, QPushButton, QTableWidget, QTableWidgetItem,
    QCheckBox, QGroupBox, QHeaderView, QMessageBox, QListWidget,
    QProgressBar, QInputDialog, QLineEdit
)
from PySide6.QtCore import Qt, QThread, Signal
from orchestrator.utils.localization import get_instance as get_i18n

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
            output_dir = Path(self.framework.config.output_dir)
            
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
                self.finished.emit(True, f"Deployment successful!\nPackage: {pkg_path.name}")
            else:
                self.finished.emit(False, "Deployment failed during transfer/execution.")

        except Exception as e:
            self.finished.emit(False, str(e))

class DeploymentWindow(QWidget):
    def __init__(self, framework_manager, parent=None):
        super().__init__(parent)
        self.framework = framework_manager
        self.target_manager = framework_manager.get_component("target_manager")
        self.secrets_manager = framework_manager.get_component("secrets_manager")
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
        grp_artifact.setLayout(layout_art)
        layout.addWidget(grp_artifact)
        
        # --- 2. Target Profile Selection ---
        grp_target = QGroupBox("2. Select Target Hardware Profile")
        layout_target = QHBoxLayout()
        self.cb_profiles = QComboBox()
        layout_target.addWidget(QLabel("Profile:"))
        layout_target.addWidget(self.cb_profiles, 1)
        
        # Target Connection Info (Quick Edit)
        self.txt_ip = QLineEdit("192.168.1.100")
        self.txt_ip.setPlaceholderText("Target IP")
        layout_target.addWidget(QLabel("IP:"))
        layout_target.addWidget(self.txt_ip)
        
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
        output_dir = Path(self.framework.config.output_dir)
        if output_dir.exists():
            for item in output_dir.glob("*"):
                # Zeige nur relevante Dateien (z.B. ZIPs oder Ordner, aber keine Logs)
                if item.name.startswith("deploy_"): continue # Verstecke bereits erstellte Pakete
                if item.is_dir() or item.suffix in ['.zip', '.tar.gz', '.bin']:
                    self.list_artifacts.addItem(item.name)
        
        # Profiles
        self.cb_profiles.clear()
        if self.target_manager:
            profiles = self.target_manager.list_hardware_profiles()
            self.cb_profiles.addItems(profiles)
        else:
            self.cb_profiles.addItem("Error: TargetManager not loaded")

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

        artifact_path = Path(self.framework.config.output_dir) / artifact_item.text()
        target_ip = self.txt_ip.text().strip()
        user = self.txt_user.text().strip()

        # 2. Credentials (Secure Access)
        password = None
        if self.secrets_manager:
            password = self.secrets_manager.get_secret("target_password")
        
        # Fallback: Wenn kein PW im Keyring, frage User (Session only)
        if not password:
            pwd, ok = QInputDialog.getText(self, "SSH Password", 
                                         f"Enter password for {user}@{target_ip}:", 
                                         QLineEdit.Password)
            if ok and pwd:
                password = pwd
            else:
                return # Cancelled

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
