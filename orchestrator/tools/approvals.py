#!/usr/bin/env python3
"""
Pending approvals (MCP design 6.9): when a call needs a person's "yes" and the client cannot ask them (it declared no
form elicitation), the question is longer than an elicitation may be, or the person set the tool to be confirmed in
the framework, the question appears in the framework instead of the call being refused: a notice in the GUI with the
approval window, and `llm-cli tools approvals` in a terminal. The person approves or declines there; the framework
then runs the call itself, under the original caller, and the client reads the result with get_job_status.

An approval is bound to the caller, the tool, the hash of the normalized arguments and the hash of the question text
the person saw, and the question shows the facts the tool looked up for it (two-phase confirmation). It runs once,
expires after 15 minutes, and ends with the session that asked or when the client withdraws it with cancel_job. If the
question would read differently when the person approves (a fact behind it changed), nothing runs and the new question
is shown instead; otherwise the handler gets exactly the facts that were shown. A pending approval is a question to
the person, so the limits on questions apply to it (a recent "no" to the same call, a paused tool, 30 questions per
hour, 5 open ones per client), and a "no" here counts like a "no" in any other window.

What "would read differently" means is the facts' decision digest (delegation design 4.4, finding F3): free space or
a byte count that changes while the question waits (a volatile fact) never asks the person again; a changed warning,
size class, commit, image digest or any other fact does, and so does a question that changed outside the facts.

The approvals are kept in the per-user folder (`tool_approvals.json`, mode 0600), signed like the tool permissions;
only framework code writes the file, and no tool, resource or prompt can create, list, approve or decline an approval.
"""

import hashlib
import json
import os
import secrets
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from orchestrator.tools.confirmation import MAX_FRAMEWORK, visible
from orchestrator.tools.grants import DECLINED_COOLDOWN, DURATION_LABELS, GrantOffer
from orchestrator.tools.registry import caller_label
from orchestrator.tools.signing import SIGNATURE_FIELD, SignatureError, sign, verify
from orchestrator.utils.helpers import redact_secrets
from orchestrator.utils.logging import get_logger

APPROVAL_TTL = 15 * 60          # seconds a pending approval waits for the person
MAX_PENDING_PER_CALLER = 5      # open approvals one client may have at a time (design 6.5)
POLL_SECONDS = 1.0              # how often the process that holds the call looks for the person's decision
KEEP_FINISHED = 24 * 3600       # finished records are kept this long for the CLI and the GUI, then pruned
MAX_RECORDS = 200
STATES = ("pending", "approved", "declined", "running", "done", "failed", "expired", "withdrawn")
OPEN_STATES = frozenset({"pending", "approved", "running"})
APPROVAL_ID_RE = r"^ap_[0-9a-f]{16}$"
_SHA256 = r"^[0-9a-f]{64}$"

logger = get_logger("Approvals")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _parse(text: str) -> datetime:
    try:
        moment = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)  # unreadable: treated as long past
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def arguments_sha256(arguments: Any) -> str:
    text = json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def question_sha256(question: str) -> str:
    return hashlib.sha256(question.encode("utf-8", "surrogatepass")).hexdigest()


def actor_label(actor: str) -> str:
    """Who acted, as the start of a sentence a person reads ("The client with the MCP token 'laptop'"), never the raw
    caller id."""
    if actor == "mcp":
        return "A local stdio client"
    if actor.startswith("mcp:"):
        return f"The client with the MCP token '{actor[4:]}'"
    return caller_label(actor)


def tool_title(registry: Any, name: str) -> str:
    """A tool's title as the person knows it from the windows (its name when the tool is not registered)."""
    try:
        return registry.get(name).title
    except Exception:
        return name


class ApprovalRecord(BaseModel):
    """One pending approval as stored (and shown to the person)."""
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=APPROVAL_ID_RE)
    caller: str = Field(max_length=64)
    client: str = Field(default="", max_length=400)  # self-reported label, already cleaned
    tool: str = Field(max_length=64)
    title: str = Field(max_length=200)
    args_sha256: str = Field(pattern=_SHA256)
    question: str = Field(max_length=MAX_FRAMEWORK + 2000)
    question_sha256: str = Field(pattern=_SHA256)
    created: str
    expires: str
    state: Literal["pending", "approved", "declined", "running", "done", "failed", "expired", "withdrawn"]
    # "call": a call waits for the person; "grant_request": an elicitation asked the framework to stop asking for such
    # calls (the call itself already ran), which only the person can turn into a standing approval here.
    kind: Literal["call", "grant_request"] = "call"
    job_id: Optional[str] = Field(default=None, pattern=r"^job_[a-z0-9]{16}$")
    pid: int = Field(ge=0)                          # the process that holds the call and runs it
    owner: str = Field(default="", max_length=120)  # the client's session (its requests end with it)
    decided_by: Optional[Literal["cli", "gui"]] = None
    decided_at: Optional[str] = None
    note: str = Field(default="", max_length=500)
    grant: Optional[GrantOffer] = None              # "do not ask again" the window may offer, from framework facts
    choice: Optional[Dict[str, str]] = None         # the width and duration the person chose for it

    @property
    def caller_class(self) -> str:
        return caller_label(self.caller)

    def expired(self, now: Optional[datetime] = None) -> bool:
        return (now or _now()) >= _parse(self.expires)

    def seconds_left(self, now: Optional[datetime] = None) -> int:
        return max(0, int((_parse(self.expires) - (now or _now())).total_seconds()))

    def holder_alive(self) -> bool:
        """Whether the process that holds the call still runs (a client's session that crashed leaves its pending
        approvals behind until they expire; they can no longer be run)."""
        if self.pid == os.getpid():
            return True
        if os.name != "posix":  # os.kill(pid, 0) sends Ctrl+C on Windows: there the expiry ends stale approvals
            return True
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return False
        except (PermissionError, OSError):
            return True
        return True


class ApprovalFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    approvals: List[ApprovalRecord] = Field(default_factory=list)


class ApprovalStoreError(Exception):
    """The approval cannot be decided (unknown, decided already, expired, the store cannot be used)."""


class ApprovalStore:
    """The pending approvals in the per-user folder (`tool_approvals.json`, mode 0600), signed (orchestrator/tools/
    signing.py) and changed under a cross-process lock by the processes that hold calls (MCP servers) and by the
    person's windows (CLI, GUI). A file that is damaged, edited by hand or unsigned holds no approval anyone can
    decide."""

    FILE_NAME = "tool_approvals.json"
    LOCK_NAME = ".tool_approvals.lock"
    KIND = "tool_approvals"

    def __init__(self, state_dir: Path, key: Optional[bytes] = None,
                 refuse_unkeyed: Optional[Callable[[], bool]] = None):
        self.path = Path(state_dir) / self.FILE_NAME
        self.lock_path = Path(state_dir) / self.LOCK_NAME
        self.key = key
        self.refuse_unkeyed = refuse_unkeyed
        self.problem: Optional[str] = None
        self._lock = threading.Lock()

    def _read(self) -> List[ApprovalRecord]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("not a JSON object")
            data = ApprovalFile.model_validate({k: v for k, v in raw.items() if k != SIGNATURE_FIELD})
            verify(self.KIND, raw, self.key, self.refuse_unkeyed)
        except FileNotFoundError:
            self.problem = None
            return []
        except (OSError, ValueError, ValidationError, RecursionError, SignatureError) as e:
            first = (str(e).splitlines() or [type(e).__name__])[0][:200]
            self.problem = (f"{self.path} cannot be used ({first}); no pending approval in it can be decided. New "
                            "requests replace it.")
            return []
        self.problem = None
        return list(data.approvals)

    def _write(self, records: List[ApprovalRecord]) -> None:
        cutoff = _now() - timedelta(seconds=KEEP_FINISHED)
        kept = [r for r in records if r.state in OPEN_STATES or _parse(r.decided_at or r.created) > cutoff]
        kept = kept[-MAX_RECORDS:]
        data = sign(self.KIND, ApprovalFile(approvals=kept).model_dump(mode="json"), self.key)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".tool_approvals.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(data, indent=2, ensure_ascii=False))
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            # Windows: while another window or the approval watcher reads the file, replacing it fails for a moment
            from orchestrator.Core.vault_key import replace_file
            replace_file(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _change(self, change: Callable[[List[ApprovalRecord]], Any], *, over_damaged: bool = False) -> Any:
        from orchestrator.utils.file_lock import file_lock
        with self._lock, file_lock(self.lock_path, timeout=10):
            records = self._read()
            if self.problem and not over_damaged:
                raise ApprovalStoreError(self.problem)
            result = change(records)
            self._write(records)
            return result

    # --- reading -----------------------------------------------------------------------------------------------

    def list(self) -> List[ApprovalRecord]:
        return self._read()

    def get(self, approval_id: str) -> Optional[ApprovalRecord]:
        return next((r for r in self._read() if r.id == approval_id), None)

    def fingerprint(self) -> Optional[Tuple[int, int, int]]:
        """Changes whenever the file is replaced; a cheap check before reading it again."""
        try:
            st = os.stat(self.path)
        except OSError:
            return None
        return st.st_ino, st.st_size, st.st_mtime_ns

    def pending(self) -> List[ApprovalRecord]:
        """Approvals the person can still decide (pending, not expired, their session still running)."""
        now = _now()
        return [r for r in self._read() if r.state == "pending" and not r.expired(now) and r.holder_alive()]

    # --- changing ----------------------------------------------------------------------------------------------

    def add(self, record: ApprovalRecord) -> None:
        def change(records):
            records.append(record)
        self._change(change, over_damaged=True)  # a damaged file holds nothing that could be decided anyway

    def update(self, approval_id: str, **fields: Any) -> Optional[ApprovalRecord]:
        def change(records):
            for i, record in enumerate(records):
                if record.id == approval_id:
                    records[i] = record.model_copy(update=fields)
                    ApprovalRecord.model_validate(records[i].model_dump())
                    return records[i]
            return None
        try:
            return self._change(change)
        except ApprovalStoreError:
            return None

    def decide(self, approval_id: str, approve: bool, actor: str, expected_question: str = "",
               record_decision: Optional[Callable[[ApprovalRecord], None]] = None,
               choice: Optional[Tuple[str, str]] = None) -> ApprovalRecord:
        """The person's answer from the framework's own window (`actor`: cli or gui). `expected_question`: the hash of
        the question the person was shown (a question that changed meanwhile is not decided). `choice`: "approve and
        do not ask again" with this width and duration (only within the record's offer). `record_decision` is called
        (to audit it and to save a standing approval) before the decision is stored; if it raises, nothing is
        stored."""
        if actor not in ("cli", "gui"):
            raise ApprovalStoreError("Only the framework's own windows decide an approval.")

        def change(records):
            for i, record in enumerate(records):
                if record.id != approval_id:
                    continue
                if record.state != "pending":
                    raise ApprovalStoreError(f"Approval {approval_id} is {record.state}, not waiting for you.")
                if record.expired():
                    raise ApprovalStoreError(f"Approval {approval_id} has expired; the AI client can ask again.")
                if not record.holder_alive():
                    raise ApprovalStoreError(f"The session that asked for approval {approval_id} has ended; nothing "
                                             "would run it.")
                if expected_question and expected_question != record.question_sha256:
                    raise ApprovalStoreError(f"The question of approval {approval_id} changed while you read it; "
                                             "look at it again.")
                if approve and record.kind == "grant_request" and choice is None:
                    raise ApprovalStoreError(f"{approval_id} asks to stop asking: approve it as a standing approval "
                                             "(--always) or decline it.")
                if approve and choice is not None:
                    offer = record.grant
                    if offer is None or choice[0] not in offer.scopes or choice[1] not in offer.durations:
                        raise ApprovalStoreError(f"'Do not ask again' with that width and duration is not offered for "
                                                 f"{approval_id}.")
                decided = record.model_copy(update={
                    "state": "approved" if approve else "declined", "decided_by": actor, "decided_at": _iso(_now()),
                    "choice": {"width": choice[0], "duration": choice[1]} if approve and choice else None})
                if record_decision is not None:
                    record_decision(decided)
                records[i] = decided
                return decided
            raise ApprovalStoreError(f"There is no approval '{approval_id}' ('llm-cli tools approvals' lists them).")
        return self._change(change)


def stable_facts(facts: Any) -> Any:
    """The facts as far as a decision depends on them (design 4.4): the consequence template instead of the rendered
    consequence, no volatile fact, no volatile number, warnings by code only (their numbers, such as the free space
    left, change while a question waits). Used to tell a question that only shows other free space from one that
    asks something new."""
    import dataclasses

    from orchestrator.tools.spec import CallWarning, volatile_numbers
    volatile = set(volatile_numbers(getattr(facts, "tool", "") or ""))
    return dataclasses.replace(
        facts, consequence=facts.consequence_template or facts.consequence,  # no template: all of it counts
        facts=tuple(f for f in facts.facts if not f.volatile),
        numbers={k: v for k, v in facts.numbers.items() if k not in volatile},
        warnings=tuple(CallWarning(w.code) for w in facts.warnings))


def apply_decision(registry: Any, record: ApprovalRecord, actor: str) -> None:
    """Everything a person's decision does before it is stored: the audit entry, and for "approve and do not ask
    again" the standing approval itself, saved with the policy (`actor`: cli or gui). Raises when either cannot be
    written; then nothing is decided."""
    always = record.state == "approved" and record.choice and record.grant is not None
    if always:  # checked before the decision is recorded: an offer that cannot be used decides nothing
        registry.check_grant(record.grant, record.choice["width"], record.choice["duration"], actor)
    audit_decision(registry, record, actor)
    if always:
        registry.create_grant(record.grant, record.choice["width"], record.choice["duration"], actor, actor)


def offer_text(offer: GrantOffer) -> str:
    """The scopes and durations an offer allows, and its warning, as the window shows them."""
    lines = ["Do not ask again for:"] + [f"  - {summary}" for summary in offer.summaries.values()]
    lines.append("For how long: " + "; ".join(DURATION_LABELS[d] for d in offer.durations))
    return "\n".join(lines) + "\n\n" + offer.warning


def audit_decision(registry: Any, record: ApprovalRecord, actor: str) -> None:
    """A person's decision on a pending approval (`actor`: cli or gui), in the audit log before it is stored; raises
    when it cannot be written, and then nothing is decided."""
    if getattr(registry, "audit", None) is None:
        raise RuntimeError("the audit log is not available")
    try:
        tier = registry.get(record.tool).tier.value
    except Exception:
        tier = "unknown"
    approved = record.state == "approved"
    registry.audit.record(actor, record.tool, tier, "person", "approval_decided",
                          arguments={"approval": record.id, "caller": record.caller, "kind": record.kind,
                                     "always": record.choice or None}, approved=approved,
                          extra={"confirm_channel": f"approval:{record.id}", "question_sha256": record.question_sha256,
                                 "answer": "accepted" if approved else "declined"})


# --- the process that holds the call --------------------------------------------------------------------------

@dataclass
class _Waiting:
    approval_id: str
    tool: str
    arguments: Dict[str, Any]
    ctx: Any                 # the original caller's ToolContext
    job_id: str
    key: Tuple[str, str, str, str]   # owner, tool, arguments hash, Ditto's session ("" for MCP callers)
    question_sha256: str
    expires: datetime
    running: bool = False
    facts: Any = None        # what the tool looked up for the question shown (CallFacts), None for a tool without
    facts_sha256: str = ""   # the facts' decision digest when the question was shown ("" without facts)
    stable_sha256: str = ""  # the question with its volatile values left out, when it was shown


@dataclass
class ApprovalService:
    """Creates pending approvals for calls nobody could be asked about, watches the store for the person's decision,
    and runs approved calls under the original caller. One per registry (attached with ToolRegistry.attach_approvals),
    in the process that holds the calls (an MCP server)."""
    store: ApprovalStore
    jobs: Any                                   # the JobService of this process
    ttl: float = APPROVAL_TTL
    poll: float = POLL_SECONDS
    registry: Any = None
    _waiting: Dict[str, _Waiting] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _thread: Optional[threading.Thread] = None
    _closed: bool = False                       # the process shuts down: no new approvals, the watch ends

    # --- the question --------------------------------------------------------------------------------------------

    def question(self, spec: Any, inp: Any, ctx: Any, facts: Any = None) -> str:
        """The question the framework's window shows: the caller class first, the client's own name marked as
        self-reported, then the registry's question (confirmation text v2: the consequence and the facts the tool
        looked up, who asks, every argument with its speaking name and the repaired values marked), up to 16,000
        characters (CallTooLong otherwise)."""
        client = visible(ctx.client) if getattr(ctx, "client", "") else "unnamed client"
        shared = " (they share one set of permissions)" if ctx.actor == "mcp" else ""
        header = f"Caller: {caller_label(ctx.actor)}{shared}\nClient: {client} (self-reported)\n"
        return header + self.registry.question_text(spec, inp.model_dump(mode="json"), ctx, facts, MAX_FRAMEWORK)

    # --- requests ------------------------------------------------------------------------------------------------

    def request(self, spec: Any, inp: Any, arguments: Dict[str, Any], ctx: Any, decision: Any) -> Any:
        """The job of the pending approval for this call: the waiting one when the same caller asked for the same
        call already, else a new one (recorded as a question before it is shown and counted toward the questions per
        hour). The question shows the facts the tool looked up (ctx.facts); the handler is bound to them. LimitHit
        when the caller has too many open approvals, the person declined this exact call recently, the tool's
        questions are paused or the client asked too many questions this hour; AuditUnavailable when the question
        cannot be recorded."""
        from orchestrator.tools.registry import LimitHit, ToolError
        # Ditto has one owner for all its tasks: its session tells two tasks apart, so one task never receives another's
        # approval (an MCP client's owner already names its connection).
        key = (ctx.owner, spec.name, arguments_sha256(arguments), ctx.session if ctx.actor == "ditto" else "")
        if self._closed:
            raise ToolError(f"'{spec.title}' was not put to the person: the framework is shutting down.",
                            next="Ask again once the framework runs again.")
        with self._lock:
            for waiting in self._waiting.values():
                if waiting.key == key:
                    return self.jobs.get(waiting.job_id)
            mine = sum(1 for w in self._waiting.values() if w.key[0] == ctx.owner)
        if mine >= MAX_PENDING_PER_CALLER:
            raise LimitHit(f"'{spec.title}' was not put to the person: this client already has "
                           f"{MAX_PENDING_PER_CALLER} calls waiting for approval.", code="limit_reached",
                           next="Wait until the person has decided the open ones (poll their jobs with "
                                "get_job_status), or withdraw one with cancel_job.")
        refusal = self.registry.question_refusal(spec, inp, ctx)  # a recent "no", a paused tool, too many questions
        if refusal is not None:
            raise refusal
        facts = getattr(ctx, "facts", None)
        question = self.question(spec, inp, ctx, facts)
        digest = question_sha256(question)
        decision_digest, stable = self._decision_of(spec, inp, ctx, facts)
        offer = self.registry.grant_offer(spec, inp, ctx) if spec.grant is not None else None
        approval_id = f"ap_{secrets.token_hex(8)}"
        job = self.jobs.create("approval", ctx.owner, state="waiting_for_approval",
                               on_cancel=lambda: self.withdraw(approval_id, "cancelled",
                                                               "Withdrawn by the client or its session ended."))
        try:
            # Recorded before it is shown; for a call that changes something, not shown when it cannot be recorded.
            self.registry._audit(ctx, spec, decision, "confirmation_requested", arguments,
                                 required=spec.tier.value != "read", confirm_channel=f"approval:{approval_id}",
                                 question_sha256=digest)
            now = _now()
            expires = now + timedelta(seconds=self.ttl)
            self.store.add(ApprovalRecord(id=approval_id, caller=ctx.actor, client=str(ctx.client or "")[:400],
                                          tool=spec.name, title=spec.title[:200], args_sha256=key[2],
                                          question=question, question_sha256=digest, created=_iso(now),
                                          expires=_iso(expires), state="pending", job_id=job.job_id,
                                          pid=os.getpid(), owner=str(ctx.owner)[:120], grant=offer))
            self.registry.asked(ctx)  # a pending approval counts toward the questions per hour
        except BaseException:
            self.jobs.update(job.job_id, state="failed", error={"code": "failed",
                                                                "message": "The approval could not be recorded."})
            raise
        self.jobs.update(job.job_id, note=f"Waiting for the person to approve '{spec.title}' in the framework.")
        with self._lock:
            self._waiting[approval_id] = _Waiting(approval_id, spec.name, dict(arguments), ctx, job.job_id, key,
                                                  digest, expires, facts=facts, facts_sha256=decision_digest,
                                                  stable_sha256=stable)
        self._emit("approval", approval_id, spec.name, ctx, "pending", job.job_id)
        self._start()
        return job

    def _decision_of(self, spec: Any, inp: Any, ctx: Any, facts: Any) -> Tuple[str, str]:
        """(the facts' decision digest, the hash of the question without its volatile values) for a call with looked-up
        facts; ("", "") without facts (then only the question text itself counts)."""
        if facts is None or not hasattr(facts, "decision_digest"):
            return "", ""
        try:
            return facts.decision_digest(), question_sha256(self.question(spec, inp, ctx, stable_facts(facts)))
        except Exception as e:  # noqa: BLE001 - without the digest the question text decides, as before
            logger.info(f"No decision digest for {spec.name}: {redact_secrets(str(e))}")
            return "", ""

    def _same_decision(self, item: "_Waiting", record: Any, spec: Any, inp: Any, facts: Any) -> bool:
        """Whether only volatile values changed since the person was shown the question (free space, exact byte
        counts): the same decision digest and the same question once those values are left out, for the question
        this process put into the store. Then the person is not asked again (F3)."""
        if not item.facts_sha256 or record.question_sha256 != item.question_sha256:
            return False
        digest, stable = self._decision_of(spec, inp, item.ctx, facts)
        return bool(digest) and digest == item.facts_sha256 and stable == item.stable_sha256

    def _emit(self, kind: str, approval_id: str, tool: str, ctx: Any, state: str, job_id: str = "") -> None:
        """Tells the registry's observers (the chat feed) that an approval was created or ended: ids and states only."""
        events = getattr(self.registry, "events", None)
        if events is not None:
            events.emit(kind, approval_id=approval_id, tool=tool, actor=str(getattr(ctx, "actor", "")),
                        owner=str(getattr(ctx, "owner", "")), state=state, job_id=job_id)

    def request_grant(self, spec: Any, inp: Any, ctx: Any) -> Optional[ApprovalRecord]:
        """An elicitation was accepted with "ask the framework to stop asking for this": a request the person confirms
        (or not) in the framework's window. Never a standing approval by itself. At most one open request per caller
        and tool; a scope the person declined is not requested again for 24 hours; a request is a question to the
        person and counts toward the client's questions per hour (none is made once they are used up). None when
        nothing is requested."""
        offer = self.registry.grant_offer(spec, inp, ctx)
        if offer is None:
            return None
        scope_key = json.dumps(offer.scopes, sort_keys=True)
        now = _now()
        for record in self.store.list():
            if record.kind != "grant_request" or record.caller != ctx.actor or record.tool != spec.name:
                continue
            if record.state == "pending" and not record.expired(now) and record.holder_alive():
                return record  # one open request per caller and tool
            if (record.state == "declined" and record.grant is not None
                    and json.dumps(record.grant.scopes, sort_keys=True) == scope_key
                    and now - _parse(record.decided_at or record.created) < DECLINED_COOLDOWN):
                return None  # the person said no to this scope less than 24 hours ago
        if self.registry.questions_full(ctx) is not None:
            return None  # this client asked the person enough this hour
        client = visible(ctx.client) if getattr(ctx, "client", "") else "unnamed client"
        shared = " (they share one set of permissions)" if ctx.actor == "mcp" else ""
        question = (f"Caller: {caller_label(ctx.actor)}{shared}\nClient: {client} (self-reported)\n"
                    f"This AI client asks the framework to stop asking you before '{spec.title}' calls like the one "
                    "you just confirmed in it.\n\n" + offer_text(offer))
        approval_id = f"ap_{secrets.token_hex(8)}"
        digest = question_sha256(question)
        self.registry._audit(ctx, spec, self.registry.decide(spec, inp, ctx), "confirmation_requested", {},
                             confirm_channel=f"approval:{approval_id}", question_sha256=digest,
                             detail="The client asked to stop asking for such calls (a standing approval request).")
        self.registry.asked(ctx)
        record = ApprovalRecord(id=approval_id, caller=ctx.actor, client=str(ctx.client or "")[:400], tool=spec.name,
                                title=spec.title[:200], args_sha256=arguments_sha256({}), question=question,
                                question_sha256=digest, created=_iso(now),
                                expires=_iso(now + timedelta(seconds=self.ttl)), state="pending",
                                kind="grant_request", pid=os.getpid(), owner=str(ctx.owner)[:120], grant=offer)
        self.store.add(record)
        return record

    def end_owner(self, owner: str) -> None:
        """A client's session ended: its open requests to stop asking can no longer be confirmed."""
        for record in self.store.list():
            if record.kind == "grant_request" and record.owner == owner and record.state == "pending":
                self.store.update(record.id, state="withdrawn", note="The client's session ended.")

    def withdraw(self, approval_id: str, answer: str = "cancelled", detail: str = "") -> None:
        """The client gave up (cancel_job) or its session ended: the approval can no longer be decided."""
        with self._lock:
            waiting = self._waiting.pop(approval_id, None)
        if waiting is None or waiting.running:
            return
        self.store.update(approval_id, state="withdrawn",
                          note=(detail or "Withdrawn by the client or its session ended.")[:500])
        self._refuse(waiting, "cancelled", detail or "The pending approval was withdrawn.", answer)
        self._emit("approval_end", approval_id, waiting.tool, waiting.ctx, "withdrawn", waiting.job_id)

    def close(self) -> None:
        """The process shuts down (framework.shutdown): every call still waiting for the person is withdrawn and
        audited, its job ends, and the watch stops; nothing runs after this."""
        detail = "The framework shut down before the person decided; nothing ran."
        with self._lock:
            self._closed = True
            waiting = [w for w in self._waiting.values() if not w.running]
        for item in waiting:
            self.withdraw(item.approval_id, "cancelled", detail)
            self.jobs.update(item.job_id, state="cancelled", note=detail)

    # --- watching for the person's decision ------------------------------------------------------------------------

    def _start(self) -> None:
        with self._lock:
            if self._closed or (self._thread is not None and self._thread.is_alive()):
                return
            self._thread = threading.Thread(target=self._watch, name="approval-watch", daemon=True)
            self._thread.start()

    def _watch(self) -> None:
        import time
        while True:
            time.sleep(self.poll)
            try:
                self.poll_once()
            except Exception as e:  # keep watching; the next round tries again
                logger.warning(f"Checking pending approvals failed: {redact_secrets(str(e))}")
            with self._lock:
                if self._closed or not any(not w.running for w in self._waiting.values()):
                    self._thread = None
                    return

    def poll_once(self, now: Optional[datetime] = None) -> None:
        """One look at the store: runs what the person approved, ends what they declined and what expired."""
        now = now or _now()
        with self._lock:
            waiting = [w for w in self._waiting.values() if not w.running]
        if not waiting:
            return
        records = {r.id: r for r in self.store.list()}
        for item in waiting:
            record = records.get(item.approval_id)
            if record is not None and record.state == "approved" and record.args_sha256 == item.key[2] \
                    and record.caller == item.ctx.actor and record.tool == item.tool:
                self._approved(item, record)
            elif record is not None and record.state == "declined":
                self._end(item, "declined")
            elif now >= item.expires:
                self._end(item, "expired")

    def _end(self, item: _Waiting, how: str) -> None:
        with self._lock:
            if self._waiting.pop(item.approval_id, None) is None:
                return
        try:  # what the tool staged for the question (a quarantined pack) is dropped: the call never runs
            import dataclasses
            spec = self.registry.get(item.tool)
            self.registry.discard_staged(spec, self.registry.validate_input(spec, item.arguments),
                                         dataclasses.replace(item.ctx, facts=item.facts))
        except Exception as e:
            logger.warning(f"Could not discard the staged work of approval {item.approval_id}: {redact_secrets(str(e))}")
        if how == "declined":
            self._refuse(item, "not_confirmed", "The person declined it in the framework.", "declined")
            try:  # a "no" in the framework's window counts like any other: the same call is not asked again soon
                spec = self.registry.get(item.tool)
                self.registry.declined(spec, self.registry.validate_input(spec, item.arguments), item.ctx)
            except Exception as e:
                logger.warning(f"Could not record the 'no' to approval {item.approval_id}: {redact_secrets(str(e))}")
            self.jobs.update(item.job_id, state="failed", error={
                "code": "declined", "message": "The person declined this call in the framework.",
                "next": "Do not retry it unless the person asks for it.", "notes": self._notes(item)})
        else:
            self.store.update(item.approval_id, state="expired")
            minutes = max(1, round(self.ttl / 60))
            self._refuse(item, "not_confirmed", f"Nobody approved it within {minutes} minutes.", "unanswered")
            self.jobs.update(item.job_id, state="expired", note=f"Nobody approved it within {minutes} minutes.")
        self._emit("approval_end", item.approval_id, item.tool, item.ctx, how, item.job_id)

    def _refuse(self, item: _Waiting, outcome: str, detail: str, answer: str) -> None:
        try:
            self.registry.refuse(item.tool, item.arguments, item.ctx, outcome=outcome, detail=detail,
                                 confirm_channel=f"approval:{item.approval_id}",
                                 question_sha256=item.question_sha256, answer=answer)
        except Exception as e:
            logger.warning(f"Could not audit the end of approval {item.approval_id}: {redact_secrets(str(e))}")

    def _approved(self, item: _Waiting, record: ApprovalRecord) -> None:
        """Runs the approved call, unless its question would read differently now (the tool's prepare runs again, so a
        changed fact changes the question): then the new question is put to the person instead (nothing runs).
        Otherwise the handler gets exactly the facts of the question the person saw, which it must use or refuse."""
        spec = self.registry.get(item.tool)
        try:
            inp = self.registry.validate_input(spec, item.arguments)
            facts = self.registry.look_up(spec, inp, item.ctx)  # looked up again, as the person decides now
            question = self.question(spec, inp, item.ctx, facts)
        except Exception as e:  # the call is no longer valid (a tool changed, a fact is gone): it cannot run
            code = getattr(e, "code", "") or "failed"
            self._fail(item, code if isinstance(code, str) else "failed",
                       f"The approved call can no longer be checked ({redact_secrets(str(e))}).", error=e)
            return
        digest = question_sha256(question)
        changed = digest != record.question_sha256 or digest != item.question_sha256
        if changed and self._same_decision(item, record, spec, inp, facts):
            # only free space or byte counts moved: the person's yes stands, and the handler gets the current facts
            # (its disk reservation is taken for what is true now)
            changed = False
            with self._lock:
                if self._waiting.get(item.approval_id) is item:
                    item.facts = facts
        if changed:
            try:
                self.registry._audit(item.ctx, spec, self.registry.decide(spec, inp, item.ctx),
                                     "confirmation_requested", item.arguments, required=spec.tier.value != "read",
                                     confirm_channel=f"approval:{item.approval_id}", question_sha256=digest)
            except Exception as e:  # AuditUnavailable: the new question is not shown, nothing runs
                self._fail(item, "failed", redact_secrets(str(e)))
                return
            expires = _now() + timedelta(seconds=self.ttl)
            # What "do not ask again" may cover is computed again from the facts behind the new question.
            offer = self.registry.grant_offer(spec, inp, item.ctx) if spec.grant is not None else None
            decision_digest, stable = self._decision_of(spec, inp, item.ctx, facts)
            with self._lock:
                if self._waiting.get(item.approval_id) is not item:  # withdrawn meanwhile: nothing is asked
                    return
                item.question_sha256, item.expires, item.facts = digest, expires, facts
                item.facts_sha256, item.stable_sha256 = decision_digest, stable
            self.store.update(item.approval_id, state="pending", question=question, question_sha256=digest,
                              decided_by=None, decided_at=None, expires=_iso(expires), grant=offer, choice=None,
                              note="The question changed after it was approved; it is asked again.")
            self.jobs.update(item.job_id, note="The facts behind the question changed; the person is asked again.")
            return
        with self._lock:
            if self._waiting.get(item.approval_id) is not item:  # withdrawn while it was checked: nothing runs
                return
            if item.running:  # another look at the store saw the same yes at the same time and started it
                return
            item.running = True
        self.store.update(item.approval_id, state="running")
        threading.Thread(target=self._run, args=(item, record), name=f"approval-{item.approval_id}",
                         daemon=True).start()

    def _run(self, item: _Waiting, record: ApprovalRecord) -> None:
        import dataclasses

        from orchestrator.tools.registry import ToolError
        job = self.jobs.get(item.job_id)
        # The facts the person saw are the ones the handler must use; the rate was counted when the call was made.
        ctx = dataclasses.replace(item.ctx, confirm=None, confirm_channel=f"approval:{item.approval_id}",
                                  question_sha256=record.question_sha256, answer="accepted",
                                  cancel=job.cancel_event, pending_approvals=False, notes=[], facts=item.facts,
                                  limits_checked=True)
        self.jobs.update(item.job_id, state="running", note=f"Approved in the framework ({record.decided_by}).")
        state = "failed"
        try:
            # The quotas once more: minutes may have passed, and the client's unfinished builds may have grown.
            self.registry.enforce_limits(self.registry.prepare(item.tool, item.arguments, ctx), ctx, rate_too=False)
            result = self.registry.call(item.tool, item.arguments, ctx, approved=True)
        except ToolError as e:
            self.store.update(item.approval_id, state="failed")
            self.jobs.update(item.job_id, state="failed", error={"code": e.code, "message": e.message,
                                                                 "next": e.next, "notes": list(e.notes)})
        except Exception as e:  # never hand an unexpected exception's text to the client
            logger.error(f"Running approved call {item.approval_id} failed: {e!r}", exc_info=True)
            self.store.update(item.approval_id, state="failed")
            self.jobs.update(item.job_id, state="failed", error={"code": "failed",
                                                                 "message": "The approved call failed (details in "
                                                                            "the framework log)."})
        else:
            state = "done"
            self.store.update(item.approval_id, state="done")
            self.jobs.update(item.job_id, state="done", result=result.model_dump(mode="json"))
        finally:
            with self._lock:
                self._waiting.pop(item.approval_id, None)
        self._emit("approval_end", item.approval_id, item.tool, item.ctx, state, item.job_id)

    def _fail(self, item: _Waiting, code: str, message: str, error: Optional[BaseException] = None) -> None:
        with self._lock:
            self._waiting.pop(item.approval_id, None)
        self.store.update(item.approval_id, state="failed", note=message[:500])
        self.jobs.update(item.job_id, state="failed", error={"code": code, "message": message,
                                                             "notes": self._notes(item, error)})
        self._emit("approval_end", item.approval_id, item.tool, item.ctx, "failed", item.job_id)

    @staticmethod
    def _notes(item: _Waiting, error: Optional[BaseException] = None) -> List[str]:
        """The notes of the repairs of the call this approval holds (the fool-proof layer's, the look-up's, those a
        refusing look-up carried): a failed job names them as the call's result would."""
        import dataclasses

        from orchestrator.tools.registry import call_repairs, repair_notes, repairs_of
        try:
            ctx = dataclasses.replace(item.ctx, facts=item.facts) if item.facts is not None else item.ctx
            return repair_notes(call_repairs(ctx, repairs_of(error) if error is not None else ()))
        except Exception:  # noqa: BLE001 - a note is a help, never a reason to lose the refusal
            return []

    def waiting_ids(self) -> List[str]:
        with self._lock:
            return list(self._waiting)
