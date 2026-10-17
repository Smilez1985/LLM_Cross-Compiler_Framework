"""The seams of wave 5b (GUI parity of the build, Ditto runtime, knowledge and Ditto models areas, merged together):

- one code path for a provider and model chosen for one run or one question: the module wizard and `module
  generate-ai` (DittoCoder.for_run), `ditto ask/eval`, the chat panel and Ditto > Evaluation... share one provider check
  and one settings overlay (orchestrator/Core/ai_override.py);
- one helper finds or makes the shared Ditto menu;
- the menu bar holds every menu of the design once, in its order, with every entry of the four chains once;
- every person-facing command and option of these areas has a mouse path (CLI_PATHS) or is listed as machine-only
  with its reason (the full walk over every area is tests/test_cli_gui_parity.py of the final integrator)."""

from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT

pytest.importorskip("PySide6")

AREAS = ("targets", "module", "build", "config", "ditto runtime", "ditto models", "ditto ask", "ditto eval",
         "knowledge", "zoo")


# --- one code path for a choice for one run ------------------------------------------------------------------------

def test_one_provider_check_and_one_overlay_for_every_choice_for_one_run(tmp_path, monkeypatch):
    from orchestrator.Core import ai_override, ditto_manager
    from orchestrator.Core.config_manager import ConfigManager
    from orchestrator.Core.ditto_manager import DittoCoder
    assert ditto_manager.provider_problem is ai_override.provider_problem
    assert ditto_manager.RunSettings is ai_override.SettingsOverlay
    config = ConfigManager(tmp_path / "cfg")
    config.set("ai_provider", "anthropic")
    ditto = DittoCoder(config_manager=config)
    used = []
    real = ai_override.ditto_with
    monkeypatch.setattr(ai_override, "ditto_with", lambda base, values: used.append((base, values))
                        or real(base, values))
    run = ditto.for_run("ollama", "small-model")
    assert used == [(ditto, {"ai_provider": "ollama", "ai_model": "small-model"})], "for_run goes through ditto_with"
    assert isinstance(run.config, ai_override.SettingsOverlay)
    assert run.client.primary_provider().id == "ollama" and config.get("ai_provider") == "anthropic"
    assert ditto.for_run(None, None) is ditto and len(used) == 1


def test_the_overlay_entries_read_like_the_settings_own(tmp_path):
    """Every reader of config_values (the LLM client, the sign-in overview, the effective settings view) reads an
    overlay entry like a ConfigManager entry: key, value, schema of the setting, is_secret, source type and path."""
    from orchestrator.Core.ai_override import SettingsOverlay
    from orchestrator.Core.config_manager import ConfigManager
    from orchestrator.Core.settings_view import effective_settings
    config = ConfigManager(tmp_path / "cfg")
    config.set("ai_provider", "anthropic")
    overlay = SettingsOverlay(config, {"ai_provider": "ollama"})
    entry = overlay.config_values["ai_provider"]
    assert (entry.key, entry.value, entry.is_secret) == ("ai_provider", "ollama", False)
    assert entry.schema is config.config_values["ai_provider"].schema
    assert (entry.source.source_type, entry.source.source_path) == ("override", None)
    shown = effective_settings(overlay)
    assert shown["ai_provider"] == {"value": "ollama", "source": "override"}
    assert effective_settings(config)["ai_provider"]["value"] == "anthropic", "the saved settings stay unchanged"


def test_for_run_refuses_a_provider_with_the_shared_words():
    """The module wizard's and `module generate-ai`'s DittoCoder.for_run refuse an unknown provider with the words of
    the shared check (the one `ditto ask/eval --provider`, the chat panel and Ditto > Evaluation... use)."""
    from orchestrator.Core import ai_override
    from orchestrator.Core.ditto_manager import DittoCoder, DittoError
    from orchestrator.llm import load_catalog
    words = ai_override.provider_problem(load_catalog(), "no-such-provider")
    ditto = SimpleNamespace(client=SimpleNamespace(catalog=load_catalog()))
    with pytest.raises(DittoError) as caught:
        DittoCoder.for_run(ditto, "no-such-provider")
    assert str(caught.value) == words


# --- the menu bar ----------------------------------------------------------------------------------------------------

def test_one_helper_finds_or_makes_the_ditto_menu():
    from orchestrator.gui.menus import ditto_models, ditto_runtime
    assert ditto_models.ditto_menu is ditto_runtime.ditto_menu
    assert ditto_models.DITTO_MENU == ditto_runtime.DITTO_MENU == "menu_ditto"


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


def _menus(window):
    return [(a.text().replace("&", ""), a.menu()) for a in window.menuBar().actions() if a.menu() is not None]


def _deep_actions(menu):
    """A menu's entries with those of its submenus in place (wave 6: Ditto > Advanced, Tools > Knowledge)."""
    found = []
    for action in menu.actions():
        found.extend(_deep_actions(action.menu()) if action.menu() is not None else [action])
    return found


def test_every_menu_of_the_design_once_in_its_order_with_every_entry_once(window):
    menus = _menus(window)
    titles = [title for title, _ in menus]
    assert len(titles) == len(set(titles)), titles
    design = ["File", "Build", "Ditto", "Security", "View", "Tools", "Help"]   # Boards comes with its own chain
    assert [t for t in titles if t in design] == design, titles
    for title, menu in menus:
        texts = [a.text() for a in menu.actions() if not a.isSeparator()]
        assert len(texts) == len(set(texts)), (title, texts)
    # Wave 6 (Q2, changed on purpose): Exit got its object name, Ditto's runtime, models and evaluation moved under
    # Ditto > Advanced, the knowledge entries under Tools > Knowledge (submenus included below).
    entries = {title: [a.objectName() for a in _deep_actions(menu) if a.objectName()] for title, menu in menus}
    assert entries["File"][-2:] == ["act_file_settings", "act_file_exit"]
    assert "act_file_output_folder" in entries["File"]
    file_texts = [a.text() for a in dict(menus)["File"].actions() if not a.isSeparator()]
    assert file_texts[-2:] == ["Settings...", "Exit"]
    assert entries["Build"] == ["act_build_targets", "act_build_history"]
    assert entries["Ditto"][-3:] == ["act_ditto_runtime_open", "act_ditto_models_open", "act_ditto_eval_open"]
    tools = entries["Tools"]
    assert tools.index("act_knowledge_sources") == tools.index("act_knowledge_snapshots") + 1
    for name in ("act_tools_module_wizard", "act_knowledge_hub", "act_knowledge_index_docs"):
        assert name in tools, name
    every = [name for names in entries.values() for name in names]
    assert len(every) == len(set(every)), every


def test_every_command_of_these_areas_has_a_mouse_path_or_a_reason():
    import click
    from orchestrator import cli
    from orchestrator.gui.menus import build, ditto_models, ditto_runtime, knowledge
    known = set()
    for module in (build, ditto_runtime, ditto_models, knowledge):
        for table in ("CLI_PATHS", "MACHINE_ONLY", "ARGUMENT_PATHS"):
            known.update(getattr(module, table, {}) or {})
    missing = []

    def walk(command, prefix):
        if isinstance(command, click.Group):   # a group only lists its commands
            for name, sub in command.commands.items():
                if not getattr(sub, "hidden", False):
                    walk(sub, f"{prefix} {name}".strip())
            return
        if not any(prefix == area or prefix.startswith(area + " ") for area in AREAS):
            return
        if prefix not in known:
            missing.append(prefix)
        for param in command.params:
            longs = [o for o in getattr(param, "opts", ()) if o.startswith("--")]
            if not isinstance(param, click.Option) or param.hidden or not longs or "--help" in longs:
                continue
            if not any(f"{prefix} {o}" in known for o in longs):
                missing.append(f"{prefix} {longs[0]}")

    walk(cli.cli, "")
    assert missing == []


# --- one runtime task at a time in the whole GUI -------------------------------------------------------------------
# Ditto > Local Runtime... and the AI settings' box run one runtime task at a time (runtime_mode_box.job_begins). A
# download in Ditto > Local Models... (the same model file the box's model offer downloads) and an evaluation of local
# tiers in Ditto > Evaluation... (it stops your server and starts its own) are runtime tasks too.

BUSY = "Another task of Ditto's local runtime is running. Wait until it is done (or cancel it), then try again."


def test_a_local_models_download_waits_for_another_runtime_task_and_counts_as_one(qapp, tmp_path, monkeypatch):
    from test_gui_ditto_models import FakeRuntime, wait_until
    from orchestrator.gui import ditto_models_window as module
    from orchestrator.gui.runtime_mode_box import job_begins, job_ends, job_running
    rt = FakeRuntime(tmp_path)
    shown, answers = [], []
    monkeypatch.setattr(module, "ask", lambda parent, title, text: answers.pop(0) if answers else False)
    monkeypatch.setattr(module, "plain_box", lambda parent, kind, title, text, **k: shown.append((kind, text)))
    win = module.LocalModelsWindow(SimpleNamespace(), runtime_factory=lambda: rt, lang="en")
    other = object()
    try:
        wait_until(qapp, lambda: win.view is not None)
        assert job_begins(other)   # e.g. Ditto > Local Runtime... builds llama-server
        assert win.download_recommended() is False
        assert shown == [("information", BUSY)] and rt.called("download_model") == [] and not win.busy
        job_ends(other)
        rt.block = True
        answers.append(True)
        assert win.download_recommended() is True
        assert job_running(), "the download is a runtime task: Local Runtime and the AI settings' box wait for it"
        win.cancel()
        wait_until(qapp, lambda: not win.busy)
        assert not job_running()
    finally:
        job_ends(other)
        if win.busy:
            win.cancel()
            wait_until(qapp, lambda: not win.busy)
        win.close()
        win.deleteLater()


def test_an_evaluation_of_local_tiers_waits_for_another_runtime_task_and_counts_as_one(qapp, tmp_path, monkeypatch):
    from test_gui_ditto_models import FakeRun, FakeRuntime, tick, wait_until
    from PySide6.QtCore import Qt
    from orchestrator.evals import eval_run
    from orchestrator.gui import ditto_eval_window as module
    from orchestrator.gui.runtime_mode_box import job_begins, job_ends, job_running
    from orchestrator.llm import load_catalog
    rt = FakeRuntime(tmp_path, states={"small": "ready"})
    run = FakeRun()
    monkeypatch.setattr(eval_run, "run_evaluation", run)
    shown, answers = [], []
    monkeypatch.setattr(module, "ask", lambda parent, title, text: answers.pop(0) if answers else False)
    monkeypatch.setattr(module, "plain_box", lambda parent, kind, title, text, **k: shown.append((kind, text)))
    values = {"ai_provider": "anthropic"}
    config = SimpleNamespace(get=lambda key, default=None: values.get(key, default), config_dir=None,
                             set=lambda key, value: values.update({key: value}))
    ditto = SimpleNamespace(config=config, framework=None, client=SimpleNamespace(catalog=load_catalog()),
                            _secrets=None, _registry_override=None)
    win = module.EvaluationWindow(SimpleNamespace(config=config, ditto_manager=ditto), runtime_factory=lambda: rt,
                                  lang="en")
    other = object()
    try:
        win.chk_kinds["healing"].setChecked(True)
        tick(win.lst_tiers, "small")
        assert job_begins(other)
        assert win.start() is False
        assert shown == [("information", BUSY)] and run.calls == [] and not win.busy
        job_ends(other)
        run.block = True
        answers.append(True)
        assert win.start() is True
        assert job_running(), "a tier's server start is a runtime task"
        win.cancel()
        wait_until(qapp, lambda: not win.busy)
        assert not job_running()
        # without a tier the run starts no server: it is no runtime task, and another one does not hold it up
        for i in range(win.lst_tiers.count()):
            win.lst_tiers.item(i).setCheckState(Qt.Unchecked)
        assert job_begins(other)
        run.block = False
        answers.append(True)
        assert win.start() is True
        wait_until(qapp, lambda: not win.busy)
        assert len(run.calls) == 2 and run.calls[-1].kw["tiers"] == ()
        assert job_running(), "the other task still holds the runtime"
    finally:
        job_ends(other)
        if win.busy:
            win.cancel()
            wait_until(qapp, lambda: not win.busy)
        win.close()
        win.deleteLater()
