#!/usr/bin/env python3
"""
How a building block's engine is reached through the gateway (a block's optional `reach` section), and the gateway's
route table the bundle generator makes from the chosen blocks (routes.json, schema llmcf-gateway-routes/1).

The gateway is a universal adapter: a new model or engine needs a target module that builds it and a block that says
how it runs and how it is reached, never a change of the gateway. A block's `reach` lists its routes; each route:

- `path`: the client's path, under /v1/ or /api/ (RE_PATH: lower-case letters, digits, '.', '_' and '-' per segment);
  the gateway's own paths (/health, /metrics and everything below /v1/llmcf and /api/llmcf) are refused;
- `method`: GET or POST (what the protocol allows: METHODS);
- `protocol`: one of the gateway's adapters (ADAPTERS: openai-chat, openai-completions, openai-models,
  openai-embeddings, openai-audio-transcription, openai-audio-speech: each checks its OpenAI body strictly) or a
  generic checked passthrough (GENERIC: http-json, a strict JSON object; http-multipart, the bounded form parser with
  the declared `fields`; http-sse, an event stream passed through with an idle timeout; http-binary, a body of the
  declared magic `types`);
- `upstream_path`: the engine's own path the request goes to (whisper-server's /inference);
- limits: `max_body_kb` (the body cap; the generator takes the smaller of it and the gateway's own cap for the
  protocol), `timeout_s` (the wait for the engine's answer and the longest gap inside it), `stream` (the answer may be
  an event stream; without it a stream is refused), `types` (http-binary: the magic types the body may have) and
  `fields` (http-multipart: each field's name, whether it is a file, the magic types of a file, whether it is needed);
- `engine_route_key`: the route's short name within its engine (metrics and /health say `<engine>.<key>`), needed
  where one engine serves several routes with the same last path word;
- `match: images` (openai-chat only): the route takes the chat requests that carry image parts (a vision engine next
  to the language model on the same path).

Python checks the rules here, the gateway (rust/llmcf-gateway/src/routes.rs) checks the very same rules when it reads
the table; tests/data/gateway_routes_vectors.json holds the vectors both sides are tested with. No two routes of one
bundle share a method, path and match.
"""

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

ROUTES_SCHEMA = "llmcf-gateway-routes/1"
ROUTES_FILE = "routes.json"
MAX_TABLE_BYTES = 64 * 1024
MAX_ROUTES = 64
MAX_BLOCK_ROUTES = 16
MAX_ENGINES = 16
MAX_FIELDS = 16
MAX_TYPES = 16
MAX_BODY_KB = 204800          # 200 MB, the gateway's largest upload
DEFAULT_TIMEOUT_S = 900       # the gateway's answer wait
MAX_TIMEOUT_S = 3600

ADAPTERS = ("openai-chat", "openai-completions", "openai-models", "openai-embeddings", "openai-audio-transcription",
            "openai-audio-speech")
GENERIC = ("http-json", "http-multipart", "http-sse", "http-binary")
PROTOCOLS = ADAPTERS + GENERIC
METHODS = {"openai-models": ("GET",), "http-json": ("GET", "POST"), "http-sse": ("GET", "POST")}
# The protocols whose body is JSON (the gateway's --max-body-kb caps them) and those that carry an upload.
JSON_PROTOCOLS = ("openai-chat", "openai-completions", "openai-models", "openai-embeddings", "openai-audio-speech",
                  "http-json", "http-sse")
# The magic types a binary body or a multipart file may be declared with (recognized by their first bytes).
MAGIC_TYPES = ("jpeg", "png", "gif", "webp", "bmp", "tiff", "wav", "flac", "ogg", "mp3", "mp4", "webm", "pdf")
MATCHES = ("images",)

RE_PATH = re.compile(r"^/(?:v1|api)(?:/[a-z0-9][a-z0-9._-]{0,63}){1,6}$", re.ASCII)
RE_UPSTREAM = re.compile(r"^(?:/[A-Za-z0-9_][A-Za-z0-9._-]{0,63}){1,8}$", re.ASCII)
RE_KEY = re.compile(r"^[a-z][a-z0-9-]{0,31}$", re.ASCII)
RE_ENGINE = re.compile(r"^[a-z][a-z0-9-]{0,39}$", re.ASCII)
RE_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$", re.ASCII)
RESERVED = ("/health", "/metrics")
RESERVED_PREFIXES = ("/v1/llmcf", "/api/llmcf")

ROUTE_KEYS = ("path", "method", "protocol", "upstream_path", "max_body_kb", "timeout_s", "stream", "types", "fields",
              "engine_route_key", "match")
TABLE_ROUTE_REQUIRED = ("path", "method", "protocol", "engine", "upstream_path", "max_body_kb", "timeout_s", "stream",
                        "key")
TABLE_ROUTE_OPTIONAL = ("types", "fields", "match")
ENGINE_KEYS = ("id", "port", "key")
ENGINE_OPTIONAL = ("ctx_size",)


class ReachError(ValueError):
    """A reach section or a route table that is not what the rules take; the message names the field."""


@dataclass(frozen=True)
class Field:
    """One field of an http-multipart route: its name, whether it is a file, a file's magic types, needed or not."""
    name: str
    file: bool = False
    types: Tuple[str, ...] = ()
    required: bool = False

    def as_json(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"name": self.name, "file": self.file, "required": self.required}
        if self.types:
            out["types"] = list(self.types)
        return out


@dataclass(frozen=True)
class Route:
    """One route as a block declares it (defaults filled in)."""
    path: str
    method: str
    protocol: str
    upstream_path: str
    max_body_kb: int
    timeout_s: int
    stream: bool
    key: str
    types: Tuple[str, ...] = ()
    fields: Tuple[Field, ...] = ()
    match: str = ""

    def entry(self, engine: str, max_body_kb: Optional[int] = None) -> Dict[str, Any]:
        """The route as one entry of the gateway's table, going to `engine`, with its effective body cap."""
        out: Dict[str, Any] = {"path": self.path, "method": self.method, "protocol": self.protocol, "engine": engine,
                               "upstream_path": self.upstream_path,
                               "max_body_kb": self.max_body_kb if max_body_kb is None else max_body_kb,
                               "timeout_s": self.timeout_s, "stream": self.stream, "key": self.key}
        if self.types:
            out["types"] = list(self.types)
        if self.fields:
            out["fields"] = [f.as_json() for f in self.fields]
        if self.match:
            out["match"] = self.match
        return out

    @property
    def collision_key(self) -> Tuple[str, str, str]:
        return (self.method, self.path, self.match)


@dataclass(frozen=True)
class Reach:
    """A block's reach section: its routes and the variable that holds its engine's port."""
    routes: Tuple[Route, ...]
    port: str = "port"


def _whole(value: Any, low: int, high: int, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ReachError(f"{what} is a whole number from {low} to {high}")
    return value


def _types(raw: Any, what: str) -> Tuple[str, ...]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_TYPES or \
            any(not isinstance(t, str) or t not in MAGIC_TYPES for t in raw) or len(set(raw)) != len(raw):
        raise ReachError(f"{what}: types lists magic types among {', '.join(MAGIC_TYPES)}, each once")
    return tuple(raw)


def _fields(raw: Any, what: str) -> Tuple[Field, ...]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_FIELDS:
        raise ReachError(f"{what}: fields is a list of 1 to {MAX_FIELDS} fields")
    out: List[Field] = []
    for item in raw:
        if not isinstance(item, dict) or set(item) - {"name", "file", "types", "required"} or "name" not in item:
            raise ReachError(f"{what}: a field is {{name, file, types, required}}")
        name = item["name"]
        if not isinstance(name, str) or not RE_FIELD.fullmatch(name):
            raise ReachError(f"{what}: a field name is letters, digits, '_', '.' and '-'")
        file, required = item.get("file", False), item.get("required", False)
        if not isinstance(file, bool) or not isinstance(required, bool):
            raise ReachError(f"{what}: field {name}: file and required are yes or no")
        types: Tuple[str, ...] = ()
        if "types" in item:
            if not file:
                raise ReachError(f"{what}: field {name}: only a file field has types")
            types = _types(item["types"], f"{what}: field {name}")
        out.append(Field(name=name, file=file, types=types, required=required))
    names = [f.name for f in out]
    if len(names) != len(set(names)):
        raise ReachError(f"{what}: a field name twice")
    return tuple(out)


def _route(raw: Any, table: bool) -> Tuple[Route, Optional[str]]:
    """A route from a block's reach (`table` False: defaults filled in) or from the gateway's table (`table` True:
    every value given, with its engine). Returns the route and, for a table entry, its engine."""
    if not isinstance(raw, dict):
        raise ReachError("a route is a mapping")
    allowed = set(TABLE_ROUTE_REQUIRED + TABLE_ROUTE_OPTIONAL) if table else set(ROUTE_KEYS)
    unknown = set(raw) - allowed
    if unknown:
        raise ReachError(f"route: unknown fields {', '.join(sorted(map(str, unknown)))}")
    if table:
        missing = [k for k in TABLE_ROUTE_REQUIRED if k not in raw]
        if missing:
            raise ReachError(f"route: {', '.join(missing)} missing")
    path = raw.get("path")
    if not isinstance(path, str) or not RE_PATH.fullmatch(path):
        raise ReachError("route path is /v1/... or /api/... (lower-case letters, digits, '.', '_', '-')")
    if path in RESERVED or any(path == p or path.startswith(p + "/") for p in RESERVED_PREFIXES):
        raise ReachError(f"route {path}: a path of the gateway's own")
    what = f"route {path}"
    protocol = raw.get("protocol")
    if protocol not in PROTOCOLS:
        raise ReachError(f"{what}: protocol is one of {', '.join(PROTOCOLS)}")
    allowed_methods = METHODS.get(protocol, ("POST",))
    method = raw.get("method", allowed_methods[0])
    if method not in allowed_methods:
        raise ReachError(f"{what}: protocol {protocol} takes {' or '.join(allowed_methods)}")
    upstream = raw.get("upstream_path", path)
    if not isinstance(upstream, str) or not RE_UPSTREAM.fullmatch(upstream):
        raise ReachError(f"{what}: upstream_path is a plain absolute path")
    body = _whole(raw.get("max_body_kb", MAX_BODY_KB if protocol in ("openai-audio-transcription", "http-multipart",
                                                                     "http-binary") else 16384),
                  1, MAX_BODY_KB, f"{what}: max_body_kb")
    timeout = _whole(raw.get("timeout_s", DEFAULT_TIMEOUT_S), 1, MAX_TIMEOUT_S, f"{what}: timeout_s")
    stream = raw.get("stream", protocol == "http-sse")
    if not isinstance(stream, bool):
        raise ReachError(f"{what}: stream is yes or no")
    if protocol == "http-sse" and not stream:
        raise ReachError(f"{what}: an http-sse route streams")
    if protocol in ("openai-models", "openai-embeddings", "openai-audio-transcription", "http-binary",
                    "http-multipart") and stream:
        raise ReachError(f"{what}: protocol {protocol} answers no stream")
    types: Tuple[str, ...] = ()
    if "types" in raw:
        if protocol != "http-binary":
            raise ReachError(f"{what}: only an http-binary route has types (a multipart file has its own)")
        types = _types(raw["types"], what)
    elif protocol == "http-binary":
        raise ReachError(f"{what}: an http-binary route names the magic types its body may have")
    fields: Tuple[Field, ...] = ()
    if "fields" in raw:
        if protocol != "http-multipart":
            raise ReachError(f"{what}: only an http-multipart route has fields")
        fields = _fields(raw["fields"], what)
    elif protocol == "http-multipart":
        raise ReachError(f"{what}: an http-multipart route names its fields")
    match = raw.get("match", "")
    if match and (match not in MATCHES or protocol != "openai-chat"):
        raise ReachError(f"{what}: match is images, on an openai-chat route only")
    key = raw.get("key" if table else "engine_route_key", None)
    if key is None:
        key = path.rsplit("/", 1)[-1].replace(".", "-").replace("_", "-").lower()
        key = re.sub(r"^[^a-z]+", "", key)[:32] or "route"
        if match:
            key = f"{key}-{match}"[:32]
    if not isinstance(key, str) or not RE_KEY.fullmatch(key):
        raise ReachError(f"{what}: engine_route_key is a short word (lower-case letters, digits and '-')")
    engine = None
    if table:
        engine = raw.get("engine")
        if not isinstance(engine, str) or not RE_ENGINE.fullmatch(engine):
            raise ReachError(f"{what}: engine is a component id")
    return Route(path=path, method=method, protocol=protocol, upstream_path=upstream, max_body_kb=body,
                 timeout_s=timeout, stream=stream, key=key, types=types, fields=fields, match=match), engine


def _collisions(routes: List[Tuple[Route, str]], what: str = "") -> None:
    seen: Dict[Tuple[str, str, str], str] = {}
    keys: Dict[Tuple[str, str], str] = {}
    for route, owner in routes:
        other = seen.get(route.collision_key)
        if other is not None:
            raise ReachError(f"route {route.method} {route.path}"
                             + (f" ({route.match})" if route.match else "")
                             + f" is declared by {other} and {owner}{what}")
        seen[route.collision_key] = owner
        if (owner, route.key) in keys:
            raise ReachError(f"{owner}: the route key {route.key} twice (give engine_route_key)")
        keys[(owner, route.key)] = route.path
    for route, owner in routes:
        if route.match and (route.method, route.path, "") not in seen:
            raise ReachError(f"route {route.path} ({route.match}) needs the plain openai-chat route on the same path")
        if route.match:
            plain = next(r for r, _ in routes if r.collision_key == (route.method, route.path, ""))
            if plain.protocol != "openai-chat":
                raise ReachError(f"route {route.path} ({route.match}) needs the plain openai-chat route on the same "
                                 "path")


def parse_reach(raw: Any, variables: Dict[str, Any]) -> Optional[Reach]:
    """A block's reach section (None when it has none); `variables` the block's variables by name (its engine's port
    is the variable `port` names, a variable of type port)."""
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) - {"routes", "port"}:
        raise ReachError("reach is {routes, port}")
    port = raw.get("port", "port")
    var = variables.get(port) if isinstance(port, str) else None
    if var is None or getattr(var, "type", None) != "port":
        raise ReachError("reach.port names the block's variable of type port its engine listens on")
    items = raw.get("routes")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_BLOCK_ROUTES:
        raise ReachError(f"reach.routes is a list of 1 to {MAX_BLOCK_ROUTES} routes")
    routes = [_route(item, table=False)[0] for item in items]
    _block_collisions(routes)
    return Reach(routes=tuple(routes), port=port)


def _block_collisions(routes: List[Route]) -> None:
    """A block's own routes: no method, path and match twice, no key twice (an images route may stand alone in a
    block: the plain chat route comes from another block of the bundle)."""
    seen, keys = set(), set()
    for r in routes:
        if r.collision_key in seen:
            raise ReachError(f"route {r.method} {r.path} twice in this block")
        if r.key in keys:
            raise ReachError(f"the route key {r.key} twice in this block (give engine_route_key)")
        seen.add(r.collision_key)
        keys.add(r.key)


@dataclass(frozen=True)
class Engine:
    id: str
    port: int
    key: bool
    ctx_size: Optional[int] = None

    def as_json(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"id": self.id, "port": self.port, "key": self.key}
        if self.ctx_size is not None:
            out["ctx_size"] = self.ctx_size
        return out


@dataclass(frozen=True)
class Table:
    """The gateway's route table: its engines (component id, loopback port, whether requests carry the runtime's
    key, the context size of a llama-server) and its routes, each to one engine."""
    engines: Tuple[Engine, ...]
    routes: Tuple[Tuple[Route, str], ...]

    def as_json(self) -> Dict[str, Any]:
        return {"schema": ROUTES_SCHEMA, "engines": [e.as_json() for e in self.engines],
                "routes": [r.entry(engine) for r, engine in self.routes]}

    def dump(self) -> bytes:
        """The bytes of routes.json as the bundle carries them (sorted keys, two-space indent, one final newline)."""
        return (json.dumps(self.as_json(), indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii")

    def lines(self) -> List[str]:
        """The table for the person: one line per route."""
        return [f"{r.method} {r.path}" + (f" ({r.match})" if r.match else "")
                + f" -> {engine} {r.upstream_path} ({r.protocol}, up to {r.max_body_kb} KB, {r.timeout_s} s"
                + (", stream" if r.stream else "") + ")" for r, engine in self.routes]


def make_table(engines: List[Engine], routes: List[Tuple[Route, str]]) -> Table:
    """A route table from the engines and their routes, checked as the gateway checks it (ReachError)."""
    table = Table(engines=tuple(engines), routes=tuple(routes))
    check_table(table.as_json())
    return table


def check_table(data: Any) -> Table:
    """The route table from its JSON (bytes, text or a parsed mapping), checked by the rules the gateway checks it with;
    ReachError with the reason."""
    if isinstance(data, (bytes, bytearray)):
        if len(data) > MAX_TABLE_BYTES:
            raise ReachError("the route table is larger than 64 KiB")
        try:
            data = json.loads(bytes(data).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ReachError("the route table is no JSON") from None
    elif isinstance(data, str):
        if len(data.encode("utf-8")) > MAX_TABLE_BYTES:
            raise ReachError("the route table is larger than 64 KiB")
        try:
            data = json.loads(data)
        except ValueError:
            raise ReachError("the route table is no JSON") from None
    if not isinstance(data, dict) or set(data) != {"schema", "engines", "routes"}:
        raise ReachError("the route table is {schema, engines, routes}")
    if data["schema"] != ROUTES_SCHEMA:
        raise ReachError(f"schema must be {ROUTES_SCHEMA}")
    raw_engines, raw_routes = data["engines"], data["routes"]
    if not isinstance(raw_engines, list) or not 1 <= len(raw_engines) <= MAX_ENGINES:
        raise ReachError(f"engines is a list of 1 to {MAX_ENGINES} engines")
    engines: List[Engine] = []
    for item in raw_engines:
        if not isinstance(item, dict) or set(item) - set(ENGINE_KEYS + ENGINE_OPTIONAL) or \
                not set(ENGINE_KEYS) <= set(item):
            raise ReachError("an engine is {id, port, key, ctx_size}")
        eid = item["id"]
        if not isinstance(eid, str) or not RE_ENGINE.fullmatch(eid):
            raise ReachError("an engine id is a component id (lower-case letters, digits and '-')")
        port = _whole(item["port"], 1024, 65535, f"engine {eid}: port")
        if not isinstance(item["key"], bool):
            raise ReachError(f"engine {eid}: key is yes or no")
        ctx = item.get("ctx_size")
        if ctx is not None:
            ctx = _whole(ctx, 256, 1 << 20, f"engine {eid}: ctx_size")
        engines.append(Engine(id=eid, port=port, key=item["key"], ctx_size=ctx))
    if len({e.id for e in engines}) != len(engines):
        raise ReachError("an engine id twice")
    if len({e.port for e in engines}) != len(engines):
        raise ReachError("an engine port twice")
    if not isinstance(raw_routes, list) or not 1 <= len(raw_routes) <= MAX_ROUTES:
        raise ReachError(f"routes is a list of 1 to {MAX_ROUTES} routes")
    routes: List[Tuple[Route, str]] = []
    ids = {e.id for e in engines}
    for item in raw_routes:
        route, engine = _route(item, table=True)
        if engine not in ids:
            raise ReachError(f"route {route.path}: engine {engine} is none of the table's engines")
        routes.append((route, engine or ""))
    _collisions(routes)
    return Table(engines=tuple(engines), routes=tuple(routes))


__all__ = ["ADAPTERS", "Engine", "Field", "GENERIC", "JSON_PROTOCOLS", "MAGIC_TYPES", "MAX_BODY_KB", "PROTOCOLS",
           "ROUTES_FILE", "ROUTES_SCHEMA", "Reach", "ReachError", "Route", "Table", "check_table", "make_table",
           "parse_reach"]
