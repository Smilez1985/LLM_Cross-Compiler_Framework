#!/usr/bin/env python3
"""
Tool permissions, asked once at Ditto's first setup and stored per user.

Every tool belongs to a risk tier. The user decides per tier (and optionally per tool) whether a call
is allowed, needs a confirmation, or is denied. Forbidden tools are always denied, and tools that
reach other machines or delete data can be allowed only one by one, never for the whole tier.

One policy per caller: Ditto in process (`ditto`), local stdio MCP clients (`mcp`) and, later, each named HTTP
token (`mcp:<name>`). A caller without its own section uses the shared one at the top of the file. A tool can
bring its own default decision (for example "deny" for an optional tool) and a cap that no setting lifts; tools
the person has not reviewed yet never inherit a loosened tier decision.
"""

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


class ToolTier(str, Enum):
    READ = "read"                # look at targets, builds, logs, knowledge; no changes
    LOCAL_WRITE = "local_write"  # change things on this computer: start/cancel builds, settings
    EXTERNAL = "external"        # reach other machines or delete data: deploy over SSH, uploads
    FORBIDDEN = "forbidden"      # never callable (e.g. arbitrary shell commands)


class Decision(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


TIER_LABELS = {
    ToolTier.READ: "Read information (targets, builds, logs, knowledge base)",
    ToolTier.LOCAL_WRITE: "Change things on this computer (start or cancel builds)",
    ToolTier.EXTERNAL: "Reach other devices or delete data (deploy over SSH, uploads)",
}

RECOMMENDED_TIERS: Dict[ToolTier, Decision] = {
    ToolTier.READ: Decision.ALLOW,
    ToolTier.LOCAL_WRITE: Decision.ASK,
    ToolTier.EXTERNAL: Decision.ASK,
}

# The highest decision a tool of a tier may get unless the tool declares its own cap: a tool that reaches another
# device or deletes data always asks (it can be set to "ask" or "deny", never to "allow").
TIER_CAPS: Dict[ToolTier, Decision] = {
    ToolTier.READ: Decision.ALLOW,
    ToolTier.LOCAL_WRITE: Decision.ALLOW,
    ToolTier.EXTERNAL: Decision.ASK,
    ToolTier.FORBIDDEN: Decision.DENY,
}

# The tools of the 3.0.0 release: a policy file saved before tools were reviewed one by one was set up while
# exactly these existed, so they count as reviewed there; every later tool starts at its own default.
TOOLS_OF_3_0_0 = frozenset({
    "list_targets", "get_target_capabilities", "list_build_requests", "get_build_status", "read_build_log",
    "list_artifacts", "run_doctor", "search_knowledge", "start_build", "cancel_build", "resolve_healing",
})

CALLER_DITTO = "ditto"   # Ditto in process
CALLER_MCP = "mcp"       # local stdio MCP clients
_CALLER = re.compile(r"^(ditto|mcp|mcp:[a-z0-9][a-z0-9_-]{0,31})$")

_STRICTNESS = {Decision.ALLOW: 0, Decision.ASK: 1, Decision.DENY: 2}

DecisionRule = Union[Decision, Callable[[Any, str], Optional[Decision]], None]


def stricter(*decisions: Optional[Decision]) -> Decision:
    """The strictest of the given decisions (deny > ask > allow)."""
    given = [d for d in decisions if d is not None]
    return max(given, key=lambda d: _STRICTNESS[d]) if given else Decision.ASK


def _rule(rule: DecisionRule, inp: Any, caller: str) -> Optional[Decision]:
    if callable(rule):
        return rule(inp, caller)
    return rule


def _check_tiers(tiers: Dict[ToolTier, Decision]) -> Dict[ToolTier, Decision]:
    if tiers.get(ToolTier.FORBIDDEN, Decision.DENY) != Decision.DENY:
        raise ValueError("forbidden tools cannot be enabled")
    if tiers.get(ToolTier.EXTERNAL) == Decision.ALLOW:
        raise ValueError("tools that reach other devices can only be allowed one by one")
    for tier in RECOMMENDED_TIERS:
        tiers.setdefault(tier, RECOMMENDED_TIERS[tier])
    return tiers


class CallerPolicy(BaseModel):
    """One caller's own permissions (replaces the shared ones for that caller)."""
    model_config = ConfigDict(extra="forbid")

    tiers: Dict[ToolTier, Decision] = Field(default_factory=lambda: dict(RECOMMENDED_TIERS))
    overrides: Dict[str, Decision] = Field(default_factory=dict)
    reviewed: Optional[List[str]] = None  # tool names the person has seen in the setup for this caller

    @model_validator(mode="after")
    def _limits(self):
        _check_tiers(self.tiers)
        return self


class ToolPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1  # new fields stay optional: a file from 3.0.0 reads as it is
    configured: bool = False  # False until the user went through the setup
    updated_at: Optional[str] = None
    # The shared permissions, for every caller without a section of its own.
    tiers: Dict[ToolTier, Decision] = Field(default_factory=lambda: dict(RECOMMENDED_TIERS))
    overrides: Dict[str, Decision] = Field(default_factory=dict)
    # Tool names the person has reviewed; None in a file from before this list (TOOLS_OF_3_0_0 count as reviewed).
    reviewed: Optional[List[str]] = None
    callers: Dict[str, CallerPolicy] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _limits(self):
        _check_tiers(self.tiers)
        return self

    @field_validator("callers")
    @classmethod
    def _caller_names(cls, value: Dict[str, CallerPolicy]) -> Dict[str, CallerPolicy]:
        bad = [name for name in value if not _CALLER.match(name)]
        if bad:
            raise ValueError(f"unknown callers {bad} (ditto, mcp or mcp:<token name>)")
        return value

    def decide(self, tool_name: str, tier: ToolTier) -> Decision:
        """The stored decision for a tool name in the shared permissions (override, else tier), without the
        tool's own default and cap; ToolRegistry.decide applies those (decide_tool)."""
        if tier == ToolTier.FORBIDDEN:
            return Decision.DENY
        if tool_name in self.overrides:
            return self.overrides[tool_name]
        return self.tiers.get(tier, Decision.ASK)

    # --- per caller, with the tool's own default and cap ------------------------------------------------------

    def section(self, caller: Optional[str]) -> Optional[Tuple[Dict[ToolTier, Decision], Dict[str, Decision],
                                                                Optional[List[str]]]]:
        """(tiers, overrides, reviewed) that apply to a caller; None for a named token without its own section
        (a new token may only read until the person sets its permissions)."""
        own = self.callers.get(caller or "")
        if own is not None:
            return own.tiers, own.overrides, own.reviewed
        if caller and caller.startswith("mcp:"):
            return None
        return self.tiers, self.overrides, self.reviewed

    def reviewed_tool(self, caller: Optional[str], name: str) -> bool:
        section = self.section(caller)
        if section is None:
            return False
        reviewed = section[2]
        return name in (TOOLS_OF_3_0_0 if reviewed is None else reviewed)

    def decide_tool(self, spec: Any, inp: Any = None, caller: Optional[str] = None) -> Decision:
        return self.explain_tool(spec, inp, caller)[0]

    def explain_tool(self, spec: Any, inp: Any = None, caller: Optional[str] = None) -> Tuple[Decision, str]:
        """The decision for one call of a tool and why, in this order: the caller's per-tool setting; else the
        tool's own default; else the caller's tier decision (for a tool not reviewed yet never looser than the
        recommended one); then capped by the tool's maximum. A tool's default never opens a tier the person
        denied."""
        name, tier = spec.name, spec.tier
        caller = caller or ""
        if tier == ToolTier.FORBIDDEN:
            return Decision.DENY, "tools in the forbidden tier never run"
        section = self.section(caller)
        if section is None:
            if tier == ToolTier.READ:
                decision, why = self.tiers.get(ToolTier.READ, Decision.ALLOW), "a new token may read"
            else:
                return Decision.DENY, "a new token may only read until you set its permissions"
        else:
            tiers, overrides, _ = section
            tier_decision = tiers.get(tier, Decision.ASK)
            default = _rule(getattr(spec, "default_decision", None), inp, caller)
            if name in overrides:
                decision, why = overrides[name], "your setting for this tool"
            elif default is not None and tier_decision == Decision.DENY:
                decision, why = Decision.DENY, f"you denied the tier {tier.value}"
            elif default is not None:
                decision, why = default, "the tool's own default"
            elif self.reviewed_tool(caller, name):
                decision, why = tier_decision, f"your setting for the tier {tier.value}"
            else:
                recommended = RECOMMENDED_TIERS.get(tier, Decision.ASK)
                decision = stricter(tier_decision, recommended)
                why = (f"new tool: the recommended setting for {tier.value} until you review it"
                       if decision != tier_decision else f"your setting for the tier {tier.value}")
        cap = _rule(getattr(spec, "max_decision", None), inp, caller) or TIER_CAPS.get(tier, Decision.ASK)
        capped = stricter(decision, cap)
        if capped != decision:
            why += f"; capped at {capped.value} for this tool"
        return capped, why


def restrictive_policy() -> ToolPolicy:
    """Used when a stored policy exists but cannot be read: it must never be looser than what the user chose."""
    return ToolPolicy(configured=False, tiers={tier: Decision.DENY for tier in RECOMMENDED_TIERS},
                      reviewed=[])


class PolicyStore:
    """Loads and saves the policy as JSON (mode 0600) in the per-user state directory."""

    FILE_NAME = "tool_policy.json"

    def __init__(self, state_dir: Path):
        self.path = Path(state_dir) / self.FILE_NAME
        self.problem: Optional[str] = None  # set when the stored file exists but could not be used

    def load(self) -> ToolPolicy:
        try:
            policy = ToolPolicy.model_validate(json.loads(self.path.read_text(encoding="utf-8")))
            self.problem = None
            return policy
        except FileNotFoundError as e:
            if os.path.lexists(self.path):
                # A link whose target is gone (moved folder, unmounted drive): the stored choice is unknown.
                self.problem = (f"{self.path} is a link to a missing file ({e}); all tools are denied until "
                                "it is back.")
                return restrictive_policy()
            self.problem = None
            return ToolPolicy()
        except OSError as e:
            # Unreadable right now (permissions, too many open files): deny everything for this call.
            self.problem = f"{self.path} cannot be read ({e}); all tools are denied until it can."
            return restrictive_policy()
        except (ValueError, ValidationError, RecursionError) as e:
            # Damaged or edited by hand (RecursionError: deeply nested JSON): deny everything (the recommended
            # defaults could be looser than the stored choice) and keep a copy, so saving the setup again does
            # not silently lose it.
            self.problem = (f"{self.path} is damaged ({str(e).splitlines()[0][:200]}); all tools are denied. "
                            f"A copy was kept as {self.path.name}.bad; set the permissions up again.")
            self._keep_copy()
            return restrictive_policy()

    @property
    def damaged(self) -> bool:
        return self.problem is not None

    def _keep_copy(self) -> None:
        bad = self.path.with_name(self.path.name + ".bad")
        try:
            data = self.path.read_bytes()
            if not bad.exists() or bad.read_bytes() != data:
                fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".tool_policy.bad.")
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                os.replace(tmp, bad)
        except OSError:
            pass

    def save(self, policy: ToolPolicy) -> ToolPolicy:
        policy = policy.model_copy(update={"updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        ToolPolicy.model_validate(policy.model_dump())  # re-check limits after edits
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".tool_policy.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(policy.model_dump_json(indent=2))
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return policy
