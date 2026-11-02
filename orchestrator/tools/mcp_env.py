"""
The environment of a stdio MCP server: the variable that carries a stdio client's MCP token, the variable that names
the channel of a server the framework hands one of the person's ACP agents, and how a client starts the server
(launch_command).

It has its own module so `llm-cli` can take the token out of the environment before any command runs without
importing the token store (lazy loading); orchestrator.tools.mcp_tokens re-exports ENV_VAR. launch_command and
installation_root live here (kernel layer) so the Core can build the stdio entry it hands an ACP agent
(Core/acp_hosts.py) without importing the MCP adapter; orchestrator.mcp_server keeps both names and delegates here.
"""

import ntpath
import posixpath
import re
import sys
from pathlib import Path, PureWindowsPath
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
    """The framework's installation (the folder that holds orchestrator/): what the client snippets put on
    PYTHONPATH, for the CLI and the GUI alike."""
    return Path(__file__).resolve().parents[2]


def launch_command(app_root: Path, windows: Optional[bool] = None) -> Dict[str, Any]:
    """How a client starts this server over stdio (the current interpreter, so the venv is used).
    -P keeps the folder the client starts it in off sys.path; the installation comes from PYTHONPATH. The POSIX
    variant follows POSIX path rules on every host: a path the caller made on Windows ('\\opt\\llm' from
    Path('/opt/llm')) is written with '/' again."""
    windows = (sys.platform == "win32") if windows is None else windows
    root = PureWindowsPath(app_root).as_posix() if not windows and isinstance(app_root, PureWindowsPath) \
        else str(app_root)
    separator = ";" if windows else ":"
    # A relative entry, or one that the separator splits, would be looked up in the working directory.
    if separator in root or not (ntpath if windows else posixpath).isabs(root):
        raise ValueError(f"The installation path {root!r} cannot be put on PYTHONPATH safely (it is relative or "
                         f"contains '{separator}'); install the framework to another folder.")
    return {"command": sys.executable, "args": ["-P", "-m", "orchestrator.cli", "-q", "mcp", "serve"],
            "cwd": root, "env": {"PYTHONPATH": root}}


__all__ = ["CHANNEL_ENV", "CHANNEL_PREFIX", "ENV_VAR", "channel_label", "checked_channel", "installation_root",
           "launch_command"]
