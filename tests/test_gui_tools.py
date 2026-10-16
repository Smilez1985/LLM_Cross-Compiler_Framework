"""The GUI tool: the AI's hands in the window (DECISIONS round 8, "HYBRID CLARIFIED" (2); W4 of 2026-10-16).

Forms register their fields (the build form, the target module wizard page by page, the AI settings dialog); the one
tool `gui` reads what the person entered (secrets only as set or not set, paths as labels), fills fields visibly
(marked with who filled them, one-click undo), steers the wizard page by page and shows tabs and windows. It never
overwrites a field the person is editing or a value the person chose, never fills a secret or a read-only field, and
never submits anything: starting a build, finishing the wizard and saving settings stay the person's clicks. Every
call is audited. Ditto and every MCP caller use the same tool with the same rules; an AI app's fields are marked with
its name (its MCP token, or the name it gives itself as its own claim), never in Ditto's look. The bridge that brings
an AI app's call from its own process into the window is tested in tests/test_gui_bridge.py.
"""

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.tools import gui_surface
from orchestrator.tools.gui_surface import DITTO, Filler, GuiField, GuiForm, GuiSurface
from orchestrator.tools.registry import ToolContext, ToolError

pytestmark = pytest.mark.usefixtures("qapp")

FAKE_KEY = "not-a-real-key-0123456789abcdef"


def yes(_spec, _inp):
    return True


def ditto(confirm=yes):
    return ToolContext("ditto", confirm=confirm, session="gui-test")


def app(name="Claude Code", actor="mcp", confirm=yes):
    """An MCP client's call as its server process makes it (the client's own name rides along as a label)."""
    return ToolContext(actor, confirm=confirm, session=f"gui-test-{actor}",
                       metadata={"client": SimpleNamespace(name=name)})


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
    return booted_framework


@pytest.fixture
def builder(fm, surface):
    from orchestrator.gui.builder_tab import BuilderTab
    tab = BuilderTab(fm)
    yield tab
    tab.deleteLater()


def with_token(fm, name="laptop"):
    """A named MCP token the person gave its own permissions (the recommended ones: reading allowed, changes asked)."""
    from orchestrator.tools.policy import ToolPolicy
    registry = fm.tool_registry
    registry.tokens.add(name)
    registry.tokens.set_policy(name, ToolPolicy(configured=True, reviewed=[s.name for s in registry.list()]))
    return f"mcp:{name}"


def call(fm, action, ctx=None, **arguments):
    return fm.tool_registry.call("gui", {"action": action, **arguments}, ctx or ditto())


def audited(fm, tool):
    return [e for e in fm.tool_registry.audit.entries(500) if e["tool"] == tool]


# --- who gets the tool, and when -------------------------------------------------------------------------------------

def test_one_gui_tool_for_ditto_and_every_mcp_caller(booted_framework):
    """W4: one tool with an action field, registered with the framework's tools in every process and offered to Ditto,
    the local stdio clients and named tokens alike; the five tool names of wave 3 are gone."""
    from orchestrator.tools.tools_gui import ACTIONS, GUI_TOOL_CALLERS, register_gui_tools
    registry = booted_framework.tool_registry
    names = {s.name for s in registry.list()}
    assert "gui" in names and not {"read_gui_form", "fill_gui_form", "wizard_state", "wizard_step", "show_gui"} & names
    register_gui_tools(registry, booted_framework)   # the window opening registers nothing twice
    assert [s.name for s in registry.list()].count("gui") == 1
    for caller in ("ditto", "mcp"):
        assert "gui" in {s.name for s in registry.visible(caller)}, caller
    assert GUI_TOOL_CALLERS == ("ditto", "mcp", "mcp:*")
    assert set(ACTIONS) == {"read_form", "fill_form", "wizard_state", "wizard_open", "wizard_fill", "wizard_next",
                            "wizard_back", "wizard_docs", "show_window"}


@pytest.mark.parametrize("caller", ["ditto", "mcp"])
def test_without_a_window_the_tool_says_so_plainly(booted_framework, caller):
    """No window in this process and none reachable over the bridge: the blocker gui_not_open with the person's action,
    for a read and for a fill (the fill is refused before anyone is asked)."""
    ctx = ToolContext(caller, confirm=lambda s, i: pytest.fail("nothing is asked while no window is open"))
    for action, arguments in (("read_form", {}), ("fill_form", {"form": "builder", "values": {"use_gpu": False}})):
        with pytest.raises(ToolError) as exc:
            call(booted_framework, action, ctx, **arguments)
        assert exc.value.blocker == "gui_not_open" and exc.value.code == "needs_person", action
        assert exc.value.person_action == "Open the framework window."
        assert "framework window" in exc.value.next


def test_the_gui_tool_refuses_an_unknown_argument(fm, builder):
    """The registry-wide guard of the fourth round, with every example call of the tool."""
    spec = fm.tool_registry.get("gui")
    for i, example in enumerate(spec.examples):
        with pytest.raises(ToolError) as exc:
            call(fm, example["action"], ToolContext("ditto", confirm=yes, session=f"guard-{i}"),
                 **{k: v for k, v in example.items() if k != "action"}, ACTION="x")
        assert exc.value.code == "unknown_argument", (example, str(exc.value))


@pytest.mark.parametrize("arguments, field", [
    ({"action": "show_window"}, "window"),
    ({"action": "fill_form", "values": {"use_gpu": False}}, "form"),
    ({"action": "fill_form", "form": "builder"}, "values"),
    ({"action": "wizard_fill"}, "values"),
    ({"action": "read_form", "values": {"use_gpu": False}}, "values"),
    ({"action": "wizard_open", "values": {"name": "x"}}, "values"),
    ({"action": "wizard_state", "form": "builder"}, "form"),
    ({"action": "read_form", "window": "builder"}, "window"),
])
def test_each_action_takes_only_its_own_fields(fm, builder, arguments, field):
    with pytest.raises(ToolError) as exc:
        fm.tool_registry.call("gui", arguments, ToolContext("ditto", confirm=lambda s, i: pytest.fail("not asked")))
    assert exc.value.code in ("invalid_value", "missing_argument") and exc.value.field == field


# --- read_form -------------------------------------------------------------------------------------------------------

def test_read_lists_the_open_forms_and_the_build_form(fm, builder):
    out = call(fm, "read_form")
    assert [f.form for f in out.open_forms] == ["builder"]
    assert "builder" in out.windows or out.windows == []
    form = call(fm, "read_form", form="builder").form
    ids = [f.id for f in form.fields]
    assert ids == ["model", "target", "format", "board", "quantization", "optimization", "use_gpu", "imatrix",
                   "dataset"]
    assert "start" not in ids and "build" not in ids, "starting a build is no field: only the person clicks Start"
    target = next(f for f in form.fields if f.id == "target")
    assert "Rockchip" in target.choices and target.choices_closed


def test_reading_is_decided_like_a_read_tool_and_filling_asks(fm, builder):
    """One tool, two kinds of calls: reading follows the caller's read tier (allowed without a question by default),
    a fill follows the tool's own tier (asked by default, like every new tool that changes something)."""
    asked = []
    ctx = ToolContext("mcp", confirm=lambda spec, inp: asked.append(inp.action) or True,
                      metadata={"client": SimpleNamespace(name="Claude Code")})
    call(fm, "read_form", ctx, form="builder")
    call(fm, "wizard_state", ctx)
    gui_surface.current().register_window("builder", "Builder tab", "Reiter Builder", lambda: None)
    call(fm, "show_window", ctx, window="builder")
    assert asked == [], "reading the window asks nobody"
    call(fm, "fill_form", ctx, form="builder", values={"use_gpu": False})
    assert asked == ["fill_form"]
    rows = {e["outcome"] for e in audited(fm, "gui") if e["actor"] == "mcp"}
    assert "started" in rows, "a fill gets its write-ahead entry; a read needs none"


def test_the_persons_setting_for_the_tool_covers_every_action(fm, builder):
    """A person who denies the GUI tool for a caller denies its reads too (their setting for this tool)."""
    from orchestrator.tools.policy import Decision, ToolPolicy
    registry = fm.tool_registry
    registry.policy_store.save(ToolPolicy(configured=True, reviewed=[s.name for s in registry.list()],
                                          overrides={"gui": Decision.DENY}))
    with pytest.raises(ToolError) as exc:
        call(fm, "read_form", ToolContext("mcp"))
    assert exc.value.code == "permission_denied"


def test_a_new_token_may_not_use_the_window_until_the_person_sets_its_permissions(fm, builder):
    """A new token may only read with read tools: the window, which can also be filled, stays closed to it."""
    fm.tool_registry.tokens.add("fresh")
    assert "gui" not in {s.name for s in fm.tool_registry.visible("mcp:fresh")}
    with pytest.raises(ToolError) as exc:
        call(fm, "read_form", app(actor="mcp:fresh"))
    assert exc.value.code == "permission_denied"


def test_reading_counts_with_the_read_tools_and_fills_have_their_own_rate(fm, builder):
    from orchestrator.tools import spec as spec_module
    from orchestrator.tools.tools_gui import FILLS_PER_MINUTE
    registry = fm.tool_registry
    assert registry.get("gui").limits.rate == spec_module.Rate(FILLS_PER_MINUTE, 60.0)
    for _ in range(FILLS_PER_MINUTE + 2):   # more reads than fills may happen in a minute: the read rate counts them
        call(fm, "read_form", form="builder")


def test_only_a_call_that_surely_reads_is_decided_like_a_read_tool():
    """ToolSpec.reads: a reading call of a tool that can also change things follows the caller's read tier; a rule that
    fails, a call without input (the permission rows) and a new token keep the tool's own tier."""
    from orchestrator.tools.policy import Decision, ToolPolicy, ToolTier, call_only_reads

    def spec(rule):
        return SimpleNamespace(name="mixed", tier=ToolTier.LOCAL_WRITE, reads=rule, default_decision=None,
                               max_decision=None)
    policy = ToolPolicy(configured=True, reviewed=["mixed"])
    broken, reading = spec(lambda inp: 1 / 0), spec(lambda inp: True)
    assert call_only_reads(broken, object()) is False and call_only_reads(reading, None) is False
    assert policy.decide_tool(broken, object(), "mcp") == Decision.ASK
    assert policy.decide_tool(reading, object(), "mcp") == Decision.ALLOW
    assert policy.decide_tool(reading, None, "mcp") == Decision.ASK
    assert policy.decide_tool(reading, object(), "mcp:new") == Decision.DENY


def test_a_secret_is_only_set_or_not_set(fm, surface, tmp_path):
    from orchestrator.gui.dialogs import AIConfigurationDialog
    dialog = AIConfigurationDialog(fm)
    dialog.show()
    try:
        dialog.txt_api_key.setText(FAKE_KEY)
        for ctx in (ditto(), app()):
            form = call(fm, "read_form", ctx, form="ai_settings").form
            key = next(f for f in form.fields if f.id == "api_key")
            assert key.value == "set" and key.read_only and key.kind == "secret"
            assert FAKE_KEY not in form.model_dump_json()
            with pytest.raises(ToolError) as exc:   # never filled either, and never asked about
                call(fm, "fill_form", ToolContext(ctx.actor, metadata=ctx.metadata,
                                                  confirm=lambda s, i: pytest.fail("a secret is never asked about")),
                     form="ai_settings", values={"api_key": "x"})
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
    assert form.fill({"token": "x"}, by=Filler("mcp", "x")).refused and not form.undo_records
    assert FAKE_KEY not in str(form._values())


def test_paths_reach_the_ai_as_labels(fm, builder, tmp_path):
    home = Path.home()
    private = home / "Private Person" / "calib.txt"
    builder.dataset_combo.setEditText(str(private))
    models = Path(fm.resolve_path(fm.config.get("models_dir", "models")))
    builder.model_input.setEditText(str(models / "tiny.gguf"))
    form = call(fm, "read_form", app(), form="builder").form
    values = {f.id: f.value for f in form.fields}
    assert values["dataset"] == "calib.txt"
    assert str(home) not in form.model_dump_json() and "Private Person" not in values["dataset"]
    assert values["model"].endswith("tiny.gguf") and str(models) not in values["model"]
    builder.model_input.setEditText("Qwen/Qwen3.5-0.8B")
    assert {f.id: f.value for f in call(fm, "read_form", form="builder").form.fields}["model"] == \
        "Qwen/Qwen3.5-0.8B", "a Hugging Face repo id stays as it is"


def test_a_path_in_the_build_folder_reaches_the_ai_by_its_label(fm, builder):
    """The framework's build folder (eighth round, answer 3) is 'builds' in every other view an AI reads (Mojo, the
    runtime tools, get_status): a model the person picked from a build waiting there is named by that label too, not by
    its file name alone."""
    from orchestrator.Core.artifact_output import build_folder
    model = build_folder(fm) / "Rockchip" / "m" / "Q4_K_M" / "m.gguf"
    builder.model_input.setEditText(str(model))
    values = {f.id: f.value for f in call(fm, "read_form", form="builder").form.fields}
    assert values["model"] == "builds:Rockchip/m/Q4_K_M/m.gguf"


# --- fill_form -------------------------------------------------------------------------------------------------------

def test_a_fill_is_visible_marked_and_undone_with_one_click(fm, builder):
    from orchestrator.gui.form_surface import is_marked
    asked = []
    out = call(fm, "fill_form", ditto(confirm=lambda spec, inp: asked.append(spec.name) or True), form="builder",
               values={"model": "Qwen/Qwen3.5-0.8B", "use_gpu": False})
    assert asked == ["gui"], "the person is asked by default (a new tool that changes something)"
    assert set(out.filled) == {"model", "use_gpu"} and not out.refused and "filled by Ditto" in out.marked
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
    for ctx in (ditto(), app()):
        out = call(fm, "fill_form", ctx, form="builder", values={"quantization": "Q8_0", "use_gpu": False})
        assert builder.quant_combo.currentText() == "Q5_K_M"
        assert out.filled in (["use_gpu"], []) and "use_gpu" in out.filled + out.unchanged
        assert "person chose" in dict((r.id, r.reason) for r in out.refused)["quantization"]


def test_ditto_may_correct_its_own_fill_but_not_after_the_person_edited_it(fm, builder):
    call(fm, "fill_form", form="builder", values={"model": "Qwen/Qwen3.5-0.8B"})
    assert call(fm, "fill_form", form="builder", values={"model": "Qwen/Qwen3.5-2B"}).filled == ["model"]
    builder.gui_form.person_edited("model")
    with pytest.raises(ToolError) as exc:   # refused before anyone is asked: nothing could be filled
        call(fm, "fill_form", ditto(confirm=lambda s, i: pytest.fail("nothing to ask")), form="builder",
             values={"model": "Qwen/Qwen3.5-4B"})
    assert exc.value.code == "needs_person" and "person chose" in str(exc.value)
    assert builder.model_input.currentText() == "Qwen/Qwen3.5-2B"


def test_a_field_the_person_is_editing_is_never_written(fm, builder):
    builder.gui_form.field("model").busy = lambda: True
    with pytest.raises(ToolError) as exc:
        call(fm, "fill_form", app(), form="builder", values={"model": "Qwen/Qwen3.5-0.8B"})
    assert "editing" in str(exc.value)
    model = builder.gui_form.field("model")
    model.busy = lambda: False

    def person_starts_typing(_spec, _inp):   # while the question is open, the person clicks into the field
        model.busy = lambda: True
        return True
    out = call(fm, "fill_form", ditto(confirm=person_starts_typing), form="builder",
               values={"use_gpu": False, "model": "Qwen/Qwen3.5-0.8B"})
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
    out = call(fm, "fill_form", form="builder", values={"target": "NoSuchTarget", "format": "gguf"})
    reasons = dict((r.id, r.reason) for r in out.refused)
    assert "not one of the values" in reasons["target"]
    builder.target_combo.setCurrentIndex(builder.target_combo.findData("Rockchip"))
    out = call(fm, "fill_form", form="builder", values={"format": "rknn", "dataset": "datasets:x.txt"})
    assert "format" in out.filled or "format" in out.unchanged


def test_the_ai_settings_never_widen_what_an_ai_may_do(fm, surface):
    from orchestrator.gui.dialogs import AIConfigurationDialog
    dialog = AIConfigurationDialog(fm)
    dialog.show()
    try:
        dialog.chk_offline.setChecked(True)
        dialog.chk_enable_rag.setChecked(False)
        out = call(fm, "fill_form", app(), form="ai_settings", values={
            "offline": False, "enable_rag": True, "provider": "openai", "fallback": "anthropic"})
        reasons = dict((r.id, r.reason) for r in out.refused)
        assert set(reasons) == {"offline", "enable_rag", "provider", "fallback"} and out.filled == []
        assert dialog.chk_offline.isChecked() and not dialog.chk_enable_rag.isChecked()
        dialog.chk_offline.setChecked(False)
        out = call(fm, "fill_form", form="ai_settings", values={"offline": True})
        assert out.filled == ["offline"], "tightening is fine; Save stays the person's click"
        assert dialog.isVisible() and dialog.result() == 0
    finally:
        dialog.reject()
    assert gui_surface.current().form("ai_settings") is None, "a closed dialog leaves the surface"


def test_nothing_is_submitted(fm, builder, monkeypatch):
    started = []
    monkeypatch.setattr(builder, "_submit_build", lambda: started.append(1))
    call(fm, "fill_form", app(), form="builder", values={"model": "Qwen/Qwen3.5-0.8B", "imatrix": True})
    assert started == [] and builder._active_request is None
    with pytest.raises(ToolError) as exc:
        call(fm, "fill_form", form="builder", values={"start": True})
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
    out = form.fill({"target": "b"}, by=Filler("mcp:laptop", "Claude Code"))
    assert state == {"target": "a", "board": "x"} and out.filled == []
    assert "Board" in out.refused[0][1] and not form.undo_records


def test_the_undo_keeps_what_the_person_changed_since():
    state = {"a": "1", "b": "1", "c": "1"}
    fields = [GuiField(k, k, k, "text", (lambda k=k: state[k]), (lambda v, k=k: state.__setitem__(k, v)))
              for k in ("a", "b", "c")]
    form = GuiForm("f", "F", "F", fields)
    form.fill({"a": "2", "b": "2"})
    form.fill({"c": "3"}, by=Filler("mcp", "Cursor"))   # the undo takes back every AI's fills
    state["b"] = "mine"
    form.person_edited("b")
    assert sorted(form.undo_all()) == ["a", "c"] and state == {"a": "1", "b": "mine", "c": "1"}
    assert not form.marked()


# --- who filled: Ditto or an AI app ------------------------------------------------------------------------------

def test_an_ai_apps_fill_names_the_app_and_never_looks_like_dittos(fm, builder):
    """W4 (2): the same rules for every caller; the marks name the calling app (the trusted token name, or the name the
    client gives itself, quoted as its claim), in another colour than Ditto's, also when the app calls itself Ditto."""
    from orchestrator.gui.form_surface import APP_MARK_COLOUR, MARK_COLOUR, is_marked, marked_by
    out = call(fm, "fill_form", app(name="Ditto"), form="builder", values={"use_gpu": False})
    assert out.filled == ["use_gpu"] and "your app" in out.marked and "Ditto" not in out.marked
    assert builder.gui_form.field("use_gpu").ai_by == Filler("mcp", "Ditto")
    assert is_marked(builder.chk_gpu) and marked_by(builder.chk_gpu) == "mcp"
    assert APP_MARK_COLOUR in builder.chk_gpu.styleSheet() and MARK_COLOUR not in builder.chk_gpu.styleSheet()
    text = builder.fill_banner.label.text()
    assert 'the AI app "Ditto" (its own name, not checked)' in text and "Ditto filled" not in text
    assert builder.fill_banner.btn_undo.text().replace("&", "") == "Undo the AI changes"
    assert "own name, not checked" in builder.chk_gpu.accessibleDescription()

    call(fm, "fill_form", app(name="Claude Code", actor=with_token(fm)), form="builder",
         values={"model": "Qwen/Qwen3.5-0.8B"})
    text = builder.fill_banner.label.text()
    assert 'the AI app with the MCP token "laptop" (it calls itself "Claude Code"): Model' in text
    call(fm, "fill_form", form="builder", values={"imatrix": True})
    text = builder.fill_banner.label.text()
    assert "Filled by Ditto: Importance matrix" in text and MARK_COLOUR in builder.chk_imatrix.styleSheet()

    builder.fill_banner.btn_undo.click()
    assert not builder.gui_form.marked() and builder.chk_gpu.isChecked()
    undone = audited(fm, "gui_fill_undone")[0]
    import json
    assert set(json.loads(undone["args_preview"])["filled_by"]) == {"mcp", "mcp:laptop", "ditto"}


def test_who_filled_reaches_the_ai_in_closed_words(fm, builder):
    """An AI reads who filled a field relative to itself ('you', 'ditto', 'another_app'), never another app's
    self-reported name."""
    call(fm, "fill_form", app(name="Cursor  \u202e evil"), form="builder", values={"use_gpu": False})
    call(fm, "fill_form", form="builder", values={"imatrix": True})
    by_app = {f.id: f.filled_by for f in call(fm, "read_form", app(name="Cursor  \u202e evil"),
                                              form="builder").form.fields}
    by_other = {f.id: f.filled_by for f in call(fm, "read_form", app(name="Claude Code"), form="builder").form.fields}
    by_ditto = {f.id: f.filled_by for f in call(fm, "read_form", form="builder").form.fields}
    assert (by_app["use_gpu"], by_app["imatrix"]) == ("you", "ditto")
    assert (by_other["use_gpu"], by_other["imatrix"]) == ("another_app", "ditto")
    assert (by_ditto["use_gpu"], by_ditto["imatrix"], by_ditto["model"]) == ("another_app", "you", "")
    summary = call(fm, "read_form", app()).open_forms[0]
    assert summary.filled_by_ai == 2


def test_an_apps_name_is_plain_and_short_for_the_person():
    """The name a client gives itself is its claim: invisible and control characters go, quotes cannot close the
    quotes around it, it is cut short; an empty or placeholder name is said in words."""
    who = Filler("mcp", 'Evil"\u202e\n' + "x" * 200)
    assert '"' not in who.app and "\u202e" not in who.app and "\n" not in who.app and len(who.app) <= 64
    assert Filler("mcp", "unnamed client").describe("en") == "an AI app that gave no name"
    assert Filler("ditto", "anything").app == "" and DITTO.is_ditto
    for bad in ("gui", "cli", "mcp:", "mcp:Bad Name", "Ditto"):
        with pytest.raises(ValueError):
            Filler(bad, "x")


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
    state = call(fm, "wizard_state")
    assert state.open and state.form.page.number == 1 and state.form.fields == []
    out = call(fm, "wizard_next")
    assert out.moved and out.page.number == 2
    out = call(fm, "wizard_next", values={"name": "Radxa_ROCK_5B", "packages": "cmake"})
    reasons = dict((r.id, r.reason) for r in out.refused)
    assert out.filled == ["name"] and "current page" in reasons["packages"] and out.page.number == 3
    assert wizard.name_edit.text() == "Radxa_ROCK_5B"
    for _ in range(5):
        out = call(fm, "wizard_next", app())
    assert out.page.last and "Only the person clicks Finish" in out.note
    assert accepted == [] and wizard.isVisible()


def test_a_module_an_ai_filled_is_ai_made(fm, wizard, monkeypatch):
    from orchestrator.gui import wizards
    saved = []
    monkeypatch.setattr(wizards.ModuleGenerator, "generate_module",
                        lambda self, data, *a, **k: saved.append(dict(data)) or Path(self.targets_dir) / "X")
    monkeypatch.setattr("orchestrator.tools.tools_targets.record_module_saved", lambda *a, **k: None)
    call(fm, "wizard_next", app())
    call(fm, "wizard_fill", app(), values={"name": "Board_X"})
    wizard.accept()
    assert saved and saved[0]["provenance"] == "ai-generated", "the first build is shown to the person once"
    assert "untested" in saved[0]["description"]


def test_wizard_sharing_choices_stay_the_persons(fm, wizard):
    for _ in range(4):
        call(fm, "wizard_next")
    with pytest.raises(ToolError) as exc:
        call(fm, "wizard_fill", values={"export": True})
    assert "only the person decides" in str(exc.value) and not wizard.chk_export.isChecked()


def test_the_wizard_opens_deep_ingest_for_the_person(fm, wizard, monkeypatch):
    opened = []
    wizard.gui_form.actions["deep_ingest"] = lambda: opened.append(1)
    out = call(fm, "wizard_docs")
    assert opened == [1] and "starts the crawl" in out.note
    with pytest.raises(ToolError) as exc:
        call(fm, "wizard_docs", values={"name": "x"})
    assert exc.value.code == "invalid_value" and opened == [1]


def test_the_wizard_state_says_when_it_is_closed(fm, surface):
    out = call(fm, "wizard_state", app())
    assert out.open is False and "wizard_open" in out.note


# --- show_window and the main window ---------------------------------------------------------------------------------

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


def test_show_window_opens_tabs_and_windows(fm, builder, qapp):
    from PySide6.QtCore import QObject
    from orchestrator.gui import form_surface
    window = FakeWindow(fm, builder)
    surface = form_surface.open_gui(window)
    try:
        assert surface.form("builder") is builder.gui_form
        out = call(fm, "show_window", window="builder")
        assert out.shown and window.tabs.currentWidget() is builder
        call(fm, "show_window", window="module_wizard")
        call(fm, "show_window", app(), window="chat")
        call(fm, "wizard_open", app())
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


def test_every_gui_tool_call_is_audited_with_its_caller(fm, builder):
    call(fm, "read_form", form="builder")
    call(fm, "fill_form", form="builder", values={"use_gpu": False})
    call(fm, "fill_form", app(actor=with_token(fm)), form="builder", values={"imatrix": True})
    rows = audited(fm, "gui")
    assert {r["actor"] for r in rows} == {"ditto", "mcp:laptop"}
    assert any(r["outcome"] == "started" and r["actor"] == "mcp:laptop" for r in rows), "write-ahead before the fill"


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


# --- review of the package: what the first tests let through ---------------------------------------------------------

def test_a_file_the_person_browsed_to_is_never_overwritten(fm, builder, monkeypatch, tmp_path):
    """A model or dataset the person picked with the Browse button was set by the program, so it did not count as the
    person's choice and Ditto could fill over it."""
    from orchestrator.gui import builder_tab
    picked = iter([str(tmp_path / "mine.gguf"), str(tmp_path / "calib.txt")])
    monkeypatch.setattr(builder_tab.QFileDialog, "getOpenFileName", lambda *a, **k: (next(picked), ""))
    builder._browse_model()
    builder._browse_dataset()
    with pytest.raises(ToolError) as exc:
        call(fm, "fill_form", ditto(confirm=lambda s, i: pytest.fail("nothing to ask")), form="builder",
             values={"model": "Qwen/Qwen3.5-0.8B"})
    assert exc.value.code == "needs_person" and builder.model_input.currentText() == str(tmp_path / "mine.gguf")
    assert builder.gui_form.field("dataset").person_set


def test_choices_never_name_the_home_folder(fm, surface):
    """The values of a choice field were checked, its offered choices were not: a local server can list its models by
    their full path in the home folder."""
    from orchestrator.gui.dialogs import AIConfigurationDialog
    dialog = AIConfigurationDialog(fm)
    dialog.show()
    try:
        private = str(Path.home() / "Private Person" / "models" / "local.gguf")
        dialog._fill_models([private, "small-model"], keep="small-model")
        form = call(fm, "read_form", app(), form="ai_settings").form
        model = next(f for f in form.fields if f.id == "model")
        assert "small-model" in model.choices and any(c.endswith("local.gguf") for c in model.choices)
        assert str(Path.home()) not in form.model_dump_json()
    finally:
        dialog.reject()


def test_ask_ditto_keeps_what_the_person_typed_in_the_chat(qapp):
    """The Builder's Ask Ditto wrote its question over a draft the person had typed in the chat."""
    from orchestrator.gui import form_surface
    from orchestrator.gui.chat_panel.composer import ComposerInput
    box = ComposerInput()
    box.setText("my own draft")
    window = SimpleNamespace(chat_window=SimpleNamespace(txt_input=box, focus_composer=lambda: None),
                             _show_chat=lambda: None)
    form_surface.ask_ditto(window, "Which board is it?")
    assert box.text().startswith("my own draft") and box.text().endswith("Which board is it?")
    box.setText("")
    form_surface.ask_ditto(window, "Which board is it?")
    assert box.text() == "Which board is it?"
    box.deleteLater()


def test_a_field_on_another_wizard_page_names_the_way_there(fm, wizard):
    """A small model that fills a field of another page was told to give up and ask the person; the way is to go to
    that page first."""
    call(fm, "wizard_next")
    with pytest.raises(ToolError) as exc:
        call(fm, "wizard_fill", ditto(confirm=lambda s, i: pytest.fail("nothing to ask")), values={"packages": "cmake"})
    assert "wizard_next" in exc.value.next and "page" in exc.value.next and "chat" not in exc.value.next


def test_a_home_folder_of_another_account_never_reaches_the_ai(fm, wizard):
    """Only the framework's own home folder was hidden. The person may type a path of their own account (the framework
    can run under another one), and the name in /home/<name> or C:\\Users\\<name> reached the AI."""
    wizard.setup_commands.setPlainText("cp /home/jane.doe/sdk/setup.sh . && tar xf C:\\Users\\Jane Roe\\x.tar")
    wizard.sdk_edit.setText("RKNN from /Users/bob")
    out = call(fm, "read_form", app(), form="module_wizard").form.model_dump_json()
    assert "jane" not in out.lower() and "Roe" not in out and "bob" not in out
    assert "~/sdk/setup.sh" in out, "the rest of the path stays readable"
