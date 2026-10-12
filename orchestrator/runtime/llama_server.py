#!/usr/bin/env python3
"""
Managed llama-server (llama.cpp) for Ditto's local/offline mode.

`install()` builds llama-server on this machine from the pinned llama.cpp commit (orchestrator/runtime/build/host.py:
the CPU build first, then the GPU build the recipe chooses, each verified before it is kept), `import_build()` takes
a build made on another machine of the same kind (checked against the pin and verified here), and
`install_prebuilt()` downloads the official prebuilt archive only on explicit request (decision D1; size and sha256
are checked before anything is unpacked, and it is always shown as "prebuilt, not self-built"). Pinned GGUF models are
downloaded the same way. `start()` starts only a build that passed its verification on this machine and still matches
its manifest (or the opted-in prebuilt archive), on 127.0.0.1 with a free port and a random per-start API key that
reaches the server only through a 0600 key file, with a cleared environment and without any option of the shared
forbidden list (orchestrator/runtime/server_policy.py, decision D22). It hands base_url, key and model to the provider
layer (read_endpoint; LLMClient uses it for the "local" provider). The server never gets its own tools, agent mode,
MCP servers or web UI: Ditto's tools run inside the framework, where approval is enforced.

Files under <user state dir>/runtime/llama.cpp/:
  builds/<build id>/    self-built and imported builds with build.json and manifest.json (build/store.py)
  src/<commit>/         the verified llama.cpp source (build/source.py)
  b<build>-<variant>/   an opted-in prebuilt archive, unpacked, with its manifest (runtime.json)
  active.json           the active build and the CPU fallback (build_id, fallback_build_id), which one is used
                        ("use": self-built or prebuilt), the prebuilt variant, and the model tier of `start --model`
  server.json           pid, start time, port and model of the running server; trusted only while that
                        exact process (pid + start time + executable) is alive
  server-<random>.key   API key of the running server (0600, deleted on stop)
  downloads/            archives while they are fetched (.part files resume)
Models: <models_dir>/ditto/<file>.gguf plus a .<file>.verified.json marker. Log: <state dir>/logs/llama-server.log.
"""

import atexit
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import psutil
import requests
from pydantic import BaseModel, Field

from orchestrator.runtime.catalog import GPU_BACKENDS, ModelEntry, RuntimeAsset, RuntimeCatalog, load_catalog
from orchestrator.runtime.hardware import (BackendChoice, HardwareInfo, Runner, backend_choice, choose_model_tier,
                                           compute_context, detect_hardware, normalize_platform, select_backend)
from orchestrator.runtime.server_policy import FORBIDDEN_FLAGS, forbidden_in, server_env  # noqa: F401 (re-export)
from orchestrator.runtime.server_policy import (LOOPBACK, PolicyError, check_server_command, server_command,
                                                write_key_file)
from orchestrator.utils.download import DownloadError, FileLock, PinnedDownloader
from orchestrator.utils.download import IntegrityError as DownloadIntegrityError
from orchestrator.utils.helpers import redact_secrets, safe_extract_archive
from orchestrator.utils.logging import get_logger

APP_ROOT = Path(__file__).resolve().parents[2]
MIB = 1024 ** 2
SERVER_NAMES = ("llama-server", "llama-server.exe")

# The forbidden options and the environment rules are shared by every launcher (decision D22):
# orchestrator/runtime/server_policy.py.
_LOG_HEADER = "=== llama-server start"

ProgressFn = Callable[[str, int, Optional[int]], None]


class LocalRuntimeError(DownloadError):
    """User-facing runtime problem (the message is safe to show). log_tail is llama-server output: untrusted
    text that must go through orchestrator.utils.untrusted.wrap_untrusted before it reaches a model."""

    def __init__(self, message: str, log_tail: str = ""):
        super().__init__(message)
        self.log_tail = log_tail


class IntegrityError(LocalRuntimeError, DownloadIntegrityError):
    """A download is not pinned or does not match its pin; nothing was installed."""


PREBUILT_LABEL = "prebuilt, not self-built"


class InstalledRuntime(BaseModel):
    """The llama-server build start() uses: a self-built or imported build of the store (origin, build_id, commit,
    verification) or the opted-in official archive (origin "prebuilt", asset_id)."""
    build: str
    variant: str
    backend: str
    path: Path
    server: Path
    files: Dict[str, str]
    origin: str = "prebuilt"                 # self-built | imported | prebuilt
    asset_id: Optional[str] = None           # prebuilt: the catalog archive
    build_id: Optional[str] = None           # self-built and imported builds
    commit: Optional[str] = None
    role: Optional[str] = None               # active | fallback (the CPU build used when the active one cannot be)
    placement: Optional[str] = None          # native | container
    built_on: Optional[str] = None           # os/arch of the machine that built it (imported builds)
    built_at: Optional[str] = None
    verified_at: Optional[str] = None
    duration_s: Optional[float] = None
    devices: List[str] = Field(default_factory=list)   # what --list-devices showed at the verification
    notes: List[str] = Field(default_factory=list)

    @property
    def label(self) -> str:
        if self.origin == "prebuilt":
            return PREBUILT_LABEL
        if self.origin == "imported":
            return f"self-built on another machine ({self.built_on or 'unknown'}), imported"
        return "self-built"

    @property
    def name(self) -> str:
        """Short name for messages: the build ID or the catalog archive."""
        return f"build {self.build_id[:16]}" if self.build_id else str(self.asset_id or self.variant)


class LocalEndpoint(BaseModel):
    """What the provider layer needs: base_url, key and the one model the server serves."""
    base_url: str
    api_key: str = Field(repr=False)
    model: str
    port: int
    pid: int


class RuntimeStatus(BaseModel):
    build: str
    installed: Optional[InstalledRuntime] = None
    problems: List[str] = Field(default_factory=list)   # why a stored build cannot be used
    rebuild: Optional[str] = None                        # "required: ..." / "recommended: ..." (decision D16)
    running: bool = False
    healthy: bool = False
    pid: Optional[int] = None
    port: Optional[int] = None
    model: Optional[str] = None
    tier: Optional[str] = None
    ctx: Optional[int] = None
    base_url: Optional[str] = None
    started_at: Optional[str] = None
    log_file: Path


# --- helpers -----------------------------------------------------------------------------------------------

def user_data_dir() -> Path:
    """Per-user framework directory (same rule as ConfigManager.user_state_dir)."""
    path = Path(os.environ.get("LLM_FRAMEWORK_HOME") or Path.home() / ".llm-framework")
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        try:
            path.chmod(0o700)
        except OSError:
            pass
    return path


def framework_models_dir(value: Optional[str] = None) -> Path:
    """The framework's models directory: `value`, else the effective setting models_dir (LLM_FRAMEWORK_MODELS_DIR,
    config.yml, or the per-user folder when the installation is read-only), as from_framework uses it. Relative
    values are relative to the installation, not the cwd."""
    if not value:
        try:
            from orchestrator.Core.config_manager import ConfigManager
            value = ConfigManager().get("models_dir", None)
        except Exception as e:  # settings that cannot be read: the variable or the installation's folder
            get_logger("LocalLLMRuntime").warning(f"Could not read the setting models_dir ({e}).")
    path = Path(value or os.environ.get("LLM_FRAMEWORK_MODELS_DIR") or "models").expanduser()
    return path if path.is_absolute() else APP_ROOT / path


def runtime_root(data_dir: Path) -> Path:
    return Path(data_dir) / "runtime" / "llama.cpp"


def _write_private(path: Path, text: str) -> None:
    """Writes a file only the current user can read (created 0600, replaced atomically)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _read_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


_RE_PLATFORM_PART = re.compile(r"^[a-z0-9_]{1,16}$", re.ASCII)
_RE_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})?$", re.ASCII)


def _platform_of(value: Any) -> Dict[str, str]:
    """{"os", "arch"} of a build host as a manifest records it; a manifest of an imported build is unsigned data, so
    anything but a short plain name becomes "unknown" (it is shown, logged and written into labels)."""
    value = value if isinstance(value, dict) else {}
    return {k: v if isinstance(v, str) and _RE_PLATFORM_PART.fullmatch(v) else "unknown"
            for k, v in (("os", value.get("os")), ("arch", value.get("arch")))}


def _timestamp_of(value: Any) -> Optional[str]:
    """An ISO 8601 time as a manifest records it, else None (unsigned data, see _platform_of)."""
    return value if isinstance(value, str) and _RE_TIMESTAMP.fullmatch(value) else None


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((LOOPBACK, 0))
        return s.getsockname()[1]


def build_server_command(server: Path, model: Path, port: int, key_file: Path, ctx: int, alias: str,
                         gpu: bool, cache_ram_mib: int, sleep_idle_seconds: Optional[int] = None) -> List[str]:
    """The complete llama-server command line, from the shared policy (server_policy.server_command): only loopback,
    only the key file, no web UI, no /slots, offline, and none of the forbidden options (no tools, agent, MCP)."""
    extra = ["-c", str(ctx), "--cache-ram", str(cache_ram_mib)]
    if gpu:
        extra += ["-ngl", "auto"]  # offload as many layers as fit (llama.cpp's --fit is on by default)
    if sleep_idle_seconds:
        extra += ["--sleep-idle-seconds", str(sleep_idle_seconds)]
    try:
        return server_command(server, model, port=port, key_file=key_file, alias=alias, extra=extra)
    except PolicyError as e:
        raise LocalRuntimeError(str(e)) from None


def popen_options(os_name: str = os.name) -> Dict[str, Any]:
    """POSIX: own session, so the whole process group can be signalled. Windows: no console window and an
    own process group."""
    if os_name == "posix":
        return {"start_new_session": True}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)}


def _process_paths(proc: psutil.Process) -> List[str]:
    """Executable and the first two argv entries (argv[1] is the script when an interpreter runs it)."""
    paths = list((proc.cmdline() or [])[:2])
    try:
        paths.append(proc.exe())
    except (psutil.AccessDenied, psutil.ZombieProcess):
        pass
    return [p for p in paths if p]


def owned_process(state: Optional[dict]) -> Optional[psutil.Process]:
    """The recorded server process, only if pid, start time and executable still match (pids get reused)."""
    if not state:
        return None
    try:
        proc = psutil.Process(int(state["pid"]))
        if abs(proc.create_time() - float(state["create_time"])) > 0.01:
            return None
        if proc.status() == psutil.STATUS_ZOMBIE:
            return None
        server = Path(state["server"]).resolve()
        if not any(Path(p).resolve() == server for p in _process_paths(proc)):
            return None
        return proc
    except (psutil.Error, KeyError, ValueError, TypeError, OSError):
        return None


def read_endpoint(root: Path) -> Optional[LocalEndpoint]:
    """base_url, key and model of the managed server under `root`, or None if it is not running. Reads only
    files of the current user; used by LLMClient in any process (GUI, CLI, MCP server)."""
    root = Path(root)
    state = _read_json(root / "server.json")
    if owned_process(state) is None:
        return None
    try:
        key_file = Path(state["key_file"])
        if key_file.parent.resolve() != root.resolve() or not key_file.name.startswith("server-"):
            return None  # the key is only ever read from the runtime's own folder
        port = int(state["port"])
        if not 0 < port < 65536:
            return None
        key = key_file.read_text(encoding="utf-8").strip()
        return LocalEndpoint(base_url=f"http://{LOOPBACK}:{port}/v1", api_key=key, model=str(state["alias"]),
                             port=port, pid=int(state["pid"]))
    except (OSError, KeyError, TypeError, ValueError):
        return None


class _FileLock(FileLock):
    error = LocalRuntimeError
    busy = "The local runtime is busy (another llm-cli or GUI holds {path}); try again when it has finished."


class _Downloads(PinnedDownloader):
    error = LocalRuntimeError
    integrity_error = IntegrityError
    lock_class = _FileLock


def _job_kill_on_close(pid: int):
    """Windows: puts the process into a job object that ends it when this process exits (the returned handle
    must stay open). None when the job cannot be set up; stopping then relies on stop()."""
    import ctypes
    from ctypes import wintypes

    class IoCounters(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount",
                                                      "OtherOperationCount", "ReadTransferCount",
                                                      "WriteTransferCount", "OtherTransferCount")]

    class BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # own instance: the argtypes stay local
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = ExtendedLimits()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    process = kernel32.OpenProcess(0x0100 | 0x0001, False, pid)  # PROCESS_SET_QUOTA | PROCESS_TERMINATE
    ok = bool(process) and bool(kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))) \
        and bool(kernel32.AssignProcessToJobObject(job, process))  # 9: JobObjectExtendedLimitInformation
    if process:
        kernel32.CloseHandle(process)
    if not ok:
        kernel32.CloseHandle(job)
        return None
    return job


# --- runtime -----------------------------------------------------------------------------------------------

class LocalLLMRuntime:
    """Install, start and stop the managed llama-server; choose Ditto's model for this machine."""

    def __init__(self, data_dir: Optional[Path] = None, models_dir: Optional[Path] = None,
                 catalog: Optional[RuntimeCatalog] = None, hardware: Optional[HardwareInfo] = None,
                 session: Optional[requests.Session] = None, runner: Optional[Runner] = None,
                 sleep: Callable[[float], None] = time.sleep, *, profile: Any = None, toolchain: Any = None,
                 engine: Any = None, executor: Any = None, verifier: Any = None,
                 platform: Optional[Tuple[str, str]] = None):
        """profile (HardwareProfile), toolchain (DetectedToolchain), engine (ContainerEngine), executor and verifier
        replace the probe, the toolchain detection, Docker and the build steps (tests); platform is (os, arch) of this
        machine."""
        self.logger = get_logger("LocalLLMRuntime")
        self.catalog = catalog or load_catalog()
        self.data_dir = Path(data_dir) if data_dir else user_data_dir()
        self.models_dir = Path(models_dir) if models_dir else framework_models_dir() / "ditto"
        self.root = runtime_root(self.data_dir)
        self._profile = profile
        self._toolchain = toolchain
        self._toolchain_full = toolchain is not None  # a given toolchain is used as it is
        self._engine = engine
        self._executor = executor
        self._verifier = verifier
        self._platform = platform
        self.state_file = self.root / "server.json"
        self.active_file = self.root / "active.json"
        self.lock_file = self.root / ".lock"
        self.log_file = self.data_dir / "logs" / "llama-server.log"
        self._hardware = hardware
        self._hardware_given = hardware is not None  # a caller's HardwareInfo is used as given, never re-probed
        self._runner = runner
        self._session = session
        self._sleep = sleep
        self._popen = subprocess.Popen          # replaced by tests
        self._process_iter = psutil.process_iter
        self._proc: Optional[subprocess.Popen] = None
        self._job = None
        # Loopback calls must never go through an HTTP(S)_PROXY from the environment.
        self._local = requests.Session()
        self._local.trust_env = False

    @classmethod
    def from_framework(cls, framework, **kwargs) -> "LocalLLMRuntime":
        """Uses the framework's per-user directory and models directory."""
        return cls(data_dir=framework.config.user_state_dir,
                   models_dir=framework.resolve_path(framework.config.get("models_dir", "models")) / "ditto",
                   **kwargs)

    def _lock(self) -> _FileLock:
        return _FileLock(self.lock_file, sleep=self._sleep)

    # --- decisions ------------------------------------------------------------------------------------------
    @property
    def build(self) -> str:
        return self.catalog.llama_cpp.build

    def hardware(self, refresh: bool = False) -> HardwareInfo:
        if self._hardware is None or refresh:
            self._hardware = detect_hardware(self._runner)
        return self._hardware

    def platform(self) -> Tuple[str, str]:
        """(os, arch) of this machine in the recipe's words."""
        return self._platform or normalize_platform()

    def profile(self, refresh: bool = False):
        """The full hardware profile of this machine (a fresh probe; volatile values are not used from it)."""
        if self._profile is None or refresh:
            from orchestrator.runtime.hardware import local_profile
            self._profile = local_profile()
        return self._profile

    def toolchain(self, full: bool = True):
        """This machine's toolchain for a native build (orchestrator/runtime/build/toolchain.py). full=False skips
        the compile checks (what -march=native expands to, OpenMP), which only a build plan needs."""
        if self._toolchain is None or (full and not getattr(self, "_toolchain_full", False)):
            from orchestrator.runtime.build.toolchain import detect_toolchain
            os_name, arch = self.platform()
            self._toolchain = detect_toolchain(system=os_name, machine=arch, full=full)
            self._toolchain_full = full
        return self._toolchain

    def engine(self):
        """The container engine for builds in the builder container (Docker through docker-py)."""
        if self._engine is None:
            from orchestrator.runtime.build.executor import DockerEngine
            self._engine = DockerEngine()
        return self._engine

    @property
    def store(self):
        """The store of self-built and imported builds (shares runtime_root and active.json)."""
        from orchestrator.runtime.build.store import BuildStore
        return BuildStore(self.data_dir)

    def select_backend(self, prefer: Optional[str] = None) -> BackendChoice:
        """The official prebuilt archive for this machine: only for `install_prebuilt` (the opt-in of decision D1)."""
        return select_backend(self.hardware(), self.catalog, prefer)

    def recommend_backend(self) -> Tuple[str, str]:
        """(backend, reason) the self-built install makes active on this machine: the recipe's choice
        (recipe.choose_backend, which follows OS, architecture and C library) when it is built here, else the CPU."""
        from orchestrator.runtime.build.host import planned_backend
        from orchestrator.runtime.build.recipe import ToolchainInfo
        try:
            detected = self.toolchain(full=False)
            profile = self.profile()
            placement = "native" if detected.native_ready else "container"
            info = detected.info if placement == "native" else ToolchainInfo(host_os=detected.info.host_os,
                                                                             host_arch=detected.info.host_arch)
            return planned_backend(profile, info, self.catalog, placement)
        except Exception as e:  # a probe or detection that fails: the CPU build is always planned
            return "cpu", f"the GPU backend could not be chosen ({e})"

    def choose_tier(self, prefer: Optional[str] = None) -> Tuple[ModelEntry, str]:
        """Recommended Ditto model for this machine (RAM, and VRAM when a GPU build is used), with the reason. The
        build in use decides the backend; before anything is built, the backend the self-built install would use
        (`prefer` names one explicitly)."""
        installed = self.installed()
        hw = self.hardware()
        if installed is not None and installed.origin == "prebuilt":
            choice = self.select_backend(installed.backend)
        elif installed is not None:
            choice = backend_choice(hw, installed.backend)
        elif prefer and prefer != "auto":
            choice = backend_choice(hw, prefer)
        else:
            choice = backend_choice(hw, self.recommend_backend()[0])
        return choose_model_tier(hw, self.catalog, choice)

    def preferred_tier(self) -> Optional[str]:
        """The tier chosen with `start --model`, if the catalog still has it."""
        tier = (_read_json(self.active_file) or {}).get("tier")
        return tier if isinstance(tier, str) and tier in {m.tier for m in self.catalog.models} else None

    def resolve_model(self, tier: Optional[str] = None) -> Tuple[ModelEntry, str]:
        """Explicit tier > the tier chosen earlier > the recommendation for this machine."""
        try:
            if tier and tier != "auto":
                return self.catalog.model(tier), "chosen"
            remembered = None if tier == "auto" else self.preferred_tier()
            if remembered:
                return self.catalog.model(remembered), "chosen earlier ('--model auto' returns to the recommendation)"
        except KeyError as e:
            raise LocalRuntimeError(e.args[0]) from None
        return self.choose_tier()

    def _update_active(self, **values) -> None:
        data = _read_json(self.active_file) or {}
        data.update(values)
        _write_private(self.active_file, json.dumps({k: v for k, v in data.items() if v is not None}))

    # --- runtime install --------------------------------------------------------------------------------------
    def install_dir(self, asset: RuntimeAsset) -> Path:
        return self.root / f"{self.build}-{asset.variant}"

    def _read_manifest(self, path: Path) -> Optional[InstalledRuntime]:
        data = _read_json(path / "runtime.json")
        try:
            inst = InstalledRuntime(path=path, server=path / data["server"], origin="prebuilt",
                                    verified_at=data.get("installed_at"), **{
                                        k: data[k] for k in ("build", "asset_id", "variant", "backend", "files")})
        except (KeyError, TypeError, ValueError):
            return None
        return inst if inst.server.is_file() and inst.build == self.build else None

    def installed(self) -> Optional[InstalledRuntime]:
        """The build start() uses, or None (resolve_installed() says why)."""
        return self.resolve_installed()[0]

    def _store_problem(self, build, build_id: str, role: str) -> Optional[str]:
        """Why a stored build cannot be started (None: it can). Only a build that passed its verification on this
        machine, still matches its manifest, is of the pinned commit and has a llama-server for this machine runs."""
        what = "active build" if role == "active" else "CPU fallback build"
        if build is None:
            return f"the {what} {build_id[:16]} is missing or incomplete"
        if not build.verified:
            return f"the {what} {build.build_id[:16]} has not passed its verification on this machine"
        problems = self.store.check_files(build)
        if problems:
            return f"the {what} {build.build_id[:16]} does not match its manifest ({'; '.join(problems[:3])})"
        pin = self.catalog.llama_cpp.source
        if pin is not None and build.commit != pin.commit:
            return (f"the {what} {build.build_id[:16]} is of llama.cpp commit {build.commit[:8]}, but the pin is "
                    f"{pin.tag} ({pin.commit[:8]})")
        target = build.plan.get("target") or {}
        machine = self.platform()
        if (target.get("os"), target.get("arch")) != machine:
            return (f"the {what} {build.build_id[:16]} is for {target.get('os')}/{target.get('arch')}, this machine "
                    f"is {machine[0]}/{machine[1]}")
        if build.purpose != "host-runtime" or "llama-server" not in (build.manifest.get("files") or {}):
            return f"the {what} {build.build_id[:16]} has no llama-server"
        cpu = self._cpu_problem(build.plan)
        if cpu:
            return f"the {what} {build.build_id[:16]} cannot run on this CPU: {cpu}"
        from orchestrator.runtime.build.verify import VerifyError
        try:
            build.binary("llama-server")
        except VerifyError as e:
            return f"the {what} {build.build_id[:16]}: {e}"
        return None

    def _cpu_problem(self, plan: Any) -> Optional[str]:
        """Why a -march=native build of another CPU cannot run here (build/host.py cpu_problem), or None. A probe that
        fails decides nothing: the build passed its verification, and this check only adds what that cannot see."""
        from orchestrator.runtime.build.host import cpu_problem
        try:
            profile = self.profile()
        except Exception as e:  # the probe could not run; the start itself still fails loudly on a wrong CPU
            self.logger.warning(f"Could not probe the CPU for the build check ({e.__class__.__name__})")
            return None
        return cpu_problem(plan, profile)

    def _from_store(self, build, role: str, notes: List[str]) -> InstalledRuntime:
        manifest = build.manifest
        verification = manifest.get("verification") or {}
        devices: List[str] = []
        for check in verification.get("checks") or []:
            if isinstance(check, dict) and check.get("name") == "devices":
                devices = [str(d.get("name")) for d in (check.get("data") or {}).get("devices") or []
                           if isinstance(d, dict)][:16]
        host = (manifest.get("imported") or {}).get("built_on") or manifest.get("build_host") or {}
        built_on = "/".join(_platform_of(host).values()) if isinstance(host, dict) and host else None
        duration = manifest.get("duration_s")
        placement = manifest.get("placement")
        return InstalledRuntime(
            build=self.build, variant=build.backend, backend=build.backend, path=build.path,
            server=build.binary("llama-server"), files=dict(manifest.get("files") or {}), origin=build.origin,
            build_id=build.build_id, commit=build.commit, role=role,
            placement=placement if placement in ("native", "container") else None,
            built_on=built_on, built_at=_timestamp_of(build.built_at), verified_at=manifest.get("verified_at"),
            duration_s=float(duration) if isinstance(duration, (int, float)) else None, devices=devices, notes=notes)

    def resolve_installed(self) -> Tuple[Optional[InstalledRuntime], List[str]]:
        """(the build start() uses, problems). A self-built or imported build of the store is used when it passed its
        verification on this machine and still matches its manifest: the active build, else the CPU fallback. The
        prebuilt archive is used only after `install_prebuilt` (the opt-in of decision D1): an archive an earlier
        version downloaded without that request is reported, not used, and a self-built build that cannot be used
        never falls back to it."""
        data = _read_json(self.active_file) or {}
        use = data.get("use")
        problems: List[str] = []
        if use != "prebuilt":
            store = self.store
            refs = store.references()
            for role in ("active", "fallback"):
                build_id = refs.get(role)
                if not build_id or (role == "fallback" and build_id == refs.get("active")):
                    continue
                build = store.get(build_id)
                problem = self._store_problem(build, build_id, role)
                if problem:
                    problems.append(problem)
                    continue
                notes = [*problems, "the CPU fallback build is used"] if role == "fallback" else []
                return self._from_store(build, role, notes), problems
            if refs or use == "self-built":
                return None, problems or ["no self-built build is active"]
        variant = data.get("variant")
        if not isinstance(variant, str) or not re.fullmatch(r"[a-z0-9.-]+", variant):
            return None, problems
        inst = self._read_manifest(self.root / f"{self.build}-{variant}")
        if inst is not None and use != "prebuilt":
            # Downloaded by an earlier version's `runtime install`, never asked for: decision D1 allows the prebuilt
            # archive only as the explicit opt-in, so it is not chosen here.
            backend = f" --backend {inst.backend}" if inst.backend != "cpu" else ""
            problems.append(f"the official archive {inst.asset_id} ({PREBUILT_LABEL}) that an earlier version "
                            "installed is used only on explicit request: llm-cli ditto runtime install --prebuilt"
                            f"{backend} uses it (an installed archive is not downloaded again), llm-cli ditto runtime "
                            "install builds llama-server from the pinned source")
            return None, problems
        return inst, problems

    def prebuilt_installed(self, asset: RuntimeAsset) -> bool:
        """Whether exactly this official archive is unpacked already (install_prebuilt then only switches to it)."""
        current = self._read_manifest(self.install_dir(asset))
        return current is not None and current.files == {f.name: f.sha256 for f in asset.files}

    # --- self-built runtime (orchestrator/runtime/build/host.py) --------------------------------------------------
    def plan_install(self, backend: str = "auto", placement: str = "auto"):
        """What `install` will build on this machine (HostPlan): the CPU build, then the GPU build of `backend`.
        Raises LocalRuntimeError when not even the CPU build can be planned (the message names the ways out)."""
        from orchestrator.runtime.build.host import HostBuildError, plan_host_runtime
        try:
            return plan_host_runtime(self.profile(), self.toolchain(full=True), self.catalog, self.store,
                                     backend=backend, placement=placement, engine=self.engine())
        except HostBuildError as e:
            raise LocalRuntimeError(str(e)) from None

    def install(self, plan: Any = None, *, backend: str = "auto", placement: str = "auto",
                jobs: Optional[int] = None, log: Optional[Callable[[str], None]] = None,
                fetch_source: Optional[Callable[[], bool]] = None,
                cancelled: Optional[Callable[[], bool]] = None):
        """Builds llama-server on this machine from the pinned commit (the plan of plan_install): the CPU build first,
        verified and made active, then the GPU build, active once it is verified. A running server keeps running; the
        next start uses the new build. fetch_source() is asked before the pinned source (about 215 MB) is fetched;
        without it the source is fetched. `cancelled()` is checked before every build and before every activation: a
        cancelled install activates nothing more (the step running at that moment finishes, its result is kept in the
        store but not made active). Returns the HostInstallResult; raises LocalRuntimeError when the CPU build cannot be
        built or the install was cancelled before it was activated (nothing is activated then)."""
        from orchestrator.runtime.build import source as llama_source
        from orchestrator.runtime.build.executor import BuildExecutor
        from orchestrator.runtime.build.host import HostBuildError, install_host_runtime
        from orchestrator.runtime.build.store import smoke_model_file
        from orchestrator.runtime.build.verify import Verifier
        plan = plan or self.plan_install(backend=backend, placement=placement)
        pin = self.catalog.llama_cpp.source
        source = None
        if plan.needs_source:
            try:
                source = llama_source.cached_source(pin, self.data_dir)
                if source is None:
                    if fetch_source is not None and not fetch_source():
                        raise LocalRuntimeError("The pinned llama.cpp source is needed for the build and was not "
                                                "fetched. On a machine without network: llm-cli ditto runtime "
                                                "import-source FILE (made with export-source elsewhere).")
                    source = llama_source.fetch_source(pin, self.data_dir, log=log)
            except llama_source.SourceError as e:
                raise LocalRuntimeError(f"The pinned llama.cpp source: {e}") from None
        executor = self._executor or BuildExecutor(engine=self.engine(), log=log)
        verifier = self._verifier or Verifier()
        smoke = smoke_model_file(self.catalog.smoke_model, self.models_dir.parent / "smoke")
        try:
            result = install_host_runtime(plan, store=self.store, source=source, executor=executor,
                                          verifier=verifier, smoke_model=smoke, jobs=jobs, log=log,
                                          cancelled=cancelled)
        except HostBuildError as e:
            where = f" (log: {e.log})" if e.log else ""
            raise LocalRuntimeError(f"{e}{where}") from None
        with self._lock():
            self._update_active(use="self-built")
            self._prune_old_builds()
        self.logger.info(f"Self-built llama.cpp {self.build}: active build {result.active.build_id[:16]} "
                         f"({result.active.backend}), CPU fallback {result.fallback.build_id[:16]}")
        return result

    def import_build(self, archive: Path, confirm: Callable[[Dict[str, Any]], bool]) -> InstalledRuntime:
        """Imports a llama-server build made on another machine of the same kind (ditto runtime export there): the
        archive is checked against its manifest and the pin, confirm() is asked before any of its programs runs, and
        the build is verified here like a local one before it becomes the active build."""
        from orchestrator.runtime.build.store import StoreError
        from orchestrator.runtime.build.verify import Verifier
        pin = self.catalog.llama_cpp.source
        if pin is None:
            raise LocalRuntimeError("configs/llama_runtime.yml has no llama_cpp.source (the pinned commit)")
        store = self.store

        def ask(info: Dict[str, Any]) -> bool:
            if "llama-server" not in (info.get("files") or []):
                raise StoreError("the build has no llama-server (a build for Ditto's server is needed)")
            cpu = self._cpu_problem(info.get("plan") or {})
            if cpu:  # before the user is asked and before any of its programs runs
                raise StoreError(f"the build cannot run on this CPU: {cpu}")
            # What the archive says about where and when it was built is shown to the user: only plain values.
            shown = {k: v for k, v in info.items() if k != "plan"}
            shown["built_on"] = _platform_of(info.get("built_on"))
            shown["built_at"] = _timestamp_of(info.get("built_at"))
            return bool(confirm(shown))

        try:
            build = store.import_build(Path(archive), pin=pin, verifier=self._verifier or Verifier(), confirm=ask,
                                       host=self.platform())
            problem = self._store_problem(build, build.build_id, "active")
            if problem:
                raise LocalRuntimeError(f"The imported build cannot be used: {problem}")
            store.activate(build.build_id)
            if build.backend == "cpu":
                store.activate(build.build_id, role="fallback")
        except StoreError as e:
            raise LocalRuntimeError(str(e)) from None
        with self._lock():
            self._update_active(use="self-built")
        inst = self.installed()
        if inst is None:  # pragma: no cover - just activated
            raise LocalRuntimeError("The imported build could not be activated.")
        return inst

    def export_build(self, out: Path) -> Path:
        """Writes the build in use to a tar.gz for another machine of the same kind (ditto runtime import there)."""
        from orchestrator.runtime.build.store import StoreError
        inst = self.installed()
        if inst is None or inst.build_id is None:
            raise LocalRuntimeError("There is no self-built build to export: llm-cli ditto runtime install builds one "
                                    "(the prebuilt archive is never exported).")
        try:
            return self.store.export_build(inst.build_id, Path(out))
        except StoreError as e:
            raise LocalRuntimeError(str(e)) from None

    def prune(self, keep_last: int = 2) -> List[str]:
        """Removes old self-built builds of Ditto's server: never the active build, the CPU fallback or the build a
        running server uses, and never a build of the device runtime (llm-cli device runtime build), which shares the
        build store."""
        from orchestrator.runtime.build.host import PURPOSE
        from orchestrator.runtime.build.store import StoreError
        try:
            return self.store.prune(keep_last=keep_last, purpose=PURPOSE)
        except StoreError as e:
            raise LocalRuntimeError(str(e)) from None

    def rebuild_advice(self) -> Optional[str]:
        """Whether the build in use should be built again (pin changed: required; this machine's plan changed:
        recommended), None when it is current or no self-built build is in use."""
        from orchestrator.runtime.build.host import rebuild_advice
        inst = self.installed()
        if inst is None or inst.build_id is None:
            return None
        build = self.store.get(inst.build_id)
        if build is None:
            return None
        try:
            plan = self.plan_install(backend=inst.backend if inst.backend != "cpu" else "cpu",
                                     placement=inst.placement or "auto")
        except LocalRuntimeError:
            plan = None
        return rebuild_advice(build, plan, self.catalog.llama_cpp.source)

    # --- the official prebuilt archive (explicit opt-in, decision D1) ------------------------------------------------
    def install_prebuilt(self, prefer: Optional[str] = None, force: bool = False,
                         progress: Optional[ProgressFn] = None) -> InstalledRuntime:
        """Downloads, verifies and unpacks the official prebuilt llama.cpp archive for this machine (or the requested
        backend): only on explicit request (`ditto runtime install --prebuilt`), and always shown as "prebuilt, not
        self-built". The previous installation of the same variant stays in place until the new one is complete."""
        choice = self.select_backend(prefer)
        asset = choice.asset
        if asset is None:
            raise LocalRuntimeError(choice.reason)
        unpinned = [f.name for f in asset.files if not f.pinned]
        if unpinned:
            raise IntegrityError(f"No sha256 and size pinned for {', '.join(unpinned)} in the runtime catalog; "
                                 "refusing to download unverifiable binaries.")
        with self._lock():
            target = self.install_dir(asset)
            current = self._read_manifest(target)
            if current and not force and current.files == {f.name: f.sha256 for f in asset.files}:
                self._update_active(variant=asset.variant, asset_id=asset.id, use="prebuilt")
                return current
            running = owned_process(_read_json(self.state_file))
            if running and target.exists() and self._uses(running, target):
                raise LocalRuntimeError("The local model server runs from this installation; stop it first "
                                        "(llm-cli ditto runtime stop).")
            for leftover in list(self.root.glob(".staging-*")) + list(self.root.glob(".old-*")):
                shutil.rmtree(leftover, ignore_errors=True)  # from an interrupted install; the lock is ours

            downloads = self.root / "downloads"
            downloads.mkdir(parents=True, exist_ok=True)
            self._ensure_space(self.root, 2 * asset.download_size, what="unpacking")
            archives = [self._fetch(self.catalog.llama_cpp.base_url + f.name, downloads / f.name, f.size, f.sha256,
                                    progress) for f in asset.files]
            staging = self.root / f".staging-{asset.variant}-{secrets.token_hex(4)}"
            old = None
            try:
                server = self._unpack(asset, archives, staging)
                manifest = {"build": self.build, "asset_id": asset.id, "variant": asset.variant,
                            "backend": asset.backend, "server": server.relative_to(staging).as_posix(),
                            "files": {f.name: f.sha256 for f in asset.files},
                            "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                (staging / "runtime.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
                if target.exists():
                    old = self.root / f".old-{target.name}-{secrets.token_hex(4)}"
                    os.replace(target, old)
                try:
                    os.replace(staging, target)
                except OSError:
                    if old is not None:
                        os.replace(old, target)  # put the previous installation back
                        old = None
                    raise
            finally:
                shutil.rmtree(staging, ignore_errors=True)
                if old is not None:
                    shutil.rmtree(old, ignore_errors=True)
            for archive in archives:
                archive.unlink(missing_ok=True)
            self._update_active(variant=asset.variant, asset_id=asset.id, use="prebuilt")
            self._prune_old_builds()
        inst = self._read_manifest(target)
        if inst is None:
            raise LocalRuntimeError(f"Installation in {target} is incomplete.")
        self.logger.info(f"Installed the official llama.cpp archive {self.build} ({asset.id}, {PREBUILT_LABEL}) in "
                         f"{target}")
        return inst

    @staticmethod
    def _uses(proc: psutil.Process, folder: Path) -> bool:
        try:
            return any(Path(p).resolve().is_relative_to(folder.resolve()) for p in _process_paths(proc))
        except (psutil.Error, OSError):
            return False

    def _unpack(self, asset: RuntimeAsset, archives: List[Path], staging: Path) -> Path:
        """Unpacks every archive of the asset; files of `libs` archives (CUDA runtime) end up next to the server."""
        lib_dirs = []
        for index, (entry, archive) in enumerate(zip(asset.files, archives)):
            dest = staging / f".libs-{index}" if entry.libs else staging
            if entry.libs:
                lib_dirs.append(dest)
            try:
                safe_extract_archive(archive, dest)
            except Exception as e:
                raise IntegrityError(f"Unsafe or broken archive {archive.name}: {e}") from e
        server = self._find_server(staging, asset)
        for lib_dir in lib_dirs:
            for path in sorted(lib_dir.rglob("*")):
                if not path.is_file() or path.is_symlink():
                    continue
                target = server.parent / path.name
                if target.exists():
                    raise IntegrityError(f"{path.name} from a runtime library archive would replace a file of the "
                                         "llama.cpp build.")
                os.replace(path, target)
            shutil.rmtree(lib_dir, ignore_errors=True)
        self._make_executable(server.parent)
        return server

    @staticmethod
    def _find_server(staging: Path, asset: RuntimeAsset) -> Path:
        name = "llama-server.exe" if asset.os == "windows" else "llama-server"
        found = sorted((p for p in staging.rglob(name) if p.is_file() and not p.is_symlink()
                        and not any(part.startswith(".libs-") for part in p.relative_to(staging).parts)),
                       key=lambda p: len(p.parts))
        if not found:
            raise LocalRuntimeError(f"{name} not found in the downloaded archive.")
        return found[0]

    @staticmethod
    def _make_executable(bin_dir: Path) -> None:
        """Zip archives carry no permission bits; executables and shared libraries need +x on POSIX."""
        if os.name != "posix":
            return
        for p in bin_dir.iterdir():
            if not p.is_file() or p.is_symlink() or p.name.upper().startswith("LICENSE"):
                continue
            if not p.suffix or ".so" in p.name or p.suffix == ".dylib":
                p.chmod(p.stat().st_mode | 0o755)

    def _prune_old_builds(self) -> None:
        """Removes installations of other llama.cpp builds (after a catalog update), unless the server runs there."""
        running = owned_process(_read_json(self.state_file))
        for folder in self.root.glob("b*-*"):
            if not folder.is_dir() or folder.name.startswith(f"{self.build}-"):
                continue
            if running and self._uses(running, folder):
                continue
            shutil.rmtree(folder, ignore_errors=True)

    # --- downloads (orchestrator/utils/download.py) ---------------------------------------------------------
    def _downloads(self) -> _Downloads:
        return _Downloads(self._session, self._sleep, self.logger)

    def _ensure_space(self, folder: Path, needed: int, what: str = "the download") -> None:
        self._downloads().ensure_space(folder, needed, what)

    def _fetch(self, url: str, dest: Path, size: Optional[int], sha256: Optional[str],
               progress: Optional[ProgressFn] = None, attempts: int = 5, stop: Any = None) -> Path:
        """Resumable download into <dest>.part; the file only gets its final name after size and hash matched. A set
        `stop` event ends it with DownloadStopped, keeping the part."""
        downloads = self._downloads()
        try:
            return downloads.fetch(url, dest, size, sha256, progress, attempts, stop=stop)
        finally:
            self._session = downloads._session  # keep the connection pool for the next file

    def _verify(self, path: Path, size: int, sha256: str, name: str) -> None:
        self._downloads().verify(path, size, sha256, name)

    # --- models -----------------------------------------------------------------------------------------------
    def model_path(self, model: ModelEntry) -> Path:
        return self.models_dir / model.file

    def _marker(self, model: ModelEntry) -> Path:
        return self.models_dir / f".{model.file}.verified.json"

    def model_ready(self, model: ModelEntry) -> bool:
        """Downloaded and verified (the marker records size and mtime, so a changed file is checked again)."""
        marker = _read_json(self._marker(model)) or {}
        try:
            st = self.model_path(model).stat()
        except OSError:
            return False
        return (bool(model.sha256) and marker.get("sha256") == model.sha256 and marker.get("size") == st.st_size
                and marker.get("mtime_ns") == st.st_mtime_ns)

    def model_state(self, model: ModelEntry) -> str:
        """ready | unverified (a file without a matching marker; checked before use) | missing"""
        if self.model_ready(model):
            return "ready"
        return "unverified" if self.model_path(model).is_file() else "missing"

    def _write_marker(self, model: ModelEntry) -> None:
        st = self.model_path(model).stat()
        _write_private(self._marker(model), json.dumps({"sha256": model.sha256, "size": st.st_size,
                                                        "mtime_ns": st.st_mtime_ns}))

    def download_model(self, tier: str, progress: Optional[ProgressFn] = None, stop: Any = None) -> Path:
        """Downloads a model at its pinned commit; it gets its final name only after size and sha256 matched. A set
        `stop` event (threading.Event) ends it with DownloadStopped and keeps the `.part` file."""
        try:
            model = self.catalog.model(tier)
        except KeyError as e:
            raise LocalRuntimeError(e.args[0]) from None
        if not model.pinned:
            raise IntegrityError(f"No sha256 and size pinned for {model.repo}/{model.file}; refusing to download it.")
        path = self.model_path(model)
        if self.model_ready(model):
            return path
        self._fetch(model.url(self.catalog.model_base_url), path, model.size, model.sha256, progress, stop=stop)
        self._write_marker(model)
        return path

    def _ensure_model_verified(self, model: ModelEntry) -> Path:
        path = self.model_path(model)
        if not path.is_file():
            raise LocalRuntimeError(f"Model {model.tier} is not downloaded. Run: llm-cli ditto models download "
                                    f"{model.tier}")
        if not self.model_ready(model):
            if not model.pinned:
                raise IntegrityError(f"No sha256 and size pinned for {model.file}; refusing to load it.")
            self._verify(path, model.size, model.sha256, model.file)
            self._write_marker(model)
        return path

    # --- process ----------------------------------------------------------------------------------------------
    def _load_state(self) -> Optional[dict]:
        return _read_json(self.state_file)

    def _health(self, port: int) -> Optional[int]:
        try:
            return self._local.get(f"http://{LOOPBACK}:{port}/health", timeout=2).status_code
        except requests.RequestException:
            return None

    def _check_auth(self, port: int, key: str) -> None:
        """Fail closed if the server answers API calls without (or ignores) the key."""
        url = f"http://{LOOPBACK}:{port}/v1/models"
        anonymous = self._local.get(url, timeout=5).status_code
        authorized = self._local.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=5).status_code
        if anonymous != 401 or authorized != 200:
            raise LocalRuntimeError(f"llama-server did not enforce its API key (HTTP {anonymous} without, "
                                    f"{authorized} with the key); stopped it.")

    def _log_tail(self, lines: int = 25) -> str:
        """The end of the current run's log (untrusted text, secrets in known formats redacted)."""
        try:
            with open(self.log_file, "rb") as f:
                f.seek(max(0, f.seek(0, os.SEEK_END) - 16384))
                text = f.read().decode("utf-8", errors="replace")
        except OSError:
            return ""
        start = text.rfind(_LOG_HEADER)
        if start >= 0:
            text = text[text.find("\n", start) + 1:]
        return redact_secrets("\n".join(text.splitlines()[-lines:]))

    def _open_log(self):
        """Appends to llama-server.log; a log above log_max_mib is rotated first (.1 newest ... .N oldest)."""
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        limit, keep = self.catalog.server.log_max_mib * MIB, self.catalog.server.log_backups
        try:
            if self.log_file.stat().st_size > limit:
                if keep == 0:
                    self.log_file.unlink()
                else:
                    for i in range(keep - 1, 0, -1):
                        older = self.log_file.with_name(f"{self.log_file.name}.{i}")
                        if older.exists():
                            os.replace(older, self.log_file.with_name(f"{self.log_file.name}.{i + 1}"))
                    os.replace(self.log_file, self.log_file.with_name(self.log_file.name + ".1"))
        except OSError:
            pass  # Windows: still open elsewhere; rotated next time
        log = os.fdopen(os.open(self.log_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "ab")
        log.write(f"\n{_LOG_HEADER} {datetime.now().isoformat(timespec='seconds')} ===\n".encode())
        log.flush()
        return log

    def endpoint(self) -> Optional[LocalEndpoint]:
        """base_url, api key and model of the running server, or None."""
        return read_endpoint(self.root)

    def status(self, check_rebuild: bool = False) -> RuntimeStatus:
        """The build in use (origin, ID, commit, backend, verification), why a stored build cannot be used, the
        running server, and with check_rebuild whether the build should be built again (plans the build, so it
        detects the toolchain)."""
        state = self._load_state()
        proc = owned_process(state)
        installed, problems = self.resolve_installed()
        status = RuntimeStatus(build=self.build, installed=installed, problems=problems, log_file=self.log_file)
        if check_rebuild and installed is not None and installed.build_id:
            status.rebuild = self.rebuild_advice()
        if proc and state:
            status.running, status.pid, status.port = True, state.get("pid"), state.get("port")
            status.model, status.tier, status.ctx = state.get("alias"), state.get("tier"), state.get("ctx")
            status.started_at = state.get("started_at")
            status.base_url = f"http://{LOOPBACK}:{state.get('port')}/v1"
            status.healthy = self._health(int(state.get("port") or 0)) == 200
        return status

    def start(self, tier: Optional[str] = None, ctx: Optional[int] = None,
              sleep_idle_seconds: Optional[int] = None, timeout: Optional[float] = None,
              tie_to_process: bool = False, remember: bool = True) -> LocalEndpoint:
        """Starts llama-server (or returns the running one if it already serves this model and context).
        tier "auto" forgets a tier chosen earlier; tie_to_process ends the server together with this process;
        remember=False keeps the tier chosen earlier (e.g. for an evaluation run). Only a build that passed its
        verification on this machine and still matches its manifest is started (or the opted-in prebuilt archive)."""
        installed, problems = self.resolve_installed()
        if installed is None:
            if problems:
                raise LocalRuntimeError("The local model server is not started: " + "; ".join(problems) +
                                        ". Build it again: llm-cli ditto runtime install")
            raise LocalRuntimeError("The local runtime is not installed. Run: llm-cli ditto runtime install (builds "
                                    "llama-server from the pinned llama.cpp source; --prebuilt downloads the official "
                                    "archive instead, not self-built)")
        for note in installed.notes:
            self.logger.warning(f"Local runtime: {note}")
        model, _ = self.resolve_model(tier)
        with self._lock():
            state = self._load_state() or {}
            running = self.endpoint()
            if running:
                same = (state.get("alias") == model.alias and state.get("server") == str(installed.server)
                        and (ctx is None or state.get("ctx") == ctx))
                if same and self._health(running.port) == 200:
                    return running
                self._stop_locked()
            self._cleanup_stale_locked()
            model_path = self._ensure_model_verified(model)
            endpoint = self._launch(installed, model, model_path, ctx, sleep_idle_seconds, timeout, tie_to_process)
        if tier and remember:
            self._update_active(tier=None if tier == "auto" else model.tier)
        return endpoint

    def _launch(self, installed: InstalledRuntime, model: ModelEntry, model_path: Path, ctx: Optional[int],
                sleep_idle_seconds: Optional[int], timeout: Optional[float], tie_to_process: bool) -> LocalEndpoint:
        # Free RAM and VRAM are volatile: every start probes them again for the context size.
        hw = self.hardware(refresh=not self._hardware_given)
        if installed.origin == "prebuilt":
            choice = select_backend(hw, self.catalog, installed.backend)
        else:
            choice = backend_choice(hw, installed.backend)
        if ctx is None:
            ctx, why = compute_context(model, hw, choice, self.catalog.server, model_bytes=model_path.stat().st_size)
        else:
            ctx, why = max(256, min(int(ctx), model.native_context)), "set explicitly"
        if sleep_idle_seconds is None:
            sleep_idle_seconds = self.catalog.server.sleep_idle_seconds
        if not self.catalog.llama_cpp.supports("sleep_idle_seconds"):
            sleep_idle_seconds = 0
        cache_ram = max(256, min(8192, hw.ram_total // (8 * MIB)))
        timeout = timeout or self.catalog.server.health_timeout_seconds

        again = ("llm-cli ditto runtime install --prebuilt --force" if installed.origin == "prebuilt"
                 else "llm-cli ditto runtime install")
        for _ in range(3):  # a free port can be taken between choosing and binding it
            key_file, key = write_key_file(self.root)
            port = _free_port()
            cmd = build_server_command(installed.server, model_path, port, key_file, ctx, model.alias,
                                       gpu=installed.backend in GPU_BACKENDS, cache_ram_mib=cache_ram,
                                       sleep_idle_seconds=sleep_idle_seconds or None)
            env = server_env()
            try:
                check_server_command(cmd)  # the shared policy, once more right before the start (decision D22)
            except PolicyError as e:
                key_file.unlink(missing_ok=True)
                raise LocalRuntimeError(str(e)) from None
            self.logger.info(f"Starting llama-server {self.build} ({installed.label}, {installed.name}, "
                             f"{installed.variant}) on {LOOPBACK}:{port}, model {model.file}, ctx {why}")
            try:
                with self._open_log() as log:
                    proc = self._popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                       cwd=str(installed.server.parent), env=env, close_fds=True,
                                       **popen_options())
            except OSError as e:  # not executable, wrong architecture, missing loader
                key_file.unlink(missing_ok=True)
                raise LocalRuntimeError(f"Could not start {installed.server}: {e.strerror or e}. Build or install "
                                        f"it again: {again}") from e
            self._proc = proc
            try:
                created = psutil.Process(proc.pid).create_time()
            except psutil.Error:
                created = 0.0  # already gone; the health wait reports it
            _write_private(self.state_file, json.dumps({
                "pid": proc.pid, "create_time": created, "port": port, "server": str(installed.server),
                "variant": installed.variant, "origin": installed.origin, "build_id": installed.build_id,
                "tier": model.tier, "alias": model.alias, "ctx": ctx,
                "key_file": str(key_file), "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}))
            if tie_to_process:
                self._tie_to_this_process(proc.pid, created)
            outcome = self._wait_healthy(proc, port, timeout)
            if outcome == "ok":
                try:
                    self._check_auth(port, key)
                except (LocalRuntimeError, requests.RequestException) as e:
                    self._stop_locked()
                    raise LocalRuntimeError(str(e)) from e
                return LocalEndpoint(base_url=f"http://{LOOPBACK}:{port}/v1", api_key=key, model=model.alias,
                                     port=port, pid=proc.pid)
            tail = self._log_tail()
            self._stop_locked()
            if outcome == "exited" and "couldn't bind" in tail:
                continue
            what = "did not become ready in time" if outcome == "timeout" else "exited during startup"
            raise LocalRuntimeError(f"llama-server {what} (log: {self.log_file}).", log_tail=tail)
        raise LocalRuntimeError("Could not find a free local port for llama-server.")

    def _tie_to_this_process(self, pid: int, created: float) -> None:
        """Ends the server together with this process: a kill-on-close job on Windows, an exit handler elsewhere
        (which a hard kill of this process skips; the next start reuses or cleans up the server)."""
        if os.name == "nt":
            try:
                self._job = _job_kill_on_close(pid)
            except (OSError, AttributeError, ValueError) as e:
                self.logger.warning(f"Could not tie llama-server to this process ({e.__class__.__name__})")
            return

        def stop_if_unchanged():
            state = self._load_state() or {}
            if state.get("pid") == pid and state.get("create_time") == created:
                try:
                    self.stop()
                except LocalRuntimeError:
                    pass
        atexit.register(stop_if_unchanged)

    def _wait_healthy(self, proc, port: int, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return "exited"
            if self._health(port) == 200:
                return "ok"
            self._sleep(0.25)
        return "timeout"

    def stop(self, timeout: float = 15) -> int:
        """Stops the server this runtime started and leftover servers from its folder (after a crash). Returns
        how many processes were stopped. A recorded pid that now belongs to another process is left alone."""
        with self._lock():
            stopped = 1 if self._stop_locked(timeout) else 0
            return stopped + self._cleanup_stale_locked()

    def _stop_locked(self, timeout: float = 15) -> bool:
        proc = owned_process(self._load_state())
        if proc:
            self._terminate(proc, timeout)
        if self._proc is not None:
            try:
                self._proc.wait(1)  # reap our own child so it does not linger as a zombie
            except subprocess.TimeoutExpired:
                pass
            self._proc = None
        self.state_file.unlink(missing_ok=True)
        for key_file in self.root.glob("server-*.key"):
            key_file.unlink(missing_ok=True)
        return proc is not None

    @staticmethod
    def _terminate(proc: psutil.Process, timeout: float) -> None:
        """SIGTERM to the server's process group (POSIX) or terminate (Windows), SIGKILL after the timeout."""
        try:
            children = proc.children(recursive=True)
        except psutil.Error:
            children = []
        group = False
        if os.name == "posix":
            try:
                if os.getpgid(proc.pid) == proc.pid:  # started with its own session, so its group is its own
                    os.killpg(proc.pid, signal.SIGTERM)
                    group = True
            except OSError:
                pass
        if not group:
            for p in [proc, *children]:
                try:
                    p.terminate()
                except psutil.Error:
                    pass
        _, alive = psutil.wait_procs([proc, *children], timeout=timeout)
        if alive:
            if group:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    pass
            for p in alive:
                try:
                    p.kill()
                except psutil.Error:
                    pass
            psutil.wait_procs(alive, timeout=5)

    def find_orphans(self, keep_pid: Optional[int] = None) -> List[psutil.Process]:
        """llama-server processes running from this runtime's folder that no state file records (a crash between
        start and bookkeeping, a deleted state file). Only executables inside the folder count."""
        root = self.root.resolve()
        found = []
        for proc in self._process_iter():
            if proc.pid == keep_pid:
                continue
            try:
                exe = proc.exe()
            except (psutil.Error, OSError):
                continue
            if not exe or Path(exe).name.lower() not in SERVER_NAMES:
                continue
            try:
                if Path(exe).resolve().is_relative_to(root):
                    found.append(proc)
            except OSError:
                continue
        return found

    def cleanup_stale(self) -> int:
        with self._lock():
            return self._cleanup_stale_locked()

    def _cleanup_stale_locked(self) -> int:
        """Removes bookkeeping of a server that is gone and stops orphaned servers; returns how many it stopped."""
        state = self._load_state()
        proc = owned_process(state)
        if state and proc is None:
            self.state_file.unlink(missing_ok=True)
        live_key = Path(state["key_file"]).name if proc and state and state.get("key_file") else None
        for key_file in self.root.glob("server-*.key"):
            if key_file.name != live_key:
                key_file.unlink(missing_ok=True)
        orphans = self.find_orphans(keep_pid=proc.pid if proc else None)
        for orphan in orphans:
            self.logger.warning(f"Stopping a leftover llama-server (pid {orphan.pid})")
            self._terminate(orphan, 10)
        return len(orphans)

    def restart(self, **kwargs) -> LocalEndpoint:
        self.stop()
        return self.start(**kwargs)


def managed_endpoint(data_dir: Optional[Path] = None) -> Optional[LocalEndpoint]:
    """The managed server of this user (LLM_FRAMEWORK_HOME or ~/.llm-framework, or `data_dir`)."""
    home = os.environ.get("LLM_FRAMEWORK_HOME") or Path.home() / ".llm-framework"
    return read_endpoint(runtime_root(Path(data_dir) if data_dir else Path(home)))
