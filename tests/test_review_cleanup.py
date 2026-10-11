"""Regression tests of the cleanup track (numbers: the items of the task).

1 one source of settings in the CLI (ConfigManager), an unreadable -c file is reported
3 the local hardware probe writes into the probe folder
4 wait_stopped after a cancel that came before the engine registered the build
5 `module create` hint and <NAME>_REPO_OVERRIDE names
6 failed saves of the settings are reported
7 configured folders instead of the working directory or the installation, Windows start and uninstall scripts
8 FrameworkManager.shutdown ends the background event loop
"""

import asyncio
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT
from orchestrator.Core.builder import BuildStatus
from test_cli_builds import invoke_plain, quiet_console, write_user_config
from test_review_capabilities_paths import make_cli, no_docker


@pytest.fixture
def cli(booted_framework, tmp_path, monkeypatch):
    return make_cli(booted_framework, tmp_path, monkeypatch)


# ---------------------------------------------------------------------------------------------------------------
# 1. The CLI reads every setting through ConfigManager
# ---------------------------------------------------------------------------------------------------------------

def test_the_cli_keeps_no_second_copy_of_the_settings():
    from orchestrator import cli as cli_module
    assert not hasattr(cli_module, "DEFAULT_CONFIG")
    assert not hasattr(cli_module, "load_config_file")
    assert not hasattr(cli_module.FrameworkContext(), "config")


def test_an_unreadable_c_file_is_reported_and_the_other_settings_still_apply(tmp_path):
    broken = tmp_path / "broken.yml"
    broken.write_text("log_level: [unclosed\n  - x: {\n")
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT), LLM_FRAMEWORK_LOG_LEVEL="WARNING")
    result = subprocess.run([sys.executable, "-m", "orchestrator.cli", "-q", "-c", str(broken), "config", "show"],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert "Failed to load config file" in result.stderr and "broken.yml" in result.stderr
    import json
    settings = json.loads(result.stdout)
    # Before, the broken file stopped the loading: the LLM_FRAMEWORK_* variables were dropped as well.
    assert settings["log_level"] == {"value": "WARNING", "source": "environment"}


# ---------------------------------------------------------------------------------------------------------------
# 3. Local hardware probe
# ---------------------------------------------------------------------------------------------------------------

class TestLocalProbe:

    @pytest.fixture
    def fake_probe(self, monkeypatch):
        from orchestrator.gui import builder_tab
        calls = []

        def run(cmd, **kwargs):
            calls.append(cmd)
            flag = "--output" if "--output" in cmd else "-OutputFile"
            Path(cmd[cmd.index(flag) + 1]).write_text("CPU_MODEL=Rockchip RK3588\nARCH=aarch64\n")

        monkeypatch.setattr(builder_tab, "subprocess", SimpleNamespace(run=run))
        return calls

    @pytest.mark.parametrize("system, flag", [("Linux", "--output"), ("Windows", "-OutputFile")])
    def test_the_worker_tells_the_probe_where_to_write(self, qapp, tmp_path, monkeypatch, fake_probe, system, flag):
        from orchestrator.gui import builder_tab
        monkeypatch.setattr(builder_tab, "platform", SimpleNamespace(system=lambda: system))
        work = tmp_path / "work"
        work.mkdir()
        monkeypatch.chdir(work)
        script = tmp_path / "hardware_probe.sh"
        script.write_text("#!/bin/bash\n")
        output = tmp_path / "cache" / "probes" / builder_tab.LOCAL_PROBE_FILE
        output.parent.mkdir(parents=True)
        output.write_text("an earlier run")
        results = []
        worker = builder_tab.LocalProbeWorker(script, output)
        worker.finished.connect(lambda ok, message, path: results.append((ok, path)))
        worker.run()
        assert results == [(True, str(output))]
        (cmd,) = fake_probe
        assert cmd[-2:] == [flag, str(output)]
        assert output.read_text().startswith("CPU_MODEL=")
        assert not (work / "target_hardware_config.txt").exists(), "nothing goes into the working directory"

    def test_the_builder_tab_uses_the_configured_probe_folder(self, qapp, booted_framework, tmp_path, monkeypatch):
        from orchestrator.gui import builder_tab
        from test_foundation import FakeOrchestrator
        booted_framework.orchestrator = FakeOrchestrator()
        (tmp_path / "work").mkdir()
        monkeypatch.chdir(tmp_path / "work")
        started = []

        class Worker:
            def __init__(self, script, output):
                started.append(Path(output))
                self.finished = SimpleNamespace(connect=lambda slot: None)

            def start(self):
                pass

        monkeypatch.setattr(builder_tab, "LocalProbeWorker", Worker)
        tab = builder_tab.BuilderTab(booted_framework)
        try:
            tab._run_local_probe()
        finally:
            tab._build_timer.stop()
        cache = booted_framework.resolve_path(booted_framework.config.get("cache_dir"))
        assert started == [cache / "probes" / builder_tab.LOCAL_PROBE_FILE]
        assert started[0].is_absolute() and not started[0].is_relative_to(Path.cwd())

    def test_the_module_wizard_fills_in_from_the_local_probe(self, qapp, tmp_path):
        from orchestrator.gui import builder_tab
        from orchestrator.gui.wizards import ModuleCreationWizard
        framework = SimpleNamespace(
            config=SimpleNamespace(get=lambda key, default=None: str(tmp_path / "cache") if key == "cache_dir"
                                   else default),
            resolve_path=Path)
        probe = builder_tab.probe_result_dir(framework) / builder_tab.LOCAL_PROBE_FILE
        probe.parent.mkdir(parents=True)
        probe.write_text("CPU_MODEL=Rockchip RK3588\nARCH=aarch64\n")
        wizard = ModuleCreationWizard(tmp_path / "targets")
        try:
            wizard.framework_manager = framework
            wizard.detect_cb.setChecked(True)
            assert "loaded" in wizard.status_lbl.text()
            assert wizard.name_edit.text()
        finally:
            wizard.deleteLater()


# ---------------------------------------------------------------------------------------------------------------
# 4. wait_stopped: a cancel before the engine registered the build
# ---------------------------------------------------------------------------------------------------------------

class LateEngine:
    """build_model still runs in a worker thread: the request knows the build id, the engine does not yet."""

    def __init__(self):
        self._status = {}
        self.stopped_at = None

    def get_build_status(self, build_id):
        status = self._status.get(build_id)
        return None if status is None else SimpleNamespace(status=status, errors=[], logs=[])

    def is_active(self, build_id):
        return self._status.get(build_id) == BuildStatus.BUILDING

    def register(self, build_id):
        self._status[build_id] = BuildStatus.BUILDING

    def stop(self, build_id):
        self.stopped_at = time.monotonic()
        self._status[build_id] = BuildStatus.CANCELLED


def late_service(engine):
    from orchestrator.Core.build_service import BuildService
    from orchestrator.Core.orchestrator import OrchestrationStatus, WorkflowState
    state = WorkflowState("r1", OrchestrationStatus.CANCELLED, datetime.now(), build_ids=["b1"])
    return BuildService(SimpleNamespace(orchestrator=SimpleNamespace(build_engine=engine, _workflows={"r1": state})))


class TestWaitStoppedBeforeRegistration:

    def test_waits_until_the_late_build_was_registered_and_stopped(self):
        engine = LateEngine()
        service = late_service(engine)
        timers = [threading.Timer(0.2, engine.register, args=("b1",)), threading.Timer(0.5, engine.stop, args=("b1",))]
        for timer in timers:
            timer.start()
        try:
            assert service.wait_stopped("r1", 5, poll=0.01) == "cancelled"
            assert engine.stopped_at is not None, "reported cancelled while the build was still starting"
            assert time.monotonic() >= engine.stopped_at
        finally:
            for timer in timers:
                timer.cancel()
                timer.join()

    def test_a_build_that_never_reaches_the_engine_counts_as_running_only_briefly(self, monkeypatch):
        from orchestrator.Core.build_service import BuildService
        monkeypatch.setattr(BuildService, "UNREGISTERED_GRACE", 0.3)
        service = late_service(LateEngine())  # e.g. cancelled while it waited for a free build slot
        assert service.wait_stopped("r1", 0.05, poll=0.01) == "running"
        start = time.monotonic()
        assert service.wait_stopped("r1", 5, poll=0.01) == "cancelled"
        assert 0.3 <= time.monotonic() - start < 3


# ---------------------------------------------------------------------------------------------------------------
# 5. module create hint, <NAME>_REPO_OVERRIDE
# ---------------------------------------------------------------------------------------------------------------

class TestRepoOverrideNames:

    @pytest.mark.parametrize("name", ["llama.cpp", "llama_cpp", "LLAMA_CPP", "llama-cpp", "llama cpp"])
    def test_spellings_of_llama_cpp(self, name):
        from orchestrator.Core.builder import repo_override_names
        assert repo_override_names(name)[0] == "LLAMA_CPP_REPO_OVERRIDE"

    def test_a_dotted_prefix_keeps_the_name_of_earlier_versions(self):
        from orchestrator.Core.builder import repo_override_names
        assert repo_override_names("sources.llama_cpp") == ["SOURCES_LLAMA_CPP_REPO_OVERRIDE",
                                                            "LLAMA_CPP_REPO_OVERRIDE"]

    def test_the_variables_the_target_scripts_read_come_from_their_natural_names(self):
        from orchestrator.Core.builder import repo_override_names
        read = set()
        for script in (REPO_ROOT / "targets").glob("*/modules/*.sh"):
            read |= set(re.findall(r"\$\{(\w+)_REPO_OVERRIDE", script.read_text(encoding="utf-8")))
        assert {"LLAMA_CPP", "RKLLM_TOOLKIT"} <= read
        for base in read:
            for name in (base.lower(), base.lower().replace("_", "."), base.lower().replace("_", "-")):
                assert repo_override_names(name)[0] == f"{base}_REPO_OVERRIDE", name

    def test_the_engine_passes_llama_dot_cpp_under_the_name_the_scripts_read(self, tmp_path, monkeypatch):
        from orchestrator.Core.builder import BuildEngine
        from orchestrator.Core.config_manager import ConfigManager
        no_docker(monkeypatch)
        cfg = ConfigManager(tmp_path / "configs")
        cfg.set("source_repositories", {"llama.cpp": {"url": "https://a.example/l.git"},
                                        "repos.rkllm_toolkit": "https://b.example/r.git",
                                        "rkllm_toolkit": {"url": "https://c.example/r.git"}})
        env = BuildEngine(cfg, base_dir=tmp_path)._repo_overrides()
        assert env["LLAMA_CPP_REPO_OVERRIDE"] == "https://a.example/l.git"
        assert env["CPP_REPO_OVERRIDE"] == "https://a.example/l.git"  # the name of earlier versions
        assert env["REPOS_RKLLM_TOOLKIT_REPO_OVERRIDE"] == "https://b.example/r.git"
        # The whole name of an entry wins over the same name derived from another entry's last part.
        assert env["RKLLM_TOOLKIT_REPO_OVERRIDE"] == "https://c.example/r.git"

    def test_config_repos_names_the_variable(self, cli):
        result = cli.invoke("config", "repos", "--add", "llama.cpp", "https://git.example.com/llama.cpp.git")
        assert result.code == 0, result.out
        assert "LLAMA_CPP_REPO_OVERRIDE" in result.out

    @pytest.mark.parametrize("name", ["...", "-", "äöü"])
    def test_config_repos_refuses_a_name_that_gives_no_variable(self, cli, name):
        # Before, the entry was saved and the command then crashed (IndexError) while printing the variable name.
        before = dict(cli.framework.config.get("source_repositories", {}) or {})
        result = cli.invoke("config", "repos", "--add", name, "https://git.example.com/x.git")
        assert result.code == 2, result.out
        assert result.exception is None or isinstance(result.exception, SystemExit), repr(result.exception)
        assert "letters" in result.out and "digits" in result.out
        assert dict(cli.framework.config.get("source_repositories", {}) or {}) == before


def test_module_create_names_the_file_the_build_runs(tmp_path, monkeypatch):
    write_user_config(tmp_path, targets_dir=str(tmp_path / "targets"))
    printed = quiet_console(monkeypatch)
    result = invoke_plain("module", "create", "--name", "MyBoard", "--arch", "aarch64", "--sdk", "None",
                          input="\n\n\n\n")
    out = printed.getvalue() + result.out
    assert result.code == 0, out
    assert "config_module.sh" not in out
    hint = re.search(r"2\. Customize (\S+)", out).group(1)
    assert hint.endswith("modules/build.sh") and Path(hint).is_file()


# ---------------------------------------------------------------------------------------------------------------
# 6. A failed save of the settings is reported
# ---------------------------------------------------------------------------------------------------------------

class TestFailedSaves:

    def test_config_repos_add_exits_1_when_nothing_was_saved(self, cli, monkeypatch):
        monkeypatch.setattr(cli.framework.config, "save_user_config", lambda: False)
        result = cli.invoke("config", "repos", "--add", "llama_cpp", "https://git.example.com/l.git")
        assert result.code == 1, result.out
        assert "Not saved" in result.out and "Added" not in result.out

    def test_config_repos_remove_exits_1_when_nothing_was_saved(self, cli, monkeypatch):
        cli.framework.config.set("source_repositories", {"old": {"url": "https://old.example/x.git"}})
        monkeypatch.setattr(cli.framework.config, "save_user_config", lambda: False)
        result = cli.invoke("config", "repos", "--remove", "old")
        assert result.code == 1, result.out
        assert "Not saved" in result.out and "Removed" not in result.out

    @pytest.mark.parametrize("saved", [True, False])
    def test_the_audio_switch_warns_when_the_setting_was_not_saved(self, monkeypatch, saved):
        from orchestrator.gui import main_window
        from orchestrator.utils.localization import get_instance
        shown, values = [], {}
        monkeypatch.setattr(main_window.QMessageBox, "warning",
                            lambda parent, title, text: shown.append((title, text)))
        config = SimpleNamespace(set=lambda key, value: values.__setitem__(key, value) or True,
                                 save_user_config=lambda: saved)
        window = SimpleNamespace(framework=SimpleNamespace(config=config), i18n=get_instance())
        main_window.MainOrchestrator._set_audio_announcements(window, False)
        assert values == {"audio_announcements": False}
        if saved:
            assert shown == []
        else:
            assert len(shown) == 1 and "config.yml" in shown[0][1]


# ---------------------------------------------------------------------------------------------------------------
# 7. Configured folders; Windows start and uninstall scripts
# ---------------------------------------------------------------------------------------------------------------

def test_the_deployment_package_goes_to_the_configured_output_folder(qapp, tmp_path):
    from orchestrator.gui.deployment_window import DeploymentWorker
    install, seen = tmp_path / "install", {}

    class Deployment:
        def create_deployment_package(self, artifact_path, profile_name, docker_config, output_dir):
            seen["output_dir"] = output_dir
            return None  # stops after packaging

    framework = SimpleNamespace(config=SimpleNamespace(get=lambda key, default=None: default),
                                resolve_path=lambda value: install / value,
                                get_component=lambda name: Deployment())
    results = []
    worker = DeploymentWorker(framework, tmp_path / "model.gguf", "Rockchip", {}, "10.0.0.2", "root", None)
    worker.finished.connect(lambda ok, message: results.append(ok))
    worker.run()
    assert seen["output_dir"] == install / "output" and results == [False]


class TestRuntimeDefaultModelsDir:

    def test_without_models_dir_the_configured_folder_is_used(self, tmp_path):
        from orchestrator.runtime.llama_server import LocalLLMRuntime
        write_user_config(tmp_path, models_dir=str(tmp_path / "my-models"))
        runtime = LocalLLMRuntime(data_dir=tmp_path / "state")
        assert runtime.models_dir == tmp_path / "my-models" / "ditto"

    def test_the_same_folder_as_from_framework(self, tmp_path):
        from orchestrator.Core.config_manager import ConfigManager
        from orchestrator.Core.framework import resolve_install_path
        from orchestrator.runtime.llama_server import LocalLLMRuntime
        write_user_config(tmp_path, models_dir="relative-models")
        framework = SimpleNamespace(config=ConfigManager(), resolve_path=resolve_install_path)
        expected = LocalLLMRuntime.from_framework(framework).models_dir
        assert LocalLLMRuntime(data_dir=tmp_path / "state").models_dir == expected == \
            REPO_ROOT / "relative-models" / "ditto"


SCRIPTS = REPO_ROOT / "scripts"


class TestWindowsScripts:

    def test_the_start_script_finds_the_framework_folder(self):
        text = (SCRIPTS / "start-llm_convertion_framework.bat").read_text(encoding="utf-8")
        # It lies in scripts\ (the updater, still in development, copies it into the framework folder): both work.
        assert 'if not exist "orchestrator\\main.py" if exist "..\\orchestrator\\main.py" cd ..' in text
        assert 'set "PYTHONPATH=%FRAMEWORK_ROOT%"' in text and 'set "PYTHONPATH=%~dp0"' not in text
        started = re.search(r'"%PYTHON_EXE%" (\S+)', text).group(1)
        assert (REPO_ROOT / started.replace("\\", "/")).is_file()
        # Files that are not shipped (planned updater, portable Python) are used only when present.
        for optional in ("updater.bat", "updater.sh", "python_embed\\python.exe"):
            assert f'if exist "{optional}"' in text, optional

    def test_the_uninstaller_reads_the_marker_install_bat_writes(self):
        uninstall = (SCRIPTS / "Uninstall-LLM-Conversion-Framework.bat").read_text(encoding="utf-8")
        install = (REPO_ROOT / "install.bat").read_text(encoding="utf-8")
        marker = re.search(r'SET "MARKER_FILE=([^"]+)"', uninstall).group(1)
        assert f'Join-Path $ScriptPath "{marker}"' in install and "-Value $InstallDir" in install
        # The marker lies next to install.bat, one level above the uninstaller in scripts\.
        assert (SCRIPTS / ".." / "install.bat").is_file()
        assert 'IF NOT EXIST "install.bat" IF EXIST "..\\install.bat" cd ..' in uninstall
        assert (uninstall.index('IF EXIST "..\\install.bat" cd ..') < uninstall.index('SET "SOURCE_REPO=%CD%"')
                < uninstall.index('IF NOT EXIST "%MARKER_FILE%"'))
        # The shortcut it removes is the one install.bat creates.
        shortcut = re.search(r'SET "SHORTCUT_NAME=([^"]+)"', uninstall).group(1)
        app = re.search(r'\$AppName = "([^"]+)"', install).group(1)
        assert shortcut == f"{app}.lnk" and '"$AppName.lnk"' in install

    def test_install_bat_finds_its_folder_without_a_script_path(self):
        # Its PowerShell part runs through Invoke-Expression, where $MyInvocation.MyCommand.Path is empty: the folder
        # it was started in (the batch part changes into it first) is the source folder then.
        install = (REPO_ROOT / "install.bat").read_text(encoding="utf-8")
        assert 'cd /d "%~dp0"' in install.split("powershell")[0]
        line = next(x for x in install.splitlines() if x.startswith("$ScriptPath ="))
        assert "if ($MyInvocation.MyCommand.Path)" in line and "(Get-Location).Path" in line

    def test_the_marker_and_the_backups_stay_out_of_git(self):
        for path in (".install_complete", "recovered_data/backup/config.yml"):
            ignored = subprocess.run(["git", "-C", str(REPO_ROOT), "check-ignore", "-q", path])
            assert ignored.returncode == 0, path


# ---------------------------------------------------------------------------------------------------------------
# 8. shutdown() ends the background event loop
# ---------------------------------------------------------------------------------------------------------------

def test_shutdown_ends_the_background_event_loop(make_framework):
    fm = make_framework()

    async def answer():
        return 42

    assert fm.run_coroutine(answer(), timeout=10) == 42
    loop, thread = fm._loop, fm._loop_thread
    pending = asyncio.run_coroutine_threadsafe(asyncio.sleep(3600), loop)  # e.g. a request still being followed
    fm.shutdown()
    assert not thread.is_alive() and loop.is_closed()
    assert pending.cancelled()
    fm.shutdown()  # a second call (the fixture's) changes nothing


def test_git_variables_of_a_calling_git_command_do_not_reach_the_tests(tmp_path):
    # Run under `git rebase --exec`, the secret-scan tests' `git init` acted on this repository (core.bare = true).
    import conftest
    env = {"GIT_DIR": "/outer/.git", "GIT_WORK_TREE": "/outer", "GIT_INDEX_FILE": "/outer/.git/index", "HOME": "/h"}
    conftest.scrub_git_environment(env)
    assert env == {"HOME": "/h"}
    assert not set(conftest.GIT_REPOSITORY_VARIABLES) & set(os.environ)
    subprocess.run(["git", "init", "-q", str(tmp_path / "repo")], check=True)
    assert (tmp_path / "repo" / ".git").is_dir()


def test_the_session_end_cleanup_is_not_stopped_by_a_dead_weak_proxy():
    # isinstance() reads __class__, which raises ReferenceError for a weakref.proxy whose object is gone: one such
    # proxy among the collected objects stopped pytest_unconfigure before it ended the loops and the widgets.
    import weakref

    import conftest
    from orchestrator.Core.framework import FrameworkManager

    class Gone:
        pass

    gone = Gone()
    proxy = weakref.proxy(gone)
    del gone
    fm = FrameworkManager.__new__(FrameworkManager)  # not booted; only its loop field matters here
    fm._loop = "a running loop"
    try:
        left = conftest.frameworks_left_running(FrameworkManager)
        assert any(obj is fm for obj in left)
        assert proxy is not None  # alive (and dead) during the call
    finally:
        fm._loop = None  # nothing for the real session end to stop
