#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Secrets Manager (credential vault v2)

Every secret (API keys, tokens, sign-in records) sits encrypted in one vault file in the per-user folder
(`secrets.store` next to the tool permissions and the audit log; LLM_FRAMEWORK_HOME moves it), the same for
the GUI, the CLI and the MCP server. Only the vault key needs protection: by default the OS keyring holds it;
other protections are chosen explicitly (orchestrator/Core/vault_key.py).

Format v2 (Fernet: AES-128-CBC with HMAC-SHA256; MultiFernet reads entries of the previous key during a
rotation):
  {"version": 2, "protection": "keyring", "vault_id": "<keyring entry of this vault>", "kid": "<current key id>",
   "kdf": {...}, "check": "<token>", "entries": {"<name>": {"kid": "<key id>", "ct": "<Fernet token>"}}}

Entry names: credentials are `cred/<provider>/<method>[@<origin-hash>]` and hold a JSON record
({"v": 1, "kind": "api_key", "secret": ...}); other secrets (hf_token, github_token, ...) keep their names and
hold the plain value. The provider keys of earlier versions (anthropic_api_key, ...) are read under their new
names and move there on the next write. Stores of version 1 (flat {name: token}) are read and rewritten as v2
on the next write.

Every read-modify-write runs under a cross-process lock (secrets.store.lock); readers never need it, because
the vault is replaced atomically. The store refuses a location inside a git work tree.
"""

import getpass
import hashlib
import json
import os
import platform
import re
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

# Security Libs
try:
    import keyring  # noqa: F401 - used by vault_key; checked here so a broken install fails early
    from cryptography.fernet import InvalidToken, MultiFernet
    SECURITY_AVAILABLE = True
except ImportError:
    SECURITY_AVAILABLE = False

from orchestrator.Core import vault_key
from orchestrator.Core.vault_key import (STORAGE_CHOICES, STORAGE_MODES, SecurityError, StorageError,
                                         StorageUnavailable, VaultKey, set_passphrase_prompt)
from orchestrator.utils.file_lock import LockTimeout, file_lock
from orchestrator.utils.helpers import redact_secrets
from orchestrator.utils.logging import get_logger

__all__ = ["SecretsManager", "SecurityError", "StorageError", "StorageUnavailable", "STORAGE_MODES",
           "STORAGE_CHOICES", "LEGACY_NAMES", "canonical_name", "credential_name", "origin_hash",
           "normalize_origin", "git_work_tree", "set_passphrase_prompt"]

# Provider keys of earlier versions and their entry names in the vault (provider ids of configs/llm_providers.yml).
LEGACY_NAMES = {
    "anthropic_api_key": "cred/anthropic/api_key",
    "openai_api_key": "cred/openai/api_key",
    "gemini_api_key": "cred/google/api_key",
    "openrouter_api_key": "cred/openrouter/api_key",
    "custom_api_key": "cred/custom/api_key",
}
_ALIASES: Dict[str, Tuple[str, ...]] = {}
for _old, _new in LEGACY_NAMES.items():
    _ALIASES[_new] = _ALIASES.get(_new, ()) + (_old,)

CRED_NAME = re.compile(r"^cred/[a-z0-9][a-z0-9_.-]{0,63}/[a-z0-9][a-z0-9_.-]{0,63}(@[0-9a-f]{16})?$")
_VAULT_ID = re.compile(r"^[0-9a-f]{16}$")
RECORD_KINDS = ("api_key", "oauth", "local_key", "aws_static", "google_sa")
MAX_RECORD_BYTES = 64 * 1024
LOCK_TIMEOUT = 30.0

# READ events are audited once per process and name, not on every request.
_read_audited: set = set()
_read_audited_lock = threading.Lock()


def canonical_name(name: str) -> str:
    """The vault name of a secret (old provider key names map to their cred/ names)."""
    return LEGACY_NAMES.get(name, name)


def normalize_origin(url: str) -> str:
    """`scheme://host:port` of a URL, lower case, with the default port filled in. Refuses user info,
    backslashes, spaces and control characters: urllib and HTTP clients may read such a URL as different
    hosts (http://evil\\@127.0.0.1 is 127.0.0.1 to urllib), and a key bound to one must never reach the other.
    The URL is never part of the error (it may carry a password)."""
    text = url.strip() if isinstance(url, str) else ""
    if not text.isprintable() or "\\" in text or any(c.isspace() for c in text):
        raise ValueError("not a plain http(s) URL (spaces, backslashes or control characters)")
    parts = urlsplit(text)
    if "@" in parts.netloc:
        raise ValueError("an origin URL must not contain user info")
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    try:
        port = parts.port
        if not host.isascii():
            host = host.encode("idna").decode("ascii")  # the same host as its xn-- form
    except (ValueError, UnicodeError):
        raise ValueError("not a valid http(s) URL (host or port)") from None
    host = host[:-1] if host.endswith(".") else host  # example.com. is example.com
    if scheme not in ("http", "https") or not host:
        raise ValueError("not an http(s) URL with a host")
    port = port or (443 if scheme == "https" else 80)
    if ":" in host:
        host = f"[{host}]"
    return f"{scheme}://{host}:{port}"


def origin_hash(url: str) -> str:
    return hashlib.sha256(normalize_origin(url).encode("utf-8")).hexdigest()[:16]


def credential_name(provider: str, method: str, origin: Optional[str] = None) -> str:
    """`cred/<provider>/<method>[@<origin-hash>]`; a credential bound to an endpoint names its origin."""
    name = f"cred/{provider}/{method}" + (f"@{origin_hash(origin)}" if origin else "")
    if not CRED_NAME.match(name):
        raise ValueError(f"invalid credential name: {name!r}")
    return name


def git_work_tree(path: Path) -> Optional[Path]:
    """The root of a git work tree containing `path` (or the file a link at `path` points to), if any."""
    path = Path(path)
    for start in (Path(os.path.abspath(path)).parent, Path(os.path.realpath(path)).parent):
        for folder in (start, *start.parents):
            if os.path.lexists(folder / ".git"):
                return folder
    return None


def _locked(method):
    """Serializes the store access of one instance (GUI workers and MCP calls run in threads)."""
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


def _same_path(a: Path, b: Path) -> bool:
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except (OSError, ValueError):
        return False


def _log_safe(text: Any) -> str:
    text = str(text)
    return text if text.isprintable() else text.encode("unicode_escape").decode("ascii")


def _valid_name(name: Any) -> bool:
    """A secret name is printable text (no line breaks or control characters) of reasonable length."""
    return isinstance(name, str) and 0 < len(name) <= 256 and name.isprintable()


def _file_signature(path: Optional[Path]):
    """Identifies the current version of a file (a new one is written with a new inode)."""
    if path is None:
        return None
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    except OSError:
        return object()  # cannot tell: never equal, so it is read again
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


class SecretsManager:
    SERVICE_ID = vault_key.KEYRING_SERVICE
    KEY_ID = vault_key.KEY_ENTRY

    def __init__(self, framework_manager, *, storage: Optional[str] = None, helper: Optional[str] = None):
        """`framework_manager`: the FrameworkManager (settings secret_storage/secret_helper, per-user folder),
        or a folder (tests, tools) whose secrets.store is used. `storage`/`helper` override the settings."""
        self.logger = get_logger("SecretsManager")
        self.framework = framework_manager
        self._config = None

        state_dir = None
        known_config_dir = None
        if isinstance(framework_manager, Path):
            self.config_dir = framework_manager
        else:
            self._config = getattr(framework_manager, "config", None)
            known_config_dir = getattr(self._config, 'config_dir', None)
            self.config_dir = Path(known_config_dir or "config")
            try:
                state_dir = getattr(self._config, "user_state_dir", None)
            except OSError:
                state_dir = None

        # Secrets are personal: one store per user (next to the tool permissions and the audit log). Older
        # versions kept it in the config folder; it is taken over once.
        self._legacy_file: Optional[Path] = None
        if isinstance(framework_manager, Path):
            self.secrets_file = framework_manager / "secrets.store"
        else:
            if not isinstance(state_dir, (str, Path)):
                # Same rule as ConfigManager.user_state_dir; never a folder relative to the working directory.
                state_dir = Path(os.environ.get("LLM_FRAMEWORK_HOME") or Path.home() / ".llm-framework")
            self.secrets_file = Path(state_dir) / "secrets.store"
            legacy = self.config_dir / "secrets.store"
            # Without a known config folder there is no old location (never a store relative to the working directory).
            if known_config_dir and not _same_path(legacy, self.secrets_file):
                self._legacy_file = legacy
        # Lists the old stores already taken over, so a secret deleted later never comes back.
        self._migrated_file = self.secrets_file.with_name("secrets.store.migrated")
        # Next to the store, never in the working directory (it names the user and the secrets read).
        self.audit_log_file = self.secrets_file.with_name("secrets-audit.log")
        self._lock_file = self.secrets_file.with_name("secrets.store.lock")
        self._key_file = self.secrets_file.with_name("secrets.store.insecure-key")

        self._storage_arg, self._helper_arg = storage, helper
        self._mode: Optional[str] = None
        self._helper: Optional[str] = None
        self._provider: Optional[vault_key.KeyProvider] = None
        self._keys: List[VaultKey] = []  # current first, then previous ones still needed
        self._key_header: Dict[str, Any] = {}  # what the vault records about the current key (vault id, scrypt)
        self._created_key = False  # the last load created the key of a new vault
        self._version = 0  # of the file last read; 0 = no file
        self._header: Dict[str, Any] = {}
        self._entries: Dict[str, Dict[str, str]] = {}
        self._signature: Any = None  # stat of the store files when the entries were loaded
        self._load_failed = False  # a damaged store is never overwritten
        self._failed_file: Optional[Path] = None  # the file that could not be read
        self._load_error: Optional[str] = None
        self.storage_error: Optional[StorageError] = None  # why initialize() failed
        self._initialized = False
        self._lock = threading.RLock()
        self._lock_depth = 0  # the cross-process lock is held by this instance (not reentrant itself)

        if not SECURITY_AVAILABLE:
            raise SecurityError(
                "CRITICAL: 'cryptography' or 'keyring' missing. "
                "Install via: pip install cryptography keyring"
            )

    # ------------------------------------------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------------------------------------------

    @property
    def storage_mode(self) -> Optional[str]:
        """keyring, helper, passphrase, insecure or ephemeral (None before initialize())."""
        return self._mode

    def _setting(self, name: str) -> Any:
        # The choice for one run (LLM_SECRET_STORAGE=ephemeral in CI) applies even when a config file is broken.
        env = os.environ.get(f"LLM_{name.upper()}")
        if env:
            return env
        config = self._config
        if config is None:
            return None
        getter = getattr(config, "get", None)
        if callable(getter):
            try:
                return getter(name)
            except Exception:
                return None
        return getattr(config, name, None)

    @_locked
    def initialize(self) -> bool:
        """Opens the vault with the chosen protection. False (reason in `storage_error`) when it cannot be
        used: no usable keyring and no other choice, no passphrase, a store inside a git work tree, ..."""
        try:
            self._open()
        except StorageError as e:
            return self._init_failed(e)
        except LockTimeout as e:
            return self._init_failed(StorageUnavailable("storage.locked", str(e)))
        except Exception as e:
            return self._init_failed(StorageUnavailable(
                "storage.unavailable", f"The credential vault cannot be opened ({type(e).__name__}: "
                                       f"{redact_secrets(str(e))[:200]})."))
        self.storage_error = None
        self._initialized = True
        return True

    def _init_failed(self, error: StorageError) -> bool:
        self.storage_error = error
        self._initialized = False
        self.logger.critical(f"Secrets store not available [{error.code}]: {error.message}")
        return False

    def _open(self) -> None:
        mode = str(self._storage_arg or self._setting("secret_storage") or "keyring").strip().lower()
        helper = self._helper_arg if self._helper_arg is not None else self._setting("secret_helper")
        if mode not in STORAGE_MODES:
            raise StorageError("storage.invalid_mode",
                               f"Unknown secret_storage '{mode}'. Choose one of: {', '.join(STORAGE_MODES)}.")
        if mode != "ephemeral":
            root = git_work_tree(self.secrets_file)
            if root is not None:
                raise StorageError("storage.in_git_tree",
                                   f"The secrets store {self.secrets_file} would be inside the git work tree "
                                   f"{root}, where a commit could publish it. Set LLM_FRAMEWORK_HOME to a folder "
                                   "outside any repository.")
        self._mode, self._helper = mode, (str(helper) if helper else None)
        self._provider = vault_key.make_provider(mode, helper=self._helper, key_file=self._key_file,
                                                 cache_id=os.path.realpath(self.secrets_file))
        self._keys, self._key_header = [], {}
        if not self._provider.persistent:
            self._keys = self._provider.create({}).keys
            self._entries, self._header, self._version = {}, {}, 0
            self.logger.info("Credentials are kept in memory only (secret_storage: ephemeral).")
            return
        if mode == "insecure":
            self.logger.warning(f"The vault key is stored unencrypted in {self._key_file} (secret_storage: insecure).")
        self._preload_keys()
        with self._vault_lock():
            self._load_store(strict=True)
            if self._load_failed:
                return  # never written; reported by doctor
            needs_header = self._created_key
            if needs_header or len(self._keys) > 1:
                # A new vault records its key id (and scrypt parameters) at once, so another process starting
                # now uses the same key; an interrupted rotation is completed.
                if not self._save_store() and needs_header:
                    if isinstance(self._provider, vault_key.DerivedKeyProvider):
                        # Without its recorded salt the passphrase would open nothing next time.
                        raise StorageUnavailable("storage.unavailable",
                                                 f"The credential vault {self.secrets_file} cannot be written.")
                    # A read-only folder: commands that need no secret keep working; the key stays for the
                    # first write that succeeds.
                    self.logger.warning(f"The credential vault {self.secrets_file} cannot be written; secrets "
                                        "cannot be stored until it can.")

    def _preload_keys(self) -> None:
        """Asks for the passphrase (or runs the credential helper) before the vault lock is taken: a prompt
        left open must never hold up the writes of other processes (a token refresh in the MCP server)."""
        if not isinstance(self._provider, vault_key.DerivedKeyProvider):
            return
        try:
            _, header, _ = self._read_vault(self.secrets_file)
        except Exception:
            return  # a new or unreadable vault: handled under the lock
        if header.get("protection") == self._mode and "kdf" in header:
            self._provider.load(header)  # cached for this process; nothing is written

    @contextmanager
    def _vault_lock(self):
        """The cross-process lock around a read-modify-write (reentrant within this instance)."""
        if self._lock_depth:
            self._lock_depth += 1
            try:
                yield
            finally:
                self._lock_depth -= 1
            return
        with file_lock(self._lock_file, timeout=LOCK_TIMEOUT):
            self._lock_depth = 1
            try:
                yield
            finally:
                self._lock_depth = 0

    def _ready(self) -> bool:
        """Opens the vault on first use (once: a failure is kept in storage_error until initialize())."""
        if not self._initialized and self.storage_error is None:
            self.initialize()
        return self._initialized

    # ------------------------------------------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------------------------------------------

    def _audit_log(self, action: str, key_name: str, status: str):
        """Names only, never values; nothing is written in ephemeral mode."""
        if self._provider is not None and not self._provider.persistent:
            return
        timestamp = datetime.now().isoformat()
        try:
            user = getpass.getuser()
        except Exception:  # no USER variable and no passwd entry (a container with an arbitrary uid)
            user = f"uid-{os.getuid()}" if hasattr(os, "getuid") else "unknown"
        # One line per event: a name (or USER) with a line break must not forge entries.
        entry = (f"[{timestamp}] USER={_log_safe(user)} ACTION={action} KEY={_log_safe(key_name)} "
                 f"STATUS={status}\n")
        try:
            self.audit_log_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            # O_BINARY: on Windows a descriptor is text mode otherwise, and lines would end in \r\r\n
            flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0)
            fd = os.open(self.audit_log_file, flags, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                if platform.system() != "Windows":
                    os.fchmod(f.fileno(), 0o600)
                f.write(entry)
        except Exception:  # auditing must never break secret access
            pass

    def _audit_read(self, name: str) -> None:
        marker = (os.path.realpath(self.audit_log_file), name)
        with _read_audited_lock:
            if marker in _read_audited:
                return
            _read_audited.add(marker)
        self._audit_log("READ", name, "SUCCESS")

    # ------------------------------------------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------------------------------------------

    @_locked
    def set_secret(self, key: str, value: str) -> bool:
        """Encrypts and stores a secret (a provider key name stores a cred/ record)."""
        if not _valid_name(key) or not isinstance(value, str):
            self.logger.error("A secret needs a name (printable text, no line breaks) and a text value.")
            return False
        try:
            value.encode("utf-8")
        except UnicodeError:
            self.logger.error(f"The value for {key} cannot be stored (not valid text).")
            return False
        name = canonical_name(key)
        plain = json.dumps({"v": 1, "kind": "api_key", "secret": value}) if name.startswith("cred/") else value
        return self._put(name, plain)

    @_locked
    def get_secret(self, key: str) -> Optional[str]:
        """The secret's value (for a cred/ record: its "secret" field), or None."""
        found = self._read(key)
        if found is None:
            return None
        stored, plain = found
        if not stored.startswith("cred/"):
            return plain
        record = self._parse_record(stored, plain)
        secret = record.get("secret") if record else None
        return secret if isinstance(secret, str) else None

    @_locked
    def get_record(self, name: str) -> Optional[Dict[str, Any]]:
        """The JSON record of a credential (`cred/...` or an old provider key name), or None."""
        if not canonical_name(name).startswith("cred/"):
            return None
        found = self._read(name)
        if found is None:
            return None
        stored, plain = found
        if not stored.startswith("cred/"):  # an old provider key, not moved yet
            return {"v": 1, "kind": "api_key", "secret": plain}
        return self._parse_record(stored, plain)

    @_locked
    def set_record(self, name: str, record: Dict[str, Any]) -> bool:
        """Stores a credential record under `cred/<provider>/<method>[@<origin-hash>]`. Records are JSON
        ({"kind": "api_key|oauth|local_key|aws_static|google_sa", "secret"/"access_token"/... }), up to 64 KB."""
        name = canonical_name(name) if isinstance(name, str) else ""
        if not CRED_NAME.match(name) or not isinstance(record, dict) or record.get("kind") not in RECORD_KINDS:
            self.logger.error(f"Invalid credential record or name: {name!r}")
            return False
        try:
            plain = json.dumps({**record, "v": 1}, ensure_ascii=True, allow_nan=False)
        except (TypeError, ValueError, RecursionError) as e:
            self.logger.error(f"Credential record {name} is not JSON: {type(e).__name__}")
            return False
        if len(plain) > MAX_RECORD_BYTES:
            self.logger.error(f"Credential record {name} is larger than {MAX_RECORD_BYTES} bytes.")
            return False
        return self._put(name, plain)

    @_locked
    def delete_secret(self, key: str) -> bool:
        """Deletes a secret (under its new and its old name)."""
        if not isinstance(key, str) or not self._ready():
            return False
        name = canonical_name(key)

        def change() -> bool:
            removed = False
            for candidate in (name, *_ALIASES.get(name, ())):
                removed = self._entries.pop(candidate, None) is not None or removed
            return removed

        if not self._mutate(change):
            return False
        self._audit_log("DELETE", name, "SUCCESS")
        return True

    @_locked
    def list_secrets(self) -> list[str]:
        """Names of the stored secrets (not values), old provider key names under their new names."""
        self._refresh()
        names: List[str] = []
        for stored in self._entries:
            name = canonical_name(stored)
            if name not in names:
                names.append(name)
        return names

    @_locked
    def refresh(self) -> bool:
        """Re-reads a store changed by another process; False when it (or the old store) cannot be read."""
        self._refresh()
        return not self._load_failed

    def credential_lock(self, name: str, timeout: float = 30.0):
        """The cross-process lock of one credential, for a token refresh (GUI, CLI and MCP server may refresh at
        the same time): take it, read the record again with get_record(), refresh, store it, release."""
        digest = hashlib.sha256(canonical_name(name).encode("utf-8")).hexdigest()[:16]
        return file_lock(self.secrets_file.with_name("locks") / f"{digest}.lock", timeout=timeout)

    @property
    def failed_file(self) -> Optional[Path]:
        """The store file that could not be read (the per-user store or the old one), if any."""
        return self._failed_file if self._load_failed else None

    @property
    def load_error(self) -> Optional[str]:
        """Why the store could not be read (no secret values)."""
        return self._load_error if self._load_failed else None

    @_locked
    def rotate_key(self) -> bool:
        """Creates a new vault key, re-encrypts every entry and removes the old key. In passphrase mode the
        passphrase is asked again (it may stay the same; the salt changes). A store of an earlier version
        moves from the shared keyring entry to its own; the shared one stays for the other stores."""
        if not self._ready():
            return False
        if not self._provider.persistent:
            plain = self._decrypt_all()
            if plain is None:
                return False
            self._keys = self._provider.rotate({}).keys
            self._entries = {name: self._encrypt(value) for name, value in plain.items()}
            return True
        derived = isinstance(self._provider, vault_key.DerivedKeyProvider)
        try:
            self._preload_keys()
            # A new passphrase is asked (or the helper run) before the lock is taken.
            fresh = self._provider.rotate({}) if derived else None
            with self._vault_lock():
                if not self._reload_for_write():
                    return False
                plain = self._decrypt_all()
                if plain is None:
                    return False
                old_keys, old_header = self._keys, self._key_header
                material = fresh or self._provider.rotate({**self._header, **self._key_header})  # the keys in use
                self._keys, self._key_header = material.keys, material.header
                self._entries = {name: self._encrypt(value) for name, value in plain.items()}
                if not self._save_store():
                    self._signature = None
                    if derived or material.header.get("vault_id") != old_header.get("vault_id"):
                        # The file still uses the old key; a keyring entry made for the new one is removed.
                        if not derived:
                            self._provider.forget(material.header)
                        self._keys, self._key_header = old_keys, old_header
                    # Otherwise the keyring keeps the old key as 'previous': the next start completes the rotation.
                    return False
        except LockTimeout as e:
            self.logger.error(str(e))
            return False
        except StorageError as e:
            self.logger.error(f"Key rotation failed [{e.code}]: {e.message}")
            return False
        except Exception as e:  # e.g. the keyring refused the write: the vault file is unchanged
            self.logger.error(f"Key rotation failed ({type(e).__name__}); the vault was not changed.")
            return False
        self._audit_log("ROTATE", "*", "SUCCESS")
        self.logger.info("Vault key rotated; every entry is re-encrypted with the new key.")
        return True

    @_locked
    def change_storage(self, mode: str, *, helper: Optional[str] = None) -> bool:
        """Moves the vault to another protection (keyring, helper, passphrase, insecure): re-encrypts every
        entry with a new key, removes the old key (not the shared keyring entry of earlier versions, which
        other stores may need) and remembers the choice in the user config. Raises StorageError when the new
        protection cannot be used (the vault is then unchanged)."""
        mode = (mode or "").strip().lower()
        if mode not in STORAGE_MODES or "ephemeral" in (mode, self._mode):
            raise StorageError("storage.invalid_mode",
                               "Choose keyring, helper, passphrase or insecure; ephemeral is chosen per run "
                               "(LLM_SECRET_STORAGE=ephemeral) and protects no vault.")
        if not self._ready():
            return False
        helper = helper if helper is not None else (self._setting("secret_helper") or self._helper)
        if mode == self._mode and (mode != "helper" or helper == self._helper):
            return self.rotate_key()
        new_provider = vault_key.make_provider(mode, helper=helper, key_file=self._key_file,
                                               cache_id=os.path.realpath(self.secrets_file))
        # The new key first, outside the lock: it may ask for a passphrase or run the helper.
        try:
            self._preload_keys()
            material = new_provider.create({}, fresh=True)
        except StorageError:
            raise
        except Exception as e:
            raise StorageUnavailable("storage.unavailable",
                                     f"The {mode} storage cannot be used ({type(e).__name__}).") from None
        moved = False
        try:
            with self._vault_lock():
                if not self._reload_for_write():
                    return False
                plain = self._decrypt_all()
                if plain is None:
                    return False
                old = (self._provider, self._mode, self._helper, self._keys, self._key_header)
                self._provider, self._mode, self._helper = new_provider, mode, helper
                self._keys, self._key_header = material.keys, material.header
                self._entries = {name: self._encrypt(value) for name, value in plain.items()}
                if not self._save_store():
                    self._provider, self._mode, self._helper, self._keys, self._key_header = old
                    self._signature = None
                    return False
                moved = True
                try:
                    old[0].forget(old[4])
                except Exception as e:
                    self.logger.warning(f"The old vault key ({old[1]}) could not be removed: {type(e).__name__}")
        except LockTimeout as e:
            self.logger.error(str(e))
            return False
        finally:
            if not moved:
                try:
                    new_provider.forget(material.header)
                except Exception as e:
                    self.logger.warning(f"The unused {mode} key could not be removed: {type(e).__name__}")
        self._storage_arg, self._helper_arg = mode, helper
        self._remember_storage(mode, helper)
        self._audit_log("STORAGE", mode, "SUCCESS")
        return True

    def _remember_storage(self, mode: str, helper: Optional[str]) -> None:
        config = self._config
        if config is None or not callable(getattr(config, "set", None)):
            return
        try:
            config.set("secret_storage", mode)
            if helper:
                config.set("secret_helper", helper)
            save = getattr(config, "save_user_config", None)
            if callable(save) and save() is False:
                raise OSError("the user config was not written")
        except Exception as e:
            # The vault is already protected the new way: without the setting the next start stops with
            # storage.mode_mismatch, so say what to set.
            self.logger.error(f"Could not save secret_storage={mode} in the user config ({e}); set it yourself "
                              f"(config.yml: secret_storage: {mode}) before the next start.")

    # ------------------------------------------------------------------------------------------------------
    # Entries
    # ------------------------------------------------------------------------------------------------------

    def _fernet(self) -> "MultiFernet":
        return MultiFernet([key.fernet() for key in self._keys])

    def _encrypt(self, plain: str) -> Dict[str, str]:
        current = self._keys[0]
        return {"kid": current.kid, "ct": current.fernet().encrypt(plain.encode("utf-8")).decode("ascii")}

    def _decrypt(self, entry: Dict[str, str]) -> str:
        return self._fernet().decrypt(entry["ct"].encode("ascii")).decode("utf-8")

    def _decrypt_all(self) -> Optional[Dict[str, str]]:
        plain = {}
        for name, entry in self._entries.items():
            try:
                plain[name] = self._decrypt(entry)
            except (InvalidToken, ValueError, UnicodeError):
                self.logger.error(f"Entry {name} cannot be decrypted; nothing was changed.")
                return None
        return plain

    def _lookup(self, key: str) -> Optional[Tuple[str, Dict[str, str]]]:
        name = canonical_name(key)
        for candidate in (name, *_ALIASES.get(name, ())):
            entry = self._entries.get(candidate)
            if entry is not None:
                return candidate, entry
        return None

    def _read(self, key: str) -> Optional[Tuple[str, str]]:
        """(stored name, decrypted value) of a secret, or None."""
        if not isinstance(key, str) or not self._ready():
            return None
        self._refresh()  # long-running processes (MCP server, GUI) see changes made by others
        found = self._lookup(key) if self._keys else None
        if found is None:
            return None
        stored, entry = found
        name = canonical_name(stored)
        try:
            plain = self._decrypt(entry)
        except (InvalidToken, ValueError, UnicodeError):
            self.logger.error(f"Decryption failed for {name}")
            self._audit_log("READ", name, "DECRYPTION_FAIL - POSSIBLE TAMPERING")
            return None
        self._audit_read(name)
        return stored, plain

    def _parse_record(self, name: str, plain: str) -> Optional[Dict[str, Any]]:
        try:
            record = json.loads(plain)
        except (ValueError, RecursionError):
            record = None
        if not isinstance(record, dict):
            self.logger.error(f"Credential record {name} is damaged.")
            return None
        return record

    def _put(self, name: str, plain: str) -> bool:
        if not self._ready():
            return False

        def change() -> bool:
            for alias in _ALIASES.get(name, ()):
                self._entries.pop(alias, None)
            self._entries[name] = self._encrypt(plain)
            return True

        if not self._mutate(change):
            self._audit_log("WRITE", name, "FAILED")
            return False
        self._audit_log("WRITE", name, "SUCCESS")
        return True

    def _mutate(self, change: Callable[[], bool]) -> bool:
        """Applies `change` to the current store under the cross-process lock and saves it."""
        if not self._provider.persistent:
            return bool(change())
        try:
            self._preload_keys()  # after a rotation elsewhere the passphrase is asked before the lock is taken
        except StorageError as e:
            self.logger.error(f"Secrets store not changed [{e.code}]: {e.message}")
            return False
        try:
            with self._vault_lock():
                if not self._reload_for_write():
                    return False
                if not change():
                    return False
                self._move_legacy_names()
                if not self._save_store():
                    self._signature = None  # the entries hold a change that was not saved: read the file again
                    return False
                return True
        except LockTimeout as e:
            self.logger.error(str(e))
            return False

    def _move_legacy_names(self) -> None:
        """Old provider key names move to their cred/ records (on every write)."""
        for old, new in LEGACY_NAMES.items():
            entry = self._entries.get(old)
            if entry is None:
                continue
            if new not in self._entries:
                try:
                    value = self._decrypt(entry)
                except (InvalidToken, ValueError, UnicodeError):
                    continue  # unreadable: left as it is
                self._entries[new] = self._encrypt(json.dumps({"v": 1, "kind": "api_key", "secret": value}))
            del self._entries[old]

    # ------------------------------------------------------------------------------------------------------
    # Store file
    # ------------------------------------------------------------------------------------------------------

    def _refresh(self):
        """Re-reads the store when another process changed, added or removed it since it was loaded."""
        if self._provider is None or not self._provider.persistent:
            return
        if self._signature != self._current_signature():
            self._load_store()

    def _current_signature(self):
        return (_file_signature(self.secrets_file), _file_signature(self._legacy_file))

    def _reload_for_write(self) -> bool:
        """Re-reads the store before a change (another process may have written it since) and refuses to
        write when it cannot be read: saving the in-memory copy would erase every other secret."""
        self._load_store()
        if self._load_failed:
            self.logger.error(f"The secrets store {self._failed_file} cannot be read; not changing "
                              f"{self.secrets_file}. Repair or move the file aside, then try again.")
            return False
        return bool(self._keys)

    def _save_store(self) -> bool:
        """Writes the encrypted store atomically (temp file, mode 0600, then rename), so a reader never
        sees a half-written file. Entries of an older key are re-encrypted with the current one first."""
        try:
            current = self._keys[0]
            fernet = self._fernet()
            entries, all_current = {}, True
            for name, entry in self._entries.items():
                if entry["kid"] != current.kid:
                    try:
                        entry = {"kid": current.kid, "ct": current.fernet().encrypt(
                            fernet.decrypt(entry["ct"].encode("ascii"))).decode("ascii")}
                    except (InvalidToken, ValueError, UnicodeError):
                        all_current = False  # unreadable: kept as it is
                entries[name] = entry
            header = {"protection": self._mode, "kid": current.kid, **self._key_header,
                      "check": vault_key.check_token(current)}
            self._write_atomic(self.secrets_file, {"version": 2, **header, "entries": entries})
        except Exception as e:
            self.logger.error(f"Failed to save secrets store: {e}")
            return False
        self._version, self._header, self._entries = 2, header, entries
        if len(self._keys) > 1 and all_current:
            try:
                self._provider.drop_previous(self._key_header)
                self._keys = self._keys[:1]
            except Exception as e:
                self.logger.warning(f"The previous vault key could not be removed yet: {type(e).__name__}")
        return True

    @staticmethod
    def _write_atomic(path: Path, data: Any) -> None:
        # Through a link (e.g. onto an encrypted volume), so the link is kept instead of being replaced.
        path = Path(os.path.realpath(path))
        tmp = None
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)  # private like the store
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".secrets.", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            if platform.system() != "Windows":
                os.chmod(tmp, 0o600)
            vault_key.replace_file(tmp, path)
        except BaseException:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            raise

    @staticmethod
    def _parse_store(raw: bytes) -> Dict[str, Any]:
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
        return data

    @classmethod
    def _read_store(cls, path: Path) -> Dict[str, Any]:
        return cls._parse_store(Path(path).read_bytes())

    @classmethod
    def _read_vault(cls, path: Path) -> Tuple[int, Dict[str, Any], Dict[str, Dict[str, str]]]:
        """(version, header, entries) of a store file of version 1 or 2."""
        return cls._vault_data(cls._read_store(path))

    @staticmethod
    def _vault_data(data: Dict[str, Any]) -> Tuple[int, Dict[str, Any], Dict[str, Dict[str, str]]]:
        version = data.get("version")
        if isinstance(version, int) and not isinstance(version, bool):
            if version != 2:
                raise ValueError(f"unsupported store version {version}")
            entries = data.get("entries")
            if not isinstance(entries, dict):
                raise ValueError("no entries")
            for name, entry in entries.items():
                if not (isinstance(entry, dict) and isinstance(entry.get("kid"), str)
                        and isinstance(entry.get("ct"), str)):
                    raise ValueError(f"damaged entry {name!r}")
            header = {k: data[k] for k in ("protection", "kid", "check") if isinstance(data.get(k), str)}
            if isinstance(data.get("kdf"), dict):
                header["kdf"] = data["kdf"]
            if "vault_id" in data:
                if not (isinstance(data["vault_id"], str) and _VAULT_ID.match(data["vault_id"])):
                    raise ValueError("damaged vault id")
                header["vault_id"] = data["vault_id"]
            return 2, header, {name: {"kid": e["kid"], "ct": e["ct"]} for name, e in entries.items()}
        for name, token in data.items():
            if not isinstance(token, str):
                raise ValueError(f"damaged entry {name!r}")
        return 1, {}, {name: {"kid": "v1", "ct": token} for name, token in data.items()}

    def _fail(self, path: Path, error: Exception) -> None:
        self._load_failed = True
        self._failed_file = path
        self._load_error = error.message if isinstance(error, StorageError) else f"{type(error).__name__}: {error}"
        self.logger.error(f"Failed to load secrets store {path}: {self._load_error}")

    def _load_store(self, strict: bool = False):
        """Loads the store and takes over the store of the old location once. `strict` (initialize): a key
        that cannot be obtained (keyring, passphrase, helper) raises instead of counting as a load failure."""
        # Taken before reading: a change made meanwhile is read on the next access.
        self._signature = self._current_signature()
        self._created_key = False
        try:
            version, header, entries = self._read_vault(self.secrets_file)
        except FileNotFoundError as e:
            if os.path.lexists(self.secrets_file):
                # A link to a file that is gone (unmounted drive): its secrets are unknown, so never write.
                self._fail(self.secrets_file, e)
                return
            # Moved aside or deleted on purpose: its secrets must not be written back from memory.
            version, header, entries = 0, {}, {}
        except Exception as e:
            self._fail(self.secrets_file, e)
            return
        try:
            self._sync_keys(version, header, entries)
        except StorageUnavailable as e:
            if strict:
                raise
            self._fail(self.secrets_file, e)
            return
        except StorageError as e:
            self._fail(self.secrets_file, e)
            return
        except LockTimeout as e:
            if strict:
                raise
            self.logger.warning(f"Secrets store not loaded yet: {e}")
            self._signature = None  # tried again on the next access
            return
        self._version, self._header, self._entries = version, header, entries
        self._load_failed = False
        self._failed_file = None
        self._load_error = None
        created = self._created_key
        self._take_over_legacy()
        if self._load_failed and created and not os.path.lexists(self.secrets_file):
            # No vault records the new key (the old store failed): it is made again once it can be used.
            self._provider.forget(self._key_header)
            self._keys, self._key_header, self._created_key = [], {}, False

    def _sync_keys(self, version: int, header: Dict[str, Any], entries: Dict[str, Dict[str, str]]) -> None:
        """Makes sure the keys open this store: loads them (again, after a rotation by another process) or
        creates the first key of a new vault. Never creates a key for a vault that holds entries."""
        protection = header.get("protection") or ("keyring" if version == 1 and entries else None)
        if protection and protection != self._mode:
            raise StorageUnavailable(
                "storage.mode_mismatch",
                f"The credential vault {self.secrets_file} is protected with '{protection}', but secret_storage "
                f"is '{self._mode}'. Set secret_storage back to '{protection}' (and change it with the CLI), or "
                "move the file aside to start a new vault (you will need to sign in again).")
        if version == 0:
            # No file: a new vault gets its own key (written with its header by _open), or the file was moved
            # aside while this process runs and the next write starts it again with the same key.
            if not self._keys:
                with self._vault_lock():
                    material = self._provider.create(header)
                self._keys, self._key_header, self._created_key = material.keys, material.header, True
            return
        keys, key_header = self._keys, self._key_header
        kids = {key.kid for key in keys}
        stale = (not keys or (header.get("kid") and header["kid"] not in kids)
                 or header.get("vault_id") != key_header.get("vault_id")
                 or ("check" in header and not vault_key.check_matches(keys, header["check"])))
        created = False
        if stale:
            material = self._provider.load(header)
            if material is None:
                if entries:
                    raise StorageError(
                        "storage.vault_unreadable",
                        f"The key of the credential vault {self.secrets_file} is missing from the {self._mode} "
                        "storage, so the vault cannot be read; it was not changed. Restore the key, or move the "
                        "file aside and sign in again.")
                with self._vault_lock():  # an empty vault whose key is gone gets a new one
                    material = self._provider.create(header)
                created = True
            keys, key_header = material.keys, material.header
        if entries and "check" in header and not vault_key.check_matches(keys, header["check"]):
            raise StorageError(
                "storage.vault_unreadable",
                f"The credential vault {self.secrets_file} cannot be opened with the key of the {self._mode} "
                "storage; it was not changed. Restore it, or move it aside and sign in again.")
        if version == 1 and entries and not self._any_decrypts(keys, entries):
            raise StorageError(
                "storage.vault_unreadable",
                f"The secrets store {self.secrets_file} cannot be decrypted with the key in the keyring; it was "
                "not changed. Restore the key, or move the file aside and set the secrets again.")
        self._keys, self._key_header = keys, key_header
        self._created_key = self._created_key or created

    @staticmethod
    def _any_decrypts(keys: List[VaultKey], entries: Dict[str, Dict[str, str]]) -> bool:
        fernet = MultiFernet([key.fernet() for key in keys])
        for entry in entries.values():
            try:
                fernet.decrypt(entry["ct"].encode("ascii"))
                return True
            except (InvalidToken, ValueError, UnicodeError):
                continue
        return False

    def _reencrypt(self, header: Dict[str, Any], entries: Dict[str, Dict[str, str]]
                   ) -> Tuple[Dict[str, Dict[str, str]], List[str]]:
        """Entries of another store (described by `header`) encrypted with this vault's key, and the names
        of those that cannot be decrypted."""
        if not entries:
            return {}, []
        try:
            material = self._provider.load(header)
        except StorageError:
            material = None
        fernet = MultiFernet([key.fernet() for key in (material.keys if material else []) + self._keys])
        added, unreadable = {}, []
        for name, entry in entries.items():
            try:
                plain = fernet.decrypt(entry["ct"].encode("ascii")).decode("utf-8")
            except (InvalidToken, ValueError, UnicodeError):
                unreadable.append(name)
                continue
            added[name] = self._encrypt(plain)
        return added, unreadable

    def _migrated_stores(self) -> Optional[Dict[str, list]]:
        """The old stores already taken over, by path and by content. None when the record cannot be read:
        a takeover already ran, and running it again could bring back secrets deleted since."""
        path = self._migrated_file
        if not os.path.lexists(path):
            return {"stores": [], "sha256": []}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not a JSON object")
            record = {}
            for name in ("stores", "sha256"):
                value = data.get(name, [])
                if not isinstance(value, list):
                    raise ValueError(f"'{name}' is not a list")
                record[name] = [str(v) for v in value]
            return record
        except Exception as e:
            self.logger.warning(f"Cannot read {path} ({e}); old secrets stores are not taken over.")
            return None

    def _take_over_legacy(self) -> None:
        """Merges the store of the old location into the per-user store, exactly once. Secrets that are
        already in the per-user store win; the old file is left untouched. Old stores were protected by the
        keyring, so only a keyring vault takes them over."""
        legacy = self._legacy_file
        if legacy is None or self._mode != "keyring":
            return
        done = self._migrated_stores()
        if done is None:
            return
        legacy_id = os.path.realpath(legacy)
        if legacy_id in done["stores"] or not os.path.lexists(legacy):
            return
        if not self._lock_depth:
            try:
                with self._vault_lock():
                    self._load_store()  # again under the lock: another process may have taken it over meanwhile
            except LockTimeout as e:
                self.logger.warning(f"Old store {legacy} not taken over yet: {e}")
            return
        try:
            raw = legacy.read_bytes()
            _, legacy_header, data = self._vault_data(self._parse_store(raw))
        except FileNotFoundError:
            return
        except Exception as e:
            # Its secrets would be lost silently: refuse changes until it is repaired or moved aside.
            self._fail(legacy, e)
            return
        digest = hashlib.sha256(raw).hexdigest()
        # The same file under another path (installation folder moved, other mount point) was taken over already.
        seen = digest in done["sha256"]
        present = {canonical_name(name) for name in self._entries}
        candidates = {} if seen else {k: v for k, v in data.items() if canonical_name(k) not in present}
        # Re-encrypted with this vault's key: the old store's key (the shared keyring entry of earlier
        # versions) is not this vault's.
        added, unreadable = self._reencrypt(legacy_header, candidates)
        if unreadable and not added:
            # Restoring its key would make them readable: refuse changes until then, or until it is moved aside.
            self._fail(legacy, StorageError("storage.vault_unreadable",
                                            f"{len(unreadable)} entries cannot be decrypted with the key in the "
                                            "keyring."))
            return
        if unreadable:
            self.logger.warning(f"Not taken over from {legacy} (cannot be decrypted): "
                                f"{', '.join(_log_safe(n) for n in sorted(unreadable))}")
        if added:
            self._entries.update(added)
            self._move_legacy_names()
            if not self._save_store():
                self._signature = None
                return  # tried again on the next load
        record = {"stores": done["stores"] + [legacy_id], "sha256": done["sha256"] + ([] if seen else [digest])}
        try:
            self._write_atomic(self._migrated_file, record)
        except Exception as e:
            self.logger.warning(f"Could not record the takeover of {legacy} in {self._migrated_file}: {e}")
        self.logger.info(f"Secrets of the old store {legacy} taken over into {self.secrets_file} "
                         f"({len(added)} added; the old file is left in place, delete it when you no longer "
                         "need it).")
