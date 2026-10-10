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
        
        # Stop container if running
        container = self._active_containers.get(build_id)
        if container:
            try: 
                container.stop(timeout=10)
            except Exception as e:
                self.logger.warning(f"Failed to stop container for {build_id}: {e}")
                
        return True

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

            # 4a. Make the model available inside the containers
            staged = self._stage_model(config, prog)
            
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
                
            prog.status = BuildStatus.COMPLETED
            prog.end_time = datetime.now()
            prog.progress_percent = 100
            
        except Exception as e:
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

    def _dataset_mount(self, config: BuildConfiguration) -> Tuple[Dict[str, Dict[str, str]], Optional[str]]:
        """Volumes and in-container path for the calibration dataset (none if no dataset is set).
        Text datasets (GGUF imatrix, RKLLM) are mounted as one file. An RKNN dataset lists sample files
        relative to its own directory, so that directory is mounted read-only."""
        if not config.dataset_path:
            return {}, None
        path = Path(config.dataset_path).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"Calibration dataset '{config.dataset_path}' not found.")
        if config.target_format == ModelFormat.RKNN:
            return {str(path.parent): {"bind": "/build-cache/dataset", "mode": "ro"}}, f"/build-cache/dataset/{path.name}"
        return {str(path): {"bind": "/build-cache/dataset.txt", "mode": "ro"}}, "/build-cache/dataset.txt"

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
        vols = {
            str(imatrix_dir): {"bind": "/build-cache/imatrix", "mode": "rw"},
            str(self.cache_dir / "models"): {"bind": "/build-cache/models", "mode": "rw"},
        }
        dataset_vols, dataset_inner = self._dataset_mount(config)
        vols.update(dataset_vols)
        vols.update(staged[1])
        
        env = self._container_env(config, staged[0])
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
             
        try:
            container = self.docker_client.containers.create(
                image=image.id,
                command=["/app/modules/build.sh"],
                volumes=vols,
                environment=env,
                name=f"llm-imatrix-{config.build_id}",
                user="0:0",
                device_requests=device_requests
            )
            
            container.start()
            for line in container.logs(stream=True, follow=True):
                progress.add_log(f"IMATRIX: {line.decode().strip()}")
                
            res = container.wait(timeout=config.build_timeout)
            if res.get('StatusCode', 1) != 0:
                raise RuntimeError("IMatrix calculation failed.")
                
            # Verify Output
            if (imatrix_dir / "imatrix.dat").exists():
                progress.add_log("✅ IMatrix successfully generated.")
            else:
                progress.add_warning("IMatrix generation finished but 'imatrix.dat' not found.")
                
        except Exception as e:
            progress.add_error(f"IMatrix generation error: {e}")
            raise # Re-raise to stop build or handle via policy? 
                  # For now we fail hard if requested IMatrix fails.
        finally:
            try: container.remove(force=True)
            except: pass

    def _execute_build_modules(self, config: BuildConfiguration, progress: BuildProgress, image: Image, target_path: Path,
                               staged: Tuple[str, Dict[str, Dict[str, str]]]):
        progress.current_stage = "Running modules"
        progress.status = BuildStatus.BUILDING
        progress.progress_percent = 60
        progress.add_log("Starting Main Build Container...")
        
        build_temp = self.cache_dir / "builds" / config.build_id
        
        # 1. Volume Setup
        vols = {
            str(build_temp / "output"): {"bind": "/build-cache/output", "mode": "rw"},
            str(self.cache_dir / "models"): {"bind": "/build-cache/models", "mode": "rw"},
            # modules/ are not mounted: the image was built from this target's staged context and
            # already contains them executable; a host mount would hide that copy (mode 644 on Linux).
        }
        vols.update(staged[1])
        
        # 2. Environment Setup
        env = self._container_env(config, staged[0])
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
        dataset_vols, dataset_inner = self._dataset_mount(config)
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

        # 5. Run Container
        container = self.docker_client.containers.create(
            image=image.id, 
            command=["/app/modules/build.sh"], 
            volumes=vols, 
            environment=env, 
            name=f"llm-build-{config.build_id}", 
            user="0:0",
            device_requests=device_requests,
            devices=devices
        )
        
        with self._lock: 
            self._active_containers[config.build_id] = container
            
        container.start()
        
        for line in container.logs(stream=True, follow=True):
            progress.add_log(f"CONT: {line.decode().strip()}")
            
        res = container.wait(timeout=config.build_timeout)
        exit_code = res.get('StatusCode', 1)
        
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

    def _write_build_manifest(self, config: BuildConfiguration, output_dir: Path) -> Dict[str, Any]:
        """Completes build_info.json (written by the container) with the artifact list and hashes.
        Deployment finds builds through this manifest and installs its primary model file."""
        manifest_path = output_dir / BUILD_MANIFEST
        info: Dict[str, Any] = {}
        if manifest_path.is_file():
            try:
                loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
                info = loaded if isinstance(loaded, dict) else {}
            except (OSError, ValueError):
                info = {}
        artifacts = [
            {"file": f.name, "bytes": f.stat().st_size, "sha256": self._sha256_file(f)}
            for f in sorted(output_dir.iterdir())
            if f.is_file() and f.name.lower().endswith(MODEL_FILE_SUFFIXES)
        ]
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
            "primary": max(artifacts, key=lambda a: a["bytes"])["file"] if artifacts else None,
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
            f"- **IMatrix (Smart Calibration):** {'Enabled' if config.use_imatrix else 'Disabled'}\n\n"
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
        manifest = self._write_build_manifest(config, output_dir)
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
