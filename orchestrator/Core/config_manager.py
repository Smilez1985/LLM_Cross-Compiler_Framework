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
"""

import os
import sys
import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Any, Union, Callable, Set, Tuple
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
import threading
import copy
import re
import yaml

from orchestrator.utils.logging import get_logger
from orchestrator.utils.validation import ValidationError, validate_path, validate_config
from orchestrator.utils.helpers import ensure_directory, safe_json_load


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
            except (ValueError, TypeError):
                errors.append(f"Invalid type for {self.field_name}: expected {self.field_type.__name__}")
        
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
        
        self._initialize_core_schemas()
        self._ensure_directories()
        
        self.logger.info(f"Configuration Manager initialized (advanced_mode: {advanced_mode})")
    
    def _detect_config_directory(self) -> Path:
        candidates = [
            Path.cwd() / "configs",
            Path.cwd() / "config", 
            Path.home() / ".llm-framework",
            Path.cwd()
        ]
        for candidate in candidates:
            if candidate.exists() and candidate.is_dir():
                return candidate
        return Path.cwd() / "configs"
    
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
        core_schemas = [
            # Core Paths & Settings
            ConfigSchema("targets_dir", str, True, "targets", "Directory for target definitions"),
            ConfigSchema("models_dir", str, True, "models", "Directory for model storage"),
            ConfigSchema("output_dir", str, True, "output", "Directory for build outputs"),
            ConfigSchema("cache_dir", str, True, "cache", "Directory for cache files"),
            ConfigSchema("logs_dir", str, True, "logs", "Directory for log files"),
            ConfigSchema("log_level", str, False, "INFO", "Logging level", ["regex:^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$"]),
            
            # Build Settings
            ConfigSchema("max_concurrent_builds", int, False, 2, "Maximum concurrent builds", ["min:1", "max:10"]),
            ConfigSchema("build_timeout", int, False, 3600, "Build timeout in seconds", ["min:60"]),
            ConfigSchema("auto_cleanup", bool, False, True, "Enable automatic cleanup"),
            ConfigSchema("docker_registry", str, False, "ghcr.io", "Docker registry URL"),
            ConfigSchema("docker_namespace", str, False, "llm-framework", "Docker namespace"),
            
            # --- NEW V2.3: Centralized Image Definitions (Fixed Hardcoding) ---
            ConfigSchema("image_trivy", str, False, "aquasec/trivy:latest", "Security Scanner Image"),
            ConfigSchema("image_qdrant", str, False, "qdrant/qdrant:v1.16.0", "Vector DB Image"),
            ConfigSchema("image_base_debian", str, False, "debian:bookworm-slim", "Base Build Image"),
            ConfigSchema("image_inference_runtime", str, False, "ghcr.io/smilez1985/llm-runtime:latest", "Inference Image"),
            # ------------------------------------------------------------------

            # GUI & API
            ConfigSchema("gui_theme", str, False, "dark", "GUI theme"),
            ConfigSchema("gui_auto_refresh", bool, False, True, "GUI auto-refresh"),
            ConfigSchema("gui_refresh_interval", int, False, 30, "GUI refresh interval"),
            ConfigSchema("api_enabled", bool, False, False, "Enable API server"),
            ConfigSchema("api_port", int, False, 8000, "API server port"),
            ConfigSchema("api_host", str, False, "127.0.0.1", "API server host"),
            
            # I18N & Security
            ConfigSchema("language", str, False, "en", "Interface Language (en/de)"),
            ConfigSchema("ai_security_level", str, False, "STRICT", "AI Data Leakage Protection Level"),
            
            # Local RAG / Vector Database (v1.5.0)
            ConfigSchema("enable_rag_knowledge", bool, False, False, "Enable local Qdrant Vector DB for AI"),
            
            # Deep Crawler Settings (v1.6.0)
            ConfigSchema("crawler_respect_robots", bool, False, True, "Respect robots.txt rules"),
            ConfigSchema("crawler_max_depth", int, False, 2, "Max recursion depth for crawler"),
            ConfigSchema("crawler_max_pages", int, False, 50, "Max pages to crawl per session"),
            
            # UX History
            ConfigSchema("input_history", list, False, [], "History of user inputs"),
            
            # v2.0.0: Chat & Brain Settings
            ConfigSchema("chat_context_limit", int, False, 4096, "Max tokens for chat context history"),
            ConfigSchema("enable_telemetry", bool, False, False, "Allow anonymous error reporting (GitHub Issues)"),
            ConfigSchema("offline_mode", bool, False, False, "Force offline mode (no external APIs)"),
            ConfigSchema("preferred_tiny_model", str, False, "tinyllama_1b", "Preferred local model for offline intelligence")
        ]
        
        for schema in core_schemas:
            self.config_schemas[schema.field_name] = schema
    
    def load_configuration(self, config_file: Optional[Path] = None) -> bool:
        try:
            self._load_default_configuration()
            if config_file:
                self._load_config_file(config_file)
            else:
                self._load_config_files_auto()
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
            Path.cwd() / "framework.yml"
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
        for key, value in config_data.items():
            schema = self.config_schemas.get(key)
            if schema and schema.enterprise_only and not self.advanced_mode.enabled:
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
                if schema and schema.enterprise_only and not self.advanced_mode.enabled:
                    continue
                
                try:
                    val = self._convert_env_value(env_value, schema)
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
            if self.advanced_mode.enabled:
                self._track_config_change(key, old.value if old else None, value, source_type)
            if self.advanced_mode.is_feature_enabled(AdvancedFeature.DYNAMIC_UPDATES):
                self._notify_change_listeners(key, value)
        return True
    
    def save_user_config(self):
        """
        Saves current configuration to config.yml (User Scope).
        Updated v2.0.0: Includes chat, telemetry and offline mode settings.
        """
        config_file = self.config_dir / "config.yml"
        data = {}
        
        if config_file.exists():
            try:
                with open(config_file, 'r') as f:
                    loaded = yaml.safe_load(f)
                    if loaded: data = loaded
            except: pass
            
        with self._lock:
            persist_keys = [
                "language", "ai_security_level", "gui_theme", "docker_registry",
                "enable_rag_knowledge", 
                "crawler_respect_robots", "crawler_max_depth", "crawler_max_pages",
                "input_history",
                # v2.0
                "chat_context_limit", "enable_telemetry", "offline_mode", "preferred_tiny_model",
                # v2.3
                "image_trivy", "image_qdrant", "image_base_debian", "image_inference_runtime"
            ]
            
            for key, val in self.config_values.items():
                if key in persist_keys:
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
