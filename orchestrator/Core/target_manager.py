#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Target Manager (v2.3.0)
DIREKTIVE: Goldstandard Hardware Management.

Verwaltet Hardware-Zielprofile (Targets).
Liest YAML-Definitionen und importiert Hardware-Probes.
"""

import yaml
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

from orchestrator.utils.logging import get_logger
from orchestrator.schemas.targets import TargetCapabilities

def read_target_folders(targets_dir: Path, logger=None) -> Dict[str, Tuple[Path, Dict[str, Any]]]:
    """Every loadable target in targets_dir: id -> (folder, data of its target.yml). The id is metadata.id, or the
    folder name when it declares none. The one map from target ids to folders: the target registry, the build
    service, the orchestrator and the build engine all resolve targets through it."""
    found: Dict[str, Tuple[Path, Dict[str, Any]]] = {}
    if not targets_dir.is_dir():
        return found
    for item in sorted(targets_dir.iterdir()):
        if not item.is_dir() or item.name.startswith(("_", ".")) or not (item / "target.yml").exists():
            continue
        try:
            with open(item / "target.yml", 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
        except Exception as e:
            if logger:
                logger.error(f"Failed to load target {item.name}: {e}")
            continue
        if not isinstance(data, dict) or not isinstance(data.get('metadata'), dict):
            if logger:
                logger.warning(f"Invalid target config in {item.name}")
            continue
        tid = str(data['metadata'].get('id') or item.name)
        data['metadata']['id'] = tid
        found[tid] = (item, data)
    return found


def resolve_target_name(name: Optional[str], folders: Dict[str, Path]) -> Optional[str]:
    """The registered id for a name the user typed: the id itself, else its folder name, each first exactly and
    then ignoring the letter case. None for an unknown name."""
    name = (name or "").strip()
    if not name:
        return None
    for matches in (lambda a, b: a == b, lambda a, b: a.casefold() == b.casefold()):
        for tid in folders:
            if matches(tid, name):
                return tid
        for tid, folder in folders.items():
            if matches(folder.name, name):
                return tid
    return None


@dataclass(frozen=True)
class TargetCheck:
    """The result of checking one target (`llm-cli targets validate`, the GUI's Check target): whether it loads, and
    whether its capabilities block passes the schema. `errors` are (field, message) pairs, the field written as in
    target.yml (capabilities.formats.gguf...). A target without a capabilities block loads but its boards, formats
    and quantizations are not checked before a build (`declared` False)."""
    name: str                              # as asked
    target_id: Optional[str] = None        # the registered id; None when no loadable target has that name
    declared: bool = False                 # it has a capabilities block
    errors: Tuple[Tuple[str, str], ...] = ()
    boards: int = 0                        # boards of a valid capabilities block
    formats: Tuple[str, ...] = ()          # formats of a valid capabilities block

    @property
    def found(self) -> bool:
        return self.target_id is not None

    @property
    def valid(self) -> bool:
        """It loads and nothing in it fails the schema (also true without a capabilities block)."""
        return self.found and not self.errors

    @property
    def no_capabilities(self) -> bool:
        return self.found and not self.declared


def check_capabilities(name: str, target_id: str, data: Dict[str, Any]) -> TargetCheck:
    """Checks the capabilities block of a target's data (target.yml) against the schema."""
    from pydantic import ValidationError
    if "capabilities" not in data:
        return TargetCheck(name, target_id)
    try:
        caps = TargetCapabilities.model_validate(data["capabilities"])
    except ValidationError as e:
        errors = []
        for err in e.errors():
            where = ".".join(str(part) for part in err.get("loc", ())) or "capabilities"
            errors.append((f"capabilities.{where}", str(err.get("msg", ""))))
        return TargetCheck(name, target_id, declared=True, errors=tuple(errors))
    return TargetCheck(name, target_id, declared=True, boards=len(caps.boards), formats=tuple(caps.formats))


class TargetManager:
    def __init__(self, framework_manager):
        self.logger = get_logger("TargetManager")
        self.framework = framework_manager
        
        # Pfade aus Config
        # Fallback für Tests: Wenn framework_manager nur ein Path ist (Legacy)
        if hasattr(framework_manager, 'config'):
            configured = getattr(framework_manager.config, 'targets_dir', None) or "targets"
            resolve = getattr(framework_manager, 'resolve_path', None)
            self.targets_dir = resolve(configured) if callable(resolve) else Path(configured)
        else:
            self.targets_dir = Path("targets") # Default/Fallback
            
        self._targets: Dict[str, Any] = {}
        self._capabilities: Dict[str, TargetCapabilities] = {}
        self._folders: Dict[str, Path] = {}  # target id -> its folder (the id may differ from the folder name)
        self._initialized = False

    def initialize(self):
        """Lädt alle verfügbaren Targets aus dem targets/ Verzeichnis."""
        self.logger.info(f"Loading targets from {self.targets_dir}...")
        self._targets = {}
        self._capabilities = {}
        self._folders = {}
        
        if not self.targets_dir.exists():
            self.logger.warning(f"Targets directory {self.targets_dir} does not exist.")
            try:
                self.targets_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                self.logger.error(f"Could not create targets dir: {e}")
                return

        for tid, (folder, data) in read_target_folders(self.targets_dir, self.logger).items():
            self._targets[tid] = data
            self._folders[tid] = folder
            if 'capabilities' in data:
                try:
                    self._capabilities[tid] = TargetCapabilities.model_validate(data['capabilities'])
                except Exception as e:  # pydantic.ValidationError, but keep loading other targets
                    self.logger.error(f"Invalid capabilities in {folder / 'target.yml'}: {e}")
            self.logger.debug(f"Loaded target: {tid}")
        
        self.logger.info(f"Loaded {len(self._targets)} targets.")
        self._initialized = True

    def get_target(self, target_id: str) -> Optional[Dict[str, Any]]:
        if not self._initialized: self.initialize()
        return self._targets.get(target_id)

    def resolve(self, name: Optional[str]) -> Optional[str]:
        """The registered id of a target given by id or folder name, in any letter case (None if unknown)."""
        if not self._initialized: self.initialize()
        return resolve_target_name(name, self._folders)

    def folder_of(self, name: Optional[str]) -> Optional[Path]:
        """The folder of a target given by id or folder name (None if unknown)."""
        tid = self.resolve(name)
        return self._folders.get(tid) if tid else None

    def list_targets(self) -> List[Dict[str, Any]]:
        if not self._initialized: self.initialize()
        # Return list of metadata for UI/CLI
        return [t['metadata'] for t in self._targets.values()]

    def get_capabilities(self, target_id: str) -> Optional[TargetCapabilities]:
        """Validated capabilities of a target (None if it declares none or they are invalid)."""
        if not self._initialized: self.initialize()
        return self._capabilities.get(target_id)

    def validate(self, name: str, reread: bool = False) -> TargetCheck:
        """Checks that the target `name` (id or folder name, any letter case) loads and that its capabilities block
        passes the schema: the one check of `llm-cli targets validate` and the GUI. reread: its target.yml is read
        from disk again first (the GUI checks a file the person may have just edited); a file that no longer loads
        then counts as not found."""
        if not self._initialized: self.initialize()
        tid = self.resolve(name)
        if tid is None:
            return TargetCheck(name)
        data = self._targets.get(tid) or {}
        if reread:
            folder = self._folders.get(tid)
            fresh = read_target_folders(folder.parent, self.logger) if folder is not None else {}
            match = next((d for f, d in fresh.values() if f == folder), None)
            if match is None:
                return TargetCheck(name)
            data = match
        return check_capabilities(name, tid, data)

    def list_hardware_profiles(self) -> List[str]:
        """Target IDs selectable as deployment profiles (see get_docker_flags_for_profile)."""
        if not self._initialized: self.initialize()
        return sorted(self._targets.keys())

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
