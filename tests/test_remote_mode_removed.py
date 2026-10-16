"""The remote mode is gone (DECISIONS round 8, "HYBRID CLARIFIED"): Ditto's hands are the framework's own tools on
this computer, the same ones every MCP client gets; Ditto never connects to a framework on another computer.

- The modules, the `llm-cli ditto remote` group, the GUI section and the settings are removed; nothing imports them.
- An older config.yml with the remote settings still loads: the settings are ignored with one log line, the others
  apply.
- A saved chat (ChatHistory version 3) with the remote fields still loads; a conversation of the remote mode starts
  anew once, saying that Ditto now uses its own tools.
- The signed-file kind of the remote mode's place index is gone; an old ditto_places.json is never read.
- Board deployment over SSH is not part of the remote mode and stays (its tests run in the full suite).
"""

import json
import logging
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT
from orchestrator.Core.ditto_manager import SOURCE_LOCAL_NOTICE, ChatHistory, ChatReply, DittoCoder, ToolUse
from orchestrator.llm.types import ChatResult, Message

REMOVED_MODULES = ("orchestrator/llm/remote_tools.py", "orchestrator/tools/remote_audit.py",
                   "orchestrator/llm/hybrid_tools.py")
RETIRED = ("ai_ditto_tool_source", "ai_ditto_mcp_transport", "ai_ditto_mcp_command", "ai_ditto_mcp_url",
           "ai_ditto_mcp_name")


# --- the code is gone -------------------------------------------------------------------------------------------------

def test_the_remote_modules_are_gone_and_nothing_imports_them():
    for name in REMOVED_MODULES:
        assert not (REPO_ROOT / name).exists(), name
    pattern = re.compile(r"\b(remote_tools|remote_audit|hybrid_tools)\b")
    offenders = [str(p.relative_to(REPO_ROOT)) for p in (REPO_ROOT / "orchestrator").rglob("*.py")
                 if pattern.search(p.read_text(encoding="utf-8"))]
    assert offenders == []


def test_the_ditto_remote_command_is_gone():
    from click.testing import CliRunner
    from orchestrator import cli
    assert "remote" not in cli.ditto.commands
    result = CliRunner().invoke(cli.cli, ["-q", "ditto", "remote", "show"])
    assert result.exit_code != 0 and "No such command" in result.output


def test_ditto_has_no_connection_to_another_computer():
    ditto = DittoCoder(None)
    assert not hasattr(ditto, "close") and not hasattr(ditto, "_remote_task")
    assert "remote_client_factory" not in DittoCoder.__init__.__code__.co_varnames
    assert "request_id" not in DittoCoder.analyze_error_log.__code__.co_varnames
    assert not hasattr(ToolUse("t", "T", True), "place")


# --- an older config ---------------------------------------------------------------------------------------------------

class Lines(logging.Handler):
    def __init__(self):
        super().__init__(logging.INFO)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def test_an_old_config_with_the_remote_settings_still_loads_and_ignores_them(tmp_path):
    from orchestrator.Core import config_manager
    from orchestrator.Core.config_manager import RETIRED_SETTINGS, ConfigManager
    assert set(RETIRED) <= set(RETIRED_SETTINGS)
    (tmp_path / "config.yml").write_text(
        "ai_ditto_tool_source: mcp\n"
        "ai_ditto_mcp_transport: stdio\n"
        "ai_ditto_mcp_command: [ssh, -T, build-box, llm-cli, mcp, serve]\n"
        "ai_ditto_mcp_name: build box\n"
        "ai_effort: high\n", encoding="utf-8")
    logger, handler = logging.getLogger(config_manager.__name__), Lines()
    level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        config = ConfigManager(tmp_path)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)
    for key in RETIRED:
        assert config.get(key) is None, key
        assert key not in config.config_schemas, key
    assert config.get("ai_effort") == "high", "the other settings of the file apply"
    lines = [line for line in handler.lines if "removed feature" in line]
    assert len(lines) == 1, lines
    assert "ai_ditto_tool_source" in lines[0] and "build box" not in lines[0], "names the keys, never the values"


def test_a_saved_config_never_writes_the_remote_settings(tmp_path):
    from orchestrator.Core.config_manager import ConfigManager
    config = ConfigManager(tmp_path)
    config.set("ai_ditto_tool_source", "mcp")
    config.set("ai_effort", "low")
    assert config.save_user_config()
    text = (tmp_path / "config.yml").read_text(encoding="utf-8")
    assert "ai_ditto" not in text and "ai_effort: low" in text


# --- an older chat -----------------------------------------------------------------------------------------------------

def remote_era_history(path: Path, source: str) -> Path:
    path.write_text(json.dumps({
        "version": 3, "tool_source": source,
        "messages": [{"role": "user", "content": "Build it on the build box."},
                     {"role": "assistant", "content": "Started req_1a2b3c4d there."}],
        "transcript": [{"role": "user", "text": "Build it on the build box."},
                       {"role": "tool", "text": "Used on build box: Start a build", "place": "build box"},
                       {"role": "assistant", "text": "Started req_1a2b3c4d there."}]}), encoding="utf-8")
    return path


class Model:
    def __init__(self):
        self.requests = []

    def chat(self, request):
        self.requests.append(request)
        return ChatResult(text="Hello.", message=Message(role="assistant", content="Hello."))


def ditto_for(model):
    config = SimpleNamespace(get=lambda key, default=None: default)
    return DittoCoder(None, config_manager=config, client=model)


def test_a_version_3_history_with_the_remote_fields_loads(tmp_path):
    history = ChatHistory.load(remote_era_history(tmp_path / "chat_history.json", "local"))
    assert [t.text for t in history.transcript][1] == "Used on build box: Start a build", "the text stays"
    assert len(history.messages) == 2 and not history.migrating()
    assert not (tmp_path / "chat_history.json.bad").exists()
    model = Model()
    reply = ditto_for(model).chat("And now?", history, use_tools=False)
    assert not reply.source_changed
    assert len(model.requests[0].messages) == 3, "a local conversation goes on"
    history.apply(reply)
    assert history.save()
    saved = json.loads((tmp_path / "chat_history.json").read_text(encoding="utf-8"))
    assert saved["version"] == 3 and all("place" not in t for t in saved["transcript"])


def test_a_conversation_of_the_remote_mode_starts_anew_once(tmp_path):
    history = ChatHistory.load(remote_era_history(tmp_path / "chat_history.json", "mcp:build box"))
    assert history.migrating()
    model = Model()
    reply = ditto_for(model).chat("Hi", history, use_tools=False)
    assert reply.source_changed and len(model.requests[0].messages) == 1, "its turns named that computer's objects"
    history.apply(reply)
    assert SOURCE_LOCAL_NOTICE in [t.text for t in history.transcript if t.role == "notice"]
    assert "build box" not in SOURCE_LOCAL_NOTICE and not history.migrating()
    second = ditto_for(Model()).chat("Again", history, use_tools=False)
    assert not second.source_changed, "only once"


def test_the_reply_carries_no_remote_fields():
    fields = set(ChatReply.__dataclass_fields__)
    assert not fields & {"places", "remote_state", "notices", "tool_source"}


# --- signed files ------------------------------------------------------------------------------------------------------

def test_the_place_index_kind_is_gone_and_an_old_file_is_never_read(tmp_path, monkeypatch):
    from orchestrator.tools import signing
    assert "ditto_places" not in signing.KINDS
    with pytest.raises(ValueError):
        signing.sign("ditto_places", {"entries": {}}, None)
    old = tmp_path / "ditto_places.json"
    old.write_text('{"entries": {}, "signature": {"key_id": "unkeyed", "mac": "0"}}', encoding="utf-8")
    opened = []
    real_open = Path.open

    def watching(self, *args, **kwargs):
        opened.append(self.name)
        return real_open(self, *args, **kwargs)
    monkeypatch.setattr(Path, "open", watching)
    reply = ditto_for(Model()).chat("Hi", ChatHistory(tmp_path / "chat_history.json"), use_tools=False)
    assert reply.text == "Hello." and "ditto_places.json" not in opened
    assert old.exists(), "nothing deletes the person's old file"


# --- the GUI ------------------------------------------------------------------------------------------------------------

pytest.importorskip("PySide6")
from test_dialogs import FakeFramework, collect_widgets_on_the_main_thread, model_lists  # noqa: E402,F401  (fixtures)

def test_the_ai_settings_have_no_remote_section(qapp, tmp_path, model_lists, collect_widgets_on_the_main_thread):
    from orchestrator.Core.config_manager import ConfigManager
    from orchestrator.gui.dialogs import AIConfigurationDialog
    dlg = AIConfigurationDialog(FakeFramework(ConfigManager(tmp_path)))
    for name in ("cb_tool_source", "cb_mcp_transport", "txt_mcp_command", "txt_mcp_url", "txt_mcp_token",
                 "lbl_remote_intro"):
        assert not hasattr(dlg, name), name
    source = (REPO_ROOT / "orchestrator" / "gui" / "dialogs.py").read_text(encoding="utf-8")
    assert "Another computer (optional)" not in source and "_remote_section" not in source
    dlg.deleteLater()


def test_ditto_confirmation_never_offers_to_ask_another_framework():
    from orchestrator.gui import tool_confirmation
    source = Path(tool_confirmation.__file__).read_text(encoding="utf-8")
    assert "stop asking" not in source.lower() and not hasattr(tool_confirmation, "STOP_ASKING")
