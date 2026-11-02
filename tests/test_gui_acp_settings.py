"""File > Settings... > Ditto in editors: the editor entry with Copy buttons (no "type this" text) and the MCP servers
editors passed to Ditto, with the person's consent per entry (offscreen)."""

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from conftest import REPO_ROOT  # noqa: E402
from orchestrator.Core.acp_editor_servers import EditorServers  # noqa: E402


def test_the_tab_copies_the_entry_and_records_consent(qapp, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from orchestrator.gui.acp_settings import AcpSettings
    state = tmp_path / "state"
    EditorServers(state).record([{"name": "files", "command": "/usr/bin/files-mcp", "args": ["--root", "/srv"]}],
                                editor="Zed 1.0 (self-reported) over ACP")
    framework = SimpleNamespace(config=SimpleNamespace(user_state_dir=str(state)))
    tab = AcpSettings(framework, lang="en", app_root=REPO_ROOT)
    entry = json.loads(tab.text_json.toPlainText())["agent_servers"]["llmcf"]
    assert entry["args"][-1] == "acp"
    tab.btn_copy_json.click()
    assert json.loads(QGuiApplication.clipboard().text()) == json.loads(tab.text_json.toPlainText())
    tab.btn_copy_command.click()
    assert QGuiApplication.clipboard().text() == tab.edit_command.text() and "orchestrator.cli" in tab.edit_command.text()
    assert tab.table.rowCount() == 1 and tab.table.item(0, 1).text() == "files"
    assert tab.table.item(0, 0).checkState() == Qt.Unchecked, "nothing is enabled without the person"
    tab.table.item(0, 0).setCheckState(Qt.Checked)
    assert EditorServers(state).list()[0].enabled is True
    assert "connects to no editor MCP server" in tab.lbl_result.text()
    tab.table.item(0, 0).setCheckState(Qt.Unchecked)
    assert EditorServers(state).list()[0].enabled is False


def test_the_settings_dialog_has_the_tab(qapp, booted_framework):
    from orchestrator.gui.settings_window import SettingsDialog
    dialog = SettingsDialog(booted_framework, lang="de")
    names = [dialog.tabs.widget(i).objectName() for i in range(dialog.tabs.count())]
    assert "tab_settings_acp" in names
    assert dialog.tabs.tabText(names.index("tab_settings_acp")) == "Ditto in Editoren"
    dialog.close()
