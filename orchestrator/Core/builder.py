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
import subprocess
import threading
import time
import shutil
import hashlib
import re
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field, asdict
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from enum import Enum

import yaml
import docker
from docker.models.containers import Container
from docker.models.images import Image
from docker.types import DeviceRequest

from orchestrator import __version__
from orchestrator.Core import build_records
from orchestrator.tools import local_files
from orchestrator.utils.logging import get_logger
from orchestrator.utils.build_context import stage_build_context

# Files a build may produce that are models (data to install), never programs to execute.
MODEL_FILE_SUFFIXES = (".gguf", ".rkllm", ".rknn", ".onnx", ".safetensors", ".bin")
BUILD_MANIFEST = "build_info.json"
# Large models are copied, hashed and zipped in chunks; a cancel is noticed between two of them.
COPY_CHUNK = 8 * 1024 * 1024
# A build is staged in ".<output folder>.<build id>.partial" next to its output folder until it has completed.
STAGING_SUFFIX = ".partial"


def is_staging_folder(name: str) -> bool:
    return name.startswith(".") and name.endswith(STAGING_SUFFIX)

# Fallback Helper if utils module not fully ready during bootstrap
def ensure_directory(path: Path):
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)

class ValidationError(Exception): pass


class BuildCancelled(Exception):
    """The user cancelled the build; not a failure."""


class BuildSlotsBusy(RuntimeError):
    """Every build slot is taken (a cancelled build keeps its slot until its worker has stopped)."""


def _is_read_timeout(exc: Optional[BaseException]) -> bool:
    """True when docker-py reports an expired wait(timeout=...): a requests ReadTimeout, or a
    ConnectionError wrapping urllib3's ReadTimeoutError (unix socket transport)."""
    import requests
    from urllib3.exceptions import ReadTimeoutError
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, (requests.exceptions.ReadTimeout, ReadTimeoutError, TimeoutError)):
            return True
        if any(isinstance(arg, ReadTimeoutError) for arg in getattr(exc, "args", ())):
            return True
        exc = exc.__cause__ or exc.__context__
    return False

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
TARGET_ID = re.compile(r"^[A-Za-z0-9][\w.-]{0,63}$")
COMMIT_ID = re.compile(r"^[0-9a-f]{40}$")
IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")


def repo_override_names(name: str) -> List[str]:
    """The variables a source_repositories entry reaches the build containers as. First the whole name in upper
    case, every run of characters other than letters and digits turned into "_": 'llama.cpp', 'llama_cpp' and
    'llama-cpp' all give LLAMA_CPP_REPO_OVERRIDE, the name targets/*/modules/source_module.sh reads. A dotted name
    also keeps the name earlier versions derived from its part after the last dot ('repos.llama_cpp' still gives
    LLAMA_CPP_REPO_OVERRIDE as well)."""
    names: List[str] = []
    for part in (str(name), str(name).split(".")[-1]):
        base = re.sub(r"[^A-Za-z0-9]+", "_", part).strip("_").upper()
        if base and f"{base}_REPO_OVERRIDE" not in names:
            names.append(f"{base}_REPO_OVERRIDE")
    return names

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

    # Recorded during the build for build_info.json (recipes are written from it)
    model_revision: Optional[str] = None      # Hugging Face commit the downloaded files belong to
    target_pack_sha256: Optional[str] = None  # content digest of the target folder used
    target_version: Optional[str] = None      # metadata.version of its target.yml
    toolchain_image: Optional[str] = None     # id of the image that ran the conversion
    request_id: Optional[str] = None  # the orchestrator request the build belongs to (shown by `build status`)
    # Local inputs of an AI caller's build (orchestrator.tools.local_files.LocalInputs): pinned files that are checked
    # again right before each container starts; without a pinned model the source is a Hugging Face repo id and is
    # never looked up on disk. None only for a person's own builds (GUI, CLI), whose paths are used as given.
    local_inputs: Optional[Any] = None

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
    
    def __init__(self, config_or_framework, max_concurrent_builds: Optional[int] = None, default_timeout: int = 3600,
                 secrets_manager=None, base_dir: Optional[Path] = None):
        self.logger = get_logger("BuildEngine")
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

        # Build slots: the argument, else the setting max_concurrent_builds (1-10, default 2).
        if max_concurrent_builds is None:
            try:
                max_concurrent_builds = int(self._get_conf("max_concurrent_builds", 2) or 2)
            except (TypeError, ValueError):
                max_concurrent_builds = 2
        self.max_concurrent_builds = max(1, min(int(max_concurrent_builds), 10))
        # The framework's target registry (set by FrameworkManager): its id -> folder map resolves targets.
        self.target_manager = None

        # Docker Client Fallback
        if not self.docker_client:
            try:
                self.docker_client = docker.from_env()
            except Exception as e:
                self.logger.error(f"Docker client not available: {e}")
        
        self._lock = threading.Lock()
        self._builds: Dict[str, BuildProgress] = {}
        self._active_containers: Dict[str, Container] = {}
        self._active_processes: Dict[str, subprocess.Popen] = {}
        self._running: set = set()  # builds whose worker thread has not returned yet
        self._settled: set = set()  # builds whose outcome is fixed; a cancel comes too late for them
        self._build_images: Dict[str, str] = {}
        self._records: Dict[str, Dict[str, Any]] = {}  # what each build's record says besides its progress
        self._executor = ThreadPoolExecutor(max_workers=self.max_concurrent_builds)
        
        # Paths initialization using centralized config getter
        # Relative to the installation; "~" means the user's home (same rule as FrameworkManager.resolve_path).
        self.targets_dir = self.base_dir / Path(str(self._get_conf("targets_dir", "targets"))).expanduser()
        self.models_dir = self.base_dir / Path(str(self._get_conf("models_dir", "models"))).expanduser()
        self.output_dir = self.base_dir / Path(str(self._get_conf("output_dir", "output"))).expanduser()
        self.cache_dir = self.base_dir / Path(str(self._get_conf("cache_dir", "cache"))).expanduser()
        # One record per build for `llm-cli build status` in other processes (see build_records).
        self.records_dir = build_records.records_dir(self.cache_dir)
        # Nothing is created here: the folders are made when a build needs them (a CLI call or an MCP server
        # start must not leave empty cache/, models/ and output/ folders in the installation; the records
        # folder is made by the first record written).
        if self.docker_client:
            self._validate_docker_environment()
        
        self.logger.info(f"Build Engine initialized (max_workers: {self.max_concurrent_builds})")

    def _get_conf(self, key: str, default: Any = None) -> Any:
        """Centralized safe configuration retrieval."""
        if self.config:
            if hasattr(self.config, 'get'):
                return self.config.get(key, default)
            return getattr(self.config, key, default)
        return default
    
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

    def resolve_target_dir(self, name: str) -> Optional[Path]:
        """The folder of a target given by id or folder name (any letter case), through the same id -> folder map
        as the target registry. A folder without a loadable target.yml is still found by its own name, as
        before; None if there is no such folder."""
        tm = self.target_manager
        if tm is not None and hasattr(tm, "folder_of"):
            folder = tm.folder_of(name)
        else:
            from orchestrator.Core.target_manager import read_target_folders, resolve_target_name
            folders = {tid: path for tid, (path, _) in read_target_folders(self.targets_dir, self.logger).items()}
            tid = resolve_target_name(name, folders)
            folder = folders.get(tid) if tid else None
        if folder is not None or not name or not self.targets_dir.is_dir():
            return folder
        for exact in (True, False):
            for path in self.targets_dir.iterdir():
                if path.is_dir() and (path.name == name if exact else path.name.casefold() == name.casefold()):
                    return path
        return None

    def build_model(self, config: BuildConfiguration) -> str:
        """Submits a new build job."""
        self._validate_build_config(config)
        
        progress = BuildProgress(config.build_id, BuildStatus.QUEUED, "Initializing", start_time=datetime.now())

        with self._lock:
            # A cancelled build still counts until its worker has really stopped.
            if len(self._running) >= self.max_concurrent_builds:
                raise BuildSlotsBusy("Max concurrent builds reached")
            if config.build_id in self._running:
                raise RuntimeError(f"Build {config.build_id} is still running")
            self._builds[config.build_id] = progress
            self._running.add(config.build_id)

        self._records[config.build_id] = {
            "build_id": config.build_id, "request_id": config.request_id, **build_records.this_process(),
            "model": config.model_source, "target": config.target_arch, "format": config.target_format.value,
            "quantization": config.quantization, "board": config.target_board, "output_dir": config.output_dir,
            "started": progress.start_time.isoformat(timespec="seconds"),
        }
        self._save_record(config.build_id)
        try:
            build_records.prune(self.records_dir)
        except OSError as e:
            self.logger.debug(f"Old build records not pruned: {e}")
        try:
            self._executor.submit(self._execute_build, config)
        except BaseException:
            with self._lock:
                self._running.discard(config.build_id)
            raise
        self.logger.info(f"Build started: {config.build_id}")
        return config.build_id

    def _save_record(self, build_id: str):
        """Writes the build's record with its current status; a record that cannot be written never stops a build."""
        info, progress = self._records.get(build_id), self._builds.get(build_id)
        if info is None or progress is None:
            return
        record = dict(info, status=progress.status.value, stage=progress.current_stage,
                      progress=progress.progress_percent,
                      ended=progress.end_time.isoformat(timespec="seconds") if progress.end_time else None,
                      error=progress.errors[-1] if progress.errors else None)
        try:
            build_records.write_record(self.records_dir, record)
        except (OSError, TypeError, ValueError) as e:
            self.logger.debug(f"Build record of {build_id} not written: {e}")

    def get_build_status(self, build_id: str) -> Optional[BuildProgress]:
        return self._builds.get(build_id)

    def is_active(self, build_id: str) -> bool:
        """True while the build's worker runs (also after a cancel, until its container has stopped and its files
        are discarded)."""
        with self._lock:
            return build_id in self._running
    
    def list_builds(self) -> List[BuildProgress]:
        with self._lock: return list(self._builds.values())
    
    def cancel_build(self, build_id: str) -> bool:
        with self._lock:
            progress = self._builds.get(build_id)
            if not progress or progress.status in [BuildStatus.COMPLETED, BuildStatus.FAILED, BuildStatus.CANCELLED]:
                return False
            if build_id in self._settled:  # failed and cleaning up, or publishing its files: too late
                return False
            progress.status = BuildStatus.CANCELLED
            container = self._active_containers.get(build_id)
            process = self._active_processes.get(build_id)
        progress.add_log("Build cancelled by user.")
        self._save_record(build_id)

        # Stop whatever runs right now (image build, security scan, imatrix or conversion container);
        # every stage checks the status afterwards and ends the build.
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except Exception as e:
                self.logger.warning(f"Failed to stop the image build for {build_id}: {e}")
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

    def _enter_stage(self, progress: "BuildProgress", status: "BuildStatus"):
        """Moves a build to its next stage - unless it was cancelled meanwhile: a cancel is never undone."""
        with self._lock:
            if progress.status == BuildStatus.CANCELLED:
                raise BuildCancelled("Build cancelled by user.")
            progress.status = status
        self._save_record(progress.build_id)

    def _settle(self, progress: "BuildProgress"):
        """Fixes a successful outcome before its files are published: raises BuildCancelled after a cancel,
        otherwise cancel_build refuses from now on."""
        with self._lock:
            if progress.status == BuildStatus.CANCELLED:
                raise BuildCancelled("Build cancelled by user.")
            self._settled.add(progress.build_id)

    def _run_container(self, config: "BuildConfiguration", progress: "BuildProgress", container: Container,
                       prefix: str) -> int:
        """Runs a stage container: streams its log into the progress, enforces the build timeout,
        honours cancellation and always removes the container. Returns the exit code."""
        with self._lock:
            self._active_containers[config.build_id] = container
        try:
            self._check_cancelled(progress)
            container.start()
            if progress.status == BuildStatus.CANCELLED:
                # cancel_build may have tried to stop the container before it was running
                try:
                    container.kill()
                except Exception:
                    pass
                raise BuildCancelled("Build cancelled by user.")

            def pump():
                try:
                    for line in container.logs(stream=True, follow=True):
                        progress.add_log(f"{prefix}{line.decode(errors='replace').rstrip()}")
                except Exception as e:  # the container may be removed while streaming
                    self.logger.debug(f"Log stream of {config.build_id} ended: {e}")

            reader = threading.Thread(target=pump, name=f"logs-{config.build_id}", daemon=True)
            reader.start()
            timeout = config.build_timeout or self.default_timeout
            started = time.monotonic()
            try:
                result = container.wait(timeout=timeout)
            except Exception as e:
                self._check_cancelled(progress)
                try:
                    container.kill()
                except Exception:
                    pass
                stage = prefix.strip(': ').lower() or 'build'
                if _is_read_timeout(e) or time.monotonic() - started >= timeout:
                    raise TimeoutError(f"The {stage} stage ran longer than {timeout} s and was stopped "
                                       f"(setting build_timeout).") from e
                # Anything else (container removed, daemon restarted, API error) keeps its own message.
                raise RuntimeError(f"The {stage} stage failed while waiting for its container: {e}\n"
                                   f"{self._log_tail(progress, prefix)}") from e
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
        
        with self._lock:
            previous = progress.status
            if previous != BuildStatus.CANCELLED:  # a cancelled build stays cancelled while it is cleaned up
                progress.status = BuildStatus.CLEANING
        try:
            
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
        outcome = BuildStatus.FAILED
        try:
            outcome = self._execute_build_stages(config, prog)
        finally:
            # The outcome becomes visible together with the free slot, so a poller that sees it can submit
            # its next build right away. A cancel is never undone.
            with self._lock:
                if prog.status != BuildStatus.CANCELLED:
                    prog.status = outcome
                self._running.discard(bid)
                self._settled.discard(bid)
            prog.end_time = prog.end_time or datetime.now()
            self._save_record(bid)

    def _execute_build_stages(self, config: BuildConfiguration, prog: "BuildProgress") -> "BuildStatus":
        """Runs the stages and returns the outcome; _execute_build publishes it."""
        bid = config.build_id
        try:
            self._enter_stage(prog, BuildStatus.PREPARING)
            self._resolve_dataset(config, prog)
            
            # 1. Resolve the target's folder (id or folder name; a plain name: no separators, no '..')
            if not TARGET_ID.fullmatch(config.target_arch or ""):
                raise ValueError(f"Invalid target name {config.target_arch!r}.")
            target_path = self.resolve_target_dir(config.target_arch)
            if target_path is None:
                raise FileNotFoundError(f"Target {config.target_arch} not found in {self.targets_dir}")

            self._record_target(config, prog, target_path)

            # 2. Prepare Environment
            self._prepare_build_environment(config, prog, target_path)
            
            # 3. Generate Dockerfile
            df_path = self._generate_dockerfile(config, prog, target_path)
            
            # 4. Build Docker Image
            image = self._build_docker_image(config, prog, df_path)
            image_id = getattr(image, "id", None)
            config.toolchain_image = image_id if isinstance(image_id, str) and IMAGE_ID.fullmatch(image_id) else None
            self._check_cancelled(prog)

            # 4a. Make the model available inside the containers
            staged = self._stage_model(config, prog)
            self._check_cancelled(prog)
            
            # 5. Security Scan
            self._scan_image_security(image.tags[0], prog, build_id=bid,
                                      timeout=config.build_timeout or self.default_timeout)
            self._check_cancelled(prog)
            
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
            
            # 7./8. Artifacts, manifest and golden ZIP are staged next to the output folder. They reach it
            # (and the deployment listings) only once the build can no longer be cancelled.
            self._enter_stage(prog, BuildStatus.PACKAGING)
            self._extract_artifacts(config, prog)
            self._create_golden_artifact(config, prog)

            # 9. Cleanup, before the outcome is settled: a cancel meanwhile still discards the staged files.
            if config.cleanup_after_build:
                self.cleanup_build(bid)

            self._settle(prog)
            self._publish_artifacts(config, prog)
            prog.end_time = datetime.now()
            prog.progress_percent = 100
            return BuildStatus.COMPLETED
            
        except BuildCancelled:
            return self._end_cancelled(config, prog)
        except Exception as e:
            with self._lock:
                cancelled = prog.status == BuildStatus.CANCELLED
                if not cancelled:
                    self._settled.add(bid)  # failed: a cancel now would only hide the error
            if cancelled:
                # Stopping the container makes the stage fail; that is the cancellation, not an error.
                return self._end_cancelled(config, prog)
            self._discard_staged(config, prog)
            prog.end_time = datetime.now()
            prog.add_error(str(e))
            self.logger.error(f"Build {bid} failed: {e}", exc_info=True)
            # Try cleanup even on failure
            try: self.cleanup_build(bid)
            except Exception: pass
            return BuildStatus.FAILED

    def _end_cancelled(self, config: BuildConfiguration, prog: "BuildProgress") -> "BuildStatus":
        self._discard_staged(config, prog)
        prog.end_time = datetime.now()
        try: self.cleanup_build(config.build_id)
        except Exception: pass
        return BuildStatus.CANCELLED

    def _record_target(self, config: BuildConfiguration, progress: BuildProgress, target_path: Path):
        """Notes which target content the build used, for build_info.json and recipes. A community target the
        signed index revoked is not built; one imported from a file without an index entry gets a warning."""
        from orchestrator.Core.community_manager import build_trust_check
        from orchestrator.Core.community_pack import PackError, tree_sha256
        state = getattr(self.config, "user_state_dir", None)
        warning = build_trust_check(target_path, Path(state) / "community" if state else None)
        if warning:
            progress.add_warning(warning)
        try:
            config.target_pack_sha256 = tree_sha256(target_path)
        except (PackError, OSError) as e:
            progress.add_warning(f"Target fingerprint not recorded, so this build cannot become a recipe: {e}")
        try:
            meta = yaml.safe_load((target_path / "target.yml").read_text(encoding="utf-8")) or {}
            version = (meta.get("metadata") or {}).get("version")
            config.target_version = str(version) if version else None
        except (OSError, yaml.YAMLError, AttributeError):
            config.target_version = None

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
        # Mounted into every stage container: it must exist (and belong to the user) before Docker sees it.
        ensure_directory(self.cache_dir / "models")
        
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
        with self._lock:
            self._active_processes[progress.build_id] = proc  # cancel_build terminates it
        try:
            self._check_cancelled(progress)
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    progress.add_log(f"BUILD: {line}")
            if proc.wait() != 0:
                self._check_cancelled(progress)
                raise RuntimeError(f"docker buildx build exited with code {proc.returncode}")
        finally:
            with self._lock:
                self._active_processes.pop(progress.build_id, None)
            if proc.poll() is None:
                proc.kill()

    @staticmethod
    def _log_tail(progress: BuildProgress, prefix: str, lines: int = 25) -> str:
        """The last log lines with the actual cause (e.g. 'No space left on device') for the user and self-healing."""
        tail = [l for l in progress.logs if prefix in l][-lines:]
        return "\n".join(tail)

    def _scan_image_security(self, image_tag: str, progress: BuildProgress, build_id: Optional[str] = None,
                             timeout: Optional[int] = None):
        if not self._get_conf("enable_security_scan", True):
            progress.add_log("Security scan disabled (enable_security_scan=false).")
            return
        progress.add_log(f"Scanning image {image_tag} for vulnerabilities...")
        build_id = build_id or progress.build_id
        container = None
        try:
            scan_cmd = ["image", "--exit-code", "1", "--severity", "HIGH,CRITICAL", image_tag]
            trivy_image = self._get_conf('image_trivy', "aquasec/trivy:0.75.0")

            # Detached and registered like the stage containers, so cancel_build can stop it.
            container = self.docker_client.containers.run(
                trivy_image, 
                command=scan_cmd,
                volumes={
                    '/var/run/docker.sock': {'bind': '/var/run/docker.sock', 'mode': 'ro'}, 
                    'trivy_cache': {'bind': '/root/.cache/', 'mode': 'rw'}
                },
                detach=True,
            )
            with self._lock:
                self._active_containers[build_id] = container
            self._check_cancelled(progress)

            def pump():
                try:
                    for line in container.logs(stream=True, follow=True):
                        progress.add_log(f"TRIVY: {line.decode(errors='replace').strip()}")
                except Exception as e:  # the container may be removed while streaming
                    self.logger.debug(f"Scan log stream of {build_id} ended: {e}")

            reader = threading.Thread(target=pump, name=f"trivy-logs-{build_id}", daemon=True)
            reader.start()
            # A scan that hangs (e.g. on its database download) must not hold the build slot forever.
            timeout = timeout or self.default_timeout
            try:
                code = int(container.wait(timeout=timeout).get("StatusCode", 1))
            except Exception as e:
                self._check_cancelled(progress)
                if not _is_read_timeout(e):
                    raise
                try:
                    container.kill()
                except Exception:
                    pass
                progress.add_warning(f"Security scan did not finish within {timeout} s and was stopped "
                                     f"(setting build_timeout).")
                return
            reader.join(timeout=10)
            self._check_cancelled(progress)
            if code == 0:
                progress.add_log("Security scan passed.")
            elif code == 1:
                progress.add_warning("Security vulnerabilities found! Review logs above.")
            else:
                progress.add_warning(f"Security scan failed to run (exit code {code}).")
        except BuildCancelled:
            raise
        except Exception as e:
            self._check_cancelled(progress)
            progress.add_warning(f"Security scan failed to run: {e}")
        finally:
            if container is not None:
                with self._lock:
                    if self._active_containers.get(build_id) is container:
                        self._active_containers.pop(build_id, None)
                try:
                    container.remove(force=True)
                except Exception:
                    pass

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
        pin = getattr(config.local_inputs, "model", None)
        if pin is not None:  # named by an AI caller: confined, pinned, never more than the file or its own folder
            inner, volumes = local_files.mount_plan(pin, "/build-cache/input-model")
            progress.add_log(f"Using local model {pin.ref}")
            return inner, volumes
        # An AI caller's build without a pinned model names a Hugging Face repo: never looked up on disk, where
        # 'Documents/private' would be a folder below the working directory (G8).
        local = Path(source).expanduser()
        if config.local_inputs is None and local.exists():
            local = local.resolve()
            progress.add_log(f"Using local model {local}")
            if local.is_dir():
                return "/build-cache/input-model", {str(local): {"bind": "/build-cache/input-model", "mode": "ro"}}
            # Only the file itself: the folder it lies in may hold anything else (G8).
            inner = f"/build-cache/input-model/{local.name}"
            return inner, {str(local): {"bind": inner, "mode": "ro"}}

        if not HF_REPO_ID.match(source) or ".." in source:
            raise ValueError(f"Model '{source}' is neither an existing local path nor a HuggingFace repo id (owner/name).")

        from huggingface_hub import HfApi, snapshot_download
        token = self._hf_token()
        api = HfApi(token=token)
        revision = config.model_branch or None
        commit = self._model_commit(api, source, revision)
        if commit:
            revision = config.model_revision = commit  # listing and download see the same commit
        files = api.list_repo_files(source, revision=revision)
        ignore = ["*.gguf", "*.onnx", "*.onnx_data", "*.h5", "*.msgpack", "*.ot", "*.tflite", "original/*", "onnx/*"]
        if any(f.endswith(".safetensors") for f in files):
            ignore += ["*.bin", "*.pt", "*.pth"]  # same weights twice otherwise

        dest = self.cache_dir / "models" / "hf" / source.replace("/", "__")
        progress.add_log(f"Downloading {source} from Hugging Face to {dest} ...")
        snapshot_download(repo_id=source, revision=revision, local_dir=str(dest), token=token, ignore_patterns=ignore)
        return f"/build-cache/models/hf/{dest.name}", {}

    @staticmethod
    def _model_commit(api, source: str, revision: Optional[str]) -> Optional[str]:
        """The commit a branch or tag points to (None when it cannot be found out)."""
        if revision and COMMIT_ID.fullmatch(revision):
            return revision
        try:
            sha = getattr(api.model_info(source, revision=revision), "sha", None)
        except Exception:
            return None
        return sha if isinstance(sha, str) and COMMIT_ID.fullmatch(sha) else None

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

    def _repo_overrides(self) -> Dict[str, str]:
        """<NAME>_REPO_OVERRIDE variables (see repo_override_names) from the setting source_repositories: entries
        {url: ...} as `llm-cli config repos --add` stores them, and plain URLs as earlier versions stored them. A
        variable named after a whole entry name wins over the same name derived from another entry's last part."""
        env, legacy = {}, {}
        repos = self._get_conf("source_repositories", {}) or {}
        for name, entry in (repos.items() if isinstance(repos, dict) else []):
            url = entry.get("url") if isinstance(entry, dict) else entry
            names = repo_override_names(name)
            if isinstance(url, str) and url.strip() and names:
                env[names[0]] = url.strip()
                legacy.update(dict.fromkeys(names[1:], url.strip()))
        return {**legacy, **env}

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
        pin = getattr(config.local_inputs, "dataset", None)
        path = pin.verify() if pin is not None else Path(config.dataset_path).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"Calibration dataset '{config.dataset_path}' not found.")
        for host, bind in (staged_volumes or {}).items():
            if not Path(host).is_dir():
                continue  # a single bound file (the model itself) holds no dataset
            try:
                rel = path.relative_to(Path(host).resolve())
            except ValueError:
                continue
            return {}, f"{bind['bind'].rstrip('/')}/{rel.as_posix()}"
        if config.target_format == ModelFormat.RKNN:
            # A sample list names files relative to its folder; an AI caller's folder must be wholly allowed.
            folder = local_files.dataset_folder(pin) if pin is not None else path.parent
            return {str(folder): {"bind": "/build-cache/dataset", "mode": "ro"}}, f"/build-cache/dataset/{path.name}"
        suffix = path.suffix.lower() if path.suffix.lower() in (".txt", ".json") else ".txt"
        inner = f"/build-cache/dataset{suffix}"
        return {str(path): {"bind": inner, "mode": "ro"}}, inner

    @staticmethod
    def _verify_local_inputs(config: BuildConfiguration) -> None:
        """Right before a container starts: the files an AI caller named are still the ones it was shown (same
        identity, size and modification time, still inside a folder the AI may use)."""
        inputs = config.local_inputs
        if getattr(inputs, "model", None) is not None:
            inputs.model.verify()
        if getattr(inputs, "dataset", None) is not None and config.dataset_path:
            inputs.dataset.verify()

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
        self._enter_stage(progress, BuildStatus.CALIBRATING)
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
        self._verify_local_inputs(config)
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
        
        env.update(self._repo_overrides())  # SSOT repository overrides

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
        self._enter_stage(progress, BuildStatus.BUILDING)
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
        
        env.update(self._repo_overrides())  # SSOT repository overrides

        # Calibration dataset (RKLLM calibration text, RKNN INT8 sample list; GGUF uses it via the imatrix)
        self._verify_local_inputs(config)
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

    @staticmethod
    def _staging_root(config: BuildConfiguration) -> Path:
        """Hidden folder next to the output folder (next to its target if it is a link): the same filesystem,
        so publishing is a rename, and deployment listings skip it."""
        out = Path(os.path.realpath(config.output_dir))
        return out.parent / f".{out.name}.{config.build_id}{STAGING_SUFFIX}"

    def _staged_output(self, config: BuildConfiguration) -> Path:
        return self._staging_root(config) / Path(config.output_dir).name

    def _discard_staged(self, config: BuildConfiguration, progress: BuildProgress):
        """Drops what a build that did not complete has staged; its output folder was never touched."""
        shutil.rmtree(self._staging_root(config), ignore_errors=True)
        progress.artifacts.clear()

    @staticmethod
    def _move(src: Path, dst: Path):
        try:
            os.replace(src, dst)  # atomic on one filesystem
        except OSError:
            shutil.move(str(src), str(dst))

    def _publish_artifacts(self, config: BuildConfiguration, progress: BuildProgress):
        """Moves the staged files into the output folder (the manifest last) and the ZIP next to it.
        Files of earlier builds in that folder are replaced only now."""
        root = self._staging_root(config)
        if not root.is_dir():
            return
        staged, dst = self._staged_output(config), Path(config.output_dir)
        files = sorted((f for f in staged.rglob("*") if f.is_file()),
                       key=lambda f: (f.name == BUILD_MANIFEST, str(f))) if staged.is_dir() else []
        ensure_directory(dst)
        for f in files:
            target = dst / f.relative_to(staged)
            ensure_directory(target.parent)
            self._move(f, target)
        archive = root / f"{dst.name}.zip"
        if archive.is_file():
            self._move(archive, dst.parent / archive.name)
            progress.add_log(f"✅ Golden Artifact created: {dst.parent / archive.name}")
        shutil.rmtree(root, ignore_errors=True)

    def _copy_file(self, src: Path, dst: Path, progress: BuildProgress):
        """shutil.copy2 that stops after a cancel and never follows a link."""
        self._check_cancelled(progress)
        fd = os.open(src, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        with open(fd, "rb") as fin, open(dst, "wb") as fout:
            for chunk in iter(lambda: fin.read(COPY_CHUNK), b""):
                self._check_cancelled(progress)
                fout.write(chunk)
        shutil.copystat(src, dst)

    def _extract_artifacts(self, config, progress):
        progress.current_stage = "Extracting"
        progress.progress_percent = 85
        
        src = self.cache_dir / "builds" / config.build_id / "output"
        dst = Path(config.output_dir)
        staged = self._staged_output(config)
        
        progress.add_log(f"Copying artifacts to {dst}...")
        shutil.rmtree(self._staging_root(config), ignore_errors=True)  # left over by an interrupted run
        ensure_directory(staged)
        
        if src.exists():
            count = 0
            root = os.path.realpath(src)
            for f in sorted(src.rglob("*")):
                inside = os.path.realpath(f).startswith(root + os.sep)
                if f.is_symlink() or not inside:
                    # Left by the sandboxed conversion: following it would copy a host file it cannot read.
                    # A link within the output is redundant, its target is copied anyway.
                    if not inside:
                        progress.add_warning(f"Not copied: '{f.relative_to(src)}' in the build output links "
                                             f"outside it.")
                    continue
                if f.is_file():
                    rel = f.relative_to(src)
                    target = staged / rel
                    ensure_directory(target.parent)
                    self._copy_file(f, target, progress)
                    progress.artifacts.append(str(dst / rel))  # where it is published
                    count += 1
            progress.add_log(f"Extracted {count} artifacts.")

    @staticmethod
    def _sha256_file(path: Path, progress: Optional[BuildProgress] = None) -> str:
        sha256 = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                if progress is not None:
                    BuildEngine._check_cancelled(progress)
                sha256.update(chunk)
        return sha256.hexdigest()

    def _write_build_manifest(self, config: BuildConfiguration, output_dir: Path,
                              produced: Optional[List[str]] = None,
                              progress: Optional[BuildProgress] = None) -> Dict[str, Any]:
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
            {"file": f.name, "bytes": f.stat().st_size, "sha256": self._sha256_file(f, progress)}
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
            # What a recipe needs to reproduce the build
            "model_revision": config.model_revision,
            "target_pack_sha256": config.target_pack_sha256,
            "target_version": config.target_version,
            "toolchain_image": config.toolchain_image,
            "max_context_length": config.max_context_length,
            "dataset": self._dataset_ref(config, progress),
            "lcf_version": __version__,
        })
        manifest_path.write_text(json.dumps(info, indent=2), encoding="utf-8")
        return info

    def _dataset_ref(self, config: BuildConfiguration, progress: Optional[BuildProgress]) -> Optional[Dict[str, str]]:
        path = Path(config.dataset_path) if config.dataset_path else None
        if path is None or not path.is_file():
            return None
        return {"name": path.name, "sha256": self._sha256_file(path, progress)}

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

    def _zip_folder(self, folder: Path, archive: Path, progress: BuildProgress):
        """The folder as <name>/... in a ZIP (like shutil.make_archive), stopping after a cancel."""
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(folder, folder.name)
            for path in sorted(folder.rglob("*")):
                arcname = str(Path(folder.name) / path.relative_to(folder))
                if path.is_dir():
                    zf.write(path, arcname)
                    continue
                info = zipfile.ZipInfo.from_file(path, arcname)
                info.compress_type = zipfile.ZIP_DEFLATED
                with open(path, "rb") as src, zf.open(info, "w") as dst:
                    for chunk in iter(lambda: src.read(COPY_CHUNK), b""):
                        self._check_cancelled(progress)
                        dst.write(chunk)

    def _create_golden_artifact(self, config: BuildConfiguration, progress: BuildProgress):
        progress.current_stage = "Archiving"
        progress.progress_percent = 95
        self._check_cancelled(progress)
        
        output_dir = Path(config.output_dir)
        staged = self._staged_output(config)
        ensure_directory(staged)
        
        progress.add_log("Writing build manifest and Model Card...")
        # Only this build's files are staged, so every model file there is its own.
        manifest = self._write_build_manifest(config, staged, progress=progress)
        if not manifest.get("primary"):
            progress.add_warning("The build produced no model file.")
        self._generate_model_card(config, staged, manifest)
        
        archive = self._staging_root(config) / f"{output_dir.name}.zip"
        try:
            progress.add_log("Creating Golden Artifact ZIP...")
            self._zip_folder(staged, archive, progress)
            progress.artifacts.append(str(output_dir.parent / archive.name))
        except BuildCancelled:
            raise
        except Exception as e:
            archive.unlink(missing_ok=True)
            progress.add_error(f"Failed to create Golden Artifact: {e}")
