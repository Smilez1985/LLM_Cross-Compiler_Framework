#!/usr/bin/env python3
"""
Deploy settings of a device (decision D7): chosen before a deployment, kept next to the device's hardware profile
(<state dir>/hardware/devices/<device id>.deploy.json), and never asked at deploy or run time.

- install: "user" (the default) installs the runtime into the device user's home and starts it with the start
  script; "systemd" also installs a systemd unit that starts the server at boot. The unit is written with sudo on the
  device, so it can only be chosen when the device's probe reports INIT_SYSTEM=systemd, and only with an explicit
  confirmation, given when the setting is made, that the deployment may run sudo there (kept with its time; the
  caller writes it to the audit log). The deployment then runs `sudo -n`, which never asks for a password.
- ssh_user: the account on the device. The server and every check run as this user; root is refused, because the
  server never runs as root.
- port: the loopback port of the unit's server (1024-65535); the start script takes --port and defaults to 8080.
- model: the model file the unit serves, a path on the device (relative paths are relative to the user's home).
- kv_cache: the KV-cache type of the board's server (f16, q8_0, q4_0; eighth round N7). Unset means automatic:
  q8_0 when the board has little RAM left next to the model (runtime/hardware.py kv_cache_default, from the board's
  probe), f16 otherwise. The person can always choose a type, also f16 to switch the quantized cache off.

The settings are data from a file: every field is checked when they are read, and a value that does not pass is an
error, never a guess.
"""

import re
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Mapping, Optional

from orchestrator.runtime.server_policy import DEVICE_DEFAULT_PORT, KV_CACHE_TYPES

INSTALL_MODES = ("user", "systemd")
PORT_RANGE = (1024, 65535)
_RE_USER = re.compile(r"^[a-z_][a-z0-9_.-]{0,31}$", re.ASCII | re.IGNORECASE)
_RE_MODEL = re.compile(r"^[A-Za-z0-9._+/-]{1,255}$", re.ASCII)
_RE_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", re.ASCII)
_FIELDS = ("install", "ssh_user", "port", "model", "sudo_confirmed_at", "kv_cache")


class SettingsError(ValueError):
    """Deploy settings that cannot be used; the message names the field and the way out."""


def check_ssh_user(user: Any) -> str:
    """An account name on the device (no option, no shell character); root is refused."""
    if not isinstance(user, str) or not _RE_USER.fullmatch(user):
        raise SettingsError("the SSH user must be an account name (letters, digits, '_', '.', '-'; at most 32)")
    if user == "root":
        raise SettingsError("the runtime never runs as root: use an unprivileged account on the device (on DietPi, "
                            "for example, dietpi)")
    return user


def check_model_path(path: Any) -> str:
    """A model path on the device: letters, digits and ._+/- only, no '..' component, not starting with '-'."""
    if not isinstance(path, str) or not _RE_MODEL.fullmatch(path) or path.startswith("-"):
        raise SettingsError("the model path on the device may contain letters, digits and ._+/- only and must not "
                            "start with '-'")
    if ".." in path.split("/") or "//" in path or path.endswith("/"):
        raise SettingsError("the model path on the device must name a file without '..' or empty parts")
    return path


@dataclass(frozen=True)
class KvChoice:
    """The KV-cache type a board's server gets, and why: "chosen" (the deploy settings name it), "little" (automatic:
    little RAM left next to the model, `free_gib` left), "enough" (automatic: enough RAM) or "unknown" (automatic,
    but the board was not probed: f16)."""
    kind: str
    why: str
    free_gib: Optional[float] = None

    @property
    def automatic(self) -> bool:
        return self.why != "chosen"

    def describe(self) -> str:
        """The choice in plain English (facts of a question; the person's windows use the localized texts)."""
        why = {"chosen": "the person's choice in the deploy settings",
               "little": f"automatic: little memory is left next to the model on this board ({self.free_gib} GiB)",
               "enough": "automatic: enough memory on this board",
               "unknown": "automatic: the board's memory is not known yet"}[self.why]
        return f"{self.kind} ({why})"


@dataclass(frozen=True)
class DeploySettings:
    install: str = "user"
    ssh_user: Optional[str] = None
    port: int = DEVICE_DEFAULT_PORT
    model: Optional[str] = None
    sudo_confirmed_at: Optional[str] = None
    kv_cache: Optional[str] = None

    def __post_init__(self) -> None:
        if self.install not in INSTALL_MODES:
            raise SettingsError(f"install must be one of {', '.join(INSTALL_MODES)}")
        if self.ssh_user is not None:
            check_ssh_user(self.ssh_user)
        if not isinstance(self.port, int) or isinstance(self.port, bool) or \
                not PORT_RANGE[0] <= self.port <= PORT_RANGE[1]:
            raise SettingsError(f"the port must be a number from {PORT_RANGE[0]} to {PORT_RANGE[1]}")
        if self.model is not None:
            check_model_path(self.model)
        if self.sudo_confirmed_at is not None and (not isinstance(self.sudo_confirmed_at, str)
                                                   or not _RE_TIMESTAMP.fullmatch(self.sudo_confirmed_at)):
            raise SettingsError("sudo_confirmed_at must be a UTC time stamp")
        if self.kv_cache is not None and self.kv_cache not in KV_CACHE_TYPES:
            raise SettingsError(f"the KV cache must be one of {', '.join(KV_CACHE_TYPES)} (or automatic)")

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> "DeploySettings":
        """Settings from the stored file; unknown fields are refused (the file is written by this module only)."""
        if not data:
            return cls()
        if not isinstance(data, Mapping):
            raise SettingsError("the deploy settings are not a mapping")
        unknown = sorted(str(k)[:40] for k in data if k not in _FIELDS)
        if unknown:
            raise SettingsError(f"the deploy settings have unknown fields: {', '.join(unknown)}")
        return cls(**{k: data[k] for k in _FIELDS if k in data and data[k] is not None})

    def as_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in _FIELDS if getattr(self, k) is not None}

    def changed(self, **values: Any) -> "DeploySettings":
        """A copy with the given fields changed. Leaving systemd drops the sudo confirmation, so choosing it again
        needs a new one."""
        new = replace(self, **values)
        if new.install != "systemd" and new.sudo_confirmed_at is not None:
            new = replace(new, sudo_confirmed_at=None)
        return new

    def problems(self, profile: Any) -> List[str]:
        """Why these settings cannot be deployed to the device whose stored profile is `profile` (empty: they can).
        Only validated profile values are read (INIT_SYSTEM is an enum of the schema)."""
        found: List[str] = []
        if self.install == "systemd":
            init = profile.get("INIT_SYSTEM") if profile is not None else None
            if init != "systemd":
                found.append("the systemd unit needs a device whose probe reports INIT_SYSTEM=systemd (this one "
                             f"reports {init or 'no init system'}); use the start script (install user) or probe the "
                             "device again")
            if self.sudo_confirmed_at is None:
                found.append("the systemd unit is written with sudo on the device, which needs your explicit "
                             "confirmation in the deploy settings (llm-cli device settings ... --install systemd)")
            if self.model is None:
                found.append("the systemd unit serves one model: set its path on the device with --model")
        return found

    def kv_choice(self, profile: Any, model_bytes: Optional[int] = None) -> KvChoice:
        """The KV-cache type of the board's server: the chosen one, or the automatic one from the board's RAM in its
        stored probe (hardware.kv_cache_default) and the model's size when known."""
        if self.kv_cache is not None:
            return KvChoice(self.kv_cache, "chosen")
        from orchestrator.runtime.hardware import kv_cache_default
        ram_mib = profile.int_value("RAM_TOTAL_MB") if hasattr(profile, "int_value") else None
        if not ram_mib:
            return KvChoice("f16", "unknown")
        kind, free = kv_cache_default(ram_mib * 1024 ** 2, model_bytes)
        return KvChoice(kind, "little" if kind != "f16" else "enough", free)

    def model_on_device(self, home: str) -> Optional[str]:
        """The model path as an absolute path on the device (relative paths are relative to `home`)."""
        if self.model is None:
            return None
        return self.model if self.model.startswith("/") else f"{home.rstrip('/')}/{self.model}"


def remember_ssh_user(store: Any, device_id: str, user: str) -> bool:
    """After a probe over SSH: the SSH user becomes the device's deploy user when none is set yet. Never root (the
    runtime never runs as root), never over damaged settings. True when it was stored."""
    try:
        current = DeploySettings.from_dict(store.load_deploy_settings(device_id))
        if current.ssh_user is not None:
            return False
        store.save_deploy_settings(device_id, current.changed(ssh_user=user).as_dict())
        return True
    except (SettingsError, ValueError, OSError):
        return False  # root, an unusual name or damaged settings: `llm-cli device settings` sets it


__all__ = ["DeploySettings", "INSTALL_MODES", "KvChoice", "SettingsError", "check_model_path", "check_ssh_user",
           "remember_ssh_user"]
