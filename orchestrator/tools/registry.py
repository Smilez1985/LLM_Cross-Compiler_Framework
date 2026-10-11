#!/usr/bin/env python3
"""
One tool registry for Ditto and the MCP server.

A tool has typed input and output (pydantic), a risk tier and a handler. Every call goes through
ToolRegistry.call(): input validation, the user's stored permission policy, an optional confirmation,
the handler with an output check, and an audit entry - the same path for every caller.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Type

from pydantic import BaseModel, ValidationError

from orchestrator.tools.audit import AuditLog
from orchestrator.tools.policy import Decision, PolicyStore, ToolPolicy, ToolTier
from orchestrator.utils.helpers import redact_secrets
from orchestrator.utils.logging import get_logger


class ToolError(Exception):
    """A tool call failed; the message is safe to show to the user and to the model."""


class ToolDenied(ToolError):
    """The user's policy (or their answer to a confirmation) does not allow this call."""


class ToolInputError(ToolError):
    """The arguments do not match the tool's input schema."""


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

    def annotations(self) -> Dict[str, Any]:
        """MCP tool annotations (hints for clients; the registry enforces the policy itself)."""
        return {
            "title": self.title,
            "readOnlyHint": self.tier == ToolTier.READ,
            "destructiveHint": self.tier == ToolTier.EXTERNAL,
            "idempotentHint": self.idempotent,
            "openWorldHint": self.open_world,
        }


Confirm = Callable[[ToolSpec, BaseModel], bool]


@dataclass
class ToolContext:
    actor: str                       # "ditto", "mcp", "cli", "gui"
    confirm: Optional[Confirm] = None  # asks the user; None = no way to ask, so "ask" means deny
    metadata: Dict[str, Any] = field(default_factory=dict)


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

    def decide(self, spec: ToolSpec) -> Decision:
        return self.policy().decide(spec.name, spec.tier)

    def _audit(self, ctx: ToolContext, spec: ToolSpec, decision: Decision, outcome: str,
               arguments: Any, approved: Optional[bool] = None, detail: str = ""):
        if self.audit is None:
            return
        try:
            self.audit.record(ctx.actor, spec.name, spec.tier.value, decision.value, outcome,
                              arguments=arguments, approved=approved, detail=detail)
        except Exception as e:  # auditing must never break the call itself
            self.logger.warning(f"Audit write failed: {e}")

    def validate_input(self, spec: ToolSpec, arguments: Optional[Dict[str, Any]]) -> BaseModel:
        try:
            return spec.input_model.model_validate(arguments or {})
        except ValidationError as e:
            problems = "; ".join(f"{'.'.join(str(p) for p in err['loc']) or 'input'}: {err['msg']}" for err in e.errors())
            raise ToolInputError(f"Invalid arguments for {spec.name}: {problems}") from None

    def refuse(self, name: str, arguments: Optional[Dict[str, Any]], ctx: ToolContext,
               outcome: str = "not_confirmed", detail: str = "") -> None:
        """Audits a call that was refused before it could reach call(), e.g. because it could not be
        confirmed. Nobody approved it, so `approved` stays NULL."""
        spec = self.get(name)
        self._audit(ctx, spec, self.decide(spec), outcome, arguments, detail=detail)

    def call(self, name: str, arguments: Optional[Dict[str, Any]], ctx: ToolContext,
             approved: Optional[bool] = None) -> BaseModel:
        """Runs a tool for `ctx.actor`. `approved` is the caller's own confirmation result
        (e.g. from an MCP elicitation); without it, ctx.confirm is asked when the policy says "ask".
        False refuses the call whatever the policy says."""
        spec = self.get(name)
        decision = self.decide(spec)
        try:
            inp = self.validate_input(spec, arguments)
        except ToolInputError as e:
            self._audit(ctx, spec, decision, "invalid_input", arguments, detail=str(e))
            raise

        if decision == Decision.DENY:
            self._audit(ctx, spec, decision, "denied", arguments)
            raise ToolDenied(f"'{spec.title}' is not allowed by your tool permissions.")
        # A person's "no" is final, even if the policy was loosened to "allow" while they were asked.
        if decision == Decision.ASK or approved is False:
            if approved is None and ctx.confirm is not None:
                approved = bool(ctx.confirm(spec, inp))
            if not approved:
                # approved stays None when nobody could be asked; False is a person's "no"
                self._audit(ctx, spec, decision, "not_confirmed", arguments, approved=approved)
                raise ToolDenied(f"'{spec.title}' needs your confirmation, which was not given.")

        try:
            result = spec.handler(inp)
            if isinstance(result, dict):
                result = spec.output_model.model_validate(result)
            if not isinstance(result, spec.output_model):
                raise TypeError(f"handler returned {type(result).__name__}, expected {spec.output_model.__name__}")
        except ToolError as e:
            message = redact_secrets(str(e))
            self._audit(ctx, spec, decision, "error", arguments, approved=approved, detail=message)
            if message == str(e):
                raise
            raise _with_message(e, message) from None  # the original still holds the secret
        except ValidationError as e:  # a schema or handler bug (output check, models built inside the handler)
            self.logger.error(f"Tool {spec.name} produced invalid data: {e}", exc_info=True)
            self._audit(ctx, spec, decision, "error", arguments, approved=approved, detail=repr(e))
            raise ToolError(f"{spec.title} failed (internal error; details are in the framework log).") from e
        except ValueError as e:  # user-facing validation problems (e.g. BuildServiceError)
            self._audit(ctx, spec, decision, "error", arguments, approved=approved, detail=str(e))
            raise ToolError(redact_secrets(str(e))) from e
        except Exception as e:
            self.logger.error(f"Tool {spec.name} failed: {e}", exc_info=True)
            self._audit(ctx, spec, decision, "error", arguments, approved=approved, detail=repr(e))
            raise ToolError(f"{spec.title} failed: {redact_secrets(str(e))}") from e
        self._audit(ctx, spec, decision, "ok", arguments, approved=approved)
        return result
