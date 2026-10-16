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
from PySide6.QtCore import Qt, QTimer
from orchestrator.Core.self_healing_manager import AUTOMATIC_STRATEGIES
from orchestrator.gui.plain import ask, button_text, one_line, plain_box, plain_label, plain_tooltip
from orchestrator.gui.worker_relay import worker_relay
from orchestrator.llm.auth.signin import plain_http_to_remote
from orchestrator.llm.auth.spec import KEY_TYPES
from orchestrator.llm.auth.store import CredentialStore, display_origin
from orchestrator.llm.errors import ANTHROPIC_WORKSPACE_INVALID, LLMError, rejected_key_error
from orchestrator.llm.providers.anthropic import WORKSPACE_ID, takes_workspace_id
from orchestrator.utils.crawl_notice import CrawlNotice, acknowledge_and_audit, notice_text
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
    AUTOMATIC = AUTOMATIC_STRATEGIES  # none of them deletes anything; a full Docker disk is a manual step

    def __init__(self, proposal, parent=None, free_docker_space=None):
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
        # A full Docker disk: the one deletion path for the framework's images (the person ticks what goes).
        self.btn_free_docker = None
        if strategy == "docker_prune" and callable(free_docker_space):
            from orchestrator.utils.localization import ui_text
            self.btn_free_docker = buttons.addButton(ui_text("healing.free_docker").replace("&", "&&"),
                                                     QDialogButtonBox.ActionRole)
            self.btn_free_docker.setAutoDefault(False)
            self.btn_free_docker.clicked.connect(lambda: free_docker_space())
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
        # Model lists come back on the application's one worker relay (gui/worker_relay.py), queued to the GUI thread.
        worker_relay().done.connect(self._on_relay, Qt.QueuedConnection)
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
        # Ditto is optional and experimental (eighth round, Q-2): shown in its setup every time.
        self.lbl_experimental = QLabel(ui_text("chat.ditto.setup"))
        self.lbl_experimental.setTextFormat(Qt.PlainText)
        self.lbl_experimental.setWordWrap(True)
        self.lbl_experimental.setStyleSheet("color: #1e1e1e; background-color: #ffd27f; padding: 4px;")
        layout.addWidget(self.lbl_experimental)

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
        layout.addWidget(self._runtime_mode_section())

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save_config)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._load_current_config()
        self.gui_form = self._bind_gui_form(layout)

    def _runtime_mode_section(self) -> QGroupBox:
        """Ditto's local server: bare metal or in Docker, with the switch (gui/runtime_mode_box.py)."""
        from orchestrator.gui.runtime_mode_box import RuntimeModeBox
        self.runtime_mode_box = RuntimeModeBox(self.framework, self)
        return self.runtime_mode_box

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
        lister, relay = self.model_lister, worker_relay()
        self.lbl_models.setText("Loading the provider's models...")

        def work():  # touches no Qt object of the dialog; the relay lives as long as the application
            try:
                ids, message = list(lister(config, secrets, preset.id)), ""
            except Exception as e:  # LLMError text is already redacted and readable
                text = redact_secrets(str(e)).strip()
                ids, message = None, (text.splitlines()[0][:300] if text else type(e).__name__)
            relay.done.emit(request, (ids, message))

        threading.Thread(target=work, name="ai-model-list", daemon=True).start()

    def _on_relay(self, token: object, result) -> None:
        """Every worker result passes the relay: only this dialog's latest model list is taken."""
        if token is self._model_request and isinstance(result, tuple) and len(result) == 2:
            self._on_models_listed(token, *result)

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
        if cfg is not None and hasattr(cfg, 'set') and hasattr(cfg, 'save_user_config') and not cfg.save_user_config():
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

    def _bind_gui_form(self, layout: QVBoxLayout):
        """The dialog's fields for Ditto's GUI tools (orchestrator/gui/form_surface.py). Nothing is saved until the
        person presses Save. An AI never reads or fills a key, and never fills what decides where data goes or widens
        what an AI may do (provider, address, privacy, fallback chain); the knowledge base only off, offline mode only
        on (the direction rule of update_settings)."""
        from orchestrator.gui.form_surface import FormBinding, person_audit
        from orchestrator.tools.tools_status import direction_problem
        b = FormBinding("ai_settings", "gui.form.ai_settings")

        def person_only(why_key: str) -> Callable[[], str]:
            return lambda: ui_text(why_key, "en")

        def key_set() -> bool:
            try:
                return bool(self.txt_api_key.text().strip() or self._stored_key())
            except Exception:  # noqa: BLE001 - unknown counts as not set
                return False

        b.combo("provider", "gui.field.ai.provider", self.cb_provider, read_only=person_only("gui.ai.where_data"))
        b.combo("model", "gui.field.ai.model", self.cb_model, by_data=False, hint="a model id of this provider")
        b.add("api_key", "gui.field.ai.api_key", "secret", key_set, None, [self.txt_api_key])
        b.add("workspace_id", "gui.field.ai.workspace", "secret", lambda: bool(self.txt_workspace.text().strip()),
              None, [self.txt_workspace])
        b.add("base_url", "gui.field.ai.base_url", "text",
              lambda: "set" if self.txt_base_url.text().strip() else "not set", None, [self.txt_base_url],
              read_only=person_only("gui.ai.where_data"))
        b.combo("effort", "gui.field.ai.effort", self.cb_effort)
        b.combo("privacy", "gui.field.ai.privacy", self.cb_security, read_only=person_only("gui.ai.privacy"))
        b.line("fallback", "gui.field.ai.fallback", self.txt_fallback, read_only=person_only("gui.ai.where_data"))
        b.check("enable_rag", "gui.field.ai.enable_rag", self.chk_enable_rag,
                check=lambda old, new: direction_problem("enable_rag_knowledge", old, new) or "")
        b.check("offline", "gui.field.ai.offline", self.chk_offline,
                check=lambda old, new: direction_problem("offline_mode", old, new) or "")

        def show():
            self.show()
            self.raise_()
            self.activateWindow()

        form = b.finish(self, show=show, is_open=self.isVisible)
        b.close_with(self)
        layout.insertWidget(max(0, layout.count() - 1),
                            b.banner(self, audit=person_audit(getattr(self.framework, "tool_registry", None))))
        b.refresh()
        self.gui_binding = b
        return form


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
    suggested: documentation links of the source catalogue for this hardware, filled in for the person to check.
    """
    def __init__(self, parent=None, recent: Sequence[str] = (), config: Any = None, suggested: Sequence[str] = ()):
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
        if suggested:  # nothing is crawled until the person presses OK; they may remove any link
            self.txt_urls.setPlainText("\n".join(dict.fromkeys(suggested)))
            note = QLabel(ui_text("gui.ingest.suggested", n=len(set(suggested))))
            note.setTextFormat(Qt.PlainText)
            note.setWordWrap(True)
            layout.addWidget(note)

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


def show_crawl_notice_once(parent, config, announcer: Optional[Callable[[str], Any]] = None,
                           registry=None) -> None:
    """Shows the notice before the user's first crawl, and again after its text changed (every time when the
    per-user state directory cannot be used). The acknowledgement records that a person read it in the GUI, and
    goes into the audit log (actor gui) when the tool registry is given: AI clients may crawl (crawl_docs_site)
    only after it."""
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
            problem = acknowledge_and_audit(notice, "gui", getattr(registry, "audit", None))
            if problem:
                logging.getLogger(__name__).warning(f"The Deep Ingest notice was not audited ({problem}).")
    except OSError as e:
        logging.getLogger(__name__).warning(f"Could not remember the Deep Ingest notice ({e}); it is shown again.")


def audit_person(registry, event: str, arguments: dict) -> Optional[str]:
    """A person's change in the GUI to who may call the tools, in the audit log (actor gui). Names only, never a
    token. Returns why it could not be recorded, or None."""
    from orchestrator.tools.mcp_tokens import person_change
    return person_change(registry, "gui", event, arguments)


class TokenShownOnceDialog(QDialog):
    """Shows a new or rotated MCP token once (the framework keeps only its hash); it is never logged or stored."""

    def __init__(self, name: str, token: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"MCP token for '{name}'")
        layout = QVBoxLayout(self)
        text = QLabel(f"The token for '{name}' is shown only now; the framework keeps only its hash.\n"
                      "Over HTTP the client sends it as 'Authorization: Bearer <token>'. Over stdio, put it into the "
                      "client's server entry as the environment variable LCF_MCP_TOKEN.")
        text.setTextFormat(Qt.PlainText)
        text.setWordWrap(True)
        layout.addWidget(text)
        self.field = QLineEdit(token)
        self.field.setReadOnly(True)
        self.field.selectAll()
        layout.addWidget(self.field)
        row = QHBoxLayout()
        copy = QPushButton("Copy")
        copy.clicked.connect(self.copy)
        row.addWidget(copy)
        row.addStretch()
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        row.addWidget(close)
        layout.addLayout(row)

    def copy(self) -> None:
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setText(self.field.text())


class TokenListMixin:
    """
    The named MCP tokens of a window (Tool Permissions and Security > AI App Connections (MCP)...): list, Add (read
    only, a copy of the local stdio clients', Ditto's or another token's own permissions, or linked to Ditto's),
    Rotate, Revoke (the token, its permissions and its standing approvals, as `llm-cli mcp token revoke`), Back to read
    only (`tools permissions --reset --token`) and Permissions.... A token is shown once, right after it is made or
    rotated; the list shows names only. Every change goes through orchestrator.tools.mcp_tokens (the CLI's functions)
    and is audited with actor gui. The window provides `registry` and may override `_tokens_changed()`. Every text is
    English or German (l10n_tools_mcp), as the window's own language.
    """
    START_WITH = (("read", "Read only (recommended for a new client)"),
                  ("mcp", "A copy of the local stdio clients' permissions"),
                  ("ditto", "A copy of Ditto's permissions"),
                  ("link", "Ditto's permissions by reference (a change to Ditto's applies at once)"))
    TOKEN_COLUMNS = ("name", "actor", "permissions", "created", "rotated")

    def _lang(self) -> str:
        return getattr(self, "lang", None) or current_language()

    def _tt(self, key: str, **values) -> str:
        return ui_text(key, self._lang(), **values)

    def _token_section(self) -> QWidget:
        box = QGroupBox(self._tt("tmcp.tokens.box"))
        layout = QVBoxLayout(box)
        layout.addWidget(plain_label(self._tt("tmcp.tokens.note")))
        self.token_table = QTableWidget(0, len(self.TOKEN_COLUMNS))
        self.token_table.setObjectName("table_mcp_tokens")
        self.token_table.setHorizontalHeaderLabels([self._tt(f"tmcp.tokens.col.{c}") for c in self.TOKEN_COLUMNS])
        self.token_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.token_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.token_table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.token_table)
        row = QHBoxLayout()
        self.token_buttons = {}
        for key, label, action in (("add", self._tt("tmcp.tokens.add"), self._add_token),
                                   ("rotate", self._tt("tmcp.tokens.rotate"), self._rotate_selected),
                                   ("revoke", self._tt("tmcp.tokens.revoke"), self._revoke_selected),
                                   ("reset", self._tt("tmcp.token.reset"), self._reset_selected_token),
                                   ("permissions", self._tt("tmcp.tokens.permissions"), self._token_permissions)):
            button = QPushButton(button_text(label))
            button.setObjectName(f"btn_tokens_{key}")
            button.setAutoDefault(False)
            button.clicked.connect(action)
            row.addWidget(button)
            self.token_buttons[key] = button
        row.addStretch()
        layout.addLayout(row)
        self.refresh_tokens()
        return box

    def _tokens(self):
        tokens = getattr(self.registry, "tokens", None)
        if tokens is None:
            raise RuntimeError("The MCP token store is not available.")
        return tokens

    def refresh_tokens(self) -> None:
        from orchestrator.tools.mcp_tokens import policy_summary
        self.token_store_problem = None   # why the store cannot be read: shown in the list instead of tokens
        try:
            records = self._tokens().list()
        except Exception as e:  # a damaged or unreadable store is shown, not hidden
            self.token_store_problem = str(e)
            self.token_table.setRowCount(1)
            self.token_table.setItem(0, 0, QTableWidgetItem(self._tt("tmcp.tokens.unusable",
                                                                     reason=one_line(str(e)))))
            return
        self.token_table.setRowCount(len(records))
        for i, record in enumerate(records):
            summary = self._tt("tmcp.tokens.linked_to", link=record.link) if record.link \
                else policy_summary(record.policy)
            for col, text in enumerate((record.name, f"mcp:{record.name}", summary, record.created,
                                        record.rotated or "-")):
                self.token_table.setItem(i, col, QTableWidgetItem(text))

    def _tokens_changed(self) -> None:
        self.refresh_tokens()

    def _selected_token(self) -> Optional[str]:
        if getattr(self, "token_store_problem", None):
            return None
        row = self.token_table.currentRow()
        item = self.token_table.item(row, 0) if row >= 0 else None
        return item.text() if item is not None else None

    def _show_token(self, name: str, token: str) -> None:
        TokenShownOnceDialog(name, token, self).exec()

    def _audit_warning(self, problem: Optional[str]) -> None:
        if problem:
            plain_box(self, "warning", self._tt("tmcp.token.title"),
                      self._tt("tmcp.audit_problem", reason=one_line(problem)))

    def start_choices(self) -> List[tuple]:
        """What a new token's permissions can start as: the fixed choices, then a copy of each token's own."""
        choices = [(key, self._tt(f"tmcp.tokens.start.{key}")) for key, _english in self.START_WITH]
        try:
            names = [record.name for record in self._tokens().list()]
        except Exception:  # noqa: BLE001 - an unusable store offers the fixed choices only
            names = []
        return choices + [(f"mcp:{name}", self._tt("tmcp.token.start.copy", name=name)) for name in names]

    def add_token(self, name: str, start_with: str = "read") -> bool:
        """Creates a token (start_with: read, mcp, ditto, link, or mcp:<token> for a copy of that token's own
        permissions, as `llm-cli mcp token add --policy-from`), shows it once and audits the change."""
        from orchestrator.tools import mcp_tokens
        copy = start_with in ("mcp", "ditto") or start_with.startswith(mcp_tokens.ACTOR_PREFIX)
        try:
            token, problem = mcp_tokens.add_token(self.registry, name, "gui", policy_from=start_with if copy else None,
                                                  link="ditto" if start_with == "link" else None, via="gui")
        except Exception as e:
            QMessageBox.warning(self, self._tt("tmcp.token.title"), str(e))
            return False
        self._tokens_changed()
        self._show_token(name, token)
        self._audit_warning(problem)
        return True

    def _add_token(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        title = self._tt("tmcp.tokens.add.title")
        name, ok = QInputDialog.getText(self, title, self._tt("tmcp.tokens.add.name"))
        if not ok or not name.strip():
            return
        choices = self.start_choices()
        labels = [label for _, label in choices]
        choice, ok = QInputDialog.getItem(self, title, self._tt("tmcp.tokens.add.start"), labels, 0, False)
        if ok and choice in labels:
            self.add_token(name.strip(), choices[labels.index(choice)][0])

    def rotate_token(self, name: str) -> bool:
        from orchestrator.tools.mcp_tokens import person_change
        try:
            token = self._tokens().rotate(name)
        except Exception as e:
            QMessageBox.warning(self, self._tt("tmcp.token.title"), str(e))
            return False
        problem = person_change(self.registry, "gui", "mcp_token.rotate", {"token": name, "via": "gui"})
        self._tokens_changed()
        self._show_token(name, token)
        self._audit_warning(problem)
        return True

    def revoke_token(self, name: str) -> bool:
        """Revokes the token completely: the token, its permissions and its standing approvals (mcp:<name>), in one
        core function with the CLI (mcp_tokens.revoke_token); audited with actor gui."""
        from orchestrator.tools import mcp_tokens
        try:
            result = mcp_tokens.revoke_token(self.registry, name, "gui", via="gui")
        except Exception as e:
            QMessageBox.warning(self, self._tt("tmcp.token.title"), str(e))
            return False
        self._tokens_changed()
        lang = self._lang()
        if result.grants_problem:
            plain_box(self, "warning", ui_text("tmcp.token.title", lang),
                      ui_text("tmcp.token.revoked.grants_failed", lang, name=one_line(name),
                              reason=one_line(result.grants_problem)))
        elif result.grants_removed:
            plain_box(self, "information", ui_text("tmcp.token.title", lang),
                      ui_text("tmcp.token.revoked.grants", lang, name=one_line(name), n=result.grants_removed))
        self._audit_warning(result.audit_problem)
        return True

    def reset_token(self, name: str) -> bool:
        """`tools permissions --reset --token NAME`: after the person's yes ("No" is the default), the token is a new
        token again (read only, not configured), not one with hand-set Allow/Deny."""
        from orchestrator.tools import mcp_tokens
        lang = self._lang()
        title = ui_text("tmcp.token.title", lang)
        try:
            record = self._tokens().get(name)
            if record is None:
                raise mcp_tokens.TokenStoreError(f"There is no token named '{name}'.")
        except Exception as e:  # noqa: BLE001 - shown as plain text
            plain_box(self, "warning", title, ui_text("tmcp.perm.failed", lang, reason=one_line(str(e))))
            return False
        if record.link is None and record.policy == mcp_tokens.read_only_policy():
            plain_box(self, "information", title, ui_text("tmcp.token.reset.nothing", lang, name=one_line(name)))
            return False
        before = (ui_text("tmcp.token.linked", lang, link=record.link) if record.link
                  else mcp_tokens.policy_summary(record.policy))
        if not ask(self, title, ui_text("tmcp.token.reset.question", lang, name=one_line(name), before=before)):
            return False
        try:
            problem = mcp_tokens.reset_token_policy(self.registry, name, "gui", via="gui")
        except Exception as e:  # noqa: BLE001
            plain_box(self, "warning", title, ui_text("tmcp.perm.failed", lang, reason=one_line(str(e))))
            return False
        self._tokens_changed()
        plain_box(self, "information", title, ui_text("tmcp.token.reset.done", lang, name=one_line(name)))
        self._audit_warning(problem)
        return True

    def _rotate_selected(self) -> None:
        name = self._selected_token()
        if name and ask(self, self._tt("tmcp.token.title"), self._tt("tmcp.tokens.rotate.question",
                                                                      name=one_line(name))):
            self.rotate_token(name)

    def _revoke_selected(self) -> None:
        name = self._selected_token()
        if not name:
            return
        grants = sum(1 for row in self.registry.grant_rows(f"mcp:{name}"))
        lang = self._lang()
        if ask(self, ui_text("tmcp.token.title", lang),
               ui_text("tmcp.token.revoke.question", lang, name=one_line(name), n=grants)):
            self.revoke_token(name)

    def _reset_selected_token(self) -> None:
        name = self._selected_token()
        if name:
            self.reset_token(name)

    def _token_permissions(self) -> None:
        name = self._selected_token()
        if not name:
            return
        try:
            record = self._tokens().get(name)
        except Exception as e:  # noqa: BLE001 - an unusable store: said in plain text, nothing opens
            plain_box(self, "warning", self._tt("tmcp.token.title"), self._tt("tmcp.tokens.unusable",
                                                                               reason=one_line(str(e))))
            return
        if record is not None and record.link and not ask(
                self, self._tt("tmcp.token.title"), self._tt("tmcp.tokens.link.question", name=one_line(name),
                                                              link=record.link)):
            return
        if ToolPermissionsDialog(self.registry, self, token=name, lang=self._lang()).exec():
            self._tokens_changed()


CALLER_CHOICES = ((None, "tmcp.perm.whose.shared"), ("ditto", "tmcp.perm.whose.ditto"),
                  ("mcp", "tmcp.perm.whose.mcp"))


class ToolPermissionsDialog(QDialog, TokenListMixin):
    """
    Ditto setup: what Ditto and connected AI clients (MCP) may do on their own.
    Asked once at the first start (and from the Tools menu); stored per user. "Whose permissions" chooses the shared
    permissions or the own section of Ditto or the local stdio MCP clients (`llm-cli tools permissions --caller`); the
    per-tool table shows what each tool's decision is now and why (`tools list`). Resets: the recommended defaults
    (`--reset`), a caller back to the shared permissions (`--reset --caller`), a token back to read only (`--reset
    --token`), each after a question that lists what changes ("No" is the default). The named MCP tokens are managed
    here as well (TokenListMixin); with `token` the dialog edits that token's own permissions.
    """

    def __init__(self, registry, parent=None, first_run: bool = False, token: Optional[str] = None,
                 caller: Optional[str] = None, lang: Optional[str] = None):
        super().__init__(parent)
        from orchestrator.tools.policy import Decision, TIER_LABELS, ToolTier
        self._Decision, self._ToolTier = Decision, ToolTier
        self.registry = registry
        self.token = token
        self.lang = lang or current_language()
        self.caller = None if token else (caller if caller in ("ditto", "mcp") else None)
        # What this window shows and changes: saving the shared permissions refuses when they changed meanwhile
        # (another window or process), so a change made elsewhere is never undone silently.
        self.loaded = registry.policy(f"mcp:{token}") if token else registry.policy()
        self.setWindowTitle(f"Tool permissions - MCP token '{token}'" if token else
                            "Ditto setup - tool permissions" if first_run else "Tool permissions")
        self.resize(760, 620 if token else 820)
        layout = QVBoxLayout(self)

        intro = QLabel(
            (f"What the client with the MCP token '{token}' (audit actor mcp:{token}) may do on its own. A new token "
             "could only read; what you save here are its own permissions.\n" if token else
             "Ditto and AI clients connected over MCP (e.g. Claude Desktop) use the framework through tools.\n")
            + "Decide once what they may do on their own, what needs your confirmation and what is never allowed. "
            "Tools that reach other devices or delete data always ask; you can deny them, never allow them unasked. "
            "Tools marked new use their own default until you save here.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        problem = getattr(registry.policy_store, "problem", None)
        if problem:  # e.g. not signed or edited by hand: everything is denied until saved here
            warning = QLabel(problem)
            warning.setWordWrap(True)
            warning.setStyleSheet("color: #b00020;")
            layout.addWidget(warning)

        if not token:
            whose = QHBoxLayout()
            whose.addWidget(plain_label(self._t("tmcp.perm.whose"), wrap=False, selectable=False))
            self.caller_combo = QComboBox()
            self.caller_combo.setObjectName("combo_permissions_caller")
            for value, key in CALLER_CHOICES:
                self.caller_combo.addItem(self._t(key), value)
            self.caller_combo.setCurrentIndex(self.caller_combo.findData(self.caller))
            self.caller_combo.currentIndexChanged.connect(self._caller_chosen)
            whose.addWidget(self.caller_combo, 1)
            self.btn_reset_caller = QPushButton(button_text(self._t("tmcp.perm.reset_caller")))
            self.btn_reset_caller.setObjectName("btn_permissions_reset_caller")
            self.btn_reset_caller.setAutoDefault(False)
            self.btn_reset_caller.clicked.connect(lambda: self.reset_caller(self.caller))
            whose.addWidget(self.btn_reset_caller)
            layout.addLayout(whose)
            self.caller_note = plain_label("")
            layout.addWidget(self.caller_note)

        tier_box = QGroupBox("Per category")
        form = QFormLayout(tier_box)
        self.tier_combos = {}
        for tier, label in TIER_LABELS.items():
            combo = QComboBox()
            choices = [Decision.ASK, Decision.DENY] if tier == ToolTier.EXTERNAL else [Decision.ALLOW, Decision.ASK, Decision.DENY]
            for choice in choices:
                combo.addItem(choice.value.capitalize(), choice.value)
            form.addRow(label + ":", combo)
            self.tier_combos[tier] = combo
        layout.addWidget(tier_box)

        layout.addWidget(QLabel("Per tool (optional, overrides the category):"))
        tools = registry.list()
        self.table = QTableWidget(len(tools), 7)
        self.table.setHorizontalHeaderLabels(["Tool", "Category", "Default / max / standing approval", "New",
                                              "Permission", "Ask in the framework",
                                              self._t("tmcp.perm.col.effective")])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.override_combos = {}
        self.framework_checks = {}  # questions always shown here (approval window), never in the client's dialog
        for row, spec in enumerate(tools):
            name_item = QTableWidgetItem(spec.title)
            name_item.setToolTip(plain_tooltip(spec.description))
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, QTableWidgetItem(spec.tier.value))
            combo = QComboBox()
            combo.addItem("Category default", "")
            for choice in (Decision.ALLOW, Decision.ASK, Decision.DENY):
                combo.addItem(choice.value.capitalize(), choice.value)
            self.table.setCellWidget(row, 4, combo)
            self.override_combos[spec.name] = combo
            check = QCheckBox()
            check.setToolTip(plain_tooltip("Always put this tool's questions to you in the framework's approval "
                                           "window, never in the AI client's own dialog"))
            self.table.setCellWidget(row, 5, check)
            self.framework_checks[spec.name] = check
        layout.addWidget(self.table)
        self.callers_note = plain_label("")
        layout.addWidget(self.callers_note)
        if not token:
            layout.addWidget(self._token_section())

        # Standing approvals ("do not ask again"): only created in the framework's approval windows, revoked here.
        layout.addWidget(QLabel("Standing approvals (\"do not ask again\"), with their uses from the audit log:"))
        self.grant_table = QTableWidget(0, 5)
        self.grant_table.setHorizontalHeaderLabels(["Id", "Caller", "Covers", "State", "Uses / last use"])
        self.grant_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.grant_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.grant_table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.grant_table)
        revoke = QPushButton("Revoke selected standing approval")
        revoke.clicked.connect(lambda: self.revoke_grant(self._selected_grant()))
        layout.addWidget(revoke)
        self.refresh_grants()

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        reset_key = "tmcp.token.reset" if token else "tmcp.perm.reset"
        self.btn_reset = buttons.addButton(button_text(self._t(reset_key)), QDialogButtonBox.ResetRole)
        self.btn_reset.setObjectName("btn_permissions_reset_token" if token else "btn_permissions_reset")
        self.btn_reset.setAutoDefault(False)
        self.btn_reset.clicked.connect(lambda: self.reset_token(token) if token else self.reset_shared())
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._fill()

    def _t(self, key: str, **values) -> str:
        return ui_text(key, self.lang, **values)

    def _who(self, caller: Optional[str]) -> str:
        return self._t(dict(CALLER_CHOICES)[caller])

    def _actor(self) -> str:
        return f"mcp:{self.token}" if self.token else (self.caller or "")

    def _section(self):
        """The permissions shown: a token's own, the shared ones, or the caller's own section (created from the shared
        ones when it has none, as a save would)."""
        from orchestrator.tools.policy import caller_section
        if self.caller:
            return caller_section(self.loaded, self.caller)
        return self.loaded

    def _fill(self) -> None:
        """Shows the chosen section in the combos and check boxes, and per tool what is decided now and why (the
        stored permissions, as `llm-cli tools list [--caller]` prints them)."""
        section = self._section()
        Decision = self._Decision
        for tier, combo in self.tier_combos.items():
            idx = combo.findData(section.tiers.get(tier, Decision.ASK).value)
            combo.setCurrentIndex(idx if idx >= 0 else combo.findData(Decision.ASK.value))
        for name, combo in self.override_combos.items():
            current = section.overrides.get(name)
            combo.setCurrentIndex(combo.findData(current.value) if current else 0)
        for name, check in self.framework_checks.items():
            check.setChecked(name in section.framework_only)
        facts = {row["tool"]: row for row in self.registry.permissions(self._actor())}
        for row, spec in enumerate(self.registry.list()):
            fact = facts.get(spec.name, {})
            grant = "standing approval possible" if fact.get("standing_approval") else "no standing approval"
            self.table.setItem(row, 2, QTableWidgetItem(f"{fact.get('default', '')} / {fact.get('max', '')} / {grant}"))
            self.table.setItem(row, 3, QTableWidgetItem("new" if fact.get("new") else ""))
            decision = fact.get("decision", "")
            effective = QTableWidgetItem(self._t(f"tmcp.decision.{decision}") if decision else "")
            effective.setData(Qt.UserRole, decision)
            effective.setToolTip(plain_tooltip(fact.get("why", "")))
            self.table.setItem(row, 6, effective)
        if not self.token:
            own = self.caller in self.loaded.callers
            who = self._who(self.caller)
            self.caller_note.setText("" if not self.caller else
                                     self._t("tmcp.perm.own" if own else "tmcp.perm.uses_shared", who=who))
            self.caller_note.setVisible(bool(self.caller))
            self.btn_reset_caller.setVisible(bool(self.caller))
            self.btn_reset_caller.setEnabled(bool(self.caller) and (own or bool(self.loaded.grants.get(self.caller))))
            self.btn_reset.setVisible(not self.caller)
        names = [self._who(name) if name in ("ditto", "mcp") else name for name in sorted(self.loaded.callers)]
        self.callers_note.setText(self._t("tmcp.perm.callers_note", names=", ".join(names))
                                  if names and not self.token and not self.caller else "")
        self.callers_note.setVisible(bool(self.callers_note.text()))
        self._shown = self._values()

    def _values(self):
        return ({tier: combo.currentData() for tier, combo in self.tier_combos.items()},
                {name: combo.currentData() for name, combo in self.override_combos.items()},
                {name for name, check in self.framework_checks.items() if check.isChecked()})

    def effective(self, tool: str) -> str:
        """The decision shown in the "Effective now" column for `tool` (allow, ask or deny)."""
        row = [spec.name for spec in self.registry.list()].index(tool)
        return self.table.item(row, 6).data(Qt.UserRole)

    def set_caller(self, caller: Optional[str]) -> bool:
        """Shows the permissions of `caller` (None: the shared ones). Unsaved changes are kept only on the person's
        "No" to discarding them; returns whether the view changed."""
        if caller == self.caller:
            return True
        if self._values() != self._shown and not ask(self, self.windowTitle(),
                                                     self._t("tmcp.perm.discard", who=self._who(self.caller))):
            return False
        self.caller = caller
        self._fill()
        return True

    def _caller_chosen(self, _index: int) -> None:
        wanted = self.caller_combo.currentData()
        if not self.set_caller(wanted):
            self.caller_combo.blockSignals(True)
            self.caller_combo.setCurrentIndex(self.caller_combo.findData(self.caller))
            self.caller_combo.blockSignals(False)

    def _reload(self) -> None:
        self.loaded = self.registry.policy(f"mcp:{self.token}") if self.token else self.registry.policy()
        self._fill()
        self.refresh_grants()

    def refresh_grants(self):
        from orchestrator.tools.approvals import caller_label, tool_title
        rows = self.registry.grant_rows(f"mcp:{self.token}" if self.token else None)
        self.grant_table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            grant = row["grant"]
            uses = f"{row['uses']}" + (f", last {row['last_use']}" if row["last_use"] else "")
            texts = (grant.id, caller_label(row["caller"]), f"{tool_title(self.registry, grant.tool)}: {grant.summary}",
                     row["state"], uses)
            for col, text in enumerate(texts):
                self.grant_table.setItem(i, col, QTableWidgetItem(text))

    def _selected_grant(self):
        row = self.grant_table.currentRow()
        item = self.grant_table.item(row, 0) if row >= 0 else None
        return item.text() if item is not None else ""

    def _carry_on_after_grants(self) -> None:
        """Carry on from the new revision only when nothing but standing approvals changed since this window opened;
        otherwise its save stays based on what it showed and is refused, so a change made elsewhere is not undone.
        (A token's own permissions live in the token store; revoking changes only the shared file.)"""
        fresh = self.registry.policy()
        ignored = {"revision", "updated_at", "grants"}
        if not self.token and fresh.model_dump(exclude=ignored) == self.loaded.model_dump(exclude=ignored):
            self.loaded = fresh

    def revoke_grant(self, grant_id: str) -> bool:
        """Revokes at once (audited); the window's other settings are then saved on top of the new state."""
        if not grant_id:
            return False
        try:
            revoked = self.registry.revoke_grant(grant_id, "gui")
        except Exception as e:
            QMessageBox.critical(self, "Tool permissions", f"Could not revoke the standing approval:\n{e}")
            return False
        self._carry_on_after_grants()
        self.refresh_grants()
        return revoked

    def _tokens_changed(self) -> None:
        """A token's revoke also removes its standing approvals from the shared file: the list of standing approvals
        and this window's base follow."""
        if hasattr(self, "token_table"):   # a token's own permissions have no token list
            self.refresh_tokens()
        self._carry_on_after_grants()
        self.refresh_grants()

    def build_policy(self):
        """The permissions as set here; every listed tool counts as reviewed. For the shared permissions, callers
        with permissions of their own keep them; for Ditto or the local MCP clients, their own section (created from
        the shared permissions); for a token, they become its own permissions."""
        from orchestrator.tools.policy import with_section
        tiers = {tier: self._Decision(combo.currentData()) for tier, combo in self.tier_combos.items()}
        overrides = {name: self._Decision(combo.currentData())
                     for name, combo in self.override_combos.items() if combo.currentData()}
        changed = with_section(self.loaded, self.caller, tiers=tiers, overrides=overrides,
                               reviewed=sorted(self.override_combos),
                               framework_only=sorted(n for n, check in self.framework_checks.items()
                                                     if check.isChecked()))
        return changed.model_copy(update={"configured": True})

    def save(self):
        try:
            if self.token:
                from orchestrator.tools.mcp_tokens import set_token_policy
                problem = set_token_policy(self.registry, self.token, self.build_policy(), "gui", via="gui")
                if problem:
                    QMessageBox.warning(self, "Tool permissions", f"Saved, but the audit log could not record it: "
                                                                  f"{problem}")
            else:
                self.registry.policy_store.save(self.build_policy(), actor="gui")
        except Exception as e:
            QMessageBox.critical(self, "Tool permissions", f"Could not save the permissions:\n{e}")
            return
        self.accept()

    # --- resets: `llm-cli tools permissions --reset [--caller C | --token NAME]` --------------------------------------

    def _reset(self, caller: Optional[str]) -> bool:
        from orchestrator.tools.policy import PolicyConflict, reset_changes, reset_policy
        title = self.windowTitle()
        changes = reset_changes(self.registry.policy(), caller)
        if not changes:
            plain_box(self, "information", title, self._t("tmcp.perm.reset.nothing"))
            return False
        listed = "\n".join(f"- {line}" for line in changes)
        question = (self._t("tmcp.perm.reset_caller.question", who=self._who(caller), changes=listed) if caller
                    else self._t("tmcp.perm.reset.question", changes=listed))
        if not ask(self, title, question):
            return False

        def change(current):  # exactly the change the person was shown, or nothing
            if reset_changes(current, caller) != changes:
                raise PolicyConflict("changed meanwhile")
            return reset_policy(current, caller)
        try:
            self.registry.policy_store.update(change, actor="gui")
        except PolicyConflict:
            plain_box(self, "warning", title, self._t("tmcp.perm.changed"))
            self._reload()
            return False
        except Exception as e:  # noqa: BLE001 - e.g. the audit log cannot record it: nothing changed
            plain_box(self, "warning", title, self._t("tmcp.perm.failed", reason=one_line(redact_secrets(str(e)))))
            return False
        plain_box(self, "information", title, self._t("tmcp.perm.reset_caller.done", who=self._who(caller)) if caller
                  else self._t("tmcp.perm.reset.done"))
        self._reload()
        return True

    def reset_shared(self) -> bool:
        """`--reset`: the recommended defaults; every caller's own section and every standing approval go."""
        return self._reset(None)

    def reset_caller(self, caller: Optional[str]) -> bool:
        """`--reset --caller C`: that caller uses the shared permissions again."""
        return bool(caller) and self._reset(caller)

    def reset_token(self, name: str) -> bool:
        done = TokenListMixin.reset_token(self, name)
        if done and self.token:
            self._reload()
        return done


class AIFoldersDialog(QDialog):
    """
    "Folders the AI may use": besides the models and datasets folders, the folders whose model and dataset files
    Ditto and MCP clients may name as <label>:<relative path>. Only a person adds or removes them, here or with
    `llm-cli tools folders`; every change is audited with actor `gui`.
    """
    def __init__(self, framework, registry=None, parent=None):
        super().__init__(parent)
        from orchestrator.tools.local_files import LocalFiles
        self.registry = registry
        self.files = LocalFiles.for_framework(framework)
        self.setWindowTitle("Folders the AI may use")
        self.resize(760, 420)
        layout = QVBoxLayout(self)
        intro = QLabel("Ditto and AI clients connected over MCP never name a path: they use a model or dataset file "
                       "as <label>:<relative path> inside one of these folders. Keys, credentials and the "
                       "framework's own folder are refused even inside them.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Label", "Kind", "Folder", "Status"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        self.add_button = QPushButton("Add folder...")
        self.add_button.setObjectName("btn_ai_folders_add")
        self.add_button.clicked.connect(self._choose_folder)
        self.remove_button = QPushButton("Remove")
        self.remove_button.setObjectName("btn_ai_folders_remove")
        self.remove_button.clicked.connect(self._remove_selected)
        row.addWidget(self.add_button)
        row.addWidget(self.remove_button)
        row.addStretch()
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        row.addWidget(close)
        layout.addLayout(row)
        self.refresh()

    def refresh(self) -> None:
        folders = self.files.folders()
        self.table.setRowCount(len(folders))
        for i, folder in enumerate(folders):
            status = folder.problem or ("built-in (setting)" if folder.builtin else "added")
            for col, text in enumerate((folder.label, folder.kind, str(folder.path), status)):
                self.table.setItem(i, col, QTableWidgetItem(text))

    KINDS = ("any", "model", "dataset")   # `llm-cli tools folders add --kind`; AI clients are shown it

    def _choose_folder(self) -> None:
        from PySide6.QtWidgets import QFileDialog, QInputDialog
        path = QFileDialog.getExistingDirectory(self, "Folder the AI may use")
        if not path:
            return
        label, ok = QInputDialog.getText(self, "Label", "Short name AI clients use for this folder (lowercase):")
        if not ok or not label:
            return
        lang = current_language()
        labels = [ui_text(f"tmcp.folders.kind.{kind}", lang) for kind in self.KINDS]
        choice, ok = QInputDialog.getItem(self, ui_text("tmcp.folders.kind.title", lang),
                                          ui_text("tmcp.folders.kind.question", lang), labels, 0, False)
        if ok and choice in labels:
            self.add_folder(path, label.strip(), self.KINDS[labels.index(choice)])

    def add_folder(self, path: str, label: str, kind: str = "any") -> bool:
        """Checks the folder, shows what sharing it means, and adds it on "Yes" (audited)."""
        from pathlib import Path
        from orchestrator.tools.local_files import FOLDER_WARNING, LocalFileError, record_folder_change
        store = self.files.store
        try:
            real = store.check_new(Path(path), self.files.data_folders())
        except LocalFileError as e:
            QMessageBox.warning(self, "Folders the AI may use", str(e))
            return False
        answer = QMessageBox.question(
            self, "Folders the AI may use",
            f"Folder: {real}\nLabel: {label}\nKind: {kind}\n\n{FOLDER_WARNING}\n\nLet AI clients and Ditto use the "
            "files in this folder?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return False
        try:
            entry = store.add(Path(path), label, kind, self.files.data_folders(), expected_real=real)
        except LocalFileError as e:
            QMessageBox.warning(self, "Folders the AI may use", str(e))
            return False
        try:
            record_folder_change(getattr(self.registry, "audit", None), "gui", "added", entry)
        except Exception as e:  # what widens an AI's reach is recorded, or it does not happen
            store.remove(label)
            QMessageBox.critical(self, "Folders the AI may use",
                                 f"The audit log could not record the change ({e}); the folder was not added.")
            return False
        self.refresh()
        return True

    def _remove_selected(self) -> None:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        if item is not None:
            self.remove_folder(item.text())

    def remove_folder(self, label: str) -> bool:
        from orchestrator.tools.local_files import LocalFileError, record_folder_change
        try:
            removed = self.files.store.remove(label)
        except LocalFileError as e:
            QMessageBox.warning(self, "Folders the AI may use", str(e))
            return False
        if not removed:
            QMessageBox.information(self, "Folders the AI may use",
                                    f"'{label}' is a built-in folder; change it in the settings (models_dir, "
                                    "datasets_dir).")
            return False
        try:
            record_folder_change(getattr(self.registry, "audit", None), "gui", "removed", {"label": label})
        except Exception as e:
            logging.getLogger(__name__).warning(f"Removing the AI folder '{label}' was not audited: {e}")
        self.refresh()
        return True


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
