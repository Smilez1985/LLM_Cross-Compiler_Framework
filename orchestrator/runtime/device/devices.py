#!/usr/bin/env python3
"""Finding a stored device (by its ID, its label or its address) and its SSH address."""

from typing import Any, List, Optional, Tuple

from orchestrator.remote.address import Endpoint
from orchestrator.runtime.profile import DEVICE_ID, ProfileStore
from orchestrator.utils.next_steps import WithSteps


class DeviceNotFound(WithSteps, LookupError):
    """No stored device, or more than one, matches; the message lists what is known."""


def resolve_device(store: ProfileStore, text: str) -> str:
    """The device ID of a stored device named by its ID (or a unique prefix of at least 4 characters), its label or
    its address, as `llm-cli device list` shows them."""
    text = (text or "").strip()
    devices = store.list_devices()
    if not devices:
        raise DeviceNotFound("no device is stored yet: probe one with llm-cli scan --device HOST --user USER")
    if DEVICE_ID.fullmatch(text) and text in devices:
        return text
    matches: List[str] = []
    for device_id in devices:
        info = store.device_info(device_id)
        if text and (text == info.get("label") or text == info.get("address")
                     or (len(text) >= 4 and device_id.startswith(text.lower()))):
            matches.append(device_id)
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise DeviceNotFound(f"no stored device is called {text[:64]!r} (llm-cli device list shows them)")
    raise DeviceNotFound(f"{text[:64]!r} names {len(matches)} devices; use the device ID")


def split_address(address: Optional[str]) -> Tuple[str, int]:
    """(host, port) of an address as the profile store keeps it: host, host:port or [IPv6]:port (Endpoint.parse)."""
    if not address:
        raise ValueError("the device has no stored address: probe it with llm-cli scan --device HOST --user USER")
    endpoint = Endpoint.parse(address)
    return endpoint.host, endpoint.port


def board_address(store: Any, device_id: str) -> Tuple[str, int]:
    """(host, port) of a stored board from its stored address; ValueError when it has none or it is not host[:port].
    The one reader (person_steps.board_address delegates here, so a module an AI can reach needs no person step)."""
    return split_address(store.device_info(device_id).get("address"))


__all__ = ["DeviceNotFound", "board_address", "resolve_device", "split_address"]
