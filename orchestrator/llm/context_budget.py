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
