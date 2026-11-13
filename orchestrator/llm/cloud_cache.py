#!/usr/bin/env python3
"""
Cloud caching the provider's way (docs/DESIGN.md, Ditto, the row "Cloud caching follows each provider's own caching").

- `cache_way(preset, model)`: how the provider of a call caches. "breakpoints": Claude through Anthropic's API, cache
  points placed by the adapter as Anthropic's guide puts them; "openrouter_breakpoints": an `anthropic/...` model
  through OpenRouter, which passes `cache_control` on as it documents for Anthropic; "automatic": the provider caches
  a stable prefix by itself (OpenAI, Google, every other OpenRouter upstream) and nothing is sent for it; "none": a
  local engine or a custom server (no cloud cache).
- The lifetime `ai_cache_ttl` ("5m", the default, or "1h") and the soft cap `ai_cache_cost_limit` (US dollars of
  estimated cache writes per conversation; 0 turns the notice off), both the person's.
- `write_cost(usage, model)`: the estimated cost of a call's cache writes from the pinned table configs/cloud_cache.yml,
  None for a model without a price there (never guessed).
- `CacheTally`: the conversation's sums, kept in chat_history.json beside the messages; `cap_crossed` says when a turn
  crossed the limit, which is told once per conversation and never turns caching off.

Qt-free; no network.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

TABLE_FILE = Path(__file__).resolve().parents[2] / "configs" / "cloud_cache.yml"
TTLS = ("5m", "1h")
DEFAULT_TTL = "5m"
DEFAULT_COST_LIMIT = 1.0
MAX_BREAKPOINTS = 4          # Anthropic: at most four cache breakpoints per request (the table repeats it)
TTL_SETTING = "ai_cache_ttl"
LIMIT_SETTING = "ai_cache_cost_limit"
WAYS = ("breakpoints", "openrouter_breakpoints", "automatic", "none")

_table: Optional[Dict[str, Any]] = None


def load_table(path: Optional[Path] = None) -> Dict[str, Any]:
    """configs/cloud_cache.yml as data ({} when it cannot be read); the default file is read once."""
    global _table
    if path is None and _table is not None:
        return _table
    try:
        data = yaml.safe_load(Path(path or TABLE_FILE).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        logger.warning(f"The cloud cache table cannot be read ({e.__class__.__name__}).")
        data = {}
    data = data if isinstance(data, dict) else {}
    if path is None:
        _table = data
    return data


def claude_id(model: str) -> str:
    """A Claude model id as Anthropic names it: OpenRouter's `anthropic/claude-opus-5.5` is `claude-opus-5-5`."""
    name = str(model or "").strip().lower()
    if name.startswith("anthropic/"):
        name = name[len("anthropic/"):].replace(".", "-")
    return name


def claude_entry(model: str, table: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """The table's entry for a Claude model: the exact id, a dated snapshot of it (`<id>-2026...`), or a prefix
    entry ending in *; None when the table does not name it."""
    table = load_table() if table is None else table
    name = claude_id(model)
    entries = [e for e in table.get("anthropic") or [] if isinstance(e, dict) and e.get("model")]
    for entry in entries:
        key = str(entry["model"])
        if not key.endswith("*") and (name == key or name.startswith(key + "-20")):
            return entry
    for entry in entries:
        key = str(entry["model"])
        if key.endswith("*") and name.startswith(key[:-1]):
            return entry
    return None


def min_tokens(model: str, table: Optional[Dict[str, Any]] = None) -> int:
    """The minimum cacheable prompt length of a Claude model (shorter prompts are not cached, without an error)."""
    table = load_table() if table is None else table
    entry = claude_entry(model, table)
    if entry is not None and isinstance(entry.get("min_tokens"), int):
        return int(entry["min_tokens"])
    default = (table.get("default") or {}).get("min_tokens")
    return int(default) if isinstance(default, int) else 512


def is_claude_model(model: str) -> bool:
    return claude_id(model).startswith("claude")


def cache_way(preset: Any, model: str) -> str:
    """How the provider of `preset` caches for `model` (one of WAYS)."""
    declared = str(getattr(preset, "prompt_caching", "") or "none")
    if declared == "openrouter":
        return "openrouter_breakpoints" if str(model or "").lower().startswith("anthropic/") else "automatic"
    return declared if declared in WAYS else "none"


def sends_breakpoints(way: str) -> bool:
    return way in ("breakpoints", "openrouter_breakpoints")


# --- the person's settings ------------------------------------------------------------------------------------------

def _read(config: Any, key: str, default: Any) -> Any:
    try:
        getter = getattr(config, "get", None)
        return getter(key, default) if callable(getter) else default
    except Exception:  # noqa: BLE001 - a broken config reads as the default
        return default


def ttl_setting(config: Any) -> str:
    """ai_cache_ttl: "5m" (the default) or "1h"; anything else is the default."""
    value = str(_read(config, TTL_SETTING, DEFAULT_TTL) or DEFAULT_TTL).strip().lower()
    return value if value in TTLS else DEFAULT_TTL


def cost_limit(config: Any) -> float:
    """ai_cache_cost_limit in US dollars (0: no notice); a value that is no number is the default."""
    try:
        value = float(_read(config, LIMIT_SETTING, DEFAULT_COST_LIMIT))
    except (TypeError, ValueError):
        return DEFAULT_COST_LIMIT
    return max(0.0, value)


def set_cache_settings(config: Any, ttl: Optional[str] = None, limit: Optional[float] = None, *,
                       save: bool = True) -> bool:
    """The one writer of ai_cache_ttl and ai_cache_cost_limit (`llm-cli ditto context cache`, `llmcf config set`, the
    Context window box): checked first (ValueError, nothing changes), then set and config.yml saved with `save`.
    Returns False when it could not be saved (the values hold for this run). The person's alone: no AI tool calls it."""
    changes: Dict[str, Any] = {}
    if ttl is not None:
        word = str(ttl).strip().lower()
        if word not in TTLS:
            raise ValueError(f"{TTL_SETTING} is 5m or 1h, not '{ttl}'.")
        changes[TTL_SETTING] = word
    if limit is not None:
        try:
            value = float(limit)
        except (TypeError, ValueError):
            raise ValueError(f"{LIMIT_SETTING} is a number of US dollars (0: no notice), not '{limit}'.") from None
        if not (0 <= value <= 100000) or value != value:
            raise ValueError(f"{LIMIT_SETTING} is between 0 and 100000 US dollars, not '{limit}'.")
        changes[LIMIT_SETTING] = round(value, 4)
    for key, value in changes.items():
        if config.set(key, value) is False:
            raise ValueError(f"{key} cannot be '{value}'.")
    saver = getattr(config, "save_user_config", None)
    return bool(saver()) if changes and save and callable(saver) else True


# --- cost -----------------------------------------------------------------------------------------------------------

def write_cost(usage: Any, model: str, table: Optional[Dict[str, Any]] = None) -> Optional[float]:
    """The estimated US-dollar cost of a call's cache writes (the 1-hour part at its own price), 0.0 without writes,
    None when a write happened for a model the table has no price for."""
    written = int(getattr(usage, "cache_write_tokens", 0) or 0)
    if written <= 0:
        return 0.0
    entry = claude_entry(model, table) if is_claude_model(model) else None
    if entry is None or not isinstance(entry.get("write_5m"), (int, float)):
        return None
    hour = min(written, int(getattr(usage, "cache_write_1h_tokens", 0) or 0))
    prompt = (int(getattr(usage, "input_tokens", 0) or 0) + int(getattr(usage, "cache_read_tokens", 0) or 0)
              + written)
    five, one = float(entry["write_5m"]), float(entry.get("write_1h", entry["write_5m"] * 1.6))
    long_over = entry.get("long_over")
    if isinstance(long_over, int) and prompt > long_over:
        five = float(entry.get("long_write_5m", five))
        one = float(entry.get("long_write_1h", one))
    return ((written - hour) * five + hour * one) / 1_000_000


class CacheTally(BaseModel):
    """The cache numbers of one conversation (kept in chat_history.json as `cache`)."""
    calls: int = 0
    uncached: int = 0
    read: int = 0
    written: int = 0
    written_1h: int = 0
    cost_usd: float = 0.0             # estimated cost of the priced cache writes
    unpriced_written: int = 0         # written tokens of models the table has no price for
    cap_told: Optional[float] = None  # the limit the person was told about in this conversation

    @classmethod
    def read_from(cls, data: Any) -> "CacheTally":
        try:
            return cls.model_validate(data) if isinstance(data, dict) else cls()
        except Exception:  # noqa: BLE001 - a damaged record starts at zero
            return cls()

    def plus(self, usage: Any, model: str, table: Optional[Dict[str, Any]] = None) -> "CacheTally":
        """These sums with one more call's usage."""
        written = int(getattr(usage, "cache_write_tokens", 0) or 0)
        cost = write_cost(usage, model, table)
        return self.model_copy(update={
            "calls": self.calls + 1,
            "uncached": self.uncached + int(getattr(usage, "input_tokens", 0) or 0),
            "read": self.read + int(getattr(usage, "cache_read_tokens", 0) or 0),
            "written": self.written + written,
            "written_1h": self.written_1h + int(getattr(usage, "cache_write_1h_tokens", 0) or 0),
            "cost_usd": round(self.cost_usd + (cost or 0.0), 6),
            "unpriced_written": self.unpriced_written + (written if cost is None else 0)})

    def read_share(self) -> int:
        total = self.uncached + self.read + self.written
        return round(100 * self.read / total) if total else 0


def cap_crossed(before: CacheTally, after: CacheTally, limit: float) -> bool:
    """Whether this turn crossed the soft cap: a limit above 0, reached now, and not told yet at this limit."""
    if limit <= 0 or after.cost_usd < limit:
        return False
    return before.cap_told != limit


def dollars(value: float) -> str:
    return f"{value:.2f}" if value >= 0.01 or value == 0 else f"{value:.4f}"


def turn_lines(way: str, turn: CacheTally, total: CacheTally, limit: float, crossed: bool,
               model: str = "") -> List[Tuple[str, Dict[str, str]]]:
    """The did lines of a turn for the Ditto console (text keys of Core/chat_feed.py with closed parameters): the
    turn's cache numbers where the provider caches, and the soft cap's line in the turn that crossed it."""
    lines: List[Tuple[str, Dict[str, str]]] = []
    if way == "none" or turn.calls == 0:
        return lines
    if turn.read or turn.written:
        lines.append(("activity.cache.turn", {"read": str(turn.read), "written": str(turn.written),
                                              "uncached": str(turn.uncached), "share": str(turn.read_share())}))
    elif turn.uncached:
        key = "activity.cache.none_breakpoints" if sends_breakpoints(way) else "activity.cache.none_automatic"
        lines.append((key, {"uncached": str(turn.uncached),
                            "minimum": str(min_tokens(model)) if sends_breakpoints(way) else "1024"}))
    if crossed:
        lines.append(("activity.cache.cap", {"cost": dollars(total.cost_usd), "limit": dollars(limit),
                                             "written": str(total.written)}))
    return lines


WAY_WORDS = {
    "breakpoints": "Anthropic's prompt caching: cache points on the system text and the conversation's newest block "
                   "(two of four)",
    "openrouter_breakpoints": "OpenRouter passes Anthropic's cache points on: the system text and the conversation's "
                              "newest block",
    "automatic": "the provider caches a stable prefix by itself; nothing is sent for it",
    "none": "no cloud cache (a local engine keeps its own prompt cache)",
}


class CacheView(BaseModel):
    """What `llm-cli ditto context`, the Context window box and get_status show about the cloud cache."""
    way: str = "none"
    words: str = ""
    ttl: str = DEFAULT_TTL
    ttl_applies: bool = False
    cost_limit: float = DEFAULT_COST_LIMIT
    min_tokens: Optional[int] = None
    totals: CacheTally = Field(default_factory=CacheTally)
    cap_reached: bool = False

    def lines(self) -> List[str]:
        out = [f"Cloud cache: {self.words}"]
        if self.ttl_applies:
            out.append(f"  Lifetime: {self.ttl} (ai_cache_ttl; 5m or 1h)")
        else:
            out.append(f"  Lifetime: set by the provider (ai_cache_ttl = {self.ttl} applies to Claude only)")
        if self.min_tokens:
            out.append(f"  Minimum cacheable prompt of this model: {self.min_tokens} tokens")
        limit = (f"{dollars(self.cost_limit)} USD of cache writes per conversation (ai_cache_cost_limit; a notice, "
                 "caching goes on)" if self.cost_limit > 0 else "off (ai_cache_cost_limit = 0)")
        out.append(f"  Soft cap: {limit}")
        t = self.totals
        priced = f"about {dollars(t.cost_usd)} USD" + (f", {t.unpriced_written} written token(s) without a price"
                                                         if t.unpriced_written else "")
        out.append(f"  This conversation: {t.calls} call(s), {t.read} token(s) read from the cache, {t.written} "
                   f"written ({t.written_1h} for 1 hour), {t.uncached} not cached; cache writes {priced}"
                   + ("; the soft cap is reached" if self.cap_reached else ""))
        return out


def cache_view(preset: Any, model: str, config: Any, totals: Optional[CacheTally] = None) -> CacheView:
    way = cache_way(preset, model) if preset is not None else "none"
    totals = totals or CacheTally()
    limit = cost_limit(config)
    return CacheView(way=way, words=WAY_WORDS[way], ttl=ttl_setting(config), ttl_applies=sends_breakpoints(way),
                     cost_limit=limit, min_tokens=min_tokens(model) if sends_breakpoints(way) else None,
                     totals=totals, cap_reached=bool(limit > 0 and totals.cost_usd >= limit))
