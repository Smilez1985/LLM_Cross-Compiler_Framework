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
