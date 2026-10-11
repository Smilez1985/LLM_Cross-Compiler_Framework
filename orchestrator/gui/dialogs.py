#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - GUI Dialogs (v2.3.0 Enterprise)
DIREKTIVE: Goldstandard UI Components.

Updates:
- SecretInputDialog supports Keyring integration.
- HealingDialog allows editing of AI proposals.
- LanguageSelectionDialog for first-run setup.
"""

import threading
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QDialogButtonBox, QMessageBox, QComboBox, QPushButton, QSizePolicy,
    QWidget, QFormLayout, QTextEdit, QSpinBox,
    QCheckBox, QGroupBox, QTableWidget, QTableWidgetItem, QHeaderView
)
from PySide6.QtCore import QObject, Qt, Signal
from orchestrator.llm.errors import ANTHROPIC_WORKSPACE_INVALID, rejected_key_error
from orchestrator.llm.providers.anthropic import WORKSPACE_ID
from orchestrator.utils.helpers import redact_secrets
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


def list_provider_models(config: Any, secrets: Any, provider: str) -> List[str]:
    """Model ids from the provider's live list (raises an LLMError with a readable message on failure)."""
    from orchestrator.llm import LLMClient
    return [m.id for m in LLMClient(config, secrets).list_models(provider)]


class _ModelListSignals(QObject):
    done = Signal(int, object, str)  # request number, model ids (None on failure), message


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
        self._model_request = 0
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
        # Placeholder for the per-provider sign-in (OAuth, device code, CLI login), built in its own work package.
        self.btn_sign_in = QPushButton("Sign in...")
        self.btn_sign_in.setEnabled(False)
        self.btn_sign_in.setToolTip("Signing in per provider is not available yet; use an API key.")
        self.btn_sign_in.clicked.connect(self._sign_in)
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

    def _stored_key(self) -> Optional[str]:
        preset, sm = self._preset(), self._secrets()
        if preset is not None and preset.secret and sm:
            return sm.get_secret(preset.secret)
        return None

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
        return self.txt_workspace.text().strip() if preset is not None and preset.adapter == "anthropic" else ""

    def _apply_enabled_state(self):
        preset = self._preset()
        if preset is None:
            return
        self.txt_api_key.setEnabled(bool(preset.secret))
        self.txt_workspace.setEnabled(preset.adapter == "anthropic")
        self.txt_base_url.setEnabled(preset.base_url_configurable)
        self.txt_base_url.setPlaceholderText(preset.base_url or "https://.../v1")
        self.btn_sign_in.setEnabled(self._sign_in_available(preset))
        self.btn_refresh_models.setEnabled(preset.live_models)
        if not preset.secret:
            self.txt_api_key.setPlaceholderText("Not needed for this provider")
        elif self._stored_key():
            self.txt_api_key.setPlaceholderText("******** (Stored in Keyring)")
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

    # --- sign-in hook ---
    def _sign_in_available(self, preset) -> bool:
        """Whether the selected provider offers an interactive sign-in here (none yet)."""
        return False

    def _sign_in(self):
        """Hook for the sign-in work package: starts the selected provider's own sign-in flow (OAuth, device
        code, CLI login) for self.cb_provider.currentData(). Until then keys go into the API Key field."""
        return None

    # --- live model list ---
    def _refresh_models(self):
        """Loads the provider's model list in a worker thread; on failure the free text stays as it is."""
        preset = self._preset()
        self._model_request += 1
        request = self._model_request
        if preset is None or not preset.live_models:
            self.lbl_models.setText("")
            return
        overrides = {"ai_provider": preset.id, "ai_model": self.cb_model.currentText().strip() or preset.id,
                     "offline_mode": self.chk_offline.isChecked()}
        if self._base_url(preset):
            overrides["ai_base_url"] = self._base_url(preset)
        if preset.adapter == "anthropic":
            overrides["ai_anthropic_workspace_id"] = self._workspace_id(preset)  # also when cleared
        config = SettingsOverlay(self._config(), overrides)
        secrets = _SecretsOverlay(self._secrets(), preset.secret, self.txt_api_key.text().strip())
        lister = self.model_lister
        # No parent: the worker emits on this object, so it must not die with the dialog (closing the dialog while
        # the list loads would free it under the worker). The connection ends when the dialog is destroyed.
        signals = _ModelListSignals()
        signals.done.connect(self._on_models_listed)
        self.lbl_models.setText("Loading the provider's models...")

        def work():
            try:
                ids, message = list(lister(config, secrets, preset.id)), ""
            except Exception as e:  # LLMError text is already redacted and readable
                text = redact_secrets(str(e)).strip()
                ids, message = None, (text.splitlines()[0][:300] if text else type(e).__name__)
            try:
                signals.done.emit(request, ids, message)
            except RuntimeError:
                pass  # the dialog was closed meanwhile

        threading.Thread(target=work, name="ai-model-list", daemon=True).start()

    def _on_models_listed(self, request: int, ids, message: str):
        if request != self._model_request:
            return  # the provider changed meanwhile
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
        self.txt_base_url.setText((self._user_value("ai_base_url") or "") if saved and preset.base_url_configurable
                                  else "")
        self.txt_workspace.setText((self._user_value("ai_anthropic_workspace_id") or "")
                                   if preset.adapter == "anthropic" else "")
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
        removed = self.catalog.removed_message(saved_provider)
        if removed:  # the saved provider no longer exists: say why and what to use instead, once per load
            QMessageBox.information(self, "AI Provider Removed", removed)

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
            QMessageBox.warning(self, "Invalid Fallback Chain",
                                f"Unknown provider in the fallback chain: {', '.join(unknown)}.\n"
                                f"Known providers: {', '.join(self.catalog.ids())}.")
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

        if api_key and preset.secret:
            sm = self._secrets()
            if not sm or not sm.set_secret(preset.secret, api_key):
                QMessageBox.critical(self, "Error", "Failed to store API key in the OS keyring.")
                return

        effort = self.cb_effort.currentData() or ""
        security = self.cb_security.currentData() or "STRICT"
        cfg = self._config()
        if cfg is not None and hasattr(cfg, 'set'):
            cfg.set("ai_provider", preset.id)
            cfg.set("ai_model", model)
            cfg.set("ai_base_url", base_url)
            if preset.adapter == "anthropic":
                cfg.set("ai_anthropic_workspace_id", workspace)
            cfg.set("ai_effort", effort)
            cfg.set("ai_security_level", security)
            cfg.set("ai_fallback_chain", fallback)
            cfg.set("offline_mode", offline)
            cfg.set("enable_rag_knowledge", enable_rag)
            if hasattr(cfg, 'save_user_config'):
                cfg.save_user_config()

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

    def get_config(self) -> Dict[str, Any]:
        """Selected settings after the dialog was accepted (empty otherwise); never contains a key."""
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
            "Tools that reach other devices or delete data always ask unless you allow them one by one.")
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
        self.table = QTableWidget(len(tools), 3)
        self.table.setHorizontalHeaderLabels(["Tool", "Category", "Permission"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.override_combos = {}
        for row, spec in enumerate(tools):
            name_item = QTableWidgetItem(spec.title)
            name_item.setToolTip(spec.description)
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, QTableWidgetItem(spec.tier.value))
            combo = QComboBox()
            combo.addItem("Category default", "")
            for choice in (Decision.ALLOW, Decision.ASK, Decision.DENY):
                combo.addItem(choice.value.capitalize(), choice.value)
            current = policy.overrides.get(spec.name)
            combo.setCurrentIndex(combo.findData(current.value) if current else 0)
            self.table.setCellWidget(row, 2, combo)
            self.override_combos[spec.name] = combo
        layout.addWidget(self.table)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def build_policy(self):
        from orchestrator.tools.policy import ToolPolicy
        tiers = {tier: self._Decision(combo.currentData()) for tier, combo in self.tier_combos.items()}
        overrides = {name: self._Decision(combo.currentData())
                     for name, combo in self.override_combos.items() if combo.currentData()}
        return ToolPolicy(configured=True, tiers=tiers, overrides=overrides)

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
