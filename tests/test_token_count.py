"""Counting tokens (llm/token_count.py): the measured estimate per model family, the calibration every answer's own
usage feeds, and the exact counts where they cost nothing (llama.cpp's /apply-template and /tokenize, Anthropic's
count_tokens) through stand-ins of their documented answers; a cloud server behind the OpenAI-compatible adapter is
never asked."""

import json
import os
import stat

import pytest

pytest.importorskip("anthropic")
pytest.importorskip("openai")

from test_llm import SECRETS, FakeConfig, FakeHTTP, FakeSecrets, ask, claude_message, completion, text_block  # noqa

from orchestrator.llm import LLMClient, NotConfigured  # noqa: E402
from orchestrator.llm import token_count  # noqa: E402
from orchestrator.llm.types import ChatRequest, Message, ToolSpecLLM  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def client_for(tmp_path, http=None, **settings):
    return LLMClient(FakeConfig(**settings), FakeSecrets(SECRETS), state_dir=tmp_path / "state",
                     http_client=http.client() if http else None, sleep=lambda s: None)


def test_the_family_comes_from_the_model_id_and_unknown_ones_get_the_smallest_ratio():
    assert token_count.family_of("qwen3.5:4b").chars_per_token == 3.31
    assert token_count.family_of("Qwen3.5-2B-Q4_K_M").family == "qwen"
    assert token_count.family_of("claude-opus-5-5").chars_per_token == 2.5
    assert token_count.family_of("smollm2-135m").max_overestimate_pct == 54.2
    other = token_count.family_of("gpt-6.1-sol")
    assert other.family == "other" and other.chars_per_token == 2.5


def test_the_table_ratios_are_the_measured_ones_and_the_fallback_is_the_smallest():
    table = token_count.load_table()
    families = table["estimates"]["families"]
    assert all(f["source"] for f in families)
    assert table["estimates"]["fallback"]["chars_per_token"] == min(f["chars_per_token"] for f in families)


def test_the_estimate_rounds_up():
    family = token_count.Family(family="x", chars_per_token=4.0)
    assert token_count.estimate("", family) == 0
    assert token_count.estimate("abcde", family) == 2


def test_the_request_text_holds_system_tools_messages_and_call_arguments():
    from orchestrator.llm.types import ToolCall
    request = ChatRequest(system="SYS", tools=[ToolSpecLLM(name="get_status", description="D", input_schema={})],
                          messages=[Message.user("Q"), Message(role="assistant", content="",
                                                               tool_calls=[ToolCall(id="t", name="n",
                                                                                    arguments={"a": 1})])])
    text = token_count.request_text(request)
    assert "SYS" in text and '"name":"get_status"' in text and "Q" in text and 'n {"a": 1}' in text


def test_the_calibration_follows_the_providers_own_numbers(tmp_path):
    store = token_count.Calibration(tmp_path / "cal.json")
    assert store.get("p", "m") == {"factor": 1.0, "answers": 0, "last_error_pct": None}
    first = store.record("p", "m", estimated=100, actual=120)
    assert first == {"factor": 1.2, "answers": 1, "last_error_pct": -16.7}
    second = store.record("p", "m", estimated=100, actual=100)
    assert second["answers"] == 2 and second["factor"] == round(0.7 * 1.2 + 0.3 * 1.0, 4)
    assert second["last_error_pct"] == 20.0, "the calibrated estimate (120) was 20 % too high"
    assert store.record("p", "m", estimated=0, actual=10) is None
    for _ in range(30):
        store.record("p", "m", estimated=100, actual=1000)
    assert store.get("p", "m")["factor"] == 2.0, "kept inside its range"
    if os.name == "posix":
        assert stat.S_IMODE((tmp_path / "cal.json").stat().st_mode) == 0o600


def test_every_answer_calibrates_the_estimate(tmp_path):
    http = FakeHTTP().add(claude_message([text_block("ok")], usage={"input_tokens": 10, "output_tokens": 5,
                                                                    "cache_read_input_tokens": 4,
                                                                    "cache_creation_input_tokens": 0}))
    client = client_for(tmp_path, http, ai_provider="anthropic")
    client.chat(ask("Hello there"))
    entry = client.token_calibration().get("anthropic", "claude-opus-5-5")
    assert entry["answers"] == 1
    estimated = token_count.estimate(token_count.request_text(ask("Hello there")),
                                     token_count.family_of("claude-opus-5-5"))
    assert entry["factor"] == round(14 / estimated, 4), "input and cache reads count"
    assert (tmp_path / "state" / "llm" / "token_calibration.json").is_file()


def test_the_tally_says_how_it_counted(tmp_path):
    parts = token_count.tally("p", "qwen3.5:4b", system="s" * 331, tools=(), question=[Message.user("q" * 331)],
                              history=[[Message.user("h" * 662)]], calibration={"factor": 1.0, "answers": 0})
    assert (parts.system, parts.question, parts.history, parts.total) == (100, 100, [200], 400)
    assert parts.detail == "estimate (qwen, 3.31 characters per token; not calibrated yet)"
    calibrated = token_count.tally("p", "qwen3.5:4b", system="s" * 331, tools=(), question=[], history=[],
                                   calibration={"factor": 1.5, "answers": 4, "last_error_pct": 3.1})
    assert calibrated.system == 150
    assert calibrated.detail == "estimate (qwen, calibrated on 4 answers, last error 3.1 %)"
    exact = token_count.scaled(parts, 800, "tokenizer")
    assert exact.total == 800 and exact.history == [400] and exact.detail == "tokenizer"


def test_llama_cpp_counts_the_request_with_its_own_template(tmp_path):
    http = (FakeHTTP().add({"prompt": "<|im_start|>user\nHello<|im_end|>\n<|im_start|>assistant\n"})
            .add({"tokens": [1, 2, 3, 4, 5, 6, 7]}))
    client = client_for(tmp_path, http, ai_provider="custom", ai_base_url="http://127.0.0.1:8080/v1", ai_model="m")
    request = ChatRequest(system="Be brief.", messages=[Message.user("Hello")],
                          tools=[ToolSpecLLM(name="get_status", description="d", input_schema={"type": "object"})])
    assert token_count.exact_total(client, request, "custom") == (7, "tokenizer")
    template, tokenize = http.requests
    assert template.path == "/apply-template" and template.json["messages"][0] == {"role": "system",
                                                                                   "content": "Be brief."}
    assert template.json["tools"][0]["function"]["name"] == "get_status"
    assert tokenize.path == "/tokenize" and tokenize.json["content"].startswith("<|im_start|>user")


def test_a_server_without_tokenize_keeps_the_estimate(tmp_path):
    http = FakeHTTP().add({"error": "not found"}, status=404)
    client = client_for(tmp_path, http, ai_provider="custom", ai_base_url="http://127.0.0.1:8080/v1", ai_model="m")
    assert token_count.exact_total(client, ask(), "custom") is None


def test_anthropic_counts_for_free_through_the_privacy_filter(tmp_path):
    http = FakeHTTP().add({"input_tokens": 4242})
    client = client_for(tmp_path, http, ai_provider="anthropic")
    request = ask("Mail me at someone@example.org")
    assert token_count.exact_total(client, request, "anthropic") == (4242, "provider count")
    assert http.last.path == "/v1/messages/count_tokens"
    sent = json.dumps(http.last.json)
    assert "someone@example.org" not in sent, "the count goes through the privacy filter like a call"
    assert "max_tokens" not in http.last.json and "cache_control" not in sent


def test_a_cloud_server_behind_the_openai_compatible_adapter_is_never_counted(tmp_path):
    http = FakeHTTP()
    client = client_for(tmp_path, http, ai_provider="openrouter", ai_model="vendor/model")
    assert token_count.exact_total(client, ask(), "openrouter") is None
    with pytest.raises(NotConfigured):
        client.count_input_tokens(ask())
    assert http.requests == []


def test_the_measurement_script_reads_the_fixed_corpus():
    import importlib.util
    spec = importlib.util.spec_from_file_location("measure_token_ratio",
                                                  os.path.join(REPO, "scripts", "measure_token_ratio.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    corpus = module.corpus()
    assert set(corpus) == {"prose_en", "prose_de", "code", "json"}
    assert all(0 < len(text) <= module.SAMPLE_CHARS for text in corpus.values())
