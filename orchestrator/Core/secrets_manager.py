#!/usr/bin/env python3
"""
LLM Cross-Compiler Framework - Enterprise Secrets Manager (v2.3.0)
DIREKTIVE: Goldstandard Security.
FEATURES:
  - OS-Keyring Integration (Windows Credential Locker / Linux Keyring)
  - PBKDF2 Key Derivation (HMAC-SHA256, 100k Iterations)
  - Audit Logging für Access-Events
  - Keine Speicherung des Master-Keys im Dateisystem!
  - Integrated with FrameworkManager (v2.3 Update)
"""

import os
import json
import base64
import hashlib
import logging
import platform
import getpass
import tempfile
import threading
from functools import wraps
from pathlib import Path
from typing import Dict, Optional, Any
from datetime import datetime

# Security Libs
try:
    import keyring
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    SECURITY_AVAILABLE = True
except ImportError:
    SECURITY_AVAILABLE = False

from orchestrator.utils.logging import get_logger

class SecurityError(Exception):
    """Kritischer Sicherheitsfehler"""
    pass


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
    SERVICE_ID = "llm-conversion-framework"
    KEY_ID = "master-encryption-key"

    def __init__(self, framework_manager):
        self.logger = get_logger("SecretsManager")
        self.framework = framework_manager
        
        # Integration: Pfade aus Config holen
        # Fallback für Standalone-Test: Wenn framework_manager ein Path ist (altes Verhalten)
        state_dir = None
        if isinstance(framework_manager, Path):
            self.config_dir = framework_manager
        else:
            self.config_dir = Path(getattr(framework_manager.config, 'config_dir', None) or "config")
            try:
                state_dir = getattr(framework_manager.config, "user_state_dir", None)
            except OSError:
                state_dir = None

        # Secrets are personal: one store per user (next to the tool permissions and the audit log), the
        # same for the GUI, the CLI and the MCP server wherever they are started. Older versions kept it in
        # the config folder; it is taken over once.
        self._legacy_file: Optional[Path] = None
        if isinstance(state_dir, (str, Path)):
            self.secrets_file = Path(state_dir) / "secrets.store"
            legacy = self.config_dir / "secrets.store"
            if not _same_path(legacy, self.secrets_file):
                self._legacy_file = legacy
        else:
            self.secrets_file = self.config_dir / "secrets.store"
        # Lists the old stores already taken over, so a secret deleted later never comes back.
        self._migrated_file = self.secrets_file.with_name("secrets.store.migrated")
        # Next to the store, never in the working directory (it names the user and the secrets read).
        self.audit_log_file = self.secrets_file.with_name("secrets-audit.log")
        self._cipher = None
        self._cache = {}
        self._signature: Any = None  # stat of the store files when the cache was loaded
        self._load_failed = False  # a damaged store is never overwritten
        self._failed_file: Optional[Path] = None  # the file that could not be read
        self._initialized = False
        self._lock = threading.RLock()

        if not SECURITY_AVAILABLE:
            raise SecurityError(
                "CRITICAL: 'cryptography' or 'keyring' missing. "
                "Install via: pip install cryptography keyring"
            )

    @_locked
    def initialize(self) -> bool:
        """
        Initialisiert die Krypto-Engine.
        Wrapper für Framework-Boot.
        """
        try:
            self._initialize_crypto()
            self._initialized = True
            return True
        except Exception as e:
            self.logger.critical(f"SecretsManager init failed: {e}")
            return False

    def _initialize_crypto(self):
        """
        Initialisiert die Krypto-Engine. 
        Versucht Key aus OS-Keyring zu laden oder generiert neuen.
        """
        try:
            # 1. Versuche Key aus dem OS-Keyring zu holen
            stored_key = keyring.get_password(self.SERVICE_ID, self.KEY_ID)

            if stored_key:
                self.logger.info("Master Key loaded from OS Keyring (Secure).")
                # Stored as the base64 Fernet key itself (see below), which is what Fernet() expects.
                key_bytes = stored_key.encode()
            else:
                self.logger.warning("No Master Key found in Keyring. Generating new one...")
                # 2. Generiere neuen Key (Fernet kompatibel)
                # Wir nutzen hier PBKDF2 um aus zufälligen Bytes einen robusten Key zu machen
                salt = os.urandom(16)
                password = os.urandom(32) 
                
                kdf = PBKDF2HMAC(
                    algorithm=hashes.SHA256(),
                    length=32,
                    salt=salt,
                    iterations=100000,
                )
                key_bytes = base64.urlsafe_b64encode(kdf.derive(password))
                
                # Speichere im Keyring
                keyring.set_password(self.SERVICE_ID, self.KEY_ID, key_bytes.decode())
                self.logger.info("New Master Key generated and stored in OS Keyring.")

            self._cipher = Fernet(key_bytes)
            self._load_store()

        except Exception as e:
            self.logger.critical(f"Crypto Initialization Failed: {e}")
            raise SecurityError(f"Keystore Access Failed: {e}")

    def _audit_log(self, action: str, key_name: str, status: str):
        """Schreibt Audit-Trail für Compliance."""
        timestamp = datetime.now().isoformat()
        user = getpass.getuser()
        entry = f"[{timestamp}] USER={user} ACTION={action} KEY={key_name} STATUS={status}\n"
        
        try:
            # Stelle sicher, dass das Verzeichnis existiert
            self.audit_log_file.parent.mkdir(parents=True, exist_ok=True)
            # O_BINARY: on Windows a descriptor is text mode otherwise, and lines would end in \r\r\n
            flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0)
            fd = os.open(self.audit_log_file, flags, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                if platform.system() != "Windows":
                    os.fchmod(f.fileno(), 0o600)
                f.write(entry)
        except Exception:
            pass

    def _refresh(self):
        """Re-reads the store when another process changed, added or removed it since it was loaded."""
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
        return True

    @_locked
    def set_secret(self, key: str, value: str) -> bool:
        """Verschlüsselt und speichert ein Secret."""
        if not self._initialized: self.initialize() 
        
        if not self._cipher or not self._reload_for_write():
            return False
        
        try:
            encrypted_val = self._cipher.encrypt(value.encode()).decode()
            self._cache[key] = encrypted_val
            if not self._save_store():
                self._signature = None  # the cache holds a change that was not saved: read the file again
                return False
            self._audit_log("WRITE", key, "SUCCESS")
            return True
        except Exception as e:
            self.logger.error(f"Encryption failed for {key}: {e}")
            self._audit_log("WRITE", key, f"FAILED: {e}")
            return False

    @_locked
    def get_secret(self, key: str) -> Optional[str]:
        """Entschlüsselt und liest ein Secret."""
        if not self._initialized: self.initialize() 

        if not self._cipher:
            return None
        self._refresh()  # long-running processes (MCP server, GUI) see changes made by others
        if key not in self._cache:
            return None
        
        try:
            encrypted_val = self._cache[key]
            decrypted_val = self._cipher.decrypt(encrypted_val.encode()).decode()
            self._audit_log("READ", key, "SUCCESS")
            return decrypted_val
        except Exception as e:
            self.logger.error(f"Decryption failed for {key}: {e}")
            self._audit_log("READ", key, "DECRYPTION_FAIL - POSSIBLE TAMPERING")
            return None

    @_locked
    def list_secrets(self) -> list[str]:
        """Listet verfügbare Keys (nicht Values)."""
        self._refresh()
        return list(self._cache.keys())

    @_locked
    def refresh(self) -> bool:
        """Re-reads a store changed by another process; False when it (or the old store) cannot be read."""
        self._refresh()
        return not self._load_failed

    @property
    def failed_file(self) -> Optional[Path]:
        """The store file that could not be read (the per-user store or the old one), if any."""
        return self._failed_file if self._load_failed else None

    @_locked
    def delete_secret(self, key: str) -> bool:
        """Löscht ein Secret."""
        if not self._initialized: self.initialize()
        if not self._reload_for_write():
            return False
        if key in self._cache:
            del self._cache[key]
            if not self._save_store():
                self._signature = None
                return False
            self._audit_log("DELETE", key, "SUCCESS")
            return True
        return False

    def _save_store(self) -> bool:
        """Writes the encrypted store atomically (temp file, mode 0600, then rename), so a reader never
        sees a half-written file."""
        try:
            self._write_atomic(self.secrets_file, self._cache)
            return True
        except Exception as e:
            self.logger.error(f"Failed to save secrets store: {e}")
            return False

    @staticmethod
    def _write_atomic(path: Path, data: Any) -> None:
        # Through a link (e.g. onto an encrypted volume), so the link is kept instead of being replaced.
        path = Path(os.path.realpath(path))
        tmp = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".secrets.", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            if platform.system() != "Windows":
                os.chmod(tmp, 0o600)
            os.replace(tmp, path)
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

    def _fail(self, path: Path, error: Exception) -> None:
        self._load_failed = True
        self._failed_file = path
        self.logger.error(f"Failed to load secrets store {path}: {error}")

    def _load_store(self):
        """Loads the store and takes over the store of the old location once."""
        # Taken before reading: a change made meanwhile is read on the next access.
        self._signature = self._current_signature()
        try:
            data = self._read_store(self.secrets_file)
        except FileNotFoundError as e:
            if os.path.lexists(self.secrets_file):
                # A link to a file that is gone (unmounted drive): its secrets are unknown, so never write.
                self._fail(self.secrets_file, e)
                return
            # Moved aside or deleted on purpose: its secrets must not be written back from memory.
            data = {}
        except Exception as e:
            self._fail(self.secrets_file, e)
            return
        self._cache = data
        self._load_failed = False
        self._failed_file = None
        self._take_over_legacy()

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
        already in the per-user store win; the old file is left untouched."""
        legacy = self._legacy_file
        if legacy is None:
            return
        done = self._migrated_stores()
        if done is None:
            return
        legacy_id = os.path.realpath(legacy)
        if legacy_id in done["stores"]:
            return
        try:
            raw = legacy.read_bytes()
            data = self._parse_store(raw)
        except FileNotFoundError:
            return
        except Exception as e:
            # Its secrets would be lost silently: refuse changes until it is repaired or moved aside.
            self._fail(legacy, e)
            return
        digest = hashlib.sha256(raw).hexdigest()
        # The same file under another path (installation folder moved, other mount point) was taken over already.
        seen = digest in done["sha256"]
        added = {} if seen else {k: v for k, v in data.items() if k not in self._cache}
        if added:
            self._cache.update(added)
            if not self._save_store():
                return  # tried again on the next load
        record = {"stores": done["stores"] + [legacy_id], "sha256": done["sha256"] + ([] if seen else [digest])}
        try:
            self._write_atomic(self._migrated_file, record)
        except Exception as e:
            self.logger.warning(f"Could not record the takeover of {legacy} in {self._migrated_file}: {e}")
        self.logger.info(f"Secrets of the old store {legacy} taken over into {self.secrets_file} "
                         f"({len(added)} added; the old file is left in place).")

if __name__ == "__main__":
    # Smoke Test
    logging.basicConfig(level=logging.INFO)
    tmp_dir = Path("temp_secrets_test")
    tmp_dir.mkdir(exist_ok=True)
    
    # Test with Path (Legacy Mode)
    sm = SecretsManager(tmp_dir)
    sm.initialize()
    
    print("Saving Secret...")
    sm.set_secret("api_token", "super-secret-value-123")
    
    print("Reading Secret...")
    val = sm.get_secret("api_token")
    print(f"Decrypted: {val}")
    
    if val == "super-secret-value-123":
        print("✅ TEST PASSED")
    else:
        print("❌ TEST FAILED")
