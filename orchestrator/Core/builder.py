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
import re
import zipfile
from pathlib import Path
from typing import Callable, Dict, List, Optional, Any, Tuple
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
from orchestrator.Core import build_identity, build_records
from orchestrator.tools import local_files
from orchestrator.utils.logging import get_logger
from orchestrator.utils.build_context import stage_build_context
from orchestrator.utils.download import FileLock as DownloadLock
from orchestrator.utils.helpers import copy_hashed, digest_matches, sha256_file
from orchestrator.utils.llama_pin import pinned_source
from orchestrator.utils.sandbox import FALLBACK_USER, container_sandbox, hand_over, sandbox_user

class StagingLock(DownloadLock):
    """The lock of one staging folder (a Hugging Face model's commit); its message names no path."""
    busy = "Another build is staging the same model commit and did not finish within 15 minutes; try again later."


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


STAGING_WATCH_EVERY = 2.0    # seconds between two disk checks while a model is staged
CONTAINER_WATCH_EVERY = 5.0  # seconds between two disk checks while a stage container runs
RESOURCES_EVERY = 5.0        # seconds between two CPU and memory samples of a running stage container

# The line the stage runner (targets/*/modules/build.sh, DECISIONS U9) prints for each of its stages: started
# ("=== Stage 3/4: convert ==="), finished ("... done in 41 s ==="), finished earlier ("... resumed ===") or stopped
# ("... failed (exit 1) ==="). It reaches the build's step and percent like every other step.
STAGE_LINE = re.compile(r"^=== Stage (\d{1,2})/(\d{1,2}): ([a-z][a-z0-9_-]{0,31})"
                        r"(?: (resumed|done in \d+ s|failed \(exit \d+\)))? ===$")
# The percent the stages of one container share, by its log prefix: the importance-matrix job from 50 to 60, the
# build from 60 to 85 (Extracting).
STAGE_BANDS = {"IMATRIX: ": ("Calculating IMatrix", 50, 60), "CONT: ": ("Running modules", 60, 85)}


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

# ModelFormat and OptimizationLevel live in build_formats (the CLI imports them without this module).
from orchestrator.Core.build_formats import ModelFormat, OptimizationLevel  # noqa: E402,F401 - re-exported

TERMINAL_STATES = ("completed", "failed", "cancelled")
IMAGE_LABEL = "org.llm-framework.target"
# The stable name of each target's newest build image (llm-framework/<target>:last-build): the next build reuses its
# layers. The builder never deletes an image (round 7 item 12); older ones are listed for the person.
LAST_BUILD_TAG = "last-build"
# owner/name as used on huggingface.co; no path traversal
HF_REPO_ID = re.compile(r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")
TARGET_ID = re.compile(r"^[A-Za-z0-9][\w.-]{0,63}$")
COMMIT_ID = re.compile(r"^[0-9a-f]{40}$")
# A llama.cpp repository override the target image may fetch the pinned commit from (the rule of the source stage's
# repo_url_ok): https, a host, an optional port and a path of plain characters; no user, password, token or query.
LLAMA_REPO_URL = re.compile(r"https://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~%+/-]*)?", re.ASCII)
LLAMA_REPO_ARG = re.compile(r"^\s*ARG\s+LLAMA_CPP_REPO\b", re.MULTILINE)
IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
# Where the Trivy container sees the saved image (read-only); it never sees the Docker socket.
SCAN_ARCHIVE = "/scan/image.tar"


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
    # The sha256 the calibration dataset must have (a recipe pins it): the build copies the file into its own folder
    # while hashing it and uses only that copy (_stage_dataset), so what was checked is what is used.
    dataset_sha256: Optional[str] = None
    dataset_source: Optional[str] = None  # the file the staged copy was made from (set by _stage_dataset)

    # Recorded during the build for build_info.json (recipes are written from it)
    model_revision: Optional[str] = None      # Hugging Face commit the downloaded files belong to
    target_pack_sha256: Optional[str] = None  # content digest of the target folder used
    target_version: Optional[str] = None      # metadata.version of its target.yml
    toolchain_image: Optional[str] = None     # id of the image that ran the conversion
    llama_cpp_tools: Optional[Dict[str, Any]] = None  # pin and recipe plan of the image's llama.cpp conversion tools
    request_id: Optional[str] = None  # the orchestrator request the build belongs to (shown by `build status`)
    # Local inputs of an AI caller's build (orchestrator.tools.local_files.LocalInputs): pinned files that are checked
    # again right before each container starts; without a pinned model the source is a Hugging Face repo id and is
    # never looked up on disk. None only for a person's own builds (GUI, CLI), whose paths are used as given.
    local_inputs: Optional[Any] = None
    # The disk reservation of the request (BuildRequest.disk_reservation; an AI caller's accepted build): the engine
    # records in it what has landed on disk (_report_landed), so a running build is not counted twice
    disk_reservation: str = ""

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
    # The folder the build was published to: its planned folder, or next to it ("-2") when an earlier build is there
    published: Optional[str] = None
    # The running stage container's CPU and memory (docker_manager.container_resources: cpu_percent, memory_bytes,
    # memory_limit_bytes), sampled every RESOURCES_EVERY seconds; None while no stage container runs
    resources: Optional[Dict[str, float]] = None
    
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
        # The framework's build folder (GUI builds wait there to be saved; artifact_output): build_dir, else the same
        # folder as the output folder (a configuration without it)
        self.build_dir = self.base_dir / Path(str(self._get_conf("build_dir", None)
                                                  or self._get_conf("output_dir", "output"))).expanduser()
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
        """Logs what the Docker check finds at the start (the check itself: validation.check_docker_environment,
        which Mojo's Docker check shares)."""
        from orchestrator.utils.validation import check_docker_environment
        docker = check_docker_environment(self.docker_client)
        if not docker.reachable:
            self.logger.warning(f"Docker environment check failed: {docker.error}")
        elif not docker.docker_cli:
            self.logger.warning("Docker executable not found in PATH.")
        elif not docker.buildx:
            self.logger.warning("Docker BuildX not available, standard build will be used.")

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
                        text = line.decode(errors='replace').rstrip()
                        progress.add_log(f"{prefix}{text}")
                        self._note_stage(progress, prefix, text)
                except Exception as e:  # the container may be removed while streaming
                    self.logger.debug(f"Log stream of {config.build_id} ended: {e}")

            reader = threading.Thread(target=pump, name=f"logs-{config.build_id}", daemon=True)
            reader.start()
            watched = threading.Event()
            full: Dict[str, str] = {}

            def low(problem: str) -> None:
                full["text"] = problem
                progress.add_log(problem)
                try:
                    container.kill()
                except Exception:
                    pass
            # Disk watchdog (design 6.5, Core/disk_plan.py): the container is stopped when the cache disk falls below
            # half the reserve, so the per-user folder always keeps room to record what happened.
            from orchestrator.Core.disk_plan import DiskWatchdog
            DiskWatchdog(Path(self.cache_dir), prefix.strip(": ") or "The build", CONTAINER_WATCH_EVERY, low, watched,
                         check=self._disk_check(config), name=f"disk-watch-{config.build_id}").start()
            threading.Thread(target=self._sample_resources, args=(container, progress, prefix, watched),
                             name=f"resources-{config.build_id}", daemon=True).start()
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
                if full:  # the disk watchdog stopped it
                    from orchestrator.Core.disk_plan import InsufficientSpace
                    raise InsufficientSpace(full["text"]) from e
                stage = prefix.strip(': ').lower() or 'build'
                if _is_read_timeout(e) or time.monotonic() - started >= timeout:
                    raise TimeoutError(f"The {stage} stage ran longer than {timeout} s and was stopped "
                                       f"(setting build_timeout).") from e
                # Anything else (container removed, daemon restarted, API error) keeps its own message.
                raise RuntimeError(f"The {stage} stage failed while waiting for its container: {e}\n"
                                   f"{self._log_tail(progress, prefix)}") from e
            reader.join(timeout=10)
            watched.set()
            self._check_cancelled(progress)
            if full:
                from orchestrator.Core.disk_plan import InsufficientSpace
                raise InsufficientSpace(full["text"])
            return int(result.get("StatusCode", 1))
        finally:
            if "watched" in locals():
                watched.set()
            progress.resources = None
            with self._lock:
                self._active_containers.pop(config.build_id, None)
            try:
                container.remove(force=True)
            except Exception:
                pass

    def _note_stage(self, progress: "BuildProgress", prefix: str, line: str) -> None:
        """A progress line of the stage runner (STAGE_LINE) becomes the build's step and percent, which the build
        status, the Builder and BuildService._live_progress show: "Running modules: convert (3/4)", and the percent
        within the container's band. A stage that started counts the stages before it as done; one that finished or
        resumed counts itself too. The build's record follows, so other processes see the stage as well. A line the log
        reader delivers after the build moved on to its next step (the reader may lag behind the container's end)
        changes nothing."""
        band = STAGE_BANDS.get(prefix)
        match = STAGE_LINE.match(line.strip()) if band else None
        if match is None:
            return
        number, total, name, state = int(match.group(1)), int(match.group(2)), match.group(3), match.group(4) or ""
        if not 1 <= number <= total or state.startswith("failed"):
            return  # a failed stage ends the container: its exit code and log tail report it
        step, low, high = band
        if not str(progress.current_stage or "").startswith(step):
            return
        done = number if state else number - 1
        progress.progress_percent = max(progress.progress_percent, low + (high - low) * done // total)
        label = f"{step}: {name} ({number}/{total}){' resumed' if state == 'resumed' else ''}"
        if label != progress.current_stage:
            progress.current_stage = label
            self._save_record(progress.build_id)

    @staticmethod
    def _sample_resources(container: Container, progress: "BuildProgress", prefix: str, stop: threading.Event) -> None:
        """The running stage container's CPU and memory into progress.resources (the person's Builder shows them under
        the progress bar), until `stop`; one warning in the build log when it nears its memory limit, which is
        Docker's (or the WSL2 / Docker Desktop VM's) when the container has none of its own. A container whose
        statistics cannot be read is not sampled any further; the build goes on."""
        from orchestrator.Core.docker_manager import MEMORY_WARN_SHARE, container_resources, memory_near_limit
        from orchestrator.utils.sizes import format_size
        warned = False
        while not stop.wait(RESOURCES_EVERY):
            try:
                sample = container_resources(container.stats(stream=False))
            except Exception:  # noqa: BLE001 - ended, removed, or a daemon without statistics
                return
            if sample is None:
                continue
            progress.resources = sample
            if stop.is_set():   # the stage ended while it was read: never show a stale sample
                progress.resources = None
                return
            if not warned and memory_near_limit(sample):
                warned = True
                progress.add_log(
                    f"{prefix}The build container uses {format_size(int(sample['memory_bytes']), 'en')} of its "
                    f"{format_size(int(sample['memory_limit_bytes']), 'en')} memory limit "
                    f"({int(MEMORY_WARN_SHARE * 100)} % or more). If it runs out, the build stops: give Docker more "
                    "memory (the resource settings of Docker Desktop, or .wslconfig for WSL2) or close other programs.",
                    "WARNING")

    def _keep_as_last_build(self, tag: str) -> None:
        """Names the image of a build `llm-framework/<target>:last-build` and then drops its per-build name `tag`.
        Removing a name never deletes an image here: the per-build name is dropped only once the image is seen to
        carry the stable name too. The image that had the stable name before keeps its layers without a name; it is
        listed for the person (Tools > Framework Build Images..., 'llm-cli doctor --fix --tier 2'), who alone deletes
        it. A running build is not affected: its containers use the image by id. Framework processes that share the
        cache folder name their images one at a time, so another build cannot move the stable name away between the
        check and the drop (which would leave the per-build name as the image's last one)."""
        from orchestrator.utils.file_lock import file_lock
        repository = tag.rsplit(":", 1)[0]
        with file_lock(Path(self.cache_dir) / ".image-names.lock", timeout=60):
            image = self.docker_client.images.get(tag)
            image.tag(repository, LAST_BUILD_TAG)
            named = self.docker_client.images.get(f"{repository}:{LAST_BUILD_TAG}")
            if getattr(named, "id", None) != getattr(image, "id", None) or \
                    tag not in (getattr(named, "tags", None) or []):
                raise RuntimeError("the stable name did not land on the build's image")
            self.docker_client.images.remove(tag, noprune=True)

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

            # The builder never deletes an image (round 7 item 12): the build's image gets the stable name of its
            # target, then only the per-build name is dropped.
            tag = self._build_images.pop(build_id, None)
            if tag and self.docker_client:
                try:
                    self._keep_as_last_build(tag)
                except Exception as e:
                    self.logger.debug(f"The image of {build_id} keeps its per-build name: {e}")
            
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
            # its next build right away. A cancel is never undone. The record names the outcome before the slot is
            # free: a poller that sees the build ended reads its record next.
            with self._lock:
                if prog.status != BuildStatus.CANCELLED:
                    prog.status = outcome
                prog.end_time = prog.end_time or datetime.now()
                self._save_record(bid)
                self._running.discard(bid)
                self._settled.discard(bid)
                self.__dict__.get("_meters", {}).pop(bid, None)

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
            # what lands here counts against the build's disk reservation (ReservationLedger.landed)
            self._report_landed(config, self.cache_dir / "builds" / bid, self._staging_root(config))
            self._stage_dataset(config, prog)
            
            # 3. Generate Dockerfile
            df_path = self._generate_dockerfile(config, prog, target_path)
            
            # 4. Build Docker Image
            image = self._build_docker_image(config, prog, df_path)
            image_id = getattr(image, "id", None)
            config.toolchain_image = image_id if isinstance(image_id, str) and IMAGE_ID.fullmatch(image_id) else None
            self._check_cancelled(prog)

            # 4a. Make the model available inside the containers
            staged = self._stage_model(config, prog)
            self._report_landed(config)
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
            self._report_landed(config)
            self._create_golden_artifact(config, prog)
            self._report_landed(config)  # the last count: publishing moves files, cleanup only frees space

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
            prog.add_error(self._disk_full_text(e) or str(e))
            self.logger.error(f"Build {bid} failed: {e}", exc_info=True)
            # Try cleanup even on failure
            try: self.cleanup_build(bid)
            except Exception: pass
            return BuildStatus.FAILED

    def _disk_full_text(self, error: BaseException) -> Optional[str]:
        """A full disk (ENOSPC, EDQUOT, or the message a tool printed) as reason insufficient_resources with the free
        space of the build's folders, by label (G40); None for any other error (disk_plan.disk_full_text)."""
        from orchestrator.Core.disk_plan import disk_full_text
        output = self.base_dir / Path(str(self._get_conf("output_dir", "output"))).expanduser()
        builds = Path(getattr(self, "build_dir", output))
        folders = {"cache": Path(self.cache_dir), "output": output, **({"builds": builds} if builds != output else {})}
        return disk_full_text(error, folders)

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

    # At most this many hadolint findings become progress warnings; every finding is in the build log.
    HADOLINT_WARNINGS = 10

    def _validate_dockerfile_hadolint(self, path: Path, prog: BuildProgress):
        """hadolint on the build's Dockerfile, advisory: the build goes on whatever it says (no setting makes the
        check stop a build). Tells apart hadolint not installed (a note on how to get it), findings (each one in the
        build log and as a progress warning) and no findings; a run without a result is said plainly too (round 23,
        item 4: before, every outcome was "skipped" and the findings were thrown away)."""
        from orchestrator.utils.hadolint import INSTALL_HINT, RULES_URL, run_hadolint
        name = path.name
        try:
            result = run_hadolint(path)
        except Exception as e:  # noqa: BLE001 - the check never stops a build
            prog.add_warning(f"Dockerfile check (hadolint) did not run: {e}. The check is advisory; the build goes on.")
            return
        if result.outcome == "missing":
            prog.add_log(f"Dockerfile check skipped: hadolint is not installed on this computer. The check is "
                         f"advisory; the build goes on. {INSTALL_HINT}")
        elif result.outcome == "passed":
            prog.add_log(f"Dockerfile check (hadolint): no findings in {name}.")
        elif result.outcome == "findings":
            prog.add_log(f"Dockerfile check (hadolint): {len(result.findings)} finding(s) in {name} "
                         f"({result.counts()}). The check is advisory; the build goes on. The rules are explained "
                         f"at {RULES_URL}.", "WARNING")
            for n, finding in enumerate(result.findings):
                text = f"hadolint, {name}: {finding.plain()}"
                if n < self.HADOLINT_WARNINGS:
                    prog.add_warning(text)
                else:
                    prog.add_log(text, "WARNING")
            if len(result.findings) > self.HADOLINT_WARNINGS:
                prog.add_warning(f"hadolint: {len(result.findings) - self.HADOLINT_WARNINGS} more finding(s) in "
                                 f"{name}, all in the build log.")
        else:
            prog.add_warning(f"Dockerfile check (hadolint) gave no result for {name}: {result.detail}. The check is "
                             "advisory; the build goes on.")

    def _build_docker_image(self, config: BuildConfiguration, progress: BuildProgress, path: Path) -> Image:
        progress.current_stage = "Building Image"
        progress.progress_percent = 40
        
        tag = f"llm-framework/{config.target_arch.lower()}:{config.build_id.lower()}"
        context = path.parent
        rel_df = path.name

        progress.add_log(f"Building Docker Image: {tag}")
        
        try:
            from orchestrator.runtime.build.host_tools import PINNED_BUILD_ARGS
            buildargs: Dict[str, str] = {}
            build_jobs = int(self._get_conf("build_jobs", 0) or 0)
            if build_jobs > 0:
                buildargs["BUILD_JOBS"] = str(build_jobs)  # lowered by self-healing after an OOM
            given = dict(getattr(config, "build_args", None) or {})
            pinned = sorted(set(given) & set(PINNED_BUILD_ARGS))
            if pinned:
                progress.add_warning(f"Build arguments {', '.join(pinned)} come only from the llama.cpp pin and the "
                                     "build recipe; the values given for this build are not used.")
            buildargs.update({k: v for k, v in given.items() if k not in PINNED_BUILD_ARGS})
            buildargs.update(self._llama_cpp_args(config, progress, path))
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

    def _llama_cpp_args(self, config: BuildConfiguration, progress: BuildProgress, dockerfile: Path) -> Dict[str, str]:
        """LLAMA_CPP_TAG and LLAMA_CPP_COMMIT from the pin, and LLAMA_CMAKE_ARGS from the build recipe's plan for the
        conversion tools (purpose host-tools) on this machine, when the target's Dockerfile takes it. The image runs
        on the build host, so the tools are tuned to the build host's probe (design 4.3). Without a usable probe the
        Dockerfile keeps its default, the fixed x86-64 baseline. Options typed into the module wizard or proposed by
        an AI never reach this: only the recipe's plan does."""
        from orchestrator.runtime.build.host_tools import dockerfile_base, dockerfile_takes_cmake_args, host_tools
        source = pinned_source()
        if not source:
            raise RuntimeError("No llama.cpp pin: llama_cpp.source in configs/llama_runtime.yml is missing or broken")
        setting = str(self._get_conf("llama_cpp_version", "") or "").strip()
        if setting and setting != source["tag"]:
            progress.add_warning(f"The setting llama_cpp_version ({setting}) is not the llama.cpp pin "
                                 f"({source['tag']}): target images build the pinned commit {source['commit'][:12]}. "
                                 "Another llama.cpp version is a change of the pin in configs/llama_runtime.yml.")
        args = {"LLAMA_CPP_TAG": source["tag"], "LLAMA_CPP_COMMIT": source["commit"]}
        try:
            text = dockerfile.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        repo = self._llama_cpp_repo(progress) if LLAMA_REPO_ARG.search(text) else None
        if repo:
            args["LLAMA_CPP_REPO"] = repo
        if not dockerfile_takes_cmake_args(text):
            progress.add_log("The target's Dockerfile takes no LLAMA_CMAKE_ARGS: its llama.cpp build, if it has one, "
                             "does not follow the build recipe.")
            config.llama_cpp_tools = {"tag": source["tag"], "commit": source["commit"], "tuning": "not-recipe"}
            return args
        profile, why = self._build_host_profile(progress)
        try:
            from orchestrator.runtime.catalog import load_catalog
            catalog = load_catalog()
        except Exception as e:  # a broken catalog leaves the Dockerfile's baseline; the pin above still applies
            self.logger.warning(f"Runtime catalog not readable: {e}")
            catalog = None
        tools = host_tools(profile, pin=source, catalog=catalog, base_image=dockerfile_base(text),
                           why_no_profile=why)
        progress.add_log(tools.reason)
        for note in tools.notes:
            progress.add_log(f"llama.cpp plan: {note}")
        config.llama_cpp_tools = tools.record()
        args.update(tools.build_args())
        return args

    def _llama_cpp_repo(self, progress: BuildProgress) -> Optional[str]:
        """Where the target image fetches the pinned llama.cpp commit from: the source_repositories override of
        llama.cpp (LLAMA_CPP_REPO_OVERRIDE, e.g. a mirror inside a company network) when it is an https address
        without a user name, password, token or query (a build argument is visible in the image's history), else
        None (upstream). The image still fetches the pinned commit and stops unless `git rev-parse HEAD` gives it
        back, so a mirror changes where the source comes from, never which source it is."""
        url = self._repo_overrides().get("LLAMA_CPP_REPO_OVERRIDE", "")
        if not url:
            return None
        from orchestrator.Core.settings_view import shown_repo_url, url_credential
        shown = shown_repo_url(url)
        if url_credential(url) or not LLAMA_REPO_URL.fullmatch(url):
            progress.add_warning(f"The llama.cpp repository override {shown} is not used for the image: only an https "
                                 "address without a user name, password, token or query is; the image fetches the "
                                 "pinned commit from upstream.")
            return None
        progress.add_log(f"llama.cpp source: the pinned commit from the repository override {shown} (the image "
                         "checks that it is the pinned commit).")
        return url

    def _build_host_profile(self, progress: BuildProgress) -> Tuple[Any, str]:
        """(profile, reason when there is none) of the machine whose CPU runs the image: a probe now (a fraction of a
        second), else this machine's stored profile. None when the Docker daemon is on another machine."""
        if self._docker_daemon_is_remote():
            return None, "the Docker daemon runs on another machine, which this machine's probe does not describe"
        try:
            from orchestrator.runtime.hardware import local_profile
            return local_profile(), ""
        except Exception as e:  # the stored profile is the fallback
            self.logger.debug(f"Hardware probe for the conversion tools failed: {e}")
        try:
            from orchestrator.runtime.profile import ProfileStore
            state = getattr(self.config, "user_state_dir", None)
            stored = (ProfileStore(state) if state else ProfileStore()).load_local()
        except Exception as e:
            self.logger.debug(f"Stored profile of this machine not readable: {e}")
            stored = None
        if stored is not None:
            progress.add_log("The hardware probe failed; the conversion tools follow this machine's stored profile.")
            return stored, ""
        return None, "the hardware probe of this machine failed and no profile of it is stored (llm-cli scan)"

    def _docker_daemon_is_remote(self) -> bool:
        """True when images are built by a Docker daemon on another machine (DOCKER_HOST or the client's address)."""
        host = os.environ.get("DOCKER_HOST", "").strip()
        if host and not host.startswith(("unix://", "npipe://")):
            return True
        base = str(getattr(getattr(self.docker_client, "api", None), "base_url", "") or "")
        return bool(base) and not base.startswith(("http+docker://localhost", "http+docker://localnpipe",
                                                   "http+unix://"))

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
        # stdin=DEVNULL: over stdio the MCP server's stdin is the protocol stream; a child must never read it (G25)
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors="replace")
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
        folder = None
        try:
            # The scanner never gets the Docker socket (whoever holds it is root on this computer): the image is saved
            # to an archive in a private folder, and Trivy reads only that file, read-only, without capabilities.
            folder, archive = self._save_image_for_scan(image_tag, progress, build_id)
            if archive is None:
                return
            scan_cmd = ["image", "--input", SCAN_ARCHIVE, "--exit-code", "1", "--severity", "HIGH,CRITICAL"]
            trivy_image = self._get_conf('image_trivy', "aquasec/trivy:0.75.0")

            # Detached and registered like the stage containers, so cancel_build can stop it.
            container = self.docker_client.containers.run(
                trivy_image,
                command=scan_cmd,
                volumes={
                    str(archive): {'bind': SCAN_ARCHIVE, 'mode': 'ro'},
                    'trivy_cache': {'bind': '/root/.cache/', 'mode': 'rw'}
                },
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
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
            # Docker and the file system name the archive's host path; build warnings reach AI clients too.
            from orchestrator.security.paths import label_paths
            progress.add_warning(f"Security scan failed to run: {label_paths(str(e), {str(self.cache_dir): 'cache'})}")
        finally:
            if container is not None:
                with self._lock:
                    if self._active_containers.get(build_id) is container:
                        self._active_containers.pop(build_id, None)
                try:
                    container.remove(force=True)
                except Exception:
                    pass
            if folder is not None:
                # The framework's own temporary copy of the image, made for this scan only (the image itself stays).
                shutil.rmtree(folder, ignore_errors=True)
                progress.add_log("Removed the temporary image archive of the scan.")

    def _save_image_for_scan(self, image_tag: str, progress: BuildProgress,
                             build_id: str) -> Tuple[Optional[Path], Optional[Path]]:
        """(private folder, archive): `docker save` of the image into a new folder only this user can open, inside the
        build's temporary folder. (None, None) when the cache disk has no room for it: the scan is skipped with a
        warning that says how much it needs."""
        import tempfile
        from orchestrator.Core.disk_plan import gb, low_disk_floor
        image = self.docker_client.images.get(image_tag)
        size = int((getattr(image, "attrs", None) or {}).get("Size") or 0)
        parent = self.cache_dir / "builds" / build_id
        parent.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(parent)
        floor = low_disk_floor(usage.total)
        if usage.free - size < floor:
            progress.add_warning(f"Security scan skipped: the scanner reads a saved copy of the image (about "
                                 f"{gb(size)}), and the cache disk has {gb(usage.free)} free, of which {gb(floor)} "
                                 "must stay free.")
            return None, None
        folder = Path(tempfile.mkdtemp(prefix="trivy-", dir=parent))  # mode 0700
        archive = folder / "image.tar"
        try:
            with open(archive, "wb") as f:
                for chunk in image.save(named=True):
                    self._check_cancelled(progress)
                    f.write(chunk)
        except BaseException:  # cancelled or the disk ran full: no half archive stays behind
            shutil.rmtree(folder, ignore_errors=True)
            raise
        # Readable for the scanner, which runs without capabilities; the folder around it stays private (0700).
        os.chmod(archive, 0o644)
        progress.add_log(f"Saved the image to a temporary archive for the scan ({gb(archive.stat().st_size)}).")
        return folder, archive

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
            # A cache blob is named by its checksum: a link to it keeps the link's name, any other file its own.
            link_name = local.name if local.is_symlink() else ""
            local = local.resolve()
            linked_name = link_name if link_name and local_files.cache_blob(local) else ""
            progress.add_log(f"Using local model {local}")
            if local.is_dir():
                # A folder of links (a Hugging Face cache snapshot) file by file: its links lead out of it.
                binds = local_files.linked_folder_binds(local, "/build-cache/input-model")
                return "/build-cache/input-model", binds or {str(local): {"bind": "/build-cache/input-model",
                                                                           "mode": "ro"}}
            # Only the file itself: the folder it lies in may hold anything else (G8).
            inner = f"/build-cache/input-model/{linked_name or local.name}"
            return inner, {str(local): {"bind": inner, "mode": "ro"}}

        if not HF_REPO_ID.match(source) or ".." in source:
            raise ValueError(f"Model '{source}' is neither an existing local path nor a HuggingFace repo id (owner/name).")

        from huggingface_hub import HfApi
        from orchestrator.Core.hf_hub import DownloadStopped
        from orchestrator.Core.hf_lookup import staged_files
        # The token stored in the framework, or none at all: never an implicit token (HF_TOKEN, the Hugging Face home
        # folder's token file) the person did not give the framework (G37).
        auth = self._hf_token() or False
        api = HfApi()
        revision = config.model_branch or None
        commit = self._model_commit(api, source, revision, auth)
        if not commit:
            raise ValueError(f"The commit of {source} could not be found out on Hugging Face (not reachable, or no "
                             f"such revision '{revision or 'main'}'); nothing was downloaded.")
        revision = config.model_revision = commit  # listing and download see the same commit
        files = self._hub_files(api, source, commit, auth)
        keep = staged_files(list(files))
        dest = self.staging_folder(source, commit, revision)
        progress.add_log(f"Downloading {source} ({len(keep)} files, commit {commit}) from Hugging Face ...")
        # One folder per commit, under a lock: two builds of different commits never mix files, and two builds of
        # the same commit fetch it once (G38). Through the framework's Hugging Face fetcher (T6): each file resumable
        # from its .part file, checked against the Hub's size and sha256 before it gets its name, and stopped between
        # two chunks by a cancel or by the disk watchdog (G39), which keeps the .part file.
        stop = threading.Event()
        reason: Dict[str, Any] = {}

        def cancelled() -> bool:
            if progress.status == BuildStatus.CANCELLED:
                reason["cancelled"] = True
                stop.set()
                return True
            return False

        def low(problem: str) -> None:
            reason["disk"] = problem
            stop.set()
        from orchestrator.Core.disk_plan import DiskWatchdog
        watcher = DiskWatchdog(dest, "Staging", STAGING_WATCH_EVERY, low, stop, also_stop_when=cancelled,
                               check=self._disk_check(config), name=f"staging-watch-{config.build_id}")
        self._report_landed(config, dest)  # the download counts as landed while it arrives
        fetcher = self._hub_fetcher(auth or None, stop)
        try:
            with StagingLock(dest.parent / f".{dest.name}.lock"):
                watcher.start()
                for name in keep:
                    self._check_cancelled(progress)
                    self._check_staging_space(dest, progress)
                    try:
                        fetcher.fetch_file(source, commit, files[name], dest / name)
                    except DownloadStopped:
                        self._check_cancelled(progress)
                        if reason.get("disk"):
                            from orchestrator.Core.disk_plan import InsufficientSpace
                            raise InsufficientSpace(reason["disk"]) from None
                        raise
        finally:
            stop.set()
        self._check_cancelled(progress)
        inner = "/build-cache/input-model"
        return inner, {str(dest): {"bind": inner, "mode": "ro"}}

    @staticmethod
    def _hub_files(api, source: str, commit: str, auth: Any) -> Dict[str, Any]:
        """The commit's files with the sizes and LFS sha256 the Hub lists (what the fetcher checks each file against)."""
        from orchestrator.Core.hf_hub import HubFile
        info = api.model_info(source, revision=commit, files_metadata=True, token=auth)
        files = {}
        for s in getattr(info, "siblings", None) or []:
            lfs = getattr(s, "lfs", None)
            sha = getattr(lfs, "sha256", None) if lfs is not None and not isinstance(lfs, dict) else \
                (lfs.get("sha256") if isinstance(lfs, dict) else None)
            size = getattr(s, "size", None)
            files[str(s.rfilename)] = HubFile(str(s.rfilename), int(size) if isinstance(size, int) else None,
                                              str(sha).lower() if sha else None)
        return files

    @staticmethod
    def _hub_fetcher(token: Optional[str], stop: threading.Event):
        from orchestrator.Core.hf_hub import HubFetcher
        return HubFetcher(token=token, stop=stop)

    def staging_folder(self, source: str, commit: Optional[str], revision: Optional[str]) -> Path:
        """<cache>/models/hf/<owner>--<name>/<commit>/: Hugging Face forbids '--' in repo ids, so the encoding never
        collides (with '__', 'a/b__c' and 'a__b/c' shared one folder). Without a known commit, a folder per branch."""
        owner, name = source.split("/", 1)
        leaf = commit if commit and COMMIT_ID.fullmatch(commit) else \
            "branch-" + (re.sub(r"[^A-Za-z0-9._-]+", "_", revision or "main")[:64] or "main")
        return self.cache_dir / "models" / "hf" / f"{owner}--{name}" / leaf

    def _check_staging_space(self, folder: Path, progress: BuildProgress) -> None:
        """Disk watchdog for staging: stops it when the file system falls below half the reserve, so the per-user
        folder (audit log, permissions) always keeps room to record what happened."""
        from orchestrator.Core.disk_plan import InsufficientSpace
        problem = self._low_disk_text(folder, "Staging")
        if problem:
            raise InsufficientSpace(problem)

    @staticmethod
    def _low_disk_text(folder: Path, what: str) -> Optional[str]:
        """Why `what` must stop when the file system of `folder` has fallen below half the reserve (design 6.5), or
        None while it has room (disk_plan.low_disk_text)."""
        from orchestrator.Core.disk_plan import low_disk_text
        return low_disk_text(folder, what)

    def _report_landed(self, config: BuildConfiguration, *folders: Path) -> None:
        """Records in the build's disk reservation (config.disk_reservation, an AI caller's accepted build) what of it
        is on disk now, so the free space and the reservation do not count those bytes twice for every other check
        (disk_plan.LandedMeter, ReservationLedger.landed); `folders` are watched from now on. Nothing without a
        reservation or a per-user folder; a ledger that cannot be written never stops a build."""
        rid = getattr(config, "disk_reservation", "")
        if not rid:
            return
        meters = self.__dict__.setdefault("_meters", {})
        try:
            meter = meters.get(config.build_id)
            if meter is None:
                # the ledger of the per-user folder, as the build service took the reservation in it
                # (build_plan.state_dir; ConfigManager raises AttributeError for a name it does not know)
                try:
                    folder = getattr(self.config, "user_state_dir", None)
                except Exception:  # noqa: BLE001
                    folder = None
                if not isinstance(folder, (str, Path)) or not str(folder):
                    return
                from orchestrator.Core.disk_plan import LandedMeter, ReservationLedger
                meter = meters.setdefault(config.build_id, LandedMeter(ReservationLedger(Path(folder)), rid))
            meter.watch(*folders)
            meter.report()
        except Exception as e:  # noqa: BLE001 - the reservation is released when the request ends anyway
            self.logger.debug(f"Disk reservation of {config.build_id} not updated: {e}")

    def _disk_check(self, config: BuildConfiguration) -> Callable[[Path, str], Optional[str]]:
        """The check of a build's disk watchdogs: records what has landed (_report_landed), then _low_disk_text."""
        def check(folder: Path, what: str) -> Optional[str]:
            self._report_landed(config)
            return self._low_disk_text(folder, what)
        return check

    @staticmethod
    def _model_commit(api, source: str, revision: Optional[str], token: Any = False) -> Optional[str]:
        """The commit a branch or tag points to (None when it cannot be found out)."""
        if revision and COMMIT_ID.fullmatch(revision):
            return revision
        try:
            sha = getattr(api.model_info(source, revision=revision, token=token), "sha", None)
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
        if config.model_revision:
            # The Hugging Face commit of the staged files: part of the resume digest of the stage runner (build.sh).
            env["MODEL_REVISION"] = config.model_revision
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

    def _stage_dataset(self, config: BuildConfiguration, progress: BuildProgress) -> None:
        """A calibration dataset pinned by sha256 (a recipe's): the file is opened once (never through a link), its
        bytes are copied into the build's own folder while they are hashed, and the copy is used only when its sha256
        is the pinned one. From here on the build reads only that copy, so a file swapped after the check (at the
        question, at the start) is never used."""
        if not config.dataset_sha256 or not config.dataset_path:
            return
        source = Path(config.dataset_path)
        folder = self.cache_dir / "builds" / config.build_id / "dataset"
        folder.mkdir(parents=True, exist_ok=True)
        folder.chmod(0o700)
        target = folder / (source.name or "dataset.txt")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        try:
            fd = os.open(str(source), flags)
        except OSError:
            raise ValueError(f"The calibration dataset {source.name} cannot be opened; nothing was built.") from None
        try:
            import stat
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError(f"The calibration dataset {source.name} is not a regular file; nothing was built.")
            digest = copy_hashed(fd, target)  # the bytes copied are the bytes hashed
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        finally:
            os.close(fd)
        if not digest_matches(digest, config.dataset_sha256):
            target.unlink(missing_ok=True)
            raise ValueError(f"The calibration dataset {source.name} is not the file that was checked (its sha256 "
                             f"differs from {str(config.dataset_sha256)[:16]}...); nothing was built.")
        config.dataset_source, config.dataset_path = str(source), str(target)
        progress.add_log(f"Calibration dataset {source.name} copied into the build (sha256 checked).")

    def _dataset_mount(self, config: BuildConfiguration,
                       staged_volumes: Optional[Dict[str, Dict[str, str]]] = None) -> Tuple[Dict[str, Dict[str, str]], Optional[str]]:
        """Volumes and in-container path for the calibration dataset (none if no dataset is set).
        Text datasets keep their suffix (RKLLM reads .json as a JSON dataset). An RKNN dataset lists sample
        files relative to its own directory, so that directory is needed. A dataset inside an already
        mounted folder (often the model folder) is reached through that mount: docker-py keys binds by
        host path, so a second bind of the same folder would replace the first."""
        if not config.dataset_path:
            return {}, None
        if config.dataset_source:
            return self._staged_dataset_mount(config, staged_volumes)
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

    def _staged_dataset_mount(self, config: BuildConfiguration, staged_volumes: Optional[Dict[str, Dict[str, str]]]
                              ) -> Tuple[Dict[str, Dict[str, str]], Optional[str]]:
        """The mount of a dataset copied into the build (_stage_dataset): always the checked copy. An RKNN sample list
        names files relative to its folder, so that folder is mounted as before and the checked copy is laid over the
        list's own name in it."""
        copy = Path(str(config.dataset_path))
        if not copy.is_file() or copy.is_symlink():
            raise ValueError(f"The checked copy of the calibration dataset {copy.name} is gone; nothing was run.")
        if config.target_format != ModelFormat.RKNN:
            suffix = copy.suffix.lower() if copy.suffix.lower() in (".txt", ".json") else ".txt"
            inner = f"/build-cache/dataset{suffix}"
            return {str(copy): {"bind": inner, "mode": "ro"}}, inner
        pin = getattr(config.local_inputs, "dataset", None)
        original = pin.verify() if pin is not None else Path(str(config.dataset_source)).expanduser().resolve()
        for host, bind in (staged_volumes or {}).items():
            if not Path(host).is_dir():
                continue
            try:
                rel = original.relative_to(Path(host).resolve())
            except ValueError:
                continue
            inner = f"{bind['bind'].rstrip('/')}/{rel.as_posix()}"
            return {str(copy): {"bind": inner, "mode": "ro"}}, inner
        folder = local_files.dataset_folder(pin) if pin is not None else original.parent
        inner = f"/build-cache/dataset/{original.name}"
        return {str(folder): {"bind": "/build-cache/dataset", "mode": "ro"},
                str(copy): {"bind": inner, "mode": "ro"}}, inner

    @staticmethod
    def _verify_local_inputs(config: BuildConfiguration) -> None:
        """Right before a container starts: the files an AI caller named are still the ones it was shown (same
        identity, size and modification time, still inside a folder the AI may use)."""
        inputs = config.local_inputs
        if getattr(inputs, "model", None) is not None:
            inputs.model.verify()
        if getattr(inputs, "dataset", None) is not None and config.dataset_path:
            inputs.dataset.verify()

    SANDBOX_FALLBACK_USER = FALLBACK_USER  # the image's build user, used when the host user is root or on Windows

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

        # The same sandbox as the llama.cpp builder container (orchestrator/utils/sandbox.py).
        user = sandbox_user()
        hand_over(build_temp, user)  # a root host owns the per-build folders; hand them to the container user
        memory = str(self._get_conf("build_memory_limit", "") or "").strip()
        kwargs: Dict[str, Any] = container_sandbox(
            user, working_dir="/build-cache/work", mem_limit=memory or None,
            pids_limit=int(self._get_conf("build_pids_limit", 4096) or 4096))
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
            # the model arrives through staged[1]: only its own folder (one commit of a Hugging Face model) or file
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
            # The model arrives through staged[1]: only its own folder (one commit of a Hugging Face model) or file,
            # never the whole model cache with other models and commits.
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

    @staticmethod
    def _free_folder(dst: Path) -> Path:
        """The folder a build is published to: `dst`, or dst-2, dst-3, ... next to it when an earlier finished build
        (its manifest or its golden ZIP) is there. A golden artifact is never replaced by another build (eighth round,
        answer 3: golden artifacts are deleted only by the person)."""
        def taken(folder: Path) -> bool:
            return (folder / BUILD_MANIFEST).exists() or os.path.lexists(folder.parent / f"{folder.name}.zip")

        candidate, n = dst, 1
        while taken(candidate):
            n += 1
            candidate = dst.with_name(f"{dst.name}-{n}")
        return candidate

    def _publish_artifacts(self, config: BuildConfiguration, progress: BuildProgress):
        """Moves the staged files into the output folder (the manifest last) and the ZIP next to it. When an earlier
        build is in that folder, this one goes next to it (_free_folder): nothing of an earlier build is replaced."""
        root = self._staging_root(config)
        if not root.is_dir():
            return
        staged, planned = self._staged_output(config), Path(config.output_dir)
        dst = self._free_folder(planned)
        if dst != planned:
            progress.add_log(f"An earlier build is in {planned}; this build goes next to it: {dst}")
        files = sorted((f for f in staged.rglob("*") if f.is_file()),
                       key=lambda f: (f.name == BUILD_MANIFEST, str(f))) if staged.is_dir() else []
        ensure_directory(dst)
        for f in files:
            target = dst / f.relative_to(staged)
            ensure_directory(target.parent)
            self._move(f, target)
        archive = root / f"{planned.name}.zip"
        if archive.is_file():
            self._move(archive, dst.parent / f"{dst.name}.zip")
            progress.add_log(f"✅ Golden Artifact created: {dst.parent / f'{dst.name}.zip'}")
        if dst != planned:  # the artifacts are where they were published
            moved = []
            for item in progress.artifacts:
                path = Path(item)
                if path == planned.parent / f"{planned.name}.zip":
                    moved.append(str(dst.parent / f"{dst.name}.zip"))
                    continue
                try:
                    moved.append(str(dst / path.relative_to(planned)))
                except ValueError:
                    moved.append(item)
            progress.artifacts[:] = moved
            info = self._records.get(config.build_id)
            if info is not None:
                info["output_dir"] = str(dst)
        progress.published = str(dst)
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
        """sha256 of a model file; a cancelled build stops it between two chunks (BuildCancelled)."""
        return sha256_file(path, cancel=None if progress is None else lambda: BuildEngine._check_cancelled(progress))

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
        dataset = self._dataset_ref(config, progress)
        options = self._build_options(config, info, dataset)
        info.update({
            "build_id": config.build_id,
            # A local model an AI caller pinned is recorded by its label, never its host path (it reaches AI callers)
            "model_source": getattr(getattr(config.local_inputs, "model", None), "ref", None) or config.model_source,
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
            "llama_cpp_tools": config.llama_cpp_tools,
            "max_context_length": config.max_context_length,
            "dataset": dataset,
            "lcf_version": __version__,
            # Every option this build was made with: an identical build is found by them (eighth round, N11)
            build_identity.MANIFEST_KEY: options,
            build_identity.DIGEST_KEY: build_identity.options_sha256(options),
        })
        manifest_path.write_text(json.dumps(info, indent=2), encoding="utf-8")
        return info

    @staticmethod
    def _build_options(config: BuildConfiguration, info: Dict[str, Any],
                       dataset: Optional[Dict[str, str]]) -> Dict[str, Any]:
        """The chosen options of this build (build_identity): a Hugging Face model by its repo id and the commit that
        was fetched, a local model by its weights files (name, size, time; never its path), the target and the sha256
        of its folder, board, format, quantization, importance matrix, calibration data, optimization, context
        length, GPU use and task."""
        pin = getattr(config.local_inputs, "model", None)
        local, is_local = None, pin is not None  # a local model is never named by its path here (AIs read manifests)
        if pin is not None:
            local = Path(pin.path) if getattr(pin, "path", None) else None
        elif config.local_inputs is None and config.model_source:
            candidate = Path(config.model_source).expanduser()
            if candidate.exists():
                local, is_local = candidate.resolve(), True
        return build_identity.build_options(
            model=None if is_local else config.model_source,
            revision=None if is_local else config.model_revision,
            weights=build_identity.local_weights(local) if local is not None else None,
            target=config.target_arch, target_sha256=config.target_pack_sha256,
            board=config.target_board or info.get("board"), fmt=config.target_format.value,
            quantization=config.quantization or info.get("quantization"),
            imatrix=bool(config.use_imatrix and config.target_format == ModelFormat.GGUF),
            dataset_sha256=(dataset or {}).get("sha256"), optimization=config.optimization_level.value,
            max_context_length=config.max_context_length, use_gpu=config.use_gpu, model_task=config.model_task)

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
