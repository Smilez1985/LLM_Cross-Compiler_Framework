"""The core is Qt-free (DECISIONS round 11, A3: PySide6 is the optional 'gui' extra; consolidation chain qt).

Only orchestrator/gui imports PySide6. The text table and its helpers (ui_text, tr, current_language, the du/Sie
overlay) live in orchestrator/utils/localization.py without Qt; the Qt object that tells the windows about a language
switch (LocalizationManager with its language_changed signal) lives in orchestrator/gui/localization.py and is reached
through the old name lazily. The first part pins today's behaviour of the texts (characterization), the second runs
the command line, the MCP server and the core's text helpers in a fresh interpreter in which PySide6 cannot be
imported at all.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT
from orchestrator.utils import localization


@pytest.fixture
def no_window(monkeypatch):
    """A process without the GUI: no LocalizationManager instance, German in the du form."""
    monkeypatch.setattr(localization, "_instance", None)
    monkeypatch.setattr(localization, "_form_of_address", "du")


# --- characterization: what the texts do today ----------------------------------------------------------------------

def test_tr_gives_the_default_or_the_key_while_there_is_no_window(no_window):
    assert localization.tr("btn.close") == "btn.close"
    assert localization.tr("btn.close", "Schliessen") == "Schliessen"
    assert localization.tr("no.such.key", "fallback") == "fallback"
    assert localization.current_language() == "en"


def test_current_language_and_tr_follow_the_windows_instance(monkeypatch):
    table = localization.text_tables("du")
    instance = SimpleNamespace(current_lang="de", get_text=lambda key, default=None: table["de"].get(key, default))
    monkeypatch.setattr(localization, "_instance", instance)
    assert localization.current_language() == "de"
    assert localization.tr("btn.close") == table["de"]["btn.close"]
    assert localization.ui_text("btn.close") == table["de"]["btn.close"], "ui_text reads the window's language too"


def test_ui_text_works_before_the_window_with_path_places_and_the_formal_overlay(no_window, monkeypatch):
    table = localization.text_tables("du")
    monkeypatch.setitem(table["en"], "test.qtfree.text", "Look in {path:act_file_trash}: {n}")
    monkeypatch.setitem(table["de"], "test.qtfree.text", "Schau in {path:act_file_trash}: {n}")
    from orchestrator.utils.next_steps import mouse_path
    assert localization.ui_text("test.qtfree.text", "en", n=3) == f"Look in {mouse_path('act_file_trash', 'en')}: 3"
    assert localization.ui_text("test.qtfree.text", "de", n=3) == f"Schau in {mouse_path('act_file_trash', 'de')}: 3"
    assert localization.ui_text("test.qtfree.missing", "de") == "test.qtfree.missing"
    monkeypatch.setitem(localization.FORMAL_TEXTS, "de", {"test.qtfree.text": "Schauen Sie in {path:act_file_trash}: {n}"})
    assert localization.ui_text("test.qtfree.text", "de", n=1).startswith("Schau in "), "du until the setting says Sie"
    localization.set_form_of_address("sie")
    assert localization.ui_text("test.qtfree.text", "de", n=1).startswith("Schauen Sie in ")
    assert localization.text_tables()["de"]["btn.close"] == table["de"]["btn.close"], "the du text where Sie has none"


@pytest.mark.parametrize("module", ["crawl_notice", "ditto_notice"])
def test_the_notice_hashes_are_made_from_the_text_table_as_before(no_window, module):
    """A changed hash would ask every person again to read the notice: it is still sha256 over the JSON of the plain
    language table (sorted languages, the texts of the notice's keys), the formal form only when it has one."""
    import importlib
    notice = importlib.import_module(f"orchestrator.utils.{module}")
    table = localization.text_tables("du")
    texts = {lang: [entries.get(key, "") for key in notice.TEXT_KEYS] for lang, entries in sorted(table.items())}
    formal = {lang: [entries.get(key, "") for key in notice.TEXT_KEYS]
              for lang, entries in sorted(localization.FORMAL_TEXTS.items())
              if any(key in entries for key in notice.TEXT_KEYS)}
    if formal:
        texts["formal"] = formal
    expected = hashlib.sha256(json.dumps(texts, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    assert notice.notice_sha256() == expected
    assert notice.notice_text("de") == (table["de"][notice.TEXT_KEYS[0]], table["de"][notice.TEXT_KEYS[1]])


def test_there_is_one_text_table():
    """The plain table, the table the l10n modules are merged into, the du view and the Qt object's table are one
    dict object, so a test or a module that adds a text adds it everywhere."""
    table = localization.TextTable.TRANSLATIONS
    assert localization.TRANSLATIONS is table
    assert localization.text_tables("du") is table
    assert localization.merge_text_modules(modules=()) is table
    assert {"en", "de"} <= set(table) and table["en"]["btn.close"] == "Close"


def test_the_qt_object_keeps_its_name_its_table_and_its_signal(qapp, monkeypatch):
    """The 50 importers keep `from orchestrator.utils.localization import LocalizationManager, get_instance`: the
    names resolve to the GUI's Qt object (orchestrator/gui/localization.py) on first use."""
    from orchestrator.gui import localization as gui_localization
    assert localization.LocalizationManager is gui_localization.LocalizationManager
    assert localization.get_instance is gui_localization.get_instance
    assert localization.LocalizationManager.TRANSLATIONS is localization.TRANSLATIONS
    monkeypatch.setattr(localization, "_instance", None)
    manager = localization.get_instance("de")
    assert localization._instance is manager and localization.get_instance("en") is manager, "one instance"
    assert localization.current_language() == "de" and manager.t("btn.close") == localization.ui_text("btn.close", "de")
    heard = []
    manager.language_changed.connect(heard.append)
    manager.set_language("en")
    manager.set_language("xx")   # an unknown language changes nothing and says nothing
    assert heard == ["en"] and manager.current_lang == "en"
    assert manager.get_text("no.such.key") == "[no.such.key]" and manager.get_text("no.such.key", "d") == "d"
    with pytest.raises(AttributeError):
        localization.no_such_name  # noqa: B018


# --- Qt's own standard buttons speak the window's language ----------------------------------------------------------

def test_qt_standard_buttons_follow_the_language(qapp, monkeypatch):
    """German dialogs showed Qt's English 'Close' and 'Cancel': Qt translates its standard buttons only with its own
    translation (qtbase_de) installed. The window installs it with the language and swaps it on a switch."""
    from PySide6.QtWidgets import QDialogButtonBox
    from orchestrator.gui import localization as gui_localization
    monkeypatch.setattr(localization, "_instance", None)
    manager = localization.get_instance("de")
    try:
        if gui_localization.qt_translator() is None:
            pytest.skip("this PySide6 has no qtbase_de translation")
        box = QDialogButtonBox(QDialogButtonBox.Close | QDialogButtonBox.Cancel)
        assert box.button(QDialogButtonBox.Close).text() == "Schließen"
        assert box.button(QDialogButtonBox.Cancel).text() == "Abbrechen"
        manager.set_language("en")
        qapp.processEvents()
        assert gui_localization.qt_translator() is None
        assert QDialogButtonBox(QDialogButtonBox.Close).button(QDialogButtonBox.Close).text() == "Close"
        assert box.button(QDialogButtonBox.Close).text() == "Close", "an open dialog follows the switch"
    finally:
        gui_localization.install_qt_translation("en")


def test_the_language_chosen_at_the_first_start_applies_at_once(qapp, monkeypatch, tmp_path):
    """Bug found on the way (chain qt): the first-run dialog runs with an English instance, and get_instance(choice)
    returned that instance unchanged, so the window (and Qt's own buttons) stayed English until the next start."""
    import orchestrator.main as gui_main
    from orchestrator.gui import localization as gui_localization
    monkeypatch.setattr(localization, "_instance", None)
    monkeypatch.setattr(gui_main, "root_dir", tmp_path)
    monkeypatch.setattr(gui_main, "QApplication", lambda argv: qapp)
    monkeypatch.setattr(gui_main, "PassphrasePrompt", lambda app: None)
    monkeypatch.setattr(gui_main, "set_passphrase_prompt", lambda prompt: None)
    monkeypatch.setattr(gui_main, "save_language", lambda cfg, lang: True)
    class Dialog:
        selected_lang = "de"

        def exec(self):
            return gui_main.QDialog.Accepted
    monkeypatch.setattr(gui_main, "LanguageSelectionDialog", Dialog)
    import orchestrator.gui.form_of_address as form_of_address
    monkeypatch.setattr(form_of_address, "apply_saved_form", lambda cfg: None)
    seen = []

    class Window:
        def __init__(self, app_root):
            seen.append(localization.current_language())

        def show(self):
            raise SystemExit(0)
    monkeypatch.setattr(gui_main, "MainOrchestrator", Window)
    monkeypatch.setenv("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    monkeypatch.setattr(sys, "argv", ["llm-gui"])
    qt_has_german = gui_localization.install_qt_translation("de")
    gui_localization.install_qt_translation("en")
    try:
        with pytest.raises(SystemExit):
            gui_main.main()
        assert seen == ["de"], "the main window starts in the chosen language"
        assert (gui_localization.qt_translator() is not None) == qt_has_german, "Qt's own buttons in German too"
    finally:
        gui_localization.install_qt_translation("en")


# --- without PySide6 at all -----------------------------------------------------------------------------------------

BLOCK_QT = "import sys\nsys.modules['PySide6'] = None\nsys.modules['shiboken6'] = None\n"

QT_FREE_TEXTS = BLOCK_QT + r"""
from orchestrator.utils.localization import TRANSLATIONS, current_language, text_tables, tr, ui_text
assert ui_text("btn.close", "de") == TRANSLATIONS["de"]["btn.close"] and tr("btn.close", "x") == "x"
assert current_language() == "en"
from orchestrator.runtime.device import reasons
assert reasons.person_title("host_down", "de", address="board.local")
from orchestrator.utils import crawl_notice, ditto_notice
assert crawl_notice.notice_text("de")[0] and len(crawl_notice.notice_sha256()) == 64
assert ditto_notice.notice_text("de")[0] and len(ditto_notice.notice_sha256()) == 64
from orchestrator.utils.sizes import format_size
assert format_size(532) == "532 bytes" and format_size(532, "de") == "532 Byte"
from orchestrator.tools import facts_catalog
assert facts_catalog.person_action_text("disk_full", "de") and facts_catalog.person_action_text("disk_full", "en")
from orchestrator.tools.confirmation import ui_text as confirmation_text
assert confirmation_text("btn.close", "de") == TRANSLATIONS["de"]["btn.close"]
from orchestrator.utils import glossary
assert glossary.explanation(glossary.term_ids()[0], "de")
from orchestrator.utils import next_steps as ns
ns.render(list(ns.steps())[:5], "cli", "de", "cli")
from orchestrator.Core.docker_manager import DockerManager
DockerManager()
import orchestrator.utils.localization as localization
try:
    localization.LocalizationManager
except ImportError as e:
    assert "PySide6" in str(e), e
else:
    raise SystemExit("the Qt object loaded without Qt")
print("qt-free")
"""


def _child_env(tmp_path):
    return dict(os.environ, PYTHONPATH=str(REPO_ROOT), LLM_SECRET_STORAGE="ephemeral",
                LLM_FRAMEWORK_HOME=str(tmp_path / "state"), PYTHON_KEYRING_BACKEND="keyring.backends.null.Keyring")


def test_the_core_texts_need_no_qt(tmp_path):
    run = subprocess.run([sys.executable, "-c", QT_FREE_TEXTS], cwd=tmp_path, env=_child_env(tmp_path),
                         stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert run.returncode == 0 and run.stdout.strip().endswith("qt-free"), run.stderr[-3000:]


def test_the_command_line_runs_without_qt(tmp_path):
    code = BLOCK_QT + "from orchestrator.cli import cli\ncli()"
    run = subprocess.run([sys.executable, "-c", code, "--help"], cwd=tmp_path, env=_child_env(tmp_path),
                         stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert run.returncode == 0 and "Usage:" in run.stdout and "mcp" in run.stdout, run.stderr[-3000:]


def test_the_mcp_server_answers_without_qt(tmp_path):
    """`llmcf mcp serve` over stdio: the framework boots, the server answers initialize and lists its tools."""
    code = BLOCK_QT + "from orchestrator.cli import cli\ncli()"
    calls = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}},
             {"jsonrpc": "2.0", "method": "notifications/initialized"},
             {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    server = subprocess.Popen([sys.executable, "-c", code, "-q", "mcp", "serve"], cwd=tmp_path,
                              env=_child_env(tmp_path), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        answers = []
        for call in calls:
            server.stdin.write(json.dumps(call) + "\n")
            server.stdin.flush()
            if "id" in call:
                answers.append(json.loads(server.stdout.readline()))
        _, errors = server.communicate(timeout=120)
    finally:
        if server.poll() is None:
            server.kill()
    assert answers[0]["result"]["serverInfo"]["name"] == "llm-cross-compiler", errors[-3000:]
    assert any(tool["name"] == "get_settings" for tool in answers[1]["result"]["tools"]), errors[-3000:]


def test_the_gui_entry_point_says_what_is_missing_without_qt(tmp_path):
    """`llm-gui` without the 'gui' extra prints one plain line on how to install it instead of a traceback."""
    code = BLOCK_QT + "from orchestrator.main import main\nraise SystemExit(main())"
    run = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=_child_env(tmp_path),
                         stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert run.returncode == 1 and "Traceback" not in run.stderr, run.stderr[-3000:]
    assert "gui" in run.stderr and "requirements-gui.txt" in run.stderr and "llmcf" in run.stderr, run.stderr


def test_the_missing_extra_is_added_only_from_the_hash_pinned_list(tmp_path):
    """Review of chain qt: the message also offered `pip install 'llm-cross-compiler-framework[gui]'`. The framework
    is installed from its checkout, not from the package index (the installers copy its files and install the
    hash-pinned exports with --no-emit-project), so that line would fetch whatever carries the name there, unpinned
    and unchecked. The person is told only the hash-pinned way: the installer, or requirements-gui.txt."""
    code = BLOCK_QT + "from orchestrator.main import main\nraise SystemExit(main())"
    run = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=_child_env(tmp_path),
                         stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert run.returncode == 1, run.stderr[-3000:]
    message = run.stderr
    assert "llm-cross-compiler-framework" not in message and "[gui]" not in message, message
    installs = [message[m.end():] for m in re.finditer(r"pip install", message)]
    # --no-deps: the export is complete and hash-pinned, and pip resolving it again could pick other versions
    assert installs and all(i.startswith(" --require-hashes --no-deps -r requirements-gui.txt") for i in installs), \
        installs
    assert "--headless" in message, "or the installer again, without --headless"


def test_only_the_gui_imports_qt():
    """PySide6 and shiboken6 are imported only below orchestrator/gui (and by the GUI's entry point shim, which
    catches the missing package): every other module, also inside a function, stays Qt-free."""
    import ast
    offenders = []
    for path in sorted((REPO_ROOT / "orchestrator").rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith("orchestrator/gui/"):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=rel)):
            names = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) and not node.level else [])
            offenders += [f"{rel}:{node.lineno} {name}" for name in names
                          if name.split(".")[0] in ("PySide6", "shiboken6")]
    assert offenders == []
