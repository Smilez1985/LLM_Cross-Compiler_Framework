#!/usr/bin/env python3
"""
Provider, model and reasoning effort for one question or one run only: `llm-cli ditto ask --provider --model`,
`llm-cli ditto eval --provider --model --effort`, and in the GUI the chat panel's "For this question" choice and
Ditto > Evaluation... (W1': every CLI action by mouse); also `llm-cli module generate-ai --provider --model` and the
module wizard's "for this analysis only" through DittoCoder.for_run. The saved AI settings stay unchanged. This is the
one code path for such a choice: ditto_manager.provider_problem and ditto_manager.RunSettings are these names.

- provider_problem(): whether a provider id may be used, with the words the CLI prints when not (a removed provider
  names its alternatives).
- override_values(): the settings an override changes (ai_provider; ai_model and every task's ai_model_<task>, since a
  per-task model wins over ai_model; every ai_effort key). Another provider than the one in effect clears what belongs
  to that one (its model and per-task models, ai_base_url, ai_auth), so a saved Ollama or custom server address never
  receives another provider's requests. The CLI sets the values in its own short-lived process with the source "cli",
  which save_user_config never writes (a settings save during the command, such as the update_settings tool, keeps the
  saved choice); the GUI, whose settings every window shares, never sets them.
- SettingsOverlay / ditto_with(): a Ditto that reads these values over the person's own settings, for the GUI and
  DittoCoder.for_run. Every other setting is read live from the real settings; nothing is written to them (set()
  stays in the overlay).
"""

from types import SimpleNamespace
from typing import Any, Dict, Optional

EFFORTS = ("low", "medium", "high")   # `ditto eval --effort` (local models: low = no thinking)
# Every task's effort: the eval's effort applies to the whole run (chat, tools, structured answers, healing).
EFFORT_KEYS = ("ai_effort", "ai_effort_chat", "ai_effort_tools", "ai_effort_json", "ai_effort_healing")
# The settings source of a one-command choice in the CLI process: ConfigManager.save_user_config never writes it.
CLI_SOURCE = "cli"
# What belongs to the provider in effect besides its per-task models: cleared ("" = not set) when another one is chosen.
PROVIDER_SETTINGS = ("ai_model", "ai_base_url", "ai_auth")


def provider_problem(catalog: Any, provider: Optional[str]) -> Optional[str]:
    """Why `provider` cannot be used for a question (None when it can, or when none is named)."""
    if not provider or catalog.get(provider) is not None:
        return None
    removed = catalog.removed_message(provider)
    return removed or f"Unknown AI provider '{provider}'. Choose one of: {', '.join(catalog.ids())}."


def _switches_provider(ditto: Any, provider: str) -> bool:
    """Whether `provider` is another provider than the one `ditto`'s settings use now (True when that cannot be told:
    no Ditto, no provider in effect, an unknown one)."""
    try:
        client = ditto.client
        chosen = client.catalog.resolve_id(provider) or provider
        return chosen != client.primary_provider().id
    except Exception:  # noqa: BLE001 - nothing of an unknown provider carries over
        return True


def override_values(provider: Optional[str] = None, model: Optional[str] = None,
                    effort: Optional[str] = None, *, ditto: Any = None) -> Dict[str, Any]:
    """The settings an override for one question or run changes (empty: none). `ditto`: the Ditto whose settings the
    values override; another provider than its own clears the settings that belong to that one (PROVIDER_SETTINGS and
    every ai_model_<task>). Without it a provider counts as another one. A model applies to every task."""
    from orchestrator.llm.types import TASKS
    values: Dict[str, Any] = {}
    if provider:
        values["ai_provider"] = provider
        if _switches_provider(ditto, provider):
            values.update({key: "" for key in PROVIDER_SETTINGS})
            values.update({f"ai_model_{task}": "" for task in TASKS})
    if model:
        values["ai_model"] = model
        values.update({f"ai_model_{task}": model for task in TASKS})   # a per-task model wins over ai_model
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
        values as set ones; None when the base has no such entries (the client then reads with get()). An overlay
        entry has the shape of ConfigManager's (key, value, schema of the base entry, is_secret, source) so every
        reader of config_values (the effective settings view too) reads it like any other."""
        base = getattr(self._base, "config_values", None)
        if not isinstance(base, dict):
            return None
        merged = dict(base)
        for key, value in self._values.items():
            merged[key] = SimpleNamespace(key=key, value=value, schema=getattr(base.get(key), "schema", None),
                                          is_secret=False,
                                          source=SimpleNamespace(source_type="override", source_path=None))
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


__all__ = ["CLI_SOURCE", "EFFORTS", "EFFORT_KEYS", "PROVIDER_SETTINGS", "SettingsOverlay", "ditto_with",
           "override_values", "provider_problem"]
