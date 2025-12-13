#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Target Manager (v2.3.0)
DIREKTIVE: Goldstandard Hardware Management.

Verwaltet Hardware-Zielprofile (Targets).
Liest YAML-Definitionen und importiert Hardware-Probes.
"""

import os
import yaml
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any

from orchestrator.utils.logging import get_logger

class TargetManager:
    def __init__(self, framework_manager):
        self.logger = get_logger("TargetManager")
        self.framework = framework_manager
        
        # Pfade aus Config
        # Fallback für Tests: Wenn framework_manager nur ein Path ist (Legacy)
        if hasattr(framework_manager, 'config'):
            self.targets_dir = Path(framework_manager.config.targets_dir) if hasattr(framework_manager.config, 'targets_dir') else Path("targets")
        else:
            self.targets_dir = Path("targets") # Default/Fallback
            
        self._targets: Dict[str, Any] = {}
        self._initialized = False

    def initialize(self):
        """Lädt alle verfügbaren Targets aus dem targets/ Verzeichnis."""
        self.logger.info(f"Loading targets from {self.targets_dir}...")
        self._targets = {}
        
        if not self.targets_dir.exists():
            self.logger.warning(f"Targets directory {self.targets_dir} does not exist.")
            try:
                self.targets_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                self.logger.error(f"Could not create targets dir: {e}")
                return

        # Scan subdirectories
        for item in self.targets_dir.iterdir():
            if item.is_dir():
                config_file = item / "target.yml"
                if config_file.exists():
                    try:
                        with open(config_file, 'r', encoding='utf-8') as f:
                            data = yaml.safe_load(f)
                            # Basic Validation
                            if 'metadata' in data and 'id' in data['metadata']:
                                tid = data['metadata']['id']
                                self._targets[tid] = data
                                self.logger.debug(f"Loaded target: {tid}")
                            else:
                                self.logger.warning(f"Invalid target config in {item.name}")
                    except Exception as e:
                        self.logger.error(f"Failed to load target {item.name}: {e}")
        
        self.logger.info(f"Loaded {len(self._targets)} targets.")
        self._initialized = True

    def get_target(self, target_id: str) -> Optional[Dict[str, Any]]:
        if not self._initialized: self.initialize()
        return self._targets.get(target_id)

    def list_targets(self) -> List[Dict[str, Any]]:
        if not self._initialized: self.initialize()
        # Return list of metadata for UI/CLI
        return [t['metadata'] for t in self._targets.values()]

    def get_docker_flags_for_profile(self, target_id: str) -> List[str]:
        """
        Gibt die Docker 'run' Flags zurück, die für dieses Target nötig sind.
        (z.B. Device Mapping für NPUs/GPUs).
        """
        target = self.get_target(target_id)
        if not target: return []
        
        flags = []
        hw = target.get('hardware', {})
        
        # 1. GPU Support
        gpu = hw.get('gpu', {}).get('vendor', '').lower()
        if gpu == 'nvidia':
            flags.append("--gpus all")
        elif gpu == 'intel':
            flags.append("--device /dev/dri") # iGPU / Arc
            
        # 2. NPU Support (Manual mappings based on vendor)
        npu = hw.get('npu', {}).get('vendor', '').lower()
        if 'rockchip' in npu:
            flags.append("--device /dev/rknpu")
            flags.append("--device /dev/rga")
        elif 'hailo' in npu:
            flags.append("--device /dev/hailo0")
        elif 'axelera' in npu:
             # Axelera benötigt oft Zugriff auf PCIe/Mem
             flags.append("--privileged") 
        
        return flags

    # --- NEW V2.0 FEATURE: PROBE IMPORT ---

    def import_hardware_profile(self, probe_file: Path) -> Dict[str, Any]:
        """
        Liest eine target_hardware_config.txt (Key=Value) ein und gibt
        ein Dictionary zurück, das vom ModuleGenerator genutzt werden kann.
        """
        if not probe_file.exists():
            self.logger.error(f"Probe file not found: {probe_file}")
            return {}
            
        raw_data = {}
        try:
            with open(probe_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'): continue
                    if '=' in line:
                        k, v = line.split('=', 1)
                        raw_data[k.strip()] = v.strip()
        except Exception as e:
            self.logger.error(f"Failed to parse probe file: {e}")
            return {}

        self.logger.info("Probe data parsed successfully.")
        return raw_data

    def find_matching_target(self, probe_data: Dict[str, str]) -> Optional[str]:
        """
        Versucht, basierend auf Probe-Daten ein existierendes Target zu finden.
        Matching-Logik: Vendor IDs & Device IDs.
        """
        if not self._initialized: self.initialize()

        # Extrahiere IDs aus der Probe
        p_gpu_vid = probe_data.get("GPU_VENDOR_ID", "").lower()
        p_npu_vid = probe_data.get("NPU_VENDOR_ID", "").lower()
        p_cpu_arch = probe_data.get("ARCH", "").lower()

        for tid, tdata in self._targets.items():
            hw = tdata.get('hardware', {})
            
            # Check Arch
            t_arch = hw.get('cpu', {}).get('architecture', '').lower()
            if t_arch and t_arch != p_cpu_arch:
                continue # Architektur passt nicht

            # Check NPU Match (Strong Signal)
            t_npu_vid = hw.get('npu', {}).get('vendor_id', '').lower()
            if t_npu_vid and p_npu_vid and t_npu_vid in p_npu_vid:
                return tid
            
            # Check GPU Match
            t_gpu_vid = hw.get('gpu', {}).get('vendor_id', '').lower()
            if t_gpu_vid and p_gpu_vid and t_gpu_vid in p_gpu_vid:
                return tid

        return None
