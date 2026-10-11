#!/usr/bin/env python3
"""
OpenAI-compatible servers through the openai SDK (Chat Completions): llama-server, Ollama (/v1), vLLM,
LM Studio, OpenRouter and Google Gemini's OpenAI-compatible endpoint.

- tools in function format with tool_choice "auto"; JSON answers via response_format json_schema (strict when
  the schema allows it), validated locally with one repair attempt.
- effort is sent only where the preset says the server understands it (llama-server: reasoning_effort, where
  "none" turns thinking off; Ollama and Gemini: reasoning_effort; OpenRouter: the reasoning object).
- llama-server takes the schema branch when tools and response_format arrive together and ignores the tools,
  so for presets with no_tools_with_response_format the schema moves into the system prompt while tools are on.
- the SDK's environment fallbacks (OPENAI_API_KEY, OPENAI_ORG_ID, OPENAI_PROJECT_ID, OPENAI_BASE_URL) never
  reach third-party servers: key and URL are passed explicitly, organization and project are cleared, and the
  Authorization header is set explicitly, so an Authorization line in OPENAI_CUSTOM_HEADERS cannot replace it.
"""

import json
from typing import Any, Dict, List, Optional

from orchestrator.llm.errors import NotConfigured, Refused, map_openai_error
from orchestrator.llm.providers.base import (DEFAULT_MAX_TOKENS, ProviderAdapter, json_dumps, schema_name,
                                             strict_object_schema)
from orchestrator.llm.types import ChatRequest, ChatResult, Message, ModelInfo, NativeMessage, ToolCall, Usage

_STOP = {"stop": "end", "tool_calls": "tool_use", "function_call": "tool_use", "length": "max_tokens",
         "content_filter": "refusal"}
_PASSTHROUGH = ("reasoning_content", "reasoning_details")  # reasoning some servers want back (llama-server, OpenRouter)
NO_KEY = "not-needed"  # local servers ignore the key, but the SDK requires a value


def is_strict_compatible(schema: Any) -> bool:
    """Strict mode needs closed objects everywhere (no free-form dicts)."""
    if isinstance(schema, dict):
        if schema.get("type") == "object" and schema.get("additionalProperties", False) is not False:
            return False
        return all(is_strict_compatible(v) for v in schema.values())
    if isinstance(schema, list):
        return all(is_strict_compatible(v) for v in schema)
    return True


def prepare_schema(schema: Dict[str, Any], want_strict: bool):
    strict = strict_object_schema(schema)
    if want_strict and is_strict_compatible(strict):
        return strict, True
    return schema, False


def parse_arguments(raw: Optional[str]):
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError) as e:
        return {}, f"arguments are not valid JSON ({e.__class__.__name__})"
    if not isinstance(value, dict):
        return {}, "arguments must be a JSON object"
    return value, None


class OpenAICompatAdapter(ProviderAdapter):
    family = "openai_compat"

    def __init__(self, preset, **kw):
        super().__init__(preset, **kw)
        self._client = None

    @property
    def label(self) -> str:
        return self.preset.label

    def client(self):
        if self._client is None:
            if not self.base_url:
                raise NotConfigured(f"{self.label}: no server URL is set.", provider=self.provider)
            self.check_credential_kind()
            import openai
            api_key = self._api_key or NO_KEY
            kwargs = dict(api_key=api_key, base_url=self.base_url, max_retries=0, timeout=self.timeout,
                          default_headers={**self.preset.headers, **self.credential_headers("Authorization"),
                                           "Authorization": f"Bearer {api_key}"})
            if self.http_client is not None:
                kwargs["http_client"] = self.http_client
            self._client = openai.OpenAI(**kwargs)
            self._client.organization = None  # read from OPENAI_ORG_ID otherwise and sent to this server
            self._client.project = None
        return self._client

    # --- request ------------------------------------------------------------------------------------------
    def build_params(self, request: ChatRequest, model: str, effort: Optional[str]) -> Dict[str, Any]:
        system = request.system
        schema_param = None
        if request.output_model is not None:
            schema, strict = prepare_schema(request.output_model.model_json_schema(), True)
            if request.tools and self.preset.no_tools_with_response_format:
                system = (system + "\n\n" if system else "") + (
                    "When you give your final answer (after any tool calls), reply with only JSON that matches "
                    f"this JSON schema, nothing else:\n{json_dumps(schema)}")
            else:
                schema_param = {"type": "json_schema",
                                "json_schema": {"name": schema_name(request.output_model), "schema": schema,
                                                "strict": strict}}
        params: Dict[str, Any] = {
            "model": model,
            "messages": render_messages(system, request.messages, self.family),
            "max_tokens": request.max_tokens or DEFAULT_MAX_TOKENS,
        }
        if request.tools:
            tools = []
            for t in request.tools:
                schema, strict = prepare_schema(t.input_schema, t.strict)
                fn: Dict[str, Any] = {"name": t.name, "description": t.description, "parameters": schema}
                if strict:
                    fn["strict"] = True
                tools.append({"type": "function", "function": fn})
            params["tools"] = tools
            params["tool_choice"] = "auto"
        if schema_param:
            params["response_format"] = schema_param
        if effort:
            if self.preset.reasoning == "reasoning_effort":
                params["reasoning_effort"] = effort
            elif self.preset.reasoning == "reasoning_object":
                params["extra_body"] = {"reasoning": {"effort": effort}}
        return params

    def _complete(self, request: ChatRequest, model: str) -> ChatResult:
        effort = self.preset.map_effort(request.effort or self.preset.efforts.get(request.task))
        params = self.build_params(request, model, effort)
        client = self.client()
        try:
            response = client.chat.completions.create(**params)
        except Exception as e:
            raise map_openai_error(e, label=self.label, provider=self.provider, preset=self.preset,
                                   base_url=self.base_url) from None
        return self.to_result(response, model)

    def to_result(self, response: Any, requested_model: str) -> ChatResult:
        if not getattr(response, "choices", None):
            raise map_openai_error(RuntimeError("the server returned no answer"), label=self.label,
                                   provider=self.provider)
        choice = response.choices[0]
        msg = choice.message
        text = msg.content or ""
        calls: List[ToolCall] = []
        native_calls = []
        for tc in msg.tool_calls or []:
            fn = getattr(tc, "function", None)
            if fn is None:
                continue
            args, problem = parse_arguments(fn.arguments)
            calls.append(ToolCall(id=tc.id, name=fn.name, arguments=args, parse_error=problem))
            native_calls.append({"id": tc.id, "type": "function",
                                 "function": {"name": fn.name, "arguments": fn.arguments or "{}"}})
        payload: Dict[str, Any] = {"role": "assistant", "content": msg.content}
        if native_calls:
            payload["tool_calls"] = native_calls
        extra = msg.model_extra or {}
        for key in _PASSTHROUGH:
            if extra.get(key):
                payload[key] = extra[key]
        stop = _STOP.get(choice.finish_reason or "stop", "end")
        if calls and stop == "end":
            stop = "tool_use"  # some servers report "stop" together with tool calls
        usage = getattr(response, "usage", None)
        details = getattr(usage, "prompt_tokens_details", None) if usage else None
        cached = getattr(details, "cached_tokens", None) or 0
        result = ChatResult(
            text=text, tool_calls=calls, stop=stop, model=getattr(response, "model", None) or requested_model,
            provider=self.provider,
            usage=Usage(input_tokens=max(0, (getattr(usage, "prompt_tokens", 0) or 0) - cached),
                        output_tokens=getattr(usage, "completion_tokens", 0) or 0, cache_read_tokens=cached),
            message=Message(role="assistant", content=text, tool_calls=calls,
                            native=NativeMessage(family=self.family, payload=payload)),
        )
        refusal = getattr(msg, "refusal", None)
        if refusal or stop == "refusal":
            result.stop = "refusal"
            result.refusal_category = "content_filter" if stop == "refusal" else "refusal"
            result.refusal_explanation = refusal or None
            raise Refused(f"{self.label} declined this request.", category=result.refusal_category,
                          explanation=refusal or None, result=result, provider=self.provider)
        return result

    def list_models(self) -> List[ModelInfo]:
        try:
            page = self.client().models.list()
            models = []
            for m in page:
                extra = m.model_extra or {}
                model_id = m.id[len("models/"):] if m.id.startswith("models/") else m.id  # Gemini lists models/<id>
                models.append(ModelInfo(id=model_id, display_name=str(extra.get("name") or model_id),
                                        max_input_tokens=extra.get("context_length"),
                                        capabilities={k: extra[k] for k in ("supported_parameters",) if k in extra}))
            return models
        except Exception as e:
            raise map_openai_error(e, label=self.label, provider=self.provider, preset=self.preset,
                                   base_url=self.base_url, during="models") from None


def _content(content) -> Any:
    if isinstance(content, str):
        return content
    parts = []
    for b in content:
        if b["type"] == "text":
            parts.append({"type": "text", "text": b.get("text", "")})
        elif b["type"] == "image":
            url = f"data:{b.get('media_type', 'image/png')};base64,{b.get('data', '')}"
            parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts


def render_messages(system: str, messages: List[Message], family: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    leading_system = [system] if system else []
    started = False
    for msg in messages:
        if msg.role == "system":
            if not started:
                leading_system.append(msg.text())
            else:
                # Many chat templates accept a system message only at the start.
                out.append({"role": "user", "content": f"[System note] {msg.text()}"})
            continue
        started = True
        if msg.role == "user":
            out.append({"role": "user", "content": _content(msg.content)})
        elif msg.role == "assistant":
            if msg.native is not None and msg.native.family == family:
                out.append(msg.native.payload)
            else:
                entry: Dict[str, Any] = {"role": "assistant", "content": msg.text() or None}
                if msg.tool_calls:
                    entry["tool_calls"] = [{"id": c.id, "type": "function",
                                            "function": {"name": c.name, "arguments": json_dumps(c.arguments)}}
                                           for c in msg.tool_calls]
                out.append(entry)
        elif msg.role == "tool":
            text = msg.text()
            out.append({"role": "tool", "tool_call_id": msg.tool_call_id,
                        "content": f"Error: {text}" if msg.is_error else text})
    if leading_system:
        out.insert(0, {"role": "system", "content": "\n\n".join(leading_system)})
    return out
