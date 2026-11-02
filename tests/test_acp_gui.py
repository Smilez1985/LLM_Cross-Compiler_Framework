"""The ACP agents the person picks, by mouse and at the command line: the chat panel's agent picker (Ditto stays the
default), a turn with the fake ACP agent streamed into the panel, its permission question answered once, the window
Ditto > Agents... (add an own command after the question, remove through the deletion list) and `llm-cli agents`."""

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT

pytest.importorskip("PySide6")
acp = pytest.importorskip("acp")

pytestmark = pytest.mark.usefixtures("qapp")

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "acp" / "fake_agent.py"
ACP_HOME = str(Path(acp.__file__).resolve().parent.parent)


class Audit:
    def __init__(self):
        self.rows = []

    def record(self, actor, tool, tier, decision, outcome, **kwargs):
        self.rows.append({"actor": actor, "tool": tool, "outcome": outcome, **kwargs})
        return len(self.rows)


class Vault:
    def __init__(self):
        self.values = {}

    def set_secret(self, name, value):
        self.values[name] = value
        return True

    def get_secret(self, name):
        return self.values.get(name)

    def delete_secret(self, name):
        self.values.pop(name, None)
        return True

    def list_secrets(self):
        return list(self.values)


class FakeDitto:
    def describe(self, task="chat"):
        return {"label": "Ollama (local)", "local": True, "auth_type": "none", "auth_source": "none"}


@pytest.fixture
def framework(tmp_path):
    from orchestrator.Core.config_manager import ConfigManager
    audit = Audit()
    config = ConfigManager(tmp_path / "configs")
    return SimpleNamespace(config=config, ditto_manager=FakeDitto(), chat_feed=None,
                           tool_registry=SimpleNamespace(audit=audit), secrets_manager=Vault(), job_service=None)


def add_fake(framework, tmp_path, name="Fake Agent"):
    from orchestrator.Core.acp_hosts import AgentStore
    store = AgentStore.for_framework(framework)
    report = tmp_path / "report.json"
    preview = store.preview_command(name, sys.executable, [str(FIXTURE), str(report), "--path", ACP_HOME],
                                    local=True)
    store.add(preview, "gui")
    return store, report


def wait_until(qapp, condition, seconds=30.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qapp.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def panel(framework):
    from orchestrator.gui import chat_window
    made = []

    def make():
        p = chat_window.ChatWindow(framework)
        made.append(p)
        return p
    yield make
    for p in made:
        p.close()
        p.deleteLater()


# --- the picker ------------------------------------------------------------------------------------------------------

def test_ditto_stays_the_default_and_the_agents_are_offered_beside_it(framework, tmp_path, panel):
    add_fake(framework, tmp_path)
    p = panel()
    combo = p.findChild(type(p.agent_picker.combo), "cb_chat_agent")
    assert combo is not None and combo.currentIndex() == 0 and combo.currentData() == ""
    assert [combo.itemText(i) for i in range(combo.count())] == ["Ditto", "Fake Agent"]
    assert p.agent_picker.current() == ""
    p.agent_picker.combo.setCurrentIndex(1)
    assert p.options.isHidden()            # Ditto's options are for Ditto
    p.agent_picker.combo.setCurrentIndex(0)
    assert not p.options.isHidden()


def test_a_turn_with_an_agent_streams_into_the_panel_and_ends_as_its_bubble(qapp, framework, tmp_path, panel):
    store, report = add_fake(framework, tmp_path)
    p = panel()
    p.agent_picker.combo.setCurrentIndex(1)
    assert p.composer.btn_send.isEnabled()
    p.txt_input.setText("hello")
    p.send_message()
    assert wait_until(qapp, lambda: not p.agents.running), "the turn ended"
    rows = [s.entry for s in p.feed_view.stored()]
    replies = [e for e in rows if e.kind == "client_reply"]
    assert replies and replies[-1].ai_text == "Hello" and replies[-1].sender.role == "client"
    assert replies[-1].sender.client == "Fake Agent"
    assert any(e.kind == "person_message" and e.person_text == "hello" for e in rows)
    assert p.agent_live.thought_count == 1 and p.agent_live.tools == {"t1": ["List the builds", "completed"]}
    assert p.agent_live.isHidden()
    [session] = p.agents.sessions.values()
    assert session.running()
    p.close()                               # the panel's close stops the agent it started
    assert not session.running() and p.agents.sessions == {}
    assert any(r.get("arguments", {}).get("what") == "stopped" for r in store.audit.rows)


@pytest.mark.parametrize("answer, chosen", [("allow", "yes"), ("reject", "no")])
def test_a_permission_question_is_answered_in_the_frameworks_own_dialog(qapp, framework, tmp_path, panel, answer,
                                                                         chosen):
    _store, report = add_fake(framework, tmp_path)
    p = panel()
    asked = []
    p.agents.answer_permission = lambda name, question: asked.append((name, question)) or answer
    p.agent_picker.combo.setCurrentIndex(1)
    p.txt_input.setText("ask")
    p.send_message()
    assert wait_until(qapp, lambda: not p.agents.running)
    assert json.loads(report.read_text(encoding="utf-8"))["permission"] == [chosen]
    assert asked[0][0] == "Fake Agent" and asked[0][1].has_always


def test_the_permission_dialog_offers_once_and_reject_and_points_to_the_approvals(qapp):
    from orchestrator.Core.acp_client import PermissionQuestion
    from orchestrator.gui.chat_panel.agent_chat import PermissionDialog
    opened = []
    dialog = PermissionDialog("Fake Agent", PermissionQuestion("Delete a build", "", True, "yes", "no"),
                              open_approvals=lambda: opened.append(True))
    assert dialog.btn_reject.isDefault() and not dialog.btn_allow.isDefault()
    dialog.btn_always.click()
    assert opened == [True] and dialog.answer == "reject"     # the approvals window, never an "always" answer
    dialog.btn_allow.click()
    assert dialog.answer == "allow"


def test_an_agent_without_allow_once_gets_no_allow_button(qapp):
    """The agent offers only "always" and "reject": the dialog offers no "Allow once" the client could not send."""
    from orchestrator.Core.acp_client import PermissionQuestion
    from orchestrator.gui.chat_panel.agent_chat import PermissionDialog
    dialog = PermissionDialog("Fake Agent", PermissionQuestion("Delete every build", "", True, "", "no"),
                              open_approvals=lambda: None)
    assert not dialog.btn_allow.isEnabled() and dialog.btn_reject.isDefault()
    dialog.btn_allow.click()
    assert dialog.answer == "reject"


def test_the_terminal_does_not_ask_allow_once_when_the_agent_offers_none(monkeypatch):
    from orchestrator import cli
    from orchestrator.Core.acp_client import PermissionQuestion
    asked = []
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr(cli.click, "confirm", lambda *a, **k: asked.append(a) or True)
    host = cli._TerminalAgentHost("Fake Agent")
    assert host.ask_permission(PermissionQuestion("Delete every build", "", True, "", "no")) == "reject"
    assert asked == []
    assert host.ask_permission(PermissionQuestion("Delete a build", "", True, "yes", "no")) == "allow"


# --- the window ------------------------------------------------------------------------------------------------------

def test_the_window_adds_an_own_command_after_the_question_and_removes_it_through_the_list(qapp, framework, tmp_path,
                                                                                            monkeypatch):
    from PySide6.QtWidgets import QDialog
    from orchestrator.gui import acp_agents_window as module
    from orchestrator.gui import deletion_dialog
    shown = []

    def answer(self):
        shown.append(self)
        return 1 if isinstance(self, module.AddAgentDialog) else 0
    monkeypatch.setattr(QDialog, "exec", answer)
    window = module.ManageAgentsWindow(framework)
    for name in ("tbl_acp_agents", "tbl_acp_registry", "btn_acp_refresh", "btn_acp_add_registry", "cb_acp_kind",
                 "txt_acp_command", "txt_acp_name", "txt_acp_args", "chk_acp_local", "txt_acp_keys",
                 "btn_acp_remove", "btn_acp_login", "btn_acp_key"):
        assert window.findChild(object, name) is not None, name
    window.txt_name.setText("Own Agent")
    window.txt_command.setText(sys.executable)
    window.txt_args.setText("-c pass")
    window.chk_local.setChecked(True)
    window.btn_add_command.click()
    assert wait_until(qapp, lambda: not window.busy and window.tbl_agents.rowCount() == 1)
    question = shown[0]
    text = question.plain_text()
    assert "Own Agent" in question.windowTitle() and "Command:" in text and "-c pass" in text
    assert question.btn_no.isDefault()
    assert window.tbl_agents.item(0, 0).text() == "Own Agent"

    asked = []
    monkeypatch.setattr(deletion_dialog, "ask_deletion",
                        lambda parent, plan, **kw: asked.append(plan) or None)   # "No": nothing is removed
    window.tbl_agents.selectRow(0)
    window.btn_remove.click()
    assert asked and [i.speaking_name for i in asked[0].items][-1] == "Own Agent"
    assert window.tbl_agents.rowCount() == 1

    from orchestrator.Core.deletion import accept
    monkeypatch.setattr(deletion_dialog, "ask_deletion", lambda parent, plan, **kw: accept(plan, "gui", "permanent"))
    window.btn_remove.click()
    assert window.tbl_agents.rowCount() == 0
    window.close()


def test_the_registry_is_fetched_only_on_the_click(qapp, framework, monkeypatch):
    from orchestrator.gui import acp_agents_window as module
    window = module.ManageAgentsWindow(framework)
    fetched = []
    data = json.dumps({"version": "1.0.0", "agents": [
        {"id": "npx-agent", "name": "Npx Agent", "version": "2.0.0", "license": "MIT",
         "distribution": {"npx": {"package": "pkg@2.0.0"}}}]}).encode()
    real = window.store.refresh_registry
    monkeypatch.setattr(window.store, "refresh_registry",
                        lambda actor, fetch=None: fetched.append(actor) or real(actor, fetch=lambda url: data))
    assert window.tbl_registry.rowCount() == 0 and fetched == []
    window.btn_refresh.click()
    assert wait_until(qapp, lambda: window.tbl_registry.rowCount() == 1)
    assert fetched == ["gui"] and window.tbl_registry.item(0, 0).text() == "Npx Agent"
    window.close()


def test_the_sign_in_closes_the_agent_off_the_gui_thread(qapp, framework, monkeypatch):
    """Closing an agent may wait for it to stop (up to several seconds for one that ignores its closed input); the
    window's sign-in never does that on the GUI thread."""
    import threading
    from orchestrator.gui import acp_agents_window as module
    window = module.ManageAgentsWindow(framework)
    threads = []

    class Session:
        def close(self):
            threads.append(threading.current_thread())

    spec = SimpleNamespace(entry=SimpleNamespace(name="Fake Agent"))
    window._choose_sign_in(spec, Session(), [])          # no sign-in methods: the started agent is closed again
    assert wait_until(qapp, lambda: bool(threads), 10)
    assert threads[0] is not threading.main_thread()
    window.close()


# --- the command line ------------------------------------------------------------------------------------------------

def test_llm_cli_agents_add_list_and_run(framework, tmp_path, monkeypatch):
    from click.testing import CliRunner
    from orchestrator import cli
    from orchestrator.Core.acp_hosts import AgentStore
    store = AgentStore.for_framework(framework)
    monkeypatch.setattr(cli, "_agent_store", lambda ctx, boot=False: store)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    report = tmp_path / "cli-report.json"
    runner = CliRunner()
    added = runner.invoke(cli.cli, ["agents", "add", "--command", sys.executable, "--name", "Cli Agent", "--local",
                                    "--arg", str(FIXTURE), "--arg", str(report), "--arg", "--path",
                                    "--arg", ACP_HOME], input="y\n")
    assert added.exit_code == 0, added.output
    assert "Command:" in added.output and "was added" in added.output
    listed = runner.invoke(cli.cli, ["-q", "agents", "list", "--json"])
    assert [a["name"] for a in json.loads(listed.output)["agents"]] == ["Cli Agent"]
    ran = runner.invoke(cli.cli, ["agents", "run", "Cli Agent"], input="ask\ny\n\n")
    assert ran.exit_code == 0, ran.output
    assert "answer=yes" in ran.output
    assert json.loads(report.read_text(encoding="utf-8"))["permission"] == ["yes"]


def test_llm_cli_agents_add_says_no_by_default(framework, monkeypatch):
    from click.testing import CliRunner
    from orchestrator import cli
    from orchestrator.Core.acp_hosts import AgentStore
    store = AgentStore.for_framework(framework)
    monkeypatch.setattr(cli, "_agent_store", lambda ctx, boot=False: store)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    result = CliRunner().invoke(cli.cli, ["agents", "add", "--command", sys.executable, "--name", "X"], input="\n")
    assert "Nothing was added." in result.output and store.list() == []
