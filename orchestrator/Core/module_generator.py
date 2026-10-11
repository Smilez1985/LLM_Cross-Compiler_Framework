#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Module Generator (Core Logic)
DIREKTIVE: Goldstandard, Separation of Concerns.

Zweck:
Zentrale Logik zur Generierung neuer Hardware-Target-Module.
Wird sowohl von der GUI (Wizard) als auch der CLI (Command) genutzt.
Enthält Templates für Dockerfile, target.yml und build.sh.

The generated target.yml carries a capabilities block (support level community) and no template placeholder.
A knowledge snapshot is attached only on request and holds only the pages crawled for this target.
validate_target_folder checks a target folder from anywhere (generated, exported or a community pack).
"""

import os
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Any, Optional
import yaml

from orchestrator.utils.logging import get_logger
from orchestrator.utils.helpers import ensure_directory

# --- Validation of module data (wizard input, AI output, community input) ---------------------------
# Every value below ends up in Dockerfiles, YAML or shell scripts, so it is checked before any
# template is rendered. Shell code fields stay free-form (they are reviewed by the user) but must
# pass a syntax check and a fixed deny-list that no setting can override.

ARCHITECTURES = ("aarch64", "x86_64", "armv7l", "riscv64")
MODULE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")
SDK_RE = re.compile(r"^[\w .+/-]{0,64}$")
DEB_PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]+(?::[a-z0-9-]+)?(?:=[A-Za-z0-9.+~:-]+)?$")
FLAG_RE = re.compile(r"^-[A-Za-z][\w.=+,:/-]*$")
IMAGE_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*(?::[\w][\w.-]{0,127})?(?:@sha256:[a-f0-9]{64})?$")
# Images that may be used without a digest pin; anything else must be pinned by digest.
TRUSTED_IMAGE_PREFIXES = ("debian:", "ubuntu:", "nvidia/cuda:", "rocm/dev-ubuntu-", "rocm/rocm-terminal:", "python:")
DETECTION_RE = re.compile(r"^[\w ./=-]{0,200}$")
SHELL_DENY_PATTERNS = [
    # Generated build code must not fetch anything: downloads belong in a reviewed Dockerfile, and build
    # containers will run without network. This also covers every download-and-execute variant.
    (re.compile(r"\b(?:curl|wget|aria2c|fetch)\b"), "downloads at build time"),
    (re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f?\s+(?:--no-preserve-root\s+)?/(?:\s|$|\*)"), "deletes the root filesystem"),
    (re.compile(r"\bchmod\s+(?:-R\s+)?0?777\b"), "makes files world-writable"),
    (re.compile(r"\bsudo\b"), "uses sudo"),
    (re.compile(r"\b(?:mkfs(?:\.\w+)?|dd\s+if=)"), "writes raw devices"),
    (re.compile(r"/var/run/docker\.sock|\bdocker\s+run\b"), "controls the Docker host"),
    (re.compile(r"(?:^|[;&|]\s*)\s*(?:eval|source|\.)\s+[\"']?\$\(", re.MULTILINE), "evaluates generated code"),
    # Process substitution runs code, except as plain input ('done < <(grep ...)'), unless a shell reads it.
    (re.compile(r"\b(?:ba|z|da)?sh\s+-c\s+[\"']?\$\(|(?<!<\s)(?<!<)<\(\s*\S|\b(?:ba|z|da)?sh\s*<\s*<\("),
     "runs generated code"),
]
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
SHIPPED_TEMPLATE = Path(__file__).resolve().parents[2] / "targets" / "_template"
# Template placeholders that must never survive into a generated module (target.yml is also checked
# for any bracketed placeholder text, see _leftover_placeholders).
TEMPLATE_PLACEHOLDERS = re.compile(  # not after a name: COMPILER_CONFIG[CMAKE_FLAGS] is a bash array entry
    r"(?<![\w$])\[(?:MODULE_NAME|Hardware-Familie|IHRE_ARCHITEKTUR|Hersteller|QUANTIZATION_LOGIC|CPU_FLAGS|CMAKE_FLAGS"
    r"|SDK_SETUP_COMMANDS|PACKAGING_COMMANDS|ihr-target-name)\]|\{packages_str\}|YYYY-MM-DD")
GGUF_DEFAULT_QUANTS = ["Q4_K_M", "Q4_0", "Q5_K_M", "Q6_K", "Q8_0", "F16"]


def module_slug(module_name: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]", "_", module_name.strip().lower().replace(" ", "_")).strip("_-")
    return slug


def validate_module_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Returns a normalized copy of the module data or raises ValueError listing every problem."""
    problems: List[str] = []
    out = dict(data)

    name = str(data.get("module_name", "")).strip()
    if not MODULE_NAME_RE.fullmatch(name):
        problems.append("module_name: letters, digits, space, '_', '.', '-' only (max 64), starting with a letter or digit")
    elif not module_slug(name) or module_slug(name).startswith("_"):
        problems.append("module_name: does not give a usable folder name")
    out["module_name"] = name

    arch = str(data.get("architecture", "aarch64")).strip()
    if arch not in ARCHITECTURES:
        problems.append(f"architecture: one of {', '.join(ARCHITECTURES)}")
    out["architecture"] = arch

    sdk = str(data.get("sdk", "none") or "none").strip()
    if not SDK_RE.fullmatch(sdk):
        problems.append("sdk: short plain name expected")
    out["sdk"] = sdk

    base_os = str(data.get("base_os", "debian:bookworm-slim") or "debian:bookworm-slim").strip()
    if not IMAGE_RE.fullmatch(base_os):
        problems.append("base_os: not a valid image reference")
    elif "@sha256:" not in base_os and not base_os.startswith(TRUSTED_IMAGE_PREFIXES):
        problems.append(f"base_os: '{base_os}' must be pinned by digest (image@sha256:...) unless it is an official base image")
    out["base_os"] = base_os

    packages = data.get("packages", [])
    if isinstance(packages, str):
        packages = packages.split()
    packages = [str(p).strip() for p in packages if str(p).strip()]
    bad = [p for p in packages if not DEB_PACKAGE_RE.fullmatch(p)]
    if bad:
        problems.append(f"packages: invalid Debian package names: {', '.join(bad[:5])}")
    out["packages"] = packages

    for key in ("cpu_flags", "cmake_flags"):
        value = data.get(key, "") or ""
        if isinstance(value, (list, tuple)):
            value = " ".join(str(v) for v in value)
        tokens = str(value).split()
        bad = [t for t in tokens if not FLAG_RE.fullmatch(t)]
        if bad:
            problems.append(f"{key}: only plain compiler/CMake options like -mcpu=cortex-a76 or -DGGML_CUDA=ON: {', '.join(bad[:5])}")
        out[key] = " ".join(tokens)

    boards = data.get("supported_boards") or []
    boards = [str(b).strip() for b in ([boards] if isinstance(boards, str) else boards) if str(b).strip()]
    bad = [b for b in boards if not MODULE_NAME_RE.fullmatch(b) or not module_slug(b)]
    if bad:
        problems.append(f"supported_boards: plain names only (letters, digits, space, '_', '.', '-'): {', '.join(bad[:5])}")
    out["supported_boards"] = boards

    detection = str(data.get("detection_commands", "lscpu") or "lscpu")
    if not DETECTION_RE.fullmatch(detection):
        problems.append("detection_commands: a single simple command line expected")
    out["detection_commands"] = detection

    for key in ("setup_commands", "quantization_logic", "packaging_commands"):
        if key not in data or data.get(key) is None:
            continue
        code = str(data.get(key))
        if _CONTROL_CHARS.search(code):
            problems.append(f"{key}: contains control characters")
        for pattern, reason in SHELL_DENY_PATTERNS:
            if pattern.search(code):
                problems.append(f"{key}: not allowed, {reason}")
        out[key] = code

    if problems:
        raise ValueError("Invalid module definition:\n- " + "\n- ".join(problems))
    return out


def capabilities_block(board_names: List[str], quantization_logic: str = "") -> Dict[str, Any]:
    """The capabilities of a generated target: its boards and GGUF with the types its build.sh handles (the
    labels of its case branches) or common defaults. Support level community: works for its makers."""
    from orchestrator.schemas.targets import TargetCapabilities
    labels = re.findall(r"^\s*([A-Za-z0-9_|\"' ]+)\)", quantization_logic or "", re.MULTILINE)
    quants = [q.strip(" \"'") for label in labels for q in label.split("|")]
    quants = list(dict.fromkeys(q for q in quants if re.fullmatch(r"[A-Za-z0-9_]{1,32}", q))) or list(GGUF_DEFAULT_QUANTS)
    boards = {module_slug(b): {"label": b} for b in board_names}
    block = {"schema_version": 1, "support_level": "community", "default_board": next(iter(boards)),
             "boards": boards,
             "formats": {"gguf": {"label": "GGUF (llama.cpp)", "input": ["hf"], "quantizations": quants,
                                  "default_quantization": quants[0], "imatrix": True, "dataset": "optional"}}}
    TargetCapabilities.model_validate(block)
    return block


def check_shell_syntax(root: Path) -> List[str]:
    """bash -n of every script below root (skipped where bash is missing)."""
    bash = shutil.which("bash")
    if not bash:
        return []
    errors = []
    for script in sorted(Path(root).rglob("*.sh")):
        result = subprocess.run([bash, "-n", str(script)], capture_output=True, text=True)
        if result.returncode != 0:
            errors.append(f"{script.relative_to(root).as_posix()}: shell syntax error: {result.stderr.strip()}")
    return errors


def _yaml_strings(value) -> Iterable[str]:
    if isinstance(value, dict):
        for item in value.values():
            yield from _yaml_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _yaml_strings(item)
    elif isinstance(value, str):
        yield value


def _leftover_placeholders(root: Path) -> List[str]:
    found = []
    for path in sorted(p for p in Path(root).rglob("*") if p.is_file() and not p.is_symlink()):
        text = path.read_text(encoding="utf-8", errors="replace")
        match = TEMPLATE_PLACEHOLDERS.search(text)
        if match is None and path.name == "target.yml":
            # Any bracketed text in a value, e.g. "[Board Name 1]" (YAML lists are not strings).
            try:
                strings = list(_yaml_strings(yaml.safe_load(text)))
            except yaml.YAMLError:
                strings = []
            match = next((m for m in (re.search(r"\[[^\]\n]*[A-Za-z][^\]\n]*\]", s) for s in strings) if m), None)
        if match:
            found.append(f"{path.relative_to(root).as_posix()}: {match.group(0)}")
    return found


def validate_target_folder(root: Path, template_dir: Path = SHIPPED_TEMPLATE) -> List[str]:
    """Problems of a target folder that others will build (empty: none): target.yml with valid capabilities,
    every Dockerfile FROM pinned by digest unless it is an official base image or an earlier stage, the shell
    deny-list on every script line that is not in the shipped template, bash -n, no template placeholders and
    no binary files. Used for generated modules, community exports and packs in quarantine."""
    from orchestrator.schemas.targets import TargetCapabilities
    root = Path(root)
    problems: List[str] = []
    try:
        meta = yaml.safe_load((root / "target.yml").read_text(encoding="utf-8"))
        TargetCapabilities.model_validate((meta or {}).get("capabilities"))
    except FileNotFoundError:
        problems.append("target.yml: missing")
    except Exception as e:  # YAML error, not a mapping, invalid capabilities
        problems.append(f"target.yml: no valid capabilities block ({str(e).splitlines()[0][:200]})")
    for path in sorted(p for p in root.rglob("*") if p.is_file() and not p.is_symlink()):
        rel = path.relative_to(root).as_posix()
        data = path.read_bytes()
        if b"\0" in data:
            problems.append(f"{rel}: binary file (packs carry text only)")
            continue
        lines = data.decode("utf-8", errors="replace").splitlines()
        if path.name.lower().startswith("dockerfile"):  # the builder also takes a lowercase 'dockerfile'
            stages = set()
            for line in (x for x in lines if not x.lstrip().startswith("#")):
                m = re.match(r"\s*FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?", line, re.IGNORECASE)
                # COPY --from=<image> and RUN --mount=...,from=<image> pull images as well
                refs = [("FROM ", m.group(1))] if m else []
                refs += [("from=", ref) for ref in re.findall(r"(?:--|,)from=([^\s,]+)", line, re.IGNORECASE)]
                for label, image in refs:
                    if image.lower() not in stages and not image.isdigit() and (not IMAGE_RE.fullmatch(image) or (
                            "@sha256:" not in image and not image.startswith(TRUSTED_IMAGE_PREFIXES))):
                        problems.append(f"{rel}: {label}{image} must be pinned by digest (image@sha256:...)")
                if m and m.group(2):
                    stages.add(m.group(2).lower())
        elif path.suffix == ".sh":
            template = template_dir / rel
            known = set(template.read_text(encoding="utf-8", errors="replace").splitlines()) if template.is_file() else set()
            for number, line in enumerate(lines, 1):
                if line in known:
                    continue
                problems += [f"{rel}:{number}: not allowed, {reason}" for pattern, reason in SHELL_DENY_PATTERNS
                             if pattern.search(line)]
    problems += [f"template placeholder left: {p}" for p in _leftover_placeholders(root)]
    return problems + check_shell_syntax(root)


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

    def generate_module(self, data: Dict[str, Any], framework_manager=None,
                        knowledge_roots: Optional[Iterable[str]] = None) -> Path:
        """
        Generate a complete module from configuration data.
        
        Args:
            data: Dictionary containing configuration keys.
            framework_manager: Optional reference to access Community/RAG Managers for snapshots.
            knowledge_roots: Opt-in knowledge snapshot: the root URLs crawled for this target. Only their
                pages are attached (scrubbed); without roots nothing from the knowledge base is attached.
        
        Returns:
            Path: Path to the created module directory
        """
        data = validate_module_data(data)
        module_name = data["module_name"]
        slug = module_slug(module_name)
        targets_root = Path(self.targets_dir).resolve()
        target_dir = (targets_root / slug).resolve()
        if target_dir.parent != targets_root:
            raise ValueError(f"Module folder '{slug}' would be outside {targets_root}.")
        # Never overwrite an existing target (case-insensitive: 'rockchip' must not replace 'Rockchip').
        existing = {p.name.lower() for p in targets_root.iterdir()} if targets_root.exists() else set()
        if slug.lower() in existing:
            raise ValueError(f"A target named '{slug}' already exists; choose another module name.")
        
        self.logger.info(f"Generating new target module: '{module_name}' at {target_dir}")
        
        try:
            # 1. Create Directory Structure
            ensure_directory(target_dir)
            ensure_directory(target_dir / "modules")
            ensure_directory(target_dir / "scripts")
            
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
            
            # 6. Knowledge snapshot: only on request, and only the pages crawled for this target.
            roots = sorted({str(r) for r in knowledge_roots or [] if r})
            if framework_manager and roots:
                self._create_knowledge_snapshot(target_dir, framework_manager, slug, roots)

            # 7. What others would build: capabilities, pinned images, deny-list, bash -n, no placeholders.
            problems = validate_target_folder(target_dir, self.template_dir)
            if problems:
                raise ValueError("The generated module is not usable:\n- " + "\n- ".join(problems[:20]))
            
            self.logger.info(f"Module generation for '{module_name}' completed successfully.")
            return target_dir
            
        except Exception as e:
            self.logger.error(f"Failed to generate module: {e}")
            # The folder did not exist before (checked above): remove the half-written target so it
            # neither shows up as a buildable target nor blocks a corrected retry.
            shutil.rmtree(target_dir, ignore_errors=True)
            raise e

    def _create_knowledge_snapshot(self, target_dir: Path, framework_manager, module_slug: str,
                                   roots: List[str]):
        """Exports the pages crawled from the given root URLs (scrubbed) into the module's knowledge folder."""
        try:
            # Lazy import to prevent circular dependencies at module level
            from orchestrator.Core.community_manager import CommunityManager

            # CommunityManager owns the export and its privacy scrub
            cm = CommunityManager(framework_manager)
            self.logger.info("Creating Knowledge Snapshot for module...")
            snapshot_path_str = cm.export_knowledge_base(output_name_prefix=f"knowledge_{module_slug}",
                                                         root_urls=roots)

            if snapshot_path_str and os.path.exists(snapshot_path_str):
                src = Path(snapshot_path_str)
                dst = ensure_directory(target_dir / "knowledge") / "initial_knowledge.json"
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
        """target.yml from the template: metadata, boards, image name and the capabilities block."""
        config = yaml.safe_load(template_content)
        name = data["module_name"]
        boards = data.get("supported_boards") or [name]
        config.setdefault("metadata", {}).update({
            "name": name, "description": data.get("description") or f"Target for {name}",
            "maintainer": data.get("maintainer") or "community", "created": date.today().isoformat(),
            "architecture_family": data["architecture"], "vendor": data.get("vendor") or "community",
            "sdk": data.get("sdk", "none")})
        config["supported_boards"] = [{"name": b} for b in boards]
        config.setdefault("docker", {})["image_name"] = f"llm-framework/{module_slug(name).replace('_', '-')}"
        config["capabilities"] = capabilities_block(boards, data.get("quantization_logic", ""))

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
