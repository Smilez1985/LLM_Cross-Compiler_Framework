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
- run_benchmark: llama-bench on a catalog tier model (Ditto's own model, natively, or in the Docker sandbox when
  `sandbox` is set) or a built GGUF artifact. A model built for a board is never benchmarked natively on this computer
  (seventh round 2': that would measure this computer, not the board): it runs in the Docker sandbox, and its real
  values come from the board itself (run_device_benchmark after deploy_to_device).

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

import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.remote.address import USER_PATTERN
# Path labelling for AIs lives in orchestrator/security/paths.py (consolidation chain c1-paths); the old names stay
# importable here.
from orchestrator.security.paths import (DEVICE_ACCOUNT, _undouble, device_text, home_folders,  # noqa: F401
                                         label_paths, replace_folder, scrub_for_ai, shorten_home)
from orchestrator.tools.facts_catalog import BLOCKERS, blocked, warn, with_blocker
from orchestrator.tools.job_audit import audited_job
from orchestrator.tools.output import ToolOutput, fact
from orchestrator.tools.policy import Decision, ToolTier
from orchestrator.tools.registry import ResourceSpec, ToolContext, ToolError, ToolRegistry, ToolSpec
from orchestrator.tools.spec import CallFacts, Choices, Fact, FieldMeta, GrantRule, ToolLimits

BACKENDS = ("auto", "cpu", "cuda", "vulkan", "rocm", "metal")
INSTALL_NEXT = ("Ask the person to run 'llm-cli ditto runtime install' in a terminal: it lists the missing parts, "
                "their sizes and licences, and only the person installs them.")
BUILD_NEXT = "Build it first with build_local_runtime (or the person runs 'llm-cli ditto runtime install')."
# Older runtime builds on a board stay there (round 5 f): an AI never removes them and never asks to.
OLDER_BUILDS_NOTE = ("The older runtime builds stay on the board; the framework removes none by itself. Only the "
                     "person removes them, from the full list at a terminal: llm-cli device runtime prune DEVICE "
                     "--on-device. Mention them to the person only if the board runs short of space.")
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
    sandbox: Optional[bool] = Field(default=None, description="tier_speed: true runs Ditto's model in the Docker "
                                                              "sandbox instead of natively. artifact_speed always runs "
                                                              "in the Docker sandbox (a built model is never "
                                                              "benchmarked natively here); false is refused. The "
                                                              "sandbox needs a build made in the builder container")


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


_ACCOUNT = USER_PATTERN   # the one account check (orchestrator/remote/address.py, ASCII only)


def device_users(device: Any, settings: Any = None) -> List[str]:
    """Every account name a device's text may contain: the trusted login and the deploy settings' account."""
    return [u for u in (getattr(device, "user", None), getattr(settings, "ssh_user", None)) if u]


def _file_name(path: Optional[str]) -> str:
    """A device path as an AI may see it: its file name only."""
    return Path(str(path or "")).name or "the model file"


# --- registration ---------------------------------------------------------------------------------------------------

def register_runtime_tools(registry: ToolRegistry, framework: Any, *, service: Any, jobs: Any) -> ToolRegistry:
    def _board_signer():
        """The framework's board signer: the tarball of an AI's deploy is signed like the person's (board_ops)."""
        from orchestrator.runtime.device import board_ops
        return board_ops.framework_signer(framework)

    """Registers the local runtime, benchmark and device runtime tools whose packages exist."""

    def runtime():
        from orchestrator.runtime.llama_server import LocalLLMRuntime
        return LocalLLMRuntime.from_framework(framework)

    def state_dir() -> Path:
        return Path(framework.config.user_state_dir)

    def offline() -> bool:
        from orchestrator.utils.locality import offline_mode   # the one offline check
        return offline_mode(getattr(framework, "config", None))

    def outside(needs) -> str:
        """Offline mode: why one of `needs` ((what, url) the call would fetch) is not on this computer or in the
        person's own network by the network-locality rule (utils/locality.py: a local mirror of the pinned source or
        of the model may be used; the person's opt-outs count); "" when none is outside or offline mode is off."""
        if not offline():
            return ""
        from orchestrator.utils.locality import check_reach
        for what, url in needs:
            verdict = check_reach(url, getattr(framework, "config", None))
            if not verdict.local:
                return verdict.refusal(what)
        return ""

    def runtime_downloads(rt, plan, model) -> list:
        """(what, url) of every download build_local_runtime's plan needs: the pinned source when it is not cached,
        the catalog model when it is not downloaded."""
        from orchestrator.runtime.build.source import cached_source
        pin = rt.catalog.llama_cpp.source
        needs = []
        if plan.needs_source and not (pin is not None and cached_source(pin, rt.data_dir) is not None):
            needs.append(("the pinned llama.cpp source", pin.repo if pin is not None else ""))
        if model is not None and not rt.model_ready(model):
            needs.append((f"the catalog model {model.tier}", model.url(rt.catalog.model_base_url)))
        return needs

    def refuse_runtime_downloads(rt, plan, model) -> None:
        why = outside(runtime_downloads(rt, plan, model))
        if why:
            raise blocked("offline_mode", "Offline mode is on, and this needs a download (the pinned source or "
                          "the model). " + why, "Ask the person to switch offline mode off, or to import the source "
                          "with 'llm-cli ditto runtime import-source'.")

    def refuse_device_source(plan) -> None:
        """deploy_device_runtime in offline mode: the board's build needs the pinned source when it is neither built
        nor cached, and it may come only from the person's own network."""
        from orchestrator.runtime.device import service as dev
        if plan.built or plan.source_cached:
            return
        why = outside([("the pinned llama.cpp source", dev.pinned_source().repo)])
        if why:
            raise blocked("offline_mode", "Offline mode is on, and the build needs the pinned source. " + why,
                          "Ask the person to switch offline mode off or import the source.")

    def sandbox_allowed() -> bool:
        """Whether the person allowed the Docker sandbox for an AI's benchmark of an untrusted model (an oversight
        setting: an AI never changes it; unreadable counts as not allowed)."""
        try:
            return framework.config.get("bench_untrusted_sandbox_ok", False) is True
        except Exception:  # noqa: BLE001
            return False

    def cache_folder() -> Optional[Path]:
        """The framework's cache folder (a sandbox benchmark's private model file may go there)."""
        try:
            value = framework.config.get("cache_dir", None)
            resolve = getattr(framework, "resolve_path", None)
            return (Path(resolve(value)) if callable(resolve) else Path(value)) if value else None
        except Exception:  # noqa: BLE001
            return None

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
        for key, label in (("cache_dir", "cache"), ("output_dir", "output"), ("build_dir", "builds"),
                           ("models_dir", "models")):
            try:
                value = framework.config.get(key, None)
                if value:
                    roots[str(resolve(value) if callable(resolve) else Path(value))] = label
            except Exception:  # noqa: BLE001
                continue
        return roots

    def output_folders() -> List[Path]:
        """Where the built models are (the output folder, the framework's build folder and the folders golden
        artifacts were saved to), for the "tested on" record of a board benchmark."""
        from orchestrator.Core.artifact_output import artifact_folders
        try:
            return artifact_folders(framework)
        except Exception:  # noqa: BLE001 - no output folder: the result stays with the board's record
            return []

    def scrub(text: str) -> str:
        """An error of a local or board runtime tool as an AI may see it: the framework's folders by label, any other
        path by its file name only (no host path, no '~/...'), secrets redacted, cleaned and capped."""
        return scrub_for_ai(str(text), folder_labels())

    def job_error(e: Exception, users: Sequence[str] = ()) -> ValueError:
        """A failed job's error as an AI may see it; `users`: the device's account names (D0), never shown."""
        clean = (lambda t: device_text(t, users, folder_labels())) if users else scrub
        code = getattr(e, "code", "failed")
        nxt = getattr(e, "next", "")
        err = ValueError(clean(f"{e} Next: {nxt}" if nxt else str(e)))
        err.code = code  # type: ignore[attr-defined]
        # A device's or llama-bench's own words: returned by get_job_status as external text, never in the message.
        err.external_text = [clean(line) for line in getattr(e, "external_text", None) or []]  # type: ignore
        return err

    def audited(name: str, inp: BaseModel, ctx: ToolContext, work: Callable[[Any], Dict[str, Any]], what: str,
                done: Optional[Callable[[Dict[str, Any]], tuple]] = None,
                cancelled: Optional[Callable[[], str]] = None) -> Callable[[Any], Dict[str, Any]]:
        """The job's work with its end in the audit log, linked to the call's started entry (design 6.7)."""
        return audited_job(registry, name, inp.model_dump(mode="json"), ctx, work, what, done=done,
                           cancelled=cancelled)

    def speed(value: Any) -> str:
        return f"{value:.1f} t/s" if isinstance(value, (int, float)) else "not measured"

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
                raise blocked("toolchain_missing", scrub(f"llama.cpp cannot be built on this computer yet: {e}"),
                              INSTALL_NEXT) from None
            if plan.cpu.refusal:
                raise blocked("toolchain_missing", scrub(f"The CPU build cannot be built here yet: {plan.cpu.refusal}"),
                              INSTALL_NEXT)
            gpu = plan.gpu
            if inp.backend not in ("auto", "cpu") and (gpu is None or gpu.refusal):
                why = gpu.refusal if gpu is not None else "; ".join(plan.skipped) or "it cannot be planned here"
                raise blocked("toolchain_missing", scrub(f"The {inp.backend} build cannot be built here yet: {why}"),
                              INSTALL_NEXT)
            pin = rt.catalog.llama_cpp.source
            cached = pin is not None and cached_source(pin, rt.data_dir) is not None
            model = model_of(inp.model_tier) if inp.model_tier else None
            if model is not None and not model.pinned:
                raise blocked("model_unpinned", f"The catalog model {model.tier} has no pinned size and sha256.",
                              "Choose another tier.", field="model_tier")
            fetch_model = model is not None and not rt.model_ready(model)
            # offline mode: only from the person's own network (a local mirror), by the network-locality rule
            refuse_runtime_downloads(rt, plan, model)
            to_build = [b for b in plan.builds if b.to_build]
            needed = (SOURCE_SIZE if plan.needs_source and not cached else 0) + BUILD_SIZE * len(to_build) + \
                (int(model.size or 0) if fetch_model else 0)
            # the one disk rule (A4): with what running builds and downloads of any process reserved there
            from orchestrator.Core.disk_plan import ReservationLedger
            disk = disk_check(rt.data_dir, needed, ReservationLedger(Path(rt.data_dir)))
            if not disk["fits"]:
                raise blocked("disk_full", f"Not enough disk space: about {_size(needed)} needed (estimate), "
                              f"{_size(disk['free'])} free, {_size(disk['reserve'])} kept free as a reserve"
                              + (f", {_size(disk['reserved'])} reserved by other calls (running builds or "
                                 "downloads)" if disk["reserved"] else "") + ".",
                              "Tell the person how much space is needed" + (
                                  "; the call fits once the running build or download has finished"
                                  if disk["reserved"] else "") + ".")
            facts = [Fact("Plan", line) for line in plan.describe()]
            facts += [Fact("Note", scrub(n)) for n in plan.notes[:5]]
            facts.append(Fact("Source", "the pinned source is cached" if cached or not plan.needs_source else
                              f"fetches the pinned llama.cpp source {pin.tag if pin else ''} (about 215 MB) from "
                              "GitHub, checked against the pinned commit"))
            if model is not None:
                facts.append(Fact("Model", f"catalog tier {model.tier}: {model.file}, {_size(model.size)}, pinned by "
                                           "sha256" + ("; already downloaded" if not fetch_model else
                                                       "; downloaded from huggingface.co")))
            facts.append(Fact("Disk", f"about {_size(needed)} needed (estimate), {_size(disk['free'])} free",
                              volatile=True))
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
            warnings = []
            if disk.get("tight"):
                warnings.append(warn("disk_tight", free_after_gb=round(disk["after"] / GB, 1),
                                     reserve_gb=round(disk["reserve"] / GB, 1)))
            if fetch_model and int(model.size or 0) >= 20 * GB:
                warnings.append(warn("download_large", download_gb=round(int(model.size) / GB, 1)))
            return CallFacts(consequence=consequence, facts=tuple(facts), bound=bound, warnings=tuple(warnings),
                             numbers={"builds": len(to_build), "needed_bytes": int(needed),
                                      "free_after_bytes": int(disk.get("after", 0))},
                             consequence_template=consequence)

        def build_local_runtime(inp: BuildRuntimeIn, ctx: ToolContext) -> JobStartedOut:
            bound = dict(ctx.facts.bound) if ctx.facts is not None else {}
            if bound.get("backend") != inp.backend or "builds" not in bound:
                raise ToolError("The build is not bound to what the person was shown; nothing was built.",
                                code="failed", next="Call build_local_runtime again through the framework's registry.")
            if offline():
                # The question refused in offline mode; the person may have switched it on while the question was
                # open: the same refusal again, before a job starts or anything is fetched (as download_hf_file).
                from orchestrator.runtime.llama_server import LocalRuntimeError
                rt = runtime()
                try:
                    plan = rt.plan_install(backend=inp.backend, placement=bound["placement"])
                except LocalRuntimeError:
                    plan = None   # the job reports why the build cannot be planned
                if plan is not None:
                    refuse_runtime_downloads(rt, plan, model_of(bound["model_tier"]) if bound.get("model_tier")
                                             else None)
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
                    # an AI started it: the soft caps of its steps stop it (DECISIONS round 25 Q8)
                    result = rt.install(plan, fetch_source=lambda: True, cancelled=cancelled, ai_started=True,
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

            def built(out: Dict[str, Any]):
                return ("ok", f"built and verified {str(out.get('build_id'))[:16]} ({out.get('backend')})"
                        + (f", catalog model {out['model_tier']} downloaded" if out.get("model_tier") else ""), "")

            job = jobs.start("runtime_build", ctx.owner,
                             audited("build_local_runtime", inp, ctx, work, f"backend {inp.backend}", done=built,
                                     cancelled=lambda: "cancelled; a half-built runtime is never activated"),
                             on_cancel=stop.set)
            ctx.notes.append("Follow the build with get_job_status (wait_seconds=50); a CPU build takes minutes.")
            return JobStartedOut(job_id=job.job_id, kind=job.kind, state=job.state)

        def in_use(rt) -> tuple:
            """(what start() starts as the build key, its description, why there is none) in either runtime mode: the
            bare-metal build, or in Docker mode Ditto's verified image (the person chose the mode; never an AI)."""
            from orchestrator.runtime.llama_server import LocalRuntimeError
            if rt.mode() == "docker":
                try:
                    image = rt.docker_image()
                except LocalRuntimeError as e:
                    return None, "", [str(e)]
                return (image.image_id, f"Ditto's image {image.name} "
                        f"({image.labels.get('org.llm-framework.ditto-backend', '?')}), Docker mode", [])
            installed, problems = rt.resolve_installed()
            if installed is None:
                return None, "", problems
            return (installed.build_id or installed.asset_id or "",
                    f"{installed.name} ({installed.backend}), {installed.label}, bare metal", [])

        def start_facts(inp: StartRuntimeIn, ctx: ToolContext) -> CallFacts:
            rt = runtime()
            key, described, problems = in_use(rt)
            if key is None:
                raise blocked("runtime_not_built", "Ditto's local server is not built on this computer" +
                              (f": {scrub('; '.join(problems))}" if problems else "."), BUILD_NEXT)
            try:
                model, why = rt.resolve_model(inp.tier)
            except Exception as e:  # noqa: BLE001 - LocalRuntimeError: an unknown tier
                raise ToolError(scrub(str(e)), code="invalid_value", field="tier",
                                next="The lcf://runtime/catalog resource lists the tiers.") from None
            if not rt.model_path(model).is_file():
                raise blocked("model_not_downloaded", f"The model of tier {model.tier} is not downloaded.",
                              f"Fetch it with build_local_runtime(model_tier='{model.tier}').", field="tier")
            facts = (Fact("Build", described),
                     Fact("Model", f"tier {model.tier}: {model.file} ({why})"),
                     Fact("Server", "on 127.0.0.1 only, with a new key for every start (never shown to an AI)"))
            consequence = f"Starts Ditto's local model server on this computer with the model {model.tier}."
            return CallFacts(consequence=consequence, facts=facts, bound={"tier": model.tier, "build": key})

        def start_local_runtime(inp: StartRuntimeIn, ctx: ToolContext) -> JobStartedOut:
            bound = dict(ctx.facts.bound) if ctx.facts is not None else {}
            if not bound.get("tier"):
                raise ToolError("The start is not bound to what the person was shown; nothing was started.",
                                code="failed", next="Call start_local_runtime again through the framework's registry.")

            def work(handle) -> Dict[str, Any]:
                rt = runtime()
                if in_use(rt)[0] != bound["build"]:
                    raise ValueError("The build in use changed after the person was asked; nothing was started.")
                handle.progress(None, None, f"starting the server with {bound['tier']}")
                try:
                    rt.start(tier=bound["tier"], ctx=inp.context_length, remember=False)
                except Exception as e:  # noqa: BLE001 - LocalRuntimeError with the log excerpt left out
                    raise job_error(e) from None
                status = rt.status()
                inst = status.installed
                origin, build_id = (inst.label, inst.build_id) if inst is not None else (None, None)
                if status.mode == "docker":  # what runs is Ditto's image, not a bare-metal build
                    image = status.docker or {}
                    origin = f"Ditto's image ({image.get('made_by') or 'made here'}), Docker mode"
                    build_id = image.get("build_id")
                # The server's address and its per-start key are never part of a result.
                return {"running": status.running, "healthy": status.healthy, "model": status.model,
                        "tier": status.tier, "ctx": status.ctx, "origin": origin, "build_id": build_id}

            def started(out: Dict[str, Any]):
                ok = bool(out.get("running"))
                return ("ok" if ok else "error", f"{'running' if ok else 'not running'}, healthy "
                        f"{bool(out.get('healthy'))}, build {str(out.get('build_id') or '')[:16]}",
                        "" if ok else "failed")

            job = jobs.start("runtime_start", ctx.owner,
                             audited("start_local_runtime", inp, ctx, work, f"model tier {bound['tier']}",
                                     done=started))
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

        from orchestrator.utils.helpers import HashCache
        hashes = HashCache(32)  # thread-safe: two benchmark questions at once share it

        def model_sha(path: Path) -> str:
            return hashes.sha256(path)

        def in_sandbox(inp: BenchmarkIn) -> bool:
            """Where the run happens: Ditto's own model natively unless the sandbox is asked for; a model built for a
            board always in the Docker sandbox, never natively on this computer (seventh round 2')."""
            if inp.kind == "tier_speed":
                return bool(inp.sandbox)
            if inp.sandbox is False:
                raise ToolError("A model built for a board is never benchmarked natively on this computer: that would "
                                "measure this computer, not the board.", code="not_supported", field="sandbox",
                                next="Benchmark it on the board (create_deploy_package, deploy_to_device, then "
                                     "run_device_benchmark), or here in the Docker sandbox: leave sandbox out or set "
                                     "it to true.")
            return True

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
                    raise blocked("model_not_downloaded", f"The model of tier {model.tier} is not downloaded and "
                                  "checked.", f"Fetch it with build_local_runtime(model_tier='{model.tier}').",
                                  field="tier")
                return path, f"tier {model.tier} ({model.file})", model.sha256, True
            if not inp.artifact_id or inp.tier:
                raise ToolError("artifact_speed takes an artifact_id (and no tier).", code="missing_argument",
                                field="artifact_id", next="Call list_artifacts for the ids.")
            from orchestrator.Core.deployment_manager import resolve_model_artifact
            try:
                _aid, label, info = service.artifact_entry(inp.artifact_id)
                path = resolve_model_artifact(Path(info["path"]))
            except ToolError:
                raise
            except Exception as e:  # noqa: BLE001 - unknown id, no model file
                raise ToolError(scrub(str(e)), code=getattr(e, "code", "unknown_id"), field="artifact_id",
                                next="Call list_artifacts for the ids you can use.") from None
            if path.suffix.lower() != ".gguf":
                raise blocked("not_gguf", "Only GGUF models can be benchmarked with llama-bench.",
                              "Choose a GGUF build (list_artifacts, format gguf).", field="artifact_id")
            digest = model_sha(path)
            return path, label, digest, digest in runner.trusted_sha256()

        def bench_facts(inp: BenchmarkIn, ctx: ToolContext) -> CallFacts:
            from orchestrator.runtime.bench import BenchError, BenchRunner
            sandbox = in_sandbox(inp)
            path, label, digest, trusted = bench_model(inp)
            try:
                build, _binary = BenchRunner(state_dir()).bench_build(sandbox=sandbox)
            except BenchError as e:
                raise ToolError(str(e), code=e.code, next=e.next) from None
            facts = [Fact("Model", f"{label}, sha256 {digest}"),
                     Fact("Trust", "a catalog model pinned by sha256: trusted" if trusted else
                          "not a catalog model: UNTRUSTED (a user conversion or a download)"),
                     Fact("Program", f"llama-bench of the self-built build {build.build_id[:16]} ({build.backend})"),
                     Fact("Where", "in the Docker sandbox (no network, no capabilities, read-only)" if sandbox else
                          "natively on this computer (Ditto's own model)")]
            if inp.kind == "artifact_speed":
                facts.append(Fact("On the board", "this measures this computer in the sandbox, not the board: the "
                                                  "real values of a model built for a board come from the board "
                                                  "(deploy it, then run_device_benchmark)"))
            consequence = (f"Measures the speed of {label} with llama-bench on this computer "
                           + ("in the Docker sandbox" if sandbox else "natively") + ".")
            # a standing approval covers catalog models natively and every other model only in the sandbox
            bound = {"sha256": digest, "trusted": trusted, "sandbox": sandbox, "label": label,
                     "build_id": build.build_id, "grant": {}}
            # an untrusted model in the Docker sandbox is the person's unless the person allowed that for AIs
            # (bench_untrusted_sandbox_ok, an oversight setting no AI can change; design 4.3, SEC-M9)
            warnings = (warn("untrusted_model_native"),) if not trusted and not sandbox_allowed() else ()
            return CallFacts(consequence=consequence, facts=tuple(facts), bound=bound, warnings=warnings,
                             consequence_template=consequence)

        def run_benchmark(inp: BenchmarkIn, ctx: ToolContext) -> JobStartedOut:
            bound = dict(ctx.facts.bound) if ctx.facts is not None else {}
            if not bound.get("sha256"):
                raise ToolError("The benchmark is not bound to what the person was shown; nothing ran.",
                                code="failed", next="Call run_benchmark again through the framework's registry.")
            path, label, digest, trusted = bench_model(inp)
            if digest != bound["sha256"] or trusted != bound["trusted"] or in_sandbox(inp) != bound["sandbox"]:
                raise ToolError("The model changed after the person was asked (its sha256 differs); nothing ran.",
                                code="failed", next="Call run_benchmark again.")
            if not trusted and not bound["sandbox"]:  # never: only catalog models run natively (seventh round 2')
                raise ToolError("Only Ditto's own catalog models are benchmarked natively; nothing ran.",
                                code="not_supported", next="Benchmark it on the board, or in the Docker sandbox.")
            stop = threading.Event()

            def work(handle) -> Dict[str, Any]:
                from orchestrator.Core.job_service import JobCancelled
                from orchestrator.runtime.bench import BenchError, BenchRunner, BenchStopped
                handle.progress(None, None, f"measuring {label}")
                try:
                    run = BenchRunner(state_dir(), cache_dir=cache_folder()).run(
                        path, kind=inp.kind, label=label, trusted=trusted, sha256=bound["sha256"],
                        sandbox=bound["sandbox"], stop=stop)
                except BenchStopped:
                    raise JobCancelled() from None
                except BenchError as e:
                    raise job_error(e) from None
                return {"run_id": run.run_id, "kind": run.kind, "where": run.where, "trusted": run.trusted,
                        "build_id": run.build_id, "backend": run.backend, "prompt_ts": run.prompt_ts,
                        "gen_ts": run.gen_ts, "peak_rss_mib": run.peak_rss_mib, "duration_s": run.duration_s}

            def measured(out: Dict[str, Any]):
                return ("ok", f"run {out.get('run_id')}: prompt {speed(out.get('prompt_ts'))}, generation "
                              f"{speed(out.get('gen_ts'))}", "")

            where = "in the Docker sandbox" if bound["sandbox"] else "natively"
            job = jobs.start("benchmark", ctx.owner,
                             audited("run_benchmark", inp, ctx, work, f"model {label} (sha256 {digest[:16]}), {where}",
                                     done=measured), on_cancel=stop.set)
            return JobStartedOut(job_id=job.job_id, kind=job.kind, state=job.state)

        def artifact_name(value) -> Optional[str]:
            """The built model's label for the person's question (speaking names), never its folder."""
            try:
                return service.artifact_entry(str(value))[1]
            except Exception:  # noqa: BLE001 - an unknown id: the question shows the id
                return None

        specs.append(ToolSpec(
            "run_benchmark", "Benchmark a model",
            "Measures prompt and generation speed (tokens per second) and peak memory with the self-built "
            "llama-bench on this computer: Ditto's own catalog tier model (tier_speed, natively or in the Docker "
            "sandbox) or a built GGUF model (artifact_speed, always in the Docker sandbox: a model built for a board "
            "is never benchmarked natively here). For real values of a model on its board use run_device_benchmark "
            "after deploy_to_device. A job.",
            ToolTier.LOCAL_WRITE, BenchmarkIn, JobStartedOut, run_benchmark, takes_context=True,
            default_decision=Decision.DENY, max_decision=Decision.ALLOW, prepare=bench_facts,
            # an untrusted model in the sandbox is the person's to allow, in the framework's own window only
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
                status = store.key_status(device) if device.paired else "not_paired"
                code = {"changed": "device_host_key_changed", "unknown": "host_key_unknown"}.get(status,
                                                                                               "device_not_trusted")
                raise blocked(code, f"The device {device.label} cannot be used now (not paired, or its trusted host "
                              "key is no longer in the framework's known_hosts).",
                              "Ask the person to trust and pair it again in the GUI or the CLI.", field="device_id")
            return device

        def svc_error(e: Exception, users: Sequence[str] = ()) -> ToolError:
            from orchestrator.remote import reasons
            text = device_text(str(e), users, folder_labels()) if users else scrub(str(e))
            reason = getattr(e, "reason", "")
            error = ToolError(text, code=getattr(e, "code", "failed"), next=getattr(e, "next", ""),
                              steps=reasons.LOCAL_STEPS if reason == reasons.LOCAL_PROBLEM else None)
            # the SSH reason code of a board that could not be reached or logged in to (N6) is its blocker; a
            # problem on this computer (local_problem) is none, it keeps its next step
            if reason in BLOCKERS:
                with_blocker(error, reason)
            return error

        def device_lines(device, fp: str) -> List[Fact]:
            # D0: the login (account and address) is the person's to see, never an AI's
            return [Fact("Device", device.label),
                    Fact("Login", f"{device.user}@{device.address}", person_only=True),
                    Fact("Host key", f"{device.key_type} {fp}, the key you trusted; read from the device just now")]

        def session_hooks():
            """(on_client, cancel, stop): the one cancel of a board job (remote/session.py CancelToken). on_client
            holds the board session, so the job's cancel closes it at once (also one handed after the cancel) and ends
            any wait for the board; stop is the token."""
            from orchestrator.remote.session import CancelToken
            stop = CancelToken()
            return stop.hold, stop.cancel, stop

        def board_events(handle, device):
            """The board session's events as the job's progress (the board by its label only, D0)."""
            from orchestrator.remote.text import progress_events
            return progress_events(handle.progress, device.label)

        def probe_facts(inp: DeviceIn, ctx: ToolContext) -> CallFacts:
            from orchestrator.runtime.device import service as dev
            device = trusted(inp.device_id)
            try:
                fp = dev.read_key(device, state_dir(), step="probe")
            except dev.DeviceRuntimeError as e:
                raise svc_error(e, device_users(device)) from None
            facts = device_lines(device, fp) + [Fact("On the device", "the framework's read-only hardware probe runs "
                                                                      f"as the {DEVICE_ACCOUNT}; nothing is installed")]
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
                    # the cancel holds the probe's session: closing it ends the probe and any wait for the board; the
                    # waits show in the job's progress as for the other board tools (the board by its label only, D0)
                    out = dev.probe(device, state_dir(), bound["fingerprint"], on_client=on_client,
                                    on_event=board_events(handle, device))
                except dev.DeviceRuntimeError as e:
                    if stop.is_set():
                        raise JobCancelled() from None
                    raise job_error(e, device_users(device)) from None
                return {**out, "next": f"Read the values with get_hardware_probe(probe_id='{device.device_id}')."}

            def probed(out: Dict[str, Any]):
                return ("ok", f"probe stored under the device id, {out.get('warnings', 0)} warnings", "")

            job = jobs.start("probe", ctx.owner,
                             audited("probe_remote_device", inp, ctx, work, f"device {device.label}", done=probed,
                                     cancelled=lambda: "cancelled; the SSH session was closed"), on_cancel=cancel)
            return JobStartedOut(job_id=job.job_id, kind=job.kind, state=job.state)

        def deploy_facts(inp: DeviceIn, ctx: ToolContext) -> CallFacts:
            from orchestrator.runtime.build.recipe import cpu_feature_lines
            from orchestrator.runtime.device import service as dev
            from orchestrator.runtime.device.package import DeviceRuntimeStore
            device = trusted(inp.device_id)
            users = device_users(device)
            try:
                profile = dev.device_profile(state_dir(), device)
                settings = dev.deploy_settings(state_dir(), device, profile)
                users = device_users(device, settings)
                plan = dev.plan_facts(state_dir(), profile)
                kv = settings.kv_choice(profile)
                if offline():   # the source only from the person's own network (the network-locality rule)
                    refuse_device_source(plan)
                fp = dev.read_key(device, state_dir(), step="runtime_deploy")
            except dev.DeviceRuntimeError as e:
                raise svc_error(e, users) from None
            target = plan.target
            facts = device_lines(device, fp) + [
                Fact("Runtime", f"llama.cpp for {target.get('os')}/{target.get('arch')} ({target.get('libc')} "
                                f"{target.get('libc_version')}), build {plan.build_id[:16]}: "
                                + ("already built" if plan.built else
                                   "built first in the builder container "
                                   f"{plan.builder.get('base')} (Docker, no network during the build)"
                                   + ("" if plan.source_cached else "; fetches the pinned source (about 215 MB)"))),
                # the board's CPU features: what the build uses and what it cannot use, with the reason
                *(Fact("CPU features", line)
                  for line in cpu_feature_lines(getattr(plan, "cpu_features", None) or {})),
                Fact("Install", f"as the {DEVICE_ACCOUNT} into its llm-framework data folder"
                                + (f", and the systemd unit with sudo -n (you confirmed it on "
                                   f"{settings.sudo_confirmed_at})" if settings.install == "systemd" else
                                   " with the start script (127.0.0.1 only)")),
                # D0: the account name and the device path are the person's to see, never an AI's
                Fact("Install as", f"{settings.ssh_user}@{device.address} into "
                                   "~/.local/share/llm-framework/llama.cpp", person_only=True),
                Fact("Checks", "the new build's programs are checked on the device before it becomes active; a "
                               "build that fails is removed and the previous one stays active"),
                # the KV-cache type the server gets (eighth round N7): q8_0 and q4_0 need less memory
                Fact("KV cache", kv.describe() + (" (the systemd unit starts the server with it)"
                                                  if settings.install == "systemd" else
                                                  " (named in the start command)"))]
            consequence = (f"Installs the self-built llama.cpp runtime on the device {device.label}"
                           + ("" if plan.built else ", building it first on this computer") + ".")
            replaced = DeviceRuntimeStore(state_dir(), device.device_id).record()
            warnings = (warn("device_runtime_replaced"),) if replaced else ()
            return CallFacts(consequence=consequence, facts=tuple(facts), cap=Decision.ASK.value,
                             bound={"device_id": device.device_id, "fingerprint": fp, "build_id": plan.build_id,
                                    "user": settings.ssh_user, "install": settings.install, "kv_cache": kv.kind},
                             warnings=warnings, scope={"device_id": device.device_id},
                             consequence_template=consequence, names={"device": device.label})

        def deploy_device_runtime(inp: DeviceIn, ctx: ToolContext) -> JobStartedOut:
            from orchestrator.runtime.device import service as dev
            bound = dict(ctx.facts.bound) if ctx.facts is not None else {}
            if bound.get("device_id") != inp.device_id or not bound.get("build_id"):
                raise ToolError("The deploy is not bound to what the person was shown; nothing was done.",
                                code="failed", next="Call deploy_device_runtime again through the framework's "
                                                    "registry.")
            device = trusted(inp.device_id)
            if offline():
                # switched on while the person was asked: the same refusal again, before a job starts (as
                # download_hf_file); a board's build that needs the pinned source fetches it only from the own network
                try:
                    refuse_device_source(dev.plan_facts(state_dir(), dev.device_profile(state_dir(), device)))
                except dev.DeviceRuntimeError as e:
                    raise svc_error(e, device_users(device)) from None
            on_client, cancel, stop = session_hooks()

            def work(handle) -> Dict[str, Any]:
                from orchestrator.Core.job_service import EXTERNAL_KEY, JobCancelled
                # the paired login, then also the deploy settings' account the deploy logs in with (D0)
                users = device_users(device)
                try:
                    profile = dev.device_profile(state_dir(), device)
                    settings = dev.deploy_settings(state_dir(), device, profile)
                    users = device_users(device, settings)
                    if (settings.ssh_user, settings.install, settings.kv_choice(profile).kind) != \
                            (bound["user"], bound["install"], bound.get("kv_cache")):
                        raise ValueError("The deploy settings changed after the person was asked; nothing was done.")
                    out = dev.deploy(state_dir(), device, profile, settings, bound["fingerprint"], bound["build_id"],
                                     on_client=on_client, cancelled=lambda: stop.is_set() or handle.cancelled,
                                     say=lambda msg: handle.progress(None, None, device_text(msg, users,
                                                                                             folder_labels())[:200]),
                                     on_event=board_events(handle, device), config=getattr(framework, "config", None),
                                     ai_started=True, signer=_board_signer)   # its build stops at the soft caps (DECISIONS round 25 Q8)
                except dev.DeviceRuntimeError as e:
                    if stop.is_set() or handle.cancelled:
                        raise JobCancelled() from None
                    raise job_error(e, users) from None
                checks = out.pop("checks")
                notes = out.pop("notes")
                # the device's words also on success: its account and paths never reach an AI (D0)
                lines = [device_text(f"{c['name']}: {c['result']} - {c['detail']}", users, folder_labels())
                         for c in checks] + [device_text(n, users, folder_labels()) for n in notes]
                if out.get("older_builds_kept"):
                    out["older_builds_note"] = OLDER_BUILDS_NOTE
                return {**out, "checks_passed": sum(1 for c in checks if c["result"] == "passed"),
                        "checks_failed": sum(1 for c in checks if c["result"] == "failed"), EXTERNAL_KEY: lines}

            def installed(out: Dict[str, Any]):
                ok = bool(out.get("activated"))
                return ("ok" if ok else "error",
                        f"{'activated' if ok else 'not activated'}: {out.get('checks_passed', 0)} checks passed, "
                        f"{out.get('checks_failed', 0)} failed", "" if ok else "failed")

            job = jobs.start("deploy", ctx.owner,
                             audited("deploy_device_runtime", inp, ctx, work,
                                     f"device {device.label}, build {str(bound['build_id'])[:16]}", done=installed,
                                     cancelled=lambda: "cancelled; the SSH session was closed, the device state is "
                                                       "unknown"), on_cancel=cancel)
            return JobStartedOut(job_id=job.job_id, kind=job.kind, state=job.state)

        def bench_device_facts(inp: DeviceIn, ctx: ToolContext) -> CallFacts:
            from orchestrator.runtime.device import service as dev
            from orchestrator.runtime.device.package import DeviceRuntimeStore
            device = trusted(inp.device_id)
            users = device_users(device)
            try:
                profile = dev.device_profile(state_dir(), device)
                settings = dev.deploy_settings(state_dir(), device, profile)
                users = device_users(device, settings)
                if not DeviceRuntimeStore(state_dir(), device.device_id).record():
                    raise blocked("no_device_runtime", f"No runtime is deployed to {device.label} yet.",
                                  "Deploy it first with deploy_device_runtime.")
                if settings.model is None:
                    raise blocked("device_model_unset", f"No model on {device.label} is named for the benchmark.",
                                  "Ask the person to name one with 'llm-cli device settings DEVICE --model PATH'.")
                fp = dev.read_key(device, state_dir(), step="benchmark")
            except dev.DeviceRuntimeError as e:
                raise svc_error(e, users) from None
            facts = device_lines(device, fp) + [
                # D0: the model by its file name; its path on the device is the person's to see
                Fact("Model", f"{_file_name(settings.model)} on the device (from its deploy settings)"),
                Fact("Model path", f"{settings.model} on the device", person_only=True),
                Fact("On the device", f"the checks of the deployed runtime and a short llama-bench run, as the "
                                      f"{DEVICE_ACCOUNT}, never with sudo"),
                Fact("Your consent", "if that file is not a catalog model (checked by its sha256 on the device), it is "
                                     "untrusted: approving this lets llama-bench load it natively on the device, not "
                                     "in a sandbox, and your consent is written to the audit log first")]
            consequence = (f"Runs the runtime checks and a short benchmark on the device {device.label} with the "
                           f"model {_file_name(settings.model)}.")
            # whether the model on the device is a catalog model is known only there: the person's consent is asked
            # for every run (an untrusted model runs natively), so the run is always the person's to decide
            return CallFacts(consequence=consequence, facts=tuple(facts), cap=Decision.ASK.value,
                             bound={"device_id": device.device_id, "fingerprint": fp, "user": settings.ssh_user,
                                    "model": settings.model},
                             warnings=(warn("untrusted_model_native"),), scope={"device_id": device.device_id},
                             consequence_template=consequence, names={"device": device.label})

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
                # the paired login, then also the deploy settings' account the checks log in with (D0)
                users = device_users(device)
                try:
                    profile = dev.device_profile(state_dir(), device)
                    settings = dev.deploy_settings(state_dir(), device, profile)
                    users = device_users(device, settings)
                    if (settings.ssh_user, settings.model) != (bound["user"], bound["model"]):
                        raise ValueError("The deploy settings changed after the person was asked; nothing ran.")
                    handle.progress(None, None, f"checking the runtime on {device.label}")
                    out = dev.benchmark(state_dir(), device, profile, settings, bound["fingerprint"],
                                        allow_untrusted=True, on_client=on_client, consent=consent,
                                        output_dirs=output_folders(), on_event=board_events(handle, device))
                except dev.DeviceRuntimeError as e:
                    if stop.is_set():
                        raise JobCancelled() from None
                    raise job_error(e, users) from None
                checks = out.pop("checks")
                # the device's words also on success: its account and paths never reach an AI (D0)
                return {**out, EXTERNAL_KEY: [device_text(f"{c['name']}: {c['result']} - {c['detail']}", users,
                                                          folder_labels()) for c in checks]}

            def checked(out: Dict[str, Any]):
                ok = bool(out.get("ok"))
                return ("ok" if ok else "error",
                        f"checks {'passed' if ok else 'failed'}, model sha256 "
                        f"{str(out.get('model_sha256') or '')[:16]} "
                        f"({'catalog model' if out.get('trusted_model') else 'untrusted'}), prompt "
                        f"{speed(out.get('prompt_ts'))}, generation {speed(out.get('gen_ts'))}",
                        "" if ok else "failed")

            job = jobs.start("benchmark", ctx.owner,
                             audited("run_device_benchmark", inp, ctx, work, f"device {device.label}", done=checked,
                                     cancelled=lambda: "cancelled; the SSH session was closed"), on_cancel=cancel)
            return JobStartedOut(job_id=job.job_id, kind=job.kind, state=job.state)

        def device_name(value) -> Optional[str]:
            """The device's label (a question for a client's form may reach an AI: never its login, D0)."""
            try:
                d = TrustedDevices.for_framework(framework).get(str(value))
                return d.label if d else None
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
                     "model its deploy settings name; an untrusted model only with the person's consent. This is "
                     "where a model built for a board is benchmarked (never on this computer); the result is recorded "
                     "with the build of that model as tested on the board (recorded_builds). A job.",
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
