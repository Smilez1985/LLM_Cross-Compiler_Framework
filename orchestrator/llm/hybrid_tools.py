#!/usr/bin/env python3
"""
Ditto's hybrid: this computer always, another computer optionally (hybrid design 6.3, 6.5, 6.6, 6.8; DECISIONS round
5 d: Ditto keeps the local registry always, a connection to a framework elsewhere is an addition, each task goes where
its object lives).

- Places. `LOCAL` is this computer (place key `this_computer`). The other computer's key comes from the person's name
  for it (`place_key("build box") == "build_box"`); a name that is not a plain short name (a login, an address, a
  path) is never shown to a model: it becomes "the other computer" (`other_computer`). One remote at a time.
- One tool list (`HybridToolSet.build`). Without a remote it is exactly this computer's list. With one, a tool both
  computers offer with the same closed input schema is offered once, with an optional `place` field; a tool only this
  computer offers stays as it is; a catalogue tool only the other computer offers needs `place`; a tool whose schema
  differs (another framework version) is offered separately as `remote_<name>`. Only names of this framework's
  catalogue (framework_tools.KNOWN_TOOLS) are ever offered from the remote, always with this computer's own titles
  and descriptions: a remote description, title or example never reaches the model (SEC-H7). Internal tools,
  report_cannot_do and the message box tools are never offered from the remote.
- Where objects live (`PlaceIndex`, `ditto_places.json`, signed, survives "Clear chat"): ids the framework issued
  (speaking_names.ID_KINDS), learned only from structured results (this computer's output models, the remote's
  structured content), never from model text or arguments. A remote result that names an id this computer knows can
  only make it ambiguous, never move it.
- Routing (`HybridRouter.execute`): `place` (or its declared aliases location, machine, computer) is taken out of
  the arguments; ids decide (an explicit place that contradicts an id gives wrong_place, ids at two places
  needs_transfer, an id at both missing_argument); otherwise this computer. A local call goes through the registry
  exactly as before; a remote call first gets this computer's write-ahead audit entry (remote_audit), then goes to the
  other computer, whose registry decides, asks and audits. Nothing widens in either direction: a local allow never
  runs a remote tool, a remote result never approves, prefills or skips a local question. A turn that received any
  remote result is tainted (ToolContext.metadata["taint"]).
"""

import copy
import dataclasses
import hashlib
import json
import os
import re
import tempfile
import threading
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from orchestrator.Core.speaking_names import ID_KINDS, id_kind
from orchestrator.llm.task_prompts import reason_words
from orchestrator.llm.types import ToolSpecLLM, tool_result_text
from orchestrator.utils.helpers import redact_secrets
from orchestrator.utils.logging import get_logger
from orchestrator.utils.untrusted import wrap_untrusted

logger = get_logger("HybridTools")

LOCAL_KEY = "this_computer"
FALLBACK_KEY = "other_computer"
FALLBACK_LABEL = "the other computer"
PLACE_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
RESERVED_KEYS = frozenset({"this_computer", "local", "here", "remote"})
# The model APIs' tool name pattern: a remote name that fails it is left out with a notice (HF9).
PROVIDER_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# An id the framework issued (job_..., req_..., a_..., lf_...): the only values that route a call.
RANDOM_ID = re.compile("^(?:" + "|".join(ID_KINDS.values()) + ")$")
# Never offered from the other computer: this computer answers honest "cannot"s itself; transfers and the message box
# are this computer's own business.
NEVER_FROM_REMOTE = frozenset({"report_cannot_do", "transfer_object", "check_messages", "reply_to_person"})
# Offered from the other computer only when it reports a live delegation created for this token itself (never one
# inherited through --link); this computer's person and delegations never decide the remote's questions (6.3 rule 6).
REMOTE_ANSWER_TOOL = "answer_approval"
PLACE_FIELD = "place"
PLACE_ALIASES = ("location", "machine", "computer")   # not "where": propose_healing_fix has a field of that name
_LOCAL_WORDS = frozenset({"local", "here", "this", "this computer", "this machine"})
_REMOTE_WORDS = frozenset({"remote", "other", "other computer", "the other computer", "other machine"})
MAX_PLACE_ENTRIES = 2000
PLACES_FILE = "ditto_places.json"
LISTING_FILE = "remote_listing.json"
REMOTE_PREFIX = "remote_"
_LABEL_RE = re.compile(r"^[\w .()+-]{1,40}$")
_ID_DEPTH = 6
_ID_COUNT = 500


# --- places ----------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Place:
    """A computer Ditto works with. `label` is what a model and the person read; `server` how the person reaches it
    (its name and the transport, for this computer's audit; never the token, never a login)."""
    key: str
    label: str
    kind: Literal["local", "remote"]
    server: str = ""


LOCAL = Place(LOCAL_KEY, "this computer", "local")


def place_key(name: Any) -> str:
    """The place key of the other computer from the person's name for it: lower case, `_` for everything else, at
    most 24 characters, starting with a letter. A reserved key (this_computer, local, here, remote) or a name with
    nothing usable gives `other_computer`."""
    text = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    text = re.sub(r"^[^a-z]+", "", text)[:24].rstrip("_")
    if not text or text in RESERVED_KEYS or not PLACE_KEY_RE.fullmatch(text):
        return FALLBACK_KEY
    return text


def place_label(name: Any) -> str:
    """The other computer's label as a model and the person read it: the person's own name when it is a plain short
    name (letters, digits, spaces and . ( ) + - _), otherwise "the other computer", so a login, an address or a path
    typed as the name never reaches a model."""
    text = " ".join(str(name or "").split())
    if not text or not _LABEL_RE.fullmatch(text) or text.lower() in _LOCAL_WORDS:
        return FALLBACK_LABEL
    return text


_SSH_VALUE_OPTIONS = frozenset("bcDEeFIiJLlmOoPpQRSWw")


def _ssh_host(command: Sequence[str]) -> str:
    """The host of an ssh command's destination, without the login ("" when it cannot be told)."""
    args, i = list(command[1:]), 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            i += 1
            break
        if arg.startswith("-") and len(arg) > 1:
            i += 2 if len(arg) == 2 and arg[1] in _SSH_VALUE_OPTIONS else 1
            continue
        break
    if i >= len(args):
        return ""
    dest = args[i]
    if dest.startswith("ssh://"):
        dest = urlsplit(dest).hostname or ""
    host = dest.rsplit("@", 1)[-1].split(":", 1)[0]
    return host if re.fullmatch(r"[A-Za-z0-9.-]{1,80}", host) else ""


def server_identity(server: Any, label: str) -> str:
    """How the person reaches the other computer, for this computer's audit log: its label and the transport (with
    the host of an address or of an ssh destination), never the token and never a login."""
    if getattr(server, "transport", "") == "http":
        parts = urlsplit(getattr(server, "url", "") or "")
        port = f":{parts.port}" if parts.port else ""
        return f"{label} ({parts.scheme or 'http'}://{parts.hostname or 'server'}{port})"[:120]
    command = tuple(getattr(server, "command", ()) or ())
    program = os.path.basename(command[0]).lower() if command else "command"
    program = re.sub(r"[^a-z0-9._-]", "", program)[:24] or "command"
    host = _ssh_host(command) if program in ("ssh", "ssh.exe") else ""
    return f"{label} ({program}{' to ' + host if host else ''})"[:120]


def remote_place(server: Any) -> Place:
    """The Place of the configured other computer."""
    name = getattr(server, "name", "") or ""
    label = place_label(name)
    key = place_key(name) if label != FALLBACK_LABEL else FALLBACK_KEY
    return Place(key, label, "remote", server_identity(server, label))


# --- schemas -------------------------------------------------------------------------------------------------------

_TEXT_KEYS = frozenset({"description", "title", "examples", "$comment"})
_NAMED = frozenset({"properties", "$defs", "definitions", "patternProperties"})


def strip_schema(schema: Any) -> Any:
    """A JSON schema without descriptions, titles, examples and comments (property names that happen to be
    "title" or "description" stay)."""
    if isinstance(schema, list):
        return [strip_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out: Dict[str, Any] = {}
    for key, value in schema.items():
        if key in _TEXT_KEYS:
            continue
        if key in _NAMED and isinstance(value, dict):
            out[key] = {name: strip_schema(sub) for name, sub in value.items()}
        else:
            out[key] = strip_schema(value)
    return out


def schema_key(schema: Any) -> str:
    """The sha256 of a closed input schema without its texts: equal on both computers when they run the same version
    of a tool."""
    stripped = strip_schema(schema if isinstance(schema, dict) else {})
    if stripped.get("type") == "object":
        stripped.setdefault("additionalProperties", False)
    text = json.dumps(stripped, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_SAFE_KEYWORDS = frozenset({"type", "properties", "required", "additionalProperties", "items", "prefixItems", "enum",
                            "const", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minLength",
                            "maxLength", "minItems", "maxItems", "uniqueItems", "anyOf", "oneOf", "allOf", "$ref",
                            "$defs", "definitions", "multipleOf", "minProperties", "maxProperties"})
_TYPES = frozenset({"object", "string", "integer", "number", "boolean", "array", "null"})
_PROPERTY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:@/+-]{1,64}$")
_REF_RE = re.compile(r"^#/(?:\$defs|definitions)/[A-Za-z0-9_.-]{1,64}$")


class _Unsafe(ValueError):
    pass


def _safe(schema: Any, depth: int = 0) -> Any:
    if depth > 12:
        raise _Unsafe("too deep")
    if isinstance(schema, bool):
        return schema
    if not isinstance(schema, dict):
        raise _Unsafe("not a schema")
    out: Dict[str, Any] = {}
    for key, value in schema.items():
        if key not in _SAFE_KEYWORDS:
            continue  # texts, defaults, patterns, formats: not needed to call the tool, the remote validates
        if key == "type":
            types = value if isinstance(value, list) else [value]
            if not types or not all(isinstance(t, str) and t in _TYPES for t in types):
                raise _Unsafe("type")
            out[key] = value
        elif key in ("properties", "$defs", "definitions"):
            if not isinstance(value, dict) or not all(isinstance(n, str) and _PROPERTY_RE.fullmatch(n) for n in value):
                raise _Unsafe("names")
            out[key] = {name: _safe(sub, depth + 1) for name, sub in value.items()}
        elif key == "required":
            if not isinstance(value, list) or not all(isinstance(n, str) and _PROPERTY_RE.fullmatch(n) for n in value):
                raise _Unsafe("required")
            out[key] = list(value)
        elif key == "$ref":
            if not isinstance(value, str) or not _REF_RE.fullmatch(value):
                raise _Unsafe("ref")
            out[key] = value
        elif key in ("enum", "const"):
            values = value if key == "enum" else [value]
            if isinstance(values, list) and all(v is None or isinstance(v, (bool, int, float))
                                                or (isinstance(v, str) and _TOKEN_RE.fullmatch(v)) for v in values):
                out[key] = value  # a closed list of plain tokens; one with other text is dropped, not shown
        elif key in ("items", "additionalProperties"):
            out[key] = _safe(value, depth + 1)
        elif key in ("prefixItems", "anyOf", "oneOf", "allOf"):
            if not isinstance(value, list):
                raise _Unsafe(key)
            out[key] = [_safe(v, depth + 1) for v in value]
        else:  # numbers and flags
            if isinstance(value, bool) or isinstance(value, (int, float)):
                out[key] = value
    return out


def safe_remote_schema(schema: Any) -> Optional[Dict[str, Any]]:
    """A remote input schema a model may see: only the keywords needed to call the tool, property names that are
    plain identifiers, closed lists of plain tokens; no text of the remote server. None when it cannot be made safe
    (then the tool is not offered)."""
    try:
        safe = _safe(strip_schema(schema))
    except _Unsafe:
        return None
    if not isinstance(safe, dict) or safe.get("type") != "object":
        return None
    safe.setdefault("properties", {})
    safe["additionalProperties"] = False
    return safe


def _with_place(schema: Dict[str, Any], values: Sequence[str], required: bool, description: str) -> Dict[str, Any]:
    out = copy.deepcopy(schema)
    properties = out.setdefault("properties", {})
    properties[PLACE_FIELD] = {"type": "string", "enum": list(values), "description": description}
    if required:
        out["required"] = [r for r in out.get("required", []) if r != PLACE_FIELD] + [PLACE_FIELD]
    out["additionalProperties"] = False
    return out


# --- the remote listing and its cache --------------------------------------------------------------------------------

@dataclass(frozen=True)
class ListedTool:
    """A tool the other computer lists for Ditto's token: its name, its input schema without texts (strip_schema),
    its read-only hint (this computer's catalogue decides first) and the internal mark. No description is kept."""
    name: str
    schema: Dict[str, Any] = field(default_factory=dict)
    read_only: bool = False
    internal: bool = False


@dataclass(frozen=True)
class RemoteListing:
    """The other computer's tools. `bad_names`: names that fail the model APIs' pattern (left out, HF9).
    `own_delegation`: that framework reports a live delegation created for this token itself (filled once delegations
    exist there; until then False). `cached`: read from remote_listing.json while the computer is not reachable."""
    tools: Tuple[ListedTool, ...] = ()
    bad_names: int = 0
    own_delegation: bool = False
    cached: bool = False


def _write_private(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class ListingCache:
    """remote_listing.json: the other computer's tool names, stripped schemas, schema keys and read-only hints (no
    descriptions), so tools from earlier turns keep their definitions while it is not reachable (6.6). Keyed by the
    server's fingerprint (never the token); without a path it lives in memory only."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else None
        self._memory: Dict[str, RemoteListing] = {}

    def load(self, server: str) -> Optional[RemoteListing]:
        if server in self._memory:
            return dataclasses.replace(self._memory[server], cached=True)
        if self.path is None:
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict) or raw.get("version") != 1 or raw.get("server") != server:
            return None
        tools = []
        for item in raw.get("tools") or []:
            if not isinstance(item, dict):
                continue
            name, schema = item.get("name"), item.get("schema")
            if not isinstance(name, str) or not PROVIDER_NAME_RE.fullmatch(name) or not isinstance(schema, dict):
                continue
            schema = strip_schema(schema)
            if item.get("schema_key") != schema_key(schema):
                continue  # edited or damaged: not used
            tools.append(ListedTool(name, schema, read_only=item.get("read_only") is True))
        return RemoteListing(tuple(tools), cached=True)

    def save(self, server: str, listing: RemoteListing) -> None:
        if self._memory.get(server) == listing:
            return   # unchanged since the last turn: nothing to write
        self._memory = {server: listing}
        if self.path is None:
            return
        data = {"version": 1, "server": server, "tools": [
            {"name": t.name, "schema": t.schema, "schema_key": schema_key(t.schema), "read_only": t.read_only}
            for t in listing.tools if not t.internal]}
        try:
            _write_private(self.path, data)
        except OSError as e:
            logger.warning(f"The other computer's tool list could not be cached: {e}")


# --- where objects live ------------------------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def collect_ids(value: Any, depth: int = 0, found: Optional[List[str]] = None) -> List[str]:
    """The framework ids under `id`, `*_id` and `*_ids` keys of a structured value, in order (never from free text)."""
    found = [] if found is None else found
    if depth > _ID_DEPTH or len(found) >= _ID_COUNT:
        return found
    if isinstance(value, dict):
        for key, item in value.items():
            key = str(key)
            if (key == "id" or key.endswith("_id")) and isinstance(item, str) and RANDOM_ID.fullmatch(item):
                if item not in found:
                    found.append(item)
            elif key.endswith("_ids") and isinstance(item, list):
                for one in item:
                    if isinstance(one, str) and RANDOM_ID.fullmatch(one) and one not in found:
                        found.append(one)
            elif isinstance(item, (dict, list)):
                collect_ids(item, depth + 1, found)
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                collect_ids(item, depth + 1, found)
    return found


class PlaceIndex:
    """Where each object Ditto saw lives (ditto_places.json in the per-user folder, mode 0600, signed like the tool
    permissions, at most 2,000 entries). It survives "Clear chat". A file that does not verify is not used (a forged
    entry could send an id to the other computer): Ditto starts with an empty index and `problem` says why."""

    def __init__(self, path: Optional[Path] = None, key: Optional[bytes] = None,
                 refuse_unkeyed: Optional[Callable[[], bool]] = None):
        self.path = Path(path) if path else None
        self.key = key
        self.refuse_unkeyed = refuse_unkeyed
        self.problem: Optional[str] = None
        self._lock = threading.RLock()
        self._entries: Dict[str, Dict[str, str]] = {}
        self._copies: Dict[str, Dict[str, Dict[str, str]]] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self.path is None:
            return
        from orchestrator.tools.signing import SignatureError, verify
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as e:
            self.problem = f"the file cannot be read ({e.__class__.__name__})"
            logger.warning(f"Ditto's place index is not used: {self.problem}")
            return
        try:
            body = verify("ditto_places", raw, self.key, self.refuse_unkeyed)
        except SignatureError as e:
            self.problem = str(e)
            logger.warning(f"Ditto's place index is not used: {self.problem}")
            return
        entries = body.get("entries") if isinstance(body.get("entries"), dict) else {}
        for object_id, entry in entries.items():
            if (isinstance(object_id, str) and RANDOM_ID.fullmatch(object_id) and isinstance(entry, dict)
                    and isinstance(entry.get("place"), str)
                    and (entry["place"] == "both" or PLACE_KEY_RE.fullmatch(entry["place"]))):
                self._entries[object_id] = {"place": entry["place"], "seen": str(entry.get("seen") or "")}
        copies = body.get("copies") if isinstance(body.get("copies"), dict) else {}
        for old, places in copies.items():
            if not (isinstance(old, str) and RANDOM_ID.fullmatch(old) and isinstance(places, dict)):
                continue
            for key, copy_ in places.items():
                if (isinstance(key, str) and PLACE_KEY_RE.fullmatch(key) and isinstance(copy_, dict)
                        and isinstance(copy_.get("id"), str) and RANDOM_ID.fullmatch(copy_["id"])):
                    self._copies.setdefault(old, {})[key] = {"id": copy_["id"], "at": str(copy_.get("at") or "")}

    def _save(self) -> None:
        if self.path is None:
            return
        from orchestrator.tools.signing import sign
        if len(self._entries) > MAX_PLACE_ENTRIES:
            newest = sorted(self._entries.items(), key=lambda kv: kv[1].get("seen", ""))[-MAX_PLACE_ENTRIES:]
            self._entries = dict(newest)
        data = sign("ditto_places", {"version": 1, "entries": self._entries, "copies": self._copies}, self.key)
        try:
            _write_private(self.path, data)
        except OSError as e:
            logger.warning(f"Ditto's place index could not be saved: {e}")

    def where(self, object_id: str) -> Optional[str]:
        """The place key where the object lives, "both", or None when unknown."""
        with self._lock:
            self._load()
            entry = self._entries.get(object_id)
            return entry["place"] if entry else None

    def learn(self, place: str, result: Any) -> int:
        """Records the ids of a structured result (a local output model's dump, a remote structured content) as living
        at `place`. An id known at another place becomes "both"; nothing ever moves. Returns how many changed."""
        ids = collect_ids(result)
        if not ids or not PLACE_KEY_RE.fullmatch(place or ""):
            return 0
        changed = 0
        with self._lock:
            self._load()
            now = _now()
            for object_id in ids:
                entry = self._entries.get(object_id)
                if entry is None:
                    self._entries[object_id] = {"place": place, "seen": now}
                    changed += 1
                elif entry["place"] not in (place, "both"):
                    self._entries[object_id] = {"place": "both", "seen": now}
                    changed += 1
                else:
                    entry["seen"] = now
            if changed:
                self._save()
        return changed

    def record_copy(self, old_id: str, place: str, new_id: str) -> None:
        """A transfer's result: `old_id` has the copy `new_id` at `place` (written by transfers)."""
        if not (RANDOM_ID.fullmatch(old_id or "") and RANDOM_ID.fullmatch(new_id or "")
                and PLACE_KEY_RE.fullmatch(place or "")):
            raise ValueError("a copy needs two framework ids and a place key")
        with self._lock:
            self._load()
            self._copies.setdefault(old_id, {})[place] = {"id": new_id, "at": _now()}
            entry = self._entries.get(new_id)
            if entry is None:
                self._entries[new_id] = {"place": place, "seen": _now()}
            self._save()

    def copy_at(self, object_id: str, place: str) -> Optional[Tuple[str, str]]:
        """(new id, when) of the object's copy at `place`, or None."""
        with self._lock:
            self._load()
            found = self._copies.get(object_id, {}).get(place)
            return (found["id"], found["at"]) if found else None

    def speaking_name(self, object_id: str, labels: Dict[str, str]) -> str:
        """A name for the chat panel: what kind of object and where it lives ("the build req_... (build box)")."""
        kind = id_kind(object_id) or "object"
        words = {"job": "the job", "request": "the build", "artifact": "the built model", "file": "the file",
                 "approval": "the question", "proposal": "the healing proposal", "trash": "the trash entry",
                 "message": "the message"}.get(kind, "the object")
        place = self.where(object_id)
        where = labels.get(place, "both computers" if place == "both" else "") if place else ""
        return f"{words} {object_id}" + (f" ({where})" if where else "")

    def __len__(self) -> int:
        with self._lock:
            self._load()
            return len(self._entries)


# --- one tool list ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolRoute:
    """Where an offered tool can run. `local` and `remote` name the tool on each computer (None: not offered there);
    `fields` are the offered schema's property names; `read_only` comes from this computer's catalogue first."""
    offered: str
    local: Optional[str] = None
    remote: Optional[str] = None
    place_field: bool = False
    place_required: bool = False
    read_only: bool = False
    title: str = ""
    fields: frozenset = frozenset()


@dataclass
class HybridToolSet:
    """The tools Ditto's model is offered in one turn, each with its route (6.3)."""
    specs: List[ToolSpecLLM]
    routes: Dict[str, ToolRoute]
    remote: Optional[Place] = None
    reachable: bool = False
    notices: List[str] = field(default_factory=list)
    counts: Dict[str, int] = field(default_factory=dict)   # once, only_there, separately

    @property
    def names(self) -> List[str]:
        return [s.name for s in self.specs]

    @property
    def places(self) -> Tuple[Place, ...]:
        return (LOCAL,) + ((self.remote,) if self.remote is not None else ())

    @classmethod
    def build(cls, registry: Any, caller: str = "ditto", listing: Optional[RemoteListing] = None,
              remote: Optional[Place] = None, reachable: bool = True, ctx: Any = None,
              local_specs: Optional[List[ToolSpecLLM]] = None) -> "HybridToolSet":
        """The tool list of this computer's registry (the tools `caller` is offered) plus the other computer's
        `listing`. Without a listing it is exactly this computer's list, with no place field. `local_specs` overrides
        the local definitions (e.g. every registered tool for a conversation that already holds tool calls)."""
        local: Dict[str, ToolSpecLLM] = {}
        registered: Dict[str, Any] = {}
        if registry is not None:
            try:
                registered = {s.name: s for s in registry.list()}
            except Exception:  # noqa: BLE001 - a registry that cannot list offers nothing here
                registered = {}
            if local_specs is None:
                try:
                    visible = registry.visible(caller, ctx) if ctx is not None else registry.visible(caller)
                except Exception as e:  # noqa: BLE001 - the turn goes on without this computer's tools
                    logger.warning(f"Tools unavailable for this question: {e}")
                    visible = []
                local = {s.name: ToolSpecLLM.from_tool_spec(s) for s in visible}
        if local_specs is not None:
            local = {s.name: s for s in local_specs}

        def title(name: str) -> str:
            spec = registered.get(name)
            return getattr(spec, "title", "") or name

        def read_only(name: str, hint: bool = False) -> bool:
            spec = registered.get(name)
            if spec is not None:
                return getattr(getattr(spec, "tier", None), "value", "") == "read"
            return hint

        specs: List[ToolSpecLLM] = []
        routes: Dict[str, ToolRoute] = {}

        def offer(spec: ToolSpecLLM, route: ToolRoute) -> None:
            specs.append(spec)
            routes[spec.name] = dataclasses.replace(
                route, fields=frozenset((spec.input_schema.get("properties") or {}).keys()))

        if listing is None or remote is None:
            for name in sorted(local):
                offer(local[name], ToolRoute(name, local=name, read_only=read_only(name), title=title(name)))
            return cls._guarded(cls(specs, routes))

        from orchestrator.tools.framework_tools import KNOWN_TOOLS
        label, key = remote.label, remote.key
        notices: List[str] = []
        if listing.bad_names:
            notices.append(f"{label} lists {listing.bad_names} tool(s) whose name the AI providers do not accept; "
                           "they are left out.")
        remote_tools: Dict[str, ListedTool] = {}
        unknown: List[str] = []
        for tool in listing.tools:
            if tool.internal or tool.name in NEVER_FROM_REMOTE:
                continue
            if tool.name == REMOTE_ANSWER_TOOL and not listing.own_delegation:
                continue
            if tool.name not in KNOWN_TOOLS:
                unknown.append(tool.name)
                continue
            remote_tools[tool.name] = tool
        if unknown:
            shown = ", ".join(sorted(unknown)[:10]) + (" and more" if len(unknown) > 10 else "")
            notices.append(f"{label} offers tools this framework does not know; they are left out: {shown}.")
        away = "" if reachable else "Not reachable now: "
        both_text = (f"Where to run it: {LOCAL_KEY} (this computer, the default) or {key} ({label}, another "
                     "computer). Leave it out when an id in the call decides." + ("" if reachable else
                                                                                f" {label} is not reachable now."))
        only_text = f"Must be {key}: this tool runs on {label}."
        counts = {"once": 0, "only_there": 0, "separately": 0}
        skewed: List[str] = []
        unsafe: List[str] = []
        for name in sorted(local):
            spec = local[name]
            tool = remote_tools.get(name)
            properties = spec.input_schema.get("properties") or {}
            if tool is not None and PLACE_FIELD not in properties and \
                    schema_key(spec.input_schema) == schema_key(tool.schema):
                offer(spec.model_copy(update={"input_schema": _with_place(spec.input_schema, [LOCAL_KEY, key], False,
                                                                          both_text)}),
                      ToolRoute(name, local=name, remote=name, place_field=True, read_only=read_only(name),
                                title=title(name)))
                counts["once"] += 1
                continue
            offer(spec, ToolRoute(name, local=name, read_only=read_only(name), title=title(name)))
            if tool is None:
                continue
            safe = safe_remote_schema(tool.schema)
            separate = REMOTE_PREFIX + name
            if safe is None or PLACE_FIELD in (safe.get("properties") or {}) or \
                    not PROVIDER_NAME_RE.fullmatch(separate) or separate in local or separate in remote_tools:
                unsafe.append(name)
                continue
            for prop, sub in (safe.get("properties") or {}).items():
                described = properties.get(prop)
                if isinstance(described, dict) and isinstance(sub, dict) and described.get("description"):
                    sub["description"] = described["description"]
            description = (f"{away}Only on {label}, which runs another framework version: {spec.description}")
            offer(ToolSpecLLM(name=separate, description=description[:2048],
                              input_schema=_with_place(safe, [key], True, only_text)),
                  ToolRoute(separate, remote=name, place_field=True, place_required=True,
                            read_only=read_only(name, tool.read_only), title=title(name)))
            skewed.append(name)
            counts["separately"] += 1
        for name in sorted(set(remote_tools) - set(local)):
            tool = remote_tools[name]
            safe = safe_remote_schema(tool.schema)
            if safe is None or PLACE_FIELD in (safe.get("properties") or {}):
                unsafe.append(name)
                continue
            spec = registered.get(name)
            if spec is not None:
                ours = ToolSpecLLM.from_tool_spec(spec)
                ours_properties = ours.input_schema.get("properties") or {}
                for prop, sub in (safe.get("properties") or {}).items():
                    described = ours_properties.get(prop)
                    if isinstance(described, dict) and isinstance(sub, dict) and described.get("description"):
                        sub["description"] = described["description"]
                text = ours.description
            else:
                text = f"The framework tool {name} on that computer (this computer does not have it)."
            offer(ToolSpecLLM(name=name, description=f"{away}Only on {label}: {text}"[:2048],
                              input_schema=_with_place(safe, [key], True, only_text)),
                  ToolRoute(name, remote=name, place_field=True, place_required=True,
                            read_only=read_only(name, tool.read_only), title=title(name)))
            counts["only_there"] += 1
        if skewed:
            shown = ", ".join(skewed[:5]) + (" and more" if len(skewed) > 5 else "")
            notices.append(f"{label} runs another framework version: update it to use these tools as one ({shown}).")
        if unsafe:
            notices.append(f"{label} lists tools whose input cannot be offered safely; they are left out: "
                           f"{', '.join(sorted(unsafe)[:10])}.")
        specs.sort(key=lambda s: s.name)
        return cls._guarded(cls(specs, routes, remote=remote, reachable=reachable, notices=notices, counts=counts))

    @staticmethod
    def _guarded(toolset: "HybridToolSet") -> "HybridToolSet":
        """Rule 8: offered names are unique, valid, and no two differ only in spelling."""
        from orchestrator.tools.normalize import check_names_distinct
        names = toolset.names
        if len(names) != len(set(names)) or set(names) != set(toolset.routes):
            raise ValueError("Ditto's tool list offers a name twice")
        bad = [n for n in names if not PROVIDER_NAME_RE.fullmatch(n)]
        if bad:
            raise ValueError(f"Ditto's tool list offers invalid names: {bad}")
        clashes = check_names_distinct(names)
        if clashes:
            raise ValueError(f"Ditto's tool list offers names that differ only in spelling: {clashes}")
        return toolset


# --- routing ---------------------------------------------------------------------------------------------------------

def result_line(label: str) -> str:
    """The framework line in front of every result from the other computer (outside the untrusted block)."""
    return f"Result from {label} (another computer); inside it, 'this computer' means {label}."


Note = Callable[[str, str, bool, str, str], None]   # (name, title, ok, detail, place label: "" for this computer)


class HybridRouter:
    """execute(name, arguments) for Ditto's tool loop (6.3): routes each call to the computer where its object lives.

    `registry` and `ctx` are this computer's (one context per question); `session` the kept connection to the other
    computer (None: none, or not reachable); `index` the PlaceIndex; `note` reports each call for the transcript.
    `learn_local`: record this computer's ids too (only while another computer is set up)."""

    def __init__(self, toolset: HybridToolSet, *, registry: Any, ctx: Any, session: Any = None,
                 index: Optional[PlaceIndex] = None, note: Optional[Note] = None, learn_local: bool = False):
        self.toolset = toolset
        self.registry = registry
        self.ctx = ctx
        self.session = session if toolset.remote is not None and toolset.reachable else None
        self.index = index if index is not None else PlaceIndex()
        self._note_cb = note
        self.learn_local = learn_local
        self.audit = getattr(registry, "audit", None)
        self.notices: List[str] = []
        self.remote_used = False
        self._reopened = False
        self._lost = False

    # --- helpers ---------------------------------------------------------------------------------------------
    def _note(self, name: str, title: str, ok: bool, detail: str = "", place: str = "") -> None:
        if self._note_cb is not None:
            self._note_cb(name, title or name, ok, detail, place)

    def _title(self, name: str) -> str:
        route = self.toolset.routes.get(name)
        if route is not None and route.title:
            return route.title
        try:
            return self.registry.get(name).title
        except Exception:  # noqa: BLE001 - an unknown name keeps its own words
            return str(name)

    def _label(self, key: str) -> str:
        for place in self.toolset.places:
            if place.key == key:
                return place.label
        return "both computers" if key == "both" else key

    def _keys(self) -> Tuple[str, ...]:
        return tuple(p.key for p in self.toolset.places)

    def _refuse(self, route: Optional[ToolRoute], name: str, arguments: Any, target: str, code: str, message: str,
                next_step: str, field_name: str = "", input_error: bool = False):
        from orchestrator.tools.registry import ToolError, ToolInputError
        from orchestrator.tools.remote_audit import record_routing_refusal
        place = self.toolset.remote if target != LOCAL_KEY and self.toolset.remote is not None else LOCAL
        record_routing_refusal(self.audit, self.ctx, (route.remote or route.local or name) if route else str(name),
                               place, arguments if isinstance(arguments, dict) else None, code, message)
        self._note(name, self._title(name), False, message, "" if place is LOCAL else place.label)
        cls = ToolInputError if input_error else ToolError
        error = cls(message, code=code, next=next_step, field=field_name)
        if input_error and code in ("invalid_value", "missing_argument"):
            error.allowed = self._allowed(route) if route is not None else list(self._keys())
        return error

    # --- the call --------------------------------------------------------------------------------------------
    def execute(self, name: str, arguments: Any) -> str:
        from orchestrator.tools.normalize import NormalizeError, resolve_name
        offered = None
        try:
            offered, _ = resolve_name(name, self.toolset.names, self.toolset.names)
        except NormalizeError:
            offered = None
        if offered is None or not isinstance(arguments, dict):
            return self._local(name, arguments, [])  # the registry refuses and audits it, as before
        route = self.toolset.routes[offered]
        args = dict(arguments)
        local_repairs: List[Any] = []
        audit_repairs: List[Dict[str, Any]] = []
        place = self._take_place(route, name, args, local_repairs, audit_repairs) if route.place_field else None
        target, why = self._decide(route, name, args, place)
        if target == LOCAL_KEY:
            if route.local is None:
                label = self.toolset.remote.label if self.toolset.remote else FALLBACK_LABEL
                raise self._refuse(route, name, args, LOCAL_KEY, "not_supported",
                                   f"{self._title(offered)} is only on {label}, not on this computer.",
                                   f"Call {offered} with place {self.toolset.remote.key if self.toolset.remote else ''}"
                                   ", or use a tool this computer offers.")
            return self._local(name if route.local == offered else route.local, args, local_repairs)
        if route.remote is None:
            label = self.toolset.remote.label if self.toolset.remote else FALLBACK_LABEL
            raise self._refuse(route, name, args, target, "not_supported",
                               f"The object of this call is on {label}, and {label} does not offer "
                               f"{self._title(offered)} to Ditto (its permissions there, or another framework "
                               "version).",
                               "Tell the person what is missing there; nothing moves between the computers by "
                               "itself.")
        if why:
            audit_repairs.append({"kind": "route", "field": PLACE_FIELD, "now": target})
        return self._remote(route, offered, args, audit_repairs, why)

    def _take_place(self, route: ToolRoute, name: str, args: Dict[str, Any], local_repairs: List[Any],
                    audit_repairs: List[Dict[str, Any]]) -> Optional[str]:
        from orchestrator.tools.normalize import Repair
        given: List[Tuple[str, Any]] = []
        if PLACE_FIELD in args:
            given.append((PLACE_FIELD, args.pop(PLACE_FIELD)))
        for alias in PLACE_ALIASES:
            if alias in args and alias not in route.fields:
                given.append((alias, args.pop(alias)))
        chosen: Optional[str] = None
        for key, value in given:
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            canonical = self._canonical_place(value)
            if canonical is None:
                raise self._refuse(route, name, args, LOCAL_KEY, "invalid_value",
                                   f"'{PLACE_FIELD}' must be one of: {', '.join(self._allowed(route))}.",
                                   f"Use {PLACE_FIELD} {self._allowed(route)[0]}, or leave it out.",
                                   field_name=PLACE_FIELD, input_error=True)
            if chosen is not None and canonical != chosen:
                raise self._refuse(route, name, args, LOCAL_KEY, "invalid_value",
                                   f"'{PLACE_FIELD}' was given twice with different computers.",
                                   f"Give {PLACE_FIELD} once.", field_name=PLACE_FIELD, input_error=True)
            if key != PLACE_FIELD:
                local_repairs.append(Repair("alias", PLACE_FIELD, key, PLACE_FIELD))
                audit_repairs.append({"kind": "alias", "field": PLACE_FIELD, "wrote": key})
            if isinstance(value, str) and value != canonical:
                local_repairs.append(Repair("canonical", PLACE_FIELD, value[:40], canonical))
                audit_repairs.append({"kind": "canonical", "field": PLACE_FIELD, "now": canonical})
            chosen = canonical
        return chosen

    def _allowed(self, route: ToolRoute) -> List[str]:
        keys = []
        if route.local is not None:
            keys.append(LOCAL_KEY)
        if route.remote is not None and self.toolset.remote is not None:
            keys.append(self.toolset.remote.key)
        return keys or [LOCAL_KEY]

    def _canonical_place(self, value: Any) -> Optional[str]:
        if not isinstance(value, str):
            return None
        words = " ".join(value.strip().lower().replace("_", " ").replace("-", " ").split())
        if words in _LOCAL_WORDS or words == LOCAL_KEY.replace("_", " "):
            return LOCAL_KEY
        remote = self.toolset.remote
        if remote is not None and (words in _REMOTE_WORDS or words == remote.key.replace("_", " ")
                                   or words == " ".join(remote.label.lower().replace("_", " ").replace("-", " ")
                                                        .split())):
            return remote.key
        return None

    def _decide(self, route: ToolRoute, name: str, args: Dict[str, Any], place: Optional[str]) -> Tuple[str, str]:
        """(the place key the call goes to, a routing note when ids decided it). Without another computer every call
        stays here, whatever the index says."""
        if self.toolset.remote is None:
            return LOCAL_KEY, ""
        keys = self._keys()
        found = {object_id: self.index.where(object_id) for object_id in collect_ids(args)}
        found = {i: w for i, w in found.items() if w == "both" or w in keys}
        if place is not None:
            for object_id, where in found.items():
                if where not in ("both", place):
                    copied = self.index.copy_at(object_id, place)
                    if copied:
                        when = copied[1][11:16] if len(copied[1]) >= 16 else copied[1]
                        hint = f"Use {copied[0]}, its copy on {self._label(place)} (sent {when})."
                    else:
                        hint = (f"Call it with {PLACE_FIELD} {where}, or leave {PLACE_FIELD} out: the id decides. "
                                "Objects never move between the computers by themselves.")
                    raise self._refuse(route, name, args, place, "wrong_place",
                                       f"{object_id} is on {self._label(where)}, not on {self._label(place)}.", hint,
                                       field_name=PLACE_FIELD, input_error=True)
            return place, ""
        singles = {w for w in found.values() if w != "both"}
        if len(singles) > 1:
            parts = [f"{i} is on {self._label(w)}" for i, w in found.items() if w != "both"]
            raise self._refuse(route, name, args, LOCAL_KEY, "needs_transfer",
                               "; ".join(parts) + ": objects never move between the computers by themselves.",
                               "Do the task on the computer where the object is, one computer at a time, or ask the "
                               "person to copy it there.")
        if singles:
            target = singles.pop()
            if target != LOCAL_KEY:
                ids = [i for i, w in found.items() if w == target]
                return target, f"Ran on {self._label(target)}, where {', '.join(ids[:3])} is (the id decided)."
            return target, ""
        possible = self._allowed(route)
        if found and len(possible) == 1:  # ids at both computers, a tool that runs at one of them only
            return possible[0], ""
        if found:  # only ids known at both computers
            ids = ", ".join(list(found)[:3])
            raise self._refuse(route, name, args, LOCAL_KEY, "missing_argument",
                               f"{ids} exists on both computers, so '{PLACE_FIELD}' is needed.",
                               f"Add {PLACE_FIELD}: one of {', '.join(self._allowed(route))}.",
                               field_name=PLACE_FIELD, input_error=True)
        if route.place_required:
            raise self._refuse(route, name, args, LOCAL_KEY, "missing_argument",
                               f"'{PLACE_FIELD}' is required: {self._title(route.offered)} runs on "
                               f"{self.toolset.remote.label if self.toolset.remote else FALLBACK_LABEL} only.",
                               f"Add {PLACE_FIELD}: {', '.join(self._allowed(route))}.", field_name=PLACE_FIELD,
                               input_error=True)
        return LOCAL_KEY, ""

    def _local(self, name: Any, arguments: Any, repairs: List[Any]) -> str:
        from orchestrator.tools.registry import ToolDenied, ToolError, ToolInputError
        if self.registry is None:
            raise ToolError("This computer's tools are not available in this turn.", code="failed",
                            next="Tell the person; the framework on this computer did not start its tools.")
        ctx = dataclasses.replace(self.ctx, repairs=list(self.ctx.repairs or []) + list(repairs)) if repairs \
            else self.ctx
        title = self._title(name)
        try:
            output = self.registry.call(name, arguments, ctx)
        except (ToolDenied, ToolInputError) as e:  # the framework's own messages
            self._note(str(name), title, False, str(e))
            raise
        except ToolError as e:
            # Handler errors can carry external text (server replies, log lines): data, not instructions. The
            # reason code and field stay, so the loop tells a fix from a repeat.
            self._note(str(name), title, False, redact_secrets(str(e))[:300])
            raise ToolError(wrap_untrusted(str(e), source=f"tool error:{name}", max_len=4000), code=e.code,
                            field=e.field) from None
        if self.learn_local:
            try:
                dumped = output.model_dump(mode="json") if hasattr(output, "model_dump") else output
                self.index.learn(LOCAL_KEY, dumped)
            except Exception as e:  # noqa: BLE001 - routing hints never fail a call that ran
                logger.warning(f"Could not record where the result's objects live: {e}")
        self._note(str(name), title, True)
        return wrap_untrusted(tool_result_text(output), source=f"tool:{name}")

    def _taint(self) -> None:
        metadata = getattr(self.ctx, "metadata", None)
        if isinstance(metadata, dict):
            metadata["taint"] = ["remote_result"]

    def _remote(self, route: ToolRoute, offered: str, args: Dict[str, Any], repairs: List[Dict[str, Any]],
                why: str) -> str:
        from orchestrator.tools.registry import ToolError
        from orchestrator.tools.remote_audit import end_outcome, record_remote_call
        place = self.toolset.remote
        label, tool, title = place.label, route.remote, self._title(offered)
        if self.session is None or self._lost:
            raise self._refuse(route, offered, args, place.key, "place_unreachable",
                               f"{label} cannot be reached now, so {title} did not run there.",
                               f"Tell the person that {label} is not reachable; this computer's tools still work.")
        try:
            started = record_remote_call(self.audit, self.ctx, tool, label, route.read_only, args, "started",
                                         remote_server=place.server, repairs=repairs)
        except ToolError as e:  # this computer's audit log cannot be written: a change is not sent
            self._note(offered, title, False, str(e), label)
            raise
        try:
            result = self.session.call_raw(tool, args)
        except ToolError as e:
            record_remote_call(self.audit, self.ctx, tool, label, route.read_only, args, "end", outcome="error",
                               reason_code=e.code, detail=f"not sent to {label}: {e.code}",
                               remote_server=place.server, started=started)
            self._note(offered, title, False, str(e), label)
            raise
        except Exception as e:  # noqa: BLE001 - a broken connection is a failed call, never a crash of the turn
            record_remote_call(self.audit, self.ctx, tool, label, route.read_only, args, "end", outcome="error",
                               reason_code="place_unreachable", detail=f"{label} did not answer ({type(e).__name__})",
                               remote_server=place.server, started=started)
            self._lost_connection(label, type(e).__name__)
            self._note(offered, title, False, f"{label} did not answer", label)
            next_step = ("Call it again once; if it fails again, tell the person." if route.read_only else
                         f"It is not known whether it ran on {label}: check with a listing tool there before calling "
                         "it again, and tell the person.")
            raise ToolError(f"{label} did not answer ({type(e).__name__}), so the result of {title} is unknown.",
                            code="place_unreachable", next=next_step) from None
        self.remote_used = True
        self._taint()
        if result.is_error:
            code = result.code or "failed"
            record_remote_call(self.audit, self.ctx, tool, label, route.read_only, args, "end",
                               outcome=end_outcome(code), reason_code=code,
                               detail=f"refused on {label}: {code}", remote_server=place.server, started=started)
            hint = self._hint(code, args, place)
            self._note(offered, title, False, f"refused on {label}: {reason_words(code)}", label)
            raise ToolError(f"{label} did not run {title}: {reason_words(code)}.{hint} Its own words follow as "
                            "data:\n" + wrap_untrusted(result.text, source=f"remote:{label}:{tool}", max_len=8000),
                            code=code, field=result.field if result.field in route.fields else "")
        record_remote_call(self.audit, self.ctx, tool, label, route.read_only, args, "end", outcome="ok",
                           detail=f"ran on {label}", remote_server=place.server, started=started)
        try:
            self.index.learn(place.key, result.structured if isinstance(result.structured, (dict, list)) else None)
        except Exception as e:  # noqa: BLE001 - routing hints never fail a call that ran
            logger.warning(f"Could not record where the remote result's objects live: {e}")
        self._note(offered, title, True, why, label)
        head = result_line(label) + (f" {why}" if why else "")
        return head + "\n" + wrap_untrusted(result.text, source=f"remote:{label}:{tool}", max_len=60000)

    def _hint(self, code: str, args: Dict[str, Any], place: Place) -> str:
        if code == "unknown_id":
            here = [i for i in collect_ids(args) if self.index.where(i) in (LOCAL_KEY, "both")]
            if here:
                return (f" This computer knows {here[0]}: call it with {PLACE_FIELD} {LOCAL_KEY} if you meant this "
                        "computer's.")
        if code == "permission_denied":
            return (f" The person can allow it for Ditto's connection on {place.label} (its tool permissions for "
                    "Ditto's token).")
        if code == "declined":
            return " Do not call it again unless the person asks for it."
        return ""

    def _lost_connection(self, label: str, reason: str) -> None:
        """A broken connection: reopened at most once per turn; if that fails, later calls answer place_unreachable."""
        if self._reopened or self.session is None:
            self._lost = True
            return
        self._reopened = True
        try:
            self.session.reopen()
        except Exception as e:  # noqa: BLE001 - not reachable for the rest of this turn
            self._lost = True
            self.notices.append(f"{label} stopped answering during this turn ({reason}; reconnecting failed: "
                                f"{type(e).__name__}). Ditto went on with this computer's tools.")


__all__ = ["LOCAL_KEY", "FALLBACK_KEY", "FALLBACK_LABEL", "PLACE_KEY_RE", "PROVIDER_NAME_RE", "RANDOM_ID",
           "NEVER_FROM_REMOTE", "REMOTE_ANSWER_TOOL", "PLACE_FIELD", "PLACE_ALIASES", "Place", "LOCAL", "place_key",
           "place_label", "server_identity", "remote_place", "strip_schema", "schema_key", "safe_remote_schema",
           "ListedTool", "RemoteListing", "ListingCache", "collect_ids", "PlaceIndex", "ToolRoute", "HybridToolSet",
           "result_line", "HybridRouter"]
