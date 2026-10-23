"""The one network-locality rule of offline mode (DECISIONS round 24, L-NET and "L-NET final";
orchestrator/utils/locality.py).

(a) a host counts by every address it resolves to (loopback, RFC 1918 / fc00::/7 and link-local are local; a name
never makes a public address local; local and public together is not local); (b) Tailscale's ranges only with
Headscale in the own network and only own relays, read from the tailscale CLI (field names of ipn.Prefs and
tailcfg.DERPMap); (c) a public name is not local; (d) the person's opt-outs; (e) the system resolver with a timeout,
cached for the run, never on the GUI thread, and the checked address is the one a connection uses."""

import json
import subprocess
import threading
import time

import pytest

from orchestrator.utils import locality
from orchestrator.utils.locality import (
    Locality,
    LocalityRefused,
    OptOuts,
    VpnCheck,
    address_kind,
    check_host,
    check_url,
    connect_address,
    label,
    needs_lookup,
    offline_mode,
    tailscale_vpn,
)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """No real lookup and no real tailscale command in these tests; every test starts with an empty cache."""
    locality.clear_cache()
    monkeypatch.setattr(locality, "_ui_thread", [None])

    def no_dns(host):
        raise AssertionError(f"a real lookup of {host}")
    monkeypatch.setattr(locality, "_system_lookup", no_dns)
    yield
    locality.clear_cache()


def table(**names):
    """A resolver from a table name -> addresses (a missing name does not resolve)."""
    def resolve(host):
        if host not in names:
            raise locality.LookupFailed("no such name")
        return names[host]
    return resolve


NOT_OWN = lambda: VpnCheck(False, "tailscale_control", "controlplane.tailscale.com")  # noqa: E731
OWN = lambda: VpnCheck(True, "own_headscale", "headscale.lan")  # noqa: E731


# --- (a) addresses --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("address, kind", [
    ("127.0.0.1", "loopback"), ("127.8.9.10", "loopback"), ("::1", "loopback"), ("0.0.0.0", "loopback"),
    ("::", "loopback"), ("10.1.2.3", "private"), ("172.16.0.1", "private"), ("172.31.255.255", "private"),
    ("192.168.178.20", "private"), ("fd12:3456::1", "private"), ("fc00::1", "private"),
    ("169.254.1.1", "link_local"), ("fe80::1%eth0", "link_local"), ("::ffff:192.168.1.5", "private"),
    ("::ffff:8.8.8.8", "public"), ("100.64.0.1", "vpn"), ("100.127.255.254", "vpn"),
    ("fd7a:115c:a1e0::1", "vpn"), ("8.8.8.8", "public"), ("172.32.0.1", "public"), ("2001:db8::1", "public"),
    ("192.0.2.1", "public"), ("224.0.0.1", "public"), ("[::1]", "loopback"), ("not an address", "invalid")])
def test_address_kinds(address, kind):
    assert address_kind(address) == kind


@pytest.mark.parametrize("host, addresses, local, reason", [
    ("nas", ["192.168.1.20"], True, "own_network"),                     # a single-label name
    ("fritz.box", ["192.168.178.1"], True, "own_network"),
    ("printer.fritz.box", ["192.168.178.30"], True, "own_network"),
    ("box.lan", ["10.0.0.5", "fd00::5"], True, "own_network"),
    ("server.home.arpa", ["fe80::2"], True, "own_network"),
    ("gpu.internal", ["172.20.0.4"], True, "own_network"),
    ("box.local", ["203.0.113.9"], False, "public"),                    # a home suffix never makes a public address local
    ("nas", ["192.168.1.20", "203.0.113.9"], False, "mixed"),           # local and public: not local
    ("myhome.duckdns.org", ["198.51.100.7"], False, "public"),          # (c) a reverse proxy's public name
    ("ollama.example.com", ["10.0.0.9"], True, "own_network"),          # any name counts by its addresses
    ("selfhosted.example", ["127.0.0.1"], True, "this_computer"),
])
def test_a_name_counts_by_every_address(host, addresses, local, reason):
    verdict = check_host(host, resolver=table(**{host: addresses}), vpn=NOT_OWN)
    assert (verdict.local, verdict.reason) == (local, reason)
    assert verdict.addresses == tuple(addresses)
    assert verdict.address == (addresses[0] if local else None)


def test_localhost_is_this_computer_without_a_lookup():
    for name in ("localhost", "LOCALHOST", "ollama.localhost", "localhost."):
        verdict = check_host(name, resolver=table())
        assert verdict.local and verdict.reason == "this_computer" and verdict.address == "127.0.0.1"


def test_an_unresolvable_name_and_no_host_are_not_local():
    verdict = check_host("gone.lan", resolver=table())
    assert not verdict.local and verdict.reason == "unresolved" and "gone.lan could not be resolved" in verdict.why()
    assert check_url("").reason == "no_host" and not check_url("http:///v1").local


def test_urls_and_ports():
    assert check_url("http://192.168.1.5:11434/v1").local
    assert check_url("https://[fd00::1]:8443/v1").local
    assert check_url("192.168.1.5:8000").local
    assert not check_url("http://8.8.8.8/v1").local
    assert check_url("http://nas:8000/v1", resolver=table(nas=["10.0.0.2"])).local


# --- (b) Tailscale and Headscale ------------------------------------------------------------------------------------

def test_a_tailscale_address_is_not_local_by_default():
    verdict = check_host("100.101.102.103", vpn=NOT_OWN)
    assert not verdict.local and verdict.reason == "vpn" and verdict.outside == ("100.101.102.103",)
    assert "Tailscale" in verdict.why() and "coordinated by Tailscale's own servers" in verdict.why()
    assert "count it as local" in verdict.refusal("Ollama")


def test_a_tailscale_address_is_local_in_the_own_headscale():
    assert check_host("100.101.102.103", vpn=OWN).reason == "own_vpn"
    assert check_host("fd7a:115c:a1e0::5", vpn=OWN).local
    assert not check_host("fd7a:115c:a1e0::5", vpn=NOT_OWN).local   # fd7a:: lies inside fc00::/7, the VPN rule wins
    verdict = check_host("gpu.tailnet", resolver=table(**{"gpu.tailnet": ["100.70.0.2"]}), vpn=NOT_OWN)
    assert verdict.reason == "vpn"


def cli(prefs, derp, calls=None):
    """A stand-in for `tailscale debug prefs` and `tailscale debug derp-map` (JSON as the CLI prints it)."""
    def run(argv):
        if calls is not None:
            calls.append(list(argv))
        assert argv[0] == "/usr/bin/tailscale" and argv[1] == "debug"
        out = {"prefs": prefs, "derp-map": derp}[argv[2]]
        if isinstance(out, Exception):
            raise out
        return out if isinstance(out, str) else json.dumps(out)
    return run


def which(name):
    return "/usr/bin/tailscale" if name == "tailscale" else None


def derp_map(*nodes):
    """A tailcfg.DERPMap as JSON: Regions keyed by region id, each with its Nodes."""
    return {"Regions": {"900": {"RegionID": 900, "RegionCode": "home", "RegionName": "Home",
                                "Nodes": [dict(Name=f"900{chr(97 + i)}", RegionID=900, **n)
                                          for i, n in enumerate(nodes)]}}}


HEADSCALE = {"ControlURL": "https://headscale.lan:8080", "WantRunning": True}
NAMES = table(**{"headscale.lan": ["192.168.1.2"], "derp.lan": ["192.168.1.3"],
                 "derp1.tailscale.com": ["199.38.181.104"], "derp.mixed": ["192.168.1.4", "203.0.113.1"]})


def test_headscale_with_only_own_relays_is_the_own_vpn():
    calls = []
    check = tailscale_vpn(cli(HEADSCALE, derp_map({"HostName": "derp.lan"}), calls), NAMES, which)
    assert check == VpnCheck(True, "own_headscale", "headscale.lan")
    assert calls == [["/usr/bin/tailscale", "debug", "prefs"], ["/usr/bin/tailscale", "debug", "derp-map"]]


@pytest.mark.parametrize("node", [{"HostName": "derp.lan", "IPv4": "192.168.1.3", "IPv6": "fd00::3"},
                                  {"HostName": "anything", "IPv4": "10.0.0.3", "IPv6": "none"},
                                  {"HostName": "derp.lan", "IPv4": "192.168.1.3"},       # IPv6 from DNS: derp.lan
                                  {"HostName": "derp.lan", "STUNOnly": True}])
def test_relay_addresses_follow_the_derp_node_fields(node):
    assert tailscale_vpn(cli(HEADSCALE, derp_map(node)), NAMES, which).local


@pytest.mark.parametrize("prefs, derp, reason", [
    ({"ControlURL": ""}, derp_map({"HostName": "derp.lan"}), "tailscale_control"),       # empty: Tailscale's default
    ({"ControlURL": "https://controlplane.tailscale.com"}, {}, "tailscale_control"),
    ({"ControlURL": "https://login.tailscale.com"}, {}, "tailscale_control"),
    ({"ControlURL": "https://headscale.example.com"}, {}, "control_outside"),            # does not resolve here
    ({"ControlURL": "https://203.0.113.5"}, {}, "control_outside"),
    (HEADSCALE, derp_map({"HostName": "derp.lan"}, {"HostName": "derp1.tailscale.com"}), "relay_outside"),
    (HEADSCALE, derp_map({"HostName": "derp.lan", "IPv4": "199.38.181.104", "IPv6": "none"}), "relay_outside"),
    (HEADSCALE, derp_map({"HostName": "derp.mixed"}), "relay_outside"),
    (HEADSCALE, {"omitDefaultRegions": True}, "unreadable"),                             # no Regions: not a DERP map
    (HEADSCALE, "not json", "unreadable"),
    ("[]", derp_map(), "unreadable"),
    (HEADSCALE, subprocess.CalledProcessError(1, "tailscale"), "unreadable"),
    (subprocess.TimeoutExpired("tailscale", 5), derp_map(), "unreadable"),
])
def test_anything_else_is_not_the_own_vpn(prefs, derp, reason):
    check = tailscale_vpn(cli(prefs, derp), NAMES, which)
    assert not check.local and check.reason == reason, check
    assert check.why()


def test_without_the_tailscale_command_the_vpn_is_not_local():
    assert tailscale_vpn(cli({}, {}), NAMES, lambda name: None) == VpnCheck(False, "no_cli")


def test_the_vpn_check_runs_once_per_run(monkeypatch):
    calls = []
    monkeypatch.setattr(locality.shutil, "which", which)
    monkeypatch.setattr(locality, "_run_cli", cli(HEADSCALE, derp_map({"HostName": "192.168.1.3"}), calls))
    monkeypatch.setattr(locality, "_system_lookup", lambda host: {"headscale.lan": ["192.168.1.2"]}[host])
    assert check_host("100.64.1.1").reason == "own_vpn"
    assert check_host("100.64.1.2").reason == "own_vpn"
    assert len(calls) == 2, "prefs and derp-map once"
    locality.clear_cache()
    check_host("100.64.1.1")
    assert len(calls) == 4


# --- (d) the person's opt-outs --------------------------------------------------------------------------------------

def test_the_person_can_count_a_host_a_range_and_a_vpn_as_local():
    proxy = table(**{"myhome.duckdns.org": ["198.51.100.7"]})
    assert not check_host("myhome.duckdns.org", resolver=proxy).local
    settings = {"offline_local_hosts": ["MyHome.DuckDNS.org."], "offline_local_ranges": ["198.51.100.0/24", "junk"],
                "offline_local_vpns": ["tailscale", "unknown-vpn"]}
    opt = OptOuts.of(settings)
    assert opt.hosts == {"myhome.duckdns.org"} and opt.vpns == {"tailscale"} and len(opt.ranges) == 1
    verdict = check_host("myhome.duckdns.org", settings, resolver=proxy)
    assert verdict.local and verdict.reason == "opt_out_host" and verdict.address == "198.51.100.7"
    assert check_host("198.51.100.99", {"offline_local_ranges": ["198.51.100.0/24"]}).reason == "opt_out_range"
    assert check_host("100.64.3.4", {"offline_local_vpns": ["tailscale"]}, vpn=NOT_OWN).reason == "opt_out_vpn"
    assert not check_host("198.51.101.1", settings).local


def test_opt_outs_come_from_any_settings_object_and_bad_entries_are_left_out():
    class Settings:
        def get(self, key, default=None):
            return {"offline_local_ranges": "10.99.0.0/16, nonsense", "offline_local_hosts": None}.get(key, default)
    opt = OptOuts.of(Settings())
    assert [str(r) for r in opt.ranges] == ["10.99.0.0/16"] and not opt.hosts
    assert not OptOuts.of(None) and not OptOuts.of({"offline_local_hosts": 5})


# --- (e) the resolver, its cache, the GUI thread, the checked address ------------------------------------------------

def test_the_system_resolver_is_asked_once_per_run(monkeypatch):
    asked = []
    monkeypatch.setattr(locality, "_system_lookup", lambda host: asked.append(host) or ["192.168.1.9", "192.168.1.9"])
    assert check_host("nas.lan").addresses == ("192.168.1.9",)
    assert check_host("NAS.lan.").local and asked == ["nas.lan"]
    assert locality.cached("nas.lan") == ("192.168.1.9",)


def test_a_slow_resolver_runs_into_the_timeout(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(locality, "_system_lookup", lambda host: release.wait(5) and ["192.168.1.9"])
    monkeypatch.setattr(locality, "RESOLVE_TIMEOUT", 0.2)
    start = time.monotonic()
    with pytest.raises(locality.LookupFailed, match="within 0.2 seconds"):
        locality.resolve("slow.lan", timeout=0.2)
    assert time.monotonic() - start < 2
    release.set()


def test_a_failed_lookup_is_asked_again_only_after_a_while(monkeypatch):
    asked = []

    def lookup(host):
        asked.append(host)
        raise OSError("Name or service not known")
    monkeypatch.setattr(locality, "_system_lookup", lookup)
    assert check_host("gone.lan").reason == "unresolved"
    assert check_host("gone.lan").reason == "unresolved" and asked == ["gone.lan"]
    monkeypatch.setattr(locality, "FAILURE_TTL", 0.0)
    check_host("gone.lan")
    assert asked == ["gone.lan", "gone.lan"]


def test_no_lookup_and_no_vpn_command_on_the_gui_thread(monkeypatch):
    locality.mark_ui_thread()
    monkeypatch.setattr(locality.shutil, "which", lambda name: pytest.fail("tailscale on the GUI thread"))
    verdict = check_host("nas.lan")
    assert not verdict.local and verdict.reason == "not_checked" and "not checked yet" in verdict.why()
    assert check_host("100.64.0.1").reason == "vpn"
    assert check_host("192.168.1.1").local, "addresses need no lookup"
    assert needs_lookup("http://nas.lan:8000") and label("http://nas.lan:8000") is None
    # checked in a worker first: then the GUI thread reads the cache
    monkeypatch.setattr(locality, "_system_lookup", lambda host: ["192.168.1.20"])
    worker = threading.Thread(target=check_host, args=("nas.lan",))
    worker.start()
    worker.join()
    assert check_host("nas.lan").local and label("http://nas.lan:8000") is True and not needs_lookup("nas.lan")


def test_labels_need_no_lookup():
    assert label("http://localhost:11434/v1") is True and label("http://10.0.0.2/v1") is True
    assert label("https://api.example.com/v1") is None and label("http://8.8.8.8/v1") is False
    assert label(None) is False


def test_the_connection_goes_to_the_checked_address(monkeypatch):
    answers = iter([["192.168.1.20"], ["203.0.113.66"]])   # a rebinding answer would come second
    monkeypatch.setattr(locality, "_system_lookup", lambda host: next(answers))
    assert check_host("nas.lan").local
    assert connect_address("nas.lan") == "192.168.1.20", "the run's checked address, not a new answer"
    with pytest.raises(LocalityRefused) as info:
        connect_address("8.8.8.8", what="Ollama")
    assert isinstance(info.value, PermissionError) and info.value.locality.reason == "public"
    assert [s.id for s in info.value.steps] == ["settings.local_network"]


def test_the_refusal_names_the_host_why_and_the_opt_out():
    text = Locality("myhome.duckdns.org", False, "public", ("198.51.100.7",), ("198.51.100.7",)).refusal(
        "The server address of Ollama")
    assert text.startswith("Offline mode is on, and The server address of Ollama (myhome.duckdns.org) is not on")
    assert "resolves to 198.51.100.7 on the internet" in text
    assert "only the person can count it as local" not in text and "the person can count it as local" in text
    assert "an AI cannot" in text
    assert Locality("x", False, "public").steps == ("settings.local_network",)
    assert "count it as local" not in Locality("gone.lan", False, "unresolved").refusal()
    assert Locality("gone.lan", False, "unresolved").steps == ()


def test_offline_mode_is_the_helper_here_too():
    assert offline_mode({"offline_mode": True}) and not offline_mode({})
