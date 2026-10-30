#!/usr/bin/env python3
"""
Every build the person can see, wherever it was started: the one query behind `llm-cli build status` and the GUI's
Build > Build History... window.

Two sources, both written by every framework process (the GUI, the CLI, an MCP server): the build records the build
engines keep in the cache folder (build_records.py: one per attempt, with its output folder and error) and the
request summaries of the shared build state in the per-user folder (request_store.py: queued requests, requests that
ended before a build ran, the program that started them). The fix proposals AI clients made for a failed build come
from the per-user folder too (client_proposals.py). Nothing here starts, stops or changes a build, and nothing is
sent anywhere: the values are shown to the person (full paths included).
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# The fields of a request summary and of a build record, in the order they are shown; each front end labels them
# (the CLI in English, the GUI in the person's language).
REQUEST_FIELDS = ("request", "status", "end", "stage", "progress", "model", "target", "board", "format",
                  "quantization", "optimization", "owner", "where", "started", "ended")
RECORD_FIELDS = ("build", "request", "status", "end", "stage", "progress", "model", "target", "board", "format",
                 "quantization", "output", "statistics", "started", "ended", "process", "error")


def end_of(record: Dict[str, Any]) -> Optional[str]:
    """A summary's or build record's end reason (Core/build_end.py END_REASONS), None for anything else."""
    from orchestrator.Core.build_end import END_REASONS
    value = record.get("end_reason")
    return value if value in END_REASONS else None


def shared_requests(config: Any) -> List[Dict[str, Any]]:
    """The build request summaries every framework process writes into the per-user folder (request_store)."""
    from orchestrator.Core.request_store import for_config
    try:
        store = for_config(config)
        return store.read() if store is not None else []
    except OSError:
        return []


def client_proposals(config: Any, request_id: Optional[str] = None) -> list:
    """The fix proposals AI clients made (propose_healing_fix), kept in the per-user folder."""
    from orchestrator.Core.client_proposals import ClientProposalStore
    state_dir = getattr(config, "user_state_dir", None)
    return ClientProposalStore(Path(state_dir)).list(request_id) if state_dir else []


def _summary(request: Dict[str, Any]) -> Dict[str, Any]:
    return request.get("summary") if isinstance(request.get("summary"), dict) else {}


def request_rows(request: Dict[str, Any]) -> List[Tuple[str, Any]]:
    """(field, value) of a request summary in REQUEST_FIELDS order; empty values included (the front end skips
    them; the CLI shows an empty owner as "the person")."""
    summary = _summary(request)
    values = {"request": request.get("request_id"), "status": request.get("status"), "end": end_of(request),
              "stage": request.get("stage"),
              "progress": f"{request.get('progress') or 0}%", "model": summary.get("model"),
              "target": summary.get("target"), "board": summary.get("board"), "format": summary.get("format"),
              "quantization": summary.get("quantization"), "optimization": summary.get("optimization"),
              "owner": request.get("owner"), "where": request.get("program"), "started": request.get("started"),
              "ended": request.get("ended")}
    return [(name, values[name]) for name in REQUEST_FIELDS]


def statistics_text(output_dir: Any) -> str:
    """The convert statistics of a finished build in one line (its build_info.json: how long the conversion and the
    quantization ran, the size before and after; DECISIONS round 25 Q8), "" when the build wrote none."""
    import json
    from orchestrator.Core.deletion import size_text
    if not output_dir:
        return ""
    try:
        info = json.loads((Path(str(output_dir)) / "build_info.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    conversion = info.get("conversion") if isinstance(info, dict) else None
    conversion = conversion if isinstance(conversion, dict) else {}

    def number(value: Any) -> Optional[int]:
        return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None
    parts = []
    seconds, before, after = (number(conversion.get(k)) for k in ("seconds", "input_bytes", "output_bytes"))
    if seconds is not None:
        parts.append(f"convert {seconds} s")
    if before and after:
        parts.append(f"{size_text(before)} to {size_text(after)}")
    quantize = number(info.get("quantize_seconds")) if isinstance(info, dict) else None
    if quantize is not None:
        parts.append(f"quantize {quantize} s")
    return ", ".join(parts)


def record_rows(record: Dict[str, Any]) -> List[Tuple[str, Any]]:
    """(field, value) of one build attempt (a build record) in RECORD_FIELDS order; empty values included."""
    values = {"build": record.get("build_id"), "request": record.get("request_id"), "status": record.get("status"),
              "end": end_of(record),
              "stage": record.get("stage"), "progress": f"{record.get('progress') or 0}%",
              "model": record.get("model"), "target": record.get("target"), "board": record.get("board"),
              "format": record.get("format"), "quantization": record.get("quantization"),
              "output": record.get("output_dir"), "statistics": statistics_text(record.get("output_dir")),
              "started": record.get("started"), "ended": record.get("ended"),
              "process": record.get("pid"), "error": record.get("error")}
    return [(name, values[name]) for name in RECORD_FIELDS]


@dataclass
class BuildRow:
    """One line of the build list: a build attempt (kind "build", from its record) or a request that has no build
    record yet (kind "request": queued, preparing, or ended before a build ran)."""
    kind: str
    data: Dict[str, Any]
    where: str = "-"   # the program that started it (GUI, CLI, MCP ...), when the shared build state names it

    @property
    def request_id(self) -> str:
        return str(self.data.get("request_id") or "-")

    @property
    def build_id(self) -> str:
        return "-" if self.kind == "request" else str(self.data.get("build_id"))

    def values(self) -> Dict[str, str]:
        """The list columns as text: request, build, status, progress, target, format, quantization, model, started,
        where."""
        source = _summary(self.data) if self.kind == "request" else self.data
        return {"request": str(self.data.get("request_id") if self.kind == "request"
                               else self.data.get("request_id") or "-"),
                "build": self.build_id, "status": str(self.data.get("status")),
                "progress": f"{self.data.get('progress') or 0}%",
                "target": str(source.get("target") or "-"), "format": str(source.get("format") or "-"),
                "quantization": str(source.get("quantization") or "-"), "model": str(source.get("model") or "-"),
                "started": str(self.data.get("started") or "-"), "where": str(self.where or "-")}


@dataclass
class BuildDetails:
    """Everything known about one build request or build id: its request summary (when the shared build state has
    one), every attempt in order (oldest first), and the request ids whose AI fix proposals belong to it."""
    asked: str
    request: Optional[Dict[str, Any]] = None
    attempts: List[Dict[str, Any]] = field(default_factory=list)
    proposal_requests: List[str] = field(default_factory=list)

    @property
    def interrupted(self) -> bool:
        return bool(self.request) and self.request.get("status") == "interrupted"

    @property
    def awaiting_decision(self) -> bool:
        return bool(self.request) and not self.interrupted and bool(self.request.get("awaiting_decision"))


@dataclass
class BuildHistory:
    """The build records and the shared request summaries, read once."""
    folder: Path                                   # the build records folder (cache folder)
    records: List[Dict[str, Any]] = field(default_factory=list)
    requests: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def by_request(self) -> Dict[Any, Dict[str, Any]]:
        return {r.get("request_id"): r for r in self.requests}

    @property
    def waiting(self) -> List[Dict[str, Any]]:
        """Requests without a build record yet (queued, preparing, or ended before a build ran)."""
        with_builds = {r.get("request_id") for r in self.records}
        return [r for r in self.requests if r.get("request_id") not in with_builds]

    def rows(self) -> List[BuildRow]:
        """Every build attempt and every request without one, newest first (by start time)."""
        by_request = self.by_request
        rows = [BuildRow("build", r, (by_request.get(r.get("request_id")) or {}).get("program") or "-")
                for r in self.records]
        rows += [BuildRow("request", r, r.get("program") or "-") for r in self.waiting]
        return sorted(rows, key=lambda row: str(row.data.get("started") or ""), reverse=True)

    def details(self, request_id: str) -> Optional[BuildDetails]:
        """The details of a request id (req_...) or a build id; None when neither source knows it."""
        matches = [r for r in self.records if request_id in (r.get("request_id"), r.get("build_id"))]
        request = self.by_request.get(request_id)
        if not matches and request is None:
            return None
        ids = list(dict.fromkeys([r.get("request_id") for r in matches if r.get("request_id")]
                                 + ([request_id] if request is not None else [])))
        return BuildDetails(request_id, request, list(reversed(matches)), ids)


def read_history(config: Any, resolve: Callable[[Any], Any]) -> BuildHistory:
    """The build history of this installation and user: `config` the settings (cache_dir, the per-user folder),
    `resolve` their path rule (a relative cache_dir is relative to the installation). Needs neither a keyring nor
    Docker."""
    from orchestrator.Core.build_records import read_records, records_dir
    folder = records_dir(resolve(config.get("cache_dir", "cache")))
    return BuildHistory(Path(folder), read_records(folder), shared_requests(config))


__all__ = ["BuildDetails", "BuildHistory", "BuildRow", "RECORD_FIELDS", "REQUEST_FIELDS", "client_proposals",
           "statistics_text",
           "read_history", "record_rows", "request_rows", "shared_requests"]
