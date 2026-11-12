#!/usr/bin/env python3
"""
The budget of one turn (docs/DESIGN.md, Ditto, the row "Every turn fits the window by explicit rules"). Pure functions
on token numbers, so the same numbers always decide the same way and tests need no model:

R1  the answer keeps the smaller of ANSWER_TOKENS, the model's maximum output when known and a quarter of the window,
    at least MIN_ANSWER;
R2  a margin of MARGIN_SHARE of the window for the counting's error;
R3  system text, tool definitions and the new question must fit next to them, else the turn is refused (fits False);
R4  the rest is the room for excerpts and history, at most the person's chat_context_limit when one is set;
R5  knowledge excerpts take at most a third of the room and at most EXCERPT_TOKENS;
R6  when the earlier turns need more than COMPACT_AT of what is left, they are compacted: the newest whole turns that
    fit into KEEP_SHARE of it stay word for word, the older ones are summarized (in at most the smaller of
    SUMMARY_TOKENS and a quarter of it); when not even the newest turn fits, all are summarized;
R7  (decided by the caller) a conversation with tool turns asked without tools is summarized whole.

`turn_groups` cuts a conversation into turns: a person's message with everything up to the next one, so a tool call
never loses its result.
"""

from typing import Any, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

ANSWER_TOKENS = 16_000      # the adapters' answer length (providers/base.DEFAULT_MAX_TOKENS)
MIN_ANSWER = 256
MARGIN_SHARE = 0.05
EXCERPT_TOKENS = 2000       # task_prompts.KNOWLEDGE_TOKENS
COMPACT_AT = 0.75
KEEP_SHARE = 0.40
SUMMARY_TOKENS = 2000


class TurnBudget(BaseModel):
    """What the rules decided for one turn, with the numbers the person sees."""
    window: int
    answer: int = Field(description="R1: tokens kept for the answer")
    margin: int = Field(description="R2")
    fixed: int = Field(description="R3: system text, tool definitions and the question")
    fits: bool = True
    room: int = 0
    limit: Optional[int] = None          # the person's chat_context_limit, when it lowered the room
    excerpts: int = 0                    # R5: the tokens excerpts may take
    excerpts_wanted: int = 0             # what the excerpts found would need
    history: int = 0                     # the earlier turns' tokens before any compaction
    compact: bool = False                # R6
    keep_turns: int = 0                  # how many of the newest turns stay word for word
    kept_tokens: int = 0
    summary_tokens: int = 0              # the summary's limit
    after: int = 0                       # the request's tokens after compaction (summary counted at its limit)
    method: str = ""                     # how the tokens were counted (token_count.TokenTally.detail)

    def total_before(self) -> int:
        return self.fixed + self.history

    def lines(self) -> List[str]:
        """The budget as plain lines (CLI, GUI box)."""
        out = [f"Window {self.window} tokens: answer {self.answer}, margin {self.margin}, system text, tools and "
               f"question {self.fixed}, room for excerpts and history {max(self.room, 0)}"
               + (f" (your chat_context_limit {self.limit})" if self.limit else "")]
        out.append(f"Earlier turns: {self.history} tokens"
                   + (f"; compacted: {self.keep_turns} newest turn(s) kept ({self.kept_tokens} tokens), the rest "
                      f"summarized in at most {self.summary_tokens} tokens" if self.compact else ""))
        if self.method:
            out.append(f"Counted by: {self.method}")
        return out


def answer_reserve(window: int, max_output: Optional[int] = None, requested: Optional[int] = None) -> int:
    """R1."""
    wanted = min(requested or ANSWER_TOKENS, ANSWER_TOKENS)
    if max_output:
        wanted = min(wanted, int(max_output))
    return max(MIN_ANSWER, min(wanted, window // 4))


def plan_turn(window: int, *, fixed: int, history: Sequence[int], excerpts: int = 0,
              max_output: Optional[int] = None, limit: Optional[int] = None, requested: Optional[int] = None,
              method: str = "") -> TurnBudget:
    """R1 to R6 for a turn: `fixed` the tokens of system text, tool definitions and the question; `history` the tokens
    of each earlier turn, oldest first; `excerpts` the tokens knowledge excerpts would need (0 with tools); `limit`
    the person's chat_context_limit (None or 0: automatic)."""
    answer = answer_reserve(window, max_output, requested)
    margin = int(window * MARGIN_SHARE)
    budget = TurnBudget(window=window, answer=answer, margin=margin, fixed=fixed, history=sum(history),
                        method=method)
    room = window - answer - margin - fixed
    if room < 0:
        budget.fits = False
        budget.room = room
        return budget
    if limit and limit > 0 and limit < room:
        room, budget.limit = limit, limit
    budget.room = room
    budget.excerpts_wanted = excerpts
    budget.excerpts = min(excerpts, room // 3, EXCERPT_TOKENS) if excerpts else 0
    left = room - budget.excerpts
    total = sum(history)
    budget.after = fixed + total + budget.excerpts
    if total <= COMPACT_AT * left:
        budget.keep_turns = len(history)
        budget.kept_tokens = total
        return budget
    budget.compact = True
    keep_room = KEEP_SHARE * left
    kept, kept_tokens = 0, 0
    for tokens in reversed(history):
        if kept_tokens + tokens > keep_room:
            break
        kept, kept_tokens = kept + 1, kept_tokens + tokens
    budget.keep_turns, budget.kept_tokens = kept, kept_tokens
    budget.summary_tokens = max(MIN_ANSWER, min(SUMMARY_TOKENS, left // 4))
    budget.after = fixed + kept_tokens + budget.summary_tokens + budget.excerpts
    return budget


def turn_groups(messages: Sequence[Any]) -> List[Tuple[int, int]]:
    """(start, end) of each turn of a conversation: a turn starts at every message of role "user" (a tool result has
    its own role, so it stays with its call). Messages before the first user message belong to the first turn."""
    starts = [i for i, m in enumerate(messages) if getattr(m, "role", None) == "user"]
    if not messages:
        return []
    if not starts or starts[0] != 0:
        starts = [0] + starts
    ends = starts[1:] + [len(messages)]
    return list(zip(starts, ends))


# --- inside a turn: the tool results that pile up in the tool loop ----------------------------------------------------
#
# Before every follow-up request of the tool loop the request is counted again (system text, tools, history and this
# turn's calls and results) against the window less the answer's room and the margin (R1, R2). When it does not fit:
# T1  a tool result of this turn larger than half of the room this turn's results have is cut to that half: its
#     beginning is kept, with a marker that says how much was cut and how to get the rest;
# T2  still too much: the older tool results of this turn (all but those of the latest model step) become short notes,
#     the oldest first, until it fits; the latest results stay whole;
# T3  still too much: the latest results are cut to an equal share of what is left (as T1);
# T4  still too much (the fixed part alone): the request goes out as it is, and that is said.
# Every step is a line of what was done (key and closed parameters); nothing is dropped silently.

TOOL_SHARE = 0.5            # T1: the largest share one result may take of the room this turn's results have
MIN_RESULT_CHARS = 400      # a cut result keeps at least this much
_SAFE_NAME = r"^[A-Za-z0-9_-]{1,64}$"


def _tool_name(messages: Sequence[Any], call_id: str) -> str:
    import re
    for message in reversed(messages):
        for call in getattr(message, "tool_calls", None) or ():
            if call.id == call_id:
                return call.name if re.fullmatch(_SAFE_NAME, str(call.name)) else "a tool"
    return "a tool"


def cut_result(text: str, keep_chars: int, window: int, tool: str) -> str:
    """`text` cut to its first `keep_chars` characters, with the framework's marker; an untrusted block keeps its
    closing line, so the cut part still reads as data."""
    keep_chars = max(MIN_RESULT_CHARS, int(keep_chars))
    if len(text) <= keep_chars:
        return text
    lines = text.rstrip("\n").split("\n")
    closing = lines[-1] if lines and lines[-1].startswith("</untrusted_data") else ""
    marker = (f"\n[cut by the framework: {keep_chars} of {len(text)} characters kept; the rest did not fit the context "
              f"window of {window} tokens. Call {tool} again with narrower arguments (a filter, a smaller range, "
              "fewer lines) to read the rest.]")
    return text[:keep_chars] + marker + (("\n" + closing) if closing else "")


def result_note(tool: str, chars: int, window: int) -> str:
    return (f"[framework note: the result of {tool} ({chars} characters) was taken out of this request to fit the "
            f"context window of {window} tokens; call {tool} again if it is needed.]")


def fit_turn(messages: List[Any], start: int, limit: int, count: Any, window: int,
             chars_per_token: float) -> List[Tuple[str, dict]]:
    """Fits a tool loop's next request into `limit` tokens by T1 to T4, replacing tool results of this turn (messages
    from index `start` on) in place; `count(messages)` gives the request's tokens. Returns the lines of what was
    done, as (text key, parameters)."""
    done: List[Tuple[str, dict]] = []
    total = count(messages)
    if total <= limit:
        return done
    results = [i for i in range(start, len(messages)) if getattr(messages[i], "role", None) == "tool"]
    if not results:
        done.append(("activity.context.still_over", {"tokens": str(total), "window": str(window)}))
        return done
    last_step = [i for i in results if i > max((j for j in range(start, len(messages))
                                                if getattr(messages[j], "role", None) == "assistant"), default=-1)]
    latest = set(last_step or results[-1:])

    def replace(i: int, content: str) -> None:
        messages[i] = messages[i].model_copy(update={"content": content})

    def text_of(i: int) -> str:
        return messages[i].text()

    emptied = [m.model_copy(update={"content": ""}) if idx in results else m for idx, m in enumerate(messages)]
    room = max(0, limit - count(emptied))           # what this turn's results may take together
    # T1: one result above half of that room is cut to it
    share_chars = int(room * TOOL_SHARE * chars_per_token)
    for i in results:
        text = text_of(i)
        if len(text) > max(share_chars, MIN_RESULT_CHARS):
            tool = _tool_name(messages[:i], messages[i].tool_call_id or "")
            replace(i, cut_result(text, share_chars, window, tool))
            done.append(("activity.context.tool_cut", {"tool": tool, "kept": str(max(share_chars, MIN_RESULT_CHARS)),
                                                       "total": str(len(text)), "window": str(window)}))
    total = count(messages)
    # T2: older results of this turn become notes, the oldest first
    for i in results:
        if total <= limit:
            break
        if i in latest or text_of(i).startswith("[framework note:"):
            continue
        tool = _tool_name(messages[:i], messages[i].tool_call_id or "")
        chars = len(text_of(i))
        replace(i, result_note(tool, chars, window))
        done.append(("activity.context.tool_note", {"tool": tool, "chars": str(chars), "window": str(window)}))
        total = count(messages)
    # T3: the latest results share what is left
    if total > limit:
        others = [m.model_copy(update={"content": ""}) if idx in latest else m for idx, m in enumerate(messages)]
        left = max(0, limit - count(others))
        each = int(left / max(1, len(latest)) * chars_per_token)
        for i in sorted(latest):
            text = text_of(i)
            if len(text) > max(each, MIN_RESULT_CHARS):
                tool = _tool_name(messages[:i], messages[i].tool_call_id or "")
                replace(i, cut_result(text, each, window, tool))
                done.append(("activity.context.tool_cut", {"tool": tool, "kept": str(max(each, MIN_RESULT_CHARS)),
                                                           "total": str(len(text)), "window": str(window)}))
        total = count(messages)
    if total > limit:   # T4
        done.append(("activity.context.still_over", {"tokens": str(total), "window": str(window)}))
    return done
