#!/usr/bin/env python3
"""
Devices a person trusted and paired (MCP design 2.7, decision 2): <state dir>/devices.json.

A record is written only by the person's own channels, the GUI and the CLI, after the person compared the device's
SSH host key fingerprint and the framework logged in to it (`llm-cli deploy run`, `llm-cli scan --device`,
`llm-cli device runtime deploy`, the GUI's Deployment window, and `llm-cli deploy devices add` for a device that is
already in the framework's known_hosts). No tool writes, changes or removes a record: an AI only ever reads them
(list_devices) and names a device by its id (deploy_to_device), and a deploy then reaches exactly the host, port, user
and host key the person confirmed.

The device id is the one the hardware profile store uses (orchestrator.runtime.profile.device_id_for_host_key): the
first 16 hex characters of the sha256 of the host key's fingerprint. It stays the same when the address changes and
names no host. A record is usable only while the framework's known_hosts still holds exactly that key for its address:
a key the person removed there (to trust a reinstalled device again) makes the record stale until the person trusts
the device again in the GUI or the CLI.

The time of the last deploy is kept apart (device_deploys.json), so the deploy tool never writes the trust records.
"""

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from orchestrator.utils.file_lock import file_lock

DEVICES_FILE = "devices.json"
DEPLOYS_FILE = "device_deploys.json"
TRUSTED_VIA = ("cli", "gui")
MAX_BYTES = 256 * 1024
MAX_DEVICES = 200

DEVICE_ID_RE = re.compile(r"^[0-9a-f]{16}$", re.ASCII)
_FINGERPRINT = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$", re.ASCII)
_HOST = re.compile(r"^(?:[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|[0-9A-Fa-f:.]{2,45})$", re.ASCII)
_USER = re.compile(r"^[a-z_][a-z0-9_.-]{0,31}$", re.ASCII | re.IGNORECASE)
_KEY_TYPE = re.compile(r"^[a-z0-9@.-]{3,64}$", re.ASCII)
_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", re.ASCII)


class DeviceStoreError(ValueError):
    """A record that cannot be stored; the message names the field."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean_label(label: Any) -> str:
    """A person's name for a device: 1-64 printable characters, spaces collapsed."""
    text = " ".join(str(label or "").split())
    if not text or len(text) > 64 or not text.isprintable():
        raise DeviceStoreError("a device label has 1 to 64 printable characters")
    return text


@dataclass(frozen=True)
class TrustedDevice:
    device_id: str
    label: str
    host: str
    port: int
    user: str
    key_type: str
    fingerprint: str
    paired: bool
    trusted_at: str
    trusted_via: str

    @property
    def address(self) -> str:
        return self.host if self.port == 22 else (f"[{self.host}]:{self.port}" if ":" in self.host
                                                  else f"{self.host}:{self.port}")

    def check(self) -> "TrustedDevice":
        from orchestrator.runtime.profile import device_id_for_host_key
        if not DEVICE_ID_RE.fullmatch(self.device_id):
            raise DeviceStoreError("a device id is 16 lower-case hex characters")
        if not _FINGERPRINT.fullmatch(self.fingerprint) or device_id_for_host_key(self.fingerprint) != self.device_id:
            raise DeviceStoreError("the fingerprint does not belong to the device id")
        if not _HOST.fullmatch(self.host) or self.host.startswith("-"):
            raise DeviceStoreError("the host is not a host name or an IP address")
        if not isinstance(self.port, int) or isinstance(self.port, bool) or not 1 <= self.port <= 65535:
            raise DeviceStoreError("the port is not between 1 and 65535")
        if not _USER.fullmatch(self.user):
            raise DeviceStoreError("the user is not an account name")
        if not _KEY_TYPE.fullmatch(self.key_type):
            raise DeviceStoreError("the key type is not an SSH key type")
        clean_label(self.label)
        if self.trusted_via not in TRUSTED_VIA or not _STAMP.fullmatch(self.trusted_at):
            raise DeviceStoreError("the record does not say when and where the person trusted the device")
        if not isinstance(self.paired, bool):
            raise DeviceStoreError("paired is true or false")
        return self


class TrustedDevices:
    """The trusted devices of this user (devices.json in the per-user folder)."""

    def __init__(self, state_dir: Path):
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / DEVICES_FILE
        self.deploys_path = self.state_dir / DEPLOYS_FILE

    @classmethod
    def for_framework(cls, framework: Any) -> "TrustedDevices":
        from orchestrator.runtime.profile import default_state_dir
        state = getattr(getattr(framework, "config", None), "user_state_dir", None)
        return cls(Path(state) if isinstance(state, (str, Path)) else default_state_dir())

    # --- reading ---

    def _read(self, path: Path) -> Dict[str, Any]:
        try:
            with open(path, "rb") as f:
                raw = f.read(MAX_BYTES + 1)
        except FileNotFoundError:
            return {}
        except OSError:
            return {}
        try:
            data = json.loads(raw) if len(raw) <= MAX_BYTES else None
        except (ValueError, RecursionError):
            data = None
        return data if isinstance(data, dict) else {}

    def list(self) -> List[TrustedDevice]:
        """Every valid record, by label; a record that does not pass its checks is left out."""
        found = []
        for device_id, row in list(self._read(self.path).items())[:MAX_DEVICES]:
            if not isinstance(row, dict):
                continue
            try:
                found.append(TrustedDevice(device_id=str(device_id), **{k: row[k] for k in (
                    "label", "host", "port", "user", "key_type", "fingerprint", "paired", "trusted_at",
                    "trusted_via")}).check())
            except (KeyError, TypeError, DeviceStoreError, ValueError):
                continue
        return sorted(found, key=lambda d: (d.label.lower(), d.device_id))

    def get(self, device_id: str) -> Optional[TrustedDevice]:
        if not isinstance(device_id, str) or not DEVICE_ID_RE.fullmatch(device_id):
            return None
        return next((d for d in self.list() if d.device_id == device_id), None)

    def key_status(self, device: TrustedDevice) -> str:
        """'known' while the framework's known_hosts holds exactly the record's key for its address; 'changed' when
        it holds another key there; 'unknown' when it holds none (the person removed it). Reads no network."""
        from orchestrator.utils import ssh_trust
        try:
            entries = ssh_trust._host_keys(self.state_dir).lookup(ssh_trust.host_entry(device.host, device.port))
        except Exception:  # noqa: BLE001 - no paramiko or a damaged known_hosts: nothing is known
            return "unknown"
        if not entries:
            return "unknown"
        stored = entries.get(device.key_type)
        if stored is None:
            return "changed"
        return "known" if ssh_trust.fingerprint(stored) == device.fingerprint else "changed"

    def last_deploys(self) -> Dict[str, Dict[str, Any]]:
        rows = self._read(self.deploys_path)
        return {k: v for k, v in rows.items() if DEVICE_ID_RE.fullmatch(str(k)) and isinstance(v, dict)}

    # --- writing: the person's channels only (GUI and CLI), never a tool ---

    def record(self, host: str, port: int, user: str, key: Any, *, via: str, paired: bool,
               label: Optional[str] = None) -> TrustedDevice:
        """Stores (or updates) the record of a device whose host key `key` the person confirmed and that the framework
        logged in to (`paired`: the framework's own SSH key works there). Called only by the GUI and the CLI."""
        from orchestrator.runtime.profile import ProfileStore, device_id_for_host_key
        from orchestrator.utils import ssh_trust
        fp = ssh_trust.fingerprint(key)
        device_id = device_id_for_host_key(fp)
        with file_lock(self.state_dir / ".devices.lock"):
            rows = self._read(self.path)
            old = rows.get(device_id) if isinstance(rows.get(device_id), dict) else {}
            if label is None:
                label = old.get("label") or ProfileStore(self.state_dir).device_info(device_id).get("label") or host
            device = TrustedDevice(device_id=device_id, label=clean_label(label), host=host, port=int(port),
                                   user=user, key_type=key.get_name(), fingerprint=fp,
                                   paired=bool(paired) or bool(old.get("paired")) and old.get("fingerprint") == fp,
                                   trusted_at=_now(), trusted_via=via).check()
            row = asdict(device)
            row.pop("device_id")
            rows[device_id] = row
            if len(rows) > MAX_DEVICES:
                raise DeviceStoreError(f"at most {MAX_DEVICES} devices are kept; remove one first")
            self._write(self.path, rows)
        return device

    def remove(self, device_id: str) -> bool:
        with file_lock(self.state_dir / ".devices.lock"):
            rows = self._read(self.path)
            if device_id not in rows:
                return False
            del rows[device_id]
            self._write(self.path, rows)
        return True

    def note_deploy(self, device_id: str, ok: bool, package: str = "") -> None:
        """The last deploy to a device (shown by list_devices); kept apart from the trust records."""
        if not DEVICE_ID_RE.fullmatch(device_id or ""):
            return
        try:
            with file_lock(self.state_dir / ".devices.lock"):
                rows = self.last_deploys()
                rows[device_id] = {"at": _now(), "ok": bool(ok), "package": str(package)[:64]}
                self._write(self.deploys_path, rows)
        except (OSError, TimeoutError):
            pass  # a deploy is never failed by its bookkeeping

    def _write(self, path: Path, rows: Dict[str, Any]) -> None:
        from orchestrator.Core.vault_key import write_private
        text = json.dumps(rows, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
        if len(text) > MAX_BYTES:
            raise DeviceStoreError("the device records are too large")
        write_private(path, text)


def record_trusted_device(state_dir: Path, host: str, port: int, user: str, key: Any, *, via: str, paired: bool,
                          label: Optional[str] = None, audit: Any = None, actor: str = "") -> Optional[TrustedDevice]:
    """For the GUI and the CLI after a successful login to a device the person trusted: stores its record and writes
    the trust to the audit log (design 6.7: host-key trust and pairing are audited). Never fails the caller's work:
    None when the record could not be stored."""
    try:
        device = TrustedDevices(state_dir).record(host, port, user, key, via=via, paired=paired, label=label)
    except Exception:  # noqa: BLE001 - the deploy or probe itself worked; the record is a convenience for tools
        return None
    if audit is not None:
        try:
            audit.record(actor or via, "device_trusted", "external", "ask", "ok",
                         arguments={"device_id": device.device_id, "fingerprint": device.fingerprint,
                                    "paired": device.paired},
                         approved=True, extra={"confirm_channel": f"framework-{via}", "answer": "accepted"})
        except Exception:  # noqa: BLE001 - logged by the audit log itself
            pass
    return device


__all__ = ["DEVICE_ID_RE", "DeviceStoreError", "TrustedDevice", "TrustedDevices", "clean_label",
           "record_trusted_device"]
