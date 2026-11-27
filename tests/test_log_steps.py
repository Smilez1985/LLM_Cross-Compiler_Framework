"""
Steps 6 and 7 of "One log the person and Ditto read" (DESIGN, the row of the one log; designed on 2026-11-27).

6: log_exceptions, log_performance and log_step mark where an error happened (logged once, with its trace, part and
   step, always raised on) and how long a step took (a PERFORMANCE entry with step and duration_ms), at one place each:
   the tool registry's handler call, the build engine's steps and Mojo's checks.
7: the levels per part come from the person's setting log_levels (one writer, one place that applies them), set with
   `llmcf logs level` and in Help > Framework Log...; an AI reads them, never changes them.
"""

import asyncio
import json
import logging
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from orchestrator.utils import logging as fw_logging
from orchestrator.utils.logging import (LOG_LEVELS_SETTING, LogLevel, apply_part_levels, get_logger, log_exceptions,
                                        log_performance, log_step, part_level_for, part_levels_of, set_part_level)

PERFORMANCE = LogLevel.PERFORMANCE.value


@pytest.fixture
def records(caplog):
    caplog.set_level(1)
    yield caplog   # caplog.records is the list of the phase that reads it


@pytest.fixture(autouse=True)
def no_part_levels():
    apply_part_levels({})
    yield
    apply_part_levels({})


def steps(records, level=None):
    return [r for r in records.records if getattr(r, "step", None) and (level is None or r.levelno == level)]


class TestLogStep:

    def test_a_steps_duration_is_one_performance_entry_with_step_and_time(self, records):
        with log_step("PartA", "step one"):
            pass
        [entry] = steps(records, PERFORMANCE)
        assert entry.name == "PartA" and entry.step == "step one" and entry.duration_ms >= 0
        assert entry.getMessage().startswith("step one took ")

    def test_a_step_faster_than_its_threshold_writes_nothing(self, records):
        with log_step("PartA", "quick", threshold_ms=60_000):
            pass
        assert steps(records) == []

    def test_an_error_is_logged_once_with_its_trace_and_always_raised_on(self, records):
        with pytest.raises(KeyError):
            with log_step("Outer", "outer step", threshold_ms=60_000):
                with log_step("Inner", "inner step", threshold_ms=60_000):
                    raise KeyError("boom")
        [error] = steps(records, logging.ERROR)
        assert error.name == "Inner" and error.step == "inner step" and error.exc_info[0] is KeyError

    def test_without_errors_only_the_time_is_written(self, records):
        with pytest.raises(ValueError):
            with log_step("PartA", "counted", errors=False):
                raise ValueError("x")
        assert steps(records, logging.ERROR) == []
        [entry] = steps(records, PERFORMANCE)
        assert entry.getMessage().endswith("and failed")


class TestDecorators:

    def test_plain_and_async_functions_keep_their_names_and_results(self, records):
        @log_performance("PartB", threshold_ms=0, step="plain")
        def plain(x):
            """doc"""
            return x + 1

        @log_performance("PartB", threshold_ms=0, step="async")
        async def later(x):
            return x * 2

        assert plain(1) == 2 and plain.__name__ == "plain" and plain.__doc__ == "doc"
        assert asyncio.run(later(3)) == 6 and later.__name__ == "later"
        assert [r.step for r in steps(records, PERFORMANCE)] == ["plain", "async"]

    def test_log_exceptions_logs_and_raises_log_performance_only_times(self, records):
        @log_exceptions("PartC")
        def broken():
            raise RuntimeError("no")

        @log_performance("PartC", threshold_ms=0)
        def timed():
            raise RuntimeError("no")

        for function in (broken, timed):
            with pytest.raises(RuntimeError):
                function()
        errors = steps(records, logging.ERROR)
        assert [e.step for e in errors] == [broken.__qualname__]
        assert errors[0].name == "PartC"

    def test_a_decorator_without_a_part_uses_the_functions_module(self, records):
        @log_exceptions()
        def broken():
            raise RuntimeError("no")

        with pytest.raises(RuntimeError):
            broken()
        assert steps(records, logging.ERROR)[0].name == __name__


class TestReader:

    def test_a_steps_entry_is_shown_at_the_default_level(self, tmp_path):
        from orchestrator.utils.framework_log import read_entries
        lines = [{"timestamp": "2026-11-27T10:00:00", "level": "PERFORMANCE", "logger_name": "BuildEngine",
                  "message": "build step image took 5 ms"},
                 {"timestamp": "2026-11-27T10:00:01", "level": "TRACE", "logger_name": "x", "message": "t"}]
        (tmp_path / "framework.log").write_text("\n".join(json.dumps(e) for e in lines) + "\n", encoding="utf-8")
        assert [e["level"] for e in read_entries(tmp_path, level="info")] == ["PERFORMANCE"]
        assert len(read_entries(tmp_path, level="debug")) == 2
        assert read_entries(tmp_path, level="warning") == []


class In(BaseModel):
    text: str = ""


class Out(BaseModel):
    text: str = ""


class TestThePlaces:

    def test_a_slow_tool_call_gets_its_duration_and_a_failure_is_logged_once(self, tmp_path, records, monkeypatch):
        from orchestrator.tools import registry as registry_module
        from orchestrator.tools.audit import AuditLog
        from orchestrator.tools.policy import PolicyStore, ToolTier
        from orchestrator.tools.registry import ToolContext, ToolError, ToolRegistry, ToolSpec
        monkeypatch.setattr(registry_module, "SLOW_CALL_MS", 0)
        reg = ToolRegistry(PolicyStore(tmp_path), AuditLog(tmp_path / "audit.db"))

        def handler(inp):
            if inp.text == "fail":
                raise RuntimeError("broken")
            return Out(text="ok")
        reg.register(ToolSpec("probe", "Probe", "", ToolTier.READ, In, Out, handler))
        reg.call("probe", {}, ToolContext("mcp"))
        with pytest.raises(ToolError):
            reg.call("probe", {"text": "fail"}, ToolContext("mcp"))
        timed = steps(records, PERFORMANCE)
        assert [r.step for r in timed] == ["tool probe", "tool probe"] and timed[0].name == "ToolRegistry"
        failures = [r for r in records.records if r.levelno == logging.ERROR and "probe" in r.getMessage()]
        assert len(failures) == 1 and failures[0].exc_info is not None

    def test_every_build_step_is_timed(self):
        from orchestrator.Core.builder import BUILD_STEP_MS, BuildEngine
        assert BUILD_STEP_MS == 0
        for method in ("_prepare_build_environment", "_generate_dockerfile", "_build_docker_image", "_stage_model",
                       "_scan_image_security", "_generate_imatrix", "_execute_build_modules", "_execute_tuning",
                       "_extract_artifacts", "_create_golden_artifact", "_publish_artifacts"):
            assert hasattr(getattr(BuildEngine, method), "__wrapped__"), method

    def test_a_build_step_writes_its_time_and_a_failed_build_names_its_step(self, records):
        from orchestrator.Core.builder import BuildEngine

        class Engine(BuildEngine):
            def __init__(self):
                pass
        with pytest.raises(AttributeError):
            Engine()._prepare_build_environment(SimpleNamespace(), SimpleNamespace(), None)
        [entry] = steps(records, PERFORMANCE)
        assert entry.step == "build step environment" and entry.name == "BuildEngine"
        assert entry.getMessage().endswith("and failed")

    def test_a_crashing_mojo_check_is_logged_before_it_is_its_finding(self, records, monkeypatch):
        from orchestrator.Core.mojo import runner

        def check_boom(framework):
            raise RuntimeError("the check broke")

        def check_slow(framework):
            return []
        monkeypatch.setattr(runner, "MOJO_CHECK_MS", 0)
        report = runner.Mojo(SimpleNamespace(), checks=[check_boom, check_slow]).run()
        assert [f.code for f in report.findings] == ["boom.check_crashed"]
        [error] = steps(records, logging.ERROR)
        assert error.step == "mojo check boom" and error.name == "Mojo" and error.exc_info[0] is RuntimeError
        assert [r.step for r in steps(records, PERFORMANCE)] == ["mojo check boom", "mojo check slow"]


class FakeConfig:
    def __init__(self, saves=True):
        self.values, self.saves, self.saved = {}, saves, 0

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value
        return True

    def save_user_config(self):
        self.saved += 1
        return self.saves


class TestPartLevels:

    def test_only_valid_parts_and_levels_are_kept(self):
        assert part_levels_of({"BuildEngine": "debug", "orchestrator.Core": "WARNING", "bad part": "INFO",
                               "x": "LOUD", "../etc": "INFO", "": "INFO"}) == {
            "BuildEngine": "DEBUG", "orchestrator.Core": "WARNING"}
        assert part_levels_of("BuildEngine=DEBUG") == {}

    def test_the_most_specific_part_wins(self):
        levels = {"orchestrator": "ERROR", "orchestrator.Core": "DEBUG"}
        assert part_level_for("orchestrator.Core.builder", levels) == "DEBUG"
        assert part_level_for("orchestrator.tools.registry", levels) == "ERROR"
        assert part_level_for("orchestratorX", levels) is None
        assert part_level_for("BuildEngine", levels) is None

    def test_a_part_gets_its_level_and_gets_its_own_back(self):
        logger = get_logger("PartLevelProbe.child")
        own = logger.level
        apply_part_levels({"PartLevelProbe": "DEBUG"})
        assert logger.level == logging.DEBUG
        assert get_logger("PartLevelProbe.later").level == logging.DEBUG
        apply_part_levels({})
        assert logger.level == own

    def test_framework_log_takes_the_lowest_level_asked_for(self):
        from orchestrator.utils.framework_log import FRAMEWORK_LOG_HANDLER
        handler = logging.Handler(logging.INFO)
        handler.set_name(FRAMEWORK_LOG_HANDLER)
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            apply_part_levels({"BuildEngine": "DEBUG"})
            assert handler.level == logging.DEBUG and root.level <= logging.DEBUG
            apply_part_levels({})
            assert handler.level == logging.INFO
        finally:
            root.removeHandler(handler)

    def test_the_global_level_keeps_the_parts(self):
        manager = fw_logging._logging_manager
        apply_part_levels({"PartKeep": "ERROR"})
        manager.set_global_level(LogLevel.INFO)
        assert get_logger("PartKeep").level == logging.ERROR

    def test_the_one_writer_checks_saves_and_applies(self):
        config = FakeConfig()
        assert set_part_level(config, "PartWriter", "debug") == {"PartWriter": "DEBUG"}
        assert config.values[LOG_LEVELS_SETTING] == {"PartWriter": "DEBUG"} and config.saved == 1
        assert get_logger("PartWriter").level == logging.DEBUG
        assert set_part_level(config, "PartWriter", None) == {}
        for part, level in (("bad part", "INFO"), ("PartWriter", "LOUD")):
            with pytest.raises(ValueError):
                set_part_level(config, part, level)
        assert config.values[LOG_LEVELS_SETTING] == {}

    def test_a_level_that_cannot_be_saved_stays_as_it_was(self):
        config = FakeConfig(saves=False)
        config.values[LOG_LEVELS_SETTING] = {"PartSaved": "ERROR"}
        with pytest.raises(OSError):
            set_part_level(config, "PartOther", "DEBUG")
        assert config.values[LOG_LEVELS_SETTING] == {"PartSaved": "ERROR"}

    def test_an_ai_reads_the_levels_and_never_changes_them(self):
        from orchestrator.tools import tools_status
        assert "log_levels" in tools_status.READ_ONLY and "log_levels" not in tools_status.WRITABLE
        framework = SimpleNamespace(config=SimpleNamespace(get=lambda key, default=None: {
            "BuildEngine": "DEBUG", "bad part": "INFO"} if key == "log_levels" else default))
        assert tools_status.setting_value(framework, "log_levels") == ["BuildEngine: DEBUG"]

    def test_the_setting_is_a_setting_of_the_person(self, tmp_path):
        from orchestrator.Core.config_manager import ConfigManager
        config = ConfigManager(tmp_path / "configs")
        assert config.get(LOG_LEVELS_SETTING) == {}


class TestCommandLine:

    @pytest.fixture
    def run(self, tmp_path, monkeypatch):
        from click.testing import CliRunner
        from orchestrator import cli
        config = FakeConfig()
        monkeypatch.setattr(cli, "_settings", lambda ctx: (config, None))

        def run(*args):
            return CliRunner().invoke(cli.cli, ["logs", *args])
        run.config = config
        return run

    def test_level_levels_and_reset(self, run):
        assert "No part has its own log level" in run("levels").output
        done = run("level", "BuildEngine", "debug")
        assert done.exit_code == 0 and "BuildEngine: DEBUG" in done.output
        listed = run("levels", "--json").output
        assert json.loads(listed[listed.index("{"):]) == {"BuildEngine": "DEBUG"}
        assert run("level", "BuildEngine", "--reset").exit_code == 0
        assert run.config.values[LOG_LEVELS_SETTING] == {}

    def test_a_level_or_reset_is_needed_and_a_bad_part_is_refused(self, run):
        assert run("level", "BuildEngine").exit_code == 2
        assert run("level", "BuildEngine", "info", "--reset").exit_code == 2
        bad = run("level", "bad part", "info")
        assert bad.exit_code == 2 and "no part of the framework" in bad.output


def test_the_framework_log_window_sets_and_resets_a_parts_level(qapp, tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from orchestrator.gui import log_window
    config = FakeConfig()
    window = log_window.FrameworkLogWindow(tmp_path, lang="en", config=config)
    try:
        assert window.lbl_part_levels.text() == "No part has its own level."
        window.txt_part_level_part.setText("BuildEngine")
        window.cmb_part_level.setCurrentIndex(log_window.PART_LEVELS.index("DEBUG"))
        window.btn_part_level_set.click()
        assert config.values[LOG_LEVELS_SETTING] == {"BuildEngine": "DEBUG"}
        assert window.lbl_part_levels.text() == "Own levels: BuildEngine: DEBUG"
        window.btn_part_level_reset.click()
        assert config.values[LOG_LEVELS_SETTING] == {}
        shown = []
        monkeypatch.setattr(log_window, "plain_box", lambda *args: shown.append(args))
        window.txt_part_level_part.setText("bad part")
        assert window.set_part_level() is False and shown
    finally:
        window.close()
    without = log_window.FrameworkLogWindow(tmp_path, lang="en")
    try:
        assert not hasattr(without, "btn_part_level_set")
    finally:
        without.close()
