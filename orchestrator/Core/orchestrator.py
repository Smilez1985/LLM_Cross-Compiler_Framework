#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Core Orchestrator (v2.4.0)
DIREKTIVE: Goldstandard, vollständig, professionell geschrieben.

Zweck:
Verwaltet den gesamten Build-Lebenszyklus, von der Anfrage bis zum Artefakt.
Integriert Builder, ModuleGenerator, Self-Healing und Smart Calibration (Ditto).

Updates v2.4.0:
- Integrated Ditto Manager for IMatrix/Smart Calibration workflow.
- Updated BuildRequest with IMatrix flags.
- Pre-Build Dataset preparation phase.
"""

import os
import sys
import json
import logging
import asyncio
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum

from orchestrator.utils.logging import get_logger
from orchestrator.Core.builder import BuildEngine, BuildStatus, OptimizationLevel, ModelFormat, BuildConfiguration
from orchestrator.Core.module_generator import ModuleGenerator

# Optional Imports for Dependency Injection
try:
    from orchestrator.Core.self_healing_manager import SelfHealingManager, HealingProposal
except ImportError:
    SelfHealingManager = None
    HealingProposal = None

try:
    from orchestrator.Core.ditto_manager import DittoCoder
except ImportError:
    DittoCoder = None

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

class OrchestrationStatus(Enum):
    QUEUED = "queued"
    PREPARING = "preparing"
    BUILDING = "building"
    HEALING = "healing"
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
    def __init__(self, config_manager):
        self.logger = get_logger(__name__)
        self.config = config_manager
        
        # Sub-Engines
        self.build_engine = BuildEngine(config_manager)
        self.module_generator = ModuleGenerator(Path(config_manager.targets_dir))
        
        # State Management
        self._workflows: Dict[str, WorkflowState] = {}
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._active_tasks: Dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        
        # Dependency Injection Containers
        self.self_healing = None 
        self.ditto = None # NEW: For IMatrix Dataset Generation

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
        """Bricht einen laufenden Request ab"""
        if request_id in self._active_tasks:
            self._active_tasks[request_id].cancel()
            if request_id in self._workflows:
                self._workflows[request_id].status = OrchestrationStatus.CANCELLED
            return True
        return False

    # --- DEPENDENCY INJECTION ---

    def inject_self_healing(self, manager):
        """Dependency Injection für SelfHealingManager"""
        self.self_healing = manager
        self.logger.info("Self-Healing Manager injected into Orchestrator.")

    def inject_ditto(self, manager):
        """Dependency Injection für DittoManager (IMatrix Support)"""
        self.ditto = manager
        self.logger.info("Ditto Manager (AI Agent) injected into Orchestrator.")

    # --- INTERNAL WORKER ---

    def _ensure_worker_running(self):
        # Einfache Implementierung: Fire & Forget Task pro Request in _process_queue
        asyncio.create_task(self._process_next_item())

    async def _process_next_item(self):
        if self._queue.empty(): return
        
        _, _, request = await self._queue.get()
        
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

    def _map_job_to_config(self, job: BuildJob, req: BuildRequest) -> BuildConfiguration:
        """Konvertiert internen Job zu Builder Config"""
        base_img = "debian:bookworm-slim"
        if hasattr(self.config, 'image_base_debian'):
            base_img = self.config.image_base_debian

        return BuildConfiguration(
            build_id=job.job_id,
            timestamp=datetime.now().strftime("%Y%m%d_%H%M%S"),
            model_source=job.source_model,
            target_arch=job.target_architecture,
            target_format=job.target_format,
            output_dir=job.output_path,
            optimization_level=job.optimization,
            quantization=job.quantization,
            use_gpu=req.use_gpu,
            base_image=base_img,
            source_format=ModelFormat.HUGGINGFACE,
            # Pass IMatrix Config
            use_imatrix=req.use_imatrix,
            dataset_path=req.dataset_path
        )

    async def _run_build_pipeline(self, req: BuildRequest):
        """Die eigentliche Pipeline-Logik"""
        state = self._workflows[req.request_id]
        state.status = OrchestrationStatus.PREPARING
        state.current_stage = "Initialization"
        
        self.logger.info(f"Starting pipeline for {req.request_id}")
        
        # 0. Smart Calibration / IMatrix Preparation (NEW v2.4.0)
        # ------------------------------------------------------------------
        # Falls IMatrix gewünscht ist, müssen wir Ditto bitten, ein Dataset zu liefern.
        if req.use_imatrix:
            state.current_stage = "Preparing Calibration Data"
            if not self.ditto:
                self.logger.warning("IMatrix requested but Ditto Manager not available. Skipping Smart Calibration.")
                req.use_imatrix = False
                state.warnings.append("IMatrix disabled (Ditto offline).")
            else:
                self.logger.info("IMatrix enabled. Requesting dataset from Ditto...")
                # Pfad definieren
                calib_dir = Path(self.config.cache_dir) / "calibration"
                if not calib_dir.exists(): calib_dir.mkdir(parents=True, exist_ok=True)
                
                # Default Dataset Name aus Config
                ds_name = getattr(self.config, 'default_calibration_dataset', 'wiki.train.raw')
                ds_path = calib_dir / ds_name
                
                # Ditto call (Synchroner Call, da Request-basiert, evtl. in Executor auslagern)
                # Wir machen es hier direkt, da requests schnell sein sollten oder offline generiert.
                try:
                    success = self.ditto.prepare_calibration_dataset(ds_path)
                    if success:
                        req.dataset_path = str(ds_path)
                        self.logger.info(f"Calibration dataset ready: {ds_path}")
                    else:
                        req.use_imatrix = False
                        state.warnings.append("Failed to prepare dataset. IMatrix disabled.")
                except Exception as e:
                    self.logger.error(f"Error during dataset preparation: {e}")
                    req.use_imatrix = False
        # ------------------------------------------------------------------

        # 1. Expand Build Matrix
        build_jobs: List[BuildJob] = []
        
        for model in req.models:
            for target in req.targets:
                # Validierung Target
                if not (Path(self.config.targets_dir) / target).exists():
                    state.warnings.append(f"Target '{target}' not found. Skipping.")
                    continue
                
                formats = req.target_formats if req.target_formats else [ModelFormat.GGUF]
                
                for fmt in formats:
                    quants = req.quantization_options if req.quantization_options else [None]
                    
                    for q in quants:
                        job_id = f"{req.request_id}_{len(build_jobs)+1:03d}"
                        
                        out_dir = Path(req.output_base_dir) / target / model.split("/")[-1]
                        if q: out_dir = out_dir / q
                        
                        job = BuildJob(
                            job_id=job_id,
                            source_model=model,
                            target_architecture=target,
                            target_format=fmt,
                            optimization=req.optimization_level,
                            quantization=q,
                            output_path=str(out_dir),
                            status=BuildStatus.PENDING
                        )
                        build_jobs.append(job)

        state.total_builds = len(build_jobs)
        state.status = OrchestrationStatus.BUILDING
        
        self.logger.info(f"Generated {len(build_jobs)} build jobs.")
        
        # 2. Execution Loop
        loop = asyncio.get_running_loop()
        
        for job in build_jobs:
            state.current_stage = f"Building {job.source_model} for {job.target_architecture}"
            
            try:
                # 1. Map Job to Config
                build_config = self._map_job_to_config(job, req)
                
                # 2. Execute Async (Non-Blocking) 
                returned_id = await loop.run_in_executor(
                    None, 
                    self.build_engine.build_model, 
                    build_config
                )
                
                # 3. Poll for Completion
                success = False
                while True:
                    status = self.build_engine.get_build_status(returned_id)
                    if not status: 
                        job.error_log = "Build vanished"
                        break
                    
                    if status.status == BuildStatus.COMPLETED:
                        success = True
                        break
                    if status.status in [BuildStatus.FAILED, BuildStatus.CANCELLED]:
                        job.error_log = "\n".join(status.errors)
                        break
                        
                    await asyncio.sleep(1)
                    
            except Exception as e:
                self.logger.error(f"Execution Error: {e}")
                job.error_log = str(e)
                success = False
            
            # --- SELF-HEALING LOOP ---
            if not success and self.self_healing:
                self.logger.warning(f"Build failed for {job.job_id}. Activating Self-Healing...")
                state.status = OrchestrationStatus.HEALING
                
                error_log = job.error_log if hasattr(job, 'error_log') else "Unknown Error"
                context = f"Target: {job.target_architecture}, Model: {job.source_model}"
                
                proposal = self.self_healing.analyze_error(error_log, context)
                
                if proposal:
                    state.healing_proposal = proposal 
                    self.logger.info(f"Healing Proposal: {proposal.fix_command}")
                else:
                    self.logger.error("Self-Healing found no solution.")
                
                state.status = OrchestrationStatus.BUILDING

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
