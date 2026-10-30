#!/usr/bin/env python3
"""Shared adapter logic: JSON answers with local validation and one repair attempt, schema normalization."""

import copy
import re
from typing import Any, Dict, List, Optional, Tuple, Type, Union

from pydantic import BaseModel, ValidationError

from orchestrator.llm.catalog import ProviderPreset
from orchestrator.llm.errors import InvalidOutput, NotConfigured, Truncated
from orchestrator.llm.types import ChatRequest, ChatResult, Message, ModelInfo, json_dumps  # noqa: F401 (moved there)
from orchestrator.security.redaction import SecretStr, reveal

DEFAULT_MAX_TOKENS = 16000  # non-streaming; room for adaptive thinking plus the answer
REPAIR_PROMPT = ("Your previous reply did not match the required JSON schema: {errors}\n"
                 "Reply again with only the corrected JSON, nothing else.")
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def parse_output(text: str, model: Type[BaseModel]) -> Tuple[Optional[BaseModel], str]:
    """Validates a JSON answer. Returns (parsed, "") or (None, a short description of the problem)."""
    raw = (text or "").strip()
    fenced = _FENCE.match(raw)
    if fenced:
        raw = fenced.group(1)
    if not raw:
        return None, "the reply was empty"
    try:
        return model.model_validate_json(raw), ""
    except ValidationError as e:
        problems = []
        for err in e.errors()[:8]:
            loc = ".".join(str(p) for p in err.get("loc", ())) or "(root)"
            problems.append(f"{loc}: {err.get('msg')}")
        return None, "; ".join(problems)[:1000]


def strict_object_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """OpenAI-style strict schema: every object closed and every property required (optional ones stay
    nullable through their own anyOf/null), None defaults dropped, $ref with siblings inlined."""
    root = copy.deepcopy(schema)

    def resolve(ref: str) -> Dict[str, Any]:
        node: Any = root
        for part in ref[2:].split("/"):
            node = node[part]
        return copy.deepcopy(node)

    def walk(node: Any) -> Any:
        if not isinstance(node, dict):
            return node
        for key in ("$defs", "definitions"):
            if isinstance(node.get(key), dict):
                node[key] = {name: walk(sub) for name, sub in node[key].items()}
        if node.get("type") == "object" and "additionalProperties" not in node:
            node["additionalProperties"] = False
        if isinstance(node.get("properties"), dict):
            node["properties"] = {k: walk(v) for k, v in node["properties"].items()}
            node["required"] = list(node["properties"])
            node.setdefault("additionalProperties", False)
        if isinstance(node.get("items"), dict):
            node["items"] = walk(node["items"])
        for key in ("anyOf", "allOf", "oneOf"):
            if isinstance(node.get(key), list):
                node[key] = [walk(v) for v in node[key]]
        if "default" in node and node["default"] is None:
            node.pop("default")
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/") and len(node) > 1:
            merged = {**resolve(ref), **{k: v for k, v in node.items() if k != "$ref"}}
            return walk(merged)
        return node

    return walk(root)


def schema_name(model: Type[BaseModel]) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", model.__name__)[:64] or "answer"


class ProviderAdapter:
    """One configured endpoint. Subclasses implement _complete() and list_models()."""

    family = ""

    # Credential kinds (orchestrator/llm/auth/resolver.py) this adapter can build a client from.
    credential_kinds: Tuple[str, ...] = ("none", "api_key")

    def __init__(self, preset: ProviderPreset, *, base_url: Optional[str] = None,
                 api_key: Union[str, SecretStr, None] = None, credential: Any = None, http_client: Any = None,
                 timeout: float = 600.0, **options: Any):
        self.preset = preset
        self.base_url = base_url
        self._key = SecretStr.wrap(api_key)
        self.credential = credential  # the ResolvedCredential (its repr shows no value), None in direct use
        self.http_client = http_client
        self.timeout = timeout
        self.options = options

    def credential_headers(self, *skip: str) -> Dict[str, str]:
        """The credential's non-secret headers (catalog fields such as the OpenAI project), minus `skip`."""
        headers = getattr(self.credential, "headers", None) or {}
        lowered = {name.lower() for name in skip}
        return {k: v for k, v in headers.items() if k.lower() not in lowered}

    def check_credential_kind(self) -> None:
        """Refuses a credential this adapter cannot use yet (a token provider before its adapter exists)."""
        kind = getattr(self.credential, "kind", None)
        if kind is not None and kind not in self.credential_kinds:
            raise NotConfigured(f"{self.preset.label}: this adapter cannot use a '{kind}' credential yet.",
                                provider=self.provider, code="auth.not_built")

    @property
    def _api_key(self) -> Optional[str]:
        """The plain key, only for building the SDK client; the adapter holds it as a SecretStr."""
        return reveal(self._key)

    @property
    def provider(self) -> str:
        return self.preset.id

    def __repr__(self) -> str:  # never show the key
        return f"{type(self).__name__}(provider={self.provider!r}, base_url={self.base_url!r})"

    def chat(self, request: ChatRequest, model: str) -> ChatResult:
        result = self._complete(request, model)
        if request.output_model is None:
            return result
        return self._parse_with_repair(request, model, result)

    def _complete(self, request: ChatRequest, model: str) -> ChatResult:
        raise NotImplementedError

    def list_models(self) -> List[ModelInfo]:
        return [ModelInfo(id=m, display_name=m) for m in self.preset.suggested_models]

    def _parse_with_repair(self, request: ChatRequest, model: str, result: ChatResult) -> ChatResult:
        if result.stop == "max_tokens":
            raise Truncated("The answer was cut off before the JSON was complete. Raise max_tokens or lower the "
                            "effort.", provider=self.provider, result=result)
        if result.tool_calls:
            return result  # the model calls tools first; the JSON comes with the final answer
        parsed, problem = parse_output(result.text, request.output_model)
        if parsed is not None:
            result.parsed = parsed
            return result
        repair = Message.user(REPAIR_PROMPT.format(errors=problem))
        retry_request = request.model_copy(update={"messages": [*request.messages, *result.new_messages, repair]})
        second = self._complete(retry_request, model)
        if second.stop == "max_tokens":
            raise Truncated("The corrected answer was cut off before the JSON was complete.",
                            provider=self.provider, result=second)
        parsed, problem = parse_output(second.text, request.output_model)
        if parsed is None:
            raise InvalidOutput(f"The model did not return valid JSON for {request.output_model.__name__}: {problem}",
                                provider=self.provider)
        second.parsed = parsed
        second.usage = result.usage + second.usage
        # The failed attempt and the correction request stay in the history: the final answer (and its
        # thinking) was produced after them, so dropping them would edit the conversation prefix.
        second.new_messages = [*result.new_messages, repair, *second.new_messages]
        return second

