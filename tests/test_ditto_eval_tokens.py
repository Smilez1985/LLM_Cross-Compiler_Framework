"""Token and cache measurement of the Ditto evaluation (ctx-measure): the `tokens` case group (per-turn numbers from
the provider's Usage only, the cache-read share), the expected-fail cases (an unexpected pass is reported), the pause
between turns (never in CI), Anthropic's cache diagnostics, and the paid OpenAI test `openai_paid` (never by default,
never in CI or offline, only with the person's yes; a 400 is never read as "works"; the result file holds only
booleans and token numbers). Every provider here is a fake: no test makes a real or a paid call."""

import ipaddress
import json
import re
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

httpx2 = pytest.importorskip("httpx2")
pytest.importorskip("anthropic")
pytest.importorskip("openai")

from conftest import REPO_ROOT  # noqa: E402
from orchestrator.Core.ditto_manager import DittoCoder  # noqa: E402
from orchestrator.evals import ditto_eval, eval_run  # noqa: E402
from orchestrator.evals.ditto_eval import DittoEval, load_cases  # noqa: E402
from orchestrator.llm import ChatRequest, ChatResult, LLMClient, Message, NativeMessage  # noqa: E402
from orchestrator.llm.providers.anthropic import AnthropicAdapter  # noqa: E402
from orchestrator.llm.providers.base import ProviderAdapter  # noqa: E402
from orchestrator.llm.providers.openai_responses import OpenAIResponsesAdapter  # noqa: E402
from orchestrator.llm.types import CacheProbe, Usage  # noqa: E402
from test_llm import SECRETS, FakeHTTP, claude_message, responses_body, text_block  # noqa: E402

TARGETS = REPO_ROOT / "targets"
DOC_NETWORKS = [ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24",
                                                     "2001:db8::/32")]
OPENAI_KEY = SECRETS["openai_api_key"]


class FakeConfig:
    def __init__(self, **values):
        self.values = {"ai_security_level": "STRICT", "ai_provider": "ollama", **values}
        self.config_dir = None

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value
        return True


def reply(text, usage=None, response_id=""):
    msg = Message(role="assistant", content=text,
                  native=NativeMessage(family="scripted", payload={"role": "assistant", "content": text}))
    return ChatResult(text=text, stop="end", message=msg, provider="ollama", model="test-model",
                      usage=usage or Usage(), response_id=response_id)


@pytest.fixture
def llm(monkeypatch):
    script, requests = [], []

    class ScriptedAdapter(ProviderAdapter):
        family = "scripted"

        def _complete(self, request, model):
            requests.append(request)
            if not script:
                raise AssertionError("unexpected model call")
            item = script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    monkeypatch.setattr("orchestrator.llm.client.adapter_class", lambda name: ScriptedAdapter)
    return SimpleNamespace(script=script, requests=requests)


def make_eval(tmp_path, config=None, **kw):
    config = config or FakeConfig()
    client = LLMClient(config, SimpleNamespace(get_secret=lambda name: None), state_dir=tmp_path / "state",
                       sleep=lambda s: None)
    return DittoEval(DittoCoder(None, config_manager=config, client=client), TARGETS, **kw)


def case(case_id):
    return next(c for c in load_cases() if c.id == case_id)


# --- the shipped cases ------------------------------------------------------------------------------------------------

def test_the_new_kinds_exist_and_their_cases_are_data():
    assert {"tokens", "openai_paid"} <= set(ditto_eval.KINDS)
    kinds = {c.kind for c in load_cases()}
    assert {"tokens", "openai_paid"} <= kinds
    assert {c.check for c in load_cases() if c.kind == "openai_paid"} == {"cache_ttl", "count"}


def test_the_measurement_cases_use_example_data_only():
    """Only documentation addresses and example names: nothing real reaches a cloud model."""
    for c in load_cases():
        if c.kind not in ("tokens", "openai_paid"):
            continue
        text = " ".join([*c.turns, c.prefix, c.question])
        for found in re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text):
            assert any(ipaddress.ip_address(found) in n for n in DOC_NETWORKS), (c.id, found)
        for host in re.findall(r"\b[\w-]+(?:\.[\w-]+)*\.(?:com|org|net|io|de)\b", text):
            assert host.endswith(("example.com", "example.org")), (c.id, host)
        assert "/home/" not in text and "C:\\Users" not in text


def test_the_two_cases_that_fail_today_are_marked_expected_fail():
    compaction = case("tokens-followup-after-compaction")
    window = case("tokens-20-turns-local-4096")
    assert compaction.expected_fail and window.expected_fail
    assert window.needs_local and window.context_limit == 4096 and len(window.turns) == 20
    assert compaction.context_limit and len(compaction.turns) >= 3


# --- which cases a run takes ------------------------------------------------------------------------------------------

def test_measurement_cases_never_run_by_default():
    default = {c.kind for c in eval_run.select_cases()}
    assert "tokens" not in default and "openai_paid" not in default and default


def test_tokens_by_kind_or_case_id_but_the_paid_test_only_by_its_kind():
    assert {c.kind for c in eval_run.select_cases(kinds=("tokens",))} == {"tokens"}
    assert [c.id for c in eval_run.select_cases(case_ids=("tokens-build-talk",))] == ["tokens-build-talk"]
    with pytest.raises(eval_run.EvalRunError):   # a case id alone never starts a paid call
        eval_run.select_cases(case_ids=("openai-count-tokens",))
    assert {c.kind for c in eval_run.select_cases(kinds=("openai_paid",))} == {"openai_paid"}
    with pytest.raises(eval_run.EvalRunError) as info:
        eval_run.select_cases(kinds=("openai_paid", "chat"))
    assert info.value.code == "paid_not_alone"


# --- tokens: per-turn numbers from Usage ------------------------------------------------------------------------------

def test_a_tokens_case_records_each_turn_from_usage_and_the_cache_read_share(tmp_path, llm):
    llm.script += [reply("ok", Usage(input_tokens=900, output_tokens=5, cache_write_tokens=100)),
                   reply("ok", Usage(input_tokens=40, output_tokens=6, cache_read_tokens=960)),
                   reply("board-c.example.com", Usage(input_tokens=50, output_tokens=7, cache_read_tokens=1000)),
                   reply("203.0.113.30", Usage(input_tokens=60, output_tokens=8, cache_read_tokens=1050))]
    attempt = make_eval(tmp_path).run_case(case("tokens-boards-on-the-network"))
    assert attempt.passed, attempt
    assert [t["input_tokens"] for t in attempt.turns] == [900, 40, 50, 60]
    assert [t["cache_read_tokens"] for t in attempt.turns] == [0, 960, 1000, 1050]
    assert attempt.turns[0]["cache_write_tokens"] == 100 and all(t["calls"] == 1 for t in attempt.turns)
    conv = attempt.conversation
    assert conv["turns"] == 4 and conv["cache_read_tokens"] == 3010 and conv["output_tokens"] == 26
    assert conv["cache_read_share"] == round(3010 / (1050 + 3010 + 100), 4)
    # one history: the fourth request carries the three earlier exchanges
    assert len(llm.requests[3].messages) == 7
    report = ditto_eval.EvalReport([ditto_eval.CaseResult(case("tokens-boards-on-the-network"), [attempt])])
    data = report.to_dict()
    assert data["tokens"][0]["cache_read_share"] == conv["cache_read_share"]
    columns, rows = eval_run.tokens_table([report])
    assert columns[-1] == "Cache-read share" and rows[0][1] == "tokens-boards-on-the-network"


def test_an_error_in_a_turn_fails_the_conversation_with_its_turn(tmp_path, llm):
    from orchestrator.llm import BadRequest
    llm.script += [reply("ok", Usage(input_tokens=10)), reply("ok", Usage(input_tokens=10)),
                   BadRequest("the request exceeds the available context size, context shift is disabled")]
    attempt = make_eval(tmp_path).run_case(case("tokens-boards-on-the-network"))
    assert not attempt.passed and attempt.error.startswith("turn 3:")
    assert attempt.turns[-1]["error"] is True and attempt.conversation["turns"] == 3


def test_a_case_that_needs_a_local_model_is_skipped_on_another(tmp_path, llm, monkeypatch):
    ev = make_eval(tmp_path)
    monkeypatch.setattr(ev, "_runs_local", lambda: False)
    attempt = ev.run_case(case("tokens-20-turns-local-4096"))
    assert attempt.skipped and not llm.requests
    result = ditto_eval.CaseResult(case("tokens-20-turns-local-4096"), [attempt])
    assert result.outcome == "skipped" and not result.graded


# --- expected-fail cases ----------------------------------------------------------------------------------------------

def _result(case_id, passed):
    return ditto_eval.CaseResult(case(case_id), [ditto_eval.Attempt(passed, 1.0)])


def test_an_expected_failure_is_not_counted_and_an_unexpected_pass_is_reported():
    normal = _result("tokens-build-talk", True)
    xfail = _result("tokens-followup-after-compaction", False)
    report = ditto_eval.EvalReport([normal, xfail])
    assert xfail.outcome == "xfail" and report.pass_rate == 1.0
    assert report.to_dict()["expected_failures"] == {"xfail": ["tokens-followup-after-compaction"],
                                                     "unexpected_pass": []}
    xpass = _result("tokens-followup-after-compaction", True)
    report = ditto_eval.EvalReport([normal, xpass])
    assert xpass.outcome == "xpass"
    data = report.to_dict()
    assert data["expected_failures"]["unexpected_pass"] == ["tokens-followup-after-compaction"]
    assert data["cases"][1]["outcome"] == "xpass" and data["cases"][1]["expected_fail"] is True
    assert "UNEXPECTED PASS" in eval_run.outcome_note(xpass)


def test_the_report_of_ordinary_cases_keeps_its_format():
    data = ditto_eval.EvalReport([_result("tokens-build-talk", True)]).to_dict()
    assert "outcome" not in data["cases"][0] and "expected_failures" not in data and "skipped" not in json.dumps(data)


def test_the_compaction_case_fails_today_for_the_right_reason(tmp_path, llm):
    """The summary of the long conversation drops the last answer: the follow-up cannot name the code word."""
    c = case("tokens-followup-after-compaction")
    llm.script += [reply("A summary of the log."), reply("The code word is AMBER-7."),
                   reply("They talked about a build log."),       # the summary request (compaction)
                   reply("I do not know the code word.")]
    attempt = make_eval(tmp_path).run_case(c)
    assert attempt.turns[2].get("new_conversation") is True and attempt.turns[2]["calls"] == 2
    assert not attempt.passed and ditto_eval.CaseResult(c, [attempt]).outcome == "xfail"


# --- the pause between turns ------------------------------------------------------------------------------------------

def test_the_pause_waits_between_turns_only(tmp_path, llm):
    waits = []
    llm.script += [reply("ok"), reply("ok"), reply("board-c"), reply("203.0.113.30")]
    attempt = make_eval(tmp_path, pause_minutes=5, wait=waits.append).run_case(case("tokens-boards-on-the-network"))
    assert attempt.passed and waits == [300.0, 300.0, 300.0]


def test_cancel_ends_a_pause(tmp_path, llm):
    stop = threading.Event()
    llm.script += [reply("ok")]
    ev = make_eval(tmp_path, pause_minutes=60, stop=stop, wait=lambda s: stop.set())
    with pytest.raises(ditto_eval.EvalCancelled):
        ev.run_case(case("tokens-boards-on-the-network"))


@pytest.mark.parametrize("variable", ["CI", "GITHUB_ACTIONS"])
def test_no_pause_in_ci(monkeypatch, variable):
    monkeypatch.setenv(variable, "true")
    with pytest.raises(eval_run.EvalRunError) as info:
        eval_run.run_evaluation(SimpleNamespace(config=FakeConfig()), [case("tokens-build-talk")],
                                make_ditto=lambda: None, pause_minutes=5)
    assert info.value.code == "pause_in_ci"


def test_no_pause_by_default(tmp_path):
    assert make_eval(tmp_path).pause_minutes is None


# --- Anthropic's cache diagnostics ------------------------------------------------------------------------------------

def _anthropic(http):
    from orchestrator.llm import load_catalog
    preset = load_catalog().get("anthropic")
    return AnthropicAdapter(preset, api_key=SECRETS["anthropic_api_key"], http_client=http.client())


def test_diagnostics_are_sent_only_with_an_evaluation_probe():
    from orchestrator.llm import load_catalog
    adapter = AnthropicAdapter(load_catalog().get("anthropic"), api_key=SECRETS["anthropic_api_key"])
    plain = adapter.build_params(ChatRequest(messages=[Message.user("Hi")]), "claude-test", None)
    assert "diagnostics" not in plain
    probed = adapter.build_params(ChatRequest(messages=[Message.user("Hi")], cache_probe=CacheProbe(
        anthropic_diagnostics=True, previous_message_id="msg_01")), "claude-test", None)
    assert probed["diagnostics"] == {"previous_message_id": "msg_01"}


def test_the_cache_miss_reason_comes_back_per_call():
    http = FakeHTTP()
    body = claude_message([text_block("Hi")])
    body["id"] = "msg_02"
    body["diagnostics"] = {"cache_miss_reason": {"type": "system_changed", "cache_missed_input_tokens": 1200}}
    http.add(body).add(claude_message([text_block("Hi")]))
    adapter = _anthropic(http)
    probe = CacheProbe(anthropic_diagnostics=True, previous_message_id="msg_01")
    result = adapter.chat(ChatRequest(messages=[Message.user("Hi")], cache_probe=probe), "claude-test")
    assert http.last.json["diagnostics"] == {"previous_message_id": "msg_01"}
    assert result.response_id == "msg_02" and result.cache_miss_reason == "system_changed"
    assert result.cache_missed_tokens == 1200
    again = adapter.chat(ChatRequest(messages=[Message.user("Hi")]), "claude-test")
    assert again.cache_miss_reason is None and "diagnostics" not in http.last.json


def test_the_probing_client_chains_the_previous_response_id():
    seen = []

    class Client:
        def chat(self, request, *, fallback=True):
            assert fallback is False   # a measurement stays on the measured provider
            seen.append(request.cache_probe)
            return reply("ok", Usage(input_tokens=3), response_id=f"msg_{len(seen)}")

    probing = ditto_eval.ProbingClient(Client())
    for _ in range(3):
        probing.chat(ChatRequest(messages=[Message.user("Hi")]))
    assert [p.previous_message_id for p in seen] == [None, "msg_1", "msg_2"]
    assert all(p.anthropic_diagnostics for p in seen) and len(probing.calls) == 3


# --- the paid OpenAI test: who may start it ---------------------------------------------------------------------------

PAID = [c for c in load_cases() if c.kind == "openai_paid"]


def _framework(**values):
    return SimpleNamespace(config=FakeConfig(**values))


def test_the_paid_test_needs_the_persons_yes(monkeypatch):
    for name in eval_run.CI_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(eval_run.EvalRunError) as info:
        eval_run.check_paid(_framework(), PAID, provider="openai")
    assert info.value.code == "paid_not_confirmed"
    eval_run.check_paid(_framework(), PAID, provider="openai", i_pay=True)   # the person's yes: allowed
    with pytest.raises(eval_run.EvalRunError) as info:
        eval_run.run_evaluation(_framework(), PAID, make_ditto=lambda: pytest.fail("no call"), provider="openai")
    assert info.value.code == "paid_not_confirmed"


def test_the_paid_test_needs_openai(monkeypatch):
    for name in eval_run.CI_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    for provider, tiers in (("anthropic", ()), (None, ()), ("openai", ("small",))):
        with pytest.raises(eval_run.EvalRunError) as info:
            eval_run.check_paid(_framework(), PAID, provider=provider, tiers=tiers, i_pay=True)
        assert info.value.code == "paid_needs_openai"


def test_the_paid_test_never_runs_in_ci(monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(eval_run.EvalRunError) as info:
        eval_run.check_paid(_framework(), PAID, provider="openai", i_pay=True)
    assert info.value.code == "paid_in_ci"


def test_the_paid_test_never_runs_offline(monkeypatch):
    for name in eval_run.CI_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(eval_run.EvalRunError) as info:
        eval_run.check_paid(_framework(offline_mode=True), PAID, provider="openai", i_pay=True)
    assert info.value.code == "offline"


def test_no_tool_and_no_ai_path_starts_an_evaluation():
    """The evaluation is no MCP or Ditto tool: an AI caller cannot start it (only the CLI and the window do)."""
    package = REPO_ROOT / "orchestrator"
    for path in [*package.joinpath("tools").rglob("*.py"), package / "mcp_server.py",
                 package / "Core" / "ditto_manager.py"]:
        text = path.read_text(encoding="utf-8")
        assert "run_evaluation" not in text and "eval_run" not in text, path


def test_the_cli_refuses_the_paid_test_without_i_pay_or_without_a_terminal(monkeypatch):
    from click.testing import CliRunner

    from orchestrator import cli
    for name in eval_run.CI_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    framework = _framework()
    monkeypatch.setattr(cli.FrameworkContext, "require_framework",
                        lambda self: setattr(self, "framework_manager", framework) or self)
    monkeypatch.setattr(eval_run, "run_evaluation", lambda *a, **k: pytest.fail("no run"))
    result = CliRunner().invoke(cli.cli, ["-q", "ditto", "eval", "--kind", "openai_paid", "--provider", "openai"])
    assert result.exit_code == 2 and "--i-pay" in result.output
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    result = CliRunner().invoke(cli.cli, ["-q", "ditto", "eval", "--kind", "openai_paid", "--provider", "openai",
                                          "--i-pay"])
    assert result.exit_code == 2 and "terminal" in result.output


def test_the_cli_asks_with_no_as_the_default(monkeypatch):
    from click.testing import CliRunner

    from orchestrator import cli
    for name in eval_run.CI_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    framework = _framework()
    monkeypatch.setattr(cli.FrameworkContext, "require_framework",
                        lambda self: setattr(self, "framework_manager", framework) or self)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr(eval_run, "run_evaluation", lambda *a, **k: pytest.fail("no run"))
    result = CliRunner().invoke(cli.cli, ["-q", "ditto", "eval", "--kind", "openai_paid", "--provider", "openai",
                                          "--i-pay"], input="\n")
    assert result.exit_code == 1 and "Not started" in result.output
    cost = eval_run.paid_estimate(PAID)
    assert f"at most {cost['calls']} calls" in result.output and cost["calls"] == 6


# --- the paid OpenAI test: what it measures (a fake OpenAI server) ----------------------------------------------------

def _final(text="ok"):
    return {"type": "message", "id": "m", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}]}


def _body(cached=0, written=0, total=2100):
    body = responses_body([_final()])
    body["usage"]["input_tokens"] = total
    body["usage"]["input_tokens_details"] = {"cached_tokens": cached, "cache_write_tokens": written}
    return body


def _openai_eval(tmp_path, http):
    config = FakeConfig(ai_provider="openai")
    client = LLMClient(config, SimpleNamespace(get_secret=SECRETS.get), state_dir=tmp_path / "state",
                       http_client=http.client(), sleep=lambda s: None)
    return DittoEval(DittoCoder(None, config_manager=config, client=client), TARGETS)


def test_a_refused_12h_ttl_is_recorded_as_refused_and_the_pair_is_measured_without_it(tmp_path):
    error = {"error": {"message": "Invalid value for prompt_cache_options.ttl", "type": "invalid_request_error",
                       "param": "prompt_cache_options.ttl", "code": None}}
    http = FakeHTTP().add(error, status=400).add(_body(written=2048)).add(_body(cached=2048))
    attempt = _openai_eval(tmp_path, http).run_case(case("openai-cache-ttl-12h"))
    assert attempt.passed, attempt
    m = attempt.measurement
    assert m["ttl_12h_accepted"] is False and m["ttl_12h_status"] == 400 and m["ttl_12h_invalid_request"] is True
    assert m["pair_sent_with_ttl"] is False and m["turn2_cached"] is True
    assert m["turn2"]["cache_read_tokens"] == 2048 and m["turn1"]["cache_write_tokens"] == 2048
    first, second, third = http.requests
    assert first.json["prompt_cache_options"] == {"ttl": "12h"}
    assert "prompt_cache_options" not in second.json and "prompt_cache_options" not in third.json


def test_an_accepted_12h_ttl_is_sent_on_both_calls(tmp_path):
    http = FakeHTTP().add(_body(written=2048)).add(_body(cached=2048))
    attempt = _openai_eval(tmp_path, http).run_case(case("openai-cache-ttl-12h"))
    m = attempt.measurement
    assert m["ttl_12h_accepted"] is True and m["pair_sent_with_ttl"] is True and m["turn2_cached"] is True
    assert all(r.json["prompt_cache_options"] == {"ttl": "12h"} for r in http.requests)


def test_the_count_goes_through_the_privacy_filter_and_is_compared_with_usage(tmp_path):
    http = FakeHTTP().add({"object": "response.input_tokens", "input_tokens": 2090}).add(_body(cached=0, total=2100))
    attempt = _openai_eval(tmp_path, http).run_case(case("openai-count-tokens"))
    assert attempt.passed, attempt
    count, call = http.requests
    assert count.url.endswith("/responses/input_tokens") and call.url.endswith("/responses")
    # the same sanitized request: the documentation address is masked in both (privacy filter on)
    assert "192.0.2.10" not in json.dumps(count.json) and "192.0.2.10" not in json.dumps(call.json)
    assert count.json["input"] == call.json["input"]
    m = attempt.measurement
    assert m["count_tokens"] == 2090 and m["usage_input_tokens"] == 2100 and m["count_difference"] == -10
    assert m["count_matches_usage"] is False and m["count_response_has_cost_field"] is False


def test_the_paid_test_never_falls_back_to_another_provider(tmp_path):
    """The person's yes covers OpenAI calls only: an OpenAI outage ends the check, it never sends the paid prompt to
    the next provider of ai_fallback_chain (another bill, another provider)."""
    outage = {"error": {"message": "overloaded", "type": "server_error", "param": None, "code": None}}
    http = FakeHTTP()
    for _ in range(10):
        http.add(outage, status=503)
    config = FakeConfig(ai_provider="openai", ai_fallback_chain=["anthropic"])
    client = LLMClient(config, SimpleNamespace(get_secret=SECRETS.get), state_dir=tmp_path / "state",
                       http_client=http.client(), sleep=lambda s: None)
    ev = DittoEval(DittoCoder(None, config_manager=config, client=client), TARGETS)
    for case_id in ("openai-cache-ttl-12h", "openai-count-tokens"):
        attempt = ev.run_case(case(case_id))
        assert not attempt.passed
    assert http.requests and all("api.openai.com" in r.url for r in http.requests), \
        sorted({r.url for r in http.requests})


def test_a_tokens_turn_is_measured_on_the_chosen_provider_only(tmp_path, llm):
    """Numbers of a measurement belong to the provider it runs on: an outage fails the turn instead of answering
    from the fallback chain (whose Usage would be reported as the measured provider's)."""
    from orchestrator.llm import ProviderUnavailable
    config = FakeConfig(ai_fallback_chain=["ollama:other-model"])
    llm.script += [ProviderUnavailable("down", retry_after=999), reply("from the fallback", Usage(input_tokens=5))]
    attempt = make_eval(tmp_path, config).run_case(case("tokens-boards-on-the-network"))
    assert not attempt.passed and attempt.turns[0]["calls"] == 0
    assert len(llm.requests) == 1


def test_the_result_file_holds_booleans_and_numbers_only(tmp_path):
    http = FakeHTTP().add(_body(written=2048)).add(_body(cached=2048)) \
        .add({"object": "response.input_tokens", "input_tokens": 2100}).add(_body())
    ev = _openai_eval(tmp_path, http)
    report = ev.run([c for c in PAID])
    path = eval_run.save_openai_check(tmp_path / "evals" / eval_run.OPENAI_CHECK_FILE, [report])
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    assert data["cache_ttl"]["ttl_12h_accepted"] is True and data["count"]["count_tokens"] == 2100

    def leaves(value):
        if isinstance(value, dict):
            for v in value.values():
                yield from leaves(v)
        else:
            yield value
    assert all(isinstance(v, (bool, int, float)) or v is None for v in leaves(data))
    for secret in (OPENAI_KEY, "example framework", "board-a", "192.0.2.10", "Reply with one word"):
        assert secret not in text


def test_no_file_without_a_finished_measurement(tmp_path):
    report = ditto_eval.EvalReport([ditto_eval.CaseResult(PAID[0], [ditto_eval.Attempt(False, 0.0, error="x")])])
    assert eval_run.save_openai_check(tmp_path / "x.json", [report]) is None and not (tmp_path / "x.json").exists()


def test_the_check_file_lives_in_the_per_user_state_folder(tmp_path):
    config = SimpleNamespace(user_state_dir=tmp_path)
    assert eval_run.openai_check_path(config) == tmp_path / "evals" / "openai_cache_check.json"


def test_another_provider_is_refused_without_a_call(tmp_path, llm):
    attempt = make_eval(tmp_path).run_case(case("openai-count-tokens"))
    assert not attempt.passed and "--provider openai" in attempt.error and not llm.requests


def test_the_openai_count_is_only_on_the_responses_adapter(tmp_path):
    from orchestrator.llm import NotConfigured
    client = LLMClient(FakeConfig(ai_provider="anthropic"), SimpleNamespace(get_secret=SECRETS.get),
                       state_dir=tmp_path / "state", sleep=lambda s: None)
    with pytest.raises(NotConfigured):
        client.count_input_tokens(ChatRequest(messages=[Message.user("Hi")]))
    assert hasattr(OpenAIResponsesAdapter, "count_input_tokens")


def test_without_a_probe_no_prompt_cache_options_are_sent(tmp_path):
    http = FakeHTTP().add(_body())
    client = LLMClient(FakeConfig(ai_provider="openai"), SimpleNamespace(get_secret=SECRETS.get),
                       state_dir=tmp_path / "state", http_client=http.client(), sleep=lambda s: None)
    client.chat(ChatRequest(messages=[Message.user("Hi")]))
    assert "prompt_cache_options" not in http.last.json


# --- the RAM measurement script ---------------------------------------------------------------------------------------

def _script():
    import importlib.util
    spec = importlib.util.spec_from_file_location("measure_cache_ram", REPO_ROOT / "scripts" / "measure_cache_ram.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_sampler_reads_only_its_own_server_child(monkeypatch):
    script = _script()
    procs = {101: SimpleNamespace(ppid=lambda: 7, memory_info=lambda: SimpleNamespace(rss=900 * 2 ** 20),
                                  cmdline=lambda: ["llama-server", "-c", "4096", "--cache-ram", "2048"]),
             202: SimpleNamespace(ppid=lambda: 1, memory_info=lambda: SimpleNamespace(rss=5000 * 2 ** 20),
                                  cmdline=lambda: ["llama-server", "--cache-ram", "1"])}
    pids = iter([None, 202, 101, 101])   # no server yet, a foreign server, then the run's own child twice

    def endpoint():
        pid = next(pids)
        return SimpleNamespace(pid=pid) if pid else None
    rt = SimpleNamespace(endpoint=endpoint)
    sampler = script.RssSampler(rt, own_pid=7, process=lambda pid: procs[pid])
    for _ in range(4):
        sampler.sample()
    assert sampler.peak_mib == 900 and sampler.cache_ram_mib == 2048 and sampler.samples == 2
    assert script.numbers(sampler, []) == {"peak_rss_mib": 900, "cache_ram_mib": 2048, "samples": 2,
                                           "conversations": []}


# --- ctx-measure follow-ups: the summary call, the reason of an expected failure, the merged check file -------------

def test_the_summary_request_neither_takes_nor_moves_the_chain():
    """Regression: Ditto's compaction summary is a separate request; it was chained to the last turn's response and
    moved the chain, so the next real turn was compared with the summary instead of the turn before it."""
    from orchestrator.llm.task_prompts import SUMMARY_SYSTEM
    seen = []

    class Client:
        def chat(self, request, *, fallback=True):
            seen.append(request.cache_probe)
            return reply("ok", Usage(input_tokens=3), response_id=f"msg_{len(seen)}")

    probing = ditto_eval.ProbingClient(Client())
    probing.chat(ChatRequest(messages=[Message.user("turn 1")]))
    probing.chat(ChatRequest(system=SUMMARY_SYSTEM, messages=[Message.user("Summarize this conversation:")]))
    probing.chat(ChatRequest(messages=[Message.user("turn 2")]))
    assert [p.previous_message_id for p in seen] == [None, None, "msg_1"]
    assert len(probing.calls) == 3, "the summary's tokens are still counted"


def test_every_expected_failure_names_its_reason():
    ValidationError = ValueError   # pydantic's ValidationError is one; model_post_init raises a plain one
    reasons = {c.id: c.expected_fail_reason for c in load_cases() if c.expected_fail}
    assert reasons == {"tokens-followup-after-compaction": "answer_lost_after_summary",
                       "tokens-20-turns-local-4096": "context_shift_disabled"}
    assert set(reasons.values()) <= set(ditto_eval.EXPECTED_FAIL_REASONS)
    base = dict(id="x", kind="tokens", turns=["hi"])
    with pytest.raises(ValidationError):
        ditto_eval.Case(**base, expected_fail=True)
    with pytest.raises(ValidationError):
        ditto_eval.Case(**base, expected_fail=True, expected_fail_reason="something_else")
    with pytest.raises(ValidationError):
        ditto_eval.Case(**base, expected_fail_reason="context_shift_disabled")


def test_a_case_that_fails_for_another_reason_is_a_failure_not_xfail():
    """Regression: any failure of an expected_fail case counted as the expected one, so a new breakage hid behind it."""
    compaction, window = case("tokens-followup-after-compaction"), case("tokens-20-turns-local-4096")
    lost = ditto_eval.Attempt(False, 1.0, ["the answer mentions none of: AMBER-7"])
    down = ditto_eval.Attempt(False, 1.0, error="turn 1: Ditto error: the provider cannot be reached")
    shift = ditto_eval.Attempt(False, 1.0, error="turn 9: Ditto error: the request exceeds the available context "
                                                 "size, context shift is disabled")
    assert ditto_eval.CaseResult(compaction, [lost]).outcome == "xfail"
    other = ditto_eval.CaseResult(compaction, [down])
    assert other.outcome == "fail" and other.graded
    assert ditto_eval.CaseResult(window, [shift]).outcome == "xfail"
    assert ditto_eval.CaseResult(window, [lost]).outcome == "fail"
    report = ditto_eval.EvalReport([other, _result("tokens-build-talk", True)])
    assert report.pass_rate == 0.5, "counted like any other failure"
    data = report.to_dict()
    assert data["expected_failures"]["failed_otherwise"] == ["tokens-followup-after-compaction"]
    assert data["cases"][0]["expected_fail_reason"] == "answer_lost_after_summary"


def _paid_report(**measurements):
    results = []
    for c in PAID:
        m = measurements.get(c.check)
        attempt = ditto_eval.Attempt(m is not None, 1.0, measurement={"check": c.check, **m} if m else None)
        results.append(ditto_eval.CaseResult(c, [attempt]))
    return ditto_eval.EvalReport(results)


def test_the_check_file_is_merged_per_check(tmp_path):
    """Regression: a run that measured one check rewrote the file and dropped the other check's earlier result."""
    path = tmp_path / "evals" / eval_run.OPENAI_CHECK_FILE
    eval_run.save_openai_check(path, [_paid_report(cache_ttl={"ttl_12h_accepted": True}, count={"count_tokens": 10})])
    eval_run.save_openai_check(path, [_paid_report(count={"count_tokens": 12})])
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["cache_ttl"] == {"ttl_12h_accepted": True}, "the complete earlier result stays"
    assert data["count"] == {"count_tokens": 12}, "the new measurement of that check replaces its old one"
    before = path.read_text(encoding="utf-8")
    assert eval_run.save_openai_check(path, [_paid_report()]) is None
    assert path.read_text(encoding="utf-8") == before, "a run without a finished measurement changes nothing"


def test_the_script_is_a_development_tool_only():
    text = (REPO_ROOT / "scripts" / "measure_cache_ram.py").read_text(encoding="utf-8")
    assert "run_evaluation" in text and "psutil" in text
    assert "Popen" not in text and "llama-server\"," not in text   # the launcher starts the server, no own argv
    assert Path(REPO_ROOT / "scripts" / "measure_cache_ram.py").exists()
