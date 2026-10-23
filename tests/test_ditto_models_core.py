"""The core functions behind `llm-cli ditto models list/download`, `ditto ask --provider/--model` and `ditto eval`
that the GUI's Ditto > Local Models..., the chat panel's "For this question" choice and Ditto > Evaluation... call too
(W1'): runtime/ditto_models.py, Core/ai_override.py and evals/eval_run.py."""

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.Core import ai_override
from orchestrator.Core.ai_override import (EFFORT_KEYS, SettingsOverlay, ditto_with, override_values,
                                           provider_problem)
from orchestrator.evals import ditto_eval, eval_run
from orchestrator.evals.ditto_eval import Attempt, CaseResult, EvalCancelled, EvalReport, load_cases
from orchestrator.evals.eval_run import EvalEvents, EvalRunError
from orchestrator.runtime.catalog import ModelEntry
from orchestrator.runtime.ditto_models import gib, model_to_download, models_overview


# --- runtime/ditto_models.py ---------------------------------------------------------------------------------------

def entry(tier, **kw):
    values = {"tier": tier, "repo": "example/Model-GGUF", "revision": "c" * 40, "file": f"{tier}.gguf",
              "size": 2 * 1024 ** 3, "sha256": "1" * 64, "license": "apache-2.0"}
    values.update(kw)
    return ModelEntry.model_validate(values)


def fake_runtime(tmp_path, models, recommended="small", preferred=None, states=None):
    catalog = SimpleNamespace(models=models, model=lambda tier: next(m for m in models if m.tier == tier)
                              if any(m.tier == tier for m in models) else (_ for _ in ()).throw(
                                  KeyError(f"Unknown model tier '{tier}'. Known: {', '.join(m.tier for m in models)}")))
    states = states or {}
    return SimpleNamespace(catalog=catalog, models_dir=tmp_path / "models" / "ditto",
                           choose_tier=lambda: (catalog.model(recommended), "8 GiB RAM: the small model fits"),
                           preferred_tier=lambda: preferred,
                           model_state=lambda m: states.get(m.tier, "missing"))


def test_overview_marks_every_tier_like_the_list(tmp_path):
    models = [entry("small"), entry("default"), entry("big", on_request=True),
              entry("unpinned", sha256=None, size=None)]
    rt = fake_runtime(tmp_path, models, recommended="small", preferred="default", states={"small": "ready",
                                                                                         "default": "unverified"})
    view = models_overview(rt)
    assert [r.tier for r in view.rows] == ["small", "default", "big", "unpinned"]
    assert view.row("small").notes() == ["recommended"] and view.row("small").state == "ready"
    assert view.row("default").notes() == ["chosen"] and view.row("default").state == "unverified"
    assert view.row("big").notes() == ["on request"] and view.row("big").state == "missing"
    assert view.row("unpinned").notes() == ["not pinned"]
    assert view.recommended.tier == "small" and "small model fits" in view.why
    assert view.preferred == "default" and view.folder == tmp_path / "models" / "ditto"
    with pytest.raises(KeyError):
        view.row("nope")


def test_on_request_is_only_named_without_another_note(tmp_path):
    models = [entry("big", on_request=True)]
    view = models_overview(fake_runtime(tmp_path, models, recommended="big", preferred="big"))
    assert view.row("big").notes() == ["recommended", "chosen"]


def test_model_to_download_is_the_named_tier_or_the_recommendation(tmp_path):
    rt = fake_runtime(tmp_path, [entry("small"), entry("default")], recommended="small")
    model, why = model_to_download(rt, None)
    assert model.tier == "small" and why
    model, why = model_to_download(rt, "default")
    assert model.tier == "default" and why is None
    with pytest.raises(KeyError, match="Unknown model tier"):
        model_to_download(rt, "nope")


def test_without_a_tier_the_download_is_the_model_runtime_start_loads(tmp_path):
    """`runtime start` loads the tier chosen earlier with --model; `models download` without a tier fetched the
    recommendation instead, so the next start still failed with "not downloaded" (why None: no recommendation)."""
    rt = fake_runtime(tmp_path, [entry("small"), entry("default")], recommended="small", preferred="default")
    model, why = model_to_download(rt, None)
    assert model.tier == "default" and why is None
    assert model_to_download(rt, "small") == (rt.catalog.model("small"), None)


def test_gib_is_written_like_the_cli():
    from orchestrator import cli
    for size in (None, 0, 1, 2 * 1024 ** 3, 4_700_000_000):
        assert gib(size) == cli._gib(size)


# --- Core/ai_override.py -------------------------------------------------------------------------------------------

def test_provider_problem_names_the_known_providers_and_removed_ones():
    from orchestrator.llm import load_catalog
    catalog = load_catalog()
    assert provider_problem(catalog, None) is None
    assert provider_problem(catalog, catalog.ids()[0]) is None
    unknown = provider_problem(catalog, "no-such-provider")
    assert unknown.startswith("Unknown AI provider 'no-such-provider'. Choose one of: ")
    assert catalog.ids()[0] in unknown
    removed = provider_problem(catalog, "claude_code")
    assert removed and "llm-cli mcp config" in removed and "Unknown AI provider" not in removed


def test_override_values():
    """Changed on purpose (port of ditto-rewire-verified): the model is set for every task too (a per-task model wins
    over ai_model), and a provider named without the Ditto whose settings it overrides counts as a switch."""
    from orchestrator.llm.types import TASKS
    assert override_values() == {}
    values = override_values("ollama", "model-a")
    assert values == {"ai_provider": "ollama", "ai_model": "model-a", "ai_base_url": "", "ai_auth": "",
                      **{f"ai_model_{task}": "model-a" for task in TASKS}}
    assert override_values(model="model-b") == {"ai_model": "model-b",
                                                 **{f"ai_model_{task}": "model-b" for task in TASKS}}
    values = override_values(effort="low")
    assert set(values) == set(EFFORT_KEYS) and set(values.values()) == {"low"}
    with pytest.raises(ValueError):
        override_values(effort="max")


class TestOneRunProviderSwitch:
    """What belongs to the saved provider (its model, per-task models, server address and sign-in method) does not
    carry over to another provider chosen for one question or run: a saved Ollama or custom address must never get
    another provider's requests (local providers are not privacy-filtered), nor another provider its model."""

    @pytest.fixture
    def ditto(self, tmp_path):
        from orchestrator.Core.config_manager import ConfigManager
        from orchestrator.Core.ditto_manager import DittoCoder
        config = ConfigManager(tmp_path / "configs")
        for key, value in {"ai_provider": "custom", "ai_base_url": "http://192.168.1.30:8000/v1",
                           "ai_model": "served-model", "ai_model_chat": "served-chat-model", "ai_auth": "api_key"
                           }.items():
            config.set(key, value)
        return DittoCoder(config_manager=config, secrets_manager=SimpleNamespace(get_secret=lambda name: None))

    def test_another_provider_gets_its_own_address_and_model(self, ditto):
        from orchestrator.llm import load_catalog
        values = override_values("ollama", ditto=ditto)
        assert values["ai_base_url"] == "" and values["ai_model"] == "" and values["ai_auth"] == ""
        target = ditto_with(ditto, values).client.resolve("chat")
        assert target.preset.id == "ollama" and target.base_url == load_catalog().get("ollama").base_url
        assert "192.168.1.30" not in target.base_url and target.model != "served-chat-model"
        assert ditto.config.get("ai_base_url") == "http://192.168.1.30:8000/v1", "the saved settings stay"

    def test_the_saved_provider_keeps_its_own_settings(self, ditto):
        assert override_values("custom", ditto=ditto) == {"ai_provider": "custom"}
        assert override_values("Custom", ditto=ditto) == {"ai_provider": "Custom"}, "a spelling of the same provider"
        target = ditto_with(ditto, override_values("custom", ditto=ditto)).client.resolve("chat")
        assert target.base_url == "http://192.168.1.30:8000/v1" and target.model == "served-chat-model"

    def test_the_model_applies_to_every_task(self, ditto):
        run = ditto_with(ditto, override_values(model="other-model", ditto=ditto)).client
        assert {task: run.resolve(task).model for task in ("chat", "tools", "json", "healing")} == dict.fromkeys(
            ("chat", "tools", "json", "healing"), "other-model")

    def test_for_run_and_the_chat_panel_take_the_same_values(self, ditto, monkeypatch):
        used = []
        real = ai_override.ditto_with
        monkeypatch.setattr(ai_override, "ditto_with", lambda base, values: used.append(values) or real(base, values))
        run = ditto.for_run("ollama", None)
        assert used == [override_values("ollama", ditto=ditto)]
        assert run.client.resolve("chat").base_url != "http://192.168.1.30:8000/v1"


def test_overlay_reads_through_and_never_writes_the_real_settings(tmp_path):
    from orchestrator.Core.config_manager import ConfigManager
    config = ConfigManager(tmp_path / "configs")
    config.set("ai_provider", "openai")
    config.set("ai_model", "base-model")
    overlay = SettingsOverlay(config, {"ai_provider": "ollama"})
    assert overlay.get("ai_provider") == "ollama" and overlay.get("ai_model") == "base-model"
    assert overlay.get("missing-key", 7) == 7
    overlay.set("ai_model", "other")
    assert overlay.get("ai_model") == "other" and config.get("ai_model") == "base-model"
    assert config.get("ai_provider") == "openai"
    # LLMClient tells set values from schema defaults by config_values: the overlay's count as set
    values = overlay.config_values
    assert values["ai_provider"].value == "ollama" and values["ai_provider"].source.source_type == "override"
    assert config.config_values["ai_provider"].value == "openai"
    assert overlay.user_state_dir == config.user_state_dir          # everything else is the real settings'
    assert overlay.values == {"ai_provider": "ollama", "ai_model": "other"}


def test_overlay_without_config_values_reads_with_get():
    base = SimpleNamespace(get=lambda key, default=None: {"ai_provider": "openai"}.get(key, default))
    overlay = SettingsOverlay(base, {"ai_model": "m"})
    assert overlay.config_values is None
    assert overlay.get("ai_provider") == "openai" and overlay.get("ai_model") == "m"
    assert SettingsOverlay(None, {}).get("x", 1) == 1
    with pytest.raises(AttributeError):
        overlay._hidden


def test_ditto_with_uses_the_override_and_leaves_the_persons_ditto_alone(tmp_path):
    from orchestrator.Core.config_manager import ConfigManager
    from orchestrator.Core.ditto_manager import DittoCoder
    config = ConfigManager(tmp_path / "configs")
    config.set("ai_provider", "openai")
    framework = SimpleNamespace(config=config, secrets_manager=SimpleNamespace(get_secret=lambda name: None),
                                tool_registry="the registry")
    ditto = DittoCoder(framework)
    once = ditto_with(ditto, override_values("ollama", "model-b"))
    assert isinstance(once, DittoCoder) and once is not ditto
    assert once.framework is framework and once._registry() == "the registry"
    assert once.client._setting("ai_provider") == "ollama" and once.client._setting("ai_model") == "model-b"
    assert ditto.client._setting("ai_provider") == "openai" and config.get("ai_provider") == "openai"
    assert config.get("ai_model") != "model-b"


def test_cli_ask_uses_the_shared_check(monkeypatch):
    """`ditto ask --provider` refuses with the core's words (exit code 2) and sets nothing."""
    from click.testing import CliRunner
    from orchestrator import cli
    calls = []
    monkeypatch.setattr(ai_override, "provider_problem", lambda catalog, provider: calls.append(provider) or None)
    values, sources = {}, set()
    ditto = SimpleNamespace(client=SimpleNamespace(catalog=object()),
                            chat=lambda q, **kw: SimpleNamespace(text="ok"))
    framework = SimpleNamespace(
        config=SimpleNamespace(set=lambda k, v, source_type="override": values.update({k: v}) or sources.add(
            source_type)), get_component=lambda name: ditto)

    def require(self):
        self.framework_manager = framework
        return self
    monkeypatch.setattr(cli.FrameworkContext, "require_framework", require)
    result = CliRunner().invoke(cli.cli, ["-q", "ditto", "ask", "--provider", "p", "--model", "m", "Hi"])
    assert result.exit_code == 0, result.output
    # Changed on purpose (port of ditto-rewire-verified): the core's override values, set as one command's own.
    assert calls == ["p"] and values == override_values("p", "m") and sources == {"cli"}


# --- evals/eval_run.py ---------------------------------------------------------------------------------------------

def test_check_choice_refuses_a_tier_with_a_provider():
    eval_run.check_choice((), "openai", "m")
    eval_run.check_choice(("small",), None, None)
    with pytest.raises(EvalRunError) as caught:
        eval_run.check_choice(("small",), "openai", None)
    assert caught.value.exit_code == 2 and caught.value.code == "tier_with_provider"
    assert str(caught.value) == eval_run.TIER_CONFLICT


def test_case_files_and_selection():
    assert eval_run.case_files() == [ditto_eval.DEFAULT_CASES]
    assert eval_run.case_files(kinds=("retrieval",)) == [ditto_eval.RETRIEVAL_CASES]
    assert eval_run.case_files(kinds=("chat", "retrieval")) == [ditto_eval.DEFAULT_CASES, ditto_eval.RETRIEVAL_CASES]
    assert eval_run.case_files(case_ids=("x",)) == [ditto_eval.DEFAULT_CASES, ditto_eval.RETRIEVAL_CASES]
    assert eval_run.case_files(Path("own.yml"), ("chat",)) == [Path("own.yml")]
    healing = eval_run.select_cases(kinds=("healing",))
    assert healing and {c.kind for c in healing} == {"healing"}
    one = eval_run.select_cases(case_ids=("healing-disk-full",))
    assert [c.id for c in one] == ["healing-disk-full"]
    with pytest.raises(EvalRunError) as caught:
        eval_run.select_cases(case_ids=("no-such-case",))
    assert caught.value.exit_code == 2 and caught.value.code == "no_cases"


def test_unreadable_cases_file(tmp_path):
    broken = tmp_path / "cases.yml"
    broken.write_text("cases: []\n", encoding="utf-8")
    for call in (lambda: eval_run.select_cases(broken), lambda: eval_run.all_cases(broken)):
        with pytest.raises(EvalRunError) as caught:
            call()
        assert caught.value.exit_code == 2 and caught.value.code == "cases_unreadable"
        assert str(caught.value).startswith("Cannot read the cases: ")


def test_all_cases_offers_ditto_and_retrieval_cases():
    ids = {c.id for c in eval_run.all_cases()}
    assert {c.id for c in load_cases()} <= ids
    assert {c.id for c in load_cases(ditto_eval.RETRIEVAL_CASES)} <= ids


class Recorder(EvalEvents):
    def __init__(self):
        self.seen = []

    def busy(self, step, **values):
        self.seen.append(("busy", step, values))
        return super().busy(step, **values)

    def run_started(self, label, name, model, cases, repeat):
        self.seen.append(("run", label, name, model, cases, repeat))

    def result(self, result):
        self.seen.append(("result", result.case.id))

    def server_replaced(self, model):
        self.seen.append(("replaced", model))

    def unused_options(self):
        self.seen.append(("unused",))


def fake_ditto(answers=None):
    return SimpleNamespace(reload=lambda: None,
                           describe=lambda task: {"label": "Local", "model": f"m-{task}", "provider": "local"})


class FakeEval:
    """DittoEval stand-in: every case passes; records the calls."""
    calls = []

    def __init__(self, ditto, targets_dir):
        self.ditto = ditto

    def run(self, cases, repeat=1, on_result=None, stop=None):
        FakeEval.calls.append((list(c.id for c in cases), repeat, stop))
        return ditto_eval.run_cases(lambda case: Attempt(True, 0.5), cases, repeat, on_result, stop=stop)


@pytest.fixture
def framework(tmp_path):
    return SimpleNamespace(config=SimpleNamespace(get=lambda k, d=None: d), resolve_path=lambda p: tmp_path / str(p))


def test_run_evaluation_with_the_configured_model(monkeypatch, framework):
    monkeypatch.setattr(ditto_eval, "DittoEval", FakeEval)
    FakeEval.calls = []
    cases = eval_run.select_cases(case_ids=("healing-disk-full",))
    events, made = Recorder(), []
    runs = eval_run.run_evaluation(framework, cases, make_ditto=lambda: made.append(1) or fake_ditto(), repeat=2,
                                   effort="low", events=events)
    assert made == [1] and len(runs) == 1
    assert events.seen == [("run", "configured model", "Local", "m-chat", 1, 2), ("result", "healing-disk-full")]
    setup = runs[0].setup
    assert setup["label"] == "configured model" and setup["effort"] == "low" and setup["repeat"] == 2
    assert setup["task_models"] == {"chat": "m-chat", "json": "m-json", "healing": "m-healing"}
    assert FakeEval.calls[0][:2] == (["healing-disk-full"], 2)


def test_run_evaluation_label_names_the_chosen_provider(monkeypatch, framework):
    monkeypatch.setattr(ditto_eval, "DittoEval", FakeEval)
    cases = eval_run.select_cases(case_ids=("healing-disk-full",))
    runs = eval_run.run_evaluation(framework, cases, make_ditto=fake_ditto, provider="ollama", model="model-b")
    assert runs[0].setup["label"] == "ollama model-b" and runs[0].setup["effort"] == "settings"


def test_a_ditto_that_is_not_set_up_stops_the_run(monkeypatch, framework):
    from orchestrator.Core.ditto_manager import DittoError

    def describe(task):
        raise DittoError("No AI provider is set up.")
    cases = eval_run.select_cases(case_ids=("healing-disk-full",))
    with pytest.raises(DittoError):
        eval_run.run_evaluation(framework, cases, make_ditto=lambda: SimpleNamespace(reload=lambda: None,
                                                                                     describe=describe))


class Runtime:
    def __init__(self, running_pid=None):
        self.pid, self.started, self.stopped = running_pid, [], 0

    def endpoint(self):
        return SimpleNamespace(pid=self.pid, model="mine") if self.pid else None

    def start(self, tier=None, remember=True, **kw):
        assert remember is False, "an evaluation never changes the chosen tier"
        self.started.append(tier)
        self.pid = 1000 + len(self.started)
        return self.endpoint()

    def stop(self):
        self.stopped += 1
        self.pid = None


def test_tiers_start_and_stop_their_own_server_and_report_a_replaced_one(monkeypatch, framework):
    monkeypatch.setattr(ditto_eval, "DittoEval", FakeEval)
    cases = eval_run.select_cases(case_ids=("healing-disk-full",))
    rt, events = Runtime(running_pid=42), Recorder()
    runs = eval_run.run_evaluation(framework, cases, make_ditto=fake_ditto, make_runtime=lambda: rt,
                                   tiers=("small", "small-q2"), events=events)
    assert [r.setup["label"] for r in runs] == ["tier small", "tier small-q2"]
    assert rt.started == ["small", "small-q2"] and rt.stopped == 2
    assert ("busy", "start_tier", {"tier": "small"}) in events.seen and events.seen[-1] == ("replaced", "mine")


def test_retrieval_only_runs_say_that_ai_options_do_not_apply(monkeypatch, framework):
    events = Recorder()
    monkeypatch.setattr(eval_run, "run_retrieval", lambda fw, cases, source, variants, repeat, ev, stop:
                        [SimpleNamespace(variants=tuple(variants), source=source)])
    cases = eval_run.select_cases(kinds=("retrieval",))
    runs = eval_run.run_evaluation(framework, cases, make_ditto=lambda: pytest.fail("no AI model is needed"),
                                   provider="openai", events=events)
    assert events.seen == [("unused",)]
    assert runs[0].variants == ("fused",) and runs[0].source == ditto_eval.RETRIEVAL_CASES


def test_cancel_stops_before_the_next_attempt_and_keeps_finished_runs(monkeypatch, framework):
    monkeypatch.setattr(ditto_eval, "DittoEval", FakeEval)
    cases = eval_run.select_cases(kinds=("healing",))[:2]
    stop = threading.Event()

    class StopAfterFirst(Recorder):
        def result(self, result):
            super().result(result)
            stop.set()
    events = StopAfterFirst()
    rt = Runtime()
    with pytest.raises(EvalCancelled) as caught:
        eval_run.run_evaluation(framework, cases, make_ditto=fake_ditto, make_runtime=lambda: rt,
                                tiers=("small", "big"), events=events, stop=stop)
    assert [e for e in events.seen if e[0] == "result"] == [("result", cases[0].id)]
    assert caught.value.runs == [] and rt.started == ["small"] and rt.stopped == 1, "the eval's server is stopped"


def test_cancel_between_runs_returns_the_finished_ones(monkeypatch, framework):
    monkeypatch.setattr(ditto_eval, "DittoEval", FakeEval)
    cases = eval_run.select_cases(case_ids=("healing-disk-full",))
    stop = threading.Event()
    rt = Runtime()

    def start(tier=None, remember=True):
        Runtime.start(rt, tier, remember)
        if tier == "small":
            return rt.endpoint()
        raise AssertionError("not started after Cancel")
    rt.start = start

    class StopAtEnd(Recorder):
        def result(self, result):
            stop.set()
    with pytest.raises(EvalCancelled) as caught:
        eval_run.run_evaluation(framework, cases, make_ditto=fake_ditto, make_runtime=lambda: rt,
                                tiers=("small", "big"), events=StopAtEnd(), stop=stop)
    assert [r.setup["label"] for r in caught.value.runs] == ["tier small"] and rt.started == ["small"]


def test_a_replaced_server_is_reported_when_the_run_ends_early(monkeypatch, framework):
    """Cancel (or a failed start) after a tier replaced the person's own llama-server: the eval's server is stopped
    as after a normal run, and the person is told that theirs is not running now (the window promises it)."""
    from orchestrator.runtime.llama_server import LocalRuntimeError
    monkeypatch.setattr(ditto_eval, "DittoEval", FakeEval)
    cases = eval_run.select_cases(kinds=("healing",))[:2]
    stop = threading.Event()

    class StopAfterFirst(Recorder):
        def result(self, result):
            super().result(result)
            stop.set()
    events, rt = StopAfterFirst(), Runtime(running_pid=42)
    with pytest.raises(EvalCancelled):
        eval_run.run_evaluation(framework, cases, make_ditto=fake_ditto, make_runtime=lambda: rt,
                                tiers=("small", "big"), events=events, stop=stop)
    assert rt.stopped == 1 and events.seen[-1] == ("replaced", "mine")

    events, rt = Recorder(), Runtime(running_pid=42)

    def start(tier=None, remember=True):
        if tier == "big":
            raise LocalRuntimeError("llama-server did not start")
        return Runtime.start(rt, tier, remember)
    rt.start = start
    with pytest.raises(LocalRuntimeError):
        eval_run.run_evaluation(framework, cases[:1], make_ditto=fake_ditto, make_runtime=lambda: rt,
                                tiers=("small", "big"), events=events)
    assert events.seen[-1] == ("replaced", "mine")

    stop = threading.Event()
    stop.set()   # cancelled before the first tier: the person's server was never touched
    events, rt = Recorder(), Runtime(running_pid=42)
    with pytest.raises(EvalCancelled):
        eval_run.run_evaluation(framework, cases[:1], make_ditto=fake_ditto, make_runtime=lambda: rt,
                                tiers=("small",), events=events, stop=stop)
    assert rt.started == [] and rt.stopped == 0 and ("replaced", "mine") not in events.seen


def test_cancel_while_indexing_loads_no_reranker(monkeypatch, framework):
    """Cancel while the retrieval corpus is indexed: the reranker (a download on first use) is not loaded and no
    attempt runs."""
    import contextlib
    stop = threading.Event()
    loaded = []

    class Knowledge:
        reranker_reason = model_reason = last_error = ""

        def get_status(self):
            return {"embedding_model": "embed-a"}

        def reranker(self):
            return SimpleNamespace(model_id="reranker-a")

        def prepare_models(self):
            loaded.append("reranker")
            return True

        def rerank_floor(self, reranker):
            return 0.1

    class Retrieval:
        def __init__(self, knowledge, docs):
            pass

        def ingest(self):
            stop.set()   # the person presses Cancel while the corpus is indexed

        def run(self, cases, variant="fused", repeat=1, on_result=None, stop=None):
            loaded.append(f"attempt {variant}")
            return ditto_eval.run_cases(lambda case: Attempt(True, 0.1), cases, repeat, on_result, stop=stop)

    monkeypatch.setattr(ditto_eval, "corpus_dir", lambda source: Path(source).parent)
    monkeypatch.setattr(ditto_eval, "load_corpus", lambda folder: ["doc"])
    monkeypatch.setattr(ditto_eval, "check_retrieval_cases", lambda cases, docs: None)
    monkeypatch.setattr(ditto_eval, "temporary_knowledge_base", lambda fw: contextlib.nullcontext(Knowledge()))
    monkeypatch.setattr(ditto_eval, "RetrievalEval", Retrieval)
    cases = eval_run.select_cases(kinds=("retrieval",))
    with pytest.raises(EvalCancelled):
        eval_run.run_retrieval(framework, cases, ditto_eval.RETRIEVAL_CASES, ("fused", "rerank"), stop=stop)
    assert loaded == []


def test_run_cases_stops_between_attempts():
    stop = threading.Event()
    cases = load_cases()[:3]
    ran = []

    def run_case(case):
        ran.append(case.id)
        if len(ran) == 2:
            stop.set()
        return Attempt(True, 0.1)
    with pytest.raises(EvalCancelled):
        ditto_eval.run_cases(run_case, cases, repeat=1, stop=stop)
    assert ran == [cases[0].id, cases[1].id]
    assert len(ditto_eval.run_cases(lambda c: Attempt(True, 0.1), cases, repeat=2).results) == 3


def report(label, kind_rates, seconds=1.0, retrieval=None):
    cases = [SimpleNamespace(id=f"{label}-{k}", kind=k) for k in kind_rates]
    results = [CaseResult(c, [Attempt(rate == 1, seconds)]) for c, rate in zip(cases, kind_rates.values())]
    rep = EvalReport(results, setup={"label": label, "model": "m"})
    if retrieval is not None:
        rep.retrieval = lambda: retrieval
    return rep


def test_tables_and_the_json_report(tmp_path):
    runs = [report("tier small", {"healing": 1, "chat": 0}, seconds=2.0),
            report("retrieval fused", {"retrieval": 1},
                   retrieval={"hit@1": 0.5, "hit@3": 1.0, "hit@k": 1.0, "mrr": 0.75, "empty_when_irrelevant": None,
                              "latency_p50_ms": 12.4})]
    columns, rows = eval_run.summary_table(runs, ["chat", "healing", "retrieval"])
    assert columns == ["Run", "Model", "Passed", "chat", "healing", "retrieval", "Avg s/case"]
    assert rows[0] == ["tier small", "m", "50%", "0%", "100%", "-", "2.0"]
    assert rows[1] == ["retrieval fused", "m", "100%", "-", "-", "100%", "1.0"]
    columns, rows = eval_run.retrieval_table(runs)
    assert columns[0] == "Run" and rows == [["retrieval fused", "50%", "100%", "100%", "0.750", "-", "12"]]
    assert eval_run.retrieval_table(runs[:1]) is None
    assert eval_run.summary_table([], ["chat"]) == (["Run", "Model", "Passed", "chat", "Avg s/case"], [])


def test_write_report_matches_the_cli(tmp_path, monkeypatch, framework):
    monkeypatch.setattr(ditto_eval, "DittoEval", FakeEval)
    cases = eval_run.select_cases(case_ids=("healing-disk-full",))
    runs = eval_run.run_evaluation(framework, cases, make_ditto=fake_ditto)
    path = eval_run.write_report(tmp_path / "report.json", runs)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"runs": [runs[0].to_dict()]} and eval_run.report_data(runs) == data
    assert eval_run.report_json(runs) == path.read_text(encoding="utf-8")
