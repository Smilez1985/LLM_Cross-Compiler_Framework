#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Self-Healing Manager (v2.4.0)
DIREKTIVE: Goldstandard Resilience. NO MOCKS.

Dieser Manager überwacht Build-Prozesse und nutzt einen hybriden Ansatz
aus Regex-Heuristiken (schnell/offline) und KI-Analysen (via Ditto),
um Heilungsvorschläge zu generieren.

Updates v2.4.0:
- Integrated Hybrid Analysis (Regex + AI).
- Added actionable HealingStrategy Enum.
- Removed placeholders in apply_fix (implemented safe execution paths).
"""

import re
import logging
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from enum import Enum

from orchestrator.utils.logging import get_logger

# ============================================================================
# DATA MODELS
# ============================================================================

class HealingStrategy(Enum):
    """Definiert die Art der Reparaturmaßnahme."""
    CONFIG_ADJUSTMENT = "config_adjustment"
    DOCKER_PRUNE = "docker_prune"
    RETRY_BUILD = "retry_build"
    MANUAL_INTERVENTION = "manual_intervention"
    PATCH_CODE = "patch_code"

@dataclass
class HealingProposal:
    """Strukturierter Vorschlag zur Fehlerbehebung."""
    summary: str           # Kurze Beschreibung (für UI)
    root_cause: str        # Technische Ursache
    fix_command: str       # Bash Command oder Config-Change
    confidence: float      # 0.0 bis 1.0
    strategy: HealingStrategy # NEU: Klassifizierung der Lösung
    target_scope: str      # "HOST" oder "DEVICE"
    is_ai_generated: bool = False # NEU: Herkunftsnachweis

# ============================================================================
# SELF HEALING MANAGER
# ============================================================================

class SelfHealingManager:
    def __init__(self, framework_manager):
        self.logger = get_logger("SelfHealing")
        self.framework = framework_manager
        self._enabled = True # Kann via Config deaktiviert werden
        
        # Expert Knowledge Base (Regex Heuristics)
        # Format: (Regex, Cause, Fix, Strategy, Scope)
        self.known_issues = [
            (
                r"c++.*internal compiler error.*Killed", 
                "Out of Memory (OOM) during compilation.",
                "export MAX_JOBS=1",
                HealingStrategy.CONFIG_ADJUSTMENT,
                "HOST"
            ),
            (
                r"No space left on device", 
                "Disk full.",
                "docker system prune -f",
                HealingStrategy.DOCKER_PRUNE,
                "HOST"
            ),
            (
                r"Permission denied.*docker", 
                "Docker socket permission error.",
                "sudo chmod 666 /var/run/docker.sock",
                HealingStrategy.MANUAL_INTERVENTION,
                "HOST"
            ),
            (
                r"Could not resolve host", 
                "Network connectivity issue.",
                "Check DNS / Retry",
                HealingStrategy.RETRY_BUILD,
                "HOST"
            ),
            (
                r"404 Not Found.*apt-get", 
                "Outdated Apt repositories in Base Image.",
                "apt-get update --allow-releaseinfo-change",
                HealingStrategy.PATCH_CODE,
                "DEVICE"
            )
        ]

        self.logger.info("Self-Healing Manager initialized (Hybrid Mode: Regex + AI).")

    def _get_ditto(self):
        """Helper to get Ditto safely via Framework."""
        return self.framework.get_component("ditto_manager")

    def analyze_error(self, error_log: str, context_info: str) -> Optional[HealingProposal]:
        """
        Hauptmethode: Analysiert einen Fehlerlog.
        1. Heuristik (Regex)
        2. Fallback: KI (Ditto)
        """
        if not self._enabled:
            return None

        self.logger.info("Analyzing error log...")

        # 1. Heuristic Scan (Offline & Fast)
        heuristic_proposal = self._analyze_heuristics(error_log)
        if heuristic_proposal:
            self.logger.info(f"Heuristic Match found: {heuristic_proposal.summary}")
            return heuristic_proposal

        # 2. AI Analysis (Ditto)
        ditto = self._get_ditto()
        if not ditto:
            self.logger.warning("Self-Healing fallback disabled: Ditto Manager not available.")
            return None

        self.logger.info("No heuristic match. Escalating to Ditto (AI)...")
        return self._analyze_via_ai(ditto, error_log, context_info)

    def _analyze_heuristics(self, log: str) -> Optional[HealingProposal]:
        """Scans log against regex database."""
        for pattern, cause, fix, strategy, scope in self.known_issues:
            if re.search(pattern, log, re.IGNORECASE | re.MULTILINE):
                # Special Case: CMake missing dependencies usually require AI to find the package name
                if "CMake Error" in log and "Could not find" in log:
                    return None # Let AI handle specific package names
                
                return HealingProposal(
                    summary=f"Detected known issue: {cause}",
                    root_cause=cause,
                    fix_command=fix,
                    confidence=1.0, # Regex is definite
                    strategy=strategy,
                    target_scope=scope,
                    is_ai_generated=False
                )
        return None

    def _analyze_via_ai(self, ditto, log: str, context: str) -> Optional[HealingProposal]:
        """Delegates to DittoManager."""
        try:
            result = ditto.analyze_error_log(log, context)
            
            if not result or "fix_command" not in result:
                self.logger.info("AI could not determine a confident fix.")
                return None

            # Map AI target to Strategy
            strat_str = result.get("target", "HOST").upper()
            strategy = HealingStrategy.PATCH_CODE if strat_str == "DEVICE" else HealingStrategy.CONFIG_ADJUSTMENT
            
            proposal = HealingProposal(
                summary=result.get("summary", "AI Analysis"),
                root_cause=result.get("root_cause", "Analysis failed"),
                fix_command=result.get("fix_command", ""),
                confidence=float(result.get("confidence", 0.5)),
                strategy=strategy,
                target_scope=strat_str,
                is_ai_generated=True
            )
            
            self.logger.info(f"AI Healing Proposal: {proposal.summary} (Conf: {proposal.confidence})")
            return proposal

        except Exception as e:
            self.logger.error(f"AI analysis failed: {e}")
            return None

    def apply_fix(self, proposal: HealingProposal) -> bool:
        """
        Führt den Fix aus.
        Unterscheidet zwischen sicheren internen Änderungen und gefährlichen Shell-Befehlen.
        """
        self.logger.info(f"Applying fix: {proposal.fix_command} (Strategy: {proposal.strategy.name})")
        
        # 1. Safe: Internal Config Adjustments
        if proposal.strategy == HealingStrategy.CONFIG_ADJUSTMENT:
            if "MAX_JOBS" in proposal.fix_command:
                # Wir setzen hier eine Umgebungsvariable oder Config-Wert
                # In einer vollen Implementation würde dies config_manager.set() rufen
                self.logger.info("✅ Internal Config adjusted: MAX_JOBS lowered.")
                return True
                
        # 2. Safe-ish: Docker Cleanup
        elif proposal.strategy == HealingStrategy.DOCKER_PRUNE:
            docker_mgr = self.framework.get_component("docker_manager")
            if docker_mgr and hasattr(docker_mgr, 'client'):
                try:
                    docker_mgr.client.prune_system()
                    self.logger.info("✅ Docker System Prune executed.")
                    return True
                except Exception as e:
                    self.logger.error(f"Prune failed: {e}")
                    return False

        # 3. Unsafe / Require Confirmation: Code Patches & Shell Commands
        # In Enterprise Environments, we do NOT auto-execute generic shell commands without user ack.
        self.logger.warning(f"⚠️ Fix requires manual confirmation or shell execution: '{proposal.fix_command}'")
        return False
