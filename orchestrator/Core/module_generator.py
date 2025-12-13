#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Module Generator (Core Logic)
DIREKTIVE: Goldstandard, Separation of Concerns.

Zweck:
Zentrale Logik zur Generierung neuer Hardware-Target-Module.
Wird sowohl von der GUI (Wizard) als auch der CLI (Command) genutzt.
Enthält Templates für Dockerfile, target.yml und build.sh.

Updates v1.6.0:
- Knowledge Snapshot Integration: Speichert gelerntes Wissen (RAG) direkt im Modul.
"""

import os
import shutil
import logging
from pathlib import Path
from typing import Dict, List, Any, Optional
import yaml

from orchestrator.utils.logging import get_logger
from orchestrator.utils.helpers import ensure_directory

class ModuleGenerator:
    """
    Generates file structure and content for new hardware targets.
    Uses a template-based approach to ensure consistency across all modules.
    Now capable of attaching Knowledge Snapshots (RAG Data).
    """
    
    def __init__(self, targets_dir: Path):
        """
        Initialize the ModuleGenerator.
        
        Args:
            targets_dir: Base directory where targets are stored (e.g. /app/targets)
        """
        self.logger = get_logger(__name__)
        self.targets_dir = targets_dir
        
        # Template Verzeichnis liegt parallel zu den Targets
        # Wir suchen es relativ zum targets_dir oder via absolutem Fallback
        self.template_dir = targets_dir / "_template"

        if not self.template_dir.exists():
             # Fallback für Development-Umgebung
             possible_path = Path(__file__).parent.parent.parent / "targets" / "_template"
             if possible_path.exists():
                 self.template_dir = possible_path
             else:
                 self.logger.warning(f"Template directory not found at {self.template_dir} or {possible_path}")

    def generate_module(self, data: Dict[str, Any], framework_manager=None) -> Path:
        """
        Generate a complete module from configuration data.
        
        Args:
            data: Dictionary containing configuration keys.
            framework_manager: Optional reference to access Community/RAG Managers for snapshots.
        
        Returns:
            Path: Path to the created module directory
        """
        # Slugify name for folder
        module_name = data.get("module_name", "Unknown")
        module_slug = module_name.lower().replace(" ", "_")
        target_dir = self.targets_dir / module_slug
        
        self.logger.info(f"Generating new target module: '{module_name}' at {target_dir}")
        
        try:
            # 1. Create Directory Structure
            ensure_directory(target_dir)
            ensure_directory(target_dir / "modules")
            ensure_directory(target_dir / "scripts")
            ensure_directory(target_dir / "knowledge") # NEU v1.6.0: Speicherort für RAG Snapshots
            
            # 2. Generate Files via Template Processing
            
            # Core Configuration
            self._process_template("target.yml", target_dir, data)
            
            # Container Definition
            # Check if GPU template is needed
            sdk = data.get("sdk", "").lower()
            if "cuda" in sdk or "nvidia" in data.get("base_os", "").lower():
                self._process_template("Dockerfile.gpu", target_dir, data, target_filename="Dockerfile")
            else:
                self._process_template("Dockerfile", target_dir, data)
            
            # Build Modules (Shell Scripts)
            self._process_template("modules/config_module.sh", target_dir, data)
            self._process_template("modules/source_module.sh", target_dir, data)
            
            # Build Dispatcher
            self._write_build_script(target_dir, data)
            
            # Specialized Modules (Optional copy if template exists)
            self._process_template("modules/rknn_module.sh", target_dir, data)
            self._process_template("modules/rkllm_module.sh", target_dir, data)
            self._process_template("modules/benchmark_module.sh", target_dir, data)
            
            # 3. Generate Helper Scripts (Dynamic)
            self._write_profile_script(target_dir, data)
            
            # 4. Copy Python Scripts (Static Helpers)
            self._copy_scripts(target_dir)
            
            # 5. Create Placeholder/Fallback Modules
            self._write_standard_modules(target_dir)
            
            # 6. Knowledge Snapshot (NEU v1.6.0)
            if framework_manager:
                self._create_knowledge_snapshot(target_dir, framework_manager, module_slug)
            
            self.logger.info(f"Module generation for '{module_name}' completed successfully.")
            return target_dir
            
        except Exception as e:
            self.logger.error(f"Failed to generate module: {e}")
            raise e

    def _create_knowledge_snapshot(self, target_dir: Path, framework_manager, module_slug: str):
        """
        Exports the current RAG knowledge (gathered via Deep Ingest) and saves it
        into the module's knowledge directory for distribution.
        """
        try:
            # Lazy import to prevent circular dependencies at module level
            from orchestrator.Core.community_manager import CommunityManager
            
            # Use existing or temporary CommunityManager to handle the export
            # (We use CommunityManager because it owns the 'export_knowledge_base' logic including sanitization)
            cm = CommunityManager(framework_manager)
            
            self.logger.info("Creating Knowledge Snapshot for module...")
            
            # Export from Qdrant to temp/community dir
            snapshot_path_str = cm.export_knowledge_base(output_name_prefix=f"knowledge_{module_slug}")
            
            if snapshot_path_str and os.path.exists(snapshot_path_str):
                src = Path(snapshot_path_str)
                # Move to target module folder
                dst = target_dir / "knowledge" / "initial_knowledge.json"
                
                shutil.move(src, dst)
                self.logger.info(f"✅ Attached Knowledge Snapshot: {dst}")
                
                # Create a small Readme for the knowledge
                with open(target_dir / "knowledge" / "README.md", "w") as f:
                    f.write(f"# Knowledge Base for {module_slug}\n\n")
                    f.write("This folder contains vectorized knowledge snapshots (JSON).\n")
                    f.write("The framework will automatically ingest these into the local Qdrant instance when using this module.\n")
            else:
                self.logger.warning("No knowledge snapshot created (maybe Qdrant was empty or disabled).")
                
        except Exception as e:
            self.logger.warning(f"Failed to create knowledge snapshot: {e}")
            # Non-fatal error, module creation should still succeed

    def _process_template(self, filename: str, target_dir: Path, data: Dict[str, Any], target_filename: str = None):
        """
        Reads a template file, replaces placeholders with data, and writes to target.
        """
        src = self.template_dir / filename
        dst_name = target_filename if target_filename else filename
        dst = target_dir / dst_name
        
        if not src.exists():
            self.logger.debug(f"Skipping optional template {filename} (not found)")
            return

        try:
            content = src.read_text(encoding="utf-8")
            
            # Prepare Data
            packages = data.get("packages", [])
            if isinstance(packages, str): packages = packages.split()
            packages_str = " \\\n        ".join(packages)
            
            # Replacements Map
            replacements = {
                "[MODULE_NAME]": data.get("module_name", "Unknown"),
                "[Hardware-Familie]": data.get("module_name", "Unknown"),
                "[IHRE_ARCHITEKTUR]": data.get("architecture", "aarch64"),
                "[Hersteller]": "Community",
                "debian:bookworm-slim": data.get("base_os", "debian:bookworm-slim"),
                "{packages_str}": packages_str,
                "# [SDK_SETUP_COMMANDS]": data.get("setup_commands", ""),
                "[QUANTIZATION_LOGIC]": data.get("quantization_logic", ""),
                "# [PACKAGING_COMMANDS]": data.get("packaging_commands", "cp -r build/* $OUTPUT_DIR/"),
                "[CPU_FLAGS]": data.get("cpu_flags", ""),
                "[CMAKE_FLAGS]": data.get("cmake_flags", "")
            }
            
            for key, value in replacements.items():
                if value is not None:
                    content = content.replace(key, str(value))
            
            # YAML Handling
            if dst.suffix == ".yml":
                try:
                    self._update_yaml_config(content, dst, data)
                except:
                    self._write_file(dst, content)
            else:
                self._write_file(dst, content)
                
            # Permissions
            if dst.suffix == ".sh":
                self._make_executable(dst)
                
        except Exception as e:
            self.logger.error(f"Error processing template {filename}: {e}")

    def _update_yaml_config(self, template_content: str, dst: Path, data: Dict[str, Any]):
        """Parses YAML content, updates fields structurally, and saves."""
        config = yaml.safe_load(template_content)
        
        if "metadata" in config:
            config["metadata"]["name"] = data["module_name"]
            config["metadata"]["architecture_family"] = data["architecture"]
            config["metadata"]["sdk"] = data.get("sdk", "none")
            config["metadata"]["description"] = data.get("description", "")
        
        if "docker" in config:
            safe_name = data["module_name"].lower().replace(" ", "-")
            config["docker"]["image_name"] = f"llm-framework/{safe_name}"
        
        with open(dst, "w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    def _write_build_script(self, target_dir: Path, data: Dict[str, Any]):
        """Generates the intelligent 'build.sh' dispatcher script."""
        src = self.template_dir / "modules/build.sh"
        if src.exists():
            self._process_template("modules/build.sh", target_dir, data)
            return

        # Fallback Generator
        quant_logic = data.get("quantization_logic", "")
        if not quant_logic:
            quant_logic = '    *) echo ">> Defaulting to FP16/Copy"; cp -r "$MODEL_SOURCE" "$OUTPUT_DIR/" ;;'

        content = f'''#!/bin/bash
# build.sh for {data["module_name"]}
# Generated by LLM Cross-Compiler Framework

set -euo pipefail

WORK_DIR="${{BUILD_CACHE_DIR:-/build-cache}}"
OUTPUT_DIR="${{WORK_DIR}}/output"
mkdir -p "$OUTPUT_DIR"

echo "=== Build Started: {data['module_name']} ==="
echo "Model: $MODEL_SOURCE"
echo "Task:  ${{MODEL_TASK:-LLM}}"
echo "Quantization: ${{QUANTIZATION:-None}}"

QUANT_TYPE="${{QUANTIZATION:-FP16}}"

case "$QUANT_TYPE" in
{quant_logic}
esac

echo "=== Build Completed ==="
'''
        dst = target_dir / "modules" / "build.sh"
        self._write_file(dst, content)
        self._make_executable(dst)

    def _write_profile_script(self, target_dir: Path, data: Dict[str, Any]):
        """Generates the hardware probe script for the target."""
        content = f'''#!/bin/bash
# Hardware Profile Generator
OUTPUT_FILE="target_hardware_config.txt"
echo "# Profile for {data['module_name']}" > "$OUTPUT_FILE"
{data.get("detection_commands", "lscpu >> $OUTPUT_FILE")}
echo "Generated $OUTPUT_FILE"
'''
        dst = target_dir / "generate_profile.sh"
        self._write_file(dst, content)
        self._make_executable(dst)

    def _copy_scripts(self, target_dir: Path):
        """Copies static python helper scripts to the target."""
        src_scripts = self.template_dir / "scripts"
        dst_scripts = target_dir / "scripts"
        
        if src_scripts.exists():
            ensure_directory(dst_scripts)
            for item in src_scripts.glob("*.py"):
                shutil.copy2(item, dst_scripts / item.name)

    def _write_standard_modules(self, target_dir: Path):
        """Creates placeholder modules."""
        if not (target_dir / "modules/convert_module.sh").exists():
             self._write_script(target_dir / "modules/convert_module.sh", "#!/bin/bash\n# Placeholder\nexit 0")
        if not (target_dir / "modules/target_module.sh").exists():
             self._write_script(target_dir / "modules" / "target_module.sh", "#!/bin/bash\n# Placeholder\nexit 0")

    def _write_script(self, path: Path, content: str):
        self._write_file(path, content)
        self._make_executable(path)

    def _write_file(self, path: Path, content: str):
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

    def _make_executable(self, path: Path):
        try: os.chmod(path, 0o755)
        except: pass
