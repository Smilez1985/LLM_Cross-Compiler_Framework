"""The next-step catalogue (wave 6, chain a0; design A, DECISIONS round 10 V1, V2, Q7): steps as data instead of
hand-written commands, one renderer per audience, mouse paths from the menu layout, the carriers, the allow-to-run
proposals (exact argv, validated values, secrets through stdin, deletions never run blind) and the error report.

Qt-free: nothing here needs a window (the offscreen checks against the live menus are in tests/test_cli_gui_parity.py).
"""

import importlib
import pickle
import pkgutil
import sys
import threading

import pytest

from orchestrator.utils import next_steps as ns
from orchestrator.utils.next_steps import Line, NextStep, ProposalRefused, WithSteps

# Every item of the wave-6 next-steps audit (w6_audit.json, area next-steps) and the catalogue steps seeded for it.
# Items the mechanism itself covers (the renderer, the channel, the menus, the tests) name what covers them.
AUDIT_ITEMS = {
    1: "mechanism: render(..., 'gui') for the person, ToolError.steps for the model",
    2: "mechanism: BlockerSpec.step, the activity producer's blocked rows carry it",
    3: "mechanism: ToolContext.channel",
    4: ("mojo.oracle", "mojo.miracle"), 5: ("images.free",), 6: ("snapshots.free", "snapshots.export"),
    7: ("storage.delete_old",), 8: ("storage.convert",), 9: ("runtime.switch", "runtime.install", "runtime.prebuilt"),
    10: ("signin.provider", "secrets.set", "keystore.use"),
    11: ("permissions.edit", "tokens.add", "audit.archive", "tokens.reset_store"), 12: ("board.trust",),
    13: ("pack.reinstall", "pack.remove"), 14: ("snapshots.list",), 15: ("images.free",),
    16: ("models.download", "runtime.install", "runtime.prebuilt", "runtime.stop", "runtime.switch"),
    17: ("runtime.start",), 18: ("runtime.switch", "runtime.start", "runtime.stop", "runtime.install_native",
                                 "probe.local"),
    19: ("runtime.ensure",),
    20: ("runtime.export", "runtime.import", "runtime.prebuilt", "runtime.placement", "runtime.backend",
         "runtime.model", "runtime.install", "probe.local", "runtime.install_tools"),
    21: ("probe.script", "probe.local", "mojo.oracle", "probe.import"), 22: ("probe.import",), 23: ("probe.local",),
    24: ("board.probe", "boards.list"), 25: ("board.settings",), 26: ("board.settings", "board.probe"),
    27: ("board.prune_on_device",), 28: ("board.restart_server",), 29: ("board.runtime_build", "board.runtime_deploy"),
    30: ("board.pair", "board.trust", "board.settings"), 31: ("board.trust",),
    32: ("pack.passphrase", "keys.show", "keys.new"), 33: ("pack.export_to",), 34: ("keys.show", "secrets.set"),
    35: ("tokens.add", "tokens.list", "tokens.reset_store"), 36: ("permissions.edit", "mojo.oracle"),
    37: ("audit.archive", "mojo.oracle"), 38: ("approvals.list",),
    39: ("signin.provider", "signin.workspace", "signin.method", "mcp.connect"),
    40: ("signin.anthropic_cli", "signin.ollama"), 41: ("crawl.notice",),
    42: ("settings.offline_off", "settings.knowledge_on"), 43: ("secrets.set",),
    44: ("permissions.edit", "permissions.token", "mojo.oracle", "approvals.list"), 45: ("mojo.oracle",),
    46: ("secrets.set", "signin.provider", "permissions.edit", "audit.archive", "board.trust", "crawl.notice",
         "pack.publish", "pack.install_file", "trash.empty", "images.free"),
    47: ("runtime.install", "runtime.install_container", "runtime.import_source", "board.verify_bench",
         "runtime.install_tools"),
    48: ("keys.show",), 49: ("folders.add",), 50: ("trash.restore", "trash.empty"), 51: ("runtime.switch", "ditto.ask"),
    52: ("board.pair", "board.trust", "settings.knowledge_on", "crawl.notice", "ditto.notice", "settings.sandbox_on"),
    53: ("settings.offline_off", "settings.sandbox_on", "settings.knowledge_on", "settings.pin_image", "board.pair",
         "board.trust", "crawl.notice", "runtime.install_tools"),
    54: ("images.free", "docker.group"), 55: ("board.forget_key",),
    56: ("docker.start", "board.ssh_enable", "docker.login", "settings.pin_image"), 57: ("probe.local", "probe.import"),
    58: "text: the boards intro names no command (l10n_boards)",
    59: "text: the deploy tooltips name no command (l10n_deploy)",
    60: ("board.runtime_deploy",),
    61: "text: zoo.export.no_recipients names no command (l10n_knowledge)",
    62: "text: no CLI flag in a label (chains build and ai-oversight)",
    63: ("mcp.connect", "signin.anthropic_cli"),
    64: "text: 'another framework window or command' (chain ditto-runtime)",
    65: ("runtime.install_container", "runtime.import"),
    66: "mechanism: every menu entry has a ui_text key and an objectName (l10n_main, MENU_LAYOUT)",
    67: "mechanism: tests/test_no_command_texts.py and tests/test_cli_gui_parity.py",
}


def cli_paths():
    import orchestrator.gui.menus as menus
    found = {}
    for info in pkgutil.iter_modules(menus.__path__):
        module = importlib.import_module(f"orchestrator.gui.menus.{info.name}")
        found.update(getattr(module, "CLI_PATHS", None) or {})
    return found


# --- the catalogue ----------------------------------------------------------------------------------------------------

def test_the_catalogue_is_consistent_with_the_cli_the_tools_and_the_windows():
    from orchestrator.tools.framework_tools import KNOWN_TOOLS
    from orchestrator.tools.tool_windows import WINDOW_IDS
    pytest.importorskip("PySide6")   # the CLI_PATHS tables live in the GUI's menu modules
    assert ns.catalogue_problems(KNOWN_TOOLS, cli_paths(), WINDOW_IDS) == []
    assert len(ns.steps()) >= 100


def test_every_audit_item_is_seeded():
    assert sorted(AUDIT_ITEMS) == list(range(1, 68)), "all 67 items of the next-steps audit"
    known = ns.steps()
    missing = [(item, step) for item, steps in AUDIT_ITEMS.items() if isinstance(steps, tuple)
               for step in steps if step not in known]
    assert missing == []


def test_the_area_modules_are_found_without_a_shared_list():
    names = {m.__name__.rsplit(".", 1)[-1] for m in ns.area_modules()}
    assert {"common", "runtime", "boards", "build", "knowledge", "deploy", "signin", "tools_mcp",
            "ditto_setup"} <= names
    owners = ns.catalogue().owner
    assert owners["runtime.install"] == "runtime" and owners["board.trust"] == "boards"


def test_step_ids_match_the_activity_rows_pattern():
    import re
    from orchestrator.tools.tool_windows import STEP_ID_RE
    assert all(re.fullmatch(STEP_ID_RE, step_id) for step_id in ns.steps())


def test_a_second_step_with_the_same_id_is_a_problem():
    class Area:
        __name__ = "orchestrator.utils.next_steps.fake"
        STEPS = (ns.Step("mojo.oracle", person_only="permission"),)
    cat = ns.Catalogue([*ns.area_modules(), Area])
    assert any("mojo.oracle" in p for p in cat.problems)


# --- mouse paths ------------------------------------------------------------------------------------------------------

def test_mouse_paths_follow_the_menu_layout_in_both_languages():
    assert ns.mouse_path("act_tools_permissions", "en") == "Ditto > Tool Permissions..."
    assert ns.mouse_path("act_tools_permissions", "de") == "Ditto > Tool-Berechtigungen..."
    assert ns.mouse_path("act_ditto_runtime_open", "en") == "Ditto > Advanced > Local Runtime..."
    assert ns.mouse_path("btn_ditto_runtime_build", "de") == ("Ditto > Erweitert > Lokale Laufzeit... > "
                                                             "llama-server bauen…")
    assert ns.mouse_path("act_mojo_health", "de") == "Extras > Wartung > Mojo-Gesundheitscheck..."
    assert ns.mouse_path("act_deploy_scan_remote", "en") == "Builder > Scan / Import... > Remote Scan via SSH..."
    assert ns.mouse_path("act_file_trash", "de") == "Datei > Papierkorb..."
    with pytest.raises(KeyError):
        ns.mouse_path("act_does_not_exist")


def test_labels_lose_mnemonics_symbols_and_colons():
    assert ns.clean_label("&File") == "File"
    assert ns.clean_label("Save && close") == "Save & close"
    assert ns.clean_label("💻 Scan Local Host") == "Scan Local Host"
    assert ns.clean_label("Run it:") == "Run it"


def test_path_placeholders_are_filled_in_the_texts_language():
    assert ns.fill_paths("Open {path:act_mojo_health}.", "de") == "Open Extras > Wartung > Mojo-Gesundheitscheck...."
    assert ns.fill_paths("no place here", "de") == "no place here"
    assert ns.fill_paths("{path:act_unknown_thing}", "en") == "act_unknown_thing"


def test_ui_text_fills_path_placeholders(monkeypatch):
    from orchestrator.utils.localization import LocalizationManager, ui_text
    monkeypatch.setitem(LocalizationManager.TRANSLATIONS["en"], "test.path.text", "Look in {path:act_file_trash}: {n}")
    monkeypatch.setitem(LocalizationManager.TRANSLATIONS["de"], "test.path.text", "Schau in {path:act_file_trash}: {n}")
    assert ui_text("test.path.text", "en", n=2) == "Look in File > Trash...: 2"
    assert ui_text("test.path.text", "de", n=2) == "Schau in Datei > Papierkorb...: 2"


def test_window_of_names_what_opens_an_objects_window():
    assert ns.window_of("btn_ditto_runtime_build") == "act_ditto_runtime_open"
    assert ns.window_of("act_deploy_scan_remote") == "builder_tab"
    assert ns.window_of("act_mojo_health") == "act_mojo_health"


# --- rendering --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("lang", ["en", "de"])
def test_the_gui_never_shows_a_framework_command(lang):
    for step_id, step in ns.steps().items():
        text = ns.render([step_id], "gui", lang)
        assert "llmcf" not in text and "llm-cli" not in text, (step_id, text)
        if not step.shown_only:   # a board's or another program's own command may carry its options
            assert " --" not in text, (step_id, text)


def test_the_cli_keeps_naming_the_command():
    assert ns.render(["runtime.install"], "cli") == ("Next: Build Ditto's local model program: "
                                                     "llmcf ditto runtime install --yes")
    assert ns.render([NextStep("board.prune_on_device", {"device": "rk3566"})], "cli").endswith(
        "llmcf device runtime prune rk3566 --on-device")
    assert ns.render(["board.ssh_enable"], "cli") == ("Next: Switch the remote login (SSH) on (on the board, in its "
                                                      "terminal): sudo systemctl enable --now ssh")


def test_the_ai_gets_the_tool_or_the_place_and_the_command_only_where_a_terminal_is():
    assert ns.render(["runtime.install"], "ai") == "Build Ditto's local model program: call build_local_runtime."
    for channel in ("gui-chat", "gui"):
        text = ns.render(["permissions.edit"], "ai", channel=channel)
        assert "Ditto > Tool Permissions..." in text and "llmcf" not in text and "never a command" in text
    for channel in ("cli", None):
        text = ns.render(["permissions.edit"], "ai", channel=channel)
        assert "Ditto > Tool Permissions..." in text and "command line: llmcf tools permissions" in text


def test_inside_its_own_window_a_step_names_only_the_button():
    assert ns.render(["runtime.start"], "gui", "en", here="ditto_runtime") == ("Start Ditto's local model program: "
                                                                                "Start")
    assert ns.render(["runtime.start"], "gui", "en", here="act_ditto_runtime_open").endswith(": Start")
    assert ns.render(["runtime.start"], "gui", "en", here="boards").endswith(
        "Ditto > Advanced > Local Runtime... > Start")


def test_rendered_steps_carry_what_the_buttons_need():
    row = ns.rendered(["runtime.install"], "gui", "en")[0]
    assert (row.target, row.runnable, row.command) == ("btn_ditto_runtime_build", True,
                                                       "llmcf ditto runtime install --yes")
    copy = ns.rendered(["docker.start"], "gui", "en")[0]
    assert copy.copy == "sudo systemctl start docker" and copy.target is None
    planned = ns.rendered(["board.forget_key"], "gui", "en")[0]
    assert planned.planned and planned.target is None and "(not in this version yet)" in planned.text
    assert ns.rendered(["no.such_step", "mojo.oracle"], "gui", "en")[0].step_id == "mojo.oracle"


def test_choice_values_are_worded_for_the_person():
    assert "in Docker" in ns.step_text(NextStep("runtime.switch", {"mode": "docker"}), "de", "what")
    assert "directly on this computer" in ns.step_text(NextStep("runtime.switch", {"mode": "bare-metal"}), "en", "what")


# --- carriers ---------------------------------------------------------------------------------------------------------

def test_a_line_is_its_text_and_carries_steps():
    line = Line("The server is not installed.", ["runtime.install"])
    assert line == "The server is not installed." and str(line) == "The server is not installed."
    assert ns.steps_of(line) == (NextStep("runtime.install"),)
    assert " ".join([line, "x"]) == "The server is not installed. x"
    again = pickle.loads(pickle.dumps(line))
    assert again == line and again.steps == line.steps


def test_exceptions_carry_steps_with_the_mixin():
    class Refused(WithSteps, RuntimeError):
        pass
    error = Refused("The model is not downloaded.").with_steps(("models.download", {"tier": "small"}))
    assert str(error) == "The model is not downloaded."
    assert ns.steps_of(error) == (NextStep("models.download", {"tier": "small"}),)
    assert ns.steps_of(RuntimeError("plain")) == ()
    assert ns.steps_of(None) == () and ns.steps_of(["mojo.oracle", 42]) == (NextStep("mojo.oracle"),)


def test_a_next_step_needs_a_step_id():
    with pytest.raises(ValueError):
        NextStep("not an id")
    assert ns.as_next_steps(["bad id", "mojo.oracle"]) == (NextStep("mojo.oracle"),)


# --- allow-to-run proposals (V1) --------------------------------------------------------------------------------------

def test_a_proposal_has_the_exact_command_and_says_what_and_why():
    prop = ns.proposal("runtime.switch", {"mode": "docker"}, "de")
    assert prop.kind == "run"
    assert prop.argv == ("ditto", "runtime", "ensure", "--mode", "docker", "--force", "--yes")
    assert prop.command == "llmcf ditto runtime ensure --mode docker --force --yes"
    assert prop.question().startswith("`llmcf ditto runtime ensure --mode docker --force --yes` zulassen? ")
    assert "in Docker" in prop.what and prop.why
    en = ns.proposal(NextStep("runtime.install", reason="rebuild"), lang="en")
    assert en.why == "The build no longer fits this computer and needs to be made again."
    with pytest.raises(ProposalRefused):
        ns.proposal("runtime.install", lang="en", reason="made_up")


@pytest.mark.parametrize("values", [{"mode": "--evil"}, {"mode": "auto"}, {"mode": "docker\n--x"}, {},
                                    {"mode": "docker", "extra": "1"}, {"mode": True}])
def test_values_outside_the_template_are_refused(values):
    with pytest.raises(ProposalRefused):
        ns.proposal("runtime.switch", values, "en")


def test_labels_and_ids_are_checked():
    assert ns.validate("board.check", {"device": "  my   board "}) == {"device": "my board"}
    for bad in ("-o ProxyCommand=x", "x" * 65, "line\nbreak", ""):
        with pytest.raises(ProposalRefused):
            ns.validate("board.check", {"device": bad})
    assert ns.validate("secrets.set", {"name": "hf_token"}) == {"name": "hf_token"}
    with pytest.raises(ProposalRefused):
        ns.validate("secrets.set", {"name": "../x"})


def test_deletions_open_the_list_and_other_places_are_only_shown():
    deletion = ns.proposal("images.free", lang="en")
    assert deletion.kind == "deletion" and deletion.argv == () and deletion.target == "act_framework_images"
    assert "full list" in deletion.question()
    shown = ns.proposal("board.ssh_enable", lang="de")
    assert shown.kind == "shown" and shown.copy == "sudo systemctl enable --now ssh" and shown.argv == ()
    assert "führt diesen Befehl nicht aus" in shown.question()
    opened = ns.proposal("permissions.edit", lang="en")
    assert opened.kind == "open" and opened.argv == () and opened.target == "act_tools_permissions"
    for prop in (deletion, shown, opened):
        with pytest.raises(ProposalRefused):
            ns.run_proposal(prop)


def test_a_secret_never_reaches_the_command_line():
    prop = ns.proposal(NextStep("secrets.set", {"name": "hf_token"}), lang="en")
    assert prop.kind == "run" and prop.secrets == ("value",)
    assert prop.argv == ("secrets", "set", "hf_token", "--with-token") and "value" not in prop.command.lower()
    assert "hidden field" in prop.question()
    with pytest.raises(ProposalRefused):
        ns.run_proposal(prop)   # the masked value is missing


def test_a_path_is_never_a_proposal_value():
    with pytest.raises(ProposalRefused):
        ns.validate("folders.add", {"folder": "/etc", "label": "x"})


def fake_child(monkeypatch, code: str):
    """run_proposal starts `python -c code` instead of the CLI (the argv check still runs first)."""
    monkeypatch.setattr(ns, "child_argv", lambda prop, config_path=None, python=None: [sys.executable, "-c", code])


def test_a_run_streams_its_redacted_lines_and_its_result(monkeypatch):
    from test_secret_scan import SAMPLES
    key = SAMPLES["Anthropic key"]
    fake_child(monkeypatch, f"import sys\nprint('first')\nprint({key!r})\nprint('\\x1b[31mred')\nsys.exit(3)")
    seen = []
    result = ns.run_proposal(ns.proposal("runtime.stop", lang="de"), seen.append, timeout=60)
    assert result.state == "failed" and result.exit_code == 3
    assert seen[0] == "first" and key not in "\n".join(seen) and "REDACTED" in seen[1]
    assert "\x1b" not in seen[2]
    assert result.summary == "Der Befehl ist fehlgeschlagen (Exit-Code 3). Die Ausgabe oben zeigt, warum."
    fake_child(monkeypatch, "print('ok')")
    done = ns.run_proposal(ns.proposal("runtime.stop", lang="en"), timeout=60)
    assert (done.state, done.exit_code, done.lines) == ("done", 0, ("ok",))


def test_a_secret_goes_through_stdin(monkeypatch):
    fake_child(monkeypatch, "import sys\nvalue = sys.stdin.readline().strip()\nprint('got', len(value))")
    prop = ns.proposal(NextStep("secrets.set", {"name": "hf_token"}), lang="en")
    result = ns.run_proposal(prop, secrets={"value": "abc123"}, timeout=60)
    assert result.lines == ("got 6",) and all("abc123" not in line for line in result.lines)


def test_a_run_can_be_cancelled_or_time_out(monkeypatch):
    fake_child(monkeypatch, "import time\nprint('start', flush=True)\ntime.sleep(60)")
    cancel = threading.Event()
    timer = threading.Timer(0.5, cancel.set)
    timer.start()
    result = ns.run_proposal(ns.proposal("runtime.stop", lang="en"), cancel=cancel, timeout=120)
    timer.cancel()
    assert result.state == "cancelled" and result.exit_code is None
    timed = ns.run_proposal(ns.proposal("runtime.stop", lang="en"), timeout=0.5)
    assert timed.state == "timeout" and timed.exit_code is None


def test_a_changed_proposal_is_refused():
    import dataclasses
    prop = ns.proposal("runtime.switch", {"mode": "docker"}, "en")
    tampered = dataclasses.replace(prop, argv=("trash", "empty", "--all"))
    with pytest.raises(ProposalRefused):
        ns.run_proposal(tampered)


def test_the_child_is_the_framework_cli_with_the_guis_config(tmp_path):
    prop = ns.proposal("runtime.stop", lang="en")
    argv = ns.child_argv(prop, config_path=str(tmp_path / "config.yml"), python="python3")
    assert argv == ["python3", "-m", "orchestrator.cli", "-q", "--config", str(tmp_path / "config.yml"),
                    "ditto", "runtime", "stop"]


def test_the_real_cli_runs_as_the_child(tmp_path):
    """The same code path as the CLI: a real `python -m orchestrator.cli -q trash list` in the test's home."""
    result = ns.run_proposal(ns.proposal("trash.show", lang="en"), timeout=180,
                             env={"LLM_SECRET_STORAGE": "ephemeral"})   # no key safe in the test home
    assert result.state == "done", result.lines


def test_the_decision_is_recorded(tmp_path):
    from orchestrator.tools.audit import AuditLog
    audit = AuditLog(tmp_path / "audit.db")
    prop = ns.proposal("runtime.stop", lang="en")
    first = ns.audit_proposal(audit, prop, approved=False)
    result = ns.ProposalResult("done", 0, (), "Done.")
    second = ns.audit_proposal(audit, prop, approved=True, result=result)
    assert first and second and second > first
    check = audit.check()
    assert check.intact and check.entries == 2, check.message
    assert ns.audit_proposal(None, prop, approved=True) is None


# --- error reports (Q7) -----------------------------------------------------------------------------------------------

def test_an_error_report_is_localized_with_the_english_detail():
    from orchestrator.tools.registry import ToolError
    from test_secret_scan import SAMPLES
    error = ToolError(f"Permission denied for the call (key {SAMPLES['Anthropic key']}).", code="permission_denied",
                      steps=["permissions.edit"])
    report = ns.error_report(error, "de")
    assert report.summary == "Deine Einstellungen erlauben das nicht."
    assert report.title == "Etwas hat nicht geklappt" and report.details_label == "Technische Details"
    assert report.steps == (NextStep("permissions.edit"),) and report.code == "permission_denied"
    assert report.detail.startswith("ToolError: Permission denied") and SAMPLES["Anthropic key"] not in report.detail
    plain = ns.error_report(OSError("disk gone"), "en")
    assert plain.summary == "Something went wrong." and plain.detail == "OSError: disk gone" and plain.steps == ()
    dotted = ns.error_report(type("E", (Exception,), {"code": "storage.locked"})("x"), "en")
    assert dotted.summary == "The key safe cannot be used."
    assert ns.error_report(OSError("x"), "en", summary="Saving failed.").summary == "Saving failed."
    assert report.dialog_kwargs() == {"summary": report.summary, "steps": report.steps, "details": report.detail,
                                      "title": report.title}


# --- the carriers in the framework ------------------------------------------------------------------------------------

def test_a_blocker_refusal_carries_its_step_rendered_for_the_channel():
    from orchestrator.tools import facts_catalog as catalog
    error = catalog.blocked("disk_full", "Not enough disk space.")
    assert error.steps == (NextStep("images.free"),)
    assert "llmcf doctor --fix --tier 2" in error.text()   # an MCP app (channel None) may get the command line
    chat = catalog.blocked("disk_full", "Not enough disk space.").fill_steps("gui-chat")
    assert "Tools > Maintenance > Framework Build Images..." in chat.next and "llmcf" not in chat.next
    resolvable = catalog.blocked("runtime_not_built", "Not built.")
    assert resolvable.next_text().count("build_local_runtime") == 1, "a tool the next step names is not repeated"
    assert catalog.with_blocker(type("E", (Exception,), {})(), "gui_not_open").steps == (NextStep("window.open"),)


def test_every_blocker_step_is_a_catalogue_step():
    from orchestrator.tools.facts_catalog import BLOCKERS
    known = ns.steps()
    assert [code for code, spec in BLOCKERS.items() if spec.step and spec.step not in known] == []
    waiting = {"commit_required"}   # nothing but waiting for Hugging Face helps
    assert [c for c, spec in BLOCKERS.items() if spec.person_action and not spec.step and c not in waiting] == []


def test_the_registry_renders_a_refusals_steps_for_the_callers_channel(tmp_path):
    from orchestrator.tools.policy import Decision, ToolPolicy
    from orchestrator.tools.registry import ToolContext, ToolError
    from test_vocabulary_records import make_registry
    registry = make_registry(tmp_path, None)
    registry.policy_store.replace(ToolPolicy(configured=True, reviewed=["tool"], overrides={"tool": Decision.DENY}))
    seen = []
    registry.events.subscribe(lambda kind, data: seen.append((kind, data)))
    with pytest.raises(ToolError) as chat:
        registry.call("tool", {"text": "x"}, ToolContext("ditto", channel="gui-chat"))
    assert "Ditto > Tool Permissions..." in chat.value.next and "llmcf" not in chat.value.next
    assert chat.value.steps == (NextStep("permissions.edit"),)
    assert seen[-1][1]["step"] == "permissions.edit", "the blocked row names the step"
    with pytest.raises(ToolError) as app:
        registry.call("tool", {"text": "x"}, ToolContext("mcp"))
    assert "command line: llmcf tools permissions" in app.value.next


def test_a_handler_failure_points_to_mojo_without_a_terminal(tmp_path):
    from orchestrator.tools.registry import FAILED_NEXT, ToolContext, ToolError
    from test_vocabulary_records import make_registry

    def boom(inp, ctx):
        raise RuntimeError("disk gone")
    registry = make_registry(tmp_path, boom)
    with pytest.raises(ToolError) as exc:
        registry.call("tool", {"text": "x"}, ToolContext("ditto", channel="gui-chat"))
    assert exc.value.next == FAILED_NEXT and "terminal" not in FAILED_NEXT and "llmcf" not in FAILED_NEXT
    assert exc.value.steps == (NextStep("mojo.oracle"),)


def test_a_core_exceptions_steps_reach_the_tool_error(tmp_path):
    from orchestrator.runtime.llama_server import LocalRuntimeError
    from orchestrator.tools.registry import ToolContext, ToolError
    from test_vocabulary_records import make_registry

    class Refused(LocalRuntimeError, ValueError):
        pass

    def refuse(inp, ctx):
        raise Refused("The model is not downloaded.").with_steps("models.download")
    registry = make_registry(tmp_path, refuse)
    with pytest.raises(ToolError) as exc:
        registry.call("tool", {"text": "x"}, ToolContext("ditto", channel="gui"))
    assert exc.value.steps == (NextStep("models.download"),) and "build_local_runtime" in exc.value.next
    assert ns.steps_of(LocalRuntimeError("x")) == ()


def test_mojo_findings_carry_steps_and_keep_their_cli_hint():
    from orchestrator.Core.mojo.findings import Finding, Severity
    finding = Finding("disk", "disk.low", Severity.WARN, "The disk is nearly full.",
                      fix_hint="llmcf mojo --miracle", steps=(NextStep("images.free"), "mojo.miracle"))
    assert ns.steps_of(finding) == (NextStep("images.free"), NextStep("mojo.miracle"))
    row = finding.to_dict()
    assert row["steps"] == ["images.free", "mojo.miracle"] and row["fix_hint"] == "llmcf mojo --miracle"


def test_the_cli_prints_an_errors_steps_as_next_lines(capsys):
    from orchestrator import cli
    from orchestrator.runtime.llama_server import LocalRuntimeError
    from orchestrator.tools.registry import ToolError
    cli._say_error(LocalRuntimeError("The local runtime is not installed.").with_steps("runtime.install"))
    err = capsys.readouterr().err.splitlines()
    assert err == ["The local runtime is not installed.",
                   "Next: Build Ditto's local model program: llmcf ditto runtime install --yes"]
    cli._say_error(ToolError("Refused.", next="Ask the person.", steps=["permissions.edit"]))
    err = capsys.readouterr().err.splitlines()
    assert err == ["Refused.", "Next: Allow it in the tool permissions: llmcf tools permissions"]
    cli._say_error(ToolError("Refused.", next="Ask the person."))
    assert capsys.readouterr().err.splitlines() == ["Refused. Next: Ask the person."]
