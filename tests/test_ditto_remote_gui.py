"""Ditto's MCP-client mode, round-2 leftovers of T12 (MCP design T12; DECISIONS round 2 items 2 and 4).

- The GUI's AI settings choose the tool source: a command (stdio) or a server address (HTTP), a name, and a named token
  that goes into the credential vault and is never shown again; the mode is refused without a token.
- Ditto's confirmation dialog offers "ask the framework to stop asking" only for a remote question whose form offers
  it, and the answer is only a request to that framework.
- Ditto's task methods in MCP-client mode take their context from that framework's task prompts (the MCP prompts), so
  both modes do the same task with the knowledge of the framework whose tools Ditto uses.
- The reason code of a refused remote call comes from the server's structured error, never from its words.
"""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("mcp")
pytest.importorskip("PySide6")

from conftest import REPO_ROOT  # noqa: E402
from orchestrator.Core.ditto_manager import (OPTIMIZER_SYSTEM, DittoCoder, DittoError, HealingAnalysis,  # noqa: E402
                                             ModuleDefinition)
from orchestrator.llm import remote_tools  # noqa: E402
from orchestrator.llm.remote_tools import RemoteAnswer, RemoteQuestion, structured_error  # noqa: E402
from orchestrator.llm.types import ChatResult, Message  # noqa: E402
from orchestrator.tools.policy import CallerPolicy, Decision, ToolPolicy  # noqa: E402
from orchestrator.tools.registry import PromptArg, PromptSpec, ToolError  # noqa: E402

from test_ditto_remote import TOKEN, Config, Remote, remote  # noqa: E402,F401  (remote: the fixture)

REMOTE_MARK = "REMOTE-CONTEXT-7f3a"   # a value only the remote framework's prompts contain
LOCAL_MARK = "LOCAL-KNOWLEDGE-91c2"    # a value only this framework's knowledge base would give


# --- helpers ------------------------------------------------------------------------------------------------------

class Structured:
    """A scripted model for Ditto's structured tasks: answers with a valid object of the requested output model and
    records each request."""

    ANSWERS = {
        "HealingAnalysis": {"summary": "The download failed.", "root_cause": "No network in the container.",
                            "fix_command": "Check the network of the build machine.", "confidence": 0.5,
                            "target": "HOST"},
        "OptimizerRecommendation": {"format": "gguf", "quantization": "Q4_K_M", "optimization": "BALANCED",
                                    "explanation": "Small and fast."},
        "ModuleDefinition": {"module_name": "Board_X", "architecture": "aarch64", "sdk": "none",
                             "base_os": "debian:bookworm-slim", "packages": ["build-essential"],
                             "cpu_flags": "-mcpu=cortex-a55", "cmake_flags": "", "setup_commands": "",
                             "quantization_logic": ""},
    }

    def __init__(self):
        self.requests = []

    def chat(self, request):
        self.requests.append(request)
        model = request.output_model
        name = next(n for n in self.ANSWERS if any(c.__name__ == n for c in model.__mro__))
        data = dict(self.ANSWERS[name])
        data = {k: v for k, v in data.items() if k in model.model_fields}
        parsed = model.model_validate(data)
        return ChatResult(parsed=parsed, message=Message(role="assistant", content="{}"))

    def user_text(self, index=-1):
        return self.requests[index].messages[0].text()


class Knowledge:
    """This framework's own knowledge base: it must not be consulted in MCP-client mode."""

    def __init__(self):
        self.queries = []

    def search(self, query, limit=5, rerank=None):
        self.queries.append(query)
        return [SimpleNamespace(source="local docs", content=LOCAL_MARK)]


def with_prompts(remote, *names):
    """The remote framework's task prompts as its registry serves them (render = the context message)."""
    seen = []

    def render(name):
        def build(args, ctx):
            seen.append((name, dict(args)))
            return f"{REMOTE_MARK} {name} " + " ".join(f"{k}={v}" for k, v in sorted(args.items()))
        return build

    arguments = {
        "recommend_build_settings": (PromptArg("model", "m", required=True, max_chars=512),
                                     PromptArg("target", "t", required=True, max_chars=64),
                                     PromptArg("board", "b", max_chars=64), PromptArg("use_knowledge", "k", max_chars=8)),
        "add_board": (PromptArg("probe_id", "p", max_chars=64), PromptArg("board", "b", max_chars=1000)),
        "diagnose_build_failure": (PromptArg("request_id", "r", required=True, max_chars=64),),
    }
    for name in names:
        remote.registry.register_prompt(PromptSpec(name, name, name, "Framework instructions.", arguments[name],
                                                   render(name), tools=("read_thing",)))
    reviewed = [s.name for s in remote.registry.list()]
    remote.registry.policy_store.replace(ToolPolicy(configured=True, reviewed=reviewed, callers={
        "ditto": CallerPolicy(overrides={"start_build": Decision.ASK}, reviewed=reviewed)}))
    return seen


def remote_ditto(remote, model, config=None, rag=None):
    framework = SimpleNamespace(config=config or Config(enable_rag_knowledge=True),
                                ensure_rag_manager=lambda: rag, rag_manager=rag, target_manager=None)
    return DittoCoder(framework, config_manager=framework.config, client=model,
                      secrets_manager=SimpleNamespace(get_secret=lambda k: {"ditto_mcp_token": remote.token}.get(k)),
                      remote_client_factory=remote.factory())


def prompt_gets(remote):
    return [e for e in remote.registry.audit.entries(200) if str(e.get("tool", "")).startswith("get_prompt:")]


# --- task methods use the server's prompts ---------------------------------------------------------------------------

class TestRemoteTaskPrompts:

    def test_recommend_build_settings_takes_the_servers_context_and_keeps_dittos_task_text(self, remote):
        seen = with_prompts(remote, "recommend_build_settings")
        rag, model = Knowledge(), Structured()
        answer = remote_ditto(remote, model, rag=rag).recommend_build_settings(
            "Qwen/Qwen3-0.6B", "Rockchip", {"gguf": ["Q4_K_M", "Q8_0"]}, board="rk3588", use_knowledge=True)
        assert answer.format == "gguf" and answer.quantization == "Q4_K_M"
        assert seen == [("recommend_build_settings", {"model": "Qwen/Qwen3-0.6B", "target": "Rockchip",
                                                      "board": "rk3588", "use_knowledge": "yes"})]
        text = model.user_text()
        assert REMOTE_MARK in text and 'source="remote prompt:build box:recommend_build_settings"' in text
        before, _, after = text.partition(REMOTE_MARK)
        assert "<untrusted_data" in before and "</untrusted_data" in after, "the server's words are data"
        assert "Framework instructions." not in text, "a remote server's instructions never steer Ditto"
        assert model.requests[0].system == OPTIMIZER_SYSTEM, "the task text stays Ditto's own (one shared source)"
        assert rag.queries == [] and LOCAL_MARK not in text, "this framework's knowledge base is not used"
        assert {e["actor"] for e in prompt_gets(remote)} == {"mcp:ditto-laptop"}

    def test_the_answer_is_still_checked_against_the_offered_options(self, remote):
        with_prompts(remote, "recommend_build_settings")
        with pytest.raises(DittoError, match="does not offer"):
            remote_ditto(remote, Structured()).recommend_build_settings("m/m", "Rockchip", {"gguf": ["Q8_0"]})

    def test_generate_module_from_a_board_description_uses_add_board(self, remote):
        seen = with_prompts(remote, "add_board")
        model = Structured()
        definition = remote_ditto(remote, model).generate_module(board="Radxa Zero 3W, RK3566")
        assert isinstance(definition, ModuleDefinition)
        assert seen == [("add_board", {"board": "Radxa Zero 3W, RK3566"})]
        assert REMOTE_MARK in model.user_text()

    def test_a_local_probe_comes_along_and_names_the_soc_family_for_the_servers_search(self, remote, tmp_path):
        seen = with_prompts(remote, "add_board")
        probe = tmp_path / "target_hardware_config.txt"
        probe.write_text("CPU_MODEL=Rockchip RK3588\nRAM_TOTAL_MB=8000\n")
        model = Structured()
        remote_ditto(remote, model).generate_module(probe)
        ((_, args),) = seen
        assert "rockchip" in args["board"] and "probe" in args["board"]
        text = model.user_text()
        assert REMOTE_MARK in text and "RK3588" in text and "Hardware probe" in text

    def test_analyze_a_remote_build_by_its_request_id(self, remote):
        seen = with_prompts(remote, "diagnose_build_failure")
        model = Structured()
        analysis = remote_ditto(remote, model).analyze_error_log("", request_id="req_1a2b3c4d")
        assert isinstance(analysis, HealingAnalysis)
        assert seen == [("diagnose_build_failure", {"request_id": "req_1a2b3c4d"})]
        assert REMOTE_MARK in model.user_text()

    def test_a_build_of_this_framework_is_analyzed_here(self, remote):
        with_prompts(remote, "diagnose_build_failure")
        model = Structured()
        remote_ditto(remote, model).analyze_error_log("ERROR: no space left on device", "local build")
        assert prompt_gets(remote) == [] and "no space left" in model.user_text()

    def test_a_prompt_the_server_does_not_offer_is_named_with_the_next_step(self, remote):
        with_prompts(remote, "add_board")  # no recommend_build_settings there
        model = Structured()
        with pytest.raises(DittoError) as exc:
            remote_ditto(remote, model).recommend_build_settings("m/m", "Rockchip", {"gguf": ["Q4_K_M"]})
        assert "recommend_build_settings" in str(exc.value) and "llm-cli ditto remote off" in str(exc.value)
        assert model.requests == [], "nothing reached the model"

    def test_in_process_mode_asks_no_server(self, remote):
        with_prompts(remote, "recommend_build_settings")
        model = Structured()
        ditto = remote_ditto(remote, model, config=Config(ai_ditto_tool_source="local"))
        ditto.recommend_build_settings("m/m", "Rockchip", {"gguf": ["Q4_K_M"]})
        assert prompt_gets(remote) == [] and REMOTE_MARK not in model.user_text()

    def test_without_a_token_no_task_starts(self, remote):
        with_prompts(remote, "add_board")
        model = Structured()
        ditto = DittoCoder(config_manager=Config(), client=model, secrets_manager=SimpleNamespace(get_secret=lambda k: None),
                           remote_client_factory=remote.factory())
        with pytest.raises(DittoError, match="no named MCP token"):
            ditto.generate_module(board="RK3566 board")
        assert model.requests == []


# --- reason codes from the structured error ----------------------------------------------------------------------------

class TestStructuredErrors:

    def test_the_server_sends_the_reason_code_and_field_as_structured_content(self, remote):
        with remote.connection() as connection:
            result = connection._run(connection._client.call_tool("read_thing", {"THING_ID": "a"}), 30)
        assert result.is_error
        assert result.structured_content == {"error": {"code": "unknown_argument", "field": "THING_ID"}} or \
            result.structured_content["error"]["code"] == "unknown_argument"

    def test_a_declined_question_has_the_code_declined(self, remote):
        with remote.connection(confirm=lambda q, i: False) as connection:
            with pytest.raises(ToolError) as exc:
                connection.call("start_build", {"thing_id": "b"})
        assert exc.value.code == "declined" and remote.ran == []

    def test_a_denied_tool_has_the_code_permission_denied(self, remote):
        remote.policy(Decision.DENY)
        with remote.connection() as connection:
            with pytest.raises(ToolError) as exc:
                connection.call("start_build", {"thing_id": "b"})
        assert exc.value.code == "permission_denied"

    def test_the_words_never_decide_the_code(self):
        # The remote error text is the server's untrusted words: "denied" or "did you mean" in it changes nothing.
        listed = remote_tools._Listed("t", "", {"properties": {"thing_id": {}}})
        words = SimpleNamespace(content=[SimpleNamespace(text="Access denied. Did you mean 'x'? declined")],
                                structured_content=None)
        assert structured_error(words, listed) == ("failed", "")
        coded = SimpleNamespace(structured_content={"error": {"code": "unknown_id", "field": "thing_id"}})
        assert structured_error(coded, listed) == ("unknown_id", "thing_id")
        odd = SimpleNamespace(structured_content={"error": {"code": "obey_me", "field": "not_in_schema"}})
        assert structured_error(odd, listed) == ("failed", ""), "only known codes and the tool's own fields"

    def test_no_text_guessing_is_left(self):
        source = (REPO_ROOT / "orchestrator" / "llm" / "remote_tools.py").read_text(encoding="utf-8")
        assert "_error_code" not in source and '"did you mean"' not in source.lower()


# --- the GUI's confirmation: "stop asking" only where the server's form offers it -----------------------------------

class TestStopAskingInTheDialog:

    def test_the_long_dialog_has_the_button_only_for_a_remote_offer(self, qapp):
        from orchestrator.gui import tool_confirmation
        dialog, allow = tool_confirmation.long_question_dialog(None, "q", "x" * 2000, remote="build box")
        assert dialog.stop_asking_button is not None and not dialog.stop_asking_button.isDefault()
        dialog.stop_asking_button.click()
        assert dialog.result() == tool_confirmation.STOP_ASKING
        plain, _ = tool_confirmation.long_question_dialog(None, "q", "x" * 2000)
        assert plain.stop_asking_button is None
        dialog.deleteLater()
        plain.deleteLater()

    def _boxes(self, monkeypatch, press, warning_answer):
        """Message boxes: the question box clicks the button whose text starts with `press`; the warning answers."""
        from orchestrator.gui import tool_confirmation
        shown = {"buttons": [], "warnings": []}
        real = tool_confirmation.QMessageBox

        class Box(real):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._warning = bool(args) and args[0] == real.Warning

            def exec(self):
                if self._warning:
                    shown["warnings"].append(self.text())
                    return warning_answer
                buttons = [b.text() for b in self.buttons()]
                shown["buttons"].append(buttons)
                target = next((b for b in self.buttons() if b.text().startswith(press)), None) or \
                    next(b for b in self.buttons() if b.text() == "Deny")
                self._clicked = target
                return 0

            def clickedButton(self):
                return getattr(self, "_clicked", None)
        monkeypatch.setattr(tool_confirmation, "QMessageBox", Box)
        return shown

    def test_a_remote_question_that_offers_it_can_send_the_request(self, qapp, monkeypatch):
        from orchestrator.gui.tool_confirmation import ToolConfirmation
        shown = self._boxes(monkeypatch, "Allow and ask it to stop asking", warning_answer=0x00004000)  # Yes
        question = RemoteQuestion("build box", "Start a build?", offers_request=True)
        answer = remote_tools.ask_person(ToolConfirmation().confirm, question)
        assert isinstance(answer, RemoteAnswer) and answer.approve and answer.stop_asking
        assert "Allow and ask it to stop asking..." in shown["buttons"][0]
        (warning,) = shown["warnings"]
        assert "REQUEST" in warning and "build box" in warning and "approval window" in warning

    def test_backing_out_of_the_warning_asks_again_without_the_option(self, qapp, monkeypatch):
        from orchestrator.gui.tool_confirmation import ToolConfirmation
        shown = self._boxes(monkeypatch, "Allow and ask it to stop asking", warning_answer=0x00010000)  # No
        question = RemoteQuestion("build box", "Start a build?", offers_request=True)
        answer = remote_tools.ask_person(ToolConfirmation().confirm, question)
        assert not answer.approve and not answer.stop_asking, "the second question was denied"
        assert len(shown["buttons"]) == 2 and not any(b.startswith("Allow and ask") for b in shown["buttons"][1])

    def test_without_the_servers_offer_there_is_no_such_button(self, qapp, monkeypatch):
        from orchestrator.gui.tool_confirmation import ToolConfirmation
        shown = self._boxes(monkeypatch, "Allow", warning_answer=0x00004000)
        answer = remote_tools.ask_person(ToolConfirmation().confirm, RemoteQuestion("build box", "Start?"))
        assert answer.approve and not answer.stop_asking
        assert not any(b.startswith("Allow and ask") for b in shown["buttons"][0])

    def test_a_local_tool_question_never_shows_it(self, qapp, monkeypatch):
        from orchestrator.gui.tool_confirmation import ToolConfirmation
        shown = self._boxes(monkeypatch, "Deny", warning_answer=0x00004000)
        inp = SimpleNamespace(model_dump=lambda **k: {})
        # A tool of this framework, even one pretending to carry the flag: a request goes only to a remote server.
        spec = SimpleNamespace(title="Start a build", name="start_build", offers_request=True, server="x")
        assert ToolConfirmation().confirm(spec, inp) is False
        assert not any(b.startswith("Allow and ask") for b in shown["buttons"][0])


# --- the GUI's fields for the remote mode ----------------------------------------------------------------------------

@pytest.fixture
def gui(tmp_path, monkeypatch):
    from orchestrator.Core.config_manager import ConfigManager
    from orchestrator.gui import dialogs
    from test_dialogs import FakeFramework, FakeSecrets
    monkeypatch.setattr(dialogs.AIConfigurationDialog, "model_lister",
                        staticmethod(lambda config, secrets, provider: (_ for _ in ()).throw(RuntimeError("offline"))))
    warnings = []
    monkeypatch.setattr(dialogs.QMessageBox, "warning", staticmethod(lambda p, t, text, *a: warnings.append(text)))
    monkeypatch.setattr(dialogs.QMessageBox, "critical", staticmethod(lambda p, t, text, *a: warnings.append(text)))
    monkeypatch.setattr(dialogs.QMessageBox, "information", staticmethod(lambda *a: None))
    framework = FakeFramework(ConfigManager(tmp_path), FakeSecrets())
    return SimpleNamespace(framework=framework, warnings=warnings, dialogs=dialogs)


def open_dialog(gui):
    dlg = gui.dialogs.AIConfigurationDialog(gui.framework)
    dlg.cb_provider.setCurrentIndex(dlg.cb_provider.findData("ollama"))
    dlg.cb_model.setCurrentText("qwen3.5:4b")
    return dlg


class TestRemoteFields:

    def test_command_mode_stores_the_token_in_the_vault_and_never_shows_it_again(self, qapp, gui):
        dlg = open_dialog(gui)
        dlg.cb_tool_source.setCurrentIndex(dlg.cb_tool_source.findData("mcp"))
        dlg.txt_mcp_command.setText("docker exec -i lcf llm-cli mcp serve")
        dlg.txt_mcp_name.setText("build box")
        dlg.txt_mcp_token.setText(TOKEN)
        dlg._save_config()
        assert gui.warnings == [] and dlg.result() == dlg.DialogCode.Accepted
        config = gui.framework.config
        assert config.get("ai_ditto_tool_source") == "mcp" and config.get("ai_ditto_mcp_transport") == "stdio"
        assert config.get("ai_ditto_mcp_command") == ["docker", "exec", "-i", "lcf", "llm-cli", "mcp", "serve"]
        assert config.get("ai_ditto_mcp_name") == "build box"
        assert gui.framework.secrets_manager.store == {"ditto_mcp_token": TOKEN}
        assert dlg.txt_mcp_token.text() == "" and TOKEN not in str(dlg.get_config())
        saved = (Path(config.config_dir) / "config.yml").read_text(encoding="utf-8") \
            if (Path(config.config_dir) / "config.yml").exists() else ""
        assert TOKEN not in saved, "the token is in the vault, never in the settings"
        again = open_dialog(gui)
        assert again.cb_tool_source.currentData() == "mcp" and again.txt_mcp_token.text() == ""
        assert "stored in the vault" in again.txt_mcp_token.placeholderText()
        assert again.txt_mcp_command.text() == "docker exec -i lcf llm-cli mcp serve"
        assert remote_tools.remote_server(config).label() == "build box"

    def test_the_mode_is_refused_without_a_token(self, qapp, gui):
        dlg = open_dialog(gui)
        dlg.cb_tool_source.setCurrentIndex(dlg.cb_tool_source.findData("mcp"))
        dlg.txt_mcp_command.setText("docker exec -i lcf llm-cli mcp serve")
        dlg._save_config()
        assert gui.warnings and "without a named token" in gui.warnings[0] and "--link ditto" in gui.warnings[0]
        assert dlg.result() != dlg.DialogCode.Accepted
        assert gui.framework.config.get("ai_ditto_tool_source") != "mcp", "nothing was saved"

    @pytest.mark.parametrize("url", ["http://box.example:8765/mcp", "https://user:pw@box.example/mcp", "box:8765"])
    def test_an_address_that_would_expose_the_token_is_refused(self, qapp, gui, url):
        dlg = open_dialog(gui)
        dlg.cb_tool_source.setCurrentIndex(dlg.cb_tool_source.findData("mcp"))
        dlg.cb_mcp_transport.setCurrentIndex(dlg.cb_mcp_transport.findData("http"))
        dlg.txt_mcp_url.setText(url)
        dlg.txt_mcp_token.setText(TOKEN)
        dlg._save_config()
        assert gui.warnings and dlg.result() != dlg.DialogCode.Accepted
        assert gui.framework.secrets_manager.store == {}, "a refused setting stores no token"

    def test_a_loopback_server_address_is_accepted_with_a_stored_token(self, qapp, gui):
        gui.framework.secrets_manager.store["ditto_mcp_token"] = TOKEN
        dlg = open_dialog(gui)
        assert dlg.txt_mcp_token.text() == ""
        dlg.cb_tool_source.setCurrentIndex(dlg.cb_tool_source.findData("mcp"))
        dlg.cb_mcp_transport.setCurrentIndex(dlg.cb_mcp_transport.findData("http"))
        dlg.txt_mcp_url.setText("http://127.0.0.1:8765/mcp")
        dlg._save_config()
        assert gui.warnings == [] and gui.framework.config.get("ai_ditto_mcp_url") == "http://127.0.0.1:8765/mcp"
        assert not dlg.txt_mcp_command.isEnabled() and dlg.txt_mcp_url.isEnabled()

    def test_back_to_the_own_tools(self, qapp, gui):
        gui.framework.config.set("ai_ditto_tool_source", "mcp")
        dlg = open_dialog(gui)
        dlg.cb_tool_source.setCurrentIndex(dlg.cb_tool_source.findData("local"))
        dlg._save_config()
        assert gui.warnings == [] and gui.framework.config.get("ai_ditto_tool_source") == "local"
        assert not dlg.txt_mcp_token.isEnabled()


# --- one relay for the GUI's workers ---------------------------------------------------------------------------------

def test_no_gui_worker_emits_on_a_qobject_of_its_own():
    """The module wizard's AI import used a WizardSignals object it created and a plain thread emitted on (a closed
    wizard could take it with it); the AI settings kept a relay of their own. Both use the one WorkerRelay now."""
    root = REPO_ROOT / "orchestrator" / "gui"
    wizards = (root / "wizards.py").read_text(encoding="utf-8")
    dialogs = (root / "dialogs.py").read_text(encoding="utf-8")
    assert "WizardSignals" not in wizards and "worker_relay()" in wizards
    assert "_ModelListRelay" not in dialogs and "worker_relay()" in dialogs
    tree = ast.parse(dialogs)
    signals = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "Signal"]
    assert signals == [], "dialogs.py defines no signal of its own for workers"


def test_the_ai_import_answer_reaches_the_gui_thread_through_the_relay(qapp, tmp_path, monkeypatch):
    import threading
    import time
    from orchestrator.gui import wizards
    arrived = []
    monkeypatch.setattr(wizards.ModuleCreationWizard, "on_ai_finished",
                        lambda self, config: arrived.append((threading.current_thread() is threading.main_thread(),
                                                             config)))
    monkeypatch.setattr(wizards.ModuleCreationWizard, "on_ai_error", lambda self, err: arrived.append(("error", err)))
    wizard = wizards.ModuleCreationWizard(tmp_path)
    other = wizards.ModuleCreationWizard(tmp_path)

    class Ditto:
        def generate_module(self, path):
            return SimpleNamespace(model_dump=lambda: {"module_name": "X"})
    token = wizard._ai_request = object()
    from orchestrator.gui.worker_relay import worker_relay
    worker = threading.Thread(target=wizards.ModuleCreationWizard._ai_worker,
                              args=(Ditto(), tmp_path / "p.txt", token, worker_relay()))
    worker.start()
    worker.join(5)
    deadline = time.monotonic() + 5
    while len(arrived) < 1 and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()
    assert arrived == [(True, {"module_name": "X"})], "only the wizard that asked, in the GUI thread"
    wizard.deleteLater()
    other.deleteLater()


# --- Stop texts ----------------------------------------------------------------------------------------------------

def test_stop_texts_say_a_page_still_arriving_is_abandoned():
    from orchestrator.utils.localization import ui_text
    assert "abandoned" in ui_text("crawl.stopping", "en") and "after the current page" not in ui_text(
        "crawl.stopping", "en")
    assert "verworfen" in ui_text("crawl.stopping", "de") and "nach der aktuellen Seite" not in ui_text(
        "crawl.stopping", "de")
    wizards = (REPO_ROOT / "orchestrator" / "gui" / "wizards.py").read_text(encoding="utf-8")
    assert "after the current page" not in wizards
