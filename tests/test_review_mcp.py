"""Regression tests for the MCP fixes of the Phase 1+2 review (commit 1a714ea).

Confirmation text (every argument, security fields first, control and bidi characters escaped, refused
when too long), who approved what (a policy decision is never recorded as a person's confirmation;
decline, cancel and url-only clients are audited), copy-pasteable client snippets, the Claude Desktop
config writer (private backup, symlinks) and a clean stdout for `llm-cli mcp serve`.
"""

import asyncio
import errno
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

pytest.importorskip("mcp")
import mcp_types  # noqa: E402
from mcp import Client  # noqa: E402
from mcp.client.session import ClientSession  # noqa: E402
from mcp.shared.exceptions import MCPError  # noqa: E402
from mcp_types import ElicitResult  # noqa: E402

from conftest import REPO_ROOT  # noqa: E402
from orchestrator import mcp_server  # noqa: E402
from orchestrator.schemas.build import BuildHandle, BuildRequestIn  # noqa: E402
from orchestrator.tools.audit import AuditLog  # noqa: E402
from orchestrator.tools.framework_tools import ResolveHealingIn  # noqa: E402
from orchestrator.tools.policy import Decision, PolicyStore, ToolPolicy, ToolTier  # noqa: E402
from orchestrator.tools.registry import ToolRegistry, ToolSpec  # noqa: E402
from test_mcp_server import In, Out, answering, run, text_of  # noqa: E402

MODES = ["auto", "legacy"]  # 2026-07-28 input_required rounds, and the classic in-request elicitation
PERMISSIONS_HINT = "llm-cli tools permissions"
# The reviewer's attack: a valid but padded local model path pushes the dangerous fields out of view.
PADDED_MODEL = "/home/user/models/" + "./" * 240 + "SmolLM2"
ATTACK = {"model": PADDED_MODEL, "target": "Rockchip", "format": "gguf", "quantization": "Q2_K",
          "use_imatrix": False, "dataset_path": "/home/user/.ssh/id_ed25519", "use_gpu": True,
          "max_context_length": 4096}


class NoInput(BaseModel):
    pass


@pytest.fixture(autouse=True)
def no_xdg_config_home(monkeypatch):
    """conftest moves HOME, but a runner's XDG_CONFIG_HOME (GitHub's Ubuntu images set one) would still point
    the Claude Desktop config at the real folder once the code honours it."""
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)


@pytest.fixture
def registry(tmp_path):
    reg = ToolRegistry(PolicyStore(tmp_path), AuditLog(tmp_path / "audit.db"))
    reg.writes, reg.builds = [], []
    reg.register(ToolSpec("echo", "Echo", "Echo text", ToolTier.READ, In, Out,
                          lambda i: Out(echoed=i.text * i.count), idempotent=True))
    reg.register(ToolSpec("write", "Write", "Writes", ToolTier.LOCAL_WRITE, In, Out,
                          lambda i: reg.writes.append(i.text) or Out(echoed="written")))
    # The real start_build schema with a fake handler, so the test sees exactly what would run.
    reg.register(ToolSpec("start_build", "Start a build", "Starts a build", ToolTier.LOCAL_WRITE, BuildRequestIn,
                          BuildHandle, lambda i: reg.builds.append(i) or BuildHandle(request_id="r1", status="queued")))
    reg.register(ToolSpec("ping", "Ping", "No arguments", ToolTier.LOCAL_WRITE, NoInput, Out,
                          lambda i: Out(echoed="pong")))
    return reg


@pytest.fixture
def server(registry):
    return mcp_server.build_mcp_server(SimpleNamespace(info=SimpleNamespace(version="test"), tool_registry=registry))


@pytest.fixture
def declare_elicitation(monkeypatch):
    """Makes the SDK client declare exactly this elicitation capability (e.g. url-only)."""
    def declare(capability):
        original = ClientSession._build_capabilities

        def build(self, version):
            return original(self, version).model_copy(update={"elicitation": capability})
        monkeypatch.setattr(ClientSession, "_build_capabilities", build)
    return declare


def call(server, tool, arguments, callback=None, mode="auto"):
    async def go():
        async with Client(server, elicitation_callback=callback, mode=mode) as client:
            return await client.call_tool(tool, arguments)
    return run(go())


def rows(registry):
    """Audit entries, newest first, as (tool, decision, approved, outcome)."""
    return [(e["tool"], e["decision"], e["approved"], e["outcome"]) for e in registry.audit.entries(50)]


def spec_named(title="Start a build"):
    return ToolSpec("t", title, "", ToolTier.LOCAL_WRITE, In, Out, lambda i: i)


def shown_keys(message):
    return [line.split(":", 1)[0].strip() for line in message.splitlines()[1:]]


# --- Confirmation text ----------------------------------------------------------------------------

class TestConfirmationText:

    @pytest.mark.parametrize("mode", MODES)
    def test_long_first_field_hides_nothing(self, server, registry, mode):
        assert len(PADDED_MODEL) >= 500
        asked = []
        result = call(server, "start_build", ATTACK, answering("accept", asked=asked), mode)
        assert not result.is_error, text_of(result)
        message = asked[0]
        assert "..." not in message.replace(PADDED_MODEL, ""), "nothing may be cut off"
        for line in (f"  model: {PADDED_MODEL}", "  dataset_path: /home/user/.ssh/id_ed25519", "  use_gpu: true",
                     "  target: Rockchip", "  format: gguf", "  quantization: Q2_K", "  use_imatrix: false",
                     "  max_context_length: 4096"):
            assert line in message.splitlines(), f"the user never saw {line.strip()!r}"
        keys = shown_keys(message)
        assert keys[:6] == ["model", "dataset_path", "use_gpu", "target", "format", "quantization"]
        assert set(keys[6:]) == {"use_imatrix", "max_context_length"}
        # What ran is exactly what was shown.
        ran = registry.builds[0]
        assert (ran.model, ran.dataset_path, ran.use_gpu, ran.max_context_length) == (
            PADDED_MODEL, "/home/user/.ssh/id_ed25519", True, 4096)

    def test_security_fields_come_first_whatever_order_the_client_sends(self):
        arguments = dict(reversed(list({**ATTACK, "board": "rk3588", "request_id": "b1"}.items())))
        keys = shown_keys(mcp_server._describe_call(spec_named(), arguments))
        assert keys[:8] == ["model", "dataset_path", "use_gpu", "target", "format", "board", "quantization",
                            "request_id"]
        assert keys[8:] == ["max_context_length", "use_imatrix"], "the rest keeps the client's order"

    def test_none_is_left_out_but_false_zero_and_empty_are_shown(self):
        keys = shown_keys(mcp_server._describe_call(spec_named(), {"board": None, "use_gpu": False,
                                                                   "count": 0, "note": ""}))
        assert keys == ["use_gpu", "count", "note"]

    @pytest.mark.parametrize("mode", MODES)
    def test_tool_without_arguments_says_so(self, server, mode):
        asked = []
        assert not call(server, "ping", {}, answering("accept", asked=asked), mode).is_error
        assert asked[0].endswith("with:\n  (no arguments)")

    def test_question_only_asks_for_the_approve_box(self, server):
        params = []

        async def callback(ctx, p):
            params.append(p)
            return ElicitResult(action="accept", content={"approve": True})
        assert not call(server, "write", {"text": "x"}, callback).is_error
        assert set(params[0].requested_schema["properties"]) == {"approve"}, "no internal marker in the form"


class TestTooLong:

    def test_limit_is_inclusive(self):
        spec = spec_named("Write")
        head = len(mcp_server._describe_call(spec, {"text": ""}))
        fits = mcp_server._describe_call(spec, {"text": "x" * (mcp_server.MAX_DESCRIPTION - head)})
        assert len(fits) == mcp_server.MAX_DESCRIPTION and fits.endswith("x")
        with pytest.raises(mcp_server.CallTooLong, match="too long"):
            mcp_server._describe_call(spec, {"text": "x" * (mcp_server.MAX_DESCRIPTION - head + 1)})

    @pytest.mark.parametrize("mode", MODES)
    def test_refused_before_asking_and_never_run(self, server, registry, mode):
        asked = []
        result = call(server, "write", {"text": "x" * 5000}, answering("accept", asked=asked), mode)
        assert result.is_error and "too long" in text_of(result) and "not run" in text_of(result)
        assert asked == [] and registry.writes == []

    @pytest.mark.parametrize("mode", MODES)
    def test_escaped_length_counts(self, server, registry, mode):
        """Within the schema's max_length, but invisible characters expand to \\uXXXX when shown."""
        asked = []
        arguments = {"model": "m/" + "\u202e" * 500, "target": "Rockchip", "format": "gguf",
                     "dataset_path": "\u2066" * 1000}
        result = call(server, "start_build", arguments, answering("accept", asked=asked), mode)
        assert result.is_error and "too long" in text_of(result)
        assert asked == [] and registry.builds == []

    def test_refusal_is_audited(self, server, registry):
        result = call(server, "write", {"text": "x" * 5000}, answering("accept"))
        assert result.is_error and registry.writes == []
        entries = rows(registry)
        assert len(entries) == 1 and entries[0][:2] == ("write", "ask") and entries[0][3] != "ok"


DANGEROUS = ["\n", "\r", "\t", "\x00", "\x1b", "\x7f", "\x85", "\u2028", "\u2029", "\u200b", "\u200e", "\u200f",
             "\u202a", "\u202b", "\u202d", "\u202e", "\u2066", "\u2067", "\u2068", "\u2069", "\u061c", "\u00ad",
             "\ufeff", "\ue000", "\ufdd0", "\U000e0041"]


class TestEscaping:

    @pytest.mark.parametrize("char", DANGEROUS, ids=lambda c: f"U+{ord(c):04X}")
    def test_control_format_and_bidi_characters_are_escaped(self, char):
        message = mcp_server._describe_call(spec_named(), {"model": f"safe{char}tail", "use_gpu": True})
        assert f"  model: safe\\u{ord(char):04x}tail" in message.splitlines()
        assert char not in message.split("\n", 1)[1].replace("\n", ""), "the raw character reaches the dialog"

    def test_nested_values_are_escaped_too(self):
        message = mcp_server._describe_call(spec_named(), {"extra": {"note": "a\u202eb\nc"}, "tags": ["x\u2066"]})
        body = message.split("\n", 1)[1]
        assert "\u202e" not in body and "\u2066" not in body and "\\u202e" in body and "\\u2066" in body
        assert len(body.splitlines()) == 2

    def test_readable_text_is_unchanged(self):
        text = "Modell für Ärzte 模型 🙂 café naïve ½"
        assert f"  model: {text}" in mcp_server._describe_call(spec_named(), {"model": text}).splitlines()

    @pytest.mark.parametrize("mode", MODES)
    def test_newline_in_a_value_cannot_forge_another_field(self, server, registry, mode):
        asked = []
        arguments = {**ATTACK, "model": "SmolLM2\n  dataset_path: (none)\n  use_gpu: false\u2028  board: rk3566"}
        result = call(server, "start_build", arguments, answering("accept", asked=asked), mode)
        assert not result.is_error
        lines = asked[0].splitlines()
        assert len(lines) == 1 + 8, "one line per argument"
        assert [x for x in lines if x.startswith("  dataset_path:")] == ["  dataset_path: /home/user/.ssh/id_ed25519"]
        assert [x for x in lines if x.startswith("  use_gpu:")] == ["  use_gpu: true"]
        assert not any(x.startswith("  board:") for x in lines)
        assert lines[1] == "  model: SmolLM2\\u000a  dataset_path: (none)\\u000a  use_gpu: false\\u2028  board: rk3566"

    @pytest.mark.parametrize("mode", MODES)
    def test_end_to_end_dialog_has_no_raw_invisible_characters(self, server, mode):
        asked = []
        hostile = "a" + "".join(DANGEROUS) + "z"
        assert not call(server, "write", {"text": hostile}, answering("accept", asked=asked), mode).is_error
        body = asked[0].split("\n", 1)[1].replace("\n", "")
        assert not any(ch in body for ch in DANGEROUS)


# --- Who approved what, and the audit log ----------------------------------------------------------

class TestApprovals:

    @pytest.mark.parametrize("with_elicitation", [True, False])
    def test_allowed_calls_are_not_recorded_as_confirmed(self, server, registry, with_elicitation):
        registry.policy_store.save(ToolPolicy(configured=True, overrides={"write": Decision.ALLOW}))
        asked = []
        callback = answering("accept", asked=asked) if with_elicitation else None
        assert not call(server, "write", {"text": "x"}, callback).is_error
        assert not call(server, "echo", {"text": "y"}, callback).is_error
        assert asked == [] and registry.writes == ["x"]
        assert rows(registry) == [("echo", "allow", None, "ok"), ("write", "allow", None, "ok")]

    def test_denied_calls_are_audited_without_a_confirmation(self, server, registry):
        registry.policy_store.save(ToolPolicy(configured=True, tiers={ToolTier.LOCAL_WRITE: Decision.DENY}))
        asked = []
        result = call(server, "write", {"text": "x"}, answering("accept", asked=asked))
        assert result.is_error and "not allowed by your tool permissions" in text_of(result)
        assert PERMISSIONS_HINT not in text_of(result) and "declined" not in text_of(result)
        assert asked == [] and registry.writes == []
        assert rows(registry) == [("write", "deny", None, "denied")]

    @pytest.mark.parametrize("mode", MODES)
    @pytest.mark.parametrize("action, approve", [("decline", None), ("cancel", None), ("accept", False)])
    def test_decline_cancel_and_unticked_are_audited_refusals(self, server, registry, mode, action, approve):
        result = call(server, "write", {"text": "x"}, answering(action, approve), mode)
        text = text_of(result)
        assert result.is_error and "The user declined 'Write'" in text
        assert PERMISSIONS_HINT not in text and "cannot ask" not in text, "never push a standing permission"
        assert registry.writes == []
        assert rows(registry) == [("write", "ask", 0, "not_confirmed")]

    @pytest.mark.parametrize("mode", MODES)
    def test_accept_is_recorded_as_confirmed(self, server, registry, mode):
        assert not call(server, "write", {"text": "x"}, answering("accept"), mode).is_error
        assert rows(registry) == [("write", "ask", 1, "ok")]

    @pytest.mark.parametrize("approve", [False, True])
    def test_client_cannot_smuggle_the_not_asked_marker(self, server, registry, approve):
        async def callback(ctx, params):
            return ElicitResult(action="accept", content={"approve": approve, "can_ask": False})
        result = call(server, "write", {"text": "x"}, callback)
        if approve:
            assert not result.is_error and rows(registry) == [("write", "ask", 1, "ok")]
        else:
            assert result.is_error and "The user declined" in text_of(result)
            assert PERMISSIONS_HINT not in text_of(result)
            assert rows(registry) == [("write", "ask", 0, "not_confirmed")]

    def test_client_without_elicitation_is_denied_with_hint_and_audited(self, server, registry):
        result = call(server, "write", {"text": "x"})
        assert result.is_error and PERMISSIONS_HINT in text_of(result) and "declined" not in text_of(result)
        assert registry.writes == []
        assert rows(registry) == [("write", "ask", None, "not_confirmed")], "nobody was asked: approved is NULL"


class TestUrlOnlyClients:

    @pytest.mark.parametrize("mode", MODES)
    def test_url_only_client_gets_a_clean_denial(self, server, registry, declare_elicitation, mode):
        declare_elicitation(mcp_types.ElicitationCapability(url=mcp_types.UrlElicitationCapability()))
        asked = []
        result = call(server, "write", {"text": "x"}, answering("accept", asked=asked), mode)  # no exception
        assert result.is_error and PERMISSIONS_HINT in text_of(result)
        assert "-32021" not in text_of(result) and "elicitation capability" not in text_of(result)
        assert asked == [] and registry.writes == []
        assert rows(registry) == [("write", "ask", None, "not_confirmed")]

    def test_url_only_client_still_runs_allowed_tools(self, server, registry, declare_elicitation):
        declare_elicitation(mcp_types.ElicitationCapability(url=mcp_types.UrlElicitationCapability()))
        result = call(server, "echo", {"text": "hi"}, answering("accept"))
        assert not result.is_error and result.structured_content == {"echoed": "hi"}
        assert rows(registry) == [("echo", "allow", None, "ok")]

    @pytest.mark.parametrize("mode", MODES)
    @pytest.mark.parametrize("capability", [
        mcp_types.ElicitationCapability(),
        mcp_types.ElicitationCapability(form=mcp_types.FormElicitationCapability()),
        mcp_types.ElicitationCapability(form=mcp_types.FormElicitationCapability(),
                                        url=mcp_types.UrlElicitationCapability()),
    ], ids=["bare", "form", "form+url"])
    def test_form_capable_clients_are_asked(self, server, registry, declare_elicitation, mode, capability):
        declare_elicitation(capability)
        asked = []
        assert not call(server, "write", {"text": "x"}, answering("accept", asked=asked), mode).is_error
        assert len(asked) == 1 and registry.writes == ["x"]
        assert rows(registry) == [("write", "ask", 1, "ok")]


class TestPolicyChanges:

    @pytest.mark.parametrize("with_elicitation", [True, False])
    def test_allow_tightened_to_ask_between_resolver_and_registry_is_denied(self, server, registry, monkeypatch,
                                                                           with_elicitation):
        registry.policy_store.save(ToolPolicy(configured=True, overrides={"write": Decision.ALLOW}))
        real_decide, seen = registry.decide, []

        def decide(spec):
            decision = real_decide(spec)
            seen.append(decision)
            if len(seen) == 1:  # right after the resolver looked at it
                registry.policy_store.save(ToolPolicy(configured=True, overrides={"write": Decision.ASK}))
            return decision
        monkeypatch.setattr(registry, "decide", decide)
        asked = []
        callback = answering("accept", asked=asked) if with_elicitation else None
        result = call(server, "write", {"text": "x"}, callback)
        assert seen == [Decision.ALLOW, Decision.ASK]
        assert result.is_error and registry.writes == [] and asked == []
        assert "declined" not in text_of(result), "nobody was asked"
        assert rows(registry) == [("write", "ask", None, "not_confirmed")]

    @pytest.mark.parametrize("mode", MODES)
    def test_ask_tightened_to_deny_during_the_dialog_wins(self, server, registry, mode):
        async def callback(ctx, params):
            registry.policy_store.save(ToolPolicy(configured=True, overrides={"write": Decision.DENY}))
            return ElicitResult(action="accept", content={"approve": True})
        result = call(server, "write", {"text": "x"}, callback, mode)
        assert result.is_error and "not allowed by your tool permissions" in text_of(result)
        assert registry.writes == []
        [(tool, decision, _, outcome)] = rows(registry)
        assert (tool, decision, outcome) == ("write", "deny", "denied")

    @pytest.mark.parametrize("mode", MODES)
    def test_no_in_the_dialog_is_not_overridden_by_a_looser_policy(self, server, registry, mode):
        async def callback(ctx, params):
            # e.g. the user allows the tool in the GUI while this dialog is still open, then says no to it
            registry.policy_store.save(ToolPolicy(configured=True, overrides={"write": Decision.ALLOW}))
            return ElicitResult(action="decline")
        result = call(server, "write", {"text": "x"}, callback, mode)
        assert registry.writes == [], "the call ran although the user declined it"
        assert result.is_error


class TestEarlyExitsAudit:

    @pytest.mark.parametrize("case", ["invalid_arguments", "malformed_answer"])
    def test_sdk_rejections_are_audited(self, server, registry, case):
        async def callback(ctx, params):
            return ElicitResult(action="accept", content={})  # no 'approve'
        if case == "invalid_arguments":
            result = call(server, "write", {"txt": "x"}, answering("accept"))
        else:
            result = call(server, "write", {"text": "x"}, callback)
        assert result.is_error and registry.writes == []
        assert len(rows(registry)) == 1 and rows(registry)[0][0] == "write"


# --- Client snippets ------------------------------------------------------------------------------

def windows_argv(cmdline):
    """Splits a command line like the Microsoft C runtime (CommandLineToArgvW) does."""
    args, current, in_arg, quoted, i = [], [], False, False, 0
    while i < len(cmdline):
        c = cmdline[i]
        if c == "\\":
            j = i
            while j < len(cmdline) and cmdline[j] == "\\":
                j += 1
            n = j - i
            if j < len(cmdline) and cmdline[j] == '"':
                current.append("\\" * (n // 2))
                if n % 2:
                    current.append('"')
                    j += 1
            else:
                current.append("\\" * n)
            i, in_arg = j, True
        elif c == '"':
            quoted, in_arg, i = not quoted, True, i + 1
        elif c in " \t" and not quoted:
            if in_arg:
                args.append("".join(current))
                current, in_arg = [], False
            i += 1
        else:
            current.append(c)
            in_arg, i = True, i + 1
    if in_arg:
        args.append("".join(current))
    return args


def expected_argv(app_root, python):
    return ["claude", "mcp", "add", mcp_server.SERVER_NAME, "--env", f"PYTHONPATH={app_root}", "--", python,
            "-P", "-m", "orchestrator.cli", "-q", "mcp", "serve"]


POSIX_ROOTS = ["/opt/LLM Framework", "/home/John Doe/llm_conversion_framework",
               "/home/O'Brien/it's \"quoted\" $(touch pwned) `touch pwned2`; echo hi & | > x"]


class TestClientSnippets:

    @pytest.mark.parametrize("app_root", POSIX_ROOTS)
    def test_posix_command_round_trips_through_shlex(self, monkeypatch, app_root):
        python = "/home/John Doe/.venv/bin/python3"
        monkeypatch.setattr(sys, "executable", python)
        snippets = mcp_server.client_snippets(Path(app_root), windows=False)
        root = str(Path(app_root))  # as the platform spells it
        assert shlex.split(snippets["claude-code"]) == expected_argv(root, python)
        desktop = json.loads(snippets["claude-desktop"])["mcpServers"][mcp_server.SERVER_NAME]
        vscode = json.loads(snippets["vscode"])["servers"][mcp_server.SERVER_NAME]
        for entry in (desktop, vscode):
            assert entry["command"] == python and entry["env"]["PYTHONPATH"] == root

    @pytest.mark.skipif(os.name != "posix" or not shutil.which("sh"), reason="needs a POSIX shell")
    def test_posix_command_means_the_same_in_a_real_shell(self, monkeypatch, tmp_path):
        real_python = sys.executable
        app_root, python = POSIX_ROOTS[2], "/opt/Py $(touch pwned3)/bin/python3"
        monkeypatch.setattr(sys, "executable", python)
        command = mcp_server.client_snippets(Path(app_root), windows=False)["claude-code"]
        # `claude` replaced by a function that prints the arguments the shell really passes
        script = ('claude() { "$PY" -c \'import json, sys; print(json.dumps(sys.argv[1:]))\' "$@"; }\n'
                  + command + "\n")
        result = subprocess.run(["sh", "-c", script], cwd=tmp_path, env=dict(os.environ, PY=real_python),
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert ["claude"] + json.loads(result.stdout) == expected_argv(app_root, python)
        assert not list(tmp_path.glob("pwned*")), "the pasted command ran something else"

    @pytest.mark.parametrize("app_root, python", [
        (r"C:\Program Files\LLM-Conversion-Framework",
         r"C:\Program Files\LLM-Conversion-Framework\.venv\Scripts\python.exe"),
        (r"C:\Users\First Last\LLM Framework" + "\\", r"C:\Users\First Last\AppData\Local\Python312\python.exe"),
        (r"D:\llm", r"D:\llm\.venv\Scripts\python.exe"),
    ])
    def test_windows_variant_uses_double_quotes(self, monkeypatch, app_root, python):
        monkeypatch.setattr(sys, "executable", python)
        command = mcp_server.client_snippets(Path(app_root), windows=True)["claude-code"]
        assert "'" not in command, "single quotes are not quotes for cmd or the C runtime"
        if " " in python:
            assert f'"{python}"' in command
        assert windows_argv(command) == expected_argv(str(Path(app_root)), python)

    def test_default_follows_the_platform(self, monkeypatch):
        argv = ["claude", "x y"]
        monkeypatch.setattr(sys, "platform", "win32")
        assert mcp_server.shell_command(argv) == 'claude "x y"'
        monkeypatch.setattr(sys, "platform", "linux")
        assert mcp_server.shell_command(argv) == "claude 'x y'"


def cli_env(tmp_path, **extra):
    """The environment of tests/test_core.py TestCLI (plus extras)."""
    return dict(os.environ, PYTHONPATH=str(REPO_ROOT), PYTHON_KEYRING_BACKEND="keyring.backends.null.Keyring",
                LLM_FRAMEWORK_HOME=str(tmp_path / "state"), LLM_FRAMEWORK_OUTPUT_DIR=str(tmp_path / "output"),
                LLM_FRAMEWORK_CACHE_DIR=str(tmp_path / "cache"), LLM_FRAMEWORK_MODELS_DIR=str(tmp_path / "models"),
                **extra)


BOX_DRAWING = re.compile("[\u2500-\u259f]")
SECTIONS = ("Claude Code", "Claude Desktop", "VS Code")


def split_command(command):
    """How the shell this snippet is meant for splits it."""
    return windows_argv(command) if sys.platform == "win32" else shlex.split(command)


def parse_mcp_config_output(stdout):
    """{section: text} for the '# <title>' blocks of `llm-cli mcp config` (blank line between blocks)."""
    blocks = {}
    for block in stdout.split("\n\n"):
        title, _, body = block.partition("\n")
        for name in SECTIONS:
            if title.startswith(f"# {name}"):
                assert name not in blocks
                blocks[name] = body
    return blocks


class TestMcpConfigCommand:

    def test_output_is_plain_text_that_parses(self, tmp_path):
        # A narrow terminal used to hard-wrap the Rich panels, with borders inside the JSON.
        result = subprocess.run([sys.executable, "-m", "orchestrator.cli", "-q", "mcp", "config"], cwd=tmp_path,
                                env=cli_env(tmp_path, COLUMNS="40", LINES="20", TERM="xterm-256color"),
                                capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stderr
        out = result.stdout
        assert not BOX_DRAWING.search(out) and "\x1b" not in out
        blocks = parse_mcp_config_output(out)
        assert set(blocks) == set(SECTIONS)
        assert split_command(blocks["Claude Code"]) == expected_argv(str(REPO_ROOT), sys.executable)
        desktop = json.loads(blocks["Claude Desktop"])["mcpServers"][mcp_server.SERVER_NAME]
        assert desktop == mcp_server.launch_command(REPO_ROOT)
        vscode = json.loads(blocks["VS Code"])["servers"][mcp_server.SERVER_NAME]
        assert vscode["type"] == "stdio" and vscode["command"] == sys.executable
        assert out.rstrip().endswith("the framework never sees it.")

    def test_paths_with_spaces_survive_the_command(self, monkeypatch):
        from click.testing import CliRunner
        from orchestrator import cli as cli_module
        python = "/opt/My Python 3.12/bin/python3"
        monkeypatch.setattr(sys, "executable", python)
        result = CliRunner().invoke(cli_module.cli, ["-q", "mcp", "config"], obj=cli_module.FrameworkContext(),
                                    env={"COLUMNS": "30"})
        assert result.exit_code == 0, result.output
        blocks = parse_mcp_config_output(result.stdout)
        assert split_command(blocks["Claude Code"]) == expected_argv(str(REPO_ROOT), python)
        assert json.loads(blocks["Claude Desktop"])["mcpServers"][mcp_server.SERVER_NAME]["command"] == python

    def test_write_claude_desktop_via_the_cli(self, tmp_path, monkeypatch):
        monkeypatch.setenv("APPDATA", str(tmp_path / "home" / "AppData" / "Roaming"))
        from click.testing import CliRunner
        from orchestrator import cli as cli_module
        path = mcp_server.claude_desktop_config_path()
        assert str(path).startswith(str(tmp_path)), "conftest points HOME at tmp_path"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"mcpServers": {"github": {"command": "gh", "env": {"GITHUB_TOKEN": "ghp_x"}}}}))
        result = CliRunner().invoke(cli_module.cli, ["-q", "mcp", "config", "--write", "claude-desktop"],
                                    obj=cli_module.FrameworkContext())
        assert result.exit_code == 0, result.output
        data = json.loads(path.read_text())
        assert set(data["mcpServers"]) == {"github", mcp_server.SERVER_NAME}

    def test_broken_desktop_config_is_refused_via_the_cli(self, tmp_path, monkeypatch):
        monkeypatch.setenv("APPDATA", str(tmp_path / "home" / "AppData" / "Roaming"))
        from click.testing import CliRunner
        from orchestrator import cli as cli_module
        path = mcp_server.claude_desktop_config_path()
        assert str(path).startswith(str(tmp_path))
        path.parent.mkdir(parents=True)
        path.write_text("{not json")
        result = CliRunner().invoke(cli_module.cli, ["-q", "mcp", "config", "--write", "claude-desktop"],
                                    obj=cli_module.FrameworkContext())
        assert result.exit_code == 1 and "not valid JSON" in result.output
        assert path.read_text() == "{not json" and sorted(p.name for p in path.parent.iterdir()) == [path.name]


# --- Claude Desktop config file -------------------------------------------------------------------

def mode_of(path):
    return stat.S_IMODE(os.lstat(path).st_mode)


@pytest.fixture
def umask():
    def set_umask(value):
        previous.append(os.umask(value))
    previous = []
    yield set_umask
    if previous:
        os.umask(previous[0])


ORIGINAL = {"mcpServers": {"github": {"command": "npx", "env": {"GITHUB_TOKEN": "ghp_secret_token"}}},
            "globalShortcut": "Ctrl+Space"}


def write_original(path, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(ORIGINAL, indent=4).encode() + b"\n"
    path.write_bytes(raw)
    os.chmod(path, mode)
    return raw


def assert_updated(path):
    data = json.loads(Path(path).read_text())
    assert data["globalShortcut"] == "Ctrl+Space"
    assert data["mcpServers"]["github"] == ORIGINAL["mcpServers"]["github"]
    assert data["mcpServers"][mcp_server.SERVER_NAME] == mcp_server.launch_command(REPO_ROOT)


@pytest.mark.skipif(os.name != "posix", reason="POSIX modes and symlinks")
class TestClaudeDesktopConfig:

    @pytest.mark.parametrize("mask", [0o022, 0o002, 0o000])
    def test_backup_is_private_whatever_the_umask(self, tmp_path, umask, mask):
        umask(mask)
        path = tmp_path / "Claude" / "claude_desktop_config.json"
        raw = write_original(path)
        assert mcp_server.write_claude_desktop_config(REPO_ROOT, path) == path
        backup = path.with_name(path.name + ".bak")
        assert backup.read_bytes() == raw, "the backup is the exact original"
        assert mode_of(backup) == 0o600 and mode_of(path) == 0o600
        assert_updated(path)
        assert sorted(p.name for p in path.parent.iterdir()) == sorted([path.name, backup.name]), "no temp files"

    def test_existing_readable_backup_becomes_private(self, tmp_path, umask):
        umask(0o022)
        path = tmp_path / "claude_desktop_config.json"
        raw = write_original(path, 0o644)
        backup = path.with_name(path.name + ".bak")
        backup.write_text("old backup")
        os.chmod(backup, 0o644)
        mcp_server.write_claude_desktop_config(REPO_ROOT, path)
        assert backup.read_bytes() == raw and mode_of(backup) == 0o600

    def test_symlinked_backup_is_replaced_not_written_through(self, tmp_path):
        path = tmp_path / "claude_desktop_config.json"
        raw = write_original(path)
        victim = tmp_path / "elsewhere" / "victim.txt"
        victim.parent.mkdir()
        victim.write_text("do not touch")
        backup = path.with_name(path.name + ".bak")
        backup.symlink_to(victim)
        mcp_server.write_claude_desktop_config(REPO_ROOT, path)
        assert victim.read_text() == "do not touch"
        assert not backup.is_symlink() and backup.read_bytes() == raw and mode_of(backup) == 0o600

    @pytest.mark.parametrize("kind", ["absolute", "relative", "chain"])
    def test_symlinked_config_is_updated_at_its_target(self, tmp_path, kind):
        dotfile = tmp_path / "dotfiles" / "claude" / "claude_desktop_config.json"
        raw = write_original(dotfile)
        link = tmp_path / "home" / ".config" / "Claude" / "claude_desktop_config.json"
        link.parent.mkdir(parents=True)
        if kind == "absolute":
            link.symlink_to(dotfile)
        elif kind == "relative":
            link.symlink_to(os.path.relpath(dotfile, link.parent))
        else:
            middle = tmp_path / "dotfiles" / "current.json"
            middle.symlink_to(dotfile)
            link.symlink_to(middle)
        pointer = os.readlink(link)
        assert mcp_server.write_claude_desktop_config(REPO_ROOT, link) == link
        assert link.is_symlink() and os.readlink(link) == pointer, "the link stays"
        assert not dotfile.is_symlink()
        assert_updated(dotfile)
        assert_updated(link)
        backup = dotfile.with_name(dotfile.name + ".bak")
        assert backup.read_bytes() == raw and mode_of(backup) == 0o600 and mode_of(dotfile) == 0o600
        assert sorted(p.name for p in link.parent.iterdir()) == [link.name], "nothing written next to the link"
        assert sorted(p.name for p in dotfile.parent.iterdir()) == sorted([dotfile.name, backup.name])
        if kind == "chain":
            assert (tmp_path / "dotfiles" / "current.json").is_symlink()

    @pytest.mark.parametrize("parent_exists", [True, False])
    def test_dangling_symlink_gets_its_target_created(self, tmp_path, parent_exists):
        dotfile = tmp_path / "dotfiles" / "claude_desktop_config.json"
        if parent_exists:
            dotfile.parent.mkdir()
        link = tmp_path / "claude_desktop_config.json"
        link.symlink_to(dotfile)
        mcp_server.write_claude_desktop_config(REPO_ROOT, link)
        assert link.is_symlink() and dotfile.is_file() and mode_of(dotfile) == 0o600
        assert json.loads(dotfile.read_text())["mcpServers"] == {
            mcp_server.SERVER_NAME: mcp_server.launch_command(REPO_ROOT)}
        assert not dotfile.with_name(dotfile.name + ".bak").exists(), "nothing to back up"

    def test_stale_entry_is_replaced_and_everything_else_kept(self, tmp_path):
        path = tmp_path / "claude_desktop_config.json"
        path.write_text(json.dumps({**ORIGINAL, "mcpServers": {
            **ORIGINAL["mcpServers"], mcp_server.SERVER_NAME: {"command": "/old/venv/bin/python", "args": []}}}))
        mcp_server.write_claude_desktop_config(REPO_ROOT, path)
        assert_updated(path)

    @pytest.mark.parametrize("content, message", [("{not json", "not valid JSON"),
                                                  ("[1, 2]", "does not contain a JSON object"),
                                                  ('{"mcpServers": []}', "is not an object")])
    def test_bad_configs_are_left_alone(self, tmp_path, content, message):
        path = tmp_path / "claude_desktop_config.json"
        path.write_text(content)
        with pytest.raises(ValueError, match=message):
            mcp_server.write_claude_desktop_config(REPO_ROOT, path)
        assert path.read_text() == content
        assert not list(tmp_path.glob(".claude_desktop_config.json.*")), "no temp files left"

    @pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux config location")
    def test_default_location_is_created_private(self, tmp_path, umask):
        umask(0o022)
        path = mcp_server.write_claude_desktop_config(REPO_ROOT)
        assert path == tmp_path / "home" / ".config" / "Claude" / "claude_desktop_config.json"
        assert mode_of(path) == 0o600 and not path.with_name(path.name + ".bak").exists()
        assert json.loads(path.read_text())["mcpServers"][mcp_server.SERVER_NAME]["command"] == sys.executable


# --- stdout of `llm-cli mcp serve` belongs to the protocol -----------------------------------------

class FakeRegistryContext:
    """A FrameworkContext whose framework is a stand-in with only a tool registry (no Docker, no keyring)."""

    @staticmethod
    def make(registry):
        from orchestrator import cli as cli_module

        class Ctx(cli_module.FrameworkContext):
            def require_framework(self):
                self.framework_manager = SimpleNamespace(tool_registry=registry)
                return self
        return Ctx()


class TestServeCommand:

    @pytest.fixture
    def served(self, monkeypatch):
        calls = []
        monkeypatch.setattr(mcp_server, "serve", lambda framework, **kwargs: calls.append(kwargs))
        return calls

    def invoke(self, registry):
        from click.testing import CliRunner
        from orchestrator import cli as cli_module
        return CliRunner().invoke(cli_module.cli, ["-q", "mcp", "serve"], obj=FakeRegistryContext.make(registry))

    def test_damaged_policy_is_reported_on_stderr_only(self, registry, served):
        registry.policy_store.path.write_text("{damaged")
        result = self.invoke(registry)
        assert result.exit_code == 0, result.output
        assert result.stdout == "" and "damaged" in result.stderr
        assert served == [{"transport": "stdio", "host": "127.0.0.1", "port": 8765}]

    def test_unconfigured_policy_note_goes_to_stderr(self, registry, served):
        result = self.invoke(registry)
        assert result.exit_code == 0 and result.stdout == ""
        assert "not set up yet" in result.stderr and len(served) == 1

    def test_missing_registry_exits_without_serving(self, served):
        result = self.invoke(None)
        assert result.exit_code == 1 and result.stdout == ""
        assert "tool registry is not available" in result.stderr and served == []


@pytest.fixture
def broken_config(tmp_path):
    path = tmp_path / "broken.yml"
    path.write_text("source_repositories: [unclosed\n  - x: {\n")
    return path


def test_broken_config_file_writes_nothing_to_stdout(tmp_path, broken_config):
    """The reviewer's reproduction: `printf '' | llm-cli -q -c broken.yml mcp serve 2>/dev/null`."""
    result = subprocess.run([sys.executable, "-m", "orchestrator.cli", "-q", "-c", broken_config.name, "mcp",
                             "serve"], cwd=tmp_path, env=cli_env(tmp_path), input="", capture_output=True,
                            text=True, timeout=120)
    assert result.stdout == "", "stdout belongs to the MCP protocol"
    assert "Failed to load config file" in result.stderr, result.stderr


def test_broken_config_file_real_stdio_client(tmp_path, broken_config):
    """`llm-cli -q -c broken.yml mcp serve` as a client starts it: every stdout line must be JSON-RPC."""
    from mcp import StdioServerParameters
    params = StdioServerParameters(command=sys.executable,
                                   args=["-m", "orchestrator.cli", "-q", "-c", str(broken_config), "mcp", "serve"],
                                   env=cli_env(tmp_path), cwd=str(tmp_path))
    problems = []

    async def handler(message):
        if isinstance(message, Exception):
            problems.append(message)

    async def go():
        async with Client(params, read_timeout_seconds=90, message_handler=handler) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            caps = await client.call_tool("get_target_capabilities", {"target": "Rockchip"})
            start = await client.call_tool("start_build", {"model": "HuggingFaceTB/SmolLM2-135M-Instruct",
                                                           "target": "Rockchip", "format": "gguf"})
            return names, caps, start

    names, caps, start = asyncio.run(go())
    assert problems == [], f"non-JSON-RPC output on stdout: {problems}"
    assert {"list_targets", "start_build"} <= names
    assert not caps.is_error and "gguf" in caps.structured_content["formats"]
    assert start.is_error and PERMISSIONS_HINT in text_of(start)


# --- Adversarial pass: cases the tests above do not reach ------------------------------------------

# Default-ignorable code points outside Cc/Cf: zero width (or a blank) in every dialog renderer.
INVISIBLE = ["\ufe0f", "\U000e0100", "\u034f", "\u115f", "\u3164", "\uffa0", "\u17b4", "\u180b"]


def longest_mark_run(text):
    """The longest run of combining marks (they stack on one letter instead of taking space)."""
    longest = run_length = 0
    for ch in text:
        run_length = run_length + 1 if unicodedata.category(ch) in ("Mn", "Me") else 0
        longest = max(longest, run_length)
    return longest


class TestFaithfulRendering:
    """The user must be able to read back exactly the values that run, and nothing may draw over them."""

    @pytest.mark.parametrize("char", INVISIBLE, ids=lambda c: f"U+{ord(c):04X}")
    def test_invisible_characters_are_escaped(self, char):
        message = mcp_server._describe_call(spec_named(), {"model": f"SmolLM2{char}", "use_gpu": True})
        assert char not in message, "the dialog reads 'SmolLM2' for a different value"

    def test_combining_mark_stacks_are_bounded(self):
        # 400 marks below one letter draw a column over the lines that follow it: dataset_path and use_gpu.
        message = mcp_server._describe_call(spec_named(), {"model": "x" + "\u0333" * 400,
                                                           "dataset_path": "/home/user/.ssh/id_ed25519",
                                                           "use_gpu": True})
        assert longest_mark_run(message) <= 30, "more marks in a row than Unicode's stream-safe limit (UAX #15)"

    def test_ordinary_combining_text_stays_readable(self):
        text = "Vie\u0323\u0302t cafe\u0301 \u0915\u093f"  # Vietnamese, decomposed e-acute, Devanagari
        assert f"  model: {text}" in mcp_server._describe_call(spec_named(), {"model": text}).splitlines()

    @pytest.mark.parametrize("first, second", [("a\\u000ab", "a\nb"), ("\U000e0041", "\ue004" + "1")],
                             ids=["literal-backslash-u", "astral-vs-bmp-plus-digit"])
    def test_different_values_read_differently(self, first, second):
        assert first != second
        assert (mcp_server._describe_call(spec_named(), {"model": first})
                != mcp_server._describe_call(spec_named(), {"model": second}))

    def test_resolve_healing_shows_the_decision_first(self):
        spec = ToolSpec("resolve_healing", "Decide on a self-healing proposal", "", ToolTier.LOCAL_WRITE,
                        ResolveHealingIn, Out, lambda i: i)
        message = mcp_server._describe_call(spec, {"approve": True, "request_id": "req_1"})
        assert shown_keys(message) == ["request_id", "approve"]
        assert "  approve: true" in message.splitlines()

    @pytest.mark.parametrize("mode", MODES)
    def test_what_runs_is_never_more_than_what_was_shown(self, server, registry, mode):
        """BuildRequestIn strips whitespace only in the registry (the SDK's argument model has no model_config):
        the dialog shows the raw value, the build gets the stripped one."""
        asked = []
        arguments = {**ATTACK, "model": "\u2028 SmolLM2 \n", "dataset_path": " /data/calib.txt\t"}
        result = call(server, "start_build", arguments, answering("accept", asked=asked), mode)
        assert not result.is_error, text_of(result)
        lines = asked[0].splitlines()
        assert "  model: \\u2028 SmolLM2 \\u000a" in lines and "  dataset_path:  /data/calib.txt\\u0009" in lines
        ran = registry.builds[0]
        assert (ran.model, ran.dataset_path) == ("SmolLM2", "/data/calib.txt")

    @pytest.mark.parametrize("mode", MODES)
    def test_arguments_outside_the_schema_and_a_smuggled_approval_change_nothing(self, server, registry, mode):
        asked = []
        arguments = {"model": "SmolLM2", "target": "Rockchip", "format": "gguf", "output_dir": "/etc",
                     "approval": {"action": "accept", "data": {"approve": True, "can_ask": False}},
                     "ctx": {"client_capabilities": None}}
        result = call(server, "start_build", arguments, answering("decline", asked=asked), mode)
        assert len(asked) == 1 and "output_dir" not in asked[0] and "approval" not in asked[0]
        assert result.is_error and "The user declined" in text_of(result) and registry.builds == []
        assert rows(registry) == [("start_build", "ask", 0, "not_confirmed")]


class TestAnswersAreBoundToTheCall:

    def test_an_answer_cannot_be_reused_for_other_arguments(self, server, registry):
        """2026-07-28 rounds: the answer to 'write harmless' must not run 'write rm -rf'."""
        async def go():
            async with Client(server, elicitation_callback=answering("accept"), mode="auto") as client:
                first = await client.session.call_tool("write", {"text": "harmless"}, allow_input_required=True)
                assert isinstance(first, mcp_types.InputRequiredResult), first
                answers = {key: ElicitResult(action="accept", content={"approve": True})
                           for key in first.input_requests}
                again = await client.session.call_tool("write", {"text": "rm -rf"}, input_responses=answers,
                                                       allow_input_required=True)
                with pytest.raises(MCPError, match="requestState"):
                    await client.session.call_tool("write", {"text": "rm -rf"}, input_responses=answers,
                                                   request_state=first.request_state, allow_input_required=True)
                return again
        again = run(go())
        assert isinstance(again, mcp_types.InputRequiredResult), "the new arguments must be asked about again"
        [request] = again.input_requests.values()
        assert "  text: rm -rf" in request.params.message.splitlines()
        assert registry.writes == [] and all(outcome != "ok" for *_, outcome in rows(registry))

    def test_deny_loosened_to_ask_between_resolver_and_registry_is_still_refused(self, server, registry,
                                                                                monkeypatch):
        registry.policy_store.save(ToolPolicy(configured=True, overrides={"write": Decision.DENY}))
        real_decide, seen = registry.decide, []

        def decide(spec):
            decision = real_decide(spec)
            seen.append(decision)
            if len(seen) == 1:
                registry.policy_store.save(ToolPolicy(configured=True, overrides={"write": Decision.ASK}))
            return decision
        monkeypatch.setattr(registry, "decide", decide)
        asked = []
        result = call(server, "write", {"text": "x"}, answering("accept", asked=asked))
        assert seen == [Decision.DENY, Decision.ASK]
        assert result.is_error and registry.writes == [] and asked == []
        assert "declined" not in text_of(result), "nobody was asked"
        assert rows(registry) == [("write", "ask", None, "not_confirmed")]

    def test_failed_confirmation_request_is_an_audited_refusal(self, server, registry):
        """The client answers elicitation/create with an error (dialog closed, client timeout, broken UI)."""
        async def callback(ctx, params):
            return mcp_types.ErrorData(code=-32603, message="the confirmation window was closed")
        try:
            result = call(server, "write", {"text": "x"}, callback, "legacy")
        except Exception as e:  # the protocol error surfaces as an exception group around MCPError
            assert registry.writes == []
            pytest.fail(f"a JSON-RPC error instead of a tool result, and audit {rows(registry)}: {e!r}")
        assert result.is_error and registry.writes == []
        entries = rows(registry)
        assert len(entries) == 1 and entries[0][:2] == ("write", "ask") and entries[0][3] != "ok"


def outside_double_quotes(command):
    """The characters of a command line that cmd.exe and PowerShell see outside double quotes."""
    out, quoted = [], False
    for ch in command:
        if ch == '"':
            quoted = not quoted
        elif not quoted:
            out.append(ch)
    return "".join(out)


class TestSnippetEdges:

    @pytest.mark.parametrize("app_root, python", [
        # cmd.exe: '&' ends the command, the rest runs as a second command
        (r"C:\R&D\llm-framework", r"C:\R&D\llm-framework\.venv\Scripts\python.exe"),
        # PowerShell (the default in Windows Terminal): "'" opens a string literal up to the next "'"
        (r"C:\Users\O'Brien\llm-framework", r"C:\Users\O'Brien\AppData\Local\Programs\Python\Python312\python.exe"),
    ], ids=["ampersand", "apostrophe"])
    def test_windows_variant_quotes_shell_metacharacters(self, monkeypatch, app_root, python):
        monkeypatch.setattr(sys, "executable", python)
        command = mcp_server.client_snippets(Path(app_root), windows=True)["claude-code"]
        assert windows_argv(command) == expected_argv(str(Path(app_root)), python), "the C runtime view is right"
        assert not set(outside_double_quotes(command)) & set("&|<>^'"), f"unquoted metacharacters: {command}"

    def test_posix_newline_in_a_path_round_trips(self, monkeypatch):
        python = "/opt/py\nthon/bin/python3"
        monkeypatch.setattr(sys, "executable", python)
        command = mcp_server.client_snippets(Path("/srv/llm\nframework"), windows=False)["claude-code"]
        assert shlex.split(command) == expected_argv(str(Path("/srv/llm\nframework")), python)

    def test_mcp_config_without_quiet_keeps_the_banner_off_stdout(self):
        from click.testing import CliRunner
        from orchestrator import cli as cli_module
        result = CliRunner().invoke(cli_module.cli, ["mcp", "config"], obj=cli_module.FrameworkContext(),
                                    env={"COLUMNS": "40"})
        assert result.exit_code == 0, result.output
        assert not BOX_DRAWING.search(result.stdout) and "Framework CLI" in result.stderr
        blocks = parse_mcp_config_output(result.stdout)
        assert set(blocks) == set(SECTIONS)
        assert json.loads(blocks["Claude Desktop"])["mcpServers"][mcp_server.SERVER_NAME]["command"] == sys.executable

    def test_linux_location_follows_xdg_config_home(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        assert mcp_server.claude_desktop_config_path() == tmp_path / "xdg" / "Claude" / "claude_desktop_config.json"


class TestDesktopConfigEdges:

    @pytest.mark.parametrize("previous", [b"the backup of the last good version", None], ids=["backup", "none"])
    @pytest.mark.parametrize("content", ['{"mcpServers": null}', '{"mcpServers": []}'], ids=["null", "list"])
    def test_refused_config_leaves_the_backup_alone(self, tmp_path, content, previous):
        path = tmp_path / "claude_desktop_config.json"
        backup = path.with_name(path.name + ".bak")
        if previous is not None:
            backup.write_bytes(previous)
        path.write_text(content)
        with pytest.raises(ValueError, match="is not an object"):
            mcp_server.write_claude_desktop_config(REPO_ROOT, path)
        assert path.read_text() == content
        assert (backup.read_bytes() if backup.exists() else None) == previous

    def test_writing_twice_keeps_one_entry_and_a_complete_backup(self, tmp_path):
        path = tmp_path / "claude_desktop_config.json"
        write_original(path)
        mcp_server.write_claude_desktop_config(REPO_ROOT, path)
        first = path.read_bytes()
        mcp_server.write_claude_desktop_config(REPO_ROOT, path)
        assert path.read_bytes() == first
        assert path.with_name(path.name + ".bak").read_bytes() == first, "the backup still has every server"
        assert_updated(path)

    def test_empty_config_file_counts_as_empty(self, tmp_path):
        path = tmp_path / "claude_desktop_config.json"
        path.write_bytes(b"")
        mcp_server.write_claude_desktop_config(REPO_ROOT, path)
        assert json.loads(path.read_text()) == {"mcpServers": {mcp_server.SERVER_NAME:
                                                               mcp_server.launch_command(REPO_ROOT)}}
        assert path.with_name(path.name + ".bak").read_bytes() == b""

    def test_unwritable_config_folder_is_reported_cleanly(self, tmp_path, monkeypatch):
        """E.g. ~/.config/Claude owned by root after a sudo install, or a home-manager link into /nix/store."""
        from click.testing import CliRunner
        from orchestrator import cli as cli_module
        monkeypatch.setenv("APPDATA", str(tmp_path / "home" / "AppData" / "Roaming"))
        path = mcp_server.claude_desktop_config_path()
        raw = write_original(path)
        real_mkstemp = tempfile.mkstemp

        def mkstemp(*args, **kwargs):
            if kwargs.get("dir") is not None and Path(kwargs["dir"]) == path.parent:
                raise PermissionError(errno.EACCES, "Permission denied", str(path.parent))
            return real_mkstemp(*args, **kwargs)
        monkeypatch.setattr(mcp_server.tempfile, "mkstemp", mkstemp)
        result = CliRunner().invoke(cli_module.cli, ["-q", "mcp", "config", "--write", "claude-desktop"],
                                    obj=cli_module.FrameworkContext())
        assert path.read_bytes() == raw
        assert isinstance(result.exception, SystemExit) and result.exit_code == 1, repr(result.exception)
        assert "Permission denied" in result.output


def test_verbose_serve_keeps_stdout_for_the_protocol(tmp_path):
    """`printf '' | llm-cli -v mcp serve 2>/dev/null` must print nothing: a client reading stdout to EOF
    would get a line that is not JSON-RPC."""
    pytest.importorskip("litellm")
    result = subprocess.run([sys.executable, "-m", "orchestrator.cli", "-v", "mcp", "serve"], cwd=tmp_path,
                            env=cli_env(tmp_path), input="", capture_output=True, text=True, timeout=120)
    assert result.stdout == "", f"stdout belongs to the MCP protocol: {result.stdout!r}"


# --- Fix pass: cases around the fixes above ---------------------------------------------------------

def read_back(shown):
    """Reads the confirmation notation: '\\u' plus all hex digits after it is one code point, the rest is literal."""
    return re.sub(r"\\u([0-9a-fA-F]+)", lambda m: chr(int(m.group(1), 16)), shown)


TRICKY_VALUES = ["a\\u000ab", "a\nb", "\U000e0041", "\ue004" + "1", "\\U000e0041", "\\U0001f600", "c:\\users\\me",
                 "\\\\server\\share\\", "x\\", "\\\\u", "\\u005c", "\u200b2024", "\u200bCAFE", "\u0301abc",
                 "e" + "\u0301" * 10, "\u200b\u0301", "\\" + "\u200b", "a\u00a0b", "a\u3000b", "\u2800",
                 "SmolLM2\ufe0f", "x" + "\u0333" * 40, "Vie\u0323\u0302t"]


class TestUnambiguousRendering:

    @pytest.mark.parametrize("value", TRICKY_VALUES, ids=lambda v: v.encode("unicode_escape").decode())
    def test_every_rendering_reads_back_as_exactly_its_value(self, value):
        assert read_back(mcp_server._visible(value)) == value

    def test_no_two_values_read_the_same(self):
        shown = [mcp_server._visible(v) for v in TRICKY_VALUES]
        assert len(set(shown)) == len(TRICKY_VALUES)

    @pytest.mark.parametrize("path", [r"C:\Users\O'Brien\models\SmolLM2.gguf", r"\\nas\models\x", "/srv/a\\b"])
    def test_ordinary_backslash_paths_stay_readable(self, path):
        assert mcp_server._visible(path) == path

    @pytest.mark.parametrize("value, shown", [
        ("\u0301xyz", "\\u0301xyz"),             # would sit on the label's separator
        ("\u200b\u0301", "\\u200b\\u0301"),      # would sit on the escape's last hex digit
        ("\u200b2024", "\\u200b\\u0032\\u0030\\u0032\\u0034"),
        ("a\u00a0z", "a\\u00a0z"), ("a\u3000z", "a\\u3000z"), ("\u2800", "\\u2800"),
        ("\u200bab", "\\u200b\\u0061\\u0062"),  # a, b and so on are hex digits
        ("\\u2028", "\\u005cu2028"), ("\\U0001f600", "\\u005cU0001f600"), ("\\Users", "\\Users"),
    ])
    def test_notation(self, value, shown):
        assert mcp_server._visible(value) == shown


class TestWindowsCommandLine:

    @pytest.mark.parametrize("char", list("&|<>^();,@{}#'") + ["\u2019", "\u00a0", "\u2003"],
                             ids=lambda c: f"U+{ord(c):04X}")
    def test_metacharacters_are_quoted_for_cmd_and_powershell(self, char):
        arg = f"C:\\Py{char}thon\\python.exe"
        command = mcp_server.shell_command(["claude", "mcp", "add", "x", "--", arg, "-P"], windows=True)
        assert windows_argv(command) == ["claude", "mcp", "add", "x", "--", arg, "-P"]
        assert char not in outside_double_quotes(command), command

    @pytest.mark.parametrize("char", list('%!$`"') + ["\u201c", "\n"], ids=lambda c: f"U+{ord(c):04X}")
    def test_command_is_withheld_when_no_quoting_protects_it(self, monkeypatch, char):
        python = f"C:\\Py{char}thon\\python.exe"
        monkeypatch.setattr(sys, "executable", python)
        with pytest.raises(ValueError, match="cmd.exe or PowerShell"):
            mcp_server.shell_command(["claude", python], windows=True)
        snippets = mcp_server.client_snippets(Path(r"C:\llm"), windows=True)
        note = snippets["claude-code"]
        assert note.startswith("# ") and "\n" not in note
        assert not set(note) & set("&|<>^%!$`\"'();"), "pasting the note must be harmless in every shell"
        assert json.loads(snippets["claude-desktop"])["mcpServers"][mcp_server.SERVER_NAME]["command"] == python

    def test_posix_variant_is_unaffected(self, monkeypatch):
        python = "/opt/100%/$HOME/`x`/python3"
        monkeypatch.setattr(sys, "executable", python)
        command = mcp_server.client_snippets(Path("/srv/llm"), windows=False)["claude-code"]
        assert shlex.split(command) == expected_argv("/srv/llm", python)


class TestLaunchCommand:

    @pytest.mark.parametrize("root, windows", [("/opt/llm:framework", False), (r"C:\llm;framework", True),
                                               ("llm-framework", False), (r"llm\framework", True)])
    def test_root_that_pythonpath_would_split_or_resolve_in_the_cwd_is_refused(self, root, windows):
        """A relative PYTHONPATH entry (also one produced by the separator) is looked up in the working directory."""
        with pytest.raises(ValueError, match="PYTHONPATH"):
            mcp_server.launch_command(Path(root), windows=windows)
        with pytest.raises(ValueError, match="PYTHONPATH"):
            mcp_server.client_snippets(Path(root), windows=windows)

    def test_refused_root_writes_no_backup(self, tmp_path):
        path = tmp_path / "Claude" / "claude_desktop_config.json"
        raw = write_original(path)
        with pytest.raises(ValueError, match="PYTHONPATH"):
            mcp_server.write_claude_desktop_config(Path("relative-root"), path)
        assert path.read_bytes() == raw and sorted(p.name for p in path.parent.iterdir()) == [path.name]

    def test_mcp_config_reports_a_refused_root_cleanly(self, monkeypatch):
        from click.testing import CliRunner
        from orchestrator import cli as cli_module

        def refuse(app_root, windows=None):
            raise ValueError("The installation path 'x' cannot be put on PYTHONPATH safely")
        monkeypatch.setattr(mcp_server, "client_snippets", refuse)
        result = CliRunner().invoke(cli_module.cli, ["-q", "mcp", "config"], obj=cli_module.FrameworkContext())
        assert isinstance(result.exception, SystemExit) and result.exit_code == 1, repr(result.exception)
        assert "cannot be put on PYTHONPATH" in result.output

    def test_shadowing_modules_in_the_working_directory_are_not_imported(self, tmp_path):
        """Not only orchestrator/: a project's yaml.py or click.py would run inside the server as well."""
        project = tmp_path / "project"
        project.mkdir()
        for name in ("yaml", "click"):
            marker = project / f"{name}-RAN"
            (project / f"{name}.py").write_text(f"import pathlib\npathlib.Path({str(marker)!r}).write_text('x')\n")
        launch = mcp_server.launch_command(REPO_ROOT)
        assert launch["args"][:2] == ["-P", "-m"] and launch["env"] == {"PYTHONPATH": str(REPO_ROOT)}
        env = {k: v for k, v in cli_env(tmp_path).items() if k != "PYTHONSAFEPATH"}
        env.update(launch["env"])
        result = subprocess.run([launch["command"], *launch["args"][:-2], "--version"], cwd=project, env=env,
                                capture_output=True, text=True, timeout=180)
        assert result.returncode == 0 and "version" in result.stdout, result.stderr
        assert not list(project.glob("*-RAN")), "code from the project folder ran"

    def test_real_session_with_the_generated_command_from_a_hostile_project(self, tmp_path):
        """The exact command a client gets, started inside a project that plants orchestrator/ and yaml.py."""
        from mcp import StdioServerParameters
        project = tmp_path / "project"
        (project / "orchestrator").mkdir(parents=True)
        marker = project / "PLANTED-RAN"
        planted = f"import pathlib\npathlib.Path({str(marker)!r}).write_text('x')\n"
        (project / "orchestrator" / "__init__.py").write_text(planted)
        (project / "yaml.py").write_text(planted)
        launch = mcp_server.launch_command(REPO_ROOT)
        env = {**{k: v for k, v in cli_env(tmp_path).items() if k not in ("PYTHONPATH", "PYTHONSAFEPATH")},
               **launch["env"]}
        params = StdioServerParameters(command=launch["command"], args=launch["args"], env=env, cwd=str(project))
        problems = []

        async def handler(message):
            if isinstance(message, Exception):
                problems.append(message)

        async def go():
            async with Client(params, read_timeout_seconds=90, message_handler=handler) as client:
                return {t.name for t in (await client.list_tools()).tools}

        names = asyncio.run(go())
        assert problems == [] and {"list_targets", "start_build"} <= names
        assert not marker.exists(), "code from the project folder ran inside the MCP server"


class TestRefusalsAreFinalAndAudited:

    @pytest.mark.parametrize("mode", MODES)
    def test_yes_under_a_loosened_policy_runs_as_confirmed(self, server, registry, mode):
        async def callback(ctx, params):
            registry.policy_store.save(ToolPolicy(configured=True, overrides={"write": Decision.ALLOW}))
            return ElicitResult(action="accept", content={"approve": True})
        assert not call(server, "write", {"text": "x"}, callback, mode).is_error
        assert registry.writes == ["x"] and rows(registry) == [("write", "allow", 1, "ok")]

    @pytest.mark.parametrize("mode", MODES)
    def test_no_under_a_loosened_policy_is_audited_as_a_persons_no(self, server, registry, mode):
        async def callback(ctx, params):
            registry.policy_store.save(ToolPolicy(configured=True, overrides={"write": Decision.ALLOW}))
            return ElicitResult(action="decline")
        result = call(server, "write", {"text": "x"}, callback, mode)
        assert result.is_error and "The user declined 'Write'" in text_of(result)
        assert rows(registry) == [("write", "allow", 0, "not_confirmed")]

    @pytest.mark.parametrize("content", [{}, {"approve": None}, {"approve": "maybe"}])
    def test_malformed_answer_in_legacy_mode_is_an_audited_refusal(self, server, registry, content):
        async def callback(ctx, params):
            return ElicitResult(action="accept", content=content)
        result = call(server, "write", {"text": "x"}, callback, "legacy")
        assert result.is_error and "not run" in text_of(result) and registry.writes == []
        assert rows(registry) == [("write", "ask", None, "not_confirmed")]

    def test_invalid_arguments_are_audited_as_invalid_input(self, server, registry):
        result = call(server, "echo", {"txt": "x"})
        assert result.is_error
        [entry] = registry.audit.entries(5)
        assert (entry["tool"], entry["decision"], entry["approved"], entry["outcome"]) == (
            "echo", "allow", None, "invalid_input")
        assert "text" in entry["detail"]

    def test_unknown_tools_and_registry_errors_are_audited_once(self, server, registry):
        assert call(server, "no_such_tool", {}).is_error and rows(registry) == []
        registry.policy_store.save(ToolPolicy(configured=True, tiers={ToolTier.LOCAL_WRITE: Decision.DENY}))
        assert call(server, "write", {"text": "x"}).is_error
        assert rows(registry) == [("write", "deny", None, "denied")], "no second row from the SDK layer"

    def test_registry_treats_a_persons_no_as_final(self, registry):
        from orchestrator.tools.registry import ToolContext, ToolDenied
        registry.policy_store.save(ToolPolicy(configured=True, overrides={"write": Decision.ALLOW}))
        with pytest.raises(ToolDenied):
            registry.call("write", {"text": "x"}, ToolContext("mcp"), approved=False)
        assert registry.writes == [] and rows(registry) == [("write", "allow", 0, "not_confirmed")]

    def test_refuse_records_without_running(self, registry):
        from orchestrator.tools.registry import ToolContext
        registry.refuse("write", {"text": "x"}, ToolContext("mcp"), detail="too long to show")
        [entry] = registry.audit.entries(5)
        assert (entry["tool"], entry["decision"], entry["approved"], entry["outcome"]) == (
            "write", "ask", None, "not_confirmed")
        assert entry["detail"] == "too long to show" and registry.writes == []


class TestDesktopConfigLocation:

    def test_relative_xdg_config_home_is_ignored(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_CONFIG_HOME", "relative/config")
        assert mcp_server.claude_desktop_config_path() == (tmp_path / "home" / ".config" / "Claude"
                                                           / "claude_desktop_config.json")

    def test_config_path_that_is_a_folder_is_reported_cleanly(self, tmp_path, monkeypatch):
        from click.testing import CliRunner
        from orchestrator import cli as cli_module
        monkeypatch.setenv("APPDATA", str(tmp_path / "home" / "AppData" / "Roaming"))
        path = mcp_server.claude_desktop_config_path()
        path.mkdir(parents=True)
        result = CliRunner().invoke(cli_module.cli, ["-q", "mcp", "config", "--write", "claude-desktop"],
                                    obj=cli_module.FrameworkContext())
        assert isinstance(result.exception, SystemExit) and result.exit_code == 1, repr(result.exception)
        assert path.is_dir() and list(path.parent.iterdir()) == [path]


LIBRARY_OUTPUT = """
import atexit, logging, os, sys
from orchestrator.cli import cli

library = logging.getLogger("some.library")
library.addHandler(logging.StreamHandler(sys.stdout))
library.setLevel(logging.INFO)
library.propagate = False

def at_exit():
    print("print at exit")
    library.error("log at exit")  # -q: the framework sets every logger to ERROR
    os.write(1, b"fd write at exit\\n")

atexit.register(at_exit)
sys.argv = ["llm-cli", "-q", "mcp", "serve"]
cli()
"""


def test_serve_keeps_any_library_output_off_stdout(tmp_path):
    """Generic, not tied to LiteLLM: a library that logs to a stdout handler, prints or writes to fd 1 at exit."""
    result = subprocess.run([sys.executable, "-P", "-c", LIBRARY_OUTPUT], cwd=tmp_path, env=cli_env(tmp_path),
                            input="", capture_output=True, text=True, timeout=120)
    assert result.stdout == "", f"stdout belongs to the MCP protocol: {result.stdout!r}"
    for line in ("print at exit", "log at exit", "fd write at exit"):
        assert line in result.stderr, result.stderr


# --- Adversarial pass ------------------------------------------------------------------------------

def test_serve_on_windows_never_runs_programs_from_the_working_directory(monkeypatch):
    """The Windows variant of the -P fix: CreateProcess (and shutil.which) look in the working directory before
    PATH unless NoDefaultCurrentDirectoryInExePath is set. Claude Code starts the server inside the project, and
    the framework runs 'docker buildx version' while it starts, so a project's docker.exe would run."""
    from click.testing import CliRunner
    from orchestrator import cli as cli_module
    monkeypatch.setenv("NoDefaultCurrentDirectoryInExePath", "")  # so that the test restores the environment
    monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath")
    seen = []

    def require_framework(self):
        seen.append(os.environ.get("NoDefaultCurrentDirectoryInExePath"))
        sys.exit(1)
    monkeypatch.setattr(cli_module.FrameworkContext, "require_framework", require_framework)
    monkeypatch.setattr(sys, "platform", "win32")
    result = CliRunner().invoke(cli_module.cli, ["-q", "mcp", "serve"], obj=cli_module.FrameworkContext())
    assert seen == ["1"], f"set before the framework starts: {seen} {result.exception!r}"
