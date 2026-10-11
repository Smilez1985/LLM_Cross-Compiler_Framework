"""MCP server: tool listing, permissions via elicitation, HTTP token, client configuration, real stdio."""

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

pytest.importorskip("mcp")
from mcp import Client  # noqa: E402
from mcp_types import ElicitResult  # noqa: E402

from conftest import REPO_ROOT  # noqa: E402
from orchestrator import mcp_server  # noqa: E402
from orchestrator.tools.audit import AuditLog  # noqa: E402
from orchestrator.tools.policy import Decision, PolicyStore, ToolPolicy, ToolTier  # noqa: E402
from orchestrator.tools.registry import ToolRegistry, ToolSpec  # noqa: E402


class In(BaseModel):
    text: str
    count: int = 1


class Out(BaseModel):
    echoed: str


@pytest.fixture
def registry(tmp_path):
    reg = ToolRegistry(PolicyStore(tmp_path), AuditLog(tmp_path / "audit.db"))
    reg.writes = []
    reg.register(ToolSpec("echo", "Echo", "Echo text", ToolTier.READ, In, Out,
                          lambda i: Out(echoed=i.text * i.count), idempotent=True))
    reg.register(ToolSpec("write", "Write", "Writes", ToolTier.LOCAL_WRITE, In, Out,
                          lambda i: reg.writes.append(i.text) or Out(echoed="written")))
    return reg


@pytest.fixture
def server(registry):
    return mcp_server.build_mcp_server(SimpleNamespace(info=SimpleNamespace(version="test"), tool_registry=registry))


def run(coro):
    return asyncio.run(coro)


def text_of(result):
    return " ".join(getattr(c, "text", "") for c in result.content)


def answering(action, approve=True, asked=None):
    async def callback(ctx, params):
        if asked is not None:
            asked.append(params.message)
        return ElicitResult(action=action, content={"approve": approve} if action == "accept" else None)
    return callback


class TestTools:

    def test_tools_carry_schemas_and_annotations(self, server):
        async def go():
            async with Client(server) as client:
                return (await client.list_tools()).tools
        tools = {t.name: t for t in run(go())}
        assert set(tools) == {"echo", "write"}
        assert tools["echo"].input_schema["required"] == ["text"]
        assert "approval" not in tools["echo"].input_schema["properties"], "the confirmation is not an LLM argument"
        assert tools["echo"].output_schema["properties"]["echoed"]["type"] == "string"
        assert tools["echo"].annotations.read_only_hint is True
        assert tools["write"].annotations.read_only_hint is False

    def test_read_tool_returns_structured_content(self, server, registry):
        async def go():
            async with Client(server) as client:
                return await client.call_tool("echo", {"text": "hi", "count": 2})
        result = run(go())
        assert not result.is_error and result.structured_content == {"echoed": "hihi"}
        assert registry.audit.entries(1)[0]["actor"] == "mcp"

    def test_invalid_arguments_are_an_error_result(self, server):
        async def go():
            async with Client(server) as client:
                return await client.call_tool("echo", {"txt": "x"})
        assert run(go()).is_error


class TestPermissions:

    def test_client_without_elicitation_cannot_confirm(self, server, registry):
        async def go():
            async with Client(server) as client:
                return await client.call_tool("write", {"text": "x"})
        result = run(go())
        assert result.is_error and "llm-cli tools permissions" in text_of(result)
        assert registry.writes == []

    @pytest.mark.parametrize("mode", ["auto", "legacy"])
    def test_ask_becomes_an_elicitation(self, server, registry, mode):
        asked = []

        async def go():
            async with Client(server, elicitation_callback=answering("accept", asked=asked), mode=mode) as client:
                return await client.call_tool("write", {"text": "build me"})
        result = run(go())
        assert not result.is_error and registry.writes == ["build me"]
        assert "Write" in asked[0] and "build me" in asked[0], "the user sees what will run"

    @pytest.mark.parametrize("action, approve", [("accept", False), ("decline", None), ("cancel", None)])
    def test_no_means_no(self, server, registry, action, approve):
        async def go():
            async with Client(server, elicitation_callback=answering(action, approve)) as client:
                return await client.call_tool("write", {"text": "x"})
        assert run(go()).is_error
        assert registry.writes == []

    def test_allowed_tools_run_without_asking(self, server, registry):
        registry.policy_store.save(ToolPolicy(configured=True, overrides={"write": Decision.ALLOW}))
        asked = []

        async def go():
            async with Client(server, elicitation_callback=answering("accept", asked=asked)) as client:
                return await client.call_tool("write", {"text": "x"})
        assert not run(go()).is_error and asked == [] and registry.writes == ["x"]

    def test_denied_tools_never_run(self, server, registry):
        registry.policy_store.save(ToolPolicy(configured=True, tiers={ToolTier.LOCAL_WRITE: Decision.DENY}))

        async def go():
            async with Client(server, elicitation_callback=answering("accept")) as client:
                return await client.call_tool("write", {"text": "x"})
        assert run(go()).is_error and registry.writes == []


class TestHttpTransport:

    def test_token_is_private_and_stable(self, tmp_path):
        token = mcp_server.http_token(tmp_path)
        assert len(token) >= 32 and mcp_server.http_token(tmp_path) == token
        if os.name == "posix":
            assert (tmp_path / "mcp_http_token").stat().st_mode & 0o077 == 0

    def test_requests_without_the_token_are_rejected(self, server, tmp_path):
        import httpx
        token = mcp_server.http_token(tmp_path)
        app = mcp_server.http_app(server, token)

        async def go():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
                body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
                missing = await client.post("/mcp", json=body)
                wrong = await client.post("/mcp", json=body, headers={"Authorization": "Bearer nope"})
                return missing.status_code, wrong.status_code
        assert run(go()) == (401, 401)

    def test_only_loopback_is_served(self, registry):
        framework = SimpleNamespace(info=SimpleNamespace(version="t"), tool_registry=registry,
                                    config=SimpleNamespace(user_state_dir=Path(".")))
        with pytest.raises(ValueError, match="loopback"):
            mcp_server.serve(framework, transport="http", host="0.0.0.0")


class TestClientConfiguration:

    def test_snippets_use_this_interpreter(self):
        snippets = mcp_server.client_snippets(REPO_ROOT)
        desktop = json.loads(snippets["claude-desktop"])["mcpServers"][mcp_server.SERVER_NAME]
        assert desktop["command"] == sys.executable and desktop["args"][-2:] == ["mcp", "serve"]
        assert snippets["claude-code"].startswith(f"claude mcp add {mcp_server.SERVER_NAME}")
        assert "servers" in json.loads(snippets["vscode"])

    def test_claude_desktop_config_keeps_other_servers(self, tmp_path):
        path = tmp_path / "claude_desktop_config.json"
        path.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}))
        mcp_server.write_claude_desktop_config(REPO_ROOT, path)
        data = json.loads(path.read_text())
        assert set(data["mcpServers"]) == {"other", mcp_server.SERVER_NAME} and data["theme"] == "dark"
        assert json.loads((tmp_path / "claude_desktop_config.json.bak").read_text())["mcpServers"] == {"other": {"command": "x"}}

    def test_broken_config_is_not_overwritten(self, tmp_path):
        path = tmp_path / "claude_desktop_config.json"
        path.write_text("{not json")
        with pytest.raises(ValueError, match="not valid JSON"):
            mcp_server.write_claude_desktop_config(REPO_ROOT, path)
        assert path.read_text() == "{not json"


def test_real_stdio_server_with_framework_tools(tmp_path):
    """`llm-cli mcp serve` started like a client does it: from another directory, over stdin/stdout."""
    from mcp import StdioServerParameters
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT), PYTHON_KEYRING_BACKEND="keyring.backends.null.Keyring",
               LLM_SECRET_STORAGE="ephemeral",
               LLM_FRAMEWORK_HOME=str(tmp_path / "state"), LLM_FRAMEWORK_OUTPUT_DIR=str(tmp_path / "out"),
               LLM_FRAMEWORK_CACHE_DIR=str(tmp_path / "cache"), LLM_FRAMEWORK_MODELS_DIR=str(tmp_path / "models"))
    params = StdioServerParameters(command=sys.executable, args=["-m", "orchestrator.cli", "-q", "mcp", "serve"],
                                   env=env, cwd=str(tmp_path))

    async def go():
        async with Client(params, read_timeout_seconds=90) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            caps = await client.call_tool("get_target_capabilities", {"target": "Rockchip"})
            start = await client.call_tool("start_build", {"model": "HuggingFaceTB/SmolLM2-135M-Instruct",
                                                           "target": "Rockchip", "format": "gguf"})
            return names, caps, start

    names, caps, start = asyncio.run(go())
    assert {"list_targets", "start_build", "resolve_healing"} <= names
    assert not caps.is_error and set(caps.structured_content["formats"]) == {"gguf", "rkllm", "rknn"}
    assert start.is_error, "starting a build needs a confirmation this client cannot give"
