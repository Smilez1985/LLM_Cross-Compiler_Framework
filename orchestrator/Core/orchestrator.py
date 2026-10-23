#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Core Orchestrator (v2.4.0)
DIREKTIVE: Goldstandard, vollständig, professionell geschrieben.

Zweck:
Verwaltet den gesamten Build-Lebenszyklus, von der Anfrage bis zum Artefakt.
Integriert Builder, ModuleGenerator, Self-Healing und Smart Calibration (shipped calibration text).

Updates v2.4.0:
- Updated BuildRequest with IMatrix flags.
- Pre-Build Dataset preparation phase.
"""

import asyncio
import threading
import uuid
from pathlib import Path
from typing import Callable, Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from orchestrator.utils.any_path import is_absolute_anywhere, last_part
from orchestrator.utils.logging import get_logger
from orchestrator.Core.builder import (BuildEngine, BuildStatus, OptimizationLevel, ModelFormat, BuildConfiguration,
                                       BuildSlotsBusy)
from orchestrator.utils.calibration import CalibrationDataError, write_calibration_dataset
from orchestrator.Core.module_generator import ModuleGenerator

# Optional Imports for Dependency Injection
try:
    from orchestrator.Core.self_healing_manager import SelfHealingManager, HealingProposal
except ImportError:
    SelfHealingManager = None
    HealingProposal = None

# ============================================================================
# DATENKLASSEN & ENUMS
# ============================================================================

class WorkflowType(Enum):
    SIMPLE_CONVERSION = "simple_conversion"
    FULL_CROSS_COMPILE = "full_cross_compile"
    RAG_OPTIMIZED = "rag_optimized"
    CUSTOM_PIPELINE = "custom_pipeline"

class PriorityLevel(Enum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    URGENT = 3
    CRITICAL = 4

# llama.cpp quantization types that cannot be produced without an importance matrix (llama-quantize refuses them;
# the same list as GGUF_NEEDS_IMATRIX of targets/Rockchip/modules/config_module.sh and needs_imatrix of its target.yml)
LLAMA_CPP_NEEDS_IMATRIX = frozenset({"IQ1_S", "IQ1_M", "IQ2_XXS", "IQ2_XS", "IQ2_S", "IQ2_M", "IQ3_XXS", "Q2_K_S"})


class OrchestrationStatus(Enum):
    QUEUED = "queued"
    PREPARING = "preparing"
    BUILDING = "building"
    HEALING = "healing"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    ERROR = "error"
    CANCELLED = "cancelled"

@dataclass
class BuildRequest:
    """Repräsentiert eine Anfrage vom User (CLI/GUI)"""
    request_id: str
    workflow_type: WorkflowType
    priority: PriorityLevel
    models: List[str]
    targets: List[str]
    target_formats: List[ModelFormat]
    optimization_level: OptimizationLevel
    quantization_options: List[str]
    parallel_builds: bool
    output_base_dir: str
    description: str = ""
    use_gpu: bool = False
    
    # NEW v2.4.0: Smart Calibration Flags
    use_imatrix: bool = False
    dataset_path: Optional[str] = None
    # The sha256 the dataset must have (a recipe pins it): the build engine uses only a copy with exactly that hash
    dataset_sha256: Optional[str] = None

    # Board within the target family (e.g. rk3588 vs rk3566) and model kind
    target_board: Optional[str] = None
    model_task: str = "LLM"
    max_context_length: Optional[int] = None
    # True when the dataset is the framework's shipped calibration text (used for GGUF importance matrices only)
    dataset_generated: bool = False
    # Commit of a Hugging Face model (recipes pin one); None builds the default branch
    model_revision: Optional[str] = None
    # Local model and dataset of an AI caller's build (orchestrator.tools.local_files.LocalInputs), checked again by
    # the build engine right before each container starts; set for every AI caller (None: a person's own paths)
    local_inputs: Optional[Any] = None
    # Who started it through a tool ("mcp#<session>", "ditto"; empty for the GUI and the CLI): that caller may
    # cancel it without a question, and a disconnected MCP session's builds are cancelled
    owner: str = ""
    # The disk reservation an AI caller's accepted build took (orchestrator.Core.disk_plan.ReservationLedger): checked
    # again when the request leaves the queue, released when it ends
    disk_reservation: str = ""

    def __post_init__(self):
        if not self.request_id:
            self.request_id = f"req_{uuid.uuid4().hex[:8]}"

@dataclass
class WorkflowState:
    """Aktueller Zustand eines Workflows"""
    request_id: str
    status: OrchestrationStatus
    start_time: datetime
    end_time: Optional[datetime] = None
    current_stage: str = "init"
    total_builds: int = 0
    completed_builds: int = 0
    failed_builds: int = 0
    artifacts: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    healing_proposal: Optional[Any] = None
    healing_decision: Optional[bool] = None
    build_ids: List[str] = field(default_factory=list)
    owner: str = ""  # BuildRequest.owner
    label: str = ""  # the request's speaking name: model (a local model by its label), target, format, quantization
    # What the request builds, as tools show it (the model by its repo id or label, never a host path)
    summary: Dict[str, Any] = field(default_factory=dict)
    # Fix proposals of AI clients (client_proposals.ClientProposal): manual steps for the person, never applied.
    client_proposals: List[Any] = field(default_factory=list)
    # Who cancelled it, when an AI did (its caller id: ditto, mcp, mcp:<token>); "" for the person or the framework
    cancelled_by: str = ""

    @property
    def progress_percent(self) -> int:
        if self.total_builds == 0: return 0
        return int((self.completed_builds / self.total_builds) * 100)

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)
        # A change other processes see (design T11): the orchestrator's listener writes the request's summary into
        # the per-user folder. Not set while the dataclass initializes itself.
        listener = self.__dict__.get("_listener")
        if listener is not None and name in WATCHED_FIELDS:
            listener(self)


# WorkflowState fields whose change is written to the shared build state (request_store)
WATCHED_FIELDS = frozenset({"status", "current_stage", "total_builds", "completed_builds", "failed_builds",
                            "end_time"})

class BuildQueueFull(RuntimeError):
    """The build queue holds MAX_UNFINISHED_REQUESTS unfinished requests; a tool caller's new one is refused, not
    queued."""


# Unfinished (queued or running) build requests in one framework process, from every caller (design 6.5: the global
# queue cap; the queue was unbounded). Only a tool caller's request (it has an owner: Ditto, an MCP client) is
# refused when the queue is full; the person's own GUI and CLI builds are always queued, AI work never blocks them.
MAX_UNFINISHED_REQUESTS = 20
FINISHED_STATES = ("COMPLETED", "ERROR", "CANCELLED")


@dataclass
class BuildJob:
    job_id: str
    source_model: str
    target_architecture: str
    target_format: ModelFormat
    optimization: OptimizationLevel
    quantization: str
    output_path: str
    status: BuildStatus
    error_log: str = ""
    model_label: str = ""  # how the model is named in stage texts and healing context (never a host path)


def _calling_actor() -> str:
    """The caller of the tool call this runs for (the registry's context, which the framework's event loop carries
    into a coroutine it was handed), "" outside a tool call."""
    try:
        from orchestrator.tools.registry import current_actor
        return str(current_actor() or "")
    except Exception:  # noqa: BLE001 - without the registry nobody is named
        return ""


def model_label(model: str, local_inputs: Any = None) -> str:
    """The model as it may be shown to people and AI models: a local model an AI caller pinned by its
    `<label>:<relative path>`, another local path by its file name, a Hugging Face repo id as it is. A path of either
    system counts as a path on every host (on Windows a board's or a Linux build's '/home/...' path has no drive)."""
    pin = getattr(local_inputs, "model", None)
    if pin is not None:
        return pin.ref
    return last_part(model) if is_absolute_anywhere(str(model)) else str(model)


def request_summary(request: "BuildRequest") -> Dict[str, Any]:
    """What a request builds, for the build tools: model (repo id or label), target, format, board, quantization."""
    return {"model": ", ".join(model_label(m, request.local_inputs) for m in request.models) or None,
            "target": ", ".join(request.targets) or None,
            "format": ", ".join(getattr(f, "value", str(f)) for f in request.target_formats) or None,
            "board": request.target_board, "quantization": ", ".join(str(q) for q in request.quantization_options if q)
            or None, "optimization": getattr(request.optimization_level, "value", None),
            "revision": request.model_revision}


def request_label(request: "BuildRequest") -> str:
    """A build request's speaking name: "<model> for <target> (<format> <quantization>)"."""
    models = ", ".join(model_label(m, request.local_inputs) for m in request.models) or "?"
    formats = ", ".join(getattr(f, "value", str(f)) for f in request.target_formats)
    quants = ", ".join(str(q) for q in request.quantization_options if q)
    detail = " ".join(part for part in (formats, quants) if part)
    return f"{models} for {', '.join(request.targets) or '?'}" + (f" ({detail})" if detail else "")

# ============================================================================
# ORCHESTRATOR KLASSE
# ============================================================================

class LLMOrchestrator:
    def __init__(self, config_manager, build_engine: Optional[BuildEngine] = None):
        self.logger = get_logger(__name__)
        self.config = config_manager
        
        # Sub-Engines (the framework passes its shared engine, which knows the secrets store)
        self.build_engine = build_engine or BuildEngine(config_manager)
        self.module_generator = ModuleGenerator(Path(getattr(self.build_engine, "targets_dir", None)
                                                     or config_manager.targets_dir))
        
        # State Management
        self._workflows: Dict[str, WorkflowState] = {}
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._active_tasks: Dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        
        # Dependency Injection Containers
        self.self_healing = None 

        # Disk reservations of accepted AI builds (disk_plan.ReservationLedger), set by the build service
        self.disk_ledger = None

        # The shared build state (request_store.RequestStore in the per-user folder): every request's summary, so
        # the GUI, the CLI and every MCP server see the same builds. None without a per-user folder.
        from orchestrator.Core.request_store import for_config
        try:
            self.request_store = for_config(config_manager)
        except OSError as e:
            self.logger.warning(f"Shared build state not available: {e}")
            self.request_store = None

        # Pending healing approvals (request_id -> event set by resolve_healing from the GUI/CLI thread)
        self._healing_events: Dict[str, asyncio.Event] = {}
        # Told once when a request ends (completed, error, cancelled): the chat feed's producer (Core/activity.py).
        self._end_listeners: List[Callable[[Dict[str, Any]], None]] = []
        self._ended: set = set()
        self._end_lock = threading.Lock()
        # A finished build's folder as an AI may read it (build_service.artifact_label: output:<relative path>), set
        # by the framework; without it the folder's own name.
        self.artifact_label: Optional[Callable[[str], str]] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # Strong references: the event loop keeps only weak references to tasks.
        self._worker_tasks: set = set()

    async def initialize(self) -> bool:
        """Asynchrone Initialisierung"""
        self.logger.info("Initializing Orchestrator...")
        # Check Docker connectivity via BuildEngine
        if hasattr(self.build_engine, 'check_docker'):
             if not self.build_engine.check_docker():
                self.logger.error("Docker not ready. Orchestrator functionality limited.")
                return False
        return True

    # --- PUBLIC API ---

    async def submit_build_request(self, request: BuildRequest) -> str:
        """Nimmt einen neuen Build-Auftrag entgegen"""
        async with self._lock:
            unfinished = sum(1 for s in self._workflows.values() if s.status.name not in FINISHED_STATES)
            if getattr(request, "owner", "") and unfinished >= MAX_UNFINISHED_REQUESTS:
                raise BuildQueueFull(f"The build queue is full ({MAX_UNFINISHED_REQUESTS} unfinished build requests); "
                                     "the request was not queued.")
            # Workflow State erstellen
            state = WorkflowState(
                request_id=request.request_id,
                status=OrchestrationStatus.QUEUED,
                start_time=datetime.now(),
                owner=getattr(request, "owner", "") or "",
                label=request_label(request),
                summary=request_summary(request),
            )
            self._workflows[request.request_id] = state
            self._share(state)
            state._listener = self._share
            if self.request_store is not None:
                try:
                    self.request_store.prune()
                except OSError as e:
                    self.logger.debug(f"Old build request summaries not pruned: {e}")

            # In Queue packen (Priorität beachten: Negativ, da PriorityQueue min-heap ist)
            # Tuple: (priority_int, timestamp, request)
            await self._queue.put((-request.priority.value, datetime.now().timestamp(), request))
            
            self.logger.info(f"Request {request.request_id} queued (Priority: {request.priority.name})")
            
            # Worker triggern
            self._ensure_worker_running()
            
            return request.request_id

    async def get_workflow_status(self, request_id: str) -> Optional[WorkflowState]:
        """Gibt den aktuellen Status zurück"""
        return self._workflows.get(request_id)

    def list_workflows(self) -> List[WorkflowState]:
        """Every workflow this orchestrator knows, oldest first: a snapshot (the build service and the GUI read it
        from their own threads)."""
        return list(self._workflows.values())

    async def cancel_request(self, request_id: str, by: str = "") -> bool:
        """Cancels a request: stops its running build container and the pipeline task. `by`: the AI caller that
        cancels it (ditto, mcp, mcp:<token>); without it the caller of the tool call that runs this, if any (the
        person's own GUI and CLI cancels name nobody)."""
        state = self._workflows.get(request_id)
        task = self._active_tasks.get(request_id)
        if state is None or (task is None and state.status in (OrchestrationStatus.COMPLETED,
                                                               OrchestrationStatus.ERROR,
                                                               OrchestrationStatus.CANCELLED)):
            return False
        who = by or _calling_actor()
        state.cancelled_by = who if who not in ("", "cli", "gui") else ""
        # Mark first, so the pipeline does not treat the stopped build as a failure to heal.
        state.status = OrchestrationStatus.CANCELLED
        loop = asyncio.get_running_loop()
        for build_id in list(state.build_ids):
            if await loop.run_in_executor(None, self.build_engine.cancel_build, build_id):
                continue
            # A build that still runs but refuses: its outcome is settled (its files are being published,
            # or it failed and cleans up). Say so instead of letting "cancelled" stand alone.
            progress = self.build_engine.get_build_status(build_id)
            if progress is not None and getattr(progress, "status", None) not in (
                    BuildStatus.COMPLETED, BuildStatus.FAILED, BuildStatus.CANCELLED):
                state.warnings.append(f"Build {build_id} could no longer be cancelled; it finishes on its own.")
        event = self._healing_events.get(request_id)
        if event is not None:
            event.set()  # a pending approval counts as dismissed
        if task is not None:
            task.cancel()  # a queued request (no task yet) is skipped by the worker
        state.end_time = state.end_time or datetime.now()
        if task is None:
            self._announce_end(state)  # a queued request ends here; a running one when its task has stopped
        return True

    def add_end_listener(self, callback: Callable[[Dict[str, Any]], None]) -> Callable[[], None]:
        """Calls `callback(facts)` once for every request that ends from now on (build_end): request_id, status
        (completed, error, cancelled), the owner's caller class ("" for the person), who cancelled it (an AI's caller
        id, else ""), the saved folder as a label (never a host path) and the counts. Called on the build loop's
        thread; a listener must not block. Returns the function that removes it."""
        with self._end_lock:
            self._end_listeners.append(callback)

        def remove() -> None:
            with self._end_lock:
                if callback in self._end_listeners:
                    self._end_listeners.remove(callback)
        return remove

    def _saved_label(self, path: Any) -> str:
        """A finished build's folder as an AI may read it: the framework's label, else the folder's own name."""
        label = ""
        if self.artifact_label is not None:
            try:
                label = str(self.artifact_label(str(path)) or "")
            except Exception as e:  # noqa: BLE001 - the folder's own name then
                self.logger.debug(f"No label for a finished build's folder: {e.__class__.__name__}")
        where, sep, rest = label.partition(":")
        # a host path, also one behind a label's place ("output:/home/<user>/..."): the folder's own name then
        if not label or is_absolute_anywhere(label) or (sep and is_absolute_anywhere(rest)):
            label = last_part(str(path))
        return label

    def _announce_end(self, state: "WorkflowState") -> None:
        """Tells the end listeners once that a request ended (completed, error, cancelled): framework facts only."""
        if state.status not in (OrchestrationStatus.COMPLETED, OrchestrationStatus.ERROR,
                                OrchestrationStatus.CANCELLED):
            return
        with self._end_lock:
            if state.request_id in self._ended:
                return
            self._ended.add(state.request_id)
            listeners = list(self._end_listeners)
        if not listeners:
            return
        from orchestrator.Core.job_service import owner_actor
        facts = {"request_id": state.request_id, "status": state.status.value, "owner": owner_actor(state.owner),
                 "cancelled_by": state.cancelled_by or "",
                 "saved": self._saved_label(state.artifacts[0]) if state.artifacts else "",
                 "artifacts": len(state.artifacts), "completed": int(state.completed_builds),
                 "failed": int(state.failed_builds)}
        for callback in listeners:
            try:
                callback(dict(facts))
            except Exception as e:  # noqa: BLE001 - a listener never changes a build
                self.logger.warning(f"An end listener of {state.request_id} failed: {e.__class__.__name__}")

    def _share(self, state: "WorkflowState") -> None:
        """Writes the request's summary into the shared build state; a summary that cannot be written never stops
        a build."""
        store = getattr(self, "request_store", None)
        if store is None:
            return
        try:
            store.save(state)
        except (OSError, TypeError, ValueError) as e:
            self.logger.debug(f"Summary of {state.request_id} not shared: {e}")

    # --- DEPENDENCY INJECTION ---

    def inject_self_healing(self, manager):
        """Dependency Injection für SelfHealingManager"""
        self.self_healing = manager
        self.logger.info("Self-Healing Manager injected into Orchestrator.")

    # --- INTERNAL WORKER ---

    def _ensure_worker_running(self):
        task = asyncio.create_task(self._process_next_item())
        self._worker_tasks.add(task)
        task.add_done_callback(self._worker_tasks.discard)

    async def _process_next_item(self):
        if self._queue.empty(): return
        
        _, _, request = await self._queue.get()
        state = self._workflows.get(request.request_id)
        if state is not None and state.status == OrchestrationStatus.CANCELLED:
            self._release_disk(request)
            self._announce_end(state)
            self._ensure_worker_running()  # cancelled while queued: go on with the next request
            return
        
        # Task erstellen und tracken
        task = asyncio.create_task(self._run_build_pipeline(request))
        self._active_tasks[request.request_id] = task
        
        try:
            await task
        except asyncio.CancelledError:
            self.logger.warning(f"Task {request.request_id} cancelled")
        except Exception as e:
            self.logger.error(f"Task {request.request_id} failed: {e}", exc_info=True)
            if request.request_id in self._workflows:
                self._workflows[request.request_id].status = OrchestrationStatus.ERROR
                self._workflows[request.request_id].errors.append(str(e))
        finally:
            self._release_disk(request)
            if request.request_id in self._workflows:
                self._share(self._workflows[request.request_id])  # errors and warnings added at the end
                self._announce_end(self._workflows[request.request_id])  # with them: the end, once
            self._active_tasks.pop(request.request_id, None)
            self._queue.task_done()
            self._ensure_worker_running()

    def _release_disk(self, request: BuildRequest) -> None:
        rid = getattr(request, "disk_reservation", "")
        if rid and self.disk_ledger is not None:
            try:
                self.disk_ledger.release(rid)
            except Exception as e:  # noqa: BLE001 - a reservation of an ended process is dropped anyway
                self.logger.warning(f"Disk reservation of {request.request_id} not released: {e}")

    def _disk_still_fits(self, req: BuildRequest, state: "WorkflowState") -> bool:
        """When an AI caller's build leaves the queue: the disk still holds what was reserved for it (other programs
        and builds wrote meanwhile). Otherwise the request fails with the numbers, before anything is downloaded."""
        rid = getattr(req, "disk_reservation", "")
        if not rid or self.disk_ledger is None:
            return True
        try:
            self.disk_ledger.recheck(rid)
        except ValueError as e:  # disk_plan.InsufficientSpace
            state.status = OrchestrationStatus.ERROR
            state.current_stage = "Failed"
            state.errors.append(f"insufficient_resources: {e}")
            state.end_time = datetime.now()
            return False
        except Exception as e:  # noqa: BLE001 - a ledger that cannot be read does not stop the build
            self.logger.warning(f"Disk reservation of {req.request_id} not checked again: {e}")
        return True

    def _target_exists(self, target: str) -> bool:
        """Through the engine's id -> folder map (a target id may differ from its folder name)."""
        resolve = getattr(self.build_engine, "resolve_target_dir", None)
        if callable(resolve):
            return resolve(target) is not None
        targets_dir = Path(getattr(self.build_engine, "targets_dir", None) or self.config.targets_dir)
        return (targets_dir / target).exists()

    def _map_job_to_config(self, job: BuildJob, req: BuildRequest, attempt: int = 1) -> BuildConfiguration:
        """Konvertiert internen Job zu Builder Config"""
        base_img = "debian:bookworm-slim"
        if hasattr(self.config, 'image_base_debian'):
            base_img = self.config.image_base_debian

        return BuildConfiguration(
            # every attempt keeps its own progress and log (a retry after a fix must not hide the failure)
            build_id=job.job_id if attempt <= 1 else f"{job.job_id}_r{attempt - 1}",
            request_id=req.request_id,
            timestamp=datetime.now().strftime("%Y%m%d_%H%M%S"),
            model_source=job.source_model,
            model_branch=req.model_revision or "main",
            target_arch=job.target_architecture,
            target_format=job.target_format,
            output_dir=job.output_path,
            optimization_level=job.optimization,
            quantization=job.quantization,
            use_gpu=req.use_gpu,
            base_image=base_img,
            source_format=ModelFormat.HUGGINGFACE,
            target_board=req.target_board,
            model_task=req.model_task,
            max_context_length=req.max_context_length,
            # The importance matrix is GGUF-only; the shipped calibration text is never an NPU calibration set.
            use_imatrix=req.use_imatrix and job.target_format == ModelFormat.GGUF,
            dataset_path=(None if req.dataset_generated and job.target_format != ModelFormat.GGUF
                          else req.dataset_path),
            dataset_sha256=None if req.dataset_generated else req.dataset_sha256,
            build_timeout=int(self.config.get("build_timeout", 3600)) if hasattr(self.config, "get") else 3600,
            local_inputs=req.local_inputs,
        )

    async def _submit_to_engine(self, build_config: BuildConfiguration, state: Optional[WorkflowState]) -> Optional[str]:
        """Hands a build to the engine. While every engine slot is taken (another request's build, or a
        cancelled one that is still stopping) it waits for a free slot instead of failing the job.
        Returns None if the request was cancelled meanwhile."""
        loop = asyncio.get_running_loop()
        stage = state.current_stage if state is not None else None
        while True:
            submit = loop.run_in_executor(None, self.build_engine.build_model, build_config)
            try:
                returned_id = await asyncio.shield(submit)
            except asyncio.CancelledError:
                # The executor thread may still submit the build: cancel it as soon as it exists.
                def cancel_submitted(fut):
                    if not fut.cancelled() and fut.exception() is None:
                        loop.run_in_executor(None, self.build_engine.cancel_build, fut.result())
                submit.add_done_callback(cancel_submitted)
                raise
            except BuildSlotsBusy:
                if state is not None and state.status == OrchestrationStatus.CANCELLED:
                    return None
                if state is not None:
                    state.current_stage = f"Waiting for a free build slot ({build_config.build_id})"
                await asyncio.sleep(1)
                continue
            if state is not None and stage is not None:
                state.current_stage = stage
            return returned_id

    async def _run_job(self, job: "BuildJob", req: BuildRequest, attempt: int = 1) -> bool:
        """Runs one build job on the BuildEngine and waits for its terminal state."""
        loop = asyncio.get_running_loop()
        try:
            build_config = self._map_job_to_config(job, req, attempt)
            state = self._workflows.get(req.request_id)
            # Known before it is submitted, so a cancel arriving meanwhile reaches it.
            if state is not None and build_config.build_id not in state.build_ids:
                state.build_ids.append(build_config.build_id)
            returned_id = await self._submit_to_engine(build_config, state)
            if returned_id is None:
                job.error_log = "Cancelled by the user."
                return False
            if state is not None and state.status == OrchestrationStatus.CANCELLED:
                await loop.run_in_executor(None, self.build_engine.cancel_build, returned_id)
            while True:
                status = self.build_engine.get_build_status(returned_id)
                if not status:
                    job.error_log = "Build vanished"
                    return False
                if status.status == BuildStatus.COMPLETED:
                    published = getattr(status, "published", None)
                    if isinstance(published, str) and published:
                        job.output_path = published  # next to the planned folder when an earlier build is there
                    return True
                if status.status == BuildStatus.CANCELLED:
                    job.error_log = "Cancelled by the user."
                    return False
                if status.status == BuildStatus.FAILED:
                    job.error_log = "\n".join(status.errors)
                    return False
                await asyncio.sleep(1)
        except Exception as e:
            self.logger.error(f"Execution Error: {e}")
            job.error_log = str(e)
            return False

    def _approval_timeout(self) -> float:
        get_fn = getattr(self.config, "get", None)
        value = get_fn("healing_approval_timeout", 900) if get_fn else 900
        try:
            return max(float(value), 1.0)
        except (TypeError, ValueError):
            return 900.0

    async def _heal_and_retry(self, state: "WorkflowState", job: "BuildJob", req: BuildRequest) -> bool:
        """Asks the self-healing manager for a proposal and waits for the user's decision
        (AWAITING_APPROVAL). An approved fix is applied and the job is retried once."""
        loop = asyncio.get_running_loop()
        self.logger.warning(f"Build failed for {job.job_id}. Activating Self-Healing...")
        state.status = OrchestrationStatus.HEALING
        state.current_stage = f"Diagnosing {job.job_id}"
        context = f"Target: {job.target_architecture}, Model: {getattr(job, 'model_label', '') or job.source_model}"
        # The analysis may call an LLM; keep the event loop free meanwhile.
        proposal = await loop.run_in_executor(None, self.self_healing.analyze_error,
                                              job.error_log or "Unknown Error", context)
        if state.status == OrchestrationStatus.CANCELLED:
            return False
        if not proposal:
            self.logger.error("Self-Healing found no solution.")
            state.status = OrchestrationStatus.BUILDING
            return False

        event = asyncio.Event()
        self._healing_events[state.request_id] = event
        state.healing_decision = None
        state.healing_proposal = proposal
        state.status = OrchestrationStatus.AWAITING_APPROVAL
        state.current_stage = f"Waiting for approval: {proposal.summary}"
        try:
            await asyncio.wait_for(event.wait(), timeout=self._approval_timeout())
        except asyncio.TimeoutError:
            state.warnings.append(f"No decision on the healing proposal for {job.job_id}; it was dismissed.")
        finally:
            self._healing_events.pop(state.request_id, None)

        approved = bool(state.healing_decision)
        state.healing_proposal = None
        if state.status == OrchestrationStatus.CANCELLED:
            return False
        state.status = OrchestrationStatus.BUILDING
        if not approved:
            return False

        applied = await loop.run_in_executor(None, self.self_healing.apply_fix, proposal)
        if not applied:
            # errors are shown by the CLI summary and the GUI; a warning alone would go unnoticed
            state.errors.append(f"The fix for {job.job_id} could not be applied; manual step: {proposal.fix_command}")
            return False
        state.current_stage = f"Retrying {job.job_id} after the fix"
        self.logger.info(f"Fix applied; retrying {job.job_id}.")
        return await self._run_job(job, req, attempt=2)

    def add_client_proposal(self, request_id: str, proposal: Any) -> Any:
        """Keeps an AI client's fix proposal (client_proposals.ClientProposal, already validated and filtered) with
        a failed build: a build that ended with an error, failed a job, or waits for or works on a self-healing
        decision. It is a manual step for the person; nothing here ever applies it. Raises ValueError for an unknown
        or not failed build, ProposalLimit when the build holds MAX_PER_REQUEST proposals."""
        from orchestrator.Core.client_proposals import MAX_PER_REQUEST, ProposalLimit
        state = self._workflows.get(request_id)
        if state is None:
            raise KeyError(request_id)
        failed = state.status in (OrchestrationStatus.ERROR, OrchestrationStatus.AWAITING_APPROVAL,
                                  OrchestrationStatus.HEALING) or state.failed_builds > 0
        if not failed:
            raise ValueError(f"build request {request_id} has not failed ({state.status.value})")
        if len(state.client_proposals) >= MAX_PER_REQUEST:
            raise ProposalLimit(f"Build request '{request_id}' already has {MAX_PER_REQUEST} proposals from AI "
                                "clients.")
        state.client_proposals.append(proposal)
        return proposal

    def resolve_healing(self, request_id: str, approved: bool, expected: Any = None) -> bool:
        """Records the user's decision on a pending healing proposal. Safe to call from any thread. `expected`: the
        proposal the decision is about; a different pending one is not decided."""
        state = self._workflows.get(request_id)
        event = self._healing_events.get(request_id)
        if not state or not event or state.status != OrchestrationStatus.AWAITING_APPROVAL:
            return False
        if expected is not None and state.healing_proposal is not expected:
            return False
        state.healing_decision = bool(approved)
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(event.set)
        else:
            event.set()
        return True

    async def _run_build_pipeline(self, req: BuildRequest):
        """Die eigentliche Pipeline-Logik"""
        state = self._workflows[req.request_id]
        state.status = OrchestrationStatus.PREPARING
        state.current_stage = "Initialization"
        self._loop = asyncio.get_running_loop()
        
        self.logger.info(f"Starting pipeline for {req.request_id}")
        if not self._disk_still_fits(req, state):
            return
        
        # 0. Importance matrix: without a dataset of its own, a GGUF build uses the shipped, checksummed
        #    calibration text (German/English/tool use, see data/calibration). No generated filler.
        wants_gguf = not req.target_formats or ModelFormat.GGUF in req.target_formats
        if req.use_imatrix and not wants_gguf:
            # The importance matrix is a GGUF concept; RKLLM/RKNN use the user's dataset (if any) directly.
            req.use_imatrix = False
            state.warnings.append("IMatrix applies to GGUF only; disabled for this request.")
        elif req.use_imatrix and req.dataset_path:
            self.logger.info(f"IMatrix enabled with user dataset {req.dataset_path}")
        elif req.use_imatrix:
            state.current_stage = "Preparing calibration data"
            language = self.config.get("calibration_language", "mixed") if hasattr(self.config, "get") else "mixed"
            cache_dir = Path(getattr(self.build_engine, "cache_dir", None) or self.config.cache_dir)
            ds_path = cache_dir / "calibration" / f"calibration_{language}.txt"
            try:
                await asyncio.get_running_loop().run_in_executor(None, write_calibration_dataset, ds_path, language)
                req.dataset_path = str(ds_path)
                req.dataset_generated = True
                self.logger.info(f"Calibration dataset ready: {ds_path}")
            except (CalibrationDataError, OSError) as e:
                needs = [q for q in req.quantization_options if str(q).upper() in LLAMA_CPP_NEEDS_IMATRIX]
                if needs:
                    # Without the matrix llama.cpp refuses these types; fail now, not after the image build.
                    state.status = OrchestrationStatus.ERROR
                    state.errors.append(f"{', '.join(needs)} needs an importance matrix, but the calibration "
                                        f"data could not be prepared: {e}")
                    state.end_time = datetime.now()
                    return
                req.use_imatrix = False
                state.warnings.append(f"IMatrix disabled: {e}")

        # 1. Expand Build Matrix
        build_jobs: List[BuildJob] = []
        
        for model in req.models:
            for target in req.targets:
                if not self._target_exists(target):
                    state.warnings.append(f"Target '{target}' not found. Skipping.")
                    continue
                
                formats = req.target_formats if req.target_formats else [ModelFormat.GGUF]
                
                for fmt in formats:
                    quants = req.quantization_options if req.quantization_options else [None]
                    
                    for q in quants:
                        job_id = f"{req.request_id}_{len(build_jobs)+1:03d}"
                        
                        base = Path(req.output_base_dir).expanduser()
                        if not base.is_absolute() and getattr(self.build_engine, "base_dir", None):
                            base = Path(self.build_engine.base_dir) / base
                        # the repo id's or the local folder's last part, at '/' and '\\' (a Windows path)
                        out_dir = base / target / last_part(model)
                        if q: out_dir = out_dir / q
                        
                        job = BuildJob(
                            job_id=job_id,
                            source_model=model,
                            target_architecture=target,
                            target_format=fmt,
                            optimization=req.optimization_level,
                            quantization=q,
                            output_path=str(out_dir),
                            status=BuildStatus.QUEUED,
                            model_label=model_label(model, req.local_inputs),
                        )
                        build_jobs.append(job)

        state.total_builds = len(build_jobs)
        if not build_jobs:
            # Every target was skipped: nothing was built, which is no success.
            state.status = OrchestrationStatus.ERROR
            state.current_stage = "Failed"
            state.errors.append("Nothing to build: none of the requested targets was found.")
            state.end_time = datetime.now()
            return
        state.status = OrchestrationStatus.BUILDING
        
        self.logger.info(f"Generated {len(build_jobs)} build jobs.")
        
        # 2. Execution Loop
        for job in build_jobs:
            state.current_stage = f"Building {job.model_label or job.source_model} for {job.target_architecture}"
            success = await self._run_job(job, req)
            if state.status == OrchestrationStatus.CANCELLED:
                return

            # --- SELF-HEALING: diagnose, ask the user, apply an approved fix, retry once ---
            if not success and self.self_healing:
                success = await self._heal_and_retry(state, job, req)
                if state.status == OrchestrationStatus.CANCELLED:
                    return

            if success:
                state.completed_builds += 1
                state.artifacts.append(job.output_path)
            else:
                state.failed_builds += 1
                state.errors.append(f"Job {job.job_id} failed.")
                if req.priority == PriorityLevel.CRITICAL:
                    self.logger.error("Critical build failed. Aborting pipeline.")
                    state.status = OrchestrationStatus.ERROR
                    return

        # 3. Finalization
        state.end_time = datetime.now()
        if state.failed_builds == 0:
            state.status = OrchestrationStatus.COMPLETED
            state.current_stage = "Done"
        elif state.completed_builds > 0:
            state.status = OrchestrationStatus.COMPLETED # Partial success
            state.current_stage = "Completed with errors"
        else:
            state.status = OrchestrationStatus.ERROR
            state.current_stage = "Failed"
            
        self.logger.info(f"Pipeline finished. Status: {state.status.name}")
