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
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Tuple, Type, Union

from pydantic import BaseModel, ConfigDict, ValidationError

from orchestrator.tools.audit import AuditLog
from orchestrator.tools.limits import Limiter, wait_text
from orchestrator.tools.confirmation import MAX_FRAMEWORK, CallTooLong, describe_call
from orchestrator.tools.normalize import (NormalizeError, Repair, check_names_distinct, list_item_literal,
                                          normalize_arguments, resolve_name)
from orchestrator.tools.output import UNTRUSTED_CHARS, ResultTooLarge, check_size, filtered_result
from orchestrator.tools.mcp_tokens import ACTOR_PREFIX, TokenStore, TokenStoreError
from orchestrator.tools.policy import (TIER_CAPS, Decision, DecisionRule, PolicyStore, ToolPolicy, ToolTier,
                                       restrictive_policy, stricter)
from orchestrator.tools.spec import (CHANNELS, DAY, LIMITS, CallerLimits, CallFacts, FieldMeta, GrantRule,
                                     ToolLimits, example_problems)
from orchestrator.utils.helpers import redact_secrets
from orchestrator.utils.logging import get_logger
from orchestrator.utils.untrusted import sanitize_untrusted


def text_sha256(text: str) -> str:
    """The hash an audit entry records for a question or a result (never the text itself)."""
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


class ToolError(Exception):
    """A tool call failed; the message is safe to show to the user and to the model.

    `code` is a reason code (normalize.CODES: unknown_tool, invalid_value, permission_denied, declined, failed,
    ...), `next` the next step; the text a model sees is "<message> Next: <next>". `quoted` is the end of the
    message that quotes external text (a handler's error can carry a server reply, a log line or a name from a
    pack): it is cleaned like external text, and an MCP client gets it marked as untrusted data. `field` names the
    argument an input error is about (Ditto's loop tells a fix from a repeat by it)."""
    code = "failed"
    quoted = ""
    field = ""

    def __init__(self, message: str = "", code: Optional[str] = None, next: str = "",
                 allowed: Optional[List[str]] = None, suggestions: Optional[List[str]] = None, quoted: str = "",
                 field: str = ""):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.next = next
        self.allowed = list(allowed or [])
        self.suggestions = list(suggestions or [])
        self.quoted = quoted if quoted and message.endswith(quoted) else ""
        if field:
            self.field = field

    def __str__(self) -> str:
        return f"{self.message} Next: {self.next}" if self.next else self.message

    def with_text(self, message: str, next: Optional[str] = None) -> "ToolError":
        """The same kind of error with the same code and another text."""
        try:
            copy = type(self)(message)
        except Exception:  # a subclass with its own constructor
            copy = ToolError(message)
        copy.code, copy.next = self.code, self.next if next is None else next
        copy.allowed, copy.suggestions = list(self.allowed), list(self.suggestions)
        copy.field = self.field
        copy.quoted = self.quoted if self.quoted and message.endswith(self.quoted) else ""
        return copy


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


def _with_message(error: ToolError, message: str) -> ToolError:
    """The same kind of error (denied, invalid input, ...) with another message."""
    return error.with_text(message)


# The person's own setting: the next step names where they decide, not a command for the AI to suggest.
DENIED_HINT = ("Tell the person it is not allowed by their tool permissions; only they can change that, in Tool "
               "Permissions. get_status shows why.")
# Nobody could be asked: the person can allow the tool, or use a client that can ask.
UNASKED_HINT = ("The person can allow it with 'llm-cli tools permissions' or in the GUI (Tools > Tool Permissions "
                "(Ditto / MCP)), or use a client that can ask them.")
REPEAT_WARN = "This exact call already failed. "
# The next step of a failure nobody can fix by changing the call (the framework's or a service's own problem).
FAILED_NEXT = ("Tell the person what failed and why; 'llm-cli doctor' and the framework log show the cause. Do not "
               "retry the same call unchanged.")
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


Confirm = Callable[[ToolSpec, BaseModel], bool]

# The registry and actor of the call a handler runs for (handlers do not get the context; get_status reads it).
_CALL: contextvars.ContextVar = contextvars.ContextVar("lcf_tool_call", default=(None, ""))
# The question the registry asks through a framework channel (Ditto's dialog or prompt shows exactly this text).
_QUESTION: contextvars.ContextVar = contextvars.ContextVar("lcf_tool_question", default="")


def current_question() -> str:
    return _QUESTION.get()


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
    # The facts the tool's prepare looked up for this call (the MCP server hands in the ones its question showed);
    # the handler must use exactly these. New for every call.
    facts: Optional[CallFacts] = None
    # Progress for the caller while the handler runs: report_progress(done, total, message); None: nobody listens.
    report_progress: Optional[Callable[..., Any]] = None
    # The MCP server counted this call against the limits already (before its question); call() does not again.
    limits_checked: bool = False

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
        # The registry-wide rows of the limits table and the per-client counters (rates, quotas, questions, "no").
        self.limits = limits
        self.limiter = Limiter(clock or time.monotonic)
        self._tools: Dict[str, ToolSpec] = {}
        # Repeat guard and per-session counters (in memory; per caller and session).
        self._guard_lock = threading.Lock()
        self._failures: "OrderedDict[tuple, Dict[str, Any]]" = OrderedDict()
        self._sessions: "OrderedDict[tuple, Dict[str, int]]" = OrderedDict()

    def register(self, spec: ToolSpec) -> ToolSpec:
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
        unknown or revoked token; for ditto and mcp their section, or the shared permissions."""
        if not actor:
            return self.policy_store.load()
        policy, section = self._policy_of(actor)
        if not section:  # a token's own policy, or one that denies everything
            return policy
        own = policy.callers.get(section)
        if own is None and section.startswith(ACTOR_PREFIX):  # a token without permissions of its own
            from orchestrator.tools.mcp_tokens import read_only_policy
            return read_only_policy()
        source = own if own is not None else policy
        return ToolPolicy(configured=policy.configured, tiers=dict(source.tiers), overrides=dict(source.overrides),
                          reviewed=list(source.reviewed) if source.reviewed is not None else None)

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

    def visible(self, caller: str) -> List[ToolSpec]:
        """The tools a caller is offered: every tool its permissions do not deny (MCP tools/list, Ditto's tool
        definitions). Denied tools stay registered; a call to one is refused and audited."""
        policy, section = self._policy_of(caller)
        return [s for s in self._tools.values()
                if policy.decide_tool(s, None, section, owner=caller) != Decision.DENY]

    def permissions(self, caller: str) -> List[Dict[str, Any]]:
        """Per tool, for one caller: decision and why, default, cap, listed or hidden, new (not reviewed yet), and
        whether a confirmation may offer a standing approval."""
        policy, section = self._policy_of(caller)
        rows = []
        for spec in self._tools.values():
            decision, why = policy.explain_tool(spec, None, section, owner=caller)
            default = spec.default_decision
            rows.append({
                "tool": spec.name, "title": spec.title, "tier": spec.tier.value, "decision": decision.value,
                "why": why, "default": ("depends on the call" if callable(default) else
                                        default.value if default is not None else "tier setting"),
                "max": ("depends on the call" if callable(spec.max_decision) else spec.cap().value),
                "listed": decision != Decision.DENY, "new": not policy.reviewed_tool(section, spec.name),
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
        if ctx.repairs:
            fields.setdefault("repairs", [r.audit() for r in ctx.repairs])
            renamed = next((r for r in ctx.repairs if r.kind == "name"), None)
            if renamed is not None:
                fields.setdefault("name_requested", str(renamed.wrote)[:64])
        try:
            return self.audit.record(ctx.actor, spec.name, spec.tier.value, decision.value, outcome,
                                     arguments=arguments, approved=approved, detail=detail, extra=fields)
        except Exception as e:  # auditing must never break a read; a change is refused instead
            reason = redact_secrets(str(e)) or type(e).__name__
            if required:
                self.logger.error(f"Audit write failed, '{spec.name}' refused: {reason}")
                raise AuditUnavailable(
                    f"The audit log cannot be written ({reason}), so '{spec.title}' was not run: calls that change "
                    "something run only when they are recorded. Run 'llm-cli doctor' to find the cause.") from None
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
        return ToolInputError(" ".join(parts), code="missing_argument" if missing and not problems else
                              "invalid_value", next=next_step, allowed=allowed_all)

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

        def decision() -> Decision:  # only for the audit entry of a refusal: a successful prepare decides nothing
            return self.decide(spec, caller=ctx.actor)

        def refuse_repeat(args: Any) -> None:
            refusal = self._repeat_refusal(work, spec.name, args)
            if refusal is not None:
                self._audit(work, spec, decision(), "repeat_refused", args, detail=refusal.message,
                            reason_code="repeat_refused")
                raise refusal
        refuse_repeat(arguments)  # the same raw call (e.g. the same unknown argument) a third time
        try:
            normalized = normalize_arguments(spec, arguments)
        except NormalizeError as e:
            error = ToolInputError(str(e), code=e.code, next=e.next, allowed=e.allowed, suggestions=e.suggestions)
            raise self._input_failed(work, spec, decision, arguments, error) from None
        work.repairs = _merge(repairs, normalized.repairs)
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

    def enforce_limits(self, prepared: Prepared, ctx: ToolContext) -> None:
        """The rate and quotas of the limits table for this caller (design 6.5), checked after validation and before
        the policy and any question: the read rate (read tools together) or the tool's own rate, its unfinished runs
        (start_build: 3 builds per client) and its runs per day (start_build: 20). A limit hit is audited
        (`rate_limited`, reason code `limit_reached`) and says when to try again; it never counts as a failed call
        for the repeat guard."""
        spec, limits = prepared.spec, prepared.spec.limits
        problem: Optional[Tuple[str, Optional[float]]] = None
        rate = limits.rate or (self.limits.read_rate if spec.tier == ToolTier.READ else None)
        if rate is not None:
            wait = self.limiter.take(("rate", ctx.actor, spec.name if limits.rate else "read"), rate.count,
                                     rate.per, rate.burst)
            if wait is not None:
                noun = f"calls of '{spec.title}'" if limits.rate else "read tool calls"
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
        error = ToolError(message, code="limit_reached",
                          next="Wait, then call it again; tell the person if they are waiting for it.")
        self._audit(ctx, spec, self.decide(spec, caller=ctx.actor), "rate_limited", prepared.arguments,
                    detail=message, reason_code="limit_reached")
        raise error

    def question_refusal(self, spec: ToolSpec, inp: Optional[BaseModel], ctx: ToolContext) -> Optional[ToolError]:
        """Why a question about this call must not be put to the person now (design 6.5, U8), or None: they said no
        to this exact call (same tool, same normalized arguments, so a respelled call counts as the same) less than
        10 minutes ago; they said no to this tool three times within 30 minutes (its questions pause for 30
        minutes); or this client already asked 30 questions in the last hour."""
        limits = self.limits
        key = _args_key(inp.model_dump(mode="json")) if inp is not None else ""
        number, since, _ = self.limiter.count(("no", ctx.actor, spec.name, key), limits.decline_cooldown)
        if number:
            return ToolError(f"The person declined this exact call of '{spec.title}' {_minutes(since)} ago; it is not "
                             f"asked again for another {_minutes(limits.decline_cooldown - since)}.", code="declined",
                             next="Do not retry it unless the person asks for it; tell them what you wanted to do.")
        number, since, _ = self.limiter.count(("pause", ctx.actor, spec.name), limits.decline_pause)
        if number:
            return ToolError(f"The person declined '{spec.title}' {limits.declines_to_pause} times within "
                             f"{_minutes(limits.decline_window)}; questions about it are paused for another "
                             f"{_minutes(limits.decline_pause - since)}.", code="declined",
                             next="Do not call it again now; ask the person in your answer what they want instead.")
        wait = self.limiter.full(("questions", ctx.actor), limits.questions_per_hour, 3600.0)
        if wait is not None:
            return ToolError(f"This client already asked the person {limits.questions_per_hour} questions in the last "
                             f"hour, the most allowed. Try again {wait_text(wait)}.", code="limit_reached",
                             next="Tell the person what you would like to do instead of asking again now.")
        return None

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
        "do not guess ids". A pending approval is not a failure."""
        if error.code == "waiting_for_approval":
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
                        question: str) -> str:
        """Records that a question about a call is put to the person (before it is asked) and returns the hash of
        its text. For a call that changes something the question is not asked when it cannot be recorded
        (AuditUnavailable)."""
        spec = self.get(name)
        digest = text_sha256(question)
        self._audit(ctx, spec, self.decide(spec, caller=ctx.actor), "confirmation_requested", arguments,
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
                      facts: Optional[CallFacts] = None, limit: int = MAX_FRAMEWORK) -> str:
        """The question for one call (confirmation text v2): the consequence and the facts the tool looked up, who
        asks, every argument with its speaking name and the repaired values marked. CallTooLong above `limit`
        (4,000 characters for an MCP elicitation, 16,000 for the framework's own windows)."""
        marks, renamed = repair_marks(ctx.repairs)
        return describe_call(spec, arguments, actor="Ditto" if ctx.actor == "ditto" else "The AI client",
                             marks=marks, renamed=renamed, requester=requester_of(ctx), facts=facts,
                             names=self.speaking_names(spec, arguments), limit=limit)

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
            error.quoted = message
            raise error from None
        except ValueError as e:  # a service's refusal (BuildServiceError carries code and next)
            message = external_error_text(str(e))
            raise ToolError(message, code=getattr(e, "code", None) or "failed",
                            next=redact_secrets(getattr(e, "next", "") or "") or FAILED_NEXT, quoted=message) from e
        except Exception as e:
            self.logger.error(f"Looking up the facts for {spec.name} failed: {e}", exc_info=True)
            message = external_error_text(str(e))
            raise ToolError(f"{spec.title} could not be prepared: {message}", next=FAILED_NEXT, quoted=message) from e
        if not isinstance(facts, CallFacts):
            raise ToolError(f"{spec.title} could not be prepared (internal error; details are in the framework log).",
                            next=FAILED_NEXT)
        return facts

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
        prepared = self.prepare(name, arguments, ctx)
        if not ctx.limits_checked:  # the MCP server counts a call before its question
            self.enforce_limits(prepared, dataclasses.replace(ctx, repairs=prepared.repairs))
        spec, inp, arguments = prepared.spec, prepared.input, prepared.arguments
        # per call: Ditto reuses one context
        ctx = dataclasses.replace(ctx, repairs=prepared.repairs, notes=[])
        try:
            result = self._run(spec, inp, arguments, ctx, approved, began)
        except ToolError as e:
            error = self._failed(ctx, spec.name, prepared.key, e)
            if error is e:
                raise
            raise error from None
        self._succeeded(ctx, spec.name, prepared.key)
        return result

    def _run(self, spec: ToolSpec, inp: BaseModel, arguments: Dict[str, Any], ctx: ToolContext,
             approved: Optional[bool], began: float) -> BaseModel:
        decision = self.decide(spec, inp, ctx)
        if decision == Decision.DENY:
            self._audit(ctx, spec, decision, "denied", arguments, reason_code="permission_denied")
            raise ToolDenied(f"'{spec.title}' is not allowed by your tool permissions.", next=DENIED_HINT)
        # Two-phase: the facts only the server knows, looked up before the question (the MCP server hands in the
        # ones its question showed). They can make an allowed call ask, and the handler is bound to them.
        if ctx.facts is None and spec.prepare is not None:
            try:
                ctx.facts = self.look_up(spec, inp, ctx)
            except ToolError as e:
                self._audit(ctx, spec, decision, "error", arguments, detail=f"{e.message} {e.next}".strip(),
                            reason_code=e.code)
                raise
        decision = self.capped(decision, ctx.facts)
        if ctx.metadata.get("grant_requested"):
            ctx.notes.append(GRANT_REQUEST_NOTE)
        writes = spec.tier != ToolTier.READ
        channel, answer, question = ctx.confirm_channel, ctx.answer, ctx.question_sha256
        # A person's "no" is final, even if the policy was loosened to "allow" while they were asked.
        if decision == Decision.ASK or approved is False:
            if approved is None and ctx.confirm is not None:
                channel = getattr(ctx.confirm, "confirm_channel", "framework")
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
                    raise ToolDenied(str(e), code="limit_reached",
                                     next="Make the call smaller (fewer or shorter values), or tell the person why "
                                          "it cannot be confirmed.") from None
                question = text_sha256(text)
                # Recorded before it is asked; a question about a change that cannot be recorded is not asked.
                self._audit(ctx, spec, decision, "confirmation_requested", arguments, required=writes,
                            confirm_channel=channel, question_sha256=question)
                self.asked(ctx)
                token = _QUESTION.set(text)
                try:
                    approved = bool(ctx.confirm(spec, inp))
                finally:
                    _QUESTION.reset(token)
                answer = "accepted" if approved else "declined"
            if not approved:
                # approved stays None when nobody could be asked; False is a person's "no"
                code = "declined" if approved is False else "needs_person"
                if approved is False and (answer or "declined") == "declined":  # not a dismissed dialog
                    self.declined(spec, inp, ctx)
                self._audit(ctx, spec, decision, "not_confirmed", arguments, approved=approved,
                            confirm_channel=channel or "none", question_sha256=question,
                            answer=answer or ("declined" if approved is False else "unavailable"), reason_code=code)
                raise ToolDenied(f"'{spec.title}' needs your confirmation, which was not given.", code=code,
                                 next=("Do not retry it unless the person asks for it." if code == "declined" else
                                       "Nobody could be asked. " + UNASKED_HINT))
        confirmation = {"confirm_channel": channel or "none", "question_sha256": question, "answer": answer}

        started = None
        if writes:  # write-ahead: a crash in the middle of a side effect still leaves a record
            started = self._audit(ctx, spec, decision, "started", arguments, approved=approved, required=True,
                                  **confirmation)
        links = [started] if started else []
        if ctx.cancel.is_set():  # the caller gave up before the handler started
            self._audit(ctx, spec, decision, "cancelled", arguments, approved=approved, links=links,
                        detail="Cancelled before it started.", reason_code="failed", **confirmation)
            raise ToolError(f"'{spec.title}' was cancelled before it started.")
        if spec.limits.per_day:  # a run counts toward the tool's quota per day once it really starts
            self.limiter.add(("day", ctx.actor, spec.name), DAY)

        def end(outcome: str, detail: str = "", result: Any = None, reason_code: str = "") -> None:
            if ctx.cancel.is_set():
                detail = ("The client cancelled the call while it ran; this is what it did. " + detail).strip()
            self._audit(ctx, spec, decision, outcome, arguments, approved=approved, detail=detail,
                        duration_ms=int((time.monotonic() - began) * 1000), links=links, result=result,
                        reason_code=reason_code, **confirmation)

        token = _CALL.set((self, ctx.actor))
        try:
            raw = spec.handler(inp, ctx) if spec.takes_context else spec.handler(inp)
            result = self._output(spec, raw, [r.note(spec.name) for r in ctx.repairs] + list(ctx.notes))
        except ResultTooLarge as e:
            end("error", str(e), reason_code="limit_reached")
            raise ToolError(f"{spec.title}: {e}", code="limit_reached") from None
        except ToolError as e:  # raised by the handler: its message can quote external text
            message, next_step = external_error_text(e.message), redact_secrets(e.next)
            end("error", f"{message} {next_step}".strip(), reason_code=e.code)
            error = e if (message, next_step) == (e.message, e.next) else e.with_text(message, next_step)
            error.quoted = message
            if error is e:
                raise
            raise error from None  # the original still holds the secret
        except ValidationError as e:  # a schema or handler bug (output check, models built inside the handler)
            self.logger.error(f"Tool {spec.name} produced invalid data: {e}", exc_info=True)
            end("error", repr(e), reason_code="failed")
            raise ToolError(f"{spec.title} failed (internal error; details are in the framework log).") from e
        except ValueError as e:  # user-facing validation problems (e.g. BuildServiceError, with its own code)
            code = getattr(e, "code", None) or "failed"
            end("error", str(e), reason_code=code)
            message = external_error_text(str(e))
            raise ToolError(message, code=code, next=redact_secrets(getattr(e, "next", "") or ""),
                            quoted=message) from e
        except Exception as e:
            self.logger.error(f"Tool {spec.name} failed: {e}", exc_info=True)
            end("error", repr(e), reason_code="failed")
            message = external_error_text(str(e))
            raise ToolError(f"{spec.title} failed: {message}", quoted=message) from e
        except BaseException:  # interrupted in the calling thread (KeyboardInterrupt, shutdown): still recorded
            end("cancelled", "Interrupted while it ran.")
            raise
        finally:
            _CALL.reset(token)
        serialized = result.model_dump_json()
        end("ok", result={"sha256": text_sha256(serialized), "bytes": len(serialized.encode("utf-8"))})
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
