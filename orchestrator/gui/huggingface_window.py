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

A6 (DECISIONS round 11): a download goes through the framework's verified fetcher, the same as an AI's
download_hf_file (ModelManager.download_file -> Core/hf_hub.py download_for_person), into
<models_dir>/hf/<owner>--<name>/<commit[:12]>/, under the one disk rule with its own reservation while it runs; the
window shows its progress and can stop it (the part downloaded so far is kept). "Take over old downloads..." copies
what earlier versions saved in huggingface_hub's cache, each file checked (Core/hf_takeover.py), and then offers to
remove the old copies only through the deletion dialog with the exact list. Every network and file step runs in a
worker thread, never on the GUI thread.
"""

import threading

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, 
    QHeaderView, QLabel, QMessageBox, QComboBox, QDialog, 
    QListWidget, QDialogButtonBox, QFormLayout, QCheckBox
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor

from orchestrator.utils.localization import current_language, ui_text
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
    """Checks the licence and lists the files of a repository: both are network calls, never on the GUI thread."""
    finished = Signal(dict, list)
    error = Signal(str)

    def __init__(self, manager, repo_id, token=None):
        super().__init__()
        self.manager = manager
        self.repo_id = repo_id
        self.token = token

    def run(self):
        try:
            licence = self.manager.check_license(self.repo_id)
            files = self.manager.list_repo_files(self.repo_id, self.token)
            self.finished.emit(licence, files)
        except Exception as e:
            self.error.emit(str(e))

class DownloadWorker(QThread):
    """One download through ModelManager.download_file (the verified fetcher). `stop` ends it between two chunks and
    keeps the part downloaded so far (`stopped`); a refusal of the disk rule arrives in the person's language."""
    finished = Signal(str)
    error = Signal(str)
    progress = Signal(object, object)   # bytes done, bytes in all (Python ints: a file can be larger than 2 GiB)
    stopped = Signal(str)

    def __init__(self, manager, repo_id, filename, token=None, lang=None):
        super().__init__()
        self.manager = manager
        self.repo_id = repo_id
        self.filename = filename
        self.token = token
        self.lang = lang or current_language()
        self.stop = threading.Event()
        self._percent = -1

    def _progress(self, _name, done, total):
        percent = int(done * 100 / total) if total else 100
        if percent != self._percent:   # at most one signal per percent, not one per chunk
            self._percent = percent
            self.progress.emit(int(done), int(total))

    def run(self):
        try:
            path = self.manager.download_file(self.repo_id, self.filename, self.token, progress=self._progress,
                                              stop=self.stop)
            if path: self.finished.emit(path)
            else: self.error.emit("Download returned None (Check logs)")
        except Exception as e:
            from orchestrator.Core.hf_hub import DownloadStopped
            if isinstance(e, DownloadStopped):
                self.stopped.emit(ui_text("hf.download.stopped", self.lang, name=self.filename))
                return
            from orchestrator.Core.disk_plan import person_reason
            self.error.emit(person_reason(e, self.lang))


class TakeOverScanWorker(QThread):
    """Lists the old download cache (hf_takeover.old_cache) and the plan of old copies that could go."""
    finished = Signal(object, object)
    error = Signal(str)

    def __init__(self, manager):
        super().__init__()
        self.manager = manager

    def run(self):
        try:
            self.finished.emit(self.manager.old_downloads(), self.manager.old_copies_plan())
        except Exception as e:
            self.error.emit(str(e))


class TakeOverWorker(QThread):
    """Copies the old downloads into the models folder, each checked (ModelManager.take_over); `stop` ends it."""
    finished = Signal(object, object)
    error = Signal(str)
    progress = Signal(int, int, str)

    def __init__(self, manager, files, lang=None):
        super().__init__()
        self.manager = manager
        self.files = list(files)
        self.lang = lang or current_language()
        self.stop = threading.Event()

    def run(self):
        try:
            result = self.manager.take_over(self.files, stop=self.stop,
                                            progress=lambda done, total, name: self.progress.emit(done, total, name))
            self.finished.emit(result, self.manager.old_copies_plan())
        except Exception as e:
            from orchestrator.Core.disk_plan import person_reason
            self.error.emit(person_reason(e, self.lang))


class RemoveOldCopiesWorker(QThread):
    """Removes exactly the old copies the person accepted in the deletion dialog (ModelManager.delete_old_copies)."""
    finished = Signal(object)
    error = Signal(str)

    def __init__(self, manager, accepted, trash, audit, lang=None):
        super().__init__()
        self.manager, self.accepted, self.trash, self.audit = manager, accepted, trash, audit
        self.lang = lang or current_language()

    def run(self):
        from orchestrator.Core.deletion import PlanChanged
        try:
            self.finished.emit(self.manager.delete_old_copies(self.accepted, trash=self.trash, audit=self.audit))
        except PlanChanged:
            self.error.emit(ui_text("deletion.changed", self.lang))
        except Exception as e:
            from orchestrator.gui.plain import one_line
            self.error.emit(ui_text("deletion.failed", self.lang, reason=one_line(str(e))))

def _running(*workers) -> bool:
    """A worker may only be replaced once its thread has ended: PySide6 aborts the whole program when a QThread
    object is destroyed while its thread still runs."""
    return any(w is not None and w.isRunning() for w in workers)

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
        self.chk_save = QCheckBox(tr("chk.save_token", "Save the token in the encrypted credential vault"))
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
        self.worker = self.list_worker = self.dl_worker = None
        self.scan_worker = self.takeover_worker = self.remove_worker = None
        self.lang = current_language()
        self._offered = 0

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
        self.takeover_btn = QPushButton(ui_text("hf.takeover.button", self.lang))
        self.takeover_btn.setObjectName("btn_hf_take_over")
        self.takeover_btn.setToolTip(ui_text("hf.takeover.tooltip", self.lang))
        self.takeover_btn.clicked.connect(self.take_over)
        filter_box.addWidget(self.takeover_btn)
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
        status_row = QHBoxLayout()
        status_row.addWidget(self.status_label, 1)
        self.stop_btn = QPushButton(ui_text("hf.stop", self.lang))
        self.stop_btn.setObjectName("btn_hf_stop")
        self.stop_btn.clicked.connect(self.stop_running)
        self.stop_btn.setVisible(False)
        status_row.addWidget(self.stop_btn)
        layout.addLayout(status_row)

    def _t(self, key, **values):
        return ui_text(key, self.lang, **values)

    def _busy(self) -> bool:
        return _running(self.list_worker, self.dl_worker, self.scan_worker, self.takeover_worker, self.remove_worker)

    def start_search(self):
        query = self.search_edit.text()
        if not query or _running(self.worker): return  # Enter while a search runs (only the button is disabled)
        
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
        if _running(self.list_worker, self.dl_worker):
            self.status_label.setText("Please wait: a file list or a download is still running.")
            return
        if self._busy():
            self.status_label.setText(self._t("hf.busy"))
            return
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
        
        # Licence check (Ethics Gate) and file list in a worker thread; the warning follows in show_file_selection
        self.status_label.setText(f"Checking the licence and listing files for {model_id}...")
        self.list_worker = FileListWorker(self.model_manager, model_id, token)
        self.list_worker.finished.connect(
            lambda licence, files: self.show_file_selection(model_id, files, token, licence))
        self.list_worker.error.connect(lambda e: QMessageBox.critical(self, "Error", str(e)))
        self.list_worker.start()

    def show_file_selection(self, model_id, files, token, licence=None):
        if licence and licence.get("is_restrictive"):
            box = QMessageBox(QMessageBox.Warning, "License Warning",
                              f"{licence.get('message', '')}\nContinue anyway?", QMessageBox.Yes | QMessageBox.No, self)
            box.setTextFormat(Qt.PlainText)  # the licence name comes from the model card
            if box.exec() != QMessageBox.Yes:
                self.status_label.setText("Ready.")
                return
        self.status_label.setText("Select file to download...")
        dlg = FileSelectionDialog(model_id, files, self)
        if dlg.exec() and dlg.selected_file:
            self.download_file(model_id, dlg.selected_file, token)
        else:
            self.status_label.setText("Ready.")

    def download_file(self, model_id, filename, token):
        self.status_label.setText(f"Downloading {filename}...")

        self.dl_worker = DownloadWorker(self.model_manager, model_id, filename, token, lang=self.lang)
        self.dl_worker.finished.connect(lambda p: self._on_download_success(p))
        self.dl_worker.error.connect(self._on_download_error)
        self.dl_worker.progress.connect(lambda done, total: self._on_download_progress(filename, done, total))
        self.dl_worker.stopped.connect(self._on_download_stopped)
        self.stop_btn.setVisible(True)
        self.stop_btn.setEnabled(True)
        self.dl_worker.start()

    def _on_download_progress(self, filename, done, total):
        from orchestrator.utils.sizes import format_size
        self.status_label.setText(self._t("hf.download.progress", name=filename, done=format_size(done, self.lang),
                                          total=format_size(total, self.lang)))

    def _on_download_error(self, text):
        self.stop_btn.setVisible(False)
        self.status_label.setText(tr("status.ready", "Ready."))
        QMessageBox.critical(self, "Error", f"Download failed:\n{text}")

    def _on_download_stopped(self, text):
        self.stop_btn.setVisible(False)
        self.status_label.setText(text)

    def _on_download_success(self, path):
        self.stop_btn.setVisible(False)
        self.status_label.setText("Download complete.")
        QMessageBox.information(self, "Success", f"Model saved to:\n{path}")

    def stop_running(self):
        """The Stop button: ends the running download or take-over between two chunks."""
        for worker, key in ((self.dl_worker, "hf.download.stopping"), (self.takeover_worker, "hf.takeover.stopping")):
            if worker is not None and worker.isRunning():
                worker.stop.set()
                self.stop_btn.setEnabled(False)
                self.status_label.setText(self._t(key))

    # --- Take over old downloads (A6) ---------------------------------------------------------------------------

    def _box(self, kind, text):
        from orchestrator.gui.plain import plain_box
        return plain_box(self, kind, self._t("hf.takeover.title"), text)

    def take_over(self):
        """Take over old downloads...: lists the old cache in a worker, asks, copies in a worker, then offers to remove
        the old copies through the deletion dialog (nothing is removed without the person's yes to that list)."""
        if self._busy():
            self.status_label.setText(self._t("hf.busy"))
            return
        self.status_label.setText(self._t("hf.takeover.scanning"))
        self.scan_worker = TakeOverScanWorker(self.model_manager)
        self.scan_worker.finished.connect(self._offer_take_over)
        self.scan_worker.error.connect(self._take_over_failed)
        self.scan_worker.start()

    def _offer_take_over(self, files, plan):
        from orchestrator.gui.plain import plain_box
        from orchestrator.utils.sizes import format_size
        new = [f for f in files if f.state == "new"]
        if not new:
            if plan is not None:
                self._offer_removal(plan)
                return
            self.status_label.setText(tr("status.ready", "Ready."))
            self._box("information", self._t("hf.takeover.none"))
            return
        shown = 15
        lines = [f"{f.repo} / {f.name} ({format_size(f.size, self.lang)})" for f in new[:shown]]
        if len(new) > shown:
            lines.append(self._t("hf.takeover.more", n=len(new) - shown))
        text = self._t("hf.takeover.ask", n=len(new), size=format_size(sum(f.size for f in new), self.lang),
                       files="\n".join(lines))
        answer = plain_box(self, "question", self._t("hf.takeover.title"), text,
                           buttons=QMessageBox.Yes | QMessageBox.No, default=QMessageBox.No)
        if answer != QMessageBox.Yes:
            self.status_label.setText(tr("status.ready", "Ready."))
            return
        self._offered = len(new)
        self.takeover_worker = TakeOverWorker(self.model_manager, new, lang=self.lang)
        self.takeover_worker.progress.connect(
            lambda done, total, name: self.status_label.setText(
                self._t("hf.takeover.running", name=name, done=done, total=total)) if name else None)
        self.takeover_worker.finished.connect(self._taken_over)
        self.takeover_worker.error.connect(self._take_over_failed)
        self.stop_btn.setVisible(True)
        self.stop_btn.setEnabled(True)
        self.takeover_worker.start()

    def _taken_over(self, result, plan):
        from orchestrator.utils.sizes import format_size
        self.stop_btn.setVisible(False)
        self.status_label.setText(tr("status.ready", "Ready."))
        lines = [self._t("hf.takeover.result", copied=len(result.copied), total=self._offered,
                         size=format_size(result.bytes_copied, self.lang))]
        if result.stopped:
            lines.append(self._t("hf.takeover.stopped"))
        lines += [self._t("hf.takeover.kept", name=f"{f.repo} / {f.name}", reason=self._t(f"hf.takeover.why.{code}"))
                  for f, code in result.skipped[:20]]
        self._box("information", "\n".join(lines))
        if plan is not None:
            self._offer_removal(plan)

    def _take_over_failed(self, text):
        self.stop_btn.setVisible(False)
        self.status_label.setText(tr("status.ready", "Ready."))
        self._box("warning", self._t("hf.takeover.failed", reason=text))

    def _offer_removal(self, plan):
        """The deletion dialog with the exact list of old copies (full path, size, what it is); "No" keeps them."""
        from orchestrator.gui.deletion_dialog import ask_deletion
        accepted = ask_deletion(self, plan, title=self._t("hf.takeover.remove_title"), lang=self.lang)
        if accepted is None:
            self.status_label.setText(tr("status.ready", "Ready."))
            return
        trash = None
        if accepted.mode == "trash":
            from orchestrator.Core.trash import Trash
            try:
                trash = Trash.for_framework(self.framework_manager)
            except Exception:  # no per-user folder: delete_old_copies refuses the trash mode then
                trash = None
        audit = getattr(getattr(self.framework_manager, "tool_registry", None), "audit", None)
        self.remove_worker = RemoveOldCopiesWorker(self.model_manager, accepted, trash, audit, lang=self.lang)
        self.remove_worker.finished.connect(lambda result: self._removed(result, accepted.mode))
        self.remove_worker.error.connect(lambda text: self._box("warning", text))
        self.remove_worker.start()

    def _removed(self, result, mode):
        from orchestrator.gui.plain import one_line
        key = "deletion.done.trash" if mode == "trash" else "deletion.done.permanent"
        lines = [self._t(key, name=one_line(n)) for n in result.done]
        lines += [self._t("deletion.kept", name=one_line(n), reason=one_line(r)) for n, r in result.failed]
        self.status_label.setText(tr("status.ready", "Ready."))
        self._box("information", "\n".join(lines))
