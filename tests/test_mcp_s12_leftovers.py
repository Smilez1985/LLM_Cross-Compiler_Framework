"""S12 leftovers: MCP tokens managed in the GUI's Tool Permissions dialog (list, add with the token shown once, rotate,
revoke, per-token permissions), a per-token share of the HTTP sessions with a shorter idle timeout, LCF_MCP_TOKEN
taken from the environment by the CLI group and handed only to `mcp serve`, the caller outside the middleware denied
everything, a revoke that closes the token's open streams, and a token cache keyed on the file's bytes."""

import asyncio
import gc
import json
import os
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

pytest.importorskip("mcp")

from orchestrator import mcp_server  # noqa: E402
from orchestrator.tools.audit import AuditLog  # noqa: E402
from orchestrator.tools.mcp_tokens import TokenStore, read_only_policy  # noqa: E402
from orchestrator.tools.policy import Decision, PolicyStore, ToolPolicy, ToolTier  # noqa: E402
from orchestrator.tools.registry import ToolRegistry, ToolSpec  # noqa: E402


class In(BaseModel):
    text: str = "x"


class Out(BaseModel):
    echoed: str


@pytest.fixture(autouse=True)
def collect_widgets_on_the_main_thread():
    """Dialogs without a parent belong to Python: if the garbage collector ran in the HTTP server's thread, it would
    destroy their widgets off the GUI thread and crash the process; collect them here instead (as test_dialogs)."""
    gc.collect()
    yield
    gc.collect()


@pytest.fixture
def tokens(tmp_path):
    return TokenStore(tmp_path)


@pytest.fixture
def registry(tmp_path, tokens):
    reg = ToolRegistry(PolicyStore(tmp_path), AuditLog(tmp_path / "audit.db"), tokens=tokens)
    reg.register(ToolSpec("echo", "Echo", "Echo text", ToolTier.READ, In, Out, lambda i: Out(echoed=i.text)))
    reg.register(ToolSpec("write", "Write", "Writes", ToolTier.LOCAL_WRITE, In, Out, lambda i: Out(echoed="w")))
    return reg


def run(coro):
    return asyncio.run(coro)


# --- GUI ------------------------------------------------------------------------------------------------------------

class TestTokensInTheToolPermissionsDialog:

    @pytest.fixture
    def dialog(self, qapp, registry, monkeypatch):
        from orchestrator.gui import dialogs
        warnings = []
        monkeypatch.setattr(dialogs.QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))
        dialog = dialogs.ToolPermissionsDialog(registry)
        dialog.shown, dialog.warnings = [], warnings
        monkeypatch.setattr(dialog, "_show_token", lambda name, token: dialog.shown.append((name, token)))
        yield dialog
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()

    def test_add_shows_the_token_once_stores_its_hash_and_audits_without_it(self, dialog, registry, tokens):
        assert dialog.add_token("laptop")
        name, token = dialog.shown[0]
        assert name == "laptop" and tokens.find(token).name == "laptop"
        assert token not in tokens.path.read_text(encoding="utf-8")
        entry = registry.audit.entries(1)[0]
        assert (entry["actor"], entry["tool"], entry["approved"]) == ("gui", "mcp_token.add", 1)
        assert token not in json.dumps(entry)
        assert dialog.token_table.item(0, 0).text() == "laptop" and dialog.token_table.item(0, 1).text() == "mcp:laptop"
        assert all(token not in (dialog.token_table.item(0, c).text()) for c in range(5)), "never listed again"
        assert registry.decide(registry.get("write"), caller="mcp:laptop") == Decision.DENY, "a new token only reads"

    def test_start_with_a_copy_or_a_link(self, dialog, registry, tokens):
        registry.policy_store.save(ToolPolicy(configured=True, overrides={"write": Decision.ALLOW},
                                              reviewed=["echo", "write"]))
        assert dialog.add_token("copied", "mcp") and dialog.add_token("remote-ditto", "link")
        assert tokens.get("copied").policy.overrides == {"write": Decision.ALLOW}
        assert tokens.get("remote-ditto").link == "ditto"
        assert not dialog.add_token("copied") and dialog.warnings, "a name exists only once"

    def test_rotate_and_revoke(self, dialog, registry, tokens):
        dialog.add_token("box")
        first = dialog.shown[-1][1]
        assert dialog.rotate_token("box")
        second = dialog.shown[-1][1]
        assert tokens.find(first) is None and tokens.find(second).name == "box"
        assert dialog.revoke_token("box") and tokens.get("box") is None and dialog.token_table.rowCount() == 0
        assert [e["tool"] for e in registry.audit.entries(3)] == ["mcp_token.revoke", "mcp_token.rotate",
                                                                  "mcp_token.add"]

    def test_a_tokens_own_permissions(self, qapp, dialog, registry, tokens):
        from orchestrator.gui.dialogs import ToolPermissionsDialog
        dialog.add_token("helper")
        own = ToolPermissionsDialog(registry, token="helper")
        try:
            assert not hasattr(own, "token_table"), "no token list inside a token's own permissions"
            combo = own.override_combos["write"]
            combo.setCurrentIndex(combo.findData("allow"))
            own.save()
        finally:
            own.close()
        assert registry.decide(registry.get("write"), caller="mcp:helper") == Decision.ALLOW
        assert registry.decide(registry.get("write"), caller="mcp") == Decision.ASK, "your own are unchanged"
        entry = registry.audit.entries(1)[0]
        assert (entry["actor"], entry["tool"]) == ("gui", "mcp_token.permissions")

    def test_the_token_is_shown_read_only(self, qapp):
        from orchestrator.gui.dialogs import TokenShownOnceDialog
        value = "lcf_mcp_" + "q" * 43
        shown = TokenShownOnceDialog("box", value)
        try:
            assert shown.field.text() == value and shown.field.isReadOnly()
        finally:
            shown.close()


# --- HTTP: a share of the sessions per token, a shorter idle timeout, a revoke ends the token's streams ---------------

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "raw", "version": "1"}}}
JSON_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def framework_for(registry, state_dir):
    return SimpleNamespace(info=SimpleNamespace(version="test"), tool_registry=registry,
                           config=SimpleNamespace(user_state_dir=state_dir))


@pytest.fixture
def live_http(registry, tokens, tmp_path, monkeypatch):
    import socket
    import threading
    import time
    import uvicorn
    monkeypatch.setattr(mcp_server.TokenAuth, "CHECK_SECONDS", 0.1)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = mcp_server.build_mcp_server(framework_for(registry, tmp_path), transport="http", events=False)
    app = mcp_server.http_app(server, tokens, "127.0.0.1", port)
    uv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not uv.started:
        assert thread.is_alive() and time.monotonic() < deadline, "the HTTP server did not start"
        time.sleep(0.05)
    yield SimpleNamespace(url=f"http://127.0.0.1:{port}/mcp", port=port, server=server)
    uv.should_exit = True
    thread.join(15)


def post(live, body, token, headers=None):
    import httpx
    with httpx.Client(timeout=30) as client:
        return client.post(live.url, json=body, headers={**JSON_HEADERS, "Authorization": f"Bearer {token}",
                                                         **(headers or {})})


class TestHttpSessions:

    def test_each_token_gets_its_share_of_the_sessions(self, live_http, tokens):
        share = mcp_server.MAX_SESSIONS_PER_TOKEN
        assert share * 2 == mcp_server.MAX_HTTP_SESSIONS
        first, second, third = tokens.add("first"), tokens.add("second"), tokens.add("third")
        statuses = [post(live_http, dict(INIT, id=i), first).status_code for i in range(share + 1)]
        assert statuses == [200] * share + [503], "one client cannot take every session"
        refused = post(live_http, INIT, first)
        assert "share" in refused.text and first not in refused.text
        assert [post(live_http, dict(INIT, id=i), second).status_code for i in range(share)] == [200] * share
        assert post(live_http, INIT, third).status_code == 503, "the server-wide limit still holds"

    def test_requests_in_an_open_session_are_not_counted(self, live_http, tokens):
        token = tokens.add("busy")
        session = post(live_http, INIT, token).headers["mcp-session-id"]
        headers = {"mcp-session-id": session, "mcp-protocol-version": "2025-11-25"}
        listing = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        for _ in range(mcp_server.MAX_SESSIONS_PER_TOKEN + 2):
            assert post(live_http, listing, token, headers).status_code == 200

    def test_sessions_idle_out_sooner_than_the_sdk_default(self, live_http):
        from mcp.server.streamable_http_manager import DEFAULT_SESSION_IDLE_TIMEOUT
        assert live_http.server.session_manager.session_idle_timeout == mcp_server.HTTP_SESSION_IDLE_TIMEOUT
        assert mcp_server.HTTP_SESSION_IDLE_TIMEOUT < DEFAULT_SESSION_IDLE_TIMEOUT
        assert mcp_server.HTTP_SESSION_IDLE_TIMEOUT > 2 * 50, "well above the longest wait (50 s)"

    def test_a_revoke_closes_the_tokens_open_streams(self, live_http, tokens):
        import threading
        import httpx
        token = tokens.add("streamer")
        session = post(live_http, INIT, token).headers["mcp-session-id"]
        headers = {**JSON_HEADERS, "Authorization": f"Bearer {token}", "mcp-session-id": session,
                   "mcp-protocol-version": "2025-11-25", "Accept": "text/event-stream"}
        post(live_http, {"jsonrpc": "2.0", "method": "notifications/initialized"}, token,
             {"mcp-session-id": session, "mcp-protocol-version": "2025-11-25"})
        ended, opened = threading.Event(), threading.Event()

        def listen():
            try:
                with httpx.Client(timeout=httpx.Timeout(30, read=30)) as client:
                    with client.stream("GET", live_http.url, headers=headers) as response:
                        opened.set()
                        for _ in response.iter_bytes():
                            pass
            except Exception:
                pass
            finally:
                opened.set()
                ended.set()
        thread = threading.Thread(target=listen, daemon=True)
        thread.start()
        assert opened.wait(10)
        assert not ended.wait(1), "the stream stays open while the token is valid"
        tokens.revoke("streamer")
        assert ended.wait(10), "the revoked token's stream was closed"

    def test_a_rotated_or_revoked_tokens_sessions_hold_no_share_and_no_slot(self, live_http, tokens):
        """Rotating is what the person does when a token leaked: the client with the new token must get in at once,
        and the old token's sessions must neither count toward the client's share nor keep the server's slots until
        they idle out (10 minutes)."""
        share = mcp_server.MAX_SESSIONS_PER_TOKEN
        old = tokens.add("client")
        sessions = [post(live_http, dict(INIT, id=i), old) for i in range(share)]
        assert [r.status_code for r in sessions] == [200] * share
        new = tokens.rotate("client")
        assert post(live_http, INIT, new).status_code == 200, "the new token is not locked out by the old sessions"
        stale = {"mcp-session-id": sessions[0].headers["mcp-session-id"], "mcp-protocol-version": "2025-11-25"}
        listing = {"jsonrpc": "2.0", "id": 9, "method": "tools/list"}
        assert post(live_http, listing, new, stale).status_code == 404, "a session belongs to the token that opened it"
        tokens.revoke("client")
        first, second = tokens.add("first"), tokens.add("second")
        statuses = [post(live_http, dict(INIT, id=i), token).status_code
                    for token in (first, second) for i in range(share)]
        assert statuses == [200] * (2 * share), "no slot is held by the sessions of a revoked or rotated token"
        assert len(live_http.server.session_manager._server_instances) <= mcp_server.MAX_HTTP_SESSIONS


# --- the caller outside the middleware, LCF_MCP_TOKEN, the token cache ----------------------------------------------

def test_outside_the_middleware_the_caller_gets_nothing(registry, tmp_path):
    assert mcp_server.current_caller() == mcp_server.UNCHECKED_CALLER
    server = mcp_server.build_mcp_server(framework_for(registry, tmp_path), events=False)
    assert run(server.list_tools()) == [], "no request that ClientTracker checked: no tools"
    with pytest.raises(Exception):
        run(server.call_tool("echo", {"text": "a"}))
    entry = registry.audit.entries(1)[0]
    assert (entry["actor"], entry["outcome"]) == (mcp_server.UNCHECKED_CALLER, "denied")
    assert registry.decide(registry.get("echo"), caller=mcp_server.current_caller()) == Decision.DENY


def test_the_cli_group_takes_lcf_mcp_token_from_the_environment_for_every_command(monkeypatch):
    from click.testing import CliRunner
    from orchestrator import cli as cli_module
    served = []
    monkeypatch.setattr(mcp_server, "serve", lambda framework, **kwargs: served.append(kwargs))
    value = "lcf_mcp_" + "v" * 43
    monkeypatch.setenv("LCF_MCP_TOKEN", value)
    result = CliRunner().invoke(cli_module.cli, ["-q", "mcp", "config"])
    assert result.exit_code == 0, result.output
    assert "LCF_MCP_TOKEN" not in os.environ, "no other command, and no child process, ever sees it"
    assert value not in result.output and served == []


def test_the_token_cache_follows_the_files_bytes(tokens):
    from orchestrator.tools.mcp_tokens import TOKEN_PREFIX, token_digest
    old = tokens.add("box")
    assert tokens.find(old).name == "box"  # cached now
    st = os.stat(tokens.path)
    new = TOKEN_PREFIX + "n" * 43
    data = tokens.path.read_bytes().replace(token_digest(old).encode(), token_digest(new).encode())
    with open(tokens.path, "r+b") as f:  # same file, same size: only the bytes differ
        f.write(data)
    os.utime(tokens.path, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert os.stat(tokens.path).st_ino == st.st_ino and os.stat(tokens.path).st_size == st.st_size
    assert tokens.find(old) is None, "a cache by file identity, size and time would still accept the old token"
    assert tokens.find(new).name == "box"
    assert tokens.get("box").policy == read_only_policy()


def test_the_session_subject_says_nothing_about_the_token(tokens):
    """A session is bound to which token of a name opened it through a value derived with a key of this process:
    never the token, never its stored hash, different in every server process."""
    from orchestrator.tools.mcp_tokens import token_digest
    token = tokens.add("subject")
    digest = token_digest(token)
    first = mcp_server.TokenAuth(None, tokens, mcp_server.allowed_hosts(8765))
    second = mcp_server.TokenAuth(None, tokens, mcp_server.allowed_hosts(8765))
    subject = first.subject(digest)
    assert subject == first.subject(digest) and subject != second.subject(digest)
    assert subject not in token and subject not in digest and digest[:16] not in subject
    assert ("mcp:subject", subject) in first.valid_principals()
    tokens.rotate("subject")
    assert ("mcp:subject", subject) not in first.valid_principals(), "a rotated token's sessions are stale"
