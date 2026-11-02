#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - the client side of ACP: one running agent the person started (Core/acp_hosts.py).

AgentSession starts the agent's process from its LaunchSpec (the clean environment, the agent's working folder, only
its own keys), speaks ACP version 1 over its stdio with the SDK's typed models (`acp`, agent-client-protocol 0.12.1)
and owns that one process: closing the session ends it (its input closed, then terminate, then kill: only the PID it
started, never a process found by name).

What the framework tells the agent in `initialize`: protocol version 1, NO file system and NO terminal capability
(the agent works through the framework's own tools only), the terminal sign-in (so an agent can offer its own login
command, which the framework runs in a console window the person sees) and form elicitation only (a form that asks
for something secret is declined: secrets never go through a form). `session/new` carries the framework's MCP
server as the one stdio entry, so every tool call of the agent goes through the tool registry, the person's
permissions, the approvals and the audit log under the channel label "acp-client:<agent name>".

The host (the GUI's chat panel, or the terminal of `llm-cli agents run`) gets every `session/update` as an AgentEvent
(text and thoughts in chunks, tool calls with their status, plans) and answers `session/request_permission` and form
elicitations itself (`AgentHost`, called on a worker thread; it may block until the person answers). Only "allow
once" and "reject" are ever selected: an agent's "always" option is never chosen (the framework's standing approvals
are made in their own window). Everything an agent sends is outside text: the host shows it marked as the agent's.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import os
import subprocess  # nosec B404 - the person's agent, started from its LaunchSpec argv (no shell)
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from orchestrator.Core.acp_hosts import PROTOCOL_VERSION, AgentError, LaunchSpec, clean_env
from orchestrator.utils.logging import get_logger

logger = get_logger("AcpClient")

START_TIMEOUT = 60.0      # seconds for initialize and session/new
PROMPT_TIMEOUT = None     # a turn runs until the agent ends it or the person cancels it
STOP_GRACE = 2.0          # seconds after the input is closed before terminate, and again before kill
STDERR_TAIL = 4000        # the last characters of the agent's own log kept for a failure message
SECRET_WORDS = ("password", "passwort", "secret", "token", "api key", "apikey", "api_key", "credential", "private key",
                "passphrase", "otp", "2fa", "pin")


@dataclass(frozen=True)
class AgentEvent:
    """One update of the agent for the host. kind: text | thought | tool | plan | notice. Every text is the agent's."""
    kind: str
    text: str = ""
    tool_id: str = ""
    status: str = ""                     # tool: pending | in_progress | completed | failed
    entries: Tuple[Tuple[str, str], ...] = ()   # plan: (content, status)


@dataclass(frozen=True)
class PermissionQuestion:
    """The agent asks before one of its tool calls. `has_always`: it offered an "always" option, which the framework
    never selects (the host may point the person to the standing approvals instead)."""
    title: str
    kind: str
    has_always: bool
    allow_option: str = ""
    reject_option: str = ""


@dataclass(frozen=True)
class FormQuestion:
    """A form the agent asks the person to fill (elicitation, form mode): plain fields only, never a secret."""
    message: str
    fields: Tuple[Tuple[str, str, str], ...]   # (name, kind: string|boolean|number|choice, title)
    choices: Dict[str, Tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class AuthMethod:
    """A sign-in method the agent offers: agent (its own browser login), terminal (its login command), env_var (keys
    the person stores in the credential vault)."""
    id: str
    name: str
    type: str
    description: str = ""
    variables: Tuple[str, ...] = ()
    args: Tuple[str, ...] = ()
    env: Tuple[Tuple[str, str], ...] = ()


class AgentHost:
    """What the session needs from its window or terminal. The defaults answer "no" to everything."""

    def on_event(self, event: AgentEvent) -> None:  # pragma: no cover - a host overrides it
        pass

    def ask_permission(self, question: PermissionQuestion) -> str:
        """'allow' (once) or 'reject'; called on a worker thread, may block until the person answers."""
        return "reject"

    def ask_form(self, question: FormQuestion) -> Optional[Dict[str, Any]]:
        """The person's values, or None to decline."""
        return None


def _outside(value: Any, limit: int = 2000) -> str:
    """Text from the agent, as one cleaned string (control characters dropped, bidi controls shown, cut)."""
    from orchestrator.Core.chat_feed import clean_ai_text
    return clean_ai_text(str(value or ""))[:limit]


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    text = getattr(content, "text", None)
    if isinstance(text, str):
        return text
    inner = getattr(content, "content", None)
    if inner is not None:
        return _content_text(inner)
    uri = getattr(content, "uri", None)
    return f"[{getattr(content, 'type', 'content')}: {uri}]" if uri else ""


def looks_secret(*texts: Any) -> bool:
    """Whether a form field asks for something secret (by its name, title, description or format)."""
    joined = " ".join(str(t or "") for t in texts).lower().replace("-", " ").replace("_", " ")
    return any(word in joined for word in SECRET_WORDS)


def form_question(message: str, schema: Any) -> Optional[FormQuestion]:
    """The form as plain fields; None when any field looks secret or is no plain field (it is declined)."""
    fields: List[Tuple[str, str, str]] = []
    choices: Dict[str, Tuple[str, ...]] = {}
    for name, prop in (getattr(schema, "properties", None) or {}).items():
        kind = getattr(prop, "type", "")
        title = _outside(getattr(prop, "title", "") or name, 200)
        if looks_secret(name, title, getattr(prop, "description", ""), getattr(prop, "format", "")):
            return None
        enum = getattr(prop, "enum", None)
        if kind == "string" and enum:
            fields.append((name, "choice", title))
            choices[name] = tuple(_outside(e, 200) for e in enum)
        elif kind in ("string", "boolean", "number", "integer"):
            fields.append((name, "number" if kind == "integer" else kind, title))
        else:
            return None
    return FormQuestion(_outside(message, 1000), tuple(fields), choices)


class _Client:
    """The framework's side of the ACP connection (the SDK calls these on the session's loop)."""

    def __init__(self, session: "AgentSession"):
        self.session = session

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self.session._on_update(update)

    async def request_permission(self, session_id: str, tool_call: Any, options: List[Any], **kwargs: Any):
        from acp.schema import AllowedOutcome, DeniedOutcome, RequestPermissionResponse
        allow = next((o for o in options if o.kind == "allow_once"), None)
        reject = next((o for o in options if o.kind == "reject_once"), None) or \
            next((o for o in options if o.kind == "reject_always"), None)
        question = PermissionQuestion(title=_outside(getattr(tool_call, "title", "") or "a tool call", 300),
                                      kind=str(getattr(tool_call, "kind", "") or ""),
                                      has_always=any(o.kind == "allow_always" for o in options),
                                      allow_option=allow.option_id if allow else "",
                                      reject_option=reject.option_id if reject else "")
        answer = await self.session._ask(self.session.host.ask_permission, question)
        self.session._permission_answered(question, answer)
        if answer == "allow" and allow is not None:
            return RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", option_id=allow.option_id))
        if answer in ("allow", "reject") and reject is not None:
            return RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", option_id=reject.option_id))
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    async def create_elicitation(self, message: str, mode: Any, **kwargs: Any):
        from acp.schema import AcceptElicitationResponse, DeclineElicitationResponse
        schema = getattr(mode, "requested_schema", None)
        question = form_question(message, schema) if schema is not None else None
        if question is None:   # url mode, or a form asking for a secret: declined
            self.session._notice("The agent asked for something in a form that the framework does not ask for "
                                 "(a secret, or a sign-in page): declined.")
            return DeclineElicitationResponse(action="decline")
        values = await self.session._ask(self.session.host.ask_form, question)
        if values is None:
            return DeclineElicitationResponse(action="decline")
        return AcceptElicitationResponse(action="accept", content=dict(values))

    async def complete_elicitation(self, elicitation_id: str, **kwargs: Any) -> None:
        return None

    async def _refuse(self, method: str):
        from acp import RequestError
        self.session._notice(f"The agent asked for {method}, which the framework does not offer; it works through "
                             "the framework's own tools.")
        raise RequestError.method_not_found(method)

    async def read_text_file(self, *args: Any, **kwargs: Any):
        await self._refuse("fs/read_text_file")

    async def write_text_file(self, *args: Any, **kwargs: Any):
        await self._refuse("fs/write_text_file")

    async def create_terminal(self, *args: Any, **kwargs: Any):
        await self._refuse("terminal/create")

    async def terminal_output(self, *args: Any, **kwargs: Any):
        await self._refuse("terminal/output")

    async def release_terminal(self, *args: Any, **kwargs: Any):
        await self._refuse("terminal/release")

    async def wait_for_terminal_exit(self, *args: Any, **kwargs: Any):
        await self._refuse("terminal/wait_for_exit")

    async def kill_terminal(self, *args: Any, **kwargs: Any):
        await self._refuse("terminal/kill")

    async def ext_method(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        from acp import RequestError
        raise RequestError.method_not_found(method)

    async def ext_notification(self, method: str, params: Dict[str, Any]) -> None:
        return None


class AgentSession:
    """One started agent: its process, the ACP connection and at most one session. Thread-safe from the outside:
    every call runs on the session's own event loop thread and waits for its result."""

    def __init__(self, spec: LaunchSpec, host: Optional[AgentHost] = None, store: Any = None, actor: str = "gui",
                 log_dir: Optional[Path] = None):
        self.spec = spec
        self.host = host or AgentHost()
        self.store = store
        self.actor = actor
        self.log_dir = Path(log_dir) if log_dir else spec.cwd.parent.parent / "logs"
        self.process: Optional[asyncio.subprocess.Process] = None
        self.conn: Any = None
        self.session_id: str = ""
        self.agent_name: str = ""
        self.auth_methods: List[AuthMethod] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._log: Any = None
        self._closed = False
        self._lock = threading.Lock()

    # --- the loop ---------------------------------------------------------------------------------------------------

    def _run(self, coro: Any, timeout: Optional[float]) -> Any:
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(target=self._loop.run_forever, name=f"acp-{self.spec.entry.id}",
                                            daemon=True)
            self._thread.start()
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise AgentError(f"{self.spec.entry.name} did not answer in time.", "failed") from None

    async def _ask(self, ask: Callable[[Any], Any], question: Any) -> Any:
        """Asks the host on a worker thread (it may block until the person answers)."""
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, ask, question)
        except Exception as e:  # noqa: BLE001 - a host that fails answers "no"
            logger.warning(f"The question of the agent could not be asked: {type(e).__name__}")
            return None

    @property
    def pid(self) -> Optional[int]:
        return self.process.pid if self.process is not None else None

    def running(self) -> bool:
        return self.process is not None and self.process.returncode is None and not self._closed

    # --- events -----------------------------------------------------------------------------------------------------

    def _emit(self, event: AgentEvent) -> None:
        try:
            self.host.on_event(event)
        except Exception as e:  # noqa: BLE001 - the window never stops the session
            logger.warning(f"An agent update could not be shown: {type(e).__name__}")

    def _notice(self, text: str) -> None:
        self._emit(AgentEvent("notice", text))

    def _on_update(self, update: Any) -> None:
        kind = getattr(update, "session_update", "")
        if kind == "agent_message_chunk":
            self._emit(AgentEvent("text", _outside(_content_text(update.content), 32_000)))
        elif kind == "agent_thought_chunk":
            self._emit(AgentEvent("thought", _outside(_content_text(update.content), 32_000)))
        elif kind in ("tool_call", "tool_call_update"):
            self._emit(AgentEvent("tool", _outside(getattr(update, "title", "") or "", 300),
                                  tool_id=str(update.tool_call_id)[:100], status=str(update.status or "")))
        elif kind == "plan":
            entries = tuple((_outside(e.content, 300), str(e.status)) for e in update.entries[:50])
            self._emit(AgentEvent("plan", entries=entries))

    def _permission_answered(self, question: PermissionQuestion, answer: Any) -> None:
        if self.store is not None:
            outcome = {"allow": "allowed once", "reject": "rejected"}.get(str(answer), "cancelled")
            self.store.record_event(self.actor, self.spec.entry, f"permission {outcome}: {question.title[:120]}")

    # --- starting ---------------------------------------------------------------------------------------------------

    def start(self) -> List[AuthMethod]:
        """Starts the process and initializes ACP v1; the sign-in methods the agent offers. AgentError when it does not
        start or speaks another version."""
        return self._run(self._start(), START_TIMEOUT)

    async def _start(self) -> List[AuthMethod]:
        import acp
        from acp.schema import (AuthCapabilities, ClientCapabilities, ElicitationCapabilities,
                                ElicitationFormCapabilities, FileSystemCapabilities, Implementation)
        self.log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._log = open(self.log_dir / f"{self.spec.entry.id}.log", "ab")  # noqa: SIM115 - closed in close()
        flags = {}
        if os.name == "nt":
            flags["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.process = await asyncio.create_subprocess_exec(
                *self.spec.argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=self._log,
                env=self.spec.env, cwd=str(self.spec.cwd), limit=50 * 1024 * 1024, **flags)
        except OSError as e:
            raise AgentError(f"{self.spec.entry.name} could not be started ({e.strerror or type(e).__name__}).",
                             "failed") from None
        if self.store is not None:
            self.store.record_event(self.actor, self.spec.entry, f"started (process {self.process.pid})")
        # use_unstable_protocol: the SDK 0.12.1 still routes elicitation/create (form mode, stable in ACP since
        # 2026-07-22) as unstable; nothing else of the unstable surface is offered (no capability says so).
        self.conn = acp.connect_to_agent(_Client(self), self.process.stdin, self.process.stdout,
                                         use_unstable_protocol=True)
        from orchestrator import __version__ as version
        try:
            answer = await self.conn.initialize(
                protocol_version=PROTOCOL_VERSION,
                client_capabilities=ClientCapabilities(
                    fs=FileSystemCapabilities(read_text_file=False, write_text_file=False), terminal=False,
                    auth=AuthCapabilities(terminal=True),
                    elicitation=ElicitationCapabilities(form=ElicitationFormCapabilities())),
                client_info=Implementation(name="llm-framework", title="LLM Cross-Compiler Framework",
                                           version=str(version)))
        except Exception as e:  # noqa: BLE001 - the process ended or answered nonsense
            await self._stop()
            raise AgentError(f"{self.spec.entry.name} did not start the Agent Client Protocol "
                             f"({type(e).__name__}).{self._tail()}", "failed") from None
        if answer.protocol_version != PROTOCOL_VERSION:
            await self._stop()
            raise AgentError(f"{self.spec.entry.name} speaks ACP version {answer.protocol_version}; this framework "
                             f"speaks version {PROTOCOL_VERSION}.", "failed")
        info = getattr(answer, "agent_info", None)
        self.agent_name = _outside(getattr(info, "title", None) or getattr(info, "name", "") or "", 100)
        self.auth_methods = [m for m in (self._method(m) for m in (answer.auth_methods or [])) if m is not None]
        return list(self.auth_methods)

    @staticmethod
    def _method(raw: Any) -> Optional[AuthMethod]:
        kind = str(getattr(raw, "type", "") or "agent")
        variables: Tuple[str, ...] = ()
        args: Tuple[str, ...] = ()
        env: Tuple[Tuple[str, str], ...] = ()
        if kind == "env_var":
            variables = tuple(str(v.name) for v in (getattr(raw, "vars", None) or []))
        elif kind == "terminal":
            args = tuple(str(a) for a in (getattr(raw, "args", None) or []) if isinstance(a, str))
            try:
                env = tuple(clean_env(getattr(raw, "env", None) or {}).items())
            except AgentError:
                return None
        elif kind != "agent":
            return None
        return AuthMethod(id=str(raw.id)[:100], name=_outside(raw.name, 100), type=kind,
                          description=_outside(getattr(raw, "description", "") or "", 300), variables=variables,
                          args=args, env=env)

    def authenticate(self, method_id: str, timeout: Optional[float] = 600.0) -> None:
        """Signs in with a method of type "agent": the agent opens its own login (its browser page with the
        provider's sign-in and second factor) and answers when it is done."""
        self._run(self._authenticate(method_id), timeout)

    async def _authenticate(self, method_id: str) -> None:
        try:
            await self.conn.authenticate(method_id=method_id)
        except Exception as e:  # noqa: BLE001
            raise AgentError(f"The sign-in of {self.spec.entry.name} did not finish ({_error_text(e)}).",
                             "not_signed_in") from None
        if self.store is not None:
            self.store.record_event(self.actor, self.spec.entry, f"signed in ({method_id[:60]})")

    def new_session(self) -> str:
        """Opens the ACP session in the agent's working folder, with the framework's MCP server as its tools."""
        return self._run(self._new_session(), START_TIMEOUT)

    async def _new_session(self) -> str:
        from acp.schema import EnvVariable, McpServerStdio
        mcp = self.spec.mcp_server
        server = McpServerStdio(name=mcp["name"], command=mcp["command"], args=list(mcp["args"]),
                                env=[EnvVariable(name=k, value=v) for k, v in sorted(mcp["env"].items())])
        try:
            answer = await self.conn.new_session(cwd=str(self.spec.cwd), mcp_servers=[server])
        except Exception as e:  # noqa: BLE001
            code = getattr(e, "code", None)
            if code == -32000:
                raise AgentError(f"{self.spec.entry.name} wants you to sign in first.", "not_signed_in") from None
            raise AgentError(f"{self.spec.entry.name} did not open a session ({_error_text(e)}).", "failed") from None
        self.session_id = str(answer.session_id)
        return self.session_id

    # --- turns ------------------------------------------------------------------------------------------------------

    def prompt(self, text: str) -> str:
        """Sends the person's message; the turn's updates reach the host as events. The stop reason (end_turn,
        cancelled, refusal, max_tokens, max_turn_requests)."""
        if not self.session_id:
            self.new_session()
        return self._run(self._prompt(text), PROMPT_TIMEOUT)

    async def _prompt(self, text: str) -> str:
        from acp import text_block
        try:
            answer = await self.conn.prompt(session_id=self.session_id, prompt=[text_block(text)])
        except Exception as e:  # noqa: BLE001
            if getattr(e, "code", None) == -32000:
                raise AgentError(f"{self.spec.entry.name} wants you to sign in first.", "not_signed_in") from None
            raise AgentError(f"{self.spec.entry.name} ended the turn with an error ({_error_text(e)}).",
                             "failed") from None
        return str(answer.stop_reason)

    def cancel(self) -> None:
        """Asks the agent to stop the running turn (session/cancel); its open questions end as cancelled."""
        if self._loop is None or not self.session_id or not self.running():
            return
        asyncio.run_coroutine_threadsafe(self._cancel(), self._loop)

    async def _cancel(self) -> None:
        with contextlib.suppress(Exception):
            await self.conn.cancel(session_id=self.session_id)

    # --- stopping ---------------------------------------------------------------------------------------------------

    def close(self) -> None:
        """Ends the session and stops the agent's process (only this PID): input closed, then terminate, then kill."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if self._loop is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._stop(), self._loop).result(STOP_GRACE * 3 + 2)
            except Exception:  # noqa: BLE001 - stopped below by its PID
                self._kill_now()
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread is not None:
                self._thread.join(timeout=STOP_GRACE)
        if self._log is not None:
            with contextlib.suppress(OSError):
                self._log.close()
        if self.store is not None and self.process is not None:
            self.store.record_event(self.actor, self.spec.entry, "stopped")

    async def _stop(self) -> None:
        if self.conn is not None:
            with contextlib.suppress(Exception):
                await self.conn.close()
        process = self.process
        if process is None or process.returncode is not None:
            return
        with contextlib.suppress(Exception):
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), STOP_GRACE)
            return
        except asyncio.TimeoutError:
            pass
        with contextlib.suppress(ProcessLookupError):
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), STOP_GRACE)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(process.wait(), STOP_GRACE)

    def _kill_now(self) -> None:
        process = self.process
        if process is not None and process.returncode is None:
            with contextlib.suppress(ProcessLookupError, OSError):
                process.kill()

    def _tail(self) -> str:
        """The end of the agent's own log, for the person (outside text, one line)."""
        try:
            data = (self.log_dir / f"{self.spec.entry.id}.log").read_bytes()[-STDERR_TAIL:]
        except OSError:
            return ""
        text = " ".join(data.decode("utf-8", "replace").split())[-300:]
        return f" Its last words: {_outside(text, 300)}" if text else ""


def _error_text(error: BaseException) -> str:
    message = getattr(error, "args", [""])[0] if getattr(error, "args", None) else ""
    return _outside(message or type(error).__name__, 200)


# --- signing in with the agent's own login command -------------------------------------------------------------------

def login_argv(spec: LaunchSpec, method: AuthMethod) -> List[str]:
    """The agent's own login command (terminal sign-in): its program and the method's arguments."""
    return [*spec.argv, *method.args]


def login_env(spec: LaunchSpec, method: AuthMethod, store: Any) -> Dict[str, str]:
    """The login command's environment: the clean basics, the entry's settings and the method's own (no vault key:
    the login is how the agent gets its own)."""
    entry = spec.entry
    plain = type(entry)(**{**entry.__dict__, "keys": []})
    env = store.clean_environment(plain) if store is not None else dict(spec.env)
    env.update(dict(method.env))
    return env


def run_login_attached(spec: LaunchSpec, method: AuthMethod, store: Any,
                       runner: Callable[..., Any] = subprocess.run) -> int:
    """The terminal sign-in at the command line: the agent's login command in this terminal, with its keyboard and
    screen (the person types into the agent's own login, the framework never sees it)."""
    result = runner(login_argv(spec, method), env=login_env(spec, method, store), cwd=str(spec.cwd), check=False)
    return int(getattr(result, "returncode", 1) or 0)


def console_argv(argv: Sequence[str], which: Callable[[str], Optional[str]] = None,
                 system: Optional[str] = None) -> Optional[List[str]]:
    """How a console window the person sees runs `argv` (Linux: the first terminal program found; macOS: Terminal
    through a script; Windows: a new console of its own, see open_console). None when no terminal program is found."""
    import platform as _platform
    import shutil
    which = which or shutil.which
    system = system or _platform.system()
    if system == "Windows":
        return list(argv)
    if system == "Darwin":
        return None   # open_console writes a small script for Terminal
    for program, flag in (("x-terminal-emulator", "-e"), ("gnome-terminal", "--"), ("konsole", "-e"),
                          ("xfce4-terminal", "-x"), ("mate-terminal", "-x"), ("kitty", ""), ("alacritty", "-e"),
                          ("xterm", "-e")):
        found = which(program)
        if found:
            return [found, *([flag] if flag else []), *argv]
    return None


def open_console(spec: LaunchSpec, method: AuthMethod, store: Any,
                 popen: Callable[..., Any] = subprocess.Popen) -> Any:
    """Opens the agent's login command in a console window the person sees (GUI, on the person's click). AgentError
    when this computer has no terminal program the framework can open."""
    import platform as _platform
    argv = login_argv(spec, method)
    env = login_env(spec, method, store)
    system = _platform.system()
    if system == "Windows":
        return popen(argv, env=env, cwd=str(spec.cwd), creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    if system == "Darwin":
        import shlex
        script = spec.cwd / ".llmcf-sign-in.command"
        exports = "".join(f"export {k}={shlex.quote(v)}\n" for k, v in sorted(env.items()))
        script.write_bytes(f"#!/bin/sh\n{exports}cd {shlex.quote(str(spec.cwd))}\nexec {shlex.join(argv)}\n"
                           .encode("utf-8"))
        script.chmod(0o700)
        return popen(["open", "-a", "Terminal", str(script)], env=env, cwd=str(spec.cwd))
    command = console_argv(argv, system=system)
    if command is None:
        raise AgentError("No terminal program was found on this computer to show the agent's own sign-in.",
                         "no_runner")
    return popen(command, env=env, cwd=str(spec.cwd))


__all__ = ["AgentEvent", "AgentHost", "AgentSession", "AuthMethod", "FormQuestion", "PermissionQuestion",
           "console_argv", "form_question", "login_argv", "login_env", "looks_secret", "open_console",
           "run_login_attached"]
