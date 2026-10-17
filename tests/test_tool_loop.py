"""Ditto's tool loop and the codes of the delegation design (WP5).

- A call that waits for a decision (waiting_for_approval, question_changed; delegation design 3.5) has not failed:
  its result goes back as a plain result, never as an error, and never counts toward the early stop.
- The remote mode's codes (wrong_place, needs_transfer, place_unreachable) are gone with it.
"""

from orchestrator.llm.tool_loop import INPUT_ERROR_CODES, PENDING_CODES, STOP_TEXT, run_tool_loop
from orchestrator.llm.types import ChatRequest, ChatResult, Message, ToolCall, Usage
from orchestrator.tools import normalize
from orchestrator.tools.registry import ToolError


class Model:
    """Calls `tool` `calls` times (one call per turn, each with other arguments), then answers."""

    def __init__(self, calls, tool="get_job_status"):
        self.left, self.tool, self.turns = calls, tool, 0

    def chat(self, request):
        self.turns += 1
        if self.left:
            self.left -= 1
            call = ToolCall(id=f"c{self.turns}", name=self.tool, arguments={"n": self.turns})
            return ChatResult(text="", stop="tool_use", tool_calls=[call], usage=Usage(),
                              message=Message(role="assistant", tool_calls=[call]))
        return ChatResult(text="Answered.", stop="end", usage=Usage(),
                          message=Message(role="assistant", content="Answered."))


def request():
    return ChatRequest(messages=[Message.user("go")])


def test_waiting_is_not_a_failure_in_dittos_loop():
    def waiting(name, arguments):
        raise ToolError("Not failed: waiting for a decision.", code="waiting_for_approval")
    loop = run_tool_loop(Model(4), request(), waiting, max_steps=8)
    results = [m for m in loop.new_messages if m.role == "tool"]
    assert len(results) == 4 and not any(m.is_error for m in results), "a waiting result is no error"
    assert not [m for m in loop.new_messages if m.role == "system" and m.text() == STOP_TEXT], \
        "four waits in a row never stop the loop early"
    assert loop.stop == "end" and loop.text == "Answered."
    assert PENDING_CODES == frozenset(normalize.PENDING_CODES) == {"waiting_for_approval", "question_changed"}


def test_a_real_failure_still_stops_after_three():
    def failing(name, arguments):
        raise ToolError("broken", code="failed")
    loop = run_tool_loop(Model(5), request(), failing, max_steps=8)
    assert [m for m in loop.new_messages if m.role == "system" and m.text() == STOP_TEXT]


def test_the_remote_modes_codes_are_gone():
    assert not {"wrong_place", "needs_transfer", "place_unreachable"} & set(normalize.CODES)
    assert "wrong_place" not in INPUT_ERROR_CODES
