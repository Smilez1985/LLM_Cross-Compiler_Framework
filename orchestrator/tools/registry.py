#!/usr/bin/env python3
"""
One tool registry for Ditto and the MCP server.

A tool has typed input and output (pydantic), a risk tier and a handler. Every call goes through
ToolRegistry.call(): name resolution and argument normalization (the fool-proof layer, normalize.py), input
validation, the repeat guard, the user's stored permission policy, the tool's look-up of the facts only the server
knows (`prepare`, two-phase confirmation), an optional confirmation, the handler with an output check, and an
audit entry - the same path for every caller. The handler gets the looked-up facts in its CallContext and must use
exactly those (what the person approved is what runs). Every refusal and error carries a reason code and, where
there is one, the next step.
"""

import contextvars
import dataclasses
import hashlib
import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Type, Union

from pydantic import BaseModel, ConfigDict, ValidationError

from orchestrator.tools.audit import AuditLog
from orchestrator.tools.confirmation import MAX_FRAMEWORK, CallTooLong, describe_call
from orchestrator.tools.grants import NEVER_GRANT
from orchestrator.tools.limits import Limiter, wait_text
from orchestrator.tools.normalize import (PENDING_CODES, NormalizeError, Repair, check_names_distinct,
                                          list_item_literal, normalize_arguments, resolve_name)
from orchestrator.tools.output import (MAX_RESULT_BYTES, UNTRUSTED_CHARS, ResultTooLarge, check_size, clean_note,
                                       filtered_result, has_untrusted, notes_text)
from orchestrator.tools.mcp_tokens import ACTOR_PREFIX, TokenStore, TokenStoreError
from orchestrator.tools.policy import (TIER_CAPS, Decision, DecisionRule, PolicyStore, ToolPolicy, ToolTier,
                                       call_only_reads, restrictive_policy, stricter)
from orchestrator.tools.spec import (CHANNELS, DAY, LIMITS, CallerLimits, CallFacts, FieldMeta, GrantRule,
                                     ToolLimits, example_problems)
from orchestrator.utils.helpers import redact_secrets
from orchestrator.utils.logging import get_logger
from orchestrator.utils.untrusted import sanitize_untrusted, wrap_untrusted


def datetime_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def text_sha256(text: str) -> str:
    """The hash an audit entry records for a question or a result (never the text itself)."""
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


class ToolError(Exception):
    """A tool call failed; the message is safe to show to the user and to the model.

    `code` is a reason code (normalize.CODES: unknown_tool, invalid_value, permission_denied, declined, failed,
    ...), `next` the next step; the text a model sees is "<message> Next: <next>". `quoted` is the end of the
    message that quotes external text (a handler's error can carry a server reply, a log line or a name from a
    pack): it is cleaned like external text, and an MCP client gets it marked as untrusted data. `field` names the
    argument an input error is about (Ditto's loop tells a fix from a repeat by it). `notes` are the framework's notes
    about the call, set by the registry (with_repairs): the repairs the fool-proof layer made (a declared alias, a
    spelling variant of the tool name), the same notes a successful result carries, shown after the text
    ("<message> Next: <next> Notes: ...") on every path, so a refused call teaches the right argument name as a
    successful one does. `repairs` are the repairs a tool's look-up made before it refused (a file name matched in
    another letter case, normalize.Repair): the registry names them in the notes and records them.

    The facts model of the delegation design (4.1): `blocker` names the hard fact that refused the call before any
    question (a code of the facts catalogue, e.g. disk_full), binding the AI and the person alike; `person_action` says
    what the person can do about it (framework text, GUI place first), separate from `next`, which is for the AI;
    `details` carries framework facts only (never external text), e.g. the waiting result of a pending approval.

    `steps` (wave 6, chain a0): what to do, as catalogue steps (orchestrator/utils/next_steps) instead of a command in
    the text. The AI's next step is `next` followed by the steps rendered for the caller's channel (fill_steps, called
    by the registry with ToolContext.channel; until then rendered for an MCP app); `person_action` defaults to their
    GUI rendering; the GUI and the chat render them in the person's language themselves."""
    code = "failed"
    quoted = ""
    field = ""
    blocker = ""
    person_action = ""
    steps: tuple = ()
    _steps_filled = False

    def __init__(self, message: str = "", code: Optional[str] = None, next: str = "",
                 allowed: Optional[List[str]] = None, suggestions: Optional[List[str]] = None, quoted: str = "",
                 field: str = "", notes: Optional[List[str]] = None, blocker: str = "", person_action: str = "",
                 details: Optional[Dict[str, Any]] = None, steps: Any = None):
        super().__init__(message)
        if steps:
            from orchestrator.utils.next_steps import as_next_steps
            self.steps = as_next_steps(steps)
        self.message = message
        if code:
            self.code = code
        self.next = next
        self.allowed = list(allowed or [])
        self.suggestions = list(suggestions or [])
        self.quoted = quoted if quoted and message.endswith(quoted) else ""
        if field:
            self.field = field
        self.notes: List[str] = list(notes or [])
        self.repairs: List[Any] = []
        if blocker:
            self.blocker = blocker
        if person_action:
            self.person_action = person_action
        self.details: Optional[Dict[str, Any]] = dict(details) if details is not None else None

    def next_text(self, channel: Optional[str] = None) -> str:
        """The AI's next step: `next` and the steps rendered for `channel` (once fill_steps ran: `next` as filled)."""
        if not self.steps or self._steps_filled:
            return self.next
        from orchestrator.utils.next_steps import as_next_steps, catalogue, render
        known = catalogue().steps
        self.steps = as_next_steps(self.steps)   # a site may have set plain step ids
        # A step whose tool `next` names already adds nothing for the AI (its steps still reach the person).
        new = [s for s in self.steps if not (s.id in known and known[s.id].tool and known[s.id].tool in self.next)]
        rendered = render(new, "ai", "en", channel) if new else ""
        return f"{self.next} {rendered}".strip() if self.next else rendered

    def fill_steps(self, channel: Optional[str] = None) -> "ToolError":
        """Renders the steps into `next` for the caller's channel (and into `person_action` when it is empty), once;
        the registry calls it at the boundary with ToolContext.channel. Returns the error."""
        if self.steps and not self._steps_filled:
            from orchestrator.utils.next_steps import render
            self.next = self.next_text(channel)
            if not self.person_action:
                self.person_action = render(self.steps, "gui", "en").replace("\n", " ")
            self._steps_filled = True
        return self

    def text(self) -> str:
        """The message and the next step, without the notes (the MCP server adds them to every refusal itself)."""
        following = self.next_text()
        return f"{self.message} Next: {following}" if following else self.message

    def __str__(self) -> str:
        return self.text() + notes_text(self.notes)

    def with_text(self, message: str, next: Optional[str] = None) -> "ToolError":
        """The same kind of error with the same code and notes and another text."""
        try:
            copy = type(self)(message)
        except Exception:  # a subclass with its own constructor
            copy = ToolError(message)
        copy.code, copy.next = self.code, self.next if next is None else next
        copy.allowed, copy.suggestions = list(self.allowed), list(self.suggestions)
        copy.field = self.field
        copy.quoted = self.quoted if self.quoted and message.endswith(self.quoted) else ""
        copy.notes = list(self.notes)
        copy.repairs = list(getattr(self, "repairs", []))
        copy.blocker, copy.person_action = self.blocker, self.person_action
        copy.steps, copy._steps_filled = tuple(getattr(self, "steps", ()) or ()), getattr(self, "_steps_filled", False)
        details = getattr(self, "details", None)
        copy.details = dict(details) if details is not None else None
        return copy


def _steps_of(error: BaseException) -> tuple:
    """The next steps a core exception carries (next_steps.WithSteps), for the ToolError made of it."""
    from orchestrator.utils.next_steps import steps_of
    return steps_of(error)


def repair_notes(repairs: List[Repair]) -> List[str]:
    """The notes of the repairs a call got (the same words as in a successful result's `notes`)."""
    return [clean_note(r.note()) for r in repairs]


def with_repairs(error: ToolError, repairs: List[Repair]) -> ToolError:
    """The error with the notes of the call's repairs, set by the registry only: whatever a handler's error carried
    there is replaced (its own words stay in the message, cleaned like external text). Returns the error."""
    error.notes = repair_notes(repairs)
    return error


def external_error_text(text: str) -> str:
    """The text of a handler's error, which can quote external text: invisible characters are removed first (a
    token split by one would otherwise not be redacted), then secrets; capped like other external text."""
    return redact_secrets(sanitize_untrusted(text, max_len=UNTRUSTED_CHARS))


class ToolDenied(ToolError):
    """The user's policy (or their answer to a confirmation) does not allow this call."""
    code = "permission_denied"


class ToolInputError(ToolError):
    """The arguments do not match the tool's input schema."""
    code = "invalid_value"


class UnknownTool(ToolInputError):
    """No registered tool has this name (or a spelling variant of it)."""
    code = "unknown_tool"


class AuditUnavailable(ToolError):
    """A call that changes something was refused because its audit entry could not be written."""


class LimitHit(ToolError):
    """A refusal of the limits table (a rate, a quota, the questions to the person: a recent "no", a paused tool,
    too many questions). The call was never tried, so it is never a failed call for the repeat guard, on either
    path."""
    code = "limit_reached"


# The person's own setting: the next step names where they decide, not a command for the AI to suggest.
DENIED_HINT = ("Tell the person it is not allowed by their tool permissions; only they can change that, in Tool "
               "Permissions. get_status shows why.")
# Nobody could be asked: the person can allow the tool, or use a client that can ask. Where they allow it is the
# step permissions.edit (UNASKED_STEPS), rendered for the caller's channel (wave 6: no command for a GUI person).
UNASKED_HINT = "The person can allow it in their tool permissions, or use a client that can ask them."
UNASKED_STEPS = ("permissions.edit",)
REPEAT_WARN = "This exact call already failed. "
# The next step of a question too long for the channel that would show it (an MCP form or the framework's windows).
TOO_LONG_NEXT = "Make the call smaller (fewer or shorter values), or tell the person why it cannot be confirmed."
# The next step of a failure nobody can fix by changing the call (the framework's or a service's own problem).
# The person checks the installation with Mojo (FAILED_STEPS: the Mojo window, or `llmcf mojo` for the CLI).
FAILED_NEXT = ("Tell the person what failed and why; run_mojo_oracle and the framework log show the cause. Do not "
               "retry the same call unchanged.")
FAILED_STEPS = ("mojo.oracle",)
# A pending approval: the person answers in the framework (its notice, or the list of pending approvals).
APPROVAL_STEPS = ("approvals.list",)
GUARD_SIZE = 512  # failing calls remembered per registry for the repeat guard
EXAMPLE_AFTER = 3  # input errors in a session before every input error shows an example call


@dataclass(frozen=True)
class ToolSpec:
    name: str
    title: str
    description: str
    tier: ToolTier
    input_model: Type[BaseModel]
    output_model: Type[BaseModel]
    handler: Callable[[BaseModel], BaseModel]
    idempotent: bool = False
    open_world: bool = False  # talks to services outside this computer
    # Framework notes for a result (e.g. "nothing found, the source is reachable"); built from framework facts only.
    notes: Optional[Callable[[BaseModel], List[str]]] = None
    # The one-sentence summary of a result for the MCP text block; built from framework facts only (output.fact).
    summary: Optional[Callable[[BaseModel], str]] = None
    paging_hint: str = ""  # how to ask for less when a result is too large
    destructive: bool = False  # deletes or overwrites something (destructiveHint), whatever the tier
    # The tool's own default decision, used instead of the caller's tier decision; a Decision or a function of
    # (input, who): `who` is the caller's owner identity (ToolContext.owner, e.g. "allow" when the caller cancels
    # its own build), or the caller's name when there is no call. None: the tier decision. A default never opens a
    # tier the person denied; a per-tool setting of the person still wins, and a damaged policy still denies.
    default_decision: DecisionRule = None
    # The highest decision any setting may give this tool; a Decision or a function of (input, who).
    # None: the tier's cap (TIER_CAPS: tools that reach other devices or delete data always ask).
    max_decision: DecisionRule = None
    channels: Tuple[str, ...] = CHANNELS  # confirmation channels the tool accepts, strongest first
    grant: Optional[GrantRule] = None     # None: a confirmation never offers "do not ask again"
    limits: ToolLimits = field(default_factory=ToolLimits)
    arg_aliases: Mapping[str, str] = field(default_factory=dict)  # declared argument aliases (alias -> field)
    examples: Tuple[Dict[str, Any], ...] = ()                     # valid example calls, shown after input errors
    fields: Mapping[str, FieldMeta] = field(default_factory=dict)  # how the fool-proof layer treats each field
    # The handler is called as handler(input, ctx) with the caller's ToolContext (owner of builds and jobs).
    takes_context: bool = False
    # An async wait the MCP server runs on its event loop before the call, holding no worker thread (long-poll):
    # long_poll(input, seconds, report_progress). The handler then sees ctx.metadata["waited"].
    long_poll: Optional[Callable[..., Awaitable[Any]]] = None
    # Two-phase confirmation: prepare(input, ctx) looks up the facts only the server knows (a pending proposal, a
    # commit, a hash) before the question and returns CallFacts; the question shows them, their `cap` can make an
    # allowed call ask, and the handler (takes_context) gets them as ctx.facts and must use exactly those. A
    # prepare that fails refuses the call before anything is asked (with the error's reason code).
    prepare: Optional[Callable[[BaseModel, "ToolContext"], CallFacts]] = None
    # The question's first line in plain words, for a tool without a prepare (a prepare gives its own).
    consequence: str = ""
    # discard(input, ctx): drops what prepare staged (a quarantined pack) when the call is not confirmed (a "no", nobody
    # could be asked, a pending approval declined or expired). It must never raise; it runs no handler code.
    discard: Optional[Callable[[BaseModel, "ToolContext"], None]] = None
    # Who may use the tool at all, checked against the real actor of a call (ToolContext.actor, never a token's link):
    # None = every caller; otherwise names such as "ditto", "mcp", "mcp:<token>", or "mcp:*" for every named token. A
    # caller outside the list is not offered the tool, and a call by name is refused (not_supported) and audited.
    callers: Optional[Tuple[str, ...]] = None
    # False: never listed (MCP tools/list, Ditto's tool definitions); callable by name only (internal tools).
    model_facing: bool = True
    # visible_when(ctx) -> bool: whether this session is offered the tool now (e.g. only while pending approvals can
    # occur); None = always. Checked again when it is called; a hidden tool called by name is refused and audited.
    visible_when: Optional[Callable[["ToolContext"], bool]] = None
    # The tool deletes something the person can see (round 5 f): only the person decides, in the framework's own
    # window; never a client's form, a standing approval, a delegation or "allow" (ToolRegistry.register checks it).
    deletes: bool = False
    # undo(UndoContext) -> UndoPlan | CannotUndo: how the chat panel undoes a call (filled when the registry is built).
    undo: Optional[Callable[[Any], Any]] = None
    # A tool with reading and changing actions (the GUI tool): reads(input) -> True for a call that only reads. Such a
    # call is decided by the caller's read tier, counts against the read rate and needs no write-ahead audit entry;
    # the tool's tier stays the one of its changing actions (lists, annotations, caps, a new token's permissions).
    reads: Optional[Callable[[BaseModel], bool]] = None

    def annotations(self) -> Dict[str, Any]:
        """MCP tool annotations (hints for clients; the registry enforces the policy itself)."""
        return {
            "title": self.title,
            "readOnlyHint": self.tier == ToolTier.READ,
            "destructiveHint": self.destructive,
            "idempotentHint": self.idempotent,
            "openWorldHint": self.open_world,
        }

    def meta(self) -> Optional[Dict[str, Any]]:
        """MCP `_meta` of the tool: a tool that reaches another device or deletes data asks the client to show
        its own prompt on every call too (Claude Code honours this)."""
        if self.tier == ToolTier.EXTERNAL:
            return {"anthropic/requiresUserInteraction": True}
        return None

    def cap(self, inp: Any = None, caller: str = "") -> Decision:
        rule = self.max_decision(inp, caller) if callable(self.max_decision) else self.max_decision
        return rule or TIER_CAPS.get(self.tier, Decision.ASK)


@dataclass(frozen=True)
class ResourceSpec:
    """An MCP resource or resource template read through the registry like a read tool (design 4.4): the caller's
    permission decides, the result is filtered, the read is audited as the pseudo-tool `read_resource:<uri>`.

    `tool` names the read tool the resource mirrors: that tool's permission decides (a resource never shows what the
    tool may not), its input model checks the parameters (`arguments` maps them to the tool's arguments) and its
    output model filters the result. Without a tool, `read(params)` returns framework text (capped, secrets
    redacted) and the caller's read permission decides. `listed()` gives the parameters of the concrete resources
    to list (e.g. the documents that exist)."""
    uri: str
    name: str
    title: str
    description: str
    mime_type: str
    tool: str = ""
    arguments: Callable[[Dict[str, str]], Dict[str, Any]] = field(default=lambda params: dict(params))
    read: Optional[Callable[[Dict[str, str]], str]] = None
    listed: Optional[Callable[[], List[Dict[str, str]]]] = None
    # Completion of template parameters (MCP completion/complete): parameter -> f(arguments so far, ctx) -> values.
    completions: Mapping[str, Callable[[Dict[str, str], "ToolContext"], List[str]]] = field(default_factory=dict)

    @property
    def audit_name(self) -> str:
        return f"read_resource:{self.uri}"


@dataclass(frozen=True)
class PromptArg:
    """One argument of a prompt: a string. Free text is only ever the last argument (Claude Code splits prompt
    arguments on whitespace)."""
    name: str
    description: str
    required: bool = False
    max_chars: int = 200


@dataclass(frozen=True)
class PromptSpec:
    """An MCP prompt read through the registry like a read tool (design 4.3, 4.4). `instructions` is a constant the
    framework wrote (no argument value, no external text); `render(arguments, ctx)` builds the context message, with
    every value from outside the framework inside a wrap_untrusted block. `tools` are the tools the prompt leads to:
    it is offered only when all of them are registered, so a prompt never names a tool that does not exist. `reads`
    are the read tools (among `tools`) whose data the context holds, such as a build's log: like a resource, a prompt
    shows nothing those tools may not, so it is hidden while the caller's permissions deny one of them and refused
    while one is set to "ask"."""
    name: str
    title: str
    description: str
    instructions: str
    arguments: Tuple[PromptArg, ...]
    render: Callable[[Dict[str, str], "ToolContext"], str]
    tools: Tuple[str, ...] = ()
    completions: Mapping[str, Callable[[Dict[str, str], "ToolContext"], List[str]]] = field(default_factory=dict)
    reads: Tuple[str, ...] = ()

    @property
    def audit_name(self) -> str:
        return f"get_prompt:{self.name}"


MAX_PROMPT_CHARS = 24000  # a whole prompt (instructions and context)
MAX_COMPLETIONS = 100     # values one completion returns (the MCP limit)


@dataclass(frozen=True)
class _Surface:
    """A resource or prompt as the policy and the audit log see it: a read-tier pseudo-tool."""
    name: str
    title: str
    tier: ToolTier = ToolTier.READ
    default_decision: DecisionRule = None
    max_decision: DecisionRule = None


class ResourceUnknown(ToolInputError):
    """No such resource, an unknown id or invalid parameters (MCP: invalid params, -32602)."""
    code = "unknown_id"


Confirm = Callable[[ToolSpec, BaseModel], bool]

# The registry and actor of the call a handler runs for (handlers do not get the context; get_status reads it).
_CALL: contextvars.ContextVar = contextvars.ContextVar("lcf_tool_call", default=(None, ""))
# The question the registry asks through a framework channel (Ditto's dialog or prompt shows exactly this text).
_QUESTION: contextvars.ContextVar = contextvars.ContextVar("lcf_tool_question", default="")
# What that question may offer as "yes, and do not ask again" (grants.GrantOffer), None when nothing.
_OFFER: contextvars.ContextVar = contextvars.ContextVar("lcf_tool_grant_offer", default=None)


def current_question() -> str:
    return _QUESTION.get()


def current_grant_offer():
    """The standing approval the framework's own dialog or prompt may offer for the question it shows (Ditto)."""
    return _OFFER.get()


def current_actor() -> str:
    return _CALL.get()[1]


def current_registry() -> Optional["ToolRegistry"]:
    return _CALL.get()[0]


@dataclass
class ToolContext:
    actor: str                       # "ditto", "mcp", "mcp:<token name>", "cli", "gui"
    confirm: Optional[Confirm] = None  # asks the user; None = no way to ask, so "ask" means deny
    metadata: Dict[str, Any] = field(default_factory=dict)
    client: str = ""                 # the client's self-reported name and version: a label, never trusted
    session: str = ""                # a hash of the session or connection, for the audit log
    # Set when the caller gave up on the call (an MCP client cancelled it) while the handler runs.
    cancel: threading.Event = field(default_factory=threading.Event)
    # Filled by a caller that asked the person itself (MCP elicitation): how, with which question, which answer.
    confirm_channel: str = ""
    question_sha256: str = ""
    answer: str = ""
    request_id: str = ""             # the JSON-RPC request id (MCP), recorded with the session hash
    repairs: List[Repair] = field(default_factory=list)  # what the fool-proof layer changed before this call
    # The server connection that owns the builds and jobs this caller starts (MCP: one stdio process, or the whole
    # HTTP server, so per token); set by the MCP server, never by the client. Ditto has none: its builds stay its
    # own across questions. `session` above is something else: it groups one client's calls for the repeat guard
    # and the audit log.
    connection: str = ""
    # Notes a handler that takes the context adds for this call's result (framework text only, e.g. "the wait was
    # shortened"); the registry puts them into the result's `notes` after the repairs. New for every call.
    notes: List[str] = field(default_factory=list)
    # Set by the MCP server when nobody can be asked through the client (no form elicitation, a question too long for
    # one, or a tool the person wants confirmed in the framework): "ask" then becomes a pending approval in the
    # framework (a notice in the GUI, the list of pending approvals) instead of a refusal.
    pending_approvals: bool = False
    # The facts the tool's prepare looked up for this call (the MCP server hands in the ones its question showed);
    # the handler must use exactly these. New for every call.
    facts: Optional[CallFacts] = None
    # Progress for the caller while the handler runs: report_progress(done, total, message); None: nobody listens.
    report_progress: Optional[Callable[..., Any]] = None
    # The MCP server counted this call against the limits already (before its question); call() does not again.
    limits_checked: bool = False
    # Set by the registry before the handler runs: the call's write-ahead `started` audit entry and its decision, so
    # work that goes on after the call returned (a job) can write its own end entry linked to it (record_job_end).
    audit_started: Optional[int] = None
    audit_decision: str = ""
    # True for Ditto while it has a live delegation: a call the delegation covers becomes a pending approval Ditto's
    # model answers, while every other question keeps the person's dialog (design 3.2). Set by Ditto, never by a client.
    ai_pending: bool = False
    # Where the person who asked sits (wave 6, design A.6): "gui-chat" (Ditto, asked in this window's chat), "gui"
    # (Ditto working for the GUI without a chat turn), "cli" (`llmcf ditto ask`), None (an MCP app). The next steps of
    # a refusal are rendered for it (ToolError.fill_steps): in the GUI's channels an AI is never told a command.
    channel: Optional[str] = None

    @property
    def owner(self) -> str:
        """Who owns the builds and jobs this caller starts: the actor and, for MCP, its connection."""
        return f"{self.actor}#{self.connection}" if self.connection else self.actor


# What a handler that takes the context gets: the caller, its client label and session, the cancel event, progress
# and the facts its question showed (design 2.0, `CallContext`). One per call.
CallContext = ToolContext

# Who a caller is, in words a person reads (questions, the audit view).
CALLER_WORDS = {"ditto": "Ditto", "mcp": "all local stdio clients", "cli": "you, in the CLI", "gui": "you, in the GUI"}
GRANT_REQUEST_NOTE = ("The answer asked the framework to stop asking for this. Only the person can give a standing "
                      "approval, in the framework's own approval window; this answer approved this one call.")


def caller_label(actor: str) -> str:
    """A caller as a person reads it: Ditto, all local stdio clients, the MCP token 'laptop', you in the CLI."""
    if actor.startswith(ACTOR_PREFIX):
        return f"the MCP token '{actor[len(ACTOR_PREFIX):]}'"
    return CALLER_WORDS.get(actor, actor or "an unnamed caller")


def requester_of(ctx: "ToolContext") -> str:
    """Who asks, for the question: Ditto, or the MCP client as it reports itself, with the transport and whose
    permissions apply ("Claude Code 2.1.295 (self-reported) over stdio, with the permissions of all local stdio
    clients"). The client's own words were escaped and shortened when they arrived (mcp_server._client_text)."""
    if ctx.actor == "ditto":
        return "Ditto, the framework's assistant in this program"
    label = ctx.metadata.get("client") if isinstance(ctx.metadata, dict) else None
    describe = getattr(label, "describe", None)
    who = describe() if callable(describe) else (f"{ctx.client} (self-reported)" if ctx.client else
                                                 "an AI client that did not name itself")
    return f"{who}, with the permissions of {caller_label(ctx.actor)}"


ANY_TOKEN = ACTOR_PREFIX + "*"  # in ToolSpec.callers: every named MCP token


def caller_allowed(callers: Optional[Sequence[str]], actor: str) -> bool:
    """Whether `actor` (the real actor of a call, never a token's link) is among a tool's callers."""
    if callers is None:
        return True
    return actor in callers or (ANY_TOKEN in callers and actor.startswith(ACTOR_PREFIX) and actor != ANY_TOKEN)


def deletes(spec: Any, facts: Optional[CallFacts] = None) -> bool:
    """Whether a call deletes something the person can see (round 5 f): a tool marked `deletes`, or a call whose
    looked-up facts carry a deletion plan (for example a healing strategy that deletes)."""
    return bool(getattr(spec, "deletes", False)) or getattr(facts, "deletion", None) is not None


def deletion_rule_problems(spec: "ToolSpec") -> List[str]:
    """Why a tool marked `deletes` breaks the deletion rule (round 5 f): it must be the person's alone, confirmed only
    in the framework's own window, never with a standing approval, never under a delegation, and every call asks."""
    from orchestrator.tools.delegation import DELEGATION_SCOPE, NEVER_DELEGATE
    problems = []
    if tuple(spec.channels) != ("framework",):
        problems.append("its questions are confirmed only in the framework's own window (channels must be exactly "
                        "('framework',))")
    if spec.name not in NEVER_GRANT or spec.grant is not None:
        problems.append("it never gets a standing approval (NEVER_GRANT, no GrantRule)")
    if spec.name not in NEVER_DELEGATE:
        problems.append("it is never delegated (NEVER_DELEGATE)")
    scope = DELEGATION_SCOPE.get(spec.name)
    if scope is not None and scope.who != "person":
        problems.append(f"its delegation scope must be 'person', not '{scope.who}'")
    if spec.tier == ToolTier.READ:
        problems.append("a tool that deletes is never a read tool")
    if callable(spec.max_decision) or spec.cap() != Decision.ASK:
        problems.append("every call asks the person (max_decision must be ask)")
    return problems


# What the registry tells its observers (design 5.4). An event carries framework facts only: names, ids and reason
# codes, never arguments, results or external text. call_done (wave 6, design B.1): a call by any caller that changed
# something (or a reported read, tool_windows.REPORTED_READ_TOOLS) ended ok; Core/activity.py writes the chat row.
# Its one outside value is the app's own name (`client`, cleaned when it arrived), which a row shows only as a claim.
EVENT_KINDS = ("refused_before_question", "grant_used", "approval", "approval_end", "call_done")
_EVENT_VALUE = (str, int, float, bool, type(None))
# What a call_done names of a call (`_done_facts`): ids the framework issued, the window or form the gui tool worked
# on, the setting keys an update_settings changed (never their values), the closed reason and action of a
# report_cannot_do, each at most this long; the producer checks every value against its closed pattern again.
_DONE_TEXT = 200


def _done_facts(spec: "ToolSpec", inp: Any, result: Any) -> Dict[str, Any]:
    """The framework facts of a finished call for its call_done event: request_id and job_id from the result, the gui
    tool's window and form, update_settings' changed keys, report_cannot_do's closed reason and action. Strings only;
    a value of any other shape is left out."""
    facts: Dict[str, Any] = {}

    def text(value: Any) -> str:
        return value[:_DONE_TEXT] if isinstance(value, str) else ""

    for name in ("request_id", "job_id", "window"):
        facts[name] = text(getattr(result, name, ""))
    facts["form"] = text(getattr(result, "form_id", ""))
    applied = getattr(result, "applied", None)   # update_settings: the keys it changed (the values stay out)
    keys = [text(getattr(a, "key", "")) for a in applied] if isinstance(applied, list) else []
    facts["keys"] = ",".join(k for k in keys if k)[:_DONE_TEXT]
    if spec.name in _reported_reads():
        facts["reason"] = text(getattr(inp, "reason", ""))
        facts["action"] = text(getattr(inp, "action", None) or "")
    return {k: v for k, v in facts.items() if v}


def _reported_reads() -> frozenset:
    from orchestrator.tools.tool_windows import REPORTED_READ_TOOLS
    return REPORTED_READ_TOOLS


class RegistryEvents:
    """The registry's observer interface (no-op until something subscribes): the producers of the chat feed subscribe
    here instead of editing the registry. A subscriber that raises is logged and skipped; emitting never changes or
    stops a call."""

    def __init__(self):
        self._subscribers: List[Callable[[str, Dict[str, Any]], None]] = []
        self._lock = threading.Lock()
        self.logger = get_logger("ToolRegistry")

    def subscribe(self, callback: Callable[[str, Dict[str, Any]], None]) -> Callable[[], None]:
        """Calls `callback(kind, data)` for every event from now on; returns the function that unsubscribes."""
        with self._lock:
            self._subscribers.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)
        return unsubscribe

    def emit(self, kind: str, **data: Any) -> None:
        if kind not in EVENT_KINDS:
            raise ValueError(f"unknown registry event {kind!r}")
        bad = sorted(k for k, v in data.items() if not isinstance(v, _EVENT_VALUE))
        if bad:
            raise ValueError(f"registry event {kind}: {bad} must be plain values (framework facts only)")
        with self._lock:
            subscribers = list(self._subscribers)
        for callback in subscribers:
            try:
                callback(kind, dict(data))
            except Exception as e:  # noqa: BLE001 - an observer never fails a call
                self.logger.warning(f"A subscriber of '{kind}' failed: {redact_secrets(str(e))}")


@dataclass
class Prepared:
    """A call after name resolution, normalization and validation. `key` is the validated input with every
    default filled in: the repeat guard's key for the call, the same whichever path (Ditto's raw arguments, an
    MCP client's raw arguments, or the SDK's parsed ones) it came on."""
    spec: "ToolSpec"
    arguments: Dict[str, Any]
    input: BaseModel
    repairs: List[Repair]
    key: Dict[str, Any] = field(default_factory=dict)


def closed_input_model(model: Type[BaseModel]) -> Type[BaseModel]:
    """The input model, forbidding fields it does not declare (maintainer decision: every tool input model forbids
    extra fields, so nothing a caller sends is ever dropped silently)."""
    if model.model_config.get("extra") == "forbid":
        return model
    return type(model.__name__, (model,), {"__module__": model.__module__, "__qualname__": model.__qualname__,
                                           "__doc__": model.__doc__, "model_config": ConfigDict(extra="forbid")})


def _merge(*groups: List[Repair]) -> List[Repair]:
    merged: List[Repair] = []
    for group in groups:
        for repair in group:
            if repair not in merged:
                merged.append(repair)
    return merged


def repairs_of(error: BaseException) -> List[Repair]:
    """The repairs a refusal carries (a look-up's ToolError, a NormalizeError): only real Repair objects."""
    return [r for r in (getattr(error, "repairs", None) or ()) if isinstance(r, Repair)]


def call_repairs(ctx: "ToolContext", extra: Sequence[Repair] = ()) -> List[Repair]:
    """Every repair of a call: the fool-proof layer's (ctx.repairs), then the tool's look-up's (ctx.facts.repairs, a
    file name matched in another letter case), then `extra` (those a refusing look-up carried). The notes of a result
    and of every refusal and the audit entries name the same ones."""
    looked = [r for r in (getattr(getattr(ctx, "facts", None), "repairs", None) or ()) if isinstance(r, Repair)]
    return _merge(list(ctx.repairs or []), looked, [r for r in extra if isinstance(r, Repair)])


def _args_key(arguments: Any) -> str:
    try:
        text = json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = repr(arguments)
    return text_sha256(text)


arguments_key = _args_key  # the hash that identifies a call's arguments (repeat guard, question rounds)


def repair_marks(repairs: List[Repair]) -> Tuple[Dict[str, str], str]:
    """(field -> what the caller wrote, the tool name as written) for the question."""
    marks, renamed = {}, ""
    for r in repairs:
        if r.kind == "name":
            renamed = str(r.wrote)
        elif r.kind in ("alias", "key"):
            marks.setdefault(r.field, f"as '{r.wrote}'")
        elif r.field:
            marks[r.field] = json.dumps(r.wrote, ensure_ascii=False, default=str) if not isinstance(r.wrote, str) \
                else r.wrote
    return marks, renamed


def _minutes(seconds: float) -> str:
    minutes = max(1, int(round(seconds / 60)))
    return f"{minutes} minute{'s' if minutes != 1 else ''}"


class ToolRegistry:
    def __init__(self, policy_store: PolicyStore, audit: Optional[AuditLog] = None,
                 tokens: Optional[TokenStore] = None, limits: CallerLimits = LIMITS,
                 clock: Optional[Callable[[], float]] = None):
        self.logger = get_logger("ToolRegistry")
        self.policy_store = policy_store
        self.audit = audit
        self.tokens = tokens  # named MCP tokens, each with its own policy
        self.approvals = None  # ApprovalService: pending approvals for callers that cannot ask (attach_approvals)
        # The registry-wide rows of the limits table and the per-client counters (rates, quotas, questions, "no").
        self.limits = limits
        self.limiter = Limiter(clock or time.monotonic)
        self._tools: Dict[str, ToolSpec] = {}
        self._resources: Dict[str, ResourceSpec] = {}  # by URI (template)
        self._prompts: Dict[str, PromptSpec] = {}
        # Repeat guard and per-session counters (in memory; per caller and session).
        self._guard_lock = threading.Lock()
        self._failures: "OrderedDict[tuple, Dict[str, Any]]" = OrderedDict()
        self._sessions: "OrderedDict[tuple, Dict[str, int]]" = OrderedDict()
        self.events = RegistryEvents()  # observers (the chat feed's producers); nothing listens by default

    def register(self, spec: ToolSpec) -> ToolSpec:
        if spec.grant is not None and spec.name in NEVER_GRANT:
            raise ValueError(f"Tool '{spec.name}' never gets a standing approval (design 6.10).")
        if spec.deletes:
            problems = deletion_rule_problems(spec)
            if problems:
                raise ValueError(f"Tool '{spec.name}' deletes something, so the person alone decides it: "
                                 + "; ".join(problems) + ".")
        if spec.callers is not None and (not spec.callers or not all(isinstance(c, str) and c for c in spec.callers)):
            raise ValueError(f"Tool '{spec.name}': callers must name at least one caller (None: every caller).")
        if spec.name in self._tools:
            raise ValueError(f"Tool '{spec.name}' is already registered.")
        if spec.tier == ToolTier.FORBIDDEN:
            raise ValueError(f"Tool '{spec.name}' is in the forbidden tier and cannot be registered.")
        spec = dataclasses.replace(spec, input_model=closed_input_model(spec.input_model))
        names = set(spec.input_model.model_fields)
        unknown = sorted((set(spec.fields) - names) | {f for f in spec.arg_aliases.values() if f not in names})
        if unknown:
            raise ValueError(f"Tool '{spec.name}' declares metadata or aliases for unknown fields {unknown}.")
        clash = sorted(a for a in spec.arg_aliases if a in names)
        if clash:
            raise ValueError(f"Tool '{spec.name}': aliases {clash} are names of its own fields.")
        bad = [c for c in spec.channels if c not in CHANNELS]
        if bad or not spec.channels:
            raise ValueError(f"Tool '{spec.name}': unknown confirmation channels {bad}.")
        problems = example_problems(list(spec.examples), spec.input_model.model_validate)
        if problems:
            raise ValueError(f"Tool '{spec.name}' has invalid example calls: {problems}")
        clashes = check_names_distinct(list(self._tools) + [spec.name])
        if clashes:
            raise ValueError(f"Tool '{spec.name}' cannot be told apart from {clashes[0][0]} by a spelling variant.")
        self._tools[spec.name] = spec
        return spec

    def attach_approvals(self, service) -> None:
        """Pending approvals (design 6.9) for callers whose client cannot ask the person."""
        service.registry = self
        self.approvals = service

    def framework_channel_required(self, spec: ToolSpec, caller: str, facts: Optional[CallFacts] = None) -> bool:
        """Whether a question about this tool must be put to the person in the framework (approval window), not in
        the client's dialog: the tool accepts no other channel, the call deletes something (round 5 f; `facts`: what
        the tool looked up for this call), or the person set it so for this caller."""
        if "elicitation" not in spec.channels or deletes(spec, facts):
            return True
        policy, section = self._policy_of(caller)
        return spec.name in policy.framework_only_for(section)

    # --- standing approvals (design 6.10) ------------------------------------------------------------------------

    def _grant_caller(self, actor: str) -> Tuple[str, Optional[str]]:
        """(the caller whose standing approvals apply to `actor`, the creation time of its named token). A token linked
        to Ditto uses Ditto's; an unknown or revoked token has none ("")."""
        if not actor.startswith(ACTOR_PREFIX):
            return actor, None
        if self.tokens is None:
            return "", None
        try:
            record = self.tokens.get(actor[len(ACTOR_PREFIX):])
        except TokenStoreError:
            return "", None
        if record is None:
            return "", None
        if record.link is not None:
            return record.link, None
        return actor, record.uid or record.created

    def _grant_facts(self, spec: ToolSpec, inp: Any, ctx: "ToolContext") -> Optional[Dict[str, Any]]:
        if spec.grant is None or spec.grant.facts is None or spec.name in NEVER_GRANT:
            return None
        try:
            facts = spec.grant.facts(inp, ctx)
        except Exception as e:  # no facts: no standing approval (the tool asks)
            self.logger.info(f"No facts for a standing approval of {spec.name}: {redact_secrets(str(e))}")
            return None
        return facts if isinstance(facts, dict) else None

    def grant_offer(self, spec: ToolSpec, inp: Any, ctx: "ToolContext"):
        """What a question about this call may offer as "yes, and do not ask again" (grants.GrantOffer), or None: no
        GrantRule, a tool that never gets one, a tool the person wants confirmed in the framework, or facts outside
        every scope. The shared stdio caller gets only the narrowest scope and the shorter durations."""
        from orchestrator.tools.grants import GrantOffer, durations_for, warning_text
        caller, token_created = self._grant_caller(ctx.actor)
        if not caller or self.framework_channel_required(spec, ctx.actor, getattr(ctx, "facts", None)):
            return None
        facts = self._grant_facts(spec, inp, ctx)
        if facts is None:
            return None
        rule, scopes, summaries = spec.grant.rule, {}, {}
        widths = [w for w in rule.widths if w == "this" or ctx.actor != "mcp"]  # narrowest scope for stdio clients
        for width in widths:
            try:
                scope = rule.scope(facts, width)
            except Exception:
                scope = None
            if scope is not None:
                scopes[width], summaries[width] = scope, rule.summary(scope)
        if not scopes:
            return None
        linked = []
        if caller == "ditto" and self.tokens is not None:
            try:
                linked = [t.name for t in self.tokens.list() if t.link == "ditto"]
            except TokenStoreError:
                linked = []
        return GrantOffer(tool=spec.name, caller=caller, rule_version=spec.grant.version, scopes=scopes,
                          summaries=summaries, durations=durations_for(caller),
                          warning=warning_text(spec.grant.warning, caller, ctx.client, linked,
                                               external=spec.tier == ToolTier.EXTERNAL),
                          token_created=token_created)

    def matching_grant(self, spec: ToolSpec, inp: Any, ctx: "ToolContext"):
        """The standing approval that covers this call, or None. Read anew for every call (a revocation stops calls
        that were accepted but have not started). An expired, lapsed or outdated one never covers a call; a lapse is
        recorded once in the audit log."""
        if spec.grant is None or spec.name in NEVER_GRANT:
            return None
        caller, token_created = self._grant_caller(ctx.actor)
        if not caller or self.framework_channel_required(spec, ctx.actor, getattr(ctx, "facts", None)):
            return None
        policy = self.policy_store.load()
        if self.policy_store.problem:
            return None
        candidates = [g for g in policy.grants.get(caller, []) if g.tool == spec.name]
        if not candidates:
            return None
        facts = self._grant_facts(spec, inp, ctx)
        if facts is None:
            return None
        for grant in candidates:
            if grant.rule_version != spec.grant.version or grant.expired() or grant.token_created != token_created:
                continue
            _, last = self.audit.grant_uses(grant.id) if self.audit is not None else (0, None)
            if grant.lapsed(last):
                self._record_lapse(spec, ctx, grant)
                continue
            try:
                if spec.grant.rule.covers(grant.scope, facts):
                    return grant
            except Exception:
                continue
        return None

    def _record_lapse(self, spec: ToolSpec, ctx: "ToolContext", grant) -> None:
        if self.audit is None:
            return
        try:
            for entry in self.audit.entries(500):
                if entry.get("outcome") == "grant_lapsed" and entry.get("confirm_channel") == f"grant:{grant.id}":
                    return
            self.audit.record(ctx.actor, spec.name, spec.tier.value, Decision.ASK.value, "grant_lapsed",
                              detail="Unused for 90 days; the tool asks again.",
                              extra={"confirm_channel": f"grant:{grant.id}"})
        except Exception as e:
            self.logger.warning(f"Could not record the lapse of {grant.id}: {redact_secrets(str(e))}")

    def check_grant(self, offer, width: str, duration: str, created_via: str) -> Tuple[str, Optional[str]]:
        """(the caller, its token's creation mark) when a person may turn this offer into a standing approval now;
        ValueError otherwise: a choice outside the offer, a tool that never gets one or no longer has the rule the
        offer was computed with, or a caller whose token was revoked (or revoked and made anew) since it asked."""
        from orchestrator.tools.grants import CREATED_VIA
        if width not in offer.scopes or duration not in offer.durations or created_via not in CREATED_VIA:
            raise ValueError("That choice is not offered for this call.")
        spec = self._tools.get(offer.tool)
        if (offer.tool in NEVER_GRANT or spec is None or spec.grant is None
                or spec.grant.version != offer.rule_version):
            raise ValueError(f"'{offer.tool}' cannot get this standing approval (never offered for it, or its rule "
                             "changed since the question was asked).")
        caller, token_created = self._grant_caller(offer.caller)
        if not caller or token_created != offer.token_created:
            raise ValueError("The client that asked no longer exists: its token was revoked (or revoked and made anew) "
                             "since. Nothing was saved.")
        return caller, token_created

    def create_grant(self, offer, width: str, duration: str, created_via: str, actor: str):
        """A standing approval a person just chose in the framework's own window (`actor`: cli or gui), from an offer
        the framework computed. Saved with the policy (audited, revision checked under the lock); ValueError when the
        offer cannot be used (check_grant)."""
        import secrets as _secrets
        from orchestrator.tools.grants import Grant, expiry_for
        caller, token_created = self.check_grant(offer, width, duration, created_via)
        now = datetime_now_iso()
        grant = Grant(id=f"g_{_secrets.token_hex(8)}", tool=offer.tool, scope=offer.scopes[width],
                      summary=offer.summaries[width][:500], width=width, rule_version=offer.rule_version,
                      created=now, created_via=created_via, expires=expiry_for(duration),
                      token_created=token_created, announced=created_via not in ("cli", "ditto-cli"))

        def change(policy):
            grants = dict(policy.grants)
            grants[caller] = list(grants.get(caller, [])) + [grant]
            return policy.model_copy(update={"grants": grants})
        self.policy_store.update(change, actor=actor)
        return grant

    def revoke_grant(self, grant_id: str, actor: str) -> bool:
        """Removes a standing approval (a person's action, audited with the policy save)."""
        found = []

        def change(policy):
            grants = {}
            for caller, items in policy.grants.items():
                kept = [g for g in items if g.id != grant_id]
                found.extend(g for g in items if g.id == grant_id)
                if kept:
                    grants[caller] = kept
            return policy.model_copy(update={"grants": grants})
        if self.policy_store.load().grant_by_id(grant_id) is None:
            return False
        self.policy_store.update(change, actor=actor)
        return bool(found)

    def grant_rows(self, caller: Optional[str] = None) -> List[Dict[str, Any]]:
        """Each stored standing approval (of one caller, or all) with its uses and last use from the audit log and its
        state: active, expired, lapsed or outdated (the tool's rule changed)."""
        policy = self.policy_store.load()
        rows = []
        for owner, grants in sorted(policy.grants.items()):
            if caller is not None and owner != caller:
                continue
            for grant in grants:
                uses, last = self.audit.grant_uses(grant.id) if self.audit is not None else (0, None)
                spec = self._tools.get(grant.tool)
                state = ("outdated" if spec is None or spec.grant is None or spec.grant.version != grant.rule_version
                         else "expired" if grant.expired() else "lapsed" if grant.lapsed(last) else "active")
                rows.append({"caller": owner, "grant": grant, "uses": uses, "last_use": last, "state": state})
        return rows

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError:
            raise UnknownTool(f"Unknown tool '{name}'.") from None

    def list(self) -> List[ToolSpec]:
        return list(self._tools.values())

    def policy(self, actor: str = "") -> ToolPolicy:
        """Without `actor`: the user's policy file as stored (the shared permissions and a section per caller).
        With an actor: that caller's permissions as one policy, for showing and copying them (`--policy-from`):
        a named MCP token's own (a new token's: read only), the linked caller's for a linked token, nothing for an
        unknown or revoked token; for ditto and mcp their section, or the shared permissions. It carries the tools
        whose questions are put to that caller's person in the framework only (framework_only, the list that applies
        to it now), so a copy or a token's first own permissions never drop that list."""
        if not actor:
            return self.policy_store.load()
        policy, section = self._policy_of(actor)
        if not section:  # a token's own policy, or one that denies everything
            return policy
        own = policy.callers.get(section)
        framework_only = policy.framework_only_for(section)
        if own is None and section.startswith(ACTOR_PREFIX):  # a token without permissions of its own
            from orchestrator.tools.mcp_tokens import read_only_policy
            return read_only_policy().model_copy(update={"framework_only": framework_only})
        source = own if own is not None else policy
        return ToolPolicy(configured=policy.configured, tiers=dict(source.tiers), overrides=dict(source.overrides),
                          reviewed=list(source.reviewed) if source.reviewed is not None else None,
                          framework_only=framework_only)

    def _policy_of(self, actor: str) -> Tuple[ToolPolicy, str]:
        """(the policy that decides for `actor`, the caller whose section in it applies). A named MCP token
        (`mcp:<name>`, checked by the framework) uses its own policy (section ""), or the linked caller's section of
        the user's policy. A token whose permissions the person has not set yet is decided as a new token in the
        user's policy: read only, never more than the shared setting for a tool or the tool's own default. An
        unknown or revoked token (also a name no token can have, such as the caller of an HTTP request nobody
        checked), a registry without a token store and an unusable token store get nothing."""
        stored = self.policy_store.load()
        if not actor.startswith(ACTOR_PREFIX):
            return stored, actor
        if self.tokens is None:  # no store to check a token against
            return restrictive_policy(), ""
        try:
            record = self.tokens.get(actor[len(ACTOR_PREFIX):])
        except TokenStoreError:
            return restrictive_policy(), ""
        if record is None:
            return restrictive_policy(), ""
        if record.link is not None:
            return stored, record.link
        if record.policy is None or not record.policy.configured:
            return stored, actor
        return record.policy, ""

    def decide(self, spec: ToolSpec, inp: Any = None, caller: Union[str, "ToolContext"] = "") -> Decision:
        """The decision for a call of `spec` by `caller` (ditto, mcp, mcp:<token>, or the call's ToolContext): the
        caller's own permissions (or the shared ones), the tool's default for tools the person has not reviewed or
        for this call (e.g. "allow" when the caller cancels its own build, decided on ToolContext.owner), and the
        tool's cap."""
        actor, owner = (caller.actor, caller.owner) if isinstance(caller, ToolContext) else (caller, caller)
        policy, section = self._policy_of(actor)
        return policy.decide_tool(spec, inp, section, owner=owner)

    def hidden_reason(self, spec: ToolSpec, ctx: "ToolContext") -> str:
        """Why this session is not offered the tool at all ("" when it is): the tool is for other callers (checked
        against the real actor, never a token's link), or its `visible_when` says no now (a check that fails hides
        it)."""
        if not caller_allowed(spec.callers, ctx.actor):
            return "it is only for other callers"
        if spec.visible_when is not None:
            try:
                shown = bool(spec.visible_when(ctx))
            except Exception as e:  # noqa: BLE001 - a check that cannot run never shows a tool
                self.logger.warning(f"visible_when of {spec.name} failed: {redact_secrets(str(e))}")
                shown = False
            if not shown:
                return "it is not offered in this session now"
        return ""

    def visible(self, caller: str, ctx: Optional["ToolContext"] = None) -> List[ToolSpec]:
        """The tools a caller is offered: every model-facing tool for this caller and session (`callers`,
        `visible_when`, checked against `ctx`, by default a context of `caller` alone) that its permissions do not deny
        (MCP tools/list, Ditto's tool definitions). Denied and hidden tools stay registered; a call to one is refused
        and audited."""
        ctx = ctx if ctx is not None else ToolContext(caller)
        policy, section = self._policy_of(ctx.actor)
        return [s for s in self._tools.values()
                if s.model_facing and not self.hidden_reason(s, ctx)
                and policy.decide_tool(s, None, section, owner=ctx.actor) != Decision.DENY]

    def permissions(self, caller: str) -> List[Dict[str, Any]]:
        """Per tool, for one caller: decision and why, default, cap, listed or hidden, new (not reviewed yet), and
        whether a confirmation may offer a standing approval."""
        policy, section = self._policy_of(caller)
        rows = []
        bare = ToolContext(caller)
        for spec in self._tools.values():
            decision, why = policy.explain_tool(spec, None, section, owner=caller)
            default = spec.default_decision
            rows.append({
                "tool": spec.name, "title": spec.title, "tier": spec.tier.value, "decision": decision.value,
                "why": why, "default": ("depends on the call" if callable(default) else
                                        default.value if default is not None else "tier setting"),
                "max": ("depends on the call" if callable(spec.max_decision) else spec.cap().value),
                "listed": decision != Decision.DENY and spec.model_facing and not self.hidden_reason(spec, bare),
                "new": not policy.reviewed_tool(section, spec.name),
                "standing_approval": spec.grant is not None,
            })
        return rows

    def _audit(self, ctx: ToolContext, spec: ToolSpec, decision: Decision, outcome: str,
               arguments: Any, approved: Optional[bool] = None, detail: str = "", required: bool = False,
               **extra: Any) -> Optional[int]:
        """Writes one audit entry and returns its id. `required`: the entry must exist before the call goes on
        (a call that changes something, or the question about it); if it cannot be written, AuditUnavailable is
        raised and the call does not run. Otherwise a failed write is only logged, so reads stay available."""
        if self.audit is None:
            return None
        fields = {"client": ctx.client,
                  "session": f"{ctx.session}:{ctx.request_id}" if ctx.request_id else ctx.session, **extra}
        repairs = call_repairs(ctx)
        if repairs:
            fields.setdefault("repairs", [r.audit() for r in repairs])
            renamed = next((r for r in repairs if r.kind == "name"), None)
            if renamed is not None:
                fields.setdefault("name_requested", str(renamed.wrote)[:64])
        # Fields a tool marks audit=False (a data chunk) stay out of the preview; args_sha256 still covers them.
        omit = tuple(name for name, meta in (getattr(spec, "fields", None) or {}).items() if not meta.audit)
        try:
            return self.audit.record(ctx.actor, spec.name, spec.tier.value, decision.value, outcome,
                                     arguments=arguments, approved=approved, detail=detail, extra=fields, omit=omit)
        except Exception as e:  # auditing must never break a read; a change is refused instead
            reason = redact_secrets(str(e)) or type(e).__name__
            if required:
                self.logger.error(f"Audit write failed, '{spec.name}' refused: {reason}")
                raise AuditUnavailable(
                    f"The audit log cannot be written ({reason}), so '{spec.title}' was not run: calls that change "
                    "something run only when they are recorded. Mojo Oracle finds the cause.",
                    steps=FAILED_STEPS) from None
            self.logger.warning(f"Audit write failed: {reason}")
            return None

    def validate_input(self, spec: ToolSpec, arguments: Optional[Dict[str, Any]]) -> BaseModel:
        """The tool's input model; ToolInputError with a message a model can act on (which field, what is allowed,
        what to do next) instead of pydantic's wording."""
        try:
            return spec.input_model.model_validate(arguments or {})
        except ValidationError as e:
            raise self._input_error(spec, arguments or {}, e) from None

    def _input_error(self, spec: ToolSpec, arguments: Dict[str, Any], error: ValidationError) -> ToolInputError:
        missing, problems, allowed_all, names = [], [], [], []
        for err in error.errors():
            name = str(err["loc"][0]) if err.get("loc") else ""
            names.append(name)
            allowed = self._allowed(spec, name, arguments)
            shown = f" (one of: {', '.join(allowed)})" if allowed else ""
            if err.get("type") == "missing":
                missing.append(f"'{name}'{shown}")
            elif err.get("type") == "extra_forbidden":
                problems.append(f"unknown argument '{name}'")
            else:
                where = ".".join(str(p) for p in err.get("loc", ())) or "input"
                problems.append(f"'{where}': {err.get('msg')}{shown}")
            allowed_all += allowed
        parts = []
        if missing:
            parts.append(f"{spec.name} needs {', '.join(missing)}.")
        if problems:
            parts.append(f"Invalid arguments for {spec.name}: {'; '.join(problems)}.")
        listing = next((spec.fields[n].list_tool for n in names if n in spec.fields and spec.fields[n].list_tool), "")
        next_step = (f"Call {listing} for the values you can use, then call {spec.name} again." if listing else
                     f"Fix {'these arguments' if len(missing) + len(problems) > 1 else 'this argument'} and call "
                     f"{spec.name} again.")
        # The fields the error is about (Ditto's loop tells a fix of another field from a repeat by them).
        fields = ",".join(sorted({n for n in names if n}))
        return ToolInputError(" ".join(parts), code="missing_argument" if missing and not problems else
                              "invalid_value", next=next_step, allowed=allowed_all, field=fields)

    @staticmethod
    def _allowed(spec: ToolSpec, name: str, arguments: Dict[str, Any]) -> List[str]:
        """The closed list of values a field takes, as far as it is known (for messages)."""
        meta = spec.fields.get(name)
        info = spec.input_model.model_fields.get(name)
        literal = list_item_literal(info.annotation) if info is not None else None
        if literal:
            return list(literal)
        if meta is None:
            return []
        if meta.choices:
            return list(meta.choices)
        if meta.choices_from is not None:
            try:
                choices = meta.choices_from(dict(arguments))
            except Exception:  # noqa: BLE001 - only a hint for the message
                choices = None
            if choices is not None:
                return list(choices.values)
        return []

    def refuse(self, name: str, arguments: Optional[Dict[str, Any]], ctx: ToolContext,
               outcome: str = "not_confirmed", detail: str = "", decision: Optional[Decision] = None,
               **extra: Any) -> None:
        """Audits a call that was refused, cancelled or left unanswered outside call() (e.g. because it could
        not be confirmed). Nobody approved it, so `approved` stays NULL. `decision`: when the refusal was not the
        policy's (e.g. the caller's token ended); otherwise the policy decides what is recorded."""
        spec = self.get(name)
        self._audit(ctx, spec, decision or self.decide(spec, caller=ctx.actor), outcome, arguments, detail=detail,
                    **extra)

    # --- the fool-proof layer and the repeat guard -------------------------------------------------------------

    def resolve(self, name: Any, caller: str) -> Tuple[ToolSpec, Optional[Repair]]:
        """The tool a name means (exact or a spelling variant); NormalizeError with suggestions otherwise."""
        if isinstance(name, str) and name in self._tools:
            return self._tools[name], None
        visible = self.visible(caller)
        resolved, repair = resolve_name(name, list(self._tools), [s.name for s in visible],
                                        read_tools=[s.name for s in visible if s.tier == ToolTier.READ])
        return self._tools[resolved], repair

    def prepare(self, name: Any, arguments: Any, ctx: ToolContext) -> Prepared:
        """Name resolution, argument normalization, the repeat guard and validation, for every caller (Ditto calls
        it through call(); the MCP server also calls it on the raw arguments, before the SDK's argument model could
        drop unknown keys). A refusal is audited and raised as a ToolError with a reason code."""
        try:
            spec, renamed = self.resolve(name, ctx.actor)
        except NormalizeError as e:
            self._audit_unknown(ctx, name, arguments, e)
            refusal = self._repeat_refusal(ctx, f"?{name!r}", arguments)
            if refusal is not None:
                raise refusal from None
            raise self._failed(ctx, f"?{name!r}", arguments,
                               UnknownTool(str(e), next=e.next, suggestions=e.suggestions)) from None
        repairs = _merge(ctx.repairs, [renamed] if renamed else [])
        work = dataclasses.replace(ctx, repairs=repairs)
        try:
            return self._prepare_call(spec, arguments, work)
        except ToolError as e:  # a refusal after a repair names the repair, as a successful result would
            with_repairs(e, call_repairs(work))
            raise

    def _prepare_call(self, spec: ToolSpec, arguments: Any, work: ToolContext) -> Prepared:
        """prepare() once the tool is known; `work.repairs` holds the repairs so far (also when it refuses)."""
        def decision() -> Decision:  # only for the audit entry of a refusal: a successful prepare decides nothing
            return self.decide(spec, caller=work.actor)

        def refuse_repeat(args: Any) -> None:
            refusal = self._repeat_refusal(work, spec.name, args)
            if refusal is not None:
                self._audit(work, spec, decision(), "repeat_refused", args, detail=refusal.message,
                            reason_code="repeat_refused")
                raise refusal
        hidden = self.hidden_reason(spec, work)  # the real actor: a token linked to Ditto is not Ditto
        if hidden:
            error = ToolError(f"'{spec.name}' is not available to this caller ({hidden}).", code="not_supported",
                              next="Use only the tools listed for you; tell the person what you wanted to do.")
            self._audit(work, spec, decision(), "denied", arguments, detail=error.message, reason_code="not_supported")
            raise self._failed(work, spec.name, arguments, error)
        failure: Optional[NormalizeError] = None
        try:
            normalized = normalize_arguments(spec, arguments)
            work.repairs = _merge(work.repairs, normalized.repairs)
        except NormalizeError as e:  # its repairs (a declared alias read before an invalid value) are shown too
            failure = e
            work.repairs = _merge(work.repairs, e.repairs)
        refuse_repeat(arguments)  # the same raw call (e.g. the same unknown argument) a third time
        if failure is not None:
            error = ToolInputError(str(failure), code=failure.code, next=failure.next, allowed=failure.allowed,
                                   suggestions=failure.suggestions, field=failure.field)
            raise self._input_failed(work, spec, decision, arguments, error) from None
        args = normalized.arguments
        if args != arguments:
            refuse_repeat(args)
        try:
            inp = self.validate_input(spec, args)
        except ToolInputError as e:
            raise self._input_failed(work, spec, decision, args, e) from None
        key = inp.model_dump(mode="json")
        refuse_repeat(key)  # a call that already failed twice after validation: refused before any question
        return Prepared(spec, args, inp, work.repairs, key)

    def enforce_limits(self, prepared: Prepared, ctx: ToolContext, rate_too: bool = True) -> None:
        """The rate and quotas of the limits table for this caller (design 6.5), checked after validation and before
        the policy and any question: the read rate (read tools together) or the tool's own rate, its unfinished runs
        (start_build: 3 builds per client) and its runs per day (start_build: 20). A limit hit is audited
        (`rate_limited`, reason code `limit_reached`) and says when to try again; it never counts as a failed call
        for the repeat guard. `rate_too=False`: only the quotas, for a call whose rate was counted when it was made
        and that runs later (a pending approval the person approved minutes after: their builds may have grown)."""
        spec, limits = prepared.spec, prepared.spec.limits
        problem: Optional[Tuple[str, Optional[float]]] = None
        own = limits.rate
        if spec.tier != ToolTier.READ and call_only_reads(spec, prepared.input):
            own = None  # a reading call of a tool that can also change things counts with the read tools
        rate = (own or (self.limits.read_rate if call_only_reads(spec, prepared.input) else None)) if rate_too else None
        if rate is not None:
            wait = self.limiter.take(("rate", ctx.actor, spec.name if own else "read"), rate.count,
                                     rate.per, rate.burst)
            if wait is not None:
                noun = f"calls of '{spec.title}'" if own else "read tool calls"
                problem = (f"This client made too many {noun} (at most {rate.count} per {rate.per:g} seconds).", wait)
        if problem is None and limits.concurrent and limits.running is not None:
            try:
                running = int(limits.running(ctx))
            except Exception as e:  # noqa: BLE001 - nothing to count is no reason to refuse
                self.logger.warning(f"Counting the unfinished {limits.what} of {ctx.actor} failed: {e}")
                running = 0
            if running >= limits.concurrent:
                problem = (f"This client already has {running} unfinished {limits.what} (at most "
                           f"{limits.concurrent} at a time).", None)
        if problem is None and limits.per_day:
            wait = self.limiter.full(("day", ctx.actor, spec.name), limits.per_day, DAY)
            if wait is not None:
                problem = (f"This client started {limits.per_day} {limits.what} in the last 24 hours, the most a day "
                           "allows.", wait)
        if problem is None:
            return
        message, wait = problem
        message += f" Try again {wait_text(wait)}." if wait is not None else " Try again when one of them has ended."
        error = LimitHit(message, code="limit_reached",
                         next="Wait, then call it again; tell the person if they are waiting for it.")
        self._audit(ctx, spec, self.decide(spec, caller=ctx.actor), "rate_limited", prepared.arguments,
                    detail=message, reason_code="limit_reached")
        raise error

    def question_refusal(self, spec: ToolSpec, inp: Optional[BaseModel], ctx: ToolContext) -> Optional[LimitHit]:
        """Why a question about this call must not be put to the person now (design 6.5, U8), or None: they said no
        to this exact call (same tool, same normalized arguments, so a respelled call counts as the same) less than
        10 minutes ago; they said no to this tool three times within 30 minutes (its questions pause for 30
        minutes); or this client already asked 30 questions in the last hour."""
        limits = self.limits
        key = _args_key(inp.model_dump(mode="json")) if inp is not None else ""
        number, since, _ = self.limiter.count(("no", ctx.actor, spec.name, key), limits.decline_cooldown)
        if number:
            return LimitHit(f"The person declined this exact call of '{spec.title}' {_minutes(since)} ago; it is not "
                             f"asked again for another {_minutes(limits.decline_cooldown - since)}.", code="declined",
                             next="Do not retry it unless the person asks for it; tell them what you wanted to do.")
        number, since, _ = self.limiter.count(("pause", ctx.actor, spec.name), limits.decline_pause)
        if number:
            return LimitHit(f"The person declined '{spec.title}' {limits.declines_to_pause} times within "
                             f"{_minutes(limits.decline_window)}; questions about it are paused for another "
                             f"{_minutes(limits.decline_pause - since)}.", code="declined",
                             next="Do not call it again now; ask the person in your answer what they want instead.")
        wait = self.questions_full(ctx)
        if wait is not None:
            return LimitHit(f"This client already asked the person {limits.questions_per_hour} questions in the last "
                             f"hour, the most allowed. Try again {wait_text(wait)}.", code="limit_reached",
                             next="Tell the person what you would like to do instead of asking again now.")
        return None

    def questions_full(self, ctx: ToolContext) -> Optional[float]:
        """None while this client may still put a question to the person this hour (elicitations, pending approvals
        and requests to stop asking together), else the seconds until it may again."""
        return self.limiter.full(("questions", ctx.actor), self.limits.questions_per_hour, 3600.0)

    def asked(self, ctx: ToolContext) -> None:
        """Counts a question put to the person (questions per hour per client)."""
        self.limiter.add(("questions", ctx.actor), 3600.0)

    def declined(self, spec: ToolSpec, inp: BaseModel, ctx: ToolContext) -> None:
        """Records a person's "no" for the cooldown of this exact call and the pause of the tool."""
        limits = self.limits
        self.limiter.add(("no", ctx.actor, spec.name, _args_key(inp.model_dump(mode="json"))),
                         limits.decline_cooldown)
        self.limiter.add(("no", ctx.actor, spec.name), limits.decline_window)
        if self.limiter.count(("no", ctx.actor, spec.name), limits.decline_window)[0] >= limits.declines_to_pause:
            self.limiter.clear(("no", ctx.actor, spec.name))  # the pause starts now; later "no" count anew
            self.limiter.add(("pause", ctx.actor, spec.name), limits.decline_pause)

    def _session(self, ctx: ToolContext) -> Dict[str, int]:
        key = (ctx.actor, ctx.session)
        counts = self._sessions.pop(key, None) or {"input_errors": 0, "unknown_ids": 0}
        self._sessions[key] = counts
        while len(self._sessions) > GUARD_SIZE:
            self._sessions.popitem(last=False)
        return counts

    def _input_failed(self, ctx: ToolContext, spec: ToolSpec, decision: Callable[[], Decision], arguments: Any,
                      error: ToolError) -> ToolError:
        with self._guard_lock:
            counts = self._session(ctx)
            counts["input_errors"] += 1
            example = counts["input_errors"] >= EXAMPLE_AFTER and bool(spec.examples)
        if example:
            shown = json.dumps(spec.examples[0], ensure_ascii=False)
            error.next = (error.next + " " if error.next else "") + f"A valid call looks like: {spec.name} {shown}"
        self._audit(ctx, spec, decision(), "invalid_input", arguments, detail=str(error), reason_code=error.code)
        return self._failed(ctx, spec.name, arguments, error)

    def _failed(self, ctx: ToolContext, tool: str, arguments: Any, error: ToolError) -> ToolError:
        """Records a failed call for the repeat guard and returns the error to raise: the second identical failure
        (same tool, same normalized arguments, same code) says so, and the second unknown id in a session adds
        "do not guess ids". A pending approval is not a failure, nor is a refusal of the limits table (LimitHit)."""
        if error.code in PENDING_CODES or isinstance(error, LimitHit):
            return error
        key = (ctx.actor, ctx.session, tool, _args_key(arguments))
        now = self.limiter.clock()
        with self._guard_lock:
            record = self._failures.pop(key, None)
            if (record is None or record["code"] != error.code
                    or now - record["at"] >= self.limits.repeat_cooldown):  # long ago: a new first failure
                record = {"code": error.code, "count": 0, "message": error.message, "quoted": error.quoted}
            record["count"] += 1
            record["at"] = now
            self._failures[key] = record
            while len(self._failures) > GUARD_SIZE:
                self._failures.popitem(last=False)
            unknown_ids = 0
            if error.code == "unknown_id":
                counts = self._session(ctx)
                counts["unknown_ids"] += 1
                unknown_ids = counts["unknown_ids"]
        message, next_step = error.message, error.next
        if record["count"] >= 2 and not message.startswith(REPEAT_WARN):
            message = REPEAT_WARN + message
        if unknown_ids >= 2:
            next_step = "Do not guess ids. " + (next_step or "List them with the matching list tool.")
        if (message, next_step) == (error.message, error.next):
            return error
        return error.with_text(message, next_step)

    def _repeat_refusal(self, ctx: ToolContext, tool: str, arguments: Any) -> Optional[ToolError]:
        """The refusal for a call that already failed twice in this session, None otherwise. Time-based: once the
        last failure is `repeat_cooldown` old (10 minutes), the call may run again (the build it waited for may have
        ended); the refusal itself never extends the time."""
        key = (ctx.actor, ctx.session, tool, _args_key(arguments))
        now = self.limiter.clock()
        with self._guard_lock:
            record = self._failures.get(key)
            if record is not None and now - record["at"] >= self.limits.repeat_cooldown:
                del self._failures[key]
                record = None
        if record is None or record["count"] < 2:
            return None
        reason = record["message"][len(REPEAT_WARN):] if record["message"].startswith(REPEAT_WARN) else \
            record["message"]
        left = self.limits.repeat_cooldown - (now - record["at"])
        return ToolError(f"Stop retrying this call. Tell the user why it failed: {reason}", code="repeat_refused",
                         next=f"This exact call is refused for another {_minutes(left)}; until then answer the user "
                              "instead of calling it again.", quoted=record.get("quoted", ""))

    def _succeeded(self, ctx: ToolContext, tool: str, arguments: Any) -> None:
        with self._guard_lock:
            self._failures.pop((ctx.actor, ctx.session, tool, _args_key(arguments)), None)

    def _audit_unknown(self, ctx: ToolContext, name: Any, arguments: Any, error: Exception) -> None:
        """An unknown tool name is audited too (there is no spec to name the tier)."""
        if self.audit is None:
            return
        shown = "".join(ch if ch.isprintable() else "?" for ch in str(name))[:64]
        try:
            self.audit.record(ctx.actor, shown, "unknown", Decision.DENY.value, "unknown_tool", arguments=arguments,
                              detail=str(error), extra={"client": ctx.client, "session": ctx.session,
                                                        "name_requested": shown, "reason_code": "unknown_tool"})
        except Exception as e:  # reads and refusals never fail on the audit log
            self.logger.warning(f"Audit write failed: {redact_secrets(str(e))}")

    def record_question(self, name: str, arguments: Optional[Dict[str, Any]], ctx: ToolContext,
                        question: str, decision: Optional[Decision] = None) -> str:
        """Records that a question about a call is put to the person (before it is asked) and returns the hash of
        its text. `decision`: the one the question is asked under (a looked-up fact can make an allowed call ask);
        otherwise the policy's. For a call that changes something the question is not asked when it cannot be
        recorded (AuditUnavailable)."""
        spec = self.get(name)
        digest = text_sha256(question)
        self._audit(ctx, spec, decision or self.decide(spec, caller=ctx.actor), "confirmation_requested", arguments,
                    required=spec.tier != ToolTier.READ, confirm_channel=ctx.confirm_channel or "elicitation",
                    question_sha256=digest)
        self.asked(ctx)
        return digest

    @staticmethod
    def speaking_names(spec: ToolSpec, arguments: Dict[str, Any]) -> Dict[str, str]:
        """Speaking names of id values for a person (FieldMeta.describe: a build's model and target for its request
        id, a local file's label for its file id). A lookup that fails shows no name; it never stops a question."""
        names = {}
        for name, meta in spec.fields.items():
            value = arguments.get(name)
            if meta.describe is None or value in (None, ""):
                continue
            try:
                described = meta.describe(value)
            except Exception:  # noqa: BLE001 - a name is a help for the person, never a reason to fail
                described = None
            if described and str(described) != str(value):
                names[name] = " ".join(str(described).split())[:200]
        return names

    def question_text(self, spec: ToolSpec, arguments: Dict[str, Any], ctx: ToolContext,
                      facts: Optional[CallFacts] = None, limit: int = MAX_FRAMEWORK,
                      person_view: Optional[bool] = None) -> str:
        """The question for one call (confirmation text v2): the consequence and the facts the tool looked up, who
        asks, every argument with its speaking name and the repaired values marked. CallTooLong above `limit`
        (4,000 characters for an MCP elicitation, 16,000 for the framework's own windows). `person_view`: the question
        is shown only in the framework's own window (default: when `limit` is the framework's), so it may hold the
        facts only the person sees; a question for a client's form never does."""
        marks, renamed = repair_marks(ctx.repairs)
        return describe_call(spec, arguments, actor="Ditto" if ctx.actor == "ditto" else "The AI client",
                             marks=marks, renamed=renamed, requester=requester_of(ctx), facts=facts,
                             names=self.speaking_names(spec, arguments), limit=limit,
                             person_view=limit >= MAX_FRAMEWORK if person_view is None else person_view)

    def question_for(self, spec: ToolSpec, inp: BaseModel, ctx: ToolContext,
                     facts: Optional[CallFacts] = None) -> str:
        """The question the framework's own channel shows (Ditto's dialog or prompt, up to 16,000 characters);
        "" when it is too long to show, in which case that channel refuses the call."""
        try:
            return self.question_text(spec, inp.model_dump(mode="json"), ctx, facts, MAX_FRAMEWORK)
        except CallTooLong:
            return ""

    def look_up(self, spec: ToolSpec, inp: BaseModel, ctx: ToolContext) -> Optional[CallFacts]:
        """The facts the tool's prepare looks up for this call (None for a tool without one). A failure is a
        ToolError with the reason code of the service's error (e.g. no pending proposal), cleaned like a handler's
        error: it can quote external text."""
        if spec.prepare is None:
            return None
        try:
            facts = spec.prepare(inp, ctx)
        except ToolError as e:
            message = external_error_text(e.message)
            error = e.with_text(message, redact_secrets(e.next))
            error.quoted = message  # with_text keeps its repairs: a repair before the refusal is named by it
            raise error from None
        except ValueError as e:  # a service's refusal (BuildServiceError carries code and next)
            message = external_error_text(str(e))
            own_next = redact_secrets(getattr(e, "next", "") or "")
            error = ToolError(message, code=getattr(e, "code", None) or "failed", next=own_next or FAILED_NEXT,
                              quoted=message, steps=_steps_of(e) or (() if own_next else FAILED_STEPS))
            error.repairs = repairs_of(e)
            blocker = getattr(e, "blocker", "")  # a service's refusal that names a blocker of the facts catalogue
            if blocker:
                from orchestrator.tools.facts_catalog import BLOCKERS, with_blocker
                if blocker in BLOCKERS:
                    with_blocker(error, blocker)
            raise error from e
        except Exception as e:
            self.logger.error(f"Looking up the facts for {spec.name} failed: {e}", exc_info=True)
            message = external_error_text(str(e))
            raise ToolError(f"{spec.title} could not be prepared: {message}", next=FAILED_NEXT, quoted=message,
                            steps=_steps_of(e) or FAILED_STEPS) from e
        if not isinstance(facts, CallFacts):
            raise ToolError(f"{spec.title} could not be prepared (internal error; details are in the framework log).",
                            next=FAILED_NEXT, steps=FAILED_STEPS)
        # The facts name their tool, so their decision digest is bound to it (design 4.4).
        return facts if facts.tool == spec.name else dataclasses.replace(facts, tool=spec.name)

    @staticmethod
    def capped(decision: Decision, facts: Optional[CallFacts]) -> Decision:
        """The decision after the cap the looked-up facts call for (e.g. ask before a destructive strategy)."""
        if facts is None or not facts.cap:
            return decision
        try:
            return stricter(decision, Decision(facts.cap))
        except ValueError:
            return Decision.DENY  # a cap nobody can read never loosens anything

    def call(self, name: str, arguments: Optional[Dict[str, Any]], ctx: ToolContext,
             approved: Optional[bool] = None) -> BaseModel:
        """Runs a tool for `ctx.actor`. `approved` is the caller's own confirmation result
        (e.g. from an MCP elicitation); without it, ctx.confirm is asked when the policy says "ask".
        False refuses the call whatever the policy says.

        Audit: a call that changes something gets a write-ahead `started` entry before its handler runs and is
        refused when that entry cannot be written; a question put to the person is recorded before it is asked;
        the end entry names the outcome, the duration, a hash of the result and the `started` entry it belongs
        to. Reads stay available when the audit log cannot be written (the failure is logged)."""
        began = time.monotonic()
        prepared = self.prepare(name, arguments, ctx)  # its refusals name the repairs already
        if not ctx.limits_checked:  # the MCP server counts a call before its question
            limited = dataclasses.replace(ctx, repairs=prepared.repairs)
            try:
                self.enforce_limits(prepared, limited)
            except ToolError as e:
                e.fill_steps(getattr(ctx, "channel", None))
                with_repairs(e, call_repairs(limited))
                raise
        spec, inp, arguments = prepared.spec, prepared.input, prepared.arguments
        # per call: Ditto reuses one context
        ctx = dataclasses.replace(ctx, repairs=prepared.repairs, notes=[])
        try:
            result = self._run(spec, inp, arguments, ctx, approved, began)
        except ToolError as e:
            e.fill_steps(getattr(ctx, "channel", None))   # the next steps in the caller's words (wave 6, A.6)
            # Every refusal and error after a repair names it, as a successful result's notes would (a denied call,
            # a look-up that found too little disk space, a "no", a handler's failure): the fool-proof layer's
            # repairs and the look-up's (ctx.facts, set by _run on this very context, or carried by its refusal).
            error = with_repairs(self._failed(ctx, spec.name, prepared.key, e), call_repairs(ctx, repairs_of(e)))
            if error is e:
                raise
            raise error from None
        self._succeeded(ctx, spec.name, prepared.key)
        return result

    def record_job_end(self, name: str, arguments: Any, ctx: ToolContext, outcome: str, detail: str = "",
                       reason_code: str = "", result: Any = None) -> Optional[int]:
        """The end entry of work a call started that runs on after the call itself returned (a job, e.g. a deploy):
        linked to the call's `started` entry (ctx.audit_started), so the audit log ties the end of the work to the
        call, the question and the answer that started it. outcome: ok, error or cancelled. A failed write is logged;
        the work has already happened."""
        spec = self.get(name)
        try:
            decision = Decision(ctx.audit_decision) if ctx.audit_decision else self.decide(spec, caller=ctx.actor)
        except ValueError:
            decision = Decision.ASK
        links = [ctx.audit_started] if ctx.audit_started else []
        return self._audit(ctx, spec, decision, outcome, arguments, detail=("job end: " + detail).strip()[:1000],
                           links=links, reason_code=reason_code, result=result)

    def discard_staged(self, spec: ToolSpec, inp: Optional[BaseModel], ctx: ToolContext) -> None:
        """Lets the tool drop what its prepare staged for a call that will not run (ToolSpec.discard)."""
        if spec.discard is None or inp is None:
            return
        try:
            spec.discard(inp, ctx)
        except Exception as e:  # noqa: BLE001 - cleaning up never fails a refusal
            self.logger.warning(f"Discarding the staged work of {spec.name} failed: {redact_secrets(str(e))}")

    def _refused_before_question(self, spec: ToolSpec, ctx: ToolContext, code: str, stage: str, blocker: str = "",
                                 person_action: str = "", steps: Any = ()) -> None:
        """Tells the observers that an AI's call was refused before anything was asked (the chat shows what the
        person can do about it). Framework facts only; the person's own CLI and GUI calls are not reported. `step`
        is the first catalogue step id (orchestrator/utils/next_steps), which the blocked row carries."""
        if ctx.actor in ("cli", "gui"):
            return
        from orchestrator.utils.next_steps import as_next_steps
        found = as_next_steps(steps)
        extra = {"step": found[0].id} if found else {}
        self.events.emit("refused_before_question", tool=spec.name, actor=ctx.actor, owner=ctx.owner,
                         code=code or "failed", stage=stage, blocker=blocker or "", person_action=person_action or "",
                         **extra)

    def _run(self, spec: ToolSpec, inp: BaseModel, arguments: Dict[str, Any], ctx: ToolContext,
             approved: Optional[bool], began: float) -> BaseModel:
        from orchestrator.Core.confirmed_builds import person_confirmed
        decision = self.decide(spec, inp, ctx)
        if decision == Decision.DENY:
            self._audit(ctx, spec, decision, "denied", arguments, reason_code="permission_denied")
            # The person's feed entry carries the place where they decide (rendered for the window); the AI's error
            # carries no step: a denial by the person's own setting never pushes a way to change it (DENIED_HINT).
            self._refused_before_question(spec, ctx, "permission_denied", stage="policy", steps=UNASKED_STEPS)
            raise ToolDenied(f"'{spec.title}' is not allowed by your tool permissions.", next=DENIED_HINT)
        # Two-phase: the facts only the server knows, looked up before the question (the MCP server hands in the
        # ones its question showed). They can make an allowed call ask, and the handler is bound to them.
        if ctx.facts is None and spec.prepare is not None:
            try:
                ctx.facts = self.look_up(spec, inp, ctx)
            except ToolError as e:
                e.fill_steps(getattr(ctx, "channel", None))
                self._audit(dataclasses.replace(ctx, repairs=call_repairs(ctx, repairs_of(e))), spec, decision,
                            "error", arguments, detail=f"{e.message} {e.next}".strip(), reason_code=e.code)
                self._refused_before_question(spec, ctx, e.code, stage="look_up", blocker=e.blocker,
                                              person_action=e.person_action, steps=e.steps)
                raise
        decision = self.capped(decision, ctx.facts)
        # Round 5 f: a call that deletes something is the person's alone. It always asks; neither "allow", a standing
        # approval, a delegation nor a client's own form counts, only the person in the framework's own window (Ditto's
        # dialog or prompt, or a pending approval decided in the GUI or CLI).
        deleting = deletes(spec, ctx.facts)
        if deleting:
            if decision == Decision.ALLOW:
                decision = Decision.ASK
            if approved is True and not person_confirmed(ctx.confirm_channel):
                approved = None  # e.g. an accepted MCP form: the client's statement, not the person's confirmation
                ctx = dataclasses.replace(ctx, confirm_channel="", answer="", question_sha256="")
        elif (approved is True and ctx.confirm_channel == "elicitation"
              and self.framework_channel_required(spec, ctx.actor, ctx.facts)):
            # Only the framework's own window may ask about this call (a tool that takes no other channel, or one the
            # person set to "confirm in the framework", also while the client's form was open): a form answer is none.
            approved = None
            ctx = dataclasses.replace(ctx, confirm_channel="", answer="", question_sha256="")
        if ctx.metadata.get("grant_requested"):
            ctx.notes.append(GRANT_REQUEST_NOTE)
        writes = not call_only_reads(spec, inp)
        channel, answer, question = ctx.confirm_channel, ctx.answer, ctx.question_sha256
        if decision == Decision.ASK and approved is None and not deleting:
            grant = self.matching_grant(spec, inp, ctx)  # read anew right before the handler
            if grant is not None:
                approved, channel, answer, question = True, f"grant:{grant.id}", "accepted", ""
                # the handler sees which standing approval ran it (a pack installed under one is marked so)
                ctx = dataclasses.replace(ctx, confirm_channel=channel)
                self.events.emit("grant_used", tool=spec.name, actor=ctx.actor, owner=ctx.owner, grant_id=grant.id)
        # Only a framework window asks about a deletion (a confirm callback of another channel cannot).
        confirm = ctx.confirm
        if confirm is not None and deleting and not person_confirmed(getattr(confirm, "confirm_channel", "framework")):
            confirm = None
        # A person's "no" is final, even if the policy was loosened to "allow" while they were asked.
        if decision == Decision.ASK or approved is False:
            if approved is None and confirm is not None:
                channel = getattr(confirm, "confirm_channel", "framework")
                refusal = self.question_refusal(spec, inp, ctx)  # a recent "no", a paused tool, too many questions
                if refusal is not None:
                    self._audit(ctx, spec, decision, "rate_limited", arguments, detail=refusal.message,
                                reason_code=refusal.code)
                    raise refusal
                try:
                    text = self.question_text(spec, inp.model_dump(mode="json"), ctx, ctx.facts, MAX_FRAMEWORK)
                except CallTooLong as e:  # nobody could be shown it: not a person's "no"
                    self._audit(ctx, spec, decision, "not_confirmed", arguments, detail=str(e),
                                confirm_channel=channel, answer="unavailable", reason_code="limit_reached")
                    raise ToolDenied(str(e), code="limit_reached", next=TOO_LONG_NEXT) from None
                question = text_sha256(text)
                # Recorded before it is asked; a question about a change that cannot be recorded is not asked.
                self._audit(ctx, spec, decision, "confirmation_requested", arguments, required=writes,
                            confirm_channel=channel, question_sha256=question)
                self.asked(ctx)
                offer = self.grant_offer(spec, inp, ctx) if spec.grant is not None else None
                token, offered, calling = _QUESTION.set(text), _OFFER.set(offer), _CALL.set((self, ctx.actor))
                try:
                    reply = confirm(spec, inp)
                finally:
                    _CALL.reset(calling)
                    _OFFER.reset(offered)
                    _QUESTION.reset(token)
                approved = bool(reply)
                answer = "accepted" if approved else "declined"
                choice = getattr(reply, "grant_choice", None)
                if approved and offer is not None and choice is not None:
                    # "Yes, and do not ask again" in Ditto's own dialog or prompt: the framework's window.
                    via = "ditto-gui" if channel == "framework-gui" else "ditto-cli"
                    try:
                        self.create_grant(offer, choice[0], choice[1], via, "gui" if via == "ditto-gui" else "cli")
                    except Exception as e:  # the call still runs once; the standing approval is reported missing
                        self.logger.warning(f"The standing approval for {spec.name} was not saved: "
                                            f"{redact_secrets(str(e))}")
            if (approved is None and ctx.pending_approvals and self.approvals is not None
                    and "framework" in spec.channels):
                # Nobody could be asked through the client: the question appears in the framework instead, with the
                # facts the tool looked up (ctx.facts), and the framework runs the call itself, bound to exactly those
                # facts, once the person approves it there. A pending approval is a question to the person: the
                # limits on questions apply to it (a recent "no", a paused tool, questions per hour, 5 open ones).
                try:
                    job = self.approvals.request(spec, inp, arguments, ctx, decision)
                except CallTooLong as e:  # longer than even the framework's window shows: nobody can confirm it
                    self._audit(ctx, spec, decision, "not_confirmed", arguments, detail=str(e), confirm_channel="none",
                                answer="unavailable", reason_code="limit_reached")
                    raise ToolDenied(str(e), code="limit_reached", next=TOO_LONG_NEXT) from None
                except LimitHit as e:
                    self._audit(ctx, spec, decision, "rate_limited", arguments, detail=e.message,
                                reason_code=e.code)
                    raise
                raise ToolError(f"Waiting for the person to approve '{spec.title}' in the framework (a notice, "
                                f"and the list of pending approvals). Job {job.job_id}: poll get_job_status with "
                                "wait_seconds=50.", code="waiting_for_approval",
                                next="Do not call this tool again; the framework runs it when approved.",
                                steps=APPROVAL_STEPS)
            if not approved:
                # approved stays None when nobody could be asked; False is a person's "no"
                self.discard_staged(spec, inp, ctx)
                code = "declined" if approved is False else "needs_person"
                if approved is False and (answer or "declined") == "declined":  # not a dismissed dialog
                    self.declined(spec, inp, ctx)
                self._audit(ctx, spec, decision, "not_confirmed", arguments, approved=approved,
                            confirm_channel=channel or "none", question_sha256=question,
                            answer=answer or ("declined" if approved is False else "unavailable"), reason_code=code)
                raise ToolDenied(f"'{spec.title}' needs your confirmation, which was not given.", code=code,
                                 next=("Do not retry it unless the person asks for it." if code == "declined" else
                                       "Nobody could be asked. " + UNASKED_HINT),
                                 steps=() if code == "declined" else UNASKED_STEPS)
        if approved and channel and channel != ctx.confirm_channel:
            # the handler sees how this call was confirmed (a build the person confirmed in the framework's own
            # window is recorded, design 6.10)
            ctx = dataclasses.replace(ctx, confirm_channel=channel, answer=answer or "accepted")
        confirmation = {"confirm_channel": channel or "none", "question_sha256": question, "answer": answer}

        started = None
        if writes:  # write-ahead: a crash in the middle of a side effect still leaves a record
            started = self._audit(ctx, spec, decision, "started", arguments, approved=approved, required=True,
                                  **confirmation)
        links = [started] if started else []
        ctx.audit_started, ctx.audit_decision = started, decision.value  # ctx is this call's own copy (call())
        if ctx.cancel.is_set():  # the caller gave up before the handler started
            self._audit(ctx, spec, decision, "cancelled", arguments, approved=approved, links=links,
                        detail="Cancelled before it started.", reason_code="failed", **confirmation)
            raise ToolError(f"'{spec.title}' was cancelled before it started.", code="failed",
                            next="Nothing ran. Call it again only if the person still wants it.")
        def end(outcome: str, detail: str = "", result: Any = None, reason_code: str = "") -> None:
            if ctx.cancel.is_set():
                detail = ("The client cancelled the call while it ran; this is what it did. " + detail).strip()
            self._audit(ctx, spec, decision, outcome, arguments, approved=approved, detail=detail,
                        duration_ms=int((time.monotonic() - began) * 1000), links=links, result=result,
                        reason_code=reason_code, **confirmation)

        token = _CALL.set((self, ctx.actor))
        try:
            raw = spec.handler(inp, ctx) if spec.takes_context else spec.handler(inp)
            result = self._output(spec, raw, [r.note(spec.name) for r in call_repairs(ctx)] + list(ctx.notes))
        except ResultTooLarge as e:
            end("error", str(e), reason_code="limit_reached")
            raise ToolError(f"{spec.title}: {e}", code="limit_reached") from None
        except ToolError as e:  # raised by the handler: its message can quote external text
            message, next_step = external_error_text(e.message), redact_secrets(e.next)
            if not next_step and e.code == "failed" and not e.steps:
                next_step = FAILED_NEXT
                e.steps = FAILED_STEPS
            end("error", f"{message} {next_step}".strip(), reason_code=e.code)
            error = e if (message, next_step) == (e.message, e.next) else e.with_text(message, next_step)
            error.quoted = message
            if error is e:
                raise
            raise error from None  # the original still holds the secret
        except ValidationError as e:  # a schema or handler bug (output check, models built inside the handler)
            self.logger.error(f"Tool {spec.name} produced invalid data: {e}", exc_info=True)
            end("error", repr(e), reason_code="failed")
            raise ToolError(f"{spec.title} failed (internal error; details are in the framework log).",
                            next=FAILED_NEXT, steps=FAILED_STEPS) from e
        except ValueError as e:  # user-facing validation problems (e.g. BuildServiceError, with its own code)
            code = getattr(e, "code", None) or "failed"
            end("error", str(e), reason_code=code)
            message = external_error_text(str(e))
            next_step = redact_secrets(getattr(e, "next", "") or "")
            steps = _steps_of(e)
            if not next_step and not steps and code == "failed" and "Next:" not in message:
                next_step, steps = FAILED_NEXT, FAILED_STEPS
            raise ToolError(message, code=code, next=next_step, quoted=message,
                            field=str(getattr(e, "field", "") or ""), steps=steps) from e
        except Exception as e:
            self.logger.error(f"Tool {spec.name} failed: {e}", exc_info=True)
            end("error", repr(e), reason_code="failed")
            message = external_error_text(str(e))
            raise ToolError(f"{spec.title} failed: {message}", next=FAILED_NEXT, quoted=message,
                            steps=_steps_of(e) or FAILED_STEPS) from e
        except BaseException:  # interrupted in the calling thread (KeyboardInterrupt, shutdown): still recorded
            end("cancelled", "Interrupted while it ran.")
            raise
        finally:
            _CALL.reset(token)
        # A run counts toward the tool's quota per day once it ran: a declined call, or a start the handler refused
        # (a full build queue, an invalid request), started nothing.
        if spec.limits.per_day:
            self.limiter.add(("day", ctx.actor, spec.name), DAY)
        serialized = result.model_dump_json()
        end("ok", result={"sha256": text_sha256(serialized), "bytes": len(serialized.encode("utf-8"))})
        if writes or spec.name in _reported_reads():
            # The observers (the chat feed's producer) hear of a call that changed something: who, which tool, the
            # turn's channel and the ids it issued. Emitting never changes or fails the finished call.
            try:
                from orchestrator.Core.job_service import owner_actor
                self.events.emit("call_done", tool=spec.name, actor=ctx.actor, owner=owner_actor(ctx.owner),
                                 channel=getattr(ctx, "channel", None) or None, client=ctx.client or "",
                                 **_done_facts(spec, inp, result))
            except Exception as e:  # noqa: BLE001
                self.logger.warning(f"The call_done of {spec.name} was not reported: {redact_secrets(str(e))}")
        return result

    def _output(self, spec: ToolSpec, result: Any, notes: Optional[List[str]] = None) -> BaseModel:
        """The handler's result as the tool's output model, filtered (secrets redacted, external text cleaned
        and capped), with the framework's notes (repairs first), within the size limit (ResultTooLarge)."""
        model = spec.output_model
        if isinstance(result, dict):
            result = model.model_validate(result)
        elif (isinstance(result, BaseModel) and not isinstance(result, model)
              and issubclass(model, type(result))):  # e.g. BuildStatusOut for its tool model with notes
            result = model.model_validate(result.model_dump())
        if not isinstance(result, model):
            raise TypeError(f"handler returned {type(result).__name__}, expected {model.__name__}")
        notes = list(notes or []) + (list(spec.notes(result)) if spec.notes is not None else [])
        result = filtered_result(result, notes)
        check_size(result, spec.paging_hint, spec.limits.max_result_bytes)
        return result

    # --- resources (design 4.4): read like the read tools, filtered and audited ------------------------------------

    def register_resource(self, spec: ResourceSpec) -> ResourceSpec:
        if spec.uri in self._resources:
            raise ValueError(f"Resource '{spec.uri}' is already registered.")
        if spec.tool:
            if self.get(spec.tool).tier != ToolTier.READ:
                raise ValueError(f"Resource '{spec.uri}' may only mirror a read tool, not '{spec.tool}'.")
        elif spec.read is None:
            raise ValueError(f"Resource '{spec.uri}' needs a read tool or a read function.")
        self._resources[spec.uri] = spec
        return spec

    def refuse_resource(self, uri: Any, ctx: ToolContext, detail: str = "") -> None:
        """Audits a resource read refused before it reached a resource (an unknown URI, another scheme, a parameter
        the SDK rejects, such as `..`), like an unknown tool name: the URI as written, cut and redacted."""
        if self.audit is None:
            return
        shown = redact_secrets("".join(ch if ch.isprintable() else "?" for ch in str(uri))[:64])
        try:
            self.audit.record(ctx.actor, f"read_resource:{shown}", ToolTier.READ.value, Decision.DENY.value,
                              "invalid_input", arguments={"uri": str(uri)[:500]}, detail=detail,
                              extra={"client": ctx.client, "session": ctx.session, "reason_code": "unknown_id"})
        except Exception as e:  # reads and refusals never fail on the audit log
            self.logger.warning(f"Audit write failed: {redact_secrets(str(e))}")

    def _decider(self, spec: ResourceSpec) -> Any:
        """What the policy decides a resource as: the read tool it mirrors, or a read-tier pseudo-tool."""
        return self._tools[spec.tool] if spec.tool else _Surface(spec.audit_name, spec.title)

    def hidden_tools(self, names: Sequence[str], ctx: "ToolContext") -> List[str]:
        """The registered tools among `names` this session is not offered (`callers`, `visible_when`, checked against
        the real actor): a resource that mirrors one, or a prompt that leads to one, is neither listed nor read for it,
        so it never shows what the tool itself would not (SEC-M13)."""
        return [n for n in names if n in self._tools and self.hidden_reason(self._tools[n], ctx)]

    def _refuse_hidden(self, ctx: "ToolContext", surface: Any, title: str, hidden: List[str],
                       arguments: Dict[str, Any]) -> None:
        """Audits and refuses a resource read or prompt get built on a tool this caller is not offered."""
        message = (f"'{title}' is not available to this caller (it uses {', '.join(hidden)}, which is for other "
                   "callers).")
        self._audit(ctx, surface, self.decide(self._tools[hidden[0]], None, ctx), "denied", arguments, detail=message,
                    reason_code="not_supported")
        raise ToolDenied(message, code="not_supported",
                         next="Use only the tools, resources and prompts listed for you; tell the person what you "
                              "wanted to do.")

    def resources(self, caller: Optional[str] = None) -> List[ResourceSpec]:
        """Every resource, or the ones `caller` may read (MCP lists only those, as it hides denied tools; a resource
        that mirrors a tool for other callers is hidden too)."""
        if caller is None:
            return list(self._resources.values())
        policy, section = self._policy_of(caller)
        bare = ToolContext(caller)
        return [r for r in self._resources.values()
                if not self.hidden_tools([r.tool] if r.tool else [], bare)
                and policy.decide_tool(self._decider(r), None, section, owner=caller) != Decision.DENY]

    def _read_rate(self, ctx: ToolContext, surface: Any, decision: Decision, arguments: Dict[str, Any]) -> None:
        """The read rate of the limits table for a resource read or a prompt get, counted together with the read tool
        calls of the same caller (design 4.4: the same rate limit as the read tools). A hit is audited
        (`rate_limited`, reason code `limit_reached`) and raised as LimitHit, like a tool call's."""
        rate = self.limits.read_rate
        if rate is None:
            return
        wait = self.limiter.take(("rate", ctx.actor, "read"), rate.count, rate.per, rate.burst)
        if wait is None:
            return
        message = (f"This client made too many read calls (read tools, resources and prompts together: at most "
                   f"{rate.count} per {rate.per:g} seconds). Try again {wait_text(wait)}.")
        self._audit(ctx, surface, decision, "rate_limited", arguments, detail=message, reason_code="limit_reached")
        raise LimitHit(message, code="limit_reached",
                       next="Wait, then read it again; tell the person if they are waiting for it.")

    def read_resource(self, uri: str, params: Optional[Dict[str, str]], ctx: ToolContext) -> Tuple[str, bool]:
        """Reads the resource `uri` (a registered URI or template) with its parameters for `ctx.actor`. Returns
        (text, external): `external` means the text is wrapped as untrusted data. The caller's permission for the
        mirrored read tool decides (a resource cannot ask, so "ask" refuses with the tool to call instead); the
        result goes through the tool's output filter; every read is audited as `read_resource:<uri>` (fail-open,
        like every read). ResourceUnknown: no such resource, an unknown id or invalid parameters."""
        spec = self._resources.get(uri)
        if spec is None:
            raise ResourceUnknown(f"Unknown resource '{uri}'.", next="List the resources (resources/list).")
        began, params = time.monotonic(), dict(params or {})
        surface, mirrored = _Surface(spec.audit_name, spec.title), self._tools.get(spec.tool)
        decider: Any = mirrored or surface
        inp: Optional[BaseModel] = None
        if mirrored is not None:
            hidden = self.hidden_tools([mirrored.name], ctx)  # a tool for other callers: its resource is too
            if hidden:
                self._refuse_hidden(ctx, surface, spec.title, hidden, params)
            try:
                normalized = normalize_arguments(mirrored, spec.arguments(params))
                ctx = dataclasses.replace(ctx, repairs=list(normalized.repairs), notes=[])
                inp = self.validate_input(mirrored, normalized.arguments)
            except (NormalizeError, ToolInputError) as e:
                self._audit(ctx, surface, self.decide(decider, None, ctx), "invalid_input", params, detail=str(e),
                            reason_code=e.code)
                raise ResourceUnknown(str(e), code=e.code) from None
        decision = self.decide(decider, inp, ctx)
        if decision != Decision.DENY:  # a denied read is refused as denied, not counted
            self._read_rate(ctx, surface, decision, params)
        if decision != Decision.ALLOW:
            asks = decision == Decision.ASK
            self._audit(ctx, surface, decision, "not_confirmed" if asks else "denied", params,
                        reason_code="needs_person" if asks else "permission_denied",
                        answer="unavailable" if asks else "")
            if not asks:
                raise ToolDenied(f"'{spec.title}' is not allowed by your tool permissions.", next=DENIED_HINT)
            raise ToolDenied(f"Reading '{spec.title}' needs the person's confirmation, which reading a resource "
                             "cannot ask for.", code="needs_person",
                             next=(f"Call the tool {spec.tool} instead; it asks the person." if spec.tool
                                   else UNASKED_HINT), steps=() if spec.tool else UNASKED_STEPS)

        def end(outcome: str, detail: str = "", reason_code: str = "", text: str = "") -> None:
            extra = {"result": {"sha256": text_sha256(text), "bytes": len(text.encode("utf-8"))}} if text else {}
            self._audit(ctx, surface, decision, outcome, params, detail=detail, reason_code=reason_code,
                        duration_ms=int((time.monotonic() - began) * 1000), **extra)

        token = _CALL.set((self, ctx.actor))
        try:
            if mirrored is not None:
                raw = mirrored.handler(inp, ctx) if mirrored.takes_context else mirrored.handler(inp)
                result = self._output(mirrored, raw)
                body = json.dumps(result.model_dump(mode="json")).replace("<", "\\u003c")
                external = has_untrusted(mirrored.output_model)
                try:
                    source = spec.uri.format(**params)
                except (KeyError, IndexError, ValueError):
                    source = spec.uri
                text = (wrap_untrusted(body, source=f"resource:{source}", max_len=len(body) + 1) if external
                        else body)
            else:
                text, external = framework_text(spec.read(params)), False
        except ResultTooLarge as e:
            end("error", str(e), "limit_reached")
            raise ToolError(f"{spec.title}: {e}", code="limit_reached") from None
        except ValidationError as e:  # a schema or handler bug, as in _run
            self.logger.error(f"Resource {spec.uri} produced invalid data: {e}", exc_info=True)
            end("error", repr(e), "failed")
            raise ToolError(f"{spec.title} failed (internal error; details are in the framework log).") from None
        except (ToolError, ValueError, LookupError) as e:  # an unknown id, or a refusal with its own code
            code = getattr(e, "code", None) or "failed"
            message = external_error_text(getattr(e, "message", None) or str(e))
            next_step = redact_secrets(getattr(e, "next", "") or "")
            end("error", message, code)
            if code in ("unknown_id", "invalid_value") or isinstance(e, LookupError):
                raise ResourceUnknown(message, code="unknown_id", next=next_step) from None
            raise ToolError(message, code=code, next=next_step, quoted=message) from None
        except Exception as e:
            self.logger.error(f"Resource {spec.uri} failed: {e}", exc_info=True)
            end("error", repr(e), "failed")
            message = external_error_text(str(e))
            raise ToolError(f"{spec.title} failed: {message}", quoted=message) from None
        finally:
            _CALL.reset(token)
        end("ok", text=text)
        return text, external

    # --- prompts (design 4.3, 4.4): read like a read tool, filtered and audited ----------------------------------------

    def register_prompt(self, spec: PromptSpec) -> PromptSpec:
        if spec.name in self._prompts:
            raise ValueError(f"Prompt '{spec.name}' is already registered.")
        names = [a.name for a in spec.arguments]
        if len(names) != len(set(names)):
            raise ValueError(f"Prompt '{spec.name}' declares an argument twice.")
        if not set(spec.reads) <= set(spec.tools):
            raise ValueError(f"Prompt '{spec.name}' reads tools it does not lead to: "
                             f"{sorted(set(spec.reads) - set(spec.tools))}.")
        self._prompts[spec.name] = spec
        return spec

    def missing_tools(self, spec: PromptSpec) -> List[str]:
        """The tools a prompt leads to that are not registered (then it is not offered)."""
        return [t for t in spec.tools if t not in self._tools]

    def prompts(self, caller: Optional[str] = None) -> List[PromptSpec]:
        """Every prompt, or the ones offered to `caller`: every tool it leads to is registered, its read permission
        allows it (pseudo-tool get_prompt:<name>, read tier), and no read tool whose data it holds is denied."""
        if caller is None:
            return list(self._prompts.values())
        policy, section = self._policy_of(caller)
        bare = ToolContext(caller)

        def offered(p: PromptSpec) -> bool:
            if self.missing_tools(p) or self.hidden_tools(p.tools, bare):  # it leads to a tool for other callers
                return False
            deciders = [_Surface(p.audit_name, p.title)] + [self._tools[t] for t in p.reads]
            return all(policy.decide_tool(d, None, section, owner=caller) != Decision.DENY for d in deciders)
        return [p for p in self._prompts.values() if offered(p)]

    def _prompt_decision(self, spec: PromptSpec, ctx: ToolContext) -> Tuple[Decision, str]:
        """(the decision for getting a prompt, the read tool that decided it or ""): the prompt's own read
        permission and that of every read tool whose data its context holds; deny before ask before allow."""
        decision, holder = self.decide(_Surface(spec.audit_name, spec.title), None, ctx), ""
        for tool in spec.reads:
            if decision == Decision.DENY:
                break
            read = self.decide(self._tools[tool], None, ctx)
            if read == Decision.DENY or (read == Decision.ASK and decision == Decision.ALLOW):
                decision, holder = read, tool
        return decision, holder

    def get_prompt(self, name: Any, arguments: Any, ctx: ToolContext) -> Tuple[str, List[str]]:
        """A prompt for `ctx.actor`: (description, [instructions, context]). The arguments are checked against the
        prompt's own (an unknown one is refused with the valid names, never dropped); the read permission decides;
        the context is built by the prompt (external text in untrusted blocks), secrets are redacted and the whole is
        capped at MAX_PROMPT_CHARS; every get is audited as `get_prompt:<name>`. ResourceUnknown (invalid params):
        no such prompt, a tool it needs is missing, invalid arguments or an unknown id."""
        spec = self._prompts.get(name) if isinstance(name, str) else None
        if spec is None:
            from orchestrator.tools.normalize import suggest
            close = suggest(str(name), [p.name for p in self.prompts(ctx.actor)])
            shown = redact_secrets("".join(ch if ch.isprintable() else "?" for ch in str(name))[:64])
            raise ResourceUnknown(f"Unknown prompt '{shown}'." + (f" Did you mean '{close[0]}'?" if close else ""),
                                  next="List the prompts (prompts/list).", suggestions=close)
        began, surface = time.monotonic(), _Surface(spec.audit_name, spec.title)
        raw = dict(arguments) if isinstance(arguments, dict) else {}
        missing = self.missing_tools(spec)
        if missing:
            message = (f"The prompt '{spec.name}' leads to the tool {', '.join(missing)}, which this framework "
                       "version does not have yet.")
            self._audit(ctx, surface, self.decide(surface, None, ctx), "error", raw, detail=message,
                        reason_code="no_tool")
            raise ResourceUnknown(message, code="no_tool", next="Do the task with the tools that are listed, or tell "
                                                                "the user which tool is missing.")
        hidden = self.hidden_tools(spec.tools, ctx)  # it leads to (or holds what returns) a tool for other callers
        if hidden:
            self._refuse_hidden(ctx, surface, spec.title, hidden, raw)
        known = {a.name: a for a in spec.arguments}
        args: Dict[str, str] = {}
        problems: List[str] = []
        for key, value in raw.items():
            if key not in known:
                from orchestrator.tools.normalize import suggest
                close = suggest(str(key), list(known))
                shown = redact_secrets("".join(ch if ch.isprintable() else "?" for ch in str(key))[:64])
                problems.append(f"unknown argument '{shown}'" + (f" (did you mean '{close[0]}'?)" if close else ""))
            elif value is None:
                continue
            elif not isinstance(value, str):
                problems.append(f"'{key}' must be text")
            elif len(value) > known[key].max_chars:
                problems.append(f"'{key}' is longer than {known[key].max_chars} characters")
            elif value.strip():
                args[key] = value.strip()
        problems += [f"'{a.name}' is required" for a in spec.arguments if a.required and a.name not in args]
        decision, holder = self._prompt_decision(spec, ctx)
        if problems:
            message = (f"Invalid arguments for the prompt {spec.name}: {'; '.join(problems)}. Valid arguments: "
                       f"{', '.join(known) or 'none'}.")
            self._audit(ctx, surface, decision, "invalid_input", raw, detail=message, reason_code="invalid_value")
            raise ResourceUnknown(message, code="invalid_value", next=f"Get the prompt {spec.name} again with these "
                                                                     "arguments fixed.")
        if decision != Decision.DENY:  # a denied get is refused as denied, not counted
            self._read_rate(ctx, surface, decision, args)
        if decision != Decision.ALLOW:
            asks = decision == Decision.ASK
            self._audit(ctx, surface, decision, "not_confirmed" if asks else "denied", args,
                        reason_code="needs_person" if asks else "permission_denied",
                        answer="unavailable" if asks else "")
            if asks and holder:
                raise ToolDenied(f"The prompt '{spec.title}' holds what {holder} returns, which needs the person's "
                                 "confirmation; getting a prompt cannot ask for it.", code="needs_person",
                                 next=f"Call the tool {holder} instead; it asks the person.")
            if asks:
                raise ToolDenied(f"The prompt '{spec.title}' needs the person's confirmation, which getting a prompt "
                                 "cannot ask for.", code="needs_person", next=UNASKED_HINT, steps=UNASKED_STEPS)
            if holder:
                raise ToolDenied(f"The prompt '{spec.title}' holds what {holder} returns, which your tool permissions "
                                 "do not allow.", next=DENIED_HINT)
            raise ToolDenied(f"The prompt '{spec.title}' is not allowed by your tool permissions.", next=DENIED_HINT)

        def end(outcome: str, detail: str = "", reason_code: str = "", text: str = "") -> None:
            extra = {"result": {"sha256": text_sha256(text), "bytes": len(text.encode("utf-8"))}} if text else {}
            self._audit(ctx, surface, decision, outcome, args, detail=detail, reason_code=reason_code,
                        duration_ms=int((time.monotonic() - began) * 1000), **extra)

        token = _CALL.set((self, ctx.actor))
        try:
            context = redact_secrets(str(spec.render(dict(args), ctx) or ""))
        except (ToolError, ValueError, LookupError) as e:
            code = getattr(e, "code", None) or "failed"
            message = external_error_text(getattr(e, "message", None) or str(e))
            next_step = redact_secrets(getattr(e, "next", "") or "")
            end("error", message, code)
            if code in ("unknown_id", "invalid_value") or isinstance(e, LookupError):
                raise ResourceUnknown(message, code="unknown_id", next=next_step) from None
            raise ToolError(message, code=code, next=next_step, quoted=message) from None
        except Exception as e:
            self.logger.error(f"Prompt {spec.name} failed: {e}", exc_info=True)
            end("error", repr(e), "failed")
            message = external_error_text(str(e))
            raise ToolError(f"{spec.title} failed: {message}", quoted=message) from None
        finally:
            _CALL.reset(token)
        room = MAX_PROMPT_CHARS - len(spec.instructions)
        if len(context) > room:
            context = context[:max(0, room - 40)] + "\n[... truncated at the prompt limit ...]"
        end("ok", text=spec.instructions + context)
        return spec.description, [spec.instructions, context]

    def complete(self, kind: str, ref: str, argument: str, value: str, context: Optional[Dict[str, str]],
                 ctx: ToolContext) -> List[str]:
        """Completion values for a prompt argument (kind "prompt") or a resource template parameter ("resource"):
        only for a prompt or resource this caller is offered, filtered by what was typed (any letter case), at most
        MAX_COMPLETIONS. A provider that fails gives nothing."""
        if kind == "prompt":
            spec: Any = self._prompts.get(ref)
            offered = spec is not None and spec.name in {p.name for p in self.prompts(ctx.actor)}
        else:
            spec = self._resources.get(ref)
            offered = spec is not None and spec.uri in {r.uri for r in self.resources(ctx.actor)}
        provider = spec.completions.get(argument) if offered else None
        if provider is None:
            return []
        token = _CALL.set((self, ctx.actor))
        try:
            values = provider(dict(context or {}), ctx)
        except Exception:  # noqa: BLE001 - completions are hints
            return []
        finally:
            _CALL.reset(token)
        typed = (value or "").lower()
        return [v for v in dict.fromkeys(str(x) for x in values or []) if v.lower().startswith(typed)][:MAX_COMPLETIONS]


def framework_text(text: Any, limit: int = MAX_RESULT_BYTES) -> str:
    """Text the framework wrote itself (its documents): secrets redacted, cut at `limit` bytes with a note."""
    text = redact_secrets(str(text or ""))
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text
    return data[:limit].decode("utf-8", "ignore") + "\n[... truncated ...]"
