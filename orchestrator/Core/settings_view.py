#!/usr/bin/env python3
"""
The framework's settings as the person sees them, and the source repository overrides: the one code path behind
`llm-cli config show`, `llm-cli config repos` and the GUI's File > Settings... window.

effective_settings() lists every setting with its value and where the value comes from (the defaults, a settings
file, an LLM_FRAMEWORK_* variable, or changed in this run); secrets are redacted (a setting the schema marks as a
secret, a setting whose name names a credential, and every value a secret pattern recognizes). The repository
overrides are kept in the setting source_repositories; builds receive each one as <NAME>_REPO_OVERRIDE.

A repository URL never takes a credential (maintainer, wave 5): config.yml is plain text, so add_repo_override refuses
a URL with a password or token in it (url_credential) and names the secrets store instead. A URL stored earlier with
one still works and is only ever shown with the credential replaced (shown_repo_url).
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, unquote_plus, urlsplit

from orchestrator.utils.helpers import redact_secrets

# Settings that are not part of the schema but look like credentials are never printed.
SECRET_NAME = re.compile(r"(?i)(^|[_.-])(token|password|passwd|secret|api_?key|access_key|private_key|credentials?)"
                         r"($|[_.-])")
REPOS_SETTING = "source_repositories"
# Parameters of a URL that carry a credential (?private_token=, ?access_token=, #token=, ;jsessionid=, ?sig=, ...)
CREDENTIAL_PARAMETER = re.compile(
    r"(?i)token|key|secret|pass(word|wd)?|pwd|auth|sig(nature)?|credential|sess|jwt|^sid$")
# A user name no person types: a token used as the user name (https://<token>@host/...)
TOKEN_LIKE_USER = re.compile(r"[A-Za-z0-9_\-]{30,}")
CREDENTIAL_IN_URL = "credential_in_url"


def redacted(value: Any) -> Any:
    """`value` with every secret pattern redacted; below a key that names a credential, the whole value."""
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):  # below a key that names a credential, even a value no pattern recognizes
        return {k: "REDACTED" if v and SECRET_NAME.search(str(k)) else redacted(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redacted(v) for v in value]
    return value


def _parameter_names(parts: Any) -> List[str]:
    return [unquote_plus(item.split("=", 1)[0]).strip() for part in (parts.path, parts.query, parts.fragment)
            for item in re.split(r"[;&]", part) if "=" in item]


def url_credential(url: str) -> Optional[str]:
    """What credential the repository URL `url` carries, or None: "password" (user:password@), "token" (a token as
    the user name, or a known token format anywhere in it) or "parameter" (a credential parameter such as
    ?private_token=). A plain user name, such as the account name of an SSH address, is no credential."""
    text = str(url or "").strip()
    if "://" not in text:   # scp-like address user@host:path
        userinfo = text.rsplit("@", 1)[0] if "@" in text else ""
        if ":" in userinfo:
            return "password"
        if userinfo and _token_like(userinfo):
            return "token"
        return "token" if redact_secrets(text) != text else None
    try:
        parts = urlsplit(text)
        user, password = parts.username, parts.password
    except ValueError:
        return None   # not a URL a build could clone either; the build reports it
    if password is not None:
        return "password"
    if user and _token_like(user):
        return "token"
    if any(CREDENTIAL_PARAMETER.search(name) for name in _parameter_names(parts)):
        return "parameter"
    return "token" if redact_secrets(text) != text else None


def _redacted_parameter(m: "re.Match[str]") -> str:
    secret = CREDENTIAL_PARAMETER.search(unquote_plus(m.group(2)).strip())
    return f"{m.group(1)}{m.group(2)}=" + ("REDACTED" if secret else m.group(3))


def _token_like(user: str) -> bool:
    user = unquote(user)
    return bool(TOKEN_LIKE_USER.fullmatch(user)) or redact_secrets(user) != user


def _redacted_userinfo(m: "re.Match[str]") -> str:
    scheme, userinfo = m.group(1) or "", m.group(2)
    user, colon, _ = userinfo.partition(":")
    if colon:   # user:password@ keeps the user name
        return f"{scheme}{user}:REDACTED@"
    return f"{scheme}REDACTED@" if _token_like(user) else m.group(0)


def shown_repo_url(url: str) -> str:
    """The repository URL as the person sees it: any credential url_credential finds is replaced by REDACTED (the
    password of user:password@, a token used as the user name, the value of a credential parameter), every other
    secret format too."""
    text = str(url or "")
    if url_credential(text) is not None:
        text = re.sub(r"^([A-Za-z][A-Za-z0-9+.\-]*://)?([^/?#@]*)@", _redacted_userinfo, text)
        text = re.sub(r"([?&#;])([^=&#;]*)=([^&#;]*)", _redacted_parameter, text)
    return redact_secrets(text)


def _shown_repos(value: Any) -> Any:
    """The setting source_repositories as `config show` prints it: every URL through shown_repo_url."""
    if not isinstance(value, dict):
        return redacted(value)
    return {k: ({**redacted(v), "url": shown_repo_url(v.get("url"))} if isinstance(v, dict) and "url" in v
                else shown_repo_url(v) if isinstance(v, str) else redacted(v)) for k, v in value.items()}


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
        elif key == REPOS_SETTING:   # a URL stored with a credential before such URLs were refused
            value = _shown_repos(entry.value)
        else:
            value = redacted(entry.value)
        result[key] = {"value": value, "source": source}
    return result


class RepoOverrideError(ValueError):
    """A repository override was not changed. code: "invalid_name" (the name gives no <NAME>_REPO_OVERRIDE),
    "credential_in_url" (the URL carries a password or token; `kind` says which) or "not_saved" (config.yml could not
    be written; the setting is unchanged)."""

    def __init__(self, message: str, code: str, kind: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.kind = kind


def repo_overrides(config: Any) -> Dict[str, str]:
    """The source repository overrides: name -> URL (also the plain URLs earlier versions stored), as stored (the
    URL may hold a credential stored before such URLs were refused: show it through shown_repo_url)."""
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
    variable, the URL carries a password or token (nothing is saved: config.yml is plain text) or nothing was
    saved."""
    from orchestrator.Core.builder import repo_override_names
    variables = repo_override_names(name)
    if not variables:  # the build engine could not pass it on under any name
        raise RepoOverrideError(f"Invalid name {name!r}: it needs ASCII letters or digits (it becomes "
                                "<NAME>_REPO_OVERRIDE, e.g. llama.cpp -> LLAMA_CPP_REPO_OVERRIDE).", "invalid_name")
    kind = url_credential(url)
    if kind is not None:
        what = "a password" if kind == "password" else "a token"
        raise RepoOverrideError(
            f"Not added: the URL contains {what} ({shown_repo_url(url)}). config.yml is plain text, so a repository "
            "URL never takes a credential: keep passwords and tokens in the secrets store ('llm-cli secrets set "
            "NAME') and enter the URL without them.", CREDENTIAL_IN_URL, kind)
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


__all__ = ["CREDENTIAL_IN_URL", "REPOS_SETTING", "RepoOverrideError", "SECRET_NAME", "add_repo_override",
           "effective_settings", "redacted", "remove_repo_override", "repo_overrides", "shown_repo_url",
           "url_credential"]
