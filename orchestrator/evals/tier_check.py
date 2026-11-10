#!/usr/bin/env python3
"""
The measurement stage of `llm-cli ditto eval --measure` (and the evaluation window's "Measure on this computer"):
whether a tier of Ditto's local model meets the start thresholds on this computer (DESIGN "Ditto, the local runtime
and the LLM layer", the row "Start thresholds for Ditto's local model").

For each tier, on this computer only, with this computer's self-built llama.cpp (the build store's verified build of
the pin that runs here, found like the benchmark's; never a program from PATH):

1. Speed: llama-bench through runtime/bench.BenchRunner, the generated tokens per second.
2. Quality: llama-perplexity with Auto-Tuning's one KLD invocation (Core/tuning.perplexity_args): the reference's logits
   on the fixed evaluation text, then the tier's file against them; the statistics read by tuning.parse_statistics.
   The reference follows tuning.reference_type (the 16-bit original when it fits into three quarters of the memory,
   else the Q8_0), pinned per tier in the catalogue. One reference pass serves every tier of the same model in a run.
3. The record (runtime/tier_advice.TierChecks) with the fingerprint; a tier whose record still matches is not measured
   again unless asked (`again`).

plan() checks everything before anything runs: the tier is downloaded, a reference is pinned, the limits (chunks,
context), and the disk (the logits file and a missing reference). A missing reference is downloaded only through
download_references(), which the CLI and the window call after the person's yes. The logits go into a private folder
in the framework's cache, removed when the stage ends, also on Cancel or an error. A set stop event ends the running
program (TierCheckStopped).

The programs' output is untrusted text: only numbers are taken from it, and a failure's message never carries it.
"""

import contextlib
import os
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from orchestrator.Core import tuning
from orchestrator.runtime import tier_advice

PERPLEXITY = "llama-perplexity"
RUN_TIMEOUT = 2 * 3600          # one llama-perplexity pass at most (a 9B F16 reference on a slow CPU takes long)
MAX_OUTPUT = 1024 * 1024
WORK_PREFIX = ".llm-tier-check-"
DISK_MARGIN = 256 * 1024 * 1024  # left free next to what the stage writes

ProgressFn = Callable[[str], None]


class TierCheckError(RuntimeError):
    """A measurement that cannot run or failed; the message says why and what helps (never a program's output)."""

    def __init__(self, message: str, code: str = "failed"):
        super().__init__(message)
        self.code = code


class TierCheckStopped(TierCheckError):
    def __init__(self):
        super().__init__("The measurement was stopped.", code="cancelled")


@dataclass
class TierPlan:
    """What the stage does for one tier."""
    model: Any                               # catalog.ModelEntry of the tier
    reference: str                           # F16 or Q8_0
    reference_reason: str
    reference_entry: Any                     # the reference as a ModelEntry (ModelEntry.reference_entry)
    reference_missing: bool
    fingerprint: tier_advice.Fingerprint
    kept: Optional[Dict[str, Any]] = None    # a matching record: not measured again

    @property
    def tier(self) -> str:
        return self.model.tier


@dataclass
class StagePlan:
    tiers: List[TierPlan]
    chunks: int = tuning.CHUNKS_DEFAULT
    ctx: int = tuning.CTX_DEFAULT
    logits_bytes: int = 0
    disk_bytes: int = 0
    notes: List[str] = field(default_factory=list)

    @property
    def to_measure(self) -> List[TierPlan]:
        return [t for t in self.tiers if t.kept is None]

    def missing_references(self) -> List[Any]:
        """The reference entries to download first (each file once)."""
        seen, out = set(), []
        for t in self.to_measure:
            if t.reference_missing and t.reference_entry.file not in seen:
                seen.add(t.reference_entry.file)
                out.append(t.reference_entry)
        return out

    def lines(self) -> List[str]:
        out = []
        for t in self.tiers:
            if t.kept is not None:
                out.append(f"{t.tier}: measured on this computer already ({tier_advice.numbers_text(t.kept)}, "
                           f"{t.kept.get('verdict')}); --again measures it anew")
            else:
                out.append(f"{t.tier}: against {t.reference_reason}"
                           + (" (to be downloaded first)" if t.reference_missing else ""))
        if self.to_measure:
            out.append(f"Evaluation: {self.chunks} chunks of {self.ctx} tokens of the fixed evaluation text; the "
                       f"reference's logits take about {self.logits_bytes / tuning.GIB:.1f} GB in the cache while it "
                       "runs")
        return out + list(self.notes)


def _vocab_guess(model: Any) -> int:
    """The vocabulary for the logits size: Qwen3.5's 248,320 tokens for its tiers, else the largest common one."""
    return 248320 if "qwen3.5" in str(model.repo).lower() else 152064


def plan(rt: Any, tiers: Sequence[str], *, chunks: int = tuning.CHUNKS_DEFAULT, ctx: int = tuning.CTX_DEFAULT,
         again: bool = False, cache_dir: Optional[Path] = None) -> StagePlan:
    """The stage for `tiers` on this computer (`rt`: a LocalLLMRuntime). TierCheckError when a tier is unknown, not
    downloaded or has no pinned reference, when the limits are wrong, or when the disk cannot hold the run."""
    try:
        tuning.check_limits(chunks, ctx)
    except tuning.TuningError as e:
        raise TierCheckError(str(e), code="invalid_value") from None
    if not tiers:
        raise TierCheckError("Name the tiers to measure (--tier).", code="invalid_value")
    host_mib = rt.hardware().ram_total / (1024 * 1024)
    backend = tier_advice.active_backend(rt)
    out: List[TierPlan] = []
    for name in dict.fromkeys(tiers):
        try:
            model = rt.catalog.model(name)
        except KeyError as e:
            raise TierCheckError(str(e.args[0]), code="invalid_value") from None
        if rt.model_state(model) == "missing":
            raise TierCheckError(f"{model.tier} is not downloaded; download it first: llm-cli ditto models download "
                                 f"{model.tier}", code="needs_person")
        if not model.reference:
            raise TierCheckError(f"The catalogue pins no reference for {model.tier}, so its KLD cannot be measured.",
                                 code="not_supported")
        f16 = model.reference.get("F16")
        kind, why = tuning.reference_type(f16.size if f16 else None, host_mib)
        if kind not in model.reference:
            kind = next(iter(model.reference))
            why = f"the {kind} model (the only reference the catalogue pins)"
        entry = model.reference_entry(kind)
        fingerprint = tier_advice.current_fingerprint(rt, model, backend=backend)
        record = rt.tier_checks().get(model.tier)
        kept = record if (record is not None and not again and fingerprint.matches(record)
                          and record.get("chunks") == int(chunks) and record.get("ctx") == int(ctx)) else None
        why = why.replace("the F16 model", f"its 16-bit original ({entry.file})").replace(
            "a Q8_0 made from the F16", f"its Q8_0 ({entry.file}), because the 16-bit original")
        out.append(TierPlan(model=model, reference=kind, reference_reason=why, reference_entry=entry,
                            reference_missing=rt.model_state(entry) == "missing", fingerprint=fingerprint, kept=kept))
    stage = StagePlan(tiers=out, chunks=int(chunks), ctx=int(ctx))
    measuring = stage.to_measure
    if measuring:
        stage.logits_bytes = max(tuning.logits_bytes(_vocab_guess(t.model), chunks, ctx) for t in measuring)
        downloads = sum(int(e.size or 0) for e in stage.missing_references())
        stage.disk_bytes = stage.logits_bytes + downloads
        _check_disk(Path(cache_dir) if cache_dir else Path(tempfile.gettempdir()), stage.logits_bytes,
                    Path(rt.models_dir), downloads)
    return stage


def _free(path: Path) -> Optional[int]:
    probe = path
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return None


def _check_disk(cache: Path, logits: int, models: Path, downloads: int) -> None:
    """The disk rule of the stage: the logits in the cache folder and the references to download in the models
    folder, with a margin, checked before anything starts (on one file system together)."""
    need_cache, need_models = logits + DISK_MARGIN, downloads + DISK_MARGIN if downloads else 0
    free_cache, free_models = _free(cache), _free(models)
    same = False
    with contextlib.suppress(OSError):
        a, b = cache, models
        while not a.exists():
            a = a.parent
        while not b.exists():
            b = b.parent
        same = os.stat(a).st_dev == os.stat(b).st_dev
    if same and free_cache is not None and free_cache < need_cache + need_models:
        raise TierCheckError(f"The measurement needs about {(need_cache + need_models) / tuning.GIB:.1f} GB free "
                             f"(the reference's logits and the references to download); "
                             f"{free_cache / tuning.GIB:.1f} GB are free. Fewer chunks (--chunks) need less.",
                             code="insufficient_resources")
    if free_cache is not None and free_cache < need_cache:
        raise TierCheckError(f"The reference's logits need about {need_cache / tuning.GIB:.1f} GB in the cache "
                             f"folder; {free_cache / tuning.GIB:.1f} GB are free. Fewer chunks (--chunks) need less.",
                             code="insufficient_resources")
    if need_models and free_models is not None and free_models < need_models:
        raise TierCheckError(f"The references to download need about {need_models / tuning.GIB:.1f} GB in Ditto's "
                             f"models folder; {free_models / tuning.GIB:.1f} GB are free.",
                             code="insufficient_resources")


def download_references(rt: Any, stage: StagePlan, progress: Any = None, stop: Any = None) -> List[Path]:
    """Downloads the references the stage misses (after the person's yes; offline mode allows only a local mirror,
    as every download of Ditto's models). Each is checked by size and sha256 like a tier's file."""
    paths = []
    for entry in stage.missing_references():
        paths.append(rt.download_entry(entry, progress=progress, stop=stop))
    for t in stage.tiers:
        t.reference_missing = rt.model_state(t.reference_entry) == "missing"
    return paths


# --- running the programs --------------------------------------------------------------------------------------------

def find_perplexity(rt: Any):
    """(StoredBuild, path) of this computer's self-built llama-perplexity (runtime/bench.find_program_build: the
    active build first, then the CPU fallback; verified here, of the pin, for this machine)."""
    from orchestrator.runtime.bench import find_program_build
    found = find_program_build(rt.store, rt.catalog, rt.platform(), PERPLEXITY, cpu_problem=rt._cpu_problem)
    if found is None:
        raise TierCheckError("There is no self-built llama-perplexity on this computer; build the local runtime "
                             "first: llm-cli ditto runtime install", code="needs_person")
    return found


def _watch(proc: Any, stop: Optional[threading.Event], timeout: float) -> bool:
    """Waits for `proc`; terminates it on stop or after `timeout`. True when it was ended that way."""
    deadline = time.monotonic() + timeout
    while proc.poll() is None:
        if (stop is not None and stop.is_set()) or time.monotonic() > deadline:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:  # noqa: BLE001 - it does not end: kill it
                proc.kill()
            return True
        time.sleep(0.2)
    return False


def run_perplexity(binary: Path, args: List[str], work: Path, *, fds: Sequence[int] = (),
                   stop: Optional[threading.Event] = None, popen: Callable[..., Any] = subprocess.Popen,
                   timeout: float = RUN_TIMEOUT) -> str:
    """Runs llama-perplexity with `args` (tuning.perplexity_args) in a cleared environment, no stdin, `fds` inherited
    (the checked model files); its output (untrusted) for parse_statistics. TierCheckError when it fails."""
    from orchestrator.runtime.server_policy import check_command, cleared_env
    argv = [str(binary), *args]
    check_command(argv)
    home = work / "home"
    home.mkdir(exist_ok=True)
    out_path = work / "perplexity.out"
    with open(out_path, "wb") as out:
        try:
            proc = popen(argv, cwd=str(work), env=cleared_env(home), stdin=subprocess.DEVNULL, stdout=out,
                         stderr=subprocess.STDOUT, **({"pass_fds": tuple(fds)} if fds and os.name != "nt" else {}))
        except OSError as e:
            raise TierCheckError(f"llama-perplexity could not start: {e}") from None
        ended = _watch(proc, stop, timeout)
    output = out_path.read_bytes()[-MAX_OUTPUT:].decode("utf-8", "replace")
    with contextlib.suppress(OSError):
        out_path.unlink()
    if stop is not None and stop.is_set():
        raise TierCheckStopped()
    if ended:
        raise TierCheckError(f"llama-perplexity took longer than {int(timeout // 60)} minutes and was stopped.")
    if proc.returncode != 0:
        raise TierCheckError(f"llama-perplexity failed (exit {proc.returncode}).")
    return output


@contextlib.contextmanager
def _opened(path: Path, sha256: str, label: str):
    """The model file opened once and checked by its sha256 (runtime/bench.open_pinned); yields (fd, the path a child
    process opens: its descriptor path where the system has one, else the file's path)."""
    from orchestrator.runtime.bench import BenchError, fd_path, open_pinned
    try:
        fd = open_pinned(path, sha256, label)
    except BenchError as e:
        raise TierCheckError(str(e)) from None
    try:
        yield fd, fd_path(fd) or str(path)
    finally:
        os.close(fd)


def _speed(rt: Any, model: Any, stop: Optional[threading.Event], bench: Any = None) -> Any:
    from orchestrator.runtime.bench import BenchError, BenchRunner, BenchStopped
    runner = bench or BenchRunner(rt.data_dir, store=rt.store, catalog=rt.catalog, platform=rt.platform(),
                                  cpu_problem=rt._cpu_problem)
    try:
        return runner.run(rt.model_path(model), kind="tier_speed", label=f"catalog model {model.tier}",
                          trusted=True, sha256=model.sha256, stop=stop)
    except BenchStopped:
        raise TierCheckStopped() from None
    except BenchError as e:
        raise TierCheckError(f"The speed of {model.tier} could not be measured: {e}") from None


def measure(rt: Any, stage: StagePlan, *, cache_dir: Optional[Path] = None, stop: Optional[threading.Event] = None,
            progress: Optional[ProgressFn] = None, popen: Callable[..., Any] = subprocess.Popen,
            bench: Any = None) -> List[Dict[str, Any]]:
    """Runs the stage: per reference one pass for its logits, then each tier's speed and KLD; each record is kept as
    soon as it is measured. Returns the records of every tier of the stage (the kept ones too), in its order."""
    from orchestrator.utils.evaluation_text import text_sha256, write_evaluation_text
    say = progress or (lambda _msg: None)
    if any(t.reference_missing for t in stage.to_measure):
        names = ", ".join(e.file for e in stage.missing_references())
        raise TierCheckError(f"The reference is not downloaded ({names}); download it first (the person's yes).",
                             code="needs_person")
    results: Dict[str, Dict[str, Any]] = {t.tier: t.kept for t in stage.tiers if t.kept is not None}
    groups: Dict[str, List[TierPlan]] = {}
    for t in stage.to_measure:
        groups.setdefault(t.reference_entry.file, []).append(t)
    if groups:
        build, binary = find_perplexity(rt)
        text_digest = text_sha256()
        parent = Path(cache_dir) if cache_dir else Path(tempfile.gettempdir())
        parent.mkdir(parents=True, exist_ok=True)
        checks = rt.tier_checks()
        for members in groups.values():
            ref = members[0].reference_entry
            work = Path(tempfile.mkdtemp(prefix=WORK_PREFIX, dir=str(parent)))
            try:
                text = write_evaluation_text(work / "evaluation.txt")
                logits = work / "reference.kld"
                say(f"The reference's logits ({ref.file}, {stage.chunks} chunks of {stage.ctx} tokens)")
                with _opened(rt.model_path(ref), ref.sha256, f"the reference {ref.file}") as (fd, through):
                    run_perplexity(binary, tuning.perplexity_args(model=through, text=str(text), ctx=stage.ctx,
                                                                  chunks=stage.chunks, logits=str(logits)),
                                   work, fds=(fd,), stop=stop, popen=popen)
                if not logits.is_file() or logits.stat().st_size == 0:
                    raise TierCheckError("llama-perplexity wrote no logits of the reference.")
                for t in members:
                    say(f"{t.tier}: speed (llama-bench)")
                    speed = _speed(rt, t.model, stop, bench)
                    say(f"{t.tier}: KLD against the reference")
                    with _opened(rt.model_path(t.model), t.model.sha256, f"the model of {t.tier}") as (fd, through):
                        output = run_perplexity(binary, tuning.perplexity_args(model=through, ctx=stage.ctx,
                                                                               chunks=stage.chunks,
                                                                               logits=str(logits)),
                                                work, fds=(fd,), stop=stop, popen=popen)
                    stats = tuning.parse_statistics(output)
                    if "kld" not in stats:
                        raise TierCheckError(f"llama-perplexity gave no KLD for {t.tier}.")
                    if "chunks" in stats:
                        stats["chunks_measured"] = stats.pop("chunks")
                    fingerprint = tier_advice.Fingerprint(t.fingerprint.sha256, t.fingerprint.computer,
                                                          str(build.commit), str(build.backend))
                    record = tier_advice.make_record(tier=t.tier, model=t.model, fingerprint=fingerprint,
                                                     reference=t.reference, chunks=stage.chunks, ctx=stage.ctx,
                                                     text_sha256=text_digest, tokens_s=speed.gen_ts,
                                                     prompt_ts=speed.prompt_ts, stats=stats,
                                                     build_id=str(build.build_id))
                    checks.save(record)
                    results[t.tier] = record
                    say(f"{t.tier}: {record['verdict']} ({tier_advice.numbers_text(record)})")
            finally:
                shutil.rmtree(work, ignore_errors=True)
    return [results[t.tier] for t in stage.tiers if t.tier in results]


def result_lines(records: Sequence[Dict[str, Any]]) -> List[str]:
    """One plain line per record: the tier, its verdict in words and the numbers, and why it failed."""
    words = {"passed": "passed", "usable": "usable (KLD above 0.05, at most 0.10)", "failed": "FAILED"}
    out = []
    for r in records:
        line = f"{r.get('tier')}: {words.get(r.get('verdict'), r.get('verdict'))}, {tier_advice.numbers_text(r)}"
        if r.get("verdict") == "failed":
            line += f" ({'; '.join(tier_advice.failure_reasons(r))})"
        out.append(line)
    return out


__all__ = ["PERPLEXITY", "StagePlan", "TierCheckError", "TierCheckStopped", "TierPlan", "download_references",
           "find_perplexity", "measure", "plan", "result_lines", "run_perplexity"]
