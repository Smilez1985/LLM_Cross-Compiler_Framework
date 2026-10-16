#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Configuration Manager
DIREKTIVE: Goldstandard, vollständige Implementierung.

Verwaltet die globale Konfiguration, validiert Eingaben gegen definierte Schemata
und persistiert Benutzereinstellungen.

Updates v2.0.0:
- Added Settings for Chat Context, Telemetry, and Offline Mode.
Updates v2.3.0:
- Added Centralized Docker Image Definitions.
Updates v2.4.0:
- Added defaults for IMatrix/Smart Calibration.
- Added SSOT for Source Repositories.

Some settings belong to features that are still being built and no code reads them yet (see the roadmap):
logs_dir, auto_cleanup, docker_namespace, default_enable_imatrix, default_calibration_dataset, gui_theme,
gui_auto_refresh, gui_refresh_interval, api_enabled, api_port and api_host. preferred_tiny_model was removed with
the Transformers offline mode; a config.yml that still contains it loads as before (the key is kept and ignored).
"""

import os
import json
import math
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable, Set, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import threading
import re
import yaml

from orchestrator.utils.logging import get_logger
from orchestrator.utils.helpers import ensure_directory
from orchestrator.utils.llama_pin import pinned_tag

# Settings per provider that the sign-in (llm-cli ditto login, the GUI's sign-in panel) writes to the user config.
SIGN_IN_SETTING = re.compile(r"^(ai_auth_[a-z0-9][a-z0-9_.-]{0,63}|ai_base_url_[a-z0-9][a-z0-9_.-]{0,63}|"
                             r"ai_oauth_profile|ai_auth)$")
# Settings of features that were removed, with the reason: found in an older config file they are ignored (one log line
# names them) and never become values.
_REMOTE_MODE = "Ditto's remote mode was removed; Ditto always uses this computer's tools"
RETIRED_SETTINGS: Dict[str, str] = {
    **{key: _REMOTE_MODE for key in ("ai_ditto_tool_source", "ai_ditto_mcp_transport", "ai_ditto_mcp_command",
                                     "ai_ditto_mcp_url", "ai_ditto_mcp_name")},
    "image_qdrant": "device packages no longer ship a Qdrant sidecar; the knowledge base stays on this computer",
}


# ============================================================================
# ENUMS AND CONSTANTS
# ============================================================================

class ConfigFormat(Enum):
    """Configuration file formats"""
    YAML = "yaml"
    JSON = "json"
    ENV = "env"
    TOML = "toml"

class ConfigScope(Enum):
    """Configuration scope levels"""
    GLOBAL = "global"
    USER = "user"
    PROJECT = "project"
    ENVIRONMENT = "environment"
    RUNTIME = "runtime"

class AdvancedFeature(Enum):
    """Optional advanced features"""
    SECRETS_MANAGEMENT = "secrets_management"
    DYNAMIC_UPDATES = "dynamic_updates"
    TEMPLATES = "templates"
    MIGRATION = "migration"
    ENVIRONMENTS = "environments"
    COMPLIANCE = "compliance"

# ============================================================================
# DATA MODELS
# ============================================================================

@dataclass
class ConfigSchema:
    """Configuration schema definition"""
    field_name: str
    field_type: type
    required: bool = False
    default_value: Any = None
    description: str = ""
    validation_rules: List[str] = field(default_factory=list)
    enterprise_only: bool = False
    
    def validate_value(self, value: Any) -> Tuple[bool, List[str]]:
        if value is None and self.default_value is None and not self.required:
            return True, []  # unset, as by default
        errors = []
        if value is not None and not isinstance(value, self.field_type):
            try:
                if self.field_type == int:
                    value = int(value)
                elif self.field_type == float:
                    value = float(value)
                elif self.field_type == bool:
                    value = str(value).lower() in ('true', '1', 'yes', 'on')
                elif self.field_type == str:
                    value = str(value)
                elif self.field_type == list:
                    if isinstance(value, (tuple, set)):
                        value = list(value)
                    elif isinstance(value, str):
                        value = [v.strip() for v in value.split(',')]
                elif self.field_type == dict:
                    if isinstance(value, str):
                        try:
                            value = json.loads(value)
                        except:
                            errors.append(f"Invalid dict format for {self.field_name}")
            except (ValueError, TypeError, OverflowError):  # OverflowError: int(.inf) from a YAML file
                errors.append(f"Invalid type for {self.field_name}: expected {self.field_type.__name__}")
        if self.field_type in (int, float) and isinstance(value, float) and not math.isfinite(value):
            errors.append(f"Invalid value for {self.field_name}: {value} is not a finite number")

        for rule in self.validation_rules:
            if not self._apply_validation_rule(value, rule):
                errors.append(f"Validation rule failed for {self.field_name}: {rule}")
        
        return len(errors) == 0, errors
    
    def _apply_validation_rule(self, value: Any, rule: str) -> bool:
        try:
            if rule.startswith("min:"):
                return value >= float(rule.split(":", 1)[1])
            elif rule.startswith("max:"):
                return value <= float(rule.split(":", 1)[1])
            elif rule.startswith("regex:"):
                pattern = rule.split(":", 1)[1]
                return bool(re.match(pattern, str(value)))
            elif rule == "positive":
                return value > 0
            elif rule == "non_empty":
                return bool(value and (str(value).strip() if isinstance(value, str) else len(value) > 0))
        except Exception:
            return False
        return True

@dataclass
class ConfigSource:
    source_type: str
    source_path: Optional[str] = None
    loaded_at: Optional[datetime] = None
    priority: int = 0

@dataclass
class ConfigValue:
    key: str
    value: Any
    source: ConfigSource
    schema: Optional[ConfigSchema] = None
    is_secret: bool = False
    is_readonly: bool = False
    last_modified: Optional[datetime] = None
    
    def is_valid(self) -> bool:
        if not self.schema:
            return True
        valid, _ = self.schema.validate_value(self.value)
        return valid
    
    def get_validation_errors(self) -> List[str]:
        if not self.schema:
            return []
        _, errors = self.schema.validate_value(self.value)
        return errors

@dataclass
class AdvancedModeConfig:
    enabled: bool = False
    enabled_features: Set[AdvancedFeature] = field(default_factory=set)
    show_advanced_options: bool = False
    show_expert_warnings: bool = True
    require_confirmation: bool = True
    audit_changes: bool = False
    require_validation: bool = True
    enforce_compliance: bool = False
    
    def is_feature_enabled(self, feature: AdvancedFeature) -> bool:
        return self.enabled and feature in self.enabled_features
    
    def enable_feature(self, feature: AdvancedFeature):
        if self.enabled:
            self.enabled_features.add(feature)

# ============================================================================
# CONFIGURATION MANAGER CLASS
# ============================================================================

class ConfigManager:
    def __init__(self, 
                 config_dir: Optional[Path] = None,
                 advanced_mode: bool = False,
                 enable_secrets: bool = False,
                 enable_templates: bool = False,
                 enable_dynamic_updates: bool = False):
        self.logger = get_logger(__name__)

        config_file: Optional[Path] = None
        if config_dir is not None:
            config_dir = Path(config_dir)
            if config_dir.is_file() or config_dir.suffix.lower() in ('.yml', '.yaml', '.json'):
                config_file = config_dir
                config_dir = config_dir.parent
        self.config_dir = config_dir or self._detect_config_directory()
        self._lock = threading.RLock()
        
        self.advanced_mode = AdvancedModeConfig(enabled=advanced_mode)
        if advanced_mode:
            if enable_secrets:
                self.advanced_mode.enable_feature(AdvancedFeature.SECRETS_MANAGEMENT)
            if enable_templates:
                self.advanced_mode.enable_feature(AdvancedFeature.TEMPLATES)
            if enable_dynamic_updates:
                self.advanced_mode.enable_feature(AdvancedFeature.DYNAMIC_UPDATES)
        
        self.config_values: Dict[str, ConfigValue] = {}
        self.config_schemas: Dict[str, ConfigSchema] = {}
        self.change_history: List[Dict[str, Any]] = []
        self.change_listeners: List[Callable] = []
        self._revisions: Dict[str, int] = {}  # per key: how often set() stored it (see revision())
        
        self._initialize_core_schemas()
        self._ensure_directories()
        self.load_configuration(config_file if config_file and config_file.exists() else None)

        self.logger.info(f"Configuration Manager initialized (advanced_mode: {advanced_mode})")
    
    @staticmethod
    def installation_config_dir() -> Path:
        return Path(__file__).resolve().parents[2] / "configs"

    @staticmethod
    def writable_dir(path: Path) -> bool:
        """Whether this user can create files in path. os.access alone is not enough on Windows: it ignores the
        folder's permissions, so C:\\Program Files looks writable to every user. A temporary file is created
        there and removed at once."""
        path = Path(path)
        if not path.is_dir() or not os.access(path, os.W_OK):
            return False
        try:
            with tempfile.TemporaryFile(dir=path):
                return True
        except OSError:
            return False

    def _detect_config_directory(self) -> Path:
        """LLM_FRAMEWORK_CONFIG_DIR, else the installation's configs/ folder, else the per-user folder.
        Never the working directory: AI clients start the MCP server inside arbitrary projects, and a
        project folder must not be able to change settings such as source repositories or images."""
        override = os.environ.get("LLM_FRAMEWORK_CONFIG_DIR")
        if override:
            return Path(override).expanduser()
        install = self.installation_config_dir()
        if self.writable_dir(install):
            return install
        return self.user_state_dir

    @classmethod
    def default_data_root(cls) -> Optional[Path]:
        """Where the data folders (models_dir, output_dir, cache_dir) are by default: None for the installation
        folder (relative paths resolve against it), or the per-user folder when this user cannot write to the
        installation (e.g. C:\\Program Files). Nothing is created here; folders are made when they are used."""
        if cls.writable_dir(cls.installation_config_dir().parent):
            return None
        return cls.user_state_path()

    @staticmethod
    def user_state_path() -> Path:
        """The per-user folder (LLM_FRAMEWORK_HOME, default ~/.llm-framework), without creating it."""
        return Path(os.environ.get("LLM_FRAMEWORK_HOME") or Path.home() / ".llm-framework")

    @property
    def user_state_dir(self) -> Path:
        """Per-user directory for personal state (chat history, later consent and known hosts), outside the repo.
        LLM_FRAMEWORK_HOME overrides the default ~/.llm-framework."""
        state = self.user_state_path()
        state.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            try:
                state.chmod(0o700)
            except OSError:
                pass
        return state

    def _ensure_directories(self):
        directories = [
            self.config_dir,
            self.config_dir / "environments",
            self.config_dir / "templates",
            self.config_dir / "backups"
        ]
        if self.advanced_mode.enabled:
            directories.extend([
                self.config_dir / "secrets",
                self.config_dir / "compliance",
                self.config_dir / "audit"
            ])
        for directory in directories:
            ensure_directory(directory)

    def _initialize_core_schemas(self):
        # Data folders: relative to the installation, or in the per-user folder if the installation is read-only.
        data_root = self.default_data_root()

        def data_dir(name: str) -> str:
            return name if data_root is None else str(data_root / name)

        core_schemas = [
            # Core Paths & Settings
            ConfigSchema("targets_dir", str, True, "targets", "Directory for target definitions"),
            ConfigSchema("models_dir", str, True, data_dir("models"), "Directory for model storage"),
            ConfigSchema("datasets_dir", str, False, data_dir("datasets"),
                         "Directory for calibration datasets (label 'datasets' for AI callers)"),
            # Golden artifacts (eighth round, answer 3): the person's output folder, changed once in the settings (only
            # the person: an AI cannot), and the framework's build folder, where GUI builds wait to be saved and where
            # builds went before (they stay listed there)
            ConfigSchema("output_dir", str, True, "~/llmcf/output",
                         "Output folder for finished builds (golden artifacts); ~ is your home folder (on Windows "
                         "C:\\Users\\<name>). The GUI's save dialog opens here, CLI and AI builds go here"),
            ConfigSchema("build_dir", str, False, data_dir("output"),
                         "The framework's build folder: GUI builds wait here until they are saved, and earlier builds "
                         "stay here"),
            ConfigSchema("cache_dir", str, True, data_dir("cache"), "Directory for cache files"),
            ConfigSchema("logs_dir", str, True, "logs", "Directory for log files"),
            ConfigSchema("log_level", str, False, "INFO", "Logging level", ["regex:^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$"]),
            
            # Build Settings
            ConfigSchema("max_concurrent_builds", int, False, 2, "Maximum concurrent builds", ["min:1", "max:10"]),
            ConfigSchema("build_timeout", int, False, 3600, "Build timeout in seconds", ["min:60"]),
            ConfigSchema("auto_cleanup", bool, False, True, "Enable automatic cleanup"),
            ConfigSchema("docker_registry", str, False, "",
                         "Docker registry (empty: none; read by nothing: the framework publishes and pulls no "
                         "images of its own)"),
            ConfigSchema("docker_namespace", str, False, "llm-framework", "Docker namespace"),
            
            # --- SSOT: Centralized Image Definitions (v2.3) ---
            ConfigSchema("image_trivy", str, False, "aquasec/trivy:0.75.0", "Security Scanner Image (pinned: 'latest' was compromised in 2026-03)"),
            ConfigSchema("enable_security_scan", bool, False, True,
                         "Scan built images with Trivy (from a saved archive of the image, never the Docker socket)"),
            ConfigSchema("llama_cpp_version", str, False, pinned_tag(),
                         "llama.cpp tag of the pin in llama_runtime.yml; target images build the pin's commit, and "
                         "another value only gives a warning"),
            ConfigSchema("calibration_language", str, False, "mixed", "Shipped calibration text for importance matrices",
                         ["regex:^(mixed|de|en)$"]),
            ConfigSchema("build_sandbox", bool, False, True, "Run conversion containers without network, capabilities or root"),
            ConfigSchema("build_memory_limit", str, False, "", "Memory limit for conversion containers, e.g. 24g (empty: none)"),
            ConfigSchema("build_pids_limit", int, False, 4096, "Process limit for conversion containers", ["min:64"]),
            ConfigSchema("build_jobs", int, False, 0, "Parallel compile jobs in image builds (0 = all cores)", ["min:0", "max:256"]),
            ConfigSchema("healing_approval_timeout", int, False, 900, "Seconds to wait for a decision on a healing proposal", ["min:10", "max:86400"]),
            ConfigSchema("image_base_debian", str, False, "debian:bookworm-slim", "Base Build Image"),
            ConfigSchema("image_inference_runtime", str, False, "llm-framework/inference:local",
                         "Inference image of deployment packages: a local image name (built on this computer) "
                         "or name@sha256:<digest>; the framework pulls no image of its own"),
            
            # --- SSOT: Repositories (v2.3) ---
            ConfigSchema("source_repositories", dict, False, {}, "Map of Git Repositories (llama.cpp, etc.)"),

            # --- IMatrix & Smart Calibration Defaults (v2.4) ---
            ConfigSchema("default_enable_imatrix", bool, False, False, "Enable IMatrix generation by default"),
            ConfigSchema("default_calibration_dataset", str, False, "wiki.train.raw", "Default dataset filename"),

            # GUI & API
            ConfigSchema("gui_theme", str, False, "dark", "GUI theme"),
            ConfigSchema("gui_auto_refresh", bool, False, True, "GUI auto-refresh"),
            ConfigSchema("gui_refresh_interval", int, False, 30, "GUI refresh interval"),
            ConfigSchema("api_enabled", bool, False, False, "Enable API server"),
            ConfigSchema("api_port", int, False, 8000, "API server port"),
            ConfigSchema("api_host", str, False, "127.0.0.1", "API server host"),

            # I18N & Security
            ConfigSchema("language", str, False, "en", "Interface Language (en/de)"),
            ConfigSchema("ai_security_level", str, False, "STRICT", "AI Data Leakage Protection Level",
                         ["regex:^(STRICT|BALANCED|OFF)$"]),
            ConfigSchema("secret_storage", str, False, "keyring",
                         "Protection of the credential vault key: keyring (OS keyring), helper (secret_helper "
                         "command), passphrase (asked once per start), insecure (key file, not encrypted at rest) "
                         "or ephemeral (memory only, nothing stored). Never changed automatically.",
                         ["regex:^(keyring|helper|passphrase|insecure|ephemeral)$"]),
            ConfigSchema("secret_helper", str, False, "",
                         "Command whose output protects the vault key when secret_storage is 'helper', e.g. "
                         "'pass show llmcc/vault-key'; run without a shell"),
            
            # Local RAG / Vector Database (v1.5.0)
            ConfigSchema("enable_rag_knowledge", bool, False, False, "Enable local Qdrant Vector DB for AI"),
            ConfigSchema("rag_embedding_model", str, False, "auto",
                         "Local embedding model of the knowledge base: 'auto' keeps the model the knowledge base "
                         "was built with, else picks by hardware (intfloat/multilingual-e5-small on CPU, "
                         "BAAI/bge-m3 with CUDA); or a model id. Each model has its own collection.",
                         ["non_empty"]),
            ConfigSchema("qdrant_url", str, False, "",
                         "Qdrant server for the knowledge base; empty = embedded storage in the user state "
                         "directory. Remote servers need https and the secret 'qdrant_api_key' (never "
                         "credentials in the URL).",
                         ["regex:^(https?://[^\\s/?#@]+([/?#]\\S*)?)?$"]),
            ConfigSchema("rag_reranker", str, False, "auto",
                         "Cross-encoder that reranks knowledge-base results and drops irrelevant ones: 'auto' "
                         "(multilingual MiniLM from 6 GB RAM, bge-reranker-v2-m3 with CUDA and 16 GB), 'off', or "
                         "a model id. Used by build recommendations, and by chat and MCP searches with "
                         "rag_rerank_default.",
                         ["non_empty"]),
            ConfigSchema("rag_rerank_default", bool, False, False,
                         "Also rerank Ditto's chat searches and the MCP tool search_knowledge"),
            ConfigSchema("rag_rerank_min_score", float, False, None,
                         "Relevance floor (0..1) for reranked results; unset = the reranker's calibrated floor, "
                         "0 = no floor",
                         ["min:0", "max:1"]),
            ConfigSchema("rag_model_idle_minutes", int, False, 10,
                         "Minutes after which unused knowledge-base models are unloaded (0 = keep them loaded)",
                         ["min:0"]),

            # Deep Crawler Settings (v1.6.0)
            ConfigSchema("crawler_max_depth", int, False, 2, "Max recursion depth for crawler"),
            ConfigSchema("crawler_max_pages", int, False, 50, "Max pages to crawl per session"),
            ConfigSchema("crawler_rate_limit", float, False, 1.0, "Delay between crawler requests (seconds)", ["min:0"]),
            ConfigSchema("crawler_download_pdfs", bool, False, True, "Download and parse PDF files while crawling"),
            ConfigSchema("crawler_languages", list, False, ["en", "de"],
                         "Languages (ISO 639-1) of the pages the crawler keeps; empty = all languages"),
            ConfigSchema("audio_announcements", bool, False, True,
                         "Spoken announcements (Deep Ingest notice and outcome) through the speech engine of the "
                         "OS; never in CI, over SSH without a display or with LLM_FRAMEWORK_NO_AUDIO=1"),
            ConfigSchema("chat_panel_auto_show", bool, False, True,
                         "Show the chat panel when a question waits for you (it never takes the keyboard focus); "
                         "an AI cannot change this setting"),
            ConfigSchema("trash_retention_days", int, False, 14,
                         "Days after which the trash reminds you of what it holds; nothing is ever deleted by "
                         "itself, and an AI cannot change this setting", ["min:1", "max:3650"]),

            # AI Agent (Ditto) Selection
            ConfigSchema("ai_provider", str, False, "OpenAI",
                         "AI provider id from configs/llm_providers.yml (anthropic, openai, google, openrouter, "
                         "ollama, local, custom); older names such as OpenAI still work"),
            ConfigSchema("ai_auth_anthropic", str, False, None,
                         "Claude sign-in: empty or api_key = the stored API key; ant_profile = an `ant auth login` "
                         "profile (never picked automatically)"),
            ConfigSchema("ai_anthropic_workspace_id", str, False, None,
                         "Claude workspace ID (wrkspc_...) for API keys that are valid in every workspace of the "
                         "organization; sent as the anthropic-workspace-id header with the API key (an `ant` "
                         "profile keeps its own workspace)"),
            ConfigSchema("ai_allow_sdk_env_headers", bool, False, False,
                         "Keep OPENAI_CUSTOM_HEADERS / ANTHROPIC_CUSTOM_HEADERS from the environment (dropped at "
                         "startup by default: they could replace the API key or reach other servers)"),
            ConfigSchema("ai_model", str, False, "gpt-6.1-sol", "AI model name"),
            ConfigSchema("ai_base_url", str, False, "", "Base URL for Ollama/Custom endpoints"),
            ConfigSchema("ai_effort", str, False, "", "Reasoning effort: low, medium, high, xhigh, max; "
                         "empty = the provider's default per task", ["regex:^(low|medium|high|xhigh|max)?$"]),
            ConfigSchema("ai_fallback_chain", list, False, [],
                         "Providers tried when the model declines or the provider is unavailable, as provider or "
                         "provider:model, e.g. local, ollama:qwen3.5:4b"),

            # v2.0.0: Chat & Brain Settings
            ConfigSchema("chat_context_limit", int, False, 4096, "Max tokens for chat context history"),
            ConfigSchema("enable_telemetry", bool, False, False, "Allow anonymous error reporting (GitHub Issues)"),
            ConfigSchema("offline_mode", bool, False, False, "Force offline mode (no external APIs)"),

            # Community zoo (3.1): the signed index (verified with configs/community_keys.yml) and the repository
            # that receives contributions as pull requests. Empty until the community repository is published.
            ConfigSchema("community_index_url", str, False, "",
                         "HTTPS URL of the signed community index (index.json; index.json.sig next to it)",
                         ["regex:^(https://\\S+)?$"]),
            ConfigSchema("community_repo", str, False, "",
                         "GitHub repository (owner/name) that receives community packs as pull requests",
                         ["regex:^([A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100})?$"]),
        ]
        
        for schema in core_schemas:
            self.config_schemas[schema.field_name] = schema
    
    def load_configuration(self, config_file: Optional[Path] = None) -> bool:
        try:
            self._load_default_configuration()
            # The user config (written by save_user_config) is always read; an explicitly
            # passed file is layered on top so its values win.
            self._load_config_files_auto()
            if config_file:
                try:
                    self._load_config_file(config_file)
                except Exception as e:
                    # A file chosen explicitly (llm-cli -c) that is ignored must show, also with -q (ERROR level);
                    # the other sources (config.yml, LLM_FRAMEWORK_* variables, defaults) still apply.
                    self.logger.error(f"Failed to load config file {config_file}: {e}; its settings are not used.")
            self._load_environment_variables()
            self._validate_configuration()
            return True
        except Exception as e:
            self.logger.error(f"Configuration loading failed: {e}")
            return False
    
    def _load_default_configuration(self):
        default_source = ConfigSource("default", loaded_at=datetime.now(), priority=0)
        for schema_name, schema in self.config_schemas.items():
            if schema.default_value is not None:
                with self._lock:
                    self.config_values[schema_name] = ConfigValue(
                        key=schema_name,
                        value=schema.default_value,
                        source=default_source,
                        schema=schema,
                        last_modified=datetime.now()
                    )
    
    def _load_config_files_auto(self):
        config_candidates = [
            self.config_dir / "framework.yml",
            self.config_dir / "config.yml",
        ]
        for config_file in config_candidates:
            if config_file.exists():
                try:
                    self._load_config_file(config_file)
                except Exception as e:
                    self.logger.warning(f"Failed to load config file {config_file}: {e}")
    
    def _load_config_file(self, config_file: Path):
        if not config_file.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_file}")
        
        with open(config_file, 'r') as f:
            if config_file.suffix in ['.yml', '.yaml']:
                config_data = yaml.safe_load(f)
            elif config_file.suffix == '.json':
                config_data = json.load(f)
            else:
                config_data = {}
        
        if not config_data: return
        
        file_source = ConfigSource("file", str(config_file), datetime.now(), 10)
        self._load_config_data(config_data, file_source)
    
    def _load_config_data(self, config_data: Dict[str, Any], source: ConfigSource):
        retired = sorted(str(k) for k in config_data if k in RETIRED_SETTINGS)
        if retired:
            reasons = "; ".join(f"{reason}: {', '.join(k for k in retired if RETIRED_SETTINGS[k] == reason)}"
                                for reason in dict.fromkeys(RETIRED_SETTINGS[k] for k in retired))
            self.logger.info(f"Ignoring settings of a removed feature in {source.source_path} ({reasons}).")
        for key, value in config_data.items():
            if key in RETIRED_SETTINGS:
                continue
            schema = self.config_schemas.get(key)
            if schema and schema.enterprise_only and not self.advanced_mode.enabled:
                continue
            try:
                valid = not schema or schema.validate_value(value)[0]
            except Exception:  # one bad value must never cost the settings after it in the file
                valid = False
            if not valid:
                self.logger.warning(f"Ignoring invalid value for '{key}' from {source.source_path}: {value!r}")
                continue

            with self._lock:
                existing = self.config_values.get(key)
                if not existing or source.priority >= existing.source.priority:
                    self.config_values[key] = ConfigValue(
                        key=key, value=value, source=source, schema=schema, last_modified=datetime.now()
                    )

    def _load_environment_variables(self):
        env_source = ConfigSource("environment", loaded_at=datetime.now(), priority=20)
        prefixes = ["LLM_FRAMEWORK_", "FRAMEWORK_", "LLM_"]
        
        for env_name, env_value in os.environ.items():
            config_key = None
            for prefix in prefixes:
                if env_name.startswith(prefix):
                    config_key = env_name[len(prefix):].lower()
                    break
            
            if config_key:
                schema = self.config_schemas.get(config_key)
                # Unrelated variables (e.g. LLM_CONFIG) must not turn into config attributes.
                if schema is None:
                    continue
                if schema.enterprise_only and not self.advanced_mode.enabled:
                    continue

                try:
                    val = self._convert_env_value(env_value, schema)
                    if not schema.validate_value(val)[0]:
                        self.logger.warning(f"Ignoring invalid value for '{config_key}' from {env_name}")
                        continue
                    with self._lock:
                        self.config_values[config_key] = ConfigValue(
                            key=config_key, value=val, source=env_source, schema=schema, last_modified=datetime.now()
                        )
                except Exception:
                    pass

    def _convert_env_value(self, env_value: str, schema: Optional[ConfigSchema]) -> Any:
        if not schema: return env_value
        try:
            if schema.field_type == bool:
                return env_value.lower() in ('true', '1', 'yes', 'on')
            elif schema.field_type == int:
                return int(env_value)
            elif schema.field_type == float:
                return float(env_value)
            return env_value
        except:
            return env_value

    def _validate_configuration(self):
        for schema_name, schema in self.config_schemas.items():
            if schema.required and schema_name not in self.config_values:
                self.logger.error(f"Missing required config: {schema_name}")

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            val = self.config_values.get(key)
            return val.value if val else default

    def revision(self, key: str) -> int:
        """How often set() stored `key` in this process: a cache can tell that a setting was changed, also when it
        was changed back to the value it had before."""
        with self._lock:
            return self._revisions.get(key, 0)

    # PROXY METHOD FOR ATTRIBUTE ACCESS (orchestrator.py expects config.log_level)
    def __getattr__(self, name):
        with self._lock:
            val = self.config_values.get(name)
            if val:
                return val.value
        raise AttributeError(f"Configuration has no attribute '{name}'")

    def set(self, key: str, value: Any, source_type: str = "override") -> bool:
        schema = self.config_schemas.get(key)
        if schema and schema.enterprise_only and not self.advanced_mode.enabled:
            return False
            
        if schema:
            valid, _ = schema.validate_value(value)
            if not valid: return False
            
        src = ConfigSource(source_type, loaded_at=datetime.now(), priority=30)
        val = ConfigValue(key, value, src, schema, last_modified=datetime.now())
        
        with self._lock:
            old = self.config_values.get(key)
            self.config_values[key] = val
            self._revisions[key] = self._revisions.get(key, 0) + 1
            if self.advanced_mode.enabled:
                self._track_config_change(key, old.value if old else None, value, source_type)
            if self.advanced_mode.is_feature_enabled(AdvancedFeature.DYNAMIC_UPDATES):
                self._notify_change_listeners(key, value)
        return True
    
    def save_user_config(self):
        """
        Saves current configuration to config.yml (User Scope).
        Updated v2.0.0: Includes chat, telemetry and offline mode settings.
        Updated v2.4.0: Includes SSOT for images and repositories.
        """
        config_file = self.config_dir / "config.yml"
        data = {}
        
        if config_file.exists():
            # A file that cannot be read was not loaded either: overwriting it would replace all its settings with
            # the few changed at runtime (e.g. the first-run language), so it is left alone.
            try:
                with open(config_file, 'r') as f:
                    loaded = yaml.safe_load(f)
            except (OSError, ValueError, yaml.YAMLError) as e:
                self.logger.error(f"Configuration not saved: {config_file} cannot be read ({e}); fix or move it.")
                return False
            if loaded is not None and not isinstance(loaded, dict):
                self.logger.error(f"Configuration not saved: {config_file} does not contain settings (key: value).")
                return False
            data = loaded or {}

        with self._lock:
            persist_keys = [
                "language", "ai_security_level", "secret_storage", "secret_helper", "gui_theme", "docker_registry",
                "enable_rag_knowledge", "rag_embedding_model", "qdrant_url",
                "rag_reranker", "rag_rerank_default", "rag_rerank_min_score", "rag_model_idle_minutes",
                "crawler_max_depth", "crawler_max_pages",
                "crawler_rate_limit", "crawler_download_pdfs", "crawler_languages", "audio_announcements",
                "chat_panel_auto_show",
                "trash_retention_days", "output_dir",
                "ai_provider", "ai_model", "ai_base_url", "ai_effort", "ai_fallback_chain",
                "ai_anthropic_workspace_id",
                # v2.0
                "chat_context_limit", "enable_telemetry", "offline_mode",
                # v2.3
                "image_trivy", "image_base_debian", "image_inference_runtime",
                "enable_security_scan", "llama_cpp_version", "build_jobs", "healing_approval_timeout",
                "calibration_language", "build_sandbox", "build_memory_limit", "build_pids_limit",
                # v2.4 (SSOT & IMatrix)
                "source_repositories", "default_enable_imatrix", "default_calibration_dataset",
            ]
            
            # Only values changed at runtime via set(); defaults and env overrides must not
            # become permanent, and values already in config.yml are kept from `data` above.
            for key, val in self.config_values.items():
                if val.source.source_type != "override":
                    continue
                if key in persist_keys:
                    data[key] = val.value
                elif SIGN_IN_SETTING.match(key):
                    # Per-provider sign-in choices (llm-cli ditto login, the sign-in panel); "" removes one.
                    if val.value in (None, ""):
                        data.pop(key, None)
                    else:
                        data[key] = val.value
        
        try:
            with open(config_file, 'w') as f:
                yaml.dump(data, f, default_flow_style=False)
            self.logger.info(f"Configuration saved to {config_file}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to save config: {e}")
            return False

    def _track_config_change(self, key: str, old_value: Any, new_value: Any, source: str):
        self.change_history.append({
            "timestamp": datetime.now().isoformat(),
            "key": key, "old": old_value, "new": new_value, "source": source
        })

    def _notify_change_listeners(self, key: str, value: Any):
        if not self.advanced_mode.is_feature_enabled(AdvancedFeature.DYNAMIC_UPDATES):
            return
        for listener in self.change_listeners:
            try:
                listener(key, value)
            except Exception as e:
                self.logger.warning(f"Listener error: {e}")

    def add_change_listener(self, listener: Callable[[str, Any], None]):
        """Add configuration change listener"""
        if self.advanced_mode.is_feature_enabled(AdvancedFeature.DYNAMIC_UPDATES):
            self.change_listeners.append(listener)
    
    def get_all(self, include_secrets: bool = False) -> Dict[str, Any]:
        result = {}
        with self._lock:
            for key, val in self.config_values.items():
                if val.is_secret and not include_secrets:
                    result[key] = "***HIDDEN***"
                else:
                    result[key] = val.value
        return result

if __name__ == "__main__":
    print("ConfigManager module loaded.")
