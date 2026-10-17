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

import os
import re
import shutil
from pathlib import Path
from typing import Optional, List, Tuple
from dataclasses import dataclass
from enum import Enum

from orchestrator.utils.logging import get_logger

# ============================================================================
# DATA MODELS
# ============================================================================

DISK_FULL_PATTERN = r"No space left on device|Disk quota exceeded"


class HealingStrategy(Enum):
    """Definiert die Art der Reparaturmaßnahme."""
    CONFIG_ADJUSTMENT = "config_adjustment"
    DOCKER_PRUNE = "docker_prune"  # a full Docker disk: a manual step (the doctor's list), never a deletion here
    RETRY_BUILD = "retry_build"
    MANUAL_INTERVENTION = "manual_intervention"
    PATCH_CODE = "patch_code"


# The strategies the framework applies itself after the person's yes (GUI dialog, CLI prompt, MCP resolve_healing).
# None of them deletes anything: deleting needs the person's question with the full list (round 5 f), so a full
# Docker disk (docker_prune) is a manual step that points to the one deletion path for the framework's Docker images
# (the GUI's Tools > Framework Build Images..., also a button in the healing dialog, and 'llm-cli doctor --fix --tier
# 2'): it lists every framework image with name, id, size and what it is, and deletes only those the person ticks and
# confirms. Docker's build cache is never cleared: it belongs to every project on the computer.
AUTOMATIC_STRATEGIES = frozenset({HealingStrategy.CONFIG_ADJUSTMENT.value, HealingStrategy.RETRY_BUILD.value})

# What the person does about a full Docker disk (the proposal's manual step).
DOCKER_DISK_STEP = ("Free space on Docker's disk yourself, then start the build again: Tools > Framework Build "
                    "Images... in the GUI or 'llm-cli doctor --fix --tier 2' (in a terminal) lists the framework's "
                    "build images with name, id, size and what each is, and deletes exactly those you tick and "
                    "confirm; nothing is ticked by default. Docker's build cache is not touched by the framework: it "
                    "belongs to every project on this computer ('docker system df' shows its size).")

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
                # The compiler was killed while building the image (OOM): fewer parallel jobs help.
                r"(?:^|\n)Image build failed[\s\S]*(?:internal compiler error.*(?-i:Killed)|Killed signal terminated program|"
                r"\b(?:cc1plus|cc1|collect2|ld)\b.*(?-i:Killed))",
                "Out of memory while compiling the build image.",
                "Lower the parallel compile jobs for image builds to 1 (setting build_jobs) and retry.",
                HealingStrategy.CONFIG_ADJUSTMENT,
                "HOST"
            ),
            (
                # The conversion itself was killed (SIGKILL / exit code 137), usually by the OOM killer.
                r"\b(?:exit(?:ed)? (?:code|status)|code):? 137\b|\bline \d+: +\d+ (?-i:Killed)\b",
                "The conversion was killed, most likely out of memory.",
                "Free memory or add swap, close other programs, or choose a smaller model or quantization. "
                "Large models need roughly their size in RAM during conversion.",
                HealingStrategy.MANUAL_INTERVENTION,
                "HOST"
            ),
            (
                # Which disk is full decides the proposal (_disk_full_proposal): pruning Docker frees only Docker's
                # data root, never the model cache or the output folder (G40).
                DISK_FULL_PATTERN,
                "Disk full.",
                DOCKER_DISK_STEP,
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
            if re.search(pattern, log, re.IGNORECASE):
                # Special Case: CMake missing dependencies usually require AI to find the package name
                if "CMake Error" in log and "Could not find" in log:
                    return None # Let AI handle specific package names
                
                if pattern == DISK_FULL_PATTERN:
                    return self._disk_full_proposal()
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

    # --- a full disk: which one? ----------------------------------------------------------------------------------

    def _config(self, key: str, default=None):
        config = getattr(self.framework, "config", None)
        get = getattr(config, "get", None)
        try:
            return get(key, default) if callable(get) else default
        except Exception:
            return default

    def _folders(self) -> List[Tuple[str, Path]]:
        """The framework's data folders by label (never shown as paths)."""
        resolve = getattr(self.framework, "resolve_path", None)
        found = []
        for label, key in (("model cache", "cache_dir"), ("models folder", "models_dir"), ("output folder", "output_dir"),
                           ("build folder", "build_dir")):
            value = self._config(key)
            if not value:
                continue
            try:
                path = Path(resolve(value)) if callable(resolve) else Path(str(value)).expanduser()
            except Exception:
                continue
            if all(path != seen for _, seen in found):  # the build folder may be the output folder
                found.append((label, path))
        return found

    def _docker_root(self) -> Optional[Path]:
        getter = getattr(self.framework, "get_component", None)
        client = getter("docker_client") if callable(getter) else None
        try:
            root = client.info().get("DockerRootDir") if client is not None else None
        except Exception:  # a remote daemon or no permission: unknown
            return None
        return Path(root) if isinstance(root, str) and root else None

    @staticmethod
    def _folder_size(path: Path, limit: int = 200000) -> Optional[int]:
        total, seen = 0, 0
        try:
            for root, _dirs, files in os.walk(path):
                for name in files:
                    seen += 1
                    if seen > limit:
                        return None
                    try:
                        total += os.lstat(os.path.join(root, name)).st_size
                    except OSError:
                        continue
        except OSError:
            return None
        return total

    def _disk_full_proposal(self) -> HealingProposal:
        """A full disk: pruning Docker is proposed only when Docker's data root is the full disk; otherwise a manual
        step names the framework's folders on the full disk with their sizes (G40)."""
        from orchestrator.Core.disk_plan import device_of, gb, reserve_for

        def short(path: Path) -> Optional[Tuple[bool, int]]:
            probe = path
            while not probe.exists() and probe.parent != probe:
                probe = probe.parent
            try:
                usage = shutil.disk_usage(probe)
            except OSError:
                return None
            return usage.free < reserve_for(usage.total), usage.free

        root = self._docker_root()
        docker = short(root) if root is not None else None
        rows, full_devices = [], set()
        for label, path in self._folders():
            state = short(path)
            if state is None:
                continue
            if state[0]:
                full_devices.add(device_of(path))
                size = self._folder_size(path) if path.exists() else 0
                rows.append(f"{label}: {gb(size) if size is not None else 'size unknown'} (its disk has "
                            f"{gb(state[1])} free)")
        if docker is not None and docker[0] and (not full_devices or device_of(root) in full_devices):
            return HealingProposal(
                summary="Detected known issue: Disk full (Docker's data root).",
                root_cause=f"The disk with Docker's data root is full ({gb(docker[1])} free).",
                fix_command=DOCKER_DISK_STEP, confidence=1.0, strategy=HealingStrategy.DOCKER_PRUNE,
                target_scope="HOST")
        where = "; ".join(rows) if rows else "the full disk could not be determined"
        return HealingProposal(
            summary="Detected known issue: Disk full.",
            root_cause=f"A disk the build writes to is full. {where}.",
            fix_command=("Free space on that disk by hand: remove built models you no longer need from the output "
                         "folder, models from the models folder, or old downloads from the model cache (the framework "
                         "deletes none of them by itself), then start the build again. Pruning Docker would not help: "
                         "Docker's data root is not the full disk." if rows else
                         "Free space on the disks of the model cache, the output folder and Docker's data root by "
                         "hand, then start the build again."),
            confidence=0.9, strategy=HealingStrategy.MANUAL_INTERVENTION, target_scope="HOST")

    def _analyze_via_ai(self, ditto, log: str, context: str) -> Optional[HealingProposal]:
        """Asks Ditto for a validated analysis. AI proposals are always a manual step for the user. Nobody is asked
        before this diagnosis (it runs by itself, also for a build an AI client started), so its knowledge search
        reads with the models on this computer only: it never starts their one-time download (next-fixes 119)."""
        from orchestrator.Core.ditto_manager import DittoError, HealingAnalysis
        from orchestrator.Core.rag_manager import local_models_only
        try:
            with local_models_only():
                analysis = ditto.analyze_error_log(log, context)
            if not isinstance(analysis, HealingAnalysis):
                analysis = HealingAnalysis.model_validate(analysis)  # e.g. a plain dict from another caller
        except DittoError as e:
            self.logger.warning(f"AI analysis unavailable: {e}")
            return None
        except Exception as e:  # an invalid answer is never turned into a proposal
            self.logger.error(f"AI analysis failed: {e}")
            return None

        if not analysis.fix_command.strip():
            self.logger.info("AI could not determine a fix.")
            return None
        # AI proposals are advice for the user: the framework never runs their command itself.
        proposal = HealingProposal(
            summary=analysis.summary,
            root_cause=analysis.root_cause or "Unknown",
            fix_command=analysis.fix_command,
            confidence=analysis.confidence,
            strategy=HealingStrategy.MANUAL_INTERVENTION,
            target_scope=analysis.target,
            is_ai_generated=True
        )
        self.logger.info(f"AI Healing Proposal: {proposal.summary} (Conf: {proposal.confidence})")
        return proposal

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
            if int(config.get("build_jobs", 0) or 0) == 1:
                self.logger.warning("build_jobs is already 1; lowering it further is not possible.")
                return False
            config.set("build_jobs", 1)
            self.logger.info("✅ build_jobs set to 1 for the next image build.")
            return True
                
        # 2. A transient problem: nothing to change, the build is simply retried
        if proposal.strategy == HealingStrategy.RETRY_BUILD:
            return True

        # 3. Everything else (a full Docker disk, AI proposals, code patches, host changes) is a manual step for the
        #    user. Nothing here deletes: freeing Docker's disk is the person's list of the framework's build images
        #    (Tools > Framework Build Images..., 'doctor --fix --tier 2').
        self.logger.warning(f"⚠️ Manual step required: '{proposal.fix_command}'")
        return False
