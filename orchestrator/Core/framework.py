#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Framework Core (v2.4.0)
DIREKTIVE: Goldstandard, Central Dependency Injection.

Der Kernel des Systems. Initialisiert alle Manager in strikter,
sicherheitskritischer Reihenfolge und verknüpft die Komponenten.

Updates v2.4.0:
- Version Bump auf v2.4.0 (Smart Calibration Update).
"""

import asyncio
import os
import sys
import logging
import threading
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Dict, Any

# Utils
from orchestrator import __version__
from orchestrator.utils.logging import setup_logging, get_logger
from orchestrator.utils.telemetry import TelemetryManager
from orchestrator.utils.updater import UpdateManager

# Managers
from orchestrator.Core.config_manager import ConfigManager
from orchestrator.Core.secrets_manager import SecretsManager
from orchestrator.Core.docker_manager import DockerManager
from orchestrator.Core.builder import BuildEngine
from orchestrator.Core.target_manager import TargetManager
from orchestrator.Core.model_manager import ModelManager
from orchestrator.Core.rag_manager import RAGManager
from orchestrator.Core.ditto_manager import DittoCoder
from orchestrator.Core.self_healing_manager import SelfHealingManager
from orchestrator.Core.deployment_manager import DeploymentManager
from orchestrator.Core.community_manager import CommunityManager
from orchestrator.Core.orchestrator import LLMOrchestrator
from orchestrator.Core.build_service import BuildService
from orchestrator.tools.framework_tools import build_tool_registry

@dataclass
class FrameworkInfo:
    name: str = "LLM Cross-Compiler Framework"
    version: str = __version__
    edition: str = "Enterprise"
    installation_path: Path = Path(".")

class FrameworkManager:
    """
    Zentrale Instanz, die den Lebenszyklus aller Sub-Systeme steuert.
    """
    
    def __init__(self, config_file: Optional[str] = None, log_level: Optional[str] = None):
        # 0. Basic Setup (Pfade)
        self.app_root = Path(__file__).resolve().parents[2] # Root dir calculation
        self.info = FrameworkInfo(installation_path=self.app_root)

        # Init Config & Logger first
        self.config = ConfigManager(config_file)
        level = (log_level or self.config.get("log_level", "INFO") or "INFO").upper()
        setup_logging(level=level if level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL") else "INFO")
        self.logger = get_logger("FrameworkKernel")
        
        self.logger.info(f"Booting {self.info.name} v{self.info.version} ({self.info.edition})...")

        # Manager Placeholders
        self.secrets_manager: Optional[SecretsManager] = None
        self.telemetry: Optional[TelemetryManager] = None
        self.docker_manager: Optional[DockerManager] = None
        self.target_manager: Optional[TargetManager] = None
        self.model_manager: Optional[ModelManager] = None
        self.rag_manager: Optional[RAGManager] = None
        self.ditto_manager: Optional[DittoCoder] = None
        self.self_healing_manager: Optional[SelfHealingManager] = None
        self.deployment_manager: Optional[DeploymentManager] = None
        self.community_manager: Optional[CommunityManager] = None
        self.orchestrator: Optional[LLMOrchestrator] = None
        self.build_service: Optional[BuildService] = None
        self.tool_registry = None
        self.updater: Optional[UpdateManager] = None

        self._initialized = False
        self.init_error: Optional[Exception] = None  # why initialize() failed (e.g. a StorageError)
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def initialize(self) -> bool:
        """Führt die Boot-Sequenz durch."""
        if self._initialized: return True
        
        try:
            # === PHASE 1: SECURITY & BASE ===
            self.logger.info("[Boot Phase 1] Security & Infrastructure")
            
            # 1. Secrets (Keyring) - MUSS ZUERST KOMMEN
            self.secrets_manager = SecretsManager(self)
            if not self.secrets_manager.initialize():
                self.init_error = self.secrets_manager.storage_error
                self.logger.critical("Failed to initialize SecretsManager. Aborting for security reasons.")
                return False

            # 2. Telemetry (Opt-In check happens inside)
            self.telemetry = TelemetryManager(self)
            
            # 3. Docker Connectivity
            # Relative data paths (targets, output, cache, models) resolve against the installation path,
            # never the working directory (the MCP server, for instance, is started from anywhere).
            self.docker_manager = DockerManager(self.config, base_dir=self.info.installation_path,
                                                secrets_manager=self.secrets_manager)
            if not self.docker_manager.initialize():
                self.logger.warning("Docker Engine not reachable. Build capabilities will be disabled.")
            
            # === PHASE 2: CORE LOGIC ===
            self.logger.info("[Boot Phase 2] Core Logic Managers")
            
            self.target_manager = TargetManager(self)
            self.target_manager.initialize() # Lädt Profile & Targets
            
            self.model_manager = ModelManager(self)  # models_dir resolves against the installation
            
            self.community_manager = CommunityManager(self) # Swarm Logic
            
            # === PHASE 3: AI SERVICES ===
            self.logger.info("[Boot Phase 3] AI & Knowledge Services")
            
            # RAG (Optional)
            self.ensure_rag_manager()

            # Ditto (the AI assistant): reads provider, model and keys from the settings on every call.
            self.ditto_manager = DittoCoder(self)
            
            # === PHASE 4: GUARDIANS & OPS ===
            self.logger.info("[Boot Phase 4] Guardians & Operations")
            
            # Self-Healing - needs Ditto!
            self.self_healing_manager = SelfHealingManager(self)
            
            # Deployment - needs Targets & Secrets!
            self.deployment_manager = DeploymentManager(self)
            
            # Orchestrator - needs everything
            build_engine = self.docker_manager.builder or BuildEngine(
                self.config, base_dir=self.info.installation_path, secrets_manager=self.secrets_manager)
            self.orchestrator = LLMOrchestrator(self.config, build_engine=build_engine)
            # Typed entry point for tools (MCP server, Ditto) on top of the orchestrator
            self.build_service = BuildService(self)
            # One tool registry with the user's permissions and the audit log, for Ditto and MCP.
            # Without it (e.g. an unusable per-user folder) only the AI tools are unavailable.
            try:
                self.tool_registry = build_tool_registry(self)
            except Exception as e:
                self.tool_registry = None
                self.logger.error(f"AI tools unavailable (tool permissions/audit log in the per-user folder): {e}")
            
            # === DEPENDENCY INJECTION ===
            # Connect the Guardian (Self-Healing, which asks Ditto) to the Orchestrator
            self.orchestrator.inject_self_healing(self.self_healing_manager)
            
            # Updater
            self.updater = UpdateManager(self.app_root)

            self._initialized = True
            self.logger.info("Framework successfully initialized.")
            return True

        except Exception as e:
            self.logger.critical(f"Fatal Error during framework initialization: {e}", exc_info=True)
            return False

    def run_coroutine(self, coro, timeout: Optional[float] = None):
        """Runs a coroutine on a long-lived background event loop and waits for its result.
        The orchestrator keeps build tasks on its loop, so that loop must outlive the caller (e.g. a GUI slot)."""
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            threading.Thread(target=self._loop.run_forever, name="framework-asyncio", daemon=True).start()
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    def ensure_rag_manager(self) -> Optional[RAGManager]:
        """Creates the RAG manager on demand once RAG is enabled (also at runtime via GUI)."""
        if self.rag_manager is None and self.config.get("enable_rag_knowledge", False):
            self.rag_manager = RAGManager(self)
        return self.rag_manager

    def resolve_path(self, value) -> Path:
        """A configured path; relative ones are relative to the installation path, not the working directory."""
        path = Path(str(value)).expanduser()
        return path if path.is_absolute() else Path(self.info.installation_path) / path

    def get_component(self, name: str) -> Any:
        """Service Locator für CLI/GUI Zugriff."""
        if not self._initialized:
            self.logger.warning("Framework not initialized. Auto-initializing...")
            self.initialize()
            
        components = {
            "secrets_manager": self.secrets_manager,
            "target_manager": self.target_manager,
            "model_manager": self.model_manager,
            "rag_manager": self.rag_manager,
            "ditto_manager": self.ditto_manager,
            "self_healing_manager": self.self_healing_manager,
            "deployment_manager": self.deployment_manager,
            "community_manager": self.community_manager,
            "orchestrator": self.orchestrator,
            "build_service": self.build_service,
            "tool_registry": self.tool_registry,
            "docker_client": getattr(getattr(self.docker_manager, "builder", None), "docker_client", None)
        }
        return components.get(name)

    def shutdown(self):
        """Graceful Shutdown."""
        self.logger.info("Shutting down Framework...")
        if self.rag_manager is not None:
            self.rag_manager.close()  # releases the lock on the embedded knowledge base storage
        self.logger.info("Shutdown complete.")
