#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Main Window (v2.3.0 Enterprise)
DIREKTIVE: Goldstandard GUI.

Features:
- Integration of SecretsManager UI.
- Status display for Self-Healing/Guardian.
- Connects BuilderTab, ChatWindow, DeploymentWindow.

Updates v2.3.0:
- Full integration of all tabs (No mocks).
- Robust Framework initialization.
"""

import sys
from pathlib import Path
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QPushButton, QLabel, QTabWidget, QStatusBar,
    QMenu, QMenuBar, QMessageBox, QToolBar
)
from PySide6.QtGui import QAction, QIcon
from PySide6.QtCore import Qt, QSize

# Core Framework
from orchestrator.Core.framework import FrameworkManager
from orchestrator.utils.localization import get_instance as get_i18n

# GUI Components
# Wir importieren ALLES regulär, da die Dateien existieren.
from orchestrator.gui.dialogs import SecretInputDialog, HealingConfirmDialog
from orchestrator.gui.builder_tab import BuilderTab
from orchestrator.gui.chat_window import ChatWindow
from orchestrator.gui.deployment_window import DeploymentWindow

class MainOrchestrator(QMainWindow):
    def __init__(self, app_root: Path):
        super().__init__()
        self.app_root = app_root
        self.i18n = get_i18n()
        
        self.framework = FrameworkManager()
        
        # Initialisierung des Kernels
        if not self.framework.initialize():
            QMessageBox.critical(self, "Fatal Error", "Framework initialization failed. Check logs.")
            sys.exit(1)
            
        self.setWindowTitle(f"LLM Cross-Compiler Framework v{self.framework.info.version} (Enterprise)")
        self.resize(1200, 800)
        
        self._setup_menu()
        self._setup_toolbar()
        self._setup_central_widget()
        self._setup_statusbar()
        self._apply_styles()

    def _setup_menu(self):
        menubar = self.menuBar()
        
        file_menu = menubar.addMenu(self.i18n.t("menu.file", "File"))
        exit_action = QAction(self.i18n.t("menu.exit", "Exit"), self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        sec_menu = menubar.addMenu("Security")
        act_openai = QAction("Set OpenAI API Key", self)
        act_openai.triggered.connect(lambda: self._open_secret_dialog("openai_api_key", "OpenAI API Key"))
        sec_menu.addAction(act_openai)
        
        act_ssh = QAction("Set Target SSH Password", self)
        act_ssh.triggered.connect(lambda: self._open_secret_dialog("target_password", "SSH Password (Root)"))
        sec_menu.addAction(act_ssh)

        help_menu = menubar.addMenu("Help")
        about_action = QAction("About", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _setup_toolbar(self):
        toolbar = QToolBar("Main Toolbar")
        toolbar.setIconSize(QSize(32, 32))
        self.addToolBar(toolbar)

    def _setup_central_widget(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        
        # 1. Builder Tab
        self.builder_tab = BuilderTab(self.framework)
        self.tabs.addTab(self.builder_tab, "🏗️ Builder")
        
        # 2. Chat Window
        self.chat_window = ChatWindow(self.framework)
        self.tabs.addTab(self.chat_window, "💬 AI Chat")
        
        # 3. Deployment Window
        self.deployment_window = DeploymentWindow(self.framework)
        self.tabs.addTab(self.deployment_window, "🚀 Deployment")
        
        # 4. Model Placeholder (Optional)
        self.tabs.addTab(QLabel("Model Manager (Coming Soon)"), "🧠 Models")

    def _setup_statusbar(self):
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        
        # Check components via framework instance
        if getattr(self.framework, 'secrets_manager', None):
            self.lbl_sec = QLabel("🛡️ Secrets Secured")
            self.lbl_sec.setStyleSheet("color: #4CAF50; font-weight: bold; margin-right: 10px;")
            self.status.addPermanentWidget(self.lbl_sec)
        
        if getattr(self.framework, 'self_healing_manager', None):
            self.lbl_heal = QLabel("🏥 Healing Active")
            self.lbl_heal.setStyleSheet("color: #2196F3; font-weight: bold; margin-right: 10px;")
            self.status.addPermanentWidget(self.lbl_heal)
            
        self.status.showMessage("Ready.")

    def _open_secret_dialog(self, key, name):
        if not getattr(self.framework, 'secrets_manager', None):
            QMessageBox.critical(self, "Error", "SecretsManager not available!")
            return
            
        dlg = SecretInputDialog(self.framework, key, name, self)
        dlg.exec()

    # --- Self-Healing Trigger Logic ---
    def trigger_healing_dialog(self, proposal):
        dlg = HealingConfirmDialog(proposal, self)
        if dlg.exec() == QDialog.Accepted:
            final_cmd = dlg.get_final_command()
            proposal.fix_command = final_cmd
            proposal.source = "USER_OVERRIDE" 
            
            if self.framework.self_healing_manager:
                success = self.framework.self_healing_manager.apply_fix(proposal, auto_confirm=True)
                if success:
                    self.status.showMessage("✅ Fix applied successfully!", 5000)
                else:
                    QMessageBox.critical(self, "Healing Failed", "The fix command failed. Check logs.")

    def _show_about(self):
        ver = self.framework.info.version if hasattr(self.framework, 'info') else "Unknown"
        QMessageBox.about(self, "About", f"LLM Cross-Compiler Framework\nVersion: {ver}\n\n(c) 2025 Framework Team")

    def _apply_styles(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #2b2b2b; color: #ffffff; }
            QTabWidget::pane { border: 1px solid #444; }
            QTabBar::tab { background: #333; color: #fff; padding: 8px; }
            QTabBar::tab:selected { background: #555; }
            QLabel { color: #fff; }
            QMenuBar { background-color: #333; color: #fff; }
            QMenuBar::item:selected { background-color: #555; }
            QMenu { background-color: #333; color: #fff; }
            QMenu::item:selected { background-color: #555; }
            QTextEdit, QLineEdit { background-color: #1e1e1e; color: #eee; border: 1px solid #555; }
            QPushButton { background-color: #444; color: #fff; padding: 5px; border-radius: 3px; }
            QPushButton:hover { background-color: #555; }
        """)

    def closeEvent(self, event):
        if self.framework:
            self.framework.shutdown()
        event.accept()
