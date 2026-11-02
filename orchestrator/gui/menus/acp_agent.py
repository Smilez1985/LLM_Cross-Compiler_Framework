#!/usr/bin/env python3
"""
Ditto as an ACP agent in the GUI (W1' parity for `llm-cli acp`): File > Settings... > Ditto in editors
(orchestrator/gui/acp_settings.py) shows the editor entry with Copy buttons and the MCP servers editors passed to
Ditto with a consent box each. The tab lives in the existing File > Settings... dialog, so this module only names the
mouse paths; it installs no menu entry of its own.
"""

from typing import Dict

SETTINGS = "act_file_settings"   # File > Settings... (orchestrator/gui/menus/build.py)

CLI_PATHS: Dict[str, str] = {
    "acp config": SETTINGS,                       # the command and the agent_servers entry, each with Copy
    "acp config --json": SETTINGS,                # the entry's own field and its Copy button
    "acp servers list": SETTINGS,                 # the table of recorded editor MCP servers
    "acp servers list --json": SETTINGS,
    "acp servers enable": SETTINGS,               # the consent box of an entry
    "acp servers disable": SETTINGS,
    "acp --login": "act_signin_overview",         # Security > Sign-in Overview... (the same sign-in)
}
MACHINE_ONLY: Dict[str, str] = {
    "acp": "the editor starts it itself (stdin/stdout carry the protocol) with the entry from the settings' tab Ditto "
           "in editors",
}


def install(window, menubar) -> None:
    """Nothing to install: the tab is part of File > Settings...."""
    return None
