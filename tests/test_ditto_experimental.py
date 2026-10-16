"""Ditto is optional and experimental (DECISIONS round 8, Q-2): the "Experimental" badge on Ditto's panel and in its
setup, the one-time notice "Use at your own risk and responsibility. The creator of LLMCF is not liable." (shown once
per person, in English and German, never blocking anything else), and `llm-cli ditto` printing it once."""

import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from conftest import REPO_ROOT
from orchestrator.utils import localization
from orchestrator.utils.ditto_notice import DittoNotice, notice_sha256, notice_text

SENTENCE_EN = "Use at your own risk and responsibility. The creator of LLMCF is not liable."
SENTENCE_DE = "Nutzung auf eigene Gefahr und Verantwortung. Der Ersteller von LLMCF haftet nicht."


def test_the_notice_says_it_in_both_languages_and_claims_no_more_than_the_licence():
    for lang, sentence in (("en", SENTENCE_EN), ("de", SENTENCE_DE)):
        title, text = notice_text(lang)
        assert sentence in text and "LICENSE.txt" in text and title
    licence = (REPO_ROOT / "LICENSE.txt").read_text(encoding="utf-8")
    assert "not liable for any damages" in licence and "WITHOUT WARRANTY" in licence
    # nothing beyond the licence: no indemnity, no promise, no waiver the person signs
    for lang in ("en", "de"):
        text = notice_text(lang)[1].lower()
        for word in ("indemn", "guarantee", "garant", "waive", "verzicht", "agree", "zustimm"):
            assert word not in text, (lang, word)


def test_shown_once_per_person_and_again_when_the_text_changes(tmp_path, monkeypatch):
    notice = DittoNotice(tmp_path / "state")
    assert notice.needed()
    notice.acknowledge("gui")
    assert not notice.needed() and notice.acknowledged_via() == "gui"
    stored = json.loads(notice.path.read_text())
    assert set(stored) == {"notice_sha256", "via", "acknowledged_at"} and stored["notice_sha256"] == notice_sha256()
    if os.name == "posix":
        assert notice.path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError):
        notice.acknowledge("mcp")   # an AI never answers it
    de = dict(localization.LocalizationManager.TRANSLATIONS["de"])
    de["chat.ditto.notice.text"] += " Neu."
    monkeypatch.setitem(localization.LocalizationManager.TRANSLATIONS, "de", de)
    assert notice.needed(), "a changed text in any language is shown again"


@pytest.mark.parametrize("content", ["{broken", "[]", '"x"', '{"notice_sha256": "x"}'])
def test_a_damaged_state_file_shows_it_again(tmp_path, content):
    (tmp_path / DittoNotice.FILE_NAME).write_text(content)
    assert DittoNotice(tmp_path).needed()


# --- the GUI --------------------------------------------------------------------------------------------------------

@pytest.fixture
def panel(qapp, tmp_path, monkeypatch):
    from orchestrator.Core.config_manager import ConfigManager
    from orchestrator.gui import chat_window

    from test_chat_panel import FakeDitto, FakeWorker

    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(chat_window, "ChatWorker", FakeWorker)
    FakeWorker.started = []
    made = []

    def factory():
        framework = SimpleNamespace(config=ConfigManager(tmp_path / "configs"), ditto_manager=FakeDitto(),
                                    chat_feed=None, tool_registry=None, job_service=None)
        made.append(chat_window.ChatWindow(framework))
        return made[-1]

    yield factory
    for item in made:
        item.close()
        item.deleteLater()


def test_the_panel_carries_the_badge_and_the_notice_until_the_person_reads_it(panel, tmp_path):
    first = panel()
    assert first.header.badge.text() == "Experimental" and SENTENCE_EN in first.header.badge.toolTip()
    assert not first.notice.isHidden() and SENTENCE_EN in first.notice.text.text()
    from test_chat_panel import FakeWorker
    first.txt_input.setText("Which boards run RKLLM?")
    first.send_message()
    assert FakeWorker.started == ["Which boards run RKLLM?"], "the notice never blocks the chat or anything else"
    first.notice.btn_ok.click()
    assert first.notice.isHidden()
    assert DittoNotice(Path(tmp_path / "state")).acknowledged_via() == "gui"
    second = panel()
    assert second.notice.isHidden() and second.header.badge.text() == "Experimental", \
        "once per person; the badge stays"


def test_the_panel_speaks_german_too(panel):
    first = panel()
    first._language_changed("de")
    assert first.header.badge.text() == "Experimentell" and SENTENCE_DE in first.notice.text.text()


def test_the_ai_settings_carry_the_badge_and_the_sentence(qapp, tmp_path):
    from orchestrator.gui import dialogs
    from orchestrator.Core.config_manager import ConfigManager

    dialog = dialogs.AIConfigurationDialog(SimpleNamespace(config=ConfigManager(tmp_path / "configs"),
                                                           secrets_manager=None))
    try:
        assert dialog.lbl_experimental.text().startswith("Experimental")
        assert SENTENCE_EN in dialog.lbl_experimental.text()
        assert dialog.lbl_experimental.textFormat() == dialogs.Qt.PlainText
    finally:
        dialog.close()


# --- the CLI --------------------------------------------------------------------------------------------------------

def test_ditto_commands_print_it_once_at_a_terminal(tmp_path, monkeypatch):
    from orchestrator import cli as cli_module

    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(cli_module, "_notice_terminal", lambda: True)
    runner = CliRunner()
    first = runner.invoke(cli_module.cli, ["ditto", "models", "list"])
    assert SENTENCE_EN in first.output
    assert DittoNotice(tmp_path / "state").acknowledged_via() == "cli"
    second = runner.invoke(cli_module.cli, ["ditto", "models", "list"])
    assert SENTENCE_EN not in second.output


def test_without_a_terminal_nothing_is_printed_or_recorded(tmp_path, monkeypatch):
    from orchestrator import cli as cli_module

    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(cli_module, "_notice_terminal", lambda: False)
    result = CliRunner().invoke(cli_module.cli, ["ditto", "models", "list"])
    assert SENTENCE_EN not in result.output and DittoNotice(tmp_path / "state").needed()


def test_other_commands_never_print_it(tmp_path, monkeypatch):
    from orchestrator import cli as cli_module

    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(cli_module, "_notice_terminal", lambda: True)
    result = CliRunner().invoke(cli_module.cli, ["targets", "list"])
    assert SENTENCE_EN not in result.output and DittoNotice(tmp_path / "state").needed()


# --- the documents --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name, sentence", [("README.md", SENTENCE_EN), ("docs/Features.md", SENTENCE_EN),
                                            ("README_DE.md", SENTENCE_DE), ("docs/Features_DE.md", SENTENCE_DE)])
def test_readme_and_features_carry_the_notice(name, sentence):
    text = re.sub(r"\s+", " ", (REPO_ROOT / name).read_text(encoding="utf-8"))
    assert sentence in text
