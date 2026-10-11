#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - GUI Dialogs (v2.3.0 Enterprise)
DIREKTIVE: Goldstandard UI Components.

Updates:
- SecretInputDialog supports Keyring integration.
- HealingDialog allows editing of AI proposals.
- LanguageSelectionDialog for first-run setup.
"""

import logging
import threading
from typing import Any, Callable, Dict, List, Optional, Sequence
from urllib.parse import urlparse

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QDialogButtonBox, QMessageBox, QComboBox, QPushButton, QSizePolicy,
    QFormLayout, QTextEdit, QSpinBox, QDoubleSpinBox,
    QCheckBox, QGroupBox, QTableWidget, QTableWidgetItem, QHeaderView, QWidget
)
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from orchestrator.llm.auth.signin import plain_http_to_remote
from orchestrator.llm.auth.spec import KEY_TYPES
from orchestrator.llm.auth.store import CredentialStore, display_origin
from orchestrator.llm.errors import ANTHROPIC_WORKSPACE_INVALID, LLMError, rejected_key_error
from orchestrator.llm.providers.anthropic import WORKSPACE_ID, takes_workspace_id
from orchestrator.utils.crawl_notice import CrawlNotice, notice_text
from orchestrator.utils.helpers import redact_secrets
from orchestrator.utils.localization import current_language, get_instance as get_i18n, ui_text

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
        
        info_text = "This value is stored in the encrypted credential vault."
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
                QMessageBox.information(self, "Success", "Secret stored in the encrypted credential vault.")
                self.accept()
            else:
                QMessageBox.critical(self, "Error", "Failed to store secret via SecretsManager.")
        else:
            QMessageBox.critical(self, "Error", "SecretsManager is not initialized!")

class HealingConfirmDialog(QDialog):
    """
    Shows a self-healing proposal and asks for the user's decision.
    Only fixed, framework-defined actions are applied; command text (from heuristics or AI) is advice
    for the user and is never executed by the framework, so it is shown read-only.
    """
    AUTOMATIC = {"config_adjustment", "docker_prune", "retry_build"}

    def __init__(self, proposal, parent=None):
        super().__init__(parent)
        self.proposal = proposal
        self.setWindowTitle("Self-Healing Proposal")
        self.resize(520, 400)
        strategy = getattr(getattr(proposal, "strategy", None), "value", "")
        self.automatic = strategy in self.AUTOMATIC and not getattr(proposal, "is_ai_generated", False)
        
        layout = QVBoxLayout(self)
        
        # Plain text only: summaries and commands may come from an LLM or a build log.
        lbl_sum = QLabel(f"Issue: {proposal.summary}")
        lbl_sum.setTextFormat(Qt.PlainText)
        lbl_sum.setWordWrap(True)
        layout.addWidget(lbl_sum)
        
        lbl_root = QLabel(f"Root cause: {proposal.root_cause}")
        lbl_root.setTextFormat(Qt.PlainText)
        lbl_root.setWordWrap(True)
        layout.addWidget(lbl_root)
        
        origin = "AI suggestion" if getattr(proposal, "is_ai_generated", False) else "Known issue"
        heading = ("What the framework will do before retrying the build:" if self.automatic
                   else "Suggested manual step (the framework does not run it):")
        layout.addWidget(QLabel(f"{origin}. {heading}"))
        self.txt_command = QTextEdit()
        self.txt_command.setPlainText(proposal.fix_command)
        self.txt_command.setReadOnly(True)
        self.txt_command.setStyleSheet("background-color: #222; color: #0f0; font-family: Consolas;")
        layout.addWidget(self.txt_command)
        
        confidence = float(getattr(proposal, "confidence", 0.0) or 0.0)
        conf_color = "green" if confidence > 0.8 else "orange"
        layout.addWidget(QLabel(f"Confidence: <span style='color:{conf_color}'>{confidence * 100:.0f}%</span>"))
        
        buttons = QDialogButtonBox()
        if self.automatic:
            buttons.addButton("Apply and retry", QDialogButtonBox.AcceptRole)
            buttons.addButton("Dismiss", QDialogButtonBox.RejectRole)
        else:
            buttons.addButton("Close", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

class SettingsOverlay:
    """The saved settings with the dialog's unsaved choices on top, for the live model list.
    Like LLMClient, it counts only values the user set: schema defaults belong to the default provider."""

    config_values = None  # makes LLMClient read every key through get()

    def __init__(self, base: Any, overrides: Dict[str, Any]):
        self._base = base
        self._overrides = overrides

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._overrides:
            return self._overrides[key]
        values = getattr(self._base, "config_values", None)
        if isinstance(values, dict):
            entry = values.get(key)
            if entry is None or getattr(getattr(entry, "source", None), "source_type", "") == "default":
                return default
            return entry.value
        return self._base.get(key, default) if self._base is not None and hasattr(self._base, "get") else default

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._base, name)


class _SecretsOverlay:
    """The secrets store, plus a key typed into the dialog but not saved yet."""

    def __init__(self, base: Any, name: Optional[str], value: str):
        self._base, self._name, self._value = base, name, value

    def get_secret(self, key: str) -> Optional[str]:
        if self._value and key == self._name:
            return self._value
        return self._base.get_secret(key) if self._base is not None else None

    # A vault that cannot be read is reported, never replaced by an environment variable (CredentialStore).
    @property
    def storage_error(self):
        return getattr(self._base, "storage_error", None)

    @property
    def load_error(self):
        return getattr(self._base, "load_error", None)


def list_provider_models(config: Any, secrets: Any, provider: str) -> List[str]:
    """Model ids from the provider's live list (raises an LLMError with a readable message on failure)."""
    from orchestrator.llm import LLMClient
    return [m.id for m in LLMClient(config, secrets).list_models(provider)]


class _ModelListRelay(QObject):
    """Hands model lists from worker threads to the GUI thread. A single instance, created in the GUI thread and
    never deleted: a worker never emits on, or frees, a Qt object that a closed dialog took with it, and the
    queued connection runs the dialog's slot in the GUI thread (or not at all once the dialog is gone)."""
    done = Signal(object, object, str)  # request token, model ids (None on failure), message


_model_list_relay: Optional[_ModelListRelay] = None


def model_list_relay() -> _ModelListRelay:
    global _model_list_relay
    if _model_list_relay is None:
        _model_list_relay = _ModelListRelay()
    return _model_list_relay


EFFORT_CHOICES = [("Provider default", ""), ("Low", "low"), ("Medium", "medium"), ("High", "high"),
                  ("Extra high", "xhigh"), ("Maximum", "max")]
SECURITY_CHOICES = [
    ("Strict: also hide host names, serial numbers, UUIDs and directories", "STRICT"),
    ("Balanced: hide IP/MAC/e-mail addresses, logins and user names", "BALANCED"),
    ("Off: only API keys and tokens are removed", "OFF"),
]


class AIConfigurationDialog(QDialog):
    """
    Configures the AI Agent (Ditto): provider (from configs/llm_providers.yml), model, effort, privacy level,
    offline mode and fallback chain. API keys go to the secrets store, selections to the user config.
    """

    # Returns model ids for (settings, secrets, provider id); replaced in tests.
    model_lister = staticmethod(list_provider_models)

    def __init__(self, framework=None, parent=None):
        super().__init__(parent)
        from orchestrator.llm import load_catalog
        self.framework = framework
        self.i18n = get_i18n()
        self._result_config: Dict[str, Any] = {}
        self._model_request = object()  # token of the model list that may still change the combo
        model_list_relay().done.connect(self._on_models_listed, Qt.QueuedConnection)
        self.catalog = load_catalog(user_dir=getattr(self._config(), "config_dir", None))

        self.setWindowTitle("Configure AI Agent (Ditto)")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)

        info_text = "Configure the AI Agent for chat, hardware analysis, module generation and build advice."
        if self.i18n:
            info_text = self.i18n.t("dlg.ai.info", info_text)
        info_lbl = QLabel(info_text)
        info_lbl.setWordWrap(True)
        layout.addWidget(info_lbl)

        form = QFormLayout()

        self.cb_provider = QComboBox()
        for preset in self.catalog.providers.values():
            self.cb_provider.addItem(preset.label, preset.id)
        self.cb_provider.currentIndexChanged.connect(self._on_provider_changed)
        form.addRow("AI Provider:", self.cb_provider)

        model_row = QHBoxLayout()
        self.cb_model = QComboBox()
        self.cb_model.setEditable(True)  # any model id works; the live list is only a suggestion
        self.cb_model.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        model_row.addWidget(self.cb_model)
        self.btn_refresh_models = QPushButton("Refresh")
        self.btn_refresh_models.setToolTip("Load the models this provider offers for your account")
        self.btn_refresh_models.clicked.connect(self._refresh_models)
        model_row.addWidget(self.btn_refresh_models)
        form.addRow("Model:", model_row)
        self.lbl_models = QLabel("")
        self.lbl_models.setWordWrap(True)
        self.lbl_models.setTextFormat(Qt.PlainText)  # may contain the provider's error text
        self.lbl_models.setStyleSheet("color: #888;")
        form.addRow("", self.lbl_models)

        key_row = QHBoxLayout()
        self.txt_api_key = QLineEdit()
        self.txt_api_key.setEchoMode(QLineEdit.Password)
        key_row.addWidget(self.txt_api_key)
        # The provider's sign-in methods as cards, with Check, Save and Sign out (orchestrator/gui/sign_in.py).
        self.btn_sign_in = QPushButton(self.i18n.t("dlg.ai.sign_in", "Sign in...") if self.i18n else "Sign in...")
        self.btn_sign_in.setEnabled(False)
        self.btn_sign_in.setToolTip(self.i18n.t("dlg.ai.sign_in_tip", "Sign in, check or sign out for this provider")
                                    if self.i18n else "Sign in, check or sign out for this provider")
        self.btn_sign_in.clicked.connect(self._sign_in)
        self._sign_in_dialog = None
        key_row.addWidget(self.btn_sign_in)
        form.addRow("API Key:", key_row)

        self.txt_workspace = QLineEdit()
        self.txt_workspace.setPlaceholderText("wrkspc_... (only for keys valid in every workspace)")
        self.txt_workspace.setToolTip("Claude Console > Settings > Workspaces. Needed when the API key belongs to "
                                      "the whole organization instead of one workspace.")
        form.addRow("Workspace ID:", self.txt_workspace)

        self.txt_base_url = QLineEdit()
        form.addRow("Base URL:", self.txt_base_url)

        self.cb_effort = QComboBox()
        for label, value in EFFORT_CHOICES:
            self.cb_effort.addItem(label, value)
        self.cb_effort.setToolTip("How much the model thinks before answering; providers without effort control "
                                  "ignore it.")
        form.addRow("Effort:", self.cb_effort)

        self.cb_security = QComboBox()
        for label, value in SECURITY_CHOICES:
            self.cb_security.addItem(label, value)
        self.cb_security.setToolTip("Personal data removed before text goes to a cloud provider (answers are "
                                    "restored locally). Local providers are never filtered.")
        form.addRow("Privacy (cloud):", self.cb_security)

        self.txt_fallback = QLineEdit()
        self.txt_fallback.setPlaceholderText("e.g. local, ollama:qwen3.5:4b")
        self.txt_fallback.setToolTip("Providers tried in order when the model declines or the provider is "
                                     "unavailable: provider or provider:model, separated by commas.")
        form.addRow("Fallback chain:", self.txt_fallback)

        self.chk_enable_rag = QCheckBox("Enable Local Knowledge Base (RAG, local embeddings; downloads a ~0.5 GB model once)")
        form.addRow("", self.chk_enable_rag)

        self.chk_offline = QCheckBox("Offline Mode (only local providers: Ollama, the managed local model, "
                                     "a custom server on your network)")
        self.chk_offline.toggled.connect(self._on_offline_toggled)
        form.addRow("", self.chk_offline)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save_config)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._load_current_config()

    # --- helpers ---
    def _config(self):
        return getattr(self.framework, 'config', None)

    def _config_get(self, key: str, default: Any = None) -> Any:
        cfg = self._config()
        if cfg is not None and hasattr(cfg, 'get'):
            return cfg.get(key, default)
        return default

    def _user_value(self, key: str, default: Any = None) -> Any:
        """A value the user set; schema defaults do not count (the default model belongs to OpenAI)."""
        return SettingsOverlay(self._config(), {}).get(key, default)

    def _preset(self):
        return self.catalog.providers.get(self.cb_provider.currentData())

    def _secrets(self):
        return getattr(self.framework, 'secrets_manager', None)

    def _key_method(self, preset):
        return next((m for m in preset.methods if m.type in KEY_TYPES), None) if preset is not None else None

    def _stored_key(self) -> Optional[str]:
        """Where the provider's key comes from ("vault", "env:<VARIABLE>"), None without one. A key bound to a
        server address counts only for the address in the dialog."""
        preset, sm = self._preset(), self._secrets()
        spec = self._key_method(preset)
        if preset is None or not preset.secret or not sm or spec is None:
            return None
        return CredentialStore(sm).key_source(preset, spec, self._base_url(preset) or preset.base_url)

    def _key_required(self, preset) -> bool:
        # Presets that also work without a key (local servers) do not need one, nor does a provider whose other
        # sign-in method the user chose (ai_auth_<provider>); opt-in methods never stand in for the key on their own.
        if not preset.secret:
            return False
        chosen = self._user_value(f"ai_auth_{preset.id}")
        if chosen and chosen != "api_key":
            return False
        return all(method == "api_key" or method in preset.opt_in_auth for method in preset.auth)

    def _base_url(self, preset) -> str:
        return self.txt_base_url.text().strip() if preset is not None and preset.base_url_configurable else ""

    def _workspace_id(self, preset) -> str:
        return self.txt_workspace.text().strip() if preset is not None and takes_workspace_id(preset) else ""

    def _apply_enabled_state(self):
        preset = self._preset()
        if preset is None:
            return
        self.txt_api_key.setEnabled(bool(preset.secret))
        self.txt_workspace.setEnabled(takes_workspace_id(preset))
        self.txt_base_url.setEnabled(preset.base_url_configurable)
        self.txt_base_url.setPlaceholderText(preset.base_url or "https://.../v1")
        self.btn_sign_in.setEnabled(self._sign_in_available(preset))
        self.btn_refresh_models.setEnabled(preset.live_models)
        source = self._stored_key() if preset.secret else None
        if not preset.secret:
            self.txt_api_key.setPlaceholderText("Not needed for this provider")
        elif source and source.startswith("env:"):
            self.txt_api_key.setPlaceholderText(f"Using the key from the variable {source[4:]}")
        elif source:
            self.txt_api_key.setPlaceholderText("******** (Stored in the vault)")
        else:
            self.txt_api_key.setPlaceholderText("Paste API key" if self._key_required(preset) else "Optional API key")

    def _fill_models(self, models: List[str], keep: str = ""):
        current = keep or self.cb_model.currentText().strip()
        self.cb_model.blockSignals(True)
        self.cb_model.clear()
        for model in models:
            if model and self.cb_model.findText(model) < 0:
                self.cb_model.addItem(model)
        self.cb_model.setCurrentText(current or (models[0] if models else ""))
        self.cb_model.blockSignals(False)

    @staticmethod
    def _parse_fallback(text: str) -> List[str]:
        return [part.strip() for part in text.split(",") if part.strip()]

    # --- sign-in ---
    def _sign_in_available(self, preset) -> bool:
        """The sign-in panel needs the provider's methods, the settings and the credential vault."""
        return (preset is not None and bool(preset.methods) and self._config() is not None
                and self._secrets() is not None)

    def _sign_in(self):
        """Opens the sign-in panel of the selected provider (for the address typed here); returns the window."""
        from orchestrator.gui.sign_in import SignInDialog
        preset = self._preset()
        if not self._sign_in_available(preset):
            return None
        if self._sign_in_dialog is not None:
            self._sign_in_dialog.close()
        dlg = SignInDialog(self.framework, preset, base_url=self._base_url(preset) or None, parent=self)
        dlg.panel.changed.connect(self._on_signed_in)
        self._sign_in_dialog = dlg
        dlg.open()  # window-modal; the settings stay responsive to its changes
        return dlg

    def _on_signed_in(self):
        """A key, method or storage changed in the sign-in panel: show it here and take it into effect."""
        preset = self._preset()
        if preset is not None and takes_workspace_id(preset):
            self.txt_workspace.setText(self._user_value("ai_anthropic_workspace_id") or self.txt_workspace.text())
        self._apply_enabled_state()
        self._refresh_models()
        ditto = getattr(self.framework, 'ditto_manager', None)
        if ditto is not None and hasattr(ditto, 'reload'):
            ditto.reload()

    # --- live model list ---
    def _refresh_models(self):
        """Loads the provider's model list in a worker thread; on failure the free text stays as it is."""
        preset = self._preset()
        request = self._model_request = object()
        if preset is None or not preset.live_models:
            self.lbl_models.setText("")
            return
        overrides = {"ai_provider": preset.id, "ai_model": self.cb_model.currentText().strip() or preset.id,
                     "offline_mode": self.chk_offline.isChecked()}
        if self._base_url(preset):
            overrides["ai_base_url"] = self._base_url(preset)
        if takes_workspace_id(preset):
            overrides["ai_anthropic_workspace_id"] = self._workspace_id(preset)  # also when cleared
        config = SettingsOverlay(self._config(), overrides)
        secrets = _SecretsOverlay(self._secrets(), preset.secret, self.txt_api_key.text().strip())
        lister, relay = self.model_lister, model_list_relay()
        self.lbl_models.setText("Loading the provider's models...")

        def work():  # touches no Qt object of the dialog; the relay lives as long as the application
            try:
                ids, message = list(lister(config, secrets, preset.id)), ""
            except Exception as e:  # LLMError text is already redacted and readable
                text = redact_secrets(str(e)).strip()
                ids, message = None, (text.splitlines()[0][:300] if text else type(e).__name__)
            relay.done.emit(request, ids, message)

        threading.Thread(target=work, name="ai-model-list", daemon=True).start()

    def _on_models_listed(self, request: object, ids, message: str):
        if request is not self._model_request:
            return  # another dialog's list, or the provider changed meanwhile
        if ids is None:
            self.lbl_models.setText(f"Live model list unavailable ({message}). Type the model id.")
            return
        if not ids:
            self.lbl_models.setText("The provider listed no models. Type the model id.")
            return
        self._fill_models(ids)
        self.lbl_models.setText(f"{len(ids)} models available.")

    # --- slots ---
    def _on_provider_changed(self):
        preset = self._preset()
        if preset is None:
            return
        saved = self.catalog.resolve_id(self._user_value("ai_provider")) == preset.id
        suggestions = [m for m in [*preset.models.values(), *preset.suggested_models] if m]
        keep = (self._user_value("ai_model") if saved else None) or preset.model_for("chat") or ""
        self.cb_model.clear()
        self._fill_models(suggestions, keep=keep)
        own_url = self._user_value(f"ai_base_url_{preset.id}") if preset.base_url_configurable else None
        self.txt_base_url.setText(own_url or ((self._user_value("ai_base_url") or "")
                                              if saved and preset.base_url_configurable else ""))
        self.txt_workspace.setText((self._user_value("ai_anthropic_workspace_id") or "")
                                   if takes_workspace_id(preset) else "")
        self.txt_api_key.clear()
        self.lbl_models.setText("")
        self._apply_enabled_state()
        self._refresh_models()

    def _on_offline_toggled(self, checked: bool):
        self._apply_enabled_state()

    def _load_current_config(self):
        saved_provider = self._config_get("ai_provider", "OpenAI")
        pid = self.catalog.resolve_id(saved_provider) or "openai"
        self.chk_offline.setChecked(bool(self._config_get("offline_mode", False)))
        self.cb_provider.blockSignals(True)
        self.cb_provider.setCurrentIndex(max(self.cb_provider.findData(pid), 0))
        self.cb_provider.blockSignals(False)
        self.chk_enable_rag.setChecked(bool(self._config_get("enable_rag_knowledge", False)))
        self.cb_effort.setCurrentIndex(max(self.cb_effort.findData(self._user_value("ai_effort") or ""), 0))
        security = str(self._config_get("ai_security_level", "STRICT") or "STRICT").upper()
        self.cb_security.setCurrentIndex(max(self.cb_security.findData(security), 0))
        chain = self._user_value("ai_fallback_chain") or []
        self.txt_fallback.setText(", ".join(chain) if isinstance(chain, list) else str(chain))
        self._on_provider_changed()
        removed = self._removed_message([saved_provider, *self._parse_fallback(self.txt_fallback.text())])
        if removed:  # a saved provider no longer exists: say why and what to use instead, once per load
            QMessageBox.information(self, "AI Provider Removed", removed)

    def _removed_message(self, entries: List[str]) -> Optional[str]:
        """The migration text for the first removed provider among `entries` (provider or provider:model)."""
        for entry in entries:
            removed = self.catalog.removed_message(str(entry).partition(":")[0])
            if removed:
                return removed
        return None

    def _save_config(self):
        preset = self._preset()
        if preset is None:
            return
        model = self.cb_model.currentText().strip()
        api_key = self.txt_api_key.text().strip()
        offline = self.chk_offline.isChecked()
        enable_rag = self.chk_enable_rag.isChecked()
        # A leftover Ollama/custom URL must never reach a cloud provider (it would receive that provider's key).
        base_url = self._base_url(preset)
        fallback = self._parse_fallback(self.txt_fallback.text())

        unknown = [entry for entry in fallback if self.catalog.get(entry.partition(":")[0]) is None]
        if unknown:
            removed = self._removed_message(unknown)
            QMessageBox.warning(self, "Invalid Fallback Chain",
                                f"Unknown provider in the fallback chain: {', '.join(unknown)}.\n"
                                f"Known providers: {', '.join(self.catalog.ids())}."
                                + (f"\n\n{removed}" if removed else ""))
            return
        if offline and preset.location == "cloud":
            QMessageBox.warning(self, "Offline Mode",
                                f"{preset.label} is a cloud provider. In offline mode choose a local provider "
                                "(Ollama, the managed local model, or a custom server on your network).")
            return
        if self._key_required(preset) and not api_key and not self._stored_key():
            QMessageBox.warning(self, "Missing API Key", "Please provide an API key for this provider.")
            return
        if preset.rejected_key(api_key):
            QMessageBox.warning(self, "Unsuitable API Key", str(rejected_key_error(preset)))
            return
        workspace = self._workspace_id(preset)
        if workspace and not WORKSPACE_ID.match(workspace):
            QMessageBox.warning(self, "Invalid Workspace ID", ANTHROPIC_WORKSPACE_INVALID)
            return
        if preset.base_url_configurable and not preset.base_url and not base_url:
            QMessageBox.warning(self, "Missing Base URL", "A custom endpoint requires a Base URL.")
            return
        if base_url and (urlparse(base_url).scheme not in ("http", "https") or not urlparse(base_url).netloc):
            QMessageBox.warning(self, "Invalid Base URL", "The Base URL must start with http:// or https://.")
            return
        if not model and not preset.runtime_endpoint:
            QMessageBox.warning(self, "Missing Model", "Please select or enter a model.")
            return

        spec = self._key_method(preset)
        key_url = base_url or preset.base_url
        if api_key and preset.secret and plain_http_to_remote(key_url) and not self._confirm_plain_http(key_url):
            return
        if api_key and preset.secret and spec is not None:
            # A key of a server whose address can be changed is stored for that address only.
            try:
                CredentialStore(self._secrets()).save_api_key(preset, spec, api_key,
                                                              base_url=base_url or preset.base_url)
            except (LLMError, ValueError) as e:
                QMessageBox.critical(self, "Error", f"Failed to store the API key: {e}")
                return
        elif api_key and preset.secret:  # a user preset with a key name but no key method
            sm = self._secrets()
            if not sm or not sm.set_secret(preset.secret, api_key):
                QMessageBox.critical(self, "Error", "Failed to store the API key in the encrypted credential vault.")
                return

        effort = self.cb_effort.currentData() or ""
        security = self.cb_security.currentData() or "STRICT"
        cfg = self._config()
        if cfg is not None and hasattr(cfg, 'set'):
            cfg.set("ai_provider", preset.id)
            cfg.set("ai_model", model)
            cfg.set("ai_base_url", base_url)
            if self._user_value(f"ai_base_url_{preset.id}"):
                cfg.set(f"ai_base_url_{preset.id}", base_url)  # set by `llm-cli ditto login`; it wins over ai_base_url
            chosen = self._user_value(f"ai_auth_{preset.id}")
            if api_key and spec is not None and chosen and getattr(preset.method(chosen), "id", None) != spec.id:
                # A key typed here is meant to be used: the method remembered by a sign-in (an `ant` profile, no
                # key) would otherwise keep it unused without a word.
                cfg.set(f"ai_auth_{preset.id}", spec.id)
            if takes_workspace_id(preset):
                cfg.set("ai_anthropic_workspace_id", workspace)
            cfg.set("ai_effort", effort)
            cfg.set("ai_security_level", security)
            cfg.set("ai_fallback_chain", fallback)
            cfg.set("offline_mode", offline)
            cfg.set("enable_rag_knowledge", enable_rag)
            if hasattr(cfg, 'save_user_config') and not cfg.save_user_config():
                QMessageBox.warning(self, "Settings Not Saved", "The AI settings apply until the program ends, but "
                                    "they could not be saved to config.yml. See the log.")

        if enable_rag and hasattr(self.framework, 'ensure_rag_manager'):
            self.framework.ensure_rag_manager()

        ditto = getattr(self.framework, 'ditto_manager', None)
        if ditto is not None and hasattr(ditto, 'reload'):
            ditto.reload()  # a new key or catalog takes effect on the next question

        self._result_config = {
            "provider": preset.id,
            "provider_label": preset.label,
            "model": model,
            "base_url": base_url or None,
            "effort": effort or None,
            "security_level": security,
            "fallback_chain": fallback,
            "offline_mode": offline,
            "enable_rag": enable_rag,
        }
        self.accept()

    def _confirm_plain_http(self, url: str) -> bool:
        """A key for a server reached over plain http:// on another computer travels unencrypted: only on a Yes."""
        return QMessageBox.question(
            self, "Unencrypted connection",
            f"{display_origin(url)} uses plain http://, so the key travels unencrypted to another computer. Save "
            "it for this address anyway?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def get_config(self) -> Dict[str, Any]:
        """Selected settings after the dialog was accepted (empty otherwise); never contains a key."""
        return dict(self._result_config)


def fill_recent(combo: QComboBox, values: Sequence[str], text: Optional[str] = None) -> None:
    """Refills an editable combo with values (recent inputs first, duplicates dropped). The edit text becomes
    text, or the first value when text is None."""
    values = list(dict.fromkeys(v for v in values if v))
    if text is None:
        text = values[0] if values else ""
    combo.blockSignals(True)
    try:
        combo.clear()
        combo.addItems(values)
        idx = combo.findText(text)
        combo.setCurrentIndex(idx)
        if idx < 0:
            combo.setEditText(text)
    finally:
        combo.blockSignals(False)


def select_recent(combo: QComboBox, recent: Sequence[str]) -> bool:
    """Selects the most recent value a fixed list still offers (by item data, else by text)."""
    for value in recent:
        idx = combo.findData(value)
        idx = idx if idx >= 0 else combo.findText(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)
            return True
    return False


class URLInputDialog(QDialog):
    """
    Dialog for inputting multiple URLs for RAG crawling.
    Supports Deep Ingest options (depth, PDFs, delay), preset from the settings crawler_max_depth,
    crawler_download_pdfs and crawler_rate_limit of config. There is no switch for robots.txt or the other
    opt-outs: the site's machine-readable reservations decide, not the user.
    recent: URLs crawled before (newest first), offered in an editable list.
    """
    def __init__(self, parent=None, recent: Sequence[str] = (), config: Any = None):
        super().__init__(parent)
        setting = config.get if config is not None else (lambda key, default: default)
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

        # Recent URLs: pick (or edit) one and add it to the list
        row_recent = QHBoxLayout()
        self.lbl_recent = QLabel("&Recent:")
        self.cb_recent = QComboBox()
        self.cb_recent.setEditable(True)
        self.cb_recent.setInsertPolicy(QComboBox.NoInsert)
        self.cb_recent.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.cb_recent.setAccessibleName("Recent URLs")
        fill_recent(self.cb_recent, recent, text="")
        self.lbl_recent.setBuddy(self.cb_recent)
        self.btn_add_recent = QPushButton("&Add")
        self.btn_add_recent.setAutoDefault(False)
        self.btn_add_recent.clicked.connect(self.add_recent_url)
        row_recent.addWidget(self.lbl_recent)
        row_recent.addWidget(self.cb_recent)
        row_recent.addWidget(self.btn_add_recent)
        self.recent_row = QWidget()
        self.recent_row.setLayout(row_recent)
        self.recent_row.setVisible(bool(recent))
        layout.addWidget(self.recent_row)

        # Options Group
        grp_options = QGroupBox("Crawl Options")
        opt_layout = QFormLayout()

        # A larger configured value widens the range: the dialog never cuts a setting down (e.g. a longer delay).
        # A QSpinBox holds a C int; a larger depth would make the dialog fail to open.
        depth = min(int(setting("crawler_max_depth", 2)), 2**31 - 1)
        delay = float(setting("crawler_rate_limit", 1.0))
        self.spin_depth = QSpinBox()
        self.spin_depth.setRange(0, max(5, depth))
        self.spin_depth.setValue(depth)
        self.spin_depth.setToolTip("How many levels deep to follow links (0: only the entered pages)")
        opt_layout.addRow("Max Depth:", self.spin_depth)

        self.chk_pdfs = QCheckBox("Download and parse PDF files")
        self.chk_pdfs.setChecked(bool(setting("crawler_download_pdfs", True)))
        opt_layout.addRow("", self.chk_pdfs)

        self.spin_delay = QDoubleSpinBox()
        self.spin_delay.setRange(0, max(10.0, delay))
        self.spin_delay.setDecimals(2)
        self.spin_delay.setSingleStep(0.5)
        self.spin_delay.setValue(delay)
        self.spin_delay.setSuffix(" s")
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

    def add_recent_url(self):
        """Adds the URL picked (or edited) in the recent list, once."""
        url = self.cb_recent.currentText().strip()
        urls = self.get_urls()
        if url and url not in urls:
            self.txt_urls.setPlainText("\n".join(urls + [url]))

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
            "download_pdfs": self.chk_pdfs.isChecked(),
            "request_delay": self.spin_delay.value()
        }


class CrawlNoticeDialog(QMessageBox):
    """
    The notice before the first Deep Ingest. It is information, not a legal confirmation (the site's
    machine-readable opt-outs decide what may be mined), so it has one "Understood" button and no checkbox.
    The spoken hint starts only once the notice is on screen.
    """

    def __init__(self, parent=None, announcer: Optional[Callable[[str], Any]] = None, lang: Optional[str] = None):
        super().__init__(parent)
        lang = lang or current_language()
        title, text = notice_text(lang)
        self.setIcon(QMessageBox.Information)
        self.setWindowTitle(title)
        self.setTextFormat(Qt.PlainText)
        self.setText(text)
        self.setAccessibleName(title)
        self.setAccessibleDescription(text)
        self.understood = self.addButton(ui_text("crawl.notice.ok", lang), QMessageBox.AcceptRole)
        self.setDefaultButton(self.understood)
        self.setEscapeButton(self.understood)
        self._announcer = announcer
        self._spoken = ui_text("crawl.notice.spoken", lang)
        self.announced = False

    def showEvent(self, event):
        super().showEvent(event)
        if self._announcer and not self.announced:
            self.announced = True
            QTimer.singleShot(0, lambda: self._announcer(self._spoken))


def show_crawl_notice_once(parent, config, announcer: Optional[Callable[[str], Any]] = None) -> None:
    """Shows the notice before the user's first crawl, and again after its text changed (every time when the
    per-user state directory cannot be used)."""
    try:
        notice = CrawlNotice(config.user_state_dir)
        if not notice.needed():
            return
    except OSError as e:
        notice = None
        logging.getLogger(__name__).warning(f"No per-user state directory for the Deep Ingest notice ({e}).")
    CrawlNoticeDialog(parent, announcer).exec()
    try:
        if notice is not None:
            notice.acknowledge()
    except OSError as e:
        logging.getLogger(__name__).warning(f"Could not remember the Deep Ingest notice ({e}); it is shown again.")


class ToolPermissionsDialog(QDialog):
    """
    Ditto setup: what Ditto and connected AI clients (MCP) may do on their own.
    Asked once at the first start (and from the Tools menu); stored per user.
    """
    def __init__(self, registry, parent=None, first_run: bool = False):
        super().__init__(parent)
        from orchestrator.tools.policy import Decision, TIER_LABELS, ToolTier
        self._Decision, self._ToolTier = Decision, ToolTier
        self.registry = registry
        policy = registry.policy()
        self.setWindowTitle("Ditto setup - tool permissions" if first_run else "Tool permissions")
        self.resize(720, 520)
        layout = QVBoxLayout(self)

        intro = QLabel(
            "Ditto and AI clients connected over MCP (e.g. Claude Desktop) use the framework through tools.\n"
            "Decide once what they may do on their own, what needs your confirmation and what is never allowed. "
            "Tools that reach other devices or delete data always ask; you can deny them, never allow them unasked. "
            "Tools marked new use their own default until you save here.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        tier_box = QGroupBox("Per category")
        form = QFormLayout(tier_box)
        self.tier_combos = {}
        for tier, label in TIER_LABELS.items():
            combo = QComboBox()
            choices = [Decision.ASK, Decision.DENY] if tier == ToolTier.EXTERNAL else [Decision.ALLOW, Decision.ASK, Decision.DENY]
            for choice in choices:
                combo.addItem(choice.value.capitalize(), choice.value)
            idx = combo.findData(policy.tiers.get(tier, Decision.ASK).value)
            combo.setCurrentIndex(idx if idx >= 0 else combo.findData(Decision.ASK.value))
            form.addRow(label + ":", combo)
            self.tier_combos[tier] = combo
        layout.addWidget(tier_box)

        layout.addWidget(QLabel("Per tool (optional, overrides the category):"))
        tools = registry.list()
        facts = {row["tool"]: row for row in registry.permissions("")}
        self.table = QTableWidget(len(tools), 5)
        self.table.setHorizontalHeaderLabels(["Tool", "Category", "Default / max / standing approval", "New",
                                              "Permission"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.override_combos = {}
        for row, spec in enumerate(tools):
            name_item = QTableWidgetItem(spec.title)
            name_item.setToolTip(spec.description)
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, QTableWidgetItem(spec.tier.value))
            fact = facts.get(spec.name, {})
            grant = "standing approval possible" if fact.get("standing_approval") else "no standing approval"
            self.table.setItem(row, 2, QTableWidgetItem(f"{fact.get('default', '')} / {fact.get('max', '')} / {grant}"))
            self.table.setItem(row, 3, QTableWidgetItem("new" if fact.get("new") else ""))
            combo = QComboBox()
            combo.addItem("Category default", "")
            for choice in (Decision.ALLOW, Decision.ASK, Decision.DENY):
                combo.addItem(choice.value.capitalize(), choice.value)
            current = policy.overrides.get(spec.name)
            combo.setCurrentIndex(combo.findData(current.value) if current else 0)
            self.table.setCellWidget(row, 4, combo)
            self.override_combos[spec.name] = combo
        layout.addWidget(self.table)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def build_policy(self):
        """The shared permissions as set here; every listed tool counts as reviewed, and callers with permissions
        of their own (llm-cli tools permissions --caller) keep them."""
        tiers = {tier: self._Decision(combo.currentData()) for tier, combo in self.tier_combos.items()}
        overrides = {name: self._Decision(combo.currentData())
                     for name, combo in self.override_combos.items() if combo.currentData()}
        return self.registry.policy().model_copy(update={
            "configured": True, "tiers": tiers, "overrides": overrides,
            "reviewed": sorted(self.override_combos)})

    def save(self):
        try:
            self.registry.policy_store.save(self.build_policy())
        except Exception as e:
            QMessageBox.critical(self, "Tool permissions", f"Could not save the permissions:\n{e}")
            return
        self.accept()


def confirm_host_key(parent, framework, host: str, port: int = 22) -> bool:
    """Trust on first use, never silently: shows an unknown device's host key fingerprint and stores it
    only if the user confirms; a changed key is refused."""
    from pathlib import Path
    from orchestrator.utils import ssh_trust
    state_dir = Path(getattr(getattr(framework, "config", None), "user_state_dir", None) or Path.home() / ".llm-framework")
    try:
        key = ssh_trust.fetch_host_key(host, port)
        status = ssh_trust.check_host_key(state_dir, host, port, key)
    except Exception as e:
        QMessageBox.critical(parent, "SSH", f"Could not reach {host}:{port}: {e}")
        return False
    if status == "known":
        return True
    if status == "changed":
        QMessageBox.critical(parent, "SSH host key changed", str(ssh_trust.HostKeyChanged(host, port)))
        return False
    answer = QMessageBox.question(
        parent, "New device",
        f"First connection to {host}:{port}.\n\nKey type: {key.get_name()}\nFingerprint: {ssh_trust.fingerprint(key)}\n\n"
        "Compare it with the output of\n  ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub\non the device. Trust this device?",
        QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
    if answer != QMessageBox.Yes:
        return False
    ssh_trust.trust_host_key(state_dir, host, port, key)
    return True
