#!/usr/bin/env python3
"""
Tool permissions, asked once at Ditto's first setup and stored per user.

Every tool belongs to a risk tier. The user decides per tier (and optionally per tool) whether a call
is allowed, needs a confirmation, or is denied. Forbidden tools are always denied, and tools that
reach other machines or delete data can be allowed only one by one, never for the whole tier.
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


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


class ToolPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    configured: bool = False  # False until the user went through the setup
    updated_at: Optional[str] = None
    tiers: Dict[ToolTier, Decision] = Field(default_factory=lambda: dict(RECOMMENDED_TIERS))
    overrides: Dict[str, Decision] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _limits(self):
        if self.tiers.get(ToolTier.FORBIDDEN, Decision.DENY) != Decision.DENY:
            raise ValueError("forbidden tools cannot be enabled")
        if self.tiers.get(ToolTier.EXTERNAL) == Decision.ALLOW:
            raise ValueError("tools that reach other devices can only be allowed one by one")
        for tier in RECOMMENDED_TIERS:
            self.tiers.setdefault(tier, RECOMMENDED_TIERS[tier])
        return self

    def decide(self, tool_name: str, tier: ToolTier) -> Decision:
        if tier == ToolTier.FORBIDDEN:
            return Decision.DENY
        if tool_name in self.overrides:
            return self.overrides[tool_name]
        return self.tiers.get(tier, Decision.ASK)


def restrictive_policy() -> ToolPolicy:
    """Used when a stored policy exists but cannot be read: it must never be looser than what the user chose."""
    return ToolPolicy(configured=False, tiers={tier: Decision.DENY for tier in RECOMMENDED_TIERS})


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
