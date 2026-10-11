"""Startup guard for the provider SDKs' environment variables, and that every entry point runs it first."""

import logging
import os
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from orchestrator.llm.sdk_env import guard_sdk_environment


class TestGuard:

    def test_header_variables_are_dropped_with_a_one_line_warning(self):
        env = {"OPENAI_CUSTOM_HEADERS": "Authorization: Bearer ENV", "ANTHROPIC_CUSTOM_HEADERS": "x-api-key: ENV",
               "PATH": "/bin"}
        warnings = guard_sdk_environment(environ=env)
        assert env == {"PATH": "/bin"}
        assert len(warnings) == 2 and all("\n" not in w for w in warnings)
        assert "OPENAI_CUSTOM_HEADERS" in warnings[0] and "ANTHROPIC_CUSTOM_HEADERS" in warnings[1]
        assert not any("ENV" in w for w in warnings)  # values are never echoed

    def test_the_setting_keeps_them(self):
        env = {"ANTHROPIC_CUSTOM_HEADERS": "X-Trace: 1"}
        assert guard_sdk_environment(True, environ=env) == []
        assert env == {"ANTHROPIC_CUSTOM_HEADERS": "X-Trace: 1"}

    @pytest.mark.parametrize("value, kept", [
        (True, True), ("true", True), ("yes", True), ("1", True), (" On ", True),
        (False, False), ("false", False), ("no", False), ("0", False), ("", False), (None, False), ("off", False),
    ])
    def test_only_a_real_yes_keeps_them(self, value, kept):
        # A quoted "false" in config.yml is a non-empty string; it must not count as yes.
        env = {"OPENAI_CUSTOM_HEADERS": "X-Trace: 1"}
        guard_sdk_environment(value, environ=env)
        assert ("OPENAI_CUSTOM_HEADERS" in env) is kept
        env = {"OPENAI_CUSTOM_HEADERS": "X-Trace: 1"}
        guard_sdk_environment(lambda: value, environ=env)
        assert ("OPENAI_CUSTOM_HEADERS" in env) is kept

    def test_settings_are_read_only_when_a_header_variable_is_set(self):
        def settings():
            raise AssertionError("settings were read")
        assert guard_sdk_environment(settings, environ={"PATH": "/bin"}) == []

    def test_sdk_debug_logging_is_lowered_unless_asked_for(self):
        env = {"ANTHROPIC_LOG": "debug", "OPENAI_LOG": "DEBUG"}
        sdk_logger = logging.getLogger("anthropic")
        previous = sdk_logger.level
        sdk_logger.setLevel(logging.DEBUG)
        try:
            warnings = guard_sdk_environment(environ=env)
            if "anthropic" in sys.modules:  # imported before the guard ran: its logger follows too
                assert sdk_logger.level == logging.INFO
        finally:
            sdk_logger.setLevel(previous)
        assert env == {"ANTHROPIC_LOG": "info", "OPENAI_LOG": "info"} and len(warnings) == 2
        assert all("--debug-http" in w for w in warnings)

        env = {"ANTHROPIC_LOG": "debug"}
        assert guard_sdk_environment(debug_http=True, environ=env) == [] and env == {"ANTHROPIC_LOG": "debug"}
        env = {"OPENAI_LOG": "info"}
        assert guard_sdk_environment(environ=env) == [] and env == {"OPENAI_LOG": "info"}


class TestEntryPoints:

    def test_cli_drops_header_variables_before_the_command_runs(self, monkeypatch):
        from orchestrator.cli import cli
        monkeypatch.setenv("ANTHROPIC_CUSTOM_HEADERS", "x-api-key: ENV")
        monkeypatch.setenv("ANTHROPIC_LOG", "debug")
        monkeypatch.setenv("COLUMNS", "40")  # a narrow terminal must not split the warning
        result = CliRunner().invoke(cli, ["-q", "mcp", "config"])
        assert result.exit_code == 0, result.output
        assert "ANTHROPIC_CUSTOM_HEADERS" not in os.environ and os.environ["ANTHROPIC_LOG"] == "info"
        lines = [line for line in result.stderr.splitlines() if "Ignoring ANTHROPIC_CUSTOM_HEADERS" in line]
        assert len(lines) == 1 and "ai_allow_sdk_env_headers: true to keep them)." in lines[0]
        assert "ENV" not in result.stderr
        assert "Ignoring" not in result.stdout  # stdout carries the MCP protocol for `mcp serve`

    def test_cli_keeps_them_when_allowed_and_with_debug_http(self, monkeypatch):
        from orchestrator.cli import cli
        monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "X-Trace: 1")
        monkeypatch.setenv("LLM_FRAMEWORK_AI_ALLOW_SDK_ENV_HEADERS", "true")
        monkeypatch.setenv("OPENAI_LOG", "debug")
        result = CliRunner().invoke(cli, ["-q", "--debug-http", "mcp", "config"])
        assert result.exit_code == 0, result.output
        assert os.environ["OPENAI_CUSTOM_HEADERS"] == "X-Trace: 1" and os.environ["OPENAI_LOG"] == "debug"
        assert "Ignoring" not in result.stderr

    def test_cli_reads_a_quoted_false_as_no(self, monkeypatch, tmp_path):
        from orchestrator.cli import cli
        config_dir = Path(os.environ["LLM_FRAMEWORK_CONFIG_DIR"])
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config.yml").write_text('ai_allow_sdk_env_headers: "false"\n', encoding="utf-8")
        monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "Authorization: Bearer ENV")
        result = CliRunner().invoke(cli, ["-q", "mcp", "config"])
        assert result.exit_code == 0, result.output
        assert "OPENAI_CUSTOM_HEADERS" not in os.environ and "Ignoring OPENAI_CUSTOM_HEADERS" in result.stderr

    def test_cli_still_runs_when_the_settings_cannot_be_read(self, monkeypatch, tmp_path):
        # The setting is read only because a header variable is set; an unusable config directory must then
        # neither crash every command nor keep the variable.
        from orchestrator.cli import cli
        blocker = tmp_path / "a-file"
        blocker.write_text("", encoding="utf-8")
        monkeypatch.setenv("LLM_FRAMEWORK_CONFIG_DIR", str(blocker / "configs"))
        monkeypatch.setenv("ANTHROPIC_CUSTOM_HEADERS", "x-api-key: ENV")
        result = CliRunner().invoke(cli, ["-q", "mcp", "config"])
        assert result.exit_code == 0, result.output
        assert "ANTHROPIC_CUSTOM_HEADERS" not in os.environ and "ENV" not in result.stderr

    def test_gui_reads_a_quoted_false_as_no(self, monkeypatch, tmp_path):
        pytest.importorskip("PySide6")
        import orchestrator.main as gui_main
        (tmp_path / "configs").mkdir()
        (tmp_path / "configs" / "config.yml").write_text('ai_allow_sdk_env_headers: "false"\n', encoding="utf-8")
        monkeypatch.setattr(gui_main, "root_dir", tmp_path)

        class QtStarted(Exception):
            pass

        seen = []

        def qt_application(argv):
            seen.append("OPENAI_CUSTOM_HEADERS" in os.environ)
            raise QtStarted

        monkeypatch.setenv("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
        monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "Authorization: Bearer ENV")
        monkeypatch.setattr(sys, "argv", ["llm-gui"])
        monkeypatch.setattr(gui_main, "QApplication", qt_application)
        with pytest.raises(QtStarted):
            gui_main.main()
        assert seen == [False]

    def test_gui_guards_the_environment_before_qt_starts(self, monkeypatch, capsys, tmp_path):
        pytest.importorskip("PySide6")
        import orchestrator.main as gui_main
        monkeypatch.setattr(gui_main, "root_dir", tmp_path)  # no configs/config.yml of the checkout

        class QtStarted(Exception):
            pass

        seen = []

        def qt_application(argv):
            seen.append((argv, "ANTHROPIC_CUSTOM_HEADERS" in os.environ, os.environ.get("ANTHROPIC_LOG")))
            raise QtStarted

        monkeypatch.setenv("QT_AUTO_SCREEN_SCALE_FACTOR", "1")  # main() sets it; restored afterwards
        monkeypatch.setenv("ANTHROPIC_CUSTOM_HEADERS", "x-api-key: ENV")
        monkeypatch.setenv("ANTHROPIC_LOG", "debug")
        monkeypatch.setattr(sys, "argv", ["llm-gui", "--debug-http"])
        monkeypatch.setattr(gui_main, "QApplication", qt_application)
        with pytest.raises(QtStarted):
            gui_main.main()
        assert seen == [(["llm-gui"], False, "debug")]
        assert "Ignoring ANTHROPIC_CUSTOM_HEADERS" in capsys.readouterr().err
