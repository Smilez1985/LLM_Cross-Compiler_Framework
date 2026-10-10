#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Build Engine (v2.4.0-IMatrix)
DIREKTIVE: Goldstandard, vollständig, professionell geschrieben.

Core logic for orchestrating Docker builds, container execution, and artifact management.
Handles security scans, GPU passthrough, dynamic volume mounting, and IMatrix generation.

Updates v2.4.0-IMatrix:
- Added IMatrix ("Per-Path") generation logic.
- Two-stage build process: 1. Generate IMatrix (if requested), 2. Build Model.
- Integration with Dataset for importance matrix calculation.
- Robust ConfigManager integration (retained from v2.3).
"""

import os
import sys
import json
import logging
import subprocess
import threading
import time
import shutil
import tempfile
import hashlib
import re
from pathlib import Path
from typing import Dict, List, Optional, Any, Union, Callable, Tuple
from dataclasses import dataclass, field, asdict
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import Enum
import asyncio

import yaml
import docker
from docker.models.containers import Container
from docker.models.images import Image
from docker.types import DeviceRequest

from orchestrator.utils.logging import get_logger
from orchestrator.utils.build_context import stage_build_context

# Files a build may produce that are models (data to install), never programs to execute.
MODEL_FILE_SUFFIXES = (".gguf", ".rkllm", ".rknn", ".onnx", ".safetensors", ".bin")
BUILD_MANIFEST = "build_info.json"

# Fallback Helper if utils module not fully ready during bootstrap
def ensure_directory(path: Path):
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)

class ValidationError(Exception): pass


class BuildCancelled(Exception):
    """The user cancelled the build; not a failure."""

# ============================================================================
# ENUMS AND CONSTANTS
# ============================================================================

class BuildStatus(Enum):
    QUEUED = "queued"
    PREPARING = "preparing"
    BUILDING = "building"
    CALIBRATING = "calibrating" # New status for IMatrix
    CONVERTING = "converting"
    OPTIMIZING = "optimizing"
    PACKAGING = "packaging"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CLEANING = "cleaning"

class ModelFormat(Enum):
    HUGGINGFACE = "hf"
    GGUF = "gguf"
    RKLLM = "rkllm"          # Rockchip NPU LLM (RK3588/RK3576)
    ONNX = "onnx"
    TENSORFLOW_LITE = "tflite"
    PYTORCH_MOBILE = "pytorch_mobile"
    # Hardware-Specific Formats
    RKNN = "rknn"           # Rockchip NPU
    TENSORRT = "tensorrt"   # NVIDIA GPU
    OPENVINO = "openvino"   # Intel NPU/CPU
    COREML = "coreml"       # Apple Silicon
    NCNN = "ncnn"           # Mobile High-Performance
    MNN = "mnn"             # Alibaba Mobile

class OptimizationLevel(Enum):
    FAST = "fast"
    BALANCED = "balanced"
    SIZE = "size"
    SPEED = "speed"
    AGGRESSIVE = "aggressive"

TERMINAL_STATES = ("completed", "failed", "cancelled")
IMAGE_LABEL = "org.llm-framework.target"
# owner/name as used on huggingface.co; no path traversal
HF_REPO_ID = re.compile(r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")

# ============================================================================
# DATA MODELS
# ============================================================================

@dataclass
class BuildConfiguration:
    """Complete build configuration object passed from Manager to Engine."""
    build_id: str
    timestamp: str
    model_source: str
    target_arch: str 
    target_format: ModelFormat
    output_dir: str
    
    # Optional / Defaults
    model_branch: Optional[str] = "main"
    source_format: ModelFormat = ModelFormat.HUGGINGFACE
    target_board: Optional[str] = None
    optimization_level: OptimizationLevel = OptimizationLevel.BALANCED
    quantization: Optional[str] = None
    max_context_length: Optional[int] = None
    custom_flags: List[str] = field(default_factory=list)
    output_name: Optional[str] = None
    include_metadata: bool = True
    base_image: str = "debian:bookworm-slim"
    build_args: Dict[str, str] = field(default_factory=dict)
    dockerfile_template: Optional[str] = None
    parallel_jobs: int = 4
    build_timeout: int = 3600
    cleanup_after_build: bool = True
    enable_hadolint: bool = True
    poetry_version: str = "latest"
    
    # Features
    model_task: str = "LLM" 
    use_gpu: bool = False
    use_imatrix: bool = False # New flag for IMatrix generation
    dataset_path: Optional[str] = None

@dataclass
class BuildProgress:
    """Tracks the state of a single build job."""
    build_id: str
    status: BuildStatus
    current_stage: str
    progress_percent: int = 0
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    logs: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    artifacts: List[str] = field(default_factory=list)
    
    def add_log(self, message: str, level: str = "INFO"):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.logs.append(f"[{timestamp}] [{level}] {message}")
    
    def add_error(self, error: str):
        self.errors.append(error)
        self.add_log(f"ERROR: {error}", "ERROR")
    
    def add_warning(self, warning: str):
        self.warnings.append(warning)
        self.add_log(f"WARNING: {warning}", "WARNING")

# ============================================================================
# BUILD ENGINE CORE CLASS
# ============================================================================

class BuildEngine:
    """
    The core execution engine.
    Responsible for:
    1. Docker Environment Setup
    2. Image Building
    3. IMatrix Generation (Optional)
    4. Container Execution (with GPU/Volume support)
    5. Security Scanning
    6. Artifact Extraction
    """
    
    def __init__(self, config_or_framework, max_concurrent_builds: int = 2, default_timeout: int = 3600,
                 secrets_manager=None, base_dir: Optional[Path] = None):
        self.logger = get_logger("BuildEngine")
        self.max_concurrent_builds = max_concurrent_builds
        self.default_timeout = default_timeout
        
        # --- Robust Initialization ---
        self.framework = None
        self.config = None
        self.secrets_manager = secrets_manager
        
        # Check if we got the full FrameworkManager or just ConfigManager
        if hasattr(config_or_framework, 'info') and hasattr(config_or_framework, 'config'):
            # FrameworkManager
            self.framework = config_or_framework
            self.config = config_or_framework.config
            self.base_dir = Path(config_or_framework.info.installation_path)
            # Try to reuse existing client
            self.docker_client = config_or_framework.get_component("docker_client")
        else:
            # ConfigManager (Standalone)
            self.config = config_or_framework
            self.base_dir = Path(base_dir).resolve() if base_dir else Path(".").resolve()
            self.docker_client = None

        # Docker Client Fallback
        if not self.docker_client:
            try:
                self.docker_client = docker.from_env()
            except Exception as e:
                self.logger.error(f"Docker client not available: {e}")
        
        self._lock = threading.Lock()
        self._builds: Dict[str, BuildProgress] = {}
        self._active_containers: Dict[str, Container] = {}
        self._build_images: Dict[str, str] = {}
        self._executor = ThreadPoolExecutor(max_workers=max_concurrent_builds)
        
        # Paths initialization using centralized config getter
        self.targets_dir = self.base_dir / self._get_conf("targets_dir", "targets")
        self.models_dir = self.base_dir / self._get_conf("models_dir", "models")
        self.output_dir = self.base_dir / self._get_conf("output_dir", "output")
        self.cache_dir = self.base_dir / self._get_conf("cache_dir", "cache")
        
        self._ensure_directories()
        if self.docker_client:
            self._validate_docker_environment()
        
        self.logger.info(f"Build Engine initialized (max_workers: {max_concurrent_builds})")

    def _get_conf(self, key: str, default: Any = None) -> Any:
        """Centralized safe configuration retrieval."""
        if self.config:
            if hasattr(self.config, 'get'):
                return self.config.get(key, default)
            return getattr(self.config, key, default)
        return default
    
    def _ensure_directories(self):
        dirs = [self.targets_dir, self.models_dir, self.output_dir, self.cache_dir, 
                self.cache_dir / "docker", self.cache_dir / "models", self.cache_dir / "tools"]
        for d in dirs: ensure_directory(d)
    
    def _validate_docker_environment(self):
        try:
            self.docker_client.ping()
            if shutil.which("docker"):
                try:
                    subprocess.run(["docker", "buildx", "version"], capture_output=True, check=True)
                except Exception:
                    self.logger.warning("Docker BuildX not available, standard build will be used.")
            else:
                 self.logger.warning("Docker executable not found in PATH.")
        except Exception as e:
            self.logger.warning(f"Docker environment check failed: {e}")

    def list_available_targets(self) -> List[Dict[str, Any]]:
        """Scans targets directory for valid target.yml files."""
        targets = []
        if not self.targets_dir.exists(): return targets
        
        for tp in self.targets_dir.iterdir():
            if tp.is_dir() and not tp.name.startswith("_") and (tp / "target.yml").exists():
                try:
                    with open(tp / "target.yml", "r") as f:
                        meta = yaml.safe_load(f)
                        targets.append({
                            "id": tp.name,
                            "target_arch": meta.get("metadata", {}).get("architecture_family", tp.name),
                            "name": meta.get("metadata", {}).get("name", tp.name),
                            "available": True,
                            "path": str(tp)
                        })
                except Exception: pass
        return targets

    def build_model(self, config: BuildConfiguration) -> str:
        """Submits a new build job."""
        self._validate_build_config(config)
        
        # Check concurrency
        active = len([b for b in self._builds.values() if b.status not in 
                      [BuildStatus.COMPLETED, BuildStatus.FAILED, BuildStatus.CANCELLED]])
        if active >= self.max_concurrent_builds:
            raise RuntimeError("Max concurrent builds reached")
            
        progress = BuildProgress(config.build_id, BuildStatus.QUEUED, "Initializing", start_time=datetime.now())
        
        with self._lock: 
            self._builds[config.build_id] = progress
            
        self._executor.submit(self._execute_build, config)
        self.logger.info(f"Build started: {config.build_id}")
        return config.build_id

    def get_build_status(self, build_id: str) -> Optional[BuildProgress]:
        return self._builds.get(build_id)
    
    def list_builds(self) -> List[BuildProgress]:
        with self._lock: return list(self._builds.values())
    
    def cancel_build(self, build_id: str) -> bool:
        progress = self._builds.get(build_id)
        if not progress or progress.status in [BuildStatus.COMPLETED, BuildStatus.FAILED, BuildStatus.CANCELLED]:
            return False
            
        progress.status = BuildStatus.CANCELLED
        progress.add_log("Build cancelled by user.")
        
        # Stop the running stage container (imatrix or build); _run_container then ends the build.
        with self._lock:
            container = self._active_containers.get(build_id)
        if container:
            try: 
                container.stop(timeout=10)
            except Exception as e:
                self.logger.warning(f"Failed to stop container for {build_id}: {e}")
                
        return True

    @staticmethod
    def _check_cancelled(progress: "BuildProgress"):
        if progress.status == BuildStatus.CANCELLED:
            raise BuildCancelled("Build cancelled by user.")

    def _run_container(self, config: "BuildConfiguration", progress: "BuildProgress", container: Container,
                       prefix: str) -> int:
        """Runs a stage container: streams its log into the progress, enforces the build timeout,
        honours cancellation and always removes the container. Returns the exit code."""
        with self._lock:
            self._active_containers[config.build_id] = container
        try:
            self._check_cancelled(progress)
            container.start()

            def pump():
                try:
                    for line in container.logs(stream=True, follow=True):
                        progress.add_log(f"{prefix}{line.decode(errors='replace').rstrip()}")
                except Exception as e:  # the container may be removed while streaming
                    self.logger.debug(f"Log stream of {config.build_id} ended: {e}")

            reader = threading.Thread(target=pump, name=f"logs-{config.build_id}", daemon=True)
            reader.start()
            timeout = config.build_timeout or self.default_timeout
            try:
                result = container.wait(timeout=timeout)
            except Exception as e:  # docker-py raises a requests timeout/connection error when it expires
                self._check_cancelled(progress)
                try:
                    container.kill()
                except Exception:
                    pass
                raise TimeoutError(f"The {prefix.strip(': ').lower() or 'build'} stage ran longer than "
                                   f"{timeout} s and was stopped (setting build_timeout).") from e
            reader.join(timeout=10)
            self._check_cancelled(progress)
            return int(result.get("StatusCode", 1))
        finally:
            with self._lock:
                self._active_containers.pop(config.build_id, None)
            try:
                container.remove(force=True)
            except Exception:
                pass

    def cleanup_build(self, build_id: str) -> bool:
        progress = self._builds.get(build_id)
        if not progress: return False
        
        previous = progress.status
        try:
            progress.status = BuildStatus.CLEANING
            
            # Remove container
            container = self._active_containers.get(build_id)
            if container:
                try: 
                    container.remove(force=True)
                    with self._lock:
                        del self._active_containers[build_id]
                except Exception: pass

            # Drop the per-build image tag (layers stay in the build cache, so the next build is fast),
            # then dangling framework images, so repeated builds do not fill the disk.
            tag = self._build_images.pop(build_id, None)
            if tag and self.docker_client:
                try:
                    self.docker_client.images.remove(tag)
                    self.docker_client.images.prune(filters={"dangling": True, "label": IMAGE_LABEL})
                except Exception as e:
                    self.logger.debug(f"Image cleanup for {build_id} skipped: {e}")
            
            # Remove temp dirs
            build_temp = self.cache_dir / "builds" / build_id
            if build_temp.exists(): 
                shutil.rmtree(build_temp)
                
            return True
        except Exception: return False
        finally:
            # Pollers wait for a terminal state; cleanup after a failure must not hide it.
            if previous.value in TERMINAL_STATES:
                progress.status = previous
    
    def _execute_build(self, config: BuildConfiguration):
        """Main execution flow running in worker thread."""
        bid = config.build_id
        prog = self._builds[bid]
        
        try:
            prog.status = BuildStatus.PREPARING
            self._resolve_dataset(config, prog)
            
            # 1. Resolve Target Path
            target_path = self.targets_dir / config.target_arch
            if not target_path.exists():
                # Fallback search (case insensitive)
                found = False
                for p in self.targets_dir.iterdir():
                    if p.name.lower() == config.target_arch.lower():
                        target_path = p
                        found = True
                        break
                if not found:
                    raise FileNotFoundError(f"Target {config.target_arch} not found in {self.targets_dir}")

            # 2. Prepare Environment
            self._prepare_build_environment(config, prog, target_path)
            
            # 3. Generate Dockerfile
            df_path = self._generate_dockerfile(config, prog, target_path)
            
            # 4. Build Docker Image
            image = self._build_docker_image(config, prog, df_path)
            self._check_cancelled(prog)

            # 4a. Make the model available inside the containers
            staged = self._stage_model(config, prog)
            self._check_cancelled(prog)
            
            # 5. Security Scan
            self._scan_image_security(image.tags[0], prog)
            
            # 5a. Importance matrix: a llama.cpp (GGUF) concept. Other formats use the dataset directly.
            if config.use_imatrix and config.target_format != ModelFormat.GGUF:
                prog.add_warning(f"IMatrix applies to GGUF only; ignored for {config.target_format.value}.")
            elif config.use_imatrix:
                if config.dataset_path and os.path.exists(config.dataset_path):
                    self._generate_imatrix(config, prog, image, target_path, staged)
                else:
                    prog.add_warning("IMatrix requested but no dataset found. Skipping IMatrix generation.")
            
            # 6. Run Build Modules (Main Conversion/Quantization)
            self._execute_build_modules(config, prog, image, target_path, staged)
            
            # 7. Extract Artifacts
            self._extract_artifacts(config, prog)
            
            # 8. Create Golden Artifact
            self._create_golden_artifact(config, prog)
            
            # 9. Cleanup
            if config.cleanup_after_build: 
                self.cleanup_build(bid)
                
            self._check_cancelled(prog)
            prog.status = BuildStatus.COMPLETED
            prog.end_time = datetime.now()
            prog.progress_percent = 100
            
        except BuildCancelled:
            prog.status = BuildStatus.CANCELLED
            prog.end_time = datetime.now()
            try: self.cleanup_build(bid)
            except Exception: pass
        except Exception as e:
            if prog.status == BuildStatus.CANCELLED:
                # Stopping the container makes the stage fail; that is the cancellation, not an error.
                prog.end_time = datetime.now()
                try: self.cleanup_build(bid)
                except Exception: pass
                return
            prog.status = BuildStatus.FAILED
            prog.end_time = datetime.now()
            prog.add_error(str(e))
            self.logger.error(f"Build {bid} failed: {e}", exc_info=True)
            # Try cleanup even on failure
            try: self.cleanup_build(bid)
            except Exception: pass

    def _validate_build_config(self, config: BuildConfiguration):
        if not config.build_id or not config.model_source or not config.output_dir:
            raise ValidationError("Missing required build config (ID, Source, or Output)")

    def _prepare_build_environment(self, config: BuildConfiguration, progress: BuildProgress, target_path: Path):
        progress.current_stage = "Preparing env"
        progress.progress_percent = 10
        progress.add_log(f"Preparing build environment for {config.target_arch}")
        
        build_temp = self.cache_dir / "builds" / config.build_id
        ensure_directory(build_temp)
        for d in ["output", "logs", "imatrix"]: ensure_directory(build_temp / d)
        
        # Dump config for debugging
        with open(build_temp / "build_config.json", 'w') as f:
            json.dump(asdict(config), f, indent=2, default=str)

    def _generate_dockerfile(self, config: BuildConfiguration, progress: BuildProgress, target_path: Path) -> Path:
        progress.current_stage = "Generating Dockerfile"
        progress.progress_percent = 20
        
        build_temp = self.cache_dir / "builds" / config.build_id
        src_df_name = "Dockerfile.gpu" if config.use_gpu else "Dockerfile"

        # The Dockerfile COPYs modules/, scripts/ and docker/entrypoint.sh relative to its context.
        df_path = stage_build_context(target_path, self.base_dir, build_temp / "context", src_df_name)
        progress.add_log(f"Using Dockerfile from {target_path.name}, build context: {df_path.parent}")
        
        if config.enable_hadolint: 
            self._validate_dockerfile_hadolint(df_path, progress)
            
        return df_path

    def _validate_dockerfile_hadolint(self, path: Path, prog: BuildProgress):
        try: 
            subprocess.run(["hadolint", str(path)], check=True, capture_output=True) 
        except Exception: 
            prog.add_warning("Hadolint check skipped (tool not found or failed)")

    def _build_docker_image(self, config: BuildConfiguration, progress: BuildProgress, path: Path) -> Image:
        progress.current_stage = "Building Image"
        progress.progress_percent = 40
        
        tag = f"llm-framework/{config.target_arch.lower()}:{config.build_id.lower()}"
        context = path.parent
        rel_df = path.name

        progress.add_log(f"Building Docker Image: {tag}")
        
        try:
            buildargs = {"LLAMA_CPP_TAG": str(self._get_conf("llama_cpp_version", "v0.6.0"))}
            build_jobs = int(self._get_conf("build_jobs", 0) or 0)
            if build_jobs > 0:
                buildargs["BUILD_JOBS"] = str(build_jobs)  # lowered by self-healing after an OOM
            buildargs.update(config.build_args)
            # uid/gid 0 already exist in the image (root); the entrypoint maps the user at runtime anyway.
            if sys.platform != "win32" and os.getuid() != 0:
                buildargs["USER_ID"] = str(os.getuid())
                buildargs["GROUP_ID"] = str(os.getgid())

            labels = {IMAGE_LABEL: config.target_arch}
            self._build_images[config.build_id] = tag
            if self._buildkit_available():
                self._build_with_buildkit(tag, path, buildargs, progress, labels)
            else:
                resp = self.docker_client.api.build(
                    path=str(context),
                    dockerfile=str(rel_df),
                    tag=tag,
                    buildargs=buildargs,
                    labels=labels,
                    decode=True
                )
                for chunk in resp:
                    if 'stream' in chunk:
                        line = chunk['stream'].strip()
                        if line: progress.add_log(f"BUILD: {line}")
                    if 'error' in chunk:
                        raise RuntimeError(chunk['error'])
            
            return self.docker_client.images.get(tag)
            
        except Exception as e:
            raise RuntimeError(f"Image build failed: {e}\n{self._log_tail(progress, 'BUILD: ')}")

    def _buildkit_available(self) -> bool:
        """BuildKit (docker buildx) caches better than the legacy builder behind docker-py's api.build,
        which Docker deprecated; it is also what CI uses."""
        if not shutil.which("docker"):
            return False
        try:
            subprocess.run(["docker", "buildx", "version"], capture_output=True, check=True, timeout=30)
            return True
        except Exception:
            return False

    def _build_with_buildkit(self, tag: str, dockerfile: Path, buildargs: Dict[str, str], progress: BuildProgress,
                             labels: Optional[Dict[str, str]] = None):
        cmd = ["docker", "buildx", "build", "--load", "--progress=plain", "-t", tag, "-f", str(dockerfile)]
        for key, value in buildargs.items():
            cmd += ["--build-arg", f"{key}={value}"]
        for key, value in (labels or {}).items():
            cmd += ["--label", f"{key}={value}"]
        cmd.append(str(dockerfile.parent))
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                progress.add_log(f"BUILD: {line}")
        if proc.wait() != 0:
            raise RuntimeError(f"docker buildx build exited with code {proc.returncode}")

    @staticmethod
    def _log_tail(progress: BuildProgress, prefix: str, lines: int = 25) -> str:
        """The last log lines with the actual cause (e.g. 'No space left on device') for the user and self-healing."""
        tail = [l for l in progress.logs if prefix in l][-lines:]
        return "\n".join(tail)

    def _scan_image_security(self, image_tag: str, progress: BuildProgress):
        if not self._get_conf("enable_security_scan", True):
            progress.add_log("Security scan disabled (enable_security_scan=false).")
            return
        progress.add_log(f"Scanning image {image_tag} for vulnerabilities...")
        try:
            scan_cmd = ["image", "--exit-code", "1", "--severity", "HIGH,CRITICAL", image_tag]
            trivy_image = self._get_conf('image_trivy', "aquasec/trivy:0.75.0")
            
            log_stream = self.docker_client.containers.run(
                trivy_image, 
                command=scan_cmd,
                volumes={
                    '/var/run/docker.sock': {'bind': '/var/run/docker.sock', 'mode': 'ro'}, 
                    'trivy_cache': {'bind': '/root/.cache/', 'mode': 'rw'}
                },
                remove=True, 
                stream=True
            )
            for line in log_stream: progress.add_log(f"TRIVY: {line.decode().strip()}")
            progress.add_log("Security scan passed.")
        except docker.errors.ContainerError:
            progress.add_warning(f"Security vulnerabilities found! Review logs above.")
        except Exception as e:
            progress.add_warning(f"Security scan failed to run: {e}")

    def _hf_token(self) -> Optional[str]:
        sm = self.secrets_manager or (getattr(self.framework, "secrets_manager", None) if self.framework else None)
        try:
            return sm.get_secret("hf_token") if sm else None
        except Exception:
            return None

    def _stage_model(self, config: BuildConfiguration, progress: BuildProgress) -> Tuple[str, Dict[str, Dict[str, str]]]:
        """Returns the model path inside the container and the extra volumes needed to reach it.
        Local paths are mounted read-only; HuggingFace repo ids are downloaded into cache/models/hf."""
        progress.current_stage = "Fetching model"
        source = (config.model_source or "").strip()
        if not source:
            # Path("") is the current directory, which must never be mounted as "the model".
            raise ValueError("No model source given.")
        local = Path(source).expanduser()
        if local.exists():
            local = local.resolve()
            mount = local if local.is_dir() else local.parent
            inner = "/build-cache/input-model" + ("" if local.is_dir() else f"/{local.name}")
            progress.add_log(f"Using local model {local}")
            return inner, {str(mount): {"bind": "/build-cache/input-model", "mode": "ro"}}

        if not HF_REPO_ID.match(source) or ".." in source:
            raise ValueError(f"Model '{source}' is neither an existing local path nor a HuggingFace repo id (owner/name).")

        from huggingface_hub import HfApi, snapshot_download
        token = self._hf_token()
        revision = config.model_branch or None
        files = HfApi(token=token).list_repo_files(source, revision=revision)
        ignore = ["*.gguf", "*.onnx", "*.onnx_data", "*.h5", "*.msgpack", "*.ot", "*.tflite", "original/*", "onnx/*"]
        if any(f.endswith(".safetensors") for f in files):
            ignore += ["*.bin", "*.pt", "*.pth"]  # same weights twice otherwise

        dest = self.cache_dir / "models" / "hf" / source.replace("/", "__")
        progress.add_log(f"Downloading {source} from Hugging Face to {dest} ...")
        snapshot_download(repo_id=source, revision=revision, local_dir=str(dest), token=token, ignore_patterns=ignore)
        return f"/build-cache/models/hf/{dest.name}", {}

    def _container_env(self, config: BuildConfiguration, model_path: str) -> Dict[str, str]:
        name = config.output_name or re.sub(r"[^\w.-]", "_", Path(config.model_source.rstrip("/")).name) or "model"
        env = {
            "BUILD_ID": config.build_id,
            "MODEL_SOURCE": model_path,
            "MODEL_NAME": name,
            "MODEL_TASK": config.model_task,
            "TARGET_ARCH": config.target_arch,
            "TARGET_BOARD": config.target_board or "",
            "TARGET_FORMAT": config.target_format.value,
            "OPTIMIZATION_LEVEL": config.optimization_level.value,
            "QUANTIZATION": config.quantization or "",
        }
        if config.max_context_length:
            env["MAX_CONTEXT_LENGTH"] = str(config.max_context_length)
        if sys.platform != "win32":
            # Files written to the mounted output/cache dirs then belong to the host user.
            env["PUID"], env["PGID"] = str(os.getuid()), str(os.getgid())
        return env

    RKNN_DATASET_QUANTS = ("i8", "int8", "q8", "q8_0")

    def _resolve_dataset(self, config: BuildConfiguration, progress: BuildProgress):
        """Checks the calibration dataset once. It is kept only where the build uses it (GGUF with an
        importance matrix, RKLLM, RKNN i8); a missing file fails only builds that need it."""
        if not config.dataset_path:
            return
        fmt = config.target_format
        quant = (config.quantization or "").lower()
        used = ((fmt == ModelFormat.GGUF and config.use_imatrix) or fmt == ModelFormat.RKLLM
                or (fmt == ModelFormat.RKNN and quant in self.RKNN_DATASET_QUANTS))
        path = Path(config.dataset_path).expanduser()
        if not used:
            progress.add_log(f"Calibration dataset not used by {fmt.value} {config.quantization or ''}; ignored.")
            config.dataset_path = None
        elif not path.is_file():
            if fmt == ModelFormat.RKNN:
                raise ValueError(f"Calibration dataset '{config.dataset_path}' not found (needed for RKNN {quant}).")
            progress.add_warning(f"Calibration dataset '{config.dataset_path}' not found; continuing without it.")
            config.dataset_path = None
        else:
            config.dataset_path = str(path.resolve())

    def _dataset_mount(self, config: BuildConfiguration,
                       staged_volumes: Optional[Dict[str, Dict[str, str]]] = None) -> Tuple[Dict[str, Dict[str, str]], Optional[str]]:
        """Volumes and in-container path for the calibration dataset (none if no dataset is set).
        Text datasets keep their suffix (RKLLM reads .json as a JSON dataset). An RKNN dataset lists sample
        files relative to its own directory, so that directory is needed. A dataset inside an already
        mounted folder (often the model folder) is reached through that mount: docker-py keys binds by
        host path, so a second bind of the same folder would replace the first."""
        if not config.dataset_path:
            return {}, None
        path = Path(config.dataset_path).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"Calibration dataset '{config.dataset_path}' not found.")
        for host, bind in (staged_volumes or {}).items():
            try:
                rel = path.relative_to(Path(host).resolve())
            except ValueError:
                continue
            return {}, f"{bind['bind'].rstrip('/')}/{rel.as_posix()}"
        if config.target_format == ModelFormat.RKNN:
            return {str(path.parent): {"bind": "/build-cache/dataset", "mode": "ro"}}, f"/build-cache/dataset/{path.name}"
        suffix = path.suffix.lower() if path.suffix.lower() in (".txt", ".json") else ".txt"
        inner = f"/build-cache/dataset{suffix}"
        return {str(path): {"bind": inner, "mode": "ro"}}, inner

    SANDBOX_FALLBACK_USER = (1000, 1000)  # the image's build user, used when the host user is root or on Windows

    def _sandbox(self, config: BuildConfiguration, build_temp: Path) -> Tuple[Dict[str, Any], Dict[str, str], Dict[str, Dict[str, str]]]:
        """Container options for the conversion stages (imatrix, build). They run model tooling and the
        target's build code - possibly AI-generated - so: no network (the model was staged on the host),
        no capabilities, no privilege escalation, a read-only root filesystem, the host user instead of
        root, and a writable work folder for intermediates. Returns (create kwargs, env, extra volumes)."""
        work = build_temp / "work"
        for sub in ("tmp", "home", "cache"):
            ensure_directory(work / sub)
        volumes = {str(work): {"bind": "/build-cache/work", "mode": "rw"}}
        env = {"BUILD_SCRATCH_DIR": "/build-cache/work", "TMPDIR": "/build-cache/work/tmp"}
        if not self._get_conf("build_sandbox", True):
            return {"user": "0:0"}, env, volumes  # legacy mode: the entrypoint maps PUID/PGID and drops root

        if sys.platform != "win32" and os.getuid() != 0:
            uid, gid = os.getuid(), os.getgid()
        else:
            uid, gid = self.SANDBOX_FALLBACK_USER
            if sys.platform != "win32":
                # A root host owns the per-build folders; hand them to the container user.
                for path in [build_temp, *build_temp.rglob("*")]:
                    try:
                        os.chown(path, uid, gid)
                    except OSError:
                        pass
        kwargs: Dict[str, Any] = {
            "user": f"{uid}:{gid}",
            "network_mode": "none",
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "read_only": True,
            "tmpfs": {"/tmp": "rw,nosuid,nodev,size=1g"},
            "pids_limit": int(self._get_conf("build_pids_limit", 4096) or 4096),
            "working_dir": "/build-cache/work",
        }
        memory = str(self._get_conf("build_memory_limit", "") or "").strip()
        if memory:
            kwargs["mem_limit"] = memory
        env.update({
            "HOME": "/build-cache/work/home",
            "XDG_CACHE_HOME": "/build-cache/work/cache",
            # Everything the tools need is local; never let a library try the network.
            "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1",
        })
        return kwargs, env, volumes

    @staticmethod
    def _render_group_ids() -> List[int]:
        """Groups owning /dev/dri render nodes: a non-root container user needs them for GPU access."""
        gids = set()
        for node in Path("/dev/dri").glob("renderD*") if Path("/dev/dri").exists() else []:
            try:
                gids.add(node.stat().st_gid)
            except OSError:
                pass
        return sorted(gids)

    def _generate_imatrix(self, config: BuildConfiguration, progress: BuildProgress, image: Image, target_path: Path,
                          staged: Tuple[str, Dict[str, Dict[str, str]]]):
        """
        NEW in v2.4.0: Runs a pre-build container to generate the importance matrix.
        """
        progress.current_stage = "Calculating IMatrix"
        progress.status = BuildStatus.CALIBRATING
        progress.progress_percent = 50
        progress.add_log("Starting IMatrix Generation (Smart Calibration)...")
        
        build_temp = self.cache_dir / "builds" / config.build_id
        imatrix_dir = build_temp / "imatrix"
        
        # Volumes for IMatrix run
        sandbox, sandbox_env, sandbox_vols = self._sandbox(config, build_temp)
        vols = {
            str(imatrix_dir): {"bind": "/build-cache/imatrix", "mode": "rw"},
            str(self.cache_dir / "models"): {"bind": "/build-cache/models", "mode": "ro" if sandbox.get("read_only") else "rw"},
        }
        dataset_vols, dataset_inner = self._dataset_mount(config, staged[1])
        vols.update(dataset_vols)
        vols.update(staged[1])
        vols.update(sandbox_vols)
        
        env = self._container_env(config, staged[0])
        env.update(sandbox_env)
        env.update({
            "JOB_TYPE": "imatrix", # Signal to build.sh to run --imatrix mode
            "DATASET_PATH": dataset_inner,
        })
        
        # SSOT Repo Injection
        source_repos = self._get_conf("source_repositories", {})
        if source_repos:
            for k, v in source_repos.items():
                if isinstance(v, dict) and 'url' in v:
                    env[f"{k.split('.')[-1].upper()}_REPO_OVERRIDE"] = v['url']

        # GPU Handling for Calculation
        device_requests = []
        if config.use_gpu and shutil.which("nvidia-smi"):
             device_requests = [DeviceRequest(count=-1, capabilities=[['gpu']])]
             
        container = self.docker_client.containers.create(
            image=image.id,
            command=["/app/modules/build.sh"],
            volumes=vols,
            environment=env,
            name=f"llm-imatrix-{config.build_id}",
            device_requests=device_requests,
            **sandbox,
        )
        # A requested importance matrix that fails stops the build (low-bit types would be unusable).
        exit_code = self._run_container(config, progress, container, "IMATRIX: ")
        if exit_code != 0:
            raise RuntimeError(f"IMatrix calculation failed (exit code {exit_code})\n{self._log_tail(progress, 'IMATRIX: ')}")
        if (imatrix_dir / "imatrix.dat").exists():
            progress.add_log("✅ IMatrix successfully generated.")
        else:
            progress.add_warning("IMatrix generation finished but 'imatrix.dat' not found.")

    def _execute_build_modules(self, config: BuildConfiguration, progress: BuildProgress, image: Image, target_path: Path,
                               staged: Tuple[str, Dict[str, Dict[str, str]]]):
        progress.current_stage = "Running modules"
        progress.status = BuildStatus.BUILDING
        progress.progress_percent = 60
        progress.add_log("Starting Main Build Container...")
        
        build_temp = self.cache_dir / "builds" / config.build_id
        sandbox, sandbox_env, sandbox_vols = self._sandbox(config, build_temp)
        
        # 1. Volume Setup
        vols = {
            str(build_temp / "output"): {"bind": "/build-cache/output", "mode": "rw"},
            str(self.cache_dir / "models"): {"bind": "/build-cache/models", "mode": "ro" if sandbox.get("read_only") else "rw"},
            # modules/ are not mounted: the image was built from this target's staged context and
            # already contains them executable; a host mount would hide that copy (mode 644 on Linux).
        }
        vols.update(staged[1])
        vols.update(sandbox_vols)
        
        # 2. Environment Setup
        env = self._container_env(config, staged[0])
        env.update(sandbox_env)
        env["JOB_TYPE"] = "build"
        
        # Check for IMatrix from previous step
        imatrix_file = build_temp / "imatrix" / "imatrix.dat"
        if config.use_imatrix and config.target_format == ModelFormat.GGUF and imatrix_file.exists():
            vols[str(build_temp / "imatrix")] = {"bind": "/build-cache/imatrix", "mode": "ro"}
            env["USE_IMATRIX"] = "1"
            env["IMATRIX_PATH"] = "/build-cache/imatrix/imatrix.dat"
            progress.add_log("Using generated IMatrix for Quantization.")
        
        # Inject SSOT Vars
        source_repos = self._get_conf("source_repositories", {})
        if source_repos:
            for k, v in source_repos.items():
                if isinstance(v, dict) and 'url' in v:
                    env[f"{k.split('.')[-1].upper()}_REPO_OVERRIDE"] = v['url']

        # Calibration dataset (RKLLM calibration text, RKNN INT8 sample list; GGUF uses it via the imatrix)
        dataset_vols, dataset_inner = self._dataset_mount(config, staged[1])
        if dataset_inner:
            vols.update(dataset_vols)
            env["DATASET_PATH"] = dataset_inner

        # 4. GPU Logic
        device_requests = []
        devices = []
        if config.use_gpu:
            if shutil.which("nvidia-smi"):
                device_requests = [DeviceRequest(count=-1, capabilities=[['gpu']])]
            elif os.path.exists("/dev/dri"):
                devices = ["/dev/dri:/dev/dri"]
                if sandbox.get("read_only") and self._render_group_ids():
                    sandbox["group_add"] = [str(gid) for gid in self._render_group_ids()]

        # 5. Run Container
        container = self.docker_client.containers.create(
            image=image.id, 
            command=["/app/modules/build.sh"], 
            volumes=vols, 
            environment=env, 
            name=f"llm-build-{config.build_id}", 
            device_requests=device_requests,
            devices=devices,
            **sandbox,
        )
        
        exit_code = self._run_container(config, progress, container, "CONT: ")
        if exit_code != 0:
            raise RuntimeError(f"Build script failed with exit code {exit_code}\n{self._log_tail(progress, 'CONT: ')}")

    def _extract_artifacts(self, config, progress):
        progress.current_stage = "Extracting"
        progress.progress_percent = 85
        
        src = self.cache_dir / "builds" / config.build_id / "output"
        dst = Path(config.output_dir)
        
        progress.add_log(f"Copying artifacts to {dst}...")
        ensure_directory(dst)
        
        if src.exists():
            count = 0
            for f in src.rglob("*"):
                if f.is_file():
                    rel = f.relative_to(src)
                    target = dst / rel
                    ensure_directory(target.parent)
                    shutil.copy2(f, target)
                    progress.artifacts.append(str(target))
                    count += 1
            progress.add_log(f"Extracted {count} artifacts.")

    @staticmethod
    def _sha256_file(path: Path) -> str:
        sha256 = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def _write_build_manifest(self, config: BuildConfiguration, output_dir: Path,
                              produced: Optional[List[str]] = None) -> Dict[str, Any]:
        """Completes build_info.json (written by the container) with the artifact list and hashes.
        Deployment finds builds through this manifest and installs its primary model file.
        `produced`: the files this build extracted; older files in the same folder (e.g. a build for
        another board) are not listed."""
        manifest_path = output_dir / BUILD_MANIFEST
        info: Dict[str, Any] = {}
        if manifest_path.is_file():
            try:
                loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
                info = loaded if isinstance(loaded, dict) else {}
            except (OSError, ValueError):
                info = {}
        own = {Path(p).resolve() for p in produced} if produced is not None else None
        artifacts = [
            {"file": f.name, "bytes": f.stat().st_size, "sha256": self._sha256_file(f)}
            for f in sorted(output_dir.iterdir())
            if f.is_file() and f.name.lower().endswith(MODEL_FILE_SUFFIXES) and (own is None or f.resolve() in own)
        ]
        target_ext = f".{config.target_format.value}"
        preferred = [a for a in artifacts if a["file"].lower().endswith(target_ext)] or artifacts
        primary = max(preferred, key=lambda a: a["bytes"])["file"] if preferred else None
        # Several weight files or bare safetensors/bin weights need their sidecar files (config, tokenizer).
        multi_file = len(preferred) > 1 or bool(primary and primary.lower().endswith((".safetensors", ".bin")))
        info.update({
            "build_id": config.build_id,
            "model_source": config.model_source,
            "target": config.target_arch,
            "board": config.target_board or info.get("board"),
            "format": config.target_format.value,
            "quantization": config.quantization or info.get("quantization"),
            "imatrix": bool(config.use_imatrix and config.target_format == ModelFormat.GGUF),
            "created": datetime.now().isoformat(timespec="seconds"),
            "artifacts": artifacts,
            "primary": primary,
            "multi_file": multi_file,
        })
        manifest_path.write_text(json.dumps(info, indent=2), encoding="utf-8")
        return info

    def _generate_model_card(self, config: BuildConfiguration, output_dir: Path, manifest: Optional[Dict[str, Any]] = None):
        readme_path = output_dir / "Model_Card.md"
        manifest = manifest or {}
        primary = next((a for a in manifest.get("artifacts", []) if a["file"] == manifest.get("primary")), None)
        model_hash = primary["sha256"] if primary else "n/a (no model file found)"

        usage_code = (
            "   Open the framework's Deployment window, select this build and deploy it to your device.\n"
            "   The generated package contains deploy.sh, which verifies the checksums and installs the model."
        )
        
        content = (
            f"# Model Card: {os.path.basename(config.model_source)}\n\n"
            f"## 🏗️ Build Information\n"
            f"- **Framework:** LLM Cross-Compiler Framework\n"
            f"- **Build ID:** {config.build_id}\n"
            f"- **Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"- **Target Architecture:** {config.target_arch}\n"
            f"- **Target Format:** {config.target_format.value.upper()}\n"
            f"- **Quantization:** {config.quantization or 'FP16'}\n"
            f"- **IMatrix (Smart Calibration):** {'Enabled' if manifest.get('imatrix') else 'Disabled'}\n\n"
            f"## 🛡️ Security & Integrity\n"
            f"- **Primary Artifact:** {manifest.get('primary') or 'n/a'}\n"
            f"- **Primary Artifact Hash (SHA256):** `{model_hash}`\n"
            f"- **Base Image:** {config.base_image}\n\n"
            f"## 🚀 Usage\n"
            f"To deploy this model on your edge device:\n\n"
            f"{usage_code}\n\n"
            f"---\n"
            f"Generated automatically by LLM-Builder.\n"
        )
        
        try:
            with open(readme_path, "w", encoding="utf-8") as f: f.write(content)
        except Exception as e:
            self.logger.error(f"Failed to write Model Card: {e}")

    def _create_golden_artifact(self, config: BuildConfiguration, progress: BuildProgress):
        progress.current_stage = "Archiving"
        progress.progress_percent = 95
        
        output_dir = Path(config.output_dir)
        
        progress.add_log("Writing build manifest and Model Card...")
        manifest = self._write_build_manifest(config, output_dir, progress.artifacts)
        if not manifest.get("primary"):
            progress.add_warning("The build produced no model file.")
        self._generate_model_card(config, output_dir, manifest)
        
        archive_name = output_dir.name 
        root_dir = output_dir.parent
        base_dir = output_dir.name
        
        try:
            progress.add_log(f"Creating Golden Artifact ZIP...")
            zip_path = shutil.make_archive(
                str(root_dir / archive_name), 
                'zip', 
                root_dir, 
                base_dir
            )
            progress.add_log(f"✅ Golden Artifact created: {zip_path}")
            progress.artifacts.append(zip_path)
        except Exception as e:
            progress.add_error(f"Failed to create Golden Artifact: {e}")
