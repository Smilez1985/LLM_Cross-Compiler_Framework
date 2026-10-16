#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Dataset Manager

Calibration datasets for quantization and the perplexity check of converted models (design 5.1, decisions D1 and
D23 as changed by the seventh round, 2').

The perplexity check runs the self-built llama-perplexity of this machine: a build of the build store
(<state dir>/runtime/llama.cpp/builds/, made by `llm-cli ditto runtime install` or imported) that passed its
verification here, still matches its manifest and is of the pinned llama.cpp commit. No prebuilt image and no
llama-perplexity from PATH is ever used.

- Trusted models (catalog models, checked by sha256: Ditto's own models) run natively, or in the Docker sandbox with
  sandbox=True.
- Every other GGUF (a model converted for a board, a download) never runs natively on this computer (seventh round
  2': models built for a target are measured on the target, or in Docker once the golden artifact is ready). It runs
  in the Docker sandbox: the build runs in its own builder image (only a build made in the builder container has one;
  the image exists locally and is never pulled) with no network, no capabilities, a read-only root and the host user,
  the model and the text mounted read-only.

Every run uses the shared launch policy (orchestrator/runtime/server_policy.py): no forbidden option and a cleared
environment. Failures raise PerplexityError with the reason instead of returning sentinel values.
"""

import hashlib
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from orchestrator.utils.helpers import ensure_directory
from orchestrator.utils.logging import get_logger

PERPLEXITY = "llama-perplexity"
PPL_TIMEOUT = 3600
MAX_OUTPUT = 4 * 1024 * 1024
CHUNKS = 4  # a quick estimate (llama-perplexity --chunks)
# The working folder and HOME of the sandbox: the tmpfs inside the container (orchestrator/utils/sandbox.py), not a
# host path.
CONTAINER_TMP = "/tmp"  # nosec B108

_RE_PPL = re.compile(r"Final estimate:\s*PPL\s*=\s*([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE)
_RE_TOO_SHORT = re.compile(r"you need at least (\d+) tokens", re.IGNORECASE)
_RE_BUILDER_IMAGE = re.compile(r"^llm-framework/llama-builder:[a-z0-9][a-z0-9._-]{0,63}-[0-9a-f]{12}$", re.ASCII)

# (argv, timeout, cwd, env) -> (returncode, stdout, stderr); -1 when it could not start or ran out of time.
Runner = Callable[[Sequence[str], float, str, Mapping[str, str]], Tuple[int, str, str]]


class PerplexityError(RuntimeError):
    """The perplexity could not be measured; the message says why and what to do."""


@dataclass(frozen=True)
class PerplexityRun:
    ppl: float
    model: str
    sha256: str
    trusted: bool
    where: str          # native | sandbox
    build_id: str


def parse_perplexity(output: str) -> float:
    """The final estimate of llama-perplexity's output ("Final estimate: PPL = 5.4321 +/- 0.12345")."""
    match = _RE_PPL.search(output or "")
    if match:
        return float(match.group(1))
    short = _RE_TOO_SHORT.search(output or "")
    if short:
        raise PerplexityError(f"the text is too short for this context: llama-perplexity needs at least "
                              f"{short.group(1)} tokens; use a longer text or a smaller context")
    raise PerplexityError("llama-perplexity printed no final estimate")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _tail(text: str, limit: int = 400) -> str:
    clean = " ".join("".join(ch if ch.isprintable() else " " for ch in (text or "")).split())
    return clean[-limit:] if clean else "no output"


def default_runner(argv: Sequence[str], timeout: float, cwd: str, env: Mapping[str, str]) -> Tuple[int, str, str]:
    try:
        res = subprocess.run(list(argv), capture_output=True, timeout=timeout, check=False, cwd=cwd, env=dict(env),
                             stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return -1, "", f"did not finish within {timeout:.0f} s"
    except (OSError, ValueError) as e:
        return -1, "", str(e)
    decode = lambda b: (b or b"")[-MAX_OUTPUT:].decode("utf-8", "replace")  # noqa: E731
    return res.returncode, decode(res.stdout), decode(res.stderr)


class DatasetManager:
    """Calibration datasets and the perplexity check of GGUF models."""

    def __init__(self, framework_manager, *, store: Any = None, runner: Optional[Runner] = None,
                 engine: Any = None, catalog: Any = None,
                 platform: Optional[Tuple[str, str]] = None, profile: Any = None):
        """store (BuildStore), runner, engine (ContainerEngine), catalog, platform ((os, arch)) and profile (this
        machine's HardwareProfile) replace the framework's (tests)."""
        self.logger = get_logger(__name__)
        self.framework = framework_manager
        self.config = getattr(framework_manager, "config", None)
        self._store = store
        self._runner = runner or default_runner
        self._engine = engine
        self._catalog = catalog
        self._platform = platform
        self._profile = profile

    # --- datasets ---
    def detect_domain(self, model_path: str) -> Optional[str]:
        """Attempts to deterministically detect the model domain."""
        path = Path(model_path)
        if not path.exists():
            return None
        marker_file = path / "domain.txt"
        if marker_file.exists():
            try:
                return marker_file.read_text(encoding='utf-8').strip().lower()
            except OSError:
                pass
        return None

    def generate_synthetic_dataset(self, domain: str, count: int = 50) -> List[str]:
        """Deprecated: builds use the shipped calibration text (data/calibration) or the user's own dataset."""
        self.logger.warning("generate_synthetic_dataset is deprecated: builds use the shipped calibration text "
                            "or your own dataset.")
        return []

    def save_dataset(self, data: List[str], path: Path) -> bool:
        """Saves the dataset to disk."""
        try:
            ensure_directory(path.parent)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            self.logger.error(f"Failed to save dataset: {e}")
            return False

    # --- what the check needs ---
    def _catalog_obj(self):
        if self._catalog is None:
            from orchestrator.runtime.catalog import load_catalog
            self._catalog = load_catalog()
        return self._catalog

    def _store_obj(self):
        if self._store is None:
            from orchestrator.runtime.build.store import BuildStore
            from orchestrator.runtime.llama_server import user_data_dir
            state = getattr(self.config, "user_state_dir", None)
            self._store = BuildStore(Path(state) if state else user_data_dir())
        return self._store

    def _host(self) -> Tuple[str, str]:
        if self._platform is None:
            from orchestrator.runtime.hardware import normalize_platform
            self._platform = normalize_platform()
        return self._platform

    def _cpu_problem(self, plan: Any) -> Optional[str]:
        """Why a -march=native build of another CPU cannot run on this one (build/host.py cpu_problem), or None; a
        probe that fails decides nothing."""
        from orchestrator.runtime.build.host import cpu_problem
        if self._profile is None:
            try:
                from orchestrator.runtime.hardware import local_profile
                self._profile = local_profile()
            except Exception as e:
                self.logger.warning(f"Could not probe the CPU for the build check ({e.__class__.__name__})")
                return None
        return cpu_problem(plan, self._profile)

    def trusted_sha256(self) -> Dict[str, str]:
        """sha256 -> name of the models that run natively without asking: the catalog's pinned models."""
        catalog = self._catalog_obj()
        trusted = {m.sha256: f"catalog model {m.tier} ({m.file})" for m in catalog.models if m.sha256}
        smoke = getattr(catalog, "smoke_model", None)
        if smoke is not None:
            trusted[smoke.sha256] = f"smoke-test model ({smoke.file})"
        return trusted

    def perplexity_build(self, *, sandbox: bool = False):
        """(StoredBuild, path of llama-perplexity): the self-built llama-perplexity of this machine. The active build
        first, then the CPU fallback, then any other stored build; each must have passed its verification here, match
        its manifest, be of the pinned commit and be built for this machine (OS, architecture, and the CPU of a
        -march=native build). The sandbox needs a build made in the builder container (it runs in that image)."""
        from orchestrator.runtime.bench import find_program_build
        found = find_program_build(self._store_obj(), self._catalog_obj(), self._host(), PERPLEXITY, sandbox=sandbox,
                                   cpu_problem=self._cpu_problem)
        if found is not None:
            return found
        if sandbox:
            raise PerplexityError("The Docker sandbox runs a llama.cpp build made in the framework's builder container, "
                                  "and there is none: llm-cli ditto runtime install --placement container")
        raise PerplexityError("There is no self-built llama-perplexity on this machine: llm-cli ditto runtime install "
                              "builds it together with the server (or import a build: llm-cli ditto runtime import)")

    # --- the perplexity check ---
    def measure_perplexity(self, model_path: Path, dataset_path: Path, context_length: int = 512, *,
                           sandbox: bool = False) -> float:
        """The perplexity (PPL, lower is better) of a GGUF model on a text file. See run_perplexity."""
        return self.run_perplexity(model_path, dataset_path, context_length, sandbox=sandbox).ppl

    def run_perplexity(self, model_path: Path, dataset_path: Path, context_length: int = 512, *,
                       sandbox: bool = False) -> PerplexityRun:
        """Runs the self-built llama-perplexity on the model. A catalog model runs natively (or in the Docker sandbox
        with sandbox=True); any other model runs in the Docker sandbox only, never natively on this computer.
        Raises PerplexityError."""
        model = Path(model_path)
        text = Path(dataset_path)
        if not model.is_file() or model.is_symlink() or model.suffix.lower() != ".gguf":
            raise PerplexityError(f"{model.name} is not a GGUF model file")
        if not text.is_file():
            raise PerplexityError(f"the text file {text.name} does not exist")
        ctx = int(context_length)
        if not 64 <= ctx <= 32768:
            raise PerplexityError("the context length must be between 64 and 32768 tokens")
        digest = _sha256(model)
        trusted = digest in self.trusted_sha256()
        sandbox = sandbox or not trusted  # a model built for a board is never measured natively here
        build, binary = self.perplexity_build(sandbox=sandbox)
        if sandbox:
            output = self._run_sandbox(build, model, text, ctx)
            where = "sandbox"
        else:
            output = self._run_native(binary, model, text, ctx)
            where = "native"
        ppl = parse_perplexity(output)
        self.logger.info(f"Perplexity of {model.name}: {ppl} ({where}, build {build.build_id[:16]})")
        return PerplexityRun(ppl=ppl, model=model.name, sha256=digest, trusted=trusted, where=where,
                             build_id=build.build_id)

    def _run_native(self, binary: Path, model: Path, text: Path, ctx: int) -> str:
        from orchestrator.runtime.server_policy import check_command, cleared_env
        argv = [str(binary), "-m", str(model.resolve()), "-f", str(text.resolve()), "-c", str(ctx),
                "--chunks", str(CHUNKS)]
        check_command(argv)
        with tempfile.TemporaryDirectory(prefix="llm-ppl-") as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            rc, out, err = self._runner(argv, PPL_TIMEOUT, tmp, cleared_env(home))
        if rc != 0:
            raise PerplexityError(f"llama-perplexity failed (exit {rc}): {_tail(err or out)}")
        return err + "\n" + out

    def _run_sandbox(self, build: Any, model: Path, text: Path, ctx: int) -> str:
        from orchestrator.runtime.build.executor import DockerEngine, Mount
        from orchestrator.runtime.server_policy import check_command
        from orchestrator.utils.sandbox import container_sandbox, sandbox_user
        engine = self._engine or DockerEngine()
        reason = engine.unavailable()
        if reason:
            raise PerplexityError(f"the Docker sandbox is not available: {reason}")
        image = str(build.manifest.get("builder_image") or "")
        # The manifest of an imported build comes from another machine: only the tag form the executor gives its own
        # builder images (executor.builder_image_tag) is accepted, and only when that image exists here.
        if not _RE_BUILDER_IMAGE.fullmatch(image) or not engine.image_exists(image):
            raise PerplexityError(f"the builder image of build {build.build_id[:16]} is not on this machine (images "
                                  "are built locally, never pulled): build the runtime in the container again, "
                                  "llm-cli ditto runtime install --placement container")
        command = ["/opt/llama/bin/llama-perplexity", "-m", "/models/model.gguf", "-f", "/data/text.txt",
                   "-c", str(ctx), "--chunks", str(CHUNKS)]
        check_command(command)
        options = container_sandbox(sandbox_user(), working_dir=CONTAINER_TMP)
        if options.get("network_mode") != "none":  # pragma: no cover - guarded by the sandbox module's tests
            raise PerplexityError("the sandbox must have no network")
        mounts = [Mount(build.bin_dir, "/opt/llama/bin", True), Mount(model.resolve(), "/models/model.gguf", True),
                  Mount(text.resolve(), "/data/text.txt", True)]
        env = {"HOME": CONTAINER_TMP, "LANG": "C", "LC_ALL": "C"}
        with tempfile.TemporaryDirectory(prefix="llm-ppl-") as tmp:
            log = Path(tmp) / "perplexity.log"
            rc = engine.run(image, command, mounts=mounts, env=env, options=options, log=log, timeout=PPL_TIMEOUT)
            try:
                output = log.read_bytes()[-MAX_OUTPUT:].decode("utf-8", "replace")
            except OSError:
                output = ""
        if rc != 0:
            raise PerplexityError(f"llama-perplexity failed in the sandbox (exit {rc}): {_tail(output)}")
        return output

    def compare_quantizations(self, base_model: Optional[Path], quant_models: List[Path], dataset: Path, *,
                              sandbox: bool = False) -> Dict[str, Any]:
        """Measures the perplexity of each quantization (and of the base model) and ranks them; a model whose
        measurement failed is listed with its error, after the measured ones."""
        results: Dict[str, Any] = {}
        base_ppl = None
        if base_model and Path(base_model).exists():
            try:
                base_ppl = self.measure_perplexity(base_model, dataset, sandbox=sandbox)
                results["baseline"] = {"path": str(base_model), "ppl": base_ppl}
            except PerplexityError as e:
                results["baseline"] = {"path": str(base_model), "error": str(e)}
        measured, failed = [], []
        for qm in quant_models:
            try:
                ppl = self.measure_perplexity(qm, dataset, sandbox=sandbox)
            except PerplexityError as e:
                failed.append({"name": Path(qm).name, "path": str(qm), "error": str(e)})
                continue
            degradation = ((ppl - base_ppl) / base_ppl) * 100 if base_ppl else 0.0
            measured.append({"name": Path(qm).name, "path": str(qm), "ppl": ppl,
                             "degradation_percent": round(degradation, 2)})
        measured.sort(key=lambda x: x["ppl"])  # lower is better
        results["ranking"] = measured + failed
        if measured:
            best = measured[0]
            results["recommendation"] = f"Best model is {best['name']} with PPL {best['ppl']}"
        return results
