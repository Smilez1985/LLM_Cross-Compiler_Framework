#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Consistency Manager
DIREKTIVE: Goldstandard, Determinismus, Pre-Flight Checks.

Zweck:
Sichert die Integrität des Builds VOR dem Start.
Prüft Kompatibilität zwischen Hardware-Treibern (Probe) und Software-SDKs (Target Definition).
Verhindert "Doomed to Fail" Builds durch strikte Gates.

Updates v2.0.1:
- Removed hardcoded assumptions.
- Implemented dynamic parsing of Target Dockerfiles to determine required SDK versions.
- Strict mapping of CUDA/Driver versions based on NVIDIA compatibility matrix.
"""

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

from packaging import version
from orchestrator.utils.logging import get_logger

@dataclass
class ConsistencyIssue:
    component: str
    severity: str
    message: str
    detected_value: str
    required_value: str
    suggested_fix: str

class ConsistencyManager:
    """
    The Guardian of the Build Pipeline.
    Validates prerequisites before execution using semantic versioning.
    """
    
    def __init__(self, framework_manager):
        self.logger = get_logger(__name__)
        self.framework = framework_manager
        self.config = framework_manager.config
        
        # NVIDIA Compatibility Matrix (Lookup Table)
        # Maps CUDA Toolkit Version -> Minimum Linux Driver Version
        # Source: https://docs.nvidia.com/deploy/cuda-compatibility/
        self.nvidia_matrix = {
            "11.0": "450.36.06",
            "11.1": "455.32",
            "11.2": "460.27.04",
            "11.3": "465.19.01",
            "11.4": "470.42.01",
            "11.5": "495.29.05",
            "11.6": "510.39.01",
            "11.7": "515.43.04",
            "11.8": "520.61.05",
            "12.0": "525.60.13",
            "12.1": "530.30.02",
            "12.2": "535.54.03",
            "12.3": "545.23.06",
            "12.4": "550.54.14",
            "12.5": "555.42.02",
            "12.6": "560.28.03"
        }

    def check_build_compatibility(self, build_config: Dict[str, Any]) -> List[ConsistencyIssue]:
        """Haupt-Prüfmethode."""
        issues = []
        target = build_config.get("target", "").lower()
        
        # 1. Lade Hardware-Profil (Ist-Zustand)
        hw_profile = self._load_hardware_profile()
        if not hw_profile:
            self.logger.warning("No hardware profile found (target_hardware_config.txt). Skipping consistency checks.")
            # In strict mode, this might be an error, but for flexibility we allow it with warning
            return []

        self.logger.info(f"Running Consistency Check for Target: {target}...")

        # 2. Router zu spezifischen Checks
        if "rockchip" in target:
            issues.extend(self._check_rockchip(hw_profile, build_config))
        elif "nvidia" in target or "cuda" in target:
            issues.extend(self._check_nvidia(hw_profile, build_config, target))
        elif "intel" in target:
            issues.extend(self._check_intel(hw_profile, build_config))

        # 3. Ressourcen Checks
        issues.extend(self._check_resources(hw_profile, build_config))

        return issues

    def _load_hardware_profile(self) -> Optional[Dict[str, str]]:
        """Liest die target_hardware_config.txt aus dem Cache."""
        profile_path = Path(self.config.cache_dir) / "target_hardware_config.txt"
        if not profile_path.exists():
            return None
            
        data = {}
        try:
            with open(profile_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"): continue
                    if "=" in line:
                        key, val = line.split("=", 1)
                        data[key.strip()] = val.strip()
            return data
        except Exception as e:
            self.logger.error(f"Failed to parse hardware profile: {e}")
            return None

    def _extract_version(self, version_str: str) -> str:
        """Extrahiert '1.2.3' aus Strings."""
        if not version_str or version_str == "Unknown": return "0.0.0"
        match = re.search(r'(\d+\.\d+(\.\d+)?)', str(version_str))
        return match.group(1) if match else "0.0.0"

    def _resolve_target_cuda_version(self, target_name: str) -> str:
        """
        Determines the CUDA version required by the target's Dockerfile.
        Scans targets/{name}/Dockerfile for 'FROM ...cuda:X.Y'.
        """
        # Path resolution
        target_dir = Path(self.config.targets_dir) / target_name
        
        # Try Dockerfile.gpu first (Priority), then Dockerfile
        for fname in ["Dockerfile.gpu", "Dockerfile"]:
            fpath = target_dir / fname
            if fpath.exists():
                try:
                    content = fpath.read_text(encoding="utf-8")
                    # Regex for image tag: nvidia/cuda:12.2.2-devel...
                    # Capture X.Y (Major.Minor)
                    match = re.search(r'cuda:(\d+\.\d+)', content)
                    if match:
                        ver = match.group(1)
                        self.logger.debug(f"Detected CUDA Requirement {ver} from {fname}")
                        return ver
                except Exception as e:
                    self.logger.warning(f"Failed to parse {fname}: {e}")
        
        self.logger.warning(f"Could not detect CUDA version in {target_name} Dockerfiles. Assuming baseline 11.8.")
        return "11.8" # Safe fallback

    # --- SPECIFIC CHECKS ---

    def _check_rockchip(self, hw: Dict[str, str], cfg: Dict[str, str]) -> List[ConsistencyIssue]:
        issues = []
        
        if hw.get("NPU_VENDOR") != "Rockchip":
            issues.append(ConsistencyIssue("NPU", "ERROR", "Target is Rockchip, but no NPU detected.",
                hw.get("NPU_VENDOR", "None"), "Rockchip", "Check hardware connection."))
            return issues

        # Quantization check
        quant = cfg.get("quantization", "")
        if "W8A8" in quant and "3588" not in hw.get("NPU_MODEL", ""):
             issues.append(ConsistencyIssue(
                component="NPU Capabilities", severity="WARNING",
                message="W8A8 quantization is optimized for RK3588. May fail on RK3566.",
                detected_value=hw.get("NPU_MODEL", "Unknown"), required_value="RK3588",
                suggested_fix="Use INT8 or Q4_K_M."
            ))

        return issues

    def _check_nvidia(self, hw: Dict[str, str], cfg: Dict[str, str], target_name: str) -> List[ConsistencyIssue]:
        issues = []
        
        # 1. Hardware Presence
        if hw.get("SUPPORTS_CUDA") != "ON":
             issues.append(ConsistencyIssue("GPU", "ERROR", "No CUDA-capable GPU detected.",
                "None", "NVIDIA GPU", "Install NVIDIA Drivers and Container Toolkit."))
             return issues

        # 2. Driver vs SDK Compatibility
        detected_driver_ver = self._extract_version(hw.get("GPU_DRIVER_VERSION", "0.0.0"))
        
        # Dynamic Lookup of required CUDA version from the Target Definition
        target_cuda_ver = self._resolve_target_cuda_version(target_name)
        
        # Lookup minimum driver for that CUDA version
        # We match Major.Minor (e.g. 12.2)
        min_driver_req = self.nvidia_matrix.get(target_cuda_ver)
        
        if not min_driver_req:
             # Try to match Major version (e.g. 12.0 base) if exact match fails
             base_ver = target_cuda_ver.split('.')[0] + ".0"
             min_driver_req = self.nvidia_matrix.get(base_ver, "450.00") # Fallback to old driver

        if version.parse(detected_driver_ver) < version.parse(min_driver_req):
             issues.append(ConsistencyIssue(
                component="NVIDIA Driver", severity="ERROR",
                message=f"Host Driver too old for CUDA {target_cuda_ver} (Container).",
                detected_value=detected_driver_ver, required_value=f">={min_driver_req}",
                suggested_fix=f"Update NVIDIA Drivers to {min_driver_req}+ or downgrade Target CUDA version."
            ))

        return issues

    def _check_intel(self, hw: Dict[str, str], cfg: Dict[str, str]) -> List[ConsistencyIssue]:
        issues = []
        format_type = str(cfg.get("format", "")).lower()
        
        if format_type == "openvino" or "ipex" in format_type:
            has_gpu = (hw.get("SUPPORTS_INTEL_XPU") == "ON")
            has_vnni = (hw.get("SUPPORTS_AVX512_VNNI") == "ON")
            has_amx = (hw.get("SUPPORTS_AMX") == "ON")
            
            if not has_gpu and not (has_vnni or has_amx):
                 issues.append(ConsistencyIssue(
                    component="Intel Acceleration", severity="WARNING",
                    message="No XPU/AMX/VNNI detected. Performance will be degraded.",
                    detected_value="Standard CPU", required_value="Intel Arc/Xeon/Core Ultra",
                    suggested_fix="Switch to standard 'GGUF' format for better compatibility on this CPU."
                ))
        return issues

    def _check_resources(self, hw: Dict[str, str], cfg: Dict[str, str]) -> List[ConsistencyIssue]:
        issues = []
        try:
            total_ram = int(hw.get("Total_RAM_MB", 0))
            model_name = str(cfg.get("model_name", "")).lower()
            
            req = 4000
            if "7b" in model_name: req = 8000
            if "13b" in model_name: req = 16000
            if "70b" in model_name: req = 48000
            
            if total_ram > 0 and total_ram < req:
                 issues.append(ConsistencyIssue(
                    component="RAM", severity="WARNING",
                    message=f"Low RAM for model {model_name}.",
                    detected_value=f"{total_ram} MB", required_value=f">{req} MB",
                    suggested_fix="Ensure Swap is active or choose smaller model."
                ))
        except: pass
        return issues
