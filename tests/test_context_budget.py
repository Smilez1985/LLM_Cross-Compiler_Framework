"""The turn budget (llm/context_budget.py): the rules R1 to R6 on fixed token numbers, and the cut into turns that never
separates a tool call from its result."""

from orchestrator.llm.context_budget import (ANSWER_TOKENS, COMPACT_AT, EXCERPT_TOKENS, KEEP_SHARE, MIN_ANSWER,
                                             answer_reserve, plan_turn, turn_groups)
from orchestrator.llm.types import Message, ToolCall


def test_the_answer_keeps_the_smallest_of_its_three_limits():
    assert answer_reserve(1_000_000) == ANSWER_TOKENS                    # the adapters' answer length
    assert answer_reserve(1_000_000, max_output=8000) == 8000             # the model's maximum output
    assert answer_reserve(8192) == 2048                                   # a quarter of the window
    assert answer_reserve(512) == MIN_ANSWER                              # never below the minimum
    assert answer_reserve(1_000_000, requested=2000) == 2000


def test_a_short_history_is_sent_unchanged():
    budget = plan_turn(8192, fixed=500, history=[300, 400, 200])
    assert budget.fits and not budget.compact and budget.keep_turns == 3
    assert budget.answer == 2048 and budget.margin == 409
    assert budget.room == 8192 - 2048 - 409 - 500 and budget.after == 500 + 900


def test_a_history_past_the_threshold_keeps_the_newest_turns_that_fit():
    room = 8192 - 2048 - 409 - 500
    history = [2000, 1500, 1000, 600, 500]                               # 5600 > 0.75 * room
    budget = plan_turn(8192, fixed=500, history=history)
    assert budget.history > COMPACT_AT * room and budget.compact
    # newest first: 500 + 600 = 1100 fit into 40 % of the room (2094); with the next turn (1000) they would not
    assert KEEP_SHARE * room < 2100 and budget.keep_turns == 2 and budget.kept_tokens == 1100
    assert budget.summary_tokens == min(2000, room // 4)
    assert budget.after == 500 + 1100 + budget.summary_tokens
    assert budget.after - 500 <= COMPACT_AT * room, "after the compaction the history is below the threshold"


def test_when_not_even_the_newest_turn_fits_everything_is_summarized():
    budget = plan_turn(4096, fixed=300, history=[200, 3000])
    assert budget.compact and budget.keep_turns == 0 and budget.kept_tokens == 0


def test_a_question_that_does_not_fit_is_refused():
    budget = plan_turn(4096, fixed=4000, history=[10])
    assert not budget.fits and budget.room < 0


def test_the_persons_limit_lowers_the_room_and_excerpts_take_a_third_at_most():
    budget = plan_turn(1_000_000, fixed=1000, history=[100], excerpts=EXCERPT_TOKENS, limit=3000)
    assert budget.limit == 3000 and budget.room == 3000 and budget.excerpts == 1000
    wide = plan_turn(1_000_000, fixed=1000, history=[100], excerpts=EXCERPT_TOKENS)
    assert wide.limit is None and wide.excerpts == EXCERPT_TOKENS
    assert plan_turn(1_000_000, fixed=1000, history=[], excerpts=0).excerpts == 0


def test_the_same_numbers_decide_the_same_way():
    args = dict(fixed=700, history=[1200, 900, 1300, 400], excerpts=2000, max_output=4096)
    assert plan_turn(6000, **args) == plan_turn(6000, **args)


def test_the_lines_name_the_numbers():
    lines = plan_turn(8192, fixed=500, history=[4000, 1500], method="estimate (qwen, ...)").lines()
    assert lines[0].startswith("Window 8192 tokens: answer 2048, margin 409")
    assert "compacted" in lines[1] and lines[-1] == "Counted by: estimate (qwen, ...)"


def test_a_turn_starts_at_every_person_message_and_keeps_its_tool_results():
    call = ToolCall(id="t1", name="list_targets", arguments={})
    messages = [Message.user("a"), Message(role="assistant", content="", tool_calls=[call]),
                Message(role="tool", content="r", tool_call_id="t1"), Message(role="assistant", content="done"),
                Message.user("b"), Message(role="assistant", content="ok")]
    assert turn_groups(messages) == [(0, 4), (4, 6)]
    assert turn_groups([]) == []
    assert turn_groups([Message(role="assistant", content="x"), Message.user("y")]) == [(0, 1), (1, 2)]
