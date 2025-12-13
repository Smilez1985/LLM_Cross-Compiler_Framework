#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Dataset Manager (v2.4.0)
DIREKTIVE: Goldstandard, Determinismus, Enterprise Quality.

Zweck:
Verwaltet Kalibrierungs-Datasets für die Quantisierung und führt
automatisierte Qualitätsmessungen (Perplexity/PPL) durch.

Updates v2.4.0:
- Added measure_perplexity logic using Docker.
- Added compare_quantizations regression testing logic.
"""

import json
import os
import re
import logging
from pathlib import Path
from typing import List, Optional, Any, Dict, Tuple

from orchestrator.utils.logging import get_logger
from orchestrator.utils.helpers import ensure_directory

class DatasetManager:
    """
    Manages datasets and evaluates model quality (Perplexity).
    """
    
    def __init__(self, framework_manager):
        self.logger = get_logger(__name__)
        self.framework = framework_manager
        self.config = framework_manager.config

    def _get_ditto(self):
        """Retrieves Ditto for generation."""
        if hasattr(self.framework, 'ditto_manager') and self.framework.ditto_manager:
            return self.framework.ditto_manager
        return None

    def _get_docker(self):
        """Retrieves DockerManager for execution."""
        return self.framework.get_component("docker_manager")

    def detect_domain(self, model_path: str) -> Optional[str]:
        """Attempts to deterministically detect the model domain."""
        path = Path(model_path)
        if not path.exists(): return None
            
        marker_file = path / "domain.txt"
        if marker_file.exists():
            try:
                return marker_file.read_text(encoding='utf-8').strip().lower()
            except Exception: pass
        return None

    def generate_synthetic_dataset(self, domain: str, count: int = 50) -> List[str]:
        """Delegates to Ditto."""
        ditto = self._get_ditto()
        if not ditto: raise RuntimeError("AI Agent not available.")
        
        # Ditto logic moved to DittoManager.prepare_calibration_dataset in v2.4.0
        # This method is kept for backwards compatibility or specialized JSON datasets
        self.logger.warning("generate_synthetic_dataset is deprecated. Use DittoManager directly.")
        return []

    def measure_perplexity(self, model_path: Path, dataset_path: Path, context_length: int = 512) -> float:
        """
        Runs a Docker container to measure the Perplexity (PPL) of a GGUF model.
        Lower PPL is better.
        """
        docker = self._get_docker()
        if not docker or not docker.client:
            self.logger.error("Docker not available for PPL measurement.")
            return 999.99

        if not model_path.exists() or not dataset_path.exists():
            self.logger.error(f"Missing model or dataset: {model_path} / {dataset_path}")
            return 999.99

        self.logger.info(f"Measuring Perplexity for {model_path.name}...")

        # We assume standard llama.cpp container logic
        # Mount paths must be absolute
        abs_model = model_path.resolve()
        abs_data = dataset_path.resolve()
        
        # Prepare volumes
        volumes = {
            str(abs_model.parent): {'bind': '/models', 'mode': 'ro'},
            str(abs_data): {'bind': '/data/calibration.txt', 'mode': 'ro'}
        }
        
        # Use runtime image from config or default
        img = getattr(self.config, 'image_inference_runtime', 'ghcr.io/smilez1985/llm-runtime:latest')
        
        # Command: llama-perplexity -m /models/model.gguf -f /data/calibration.txt -c 512
        cmd = [
            "/app/bin/llama-perplexity",
            "-m", f"/models/{abs_model.name}",
            "-f", "/data/calibration.txt",
            "-c", str(context_length),
            "--chunks", "4" # Fast estimation
        ]

        try:
            # Run Container
            logs = docker.client.containers.run(
                img,
                command=cmd,
                volumes=volumes,
                remove=True,
                stdout=True,
                stderr=True # PPL is often printed to stderr
            )
            
            output = logs.decode('utf-8')
            
            # Parse Output for "Final Estimate: PPL = 5.4321"
            # Regex for standard llama.cpp output
            match = re.search(r"Final Estimate: PPL = ([0-9.]+)", output)
            if not match:
                # Try alternate format
                match = re.search(r"perplexity: ([0-9.]+)", output)
                
            if match:
                ppl = float(match.group(1))
                self.logger.info(f"✅ Measured PPL: {ppl}")
                return ppl
            else:
                self.logger.warning(f"Could not parse PPL from output. Output snippet:\n{output[-200:]}")
                return 888.88 # Error code equivalent

        except Exception as e:
            self.logger.error(f"Perplexity measurement failed: {e}")
            return 999.99

    def compare_quantizations(self, base_model: Path, quant_models: List[Path], dataset: Path) -> Dict[str, Any]:
        """
        Runs PPL measurements on a list of quantized models and compares them to a baseline (or each other).
        Returns a report dict.
        """
        results = {}
        
        # 1. Measure Baseline (Optional, if base_model provided and supported)
        # Often base_model is FP16 GGUF.
        base_ppl = None
        if base_model and base_model.exists():
            base_ppl = self.measure_perplexity(base_model, dataset)
            results["baseline"] = {"path": str(base_model), "ppl": base_ppl}

        # 2. Measure Candidates
        candidates = []
        for qm in quant_models:
            ppl = self.measure_perplexity(qm, dataset)
            degradation = 0.0
            if base_ppl and base_ppl > 0:
                # Degradation percentage: (New - Old) / Old * 100
                degradation = ((ppl - base_ppl) / base_ppl) * 100
            
            candidates.append({
                "name": qm.name,
                "path": str(qm),
                "ppl": ppl,
                "degradation_percent": round(degradation, 2)
            })

        # 3. Rank
        candidates.sort(key=lambda x: x["ppl"]) # Lower is better
        results["ranking"] = candidates
        
        # 4. Generate Recommendation
        if candidates:
            best = candidates[0]
            results["recommendation"] = f"Best model is {best['name']} with PPL {best['ppl']}"
        
        return results

    def save_dataset(self, data: List[str], path: Path) -> bool:
        """Saves the dataset to disk."""
        try:
            ensure_directory(path.parent)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            self.logger.error(f"Failed to save dataset: {e}")
            return False
