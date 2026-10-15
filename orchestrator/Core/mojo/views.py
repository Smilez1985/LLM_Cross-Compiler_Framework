#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - What an AI reads of Mojo's findings

The framework's folders by label and any other path by its file name only, secrets redacted; the person's own
output (the CLI, `--json` included) keeps the full paths.
"""

from pathlib import Path
from typing import Any, Dict, List

from orchestrator.Core.mojo.findings import MojoReport


def ai_folder_labels(framework) -> Dict[str, str]:
    """The framework's own folders by the label an AI may read: state (the per-user folder), cache, output, models,
    targets, community and framework (the installation)."""
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
    for key, label in (("cache_dir", "cache"), ("output_dir", "output"), ("models_dir", "models")):
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


def ai_findings(framework, report: MojoReport) -> List[Dict[str, Any]]:
    """The findings as an AI may read them (the run_doctor tool): every message and fix hint, a crashed check's
    included, with the framework's folders named by label ('the state folder', 'state:audit.db') and any other path
    by its file name only - no host path, no user name - and secrets redacted. The person's own output (the CLI,
    `--json` included) keeps the full paths."""
    from orchestrator.tools.tools_runtime import label_paths
    from orchestrator.utils.helpers import redact_secrets
    roots = ai_folder_labels(framework)

    def label(text: str) -> str:
        return label_paths(redact_secrets(str(text or "")), roots)

    return [{"subsystem": f.subsystem, "code": f.code, "severity": f.severity.value, "message": label(f.message),
             "fix_hint": label(f.fix_hint)} for f in report.findings]
