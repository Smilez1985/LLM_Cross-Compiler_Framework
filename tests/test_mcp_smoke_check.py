"""Client feedback (81): the real-client smoke test, made repeatable.

scripts/mcp_client_smoke.sh runs Claude Code against the framework's stdio server in a throw-away environment;
scripts/mcp_smoke_check.py writes that environment's MCP configuration and checks the outcomes from the isolated
audit database (and what the client was shown from the client's own JSON output), never from the model's words.
These tests cover the checker with fixture databases in the framework's real audit format, the isolation of the
server's environment, the skip rules, the clean-up, and the script's wiring end to end with a stand-in client that
drives the real server the way Claude Code does.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from conftest import REPO_ROOT
from orchestrator.tools.audit import AuditLog

SCRIPTS = REPO_ROOT / "scripts"
_spec = importlib.util.spec_from_file_location("mcp_smoke_check", SCRIPTS / "mcp_smoke_check.py")
smoke = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = smoke  # its dataclasses look their module up
_spec.loader.exec_module(smoke)

SCRIPT = SCRIPTS / "mcp_client_smoke.sh"
CLIENT = "claude-code 2.1.296 (stdio, protocol 2026-07-28)"
ALIAS_REPAIR = {"kind": "alias", "field": "quantization", "wrote": "quant"}
SH = shutil.which("sh")


def record(audit, tool, outcome, detail="", actor="mcp", **extra):
    audit.record(actor, tool, "read", "allow", outcome, arguments={}, detail=detail,
                 extra={"client": CLIENT, "session": "s", **extra})


def good_audit(path: Path) -> Path:
    """The entries a good run leaves, in the framework's own format."""
    audit = AuditLog(path)
    record(audit, "get_status", "ok")
    record(audit, "get_status", "invalid_input", detail="Unknown argument 'section' for get_status. Valid arguments: "
           "sections. Did you mean 'sections'? Next: Call get_status with only these arguments: sections.",
           reason_code="unknown_argument")
    record(audit, "check_build_request", "ok", repairs=[ALIAS_REPAIR])
    record(audit, "check_build_request", "invalid_input", detail="'rk3567' is not a valid board for Rockchip.",
           reason_code="invalid_value", repairs=[ALIAS_REPAIR])
    record(audit, "update_settings", "confirmation_requested", confirm_channel="elicitation",
           question_sha256="0" * 64)
    record(audit, "update_settings", "not_confirmed", confirm_channel="elicitation", answer="declined",
           reason_code="declined")
    return path


def transcript(path: Path, results) -> Path:
    """A `claude -p --output-format json --verbose` output with these (tool, text, is_error) results."""
    messages = [{"type": "system", "subtype": "init"}]
    for i, (tool, text, is_error) in enumerate(results):
        messages.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": f"t{i}", "name": f"mcp__lcf__{tool}", "input": {}}]}})
        messages.append({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": [{"type": "text", "text": text}],
             "is_error": is_error}]}})
    messages.append({"type": "result", "subtype": "success", "is_error": False, "permission_denials": []})
    path.write_text(json.dumps(messages), encoding="utf-8")
    return path


NOTE = smoke.ALIAS_NOTE
GOOD_RESULTS = [("check_build_request", f'Build check: 0 problems.\n{{"notes": ["{NOTE}"]}}', False),
                ("check_build_request", f"'rk3567' is not a valid board. Next: ... Notes: {NOTE}.", True)]


def run_check(tmp_path, audit=None, results=GOOD_RESULTS, raw_transcript=None):
    audit = audit or good_audit(tmp_path / "audit.db")
    out = raw_transcript or transcript(tmp_path / "alias.json", results)
    entries = smoke.read_audit(audit)
    calls, _ = smoke.read_transcript(out)
    return {message: ok for ok, message in smoke.run_checks(entries, calls)}


def failed(results):
    return [m for m, ok in results.items() if not ok]


# --- the checker ---------------------------------------------------------------------------------------------------

class TestChecker:

    def test_a_good_run_passes_every_check(self, tmp_path, capsys):
        audit = good_audit(tmp_path / "audit.db")
        out = transcript(tmp_path / "alias.json", GOOD_RESULTS)
        assert smoke.main(["check", "--audit", str(audit), "--transcript", f"alias={out}"]) == 0
        printed = capsys.readouterr().out
        assert f"PASS  client: {CLIENT}" in printed
        assert "PASS  unknown argument refused by get_status with a suggestion: Did you mean 'sections'?" in printed
        assert "PASS  update_settings did not run without an answer: asked via elicitation, ended not_confirmed " \
               "(answer declined)" in printed
        assert "all 8 checks passed" in printed

    def test_the_isolated_root_names_its_audit_database(self, tmp_path):
        good_audit(smoke.layout(tmp_path)["framework"] / "audit.db")  # AuditLog creates the folder
        out = transcript(tmp_path / "alias.json", GOOD_RESULTS)
        assert smoke.main(["check", "--root", str(tmp_path), "--transcript", f"alias={out}"]) == 0

    def test_no_audit_database_is_an_input_problem(self, tmp_path, capsys):
        assert smoke.main(["check", "--audit", str(tmp_path / "missing.db")]) == 2
        assert "could not be read" in capsys.readouterr().out

    def test_another_client_is_not_claude_code(self, tmp_path):
        audit = AuditLog(tmp_path / "audit.db")
        audit.record("mcp", "get_status", "read", "allow", "ok", extra={"client": "mcp 0.1.0 (stdio)"})
        results = run_check(tmp_path, audit=tmp_path / "audit.db")
        assert any(m.startswith("no call from a Claude Code client") and "mcp 0.1.0" in m for m in failed(results))

    def test_an_unknown_argument_without_a_suggestion_fails(self, tmp_path):
        audit = AuditLog(tmp_path / "audit.db")
        record(audit, "get_status", "invalid_input", detail="Unknown argument 'zz' for get_status.",
               reason_code="unknown_argument")
        assert "an unknown argument was refused by get_status, but without a suggestion" in failed(
            run_check(tmp_path, audit=tmp_path / "audit.db"))

    def test_an_unknown_argument_the_client_dropped_fails(self, tmp_path):
        audit = AuditLog(tmp_path / "audit.db")
        record(audit, "get_status", "ok")
        assert any(m.startswith("no call with an unknown argument reached the server")
                   for m in failed(run_check(tmp_path, audit=tmp_path / "audit.db")))

    def test_the_alias_must_be_recorded_on_a_refusal_too(self, tmp_path):
        audit = AuditLog(tmp_path / "audit.db")
        record(audit, "check_build_request", "ok", repairs=[ALIAS_REPAIR])
        record(audit, "check_build_request", "invalid_input", reason_code="invalid_value")  # the repair is missing
        assert "no refused call recorded the alias 'quant' as a repair" in failed(
            run_check(tmp_path, audit=tmp_path / "audit.db"))

    def test_a_refusal_without_the_note_fails(self, tmp_path):
        """The real client's finding (79): the refusal the client got did not name the repair."""
        results = run_check(tmp_path, results=[GOOD_RESULTS[0], ("check_build_request", "'rk3567' is not valid.",
                                                                  True)])
        assert failed(results) == [f"no refusal the client got named \"{NOTE}\""]

    def test_output_without_tool_results_cannot_show_anything(self, tmp_path):
        raw = tmp_path / "alias.json"
        raw.write_text(json.dumps({"type": "result", "subtype": "success", "result": "done"}), encoding="utf-8")
        assert any("--output-format json --verbose" in m for m in failed(run_check(tmp_path, raw_transcript=raw)))

    def test_the_models_prose_is_never_evidence(self, tmp_path):
        """The model claiming the note in its answer proves nothing: only tool results count."""
        messages = [{"type": "assistant", "message": {"content": [{"type": "text", "text": f"Notes: {NOTE}"}]}},
                    {"type": "result", "subtype": "success", "result": f"The tool said: {NOTE}"}]
        raw = tmp_path / "alias.json"
        raw.write_text(json.dumps(messages), encoding="utf-8")
        assert smoke.read_transcript(raw)[0] == []
        assert any("no tool results" in m for m in failed(run_check(tmp_path, raw_transcript=raw)))

    @pytest.mark.parametrize("outcome", ["started", "ok"])
    def test_a_confirmation_needing_call_that_ran_fails(self, tmp_path, outcome):
        audit = AuditLog(tmp_path / "audit.db")
        record(audit, "update_settings", "confirmation_requested", confirm_channel="elicitation")
        record(audit, "update_settings", outcome)
        assert f"update_settings ran without the person's answer (outcome {outcome})" in failed(
            run_check(tmp_path, audit=tmp_path / "audit.db"))

    def test_a_confirmation_needing_call_never_made_fails(self, tmp_path):
        audit = AuditLog(tmp_path / "audit.db")
        record(audit, "get_status", "ok")
        assert "update_settings was never called" in failed(run_check(tmp_path, audit=tmp_path / "audit.db"))

    def test_a_pending_approval_counts_as_not_run(self, tmp_path):
        audit = AuditLog(tmp_path / "audit.db")
        record(audit, "update_settings", "confirmation_requested", confirm_channel="approval:ap_0123456789abcdef")
        ok, message = smoke.check_confirmation(smoke.read_audit(tmp_path / "audit.db"))
        assert ok and "asked via approval:ap_0123456789abcdef" in message

    def test_text_from_the_log_is_shown_as_one_short_line(self):
        line = smoke.shown("a\nb\x1b[31m" + "x" * 300, 20)
        assert len(line) == 20 and line.startswith("a b?[31mx") and line.endswith("...")

    def test_the_registrys_own_audit_entries_pass_the_alias_checks(self, tmp_path):
        """The fixture format is the registry's: a real alias call that ran and one that was refused."""
        from orchestrator.evals.ditto_eval import EvalEnvironment
        from orchestrator.tools.registry import ToolContext, ToolError
        env = EvalEnvironment(REPO_ROOT / "targets")
        try:
            call = {"model": "Qwen/Qwen2.5-0.5B-Instruct", "target": "Rockchip", "format": "gguf", "quant": "Q4_K_M",
                    "include_download_size": False}
            env.registry.call("check_build_request", call, ToolContext("mcp", session="a"))
            with pytest.raises(ToolError):
                env.registry.call("check_build_request", {**call, "board": "rk3567"}, ToolContext("mcp", session="a"))
            entries = smoke.read_audit(Path(env.registry.audit.path))
        finally:
            env.close()
        recorded = smoke.check_alias(entries, [])[:2]
        assert all(ok for ok, _ in recorded), recorded


# --- the server's environment: isolated, nothing inherited ---------------------------------------------------------

class TestIsolation:

    @pytest.mark.skipif(sys.platform == "win32", reason="the script and its env -i line are for POSIX systems")
    def test_the_server_gets_only_the_isolated_environment(self, tmp_path, monkeypatch):
        for name in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "LCF_MCP_TOKEN", "HF_TOKEN", "GITHUB_TOKEN"):
            monkeypatch.setenv(name, "secret-value-that-must-not-travel")
        out = smoke.write_config(tmp_path / "root", REPO_ROOT, sys.executable, "/usr/bin:/bin", tmp_path / "mcp.json")
        config = json.loads(out.read_text())["mcpServers"]["lcf"]
        assert config["type"] == "stdio" and Path(config["command"]).name == "env" and config["args"][0] == "-i"
        assert config["args"][-7:] == [sys.executable, "-P", "-m", "orchestrator.cli", "-q", "mcp", "serve"]
        variables = dict(a.split("=", 1) for a in config["args"][1:-7])
        assert "secret-value-that-must-not-travel" not in out.read_text()
        assert not [n for n in variables if any(w in n for w in ("TOKEN", "KEY", "ANTHROPIC", "CLAUDE", "SECRET"))
                    and n not in ("PYTHON_KEYRING_BACKEND", "LLM_SECRET_STORAGE")]
        root = (tmp_path / "root").resolve()
        for name in ("HOME", "HF_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
                     "LLM_FRAMEWORK_HOME", "TMPDIR", "DOCKER_CONFIG"):
            assert Path(variables[name]).resolve().is_relative_to(root), name
            assert Path(variables[name]).is_dir() or name in ("DOCKER_CONFIG",), name
        assert variables["LLM_SECRET_STORAGE"] == "ephemeral"
        assert variables["QT_QPA_PLATFORM"] == "offscreen"
        assert variables["PYTHON_KEYRING_BACKEND"] == "keyring.backends.null.Keyring"  # never the real keyring
        assert variables["HF_HUB_OFFLINE"] == "1" and variables["PYTHONPATH"] == str(REPO_ROOT)
        assert oct(out.stat().st_mode & 0o777) == "0o600"

    def test_the_prompts_name_the_servers_tools(self):
        for name, prompt in smoke.PROMPTS.items():
            assert "mcp__lcf__" in prompt, name
        assert "'section'" in smoke.PROMPTS["unknown"] and '"quant"' in smoke.PROMPTS["alias"]

    def test_the_script_never_touches_claude_credentials(self):
        """The claude command signs in by itself: neither file reads, copies or passes anything of it."""
        for path in (SCRIPT, SCRIPTS / "mcp_smoke_check.py"):
            text = path.read_text(encoding="utf-8").lower()
            for word in ("credentials.json", ".claude/", ".claude.json", "anthropic_api_key", "oauth_token",
                         "setup-token", "keychain", "auth login --", "claude_config_dir"):
                assert word not in text, (path.name, word)


# --- the script: skips, clean-up, and its wiring end to end ------------------------------------------------------

FAKE_CLAUDE = textwrap.dedent('''\
    #!{python}
    """A stand-in for the claude command: answers `auth status`, and for -p drives the server of the MCP
    configuration through the SDK the way Claude Code does (one session per prompt), printing a verbose JSON output."""
    import asyncio, json, os, sys
    args = sys.argv[1:]
    if args[:2] == ["auth", "status"]:
        print(json.dumps({{"loggedIn": {signed_in}, "authMethod": "none"}}, indent=2))
        sys.exit(0 if {signed_in} else 1)
    if args[:1] == ["--version"]:
        print("0.0.0 (stand-in)")
        sys.exit(0)
    prompt = args[args.index("-p") + 1]
    config = json.load(open(args[args.index("--mcp-config") + 1]))["mcpServers"]["lcf"]
    assert "--strict-mcp-config" in args and "--no-session-persistence" in args
    assert args[args.index("--allowedTools") + 1] == "mcp__lcf" and args[args.index("--tools") + 1] == ""
    # What the server's process gets from this configuration (the same `env -i` line, another Python program).
    import subprocess
    names = subprocess.run([config["command"], *config["args"][:-6], "-c",
                            "import json, os, sys; json.dump(sorted(os.environ), sys.stdout)"],
                           capture_output=True, text=True, check=True).stdout
    open(os.path.join(os.environ["FAKE_LOG"], "server_env.json"), "w").write(names)
    open(os.path.join(os.environ["FAKE_LOG"], "client_cache.txt"), "w").write(os.environ.get("XDG_CACHE_HOME", ""))
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters
    from mcp_types import ElicitResult, Implementation
    decoder, calls, text = json.JSONDecoder(), [], prompt
    while "{{" in text:
        text = text[text.index("{{"):]
        value, end = decoder.raw_decode(text)
        calls.append(value)
        text = text[end:]
    tool = prompt.split("mcp__lcf__")[1].split()[0].rstrip(".,")
    if "get_status once, without arguments" in prompt:
        calls = [{{}}]
    async def decline(ctx, params):
        return ElicitResult(action="decline")
    async def go():
        params = StdioServerParameters(command=config["command"], args=config["args"], cwd=os.getcwd())
        messages = [{{"type": "system", "subtype": "init"}}]
        async with Client(params, elicitation_callback=decline,
                          client_info=Implementation(name="claude-code", version="0.0.0")) as client:
            for i, arguments in enumerate(calls):
                result = await client.call_tool(tool, arguments)
                text = " ".join(getattr(c, "text", "") for c in result.content)
                messages.append({{"type": "assistant", "message": {{"content": [
                    {{"type": "tool_use", "id": f"t{{i}}", "name": "mcp__lcf__" + tool, "input": arguments}}]}}}})
                messages.append({{"type": "user", "message": {{"content": [
                    {{"type": "tool_result", "tool_use_id": f"t{{i}}", "content": text,
                     "is_error": bool(result.is_error)}}]}}}})
        messages.append({{"type": "result", "subtype": "success", "is_error": False, "permission_denials": []}})
        print(json.dumps(messages))
    asyncio.run(go())
''')


def fake_claude(tmp_path, signed_in=True):
    path = tmp_path / "bin" / "claude"
    path.parent.mkdir(exist_ok=True)
    path.write_text(FAKE_CLAUDE.format(python=sys.executable, signed_in="True" if signed_in else "False"))
    path.chmod(0o755)
    return path


def run_script(tmp_path, claude, extra_env=None, timeout=600):
    work_tmp = tmp_path / "tmp"
    work_tmp.mkdir(exist_ok=True)
    (tmp_path / "log").mkdir(exist_ok=True)
    env = {**os.environ, "LCF_SMOKE_CLAUDE": str(claude), "LCF_SMOKE_PYTHON": sys.executable,
           "TMPDIR": str(work_tmp), "FAKE_LOG": str(tmp_path / "log"),
           "CLAUDE_CODE_OAUTH_TOKEN": "a-token-the-server-must-never-see", **(extra_env or {})}
    return subprocess.run([SH, str(SCRIPT)], capture_output=True, text=True, env=env, timeout=timeout,
                          stdin=subprocess.DEVNULL)


@pytest.mark.skipif(SH is None or sys.platform == "win32", reason="the script is for POSIX sh")
class TestScript:

    def test_posix_sh_syntax(self):
        assert subprocess.run([SH, "-n", str(SCRIPT)]).returncode == 0
        dash = shutil.which("dash")
        if dash:
            assert subprocess.run([dash, "-n", str(SCRIPT)]).returncode == 0

    def test_no_claude_command_is_a_skip(self, tmp_path):
        result = run_script(tmp_path, tmp_path / "no-such-claude")
        assert result.returncode == 77 and "SKIP: the claude command is not installed" in result.stderr
        assert list((tmp_path / "tmp").iterdir()) == []

    def test_not_signed_in_is_a_skip(self, tmp_path):
        result = run_script(tmp_path, fake_claude(tmp_path, signed_in=False))
        assert result.returncode == 77 and "SKIP: the claude command is not signed in" in result.stderr
        assert list((tmp_path / "tmp").iterdir()) == []

    def test_a_python_without_the_framework_is_a_setup_problem(self, tmp_path):
        result = run_script(tmp_path, fake_claude(tmp_path), {"LCF_SMOKE_PYTHON": "/no/such/python"})
        assert result.returncode == 2 and "LCF_SMOKE_PYTHON" in result.stderr

    def test_the_whole_run_with_a_stand_in_client(self, tmp_path):
        """The script's wiring end to end: the isolated server boots under `env -i`, the stand-in client drives the
        four prompts through it, the checker reads the isolated audit database and the outputs, the temporary folder
        is removed afterwards, and the server never saw the caller's token."""
        home_before = sorted(p.name for p in Path(os.environ["HOME"]).iterdir()) if Path(
            os.environ["HOME"]).is_dir() else []
        result = run_script(tmp_path, fake_claude(tmp_path))
        assert result.returncode == 0, result.stdout + result.stderr
        out = result.stdout
        for name in ("status", "unknown", "alias", "confirm"):
            assert f"run '{name}': claude exited with 0" in out
        assert "PASS  client: claude-code 0.0.0 (stdio, protocol" in out
        assert "PASS  unknown argument refused by get_status with a suggestion: Did you mean 'sections'?" in out
        assert f"PASS  the client was shown \"{smoke.ALIAS_NOTE}\" in a refusal" in out
        assert "PASS  update_settings did not run without an answer" in out
        assert "all 8 checks passed" in out
        assert list((tmp_path / "tmp").iterdir()) == [], "the temporary folder is cleaned up"
        server_env = json.loads((tmp_path / "log" / "server_env.json").read_text())
        client_cache = Path((tmp_path / "log" / "client_cache.txt").read_text())
        assert client_cache.is_relative_to(tmp_path / "tmp"), "the client logs the server's stderr into the temp folder"
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in server_env and "FAKE_LOG" not in server_env
        assert {"HOME", "HF_HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "LLM_FRAMEWORK_HOME", "LLM_SECRET_STORAGE",
                "QT_QPA_PLATFORM"} <= set(server_env)
        home_after = sorted(p.name for p in Path(os.environ["HOME"]).iterdir()) if Path(
            os.environ["HOME"]).is_dir() else []
        assert home_after == home_before, "nothing was written into the home folder"
