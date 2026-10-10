#!/usr/bin/env python3
"""
Provider-neutral request/response types of the LLM layer.

Conversations are append-only: a ChatResult carries the provider's own assistant message (`native`), which
goes back into the history unchanged. Claude binds its thinking blocks to the exact conversation prefix, and
every provider's prompt cache is a prefix match, so earlier turns must never be rebuilt or edited.
"""

import json
from typing import Any, Dict, List, Literal, Optional, Type, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

Role = Literal["user", "assistant", "tool", "system"]
Effort = Literal["low", "medium", "high", "xhigh", "max"]
Task = Literal["chat", "tools", "json", "healing"]
StopReason = Literal["end", "tool_use", "max_tokens", "refusal"]

TASKS: tuple = ("chat", "tools", "json", "healing")
EFFORTS: tuple = ("low", "medium", "high", "xhigh", "max")


class NativeMessage(BaseModel):
    """A provider's assistant turn exactly as returned (thinking blocks, reasoning items, ids).
    Only the adapter family that produced it reads `payload`; others fall back to the neutral fields."""
    model_config = ConfigDict(frozen=True)

    family: str
    payload: Dict[str, Any]

    def __repr__(self) -> str:  # payloads can be large; keep logs readable
        return f"NativeMessage(family={self.family!r})"


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    parse_error: Optional[str] = None  # the model sent arguments that are not a JSON object


class Message(BaseModel):
    """One turn. `content` is plain text or a list of neutral blocks:
    {"type": "text", "text": ...} or {"type": "image", "media_type": "image/png", "data": <base64>}."""
    role: Role
    content: Union[str, List[Dict[str, Any]]] = ""
    tool_call_id: Optional[str] = None   # role == "tool": the call this result answers
    is_error: bool = False               # role == "tool": the tool failed
    tool_calls: List[ToolCall] = Field(default_factory=list)  # role == "assistant"
    native: Optional[NativeMessage] = None                    # role == "assistant"

    @field_validator("content")
    @classmethod
    def _blocks_have_type(cls, value):
        if isinstance(value, list):
            for block in value:
                if not isinstance(block, dict) or block.get("type") not in ("text", "image"):
                    raise ValueError("content blocks must be {'type': 'text'|'image', ...}")
        return value

    def text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        return "".join(b.get("text", "") for b in self.content if b.get("type") == "text")

    @classmethod
    def user(cls, text: str) -> "Message":
        return cls(role="user", content=text)

    @classmethod
    def tool_result(cls, call_id: str, content: str, is_error: bool = False) -> "Message":
        return cls(role="tool", tool_call_id=call_id, content=content, is_error=is_error)


class ToolSpecLLM(BaseModel):
    """A tool as the model sees it. `input_schema` is a JSON schema of an object."""
    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    description: str = ""
    input_schema: Dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    strict: bool = True

    @classmethod
    def from_model(cls, name: str, description: str, input_model: Type[BaseModel],
                   strict: bool = True) -> "ToolSpecLLM":
        return cls(name=name, description=description, input_schema=input_model.model_json_schema(), strict=strict)

    @classmethod
    def from_tool_spec(cls, spec) -> "ToolSpecLLM":
        """From an orchestrator.tools.registry.ToolSpec."""
        return cls.from_model(spec.name, spec.description, spec.input_model)


class ChatRequest(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    system: str = ""
    messages: List[Message]
    tools: List[ToolSpecLLM] = Field(default_factory=list)
    output_model: Optional[Type[BaseModel]] = None  # forces a JSON answer validated into this model
    max_tokens: Optional[int] = Field(default=None, ge=1)
    effort: Optional[Effort] = None
    task: Task = "chat"
    cache: bool = True
    cache_ttl: Literal["5m", "1h"] = "5m"
    model: Optional[str] = None  # overrides the configured model for this one call


class Usage(BaseModel):
    input_tokens: int = 0       # uncached input
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(**{k: getattr(self, k) + getattr(other, k) for k in type(self).model_fields})


class ChatResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    text: str = ""
    tool_calls: List[ToolCall] = Field(default_factory=list)
    parsed: Optional[BaseModel] = None
    stop: StopReason = "end"
    usage: Usage = Field(default_factory=Usage)
    model: str = ""
    provider: str = ""
    message: Message                      # the final assistant turn
    new_messages: List[Message] = Field(default_factory=list)  # everything to append, in order (see repair retry)
    refusal_category: Optional[str] = None
    refusal_explanation: Optional[str] = None

    def model_post_init(self, __context: Any) -> None:
        if not self.new_messages:
            self.new_messages = [self.message]


class ModelInfo(BaseModel):
    id: str
    display_name: str = ""
    max_input_tokens: Optional[int] = None
    max_tokens: Optional[int] = None
    capabilities: Dict[str, Any] = Field(default_factory=dict)


def tool_result_text(value: Any) -> str:
    """Tool outputs as text for the model (pydantic models and dicts as compact JSON)."""
    if isinstance(value, str):
        return value
    if isinstance(value, BaseModel):
        return value.model_dump_json()
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)
