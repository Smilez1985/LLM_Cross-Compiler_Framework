"""
Ditto's provider layer: the official anthropic and openai SDKs behind one provider-neutral interface.

    client = LLMClient(framework.config, framework.secrets_manager)
    result = client.chat(ChatRequest(system=SYSTEM, messages=history, task="chat"))
    history += result.new_messages          # append-only; never rebuild earlier turns

Tool use: run_tool_loop(client, request, make_registry_executor(registry, ctx)).
Presets live in configs/llm_providers.yml; see client.py for the settings keys.
"""

from orchestrator.utils.lazy_imports import lazy_attributes

# Lazy loading: `from orchestrator.llm import LLMClient` imports the provider layer on first use, so importing a
# light submodule (orchestrator.llm.sdk_env, which every CLI command uses) does not load all of it.
_SOURCES = {
    "orchestrator.llm.catalog": ("Catalog", "ProviderPreset", "load_catalog"),
    "orchestrator.llm.client": ("LLMClient", "Target"),
    "orchestrator.llm.errors": ("AuthFailed", "BadRequest", "BillingError", "InvalidOutput", "LLMError",
                                "NotConfigured", "PermissionDenied", "ProviderTimeout", "ProviderUnavailable",
                                "RateLimited", "Refused", "Truncated"),
    "orchestrator.llm.privacy": ("PrivacyFilter", "restore", "sanitize_for_cloud"),
    "orchestrator.llm.tool_loop": ("ToolLoopResult", "make_registry_executor", "run_tool_loop",
                                   "tool_specs_from_registry"),
    "orchestrator.llm.types": ("ChatRequest", "ChatResult", "Message", "ModelInfo", "NativeMessage", "ToolCall",
                               "ToolSpecLLM", "Usage"),
}
__getattr__ = lazy_attributes(__name__, {name: module for module, names in _SOURCES.items() for name in names})

__all__ = [
    "AuthFailed", "BadRequest", "BillingError", "Catalog", "ChatRequest", "ChatResult", "InvalidOutput", "LLMClient",
    "LLMError", "Message", "ModelInfo", "NativeMessage", "NotConfigured", "PermissionDenied", "PrivacyFilter",
    "ProviderPreset", "ProviderTimeout", "ProviderUnavailable", "RateLimited", "Refused", "Target", "ToolCall",
    "ToolLoopResult", "ToolSpecLLM", "Truncated", "Usage", "load_catalog", "make_registry_executor", "restore",
    "run_tool_loop", "sanitize_for_cloud", "tool_specs_from_registry",
]
