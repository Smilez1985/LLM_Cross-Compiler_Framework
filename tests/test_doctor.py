"""Doctor: deterministic checks, tiered fixes with re-check, exit codes, CLI and read-only tool."""

import json
import os

import pytest

from orchestrator.Core.doctor import Doctor, Finding, Severity
from orchestrator.tools.registry import ToolContext


def finding(code, severity, fix=None, tier=0):
    return Finding("test", code, severity, f"message {code}", "hint", fix_tier=tier, fix=fix)


class TestDoctor:

    def test_exit_codes_follow_the_worst_finding(self):
        for severities, code in (([Severity.OK, Severity.INFO], 0), ([Severity.OK, Severity.WARN], 1),
                                 ([Severity.WARN, Severity.ERROR], 2)):
            doc = Doctor(None, [lambda fw, s=severities: [finding(f"c{i}", sev) for i, sev in enumerate(s)]])
            assert doc.run().exit_code == code

    def test_crashing_check_becomes_an_error_finding(self):
        def check_broken(_fw):
            raise RuntimeError("boom")
        report = Doctor(None, [check_broken]).run()
        assert report.findings[0].code == "broken.check_crashed" and report.exit_code == 2

    def test_fixes_respect_the_tier_and_are_rechecked(self):
        state = {"perm": False, "cache": False, "stubborn": 0}

        def check(_fw):
            out = []
            if not state["perm"]:
                out.append(finding("perm", Severity.WARN, lambda: state.update(perm=True), tier=1))
            if not state["cache"]:
                out.append(finding("cache", Severity.ERROR, lambda: state.update(cache=True), tier=2))
            out.append(finding("stubborn", Severity.WARN, lambda: state.update(stubborn=state["stubborn"] + 1), tier=1))
            return out

        report = Doctor(None, [check]).fix(tier_max=1)
        assert report.fixed == ["perm"] and report.still_present == ["stubborn"]
        assert state["cache"] is False, "tier 2 fixes need --tier 2"
        assert Doctor(None, [check]).fix(tier_max=2).fixed == ["cache"]

    def test_real_checks_on_a_booted_framework(self, booted_framework):
        report = Doctor(booted_framework).run()
        codes = {f.code: f for f in report.findings}
        assert codes["python.version"].severity == Severity.OK
        assert codes["targets.Rockchip"].severity == Severity.OK
        assert codes["data.calibration"].severity == Severity.OK
        assert codes["tools.permissions"].severity == Severity.INFO
        assert "docker.daemon" in codes

    @pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
    def test_world_readable_state_dir_ends_up_private(self, booted_framework):
        state = booted_framework.config.user_state_dir
        os.chmod(state, 0o755)
        from orchestrator.Core.doctor import check_user_state
        report = Doctor(booted_framework, [check_user_state]).fix(1)
        assert state.stat().st_mode & 0o077 == 0 and report.exit_code == 0

    def test_tool_is_read_only_and_never_fixes(self, booted_framework):
        spec = booted_framework.tool_registry.get("run_doctor")
        assert spec.annotations()["readOnlyHint"] is True
        result = booted_framework.tool_registry.call("run_doctor", {}, ToolContext("mcp"))
        assert any(f.code == "python.version" for f in result.findings)

    def test_cli_json_output(self, booted_framework):
        from click.testing import CliRunner
        from orchestrator import cli as cli_module

        class Ctx(cli_module.FrameworkContext):
            def require_framework(self):
                self.framework_manager = booted_framework
                return self

        result = CliRunner().invoke(cli_module.cli, ["doctor", "--json"], obj=Ctx())
        data = json.loads(result.stdout)
        assert result.exit_code == data["exit_code"]
        assert any(f["code"] == "targets.Rockchip" for f in data["findings"])
