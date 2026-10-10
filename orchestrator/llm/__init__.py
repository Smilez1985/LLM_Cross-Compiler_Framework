"""
Ditto's provider layer: the official anthropic and openai SDKs behind one provider-neutral interface.

    client = LLMClient(framework.config, framework.secrets_manager)
    result = client.chat(ChatRequest(system=SYSTEM, messages=history, task="chat"))
    history += result.new_messages          # append-only; never rebuild earlier turns

Tool use: run_tool_loop(client, request, make_registry_executor(registry, ctx)).
Presets live in configs/llm_providers.yml; see client.py for the settings keys.
"""

from orchestrator.llm.catalog import Catalog, ProviderPreset, load_catalog
from orchestrator.llm.client import LLMClient, Target
from orchestrator.llm.errors import (AuthFailed, BadRequest, BillingError, InvalidOutput, LLMError, NotConfigured,
                                     PermissionDenied, ProviderTimeout, ProviderUnavailable, RateLimited, Refused,
                                     Truncated)
from orchestrator.llm.privacy import PrivacyFilter, restore, sanitize_for_cloud
from orchestrator.llm.tool_loop import ToolLoopResult, make_registry_executor, run_tool_loop, tool_specs_from_registry
from orchestrator.llm.types import (ChatRequest, ChatResult, Message, ModelInfo, NativeMessage, ToolCall, ToolSpecLLM,
                                    Usage)

__all__ = [
    "AuthFailed", "BadRequest", "BillingError", "Catalog", "ChatRequest", "ChatResult", "InvalidOutput", "LLMClient",
    "LLMError", "Message", "ModelInfo", "NativeMessage", "NotConfigured", "PermissionDenied", "PrivacyFilter",
    "ProviderPreset", "ProviderTimeout", "ProviderUnavailable", "RateLimited", "Refused", "Target", "ToolCall",
    "ToolLoopResult", "ToolSpecLLM", "Truncated", "Usage", "load_catalog", "make_registry_executor", "restore",
    "run_tool_loop", "sanitize_for_cloud", "tool_specs_from_registry",
]
