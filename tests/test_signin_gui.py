"""
Security > Secrets..., Security > Sign-in Overview... and Security > Key Storage... (W1' of 2026-10-16: every action
of `llmcf secrets` and `llmcf ditto login|logout|auth` also by mouse). Offscreen: each entry is reachable from the
menu bar, each button calls the same core function as its CLI command with the right arguments, every question
defaults to No, every vault, keyring or network call runs off the GUI thread, a secret never reaches a widget, and
every text exists in English and German.

No network: checks are stand-ins; the vault is a real one in a temporary per-user folder with an in-memory keyring.
"""

import gc
import re
import string
import threading
import time
from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT

pytest.importorskip("PySide6")

from orchestrator.Core.config_manager import ConfigManager  # noqa: E402
from orchestrator.Core.secret_entries import KNOWN_SECRETS, SecretNotStored, StoredSecret  # noqa: E402
from orchestrator.Core.secrets_manager import SecretsManager  # noqa: E402
from orchestrator.llm.auth.signin import SignIn  # noqa: E402
from orchestrator.llm.auth.validate import ValidationResult  # noqa: E402

OPENAI_KEY = "sk-" + "proj-" + "G" * 40
HF_TOKEN = "hf_" + "W" * 30


@pytest.fixture(autouse=True)
def fresh_keyring_probe():
    from orchestrator.Core import vault_key
    vault_key.reset_probe()
    yield
    vault_key.reset_probe()


@pytest.fixture
def home(tmp_path, monkeypatch, memory_keyring):
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "LLM_SECRET_STORAGE"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path / "state"


@pytest.fixture(autouse=True)
def gui_threads():
    gc.collect()
    yield
    for thread in threading.enumerate():
        if thread.name.startswith("sign-in-"):
            thread.join(timeout=5)
    gc.collect()


def open_vault(home):
    sm = SecretsManager(home)
    assert sm.initialize(), sm.storage_error
    return sm


def framework_for(tmp_path, vault):
    return SimpleNamespace(config=ConfigManager(tmp_path / "cfg"), secrets_manager=vault)


def wait_for(qapp, condition, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()
    return condition()


def widget_texts(widget):
    """Every text a person can read in `widget`: labels, buttons, line edits, table cells."""
    from PySide6.QtWidgets import QAbstractButton, QLabel, QLineEdit, QTableWidget
    texts = [w.text() for w in widget.findChildren(QLabel)] + [w.text() for w in widget.findChildren(QAbstractButton)]
    texts += [w.text() for w in widget.findChildren(QLineEdit) if w.echoMode() == QLineEdit.Normal]
    for table in widget.findChildren(QTableWidget):
        texts += [table.item(r, c).text() for r in range(table.rowCount()) for c in range(table.columnCount())
                  if table.item(r, c) is not None]
    return "\n".join(texts)


class Boxes:
    """Answers plain message boxes (plain._exec) and keeps what each one asked, with its default button."""

    def __init__(self, monkeypatch, answer="no"):
        from PySide6.QtWidgets import QMessageBox
        from orchestrator.gui import plain
        self.asked, self.answer = [], answer
        buttons = {"yes": QMessageBox.Yes, "no": QMessageBox.No}

        def fake(box):
            default = box.defaultButton()
            self.asked.append(SimpleNamespace(text=box.text(), default_is_no=default is not None and default is
                                              box.button(QMessageBox.No)))
            if box.button(QMessageBox.Yes) is None:
                return int(QMessageBox.Ok)
            return int(buttons[self.answer])

        monkeypatch.setattr(plain, "_exec", fake)


class Recorder:
    """Records each call of a core function with whether it ran on the GUI thread."""

    def __init__(self, result=None, error=None):
        self.calls, self.result, self.error = [], result, error

    def __call__(self, *args, **kwargs):
        self.calls.append(SimpleNamespace(args=args, kwargs=kwargs,
                                          gui=threading.current_thread() is threading.main_thread()))
        if self.error is not None:
            raise self.error
        return self.result


# --- the menu bar --------------------------------------------------------------------------------------------------

def test_the_security_menu_opens_the_three_windows(qapp, tmp_path, monkeypatch, memory_keyring):
    from orchestrator.Core.framework import FrameworkManager
    from orchestrator.gui import main_window
    from orchestrator.gui.menus import signin as menu_module
    from orchestrator.gui.signin_windows import KeyStorageWindow, SecretsWindow, SignInOverviewWindow

    (tmp_path / "configs").mkdir()
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))

    class TmpFramework(FrameworkManager):
        def __init__(self, *args, **kwargs):
            super().__init__(str(tmp_path / "configs"))
            self.info.installation_path = tmp_path

    monkeypatch.setattr(main_window, "FrameworkManager", TmpFramework)
    monkeypatch.setattr(main_window.MainOrchestrator, "_open_tool_permissions", lambda self, first_run=False: None)
    win = main_window.MainOrchestrator(REPO_ROOT)
    qapp.processEvents()
    try:
        menu = win.menu_security
        assert menu.objectName() == menu_module.MENU_NAME and menu.title() == "Security"
        actions = {a.objectName(): a for a in menu.actions() if a.objectName()}
        assert actions[menu_module.ACT_OPENAI_KEY].text() == "Set OpenAI API Key"
        expected = {menu_module.ACT_SECRETS: ("Secrets...", SecretsWindow),
                    menu_module.ACT_OVERVIEW: ("Sign-in Overview...", SignInOverviewWindow),
                    menu_module.ACT_KEY_STORAGE: ("Key Storage...", KeyStorageWindow)}
        windows = {}
        for name, (text, cls) in expected.items():
            action = win.findChild(type(actions[name]), name)
            assert action is actions[name] and action.text() == text and getattr(win, name) is action
            action.trigger()
            dialog = next(d for d in win.findChildren(cls))
            assert dialog.isVisible()
            assert wait_for(qapp, lambda: not dialog.busy)
            windows[name] = dialog
            action.trigger()
            assert len(win.findChildren(cls)) == 1, "a second click shows the same window"
            assert wait_for(qapp, lambda: not dialog.busy)
        overview = windows[menu_module.ACT_OVERVIEW]
        for name, action_name in menu_module.WINDOW_OBJECTS.items():
            dialog = windows[action_name]
            if name in menu_module.PANEL_OBJECTS:
                dialog.select_provider(menu_module.PANEL_OBJECTS[name])
                assert wait_for(qapp, lambda: not dialog.panel.busy)
            assert dialog.findChild(object, name) is not None, name
        assert overview.panel is not None
        for dialog in windows.values():
            dialog.close()
    finally:
        win.close()


def test_cli_paths_name_every_command_and_switch_of_the_area():
    from orchestrator import cli as cli_module
    from orchestrator.gui.menus import signin as menu_module

    def walk(group, path):
        for name, command in group.commands.items():
            full = f"{path} {name}".strip()
            yield full, command
            if hasattr(command, "commands"):
                yield from walk(command, full)

    area = {p: c for p, c in walk(cli_module.cli, "")
            if p == "secrets" or p.startswith(("secrets ", "ditto login", "ditto logout", "ditto auth"))}
    assert {"secrets set", "secrets list", "ditto login", "ditto logout", "ditto auth status", "ditto auth methods",
            "ditto auth storage", "ditto auth rotate-key"} <= set(area)
    for path, command in area.items():
        assert path in menu_module.CLI_PATHS, path
        for param in command.params:
            flag = next((o for o in param.opts if o.startswith("--")), None)
            if flag is None or flag == "--help":
                continue
            key = f"{path} {flag}"
            assert key in menu_module.CLI_PATHS or key in menu_module.MACHINE_ONLY, key
    named = set(menu_module.CLI_PATHS.values())
    actions = {menu_module.ACT_SECRETS, menu_module.ACT_OVERVIEW, menu_module.ACT_KEY_STORAGE}
    assert named - actions <= set(menu_module.WINDOW_OBJECTS), named - actions - set(menu_module.WINDOW_OBJECTS)


# --- Security > Secrets... ----------------------------------------------------------------------------------------

class TestSecretsWindow:

    def window(self, tmp_path, vault, lang=None):
        from orchestrator.gui.signin_windows import SecretsWindow
        return SecretsWindow(framework_for(tmp_path, vault), lang=lang)

    def test_names_with_stored_or_not_never_a_value_read_off_the_gui_thread(self, qapp, tmp_path, home,
                                                                           monkeypatch):
        from orchestrator.gui import signin_windows
        vault = open_vault(home)
        vault.set_secret("hf_token", HF_TOKEN)
        vault.set_secret("openai_api_key", OPENAI_KEY)
        real = signin_windows.secret_entries
        threads = []

        def entries(sm):
            threads.append(threading.current_thread() is threading.main_thread())
            return real(sm)

        monkeypatch.setattr(signin_windows, "secret_entries", entries)
        dialog = self.window(tmp_path, vault)
        dialog.reload()
        assert wait_for(qapp, lambda: not dialog.busy and dialog.table.rowCount() > 0)
        assert threads == [False]
        rows = {dialog.table.item(r, 0).text(): (dialog.table.item(r, 1).text(), dialog.table.item(r, 2).text())
                for r in range(dialog.table.rowCount())}
        assert rows["hf_token"][0] == "stored" and rows["qdrant_api_key"][0] == "not stored"
        assert rows["cred/openai/api_key"] == ("stored", "Sign-in of the AI provider openai (Security > Sign-in "
                                                         "Overview...)")
        assert list(rows)[:len(KNOWN_SECRETS)] == list(KNOWN_SECRETS)
        text = widget_texts(dialog)
        assert HF_TOKEN not in text and OPENAI_KEY not in text
        assert str(vault.secrets_file) in dialog.intro.text(), "the person sees where the vault is"
        dialog.table.selectRow(0)
        assert dialog.cb_name.currentText() == "hf_token" and "replaces" in dialog.lbl_replace.text()

    def test_store_calls_the_core_with_name_and_value_off_the_gui_thread(self, qapp, tmp_path, home, monkeypatch):
        from orchestrator.gui import signin_windows
        vault = open_vault(home)
        dialog = self.window(tmp_path, vault)
        recorder = Recorder(StoredSecret("qdrant_api_key", False))
        monkeypatch.setattr(signin_windows, "store_secret", recorder)
        dialog.cb_name.setCurrentText("qdrant_api_key")
        dialog.store()
        assert dialog.lbl_result.text() == "Enter the value." and recorder.calls == []
        dialog.txt_value.setText("q-123")
        dialog.txt_repeat.setText("q-124")
        dialog.btn_store.click()
        assert dialog.lbl_result.text() == "The two entered values do not match." and recorder.calls == []
        dialog.txt_repeat.setText("q-123")
        dialog.btn_store.click()
        assert wait_for(qapp, lambda: not dialog.busy and recorder.calls)
        call = recorder.calls[0]
        assert call.args == (vault, "qdrant_api_key", "q-123") and not call.gui
        assert wait_for(qapp, lambda: not dialog.busy)
        assert dialog.lbl_result.text() == "Secret 'qdrant_api_key' stored in the encrypted credential vault."
        assert dialog.txt_value.text() == "" and dialog.txt_repeat.text() == "", "the value leaves the widgets"
        dialog.cb_name.setCurrentText("")
        dialog.store()
        assert dialog.lbl_result.text() == "Enter a name."

    def test_any_name_really_stored_ephemeral_and_refusals_said_plainly(self, qapp, tmp_path, home, monkeypatch):
        from orchestrator.gui import signin_windows
        vault = open_vault(home)
        dialog = self.window(tmp_path, vault)
        dialog.cb_name.setCurrentText("my_own_token")
        dialog.txt_value.setText("abc")
        dialog.txt_repeat.setText("abc")
        dialog.store()
        assert wait_for(qapp, lambda: not dialog.busy and "stored in" in dialog.lbl_result.text())
        assert vault.get_secret("my_own_token") == "abc"
        assert wait_for(qapp, lambda: any(dialog.table.item(r, 0).text() == "my_own_token"
                                          for r in range(dialog.table.rowCount())))
        monkeypatch.setattr(signin_windows, "store_secret", Recorder(StoredSecret("hf_token", True)))
        dialog.cb_name.setCurrentText("hf_token")
        dialog.txt_value.setText(HF_TOKEN)
        dialog.txt_repeat.setText(HF_TOKEN)
        dialog.store()
        assert wait_for(qapp, lambda: not dialog.busy and "nothing was saved" in dialog.lbl_result.text())
        monkeypatch.setattr(signin_windows, "store_secret", Recorder(error=SecretNotStored("hf_token")))
        dialog.txt_value.setText(HF_TOKEN)
        dialog.txt_repeat.setText(HF_TOKEN)
        dialog.store()
        assert wait_for(qapp, lambda: not dialog.busy and "Failed to store secret 'hf_token'" in
                        dialog.lbl_result.text())
        assert HF_TOKEN not in widget_texts(dialog)

    def test_german(self, qapp, tmp_path, home):
        vault = open_vault(home)
        dialog = self.window(tmp_path, vault, lang="de")
        dialog.reload()
        assert wait_for(qapp, lambda: not dialog.busy and dialog.table.rowCount() > 0)
        assert dialog.windowTitle() == "Geheimnisse" and dialog.btn_store.text() == "Speichern"
        assert dialog.table.item(0, 1).text() == "nicht gespeichert"


# --- Security > Sign-in Overview... ---------------------------------------------------------------------------------

class RecordingService(SignIn):
    """A SignIn that notes which of its vault, keyring or network calls ran on the GUI thread."""

    on_gui: list = []

    def _note(self, name):
        if threading.current_thread() is threading.main_thread():
            self.on_gui.append(name)

    def status_all(self, *a, **kw):
        self._note("status_all")
        return super().status_all(*a, **kw)

    def status(self, *a, **kw):
        self._note("status")
        return super().status(*a, **kw)

    def storage(self, *a, **kw):
        self._note("storage")
        return super().storage(*a, **kw)

    def check(self, *a, **kw):
        self._note("check")
        return super().check(*a, **kw)

    def save_key(self, *a, **kw):
        self._note("save_key")
        return super().save_key(*a, **kw)

    def env_key(self, *a, **kw):
        self._note("env_key")
        return super().env_key(*a, **kw)

    def use_method(self, *a, **kw):
        self._note("use_method")
        return super().use_method(*a, **kw)

    def sign_out(self, *a, **kw):
        self._note("sign_out")
        return super().sign_out(*a, **kw)

    def choose_storage(self, *a, **kw):
        self._note("choose_storage")
        return super().choose_storage(*a, **kw)

    def rotate_key(self, *a, **kw):
        self._note("rotate_key")
        return super().rotate_key(*a, **kw)


def overview(tmp_path, vault, lang=None, **values):
    from orchestrator.gui.signin_windows import SignInOverviewWindow
    framework = framework_for(tmp_path, vault)
    for key, value in values.items():
        framework.config.set(key, value)
    RecordingService.on_gui = []
    service = RecordingService(framework.config, vault)
    return SignInOverviewWindow(framework, lang=lang, service=service), service


class TestSignInOverview:

    def test_every_provider_and_the_vault_read_off_the_gui_thread(self, qapp, tmp_path, home, monkeypatch):
        vault = open_vault(home)
        dialog, service = overview(tmp_path, vault, ai_provider="ollama")
        ids = [p.id for p in service.catalog.providers.values()]
        assert dialog.table.rowCount() == len(ids) and "openai" in ids and "ollama" in ids
        calls = []
        real = RecordingService.status_all
        monkeypatch.setattr(RecordingService, "status_all",
                            lambda self, check=False: calls.append(check) or real(self, check=check))
        dialog.reload()
        assert wait_for(qapp, lambda: not dialog.busy and calls)
        assert calls == [False]
        rows = {dialog.table.item(r, 0).text(): [dialog.table.item(r, c).text() for c in range(6)]
                for r in range(dialog.table.rowCount())}
        assert rows["* Ollama (local)"][4] == "No sign-in needed", "Ditto's provider is marked"
        assert rows["OpenAI (Responses API)"][4] == "Not signed in"
        assert dialog.lbl_vault.text() == f"Credential vault: keyring, {vault.secrets_file}"
        assert RecordingService.on_gui == []

    def test_check_all_runs_the_free_check_of_every_provider_off_the_gui_thread(self, qapp, tmp_path, home,
                                                                              monkeypatch):
        vault = open_vault(home)
        dialog, service = overview(tmp_path, vault)
        openai = service.catalog.get("openai")
        service.save_key(openai, openai.method("api_key"), OPENAI_KEY)
        checks = []

        def check(self, preset, spec, base_url=None, **kw):
            checks.append((preset.id, threading.current_thread() is threading.main_thread()))
            return ValidationResult(ok=True, provider=preset.id, method=spec.id, models=3)

        monkeypatch.setattr(RecordingService, "check", check)
        dialog.btn_check_all.click()
        assert dialog.busy and not dialog.btn_check_all.isEnabled()
        assert wait_for(qapp, lambda: not dialog.busy)
        assert ("openai", False) in checks and all(not gui for _, gui in checks)
        row = next(r for r in range(dialog.table.rowCount())
                   if dialog.table.item(r, 0).text().endswith("OpenAI (Responses API)"))
        assert dialog.table.item(row, 5).text() == "Valid, 3 models"
        assert dialog.table.item(row, 3).text() == "...GGGG" and dialog.table.item(row, 2).text() == "credential vault"
        assert dialog.lbl_state.text().startswith("Checked: ")
        assert OPENAI_KEY not in widget_texts(dialog)

    def test_the_methods_with_their_environment_variables_and_notes(self, qapp, tmp_path, home):
        vault = open_vault(home)
        dialog, _ = overview(tmp_path, vault)
        rows = [[dialog.methods.item(r, c).text() for c in range(dialog.methods.columnCount())]
                for r in range(dialog.methods.rowCount())]
        openai = next(r for r in rows if r[0] == "OpenAI (Responses API) [openai]")
        assert "OPENAI_API_KEY" in openai[4] and "Recommended" in openai[3]
        profile = next(r for r in rows if r[1].endswith("[ant_profile]"))
        assert "Advanced" in profile[3] and "ant auth login" in profile[8], "the note is shown"

    def test_store_the_key_from_the_environment_variable(self, qapp, tmp_path, home, monkeypatch):
        vault = open_vault(home)
        dialog, service = overview(tmp_path, vault)
        dialog.select_provider("openai")
        panel = dialog.panel
        form = panel.forms["api_key"]
        assert "OPENAI_API_KEY" in form.lbl_env.text()
        assert wait_for(qapp, lambda: not panel.busy)
        assert form.btn_from_env.isHidden(), "offered only while the variable is set"
        monkeypatch.setenv("OPENAI_API_KEY", OPENAI_KEY)
        panel.refresh()
        assert wait_for(qapp, lambda: not panel.busy and not form.btn_from_env.isHidden())
        assert form.btn_from_env.text() == "Store the key from OPENAI_API_KEY in the vault"
        assert form.chk_from_env_check.isChecked()
        failed = ValidationResult(ok=False, provider="openai", method="api_key")
        monkeypatch.setattr(RecordingService, "check", lambda self, *a, **kw: failed)
        form.btn_from_env.click()
        assert wait_for(qapp, lambda: not panel.busy)
        assert vault.list_secrets() == [], "a failed check stores nothing"
        form.chk_from_env_check.setChecked(False)  # as --no-check
        saves = []
        real = RecordingService.save_key
        monkeypatch.setattr(RecordingService, "save_key", lambda self, *a, **kw: saves.append((a, kw)) or real(
            self, *a, **kw))
        form.btn_from_env.click()
        assert wait_for(qapp, lambda: not panel.busy and "now stored" in panel.lbl_result.text())
        args, kwargs = saves[0]
        assert args[2] == OPENAI_KEY and kwargs["source"] == "env:OPENAI_API_KEY" and kwargs["checked"] is None
        assert vault.get_record("cred/openai/api_key")["secret"] == OPENAI_KEY
        assert panel.lbl_result.text().startswith("The key from OPENAI_API_KEY is now stored in the vault")
        assert OPENAI_KEY not in widget_texts(dialog)
        assert RecordingService.on_gui == []
        monkeypatch.setenv("OPENAI_API_KEY", "two words")
        form.btn_from_env.click()
        assert wait_for(qapp, lambda: not panel.busy and "has a space" in panel.lbl_result.text())

    def test_the_server_address_is_remembered_without_making_it_dittos_provider(self, qapp, tmp_path, home,
                                                                               monkeypatch):
        from orchestrator.gui import sign_in
        vault = open_vault(home)
        dialog, service = overview(tmp_path, vault, ai_provider="openai")
        dialog.select_provider("ollama")
        panel = dialog.panel
        assert panel.txt_base_url is not None and wait_for(qapp, lambda: not panel.busy)
        assert panel.txt_base_url.text() == "http://localhost:11434/v1", "the configured address fills the field"
        panel.forms["none"].btn_use.click()
        assert wait_for(qapp, lambda: not panel.busy)
        assert service.config.get("ai_base_url_ollama", None) in (None, ""), "an unchanged address is not written"
        panel.txt_base_url.setText("http://127.0.0.1:11500/v1")
        panel.forms["none"].btn_use.click()
        assert wait_for(qapp, lambda: not panel.busy and "Saved" in panel.lbl_result.text())
        assert service.config.get("ai_base_url_ollama") == "http://127.0.0.1:11500/v1"
        assert service.config.get("ai_auth_ollama") == "none"
        assert service.config.get("ai_provider") == "openai", "Ditto's provider stays as it was"
        # Forget (`llmcf ditto logout ollama`): asks first with No as the default.
        asked = []

        def question(parent, title, text, buttons, default):
            asked.append((text, default))
            return answers[-1]

        answers = [sign_in.QMessageBox.No]
        monkeypatch.setattr(sign_in.QMessageBox, "question", question)
        panel.forms["none"].btn_forget.click()
        assert asked[0] == ("Forget the remembered server address and method of Ollama (local)?",
                            sign_in.QMessageBox.No)
        assert service.config.get("ai_base_url_ollama") == "http://127.0.0.1:11500/v1"
        answers.append(sign_in.QMessageBox.Yes)
        panel.forms["none"].btn_forget.click()
        assert wait_for(qapp, lambda: not panel.busy and "forgotten" in panel.lbl_result.text())
        assert service.config.get("ai_base_url_ollama", None) in (None, "")
        assert service.config.get("ai_auth_ollama", None) in (None, "")
        assert RecordingService.on_gui == []

    def test_the_ai_settings_panel_remembers_the_address_it_was_given(self, qapp, tmp_path, home, monkeypatch):
        from orchestrator.gui.sign_in import SignInPanel
        vault = open_vault(home)
        config = ConfigManager(tmp_path / "cfg")
        service = SignIn(config, vault)
        panel = SignInPanel(SimpleNamespace(config=config, secrets_manager=vault), service.catalog.get("custom"),
                            base_url="http://127.0.0.1:9000/v1", service=service)
        assert panel.txt_base_url is None and panel.base_url == "http://127.0.0.1:9000/v1"
        assert wait_for(qapp, lambda: not panel.busy)
        panel.select_method("none")
        panel.use("none")
        assert wait_for(qapp, lambda: not panel.busy)
        assert config.get("ai_base_url_custom") == "http://127.0.0.1:9000/v1"

    def test_german(self, qapp, tmp_path, home):
        vault = open_vault(home)
        dialog, _ = overview(tmp_path, vault, lang="de")
        assert dialog.windowTitle() == "Anmeldeübersicht" and dialog.btn_check_all.text() == "Alle prüfen"
        dialog.select_provider("ollama")
        assert dialog.panel.forms["none"].btn_forget.text() == "Vergessen"
        assert dialog.tabs.tabText(0) == "Bei Ollama (local) anmelden"
        assert wait_for(qapp, lambda: not dialog.panel.busy)


# --- Security > Key Storage... ---------------------------------------------------------------------------------------

class TestKeyStorage:

    def window(self, tmp_path, vault, lang=None):
        from orchestrator.gui.signin_windows import KeyStorageWindow
        framework = framework_for(tmp_path, vault)
        RecordingService.on_gui = []
        service = RecordingService(framework.config, vault)
        return KeyStorageWindow(framework, lang=lang, service=service), service

    def test_the_protection_and_the_file_while_it_works(self, qapp, tmp_path, home):
        vault = open_vault(home)
        dialog, _ = self.window(tmp_path, vault)
        dialog.reload()
        assert wait_for(qapp, lambda: not dialog.busy and dialog.info)
        assert dialog.lbl_mode.text() == "Protected with: keyring (the OS keyring (default))"
        assert dialog.lbl_file.text() == f"Vault file: {vault.secrets_file}" and dialog.lbl_problem.text() == ""
        assert dialog.chooser.rb_keyring.isChecked() and dialog.chooser.rb_keyring.text().endswith("(current)")
        assert RecordingService.on_gui == []

    def test_switching_calls_the_core_with_mode_and_helper_off_the_gui_thread(self, qapp, tmp_path, home,
                                                                            monkeypatch):
        vault = open_vault(home)
        dialog, _ = self.window(tmp_path, vault)
        recorder = Recorder()
        monkeypatch.setattr(RecordingService, "choose_storage", lambda self, *a: recorder(*a))
        chooser = dialog.chooser
        chooser.rb_helper.setChecked(True)
        chooser.btn_use.click()
        assert recorder.calls == [] and "helper command" in chooser.lbl_result.text()
        chooser.txt_helper.setText("pass show llmcc/vault-key")
        chooser.btn_use.click()
        assert wait_for(qapp, lambda: not dialog.busy and recorder.calls)
        assert recorder.calls[0].args == ("helper", "pass show llmcc/vault-key") and not recorder.calls[0].gui
        chooser.rb_passphrase.setChecked(True)
        chooser.btn_use.click()
        chooser.rb_keyring.setChecked(True)
        assert wait_for(qapp, lambda: not dialog.busy)
        chooser.btn_use.click()
        assert wait_for(qapp, lambda: not dialog.busy and len(recorder.calls) == 3)
        assert [c.args for c in recorder.calls[1:]] == [("passphrase", None), ("keyring", None)]

    def test_the_insecure_key_file_only_after_the_warning_no_is_the_default(self, qapp, tmp_path, home,
                                                                          monkeypatch):
        vault = open_vault(home)
        dialog, _ = self.window(tmp_path, vault)
        recorder = Recorder()
        monkeypatch.setattr(RecordingService, "choose_storage", lambda self, *a: recorder(*a))
        boxes = Boxes(monkeypatch, "no")
        chooser = dialog.chooser
        chooser.rb_insecure.setChecked(True)
        chooser.btn_use.click()
        assert boxes.asked == [] and "Confirm" in chooser.lbl_result.text(), "the tick comes first"
        chooser.chk_insecure.setChecked(True)
        chooser.btn_use.click()
        assert boxes.asked[0].text == ("The vault key will be kept in a file next to the vault, NOT encrypted at "
                                       "rest. Continue?") and boxes.asked[0].default_is_no
        assert recorder.calls == [] and dialog.lbl_result.text() == "Cancelled: the protection was not changed."
        boxes.answer = "yes"
        chooser.btn_use.click()
        assert wait_for(qapp, lambda: not dialog.busy and recorder.calls)
        assert recorder.calls[0].args == ("insecure", None) and not recorder.calls[0].gui

    def test_a_real_switch_and_back_to_the_keyring(self, qapp, tmp_path, home, monkeypatch):
        vault = open_vault(home)
        vault.set_secret("hf_token", HF_TOKEN)
        dialog, _ = self.window(tmp_path, vault)
        Boxes(monkeypatch, "yes")
        dialog.choose("insecure")
        assert wait_for(qapp, lambda: not dialog.busy and "now protected" in dialog.lbl_result.text())
        assert vault.storage_mode == "insecure"
        assert wait_for(qapp, lambda: not dialog.busy and "insecure" in dialog.lbl_mode.text())
        dialog.choose("keyring")
        assert wait_for(qapp, lambda: not dialog.busy and vault.storage_mode == "keyring")
        assert vault.get_secret("hf_token") == HF_TOKEN
        assert RecordingService.on_gui == []

    def test_rotate_asks_first_no_is_the_default(self, qapp, tmp_path, home, monkeypatch):
        vault = open_vault(home)
        vault.set_secret("hf_token", HF_TOKEN)
        dialog, _ = self.window(tmp_path, vault)
        calls = []
        real = RecordingService.rotate_key
        monkeypatch.setattr(RecordingService, "rotate_key",
                            lambda self: calls.append(threading.current_thread() is threading.main_thread())
                            or real(self))
        boxes = Boxes(monkeypatch, "no")
        dialog.btn_rotate.click()
        assert boxes.asked[0].default_is_no and "re-encrypt every stored credential" in boxes.asked[0].text
        assert calls == [] and dialog.lbl_result.text() == "Cancelled: the vault key was not changed."
        boxes.answer = "yes"
        dialog.btn_rotate.click()
        assert wait_for(qapp, lambda: not dialog.busy and calls)
        assert calls == [False]
        assert wait_for(qapp, lambda: "rotated" in dialog.lbl_result.text())
        assert vault.get_secret("hf_token") == HF_TOKEN

    def test_german(self, qapp, tmp_path, home):
        vault = open_vault(home)
        dialog, _ = self.window(tmp_path, vault, lang="de")
        dialog.reload()
        assert wait_for(qapp, lambda: not dialog.busy and dialog.info)
        assert dialog.windowTitle() == "Schlüsselspeicher" and dialog.btn_rotate.text() == "Schlüssel erneuern..."
        assert dialog.lbl_mode.text().startswith("Geschützt mit: keyring")
        assert dialog.chooser.rb_keyring.text() == "Schlüsselbund des Betriebssystems (Standard) (aktuell)"


# --- the AI settings' Security > Set OpenAI API Key in the ephemeral storage -----------------------------------------

def test_the_openai_key_dialog_says_when_nothing_was_saved(qapp, monkeypatch):
    from orchestrator.gui import dialogs
    said = []
    sm = SimpleNamespace(get_secret=lambda key: None, set_secret=lambda key, value: True, storage_mode="ephemeral")
    monkeypatch.setattr(dialogs.QMessageBox, "warning", lambda parent, title, text: said.append(text))
    monkeypatch.setattr(dialogs.QMessageBox, "information", lambda parent, title, text: said.append(text))
    dlg = dialogs.SecretInputDialog(SimpleNamespace(secrets_manager=sm), "openai_api_key", "OpenAI API Key")
    dlg.input_field.setText(OPENAI_KEY)
    dlg._save_secret()
    assert said == ["Secret 'openai_api_key' is kept in memory for this run only (secret_storage: ephemeral); "
                    "nothing was saved."]


# --- texts ---------------------------------------------------------------------------------------------------------

def test_every_text_exists_in_english_and_german_with_the_same_placeholders():
    from orchestrator.utils.l10n_signin import TEXTS
    assert set(TEXTS["en"]) == set(TEXTS["de"])

    def fields(text):
        return {name for _, name, _, _ in string.Formatter().parse(text) if name}

    for key, text in TEXTS["en"].items():
        assert fields(text) == fields(TEXTS["de"][key]), key
        assert TEXTS["de"][key].strip(), key


def test_the_windows_build_no_markup_and_never_start_the_claude_command():
    source = (REPO_ROOT / "orchestrator" / "gui" / "signin_windows.py").read_text(encoding="utf-8")
    assert "setToolTip(" not in source and "RichText" not in source and "setHtml" not in source
    assert not re.search(r"subprocess|Popen|os\.system", source)
