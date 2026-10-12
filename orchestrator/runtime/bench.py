#!/usr/bin/env python3
"""
Speed benchmarks of GGUF models on this computer with the self-built llama-bench (runtime design 6.2 and 6.3, runtime
decision D23).

- The program is the llama-bench of a self-built (or imported) build of the build store that passed its verification
  here, still matches its manifest, is of the pinned llama.cpp commit and was built for this machine: the active build
  first, then the CPU fallback, then any other. No llama-bench from PATH and no prebuilt image is ever used.
- Catalog models (Ditto's tiers and the smoke-test model, pinned by sha256) are trusted and run natively.
- Every other GGUF (a user conversion, a download) is untrusted. It runs natively only after the person's explicit
  consent, which the caller writes to the audit log before the run (decision D23: native is the point of the
  self-built tools); without that consent nothing runs. The Docker sandbox is the opt-in: the build's own builder
  image (made locally, never pulled), no network, no capabilities, a read-only root, the host user, the program and the
  model mounted read-only.
- Every run goes through the shared launch policy (server_policy.check_command, a cleared environment, no stdin).
- Results are kept in <state dir>/benchmarks/runs/<run_id>.json (schema llm-framework.bench/1) with an append-only
  index.jsonl: kind, model sha256, build id, the llama-bench values (prompt and generation tokens per second), peak
  memory, duration and time. A run is valid while build id and model sha256 are unchanged.

A stop event ends a native run (the process is terminated); a sandbox run stops at its time limit.
"""

import hashlib
import json
import secrets
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

BENCH = "llama-bench"
SCHEMA = "llm-framework.bench/1"
RUN_TIMEOUT = 1800
MAX_OUTPUT = 4 * 1024 * 1024
# A short, comparable run: 512 prompt tokens, 128 generated tokens, 3 repetitions (runtime design 6.2).
BENCH_ARGS = ("-p", "512", "-n", "128", "-r", "3", "-o", "json")
# The tmpfs inside the sandbox container, not a host path.
CONTAINER_TMP = "/tmp"  # nosec B108
AUDIT_TOOL = "benchmark.untrusted_model_native"


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


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


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
                 popen: Callable[..., Any] = subprocess.Popen):
        self.state_dir = Path(state_dir)
        self.root = self.state_dir / "benchmarks"
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
        """Runs llama-bench on `model` (sha256 checked by the caller; `trusted` says whether it is a catalog model).
        An untrusted model runs natively only when the caller has the person's consent on record; this method does
        not ask. Raises BenchError."""
        model = Path(model)
        if not model.is_file() or model.is_symlink() or model.suffix.lower() != ".gguf":
            raise BenchError(f"{label} is not a GGUF model file.", code="not_supported")
        build, binary = self.bench_build(sandbox=sandbox)
        started = time.monotonic()
        if sandbox:
            output, peak = self._run_sandbox(build, model), None
        else:
            output, peak = self._run_native(binary, model, build, stop)
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

    def _run_native(self, binary: Path, model: Path, build: Any, stop: Optional[threading.Event]):
        from orchestrator.runtime.server_policy import check_command, cleared_env
        argv = self._argv(str(binary), str(model.resolve()), build)
        check_command(argv)
        with tempfile.TemporaryDirectory(prefix="llm-bench-") as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            out_path, err_path = Path(tmp) / "out", Path(tmp) / "err"
            with open(out_path, "wb") as out, open(err_path, "wb") as err:
                try:
                    proc = self._popen(argv, cwd=tmp, env=cleared_env(home), stdin=subprocess.DEVNULL, stdout=out,
                                       stderr=err)
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

    def _run_sandbox(self, build: Any, model: Path) -> str:
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
        mounts = [Mount(build.bin_dir, "/opt/llama/bin", True), Mount(model.resolve(), "/models/model.gguf", True)]
        env = {"HOME": CONTAINER_TMP, "LANG": "C", "LC_ALL": "C"}
        with tempfile.TemporaryDirectory(prefix="llm-bench-") as tmp:
            log = Path(tmp) / "bench.log"
            rc = engine.run(image, command, mounts=mounts, env=env, options=options, log=log, timeout=RUN_TIMEOUT)
            try:
                output = log.read_bytes()[-MAX_OUTPUT:].decode("utf-8", "replace")
            except OSError:
                output = ""
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


__all__ = ["AUDIT_TOOL", "BENCH", "BenchError", "BenchRun", "BenchRunner", "BenchStopped", "find_program_build",
           "sha256_file"]
