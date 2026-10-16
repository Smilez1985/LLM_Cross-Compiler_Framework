#!/usr/bin/env python3
"""
The framework's settings as the person sees them, and the source repository overrides: the one code path behind
`llm-cli config show`, `llm-cli config repos` and the GUI's File > Settings... window.

effective_settings() lists every setting with its value and where the value comes from (the defaults, a settings
file, an LLM_FRAMEWORK_* variable, or changed in this run); secrets are redacted (a setting the schema marks as a
secret, a setting whose name names a credential, and every value a secret pattern recognizes). The repository
overrides are kept in the setting source_repositories; builds receive each one as <NAME>_REPO_OVERRIDE.
"""

import re
from pathlib import Path
from typing import Any, Dict

from orchestrator.utils.helpers import redact_secrets

# Settings that are not part of the schema but look like credentials are never printed.
SECRET_NAME = re.compile(r"(?i)(^|[_.-])(token|password|passwd|secret|api_?key|access_key|private_key|credentials?)"
                         r"($|[_.-])")
REPOS_SETTING = "source_repositories"


def redacted(value: Any) -> Any:
    """`value` with every secret pattern redacted; below a key that names a credential, the whole value."""
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):  # below a key that names a credential, even a value no pattern recognizes
        return {k: "REDACTED" if v and SECRET_NAME.search(str(k)) else redacted(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redacted(v) for v in value]
    return value


def effective_settings(config: Any) -> Dict[str, Dict[str, Any]]:
    """Every setting with its value and source (default, a file, the environment, or changed in this run), sorted by
    name: {name: {"value": ..., "source": "default" | "file <path>" | "environment" | ...}}."""
    result = {}
    values = getattr(config, "config_values", {})
    for key in sorted(values):
        entry = values[key]
        src = entry.source
        source = f"file {src.source_path}" if src.source_type == "file" and src.source_path else src.source_type
        unknown = getattr(entry, "schema", None) is None
        if getattr(entry, "is_secret", False) or (unknown and SECRET_NAME.search(key) and entry.value):
            value = "REDACTED"
        else:
            value = redacted(entry.value)
        result[key] = {"value": value, "source": source}
    return result


class RepoOverrideError(ValueError):
    """A repository override was not changed. code: "invalid_name" (the name gives no <NAME>_REPO_OVERRIDE) or
    "not_saved" (config.yml could not be written; the setting is unchanged)."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def repo_overrides(config: Any) -> Dict[str, str]:
    """The source repository overrides: name -> URL (also the plain URLs earlier versions stored), as stored (the
    URL may hold a credential: show it through redact_secrets)."""
    current = config.get(REPOS_SETTING, {}) or {}
    if not isinstance(current, dict):
        return {}
    return {str(k): str((v.get("url") if isinstance(v, dict) else v) or "-") for k, v in current.items()}


def _save(config: Any, repos: Dict[str, Any], before: Any) -> None:
    if not (config.set(REPOS_SETTING, repos) and config.save_user_config()):
        config.set(REPOS_SETTING, before)   # the setting stays as it was, also in this program
        raise RepoOverrideError(f"Not saved: {Path(config.config_dir) / 'config.yml'} could not be updated (see the "
                                "log); the repository overrides are unchanged.", "not_saved")


def add_repo_override(config: Any, name: str, url: str) -> str:
    """Adds (or replaces) the override `name` -> `url` in the shape the build engine passes on as
    <NAME>_REPO_OVERRIDE, saves config.yml and returns that variable's name. RepoOverrideError when the name gives no
    variable or nothing was saved."""
    from orchestrator.Core.builder import repo_override_names
    variables = repo_override_names(name)
    if not variables:  # the build engine could not pass it on under any name
        raise RepoOverrideError(f"Invalid name {name!r}: it needs ASCII letters or digits (it becomes "
                                "<NAME>_REPO_OVERRIDE, e.g. llama.cpp -> LLAMA_CPP_REPO_OVERRIDE).", "invalid_name")
    before = config.get(REPOS_SETTING, {}) or {}
    repos = dict(before) if isinstance(before, dict) else {}
    # The shape the build engine passes on as <NAME>_REPO_OVERRIDE (plain URLs of earlier versions still work).
    repos[name.lower()] = {"url": url}
    _save(config, repos, before)
    return variables[0]


def remove_repo_override(config: Any, name: str) -> bool:
    """Removes the override `name` (any letter case) and saves config.yml; False when there is none of that name.
    RepoOverrideError when nothing was saved."""
    before = config.get(REPOS_SETTING, {}) or {}
    repos = dict(before) if isinstance(before, dict) else {}
    if name.lower() not in repos:
        return False
    del repos[name.lower()]
    _save(config, repos, before)
    return True


__all__ = ["REPOS_SETTING", "RepoOverrideError", "SECRET_NAME", "add_repo_override", "effective_settings",
           "redacted", "remove_repo_override", "repo_overrides"]
