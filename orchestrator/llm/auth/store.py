#!/usr/bin/env python3
"""
CredentialStore: the providers' credentials on top of the credential vault (SecretsManager v2).

- Secret records sit encrypted in the vault as cred/<provider>/<method>[@<origin-hash>]: {"kind": "api_key",
  "secret": ...} or {"kind": "oauth", "access_token": ..., "refresh_token": ..., "expires_at": ...}.
- Non-secret metadata (account label, last four characters of a key, workspace ID and the other catalog fields,
  origin, times, state) sits in auth.json next to the vault, under auth.<provider>.<method>[@<origin-hash>].
  Strings pass through redact_secrets, so a secret written there by mistake is masked.
- A key of a preset whose base URL the user can change, or of a local engine, is bound to the origin it was stored
  for (R6): cred/<provider>/<method>@<origin-hash>. Changing the base URL never carries it over. A key stored
  without an origin (earlier versions, `llm-cli secrets set custom_api_key`) is bound to the origin it is first
  used with, and is not used when it cannot be bound.
- Environment variables are a source only as the catalog lists them (method `env`), only when nothing is stored,
  and always reported as their source (R8). Nothing here writes them to the vault.
"""

import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Union

from orchestrator.Core.secrets_manager import LEGACY_NAMES, credential_name, git_work_tree, normalize_origin
from orchestrator.llm.auth.errors import AuthError, auth_error, storage_error
from orchestrator.llm.auth.spec import KEY_TYPES, AuthMethodSpec, FieldSpec
from orchestrator.security.redaction import SecretStr
from orchestrator.utils.file_lock import LockTimeout, file_lock
from orchestrator.utils.helpers import redact_secrets
from orchestrator.utils.logging import get_logger

METADATA_FILE = "auth.json"
METADATA_VERSION = 1
SIGNED_OUT = "signed_out"
_LEGACY_BY_NAME: Dict[str, List[str]] = {}
for _old, _new in LEGACY_NAMES.items():
    _LEGACY_BY_NAME.setdefault(_new, []).append(_old)


@dataclass(frozen=True, repr=False)
class FoundSecret:
    """A key and where it came from: "vault" or "env:<NAME>"."""
    value: SecretStr
    source: str
    name: Optional[str] = None  # the vault entry or the variable

    def __repr__(self) -> str:
        return f"FoundSecret(source={self.source!r}, name={self.name!r})"


def vault_name(provider: str, method: str, origin: Optional[str] = None) -> str:
    """cred/<provider>/<method>[@<origin-hash>]; an AuthError for a provider id the vault cannot name."""
    try:
        return credential_name(provider, method, origin)
    except ValueError:
        raise auth_error("auth.field_invalid", provider=provider,
                         message=f"The provider id '{provider}' cannot name a stored credential: use lower-case "
                                 "letters, digits, '.', '_' or '-' (at most 64) in your llm_providers.yml.") from None


def display_origin(url: Optional[str]) -> str:
    """scheme://host:port of a URL for messages and metadata (never user info or a path); "" for no URL."""
    try:
        return normalize_origin(url or "")
    except (ValueError, TypeError):
        return ""


def last4(key: str) -> Optional[str]:
    """The last four characters, shown as ...a1b2; not for short keys, where they would be a large part."""
    return key[-4:] if len(key) >= 16 else None


def _clean(value: Any) -> Any:
    """JSON-safe, non-secret metadata values."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redact_secrets(value)[:500]
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value if isinstance(v, (str, int, float, bool))][:50]
    return redact_secrets(str(value))[:500]


class CredentialStore:
    """`secrets`: the SecretsManager (or a stand-in that only has get_secret/set_secret, like the AI dialog's
    overlay of an unsaved key). `state_dir` (a path or a callable returning one) holds auth.json when `secrets`
    has no vault file of its own; without either, metadata stays in memory."""

    def __init__(self, secrets: Any, *, state_dir: Union[None, str, Path, Callable[[], Any]] = None,
                 environ: Optional[Mapping[str, str]] = None, clock: Callable[[], float] = time.time):
        self.secrets = secrets
        self._state_dir = state_dir
        self.environ = os.environ if environ is None else environ
        self.clock = clock
        self.logger = get_logger("CredentialStore")
        self._memory: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._lock = threading.RLock()
        self._local_locks: Dict[str, threading.Lock] = {}
        self._warned: set = set()

    # --- the secrets object ---------------------------------------------------------------------------------
    @property
    def records_supported(self) -> bool:
        """The vault can hold records (and so bind keys to origins); stand-ins only answer get_secret."""
        return all(callable(getattr(self.secrets, name, None)) for name in ("get_record", "set_record",
                                                                            "delete_secret"))

    def _warn_once(self, key: str, message: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            self.logger.warning(message)

    def _read_secret(self, name: str) -> Optional[str]:
        getter = getattr(self.secrets, "get_secret", None)
        if not callable(getter):
            return None
        try:
            value = getter(name)
        except Exception as e:  # a broken store must not hide the other sources
            self._warn_once(f"read:{name}", f"Could not read the stored credential {name}: {type(e).__name__}")
            return None
        return value.strip() if isinstance(value, str) and value.strip() else None

    def _failure(self, provider: str) -> AuthError:
        error = getattr(self.secrets, "storage_error", None)
        if error is not None:
            return storage_error(error, provider)
        unreadable = getattr(self.secrets, "load_error", None)
        if isinstance(unreadable, str) and unreadable:
            return auth_error("storage.vault_unreadable", detail=unreadable, provider=provider)
        return auth_error("storage.write_failed", provider=provider,
                          message="The credential could not be stored in the vault (the log says why).")

    # --- metadata (auth.json) -------------------------------------------------------------------------------
    def metadata_path(self) -> Optional[Path]:
        """auth.json next to the vault; None (memory only) for an ephemeral vault, without a folder, or inside a
        git work tree (account labels must never be committed)."""
        if getattr(self.secrets, "storage_mode", None) == "ephemeral":
            return None
        secrets_file = getattr(self.secrets, "secrets_file", None)
        if isinstance(secrets_file, (str, Path)):
            path = Path(secrets_file).with_name(METADATA_FILE)
        else:
            base = self._state_dir() if callable(self._state_dir) else self._state_dir
            if not isinstance(base, (str, Path)):
                return None
            path = Path(base) / METADATA_FILE
        root = git_work_tree(path)
        if root is not None:
            self._warn_once("git", f"Sign-in details are kept in memory only: {path} would be inside the git work "
                                   f"tree {root}. Set LLM_FRAMEWORK_HOME to a folder outside any repository.")
            return None
        return path

    def _load(self, path: Path) -> Dict[str, Dict[str, Dict[str, Any]]]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, RecursionError) as e:
            self._warn_once(f"load:{path}", f"Sign-in details in {path} cannot be read ({type(e).__name__}); they "
                                            "are ignored and rewritten on the next change.")
            return {}
        auth = data.get("auth") if isinstance(data, dict) else None
        if not isinstance(auth, dict):
            return {}
        return {p: {m: dict(v) for m, v in methods.items() if isinstance(v, dict)}
                for p, methods in auth.items() if isinstance(methods, dict)}

    def _write(self, path: Path, auth: Dict[str, Any]) -> None:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{METADATA_FILE}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"version": METADATA_VERSION, "auth": auth}, f, indent=2, sort_keys=True)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _all(self) -> Dict[str, Dict[str, Dict[str, Any]]]:
        path = self.metadata_path()
        with self._lock:
            return self._load(path) if path is not None else json.loads(json.dumps(self._memory))

    def _change(self, change: Callable[[Dict[str, Dict[str, Dict[str, Any]]]], None]) -> None:
        path = self.metadata_path()
        with self._lock:
            if path is None:
                change(self._memory)
                return
            try:
                with file_lock(path.with_name(METADATA_FILE + ".lock")):
                    data = self._load(path)
                    change(data)
                    self._write(path, data)
            except (OSError, LockTimeout) as e:
                self.logger.warning(f"Sign-in details could not be saved to {path}: {type(e).__name__}")

    @staticmethod
    def entry_key(method: str, origin: Optional[str] = None) -> str:
        """auth.<provider>.<entry_key>: the method id, with @<origin-hash> for a key bound to an origin."""
        return credential_name("x", method, origin).split("/", 2)[2]

    def metadata(self, provider: str, method: str, origin: Optional[str] = None) -> Dict[str, Any]:
        return dict(self._all().get(provider, {}).get(self.entry_key(method, origin), {}))

    def all_metadata(self) -> Dict[str, Dict[str, Dict[str, Any]]]:
        return self._all()

    def update_metadata(self, provider: str, method: str, changes: Mapping[str, Any], origin: Optional[str] = None,
                        *, replace: bool = False) -> None:
        """Merges `changes` into the entry (None removes a key); `replace` starts from an empty entry."""
        key = self.entry_key(method, origin)

        def change(data):
            entry = {} if replace else dict(data.get(provider, {}).get(key, {}))
            for name, value in changes.items():
                if value is None:
                    entry.pop(name, None)
                else:
                    entry[name] = _clean(value)
            methods = data.setdefault(provider, {})
            if entry:
                methods[key] = entry
            else:
                methods.pop(key, None)
            if not methods:
                data.pop(provider, None)

        self._change(change)

    def remove_metadata(self, provider: str, method: str, origin: Optional[str] = None) -> None:
        self.update_metadata(provider, method, {}, origin, replace=True)

    def now(self) -> str:
        return datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(timespec="seconds")

    # --- records --------------------------------------------------------------------------------------------
    def get_record(self, provider: str, method: str, origin: Optional[str] = None) -> Optional[Dict[str, Any]]:
        name = vault_name(provider, method, origin)
        if self.records_supported:
            try:
                record = self.secrets.get_record(name)
            except Exception as e:
                self._warn_once(f"read:{name}", f"Could not read the stored credential {name}: {type(e).__name__}")
                return None
            return record if isinstance(record, dict) else None
        value = self._read_secret(name)
        return {"kind": "api_key", "secret": value} if value else None

    def save_record(self, provider: str, method: str, record: Dict[str, Any], *, origin: Optional[str] = None,
                    metadata: Optional[Mapping[str, Any]] = None, replace_metadata: bool = False) -> None:
        """Stores a record (raises an AuthError when the vault refuses); `metadata` goes to auth.json after."""
        if not self.records_supported:
            raise auth_error("storage.write_failed", provider=provider,
                             message="This credential store cannot hold sign-in records.")
        if not self.secrets.set_record(vault_name(provider, method, origin), record):
            raise self._failure(provider)
        if metadata is not None:
            self.update_metadata(provider, method, metadata, origin, replace=replace_metadata)

    def mark_signed_out(self, provider: str, method: str, origin: Optional[str] = None,
                        reason: Optional[str] = None) -> None:
        """The grant is gone (invalid_grant and the like): the record is deleted, the state says so until the
        next sign-in."""
        if self.records_supported:
            try:
                self.secrets.delete_secret(vault_name(provider, method, origin))
            except Exception as e:
                self.logger.warning(f"Could not delete the expired sign-in of {provider}: {type(e).__name__}")
        self.update_metadata(provider, method, {"state": SIGNED_OUT, "signed_out_at": self.now(),
                                                "signed_out_reason": reason}, origin)

    def is_signed_out(self, provider: str, method: str, origin: Optional[str] = None) -> bool:
        return self.metadata(provider, method, origin).get("state") == SIGNED_OUT

    @contextmanager
    def credential_lock(self, provider: str, method: str, origin: Optional[str] = None,
                        timeout: float = 30.0) -> Iterator[None]:
        """The cross-process lock of one credential (a token refresh in the GUI, the CLI and an MCP server at once)."""
        name = vault_name(provider, method, origin)
        vault_lock = getattr(self.secrets, "credential_lock", None)
        if callable(vault_lock):
            with vault_lock(name, timeout=timeout):
                yield
            return
        with self._lock:
            local = self._local_locks.setdefault(name, threading.Lock())
        if not local.acquire(timeout=timeout):
            raise LockTimeout(f"The credential {name} is being refreshed elsewhere; try again.")
        try:
            yield
        finally:
            local.release()

    # --- keys -----------------------------------------------------------------------------------------------
    def _unbound_names(self, preset: Any, spec: AuthMethodSpec) -> List[str]:
        """Names a key without an origin may have: the preset's `secret` first (the AI dialog's overlay answers
        to it; it belongs to the preset's first key method), the vault name, the key names of earlier versions."""
        base = vault_name(preset.id, spec.id)
        first = next((m for m in getattr(preset, "methods", []) if m.type in KEY_TYPES), None)
        secret = getattr(preset, "secret", None) if first is None or first.id == spec.id else None
        names = [n for n in (secret, base, *_LEGACY_BY_NAME.get(base, ())) if n]
        return list(dict.fromkeys(names))

    def _origin(self, preset: Any, url: Optional[str]) -> Optional[str]:
        if not url:
            return None
        origin = display_origin(url)
        if not origin:
            self._warn_once(f"origin:{preset.id}", f"{preset.label}: the address is not a plain http(s) URL, so no "
                                                   "stored key is sent to it.")
        return origin or None

    def _adopt(self, preset: Any, spec: AuthMethodSpec, value: str, origin: str) -> bool:
        """Binds a key stored without an origin to `origin` (moves it to the bound name)."""
        if not self.records_supported:
            return False
        try:
            if not self.secrets.set_record(vault_name(preset.id, spec.id, origin),
                                           {"kind": "api_key", "secret": value}):
                return False
            for name in self._unbound_names(preset, spec):
                self.secrets.delete_secret(name)
        except Exception as e:
            self.logger.warning(f"{preset.label}: the stored key could not be bound to {origin}: {type(e).__name__}")
            return False
        self.update_metadata(preset.id, spec.id, {"origin": origin, "last4": last4(value), "created_at": self.now(),
                                                  "source": "migrated"}, origin, replace=True)
        self.logger.info(f"{preset.label}: the stored key now belongs to {origin} only.")
        return True

    def find_secret(self, preset: Any, spec: AuthMethodSpec, base_url: Optional[str] = None) -> Optional[FoundSecret]:
        """The key for `spec`: from the vault (bound to the origin of `base_url` where the method binds keys),
        else from the first variable of the method's `env` list that is set. None when there is none."""
        if spec.type not in KEY_TYPES:
            return None
        try:
            found = self._stored(preset, spec, base_url)
        except AuthError as e:
            self._warn_once(f"name:{preset.id}", str(e))
            found = None
        if found is not None:
            return found
        for variable in spec.env:
            value = (self.environ.get(variable) or "").strip()
            if value:
                return FoundSecret(SecretStr(value), f"env:{variable}", variable)
        return None

    def _stored(self, preset: Any, spec: AuthMethodSpec, base_url: Optional[str]) -> Optional[FoundSecret]:
        if spec.binds_origin(preset):
            origin = self._origin(preset, base_url)
            if origin:
                for name in self._unbound_names(preset, spec):
                    value = self._read_secret(name)
                    if not value:
                        continue
                    if self._adopt(preset, spec, value, origin):
                        return FoundSecret(SecretStr(value), "vault", vault_name(preset.id, spec.id, origin))
                    if not self.records_supported:  # a read-only stand-in cannot bind it: used as it is
                        return FoundSecret(SecretStr(value), "vault", name)
                    self._warn_once(f"unbound:{preset.id}", f"{preset.label}: a stored key could not be bound to "
                                                            f"{origin}, so it is not sent.")
                    break
                bound = vault_name(preset.id, spec.id, origin)
                value = self._read_secret(bound)
                if value:
                    return FoundSecret(SecretStr(value), "vault", bound)
        else:
            for name in self._unbound_names(preset, spec):
                value = self._read_secret(name)
                if value:
                    return FoundSecret(SecretStr(value), "vault", name)
        return None

    def field_error(self, preset: Any, field: FieldSpec) -> AuthError:
        return auth_error(field.invalid_code or "auth.field_invalid", provider=preset.id, provider_label=preset.label,
                          field=field.label)

    def check_fields(self, preset: Any, spec: AuthMethodSpec, values: Mapping[str, Any]) -> Dict[str, Optional[str]]:
        """Trimmed field values; raises ValueError for an unknown field and an AuthError for a malformed value."""
        out: Dict[str, Optional[str]] = {}
        for field_id, raw in values.items():
            field = spec.field(field_id)
            if field is None:
                raise ValueError(f"{preset.label}: the sign-in method '{spec.id}' has no field '{field_id}'")
            value = str(raw).strip() if raw is not None else ""
            if value and not field.matches(value):
                raise self.field_error(preset, field)
            out[field_id] = value or None
        return out

    def save_api_key(self, preset: Any, spec: AuthMethodSpec, key: str, *, base_url: Optional[str] = None,
                     fields: Optional[Mapping[str, Any]] = None, source: str = "manual") -> Dict[str, Any]:
        """Stores a key typed or pasted by the user (trimmed, admin keys refused before anything is written) with
        its fields; a key of a method that binds keys is stored for the origin of `base_url`. Returns the new
        metadata. Raises an AuthError when the key or a field is refused or the vault cannot be written."""
        from orchestrator.llm.errors import rejected_key_error
        if spec.type not in KEY_TYPES:
            raise ValueError(f"{preset.label}: '{spec.id}' is not a key method")
        key = (key or "").strip()
        if not key:
            raise auth_error("auth.not_signed_in", provider=preset.id, message="No key was entered.")
        if spec.rejected(key):
            raise rejected_key_error(preset)
        values = self.check_fields(preset, spec, fields or {})
        origin = None
        if spec.binds_origin(preset):
            origin = self._origin(preset, base_url or preset.base_url)
            if not origin:
                raise auth_error("auth.field_invalid", provider=preset.id, provider_label=preset.label,
                                 field="server address (the key is stored for one address)")
        name = vault_name(preset.id, spec.id, origin)
        if self.records_supported:
            if not self.secrets.set_record(name, {"kind": "api_key", "secret": key}):
                raise self._failure(preset.id)
            if origin:  # a key left without an origin would otherwise replace this one when it is next used
                for legacy in self._unbound_names(preset, spec):
                    self.secrets.delete_secret(legacy)
        else:
            setter = getattr(self.secrets, "set_secret", None)
            if not callable(setter) or not setter(getattr(preset, "secret", None) or name, key):
                raise self._failure(preset.id)
        self.update_metadata(preset.id, spec.id, {"origin": origin, "last4": last4(key), "created_at": self.now(),
                                                  "source": source, **values}, origin, replace=True)
        return self.metadata(preset.id, spec.id, origin)

    def key_source(self, preset: Any, spec: AuthMethodSpec, base_url: Optional[str] = None) -> Optional[str]:
        """Where the key of `spec` would come from ("vault", "env:NAME") without revealing it; None for none."""
        found = self.find_secret(preset, spec, base_url)
        return found.source if found else None

    def delete_key(self, preset: Any, spec: AuthMethodSpec, base_url: Optional[str] = None) -> bool:
        """Signs out locally: deletes the stored key (for the origin of `base_url` where keys are bound) and its
        metadata. True when something was deleted. A key in an environment variable stays where it is."""
        deleter = getattr(self.secrets, "delete_secret", None)
        if not callable(deleter):
            return False
        if spec.binds_origin(preset):
            origin = self._origin(preset, base_url or preset.base_url)
            names = [vault_name(preset.id, spec.id, origin)] if origin else []
        else:
            origin, names = None, self._unbound_names(preset, spec)
        deleted = False
        for name in names:
            try:
                deleted = bool(deleter(name)) or deleted
            except Exception as e:
                raise auth_error("storage.write_failed", provider=preset.id,
                                 message=f"The stored key could not be deleted ({type(e).__name__}).") from None
        self.remove_metadata(preset.id, spec.id, origin)
        return deleted
