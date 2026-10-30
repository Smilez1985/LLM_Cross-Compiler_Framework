"""A repeated tool result goes to the model as a pointer (llm/tool_loop.py; consolidation chain ctx-slim, decision:
smaller tool results).

When a call returns byte for byte what an earlier call of the same tool with the same arguments returned, and that
earlier result is still in the current request, the model gets "same result as tool call #<id>" instead of the
bytes again. Old turns are never rewritten, an error result is never pointed at, and a result no longer in the request
(compacted away into a summary) is never pointed at. The random id of an untrusted-data block is not part of the
bytes compared. The vectors live in tests/fixtures/context_rules/duplicate_result.json for the board gateway.
"""

import json
from pathlib import Path

import pytest

from orchestrator.llm.tool_loop import DUPLICATE_TEXT, run_tool_loop
from orchestrator.llm.types import ChatRequest, ChatResult, Message, ToolCall, Usage
from orchestrator.utils.untrusted import wrap_untrusted

VECTORS = json.loads((Path(__file__).parent / "fixtures" / "context_rules" / "duplicate_result.json")
                     .read_text("utf-8"))


class Model:
    """Makes the given calls, one per turn, then answers; keeps every request it was sent."""

    def __init__(self, calls):
        self.calls, self.requests = list(calls), []

    def chat(self, request):
        self.requests.append(request)
        if self.calls:
            call = self.calls.pop(0)
            return ChatResult(text="", stop="tool_use", tool_calls=[call], usage=Usage(),
                              message=Message(role="assistant", tool_calls=[call]))
        return ChatResult(text="Answered.", stop="end", usage=Usage(),
                          message=Message(role="assistant", content="Answered."))


def present_turns(present):
    if not present:
        return []
    calls = [ToolCall(id=p["id"], name=p["name"], arguments=p["arguments"]) for p in present]
    return [Message.user("earlier"), Message(role="assistant", tool_calls=calls),
            *[Message.tool_result(p["id"], p["result"], is_error=p.get("is_error", False)) for p in present]]


@pytest.mark.parametrize("case", VECTORS, ids=[c["name"] for c in VECTORS])
def test_vectors(case):
    earlier = present_turns(case["present"])
    snapshot = [m.model_dump_json() for m in earlier]
    results = {n["id"]: n["result"] for n in case["new"]}
    calls = [ToolCall(id=n["id"], name=n["name"], arguments=n["arguments"]) for n in case["new"]]
    model = Model(calls)
    executed = []

    def execute(name, arguments):
        call = calls[len(executed)]
        executed.append(call.id)
        return results[call.id]

    loop = run_tool_loop(model, ChatRequest(messages=[*earlier, Message.user("go")]), execute, max_steps=8)
    sent = [m.content for m in loop.new_messages if m.role == "tool"]
    assert sent == case["sent"]
    assert [m.model_dump_json() for m in earlier] == snapshot, "old turns keep their bytes"
    assert executed == [n["id"] for n in case["new"]], "every call still runs"


def test_the_pointer_text():
    assert DUPLICATE_TEXT.format(call_id="c1") == "same result as tool call #c1"


def test_two_wrapped_results_of_the_registry_executor_are_one():
    """Ditto's executor wraps every result in an untrusted block with a new random id each time."""
    calls = [ToolCall(id="a", name="list_targets", arguments={}), ToolCall(id="b", name="list_targets", arguments={})]
    loop = run_tool_loop(Model(calls), ChatRequest(messages=[Message.user("go")]),
                         lambda name, arguments: wrap_untrusted('["Rockchip"]', source=f"tool:{name}"))
    first, second = [m.content for m in loop.new_messages if m.role == "tool"]
    assert '["Rockchip"]' in first and second == "same result as tool call #a"


def test_the_pointed_at_result_is_in_the_request_that_carries_the_pointer():
    calls = [ToolCall(id="a", name="t", arguments={"x": 1}), ToolCall(id="b", name="t", arguments={"x": 1})]
    model = Model(calls)
    run_tool_loop(model, ChatRequest(messages=[Message.user("go")]), lambda name, arguments: {"ok": True})
    last = model.requests[-1].messages
    pointer = next(m for m in last if m.role == "tool" and m.tool_call_id == "b")
    original = next(m for m in last if m.role == "tool" and m.tool_call_id == "a")
    assert pointer.content == "same result as tool call #a" and original.content == '{"ok":true}'


def test_an_error_is_never_replaced():
    from orchestrator.tools.registry import ToolError
    calls = [ToolCall(id="a", name="t", arguments={}), ToolCall(id="b", name="t", arguments={})]

    def failing(name, arguments):
        raise ToolError("broken", code="failed")
    loop = run_tool_loop(Model(calls), ChatRequest(messages=[Message.user("go")]), failing)
    assert [m.content for m in loop.new_messages if m.role == "tool"] == ["broken", "broken"]
