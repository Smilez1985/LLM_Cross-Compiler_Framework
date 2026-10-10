#!/usr/bin/env python3
"""
Managed llama-server (llama.cpp) for Ditto's local/offline mode.

Downloads a pinned llama.cpp build and pinned GGUF models (hashes verified before anything is unpacked
or loaded), starts llama-server on loopback with a random port and a per-start API key passed via a
0600 key file, and hands base_url/api_key/model to the provider layer. The server never gets its own
tools, agent mode or MCP servers: Ditto's tools run inside the framework, where approval is enforced.
"""

import hmac
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import psutil
import requests
from pydantic import BaseModel, Field

from orchestrator.runtime.catalog import GPU_BACKENDS, ModelEntry, RuntimeAsset, RuntimeCatalog, load_catalog
from orchestrator.runtime.hardware import (BackendChoice, HardwareInfo, Runner, choose_model_tier, compute_context,
                                           detect_hardware, select_backend)
from orchestrator.utils.helpers import calculate_file_checksum, redact_secrets, safe_extract_archive
from orchestrator.utils.logging import get_logger

APP_ROOT = Path(__file__).resolve().parents[2]
LOOPBACK = "127.0.0.1"

# llama-server options that give the model its own tools, file/shell access, MCP clients, a web UI or a
# command-line key. None of them may ever reach the command line.
FORBIDDEN_FLAGS = frozenset({
    "--tools", "--tools-runtime", "-ag", "--agent", "--mcp-servers-config", "--mcp-servers-json",
    "--ui-mcp-proxy", "--webui-mcp-proxy", "--ui", "--webui", "--api-key", "--props", "--media-path",
    "--slot-save-path", "--models-dir", "--models-preset", "--path", "-hf", "--hf-repo", "-mu", "--model-url",
})
# Environment variables are a second way to set every llama-server option (LLAMA_ARG_*, LLAMA_API_KEY);
# secrets of other programs have no business in the server's environment either.
_SECRET_ENV = re.compile(r"KEY|TOKEN|SECRET|PASSW|CREDENTIAL|AUTH", re.IGNORECASE)

ProgressFn = Callable[[str, int, Optional[int]], None]


class LocalRuntimeError(Exception):
    """User-facing runtime problem (message is safe to show)."""


class IntegrityError(LocalRuntimeError):
    """A download is not pinned or does not match its pinned hash; nothing was installed."""


class InstalledRuntime(BaseModel):
    build: str
    asset_id: str
    variant: str
    backend: str
    path: Path
    server: Path
    files: Dict[str, str]


class LocalEndpoint(BaseModel):
    """What the provider layer needs (orchestrator.llm client.set_runtime_endpoint("local", ...))."""
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
    ctx: Optional[int] = None
    base_url: Optional[str] = None
    log_file: Path


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
    """The framework's models directory; relative values are relative to the installation, not the cwd."""
    path = Path(value or os.environ.get("LLM_FRAMEWORK_MODELS_DIR") or "models").expanduser()
    return path if path.is_absolute() else APP_ROOT / path


def _write_private(path: Path, text: str) -> None:
    """Writes a file only the current user can read (created 0600, replaced atomically)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


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
           "--no-webui",
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


class LocalLLMRuntime:
    """Install, start and stop the managed llama-server; choose Ditto's model for this machine."""

    def __init__(self, data_dir: Optional[Path] = None, models_dir: Optional[Path] = None,
                 catalog: Optional[RuntimeCatalog] = None, hardware: Optional[HardwareInfo] = None,
                 session: Optional[requests.Session] = None, runner: Optional[Runner] = None,
                 hf_download: Optional[Callable[..., str]] = None):
        self.logger = get_logger("LocalLLMRuntime")
        self.catalog = catalog or load_catalog()
        self.data_dir = Path(data_dir) if data_dir else user_data_dir()
        self.models_dir = Path(models_dir) if models_dir else framework_models_dir() / "ditto"
        self.root = self.data_dir / "runtime" / "llama.cpp"
        self.state_file = self.root / "server.json"
        self.key_file = self.root / "server.key"
        self.active_file = self.root / "active.json"
        self.log_file = self.data_dir / "logs" / "llama-server.log"
        self._hardware = hardware
        self._runner = runner
        self._session = session
        self._hf_download = hf_download
        self._proc: Optional[subprocess.Popen] = None
        # Loopback calls must never go through an HTTP(S)_PROXY from the environment.
        self._local = requests.Session()
        self._local.trust_env = False

    @classmethod
    def from_framework(cls, framework, **kwargs) -> "LocalLLMRuntime":
        """Uses the framework's per-user directory and models directory."""
        return cls(data_dir=framework.config.user_state_dir,
                   models_dir=framework.resolve_path(framework.config.get("models_dir", "models")) / "ditto",
                   **kwargs)

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
        """Default Ditto model for this machine (RAM, and VRAM when a GPU build is used), with the reason."""
        installed = self.installed()
        choice = self.select_backend(installed.backend if installed else prefer)
        return choose_model_tier(self.hardware(), self.catalog, choice)

    # --- runtime install --------------------------------------------------------------------------------------
    def install_dir(self, asset: RuntimeAsset) -> Path:
        return self.root / f"{self.build}-{asset.variant}"

    def _read_manifest(self, path: Path) -> Optional[InstalledRuntime]:
        try:
            data = json.loads((path / "runtime.json").read_text(encoding="utf-8"))
            inst = InstalledRuntime(path=path, server=path / data["server"], **{
                k: data[k] for k in ("build", "asset_id", "variant", "backend", "files")})
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return inst if inst.server.is_file() and inst.build == self.build else None

    def installed(self) -> Optional[InstalledRuntime]:
        """The runtime chosen by the last install, if it is still complete and belongs to the pinned build."""
        try:
            variant = json.loads(self.active_file.read_text(encoding="utf-8"))["variant"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return self._read_manifest(self.root / f"{self.build}-{variant}")

    def install(self, prefer: Optional[str] = None, force: bool = False,
                progress: Optional[ProgressFn] = None) -> InstalledRuntime:
        """Downloads, verifies and unpacks the llama.cpp build for this machine (or the requested backend)."""
        choice = self.select_backend(prefer)
        asset = choice.asset
        if asset is None:
            raise LocalRuntimeError(choice.reason)
        unpinned = [f.name for f in asset.files if not f.sha256]
        if unpinned:
            raise IntegrityError(f"No sha256 pinned for {', '.join(unpinned)} in the runtime catalog; "
                                 "refusing to download unverifiable binaries.")
        target = self.install_dir(asset)
        current = self._read_manifest(target)
        if current and not force and current.files == {f.name: f.sha256 for f in asset.files}:
            self._set_active(asset)
            return current
        if self.status().running:
            raise LocalRuntimeError("The local model server is running; stop it before reinstalling.")

        downloads = self.root / "downloads"
        archives = [self._fetch(self.catalog.llama_cpp.base_url + f.name, downloads / f.name, f.size, f.sha256,
                                progress) for f in asset.files]
        staging = self.root / f".staging-{asset.variant}-{secrets.token_hex(4)}"
        try:
            for archive in archives:
                try:
                    safe_extract_archive(archive, staging)
                except Exception as e:
                    raise IntegrityError(f"Unsafe or broken archive {archive.name}: {e}") from e
            server = self._find_server(staging, asset)
            self._make_executable(server.parent)
            manifest = {"build": self.build, "asset_id": asset.id, "variant": asset.variant,
                        "backend": asset.backend, "server": server.relative_to(staging).as_posix(),
                        "files": {f.name: f.sha256 for f in asset.files},
                        "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            (staging / "runtime.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            if target.exists():
                shutil.rmtree(target)
            os.replace(staging, target)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        for archive in archives:
            archive.unlink(missing_ok=True)
        self._set_active(asset)
        inst = self._read_manifest(target)
        if inst is None:
            raise LocalRuntimeError(f"Installation in {target} is incomplete.")
        self.logger.info(f"Installed llama.cpp {self.build} ({asset.id}) in {target}")
        return inst

    def _set_active(self, asset: RuntimeAsset) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.active_file.write_text(json.dumps({"variant": asset.variant, "asset_id": asset.id}), encoding="utf-8")

    @staticmethod
    def _find_server(staging: Path, asset: RuntimeAsset) -> Path:
        name = "llama-server.exe" if asset.os == "windows" else "llama-server"
        found = sorted((p for p in staging.rglob(name) if p.is_file() and not p.is_symlink()),
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

    def _http(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
        return self._session

    def _fetch(self, url: str, dest: Path, size: Optional[int], sha256: Optional[str],
               progress: Optional[ProgressFn] = None, attempts: int = 5) -> Path:
        """Resumable download into <dest>.part; the file only gets its final name after the hash matched."""
        if not sha256:
            raise IntegrityError(f"No sha256 pinned for {dest.name}; refusing to download it.")
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        for attempt in range(1, attempts + 1):
            have = part.stat().st_size if part.exists() else 0
            if size and have > size:
                part.unlink()
                have = 0
            if size and have == size:
                break
            try:
                self._download_range(url, part, have, size, progress)
                if not size or part.stat().st_size >= size:
                    break
            except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError) as e:
                self.logger.warning(f"Download of {dest.name} interrupted ({e.__class__.__name__}), "
                                    f"attempt {attempt}/{attempts}")
            if attempt == attempts:
                raise LocalRuntimeError(f"Download of {dest.name} did not complete after {attempts} attempts; "
                                        "run the command again to resume.")
            time.sleep(min(2 ** attempt, 15))
        self._verify(part, size, sha256)
        os.replace(part, dest)
        return dest

    def _download_range(self, url: str, part: Path, have: int, size: Optional[int],
                        progress: Optional[ProgressFn]) -> None:
        headers = {"Range": f"bytes={have}-"} if have else {}
        with self._http().get(url, headers=headers, stream=True, timeout=(15, 60), allow_redirects=True) as r:
            if not str(r.url).startswith("https://"):
                raise LocalRuntimeError(f"Refusing a download that was redirected away from HTTPS: {r.url}")
            if r.status_code not in (200, 206):
                raise LocalRuntimeError(f"Download of {part.name[:-5]} failed: HTTP {r.status_code}")
            resumed = bool(have) and r.status_code == 206
            if resumed and not str(r.headers.get("Content-Range", "")).startswith(f"bytes {have}-"):
                part.unlink(missing_ok=True)  # a range we did not ask for: start over
                raise requests.ConnectionError("unexpected Content-Range")
            if not resumed:
                have = 0
            with open(part, "ab" if resumed else "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    if not chunk:
                        continue
                    f.write(chunk)
                    have += len(chunk)
                    if size and have > size:
                        break  # more than pinned: the hash check below refuses it
                    if progress:
                        progress(part.name[:-5], have, size)

    @staticmethod
    def _verify(path: Path, size: Optional[int], sha256: str) -> None:
        actual_size = path.stat().st_size
        digest = calculate_file_checksum(path, "sha256")
        if (size and actual_size != size) or not hmac.compare_digest(digest, sha256.lower()):
            path.unlink(missing_ok=True)
            name = path.name[:-5] if path.name.endswith(".part") else path.name
            raise IntegrityError(f"{name} does not match its pinned hash (expected sha256 {sha256}, got {digest}, "
                                 f"{actual_size} bytes). The file was deleted and will not be used.")

    # --- models -----------------------------------------------------------------------------------------------
    def model_path(self, model: ModelEntry) -> Path:
        return self.models_dir / model.file

    def _marker(self, model: ModelEntry) -> Path:
        return self.models_dir / f".{model.file}.verified.json"

    def model_ready(self, model: ModelEntry) -> bool:
        """Downloaded and verified (the marker records size and mtime, so a changed file is checked again)."""
        path = self.model_path(model)
        try:
            marker = json.loads(self._marker(model).read_text(encoding="utf-8"))
            st = path.stat()
        except (OSError, ValueError):
            return False
        return (marker.get("sha256") == model.sha256 and marker.get("size") == st.st_size
                and marker.get("mtime_ns") == st.st_mtime_ns)

    def download_model(self, tier: str) -> Path:
        """hf_hub_download at the pinned commit, then the pinned sha256 is checked before the file is used."""
        model = self.catalog.model(tier)
        if not model.sha256:
            raise IntegrityError(f"No sha256 pinned for {model.repo}/{model.file}; refusing to download it.")
        path = self.model_path(model)
        if self.model_ready(model):
            return path
        download = self._hf_download
        if download is None:
            from huggingface_hub import hf_hub_download as download
        self.models_dir.mkdir(parents=True, exist_ok=True)
        got = Path(download(repo_id=model.repo, filename=model.file, revision=model.revision,
                            local_dir=str(self.models_dir), token=False))
        if got.resolve() != path.resolve():
            raise LocalRuntimeError(f"Unexpected download location {got}")
        self._verify(path, model.size, model.sha256)
        st = path.stat()
        self._marker(model).write_text(json.dumps({"sha256": model.sha256, "size": st.st_size,
                                                   "mtime_ns": st.st_mtime_ns}), encoding="utf-8")
        return path

    def _ensure_model_verified(self, model: ModelEntry) -> Path:
        path = self.model_path(model)
        if not path.is_file():
            raise LocalRuntimeError(f"Model {model.tier} is not downloaded. Run: llm-cli ditto models download "
                                    f"{model.tier}")
        if not self.model_ready(model):
            if not model.sha256:
                raise IntegrityError(f"No sha256 pinned for {model.file}; refusing to load it.")
            self._verify(path, model.size, model.sha256)
            st = path.stat()
            self._marker(model).write_text(json.dumps({"sha256": model.sha256, "size": st.st_size,
                                                       "mtime_ns": st.st_mtime_ns}), encoding="utf-8")
        return path

    # --- process ----------------------------------------------------------------------------------------------
    def _load_state(self) -> Optional[dict]:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _owned_process(self, state: Optional[dict]) -> Optional[psutil.Process]:
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
            candidates = (proc.cmdline() or [])[:2]
            try:
                candidates.append(proc.exe())
            except psutil.AccessDenied:
                pass
            if not any(c and Path(c).resolve() == server for c in candidates):
                return None
            return proc
        except (psutil.Error, KeyError, ValueError, TypeError, OSError):
            return None

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
        try:
            with open(self.log_file, "rb") as f:
                f.seek(max(0, f.seek(0, os.SEEK_END) - 16384))
                text = f.read().decode("utf-8", errors="replace")
        except OSError:
            return ""
        return redact_secrets("\n".join(text.splitlines()[-lines:]))

    def _open_log(self):
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            if self.log_file.stat().st_size > 5 * 1024 * 1024:
                os.replace(self.log_file, self.log_file.with_name(self.log_file.name + ".1"))
        except OSError:
            pass
        log = open(self.log_file, "ab")
        log.write(f"\n=== llama-server start {datetime.now().isoformat(timespec='seconds')} ===\n".encode())
        log.flush()
        return log

    def endpoint(self) -> Optional[LocalEndpoint]:
        """base_url, api key and model of the running server, or None."""
        state = self._load_state()
        if not self._owned_process(state):
            return None
        try:
            key = self.key_file.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return LocalEndpoint(base_url=f"http://{LOOPBACK}:{state['port']}/v1", api_key=key, model=state["alias"],
                             port=state["port"], pid=state["pid"])

    def status(self) -> RuntimeStatus:
        state = self._load_state()
        proc = self._owned_process(state)
        status = RuntimeStatus(build=self.build, installed=self.installed(), log_file=self.log_file)
        if proc:
            status.running, status.pid, status.port = True, state["pid"], state["port"]
            status.model, status.ctx = state.get("alias"), state.get("ctx")
            status.base_url = f"http://{LOOPBACK}:{state['port']}/v1"
            status.healthy = self._health(state["port"]) == 200
        return status

    def start(self, tier: Optional[str] = None, ctx: Optional[int] = None,
              sleep_idle_seconds: Optional[int] = None, timeout: Optional[float] = None) -> LocalEndpoint:
        """Starts llama-server (or returns the running one if it already serves this model)."""
        installed = self.installed()
        if installed is None:
            raise LocalRuntimeError("The local runtime is not installed. Run: llm-cli ditto runtime install")
        try:
            model = self.catalog.model(tier) if tier else self.choose_tier()[0]
        except KeyError as e:
            raise LocalRuntimeError(e.args[0]) from None
        alias = Path(model.file).stem
        running = self.endpoint()
        if running and running.model == alias and self._health(running.port) == 200:
            return running
        if running:
            self.stop()
        model_path = self._ensure_model_verified(model)

        hw = self.hardware()
        choice = select_backend(hw, self.catalog, installed.backend)
        if ctx is None:
            ctx, why = compute_context(model, hw, choice, self.catalog.server, model_bytes=model_path.stat().st_size)
        else:
            ctx, why = max(256, min(int(ctx), model.native_context)), "set explicitly"
        if sleep_idle_seconds is None:
            sleep_idle_seconds = self.catalog.server.sleep_idle_seconds
        if not self.catalog.llama_cpp.supports("sleep_idle_seconds"):
            sleep_idle_seconds = 0
        cache_ram = max(256, min(8192, hw.ram_total // (8 * 1024 * 1024)))
        timeout = timeout or self.catalog.server.health_timeout_seconds

        for _ in range(3):  # a random free port can be taken between choosing and binding
            key = secrets.token_urlsafe(32)
            _write_private(self.key_file, key + "\n")
            port = _free_port()
            cmd = build_server_command(installed.server, model_path, port, self.key_file, ctx, alias,
                                       gpu=installed.backend in GPU_BACKENDS, cache_ram_mib=cache_ram,
                                       sleep_idle_seconds=sleep_idle_seconds or None)
            self.logger.info(f"Starting llama-server {self.build} ({installed.variant}) on {LOOPBACK}:{port}, "
                             f"model {model.file}, ctx {why}")
            with self._open_log() as log:
                kwargs = {"start_new_session": True} if os.name == "posix" else {
                    "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
                proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                        cwd=str(installed.server.parent), env=server_env(), close_fds=True, **kwargs)
            self._proc = proc
            try:
                created = psutil.Process(proc.pid).create_time()
            except psutil.Error:
                created = 0.0  # already gone; the health wait reports it
            _write_private(self.state_file, json.dumps({
                "pid": proc.pid, "create_time": created, "port": port,
                "server": str(installed.server), "variant": installed.variant, "tier": model.tier, "alias": alias,
                "ctx": ctx, "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}))
            outcome = self._wait_healthy(proc, port, timeout)
            if outcome == "ok":
                try:
                    self._check_auth(port, key)
                except (LocalRuntimeError, requests.RequestException) as e:
                    self.stop()
                    raise LocalRuntimeError(str(e)) from e
                return self.endpoint() or LocalEndpoint(base_url=f"http://{LOOPBACK}:{port}/v1", api_key=key,
                                                        model=alias, port=port, pid=proc.pid)
            tail = self._log_tail()
            self.stop()
            if outcome == "exited" and "couldn't bind" in tail:
                continue
            what = "did not become ready in time" if outcome == "timeout" else "exited during startup"
            raise LocalRuntimeError(f"llama-server {what}. Log ({self.log_file}):\n{tail}")
        raise LocalRuntimeError("Could not find a free local port for llama-server.")

    def _wait_healthy(self, proc: subprocess.Popen, port: int, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return "exited"
            if self._health(port) == 200:
                return "ok"
            time.sleep(0.25)
        return "timeout"

    def stop(self, timeout: float = 15) -> bool:
        """Stops the server this runtime started. A recorded pid that now belongs to another process is left alone."""
        state = self._load_state()
        proc = self._owned_process(state)
        stopped = False
        if proc:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout)
                except psutil.TimeoutExpired:
                    proc.kill()
                    proc.wait(5)
                stopped = True
            except psutil.NoSuchProcess:
                stopped = True
        if self._proc is not None:
            try:
                self._proc.wait(1)  # reap our own child so it does not linger as a zombie
            except subprocess.TimeoutExpired:
                pass
            self._proc = None
        self.state_file.unlink(missing_ok=True)
        self.key_file.unlink(missing_ok=True)
        return stopped

    def restart(self, **kwargs) -> LocalEndpoint:
        self.stop()
        return self.start(**kwargs)
