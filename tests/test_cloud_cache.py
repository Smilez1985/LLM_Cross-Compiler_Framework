"""Cloud caching the provider's way (llm/cloud_cache.py) with stand-ins of the providers' documented request and answer
shapes, no network and no paid call: Claude's cache points (where, how many, which lifetime), OpenRouter's pass-through
for an anthropic/ model only, OpenAI's automatic caching (nothing extra sent), the usage fields of Anthropic, OpenRouter
and OpenAI, the prefix staying byte for byte the same across Ditto's turns, the lifetime setting, the soft cap's notice
(once, caching goes on), `llm-cli ditto context cache`, get_status and the Cloud cache box (offscreen, a real
QApplication)."""

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("anthropic")
pytest.importorskip("openai")
pytest.importorskip("httpx2")

from test_ditto import registry  # noqa: E402,F401 - a fixture
from test_llm import (KEY, SECRETS, TOOLS, FakeConfig, FakeHTTP, FakeSecrets, ask, claude_message,  # noqa: E402,F401
                      completion, make_client, responses_body, text_block)

from orchestrator.Core.ditto_manager import ChatHistory, DittoCoder  # noqa: E402
from orchestrator.llm import LLMClient  # noqa: E402
from orchestrator.llm import cloud_cache as cc  # noqa: E402
from orchestrator.llm.catalog import load_catalog  # noqa: E402
from orchestrator.llm.context_window import ContextWindow  # noqa: E402
from orchestrator.llm.providers.anthropic import count_breakpoints  # noqa: E402
from orchestrator.llm.types import Usage  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ROUTER_KEY = SECRETS["openrouter_api_key"]


def claude_usage(read=0, written=0, uncached=10, hour=0):
    """Anthropic's documented usage object: input_tokens is what follows the last cache point; cache_creation splits the
    written tokens by lifetime."""
    return {"input_tokens": uncached, "output_tokens": 5, "cache_read_input_tokens": read,
            "cache_creation_input_tokens": written,
            "cache_creation": {"ephemeral_5m_input_tokens": written - hour, "ephemeral_1h_input_tokens": hour}}


# --- the table and the rules ----------------------------------------------------------------------------------------

def test_the_table_names_its_sources_and_every_current_claude_model():
    table = cc.load_table()
    assert table["checked"] == "2026-11-13" and table["breakpoints_max"] == cc.MAX_BREAKPOINTS == 4
    assert all(s.startswith("https://") for s in table["sources"])
    assert cc.min_tokens("claude-opus-5-5") == 512 and cc.min_tokens("claude-haiku-4-5-20251001") == 4096
    assert cc.min_tokens("claude-opus-5") == 512 and cc.min_tokens("claude-opus-4-7") == 2048
    assert cc.claude_entry("claude-opus-5-5")["model"] == "claude-opus-5-5", "an exact id is never a prefix of another"
    assert cc.min_tokens("anthropic/claude-sonnet-4.6") == 1024, "OpenRouter's name is matched as Anthropic's id"
    assert cc.min_tokens("claude-zeta-9") == 512, "an unknown model gets the smallest minimum: no point withheld"


def test_write_cost_prices_both_lifetimes_and_never_guesses():
    usage = Usage(input_tokens=10, cache_write_tokens=1_000_000, cache_write_1h_tokens=400_000)
    assert cc.write_cost(usage, "claude-opus-5-5") == pytest.approx((600_000 * 5.0 + 400_000 * 8.0) / 1e6)
    assert cc.write_cost(Usage(cache_write_tokens=200_000), "claude-haiku-5-5") == pytest.approx(0.2 * 0.625), \
        "Haiku 5.5 above 100,000 prompt tokens at its higher price"
    assert cc.write_cost(Usage(cache_write_tokens=50), "gpt-6.1-sol") is None, "no price: unpriced, not guessed"
    assert cc.write_cost(Usage(input_tokens=99), "gpt-6.1-sol") == 0.0


def test_the_way_follows_the_preset_and_the_upstream():
    catalog = load_catalog()
    assert cc.cache_way(catalog.get("anthropic"), "claude-opus-5-5") == "breakpoints"
    assert cc.cache_way(catalog.get("openrouter"), "anthropic/claude-opus-5.5") == "openrouter_breakpoints"
    assert cc.cache_way(catalog.get("openrouter"), "deepseek/deepseek-v4") == "automatic"
    assert cc.cache_way(catalog.get("openai"), "gpt-6.1-sol") == "automatic"
    assert cc.cache_way(catalog.get("google"), "gemini-3.8-flash") == "automatic"
    for local in ("ollama", "local", "custom"):
        assert cc.cache_way(catalog.get(local), "any") == "none"


def test_the_settings_and_their_one_writer(tmp_path):
    config = FakeConfig()
    assert cc.ttl_setting(config) == "5m" and cc.cost_limit(config) == 1.0, "the short lifetime is the default"
    config.values.update(ai_cache_ttl="2h", ai_cache_cost_limit="lots")
    assert cc.ttl_setting(config) == "5m" and cc.cost_limit(config) == 1.0, "a wrong value reads as the default"
    config.set = lambda k, v: config.values.__setitem__(k, v)
    saved = []
    config.save_user_config = lambda: saved.append(True) or True
    assert cc.set_cache_settings(config, "1h", 2.5) and saved
    assert cc.ttl_setting(config) == "1h" and cc.cost_limit(config) == 2.5
    for bad in (dict(ttl="30m"), dict(limit=-1), dict(limit="x")):
        with pytest.raises(ValueError):
            cc.set_cache_settings(config, **bad)
    assert cc.ttl_setting(config) == "1h", "a refused value changes nothing"


# --- Claude ------------------------------------------------------------------------------------------------------------

class TestClaude:

    def test_cache_points_on_the_system_text_and_the_moving_tail_with_the_default_lifetime(self, make_client):
        http = FakeHTTP().add(claude_message([text_block("ok")], usage=claude_usage(read=900, written=40)))
        result = make_client(http, SECRETS, ai_provider="anthropic").chat(ask(tools=TOOLS))
        body = http.last.json
        assert body["system"][-1]["cache_control"] == {"type": "ephemeral"}, "the stable prefix ends with the system"
        assert body["cache_control"] == {"type": "ephemeral"}, "automatic caching moves with the conversation"
        assert count_breakpoints(body) == 2 <= cc.MAX_BREAKPOINTS
        assert all("cache_control" not in tool for tool in body["tools"]), "the system point covers the tools"
        assert all("cache_control" not in json.dumps(m) for m in body["messages"])
        assert result.usage.cache_read_tokens == 900 and result.usage.cache_write_tokens == 40
        assert result.usage.input_tokens == 10 and result.usage.cache_write_1h_tokens == 0

    def test_the_one_hour_lifetime_comes_from_the_setting_on_every_point(self, make_client):
        http = FakeHTTP().add(claude_message([text_block("ok")], usage=claude_usage(written=300, hour=300)))
        result = make_client(http, SECRETS, ai_provider="anthropic", ai_cache_ttl="1h").chat(ask())
        body = http.last.json
        assert body["cache_control"] == body["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}, \
            "the same lifetime on both points: a longer one never follows a shorter one"
        assert result.usage.cache_write_1h_tokens == 300

    def test_a_request_s_own_lifetime_wins_and_a_wrong_setting_is_the_default(self, make_client):
        http = FakeHTTP().add(claude_message([text_block("ok")])).add(claude_message([text_block("ok")]))
        client = make_client(http, SECRETS, ai_provider="anthropic", ai_cache_ttl="12h")
        client.chat(ask())
        assert http.last.json["cache_control"] == {"type": "ephemeral"}
        client.chat(ask(cache_ttl="1h"))
        assert http.last.json["cache_control"]["ttl"] == "1h"


def _ditto(tmp_path, http, **settings):
    settings.setdefault("ai_provider", "anthropic")
    config = FakeConfig(**settings)
    client = LLMClient(config, FakeSecrets(SECRETS), state_dir=tmp_path / "state", http_client=http.client(),
                       sleep=lambda s: None)
    return config, client


def _fixed_window(ditto):
    ditto.context_window = lambda refresh=False, cached_only=False: ContextWindow(
        tokens=1_000_000, max_output=128_000, source="table", provider="anthropic", model="claude-opus-5-5")


def test_ditto_s_prefix_stays_byte_for_byte_the_same_across_turns(tmp_path, registry):
    http = FakeHTTP()
    http.add(claude_message([text_block("First answer.")], usage=claude_usage(written=3000)))
    http.add(claude_message([text_block("Second answer.")], usage=claude_usage(read=3000, written=60)))
    config, client = _ditto(tmp_path, http)
    ditto = DittoCoder(None, config_manager=config, client=client, registry=registry)
    _fixed_window(ditto)
    history = ChatHistory()
    history.apply(ditto.chat("Which targets exist?", history))
    reply = ditto.chat("And which boards?", history)
    first, second = (r.json for r in http.requests)
    for part in ("tools", "system"):
        assert json.dumps(first[part], sort_keys=False) == json.dumps(second[part], sort_keys=False), part
    assert [t["name"] for t in first["tools"]] == sorted(t["name"] for t in first["tools"])
    assert second["messages"][:len(first["messages"])] == first["messages"], "the history is a prefix of the next"
    assert "Which targets exist?" not in json.dumps(first["system"]), "no question in the cached prefix"
    assert reply.cache_turn.read == 3000 and reply.cache.calls == 2 and reply.cache.read == 3000
    assert reply.cache_lines[0][0] == "activity.cache.turn"
    assert reply.cache_lines[0][1] == {"read": "3000", "written": "60", "uncached": "10", "share": "98"}


def test_the_soft_cap_tells_once_and_caching_goes_on(tmp_path):
    http = FakeHTTP()
    for _ in range(3):   # 200,000 tokens written for 1 hour on Opus 5.5: 1.60 USD each
        http.add(claude_message([text_block("ok")], usage=claude_usage(written=200_000, hour=200_000)))
    config, client = _ditto(tmp_path, http, ai_cache_ttl="1h", ai_cache_cost_limit=3.0)
    ditto = DittoCoder(None, config_manager=config, client=client)
    _fixed_window(ditto)
    history = ChatHistory(tmp_path / "chat_history.json")
    capped = []
    for question in ("one", "two", "three"):
        reply = ditto.chat(question, history)
        history.apply(reply)
        capped.append(reply.cache_cap)
        assert http.last.json["cache_control"] == {"type": "ephemeral", "ttl": "1h"}, "the cap never turns it off"
    assert capped == [False, True, False], "told in the turn that crossed it, once per conversation and limit"
    assert history.cache["cost_usd"] == pytest.approx(4.8) and history.cache["cap_told"] == 3.0
    assert history.save()
    assert ChatHistory.load(history.path).cache["written_1h"] == 600_000, "the sums are kept with the conversation"


def test_the_cap_line_and_a_turn_without_cache_numbers():
    tally = cc.CacheTally().plus(Usage(input_tokens=200), "claude-opus-5-5")
    lines = cc.turn_lines("breakpoints", tally, tally, 1.0, False, "claude-opus-5-5")
    assert lines == [("activity.cache.none_breakpoints", {"uncached": "200", "minimum": "512"})]
    assert cc.turn_lines("none", tally, tally, 1.0, False) == [], "a local engine has no cloud cache line"
    total = cc.CacheTally(cost_usd=1.2, written=5000)
    assert cc.turn_lines("automatic", cc.CacheTally(calls=1, read=5), total, 1.0, True)[-1] == \
        ("activity.cache.cap", {"cost": "1.20", "limit": "1.00", "written": "5000"})
    assert not cc.cap_crossed(cc.CacheTally(), total, 0.0), "0 turns the notice off"


# --- OpenRouter and OpenAI ---------------------------------------------------------------------------------------------

class TestOpenRouter:

    def test_an_anthropic_upstream_gets_the_cache_points_as_openrouter_documents(self, make_client):
        usage = {"prompt_tokens": 1200, "completion_tokens": 3, "total_tokens": 1203,
                 "prompt_tokens_details": {"cached_tokens": 1000, "cache_write_tokens": 150}}
        http = FakeHTTP().add(completion("ok", usage=usage))
        client = make_client(http, SECRETS, ai_provider="openrouter", ai_model="anthropic/claude-opus-5.5",
                             ai_cache_ttl="1h")
        result = client.chat(ask(effort="high"))
        body = http.last.json
        assert body["messages"][0] == {"role": "system", "content": [
            {"type": "text", "text": "You are Ditto.", "cache_control": {"type": "ephemeral", "ttl": "1h"}}]}
        assert body["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
        assert body["reasoning"]["effort"], "the reasoning object is kept beside the cache point"
        assert result.usage.cache_read_tokens == 1000 and result.usage.cache_write_tokens == 150
        assert result.usage.input_tokens == 50, "read and written tokens are taken out of prompt_tokens"

    def test_any_other_upstream_gets_nothing_extra(self, make_client):
        usage = {"prompt_tokens": 1200, "completion_tokens": 3, "total_tokens": 1203,
                 "prompt_tokens_details": {"cached_tokens": 1024}}
        http = FakeHTTP().add(completion("ok", usage=usage))
        result = make_client(http, SECRETS, ai_provider="openrouter", ai_model="deepseek/deepseek-v4").chat(ask())
        assert "cache_control" not in json.dumps(http.last.json)
        assert http.last.json["messages"][0] == {"role": "system", "content": "You are Ditto."}
        assert result.usage.cache_read_tokens == 1024 and result.usage.input_tokens == 176


def test_openai_caches_by_itself_and_its_numbers_are_read(make_client):
    http = FakeHTTP().add(responses_body([{"type": "message", "id": "m1", "role": "assistant", "status": "completed",
                                           "content": [{"type": "output_text", "text": "ok", "annotations": []}]}]))
    result = make_client(http, SECRETS, ai_provider="openai", ai_cache_ttl="1h").chat(ask())
    body = http.last.json
    assert "cache_control" not in json.dumps(body) and "prompt_cache_options" not in body, \
        "no breakpoints, and the long lifetime waits for the person's paid run"
    assert len(body["prompt_cache_key"]) == 32
    assert result.usage.cache_read_tokens == 30 and result.usage.cache_write_tokens == 4
    assert result.usage.input_tokens == 20


def test_a_local_engine_gets_nothing_cache_related(make_client):
    http = FakeHTTP().add(completion("ok"))
    make_client(http, SECRETS, ai_provider="ollama", ai_cache_ttl="1h").chat(ask())
    assert "cache_control" not in json.dumps(http.last.json)


# --- what the person and an AI see ---------------------------------------------------------------------------------------

def test_the_overview_and_get_status_show_the_way_the_settings_and_the_sums(tmp_path):
    from orchestrator.Core.ditto_context import cloud_cache_view
    from orchestrator.tools import tools_status
    config, client = _ditto(tmp_path, FakeHTTP(), ai_model="claude-opus-5-5", ai_cache_cost_limit=0.5)
    ditto = DittoCoder(None, config_manager=config, client=client)
    history = ChatHistory()
    history.cache = cc.CacheTally(calls=2, read=100, written=200_000, cost_usd=1.0).model_dump()
    view = cloud_cache_view(ditto, history, config)
    assert view.way == "breakpoints" and view.ttl == "5m" and view.ttl_applies and view.min_tokens == 512
    assert view.cap_reached and view.totals.read == 100
    text = "\n".join(view.lines())
    assert "Lifetime: 5m" in text and "0.50 USD" in text and "soft cap is reached" in text
    schema = tools_status.CloudCacheOut.model_json_schema()["properties"]
    assert "cloud_cache" in tools_status.AiAccessSection.model_json_schema()["properties"]
    assert {"way", "ttl", "cost_limit_usd", "read_tokens", "cap_reached"} <= set(schema)
    assert "ai_cache_ttl" not in tools_status.WRITABLE and "ai_cache_cost_limit" not in tools_status.WRITABLE, \
        "an AI reads the facts and changes neither setting"


def test_config_set_takes_and_checks_the_cloud_cache_settings(tmp_path):
    home = tmp_path / "home"
    env = {**os.environ, "PYTHONUTF8": "1", "LLM_FRAMEWORK_HOME": str(home), "HOME": str(home),
           "USERPROFILE": str(home), "PYTHONPATH": str(ROOT)}

    def run(*args):
        return subprocess.run([sys.executable, "-m", "orchestrator.cli", *args], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=180)
    done = run("config", "set", "ai_cache_ttl", "1h")
    assert done.returncode == 0 and "ai_cache_ttl: 1h" in done.stdout, done.stdout + done.stderr
    assert run("config", "set", "ai_cache_cost_limit", "2.5").returncode == 0
    refused = run("config", "set", "ai_cache_cost_limit", "--", "-3")
    assert refused.returncode == 2 and "between 0 and 100000" in refused.stdout
    assert run("config", "set", "ai_cache_ttl", "12h").returncode == 2
    shown = run("config", "show")
    assert "1h" in shown.stdout and "2.5" in shown.stdout, "both were saved, the refused values were not"


def test_ditto_context_shows_and_sets_the_cloud_cache(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from orchestrator import cli
    config, client = _ditto(tmp_path, FakeHTTP(), ai_model="claude-opus-5-5")
    config.set = lambda k, v: config.values.__setitem__(k, v)
    config.save_user_config = lambda: True
    config.user_state_dir = tmp_path / "state"
    ditto = DittoCoder(None, config_manager=config, client=client)
    _fixed_window(ditto)
    history = ChatHistory(tmp_path / "state" / ChatHistory.FILE_NAME)
    history.cache = cc.CacheTally(calls=1, read=700, written=50, uncached=10).model_dump()
    assert history.save()
    framework = SimpleNamespace(ditto_manager=ditto, config=config, resolve_path=lambda p: p)

    def require(self):
        self.framework_manager, self._initialized = framework, True
        return self
    monkeypatch.setattr(cli.FrameworkContext, "require_framework", require)
    monkeypatch.setattr(cli, "_ditto_notice_once", lambda: None)

    def invoke(*args):
        return CliRunner(env={"PYTHONUTF8": "1", "COLUMNS": "250"}).invoke(cli.cli, list(args))
    overview = invoke("ditto", "context")
    assert overview.exit_code == 0, overview.output
    assert "Cloud cache: Anthropic's prompt caching" in overview.output and "700 token(s) read" in overview.output
    done = invoke("ditto", "context", "cache", "--ttl", "1h", "--cost-limit", "2.5", "--json")
    assert done.exit_code == 0, done.output
    data = json.loads(done.stdout[done.stdout.index("{"):])
    assert data["ttl"] == "1h" and data["cost_limit"] == 2.5 and data["totals"]["read"] == 700
    assert config.values["ai_cache_ttl"] == "1h"
    assert invoke("ditto", "context", "cache", "--ttl", "2h").exit_code == 2


def test_the_cloud_cache_box_shows_and_saves(qapp, tmp_path):
    from orchestrator.gui.cloud_cache_box import CloudCacheBox
    config, client = _ditto(tmp_path, FakeHTTP(), ai_model="claude-opus-5-5")
    config.set = lambda k, v: config.values.__setitem__(k, v)
    config.save_user_config = lambda: True
    ditto = DittoCoder(None, config_manager=config, client=client)
    history = ChatHistory()
    history.cache = cc.CacheTally(calls=1, read=700, written=50, uncached=10).model_dump()
    framework = SimpleNamespace(config=config, ditto_manager=ditto)
    box = CloudCacheBox(framework, history_factory=lambda: history, lang="en")
    try:
        assert box.cb_ttl.currentData() == "5m" and box.spn_limit.value() == 1.0
        assert "cache points on the system text" in box.lbl_info.text() and "700 tokens read" in box.lbl_info.text()
        box.cb_ttl.setCurrentIndex(box.cb_ttl.findData("1h"))
        box.spn_limit.setValue(4.0)
        assert box.apply()
        assert config.values["ai_cache_ttl"] == "1h" and config.values["ai_cache_cost_limit"] == 4.0
    finally:
        box.deleteLater()


def test_the_settings_window_holds_the_cloud_cache_box_and_saves_it(qapp, booted_framework):
    from orchestrator.gui.settings_window import SettingsDialog
    dialog = SettingsDialog(booted_framework, lang="en")
    try:
        dialog.show_page("language_model", "context")
        box = dialog.cloud_cache_box
        assert box.objectName() == "grp_cloud_cache" and box.cb_ttl.currentData() == "5m"
        box.cb_ttl.setCurrentIndex(box.cb_ttl.findData("1h"))
        box.spn_limit.setValue(3.0)
        box.btn_apply.click()
        assert booted_framework.config.get("ai_cache_ttl") == "1h"
        assert booted_framework.config.get("ai_cache_cost_limit") == 3.0
        saved = (Path(booted_framework.config.config_dir) / "config.yml").read_text(encoding="utf-8")
        assert "ai_cache_ttl: 1h" in saved and "ai_cache_cost_limit: 3.0" in saved
    finally:
        dialog.close()
        dialog.deleteLater()

