#!/usr/bin/env python3
"""
SearXNG in the person's own Python folder, without a container (DESIGN, Knowledge base and Deep Ingest, round 103).

The other way to have a search engine for Ditto's web search besides the container (Core/searxng_setup.py), for a computer
without a container engine or a person who prefers it: SearXNG's source at one pinned commit, installed into a virtual
environment inside the person's own state folder, as the person (never root, no system user, no service, no uWSGI), and
started only when a search needs it. Nothing here is installed or started unless the person chose this way.

- `install` downloads the archive of the pinned commit from GitHub, checks that it is that commit (GitHub writes the commit
  into the archive's comment) and unpacks it without the folder of web-server templates: four files there have a ":" in
  their name, which Windows cannot store (a `git checkout` fails on it there, found by the CI check), and a local SearXNG
  does not need them. It makes the virtual environment and installs SearXNG's own pinned requirements the way its
  documentation does (`pip install --use-pep517 --no-build-isolation -e .`). It needs no git. On Windows it also puts the
  stand-in `pwd.py` (configs/searxng/shim) where SearXNG's process finds it, because SearXNG's `import pwd` exists only
  on Linux and macOS.
- `start` runs `python -m searx.webapp` on this computer's address only (127.0.0.1), with the finished settings file and the
  secret in the environment (SEARXNG_SECRET), waits until it answers (`/healthz`) and returns the process; `stop` ends it.
- `Running` does both around a search: it starts SearXNG when it is not running, and stops what it started when the last
  search that needed it is done (a search engine somebody else started is never stopped).

The calls into the system (`run`, `popen`) are parameters, so the tests need neither git nor a network.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable, List, Optional

from orchestrator.utils import atomic

REPOSITORY = "https://github.com/searxng/searxng"
COMMIT = "b99cf7496eeb837e4c60a17eacaa60fe946fc4e4"      # the pinned stand of SearXNG (2026-11-17)
MIN_PYTHON = (3, 10)                                       # setup.py of SearXNG: python_requires >= 3.10
HELPERS = ("pip", "setuptools", "wheel", "pyyaml", "msgspec", "typing-extensions", "pybind11")   # its documentation's list
ARCHIVE = f"{REPOSITORY}/archive/{COMMIT}.zip"
SKIP_PREFIXES = ("utils/templates/etc/",)   # web-server templates; four names there hold ":" (not storable on Windows)
MAX_ARCHIVE = 80 * 1024 * 1024
SHIM = Path(__file__).resolve().parents[2] / "configs" / "searxng" / "shim" / "pwd.py"
HOST = "127.0.0.1"
START_TIMEOUT = 45.0


class NativeError(RuntimeError):
    """SearXNG could not be installed or started in the person's own folder; the message says why in plain words."""


def source_dir(folder: Path) -> Path:
    return Path(folder) / "src"


def venv_dir(folder: Path) -> Path:
    return Path(folder) / "venv"


def venv_python(folder: Path) -> Path:
    return venv_dir(folder) / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")


def marker(folder: Path) -> Path:
    return Path(folder) / "installed.json"


def installed(folder: Path) -> bool:
    """Whether SearXNG is installed there at the pinned commit (an older install is not: it is installed again)."""
    try:
        data = json.loads(marker(folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("commit") == COMMIT and venv_python(folder).is_file()


def _step(run: Callable, command: List[str], what: str, cwd: Optional[Path] = None, env: Optional[dict] = None) -> None:
    try:
        done = run(command, cwd=str(cwd) if cwd else None, env=env, capture_output=True, text=True, timeout=1800)
    except FileNotFoundError:
        raise NativeError(f"{command[0]} is not installed, and {what} needs it.") from None
    except subprocess.TimeoutExpired:
        raise NativeError(f"{what} took too long and was stopped.") from None
    if done.returncode != 0:
        tail = [line for line in (done.stderr or done.stdout or "").strip().splitlines() if line.strip()][-1:]
        raise NativeError(f"{what} did not work" + (f": {tail[0][:200]}" if tail else "."))


def fetch_source(src: Path, folder: Path, urlopen: Callable = urllib.request.urlopen) -> None:
    """Downloads the pinned commit's archive and unpacks it into `src` without SKIP_PREFIXES. NativeError when the download
    fails, the archive is not that commit, or a name in it would land outside `src`."""
    archive = Path(folder) / "searxng.zip"
    try:
        with urlopen(ARCHIVE, timeout=120) as answer, open(archive, "wb") as out:
            size = 0
            while True:
                chunk = answer.read(1024 * 256)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_ARCHIVE:
                    raise NativeError("The download of SearXNG is larger than expected and was stopped.")
                out.write(chunk)
    except urllib.error.HTTPError as e:
        raise NativeError(f"Fetching SearXNG did not work (HTTP {e.code}).") from None
    except (urllib.error.URLError, OSError) as e:
        raise NativeError(f"Fetching SearXNG did not work ({type(e).__name__}).") from None
    try:
        with zipfile.ZipFile(archive) as bundle:
            if bundle.comment.decode("ascii", "replace").strip() != COMMIT:
                raise NativeError("The files fetched are not the pinned stand of SearXNG; nothing was installed.")
            members = bundle.infolist()
            root = members[0].filename.split("/")[0] + "/" if members else ""
            base = Path(src).resolve()
            for info in members:
                name = info.filename[len(root):] if info.filename.startswith(root) else info.filename
                if not name or name.startswith(SKIP_PREFIXES):
                    continue
                target = (base / name).resolve()
                if base != target and base not in target.parents:
                    raise NativeError("The archive holds a name outside its folder; nothing was installed.")
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(bundle.read(info))
    except zipfile.BadZipFile:
        raise NativeError("The download of SearXNG is damaged; nothing was installed.") from None
    except OSError as e:
        raise NativeError(f"SearXNG could not be unpacked ({type(e).__name__}).") from None
    finally:
        try:
            archive.unlink()
        except OSError:
            pass


def install(folder: Path, python: Optional[str] = None, run: Callable = subprocess.run,
            progress: Callable[[str], None] = lambda text: None, urlopen: Callable = urllib.request.urlopen) -> None:
    """Installs SearXNG at the pinned commit into `folder` (a no-op when it is installed). NativeError with the reason."""
    folder = Path(folder)
    if installed(folder):
        return
    if sys.version_info < MIN_PYTHON and python is None:
        raise NativeError(f"SearXNG needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer.")
    src = source_dir(folder)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for old in (src, venv_dir(folder)):
            if old.exists():
                atomic.remove_tree(old)     # an older or broken stand: fetched again, never mixed
        marker(folder).unlink(missing_ok=True)
        src.mkdir(parents=True)
    except OSError as e:
        raise NativeError(f"The folder for the search engine could not be prepared ({type(e).__name__}).") from None
    progress("Fetching SearXNG ...")
    fetch_source(src, folder, urlopen)
    progress("Making its Python environment ...")
    _step(run, [python or sys.executable, "-m", "venv", str(venv_dir(folder))], "making the Python environment")
    py = str(venv_python(folder))
    progress("Installing it (this takes a minute) ...")
    _step(run, [py, "-m", "pip", "install", "-q", "--upgrade", *HELPERS], "installing SearXNG's helpers")
    _step(run, [py, "-m", "pip", "install", "-q", "--use-pep517", "--no-build-isolation", "-e", "."],
          "installing SearXNG", cwd=src)
    if os.name == "nt":
        try:
            (Path(folder) / "shim").mkdir(exist_ok=True)
            shutil.copyfile(SHIM, Path(folder) / "shim" / "pwd.py")
        except OSError as e:
            raise NativeError(f"The Windows stand-in for pwd could not be written ({type(e).__name__}).") from None
    try:
        marker(folder).write_text(json.dumps({"commit": COMMIT, "repository": REPOSITORY}), encoding="utf-8")
    except OSError as e:
        raise NativeError(f"The install could not be recorded ({type(e).__name__}).") from None


def remove(folder: Path) -> bool:
    """Removes what `install` made in `folder` (the source, the Python environment, the log and the record); True when
    something was there. The settings file and the secret are not in this folder and stay."""
    folder, found = Path(folder), False
    for name in ("src", "venv", "shim"):
        if (folder / name).exists():
            atomic.remove_tree(folder / name)
            found = True
    for name in ("installed.json", "server.log", "searxng.zip"):
        if (folder / name).exists():
            (folder / name).unlink(missing_ok=True)
            found = True
    return found


def port_of(address: str, default: int = 8888) -> int:
    """The port of the SearXNG address in the setting web_search_url."""
    from urllib.parse import urlsplit
    try:
        return urlsplit(address).port or default
    except ValueError:
        return default


def environment(folder: Path, settings: Path, secret: str, port: int, base: Optional[dict] = None) -> dict:
    """The environment of SearXNG's process: its settings file, its secret, this computer's address only, and on Windows
    the stand-in for pwd on PYTHONPATH."""
    env = dict(os.environ if base is None else base)
    env.update({"SEARXNG_SETTINGS_PATH": str(settings), "SEARXNG_SECRET": secret, "SEARXNG_PORT": str(port),
                "SEARXNG_BIND_ADDRESS": HOST, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"})
    if os.name == "nt":
        shim = str(Path(folder) / "shim")
        env["PYTHONPATH"] = shim + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def healthy(port: int, timeout: float = 2.0) -> bool:
    """Whether a SearXNG answers on this computer's `port` (its /healthz; never through a proxy)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://{HOST}:{port}/healthz", timeout=timeout) as answer:
            return answer.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def start(folder: Path, settings: Path, secret: str, port: int, popen: Callable = subprocess.Popen,
          timeout: float = START_TIMEOUT, check: Callable[[int], bool] = healthy, clock: Callable = time.monotonic,
          pause: Callable = time.sleep) -> Any:
    """Starts SearXNG and waits until it answers; the process. NativeError when it ends early or does not answer in time
    (its log is in `<folder>/server.log`)."""
    if not installed(folder):
        raise NativeError("SearXNG is not installed in its folder yet.")
    log = Path(folder) / "server.log"
    try:
        out = open(log, "wb")
    except OSError as e:
        raise NativeError(f"The log of the search engine could not be opened ({type(e).__name__}).") from None
    try:
        process = popen([str(venv_python(folder)), "-m", "searx.webapp"], cwd=str(source_dir(folder)),
                        env=environment(folder, settings, secret, port), stdout=out, stderr=subprocess.STDOUT,
                        stdin=subprocess.DEVNULL)
    except OSError as e:
        out.close()
        raise NativeError(f"The search engine could not be started ({type(e).__name__}).") from None
    out.close()                      # the process has its own handle on the log
    end = clock() + timeout
    while clock() < end:
        if process.poll() is not None:
            raise NativeError("The search engine stopped right after it started; see server.log in its folder.")
        if check(port):
            return process
        pause(0.25)
    stop(process)
    raise NativeError(f"The search engine did not answer within {timeout:g} seconds; see server.log in its folder.")


def stop(process: Any, wait: float = 10.0) -> None:
    """Ends the process (asks, then insists); never raises."""
    try:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=wait)
    except Exception:  # noqa: BLE001 - a process that is gone already is the goal
        pass


class Running:
    """SearXNG for the time of a search: started when it is not running, stopped when the last search that needed it is
    done. A search engine that answered before (the person started it) is used and never stopped. One object per
    folder, shared by every search of the process."""

    def __init__(self, folder: Path, settings: Path, secret: str, port: int, start_fn: Callable = start,
                 stop_fn: Callable = stop, check: Callable[[int], bool] = healthy):
        self.folder, self.settings, self.secret, self.port = Path(folder), Path(settings), secret, port
        self._start, self._stop, self._check = start_fn, stop_fn, check
        self._lock = threading.Lock()
        self._users = 0
        self._process: Any = None

    def __enter__(self) -> "Running":
        with self._lock:
            if self._users == 0 and self._process is None and not self._check(self.port):
                self._process = self._start(self.folder, self.settings, self.secret, self.port)
            self._users += 1
        return self

    def __exit__(self, *exc: Any) -> None:
        with self._lock:
            self._users -= 1
            if self._users == 0 and self._process is not None:
                process, self._process = self._process, None
                self._stop(process)


__all__ = ["ARCHIVE", "COMMIT", "HELPERS", "HOST", "NativeError", "REPOSITORY", "Running", "SHIM", "SKIP_PREFIXES",
           "environment", "fetch_source", "healthy", "install", "port_of", "remove", "installed", "marker", "source_dir", "start", "stop", "venv_dir", "venv_python"]
