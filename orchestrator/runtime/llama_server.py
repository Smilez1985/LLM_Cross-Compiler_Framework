#!/usr/bin/env python3
"""
Managed llama-server (llama.cpp) for Ditto's local/offline mode.

Downloads a pinned llama.cpp build and pinned GGUF models (size and sha256 are checked before anything is
unpacked or loaded; entries without both are refused), starts llama-server on 127.0.0.1 with a free port and
a random per-start API key that reaches the server only through a 0600 key file, and hands base_url, key and
model to the provider layer (read_endpoint; LLMClient uses it for the "local" provider). The server never gets
its own tools, agent mode, MCP servers or web UI: Ditto's tools run inside the framework, where approval is
enforced.

Files under <user state dir>/runtime/llama.cpp/:
  b<build>-<variant>/   unpacked build with its manifest (runtime.json)
  active.json           installed variant and the model tier chosen with `start --model`
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
from orchestrator.runtime.hardware import (BackendChoice, HardwareInfo, Runner, choose_model_tier, compute_context,
                                           detect_hardware, select_backend)
from orchestrator.utils.download import DownloadError, FileLock, PinnedDownloader
from orchestrator.utils.download import IntegrityError as DownloadIntegrityError
from orchestrator.utils.helpers import redact_secrets, safe_extract_archive
from orchestrator.utils.logging import get_logger

APP_ROOT = Path(__file__).resolve().parents[2]
LOOPBACK = "127.0.0.1"
MIB = 1024 ** 2
SERVER_NAMES = ("llama-server", "llama-server.exe")

# llama-server options that give the model its own tools, file/shell access, MCP clients, a web UI, remote
# compute (RPC), state endpoints or a command-line key. None of them may ever reach the command line.
FORBIDDEN_FLAGS = frozenset({
    "--tools", "--tools-runtime", "-ag", "--agent", "--mcp-servers-config", "--mcp-servers-json",
    "--ui-mcp-proxy", "--webui-mcp-proxy", "--ui", "--webui", "--api-key", "--props", "--media-path",
    "--slot-save-path", "--models-dir", "--models-preset", "--path", "-hf", "--hf-repo", "-mu", "--model-url",
    "--rpc", "--slots", "--metrics",
})
# Environment variables are a second way to set every llama-server option (LLAMA_ARG_*, LLAMA_API_KEY);
# secrets of other programs have no business in the server's environment either.
_SECRET_ENV = re.compile(r"KEY|TOKEN|SECRET|PASSW|CREDENTIAL|AUTH", re.IGNORECASE)
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


class InstalledRuntime(BaseModel):
    build: str
    asset_id: str
    variant: str
    backend: str
    path: Path
    server: Path
    files: Dict[str, str]


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


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((LOOPBACK, 0))
        return s.getsockname()[1]


def server_env(base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    env = dict(os.environ if base is None else base)
    return {k: v for k, v in env.items() if not k.upper().startswith("LLAMA_") and not _SECRET_ENV.search(k)}


def build_server_command(server: Path, model: Path, port: int, key_file: Path, ctx: int, alias: str,
                         gpu: bool, cache_ram_mib: int, sleep_idle_seconds: Optional[int] = None) -> List[str]:
    """The complete llama-server command line. Only loopback, only the key file, no tools/agent/MCP/web UI."""
    cmd = [str(server), "-m", str(model), "--alias", alias,
           "--host", LOOPBACK, "--port", str(port),
           "--api-key-file", str(key_file),
           "--no-ui",              # web UI off (--no-webui is the deprecated spelling)
           "--no-slots",           # /slots would show the prompts in the slots
           "--jinja",              # chat template engine; OpenAI-style tool calls need it
           "--offline",            # never fetch anything; the model is a local file
           "-c", str(ctx),
           "--cache-ram", str(cache_ram_mib)]
    if gpu:
        cmd += ["-ngl", "auto"]    # offload as many layers as fit (llama.cpp's --fit is on by default)
    if sleep_idle_seconds:
        cmd += ["--sleep-idle-seconds", str(sleep_idle_seconds)]
    bad = FORBIDDEN_FLAGS.intersection(cmd)
    if bad:
        raise LocalRuntimeError(f"Refusing to start llama-server with {sorted(bad)}")
    return cmd


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
                 sleep: Callable[[float], None] = time.sleep):
        self.logger = get_logger("LocalLLMRuntime")
        self.catalog = catalog or load_catalog()
        self.data_dir = Path(data_dir) if data_dir else user_data_dir()
        self.models_dir = Path(models_dir) if models_dir else framework_models_dir() / "ditto"
        self.root = runtime_root(self.data_dir)
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

    def select_backend(self, prefer: Optional[str] = None) -> BackendChoice:
        return select_backend(self.hardware(), self.catalog, prefer)

    def choose_tier(self, prefer: Optional[str] = None) -> Tuple[ModelEntry, str]:
        """Recommended Ditto model for this machine (RAM, and VRAM when a GPU build is used), with the reason."""
        installed = self.installed()
        choice = self.select_backend(installed.backend if installed else prefer)
        return choose_model_tier(self.hardware(), self.catalog, choice)

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
            inst = InstalledRuntime(path=path, server=path / data["server"], **{
                k: data[k] for k in ("build", "asset_id", "variant", "backend", "files")})
        except (KeyError, TypeError, ValueError):
            return None
        return inst if inst.server.is_file() and inst.build == self.build else None

    def installed(self) -> Optional[InstalledRuntime]:
        """The runtime chosen by the last install, if it is still complete and belongs to the pinned build."""
        variant = (_read_json(self.active_file) or {}).get("variant")
        if not isinstance(variant, str) or not re.fullmatch(r"[a-z0-9.-]+", variant):
            return None
        return self._read_manifest(self.root / f"{self.build}-{variant}")

    def install(self, prefer: Optional[str] = None, force: bool = False,
                progress: Optional[ProgressFn] = None) -> InstalledRuntime:
        """Downloads, verifies and unpacks the llama.cpp build for this machine (or the requested backend).
        The previous installation of the same variant stays in place until the new one is complete."""
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
                self._update_active(variant=asset.variant, asset_id=asset.id)
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
            self._update_active(variant=asset.variant, asset_id=asset.id)
            self._prune_old_builds()
        inst = self._read_manifest(target)
        if inst is None:
            raise LocalRuntimeError(f"Installation in {target} is incomplete.")
        self.logger.info(f"Installed llama.cpp {self.build} ({asset.id}) in {target}")
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
               progress: Optional[ProgressFn] = None, attempts: int = 5) -> Path:
        """Resumable download into <dest>.part; the file only gets its final name after size and hash matched."""
        downloads = self._downloads()
        try:
            return downloads.fetch(url, dest, size, sha256, progress, attempts)
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

    def download_model(self, tier: str, progress: Optional[ProgressFn] = None) -> Path:
        """Downloads a model at its pinned commit; it gets its final name only after size and sha256 matched."""
        try:
            model = self.catalog.model(tier)
        except KeyError as e:
            raise LocalRuntimeError(e.args[0]) from None
        if not model.pinned:
            raise IntegrityError(f"No sha256 and size pinned for {model.repo}/{model.file}; refusing to download it.")
        path = self.model_path(model)
        if self.model_ready(model):
            return path
        self._fetch(model.url(self.catalog.model_base_url), path, model.size, model.sha256, progress)
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

    def status(self) -> RuntimeStatus:
        state = self._load_state()
        proc = owned_process(state)
        status = RuntimeStatus(build=self.build, installed=self.installed(), log_file=self.log_file)
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
        remember=False keeps the tier chosen earlier (e.g. for an evaluation run)."""
        installed = self.installed()
        if installed is None:
            raise LocalRuntimeError("The local runtime is not installed. Run: llm-cli ditto runtime install")
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
        choice = select_backend(hw, self.catalog, installed.backend)
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

        for _ in range(3):  # a free port can be taken between choosing and binding it
            key = secrets.token_urlsafe(32)
            key_file = self.root / f"server-{secrets.token_hex(8)}.key"
            _write_private(key_file, key + "\n")
            port = _free_port()
            cmd = build_server_command(installed.server, model_path, port, key_file, ctx, model.alias,
                                       gpu=installed.backend in GPU_BACKENDS, cache_ram_mib=cache_ram,
                                       sleep_idle_seconds=sleep_idle_seconds or None)
            self.logger.info(f"Starting llama-server {self.build} ({installed.variant}) on {LOOPBACK}:{port}, "
                             f"model {model.file}, ctx {why}")
            try:
                with self._open_log() as log:
                    proc = self._popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                       cwd=str(installed.server.parent), env=server_env(), close_fds=True,
                                       **popen_options())
            except OSError as e:  # not executable, wrong architecture, missing loader
                key_file.unlink(missing_ok=True)
                raise LocalRuntimeError(f"Could not start {installed.server}: {e.strerror or e}. Reinstall with: "
                                        "llm-cli ditto runtime install --force") from e
            self._proc = proc
            try:
                created = psutil.Process(proc.pid).create_time()
            except psutil.Error:
                created = 0.0  # already gone; the health wait reports it
            _write_private(self.state_file, json.dumps({
                "pid": proc.pid, "create_time": created, "port": port, "server": str(installed.server),
                "variant": installed.variant, "tier": model.tier, "alias": model.alias, "ctx": ctx,
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
