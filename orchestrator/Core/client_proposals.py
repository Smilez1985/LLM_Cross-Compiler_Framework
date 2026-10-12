#!/usr/bin/env python3
"""
Fix proposals of AI clients for failed builds (MCP design 2.3, work package T4).

An AI client that does Ditto's job (DECISIONS round 1 item 5) analyses a failed build itself and hands its proposal to
the framework with the tool `propose_healing_fix`. The proposal passes the same checks as Ditto's own analysis (the
`HealingAnalysis` model and the dangerous-command filter) and is kept as a manual step for the person: the framework
never runs it, and `resolve_healing` never applies it.

The proposals are kept in the per-user folder (`client_proposals.json`, mode 0600), so the GUI and `llm-cli build
status` show them although the build ran in another process (an MCP server). Every entry says who proposed it: the
caller as the framework knows it (`mcp`, `mcp:<token name>`, `ditto`) and the client's own name, which the client
reports itself and is shown as such.
"""

import json
import os
import secrets
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from orchestrator.utils.file_lock import file_lock
from orchestrator.utils.logging import get_logger

FILE_NAME = "client_proposals.json"
MAX_PER_REQUEST = 10   # proposals one build request keeps (a client cannot flood the person's view)
MAX_KEPT = 200         # proposals kept in all; the oldest go first
PROPOSAL_ID_PREFIX = "hp_"

logger = get_logger("ClientProposals")


class ProposalLimit(ValueError):
    """A build request already holds MAX_PER_REQUEST proposals."""


@dataclass(frozen=True)
class ClientProposal:
    """One AI client's fix proposal: plain data. The texts are the AI's (external text, never run)."""
    proposal_id: str
    request_id: str
    summary: str
    root_cause: str
    manual_step: str
    confidence: float
    where: str             # HOST or DEVICE
    proposed_by: str       # the caller: mcp, mcp:<token name>, ditto
    client: str = ""       # the client's own name and version, as it reports it (a label, never trusted)
    request: str = ""      # the build request's speaking name (model, target, format)
    created: str = ""

    def who(self) -> str:
        """Who proposed it, for a person: the caller, and the client's own name marked as self-reported."""
        caller = {"mcp": "a local AI client (stdio)", "ditto": "Ditto"}.get(self.proposed_by, "")
        if not caller and self.proposed_by.startswith("mcp:"):
            caller = f"the AI client with the token '{self.proposed_by[4:]}'"
        caller = caller or self.proposed_by or "an AI client"
        return caller + (f" ({self.client}, self-reported)" if self.client else "")


def new_proposal_id() -> str:
    return PROPOSAL_ID_PREFIX + secrets.token_hex(8)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ClientProposalStore:
    """The proposals in the per-user folder; written under a lock by the process that took the proposal."""

    def __init__(self, state_dir: Path):
        self.path = Path(state_dir) / FILE_NAME
        self._lock_path = Path(state_dir) / f".{FILE_NAME}.lock"

    def _read(self) -> List[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        items = data.get("proposals") if isinstance(data, dict) else None
        return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []

    def _write(self, items: List[dict]) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=f".{FILE_NAME}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "proposals": items}, f, indent=2, ensure_ascii=False)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @staticmethod
    def _load(item: dict) -> Optional[ClientProposal]:
        try:
            return ClientProposal(**{k: item[k] for k in ClientProposal.__dataclass_fields__ if k in item})
        except (TypeError, KeyError):
            return None

    def add(self, proposal: ClientProposal) -> ClientProposal:
        """Keeps a proposal (with its creation time); refuses the eleventh one of a build request."""
        proposal = ClientProposal(**{**asdict(proposal), "created": proposal.created or _now()})
        with file_lock(self._lock_path, timeout=10):
            items = self._read()
            if sum(1 for i in items if i.get("request_id") == proposal.request_id) >= MAX_PER_REQUEST:
                raise ProposalLimit(f"Build request '{proposal.request_id}' already has {MAX_PER_REQUEST} proposals "
                                    "from AI clients.")
            items.append(asdict(proposal))
            self._write(items[-MAX_KEPT:])
        return proposal

    def list(self, request_id: Optional[str] = None) -> List[ClientProposal]:
        """The kept proposals, oldest first; with `request_id` only that build request's."""
        found = [self._load(i) for i in self._read() if request_id is None or i.get("request_id") == request_id]
        return [p for p in found if p is not None]

    def count(self, request_id: str) -> int:
        return len(self.list(request_id))
