#!/usr/bin/env python3
"""
Counting the tokens of a request (docs/DESIGN.md, Ditto, the row "Tokens are counted by the model's own tokenizer").

* The estimate: the characters of a request's system text, tool definitions (JSON) and messages, divided by the
  characters per token of the model's family (the `estimates:` table of configs/context_windows.yml, measured with
  scripts/measure_token_ratio.py; the smallest ratio of each family, so it does not count too few on those kinds).
* Calibration: after every answer the provider's own input tokens are compared with the estimate of the same request
  (LLMClient records them, `Calibration.record`); the factor per provider and model multiplies later estimates.
* Exact counts only when the calibrated estimate reaches half of the window: llama.cpp's /apply-template and /tokenize,
  Anthropic's free messages.count_tokens (LLMClient.count_input_tokens, through the privacy filter).

Every result says how it was made (TokenTally.method), never silently.
"""

import json
import math
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from pydantic import BaseModel

from orchestrator.llm.context_window import load_table
from orchestrator.utils.logging import get_logger

logger = get_logger("TokenCount")

CALIBRATION_FILE = "token_calibration.json"
WEIGHT = 0.3                 # the newest answer's weight in the moving average
FACTOR_RANGE = (0.5, 2.0)    # a calibration factor stays inside
EXACT_SHARE = 0.5            # an exact count is asked when the estimate reaches this share of the window
FALLBACK_RATIO = 2.5
# Providers whose own count is free and asked when it matters (OpenAI's stays with the paid measurement).
EXACT_PROVIDERS = ("anthropic",)


class Family(BaseModel):
    family: str
    chars_per_token: float
    max_overestimate_pct: Optional[float] = None
    source: str = ""


def _families(table: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    table = load_table() if table is None else table
    estimates = table.get("estimates") if isinstance(table.get("estimates"), dict) else {}
    families = estimates.get("families") if isinstance(estimates.get("families"), list) else []
    return [f for f in families if isinstance(f, dict)]


def family_of(model: str, table: Optional[Dict[str, Any]] = None) -> Family:
    """The family of `model` by the words of its id, or the fallback (the table's smallest ratio)."""
    table = load_table() if table is None else table
    name = str(model or "").lower()
    for entry in _families(table):
        if any(str(word).lower() in name for word in entry.get("match") or []):
            return Family(family=str(entry.get("family")), chars_per_token=float(entry["chars_per_token"]),
                          max_overestimate_pct=entry.get("max_overestimate_pct"), source=str(entry.get("source", "")))
    estimates = table.get("estimates") if isinstance(table.get("estimates"), dict) else {}
    fallback = estimates.get("fallback") if isinstance(estimates.get("fallback"), dict) else {}
    return Family(family="other", chars_per_token=float(fallback.get("chars_per_token") or FALLBACK_RATIO),
                  source=str(fallback.get("source") or ""))


def estimate(text: str, family: Family) -> int:
    """Tokens of `text` by the family's characters per token (rounded up)."""
    return int(math.ceil(len(text or "") / family.chars_per_token)) if text else 0


# --- the parts of a request as text ----------------------------------------------------------------------------------

def message_text(message: Any) -> str:
    """One message as the characters it sends: its text, its tool calls' names and arguments (JSON)."""
    parts = [message.text() if hasattr(message, "text") else str(message)]
    for call in getattr(message, "tool_calls", None) or []:
        parts.append(f"{call.name} {json.dumps(call.arguments, ensure_ascii=False, sort_keys=True, default=str)}")
    return "\n".join(p for p in parts if p)


def tools_text(tools: Iterable[Any]) -> str:
    out = []
    for tool in tools or ():
        out.append(json.dumps({"name": getattr(tool, "name", ""), "description": getattr(tool, "description", ""),
                               "parameters": getattr(tool, "input_schema", {})}, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"), default=str))
    return "\n".join(out)


def request_text(request: Any) -> str:
    """Everything of a request that is sent as input, as one text (for the estimate)."""
    parts = [getattr(request, "system", "") or "", tools_text(getattr(request, "tools", None) or ())]
    parts.extend(message_text(m) for m in getattr(request, "messages", None) or ())
    return "\n".join(p for p in parts if p)


# --- calibration -----------------------------------------------------------------------------------------------------

class Calibration:
    """The factor per provider and model by which the estimate missed the provider's own input tokens (a moving
    average, the newest answer weighted WEIGHT), with the number of answers and the last error in percent. Kept in
    `path` (mode 0600) when given, else in memory only."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._memory: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def key(provider: str, model: str) -> str:
        return f"{provider}:{model}"

    def _read(self) -> Dict[str, Dict[str, Any]]:
        if self.path is None:
            return dict(self._memory)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: Dict[str, Dict[str, Any]]) -> None:
        if self.path is None:
            self._memory = data
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=f".{self.path.name}.")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, sort_keys=True)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except OSError as e:
            logger.warning(f"Could not keep the token calibration ({e.__class__.__name__}).")

    def get(self, provider: str, model: str) -> Dict[str, Any]:
        entry = self._read().get(self.key(provider, model))
        if not isinstance(entry, dict) or not isinstance(entry.get("factor"), (int, float)):
            return {"factor": 1.0, "answers": 0, "last_error_pct": None}
        return entry

    def record(self, provider: str, model: str, estimated: int, actual: int) -> Optional[Dict[str, Any]]:
        """One answer: the provider counted `actual` input tokens for a request estimated at `estimated`."""
        if estimated <= 0 or actual <= 0:
            return None
        with self._lock:
            data = self._read()
            key = self.key(provider, model)
            old = data.get(key) if isinstance(data.get(key), dict) else {}
            ratio = actual / estimated
            answers = int(old.get("answers") or 0)
            factor = ratio if answers == 0 else (1 - WEIGHT) * float(old.get("factor") or 1.0) + WEIGHT * ratio
            low, high = FACTOR_RANGE
            calibrated = estimated * float(old.get("factor") or 1.0) if answers else estimated
            entry = {"factor": round(min(high, max(low, factor)), 4), "answers": answers + 1,
                     "last_error_pct": round(100 * (calibrated - actual) / actual, 1)}
            data[key] = entry
            self._write(data)
            return entry


def calibration_for(client: Any) -> Calibration:
    """The client's calibration store (its state folder), or one in memory."""
    existing = getattr(client, "calibration", None)
    if isinstance(existing, Calibration):
        return existing
    state_path = getattr(client, "_state_path", None)
    try:
        return Calibration(Path(state_path("llm")) / CALIBRATION_FILE) if callable(state_path) else Calibration()
    except Exception:  # noqa: BLE001
        return Calibration()


# --- the tally of one request ----------------------------------------------------------------------------------------

class TokenTally(BaseModel):
    """The tokens of a request's parts and their sum, with how they were counted. `method`: tokenizer, provider count,
    or estimate; `detail`: the words the person reads (e.g. "estimate (calibrated on 4 answers, last error 3.1 %)")."""
    system: int = 0
    tools: int = 0
    question: int = 0
    history: List[int] = []           # one number per earlier turn (context_budget.turn_groups)
    excerpts: int = 0
    total: int = 0
    method: str = "estimate"
    detail: str = ""
    family: str = ""


def method_words(method: str, calibration: Dict[str, Any], family: Family) -> str:
    if method != "estimate":
        return method
    answers = int(calibration.get("answers") or 0)
    if not answers:
        return f"estimate ({family.family}, {family.chars_per_token} characters per token; not calibrated yet)"
    error = calibration.get("last_error_pct")
    return (f"estimate ({family.family}, calibrated on {answers} answer{'s' if answers != 1 else ''}"
            + (f", last error {error} %" if error is not None else "") + ")")


def tally(provider: str, model: str, *, system: str, tools: Sequence[Any], question: Sequence[Any],
          history: Sequence[Sequence[Any]], excerpts: str = "", calibration: Optional[Dict[str, Any]] = None,
          table: Optional[Dict[str, Any]] = None) -> TokenTally:
    """The calibrated estimate of each part: `question` the new turn's messages, `history` the earlier turns (each a
    list of messages)."""
    family = family_of(model, table)
    calibration = calibration or {"factor": 1.0, "answers": 0}
    factor = float(calibration.get("factor") or 1.0)

    def count(text: str) -> int:
        return int(math.ceil(estimate(text, family) * factor)) if text else 0

    parts = TokenTally(system=count(system), tools=count(tools_text(tools)),
                       question=sum(count(message_text(m)) for m in question),
                       history=[sum(count(message_text(m)) for m in turn) for turn in history],
                       excerpts=count(excerpts), family=family.family)
    parts.total = parts.system + parts.tools + parts.question + sum(parts.history) + parts.excerpts
    parts.detail = method_words("estimate", calibration, family)
    return parts


def exact_total(client: Any, request: Any, provider: str) -> Optional[Tuple[int, str]]:
    """(tokens, method) of `request` where an exact count costs nothing: llama.cpp (the managed local server, a
    llama.cpp server behind a local engine's address: "tokenizer") and Anthropic ("provider count"). None where there
    is no such count or it failed."""
    counter = getattr(client, "count_input_tokens", None)
    if not callable(counter):
        return None
    try:
        target = client.resolve(getattr(request, "task", "chat"), model=getattr(request, "model", None))
    except Exception:  # noqa: BLE001
        return None
    if provider not in EXACT_PROVIDERS and target.preset.adapter != "openai_compat":
        return None
    if target.preset.adapter == "openai_compat" and not (target.preset.runtime_endpoint
                                                         or target.preset.category == "local_engine"):
        return None
    try:
        tokens = int(counter(request).input_tokens)
    except Exception as e:  # noqa: BLE001 - no count here: the estimate stays, and says so
        logger.info(f"{provider}: no exact token count ({e.__class__.__name__}).")
        return None
    return tokens, ("provider count" if target.preset.adapter == "anthropic" else "tokenizer")


def scaled(parts: TokenTally, exact: int, method: str) -> TokenTally:
    """The parts scaled so they sum to the exact count (each part keeps its share)."""
    if parts.total <= 0 or exact <= 0:
        return parts.model_copy(update={"method": method, "detail": method})
    share = exact / parts.total
    history = [int(math.ceil(h * share)) for h in parts.history]
    out = parts.model_copy(update={"system": int(math.ceil(parts.system * share)),
                                   "tools": int(math.ceil(parts.tools * share)),
                                   "question": int(math.ceil(parts.question * share)), "history": history,
                                   "excerpts": int(math.ceil(parts.excerpts * share)), "method": method,
                                   "detail": method})
    out.total = exact
    return out
