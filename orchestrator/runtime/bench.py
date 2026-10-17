#!/usr/bin/env python3
"""
Speed benchmarks of GGUF models on this computer with the self-built llama-bench (runtime design 6.2 and 6.3, runtime
decision D23 as changed by the seventh round, 2').

- The program is the llama-bench of a self-built (or imported) build of the build store that passed its verification
  here, still matches its manifest, is of the pinned llama.cpp commit and was built for this machine: the active build
  first, then the CPU fallback, then any other. No llama-bench from PATH and no prebuilt image is ever used.
- Catalog models (Ditto's tiers and the smoke-test model, pinned by sha256) are trusted and run natively: Ditto's own
  model really runs on this computer.
- Every other GGUF (a model built for a board, a download) never runs natively on this computer: measuring it here
  would say how fast this computer is, not the board (seventh round 2'). Its real values come from the board itself
  (orchestrator/runtime/device/service.benchmark, after a deploy); here it runs only in the Docker sandbox: the
  build's own builder image (made locally, never pulled), no network, no capabilities, a read-only root, the host
  user, the program and the model mounted read-only. run() refuses a native run of a model that is not a catalog
  model, whatever the caller asks.
- Every run goes through the shared launch policy (server_policy.check_command, a cleared environment, no stdin).
- Results are kept in <state dir>/benchmarks/runs/<run_id>.json (schema llm-framework.bench/1) with an append-only
  index.jsonl: kind, model sha256, build id, the llama-bench values (prompt and generation tokens per second), peak
  memory, duration and time. A run is valid while build id and model sha256 are unchanged.

A stop event ends a native run (the process is terminated) and a sandbox run (its container is killed).

The sandbox mounts a private file that is exactly the checked open model: a hard link in a private folder next to the
model, else in the framework's own cache or state folder on the same file system, else a copy read from the open file
and hashed again (its sha256 must be the checked one) in one of the framework's own folders with room for it. The
private folder is removed when the run ends, also on failure and cancel; a folder a crash left behind is removed by a
later run. When no private file can be made, the error says why (reason code insufficient_resources or needs_person).
"""

import contextlib
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from orchestrator.utils.helpers import copy_hashed, digest_matches, sha256_fd, sha256_file

BENCH = "llama-bench"
SCHEMA = "llm-framework.bench/1"
RUN_TIMEOUT = 1800
MAX_OUTPUT = 4 * 1024 * 1024
# A short, comparable run: 512 prompt tokens, 128 generated tokens, 3 repetitions (runtime design 6.2).
BENCH_ARGS = ("-p", "512", "-n", "128", "-r", "3", "-o", "json")
# The tmpfs inside the sandbox container, not a host path.
CONTAINER_TMP = "/tmp"  # nosec B108
PIN_PREFIX = ".llm-bench-"              # the private folder of a sandbox run's model file
PIN_STALE_SECONDS = 3 * RUN_TIMEOUT     # older private folders are leftovers of a crash
COPY_RESERVE = 512 * 1024 ** 2          # free space a copy of the model leaves on its disk


class BenchError(RuntimeError):
    """A benchmark that could not run; the message says why, `code` is a tool reason code, `next` what helps.
    `external_text` holds what llama-bench printed (it prints the metadata of the model it loads, which an untrusted
    GGUF controls): never part of the message, returned to AI callers marked as external text."""

    def __init__(self, message: str, code: str = "failed", next: str = "", external_text: Optional[List[str]] = None):
        super().__init__(message)
        self.code, self.next = code, next
        self.external_text = list(external_text or [])


class BenchStopped(BenchError):
    def __init__(self):
        super().__init__("The benchmark was stopped.", code="failed")


@dataclass
class BenchRun:
    run_id: str
    kind: str                    # tier_speed | artifact_speed
    model: str                   # a label, never a path
    sha256: str
    trusted: bool
    where: str                   # native | sandbox
    build_id: str
    backend: str
    prompt_ts: Optional[float] = None
    gen_ts: Optional[float] = None
    peak_rss_mib: Optional[int] = None
    duration_s: float = 0.0
    at: str = ""
    records: List[Dict[str, Any]] = field(default_factory=list)


def open_pinned(model: Path, sha256: str, label: str) -> int:
    """Opens the model file once (never following a link), hashes exactly that open file and returns its descriptor
    when the hash is `sha256`; BenchError otherwise. The run then reads this descriptor, never the path again, so the
    file cannot be swapped between the check and the run (a catalog model's hash decides that it runs natively without
    a question)."""
    import stat
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(str(model), flags)
    except OSError:
        raise BenchError(f"{label} cannot be opened.", code="failed") from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise BenchError(f"{label} is not a regular file.", code="not_supported")
        if not digest_matches(sha256_fd(fd), sha256):
            raise BenchError(f"{label} changed after it was checked (its sha256 differs); nothing ran.",
                             code="failed", next="Call run_benchmark again; the person is asked again.")
    except BaseException:
        os.close(fd)
        raise
    return fd


def _read_at(fd: int, offset: int) -> bytes:
    """1 MiB of `fd` from `offset`; a delegate of the one read by offset in utils/helpers.py (pread, or seek and read
    where the system has no pread)."""
    from orchestrator.utils import helpers
    return helpers._read_at(fd, offset, 1 << 20)


def fd_path(fd: int) -> Optional[str]:
    """A path a child process opens to reach exactly the open file `fd` it inherited (Linux /proc/self/fd, other
    Unix systems /dev/fd); None where there is none (Windows)."""
    import os
    if os.name == "nt":  # pragma: no cover - Windows keeps an open file from being replaced or removed
        return None
    for base in ("/proc/self/fd", "/dev/fd"):
        if os.path.isdir(base):
            return f"{base}/{fd}"
    return None


def _tail(text: str, limit: int = 400) -> str:
    clean = " ".join("".join(ch if ch.isprintable() else " " for ch in (text or "")).split())
    return clean[-limit:] if clean else "no output"


def find_program_build(store: Any, catalog: Any, machine: Tuple[str, str], program: str, *, sandbox: bool = False,
                       cpu_problem: Optional[Callable[[Any], Optional[str]]] = None):
    """(StoredBuild, path of `program`) of this machine's self-built llama.cpp: the active build first, then the CPU
    fallback, then any other stored build; each must have passed its verification here, match its manifest, be of the
    pinned commit and be built for this machine (and its CPU). The sandbox needs a build made in the builder container.
    None when there is none."""
    from orchestrator.runtime.build.verify import VerifyError
    pin = catalog.llama_cpp.source
    refs = store.references()
    builds = [store.get(i) for i in (refs.get("active"), refs.get("fallback")) if i] + store.list_builds()
    seen = set()
    for build in builds:
        if build is None or build.build_id in seen:
            continue
        seen.add(build.build_id)
        target = build.plan.get("target") or {}
        if not build.verified or program not in (build.manifest.get("files") or {}):
            continue
        if pin is None or build.commit != pin.commit or (target.get("os"), target.get("arch")) != machine:
            continue
        if sandbox and not build.manifest.get("builder_image"):
            continue
        if cpu_problem is not None and cpu_problem(build.plan):
            continue
        if store.check_files(build):
            continue
        try:
            return build, build.binary(program)
        except VerifyError:
            continue
    return None


class BenchRunner:
    """Runs llama-bench on a GGUF model with this machine's self-built build and keeps the result."""

    def __init__(self, state_dir: Path, *, store: Any = None, catalog: Any = None, engine: Any = None,
                 platform: Optional[Tuple[str, str]] = None, cpu_problem: Optional[Callable[[Any], Optional[str]]] = None,
                 popen: Callable[..., Any] = subprocess.Popen, cache_dir: Optional[Path] = None):
        """`cache_dir`: the framework's cache folder, where a sandbox run's private model file may go when the
        model's own folder cannot hold it (before the state folder)."""
        self.state_dir = Path(state_dir)
        self.root = self.state_dir / "benchmarks"
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self._store, self._catalog, self._engine = store, catalog, engine
        self._platform, self._cpu_problem, self._popen = platform, cpu_problem, popen

    # --- what a run needs ---

    def catalog(self):
        if self._catalog is None:
            from orchestrator.runtime.catalog import load_catalog
            self._catalog = load_catalog()
        return self._catalog

    def store(self):
        if self._store is None:
            from orchestrator.runtime.build.store import BuildStore
            self._store = BuildStore(self.state_dir)
        return self._store

    def machine(self) -> Tuple[str, str]:
        if self._platform is None:
            from orchestrator.runtime.hardware import normalize_platform
            self._platform = normalize_platform()
        return self._platform

    def trusted_sha256(self) -> Dict[str, str]:
        """sha256 -> name of the models that run natively without asking: the catalog's pinned models."""
        catalog = self.catalog()
        trusted = {m.sha256: f"catalog model {m.tier} ({m.file})" for m in catalog.models if m.sha256}
        smoke = getattr(catalog, "smoke_model", None)
        if smoke is not None and getattr(smoke, "sha256", None):
            trusted[smoke.sha256] = f"smoke-test model ({smoke.file})"
        return trusted

    def bench_build(self, *, sandbox: bool = False):
        found = find_program_build(self.store(), self.catalog(), self.machine(), BENCH, sandbox=sandbox,
                                   cpu_problem=self._cpu_problem)
        if found is None:
            if sandbox:
                raise BenchError("The Docker sandbox runs a llama.cpp build made in the framework's builder "
                                 "container, and there is none.", code="needs_person",
                                 next="Ask the person to run 'llm-cli ditto runtime install --placement container', "
                                      "or benchmark natively.")
            raise BenchError("There is no self-built llama-bench on this computer.", code="needs_person",
                             next="Build the local runtime first (build_local_runtime, or 'llm-cli ditto runtime "
                                  "install').")
        return found

    # --- the run ---

    def run(self, model: Path, *, kind: str, label: str, trusted: bool, sha256: str, sandbox: bool = False,
            stop: Optional[threading.Event] = None) -> BenchRun:
        """Runs llama-bench on `model` with the sha256 `sha256` (`trusted` says whether it is a catalog model). The
        file is opened once and hashed through that open file, and llama-bench reads exactly that file (the inherited
        descriptor natively, a private hard link of it in the sandbox), so it cannot be swapped between the check and
        the run. Only a catalog model runs natively; any other model only in the Docker sandbox (a native run of it is
        refused here, before anything is opened). Raises BenchError."""
        import os
        model = Path(model)
        if not model.is_file() or model.is_symlink() or model.suffix.lower() != ".gguf":
            raise BenchError(f"{label} is not a GGUF model file.", code="not_supported")
        if not sandbox and sha256 not in self.trusted_sha256():
            raise BenchError(f"{label} is not one of Ditto's catalog models, so it is never benchmarked natively on "
                             "this computer (that would measure this computer, not the board it was built for).",
                             code="not_supported",
                             next="Benchmark it on the board after deploying it (run_device_benchmark, 'llm-cli device "
                                  "runtime verify --bench-model', or \"Benchmark on a board\" in the Deployment "
                                  "window), or here in the Docker sandbox.")
        build, binary = self.bench_build(sandbox=sandbox)
        fd = open_pinned(model, sha256, label)
        try:
            started = time.monotonic()
            if sandbox:
                output, peak = self._run_sandbox(build, model, fd=fd, sha256=sha256, label=label, stop=stop), None
            else:
                output, peak = self._run_native(binary, model, build, stop, fd=fd)
        finally:
            os.close(fd)
        from orchestrator.runtime.build.verify import parse_bench_json
        try:
            records = parse_bench_json(output)
        except ValueError as e:
            raise BenchError(f"llama-bench wrote no usable result: {e}") from None
        prompt = [r.get("avg_ts") for r in records if r.get("n_prompt") and not r.get("n_gen")]
        gen = [r.get("avg_ts") for r in records if r.get("n_gen") and not r.get("n_prompt")]
        result = BenchRun(run_id=f"bench_{secrets.token_hex(6)}", kind=kind, model=label, sha256=sha256,
                          trusted=trusted, where="sandbox" if sandbox else "native", build_id=build.build_id,
                          backend=str(build.backend), records=records,
                          prompt_ts=float(prompt[0]) if prompt and isinstance(prompt[0], (int, float)) else None,
                          gen_ts=float(gen[0]) if gen and isinstance(gen[0], (int, float)) else None,
                          peak_rss_mib=peak, duration_s=round(time.monotonic() - started, 1),
                          at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        self._keep(result)
        return result

    def _argv(self, binary: str, model: str, build: Any) -> List[str]:
        argv = [binary, "-m", model, *BENCH_ARGS]
        if str(build.backend) != "cpu":
            argv += ["-ngl", "99"]
        return argv

    def _run_native(self, binary: Path, model: Path, build: Any, stop: Optional[threading.Event],
                    fd: Optional[int] = None):
        """`fd`: the open, checked model file; llama-bench gets it inherited and opens it through its descriptor path,
        never the model's path (on Windows the open file cannot be replaced or removed while it is open)."""
        from orchestrator.runtime.server_policy import check_command, cleared_env
        through = fd_path(fd) if fd is not None else None
        argv = self._argv(str(binary), through or str(model.resolve()), build)
        extra = {"pass_fds": (fd,)} if through else {}
        check_command(argv)
        with tempfile.TemporaryDirectory(prefix="llm-bench-") as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            out_path, err_path = Path(tmp) / "out", Path(tmp) / "err"
            with open(out_path, "wb") as out, open(err_path, "wb") as err:
                try:
                    proc = self._popen(argv, cwd=tmp, env=cleared_env(home), stdin=subprocess.DEVNULL, stdout=out,
                                       stderr=err, **extra)
                except OSError as e:
                    raise BenchError(f"llama-bench could not start: {e}") from None
                peak = self._watch(proc, stop)
            rc = proc.returncode
            output = out_path.read_bytes()[-MAX_OUTPUT:].decode("utf-8", "replace")
            errors = err_path.read_bytes()[-MAX_OUTPUT:].decode("utf-8", "replace")
        if stop is not None and stop.is_set():
            raise BenchStopped()
        if rc != 0:
            raise BenchError(f"llama-bench failed (exit {rc}); its last output is kept apart as external text.",
                             external_text=[_tail(errors or output)])
        return output, peak

    @staticmethod
    def _watch(proc: Any, stop: Optional[threading.Event]) -> Optional[int]:
        """Waits for the process, samples its peak resident memory (MiB) and terminates it on stop or at the limit."""
        try:
            import psutil
            handle = psutil.Process(proc.pid)
        except Exception:  # noqa: BLE001 - no psutil or the process is gone: no memory figure
            handle = None
        peak = 0
        deadline = time.monotonic() + RUN_TIMEOUT
        while proc.poll() is None:
            if (stop is not None and stop.is_set()) or time.monotonic() > deadline:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except Exception:  # noqa: BLE001
                    proc.kill()
                break
            if handle is not None:
                try:
                    peak = max(peak, handle.memory_info().rss)
                except Exception:  # noqa: BLE001 - it ended meanwhile
                    pass
            time.sleep(0.2)
        return peak // (1024 * 1024) if peak else None

    @staticmethod
    def _pinned_link(fd: int, folder: Path, model: Optional[Path] = None) -> Optional[Path]:
        """A hard link of the open, checked model in the private folder `folder` (no copy): through its descriptor
        path (Linux), else through the model's path, kept only when the link is the very file that is open (same
        device and inode; a path swapped meanwhile names another file and is refused). None where the system or the
        file system allows neither."""
        target = folder / "model.gguf"
        open_st = os.fstat(fd)
        through = fd_path(fd)
        for source in ([through] if through and through.startswith("/proc/") else []) + \
                ([str(model)] if model is not None else []):
            try:
                os.link(source, target)
            except (OSError, NotImplementedError):
                continue
            try:
                st = os.lstat(target)
                if (st.st_dev, st.st_ino) == (open_st.st_dev, open_st.st_ino):
                    return target
            except OSError:
                pass
            with contextlib.suppress(OSError):
                target.unlink()
        return None

    @staticmethod
    def _pinned_copy(model: Path, fd: int, folder: Path, sha256: Optional[str] = None,
                     stop: Optional[threading.Event] = None) -> Path:
        """A file in the private folder `folder` that is exactly the open, checked model: a hard link of the open file
        where the system allows it, else a copy read from it. With `sha256` the copy is hashed while it is written and
        must have that sha256 (BenchError otherwise; the copy is removed). A set `stop` (a cancel) ends the copy at
        once (BenchStopped; the partial copy is removed)."""
        linked = BenchRunner._pinned_link(fd, folder, model)
        if linked is not None:
            return linked
        target = folder / "model.gguf"

        def stopped() -> None:
            if stop is not None and stop.is_set():
                raise BenchStopped()
        try:
            digest = copy_hashed(fd, target, cancel=stopped, mode=0o644)
        except FileExistsError:
            raise  # not this copy's file
        except BaseException:
            with contextlib.suppress(OSError):
                target.unlink()
            raise
        if sha256 is not None and not digest_matches(digest, sha256):
            with contextlib.suppress(OSError):
                target.unlink()
            raise BenchError("The model changed after it was checked (the copy for the sandbox has another sha256); "
                             "nothing ran.", code="failed", next="Call run_benchmark again; the person is asked again.")
        return target

    def _scratch_folders(self) -> List[Path]:
        """The framework's own folders a sandbox run's private model file may go to: the cache folder, then the state
        folder's benchmarks folder."""
        return ([self.cache_dir / "bench"] if self.cache_dir else []) + [self.root / "pinned"]

    @staticmethod
    def _private_folder(parent: Path) -> Optional[Path]:
        """A new private folder (mode 0700) in `parent`, or None when `parent` cannot hold one (read-only, missing)."""
        try:
            return Path(tempfile.mkdtemp(prefix=PIN_PREFIX, dir=str(parent)))
        except OSError:
            return None

    @staticmethod
    def _sweep(parent: Path) -> None:
        """Removes private folders a crashed run left in `parent` (older than any run can take)."""
        cutoff = time.time() - PIN_STALE_SECONDS
        try:
            entries = list(parent.iterdir())
        except OSError:
            return
        for entry in entries:
            try:
                if entry.name.startswith(PIN_PREFIX) and entry.is_dir() and not entry.is_symlink() \
                        and entry.lstat().st_mtime < cutoff:
                    shutil.rmtree(entry, ignore_errors=True)
            except OSError:
                continue

    @contextlib.contextmanager
    def _pinned_model(self, model: Path, fd: int, sha256: str, label: str, stop: Optional[threading.Event] = None):
        """Yields a private file that is exactly the checked open model, for the sandbox mount, and removes its
        private folder afterwards (also on failure and cancel). In this order: a hard link next to the model; a hard
        link in one of the framework's own folders on the same file system; a copy read from the open file and hashed
        again in one of the framework's own folders (else next to the model) with room for it. BenchError when nothing
        works; BenchStopped when `stop` is set meanwhile (a copy stops at once)."""
        open_st = os.fstat(fd)
        scratch = []
        for folder in self._scratch_folders():
            try:
                folder.mkdir(parents=True, exist_ok=True, mode=0o700)
                scratch.append(folder)
            except OSError:
                continue
        for folder in [model.parent, *scratch]:
            self._sweep(folder)
        made: Optional[Path] = None
        try:
            source: Optional[Path] = None
            # 1 and 2: a hard link (no copy) where the folder lies on the model's file system
            for parent in [model.parent, *scratch]:
                try:
                    if os.stat(parent).st_dev != open_st.st_dev:
                        continue
                except OSError:
                    continue
                made = self._private_folder(parent)
                if made is None:
                    continue
                source = self._pinned_link(fd, made, model)
                if source is not None:
                    break
                shutil.rmtree(made, ignore_errors=True)
                made = None
            # 3: a copy, hashed again, in one of the framework's own folders with room for it (the model's own
            # folder last, as before: a system without descriptor links, e.g. macOS)
            no_room = False
            if source is None:
                for parent in [*scratch, model.parent]:
                    try:
                        if shutil.disk_usage(parent).free < open_st.st_size + COPY_RESERVE:
                            no_room = True
                            continue
                    except OSError:
                        continue
                    made = self._private_folder(parent)
                    if made is None:
                        continue
                    try:
                        source = self._pinned_copy(model, fd, made, sha256=sha256 or None, stop=stop)
                        break
                    except BenchError:
                        raise
                    except OSError as e:
                        import errno
                        no_room = no_room or e.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC))
                        shutil.rmtree(made, ignore_errors=True)
                        made = None
            if source is None:
                if no_room:
                    raise BenchError(f"The Docker sandbox needs a private copy of {label} (its folder cannot hold a "
                                     "link of it), and the framework's folders lack the room for one (about "
                                     f"{(open_st.st_size + COPY_RESERVE) / 1000 ** 3:.1f} GB needed).",
                                     code="insufficient_resources",
                                     next="Tell the person how much space the copy needs, or benchmark natively.")
                raise BenchError(f"The Docker sandbox needs a private link or copy of {label}, and neither its own "
                                 "folder nor the framework's cache or state folder can hold one (read-only or not "
                                 "writable).", code="needs_person",
                                 next="Ask the person to make the model's folder or the framework's cache folder "
                                      "writable, or benchmark natively.")
            yield source
        finally:
            if made is not None:
                shutil.rmtree(made, ignore_errors=True)

    def _run_sandbox(self, build: Any, model: Path, fd: Optional[int] = None, sha256: str = "",
                     label: str = "the model", stop: Optional[threading.Event] = None) -> str:
        import re
        from orchestrator.runtime.build.executor import DockerEngine, Mount
        from orchestrator.runtime.server_policy import check_command
        from orchestrator.utils.sandbox import container_sandbox, sandbox_user
        engine = self._engine or DockerEngine()
        reason = engine.unavailable()
        if reason:
            raise BenchError(f"The Docker sandbox is not available: {reason}", code="needs_person",
                             next="Start Docker, or benchmark natively.")
        image = str(build.manifest.get("builder_image") or "")
        if not re.fullmatch(r"llm-framework/llama-builder:[a-z0-9][a-z0-9._-]{0,63}-[0-9a-f]{12}", image) or \
                not engine.image_exists(image):
            raise BenchError(f"The builder image of build {build.build_id[:16]} is not on this computer (images are "
                             "built locally, never pulled).", code="needs_person",
                             next="Ask the person to run 'llm-cli ditto runtime install --placement container'.")
        command = self._argv("/opt/llama/bin/llama-bench", "/models/model.gguf", build)
        check_command(command)
        options = container_sandbox(sandbox_user(), working_dir=CONTAINER_TMP)
        if options.get("network_mode") != "none":  # pragma: no cover - guarded by the sandbox module's tests
            raise BenchError("the sandbox must have no network")
        env = {"HOME": CONTAINER_TMP, "LANG": "C", "LC_ALL": "C"}
        # the mounted file is exactly the checked open file: a private hard link or a checked copy (_pinned_model)
        pinned = self._pinned_model(model, fd, sha256, label, stop=stop) if fd is not None else \
            contextlib.nullcontext(model.resolve())
        with pinned as source, tempfile.TemporaryDirectory(prefix="llm-bench-") as tmp:
            if stop is not None and stop.is_set():  # cancelled while the private file was made: no container
                raise BenchStopped()
            mounts = [Mount(build.bin_dir, "/opt/llama/bin", True), Mount(source, "/models/model.gguf", True)]
            log = Path(tmp) / "bench.log"
            extra = {"stop": stop} if stop is not None else {}
            rc = engine.run(image, command, mounts=mounts, env=env, options=options, log=log, timeout=RUN_TIMEOUT,
                            **extra)
            try:
                output = log.read_bytes()[-MAX_OUTPUT:].decode("utf-8", "replace")
            except OSError:
                output = ""
        if stop is not None and stop.is_set():
            raise BenchStopped()
        if rc != 0:
            raise BenchError(f"llama-bench failed in the sandbox (exit {rc}); its last output is kept apart as "
                             "external text.", external_text=[_tail(output)])
        return output

    # --- results ---

    def _keep(self, run: BenchRun) -> None:
        from orchestrator.Core.vault_key import write_private
        runs = self.root / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        data = {"schema": SCHEMA, **asdict(run)}
        write_private(runs / f"{run.run_id}.json", json.dumps(data, indent=2, sort_keys=True) + "\n")
        line = {k: data[k] for k in ("run_id", "kind", "sha256", "build_id", "where", "prompt_ts", "gen_ts", "at")}
        with open(self.root / "index.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(line, sort_keys=True) + "\n")

    def latest(self, limit: int = 5) -> List[Dict[str, Any]]:
        """The newest runs from the index (for status displays)."""
        try:
            lines = (self.root / "index.jsonl").read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for text in reversed(lines[-200:]):
            try:
                row = json.loads(text)
            except ValueError:
                continue
            if isinstance(row, dict):
                out.append(row)
            if len(out) >= limit:
                break
        return out


__all__ = ["BENCH", "BenchError", "BenchRun", "BenchRunner", "BenchStopped", "find_program_build", "sha256_file"]
