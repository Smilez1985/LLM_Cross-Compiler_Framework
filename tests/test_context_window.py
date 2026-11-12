"""The real context window per model (llm/context_window.py): the local server's /props and its -c, the providers'
servers through stand-ins of their documented answers (Anthropic's Models API, OpenRouter's /models, Ollama's /api/ps
and /api/show, LM Studio's /api/v1/models, vLLM's max_model_len, llama.cpp's /props), the 24-hour cache with its
source, the pinned table, the conservative default and the person's ai_context_window. No request leaves the test:
every server is a FakeHTTP queue that fails on an unexpected request."""

import json
import os
import stat

import pytest

pytest.importorskip("anthropic")
pytest.importorskip("openai")

from test_llm import SECRETS, FakeConfig, FakeHTTP, FakeSecrets  # noqa: E402

from orchestrator.llm import LLMClient  # noqa: E402
from orchestrator.llm.context_window import (CACHE_TTL, ContextWindow, context_window, load_table,  # noqa: E402
                                             pinned_window, window_lines)

MODEL_INFO = {"id": "claude-opus-5-5", "type": "model", "display_name": "Claude Opus 5.5",
              "created_at": "2026-09-01T00:00:00Z", "lifecycle": "active", "max_input_tokens": 1000000,
              "max_tokens": 128000}


def client_for(tmp_path, http=None, **settings):
    return LLMClient(FakeConfig(**settings), FakeSecrets(SECRETS), state_dir=tmp_path / "state",
                     http_client=http.client() if http else None, sleep=lambda s: None)


# --- the local server -------------------------------------------------------------------------------------------------

@pytest.fixture
def local(monkeypatch):
    """The managed server's endpoint as server.json holds it (n_ctx from /props, ctx from -c)."""
    from orchestrator.runtime import llama_server
    state = {}

    def endpoint(data_dir=None):
        if not state:
            return None
        return llama_server.LocalEndpoint(base_url="http://127.0.0.1:18080/v1", api_key="k", model="qwen3.5-2b",
                                          port=18080, pid=1, **state)
    monkeypatch.setattr(llama_server, "managed_endpoint", endpoint)
    monkeypatch.setattr(LLMClient, "_managed_runtime_endpoint",
                        lambda self, provider: ("http://127.0.0.1:18080/v1", "k", "qwen3.5-2b") if state else None)
    return state


def test_the_local_window_is_what_props_confirmed(tmp_path, local):
    local.update(n_ctx=12288, ctx=12288)
    window = context_window(client_for(tmp_path, ai_provider="local"))
    assert (window.tokens, window.source, window.started, window.confirmed) == (12288, "local_server", 12288, True)
    assert "/props" in window.how
    assert "confirmed by /props" in "\n".join(window_lines(window))


def test_before_props_answered_the_started_context_counts_and_says_so(tmp_path, local):
    local.update(n_ctx=None, ctx=8192)
    window = context_window(client_for(tmp_path, ai_provider="local"))
    assert (window.tokens, window.source, window.confirmed) == (8192, "local_started", None)
    assert "not confirmed by /props yet" in "\n".join(window_lines(window))


def test_a_server_that_reports_another_context_is_shown_with_both(tmp_path, local):
    local.update(n_ctx=4096, ctx=8192)
    window = context_window(client_for(tmp_path, ai_provider="local"))
    assert (window.tokens, window.confirmed) == (4096, False), "the server's own number is used"


def test_the_persons_setting_never_goes_above_the_local_server(tmp_path, local):
    local.update(n_ctx=8192, ctx=8192)
    assert context_window(client_for(tmp_path, ai_provider="local", ai_context_window=4096)).tokens == 4096
    high = context_window(client_for(tmp_path, ai_provider="local", ai_context_window=65536))
    assert high.tokens == 8192 and high.source == "local_server" and "above what the server reports" in high.note


# --- Anthropic: the Models API, the cache, the table ------------------------------------------------------------------

def test_anthropic_tells_the_window_and_the_answer_is_kept_for_a_day(tmp_path):
    http = FakeHTTP().add(MODEL_INFO)
    client = client_for(tmp_path, http, ai_provider="anthropic")
    window = context_window(client, now=1000.0)
    assert http.last.path == "/v1/models/claude-opus-5-5"
    assert (window.tokens, window.max_output, window.source, window.cached) == (1000000, 128000, "provider", False)
    assert "Models API" in window.how
    again = context_window(client, now=1000.0 + CACHE_TTL - 1)
    assert len(http.requests) == 1 and again.cached and again.tokens == 1000000
    path = tmp_path / "state" / "llm" / "context_windows.json"
    kept = json.loads(path.read_text())
    assert [entry["how"] for entry in kept.values()] == [window.how]
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    http.add(dict(MODEL_INFO, max_input_tokens=200000))
    assert context_window(client, now=1000.0 + CACHE_TTL + 1).tokens == 200000, "after a day it asks again"
    http.add(dict(MODEL_INFO, max_input_tokens=300000))
    assert context_window(client, refresh=True, now=1000.0 + CACHE_TTL + 2).tokens == 300000


def test_a_status_read_never_asks_a_server(tmp_path):
    client = client_for(tmp_path, FakeHTTP(), ai_provider="anthropic")
    window = context_window(client, cached_only=True)
    assert window.source == "table" and window.tokens == 1000000 and "models/overview" in window.how


def test_an_unreachable_server_falls_back_to_the_table_then_the_default(tmp_path):
    http = FakeHTTP().add({"type": "error", "error": {"type": "api_error", "message": "x"}}, status=500)
    window = context_window(client_for(tmp_path, http, ai_provider="anthropic", ai_model="claude-haiku-4-5-20251001"))
    assert (window.source, window.tokens, window.max_output) == ("table", 200000, 64000)
    http.add({"type": "error", "error": {"type": "not_found_error", "message": "x"}}, status=404)
    unknown = context_window(client_for(tmp_path, http, ai_provider="anthropic", ai_model="claude-unknown"))
    assert unknown.source == "default" and unknown.tokens == 32768 and "conservative default" in unknown.how


def test_the_table_names_its_source_and_date_for_every_entry():
    table = load_table()
    for entry in table["windows"]:
        assert entry["source"].startswith("https://") and entry["checked"] == "2026-11-12", entry
        assert entry["tokens"] >= entry["max_output"] > 0
    assert pinned_window("anthropic", "claude-sonnet-4-5-20250929")["tokens"] == 200000, "a prefix entry"
    assert pinned_window("anthropic", "claude-opus-5-5")["tokens"] == 1000000
    assert pinned_window("openai", "claude-opus-5-5") is None, "an entry belongs to its provider"


def test_a_provider_that_is_not_configured_gets_the_default_with_the_reason(tmp_path):
    window = context_window(client_for(tmp_path, ai_provider="no-such-provider"))
    assert window.source == "default" and "Unknown AI provider" in window.note


def test_the_persons_setting_is_the_window_where_no_server_tells(tmp_path):
    window = context_window(client_for(tmp_path, ai_provider="openai", ai_context_window=400000))
    assert (window.tokens, window.source) == (400000, "setting")
    assert ContextWindow(**window.data()).words().startswith("400000 tokens, from your setting")


def test_openai_is_not_asked_its_list_names_no_length(tmp_path):
    http = FakeHTTP()
    window = context_window(client_for(tmp_path, http, ai_provider="openai"))
    assert http.requests == [] and window.source == "default"


# --- OpenAI-compatible servers ----------------------------------------------------------------------------------------

def test_openrouter_tells_context_length_in_its_model_list(tmp_path):
    http = FakeHTTP().add({"data": [{"id": "vendor/model-a", "name": "Model A", "context_length": 131072,
                                     "top_provider": {"context_length": 131072, "max_completion_tokens": 8192,
                                                      "is_moderated": False}}]})
    window = context_window(client_for(tmp_path, http, ai_provider="openrouter", ai_model="vendor/model-a",
                                       openrouter_api_key="x"))
    assert http.last.path.endswith("/models") and len(http.requests) == 1, "a cloud server gets no engine probe"
    assert (window.tokens, window.max_output, window.source) == (131072, 8192, "provider")
    assert "context_length" in window.how


def test_ollama_names_the_loaded_models_context(tmp_path):
    http = (FakeHTTP().add({"error": "not found"}, status=404)               # /props: not llama.cpp
            .add({"models": [{"name": "qwen3.5:4b", "model": "qwen3.5:4b", "context_length": 32768}]}))
    window = context_window(client_for(tmp_path, http, ai_provider="ollama"))
    assert [r.path for r in http.requests] == ["/props", "/api/ps"]
    assert (window.tokens, window.source) == (32768, "provider") and "/api/ps" in window.how


def test_ollama_before_it_loaded_the_model_gets_its_default_and_is_asked_again(tmp_path):
    def queue(http):
        return (http.add({"error": "not found"}, status=404).add({"models": []})
                .add({"model_info": {"general.architecture": "qwen35", "qwen35.context_length": 262144}}))
    http = queue(FakeHTTP())
    client = client_for(tmp_path, http, ai_provider="ollama")
    window = context_window(client)
    assert [r.path for r in http.requests] == ["/props", "/api/ps", "/api/show"]
    assert http.requests[-1].json == {"model": "qwen3.5:4b"}
    assert window.tokens == 4096 and "not loaded yet" in window.how
    queue(http)
    context_window(client)
    assert len(http.requests) == 6, "not cached: asked again at the next turn"


def test_lm_studio_names_the_loaded_instances_context(tmp_path):
    http = (FakeHTTP().add({}, status=404).add({}, status=404)
            .add({"models": [{"type": "llm", "key": "model-b", "display_name": "B", "max_context_length": 262144,
                              "loaded_instances": [{"id": "model-b", "config": {"context_length": 16384}}]}]}))
    window = context_window(client_for(tmp_path, http, ai_provider="custom", ai_base_url="http://127.0.0.1:1234/v1",
                                       ai_model="model-b"))
    assert [r.path for r in http.requests] == ["/props", "/api/ps", "/api/v1/models"]
    assert window.tokens == 16384 and "loaded instance" in window.how


def test_vllm_max_model_len_is_read_when_the_server_sends_it(tmp_path):
    http = (FakeHTTP().add({}, status=404).add({}, status=404).add({}, status=404)
            .add({"object": "list", "data": [{"id": "model-c", "object": "model", "max_model_len": 40960}]}))
    window = context_window(client_for(tmp_path, http, ai_provider="custom", ai_base_url="http://127.0.0.1:8000/v1",
                                       ai_model="model-c"))
    assert window.tokens == 40960 and "max_model_len" in window.how


def test_a_llama_cpp_server_behind_a_custom_address_answers_props(tmp_path):
    http = FakeHTTP().add({"default_generation_settings": {"n_ctx": 2048}, "total_slots": 1})
    window = context_window(client_for(tmp_path, http, ai_provider="custom", ai_base_url="http://127.0.0.1:8080/v1",
                                       ai_model="smol"))
    assert window.tokens == 2048 and window.source == "provider" and http.last.path == "/props"


def test_get_status_names_the_window_without_a_request(tmp_path):
    """The AI's read-only fact (get_status, section ai_access): from what is known already, never a request."""
    from types import SimpleNamespace
    from orchestrator.Core.ditto_manager import DittoCoder
    from orchestrator.tools import tools_status
    http = FakeHTTP()
    config = FakeConfig(ai_provider="anthropic")
    ditto = DittoCoder(None, config_manager=config, client=client_for(tmp_path, http, ai_provider="anthropic"))

    def no_sign_in():
        raise RuntimeError("not needed here")
    section = tools_status.ai_access_section(SimpleNamespace(ditto_manager=ditto, secrets_manager=None), no_sign_in)
    assert http.requests == []
    window = section.context_window
    assert (window.tokens, window.source, window.model) == (1000000, "table", "claude-opus-5-5")
    assert "context_window" in tools_status.AiAccessSection.model_json_schema()["properties"]
