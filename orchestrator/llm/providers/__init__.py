"""Provider adapters: one class per API family (see configs/llm_providers.yml for the presets)."""

from orchestrator.llm.providers.base import ProviderAdapter


def adapter_class(name: str):
    """Imported lazily so that a missing optional SDK only matters for the provider that needs it."""
    if name == "anthropic":
        from orchestrator.llm.providers.anthropic import AnthropicAdapter
        return AnthropicAdapter
    if name == "openai_compat":
        from orchestrator.llm.providers.openai_compat import OpenAICompatAdapter
        return OpenAICompatAdapter
    if name == "openai_responses":
        from orchestrator.llm.providers.openai_responses import OpenAIResponsesAdapter
        return OpenAIResponsesAdapter
    if name == "claude_code":
        from orchestrator.llm.providers.claude_code import ClaudeCodeAdapter
        return ClaudeCodeAdapter
    raise ValueError(f"Unknown adapter '{name}'")


__all__ = ["ProviderAdapter", "adapter_class"]
