#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Framework Core (v2.4.0)
DIREKTIVE: Goldstandard, Central Dependency Injection.

Der Kernel des Systems. Initialisiert alle Manager in strikter,
sicherheitskritischer Reihenfolge und verknüpft die Komponenten.

Updates v2.4.0:
- Dependency Injection für DittoManager in Orchestrator (für IMatrix-Flow).
- Version Bump auf v2.4.0 (Smart Calibration Update).
"""

import os
import sys
import logging
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Dict, Any

# Utils
from orchestrator.utils.logging import setup_logging, get_logger
from orchestrator.utils.telemetry import TelemetryManager
from orchestrator.utils.updater import UpdateManager

# Managers
from orchestrator.Core.config_manager import ConfigManager
from orchestrator.Core.secrets_manager import SecretsManager
from orchestrator.Core.docker_manager import DockerManager
from orchestrator.Core.target_manager import TargetManager
from orchestrator.Core.model_manager import ModelManager
from orchestrator.Core.rag_manager import RAGManager
from orchestrator.Core.ditto_manager import DittoCoder
from orchestrator.Core.self_healing_manager import SelfHealingManager
from orchestrator.Core.deployment_manager import DeploymentManager
from orchestrator.Core.community_manager import CommunityManager
from orchestrator.Core.orchestrator import LLMOrchestrator

@dataclass
class FrameworkInfo:
    name: str = "LLM Cross-Compiler Framework"
    version: str = "2.4.0"
    edition: str = "Enterprise"
    installation_path: Path = Path(".")

class FrameworkManager:
    """
    Zentrale Instanz, die den Lebenszyklus aller Sub-Systeme steuert.
    """
    
    def __init__(self, config_file: Optional[str] = None):
        # 0. Basic Setup (Pfade)
        self.app_root = Path(__file__).resolve().parents[2] # Root dir calculation
        self.info = FrameworkInfo(installation_path=self.app_root)
        
        # Init Config & Logger first
        self.config = ConfigManager(config_file)
        setup_logging(self.config.log_level, self.config.logs_dir)
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
        self.updater: Optional[UpdateManager] = None

        self._initialized = False

    def initialize(self) -> bool:
        """Führt die Boot-Sequenz durch."""
        if self._initialized: return True
        
        try:
            # === PHASE 1: SECURITY & BASE ===
            self.logger.info("[Boot Phase 1] Security & Infrastructure")
            
            # 1. Secrets (Keyring) - MUSS ZUERST KOMMEN
            self.secrets_manager = SecretsManager(self)
            if not self.secrets_manager.initialize():
                self.logger.critical("Failed to initialize SecretsManager. Aborting for security reasons.")
                return False

            # 2. Telemetry (Opt-In check happens inside)
            self.telemetry = TelemetryManager(self.config)
            
            # 3. Docker Connectivity
            self.docker_manager = DockerManager(self.config)
            if not self.docker_manager.initialize():
                self.logger.warning("Docker Engine not reachable. Build capabilities will be disabled.")
            
            # === PHASE 2: CORE LOGIC ===
            self.logger.info("[Boot Phase 2] Core Logic Managers")
            
            self.target_manager = TargetManager(self)
            self.target_manager.initialize() # Lädt Profile & Targets
            
            self.model_manager = ModelManager(self.config)
            
            self.community_manager = CommunityManager(self) # Swarm Logic
            
            # === PHASE 3: AI SERVICES ===
            self.logger.info("[Boot Phase 3] AI & Knowledge Services")
            
            # RAG (Optional)
            if self.config.enable_rag_knowledge:
                self.rag_manager = RAGManager(self.config)
                # Async Connect via Thread or check later? For CLI speed, we lazy load or check quick.
                # Hier simple init.
            
            # Ditto (The Brain) - needs Secrets!
            self.ditto_manager = DittoCoder(
                config_manager=self.config,
                framework_manager=self # Pass self to access secrets_manager
            )
            
            # === PHASE 4: GUARDIANS & OPS ===
            self.logger.info("[Boot Phase 4] Guardians & Operations")
            
            # Self-Healing - needs Ditto!
            self.self_healing_manager = SelfHealingManager(self)
            
            # Deployment - needs Targets & Secrets!
            self.deployment_manager = DeploymentManager(self)
            
            # Orchestrator - needs everything
            self.orchestrator = LLMOrchestrator(self.config)
            
            # === DEPENDENCY INJECTION ===
            # Connect the Brain (Ditto) and the Guardian (Self-Healing) to the Orchestrator
            self.orchestrator.inject_self_healing(self.self_healing_manager)
            if self.ditto_manager:
                self.orchestrator.inject_ditto(self.ditto_manager) # NEW: IMatrix Dataset Provider
            
            # Updater
            self.updater = UpdateManager(self)

            self._initialized = True
            self.logger.info("Framework successfully initialized.")
            return True

        except Exception as e:
            self.logger.critical(f"Fatal Error during framework initialization: {e}", exc_info=True)
            return False

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
            "docker_client": self.docker_manager.client if self.docker_manager else None
        }
        return components.get(name)

    def shutdown(self):
        """Graceful Shutdown."""
        self.logger.info("Shutting down Framework...")
        # Close DB connections, stop threads if needed
        if self.telemetry:
            self.telemetry.flush()
        self.logger.info("Shutdown complete.")
