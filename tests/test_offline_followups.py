"""Offline mode follow-ups (DECISIONS round 26 Q3, Q4, Q5; round 24 L-NET "every reach-out on the rule", items d-i).

- Q3: the person's opt-outs count only for offline mode's reach (locality.check_reach); the privacy filter and the
  provider's local/cloud label ask where the server stands (locality.check_location) and never count them.
- Q4: a name with local and public addresses counts as local, and every connection (the pinned provider client, the
  local-only requests adapter, git's curloptResolve pin) goes only to its checked local addresses, never a public one.
- (h) with the own Headscale an address of the VPN counts only for a device of the tailnet; a private address inside
  a peer's subnet route is judged like a VPN address.
- (i) a lookup lives LOOKUP_TTL seconds and is dropped after a failed connection.
- (g) an international mirror host is refused in offline mode until it is written in its xn-- form.
- (f) the pin-bump check (upstream_tag_commit) reads offline mode and reaches a mirror like fetch_source does.

Every public stand-in is a documentation address (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24, 2001:db8::/32) and
every name an example name.
"""

import inspect
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from orchestrator.llm.catalog import is_local_url, load_catalog
from orchestrator.utils import locality
from orchestrator.utils.locality import (
    LocalityRefused,
    VpnCheck,
    check_host,
    check_location,
    check_reach,
    connect_addresses,
)


class Settings:
    def __init__(self, **values):
        self.values = {"ai_security_level": "STRICT", **values}
        self.config_dir = None

    def get(self, key, default=None):
        return self.values.get(key, default)


@pytest.fixture
def names(monkeypatch):
    """The resolver's answers in these tests (anything else does not resolve)."""
    table = {}

    def lookup(host):
        if host not in table:
            raise OSError(f"{host}: unknown")
        return table[host]
    monkeypatch.setattr(locality, "_system_lookup", lookup)
    return table


@pytest.fixture
def server():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.headers.get("Host"))
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1], seen
    httpd.shutdown()
    httpd.server_close()


def closed_port() -> int:
    """A port on 127.0.0.1 nobody listens on (a connection to it is refused)."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def spy_backend(http):
    """The addresses the pinned client's own network backend is asked to connect to (after the locality check)."""
    backend = http._transport._pool._network_backend
    inner, asked = backend._inner, []

    class Spy:
        def connect_tcp(self, host, port, **kwargs):
            asked.append(host)
            return inner.connect_tcp(host, port, **kwargs)

        def sleep(self, seconds):
            inner.sleep(seconds)
    backend._inner = Spy()
    return asked


def client(tmp_path, **settings):
    from orchestrator.llm import LLMClient
    return LLMClient(Settings(**settings), None, state_dir=tmp_path / "state")


NOT_OWN = lambda: VpnCheck(False, "tailscale_control", "controlplane.tailscale.com")  # noqa: E731


# --- Q3: the opt-outs count for offline mode's reach only ---------------------------------------------------------

def test_one_api_for_two_questions(names):
    """check_reach (may offline mode reach it: the opt-outs count) and check_location (where does it stand: they do
    not); the catalog's label asks the second, offline_refusal the first."""
    names["myhome.example.org"] = ["198.51.100.7"]
    opted = Settings(offline_mode=True, offline_local_hosts=["myhome.example.org"])
    url = "https://myhome.example.org/v1"
    assert check_reach(url, opted).local and check_reach(url, opted).reason == "opt_out_host"
    assert not check_location(url).local and check_location(url).reason == "public"
    custom = load_catalog().get("custom")
    assert custom.offline_refusal(url, opted) is None, "offline mode may reach the opted-in host"
    assert custom.is_local(url) is False and is_local_url(url) is False, "but it keeps its label"
    # the label has no opt-out argument any more: nobody can pass the person's settings to it by mistake
    assert list(inspect.signature(custom.is_local).parameters) == ["base_url"]
    assert list(inspect.signature(is_local_url).parameters) == ["url"]


def test_an_opted_in_public_host_keeps_the_privacy_filter_online_and_offline(tmp_path, names):
    names["myhome.example.org"] = ["198.51.100.7"]
    url = "https://myhome.example.org/v1"
    for offline in (True, False):
        llm = client(tmp_path, ai_provider="custom", ai_model="m", ai_base_url=url, offline_mode=offline,
                     offline_local_hosts=["myhome.example.org"], offline_local_ranges=["198.51.100.0/24"])
        target = llm.resolve()
        assert target.local is False, f"offline={offline}: the privacy filter stays on"
        assert llm.describe()["privacy"] == "STRICT"


def test_the_opt_out_explanations_say_the_privacy_filter_stays_on():
    from orchestrator.Core.local_network import explain
    for kind, value in (("host", "myhome.example.org"), ("range", "198.51.100.0/24"), ("vpn", "tailscale")):
        assert "privacy filter stays on" in explain(kind, value, "en")
        assert "Datenschutzfilter bleibt" in explain(kind, value, "de")
    from orchestrator.utils.localization import ui_text
    assert "privacy filter stays on" in ui_text("net.intro", "en")


# --- Q4: a name with local and public addresses ------------------------------------------------------------------

def test_a_name_with_local_and_public_addresses_is_local_by_its_local_ones(names):
    names["nas.lan"] = ["2001:db8::20", "192.168.1.20", "203.0.113.9", "fd00::20"]
    verdict = check_host("nas.lan")
    assert verdict.local and verdict.reason == "own_network"
    assert verdict.local_addresses == ("192.168.1.20", "fd00::20"), "the resolver's order, the local ones only"
    assert verdict.outside == ("2001:db8::20", "203.0.113.9") and verdict.address == "192.168.1.20"
    assert "never to 2001:db8::20, 203.0.113.9" in verdict.why()
    assert "nie mit 2001:db8::20, 203.0.113.9" in verdict.why("de")
    assert connect_addresses("nas.lan") == ("192.168.1.20", "fd00::20"), "a public address is never used"
    names["cloud.example.com"] = ["203.0.113.9", "2001:db8::9"]
    locality.clear_cache()
    assert check_host("cloud.example.com").reason == "public", "only public addresses: not local"
    with pytest.raises(LocalityRefused):
        connect_addresses("cloud.example.com")


def test_a_vpn_address_in_a_mixed_answer_follows_the_vpn_rule(names):
    own = lambda: VpnCheck(True, "own_headscale", "headscale.lan", members=frozenset({"100.64.0.9"}))  # noqa: E731
    answer = ["100.64.0.9", "192.168.1.20", "203.0.113.9"]
    verdict = check_host("gpu.lan", resolver=lambda name: answer, vpn=NOT_OWN)
    assert verdict.local and verdict.local_addresses == ("192.168.1.20",), "no proof: the VPN address is not used"
    assert verdict.outside == ("203.0.113.9", "100.64.0.9")
    verdict = check_host("gpu.lan", resolver=lambda name: answer, vpn=own)
    assert verdict.local_addresses == ("100.64.0.9", "192.168.1.20") and verdict.reason == "own_vpn"
    only_vpn_and_public = check_host("x.lan", resolver=lambda name: ["100.64.0.9", "203.0.113.9"], vpn=NOT_OWN)
    assert not only_vpn_and_public.local and only_vpn_and_public.reason == "public"


def test_the_pinned_provider_client_connects_only_to_the_local_address(names, server):
    from orchestrator.llm.client import offline_http_client
    port, seen = server
    names["box.lan"] = ["203.0.113.66", "127.0.0.1"]     # public first: it must never be tried
    http = offline_http_client(Settings(offline_mode=True))
    asked = spy_backend(http)
    assert http.get(f"http://box.lan:{port}/v1/models", timeout=10).status_code == 200
    assert asked == ["127.0.0.1"] and seen == [f"box.lan:{port}"]


def test_the_local_only_session_connects_only_to_the_local_address(names, server, monkeypatch):
    from urllib3.connection import HTTPConnection

    from orchestrator.security.http import local_only_session
    port, seen = server
    names["models.lan"] = ["203.0.113.66", "127.0.0.1"]
    asked = []
    real = HTTPConnection._new_conn

    def spy(conn):
        asked.append(conn._dns_host)
        return real(conn)
    monkeypatch.setattr(HTTPConnection, "_new_conn", spy)
    session = local_only_session(Settings(offline_mode=True))
    assert session.get(f"http://models.lan:{port}/model.gguf", timeout=10).status_code == 200
    assert asked == ["127.0.0.1"] and seen == [f"models.lan:{port}"]


def test_the_git_pin_names_only_the_local_addresses(names):
    from orchestrator.runtime.build.source import offline_reach
    names["mirror.lan"] = ["2001:db8::9", "192.168.1.5", "203.0.113.5", "fd00::5"]
    assert offline_reach("https://mirror.lan/llama.cpp.git", Settings(offline_mode=True)) == \
        "mirror.lan:443:192.168.1.5,[fd00::5]"


def test_online_a_server_labelled_local_by_its_address_is_reached_only_there(tmp_path, names, server):
    """A custom server whose name has local and public addresses is labelled local (no privacy filter), so online too
    its connection goes only to the checked local addresses, never the public one; Ollama (labelled local by its
    preset) and a cloud provider keep the SDK's own client online."""
    port, seen = server
    names["box.lan"] = ["203.0.113.66", "127.0.0.1"]
    llm = client(tmp_path, ai_provider="custom", ai_model="m", ai_base_url=f"http://box.lan:{port}/v1")
    target = llm.resolve()
    assert target.local is True
    http = llm._http_client_for(target)
    assert http is not None and type(http._transport._pool._network_backend).__name__ == "PinnedBackend"
    asked = spy_backend(http)
    assert http.get(f"http://box.lan:{port}/v1/models", timeout=10).status_code == 200 and asked == ["127.0.0.1"]
    names["gpu.lan"] = ["192.168.1.40"]
    ollama = client(tmp_path, ai_provider="ollama", ai_base_url="http://gpu.lan:11434/v1")
    assert ollama._http_client_for(ollama.resolve()) is None, "Ollama online: the SDK default, as before"


def test_online_a_name_that_turned_public_is_refused_without_naming_an_opt_out(names, server):
    from orchestrator.llm.client import offline_http_client
    port, _seen = server
    names["box.lan"] = ["203.0.113.66"]
    http = offline_http_client(None, offline=False)
    with pytest.raises(LocalityRefused) as info:
        http.get(f"http://box.lan:{port}/v1/models", timeout=2)
    text = str(info.value)
    assert "no longer counts as local" in text and "privacy filter" in text and "Offline mode is on" not in text
    assert not info.value.steps and info.value.offline is False


# --- (h) the tailnet's devices and subnet routes -----------------------------------------------------------------

# RouteAll: this node accepts its peers' subnet routes (ipn.Prefs; without it the routes are not in use here).
PREFS = {"ControlURL": "https://headscale.lan:8080", "RouteAll": True}
DERP = {"Regions": {"900": {"Nodes": [{"HostName": "derp.lan", "IPv4": "192.168.1.3", "IPv6": "none"}]}}}
STATUS = {"BackendState": "Running", "TailscaleIPs": ["100.64.0.1", "fd7a:115c:a1e0::1"],
          "Self": {"TailscaleIPs": ["100.64.0.1", "fd7a:115c:a1e0::1"]},
          "Peer": {"nodekey:a": {"TailscaleIPs": ["100.64.0.2"], "AllowedIPs": ["100.64.0.2/32", "10.20.0.0/16"],
                                 "PrimaryRoutes": ["10.20.0.0/16"]},
                   "nodekey:b": {"TailscaleIPs": ["100.64.0.3"],
                                 "AllowedIPs": ["100.64.0.3/32", "0.0.0.0/0", "::/0"]}}}


def tailscale(prefs=PREFS, status=STATUS, derp=DERP, calls=None):
    def run(argv):
        if calls is not None:
            calls.append(list(argv[1:]))
        key = argv[2] if argv[1] == "debug" else "status"
        return json.dumps({"prefs": prefs, "derp-map": derp, "status": status}[key])
    return run


def which(name):
    return "/usr/bin/tailscale" if name == "tailscale" else None


NAMES = {"headscale.lan": ["192.168.1.2"]}


def vpn_check(**kw):
    return locality.tailscale_vpn(tailscale(**kw), lambda host: NAMES[host], which)


def test_the_tailnet_is_read_with_the_field_names_of_ipnstate_status():
    calls = []
    check = vpn_check(calls=calls)
    assert check.local and check.reason == "own_headscale"
    assert calls == [["debug", "prefs"], ["status", "--json"], ["debug", "derp-map"]]
    assert check.members == {"100.64.0.1", "fd7a:115c:a1e0::1", "100.64.0.2", "100.64.0.3"}
    # a peer's own address and an exit node's default routes are no subnet routes
    assert [str(r) for r in check.routes] == ["10.20.0.0/16"]


def test_with_the_own_headscale_only_a_device_of_the_tailnet_counts_as_local():
    own = vpn_check()
    member = check_host("100.64.0.2", vpn=lambda: own)
    assert member.local and member.reason == "own_vpn"
    stranger = check_host("100.64.9.9", vpn=lambda: own)   # e.g. a carrier-grade NAT address of the provider
    assert not stranger.local and stranger.reason == "vpn"
    assert "belongs to no device in your tailnet" in stranger.why()
    assert "keinem Gerät deines Tailnets" in stranger.why("de")
    assert stranger.steps == ("settings.local_network",)


def test_a_private_address_behind_a_peers_subnet_route_is_judged_like_a_vpn_address():
    own = vpn_check()
    assert check_host("10.20.3.4", vpn=lambda: own).reason == "own_vpn", "own Headscale, own relays: local"
    cloud = vpn_check(prefs={"ControlURL": "", "RouteAll": True})   # Tailscale's own control server
    assert cloud.reason == "tailscale_control" and [str(r) for r in cloud.routes] == ["10.20.0.0/16"]
    routed = check_host("10.20.3.4", vpn=lambda: cloud)
    assert not routed.local and routed.reason == "vpn" and "Tailscale's own servers" in routed.why()
    assert check_host("192.168.1.20", vpn=lambda: cloud).reason == "own_network", "outside the route: as before"
    assert check_host("10.20.3.4", {"offline_local_vpns": ["tailscale"]}, vpn=lambda: cloud).reason == "opt_out_vpn"
    assert check_host("10.20.3.4", {"offline_local_ranges": ["10.20.0.0/16"]},
                      vpn=lambda: cloud).reason == "opt_out_range"


def test_a_vpn_that_is_not_connected_or_cannot_be_read_is_not_local():
    assert vpn_check(status={"BackendState": "Stopped", "Peer": {}}).reason == "not_running"
    assert vpn_check(status=[]).reason == "unreadable"
    assert vpn_check(status={"BackendState": "Running", "Peer": "x"}).reason == "unreadable"


# --- (i) a lookup lives a short time and a failed connection drops it --------------------------------------------

def test_a_lookup_is_kept_five_minutes(names, monkeypatch):
    assert locality.LOOKUP_TTL == 300.0
    names["nas.lan"] = ["192.168.1.20"]
    assert check_host("nas.lan").local
    names["nas.lan"] = ["10.0.0.20"]              # the laptop moved to another network
    assert check_host("nas.lan").addresses == ("192.168.1.20",), "within the time: the kept answer"
    monkeypatch.setattr(locality, "LOOKUP_TTL", 0.0)
    assert check_host("nas.lan").addresses == ("10.0.0.20",), "after it: a fresh answer"


def test_a_failed_connection_drops_the_lookup(names):
    from orchestrator.llm.client import offline_http_client
    names["box.lan"] = ["127.0.0.1"]
    http = offline_http_client(Settings(offline_mode=True))
    with pytest.raises(Exception):
        http.get(f"http://box.lan:{closed_port()}/v1/models", timeout=5)
    assert locality.cached("box.lan") is None, "the next connection asks the resolver again"


def test_a_failed_download_connection_drops_the_lookup(names):
    import requests

    from orchestrator.security.http import local_only_session
    names["models.lan"] = ["127.0.0.1"]
    with pytest.raises(requests.exceptions.ConnectionError):
        local_only_session(Settings(offline_mode=True)).get(f"http://models.lan:{closed_port()}/m.gguf", timeout=5)
    assert locality.cached("models.lan") is None


def test_forget_drops_the_vpn_check_for_a_vpn_address(names, monkeypatch):
    monkeypatch.setattr(locality.shutil, "which", which)
    monkeypatch.setattr(locality, "_run_cli", tailscale())
    monkeypatch.setattr(locality, "_system_lookup", lambda host: NAMES[host])
    assert check_host("100.64.0.2").reason == "own_vpn"
    assert locality._vpn_cache
    locality.forget("192.168.1.99")
    assert locality._vpn_cache, "an address outside the VPN keeps the VPN check"
    locality.forget("100.64.0.2")
    assert not locality._vpn_cache, "a VPN address drops it: the tailnet may have changed"


# --- (g) an international mirror host --------------------------------------------------------------------------------

def test_an_international_mirror_host_is_refused_with_its_xn_form(names):
    from orchestrator.runtime.build.source import SourceError, offline_reach
    with pytest.raises(SourceError) as info:
        offline_reach("https://bücher.example/llama.cpp.git", Settings(offline_mode=True))
    text = str(info.value)
    assert "bücher.example" in text and "xn--bcher-kva.example" in text and "xn--" in text
    assert offline_reach("https://bücher.example/llama.cpp.git", Settings()) is None, "online: as before"
    names["xn--bcher-kva.example"] = ["192.168.1.5"]
    assert offline_reach("https://xn--bcher-kva.example/llama.cpp.git", Settings(offline_mode=True)) == \
        "xn--bcher-kva.example:443:192.168.1.5"


# --- (f) the pin-bump check reads offline mode -------------------------------------------------------------------

def test_the_pin_bump_check_reaches_only_a_mirror_in_the_own_network(names):
    from orchestrator.runtime.build.source import SourceError, check_pin_upstream, upstream_tag_commit
    names["github.example"] = ["203.0.113.4"]
    names["mirror.lan"] = ["192.168.1.5"]
    calls = []

    def git(args, **kwargs):
        calls.append((list(args), kwargs.get("resolve"), kwargs.get("offline")))
        return SimpleNamespace(stdout=f"{'c' * 40}\trefs/tags/b42\n", returncode=0)
    offline = Settings(offline_mode=True)
    with pytest.raises(SourceError, match="github.example resolves to 203.0.113.4 on the internet"):
        upstream_tag_commit("b42", repo="https://github.example/ggml-org/llama.cpp", git=git, settings=offline)
    assert calls == [], "nothing reached the public repository"
    assert upstream_tag_commit("b42", repo="https://mirror.lan/llama.cpp.git", git=git, settings=offline) == "c" * 40
    assert calls[-1][1] == "mirror.lan:443:192.168.1.5"
    assert upstream_tag_commit("b42", repo="https://192.168.1.5/llama.cpp.git", git=git, settings=offline)
    assert calls[-1][1:] == (None, True), "an address: no pin, but no proxy and no redirect either"
    upstream_tag_commit("b42", repo="https://github.example/ggml-org/llama.cpp", git=git, settings=Settings())
    assert calls[-1][1:] == (None, None), "online: as before"
    source = SimpleNamespace(tag="b42", commit="c" * 40, repo="https://github.example/ggml-org/llama.cpp")
    with pytest.raises(SourceError):
        check_pin_upstream(source, git=git, settings=offline)
    assert check_pin_upstream(source, repo="https://mirror.lan/llama.cpp.git", git=git, settings=offline) == "c" * 40


# --- Q5: a crawl the person starts reads the own network in offline mode -------------------------------------------

EN = " This guide explains how to install the software development kit on your single board computer. "


def page(status=200, html="", location=None, content_type="text/html"):
    from requests.structures import CaseInsensitiveDict
    headers = CaseInsensitiveDict({"Content-Type": content_type})
    if location:
        headers["Location"] = location
    return SimpleNamespace(status_code=status, headers=headers, text=html, content=html.encode(),
                           is_redirect=status in (301, 302, 303, 307, 308) and location is not None)


class FakeWeb:
    def __init__(self, pages):
        self.pages, self.urls, self.kwargs = pages, [], {}

    def __call__(self, prepared, **kwargs):
        self.urls.append(prepared.url)
        self.kwargs = kwargs
        return self.pages.get(prepared.url) or page(404)


def crawler_for(settings, actor="", pages=None, monkeypatch=None, **options):
    from orchestrator.Core.crawler_manager import CrawlerManager
    options.setdefault("request_delay", 0)
    options.setdefault("languages", [])
    crawler = CrawlerManager(SimpleNamespace(config=settings), options, actor=actor, _skip_robots_txt_for_tests=True)
    web = FakeWeb(pages or {})
    if monkeypatch is not None:
        monkeypatch.setattr(crawler.session, "send", web)
    return crawler, web


def test_the_persons_offline_crawl_reads_the_own_network_and_judges_every_hop(names, monkeypatch):
    names.update({"docs.lan": ["192.168.1.20"], "public.example": ["203.0.113.9"]})
    pages = {"http://docs.lan/": page(html='<a href="/guide">Guide</a><a href="/away">Away</a>'
                                           '<a href="/meta">Meta</a>' + EN),
             "http://docs.lan/guide": page(html="Guide." + EN),
             "http://docs.lan/away": page(302, location="http://public.example/"),
             "http://docs.lan/meta": page(302, location="http://169.254.169.254/latest/meta-data/")}
    crawler, web = crawler_for(Settings(offline_mode=True), "gui", pages, monkeypatch, max_depth=1)
    assert crawler.own_network
    docs = crawler.crawl("http://docs.lan/")
    assert [d.metadata["source"] for d in docs] == ["http://docs.lan/", "http://docs.lan/guide"]
    assert "http://public.example/" not in web.urls and not any("169.254" in u for u in web.urls)
    assert web.kwargs.get("proxies") == {}, "no proxy: it would resolve the name itself"
    assert type(crawler.session.get_adapter("http://docs.lan/")).__name__ == "LocalOnlyAdapter"


def test_a_redirect_at_the_start_to_a_public_host_is_judged_again(names, monkeypatch):
    names.update({"docs.lan": ["192.168.1.20"], "public.example": ["203.0.113.9"]})
    pages = {"http://docs.lan/": page(301, location="http://public.example/docs")}
    crawler, web = crawler_for(Settings(offline_mode=True), "cli", pages, monkeypatch)
    assert crawler.crawl("http://docs.lan/") == []
    assert web.urls == ["http://docs.lan/.well-known/tdmrep.json", "http://docs.lan/"]
    skipped = {s.url: s.reason for s in crawler.skipped}
    assert "on the internet" in skipped["http://public.example/docs"]


def test_an_ai_crawl_stays_public_only_and_reads_nothing_in_offline_mode(names, monkeypatch):
    names["docs.lan"] = ["192.168.1.20"]
    for actor in ("", "mcp", "mcp:token", "ditto", "framework"):
        crawler, web = crawler_for(Settings(offline_mode=True), actor, {"http://docs.lan/": page(html=EN)},
                                   monkeypatch)
        assert not crawler.own_network and crawler.crawl("http://docs.lan/") == [] and web.urls == []
        assert "Offline mode is on" in crawler.skipped[0].reason
        assert type(crawler.session.get_adapter("http://docs.lan/")).__name__ == "PublicOnlyAdapter"


def test_online_every_crawl_keeps_the_public_only_guard(names, monkeypatch):
    names["docs.lan"] = ["192.168.1.20"]
    for actor in ("gui", "cli", ""):
        crawler, web = crawler_for(Settings(), actor, {"http://docs.lan/": page(html=EN)}, monkeypatch)
        assert not crawler.own_network and crawler.crawl("http://docs.lan/") == [] and web.urls == []
        assert crawler.skipped[0].reason == "unsafe or invalid address (security check)"


@pytest.mark.parametrize("address", [
    "169.254.169.254", "169.254.1.1", "169.254.42.42", "::ffff:169.254.169.254", "::169.254.169.254",
    "64:ff9b::a9fe:a9fe", "64:ff9b:1::a9fe:a9fe", "2002:a9fe:a9fe::1", "fd00:ec2::254", "fd20:ce::254",
    "fe80::a9fe:a9fe", "fe80::a9fe:a9fe%eth0", "[fd00:ec2::254]", "fd00:a9fe:a9fe::1", "fd00:42::42",
    "100.100.100.200", "::ffff:100.100.100.200", "64:ff9b::6464:64c8"])
def test_a_crawl_never_reads_link_local_ipv4_or_a_metadata_service_in_any_spelling(address):
    assert locality.crawl_excluded(address)
    with pytest.raises(LocalityRefused) as info:
        locality.crawl_addresses(address.strip("[]").split("%")[0], {"offline_local_ranges": ["0.0.0.0/0", "::/0"]})
    assert info.value.locality.reason == "metadata" and not info.value.steps, "no opt-out lifts it"


def test_every_listed_metadata_address_is_excluded_in_its_mapped_spelling_too():
    """The list itself (one of them is a public address of a cloud platform, named here only through the list)."""
    for ip in locality.METADATA_ADDRESSES:
        assert locality.crawl_excluded(str(ip))
        if ip.version == 4:
            assert locality.crawl_excluded(f"::ffff:{ip}") and locality.crawl_excluded(f"64:ff9b::{ip}")


@pytest.mark.parametrize("address", ["192.168.1.20", "10.0.0.1", "fe80::1", "fd00::5", "127.0.0.1"])
def test_other_local_addresses_stay_readable_for_a_crawl(address):
    assert not locality.crawl_excluded(address)
    assert locality.crawl_addresses(address) == check_host(address).local_addresses


def test_a_metadata_name_or_a_name_with_one_metadata_address_is_refused(names):
    names.update({"metadata.google.internal": ["192.168.1.5"], "evil.lan": ["192.168.1.5", "169.254.169.254"],
                  "mixed.lan": ["192.168.1.5", "203.0.113.5"]})
    for host in ("metadata.google.internal", "METADATA.GOOG.", "metadata.tencentyun.com", "evil.lan"):
        with pytest.raises(LocalityRefused) as info:
            locality.crawl_addresses(host)
        assert info.value.locality.reason == "metadata" and "never reads" in str(info.value)
    assert locality.crawl_addresses("mixed.lan") == ("192.168.1.5",), "a public address is never used"


def test_the_persons_crawl_connects_only_to_the_checked_local_address(names, monkeypatch):
    """End to end: a real HTTP server on this computer, a name with a public and a local address, a proxy in the
    environment: the page arrives over the pinned connection to the local address, through no proxy."""
    from urllib3.connection import HTTPConnection

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = f"<html><body><p>{EN * 3}</p></body></html>".encode() if self.path == "/" else b""
            self.send_response(200 if self.path == "/" else 404)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    try:
        names["docs.lan"] = ["203.0.113.66", "127.0.0.1"]
        for var in ("HTTP_PROXY", "http_proxy", "ALL_PROXY"):
            monkeypatch.setenv(var, "http://203.0.113.1:9")
        asked = []
        real = HTTPConnection._new_conn

        def spy(conn):
            asked.append(conn._dns_host)
            return real(conn)
        monkeypatch.setattr(HTTPConnection, "_new_conn", spy)
        crawler, _web = crawler_for(Settings(offline_mode=True), "gui")
        docs = crawler.crawl(f"http://docs.lan:{port}/")
        assert [d.metadata["source"] for d in docs] == [f"http://docs.lan:{port}/"], crawler.failed
        assert asked and set(asked) == {"127.0.0.1"}
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_rag_manager_hands_the_persons_actor_to_the_crawler(monkeypatch):
    from orchestrator.Core import rag_manager as rag_module
    made = []

    class Crawler:
        def __init__(self, framework, options, **kwargs):
            made.append(kwargs)
            self.skipped, self.failed, self.stopped = [], [], False

        def iter_crawl(self, url, stop=None, progress=None):
            return iter(())

    rag = rag_module.RAGManager.__new__(rag_module.RAGManager)
    rag.framework = SimpleNamespace(config=Settings())
    rag.logger = SimpleNamespace(info=lambda *a: None, error=lambda *a: None)
    rag._connect = lambda: True
    rag.create_snapshot = lambda *a, **k: None
    monkeypatch.setattr(rag_module, "CrawlerManager", Crawler)
    rag.ingest_url("http://docs.lan/", actor="gui")
    rag.ingest_url("http://docs.lan/")
    assert made == [{"actor": "gui"}, {}], "an AI's crawl (crawl_jobs) passes no actor"


# --- (d) a Qdrant server in offline mode ------------------------------------------------------------------------------

def rag_for(settings):
    from orchestrator.Core import rag_manager as rag_module
    rag = rag_module.RAGManager.__new__(rag_module.RAGManager)
    rag.framework = SimpleNamespace(config=settings, secrets_manager=None)
    rag.config = settings
    return rag


def test_a_cloud_qdrant_is_refused_in_offline_mode(names):
    from orchestrator.Core.rag_manager import KnowledgeBaseError
    names["qdrant.example.com"] = ["203.0.113.30"]
    rag = rag_for(Settings(offline_mode=True))
    with pytest.raises(KnowledgeBaseError) as info:
        rag._offline_server_options("https://qdrant.example.com:6333")
    text = str(info.value)
    assert "the Qdrant server (setting qdrant_url)" in text and "203.0.113.30 on the internet" in text
    assert rag_for(Settings())._offline_server_options("https://qdrant.example.com:6333") == {}, "online: as before"


def test_a_qdrant_server_in_the_own_network_is_reached_only_at_its_checked_address(names, monkeypatch):
    """End to end with qdrant-client: its REST client gets the pinned transport (no proxy, only the local address of a
    name with a public and a local one) and no version request of its own."""
    from qdrant_client import QdrantClient

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"result": {"collections": []}, "status": "ok", "time": 0.0}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    try:
        names["qdrant.lan"] = ["203.0.113.66", "127.0.0.1"]
        monkeypatch.setenv("HTTP_PROXY", "http://203.0.113.1:9")
        options = rag_for(Settings(offline_mode=True))._offline_server_options(f"http://qdrant.lan:{port}")
        assert options["check_compatibility"] is False and options["trust_env"] is False
        backend = options["transport"]._pool._network_backend
        assert type(backend).__name__ == "PinnedBackend"
        inner, asked = backend._inner, []

        class Spy:
            def connect_tcp(self, host, port, **kwargs):
                asked.append(host)
                return inner.connect_tcp(host, port, **kwargs)

            def sleep(self, seconds):
                inner.sleep(seconds)
        backend._inner = Spy()
        client = QdrantClient(url=f"http://qdrant.lan:{port}", timeout=10, **options)
        assert client.get_collections().collections == []
        assert asked == ["127.0.0.1"]
        client.close()
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- (e) the sign-in check in offline mode ----------------------------------------------------------------------------

def test_the_sign_in_check_refuses_a_cloud_provider_before_any_socket_opens(tmp_path, names, monkeypatch):
    import httpx2

    from orchestrator.llm.auth.signin import SignIn
    opened = []
    monkeypatch.setattr(httpx2.Client, "send", lambda *a, **k: opened.append(a) or pytest.fail("a request was sent"))
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("a socket was opened"))
    service = SignIn(Settings(offline_mode=True, ai_provider="openai"), None)
    preset = service.catalog.get("openai")
    result = service.check(preset, preset.method("api_key"), key="sk-proj-0123456789abcdefghijkl")
    assert not result.ok and "is a cloud provider" in str(result.error) and opened == []
    names["llm.example.com"] = ["203.0.113.70"]
    custom = service.catalog.get("custom")
    result = service.check(custom, custom.method(custom.auth[0]), "https://llm.example.com/v1")
    assert not result.ok and "on the internet" in str(result.error)
    assert [s.id for s in result.error.steps] == ["settings.local_network"]


def test_the_sign_in_check_of_a_local_server_goes_only_to_its_checked_address(tmp_path, names, server):
    from orchestrator.llm.auth.signin import SignIn
    port, seen = server
    names["box.lan"] = ["203.0.113.66", "127.0.0.1"]
    service = SignIn(Settings(offline_mode=True, ai_provider="ollama"), None)
    preset = service.catalog.get("ollama")
    spec = preset.method(preset.auth[0])
    result = service.check(preset, spec, f"http://box.lan:{port}/v1")
    assert seen and set(seen) == {f"box.lan:{port}"}, "the request reached the local server"
    assert result.status == 200, result


# --- (f) LLAMA_CPP_REPO_OVERRIDE in offline mode ---------------------------------------------------------------------

def image_build(tmp_path, monkeypatch, repos, offline=True, images=()):
    from orchestrator.Core import builder as builder_module
    from orchestrator.Core.builder import BuildEngine, BuildProgress, BuildStatus
    from orchestrator.Core.config_manager import ConfigManager
    monkeypatch.setattr(builder_module.docker, "from_env", lambda: (_ for _ in ()).throw(RuntimeError("no docker")))
    config = ConfigManager(tmp_path / "configs")
    config.set("offline_mode", offline)
    engine = BuildEngine(config)
    monkeypatch.setattr(engine, "_get_conf", lambda key, default=None: repos if key == "source_repositories" else default)
    seen = {}

    def buildkit(tag, path, args, progress, labels=None, **kwargs):
        seen.update(args=dict(args), **kwargs)
    monkeypatch.setattr(engine, "_build_with_buildkit", buildkit)
    monkeypatch.setattr(engine, "_buildkit_available", lambda: True)
    monkeypatch.setattr(engine, "_buildx_driver", lambda: "docker", raising=False)   # the daemon's own buildx builder
    monkeypatch.setattr(engine, "docker_client", SimpleNamespace(images=SimpleNamespace(
        get=lambda tag: tag, list=lambda filters=None: seen.setdefault("listed", []).append(filters) or images)))
    record = engine._offline_repo_hosts

    def hosts(url, progress):   # what the override's check pinned, before the image step uses or drops it
        record(url, progress)
        seen["pinned"] = list(engine._repo_hosts.get(progress.build_id, []))
    monkeypatch.setattr(engine, "_offline_repo_hosts", hosts)
    dockerfile = tmp_path / "ctx" / "Dockerfile"
    dockerfile.parent.mkdir(exist_ok=True)
    dockerfile.write_text("FROM scratch\nARG LLAMA_CPP_REPO=x\n")
    progress = BuildProgress("b1", BuildStatus.QUEUED, "")
    build = SimpleNamespace(target_arch="rockchip", build_id="b1", build_args={})
    engine._build_docker_image(build, progress, dockerfile)
    return seen, progress


def test_the_repository_override_is_a_pinned_mirror_in_offline_mode(tmp_path, names, monkeypatch):
    """Offline mode judges the override as a mirror of the own network and pins only its checked local addresses
    (offline-followups). The image built earlier from the same settings, override included, is used first. Without
    one, round 36 (changed on purpose: before, offline mode never built the image) builds it when every source is
    local: here the Dockerfile needs only llama.cpp, and the mirror is in the own network, so the build goes to the
    mirror's checked local addresses with no proxy (Core/build_image.offline_plan). Online the image build is as
    before."""
    names["mirror.lan"] = ["192.168.1.5", "2001:db8::5", "fd00::5"]
    repos = {"llama.cpp": {"url": "https://mirror.lan/llama.cpp.git"}}
    built, _ = image_build(tmp_path, monkeypatch, repos)
    assert built["args"]["LLAMA_CPP_REPO"] == "https://mirror.lan/llama.cpp.git" and built["args"]["NO_PROXY"] == "*"
    assert built["hosts"] == ["mirror.lan:192.168.1.5", "mirror.lan=[fd00::5]"], "only the checked local addresses"
    image = SimpleNamespace(attrs={"Created": "2026"}, short_id="i1", tag=lambda *a: None)
    seen, _ = image_build(tmp_path, monkeypatch, repos, images=[image])
    assert seen["pinned"] == ["mirror.lan:192.168.1.5", "mirror.lan=[fd00::5]"], "only the checked local addresses"
    assert "args" not in seen, "the image built earlier from the same settings is used, nothing is built"
    label = [f for f in seen["listed"][0]["label"] if f.startswith("org.llm-framework.inputs=")]
    assert label, "the image of the same settings is looked for"
    other, _ = image_build(tmp_path, monkeypatch, {}, images=[image])
    # Changed on purpose (chain followups-3, Core/builder.MACHINE_ONLY_ARGS): the mirror serves the same pinned and
    # checked commit, so the fetch address is not part of what the image holds; the image built from the mirror is the
    # image of the upstream address and stays usable on a computer without that mirror.
    assert other["listed"][0]["label"] == seen["listed"][0]["label"], "the fetch address is not an input"
    online, _ = image_build(tmp_path, monkeypatch, repos, offline=False)
    assert online["args"]["LLAMA_CPP_REPO"] == "https://mirror.lan/llama.cpp.git"
    assert "hosts" not in online and "HTTPS_PROXY" not in online["args"], "online: as before"


def test_a_public_repository_override_stops_the_build_in_offline_mode(tmp_path, names, monkeypatch):
    names["mirror.example.com"] = ["203.0.113.40"]
    with pytest.raises(RuntimeError) as info:
        image_build(tmp_path, monkeypatch, {"llama.cpp": {"url": "https://mirror.example.com/llama.cpp.git"}})
    assert "LLAMA_CPP_REPO_OVERRIDE" in str(info.value) and "on the internet" in str(info.value)
    with pytest.raises(RuntimeError, match="xn--"):
        image_build(tmp_path, monkeypatch, {"llama.cpp": {"url": "https://bücher.example/llama.cpp.git"}})
