"""A tiny ACP agent for the tests of the framework's ACP client (Core/acp_client.py), built on the SDK's agent side
(acp.run_agent). It writes what it received to the report file named by its first argument:

- the environment it was started with and its working folder;
- `initialize`: the protocol version and the client capabilities;
- `session/new`: the cwd and the MCP servers;
- `authenticate`: the method id;
- every prompt and the permission answers it got.

Prompts it understands: "hello" (a streamed answer: two text chunks, a thought, a tool call with status updates, a
plan), "ask" (a permission request with allow once / allow always / reject once; the answer is in its reply), "wait"
(waits until the client cancels, then ends the turn as cancelled), "fs" (asks the client to read a file, which the
client refuses), "form" and "secret form" (a form elicitation without and with a password field).

Usage: python fake_agent.py REPORT [--path DIR ...] [--login]   (--path: where the acp package lives)
"""

import asyncio
import json
import os
import sys


def _paths(argv):
    out, rest = [], []
    i = 0
    while i < len(argv):
        if argv[i] == "--path" and i + 1 < len(argv):
            out.append(argv[i + 1])
            i += 2
        else:
            rest.append(argv[i])
            i += 1
    return out, rest


PATHS, ARGS = _paths(sys.argv[1:])
sys.path[:0] = PATHS
REPORT = ARGS[0]
LOGIN = "--login" in ARGS

import acp  # noqa: E402
from acp import schema  # noqa: E402


def report(key, value):
    try:
        with open(REPORT, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data.setdefault(key, []).append(value)
    tmp = f"{REPORT}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:   # whole or not at all: the test reads it while the agent runs
        json.dump(data, f)
    os.replace(tmp, REPORT)


if LOGIN:   # the terminal sign-in: the agent's own login command
    report("login", {"argv": sys.argv[1:], "env": sorted(os.environ)})
    sys.exit(0)


class FakeAgent:
    def __init__(self):
        self.conn = None
        self.cancelled = asyncio.Event()

    def on_connect(self, conn):
        self.conn = conn

    async def initialize(self, protocol_version, client_capabilities=None, client_info=None, **kwargs):
        report("env", dict(os.environ))
        report("cwd", os.getcwd())
        report("initialize", {"protocol_version": protocol_version,
                              "capabilities": client_capabilities.model_dump(mode="json", by_alias=True,
                                                                             exclude_none=True),
                              "client": client_info.name if client_info else ""})
        return schema.InitializeResponse(
            protocol_version=1, agent_info=schema.Implementation(name="fake-agent", title="Fake Agent", version="1.0"),
            auth_methods=[schema.AuthMethodAgent(id="browser", name="Sign in with the browser"),
                          schema.TerminalAuthMethod(type="terminal", id="console", name="Sign in at the console",
                                                    args=["--login"], env={"FAKE_LOGIN": "1"})])

    async def authenticate(self, method_id, **kwargs):
        report("authenticate", method_id)
        return schema.AuthenticateResponse()

    async def new_session(self, cwd, additional_directories=None, mcp_servers=None, **kwargs):
        report("new_session", {"cwd": cwd, "mcp_servers": [s.model_dump(mode="json", by_alias=True)
                                                           for s in (mcp_servers or [])]})
        return schema.NewSessionResponse(session_id="s-1")

    async def cancel(self, session_id, **kwargs):
        report("cancel", session_id)
        self.cancelled.set()

    async def prompt(self, session_id, prompt, **kwargs):
        text = " ".join(getattr(block, "text", "") for block in prompt)
        report("prompt", text)
        if text == "hello":
            await self.conn.session_update(session_id, acp.update_agent_message_text("Hel"))
            await self.conn.session_update(session_id, acp.update_agent_thought_text("thinking about it"))
            await self.conn.session_update(session_id, acp.start_tool_call("t1", "List the builds", status="pending"))
            await self.conn.session_update(session_id, acp.update_tool_call("t1", status="completed"))
            await self.conn.session_update(session_id, acp.update_plan([acp.plan_entry("Look", status="completed"),
                                                                        acp.plan_entry("Answer")]))
            await self.conn.session_update(session_id, acp.update_agent_message_text("lo"))
            return schema.PromptResponse(stop_reason="end_turn")
        if text == "ask":
            answer = await self.conn.request_permission(
                session_id, schema.ToolCallUpdate(tool_call_id="t2", title="Delete a build"),
                [schema.PermissionOption(option_id="yes", name="Allow once", kind="allow_once"),
                 schema.PermissionOption(option_id="always", name="Always", kind="allow_always"),
                 schema.PermissionOption(option_id="no", name="Reject", kind="reject_once")])
            chosen = getattr(answer.outcome, "option_id", "cancelled")
            report("permission", chosen)
            await self.conn.session_update(session_id, acp.update_agent_message_text(f"answer={chosen}"))
            return schema.PromptResponse(stop_reason="end_turn")
        if text == "wait":
            try:
                await asyncio.wait_for(self.cancelled.wait(), 30)
            except asyncio.TimeoutError:
                return schema.PromptResponse(stop_reason="end_turn")
            return schema.PromptResponse(stop_reason="cancelled")
        if text == "fs":
            try:
                await self.conn.read_text_file(path=os.path.join(os.getcwd(), "x.txt"), session_id=session_id)
                report("fs", "allowed")
            except Exception as e:  # noqa: BLE001
                report("fs", f"refused {getattr(e, 'code', '')}")
            return schema.PromptResponse(stop_reason="end_turn")
        if text in ("form", "secret form"):
            props = {"board": schema.ElicitationStringPropertySchema(type="string", title="Board name")}
            if text == "secret form":
                props["password"] = schema.ElicitationStringPropertySchema(type="string", title="Password")
            answer = await self.conn.create_elicitation(
                message="Which board?",
                mode=schema.ElicitationFormSessionMode(session_id=session_id,
                                                       requested_schema=schema.ElicitationSchema(
                                                           type="object", properties=props)))
            report("form", answer.model_dump(mode="json"))
            return schema.PromptResponse(stop_reason="end_turn")
        await self.conn.session_update(session_id, acp.update_agent_message_text("ok"))
        return schema.PromptResponse(stop_reason="end_turn")


if __name__ == "__main__":
    asyncio.run(acp.run_agent(FakeAgent()))
