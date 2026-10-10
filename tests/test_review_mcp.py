"""Regression tests for the MCP fixes of the Phase 1+2 review (commit 1a714ea).

Confirmation text (every argument, security fields first, control and bidi characters escaped, refused
when too long), who approved what (a policy decision is never recorded as a person's confirmation;
decline, cancel and url-only clients are audited), copy-pasteable client snippets, the Claude Desktop
config writer (private backup, symlinks) and a clean stdout for `llm-cli mcp serve`.
"""

import asyncio
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

pytest.importorskip("mcp")
import mcp_types  # noqa: E402
from mcp import Client  # noqa: E402
from mcp.client.session import ClientSession  # noqa: E402
from mcp_types import ElicitResult  # noqa: E402

from conftest import REPO_ROOT  # noqa: E402
from orchestrator import mcp_server  # noqa: E402
from orchestrator.schemas.build import BuildHandle, BuildRequestIn  # noqa: E402
from orchestrator.tools.audit import AuditLog  # noqa: E402
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

    @pytest.mark.xfail(strict=True, reason="BUG: a call refused as too long to confirm leaves no audit entry")
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

    @pytest.mark.xfail(strict=True, reason="BUG: an explicit 'no' in the dialog is overridden when the policy "
                                           "is loosened to allow while the dialog is open")
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

    @pytest.mark.xfail(strict=True, reason="BUG: calls the SDK rejects before the registry (invalid arguments, "
                                           "an answer that does not match the form) leave no audit entry")
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
            "-m", "orchestrator.cli", "-q", "mcp", "serve"]


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
