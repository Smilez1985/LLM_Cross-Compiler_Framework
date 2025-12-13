#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Community Hub (v2.3.0 Enterprise)
DIREKTIVE: Goldstandard, GUI, Swarm Integration.

Zweck:
Ermöglicht den Zugriff auf Community-Module und den 'Swarm Memory' Upload.

Updates v2.3.0:
- Reuse existing CommunityManager instance.
- Robust SecretsManager integration for GitHub tokens.
- Dark Mode styling.
"""

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, 
    QHeaderView, QMessageBox, QLineEdit, QInputDialog
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor

# Localization Import
try:
    from orchestrator.utils.localization import tr
except ImportError:
    def tr(key, default=None): return default or key

# --- WORKERS ---

class CommunityWorker(QThread):
    finished = Signal(list)
    error = Signal(str)
    
    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        
    def run(self):
        try:
            if not self.manager:
                raise Exception("Community Manager not initialized.")
            items = self.manager.scan_modules() # Local scan as placeholder
            self.finished.emit(items)
        except Exception as e:
            self.error.emit(str(e))

class DownloadWorker(QThread):
    finished = Signal(str)
    error = Signal(str)
    
    def __init__(self, manager, package_id):
        super().__init__()
        self.manager = manager
        self.pkg_id = package_id
        
    def run(self):
        try:
            path = self.manager.install_module(self.pkg_id)
            self.finished.emit(str(path))
        except Exception as e:
            self.error.emit(str(e))

class UploadWorker(QThread):
    """Worker für den Swarm Upload"""
    finished = Signal(bool)
    log = Signal(str)
    
    def __init__(self, manager, token, username):
        super().__init__()
        self.manager = manager
        self.token = token
        self.username = username
        
    def run(self):
        try:
            self.log.emit("Exporting Knowledge Base...")
            export_path = self.manager.export_knowledge_base("swarm_upload")
            
            if not export_path:
                self.log.emit("Export failed (Empty RAG?).")
                self.finished.emit(False)
                return

            self.log.emit(f"Encrypting & Uploading {export_path}...")
            success = self.manager.upload_knowledge_to_swarm(export_path, self.token, self.username)
            self.finished.emit(success)
        except Exception as e:
            self.log.emit(f"Error: {e}")
            self.finished.emit(False)

# --- MAIN WINDOW ---

class CommunityHubWindow(QMainWindow):
    def __init__(self, framework_manager, parent=None):
        super().__init__(parent)
        self.framework_manager = framework_manager
        
        # Reuse existing manager instance from framework if available
        self.comm_manager = getattr(framework_manager, 'community_manager', None)
        
        self.setWindowTitle(tr("menu.open_hub", "Community Hub"))
        self.resize(900, 600)
        
        self._init_ui()
        self.all_items = []
        self.refresh_list()

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        
        # Header
        intro_text = tr("hub.intro", "Discover and share hardware targets and models.")
        layout.addWidget(QLabel(intro_text))
        
        # Top Bar
        hbox = QHBoxLayout()
        self.search_edit = QLineEdit()
        search_ph = tr("hf.search_placeholder", "Search...")
        self.search_edit.setPlaceholderText(search_ph)
        self.search_edit.textChanged.connect(self.filter_list)
        hbox.addWidget(self.search_edit)
        
        refresh_txt = tr("btn.refresh", "Refresh")
        self.refresh_btn = QPushButton(refresh_txt)
        self.refresh_btn.clicked.connect(self.refresh_list)
        hbox.addWidget(self.refresh_btn)
        
        # Upload Button
        self.upload_btn = QPushButton("🧠 Contribute to Swarm")
        self.upload_btn.setStyleSheet("background-color: #673AB7; color: white; font-weight: bold;")
        self.upload_btn.clicked.connect(self.on_upload_click)
        hbox.addWidget(self.upload_btn)
        
        layout.addLayout(hbox)
        
        # Table
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Name", "Arch", "Author", "Actions"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        # Dark Mode Styling
        self.table.setStyleSheet("QTableWidget { background-color: #1e1e1e; color: #eee; gridline-color: #333; } QHeaderView::section { background-color: #333; color: white; }")
        layout.addWidget(self.table)
        
        self.status_label = QLabel(tr("status.ready", "Ready."))
        layout.addWidget(self.status_label)

    def refresh_list(self):
        if not self.comm_manager:
             self.status_label.setText("Error: Community Manager missing.")
             return

        self.status_label.setText("Loading...")
        self.worker = CommunityWorker(self.comm_manager)
        self.worker.finished.connect(self.on_load_finished)
        self.worker.error.connect(self.on_error)
        self.worker.start()

    def on_load_finished(self, items):
        self.all_items = items
        self.populate_table(items)
        self.status_label.setText(tr("status.ready", "Ready."))

    def on_error(self, err):
        self.status_label.setText(f"Error: {err}")
        QMessageBox.warning(self, "Error", str(err))

    def filter_list(self, text):
        filtered = [i for i in self.all_items if text.lower() in i.name.lower()]
        self.populate_table(filtered)

    def populate_table(self, items):
        self.table.setRowCount(0)
        for item in items:
            row = self.table.rowCount()
            self.table.insertRow(row)
            
            self.table.setItem(row, 0, QTableWidgetItem(item.name))
            self.table.setItem(row, 1, QTableWidgetItem(item.architecture))
            self.table.setItem(row, 2, QTableWidgetItem(item.author))
            
            btn_txt = tr("btn.download", "Download")
            btn = QPushButton(btn_txt)
            if item.is_installed:
                btn.setText("Installed")
                btn.setEnabled(False)
                btn.setStyleSheet("color: green;")
            else:
                btn.clicked.connect(lambda checked, pid=item.id: self.download_item(pid))
                btn.setStyleSheet("background-color: #2196F3; color: white;")
            
            self.table.setCellWidget(row, 3, btn)

    def download_item(self, pkg_id):
        self.status_label.setText(f"Installing {pkg_id}...")
        self.dl_worker = DownloadWorker(self.comm_manager, pkg_id)
        self.dl_worker.finished.connect(lambda p: QMessageBox.information(self, "Success", "Module installed!"))
        self.dl_worker.error.connect(self.on_error)
        self.dl_worker.start()

    # --- UPLOAD LOGIC ---

    def on_upload_click(self):
        if not self.comm_manager: return

        # 1. Credentials
        token = None
        user = "CommunityUser"
        
        # Try Keyring first via SecretsManager
        sm = getattr(self.framework_manager, 'secrets_manager', None)
        if sm:
            token = sm.get_secret("github_token")
            
        if not token:
            token, ok = QInputDialog.getText(self, "GitHub Auth", "Enter GitHub Token (repo scope):", QLineEdit.Password)
            if not ok or not token: return
            
            # Save for future?
            if sm:
                sm.set_secret("github_token", token)

        user_in, ok = QInputDialog.getText(self, "Contributor Name", "Enter your Username:", text=user)
        if ok: user = user_in

        # 2. Start Upload
        self.status_label.setText("Uploading to Swarm...")
        self.upload_btn.setEnabled(False)
        
        self.up_worker = UploadWorker(self.comm_manager, token, user)
        self.up_worker.log.connect(lambda s: self.status_label.setText(s))
        self.up_worker.finished.connect(self.on_upload_finished)
        self.up_worker.start()

    def on_upload_finished(self, success):
        self.upload_btn.setEnabled(True)
        if success:
            QMessageBox.information(self, "Swarm Upload", "✅ Knowledge successfully contributed to the Swarm!")
        else:
            QMessageBox.warning(self, "Swarm Upload", "❌ Upload failed. Check logs.")
        self.status_label.setText("Ready.")
