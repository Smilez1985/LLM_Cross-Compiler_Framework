"""Ditto as an ACP agent (`llm-cli acp`, orchestrator/acp_agent.py), driven end to end by the official SDK's own client
over a real byte stream, with a scripted LLM provider behind the real LLMClient (no network):

initialize (agent info, capabilities, the terminal sign-in while Ditto has no provider), session/new (folder labels,
config options, slash commands), session/prompt with a tool call and the permission round trip (allow and reject, only
once-options), session/cancel, path labels (no host path reaches the provider), the experimental notice, the editor's
MCP servers recorded and never used, builds followed with a plan; and a real `llm-cli acp` process started like an
editor starts it (stdout carries only protocol messages).
"""

import asyncio
import json
import os
import socket
import sys
import threading
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("acp")
pytest.importorskip("anthropic")
pytest.importorskip("openai")

import acp  # noqa: E402
from acp import text_block  # noqa: E402
from acp.schema import AllowedOutcome, DeniedOutcome, McpServerStdio, RequestPermissionResponse  # noqa: E402

from conftest import REPO_ROOT  # noqa: E402
from orchestrator import acp_agent  # noqa: E402
from orchestrator.Core.acp_editor_servers import EditorServers  # noqa: E402
from orchestrator.Core.ditto_manager import DittoCoder  # noqa: E402
from orchestrator.llm import ChatResult, LLMClient, Message, NativeMessage, ToolCall  # noqa: E402
from orchestrator.llm.providers.base import ProviderAdapter  # noqa: E402
from orchestrator.tools.audit import AuditLog  # noqa: E402
from orchestrator.tools.policy import Decision, PolicyStore, ToolPolicy, ToolTier  # noqa: E402
from orchestrator.tools.registry import ToolRegistry, ToolSpec  # noqa: E402

from test_ditto import BuildIn, BuildOut, FakeConfig, FakeSecrets, NoInput, TextOut  # noqa: E402


# --- fakes: the provider, the registry, the editor ------------------------------------------------------------------

@pytest.fixture
def llm(monkeypatch):
    script, requests = [], []

    class ScriptedAdapter(ProviderAdapter):
        family = "scripted"

        def _complete(self, request, model):
            requests.append(request)
            if not script:
                raise AssertionError("unexpected model call")
            item = script.pop(0)
            if callable(item):
                item = item()
            if isinstance(item, BaseException):
                raise item
            return item

    monkeypatch.setattr("orchestrator.llm.client.adapter_class", lambda name: ScriptedAdapter)
    return SimpleNamespace(script=script, requests=requests)


def answer(text="", calls=None):
    calls = calls or []
    msg = Message(role="assistant", content=text, tool_calls=calls,
                  native=NativeMessage(family="scripted", payload={"role": "assistant", "content": text}))
    return ChatResult(text=text, tool_calls=calls, stop="tool_use" if calls else "end", message=msg,
                      provider="ollama", model="test-model")


@pytest.fixture
def registry(tmp_path):
    reg = ToolRegistry(PolicyStore(tmp_path / "state"), AuditLog(tmp_path / "state" / "audit.db"))
    reg.started = []

    def start(inp):
        reg.started.append(inp.model_dump())
        return BuildOut(request_id="req_1a2b3c4d")

    reg.register(ToolSpec("list_targets", "List targets", "Lists targets", ToolTier.READ, NoInput, TextOut,
                          lambda _: TextOut(text="Rockchip")))
    reg.register(ToolSpec("start_build", "Start a build", "Starts a build", ToolTier.LOCAL_WRITE, BuildIn, BuildOut,
                          start))
    reg.policy_store.replace(ToolPolicy(configured=True, overrides={"start_build": Decision.ASK}))
    return reg


def make_ditto(tmp_path, registry, configured=True):
    config = FakeConfig(ai_provider="ollama" if configured else "")
    client = LLMClient(config, FakeSecrets(), state_dir=tmp_path / "state", sleep=lambda s: None)
    ditto = DittoCoder(None, config_manager=config, client=client, registry=registry)
    if not configured:
        ditto.is_configured = lambda task="chat": False
    return ditto


class Editor:
    """The editor side (acp.Client): records every update, answers permission requests from `answers`."""

    def __init__(self, answers=()):
        self.updates, self.permissions = [], []
        self.answers = list(answers)
        self.asked = threading.Event()

    async def session_update(self, session_id, update, **kwargs):
        self.updates.append(update)

    async def request_permission(self, session_id, tool_call, options, **kwargs):
        self.permissions.append((tool_call, options))
        self.asked.set()
        choice = self.answers.pop(0) if self.answers else "reject_once"
        if choice == "wait":
            await asyncio.sleep(30)
        if choice == "error":
            raise acp.RequestError(-32603, "the editor broke")
        if choice == "cancelled":
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        return RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", option_id=choice))

    def kinds(self):
        return [getattr(u, "session_update", None) for u in self.updates]

    def texts(self, kind="agent_message_chunk"):
        return "".join(u.content.text for u in self.updates if getattr(u, "session_update", None) == kind)


async def connect(agent, editor):
    """The SDK's own client and agent connections over one real socket pair (both directions, every system)."""
    a, b = socket.socketpair()
    agent_reader, agent_writer = await asyncio.open_connection(sock=a)
    editor_reader, editor_writer = await asyncio.open_connection(sock=b)
    task = asyncio.create_task(acp_agent.serve_streams(agent, agent_reader, agent_writer))
    conn = acp.connect_to_agent(editor, editor_writer, editor_reader)
    return conn, task, (agent_writer, editor_writer)


async def finish(task, writers):
    for writer in writers:
        writer.close()
    task.cancel()
    try:
        await task   # ends with the closed stream, or by the cancel
    except (asyncio.CancelledError, Exception):
        pass


def run(coroutine):
    return asyncio.run(asyncio.wait_for(coroutine, 60))


def updates_of(editor, kind):
    return [u for u in editor.updates if getattr(u, "session_update", None) == kind]


async def settle():
    await asyncio.sleep(0.2)   # notifications sent right before a reply may still be in flight


# --- initialize, sign-in, sessions -----------------------------------------------------------------------------------

def test_initialize_offers_the_terminal_sign_in_until_ditto_can_answer(tmp_path, registry, llm):
    editor = Editor()
    agent = acp_agent.DittoAcpAgent(ditto=make_ditto(tmp_path, registry, configured=False), registry=registry,
                                    state_dir=tmp_path / "state")

    async def go():
        conn, task, writers = await connect(agent, editor)
        init = await conn.initialize(protocol_version=1,
                                     client_info=acp.schema.Implementation(name="Zed", version="1.0"))
        with pytest.raises(acp.RequestError) as refused:
            await conn.new_session(cwd=str(tmp_path / "proj"), mcp_servers=[])
        await finish(task, writers)
        return init, refused.value

    init, refused = run(go())
    assert init.protocol_version == 1
    assert init.agent_info.name == "llmcf"
    caps = init.agent_capabilities
    assert caps.load_session is False
    assert caps.mcp_capabilities.http is False and caps.mcp_capabilities.sse is False
    assert caps.prompt_capabilities.image is False and caps.prompt_capabilities.embedded_context is False
    (method,) = init.auth_methods
    assert method.type == "terminal" and method.id == acp_agent.LOGIN_METHOD_ID and method.args == ["--login"]
    assert refused.code == -32000, "a session needs the sign-in first"


def test_a_signed_in_ditto_needs_no_sign_in_and_announces_its_commands(tmp_path, registry, llm):
    editor = Editor()
    agent = acp_agent.DittoAcpAgent(ditto=make_ditto(tmp_path, registry), registry=registry,
                                    state_dir=tmp_path / "state")

    async def go():
        conn, task, writers = await connect(agent, editor)
        init = await conn.initialize(protocol_version=1)
        session = await conn.new_session(cwd=str(tmp_path / "proj"), mcp_servers=[])
        await asyncio.sleep(0.3)
        options = await conn.set_config_option(config_id="provider", session_id=session.session_id,
                                               value="ollama")
        with pytest.raises(acp.RequestError):
            await conn.set_config_option(config_id="provider", session_id=session.session_id, value="no-such")
        await finish(task, writers)
        return init, session, options

    init, session, options = run(go())
    assert not init.auth_methods
    assert {o.id for o in session.config_options} == {"provider", "model"}
    assert session.config_options[0].current_value == acp_agent.SETTINGS_VALUE
    assert options.config_options[0].current_value == "ollama", "for this session only"
    (commands,) = updates_of(editor, "available_commands_update")
    assert {c.name for c in commands.available_commands} == {name for name, *_ in acp_agent.SLASH_COMMANDS}


# --- a prompt turn with a tool call and the permission round trip ---------------------------------------------------

def _build_turn(llm, final="Started."):
    llm.script.extend([
        answer("Let me look first.", [ToolCall(id="t1", name="list_targets", arguments={})]),
        answer("", [ToolCall(id="t2", name="start_build",
                             arguments={"model": "Qwen/Qwen3.5-0.8B", "target": "Rockchip"})]),
        answer(final)])


@pytest.mark.parametrize("choice", ["allow_once", "reject_once"])
def test_a_tool_call_asks_the_editor_with_once_options_only(tmp_path, registry, llm, choice):
    editor = Editor([choice])
    seen = []
    registry.events.subscribe(lambda kind, data: seen.append((kind, dict(data))))
    agent = acp_agent.DittoAcpAgent(ditto=make_ditto(tmp_path, registry), registry=registry,
                                    state_dir=tmp_path / "state", build_status=lambda rid: {
                                        "status": "completed", "stage": "target", "progress_percent": 100,
                                        "end_reason": "done"}, poll_seconds=0.05)
    _build_turn(llm)

    async def go():
        conn, task, writers = await connect(agent, editor)
        await conn.initialize(protocol_version=1, client_info=acp.schema.Implementation(name="Zed", version="1.0"))
        session = await conn.new_session(cwd=str(tmp_path / "proj"), mcp_servers=[])
        reply = await conn.prompt(session_id=session.session_id, prompt=[text_block("Build Qwen for Rockchip")])
        await asyncio.sleep(0.5)
        await finish(task, writers)
        return reply

    reply = run(go())
    assert reply.stop_reason == "end_turn"
    (tool_call, options), = editor.permissions
    assert [o.kind for o in options] == ["allow_once", "reject_once"], "standing approvals stay in the framework"
    assert "Start a build" in tool_call.title and tool_call.kind == "execute"
    assert "Qwen/Qwen3.5-0.8B" in tool_call.content[0].content.text, "the framework's own question"
    starts = updates_of(editor, "tool_call")
    assert [s.title for s in starts[:2]] == ["List targets", "Start a build"]
    assert starts[0].kind == "read" and starts[0].status == "in_progress"
    done = [u for u in updates_of(editor, "tool_call_update") if u.tool_call_id in ("call_1", "call_2")]
    assert done[0].status == "completed" and "Rockchip" in done[0].content[0].content.text
    assert "Let me look first." in editor.texts("agent_thought_chunk")
    assert editor.texts().endswith("Started.")
    if choice == "allow_once":
        assert registry.started == [{"model": "Qwen/Qwen3.5-0.8B", "target": "Rockchip", "quantization": "Q4_K_M"}]
        assert done[1].status == "completed"
    else:
        assert registry.started == []
        assert done[1].status == "failed"
    # the audit names how it was confirmed and the editor as the client; the channel reaches the registry's events
    entries = [e for e in registry.audit.entries(50) if e.get("tool") == "start_build"]
    asked = [e for e in entries if e.get("outcome") == "confirmation_requested"]
    assert asked and asked[0]["confirm_channel"] == "elicitation", "the editor's dialog is a client's own form"
    assert asked[0]["client"].startswith("Zed 1.0 (self-reported) over ACP")
    ended = [e for e in entries if e.get("outcome") in ("ok", "not_confirmed")][0]
    assert ended["answer"] == ("accepted" if choice == "allow_once" else "declined")
    if choice == "allow_once":
        assert ("call_done", "acp") in [(k, d.get("channel")) for k, d in seen if d.get("tool") == "start_build"]


def test_a_build_ditto_started_is_followed_with_a_plan(tmp_path, registry, llm):
    editor = Editor(["allow_once"])
    states = iter([{"status": "running", "stage": "convert: quantizing", "progress_percent": 60},
                   {"status": "completed", "stage": "target", "progress_percent": 100, "end_reason": "done"}])
    last = {}

    def status(rid):
        last.update(next(states, last))
        return dict(last)

    agent = acp_agent.DittoAcpAgent(ditto=make_ditto(tmp_path, registry), registry=registry,
                                    state_dir=tmp_path / "state", build_status=status, poll_seconds=0.05)
    _build_turn(llm)

    async def go():
        conn, task, writers = await connect(agent, editor)
        await conn.initialize(protocol_version=1)
        session = await conn.new_session(cwd=str(tmp_path / "proj"), mcp_servers=[])
        await conn.prompt(session_id=session.session_id, prompt=[text_block("Build it")])
        for _ in range(50):
            await asyncio.sleep(0.1)
            if any(u.tool_call_id == "build_req_1a2b3c4d" and u.status == "completed"
                   for u in updates_of(editor, "tool_call_update")):
                break
        await finish(task, writers)

    run(go())
    build = [u for u in updates_of(editor, "tool_call_update") if u.tool_call_id == "build_req_1a2b3c4d"]
    assert [u.status for u in build] == ["in_progress", "completed"]
    plans = updates_of(editor, "plan")
    assert [e.status for e in plans[0].entries] == ["completed", "completed", "in_progress", "pending"]
    assert [e.status for e in plans[-1].entries] == ["completed"] * 4
    assert all("quantizing" not in e.content for p in plans for e in p.entries), "fixed stage names only"


def test_a_permission_the_editor_cannot_answer_is_not_a_no(tmp_path, registry, llm):
    """An error instead of an answer is nobody's answer: the call does not run and is not recorded as declined."""
    editor = Editor(["error"])
    agent = acp_agent.DittoAcpAgent(ditto=make_ditto(tmp_path, registry), registry=registry,
                                    state_dir=tmp_path / "state")
    _build_turn(llm)

    async def go():
        conn, task, writers = await connect(agent, editor)
        await conn.initialize(protocol_version=1)
        session = await conn.new_session(cwd=str(tmp_path / "proj"), mcp_servers=[])
        await conn.prompt(session_id=session.session_id, prompt=[text_block("Build it")])
        await finish(task, writers)

    run(go())
    assert registry.started == []
    second = [m for m in llm.requests[2].messages if m.role == "tool"][-1]
    assert "Nobody could be asked" in second.text() or "needs_person" in second.text() or \
        "confirmation" in second.text()
    entries = [e for e in registry.audit.entries(50) if e.get("tool") == "start_build"]
    ended = [e for e in entries if e.get("outcome") == "not_confirmed"]
    assert ended and ended[0]["answer"] == "unavailable" and ended[0]["reason_code"] == "needs_person"


def test_cancel_ends_the_turn_and_runs_nothing(tmp_path, registry, llm):
    editor = Editor(["wait"])
    agent = acp_agent.DittoAcpAgent(ditto=make_ditto(tmp_path, registry), registry=registry,
                                    state_dir=tmp_path / "state")
    _build_turn(llm, final="never asked for")

    async def go():
        conn, task, writers = await connect(agent, editor)
        await conn.initialize(protocol_version=1)
        session = await conn.new_session(cwd=str(tmp_path / "proj"), mcp_servers=[])
        turn = asyncio.create_task(conn.prompt(session_id=session.session_id, prompt=[text_block("Build it")]))
        while not editor.asked.is_set():
            await asyncio.sleep(0.05)
        await conn.cancel(session_id=session.session_id)
        reply = await turn
        await finish(task, writers)
        return reply

    reply = run(go())
    assert reply.stop_reason == "cancelled"
    assert registry.started == []
    assert len(llm.requests) == 2, "no further model turn after the cancel"
    assert llm.script and llm.script[0].text == "never asked for"


# --- privacy: path labels, the notice, the editor's MCP servers ------------------------------------------------------

def test_no_host_path_of_the_editor_reaches_the_provider(tmp_path, registry, llm):
    editor = Editor()
    project = tmp_path / "home" / "alice" / "proj"
    extra = tmp_path / "home" / "alice" / "lib"
    agent = acp_agent.DittoAcpAgent(ditto=make_ditto(tmp_path, registry), registry=registry,
                                    state_dir=tmp_path / "state")
    llm.script.append(answer("Looked."))

    async def go():
        conn, task, writers = await connect(agent, editor)
        await conn.initialize(protocol_version=1)
        session = await conn.new_session(cwd=str(project), additional_directories=[str(extra)], mcp_servers=[])
        link = acp.resource_link_block("a.py", (project / "src" / "a.py").as_uri())
        await conn.prompt(session_id=session.session_id, prompt=[
            text_block(f"Why does {project / 'src' / 'a.py'} import {extra / 'util.py'}?"), link])
        await finish(task, writers)

    run(go())
    sent = "\n".join(m.text() for m in llm.requests[0].messages)   # the text as the provider gets it
    assert str(tmp_path) not in sent and tmp_path.as_posix() not in sent and "alice" not in sent
    assert "Why does" in sent, "the question itself arrives"
    assert "project:" in sent and "project-2:" in sent


def test_the_experimental_notice_and_the_editors_mcp_servers(tmp_path, registry, llm):
    editor = Editor()
    state = tmp_path / "state"
    agent = acp_agent.DittoAcpAgent(ditto=make_ditto(tmp_path, registry), registry=registry, state_dir=state)
    llm.script.extend([answer("One."), answer("Two.")])
    server = McpServerStdio(name="files", command="/usr/bin/files-mcp", args=["--root", "/srv", "--token=abc123"],
                            env=[acp.schema.EnvVariable(name="API_KEY", value="sk-test-secret-value")])

    async def go():
        conn, task, writers = await connect(agent, editor)
        await conn.initialize(protocol_version=1, client_info=acp.schema.Implementation(name="Zed", version="1.0"))
        session = await conn.new_session(cwd=str(tmp_path / "proj"), mcp_servers=[server])
        await conn.prompt(session_id=session.session_id, prompt=[text_block("first")])
        first = editor.texts()
        await conn.prompt(session_id=session.session_id, prompt=[text_block("second")])
        await finish(task, writers)
        return first, editor.texts()[len(first):]

    first, second = run(go())
    from orchestrator.utils.ditto_notice import notice_text
    title, _ = notice_text("en")
    assert first.startswith(f"**{title}**"), "the notice is the first thing the editor's person reads"
    assert "files" in first and "connects to none" in first
    assert title not in second, "once per session"
    (record,) = EditorServers(state).list()
    assert record.name == "files" and record.enabled is False and record.editor.startswith("Zed 1.0")
    assert record.env_names == ["API_KEY"]
    stored = (state / "acp_editor_servers.json").read_text(encoding="utf-8")
    assert "sk-test-secret-value" not in stored, "environment values are never kept"
    # The person enables it in llmcf: still recorded, still not used, never offered to the model
    assert EditorServers(state).set_enabled(record.id, True).enabled is True
    tools = {t.name for r in llm.requests for t in (r.tools or [])}
    assert tools <= {"list_targets", "start_build"}


def test_secrets_in_an_editor_servers_arguments_are_never_kept(tmp_path):
    """A secret handed as a command-line value (after a flag that names a secret, as --flag=value or NAME=value) is
    redacted like a known token format: neither the state file nor what the person is shown keeps it."""
    state = tmp_path / "state"
    secrets_ = ["abcd1234efgh5678ijkl", "zzzz9999yyyy8888", "hunter2hunter2", "s3cr3t-bearer-value", "envvalue777xyz"]
    server = {"name": "gh", "command": "npx", "args": [
        "-y", "some-mcp", "--api-key", secrets_[0], f"--token={secrets_[1]}", "--password", secrets_[2],
        f"--auth={secrets_[3]}", f"GITHUB_TOKEN={secrets_[4]}", "--root", "/srv/data", "--port", "8080"]}
    (record,) = EditorServers(state).record([server], editor="Zed")
    stored = (state / "acp_editor_servers.json").read_text(encoding="utf-8")
    shown = record.summary() + json.dumps(record.args)
    for secret in secrets_:
        assert secret not in stored and secret not in shown, secret
    # what is no secret stays readable for the person
    assert record.args[:2] == ["-y", "some-mcp"] and "--api-key" in record.args
    assert record.args[-4:] == ["--root", "/srv/data", "--port", "8080"]
    assert "GITHUB_TOKEN=" in " ".join(record.args)


# --- the parts below the adapter -------------------------------------------------------------------------------------

def test_the_tool_loop_stops_at_a_cancel_and_answers_every_call():
    from orchestrator.llm.tool_loop import CALL_CANCELLED, conversation_is_consistent, run_tool_loop
    from orchestrator.llm import ChatRequest
    flag = threading.Event()
    calls = []

    class Client:
        def chat(self, request):
            return answer("", [ToolCall(id="a", name="x", arguments={}), ToolCall(id="b", name="y", arguments={})])

    def execute(name, arguments):
        calls.append(name)
        flag.set()   # the person cancels while the first call runs
        return "ok"

    result = run_tool_loop(Client(), ChatRequest(messages=[Message.user("go")], tools=[], task="chat"), execute,
                           cancelled=flag.is_set)
    assert result.stop == "cancelled" and calls == ["x"]
    tool_results = [m for m in result.new_messages if m.role == "tool"]
    assert [m.text() for m in tool_results][1] == CALL_CANCELLED
    assert conversation_is_consistent([Message.user("go"), *result.new_messages])


def test_a_client_dialog_never_asks_about_a_framework_only_tool(tmp_path):
    """A confirm callback of a client's own dialog (elicitation) is not asked about a tool only the framework's window
    may ask about; with pending approvals off nobody can be asked, and that is no "no" either."""
    from orchestrator.tools.registry import NOT_ASKED, ToolContext, ToolDenied
    reg = ToolRegistry(PolicyStore(tmp_path / "state"), AuditLog(tmp_path / "state" / "audit.db"))
    ran = []
    reg.register(ToolSpec("start_build", "Start a build", "Starts", ToolTier.LOCAL_WRITE, BuildIn, BuildOut,
                          lambda inp: ran.append(1) or BuildOut(request_id="r"), channels=("framework",)))
    reg.policy_store.replace(ToolPolicy(configured=True, overrides={"start_build": Decision.ASK}))
    asked = []

    def editor(spec, inp):
        asked.append(spec.name)
        return True
    editor.confirm_channel = "elicitation"
    with pytest.raises(ToolDenied) as denied:
        reg.call("start_build", {"model": "m", "target": "t"}, ToolContext("ditto", confirm=editor, channel="acp"))
    assert asked == [] and ran == [] and denied.value.code == "needs_person"

    reg.register(ToolSpec("other_build", "Other", "Starts", ToolTier.LOCAL_WRITE, BuildIn, BuildOut,
                          lambda inp: ran.append(2) or BuildOut(request_id="r")))
    reg.policy_store.replace(ToolPolicy(configured=True, overrides={"other_build": Decision.ASK}))

    def unreachable(spec, inp):
        return NOT_ASKED
    unreachable.confirm_channel = "elicitation"
    with pytest.raises(ToolDenied) as nobody:
        reg.call("other_build", {"model": "m", "target": "t"}, ToolContext("ditto", confirm=unreachable))
    assert nobody.value.code == "needs_person" and ran == []
    # not recorded as a recent "no": asking again is allowed and a yes runs it
    editor.confirm_channel = "elicitation"
    reg.call("other_build", {"model": "m", "target": "t"}, ToolContext("ditto", confirm=editor))
    assert ran == [2]


def test_folder_labels_and_slash_commands():
    roots, labels = acp_agent.folder_roots(None, os.path.abspath("proj-a"), [os.path.abspath("proj-b"), "relative"])
    assert labels == ["project", "project-2"] and "relative" not in json.dumps(roots)
    assert "get_status" in acp_agent.DittoAcpAgent._slash_command("/status")
    assert acp_agent.DittoAcpAgent._slash_command("/nosuch") == ""
    assert acp_agent.DittoAcpAgent._slash_command("please /status") == ""


def test_the_editor_entry_starts_the_acp_agent():
    from orchestrator.utils.stdio_programs import ACP_AGENT, acp_entries, launch_command
    launch = launch_command(REPO_ROOT, command=ACP_AGENT)
    assert launch["args"] == ["-P", "-m", "orchestrator.cli", "-q", "acp"]
    entries = acp_entries(REPO_ROOT)
    entry = json.loads(entries["json"])["agent_servers"]["llmcf"]
    assert entry["args"][-1] == "acp" and entry["env"]["PYTHONPATH"] == str(REPO_ROOT)
    assert "orchestrator.cli" in entries["command"]


def test_cli_acp_config_and_servers(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from orchestrator import cli
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
    runner = CliRunner()
    shown = runner.invoke(cli.cli, ["-q", "acp", "config", "--json"])
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output)["agent_servers"]["llmcf"]["args"][-1] == "acp"
    monkeypatch.setattr(cli, "_state_dir", lambda ctx: tmp_path / "state")
    EditorServers(tmp_path / "state").record([{"name": "files", "command": "/usr/bin/files-mcp", "args": []}],
                                             editor="Zed")
    listed = runner.invoke(cli.cli, ["-q", "acp", "servers", "list", "--json"])
    (row,) = json.loads(listed.output)
    assert row["name"] == "files" and row["enabled"] is False
    enabled = runner.invoke(cli.cli, ["-q", "acp", "servers", "enable", "files"])
    assert enabled.exit_code == 0 and EditorServers(tmp_path / "state").list()[0].enabled is True
    missing = runner.invoke(cli.cli, ["-q", "acp", "servers", "disable", "nothing"])
    assert missing.exit_code == 1


# --- a real `llm-cli acp` process, started like an editor starts it -------------------------------------------------

def test_a_real_llm_cli_acp_process_speaks_only_acp_on_stdout(tmp_path):
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(REPO_ROOT), *sys.path]),
               PYTHON_KEYRING_BACKEND="keyring.backends.null.Keyring", LLM_SECRET_STORAGE="ephemeral",
               LLM_FRAMEWORK_HOME=str(tmp_path / "state"), LLM_FRAMEWORK_OUTPUT_DIR=str(tmp_path / "out"),
               LLM_FRAMEWORK_CACHE_DIR=str(tmp_path / "cache"), LLM_FRAMEWORK_MODELS_DIR=str(tmp_path / "models"))
    editor = Editor()

    async def go():
        async with acp.spawn_agent_process(editor, sys.executable, "-m", "orchestrator.cli", "-q", "acp",
                                           env=env, cwd=str(tmp_path)) as (conn, process):
            init = await asyncio.wait_for(conn.initialize(protocol_version=1), 120)
            try:
                session = await asyncio.wait_for(conn.new_session(cwd=str(tmp_path), mcp_servers=[]), 60)
            except acp.RequestError as e:
                session = e
            return init, session

    started = time.monotonic()
    init, session = asyncio.run(go())
    assert time.monotonic() - started < 180
    assert init.protocol_version == 1 and init.agent_info.name == "llmcf"
    # Without a provider set up the editor gets the sign-in; with one (an environment that has it) a session.
    if isinstance(session, acp.RequestError):
        assert session.code == -32000 and init.auth_methods
    else:
        assert session.session_id.startswith("sess_")
