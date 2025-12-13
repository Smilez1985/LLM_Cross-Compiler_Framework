#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Hugging Face Browser
DIREKTIVE: Goldstandard, GUI, Internationalisierung.
"""

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, 
    QHeaderView, QLabel, QMessageBox, QComboBox, QDialog, QListWidget
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QIcon, QColor

from orchestrator.gui.dialogs import AskTokenDialog
# Localization Import
try:
    from orchestrator.utils.localization import tr
except ImportError:
    def tr(key): return key

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
            else: self.error.emit("Download returned None")
        except Exception as e:
            self.error.emit(str(e))

class FileSelectionDialog(QDialog):
    def __init__(self, repo_id, files, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{tr('btn.browse_hf')} - {repo_id}")
        self.resize(600, 450)
        self.selected_file = None
        
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("hf.files_available") if tr("hf.files_available") != "hf.files_available" else "Available Files:"))
        
        self.list_widget = QListWidget()
        # Prioritize GGUF files
        priority_files = sorted([f for f in files if f.endswith('.gguf') or f.endswith('.bin')])
        other_files = sorted([f for f in files if f not in priority_files])
        self.list_widget.addItems(priority_files + other_files)
        
        layout.addWidget(self.list_widget)
        
        btn = QPushButton(tr("hf.download_selected") if tr("hf.download_selected") != "hf.download_selected" else "Download Selected")
        btn.clicked.connect(self.accept_selection)
        layout.addWidget(btn)

    def accept_selection(self):
        item = self.list_widget.currentItem()
        if item:
            self.selected_file = item.text()
            self.accept()

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

        # Search
        hbox = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search models (e.g. 'llama-3', 'mistral')...")
        self.search_edit.returnPressed.connect(self.start_search)
        hbox.addWidget(self.search_edit)
        
        self.search_btn = QPushButton(tr("btn.search") if tr("btn.search") != "btn.search" else "Search")
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
        self.table.cellDoubleClicked.connect(self.on_download_request)
        layout.addWidget(self.table)
        
        self.status_label = QLabel(tr("status.ready"))
        layout.addWidget(self.status_label)

    def start_search(self):
        query = self.search_edit.text()
        if not query: return
        
        self.status_label.setText("Searching...")
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
        self.status_label.setText(f"{tr('status.error')}: {err}")
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
                item.setForeground(QColor("orange"))
                item.setToolTip("Authentication required. Double click to select file.")
            else:
                item.setForeground(QColor("green"))
            
            self.table.setItem(row, 3, item)

    def on_download_request(self, row, col):
        model_id = self.table.item(row, 0).text()
        access_item = self.table.item(row, 3)
        is_gated = "🔒" in access_item.text()
        
        token = None
        if is_gated:
            # AskTokenDialog from dialogs.py
            dlg = AskTokenDialog(model_id, self)
            if dlg.exec():
                token = dlg.token
            else:
                return # Cancelled
        
        # List files first
        self.status_label.setText(f"Listing files for {model_id}...")
        self.list_worker = FileListWorker(self.model_manager, model_id, token)
        self.list_worker.finished.connect(lambda files: self.show_file_selection(model_id, files, token))
        self.list_worker.error.connect(lambda e: QMessageBox.critical(self, tr("status.error"), str(e)))
        self.list_worker.start()

    def show_file_selection(self, model_id, files, token):
        dlg = FileSelectionDialog(model_id, files, self)
        if dlg.exec() and dlg.selected_file:
            self.download_file(model_id, dlg.selected_file, token)

    def download_file(self, model_id, filename, token):
        self.status_label.setText(f"Downloading {filename}...")
        
        self.dl_worker = DownloadWorker(self.model_manager, model_id, filename, token)
        self.dl_worker.finished.connect(lambda p: QMessageBox.information(self, tr("msg.success"), f"Saved to: {p}"))
        self.dl_worker.error.connect(lambda e: QMessageBox.critical(self, tr("status.error"), f"Download failed:\n{e}"))
        self.dl_worker.start()
