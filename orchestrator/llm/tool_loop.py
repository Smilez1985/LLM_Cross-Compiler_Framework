#!/usr/bin/env python3
"""
A thin tool loop on top of LLMClient.chat().

The history only grows: assistant turns are appended exactly as the provider returned them, the tools array is
the same object on every step, and each step's tool results go back together (one user turn on Claude).
That keeps the prompt cache warm and Claude's preserved thinking valid across the loop.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from orchestrator.llm.errors import Refused
from orchestrator.llm.types import ChatRequest, ChatResult, Message, ToolSpecLLM, Usage, tool_result_text
from orchestrator.utils.helpers import redact_secrets
from orchestrator.utils.untrusted import wrap_untrusted

ExecuteTool = Callable[[str, dict], Any]


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


def run_tool_loop(client: Any, request: ChatRequest, execute_tool: ExecuteTool, max_steps: int = 8) -> ToolLoopResult:
    """Runs model turns and tool calls until the model answers, refuses, or max_steps model turns were made.
    `execute_tool(name, arguments)` returns the result (text, dict or pydantic model) or raises on failure."""
    history = list(request.messages)
    new: List[Message] = []
    results: List[ChatResult] = []
    usage = Usage()
    tools = request.tools  # the very same list on every step
    for step in range(1, max_steps + 1):
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
        for call in result.tool_calls:
            if call.parse_error:
                outcome = Message.tool_result(call.id, f"Invalid arguments for {call.name}: {call.parse_error}",
                                              is_error=True)
            else:
                try:
                    outcome = Message.tool_result(call.id, tool_result_text(execute_tool(call.name, call.arguments)))
                except Exception as e:  # the model sees the failure and can adapt
                    outcome = Message.tool_result(call.id, redact_secrets(str(e)) or type(e).__name__, is_error=True)
            history.append(outcome)
            new.append(outcome)
    last = results[-1] if results else None
    return ToolLoopResult(text=last.text if last else "", stop="max_steps", steps=max_steps, new_messages=new,
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
