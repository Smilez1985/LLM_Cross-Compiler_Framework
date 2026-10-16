#!/usr/bin/env python3
"""
Provider, model and reasoning effort for one question or one run only: `llm-cli ditto ask --provider --model`,
`llm-cli ditto eval --provider --model --effort`, and in the GUI the chat panel's "For this question" choice and
Ditto > Evaluation... (W1': every CLI action by mouse). The saved AI settings stay unchanged.

- provider_problem(): whether a provider id may be used, with the words the CLI prints when not (a removed provider
  names its alternatives).
- override_values(): the settings an override changes (ai_provider, ai_model, every ai_effort key). The CLI sets them
  in its own short-lived process; the GUI, whose settings every window shares, never does.
- SettingsOverlay / ditto_with(): a Ditto that reads these values over the person's own settings, for the GUI. Every
  other setting is read live from the real settings; nothing is written to them (set() stays in the overlay).
"""

from types import SimpleNamespace
from typing import Any, Dict, Optional

EFFORTS = ("low", "medium", "high")   # `ditto eval --effort` (local models: low = no thinking)
# Every task's effort: the eval's effort applies to the whole run (chat, tools, structured answers, healing).
EFFORT_KEYS = ("ai_effort", "ai_effort_chat", "ai_effort_tools", "ai_effort_json", "ai_effort_healing")


def provider_problem(catalog: Any, provider: Optional[str]) -> Optional[str]:
    """Why `provider` cannot be used for a question (None when it can, or when none is named)."""
    if not provider or catalog.get(provider) is not None:
        return None
    removed = catalog.removed_message(provider)
    return removed or f"Unknown AI provider '{provider}'. Choose one of: {', '.join(catalog.ids())}."


def override_values(provider: Optional[str] = None, model: Optional[str] = None,
                    effort: Optional[str] = None) -> Dict[str, Any]:
    """The settings an override for one question or run changes (empty: none)."""
    values: Dict[str, Any] = {}
    if provider:
        values["ai_provider"] = provider
    if model:
        values["ai_model"] = model
    if effort:
        if effort not in EFFORTS:
            raise ValueError(f"Unknown effort '{effort}' (known: {', '.join(EFFORTS)}).")
        values.update({key: effort for key in EFFORT_KEYS})
    return values


class SettingsOverlay:
    """The person's settings with a few values replaced for one question or run. Reads every other key live from
    `base`; set() changes only the overlay, never the settings the rest of the program uses or saves."""

    def __init__(self, base: Any, values: Optional[Dict[str, Any]] = None):
        self._base = base
        self._values: Dict[str, Any] = dict(values or {})

    @property
    def values(self) -> Dict[str, Any]:
        return dict(self._values)

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._values:
            return self._values[key]
        return self._base.get(key, default) if self._base is not None else default

    def set(self, key: str, value: Any, source_type: str = "override") -> bool:
        self._values[key] = value
        return True

    @property
    def config_values(self) -> Optional[Dict[str, Any]]:
        """The base's entries (LLMClient tells values a person set from schema defaults by them) with the overlay's
        values as set ones; None when the base has no such entries (the client then reads with get())."""
        base = getattr(self._base, "config_values", None)
        if not isinstance(base, dict):
            return None
        merged = dict(base)
        for key, value in self._values.items():
            merged[key] = SimpleNamespace(value=value, source=SimpleNamespace(source_type="override"))
        return merged

    def __getattr__(self, name: str) -> Any:
        # user_state_dir, config_dir, revision() ...: the real settings' own
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._base, name)


def ditto_with(ditto: Any, values: Dict[str, Any]) -> Any:
    """A Ditto for one question or run with `values` over the person's settings: the same framework, keys, tools and
    permissions, its own provider client. The given Ditto and the saved settings stay as they are."""
    from orchestrator.Core.ditto_manager import DittoCoder
    return DittoCoder(getattr(ditto, "framework", None), config_manager=SettingsOverlay(ditto.config, values),
                      secrets_manager=getattr(ditto, "_secrets", None),
                      registry=getattr(ditto, "_registry_override", None))


__all__ = ["EFFORTS", "EFFORT_KEYS", "SettingsOverlay", "ditto_with", "override_values", "provider_problem"]
