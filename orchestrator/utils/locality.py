#!/usr/bin/env python3
"""
The one network-locality rule of offline mode (DECISIONS round 24, L-NET and "L-NET final"): in offline mode the
framework reaches only hosts on this computer or in the person's own network, and the person decides what their own
network allows (no patronizing). Every offline decision that reaches out asks this module: the AI provider's address
(llm/client.py, llm/settings_change.py), Ollama's or a custom server's address, the pinned llama.cpp source from a
mirror (runtime/build/source.py); the provider presets' local/cloud labels follow it for an address-dependent preset
(llm/catalog.py is_local_url).

(a) A host is judged by EVERY address it resolves to. Local are loopback (127.0.0.0/8, ::1; the unspecified address
    0.0.0.0 / :: never leaves this computer either), private addresses (RFC 1918: 10/8, 172.16/12, 192.168/16, and
    fc00::/7) and link-local addresses (169.254/16, fe80::/10); IPv4 inside IPv6 (::ffff:a.b.c.d) is judged as IPv4.
    A name decides nothing by itself: a single-label name or a home suffix (HOME_SUFFIXES: .fritz.box, .home, .lan,
    .local, .home.arpa, .internal) is local only when it resolves to such addresses, a name never makes a public
    address local, and a name that resolves to both local and public addresses is not local. localhost and
    *.localhost are this computer (RFC 6761) and are not looked up.
(b) Tailscale's ranges (VPN_NETWORKS: 100.64.0.0/10, fd7a:115c:a1e0::/48) are not local by default: traffic and
    coordination can go through servers outside the own network. They count as local only when the VPN is Headscale
    in the person's own network AND its DERP map holds only relays in the own network (tailscale_vpn(): the tailscale
    CLI when present, `tailscale debug prefs` for ControlURL and `tailscale debug derp-map` for Regions -> Nodes ->
    HostName / IPv4 / IPv6, every host judged by (a); Tailscale's own control server is not local; anything that
    cannot be read is not local).
(c) A public name (a reverse proxy such as Caddy with a DuckDNS name) resolves to a public address: not local by
    default.
(d) The person can count a VPN, a host or an address range as local (OptOuts: the settings offline_local_vpns,
    offline_local_hosts and offline_local_ranges). Only the person sets them, in File > Settings... or with
    `llmcf config local-network` (Core/local_network.py, which no AI-reachable code imports); AISettingsChange and MCP
    update_settings refuse them.
(e) The lookup uses the system resolver only (socket.getaddrinfo: no DNS-over-HTTPS, no other library), with a short
    timeout (RESOLVE_TIMEOUT), never on the GUI thread (mark_ui_thread: a lookup there is refused as "not checked";
    the GUI checks in a worker first), and is cached for the run (failures for FAILURE_TTL seconds). Where a
    connection follows the check, it goes only to the checked addresses (connect_addresses: every address the check
    judged, tried in turn as the system's own connection would, a link-local one with its zone; a localhost name to
    the loopback addresses), so a DNS answer that changes between the check and the connection cannot reach a public
    host.

offline_mode(config) is the one offline check: every caller that asks whether offline mode is on reads it here.

Only the standard library is imported at load time; the texts (orchestrator/utils/l10n_network.py) and the next
steps come from the framework's text table and step catalogue when they are needed.
"""

import ipaddress
import json
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    List,
    Optional,
    Sequence,
    Tuple,
    Union,
)
from urllib.parse import urlsplit

IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]
IPNetwork = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]

# --- the networks of rule (a) and (b) ------------------------------------------------------------------------------

LOOPBACK_NETWORKS = (ipaddress.ip_network("127.0.0.0/8"), ipaddress.ip_network("::1/128"))
PRIVATE_NETWORKS = (ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("172.16.0.0/12"),
                    ipaddress.ip_network("192.168.0.0/16"), ipaddress.ip_network("fc00::/7"))
LINK_LOCAL_NETWORKS = (ipaddress.ip_network("169.254.0.0/16"), ipaddress.ip_network("fe80::/10"))
# Tailscale's address ranges (CGNAT space and its ULA prefix, which lies inside fc00::/7: checked first).
VPN_NETWORKS = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))
HOME_SUFFIXES = (".fritz.box", ".home", ".lan", ".local", ".home.arpa", ".internal")
LOCALHOST_NAMES = ("localhost",)
# Tailscale's own coordination servers (ipn.DefaultControlURL and its synonym, ipn.IsLoginServerSynonym).
TAILSCALE_CONTROL_HOSTS = ("controlplane.tailscale.com", "login.tailscale.com")
TAILSCALE_DOMAIN = ".tailscale.com"

# The VPNs a person can count as local (offline_local_vpns), with the ranges they bring.
VPNS: Dict[str, Tuple[IPNetwork, ...]] = {"tailscale": VPN_NETWORKS}

# The person's opt-outs (rule d); only Core/local_network.py writes them.
OPT_OUT_SETTINGS = ("offline_local_vpns", "offline_local_hosts", "offline_local_ranges")

RESOLVE_TIMEOUT = 2.0    # seconds for one lookup through the system resolver
FAILURE_TTL = 30.0       # a failed lookup is asked again after this many seconds (a success is kept for the run)
CLI_TIMEOUT = 5.0        # seconds for one tailscale CLI command

# Verdict reasons. Local: this computer, the own network, the own Headscale VPN, or one of the person's opt-outs.
LOCAL_REASONS = ("this_computer", "own_network", "own_vpn", "opt_out_host", "opt_out_range", "opt_out_vpn")
# Not local: a public address, a name with local and public addresses, a VPN address whose VPN is not the person's own
# Headscale, a name that could not be resolved, no host at all, a lookup refused on the GUI thread.
NOT_LOCAL_REASONS = ("public", "mixed", "vpn", "unresolved", "no_host", "not_checked")
# The refusals an opt-out of the person would lift, and the next step that names it (utils/next_steps/common.py).
OPT_OUT_HELPS = ("public", "mixed", "vpn")
OPT_OUT_STEP = "settings.local_network"


# --- offline mode --------------------------------------------------------------------------------------------------

def offline_mode(config: Any) -> bool:
    """Offline mode (the setting offline_mode) of `config`: a ConfigManager, a dict or a stand-in with get(), or an
    object that has an offline_mode attribute instead. False when it cannot be read, as every earlier reader had it
    (hf_hub.offline, crawl_jobs, rag_manager, build_service, build_plan, Ditto, the community, target and runtime
    tools, the module wizard)."""
    if config is None:
        return False
    try:
        get = getattr(config, "get", None)
        value = get("offline_mode", False) if callable(get) else getattr(config, "offline_mode", False)
    except Exception:  # noqa: BLE001 - an unreadable setting is "not offline", as before
        return False
    return bool(value)


# --- addresses -----------------------------------------------------------------------------------------------------

def parse_address(text: Any) -> Optional[IPAddress]:
    """An IP address (IPv4 inside IPv6 unwrapped, an IPv6 scope such as %eth0 dropped); None for anything else."""
    try:
        ip = ipaddress.ip_address(str(text).strip().strip("[]").split("%", 1)[0])
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def address_kind(address: Any) -> str:
    """"loopback" (this computer), "private", "link_local", "vpn" (Tailscale's ranges, rule b), "public" (any other
    address, also multicast and reserved ranges) or "invalid"."""
    ip = parse_address(address)
    if ip is None:
        return "invalid"
    if ip.is_unspecified:
        return "loopback"      # 0.0.0.0 / :: is this computer; a connection to it never leaves it
    if any(ip in net for net in VPN_NETWORKS if net.version == ip.version):
        return "vpn"
    for kind, nets in (("loopback", LOOPBACK_NETWORKS), ("private", PRIVATE_NETWORKS),
                       ("link_local", LINK_LOCAL_NETWORKS)):
        if any(ip in net for net in nets if net.version == ip.version):
            return kind
    return "public"


def normal_host(host: Any) -> str:
    """A host name as compared here: lower case, without brackets and without a trailing dot."""
    return str(host or "").strip().strip("[]").rstrip(".").lower()


def is_localhost_name(host: str) -> bool:
    name = normal_host(host)
    return name in LOCALHOST_NAMES or name.endswith(".localhost")


def is_home_name(host: str) -> bool:
    """A single-label name or one with a home suffix: names that usually live in the own network (they still count only
    by their addresses, rule a)."""
    name = normal_host(host)
    return bool(name) and parse_address(name) is None and ("." not in name or name.endswith(HOME_SUFFIXES))


def url_host(url: Any) -> Tuple[str, Optional[int]]:
    """(host, port) of a URL, of a bare host[:port] or of a bare IPv6 address; ("", None) when there is none."""
    text = str(url or "").strip()
    if not text:
        return "", None
    if ":" in text and "/" not in text and parse_address(text) is not None:
        return normal_host(text), None      # a bare IPv6 address (fd00::1, fe80::1%eth0): no port to split off
    try:
        parts = urlsplit(text if "//" in text else f"//{text}")
        port = parts.port
    except ValueError:
        return "", None
    return normal_host(parts.hostname or ""), port


# --- the person's opt-outs (rule d) --------------------------------------------------------------------------------

@dataclass(frozen=True)
class OptOuts:
    """What the person counts as local: VPNs (ids of VPNS), host names and address ranges."""
    vpns: FrozenSet[str] = frozenset()
    hosts: FrozenSet[str] = frozenset()
    ranges: Tuple[IPNetwork, ...] = ()

    @classmethod
    def of(cls, config: Any) -> "OptOuts":
        """The opt-outs in the settings of `config` (entries that are not valid are left out)."""
        if config is None:
            return cls()
        if isinstance(config, OptOuts):
            return config

        def listed(key: str) -> List[str]:
            try:
                get = getattr(config, "get", None)
                value = get(key, []) if callable(get) else []
            except Exception:  # noqa: BLE001 - an unreadable setting holds no opt-out
                return []
            if isinstance(value, str):
                value = [v for v in value.split(",")]
            return [str(v).strip() for v in (value or []) if str(v).strip()] if isinstance(value, (list, tuple)) \
                else []

        vpns = frozenset(v.lower() for v in listed("offline_local_vpns") if v.lower() in VPNS)
        hosts = frozenset(normal_host(h) for h in listed("offline_local_hosts") if normal_host(h))
        ranges = tuple(net for net in (parse_network(r) for r in listed("offline_local_ranges")) if net is not None)
        return cls(vpns, hosts, ranges)

    def covers_address(self, ip: IPAddress) -> bool:
        return any(ip in net for net in self.ranges if net.version == ip.version)

    def __bool__(self) -> bool:
        return bool(self.vpns or self.hosts or self.ranges)


def parse_network(text: Any) -> Optional[IPNetwork]:
    """An address range (10.8.0.0/24, 2001:db8::/48, or one address); None when it is none."""
    try:
        return ipaddress.ip_network(str(text).strip(), strict=False)
    except ValueError:
        return None


# --- the system resolver (rule e) ----------------------------------------------------------------------------------

class LookupFailed(LookupError):
    """The name could not be resolved through the system resolver (or not in time)."""


class LookupOnUiThread(LookupError):
    """A name that is not cached yet was to be looked up on the GUI thread (refused: the GUI checks in a worker)."""


_lock = threading.Lock()
_resolved: Dict[str, Tuple[str, ...]] = {}          # host -> addresses, for the run
_failed: Dict[str, Tuple[float, str]] = {}          # host -> (monotonic time, why), for FAILURE_TTL
_ui_thread: List[Optional[int]] = [None]


def mark_ui_thread(ident: Optional[int] = None) -> None:
    """The GUI thread (default: the calling thread): no lookup and no tailscale command runs on it."""
    _ui_thread[0] = threading.get_ident() if ident is None else ident


def on_ui_thread() -> bool:
    return _ui_thread[0] is not None and threading.get_ident() == _ui_thread[0]


def clear_cache() -> None:
    """Forgets every lookup and the VPN check (the next check asks again)."""
    with _lock:
        _resolved.clear()
        _failed.clear()
        _vpn_cache.clear()


def _system_lookup(host: str) -> List[str]:
    """Every address of `host` from the system resolver (getaddrinfo), in its order. A link-local IPv6 address keeps
    its zone (getaddrinfo gives it as the scope id beside the text, e.g. for an mDNS .local name on macOS or
    Windows): a connection to fe80::... fails without it. The zone does not change how the address is judged."""
    found = []
    for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP):
        sockaddr = info[4]
        text = str(sockaddr[0])
        if "%" not in text and len(sockaddr) >= 4 and sockaddr[3]:
            text = f"{text}%{sockaddr[3]}"
        found.append(text)
    return found


def _address_text(text: str) -> str:
    """An address as a connection uses it: the address itself (IPv4 inside IPv6 unwrapped), with the zone of a
    link-local IPv6 address kept."""
    ip = parse_address(text)
    if ip is None:
        return str(text)
    zone = str(text).strip().strip("[]").split("%", 1)[1] if "%" in str(text) else ""
    return f"{ip}%{zone}" if zone and ip.version == 6 else str(ip)


def cached(host: str) -> Optional[Tuple[str, ...]]:
    """The addresses of `host` when they were resolved in this run already (None otherwise)."""
    with _lock:
        return _resolved.get(normal_host(host))


def resolve(host: str, timeout: float = RESOLVE_TIMEOUT) -> Tuple[str, ...]:
    """Every address `host` resolves to through the system resolver, cached for the run. LookupFailed when it cannot be
    resolved within `timeout` seconds, LookupOnUiThread on the GUI thread for a name not resolved yet."""
    name = normal_host(host)
    with _lock:
        if name in _resolved:
            return _resolved[name]
        failed = _failed.get(name)
        if failed is not None and time.monotonic() - failed[0] < FAILURE_TTL:
            raise LookupFailed(failed[1])
    if on_ui_thread():
        raise LookupOnUiThread(f"{name} was not checked yet")
    result: Dict[str, Any] = {}

    def run():
        try:
            result["addresses"] = _system_lookup(name)
        except (OSError, UnicodeError, ValueError) as e:
            result["error"] = str(e) or type(e).__name__

    worker = threading.Thread(target=run, name="locality-lookup", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        why = f"no answer from the system resolver within {timeout:g} seconds"
    elif "error" in result or not result.get("addresses"):
        why = result.get("error") or "the name has no address"
    else:
        addresses = tuple(dict.fromkeys(result["addresses"]))
        with _lock:
            _resolved[name] = addresses
            _failed.pop(name, None)
        return addresses
    with _lock:
        _failed[name] = (time.monotonic(), why)
    raise LookupFailed(why)


# --- the verdict ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Locality:
    """Whether `host` counts as local, why (`reason`, one of LOCAL_REASONS or NOT_LOCAL_REASONS), the addresses it
    was judged by and those that are not local. A connection after the check goes to `address`."""
    host: str
    local: bool
    reason: str
    addresses: Tuple[str, ...] = ()
    outside: Tuple[str, ...] = ()
    detail: str = ""      # why a lookup failed
    vpn_check: Optional["VpnCheck"] = None   # the VPN check behind a "vpn" verdict

    @property
    def address(self) -> Optional[str]:
        """The checked address a connection goes to (the first one); None when the host is not local."""
        return self.addresses[0] if self.local and self.addresses else None

    def why(self, lang: str = "en") -> str:
        """Why the host counts as local or not, in plain words (English by default, as the framework's refusals are;
        the window and the command line pass the person's language)."""
        from orchestrator.utils.localization import ui_text
        shown = ", ".join(self.outside[:3]) + (" ..." if len(self.outside) > 3 else "")
        detail = self.vpn_check.why(lang) if self.vpn_check is not None else self.detail
        return ui_text(f"net.why.{self.reason}", lang, host=self.host or ui_text("net.subject", lang),
                       addresses=shown or ui_text("net.why.an_address", lang),
                       detail=(f" ({detail})" if detail else "") if self.reason == "unresolved" else detail)

    def refusal(self, what: str = "", lang: str = "en") -> str:
        """The plain refusal of a reach-out in offline mode: the host, why it is not local, and (when it would help)
        that the person can count it as local; the next step itself is `steps`."""
        from orchestrator.utils.localization import ui_text
        subject = f"{what} ({self.host})" if what and self.host else (what or self.host or ui_text("net.subject", lang))
        text = ui_text("net.refusal", lang, subject=subject, why=self.why(lang))
        if self.reason in OPT_OUT_HELPS:
            text += ui_text("net.refusal.opt_out", lang)
        return text

    @property
    def steps(self) -> Tuple[str, ...]:
        """The next step that goes with the refusal: the person's opt-out (rule d), when it would help."""
        return (OPT_OUT_STEP,) if not self.local and self.reason in OPT_OUT_HELPS else ()


class LocalityRefused(PermissionError):
    """A connection in offline mode to a host that is not local (`locality` says why; `steps`: the person's opt-out
    when it would help, as the framework's next steps carry it)."""

    def __init__(self, locality: Locality, what: str = ""):
        super().__init__(locality.refusal(what))
        self.locality = locality
        from orchestrator.utils.next_steps import as_next_steps
        self.steps = as_next_steps(list(locality.steps))


Resolver = Callable[[str], Sequence[str]]


def _judge(host: str, addresses: Sequence[str], optouts: OptOuts, vpn: Callable[[], "VpnCheck"]) -> Locality:
    """The verdict for `host` by its `addresses` (rule a, b and d)."""
    addresses = tuple(dict.fromkeys(addresses))
    if not addresses:
        return Locality(host, False, "unresolved")
    if host and normal_host(host) in optouts.hosts:
        return Locality(host, True, "opt_out_host", addresses)
    found, public, vpn_outside, vpn_check = set(), [], [], None
    for text in addresses:
        ip = parse_address(text)
        kind = address_kind(ip) if ip is not None else "invalid"
        if kind == "loopback":
            found.add("this_computer")
        elif ip is not None and optouts.covers_address(ip):
            found.add("opt_out_range")
        elif kind in ("private", "link_local"):
            found.add("own_network")
        elif kind == "vpn":
            if any(ip in net for vid in optouts.vpns for net in VPNS.get(vid, ()) if net.version == ip.version):
                found.add("opt_out_vpn")
            else:
                check = vpn()
                if check.local:
                    found.add("own_vpn")
                else:
                    vpn_outside.append(text)
                    vpn_check = check
        else:
            public.append(text)
    if not public and not vpn_outside:   # every address is local: the reason that needed the most
        reason = next(r for r in ("opt_out_vpn", "opt_out_range", "own_vpn", "own_network", "this_computer")
                      if r in found)
        return Locality(host, True, reason, addresses)
    outside = tuple(public + vpn_outside)
    if not public:                       # only VPN addresses are outside: the VPN is not the person's own
        return Locality(host, False, "vpn", addresses, outside, vpn_check=vpn_check)
    return Locality(host, False, "mixed" if len(outside) < len(addresses) else "public", addresses, outside)


def check_host(host: str, config: Any = None, *, resolver: Optional[Resolver] = None,
               vpn: Optional[Callable[[], "VpnCheck"]] = None) -> Locality:
    """Whether `host` (a name or an address) counts as local under the person's opt-outs in `config` (a settings
    object, an OptOuts, or None for none). Names are resolved through the system resolver (resolve; `resolver` stands
    in for it in tests), every address counts."""
    name = normal_host(host)
    optouts = OptOuts.of(config)
    vpn = vpn or (lambda: tailscale_vpn())
    if not name:
        return Locality("", False, "no_host")
    if parse_address(name) is not None:
        return _judge(name, (_address_text(name),), optouts, vpn)
    if is_localhost_name(name):
        return Locality(name, True, "this_computer", ("127.0.0.1", "::1"))
    try:
        addresses = tuple(resolver(name)) if resolver is not None else resolve(name)
    except LookupOnUiThread:
        return Locality(name, False, "not_checked")
    except (LookupFailed, OSError, UnicodeError, ValueError) as e:
        return Locality(name, False, "unresolved", (), (), str(e)[:200])
    return _judge(name, addresses, optouts, vpn)


def check_url(url: Any, config: Any = None, *, resolver: Optional[Resolver] = None,
              vpn: Optional[Callable[[], "VpnCheck"]] = None) -> Locality:
    """check_host for the host of `url` (a URL or host[:port]); "no_host" when it names none."""
    host, _port = url_host(url)
    return check_host(host, config, resolver=resolver, vpn=vpn)


def label(url: Any, config: Any = None) -> Optional[bool]:
    """Whether `url` counts as local, without a lookup: an address, localhost or a name resolved earlier in this run;
    None when it would need a lookup (a GUI label shows "not checked yet" then)."""
    host, _port = url_host(url)
    if not host:
        return False
    if needs_lookup(host):
        return None
    return check_host(host, config, resolver=lambda name: cached(name) or ()).local


def needs_lookup(url: Any) -> bool:
    """True when checking `url` would ask the resolver (a name that is not cached yet) or run the VPN check (a
    Tailscale address, by itself or behind a cached name, while this computer's VPN was not checked yet): the GUI
    then checks it in a worker thread first."""
    host, _port = url_host(url)
    if not host or is_localhost_name(host):
        return False
    addresses = (host,) if parse_address(host) is not None else cached(host)
    if addresses is None:
        return True
    with _lock:
        vpn_checked = "tailscale" in _vpn_cache
    return not vpn_checked and any(address_kind(a) == "vpn" for a in addresses)


def connect_addresses(host: str, config: Any = None, what: str = "") -> Tuple[str, ...]:
    """Every checked address a connection to `host` may go to (rule e), in the resolver's order: the address itself,
    the addresses of a name that counts as local, or the loopback addresses of a localhost name (never looked up
    again, so no DNS server decides where a localhost name goes); LocalityRefused otherwise. Uses the run's cache,
    so the connection goes where the check looked; a caller tries them in turn, as the system's own connection
    would."""
    verdict = check_host(host, config)
    if not verdict.local or not verdict.addresses:
        raise LocalityRefused(verdict, what)
    return verdict.addresses


def connect_address(host: str, config: Any = None, what: str = "") -> str:
    """The first checked address a connection to `host` goes to (connect_addresses); LocalityRefused when the host
    is not local."""
    return connect_addresses(host, config, what)[0]


# --- Tailscale and Headscale (rule b) ------------------------------------------------------------------------------

@dataclass(frozen=True)
class VpnCheck:
    """Whether the Tailscale-compatible VPN of this computer is the person's own: Headscale in the own network with only
    own relays. `reason`: own_headscale, no_cli, unreadable, tailscale_control, control_outside, relay_outside,
    not_checked."""
    local: bool
    reason: str
    control: str = ""
    relays: Tuple[str, ...] = ()
    detail: str = ""

    def why(self, lang: str = "en") -> str:
        from orchestrator.utils.localization import ui_text
        return ui_text(f"net.vpn.{self.reason}", lang, control=self.control, relays=", ".join(self.relays[:3]),
                       detail=f" ({self.detail})" if self.detail else "")


_vpn_cache: Dict[str, VpnCheck] = {}
CliRunner = Callable[[Sequence[str]], str]


def _run_cli(argv: Sequence[str]) -> str:
    """The output of one tailscale CLI command, run as every helper command of the framework is
    (utils/helpers.execute_command: no shell, stdin closed, no console window on Windows when the GUI starts it,
    output decoded with replacement characters). CalledProcessError (a SubprocessError) when it fails, does not start
    or runs past CLI_TIMEOUT."""
    from orchestrator.utils.helpers import execute_command
    code, out, err = execute_command(list(argv), timeout=CLI_TIMEOUT)
    if code != 0:
        raise subprocess.CalledProcessError(code, list(argv), out, err)
    return out


def _relay_hosts(derp_map: Any) -> Optional[List[Tuple[str, List[str]]]]:
    """(HostName, the addresses to judge) of every node in a DERP map (tailcfg.DERPMap as JSON: Regions -> region ->
    Nodes -> HostName, IPv4, IPv6); None when it is not a DERP map. An address field that names an address is used
    as is; an empty one means the HostName's DNS records are used (tailcfg.DERPNode), so the name is resolved."""
    if not isinstance(derp_map, dict) or not isinstance(derp_map.get("Regions"), dict):
        return None
    found: List[Tuple[str, List[str]]] = []
    for region in derp_map["Regions"].values():
        if region is None:
            continue
        if not isinstance(region, dict) or not isinstance(region.get("Nodes") or [], list):
            return None
        for node in region.get("Nodes") or []:
            if not isinstance(node, dict):
                return None
            name = normal_host(node.get("HostName") or "")
            fields = [str(node.get(key) or "").strip() for key in ("IPv4", "IPv6")]
            literal = [f for f in fields if parse_address(f) is not None]
            dns = any(not f for f in fields)        # an empty field: that family comes from HostName's DNS
            found.append((name, literal + ([name] if dns and name else [])))
    return found


def tailscale_vpn(run: Optional[CliRunner] = None, resolver: Optional[Resolver] = None,
                  which: Optional[Callable[[str], Optional[str]]] = None) -> VpnCheck:
    """Rule (b), checked once per run: the control URL from `tailscale debug prefs` (ControlURL; empty means
    Tailscale's default control server) and the relays from `tailscale debug derp-map`, every host judged by rule (a)
    without the VPN itself. Anything that cannot be read counts as not local. `run`, `resolver` and `which` stand in
    for the CLI, the resolver and the PATH lookup in tests (a check with them is not cached)."""
    use_cache = run is None and resolver is None and which is None
    if use_cache:
        with _lock:
            if "tailscale" in _vpn_cache:
                return _vpn_cache["tailscale"]
        if on_ui_thread():
            return VpnCheck(False, "not_checked")
    result = _check_tailscale(run, resolver, which or shutil.which)
    if use_cache:
        with _lock:
            _vpn_cache["tailscale"] = result
    return result


def _check_tailscale(run: Optional[CliRunner], resolver: Optional[Resolver],
                     which: Callable[[str], Optional[str]]) -> VpnCheck:
    exe = which("tailscale")
    if not exe:
        return VpnCheck(False, "no_cli")
    run = run or _run_cli
    try:
        prefs = json.loads(run([exe, "debug", "prefs"]) or "null")
        control_url = str(prefs.get("ControlURL") or "") if isinstance(prefs, dict) else None
        if control_url is None:
            return VpnCheck(False, "unreadable", detail="no prefs")
        derp = json.loads(run([exe, "debug", "derp-map"]) or "null")
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        return VpnCheck(False, "unreadable", detail=type(e).__name__)
    control = url_host(control_url)[0] if control_url else TAILSCALE_CONTROL_HOSTS[0]
    if control in TAILSCALE_CONTROL_HOSTS or control.endswith(TAILSCALE_DOMAIN):
        return VpnCheck(False, "tailscale_control", control)
    no_vpn = lambda: VpnCheck(False, "not_checked")  # noqa: E731 - the VPN's own hosts never count by the VPN

    def plain(host: str) -> Locality:
        return check_host(host, None, resolver=resolver, vpn=no_vpn)

    if not plain(control).local:
        return VpnCheck(False, "control_outside", control)
    relays = _relay_hosts(derp)
    if relays is None:
        return VpnCheck(False, "unreadable", control, detail="no DERP map")
    outside = []
    for name, hosts in relays:
        if hosts and not all(plain(h).local for h in hosts):   # a node with no usable address relays nothing
            outside.append(name or "(a relay without a name)")
    if outside:
        return VpnCheck(False, "relay_outside", control, tuple(dict.fromkeys(outside)))
    return VpnCheck(True, "own_headscale", control)


__all__ = ["CLI_TIMEOUT", "FAILURE_TTL", "HOME_SUFFIXES", "LINK_LOCAL_NETWORKS", "LOCAL_REASONS", "LOOPBACK_NETWORKS",
           "Locality", "LocalityRefused", "LookupFailed", "LookupOnUiThread", "NOT_LOCAL_REASONS", "OPT_OUT_SETTINGS",
           "OptOuts", "PRIVATE_NETWORKS", "RESOLVE_TIMEOUT", "TAILSCALE_CONTROL_HOSTS", "VPNS", "VPN_NETWORKS",
           "VpnCheck", "address_kind", "cached", "check_host", "check_url", "clear_cache", "connect_address",
           "connect_addresses", "is_home_name", "is_localhost_name", "label", "mark_ui_thread", "needs_lookup",
           "normal_host", "offline_mode", "on_ui_thread", "parse_address", "parse_network", "resolve", "tailscale_vpn", "url_host"]
