"""Ditto and the framework on another computer: the wording (design WP2, 6.1; DECISIONS round 5 d and h).

- "Another framework" is the same program on a second computer; the GUI section is optional, off by default, and
  says in plain text that Docker is not needed (SSH or a server address is enough).
- No text of the section, of `llm-cli ditto remote` or of Ditto's connection errors suggests a published framework
  image ("the headless Docker image") or names Docker as the way to connect: SSH comes first, a container you built
  yourself is one alternative.
"""

import re
from types import SimpleNamespace

import pytest

pytest.importorskip("mcp")
pytest.importorskip("PySide6")

from orchestrator.llm import remote_tools  # noqa: E402
from orchestrator.llm.remote_tools import RemoteSetupError  # noqa: E402

from test_ditto_remote_gui import gui, open_dialog  # noqa: E402,F401  (gui: the fixture)

# What a person reads; the code may keep the technical name "MCP-client mode" in docstrings.
PUBLISHED_IMAGE = re.compile(r"headless docker image|framework (running )?elsewhere", re.I)
OLD_WORDING = re.compile(PUBLISHED_IMAGE.pattern + r"|mcp-client mode", re.I)


def section_texts(dlg):
    """Every text the remote section shows: titles, labels, items, placeholders and tooltips."""
    box = dlg.lbl_remote_intro.parentWidget()
    from PySide6.QtWidgets import QComboBox, QLabel, QLineEdit
    texts = [box.title()]
    texts += [w.text() for w in box.findChildren(QLabel)]
    for combo in box.findChildren(QComboBox):
        texts += [combo.itemText(i) for i in range(combo.count())] + [combo.toolTip()]
    for edit in box.findChildren(QLineEdit):
        texts += [edit.placeholderText(), edit.toolTip()]
    return texts


def test_the_section_is_off_by_default_and_says_docker_is_not_needed(qapp, gui):
    dlg = open_dialog(gui)
    box = dlg.lbl_remote_intro.parentWidget()
    assert box.title() == "Another computer (optional)"
    assert dlg.cb_tool_source.currentData() == "local", "off by default: Ditto works with this computer"
    assert dlg.cb_tool_source.itemText(0) == "This computer (default)"
    assert not dlg.txt_mcp_command.isEnabled() and not dlg.txt_mcp_url.isEnabled()
    assert not dlg.txt_mcp_token.isEnabled()

    intro = dlg.lbl_remote_intro
    from PySide6.QtCore import Qt
    assert intro.textFormat() == Qt.PlainText, "plain text, never rich text"
    assert "Docker is not needed" in intro.text() and "second computer" in intro.text()
    assert "this computer" in intro.text()

    texts = section_texts(dlg)
    assert not [t for t in texts if OLD_WORDING.search(t)], "no old wording and no published image"
    command = dlg.txt_mcp_command.placeholderText()
    assert command.startswith("ssh ") and "SendEnv=LCF_MCP_TOKEN" in command, "SSH first"
    tooltip = dlg.txt_mcp_command.toolTip()
    assert tooltip.index("SSH") < tooltip.index("docker exec"), "Docker only as one alternative, after SSH"
    assert "built yourself" in tooltip
    assert "SSH tunnel" in dlg.txt_mcp_url.toolTip()


def test_switching_on_says_honestly_what_changes(qapp, gui):
    gui.framework.secrets_manager.store["ditto_mcp_token"] = "lcf_mcp_" + "a" * 43
    dlg = open_dialog(gui)
    dlg.cb_tool_source.setCurrentIndex(dlg.cb_tool_source.findData("mcp"))
    note = dlg.lbl_mcp.text()
    assert "instead of this computer's" in note and "stay as they are" in note and "new conversation" in note


def test_a_missing_command_names_ssh_first(qapp, gui):
    dlg = open_dialog(gui)
    dlg.cb_tool_source.setCurrentIndex(dlg.cb_tool_source.findData("mcp"))
    dlg._save_config()
    assert gui.warnings and gui.warnings[0].count("ssh ") == 1 and "docker" not in gui.warnings[0].lower()


def test_the_cli_and_the_connection_errors_name_ssh_first():
    from click.testing import CliRunner
    from orchestrator.cli import cli
    runner = CliRunner()
    for args in (["ditto", "remote", "--help"], ["ditto", "remote", "set", "--help"]):
        text = " ".join(runner.invoke(cli, args).output.split())
        assert not OLD_WORDING.search(text), text
        assert "another computer" in text.lower() or "other computer" in text.lower()
    text = " ".join(runner.invoke(cli, ["ditto", "remote", "--help"]).output.split())
    assert "Docker is not needed" in text and "SSH" in text
    text = " ".join(runner.invoke(cli, ["ditto", "remote", "set", "--help"]).output.split())
    assert text.index("ssh -T") < text.index("docker exec"), "SSH first, Docker as one alternative"

    config = SimpleNamespace(get=lambda key, default=None: {"ai_ditto_tool_source": "mcp",
                                                            "ai_ditto_mcp_transport": "stdio"}.get(key, default))
    with pytest.raises(RemoteSetupError) as missing:
        remote_tools.remote_server(config)
    message = str(missing.value)
    assert message.index("ssh -T") < message.index("docker exec") and "built yourself" in message
    with pytest.raises(RemoteSetupError) as plain:
        remote_tools.check_url("http://build-box.example:8765/mcp")
    assert "SSH tunnel" in str(plain.value) and "container" not in str(plain.value)
    assert not PUBLISHED_IMAGE.search(remote_tools.__doc__ or "")
    assert "Docker is not needed" in (remote_tools.__doc__ or "")
