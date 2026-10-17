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
import threading
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from orchestrator.tools.grants import Grant
from orchestrator.tools.signing import SIGNATURE_FIELD, RevisionAnchor, SignatureError, sign, verify
from orchestrator.utils.next_steps import WithSteps


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

# Tools renamed after 3.0.0 (old name -> new name). The old name stays registered, callable but not listed. A person's
# setting for either name applies to both and the stricter one wins, so neither name opens what the other's setting
# closed; a tool reviewed under one name counts as reviewed under the other.
TOOL_RENAMES: Dict[str, str] = {"run_doctor": "run_mojo_oracle"}


def renamed_names(name: str) -> Tuple[str, ...]:
    """The other names of a tool through TOOL_RENAMES (its old name, or its new one), in a fixed order."""
    return tuple(sorted({new for old, new in TOOL_RENAMES.items() if old == name}
                        | {old for old, new in TOOL_RENAMES.items() if new == name}))


CALLER_DITTO = "ditto"   # Ditto in process
CALLER_MCP = "mcp"       # local stdio MCP clients
_CALLER = re.compile(r"^(ditto|mcp|mcp:[a-z0-9][a-z0-9_-]{0,31})$")

_STRICTNESS = {Decision.ALLOW: 0, Decision.ASK: 1, Decision.DENY: 2}

# A Decision, or a function of (input, who) -> Decision or None; see _rule for `who`.
DecisionRule = Union[Decision, Callable[[Any, str], Optional[Decision]], None]


def stricter(*decisions: Optional[Decision]) -> Decision:
    """The strictest of the given decisions (deny > ask > allow)."""
    given = [d for d in decisions if d is not None]
    return max(given, key=lambda d: _STRICTNESS[d]) if given else Decision.ASK


def _rule(rule: DecisionRule, inp: Any, who: str, failed: Decision) -> Optional[Decision]:
    """A tool's default or cap for one call. `who` is the caller's owner identity (ToolContext.owner) when there is
    a call, else the caller's name. A rule that fails gives `failed` (never a looser answer than the rule's)."""
    if callable(rule):
        try:
            return rule(inp, who)
        except Exception:
            return failed
    return rule


def call_only_reads(spec: Any, inp: Any) -> bool:
    """Whether one call of a tool only reads: a read tool, or a call of a tool with reading and changing actions
    (ToolSpec.reads, e.g. the GUI tool's read_form) that its `reads(input)` names as reading. A rule that fails, or a
    call without input, counts as changing something (never a looser answer)."""
    if getattr(spec, "tier", None) == ToolTier.READ:
        return True
    rule = getattr(spec, "reads", None)
    if not callable(rule) or inp is None:
        return False
    try:
        return rule(inp) is True
    except Exception:
        return False


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
    # Tools whose question always appears in the framework (approval window), never in the client's own dialog.
    framework_only: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _limits(self):
        _check_tiers(self.tiers)
        return self


class ToolPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1  # new fields stay optional: a file from 3.0.0 reads as it is
    configured: bool = False  # False until the user went through the setup
    updated_at: Optional[str] = None
    # Counts the saves; a save based on an older revision is refused (PolicyConflict), so a change made elsewhere
    # meanwhile (a revocation in the GUI) is never undone by a window or process that read the file before it.
    revision: int = Field(default=0, ge=0)
    # The shared permissions, for every caller without a section of its own.
    tiers: Dict[ToolTier, Decision] = Field(default_factory=lambda: dict(RECOMMENDED_TIERS))
    overrides: Dict[str, Decision] = Field(default_factory=dict)
    # Tool names the person has reviewed; None in a file from before this list (TOOLS_OF_3_0_0 count as reviewed).
    reviewed: Optional[List[str]] = None
    # Tools whose question always appears in the framework (approval window), never in the client's own dialog.
    framework_only: List[str] = Field(default_factory=list)
    callers: Dict[str, CallerPolicy] = Field(default_factory=dict)
    # Standing approvals per caller (ditto, mcp, mcp:<token>), whatever permissions section that caller uses: one never
    # covers another caller. Written only on a person's action; their uses come from the audit log.
    grants: Dict[str, List[Grant]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _limits(self):
        _check_tiers(self.tiers)
        return self

    @field_validator("callers", "grants")
    @classmethod
    def _caller_names(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        bad = [name for name in value if not _CALLER.match(name)]
        if bad:
            raise ValueError(f"unknown callers {bad} (ditto, mcp or mcp:<token name>)")
        return value

    @model_validator(mode="after")
    def _unique_grants(self):
        ids = [g.id for grants in self.grants.values() for g in grants]
        if len(ids) != len(set(ids)):
            raise ValueError("standing approval ids must be unique")
        return self

    def grant_by_id(self, grant_id: str) -> Optional[Tuple[str, Grant]]:
        for caller, grants in self.grants.items():
            for grant in grants:
                if grant.id == grant_id:
                    return caller, grant
        return None

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

    def framework_only_for(self, caller: Optional[str]) -> List[str]:
        """The tools this caller's questions are always put to the person in the framework (the caller's own section,
        else the shared one; a named token without its own section: the shared one)."""
        own = self.callers.get(caller or "")
        return list(own.framework_only if own is not None else self.framework_only)

    def reviewed_tool(self, caller: Optional[str], name: str) -> bool:
        section = self.section(caller)
        if section is None:
            return False
        reviewed = TOOLS_OF_3_0_0 if section[2] is None else section[2]
        return any(n in reviewed for n in (name, *renamed_names(name)))

    def decide_tool(self, spec: Any, inp: Any = None, caller: Optional[str] = None,
                    owner: Optional[str] = None) -> Decision:
        return self.explain_tool(spec, inp, caller, owner)[0]

    def explain_tool(self, spec: Any, inp: Any = None, caller: Optional[str] = None,
                     owner: Optional[str] = None) -> Tuple[Decision, str]:
        """The decision for one call of a tool and why, in this order: the caller's per-tool setting; else the
        tool's own default; else the caller's tier decision (for a tool not reviewed yet never looser than the
        recommended one); then capped by the tool's maximum. A tool's default never opens a tier the person
        denied. A callable default or cap gets `owner` (the caller with the connection that owns its builds and
        jobs, ToolContext.owner) when given, else the caller's name."""
        name, tier = spec.name, spec.tier
        caller = caller or ""
        who = owner or caller
        if tier == ToolTier.FORBIDDEN:
            return Decision.DENY, "tools in the forbidden tier never run"
        section = self.section(caller)
        if section is None:
            if tier != ToolTier.READ:
                return Decision.DENY, "a new token may only read until you set its permissions"
            # Never looser than the shared setting for this tool or the tool's own default (an optional read tool
            # that starts as "deny" stays denied for a token too).
            shared = self.overrides.get(name, self.tiers.get(ToolTier.READ, Decision.ALLOW))
            default = _rule(getattr(spec, "default_decision", None), inp, who, Decision.ASK)
            decision, why = stricter(shared, default) if default is not None else shared, "a new token may read"
            if decision != shared:
                why += "; the tool's own default"
            elif name in self.overrides:
                why += "; your shared setting for this tool"
            decision, why = _renamed_setting(name, self.overrides, decision, why)
        else:
            tiers, overrides, _ = section
            # A call that only reads (a reading action of a tool that can also change things) is decided like a read
            # tool: the caller's read tier. A new token (no section) stays with the tool's own tier above.
            if tier != ToolTier.READ and call_only_reads(spec, inp):
                tier = ToolTier.READ
            tier_decision = tiers.get(tier, Decision.ASK)
            default = _rule(getattr(spec, "default_decision", None), inp, who, Decision.ASK)
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
            decision, why = _renamed_setting(name, overrides, decision, why)
        cap = (_rule(getattr(spec, "max_decision", None), inp, who, Decision.DENY)
               or TIER_CAPS.get(tier, Decision.ASK))
        capped = stricter(decision, cap)
        if capped != decision:
            why += f"; capped at {capped.value} for this tool"
        return capped, why


def _renamed_setting(name: str, overrides: Dict[str, Decision], decision: Decision, why: str) -> Tuple[Decision, str]:
    """A person's setting for another name of the same tool (TOOL_RENAMES): the stricter decision wins."""
    for other in renamed_names(name):
        setting = overrides.get(other)
        if setting is not None and stricter(decision, setting) != decision:
            decision, why = setting, f"your setting for {other} (another name of this tool)"
    return decision, why


def restrictive_policy() -> ToolPolicy:
    """Used when a stored policy exists but cannot be read: it must never be looser than what the user chose."""
    return ToolPolicy(configured=False, tiers={tier: Decision.DENY for tier in RECOMMENDED_TIERS},
                      reviewed=[])


# --- a person's change to one caller's permissions (the CLI's `tools permissions` and the GUI's Tool Permissions) ---

PERSON_CALLERS = (CALLER_DITTO, CALLER_MCP)   # callers a person can give permissions of their own (besides tokens)
SECTION_FIELDS = ("tiers", "overrides", "reviewed", "framework_only")


def caller_section(policy: ToolPolicy, caller: str) -> CallerPolicy:
    """The permissions a change for `caller` (ditto or mcp) starts from: its own section, else a new one copied from
    the shared permissions (tiers, per-tool settings, reviewed tools and the tools asked in the framework only), so
    creating it never loosens a setting."""
    own = policy.callers.get(caller)
    if own is not None:
        return own
    return CallerPolicy(tiers=dict(policy.tiers), overrides=dict(policy.overrides),
                        reviewed=list(policy.reviewed) if policy.reviewed is not None else None,
                        framework_only=list(policy.framework_only))


def with_section(policy: ToolPolicy, caller: Optional[str], **changes: Any) -> ToolPolicy:
    """`policy` with the permissions of `caller` changed as given (tiers, overrides, reviewed, framework_only):
    ditto or mcp get their own section (created from the shared permissions when they have none), None changes the
    shared permissions. Nothing else changes; the store checks the limits when it saves."""
    unknown = sorted(set(changes) - set(SECTION_FIELDS))
    if unknown:
        raise ValueError(f"not a part of a caller's permissions: {', '.join(unknown)}")
    if not caller:
        return policy.model_copy(update=changes)
    if caller not in PERSON_CALLERS:
        raise ValueError(f"unknown caller {caller!r} (ditto or mcp; a named token keeps its own permissions)")
    section = caller_section(policy, caller).model_copy(update=changes)
    return policy.model_copy(update={"callers": {**policy.callers, caller: section}})


def reset_policy(policy: ToolPolicy, caller: Optional[str] = None) -> ToolPolicy:
    """`tools permissions --reset`: the recommended defaults (ToolPolicy()); every caller's own section and every
    standing approval go, and the setup asks again at the next start. With `caller` (ditto or mcp): that caller uses
    the shared permissions again; its own section and its standing approvals go, nothing else changes."""
    if not caller:
        return ToolPolicy()
    if caller not in PERSON_CALLERS:
        raise ValueError(f"unknown caller {caller!r} (ditto or mcp)")
    return policy.model_copy(update={
        "callers": {name: section for name, section in policy.callers.items() if name != caller},
        "grants": {name: grants for name, grants in policy.grants.items() if name != caller}})


def reset_changes(policy: ToolPolicy, caller: Optional[str] = None) -> List[str]:
    """What reset_policy(policy, caller) would change, one line per change in the words of the audit log; empty when
    nothing would change. Shown to the person before a reset."""
    after = reset_policy(policy, caller)
    if after.model_dump(exclude={"revision", "updated_at"}) == policy.model_dump(exclude={"revision", "updated_at"}):
        return []
    return policy_changes(policy, after)


class PolicyStoreError(WithSteps, Exception):
    """The permissions could not be saved; the message is safe to show."""


class PolicyConflict(PolicyStoreError):
    """The stored permissions changed since they were read (another window or process saved them meanwhile)."""


def _section_rows(policy: Optional[ToolPolicy]) -> Dict[str, Dict[str, Any]]:
    """The policy's sections ("shared" and one per caller) in a comparable form, for the audit of a change."""
    if policy is None:
        return {}
    sections = {"shared": policy, **policy.callers}
    return {name: {"tiers": {t.value: d.value for t, d in section.tiers.items()},
                   "overrides": {n: d.value for n, d in section.overrides.items()},
                   "reviewed": None if section.reviewed is None else len(section.reviewed),
                   "framework_only": sorted(section.framework_only)}
            for name, section in sections.items()}


def policy_changes(before: Optional[ToolPolicy], after: ToolPolicy) -> List[str]:
    """What a save changes, one line per change ("ditto: tier local_write ask -> allow"), for the audit log: old and
    new decisions, never anything but tool names and decisions."""
    lines: List[str] = []
    if before is None:
        lines.append("set up anew (the stored permissions were missing or could not be used)")
    elif before.configured != after.configured:
        lines.append(f"configured: {before.configured} -> {after.configured}")
    before_rows, after_rows = _section_rows(before), _section_rows(after)
    for name in sorted(set(before_rows) - set(after_rows)):
        lines.append(f"{name}: own permissions removed (uses the shared ones)")
    empty = {"tiers": {}, "overrides": {}, "reviewed": None, "framework_only": []}
    for name in sorted(after_rows, key=lambda n: (n != "shared", n)):
        if before is not None and name not in before_rows:
            lines.append(f"{name}: own permissions added")
        old, new = before_rows.get(name, empty), after_rows[name]
        for tier in sorted(set(old["tiers"]) | set(new["tiers"])):
            if old["tiers"].get(tier) != new["tiers"].get(tier):
                lines.append(f"{name}: tier {tier} {old['tiers'].get(tier, '-')} -> {new['tiers'].get(tier, '-')}")
        for tool in sorted(set(old["overrides"]) | set(new["overrides"])):
            if old["overrides"].get(tool) != new["overrides"].get(tool):
                lines.append(f"{name}: {tool} {old['overrides'].get(tool, 'tier setting')} -> "
                             f"{new['overrides'].get(tool, 'tier setting')}")
        for tool in sorted(set(old["framework_only"]) ^ set(new["framework_only"])):
            lines.append(f"{name}: {tool} " + ("asked in the framework only" if tool in new["framework_only"] else
                                              "asked in any channel"))
        if old["reviewed"] != new["reviewed"]:
            lines.append(f"{name}: reviewed tools {'-' if old['reviewed'] is None else old['reviewed']} -> "
                         f"{'-' if new['reviewed'] is None else new['reviewed']}")
    old_grants = {g.id: (caller, g) for caller, gs in (before.grants if before else {}).items() for g in gs}
    new_grants = {g.id: (caller, g) for caller, gs in after.grants.items() for g in gs}
    for gid in sorted(set(new_grants) - set(old_grants)):
        caller, g = new_grants[gid]
        until = f"until {g.expires}" if g.expires else "until revoked"
        lines.append(f"{caller}: standing approval {gid} for {g.tool} created ({g.summary}; {until}; via "
                     f"{g.created_via})")
    for gid in sorted(set(old_grants) - set(new_grants)):
        caller, g = old_grants[gid]
        lines.append(f"{caller}: standing approval {gid} for {g.tool} revoked")
    for gid in sorted(set(old_grants) & set(new_grants)):
        if new_grants[gid][1].announced and not old_grants[gid][1].announced:
            lines.append(f"{new_grants[gid][0]}: standing approval {gid} shown to you in the GUI")
    return lines or ["saved without changes"]


class PolicyStore:
    """Loads and saves the policy as JSON (mode 0600) in the per-user state directory.

    The file is signed (orchestrator/tools/signing.py) with a key derived from the audit key; a file that is
    damaged, edited by hand, unsigned or signed with another key denies everything. It is written only here, on a
    person's action (the CLI at a terminal, the GUI), under a cross-process lock, and every save is audited with the
    old and new decisions before it takes effect: when the audit entry cannot be written, nothing changes. Each save
    raises the revision, and a save based on an older revision is refused (PolicyConflict)."""

    FILE_NAME = "tool_policy.json"
    LOCK_NAME = ".tool_policy.lock"
    KIND = "tool_policy"

    def __init__(self, state_dir: Path, key: Optional[bytes] = None, audit: Any = None,
                 refuse_unkeyed: Optional[Callable[[], bool]] = None, anchor: Optional[RevisionAnchor] = None):
        """key: the audit key (OS keyring), None without a keyring (unkeyed signatures). audit: the audit log every
        save is recorded in (None: not recorded, e.g. a test or the evaluation's own staging). refuse_unkeyed: asked
        whether an unkeyed signature must be refused although this process has no key (the audit log is kept with
        one). anchor: where the newest saved revision is kept outside the per-user folder (the OS keyring); with it
        an older signed copy put back over the file, or a removed file, denies everything."""
        self.path = Path(state_dir) / self.FILE_NAME
        self.lock_path = Path(state_dir) / self.LOCK_NAME
        self.key = key
        self.audit = audit
        self.refuse_unkeyed = refuse_unkeyed
        self.anchor = anchor
        self.problem: Optional[str] = None  # set when the stored file exists but could not be used
        self._lock = threading.Lock()  # a file lock belongs to one open file: one save of this store at a time

    def load(self) -> ToolPolicy:
        policy, problem, _ = self._read()
        self.problem = problem
        return policy

    def _read(self) -> Tuple[ToolPolicy, Optional[str], Optional[int]]:
        """(the policy to decide with, the problem with the stored file or None, the revision the stored file names
        when it could be parsed, verified or not, for a save over it). Never raises."""
        anchored = self._anchored()
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError as e:
            if os.path.lexists(self.path):
                # A link whose target is gone (moved folder, unmounted drive): the stored choice is unknown.
                return restrictive_policy(), (f"{self.path} is a link to a missing file ({e}); all tools are "
                                              "denied until it is back."), None
            if anchored:
                # The framework saved permissions here before: the defaults could be looser than what was chosen.
                return restrictive_policy(), (f"{self.path} was removed after the framework had saved revision "
                                              f"{anchored} of it; all tools are denied until you set the "
                                              "permissions up again with 'llm-cli tools permissions' or in the GUI "
                                              "(Tool Permissions)."), None
            return ToolPolicy(), None, None
        except OSError as e:
            # Unreadable right now (permissions, too many open files): deny everything for this call.
            return restrictive_policy(), f"{self.path} cannot be read ({e}); all tools are denied until it can.", None
        except ValueError as e:  # not UTF-8
            return self._damaged(e)
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("not a JSON object")
            policy = ToolPolicy.model_validate({k: v for k, v in data.items() if k != SIGNATURE_FIELD})
        except (ValueError, ValidationError, RecursionError) as e:
            # Damaged or edited by hand (RecursionError: deeply nested JSON): deny everything (the recommended
            # defaults could be looser than the stored choice) and keep a copy, so saving the setup again does not
            # silently lose it.
            return self._damaged(e)
        try:
            verify(self.KIND, data, self.key, self.refuse_unkeyed)
        except SignatureError as e:
            self._keep_copy()
            return restrictive_policy(), (f"{self.path} cannot be trusted: {e}. All tools are denied. A copy was "
                                          f"kept as {self.path.name}.bad; set the permissions up again with "
                                          "'llm-cli tools permissions' or in the GUI (Tool Permissions)."), \
                policy.revision
        if policy.revision < anchored:
            # Genuinely signed, but older than what the framework saved last: put back to undo a later change.
            self._keep_copy()
            return restrictive_policy(), (f"{self.path} cannot be trusted: it is an older copy (revision "
                                          f"{policy.revision}; the framework saved revision {anchored} last). All "
                                          f"tools are denied. A copy was kept as {self.path.name}.bad; set the "
                                          "permissions up again with 'llm-cli tools permissions' or in the GUI "
                                          "(Tool Permissions)."), policy.revision
        return policy, None, policy.revision

    def _anchored(self) -> int:
        """The newest revision the framework saved of this file (kept in the keyring), 0 without an anchor."""
        return self.anchor.current(self.path) if self.anchor is not None else 0

    def _damaged(self, error: Exception) -> Tuple[ToolPolicy, str, None]:
        self._keep_copy()
        first = (str(error).splitlines() or [type(error).__name__])[0][:200]
        return restrictive_policy(), (f"{self.path} is damaged ({first}); all tools are denied. A copy was kept as "
                                      f"{self.path.name}.bad; set the permissions up again."), None

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

    def save(self, policy: ToolPolicy, actor: str = "cli") -> ToolPolicy:
        """Saves the policy a person set (`actor`: cli or gui) and returns it as stored, with the next revision.
        PolicyConflict when the stored file changed since `policy` was read (its revision is older), PolicyStoreError
        when the change cannot be audited (then nothing changes)."""
        with self._locked():
            return self._save_locked(policy, actor)

    def update(self, change: Callable[[ToolPolicy], ToolPolicy], actor: str = "cli") -> ToolPolicy:
        """Load, change and save in one step under the lock (`change` gets the stored policy, or the restrictive one
        when the stored file cannot be used); never refused as stale."""
        with self._locked():
            current, _, _ = self._read()
            changed = change(current)
            return self._save_locked(changed.model_copy(update={"revision": current.revision}), actor)

    def replace(self, policy: ToolPolicy, actor: str = "cli") -> ToolPolicy:
        """Saves `policy` as the whole new policy, whatever is stored now (a reset to given values), audited like
        every save. A change based on what a person was shown uses save() (stale saves refused) or update()."""
        return self.update(lambda _current: policy, actor)

    def _locked(self):
        import contextlib

        from orchestrator.utils.file_lock import file_lock
        stack = contextlib.ExitStack()
        stack.enter_context(self._lock)
        try:
            stack.enter_context(file_lock(self.lock_path, timeout=10))
        except BaseException:
            stack.close()
            raise
        return stack

    def _save_locked(self, policy: ToolPolicy, actor: str) -> ToolPolicy:
        before, problem, stored_revision = self._read()
        stored = problem is None and os.path.lexists(self.path)  # a usable stored file to compare with
        if stored and policy.revision != before.revision:
            raise PolicyConflict(
                f"The tool permissions were changed elsewhere (now revision {before.revision}; this change was based "
                f"on revision {policy.revision}). Nothing was saved: open them again and repeat the change.")
        revision = max(stored_revision or 0, policy.revision, self._anchored()) + 1
        policy = policy.model_copy(update={"updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                           "revision": revision})
        policy = ToolPolicy.model_validate(policy.model_dump())  # re-check limits after edits
        data = sign(self.KIND, policy.model_dump(mode="json"), self.key)
        changes = policy_changes(before if stored else None, policy)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".tool_policy.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(data, indent=2, ensure_ascii=False))
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            self._audit_change(actor, changes, revision)  # recorded before it takes effect, or not done at all
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        if self.anchor is not None:  # after the file: a reader in between sees a newer file, never an older one
            self.anchor.saved(self.path, revision)
        self.problem = None
        return policy

    def _audit_change(self, actor: str, changes: List[str], revision: int) -> None:
        if self.audit is None:
            return
        try:
            self.audit.record(actor, "tool_policy", "framework", "person", "policy_changed",
                              arguments={"revision": revision, "changes": changes}, approved=True,
                              detail="; ".join(changes))
        except Exception as e:
            from orchestrator.utils.helpers import redact_secrets
            raise PolicyStoreError(f"The audit log cannot record the change ({redact_secrets(str(e))}); the tool "
                                   "permissions were not changed. Run 'llmcf mojo' to find the cause.") from None
