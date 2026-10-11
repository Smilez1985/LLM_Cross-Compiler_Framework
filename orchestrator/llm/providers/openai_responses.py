#!/usr/bin/env python3
"""
OpenAI's own API through the Responses API.

The current models (gpt-6.1-sol, gpt-6-luna, gpt-6-astra) call tools only there: the model pages say
"Chat Completions is supported without tool calling. Use the Responses API for tool calling."

- input items, function tools (strict), tool_choice "auto", JSON answers via text.format json_schema (strict),
  reasoning.effort from the request, no temperature.
- store=false: nothing is kept on OpenAI's side; the output items (reasoning items carry encrypted_content in
  stateless mode) are replayed unchanged as the next request's input.
- prompt_cache_key is a random per-install id, never derived from the user's identity.
- key and base_url are explicit, Authorization is set explicitly (OPENAI_CUSTOM_HEADERS cannot replace it), and
  organization and project are cleared (no OPENAI_ORG_ID / OPENAI_PROJECT_ID from the environment).
"""

from typing import Any, Dict, List, Optional

from orchestrator.llm.errors import NotConfigured, Refused, map_openai_error
from orchestrator.llm.providers.base import DEFAULT_MAX_TOKENS, ProviderAdapter, json_dumps, schema_name
from orchestrator.llm.providers.openai_compat import parse_arguments, prepare_schema
from orchestrator.llm.types import ChatRequest, ChatResult, Message, ModelInfo, NativeMessage, ToolCall, Usage


class OpenAIResponsesAdapter(ProviderAdapter):
    family = "openai_responses"

    def __init__(self, preset, *, prompt_cache_key: Optional[str] = None, **kw):
        super().__init__(preset, **kw)
        self.base_url = self.base_url or preset.base_url or "https://api.openai.com/v1"
        self.prompt_cache_key = prompt_cache_key
        self._client = None

    def client(self):
        if self._client is None:
            if not self._api_key:
                raise NotConfigured("No OpenAI API key is stored. Add one in the AI settings.", provider=self.provider)
            import openai
            kwargs = dict(api_key=self._api_key, base_url=self.base_url, max_retries=0, timeout=self.timeout,
                          default_headers={**self.preset.headers, "Authorization": f"Bearer {self._api_key}"})
            if self.http_client is not None:
                kwargs["http_client"] = self.http_client
            self._client = openai.OpenAI(**kwargs)
            self._client.organization = None
            self._client.project = None
        return self._client

    def build_params(self, request: ChatRequest, model: str, effort: Optional[str]) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "model": model,
            "input": render_input(request.messages, self.family),
            "max_output_tokens": request.max_tokens or DEFAULT_MAX_TOKENS,
            "store": False,
        }
        instructions = [request.system] if request.system else []
        instructions += [m.text() for m in _leading_system(request.messages)]
        if instructions:
            params["instructions"] = "\n\n".join(instructions)
        if request.tools:
            tools = []
            for t in request.tools:
                schema, strict = prepare_schema(t.input_schema, t.strict)
                tools.append({"type": "function", "name": t.name, "description": t.description,
                              "parameters": schema, "strict": strict})
            params["tools"] = tools
            params["tool_choice"] = "auto"
        if request.output_model is not None:
            schema, strict = prepare_schema(request.output_model.model_json_schema(), True)
            params["text"] = {"format": {"type": "json_schema", "name": schema_name(request.output_model),
                                         "schema": schema, "strict": strict}}
        if effort:
            params["reasoning"] = {"effort": effort}
        if request.cache and self.prompt_cache_key:
            params["prompt_cache_key"] = self.prompt_cache_key
        return params

    def _complete(self, request: ChatRequest, model: str) -> ChatResult:
        effort = self.preset.map_effort(request.effort or self.preset.efforts.get(request.task))
        params = self.build_params(request, model, effort)
        client = self.client()
        try:
            response = client.responses.create(**params)
        except Exception as e:
            raise map_openai_error(e, label="OpenAI", provider=self.provider) from None
        return self.to_result(response, model)

    def to_result(self, response: Any, requested_model: str) -> ChatResult:
        items = [item.to_dict() for item in (response.output or [])]
        texts, refusals, calls = [], [], []
        for item in items:
            kind = item.get("type")
            if kind == "message":
                for part in item.get("content") or []:
                    if part.get("type") == "output_text":
                        texts.append(part.get("text") or "")
                    elif part.get("type") == "refusal":
                        refusals.append(part.get("refusal") or "")
            elif kind == "function_call":
                args, problem = parse_arguments(item.get("arguments"))
                calls.append(ToolCall(id=item.get("call_id") or item.get("id"), name=item.get("name", ""),
                                      arguments=args, parse_error=problem))
        text = "".join(texts)
        incomplete = getattr(getattr(response, "incomplete_details", None), "reason", None)
        if calls:
            stop = "tool_use"
        elif incomplete == "max_output_tokens":
            stop = "max_tokens"
        elif incomplete == "content_filter" or refusals:
            stop = "refusal"
        else:
            stop = "end"
        usage = getattr(response, "usage", None)
        details = getattr(usage, "input_tokens_details", None) if usage else None
        cached = getattr(details, "cached_tokens", 0) or 0
        result = ChatResult(
            text=text, tool_calls=calls, stop=stop, model=getattr(response, "model", None) or requested_model,
            provider=self.provider,
            usage=Usage(input_tokens=max(0, (getattr(usage, "input_tokens", 0) or 0) - cached),
                        output_tokens=getattr(usage, "output_tokens", 0) or 0, cache_read_tokens=cached,
                        cache_write_tokens=getattr(details, "cache_write_tokens", 0) or 0),
            message=Message(role="assistant", content=text, tool_calls=calls,
                            native=NativeMessage(family=self.family, payload={"items": items})),
        )
        if stop == "refusal":
            result.refusal_category = "content_filter" if incomplete == "content_filter" else "refusal"
            result.refusal_explanation = " ".join(refusals) or None
            raise Refused("OpenAI declined this request.", category=result.refusal_category,
                          explanation=result.refusal_explanation, result=result, provider=self.provider)
        return result

    def list_models(self) -> List[ModelInfo]:
        try:
            return [ModelInfo(id=m.id, display_name=m.id) for m in self.client().models.list()]
        except Exception as e:
            raise map_openai_error(e, label="OpenAI", provider=self.provider) from None


def _leading_system(messages: List[Message]) -> List[Message]:
    lead = []
    for m in messages:
        if m.role != "system":
            break
        lead.append(m)
    return lead


def _input_content(content) -> Any:
    if isinstance(content, str):
        return content
    parts = []
    for b in content:
        if b["type"] == "text":
            parts.append({"type": "input_text", "text": b.get("text", "")})
        elif b["type"] == "image":
            parts.append({"type": "input_image",
                          "image_url": f"data:{b.get('media_type', 'image/png')};base64,{b.get('data', '')}"})
    return parts


def render_input(messages: List[Message], family: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    skip = len(_leading_system(messages))  # those go into `instructions`
    for msg in messages[skip:]:
        if msg.role == "system":
            items.append({"role": "developer", "content": msg.text()})
        elif msg.role == "user":
            items.append({"role": "user", "content": _input_content(msg.content)})
        elif msg.role == "assistant":
            if msg.native is not None and msg.native.family == family:
                items.extend(msg.native.payload.get("items", []))
            else:
                if msg.text():
                    items.append({"role": "assistant", "content": msg.text()})
                items += [{"type": "function_call", "call_id": c.id, "name": c.name,
                           "arguments": json_dumps(c.arguments)} for c in msg.tool_calls]
        elif msg.role == "tool":
            text = msg.text()
            items.append({"type": "function_call_output", "call_id": msg.tool_call_id,
                          "output": f"Error: {text}" if msg.is_error else text})
    return items
