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
        # Format: (Regex, Cause, Fix, Strategy, Scope). Only the strategy decides what the framework does;
        # the fix text is shown to the user and is never executed as a shell command.
        self.known_issues = [
            (
                # gcc/clang killed by the kernel OOM killer, or a container that exited with 137 (SIGKILL)
                r"internal compiler error.*Killed|Killed signal terminated program|"
                r"(?:cc1plus|cc1|collect2|ld)\b.*\bKilled\b|\b(?:exit(?:ed)? (?:code|status)|code) 137\b",
                "Out of memory during compilation.",
                "Lower the parallel compile jobs for image builds to 1 (setting build_jobs) and retry.",
                HealingStrategy.CONFIG_ADJUSTMENT,
                "HOST"
            ),
            (
                r"No space left on device", 
                "Disk full.",
                "Remove dangling framework images and the Docker build cache, then retry.",
                HealingStrategy.DOCKER_PRUNE,
                "HOST"
            ),
            (
                r"permission denied.*docker\.sock|Got permission denied while trying to connect to the Docker daemon", 
                "Your user may not access the Docker daemon.",
                "Add your user to the 'docker' group (sudo usermod -aG docker $USER, then log in again) "
                "or use rootless Docker. Never make /var/run/docker.sock world-writable.",
                HealingStrategy.MANUAL_INTERVENTION,
                "HOST"
            ),
            (
                r"Could not resolve host|Temporary failure in name resolution", 
                "Network connectivity issue.",
                "Check the network/DNS; the build is retried once.",
                HealingStrategy.RETRY_BUILD,
                "HOST"
            ),
            (
                r"(?:404\s+Not Found|does not have a Release file).*(?:apt|debian|ubuntu)|apt-get.*404", 
                "Outdated apt repositories in the base image.",
                "Update the target's base image (or add 'apt-get update --allow-releaseinfo-change' to its Dockerfile).",
                HealingStrategy.MANUAL_INTERVENTION,
                "HOST"
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

            # AI proposals are advice for the user: the framework never runs their command itself.
            scope = str(result.get("target", "HOST")).upper()
            try:
                confidence = float(result.get("confidence", 0.5))
            except (TypeError, ValueError):
                confidence = 0.5
            if confidence > 1:
                confidence /= 100.0  # some models answer in percent
            
            proposal = HealingProposal(
                summary=str(result.get("summary", "AI Analysis")),
                root_cause=str(result.get("root_cause", "Analysis failed")),
                fix_command=str(result.get("fix_command", "")),
                confidence=min(max(confidence, 0.0), 1.0),
                strategy=HealingStrategy.MANUAL_INTERVENTION,
                target_scope=scope if scope in ("HOST", "DEVICE") else "HOST",
                is_ai_generated=True
            )
            
            self.logger.info(f"AI Healing Proposal: {proposal.summary} (Conf: {proposal.confidence})")
            return proposal

        except Exception as e:
            self.logger.error(f"AI analysis failed: {e}")
            return None

    def apply_fix(self, proposal: HealingProposal) -> bool:
        """
        Applies a fix the user approved. Only the fixed strategies below act; free-text commands
        (from heuristics or AI) are never executed. Returns True only if something was actually done
        (or nothing needs doing before a retry).
        """
        self.logger.info(f"Applying approved fix (Strategy: {proposal.strategy.name})")
        
        # 1. Internal setting: fewer parallel compile jobs after an out-of-memory build
        if proposal.strategy == HealingStrategy.CONFIG_ADJUSTMENT and not proposal.is_ai_generated:
            config = getattr(self.framework, "config", None)
            if config is None or not hasattr(config, "set"):
                return False
            config.set("build_jobs", 1)
            self.logger.info("✅ build_jobs set to 1 for the next image build.")
            return True
                
        # 2. Docker cleanup, limited to what the framework created (dangling labeled images) plus build cache
        if proposal.strategy == HealingStrategy.DOCKER_PRUNE:
            client = self.framework.get_component("docker_client") if self.framework else None
            if client is None:
                self.logger.error("Docker cleanup not possible: no Docker client.")
                return False
            try:
                from orchestrator.Core.builder import IMAGE_LABEL
                client.images.prune(filters={"dangling": True, "label": IMAGE_LABEL})
                client.api.prune_builds()
                self.logger.info("✅ Dangling framework images and the build cache were removed.")
                return True
            except Exception as e:
                self.logger.error(f"Docker cleanup failed: {e}")
                return False

        # 3. A transient problem: nothing to change, the build is simply retried
        if proposal.strategy == HealingStrategy.RETRY_BUILD:
            return True

        # 4. Everything else (AI proposals, code patches, host changes) is a manual step for the user.
        self.logger.warning(f"⚠️ Manual step required: '{proposal.fix_command}'")
        return False
