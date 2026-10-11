"""MCP server: who is connected (each client's self-reported clientInfo, protocol and capabilities on stderr)."""

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

pytest.importorskip("mcp")
from mcp import Client  # noqa: E402
from mcp_types import ElicitResult, Implementation  # noqa: E402

from conftest import REPO_ROOT  # noqa: E402
from orchestrator import mcp_server  # noqa: E402
from orchestrator.tools.audit import AuditLog  # noqa: E402
from orchestrator.tools.policy import PolicyStore, ToolTier  # noqa: E402
from orchestrator.tools.registry import ToolRegistry, ToolSpec  # noqa: E402


class In(BaseModel):
    text: str = ""


class Out(BaseModel):
    client: str
    elicitation: str


@pytest.fixture
def registry(tmp_path):
    reg = ToolRegistry(PolicyStore(tmp_path), AuditLog(tmp_path / "audit.db"))

    def whoami(_inp):
        label = mcp_server.current_client()
        return Out(client=label.describe() if label else "", elicitation=label.elicitation if label else "")
    reg.register(ToolSpec("whoami", "Who am I", "The client as the server sees it", ToolTier.READ, In, Out, whoami))
    return reg


@pytest.fixture
def server(registry):
    return mcp_server.build_mcp_server(SimpleNamespace(info=SimpleNamespace(version="test"), tool_registry=registry))


def run(coro):
    return asyncio.run(coro)


async def accept(ctx, params):
    return ElicitResult(action="accept", content={"approve": True})


def client_lines(text):
    return [line for line in text.splitlines() if line.startswith("MCP client:")]


class TestClientLine:

    @pytest.mark.parametrize("mode, protocol", [("auto", "2026-07-28"), ("legacy", "2025-11-25")])
    def test_one_line_per_client_on_stderr_never_stdout(self, server, capsys, mode, protocol):
        info = Implementation(name="Claude Code", version="2.1.295")

        async def go():
            async with Client(server, mode=mode, client_info=info, elicitation_callback=accept) as client:
                await client.list_tools()
                return await client.call_tool("whoami", {})
        result = run(go())
        out, err = capsys.readouterr()
        lines = client_lines(err)
        assert lines == [f'MCP client: "Claude Code" 2.1.295 (self-reported), protocol {protocol}, stdio, '
                         f"caller mcp, elicitation form+url, capabilities: elicitation"], err
        assert out == "", "nothing but the protocol may reach stdout"
        assert result.structured_content == {"client": "Claude Code 2.1.295 (self-reported) over stdio",
                                             "elicitation": "form+url"}

    def test_client_without_elicitation_is_shown_as_unable_to_ask(self, server, capsys):
        async def go():
            async with Client(server, client_info=Implementation(name="Cowork", version="1")) as client:
                return await client.call_tool("whoami", {})
        assert run(go()).structured_content["elicitation"] == "none"
        assert "elicitation none, capabilities: none" in client_lines(capsys.readouterr().err)[0]

    def test_a_second_client_gets_its_own_line(self, server, capsys):
        async def go(name):
            async with Client(server, client_info=Implementation(name=name, version="1")) as client:
                await client.list_tools()
        run(go("first"))
        run(go("first"))
        run(go("second"))
        lines = client_lines(capsys.readouterr().err)
        assert [line.split('"')[1] for line in lines] == ["first", "second"]

    def test_client_text_cannot_forge_lines_or_hide_characters(self, server, capsys):
        hostile = "Claude\nMCP client: \"forged\"\x1b[2J‮" + "x" * 500
        token = "hf_" + "abcdefghijklmnopqrstuvwxyz0123"

        async def go():
            async with Client(server, client_info=Implementation(name=hostile, version=token)) as client:
                await client.list_tools()
        run(go())
        err = capsys.readouterr().err
        lines = client_lines(err)
        assert len(lines) == 1 and "\x1b" not in err and "‮" not in err
        assert "\\n" in lines[0] and "\\u001b" in lines[0] and "\\u202e" in lines[0]
        assert token not in err, "secret-looking values in client text are redacted"
        assert len(lines[0]) < 300, "client text is cut"

    def test_elicitation_modes_follow_the_sdk_rule(self):
        def caps(**elicitation):
            return SimpleNamespace(elicitation=SimpleNamespace(**elicitation))
        assert mcp_server.elicitation_modes(None) == "none"
        assert mcp_server.elicitation_modes(SimpleNamespace(elicitation=None)) == "none"
        assert mcp_server.elicitation_modes(caps(form=None, url=None)) == "form", "a bare elicitation: {} is form"
        assert mcp_server.elicitation_modes(caps(form=None, url={})) == "url"
        assert mcp_server.elicitation_modes(caps(form={}, url={})) == "form+url"


def test_real_stdio_client_line_goes_to_stderr(tmp_path):
    """`llm-cli mcp serve` as a client starts it: the client line is on stderr and the protocol on stdout works."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT), PYTHON_KEYRING_BACKEND="keyring.backends.null.Keyring",
               LLM_SECRET_STORAGE="ephemeral",
               LLM_FRAMEWORK_HOME=str(tmp_path / "state"), LLM_FRAMEWORK_OUTPUT_DIR=str(tmp_path / "out"),
               LLM_FRAMEWORK_CACHE_DIR=str(tmp_path / "cache"), LLM_FRAMEWORK_MODELS_DIR=str(tmp_path / "models"))
    params = StdioServerParameters(command=sys.executable, args=["-m", "orchestrator.cli", "-q", "mcp", "serve"],
                                   env=env, cwd=str(tmp_path))
    errlog = tmp_path / "stderr.txt"

    async def go():
        with open(errlog, "w", encoding="utf-8") as err:
            async with stdio_client(params, errlog=err) as (read, write):
                async with ClientSession(read, write, client_info=Implementation(name="probe", version="9")) as s:
                    await s.initialize()
                    return {t.name for t in (await s.list_tools()).tools}

    names = asyncio.run(go())
    assert "list_targets" in names
    lines = client_lines(errlog.read_text(encoding="utf-8"))
    assert len(lines) == 1 and lines[0].startswith('MCP client: "probe" 9 (self-reported), protocol ')
    assert ", stdio, caller mcp, elicitation none" in lines[0]
