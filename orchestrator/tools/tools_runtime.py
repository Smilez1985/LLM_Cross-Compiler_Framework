#!/usr/bin/env python3
"""
The local runtime and the device runtime for AI callers (MCP design 2.10 and 2.7, work package T14; runtime decisions
D1, D7, D23), through the one tool registry. Optional: every tool here is registered only when the runtime package
it needs exists, and is denied (so not listed) until the person enables it, also when the person allowed the whole
tier (`default_decision` deny).

This computer (Ditto's offline server, self-built from the pinned llama.cpp commit):
- build_local_runtime: plans and builds llama-server (CPU build first, then the GPU build) as a job; optionally fetches
  the catalog model of a tier (pinned by size and sha256, stopped by cancel_job, its `.part` kept). There is no
  "prebuilt" option: the official archive stays the person's labelled opt-in in the CLI (runtime decision D1). A
  missing toolchain part is refused before the question (`needs_person`, `llm-cli ditto runtime install`), because
  the framework never installs toolchains or accepts a licence for the person. No input takes a CMake option or a
  command. A cancelled build is never activated.
- start_local_runtime / stop_local_runtime: start (a job) and stop Ditto's server; only a build that passed its
  verification here is started. No result holds the server's address or its per-start key.
- run_benchmark: llama-bench on a catalog tier model or a built GGUF artifact. A catalog model is trusted and runs
  natively. Any other model is untrusted: it runs natively only after the person's explicit consent in the framework's
  own window (always asked there, written to the audit log first; runtime decision D23), or in the Docker sandbox when
  `sandbox` is set.

A board (a device the person trusted and paired, orchestrator/runtime/device/trusted.py):
- probe_remote_device: the read-only hardware probe over SSH (as `llm-cli scan --device`).
- deploy_device_runtime: builds the board's runtime in the builder container when needed and installs it as the deploy
  settings the person chose beforehand say (as `llm-cli device runtime build` and `deploy`); activated only after its
  checks pass on the board.
- run_device_benchmark: the checks of the deployed runtime with a short llama-bench run on the model the deploy settings
  name (as `llm-cli device runtime verify --bench-model`); an untrusted model only with the person's consent.
These reach another device: tier external, always asked, never covered by a standing approval; the host key the board
presents must be the trusted one, and the question shows its fingerprint.
"""

import os
import re
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.tools.output import ToolOutput, fact
from orchestrator.tools.policy import Decision, ToolTier
from orchestrator.tools.registry import ResourceSpec, ToolContext, ToolError, ToolRegistry, ToolSpec
from orchestrator.tools.spec import CallFacts, Choices, Fact, FieldMeta, GrantRule, ToolLimits

BACKENDS = ("auto", "cpu", "cuda", "vulkan", "rocm", "metal")
INSTALL_NEXT = ("Ask the person to run 'llm-cli ditto runtime install' in a terminal: it lists the missing parts, "
                "their sizes and licences, and only the person installs them.")
BUILD_NEXT = "Build it first with build_local_runtime (or the person runs 'llm-cli ditto runtime install')."
SOURCE_SIZE = 300 * 1000 ** 2          # the pinned source with its unpacked tree (estimate)
BUILD_SIZE = 2 * 1000 ** 3              # one build folder with its intermediate files (estimate)
GB = 1000 ** 3


def _size(n: Optional[int]) -> str:
    if not n:
        return "size unknown"
    return f"{n / GB:.2f} GB" if n >= 10 ** 8 else f"{n / 1000 ** 2:.1f} MB"


# Which runtime packages exist in this installation (design T14: a tool is registered only with its package).
def _has_local_runtime() -> bool:
    try:
        from orchestrator.runtime.llama_server import LocalLLMRuntime
        return all(hasattr(LocalLLMRuntime, name) for name in ("plan_install", "install", "start", "stop"))
    except Exception:  # noqa: BLE001
        return False


def _has_bench() -> bool:
    try:
        from orchestrator.runtime import bench  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _has_device_runtime() -> bool:
    try:
        from orchestrator.runtime.device import service  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


PACKAGES: Dict[str, Callable[[], bool]] = {"local_runtime": _has_local_runtime, "bench": _has_bench,
                                           "device_runtime": _has_device_runtime}


# --- inputs and outputs ---------------------------------------------------------------------------------------------

class BuildRuntimeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    backend: Literal[BACKENDS] = Field(default="auto", description="GPU backend built after the CPU build: auto (the "
                                                                   "recipe chooses for this computer), cpu (only the "
                                                                   "CPU build), cuda, vulkan, rocm, metal")
    model_tier: Optional[str] = Field(default=None, min_length=1, max_length=32,
                                      description="Also fetch the catalog model of this tier (get_status "
                                                  "local_runtime shows the recommended one)")


class StartRuntimeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tier: Optional[str] = Field(default=None, min_length=1, max_length=32,
                                description="Model tier to serve; default: the one chosen earlier or recommended")
    context_length: Optional[int] = Field(default=None, ge=256, le=131072,
                                          description="Context length in tokens (default: from free memory)")


class NoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BenchmarkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["tier_speed", "artifact_speed"] = Field(description="tier_speed: a catalog model of Ditto's tiers; "
                                                                      "artifact_speed: a built GGUF model")
    tier: Optional[str] = Field(default=None, min_length=1, max_length=32, description="The tier (tier_speed)")
    artifact_id: Optional[str] = Field(default=None, min_length=18, max_length=18, pattern=r"^a_[0-9a-f]{16}$",
                                       description="A built GGUF model from list_artifacts (artifact_speed)")
    sandbox: bool = Field(default=False, description="Run an untrusted model in the Docker sandbox instead of "
                                                     "natively (needs a build made in the builder container)")


class DeviceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: str = Field(min_length=16, max_length=16, pattern=r"^[0-9a-f]{16}$",
                           description="A trusted, paired device from list_devices")


class JobStartedOut(ToolOutput):
    job_id: str
    kind: str
    state: str


class StopOut(ToolOutput):
    stopped_processes: int


# A path in an error text: absolute (POSIX or a Windows drive) or starting with ~/; never part of a URL or a word.
_PATH_TOKEN = re.compile(r"(?<![\w:/.~-])(?:~/|/|[A-Za-z]:[\\/])[^\s'\"`,;()\[\]<>]+")


def label_paths(text: str, roots: Dict[str, str]) -> str:
    """Replaces every path in `text`: one inside a known folder becomes `<label>:<relative path>` (roots maps the
    folder to its label), any other one, the home folder's ('~/...') included, its file name only. Ids and labels
    instead of paths toward an AI (design 6.4)."""
    ordered = sorted(((str(Path(k)), v) for k, v in roots.items() if k), key=lambda kv: -len(kv[0]))

    def one(match: "re.Match[str]") -> str:
        token = match.group(0)
        tail = ""
        while token and token[-1] in ".:!?":
            token, tail = token[:-1], token[-1] + tail
        for root, label in ordered:
            if token == root or token.startswith(root.rstrip("/\\") + os.sep) or token.startswith(root + "/"):
                rel = token[len(root):].lstrip("/\\").replace("\\", "/")
                return f"{label}:{rel}" + tail if rel else f"the {label} folder" + tail
        name = re.split(r"[/\\]", token.rstrip("/\\"))[-1]
        return (name or "a folder") + tail

    return _PATH_TOKEN.sub(one, text or "")


# --- registration ---------------------------------------------------------------------------------------------------

def register_runtime_tools(registry: ToolRegistry, framework: Any, *, service: Any, jobs: Any) -> ToolRegistry:
    """Registers the local runtime, benchmark and device runtime tools whose packages exist."""

    def runtime():
        from orchestrator.runtime.llama_server import LocalLLMRuntime
        return LocalLLMRuntime.from_framework(framework)

    def state_dir() -> Path:
        return Path(framework.config.user_state_dir)

    def offline() -> bool:
        try:
            return bool(framework.config.get("offline_mode", False))
        except Exception:  # noqa: BLE001
            return False

    def catalog():
        from orchestrator.runtime.catalog import load_catalog
        return load_catalog()

    def tiers(_args=None) -> Optional[Choices]:
        try:
            return Choices(tuple(m.tier for m in catalog().models), where="in the model catalog")
        except Exception:  # noqa: BLE001
            return None

    def model_of(tier: str):
        try:
            return catalog().model(tier)
        except KeyError:
            raise ToolError(f"There is no model tier '{tier}'.", code="invalid_value", field="tier",
                            next="get_status (local_runtime) and the lcf://runtime/catalog resource list the tiers.") \
                from None

    def running(kind: str, ctx: ToolContext) -> int:
        return sum(1 for j in jobs.list() if j.kind == kind and not j.finished)

    def folder_labels() -> Dict[str, str]:
        """The framework's own folders by label (state, cache, output, models), as an AI may name them."""
        roots: Dict[str, str] = {}
        try:
            roots[str(state_dir())] = "state"
        except Exception:  # noqa: BLE001
            pass
        resolve = getattr(framework, "resolve_path", None)
        for key, label in (("cache_dir", "cache"), ("output_dir", "output"), ("models_dir", "models")):
            try:
                value = framework.config.get(key, None)
                if value:
                    roots[str(resolve(value) if callable(resolve) else Path(value))] = label
            except Exception:  # noqa: BLE001
                continue
        return roots

    def scrub(text: str) -> str:
        """An error of a local or board runtime tool as an AI may see it: the framework's folders by label, any other
        path by its file name only (no host path, no '~/...'), secrets redacted, cleaned and capped."""
        from orchestrator.tools.tools_status import _scrub_paths
        return _scrub_paths(label_paths(str(text), folder_labels()))

    def job_error(e: Exception) -> ValueError:
        code = getattr(e, "code", "failed")
        nxt = getattr(e, "next", "")
        err = ValueError(scrub(f"{e} Next: {nxt}" if nxt else str(e)))
        err.code = code  # type: ignore[attr-defined]
        # A device's or llama-bench's own words: returned by get_job_status as external text, never in the message.
        err.external_text = [scrub(line) for line in getattr(e, "external_text", None) or []]  # type: ignore
        return err

    specs: List[ToolSpec] = []
    tier_field = FieldMeta(kind="choice", choices_from=tiers)

    # ================================================================ this computer: build, start, stop

    if PACKAGES["local_runtime"]():

        def build_facts(inp: BuildRuntimeIn, ctx: ToolContext) -> CallFacts:
            from orchestrator.Core.hf_hub import disk_check
            from orchestrator.runtime.build.source import cached_source
            from orchestrator.runtime.llama_server import LocalRuntimeError
            rt = runtime()
            try:
                plan = rt.plan_install(backend=inp.backend, placement="auto")
            except LocalRuntimeError as e:
                raise ToolError(scrub(f"llama.cpp cannot be built on this computer yet: {e}"), code="needs_person",
                                next=INSTALL_NEXT) from None
            if plan.cpu.refusal:
                raise ToolError(scrub(f"The CPU build cannot be built here yet: {plan.cpu.refusal}"),
                                code="needs_person", next=INSTALL_NEXT)
            gpu = plan.gpu
            if inp.backend not in ("auto", "cpu") and (gpu is None or gpu.refusal):
                why = gpu.refusal if gpu is not None else "; ".join(plan.skipped) or "it cannot be planned here"
                raise ToolError(scrub(f"The {inp.backend} build cannot be built here yet: {why}"), code="needs_person",
                                next=INSTALL_NEXT)
            pin = rt.catalog.llama_cpp.source
            cached = pin is not None and cached_source(pin, rt.data_dir) is not None
            model = model_of(inp.model_tier) if inp.model_tier else None
            if model is not None and not model.pinned:
                raise ToolError(f"The catalog model {model.tier} has no pinned size and sha256.", code="not_supported",
                                field="model_tier", next="Choose another tier.")
            fetch_model = model is not None and not rt.model_ready(model)
            if offline() and (fetch_model or (plan.needs_source and not cached)):
                raise ToolError("Offline mode is on, and this needs a download (the pinned source or the model).",
                                code="needs_person", next="Ask the person to switch offline mode off, or to import "
                                "the source with 'llm-cli ditto runtime import-source'.")
            to_build = [b for b in plan.builds if b.to_build]
            needed = (SOURCE_SIZE if plan.needs_source and not cached else 0) + BUILD_SIZE * len(to_build) + \
                (int(model.size or 0) if fetch_model else 0)
            disk = disk_check(rt.data_dir, needed)
            if not disk["fits"]:
                raise ToolError(f"Not enough disk space: about {_size(needed)} needed (estimate), "
                                f"{_size(disk['free'])} free, {_size(disk['reserve'])} kept free as a reserve.",
                                code="insufficient_resources", next="Tell the person how much space is needed.")
            facts = [Fact("Plan", line) for line in plan.describe()]
            facts += [Fact("Note", scrub(n)) for n in plan.notes[:5]]
            facts.append(Fact("Source", "the pinned source is cached" if cached or not plan.needs_source else
                              f"fetches the pinned llama.cpp source {pin.tag if pin else ''} (about 215 MB) from "
                              "GitHub, checked against the pinned commit"))
            if model is not None:
                facts.append(Fact("Model", f"catalog tier {model.tier}: {model.file}, {_size(model.size)}, pinned by "
                                           "sha256" + ("; already downloaded" if not fetch_model else
                                                       "; downloaded from huggingface.co")))
            facts.append(Fact("Disk", f"about {_size(needed)} needed (estimate), {_size(disk['free'])} free"))
            facts.append(Fact("Origin", "self-built from the pinned commit; never a prebuilt archive"))
            what = (f"Builds llama.cpp's server for this computer from the pinned commit "
                    f"{pin.tag + ' (' + pin.commit[:12] + ')' if pin else ''}: "
                    + ", ".join(f"{b.backend} build" for b in to_build) if to_build else
                    "Reuses the builds already made for this computer from the pinned commit")
            consequence = what + (" (a CPU build takes a few minutes, a GPU build longer)" if to_build else "") + \
                (f", and downloads the catalog model {model.tier} ({_size(model.size)})" if fetch_model else "") + "."
            bound = {"backend": inp.backend, "placement": plan.placement,
                     "builds": [b.plan.build_id for b in to_build], "model_tier": inp.model_tier or "",
                     "model_sha256": (model.sha256 or "") if model is not None else ""}
            return CallFacts(consequence=consequence, facts=tuple(facts), bound=bound)

        def build_local_runtime(inp: BuildRuntimeIn, ctx: ToolContext) -> JobStartedOut:
            bound = dict(ctx.facts.bound) if ctx.facts is not None else {}
            if bound.get("backend") != inp.backend or "builds" not in bound:
                raise ToolError("The build is not bound to what the person was shown; nothing was built.",
                                code="failed", next="Call build_local_runtime again through the framework's registry.")
            stop = threading.Event()

            def work(handle) -> Dict[str, Any]:
                from orchestrator.Core.job_service import JobCancelled
                from orchestrator.runtime.llama_server import LocalRuntimeError
                from orchestrator.utils.download import DownloadStopped
                rt = runtime()
                handle.progress(None, None, "planning the build")
                try:
                    plan = rt.plan_install(backend=inp.backend, placement=bound["placement"])
                except LocalRuntimeError as e:
                    raise job_error(e) from None
                if [b.plan.build_id for b in plan.builds if b.to_build] != list(bound["builds"]):
                    raise ValueError("The build plan changed after the person was asked (the toolchain, the probe or "
                                     "the pin changed); nothing was built. Next: call build_local_runtime again.")
                cancelled = lambda: stop.is_set() or handle.cancelled  # noqa: E731
                try:
                    result = rt.install(plan, fetch_source=lambda: True, cancelled=cancelled,
                                        log=lambda msg: handle.progress(None, None, scrub(msg)[:200]))
                except LocalRuntimeError as e:
                    if cancelled():
                        raise JobCancelled() from None
                    raise job_error(e) from None
                if cancelled():
                    raise JobCancelled()
                out: Dict[str, Any] = {"build_id": result.active.build_id, "backend": result.active.backend,
                                       "verified": True, "origin": "self-built", "commit": result.active.commit,
                                       "fallback_build_id": result.fallback.build_id,
                                       "problems": [scrub(p) for p in result.problems][:10]}
                if bound.get("model_tier"):
                    model = rt.catalog.model(bound["model_tier"])
                    if (model.sha256 or "") != bound.get("model_sha256"):
                        raise ValueError("The catalog model changed after the person was asked; it was not "
                                         "downloaded.")
                    try:
                        rt.download_model(model.tier, stop=stop,
                                          progress=lambda name, done, total: handle.progress(done, total,
                                                                                             f"model {model.tier}"))
                    except DownloadStopped:
                        raise JobCancelled() from None
                    except LocalRuntimeError as e:
                        raise job_error(e) from None
                    out["model_file"] = f"models:ditto/{model.file}"
                    out["model_tier"] = model.tier
                return out

            job = jobs.start("runtime_build", ctx.owner, work, on_cancel=stop.set)
            ctx.notes.append("Follow the build with get_job_status (wait_seconds=50); a CPU build takes minutes.")
            return JobStartedOut(job_id=job.job_id, kind=job.kind, state=job.state)

        def start_facts(inp: StartRuntimeIn, ctx: ToolContext) -> CallFacts:
            rt = runtime()
            installed, problems = rt.resolve_installed()
            if installed is None:
                raise ToolError("Ditto's local server is not built on this computer" +
                                (f": {scrub('; '.join(problems))}" if problems else "."), code="needs_person",
                                next=BUILD_NEXT)
            try:
                model, why = rt.resolve_model(inp.tier)
            except Exception as e:  # noqa: BLE001 - LocalRuntimeError: an unknown tier
                raise ToolError(scrub(str(e)), code="invalid_value", field="tier",
                                next="The lcf://runtime/catalog resource lists the tiers.") from None
            if not rt.model_path(model).is_file():
                raise ToolError(f"The model of tier {model.tier} is not downloaded.", code="missing_information",
                                field="tier", next=f"Fetch it with build_local_runtime(model_tier='{model.tier}').")
            origin = installed.label
            facts = (Fact("Build", f"{installed.name} ({installed.backend}), {origin}"),
                     Fact("Model", f"tier {model.tier}: {model.file} ({why})"),
                     Fact("Server", "on 127.0.0.1 only, with a new key for every start (never shown to an AI)"))
            consequence = f"Starts Ditto's local model server on this computer with the model {model.tier}."
            return CallFacts(consequence=consequence, facts=facts,
                             bound={"tier": model.tier, "build": installed.build_id or installed.asset_id or ""})

        def start_local_runtime(inp: StartRuntimeIn, ctx: ToolContext) -> JobStartedOut:
            bound = dict(ctx.facts.bound) if ctx.facts is not None else {}
            if not bound.get("tier"):
                raise ToolError("The start is not bound to what the person was shown; nothing was started.",
                                code="failed", next="Call start_local_runtime again through the framework's registry.")

            def work(handle) -> Dict[str, Any]:
                rt = runtime()
                installed = rt.installed()
                if installed is None or (installed.build_id or installed.asset_id or "") != bound["build"]:
                    raise ValueError("The build in use changed after the person was asked; nothing was started.")
                handle.progress(None, None, f"starting the server with {bound['tier']}")
                try:
                    rt.start(tier=bound["tier"], ctx=inp.context_length, remember=False)
                except Exception as e:  # noqa: BLE001 - LocalRuntimeError with the log excerpt left out
                    raise job_error(e) from None
                status = rt.status()
                inst = status.installed
                # The server's address and its per-start key are never part of a result.
                return {"running": status.running, "healthy": status.healthy, "model": status.model,
                        "tier": status.tier, "ctx": status.ctx,
                        "origin": inst.label if inst is not None else None,
                        "build_id": inst.build_id if inst is not None else None}

            job = jobs.start("runtime_start", ctx.owner, work)
            return JobStartedOut(job_id=job.job_id, kind=job.kind, state=job.state)

        def stop_local_runtime(_inp: NoInput) -> StopOut:
            try:
                return StopOut(stopped_processes=int(runtime().stop()))
            except Exception as e:  # noqa: BLE001 - LocalRuntimeError: another process holds the lock
                raise ToolError(scrub(str(e)), code="failed", next="Try again in a moment.") from None

        local_grant = lambda name, warning: GrantRule(  # noqa: E731
            rule=_tool_rule(name), facts=lambda i, c: {}, warning=warning)
        specs += [
            ToolSpec("build_local_runtime", "Build the local runtime",
                     "Builds llama.cpp's server (llama-server) for this computer from the pinned commit, tuned by "
                     "the hardware probe: the CPU build first, then the GPU build; optionally downloads the catalog "
                     "model of a tier. Always self-built, never a prebuilt archive. A job: poll get_job_status.",
                     ToolTier.LOCAL_WRITE, BuildRuntimeIn, JobStartedOut, build_local_runtime, takes_context=True,
                     idempotent=True, open_world=True, default_decision=Decision.DENY, max_decision=Decision.ALLOW,
                     prepare=build_facts,
                     fields={"backend": FieldMeta(kind="choice", choices=BACKENDS), "model_tier": tier_field},
                     examples=({}, {"backend": "cpu", "model_tier": "small"}),
                     limits=ToolLimits(concurrent=1, running=lambda ctx: running("runtime_build", ctx), per_day=10,
                                       what="runtime builds"),
                     grant=local_grant("build_local_runtime", "build llama.cpp's server for this computer from the "
                                                              "pinned commit"),
                     summary=lambda o: f"Runtime build job {fact(o.job_id)} is {fact(o.state)}."),
            ToolSpec("start_local_runtime", "Start the local runtime",
                     "Starts Ditto's local model server (the self-built llama-server) on 127.0.0.1 with a catalog "
                     "model tier. Only a build verified on this computer is started. A job: poll get_job_status.",
                     ToolTier.LOCAL_WRITE, StartRuntimeIn, JobStartedOut, start_local_runtime, takes_context=True,
                     idempotent=True, default_decision=Decision.DENY, max_decision=Decision.ALLOW,
                     prepare=start_facts, fields={"tier": tier_field, "context_length": FieldMeta(kind="value")},
                     examples=({}, {"tier": "small", "context_length": 8192}),
                     limits=ToolLimits(concurrent=1, running=lambda ctx: running("runtime_start", ctx),
                                       what="runtime starts"),
                     grant=local_grant("start_local_runtime", "start Ditto's local model server"),
                     summary=lambda o: f"Runtime start job {fact(o.job_id)} is {fact(o.state)}."),
            ToolSpec("stop_local_runtime", "Stop the local runtime",
                     "Stops Ditto's local model server on this computer (and leftovers of a crash).",
                     ToolTier.LOCAL_WRITE, NoInput, StopOut, stop_local_runtime, idempotent=True,
                     default_decision=Decision.DENY, max_decision=Decision.ALLOW,
                     consequence="Stops Ditto's local model server on this computer.",
                     grant=local_grant("stop_local_runtime", "stop Ditto's local model server"),
                     summary=lambda o: f"Stopped {fact(o.stopped_processes)} server processes."),
        ]

    # ================================================================ this computer: benchmarks

    if PACKAGES["bench"]() and PACKAGES["local_runtime"]():

        hashes: Dict[tuple, str] = {}

        def model_sha(path: Path) -> str:
            from orchestrator.runtime.bench import sha256_file
            st = path.stat()
            key = (str(path), st.st_size, st.st_mtime_ns)
            if key not in hashes:
                if len(hashes) > 32:
                    hashes.clear()
                hashes[key] = sha256_file(path)
            return hashes[key]

        def bench_model(inp: BenchmarkIn):
            """(path, label, sha256, trusted) of the model to measure."""
            from orchestrator.runtime.bench import BenchRunner
            runner = BenchRunner(state_dir())
            if inp.kind == "tier_speed":
                if not inp.tier or inp.artifact_id:
                    raise ToolError("tier_speed takes a tier (and no artifact_id).", code="missing_argument",
                                    field="tier", next="Give the tier, e.g. {\"kind\": \"tier_speed\", \"tier\": "
                                                       "\"small\"}.")
                model = model_of(inp.tier)
                rt = runtime()
                path = rt.model_path(model)
                if not rt.model_ready(model):
                    raise ToolError(f"The model of tier {model.tier} is not downloaded and checked.",
                                    code="missing_information", field="tier",
                                    next=f"Fetch it with build_local_runtime(model_tier='{model.tier}').")
                return path, f"tier {model.tier} ({model.file})", model.sha256, True
            if not inp.artifact_id or inp.tier:
                raise ToolError("artifact_speed takes an artifact_id (and no tier).", code="missing_argument",
                                field="artifact_id", next="Call list_artifacts for the ids.")
            from orchestrator.Core.deployment_manager import resolve_model_artifact
            try:
                _aid, label, info = service._artifact(inp.artifact_id)
                path = resolve_model_artifact(Path(info["path"]))
            except ToolError:
                raise
            except Exception as e:  # noqa: BLE001 - unknown id, no model file
                raise ToolError(scrub(str(e)), code=getattr(e, "code", "unknown_id"), field="artifact_id",
                                next="Call list_artifacts for the ids you can use.") from None
            if path.suffix.lower() != ".gguf":
                raise ToolError("Only GGUF models can be benchmarked with llama-bench.", code="not_supported",
                                field="artifact_id", next="Choose a GGUF build (list_artifacts, format gguf).")
            digest = model_sha(path)
            return path, label, digest, digest in runner.trusted_sha256()

        def bench_facts(inp: BenchmarkIn, ctx: ToolContext) -> CallFacts:
            from orchestrator.runtime.bench import BenchError, BenchRunner
            path, label, digest, trusted = bench_model(inp)
            try:
                build, _binary = BenchRunner(state_dir()).bench_build(sandbox=inp.sandbox and not trusted)
            except BenchError as e:
                raise ToolError(str(e), code=e.code, next=e.next) from None
            native = trusted or not inp.sandbox
            facts = [Fact("Model", f"{label}, sha256 {digest}"),
                     Fact("Trust", "a catalog model pinned by sha256: trusted" if trusted else
                          "not a catalog model: UNTRUSTED (a user conversion or a download)"),
                     Fact("Program", f"llama-bench of the self-built build {build.build_id[:16]} ({build.backend})"),
                     Fact("Where", "natively on this computer" if native else
                          "in the Docker sandbox (no network, no capabilities, read-only)")]
            if native and not trusted:
                facts.append(Fact("Your consent", "llama-bench loads this untrusted model natively, with your user's "
                                                  "rights and not in a sandbox. Approving this is your consent, and "
                                                  "it is written to the audit log before the model is loaded."))
            consequence = (f"Measures the speed of {label} with llama-bench on this computer "
                           + ("natively" if native else "in the Docker sandbox")
                           + (" (an UNTRUSTED model, run natively)" if native and not trusted else "") + ".")
            bound = {"sha256": digest, "trusted": trusted, "sandbox": bool(inp.sandbox and not trusted),
                     "label": label, "build_id": build.build_id,
                     # a standing approval never covers an untrusted model run natively (runtime decision D23)
                     "grant": {} if trusted or inp.sandbox else None}
            return CallFacts(consequence=consequence, facts=tuple(facts), bound=bound,
                             cap=None if trusted or inp.sandbox else Decision.ASK.value)

        def run_benchmark(inp: BenchmarkIn, ctx: ToolContext) -> JobStartedOut:
            from orchestrator.runtime.bench import AUDIT_TOOL
            bound = dict(ctx.facts.bound) if ctx.facts is not None else {}
            if not bound.get("sha256"):
                raise ToolError("The benchmark is not bound to what the person was shown; nothing ran.",
                                code="failed", next="Call run_benchmark again through the framework's registry.")
            path, label, digest, trusted = bench_model(inp)
            if digest != bound["sha256"] or trusted != bound["trusted"]:
                raise ToolError("The model changed after the person was asked (its sha256 differs); nothing ran.",
                                code="failed", next="Call run_benchmark again.")
            if not trusted and not bound["sandbox"]:
                # Runtime decision D23: the consent is on record before an untrusted model is loaded natively.
                audit = getattr(registry, "audit", None)
                if audit is None:
                    raise ToolError("The consent to run an untrusted model natively cannot be written to the audit "
                                    "log; nothing ran.", code="failed", next="Use sandbox=true.")
                try:
                    audit.record(ctx.actor or "unknown", AUDIT_TOOL, ToolTier.LOCAL_WRITE.value, "ask", "ok",
                                 arguments={"model_sha256": digest, "build_id": bound.get("build_id"),
                                            "runs": "natively, not in a sandbox"}, approved=True,
                                 detail="benchmark of an untrusted model: the person approved the native run")
                except Exception:  # noqa: BLE001 - without the record there is no consent
                    raise ToolError("The consent could not be written to the audit log; nothing ran.", code="failed",
                                    next="Try again, or use sandbox=true.") from None
            stop = threading.Event()

            def work(handle) -> Dict[str, Any]:
                from orchestrator.Core.job_service import JobCancelled
                from orchestrator.runtime.bench import BenchError, BenchRunner, BenchStopped
                handle.progress(None, None, f"measuring {label}")
                try:
                    run = BenchRunner(state_dir()).run(path, kind=inp.kind, label=label, trusted=trusted,
                                                       sha256=digest, sandbox=bound["sandbox"], stop=stop)
                except BenchStopped:
                    raise JobCancelled() from None
                except BenchError as e:
                    raise job_error(e) from None
                return {"run_id": run.run_id, "kind": run.kind, "where": run.where, "trusted": run.trusted,
                        "build_id": run.build_id, "backend": run.backend, "prompt_ts": run.prompt_ts,
                        "gen_ts": run.gen_ts, "peak_rss_mib": run.peak_rss_mib, "duration_s": run.duration_s}

            job = jobs.start("benchmark", ctx.owner, work, on_cancel=stop.set)
            return JobStartedOut(job_id=job.job_id, kind=job.kind, state=job.state)

        def artifact_name(value) -> Optional[str]:
            """The built model's label for the person's question (speaking names), never its folder."""
            try:
                return service._artifact(str(value))[1]
            except Exception:  # noqa: BLE001 - an unknown id: the question shows the id
                return None

        specs.append(ToolSpec(
            "run_benchmark", "Benchmark a model",
            "Measures prompt and generation speed (tokens per second) and peak memory with the self-built "
            "llama-bench on this computer: a catalog tier model (tier_speed) or a built GGUF model "
            "(artifact_speed). A model that is not a catalog model is untrusted: it runs natively only after the "
            "person's consent in the framework, or in the Docker sandbox with sandbox=true. A job.",
            ToolTier.LOCAL_WRITE, BenchmarkIn, JobStartedOut, run_benchmark, takes_context=True,
            default_decision=Decision.DENY, max_decision=Decision.ALLOW, prepare=bench_facts,
            # the person's consent to load an untrusted model natively is given in the framework's own window only
            channels=("framework",),
            fields={"kind": FieldMeta(kind="choice", choices=("tier_speed", "artifact_speed")), "tier": tier_field,
                    "artifact_id": FieldMeta(kind="id", list_tool="list_artifacts", describe=artifact_name),
                    "sandbox": FieldMeta(kind="trust")},
            examples=({"kind": "tier_speed", "tier": "small"},
                      {"kind": "artifact_speed", "artifact_id": "a_0123456789abcdef", "sandbox": True}),
            limits=ToolLimits(concurrent=1, running=lambda ctx: running("benchmark", ctx), per_day=20,
                              what="benchmarks"),
            grant=GrantRule(rule=_tool_rule("run_benchmark"),
                            facts=lambda i, c: (getattr(getattr(c, "facts", None), "bound", None) or {}).get("grant"),
                            warning="benchmark catalog models, and other models only in the Docker sandbox"),
            summary=lambda o: f"Benchmark job {fact(o.job_id)} is {fact(o.state)}."))

    # ================================================================ a board

    if PACKAGES["device_runtime"]():
        from orchestrator.runtime.device.trusted import TrustedDevices

        def trusted(device_id: str):
            store = TrustedDevices.for_framework(framework)
            device = store.get(device_id)
            if device is None:
                raise ToolError(f"Unknown device '{device_id}': only devices a person trusted and paired are known.",
                                code="unknown_id", field="device_id",
                                next="Call list_devices for the ids you can use.")
            if not device.paired or store.key_status(device) != "known":
                raise ToolError(f"The device {device.label} cannot be used now (not paired, or its trusted host key "
                                "is no longer in the framework's known_hosts).", code="needs_person",
                                field="device_id", next="Ask the person to trust and pair it again in the GUI or "
                                                        "the CLI.")
            return device

        def svc_error(e: Exception) -> ToolError:
            return ToolError(scrub(str(e)), code=getattr(e, "code", "failed"), next=getattr(e, "next", ""))

        def device_lines(device, fp: str) -> List[Fact]:
            return [Fact("Device", f"{device.label}: {device.user}@{device.address}"),
                    Fact("Host key", f"{device.key_type} {fp}, the key you trusted; read from the device just now")]

        def session_hooks():
            holder: Dict[str, Any] = {}
            stop = threading.Event()

            def on_client(client):
                holder["client"] = client
                if stop.is_set():
                    client.close()

            def cancel():
                stop.set()
                client = holder.get("client")
                if client is not None:
                    try:
                        client.close()
                    except Exception:  # noqa: BLE001
                        pass
            return on_client, cancel, stop

        def probe_facts(inp: DeviceIn, ctx: ToolContext) -> CallFacts:
            from orchestrator.runtime.device import service as dev
            device = trusted(inp.device_id)
            try:
                fp = dev.read_key(device, state_dir())
            except dev.DeviceRuntimeError as e:
                raise svc_error(e) from None
            facts = device_lines(device, fp) + [Fact("On the device", "the framework's read-only hardware probe runs "
                                                                      f"as {device.user}; nothing is installed")]
            return CallFacts(consequence=f"Runs the hardware probe on the device {device.label} over SSH and stores "
                                         "the result under its device id.", facts=tuple(facts),
                             bound={"device_id": device.device_id, "fingerprint": fp}, cap=Decision.ASK.value)

        def probe_remote_device(inp: DeviceIn, ctx: ToolContext) -> JobStartedOut:
            from orchestrator.runtime.device import service as dev
            bound = dict(ctx.facts.bound) if ctx.facts is not None else {}
            if bound.get("device_id") != inp.device_id or not bound.get("fingerprint"):
                raise ToolError("The probe is not bound to what the person was shown; nothing ran.", code="failed",
                                next="Call probe_remote_device again through the framework's registry.")
            device = trusted(inp.device_id)
            on_client, cancel, stop = session_hooks()

            def work(handle) -> Dict[str, Any]:
                from orchestrator.Core.job_service import JobCancelled
                handle.progress(None, None, f"probing {device.label}")
                try:
                    out = dev.probe(device, state_dir(), bound["fingerprint"], on_client=on_client)
                except dev.DeviceRuntimeError as e:
                    if stop.is_set():
                        raise JobCancelled() from None
                    raise job_error(e) from None
                return {**out, "next": f"Read the values with get_hardware_probe(probe_id='{device.device_id}')."}

            job = jobs.start("probe", ctx.owner, work, on_cancel=cancel)
            return JobStartedOut(job_id=job.job_id, kind=job.kind, state=job.state)

        def deploy_facts(inp: DeviceIn, ctx: ToolContext) -> CallFacts:
            from orchestrator.runtime.device import service as dev
            device = trusted(inp.device_id)
            try:
                profile = dev.device_profile(state_dir(), device)
                settings = dev.deploy_settings(state_dir(), device, profile)
                plan = dev.plan_facts(state_dir(), profile)
                if offline() and not plan.built and not plan.source_cached:
                    raise ToolError("Offline mode is on, and the build needs the pinned source.", code="needs_person",
                                    next="Ask the person to switch offline mode off or import the source.")
                fp = dev.read_key(device, state_dir())
            except dev.DeviceRuntimeError as e:
                raise svc_error(e) from None
            target = plan.target
            facts = device_lines(device, fp) + [
                Fact("Runtime", f"llama.cpp for {target.get('os')}/{target.get('arch')} ({target.get('libc')} "
                                f"{target.get('libc_version')}), build {plan.build_id[:16]}: "
                                + ("already built" if plan.built else
                                   "built first in the builder container "
                                   f"{plan.builder.get('base')} (Docker, no network during the build)"
                                   + ("" if plan.source_cached else "; fetches the pinned source (about 215 MB)"))),
                Fact("Install", f"as {settings.ssh_user} into ~/.local/share/llm-framework/llama.cpp"
                                + (f", and the systemd unit with sudo -n (you confirmed it on "
                                   f"{settings.sudo_confirmed_at})" if settings.install == "systemd" else
                                   " with the start script (127.0.0.1 only)")),
                Fact("Checks", "the new build's programs are checked on the device before it becomes active; a "
                               "build that fails is removed and the previous one stays active")]
            consequence = (f"Installs the self-built llama.cpp runtime on the device {device.label} "
                           f"({settings.ssh_user}@{device.address})"
                           + ("" if plan.built else ", building it first on this computer") + ".")
            return CallFacts(consequence=consequence, facts=tuple(facts), cap=Decision.ASK.value,
                             bound={"device_id": device.device_id, "fingerprint": fp, "build_id": plan.build_id,
                                    "user": settings.ssh_user, "install": settings.install})

        def deploy_device_runtime(inp: DeviceIn, ctx: ToolContext) -> JobStartedOut:
            from orchestrator.runtime.device import service as dev
            bound = dict(ctx.facts.bound) if ctx.facts is not None else {}
            if bound.get("device_id") != inp.device_id or not bound.get("build_id"):
                raise ToolError("The deploy is not bound to what the person was shown; nothing was done.",
                                code="failed", next="Call deploy_device_runtime again through the framework's "
                                                    "registry.")
            device = trusted(inp.device_id)
            on_client, cancel, stop = session_hooks()

            def work(handle) -> Dict[str, Any]:
                from orchestrator.Core.job_service import EXTERNAL_KEY, JobCancelled
                try:
                    profile = dev.device_profile(state_dir(), device)
                    settings = dev.deploy_settings(state_dir(), device, profile)
                    if (settings.ssh_user, settings.install) != (bound["user"], bound["install"]):
                        raise ValueError("The deploy settings changed after the person was asked; nothing was done.")
                    out = dev.deploy(state_dir(), device, profile, settings, bound["fingerprint"], bound["build_id"],
                                     on_client=on_client, cancelled=lambda: stop.is_set() or handle.cancelled,
                                     say=lambda msg: handle.progress(None, None, scrub(msg)[:200]))
                except dev.DeviceRuntimeError as e:
                    if stop.is_set() or handle.cancelled:
                        raise JobCancelled() from None
                    raise job_error(e) from None
                checks = out.pop("checks")
                notes = out.pop("notes")
                lines = [f"{c['name']}: {c['result']} - {c['detail']}" for c in checks] + list(notes)
                return {**out, "checks_passed": sum(1 for c in checks if c["result"] == "passed"),
                        "checks_failed": sum(1 for c in checks if c["result"] == "failed"), EXTERNAL_KEY: lines}

            job = jobs.start("deploy", ctx.owner, work, on_cancel=cancel)
            return JobStartedOut(job_id=job.job_id, kind=job.kind, state=job.state)

        def bench_device_facts(inp: DeviceIn, ctx: ToolContext) -> CallFacts:
            from orchestrator.runtime.device import service as dev
            from orchestrator.runtime.device.package import DeviceRuntimeStore
            device = trusted(inp.device_id)
            try:
                profile = dev.device_profile(state_dir(), device)
                settings = dev.deploy_settings(state_dir(), device, profile)
                if not DeviceRuntimeStore(state_dir(), device.device_id).record():
                    raise ToolError(f"No runtime is deployed to {device.label} yet.", code="missing_information",
                                    next="Deploy it first with deploy_device_runtime.")
                if settings.model is None:
                    raise ToolError(f"No model on {device.label} is named for the benchmark.", code="needs_person",
                                    next="Ask the person to name one with 'llm-cli device settings DEVICE --model "
                                         "PATH'.")
                fp = dev.read_key(device, state_dir())
            except dev.DeviceRuntimeError as e:
                raise svc_error(e) from None
            facts = device_lines(device, fp) + [
                Fact("Model", f"{settings.model} on the device (from its deploy settings)"),
                Fact("On the device", f"the checks of the deployed runtime and a short llama-bench run, as "
                                      f"{settings.ssh_user}, never with sudo"),
                Fact("Your consent", "if that file is not a catalog model (checked by its sha256 on the device), it is "
                                     "untrusted: approving this lets llama-bench load it natively on the device, not "
                                     "in a sandbox, and your consent is written to the audit log first")]
            return CallFacts(consequence=f"Runs the runtime checks and a short benchmark on the device {device.label} "
                                         f"with the model {settings.model}.", facts=tuple(facts),
                             cap=Decision.ASK.value,
                             bound={"device_id": device.device_id, "fingerprint": fp, "user": settings.ssh_user,
                                    "model": settings.model})

        def run_device_benchmark(inp: DeviceIn, ctx: ToolContext) -> JobStartedOut:
            from orchestrator.runtime.device import service as dev
            bound = dict(ctx.facts.bound) if ctx.facts is not None else {}
            if bound.get("device_id") != inp.device_id or not bound.get("model"):
                raise ToolError("The benchmark is not bound to what the person was shown; nothing ran.",
                                code="failed", next="Call run_device_benchmark again through the framework's "
                                                    "registry.")
            device = trusted(inp.device_id)
            on_client, cancel, stop = session_hooks()
            audit = getattr(registry, "audit", None)
            actor = ctx.actor or "unknown"

            def consent(digest: str) -> None:
                if audit is None:
                    raise dev.DeviceRuntimeError("The consent cannot be written to the audit log; nothing was loaded.")
                try:
                    audit.record(actor, "device_runtime_untrusted_model", ToolTier.EXTERNAL.value, "ask", "ok",
                                 arguments={"device": device.device_id, "model_sha256": digest}, approved=True,
                                 detail="an untrusted model may run natively on the device for the short llama-bench "
                                        "run; the person approved it")
                except Exception:  # noqa: BLE001
                    raise dev.DeviceRuntimeError("The consent could not be written to the audit log; nothing was "
                                                 "loaded.") from None

            def work(handle) -> Dict[str, Any]:
                from orchestrator.Core.job_service import EXTERNAL_KEY, JobCancelled
                try:
                    profile = dev.device_profile(state_dir(), device)
                    settings = dev.deploy_settings(state_dir(), device, profile)
                    if (settings.ssh_user, settings.model) != (bound["user"], bound["model"]):
                        raise ValueError("The deploy settings changed after the person was asked; nothing ran.")
                    handle.progress(None, None, f"checking the runtime on {device.label}")
                    out = dev.benchmark(state_dir(), device, profile, settings, bound["fingerprint"],
                                        allow_untrusted=True, on_client=on_client, consent=consent)
                except dev.DeviceRuntimeError as e:
                    if stop.is_set():
                        raise JobCancelled() from None
                    raise job_error(e) from None
                checks = out.pop("checks")
                return {**out, EXTERNAL_KEY: [f"{c['name']}: {c['result']} - {c['detail']}" for c in checks]}

            job = jobs.start("benchmark", ctx.owner, work, on_cancel=cancel)
            return JobStartedOut(job_id=job.job_id, kind=job.kind, state=job.state)

        def device_name(value) -> Optional[str]:
            try:
                d = TrustedDevices.for_framework(framework).get(str(value))
                return f"{d.label} ({d.user}@{d.address})" if d else None
            except Exception:  # noqa: BLE001
                return None

        device_fields = {"device_id": FieldMeta(kind="id", list_tool="list_devices", describe=device_name)}
        device_example = ({"device_id": "0123456789abcdef"},)
        external = dict(takes_context=True, open_world=True, default_decision=Decision.DENY,
                        max_decision=Decision.ASK, fields=device_fields, examples=device_example)
        specs += [
            ToolSpec("probe_remote_device", "Probe a board",
                     "Runs the framework's read-only hardware probe on a trusted, paired board over SSH and stores it "
                     "under the device id (read it with get_hardware_probe). The person confirms it. A job.",
                     ToolTier.EXTERNAL, DeviceIn, JobStartedOut, probe_remote_device, idempotent=True,
                     prepare=probe_facts, limits=ToolLimits(per_day=20, what="board probes"),
                     summary=lambda o: f"Board probe job {fact(o.job_id)} is {fact(o.state)}.", **external),
            ToolSpec("deploy_device_runtime", "Deploy the board runtime",
                     "Installs the self-built llama.cpp runtime on a trusted, paired board as its deploy settings say "
                     "(built first in the builder container when needed); it becomes active only after its checks "
                     "pass on the board. The person confirms it. A job.",
                     ToolTier.EXTERNAL, DeviceIn, JobStartedOut, deploy_device_runtime, destructive=True,
                     prepare=deploy_facts, limits=ToolLimits(per_day=10, what="board runtime deploys"),
                     summary=lambda o: f"Board runtime job {fact(o.job_id)} is {fact(o.state)}.", **external),
            ToolSpec("run_device_benchmark", "Benchmark on a board",
                     "Runs the checks of the deployed runtime and a short llama-bench on a trusted board with the "
                     "model its deploy settings name; an untrusted model only with the person's consent. A job.",
                     ToolTier.EXTERNAL, DeviceIn, JobStartedOut, run_device_benchmark, channels=("framework",),
                     prepare=bench_device_facts, limits=ToolLimits(per_day=20, what="board benchmarks"),
                     summary=lambda o: f"Board benchmark job {fact(o.job_id)} is {fact(o.state)}.", **external),
        ]

    for spec in specs:
        registry.register(spec)

    if PACKAGES["local_runtime"]():
        def read_catalog(_params: Dict[str, str]) -> str:
            import json
            cat = catalog()
            pin = cat.llama_cpp.source
            data = {"llama_cpp": {"tag": pin.tag, "commit": pin.commit, "build": cat.llama_cpp.build} if pin else None,
                    "models": [{"tier": m.tier, "file": m.file, "repo": m.repo, "revision": m.revision,
                                "size": m.size, "sha256": m.sha256, "license": m.license,
                                "on_request": m.on_request} for m in cat.models],
                    "tier_rules": [r.model_dump(exclude_none=True) for r in cat.tier_rules]}
            return json.dumps(data, indent=2)

        registry.register_resource(ResourceSpec(
            "lcf://runtime/catalog", "runtime-catalog", "Local runtime catalog",
            "The pinned llama.cpp commit and Ditto's model tiers (file, size, sha256, licence) of the local runtime.",
            "application/json", read=read_catalog))
    return registry


def _tool_rule(name: str):
    from orchestrator.tools.grants import tool_rule
    return tool_rule(name)
