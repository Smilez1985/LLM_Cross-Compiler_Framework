#!/usr/bin/env python3
"""
Ditto and the framework on another computer: the connection (MCP design T12; DECISIONS round 2 item 2 and round 5 d:
Ditto is hybrid; hybrid design 6.1-6.8).

"Another framework" is not a different program: it is the same framework installed on a second computer (a stronger
build PC, a server, a lab PC with the boards). Ditto always keeps its in-process path (ToolRegistry.call with the
actor "ditto"): this computer's tools, files, targets and boards stay. The connection to the second installation is
an addition: Ditto reaches it through that installation's MCP server, the same door Claude Code uses, lists its tools
(names and closed input schemas only), sends tools/call with the model's arguments exactly as the model wrote them,
fetches its task prompts, and answers its questions (elicitations) with its own dialog or prompt, which names that
computer. The registry, the policy, the fool-proof layer (an unknown argument is refused there with a suggestion,
never dropped) and the audit log there are that framework's; this computer writes its own started and end entries for
every remote call (orchestrator/tools/remote_audit.py). orchestrator/llm/hybrid_tools.py makes one tool list of both
computers and routes each call to the computer where its object lives.

Docker is not needed for this. The connection is a command (stdio) or a server address (HTTP), and neither depends
on Docker: SSH reaches the other computer, either as the command (`ssh -T -o SendEnv=LCF_MCP_TOKEN user@build-box
llm-cli mcp serve`, with `AcceptEnv LCF_MCP_TOKEN` in the SSH server's configuration there) or as a tunnel to the
server's loopback address (`ssh -N -L 8765:127.0.0.1:8765 user@build-box`, then http://127.0.0.1:8765/mcp). A
framework container the person built locally (no framework image is published) is one alternative:
`docker exec -i <container> llm-cli mcp serve`.

Rules of the connection:
- Identity is a named token, created on the other computer with `llm-cli mcp token add NAME --link ditto` (so the
  server uses Ditto's own policy and standing approvals by reference) and stored in Ditto's credential vault
  (`ditto_mcp_token`). Over stdio it reaches the server as the environment variable LCF_MCP_TOKEN of the started
  process, over HTTP as the bearer header. It never appears in a tool argument, a result, a prompt, an audit entry or
  a log, and the connection is not made without it (Ditto never runs as the shared local caller "mcp" with another
  policy).
- The server and the transport are chosen in Ditto's AI settings (`ai_ditto_*`), which only the person changes (GUI
  or CLI); an AI cannot (update_settings never writes an ai_* key).
- A connection that cannot be made, or breaks, never stops a turn: Ditto goes on with this computer's tools and says
  that the other computer is not reachable.
- A question of the other computer is answered only by this computer's person in Ditto's dialog. A turn without a
  person-facing dialog connects without the elicitation capability, so the framework there makes a pending approval
  (or applies its own delegation for this token); an unanswered question is cancelled, never sent as the person's
  "no" (SEC-H8).
- Ditto cannot check a remote server, so every result from it (the server's notes and summary line included), every
  error text and every prompt text is treated as untrusted data, and its tool descriptions, titles and examples are
  never read into anything a model sees.
"""

import asyncio
import hashlib
import ipaddress
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

from orchestrator.llm.types import ToolSpecLLM
from orchestrator.utils.untrusted import sanitize_untrusted, wrap_untrusted

TOKEN_SECRET = "ditto_mcp_token"  # the vault entry that holds the named token
TOKEN_ENV = "LCF_MCP_TOKEN"       # how a stdio server receives it (orchestrator.tools.mcp_tokens.ENV_VAR)
SOURCE_SETTING = "ai_ditto_tool_source"
TRANSPORT_SETTING = "ai_ditto_mcp_transport"
COMMAND_SETTING = "ai_ditto_mcp_command"
URL_SETTING = "ai_ditto_mcp_url"
NAME_SETTING = "ai_ditto_mcp_name"
CALL_TIMEOUT = 900.0      # seconds one remote call may take (a question to the person included)
CONNECT_TIMEOUT = 60.0
IDLE_SECONDS = 600.0      # a kept connection closes after this long without a call (hybrid design 6.7)
MAX_DESCRIPTION = 2048
MAX_RESULT = 60000

TOKEN_NEXT = ("On the other computer run 'llm-cli mcp token add NAME --link ditto' (Ditto's own permissions "
              "then apply there), then store the token here with 'llm-cli ditto remote set ... --with-token' or in "
              "the GUI's AI settings.")


class RemoteSetupError(ValueError):
    """The MCP-client mode cannot start; the message says why and what to do (never contains the token)."""


@dataclass(frozen=True)
class RemoteServer:
    """The framework on another computer whose tools Ditto uses in MCP-client mode."""
    transport: str                        # "stdio" or "http"
    command: Tuple[str, ...] = ()         # stdio: the program and its arguments, e.g. ssh ... llm-cli mcp serve
    url: str = ""                         # http: the server's endpoint, e.g. http://127.0.0.1:8765/mcp
    name: str = ""                        # the person's name for it, shown in dialogs

    def label(self) -> str:
        """The server's speaking name for the person (dialogs, notes, untrusted-block labels); never the token."""
        if self.name:
            return self.name[:80]
        if self.transport == "http":
            parts = urlsplit(self.url)
            return f"{parts.hostname or 'server'}:{parts.port}" if parts.port else (parts.hostname or "server")
        return " ".join(self.command[:4])[:80] or "server"


def _loopback(host: str) -> bool:
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def check_url(url: str) -> str:
    """An HTTP server address Ditto may send its token to: no user info in it; plain http only to a loopback address
    (the other computer's server reached through an SSH tunnel, or a container port published to the host's
    loopback, `-p 127.0.0.1:PORT:PORT`), https otherwise, so the bearer token never crosses a network in clear
    text."""
    parts = urlsplit(url or "")
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise RemoteSetupError("The MCP server address must be an http(s) URL, e.g. http://127.0.0.1:8765/mcp.")
    if parts.username or parts.password:
        raise RemoteSetupError("The MCP server address must not contain a user name or password; the token is stored "
                               "in the credential vault.")
    if parts.scheme == "http" and not _loopback(parts.hostname):
        raise RemoteSetupError("Plain http is accepted only for a loopback address (the token would cross the "
                               "network in clear text): use https, or reach the other computer's server through an "
                               "SSH tunnel (ssh -N -L 8765:127.0.0.1:8765 user@build-box) and connect to "
                               "http://127.0.0.1:8765/mcp.")
    return url


CONTAINER_CLIS = ("docker", "podman", "nerdctl")


def with_token_env(command: Tuple[str, ...]) -> Tuple[str, ...]:
    """The stdio command as Ditto starts it. For a framework container (one alternative to SSH): `docker exec`
    (podman, nerdctl) does not hand its own environment to the process in the container, so without
    `-e LCF_MCP_TOKEN` the server there would run without the token, as the shared local caller "mcp" with another
    policy. The name-only form passes the value from Ditto's environment of the started process, so the token never
    appears on the command line. Other commands (ssh among them) are left as they are; ssh passes the variable with
    `-o SendEnv=LCF_MCP_TOKEN` when the SSH server there accepts it."""
    cmd = list(command)
    if not cmd:
        return tuple(cmd)
    program = os.path.basename(cmd[0]).lower()
    program = program[:-4] if program.endswith(".exe") else program
    if program not in CONTAINER_CLIS or any(TOKEN_ENV in part for part in cmd[1:]):
        return tuple(cmd)
    if cmd[1:2] == ["exec"]:
        at = 2
    elif cmd[1:3] == ["container", "exec"]:
        at = 3
    else:
        return tuple(cmd)
    return tuple(cmd[:at] + ["-e", TOKEN_ENV] + cmd[at:])


def remote_server(config: Any) -> Optional[RemoteServer]:
    """The configured remote server, or None when Ditto uses its in-process tools (the default). RemoteSetupError
    when the mode is chosen but incomplete."""
    get = getattr(config, "get", None)
    value = (lambda key, default=None: get(key, default)) if callable(get) else \
        (lambda key, default=None: getattr(config, key, default))
    if str(value(SOURCE_SETTING, "local") or "local").lower() != "mcp":
        return None
    transport = str(value(TRANSPORT_SETTING, "stdio") or "stdio").lower()
    name = str(value(NAME_SETTING, "") or "")
    if transport == "stdio":
        command = value(COMMAND_SETTING, []) or []
        if isinstance(command, str):
            import shlex
            command = shlex.split(command)
        command = tuple(str(c) for c in command if str(c))
        if not command:
            raise RemoteSetupError("Ditto's connection to another computer over stdio needs the command that starts "
                                   "the framework's MCP server there, e.g. 'ssh -T -o SendEnv=LCF_MCP_TOKEN "
                                   "user@build-box llm-cli mcp serve' (or, for a framework container you built "
                                   "yourself, 'docker exec -i <container> llm-cli mcp serve'): llm-cli ditto remote "
                                   "set --command \"...\".")
        return RemoteServer("stdio", command=command, name=name)
    if transport == "http":
        return RemoteServer("http", url=check_url(str(value(URL_SETTING, "") or "")), name=name)
    raise RemoteSetupError(f"Unknown transport '{transport}' for Ditto's connection to another computer (stdio, "
                           "http).")


@dataclass
class RemoteQuestion:
    """What Ditto's confirmation dialog or prompt shows for a question of the remote server: the server's name and
    its text (the framework there wrote it from its own facts, but Ditto cannot check that server, so it is shown as
    that server's words). `title` and `name` let the existing dialogs show it like a tool question."""
    server: str
    message: str
    offers_request: bool = False  # the server's form offers "ask the framework to stop asking"

    @property
    def title(self) -> str:
        return f"Question from the framework at {self.server}"

    @property
    def name(self) -> str:
        return "remote_question"

    def text(self) -> str:
        return (f"The framework at {self.server} asks (its text, which Ditto cannot check):\n\n"
                f"{sanitize_untrusted(self.message, 16000)}")


@dataclass
class RemoteAnswer:
    """A confirmation dialog's answer to a RemoteQuestion: `stop_asking` asks the server to stop asking for calls like
    this one; the person confirms that again in that framework's own approval window (never here). `answered` is False
    when nobody could be asked (no dialog, or the dialog failed): the question is then cancelled, never sent as the
    person's "no"."""
    approve: bool
    stop_asking: bool = False
    answered: bool = True

    def __bool__(self) -> bool:
        return self.approve


class _Empty:
    """The input a dialog shows for a remote question (the text is the question)."""

    @staticmethod
    def model_dump(**_kw) -> Dict[str, Any]:
        return {}


def person_facing(confirm: Optional[Callable]) -> bool:
    """Whether `confirm` can put a question to a person: not None and not marked `person_facing = False` (a prompt
    without a terminal, which nobody may read)."""
    return confirm is not None and getattr(confirm, "person_facing", True) is not False


def ask_person(confirm: Optional[Callable], question: RemoteQuestion) -> RemoteAnswer:
    """Puts the remote server's question to the person through Ditto's own dialog or prompt. No dialog (or one that
    cannot reach a person), a dialog that fails, or a dialog that says nobody answered (RemoteAnswer answered=False)
    approves nothing and is not an answer (answered=False): nobody said no."""
    if not person_facing(confirm):
        return RemoteAnswer(False, answered=False)
    from orchestrator.tools import registry as tool_registry
    token = tool_registry._QUESTION.set(question.text())
    try:
        reply = confirm(question, _Empty())
    except Exception:  # noqa: BLE001 - a dialog that fails approves nothing and is no answer
        return RemoteAnswer(False, answered=False)
    finally:
        tool_registry._QUESTION.reset(token)
    if isinstance(reply, RemoteAnswer):
        if reply.answered is False:
            return RemoteAnswer(False, answered=False)
        return RemoteAnswer(reply.approve, reply.stop_asking and question.offers_request and reply.approve)
    return RemoteAnswer(bool(reply))


@dataclass
class _Listed:
    name: str
    description: str
    schema: Dict[str, Any]


@dataclass(frozen=True)
class RemoteResult:
    """One remote call's result, as the server sent it: `text` is the server's text (untrusted: whoever shows it to a
    model wraps it), `structured` its structured content (untrusted too; only id-shaped values are ever learned from
    it), `code` and `field` the reason code and argument of a refused call from its structured error. `unanswered`:
    the server asked a question during the call that nobody here answered (cancelled), so a "declined" it reports is
    not the person's "no"."""
    text: str
    structured: Any = None
    is_error: bool = False
    code: str = ""
    field: str = ""
    unanswered: bool = False


@dataclass
class RemoteConnection:
    """One connection to the remote framework's MCP server for one conversation turn: a private event loop thread
    holds the SDK client; every method blocks the calling thread (Ditto's tool loop runs in a worker thread).

    `client_factory(elicitation_callback)` returns the SDK client (tests pass an in-process server); by default it
    is built from `server` and `token`.

    `elicitation`: whether the client declares that it can ask the person (the server then sends its questions here).
    None means: only when a person-facing `confirm` is given (SEC-H8). A kept connection (RemoteSession) declares it
    for its whole life and binds each turn's dialog to `confirm`; a question arriving while none is bound is
    cancelled, never answered "no"."""
    server: RemoteServer
    token: Optional[str] = field(repr=False)  # never in a repr, a log line or an error text
    confirm: Optional[Callable] = None
    client_factory: Optional[Callable[[Any], Any]] = None
    elicitation: Optional[bool] = None
    _unanswered: int = field(default=0, init=False, repr=False)   # questions of the server nobody here answered
    _loop: Optional[asyncio.AbstractEventLoop] = field(default=None, init=False, repr=False)
    _thread: Optional[threading.Thread] = field(default=None, init=False, repr=False)
    _client: Any = field(default=None, init=False, repr=False)
    _listed: Dict[str, _Listed] = field(default_factory=dict, init=False, repr=False)

    # --- connecting ---------------------------------------------------------------------------------------------

    def _make_client(self, elicit) -> Any:
        if self.client_factory is not None:
            return self.client_factory(elicit)
        if not self.token:
            raise RemoteSetupError("Ditto's connection to another computer needs a named MCP token and none is "
                                   "stored. " + TOKEN_NEXT)
        from mcp import Client
        from mcp_types import Implementation
        info = Implementation(name="Ditto (LLM Cross-Compiler Framework)", version="1")
        if self.server.transport == "stdio":
            from mcp.client.stdio import StdioServerParameters
            command = with_token_env(self.server.command)
            params = StdioServerParameters(command=command[0], args=list(command[1:]), env={TOKEN_ENV: self.token})
            return Client(params, elicitation_callback=elicit, client_info=info)
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client
        http = create_mcp_http_client(headers={"Authorization": f"Bearer {self.token}"})
        return Client(streamable_http_client(check_url(self.server.url), http_client=http),
                      elicitation_callback=elicit, client_info=info)

    def wants_elicitation(self) -> bool:
        """Whether this connection tells the server it can ask the person (see `elicitation`)."""
        return person_facing(self.confirm) if self.elicitation is None else bool(self.elicitation)

    async def _elicit(self, _ctx, params) -> Any:
        from mcp_types import ElicitResult
        import anyio.to_thread
        schema = getattr(params, "requested_schema", None) or {}
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        question = RemoteQuestion(self.server.label(), str(getattr(params, "message", "") or ""),
                                  offers_request="stop_asking" in properties)
        if "approve" not in properties:  # a form Ditto does not know: never filled in on the person's behalf
            self._unanswered += 1         # nobody was asked: a "declined" it causes is not the person's
            return ElicitResult(action="decline")
        answer = await anyio.to_thread.run_sync(ask_person, self.confirm, question)
        if not answer.answered:  # no dialog bound now, or it failed: nobody said no
            self._unanswered += 1
            return ElicitResult(action="cancel")
        content: Dict[str, Any] = {"approve": answer.approve}
        if question.offers_request:
            content["stop_asking"] = bool(answer.stop_asking)
        return ElicitResult(action="accept", content=content)

    def _run(self, coro, timeout: float):
        if self._loop is None:
            raise RemoteSetupError("The connection to the framework's MCP server is closed.")
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    async def _hold(self, client, ready: "asyncio.Future", stop: asyncio.Event) -> None:
        """Enters and leaves the SDK client in one task (its task groups require that) and keeps it open until
        close(); the calls themselves run as their own tasks on the same loop."""
        try:
            async with client as connected:
                ready.set_result(connected)
                await stop.wait()
        except BaseException as e:  # noqa: BLE001 - reported to open() when it happens while connecting
            if not ready.done():
                ready.set_exception(e)

    def open(self) -> "RemoteConnection":
        client = self._make_client(self._elicit if self.wants_elicitation() else None)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="ditto-mcp-client", daemon=True)
        self._thread.start()

        async def start():
            ready = self._loop.create_future()
            self._stop = asyncio.Event()
            self._holder = self._loop.create_task(self._hold(client, ready, self._stop))
            return await ready
        try:
            self._client = self._run(start(), CONNECT_TIMEOUT)
        except BaseException as e:
            self._stop_loop()
            raise RemoteSetupError(f"Could not connect to the framework's MCP server at {self.server.label()}: "
                                   f"{type(e).__name__}. Check the server settings ('llm-cli ditto remote show') "
                                   "and that the token is valid there ('llm-cli mcp token list').") from None
        try:
            self._check_identity()
        except RemoteSetupError:
            self.close()
            raise
        return self

    def _check_identity(self) -> None:
        """A stdio server that did not receive the token (a command that does not pass the environment on, such as
        ssh without -o SendEnv=LCF_MCP_TOKEN or a container runtime without -e LCF_MCP_TOKEN) runs as the shared
        local caller "mcp" with another policy than Ditto's: the mode refuses that. Checked with the server's own
        get_status (permissions section, which names the caller); when the server does not offer it, nothing can be
        checked here. Over HTTP a server refuses a request without a valid token anyway."""
        if self.server.transport != "stdio":
            return
        try:
            listed = self._run(self._client.list_tools(), CONNECT_TIMEOUT)
            if "get_status" not in {str(getattr(t, "name", "")) for t in getattr(listed, "tools", []) or []}:
                return
            result = self._run(self._client.call_tool("get_status", {"sections": ["permissions"]}), CONNECT_TIMEOUT)
        except Exception:  # noqa: BLE001 - cannot be checked: the server's own policy still applies
            return
        data = getattr(result, "structured_content", None)
        permissions = data.get("permissions") if isinstance(data, dict) else None
        caller = permissions.get("caller") if isinstance(permissions, dict) else None
        if isinstance(caller, str) and not caller.startswith("mcp:"):
            raise RemoteSetupError(f"The framework at {self.server.label()} did not receive Ditto's named token (it "
                                   "treats Ditto as the shared local caller, with other permissions than Ditto's), so "
                                   "Ditto does not use it. The command must pass the environment variable "
                                   f"{TOKEN_ENV} on to the server: over SSH 'ssh -T -o SendEnv={TOKEN_ENV} "
                                   f"user@build-box llm-cli mcp serve' (the SSH server there needs 'AcceptEnv "
                                   f"{TOKEN_ENV}'), for a framework container 'docker exec -i -e {TOKEN_ENV} "
                                   "<container> llm-cli mcp serve': llm-cli ditto remote set --command \"...\".")

    def _stop_loop(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread is not None:
                self._thread.join(10)
            if not self._loop.is_running():
                self._loop.close()
        self._loop = self._thread = None

    def close(self) -> None:
        holder = getattr(self, "_holder", None)
        if holder is not None and self._loop is not None:
            async def finish():
                self._stop.set()
                await asyncio.wait_for(holder, 30)
            try:
                self._run(finish(), 35)
            except Exception:  # noqa: BLE001 - closing a broken connection
                pass
        self._client = None
        self._holder = None
        self._stop_loop()

    def __enter__(self) -> "RemoteConnection":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    # --- tools --------------------------------------------------------------------------------------------------

    def tools(self) -> List[ToolSpecLLM]:
        """The server's tools for this caller (the server lists only what its policy allows), each with the server's
        closed input schema without descriptions, titles and examples, and no description of its own: a remote
        server's words never reach a model through a tool definition (hybrid design 6.3). For listings such as
        `llm-cli ditto remote test`; Ditto's model gets its tools from hybrid_tools.HybridToolSet."""
        return sorted((ToolSpecLLM(name=tool.name, input_schema=dict(tool.schema), description="")
                       for tool in self.listing().tools), key=lambda s: s.name)

    def listing(self):
        """The server's tools as hybrid_tools.RemoteListing: names that pass the model APIs' pattern, their input
        schemas stripped of descriptions, titles and examples, the read-only hint and the internal mark; names that
        fail the pattern are only counted (left out with a notice, HF9). Nothing of a tool's description is kept."""
        from orchestrator.llm.hybrid_tools import PROVIDER_NAME_RE, ListedTool, RemoteListing, strip_schema
        result = self._run(self._client.list_tools(), CONNECT_TIMEOUT)
        tools, bad = [], 0
        self._listed = {}
        for tool in getattr(result, "tools", []) or []:
            name = str(getattr(tool, "name", "") or "")
            if not PROVIDER_NAME_RE.fullmatch(name):
                bad += 1
                continue
            raw = getattr(tool, "input_schema", None)
            schema = strip_schema(dict(raw) if isinstance(raw, dict) else {"type": "object", "properties": {}})
            annotations = getattr(tool, "annotations", None)
            meta = getattr(tool, "meta", None)
            tools.append(ListedTool(name=name, schema=schema,
                                    read_only=getattr(annotations, "read_only_hint", False) is True,
                                    internal=bool(isinstance(meta, dict) and meta.get("lcf/internal"))))
            self._listed[name] = _Listed(name, "", schema)
        return RemoteListing(tools=tuple(sorted(tools, key=lambda t: t.name)), bad_names=bad)

    def call_raw(self, name: str, arguments: Dict[str, Any]) -> RemoteResult:
        """Calls a remote tool with the arguments exactly as given (the server checks them against its closed input
        model and refuses an unknown one with a suggestion; nothing is dropped or renamed here) and returns what the
        server sent, unwrapped: the caller wraps the text as untrusted data. Raises for a broken connection."""
        from orchestrator.tools.registry import ToolError
        if not isinstance(arguments, dict):
            raise ToolError("Tool arguments must be an object.", code="invalid_value")
        before = self._unanswered   # one call at a time per turn: a question nobody answered belongs to this call
        result = self._run(self._client.call_tool(name, dict(arguments)), CALL_TIMEOUT)
        text = _result_text(result)
        structured = getattr(result, "structured_content", None)
        if getattr(result, "is_error", False):
            code, field_name = structured_error(result, self._listed.get(name))
            return RemoteResult(text, structured, True, code, field_name, unanswered=self._unanswered > before)
        return RemoteResult(text, structured)

    def call(self, name: str, arguments: Dict[str, Any]) -> str:
        """Calls a remote tool (call_raw) and returns its result as untrusted data; a refused or failed call raises
        ToolError with the server's reason code, and its text as untrusted data."""
        from orchestrator.tools.registry import ToolError
        result = self.call_raw(name, arguments)
        source = f"remote:{self.server.label()}:{name}"
        if result.is_error:
            raise ToolError(wrap_untrusted(result.text, source=source, max_len=8000), code=result.code,
                            field=result.field)
        return wrap_untrusted(result.text, source=source, max_len=MAX_RESULT)

    # --- prompts ------------------------------------------------------------------------------------------------

    def prompts(self) -> List[str]:
        result = self._run(self._client.list_prompts(), CONNECT_TIMEOUT)
        return sorted(str(getattr(p, "name", "")) for p in getattr(result, "prompts", []) or [] if getattr(p, "name", ""))

    def prompt_messages(self, name: str, arguments: Optional[Dict[str, str]] = None) -> List[str]:
        """The texts of the server's task prompt, one per message (the framework's prompts: the instructions, then the
        context it put together). Raw remote text: whoever uses it wraps it as untrusted data."""
        result = self._run(self._client.get_prompt(name, arguments or {}), CONNECT_TIMEOUT)
        parts = []
        for message in getattr(result, "messages", []) or []:
            content = getattr(message, "content", None)
            parts.append(str(getattr(content, "text", "") or ""))
        return parts

    def prompt(self, name: str, arguments: Optional[Dict[str, str]] = None) -> str:
        """The server's task prompt (its messages as text), as untrusted data: only used with this server's tools."""
        return wrap_untrusted("\n\n".join(self.prompt_messages(name, arguments)),
                              source=f"remote prompt:{self.server.label()}:{name}")


def _result_text(result: Any) -> str:
    texts = [str(getattr(c, "text", "") or "") for c in getattr(result, "content", []) or []]
    text = "\n".join(t for t in texts if t)
    if not text and getattr(result, "structured_content", None) is not None:
        import json
        text = json.dumps(result.structured_content, ensure_ascii=False, default=str)
    return text


def structured_error(result: Any, listed: Optional["_Listed"] = None) -> Tuple[str, str]:
    """(reason code, field) of a refused or failed remote call, from the result's structured error
    {"error": {"code", "field"}} that the framework's server sends (Ditto's loop tells a fix from a repeat by them).
    Never guessed from the error's words, which are the remote server's untrusted text: a code outside the known
    reason codes, or no structured error at all (an older server), is "failed"; a field the tool's input schema does
    not have is dropped."""
    from orchestrator.tools.normalize import CODES
    data = getattr(result, "structured_content", None)
    error = data.get("error") if isinstance(data, dict) else None
    if not isinstance(error, dict):
        return "failed", ""
    code = error.get("code")
    code = code if isinstance(code, str) and code in CODES else "failed"
    field_name = error.get("field")
    known = (listed.schema.get("properties") or {}) if listed is not None and isinstance(listed.schema, dict) else {}
    field_name = field_name if isinstance(field_name, str) and field_name in known else ""
    return code, field_name


def stored_token(secrets_manager: Any) -> Optional[str]:
    """The named token from Ditto's credential vault (read inside the framework only), or None."""
    if secrets_manager is None:
        return None
    try:
        token = secrets_manager.get_secret(TOKEN_SECRET)
    except Exception:  # noqa: BLE001 - a locked vault: no token
        return None
    return token.strip() if isinstance(token, str) and token.strip() else None


def server_fingerprint(server: RemoteServer) -> str:
    """Names the configured server (transport, command or address, the person's name for it), never the token: keys
    the cached tool listing (hybrid_tools.ListingCache) and tells a settings change."""
    import json
    data = json.dumps([server.transport, list(server.command), server.url, server.name], ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:32]


class RemoteSession:
    """Ditto's connection to the framework on another computer, kept across turns (hybrid design 6.7): opened lazily,
    closed after IDLE_SECONDS without a call, on a settings change (another server, another token, or a turn that
    needs the other dialog capability) and on close(); Ditto reopens it at most once per turn. Each turn binds its
    person-facing dialog (or none); a question that arrives while none is bound is cancelled, never answered "no".
    The token stays in the connection; the session keeps only a hash of it to tell a change."""

    def __init__(self, server: RemoteServer, token: Optional[str], *, elicitation: bool,
                 client_factory: Optional[Callable[[Any], Any]] = None, idle: float = IDLE_SECONDS):
        self.server = server
        self.elicitation = bool(elicitation)
        self._key = self.key_of(server, token, elicitation)
        self._token = token
        self._client_factory = client_factory
        self._idle = float(idle)
        self._lock = threading.RLock()
        self._connection: Optional[RemoteConnection] = None
        self._listing = None
        self._timer: Optional[threading.Timer] = None
        self._busy = 0

    @staticmethod
    def key_of(server: RemoteServer, token: Optional[str], elicitation: bool) -> str:
        token_hash = hashlib.sha256((token or "").encode("utf-8")).hexdigest()
        return hashlib.sha256(f"{server_fingerprint(server)}|{token_hash}|{bool(elicitation)}".encode("ascii")
                              ).hexdigest()

    def matches(self, server: RemoteServer, token: Optional[str], elicitation: bool) -> bool:
        return self._key == self.key_of(server, token, elicitation)

    @property
    def is_open(self) -> bool:
        return self._connection is not None

    def open(self) -> "RemoteSession":
        """Connects when not connected yet; RemoteSetupError when it cannot (the message names why, never the token)."""
        with self._lock:
            if self._connection is None:
                connection = RemoteConnection(self.server, self._token, None, client_factory=self._client_factory,
                                              elicitation=self.elicitation)
                connection.open()
                self._connection, self._listing = connection, None
            self._touch()
            return self

    def reopen(self) -> "RemoteSession":
        """Drops a broken connection and connects again (RemoteSetupError when that fails)."""
        with self._lock:
            confirm = self._connection.confirm if self._connection is not None else None
            self._drop()
            self.open()
            self.bind(confirm)
            return self

    def bind(self, confirm: Optional[Callable]) -> None:
        """The person-facing dialog of the current turn (None between turns and for a turn without one)."""
        with self._lock:
            if self._connection is not None:
                self._connection.confirm = confirm if self.elicitation else None

    def _need(self) -> RemoteConnection:
        connection = self._connection
        if connection is None:
            raise RemoteSetupError(f"The connection to the framework at {self.server.label()} is closed.")
        return connection

    def listing(self):
        """The server's tools (RemoteConnection.listing), fetched once per connection."""
        with self._lock:
            connection = self._need()
            if self._listing is None:
                self._listing = connection.listing()
            self._touch()
            return self._listing

    def call_raw(self, name: str, arguments: Dict[str, Any]) -> RemoteResult:
        connection = self._need()
        with self._lock:
            self._busy += 1
        try:
            return connection.call_raw(name, arguments)
        finally:
            with self._lock:
                self._busy -= 1
                self._touch()

    def prompts(self) -> List[str]:
        return self._need().prompts()

    def prompt_messages(self, name: str, arguments: Optional[Dict[str, str]] = None) -> List[str]:
        return self._need().prompt_messages(name, arguments)

    def _touch(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(self._idle, self._idle_close)
        self._timer.daemon = True
        self._timer.start()

    def _idle_close(self) -> None:
        with self._lock:
            if self._busy:  # a call (a question to the person, a long build start) is still running
                self._touch()
                return
            self._drop()

    def _drop(self) -> None:
        connection, self._connection, self._listing = self._connection, None, None
        if connection is not None:
            connection.close()

    def close(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            self._drop()
