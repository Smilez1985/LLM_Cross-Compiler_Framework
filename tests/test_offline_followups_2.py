"""Offline-mode follow-ups: the sign-in check's refusal by the locality rule, Tailscale's RouteAll and exit node
preferences, the GUI thread before the VPN check is known, and a crawl when offline mode is switched off.

The Tailscale preferences are ipn.Prefs as `tailscale debug prefs` prints them (the Go field names are the JSON keys:
RouteAll, ExitNodeID, ExitNodeIP, ExitNodeAllowLANAccess)."""

import json
import threading

import pytest

from orchestrator.utils import locality
from orchestrator.utils.locality import LocalityRefused, check_host, label
from test_offline_followups import STATUS, Settings, crawler_for, page

DERP = {"Regions": {"900": {"Nodes": [{"HostName": "derp.lan", "IPv4": "192.168.1.3", "IPv6": "none"}]}}}
NAMES = {"headscale.lan": ["192.168.1.2"], "derp.lan": ["192.168.1.3"]}
OWN = {"ControlURL": "https://headscale.lan:8080"}


def vpn(prefs):
    def run(argv):
        key = argv[2] if argv[1] == "debug" else "status"
        return json.dumps({"prefs": prefs, "derp-map": DERP, "status": STATUS}[key])
    return locality.tailscale_vpn(run, lambda host: NAMES[host], lambda name: "/usr/bin/tailscale")


@pytest.fixture
def names(monkeypatch):
    table = {}

    def lookup(host):
        if host not in table:
            raise OSError(f"{host}: unknown")
        return table[host]
    monkeypatch.setattr(locality, "_system_lookup", lookup)
    return table


# --- the sign-in check -------------------------------------------------------------------------------------------------

def test_a_locality_refusal_during_the_sign_in_check_is_a_plain_result(monkeypatch, names):
    from orchestrator.llm.auth import signin
    from orchestrator.llm.errors import NotConfigured
    names["box.lan"] = ["192.168.1.20"]
    refused = LocalityRefused(check_host("203.0.113.70", vpn=lambda: locality.VpnCheck(False, "no_cli")),
                              "the AI server")

    def validate(*a, **k):
        raise refused
    monkeypatch.setattr(signin, "validate_credential", validate)
    service = signin.SignIn(Settings(offline_mode=True, ai_provider="ollama"), None)
    preset = service.catalog.get("ollama")
    result = service.check(preset, preset.method(preset.auth[0]), "http://box.lan:11434/v1")
    assert not result.ok and isinstance(result.error, NotConfigured)
    assert str(result.error) == str(refused) and "Traceback" not in str(result.error)


# --- RouteAll and the exit node ----------------------------------------------------------------------------------------

def test_subnet_routes_count_only_when_this_node_accepts_them():
    without = vpn(dict(OWN))
    assert without.routes == ()
    assert check_host("10.20.3.4", vpn=lambda: without).reason == "own_network", "the route is not in use here"
    accepting = vpn(dict(OWN, RouteAll=True))
    assert [str(r) for r in accepting.routes] == ["10.20.0.0/16"]
    assert check_host("10.20.3.4", vpn=lambda: accepting).reason == "own_vpn"


@pytest.mark.parametrize("exit_prefs", [{"ExitNodeID": "nB3x2YqCNTRL"}, {"ExitNodeIP": "100.64.0.3"}],
                         ids=["by-id", "by-ip"])
def test_an_exit_node_without_lan_access_sends_private_addresses_through_the_vpn_rule(exit_prefs):
    cloud = vpn(dict(exit_prefs, ControlURL=""))   # Tailscale's own control server: not the person's own VPN
    assert cloud.lan_via_exit
    routed = check_host("192.168.1.20", vpn=lambda: cloud)
    assert not routed.local and routed.reason == "vpn"
    own = vpn(dict(OWN, **exit_prefs))
    assert check_host("192.168.1.20", vpn=lambda: own).reason == "own_vpn"
    lan = vpn(dict(exit_prefs, ControlURL="", ExitNodeAllowLANAccess=True))
    assert not lan.lan_via_exit and check_host("192.168.1.20", vpn=lambda: lan).reason == "own_network"


def test_no_exit_node_keeps_the_local_network_local():
    plain = vpn({"ControlURL": "", "ExitNodeID": "", "ExitNodeIP": ""})
    assert not plain.lan_via_exit and check_host("192.168.1.20", vpn=lambda: plain).reason == "own_network"


# --- the GUI thread before the VPN check ran --------------------------------------------------------------------------

def test_on_the_gui_thread_a_private_address_waits_for_the_vpn_check(monkeypatch):
    locality.clear_cache()
    monkeypatch.setattr(locality, "_ui_thread", [threading.get_ident()])
    monkeypatch.setattr(locality.shutil, "which", lambda name: "/usr/bin/tailscale")
    monkeypatch.setattr(locality, "_run_cli", lambda argv: pytest.fail("tailscale ran on the GUI thread"))
    verdict = check_host("10.20.3.4")
    assert not verdict.local and verdict.reason == "not_checked", "never own_network before the check"
    assert label("http://10.20.3.4:8080/v1") is None, "the GUI shows 'not checked yet'"
    assert check_host("127.0.0.1").local, "this computer needs no VPN check"
    # a worker runs the check; then the GUI thread reads the cached answer
    with locality._lock:
        locality._vpn_cache["tailscale"] = (locality.time.monotonic(), vpn(dict(OWN, RouteAll=True)))
    assert check_host("10.20.3.4").reason == "own_vpn" and label("http://10.20.3.4:8080/v1") is True
    locality.clear_cache()


def test_on_the_gui_thread_without_tailscale_a_private_address_is_the_own_network(monkeypatch):
    locality.clear_cache()
    monkeypatch.setattr(locality, "_ui_thread", [threading.get_ident()])
    monkeypatch.setattr(locality.shutil, "which", lambda name: None)
    assert check_host("192.168.1.20").reason == "own_network"
    locality.clear_cache()


# --- a crawl when offline mode is switched off ------------------------------------------------------------------------

def test_the_persons_offline_crawl_drops_the_own_network_when_offline_mode_is_switched_off(names, monkeypatch):
    names.update({"docs.lan": ["192.168.1.20"]})
    settings = Settings(offline_mode=True)
    pages = {"http://docs.lan/": page(html="<html lang='en'>Start.</html>")}
    crawler, web = crawler_for(settings, "gui", pages, monkeypatch)
    assert crawler.own_network
    assert crawler._address_refusal("http://docs.lan/") is None
    settings.values["offline_mode"] = False
    refusal = crawler._address_refusal("http://docs.lan/guide")
    assert refusal, "online the person's crawl reads public sites only again"
    assert not crawler.own_network
    assert type(crawler.session.get_adapter("http://docs.lan/")).__name__ == "PublicOnlyAdapter"
