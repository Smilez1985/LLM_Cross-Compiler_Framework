#!/usr/bin/env python3
"""
Claude through the official anthropic SDK (Messages API).

Request rules for the current models (Claude Opus 5.5, Sonnet 5.5, Haiku 5.5, Fable 5.1):
- no temperature/top_p/top_k and no `thinking` field: adaptive thinking is the default, Sonnet 5.5 rejects
  {"type": "disabled"} and Opus 5.5 rejects any thinking budget; depth is set with output_config.effort,
  always sent explicitly (Opus 5.5 defaults to medium, other models to high).
- tool_choice is always auto: forced tool choice is a 400 on Opus 5.5, Sonnet 5.5 and Fable 5.1. Tools are
  strict with closed schemas, so arguments match the schema.
- JSON answers use output_config.format (json_schema), validated locally with one repair attempt.
- automatic prompt caching via the top-level cache_control, plus a breakpoint on the static system prompt.
- server-side refusal fallback (fallbacks: "default") for the models listed in the preset, Claude API only.
- assistant turns are passed back unchanged (thinking blocks are bound to the conversation prefix).

Credentials: the stored API key, or an `ant auth login` profile only when the user chose that method. The
client always gets an explicit credential and base_url, so neither ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL nor the
CLI's active profile can switch the account or the endpoint. A workspace ID (keys valid in every workspace of
the organization need one) goes out as the anthropic-workspace-id header on every request made with the API
key to the Claude API; an `ant` profile keeps the workspace it was set up with, and other hosts never get it.
"""

import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from orchestrator.llm.errors import ANTHROPIC_WORKSPACE_INVALID, NotConfigured, Refused, map_anthropic_error
from orchestrator.llm.providers.base import DEFAULT_MAX_TOKENS, ProviderAdapter
from orchestrator.llm.types import (ChatRequest, ChatResult, Message, ModelInfo, NativeMessage, ToolCall,
                                    ToolSpecLLM, Usage)

FALLBACK_BETA = "server-side-fallback-2026-07-01"  # pairs with the scalar form fallbacks="default"
CLAUDE_API_HOSTS = ("api.anthropic.com",)
WORKSPACE_HEADER = "anthropic-workspace-id"
WORKSPACE_ID = re.compile(r"^wrkspc_[A-Za-z0-9]+\Z")

_STOP = {"end_turn": "end", "stop_sequence": "end", "pause_turn": "end", "tool_use": "tool_use",
         "max_tokens": "max_tokens", "model_context_window_exceeded": "max_tokens", "refusal": "refusal"}


def is_claude_api(base_url: Optional[str]) -> bool:
    return (urlparse(base_url or "").hostname or "").lower() in CLAUDE_API_HOSTS


def takes_workspace_id(preset) -> bool:
    """Whether ai_anthropic_workspace_id applies to this preset (Claude API presets only)."""
    return preset.adapter == "anthropic" and is_claude_api(preset.base_url)


def _matches(model: str, prefixes: List[str]) -> bool:
    """Exact id or a dated snapshot of it (prefix match)."""
    return any(model.startswith(p) for p in prefixes if p)


def claude_schema(schema: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The schema in the subset strict mode accepts (closed objects; unsupported constraints move into the
    description and are still checked locally). None when the schema cannot be made strict."""
    import anthropic
    try:
        return anthropic.transform_schema(dict(schema))
    except (ValueError, TypeError, KeyError):
        return None


class AnthropicAdapter(ProviderAdapter):
    family = "anthropic"

    def __init__(self, preset, *, auth: str = "api_key", oauth_profile: Optional[str] = None,
                 workspace_id: Optional[str] = None, **kw):
        super().__init__(preset, **kw)
        self.base_url = self.base_url or preset.base_url or "https://api.anthropic.com"
        self.auth = auth
        self.oauth_profile = oauth_profile
        # For the API key on the Claude API only: a profile's own workspace must not be replaced silently.
        use_workspace = workspace_id and auth == "api_key" and is_claude_api(self.base_url)
        self.workspace_id = (str(workspace_id).strip() or None) if use_workspace else None
        self._profile: Optional[str] = None  # resolved name of the `ant` profile in use
        self._client = None

    def _map_error(self, err: BaseException):
        profile = (self._profile or "") if self.auth == "ant_profile" else None
        return map_anthropic_error(err, provider=self.provider, profile=profile)

    # --- client -------------------------------------------------------------------------------------------
    def client(self):
        if self._client is None:
            if self.workspace_id and not WORKSPACE_ID.match(self.workspace_id):
                raise NotConfigured(ANTHROPIC_WORKSPACE_INVALID, provider=self.provider,
                                    code="anthropic.workspace_invalid")
            import anthropic
            common: Dict[str, Any] = dict(base_url=self.base_url, max_retries=0,  # retries: LLMClient
                                          timeout=self.timeout)
            if self.workspace_id:
                common["default_headers"] = {WORKSPACE_HEADER: self.workspace_id}
            if self.http_client is not None:
                common["http_client"] = self.http_client
            try:
                if self.auth == "ant_profile":
                    # An `ant auth login` profile (Anthropic's own sign-in), chosen explicitly by the user; the
                    # SDK reads and refreshes the token itself, this code never sees it. Without a name, the
                    # CLI's active profile is used.
                    from anthropic.lib.credentials import CredentialsFile
                    self._profile = self.oauth_profile or CredentialsFile().profile
                    self._client = anthropic.Anthropic(profile=self._profile, **common)
                elif self._api_key:
                    # Passing the key explicitly turns off the SDK's environment and profile fallbacks.
                    self._client = anthropic.Anthropic(api_key=self._api_key, **common)
                else:
                    raise NotConfigured("No Claude API key is stored. Add one in the AI settings.",
                                        provider=self.provider)
            except NotConfigured:
                raise
            except Exception as e:
                raise self._map_error(e) from None
        return self._client

    def server_fallbacks(self, model: str) -> bool:
        return is_claude_api(self.base_url) and _matches(model, self.preset.server_fallback_models)

    def supports_effort(self, model: str) -> bool:
        return not _matches(model, self.preset.no_effort_models)

    # --- request ------------------------------------------------------------------------------------------
    def build_params(self, request: ChatRequest, model: str, effort: Optional[str]) -> Dict[str, Any]:
        cache_control = None
        if request.cache:
            cache_control = {"type": "ephemeral"}
            if request.cache_ttl == "1h":
                cache_control["ttl"] = "1h"

        system_parts, messages = render_messages(request.messages, self.family)
        system_text = "\n\n".join(p for p in [request.system, *system_parts] if p)
        params: Dict[str, Any] = {
            "model": model,
            "max_tokens": request.max_tokens or DEFAULT_MAX_TOKENS,
            "messages": messages,
        }
        if system_text:
            block: Dict[str, Any] = {"type": "text", "text": system_text}
            if cache_control:
                block["cache_control"] = dict(cache_control)  # read point that survives changes later on
            params["system"] = [block]
        if request.tools:
            params["tools"] = [render_tool(t) for t in request.tools]
            params["tool_choice"] = {"type": "auto"}
        output_config: Dict[str, Any] = {}
        if effort and self.supports_effort(model):
            output_config["effort"] = effort
        if request.output_model is not None:
            schema = claude_schema(request.output_model.model_json_schema())
            if schema is None:
                raise NotConfigured(f"{request.output_model.__name__} cannot be expressed as a JSON schema for "
                                    "structured outputs.", provider=self.provider)
            output_config["format"] = {"type": "json_schema", "schema": schema}
        if output_config:
            params["output_config"] = output_config
        if cache_control:
            params["cache_control"] = cache_control  # automatic caching of the growing conversation
        return params

    def _complete(self, request: ChatRequest, model: str) -> ChatResult:
        effort = self.preset.map_effort(request.effort or self.preset.efforts.get(request.task) or "medium")
        params = self.build_params(request, model, effort)
        client = self.client()
        try:
            if self.server_fallbacks(model):
                response = client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **params)
            else:
                response = client.messages.create(**params)
        except Exception as e:
            raise self._map_error(e) from None
        return self.to_result(response, model)

    def to_result(self, response: Any, requested_model: str) -> ChatResult:
        blocks = [b.to_dict() for b in response.content]  # exactly as returned: thinking blocks, signatures
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        calls = [ToolCall(id=b["id"], name=b["name"], arguments=b.get("input") or {})
                 for b in blocks if b.get("type") == "tool_use"]
        stop = _STOP.get(response.stop_reason or "end_turn", "end")
        usage = response.usage
        result = ChatResult(
            text=text, tool_calls=calls, stop=stop, model=response.model or requested_model, provider=self.provider,
            usage=Usage(input_tokens=usage.input_tokens or 0, output_tokens=usage.output_tokens or 0,
                        cache_read_tokens=getattr(usage, "cache_read_input_tokens", None) or 0,
                        cache_write_tokens=getattr(usage, "cache_creation_input_tokens", None) or 0),
            message=Message(role="assistant", content=text, tool_calls=calls,
                            native=NativeMessage(family=self.family, payload={"role": "assistant", "content": blocks})),
        )
        if stop == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            explanation = getattr(details, "explanation", None) if details else None
            result.refusal_category, result.refusal_explanation = category, explanation
            reason = f" ({category})" if category else ""
            raise Refused(f"Claude declined this request{reason}.", category=category, explanation=explanation,
                          result=result, provider=self.provider)
        return result

    def list_models(self) -> List[ModelInfo]:
        try:
            models = []
            for m in self.client().models.list():
                caps = m.capabilities.model_dump(exclude_none=True) if getattr(m, "capabilities", None) else {}
                models.append(ModelInfo(id=m.id, display_name=getattr(m, "display_name", "") or m.id,
                                        max_input_tokens=getattr(m, "max_input_tokens", None),
                                        max_tokens=getattr(m, "max_tokens", None), capabilities=caps))
            return models
        except Exception as e:
            raise self._map_error(e) from None


def render_tool(tool: ToolSpecLLM) -> Dict[str, Any]:
    schema = claude_schema(tool.input_schema) if tool.strict else None
    entry: Dict[str, Any] = {"name": tool.name, "description": tool.description,
                             "input_schema": schema or tool.input_schema}
    if schema is not None:
        entry["strict"] = True
    return entry


def _blocks(content) -> Any:
    if isinstance(content, str):
        return content
    out = []
    for b in content:
        if b["type"] == "text":
            out.append({"type": "text", "text": b.get("text", "")})
        elif b["type"] == "image":
            out.append({"type": "image", "source": {"type": "base64", "media_type": b.get("media_type", "image/png"),
                                                    "data": b.get("data", "")}})
    return out


def render_messages(messages: List[Message], family: str = "anthropic"):
    """Neutral history -> (extra system text from leading system messages, Messages API turns).
    Consecutive tool results become one user turn with one tool_result block each."""
    system_parts: List[str] = []
    out: List[Dict[str, Any]] = []
    started = False
    for msg in messages:
        if msg.role == "system":
            if not started:
                system_parts.append(msg.text())
            else:
                # Mid-conversation operator note as user text: works on every model, and the history stays
                # append-only (the note is never removed).
                out.append({"role": "user", "content": f"[System note] {msg.text()}"})
            continue
        started = True
        if msg.role == "user":
            out.append({"role": "user", "content": _blocks(msg.content)})
        elif msg.role == "assistant":
            if msg.native is not None and msg.native.family == family:
                out.append(msg.native.payload)
            else:
                content: List[Dict[str, Any]] = []
                if msg.text():
                    content.append({"type": "text", "text": msg.text()})
                content += [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                            for c in msg.tool_calls]
                out.append({"role": "assistant", "content": content or [{"type": "text", "text": ""}]})
        elif msg.role == "tool":
            block = {"type": "tool_result", "tool_use_id": msg.tool_call_id, "content": msg.text()}
            if msg.is_error:
                block["is_error"] = True
            last = out[-1] if out else None
            if (last and last["role"] == "user" and isinstance(last["content"], list) and last["content"]
                    and all(b.get("type") == "tool_result" for b in last["content"])):
                last["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    return system_parts, out
