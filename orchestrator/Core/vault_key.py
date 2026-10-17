#!/usr/bin/env python3
"""Where the key of the secrets vault comes from.

Every secret sits encrypted in the vault file (`secrets.store` in the per-user folder); only the vault key
needs protection. The user picks how (setting `secret_storage`), and nothing falls back on its own:

  keyring     the OS keyring holds the key (default; about 50 bytes, fits every backend), one entry per vault
  helper      the output of a credential helper command (setting `secret_helper`, e.g.
              `pass show llmcc/vault-key`), run without a shell and stretched with scrypt
  passphrase  a passphrase asked once per process (terminal or GUI dialog), stretched with scrypt; never
              read from an environment variable or a config file
  insecure    the key in a 0600 file next to the vault: not encrypted at rest
  ephemeral   memory only, nothing is written (CI; credentials come from environment variables per run)
"""

import base64
import contextlib
import getpass
import hashlib
import json
import os
import secrets
import shlex
import subprocess
import sys
import threading
import time
import unicodedata
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:  # SecretsManager reports the missing package (SecurityError) when it is created
    Fernet = None

    class InvalidToken(Exception):
        pass

# The private atomic writer and the Windows replace retry live in orchestrator/utils/atomic.py (consolidation chain
# c1-imports); the old names stay importable here.
from orchestrator.utils.atomic import REPLACE_RETRIES, replace_file, write_private  # noqa: F401
from orchestrator.utils.helpers import redact_secrets
from orchestrator.utils.logging import get_logger

KEYRING_SERVICE = "llm-cross-compiler-framework"
# The service name before the repository was renamed to LLM_Cross-Compiler_Framework. Its entries (the vault keys of
# every vault, the audit key, the revision anchors) are still read: when the new name holds nothing, the entry under
# the old name is used and copied to the new name. The old entry is never removed (an installation that is still
# on the old version reads it there).
LEGACY_KEYRING_SERVICE = "llm-conversion-framework"
KEY_ENTRY = "master-encryption-key"
PREVIOUS_SUFFIX = ".prev"
# The probe uses its own service, so it never touches the real entries (Windows Credential Manager moves
# the existing entry of a service aside when another user name is stored under it).
PROBE_SERVICE = KEYRING_SERVICE + "-probe"
PROBE_TIMEOUT = 3.0
HELPER_TIMEOUT = 30.0

STORAGE_MODES = ("keyring", "helper", "passphrase", "insecure", "ephemeral")
STORAGE_CHOICES = {
    "keyring": "the OS keyring (default)",
    "helper": "a credential helper command, e.g. secret_helper: \"pass show llmcc/vault-key\"",
    "passphrase": "a passphrase you enter once per start",
    "insecure": "a key file next to the vault (NOT encrypted at rest)",
    "ephemeral": "memory only, nothing is stored (CI; keys from environment variables)",
}

# scrypt for helper output and passphrases (n=2**17, r=8, p=1: about 128 MiB and a fraction of a second).
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 17, 8, 1
_SCRYPT_LIMITS = {"n": (2 ** 14, 2 ** 18), "r": (8, 16), "p": (1, 4)}
# A stored format value, frozen: every vault protected by a passphrase or a credential helper holds this text encrypted
# as its check token, so changing it (also with the product name) would make every existing vault look like a wrong
# passphrase. It keeps the name from before the rename on purpose.
CHECK_PLAINTEXT = b"llm-conversion-framework vault v2"


class SecurityError(Exception):
    """Critical security error."""


class StorageError(SecurityError):
    """The credential vault cannot be used. `code` names the case (e.g. storage.keyring_unavailable);
    the CLI exits with code 5 for every storage error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class StorageUnavailable(StorageError):
    """No usable protection for the vault key right now (no keyring, no passphrase, helper failed, ...)."""


def choices_text() -> str:
    return "; ".join(f"{mode}: {text}" for mode, text in STORAGE_CHOICES.items() if mode != "keyring")


def keyring_unavailable(reason: str) -> StorageUnavailable:
    return StorageUnavailable(
        "storage.keyring_unavailable",
        f"Your system keyring is locked or not available ({reason}). Choose how to protect your credentials "
        f"with the setting secret_storage (config.yml, or LLM_SECRET_STORAGE for one run): {choices_text()}.")


# ----------------------------------------------------------------------------------------------------------
# Keys
# ----------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class VaultKey:
    kid: str
    key: bytes = field(repr=False)  # urlsafe base64 Fernet key

    def fernet(self) -> Fernet:
        return Fernet(self.key)


@dataclass
class KeyMaterial:
    """The keys of a vault, current first, then older ones still needed to read entries not yet
    re-encrypted; `header` holds what the vault must record about the current key (scrypt parameters)."""
    keys: List[VaultKey]
    header: Dict[str, Any] = field(default_factory=dict)


def new_key() -> VaultKey:
    return VaultKey(secrets.token_hex(4), Fernet.generate_key())


def format_key(key: VaultKey) -> str:
    return f"v2:{key.kid}:{key.key.decode('ascii')}"


def parse_key(value: str) -> VaultKey:
    """`v2:<kid>:<fernet key>`, or the bare Fernet key of stores before v2 (key id "v1")."""
    value = value.strip()
    if value.startswith("v2:"):
        _, kid, key = value.split(":", 2)
    else:
        kid, key = "v1", value
    Fernet(key.encode("ascii"))  # raises ValueError when it is not a key
    return VaultKey(kid, key.encode("ascii"))


def check_token(key: VaultKey) -> str:
    return key.fernet().encrypt(CHECK_PLAINTEXT).decode("ascii")


def check_matches(keys: List[VaultKey], token: str) -> bool:
    for key in keys:
        try:
            if key.fernet().decrypt(token.encode("ascii")) == CHECK_PLAINTEXT:
                return True
        except (InvalidToken, ValueError, TypeError, AttributeError):
            continue
    return False


# ----------------------------------------------------------------------------------------------------------
# OS keyring: probe and entries
# ----------------------------------------------------------------------------------------------------------

_MISSING = object()
_probe_results: "weakref.WeakKeyDictionary[Any, Optional[str]]" = weakref.WeakKeyDictionary()
_probe_lock = threading.Lock()
_backend_problem: Optional[str] = None  # finding the backend itself did not answer (cached like a probe)


def _call_with_timeout(fn: Callable[[], Any], timeout: float, name: str) -> Any:
    """Runs `fn` in a daemon thread (a hung keyring must not keep the process alive or block it)."""
    box: Dict[str, Any] = {}

    def run():
        try:
            box["value"] = fn()
        except BaseException as e:  # handed to the caller
            box["error"] = e

    worker = threading.Thread(target=run, name=name, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        raise TimeoutError(f"no answer within {timeout:g} s")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def prefer_local_machine(backend: Any) -> None:
    """Windows Credential Manager: keep the entries on this machine. keyring's default
    (CRED_PERSIST_ENTERPRISE) roams with roaming profiles."""
    for candidate in (backend, *(getattr(backend, "backends", None) or ())):
        cls = type(candidate)
        if cls.__name__ == "WinVaultKeyring" or cls.__module__.endswith("backends.Windows"):
            candidate.persist = "local machine"


def unencrypted_backend(backend: Any) -> Optional[str]:
    """The name of the backend if it keeps secrets without encryption (keyrings.alt, which calls itself
    possibly insecure, and plaintext backends): using it would be secret_storage 'insecure' without the
    user choosing it. A chainer writes to its first backend."""
    chained = getattr(backend, "backends", None)
    if isinstance(chained, (list, tuple)) and chained:
        backend = chained[0]
    cls = type(backend)
    if cls.__module__.startswith("keyrings.alt") or "plaintext" in cls.__name__.lower():
        return f"{cls.__module__}.{cls.__name__}"
    return None


def _probe(backend: Any) -> Optional[str]:
    user = f"probe-{os.getpid()}-{secrets.token_hex(4)}"
    value = secrets.token_urlsafe(16)

    def run() -> bool:
        prefer_local_machine(backend)
        backend.set_password(PROBE_SERVICE, user, value)
        try:
            return backend.get_password(PROBE_SERVICE, user) == value
        finally:
            try:
                backend.delete_password(PROBE_SERVICE, user)
            except Exception:  # a probe entry that cannot be removed is harmless
                pass

    try:
        ok = _call_with_timeout(run, PROBE_TIMEOUT, "keyring-probe")
    except TimeoutError:
        return f"it did not answer within {PROBE_TIMEOUT:g} s"
    except Exception as e:
        return f"{type(e).__name__}: {redact_secrets(str(e))[:200]}"
    return None if ok else "it does not keep what is stored in it"


def keyring_problem() -> Optional[str]:
    """None when the OS keyring stores, returns and deletes a value within PROBE_TIMEOUT, else why not.
    Probed once per process (and keyring backend)."""
    global _backend_problem
    if _backend_problem is not None:
        return _backend_problem
    try:
        import keyring
        # Choosing the backend asks D-Bus on Linux (Secret Service), which can hang like the keyring itself.
        backend = _call_with_timeout(keyring.get_keyring, PROBE_TIMEOUT, "keyring-backend")
    except TimeoutError:
        _backend_problem = f"it did not answer within {PROBE_TIMEOUT:g} s"
        return _backend_problem
    except Exception as e:
        return f"no keyring backend ({type(e).__name__})"
    with _probe_lock:
        try:
            cached = _probe_results.get(backend, _MISSING)
        except TypeError:
            cached = _MISSING
    if cached is not _MISSING:
        return cached
    plaintext = unencrypted_backend(backend)
    problem = f"its backend {plaintext} stores secrets without encryption" if plaintext else _probe(backend)
    with _probe_lock:
        try:
            _probe_results[backend] = problem
        except TypeError:
            pass
    return problem


def keyring_usable() -> bool:
    return keyring_problem() is None


def reset_probe() -> None:
    """Probe again on the next use (e.g. after the user unlocked the keyring)."""
    global _backend_problem
    with _probe_lock:
        _probe_results.clear()
        _backend_problem = None


def new_vault_id() -> str:
    return secrets.token_hex(8)


def legacy_service(service: str) -> Optional[str]:
    """The name `service` had before the rename (llm-conversion-framework...): the plain service and every
    "<service>-..." name (one per vault) were renamed. None for any other name."""
    if service == KEYRING_SERVICE or service.startswith(KEYRING_SERVICE + "-"):
        return LEGACY_KEYRING_SERVICE + service[len(KEYRING_SERVICE):]
    return None


def read_entry(backend: Any, service: str, name: str) -> Optional[str]:
    """`backend.get_password(service, name)` (backend: a keyring backend or the keyring module). When nothing is
    stored there, the entry under the service's name from before the rename: it is copied to the new name (so the
    next read finds it there) and returned. The old entry stays where it is; a copy that fails is tried again on the
    next read, the value is returned anyway."""
    value = backend.get_password(service, name)
    old_service = None if value else legacy_service(service)
    if not old_service:
        return value
    old = backend.get_password(old_service, name)
    if not old:
        return value
    try:
        # The keyring module itself has a 'backends' subpackage, not a chain: the setting goes to its backend.
        prefer_local_machine(backend.get_keyring() if callable(getattr(backend, "get_keyring", None)) else backend)
        backend.set_password(service, name, old)
    except Exception:  # still readable under the old name; copied on a later read
        pass
    return old


def vault_service(vault_id: Optional[str]) -> str:
    """The keyring service holding a vault's key. Each vault has its own (a rotation or a change of storage
    must never remove the key of another vault, e.g. of another LLM_FRAMEWORK_HOME folder); stores of
    earlier versions (no vault id) share the plain service."""
    return f"{KEYRING_SERVICE}-vault-{vault_id}" if vault_id else KEYRING_SERVICE


class KeyringHolder:
    """The vault key (and, during a rotation, the previous one) as entries of the OS keyring.

    A vault whose key is still under the service name from before the rename (LEGACY_KEYRING_SERVICE) opens: the
    first read copies its key, and the previous key of a rotation not yet finished, to the new name; from then on
    only the new name is used. The entries under the old name are never removed by the framework."""

    def __init__(self, service: str = KEYRING_SERVICE):
        problem = keyring_problem()
        if problem:
            raise keyring_unavailable(problem)
        import keyring
        self._backend = keyring.get_keyring()
        self.service = service
        self.legacy_service = legacy_service(service)

    @staticmethod
    def _entry(previous: bool) -> str:
        return KEY_ENTRY + (PREVIOUS_SUFFIX if previous else "")

    def get(self, previous: bool = False) -> Optional[str]:
        value = self._backend.get_password(self.service, self._entry(previous))
        if value or not self.legacy_service:
            return value
        if previous and self._backend.get_password(self.service, self._entry(False)):
            return None  # the new name is in use: its previous key was dropped, the old name is not asked again
        old = self._backend.get_password(self.legacy_service, self._entry(previous))
        if old and not previous:
            self._copy_from_legacy(old)
        return old

    def _copy_from_legacy(self, current: str) -> None:
        """The first use after the rename: the key (and a previous key still needed) under the new name too. The
        previous key first, so a reader never sees the new current key without it. A copy that fails is tried
        again on the next read; the old entries stay either way."""
        try:
            previous = self._backend.get_password(self.legacy_service, self._entry(True))
            if previous:
                self.set(previous, previous=True)
            self.set(current)
        except Exception:
            pass

    def set(self, value: str, previous: bool = False) -> None:
        prefer_local_machine(self._backend)
        self._backend.set_password(self.service, self._entry(previous), value)

    def delete(self, previous: bool = False) -> None:
        try:
            self._backend.delete_password(self.service, self._entry(previous))
        except Exception:  # not there (keyring raises PasswordDeleteError)
            pass
        if previous or not self.legacy_service:
            return
        try:
            left = self._backend.get_password(self.legacy_service, self._entry(False))
        except Exception:
            left = None
        if left:
            # Not removed by the framework: said in plain words, so the person decides.
            get_logger("SecretsManager").warning(
                f"A copy of this vault key is still kept under the keyring service name from before the rename, "
                f"'{self.legacy_service}' (entry '{self._entry(False)}'). The framework does not remove it. If no "
                f"older installation of the LLM Conversion Framework uses this vault any more, remove it in your "
                f"keyring manager, e.g. python -m keyring del {self.legacy_service} {self._entry(False)}")


class KeyFileHolder:
    """The vault key in a 0600 file next to the vault. Not encrypted at rest; only when chosen explicitly."""

    WARNING = ("NOT ENCRYPTED AT REST: anyone who can read this file can decrypt the secrets store next to it "
               "(chosen with secret_storage: insecure).")

    def __init__(self, path: Path):
        self.path = Path(path)

    def _read(self) -> Dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            raise StorageError("storage.vault_unreadable",
                               f"The key file {self.path} cannot be read ({type(e).__name__}).") from None
        return data if isinstance(data, dict) else {}

    def get(self, previous: bool = False) -> Optional[str]:
        value = self._read().get("previous" if previous else "current")
        return value if isinstance(value, str) else None

    def _write(self, data: Dict[str, Any]) -> None:
        data = {k: v for k, v in data.items() if v}
        if not data.get("current") and not data.get("previous"):
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            return
        write_private(self.path, json.dumps({"warning": self.WARNING, **data}, indent=2) + "\n")

    def set(self, value: str, previous: bool = False) -> None:
        data = self._read()
        data["previous" if previous else "current"] = value
        self._write(data)

    def delete(self, previous: bool = False) -> None:
        data = self._read()
        data.pop("previous" if previous else "current", None)
        self._write(data)


# ----------------------------------------------------------------------------------------------------------
# Providers: one per protection mode
# ----------------------------------------------------------------------------------------------------------

class KeyProvider:
    mode = ""
    persistent = True  # False: the vault is never read from or written to disk

    def load(self, header: Dict[str, Any]) -> Optional[KeyMaterial]:
        """The existing keys of the vault, or None when this mode has none yet."""
        raise NotImplementedError

    def create(self, header: Dict[str, Any], fresh: bool = False) -> KeyMaterial:
        """The first key of a new vault (stored where this mode keeps it); `fresh` replaces a stale one."""
        raise NotImplementedError

    def rotate(self, header: Dict[str, Any]) -> KeyMaterial:
        """A new current key; the old one stays readable until drop_previous()."""
        raise NotImplementedError

    def drop_previous(self, header: Dict[str, Any]) -> None:
        """Removes the previous key once every entry of the vault (described by `header`) uses the current one."""

    def forget(self, header: Dict[str, Any]) -> None:
        """Removes what this mode stored for the vault (after it moved to another mode)."""


class StoredKeyProvider(KeyProvider):
    """keyring and insecure: a random key kept by a holder; a rotation keeps the old key as 'previous'
    until every entry is re-encrypted, so a crash in between loses nothing.

    `holder_for(vault_id)` returns where a vault's key is kept. With `vault_ids` (keyring) every new vault
    gets its own id, recorded in the vault header; a vault without one (made by an earlier version) uses
    the shared entry, moves to its own at the first rotation, and leaves the shared entry for the other
    stores that may still need it (the old configs/secrets.store, other LLM_FRAMEWORK_HOME folders)."""

    def __init__(self, mode: str, holder_for: Callable[[Optional[str]], Any], vault_ids: bool = False):
        self.mode = mode
        self._holder_for = holder_for
        self._vault_ids = vault_ids

    def _id(self, header: Dict[str, Any]) -> Optional[str]:
        vault_id = header.get("vault_id") if self._vault_ids else None
        return vault_id if isinstance(vault_id, str) and vault_id else None

    def _holder(self, header: Dict[str, Any]):
        return self._holder_for(self._id(header))

    def _id_header(self, header: Dict[str, Any]) -> Dict[str, Any]:
        vault_id = self._id(header)
        return {"vault_id": vault_id} if vault_id else {}

    def load(self, header):
        holder = self._holder(header)
        current = holder.get()
        if not current:
            return None
        keys = [self._parse(current)]
        previous = holder.get(previous=True)
        if previous:
            keys.append(self._parse(previous))
        return KeyMaterial(keys, self._id_header(header))

    def _parse(self, value: str) -> VaultKey:
        try:
            return parse_key(value)
        except (ValueError, TypeError):
            raise StorageError("storage.vault_unreadable",
                               f"The vault key kept by the {self.mode} storage is damaged.") from None

    def _store(self, holder, key: VaultKey) -> None:
        value = format_key(key)
        holder.set(value)
        if holder.get() != value:
            raise StorageUnavailable("storage.keyring_unavailable",
                                     f"The {self.mode} storage does not keep the vault key.")

    def create(self, header, fresh=False):
        if self._vault_ids:
            header = {"vault_id": new_vault_id()}  # a new vault never takes over another vault's key
        elif not fresh:
            existing = self.load(header)
            if existing:
                return existing
        holder = self._holder(header)
        key = new_key()
        self._store(holder, key)
        if fresh:
            holder.delete(previous=True)
        return KeyMaterial([key], self._id_header(header))

    def rotate(self, header):
        old_holder = self._holder(header)
        current = old_holder.get()
        old = [self._parse(current)] if current else []
        if self._vault_ids and not self._id(header):
            # Off the shared entry: the old key stays there for the other stores (and for a crash meanwhile).
            header = {"vault_id": new_vault_id()}
            holder = self._holder(header)
        else:
            holder = old_holder
            if current:
                holder.set(current, previous=True)
        key = new_key()
        self._store(holder, key)
        return KeyMaterial([key, *old], self._id_header(header))

    def drop_previous(self, header):
        self._holder(header).delete(previous=True)

    def forget(self, header):
        if self._vault_ids and not self._id(header):
            return  # the shared entry of earlier versions may still protect other stores
        holder = self._holder(header)
        holder.delete(previous=True)
        holder.delete()


_derived_cache: Dict[tuple, bytes] = {}
_derived_lock = threading.Lock()


def clear_derived_keys() -> None:
    """Forgets the keys derived from passphrases and helper output in this process."""
    with _derived_lock:
        _derived_cache.clear()


def new_scrypt_params() -> Dict[str, Any]:
    return {"name": "scrypt", "salt": base64.b64encode(secrets.token_bytes(16)).decode("ascii"),
            "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P}


def derive_key(secret: str, params: Dict[str, Any]) -> bytes:
    """Fernet key = scrypt(secret, salt) with parameters from the vault header, within fixed limits (a
    tampered header can neither weaken nor exhaust the derivation)."""
    try:
        if params.get("name") != "scrypt":
            raise ValueError("unknown key derivation")
        salt = base64.b64decode(params["salt"], validate=True)
        n, r, p = (int(params[k]) for k in ("n", "r", "p"))
        for name, value in (("n", n), ("r", r), ("p", p)):
            low, high = _SCRYPT_LIMITS[name]
            if not low <= value <= high:
                raise ValueError(f"scrypt {name}={value} outside {low}..{high}")
        if n & (n - 1) or len(salt) < 16:
            raise ValueError("invalid scrypt parameters")
    except (KeyError, TypeError, ValueError) as e:
        raise StorageError("storage.vault_unreadable",
                           f"The key derivation recorded in the vault is invalid ({e}).") from None
    raw = hashlib.scrypt(secret.encode("utf-8"), salt=salt, n=n, r=r, p=p,
                         maxmem=128 * r * (n + p + 2) + (1 << 20), dklen=32)
    return base64.urlsafe_b64encode(raw)


class DerivedKeyProvider(KeyProvider):
    """helper and passphrase: the key is derived from a secret string; the vault header records the scrypt
    parameters and a check token, so a wrong secret is detected before anything is written."""

    def __init__(self, mode: str, secret_source: Callable[[bool], str], cache_id: str):
        self.mode = mode
        self._secret = secret_source
        self._cache_id = cache_id

    def _wrong_secret(self) -> StorageUnavailable:
        if self.mode == "passphrase":
            return StorageUnavailable("storage.wrong_passphrase",
                                      "The passphrase is wrong; the credential vault was not changed.")
        return StorageUnavailable("storage.helper_mismatch",
                                  "The output of the credential helper does not open the credential vault; "
                                  "it was not changed. Check secret_helper.")

    def load(self, header):
        params = header.get("kdf")
        kid = header.get("kid")
        if not isinstance(params, dict) or not isinstance(kid, str):
            return None
        cache_key = (self._cache_id, self.mode, json.dumps(params, sort_keys=True))
        with _derived_lock:
            key = _derived_cache.get(cache_key)
        if key is None:
            key = derive_key(self._secret(False), params)
        vault_key = VaultKey(kid, key)
        check = header.get("check")
        if not isinstance(check, str) or not check_matches([vault_key], check):
            raise self._wrong_secret()
        with _derived_lock:
            _derived_cache[cache_key] = key
        return KeyMaterial([vault_key], {"kdf": params})

    def create(self, header, fresh=False):
        params = new_scrypt_params()
        key = VaultKey(secrets.token_hex(4), derive_key(self._secret(True), params))
        with _derived_lock:
            _derived_cache[(self._cache_id, self.mode, json.dumps(params, sort_keys=True))] = key.key
        return KeyMaterial([key], {"kdf": params})

    def rotate(self, header):
        return self.create(header, fresh=True)


class EphemeralKeyProvider(KeyProvider):
    mode = "ephemeral"
    persistent = False

    def load(self, header):
        return None

    def create(self, header, fresh=False):
        return KeyMaterial([new_key()])

    def rotate(self, header):
        return KeyMaterial([new_key()])


# ----------------------------------------------------------------------------------------------------------
# Secret sources for the derived modes
# ----------------------------------------------------------------------------------------------------------

PassphrasePrompt = Callable[[bool], Optional[str]]
_passphrase_prompt: Optional[PassphrasePrompt] = None


def set_passphrase_prompt(prompt: Optional[PassphrasePrompt]) -> None:
    """Installs the passphrase dialog (the GUI does); `prompt(new)` returns the passphrase or None when the
    user cancels. Without one, a terminal prompt is used when there is a terminal."""
    global _passphrase_prompt
    _passphrase_prompt = prompt


def _terminal_prompt(new: bool) -> Optional[str]:
    try:
        interactive = sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        interactive = False
    if not interactive:
        return None
    if not new:
        return getpass.getpass("Passphrase of the credential vault: ")
    first = getpass.getpass("New passphrase for the credential vault (8 characters or more): ")
    if first and getpass.getpass("Repeat the passphrase: ") != first:
        raise StorageUnavailable("storage.passphrase_needed", "The passphrases do not match; nothing was changed.")
    return first


def ask_passphrase(new: bool) -> str:
    prompt = _passphrase_prompt or _terminal_prompt
    passphrase = prompt(new)
    if passphrase:
        # The same characters typed in another terminal, keyboard layout or the GUI may arrive decomposed.
        passphrase = unicodedata.normalize("NFC", passphrase)
    if not passphrase:
        raise StorageUnavailable(
            "storage.passphrase_needed",
            "The credential vault is protected by a passphrase, but none was entered (a run without a terminal "
            "cannot ask for it). Start it from a terminal or the GUI, or choose another secret_storage: "
            f"{choices_text()}.")
    if new and len(passphrase) < 8:
        raise StorageUnavailable("storage.passphrase_needed",
                                 "The passphrase is too short (8 characters or more); nothing was changed.")
    return passphrase


def helper_argv(command: str) -> List[str]:
    if os.name == "nt":
        return [part[1:-1] if len(part) > 1 and part[0] == part[-1] == '"' else part
                for part in shlex.split(command, posix=False)]
    return shlex.split(command)


def run_helper(command: str) -> str:
    """The credential helper's output (stripped). Run as an argument list without a shell, without stdin,
    with a timeout; its output never reaches a log or an error message."""
    try:
        argv = helper_argv(command or "")
    except ValueError as e:
        raise StorageUnavailable("storage.helper_failed", f"secret_helper cannot be parsed ({e}).") from None
    if not argv:
        raise StorageUnavailable("storage.helper_failed",
                                 "secret_storage is 'helper' but secret_helper is empty. Set the command, e.g. "
                                 "secret_helper: \"pass show llmcc/vault-key\".")
    name = os.path.basename(argv[0])
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=HELPER_TIMEOUT,
                                check=False, shell=False)  # an argument list, never a shell
    except FileNotFoundError:
        raise StorageUnavailable("storage.helper_failed", f"The credential helper '{name}' was not found.") from None
    except subprocess.TimeoutExpired:
        raise StorageUnavailable("storage.helper_failed",
                                 f"The credential helper '{name}' did not finish within {HELPER_TIMEOUT:g} s.") from None
    except OSError as e:
        raise StorageUnavailable("storage.helper_failed",
                                 f"The credential helper '{name}' cannot be started ({type(e).__name__}).") from None
    if result.returncode != 0:
        detail = redact_secrets(result.stderr.decode("utf-8", "replace").strip().splitlines()[0][:200]
                                if result.stderr.strip() else "")
        raise StorageUnavailable("storage.helper_failed",
                                 f"The credential helper '{name}' failed (exit code {result.returncode})"
                                 + (f": {detail}" if detail else "."))
    output = result.stdout.decode("utf-8", "replace").strip()
    if not output:
        raise StorageUnavailable("storage.helper_failed", f"The credential helper '{name}' printed nothing.")
    return output


def make_provider(mode: str, *, helper: Optional[str], key_file: Path, cache_id: str) -> KeyProvider:
    """The provider of `mode`. Raises StorageUnavailable when it cannot be used (e.g. no keyring)."""
    if mode == "keyring":
        KeyringHolder()  # probes the keyring: raises when it cannot be used
        return StoredKeyProvider("keyring", lambda vault_id: KeyringHolder(vault_service(vault_id)), vault_ids=True)
    if mode == "insecure":
        return StoredKeyProvider("insecure", lambda vault_id: KeyFileHolder(key_file))
    if mode == "passphrase":
        return DerivedKeyProvider("passphrase", ask_passphrase, cache_id)
    if mode == "helper":
        if not (helper or "").strip():
            run_helper("")  # raises the explanation
        return DerivedKeyProvider("helper", lambda new: run_helper(helper), cache_id)
    if mode == "ephemeral":
        return EphemeralKeyProvider()
    raise StorageError("storage.invalid_mode",
                       f"Unknown secret_storage '{mode}'. Choose one of: {', '.join(STORAGE_MODES)}.")


# ----------------------------------------------------------------------------------------------------------
# The audit log's HMAC key (moved unchanged from tools/framework_tools.py by consolidation chain c1-imports)
# ----------------------------------------------------------------------------------------------------------

AUDIT_KEY_ID = "audit-hmac-key"
_KEY_THREAD_LOCK = threading.Lock()


@contextlib.contextmanager
def _key_lock(state_dir: Path, timeout: float = 30.0):
    """Serializes the audit key's get-or-create between threads and processes (GUI, CLI, MCP servers) with a
    lock file in the per-user folder (the shared file_lock helper). Without a usable lock file it goes on
    unlocked. The lock library is imported here: the storage names of this module are read without it (RG1)."""
    from orchestrator.utils.file_lock import file_lock
    deadline = time.monotonic() + timeout
    if not _KEY_THREAD_LOCK.acquire(timeout=timeout):
        raise TimeoutError("another thread holds the audit key lock")
    try:
        with contextlib.ExitStack() as stack:
            try:
                remaining = max(0.0, deadline - time.monotonic())
                stack.enter_context(file_lock(Path(state_dir) / "audit-key.lock", timeout=remaining))
            except TimeoutError:  # LockTimeout: another process holds it
                raise
            except (OSError, NotImplementedError):
                pass  # no usable lock file (a file in the way, a read-only folder): go on unlocked
            yield
    finally:
        _KEY_THREAD_LOCK.release()


def _state_dir(framework) -> Path:
    try:
        path = getattr(getattr(framework, "config", None), "user_state_dir", None)
    except OSError:
        path = None
    if isinstance(path, (str, Path)):
        return Path(path)
    return Path(os.environ.get("LLM_FRAMEWORK_HOME") or Path.home() / ".llm-framework")


def audit_key(framework, state_dir: Optional[Path] = None) -> Optional[bytes]:
    """The audit HMAC key: its own entry in the OS keyring, per user like the audit database itself, and
    never in a file an editor of the database could also read. None (an unkeyed chain, reported by
    `llm-cli doctor`) when no keyring is available. A key is created under a lock, so two first starts at
    the same time (GUI and MCP server) end up with the same key. A key kept under the keyring service name from
    before the rename is used and copied to the new name (read_entry), so the existing audit chain stays verifiable."""
    if not keyring_usable():  # probed once per process; a hung keyring must not stop the boot
        return None
    try:
        import keyring
        from orchestrator.Core.secrets_manager import SecretsManager
        service = SecretsManager.SERVICE_ID
        key = read_entry(keyring, service, AUDIT_KEY_ID)
        if not key:
            with _key_lock(Path(state_dir) if state_dir else _state_dir(framework)):
                key = read_entry(keyring, service, AUDIT_KEY_ID)  # another process may have been first
                if not key:
                    legacy = None
                    sm = getattr(framework, "secrets_manager", None)
                    if sm is not None:  # 3.0.0 development builds kept the key in the secrets store
                        try:
                            legacy = sm.get_secret("audit_hmac_key")
                        except Exception:
                            legacy = None
                    keyring.set_password(service, AUDIT_KEY_ID, legacy or secrets.token_hex(32))
                    key = keyring.get_password(service, AUDIT_KEY_ID)  # None if the backend keeps nothing
        return key.encode("utf-8") if key else None
    except Exception:
        return None
