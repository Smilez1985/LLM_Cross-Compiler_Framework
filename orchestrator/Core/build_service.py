#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Build Service

Typed facade over the orchestrator, the build engine and the target registry. Every entry point
that is not the GUI's own widgets (MCP server, Ditto's tools, scripts) goes through this class, so
requests are validated once, against the target's declared capabilities, and results have one shape.
"""

import time
from pathlib import Path
from typing import List

from orchestrator.Core.builder import HF_REPO_ID, MODEL_FILE_SUFFIXES, ModelFormat, OptimizationLevel
from orchestrator.Core.deployment_manager import list_builds
from orchestrator.Core.orchestrator import (BuildRequest, OrchestrationStatus, PriorityLevel, WorkflowState,
                                            WorkflowType)
from orchestrator.schemas.build import (ArtifactList, ArtifactOut, BuildHandle, BuildList, BuildRequestIn,
                                        BuildStatusOut, FormatSummary, LogTail, PendingHealing, TargetList,
                                        TargetOut)
from orchestrator.schemas.targets import TargetCapabilities
from orchestrator.utils.helpers import redact_secrets
from orchestrator.utils.logging import get_logger

AUTOMATIC_STRATEGIES = {"config_adjustment", "docker_prune", "retry_build"}


class BuildServiceError(ValueError):
    """A request the service refuses, with a message meant for the user (or the calling model)."""


class BuildService:
    def __init__(self, framework):
        self.framework = framework
        self.logger = get_logger("BuildService")

    # --- helpers ----------------------------------------------------------------------------

    @property
    def _orchestrator(self):
        orch = getattr(self.framework, "orchestrator", None)
        if orch is None:
            raise BuildServiceError("The framework is not initialized (no orchestrator).")
        return orch

    def _targets(self):
        tm = getattr(self.framework, "target_manager", None)
        if tm is None:
            raise BuildServiceError("The framework is not initialized (no target registry).")
        return tm

    def _config_get(self, key: str, default=None):
        get = getattr(self.framework.config, "get", None)
        return get(key, default) if get else getattr(self.framework.config, key, default)

    def _output_dir(self) -> Path:
        return self.framework.resolve_path(self._config_get("output_dir", "output"))

    def resolve_target(self, target: str) -> str:
        """The registered id of a target given by id or folder name (any letter case)."""
        resolve = getattr(self._targets(), "resolve", None)
        tid = resolve(target) if callable(resolve) else target
        if tid is None:
            known = ", ".join(t.get("id", "?") for t in self._targets().list_targets()) or "none"
            raise BuildServiceError(f"Target '{target}' is unknown (targets: {known}).")
        return tid

    def capabilities(self, target: str) -> TargetCapabilities:
        caps = self._targets().get_capabilities(self.resolve_target(target))
        if caps is None:
            known = ", ".join(t.get("id", "?") for t in self._targets().list_targets()) or "none"
            raise BuildServiceError(f"Target '{target}' is unknown or declares no capabilities (targets: {known}).")
        return caps

    @staticmethod
    def _check_model_source(model: str) -> str:
        local = Path(model).expanduser()
        if local.exists():
            local = local.resolve()
            if local.is_dir():
                if not ((local / "config.json").is_file()
                        or any(f.name.lower().endswith(MODEL_FILE_SUFFIXES) for f in local.iterdir() if f.is_file())):
                    raise BuildServiceError(f"'{local}' does not look like a model directory (no config.json or model file).")
            elif not local.name.lower().endswith(MODEL_FILE_SUFFIXES):
                raise BuildServiceError(f"'{local.name}' is not a model file.")
            return str(local)
        if ".." in model or not HF_REPO_ID.match(model):
            raise BuildServiceError(f"'{model}' is neither an existing local model nor a Hugging Face repo id (owner/name).")
        return model

    # --- queries ----------------------------------------------------------------------------

    def list_targets(self) -> TargetList:
        targets = []
        for meta in self._targets().list_targets():
            tid = meta.get("id")
            caps = self._targets().get_capabilities(tid)
            formats = []
            if caps:
                for fid, cap in caps.formats.items():
                    formats.append(FormatSummary(id=fid, label=cap.label, boards=caps.boards_for(fid),
                                                 default_quantization=cap.default_quantization,
                                                 quantizations=list(cap.quantizations)))
            targets.append(TargetOut(id=tid, name=meta.get("name", tid),
                                     architecture=meta.get("architecture_family") or meta.get("architecture"),
                                     support_level=caps.support_level if caps else None,
                                     boards=list(caps.boards) if caps else [], formats=formats))
        return TargetList(targets=targets)

    def _status_of(self, state: WorkflowState) -> BuildStatusOut:
        pending = None
        hp = state.healing_proposal if state.status == OrchestrationStatus.AWAITING_APPROVAL else None
        if hp is not None:
            strategy = getattr(getattr(hp, "strategy", None), "value", "")
            pending = PendingHealing(summary=str(hp.summary), root_cause=str(hp.root_cause),
                                     suggested_step=str(hp.fix_command),
                                     automatic=strategy in AUTOMATIC_STRATEGIES and not hp.is_ai_generated,
                                     ai_generated=bool(hp.is_ai_generated))
        return BuildStatusOut(
            request_id=state.request_id, status=state.status.value, stage=state.current_stage,
            progress_percent=state.progress_percent, total_builds=state.total_builds,
            completed_builds=state.completed_builds, failed_builds=state.failed_builds,
            artifacts=list(state.artifacts), errors=[redact_secrets(e) for e in state.errors],
            warnings=[redact_secrets(w) for w in state.warnings], pending_healing=pending)

    def _state(self, request_id: str) -> WorkflowState:
        state = self._orchestrator._workflows.get(request_id)
        if state is None:
            raise BuildServiceError(f"Unknown build request '{request_id}'.")
        return state

    def get_status(self, request_id: str) -> BuildStatusOut:
        return self._status_of(self._state(request_id))

    def list_requests(self) -> BuildList:
        return BuildList(builds=[self._status_of(s) for s in list(self._orchestrator._workflows.values())])

    def list_artifacts(self) -> ArtifactList:
        def text(value):
            return None if value is None else str(value)

        items = []
        for info in list_builds(self._output_dir()):
            primary = info.get("primary")
            artifacts = info.get("artifacts") if isinstance(info.get("artifacts"), list) else []
            digest = next((a.get("sha256") for a in artifacts if isinstance(a, dict) and a.get("file") == primary), None)
            try:
                items.append(ArtifactOut(path=info["path"], model=text(info.get("model") or info.get("model_source")),
                                         format=text(info.get("format")), quantization=text(info.get("quantization")),
                                         board=text(info.get("board")), target=text(info.get("target")),
                                         created=text(info.get("created")), primary=text(primary), sha256=text(digest)))
            except ValueError as e:  # one odd manifest must not hide every other build
                self.logger.warning(f"Skipping the build manifest in {info.get('path')}: {e}")
        return ArtifactList(artifacts=items)

    def log_tail(self, request_id: str, lines: int = 200) -> LogTail:
        state = self._state(request_id)
        engine = self._orchestrator.build_engine
        collected: List[str] = []
        build_ids = list(dict.fromkeys(state.build_ids))  # each attempt once, in order
        for build_id in build_ids:
            progress = engine.get_build_status(build_id) if hasattr(engine, "get_build_status") else None
            if progress is not None:
                if len(build_ids) > 1:
                    collected.append(f"===== {build_id} =====")
                collected.extend(progress.logs)
        lines = max(1, min(int(lines), 2000))
        return LogTail(request_id=request_id, lines=[redact_secrets(line) for line in collected[-lines:]])

    # --- actions ----------------------------------------------------------------------------

    def validate(self, request: BuildRequestIn) -> BuildRequestIn:
        """Checks a request against the target's capabilities and fills the default quantization."""
        target = self.resolve_target(request.target)
        caps = self.capabilities(target)
        # Canonical spellings, the board the scripts use when none is given, the board's default type.
        fmt, board, quant = caps.normalize(request.format, request.board, request.quantization)
        dataset = None
        if request.dataset_path:
            path = Path(request.dataset_path).expanduser()
            if not path.is_file():
                raise BuildServiceError(f"Calibration dataset '{request.dataset_path}' not found.")
            dataset = str(path.resolve())
        problems = caps.check_request(fmt, board, quant, dataset=bool(dataset), imatrix=request.use_imatrix)
        if problems:
            raise BuildServiceError(" ".join(problems))
        model = self._check_model_source(request.model)
        if request.revision and Path(model).is_absolute():
            raise BuildServiceError("A revision applies to Hugging Face models only, not to a local model.")
        return request.model_copy(update={"target": target, "format": fmt, "board": board, "quantization": quant,
                                          "dataset_path": dataset, "model": model})

    def start_build(self, request: BuildRequestIn) -> BuildHandle:
        req = self.validate(request)
        build_request = BuildRequest(
            request_id="",
            workflow_type=WorkflowType.SIMPLE_CONVERSION,
            priority=PriorityLevel.NORMAL,
            models=[req.model],
            model_revision=req.revision,
            targets=[req.target],
            target_formats=[ModelFormat(req.format)],
            optimization_level=OptimizationLevel.BALANCED,
            quantization_options=[req.quantization] if req.quantization else [],
            parallel_builds=False,
            output_base_dir=str(self._output_dir()),
            description="Build service request",
            use_gpu=req.use_gpu,
            use_imatrix=req.use_imatrix,
            dataset_path=req.dataset_path,
            target_board=req.board,
            max_context_length=req.max_context_length,
        )
        request_id = self.framework.run_coroutine(self._orchestrator.submit_build_request(build_request), timeout=30)
        self.logger.info(f"Build request {request_id} submitted ({req.model} -> {req.target}/{req.format}/{req.quantization})")
        return BuildHandle(request_id=request_id, status=OrchestrationStatus.QUEUED.value)

    def cancel(self, request_id: str) -> BuildStatusOut:
        self._state(request_id)
        if not self.framework.run_coroutine(self._orchestrator.cancel_request(request_id), timeout=60):
            raise BuildServiceError(f"Build request '{request_id}' is not running.")
        return self.get_status(request_id)

    # A build id is added to the request just before the engine registers it (build_model runs in a worker thread,
    # or retries while every build slot is taken). A cancel in between does not reach the engine; the orchestrator
    # cancels the build as soon as it is registered. Until then, for at most this many seconds, it counts as running.
    UNREGISTERED_GRACE = 5.0

    def wait_stopped(self, request_id: str, timeout: float, poll: float = 0.2) -> str:
        """After cancel(): waits until no build of the request runs in the engine any more (its container is
        stopped and removed, staged files are discarded). Returns "cancelled" when every build that ran ended
        cancelled, "finished" when one had already settled and ended on its own (completed or failed), and
        "running" when it has not stopped within `timeout` seconds."""
        engine = self._orchestrator.build_engine
        active = getattr(engine, "is_active", None)
        known = getattr(engine, "get_build_status", None)
        start = time.monotonic()
        deadline = start + max(float(timeout), 0.0)
        while True:
            build_ids = list(self._state(request_id).build_ids)
            running = callable(active) and any(active(bid) for bid in build_ids)
            unregistered = (callable(known) and time.monotonic() - start < self.UNREGISTERED_GRACE
                            and any(known(bid) is None for bid in build_ids))
            if not running and not unregistered:
                break
            if time.monotonic() >= deadline:
                return "running"
            time.sleep(poll)
        for bid in build_ids:
            progress = engine.get_build_status(bid) if hasattr(engine, "get_build_status") else None
            outcome = getattr(getattr(progress, "status", None), "value", None)
            if outcome in ("completed", "failed"):
                return "finished"
        return "cancelled"

    def resolve_healing(self, request_id: str, approve: bool) -> BuildStatusOut:
        state = self._state(request_id)
        if state.status != OrchestrationStatus.AWAITING_APPROVAL or state.healing_proposal is None:
            raise BuildServiceError(f"Build request '{request_id}' has no pending healing proposal.")
        status = self._status_of(state)
        if approve and status.pending_healing and not status.pending_healing.automatic:
            raise BuildServiceError("This proposal is a manual step; it cannot be applied automatically. Dismiss it instead.")
        if not self._orchestrator.resolve_healing(request_id, approve):
            raise BuildServiceError(f"The healing proposal of '{request_id}' is no longer pending.")
        return self.get_status(request_id)
