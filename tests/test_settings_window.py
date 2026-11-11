"""The one settings window (docs/DESIGN.md "GUI and the person's path"): File > Settings... holds every setting on its
pages; the old entries open their page; the existing dialogs are moved there as pages, never copied; the language
switches at once where the window says so; and `llmcf config set` changes the same settings at the command line."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

pytest.importorskip("PySide6")

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def language_back():
    """Every language and form a test switches to is switched back (the one text table is shared)."""
    from orchestrator.utils import localization
    language, form = localization.current_language(), localization.form_of_address()
    yield
    localization.set_form_of_address(form)
    if localization._instance is not None:
        localization._instance.set_language(language)


def settings(config, **kwargs):
    from orchestrator.gui.settings_window import SettingsDialog
    return SettingsDialog(SimpleNamespace(config=config), lang="en", **kwargs)


# --- the pages ------------------------------------------------------------------------------------------------------

def test_the_pages_in_their_order_and_the_heavy_ones_built_when_first_shown(qapp, tmp_path):
    from orchestrator.Core.config_manager import ConfigManager
    from orchestrator.gui.settings_pages import LazyPage
    from orchestrator.gui.settings_window import PAGES
    dialog = settings(ConfigManager(tmp_path / "cfg"))
    assert [dialog.page_name(dialog.tabs.widget(i)) for i in range(dialog.tabs.count())] == list(PAGES)
    assert [dialog.tabs.tabText(i) for i in range(4)] == ["General", "Ditto", "Language model", "Update"]
    for page in (dialog.page_ditto, dialog.page_model, dialog.page_update):
        assert isinstance(page, LazyPage) and not page.built, "opening the window reads no vault, calls no provider"
    dialog.tabs.setCurrentWidget(dialog.page_update)
    assert dialog.page_update.built and dialog.findChild(type(dialog.update_page.btn_check), "btn_update_check")
    assert not dialog.page_ditto.built
    dialog.close()


def test_a_page_that_cannot_be_built_says_why_in_plain_text(qapp, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel
    from orchestrator.Core.config_manager import ConfigManager
    dialog = settings(ConfigManager(tmp_path / "cfg"), page="ditto", section="permissions")
    shown = dialog.sections[("ditto", "permissions")].content
    assert isinstance(shown, QLabel) and "tool registry is not available" in shown.text()
    assert shown.textFormat() == Qt.PlainText
    dialog.close()


def test_a_wrong_page_name_is_refused(qapp, tmp_path):
    from orchestrator.Core.config_manager import ConfigManager
    dialog = settings(ConfigManager(tmp_path / "cfg"))
    with pytest.raises(KeyError):
        dialog.show_page("nowhere")
    with pytest.raises(KeyError):
        dialog.show_page("ditto", "nowhere")
    dialog.close()


# --- the existing dialogs as pages ------------------------------------------------------------------------------------

def test_an_embedded_dialog_saves_and_stays_and_escape_closes_the_window(qapp):
    from PySide6.QtWidgets import QDialog, QDialogButtonBox, QWidget
    from orchestrator.gui import settings_pages

    class Page(QDialog):
        def __init__(self, parent):
            super().__init__(parent)
            self.box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel, self)
            self.box.accepted.connect(self.accept)
            self.box.rejected.connect(self.reject)
            self.finished_with = []
            self.finished.connect(self.finished_with.append)

        def done(self, result):
            if settings_pages.page_done(self, result):
                return
            super().done(result)

    holder = QWidget()
    holder.show()
    page = Page(holder)
    seen = []
    settings_pages.embed(page, seen.append)
    assert not page.isWindow() and page.isVisible()
    assert page.box.button(QDialogButtonBox.Cancel).isHidden() and not page.box.button(
        QDialogButtonBox.Save).isHidden()
    page.box.button(QDialogButtonBox.Save).click()
    assert seen == [QDialog.Accepted] and page.isVisible() and page.finished_with == [], "Save keeps the page"
    page.reject()   # Escape
    assert seen == [QDialog.Accepted, QDialog.Rejected] and page.isVisible(), "the window decides"
    settings_pages.release(page)
    assert page.finished_with == [QDialog.Rejected] and not settings_pages.embedded(page), "ends as its dialog did"
    holder.close()


def test_closing_the_window_forgets_a_secret_typed_and_not_stored(qapp, tmp_path):
    from orchestrator.Core.config_manager import ConfigManager
    config = ConfigManager(tmp_path / "cfg")
    from orchestrator.gui.settings_window import SettingsDialog
    dialog = SettingsDialog(SimpleNamespace(config=config, secrets_manager=None), lang="en",
                            page="language_model", section="secrets")
    dialog.show()
    secrets = dialog.secrets
    assert secrets in dialog.embedded and not secrets.isWindow()
    secrets.txt_value.setText("typed-not-stored")
    secrets.txt_repeat.setText("typed-not-stored")
    assert secrets.txt_value.echoMode() == secrets.txt_value.EchoMode.Password, "never shown in clear text"
    dialog.close()
    assert secrets.txt_value.text() == "" and secrets.txt_repeat.text() == ""


def test_the_ai_settings_page_has_no_local_runtime_boxes_and_the_model_page_has_them(qapp, booted_framework):
    from orchestrator.gui.settings_window import SettingsDialog
    dialog = SettingsDialog(booted_framework, lang="en", page="ditto", section="ai")
    ai = dialog.ai_settings
    assert not hasattr(ai, "runtime_mode_box") and not hasattr(ai, "prompt_cache_box")
    dialog.show_page("language_model", "runtime")
    from PySide6.QtWidgets import QComboBox
    assert dialog.runtime_mode_box.findChild(QComboBox, "cb_ditto_runtime_mode") is not None
    assert dialog.prompt_cache_box.objectName() == "grp_prompt_cache"
    dialog.close()


# --- the language ---------------------------------------------------------------------------------------------------

def test_the_language_switches_at_once_is_saved_and_the_window_opens_again(qapp, tmp_path, language_back):
    from orchestrator.Core.config_manager import ConfigManager
    from orchestrator.gui.localization import get_instance
    from orchestrator.utils import localization
    i18n = get_instance("en")
    i18n.set_language("en")
    heard = []
    i18n.language_changed.connect(heard.append)
    config = ConfigManager(tmp_path / "cfg")
    dialog = settings(config)
    assert dialog.cmb_language.currentData() == "en"
    assert "next start" in dialog.lbl_language_note.text(), "the page says what waits for the next start"
    dialog.cmb_language.setCurrentIndex(dialog.cmb_language.findData("de"))
    assert localization.current_language() == "de" and heard[-1:] == ["de"], "applied at once"
    saved = yaml.safe_load((tmp_path / "cfg" / "config.yml").read_text(encoding="utf-8"))
    assert saved["language"] == "de"
    page, section, notice = dialog.reopen
    assert (page, section) == ("general", None) and notice.startswith("Gespeichert.")
    again = settings(config, page=page, notice=notice)
    assert again.lbl_general_result.text() == notice
    dialog.close()
    again.close()


def test_open_settings_opens_again_in_the_new_language(qapp, tmp_path, monkeypatch, language_back):
    from PySide6.QtWidgets import QDialog, QWidget
    from orchestrator.Core.config_manager import ConfigManager
    from orchestrator.gui.localization import get_instance
    from orchestrator.gui.menus import build
    get_instance("en").set_language("en")
    window = QWidget()
    window.framework = SimpleNamespace(config=ConfigManager(tmp_path / "cfg"))
    closed, opened = [], []
    window.settings_closed = lambda: closed.append(True)

    def exec_(dialog):
        opened.append((dialog.lang, dialog.page_name()))
        if len(opened) == 1:
            dialog.cmb_language.setCurrentIndex(dialog.cmb_language.findData("de"))
        return 0
    monkeypatch.setattr(QDialog, "exec", exec_)
    build.open_settings(window, "general")
    assert opened == [("en", "general"), ("de", "general")] and closed == [True]
    window.close()


def test_the_cli_sets_the_language_and_the_form_of_address(cli):
    from orchestrator.Core.config_manager import ConfigManager
    result = cli.invoke("config", "set", "language", "DE")
    assert result.code == 0 and "language: de" in result.out, result.out
    result = cli.invoke("config", "set", "form_of_address", "sie")
    assert result.code == 0 and "form_of_address: sie" in result.out, result.out
    again = ConfigManager(str(cli.framework.config.config_dir))
    assert again.get("language") == "de" and again.get("form_of_address") == "sie"
    result = cli.invoke("config", "set", "language", "fr")
    assert result.code == 2 and "en or de" in result.out


# --- the AI settings at the command line (the rules of the window) ------------------------------------------------------

def test_the_cli_sets_the_ai_settings_through_the_windows_rules(cli):
    from orchestrator.Core.config_manager import ConfigManager
    result = cli.invoke("config", "set", "ai_effort", "high")
    assert result.code == 0 and "ai_effort: high" in result.out, result.out
    result = cli.invoke("config", "set", "enable_rag_knowledge", "no")
    assert result.code == 0, result.out
    result = cli.invoke("config", "set", "ai_fallback_chain", "ollama, local")
    assert result.code == 0, result.out
    again = ConfigManager(str(cli.framework.config.config_dir))
    assert again.get("ai_effort") == "high" and again.get("ai_fallback_chain") == ["ollama", "local"]
    assert cli.invoke("config", "set", "offline_mode", "maybe").code == 2
    result = cli.invoke("config", "set", "ai_effort", "enormous")
    assert result.code == 2 and "enormous" in result.out
    result = cli.invoke("config", "set", "ai_provider", "no-such-provider")
    assert result.code == 2 and "no-such-provider" in result.out


def test_the_cli_refuses_what_the_window_refuses(cli):
    """An address that is no http(s) URL is refused as the AI settings window refuses it, and nothing changes."""
    from orchestrator.Core.config_manager import ConfigManager
    assert cli.invoke("config", "set", "ai_provider", "ollama").code == 0
    result = cli.invoke("config", "set", "ai_base_url", "ftp://example.invalid/")
    assert result.code == 2, result.out
    assert ConfigManager(str(cli.framework.config.config_dir)).get("ai_base_url") != "ftp://example.invalid/"


@pytest.fixture
def cli(booted_framework, tmp_path, monkeypatch):
    from test_review_capabilities_paths import make_cli
    return make_cli(booted_framework, tmp_path, monkeypatch)


# --- the old menu entries open their page ----------------------------------------------------------------------------

@pytest.fixture
def window(qapp, tmp_path, monkeypatch, memory_keyring):
    from PySide6.QtWidgets import QDialog, QMessageBox

    import orchestrator.Core.mojo as mojo_package
    from orchestrator.Core.framework import FrameworkManager
    from orchestrator.Core.mojo import MojoReport
    from orchestrator.gui import main_window, plain

    class FakeMojo:
        def __init__(self, framework, checks=None, contact=()):
            self.framework = framework

        def run(self):
            return MojoReport([])

    monkeypatch.setattr(mojo_package, "Mojo", FakeMojo)
    monkeypatch.setattr(mojo_package, "offers", lambda framework, report: [])
    monkeypatch.chdir(tmp_path)
    (tmp_path / "configs").mkdir()

    class TmpFramework(FrameworkManager):
        def __init__(self, *args, **kwargs):
            super().__init__(str(tmp_path / "configs"))
            self.info.installation_path = tmp_path

    monkeypatch.setattr(main_window, "FrameworkManager", TmpFramework)
    shown = []
    monkeypatch.setattr(QDialog, "exec", lambda self: (self.show(), shown.append(self), 0)[2])
    monkeypatch.setattr(plain, "_exec", lambda box: QMessageBox.No)
    real = main_window.MainOrchestrator._open_tool_permissions
    monkeypatch.setattr(main_window.MainOrchestrator, "_open_tool_permissions",
                        lambda self, first_run=False: None if first_run else real(self))
    win = main_window.MainOrchestrator(REPO_ROOT)
    win.shown = shown
    qapp.processEvents()
    yield win
    win.update_timer.stop()
    for child in win.findChildren(QDialog):
        child.close()
    win.close()
    qapp.processEvents()


ENTRIES = {"act_file_settings": ("general", None), "act_ditto_settings": ("ditto", "ai"),
           "act_tools_permissions": ("ditto", "permissions"), "act_tools_folders": ("ditto", "folders"),
           "act_signin_secrets": ("language_model", "secrets"), "act_signin_overview": ("language_model", "sign_in"),
           "act_signin_key_storage": ("language_model", "key_storage"),
           "act_signin_openai_key": ("language_model", "sign_in"), "act_tools_update": ("update", None)}


def test_every_old_entry_opens_its_page_of_the_one_window(qapp, window):
    from PySide6.QtGui import QAction
    from orchestrator.gui.settings_window import SettingsDialog
    for name, (page, section) in ENTRIES.items():
        window.findChild(QAction, name).trigger()
        qapp.processEvents()
        dialog = window.shown[-1]
        assert isinstance(dialog, SettingsDialog), name
        assert (dialog.page_name(), dialog.section_name(dialog.page_name())) == (page, section), name
        if name == "act_signin_openai_key":
            assert dialog.sign_in.selected_provider() == "openai"
        dialog.close()
        qapp.processEvents()


def test_saving_the_ai_settings_page_keeps_it_open_and_tells_the_main_window(qapp, window, monkeypatch):
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Ok))
    told = []
    monkeypatch.setattr(window, "ai_settings_saved", told.append)
    window.findChild(QAction, "act_ditto_settings").trigger()
    qapp.processEvents()
    dialog = window.shown[-1]
    ai = dialog.ai_settings
    ai.cb_provider.setCurrentIndex(ai.cb_provider.findData("ollama"))
    ai._save_config()
    assert told and told[-1]["provider"] == "ollama", told
    assert dialog.isVisible() and ai.isVisible(), "Save keeps the page open"
    assert "ollama" in dialog.lbl_status.text().lower() or "Ollama" in dialog.lbl_status.text()
    dialog.close()
