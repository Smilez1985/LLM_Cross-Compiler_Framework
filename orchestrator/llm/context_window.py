#!/usr/bin/env python3
"""
The real context window of the model Ditto talks to (docs/DESIGN.md, Ditto, the row "The real context window of the
model Ditto talks to comes from one function"). One function, `context_window`, for the local model and every API
model, with its source named:

1. the managed local server: the context `/props` reported after the start (`n_ctx_slot` in server.json), else the
   `-c` it was started with, marked as not confirmed yet;
2. the provider's server, asked through the adapter's own SDK client (`ProviderAdapter.context_window`), cached for
   24 hours per provider, address and model in `<state dir>/llm/context_windows.json`;
3. the pinned table of configs/context_windows.yml (source and date per entry);
4. the conservative default of the same file, shown as such.

The person's `ai_context_window` (0 = automatic) is the window of a model whose provider does not tell it; it never
goes above what a local server or a provider reported. Nothing here raises for a provider that cannot be reached: the
lookup falls through to the table and the default, and the result says which source decided.
"""

import fnmatch
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel

from orchestrator.utils.logging import get_logger

logger = get_logger("ContextWindow")

TABLE_FILE = Path(__file__).resolve().parents[2] / "configs" / "context_windows.yml"
CACHE_FILE = "context_windows.json"
CACHE_TTL = 24 * 3600          # seconds an answer of a provider's server is reused
DEFAULT_TOKENS = 32768         # the table's default when the file cannot be read
SOURCES = ("local_server", "local_started", "provider", "table", "default", "setting")

# How each source is worded for the person (CLI, GUI box); the details follow in `how`.
SOURCE_WORDS = {
    "local_server": "the local server's own report (/props)",
    "local_started": "the context the local server was started with (not confirmed by /props yet)",
    "provider": "the provider's server",
    "table": "the pinned table",
    "default": "conservative default",
    "setting": "your setting ai_context_window",
}


class ServerWindow(BaseModel):
    """What a provider's server said about a model's window (ProviderAdapter.context_window). `how`: the endpoint and
    field, in plain words; `cache` False for an answer that must be asked again next time (Ollama before it loaded the
    model)."""
    tokens: int
    max_output: Optional[int] = None
    how: str
    cache: bool = True


class ContextWindow(BaseModel):
    """The window of one model. `source`: one of SOURCES; `how`: what said it (endpoint and field, table source and
    date); `started`/`confirmed`: the local server's -c and whether /props reported the same."""
    tokens: int
    max_output: Optional[int] = None
    source: str
    how: str = ""
    provider: str = ""
    model: str = ""
    checked_at: str = ""
    started: Optional[int] = None
    confirmed: Optional[bool] = None
    cached: bool = False
    note: str = ""

    def words(self) -> str:
        """The source for the person: '1000000 tokens, from the provider's server (Anthropic Models API ...)'."""
        what = SOURCE_WORDS.get(self.source, self.source)
        return f"{self.tokens} tokens, from {what}" + (f" ({self.how})" if self.how else "")

    def data(self) -> Dict[str, Any]:
        return self.model_dump()


def _now_iso(now: Optional[float] = None) -> str:
    moment = datetime.fromtimestamp(now if now is not None else time.time(), tz=timezone.utc)
    return moment.isoformat(timespec="seconds")


# --- the pinned table ----------------------------------------------------------------------------------------------

def load_table(path: Optional[Path] = None) -> Dict[str, Any]:
    """configs/context_windows.yml as data ({} when it cannot be read)."""
    try:
        data = yaml.safe_load(Path(path or TABLE_FILE).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        logger.warning(f"The context window table cannot be read ({e.__class__.__name__}).")
        return {}
    return data if isinstance(data, dict) else {}


def pinned_window(provider: str, model: str, table: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """The table's entry for `provider` and `model` (an exact id first, then a prefix ending in *), or None."""
    table = load_table() if table is None else table
    entries = [e for e in table.get("windows") or [] if isinstance(e, dict) and e.get("provider") == provider]
    for entry in entries:
        if str(entry.get("model")) == model:
            return entry
    for entry in entries:
        pattern = str(entry.get("model") or "")
        if pattern.endswith("*") and fnmatch.fnmatchcase(model, pattern):
            return entry
    return None


def default_window(table: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    table = load_table() if table is None else table
    entry = table.get("default") if isinstance(table.get("default"), dict) else {}
    tokens = entry.get("tokens") if isinstance(entry.get("tokens"), int) else DEFAULT_TOKENS
    return {"tokens": tokens, "source": str(entry.get("source") or SOURCE_WORDS["default"])}


# --- the cache of the providers' answers ---------------------------------------------------------------------------

class WindowCache:
    """The providers' answers, per provider, address and model, with their source and time (mode 0600)."""

    def __init__(self, path: Path, ttl: float = CACHE_TTL):
        self.path = Path(path)
        self.ttl = ttl

    @staticmethod
    def key(provider: str, base_url: Optional[str], model: str) -> str:
        return json.dumps([provider, base_url or "", model])

    def _read(self) -> Dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, key: str, now: float) -> Optional[Dict[str, Any]]:
        entry = self._read().get(key)
        if not isinstance(entry, dict) or not isinstance(entry.get("tokens"), int):
            return None
        if now - float(entry.get("at") or 0) > self.ttl:
            return None
        return entry

    def put(self, key: str, entry: Dict[str, Any]) -> None:
        data = self._read()
        data[key] = entry
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=f".{self.path.name}.")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, sort_keys=True)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except OSError as e:
            logger.warning(f"Could not keep the context window ({e.__class__.__name__}).")


def cache_for(client: Any) -> Optional[WindowCache]:
    state_path = getattr(client, "_state_path", None)
    if not callable(state_path):
        return None
    try:
        return WindowCache(Path(state_path("llm")) / CACHE_FILE)
    except Exception:  # noqa: BLE001 - no state folder: nothing is cached
        return None


# --- the one function ----------------------------------------------------------------------------------------------

def _setting(client: Any) -> Optional[int]:
    reader = getattr(client, "_setting", None)
    try:
        value = int(reader("ai_context_window", 0)) if callable(reader) else 0
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _local(client: Any, target: Any, now: float) -> Optional[ContextWindow]:
    """The managed local server's window: /props when it answered, else its -c (not confirmed yet)."""
    try:
        from orchestrator.runtime.llama_server import managed_endpoint
        endpoint = managed_endpoint(client._state_path(""))
    except Exception:  # noqa: BLE001 - no state, no runtime: asked like any server below
        endpoint = None
    if endpoint is None:
        return None
    base = dict(provider=target.provider, model=target.model, checked_at=_now_iso(now), started=endpoint.ctx)
    if endpoint.n_ctx:
        confirmed = None if endpoint.ctx is None else endpoint.ctx == endpoint.n_ctx
        return ContextWindow(tokens=endpoint.n_ctx, source="local_server", confirmed=confirmed,
                             how="llama-server GET /props default_generation_settings.n_ctx", **base)
    if endpoint.ctx:
        return ContextWindow(tokens=endpoint.ctx, source="local_started", confirmed=None,
                             how="the -c of the start (server.json ctx)", **base)
    return None


def _apply_setting(window: ContextWindow, setting: Optional[int], told: bool) -> ContextWindow:
    """The person's ai_context_window: the window, but never above what a server told (`told`)."""
    if setting is None:
        return window
    if told and setting >= window.tokens:
        return window.model_copy(update={"note": f"your setting ai_context_window ({setting}) is above what the "
                                                 f"server reports; {window.tokens} is used"})
    return window.model_copy(update={"tokens": setting, "source": "setting",
                                     "how": f"ai_context_window = {setting}" + (f"; the server reports "
                                                                                 f"{window.tokens}" if told else ""),
                                     "note": ""})


def context_window(client: Any, task: str = "chat", *, refresh: bool = False, cached_only: bool = False,
                   now: Optional[float] = None, table: Optional[Dict[str, Any]] = None) -> ContextWindow:
    """The window of the model `client` (an LLMClient) uses for `task`, with its source. `refresh` asks the provider's
    server again instead of the cache; `cached_only` never asks a server (get_status: no network for a status
    read). Never raises: a provider that is not configured gets the default with the reason in `note`."""
    moment = time.time() if now is None else now
    table = load_table() if table is None else table
    setting = _setting(client)
    try:
        target = client.resolve(task)
    except Exception as e:  # noqa: BLE001 - not configured: the default, with why
        fallback = default_window(table)
        window = ContextWindow(tokens=fallback["tokens"], source="default", how=fallback["source"],
                               checked_at=_now_iso(moment), note=str(e)[:300])
        return _apply_setting(window, setting, told=False)
    provider, model = target.provider, target.model
    if target.preset.runtime_endpoint:
        local = _local(client, target, moment)
        if local is not None:
            return _apply_setting(local, setting, told=True)
    cache = cache_for(client)
    key = WindowCache.key(provider, target.base_url, model)
    if cache is not None and not refresh:
        entry = cache.get(key, moment)
        if entry is not None:
            window = ContextWindow(tokens=entry["tokens"], max_output=entry.get("max_output"), source="provider",
                                   how=str(entry.get("how") or ""), provider=provider, model=model,
                                   checked_at=str(entry.get("checked_at") or ""), cached=True)
            return _apply_setting(window, setting, told=True)
    if not cached_only:
        told = _ask(client, target)
        if told is not None:
            checked = _now_iso(moment)
            if cache is not None and told.cache:
                cache.put(key, {"tokens": told.tokens, "max_output": told.max_output, "how": told.how,
                                "at": moment, "checked_at": checked})
            window = ContextWindow(tokens=told.tokens, max_output=told.max_output, source="provider", how=told.how,
                                   provider=provider, model=model, checked_at=checked)
            return _apply_setting(window, setting, told=True)
    entry = pinned_window(provider, model, table)
    if entry is not None:
        window = ContextWindow(tokens=int(entry["tokens"]), max_output=entry.get("max_output"), source="table",
                               how=f"{entry.get('source')}, checked {entry.get('checked')}", provider=provider,
                               model=model, checked_at=str(entry.get("checked") or ""))
        return _apply_setting(window, setting, told=False)
    fallback = default_window(table)
    window = ContextWindow(tokens=fallback["tokens"], source="default", how=fallback["source"], provider=provider,
                           model=model, checked_at=_now_iso(moment))
    return _apply_setting(window, setting, told=False)


def _ask(client: Any, target: Any) -> Optional[ServerWindow]:
    """The provider's server, through the adapter (its SDK client: the same privacy filter, address pinning and
    offline rule as a call). None when it does not tell or cannot be reached."""
    try:
        adapter = client.adapter(target)
        asker = getattr(adapter, "context_window", None)
        told = asker(target.model) if callable(asker) else None
    except Exception as e:  # noqa: BLE001 - a server that cannot be asked: the table and the default decide
        logger.info(f"{target.provider}: the context window could not be asked ({e.__class__.__name__}).")
        return None
    if told is None or not isinstance(told.tokens, int) or told.tokens < 256:
        return None
    return told


def window_lines(window: ContextWindow) -> List[str]:
    """The window as lines for the terminal and the GUI box (plain text)."""
    lines = [f"Context window: {window.words()}"]
    if window.max_output:
        lines.append(f"Longest answer: {window.max_output} tokens")
    if window.source in ("local_server", "local_started") and window.started:
        state = ("confirmed by /props" if window.confirmed else "/props reports another number" if window.confirmed
                 is False else "not confirmed by /props yet")
        lines.append(f"Local server started with -c {window.started} ({state})")
    if window.cached:
        lines.append(f"Asked at {window.checked_at}; kept for 24 hours (Check again asks anew)")
    if window.note:
        lines.append(f"Note: {window.note}")
    return lines
