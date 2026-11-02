"""
The environment of a stdio MCP server: the variable that carries a stdio client's MCP token, the variable that names
the channel of a server the framework hands one of the person's ACP agents.

It has its own module so `llm-cli` can take the token out of the environment before any command runs without
importing the token store (lazy loading); orchestrator.tools.mcp_tokens re-exports ENV_VAR. How a client starts the
server (launch_command, installation_root) lives in orchestrator/utils/stdio_programs.py, the one implementation the
MCP server's entries, Ditto as an ACP agent and the entry the ACP client hands an agent (Core/acp_hosts.py) share;
the two names here stay as deprecated delegates.
"""

import re
from pathlib import Path
from typing import Any, Dict, Optional

ENV_VAR = "LCF_MCP_TOKEN"      # the token of a stdio client, read once by `llm-cli mcp serve`
# The channel label of a stdio server the framework hands one of the person's ACP agents (session/new.mcpServers):
# "acp-client:<agent name>". `llm-cli mcp serve` reads it once and removes it from its environment; the MCP
# ClientTracker puts it in front of the client's self-reported name in every log line and audit entry. A label for
# people, never a reason to allow anything (the caller and its permissions stay those of the stdio server).
CHANNEL_ENV = "LCF_MCP_CHANNEL"
CHANNEL_PREFIX = "acp-client:"
_CHANNEL_NAME = re.compile(r"[^A-Za-z0-9 ._()-]")


def channel_label(agent_name: Any) -> str:
    """The channel label for an agent's name: "acp-client:" and the name in plain letters (at most 64 characters;
    every other character becomes "_"), "" for an empty name."""
    name = _CHANNEL_NAME.sub("_", str(agent_name or "")).strip()[:64].strip()
    return f"{CHANNEL_PREFIX}{name}" if name else ""


def checked_channel(value: Any) -> str:
    """A channel label read from the environment when it is one channel_label makes; "" for anything else."""
    text = str(value or "")
    if not text.startswith(CHANNEL_PREFIX):
        return ""
    return text if channel_label(text[len(CHANNEL_PREFIX):]) == text else ""


def installation_root() -> Path:
    """Deprecated name: the one implementation is orchestrator/utils/stdio_programs.installation_root (shared by the
    MCP server, Ditto as an ACP agent and the ACP client); this name stays for its callers."""
    from orchestrator.utils.stdio_programs import installation_root as root
    return root()


def launch_command(app_root: Path, windows: Optional[bool] = None) -> Dict[str, Any]:
    """Deprecated name: how a client starts the MCP server over stdio. The one implementation is
    orchestrator/utils/stdio_programs.launch_command (its default command is `mcp serve`); this name stays for its
    callers."""
    from orchestrator.utils.stdio_programs import launch_command as launch
    return launch(app_root, windows)


__all__ = ["CHANNEL_ENV", "CHANNEL_PREFIX", "ENV_VAR", "channel_label", "checked_channel", "installation_root",
           "launch_command"]
