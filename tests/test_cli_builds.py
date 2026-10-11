"""Regression tests for the CLI and build fixes of the cli-builds track (numbers: the items of the task).

1 version strings, 2 `build status` across processes (build records), 3 `--parallel` / `--optimization`,
4 Ctrl+C cancels the build, 5 Cancel button in the Builder tab, 6 `scan --output`, 7 `module create` folder,
8 `config show`, 9 `config repos --add`, 10 `targets list` / `targets validate`, 11 exit codes on failure,
12 `doctor --tier` help, 13 runtime models_dir, 14 `ditto eval --tier` keeps a server you started,
15 target id vs folder name, 16 `max_concurrent_builds`, 17 `IMATRIX_CHUNKS`, 18 plain-text Builder log.
"""

import io
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from conftest import REPO_ROOT
from orchestrator.Core.builder import BuildProgress, BuildStatus, ModelFormat
from test_foundation import FakeOrchestrator
from test_review_capabilities_paths import (HF_MODEL, ROCKCHIP_DIR, FakeDeploymentManager, build_start,
                                            framework_with_targets, make_cli, no_docker, write_manifest)

ROCKCHIP_CAPS = yaml.safe_load((ROCKCHIP_DIR / "target.yml").read_text(encoding="utf-8"))["capabilities"]


@pytest.fixture
def cli(booted_framework, tmp_path, monkeypatch):
    return make_cli(booted_framework, tmp_path, monkeypatch)


def invoke_plain(*args, input=None, ctx=None):
    """The real CLI without a framework (commands that do not boot it), output captured as plain text."""
    from click.testing import CliRunner

    from orchestrator import cli as cli_module
    result = CliRunner().invoke(cli_module.cli, ["-q", *args], input=input, obj=ctx)
    return SimpleNamespace(code=result.exit_code, out=result.output, stdout=result.stdout,
                           exception=result.exception)


def quiet_console(monkeypatch):
    from rich.console import Console

    from orchestrator import cli as cli_module
    printed = io.StringIO()
    monkeypatch.setattr(cli_module, "console", Console(file=printed, width=500, color_system=None))
    return printed


def write_user_config(tmp_path, **values):
    """config.yml in the test's settings folder (LLM_FRAMEWORK_CONFIG_DIR of conftest)."""
    folder = tmp_path / "configs"
    folder.mkdir(exist_ok=True)
    path = folder / "config.yml"
    data = yaml.safe_load(path.read_text()) if path.exists() else {}
    data = data or {}
    data.update(values)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------------------------------------------
# 1. Version strings
# ---------------------------------------------------------------------------------------------------------------

class TestVersionStrings:

    def test_help_shows_the_package_version(self):
        from orchestrator import __version__
        result = invoke_plain("--help")
        assert result.code == 0, result.out
        assert f"CLI v{__version__}" in result.out
        assert "2.4.0" not in result.out

    def test_module_docstring_has_no_stale_version(self):
        from orchestrator import cli as cli_module
        assert not re.search(r"v\d+\.\d+\.\d+", cli_module.__doc__)

    def test_version_option(self):
        from orchestrator import __version__
        result = invoke_plain("--version")
        assert result.code == 0 and __version__ in result.out


# ---------------------------------------------------------------------------------------------------------------
# 2. build status reads the build records every engine writes
# ---------------------------------------------------------------------------------------------------------------

def dead_pid():
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def record(**values):
    data = dict(build_id="req_other001_001", request_id="req_other001", pid=os.getpid(), process_started=None,
                status="building", stage="Running modules", progress=60, model="org/model", target="Rockchip",
                format="gguf", quantization="Q4_K_M", board="rk3588", output_dir="/out",
                started="2026-10-11T10:00:00")
    data.update(values)
    return data


class TestBuildRecords:

    def test_engine_writes_a_record_through_the_whole_build(self, tmp_path, monkeypatch):
        from orchestrator.Core import build_records
        from orchestrator.Core.builder import BuildConfiguration, BuildEngine
        from orchestrator.Core.config_manager import ConfigManager
        no_docker(monkeypatch)
        engine = BuildEngine(ConfigManager(tmp_path / "configs"), base_dir=tmp_path)
        monkeypatch.setattr(engine, "_resolve_dataset", lambda c, p: (_ for _ in ()).throw(
            RuntimeError("token hf_" "abcdefghijklmnopqrstuvwxyzABCDEFGH broke it")))
        config = BuildConfiguration(build_id="req_abc_001", timestamp="t", model_source="org/m", target_arch="Rockchip",
                                    target_format=ModelFormat.GGUF, output_dir=str(tmp_path / "out"),
                                    quantization="Q4_0", target_board="rk3566", request_id="req_abc")
        engine.build_model(config)
        deadline = time.monotonic() + 10
        while engine.is_active("req_abc_001") and time.monotonic() < deadline:
            time.sleep(0.01)
        (rec,) = build_records.read_records(engine.records_dir)
        assert engine.records_dir == tmp_path / "cache" / "build_records"
        assert (rec["build_id"], rec["request_id"], rec["status"]) == ("req_abc_001", "req_abc", "failed")
        assert (rec["target"], rec["board"], rec["quantization"], rec["pid"]) == ("Rockchip", "rk3566", "Q4_0",
                                                                                   os.getpid())
        assert "broke it" in rec["error"] and "hf_abcdefghij" not in rec["error"]
        assert rec["ended"]

    def test_an_unfinished_build_of_an_ended_process_is_interrupted(self, tmp_path):
        from orchestrator.Core import build_records
        folder = tmp_path / "records"
        build_records.write_record(folder, record(build_id="b_alive", **build_records.this_process()))
        build_records.write_record(folder, record(build_id="b_dead", pid=dead_pid()))
        build_records.write_record(folder, record(build_id="b_reused_pid", pid=os.getpid(), process_started=1.0))
        build_records.write_record(folder, record(build_id="b_done", pid=dead_pid(), status="completed"))
        (folder / "broken.json").write_text("{nope")
        states = {r["build_id"]: r["status"] for r in build_records.read_records(folder)}
        assert states == {"b_alive": "building", "b_dead": "interrupted", "b_reused_pid": "interrupted",
                          "b_done": "completed"}

    @pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
    def test_records_are_readable_only_by_their_owner(self, tmp_path):
        # A record names the model, the output folder and errors; in a shared installation other users read them.
        import stat
        from orchestrator.Core import build_records
        old_umask = os.umask(0o022)
        try:
            folder = tmp_path / "cache" / "build_records"
            build_records.write_record(folder, record(build_id="b1"))
            earlier = tmp_path / "earlier" / "build_records"  # a folder an earlier version created world-readable
            earlier.mkdir(parents=True)
            earlier.chmod(0o755)
            build_records.write_record(earlier, record(build_id="b2"))
        finally:
            os.umask(old_umask)
        for path, mode in ((folder, 0o700), (folder / "b1.json", 0o600),
                           (earlier, 0o700), (earlier / "b2.json", 0o600)):
            assert stat.S_IMODE(path.stat().st_mode) == mode, path
        assert [r["build_id"] for r in build_records.read_records(folder)] == ["b1"]

    def test_prune_keeps_unfinished_and_the_newest_finished(self, tmp_path):
        from orchestrator.Core import build_records
        folder = tmp_path / "records"
        for i in range(5):
            build_records.write_record(folder, record(build_id=f"done{i}", status="completed",
                                                      started=f"2026-10-0{i + 1}T00:00:00"))
        build_records.write_record(folder, record(build_id="running", started="2026-01-01T00:00:00"))
        build_records.prune(folder, keep=2)
        assert sorted(p.stem for p in folder.glob("*.json")) == ["done3", "done4", "running"]

    def test_prune_counts_interrupted_builds_as_finished(self, tmp_path):
        # A program that crashed or was killed never finishes its records; they must not pile up forever.
        from orchestrator.Core import build_records
        folder = tmp_path / "records"
        for i in range(3):
            build_records.write_record(folder, record(build_id=f"crashed{i}", pid=dead_pid(),
                                                      started=f"2026-10-0{i + 1}T00:00:00"))
        build_records.write_record(folder, record(build_id="running", started="2026-01-01T00:00:00",
                                                  **build_records.this_process()))
        build_records.prune(folder, keep=1)
        assert sorted(p.stem for p in folder.glob("*.json")) == ["crashed2", "running"]

    def test_records_in_the_installation_stay_out_of_git(self):
        # The default cache_dir is cache/ in the installation, usually a git clone: records hold model names,
        # local paths and errors and must not show up as files to commit. .gitignore ignores the whole cache/
        # folder of the installation, which includes the records.
        lines = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        assert "/cache/" in lines or "/cache/build_records/" in lines
        if not (REPO_ROOT / ".git").exists():
            return
        record = "cache/build_records/req_0123abcd_001.json"
        env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
        ignored = subprocess.run(["git", "-C", str(REPO_ROOT), "check-ignore", "--no-index", record],
                                 capture_output=True, text=True, env=env)
        assert ignored.stdout.split() == [record]


class TestCliBuildStatus:

    @pytest.fixture
    def records(self, tmp_path):
        from orchestrator.Core import build_records
        cache = tmp_path / "shared-cache"
        write_user_config(tmp_path, cache_dir=str(cache))
        folder = build_records.records_dir(cache)
        build_records.write_record(folder, record(build_id="req_gui00001_001", request_id="req_gui00001",
                                                  **build_records.this_process()))
        build_records.write_record(folder, record(build_id="req_mcp00002_001", request_id="req_mcp00002",
                                                  status="completed", progress=100, started="2026-10-11T09:00:00"))
        build_records.write_record(folder, record(build_id="req_old00003_001", request_id="req_old00003",
                                                  pid=dead_pid(), started="2026-10-10T09:00:00"))
        return folder

    def test_lists_builds_of_other_processes(self, records, monkeypatch):
        printed = quiet_console(monkeypatch)
        result = invoke_plain("build", "status")
        out = printed.getvalue() + result.out
        assert result.code == 0, out
        for rid, status in (("req_gui00001", "building"), ("req_mcp00002", "completed"),
                            ("req_old00003", "interrupted")):
            assert re.search(rf"{rid}\s.*{status}", out), out

    def test_one_request_in_detail(self, records, monkeypatch):
        printed = quiet_console(monkeypatch)
        result = invoke_plain("build", "status", "req_old00003")
        out = printed.getvalue()
        assert result.code == 0, out
        assert "Status: interrupted" in out and "Stage: Running modules" in out and "has ended" in out

    def test_unknown_request_exits_1(self, records, monkeypatch):
        printed = quiet_console(monkeypatch)
        assert invoke_plain("build", "status", "req_nothere").code == 1
        assert "not found" in printed.getvalue()

    def test_default_lists_the_newest_and_all_lists_every_build(self, tmp_path, monkeypatch):
        from orchestrator import cli as cli_module
        from orchestrator.Core import build_records
        cache = tmp_path / "cache-many"
        write_user_config(tmp_path, cache_dir=str(cache))
        for i in range(cli_module.STATUS_ROWS + 3):
            build_records.write_record(build_records.records_dir(cache), record(
                build_id=f"req_{i:08d}_001", request_id=f"req_{i:08d}", status="completed",
                started=f"2026-10-11T10:{i:02d}:00"))
        printed = quiet_console(monkeypatch)
        invoke_plain("build", "status")
        assert "3 older builds" in printed.getvalue() and "req_00000000" not in printed.getvalue()
        printed.truncate(0)
        invoke_plain("build", "status", "--all")
        assert "req_00000000" in printed.getvalue()

    def test_needs_neither_keyring_nor_docker(self, records, monkeypatch):
        from orchestrator import cli as cli_module
        monkeypatch.setattr(cli_module.FrameworkContext, "require_framework",
                            lambda self: pytest.fail("build status must not boot the framework"))
        quiet_console(monkeypatch)
        assert invoke_plain("build", "status").code == 0


# ---------------------------------------------------------------------------------------------------------------
# 3. --parallel is deprecated, --optimization is ignored by the Rockchip target
# ---------------------------------------------------------------------------------------------------------------

class TestParallelAndOptimization:

    def test_parallel_warns_and_still_builds(self, cli):
        result = build_start(cli, "--parallel")
        assert result.code == 0, result.out
        assert "--parallel is deprecated and has no effect" in result.out
        assert len(cli.submitted) == 1

    def test_parallel_is_hidden_and_optimization_documented(self):
        result = invoke_plain("build", "start", "--help")
        assert result.code == 0
        assert "--parallel" not in result.out
        assert "Rockchip target ignores it" in " ".join(result.out.split())

    def test_rockchip_scripts_do_not_read_optimization_level(self):
        # The help text says so; if a script starts using it, the help and CLI_Usage must change.
        for script in (ROCKCHIP_DIR / "modules").glob("*.sh"):
            if script.name in ("build.sh", "rkllm_module.sh", "rknn_module.sh"):
                code = "\n".join(line for line in script.read_text().splitlines()
                                 if not line.lstrip().startswith("#"))
                assert "OPTIMIZATION_LEVEL" not in code, script


# ---------------------------------------------------------------------------------------------------------------
# 4. Ctrl+C in build start cancels the build through the build service
# ---------------------------------------------------------------------------------------------------------------

class RunningEngine:
    """A build that runs until it is cancelled; cancelling 'stops its container'."""

    def __init__(self, targets_dir):
        self.targets_dir = targets_dir
        self.configs, self.cancelled = [], []
        self.started = threading.Event()
        self._status = {}

    def resolve_target_dir(self, name):
        path = self.targets_dir / name
        return path if path.is_dir() else None

    def build_model(self, config):
        self.configs.append(config)
        self._status[config.build_id] = BuildStatus.BUILDING
        self.started.set()
        return config.build_id

    def get_build_status(self, build_id):
        return SimpleNamespace(status=self._status[build_id], errors=[], logs=[])

    def cancel_build(self, build_id):
        self.cancelled.append(build_id)
        self._status[build_id] = BuildStatus.CANCELLED
        return True

    def is_active(self, build_id):
        return self._status.get(build_id) == BuildStatus.BUILDING


class TestCtrlC:

    @pytest.fixture
    def setup(self, booted_framework, tmp_path, monkeypatch):
        from orchestrator import cli as cli_module
        from orchestrator.Core.orchestrator import LLMOrchestrator
        engine = RunningEngine(REPO_ROOT / "targets")
        orch = LLMOrchestrator(booted_framework.config, build_engine=engine)
        booted_framework.orchestrator = orch
        cli = make_cli(booted_framework, tmp_path, monkeypatch)

        def interrupt_once_the_build_runs(seconds):
            assert engine.started.wait(10), "the build never reached the engine"
            raise KeyboardInterrupt

        monkeypatch.setattr(cli_module, "_pause", interrupt_once_the_build_runs)
        # The test's CLI context uses the framework's orchestrator (production: the same object).
        return SimpleNamespace(cli=cli, engine=engine, orch=orch)

    def test_ctrl_c_cancels_the_build_and_exits_130(self, setup, monkeypatch):
        from orchestrator import cli as cli_module

        class Ctx(cli_module.FrameworkContext):
            def require_framework(self):
                self.framework_manager = setup.cli.framework
                self.orchestrator = setup.orch
                return self

        printed = quiet_console(monkeypatch)
        result = invoke_plain("build", "start", "-m", HF_MODEL, "-t", "Rockchip", "-f", "gguf", "-b", "rk3588",
                              ctx=Ctx())
        out = printed.getvalue() + result.out
        assert result.code == 130, out
        (config,) = setup.engine.configs
        assert setup.engine.cancelled == [config.build_id], "the build's container was not stopped"
        state = setup.orch._workflows[config.request_id]
        assert state.status.value == "cancelled"
        assert "cancelled: its container was stopped" in out
        assert "when the CLI exits" not in out

    def test_ctrl_c_right_after_the_submission_cancels_too(self, setup, monkeypatch):
        # Ctrl+C while the CLI still prints the build's summary: the request exists, so it must be cancelled
        # (otherwise the CLI ends with "Aborted!" and waits at exit for the build to finish).
        from rich.console import Console

        from orchestrator import cli as cli_module

        class InterruptedConsole(Console):
            def print(self, *objects, **kwargs):
                if any("Following build progress" in str(o) for o in objects):
                    raise KeyboardInterrupt
                super().print(*objects, **kwargs)

        class Ctx(cli_module.FrameworkContext):
            def require_framework(self):
                self.framework_manager = setup.cli.framework
                self.orchestrator = setup.orch
                return self

        printed = io.StringIO()
        monkeypatch.setattr(cli_module, "console", InterruptedConsole(file=printed, width=500, color_system=None))
        result = invoke_plain("build", "start", "-m", HF_MODEL, "-t", "Rockchip", "-f", "gguf", "-b", "rk3588",
                              "--follow", ctx=Ctx())
        out = printed.getvalue() + result.out
        assert result.code == 130, out
        (state,) = setup.orch._workflows.values()
        assert state.status.value == "cancelled", out
        assert "Aborted" not in out

    def test_ctrl_c_after_the_build_ended_says_so(self, booted_framework, tmp_path, monkeypatch):
        from orchestrator import cli as cli_module
        make_cli(booted_framework, tmp_path, monkeypatch)  # working directory and console
        booted_framework.orchestrator = FakeOrchestrator()  # the service's orchestrator knows no running build

        def interrupt(seconds):
            raise KeyboardInterrupt

        monkeypatch.setattr(cli_module, "_pause", interrupt)
        from test_review_capabilities_paths import CliOrchestrator

        class Running(CliOrchestrator):
            async def get_workflow_status(self, request_id):
                return self._workflows.get(request_id)

        class Ctx(cli_module.FrameworkContext):
            def require_framework(self):
                self.framework_manager = booted_framework
                self.orchestrator = Running()
                return self

        printed = quiet_console(monkeypatch)
        result = invoke_plain("build", "start", "-m", HF_MODEL, "-t", "Rockchip", ctx=Ctx())
        assert result.code == 130
        assert "Unknown build request" in printed.getvalue()


class TestWaitStopped:

    def test_waits_until_the_engine_has_stopped_the_build(self, booted_framework):
        from orchestrator.Core.build_service import BuildService
        from orchestrator.Core.orchestrator import OrchestrationStatus, WorkflowState
        from datetime import datetime
        engine = RunningEngine(REPO_ROOT / "targets")
        engine._status["b1"] = BuildStatus.BUILDING
        booted_framework.orchestrator = SimpleNamespace(build_engine=engine, _workflows={
            "r1": WorkflowState("r1", OrchestrationStatus.CANCELLED, datetime.now(), build_ids=["b1"])})
        service = BuildService(booted_framework)
        assert service.wait_stopped("r1", 0.05, poll=0.01) == "running"
        threading.Timer(0.05, engine.cancel_build, args=("b1",)).start()
        assert service.wait_stopped("r1", 5, poll=0.01) == "cancelled"
        engine._status["b1"] = BuildStatus.COMPLETED
        assert service.wait_stopped("r1", 5) == "finished"


# ---------------------------------------------------------------------------------------------------------------
# 5. Cancel button in the Builder tab / 18. plain-text Builder log
# ---------------------------------------------------------------------------------------------------------------

@pytest.mark.usefixtures("qapp")
class TestBuilderTabCancel:

    @pytest.fixture
    def tab(self, booted_framework, monkeypatch):
        from orchestrator.gui import builder_tab
        monkeypatch.setattr(builder_tab.QMessageBox, "information", lambda *a, **k: None)
        monkeypatch.setattr(builder_tab.QMessageBox, "warning", lambda *a, **k: None)
        booted_framework.orchestrator = FakeOrchestrator()
        tab = builder_tab.BuilderTab(booted_framework)
        tab.target_combo.setCurrentIndex(tab.target_combo.findData("Rockchip"))
        tab.model_input.setEditText(HF_MODEL)
        yield tab
        # No polling timer or worker thread of this tab may outlive the test (and its framework).
        tab._build_timer.stop()
        worker = getattr(tab, "cancel_worker", None)
        if worker is not None:
            worker.wait(10000)

    @staticmethod
    def answer(monkeypatch, button):
        from orchestrator.gui import builder_tab
        asked = []
        monkeypatch.setattr(builder_tab.QMessageBox, "question", lambda *a, **k: asked.append(a) or button)
        return asked

    @staticmethod
    def finish(tab):
        from PySide6.QtWidgets import QApplication
        assert tab.cancel_worker.wait(10000)
        deadline = time.monotonic() + 5
        while tab.btn_cancel.isEnabled() or tab._active_request is not None:
            QApplication.processEvents()
            if time.monotonic() > deadline:
                break

    def test_cancel_is_enabled_only_while_a_build_runs(self, tab, monkeypatch):
        from orchestrator.gui.builder_tab import QMessageBox
        assert not tab.btn_cancel.isEnabled()
        tab._submit_build()
        (rid,) = tab.framework.orchestrator._workflows
        assert tab.btn_cancel.isEnabled() and tab._active_request == rid
        asked = self.answer(monkeypatch, QMessageBox.Yes)
        tab.btn_cancel.click()
        self.finish(tab)
        assert len(asked) == 1 and rid in asked[0][2]
        assert tab.framework.orchestrator.cancelled == [rid]
        assert not tab.btn_cancel.isEnabled() and tab._active_request is None
        assert f"Build {rid} cancelled" in tab.log_area.toPlainText()

    def test_declining_the_confirmation_cancels_nothing(self, tab, monkeypatch):
        from orchestrator.gui.builder_tab import QMessageBox
        tab._submit_build()
        self.answer(monkeypatch, QMessageBox.No)
        tab.btn_cancel.click()
        assert tab.framework.orchestrator.cancelled == [] and tab.btn_cancel.isEnabled()

    def test_a_build_that_ends_disables_cancel(self, tab):
        from orchestrator.Core.orchestrator import OrchestrationStatus
        tab._submit_build()
        (state,) = tab.framework.orchestrator._workflows.values()
        state.status = OrchestrationStatus.COMPLETED
        tab._poll_build()
        assert not tab.btn_cancel.isEnabled() and "ended: completed" in tab.log_area.toPlainText()

    def test_log_shows_markup_as_plain_text(self, tab):
        text = '<b>bold</b> <a href="https://evil.example">click</a> <img src="x.png">'
        tab._log(text)
        assert text in tab.log_area.toPlainText()
        assert not hasattr(tab.log_area, "toHtml"), "the log must be a plain-text widget"


# ---------------------------------------------------------------------------------------------------------------
# 6. scan --output / 11. exit codes on failure
# ---------------------------------------------------------------------------------------------------------------

def board_profile():
    from orchestrator.runtime.profile import parse_profile
    return parse_profile((REPO_ROOT / "tests" / "fixtures" / "hardware_probe" / "schema2_rk3566_dietpi.txt")
                         .read_text(encoding="utf-8"))


def state_dir():
    from orchestrator.Core.config_manager import ConfigManager
    return Path(ConfigManager().user_state_dir)


class TestScan:

    def fake_probe(self, monkeypatch, write=True):
        from orchestrator import cli as cli_module
        calls = []

        def run(cmd, check, cwd):
            calls.append(cmd)
            if write:
                out = Path(cmd[cmd.index("--output") + 1])
                out.write_text("ARCH=aarch64\n")
            return SimpleNamespace(returncode=0)

        monkeypatch.setattr(cli_module, "platform", SimpleNamespace(system=lambda: "Linux"))
        monkeypatch.setattr(cli_module, "subprocess", SimpleNamespace(
            run=run, CalledProcessError=subprocess.CalledProcessError))
        return calls

    def test_output_reaches_the_probe(self, tmp_path, monkeypatch):
        calls = self.fake_probe(monkeypatch)
        monkeypatch.chdir(tmp_path)
        result = invoke_plain("scan", "--script", "--output", "probes/board.txt")
        assert result.code == 0, result.out
        (cmd,) = calls
        assert cmd[-2:] == ["--output", str((tmp_path / "probes" / "board.txt").resolve())]
        assert (tmp_path / "probes" / "board.txt").exists()
        assert not (tmp_path / "target_hardware_config.txt").exists()

    def test_no_output_file_exits_1(self, tmp_path, monkeypatch):
        self.fake_probe(monkeypatch, write=False)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "target_hardware_config.txt").write_text("old run")  # never taken as this run's result
        result = invoke_plain("scan", "--script")
        assert result.code == 1, result.out

    def test_python_probe_is_the_default_and_the_profile_is_stored_as_local(self, tmp_path, monkeypatch):
        from orchestrator import cli as cli_module
        from orchestrator.runtime import hardware
        from orchestrator.runtime.profile import ProfileStore, read_profile
        seen = {}

        def local_profile(**options):
            seen.update(options)
            return board_profile()

        monkeypatch.setattr(hardware, "local_profile", local_profile)
        monkeypatch.setattr(cli_module, "subprocess", None)  # the script is not run
        monkeypatch.chdir(tmp_path)
        result = invoke_plain("scan", "--output", "probes/board.txt")
        assert result.code == 0, result.out
        assert "Python probe" in result.out and "Cortex-A55" in result.out
        written = read_profile(tmp_path / "probes" / "board.txt")
        assert written == board_profile()
        stored = ProfileStore(state_dir()).load_local()
        assert stored == board_profile().stable() and "RAM_AVAILABLE_MB" not in stored
        assert "disk_path" in seen

    def test_json_view(self, tmp_path, monkeypatch):
        from orchestrator.runtime import hardware
        from orchestrator.runtime.profile import parse_profile_json
        monkeypatch.setattr(hardware, "local_profile", lambda **o: board_profile())
        monkeypatch.chdir(tmp_path)
        result = invoke_plain("scan", "--json", "--output", "board.json")
        assert result.code == 0, result.out
        assert parse_profile_json((tmp_path / "board.json").read_text()) == board_profile()

    def test_a_failing_python_probe_falls_back_to_the_script(self, tmp_path, monkeypatch):
        from orchestrator.runtime import hardware
        from orchestrator.runtime.profile import ProfileStore

        def broken(**options):
            raise RuntimeError("probe file missing")

        monkeypatch.setattr(hardware, "local_profile", broken)
        calls = self.fake_probe(monkeypatch)
        monkeypatch.chdir(tmp_path)
        result = invoke_plain("scan")
        assert result.code == 0, result.out
        assert len(calls) == 1 and "fallback script" in result.out and "probe file missing" in result.out
        stored = ProfileStore(state_dir()).load_local()
        assert stored.converted and stored.get("ARCH") == "arm64"  # the fake script wrote a schema 1 line
        assert "run the probe again" in result.out

    def test_an_unreadable_script_result_exits_1(self, tmp_path, monkeypatch):
        from orchestrator import cli as cli_module

        def run(cmd, check, cwd):
            Path(cmd[cmd.index("--output") + 1]).write_text("PROBE_SCHEMA=2\nOS=linux\nOS=linux\n")
            return SimpleNamespace(returncode=0)

        monkeypatch.setattr(cli_module, "platform", SimpleNamespace(system=lambda: "Linux"))
        monkeypatch.setattr(cli_module, "subprocess", SimpleNamespace(
            run=run, CalledProcessError=subprocess.CalledProcessError))
        monkeypatch.chdir(tmp_path)
        result = invoke_plain("scan", "--script")
        assert result.code == 1 and "cannot read" in result.out

    def test_the_real_python_probe(self, tmp_path, monkeypatch):
        from orchestrator.runtime.profile import ProfileStore, read_profile
        monkeypatch.chdir(tmp_path)
        result = invoke_plain("scan")
        assert result.code == 0, result.out
        profile = read_profile(tmp_path / "target_hardware_config.txt")
        assert profile.get("PROBE_TOOL") == "python" and profile.get("PROBE_SCHEMA") == "2"
        assert ProfileStore(state_dir()).load_local() == profile.stable()

    def test_the_probe_script_writes_where_it_is_told(self, tmp_path):
        work, target = tmp_path / "work", tmp_path / "out" / "probe.txt"
        work.mkdir()
        target.parent.mkdir()
        env = dict(os.environ, NON_INTERACTIVE="true")
        result = subprocess.run(["bash", str(REPO_ROOT / "scripts" / "hardware_probe.sh"), "--output", str(target)],
                                cwd=work, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                timeout=120, check=False)
        assert result.returncode == 0, result.stderr
        assert target.read_text().startswith("# LLM Cross-Compiler Framework hardware profile (schema 2")
        from orchestrator.runtime.profile import read_profile
        assert read_profile(target).get("PROBE_TOOL") == "sh"
        assert not (work / "target_hardware_config.txt").exists()

    def test_the_probe_script_refuses_unknown_options(self, tmp_path):
        result = subprocess.run(["bash", str(REPO_ROOT / "scripts" / "hardware_probe.sh"), "--bogus"], cwd=tmp_path,
                                capture_output=True, text=True, timeout=30, check=False)
        assert result.returncode == 2 and "Unknown option" in result.stderr
        assert not (tmp_path / "target_hardware_config.txt").exists()

    @pytest.mark.parametrize("args", [["--output="], ["--output", ""], ["-o"]])
    def test_the_probe_script_refuses_an_empty_output(self, tmp_path, args):
        work = tmp_path / "work"
        work.mkdir()
        result = subprocess.run(["bash", str(REPO_ROOT / "scripts" / "hardware_probe.sh"), *args], cwd=work,
                                capture_output=True, text=True, timeout=30, check=False)
        assert result.returncode == 2 and "Usage" in result.stderr, result.stderr
        assert list(work.iterdir()) == []

    def test_windows_probe_takes_an_output_file(self):
        text = (REPO_ROOT / "scripts" / "hardware_probe.ps1").read_text(encoding="utf-8")
        assert re.search(r"param\(\s*#[^\n]*\n\s*\[string\]\$OutputFile", text)
        assert '-OutputFile `"$OutputFile`"' in text, "the elevated restart must keep the output file"

    def test_windows_probe_waits_for_the_elevated_scan(self):
        # `llm-cli scan` checks the file as soon as the script returns (exit code 1 without it): the
        # non-elevated script must wait for the elevated one and pass on its exit code.
        text = (REPO_ROOT / "scripts" / "hardware_probe.ps1").read_text(encoding="utf-8")
        (line,) = [ln for ln in text.splitlines() if "Start-Process PowerShell -Verb RunAs" in ln]
        assert " -Wait " in line and " -PassThru " in line, line
        assert "Exit $elevated.ExitCode" in line, line


class TestExitCodes:

    def test_deploy_package_failure_exits_1(self, cli, monkeypatch):
        write_manifest(Path(cli.framework.info.installation_path) / "output" / "b", {"model": "m", "primary": "m"})
        assert cli.invoke("deploy", "package", "b", "--profile", "Rockchip").code == 0
        monkeypatch.setattr(FakeDeploymentManager, "create_deployment_package", lambda self, **kw: None)
        result = cli.invoke("deploy", "package", "b", "--profile", "Rockchip")
        assert result.code == 1, result.out
        assert "Package generation failed" in result.out

    def test_secrets_set_failure_exits_1(self, cli, monkeypatch):
        from click.testing import CliRunner

        from orchestrator import cli as cli_module
        monkeypatch.setattr(cli.framework.secrets_manager, "set_secret", lambda key, value: False)

        class Ctx(cli_module.FrameworkContext):
            def require_framework(self):
                self.framework_manager = cli.framework
                return self

        result = CliRunner().invoke(cli_module.cli, ["-q", "secrets", "set", "hf_token", "--with-token"],
                                    input="value-123\n", obj=Ctx())
        assert result.exit_code == 1


# ---------------------------------------------------------------------------------------------------------------
# 7. module create writes into the configured targets folder
# ---------------------------------------------------------------------------------------------------------------

class TestModuleCreate:

    @pytest.fixture
    def generators(self, monkeypatch):
        from orchestrator import cli as cli_module
        seen = []

        class Recorder:
            def __init__(self, targets_dir):
                seen.append(Path(targets_dir))

            def generate_module(self, data):
                return seen[-1] / data["module_name"]

        monkeypatch.setattr(cli_module, "ModuleGenerator", Recorder)
        return seen

    def create(self):
        # base image, description, packages, cpu flags
        return invoke_plain("module", "create", "--name", "MyBoard", "--arch", "aarch64", "--sdk", "None",
                            input="\n\n\n\n")

    def test_absolute_targets_dir_from_config(self, tmp_path, monkeypatch, generators):
        write_user_config(tmp_path, targets_dir=str(tmp_path / "my-targets"))
        monkeypatch.chdir(tmp_path / "home")
        result = self.create()
        assert result.code == 0, result.out
        assert generators == [tmp_path / "my-targets"]

    def test_relative_targets_dir_is_the_installations(self, tmp_path, monkeypatch, generators):
        write_user_config(tmp_path, targets_dir="custom-targets")
        monkeypatch.chdir(tmp_path / "home")
        assert self.create().code == 0
        assert generators == [REPO_ROOT / "custom-targets"]
        assert not generators[0].is_relative_to(tmp_path / "home")


# ---------------------------------------------------------------------------------------------------------------
# 8. config show / 9. config repos --add
# ---------------------------------------------------------------------------------------------------------------

class TestConfigShow:

    def test_effective_settings_with_source_and_redaction(self, tmp_path, monkeypatch):
        token = "hf_" + "abcdefghijklmnopqrstuvwxyzABCDEFGH"
        url = "https://user:" + "FAKEpassword123" + "@git.example.com/llama.cpp.git"
        path = write_user_config(tmp_path, models_dir=str(tmp_path / "models-x"), hf_token=token,
                                 source_repositories={"llama_cpp": {"url": url}})
        monkeypatch.setenv("LLM_FRAMEWORK_OUTPUT_DIR", str(tmp_path / "out-env"))
        result = invoke_plain("config", "show")
        assert result.code == 0, result.out
        data = json.loads(result.stdout)
        assert data["models_dir"] == {"value": str(tmp_path / "models-x"), "source": f"file {path}"}
        assert data["output_dir"] == {"value": str(tmp_path / "out-env"), "source": "environment"}
        assert data["ai_provider"]["source"] == "default"
        assert data["hf_token"]["value"] == "REDACTED"
        assert "FAKEpassword123" not in result.out and token not in result.out
        assert data["source_repositories"]["value"]["llama_cpp"]["url"].startswith("https://user:REDACTED@")

    def test_credential_names_inside_a_setting_are_redacted(self, tmp_path):
        # A value no secret pattern recognizes, below a key that names a credential, at any depth.
        write_user_config(tmp_path, my_service={"user": "bob", "password": "hunter2-plain",
                                                "nested": [{"api_key": "plainkey-123"}]})
        result = invoke_plain("config", "show")
        assert result.code == 0, result.out
        data = json.loads(result.stdout)
        assert data["my_service"]["value"] == {"user": "bob", "password": "REDACTED",
                                               "nested": [{"api_key": "REDACTED"}]}
        assert "hunter2-plain" not in result.out and "plainkey-123" not in result.out

    def test_the_c_file_wins_and_is_named(self, tmp_path):
        other = tmp_path / "elsewhere" / "custom.yml"
        other.parent.mkdir()
        other.write_text("log_level: WARNING\n")
        result = invoke_plain("-c", str(other), "config", "show")
        data = json.loads(result.stdout)
        assert data["log_level"] == {"value": "WARNING", "source": f"file {other}"}


class TestConfigRepos:

    def test_add_stores_the_shape_the_build_engine_reads(self, cli, tmp_path):
        result = cli.invoke("config", "repos", "--add", "LLAMA_CPP", "https://git.example.com/llama.cpp.git")
        assert result.code == 0, result.out
        repos = cli.framework.config.get("source_repositories")
        assert repos == {"llama_cpp": {"url": "https://git.example.com/llama.cpp.git"}}
        saved = yaml.safe_load((Path(cli.framework.config.config_dir) / "config.yml").read_text())
        assert saved["source_repositories"] == repos
        assert "LLAMA_CPP_REPO_OVERRIDE" in result.out

    def test_engine_passes_new_and_old_entries(self, tmp_path, monkeypatch):
        from orchestrator.Core.builder import BuildEngine
        from orchestrator.Core.config_manager import ConfigManager
        no_docker(monkeypatch)
        cfg = ConfigManager(tmp_path / "configs")
        cfg.set("source_repositories", {"llama_cpp": {"url": "https://a.example/l.git"},
                                        "rkllm_toolkit": "https://b.example/r.git",  # stored by older versions
                                        "tiny_models": {"tinyllama": {"url": "https://c.example"}}})
        engine = BuildEngine(cfg, base_dir=tmp_path)
        assert engine._repo_overrides() == {"LLAMA_CPP_REPO_OVERRIDE": "https://a.example/l.git",
                                            "RKLLM_TOOLKIT_REPO_OVERRIDE": "https://b.example/r.git"}

    def test_listing_shows_old_string_entries(self, cli):
        cli.framework.config.set("source_repositories", {"old": "https://old.example/x.git",
                                                         "new": {"url": "https://new.example/y.git"}})
        result = cli.invoke("config", "repos")
        assert result.code == 0 and "https://old.example/x.git" in result.out and "https://new.example/y.git" in result.out


# ---------------------------------------------------------------------------------------------------------------
# 10. targets list / targets validate
# ---------------------------------------------------------------------------------------------------------------

def broken_caps_target(folder):
    caps = json.loads(json.dumps(ROCKCHIP_CAPS))
    caps["default_board"] = "rk9999"  # not one of the boards
    (folder / "Broken").mkdir(parents=True)
    (folder / "Broken" / "target.yml").write_text(yaml.safe_dump(
        {"metadata": {"name": "Broken board", "architecture_family": "aarch64"}, "capabilities": caps}))


class TestTargets:

    @pytest.fixture
    def cli_targets(self, make_framework, tmp_path, monkeypatch):
        folder = tmp_path / "targets_cli"
        broken_caps_target(folder)
        fm = framework_with_targets(make_framework, folder)
        return make_cli(fm, tmp_path, monkeypatch)

    def test_list_shows_ids_boards_and_formats(self, cli_targets):
        result = cli_targets.invoke("targets", "list")
        assert result.code == 0, result.out
        row = next(line for line in result.out.splitlines() if line.strip().startswith("│ Rockchip"))
        assert "Rockchip Family" in row and "rk3588" in row and "rk3566" in row
        assert "gguf, rkllm, rknn" in row
        assert "llm-framework/builder" not in result.out and "targets_cli" not in result.out
        jetson = next(line for line in result.out.splitlines() if "MyJetson" in line)
        assert "no capabilities declared" in jetson

    def test_list_json(self, cli_targets):
        result = cli_targets.invoke("targets", "list", "-f", "json")
        data = {t["id"]: t for t in json.loads(result.out[result.out.index("["):])}
        assert data["Rockchip"]["name"] == "Rockchip Family"
        assert "rk3576" in data["Rockchip"]["boards"]
        assert [f["id"] for f in data["Rockchip"]["formats"]] == ["gguf", "rkllm", "rknn"]
        assert data["MyJetson"]["formats"] == []

    def test_validate_refuses_invalid_capabilities(self, cli_targets):
        result = cli_targets.invoke("targets", "validate", "Broken")
        assert result.code == 1, result.out
        assert "capabilities block is invalid" in result.out and "rk9999" in result.out

    def test_validate_accepts_a_valid_target_by_folder_name_in_any_case(self, cli_targets):
        result = cli_targets.invoke("targets", "validate", "rockchip")
        assert result.code == 0, result.out
        assert "Target 'Rockchip' is valid" in result.out

    def test_validate_a_target_without_capabilities(self, cli_targets):
        result = cli_targets.invoke("targets", "validate", "MyJetson")
        assert result.code == 0 and "declares no capabilities" in result.out

    def test_validate_unknown_target(self, cli_targets):
        assert cli_targets.invoke("targets", "validate", "Nope").code == 1


# ---------------------------------------------------------------------------------------------------------------
# 12. doctor --tier help
# ---------------------------------------------------------------------------------------------------------------

def test_doctor_tier_help_names_only_existing_fixes():
    result = invoke_plain("doctor", "--help")
    text = " ".join(result.out.split())
    assert "folders" not in text
    assert "makes your personal folder private" in text and "Docker build cache" in text
    from orchestrator.Core import doctor
    assert "create a folder" not in doctor.__doc__  # the module's description of the tiers is true as well


# ---------------------------------------------------------------------------------------------------------------
# 13. runtime commands use the configured models_dir
# ---------------------------------------------------------------------------------------------------------------

class TestRuntimeModelsDir:

    def test_configured_models_dir(self, tmp_path):
        from orchestrator import cli as cli_module
        write_user_config(tmp_path, models_dir=str(tmp_path / "big-disk" / "models"))
        rt = cli_module._local_runtime(cli_module.FrameworkContext())
        assert rt.models_dir == tmp_path / "big-disk" / "models" / "ditto"

    def test_same_folder_as_ditto(self, booted_framework, tmp_path):
        from orchestrator import cli as cli_module
        from orchestrator.runtime.llama_server import LocalLLMRuntime
        booted_framework.config.set("models_dir", str(tmp_path / "shared-models"))
        ctx = cli_module.FrameworkContext()
        ctx.framework_manager = booted_framework
        assert cli_module._local_runtime(ctx).models_dir == LocalLLMRuntime.from_framework(booted_framework).models_dir

    def test_environment_still_wins(self, tmp_path, monkeypatch):
        from orchestrator import cli as cli_module
        write_user_config(tmp_path, models_dir=str(tmp_path / "from-file"))
        monkeypatch.setenv("LLM_FRAMEWORK_MODELS_DIR", str(tmp_path / "from-env"))
        assert cli_module._local_runtime(cli_module.FrameworkContext()).models_dir == tmp_path / "from-env" / "ditto"


# ---------------------------------------------------------------------------------------------------------------
# 14. ditto eval --tier stops only a server the eval started
# ---------------------------------------------------------------------------------------------------------------

class FakeRuntime:
    def __init__(self, running_pid=None):
        self.pid = running_pid
        self.started, self.stopped = [], 0

    def endpoint(self):
        return SimpleNamespace(pid=self.pid, model="mine") if self.pid else None

    def start(self, tier=None, remember=True, **kw):
        self.started.append(tier)
        if self.pid is None or tier != "mine":
            self.pid = 1000 + len(self.started)  # a server of the eval's own
        return self.endpoint()

    def stop(self):
        self.stopped += 1
        self.pid = None
        return 1


class TestEvalTiers:

    @pytest.fixture
    def run_eval(self, monkeypatch, tmp_path):
        from orchestrator import cli as cli_module
        from orchestrator.evals import ditto_eval

        class Report:
            def __init__(self):
                self.results, self.pass_rate, self.setup = [SimpleNamespace(seconds=1.0)], 1.0, {}

            def by_kind(self):
                return {"healing": 1.0}

            def retrieval(self):
                return None

        ditto = SimpleNamespace(reload=lambda: None,
                                describe=lambda task: {"label": "Local", "model": "m", "provider": "local"})
        monkeypatch.setattr(ditto_eval, "DittoEval",
                            lambda d, targets_dir: SimpleNamespace(run=lambda cases, **kw: Report()))
        monkeypatch.setattr(cli_module, "_ai_override", lambda ctx, provider, model: ditto)
        framework = SimpleNamespace(config=SimpleNamespace(get=lambda k, d=None: d, set=lambda *a: None),
                                    resolve_path=lambda p: tmp_path / str(p))

        def require(self):
            self.framework_manager = framework
            return self

        monkeypatch.setattr(cli_module.FrameworkContext, "require_framework", require)

        def run(rt, *tiers):
            monkeypatch.setattr(cli_module, "_local_runtime", lambda ctx: rt)
            args = [a for tier in tiers for a in ("--tier", tier)]
            printed = quiet_console(monkeypatch)
            result = invoke_plain("ditto", "eval", "--case", "healing-disk-full", *args)
            return SimpleNamespace(code=result.code, out=printed.getvalue() + result.out)
        return run

    def test_a_server_you_started_keeps_running(self, run_eval):
        rt = FakeRuntime(running_pid=42)
        result = run_eval(rt, "mine")
        assert result.code == 0, result.out
        assert rt.started == ["mine"] and rt.stopped == 0 and rt.pid == 42

    def test_the_eval_stops_its_own_server(self, run_eval):
        rt = FakeRuntime()
        assert run_eval(rt, "small", "small-q2").code == 0
        assert rt.started == ["small", "small-q2"] and rt.stopped == 2 and rt.pid is None

    def test_a_replaced_server_is_reported(self, run_eval):
        rt = FakeRuntime(running_pid=42)
        result = run_eval(rt, "small")
        assert rt.stopped == 1
        assert "was replaced for the evaluation" in result.out


# ---------------------------------------------------------------------------------------------------------------
# 15. A target whose id differs from its folder name
# ---------------------------------------------------------------------------------------------------------------

def acme_target(folder):
    (folder / "VendorBoards").mkdir(parents=True)
    (folder / "VendorBoards" / "target.yml").write_text(yaml.safe_dump(
        {"metadata": {"id": "acme-npu", "name": "ACME NPU boards", "architecture_family": "aarch64"},
         "capabilities": ROCKCHIP_CAPS}))
    return folder / "VendorBoards"


class TestTargetIdVsFolder:

    @pytest.fixture
    def acme(self, make_framework, tmp_path):
        folder = tmp_path / "targets_acme"
        path = acme_target(folder)
        fm = make_framework(initialize=False)
        fm.config.set("targets_dir", str(folder))
        assert fm.initialize()
        return SimpleNamespace(fm=fm, folder=folder, path=path)

    def test_registry_resolves_id_and_folder_name(self, acme):
        tm = acme.fm.target_manager
        for name in ("acme-npu", "ACME-NPU", "VendorBoards", "vendorboards", " VendorBoards "):
            assert tm.resolve(name) == "acme-npu", name
            assert tm.folder_of(name) == acme.path
        assert tm.resolve("Rockchip") is None and tm.folder_of("nope") is None

    def test_the_engine_uses_the_registrys_map(self, acme):
        engine = acme.fm.orchestrator.build_engine
        assert engine.target_manager is acme.fm.target_manager
        assert engine.resolve_target_dir("acme-npu") == acme.path

    def test_standalone_engine_uses_the_same_map(self, tmp_path, monkeypatch):
        from orchestrator.Core.builder import BuildEngine
        from orchestrator.Core.config_manager import ConfigManager
        no_docker(monkeypatch)
        cfg = ConfigManager(tmp_path / "configs")
        path = acme_target(tmp_path / "t")
        (tmp_path / "t" / "Bare").mkdir()  # no target.yml: still found by its folder name, as before
        cfg.set("targets_dir", str(tmp_path / "t"))
        engine = BuildEngine(cfg, base_dir=tmp_path)
        assert engine.resolve_target_dir("acme-npu") == path
        assert engine.resolve_target_dir("vendorboards") == path
        assert engine.resolve_target_dir("bare") == tmp_path / "t" / "Bare"
        assert engine.resolve_target_dir("missing") is None

    def test_engine_builds_from_the_folder_of_the_id(self, tmp_path, monkeypatch):
        from orchestrator.Core.builder import BuildConfiguration, BuildEngine
        from orchestrator.Core.config_manager import ConfigManager
        no_docker(monkeypatch)
        cfg = ConfigManager(tmp_path / "configs")
        path = acme_target(tmp_path / "t")
        cfg.set("targets_dir", str(tmp_path / "t"))
        engine = BuildEngine(cfg, base_dir=tmp_path)
        seen = []

        def record_target(config, progress, target_path):
            seen.append(target_path)
            raise RuntimeError("stop here")

        monkeypatch.setattr(engine, "_record_target", record_target)
        config = BuildConfiguration(build_id="b1", timestamp="t", model_source="org/m", target_arch="acme-npu",
                                    target_format=ModelFormat.GGUF, output_dir=str(tmp_path / "out"))
        engine._builds["b1"] = BuildProgress("b1", BuildStatus.QUEUED, "")
        engine._execute_build(config)
        assert seen == [path]
        assert "stop here" in engine._builds["b1"].errors[-1]

    def test_orchestrator_does_not_skip_the_target(self, acme):
        import asyncio
        from datetime import datetime

        from orchestrator.Core.builder import OptimizationLevel
        from orchestrator.Core.orchestrator import (BuildRequest, OrchestrationStatus, PriorityLevel, WorkflowState,
                                                    WorkflowType)
        orch = acme.fm.orchestrator
        submitted = []
        engine = orch.build_engine
        engine.build_model = lambda config: submitted.append(config) or config.build_id
        engine.get_build_status = lambda bid: SimpleNamespace(status=BuildStatus.COMPLETED, errors=[])
        req = BuildRequest(request_id="r1", workflow_type=WorkflowType.SIMPLE_CONVERSION,
                           priority=PriorityLevel.NORMAL, models=[HF_MODEL], targets=["acme-npu"],
                           target_formats=[ModelFormat.GGUF], optimization_level=OptimizationLevel.BALANCED,
                           quantization_options=["Q4_K_M"], parallel_builds=False,
                           output_base_dir=str(acme.folder.parent / "out"))
        orch._workflows["r1"] = WorkflowState("r1", OrchestrationStatus.PREPARING, datetime.now())
        asyncio.run(orch._run_build_pipeline(req))
        state = orch._workflows["r1"]
        assert state.status == OrchestrationStatus.COMPLETED, (state.errors, state.warnings)
        assert [c.target_arch for c in submitted] == ["acme-npu"] and submitted[0].request_id == "r1"

    @pytest.mark.parametrize("name", ["acme-npu", "VendorBoards", "vendorboards"])
    def test_cli_and_build_service_submit_the_id(self, acme, tmp_path, monkeypatch, name):
        from orchestrator.Core.build_service import BuildService
        from orchestrator.schemas.build import BuildRequestIn
        cli = make_cli(acme.fm, tmp_path, monkeypatch)
        result = cli.invoke("build", "start", "-m", HF_MODEL, "-t", name, "-f", "gguf", "-b", "rk3566")
        assert result.code == 0, result.out
        assert cli.submitted[0].targets == ["acme-npu"]
        acme.fm.orchestrator = FakeOrchestrator()
        req = BuildService(acme.fm).validate(BuildRequestIn(model=HF_MODEL, target=name, format="gguf",
                                                             board="rk3566"))
        assert req.target == "acme-npu"


# ---------------------------------------------------------------------------------------------------------------
# 16. max_concurrent_builds
# ---------------------------------------------------------------------------------------------------------------

class TestMaxConcurrentBuilds:

    def test_the_setting_sets_the_build_slots(self, tmp_path, monkeypatch):
        from orchestrator.Core.builder import BuildEngine
        from orchestrator.Core.config_manager import ConfigManager
        no_docker(monkeypatch)
        cfg = ConfigManager(tmp_path / "configs")
        cfg.set("max_concurrent_builds", 3)
        engine = BuildEngine(cfg, base_dir=tmp_path)
        assert engine.max_concurrent_builds == 3 and engine._executor._max_workers == 3
        assert BuildEngine(cfg, max_concurrent_builds=1, base_dir=tmp_path).max_concurrent_builds == 1

    def test_the_framework_engine_follows_config_yml(self, make_framework, tmp_path):
        write_user_config(tmp_path, max_concurrent_builds=1)
        fm = make_framework()
        assert fm.orchestrator.build_engine.max_concurrent_builds == 1


# ---------------------------------------------------------------------------------------------------------------
# 17. build.sh reads no variable the engine never sets
# ---------------------------------------------------------------------------------------------------------------

def test_build_scripts_do_not_read_imatrix_chunks():
    engine_code = (REPO_ROOT / "orchestrator" / "Core" / "builder.py").read_text(encoding="utf-8")
    assert "IMATRIX_CHUNKS" not in engine_code
    for script in list((REPO_ROOT / "targets").glob("*/modules/*.sh")):
        assert "IMATRIX_CHUNKS" not in script.read_text(encoding="utf-8"), script
    assert "--chunks 100" in (ROCKCHIP_DIR / "modules" / "build.sh").read_text(encoding="utf-8")


def test_changed_shell_scripts_parse():
    for script in (ROCKCHIP_DIR / "modules" / "build.sh", REPO_ROOT / "scripts" / "hardware_probe.sh"):
        assert subprocess.run(["bash", "-n", str(script)], check=False).returncode == 0, script
