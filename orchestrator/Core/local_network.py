#!/usr/bin/env python3
"""
The person's opt-outs of offline mode's locality rule (DECISIONS round 24, "L-NET final", rule d of
orchestrator/utils/locality.py): "count this VPN / this host / this address range as local", each shown with a plain
explanation of what can then leave the network. The person decides what their own network allows; the framework does
not patronize, and an AI never decides it.

Person-only. The one code path behind File > Settings... > Local network (orchestrator/gui/settings_window.py) and
`llmcf config local-network`; no module an AI can reach imports this one (tests/test_local_network.py follows every
import of the tool modules, the MCP server, the provider layer and Ditto), AISettingsChange and MCP update_settings
refuse its settings (offline_local_vpns, offline_local_hosts, offline_local_ranges), and they are no GUI-tool field.

check() runs the rule for an address or host the person types (a lookup through the system resolver: the window runs
it in a worker thread, never on the GUI thread).
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

from orchestrator.utils.locality import (VPNS, Locality, VpnCheck, check_reach, normal_host, parse_address,
                                         parse_network, tailscale_vpn)
from orchestrator.utils.localization import ui_text

KINDS = ("vpn", "host", "range")
SETTINGS = {"vpn": "offline_local_vpns", "host": "offline_local_hosts", "range": "offline_local_ranges"}
_LABEL = re.compile(r"^[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?$")


class LocalNetworkError(ValueError):
    """An opt-out that cannot be added or removed (`code`: kind, vpn, host, host_is_address, range, not_saved); the
    message is in the language it was asked in."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class OptOut:
    """One thing the person counts as local: `kind` (vpn, host, range) and its `value` as stored."""
    kind: str
    value: str

    def label(self, lang: Optional[str] = None) -> str:
        return ui_text("net.vpn.tailscale", lang) if self.kind == "vpn" and self.value == "tailscale" else self.value

    def kind_label(self, lang: Optional[str] = None) -> str:
        return ui_text(f"net.kind.{self.kind}", lang)

    def explanation(self, lang: Optional[str] = None) -> str:
        """What can then leave the person's network, in plain words."""
        return explain(self.kind, self.value, lang)


def _config_file(config: Any) -> str:
    return str(Path(getattr(config, "config_dir", ".")) / "config.yml")


def _listed(config: Any, key: str) -> List[str]:
    value = config.get(key, []) if hasattr(config, "get") else []
    if isinstance(value, str):
        value = value.split(",")
    return [str(v).strip() for v in (value or []) if str(v).strip()] if isinstance(value, (list, tuple)) else []


def normalize(kind: str, value: str, lang: Optional[str] = None) -> str:
    """The value as it is stored: a known VPN id, a host name in lower case without a trailing dot, a range as
    address/prefix. LocalNetworkError when it is not one."""
    text = str(value or "").strip()
    if kind == "vpn":
        if text.lower() not in VPNS:
            raise LocalNetworkError(ui_text("net.error.vpn", lang, value=text), "vpn")
        return text.lower()
    if kind == "host":
        name = normal_host(text)
        if parse_address(name) is not None:
            raise LocalNetworkError(ui_text("net.error.host_is_address", lang, value=name), "host_is_address")
        try:
            name = name.encode("idna").decode("ascii")
        except UnicodeError:
            raise LocalNetworkError(ui_text("net.error.host", lang, value=text), "host") from None
        if not name or len(name) > 253 or not all(_LABEL.match(label) for label in name.split(".")):
            raise LocalNetworkError(ui_text("net.error.host", lang, value=text), "host")
        return name
    if kind == "range":
        network = parse_network(text)
        if network is None:
            raise LocalNetworkError(ui_text("net.error.range", lang, value=text), "range")
        return str(network)
    raise LocalNetworkError(ui_text("net.error.kind", lang), "kind")


def explain(kind: str, value: str, lang: Optional[str] = None) -> str:
    """The plain explanation of what can leave the network once `value` counts as local."""
    if kind not in KINDS:
        raise LocalNetworkError(ui_text("net.error.kind", lang), "kind")
    return ui_text(f"net.explain.{kind}", lang, value=value)


def opt_outs(config: Any) -> List[OptOut]:
    """Everything the person counts as local, VPNs first, then hosts, then ranges (entries that are not valid are
    left out, as the rule leaves them out)."""
    found: List[OptOut] = []
    for kind in KINDS:
        for value in _listed(config, SETTINGS[kind]):
            try:
                found.append(OptOut(kind, normalize(kind, value, "en")))
            except LocalNetworkError:
                continue
    return list(dict.fromkeys(found))


def _save(config: Any, key: str, values: Any, before: Any, lang: Optional[str]) -> None:
    from orchestrator.utils.locality import clear_cache
    save = getattr(config, "save_user_config", None)
    if not (config.set(key, values) and (save() if callable(save) else True)):
        config.set(key, before)   # the setting stays as it was, also in this program
        raise LocalNetworkError(ui_text("net.error.not_saved", lang, path=_config_file(config)), "not_saved")
    clear_cache()   # the VPN check and the cached verdicts start again with the new opt-outs


def add_opt_out(config: Any, kind: str, value: str, lang: Optional[str] = None) -> OptOut:
    """Counts `value` (a VPN id, a host name or an address range) as local in offline mode and saves config.yml.
    The caller has shown the person explain() and the person said yes. LocalNetworkError when the value is not one
    or nothing could be saved."""
    stored = normalize(kind, value, lang)
    key = SETTINGS[kind]
    before = config.get(key, []) if hasattr(config, "get") else []
    current = _listed(config, key)
    if stored not in current:
        _save(config, key, current + [stored], before, lang)
    return OptOut(kind, stored)


def remove_opt_out(config: Any, kind: str, value: str, lang: Optional[str] = None) -> bool:
    """Stops counting `value` as local and saves config.yml; False when it was not counted as local."""
    try:
        stored = normalize(kind, value, lang)
    except LocalNetworkError:
        stored = str(value or "").strip()
    key = SETTINGS.get(kind)
    if key is None:
        raise LocalNetworkError(ui_text("net.error.kind", lang), "kind")
    before = config.get(key, []) if hasattr(config, "get") else []
    current = _listed(config, key)
    kept = [v for v in current if v != stored and v.lower() != stored.lower()]
    if len(kept) == len(current):
        return False
    _save(config, key, kept, before, lang)
    return True


def build_sources(config: Any) -> dict:
    """The person's build sources in the own network (locality.BUILD_SOURCE_SETTINGS: apt mirror, apt cache proxy,
    pip index), each as stored ("" when not set)."""
    from orchestrator.utils.locality import BUILD_SOURCE_SETTINGS
    return {key: str((config.get(key, "") if hasattr(config, "get") else "") or "").strip()
            for key in BUILD_SOURCE_SETTINGS}


def set_build_source(config: Any, key: str, url: str, lang: Optional[str] = None) -> str:
    """Sets one build source (a key of locality.BUILD_SOURCE_SETTINGS) to `url`, or clears it with "", and saves
    config.yml. Person-only (this module): an offline build of the build container image then fetches apt or pip
    packages through it, after the locality rule judged it on that build (Core/build_image.py). LocalNetworkError when
    the key is not one, the address is not a plain http(s) address without credentials, or nothing could be saved."""
    from orchestrator.Core.build_image import mirror_problem
    from orchestrator.utils.locality import BUILD_SOURCE_SETTINGS
    if key not in BUILD_SOURCE_SETTINGS:
        raise LocalNetworkError(ui_text("bimg.error.key", lang, setting=key, keys=", ".join(BUILD_SOURCE_SETTINGS)), "kind")
    value = str(url or "").strip()
    if value:
        problem = mirror_problem(value)
        if problem:
            raise LocalNetworkError(ui_text("bimg.error.url", lang, setting=key, problem=problem), "host")
    before = config.get(key, "") if hasattr(config, "get") else ""
    _save(config, key, value, before, lang)
    return value


def check(config: Any, target: str) -> Locality:
    """The rule for an address, host or URL the person typed, with their opt-outs: what offline mode may reach
    (locality.check_reach; a lookup: never on the GUI thread)."""
    return check_reach(target, config)


def vpn_status() -> VpnCheck:
    """The check of this computer's Tailscale or Headscale VPN (rule b; the tailscale CLI, once per run)."""
    return tailscale_vpn()


def check_text(verdict: Locality, lang: Optional[str] = None) -> str:
    """The verdict of check() in the person's words."""
    key = "net.check.local" if verdict.local else "net.check.not_local"
    text = ui_text(key, lang, host=verdict.host or ui_text("net.subject", lang), why=verdict.why(lang))
    if verdict.addresses:
        text += "\n" + ui_text("net.check.addresses", lang, addresses=", ".join(verdict.addresses[:8]))
    return text


def vpn_text(check_result: VpnCheck, lang: Optional[str] = None) -> str:
    state = ui_text("net.vpn.state.local" if check_result.local else "net.vpn.state.not_local", lang,
                    why=check_result.why(lang))
    return ui_text("net.vpn.state", lang, state=state)


__all__ = ["KINDS", "LocalNetworkError", "OptOut", "SETTINGS", "add_opt_out", "build_sources", "check", "check_text",
           "explain", "normalize", "opt_outs", "remove_opt_out", "set_build_source", "vpn_status", "vpn_text"]
