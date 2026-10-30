#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - The framework's own folders by the label an AI may read

A leaf module (it imports nothing of the framework), so any part of the Core can name its folders by label without
closing an import loop: Mojo's views, BuildService's build log for an AI and Ditto's healing prompt read the same
labels.
"""

from pathlib import Path
from typing import Any, Dict


def ai_folder_labels(framework) -> Dict[str, str]:
    """The framework's own folders by the label an AI may read: state (the per-user folder), cache, output, builds
    (the framework's build folder), models, targets, community and framework (the installation)."""
    roots: Dict[str, str] = {}

    def add(path: Any, label: str) -> None:
        try:
            if path:
                roots.setdefault(str(Path(path)), label)
        except (TypeError, ValueError):
            pass

    config = getattr(framework, "config", None)
    try:
        add(getattr(config, "user_state_dir", None), "state")
    except Exception:  # noqa: BLE001 - a config that cannot name it: no label, the file name rule still applies
        pass
    engine = getattr(getattr(framework, "orchestrator", None), "build_engine", None)
    add(getattr(engine, "cache_dir", None), "cache")
    add(getattr(engine, "output_dir", None), "output")
    resolve = getattr(framework, "resolve_path", None)
    for key, label in (("cache_dir", "cache"), ("output_dir", "output"), ("build_dir", "builds"),
                       ("models_dir", "models")):
        try:
            value = config.get(key, None) if config is not None else None
            if value:
                add(resolve(value) if callable(resolve) else value, label)
        except Exception:  # noqa: BLE001
            continue
    add(getattr(getattr(framework, "target_manager", None), "targets_dir", None), "targets")
    add(getattr(getattr(framework, "community_manager", None), "community_dir", None), "community")
    add(getattr(getattr(framework, "info", None), "installation_path", None), "framework")
    add(getattr(framework, "app_root", None), "framework")
    return roots


__all__ = ["ai_folder_labels"]
