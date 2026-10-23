#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Build Service

Typed facade over the orchestrator, the build engine and the target registry. It is the one build-submission
path: the CLI's `build start`, the Builder tab's Start Build, the MCP server, Ditto's tools and scripts all queue a
build through start_build (start_recipe for an installed community recipe), so requests are validated once, against
the target's declared capabilities, and results have one shape. The person's own callers (local_paths=True) may name
any path, choose the priority and the folder the build is saved to, and build a target without capabilities
unchecked; AI callers may not.
"""

import hashlib
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from orchestrator.Core import job_service
from orchestrator.Core.builder import HF_REPO_ID, MODEL_FILE_SUFFIXES, ModelFormat, OptimizationLevel
from orchestrator.Core.job_service import owner_actor
from orchestrator.Core.orchestrator import (BuildQueueFull, BuildRequest, OrchestrationStatus, PriorityLevel,
                                            WorkflowState, WorkflowType)
from orchestrator.schemas.build import (ArtifactDetails, ArtifactFile, ArtifactList, ArtifactOut, BuildHandle,
                                        BuildList, BuildRequestIn, BuildStatusOut, ClientProposalOut,
                                        FormatSummary, LogTail, PendingHealing, RequestSummary, TargetList,
                                        TargetOut)
from orchestrator.schemas.targets import TargetCapabilities
from orchestrator.tools.local_files import (LocalFileError, LocalFiles, LocalInputs, is_path, labels_hint,
                                            looks_like_reference, split_reference)
from orchestrator.Core.self_healing_manager import AUTOMATIC_STRATEGIES
from orchestrator.utils.any_path import is_absolute_anywhere, last_part
from orchestrator.utils.helpers import digest_matches, redact_secrets, sha256_file
from orchestrator.utils.logging import get_logger
from orchestrator.utils.next_steps import WithSteps

MODEL_NEXT = ("Give a Hugging Face repo id (owner/name), or a local model file or a folder with config.json as "
              "<label>:<relative path>.")
BUILD_FINISHED = frozenset({"completed", "error", "cancelled"})
PAGE_SIZE = 50  # entries per page of a list (design 6.5)
LOG_MAX_LINES = 1000
LOG_LINE_CHARS = 2000
LOG_TOTAL_CHARS = 48000  # so Claude Code keeps a log inline (it files results over 50,000 characters)
ARTIFACT_ID = re.compile(r"^a_[0-9a-f]{16}$")
MAX_CARD_CHARS = 20000
MAX_ARTIFACT_FILES = 200
# Engine statuses of a build record (another process's build) as request statuses
RECORD_STATUS = {"failed": "error", "queued": "queued", "completed": "completed", "cancelled": "cancelled",
                 "interrupted": "interrupted"}
# Request statuses a shared summary (request_store) may carry
SHARED_STATUSES = ("queued", "preparing", "building", "healing", "awaiting_approval", "completed", "error",
                   "cancelled", "interrupted")
INTERRUPTED_TEXT = "The process that ran this build ended before the build finished."
NOT_READY_NEXT = ("Tell the person the framework is not ready; run_mojo_oracle (or 'llmcf mojo' in a terminal) shows "
                  "why.")


def artifact_id(relative: str) -> str:
    """A built model's id: stable for its folder under the output folder, never the folder's path."""
    return "a_" + hashlib.sha256(relative.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def proposal_fingerprint(proposal: Any) -> str:
    """Identifies one pending healing proposal: its texts, strategy and origin, and the proposal itself (a new
    proposal with the same text is another proposal). A question shows it; the decision must be about it."""
    strategy = getattr(getattr(proposal, "strategy", None), "value", "")
    data = [str(getattr(proposal, "summary", "")), str(getattr(proposal, "root_cause", "")),
            str(getattr(proposal, "fix_command", "")), strategy, bool(getattr(proposal, "is_ai_generated", False)),
            id(proposal)]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode("utf-8", "surrogatepass")).hexdigest()


class BuildServiceError(WithSteps, ValueError):
    """A request the service refuses, with a message meant for the user (or the calling model), a reason code
    (the tool registry's codes, e.g. unknown_id, not_supported), the next step and, for an input refusal, the
    argument it is about (Ditto's loop tells a fix of another argument from a repeat by it)."""

    def __init__(self, message: str, code: str = "failed", next: str = "", field: str = ""):
        super().__init__(message)
        self.code, self.next, self.field = code, next, field


class BuildService:
    def __init__(self, framework, orchestrator=None):
        """`orchestrator`: the orchestrator to queue builds on and read them from when it is not the framework's own
        (the CLI passes the one its command context holds; in a running framework it is the same object)."""
        self.framework = framework
        self._own_orchestrator = orchestrator
        self.logger = get_logger("BuildService")

    # --- helpers ----------------------------------------------------------------------------

    @property
    def _orchestrator(self):
        orch = self._own_orchestrator
        if orch is None:
            orch = getattr(self.framework, "orchestrator", None)
        if orch is None:
            raise BuildServiceError("The framework is not initialized (no orchestrator).", next=NOT_READY_NEXT)
        return orch

    def _targets(self):
        tm = getattr(self.framework, "target_manager", None)
        if tm is None:
            raise BuildServiceError("The framework is not initialized (no target registry).", next=NOT_READY_NEXT)
        return tm

    def _config_get(self, key: str, default=None):
        get = getattr(self.framework.config, "get", None)
        return get(key, default) if get else getattr(self.framework.config, key, default)

    def _output_dir(self) -> Path:
        return self.framework.resolve_path(self._config_get("output_dir", "output"))

    def output_folder(self) -> Path:
        """The output folder finished builds are written to (resolved from the settings on every call)."""
        return self._output_dir()

    def artifact_roots(self) -> List[Tuple[str, Path]]:
        """(label, folder) of the folders built models lie in: the output folder, and the framework's build folder
        when it is another folder (artifact_output)."""
        from orchestrator.Core.artifact_output import roots
        return roots(self.framework)

    def artifact_entries(self) -> List[Tuple[str, str, Dict[str, Any]]]:
        """(artifact id, where:<relative path>, manifest) of every finished build in the output folder, the build folder
        and the folders builds were saved to, the entries list_artifacts shows (the manifest is the build engine's,
        its values untrusted)."""
        return self._artifacts()

    def artifact_store(self):
        """The deletion side of the built models (ArtifactStore), on this service's public methods only."""
        from orchestrator.Core.artifact_store import ArtifactStore
        return ArtifactStore.for_service(self)

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
                                    code="invalid_value", next="Call list_targets for the targets you can use.",
                                    field="target")
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

        field = "model"  # the argument a refusal is about (Ditto's loop tells a fix from a repeat by it)
        try:
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
            field = "dataset_path"
            dataset_pin = local().resolve(dataset) if dataset else None
            if dataset_pin is not None and dataset_pin.is_dir:
                raise LocalFileError(f"{dataset_pin.ref} is a folder; a calibration dataset is a file (for RKNN the "
                                     "sample list).", next_step="name the dataset file inside its folder.")
        except LocalFileError as e:
            e.field = field
            raise
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
                                            "or model file).", code="invalid_value", next=MODEL_NEXT,
                                            field="model")
            elif not local.name.lower().endswith(MODEL_FILE_SUFFIXES):
                raise BuildServiceError(f"'{shown or local.name}' is not a model file.", code="invalid_value",
                                        next=MODEL_NEXT, field="model")
            return str(local)
        if ".." in model or not HF_REPO_ID.match(model):
            raise BuildServiceError(f"'{shown or model}' is neither an existing local model nor a Hugging Face repo "
                                    "id (owner/name).", code="invalid_value", next=MODEL_NEXT, field="model")
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
        stage, percent = self._live_progress(state)
        return BuildStatusOut(
            request_id=state.request_id, status=state.status.value, stage=stage,
            progress_percent=percent, total_builds=state.total_builds,
            completed_builds=state.completed_builds, failed_builds=state.failed_builds,
            # where each finished build lies, by label (output:<relative path>); the person's own windows show the path
            artifacts=[self.artifact_label(a) for a in state.artifacts],
            errors=[redact_secrets(e) for e in state.errors],
            warnings=[redact_secrets(w) for w in state.warnings], pending_healing=pending,
            client_proposals=[ClientProposalOut(
                proposal_id=p.proposal_id, summary=p.summary, root_cause=p.root_cause, manual_step_text=p.manual_step,
                confidence=p.confidence, where=p.where, proposed_by=p.proposed_by, client=p.client,
                created=p.created) for p in getattr(state, "client_proposals", [])],
            owner=owner_actor(getattr(state, "owner", "")) or None,
            request=RequestSummary(**getattr(state, "summary", {})) if getattr(state, "summary", None) else None)

    _RUNNING = (OrchestrationStatus.PREPARING, OrchestrationStatus.BUILDING, OrchestrationStatus.HEALING)

    def _live_progress(self, state: WorkflowState) -> Tuple[str, int]:
        """The request's stage and percent with its running build counted in: the build engine's own step and percent
        (builder.py BuildProgress), so one build of one request moves 10, 20, 40 ... instead of 0 % until it ends.
        A build that ended adds nothing; a request that still runs never says 100 %."""
        stage, percent = state.current_stage, state.progress_percent
        if state.status not in self._RUNNING or not state.total_builds or not state.build_ids:
            return stage, percent
        try:
            build = self._orchestrator.build_engine.get_build_status(state.build_ids[-1])
        except Exception:  # noqa: BLE001 - no engine progress: the request's own values
            return stage, percent
        status = getattr(getattr(build, "status", None), "value", None)
        if build is None or status in (None, "completed", "failed", "cancelled"):
            return stage, percent
        part = max(0, min(99, int(getattr(build, "progress_percent", 0) or 0)))
        ended = state.completed_builds + state.failed_builds
        percent = max(percent, min(99, (ended * 100 + part) // state.total_builds))
        step = str(getattr(build, "current_stage", "") or "")
        return (f"{stage}: {step}" if stage and step else stage or step), percent

    def live_resources(self, request_id: str) -> Optional[Dict[str, float]]:
        """The CPU and memory of the request's running build container (cpu_percent, memory_bytes,
        memory_limit_bytes; BuildEngine samples them while a stage container runs), or None while none runs or for a
        request of another process. For the person's Builder under the progress bar (DECISIONS round 12, item 6);
        not part of the AI's build status. Read from the orchestrator this service is bound to, like its status."""
        try:
            orchestrator = self._orchestrator
        except BuildServiceError:   # the framework is not booted: no build runs here
            return None
        state = orchestrator._workflows.get(request_id)
        if state is None or state.status not in self._RUNNING or not state.build_ids:
            return None
        try:
            build = orchestrator.build_engine.get_build_status(state.build_ids[-1])
        except Exception:  # noqa: BLE001 - no engine: nothing to show
            return None
        resources = getattr(build, "resources", None)
        return dict(resources) if isinstance(resources, dict) else None

    def _state(self, request_id: str) -> WorkflowState:
        state = self._orchestrator._workflows.get(request_id)
        if state is None:
            if self._record_of(request_id) is not None:
                raise BuildServiceError(f"Build request '{request_id}' was started by another framework process (the "
                                        "GUI, the CLI or another MCP server); only its status can be read here.",
                                        code="not_supported", next="Read it with get_build_status; the person "
                                                                   "stops it where it was started.")
            raise BuildServiceError(f"Unknown build request '{request_id}'.", code="unknown_id",
                                    next="Call list_build_requests for the ids you can use.")
        return state

    def get_status(self, request_id: str) -> BuildStatusOut:
        try:
            state = self._orchestrator._workflows.get(request_id)
        except BuildServiceError:
            state = None
        if state is None:
            record = self._record_of(request_id)
            if record is not None:
                return record
        return self._status_of(self._state(request_id))

    # --- builds of other processes (their build records, design T11) ---------------------------------------------

    def _records(self) -> List[Dict[str, Any]]:
        try:
            from orchestrator.Core.build_records import read_records, records_dir
            return read_records(records_dir(self.framework.resolve_path(self._config_get("cache_dir", "cache"))))
        except Exception:  # noqa: BLE001 - no cache folder (a stand-in framework): nothing to show
            return []

    def _shared_requests(self) -> List[Dict[str, Any]]:
        """The request summaries of the shared build state (request_store, per-user folder), newest first."""
        try:
            from orchestrator.Core.request_store import for_config
            store = for_config(self.framework.config)
            return store.read() if store is not None else []
        except Exception:  # noqa: BLE001 - no per-user folder (a stand-in framework): nothing to show
            return []

    def _other_builds(self) -> Dict[str, BuildStatusOut]:
        """Build requests of other framework processes, read-only: first from the shared build state (every
        request, also one still queued there), then from the build engines' records for builds without a summary
        (an older framework version). The model as a repo id or label, never a host path; errors as external text."""
        try:
            own = set(self._orchestrator._workflows)
        except BuildServiceError:
            own = set()
        found: Dict[str, BuildStatusOut] = {}
        for record in self._shared_requests():  # newest first
            rid = str(record.get("request_id") or "")
            if not rid or rid in own or rid in found or not re.fullmatch(r"[\w.-]{1,64}", rid):
                continue
            found[rid] = self._shared_status(rid, record)
        for record in self._records():  # newest first
            rid = str(record.get("request_id") or record.get("build_id") or "")
            if not rid or rid in own or rid in found or not re.fullmatch(r"[\w.-]{1,64}", rid):
                continue
            status = RECORD_STATUS.get(str(record.get("status")), "building")
            error = record.get("error")
            try:
                progress = int(record.get("progress") or 0)
            except (TypeError, ValueError):
                progress = 0
            found[rid] = BuildStatusOut(
                request_id=rid, status=status, stage=str(record.get("stage") or ""), progress_percent=progress,
                total_builds=1, completed_builds=1 if status == "completed" else 0,
                failed_builds=1 if status in ("error", "interrupted") else 0,
                errors=[redact_secrets(str(error))] if error else [],
                warnings=([INTERRUPTED_TEXT] if status == "interrupted" else []),
                request=RequestSummary(model=self._shown_model(record.get("model")), target=record.get("target"),
                                       format=record.get("format"), board=record.get("board"),
                                       quantization=record.get("quantization")),
                process="other")
        return found

    def _shared_status(self, rid: str, record: Dict[str, Any]) -> BuildStatusOut:
        """One summary of the shared build state as a read-only build status."""
        def number(key: str) -> int:
            try:
                return max(0, int(record.get(key) or 0))
            except (TypeError, ValueError):
                return 0

        status = str(record.get("status") or "")
        if status not in SHARED_STATUSES:
            status = "interrupted"
        warnings = [redact_secrets(str(w)) for w in (record.get("warnings") or [])[-5:] if isinstance(w, str)]
        if status == "interrupted":
            warnings.append(INTERRUPTED_TEXT)
        elif record.get("awaiting_decision"):
            warnings.append(f"Waits for the person's decision on a self-healing proposal in the {self._program(record)}"
                            " that started it.")
        summary = record.get("summary") if isinstance(record.get("summary"), dict) else {}
        owner = str(record.get("owner") or "")
        return BuildStatusOut(
            request_id=rid, status=status, stage=str(record.get("stage") or ""),
            progress_percent=min(100, number("progress")), total_builds=number("total_builds"),
            completed_builds=number("completed_builds"), failed_builds=number("failed_builds"),
            errors=[redact_secrets(str(e)) for e in (record.get("errors") or [])[-5:] if isinstance(e, str)],
            warnings=warnings, owner=owner if re.fullmatch(r"[\w:.-]{1,80}", owner) else None,
            request=RequestSummary(model=self._shown_model(summary.get("model")), target=summary.get("target"),
                                   format=summary.get("format"), board=summary.get("board"),
                                   quantization=summary.get("quantization"),
                                   optimization=summary.get("optimization"), revision=summary.get("revision")),
            process="other", started_in=self._program(record))

    @staticmethod
    def _program(record: Dict[str, Any]) -> str:
        from orchestrator.Core.request_store import PROGRAMS
        kind = str(record.get("program") or "")
        return {"gui": "GUI", "cli": "CLI", "mcp": "MCP server", "api": "API server"}.get(
            kind if kind in PROGRAMS else "", "framework process")

    def _record_of(self, request_id: str) -> Optional[BuildStatusOut]:
        return self._other_builds().get(request_id)

    def list_requests(self, status: Optional[str] = None, page: int = 1) -> BuildList:
        """This process's build requests, then those of other processes (from their records), 50 per page."""
        try:
            own = [self._status_of(s) for s in list(self._orchestrator._workflows.values())]
        except BuildServiceError:
            own = []
        builds = own + list(self._other_builds().values())
        if status:
            builds = [b for b in builds if b.status.lower() == status.lower()]
        page = max(1, int(page))
        start = (page - 1) * PAGE_SIZE
        return BuildList(builds=builds[start:start + PAGE_SIZE], page=page, more=len(builds) > start + PAGE_SIZE)

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
        if not is_absolute_anywhere(text):  # a path of either system: a Linux build's on a Windows host, too
            return text
        try:
            label = LocalFiles.for_framework(self.framework).label_for(text)
        except (LocalFileError, OSError, AttributeError, TypeError):
            label = None
        return label or last_part(text)

    def _artifacts(self) -> List[Tuple[str, str, Dict[str, Any]]]:
        """(artifact id, where:<relative path>, manifest) of every finished build: output:<path> in the output folder,
        builds:<path> in the framework's build folder (not saved yet), saved:<path> in a folder the person saved it to
        (artifact_output.list_artifacts). Never a host path."""
        from orchestrator.Core.artifact_output import list_artifacts
        return [(artifact_id(item.key), item.label, item.info) for item in list_artifacts(self.framework)]

    def artifact_aliases(self) -> Dict[str, str]:
        """{earlier id: current id} for builds in the framework's build folder: before the output folder setting
        (eighth round, answer 3) that folder was the output folder, and a build's id came from its relative path alone
        (key <path>, label output:<path>). Feed rows, pending approvals and AI callers that kept such an id still reach
        the build. An earlier id that is a current id of another build stays that build's."""
        from orchestrator.Core.artifact_output import BUILDS, list_artifacts
        framework = getattr(self, "framework", None)
        if framework is None:   # a service without a framework (a test's stand-in) has no build folder
            return {}
        items = list_artifacts(framework)
        current = {artifact_id(item.key) for item in items}
        aliases: Dict[str, str] = {}
        for item in items:
            prefix = f"{BUILDS}:"
            if item.where == BUILDS and item.key.startswith(prefix):
                earlier = artifact_id(item.key[len(prefix):])
                if earlier not in current:
                    aliases.setdefault(earlier, artifact_id(item.key))
        return aliases

    def artifact_label(self, path: Any) -> str:
        """A build folder or ZIP as an AI may read it (where:<relative path>, else its file name)."""
        from orchestrator.Core.artifact_output import label_for
        return label_for(self.framework, path)

    def list_artifacts(self, target: Optional[str] = None, format: Optional[str] = None,
                       page: int = 1) -> ArtifactList:
        def text(value):
            return None if value is None else str(value)

        items = []
        for aid, label, info in self._artifacts():
            if target and str(info.get("target") or "").lower() != target.lower():
                continue
            if format and str(info.get("format") or "").lower() != format.lower():
                continue
            primary = info.get("primary")
            artifacts = info.get("artifacts") if isinstance(info.get("artifacts"), list) else []
            entry = next((a for a in artifacts if isinstance(a, dict) and a.get("file") == primary), {})
            size = None
            if primary:
                try:
                    # never through a link: the container writes this folder and could point it at any host file
                    file = Path(info["path"]) / primary
                    size = file.lstat().st_size if file.is_file() and not file.is_symlink() else None
                except OSError:
                    size = entry.get("size") if isinstance(entry.get("size"), int) else None
            try:
                items.append(ArtifactOut(artifact_id=aid, label=label, size_bytes=size,
                                         model=self._shown_model(info.get("model") or info.get("model_source")),
                                         format=text(info.get("format")), quantization=text(info.get("quantization")),
                                         board=text(info.get("board")), target=text(info.get("target")),
                                         created=text(info.get("created")), primary=text(primary),
                                         sha256=text(entry.get("sha256"))))
            except ValueError as e:  # one odd manifest must not hide every other build
                self.logger.warning(f"Skipping the build manifest of {label}: {e}")
        page = max(1, int(page))
        start = (page - 1) * PAGE_SIZE
        return ArtifactList(artifacts=items[start:start + PAGE_SIZE], page=page, more=len(items) > start + PAGE_SIZE)

    def busy_output_folders(self) -> List[str]:
        """Output folders unfinished builds write into, in every framework process: the build engines' records of
        running builds, and for requests still queued (no build record yet) the folder their build will use."""
        folders: List[str] = []
        for record in self._records():
            if record.get("status") not in ("completed", "failed", "cancelled", "interrupted") and \
                    record.get("output_dir"):
                folders.append(str(record["output_dir"]))
        bases = [folder for _, folder in self.artifact_roots()]
        pending = []
        try:
            for state in list(self._orchestrator._workflows.values()):
                if state.status.value not in BUILD_FINISHED:
                    pending.append(dict(getattr(state, "summary", {}) or {}))
        except BuildServiceError:
            pass
        pending += [r.get("summary") or {} for r in self._shared_requests()
                    if r.get("status") not in ("completed", "error", "cancelled", "interrupted")]
        for summary in pending:
            target, model = summary.get("target"), str(summary.get("model") or "")
            if target and model:
                name = model.rsplit(":", 1)[-1].rstrip("/").split("/")[-1]
                folders += [str(base / str(target) / name) for base in bases]
        return folders

    def artifact_entry(self, aid: str) -> Tuple[str, str, Dict[str, Any]]:
        """(artifact id, output:<relative path>, manifest) of one finished build by its id, as list_artifacts shows it
        (the manifest is the build engine's, its values untrusted); an earlier id of a build in the build folder
        (artifact_aliases) gives the build under its current id. BuildServiceError (unknown_id) otherwise."""
        if ARTIFACT_ID.fullmatch(aid or ""):
            items = self._artifacts()
            found = next((item for item in items if item[0] == aid), None)
            if found is None:
                current = self.artifact_aliases().get(aid)
                found = next((item for item in items if current and item[0] == current), None)
            if found is not None:
                return found
        raise BuildServiceError(f"Unknown built model '{aid}'.", code="unknown_id",
                                next="Call list_artifacts for the ids you can use.")

    _artifact = artifact_entry  # the former private name; tools use artifact_entry

    def artifact_details(self, aid: str) -> ArtifactDetails:
        """Manifest (every string external text, model_source as a label), model card and files of one build. Only
        regular files directly inside the build's folder are listed; links are skipped."""
        _, label, info = self.artifact_entry(aid)
        folder = Path(info["path"])
        manifest = {k: v for k, v in info.items() if k not in ("path",)}
        for key in ("model_source", "model"):
            if key in manifest:
                manifest[key] = self._shown_model(manifest[key])
        if isinstance(manifest.get("dataset"), dict) and manifest["dataset"].get("path"):
            manifest["dataset"] = dict(manifest["dataset"], path=self._shown_model(manifest["dataset"]["path"]))
        hashes = {a.get("file"): a.get("sha256") for a in (info.get("artifacts") or []) if isinstance(a, dict)}
        files, card = [], ""
        try:
            entries = sorted(folder.iterdir(), key=lambda p: p.name)
        except OSError:
            entries = []
        for entry in entries[:MAX_ARTIFACT_FILES]:
            try:
                if entry.is_symlink() or not entry.is_file():
                    continue
                files.append(ArtifactFile(name=entry.name, size=entry.stat().st_size,
                                          sha256=hashes.get(entry.name)))
            except OSError:
                continue
        card_path = folder / "Model_Card.md"
        if card_path.is_file() and not card_path.is_symlink():
            try:
                with open(card_path, "r", encoding="utf-8", errors="replace") as f:
                    card = f.read(MAX_CARD_CHARS)
            except OSError:
                card = ""
        return ArtifactDetails(artifact_id=aid, label=label, manifest=manifest, model_card=card, files=files)

    def log_tail(self, request_id: str, lines: int = 200, before_line: Optional[int] = None,
                 for_person: bool = False) -> LogTail:
        """The last `lines` lines (at most 1,000), or those before line `before_line` (paging back). Each line is cut
        at 2,000 characters and the whole tail at 48,000; `truncated` says older lines exist or lines were cut.
        Secrets are always redacted. A host path reaches an AI by its label or file name (_log_paths); `for_person`:
        the Builder's live log, the person's own window, keeps the full paths (round 5 f)."""
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
        lines = max(1, min(int(lines), LOG_MAX_LINES))
        total = len(collected)
        end = total if before_line is None else max(0, min(total, int(before_line) - 1))
        begin = max(0, end - lines)
        chosen, size, cut = [], 0, False
        clean = (lambda text: text) if for_person else self._log_paths()
        for line in reversed(collected[begin:end]):  # newest first, so a cut drops the oldest lines
            text = clean(redact_secrets(line))
            if len(text) > LOG_LINE_CHARS:
                text, cut = text[:LOG_LINE_CHARS] + " [... cut ...]", True
            if size + len(text) > LOG_TOTAL_CHARS:
                cut = True
                break
            chosen.append(text)
            size += len(text)
        chosen.reverse()
        first = end - len(chosen) + 1 if chosen else end + 1
        return LogTail(request_id=request_id, lines=chosen, first_line=first, total_lines=total,
                       truncated=cut or first > 1)

    def _log_paths(self):
        """A log line as an AI reads it (every reader but the Builder's live log): a line with a path on this
        computer, such as the output folder in the home folder, gets the framework's folders by label (output:<path>,
        builds:, cache:, ...) and any other path by its file name (tools_runtime.label_paths), so no user name is left;
        a line without one stays as it is, with the paths inside the build container. Folders that hold user names on
        any system (/home/, /media/ and /run/media/ for removable disks, /Users/, C:\\Users\\) count as paths on this
        computer too, so a local model on a USB disk or in another user's folder is named by its file name."""
        import os
        from orchestrator.Core.mojo.views import ai_folder_labels
        from orchestrator.security.paths import label_paths
        roots = ai_folder_labels(self.framework)
        home = str(Path.home())
        marks = [r for r in roots if r not in ("", "/", "\\")]
        marks += [h for h in {home, os.path.realpath(home)} if h not in ("", "/", "\\")]
        users = ("/home/", "/media/", "/users/", "\\users\\")  # compared without letter case

        def clean(line: str) -> str:
            lowered = line.lower()
            if any(m in line for m in marks) or any(u in lowered for u in users):
                return label_paths(line, roots)
            return line
        return clean

    # --- the plan of a build (check_build_request, start_build's question) ------------------------------------

    def hf_token(self) -> Optional[str]:
        """The Hugging Face token stored in the framework (read inside the framework only), or None."""
        sm = getattr(self.framework, "secrets_manager", None)
        try:
            token = sm.get_secret("hf_token") if sm is not None else None
        except Exception:  # noqa: BLE001 - a locked vault: no token
            return None
        return token or None

    def hub_facts(self, repo: str, revision: Optional[str]):
        from orchestrator.Core.hf_lookup import look_up
        return look_up(repo, revision, self.hf_token())

    def model_commit(self, repo: str) -> Optional[str]:
        """The commit a Hugging Face model's default branch points to now: one short look-up without the file list
        (the GUI's identical-build check). None in offline mode, for anything but a repo id, or when Hugging Face
        cannot be asked."""
        from orchestrator.utils.locality import offline_mode
        if offline_mode(self.framework.config) or not HF_REPO_ID.match(str(repo or "")):
            return None
        from orchestrator.Core.hf_hub import hub_api
        token = self.hf_token()
        try:  # the stored token or none, only to huggingface.co (hf_hub.hub_api, never the host in HF_ENDPOINT)
            sha = getattr(hub_api(token).model_info(repo, token=token or False, timeout=10), "sha", None)
        except Exception:  # noqa: BLE001 - not known: the earlier build is reported as "commit not checked"
            return None
        return sha if isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{40}", sha) else None

    def host_hardware(self):
        from orchestrator.Core.build_plan import host_hardware
        return host_hardware(self.framework)

    @staticmethod
    def disk_usage(path: str):
        return shutil.disk_usage(path)

    def disk_ledger(self):
        from orchestrator.Core.build_plan import state_dir
        from orchestrator.Core.disk_plan import ReservationLedger
        folder = state_dir(self.framework)
        return ReservationLedger(folder) if folder is not None else None

    def plan(self, request, look_up_hub: bool = True, recipe: Optional[Dict[str, Any]] = None):
        """The plan of an AI caller's build; `recipe` (id, installed_via, pack_sha256) for a recipe build."""
        from orchestrator.Core.build_plan import plan_build
        return plan_build(self, request, look_up_hub=look_up_hub, ledger=self.disk_ledger(), recipe=recipe)

    def reserve_disk(self, bound: Dict[str, Any], owner: str, label: str, standing: bool = False) -> str:
        """Takes the disk reservation for an accepted build from the sizes its question showed; InsufficientSpace
        when the disk no longer holds it beside what others reserved. `standing`: the build runs under a standing
        approval, so the larger reserve (20 GiB or 10 %) must stay free, checked under the ledger's lock together
        with every other process's reservations (design 6.5). "" when there is no ledger (no per-user folder) or
        nothing lands on disk."""
        from orchestrator.Core.build_plan import folder
        from orchestrator.Core import disk_plan
        ledger = self.disk_ledger()
        size = int(bound.get("download") or 0) or int(bound.get("local_bytes") or 0)
        if ledger is None or not size:
            return ""
        cached = int(bound.get("cached") or 0) if bound.get("download") else size
        needs = disk_plan.build_needs(size, str(bound.get("format") or ""), bound.get("quantization"),
                                      folder(self.framework, "cache_dir", "cache"),
                                      folder(self.framework, "output_dir", "output"), cached=cached)
        rid, _ = ledger.reserve(needs, owner, label, standing=standing, disk_usage=self.disk_usage)
        return rid

    def record_confirmed_build(self, bound: Dict[str, Any], channel: str) -> bool:
        """After a build the person confirmed in the framework's own window was accepted: records it, so a target or
        recipe pack installed under a standing approval may be covered by a build standing approval from now on, and
        the target's digest-pinned base images count as confirmed (design 6.10). Nothing for any other channel."""
        from orchestrator.Core.build_plan import state_dir
        from orchestrator.Core.confirmed_builds import ConfirmedBuilds, person_confirmed
        record = bound.get("confirm_record") if isinstance(bound, dict) else None
        state = state_dir(self.framework)
        if not isinstance(record, dict) or state is None or not person_confirmed(channel):
            return False
        store = ConfirmedBuilds.for_framework(self.framework, state)  # signed with the permission file's key
        try:
            done = store.record("target", str(record.get("target") or ""),
                                installed_via=str(record.get("installed_via") or ""),
                                pack_sha256=str(record.get("pack_sha256") or ""),
                                base_images=list(record.get("base_images") or []), channel=channel)
            recipe = record.get("recipe")
            if isinstance(recipe, dict) and recipe.get("id"):
                done = store.record("recipe", str(recipe["id"]), installed_via=str(recipe.get("installed_via") or ""),
                                    pack_sha256=str(recipe.get("pack_sha256") or ""), channel=channel) and done
            module = record.get("module")
            if isinstance(module, dict) and module.get("definition_sha256"):
                # a module an AI made unattended: confirmed for exactly this definition ([Q1], design 3.9)
                done = store.record("module", str(record.get("target") or ""), channel=channel,
                                    definition_sha256=str(module["definition_sha256"])) and done
            return done
        except Exception as e:  # noqa: BLE001 - the build runs; the next one is asked again
            self.logger.warning(f"Could not record the confirmed build: {redact_secrets(str(e))}")
            return False

    # --- recipes (community zoo) ----------------------------------------------------------------------------

    def installed_recipe(self, recipe_id: str):
        """(Recipe, provenance) of an installed recipe pack; BuildServiceError (unknown_id, not_supported)
        otherwise. A revoked recipe is refused. The only path that builds an installed recipe: the Community Hub's
        Build button and AI callers (start_recipe_build) both come here."""
        from orchestrator.Core.community_manager import CommunityManager, read_recipe, trust_of_folder
        cm = getattr(self.framework, "community_manager", None)
        if cm is None:
            raise BuildServiceError("The community zoo is not available here.", code="not_supported",
                                    next="Tell the person; run_mojo_oracle (or 'llmcf mojo' in a terminal) checks "
                                         "the installation.")
        found = next(((folder, prov) for folder, prov in cm.installed()
                      if prov.kind == "recipe" and prov.id.casefold() == str(recipe_id).casefold()), None)
        if found is None:
            raise BuildServiceError(f"No installed recipe '{recipe_id}'.", code="unknown_id", field="recipe_id",
                                    next="Call list_packs with kind recipe and installed_only true for the ids, or "
                                         "add one with add_pack_from_index.")
        folder, prov = found
        try:
            # the revocations the Community Hub's list shows too: the manager's own trusted keys
            cached = getattr(cm, "cached_index", None)
            index = (cached(revocations=True) if callable(cached)
                     else CommunityManager.read_cached_index(Path(cm.cache_dir), revocations=True))
        except Exception:  # noqa: BLE001 - no cached index: no revocations known
            index = None
        trust, reason = trust_of_folder(folder, index)
        if trust is not None and trust.value == "revoked":
            raise BuildServiceError(f"The recipe {prov.id} was revoked by the community index; it is not built.",
                                    code="not_supported", next="Tell the person; they remove it in the Community Hub.")
        try:
            recipe = read_recipe(folder)
        except Exception:  # noqa: BLE001 - details of a broken pack are not shown
            raise BuildServiceError(f"The installed recipe {prov.id} cannot be read.", code="not_supported",
                                    next="Tell the person; list_packs with verify true checks the installed "
                                         "packs.") from None
        return recipe, prov

    def recipe_request(self, recipe_id: str, dataset_path: Optional[str] = None,
                       use_gpu: bool = False) -> Tuple[BuildRequestIn, Any, Any]:
        """The build request an installed recipe pins: its model at its commit, target, format, board, quantization,
        importance matrix and context length. A calibration dataset is taken only when the recipe names one (and
        then it is required). Returns (request, recipe, provenance)."""
        recipe, prov = self.installed_recipe(recipe_id)
        named = recipe.options.dataset
        if named is not None and not dataset_path:
            raise BuildServiceError(f"The recipe {recipe.id} was calibrated with {named.name} (sha256 "
                                    f"{named.sha256[:16]}...); give that file as dataset_path.",
                                    code="missing_argument", field="dataset_path",
                                    next="Call list_local_files with kind dataset and pass its reference.")
        if named is None and dataset_path:
            raise BuildServiceError(f"The recipe {recipe.id} names no calibration dataset; leave dataset_path out.",
                                    code="invalid_value", field="dataset_path",
                                    next="Call start_recipe_build without dataset_path.")
        request = BuildRequestIn(model=recipe.model.repo, revision=recipe.model.revision, target=recipe.target.id,
                                 format=recipe.format, board=recipe.board, quantization=recipe.quantization,
                                 use_imatrix=recipe.options.use_imatrix, dataset_path=dataset_path or None,
                                 use_gpu=bool(use_gpu), max_context_length=recipe.options.max_context_length)
        return request, recipe, prov

    @staticmethod
    def recipe_digest(recipe) -> str:
        """sha256 of every field of a recipe (canonical JSON): the recipe the person was asked about is the one that
        is built, including the importance matrix, context length and dataset the question showed."""
        text = json.dumps(recipe.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def check_recipe_dataset(recipe, dataset_file: Optional[str]) -> None:
        """The calibration dataset must be exactly the file the recipe pins (its sha256)."""
        named = recipe.options.dataset
        if named is None or not dataset_file:
            return
        try:
            digest = sha256_file(dataset_file)
        except OSError:
            raise BuildServiceError("The calibration dataset cannot be read.", code="invalid_value",
                                    field="dataset_path", next="Name an existing dataset file.") from None
        if not digest_matches(digest, named.sha256):
            raise BuildServiceError(f"The calibration dataset is not the file the recipe {recipe.id} pins (its sha256 "
                                    f"differs from {named.sha256[:16]}...).", code="invalid_value",
                                    field="dataset_path", next=f"Give exactly the file {named.name} the recipe names.")

    def start_recipe(self, recipe_id: str, *, dataset_path: Optional[str] = None, use_gpu: bool = False,
                     owner: str = "", local_paths: bool = False, disk_reservation: str = "",
                     expect: Optional[Dict[str, Any]] = None, **build_options: Any) -> BuildHandle:
        """Builds an installed recipe exactly as pinned, through start_build: the use-time revocation check, the
        calibration-dataset pin. `expect`: the request values the person was shown (model, revision, target, format,
        board, quantization, recipe_sha256); a recipe changed since is refused. `build_options`: start_build's
        optimization, priority, output_dir, parallel_builds and description (the Builder tab's)."""
        request, recipe, _prov = self.recipe_request(recipe_id, dataset_path, use_gpu)
        validated, _ = self._validated(request, local_paths)
        if expect is not None:
            now = {"model": request.model, "revision": request.revision, "target": validated.target,
                   "format": validated.format, "board": validated.board, "quantization": validated.quantization}
            changed = [k for k, v in now.items() if str(v or "").casefold() != str(expect.get(k) or "").casefold()]
            if not changed and self.recipe_digest(recipe) != str(expect.get("recipe_sha256") or ""):
                changed = ["recipe"]  # any other field (importance matrix, context length, dataset, target pin)
            if changed:
                raise BuildServiceError(f"The recipe {recipe.id} changed after the question ({', '.join(changed)}); "
                                        "nothing was started.", code="failed",
                                        next="Call start_recipe_build again; the person is asked again.")
        self.check_recipe_dataset(recipe, validated.dataset_path)
        # checked now for an early answer; the build engine checks the bytes it uses again (a copy in its own folder
        # with exactly the pinned sha256), so a file swapped after this check is never used
        named = recipe.options.dataset
        return self.start_build(request, local_paths=local_paths, owner=owner, disk_reservation=disk_reservation,
                                dataset_sha256=named.sha256 if named is not None else None, **build_options)

    # --- identical builds (DECISIONS, eighth round, N11) -------------------------------------------------------

    def request_options(self, req: BuildRequestIn, inputs: Optional[LocalInputs] = None, *,
                        revision: Optional[str] = None, optimization: str = "balanced",
                        model_task: str = "LLM") -> Dict[str, Any]:
        """The chosen options of a validated request (build_identity), as the build engine will record them: the
        model by its repo id and `revision` (the commit the build would fetch; None when not known yet), a local
        model by its weights files, the target with the sha256 of its folder, board, format, quantization,
        importance matrix, calibration data, optimization, context length, GPU use and task."""
        from orchestrator.Core import build_identity
        pin = getattr(inputs, "model", None)
        local = Path(pin.path) if pin is not None else (Path(req.model) if Path(req.model).is_absolute() else None)
        target_sha = None
        try:
            from orchestrator.Core.community_pack import tree_sha256
            tm = self._targets()
            folder = tm.folder_of(req.target) if hasattr(tm, "folder_of") else None
            target_sha = tree_sha256(Path(folder)) if folder is not None else None
        except Exception:  # noqa: BLE001 - a target that cannot be hashed is never the same as an earlier one
            target_sha = None
        imatrix = bool(req.use_imatrix and str(req.format).lower() == "gguf")
        if req.dataset_path:
            dataset = build_identity.file_sha256(req.dataset_path)
        elif imatrix:  # without a dataset of its own, a GGUF build uses the shipped calibration text
            dataset = build_identity.shipped_calibration_sha256(
                str(self._config_get("calibration_language", "mixed") or "mixed"))
        else:
            dataset = None
        return build_identity.build_options(
            model=None if local is not None else req.model,
            revision=None if local is not None else (revision or req.revision),
            weights=build_identity.local_weights(local) if local is not None else None,
            target=req.target, target_sha256=target_sha, board=req.board, fmt=req.format,
            quantization=req.quantization, imatrix=imatrix, dataset_sha256=dataset,
            optimization=str(optimization or "balanced").lower(), max_context_length=req.max_context_length,
            use_gpu=req.use_gpu, model_task=model_task)

    def earlier_builds(self, request: BuildRequestIn, *, revision: Optional[str] = None,
                       optimization: str = "balanced", local_paths: bool = False, model_task: str = "LLM"):
        """Finished builds whose recorded options are 100 % those of this request (build_identity.EarlierBuild,
        identical ones first). `revision`: the commit the build would fetch (looked up by check_build_request); without
        it a build with the same options is "commit_unchecked", never identical. Nothing is skipped here: the caller
        tells the person, who uses the earlier build or builds again."""
        from orchestrator.Core.build_identity import find_earlier
        req, inputs = self._validated(request, local_paths)
        options = self.request_options(req, inputs, revision=revision, optimization=optimization,
                                       model_task=model_task)
        return find_earlier(self._artifacts(), options)

    # --- actions ----------------------------------------------------------------------------

    def validate(self, request: BuildRequestIn, *, local_paths: bool = False) -> BuildRequestIn:
        """Checks a request against the target's capabilities and fills the default quantization. A local model
        or dataset must be a local-file reference inside the folders the AI may use, unless `local_paths` is set
        for a person's own caller (GUI, CLI, a script), which may name any path."""
        return self._validated(request, local_paths)[0]

    def _validated(self, request: BuildRequestIn, local_paths: bool) -> Tuple[BuildRequestIn, Optional[LocalInputs]]:
        target = self.resolve_target(request.target)
        caps = self._targets().get_capabilities(target) if local_paths else self.capabilities(target)
        if caps is not None:
            # Canonical spellings, the board the scripts use when none is given, the board's default type.
            fmt, board, quant = caps.normalize(request.format, request.board, request.quantization)
        else:
            # A person's own target without a capabilities block (a module the wizard wrote): built unchecked, as the
            # CLI and the Builder tab always did; the board lower-cased as the module scripts read it.
            fmt, quant = str(request.format).strip().lower(), request.quantization
            board = (request.board or "").strip().lower() or None
            if fmt == ModelFormat.HUGGINGFACE.value or fmt not in {f.value for f in ModelFormat}:
                raise BuildServiceError(f"Unknown format '{request.format}'.", code="invalid_value", field="format",
                                        next="Use one of: " + ", ".join(f.value for f in ModelFormat
                                                                        if f is not ModelFormat.HUGGINGFACE) + ".")
        inputs = None
        if not local_paths:
            request, inputs = self._confined(request)
        dataset = None
        if request.dataset_path:
            path = Path(request.dataset_path).expanduser()
            if not path.is_file():  # an AI caller's dataset by its label, never its real path
                shown = getattr(getattr(inputs, "dataset", None), "ref", None) or request.dataset_path
                raise BuildServiceError(f"Calibration dataset '{shown}' not found.",
                                        code="invalid_value",
                                        next="Name an existing dataset file as <label>:<relative path>.",
                                        field="dataset_path")
            dataset = str(path.resolve())
        problems = caps.check_request(fmt, board, quant, dataset=bool(dataset),
                                      imatrix=request.use_imatrix) if caps is not None else []
        if problems:
            error = BuildServiceError(" ".join(problems), code="not_supported",
                                      next=f"Call get_target_capabilities for {target}, then choose a supported "
                                           "combination.")
            error.problems = list(problems)  # one by one, as the CLI and the Builder tab show them
            raise error
        if local_paths or getattr(inputs, "model", None) is not None:
            model = self._check_model_source(request.model, None if local_paths else inputs.model.ref)
        else:
            model = request.model  # a Hugging Face repo id: never looked up on disk for an AI caller
        if request.revision and Path(model).is_absolute():
            raise BuildServiceError("A revision applies to Hugging Face models only, not to a local model.",
                                    code="invalid_value", next="Leave out the revision for a local model.",
                                    field="revision")
        self._refuse_what_the_engine_cannot(request)
        return request.model_copy(update={"target": target, "format": fmt, "board": board, "quantization": quant,
                                          "dataset_path": dataset, "model": model}), inputs

    def _refuse_what_the_engine_cannot(self, request: BuildRequestIn) -> None:
        """Before anything is queued, a build the container engine cannot run as asked (DECISIONS round 26 Q1): a GPU
        build that passes an NVIDIA GPU (nvidia-smi) into the container needs Docker's GPU request, which Podman's
        Docker-compatible interface drops without a word; the build would run on the CPU instead."""
        if not request.use_gpu or not shutil.which("nvidia-smi"):
            return
        from orchestrator.utils.docker_engine import engine_name, podman_refusal
        get = getattr(self.framework, "get_component", None)
        refusal = podman_refusal("gpu", engine_name(get("docker_client") if callable(get) else None))
        if refusal:
            raise BuildServiceError(refusal, code="not_supported", field="use_gpu",
                                    next="Build without the GPU option, or switch to Docker CE as described.")

    @staticmethod
    def request_in(**values: Any) -> BuildRequestIn:
        """The build request of a person's own window (the CLI's options, the Builder tab's form) from its values;
        BuildServiceError (invalid_value, the first field) in plain words when a value breaks the request's limits,
        e.g. no model at all."""
        from pydantic import ValidationError
        try:
            return BuildRequestIn(**values)
        except ValidationError as e:
            errors = e.errors()
            field = str(errors[0]["loc"][0]) if errors and errors[0].get("loc") else ""
            text = "; ".join(f"{'.'.join(str(p) for p in err.get('loc', ())) or 'request'}: {err.get('msg')}"
                             for err in errors)
            raise BuildServiceError(f"The build request is not valid ({text}).", code="invalid_value", field=field,
                                    next="Correct that value and start the build again.") from None

    @staticmethod
    def _priority(priority: Any) -> PriorityLevel:
        if isinstance(priority, PriorityLevel):
            return priority
        try:
            return PriorityLevel[str(priority or "normal").strip().upper()]
        except KeyError:
            raise BuildServiceError(f"Unknown priority '{priority}'.", code="invalid_value", field="priority",
                                    next="Use one of: low, normal, high, urgent, critical.") from None

    def start_build(self, request: BuildRequestIn, *, local_paths: bool = False, owner: str = "",
                    optimization: str = "balanced", disk_reservation: str = "",
                    dataset_sha256: Optional[str] = None, priority: Any = "normal",
                    output_dir: Optional[str] = None, parallel_builds: bool = False,
                    description: str = "Build service request") -> BuildHandle:
        """Validates and queues a build: the one build-submission path of the CLI, the GUI and AI callers. `owner` is
        the tool caller that starts it (ToolContext.owner; empty for the person's own CLI and GUI, whose builds the
        queue limit for tool callers never refuses); `disk_reservation` the reservation an AI caller's accepted build
        took (released when the request ends); `dataset_sha256` the sha256 the calibration dataset must have (a
        recipe's pin), checked by the build engine on the copy it uses. Only the person's own callers (`local_paths`)
        set `output_dir`, the folder the finished build is saved to (default: the output folder), and a `priority`
        other than normal (a PriorityLevel or its name); `parallel_builds` and `description` as their windows set
        them."""
        try:
            level = OptimizationLevel(str(optimization or "balanced").lower())
        except ValueError:
            raise BuildServiceError(f"Unknown optimization level '{optimization}'.", code="invalid_value",
                                    next="Use one of: fast, balanced, size, speed, aggressive.",
                                    field="optimization") from None
        chosen_priority = self._priority(priority)
        if not local_paths and (output_dir is not None or chosen_priority is not PriorityLevel.NORMAL):
            raise BuildServiceError("Only the person's own windows choose the priority and the folder a build is "
                                    "saved to.", code="not_supported",
                                    next="Leave them out; the build goes to the output folder.",
                                    field="output_dir" if output_dir is not None else "priority")
        req, inputs = self._validated(request, local_paths)
        build_request = BuildRequest(
            request_id="",
            workflow_type=WorkflowType.SIMPLE_CONVERSION,
            priority=chosen_priority,
            models=[req.model],
            model_revision=req.revision,
            targets=[req.target],
            target_formats=[ModelFormat(req.format)],
            optimization_level=level,
            quantization_options=[req.quantization] if req.quantization else [],
            parallel_builds=bool(parallel_builds),
            output_base_dir=str(output_dir) if output_dir is not None else str(self._output_dir()),
            description=description,
            use_gpu=req.use_gpu,
            use_imatrix=req.use_imatrix,
            dataset_path=req.dataset_path,
            dataset_sha256=dataset_sha256 if req.dataset_path else None,
            target_board=req.board,
            max_context_length=req.max_context_length,
            local_inputs=inputs,
            owner=owner,
            disk_reservation=disk_reservation,
        )
        if disk_reservation:
            self._orchestrator.disk_ledger = self.disk_ledger()
        try:
            request_id = self.framework.run_coroutine(self._orchestrator.submit_build_request(build_request),
                                                      timeout=30)
        except BuildQueueFull as e:
            raise BuildServiceError(str(e), code="limit_reached",
                                    next="Wait until a build finishes (get_build_status with wait_seconds) or cancel "
                                         "one of your builds, then call start_build again.") from None
        self.logger.info(f"Build request {request_id} submitted ({req.model} -> {req.target}/{req.format}/{req.quantization})")
        return BuildHandle(request_id=request_id, status=OrchestrationStatus.QUEUED.value)

    def request_state(self, request_id: str) -> Optional[WorkflowState]:
        """The orchestrator's own state of a request this process queued (WorkflowState: the finished builds' real
        paths, the pending healing proposal itself), None when it does not know the request. For the person's own
        windows only (the CLI's follow loop); AI callers read get_status, which labels paths and redacts texts."""
        return self.framework.run_coroutine(self._orchestrator.get_workflow_status(request_id), timeout=30)

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
        if proposal is None and getattr(state, "client_proposals", None):
            raise BuildServiceError(f"Build request '{request_id}' has only fix proposals from AI clients: manual "
                                    "steps for the person, which the framework never applies.", code="not_supported",
                                    next="Show the person the manual step (client_proposals in get_build_status); "
                                         "they do it themselves.")
        if proposal is None:
            error = BuildServiceError(f"Build request '{request_id}' has no pending healing proposal.", code="failed",
                                      next="Call get_build_status to see the build's state; only a build that waits "
                                           "for a decision has a proposal.")
            error.blocker = "proposal_missing"  # the facts catalogue's blocker (delegation design 4.2)
            raise error
        return state, proposal

    def propose_fix(self, request_id: str, summary: str, root_cause: str, manual_step: str, confidence: float,
                    where: str, proposed_by: str, client: str = ""):
        """An AI client's fix proposal for a failed build (propose_healing_fix): checked like Ditto's own analysis
        (HealingAnalysis and the dangerous-command filter), kept with the build as a manual step and in the per-user
        folder, so the GUI and `llm-cli build status` show it with who proposed it. Never applied."""
        from orchestrator.Core.client_proposals import ClientProposal, ProposalLimit, new_proposal_id
        from orchestrator.Core.ditto_manager import HealingAnalysis, dangerous_command
        from orchestrator.utils.untrusted import sanitize_untrusted
        state = self._state(request_id)
        # Kept as the person will read it (cleaned), so the text the filter checked is the text that is shown.
        summary, root_cause, manual_step = (sanitize_untrusted(str(t or ""), 4000)
                                            for t in (summary, root_cause, manual_step))
        try:
            analysis = HealingAnalysis(summary=summary, root_cause=root_cause, fix_command=manual_step,
                                       confidence=confidence, target=where)
        except ValueError as e:
            raise BuildServiceError(f"The proposal is not valid: {e}", code="invalid_value",
                                    next="Give a one-sentence summary, the cause and one manual step.") from None
        danger = dangerous_command(analysis.fix_command, analysis.root_cause, analysis.summary)
        if danger:
            raise BuildServiceError(f"The proposal contains a dangerous command ({danger}), so it was refused. The "
                                    "build log may contain instructions aimed at the AI.", code="invalid_value",
                                    next="Propose a step without that command, or tell the person to check the log "
                                         "themselves.")
        proposal = ClientProposal(proposal_id=new_proposal_id(), request_id=request_id, summary=analysis.summary,
                                  root_cause=analysis.root_cause, manual_step=analysis.fix_command,
                                  confidence=float(analysis.confidence), where=analysis.target,
                                  proposed_by=proposed_by, client=str(client or "")[:200],
                                  request=getattr(state, "label", "") or "")
        try:
            self._orchestrator.add_client_proposal(request_id, proposal)
        except ProposalLimit as e:
            raise BuildServiceError(str(e), code="limit_reached",
                                    next="Tell the person about the proposals already made.") from None
        except ValueError:
            raise BuildServiceError(f"Build request '{request_id}' has not failed; a fix is proposed only for a "
                                    "failed build.", code="not_supported",
                                    next="Call get_build_status; propose a fix once the build has failed.") from None
        store = self._proposal_store()
        if store is not None:
            try:
                proposal = store.add(proposal)
            except (ProposalLimit, OSError, TimeoutError) as e:  # the build's own copy is kept and shown
                self.logger.warning(f"Could not keep the fix proposal {proposal.proposal_id} for other windows: {e}")
        return proposal

    def _proposal_store(self):
        from orchestrator.Core.client_proposals import ClientProposalStore
        state_dir = getattr(getattr(self.framework, "config", None), "user_state_dir", None)
        return ClientProposalStore(Path(state_dir)) if isinstance(state_dir, (str, Path)) else None

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
