#!/usr/bin/env python3
"""
One tool registry for Ditto and the MCP server.

A tool has typed input and output (pydantic), a risk tier and a handler. Every call goes through
ToolRegistry.call(): name resolution and argument normalization (the fool-proof layer, normalize.py), input
validation, the repeat guard, the user's stored permission policy, an optional confirmation, the handler with an
output check, and an audit entry - the same path for every caller. Every refusal and error carries a reason code
and, where there is one, the next step.
"""

import contextvars
import dataclasses
import hashlib
import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, Type

from pydantic import BaseModel, ValidationError

from orchestrator.tools.audit import AuditLog
from orchestrator.tools.confirmation import CallTooLong, describe_call
from orchestrator.tools.normalize import (NormalizeError, Repair, check_names_distinct, list_item_literal,
                                          normalize_arguments, resolve_name)
from orchestrator.tools.output import UNTRUSTED_CHARS, ResultTooLarge, check_size, filtered_result
from orchestrator.tools.policy import TIER_CAPS, Decision, DecisionRule, PolicyStore, ToolPolicy, ToolTier
from orchestrator.tools.spec import CHANNELS, FieldMeta, GrantRule, ToolLimits, example_problems
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
    pack): it is cleaned like external text, and an MCP client gets it marked as untrusted data."""
    code = "failed"
    quoted = ""

    def __init__(self, message: str = "", code: Optional[str] = None, next: str = "",
                 allowed: Optional[List[str]] = None, suggestions: Optional[List[str]] = None, quoted: str = ""):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.next = next
        self.allowed = list(allowed or [])
        self.suggestions = list(suggestions or [])
        self.quoted = quoted if quoted and message.endswith(quoted) else ""

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
    # (input, caller). None: the tier decision. A default never opens a tier the person denied.
    default_decision: DecisionRule = None
    # The highest decision any setting may give this tool; a Decision or a function of (input, caller).
    # None: the tier's cap (TIER_CAPS: tools that reach other devices or delete data always ask).
    max_decision: DecisionRule = None
    channels: Tuple[str, ...] = CHANNELS  # confirmation channels the tool accepts, strongest first
    grant: Optional[GrantRule] = None     # None: a confirmation never offers "do not ask again"
    limits: ToolLimits = field(default_factory=ToolLimits)
    arg_aliases: Mapping[str, str] = field(default_factory=dict)  # declared argument aliases (alias -> field)
    examples: Tuple[Dict[str, Any], ...] = ()                     # valid example calls, shown after input errors
    fields: Mapping[str, FieldMeta] = field(default_factory=dict)  # how the fool-proof layer treats each field

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
    actor: str                       # "ditto", "mcp", "cli", "gui"
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


@dataclass
class Prepared:
    """A call after name resolution, normalization and validation."""
    spec: "ToolSpec"
    arguments: Dict[str, Any]
    input: BaseModel
    repairs: List[Repair]


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


class ToolRegistry:
    def __init__(self, policy_store: PolicyStore, audit: Optional[AuditLog] = None):
        self.logger = get_logger("ToolRegistry")
        self.policy_store = policy_store
        self.audit = audit
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

    def policy(self) -> ToolPolicy:
        return self.policy_store.load()

    def decide(self, spec: ToolSpec, inp: Any = None, caller: str = "") -> Decision:
        """The decision for a call of `spec` by `caller` (ditto, mcp, mcp:<token>): the caller's own permissions
        (or the shared ones), the tool's default for tools the person has not reviewed, and the tool's cap."""
        return self.policy().decide_tool(spec, inp, caller)

    def visible(self, caller: str) -> List[ToolSpec]:
        """The tools a caller is offered: every tool its permissions do not deny (MCP tools/list, Ditto's tool
        definitions). Denied tools stay registered; a call to one is refused and audited."""
        policy = self.policy()
        return [s for s in self._tools.values() if policy.decide_tool(s, None, caller) != Decision.DENY]

    def permissions(self, caller: str) -> List[Dict[str, Any]]:
        """Per tool, for one caller: decision and why, default, cap, listed or hidden, new (not reviewed yet), and
        whether a confirmation may offer a standing approval."""
        policy = self.policy()
        rows = []
        for spec in self._tools.values():
            decision, why = policy.explain_tool(spec, None, caller)
            default = spec.default_decision
            rows.append({
                "tool": spec.name, "title": spec.title, "tier": spec.tier.value, "decision": decision.value,
                "why": why, "default": ("depends on the call" if callable(default) else
                                        default.value if default is not None else "tier setting"),
                "max": ("depends on the call" if callable(spec.max_decision) else spec.cap().value),
                "listed": decision != Decision.DENY, "new": not policy.reviewed_tool(caller, spec.name),
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
               outcome: str = "not_confirmed", detail: str = "", **extra: Any) -> None:
        """Audits a call that was refused, cancelled or left unanswered outside call() (e.g. because it could
        not be confirmed). Nobody approved it, so `approved` stays NULL."""
        spec = self.get(name)
        self._audit(ctx, spec, self.decide(spec, caller=ctx.actor), outcome, arguments, detail=detail, **extra)

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
        return Prepared(spec, args, inp, work.repairs)

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
        with self._guard_lock:
            record = self._failures.pop(key, None)
            if record is None or record["code"] != error.code:
                record = {"code": error.code, "count": 0, "message": error.message, "quoted": error.quoted}
            record["count"] += 1
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
        """The refusal for a call that already failed twice in this session, None otherwise."""
        key = (ctx.actor, ctx.session, tool, _args_key(arguments))
        with self._guard_lock:
            record = self._failures.get(key)
        if record is None or record["count"] < 2:
            return None
        reason = record["message"][len(REPEAT_WARN):] if record["message"].startswith(REPEAT_WARN) else \
            record["message"]
        return ToolError(f"Stop retrying this call. Tell the user why it failed: {reason}", code="repeat_refused",
                         quoted=record.get("quoted", ""))

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
        return digest

    @staticmethod
    def question_for(spec: ToolSpec, inp: BaseModel, ctx: ToolContext) -> str:
        """The question the framework's own channel shows (Ditto's dialog or prompt), with the values the fool-proof
        layer repaired marked; "" when it is too long to show, in which case that channel refuses the call."""
        marks, renamed = repair_marks(ctx.repairs)
        try:
            return describe_call(spec, inp.model_dump(mode="json"),
                                 actor="Ditto" if ctx.actor == "ditto" else "The AI client", marks=marks,
                                 renamed=renamed)
        except CallTooLong:
            return ""

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
        spec, inp, arguments = prepared.spec, prepared.input, prepared.arguments
        ctx = dataclasses.replace(ctx, repairs=prepared.repairs)  # per call: Ditto reuses one context
        try:
            result = self._run(spec, inp, arguments, ctx, approved, began)
        except ToolError as e:
            error = self._failed(ctx, spec.name, arguments, e)
            if error is e:
                raise
            raise error from None
        self._succeeded(ctx, spec.name, arguments)
        return result

    def _run(self, spec: ToolSpec, inp: BaseModel, arguments: Dict[str, Any], ctx: ToolContext,
             approved: Optional[bool], began: float) -> BaseModel:
        decision = self.decide(spec, inp, ctx.actor)
        if decision == Decision.DENY:
            self._audit(ctx, spec, decision, "denied", arguments, reason_code="permission_denied")
            raise ToolDenied(f"'{spec.title}' is not allowed by your tool permissions.", next=DENIED_HINT)
        writes = spec.tier != ToolTier.READ
        channel, answer, question = ctx.confirm_channel, ctx.answer, ctx.question_sha256
        # A person's "no" is final, even if the policy was loosened to "allow" while they were asked.
        if decision == Decision.ASK or approved is False:
            if approved is None and ctx.confirm is not None:
                channel = getattr(ctx.confirm, "confirm_channel", "framework")
                text = self.question_for(spec, inp, ctx)
                question = text_sha256(text) if text else ""
                # Recorded before it is asked; a question about a change that cannot be recorded is not asked.
                self._audit(ctx, spec, decision, "confirmation_requested", arguments, required=writes,
                            confirm_channel=channel, question_sha256=question)
                token = _QUESTION.set(text)
                try:
                    approved = bool(ctx.confirm(spec, inp))
                finally:
                    _QUESTION.reset(token)
                answer = "accepted" if approved else "declined"
            if not approved:
                # approved stays None when nobody could be asked; False is a person's "no"
                code = "declined" if approved is False else "needs_person"
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

        def end(outcome: str, detail: str = "", result: Any = None, reason_code: str = "") -> None:
            if ctx.cancel.is_set():
                detail = ("The client cancelled the call while it ran; this is what it did. " + detail).strip()
            self._audit(ctx, spec, decision, outcome, arguments, approved=approved, detail=detail,
                        duration_ms=int((time.monotonic() - began) * 1000), links=links, result=result,
                        reason_code=reason_code, **confirmation)

        token = _CALL.set((self, ctx.actor))
        try:
            result = self._output(spec, spec.handler(inp), [r.note(spec.name) for r in ctx.repairs])
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
