#!/usr/bin/env python3
"""
One tool registry for Ditto and the MCP server.

A tool has typed input and output (pydantic), a risk tier and a handler. Every call goes through
ToolRegistry.call(): input validation, the user's stored permission policy, an optional confirmation,
the handler with an output check, and an audit entry - the same path for every caller.
"""

import contextvars
import hashlib
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, Type

from pydantic import BaseModel, ValidationError

from orchestrator.tools.audit import AuditLog
from orchestrator.tools.confirmation import CallTooLong, describe_call
from orchestrator.tools.output import ResultTooLarge, check_size, filtered_result
from orchestrator.tools.policy import TIER_CAPS, Decision, DecisionRule, PolicyStore, ToolPolicy, ToolTier
from orchestrator.tools.spec import CHANNELS, FieldMeta, GrantRule, ToolLimits, example_problems
from orchestrator.utils.helpers import redact_secrets
from orchestrator.utils.logging import get_logger


def text_sha256(text: str) -> str:
    """The hash an audit entry records for a question or a result (never the text itself)."""
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


class ToolError(Exception):
    """A tool call failed; the message is safe to show to the user and to the model."""


class ToolDenied(ToolError):
    """The user's policy (or their answer to a confirmation) does not allow this call."""


class ToolInputError(ToolError):
    """The arguments do not match the tool's input schema."""


class AuditUnavailable(ToolError):
    """A call that changes something was refused because its audit entry could not be written."""


def _with_message(error: ToolError, message: str) -> ToolError:
    """The same kind of error (denied, invalid input, ...) with another message."""
    try:
        return type(error)(message)
    except Exception:  # a subclass with its own constructor
        return ToolError(message)


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


class ToolRegistry:
    def __init__(self, policy_store: PolicyStore, audit: Optional[AuditLog] = None):
        self.logger = get_logger("ToolRegistry")
        self.policy_store = policy_store
        self.audit = audit
        self._tools: Dict[str, ToolSpec] = {}

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
        self._tools[spec.name] = spec
        return spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolError(f"Unknown tool '{name}'.") from None

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
        fields = {"client": ctx.client, "session": ctx.session, **extra}
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
        try:
            return spec.input_model.model_validate(arguments or {})
        except ValidationError as e:
            problems = "; ".join(f"{'.'.join(str(p) for p in err['loc']) or 'input'}: {err['msg']}" for err in e.errors())
            raise ToolInputError(f"Invalid arguments for {spec.name}: {problems}") from None

    def refuse(self, name: str, arguments: Optional[Dict[str, Any]], ctx: ToolContext,
               outcome: str = "not_confirmed", detail: str = "", **extra: Any) -> None:
        """Audits a call that was refused, cancelled or left unanswered outside call() (e.g. because it could
        not be confirmed). Nobody approved it, so `approved` stays NULL."""
        spec = self.get(name)
        self._audit(ctx, spec, self.decide(spec, caller=ctx.actor), outcome, arguments, detail=detail, **extra)

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
        """The question the framework's own channel shows (Ditto's dialog or prompt); "" when it is too long to
        show, in which case that channel refuses the call itself."""
        try:
            return describe_call(spec, inp.model_dump(mode="json"),
                                 actor="Ditto" if ctx.actor == "ditto" else "The AI client")
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
        spec = self.get(name)
        decision = self.decide(spec, caller=ctx.actor)
        try:
            inp = self.validate_input(spec, arguments)
        except ToolInputError as e:
            self._audit(ctx, spec, decision, "invalid_input", arguments, detail=str(e))
            raise
        if callable(spec.default_decision) or callable(spec.max_decision):
            decision = self.decide(spec, inp, ctx.actor)  # the rule depends on the call's input

        if decision == Decision.DENY:
            self._audit(ctx, spec, decision, "denied", arguments)
            raise ToolDenied(f"'{spec.title}' is not allowed by your tool permissions.")
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
                approved = bool(ctx.confirm(spec, inp))
                answer = "accepted" if approved else "declined"
            if not approved:
                # approved stays None when nobody could be asked; False is a person's "no"
                self._audit(ctx, spec, decision, "not_confirmed", arguments, approved=approved,
                            confirm_channel=channel or "none", question_sha256=question,
                            answer=answer or ("declined" if approved is False else "unavailable"))
                raise ToolDenied(f"'{spec.title}' needs your confirmation, which was not given.")
        confirmation = {"confirm_channel": channel or "none", "question_sha256": question, "answer": answer}

        started = None
        if writes:  # write-ahead: a crash in the middle of a side effect still leaves a record
            started = self._audit(ctx, spec, decision, "started", arguments, approved=approved, required=True,
                                  **confirmation)
        links = [started] if started else []
        if ctx.cancel.is_set():  # the caller gave up before the handler started
            self._audit(ctx, spec, decision, "cancelled", arguments, approved=approved, links=links,
                        detail="Cancelled before it started.", **confirmation)
            raise ToolError(f"'{spec.title}' was cancelled before it started.")

        def end(outcome: str, detail: str = "", result: Any = None) -> None:
            if ctx.cancel.is_set():
                detail = ("The client cancelled the call while it ran; this is what it did. " + detail).strip()
            self._audit(ctx, spec, decision, outcome, arguments, approved=approved, detail=detail,
                        duration_ms=int((time.monotonic() - began) * 1000), links=links, result=result,
                        **confirmation)

        token = _CALL.set((self, ctx.actor))
        try:
            result = self._output(spec, spec.handler(inp))
        except ResultTooLarge as e:
            end("error", str(e))
            raise ToolError(f"{spec.title}: {e}") from None
        except ToolError as e:
            message = redact_secrets(str(e))
            end("error", message)
            if message == str(e):
                raise
            raise _with_message(e, message) from None  # the original still holds the secret
        except ValidationError as e:  # a schema or handler bug (output check, models built inside the handler)
            self.logger.error(f"Tool {spec.name} produced invalid data: {e}", exc_info=True)
            end("error", repr(e))
            raise ToolError(f"{spec.title} failed (internal error; details are in the framework log).") from e
        except ValueError as e:  # user-facing validation problems (e.g. BuildServiceError)
            end("error", str(e))
            raise ToolError(redact_secrets(str(e))) from e
        except Exception as e:
            self.logger.error(f"Tool {spec.name} failed: {e}", exc_info=True)
            end("error", repr(e))
            raise ToolError(f"{spec.title} failed: {redact_secrets(str(e))}") from e
        except BaseException:  # interrupted in the calling thread (KeyboardInterrupt, shutdown): still recorded
            end("cancelled", "Interrupted while it ran.")
            raise
        finally:
            _CALL.reset(token)
        serialized = result.model_dump_json()
        end("ok", result={"sha256": text_sha256(serialized), "bytes": len(serialized.encode("utf-8"))})
        return result

    def _output(self, spec: ToolSpec, result: Any) -> BaseModel:
        """The handler's result as the tool's output model, filtered (secrets redacted, external text cleaned
        and capped), with the framework's notes, within the size limit (ResultTooLarge)."""
        model = spec.output_model
        if isinstance(result, dict):
            result = model.model_validate(result)
        elif (isinstance(result, BaseModel) and not isinstance(result, model)
              and issubclass(model, type(result))):  # e.g. BuildStatusOut for its tool model with notes
            result = model.model_validate(result.model_dump())
        if not isinstance(result, model):
            raise TypeError(f"handler returned {type(result).__name__}, expected {model.__name__}")
        notes = list(spec.notes(result)) if spec.notes is not None else []
        result = filtered_result(result, notes)
        check_size(result, spec.paging_hint, spec.limits.max_result_bytes)
        return result
