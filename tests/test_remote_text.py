"""A board's words and a running deploy's state as text (consolidation chain c2-remote-base): the one-line tail of a
board's answer (runtime/device/remote.py), the kept output lines of a deploy (DeploymentManager._keep) and the three
copies of a deploy event's text (the GUI's Deployment tab, `llmcf deploy run` and the deploy tool for AIs) become
orchestrator/remote/text.py. These tests pin each copy's exact words, in English and German.
"""

import pytest

from orchestrator.Core.deployment_manager import DeployEvent

HOST = "myboard.local"
LABEL = "Living room board"

EVENTS = {
    "waiting": DeployEvent("waiting", elapsed=75.9, limit=300, step="upload"),
    "waiting_install": DeployEvent("waiting", elapsed=5, limit=60, step="install"),
    "back": DeployEvent("back", done=1_300_000, total=3_072_000, step="upload"),
    "back_install": DeployEvent("back", step="install"),
    "uploading": DeployEvent("uploading", done=2_500_000, total=150_000_000),
    "installing": DeployEvent("installing", done=10, total=10),
}

PERSON = {
    "en": {
        "waiting": "Waiting for the board at myboard.local: 1:15 of at most 5:00. The upload continues where it "
                   "stopped as soon as the board answers again.",
        "waiting_install": "Waiting for the board at myboard.local: 0:05 of at most 1:00. The installation runs on "
                           "the board on its own; it is followed again as soon as the board answers.",
        "back": "The board answers again: the upload continues at 1.3 MB of 3.1 MB.",
        "back_install": "The board answers again: checking whether the installation on the board has finished (it "
                        "is never started twice).",
        "uploading": "Uploading the package: 2.5 MB of 150.0 MB",
        "installing": "Installing on the board...",
    },
}

AI = {
    "waiting": "waiting for the board Living room board: 75 s of at most 300 s (the network dropped; the upload "
               "continues when it answers)",
    "waiting_install": "waiting for the board Living room board: 5 s of at most 60 s (the network dropped; the "
                       "installation is followed again when it answers)",
    "back": "Living room board answers again: the upload continues at 1.3 MB of 3.1 MB",
    "back_install": "Living room board answers again: the installation on the board is followed again (it is never "
                    "started twice)",
    "uploading": "uploading to Living room board: 2.5 MB of 0.15 GB",
    "installing": "installing on Living room board",
}


def person_texts(lang):
    """The person's words as the language table has them (pinned once from the l10n table, not retyped here)."""
    from orchestrator.utils.localization import ui_text
    return {
        "waiting": ui_text("device.wait.status", lang, address=HOST, elapsed="1:15", limit="5:00"),
        "waiting_install": ui_text("device.wait.status_install", lang, address=HOST, elapsed="0:05", limit="1:00"),
        "back": ui_text("device.wait.back", lang, done="1.3 MB", total="3.1 MB"),
        "back_install": ui_text("device.wait.install", lang),
        "uploading": ui_text("device.deploy.uploading", lang, done="2.5 MB", total="150.0 MB"),
        "installing": ui_text("device.deploy.installing", lang),
    }


@pytest.mark.parametrize("kind", sorted(EVENTS))
@pytest.mark.parametrize("lang", ["en", "de"])
def test_the_cli_line(kind, lang, monkeypatch):
    import orchestrator.cli as cli
    from rich.markup import escape
    monkeypatch.setattr(cli, "_config_language", lambda ctx: lang)
    want = person_texts(lang)[kind] + (" (Ctrl+C)" if kind.startswith("waiting") else "")
    assert cli._deploy_event_text(None, EVENTS[kind], HOST) == escape(want)


@pytest.mark.parametrize("kind", sorted(EVENTS))
def test_the_gui_line(kind, monkeypatch):
    pytest.importorskip("PySide6")
    from orchestrator.gui import deployment_window
    from orchestrator.utils import localization
    for lang in ("en", "de"):
        monkeypatch.setattr(localization, "current_language", lambda lang=lang: lang)
        assert deployment_window.event_text(EVENTS[kind], HOST) == person_texts(lang)[kind]


def test_the_person_words_are_the_tables():
    assert person_texts("en") == PERSON["en"]


@pytest.mark.parametrize("kind", sorted(EVENTS))
def test_the_ai_line(kind):
    from orchestrator.tools.tools_deploy import event_text
    assert event_text(EVENTS[kind], LABEL) == AI[kind]


def test_the_ai_sizes():
    from orchestrator.tools.tools_deploy import _size
    assert [_size(n) for n in (None, 0, 99_999_999, 10 ** 8, 2_340_000_000)] == \
        ["size unknown", "0.0 MB", "100.0 MB", "0.10 GB", "2.34 GB"]


def test_a_boards_answer_on_one_line():
    from orchestrator.runtime.device.remote import _tail
    assert _tail("") == "no output" and _tail(None) == "no output"
    assert _tail("line one\nline\ttwo\x1b[31m red\r\n") == "line one line two [31m red"
    assert _tail("x" * 400) == "x" * 300 and _tail("ab" * 10, limit=5) == "babab"


def test_the_kept_output_lines():
    from orchestrator.Core.deployment_manager import REMOTE_LINE_CHARS, REMOTE_OUTPUT_LINES, DeploymentManager
    lines = []
    DeploymentManager._keep(lines, "ok\r\n")
    DeploymentManager._keep(lines, "a\tb\x07c")
    DeploymentManager._keep(lines, "y" * 600)
    assert lines == ["ok", "a b c", "y" * REMOTE_LINE_CHARS]
    for i in range(REMOTE_OUTPUT_LINES + 5):
        DeploymentManager._keep(lines, str(i))
    assert len(lines) == REMOTE_OUTPUT_LINES and lines[-1] == str(REMOTE_OUTPUT_LINES + 4)
