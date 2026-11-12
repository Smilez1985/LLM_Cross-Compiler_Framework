#!/usr/bin/env python3
"""
Ditto's context as the person reads it (docs/DESIGN.md, Ditto, the rows from "The real context window" to "A
conversation that crosses the threshold"): the window of the model the chat uses, how tokens are counted for it, the
saved conversation measured against the turn budget, and the compactions kept with their originals. One overview for
`llm-cli ditto context` and the GUI's Settings > Language model > Context window; Qt-free.

Reading the window may ask the provider's server (a network request; `refresh` asks again instead of the 24-hour
cache), so the GUI calls `context_overview` in a worker thread. Showing an original and undoing a compaction work on
the conversation file (Core/ditto_manager.ChatHistory), never on the feed.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from orchestrator.Core.ditto_manager import ChatHistory, render_transcript


class CompactionRow(BaseModel):
    id: str
    at: str = ""
    reason: str = "long"
    summarized: int = 0
    kept: int = 0
    window: Optional[int] = None
    before: Optional[int] = None
    after: Optional[int] = None


class ContextOverview(BaseModel):
    window: Dict[str, Any]
    counting: str = ""
    family: str = ""
    calibration: Dict[str, Any] = Field(default_factory=dict)
    messages: int = 0
    budget: Dict[str, Any] = Field(default_factory=dict)
    budget_lines: List[str] = Field(default_factory=list)
    compactions: List[CompactionRow] = Field(default_factory=list)

    def data(self) -> Dict[str, Any]:
        return self.model_dump()

    def lines(self) -> List[str]:
        """The overview in plain English lines (the terminal)."""
        from orchestrator.llm.context_window import ContextWindow, window_lines
        out = window_lines(ContextWindow(**self.window))
        out.append(f"Counting: {self.counting}")
        out.append(f"Saved conversation: {self.messages} message(s)")
        out.extend(self.budget_lines)
        if self.compactions:
            out.append("Compactions (newest last; the original of each is kept):")
            for row in self.compactions:
                numbers = f", about {row.before} -> {row.after} tokens" if row.before and row.after else ""
                out.append(f"  {row.id} at {row.at}: {row.summarized} message(s) summarized, {row.kept} kept "
                           f"({row.reason}{numbers})")
        else:
            out.append("Compactions: none")
        return out


def context_overview(ditto: Any, history: ChatHistory, *, refresh: bool = False) -> ContextOverview:
    """The overview of `ditto`'s chat model and the saved conversation `history`."""
    from orchestrator.llm import token_count
    from orchestrator.llm.context_budget import plan_turn, turn_groups
    window = ditto.context_window(refresh=refresh)
    client = ditto.client
    try:
        calibration = client.token_calibration().get(window.provider, window.model)
    except Exception:  # noqa: BLE001
        calibration = {"factor": 1.0, "answers": 0}
    messages = list(history.messages)
    groups = turn_groups(messages)
    tally = token_count.tally(window.provider, window.model, system="", tools=(), question=(),
                              history=[messages[a:b] for a, b in groups], calibration=calibration)
    limit = ditto._context_limit() if hasattr(ditto, "_context_limit") else None
    budget = plan_turn(window.tokens, fixed=0, history=tally.history, max_output=window.max_output, limit=limit,
                       method=tally.detail)
    rows = [CompactionRow(id=str(r.get("id") or "?"), at=str(r.get("at") or ""), reason=str(r.get("reason") or
                                                                                            "long"),
                          summarized=int(r.get("summarized") or len(r.get("messages") or [])),
                          kept=int(r.get("kept") or 0), window=r.get("window"), before=r.get("before"),
                          after=r.get("after"))
            for r in history.compactions]
    return ContextOverview(window=window.data(), counting=tally.detail, family=tally.family,
                           calibration=dict(calibration), messages=len(messages),
                           budget=budget.model_dump(), budget_lines=budget.lines()[1:], compactions=rows)


def compaction_record(history: ChatHistory, compaction_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The record of `compaction_id`, or the newest one."""
    if not history.compactions:
        return None
    if compaction_id is None:
        return history.compactions[-1]
    return next((r for r in history.compactions if str(r.get("id")) == compaction_id), None)


def original_text(history: ChatHistory, compaction_id: Optional[str] = None) -> Optional[str]:
    """The original conversation a compaction kept, as plain text (tool results shortened), with its summary."""
    record = compaction_record(history, compaction_id)
    if record is None:
        return None
    transcript = render_transcript(history.compaction_messages(record))
    return (f"Compaction {record.get('id')} at {record.get('at')} ({record.get('reason')})\n\n"
            f"Summary Ditto continued with:\n{record.get('summary') or '-'}\n\nOriginal conversation:\n{transcript}")
