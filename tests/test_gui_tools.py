"""The GUI tools: Ditto's hands in the window (DECISIONS round 8, "HYBRID CLARIFIED" (2)).

Forms register their fields (the build form, the target module wizard page by page, the AI settings dialog); Ditto
reads what the person entered (secrets only as set or not set, paths as labels), fills fields visibly (marked "filled
by Ditto", one-click undo), steers the wizard page by page and shows tabs and windows. It never overwrites a field the
person is editing or a value the person chose, never fills a secret or a read-only field, and never submits anything:
starting a build, finishing the wizard and saving settings stay the person's clicks. Every call is audited.
"""

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.tools import gui_surface
from orchestrator.tools.gui_surface import GuiField, GuiForm, GuiSurface
from orchestrator.tools.registry import ToolContext, ToolError

pytestmark = pytest.mark.usefixtures("qapp")

FAKE_KEY = "not-a-real-key-0123456789abcdef"


def yes(_spec, _inp):
    return True


def ditto(confirm=yes):
    return ToolContext("ditto", confirm=confirm, session="gui-test")


@pytest.fixture
def surface():
    from orchestrator.gui.form_surface import GuiInvoker
    invoker = GuiInvoker()
    attached = gui_surface.attach(GuiSurface(invoker.call))
    attached.extra["invoker"] = invoker
    yield attached
    gui_surface.detach(attached)


@pytest.fixture
def fm(booted_framework):
    from orchestrator.tools.tools_gui import register_gui_tools
    register_gui_tools(booted_framework.tool_registry, booted_framework)
    return booted_framework


@pytest.fixture
def builder(fm, surface):
    from orchestrator.gui.builder_tab import BuilderTab
    tab = BuilderTab(fm)
    yield tab
    tab.deleteLater()


def call(fm, name, arguments, ctx=None):
    return fm.tool_registry.call(name, arguments, ctx or ditto())


def audited(fm, tool):
    return [e for e in fm.tool_registry.audit.entries(500) if e["tool"] == tool]


# --- who gets the tools, and when ----------------------------------------------------------------------------------

def test_the_gui_tools_exist_only_with_the_gui_and_only_for_ditto(booted_framework, surface):
    from orchestrator.tools.tools_gui import GUI_TOOLS, register_gui_tools
    registry = booted_framework.tool_registry
    assert not GUI_TOOLS & {s.name for s in registry.list()}, "a framework without its window has no GUI tools"
    register_gui_tools(registry, booted_framework)
    register_gui_tools(registry, booted_framework)  # a second window registers nothing twice
    assert GUI_TOOLS <= {s.name for s in registry.list()}
    assert GUI_TOOLS <= {s.name for s in registry.visible("ditto")}
    assert not GUI_TOOLS & {s.name for s in registry.visible("mcp")}, "external apps: later, via the host service"
    with pytest.raises(ToolError) as exc:
        registry.call("read_gui_form", {}, ToolContext("mcp"))
    assert exc.value.code == "not_supported"
    gui_surface.detach(surface)   # the window closed
    assert not GUI_TOOLS & {s.name for s in registry.visible("ditto")}
    with pytest.raises(ToolError) as exc:
        registry.call("read_gui_form", {}, ditto())
    assert exc.value.code == "not_supported"


def test_every_gui_tool_refuses_an_unknown_argument(fm, builder):
    """The registry-wide guard of the fourth round for the GUI tools, which only a framework with its window has."""
    from orchestrator.tools.tools_gui import GUI_TOOLS
    for name in sorted(GUI_TOOLS):
        spec = fm.tool_registry.get(name)
        valid = dict(spec.examples[0]) if spec.examples else {}
        fields = list(spec.input_model.model_fields)
        with pytest.raises(ToolError) as exc:
            call(fm, name, {**valid, (fields[0].upper() if fields else "verbose"): "x"},
                 ToolContext("ditto", confirm=yes, session=f"guard-{name}"))
        assert exc.value.code == "unknown_argument", (name, str(exc.value))


# --- read_gui_form ---------------------------------------------------------------------------------------------------

def test_read_lists_the_open_forms_and_the_build_form(fm, builder):
    out = call(fm, "read_gui_form", {})
    assert [f.form for f in out.open_forms] == ["builder"]
    assert "builder" in out.windows or out.windows == []
    form = call(fm, "read_gui_form", {"form": "builder"}).form
    ids = [f.id for f in form.fields]
    assert ids == ["model", "target", "format", "board", "quantization", "optimization", "use_gpu", "imatrix",
                   "dataset"]
    assert "start" not in ids and "build" not in ids, "starting a build is no field: only the person clicks Start"
    target = next(f for f in form.fields if f.id == "target")
    assert "Rockchip" in target.choices and target.choices_closed


def test_a_secret_is_only_set_or_not_set(fm, surface, tmp_path):
    from orchestrator.gui.dialogs import AIConfigurationDialog
    dialog = AIConfigurationDialog(fm)
    dialog.show()
    try:
        dialog.txt_api_key.setText(FAKE_KEY)
        form = call(fm, "read_gui_form", {"form": "ai_settings"}).form
        key = next(f for f in form.fields if f.id == "api_key")
        assert key.value == "set" and key.read_only and key.kind == "secret"
        assert FAKE_KEY not in form.model_dump_json()
        with pytest.raises(ToolError) as exc:   # never filled either, and never asked about
            call(fm, "fill_gui_form", {"form": "ai_settings", "values": {"api_key": "x"}},
                 ToolContext("ditto", confirm=lambda s, i: pytest.fail("a secret is never asked about")))
        assert dialog.txt_api_key.text() == FAKE_KEY
        assert "secret" in str(exc.value).lower() or exc.value.code
    finally:
        dialog.reject()
    for entry in fm.tool_registry.audit.entries(200):
        assert FAKE_KEY not in str(entry)


def test_a_secret_is_only_set_or_not_set_whatever_its_getter_returns():
    """The form itself hands out only 'set' or 'not set' for a secret field, also when its getter returns the value."""
    token = GuiField("token", "Token", "Token", "secret", lambda: FAKE_KEY, lambda v: None)
    form = GuiForm("f", "F", "F", [token])
    rows = form.snapshot()
    assert rows[0]["value"] is True and rows[0]["read_only"] and FAKE_KEY not in str(rows)
    assert form.fill({"token": "x"}).refused and not form.undo_records and FAKE_KEY not in str(form._values())


def test_paths_reach_the_ai_as_labels(fm, builder, tmp_path):
    home = Path.home()
    private = home / "Private Person" / "calib.txt"
    builder.dataset_combo.setEditText(str(private))
    models = Path(fm.resolve_path(fm.config.get("models_dir", "models")))
    builder.model_input.setEditText(str(models / "tiny.gguf"))
    form = call(fm, "read_gui_form", {"form": "builder"}).form
    values = {f.id: f.value for f in form.fields}
    assert values["dataset"] == "calib.txt"
    assert str(home) not in form.model_dump_json() and "Private Person" not in values["dataset"]
    assert values["model"].endswith("tiny.gguf") and str(models) not in values["model"]
    builder.model_input.setEditText("Qwen/Qwen3.5-0.8B")
    assert {f.id: f.value for f in call(fm, "read_gui_form", {"form": "builder"}).form.fields}["model"] == \
        "Qwen/Qwen3.5-0.8B", "a Hugging Face repo id stays as it is"


# --- fill_gui_form ---------------------------------------------------------------------------------------------------

def test_a_fill_is_visible_marked_and_undone_with_one_click(fm, builder):
    from orchestrator.gui.form_surface import is_marked
    asked = []
    out = call(fm, "fill_gui_form", {"form": "builder", "values": {"model": "Qwen/Qwen3.5-0.8B", "use_gpu": False}},
               ditto(confirm=lambda spec, inp: asked.append(spec.name) or True))
    assert asked == ["fill_gui_form"], "the person is asked by default (a new tool that changes something)"
    assert set(out.filled) == {"model", "use_gpu"} and not out.refused
    assert builder.model_input.currentText() == "Qwen/Qwen3.5-0.8B" and not builder.chk_gpu.isChecked()
    assert is_marked(builder.model_input) and is_marked(builder.chk_gpu)
    assert builder.fill_banner.isVisibleTo(builder)
    assert "Model" in builder.fill_banner.label.text() and "Nothing was started or saved" in \
        builder.fill_banner.label.text()
    builder.fill_banner.btn_undo.click()
    assert builder.model_input.currentText() != "Qwen/Qwen3.5-0.8B" and builder.chk_gpu.isChecked() is True
    assert not is_marked(builder.model_input) and not builder.fill_banner.isVisibleTo(builder)
    undone = audited(fm, "gui_fill_undone")
    assert undone and undone[0]["actor"] == "gui", "the person's undo is in the audit log"


def test_a_value_the_person_chose_is_never_overwritten(fm, builder):
    builder.quant_combo.setCurrentText("Q5_K_M")
    builder.gui_form.person_edited("quantization")
    out = call(fm, "fill_gui_form", {"form": "builder", "values": {"quantization": "Q8_0", "use_gpu": False}})
    assert builder.quant_combo.currentText() == "Q5_K_M"
    assert out.filled == ["use_gpu"]
    assert "person chose" in dict((r.id, r.reason) for r in out.refused)["quantization"]


def test_ditto_may_correct_its_own_fill_but_not_after_the_person_edited_it(fm, builder):
    call(fm, "fill_gui_form", {"form": "builder", "values": {"model": "Qwen/Qwen3.5-0.8B"}})
    assert call(fm, "fill_gui_form", {"form": "builder", "values": {"model": "Qwen/Qwen3.5-2B"}}).filled == ["model"]
    builder.gui_form.person_edited("model")
    with pytest.raises(ToolError) as exc:   # refused before anyone is asked: nothing could be filled
        call(fm, "fill_gui_form", {"form": "builder", "values": {"model": "Qwen/Qwen3.5-4B"}},
             ditto(confirm=lambda s, i: pytest.fail("nothing to ask")))
    assert exc.value.code == "needs_person" and "person chose" in str(exc.value)
    assert builder.model_input.currentText() == "Qwen/Qwen3.5-2B"


def test_a_field_the_person_is_editing_is_never_written(fm, builder):
    builder.gui_form.field("model").busy = lambda: True
    with pytest.raises(ToolError) as exc:
        call(fm, "fill_gui_form", {"form": "builder", "values": {"model": "Qwen/Qwen3.5-0.8B"}})
    assert "editing" in str(exc.value)
    model = builder.gui_form.field("model")
    model.busy = lambda: False

    def person_starts_typing(_spec, _inp):   # while the question is open, the person clicks into the field
        model.busy = lambda: True
        return True
    out = call(fm, "fill_gui_form", {"form": "builder", "values": {"use_gpu": False, "model": "Qwen/Qwen3.5-0.8B"}},
               ditto(confirm=person_starts_typing))
    assert out.filled == ["use_gpu"] and "editing" in dict((r.id, r.reason) for r in out.refused)["model"], \
        "checked again when filling"


def test_focus_counts_as_editing(fm, builder, qapp):
    from orchestrator.gui.form_surface import _has_focus
    builder.show()
    builder.activateWindow()
    builder.model_input.setFocus()
    qapp.processEvents()
    if not builder.model_input.hasFocus() and not builder.model_input.lineEdit().hasFocus():
        pytest.skip("this platform gives no keyboard focus offscreen")
    assert _has_focus(builder.model_input) and builder.gui_form.field("model").busy()


def test_choices_and_read_only_fields_are_checked(fm, builder):
    out = call(fm, "fill_gui_form", {"form": "builder", "values": {"target": "NoSuchTarget", "format": "gguf"}})
    reasons = dict((r.id, r.reason) for r in out.refused)
    assert "not one of the values" in reasons["target"]
    builder.target_combo.setCurrentIndex(builder.target_combo.findData("Rockchip"))
    out = call(fm, "fill_gui_form", {"form": "builder", "values": {"format": "rknn", "dataset": "datasets:x.txt"}})
    assert "format" in out.filled or "format" in out.unchanged


def test_the_ai_settings_never_widen_what_an_ai_may_do(fm, surface):
    from orchestrator.gui.dialogs import AIConfigurationDialog
    dialog = AIConfigurationDialog(fm)
    dialog.show()
    try:
        dialog.chk_offline.setChecked(True)
        dialog.chk_enable_rag.setChecked(False)
        out = call(fm, "fill_gui_form", {"form": "ai_settings", "values": {
            "offline": False, "enable_rag": True, "provider": "openai", "fallback": "anthropic"}})
        reasons = dict((r.id, r.reason) for r in out.refused)
        assert set(reasons) == {"offline", "enable_rag", "provider", "fallback"} and out.filled == []
        assert dialog.chk_offline.isChecked() and not dialog.chk_enable_rag.isChecked()
        dialog.chk_offline.setChecked(False)
        out = call(fm, "fill_gui_form", {"form": "ai_settings", "values": {"offline": True}})
        assert out.filled == ["offline"], "tightening is fine; Save stays the person's click"
        assert dialog.isVisible() and dialog.result() == 0
    finally:
        dialog.reject()
    assert gui_surface.current().form("ai_settings") is None, "a closed dialog leaves the surface"


def test_nothing_is_submitted(fm, builder, monkeypatch):
    started = []
    monkeypatch.setattr(builder, "_submit_build", lambda: started.append(1))
    call(fm, "fill_gui_form", {"form": "builder", "values": {"model": "Qwen/Qwen3.5-0.8B", "imatrix": True}})
    assert started == [] and builder._active_request is None
    with pytest.raises(ToolError) as exc:
        call(fm, "fill_gui_form", {"form": "builder", "values": {"start": True}})
    assert exc.value.code == "invalid_value" and "no field start" in str(exc.value)


def test_a_fill_that_would_change_a_persons_choice_is_rolled_back():
    """The form's own logic (a target change resets the board) never takes a value the person chose."""
    state = {"target": "a", "board": "x"}

    def set_target(value):
        state["target"] = value
        state["board"] = "y"   # like the builder: another target offers other boards

    target = GuiField("target", "Target", "Target", "choice", lambda: state["target"], set_target)
    board = GuiField("board", "Board", "Board", "choice", lambda: state["board"],
                     lambda v: state.__setitem__("board", v))
    form = GuiForm("f", "F", "F", [target, board])
    form.person_edited("board")
    out = form.fill({"target": "b"})
    assert state == {"target": "a", "board": "x"} and out.filled == []
    assert "Board" in out.refused[0][1] and not form.undo_records


def test_the_undo_keeps_what_the_person_changed_since():
    state = {"a": "1", "b": "1"}
    fields = [GuiField(k, k, k, "text", (lambda k=k: state[k]), (lambda v, k=k: state.__setitem__(k, v)))
              for k in ("a", "b")]
    form = GuiForm("f", "F", "F", fields)
    form.fill({"a": "2", "b": "2"})
    state["b"] = "mine"
    form.person_edited("b")
    assert form.undo_all() == ["a"] and state == {"a": "1", "b": "mine"}


# --- the wizard ------------------------------------------------------------------------------------------------------

@pytest.fixture
def wizard(fm, surface, tmp_path, monkeypatch):
    from orchestrator.gui import wizards
    monkeypatch.setattr(wizards.QMessageBox, "information", lambda *a, **k: 0)
    w = wizards.ModuleCreationWizard(tmp_path / "targets")
    w.framework_manager = fm
    w.show()
    yield w
    w.reject()
    w.deleteLater()


def test_the_wizard_page_by_page_and_never_finished_by_an_ai(fm, wizard, monkeypatch):
    accepted = []
    monkeypatch.setattr(type(wizard), "accept", lambda self: accepted.append(1))
    state = call(fm, "wizard_state", {})
    assert state.open and state.form.page.number == 1 and state.form.fields == []
    out = call(fm, "wizard_step", {"action": "next"})
    assert out.moved and out.page.number == 2
    out = call(fm, "wizard_step", {"action": "next", "values": {"name": "Radxa_ROCK_5B", "packages": "cmake"}})
    reasons = dict((r.id, r.reason) for r in out.refused)
    assert out.filled == ["name"] and "current page" in reasons["packages"] and out.page.number == 3
    assert wizard.name_edit.text() == "Radxa_ROCK_5B"
    for _ in range(5):
        out = call(fm, "wizard_step", {"action": "next"})
    assert out.page.last and "Only the person clicks Finish" in out.note
    assert accepted == [] and wizard.isVisible()


def test_a_module_ditto_filled_is_ai_made(fm, wizard, monkeypatch):
    from orchestrator.gui import wizards
    saved = []
    monkeypatch.setattr(wizards.ModuleGenerator, "generate_module",
                        lambda self, data, *a, **k: saved.append(dict(data)) or Path(self.targets_dir) / "X")
    monkeypatch.setattr("orchestrator.tools.tools_targets.record_module_saved", lambda *a, **k: None)
    call(fm, "wizard_step", {"action": "next"})
    call(fm, "wizard_step", {"action": "fill", "values": {"name": "Board_X"}})
    wizard.accept()
    assert saved and saved[0]["provenance"] == "ai-generated", "the first build is shown to the person once"
    assert "untested" in saved[0]["description"]


def test_wizard_sharing_choices_stay_the_persons(fm, wizard):
    for _ in range(4):
        call(fm, "wizard_step", {"action": "next"})
    with pytest.raises(ToolError) as exc:
        call(fm, "wizard_step", {"action": "fill", "values": {"export": True}})
    assert "only the person decides" in str(exc.value) and not wizard.chk_export.isChecked()


def test_wizard_step_opens_deep_ingest_for_the_person(fm, wizard, monkeypatch):
    opened = []
    wizard.gui_form.actions["deep_ingest"] = lambda: opened.append(1)
    out = call(fm, "wizard_step", {"action": "deep_ingest"})
    assert opened == [1] and "starts the crawl" in out.note
    with pytest.raises(ToolError) as exc:
        call(fm, "wizard_step", {"action": "deep_ingest", "values": {"name": "x"}})
    assert exc.value.code == "invalid_value" and opened == [1]


# --- show_gui and the main window ------------------------------------------------------------------------------------

class FakeWindow:
    """The parts of the main window open_gui uses."""

    def __init__(self, fm, builder):
        from PySide6.QtWidgets import QMainWindow, QTabWidget, QWidget
        self.qt = QMainWindow()
        self.tabs = QTabWidget(self.qt)
        self.builder_tab = builder
        self.deployment_window = QWidget()
        self.tabs.addTab(QWidget(), "other")
        self.tabs.addTab(builder, "builder")
        self.tabs.addTab(self.deployment_window, "deploy")
        self.framework = fm
        self.calls = []
        self.chat_window = SimpleNamespace(txt_input=SimpleNamespace(setText=lambda t: self.calls.append(("text", t))),
                                           focus_composer=lambda: self.calls.append(("focus",)))

    def raise_(self):
        pass

    def _show_chat(self):
        self.calls.append(("chat",))

    def _open_module_wizard(self, *_a, probe_path=None):
        self.calls.append(("wizard", probe_path))

    def _open_ai_config(self):
        self.calls.append(("ai",))


def test_show_gui_opens_tabs_and_windows(fm, builder, qapp):
    from PySide6.QtCore import QObject
    from orchestrator.gui import form_surface
    window = FakeWindow(fm, builder)
    surface = form_surface.open_gui(window)
    try:
        assert surface.form("builder") is builder.gui_form
        out = call(fm, "show_gui", {"window": "builder"})
        assert out.shown and window.tabs.currentWidget() is builder
        call(fm, "show_gui", {"window": "module_wizard"})
        call(fm, "show_gui", {"window": "chat"})
        deadline = time.monotonic() + 2
        while ("wizard", None) not in window.calls and time.monotonic() < deadline:
            qapp.processEvents()
        assert ("wizard", None) in window.calls and ("chat",) in window.calls
        assert isinstance(surface.extra["invoker"], QObject)
    finally:
        form_surface.close_gui(surface)
    assert gui_surface.current() is None


def test_the_no_target_offer_reaches_the_wizard_and_the_chat(fm, builder):
    from orchestrator.gui import form_surface
    window = FakeWindow(fm, builder)
    surface = form_surface.open_gui(window)
    try:
        builder._last_probe_path = "/probes/board.txt"
        builder.board_advice.show_no_target(SimpleNamespace(get=lambda k: {"SOC_MODEL": "rk9999"}.get(k)))
        assert builder.board_advice.isVisibleTo(builder)
        wizard_button, ditto_button = builder.board_advice.buttons
        wizard_button.click()
        ditto_button.click()
        assert ("wizard", "/probes/board.txt") in window.calls
        text = next(c[1] for c in window.calls if c[0] == "text")
        assert "rk9999" in text and ("chat",) in window.calls and ("focus",) in window.calls, \
            "the question is written into the chat; the person sends it"
    finally:
        form_surface.close_gui(surface)


def test_every_gui_tool_call_is_audited(fm, builder):
    call(fm, "read_gui_form", {"form": "builder"})
    call(fm, "fill_gui_form", {"form": "builder", "values": {"use_gpu": False}})
    for tool in ("read_gui_form", "fill_gui_form"):
        rows = audited(fm, tool)
        assert rows and all(r["actor"] == "ditto" for r in rows), tool
    assert any(r["outcome"] == "started" for r in audited(fm, "fill_gui_form")), "write-ahead entry before the fill"


# --- the invoker -----------------------------------------------------------------------------------------------------

def test_the_invoker_runs_on_the_gui_thread_and_never_late(qapp):
    from orchestrator.gui.form_surface import GuiInvoker
    invoker = GuiInvoker()
    seen = {}

    def worker():
        seen["value"] = invoker.call(lambda: threading.current_thread() is threading.main_thread(), timeout=5)
        try:
            invoker.call(lambda: seen.setdefault("late", True), timeout=0.05)
        except TimeoutError:
            seen["timeout"] = True

    thread = threading.Thread(target=worker)
    thread.start()
    deadline = time.monotonic() + 5
    while "value" not in seen and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    thread.join(5)   # the GUI thread is busy (not processing events) while the second call times out
    qapp.processEvents()
    assert seen["value"] is True and seen.get("timeout") and "late" not in seen, \
        "a call that timed out before it started never runs"


# --- texts -----------------------------------------------------------------------------------------------------------

def test_every_gui_text_has_both_languages_with_the_same_placeholders():
    import string
    from orchestrator.utils.l10n_gui import TEXTS
    assert set(TEXTS["en"]) == set(TEXTS["de"])

    def names(text):
        return {n for _, n, _, _ in string.Formatter().parse(text) if n}
    for key, text in TEXTS["en"].items():
        assert names(text) == names(TEXTS["de"][key]), key
