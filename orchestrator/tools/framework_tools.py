#!/usr/bin/env python3
"""The framework's own tools (targets, builds, artifacts, logs, healing, knowledge base)."""

import dataclasses
from pathlib import Path
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.Core import job_service
from orchestrator.Core.job_service import JobService
from orchestrator.schemas.build import BuildHandle, BuildStatusOut, TargetList
from orchestrator.schemas.jobs import CancelJobOut, JobErrorOut, JobIn, JobProgress, JobStatusOut, JobWaitIn
# TargetIn lives in schemas/targets.py and TargetCapabilitiesOut with the target tools (tools/tools_targets.py), which
# build on them without the hub; both names stay importable here (TargetCapabilitiesOut on first use, below).
from orchestrator.schemas.targets import TargetCapabilities, TargetIn  # noqa: F401
from orchestrator.tools.audit import AuditLog
from orchestrator.tools.mcp_tokens import TokenStore
from orchestrator.tools import tools_status
from orchestrator.tools.output import ToolOutput, count, fact
from orchestrator.tools.policy import Decision, PolicyStore, ToolTier
from orchestrator.tools.registry import (ToolContext, ToolError, ToolRegistry, ToolSpec, current_actor,
                                         current_registry)
from orchestrator.tools.signing import RevisionAnchor
from orchestrator.tools.tools_healing import register_healing_tools
from orchestrator.tools.tools_hf import register_hf_tools
from orchestrator.tools.tools_knowledge import EMPTY_SEARCH_NOTE, register_knowledge_tools  # noqa: F401
from orchestrator.tools.tools_mojo import DoctorFinding, DoctorOut, mojo_tool_specs  # noqa: F401 - their old home
from orchestrator.tools.spec import BUILD_QUOTA, CallFacts, Choices, Fact, FieldMeta, ToolLimits
from orchestrator.utils.helpers import redact_secrets
from orchestrator.utils.lazy_imports import lazy_attributes

__getattr__ = lazy_attributes(__name__, {"TargetCapabilitiesOut": "orchestrator.tools.tools_targets"})


class NoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=64, pattern=r"^[\w.-]+$")


class BuildStatusIn(RequestIn):
    wait_seconds: int = Field(default=0, ge=0,
                              description="Wait up to this many seconds (at most 50) until the build changes or "
                                          "ends; 0 returns at once. Waiting never affects the build. Example: 50")


class ResolveHealingIn(RequestIn):
    approve: bool = Field(description="True applies the proposed automatic fix and retries; False dismisses it")


# The tools' output models: the service's models plus the framework's `notes` (output.ToolOutput).
class TargetListOut(TargetList, ToolOutput):
    pass


class BuildStatusResult(BuildStatusOut, ToolOutput):
    pass


class BuildHandleOut(BuildHandle, ToolOutput):
    pass


class JobStatusResult(JobStatusOut, ToolOutput):
    pass


class CancelJobResult(CancelJobOut, ToolOutput):
    pass


class StatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sections: List[Literal["framework", "host", "local_runtime", "knowledge", "ai_access", "permissions", "audit"]] = \
        Field(default_factory=lambda: ["framework", "permissions"], min_length=1, max_length=7,
              description="Which parts of the framework's state to show: framework, host, local_runtime, knowledge, "
                          "ai_access, permissions, audit (default: framework and permissions)")


class ToolPermission(BaseModel):
    tool: str
    tier: str
    decision: Literal["allow", "ask", "deny"]
    why: str
    default: str
    max: str
    listed: bool
    new: bool = Field(description="Not reviewed by the person yet: it uses its own default, never a looser tier "
                                  "setting")
    standing_approval: bool = Field(description="Whether a confirmation may offer 'yes, and do not ask again'")


class StandingApprovalOut(BaseModel):
    id: str
    tool: str
    scope: str = Field(description="What it covers, in plain words")
    state: Literal["active", "expired", "lapsed", "outdated"]
    created: str
    created_via: str
    expires: Optional[str] = None
    uses: int
    last_use: Optional[str] = None


class NoToolAction(BaseModel):
    action: str
    where: str


class PermissionsStatus(BaseModel):
    caller: str
    configured: bool
    damaged: bool = Field(description="The permission file is damaged or unreadable: every tool is denied")
    listed: int
    tools: List[ToolPermission]
    # This caller's own standing approvals, read-only (only the person creates or revokes them, in the framework).
    standing_approvals: List[StandingApprovalOut] = Field(default_factory=list)
    no_tool_actions: List[NoToolAction]
    warning: str = ""


class StatusOut(ToolOutput):
    # The sections of design 2.9 (orchestrator/tools/tools_status.py); none holds a secret or a host path.
    framework: Optional[tools_status.FrameworkSection] = None
    host: Optional[tools_status.HostSection] = None
    local_runtime: Optional[tools_status.LocalRuntimeSection] = None
    knowledge: Optional[tools_status.KnowledgeSection] = None
    ai_access: Optional[tools_status.AiAccessSection] = None
    permissions: Optional[PermissionsStatus] = None
    audit: Optional[tools_status.AuditSection] = None


# What no tool does (design section 3), with where the person does it, so a model can name the real reason.
NO_TOOL_ACTIONS = [
    ("sign_in", "Sign in, enter or rotate keys: the GUI's sign-in panel, 'llm-cli ditto login' or "
                "'llm-cli secrets set' (keys never pass through an AI)."),
    ("change_permissions", "Change tool permissions: 'llm-cli tools permissions' or Tools > Tool Permissions "
                           "(Ditto / MCP) in the GUI (an AI never changes its own permissions)."),
    ("audit_log", "Archive or repair the audit log: 'llm-cli tools audit --archive' (the person only)."),
    ("trust_device", "Trust a device's SSH host key or pair it with a password: the GUI's Deployment window, "
                     "'llm-cli deploy run', 'llm-cli scan --device' or 'llm-cli deploy devices add' (only a person "
                     "compares a fingerprint; deploy_to_device then reaches only such devices)."),
    ("deep_ingest_notice", "Acknowledge the one-time Deep Ingest notice (before crawl_docs_site can run): the GUI "
                           "(Tools > Create Hardware Module..., Deep Ingest) or 'llm-cli tools crawl-notice' (only a "
                           "person reads it)."),
    ("publish_pull_request", "Publish a community pull request: 'llm-cli zoo export --pr' (not for AI clients)."),
    ("install_pack_from_file", "Install a pack from a file: the Community Hub in the GUI or "
                               "'llm-cli zoo install --file' (unreviewed code an AI could write itself)."),
    ("run_command", "Run shell commands, read or write files by path, download URLs, run Docker images: no tool, "
                    "for any AI."),
    ("empty_trash", "Empty the trash or delete anything for good, Docker images included: only the person, in the "
                    "framework (File > Trash..., Tools > Framework Build Images..., 'llm-cli trash empty' or "
                    "'llm-cli doctor --fix --tier 2' in a terminal), always with the full list; no tool does."),
]
MAX_LISTED_TOOLS = 40  # some clients cap the number of tools (Cursor: 40)

# Every tool name of this framework's catalogue (design section 2.1: the core, optional and later tools; pinned by
# tests/test_tool_catalogue.py). A new tool joins this set and the test, so the catalogue stays one deliberate list.
KNOWN_TOOLS = frozenset({
    # core
    "list_build_requests", "get_build_status", "read_build_log", "list_artifacts", "get_artifact_details",
    "check_build_request", "start_build", "cancel_build", "resolve_healing", "propose_healing_fix",
    "get_job_status", "cancel_job", "search_hf_models", "get_hf_model_info", "download_hf_file", "list_local_files",
    "search_knowledge", "list_knowledge_sources", "read_knowledge_source", "crawl_docs_site", "index_framework_docs",
    "list_targets", "get_target_capabilities", "get_hardware_probe", "probe_local_hardware", "create_target_module",
    "list_devices", "create_deploy_package", "deploy_to_device",
    "list_packs", "add_pack_from_index", "export_pack", "start_recipe_build",
    "run_mojo_oracle", "run_mojo_miracle", "get_status", "get_settings", "update_settings", "report_cannot_do",
    # the AI's hands in the open window (DECISIONS round 8, W4): one tool with an action field, the same for Ditto and
    # MCP clients (orchestrator/tools/tools_gui.py)
    "gui",
    # hidden: the 3.0.0 name of run_mojo_oracle, callable but never listed
    "run_doctor",
    # optional (default deny until the person enables them)
    "delete_artifact", "restore_knowledge_snapshot", "build_local_runtime", "start_local_runtime",
    "stop_local_runtime", "run_benchmark", "deploy_device_runtime", "list_trash", "restore_from_trash",
    "remove_pack",
    # later
    "probe_remote_device", "run_device_benchmark",
})


# --- notes and summary lines (framework facts only: ids the framework issued, states, counts) ------------------

VISIBILITY_NOTE = ("Builds with process 'other' run in another framework process (started_in: the GUI, the CLI, "
                   "another MCP server), read from the shared build state: read-only here; the person stops them "
                   "where they were started.")


def _status_summary(out) -> str:
    return f"Build request {fact(out.request_id)}: {fact(out.status)}, {fact(out.progress_percent)}% done."




def build_fields(service) -> dict:
    """How the fool-proof layer treats start_build's fields: target, format, board and quantization are closed
    lists computed from the target's capabilities (exact, canonical form if unique, or the target's declared
    aliases; never a near miss); the model, its revision and the dataset are never changed."""

    def safely(compute):
        def choices(args):
            try:
                return compute(args)
            except Exception:  # noqa: BLE001 - unknown yet (e.g. an invalid target): the service checks it later
                return None
        return choices

    def targets(_args):
        ids, aliases = service.target_choices()
        return Choices(tuple(ids), aliases)

    def caps(args):
        return service.capabilities(args["target"]) if args.get("target") else None

    def formats(args):
        c = caps(args)
        return Choices(tuple(c.formats), where=f"for {args['target']}") if c else None

    # Boards and quantizations of the whole target and format: a known value the combination does not support is
    # refused by the service with reason not_supported and the supported values (e.g. RKLLM on rk3566).
    def boards(args):
        c = caps(args)
        return Choices(tuple(c.boards), where=f"for {args['target']}") if c else None

    def quantizations(args):
        c = caps(args)
        cap = c.formats.get(args.get("format")) if c else None
        if cap is None:
            return None
        return Choices(tuple(cap.quantizations), dict(cap.aliases), f"for {args['format']} ({args['target']})")

    return {
        "model": FieldMeta(kind="path"),
        "revision": FieldMeta(kind="trust"),
        "target": FieldMeta(kind="choice", choices_from=safely(targets), list_tool="list_targets"),
        "format": FieldMeta(kind="choice", choices_from=safely(formats), list_tool="get_target_capabilities"),
        "board": FieldMeta(kind="choice", choices_from=safely(boards), list_tool="get_target_capabilities"),
        "quantization": FieldMeta(kind="choice", choices_from=safely(quantizations),
                                  list_tool="get_target_capabilities"),
        "dataset_path": FieldMeta(kind="path"),
    }


# request_id and job_id fields: see register_framework_tools (they name the build or job for a person); a job id
# has no listing tool, it comes from the tool that started the job.
JOB_EXAMPLE = {"job_id": "job_0123456789abcdef"}

# What approving a healing strategy does, in plain words (the question's first line).
HEALING_CONSEQUENCES = {
    "config_adjustment": "Sets the framework setting build_jobs to 1 (fewer parallel compile jobs for image builds), "
                         "then retries build request {rid} once.",
    "retry_build": "Retries build request {rid} once, without changes.",
}
# Strategies asked even when the tool is allowed. None is left: no automatic strategy deletes anything (round 5 f); a
# full Docker disk (docker_prune) is a manual step that points to the person's one deletion path for Docker images
# (Tools > Framework Build Images... or 'llm-cli doctor --fix --tier 2'), and approving a manual step is refused
# before anything is asked.
DESTRUCTIVE_STRATEGIES: frozenset = frozenset()


def healing_facts(service, request_id: str, approve: bool) -> CallFacts:
    """The facts of a decision on a pending healing proposal (two-phase confirmation): what the decision does, the
    proposal from server state (its texts come from the build log or an AI and are labelled as such) and its
    fingerprint, to which the decision is bound. A destructive strategy is asked even when the tool is allowed."""
    proposal = service.healing_proposal(request_id, approve)
    strategy = proposal["strategy"]
    # The build by its speaking name too: the first line is what the person reads first (DECISIONS round 4 item 1).
    named = f"{request_id} ({proposal['request']})" if proposal.get("request") else request_id
    if approve:
        consequence = HEALING_CONSEQUENCES.get(strategy, "Applies the proposed fix and retries build request {rid} "
                                                         "once.").format(rid=named)
    else:
        consequence = f"Dismisses the self-healing proposal of build request {named}; the failed build is not retried."
    source = "written by an AI" if proposal["ai_generated"] else "from the build log analysis"
    facts = []
    if proposal.get("request"):
        facts.append(Fact("Build request", f"{request_id}: {proposal['request']}"))
    facts += [Fact("Proposal", proposal["summary"], source), Fact("Cause", proposal["root_cause"], source),
              Fact("Fix or manual step", proposal["fix"], source),
              Fact("Strategy", strategy + (" (destructive: deletes data)" if strategy in DESTRUCTIVE_STRATEGIES else "")
                   + ("" if proposal["automatic"] else " (a manual step: the framework cannot apply it)"))]
    destructive = approve and strategy in DESTRUCTIVE_STRATEGIES
    return CallFacts(consequence=consequence, facts=tuple(facts),
                     bound={"request_id": request_id, "approve": approve, "proposal": proposal["fingerprint"]},
                     cap=Decision.ASK.value if destructive else None)


def _wait_seconds(requested: int, notes: List[str]) -> int:
    """The wait a call may take (at most MAX_WAIT_SECONDS); a shorter wait than asked is noted for the result."""
    if requested > job_service.MAX_WAIT_SECONDS:
        notes.append(f"wait_seconds was reduced to {job_service.MAX_WAIT_SECONDS} (the longest a wait may take); "
                     "call again to keep waiting.")
    return min(requested, job_service.MAX_WAIT_SECONDS)


def _job_out(job, ctx: ToolContext) -> JobStatusOut:
    error = None
    if job.error:
        notes = job.error.get("notes")
        error = JobErrorOut(**{k: job.error.get(k, "") for k in ("code", "message", "next")},
                            notes=[str(n) for n in notes] if isinstance(notes, list) else [])
    # An approved call's result belongs to the caller that asked for it.
    result = job.result if job.kind != "approval" or job.owner == ctx.owner else None
    # A job's external text (job_service.EXTERNAL_KEY, e.g. a device's output) goes into the marked field.
    external = []
    if isinstance(result, dict) and job_service.EXTERNAL_KEY in result:
        result = dict(result)
        external = [str(line) for line in result.pop(job_service.EXTERNAL_KEY) or []]
    return JobStatusOut(job_id=job.job_id, kind=job.kind, state=job.state,
                        progress=JobProgress(done=job.done, total=job.total, message=job.message),
                        result=result, external_text=external, error=error, owner=job_service.owner_actor(job.owner),
                        yours=job.owner == ctx.owner, created=job.created, updated=job.updated, note=job.note,
                        end_reason=getattr(job, "end_reason", None))


def register_framework_tools(registry: ToolRegistry, framework) -> ToolRegistry:
    service = framework.build_service
    jobs = getattr(framework, "job_service", None)
    if jobs is None:  # e.g. a stand-in framework: jobs in memory only
        jobs = JobService()
        try:
            framework.job_service = jobs
        except AttributeError:
            pass

    # Speaking names for the person (questions): never shown to a model, which keeps the ids and labels it sent.
    def target_name(value):
        names = {t.id: t.name for t in service.list_targets().targets}
        name = names.get(service.resolve_target(value))
        return name if name and name != value else None

    def local_file(value):
        if not str(value).startswith("lf_"):
            return None
        from orchestrator.tools.local_files import LocalFiles
        return LocalFiles.for_framework(framework).ref_of(str(value))

    def job_name(value):
        job = jobs.get(value)
        return f"a {job.kind} job ({job.state})"

    def request_name(value):
        label = getattr(service, "request_label", None)
        return label(value) if callable(label) else None

    request_id = {"request_id": FieldMeta(kind="id", list_tool="list_build_requests", describe=request_name)}
    job_id = {"job_id": FieldMeta(kind="id", describe=job_name)}
    fields = build_fields(service)
    fields["target"] = dataclasses.replace(fields["target"], describe=target_name)
    fields["model"] = dataclasses.replace(fields["model"], describe=local_file)
    fields["dataset_path"] = dataclasses.replace(fields["dataset_path"], describe=local_file)
    target_field = fields["target"]

    # The wait notes are the framework's own (limits); the registry puts ctx.notes into the result's `notes`.
    def get_build_status(inp: BuildStatusIn, ctx: ToolContext) -> BuildStatusOut:
        ctx.notes.extend(ctx.metadata.get("wait_notes", []))
        seconds = _wait_seconds(inp.wait_seconds, ctx.notes)
        if seconds:   # Ditto waits for this build: its end gives Ditto a turn in the GUI (Core/ditto_build_turns.py)
            from orchestrator.Core.ditto_build_turns import note_wait
            note_wait(ctx.actor, inp.request_id)
        if seconds and not ctx.metadata.get("waited"):  # Ditto: waits in its own thread; MCP waited on its loop
            service.wait_for_change(inp.request_id, seconds)
        status = service.get_status(inp.request_id)
        # an interrupted build of this request with finished stages kept: only the person decides (a pending
        # decision they answer in the GUI or at a terminal); the AI reads the labels and the fixed stage names
        from orchestrator.Core.interrupted_build import ai_notes
        ctx.notes.extend(ai_notes(framework, inp.request_id, ctx.actor))
        return status

    def get_job_status(inp: JobWaitIn, ctx: ToolContext) -> JobStatusOut:
        ctx.notes.extend(ctx.metadata.get("wait_notes", []))
        seconds = _wait_seconds(inp.wait_seconds, ctx.notes)
        if seconds and not ctx.metadata.get("waited"):
            jobs.wait(inp.job_id, seconds)
        return _job_out(jobs.get(inp.job_id), ctx)

    def resolve_healing(inp: ResolveHealingIn, ctx: ToolContext) -> BuildStatusOut:
        """Bound to the proposal the question showed (its fingerprint in the looked-up facts): a changed proposal is
        refused with nothing applied, and without those facts nothing is decided at all."""
        expect = ctx.facts.bound.get("proposal") if ctx.facts is not None else None
        if not expect:
            raise ToolError("The decision is not bound to a proposal the person was shown; nothing was applied.",
                            code="failed", next="Call resolve_healing again through the framework's tool registry.")
        return service.resolve_healing(inp.request_id, inp.approve, expect=expect)

    def cancel_job(inp: JobIn, ctx: ToolContext) -> CancelJobOut:
        before = jobs.get(inp.job_id).state
        job = jobs.cancel(inp.job_id)
        note = job.note
        if before in job_service.TERMINAL_STATES:
            note = f"The job had already ended ({before}); nothing was stopped."
        return CancelJobOut(job_id=job.job_id, state=job.state, note=note)

    # The tool's own default for a call (ToolSpec.default_decision): `who` is the caller's owner identity
    # (ToolContext.owner). Its own build or job: allow; anything else (no call, another caller's work, or no caller
    # named at all, which must never match work the GUI or the CLI started, owner ""): the policy.
    def own_build(inp, who: str):
        return Decision.ALLOW if inp is not None and who and service.owner_of(inp.request_id) == who else None

    def own_job(inp, who: str):
        return Decision.ALLOW if inp is not None and who and jobs.owner_of(inp.job_id) == who else None

    def sign_in():
        from orchestrator.llm.auth.signin import SignIn
        return SignIn(framework.config, getattr(framework, "secrets_manager", None))

    def get_status(inp: StatusIn, ctx: ToolContext) -> StatusOut:
        """The sections asked for; one that cannot be read is left out with a note (it never fails the others)."""
        caller, running = current_actor(), current_registry() or registry
        builders = {
            "framework": lambda: tools_status.framework_section(framework, running),
            "host": lambda: tools_status.host_section(framework, service.host_hardware),
            "local_runtime": lambda: tools_status.local_runtime_section(framework),
            "knowledge": lambda: tools_status.knowledge_section(framework),
            "ai_access": lambda: tools_status.ai_access_section(framework, sign_in),
            "permissions": lambda: permissions_section(caller, running),
            "audit": lambda: tools_status.audit_section(running, caller or "framework"),
        }
        values = {}
        for name in dict.fromkeys(inp.sections):
            try:
                values[name] = builders[name]()
            except Exception as e:  # noqa: BLE001 - a section that cannot be read never hides the others
                registry.logger.warning(f"Status section {name} not read: {redact_secrets(str(e))}")
                ctx.notes.append(f"The section {name} could not be read in this installation.")
            if values.get(name) is None and name == "host":
                ctx.notes.append("This computer's hardware could not be probed.")
        return StatusOut(**{k: v for k, v in values.items() if v is not None})

    def permissions_section(caller, running) -> PermissionsStatus:
        rows = running.permissions(caller)
        # loaded after permissions(): the damage flag below belongs to this load; a token's own permissions for it
        policy = running.policy(caller) if caller else running.policy()
        listed = sum(1 for r in rows if r["listed"])
        # The cap is a client's (MCP): Ditto in the framework's own process has none
        client_listed = 0 if caller == "ditto" else listed
        grant_caller = running._grant_caller(caller)[0] if caller else ""
        standing = [StandingApprovalOut(id=g["grant"].id, tool=g["grant"].tool, scope=g["grant"].summary,
                                        state=g["state"], created=g["grant"].created,
                                        created_via=g["grant"].created_via, expires=g["grant"].expires,
                                        uses=g["uses"], last_use=g["last_use"])
                    for g in (running.grant_rows(grant_caller) if grant_caller else [])]
        return PermissionsStatus(
            caller=caller or "framework", configured=policy.configured,
            damaged=bool(getattr(running.policy_store, "problem", None)), listed=listed,
            tools=[ToolPermission(**{k: r[k] for k in ToolPermission.model_fields}) for r in rows],
            standing_approvals=standing, no_tool_actions=[NoToolAction(action=a, where=w) for a, w in NO_TOOL_ACTIONS],
            warning=(f"{client_listed} tools are listed for MCP clients; some clients take at most "
                     f"{MAX_LISTED_TOOLS}." if client_listed > MAX_LISTED_TOOLS else ""))

    specs = [
        ToolSpec("get_build_status", "Build status",
                 "Status, progress, errors and a pending self-healing proposal of a build request. With "
                 "wait_seconds (at most 50) it waits until the build changes or ends; waiting never affects the "
                 'build. Example: {"request_id": "req_1a2b3c4d", "wait_seconds": 50}',
                 ToolTier.READ, BuildStatusIn, BuildStatusResult, get_build_status, idempotent=True,
                 takes_context=True, summary=_status_summary,
                 fields={**request_id, "wait_seconds": FieldMeta(kind="amount")},
                 long_poll=lambda i, seconds, report: service.wait_for_change_async(i.request_id, seconds, report)),
        # Mojo (round 7 item 11'): run_mojo_oracle, run_mojo_miracle and the hidden 3.0.0 name run_doctor
        *mojo_tool_specs(framework),
        ToolSpec("cancel_build", "Cancel a build",
                 "Stops a running or queued build request. Builds this client started are stopped without a "
                 "question; another client's build needs the user's confirmation.",
                 ToolTier.LOCAL_WRITE, RequestIn, BuildStatusResult, lambda i: service.cancel(i.request_id),
                 summary=_status_summary, fields=request_id, default_decision=own_build,
                 consequence="Stops a build request that another client started (its container is stopped)."),
        ToolSpec("get_job_status", "Job status",
                 "State, progress and result of a long operation (a job) other than a build. With wait_seconds (at "
                 "most 50) it waits until the job changes or ends; waiting never affects the job. "
                 'Example: {"job_id": "job_0123456789abcdef", "wait_seconds": 50}',
                 ToolTier.READ, JobWaitIn, JobStatusResult, get_job_status, idempotent=True, takes_context=True,
                 summary=lambda o: f"Job {fact(o.job_id)} ({fact(o.kind)}): {fact(o.state)}.",
                 fields={**job_id, "wait_seconds": FieldMeta(kind="amount")},
                 examples=({**JOB_EXAMPLE, "wait_seconds": 50},),
                 long_poll=lambda i, seconds, report: jobs.wait_async(i.job_id, seconds, report)),
        ToolSpec("cancel_job", "Cancel a job",
                 "Stops a long operation (a job) or withdraws a pending approval. Jobs this client started are "
                 "stopped without a question; another client's job needs the user's confirmation.",
                 ToolTier.LOCAL_WRITE, JobIn, CancelJobResult, cancel_job, idempotent=True, takes_context=True,
                 summary=lambda o: f"Job {fact(o.job_id)}: {fact(o.state)}.", fields=job_id,
                 examples=(JOB_EXAMPLE,), default_decision=own_job,
                 consequence="Stops a long operation (a job) that another client started."),
        ToolSpec("resolve_healing", "Decide on a self-healing proposal",
                 "Approves (applies the automatic fix and retries the build) or dismisses a pending proposal. The "
                 "person is shown the proposal from the framework's own state; a destructive fix is always asked.",
                 ToolTier.LOCAL_WRITE, ResolveHealingIn, BuildStatusResult, resolve_healing,
                 takes_context=True, summary=_status_summary,
                 prepare=lambda i, ctx: healing_facts(service, i.request_id, i.approve),
                 # kept destructive: it changes a setting (build_jobs) and retries a build; no strategy deletes
                 # anything (docker_prune is a manual step that points to the doctor's list)
                 destructive=True, fields={**request_id, "approve": FieldMeta(kind="trust")}),
        ToolSpec("get_status", "Framework status",
                 "The framework's state in sections: framework (version, Docker, targets, offline mode, active "
                 "builds), host (this computer's hardware and the recommended local runtime), local_runtime (the "
                 "self-built llama.cpp server), knowledge (the knowledge base), ai_access (sign-in states, never a "
                 "key), permissions (every tool's decision and why, standing approvals, and the actions no tool "
                 "does, with where the person does them) and audit (your own recent calls). Default: framework and "
                 'permissions. Example: {"sections": ["permissions", "audit"]}',
                 ToolTier.READ, StatusIn, StatusOut, get_status, idempotent=True, takes_context=True,
                 fields={"sections": FieldMeta(kind="value", split=True)},
                 examples=({"sections": ["permissions", "audit"]},),
                 summary=lambda o: (f"Permissions for {fact(o.permissions.caller)}: "
                                    + count(o.permissions.listed, "tool", "tools") + " listed."
                                    if o.permissions else "Framework status.")),
    ]
    # Targets, hardware probes and new target modules: their own module (design T5), first in the tool list.
    from orchestrator.tools.tools_targets import register_target_tools
    register_target_tools(registry, framework, service=service, jobs=jobs, target_field=target_field)
    # The build tools of T1 (start_build's two-phase question, check_build_request, artifacts by id, paged reads).
    from orchestrator.tools.tools_builds import build_tool_specs
    specs += build_tool_specs(registry, service, fields, request_id, VISIBILITY_NOTE,
                              # 3 unfinished builds and 20 starts a day per client (the limits table, BUILD_QUOTA)
                              ToolLimits(running=lambda ctx: len(service.active_requests(ctx.owner)), **BUILD_QUOTA),
                              BuildHandleOut)
    # Settings and the honest "cannot" path of T2.
    specs += tools_status.status_tool_specs(registry, framework, NO_TOOL_ACTIONS)
    # The AI's hands in the open window (W4): in the GUI process directly, elsewhere over the per-user bridge.
    from orchestrator.tools.tools_gui import gui_tool_specs
    specs += gui_tool_specs(registry, framework)
    for spec in specs:
        registry.register(spec)
    # Each group of later tools lives in its own module (design section 8, phase T), registered here.
    register_knowledge_tools(registry, framework)
    from orchestrator.tools.tools_framework_docs import register_framework_docs_tools  # N12: the own documents
    register_framework_docs_tools(registry, framework, jobs)
    register_healing_tools(registry, framework, request_id["request_id"])
    register_hf_tools(registry, framework, jobs)
    from orchestrator.tools.tools_crawl import register_crawl_tools  # Deep Ingest (T7)
    register_crawl_tools(registry, framework, jobs)
    from orchestrator.tools.tools_community import register_community_tools  # community zoo (T9)
    register_community_tools(registry, framework, jobs)
    # Building an installed recipe through start_build's two-phase path, and the cached index as a resource (T9).
    from orchestrator.tools.tools_recipes import recipe_tool_specs, register_packs_index_resource
    for spec in recipe_tool_specs(registry, service, fields, ToolLimits(
            running=lambda ctx: len(service.active_requests(ctx.owner)), **BUILD_QUOTA), BuildHandleOut):
        registry.register(spec)
    register_packs_index_resource(registry)
    # Devices and deploy (design 2.7, T8): trusted devices by id, packages as jobs, deploys always asked.
    from orchestrator.tools.tools_deploy import register_deploy_tools
    register_deploy_tools(registry, framework, service=service, jobs=jobs, target_field=target_field)
    # The optional local and device runtime tools (design 2.10, T14): registered with their runtime packages, hidden
    # (denied) until the person enables them.
    from orchestrator.tools.tools_runtime import register_runtime_tools
    register_runtime_tools(registry, framework, service=service, jobs=jobs)
    from orchestrator.tools.tools_optional import register_optional_tools  # T10: hidden until enabled
    register_optional_tools(registry, framework, jobs)
    return registry


# The audit HMAC key (its keyring entry, created once under a lock) lives in Core/vault_key.py next to the other
# keyring entries (consolidation chain c1-imports), so the chat feed and the CLI no longer import this tool module;
# the old names stay here (build_tool_registry reads the key through _audit_key).
from orchestrator.Core.vault_key import (AUDIT_KEY_ID, _KEY_THREAD_LOCK, _key_lock,  # noqa: E402,F401
                                         audit_key as _audit_key)


def _revision_anchor(kind: str, state_dir: Optional[Path] = None) -> RevisionAnchor:
    """Where the newest saved revision of a signed per-user file of the framework home `state_dir` is kept: its own
    entry in the OS keyring (Core/vault_key.revision_anchor, one per framework home; None: the default home's)."""
    from orchestrator.Core.vault_key import revision_anchor
    return revision_anchor(kind, state_dir)


def build_tool_registry(framework) -> ToolRegistry:
    """The registry with the user's policy, the audit log and all framework tools."""
    state_dir = Path(framework.config.user_state_dir)
    key = _audit_key(framework, state_dir)
    audit = AuditLog(state_dir / "audit.db", key=key)
    # The permissions and the tokens are signed with a key derived from the audit key; without a keyring an unkeyed
    # signature is accepted only while the audit log is not kept with a key either. With the keyring, the newest saved
    # revision of each is kept there too (an older signed copy or a removed permission file then denies everything).
    refuse_unkeyed = None if key else audit.kept_with_key
    registry = ToolRegistry(PolicyStore(state_dir, key=key, audit=audit, refuse_unkeyed=refuse_unkeyed,
                                        anchor=_revision_anchor("tool_policy", state_dir) if key else None), audit,
                            tokens=TokenStore(state_dir, key=key, refuse_unkeyed=refuse_unkeyed,
                                              anchor=_revision_anchor("mcp_tokens", state_dir) if key else None))
    # Tools, resources and prompts all go through this one registry: the same policy, limits and audit (design S10/S11).
    from orchestrator.tools.framework_prompts import register_framework_prompts
    from orchestrator.tools.framework_resources import register_framework_resources
    registry = register_framework_resources(register_framework_tools(registry, framework), framework)
    from orchestrator.runtime.probe_service import HardwareProbes
    registry = register_framework_prompts(registry, framework,
                                          probe_text=lambda pid: HardwareProbes.for_framework(framework).text(pid))
    # Pending approvals: a client that cannot ask gets its question shown in the framework instead (design 6.9).
    from orchestrator.tools.approvals import ApprovalService, ApprovalStore
    registry.attach_approvals(ApprovalService(ApprovalStore(state_dir, key=key, refuse_unkeyed=refuse_unkeyed),
                                              framework.job_service))
    return registry
