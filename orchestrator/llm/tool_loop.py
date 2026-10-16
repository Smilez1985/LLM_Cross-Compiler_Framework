#!/usr/bin/env python3
"""
A thin tool loop on top of LLMClient.chat().

The history only grows: assistant turns are appended exactly as the provider returned them, the tools array is
the same object on every step, and each step's tool results go back together (one user turn on Claude).
That keeps the prompt cache warm and Claude's preserved thinking valid across the loop.

An early honest stop (MCP design 6.11 D8): after `stop_after` failed tool calls in a row without progress (the same
tool, reason code and field as the failure before it) or a `repeat_refused`, one instruction is appended telling the
model to stop calling tools and answer the user with what it tried, what failed and why; tool calls after it are
answered without running, and the loop ends at most two turns later. A model that fixes one input error after another makes progress and is not stopped, and
while the last result is an input error it is fixing, the step budget grows by up to `extra_steps`.

A call that waits for a decision (reason code waiting_for_approval or question_changed, delegation design 3.5) has not
failed: its result goes back as a plain result (not an error) and never counts toward the failure streak.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from orchestrator.llm.errors import Refused
from orchestrator.llm.types import ChatRequest, ChatResult, Message, ToolSpecLLM, Usage, tool_result_text
from orchestrator.tools.normalize import PENDING_CODES as _PENDING
from orchestrator.utils.helpers import redact_secrets
from orchestrator.utils.untrusted import wrap_untrusted

ExecuteTool = Callable[[str, dict], Any]

STOP_TEXT = ("Several tool calls failed. Do not call more tools. Answer the user now: what you tried, what failed and "
             "why, and what they can do.")
NOT_RUN_TEXT = "Not run: several tool calls failed before. Answer the user now without calling more tools."
# Errors the model removes by changing its call (a missing, unknown or invalid argument).
INPUT_ERROR_CODES = frozenset({"unknown_argument", "missing_argument", "invalid_value"})
# A call that waits for a decision: not a failure (normalize.PENDING_CODES, the one list of them).
PENDING_CODES = frozenset(_PENDING)


@dataclass
class ToolLoopResult:
    text: str
    stop: str                      # end | max_tokens | refusal | max_steps
    steps: int
    new_messages: List[Message] = field(default_factory=list)  # append these to the conversation
    results: List[ChatResult] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    refusal: Optional[Refused] = None
    parsed: Any = None


def run_tool_loop(client: Any, request: ChatRequest, execute_tool: ExecuteTool, max_steps: int = 8,
                  extra_steps: int = 0, stop_after: int = 3) -> ToolLoopResult:
    """Runs model turns and tool calls until the model answers, refuses, or the step budget is used up (max_steps
    model turns, plus up to `extra_steps` while the model fixes an input error, plus the one answer turn after an
    early stop). `execute_tool(name, arguments)` returns the result (text, dict or pydantic model) or raises on
    failure; an exception's `code` and `field` (ToolError) tell progress from a repeat."""
    history = list(request.messages)
    new: List[Message] = []
    results: List[ChatResult] = []
    usage = Usage()
    tools = request.tools  # the very same list on every step
    budget, extended, step = max_steps, 0, 0
    previous: Optional[tuple] = None  # (tool, code, field) of the failure before
    streak = 0                        # failures in a row without progress
    stopped = noted = was_fixing = False
    while step < budget:
        step += 1
        step_request = request.model_copy(update={"messages": list(history), "tools": tools})
        try:
            result = client.chat(step_request)
        except Refused as e:
            return ToolLoopResult(text="", stop="refusal", steps=step, new_messages=new, results=results,
                                  usage=usage, refusal=e)
        results.append(result)
        usage = usage + result.usage
        history.extend(result.new_messages)
        new.extend(result.new_messages)
        if result.stop != "tool_use" or not result.tool_calls:
            return ToolLoopResult(text=result.text, stop=result.stop, steps=step, new_messages=new,
                                  results=results, usage=usage, parsed=result.parsed)
        fixing = fixed = False
        for call in result.tool_calls:
            failure = None
            if stopped:  # every call is answered, none runs after the stop instruction
                outcome = Message.tool_result(call.id, NOT_RUN_TEXT, is_error=True)
            elif call.parse_error:
                outcome = Message.tool_result(call.id, f"Invalid arguments for {call.name}: {call.parse_error}",
                                              is_error=True)
                failure = (call.name, "invalid_value", "")
            else:
                waiting = False
                try:
                    outcome = Message.tool_result(call.id, tool_result_text(execute_tool(call.name, call.arguments)))
                except Exception as e:  # the model sees the failure and can adapt
                    code = str(getattr(e, "code", "") or "failed")
                    waiting = code in PENDING_CODES
                    outcome = Message.tool_result(call.id, redact_secrets(str(e)) or type(e).__name__,
                                                  is_error=not waiting)
                    if not waiting:
                        failure = (call.name, code, str(getattr(e, "field", "") or ""))
                if waiting:  # waits for a decision: neither a failure nor progress for the streak
                    history.append(outcome)
                    new.append(outcome)
                    continue
            history.append(outcome)
            new.append(outcome)
            if stopped:
                continue
            if failure is None:
                fixed = fixing or was_fixing  # a fix that went through gets its turn to answer
                streak, previous, fixing = 0, None, False
                continue
            streak = streak + 1 if failure == previous else 1
            previous = failure
            fixing, fixed = failure[1] in INPUT_ERROR_CODES, False
            if failure[1] == "repeat_refused" or streak >= stop_after:
                stopped = True
        if stopped and not noted:
            note = Message(role="system", content=STOP_TEXT)  # a framework instruction, never tool output
            history.append(note)
            new.append(note)
            noted = True
            # One turn to answer, and one more for a model that still called a tool; never the rest of the budget
            # in "Not run" answers.
            budget = min(max(budget, step + 1), step + 2)
        elif (fixing or fixed) and step == budget and extended < extra_steps:
            budget, extended = budget + 1, extended + 1
        was_fixing = fixing
    last = results[-1] if results else None
    return ToolLoopResult(text=last.text if last else "", stop="max_steps", steps=step, new_messages=new,
                          results=results, usage=usage)


def tool_specs_from_registry(registry: Any, names: Optional[List[str]] = None) -> List[ToolSpecLLM]:
    """The registry's tools as model tool definitions, in a stable order (sorted by name)."""
    specs = [s for s in registry.list() if names is None or s.name in names]
    return [ToolSpecLLM.from_tool_spec(s) for s in sorted(specs, key=lambda s: s.name)]


def make_registry_executor(registry: Any, ctx: Any, wrap: bool = True) -> ExecuteTool:
    """execute_tool for run_tool_loop that goes through ToolRegistry.call (validation, permissions, audit).
    Tool output can carry external text (logs, crawled pages), so it is wrapped as untrusted data."""
    def execute(name: str, arguments: dict) -> str:
        output = tool_result_text(registry.call(name, arguments, ctx))
        return wrap_untrusted(output, source=f"tool:{name}") if wrap else output
    return execute
