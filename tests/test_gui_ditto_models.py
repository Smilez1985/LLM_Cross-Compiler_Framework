"""
Ditto > Local Models..., Ditto > Evaluation... and the chat panel's "Options for this question" (W1': every
`llm-cli ditto models ...`, `ditto ask --no-tools/--provider/--model` and `ditto eval` action by mouse).

Offscreen: the entries are reachable from the menu bar (the Ditto menu is shared with Ditto > Local Runtime...), each
handler calls the same core function as the CLI with the values of the fields, questions default to "No", long work
runs off the GUI thread with progress and Cancel, the saved AI settings never change, and every text exists in English
and German. No real download, model call or server: fakes record the core calls.
"""

import json
import re
import threading
import time
from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT

pytest.importorskip("PySide6")

GIB = 1024 ** 3


def wait_until(qapp, condition, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("timed out")


def on_gui_thread() -> bool:
    return threading.current_thread() is threading.main_thread()


def entry(tier, **kw):
    from orchestrator.runtime.catalog import ModelEntry
    values = {"tier": tier, "repo": "example/Model-GGUF", "revision": "c" * 40, "file": f"{tier}.gguf",
              "size": 2 * GIB, "sha256": "1" * 64, "license": "apache-2.0"}
    values.update(kw)
    return ModelEntry.model_validate(values)


class FakeRuntime:
    """A LocalLLMRuntime stand-in for the models: records each core call and whether it ran on the GUI thread."""

    def __init__(self, tmp_path, models=None, recommended="small", states=None):
        models = models or [entry("small"), entry("default", size=5 * GIB), entry("big", on_request=True),
                            entry("loose", sha256=None, size=None)]
        self.catalog = SimpleNamespace(models=models, model=self._model)
        self.models_dir = tmp_path / "models" / "ditto"
        self.recommended = recommended
        self.preferred = None       # the tier chosen with `runtime start --model`
        self.states = dict(states or {})
        self.calls = []
        self.block = False          # a download that waits until it is stopped
        self.stopped = None

    def _model(self, tier):
        for m in self.catalog.models:
            if m.tier == tier:
                return m
        raise KeyError(f"Unknown model tier '{tier}'. Known: {', '.join(m.tier for m in self.catalog.models)}")

    def choose_tier(self, prefer=None):
        self.calls.append(("choose_tier", None, on_gui_thread()))
        return self._model(self.recommended), "8 GiB RAM: the small model fits"

    def preferred_tier(self):
        return self.preferred

    def model_state(self, m):
        return self.states.get(m.tier, "missing")

    def model_ready(self, m):
        return self.states.get(m.tier) == "ready"

    def download_model(self, tier, progress=None, stop=None):
        from orchestrator.runtime.llama_server import IntegrityError
        from orchestrator.utils.download import DownloadStopped
        self.calls.append(("download_model", tier, on_gui_thread()))
        model = self._model(tier)
        if not model.pinned:
            raise IntegrityError(f"No sha256 and size pinned for {model.repo}/{model.file}; refusing to download it.")
        progress(model.file, model.size // 2, model.size)
        if self.block:
            stop.wait(10)
            if stop.is_set():
                self.stopped = True
                raise DownloadStopped("stopped")
        progress(model.file, model.size, model.size)
        self.states[tier] = "ready"
        return self.models_dir / model.file

    def called(self, name):
        return [(arg, gui) for n, arg, gui in self.calls if n == name]


@pytest.fixture
def models_ui(qapp, tmp_path, monkeypatch):
    from orchestrator.gui import ditto_models_window as module
    rt = FakeRuntime(tmp_path)
    asked, shown, answers = [], [], []

    def answer(parent, title, text):
        asked.append((text, on_gui_thread()))
        return answers.pop(0) if answers else False
    monkeypatch.setattr(module, "ask", answer)
    monkeypatch.setattr(module, "plain_box", lambda parent, kind, title, text, **k: shown.append((kind, text)))
    win = module.LocalModelsWindow(SimpleNamespace(), runtime_factory=lambda: rt, lang="en")
    wait_until(qapp, lambda: win.view is not None)
    ns = SimpleNamespace(win=win, rt=rt, asked=asked, shown=shown, answers=answers, qapp=qapp)
    yield ns
    if win.busy:
        win.cancel()
        wait_until(qapp, lambda: not win.busy)
    win.close()
    win.deleteLater()


def cells(table, row):
    return [table.item(row, c).text() for c in range(table.columnCount())]


# --- the menu bar ----------------------------------------------------------------------------------------------------

class TestMenu:

    def test_the_ditto_menu_opens_both_windows_with_every_object_of_cli_paths(self, qapp, tmp_path, monkeypatch,
                                                                             memory_keyring):
        from PySide6.QtCore import QObject
        from orchestrator.Core.framework import FrameworkManager
        from orchestrator.gui import ditto_eval_window, ditto_models_window, main_window
        from orchestrator.gui.menus import ditto_models as menu

        (tmp_path / "configs").mkdir()

        class TmpFramework(FrameworkManager):
            def __init__(self, *args, **kwargs):
                super().__init__(str(tmp_path / "configs"))
                self.info.installation_path = tmp_path

        rt = FakeRuntime(tmp_path)
        monkeypatch.setattr(main_window, "FrameworkManager", TmpFramework)
        monkeypatch.setattr(main_window.MainOrchestrator, "_open_tool_permissions", lambda self, first_run=False: None)
        monkeypatch.setattr(ditto_models_window.LocalModelsWindow, "_runtime", lambda self: rt)
        monkeypatch.setattr(ditto_eval_window.EvaluationWindow, "_runtime", lambda self: rt)
        win = main_window.MainOrchestrator(REPO_ROOT)
        qapp.processEvents()
        try:
            titles = [a.text().replace("&", "") for a in win.menuBar().actions()]   # wave 6: titles with mnemonics
            assert "Ditto" in titles and titles.index("Ditto") < titles.index("Security")
            ditto = next(a.menu() for a in win.menuBar().actions() if a.text().replace("&", "") == "Ditto")
            assert ditto.objectName() == menu.DITTO_MENU
            # wave 6 (Q2): the local models and the evaluation sit under Ditto > Advanced
            advanced = next(a.menu() for a in ditto.actions() if a.menu() is not None)
            assert advanced.objectName() == "menu_ditto_advanced"
            names = {a.objectName(): a for a in advanced.actions()}
            assert names[menu.MODELS_ACTION].text() == "Local Models..." and win.act_ditto_models is \
                names[menu.MODELS_ACTION]
            assert names[menu.EVAL_ACTION].text() == "Evaluation..." and win.act_ditto_eval is names[menu.EVAL_ACTION]
            assert menu.ditto_menu(win, win.menuBar()) is ditto, "the shared menu is found, not made twice"

            names[menu.MODELS_ACTION].trigger()
            models = win.ditto_models_window
            assert isinstance(models, ditto_models_window.LocalModelsWindow) and models.isVisible()
            wait_until(qapp, lambda: models.view is not None)
            names[menu.EVAL_ACTION].trigger()
            evaluation = win.ditto_eval_window
            assert isinstance(evaluation, ditto_eval_window.EvaluationWindow) and evaluation.isVisible()
            for command, name in menu.CLI_PATHS.items():
                assert win.findChild(QObject, name) is not None, (command, name)
            for name in menu.WINDOW_OBJECTS:
                assert win.findChild(QObject, name) is not None, name

            names[menu.MODELS_ACTION].trigger()
            names[menu.EVAL_ACTION].trigger()
            assert win.ditto_models_window is models and win.ditto_eval_window is evaluation, "the same windows"
            assert len(win.findChildren(ditto_models_window.LocalModelsWindow)) == 1
            wait_until(qapp, lambda: models.view is not None and models._load_token is None)
        finally:
            win.close()

    def test_an_existing_ditto_menu_is_used(self, qapp):
        from PySide6.QtWidgets import QMainWindow, QMenu
        from orchestrator.gui.menus import ditto_models as menu
        window = QMainWindow()
        bar = window.menuBar()
        bar.addMenu("File")
        security = bar.addMenu("Security")
        shared = QMenu("Ditto", bar)
        shared.setObjectName("menu_ditto")
        shared.addAction("Local Runtime...")
        bar.insertMenu(security.menuAction(), shared)
        window.framework = SimpleNamespace()
        menu.install(window, bar)
        assert [a.text() for a in bar.actions()] == ["File", "Ditto", "Security"]
        assert [a.text() for a in shared.actions()] == ["Local Runtime...", "Local Models...", "Evaluation..."]
        fresh = QMainWindow()
        fresh.menuBar().addMenu("File")
        fresh.menuBar().addMenu("Security")
        fresh.framework = SimpleNamespace()
        menu.install(fresh, fresh.menuBar())
        assert [a.text() for a in fresh.menuBar().actions()] == ["File", "Ditto", "Security"]
        window.deleteLater()
        fresh.deleteLater()

    def test_cli_paths_cover_every_person_facing_command_and_option_of_the_area(self):
        from orchestrator import cli
        from orchestrator.gui.menus import ditto_models as menu
        named = set(menu.CLI_PATHS) | set(menu.MACHINE_ONLY)
        assert not set(menu.CLI_PATHS) & set(menu.MACHINE_ONLY)
        commands = {"ditto models list": cli.models_list, "ditto models download": cli.models_download,
                    "ditto ask": cli.ditto_ask, "ditto eval": cli.ditto_eval}
        assert "ditto models" in menu.CLI_PATHS
        assert set(cli.ditto_models.commands) == {"list", "download"}
        for path, command in commands.items():
            assert path in menu.CLI_PATHS, path
            for param in command.params:
                if param.param_type_name == "option":
                    assert any(f"{path} {o}" in named for o in param.opts), (path, param.opts)
        for path in named:
            words = path.split()
            if words[-1].startswith("--"):
                command = commands[" ".join(words[:-1])]
                assert any(words[-1] in p.opts for p in command.params), path


# --- Ditto > Local Models... (`ditto models list`) -------------------------------------------------------------------

class TestModelsList:

    def test_the_table_shows_the_catalogue_from_the_same_call_off_the_gui_thread(self, models_ui):
        win, rt = models_ui.win, models_ui.rt
        assert rt.called("choose_tier") == [(None, False)], "the hardware probe runs off the GUI thread"
        assert win.table.rowCount() == 4
        assert cells(win.table, 0) == ["small", "small.gguf", "2.0 GiB", "apache-2.0", "not downloaded",
                                       "recommended"]
        assert cells(win.table, 2)[-1] == "on request" and cells(win.table, 3)[-1] == \
            "not pinned (cannot be downloaded)"
        assert win.selected_tier() == "small", "the recommended tier is preselected"
        assert "Recommended for this computer: small (8 GiB RAM: the small model fits)" in win.lbl_recommended.text()
        assert str(rt.models_dir) in win.lbl_intro.text(), "the person sees the full models folder"

    def test_check_again_reads_the_list_again(self, models_ui):
        models_ui.rt.states["small"] = "ready"
        models_ui.win.btn_refresh.click()
        wait_until(models_ui.qapp, lambda: models_ui.win._load_token is None)
        assert cells(models_ui.win.table, 0)[4] == "downloaded"
        assert len(models_ui.rt.called("choose_tier")) == 2

    def test_a_failing_catalogue_is_said_in_plain_words(self, qapp, tmp_path):
        from orchestrator.gui.ditto_models_window import LocalModelsWindow

        def broken():
            raise OSError("no per-user folder")
        win = LocalModelsWindow(SimpleNamespace(), runtime_factory=broken, lang="en")
        try:
            wait_until(qapp, lambda: win._load_token is None)
            assert win.lbl_recommended.text() == "The models could not be listed: no per-user folder"
        finally:
            win.close()
            win.deleteLater()


# --- download (`ditto models download [TIER]`) ------------------------------------------------------------------------

class TestModelsDownload:

    def test_declining_the_question_downloads_nothing(self, models_ui):
        assert models_ui.win.btn_recommended.isEnabled()
        models_ui.win.btn_recommended.click()
        (question, gui), = models_ui.asked
        assert gui and question == (f"Recommended: 8 GiB RAM: the small model fits\n\nexample/Model-GGUF / small.gguf "
                                    f"(apache-2.0)\n\nDownload 2.0 GiB into {models_ui.rt.models_dir}?")
        assert models_ui.rt.called("download_model") == []

    def test_the_question_has_no_as_its_default_button(self, models_ui, monkeypatch):
        """The real question (plain.ask): "No" is the default and the Escape answer."""
        from PySide6.QtWidgets import QMessageBox
        from orchestrator.gui import ditto_models_window as module
        from orchestrator.gui import plain
        monkeypatch.setattr(module, "ask", plain.ask)
        seen = []

        def answer(box):
            seen.append((box.defaultButton() is box.button(QMessageBox.No),
                         box.escapeButton() is box.button(QMessageBox.No), box.textFormat()))
            return QMessageBox.No
        monkeypatch.setattr(plain, "_exec", answer)
        assert models_ui.win.download_recommended() is False
        from PySide6.QtCore import Qt
        assert seen == [(True, True, Qt.PlainText)] and models_ui.rt.called("download_model") == []

    def test_yes_downloads_the_recommended_tier_off_the_gui_thread_with_progress(self, models_ui):
        win, rt = models_ui.win, models_ui.rt
        models_ui.answers.append(True)
        assert win.download_recommended() is True
        assert not win.btn_cancel.isHidden() and not win.btn_download.isEnabled()
        wait_until(models_ui.qapp, lambda: not win.busy)
        assert rt.called("download_model") == [("small", False)]
        assert win.lbl_busy.text() == f"Verified and saved: {rt.models_dir / 'small.gguf'}"
        assert win.btn_cancel.isHidden() and win.btn_download.isEnabled()
        wait_until(models_ui.qapp, lambda: win._load_token is None)
        assert cells(win.table, 0)[4] == "downloaded", "the list is read again afterwards"

    def test_without_a_selection_the_download_is_the_model_the_server_loads(self, models_ui):
        """A tier chosen earlier for the local server is what a plain start loads: the window's own download takes it
        (with its name in the question), as `ditto models download` without a tier does."""
        win, rt = models_ui.win, models_ui.rt
        assert win.btn_recommended.text() == "Download the recommended model..."
        rt.preferred = "default"
        win.refresh()
        wait_until(models_ui.qapp, lambda: win._load_token is None)
        assert win.btn_recommended.text() == "Download the chosen model..."
        models_ui.answers.append(True)
        assert win.download_recommended() is True
        (question, _), = models_ui.asked
        assert question.startswith("Chosen earlier for the local server: default\n\nexample/Model-GGUF / default.gguf")
        wait_until(models_ui.qapp, lambda: not win.busy)
        assert rt.called("download_model") == [("default", False)]

    def test_download_selected_takes_the_chosen_tier(self, models_ui):
        win = models_ui.win
        win.table.selectRow(1)
        models_ui.answers.append(True)
        win.btn_download.click()
        (question, _), = models_ui.asked
        assert question.startswith("example/Model-GGUF / default.gguf (apache-2.0)") and "5.0 GiB" in question
        assert "Recommended:" not in question
        wait_until(models_ui.qapp, lambda: not win.busy)
        assert models_ui.rt.called("download_model") == [("default", False)]

    def test_a_download_probes_no_hardware_on_the_gui_thread(self, models_ui):
        """The recommendation is a hardware probe that takes seconds: it is read off the GUI thread with the list, and
        "Download the recommended model..." asks about the tier the window shows as recommended."""
        win, rt = models_ui.win, models_ui.rt
        win.btn_recommended.click()
        win.table.selectRow(1)
        win.btn_download.click()
        assert [q.split("\n")[0] for q, _ in models_ui.asked] == [
            "Recommended: 8 GiB RAM: the small model fits", "example/Model-GGUF / default.gguf (apache-2.0)"]
        assert rt.called("choose_tier") == [(None, False)], "only the list's probe, off the GUI thread"

    def test_the_recommended_download_waits_for_the_list(self, qapp, tmp_path, monkeypatch):
        from orchestrator.gui import ditto_models_window as module
        from orchestrator.gui.ditto_models_window import LocalModelsWindow
        shown = []
        monkeypatch.setattr(module, "plain_box", lambda parent, kind, title, text, **k: shown.append((kind, text)))
        rt = FakeRuntime(tmp_path)
        gate = threading.Event()

        def factory():
            if not on_gui_thread():
                gate.wait(10)   # the hardware probe is still running
            return rt
        win = LocalModelsWindow(SimpleNamespace(), runtime_factory=factory, lang="en")
        try:
            assert not win.btn_recommended.isEnabled(), "nothing is recommended before the list is read"
            assert win.download_recommended() is False and rt.called("choose_tier") == []
            assert shown == [("information", "Reading the catalogue and this computer's hardware…")]
            gate.set()
            wait_until(qapp, lambda: win.view is not None)
            assert win.btn_recommended.isEnabled()
        finally:
            gate.set()
            win.close()
            win.deleteLater()

    def test_nothing_selected_is_said(self, models_ui):
        models_ui.win.table.clearSelection()
        assert models_ui.win.download_selected() is False
        assert models_ui.shown == [("information", "Select a model in the list first.")]

    def test_an_existing_model_is_not_downloaded_again(self, models_ui):
        models_ui.rt.states["small"] = "ready"
        assert models_ui.win.download_recommended() is False
        assert models_ui.asked == [] and models_ui.shown == [("information", "small.gguf is already downloaded.")]

    def test_an_unpinned_model_is_refused_by_the_download_itself(self, models_ui):
        win = models_ui.win
        win.table.selectRow(3)
        assert win.download_selected() is True
        assert models_ui.asked == [], "nothing to ask: the core refuses before anything is fetched"
        wait_until(models_ui.qapp, lambda: not win.busy)
        (kind, text), = models_ui.shown
        assert kind == "warning" and "refusing to download it" in text

    def test_cancel_stops_the_download_and_keeps_the_part(self, models_ui):
        win, rt = models_ui.win, models_ui.rt
        rt.block = True
        models_ui.answers.append(True)
        win.download_recommended()
        wait_until(models_ui.qapp, lambda: rt.called("download_model"))
        assert win.download_recommended() is False and models_ui.shown[-1][1].startswith("A download is running")
        win.btn_cancel.click()
        assert win.lbl_busy.text().startswith("Cancelling")
        wait_until(models_ui.qapp, lambda: not win.busy)
        assert rt.stopped and win.lbl_busy.text().startswith("The download was cancelled.")
        assert ".part" in win.lbl_busy.text()


# --- the chat panel: options for one question (`ditto ask --no-tools / --provider / --model`) ----------------------

class RecordingWorker:
    made = []

    def __init__(self, ditto, question, history, confirm):
        self.ditto, self.question = ditto, question
        RecordingWorker.made.append(self)
        for name in ("answered", "failed", "tool_used"):
            setattr(self, name, SimpleNamespace(connect=lambda f: None))

    def start(self):
        pass

    def isRunning(self):
        return False


@pytest.fixture
def chat(qapp, tmp_path, monkeypatch):
    from orchestrator.Core.config_manager import ConfigManager
    from orchestrator.Core.ditto_manager import DittoCoder
    from orchestrator.gui import chat_window
    monkeypatch.setattr(chat_window, "ChatWorker", RecordingWorker)
    RecordingWorker.made = []
    config = ConfigManager(tmp_path / "configs")
    config.set("ai_provider", "ollama")   # local: set up without a key, so Send stays on
    framework = SimpleNamespace(config=config, chat_feed=None, tool_registry=None, job_service=None,
                                secrets_manager=SimpleNamespace(get_secret=lambda name: None))
    framework.ditto_manager = DittoCoder(framework)
    panel = chat_window.ChatWindow(framework)
    wait_until(qapp, lambda: bool(panel.lbl_access.text()))
    assert panel.btn_send.isEnabled()
    shown = []
    monkeypatch.setattr("orchestrator.gui.chat_panel.panel.plain_box",
                        lambda parent, kind, title, text, **k: shown.append((kind, text)))
    yield SimpleNamespace(panel=panel, framework=framework, config=config, shown=shown, qapp=qapp)
    panel.close()
    panel.deleteLater()


def notices(panel):
    from PySide6.QtWidgets import QLabel
    return [label.text() for label in panel.feed_view.findChildren(QLabel)]


class TestChatOptions:

    def test_the_options_row_is_hidden_until_asked_for_and_offers_the_catalogue(self, chat):
        options = chat.panel.options
        assert options.row.isHidden() and options.btn_toggle.text() == "Options for this question"
        options.btn_toggle.click()
        assert not options.row.isHidden()
        ids = [options.cb_provider.itemData(i) for i in range(options.cb_provider.count())]
        assert ids[0] == "" and options.cb_provider.itemText(0) == "As in the AI settings"
        assert set(ids[1:]) == set(chat.framework.ditto_manager.client.catalog.ids())
        options.cb_provider.setCurrentIndex(options.cb_provider.findData("ollama"))
        suggested = chat.framework.ditto_manager.client.catalog.get("ollama").suggested_models
        assert [options.cb_model.itemText(i) for i in range(1, options.cb_model.count())] == list(suggested)

    def test_a_plain_question_is_asked_as_before(self, chat):
        chat.panel.txt_input.setText("Which targets are there?")
        chat.panel.btn_send.click()
        (worker,) = RecordingWorker.made
        assert worker.ditto is chat.framework.ditto_manager and worker.use_tools is True
        assert worker.question == "Which targets are there?"

    def test_without_tools_is_for_this_question_only(self, chat):
        panel = chat.panel
        panel.options.btn_toggle.click()
        panel.options.chk_no_tools.setChecked(True)
        panel.txt_input.setText("Hello")
        panel.btn_send.click()
        (worker,) = RecordingWorker.made
        assert worker.use_tools is False and worker.ditto is chat.framework.ditto_manager
        assert any("This question: without tools. The AI settings stay unchanged." in t for t in notices(panel))
        assert not panel.options.chk_no_tools.isChecked(), "reset after the question was sent"
        panel._on_ai_error("stand-in: the turn is over")
        panel.txt_input.setText("Again")
        panel.btn_send.click()
        assert RecordingWorker.made[-1].use_tools is True

    def test_another_provider_and_model_leave_the_saved_settings_unchanged(self, chat):
        from orchestrator.Core.ditto_manager import DittoCoder
        panel, config = chat.panel, chat.config
        panel.options.btn_toggle.click()
        panel.options.cb_provider.setCurrentIndex(panel.options.cb_provider.findData("anthropic"))
        panel.options.cb_model.setEditText("model-x")
        panel.txt_input.setText("Hello")
        panel.btn_send.click()
        (worker,) = RecordingWorker.made
        assert isinstance(worker.ditto, DittoCoder) and worker.ditto is not chat.framework.ditto_manager
        assert worker.ditto.config.get("ai_provider") == "anthropic"
        assert worker.ditto.config.get("ai_model") == "model-x"
        assert worker.ditto.framework is chat.framework, "the same framework, tools and permissions"
        assert config.get("ai_provider") == "ollama" and config.get("ai_model") != "model-x"
        assert chat.framework.ditto_manager.config is config
        assert any("This question: provider anthropic, model model-x." in t for t in notices(panel))
        assert panel.options.cb_provider.currentIndex() == 0 and panel.options.cb_model.currentText() == ""

    def test_a_provider_the_catalogue_does_not_know_is_refused(self, chat):
        panel = chat.panel
        panel.options.btn_toggle.click()
        panel.options.cb_provider.addItem("gone", "no-such-provider")
        panel.options.cb_provider.setCurrentIndex(panel.options.cb_provider.count() - 1)
        panel.txt_input.setText("Hello")
        panel.btn_send.click()
        assert RecordingWorker.made == [] and panel.txt_input.text() == "Hello", "nothing sent, nothing lost"
        (kind, text), = chat.shown
        assert kind == "warning"
        assert text.startswith("The question was not sent: Unknown AI provider 'no-such-provider'")

    def test_the_options_are_off_while_ditto_answers(self, chat):
        chat.panel._set_thinking(True)
        assert not chat.panel.options.row.isEnabled()
        chat.panel._set_thinking(False)
        assert chat.panel.options.row.isEnabled()

    def test_the_options_follow_the_language(self, chat):
        chat.panel._language_changed("de")
        assert chat.panel.options.btn_toggle.text() == "Optionen für diese Frage"
        assert chat.panel.options.chk_no_tools.text() == "Ohne Werkzeuge antworten"
        chat.panel._language_changed("en")

    def test_the_real_worker_passes_use_tools_to_ditto(self, qapp):
        from orchestrator.gui.chat_window import ChatWorker
        seen = []

        class Ditto:
            def chat(self, question, history, confirm=None, use_tools=True, on_tool=None):
                seen.append((question, use_tools))
                return SimpleNamespace(text="ok")
        worker = ChatWorker(Ditto(), "Hi", None, None)
        assert worker.use_tools is True
        worker.use_tools = False
        worker.run()
        worker2 = ChatWorker(Ditto(), "Again", None, None)
        worker2.run()
        assert seen == [("Hi", False), ("Again", True)]


# --- Ditto > Evaluation... (`ditto eval`) -----------------------------------------------------------------------------

def fake_report(label, kind="healing", rate=1.0, retrieval=None):
    from orchestrator.evals.ditto_eval import Attempt, CaseResult, EvalReport
    case = SimpleNamespace(id=f"{label}-case", kind=kind)
    report = EvalReport([CaseResult(case, [Attempt(rate == 1, 1.5)])], setup={"label": label, "model": "m"})
    report.to_dict = lambda: {"setup": report.setup, "pass_rate": report.pass_rate}
    if retrieval is not None:
        report.retrieval = lambda: retrieval
    return report


class FakeRun:
    """Stands in for eval_run.run_evaluation: records the call, tells events like the real run, returns reports."""

    def __init__(self):
        self.calls = []
        self.block = False
        self.error = None

    def __call__(self, framework, cases, **kw):
        from orchestrator.evals.ditto_eval import Attempt, CaseResult, EvalCancelled
        ditto = kw["make_ditto"]() if any(c.kind != "retrieval" for c in cases) else None
        self.calls.append(SimpleNamespace(cases=[c.id for c in cases], kw=kw, gui=on_gui_thread(), ditto=ditto))
        events = kw["events"]
        with events.busy("start_tier", tier="small"):
            pass
        events.run_started("tier small", "Local", "m", len(cases), kw["repeat"])
        events.result(CaseResult(cases[0], [Attempt(True, 1.25)]))
        if self.block:
            kw["stop"].wait(10)
            if kw["stop"].is_set():
                raise EvalCancelled(runs=[fake_report("tier small")])
        if self.error is not None:
            raise self.error
        events.result(CaseResult(cases[-1], [Attempt(False, 2.0, problems=["no fix named"])]))
        events.server_replaced("mine")
        return [fake_report("tier small"),
                fake_report("retrieval fused", "retrieval", retrieval={"hit@1": 0.5, "hit@3": 1.0, "hit@k": 1.0,
                                                                      "mrr": 0.75, "empty_when_irrelevant": 1.0,
                                                                      "latency_p50_ms": 12.0})]


@pytest.fixture
def eval_ui(qapp, tmp_path, monkeypatch):
    from orchestrator.evals import eval_run
    from orchestrator.gui import ditto_eval_window as module
    from orchestrator.llm import load_catalog
    rt = FakeRuntime(tmp_path, states={"small": "ready"})
    run = FakeRun()
    monkeypatch.setattr(eval_run, "run_evaluation", run)
    asked, shown, answers = [], [], []

    def answer(parent, title, text):
        asked.append((text, on_gui_thread()))
        return answers.pop(0) if answers else False
    monkeypatch.setattr(module, "ask", answer)
    monkeypatch.setattr(module, "plain_box", lambda parent, kind, title, text, **k: shown.append((kind, text)))
    values = {"ai_provider": "anthropic"}
    config = SimpleNamespace(get=lambda key, default=None: values.get(key, default), config_dir=None,
                             set=lambda key, value: values.update({key: value}))
    ditto = SimpleNamespace(config=config, framework=None, client=SimpleNamespace(catalog=load_catalog()),
                            _secrets=None, _registry_override=None)
    framework = SimpleNamespace(config=config, ditto_manager=ditto)
    win = module.EvaluationWindow(framework, runtime_factory=lambda: rt, lang="en")
    ns = SimpleNamespace(win=win, rt=rt, run=run, asked=asked, shown=shown, answers=answers, qapp=qapp,
                         module=module, values=values)
    yield ns
    if win.busy:
        win.cancel()
        wait_until(qapp, lambda: not win.busy)
    win.close()
    win.deleteLater()


def tick(widget, data):
    from PySide6.QtCore import Qt
    for i in range(widget.count()):
        if widget.item(i).data(Qt.UserRole) == data:
            widget.item(i).setCheckState(Qt.Checked)
            return
    raise AssertionError(data)


class TestEvaluation:

    def test_the_window_lists_cases_kinds_variants_and_tiers(self, eval_ui):
        from orchestrator.evals import eval_run
        from orchestrator.evals.ditto_eval import KINDS, RAG_VARIANTS
        win = eval_ui.win
        assert win.lst_cases.count() == len(eval_run.all_cases())
        assert set(win.chk_kinds) == set(KINDS) and set(win.chk_variants) == set(RAG_VARIANTS)
        assert win.chk_variants["fused"].isChecked() and not win.chk_variants["rerank"].isChecked()
        tiers = [win.lst_tiers.item(i).text() for i in range(win.lst_tiers.count())]
        assert tiers[0] == "small (small.gguf, 2.0 GiB, downloaded)" and len(tiers) == 4
        assert win.sp_repeat.minimum() == 1 and win.sp_repeat.maximum() == 20
        assert [win.cb_effort.itemData(i) for i in range(win.cb_effort.count())] == ["", "low", "medium", "high"]
        assert "A cloud provider bills every request" in win.lbl_cost.text()

    def test_declining_the_question_runs_nothing(self, eval_ui):
        win = eval_ui.win
        win.chk_kinds["healing"].setChecked(True)
        assert win.start() is False
        (question, gui), = eval_ui.asked
        assert gui and "against: the provider and model in the AI settings" in question
        assert "This provider runs in the cloud and bills every request" in question, "the configured provider"
        assert eval_ui.run.calls == []

    def test_the_question_names_a_cloud_provider_of_the_fallback_chain(self, eval_ui):
        """A local model is free, but the fallback chain of the AI settings applies to the run too: a provider in it
        that is not on this computer is named before the run instead of "nothing is billed"."""
        win = eval_ui.win
        eval_ui.values["ai_fallback_chain"] = ["local", "openai:model-c"]
        win.chk_kinds["healing"].setChecked(True)
        tick(win.lst_tiers, "small")
        assert win.start() is False
        question = eval_ui.asked[-1][0]
        assert "nothing is billed" not in question
        assert "fallback chain of the AI settings (openai)" in question and "a cloud provider there bills" in question
        eval_ui.values["ai_fallback_chain"] = ["local"]
        assert win.start() is False
        assert "Each case calls a model on this computer; nothing is billed." in eval_ui.asked[-1][0]

    def test_the_question_says_that_retrieval_downloads_its_models_on_first_use(self, eval_ui):
        win = eval_ui.win
        win.chk_kinds["retrieval"].setChecked(True)
        assert win.start() is False
        assert "downloaded on first use" in eval_ui.asked[-1][0]

    def test_the_question_has_no_as_its_default_button(self, eval_ui, monkeypatch):
        from PySide6.QtWidgets import QMessageBox
        from orchestrator.gui import plain
        monkeypatch.setattr(eval_ui.module, "ask", plain.ask)
        seen = []
        monkeypatch.setattr(plain, "_exec", lambda box: seen.append(
            box.defaultButton() is box.button(QMessageBox.No)) or QMessageBox.No)
        assert eval_ui.win.start() is False and seen == [True] and eval_ui.run.calls == []

    def test_a_run_with_tiers_kinds_and_repeat_goes_off_the_gui_thread(self, eval_ui):
        from orchestrator.Core.ai_override import EFFORT_KEYS
        from orchestrator.evals import eval_run
        win = eval_ui.win
        win.chk_kinds["healing"].setChecked(True)
        win.sp_repeat.setValue(3)
        win.cb_provider.setCurrentIndex(win.cb_provider.findData("openai"))
        eval_ui.rt.states["default"] = "unverified"   # downloaded, checked when its server starts
        tick(win.lst_tiers, "small")
        tick(win.lst_tiers, "default")
        assert win.cb_provider.currentIndex() == 0 and not win.cb_provider.isEnabled(), "a tier runs the local model"
        win.cb_effort.setCurrentIndex(win.cb_effort.findData("low"))
        eval_ui.answers.append(True)
        assert win.start() is True
        (question, _), = eval_ui.asked
        assert "the local model tiers small, default, one after the other" in question
        assert "Each case calls a model on this computer; nothing is billed." in question
        wait_until(eval_ui.qapp, lambda: not win.busy)
        (call,) = eval_ui.run.calls
        assert call.gui is False and call.kw["tiers"] == ("small", "default") and call.kw["repeat"] == 3
        assert call.kw["provider"] is None and call.kw["model"] is None and call.kw["effort"] == "low"
        assert call.kw["make_runtime"]() is eval_ui.rt
        assert call.cases == [c.id for c in eval_run.select_cases(kinds=("healing",))]
        overlay = call.ditto.config.values
        assert overlay["ai_provider"] == "local" and all(overlay[k] == "low" for k in EFFORT_KEYS)
        assert eval_ui.values == {"ai_provider": "anthropic"}, "the saved settings stay unchanged"
        log = win.log.toPlainText()
        assert "tier small: Local / m" in log and "pass " in log and "fail " in log and "no fix named" in log
        assert "Your local server (mine) was replaced" in log
        assert win.lbl_busy.text() == "Done: 2 runs."
        assert [win.tbl_results.horizontalHeaderItem(c).text() for c in range(win.tbl_results.columnCount())] == \
            ["Run", "Model", "Passed", "healing", "Avg s/case"]
        assert win.tbl_results.item(0, 0).text() == "tier small" and not win.tbl_retrieval.isHidden()
        assert win.tbl_retrieval.item(0, 4).text() == "0.750"
        assert win.btn_save.isEnabled()

    def test_a_tier_that_is_not_downloaded_is_refused_before_the_question(self, eval_ui):
        """Starting a tier stops the person's own server before it finds the model missing, and the core's message
        names a CLI command: the window refuses first and names Ditto > Local Models... instead."""
        win = eval_ui.win
        win.chk_kinds["healing"].setChecked(True)
        tick(win.lst_tiers, "small")
        tick(win.lst_tiers, "default")   # not downloaded
        assert win.start() is False
        assert eval_ui.shown == [("warning", "These local model tiers are not downloaded yet: default. Download "
                                             "them first in Ditto > Advanced > Local Models..., or untick them.")]
        assert eval_ui.asked == [] and eval_ui.run.calls == [] and "llm-cli" not in eval_ui.shown[0][1]
        win.chk_kinds["healing"].setChecked(False)
        win.chk_kinds["retrieval"].setChecked(True)   # retrieval needs no tier: nothing to refuse
        assert win.start() is False and len(eval_ui.shown) == 1 and len(eval_ui.asked) == 1

    def test_provider_model_and_variants_are_passed(self, eval_ui):
        win = eval_ui.win
        win.chk_kinds["chat"].setChecked(True)
        win.chk_kinds["retrieval"].setChecked(True)
        win.chk_variants["rerank"].setChecked(True)
        win.cb_provider.setCurrentIndex(win.cb_provider.findData("ollama"))
        win.cb_model.setEditText("model-y")
        eval_ui.answers.append(True)
        assert win.start() is True
        (question, _), = eval_ui.asked
        assert "provider ollama, model model-y; and the knowledge-base search" in question
        assert "nothing is billed" in question
        wait_until(eval_ui.qapp, lambda: not win.busy)
        (call,) = eval_ui.run.calls
        assert call.kw["rag_variants"] == ("fused", "rerank") and call.kw["provider"] == "ollama"
        assert call.kw["model"] == "model-y" and call.ditto.config.values == {"ai_provider": "ollama",
                                                                            "ai_model": "model-y"}

    def test_retrieval_only_needs_no_ai_model(self, eval_ui):
        win = eval_ui.win
        win.chk_kinds["retrieval"].setChecked(True)
        eval_ui.answers.append(True)
        win.start()
        (question, _), = eval_ui.asked
        assert "the knowledge-base search with the local embedding model" in question
        assert "No AI model is called; nothing is billed." in question
        wait_until(eval_ui.qapp, lambda: not win.busy)
        assert eval_ui.run.calls[0].ditto is None

    def test_single_cases_and_a_cases_file(self, eval_ui, tmp_path, monkeypatch):
        win = eval_ui.win
        cases = tmp_path / "own cases.yml"
        cases.write_text(json.dumps({"cases": [{"id": "own-1", "kind": "healing", "log": "No space left",
                                                "expect": {"target": "HOST"}},
                                               {"id": "own-2", "kind": "healing", "log": "Killed"}]}),
                         encoding="utf-8")
        monkeypatch.setattr(eval_ui.module, "_open_file", lambda parent, title, filters: str(cases))
        win.btn_cases_file.click()
        assert win.txt_cases_file.text() == str(cases) and win.lst_cases.count() == 2
        tick(win.lst_cases, "own-2")
        eval_ui.answers.append(True)
        win.start()
        wait_until(eval_ui.qapp, lambda: not win.busy)
        (call,) = eval_ui.run.calls
        assert call.cases == ["own-2"] and call.kw["cases_file"] == cases
        win.btn_cases_default.click()
        assert win.cases_file is None and win.txt_cases_file.text() == "the installation's cases"

    def test_an_unreadable_cases_file_keeps_the_old_one(self, eval_ui, tmp_path, monkeypatch):
        win = eval_ui.win
        bad = tmp_path / "bad.yml"
        bad.write_text("cases: []\n", encoding="utf-8")
        monkeypatch.setattr(eval_ui.module, "_open_file", lambda parent, title, filters: str(bad))
        assert win.choose_cases_file() is False
        assert win.cases_file is None and eval_ui.shown[-1][1].startswith("The cases cannot be read:")

    def test_no_matching_case_is_said_and_nothing_runs(self, eval_ui, tmp_path, monkeypatch):
        win = eval_ui.win
        cases = tmp_path / "c.yml"
        cases.write_text(json.dumps({"cases": [{"id": "own-1", "kind": "healing", "log": "x"}]}), encoding="utf-8")
        win.set_cases_file(cases)
        win.chk_kinds["chat"].setChecked(True)
        assert win.start() is False
        assert eval_ui.shown == [("warning", "The evaluation stopped: No case matches the filters.")]
        assert eval_ui.asked == [] and eval_ui.run.calls == []

    def test_cancel_keeps_the_finished_runs(self, eval_ui):
        win = eval_ui.win
        eval_ui.run.block = True
        win.chk_kinds["healing"].setChecked(True)
        eval_ui.answers.append(True)
        win.start()
        wait_until(eval_ui.qapp, lambda: eval_ui.run.calls)
        assert win.start() is False and eval_ui.shown[-1][1].startswith("An evaluation is running")
        win.btn_cancel.click()
        assert win.lbl_busy.text().startswith("Cancelling")
        wait_until(eval_ui.qapp, lambda: not win.busy)
        assert win.lbl_busy.text().startswith("Cancelled.") and len(win.runs) == 1 and win.btn_save.isEnabled()
        assert win.tbl_results.rowCount() == 1

    def test_cancel_after_a_tier_replaced_your_server_says_so(self, qapp, tmp_path, monkeypatch):
        """The real run (evals/eval_run.run_evaluation): Cancel while a tier runs stops the eval's server, and the log
        says that the person's own server was replaced and is not running now (the question promised it)."""
        from orchestrator.Core import ai_override
        from orchestrator.evals import ditto_eval
        from orchestrator.evals.ditto_eval import Attempt
        from orchestrator.gui import ditto_eval_window as module
        waiting = threading.Event()

        class Eval:
            def __init__(self, ditto, targets_dir):
                pass

            def run(self, cases, repeat=1, on_result=None, stop=None):
                def run_case(case):
                    waiting.set()
                    stop.wait(10)   # a model answer that takes a while
                    return Attempt(True, 0.1)
                return ditto_eval.run_cases(run_case, cases, repeat, on_result, stop=stop)

        class ServerRuntime(FakeRuntime):
            pid = 42   # the person's own server (serving "mine")

            def endpoint(self):
                return SimpleNamespace(pid=self.pid, port=8080, model="mine") if self.pid else None

            def start(self, tier=None, remember=True):
                self.calls.append(("start", tier, on_gui_thread()))
                self.pid = 1001   # the tier's server replaces it

            def stop(self):
                self.calls.append(("stop", None, on_gui_thread()))
                self.pid = None

        rt = ServerRuntime(tmp_path, states={"small": "ready"})
        ditto = SimpleNamespace(reload=lambda: None, describe=lambda task: {"label": "Local", "model": "small.gguf",
                                                                            "provider": "local"})
        monkeypatch.setattr(ditto_eval, "DittoEval", Eval)
        monkeypatch.setattr(ai_override, "ditto_with", lambda base, values: ditto)
        monkeypatch.setattr(module, "ask", lambda parent, title, text: True)
        config = SimpleNamespace(get=lambda key, default=None: default, config_dir=None)
        framework = SimpleNamespace(config=config, ditto_manager=ditto, resolve_path=lambda p: tmp_path / str(p))
        win = module.EvaluationWindow(framework, runtime_factory=lambda: rt, lang="en")
        try:
            win.chk_kinds["healing"].setChecked(True)
            tick(win.lst_tiers, "small")
            assert win.start() is True
            wait_until(qapp, waiting.is_set)
            win.btn_cancel.click()
            wait_until(qapp, lambda: not win.busy)
            assert rt.called("start") == [("small", False)] and rt.called("stop") == [(None, False)]
            assert "Your local server (mine) was replaced for the evaluation" in win.log.toPlainText()
            assert win.lbl_busy.text().startswith("Cancelled.")
        finally:
            win.close()
            win.deleteLater()

    def test_a_failed_run_is_said(self, eval_ui):
        from orchestrator.evals.eval_run import EvalRunError
        eval_ui.run.error = EvalRunError("The rerank variant needs a reranker: turned off.", 2, "no_reranker")
        win = eval_ui.win
        win.chk_kinds["healing"].setChecked(True)
        eval_ui.answers.append(True)
        win.start()
        wait_until(eval_ui.qapp, lambda: not win.busy)
        assert eval_ui.shown[-1] == ("warning", "The evaluation stopped: The rerank variant needs a reranker: "
                                                "turned off.")
        assert win.runs == [] and not win.btn_save.isEnabled()

    def test_without_ditto_only_retrieval_runs(self, eval_ui, monkeypatch):
        win = eval_ui.win
        win.ditto = None
        win.chk_kinds["healing"].setChecked(True)
        assert win.start() is False and eval_ui.shown[-1][1] == "Ditto is not available, so only retrieval cases " \
                                                                "can run."

    def test_save_report_writes_the_clis_json(self, eval_ui, tmp_path, monkeypatch):
        from orchestrator.evals import eval_run
        win = eval_ui.win
        assert win.save_report() is None and eval_ui.shown[-1][1] == "There is no report yet: run an evaluation first."
        win.chk_kinds["healing"].setChecked(True)
        eval_ui.answers.append(True)
        win.start()
        wait_until(eval_ui.qapp, lambda: not win.busy)
        target = tmp_path / "reports" / "my report"
        target.parent.mkdir()
        offered = []
        monkeypatch.setattr(eval_ui.module, "_save_file", lambda parent, title, name, filters: offered.append(
            (name, filters)) or str(target))
        path = win.save_report()
        assert path == target.with_suffix(".json") and offered == [("ditto-eval.json", "JSON files (*.json)")]
        assert json.loads(path.read_text(encoding="utf-8")) == eval_run.report_data(win.runs)
        assert win.lbl_busy.text() == f"Report written to {path}"
        monkeypatch.setattr(eval_ui.module, "_save_file", lambda *a: "")
        assert win.save_report() is None, "a cancelled save dialog writes nothing"


# --- texts ---------------------------------------------------------------------------------------------------------

class TestTexts:

    def test_every_text_exists_in_english_and_german_with_the_same_placeholders(self):
        from orchestrator.utils.l10n_ditto_models import TEXTS
        assert set(TEXTS) == {"en", "de"} and set(TEXTS["en"]) == set(TEXTS["de"])
        for key, text in TEXTS["en"].items():
            assert set(re.findall(r"{(\w+)}", text)) == set(re.findall(r"{(\w+)}", TEXTS["de"][key])), key

    def test_the_texts_are_merged_and_shown_in_german(self):
        from orchestrator.utils.localization import ui_text
        assert ui_text("ditto_models.menu.models", "de") == "Lokale Modelle..."
        assert ui_text("ditto_eval.title", "de") == "Ditto bewerten"

    def test_no_rich_text_and_no_cli_command_in_the_new_texts(self):
        from orchestrator.utils.l10n_ditto_models import TEXTS
        for lang in TEXTS:
            for key, text in TEXTS[lang].items():
                assert "<" not in text and "llm-cli" not in text and "llmcf" not in text, (lang, key)

    def test_the_install_question_names_the_window_not_the_cli(self):
        from orchestrator.utils.localization import ui_text
        # wave 6 (V2): the path is built from the live menu labels (Ditto > Advanced since Q2)
        for lang, window in (("en", "Ditto > Advanced > Local Models..."),
                             ("de", "Ditto > Erweitert > Lokale Modelle...")):
            text = ui_text("runtime.mode.install_question", lang, mode="x")
            assert window in text and "llm-cli ditto models" not in text

    def test_the_german_windows(self, qapp, tmp_path):
        from orchestrator.gui.ditto_eval_window import EvaluationWindow
        from orchestrator.gui.ditto_models_window import LocalModelsWindow
        rt = FakeRuntime(tmp_path)
        models = LocalModelsWindow(SimpleNamespace(), runtime_factory=lambda: rt, lang="de")
        evaluation = EvaluationWindow(SimpleNamespace(config=None, ditto_manager=None), runtime_factory=lambda: rt,
                                      lang="de")
        try:
            wait_until(qapp, lambda: models.view is not None)
            assert models.windowTitle() == "Dittos lokale Modelle"
            assert cells(models.table, 0)[4:] == ["nicht heruntergeladen", "empfohlen"]
            assert models.btn_recommended.text() == "Empfohlenes Modell herunterladen..."
            assert evaluation.windowTitle() == "Ditto bewerten" and evaluation.btn_run.text() == "Bewertung starten..."
        finally:
            for win in (models, evaluation):
                win.close()
                win.deleteLater()


def test_the_menu_module_loads_no_window_before_it_is_used():
    """Lazy loading (RG1): the windows and the evaluation load when an entry is first used."""
    import os
    import subprocess
    import sys
    code = ("import sys; import orchestrator.gui.menus.ditto_models; "
            "print(any(m in sys.modules for m in ('orchestrator.gui.ditto_models_window', "
            "'orchestrator.gui.ditto_eval_window', 'orchestrator.evals.eval_run')))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(REPO_ROOT),
                         env={**os.environ, "QT_QPA_PLATFORM": "offscreen"}, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False"

