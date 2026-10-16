#!/usr/bin/env python3
"""
Provider and model fields for one question or one run (the chat panel's "Options for this question" and Ditto >
Evaluation...; `llm-cli ditto ask/eval --provider --model`). The first entry of each field is "as in the AI settings"
(no override); the providers are the catalogue's (the same ids the CLI accepts), the models the provider's suggested
ones, and any other model name can be typed. Nothing here changes the saved settings.
"""

from typing import Any, Optional, Tuple

from PySide6.QtWidgets import QComboBox

from orchestrator.gui.plain import one_line


def catalog_of(ditto: Any, config: Any = None) -> Any:
    """The provider catalogue Ditto uses (the CLI checks --provider against it); None when it cannot be read."""
    try:
        if ditto is not None:
            return ditto.client.catalog
        from orchestrator.llm import load_catalog
        return load_catalog(user_dir=getattr(config, "config_dir", None))
    except Exception:  # noqa: BLE001 - no provider list: only "as in the AI settings" is offered
        return None


def fill_providers(combo: QComboBox, catalog: Any, settings_text: str) -> None:
    """"As in the AI settings" first (data ""), then every provider of the catalogue by its label (data: its id)."""
    current = combo.currentData() or ""
    combo.blockSignals(True)
    combo.clear()
    combo.addItem(settings_text, "")
    for pid in (catalog.ids() if catalog is not None else []):
        preset = catalog.get(pid)
        label = getattr(preset, "label", "") or pid
        combo.addItem(one_line(f"{label} ({pid})" if label != pid else pid), pid)
    index = combo.findData(current)
    combo.setCurrentIndex(index if index >= 0 else 0)
    combo.blockSignals(False)


def fill_models(combo: QComboBox, catalog: Any, provider: str) -> None:
    """The provider's suggested models in an editable field ("" stands for the AI settings); a typed name stays."""
    typed = combo.currentText()
    combo.blockSignals(True)
    combo.clear()
    combo.addItem("")
    preset = catalog.get(provider) if (catalog is not None and provider) else None
    for name in (getattr(preset, "suggested_models", None) or []):
        combo.addItem(one_line(name))
    combo.setEditText(typed)
    combo.blockSignals(False)


def chosen(provider_combo: QComboBox, model_combo: QComboBox) -> Tuple[Optional[str], Optional[str]]:
    """(provider, model) for the override; None for each that stays as in the AI settings."""
    provider = provider_combo.currentData() or None
    model = model_combo.currentText().strip() or None
    return provider, model


def is_local(catalog: Any, provider: Optional[str]) -> Optional[bool]:
    """Whether `provider` runs on this computer (True), in the cloud (False), or is not known (None)."""
    preset = catalog.get(provider) if (catalog is not None and provider) else None
    if preset is None:
        return None
    if getattr(preset, "runtime_endpoint", False) or getattr(preset, "location", "") == "local":
        return True   # "auto" (a configurable address, local or not) stays unknown
    return False if getattr(preset, "location", "") == "cloud" else None


__all__ = ["catalog_of", "chosen", "fill_models", "fill_providers", "is_local"]
