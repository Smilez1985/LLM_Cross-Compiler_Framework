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
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

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

# llama.cpp quantization types that cannot be produced without an importance matrix
LLAMA_CPP_NEEDS_IMATRIX = frozenset({"IQ1_S", "IQ1_M", "IQ2_XXS", "IQ2_XS", "IQ2_S"})


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

    # Board within the target family (e.g. rk3588 vs rk3566) and model kind
    target_board: Optional[str] = None
    model_task: str = "LLM"
    max_context_length: Optional[int] = None
    # True when the dataset is the framework's shipped calibration text (used for GGUF importance matrices only)
    dataset_generated: bool = False
    # Commit of a Hugging Face model (recipes pin one); None builds the default branch
    model_revision: Optional[str] = None
    
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

    @property
    def progress_percent(self) -> int:
        if self.total_builds == 0: return 0
        return int((self.completed_builds / self.total_builds) * 100)

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

        # Pending healing approvals (request_id -> event set by resolve_healing from the GUI/CLI thread)
        self._healing_events: Dict[str, asyncio.Event] = {}
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
            # Workflow State erstellen
            state = WorkflowState(
                request_id=request.request_id,
                status=OrchestrationStatus.QUEUED,
                start_time=datetime.now()
            )
            self._workflows[request.request_id] = state
            
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

    async def list_workflows(self) -> List[WorkflowState]:
        """Listet alle bekannten Workflows"""
        return list(self._workflows.values())

    async def cancel_request(self, request_id: str) -> bool:
        """Cancels a request: stops its running build container and the pipeline task."""
        state = self._workflows.get(request_id)
        task = self._active_tasks.get(request_id)
        if state is None or (task is None and state.status in (OrchestrationStatus.COMPLETED,
                                                               OrchestrationStatus.ERROR,
                                                               OrchestrationStatus.CANCELLED)):
            return False
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
        return True

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
            self._active_tasks.pop(request.request_id, None)
            self._queue.task_done()
            self._ensure_worker_running()

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
            build_timeout=int(self.config.get("build_timeout", 3600)) if hasattr(self.config, "get") else 3600,
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
        context = f"Target: {job.target_architecture}, Model: {job.source_model}"
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

    def resolve_healing(self, request_id: str, approved: bool) -> bool:
        """Records the user's decision on a pending healing proposal. Safe to call from any thread."""
        state = self._workflows.get(request_id)
        event = self._healing_events.get(request_id)
        if not state or not event or state.status != OrchestrationStatus.AWAITING_APPROVAL:
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
                        out_dir = base / target / model.split("/")[-1]
                        if q: out_dir = out_dir / q
                        
                        job = BuildJob(
                            job_id=job_id,
                            source_model=model,
                            target_architecture=target,
                            target_format=fmt,
                            optimization=req.optimization_level,
                            quantization=q,
                            output_path=str(out_dir),
                            status=BuildStatus.QUEUED
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
            state.current_stage = f"Building {job.source_model} for {job.target_architecture}"
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
