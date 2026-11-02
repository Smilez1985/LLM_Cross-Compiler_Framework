#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - the MCP servers an editor hands to Ditto over ACP

An editor that drives Ditto as an ACP agent (`llm-cli acp`, orchestrator/acp_agent.py) passes MCP servers in
`session/new` (`mcpServers`). The protocol requires an agent to accept stdio entries; accepting means here: they are
recorded and shown to the person, and Ditto connects to none of them. Their tools would not go through the framework's
tool registry (policy, approvals, audit), so this version has no client for them at all; an entry the person enables
is their consent for a later version that connects to it with its results marked as untrusted data and through the
privacy filter.

What is kept per entry (a JSON file in the per-user state folder, mode 0600): its name, transport, command and
arguments (secrets in them redacted), the names of its environment variables (never their values), the editor that
sent it (self-reported, a label), when it was first and last seen, and whether the person enabled it. Nothing of it
reaches an AI: the entries are for the person only (`llm-cli acp servers`, File > Settings... > Ditto in editors).
Enabling and disabling is the person's alone (no tool, no AI caller can).
"""

import hashlib
import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from orchestrator.utils.atomic import write_private
from orchestrator.utils.helpers import redact_secrets

FILE_NAME = "acp_editor_servers.json"
VERSION = 1
MAX_ENTRIES = 64           # the oldest unenabled entries go first beyond this
MAX_TEXT = 200             # characters of one name, command or argument kept
MAX_ARGS = 32
TRANSPORTS = ("stdio", "http", "sse", "acp")


def _clean(value: Any, limit: int = MAX_TEXT) -> str:
    """One line, secrets redacted, control characters removed, cut to `limit`."""
    text = redact_secrets(str(value if value is not None else ""))
    text = "".join(ch for ch in text if ch.isprintable())
    return text[:limit]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class EditorServer:
    """One MCP server an editor named for Ditto."""
    id: str
    name: str
    transport: str
    command: str = ""
    args: List[str] = field(default_factory=list)
    env_names: List[str] = field(default_factory=list)
    url: str = ""
    editor: str = ""
    first_seen: str = ""
    last_seen: str = ""
    enabled: bool = False

    def summary(self) -> str:
        """For the person: the name, the transport and what would run or be called."""
        what = " ".join([self.command, *self.args]).strip() if self.transport == "stdio" else self.url
        return f"{self.name} ({self.transport}): {what}" if what else f"{self.name} ({self.transport})"


def entry_id(transport: str, name: str, command: str, args: Iterable[str], url: str) -> str:
    """The same server from the same editor setup keeps its id (and the person's choice) across sessions."""
    raw = json.dumps([transport, name, command, list(args), url], ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def describe_entry(server: Any) -> Dict[str, Any]:
    """The fields of one `mcpServers` entry of ACP (an SDK model or a dict), cleaned: transport, name, command,
    arguments, the names of the environment variables or headers (never their values), url."""
    def get(key: str, default: Any = None) -> Any:
        if isinstance(server, dict):
            return server.get(key, default)
        return getattr(server, key, default)

    transport = str(get("type", "") or "stdio")
    if transport not in TRANSPORTS:
        transport = "stdio" if get("command") else _clean(transport, 16) or "stdio"
    env = get("env") or get("headers") or []
    names = []
    for item in list(env)[:MAX_ARGS]:
        name = item.get("name") if isinstance(item, dict) else getattr(item, "name", None)
        if name:
            names.append(_clean(name, 80))
    return {"transport": transport, "name": _clean(get("name", "") or "unnamed", 80),
            "command": _clean(get("command", "") or ""),
            "args": [_clean(a) for a in list(get("args") or [])[:MAX_ARGS]],
            "env_names": names, "url": _clean(get("url", "") or "")}


class EditorServers:
    """The recorded editor MCP servers of this person (state folder). Thread-safe within one process; the file is
    written atomically, so the GUI and an `llm-cli acp` process can both use it."""

    def __init__(self, state_dir: Path):
        self.path = Path(state_dir) / FILE_NAME
        self._lock = threading.Lock()

    def _read(self) -> List[EditorServer]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        rows = data.get("servers") if isinstance(data, dict) else None
        out = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            try:
                out.append(EditorServer(**{k: row[k] for k in EditorServer.__dataclass_fields__ if k in row}))
            except TypeError:
                continue
        return out

    def _write(self, servers: List[EditorServer]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_private(self.path, json.dumps({"version": VERSION, "servers": [asdict(s) for s in servers]},
                                            indent=2, ensure_ascii=False))

    def list(self) -> List[EditorServer]:
        with self._lock:
            return self._read()

    def find(self, key: str) -> Optional[EditorServer]:
        """An entry by its id or, when exactly one has it, by its name."""
        servers = self.list()
        for server in servers:
            if server.id == key:
                return server
        named = [s for s in servers if s.name == key]
        return named[0] if len(named) == 1 else None

    def record(self, entries: Iterable[Any], editor: str = "") -> List[EditorServer]:
        """Records the `mcpServers` of one session (new ones disabled, known ones keep the person's choice) and returns
        their records in the given order. Never connects to any of them."""
        seen = [describe_entry(e) for e in entries]
        if not seen:
            return []
        now = _now()
        with self._lock:
            servers = self._read()
            by_id = {s.id: s for s in servers}
            result = []
            for item in seen:
                key = entry_id(item["transport"], item["name"], item["command"], item["args"], item["url"])
                server = by_id.get(key)
                if server is None:
                    server = EditorServer(id=key, first_seen=now, **item)
                    servers.append(server)
                    by_id[key] = server
                server.last_seen, server.editor = now, _clean(editor, 120)
                server.env_names = item["env_names"]
                result.append(server)
            if len(servers) > MAX_ENTRIES:   # the person's enabled ones stay; the oldest others go first
                enabled = [s for s in servers if s.enabled]
                others = sorted((s for s in servers if not s.enabled), key=lambda s: s.last_seen, reverse=True)
                servers = enabled + others[:max(0, MAX_ENTRIES - len(enabled))]
            self._write(servers)
            return result

    def set_enabled(self, key: str, enabled: bool) -> Optional[EditorServer]:
        """The person enables or disables an entry (by id, or by a name only one entry has). None when unknown."""
        with self._lock:
            servers = self._read()
            target = next((s for s in servers if s.id == key), None)
            if target is None:
                named = [s for s in servers if s.name == key]
                target = named[0] if len(named) == 1 else None
            if target is None:
                return None
            target.enabled = bool(enabled)
            self._write(servers)
            return target


def session_notice(records: List[EditorServer]) -> str:
    """What the editor's person reads when the editor passed MCP servers: they are recorded, none is used."""
    if not records:
        return ""
    names = ", ".join(r.name for r in records[:8]) + (" ..." if len(records) > 8 else "")
    enabled = sum(1 for r in records if r.enabled)
    text = (f"The editor passed {len(records)} MCP server(s) ({names}). The framework recorded them and connects to "
            "none: their tools would bypass its permissions and audit log. You see them in the framework's settings, "
            "on the tab Ditto in editors.")
    if enabled:
        text += (f" {enabled} of them you enabled there; this version still connects to none (enabling is your "
                 "consent for a later version).")
    return text


__all__ = ["EditorServer", "EditorServers", "FILE_NAME", "describe_entry", "entry_id", "session_notice"]
