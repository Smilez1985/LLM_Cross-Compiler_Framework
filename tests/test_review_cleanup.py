"""Regression tests of the cleanup track (numbers: the items of the task).

1 one source of settings in the CLI (ConfigManager), an unreadable -c file is reported
3 the local hardware probe writes into the probe folder
8 FrameworkManager.shutdown ends the background event loop
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT
from test_review_capabilities_paths import make_cli


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
