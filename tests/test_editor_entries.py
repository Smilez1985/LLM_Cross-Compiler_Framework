"""The editor entries the setup guide (docs/Editors_and_Agents.md) points to, built from the one launch command
(utils/stdio_programs.launch_command):

- Zed's own formats: `context_servers` for the framework's MCP server (`llm-cli mcp config`, Security > AI App
  Connections (MCP)...) and `agent_servers` with `"type": "custom"` for Ditto as an ACP agent (`llm-cli acp config`,
  File > Settings... > Ditto in editors);
- the JetBrains `acp.json` entry stays the generic `agent_servers` entry it was.
"""

import json
import sys
from pathlib import Path, PureWindowsPath

import pytest

from conftest import REPO_ROOT
from orchestrator import mcp_server
from orchestrator.utils.stdio_programs import ACP_AGENT, MCP_SERVE, acp_entries, launch_command

ROOT = Path("/opt/llm-framework")


def _without_cwd(launch):
    return {"command": launch["command"], "args": launch["args"], "env": launch["env"]}


def test_zed_gets_the_mcp_server_as_a_context_server():
    snippets = mcp_server.client_snippets(ROOT, windows=False)
    zed = json.loads(snippets["zed"])
    entry = zed["context_servers"][mcp_server.SERVER_NAME]
    # Zed's context_servers entry has no working folder: -P and PYTHONPATH are what the launch needs.
    assert entry == _without_cwd(launch_command(ROOT, False, MCP_SERVE))
    assert entry["args"][-2:] == ["mcp", "serve"] and entry["env"]["PYTHONPATH"] == "/opt/llm-framework"


def test_zed_gets_ditto_as_a_custom_agent_and_jetbrains_keeps_its_entry():
    entries = acp_entries(ROOT, windows=False)
    zed = json.loads(entries["zed"])["agent_servers"]["llmcf"]
    assert zed == {"type": "custom", **_without_cwd(launch_command(ROOT, False, ACP_AGENT))}
    jetbrains = json.loads(entries["json"])["agent_servers"]["llmcf"]
    assert "type" not in jetbrains and jetbrains["args"][-1] == "acp"


def test_the_zed_entries_follow_windows_paths():
    root = PureWindowsPath(r"C:\Program Files\llmcf")
    mcp = json.loads(mcp_server.client_snippets(root, windows=True)["zed"])["context_servers"]
    agent = json.loads(acp_entries(root, windows=True)["zed"])["agent_servers"]["llmcf"]
    for entry in (mcp[mcp_server.SERVER_NAME], agent):
        assert entry["command"] == sys.executable and entry["env"]["PYTHONPATH"] == str(root)


def test_the_cli_prints_the_zed_entries(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from orchestrator import cli
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
    runner = CliRunner()
    shown = runner.invoke(cli.cli, ["-q", "mcp", "config"])
    assert shown.exit_code == 0, shown.output
    assert "# Zed (settings.json, context_servers)" in shown.output
    assert mcp_server.client_snippets(mcp_server.installation_root())["zed"] in shown.output
    shown = runner.invoke(cli.cli, ["-q", "acp", "config"])
    assert shown.exit_code == 0, shown.output
    assert "# Zed (settings.json, agent_servers)" in shown.output
    assert "# JetBrains IDEs (~/.jetbrains/acp.json, agent_servers)" in shown.output
    entries = acp_entries(mcp_server.installation_root())
    assert entries["zed"] in shown.output and entries["json"] in shown.output
    only = runner.invoke(cli.cli, ["-q", "acp", "config", "--json"])
    assert json.loads(only.output) == json.loads(entries["json"]), "--json keeps printing the generic entry"


def test_the_gui_shows_the_zed_entries_with_copy_buttons(qapp, tmp_path):
    pytest.importorskip("PySide6")
    from types import SimpleNamespace

    from PySide6.QtGui import QGuiApplication
    from orchestrator.gui.acp_settings import AcpSettings
    framework = SimpleNamespace(config=SimpleNamespace(user_state_dir=str(tmp_path / "state")))
    tab = AcpSettings(framework, lang="en", app_root=REPO_ROOT)
    assert tab.text_zed.toPlainText() == acp_entries(REPO_ROOT)["zed"] and tab.text_zed.isReadOnly()
    tab.btn_copy_zed.click()
    assert QGuiApplication.clipboard().text() == acp_entries(REPO_ROOT)["zed"]
    tab.close()

