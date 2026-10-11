"""Regression tests of the cleanup track (numbers: the items of the task).

1 one source of settings in the CLI (ConfigManager), an unreadable -c file is reported
8 FrameworkManager.shutdown ends the background event loop
"""

import asyncio
import os
import subprocess
import sys

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
