#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - GUI Dialogs (v2.3.0 Enterprise)
DIREKTIVE: Goldstandard UI Components.

Updates:
- SecretInputDialog supports Keyring integration.
- HealingDialog allows editing of AI proposals.
- LanguageSelectionDialog for first-run setup.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QLineEdit, 
    QDialogButtonBox, QMessageBox, QComboBox, 
    QWidget, QFormLayout, QTextEdit
)
from PySide6.QtCore import Qt
from orchestrator.utils.localization import get_instance as get_i18n

class LanguageSelectionDialog(QDialog):
    """
    Initial Dialog to select the interface language.
    Does not rely on I18n system as it sets it up.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Language Selection / Sprachauswahl")
        self.setModal(True)
        self.selected_lang = "en"
        
        layout = QVBoxLayout(self)
        
        lbl = QLabel("Please select your language:\nBitte wählen Sie Ihre Sprache:")
        lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(lbl)
        
        self.cb_lang = QComboBox()
        self.cb_lang.addItem("English", "en")
        self.cb_lang.addItem("Deutsch", "de")
        layout.addWidget(self.cb_lang)
        
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
    def accept(self):
        self.selected_lang = self.cb_lang.currentData()
        super().accept()

class SecretInputDialog(QDialog):
    """
    Secure dialog to input API Keys and secrets.
    Stores directly to SecretsManager (Keyring).
    """
    def __init__(self, framework, secret_key: str, display_name: str, parent=None):
        super().__init__(parent)
        self.framework = framework
        self.secret_key = secret_key
        # Safe I18n access
        self.i18n = get_i18n() if get_i18n else None
        
        self.setWindowTitle(f"Security: {display_name}")
        self.setMinimumWidth(400)
        
        layout = QVBoxLayout(self)
        
        info_text = "This value will be encrypted and stored in your OS Keyring."
        if self.i18n:
             info_text = self.i18n.t("secrets.dialog_info", info_text)
             
        info_lbl = QLabel(info_text)
        info_lbl.setWordWrap(True)
        layout.addWidget(info_lbl)
        
        form = QFormLayout()
        self.input_field = QLineEdit()
        self.input_field.setEchoMode(QLineEdit.Password)
        self.input_field.setPlaceholderText("sk-...")
        
        # Check if secret exists (via framework -> secrets_manager)
        sm = getattr(self.framework, 'secrets_manager', None)
        if sm:
            exists = sm.get_secret(secret_key)
            if exists:
                self.input_field.setPlaceholderText("******** (Stored)")
        
        form.addRow(display_name + ":", self.input_field)
        layout.addLayout(form)
        
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save_secret)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
    def _save_secret(self):
        val = self.input_field.text().strip()
        if not val:
            QMessageBox.warning(self, "Error", "Secret cannot be empty.")
            return
            
        sm = getattr(self.framework, 'secrets_manager', None)
        if sm:
            success = sm.set_secret(self.secret_key, val)
            if success:
                QMessageBox.information(self, "Success", "Secret stored securely in Keyring.")
                self.accept()
            else:
                QMessageBox.critical(self, "Error", "Failed to store secret via SecretsManager.")
        else:
            QMessageBox.critical(self, "Error", "SecretsManager is not initialized!")

class HealingConfirmDialog(QDialog):
    """
    Dialog to review and EDIT an AI healing proposal.
    """
    def __init__(self, proposal, parent=None):
        super().__init__(parent)
        self.proposal = proposal
        self.setWindowTitle("Self-Healing Proposal")
        self.resize(500, 400)
        
        layout = QVBoxLayout(self)
        
        # Summary
        lbl_sum = QLabel(f"<b>Issue:</b> {proposal.error_summary}")
        layout.addWidget(lbl_sum)
        
        lbl_root = QLabel(f"<b>Root Cause:</b> {proposal.root_cause}")
        lbl_root.setWordWrap(True)
        layout.addWidget(lbl_root)
        
        # Command (Editable!)
        layout.addWidget(QLabel("<b>Proposed Fix (You can edit this):</b>"))
        self.txt_command = QTextEdit()
        self.txt_command.setPlainText(proposal.fix_command)
        self.txt_command.setReadOnly(False) 
        self.txt_command.setStyleSheet("background-color: #222; color: #0f0; font-family: Consolas;")
        layout.addWidget(self.txt_command)
        
        # Confidence
        conf_color = "green" if proposal.confidence_score > 0.8 else "orange"
        layout.addWidget(QLabel(f"Confidence: <span style='color:{conf_color}'>{proposal.confidence_score*100:.1f}%</span>"))
        
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_final_command(self):
        """Returns the (potentially edited) command."""
        return self.txt_command.toPlainText().strip()
