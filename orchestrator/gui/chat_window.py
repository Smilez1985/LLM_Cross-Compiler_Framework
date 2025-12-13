#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Chat Window (v2.3.0 Goldstandard)
DIREKTIVE: Echter Threading-Support (QThread) & Persistenz.

Features:
- Asynchrone AI-Antworten (Kein GUI-Freeze).
- Persistente Chat-History (JSON im Cache).
- Manuelles Löschen der History.

Updates v2.3.0:
- Safe Config access via .get().
- Improved Dark Mode styling.
"""

import json
from pathlib import Path
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QTextBrowser, QLineEdit, 
    QPushButton, QHBoxLayout, QLabel, QProgressBar, QMessageBox
)
from PySide6.QtCore import Qt, QTimer, Signal, QThread
from PySide6.QtGui import QIcon, QAction

from orchestrator.utils.localization import get_instance as get_i18n

class ChatWorker(QThread):
    """
    Führt die AI-Anfrage im Hintergrund aus, damit die GUI responsive bleibt.
    """
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, ditto, question, history):
        super().__init__()
        self.ditto = ditto
        self.question = question
        self.history = history

    def run(self):
        try:
            if not self.ditto:
                raise Exception("AI Core (Ditto) not initialized.")
            
            # Hier läuft die teure Operation (Netzwerk/Inference)
            answer = self.ditto.ask_ditto(self.question, self.history)
            self.finished.emit(answer)
        except Exception as e:
            self.error.emit(str(e))

class ChatWindow(QWidget):
    def __init__(self, framework_manager, parent=None):
        super().__init__(parent)
        self.framework = framework_manager
        
        # Access components safely
        self.ditto = getattr(framework_manager, 'ditto_manager', None)
        
        self.history = []
        self.i18n = get_i18n()
        self.worker = None
        
        # Pfad für Chat-History sicher ermitteln
        config = framework_manager.config
        get_cfg = getattr(config, 'get', lambda k, d=None: getattr(config, k, d))
        cache_dir = Path(get_cfg("cache_dir", "cache"))
        
        if not cache_dir.exists():
            cache_dir.mkdir(parents=True, exist_ok=True)
            
        self.history_file = cache_dir / "chat_history.json"
        
        self._init_ui()
        self._load_history()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        
        # Header mit Clear-Button
        header_layout = QHBoxLayout()
        title = QLabel("Ditto AI Assistant")
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        header_layout.addWidget(title)
        
        self.btn_clear = QPushButton("🗑️ Clear History")
        self.btn_clear.clicked.connect(self._confirm_clear_history)
        self.btn_clear.setFixedWidth(120)
        header_layout.addWidget(self.btn_clear, alignment=Qt.AlignRight)
        
        layout.addLayout(header_layout)
        
        # Chat Display
        self.txt_display = QTextBrowser()
        self.txt_display.setOpenExternalLinks(True)
        # Base styling for the document
        self.txt_display.setStyleSheet("background-color: #1e1e1e; border: 1px solid #333;")
        layout.addWidget(self.txt_display)
        
        # Status Bar (Thinking...)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0) # Indeterminate (Ladebalken läuft hin und her)
        self.progress.setFixedHeight(5)
        self.progress.setTextVisible(False)
        self.progress.setStyleSheet("QProgressBar::chunk { background-color: #2196F3; }")
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        
        # Input Area
        input_layout = QHBoxLayout()
        self.txt_input = QLineEdit()
        placeholder = "Ask Ditto about compilation errors..."
        if self.i18n:
             placeholder = self.i18n.t("chat.placeholder", placeholder)
        self.txt_input.setPlaceholderText(placeholder)
        self.txt_input.returnPressed.connect(self.send_message)
        
        self.btn_send = QPushButton("Send")
        self.btn_send.clicked.connect(self.send_message)
        self.btn_send.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold;")
        
        input_layout.addWidget(self.txt_input)
        input_layout.addWidget(self.btn_send)
        layout.addLayout(input_layout)

    def _add_message(self, text, is_user=False):
        color = "#aaffaa" if is_user else "#80d4ff"
        sender = "You" if is_user else "Ditto"
        align = "right" if is_user else "left"
        bg_color = "#2d2d2d" if is_user else "#252526"
        
        # Formatting text (replace newlines for HTML)
        formatted_text = text.replace("\n", "<br>")
        
        html = f"""
        <div style='text-align: {align}; margin: 10px;'>
            <span style='font-weight: bold; color: {color}; font-size: 12px;'>{sender}:</span><br>
            <div style='background-color: {bg_color}; padding: 8px; border-radius: 8px; display: inline-block; text-align: left;'>
                <span style='color: #eeeeee; font-size: 13px;'>{formatted_text}</span>
            </div>
        </div>
        """
        # Append HTML intelligently
        cursor = self.txt_display.textCursor()
        cursor.movePosition(cursor.End)
        self.txt_display.setTextCursor(cursor)
        self.txt_display.insertHtml(html)
        self.txt_display.ensureCursorVisible()
        
        # Update internal history logic
        # Note: We only add to history list if it's a new message, not during reload
        # This check is done in send_message vs load_history

    def send_message(self):
        text = self.txt_input.text().strip()
        if not text: return
        
        self.txt_input.clear()
        
        # Add to UI
        self._add_message(text, True)
        
        # Update history state
        self.history.append({"role": "user", "content": text})
        self._save_history()
        
        # UI Sperren und Ladebalken zeigen
        self._set_thinking(True)
        
        # Worker starten (Echter Thread!)
        self.worker = ChatWorker(self.ditto, text, self.history)
        self.worker.finished.connect(self._on_ai_response)
        self.worker.error.connect(self._on_ai_error)
        self.worker.start()

    def _on_ai_response(self, answer):
        self._set_thinking(False)
        self._add_message(answer, False)
        
        # Update history
        self.history.append({"role": "assistant", "content": answer})
        self._save_history()

    def _on_ai_error(self, error_msg):
        self._set_thinking(False)
        self._add_message(f"⚠️ Error: {error_msg}", False)

    def _set_thinking(self, active: bool):
        self.txt_input.setEnabled(not active)
        self.btn_send.setEnabled(not active)
        self.progress.setVisible(active)

    def _save_history(self):
        try:
            with open(self.history_file, "w", encoding='utf-8') as f:
                json.dump(self.history, f, indent=2)
        except Exception as e:
            print(f"Warning: Could not save chat history: {e}")

    def _load_history(self):
        if self.history_file.exists():
            try:
                with open(self.history_file, "r", encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self.history = [] # Reset RAM history first
                        self.txt_display.clear()
                        for msg in data:
                            content = msg.get("content", "")
                            role = msg.get("role", "user")
                            is_user = (role == "user")
                            
                            self._add_message(content, is_user)
                            # Re-populate internal list
                            self.history.append(msg)
            except Exception as e:
                print(f"Warning: Corrupt chat history: {e}")

    def _confirm_clear_history(self):
        reply = QMessageBox.question(self, "Clear History", 
                                   "Are you sure you want to delete the entire chat history?\nThis cannot be undone.",
                                   QMessageBox.Yes | QMessageBox.No)
        
        if reply == QMessageBox.Yes:
            self.history = []
            self.txt_display.clear()
            self._save_history() # Leere Datei schreiben
            self._add_message("History cleared.", False)
