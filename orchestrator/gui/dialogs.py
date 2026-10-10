#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - GUI Dialogs (v2.3.0 Enterprise)
DIREKTIVE: Goldstandard UI Components.

Updates:
- SecretInputDialog supports Keyring integration.
- HealingDialog allows editing of AI proposals.
- LanguageSelectionDialog for first-run setup.
"""

from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QLineEdit,
    QDialogButtonBox, QMessageBox, QComboBox,
    QWidget, QFormLayout, QTextEdit, QSpinBox,
    QCheckBox, QGroupBox
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

# (display name, provider id understood by DittoCoder, keyring secret, key required, suggested models)
AI_PROVIDERS = [
    ("OpenAI", "OpenAI", "openai_api_key", True, ["gpt-6.1-sol", "gpt-6-luna", "gpt-6-astra"]),
    ("Anthropic (Claude)", "Anthropic", "anthropic_api_key", True, ["claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5-5"]),
    ("Google (Gemini)", "Google", "gemini_api_key", True, ["gemini-3.8-flash", "gemini-3.1-pro-preview"]),
    ("Ollama (Local)", "Ollama", None, False, ["qwen3.5:4b", "qwen3.5:9b", "gemma4:e4b"]),
    ("Custom Endpoint (OpenAI-compatible)", "Custom", "custom_api_key", False, []),
]
# Only these providers talk to a user-supplied URL; cloud providers keep their fixed endpoints.
ENDPOINT_PROVIDERS = ("Ollama", "Custom")

class AIConfigurationDialog(QDialog):
    """
    Configures the AI Agent (Ditto) via dropdowns.
    API keys go to the OS keyring, selections to the user config.
    """
    def __init__(self, framework=None, parent=None):
        super().__init__(parent)
        self.framework = framework
        self.i18n = get_i18n()
        self._result_config: Dict[str, Any] = {}

        self.setWindowTitle("Configure AI Agent (Ditto)")
        self.setMinimumWidth(500)

        layout = QVBoxLayout(self)

        info_text = "Configure the AI Agent for hardware analysis and module generation."
        if self.i18n:
            info_text = self.i18n.t("dlg.ai.info", info_text)
        info_lbl = QLabel(info_text)
        info_lbl.setWordWrap(True)
        layout.addWidget(info_lbl)

        form = QFormLayout()

        self.cb_provider = QComboBox()
        for display, provider_id, _, _, _ in AI_PROVIDERS:
            self.cb_provider.addItem(display, provider_id)
        self.cb_provider.currentIndexChanged.connect(self._on_provider_changed)
        form.addRow("AI Provider:", self.cb_provider)

        self.cb_model = QComboBox()
        self.cb_model.setEditable(True)
        form.addRow("Model:", self.cb_model)

        self.txt_api_key = QLineEdit()
        self.txt_api_key.setEchoMode(QLineEdit.Password)
        form.addRow("API Key:", self.txt_api_key)

        self.txt_base_url = QLineEdit()
        form.addRow("Base URL:", self.txt_base_url)

        self.chk_enable_rag = QCheckBox("Enable Local Knowledge Base (RAG, uses an online embedding API)")
        form.addRow("", self.chk_enable_rag)

        self.chk_offline = QCheckBox("Offline Mode (built-in local model, no external APIs)")
        self.chk_offline.toggled.connect(self._on_offline_toggled)
        form.addRow("", self.chk_offline)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save_config)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._load_current_config()

    # --- helpers ---
    def _config_get(self, key: str, default: Any = None) -> Any:
        cfg = getattr(self.framework, 'config', None)
        if cfg is not None and hasattr(cfg, 'get'):
            return cfg.get(key, default)
        return default

    def _provider_meta(self) -> Tuple[Optional[str], bool, List[str]]:
        provider = self.cb_provider.currentData()
        for _, provider_id, secret_key, key_required, models in AI_PROVIDERS:
            if provider_id == provider:
                return secret_key, key_required, models
        return None, False, []

    def _stored_key(self) -> Optional[str]:
        secret_key, _, _ = self._provider_meta()
        sm = getattr(self.framework, 'secrets_manager', None)
        if secret_key and sm:
            return sm.get_secret(secret_key)
        return None

    def _apply_enabled_state(self):
        secret_key, key_required, _ = self._provider_meta()
        offline = self.chk_offline.isChecked()
        provider = self.cb_provider.currentData()

        self.cb_provider.setEnabled(not offline)
        self.cb_model.setEnabled(not offline)
        self.txt_api_key.setEnabled(secret_key is not None and not offline)
        self.txt_base_url.setEnabled(provider in ENDPOINT_PROVIDERS and not offline)
        self.txt_base_url.setPlaceholderText("http://localhost:11434" if provider == "Ollama" else "https://.../v1")
        if self._stored_key():
            self.txt_api_key.setPlaceholderText("******** (Stored in Keyring)")
        else:
            self.txt_api_key.setPlaceholderText("Paste API key" if key_required else "Optional API key")

    # --- slots ---
    def _on_provider_changed(self):
        _, _, models = self._provider_meta()
        self.cb_model.clear()
        self.cb_model.addItems(models)
        self._apply_enabled_state()

    def _on_offline_toggled(self, checked: bool):
        self._apply_enabled_state()

    def _load_current_config(self):
        idx = self.cb_provider.findData(self._config_get("ai_provider", "OpenAI"))
        self.cb_provider.setCurrentIndex(max(idx, 0))
        self._on_provider_changed()

        model = self._config_get("ai_model")
        if model:
            self.cb_model.setCurrentText(model)
        self.txt_base_url.setText(self._config_get("ai_base_url", "") or "")
        self.chk_enable_rag.setChecked(bool(self._config_get("enable_rag_knowledge", False)))
        self.chk_offline.setChecked(bool(self._config_get("offline_mode", False)))
        self._apply_enabled_state()

    def _save_config(self):
        provider = self.cb_provider.currentData()
        model = self.cb_model.currentText().strip()
        api_key = self.txt_api_key.text().strip()
        offline = self.chk_offline.isChecked()
        enable_rag = self.chk_enable_rag.isChecked()
        secret_key, key_required, _ = self._provider_meta()
        # A leftover Ollama/Custom URL must never reach a cloud provider (it would receive that provider's key).
        base_url = self.txt_base_url.text().strip() if provider in ENDPOINT_PROVIDERS else ""

        if not offline:
            if key_required and not api_key and not self._stored_key():
                QMessageBox.warning(self, "Missing API Key",
                    "Please provide an API key for this provider, or enable Offline Mode.")
                return
            if provider == "Custom" and not base_url:
                QMessageBox.warning(self, "Missing Base URL", "A custom endpoint requires a Base URL.")
                return
            if base_url and (urlparse(base_url).scheme not in ("http", "https") or not urlparse(base_url).netloc):
                QMessageBox.warning(self, "Invalid Base URL", "The Base URL must start with http:// or https://.")
                return
            if not model:
                QMessageBox.warning(self, "Missing Model", "Please select or enter a model.")
                return

        if api_key and secret_key:
            sm = getattr(self.framework, 'secrets_manager', None)
            if not sm or not sm.set_secret(secret_key, api_key):
                QMessageBox.critical(self, "Error", "Failed to store API key in the OS keyring.")
                return

        cfg = getattr(self.framework, 'config', None)
        if cfg is not None and hasattr(cfg, 'set'):
            cfg.set("ai_provider", provider)
            cfg.set("ai_model", model)
            cfg.set("ai_base_url", base_url)
            cfg.set("offline_mode", offline)
            cfg.set("enable_rag_knowledge", enable_rag)
            if hasattr(cfg, 'save_user_config'):
                cfg.save_user_config()

        if enable_rag and hasattr(self.framework, 'ensure_rag_manager'):
            self.framework.ensure_rag_manager()

        ditto = getattr(self.framework, 'ditto_manager', None)
        if ditto and hasattr(ditto, 'reinitialize'):
            # "" (not None) so that a previously set URL is cleared.
            ditto.reinitialize(provider=provider, model=model, api_key=api_key or None,
                               base_url=base_url, offline_mode=offline)

        self._result_config = {
            "provider": provider,
            "model": model,
            "api_key": api_key or None,
            "base_url": base_url or None,
            "offline_mode": offline,
            "enable_rag": enable_rag,
        }
        self.accept()

    def get_config(self) -> Dict[str, Any]:
        """Selected settings after the dialog was accepted (empty otherwise)."""
        return dict(self._result_config)

class URLInputDialog(QDialog):
    """
    Dialog for inputting multiple URLs for RAG crawling.
    Supports Deep Ingest options (depth, robots.txt, etc.)
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Deep Ingest: Add Documentation URLs")
        self.resize(600, 400)

        layout = QVBoxLayout(self)

        # Info
        info = QLabel("Enter documentation URLs to crawl into the Knowledge Base.\n"
                     "One URL per line. Example: https://docs.radxa.com/")
        info.setWordWrap(True)
        layout.addWidget(info)

        # URL Input (Multi-line)
        layout.addWidget(QLabel("URLs:"))
        self.txt_urls = QTextEdit()
        self.txt_urls.setPlaceholderText("https://example.com/docs/\nhttps://wiki.example.org/")
        self.txt_urls.setMinimumHeight(150)
        layout.addWidget(self.txt_urls)

        # Options Group
        grp_options = QGroupBox("Crawl Options")
        opt_layout = QFormLayout()

        # Max Depth
        self.spin_depth = QSpinBox()
        self.spin_depth.setRange(1, 5)
        self.spin_depth.setValue(2)
        self.spin_depth.setToolTip("How many levels deep to follow links")
        opt_layout.addRow("Max Depth:", self.spin_depth)

        # Respect robots.txt
        self.chk_robots = QCheckBox("Respect robots.txt (Recommended)")
        self.chk_robots.setChecked(True)
        opt_layout.addRow("", self.chk_robots)

        # Download PDFs
        self.chk_pdfs = QCheckBox("Download and parse PDF files")
        self.chk_pdfs.setChecked(True)
        opt_layout.addRow("", self.chk_pdfs)

        # Rate Limiting
        self.spin_delay = QSpinBox()
        self.spin_delay.setRange(0, 10)
        self.spin_delay.setValue(1)
        self.spin_delay.setSuffix(" sec")
        self.spin_delay.setToolTip("Delay between requests (be polite!)")
        opt_layout.addRow("Request Delay:", self.spin_delay)

        grp_options.setLayout(opt_layout)
        layout.addWidget(grp_options)

        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def validate_and_accept(self):
        """Validate URLs before accepting."""
        urls = self.get_urls()
        if not urls:
            QMessageBox.warning(self, "No URLs", "Please enter at least one URL.")
            return

        invalid_urls = []
        for url in urls:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                invalid_urls.append(url)

        if invalid_urls:
            QMessageBox.warning(self, "Invalid URLs",
                f"The following URLs are invalid:\n" + "\n".join(invalid_urls[:5]))
            return

        self.accept()

    def get_urls(self):
        """Get list of URLs (one per line)."""
        text = self.txt_urls.toPlainText().strip()
        if not text:
            return []

        urls = [line.strip() for line in text.split('\n') if line.strip()]
        return urls

    def get_options(self):
        """Get crawl options as dict."""
        return {
            "max_depth": self.spin_depth.value(),
            "respect_robots": self.chk_robots.isChecked(),
            "download_pdfs": self.chk_pdfs.isChecked(),
            "request_delay": self.spin_delay.value()
        }
