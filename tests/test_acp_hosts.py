"""The framework as an ACP client: agents the person picks (Core/acp_hosts.py, Core/acp_client.py).

A fake ACP agent (tests/fixtures/acp/fake_agent.py, built on the SDK's agent side) is started by the client and
writes what it received to a report file: the capabilities of `initialize` (no file system, no terminal), the MCP
stdio entry of `session/new`, its environment and its working folder. The tests also pin the person-only rules (an AI
never adds, installs, signs in or starts an agent), offline mode, the exact-version and checksum rules of the
registry, removal through Core/deletion and the channel label the MCP server gets. Qt-free: they run headless too."""

import ast
import io
import json
import os
import sys
import tarfile
import threading
import time
from pathlib import Path

import pytest

from conftest import REPO_ROOT

acp = pytest.importorskip("acp")

from orchestrator.Core import acp_hosts  # noqa: E402
from orchestrator.Core.acp_client import (AgentHost, AgentSession, AuthMethod, console_argv,  # noqa: E402
                                          looks_secret, run_login_attached)
from orchestrator.Core.acp_hosts import AgentError, AgentStore, parse_registry, platform_key  # noqa: E402

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "acp" / "fake_agent.py"
ACP_HOME = str(Path(acp.__file__).resolve().parent.parent)


class Audit:
    def __init__(self):
        self.rows = []

    def record(self, actor, tool, tier, decision, outcome, **kwargs):
        self.rows.append({"actor": actor, "tool": tool, "outcome": outcome, **kwargs})
        return len(self.rows)


class Vault:
    def __init__(self):
        self.values = {}

    def set_secret(self, name, value):
        self.values[name] = value
        return True

    def get_secret(self, name):
        return self.values.get(name)

    def delete_secret(self, name):
        self.values.pop(name, None)
        return True

    def list_secrets(self):
        return list(self.values)


class Host(AgentHost):
    def __init__(self, answer="allow", form=None):
        self.events = []
        self.questions = []
        self.answer = answer
        self.form = form

    def on_event(self, event):
        self.events.append(event)

    def ask_permission(self, question):
        self.questions.append(question)
        return self.answer

    def ask_form(self, question):
        self.questions.append(question)
        return self.form


def environment(tmp_path, **extra):
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path / "person-home"),
           "OPENAI_API_KEY": "sk-not-for-the-agent", "LCF_MCP_TOKEN": "token-not-for-the-agent",
           "UNRELATED_SETTING": "x"}
    if os.name == "nt":
        env.update({k: os.environ[k] for k in ("SYSTEMROOT", "PATHEXT", "TEMP") if k in os.environ})
    env.update(extra)
    return env


@pytest.fixture
def store(tmp_path):
    return AgentStore(tmp_path / "framework-home", config={"offline_mode": False}, secrets=Vault(), audit=Audit(),
                      environ=environment(tmp_path))


def add_fake(store, tmp_path, name="Fake Agent", local=True, keys=()):
    report = tmp_path / f"{acp_hosts._slug(name)}-report.json"
    preview = store.preview_command(name, sys.executable, [str(FIXTURE), str(report), "--path", ACP_HOME],
                                    local=local, keys=keys)
    assert preview.ok, preview.problems
    store.add(preview, "cli")
    return report


def read(report):
    return json.loads(Path(report).read_text(encoding="utf-8"))


@pytest.fixture
def running(store, tmp_path):
    """A started fake agent with an open session; closed at the end."""
    report = add_fake(store, tmp_path)
    host = Host()
    session = AgentSession(store.launch_spec("Fake Agent", "cli"), host, store, "cli")
    session.start()
    session.new_session()
    yield session, host, report
    session.close()


# --- the protocol ----------------------------------------------------------------------------------------------------

def test_initialize_offers_no_file_system_and_no_terminal(running):
    session, _host, report = running
    sent = read(report)["initialize"][0]
    assert sent["protocol_version"] == 1
    caps = sent["capabilities"]
    assert caps["fs"] == {"readTextFile": False, "writeTextFile": False}
    assert caps["terminal"] is False
    assert caps["elicitation"] == {"form": {}}          # form only: no URL mode
    assert caps["auth"] == {"terminal": True}           # the agent's own login command, in a window the person sees
    assert sent["client"] == "llm-framework"
    assert [m.type for m in session.auth_methods] == ["agent", "terminal"]


def test_session_new_carries_the_frameworks_own_mcp_server(running, store):
    session, _host, report = running
    sent = read(report)["new_session"][0]
    assert sent["cwd"] == str(store.root / "workspaces" / "fake-agent")
    [server] = sent["mcp_servers"]
    assert server["command"] == sys.executable and Path(server["command"]).is_absolute()
    assert server["args"][-2:] == ["mcp", "serve"]
    env = {e["name"]: e["value"] for e in server["env"]}
    assert env["LCF_MCP_CHANNEL"] == "acp-client:Fake Agent"
    assert env["PYTHONPATH"] == str(REPO_ROOT)


def test_a_streamed_answer_reaches_the_host(running):
    session, host, _report = running
    assert session.prompt("hello") == "end_turn"
    kinds = [e.kind for e in host.events]
    assert kinds == ["text", "thought", "tool", "tool", "plan", "text"]
    assert "".join(e.text for e in host.events if e.kind == "text") == "Hello"
    assert [(e.tool_id, e.status) for e in host.events if e.kind == "tool"] == [("t1", "pending"), ("t1", "completed")]
    assert host.events[4].entries == (("Look", "completed"), ("Answer", "pending"))


@pytest.mark.parametrize("answer, chosen", [("allow", "yes"), ("reject", "no"), (None, "cancelled")])
def test_a_permission_question_is_answered_once_never_always(running, store, answer, chosen):
    session, host, report = running
    host.answer = answer
    session.prompt("ask")
    assert read(report)["permission"] == [chosen]       # "always" is never selected
    [question] = host.questions
    assert question.title == "Delete a build" and question.has_always
    assert any("permission" in str(r.get("arguments", {}).get("what", "")) for r in store.audit.rows)


def test_cancel_ends_the_running_turn(running):
    session, _host, report = running
    result = {}
    thread = threading.Thread(target=lambda: result.setdefault("stop", session.prompt("wait")))
    thread.start()
    for _ in range(200):
        try:
            if "wait" in read(report).get("prompt", []):
                break
        except (OSError, ValueError):   # the agent replaces the file at this moment
            pass
        time.sleep(0.05)
    session.cancel()
    thread.join(20)
    assert result["stop"] == "cancelled"
    assert read(report)["cancel"] == ["s-1"]


def test_file_system_requests_are_refused(running):
    session, host, report = running
    session.prompt("fs")
    assert read(report)["fs"] == ["refused -32601"]
    assert any(e.kind == "notice" for e in host.events)


def test_a_form_is_asked_but_a_secret_form_is_declined(running):
    session, host, report = running
    host.form = {"board": "board-1"}
    session.prompt("form")
    session.prompt("secret form")
    answers = read(report)["form"]
    assert answers[0]["action"] == "accept" and answers[0]["content"] == {"board": "board-1"}
    assert answers[1]["action"] == "decline"
    assert len([q for q in host.questions if hasattr(q, "fields")]) == 1   # the secret one never reached the person
    assert looks_secret("api_key") and looks_secret("", "Your Password") and not looks_secret("board", "Board name")


def test_sign_in_of_type_agent_calls_authenticate(running, store):
    session, _host, report = running
    session.authenticate("browser")
    assert read(report)["authenticate"] == ["browser"]
    assert any("signed in" in str(r.get("arguments", {}).get("what", "")) for r in store.audit.rows)


def test_the_process_stops_on_close(store, tmp_path):
    add_fake(store, tmp_path)
    session = AgentSession(store.launch_spec("Fake Agent", "cli"), Host(), store, "cli")
    session.start()
    pid = session.pid
    session.close()
    assert session.process.returncode is not None and session.pid == pid
    whats = [r.get("arguments", {}).get("what", "") for r in store.audit.rows]
    assert any(w.startswith("started (process") for w in whats) and "stopped" in whats


# --- the environment and the working folder --------------------------------------------------------------------------

def test_the_agent_gets_a_clean_environment_with_only_its_own_key(store, tmp_path):
    other = store.preview_command("Other", sys.executable, ["-c", "pass"], local=True, keys=["OTHER_KEY"])
    store.add(other, "cli")
    store.set_key("Other", "OTHER_KEY", "other-secret", "cli")
    report = add_fake(store, tmp_path, keys=["FAKE_KEY"])
    store.set_key("Fake Agent", "FAKE_KEY", "own-secret", "cli")
    session = AgentSession(store.launch_spec("Fake Agent", "cli"), Host(), store, "cli")
    try:
        session.start()
    finally:
        session.close()
    env = read(report)["env"][0]
    assert env["FAKE_KEY"] == "own-secret"
    for leaked in ("OPENAI_API_KEY", "LCF_MCP_TOKEN", "UNRELATED_SETTING", "OTHER_KEY"):
        assert leaked not in env
    allowed = set(acp_hosts.WINDOWS_BASICS if os.name == "nt" else acp_hosts.POSIX_BASICS) | {"FAKE_KEY"}
    # the agent's own interpreter may add its locale coercion (PEP 538): nothing else came from outside
    assert {k.upper() for k in env} - {k.upper() for k in allowed} <= {"LC_CTYPE"}
    assert "own-secret" not in json.dumps([r for r in store.audit.rows])


def test_the_agent_runs_in_its_workspace_under_the_framework_home(running, store, tmp_path):
    _session, _host, report = running
    cwd = Path(read(report)["cwd"][0]).resolve()
    assert cwd == (store.root / "workspaces" / "fake-agent").resolve()
    assert (tmp_path / "person-home").resolve() not in [cwd, *cwd.parents]


def test_registry_settings_never_change_what_runs():
    for name in ("PATH", "LD_PRELOAD", "NODE_OPTIONS", "PYTHONPATH", "LCF_MCP_TOKEN", "HOME", "DYLD_INSERT"):
        with pytest.raises(AgentError):
            acp_hosts.clean_env({name: "x"})
    assert acp_hosts.clean_env({"DISABLE_AUTO_UPDATE": "1"}) == {"DISABLE_AUTO_UPDATE": "1"}


# --- offline mode ----------------------------------------------------------------------------------------------------

def test_offline_mode_refuses_an_agent_that_needs_the_internet(store, tmp_path):
    add_fake(store, tmp_path, name="Cloud Agent", local=False)
    add_fake(store, tmp_path, name="Local Agent", local=True)
    store.config = {"offline_mode": True}
    with pytest.raises(AgentError) as refused:
        store.launch_spec("Cloud Agent", "gui")
    assert refused.value.code == "offline" and "Offline mode" in str(refused.value)
    assert store.launch_spec("Local Agent", "gui").entry.name == "Local Agent"


def test_offline_mode_refuses_the_registry_fetch(store):
    from orchestrator.utils.locality import LocalityRefused
    store.config = {"offline_mode": True}
    asked = []
    with pytest.raises(LocalityRefused):
        store.refresh_registry("cli", fetch=lambda url: asked.append(url) or b"{}")
    assert asked == []


# --- the registry ----------------------------------------------------------------------------------------------------

def registry(agents):
    return json.dumps({"version": "1.0.0", "agents": agents}).encode("utf-8")


def binary_agent(sha256=None, archive="https://downloads.example.com/agent-1.0.tar.gz"):
    target = {"archive": archive, "cmd": "./agent"}
    if sha256:
        target["sha256"] = sha256
    return {"id": "bin-agent", "name": "Binary Agent", "version": "1.0.0", "description": "An agent",
            "license": "MIT", "distribution": {"binary": {platform_key(): target}}}


def test_the_registry_is_fetched_on_request_and_cached(store):
    data = registry([{"id": "npx-agent", "name": "Npx Agent", "version": "2.0.0", "license": "Apache-2.0",
                      "distribution": {"npx": {"package": "@example/agent@2.0.0", "args": ["--acp"]}}}])
    agents = store.refresh_registry("gui", fetch=lambda url: data)
    assert [a.id for a in agents] == ["npx-agent"]
    assert [a.id for a in store.search("npx")] == ["npx-agent"]
    assert store.cached_registry()[1] is not None


def test_npx_runs_only_an_exact_version(store, tmp_path):
    runner = tmp_path / "bin" / ("npx.cmd" if os.name == "nt" else "npx")
    runner.parent.mkdir()
    runner.write_bytes(b"#!/bin/sh\n")
    runner.chmod(0o755)
    store._environ["PATH"] = str(runner.parent)
    store.refresh_registry("cli", fetch=lambda url: registry([
        {"id": "pinned", "name": "Pinned", "version": "1.2.3", "distribution": {"npx": {"package": "pkg@1.2.3"}}},
        {"id": "latest", "name": "Latest", "version": "1", "distribution": {"npx": {"package": "pkg@latest"}}},
        {"id": "range", "name": "Range", "version": "1", "distribution": {"npx": {"package": "pkg@^1.0.0"}}}]))
    pinned = store.preview_registry("pinned")
    assert pinned.ok and pinned.command_line.endswith("--yes pkg@1.2.3")
    for agent_id in ("latest", "range"):
        preview = store.preview_registry(agent_id)
        assert not preview.ok and "exact version" in preview.problems[0]
        with pytest.raises(AgentError):
            store.add(preview, "cli")


def test_a_binary_without_a_checksum_is_refused(store):
    store.refresh_registry("cli", fetch=lambda url: registry([binary_agent()]))
    preview = store.preview_registry("bin-agent")
    assert not preview.ok and any("no checksum" in p for p in preview.problems)
    with pytest.raises(AgentError):
        store.add(preview, "cli")
    assert store.list() == []


def _archive_bytes():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        body = b"#!/bin/sh\necho agent\n"
        info = tarfile.TarInfo("agent")
        info.size = len(body)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(body))
    return buffer.getvalue()


class FakeFetcher:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def remote_size(self, url):
        return len(self.data)

    def fetch(self, url, dest, size, sha256, progress=None):
        from orchestrator.utils.download import IntegrityError
        from orchestrator.utils.helpers import sha256_bytes
        self.calls.append((url, sha256))
        if sha256_bytes(self.data) != sha256:
            raise IntegrityError("does not match its pin")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.data)
        return dest


def test_a_binary_with_its_checksum_is_installed(store):
    from orchestrator.utils.helpers import sha256_bytes
    data = _archive_bytes()
    store.refresh_registry("cli", fetch=lambda url: registry([binary_agent(sha256_bytes(data))]))
    preview = store.preview_registry("bin-agent")
    assert preview.ok, preview.problems
    assert preview.download.startswith("https://") and preview.sha256 == sha256_bytes(data)
    fetcher = FakeFetcher(data)
    entry = store.add(preview, "gui", fetcher=fetcher)
    assert fetcher.calls == [(preview.download, preview.sha256)]
    program = Path(entry.command)
    assert program.is_file() and (store.root / "bin" / "bin-agent") in program.parents


def test_a_binary_with_a_wrong_checksum_is_not_installed(store):
    data = _archive_bytes()
    store.refresh_registry("cli", fetch=lambda url: registry([binary_agent("0" * 64)]))
    with pytest.raises(AgentError):
        store.add(store.preview_registry("bin-agent"), "gui", fetcher=FakeFetcher(data))
    assert store.list() == []


def test_a_registry_that_is_no_registry_is_refused():
    with pytest.raises(AgentError):
        parse_registry(b"not json")
    with pytest.raises(AgentError):
        parse_registry(b'{"version": "2.0.0", "agents": []}')
    assert parse_registry(registry([{"id": "../evil", "name": "x", "distribution": {}}])) == []


# --- person-only -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("actor", ["mcp", "mcp:client", "ditto", "framework", "", None])
def test_an_ai_can_never_add_install_sign_in_or_start_an_agent(store, tmp_path, actor):
    add_fake(store, tmp_path)
    preview = store.preview_command("Second", sys.executable, ["-c", "pass"], local=True)
    with pytest.raises(PermissionError):
        store.add(preview, actor)
    with pytest.raises(PermissionError):
        store.launch_spec("Fake Agent", actor)
    with pytest.raises(PermissionError):
        store.refresh_registry(actor, fetch=lambda url: b"{}")
    with pytest.raises(PermissionError):
        store.set_key("Fake Agent", "FAKE_KEY", "x", actor)
    assert [e.name for e in store.list()] == ["Fake Agent"]


def test_no_tool_of_the_registry_reaches_the_agents():
    """The registry guard: no tool module, no tool registry module and no MCP or Ditto code imports the agents'
    modules; only the person's windows (orchestrator/gui) and the command line do."""
    allowed = {"orchestrator/cli.py"}
    found = []
    for path in (REPO_ROOT / "orchestrator").rglob("*.py"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith(("orchestrator/gui/", "orchestrator/Core/acp_")) or rel in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = [node.module or ""] if isinstance(node, ast.ImportFrom) else \
                [a.name for a in node.names] if isinstance(node, ast.Import) else []
            if any(n.startswith(("orchestrator.Core.acp_hosts", "orchestrator.Core.acp_client")) for n in names):
                found.append(rel)
    assert found == []


# --- removing --------------------------------------------------------------------------------------------------------

def test_removing_an_agent_lists_everything_and_takes_its_keys(store, tmp_path):
    from orchestrator.Core.deletion import accept
    add_fake(store, tmp_path, keys=["FAKE_KEY"])
    store.set_key("Fake Agent", "FAKE_KEY", "own-secret", "cli")
    store.workspace(store.get("Fake Agent"))
    plan = store.deletion_plan(["Fake Agent"])
    assert {i.what.split(" of the")[0] for i in plan.items} >= {"working folder", "the entry"}
    with pytest.raises(PermissionError):
        store.delete(plan)                       # a plain plan nobody accepted
    result = store.delete(accept(plan, "cli", "permanent"))
    assert "Fake Agent" in result.done and store.list() == []
    assert store.secrets.values == {}
    assert not (store.root / "workspaces" / "fake-agent").exists()
    assert any(r["outcome"] == "started" and "removed" in r.get("detail", "") for r in store.audit.rows)


# --- the terminal sign-in --------------------------------------------------------------------------------------------

def test_the_terminal_sign_in_runs_the_agents_own_login_without_vault_keys(store, tmp_path):
    add_fake(store, tmp_path, keys=["FAKE_KEY"])
    store.set_key("Fake Agent", "FAKE_KEY", "own-secret", "cli")
    spec = store.launch_spec("Fake Agent", "cli")
    calls = []

    def runner(argv, env=None, cwd=None, check=False):
        calls.append((argv, env, cwd))
        return type("R", (), {"returncode": 0})()

    method = AuthMethod(id="console", name="Console", type="terminal", args=("--login",), env=(("FAKE_LOGIN", "1"),))
    assert run_login_attached(spec, method, store, runner=runner) == 0
    [(argv, env, cwd)] = calls
    assert argv[-1] == "--login" and env["FAKE_LOGIN"] == "1" and "FAKE_KEY" not in env
    assert Path(cwd) == spec.cwd


def test_a_console_window_uses_a_terminal_program_found_on_this_computer():
    found = console_argv(["agent", "--login"], which=lambda name: f"/usr/bin/{name}" if name == "xterm" else None,
                         system="Linux")
    assert found == ["/usr/bin/xterm", "-e", "agent", "--login"]
    assert console_argv(["agent"], which=lambda name: None, system="Linux") is None


# --- the MCP side: the channel label ---------------------------------------------------------------------------------

def test_the_channel_label_of_an_agents_mcp_server():
    from orchestrator.tools.mcp_env import channel_label, checked_channel
    assert channel_label("Gemini CLI") == "acp-client:Gemini CLI"
    assert channel_label("a\nb;rm") == "acp-client:a_b_rm"
    assert checked_channel("acp-client:Gemini CLI") == "acp-client:Gemini CLI"
    assert checked_channel("mcp:other") == "" and checked_channel("acp-client:a\nb") == ""


def test_the_mcp_client_label_carries_the_channel():
    pytest.importorskip("mcp")
    from orchestrator.mcp_server import ClientLabel, ClientTracker
    label = ClientLabel("claude-agent", "1.0", "2026-07-28", "stdio", "none", (), channel="acp-client:Claude")
    assert label.describe().startswith("acp-client:Claude: claude-agent 1.0")
    assert ClientTracker("stdio", channel="acp-client:Claude").channel == "acp-client:Claude"
    assert ClientTracker("http", channel="acp-client:Claude").channel == ""
    assert ClientLabel("x", "", "", "stdio", "none", ()).describe() == "x (self-reported) over stdio"


# --- review findings (cs-acp-client-v) -------------------------------------------------------------------------------

def test_an_agent_without_allow_once_is_never_told_allowed_and_the_audit_says_rejected(running, store):
    """The agent offers only "always" and "reject": the person's question has no allow option, the client sends the
    rejection and the audit records what was sent, never "allowed once"."""
    session, host, report = running
    host.answer = "allow"
    session.prompt("ask always")
    assert read(report)["permission"] == ["no"]
    [question] = host.questions
    assert question.allow_option == "" and question.has_always
    whats = [str(r.get("arguments", {}).get("what", "")) for r in store.audit.rows]
    assert any(w.startswith("permission rejected") for w in whats)
    assert not any(w.startswith("permission allowed") for w in whats)


def test_an_agent_that_does_not_answer_initialize_is_stopped(store, tmp_path, monkeypatch):
    """A start that fails (here: no answer in time) leaves no agent process running."""
    from orchestrator.Core import acp_client
    monkeypatch.setattr(acp_client, "START_TIMEOUT", 1.0)
    preview = store.preview_command("Silent", sys.executable, ["-c", "import time; time.sleep(60)"], local=True)
    store.add(preview, "cli")
    session = AgentSession(store.launch_spec("Silent", "cli"), Host(), store, "cli")
    try:
        with pytest.raises(AgentError):
            session.start()
        assert session.process is not None
        for _ in range(100):
            if session.process.returncode is not None:
                break
            time.sleep(0.05)
        assert session.process.returncode is not None and not session.running()
    finally:
        session.close()


def _try(call):
    try:
        return call()
    except Exception as e:  # noqa: BLE001
        return e


def test_a_call_after_close_is_refused_instead_of_waiting_forever(store, tmp_path):
    add_fake(store, tmp_path)
    session = AgentSession(store.launch_spec("Fake Agent", "cli"), Host(), store, "cli")
    session.start()
    session.new_session()
    session.close()
    result = {}
    thread = threading.Thread(target=lambda: result.setdefault("r", _try(lambda: session.prompt("hello"))),
                              daemon=True)
    thread.start()
    thread.join(10)
    assert not thread.is_alive() and isinstance(result["r"], AgentError)


def test_a_form_whose_message_asks_for_a_secret_is_declined_and_short_words_are_whole_words():
    from types import SimpleNamespace

    from orchestrator.Core.acp_client import form_question
    schema = SimpleNamespace(properties={"value": SimpleNamespace(type="string", title="Value")})
    assert form_question("Paste your API token here", schema) is None
    assert form_question("Which board?", schema) is not None
    assert looks_secret("PIN") and looks_secret("one-time OTP") and looks_secret("accessToken")
    for harmless in ("Board mapping", "Shipping", "Spinner speed", "Footprint", "Opinion"):
        assert not looks_secret(harmless), harmless


@pytest.mark.skipif(os.name == "nt", reason="the display variables of a POSIX desktop")
def test_the_agent_and_its_console_sign_in_get_the_desktop_display(store, tmp_path):
    """The browser sign-in (OAuth with the provider's second factor) and the console window need the person's
    display; the display variables are no secret and go along, nothing else of the framework's environment."""
    from orchestrator.Core.acp_client import login_env
    display = {"DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0", "XDG_RUNTIME_DIR": str(tmp_path / "run"),
               "XAUTHORITY": str(tmp_path / "xauth"), "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1/bus"}
    store._environ = environment(tmp_path, **display)
    add_fake(store, tmp_path)
    spec = store.launch_spec("Fake Agent", "cli")
    for name, value in display.items():
        assert spec.env[name] == value
    method = AuthMethod(id="console", name="Console", type="terminal", args=("--login",))
    env = login_env(spec, method, store)
    assert env["DISPLAY"] == ":0" and "OPENAI_API_KEY" not in env and "LCF_MCP_TOKEN" not in env
