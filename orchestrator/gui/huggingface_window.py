#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Hugging Face Browser (v2.3.0)
DIREKTIVE: Goldstandard, GUI, Internationalisierung.

Features:
- Suche nach Modellen auf Hugging Face.
- Filterung nach Lizenz/Gated Status.
- Asynchroner Download manager.
- Token-Handling für geschützte Modelle (Llama-3 etc.).

Updates v2.3.0:
- Integrated `AskTokenDialog` locally for robustness.
- Improved threading and error handling.
- Dark Mode Styling.
"""

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, 
    QHeaderView, QLabel, QMessageBox, QComboBox, QDialog, 
    QListWidget, QDialogButtonBox, QFormLayout, QCheckBox
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor

from orchestrator.utils.localization import get_instance as get_i18n

# Helper for Translation
def tr(key, default=None):
    i18n = get_i18n()
    return i18n.t(key, default) if i18n else (default or key)

# --- WORKERS ---

class SearchWorker(QThread):
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, manager, query):
        super().__init__()
        self.manager = manager
        self.query = query

    def run(self):
        try:
            results = self.manager.search_huggingface_models(self.query)
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))

class FileListWorker(QThread):
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, manager, repo_id, token=None):
        super().__init__()
        self.manager = manager
        self.repo_id = repo_id
        self.token = token

    def run(self):
        try:
            files = self.manager.list_repo_files(self.repo_id, self.token)
            self.finished.emit(files)
        except Exception as e:
            self.error.emit(str(e))

class DownloadWorker(QThread):
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, manager, repo_id, filename, token=None):
        super().__init__()
        self.manager = manager
        self.repo_id = repo_id
        self.filename = filename
        self.token = token

    def run(self):
        try:
            path = self.manager.download_file(self.repo_id, self.filename, self.token)
            if path: self.finished.emit(path)
            else: self.error.emit("Download returned None (Check logs)")
        except Exception as e:
            self.error.emit(str(e))

# --- DIALOGS ---

class AskTokenDialog(QDialog):
    """
    Dialog to request HF Token for gated models.
    Supports saving to SecretsManager via Framework.
    """
    def __init__(self, repo_id, parent=None):
        super().__init__(parent)
        self.repo_id = repo_id
        self.token = None
        self.setWindowTitle(tr("dlg.token.title", "Authentication Required"))
        self.setMinimumWidth(400)
        
        layout = QVBoxLayout(self)
        
        lbl = QLabel(tr("dlg.token.info", f"Model '{repo_id}' is gated.\nPlease enter your Hugging Face Access Token."))
        lbl.setWordWrap(True)
        layout.addWidget(lbl)
        
        self.token_input = QLineEdit()
        self.token_input.setEchoMode(QLineEdit.Password)
        self.token_input.setPlaceholderText("hf_...")
        layout.addWidget(self.token_input)
        
        # Checkbox to save token
        self.chk_save = QCheckBox(tr("chk.save_token", "Save Token securely (Keyring)"))
        layout.addWidget(self.chk_save)
        
        # Try to pre-fill from secrets if available
        # Accessing secrets via parent's framework reference if possible, else skip
        self.framework = getattr(parent, 'framework_manager', None) if parent else None
        if self.framework and self.framework.secrets_manager:
            stored = self.framework.secrets_manager.get_secret("hf_token")
            if stored:
                self.token_input.setText(stored)
                self.chk_save.setChecked(True)
        
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
    def validate(self):
        t = self.token_input.text().strip()
        if not t:
            QMessageBox.warning(self, "Error", tr("dlg.token.err_empty", "Token cannot be empty!"))
            return
        
        self.token = t
        
        # Save if requested
        if self.chk_save.isChecked() and self.framework and self.framework.secrets_manager:
            self.framework.secrets_manager.set_secret("hf_token", t)
            
        self.accept()

class FileSelectionDialog(QDialog):
    def __init__(self, repo_id, files, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Browse: {repo_id}")
        self.resize(600, 450)
        self.selected_file = None
        
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("hf.files_available", "Available Files:")))
        
        self.list_widget = QListWidget()
        # Prioritize GGUF/Bin files
        priority_files = sorted([f for f in files if f.lower().endswith('.gguf') or f.lower().endswith('.bin')])
        other_files = sorted([f for f in files if f not in priority_files])
        
        # Color coding via HTML is tricky in ListWidget items directly, stick to order
        self.list_widget.addItems(priority_files + other_files)
        
        layout.addWidget(self.list_widget)
        
        btn = QPushButton(tr("hf.download_selected", "Download Selected"))
        btn.clicked.connect(self.accept_selection)
        btn.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold;")
        layout.addWidget(btn)

    def accept_selection(self):
        item = self.list_widget.currentItem()
        if item:
            self.selected_file = item.text()
            self.accept()

# --- MAIN WINDOW ---

class HuggingFaceWindow(QMainWindow):
    def __init__(self, framework_manager, parent=None):
        super().__init__(parent)
        self.framework_manager = framework_manager
        self.model_manager = framework_manager.model_manager
        self.setWindowTitle("Hugging Face Model Hub")
        self.resize(1000, 700)
        self.all_results = [] 

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # Search Bar
        hbox = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search models (e.g. 'llama-3', 'mistral', 'gemma')...")
        self.search_edit.returnPressed.connect(self.start_search)
        hbox.addWidget(self.search_edit)
        
        self.search_btn = QPushButton(tr("btn.search", "Search"))
        self.search_btn.clicked.connect(self.start_search)
        hbox.addWidget(self.search_btn)
        layout.addLayout(hbox)
        
        # Filter
        filter_box = QHBoxLayout()
        filter_box.addWidget(QLabel("Filter:"))
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["All Models", "Free Models Only", "Gated Models Only"])
        self.filter_combo.currentIndexChanged.connect(self.apply_filter)
        filter_box.addWidget(self.filter_combo)
        filter_box.addStretch()
        layout.addLayout(filter_box)

        # Table
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Model ID", "Downloads", "Likes", "Access"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.cellDoubleClicked.connect(self.on_download_request)
        self.table.setStyleSheet("QTableWidget { background-color: #1e1e1e; color: #eee; gridline-color: #333; } QHeaderView::section { background-color: #333; color: white; }")
        layout.addWidget(self.table)
        
        self.status_label = QLabel(tr("status.ready", "Ready."))
        layout.addWidget(self.status_label)

    def start_search(self):
        query = self.search_edit.text()
        if not query: return
        
        self.status_label.setText("Searching Hugging Face...")
        self.search_btn.setEnabled(False)
        self.table.setRowCount(0)
        
        self.worker = SearchWorker(self.model_manager, query)
        self.worker.finished.connect(self.on_search_finished)
        self.worker.error.connect(self.on_search_error)
        self.worker.start()

    def on_search_finished(self, results):
        self.all_results = results
        self.apply_filter()
        self.status_label.setText(f"Found {len(results)} models")
        self.search_btn.setEnabled(True)

    def on_search_error(self, err):
        self.status_label.setText(f"Error: {err}")
        self.search_btn.setEnabled(True)
        
    def apply_filter(self):
        mode = self.filter_combo.currentIndex() # 0=All, 1=Free, 2=Gated
        filtered = []
        for m in self.all_results:
            is_gated = m.get("gated", False)
            if mode == 1 and is_gated: continue
            if mode == 2 and not is_gated: continue
            filtered.append(m)
        self.populate_table(filtered)

    def populate_table(self, models):
        self.table.setRowCount(0)
        for m in models:
            row = self.table.rowCount()
            self.table.insertRow(row)
            
            self.table.setItem(row, 0, QTableWidgetItem(m["id"]))
            self.table.setItem(row, 1, QTableWidgetItem(str(m["downloads"])))
            self.table.setItem(row, 2, QTableWidgetItem(str(m["likes"])))
            
            gated = m.get("gated", False)
            status_text = "🔒 Gated" if gated else "✅ Free"
            item = QTableWidgetItem(status_text)
            if gated:
                item.setForeground(QColor("#FFA500")) # Orange
                item.setToolTip("Authentication required.")
            else:
                item.setForeground(QColor("#4CAF50")) # Green
            
            self.table.setItem(row, 3, item)

    def on_download_request(self, row, col):
        model_id = self.table.item(row, 0).text()
        access_item = self.table.item(row, 3)
        is_gated = "🔒" in access_item.text()
        
        token = None
        if is_gated:
            # AskTokenDialog (Defined locally above)
            dlg = AskTokenDialog(model_id, self)
            if dlg.exec():
                token = dlg.token
            else:
                return # Cancelled
        
        # Check license via ModelManager (Ethics Gate)
        lic_info = self.model_manager.check_license(model_id)
        if lic_info.get("is_restrictive"):
            res = QMessageBox.warning(self, "License Warning", f"{lic_info['message']}\nContinue anyway?", QMessageBox.Yes | QMessageBox.No)
            if res == QMessageBox.No: return

        # List files first
        self.status_label.setText(f"Listing files for {model_id}...")
        self.list_worker = FileListWorker(self.model_manager, model_id, token)
        self.list_worker.finished.connect(lambda files: self.show_file_selection(model_id, files, token))
        self.list_worker.error.connect(lambda e: QMessageBox.critical(self, "Error", str(e)))
        self.list_worker.start()

    def show_file_selection(self, model_id, files, token):
        self.status_label.setText("Select file to download...")
        dlg = FileSelectionDialog(model_id, files, self)
        if dlg.exec() and dlg.selected_file:
            self.download_file(model_id, dlg.selected_file, token)
        else:
            self.status_label.setText("Ready.")

    def download_file(self, model_id, filename, token):
        self.status_label.setText(f"Downloading {filename}...")
        
        self.dl_worker = DownloadWorker(self.model_manager, model_id, filename, token)
        self.dl_worker.finished.connect(lambda p: self._on_download_success(p))
        self.dl_worker.error.connect(lambda e: QMessageBox.critical(self, "Error", f"Download failed:\n{e}"))
        self.dl_worker.start()

    def _on_download_success(self, path):
        self.status_label.setText("Download complete.")
        QMessageBox.information(self, "Success", f"Model saved to:\n{path}")
