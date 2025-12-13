#!/usr/bin/env python3
"""
Unit Tests für LLM Cross-Compiler Framework
DIREKTIVE: Prüft Kernlogik ohne externe Abhängigkeiten (Docker/Hardware).
"""

import pytest
from pathlib import Path
import sys

# Pfad anpassen, damit Module gefunden werden
sys.path.append(str(Path(__file__).parent.parent))

from orchestrator.Core.framework import FrameworkConfig, SystemRequirements, FrameworkManager
from orchestrator.utils.validation import validate_model_requirements

class TestFrameworkCore:
    
    def test_config_defaults(self):
        """Prüft, ob die Standardkonfiguration sicher und sinnvoll ist."""
        config = FrameworkConfig()
        assert config.log_level == "INFO"
        assert config.max_concurrent_builds == 2
        assert config.docker_registry == "ghcr.io"
        assert config.auto_cleanup is True

    def test_system_requirements(self):
        """Prüft die Definition der Systemanforderungen."""
        reqs = SystemRequirements()
        assert reqs.min_python_version == "3.10"
        assert "docker" in reqs.required_commands
        assert reqs.min_memory_gb >= 8

    def test_framework_initialization_dry(self):
        """Testet die Initialisierung des Managers (ohne Seiteneffekte)."""
        config = FrameworkConfig(targets_dir="./tests/mock_targets")
        manager = FrameworkManager(config)
        
        # Prüfen ob Instanz korrekt erstellt wurde
        assert manager.info.version == "1.0.0"
        assert manager.config.log_level == "INFO"

def test_validation_utils():
    """Prüft die Validierungs-Werkzeuge."""
    # Test: Anforderungen sollten 'transformers' als fehlend melden, wenn wir im minimalen Test-Env sind,
    # oder erfolgreich sein, wenn poetry install lief.
    result = validate_model_requirements()
    assert "valid" in result
    assert isinstance(result["errors"], list)
    assert isinstance(result["warnings"], list)

if __name__ == "__main__":
    sys.exit(pytest.main(["-v", __file__]))
