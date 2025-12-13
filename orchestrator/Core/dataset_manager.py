#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Dataset Manager
DIREKTIVE: Goldstandard, Determinismus, Enterprise Quality.

Zweck:
Verwaltet Kalibrierungs-Datasets für die Quantisierung.
Stellt sicher, dass keine "geratenen" Daten verwendet werden.
"""

import json
import os
import logging
from pathlib import Path
from typing import List, Optional, Any

from orchestrator.utils.logging import get_logger
from orchestrator.utils.helpers import ensure_directory

class DatasetManager:
    """
    Manages the lifecycle of quantization datasets.
    Enforces deterministic domain detection or user input.
    """
    
    def __init__(self, framework_manager):
        self.logger = get_logger(__name__)
        self.framework = framework_manager
        self.config = framework_manager.config

    def _get_ditto(self):
        """
        Retrieves the DittoCoder instance via framework or creates a temporary one.
        Used for synthetic data generation.
        """
        # Access existing instance from framework if available
        if hasattr(self.framework, 'ditto_manager') and self.framework.ditto_manager:
            return self.framework.ditto_manager
            
        # Lazy instantiation fallback (using config from manager)
        try:
            from orchestrator.Core.ditto_manager import DittoCoder
            return DittoCoder(config_manager=self.framework.config)
        except ImportError:
            self.logger.error("DittoManager (AI) not available for dataset generation.")
            return None

    def detect_domain(self, model_path: str) -> Optional[str]:
        """
        Attempts to DETERMINISTICALLY detect the model domain from metadata.
        
        Strict Policy:
        - No name guessing (e.g. no "contains 'code'").
        - Checks for explicit 'domain.txt' marker file.
        - Checks for explicit metadata in 'config.json' (if standardized keys exist).
        
        Returns:
            str: The detected domain (e.g. 'code', 'chat', 'medical').
            None: If no explicit information is found (triggers User Query).
        """
        path = Path(model_path)
        if not path.exists():
            return None
            
        # 1. Explicit Marker File
        marker_file = path / "domain.txt"
        if marker_file.exists():
            try:
                domain = marker_file.read_text(encoding='utf-8').strip().lower()
                if domain:
                    self.logger.info(f"Domain detected via marker file: {domain}")
                    return domain
            except Exception as e:
                self.logger.warning(f"Failed to read domain.txt: {e}")

        # 2. config.json Metadata (Safe check only)
        config_file = path / "config.json"
        if config_file.exists():
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Check for custom framework tags if they exist
                    if "framework_domain" in data:
                        return data["framework_domain"]
                    # Check for task specific params (HuggingFace standard)
                    if "task_specific_params" in data:
                        # This is a weak hint, but standard.
                        # We log it but return None to be safe unless we are sure.
                        self.logger.debug(f"Found task params: {list(data['task_specific_params'].keys())}")
            except Exception:
                pass
        
        # No guessing allowed.
        return None

    def generate_synthetic_dataset(self, domain: str, count: int = 50) -> List[str]:
        """
        Uses Ditto (AI) to generate representative calibration data for a specific domain.
        This is only called after the user (or file) has confirmed the domain.
        """
        ditto = self._get_ditto()
        if not ditto:
            raise RuntimeError("AI Agent not available.")
            
        self.logger.info(f"Requesting AI generation for domain: '{domain}' (Count: {count})")
        
        # Delegate to DittoManager
        # Note: DittoManager needs to implement 'generate_dataset_content'
        if hasattr(ditto, 'generate_dataset_content'):
            return ditto.generate_dataset_content(domain, count)
        else:
            raise NotImplementedError("DittoManager does not support dataset generation yet.")

    def save_dataset(self, data: List[str], path: Path) -> bool:
        """Saves the dataset to disk in RKLLM-compatible JSON format."""
        try:
            ensure_directory(path.parent)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            self.logger.info(f"Dataset successfully saved to {path}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to save dataset: {e}")
            return False

    def validate_dataset_file(self, path: Path) -> bool:
        """Checks if a file is a valid JSON list of strings (Schema Validation)."""
        if not path.exists():
            self.logger.error(f"Dataset file not found: {path}")
            return False
            
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = json.load(f)
            
            if not isinstance(content, list):
                self.logger.error("Dataset invalid: Root must be a JSON list.")
                return False
                
            if len(content) == 0:
                self.logger.warning("Dataset is empty.")
                return False
                
            if not isinstance(content[0], str):
                self.logger.error("Dataset invalid: Items must be strings.")
                return False
                
            return True
        except json.JSONDecodeError:
            self.logger.error("Dataset invalid: Malformed JSON.")
            return False
        except Exception as e:
            self.logger.error(f"Dataset validation error: {e}")
            return False
