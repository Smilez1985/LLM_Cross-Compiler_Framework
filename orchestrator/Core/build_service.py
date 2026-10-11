#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Build Service

Typed facade over the orchestrator, the build engine and the target registry. Every entry point
that is not the GUI's own widgets (MCP server, Ditto's tools, scripts) goes through this class, so
requests are validated once, against the target's declared capabilities, and results have one shape.
"""

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from orchestrator.Core import job_service
from orchestrator.Core.builder import HF_REPO_ID, MODEL_FILE_SUFFIXES, ModelFormat, OptimizationLevel
from orchestrator.Core.job_service import owner_actor
from orchestrator.Core.deployment_manager import list_builds
from orchestrator.Core.orchestrator import (BuildQueueFull, BuildRequest, OrchestrationStatus, PriorityLevel,
                                            WorkflowState, WorkflowType)
from orchestrator.schemas.build import (ArtifactList, ArtifactOut, BuildHandle, BuildList, BuildRequestIn,
                                        BuildStatusOut, FormatSummary, LogTail, PendingHealing, TargetList,
                                        TargetOut)
from orchestrator.schemas.targets import TargetCapabilities
from orchestrator.tools.local_files import (LocalFileError, LocalFiles, LocalInputs, is_path, labels_hint,
                                            looks_like_reference, split_reference)
from orchestrator.utils.helpers import redact_secrets
from orchestrator.utils.logging import get_logger

AUTOMATIC_STRATEGIES = {"config_adjustment", "docker_prune", "retry_build"}
MODEL_NEXT = ("Give a Hugging Face repo id (owner/name), or a local model file or a folder with config.json as "
              "<label>:<relative path>.")
BUILD_FINISHED = frozenset({"completed", "error", "cancelled"})


def proposal_fingerprint(proposal: Any) -> str:
    """Identifies one pending healing proposal: its texts, strategy and origin, and the proposal itself (a new
    proposal with the same text is another proposal). A question shows it; the decision must be about it."""
    strategy = getattr(getattr(proposal, "strategy", None), "value", "")
    data = [str(getattr(proposal, "summary", "")), str(getattr(proposal, "root_cause", "")),
            str(getattr(proposal, "fix_command", "")), strategy, bool(getattr(proposal, "is_ai_generated", False)),
            id(proposal)]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode("utf-8", "surrogatepass")).hexdigest()


class BuildServiceError(ValueError):
    """A request the service refuses, with a message meant for the user (or the calling model), a reason code
    (the tool registry's codes, e.g. unknown_id, not_supported) and the next step."""

    def __init__(self, message: str, code: str = "failed", next: str = ""):
        super().__init__(message)
        self.code, self.next = code, next


class BuildService:
    def __init__(self, framework):
        self.framework = framework
        self.logger = get_logger("BuildService")

    # --- helpers ----------------------------------------------------------------------------

    @property
    def _orchestrator(self):
        orch = getattr(self.framework, "orchestrator", None)
        if orch is None:
            raise BuildServiceError("The framework is not initialized (no orchestrator).", next="Tell the person the framework is not ready; 'llm-cli doctor' shows why.")
        return orch

    def _targets(self):
        tm = getattr(self.framework, "target_manager", None)
        if tm is None:
            raise BuildServiceError("The framework is not initialized (no target registry).", next="Tell the person the framework is not ready; 'llm-cli doctor' shows why.")
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
            raise BuildServiceError(f"Target '{target}' is unknown (targets: {known}).", code="invalid_value",
                                    next="Call list_targets for the targets you can use.")
        return tid

    def target_choices(self):
        """(target ids, {folder name: id} for folders named differently): the names a request may use."""
        tm = self._targets()
        ids = [t.get("id") for t in tm.list_targets() if t.get("id")]
        folder_of = getattr(tm, "folder_of", None)
        aliases = {}
        for tid in ids:
            folder = folder_of(tid) if callable(folder_of) else None
            if folder is not None and folder.name != tid:
                aliases[folder.name] = tid
        return ids, aliases

    def capabilities(self, target: str) -> TargetCapabilities:
        caps = self._targets().get_capabilities(self.resolve_target(target))
        if caps is None:
            known = ", ".join(t.get("id", "?") for t in self._targets().list_targets()) or "none"
            raise BuildServiceError(f"Target '{target}' is unknown or declares no capabilities (targets: {known}).",
                                    code="invalid_value", next="Call list_targets for the targets you can use.")
        return caps

    def _confined(self, request: BuildRequestIn) -> Tuple[BuildRequestIn, Optional[LocalInputs]]:
        """AI callers name a local model or dataset only by a local-file reference inside the folders the AI may
        use (G8); a path is refused, and a Hugging Face repo id is never looked up on disk. Returns the request with
        the confined real paths and their pins."""
        model, dataset = request.model, request.dataset_path
        files = None

        def local() -> LocalFiles:
            nonlocal files
            files = files or LocalFiles.for_framework(self.framework)
            return files

        model_pin = local().resolve(model) if looks_like_reference(model) else None
        if model_pin is None and (".." in model or not HF_REPO_ID.match(model)):
            try:
                labels = [f.label for f in local().folders() if not f.problem]
            except LocalFileError:
                labels = []
            if is_path(model):
                split_reference(model, labels)  # refused with the reason: AI callers never name a path
            raise LocalFileError(f"'{model}' is neither a Hugging Face repo id (owner/name) nor a local file "
                                 "reference.", next_step=labels_hint(labels))
        dataset_pin = local().resolve(dataset) if dataset else None
        if dataset_pin is not None and dataset_pin.is_dir:
            raise LocalFileError(f"{dataset_pin.ref} is a folder; a calibration dataset is a file (for RKNN the "
                                 "sample list).", next_step="name the dataset file inside its folder.")
        update = {"model": model_pin.path if model_pin else model,
                  "dataset_path": dataset_pin.path if dataset_pin else None}
        # Always present for an AI caller, also without pins: the build engine then never looks the model source up
        # on disk (a repo id that happens to name a folder below the working directory stays a repo id).
        return request.model_copy(update=update), LocalInputs(model_pin, dataset_pin)

    @staticmethod
    def _check_model_source(model: str, shown: Optional[str] = None) -> str:
        """`shown` names the model in errors instead of its path (an AI caller's `<label>:<relative path>`)."""
        local = Path(model).expanduser()
        if local.exists():
            local = local.resolve()
            if local.is_dir():
                if not ((local / "config.json").is_file()
                        or any(f.name.lower().endswith(MODEL_FILE_SUFFIXES) for f in local.iterdir() if f.is_file())):
                    raise BuildServiceError(f"'{shown or local}' does not look like a model directory (no config.json "
                                            "or model file).", code="invalid_value", next=MODEL_NEXT)
            elif not local.name.lower().endswith(MODEL_FILE_SUFFIXES):
                raise BuildServiceError(f"'{shown or local.name}' is not a model file.", code="invalid_value",
                                        next=MODEL_NEXT)
            return str(local)
        if ".." in model or not HF_REPO_ID.match(model):
            raise BuildServiceError(f"'{model}' is neither an existing local model nor a Hugging Face repo id "
                                    "(owner/name).", code="invalid_value", next=MODEL_NEXT)
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
                                     manual_step_text=str(hp.fix_command),
                                     automatic=strategy in AUTOMATIC_STRATEGIES and not hp.is_ai_generated,
                                     ai_generated=bool(hp.is_ai_generated))
        return BuildStatusOut(
            request_id=state.request_id, status=state.status.value, stage=state.current_stage,
            progress_percent=state.progress_percent, total_builds=state.total_builds,
            completed_builds=state.completed_builds, failed_builds=state.failed_builds,
            artifacts=list(state.artifacts), errors=[redact_secrets(e) for e in state.errors],
            warnings=[redact_secrets(w) for w in state.warnings], pending_healing=pending,
            owner=owner_actor(getattr(state, "owner", "")) or None)

    def _state(self, request_id: str) -> WorkflowState:
        state = self._orchestrator._workflows.get(request_id)
        if state is None:
            raise BuildServiceError(f"Unknown build request '{request_id}'.", code="unknown_id",
                                    next="Call list_build_requests for the ids you can use.")
        return state

    def get_status(self, request_id: str) -> BuildStatusOut:
        return self._status_of(self._state(request_id))

    def list_requests(self) -> BuildList:
        return BuildList(builds=[self._status_of(s) for s in list(self._orchestrator._workflows.values())])

    def request_label(self, request_id: str) -> Optional[str]:
        """The speaking name of a build request for a person: the model (a local model by its label) and the
        target, format and quantization; None for an unknown request."""
        try:
            state = self._orchestrator._workflows.get(request_id)
        except BuildServiceError:
            return None
        return (getattr(state, "label", "") or None) if state is not None else None

    # --- ownership and waiting --------------------------------------------------------------------------------

    def owner_of(self, request_id: str) -> Optional[str]:
        """The caller that started a build through a tool (`mcp#<session>`, `ditto`), or None."""
        try:
            state = self._orchestrator._workflows.get(request_id)
        except BuildServiceError:
            return None
        return (getattr(state, "owner", "") or None) if state is not None else None

    def active_requests(self, owner: str) -> List[str]:
        """The unfinished builds `owner` started (a disconnected MCP session's are cancelled)."""
        try:
            items = list(self._orchestrator._workflows.items())
        except BuildServiceError:
            return []
        return [rid for rid, state in items
                if getattr(state, "owner", "") == owner and state.status.value not in BUILD_FINISHED]

    def _change_key(self, request_id: str):
        s = self.get_status(request_id)
        return (s.status, s.stage, s.progress_percent, s.completed_builds, s.failed_builds, len(s.errors),
                len(s.warnings), s.pending_healing is not None)

    def wait_for_change(self, request_id: str, timeout: float, poll: float = 0.25) -> bool:
        """Blocks until the build's status, stage, progress or healing proposal changes, or it has ended, at most
        `timeout` seconds (capped at 50). For a caller in its own thread (Ditto). True when something changed."""
        start = self._change_key(request_id)
        if str(start[0]).lower() in BUILD_FINISHED:
            return False
        deadline = time.monotonic() + max(0.0, min(float(timeout), job_service.MAX_WAIT_SECONDS))
        while time.monotonic() < deadline:
            time.sleep(min(poll, max(0.0, deadline - time.monotonic())))
            if self._change_key(request_id) != start:
                return True
        return False

    async def wait_for_change_async(self, request_id: str, timeout: float, report=None, poll: float = 0.25) -> bool:
        """wait_for_change() on the event loop, holding no worker thread (MCP long-poll), with a progress
        notification every few seconds through `report` (sent only when the client gave a progress token).
        Cancelling the wait never touches the build."""
        import anyio
        start = self._change_key(request_id)
        if str(start[0]).lower() in BUILD_FINISHED:
            return False
        now = time.monotonic()
        deadline = now + max(0.0, min(float(timeout), job_service.MAX_WAIT_SECONDS))
        next_report = now + job_service.PROGRESS_EVERY
        while time.monotonic() < deadline:
            await anyio.sleep(min(poll, max(0.0, deadline - time.monotonic())))
            key = self._change_key(request_id)
            if key != start:
                return True
            if report is not None and time.monotonic() >= next_report:
                next_report = time.monotonic() + job_service.PROGRESS_EVERY
                try:  # framework facts only: status and percent, never stage text (it may quote a proposal)
                    await report(key[2], 100, f"{key[0]} {key[2]}%")
                except Exception:
                    pass
        return False

    def _shown_model(self, value: Any) -> Optional[str]:
        """A manifest's model as an AI caller sees it: a Hugging Face repo id or a label as written; a host path (a
        local model of an older build, or one a person built) as `<label>:<relative path>` when it lies in a folder
        the AI may use, else as its file name only, never the folders above it."""
        if value is None:
            return None
        text = str(value)
        if not Path(text).is_absolute():
            return text
        try:
            label = LocalFiles.for_framework(self.framework).label_for(text)
        except (LocalFileError, OSError, AttributeError, TypeError):
            label = None
        return label or Path(text).name

    def list_artifacts(self) -> ArtifactList:
        def text(value):
            return None if value is None else str(value)

        items = []
        for info in list_builds(self._output_dir()):
            primary = info.get("primary")
            artifacts = info.get("artifacts") if isinstance(info.get("artifacts"), list) else []
            digest = next((a.get("sha256") for a in artifacts if isinstance(a, dict) and a.get("file") == primary), None)
            try:
                items.append(ArtifactOut(path=info["path"],
                                         model=self._shown_model(info.get("model") or info.get("model_source")),
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

    def validate(self, request: BuildRequestIn, *, local_paths: bool = False) -> BuildRequestIn:
        """Checks a request against the target's capabilities and fills the default quantization. A local model
        or dataset must be a local-file reference inside the folders the AI may use, unless `local_paths` is set
        for a person's own caller (GUI, CLI, a script), which may name any path."""
        return self._validated(request, local_paths)[0]

    def _validated(self, request: BuildRequestIn, local_paths: bool) -> Tuple[BuildRequestIn, Optional[LocalInputs]]:
        target = self.resolve_target(request.target)
        caps = self.capabilities(target)
        # Canonical spellings, the board the scripts use when none is given, the board's default type.
        fmt, board, quant = caps.normalize(request.format, request.board, request.quantization)
        inputs = None
        if not local_paths:
            request, inputs = self._confined(request)
        dataset = None
        if request.dataset_path:
            path = Path(request.dataset_path).expanduser()
            if not path.is_file():
                raise BuildServiceError(f"Calibration dataset '{request.dataset_path}' not found.",
                                        code="invalid_value",
                                        next="Name an existing dataset file as <label>:<relative path>.")
            dataset = str(path.resolve())
        problems = caps.check_request(fmt, board, quant, dataset=bool(dataset), imatrix=request.use_imatrix)
        if problems:
            raise BuildServiceError(" ".join(problems), code="not_supported",
                                    next=f"Call get_target_capabilities for {target}, then choose a supported "
                                         "combination.")
        if local_paths or getattr(inputs, "model", None) is not None:
            model = self._check_model_source(request.model, None if local_paths else inputs.model.ref)
        else:
            model = request.model  # a Hugging Face repo id: never looked up on disk for an AI caller
        if request.revision and Path(model).is_absolute():
            raise BuildServiceError("A revision applies to Hugging Face models only, not to a local model.",
                                    code="invalid_value", next="Leave out the revision for a local model.")
        return request.model_copy(update={"target": target, "format": fmt, "board": board, "quantization": quant,
                                          "dataset_path": dataset, "model": model}), inputs

    def start_build(self, request: BuildRequestIn, *, local_paths: bool = False, owner: str = "") -> BuildHandle:
        """Validates and queues a build. `owner` is the tool caller that starts it (ToolContext.owner)."""
        req, inputs = self._validated(request, local_paths)
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
            local_inputs=inputs,
            owner=owner,
        )
        try:
            request_id = self.framework.run_coroutine(self._orchestrator.submit_build_request(build_request),
                                                      timeout=30)
        except BuildQueueFull as e:
            raise BuildServiceError(str(e), code="limit_reached",
                                    next="Wait until a build finishes (get_build_status with wait_seconds) or cancel "
                                         "one of your builds, then call start_build again.") from None
        self.logger.info(f"Build request {request_id} submitted ({req.model} -> {req.target}/{req.format}/{req.quantization})")
        return BuildHandle(request_id=request_id, status=OrchestrationStatus.QUEUED.value)

    def cancel(self, request_id: str) -> BuildStatusOut:
        self._state(request_id)
        if not self.framework.run_coroutine(self._orchestrator.cancel_request(request_id), timeout=60):
            raise BuildServiceError(f"Build request '{request_id}' is not running.", code="failed",
                                    next="It has already ended; get_build_status shows how.")
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

    def _pending_proposal(self, request_id: str):
        state = self._state(request_id)
        proposal = state.healing_proposal if state.status == OrchestrationStatus.AWAITING_APPROVAL else None
        if proposal is None:
            raise BuildServiceError(f"Build request '{request_id}' has no pending healing proposal.", code="failed",
                                    next="Call get_build_status to see the build's state; only a build that waits "
                                         "for a decision has a proposal.")
        return state, proposal

    def healing_proposal(self, request_id: str, approve: bool) -> Dict[str, Any]:
        """The pending healing proposal as the question about it shows it (texts redacted; they come from the
        build log or an AI and are external text) with its fingerprint. Refuses an approval of a manual step
        before anything is asked."""
        state, proposal = self._pending_proposal(request_id)
        pending = self._status_of(state).pending_healing
        if approve and pending is not None and not pending.automatic:
            raise BuildServiceError("This proposal is a manual step; it cannot be applied automatically.",
                                    code="not_supported",
                                    next="Dismiss it (approve: false) and show the person the manual step.")
        return {"summary": redact_secrets(str(proposal.summary)), "root_cause": redact_secrets(str(proposal.root_cause)),
                "fix": redact_secrets(str(proposal.fix_command)),
                "strategy": getattr(getattr(proposal, "strategy", None), "value", "") or "unknown",
                "automatic": bool(pending.automatic) if pending else False,
                "ai_generated": bool(proposal.is_ai_generated), "fingerprint": proposal_fingerprint(proposal),
                "request": getattr(state, "label", "") or ""}

    def resolve_healing(self, request_id: str, approve: bool, expect: Optional[str] = None) -> BuildStatusOut:
        """The decision on the pending proposal. `expect`: the fingerprint of the proposal the person was shown;
        a different pending proposal is refused, nothing is applied."""
        state, proposal = self._pending_proposal(request_id)
        if expect is not None and proposal_fingerprint(proposal) != expect:
            raise BuildServiceError(f"The self-healing proposal of '{request_id}' changed after the question was "
                                    "asked; nothing was applied.", code="failed",
                                    next="Call get_build_status for the new proposal and ask the person again.")
        status = self._status_of(state)
        if approve and status.pending_healing and not status.pending_healing.automatic:
            raise BuildServiceError("This proposal is a manual step; it cannot be applied automatically.",
                                    code="not_supported",
                                    next="Dismiss it (approve: false) and show the person the manual step.")
        if not self._orchestrator.resolve_healing(request_id, approve, expected=proposal):
            raise BuildServiceError(f"The healing proposal of '{request_id}' is no longer pending; nothing was "
                                    "applied.", code="failed", next="Call get_build_status to see the build's state.")
        return self.get_status(request_id)
