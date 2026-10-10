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
import logging
import platform
import getpass
import tempfile
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
            self.audit_log_file = self.config_dir / "audit.log"
        else:
            self.config_dir = Path(getattr(framework_manager.config, 'config_dir', None) or "config")
            self.audit_log_file = (Path(framework_manager.config.logs_dir) if hasattr(framework_manager.config, 'logs_dir') else Path("logs")) / "audit.log"
            try:
                state_dir = getattr(framework_manager.config, "user_state_dir", None)
            except OSError:
                state_dir = None

        # Secrets are personal: one store per user (next to the tool permissions and the audit log), the
        # same for the GUI, the CLI and the MCP server wherever they are started. Older versions kept it in
        # the config folder; it is copied over once.
        if isinstance(state_dir, (str, Path)):
            self.secrets_file = Path(state_dir) / "secrets.store"
            self._legacy_file: Optional[Path] = self.config_dir / "secrets.store"
        else:
            self.secrets_file = self.config_dir / "secrets.store"
            self._legacy_file = None
        self._cipher = None
        self._cache = {}
        self._load_failed = False  # a damaged store is never overwritten
        self._initialized = False 

        if not SECURITY_AVAILABLE:
            raise SecurityError(
                "CRITICAL: 'cryptography' or 'keyring' missing. "
                "Install via: pip install cryptography keyring"
            )

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
            with open(self.audit_log_file, "a") as f:
                f.write(entry)
        except Exception:
            pass 

    def _reload_for_write(self) -> bool:
        """Re-reads the store before a change (another process may have written it since) and refuses to
        write when it cannot be read: saving the in-memory copy would erase every other secret."""
        self._load_store()
        if self._load_failed:
            self.logger.error(f"The secrets store {self.secrets_file} cannot be read; not changing it. "
                              "Repair or remove the file, then try again.")
            return False
        return True

    def set_secret(self, key: str, value: str) -> bool:
        """Verschlüsselt und speichert ein Secret."""
        if not self._initialized: self.initialize() 
        
        if not self._cipher or not self._reload_for_write():
            return False
        
        try:
            encrypted_val = self._cipher.encrypt(value.encode()).decode()
            self._cache[key] = encrypted_val
            if not self._save_store():
                return False
            self._audit_log("WRITE", key, "SUCCESS")
            return True
        except Exception as e:
            self.logger.error(f"Encryption failed for {key}: {e}")
            self._audit_log("WRITE", key, f"FAILED: {e}")
            return False

    def get_secret(self, key: str) -> Optional[str]:
        """Entschlüsselt und liest ein Secret."""
        if not self._initialized: self.initialize() 

        if not self._cipher or key not in self._cache:
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

    def list_secrets(self) -> list[str]:
        """Listet verfügbare Keys (nicht Values)."""
        return list(self._cache.keys())

    def delete_secret(self, key: str) -> bool:
        """Löscht ein Secret."""
        if not self._initialized: self.initialize()
        if not self._reload_for_write():
            return False
        if key in self._cache:
            del self._cache[key]
            if not self._save_store():
                return False
            self._audit_log("DELETE", key, "SUCCESS")
            return True
        return False

    def _save_store(self) -> bool:
        """Writes the encrypted store atomically (temp file, mode 0600, then rename), so a reader never
        sees a half-written file."""
        tmp = None
        try:
            self.secrets_file.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.secrets_file.parent, prefix=".secrets.", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            if platform.system() != "Windows":
                os.chmod(tmp, 0o600)
            os.replace(tmp, self.secrets_file)
            return True
        except Exception as e:
            self.logger.error(f"Failed to save secrets store: {e}")
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            return False

    def _load_store(self):
        """Loads the store (copying a store from the old location once)."""
        if not self.secrets_file.exists() and self._legacy_file is not None and self._legacy_file.is_file():
            try:
                self.secrets_file.parent.mkdir(parents=True, exist_ok=True)
                data = json.loads(self._legacy_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._cache = data
                    if self._save_store():
                        self.logger.info(f"Secrets moved to {self.secrets_file} (old copy left in {self._legacy_file}).")
            except Exception as e:
                self.logger.warning(f"Could not take over the old secrets store {self._legacy_file}: {e}")
        if not self.secrets_file.exists():
            self._load_failed = False
            return
        
        try:
            with open(self.secrets_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("not a JSON object")
            self._cache = data
            self._load_failed = False
        except Exception as e:
            self._load_failed = True
            self.logger.error(f"Failed to load secrets store {self.secrets_file}: {e}")

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
