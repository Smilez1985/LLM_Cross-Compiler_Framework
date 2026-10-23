"""Offline mode reaches only local hosts (DECISIONS round 24, L-NET and "L-NET final"): every reach-out decision of
offline mode asks the one network-locality rule (orchestrator/utils/locality.py), and where a connection follows it
goes to the checked address.

- the AI provider's address and Ollama's (llm/catalog.ProviderPreset.offline_refusal, LLMClient.resolve, the AI
  settings change), the SDK client of offline mode (llm/client.offline_http_client) and its refusal
  (llm/errors.map_openai_error);
- the pinned llama.cpp source from a local mirror (runtime/build/source.offline_reach, http.curloptResolve);
- the local/cloud labels of the bundled presets are pinned (they do not change)."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from orchestrator.llm.catalog import load_catalog
from orchestrator.utils import locality
from orchestrator.utils.locality import LocalityRefused


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


# --- the bundled presets keep their labels --------------------------------------------------------------------------

def test_the_local_and_cloud_labels_of_the_bundled_presets_do_not_change():
    catalog = load_catalog()
    labels = {pid: (p.location, p.is_local()) for pid, p in catalog.providers.items()}
    assert labels == {"anthropic": ("cloud", False), "openai": ("cloud", False), "google": ("cloud", False),
                      "openrouter": ("cloud", False), "ollama": ("local", True), "local": ("local", True),
                      "custom": ("auto", False)}
    # a configurable local engine keeps its label whatever its address; offline mode judges the address on its own
    assert catalog.get("ollama").is_local("https://ollama.example.com/v1") is True


# --- the AI provider's address ----------------------------------------------------------------------------------------

def client(tmp_path, **settings):
    from orchestrator.llm import LLMClient
    return LLMClient(Settings(**settings), None, state_dir=tmp_path / "state")


def test_offline_mode_refuses_ollama_outside_the_own_network(tmp_path, names):
    from orchestrator.llm import NotConfigured
    names.update({"ollama.example.com": ["198.51.100.20"], "gpu.lan": ["192.168.1.40"]})
    public = client(tmp_path, ai_provider="ollama", ai_base_url="https://ollama.example.com/v1", offline_mode=True)
    with pytest.raises(NotConfigured) as info:
        public.resolve()
    text = str(info.value)
    assert "ollama.example.com" in text and "on the internet" in text and "count it as local" in text
    assert [s.id for s in info.value.steps] == ["settings.local_network"]
    lan = client(tmp_path, ai_provider="ollama", ai_base_url="http://gpu.lan:11434/v1", offline_mode=True)
    assert lan.resolve().base_url == "http://gpu.lan:11434/v1"
    default = client(tmp_path, ai_provider="ollama", offline_mode=True)
    assert default.resolve().base_url == "http://localhost:11434/v1", "localhost needs no lookup"
    online = client(tmp_path, ai_provider="ollama", ai_base_url="https://ollama.example.com/v1")
    assert online.resolve().local, "without offline mode nothing changes"


def test_offline_mode_keeps_the_cloud_refusal_and_judges_a_custom_server_by_its_address(tmp_path, names):
    from orchestrator.llm import NotConfigured
    with pytest.raises(NotConfigured, match="is a cloud provider. Choose a local model."):
        client(tmp_path, ai_provider="anthropic", offline_mode=True).resolve()
    names["box.local"] = ["203.0.113.4"]
    with pytest.raises(NotConfigured, match="box.local resolves to 203.0.113.4 on the internet"):
        client(tmp_path, ai_provider="custom", ai_model="m", ai_base_url="http://box.local:8000/v1",
               offline_mode=True).resolve()
    names["box.local"] = ["192.168.1.30"]
    locality.clear_cache()
    target = client(tmp_path, ai_provider="custom", ai_model="m", ai_base_url="http://box.local:8000/v1",
                    offline_mode=True).resolve()
    assert target.local, "the privacy label follows the same rule"


def test_the_persons_opt_outs_count_for_the_provider(tmp_path, names):
    from orchestrator.llm import NotConfigured
    names["myhome.duckdns.org"] = ["198.51.100.7"]
    url = "https://myhome.duckdns.org/ollama/v1"
    with pytest.raises(NotConfigured):
        client(tmp_path, ai_provider="ollama", ai_base_url=url, offline_mode=True).resolve()
    allowed = client(tmp_path, ai_provider="ollama", ai_base_url=url, offline_mode=True,
                     offline_local_hosts=["myhome.duckdns.org"])
    assert allowed.resolve().base_url == url
    vpn = client(tmp_path, ai_provider="ollama", ai_base_url="http://100.80.1.2:11434/v1", offline_mode=True)
    with pytest.raises(NotConfigured, match="Tailscale address"):
        vpn.resolve()
    vpn.config.values["offline_local_vpns"] = ["tailscale"]
    assert vpn.resolve().base_url.startswith("http://100.80.1.2")


# --- the connection goes to the checked address ---------------------------------------------------------------------

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


def test_the_offline_client_connects_to_the_checked_address(names, server):
    from orchestrator.llm.client import offline_http_client
    port, seen = server
    names["box.lan"] = ["127.0.0.1"]
    http = offline_http_client(Settings(offline_mode=True))
    assert locality.check_host("box.lan").local
    names["box.lan"] = ["203.0.113.66"]          # a rebinding answer after the check
    response = http.get(f"http://box.lan:{port}/v1/models")
    assert response.status_code == 200 and seen == [f"box.lan:{port}"], "the checked address, the name as Host"
    with pytest.raises(LocalityRefused):
        http.get(f"http://203.0.113.66:{port}/v1/models", timeout=2)
    names["public.lan"] = ["203.0.113.66"]
    with pytest.raises(LocalityRefused):
        http.get(f"http://public.lan:{port}/v1/models", timeout=2)
    assert len(seen) == 1, "nothing reached a public address"


def test_offline_mode_gives_every_local_server_the_pinned_client(tmp_path, names):
    from orchestrator.llm import Target
    names["gpu.lan"] = ["192.168.1.40"]
    offline = client(tmp_path, ai_provider="ollama", ai_base_url="http://gpu.lan:11434/v1", offline_mode=True)
    target = offline.resolve()
    http = offline._http_client_for(target)
    assert http is offline._http_client_for(target) and http is not None
    assert type(http._transport._pool._network_backend).__name__ == "PinnedBackend"
    online = client(tmp_path, ai_provider="ollama", ai_base_url="http://gpu.lan:11434/v1")
    assert online._http_client_for(online.resolve()) is None, "online: the SDK default, as before"
    cloud = Target(preset=online.catalog.get("openrouter"), model="m", base_url="https://openrouter.ai/api/v1",
                   auth="api_key", local=False)
    assert offline._http_client_for(cloud) is http, "offline: even a cloud address only through the pinned client"
    assert online._http_client_for(cloud) is None


def test_a_refused_connection_reads_as_the_plain_refusal():
    import openai
    from orchestrator.llm.errors import NotConfigured, map_openai_error
    refused = LocalityRefused(locality.Locality("public.lan", False, "public", ("203.0.113.66",), ("203.0.113.66",)),
                              "the AI provider's server")
    err = openai.APIConnectionError(request=SimpleNamespace(method="POST", url="http://public.lan/v1"))
    err.__cause__ = refused
    mapped = map_openai_error(err, preset=load_catalog().get("ollama"), base_url="http://public.lan/v1")
    assert isinstance(mapped, NotConfigured) and "public.lan" in str(mapped) and "on the internet" in str(mapped)
    assert [s.id for s in mapped.steps] == ["settings.local_network"]


# --- the pinned source from a local mirror --------------------------------------------------------------------------

def test_a_mirror_in_the_own_network_is_pinned_to_its_checked_address(names, tmp_path):
    from orchestrator.runtime.build.source import SourceError, _git_config, offline_reach
    offline = Settings(offline_mode=True)
    names.update({"mirror.lan": ["192.168.1.5"], "v6.lan": ["fd00::5"], "github.com": ["140.82.121.4"]})
    assert offline_reach("https://mirror.lan/llama.cpp.git", offline) == "mirror.lan:443:192.168.1.5"
    assert offline_reach("https://v6.lan:8443/llama.cpp.git", offline) == "v6.lan:8443:[fd00::5]"
    assert offline_reach(str(tmp_path / "mirror"), offline) is None, "a local folder needs no network"
    assert offline_reach("https://github.com/ggml-org/llama.cpp", Settings()) is None, "online: as before"
    with pytest.raises(SourceError, match="github.com resolves to 140.82.121.4 on the internet"):
        offline_reach("https://github.com/ggml-org/llama.cpp", offline)
    assert "http.curloptResolve=mirror.lan:443:192.168.1.5" in _git_config(False, "mirror.lan:443:192.168.1.5")
    assert not any("curloptResolve" in a for a in _git_config(False))


def test_fetch_source_in_offline_mode_passes_the_pin_to_git(names, tmp_path, monkeypatch):
    from orchestrator.runtime.build import source as src
    names["mirror.lan"] = ["192.168.1.5"]
    calls = []

    def git(args, **kwargs):
        calls.append((list(args), kwargs.get("resolve")))
        raise src.SourceError("stop after the fetch call")
    pin = SimpleNamespace(repo="https://github.com/ggml-org/llama.cpp", commit="a" * 40, tag="b1")
    monkeypatch.setattr(src, "cached_source", lambda *a, **k: None)
    monkeypatch.setattr(src, "_init", lambda staging, g: None)
    # the real fetch_source behind the conftest guard (no test fetches from the network; this git is a fake)
    fetch = next(c.cell_contents for c in src.fetch_source.__closure__ or ()
                 if getattr(c.cell_contents, "__name__", "") == "fetch_source")
    with pytest.raises(src.SourceError):
        fetch(pin, tmp_path, url="https://mirror.lan/llama.cpp.git", git=git, settings=Settings(offline_mode=True))
    assert calls and calls[0][1] == "mirror.lan:443:192.168.1.5"
    with pytest.raises(src.SourceError, match="not on this computer or in your own network"):
        fetch(pin, tmp_path, url="https://public.example/llama.cpp.git", git=git,
              settings=Settings(offline_mode=True))
    assert len(calls) == 1, "nothing was fetched from the public mirror"


# --- the runtime's downloads (a local model mirror) -----------------------------------------------------------------

def test_the_local_only_session_connects_to_the_checked_address(names, server):
    import requests
    from orchestrator.security.http import local_only_session
    port, seen = server
    names["models.lan"] = ["127.0.0.1"]
    session = local_only_session(Settings(offline_mode=True))
    assert locality.check_host("models.lan").local
    names["models.lan"] = ["203.0.113.66"]           # a rebinding answer after the check
    assert session.get(f"http://models.lan:{port}/model.gguf", timeout=5).status_code == 200
    assert seen == [f"models.lan:{port}"] and session.trust_env is False
    with pytest.raises((LocalityRefused, requests.exceptions.ConnectionError)):
        session.get(f"http://203.0.113.66:{port}/model.gguf", timeout=2)
    assert len(seen) == 1


def test_runtime_downloads_in_offline_mode_reach_only_the_own_network(names, tmp_path):
    from orchestrator.runtime.llama_server import LocalLLMRuntime, LocalRuntimeError
    names.update({"models.lan": ["192.168.1.9"], "huggingface.co": ["18.239.50.1"]})
    rt = LocalLLMRuntime(data_dir=tmp_path / "state", models_dir=tmp_path / "models",
                         settings=Settings(offline_mode=True))
    with pytest.raises(LocalRuntimeError, match="huggingface.co resolves to 18.239.50.1 on the internet"):
        rt._fetch("https://huggingface.co/x/y/resolve/c/m.gguf", tmp_path / "m.gguf", 1, "0" * 64)
    assert not (tmp_path / "m.gguf").exists() and not (tmp_path / "m.gguf.part").exists()
    session = rt._offline_session("https://models.lan/x/y/resolve/c/m.gguf")
    assert type(session.get_adapter("https://models.lan/")).__name__ == "LocalOnlyAdapter"
    online = LocalLLMRuntime(data_dir=tmp_path / "state", models_dir=tmp_path / "models", settings=Settings())
    assert online._offline_session("https://huggingface.co/x") is None, "online: the downloads as before"
    framework = SimpleNamespace(config=SimpleNamespace(user_state_dir=tmp_path / "s", get=lambda k, d=None: d),
                                resolve_path=lambda p: tmp_path / p)
    assert LocalLLMRuntime.from_framework(framework).settings is framework.config


# --- review offline-lan-v: the connection goes to a checked address, and to every one of them ---------------------

def spy_backend(http):
    """The hosts the pinned client's own network backend is asked to connect to (after the locality check)."""
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


def test_the_offline_client_tries_every_checked_address(names, server):
    """A name with an IPv6 and an IPv4 address in the own network (a home router hands out both) and a server that
    listens on IPv4 only (Ollama with OLLAMA_HOST=0.0.0.0): the system's own connection tries the next address, the
    pinned client gave up after the first one."""
    from orchestrator.llm.client import offline_http_client
    port, seen = server
    names["box.lan"] = ["::1", "127.0.0.1"]
    http = offline_http_client(Settings(offline_mode=True))
    asked = spy_backend(http)
    assert http.get(f"http://box.lan:{port}/v1/models", timeout=10).status_code == 200
    assert asked == ["::1", "127.0.0.1"] and seen == [f"box.lan:{port}"]


def test_the_offline_client_pins_localhost_names_too(names, server):
    """*.localhost and localhost. are this computer by the rule (no lookup), but a system with `hosts: files dns`
    asks the DNS server for them: the pinned client handed such a name to the system resolver, so the answer of a DNS
    server decided where the prompts went in offline mode. It connects to the loopback addresses instead."""
    from orchestrator.llm.client import offline_http_client
    port, seen = server
    http = offline_http_client(Settings(offline_mode=True))
    asked = spy_backend(http)
    assert http.get(f"http://box.localhost:{port}/v1/models", timeout=10).status_code == 200
    assert asked == ["127.0.0.1"] and seen == [f"box.localhost:{port}"]


def test_the_local_only_session_tries_every_checked_address(names, server):
    from orchestrator.security.http import local_only_session
    port, seen = server
    names["models.lan"] = ["::1", "127.0.0.1"]
    session = local_only_session(Settings(offline_mode=True))
    assert session.get(f"http://models.lan:{port}/model.gguf", timeout=10).status_code == 200
    assert seen == [f"models.lan:{port}"]


def test_the_mirror_pin_names_every_checked_address_and_the_host_as_git_spells_it(names):
    """curl matches http.curloptResolve by the host as the URL spells it: a trailing dot made curl look the name up
    itself (the pin did not apply). Every checked address is pinned (curl tries them in turn), and a literal address
    needs no pin (curl cannot parse an IPv6 one there: the fetch failed)."""
    from orchestrator.runtime.build.source import offline_reach
    offline = Settings(offline_mode=True)
    names.update({"mirror.lan": ["fd00::5", "192.168.1.5"]})
    assert offline_reach("https://mirror.lan/llama.cpp.git", offline) == "mirror.lan:443:[fd00::5],192.168.1.5"
    assert offline_reach("https://mirror.lan.:8443/llama.cpp.git", offline) == \
        "mirror.lan.:8443:[fd00::5],192.168.1.5"
    assert offline_reach("https://[fd00::5]:8443/llama.cpp.git", offline) is None
    assert offline_reach("https://192.168.1.5/llama.cpp.git", offline) is None


def test_git_in_offline_mode_uses_no_proxy_and_follows_no_redirect(monkeypatch):
    """In offline mode git reaches the mirror directly: no proxy from the environment (it would receive the request
    and resolve the name itself) and no redirect to a host nobody checked."""
    import subprocess
    from orchestrator.runtime.build import source as src
    for var in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "http_proxy"):
        monkeypatch.setenv(var, "http://proxy.example:3128")
    runs = []

    def runner(argv, **kwargs):
        runs.append((argv, kwargs["env"]))
        return subprocess.CompletedProcess(argv, 0, "", "")
    git = src.Git(runner=runner, git="git")
    git(["fetch", "https://mirror.lan/x"], resolve="mirror.lan:443:192.168.1.5")
    git(["fetch", "https://192.168.1.5/x"], offline=True)
    for argv, env in runs:
        assert "http.proxy=" in argv and "http.followRedirects=false" in argv, argv
        assert not [k for k in env if k.lower().endswith("_proxy")], env
    git(["fetch", "https://github.com/x"])
    argv, env = runs[-1]
    assert "http.proxy=" not in argv and "http.followRedirects=false" not in argv, "online: as before"
    assert env.get("HTTPS_PROXY") == "http://proxy.example:3128"


def real_fetch_source():
    """fetch_source behind the conftest guard (no test fetches from the network; these reach only 127.0.0.1)."""
    from orchestrator.runtime.build import source as src
    return next(c.cell_contents for c in src.fetch_source.__closure__ or ()
                if getattr(c.cell_contents, "__name__", "") == "fetch_source")


def test_fetch_source_in_offline_mode_gives_git_the_offline_options_also_for_an_address(tmp_path, monkeypatch):
    from orchestrator.runtime.build import source as src
    calls = []

    def git(args, **kwargs):
        calls.append(kwargs)
        raise src.SourceError("stop after the fetch call")
    pin = SimpleNamespace(repo="https://github.com/ggml-org/llama.cpp", commit="a" * 40, tag="b1")
    monkeypatch.setattr(src, "cached_source", lambda *a, **k: None)
    monkeypatch.setattr(src, "_init", lambda staging, g: None)
    with pytest.raises(src.SourceError):
        real_fetch_source()(pin, tmp_path, url="https://[fd00::5]:8443/llama.cpp.git", git=git,
                            settings=Settings(offline_mode=True))
    assert calls and calls[0].get("offline") is True and not calls[0].get("resolve")


@pytest.fixture
def listeners():
    """TCP listeners on 127.0.0.1 that record every connection (the first bytes each one sent)."""
    import socket
    made = []

    def listener():
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(4)
        sock.settimeout(0.2)
        got, stop = [], threading.Event()

        def serve():
            while not stop.is_set():
                try:
                    conn, _ = sock.accept()
                except OSError:
                    continue
                conn.settimeout(2)
                try:
                    got.append(conn.recv(200))
                except OSError:
                    got.append(b"")
                conn.close()
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        made.append((sock, stop, thread))
        return sock.getsockname()[1], got
    yield listener
    for sock, stop, thread in made:
        stop.set()
        thread.join(2)
        sock.close()


def test_a_real_fetch_in_offline_mode_goes_to_the_checked_address_not_to_the_proxy(names, tmp_path, monkeypatch,
                                                                                  listeners):
    """The real git: with HTTPS_PROXY set it sent `CONNECT mirror.lan:PORT` to the proxy (the pin applies only to a
    connection git makes itself), and a trailing dot in the URL made curl resolve the name on its own. Now git
    connects to the checked address of the mirror and nowhere else."""
    import shutil
    import subprocess
    from orchestrator.runtime.build import source as src
    if not shutil.which("git"):
        pytest.skip("git is not installed")
    version = subprocess.run(["git", "--version"], capture_output=True, text=True).stdout.split()[-1]
    if tuple(int(p) for p in version.split(".")[:2] if p.isdigit()) < (2, 37):
        pytest.skip("git older than 2.37 has no http.curloptResolve")
    proxy_port, via_proxy = listeners()
    mirror_port, direct = listeners()
    for var in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(var, f"http://127.0.0.1:{proxy_port}")
    for var in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(var, "")
    names["mirror.lan"] = ["127.0.0.1"]
    pin = SimpleNamespace(repo="https://github.com/ggml-org/llama.cpp", commit="a" * 40, tag="b1")
    for url in (f"https://mirror.lan:{mirror_port}/llama.cpp.git",
                f"https://mirror.lan.:{mirror_port}/llama.cpp.git"):
        before = len(direct)
        with pytest.raises(src.SourceError):   # the listener speaks no TLS
            real_fetch_source()(pin, tmp_path / "state", url=url, settings=Settings(offline_mode=True))
        assert via_proxy == [], f"nothing went to the proxy ({url})"
        assert len(direct) > before, f"git connected to the checked address of the mirror ({url})"
