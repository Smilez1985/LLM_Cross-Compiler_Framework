#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Ditto as an ACP agent (`llm-cli acp`)

The Agent Client Protocol (ACP, version 1, JSON-RPC over stdio; the official Python SDK `agent-client-protocol`,
import name `acp`) lets an editor such as Zed, a JetBrains IDE, Android Studio, Neovim or Emacs start an agent and
chat with it. This module is a new, additional way in to Ditto: the editor is the client, Ditto is the agent, and
through Ditto the editor drives the framework. Ditto's chat in the GUI and the CLI stays exactly as it is; this adapter
reuses it (DittoCoder.chat with a ChatTurn), and every tool call goes through the same ToolRegistry.call() path as
everywhere (input schema, the person's tool permissions, confirmation, pending approvals, audit log, output filter).
It belongs to the CLI adapter (started only by `llm-cli acp`) and imports no other adapter.

Mapping
- initialize: protocol version 1, agentInfo, capabilities (no session loading: an ACP session lives in memory for the
  connection; text and resource links in prompts; MCP servers over stdio only, as the protocol requires), and while
  Ditto has no AI provider set up, one `terminal` auth method that runs the existing sign-in (`llm-cli acp --login`,
  which leads to `llm-cli ditto login`). authenticate checks the sign-in again; session/new and session/prompt answer
  "authentication required" (-32000) until Ditto can answer.
- session/new: `cwd` and `additionalDirectories` become folder labels (project, project-2, ...) next to the framework's
  own folder labels; the model only ever sees `label:relative/path`, never a host path. `mcpServers` are recorded
  (Core/acp_editor_servers.py) and none is used. The reply carries the config options (provider and model for this
  session only) and the slash commands follow as available_commands_update.
- session/prompt: one Ditto turn. The first prompt of a session starts with Ditto's experimental notice; the answer
  comes as agent_message_chunk, what the model says between its tool calls as agent_thought_chunk, every registry
  call as tool_call (in_progress) and tool_call_update (completed or failed, with its result), a build Ditto started
  as its own tool call with in_progress updates and a plan of the four build stages. Stop reasons: end_turn,
  max_tokens, max_turn_requests, refusal, cancelled.
- session/request_permission: the registry's "ask" (the same question text the framework's own window shows), with
  only "allow once" and "reject" (standing approvals stay in the framework's own approval window). A question only the
  framework's window may ask (deletions, tools set to "confirm in the framework") and a question the editor cannot
  answer (no reply, an error) become a pending approval in the framework, as for an MCP client.
- session/cancel: the turn's cancel event (no further model turn or tool call starts; a running handler that checks
  its context stops early; an open permission request is answered "cancelled").
- session/set_config_option: provider and model for this session only, through the shared settings overlay
  (DittoCoder.for_run); the saved AI settings never change, and the LLM layer's rules (offline mode, the privacy
  filter for every cloud call) apply as for every other question.
- The editor's fs/* and terminal/* are never used: the client capabilities are read, never called.

stdout carries only ACP messages: the protocol gets its own copy of the stdout descriptor, descriptor 1 then points at
stderr (child processes, C code) and sys.stdout is stderr too; logs go to stderr.
"""

import asyncio
import contextlib
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from acp import RequestError, run_agent
from acp.helpers import (plan_entry, start_tool_call, text_block, tool_content, update_agent_message_text,
                         update_agent_thought_text, update_available_commands, update_plan, update_tool_call)
from acp.schema import (AgentCapabilities, AuthenticateResponse, AvailableCommand, AvailableCommandInput,
                        CloseSessionResponse, DeniedOutcome, Implementation, InitializeResponse, McpCapabilities,
                        NewSessionResponse, PermissionOption, PromptCapabilities, PromptResponse,
                        SessionCapabilities, SessionCloseCapabilities, SessionConfigOptionSelect,
                        SessionConfigSelectOption, SetSessionConfigOptionResponse, TerminalAuthMethod, ToolCallUpdate,
                        UnstructuredCommandInput)

from orchestrator import __version__
from orchestrator.utils.logging import get_logger

logger = get_logger("AcpAgent")

PROTOCOL_VERSION = 1            # ACP's stable major version (the SDK's acp.PROTOCOL_VERSION)
AGENT_NAME = "llmcf"
AGENT_TITLE = "Ditto (LLM Cross-Compiler Framework)"
CHANNEL = "acp"                 # ToolContext.channel of every call an editor's turn makes (audit, next steps)
LOGIN_METHOD_ID = "llmcf-ditto-login"
LOGIN_ARGS = ["--login"]        # the editor runs the agent's own command with these to sign Ditto in
ALLOW_ONCE, REJECT_ONCE = "allow_once", "reject_once"
SETTINGS_VALUE = "settings"     # a config option's "as in the AI settings"
CONFIG_PROVIDER, CONFIG_MODEL = "provider", "model"
PERMISSION_TIMEOUT = 600.0      # seconds an editor's permission dialog may stay open; then a pending approval
BUILD_POLL_SECONDS = 2.0
MAX_TOOL_TEXT = 4000            # characters of a tool result shown in the editor
MAX_PROMPT = 16000              # characters of one prompt taken from the editor
MAX_SESSIONS = 16               # sessions one connection may keep open
BUILD_TOOLS = ("start_build", "start_recipe_build")
BUILD_STAGES = ("source", "config", "convert", "target")   # the four build stages (Core/runtime_layer.FOUR_STAGES)
_FINISHED = ("completed", "error", "cancelled")            # Core/build_service.BUILD_FINISHED
# Slash commands: each one asks Ditto to use one existing tool (the registry decides as for every call).
SLASH_COMMANDS: Tuple[Tuple[str, str, str, str], ...] = (
    ("status", "get_status", "The framework's state: builds, jobs, sign-in, offline mode", ""),
    ("targets", "list_targets", "The hardware targets and the formats they build", ""),
    ("builds", "list_build_requests", "The build requests and how far they are", ""),
    ("mojo", "run_mojo_oracle", "Mojo's check of this computer and its setup", "an area to check, optional"),
)


def _label_text(value: Any, limit: int = 64) -> str:
    """Deprecated name: a self-reported value as one short, harmless line (secrets redacted first). The one
    implementation is Core/acp_common.label, shared with the ACP client."""
    from orchestrator.Core.acp_common import label
    return label(value, limit)


def tool_kind(spec: Any) -> str:
    """The ACP kind of a registry tool, for the editor's icon: read, search, fetch, delete or execute."""
    name = str(getattr(spec, "name", "") or "")
    if getattr(spec, "deletes", False) or name.startswith(("delete_", "remove_")):
        return "delete"
    if name.startswith("search_") or name == "search_knowledge":
        return "search"
    if name.startswith(("download_", "crawl_")) or name == "get_hf_model_info":
        return "fetch"
    tier = getattr(getattr(spec, "tier", None), "value", getattr(spec, "tier", ""))
    return "read" if str(tier) == "read" else "execute"


def folder_roots(framework: Any, cwd: str, additional: Sequence[str] = ()) -> Tuple[Dict[str, str], List[str]]:
    """The folders an AI may name by label in this session: the framework's own (state, output, models, ...) and the
    editor's project folders (project, project-2, ...). A relative path from the editor gets no label (its file names
    are still all the model sees). Returns (roots for security.paths.scrub_for_ai, the editor's labels)."""
    roots: Dict[str, str] = {}
    if framework is not None:
        try:
            from orchestrator.Core.folder_labels import ai_folder_labels
            roots.update(ai_folder_labels(framework))
        except Exception as e:  # noqa: BLE001 - no framework labels: the file name rule still applies
            logger.debug(f"No framework folder labels: {e.__class__.__name__}")
    labels: List[str] = []
    for index, folder in enumerate([cwd, *list(additional or [])]):
        if not folder or not os.path.isabs(str(folder)):
            continue
        label = "project" if index == 0 else f"project-{index + 1}"
        key = str(Path(str(folder)))
        if key not in roots:
            roots[key] = label
            labels.append(label)
    return roots, labels


@dataclass
class AcpSession:
    """One editor session: its own conversation (in memory), folder labels, recorded MCP servers, per-session provider
    and model, the running turn's cancel event and the builds it follows."""
    id: str
    roots: Dict[str, str]
    labels: List[str]
    servers: List[Any] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    history: Any = None
    notice_sent: bool = False
    cancel: threading.Event = field(default_factory=threading.Event)
    running: bool = False
    calls: int = 0
    current_call: str = ""
    started_builds: List[str] = field(default_factory=list)
    tasks: List[Any] = field(default_factory=list)
    turns: int = 0


class DittoAcpAgent:
    """The ACP agent (acp.Agent protocol): Ditto behind the editor. `framework`: the booted framework (its Ditto, tool
    registry, build service and state folder); `ditto`, `registry`, `state_dir` and `build_status(request_id)` replace
    the framework's for tests."""

    def __init__(self, framework: Any = None, *, ditto: Any = None, registry: Any = None,
                 state_dir: Optional[Path] = None, build_status: Optional[Callable[[str], Any]] = None,
                 permission_timeout: float = PERMISSION_TIMEOUT, poll_seconds: float = BUILD_POLL_SECONDS):
        self.framework = framework
        self._ditto = ditto
        self._registry = registry
        self._state_dir = Path(state_dir) if state_dir is not None else None
        self._build_status = build_status
        self.permission_timeout = float(permission_timeout)
        self.poll_seconds = float(poll_seconds)
        self.conn: Any = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.sessions: Dict[str, AcpSession] = {}
        self.client_label = ""
        self.client_capabilities: Any = None
        self._turn_sessions: Dict[int, AcpSession] = {}
        self._lock = threading.Lock()
        self._unsubscribe: Optional[Callable[[], None]] = None
        registry_ = self.registry
        events = getattr(registry_, "events", None)
        if events is not None and hasattr(events, "subscribe"):
            self._unsubscribe = events.subscribe(self._on_registry_event)

    # --- wiring ------------------------------------------------------------------------------------------------
    @property
    def ditto(self) -> Any:
        if self._ditto is None and self.framework is not None:
            get = getattr(self.framework, "get_component", None)
            self._ditto = get("ditto_manager") if callable(get) else getattr(self.framework, "ditto_manager", None)
        return self._ditto

    @property
    def registry(self) -> Any:
        if self._registry is not None:
            return self._registry
        ditto = self.ditto
        if ditto is not None and hasattr(ditto, "_registry"):
            return ditto._registry()
        return getattr(self.framework, "tool_registry", None)

    @property
    def state_dir(self) -> Optional[Path]:
        if self._state_dir is not None:
            return self._state_dir
        config = getattr(self.framework, "config", None)
        folder = getattr(config, "user_state_dir", None)
        return Path(folder) if folder else None

    def on_connect(self, conn: Any) -> None:
        self.conn = conn

    def close(self) -> None:
        """The connection ended: open turns are cancelled, build followers stop, the event listener goes."""
        for session in list(self.sessions.values()):
            self._end_session(session)
        if self._unsubscribe is not None:
            with contextlib.suppress(Exception):
                self._unsubscribe()
            self._unsubscribe = None

    def _configured(self) -> bool:
        ditto = self.ditto
        if ditto is None:
            return False
        try:
            return bool(ditto.is_configured("chat"))
        except Exception:  # noqa: BLE001
            return False

    def _require_auth(self) -> None:
        if not self._configured():
            raise RequestError.auth_required({"reason": "Ditto has no AI provider set up yet. Sign Ditto in first "
                                                        "(the editor's sign-in for this agent runs llmcf's sign-in)."})

    def _session(self, session_id: str) -> AcpSession:
        session = self.sessions.get(str(session_id))
        if session is None:
            raise RequestError.invalid_params({"sessionId": "unknown session"})
        return session

    # --- the protocol: initialize, authenticate ----------------------------------------------------------------
    async def initialize(self, protocol_version: int, client_capabilities: Any = None, client_info: Any = None,
                         **kwargs: Any) -> InitializeResponse:
        self.loop = asyncio.get_running_loop()
        self.client_capabilities = client_capabilities
        name = _label_text(getattr(client_info, "name", "") or "unnamed editor")
        version = _label_text(getattr(client_info, "version", "") or "", 32)
        self.client_label = f"{name}{' ' + version if version else ''} (self-reported) over ACP"
        print(f'ACP client: "{name}" {version or "-"} (self-reported), protocol {_label_text(protocol_version, 8)}, '
              f"stdio, channel {CHANNEL}", file=sys.stderr, flush=True)
        methods = None
        if not self._configured():
            methods = [TerminalAuthMethod(
                type="terminal", id=LOGIN_METHOD_ID, name="Sign Ditto in",
                description="Signs Ditto in to an AI provider with llmcf's own sign-in (the provider's browser sign-in "
                            "or a key that goes into llmcf's encrypted vault).", args=list(LOGIN_ARGS))]
        return InitializeResponse(
            protocol_version=PROTOCOL_VERSION,
            agent_info=Implementation(name=AGENT_NAME, title=AGENT_TITLE, version=__version__),
            agent_capabilities=AgentCapabilities(
                load_session=False,
                prompt_capabilities=PromptCapabilities(image=False, audio=False, embedded_context=False),
                mcp_capabilities=McpCapabilities(http=False, sse=False),
                session_capabilities=SessionCapabilities(close=SessionCloseCapabilities())),
            auth_methods=methods)

    async def authenticate(self, method_id: str, **kwargs: Any) -> AuthenticateResponse:
        if method_id != LOGIN_METHOD_ID:
            raise RequestError.invalid_params({"methodId": "unknown authentication method"})
        ditto = self.ditto
        if ditto is not None:
            with contextlib.suppress(Exception):
                ditto.reload()   # the sign-in ran in another process: read the settings and keys again
        self._require_auth()
        return AuthenticateResponse()

    # --- sessions ----------------------------------------------------------------------------------------------
    async def new_session(self, cwd: str, additional_directories: Optional[List[str]] = None,
                          mcp_servers: Optional[List[Any]] = None, **kwargs: Any) -> NewSessionResponse:
        self._require_auth()
        if len(self.sessions) >= MAX_SESSIONS:
            raise RequestError.invalid_request({"reason": f"at most {MAX_SESSIONS} sessions per connection"})
        from orchestrator.Core.ditto_manager import ChatHistory
        roots, labels = folder_roots(self.framework, cwd, additional_directories or [])
        session = AcpSession(id=f"sess_{os.urandom(8).hex()}", roots=roots, labels=labels, history=ChatHistory(None))
        if mcp_servers:
            session.servers = self._record_servers(mcp_servers)
        self.sessions[session.id] = session
        self._later(self._announce(session))
        return NewSessionResponse(session_id=session.id, config_options=self._config_options(session))

    def _record_servers(self, servers: List[Any]) -> List[Any]:
        state = self.state_dir
        if state is None:
            return []
        from orchestrator.Core.acp_editor_servers import EditorServers
        try:
            return EditorServers(state).record(servers, editor=self.client_label)
        except OSError as e:
            logger.warning(f"The editor's MCP servers could not be recorded: {e.__class__.__name__}")
            return []

    async def close_session(self, session_id: str, **kwargs: Any) -> CloseSessionResponse:
        session = self.sessions.get(str(session_id))
        if session is not None:
            self._end_session(session)
        return CloseSessionResponse()

    def _end_session(self, session: AcpSession) -> None:
        session.cancel.set()
        for task in session.tasks:
            with contextlib.suppress(Exception):
                task.cancel()
        self.sessions.pop(session.id, None)

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        session = self.sessions.get(str(session_id))
        if session is not None:
            session.cancel.set()

    async def _announce(self, session: AcpSession) -> None:
        await asyncio.sleep(0.05)   # after the session/new answer reached the editor
        commands = [AvailableCommand(name=name, description=text,
                                     input=AvailableCommandInput(UnstructuredCommandInput(hint=hint)) if hint
                                     else None)
                    for name, _tool, text, hint in SLASH_COMMANDS]
        await self._send(session.id, update_available_commands(commands))

    def _later(self, coroutine) -> None:
        task = asyncio.get_running_loop().create_task(coroutine)
        task.add_done_callback(lambda t: t.cancelled() or t.exception())

    # --- config options: provider and model for this session only ----------------------------------------------
    def _catalog(self) -> Any:
        try:
            return self.ditto.client.catalog
        except Exception:  # noqa: BLE001 - no catalogue: only "as in the AI settings" is offered
            return None

    def _config_options(self, session: AcpSession) -> List[SessionConfigOptionSelect]:
        catalog = self._catalog()
        providers = [SessionConfigSelectOption(value=SETTINGS_VALUE, name="As in the AI settings")]
        for pid in (catalog.ids() if catalog is not None else []):
            label = getattr(catalog.get(pid), "label", "") or pid
            providers.append(SessionConfigSelectOption(value=pid, name=str(label)))
        models = [SessionConfigSelectOption(value=SETTINGS_VALUE, name="As in the AI settings")]
        preset = catalog.get(session.provider) if (catalog is not None and session.provider) else None
        for name in (getattr(preset, "suggested_models", None) or [])[:40]:
            models.append(SessionConfigSelectOption(value=str(name), name=str(name)))
        if session.model and session.model not in [m.value for m in models]:
            models.append(SessionConfigSelectOption(value=session.model, name=session.model))
        return [
            SessionConfigOptionSelect(type="select", id=CONFIG_PROVIDER, name="AI provider", category="model",
                                      description="For this session only; the saved AI settings stay as they are.",
                                      current_value=session.provider or SETTINGS_VALUE, options=providers),
            SessionConfigOptionSelect(type="select", id=CONFIG_MODEL, name="Model", category="model",
                                      description="For this session only.",
                                      current_value=session.model or SETTINGS_VALUE, options=models)]

    async def set_config_option(self, config_id: str, session_id: str, value: Any,
                                **kwargs: Any) -> SetSessionConfigOptionResponse:
        session = self._session(session_id)
        value = str(value or "")
        if config_id == CONFIG_PROVIDER:
            if value != SETTINGS_VALUE:
                from orchestrator.Core.ai_override import provider_problem
                catalog = self._catalog()
                problem = provider_problem(catalog, value) if catalog is not None else "No provider catalogue."
                if problem:
                    raise RequestError.invalid_params({"value": problem})
            session.provider = "" if value == SETTINGS_VALUE else value
            session.model = ""   # another provider's model never carries over
        elif config_id == CONFIG_MODEL:
            allowed = [o.value for o in self._config_options(session)[1].options]
            if value not in allowed:
                raise RequestError.invalid_params({"value": "not one of the offered models"})
            session.model = "" if value == SETTINGS_VALUE else value
        else:
            raise RequestError.invalid_params({"configId": "unknown config option"})
        return SetSessionConfigOptionResponse(config_options=self._config_options(session))

    # --- prompt turns ------------------------------------------------------------------------------------------
    async def prompt(self, session_id: str, prompt: List[Any], **kwargs: Any) -> PromptResponse:
        session = self._session(session_id)
        self._require_auth()
        if session.running:
            raise RequestError.invalid_request({"reason": "a prompt of this session is still running"})
        self.loop = asyncio.get_running_loop()
        session.running, session.cancel = True, threading.Event()
        try:
            if not session.notice_sent:
                session.notice_sent = True
                await self._send(session.id, update_agent_message_text(self._notice() + "\n\n"))
                from orchestrator.Core.acp_editor_servers import session_notice
                servers = session_notice(session.servers)
                if servers:
                    await self._send(session.id, update_agent_message_text(servers + "\n\n"))
            question, skipped = self.prompt_text(prompt, session)
            if skipped:
                await self._send(session.id, update_agent_message_text(
                    f"({skipped} part(s) of the prompt were not read: Ditto takes text and links here.)\n\n"))
            if not question.strip():
                await self._send(session.id, update_agent_message_text("(The prompt held no text.)"))
                return PromptResponse(stop_reason="end_turn")
            stop, text = await asyncio.to_thread(self._turn, session, question)
            if text:
                await self._send(session.id, update_agent_message_text(text))
            return PromptResponse(stop_reason=stop)
        finally:
            session.running = False

    def _notice(self) -> str:
        from orchestrator.utils.ditto_notice import notice_text
        from orchestrator.utils.localization import current_language
        title, text = notice_text(current_language())
        return f"**{title}** {text}"

    def prompt_text(self, blocks: List[Any], session: AcpSession) -> Tuple[str, int]:
        """The person's question from the editor's content blocks, as the model may read it: text and resource links,
        every host path labelled (project:..., output:..., or only its file name), secrets redacted. Returns (text,
        number of blocks not read)."""
        from orchestrator.security.paths import scrub_for_ai
        parts, skipped = [], 0
        for block in blocks or []:
            kind = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
            get = (lambda k, b=block: b.get(k)) if isinstance(block, dict) else (lambda k, b=block: getattr(b, k, None))
            if kind == "text":
                parts.append(str(get("text") or ""))
            elif kind == "resource_link":
                name, uri = str(get("name") or ""), str(get("uri") or "")
                parts.append(f"[linked: {name} {uri}]".strip())
            else:
                skipped += 1
        text = scrub_for_ai("\n".join(parts), session.roots, limit=MAX_PROMPT)
        command = self._slash_command(text)
        if command:
            text = command
        if session.turns == 0 and session.labels:
            text = (f"(Asked from an editor over ACP. The editor's folders by label: {', '.join(session.labels)}; "
                    "a path under one appears as label:relative/path.)\n" + text)
        return text, skipped

    @staticmethod
    def _slash_command(text: str) -> str:
        """`/status`, `/targets`, ... as the question that asks for its tool ("" when the text is no command)."""
        match = re.match(r"^\s*/([a-z]+)\b\s*(.*)$", text or "", re.S)
        if not match:
            return ""
        for name, tool, description, _hint in SLASH_COMMANDS:
            if name == match.group(1):
                rest = match.group(2).strip()
                return (f"Use the tool {tool} ({description.lower()}) and tell me the result."
                        + (f" Details: {rest}" if rest else ""))
        return ""

    def _session_ditto(self, session: AcpSession) -> Any:
        ditto = self.ditto
        if session.provider or session.model:
            return ditto.for_run(session.provider or None, session.model or None)
        return ditto

    def _turn(self, session: AcpSession, question: str) -> Tuple[str, str]:
        """One Ditto turn in a worker thread: (stop reason, the answer's text)."""
        from orchestrator.Core.ditto_manager import ChatTurn, DittoError
        with self._lock:
            self._turn_sessions[threading.get_ident()] = session
        try:
            ditto = self._session_ditto(session)
            turn = ChatTurn(channel=CHANNEL, client=self.client_label[:400], pending_approvals=True,
                            cancel=session.cancel,
                            on_text=lambda text: self._from_thread(session.id, update_agent_thought_text(text)),
                            on_tool_start=lambda name, title, args: self._tool_started(session, name, title, args))
            reply = ditto.chat(question, session.history, confirm=self._confirm_for(session),
                               on_tool=lambda use: self._tool_done(session, use), turn=turn)
            session.history.apply(reply)
            session.turns += 1
            stop = {"end": "end_turn", "max_tokens": "max_tokens", "max_steps": "max_turn_requests",
                    "cancelled": "cancelled"}.get(reply.stop, "end_turn")
            if session.cancel.is_set():
                stop = "cancelled"
            return stop, reply.text
        except DittoError as e:
            if session.cancel.is_set():
                return "cancelled", ""
            refusal = "refus" in type(getattr(e, "__cause__", None) or e).__name__.lower()
            return ("refusal" if refusal else "end_turn"), str(e)
        finally:
            with self._lock:
                self._turn_sessions.pop(threading.get_ident(), None)

    # --- from the worker thread to the editor ------------------------------------------------------------------
    async def _send(self, session_id: str, update: Any) -> None:
        if self.conn is not None:
            await self.conn.session_update(session_id, update)

    def _from_thread(self, session_id: str, update: Any, timeout: float = 30.0) -> None:
        loop = self.loop
        if loop is None or self.conn is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self._send(session_id, update), loop).result(timeout)
        except Exception as e:  # noqa: BLE001 - an update the editor did not take never stops the turn
            logger.debug(f"An ACP update was not sent: {e.__class__.__name__}")

    def _tool_started(self, session: AcpSession, name: str, title: str, arguments: Dict[str, Any]) -> None:
        session.calls += 1
        session.current_call = f"call_{session.calls}"
        spec = None
        with contextlib.suppress(Exception):
            spec = self.registry.get(name)
        self._from_thread(session.id, start_tool_call(session.current_call, title or name, kind=tool_kind(spec),
                                                      status="in_progress", raw_input=arguments))

    def _tool_done(self, session: AcpSession, use: Any) -> None:
        text = (use.output if use.ok else use.detail) or ("Done." if use.ok else "Not run.")
        text = text if len(text) <= MAX_TOOL_TEXT else text[:MAX_TOOL_TEXT] + " ..."
        self._from_thread(session.id, update_tool_call(session.current_call or f"call_{session.calls}",
                                                       status="completed" if use.ok else "failed",
                                                       content=[tool_content(text_block(text))]))
        with self._lock:
            started, session.started_builds = list(session.started_builds), []
        for request_id in started:
            loop = self.loop
            if loop is not None:
                future = asyncio.run_coroutine_threadsafe(self._follow_build(session, request_id), loop)
                session.tasks.append(future)

    def _on_registry_event(self, kind: str, data: Dict[str, Any]) -> None:
        """The registry's call_done of a build an editor's turn started (same thread as the call): followed."""
        if kind != "call_done" or data.get("channel") != CHANNEL or data.get("tool") not in BUILD_TOOLS:
            return
        request_id = str(data.get("request_id") or "")
        with self._lock:
            session = self._turn_sessions.get(threading.get_ident())
            if session is not None and request_id:
                session.started_builds.append(request_id)

    # --- permission: the registry's "ask" ----------------------------------------------------------------------
    def _confirm_for(self, session: AcpSession):
        def confirm(spec: Any, inp: Any):
            return self._ask_permission(session, spec, inp)
        confirm.confirm_channel = "elicitation"   # the editor's dialog: a client's own form, as an MCP elicitation
        return confirm

    def _ask_permission(self, session: AcpSession, spec: Any, inp: Any):
        from orchestrator.tools.confirmation import MAX_FRAMEWORK, CallTooLong, describe_call
        from orchestrator.tools.registry import CANCELLED, NOT_ASKED, current_question
        if session.cancel.is_set():
            return CANCELLED
        loop = self.loop
        if loop is None or self.conn is None:
            return NOT_ASKED
        try:
            question = current_question() or describe_call(spec, inp.model_dump(mode="json"), actor="Ditto",
                                                            limit=MAX_FRAMEWORK)
        except CallTooLong:
            return NOT_ASKED
        call = ToolCallUpdate(tool_call_id=session.current_call or f"call_{session.calls}",
                              title=str(getattr(spec, "title", "") or getattr(spec, "name", "")),
                              kind=tool_kind(spec), status="pending",
                              content=[tool_content(text_block(question))])
        options = [PermissionOption(option_id=ALLOW_ONCE, name="Allow once", kind="allow_once"),
                   PermissionOption(option_id=REJECT_ONCE, name="Reject", kind="reject_once")]
        future = asyncio.run_coroutine_threadsafe(self.conn.request_permission(session.id, call, options), loop)
        deadline = time.monotonic() + self.permission_timeout
        while True:
            if session.cancel.is_set():
                future.cancel()
                return CANCELLED
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                future.cancel()
                return NOT_ASKED          # nobody answered in the editor: a pending approval in the framework
            try:
                response = future.result(timeout=min(0.2, remaining))
                break
            except TimeoutError:
                continue
            except Exception as e:  # noqa: BLE001 - the editor could not ask (an error, the connection ended)
                logger.info(f"The editor could not ask about '{getattr(spec, 'name', '')}': {e.__class__.__name__}")
                return NOT_ASKED
        outcome = getattr(response, "outcome", None)
        if isinstance(outcome, DeniedOutcome) or getattr(outcome, "outcome", "") == "cancelled":
            return CANCELLED
        chosen = getattr(outcome, "option_id", None)
        if chosen == ALLOW_ONCE:
            return True
        if chosen == REJECT_ONCE:
            return False
        return NOT_ASKED              # an option this agent never offered: nobody's valid answer

    # --- builds Ditto started: their own tool call with progress and a plan ------------------------------------
    def _status_of(self, request_id: str) -> Dict[str, Any]:
        if self._build_status is not None:
            status = self._build_status(request_id)
        else:
            service = getattr(self.framework, "build_service", None)
            if service is None:
                from orchestrator.Core.build_service import BuildService
                service = BuildService(self.framework)
            status = service.get_status(request_id)
        get = (status.get if isinstance(status, dict) else (lambda k, d=None: getattr(status, k, d)))
        return {"status": str(get("status", "") or ""), "stage": str(get("stage", "") or ""),
                "percent": int(get("progress_percent", 0) or 0), "end_reason": get("end_reason", None)}

    @staticmethod
    def build_plan(stage: str, finished: bool, ok: bool) -> List[Any]:
        """The four build stages as plan entries: those before the current one completed, the current one in
        progress, the rest pending (all completed for a build that ended done). Fixed names only, never the stage's
        own text."""
        current = next((i for i, name in enumerate(BUILD_STAGES) if name in (stage or "").lower()), None)
        entries = []
        for index, name in enumerate(BUILD_STAGES):
            if finished and ok:
                status = "completed"
            elif current is None:
                status = "pending"
            elif index < current:
                status = "completed"
            elif index == current:
                status = "pending" if finished else "in_progress"
            else:
                status = "pending"
            entries.append(plan_entry(f"Build stage: {name}", priority="medium", status=status))
        return entries

    async def _follow_build(self, session: AcpSession, request_id: str) -> None:
        call_id = f"build_{_label_text(request_id, 40)}"
        await self._send(session.id, start_tool_call(call_id, f"Build {_label_text(request_id, 40)}",
                                                     kind="execute", status="in_progress"))
        last = None
        while session.id in self.sessions:
            try:
                state = await asyncio.to_thread(self._status_of, request_id)
            except Exception as e:  # noqa: BLE001 - the build cannot be read (any more)
                await self._send(session.id, update_tool_call(call_id, status="failed", content=[tool_content(
                    text_block(f"The build's state cannot be read: {e.__class__.__name__}"))]))
                return
            finished = state["status"].lower() in _FINISHED or state["end_reason"] is not None
            ok = state["end_reason"] == "done" or (state["end_reason"] is None and state["status"] == "completed")
            key = (state["status"], state["stage"], state["percent"], finished)
            if key != last:
                last = key
                stage = next((n for n in BUILD_STAGES if n in state["stage"].lower()), "")
                text = f"{state['status']}, {state['percent']}%" + (f" (stage {stage})" if stage else "")
                await self._send(session.id, update_plan(self.build_plan(state["stage"], finished, ok)))
                await self._send(session.id, update_tool_call(
                    call_id, status=("completed" if ok else "failed") if finished else "in_progress",
                    content=[tool_content(text_block(text))]))
            if finished:
                return
            await asyncio.sleep(self.poll_seconds)


# --- stdio: stdout belongs to the protocol -----------------------------------------------------------------------

class _ProtocolOut(asyncio.BaseTransport):
    """Writes the protocol to its own copy of the stdout descriptor (every other writer gets stderr)."""

    def __init__(self, stream):
        super().__init__()
        self._stream = stream
        self._closing = False
        self._lock = threading.Lock()

    def write(self, data: bytes) -> None:
        if self._closing:
            return
        with self._lock:
            self._stream.write(data)
            self._stream.flush()

    def is_closing(self) -> bool:
        return self._closing

    def close(self) -> None:
        self._closing = True
        with contextlib.suppress(Exception):
            self._stream.flush()

    def can_write_eof(self) -> bool:
        return False

    def get_extra_info(self, name, default=None):
        return default


class _Drain(asyncio.BaseProtocol):
    async def _drain_helper(self) -> None:
        return None


async def _protocol_streams(stdin, protocol_out) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """(reader of stdin, writer to the protocol's stdout copy) for the SDK, the same on every system: stdin is read
    line by line in a thread (Windows pipes and consoles take no asyncio read pipe)."""
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader(limit=50 * 1024 * 1024)

    def feed() -> None:
        try:
            while True:
                line = stdin.readline()
                if not line:
                    break
                loop.call_soon_threadsafe(reader.feed_data, line)
        except (OSError, ValueError):
            pass
        finally:
            loop.call_soon_threadsafe(reader.feed_eof)

    threading.Thread(target=feed, name="acp-stdin", daemon=True).start()
    writer = asyncio.StreamWriter(_ProtocolOut(protocol_out), _Drain(), None, loop)
    return reader, writer


def _take_stdout():
    """The protocol's own copy of stdout; descriptor 1 and sys.stdout then point at stderr."""
    with contextlib.suppress(AttributeError, OSError, ValueError):
        sys.stdout.flush()
    fd = os.dup(1)
    with contextlib.suppress(OSError):
        os.dup2(2, 1)
    sys.stdout = sys.stderr
    return os.fdopen(fd, "wb", buffering=0)


async def serve_streams(agent: DittoAcpAgent, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Runs `agent` over the given streams until the editor closes them (also the tests' entry point)."""
    try:
        await run_agent(agent, writer, reader)
    finally:
        agent.close()


def serve(framework: Any, agent: Optional[DittoAcpAgent] = None) -> None:
    """`llm-cli acp`: Ditto as an ACP agent over stdio until the editor ends the connection; the framework is shut down
    afterwards."""
    from orchestrator.utils.stdio_programs import logging_to_stderr, sigterm_ends_the_session
    logging_to_stderr()
    protocol_out = _take_stdout()
    stdin = getattr(sys.stdin, "buffer", sys.stdin)
    agent = agent or DittoAcpAgent(framework)

    async def main() -> None:
        reader, writer = await _protocol_streams(stdin, protocol_out)
        await serve_streams(agent, reader, writer)

    try:
        with sigterm_ends_the_session():
            asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("ACP agent stopped by a signal.")
    finally:
        with contextlib.suppress(Exception):
            protocol_out.close()
        shutdown = getattr(framework, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Framework shutdown after the ACP session failed: {e.__class__.__name__}")


__all__ = ["AGENT_NAME", "CHANNEL", "DittoAcpAgent", "LOGIN_ARGS", "LOGIN_METHOD_ID", "PROTOCOL_VERSION",
           "SLASH_COMMANDS", "folder_roots", "serve", "serve_streams", "tool_kind"]
