#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - ACP agents the person picks (the framework as an ACP client, "bring your own agent").

The person adds external coding agents that speak the Agent Client Protocol (ACP v1, stdio): from the ACP registry
(fetched only when the person asks, through the offline rule of utils/locality, and kept as a cache) or as a command
they name themselves. The framework then starts such an agent on the person's click, gives it the framework's own MCP
server as its one tool source (session/new.mcpServers, Core/acp_client.py), so every action the agent takes goes
through the tool registry, the person's permissions, the approvals and the audit log like any other MCP client.

This module is Qt-free and imports the ACP SDK nowhere (Core/acp_client.py does, when an agent starts). It holds:

- the agents' list (`<framework home>/acp/agents.json`, owner-only) and the cached registry (`acp/registry.json`);
- install per distribution kind: npx and uvx run through the node or uv found on PATH with the exact version the
  registry names (never "latest" or a range: refused otherwise); a binary is downloaded over HTTPS only, with the
  sha256 the registry gives (no checksum, no download: refused with a plain reason), through the framework's one
  pinned downloader and the one disk rule (Core/disk_plan.download_rule);
- the start of an agent (`launch_spec`): a CLEAN environment (PATH, HOME, the locale and the desktop's display
  variables, so its browser sign-in reaches the person's screen; nothing else of the framework's environment) plus that one agent's own keys from the credential vault (`acp/<agent id>/<VARIABLE>`),
  the working folder `acp/workspaces/<agent id>` under the framework home (never the person's home), and the stdio
  entry of the framework's MCP server with its channel label "acp-client:<agent name>";
- removal through Core/deletion (the full list and the person's yes; the trash or for good).

Person-only: adding, installing, signing in and starting an agent are the person's actions in the GUI or at the
terminal. `require_person` refuses every other actor (an MCP client, Ditto, a delegation); no tool of the registry
reaches this module (tests/test_acp_hosts.py guards both).

Offline mode: an agent that needs the network (every registry agent, and an own command unless the person marked it
as local) is refused with a plain reason; a local agent starts. Every refusal names no host path (it may be shown
anywhere); paths are only in the person's own lists.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from orchestrator.utils.logging import get_logger
from orchestrator.utils.next_steps import WithSteps

logger = get_logger("AcpHosts")

REGISTRY_URL = "https://cdn.agentclientprotocol.com/registry/v1/latest/registry.json"
REGISTRY_MAX_BYTES = 4 * 1024 * 1024
PROTOCOL_VERSION = 1
FOLDER = "acp"
AGENTS_FILE = "agents.json"
REGISTRY_FILE = "registry.json"
WORKSPACES = "workspaces"
BINARIES = "bin"
KINDS = ("npx", "uvx", "binary", "command")
REGISTRY_KINDS = ("npx", "uvx", "binary")
# What a first start of an npx or uvx agent may put into the package cache; the disk rule checks it before the yes.
RUNNER_ESTIMATE = 500 * 1024 * 1024
PERSON_ACTORS = ("gui", "cli")
SECRET_PREFIX = "acp/"

# The clean environment of an agent: these, when set, and nothing else of the framework's own environment.
POSIX_BASICS = ("PATH", "HOME", "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "LC_NUMERIC", "LC_TIME",
                "LC_COLLATE", "LC_MONETARY", "TZ", "TERM", "TMPDIR",
                # the person's desktop (no secret): the agent's browser sign-in and the console window of its own
                # login open on the person's screen only with these
                "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")
WINDOWS_BASICS = ("PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "USERPROFILE",
                  "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES",
                  "PROGRAMFILES(X86)", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS", "LANG", "TZ")
# Variables an agent entry may never set (they change what runs, or where it looks): from the registry or by hand.
FORBIDDEN_ENV = re.compile(r"^(?:PATH|PATHEXT|HOME|USERPROFILE|COMSPEC|SYSTEMROOT|SHELL|ENV|BASH_ENV|IFS|"
                           r"LD_\w*|DYLD_\w*|PYTHON\w*|NODE_OPTIONS|NODE_PATH|NPM_CONFIG_\w*|UV_\w*|PERL5\w*|"
                           r"RUBYOPT|RUBYLIB|JAVA_TOOL_OPTIONS|LCF_\w*|LLM_FRAMEWORK_\w*)$", re.IGNORECASE)
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
AGENT_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
NPX_PIN = re.compile(r"^(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*@\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?"
                     r"(?:\+[0-9A-Za-z.-]+)?$")
UVX_PIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9._,-]+\])?==\d[0-9A-Za-z.+!-]*$")
SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
ARCHIVES = (".tar.gz", ".tgz", ".tar", ".zip")


# --- errors ----------------------------------------------------------------------------------------------------------

class AgentError(WithSteps, RuntimeError):
    """A refusal or failure with a plain reason (`code`: unknown, refused, offline, no_runner, no_checksum, invalid,
    failed, registry, not_signed_in). The text names no host path."""

    def __init__(self, message: str, code: str = "failed"):
        super().__init__(message)
        self.code = code


def require_person(actor: Any, what: str = "This") -> str:
    """`actor` when it is the person in the GUI or at the terminal; PermissionError for every other caller (an MCP
    client, Ditto, a delegation, a standing approval): only the person adds, installs, signs in or starts an agent."""
    if isinstance(actor, str) and actor in PERSON_ACTORS:
        return actor
    raise PermissionError(f"{what} is the person's own action, in the framework's window or at the terminal; an AI "
                          "can never add, install, sign in or start an agent.")


def _text(value: Any, limit: int = 300) -> str:
    """A label from outside (the registry, an agent): one printable line, secrets redacted, cut. The one helper is
    Core/acp_common.one_line, shared with Ditto as an ACP agent."""
    from orchestrator.Core.acp_common import one_line
    return one_line(value, limit)


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9._-]+", "-", name.lower()).strip("-._")[:64]
    return slug or "agent"


# --- the registry ----------------------------------------------------------------------------------------------------

def platform_key(system: Optional[str] = None, machine: Optional[str] = None) -> str:
    """This computer as the registry names its binaries: darwin|linux|windows - aarch64|x86_64."""
    system = (system or platform.system()).lower()
    os_name = {"darwin": "darwin", "linux": "linux", "windows": "windows"}.get(system, system)
    machine = (machine or platform.machine()).lower()
    arch = {"amd64": "x86_64", "x86_64": "x86_64", "x64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}.get(
        machine, machine)
    return f"{os_name}-{arch}"


@dataclass(frozen=True)
class RegistryAgent:
    """One agent of the ACP registry, as the person sees it before the yes. Every text came from outside."""
    id: str
    name: str
    version: str
    description: str
    license: str
    repository: str
    distribution: Dict[str, Any]

    def kinds(self) -> List[str]:
        return [k for k in REGISTRY_KINDS if isinstance(self.distribution.get(k), dict)]

    @classmethod
    def from_json(cls, raw: Any) -> Optional["RegistryAgent"]:
        if not isinstance(raw, dict):
            return None
        agent_id = str(raw.get("id") or "")
        dist = raw.get("distribution")
        if not AGENT_ID.fullmatch(agent_id) or not isinstance(dist, dict):
            return None
        return cls(id=agent_id, name=_text(raw.get("name") or agent_id, 100), version=_text(raw.get("version"), 40),
                   description=_text(raw.get("description"), 500), license=_text(raw.get("license"), 80),
                   repository=_text(raw.get("repository"), 300),
                   distribution={k: v for k, v in dist.items() if k in REGISTRY_KINDS and isinstance(v, dict)})


def parse_registry(data: bytes) -> List[RegistryAgent]:
    """The agents of the registry JSON (version 1.x); AgentError("registry") when it is not that."""
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise AgentError("The ACP registry could not be read (it is not JSON).", "registry") from None
    if not isinstance(raw, dict) or not isinstance(raw.get("agents"), list):
        raise AgentError("The ACP registry has no list of agents.", "registry")
    if not str(raw.get("version", "1")).startswith("1"):
        raise AgentError(f"The ACP registry has version {_text(raw.get('version'), 20)}; this framework reads "
                         "version 1.", "registry")
    agents = [a for a in (RegistryAgent.from_json(item) for item in raw["agents"]) if a is not None]
    return sorted({a.id: a for a in agents}.values(), key=lambda a: a.name.lower())


def _clean_args(args: Any) -> List[str]:
    if args is None:
        return []
    if not isinstance(args, list) or len(args) > 32:
        raise AgentError("The agent's arguments are not a short list of words.", "invalid")
    out = []
    for arg in args:
        if not isinstance(arg, str) or not arg or len(arg) > 200 or not arg.isprintable():
            raise AgentError("An argument of the agent is not one printable word.", "invalid")
        out.append(arg)
    return out


def clean_env(env: Any) -> Dict[str, str]:
    """Settings an agent entry passes as environment (the registry's, e.g. DISABLE_AUTO_UPDATE=1): names of letters,
    digits and _, never one that changes what runs or where it looks (FORBIDDEN_ENV); values one printable line."""
    if env is None:
        return {}
    if not isinstance(env, dict) or len(env) > 32:
        raise AgentError("The agent's settings are not a short list of names and values.", "invalid")
    out = {}
    for name, value in env.items():
        if not isinstance(name, str) or not ENV_NAME.fullmatch(name) or FORBIDDEN_ENV.fullmatch(name):
            raise AgentError(f"The agent may not set the variable {_text(name, 64)!r}.", "invalid")
        if not isinstance(value, str) or len(value) > 1000 or not value.isprintable():
            raise AgentError(f"The value of {name} is not one printable line.", "invalid")
        out[name] = value
    return out


# --- the agents' list ------------------------------------------------------------------------------------------------

@dataclass
class AgentEntry:
    """One agent the person added. `kind`: npx | uvx | binary | command; `package`: the exact npx or uvx package with
    its version; `command`: the full path of an own command or of the installed binary (person view only)."""
    id: str
    name: str
    kind: str
    version: str = ""
    description: str = ""
    license: str = ""
    source: str = "command"            # registry | command
    package: str = ""
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    command: str = ""
    network: bool = True               # needs the network (offline mode refuses it)
    keys: List[str] = field(default_factory=list)   # variables whose values live in the credential vault
    added_at: str = ""
    started: bool = False              # it answered a start once (an npx/uvx package is then in the runner's cache)

    def secret_name(self, variable: str) -> str:
        return f"{SECRET_PREFIX}{self.id}/{variable}"

    def ai_view(self) -> Dict[str, Any]:
        """What any caller may see of the entry: labels, never a path or a key value."""
        return {"id": self.id, "name": self.name, "kind": self.kind, "version": self.version,
                "source": self.source, "network": self.network, "keys": list(self.keys)}

    @classmethod
    def from_json(cls, raw: Any) -> Optional["AgentEntry"]:
        if not isinstance(raw, dict):
            return None
        try:
            entry = cls(**{k: raw[k] for k in cls.__dataclass_fields__ if k in raw})
        except TypeError:
            return None
        if not AGENT_ID.fullmatch(str(entry.id)) or entry.kind not in KINDS:
            return None
        return entry


@dataclass(frozen=True)
class AddPreview:
    """What the person sees before the yes: name, description, distribution, licence and the exact command."""
    entry: AgentEntry
    command_line: str
    download: str = ""                 # the URL of a binary download, when there is one
    sha256: str = ""
    problems: Tuple[str, ...] = ()     # why it cannot be added here (no runner, no checksum, offline, ...)

    @property
    def ok(self) -> bool:
        return not self.problems


@dataclass(frozen=True)
class LaunchSpec:
    """How one agent starts: argv, the clean environment, the working folder and the MCP stdio entry it gets."""
    entry: AgentEntry
    argv: Tuple[str, ...]
    env: Dict[str, str]
    cwd: Path
    mcp_server: Dict[str, Any]
    channel: str
    first_start: bool = False          # the first start of a freshly added npx/uvx agent (it may download first)


def _command_line(argv: Sequence[str]) -> str:
    import shlex
    import subprocess  # nosec B404 - list2cmdline only quotes, it runs nothing
    return subprocess.list2cmdline(list(argv)) if os.name == "nt" else shlex.join(list(argv))


class AgentStore:
    """The person's ACP agents in the framework home `state_dir`. `config`: the settings (offline mode);
    `secrets`: the credential vault (SecretsManager); `audit`: the tool audit log (every add, install, start,
    sign-in and removal is recorded); `which`: shutil.which in tests."""

    def __init__(self, state_dir: Path, config: Any = None, secrets: Any = None, audit: Any = None,
                 which: Optional[Callable[..., Optional[str]]] = None, environ: Optional[Dict[str, str]] = None):
        self.state_dir = Path(state_dir)
        self.root = self.state_dir / FOLDER
        self.config = config
        self.secrets = secrets
        self.audit = audit
        self._which = which or shutil.which
        self._environ = environ

    @classmethod
    def for_framework(cls, framework: Any) -> "AgentStore":
        config = getattr(framework, "config", None)
        registry = getattr(framework, "tool_registry", None)
        return cls(Path(getattr(config, "user_state_dir", None) or Path.home() / ".llm-framework"), config=config,
                   secrets=getattr(framework, "secrets_manager", None), audit=getattr(registry, "audit", None))

    # --- files ------------------------------------------------------------------------------------------------------

    @property
    def agents_file(self) -> Path:
        return self.root / AGENTS_FILE

    @property
    def registry_file(self) -> Path:
        return self.root / REGISTRY_FILE

    def workspace(self, entry: AgentEntry) -> Path:
        """The agent's working folder (cwd of the process and of its ACP session), made owner-only."""
        folder = self.root / WORKSPACES / entry.id
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        return folder

    def _lock(self):
        from orchestrator.utils.file_lock import file_lock
        return file_lock(self.root / f".{AGENTS_FILE}.lock", timeout=10)

    def list(self) -> List[AgentEntry]:
        try:
            raw = json.loads(self.agents_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError):
            logger.warning("The list of ACP agents cannot be read; it is shown empty.")
            return []
        items = raw.get("agents") if isinstance(raw, dict) else None
        entries = [e for e in (AgentEntry.from_json(i) for i in items or []) if e is not None]
        return sorted(entries, key=lambda e: e.name.lower())

    def _save(self, entries: Iterable[AgentEntry]) -> None:
        from orchestrator.utils.atomic import write_private
        body = {"version": 1, "agents": [asdict(e) for e in sorted(entries, key=lambda e: e.id)]}
        write_private(self.agents_file, json.dumps(body, indent=1, sort_keys=True))

    def get(self, name: str) -> AgentEntry:
        """The agent with this id or name (case does not matter); AgentError("unknown") otherwise."""
        wanted = str(name or "").strip().lower()
        for entry in self.list():
            if wanted in (entry.id, entry.name.lower()):
                return entry
        raise AgentError(f"There is no agent named {_text(name, 64)!r}. Add it first.", "unknown")

    # --- offline mode -----------------------------------------------------------------------------------------------

    def offline(self) -> bool:
        from orchestrator.utils.locality import offline_mode
        return offline_mode(self.config)

    def check_reach(self, url: str, what: str) -> None:
        """The one offline rule for a reach-out to `url` (utils/locality): refused in offline mode unless the host
        counts as local."""
        if not self.offline():
            return
        from orchestrator.utils.locality import LocalityRefused, check_reach
        verdict = check_reach(url, self.config)
        if not verdict.local:
            raise LocalityRefused(verdict, what)

    def offline_refusal(self, entry: AgentEntry) -> Optional[str]:
        """Why offline mode does not start this agent (None: it may start)."""
        if entry.network and self.offline():
            return (f"Offline mode is on, and {entry.name} needs the internet (it talks to its AI service). Only an "
                    "agent you marked as local starts in offline mode.")
        return None

    # --- the registry -----------------------------------------------------------------------------------------------

    def cached_registry(self) -> Tuple[List[RegistryAgent], Optional[float]]:
        """The registry as last fetched, and when (None: never fetched)."""
        try:
            data = self.registry_file.read_bytes()
            fetched = self.registry_file.stat().st_mtime
        except OSError:
            return [], None
        try:
            return parse_registry(data), fetched
        except AgentError:
            return [], fetched

    def refresh_registry(self, actor: str, fetch: Optional[Callable[[str], bytes]] = None) -> List[RegistryAgent]:
        """Fetches the ACP registry on the person's request (HTTPS, at most 4 MB, the offline rule) and keeps it as
        the cache; the agents in it. `fetch(url) -> bytes` stands in for the download in tests."""
        require_person(actor, "Fetching the ACP registry")
        self.check_reach(REGISTRY_URL, "The ACP registry")
        if fetch is None:
            from orchestrator.utils.download import DownloadError, PinnedDownloader
            try:
                data = PinnedDownloader(logger=logger).get_bytes(REGISTRY_URL, REGISTRY_MAX_BYTES, "registry.json")
            except DownloadError as e:
                raise AgentError(f"The ACP registry could not be fetched: {e}", "registry") from None
        else:
            data = fetch(REGISTRY_URL)
        agents = parse_registry(data)
        from orchestrator.utils.atomic import write_private
        write_private(self.registry_file, data.decode("utf-8"))
        return agents

    def search(self, text: str = "") -> List[RegistryAgent]:
        """The cached registry's agents whose id, name or description holds `text` (all for "")."""
        agents, _ = self.cached_registry()
        needle = (text or "").strip().lower()
        if not needle:
            return agents
        return [a for a in agents if needle in a.id or needle in a.name.lower() or needle in a.description.lower()]

    def registry_agent(self, agent_id: str) -> RegistryAgent:
        agents, fetched = self.cached_registry()
        if fetched is None:
            raise AgentError("The ACP registry was not fetched yet. Fetch it first (search with refresh).", "registry")
        for agent in agents:
            if agent.id == agent_id or agent.name.lower() == str(agent_id).lower():
                return agent
        raise AgentError(f"The ACP registry has no agent {_text(agent_id, 64)!r}.", "unknown")

    # --- previews ---------------------------------------------------------------------------------------------------

    def _runner(self, name: str) -> Optional[str]:
        path = self._environ.get("PATH") if self._environ is not None else os.environ.get("PATH")
        found = self._which(name, path=path)
        return str(Path(found).resolve()) if found else None

    def preview_registry(self, agent_id: str, kind: str = "") -> AddPreview:
        """The registry agent as it would be added: the kind (the given one, else the first that works here: npx
        with node, uvx with uv, a binary with a checksum for this computer), the exact command, the download and why
        it cannot be added (empty when it can)."""
        agent = self.registry_agent(agent_id)
        kinds = agent.kinds()
        if kind and kind not in kinds:
            raise AgentError(f"{agent.name} is not offered as {kind} (offered: {', '.join(kinds) or 'nothing'}).",
                             "invalid")
        previews = [self._preview_kind(agent, k) for k in ([kind] if kind else kinds)]
        if not previews:
            raise AgentError(f"{agent.name} offers no distribution this framework can run.", "invalid")
        for preview in previews:
            if preview.ok:
                return preview
        return previews[0]

    def _preview_kind(self, agent: RegistryAgent, kind: str) -> AddPreview:
        dist = agent.distribution.get(kind) or {}
        problems: List[str] = []
        entry = AgentEntry(id=agent.id, name=agent.name, kind=kind, version=agent.version,
                           description=agent.description, license=agent.license, source="registry", network=True)
        try:
            entry.args = _clean_args(dist.get("args"))
            entry.env = clean_env(dist.get("env"))
        except AgentError as e:
            return AddPreview(entry, "", problems=(str(e),))
        download = digest = ""
        if kind in ("npx", "uvx"):
            entry.package = str(dist.get("package") or "")
            pin = NPX_PIN if kind == "npx" else UVX_PIN
            if not pin.fullmatch(entry.package):
                problems.append(f"The registry names {_text(entry.package, 100) or 'no package'} for {agent.name}, "
                                "not one exact version; the framework runs only an exact version.")
            runner = self._runner("npx" if kind == "npx" else "uvx")
            if runner is None:
                problems.append("Node.js (npx) is not installed on this computer." if kind == "npx"
                                else "uv (uvx) is not installed on this computer.")
            argv = [runner or kind] + (["--yes"] if kind == "npx" else []) + [entry.package] + entry.args
        else:
            target = (dist or {}).get(platform_key())
            if not isinstance(target, dict):
                return AddPreview(entry, "", problems=(f"{agent.name} has no download for this computer "
                                                       f"({platform_key()}).",))
            download = str(target.get("archive") or "")
            digest = str(target.get("sha256") or "")
            cmd = str(target.get("cmd") or "")
            try:
                entry.args = _clean_args(target.get("args")) or entry.args
                entry.env = {**entry.env, **clean_env(target.get("env"))}
            except AgentError as e:
                problems.append(str(e))
            if not download.startswith("https://"):
                problems.append(f"The download of {agent.name} is not offered over HTTPS; it is not fetched.")
            if not SHA256.fullmatch(digest):
                problems.append(f"The ACP registry gives no checksum for the download of {agent.name} for this "
                                "computer, so the framework does not download it. Install it yourself and add it "
                                "as your own command.")
            if not download.endswith(ARCHIVES):
                problems.append(f"The download of {agent.name} is no archive the framework unpacks (.zip, .tar.gz, "
                                ".tgz, .tar).")
            relative = cmd[2:] if cmd.startswith("./") else cmd
            if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts or "\\" in relative:
                problems.append(f"The registry names no program inside the download of {agent.name}.")
            entry.command = relative
            argv = [str(self.root / BINARIES / agent.id / (agent.version or "0") / relative)] + entry.args
        return AddPreview(entry, _command_line(argv), download=download, sha256=digest.lower(),
                          problems=tuple(problems))

    def preview_command(self, name: str, command: str, args: Sequence[str] = (), local: bool = False,
                        keys: Sequence[str] = ()) -> AddPreview:
        """An own command as it would be added: the full path of an existing program, its arguments, whether it works
        without the internet (`local`) and the variables of its keys."""
        problems: List[str] = []
        name = _text(name, 100)
        if not name:
            raise AgentError("The agent needs a name.", "invalid")
        program = Path(os.path.expanduser(str(command or "")))
        if not program.is_absolute():
            found = self._runner(str(command or ""))
            program = Path(found) if found else program
        if not program.is_absolute() or not program.is_file():
            problems.append("The program was not found; give its full path.")
        elif os.name != "nt" and not os.access(program, os.X_OK):
            problems.append("The program cannot be run (it is not executable).")
        variables = []
        for key in keys:
            if not ENV_NAME.fullmatch(str(key)) or FORBIDDEN_ENV.fullmatch(str(key)):
                raise AgentError(f"{_text(key, 64)!r} is no variable name an agent's key may use.", "invalid")
            variables.append(str(key))
        entry = AgentEntry(id=_slug(name), name=name, kind="command", source="command", command=str(program),
                           args=_clean_args(list(args)), network=not local, keys=variables)
        return AddPreview(entry, _command_line([str(program), *entry.args]), problems=tuple(problems))

    # --- adding and installing --------------------------------------------------------------------------------------

    def _record(self, actor: str, outcome: str, arguments: Dict[str, Any], detail: str, **extra: Any) -> None:
        """The audit entry of an agent action (never a key value or a path); RuntimeError when the log is missing or
        cannot write for an action that changes something."""
        audit = self.audit
        if audit is None or not callable(getattr(audit, "record", None)):
            raise RuntimeError("The audit log is not available, so nothing was changed.")
        try:
            audit.record(actor, "acp_agent", "framework", "person", outcome, approved=True, arguments=arguments,
                         detail=detail, extra={"answered_by": "person", "confirm_channel": f"framework-{actor}",
                                               **extra})
        except Exception:  # noqa: BLE001 - the reason could name a host path
            raise RuntimeError("The audit log cannot record this, so nothing was changed.") from None

    def record_event(self, actor: str, entry: AgentEntry, what: str, outcome: str = "ok", detail: str = "") -> None:
        """Records a session event of an agent (started, stopped, signed in, a permission answered) in the audit log.
        Fails open (logged) for an event that changes nothing."""
        try:
            self._record(actor, outcome, {"agent": entry.id, "what": what}, detail or f"ACP agent {entry.name}: {what}")
        except RuntimeError as e:
            logger.warning(f"An ACP agent event was not audited: {e}")

    def add(self, preview: AddPreview, actor: str, *, fetcher: Any = None,
            progress: Optional[Callable[[str, int, Optional[int]], None]] = None) -> AgentEntry:
        """Adds the agent the person said yes to, exactly as previewed (installing a binary first: HTTPS, the
        registry's checksum, the disk rule). AgentError when it cannot be added; PermissionError for anyone but the
        person."""
        require_person(actor, "Adding an agent")
        if not preview.ok:
            raise AgentError(preview.problems[0], "refused")
        entry = AgentEntry(**asdict(preview.entry))
        if preview.download:   # the one reach-out of adding: a binary's download (offline rule)
            self.check_reach(preview.download, f"The download of {entry.name}")
        with self._lock():
            entries = [e for e in self.list() if e.id != entry.id]
            if any(e.name.lower() == entry.name.lower() for e in entries):
                raise AgentError(f"An agent named {entry.name} exists already.", "invalid")
            self._record(actor, "started", {"agent": entry.id, "kind": entry.kind, "version": entry.version},
                         f"ACP agent {entry.name} added ({entry.kind})")
            if entry.kind in ("npx", "uvx"):
                self._check_space(RUNNER_ESTIMATE, f"the first start of {entry.name}")
            if entry.kind == "binary":
                entry.command = str(self._install_binary(entry, preview, fetcher, progress))
            entry.added_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._save([*entries, entry])
        return entry

    def _check_space(self, needed: int, what: str) -> None:
        from orchestrator.Core.disk_plan import ReservationLedger, download_rule
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        refusal = download_rule(ledger=ReservationLedger(self.state_dir))(self.root, int(needed), what)
        if refusal is not None:
            raise AgentError(str(refusal), "no_space")

    def _install_binary(self, entry: AgentEntry, preview: AddPreview, fetcher: Any,
                        progress: Optional[Callable[[str, int, Optional[int]], None]]) -> Path:
        """Downloads the archive (pinned by the registry's sha256; its size from the server before the download, the
        disk rule for it and for the unpacked files), unpacks it into acp/bin/<id>/<version> and returns the
        program's full path."""
        from orchestrator.utils.download import DownloadError
        from orchestrator.utils.helpers import UnsafeArchive, safe_extract_archive
        folder = self.root / BINARIES / entry.id / (entry.version or "0")
        archive_name = Path(preview.download.split("?", 1)[0]).name or "agent-archive"
        downloader = fetcher or AgentDownloader(state_dir=self.state_dir)
        try:
            size = downloader.remote_size(preview.download)
            self._check_space(size * 3, f"the download of {entry.name}")
            archive = downloader.fetch(preview.download, self.root / "downloads" / archive_name, size, preview.sha256,
                                       progress=progress)
        except DownloadError as e:
            raise AgentError(f"The download of {entry.name} failed: {e}", "failed") from None
        if folder.exists():
            from orchestrator.utils.atomic import remove_tree
            remove_tree(folder)
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            safe_extract_archive(archive, folder)
        except (UnsafeArchive, OSError, ValueError) as e:
            raise AgentError(f"The download of {entry.name} could not be unpacked ({type(e).__name__}).",
                             "failed") from None
        finally:
            from orchestrator.utils.atomic import remove_file
            try:
                remove_file(archive)
            except OSError:
                pass
        program = (folder / entry.command).resolve()
        if folder.resolve() not in program.parents or not program.is_file():
            raise AgentError(f"The download of {entry.name} does not hold the program the registry names.", "failed")
        if os.name != "nt":
            program.chmod(program.stat().st_mode | 0o100)
        return program

    def set_key(self, name: str, variable: str, value: str, actor: str) -> None:
        """Stores one key of an agent in the credential vault (acp/<agent id>/<VARIABLE>) and notes the variable on
        the entry; it goes only to that agent's process, as that variable."""
        require_person(actor, "Storing an agent's key")
        if not ENV_NAME.fullmatch(str(variable)) or FORBIDDEN_ENV.fullmatch(str(variable)):
            raise AgentError(f"{_text(variable, 64)!r} is no variable name an agent's key may use.", "invalid")
        if not isinstance(value, str) or not value:
            raise AgentError("The key is empty.", "invalid")
        from orchestrator.Core.secret_entries import SecretNotStored, SecretsUnavailable, store_secret
        with self._lock():
            entries = self.list()
            entry = next((e for e in entries if e.id == self.get(name).id), None)
            try:
                store_secret(self.secrets, entry.secret_name(variable), value)
            except (SecretsUnavailable, SecretNotStored) as e:
                raise AgentError(f"The key could not be stored ({e}).", "failed") from None
            if variable not in entry.keys:
                entry.keys.append(variable)
                self._save(entries)
        self.record_event(actor, entry, f"key {variable} stored")

    # --- starting ---------------------------------------------------------------------------------------------------

    def clean_environment(self, entry: AgentEntry) -> Dict[str, str]:
        """The agent's whole environment: the basics of this computer (PATH, HOME, the locale), the entry's own
        settings and its keys from the vault. Nothing else of the framework's environment: no token, no other
        agent's key, no proxy credentials, no framework variable."""
        source = self._environ if self._environ is not None else os.environ
        names = WINDOWS_BASICS if os.name == "nt" else POSIX_BASICS
        env = {k: source[k] for k in names if k in source and not str(source[k]).startswith("()")}
        env.update(entry.env)
        for variable in entry.keys:
            value = None
            if self.secrets is not None:
                try:
                    value = self.secrets.get_secret(entry.secret_name(variable))
                except Exception:  # noqa: BLE001 - a vault that cannot be read: the key is missing
                    value = None
            if value:
                env[variable] = value
        return env

    def mcp_entry(self, entry: AgentEntry) -> Dict[str, Any]:
        """The stdio entry of the framework's MCP server the agent gets in session/new: the current Python with
        `-m orchestrator.cli -q mcp serve` (utils/stdio_programs.launch_command, the launch every MCP client entry
        of the framework uses), its PYTHONPATH, the framework home when it is not the default one, and the channel
        label."""
        from orchestrator.tools.mcp_env import CHANNEL_ENV, channel_label
        from orchestrator.utils.stdio_programs import MCP_SERVE, installation_root, launch_command
        launch = launch_command(installation_root(), command=MCP_SERVE)
        env = dict(launch.get("env") or {})
        source = self._environ if self._environ is not None else os.environ
        for name in ("LLM_FRAMEWORK_HOME", "LLM_FRAMEWORK_CONFIG_DIR"):
            if source.get(name):
                env[name] = source[name]
        env[CHANNEL_ENV] = channel_label(entry.name)
        return {"name": "llm-framework", "command": launch["command"], "args": list(launch["args"]), "env": env}

    def launch_spec(self, name: str, actor: str) -> LaunchSpec:
        """How the agent starts, for the person's start (GUI or terminal): refused for an AI, in offline mode for an
        agent that needs the internet, and when its program is missing."""
        require_person(actor, "Starting an agent")
        entry = self.get(name)
        refusal = self.offline_refusal(entry)
        if refusal:
            raise AgentError(refusal, "offline")
        if entry.kind in ("npx", "uvx"):
            runner = self._runner("npx" if entry.kind == "npx" else "uvx")
            if runner is None:
                raise AgentError("Node.js (npx) is not installed on this computer." if entry.kind == "npx"
                                 else "uv (uvx) is not installed on this computer.", "no_runner")
            pin = NPX_PIN if entry.kind == "npx" else UVX_PIN
            if not pin.fullmatch(entry.package):   # an edited list: still only an exact version
                raise AgentError(f"{entry.name} is not pinned to one exact version.", "invalid")
            argv = [runner] + (["--yes"] if entry.kind == "npx" else []) + [entry.package] + list(entry.args)
        else:
            program = Path(entry.command)
            if not program.is_absolute() or not program.is_file():
                raise AgentError(f"The program of {entry.name} is missing. Remove the agent and add it again.",
                                 "no_runner")
            argv = [str(program)] + list(entry.args)
        mcp = self.mcp_entry(entry)
        return LaunchSpec(entry=entry, argv=tuple(argv), env=self.clean_environment(entry), cwd=self.workspace(entry),
                          mcp_server=mcp, channel=mcp["env"].get("LCF_MCP_CHANNEL", ""),
                          first_start=self.first_start(entry))

    @staticmethod
    def first_start(entry: AgentEntry) -> bool:
        """Whether the next start is the first one of a freshly added npx or uvx agent: its runner may download the
        agent's package first, so the start may take FIRST_START_TIMEOUT (Core/acp_client.py) instead of the usual
        START_TIMEOUT, and the person is told so."""
        return entry.kind in ("npx", "uvx") and not entry.started

    def mark_started(self, entry: AgentEntry) -> None:
        """Notes that the agent answered a start (its next starts are quick again). Changes nothing else."""
        with self._lock():
            entries = self.list()
            for item in entries:
                if item.id == entry.id:
                    item.started = True
            self._save(entries)

    # --- removing ---------------------------------------------------------------------------------------------------

    def deletion_plan(self, names: Optional[Sequence[str]] = None):
        """The agents to remove as the person's list (Core/deletion): each agent's own folders (installed program,
        working folder) with full path, size and what it is; an agent without folders is listed by its entry in the
        list file. None when there is nothing to list."""
        from orchestrator.Core.deletion import plan_of
        from orchestrator.Core.trash import tree_size
        from orchestrator.tools.spec import DeletionItem
        wanted = None if names is None else {self.get(n).id for n in names}
        items = []
        for entry in self.list():
            if wanted is not None and entry.id not in wanted:
                continue
            for folder, what in ((self.root / BINARIES / entry.id, "installed program of the ACP agent"),
                                 (self.root / WORKSPACES / entry.id, "working folder of the ACP agent")):
                if folder.is_dir() and not folder.is_symlink():
                    items.append(DeletionItem(kind="acp_agent", speaking_name=f"{entry.name} ({what.split()[0]})",
                                              full_path=str(folder), file_name=folder.name,
                                              size_bytes=tree_size(folder), what=f"{what} {entry.name}"))
            items.append(DeletionItem(kind="acp_agent", speaking_name=entry.name, full_path=str(self.agents_file),
                                      file_name=entry.id, size_bytes=0,
                                      what=f"the entry of the ACP agent {entry.name} in the agents' list"
                                           + (f" and its {len(entry.keys)} keys in the credential vault"
                                              if entry.keys else "")))
        return plan_of(items, ("trash", "permanent"))

    def delete(self, accepted: Any, trash: Any = None):
        """Removes exactly the agents of the accepted plan (the person's yes, Core/deletion): their folders into the
        trash or for good, their entries and their keys. Looked up again first (PlanChanged), audited before anything
        goes."""
        from orchestrator.Core.deletion import DeletionResult, note_kept, require_person as person_yes
        from orchestrator.Core.deletion import require_same, same_items
        from orchestrator.utils.atomic import remove_tree
        accepted = person_yes(accepted)
        if accepted.actor not in PERSON_ACTORS:
            raise PermissionError("Only the person removes an agent, in the GUI or at a terminal.")
        names = sorted({i.speaking_name for i in accepted.plan.items if i.full_path == str(self.agents_file)})
        require_same(accepted, same_items(self.deletion_plan(), accepted.plan), "the list of agents")
        if accepted.mode == "trash" and trash is None:
            raise RuntimeError("The trash is not available; nothing was deleted.")
        self._record(accepted.actor, "started", {"agents": names, "bytes": accepted.plan.total_bytes,
                                                 "mode": accepted.mode},
                     f"{len(names)} ACP agents removed", **{k: v for k, v in accepted.audit_extra().items()
                                                            if k == "deletion_sha256"})
        result = DeletionResult()
        for item in accepted.plan.items:
            if item.full_path == str(self.agents_file):
                continue
            path = Path(item.full_path)
            try:
                if accepted.mode == "trash":
                    entry = trash.move("acp_agent", path, self.state_dir, "state", item.speaking_name, accepted,
                                       accepted.actor)
                    result.trash_ids.append(entry.id)
                else:
                    if note_kept(result, item.speaking_name, path, remove_tree(path)):
                        continue
                    result.freed_bytes += item.size_bytes
            except Exception as e:  # noqa: BLE001 - that folder stays and is reported
                result.failed.append((item.speaking_name, type(e).__name__ if isinstance(e, OSError) else str(e)))
                continue
            result.done.append(item.speaking_name)
        with self._lock():
            entries = self.list()
            gone = [e for e in entries if e.name in names]
            for entry in gone:
                for variable in entry.keys:
                    try:
                        if self.secrets is not None:
                            self.secrets.delete_secret(entry.secret_name(variable))
                    except Exception:  # noqa: BLE001 - a key that stays is said, not hidden
                        result.failed.append((entry.name, f"its key {variable} stayed in the credential vault"))
                result.done.append(entry.name)
            self._save([e for e in entries if e not in gone])
        return result


class AgentDownloader:
    """The framework's one pinned downloader (utils/download.PinnedDownloader) for an agent's binary: HTTPS only, the
    registry's sha256 as the pin, the disk rule of the framework home (download_rule with the reservation ledger).
    The registry gives no size, so the size is asked from the server first (HEAD over HTTPS); the hash still decides."""

    def __init__(self, state_dir: Path, session: Any = None):
        from orchestrator.Core.disk_plan import ReservationLedger, download_rule
        from orchestrator.utils.download import PinnedDownloader
        self._downloader = PinnedDownloader(session, logger=logger,
                                            space=download_rule(ledger=ReservationLedger(Path(state_dir))))

    def remote_size(self, url: str) -> int:
        from orchestrator.utils.download import DownloadError
        if not url.startswith("https://"):
            raise DownloadError("only HTTPS downloads are fetched")
        try:
            r = self._downloader.session.head(url, allow_redirects=True, timeout=(15, 60),
                                              headers={"Accept-Encoding": "identity"})
        except Exception as e:  # noqa: BLE001 - requests errors
            raise DownloadError(f"the server did not answer ({type(e).__name__})") from None
        if not str(r.url).startswith("https://"):
            raise DownloadError("the download was redirected away from HTTPS")
        length = str(r.headers.get("Content-Length") or "")
        if r.status_code != 200 or not length.isdigit() or int(length) <= 0:
            raise DownloadError(f"the server gives no size for it (HTTP {r.status_code})")
        return int(length)

    def fetch(self, url: str, dest: Path, size: int, sha256: str, progress=None) -> Path:
        return self._downloader.fetch(url, dest, size, sha256, progress=progress)


def python_executable() -> str:
    """The interpreter the framework runs on (the MCP entry starts the same one)."""
    return sys.executable


__all__ = ["AddPreview", "AgentDownloader", "AgentEntry", "AgentError", "AgentStore", "KINDS", "LaunchSpec",
           "PERSON_ACTORS", "PROTOCOL_VERSION", "REGISTRY_URL", "RegistryAgent", "clean_env", "parse_registry",
           "platform_key", "require_person"]
