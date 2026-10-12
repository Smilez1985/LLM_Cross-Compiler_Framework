#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Build Service

Typed facade over the orchestrator, the build engine and the target registry. Every entry point
that is not the GUI's own widgets (MCP server, Ditto's tools, scripts) goes through this class, so
requests are validated once, against the target's declared capabilities, and results have one shape.
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
from orchestrator.Core.deployment_manager import list_builds
from orchestrator.Core.orchestrator import (BuildQueueFull, BuildRequest, OrchestrationStatus, PriorityLevel,
                                            WorkflowState, WorkflowType)
from orchestrator.schemas.build import (ArtifactDetails, ArtifactFile, ArtifactList, ArtifactOut, BuildHandle,
                                        BuildList, BuildRequestIn, BuildStatusOut, ClientProposalOut,
                                        FormatSummary, LogTail, PendingHealing, RequestSummary, TargetList,
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


class BuildServiceError(ValueError):
    """A request the service refuses, with a message meant for the user (or the calling model), a reason code
    (the tool registry's codes, e.g. unknown_id, not_supported), the next step and, for an input refusal, the
    argument it is about (Ditto's loop tells a fix of another argument from a repeat by it)."""

    def __init__(self, message: str, code: str = "failed", next: str = "", field: str = ""):
        super().__init__(message)
        self.code, self.next, self.field = code, next, field


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
        return BuildStatusOut(
            request_id=state.request_id, status=state.status.value, stage=state.current_stage,
            progress_percent=state.progress_percent, total_builds=state.total_builds,
            completed_builds=state.completed_builds, failed_builds=state.failed_builds,
            artifacts=list(state.artifacts), errors=[redact_secrets(e) for e in state.errors],
            warnings=[redact_secrets(w) for w in state.warnings], pending_healing=pending,
            client_proposals=[ClientProposalOut(
                proposal_id=p.proposal_id, summary=p.summary, root_cause=p.root_cause, manual_step_text=p.manual_step,
                confidence=p.confidence, where=p.where, proposed_by=p.proposed_by, client=p.client,
                created=p.created) for p in getattr(state, "client_proposals", [])],
            owner=owner_actor(getattr(state, "owner", "")) or None,
            request=RequestSummary(**getattr(state, "summary", {})) if getattr(state, "summary", None) else None)

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

    def _other_builds(self) -> Dict[str, BuildStatusOut]:
        """Build requests of other framework processes from their build records (newest attempt per request),
        read-only: model as a repo id or label, never a host path; errors as external text."""
        try:
            own = set(self._orchestrator._workflows)
        except BuildServiceError:
            own = set()
        found: Dict[str, BuildStatusOut] = {}
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
                warnings=(["The process that ran this build ended before the build finished."]
                          if status == "interrupted" else []),
                request=RequestSummary(model=self._shown_model(record.get("model")), target=record.get("target"),
                                       format=record.get("format"), board=record.get("board"),
                                       quantization=record.get("quantization")),
                process="other")
        return found

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
        if not Path(text).is_absolute():
            return text
        try:
            label = LocalFiles.for_framework(self.framework).label_for(text)
        except (LocalFileError, OSError, AttributeError, TypeError):
            label = None
        return label or Path(text).name

    def _artifacts(self) -> List[Tuple[str, str, Dict[str, Any]]]:
        """(artifact id, output:<relative path>, manifest) of every finished build under the output folder."""
        out = self._output_dir()
        found = []
        for info in list_builds(out):
            try:
                relative = Path(info["path"]).resolve().relative_to(out.resolve()).as_posix()
            except (ValueError, OSError):
                relative = Path(info["path"]).name  # a linked subfolder elsewhere: its name only
            found.append((artifact_id(relative), f"output:{relative}", info))
        return found

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

    def _artifact(self, aid: str) -> Tuple[str, str, Dict[str, Any]]:
        if ARTIFACT_ID.fullmatch(aid or ""):
            for item in self._artifacts():
                if item[0] == aid:
                    return item
        raise BuildServiceError(f"Unknown built model '{aid}'.", code="unknown_id",
                                next="Call list_artifacts for the ids you can use.")

    def artifact_details(self, aid: str) -> ArtifactDetails:
        """Manifest (every string external text, model_source as a label), model card and files of one build. Only
        regular files directly inside the build's folder are listed; links are skipped."""
        _, label, info = self._artifact(aid)
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

    def log_tail(self, request_id: str, lines: int = 200, before_line: Optional[int] = None) -> LogTail:
        """The last `lines` lines (at most 1,000), or those before line `before_line` (paging back). Each line is cut
        at 2,000 characters and the whole tail at 48,000; `truncated` says older lines exist or lines were cut."""
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
        for line in reversed(collected[begin:end]):  # newest first, so a cut drops the oldest lines
            text = redact_secrets(line)
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

    def plan(self, request, look_up_hub: bool = True):
        from orchestrator.Core.build_plan import plan_build
        return plan_build(self, request, look_up_hub=look_up_hub, ledger=self.disk_ledger())

    def reserve_disk(self, bound: Dict[str, Any], owner: str, label: str) -> str:
        """Takes the disk reservation for an accepted build from the sizes its question showed; InsufficientSpace
        when the disk no longer holds it beside what others reserved. "" when there is no ledger (no per-user
        folder) or nothing lands on disk."""
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
        rid, _ = ledger.reserve(needs, owner, label, disk_usage=self.disk_usage)
        return rid

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
            if not path.is_file():  # an AI caller's dataset by its label, never its real path
                shown = getattr(getattr(inputs, "dataset", None), "ref", None) or request.dataset_path
                raise BuildServiceError(f"Calibration dataset '{shown}' not found.",
                                        code="invalid_value",
                                        next="Name an existing dataset file as <label>:<relative path>.",
                                        field="dataset_path")
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
                                    code="invalid_value", next="Leave out the revision for a local model.",
                                    field="revision")
        return request.model_copy(update={"target": target, "format": fmt, "board": board, "quantization": quant,
                                          "dataset_path": dataset, "model": model}), inputs

    def start_build(self, request: BuildRequestIn, *, local_paths: bool = False, owner: str = "",
                    optimization: str = "balanced", disk_reservation: str = "") -> BuildHandle:
        """Validates and queues a build. `owner` is the tool caller that starts it (ToolContext.owner);
        `disk_reservation` the reservation an AI caller's accepted build took (released when the request ends)."""
        try:
            level = OptimizationLevel(str(optimization or "balanced").lower())
        except ValueError:
            raise BuildServiceError(f"Unknown optimization level '{optimization}'.", code="invalid_value",
                                    next="Use one of: fast, balanced, size, speed, aggressive.",
                                    field="optimization") from None
        req, inputs = self._validated(request, local_paths)
        build_request = BuildRequest(
            request_id="",
            workflow_type=WorkflowType.SIMPLE_CONVERSION,
            priority=PriorityLevel.NORMAL,
            models=[req.model],
            model_revision=req.revision,
            targets=[req.target],
            target_formats=[ModelFormat(req.format)],
            optimization_level=level,
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
            raise BuildServiceError(f"Build request '{request_id}' has no pending healing proposal.", code="failed",
                                    next="Call get_build_status to see the build's state; only a build that waits "
                                         "for a decision has a proposal.")
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
