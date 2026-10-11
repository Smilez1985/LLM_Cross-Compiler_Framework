"""Named MCP tokens and the hardened HTTP transport: hashed store, per-token policy and audit actor, Origin/Host
checks, session limits, LCF_MCP_TOKEN over stdio, and no token in any output."""

import asyncio
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from pydantic import BaseModel

pytest.importorskip("mcp")
from mcp import Client  # noqa: E402
from mcp_types import ElicitResult, Implementation  # noqa: E402

from conftest import REPO_ROOT  # noqa: E402
from orchestrator import mcp_server  # noqa: E402
from orchestrator.tools.audit import AuditLog  # noqa: E402
from orchestrator.tools.mcp_tokens import (TOKEN_PREFIX, TokenStore, TokenStoreError,  # noqa: E402
                                           read_only_policy)
from orchestrator.tools.policy import Decision, PolicyStore, ToolPolicy, ToolTier  # noqa: E402
from orchestrator.tools.registry import ToolContext, ToolDenied, ToolRegistry, ToolSpec  # noqa: E402


class In(BaseModel):
    text: str = "x"


class Out(BaseModel):
    echoed: str


@pytest.fixture
def tokens(tmp_path):
    return TokenStore(tmp_path)


@pytest.fixture
def registry(tmp_path, tokens):
    reg = ToolRegistry(PolicyStore(tmp_path), AuditLog(tmp_path / "audit.db"), tokens=tokens)
    reg.writes = []
    reg.register(ToolSpec("echo", "Echo", "Echo text", ToolTier.READ, In, Out, lambda i: Out(echoed=i.text)))
    reg.register(ToolSpec("write", "Write", "Writes", ToolTier.LOCAL_WRITE, In, Out,
                          lambda i: reg.writes.append(i.text) or Out(echoed="written")))
    return reg


def framework_for(registry, state_dir):
    return SimpleNamespace(info=SimpleNamespace(version="test"), tool_registry=registry,
                           config=SimpleNamespace(user_state_dir=Path(state_dir)))


def run(coro):
    return asyncio.run(coro)


async def accept(ctx, params):
    return ElicitResult(action="accept", content={"approve": True})


def error_text(error: BaseException) -> str:
    """The messages of an error, also inside the exception groups that the SDK's task groups raise."""
    if isinstance(error, BaseExceptionGroup):
        return " | ".join(error_text(inner) for inner in error.exceptions)
    return str(error)


# --- the store ------------------------------------------------------------------------------------------------

class TestTokenStore:

    def test_token_is_shown_once_and_stored_only_as_a_hash(self, tokens):
        token = tokens.add("laptop")
        assert token.startswith(TOKEN_PREFIX) and len(token) > 40
        raw = tokens.path.read_text(encoding="utf-8")
        assert token not in raw and token[len(TOKEN_PREFIX):] not in raw
        assert tokens.find(token).name == "laptop"
        if os.name == "posix":
            assert tokens.path.stat().st_mode & 0o077 == 0

    def test_a_new_token_can_only_read(self, tokens, registry):
        tokens.add("fresh")
        policy = registry.policy("mcp:fresh")
        assert policy.decide("echo", ToolTier.READ) == Decision.ALLOW
        assert policy.decide("write", ToolTier.LOCAL_WRITE) == Decision.DENY
        assert policy.decide("deploy", ToolTier.EXTERNAL) == Decision.DENY

    def test_two_tokens_have_separate_policies(self, tokens, registry):
        tokens.add("one")
        tokens.add("two")
        tokens.set_policy("one", ToolPolicy(configured=True, overrides={"write": Decision.ALLOW}))
        assert registry.decide(registry.get("write"), "mcp:one") == Decision.ALLOW
        assert registry.decide(registry.get("write"), "mcp:two") == Decision.DENY
        assert registry.decide(registry.get("write"), "mcp") == Decision.ASK, "the shared stdio caller is unchanged"

    def test_policy_from_copies_and_link_follows_by_reference(self, tokens, registry):
        registry.policy_store.save(ToolPolicy(configured=True, tiers={ToolTier.LOCAL_WRITE: Decision.ALLOW}))
        tokens.add("copy", policy=registry.policy("mcp").model_copy(deep=True))
        tokens.add("linked", link="ditto")
        write = registry.get("write")
        assert registry.decide(write, "mcp:copy") == registry.decide(write, "mcp:linked") == Decision.ALLOW
        # A change to Ditto's permissions applies to the linked token at once; the copy keeps what it got.
        registry.policy_store.save(ToolPolicy(configured=True, tiers={ToolTier.LOCAL_WRITE: Decision.DENY}))
        assert registry.decide(write, "mcp:linked") == Decision.DENY
        assert registry.decide(write, "mcp:copy") == Decision.ALLOW

    def test_revoked_or_unknown_tokens_get_nothing(self, tokens, registry):
        token = tokens.add("gone")
        tokens.set_policy("gone", ToolPolicy(configured=True, overrides={"write": Decision.ALLOW}))
        tokens.revoke("gone")
        assert tokens.find(token) is None and tokens.get("gone") is None
        for actor in ("mcp:gone", "mcp:never-made"):
            assert registry.decide(registry.get("echo"), actor) == Decision.DENY, "not even reads"
        tokens.add("gone")
        assert registry.decide(registry.get("write"), "mcp:gone") == Decision.DENY, "a new token starts over"

    def test_rotate_keeps_the_policy_and_ends_the_old_token(self, tokens):
        old = tokens.add("box")
        tokens.set_policy("box", ToolPolicy(configured=True, overrides={"write": Decision.ALLOW}))
        new = tokens.rotate("box")
        assert tokens.find(old) is None and tokens.find(new).name == "box"
        assert tokens.get("box").policy.overrides == {"write": Decision.ALLOW}

    def test_a_damaged_store_accepts_no_token_and_denies_everything(self, tokens, registry):
        token = tokens.add("ok")
        tokens.path.write_text("{broken", encoding="utf-8")
        assert tokens.find(token) is None
        assert registry.decide(registry.get("echo"), "mcp:ok") == Decision.DENY
        # "is damaged (": the test's own folder name contains "damaged" as well.
        with pytest.raises(TokenStoreError, match=r"is damaged \("):
            tokens.add("another")

    @pytest.mark.parametrize("name", ["", "UPPER", "with space", "a" * 33, "-lead", "trail-", "ünï", "mcp:x", "a/b"])
    def test_names_are_plain(self, tokens, name):
        with pytest.raises(TokenStoreError, match="not a valid token name"):
            tokens.add(name)

    def test_names_are_unique_and_links_limited(self, tokens):
        tokens.add("same")
        with pytest.raises(TokenStoreError, match="exists already"):
            tokens.add("same")
        with pytest.raises(TokenStoreError, match="linked to"):
            tokens.add("other", link="mcp")

    @pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
    def test_a_store_others_can_read_is_reported(self, tokens):
        tokens.add("x")
        os.chmod(tokens.path, 0o644)
        assert "chmod 600" in tokens.private_problem()

    @pytest.mark.skipif(os.name != "posix", reason="POSIX owners and permission bits")
    def test_a_store_others_can_change_accepts_no_token_while_the_server_runs(self, tokens, registry, monkeypatch):
        # Checked on every read, not only at the start: a store that becomes writable for others (or is replaced by
        # another user's file) while a server runs could hold a token and a policy someone else wrote.
        token = tokens.add("box")
        tokens.set_policy("box", ToolPolicy(configured=True, overrides={"write": Decision.ALLOW}))
        write = registry.get("write")
        assert tokens.find(token) and registry.decide(write, "mcp:box") == Decision.ALLOW
        os.chmod(tokens.path, 0o666)  # the file's size, inode and mtime stay the same: the cache must not answer
        assert tokens.find(token) is None and registry.decide(write, "mcp:box") == Decision.DENY
        with pytest.raises(TokenStoreError, match="chmod 600"):
            tokens.list()
        os.chmod(tokens.path, 0o600)
        assert tokens.find(token).name == "box"
        owner = tokens.path.stat().st_uid
        monkeypatch.setattr(os, "getuid", lambda: owner + 1)
        assert tokens.find(token) is None and registry.decide(write, "mcp:box") == Decision.DENY
        with pytest.raises(TokenStoreError, match="belongs to another user"):
            tokens.list()

    def test_a_bare_token_is_redacted_wherever_it_is_written(self, tokens):
        from orchestrator.utils.helpers import redact_secrets
        token = tokens.add("leaky")
        secret = token[len(TOKEN_PREFIX):]
        for text in (token, f"model {token}", json.dumps({"model": token}), json.dumps("line\n" + token),
                     f"{token}.", f"'{token}'"):
            assert secret not in redact_secrets(text), text


# --- the registry: the token's actor and policy -----------------------------------------------------------------

def test_registry_audits_and_decides_per_token(tokens, registry):
    tokens.add("reader")
    registry.call("echo", {"text": "a"}, ToolContext("mcp:reader"))
    with pytest.raises(ToolDenied):
        registry.call("write", {"text": "b"}, ToolContext("mcp:reader"))
    entries = registry.audit.entries(2)
    assert {e["actor"] for e in entries} == {"mcp:reader"}
    assert {e["outcome"] for e in entries} == {"ok", "denied"} and registry.writes == []


def test_registry_without_a_token_store_denies_token_callers(tmp_path):
    reg = ToolRegistry(PolicyStore(tmp_path))
    reg.register(ToolSpec("echo", "Echo", "Echo", ToolTier.READ, In, Out, lambda i: Out(echoed=i.text)))
    assert reg.decide(reg.get("echo"), "mcp:any") == Decision.DENY


def test_the_framework_registry_has_the_token_store(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from orchestrator.Core import vault_key
    from orchestrator.tools.framework_tools import build_tool_registry
    monkeypatch.setattr(vault_key, "keyring_usable", lambda: False)
    framework = SimpleNamespace(config=SimpleNamespace(user_state_dir=tmp_path), build_service=Mock())
    reg = build_tool_registry(framework)
    assert isinstance(reg.tokens, TokenStore) and reg.tokens.path == tmp_path / TokenStore.FILE_NAME


# --- HTTP: a real server on a free loopback port -----------------------------------------------------------------

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "raw", "version": "1"}}}
JSON_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_http(registry, tokens, tmp_path):
    import uvicorn
    port = free_port()
    server = mcp_server.build_mcp_server(framework_for(registry, tmp_path), transport="http")
    app = mcp_server.http_app(server, tokens, "127.0.0.1", port)
    uv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not uv.started:
        assert thread.is_alive() and time.monotonic() < deadline, "the HTTP server did not start"
        time.sleep(0.05)
    yield SimpleNamespace(url=f"http://127.0.0.1:{port}/mcp", port=port)
    uv.should_exit = True
    thread.join(15)


def post(live, body=None, token=None, headers=None, content=None, method="POST"):
    import httpx
    all_headers = dict(JSON_HEADERS)
    if token is not None:
        all_headers["Authorization"] = f"Bearer {token}"
    all_headers.update(headers or {})
    with httpx.Client(timeout=30) as client:
        return client.request(method, live.url, headers=all_headers, content=content,
                              json=body if content is None and body is not None else None)


async def session_calls(live, token, *calls):
    """A full MCP session over HTTP with this bearer token; returns the results of the tool calls."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared._httpx_utils import create_mcp_http_client
    async with create_mcp_http_client(headers={"Authorization": f"Bearer {token}"}) as http:
        async with streamable_http_client(live.url, http_client=http) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                return [await session.call_tool(name, args) for name, args in calls]


class TestHttp:

    def test_a_named_token_is_the_caller_and_its_policy_applies(self, live_http, tokens, registry):
        reader = tokens.add("reader")
        writer = tokens.add("writer")
        tokens.set_policy("writer", ToolPolicy(configured=True, overrides={"write": Decision.ALLOW}))
        read_only = run(session_calls(live_http, reader, ("echo", {"text": "a"}), ("write", {"text": "b"})))
        allowed = run(session_calls(live_http, writer, ("write", {"text": "c"})))
        assert not read_only[0].is_error and read_only[1].is_error
        assert "llm-cli tools permissions --token reader" in read_only[1].content[0].text
        assert not allowed[0].is_error and registry.writes == ["c"]
        actors = [(e["actor"], e["tool"], e["outcome"]) for e in reversed(registry.audit.entries(10))]
        assert actors == [("mcp:reader", "echo", "ok"), ("mcp:reader", "write", "denied"),
                          ("mcp:writer", "write", "ok")]

    def test_missing_wrong_and_revoked_tokens_get_401(self, live_http, tokens):
        token = tokens.add("soon-gone")
        assert post(live_http, INIT, token).status_code == 200
        tokens.revoke("soon-gone")
        statuses = [post(live_http, INIT, t).status_code for t in (None, "nope", token, TOKEN_PREFIX + "x" * 43)]
        assert statuses == [401, 401, 401, 401]

    def test_the_old_shared_token_file_is_not_accepted(self, live_http, tokens, tmp_path):
        tokens.add("someone")
        legacy = "L" * 43
        (tmp_path / "mcp_http_token").write_text(legacy)
        assert post(live_http, INIT, legacy).status_code == 401

    def test_requests_from_web_pages_fail(self, live_http, tokens):
        token = tokens.add("browserless")
        for origin in ("http://localhost:3000", f"http://127.0.0.1:{live_http.port}", "null", "https://evil.example"):
            assert post(live_http, INIT, token, headers={"Origin": origin}).status_code == 403, origin
        preflight = post(live_http, method="OPTIONS",
                         headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST",
                                  "Access-Control-Request-Headers": "authorization"})
        assert preflight.status_code == 403 and "access-control-allow-origin" not in preflight.headers
        plain = post(live_http, token=token, content=json.dumps(INIT).encode(), headers={"Content-Type": "text/plain"})
        assert plain.status_code == 400, "a text/plain POST (what a form can send) never reaches the server"

    @pytest.mark.parametrize("host", ["evil.example", "evil.example:{port}", "localhost.evil.example:{port}",
                                      "localhost:1{port}", "127.0.0.1"])
    def test_dns_rebinding_hosts_fail(self, live_http, tokens, host):
        token = tokens.add("rebind")
        response = post(live_http, INIT, token, headers={"Host": host.format(port=live_http.port)})
        assert response.status_code == 421

    @pytest.mark.parametrize("host", ["127.0.0.1:{port}", "localhost:{port}", "[::1]:{port}"])
    def test_loopback_hosts_work(self, live_http, tokens, host):
        token = tokens.add("loop")
        assert post(live_http, INIT, token, headers={"Host": host.format(port=live_http.port)}).status_code == 200

    def test_body_limit_is_1_mib(self, live_http, tokens):
        token = tokens.add("big")
        body = dict(INIT, params=dict(INIT["params"], padding="x" * (1024 * 1024)))
        assert post(live_http, body, token).status_code == 413

    def test_at_most_8_sessions(self, live_http, tokens):
        token = tokens.add("many")
        statuses = [post(live_http, dict(INIT, id=i), token).status_code for i in range(9)]
        assert statuses == [200] * 8 + [503]

    def test_one_token_cannot_use_another_tokens_session(self, live_http, tokens):
        first, second = tokens.add("first"), tokens.add("second")
        session = post(live_http, INIT, first).headers["mcp-session-id"]
        listing = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        session_headers = {"mcp-session-id": session, "mcp-protocol-version": "2025-11-25"}
        assert post(live_http, listing, second, headers=session_headers).status_code == 404

    def test_a_pending_question_of_one_token_is_refused_for_another(self, live_http, tokens, registry):
        # 2026-07-28 has no sessions: an answer round carries the server's sealed requestState. It must be bound to
        # the token that got the question, as the SDK binds it to the authenticated principal.
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client
        alice, bob = tokens.add("alice"), tokens.add("bob")
        for name in ("alice", "bob"):
            tokens.set_policy(name, ToolPolicy(configured=True, overrides={"write": Decision.ASK}))
        yes = ElicitResult(action="accept", content={"approve": True})

        async def as_token(token, **call):
            async with create_mcp_http_client(headers={"Authorization": f"Bearer {token}"}) as http:
                async with Client(streamable_http_client(live_http.url, http_client=http), mode="2026-07-28",
                                  elicitation_callback=accept) as client:
                    return await client.session.call_tool("write", {"text": "a"}, **call)

        asked = run(as_token(alice, allow_input_required=True))
        answer = {key: yes for key in asked.input_requests}
        with pytest.raises(BaseException) as refused:
            run(as_token(bob, input_responses=answer, request_state=asked.request_state))
        assert "Invalid or expired requestState" in error_text(refused.value)
        assert registry.writes == []
        done = run(as_token(alice, input_responses=answer, request_state=asked.request_state))
        assert not done.is_error and registry.writes == ["a"]
        assert registry.audit.entries(1)[0]["actor"] == "mcp:alice"

    def test_no_token_in_results_audit_or_logs(self, live_http, tokens, registry, capfd, tmp_path):
        token = tokens.add("quiet")
        results = run(session_calls(live_http, token, ("echo", {"text": "hello"}), ("write", {"text": "x"})))
        post(live_http, INIT, token + "wrong")
        captured = capfd.readouterr()
        texts = [captured.out, captured.err, json.dumps([r.model_dump(mode="json") for r in results])]
        texts += [p.read_bytes().decode("utf-8", "replace") for p in tmp_path.rglob("*") if p.is_file()]
        assert not [t for t in texts if token in t or token[len(TOKEN_PREFIX):] in t]
        assert "caller mcp:quiet" in captured.err, "the client line names the token, never its value"


def test_a_token_pasted_into_an_argument_or_client_name_reaches_no_audit_entry_or_log(registry, tokens, tmp_path,
                                                                                      capsys):
    token = tokens.add("pasted")
    server = mcp_server.build_mcp_server(framework_for(registry, tmp_path))

    async def go():
        async with Client(server, client_info=Implementation(name=f"client {token}", version="1")) as client:
            await client.call_tool("echo", {"text": f"use {token}"})
            await client.call_tool("write", {"text": token})
    run(go())
    secret = token[len(TOKEN_PREFIX):]
    assert secret not in capsys.readouterr().err
    assert secret not in json.dumps(registry.audit.entries(5))


def test_an_http_server_never_falls_back_to_the_shared_caller(registry, tmp_path):
    # Your own permissions allow the write. A server built for HTTP that gets a request without a checked token
    # (here: served in-process, no HTTP request at all) refuses it instead of running it as the shared caller "mcp".
    registry.policy_store.save(ToolPolicy(configured=True, overrides={"write": Decision.ALLOW}))
    server = mcp_server.build_mcp_server(framework_for(registry, tmp_path), transport="http")

    async def go():
        async with Client(server) as client:
            return await client.call_tool("write", {"text": "x"})
    with pytest.raises(BaseException) as refused:
        run(go())
    assert "no checked MCP token" in error_text(refused.value)
    assert registry.writes == [] and not [e for e in registry.audit.entries(5) if e["actor"] == "mcp"]


class TestEachLayerOnItsOwn:
    """TokenAuth and the SDK's transport security each refuse web pages and foreign hosts, so neither depends on
    the other."""

    def asgi(self, tokens, headers, scope_type="http"):
        reached, sent = [], []

        async def inner(scope, receive, send):
            reached.append(scope)

        async def send(message):
            sent.append(message)

        auth = mcp_server.TokenAuth(inner, tokens, mcp_server.allowed_hosts(8765))
        scope = {"type": scope_type, "method": "POST", "path": "/mcp",
                 "headers": [(k.encode(), v.encode()) for k, v in headers]}
        run(auth(scope, None, send))
        status = next((m["status"] for m in sent if m["type"] == "http.response.start"), None)
        return status, reached, sent

    def test_token_auth_refuses_before_the_app_runs(self, tokens):
        token = tokens.add("unit")
        good = [("host", "127.0.0.1:8765"), ("authorization", f"Bearer {token}")]
        assert self.asgi(tokens, good)[0] is None and self.asgi(tokens, good)[1][0]["lcf.mcp_caller"] == "mcp:unit"
        cases = {403: good + [("origin", "http://localhost:3000")],
                 421: [("host", "localhost:9999"), ("authorization", f"Bearer {token}")],
                 401: [("host", "127.0.0.1:8765"), ("authorization", f"Basic {token}")]}
        for status, headers in cases.items():
            got, reached, _ = self.asgi(tokens, headers)
            assert (got, reached) == (status, []), headers
        assert self.asgi(tokens, good + [("host", "127.0.0.1:8765")])[0] == 421, "two Host headers"
        assert self.asgi(tokens, good + [("authorization", f"Bearer {token}")])[0] == 401, "two credentials"
        _, reached, sent = self.asgi(tokens, good, scope_type="websocket")
        assert reached == [] and sent == [{"type": "websocket.close", "code": 1008}]

    def test_the_sdk_layer_allows_no_origin_and_only_loopback_with_the_port(self, registry, tokens, tmp_path):
        server = mcp_server.build_mcp_server(framework_for(registry, tmp_path), transport="http")
        mcp_server.http_app(server, tokens, "127.0.0.1", 9100)
        manager = server.session_manager
        settings = manager.security_settings
        assert settings.enable_dns_rebinding_protection and settings.allowed_origins == []
        assert settings.allowed_hosts == ["127.0.0.1:9100", "localhost:9100", "[::1]:9100"]
        assert manager.max_sessions == 8


class TestServeHttp:

    def test_http_needs_a_named_token(self, registry, tmp_path):
        (tmp_path / "mcp_http_token").write_text("L" * 43)
        with pytest.raises(ValueError, match="llm-cli mcp token add") as refused:
            mcp_server.serve(framework_for(registry, tmp_path), transport="http")
        assert "no longer accepted" in str(refused.value)

    @pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
    def test_a_store_others_can_read_refuses_to_start(self, registry, tokens, tmp_path):
        tokens.add("x")
        os.chmod(tokens.path, 0o640)
        with pytest.raises(ValueError, match="chmod 600"):
            mcp_server.serve(framework_for(registry, tmp_path), transport="http")

    def test_a_damaged_store_refuses_to_start(self, registry, tokens, tmp_path):
        tokens.path.write_text("[]")
        os.chmod(tokens.path, 0o600)  # damaged, not open to others (that is refused first, as it must be)
        with pytest.raises(ValueError, match=r"is damaged \("):
            mcp_server.serve(framework_for(registry, tmp_path), transport="http")


# --- stdio: LCF_MCP_TOKEN -----------------------------------------------------------------------------------------

class TestStdioToken:

    def test_no_variable_is_the_shared_caller(self, tokens):
        assert mcp_server.stdio_caller(tokens, None) == ("mcp", None)

    @pytest.mark.parametrize("value", ["", "   ", "nope", TOKEN_PREFIX + "unknown-value"])
    def test_an_unknown_value_refuses_to_start_without_echoing_it(self, tokens, value):
        tokens.add("real")
        with pytest.raises(ValueError, match="LCF_MCP_TOKEN") as refused:
            mcp_server.stdio_caller(tokens, value)
        assert not value.strip() or value not in str(refused.value)

    def test_the_token_sets_the_caller_and_a_rotate_ends_it(self, tokens, registry, tmp_path, capsys):
        token = tokens.add("desk")
        tokens.set_policy("desk", ToolPolicy(configured=True, overrides={"write": Decision.ALLOW}))
        caller, still_valid = mcp_server.stdio_caller(tokens, token + "\n")
        assert caller == "mcp:desk" and still_valid()
        server = mcp_server.build_mcp_server(framework_for(registry, tmp_path), caller=caller, still_valid=still_valid)

        async def go():
            async with Client(server, elicitation_callback=accept) as client:
                first = await client.call_tool("write", {"text": "ok"})
                tokens.rotate("desk")
                with pytest.raises(Exception, match="revoked or rotated"):
                    await client.call_tool("write", {"text": "late"})
                return first
        assert not run(go()).is_error and registry.writes == ["ok"]
        assert registry.audit.entries(1)[0]["actor"] == "mcp:desk"
        assert "caller mcp:desk" in capsys.readouterr().err

    def test_calls_after_a_revoke_are_refused_and_audited(self, tokens, registry, tmp_path):
        token = tokens.add("desk2")
        caller, still_valid = mcp_server.stdio_caller(tokens, token)
        server = mcp_server.build_mcp_server(framework_for(registry, tmp_path), caller=caller, still_valid=still_valid)

        async def go():
            async with Client(server) as client:
                await client.call_tool("echo", {"text": "before"})
                tokens.revoke("desk2")
                with pytest.raises(Exception, match="revoked or rotated"):
                    await client.call_tool("echo", {"text": "after"})
        run(go())
        latest = registry.audit.entries(1)[0]
        assert (latest["actor"], latest["tool"], latest["decision"], latest["outcome"]) == \
            ("mcp:desk2", "echo", "deny", "denied")
        assert "revoked or rotated" in latest["detail"] and token[len(TOKEN_PREFIX):] not in json.dumps(latest)

    def test_a_revoked_stdio_token_gets_nothing(self, tokens, registry, tmp_path):
        token = tokens.add("gone")
        caller, still_valid = mcp_server.stdio_caller(tokens, token)
        tokens.revoke("gone")
        assert not still_valid()
        assert registry.decide(registry.get("echo"), caller) == Decision.DENY


def _stdio_env(tmp_path, **extra):
    return dict(os.environ, PYTHONPATH=str(REPO_ROOT), PYTHON_KEYRING_BACKEND="keyring.backends.null.Keyring",
                LLM_SECRET_STORAGE="ephemeral",
                LLM_FRAMEWORK_HOME=str(tmp_path / "state"), LLM_FRAMEWORK_OUTPUT_DIR=str(tmp_path / "out"),
                LLM_FRAMEWORK_CACHE_DIR=str(tmp_path / "cache"), LLM_FRAMEWORK_MODELS_DIR=str(tmp_path / "models"),
                **extra)


def test_real_stdio_server_with_lcf_mcp_token(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    token = TokenStore(tmp_path / "state").add("container")
    params = StdioServerParameters(command=sys.executable, args=["-m", "orchestrator.cli", "-q", "mcp", "serve"],
                                   env=_stdio_env(tmp_path, LCF_MCP_TOKEN=token), cwd=str(tmp_path))
    errlog = tmp_path / "stderr.txt"

    async def go():
        with open(errlog, "w", encoding="utf-8") as err:
            async with stdio_client(params, errlog=err) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    caps = await session.call_tool("get_target_capabilities", {"target": "Rockchip"})
                    start = await session.call_tool("start_build", {"model": "HuggingFaceTB/SmolLM2-135M-Instruct",
                                                                    "target": "Rockchip", "format": "gguf"})
                    return caps, start
    caps, start = asyncio.run(go())
    assert not caps.is_error and start.is_error, "a new token can only read"
    entries = AuditLog(tmp_path / "state" / "audit.db").entries(5)
    assert {e["actor"] for e in entries} == {"mcp:container"}
    assert [e["outcome"] for e in reversed(entries)] == ["ok", "denied"]
    err = errlog.read_text(encoding="utf-8")
    assert "caller mcp:container" in err and token not in err


def test_real_stdio_server_refuses_an_unknown_token(tmp_path):
    import subprocess
    TokenStore(tmp_path / "state").add("real")
    value = TOKEN_PREFIX + "made-up-value-0123456789"
    proc = subprocess.run([sys.executable, "-m", "orchestrator.cli", "-q", "mcp", "serve"], cwd=str(tmp_path),
                          env=_stdio_env(tmp_path, LCF_MCP_TOKEN=value), input=b"", capture_output=True, timeout=180)
    assert proc.returncode == 2 and proc.stdout == b""
    assert b"LCF_MCP_TOKEN" in proc.stderr and value.encode() not in proc.stderr


# --- the CLI: only a person at a terminal manages tokens ------------------------------------------------------------

class FakeContext:
    @staticmethod
    def make(registry):
        from orchestrator import cli as cli_module

        class Ctx(cli_module.FrameworkContext):
            def require_framework(self):
                self.framework_manager = SimpleNamespace(tool_registry=registry)
                return self
        return Ctx()


@pytest.fixture
def cli_run(registry, monkeypatch):
    from orchestrator import cli as cli_module

    def invoke(*args, terminal=True, stdout_terminal=True, input=None):
        monkeypatch.setattr(cli_module, "_interactive", lambda: terminal)
        monkeypatch.setattr(cli_module, "_stdout_is_terminal", lambda: stdout_terminal)
        # Without -q: it would leave the process-wide log level at ERROR for the tests after this one.
        return CliRunner().invoke(cli_module.cli, list(args), obj=FakeContext.make(registry), input=input)
    return invoke


def printed_token(output):
    return next(line for line in output.splitlines() if line.startswith(TOKEN_PREFIX))


class TestTokenCommands:

    @pytest.mark.parametrize("terminal, stdout_terminal", [(False, True), (True, False)])
    def test_add_needs_a_person_at_a_terminal(self, cli_run, tokens, terminal, stdout_terminal):
        result = cli_run("mcp", "token", "add", "agent", terminal=terminal, stdout_terminal=stdout_terminal)
        assert result.exit_code == 1 and "needs a person at a terminal" in result.output
        assert TOKEN_PREFIX not in result.output and tokens.list() == []

    def test_add_shows_the_token_once_read_only_and_audits_without_it(self, cli_run, tokens, registry):
        result = cli_run("mcp", "token", "add", "laptop")
        assert result.exit_code == 0, result.output
        token = printed_token(result.output)
        assert tokens.find(token).name == "laptop" and "read: allow, local_write: deny" in result.output
        entry = registry.audit.entries(1)[0]
        assert (entry["actor"], entry["tool"], entry["approved"]) == ("cli", "mcp_token.add", 1)
        assert token not in json.dumps(entry)
        listing = cli_run("mcp", "token", "list", terminal=False, stdout_terminal=False)
        assert listing.exit_code == 0 and "laptop" in listing.output and "mcp:laptop" in listing.output
        assert token not in listing.output and tokens.get("laptop").sha256 not in listing.output

    def test_policy_from_and_link(self, cli_run, tokens, registry):
        registry.policy_store.save(ToolPolicy(configured=True, tiers={ToolTier.LOCAL_WRITE: Decision.ALLOW}))
        assert cli_run("mcp", "token", "add", "copy", "--policy-from", "mcp").exit_code == 0
        assert cli_run("mcp", "token", "add", "remote-ditto", "--link", "ditto").exit_code == 0
        assert cli_run("mcp", "token", "add", "copy2", "--policy-from", "mcp:copy").exit_code == 0
        assert tokens.get("copy").policy.tiers[ToolTier.LOCAL_WRITE] == Decision.ALLOW
        assert tokens.get("copy2").policy.tiers[ToolTier.LOCAL_WRITE] == Decision.ALLOW
        assert tokens.get("remote-ditto").link == "ditto"
        assert cli_run("mcp", "token", "add", "x", "--policy-from", "nobody").exit_code == 2
        assert cli_run("mcp", "token", "add", "y", "--policy-from", "mcp", "--link", "ditto").exit_code == 2

    def test_policy_from_with_the_mcp_prefix_names_only_a_token(self, cli_run, tokens, registry):
        # "mcp:<name>" is the audit actor of a token. A token may be named like a caller ("mcp", "ditto"); its actor
        # must then copy that token's permissions, never the wider ones of the caller with the same name.
        registry.policy_store.save(ToolPolicy(configured=True, tiers={ToolTier.LOCAL_WRITE: Decision.ALLOW}))
        tokens.add("mcp")
        tokens.add("ditto")
        assert cli_run("mcp", "token", "add", "from-mcp", "--policy-from", "mcp:mcp").exit_code == 0
        assert cli_run("mcp", "token", "add", "from-ditto", "--policy-from", "mcp:ditto").exit_code == 0
        assert tokens.get("from-mcp").policy == tokens.get("from-ditto").policy == read_only_policy()
        assert cli_run("mcp", "token", "add", "shared", "--policy-from", "mcp").exit_code == 0
        assert tokens.get("shared").policy.tiers[ToolTier.LOCAL_WRITE] == Decision.ALLOW, "the caller mcp"
        missing = cli_run("mcp", "token", "add", "x", "--policy-from", "mcp:nobody")
        assert missing.exit_code == 2 and tokens.get("x") is None

    def test_rotate_and_revoke(self, cli_run, tokens, registry):
        first = printed_token(cli_run("mcp", "token", "add", "box").output)
        rotated = cli_run("mcp", "token", "rotate", "box")
        second = printed_token(rotated.output)
        assert tokens.find(first) is None and tokens.find(second).name == "box"
        assert cli_run("mcp", "token", "revoke", "box", input="n\n").exit_code == 0 and tokens.get("box")
        assert cli_run("mcp", "token", "revoke", "box", input="y\n").exit_code == 0
        assert tokens.get("box") is None and tokens.find(second) is None
        assert [e["tool"] for e in registry.audit.entries(3)] == ["mcp_token.revoke", "mcp_token.rotate",
                                                                  "mcp_token.add"]
        assert cli_run("mcp", "token", "revoke", "box", terminal=False).exit_code == 1

    def test_token_permissions_are_set_by_a_person(self, cli_run, tokens, registry):
        tokens.add("helper")
        refused = cli_run("tools", "permissions", "--token", "helper", terminal=False, input="allow\nallow\nask\n")
        assert refused.exit_code == 1 and tokens.get("helper").policy == read_only_policy()
        result = cli_run("tools", "permissions", "--token", "helper", input="allow\nallow\nask\n")
        assert result.exit_code == 0, result.output
        assert registry.decide(registry.get("write"), "mcp:helper") == Decision.ALLOW
        assert registry.decide(registry.get("write"), "mcp") == Decision.ASK, "your own permissions are unchanged"
        assert registry.audit.entries(1)[0]["tool"] == "mcp_token.permissions"
        assert cli_run("tools", "permissions", "--token", "helper", "--reset").exit_code == 0
        assert registry.decide(registry.get("write"), "mcp:helper") == Decision.DENY

    def test_serve_reads_lcf_mcp_token_once_and_removes_it(self, registry, monkeypatch):
        from orchestrator import cli as cli_module
        calls = []
        monkeypatch.setattr(mcp_server, "serve", lambda framework, **kwargs: calls.append(kwargs))
        monkeypatch.setenv("LCF_MCP_TOKEN", "value-from-env")
        result = CliRunner().invoke(cli_module.cli, ["mcp", "serve"], obj=FakeContext.make(registry))
        assert result.exit_code == 0 and calls[0]["stdio_token"] == "value-from-env"
        assert "LCF_MCP_TOKEN" not in os.environ, "child processes must not inherit it"
