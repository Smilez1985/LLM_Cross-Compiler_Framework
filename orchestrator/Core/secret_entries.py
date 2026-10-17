#!/usr/bin/env python3
"""
Storing and listing named secrets: the shared steps behind `llmcf secrets set|list` and the GUI's Security >
Secrets... window (W1' of 2026-10-16: every CLI action also by mouse).

- store_secret(): stores a value under a name in the credential vault and says whether it was really saved: in the
  ephemeral storage (LLM_SECRET_STORAGE=ephemeral, for CI) it is kept in memory for this run only, and the person is
  told that nothing was saved instead of "stored".
- secret_entries(): the secret names the framework reads (hf_token, qdrant_api_key, the community pack keys) and every
  name stored in the vault, each with "stored" or "not stored" and what it is used for. Never a value.

The provider keys of Ditto (`cred/<provider>/<method>...`) are listed by name; they are signed in and out in the
sign-in panel (`llmcf ditto login`). This module imports nothing heavy: the pack key names are kept here as text
(tests/test_signin_steps.py checks them against orchestrator/Core/community_pack.py).
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# The names the framework reads, and what each one is for (the text keys are in orchestrator/utils/l10n_signin.py).
KNOWN_SECRETS: Dict[str, str] = {
    "hf_token": "secrets.what.hf_token",
    "qdrant_api_key": "secrets.what.qdrant_api_key",
    "community_signing_key": "secrets.what.community_signing_key",
    "community_age_identity": "secrets.what.community_age_identity",
}
PROVIDER_PREFIX = "cred/"
KIND_KNOWN, KIND_PROVIDER, KIND_OTHER = "known", "provider", "other"


class SecretsUnavailable(Exception):
    """The credential vault is not available in this process (no SecretsManager)."""


class SecretNotStored(Exception):
    """The vault refused the name or the value, or could not be written (the log says why; never the value)."""

    def __init__(self, name: str):
        super().__init__(f"Failed to store secret '{name}'.")
        self.name = name


@dataclass(frozen=True)
class StoredSecret:
    name: str
    memory_only: bool  # ephemeral storage: kept in memory for this run only, nothing was saved


@dataclass(frozen=True)
class SecretEntry:
    """One secret name: whether a value is stored, and what kind of name it is. Holds no value."""
    name: str
    stored: bool
    kind: str                       # known | provider | other
    what_key: Optional[str] = None  # the text key of what it is used for (known names)
    provider: Optional[str] = None  # cred/<provider>/...: the provider id

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "stored": self.stored, "kind": self.kind, "provider": self.provider}


def store_secret(secrets_manager: Any, name: str, value: str) -> StoredSecret:
    """Stores `value` under `name` (any printable name, as `llmcf secrets set` allows). Raises SecretsUnavailable
    without a vault and SecretNotStored when the vault refuses; the value is never part of an error."""
    if secrets_manager is None:
        raise SecretsUnavailable("SecretsManager not available!")
    if not secrets_manager.set_secret(name, value):
        raise SecretNotStored(name)
    return StoredSecret(name, getattr(secrets_manager, "storage_mode", None) == "ephemeral")


def stored_names(secrets_manager: Any) -> List[str]:
    """The names stored in the vault (never values), as `llmcf secrets list` prints them."""
    if secrets_manager is None:
        raise SecretsUnavailable("SecretsManager not available!")
    return list(secrets_manager.list_secrets())


def secret_entries(secrets_manager: Any) -> List[SecretEntry]:
    """The known names first (stored or not), then every other stored name in the vault's order."""
    stored = stored_names(secrets_manager)
    present = set(stored)
    entries = [SecretEntry(name, name in present, KIND_KNOWN, what) for name, what in KNOWN_SECRETS.items()]
    for name in stored:
        if name in KNOWN_SECRETS:
            continue
        if name.startswith(PROVIDER_PREFIX):
            provider = name[len(PROVIDER_PREFIX):].split("/", 1)[0] or None
            entries.append(SecretEntry(name, True, KIND_PROVIDER, provider=provider))
        else:
            entries.append(SecretEntry(name, True, KIND_OTHER))
    return entries


__all__ = ["KNOWN_SECRETS", "KIND_KNOWN", "KIND_OTHER", "KIND_PROVIDER", "SecretEntry", "SecretNotStored",
           "SecretsUnavailable", "StoredSecret", "secret_entries", "store_secret", "stored_names"]
