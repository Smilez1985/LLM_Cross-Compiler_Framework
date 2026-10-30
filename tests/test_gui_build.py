"""GUI parity of the build area (W1': every action a person can start with the CLI is also startable by mouse):
Build > Targets... (targets list / validate), Build > Build History... (build status [--all] [REQUEST_ID]),
File > Settings... (config show / config repos), the Builder (Targets..., Check target, every format for a target
without capabilities, optimization 'fast', priority) and the module wizard (description, provider/model for one
analysis, Ditto's proposal saved through DittoManager.save_module with its base image pinned).

Each mouse path is reached from the menu bar or its window, calls the same core function as the CLI with the
right arguments, asks with "No" as the default where something is removed, runs long work off the GUI thread and
has its texts in English and German."""

import json
import string
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from conftest import REPO_ROOT

pytest.importorskip("PySide6")

ROCKCHIP_YML = REPO_ROOT / "targets" / "Rockchip" / "target.yml"
DIGEST = "sha256:" + "cd" * 32
GUI = REPO_ROOT / "orchestrator" / "gui"
NEW_FILES = [GUI / "targets_window.py", GUI / "build_history_window.py", GUI / "settings_window.py",
             GUI / "menus" / "build.py"]


class Boxes:
    """plain_box / ask without a window: records (icon, title, text, whether "No" is the default) and answers."""

    def __init__(self, monkeypatch, answer=None):
        from PySide6.QtWidgets import QMessageBox
        from orchestrator.gui import plain
        self.shown = []
        self.answer = QMessageBox.No if answer is None else answer

        def fake_exec(box):
            self.shown.append((box.icon(), box.windowTitle(), box.text(),
                               box.defaultButton() is not None and box.defaultButton() is box.button(QMessageBox.No)))
            return self.answer
        monkeypatch.setattr(plain, "_exec", fake_exec)

    @property
    def texts(self):
        return [text for _, _, text, _ in self.shown]


# --- texts --------------------------------------------------------------------------------------------------------

def test_every_text_exists_in_english_and_german_with_the_same_placeholders():
    from orchestrator.utils import localization
    from orchestrator.utils.l10n_build import TEXTS
    fields = string.Formatter()

    def names(text):
        return {name for _, name, _, _ in fields.parse(text) if name}

    from orchestrator.utils.next_steps import fill_paths
    assert set(TEXTS["en"]) == set(TEXTS["de"])
    for key, english in TEXTS["en"].items():
        assert names(english) == names(TEXTS["de"][key]), key
        # merged; a {path:...} place is filled from the live labels (wave 6, V2)
        assert localization.ui_text(key, "de") == fill_paths(TEXTS["de"][key], "de")
    assert "orchestrator.utils.l10n_build" in localization.TEXT_MODULES


def test_the_new_windows_keep_every_text_plain():
    from test_gui_plain import box_offenders, tooltip_offenders
    for path in NEW_FILES:
        source = path.read_text(encoding="utf-8")
        assert box_offenders(source) == [], path
        assert tooltip_offenders(source) == [], path
        assert "setHtml" not in source and "RichText" not in source, path


# --- the menu bar -------------------------------------------------------------------------------------------------

@pytest.fixture
def window(qapp, tmp_path, monkeypatch, memory_keyring):
    from orchestrator.Core.framework import FrameworkManager
    from orchestrator.gui import main_window

    monkeypatch.chdir(tmp_path)
    (tmp_path / "configs").mkdir()

    class TmpFramework(FrameworkManager):
        def __init__(self, *args, **kwargs):
            super().__init__(str(tmp_path / "configs"))
            self.info.installation_path = tmp_path

    monkeypatch.setattr(main_window, "FrameworkManager", TmpFramework)
    monkeypatch.setattr(main_window.MainOrchestrator, "_open_tool_permissions", lambda self, first_run=False: None)
    win = main_window.MainOrchestrator(REPO_ROOT)
    qapp.processEvents()
    yield win
    win.close()


def test_the_build_menu_follows_file_and_settings_sits_above_exit(window):
    from PySide6.QtCore import QObject
    from orchestrator.gui.menus.build import CLI_PATHS, WINDOW_OBJECTS
    titles = [a.text().replace("&", "") for a in window.menuBar().actions()]   # wave 6: titles with mnemonics
    assert titles[:2] == ["File", "Build"], titles
    # Round 36 (on purpose): Build Container Images... follows Build History...; Build Dependencies... comes last
    assert [a.text() for a in window.menu_build.actions()] == ["Targets...", "Build History...",
                                                               "Build Container Images...", "Build Dependencies..."]
    file_menu = window.menuBar().actions()[0].menu()
    assert [a.text() for a in file_menu.actions() if not a.isSeparator()][-2:] == ["Settings...", "Exit"]
    assert (window.act_build_targets.objectName(), window.act_build_history.objectName(),
            window.act_file_settings.objectName()) == ("act_build_targets", "act_build_history", "act_file_settings")
    missing = [name for name in set(CLI_PATHS.values()) - set(WINDOW_OBJECTS)
               if window.findChild(QObject, name) is None]
    assert missing == [], "every mouse path of CLI_PATHS exists in the main window"
    for command in ("targets list", "targets validate", "module create", "module generate-ai --provider",
                    "module generate-ai --model", "build start --format", "build start --optimization",
                    "build start --priority", "build status", "build status --all", "config show", "config repos",
                    "config repos --add", "config repos --remove", "config output-folder", "build save",
                    "build save --to"):
        assert command in CLI_PATHS, command


def test_each_menu_entry_opens_its_window_with_the_framework(window, monkeypatch):
    from orchestrator.gui import build_history_window, settings_window, targets_window
    opened = []
    for module, cls in ((targets_window, "TargetsDialog"), (build_history_window, "BuildHistoryDialog"),
                        (settings_window, "SettingsDialog")):
        monkeypatch.setattr(getattr(module, cls), "exec", lambda self, name=cls: opened.append((name, self)) or 0)
    window.act_build_targets.trigger()
    window.act_build_history.trigger()
    window.act_file_settings.trigger()
    assert [name for name, _ in opened] == ["TargetsDialog", "BuildHistoryDialog", "SettingsDialog"]
    assert all(dialog.framework is window.framework for _, dialog in opened)
    assert opened[2][1]._choose_output_folder == window._choose_output_folder, "File > Output Folder... offered"


def test_the_menu_labels_follow_the_language(qapp, monkeypatch):
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QMainWindow
    from orchestrator.gui.menus import build as menu_build
    monkeypatch.setattr(menu_build, "current_language", lambda: "de")
    win = QMainWindow()
    win.i18n = SimpleNamespace(t=lambda key, default=None: default)
    file_menu = win.menuBar().addMenu("File")
    win.act_output_folder = QAction("Output Folder...", win)
    file_menu.addAction(win.act_output_folder)
    file_menu.addAction(QAction("Exit", win))
    win.menuBar().addMenu("Help")
    menu_build.install(win, win.menuBar())
    assert [a.text() for a in win.menuBar().actions()] == ["File", "Build", "Help"]
    assert [a.text() for a in win.menu_build.actions()] == ["Targets…", "Build-Verlauf…", "Build-Abhängigkeiten…"]
    assert [a.text() for a in file_menu.actions()] == ["Output Folder...", "Einstellungen…", "Exit"]
    assert win.act_output_folder.objectName() == "act_file_output_folder"
    win.close()


def test_the_status_bar_points_to_the_build_history_not_a_command(window, monkeypatch):
    import os
    store = SimpleNamespace(read=lambda: [{"pid": os.getpid() + 1, "status": "building", "program": "cli",
                                           "summary": {"model": "org/m", "target": "Rockchip"}}])
    monkeypatch.setattr(window.framework.orchestrator, "request_store", store, raising=False)
    window._other_builds_at = -1e9
    window._show_other_builds()
    message = window.status.currentMessage()
    assert "Build > Build History..." in message and "llm-cli" not in message


# --- Build > Targets... ---------------------------------------------------------------------------------------------

def targets_framework(tmp_path):
    from orchestrator.Core.build_service import BuildService
    from orchestrator.Core.target_manager import TargetManager
    folder = tmp_path / "targets"
    (folder / "Rockchip").mkdir(parents=True)
    (folder / "Rockchip" / "target.yml").write_text(ROCKCHIP_YML.read_text(encoding="utf-8"), encoding="utf-8")
    (folder / "MyBoard").mkdir()
    (folder / "MyBoard" / "target.yml").write_text(yaml.safe_dump(
        {"metadata": {"name": "My board", "architecture_family": "aarch64"}}), encoding="utf-8")
    tm = TargetManager(SimpleNamespace(config=SimpleNamespace(targets_dir=str(folder)), resolve_path=Path))
    framework = SimpleNamespace(target_manager=tm, get_component=lambda name: tm if name == "target_manager" else None)
    framework.build_service = BuildService(framework)
    return framework, folder


def test_targets_window_lists_ids_boards_formats_and_support_level(qapp, tmp_path):
    from orchestrator.gui.targets_window import TargetsDialog
    framework, folder = targets_framework(tmp_path)
    dialog = TargetsDialog(framework, select="MyBoard", lang="en")
    rows = {dialog.table.item(r, 0).text(): [dialog.table.item(r, c).text() for c in range(6)]
            for r in range(dialog.table.rowCount())}
    assert set(rows) == {"Rockchip", "MyBoard"}
    assert "rk3588" in rows["Rockchip"][3] and rows["Rockchip"][4] == "gguf, rkllm, rknn"
    assert rows["Rockchip"][5] == "official"
    assert rows["MyBoard"][4] == "- (no capabilities declared)" and rows["MyBoard"][5] == "-"
    assert dialog.selected() == "MyBoard" and str(folder) in dialog.intro.text()
    german = TargetsDialog(framework, lang="de")
    assert german.table.horizontalHeaderItem(5).text() == "Unterstützungsgrad"
    dialog.close()
    german.close()


def test_targets_window_names_an_invalid_capabilities_block(qapp, tmp_path):
    """Review: a target whose capabilities block fails the schema was listed as "no capabilities declared"; the
    window names it invalid and points to Check target (the Builder's note says the same)."""
    from orchestrator.gui.targets_window import TargetsDialog
    framework, folder = targets_framework(tmp_path)
    caps = yaml.safe_load(ROCKCHIP_YML.read_text(encoding="utf-8"))["capabilities"]
    caps["default_board"] = "rk9999"
    (folder / "Broken").mkdir()
    (folder / "Broken" / "target.yml").write_text(yaml.safe_dump({"metadata": {"name": "Broken"},
                                                                  "capabilities": caps}), encoding="utf-8")
    dialog = TargetsDialog(framework, lang="en")
    rows = {dialog.table.item(r, 0).text(): dialog.table.item(r, 4).text() for r in range(dialog.table.rowCount())}
    assert "invalid" in rows["Broken"] and "Check target" in rows["Broken"]
    assert rows["MyBoard"] == "- (no capabilities declared)"
    german = TargetsDialog(framework, lang="de")
    rows = {german.table.item(r, 0).text(): german.table.item(r, 4).text() for r in range(german.table.rowCount())}
    assert "ungültig" in rows["Broken"]
    dialog.close()
    german.close()


def test_check_target_calls_the_core_check_and_shows_each_error(qapp, tmp_path, monkeypatch):
    from orchestrator.gui.targets_window import TargetsDialog
    framework, folder = targets_framework(tmp_path)
    boxes = Boxes(monkeypatch)
    calls = []
    real = framework.target_manager.validate
    monkeypatch.setattr(framework.target_manager, "validate",
                        lambda name, reread=False: calls.append((name, reread)) or real(name, reread=reread))
    dialog = TargetsDialog(framework, select="MyBoard", lang="en")
    dialog.btn_check.click()
    assert calls == [("MyBoard", True)], "the file is read again: an edited target.yml is checked as it is now"
    assert "declares no capabilities" in boxes.texts[-1]
    caps = yaml.safe_load(ROCKCHIP_YML.read_text(encoding="utf-8"))["capabilities"]
    caps["default_board"] = "rk9999"
    (folder / "MyBoard" / "target.yml").write_text(yaml.safe_dump(
        {"metadata": {"name": "My board"}, "capabilities": caps}), encoding="utf-8")
    dialog.btn_check.click()
    assert "capabilities block is invalid" in boxes.texts[-1] and "rk9999" in boxes.texts[-1]
    dialog.close()


def test_reload_reads_the_targets_folder_and_refreshes_the_callers_lists(qapp, tmp_path, monkeypatch):
    from orchestrator.gui import targets_window
    framework, folder = targets_framework(tmp_path)
    reloaded = []

    def press_reload(self):
        (folder / "New").mkdir()
        (folder / "New" / "target.yml").write_text(yaml.safe_dump({"metadata": {"name": "New"}}), encoding="utf-8")
        self.btn_reload.click()
        assert any(t.id == "New" for t in self.targets)
        return 0

    monkeypatch.setattr(targets_window.TargetsDialog, "exec", press_reload)
    targets_window.open_targets_dialog(None, framework, on_reload=lambda: reloaded.append(1))
    assert reloaded == [1]


# --- the Builder ----------------------------------------------------------------------------------------------------

@pytest.fixture
def builder(qapp, make_framework, tmp_path, monkeypatch):
    from orchestrator.gui import builder_tab
    from test_review_capabilities_paths import framework_with_targets, make_tab
    monkeypatch.setattr(builder_tab, "QMessageBox", SimpleNamespace(information=lambda *a: None,
                                                                    warning=lambda *a: None,
                                                                    critical=lambda *a: None))
    framework = framework_with_targets(make_framework, tmp_path / "targets_builder")
    return make_tab(framework, "MyJetson")


def test_a_target_without_capabilities_offers_every_format_and_says_so(builder):
    from orchestrator.Core.build_formats import ModelFormat
    from test_review_capabilities_paths import select
    offered = [builder.format_combo.itemData(i) for i in range(builder.format_combo.count())]
    assert offered == ["gguf", "rkllm", "rknn"] + [f.value for f in ModelFormat
                                                   if f.value not in ("hf", "gguf", "rkllm", "rknn")]
    assert not builder.lbl_no_caps.isHidden() and "not checked" in builder.lbl_no_caps.text()
    select(builder.target_combo, "Rockchip")
    assert builder.lbl_no_caps.isHidden()
    assert [builder.format_combo.itemData(i) for i in range(builder.format_combo.count())] == ["gguf", "rkllm",
                                                                                             "rknn"]


def test_an_invalid_capabilities_block_is_named_invalid_not_undeclared(builder):
    """Review: a capabilities block that fails the schema counts as none on the build path (every format, nothing
    checked), but the target does declare one: the note says it is invalid and that Check target names each error,
    instead of claiming that no capabilities are declared."""
    from orchestrator.gui.builder_tab import UNCHECKED_FORMATS
    from test_review_capabilities_paths import select
    tm = builder.framework.get_component("target_manager")
    caps = yaml.safe_load(ROCKCHIP_YML.read_text(encoding="utf-8"))["capabilities"]
    caps["default_board"] = "rk9999"
    folder = Path(tm.targets_dir) / "Broken"
    folder.mkdir()
    (folder / "target.yml").write_text(yaml.safe_dump({"metadata": {"name": "Broken"}, "capabilities": caps}),
                                       encoding="utf-8")
    tm.initialize()
    builder._refresh_targets()
    select(builder.target_combo, "Broken")
    text = builder.lbl_no_caps.text()
    assert not builder.lbl_no_caps.isHidden()
    assert "invalid" in text and "Check target" in text and "declares no capabilities" not in text
    assert builder.format_combo.count() == len(UNCHECKED_FORMATS), "what the CLI allows for it"
    select(builder.target_combo, "MyJetson")
    assert "declares no capabilities" in builder.lbl_no_caps.text()


def test_onnx_on_a_target_without_capabilities_is_submitted_as_the_cli_does(builder):
    from orchestrator.Core.build_formats import ModelFormat
    from test_review_capabilities_paths import select, submit
    builder.model_input.setEditText("Qwen/Qwen3.5-0.8B")
    select(builder.format_combo, "onnx")
    req = submit(builder)
    assert req is not None and req.target_formats == [ModelFormat.ONNX] and req.targets == ["MyJetson"]


def test_optimization_fast_and_the_priority_reach_the_request(builder):
    from orchestrator.Core.build_formats import OptimizationLevel
    from orchestrator.Core.orchestrator import PriorityLevel
    from test_review_capabilities_paths import select, submit
    builder.model_input.setEditText("Qwen/Qwen3.5-0.8B")
    assert [builder.opt_combo.itemText(i) for i in range(builder.opt_combo.count())] == [
        "BALANCED", "SPEED", "SIZE", "AGGRESSIVE", "FAST"]
    assert builder.priority_combo.currentData() == "NORMAL", "normal is the default, as on the command line"
    assert submit(builder).priority == PriorityLevel.NORMAL
    builder.opt_combo.setCurrentText("FAST")
    select(builder.priority_combo, "CRITICAL")
    req = submit(builder)
    assert req.optimization_level == OptimizationLevel.FAST and req.priority == PriorityLevel.CRITICAL
    assert [builder.priority_combo.itemData(i) for i in range(builder.priority_combo.count())] == [
        "NORMAL", "LOW", "HIGH", "URGENT", "CRITICAL"]


def test_the_builder_buttons_open_the_targets_and_check_the_selected_one(builder, monkeypatch):
    from orchestrator.gui import targets_window
    opened, checked = [], []
    monkeypatch.setattr(targets_window, "open_targets_dialog",
                        lambda parent, framework, select=None, on_reload=None: opened.append((framework, select)))
    monkeypatch.setattr(targets_window, "show_target_check",
                        lambda parent, framework, name, lang=None, intro="": checked.append((framework, name)))
    builder.btn_targets.click()
    builder.btn_check_target.click()
    assert opened == [(builder.framework, "MyJetson")] and checked == [(builder.framework, "MyJetson")]
    assert builder.btn_check_target.objectName() == "btn_builder_check_target"


# --- Build > Build History... ---------------------------------------------------------------------------------------

def record(**values):
    import os
    data = dict(build_id="req_a_001", request_id="req_a", pid=os.getpid(), process_started=None, status="completed",
                stage="Done", progress=100, model="org/model", target="Rockchip", format="gguf",
                quantization="Q4_K_M", board="rk3588", output_dir="/out/a", started="2026-10-11T10:00:00", error=None)
    data.update(values)
    return data


@pytest.fixture
def history_framework(tmp_path, monkeypatch):
    from orchestrator.Core import build_records, client_proposals, request_store
    cache = tmp_path / "cache"
    folder = build_records.records_dir(cache)
    for i in range(25):   # more than the CLI's 20 without --all: the window shows every build
        build_records.write_record(folder, record(build_id=f"req_{i:03d}_001", request_id=f"req_{i:03d}",
                                                  started=f"2026-10-10T10:{i:02d}:00"))
    build_records.write_record(folder, record(build_id="req_fail_001", request_id="req_fail", status="failed",
                                              error="out of disk space", output_dir=str(tmp_path / "out" / "fail"),
                                              started="2026-10-11T11:00:00"))
    requests = [{"request_id": "req_wait", "status": "queued", "program": "mcp", "started": "2026-10-11T12:00:00",
                 "summary": {"model": "org/queued", "target": "Rockchip"}},
                {"request_id": "req_fail", "status": "interrupted", "program": "cli", "owner": "",
                 "errors": ["out of disk space"], "started": "2026-10-11T11:00:00", "summary": {"model": "org/model"}}]
    monkeypatch.setattr(request_store, "for_config", lambda config: SimpleNamespace(read=lambda: list(requests)))
    state = tmp_path / "state"
    state.mkdir()
    client_proposals.ClientProposalStore(state).add(client_proposals.ClientProposal(
        proposal_id="hp_1", request_id="req_fail", summary="Free <b>disk</b> space", root_cause="disk full",
        manual_step="docker system df", confidence=0.7, where="HOST", proposed_by="mcp", client="Some App"))
    config = SimpleNamespace(get=lambda key, default=None: str(cache) if key == "cache_dir" else default,
                             user_state_dir=state)
    return SimpleNamespace(config=config, resolve_path=Path)


def test_the_history_lists_every_build_of_every_process(qapp, history_framework):
    from orchestrator.gui.build_history_window import BuildHistoryDialog
    dialog = BuildHistoryDialog(history_framework, lang="en")
    assert dialog.table.rowCount() == 27, "all of them, as build status --all"
    first = [dialog.table.item(0, c).text() for c in range(10)]
    assert first[0] == "req_wait" and first[1] == "-" and first[2] == "queued" and first[9] == "mcp"
    second = [dialog.table.item(1, c).text() for c in range(10)]
    assert second[:3] == ["req_fail", "req_fail_001", "failed"] and second[9] == "cli"
    assert "1 failed build" in dialog.notice.text() and "req_fail" in dialog.notice.text()
    dialog.close()


def test_the_details_show_errors_attempts_output_folder_and_proposals(qapp, history_framework, tmp_path,
                                                                     monkeypatch):
    from orchestrator.gui import build_history_window
    shown = []
    monkeypatch.setattr(build_history_window.BuildDetailsDialog, "exec",
                        lambda self: shown.append((self.windowTitle(), self.text.toPlainText())) or 0)
    dialog = build_history_window.BuildHistoryDialog(history_framework, lang="en")
    dialog.table.selectRow(1)
    dialog.btn_details.click()
    title, text = shown[-1]
    assert title == "Build req_fail"
    assert "Error: out of disk space" in text and "Started by: the person" in text
    assert "ended before it finished" in text
    assert "Attempt 1" in text and f"Output folder: {tmp_path / 'out' / 'fail'}" in text
    assert "Fix proposal hp_1 (AI-generated)" in text and "Free <b>disk</b> space" in text
    assert "not run by the framework" in text
    german = build_history_window.details_text(dialog.history, "req_fail", history_framework.config, "de")
    assert "Ausgabeordner:" in german and "Versuch 1" in german and "KI-generiert" in german
    dialog.close()


def test_the_history_reads_the_same_core_query_as_the_cli(qapp, history_framework, monkeypatch):
    from orchestrator.Core import build_history
    from orchestrator.gui.build_history_window import BuildHistoryDialog
    calls = []
    real = build_history.read_history
    monkeypatch.setattr(build_history, "read_history", lambda config, resolve: calls.append(config) or
                        real(config, resolve))
    dialog = BuildHistoryDialog(history_framework)
    dialog.btn_refresh.click()
    assert calls == [history_framework.config] * 2
    dialog.close()


# --- File > Settings... ----------------------------------------------------------------------------------------------

@pytest.fixture
def settings_framework(tmp_path):
    from orchestrator.Core.config_manager import ConfigManager
    folder = tmp_path / "cfg"
    folder.mkdir()
    token = "hf_" + "abcdefghijklmnopqrstuvwxyzABCDEFGH"
    url = "https://user:" + "FAKEpassword123" + "@git.example.com/llama.cpp.git"
    (folder / "config.yml").write_text(yaml.safe_dump({"hf_token": token, "source_repositories": {
        "llama_cpp": {"url": url}}}), encoding="utf-8")
    return SimpleNamespace(config=ConfigManager(folder), secret=token, password="FAKEpassword123")


def table_text(table):
    return [[table.item(r, c).text() for c in range(table.columnCount())] for r in range(table.rowCount())]


def test_effective_settings_with_source_and_secrets_redacted(qapp, settings_framework):
    from orchestrator.gui.settings_window import SettingsDialog
    dialog = SettingsDialog(settings_framework, lang="en")
    rows = {row[0]: row for row in table_text(dialog.table_effective)}
    assert rows["hf_token"][1] == "REDACTED" and rows["ai_provider"][2] == "default"
    assert rows["hf_token"][2].startswith("file ") and rows["hf_token"][2].endswith("config.yml")
    everything = json.dumps(table_text(dialog.table_effective) + table_text(dialog.table_repos))
    assert settings_framework.secret not in everything and settings_framework.password not in everything
    assert table_text(dialog.table_repos) == [["llama_cpp", "https://user:REDACTED@git.example.com/llama.cpp.git",
                                               "LLAMA_CPP_REPO_OVERRIDE"]]
    german = SettingsDialog(settings_framework, lang="de")
    assert {row[0]: row for row in table_text(german.table_effective)}["ai_provider"][2] == "Vorgabe"
    dialog.close()
    german.close()


def test_add_a_repository_override_through_the_core(qapp, settings_framework, monkeypatch):
    from orchestrator.Core import settings_view
    from orchestrator.gui.settings_window import SettingsDialog
    boxes = Boxes(monkeypatch)
    calls = []
    real = settings_view.add_repo_override
    monkeypatch.setattr(settings_view, "add_repo_override",
                        lambda config, name, url: calls.append((name, url)) or real(config, name, url))
    dialog = SettingsDialog(settings_framework, lang="en")
    dialog.edit_repo_name.setText("rkllm.toolkit")
    dialog.edit_repo_url.setText("https://git.example.com/rkllm.git")
    dialog.btn_repo_add.click()
    assert calls == [("rkllm.toolkit", "https://git.example.com/rkllm.git")]
    assert "RKLLM_TOOLKIT_REPO_OVERRIDE" in dialog.lbl_repo_result.text()
    saved = yaml.safe_load((Path(settings_framework.config.config_dir) / "config.yml").read_text(encoding="utf-8"))
    assert saved["source_repositories"]["rkllm.toolkit"] == {"url": "https://git.example.com/rkllm.git"}
    dialog.edit_repo_name.setText("äöü")
    dialog.edit_repo_url.setText("https://x.example/x.git")
    dialog.btn_repo_add.click()
    assert "letters or digits" in boxes.texts[-1]
    dialog.close()


def test_removing_an_override_asks_first_with_no_as_the_default(qapp, settings_framework, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from orchestrator.gui.settings_window import SettingsDialog
    boxes = Boxes(monkeypatch)   # answers No
    dialog = SettingsDialog(settings_framework, lang="en")
    dialog.table_repos.selectRow(0)
    dialog.btn_repo_remove.click()
    _, _, question, no_is_default = boxes.shown[-1]
    assert no_is_default, "the question's default answer is No"
    assert "Name: llama_cpp" in question and "LLAMA_CPP_REPO_OVERRIDE" in question
    assert "REDACTED" in question and settings_framework.password not in question
    assert str(Path(settings_framework.config.config_dir) / "config.yml") in question
    assert "llama_cpp" in settings_framework.config.get("source_repositories"), "No keeps it"
    boxes.answer = QMessageBox.Yes
    dialog.table_repos.selectRow(0)
    dialog.btn_repo_remove.click()
    assert "llama_cpp" not in settings_framework.config.get("source_repositories")
    assert "Removed repo override: llama_cpp" in dialog.lbl_repo_result.text()
    dialog.close()


def test_the_removal_question_has_no_as_its_default_button(qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from orchestrator.gui import plain
    seen = []
    monkeypatch.setattr(plain, "_exec", lambda box: seen.append(box.defaultButton() is box.button(QMessageBox.No))
                        or QMessageBox.No)
    assert plain.ask(None, "t", "q") is False and seen == [True]


# --- the module wizard ----------------------------------------------------------------------------------------------

MODULE = {"module_name": "Orange_Pi_5", "architecture": "aarch64", "sdk": "rknn-toolkit2",
          "base_os": f"debian:bookworm-slim@{DIGEST}", "packages": ["build-essential"], "cpu_flags": "",
          "cmake_flags": "", "setup_commands": "", "quantization_logic": "", "assumptions": ["NPU driver 0.9.8"],
          "base_image_reason": "", "pip_packages": ["rknn-toolkit2==2.3.0"]}


class WizardBox:
    """wizards.QMessageBox without windows: every box is recorded."""
    Information, Critical, Ok, Yes, No = 1, 2, 1, 16384, 65536
    shown = []

    def __init__(self, icon, title, text, buttons=None, parent=None):
        WizardBox.shown.append(("box", title, text))

    def setTextFormat(self, fmt):
        pass

    def exec(self):
        return 0

    @staticmethod
    def information(parent, title, text, *args):
        WizardBox.shown.append(("information", title, text))

    @staticmethod
    def critical(parent, title, text, *args):
        WizardBox.shown.append(("critical", title, text))


@pytest.fixture
def wizard(qapp, tmp_path, monkeypatch):
    from orchestrator.gui import wizards
    WizardBox.shown = []
    monkeypatch.setattr(wizards, "QMessageBox", WizardBox)
    generated = []

    class Generator:
        def __init__(self, targets_dir):
            self.targets_dir = targets_dir

        def generate_module(self, data, *args, **kwargs):
            generated.append((dict(data), kwargs))
            return Path(self.targets_dir) / str(data["module_name"])

    monkeypatch.setattr(wizards, "ModuleGenerator", Generator)
    w = wizards.ModuleCreationWizard(tmp_path / "targets")
    w.generated = generated
    yield w
    w.close()


@pytest.mark.parametrize("flags, avx512", [
    ("avx2,fma,avx512f,avx512cd,avx512vl,avx512dq,avx512bw", True),
    ("avx2,fma,avx512f,avx512cd,avx512er,avx512pf", False),      # a Xeon Phi (Knights Landing)
    ("", False),                                                  # no CPU flags: AVX-512 BW, VL, DQ not confirmed
], ids=["full-avx512", "avx512f-only", "no-flags"])
def test_the_wizard_proposes_avx512_only_with_every_part_ggml_avx512_compiles_for(wizard, tmp_path, monkeypatch,
                                                                                    flags, avx512):
    """The module wizard's flags from a probe follow the build recipe's rule (recipe.avx512_missing): GGML_AVX512 adds
    -mavx512cd/vl/dq/bw too, which a CPU with AVX-512 F alone cannot run."""
    monkeypatch.setattr(wizard, "_store_probe", lambda path: None)
    probe = tmp_path / "probe.txt"
    probe.write_text(f"ARCH=x86_64\nSUPPORTS_AVX=ON\nSUPPORTS_AVX2=ON\nSUPPORTS_AVX512=ON\nCPU_FLAGS_ALL={flags}\n")
    wizard.import_probe_file(str(probe))
    assert ("-DGGML_AVX512=ON" in wizard.cmake_flags.text()) is avx512
    assert ("-mavx512f" in wizard.cpu_flags.text()) is avx512
    assert "-DGGML_AVX2=ON" in wizard.cmake_flags.text()


def test_the_description_field_is_written_and_defaults_like_the_cli(wizard):
    wizard.name_edit.setText("My_Board")
    assert wizard.desc_edit.placeholderText() == "Target for My_Board"
    wizard.accept()
    assert wizard.generated[-1][0]["description"] == "Target for My_Board"
    wizard.desc_edit.setText("NPU board in the lab")
    wizard.accept()
    assert wizard.generated[-1][0]["description"] == "NPU board in the lab"
    assert wizard.gui_form.field("description") is not None, "the GUI tools read what the person entered"


def test_the_description_placeholder_shows_the_value_written_in_both_languages():
    """Review: the German placeholder promised "Target für <name>", but an empty field writes the CLI's default
    "Target for <name>" into target.yml (that text is not translated)."""
    from orchestrator.utils.localization import ui_text
    for lang in ("en", "de"):
        assert ui_text("bld.wizard.description_hint", lang, name="X") == "Target for X", lang


def test_the_person_s_description_is_kept_when_an_ai_filled_other_fields(wizard):
    """Review: an AI filled a field through the GUI tools, so the module is AI-made and its description says it is
    untested; the description the person typed was dropped silently. It now leads, the framework's note follows."""
    wizard.name_edit.setText("Board_D")
    wizard.desc_edit.setText("NPU board in the lab")
    assert wizard.gui_form.fill({"packages": "build-essential cmake"}).filled == ["packages"]
    wizard.accept()
    data = wizard.generated[-1][0]
    assert data["provenance"] == "ai-generated"
    assert data["description"].startswith("NPU board in the lab. ")
    assert "AI-generated target for Board_D, untested on real hardware" in data["description"]


def test_the_person_s_description_is_kept_on_the_save_module_path(wizard):
    """The same when an AI chose the base image (the module is saved through DittoManager.save_module)."""
    wizard.name_edit.setText("Board_E")
    wizard.desc_edit.setText("NPU board in the lab.")
    assert wizard.gui_form.fill({"base_image": f"debian:bookworm-slim@{DIGEST}"}).filled == ["base_image"]
    wizard.accept()
    data = wizard.generated[-1][0]
    assert data["provenance"] == "ai-generated" and data["base_os"] == f"debian:bookworm-slim@{DIGEST}"
    assert data["description"].startswith("NPU board in the lab. AI-generated target for Board_E, untested")


def test_an_ai_cannot_fill_the_description(wizard):
    """The framework writes the description of a module an AI helps with; a description an AI filled in was
    replaced without a word. The field is the person's: the GUI tools read it, and a fill is refused with why."""
    wizard.name_edit.setText("Board_F")
    outcome = wizard.gui_form.fill({"description": "Best board ever"})
    assert outcome.filled == [] and outcome.refused and outcome.refused[0][0] == "description"
    assert wizard.desc_edit.text() == ""
    assert wizard.gui_form.field("description").get() == ""


def test_a_standard_import_after_dittos_proposal_keeps_none_of_dittos_values(wizard, tmp_path, monkeypatch):
    """Review: the rule-based import of a probe replaced Ditto's proposal (the module was then saved as the person's,
    unmarked and not audited), but Ditto's packages, quantization logic and setup commands stayed in their fields
    and were written into that module. The import now replaces them too."""
    from orchestrator.gui import wizards
    wizard.on_ai_finished({**MODULE, "packages": ["libfoo-dev"], "setup_commands": "export SDK=/opt/vendor",
                           "quantization_logic": "q4) QTYPE=Q4_0 ;;"})
    probe = tmp_path / "probe.txt"
    probe.write_text("Architecture=aarch64\n", encoding="utf-8")
    monkeypatch.setattr(wizards.QFileDialog, "getOpenFileName", lambda *a, **k: (str(probe), ""))
    wizard.run_standard_import()
    wizard.accept()
    data = wizard.generated[-1][0]
    assert "libfoo-dev" not in data["packages"] and data["setup_commands"] == "" and data["quantization_logic"] == ""
    assert data.get("provenance") != "ai-generated" and "untested" not in data["description"]
    assert data["packages"][:4] == wizards.DEFAULT_PACKAGES.split()


def test_dittos_proposal_is_saved_through_save_module_and_audited(wizard, tmp_path, monkeypatch):
    from orchestrator.Core import ditto_manager
    from orchestrator.Core import module_generator   # record_module_saved's home since chain c1-imports
    saves, audits = [], []
    real = ditto_manager.DittoCoder.save_module
    monkeypatch.setattr(ditto_manager.DittoCoder, "save_module",
                        lambda self, definition, targets_dir, **kw: saves.append((dict(definition), kw)) or
                        real(self, definition, targets_dir, **kw))
    monkeypatch.setattr(module_generator, "record_module_saved",
                        lambda fm, actor, data, folder, channel: audits.append((actor, data, channel)))
    wizard.crawled_roots.add("https://docs.example/board")
    wizard.chk_knowledge.setEnabled(True)
    wizard.on_ai_finished(MODULE)
    assert wizard.desc_edit.isReadOnly() and "untested" in wizard.desc_edit.placeholderText()
    wizard.chk_knowledge.setChecked(True)
    wizard.cpu_flags.setText("-mcpu=cortex-a76")   # the person's change of Ditto's value counts
    wizard.accept()
    definition, kw = saves[-1]
    assert definition["cpu_flags"] == "-mcpu=cortex-a76" and definition["pip_packages"] == ["rknn-toolkit2==2.3.0"]
    assert kw["module_name"] == "Orange_Pi_5" and kw["knowledge_roots"] == ["https://docs.example/board"]
    data, kwargs = wizard.generated[-1]
    assert data["provenance"] == "ai-generated" and "untested on real hardware" in data["description"]
    assert data["base_os"] == f"debian:bookworm-slim@{DIGEST}"
    assert audits[-1][0] == "gui" and audits[-1][2] == "framework-gui"
    assert audits[-1][1]["provenance"] == "ai-generated"


@pytest.mark.parametrize("base, reason, refusal", [
    (f"nvidia/cuda:12.4.1-devel-ubuntu22.04@{DIGEST}", "vendor CUDA", "nvidia/cuda"),
    (f"registry.example/vendor/img@{DIGEST}", "", "base_image_reason"),
])
def test_save_module_refuses_what_the_cli_refuses(wizard, base, reason, refusal):
    wizard.on_ai_finished({**MODULE, "base_os": base, "base_image_reason": reason})
    wizard.accept()
    assert wizard.generated == [], "nothing was written"
    assert WizardBox.shown[-1][0] == "critical" and refusal in WizardBox.shown[-1][2]


def test_a_base_image_an_ai_filled_through_the_gui_tools_takes_the_same_path(wizard):
    out = wizard.gui_form.fill({"base_image": f"nvidia/cuda:12.4.1-devel-ubuntu22.04@{DIGEST}"})
    assert out.filled == ["base_image"]
    wizard.name_edit.setText("Board_X")
    wizard.accept()
    assert wizard.generated == [] and "nvidia/cuda" in WizardBox.shown[-1][2]


def test_the_person_s_own_prebuilt_cuda_choice_stays_possible(wizard):
    wizard.name_edit.setText("Board_Y")
    wizard.rad_custom.setChecked(True)
    wizard.custom_os_edit.setText(f"nvidia/cuda:12.4.1-devel-ubuntu22.04@{DIGEST}")
    wizard.accept()
    assert wizard.generated and wizard.generated[-1][0]["base_os"].startswith("nvidia/cuda:")


@pytest.fixture
def pin_lookup(monkeypatch):
    """The registry lookup of a base image's digest (recorded; None: it fails) and the wizard's worker thread run in
    place, so the answer comes back through the relay as it would from a worker; the person's answers are recorded."""
    from PySide6.QtWidgets import QMessageBox
    from orchestrator.gui import plain, wizards
    from orchestrator.utils import image_registry
    state = SimpleNamespace(asked=[], threads=[], boxes=[], answer=QMessageBox.Yes, fail=None)

    def inspect(reference, offline=False, client=None):
        state.asked.append((reference, offline))
        if state.fail is not None:
            raise state.fail
        return SimpleNamespace(pinned=f"{reference}@{DIGEST}")

    class Thread:
        def __init__(self, target=None, args=(), name=None, daemon=None):
            self.target, self.args = target, args
            state.threads.append(name)

        def start(self):
            self.target(*self.args)

    def answer(box):
        state.boxes.append((box.icon(), box.text()))
        return state.answer

    monkeypatch.setattr(image_registry, "inspect_image", inspect)
    monkeypatch.setattr(wizards.threading, "Thread", Thread)
    monkeypatch.setattr(plain, "_exec", answer)
    return state


def test_an_unpinned_base_image_an_ai_filled_is_pinned_before_the_module_is_saved(qapp, wizard, pin_lookup):
    """Final review of wave 5: `llm-cli module generate-ai` and create_target_module write an AI's base image with the
    digest its tag points at now; a base image an AI filled into the wizard through the GUI tools was saved as a bare
    tag (ubuntu:24.04). Finish now looks the digest up off the GUI thread, shows the pinned image and asks ("No" is
    the default); only then is the module saved through save_module, with exactly the reference shown."""
    assert wizard.gui_form.fill({"base_image": "ubuntu:24.04"}).filled == ["base_image"]
    wizard.name_edit.setText("Board_P")
    wizard.accept()
    assert wizard.generated == [], "nothing is saved before the digest is known and the person said yes"
    assert pin_lookup.threads == ["ai-image-pin"] and pin_lookup.asked == [("ubuntu:24.04", False)]
    qapp.processEvents()
    assert pin_lookup.boxes and f"ubuntu:24.04@{DIGEST}" in pin_lookup.boxes[-1][1]
    data = wizard.generated[-1][0]
    assert data["base_os"] == f"ubuntu:24.04@{DIGEST}" and data["provenance"] == "ai-generated"
    assert wizard.custom_os_edit.text() == "ubuntu:24.04", "the field keeps the AI's value and its mark"


def test_no_to_the_pinned_image_saves_nothing(qapp, wizard, pin_lookup):
    from PySide6.QtWidgets import QMessageBox
    pin_lookup.answer = QMessageBox.No
    assert wizard.gui_form.fill({"base_image": "python:3.12-slim"}).filled == ["base_image"]
    wizard.name_edit.setText("Board_N")
    wizard.accept()
    qapp.processEvents()
    assert pin_lookup.boxes and wizard.generated == []


def test_a_base_image_whose_digest_cannot_be_looked_up_is_not_saved(qapp, wizard, pin_lookup):
    """As `module generate-ai` refuses a proposal whose image cannot be pinned (offline mode, no registry)."""
    from orchestrator.utils.image_registry import ImageLookupError
    pin_lookup.fail = ImageLookupError("offline mode is on", code="offline", next="Turn offline mode off.")
    assert wizard.gui_form.fill({"base_image": "ubuntu:24.04"}).filled == ["base_image"]
    wizard.name_edit.setText("Board_O")
    wizard.accept()
    qapp.processEvents()
    assert wizard.generated == []
    text = pin_lookup.boxes[-1][1]
    assert "ubuntu:24.04" in text and "offline mode is on" in text and "Turn offline mode off." in text


def test_closing_the_wizard_during_the_lookup_asks_and_saves_nothing(qapp, wizard, pin_lookup):
    assert wizard.gui_form.fill({"base_image": "ubuntu:24.04"}).filled == ["base_image"]
    wizard.name_edit.setText("Board_C")
    wizard.accept()
    wizard.reject()   # the answer of the lookup is still on its way
    qapp.processEvents()
    assert pin_lookup.boxes == [] and wizard.generated == []


def test_a_pinned_ai_image_and_the_person_s_own_image_are_saved_without_a_lookup(qapp, wizard, pin_lookup):
    assert wizard.gui_form.fill({"base_image": f"ubuntu:24.04@{DIGEST}"}).filled == ["base_image"]
    wizard.name_edit.setText("Board_R")
    wizard.accept()
    assert wizard.generated[-1][0]["base_os"] == f"ubuntu:24.04@{DIGEST}"
    wizard.rad_custom.click()   # the person's own choice: no AI value, no lookup
    wizard.custom_os_edit.setText("ubuntu:24.04")
    wizard.custom_os_edit.textEdited.emit("ubuntu:24.04")
    wizard.accept()
    assert wizard.generated[-1][0]["base_os"] == "ubuntu:24.04" and pin_lookup.asked == []


def test_the_new_target_is_checked_and_the_result_shown(wizard, tmp_path):
    from orchestrator.Core.target_manager import TargetCheck
    asked = []
    tm = SimpleNamespace(initialize=lambda: asked.append("init"),
                         validate=lambda name, reread=False: asked.append((name, reread)) or TargetCheck(name, name))
    wizard.framework_manager = SimpleNamespace(target_manager=tm, config=None)
    wizard.name_edit.setText("Board_Z")
    wizard.accept()
    assert asked == ["init", ("Board_Z", True)]
    kind, _, text = WizardBox.shown[-1]
    assert kind == "information" and "Check of the new target:" in text and "declares no capabilities" in text


def test_the_ai_analysis_runs_with_the_provider_chosen_for_it_only(wizard, tmp_path, monkeypatch):
    from orchestrator.Core.config_manager import ConfigManager
    from orchestrator.Core.ditto_manager import DittoCoder
    from orchestrator.gui import wizards
    config = ConfigManager(tmp_path / "cfg")
    config.set("ai_provider", "anthropic")
    ditto = DittoCoder(config_manager=config)
    wizard.framework_manager = SimpleNamespace(ditto_manager=ditto, config=config)
    wizard._fill_run_providers()
    providers = [wizard.combo_run_provider.itemData(i) for i in range(wizard.combo_run_provider.count())]
    assert providers[0] is None and "ollama" in providers and "anthropic" in providers
    wizard.combo_run_provider.setCurrentIndex(providers.index("ollama"))
    wizard.combo_run_model.setEditText("small-model")
    probe = tmp_path / "probe.txt"
    probe.write_text("CPU_MODEL=Cortex-A76\n")
    monkeypatch.setattr(wizards.QFileDialog, "getOpenFileName", lambda *a, **k: (str(probe), ""))
    monkeypatch.setattr(DittoCoder, "is_configured", lambda self, task="chat": True)
    started = []

    class Thread:
        def __init__(self, target=None, args=(), name=None, daemon=None):
            started.append((target, args, name))

        def start(self):
            pass

    monkeypatch.setattr(wizards.threading, "Thread", Thread)
    wizard.run_ai_analysis()
    target, args, name = started[-1]
    run = args[0]
    assert name == "ai-import" and target == wizard._ai_worker, "the analysis runs off the GUI thread"
    assert run is not ditto and run.config.get("ai_provider") == "ollama"
    assert run.config.get("ai_model") == "small-model"
    assert config.get("ai_provider") == "anthropic", "the saved AI settings stay unchanged"
    assert config.get("ai_model") != "small-model"


def test_an_unknown_run_provider_is_refused_before_anything_starts(wizard, tmp_path, monkeypatch):
    from orchestrator.Core.ditto_manager import DittoError
    from orchestrator.gui import wizards
    def refuse(provider, model):
        raise DittoError("Unknown AI provider 'x'")

    ditto = SimpleNamespace(for_run=refuse, is_configured=lambda task: True)
    wizard.framework_manager = SimpleNamespace(ditto_manager=ditto)
    wizard.combo_run_provider.addItem("x", "x")
    wizard.combo_run_provider.setCurrentIndex(wizard.combo_run_provider.count() - 1)
    monkeypatch.setattr(wizards.QFileDialog, "getOpenFileName", lambda *a, **k: pytest.fail("no file dialog"))
    wizard.run_ai_analysis()
    assert "Not started: Unknown AI provider 'x'" in WizardBox.shown[-1][2]


def test_the_worker_pins_the_proposed_image_before_the_person_sees_it(qapp, tmp_path, monkeypatch):
    from orchestrator.Core.ditto_manager import ModuleDefinition
    from orchestrator.gui import wizards
    from orchestrator.utils import image_registry
    pinned_to = f"debian:bookworm-slim@{DIGEST}"
    asked = []

    def inspect(reference, offline=False, client=None):
        asked.append((reference, offline, threading.current_thread() is threading.main_thread()))
        return SimpleNamespace(pinned=pinned_to)

    real = image_registry.inspect_image
    monkeypatch.setattr(image_registry, "inspect_image", inspect)
    unpinned = ModuleDefinition(**{**MODULE, "base_os": "debian:bookworm-slim"})
    results = []
    relay = SimpleNamespace(done=SimpleNamespace(emit=lambda token, result: results.append(result)))
    ditto = SimpleNamespace(generate_module=lambda path: unpinned)
    worker = threading.Thread(target=wizards.ModuleCreationWizard._ai_worker,
                              args=(ditto, tmp_path / "p.txt", object(), relay, False))
    worker.start()
    worker.join(5)
    assert results[-1][0] == "done" and results[-1][1]["base_os"] == pinned_to
    assert asked == [("debian:bookworm-slim", False, False)], "the registry is asked off the GUI thread"
    monkeypatch.setattr(image_registry, "inspect_image", real)   # offline mode: a tag cannot be resolved
    wizards.ModuleCreationWizard._ai_worker(ditto, tmp_path / "p.txt", object(), relay, True)
    assert results[-1][0] == "error" and "cannot be pinned" in results[-1][1] and "offline" in results[-1][1]


# --- the Community Hub ------------------------------------------------------------------------------------------------

def test_a_community_target_install_is_checked_and_shown(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QLabel
    from orchestrator.gui import community_hub, targets_window
    checks = []
    monkeypatch.setattr(targets_window, "show_target_check",
                        lambda parent, framework, name, lang=None, intro="": checks.append((name, intro)))

    class Box:
        Warning, Question, Yes, No = 1, 2, 16384, 65536

        def __init__(self, *args):
            pass

        def setTextFormat(self, fmt):
            pass

        def setDefaultButton(self, button):
            pass

        def exec(self):
            return Box.Yes

    monkeypatch.setattr(community_hub, "QMessageBox", Box)
    tm = SimpleNamespace(initialize=lambda: None)
    dest = tmp_path / "targets" / "demo-board"
    hub = SimpleNamespace(comm_manager=SimpleNamespace(finish_install=lambda prepared: dest),
                          framework_manager=SimpleNamespace(target_manager=tm), status_label=QLabel(),
                          installed=SimpleNamespace(emit=lambda kind: None), refresh_list=lambda: None, lang="en")
    # The install runs in the hub's worker (knowledge chain: finish_install off the GUI thread); here the worker's
    # work and its done slot run in turn, as the hub's _run does with them.
    window = community_hub.CommunityHubWindow
    hub._run = lambda work, done, *args: done(work(*args))
    hub._finish_install = lambda prepared: window._finish_install(hub, prepared)
    hub._installed = lambda result: window._installed(hub, result)
    prepared = SimpleNamespace(trust="community", summary=lambda: "pack", manifest=SimpleNamespace(kind="target",
                                                                                                id="demo-board"))
    window.confirm_install(hub, prepared)
    assert checks == [("demo-board", "Check of the installed target:")]
    assert "Installed target demo-board" in hub.status_label.text()


# --- the CLI shares the one-run choice -------------------------------------------------------------------------------

def test_generate_ai_with_provider_leaves_the_settings_unchanged(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from orchestrator import cli
    from orchestrator.Core.config_manager import ConfigManager
    from orchestrator.Core.ditto_manager import DittoCoder
    config = ConfigManager(tmp_path / "cfg")
    config.set("ai_provider", "anthropic")
    ditto = DittoCoder(config_manager=config)
    used = []
    real = DittoCoder.for_run
    monkeypatch.setattr(DittoCoder, "for_run", lambda self, provider=None, model=None: used.append((provider, model))
                        or real(self, provider, model))
    framework = SimpleNamespace(config=config, get_component=lambda name: ditto)

    def require(self):
        self.framework_manager = framework
        return self

    monkeypatch.setattr(cli.FrameworkContext, "require_framework", require)
    monkeypatch.setattr(cli, "_ditto_notice_once", lambda: None)
    probe = tmp_path / "probe.txt"
    probe.write_text("cpu: aarch64\n", encoding="utf-8")
    result = CliRunner().invoke(cli.cli, ["-q", "module", "generate-ai", str(probe), "--provider", "openai",
                                          "--model", "other-model"], input="")
    assert used == [("openai", "other-model")]
    assert result.exit_code == 1   # no key stored for openai in this test: nothing is generated
    assert config.get("ai_provider") == "anthropic" and config.get("ai_model") != "other-model"
    refused = CliRunner().invoke(cli.cli, ["-q", "module", "generate-ai", str(probe), "--provider", "nope"])
    assert refused.exit_code == 2 and "Unknown AI provider 'nope'" in " ".join(refused.output.split())


# --- the board's optimization profile and benchmark rows (DECISIONS round 23 item 10) ---------------------------

def test_targets_window_boards_shows_profiles_and_marks_unmeasured_rows(qapp, tmp_path, monkeypatch):
    from orchestrator.gui.targets_window import TargetsDialog
    framework, _folder = targets_framework(tmp_path)
    boxes = Boxes(monkeypatch)
    dialog = TargetsDialog(framework, select="Rockchip", lang="en")
    assert dialog.btn_boards.objectName() == "btn_targets_boards" and dialog.btn_boards.isEnabled()
    dialog._boards()
    text = " ".join(str(part) for shown in boxes.shown for part in shown)
    assert "rk3566: RK3566" in text and "recommended quantization: Q4_0, IQ4_NL, Q4_K_M" in text
    assert "[unmeasured" in text
    dialog.close()


def test_the_builder_offers_the_board_profile_first_and_its_level(builder):
    from test_review_capabilities_paths import select
    select(builder.target_combo, "Rockchip")
    select(builder.board_combo, "rk3566")
    items = [builder.quant_combo.itemText(i) for i in range(builder.quant_combo.count())]
    assert items[:3] == ["Q4_0", "IQ4_NL", "Q4_K_M"]
    assert builder.quant_combo.currentText() == "Q4_0"          # the board's default stays the default
    assert builder.opt_combo.currentText() == "SIZE"             # the profile's level until the person picks one
    assert not builder.lbl_board_profile.isHidden()
    assert "unmeasured" in builder.lbl_board_profile.text()
    select(builder.board_combo, "rk3588")
    assert builder.opt_combo.currentText() == "BALANCED"
    builder.opt_combo.setCurrentIndex(builder.opt_combo.findText("SPEED"))
    builder._mark_opt_user_set()
    select(builder.board_combo, "rk3566")
    assert builder.opt_combo.currentText() == "SPEED"            # the person's choice is kept


def test_a_board_without_a_profile_goes_back_to_balanced_as_the_cli_does(builder):
    """Integration review: the level a profile set (not the person) does not outlive the profile. Without a profile
    `build start` and start_build use balanced (board_profiles.optimization_for); the Builder must start the same."""
    from orchestrator.Core.board_profiles import optimization_for
    from test_review_capabilities_paths import select
    select(builder.target_combo, "Rockchip")
    select(builder.board_combo, "rk3566")
    assert builder.opt_combo.currentText() == "SIZE"
    select(builder.target_combo, "MyJetson")                     # a target without capabilities or profiles
    assert builder.lbl_board_profile.isHidden()
    assert builder.opt_combo.currentText() == optimization_for(None).upper() == "BALANCED"
    select(builder.target_combo, "Rockchip")
    select(builder.board_combo, "rk3566")
    builder.opt_combo.setCurrentIndex(builder.opt_combo.findText("SPEED"))
    builder._mark_opt_user_set()
    select(builder.target_combo, "MyJetson")
    assert builder.opt_combo.currentText() == "SPEED"            # the person's own pick stays
