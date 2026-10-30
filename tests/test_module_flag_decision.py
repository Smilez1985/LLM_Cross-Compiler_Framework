#!/usr/bin/env python3
"""The person's decision on a module's CMake options outside the allowlist (DECISIONS round 25 Q6): the question with
what each option does and a recommendation, person-only, remembered per module version and flag set in the store of
the person's build confirmations, handed to the build container."""

from pathlib import Path

import pytest

from orchestrator.Core import module_flags
from test_build_parity_core import HF_MODEL
from test_review_capabilities_paths import gui_messages  # noqa: F401 - the Builder tab's recorded message boxes


def module(tmp_path: Path, options: str, version: str = "1.2.0") -> Path:
    folder = tmp_path / "myboard"
    (folder / "modules").mkdir(parents=True)
    (folder / "modules" / "config_module.sh").write_text(
        f"#!/bin/bash\nsetup() {{\n    CMAKE_OPTIONS='{options}'\n}}\n", encoding="utf-8")
    (folder / "target.yml").write_text(
        f"metadata:\n  name: myboard\n  version: \"{version}\"\nruntime:\n  backends: [cpu]\n  cpu: explicit\n"
        "  arch: arm64\noptimization_profiles:\n  rk3588_default:\n    recommended_quantization: [Q4_K_M, Q5_K_M]\n",
        encoding="utf-8")
    return folder


def test_the_question_names_each_option_and_a_recommendation_for_this_model(tmp_path):
    folder = module(tmp_path, "-DGGML_NATIVE=OFF -G Ninja --trace")
    question = module_flags.question_for("myboard", folder, model="org/model", quantization="Q4_K_M", board="rk3588")
    assert [o for o, _ in question.options] == ["-G Ninja", "--trace"]
    assert "build tool" in question.options[0][1]
    text = question.text()
    assert "myboard (version 1.2.0)" in text and "-G Ninja:" in text and "Recommendation:" in text
    assert "org/model as Q4_K_M for rk3588" in question.recommendation
    assert "backend cpu" in question.recommendation
    assert "Q4_K_M is among the quantizations this target recommends" in question.recommendation
    assert "/" not in question.ai_text().replace("org/model", ""), "no path reaches an AI"


def test_only_allowed_options_ask_nothing(tmp_path):
    assert module_flags.question_for("myboard", module(tmp_path, "-DGGML_NATIVE=OFF -Wno-dev"), model="m") is None
    assert module_flags.question_for("x", None) is None


def test_diagnostic_options_are_recommended_as_harmless(tmp_path):
    question = module_flags.question_for("myboard", module(tmp_path, "--log-level=VERBOSE"), quantization="Q8_0")
    assert "change only what CMake prints" in question.recommendation


def test_the_decision_is_remembered_per_module_version_and_flag_set(tmp_path):
    from orchestrator.Core.confirmed_builds import ConfirmedBuilds
    store = ConfirmedBuilds(tmp_path / "state")
    question = module_flags.question_for("myboard", module(tmp_path, "-G Ninja"))
    assert module_flags.remembered(store, question) is None
    assert not module_flags.remember(store, question, module_flags.decide(question, "allow"), "grant:g_1"), \
        "a standing approval is never the person's decision"
    assert module_flags.remember(store, question, module_flags.decide(question, "allow"), "framework-gui")
    kept = module_flags.remembered(store, question)
    assert kept.decision == "allow" and kept.allowed == ("-G Ninja",)
    other_version = module_flags.FlagQuestion("myboard", "1.3.0", list(question.options))
    other_flags = module_flags.FlagQuestion("myboard", "1.2.0", [("-G Ninja", ""), ("--trace", "")])
    assert module_flags.remembered(store, other_version) is None
    assert module_flags.remembered(store, other_flags) is None


def test_the_decision_becomes_the_container_s_variables():
    allow = module_flags.FlagDecision("allow", ("-G Ninja", "--trace"))
    assert allow.environment() == {"LLMCF_FLAG_DECISION": "allow", "LLMCF_ALLOWED_FLAGS": "-G Ninja\x1f--trace"}
    assert module_flags.FlagDecision("standard").environment() == {"LLMCF_FLAG_DECISION": "standard"}
    with pytest.raises(ValueError):
        module_flags.FlagDecision("sometimes")


class TestTheBuildService:

    @pytest.fixture
    def service(self, booted_framework, tmp_path, monkeypatch):
        from orchestrator.Core.build_service import BuildService
        from test_foundation import FakeOrchestrator
        booted_framework.orchestrator = FakeOrchestrator()
        folder = module(tmp_path, "-DGGML_NATIVE=OFF -G Ninja")
        words = module_flags.module_cmake_words(folder)   # the Rockchip target's folder stands for this module
        monkeypatch.setattr(module_flags, "module_cmake_words", lambda _folder: list(words) if _folder else [])
        monkeypatch.setattr(module_flags, "module_version", lambda _folder: "1.2.0")
        return BuildService(booted_framework)

    def request(self):
        from orchestrator.schemas.build import BuildRequestIn
        return BuildRequestIn(model=HF_MODEL, target="Rockchip", format="gguf")

    def test_a_person_s_build_without_a_decision_asks(self, service):
        from orchestrator.Core.build_service import BuildServiceError
        with pytest.raises(BuildServiceError) as raised:
            service.start_build(self.request(), local_paths=True)
        assert raised.value.code == "needs_person" and raised.value.flag_question.options[0][0] == "-G Ninja"
        assert service.framework.orchestrator.submitted == []

    @pytest.mark.parametrize("answer", ["allow", "standard"])
    def test_the_person_s_answer_travels_with_the_build(self, service, answer):
        service.start_build(self.request(), local_paths=True, flag_decision=answer)
        (req,) = service.framework.orchestrator.submitted
        assert req.flag_decision["decision"] == answer
        assert req.flag_decision["allowed"] == (["-G Ninja"] if answer == "allow" else [])

    def test_an_ai_caller_never_decides_and_stops_without_a_remembered_decision(self, service):
        from orchestrator.Core.build_service import BuildServiceError
        with pytest.raises(BuildServiceError) as refused:
            service.start_build(self.request(), owner="mcp#a", flag_decision="allow")
        assert refused.value.code == "permission_denied"
        with pytest.raises(BuildServiceError) as stopped:
            service.start_build(self.request(), owner="mcp#a")
        assert stopped.value.code == "needs_person" and "only the person decides" in str(stopped.value)
        assert service.framework.orchestrator.submitted == []

    def test_a_remembered_decision_is_used_by_an_ai_started_build(self, service):
        service.start_build(self.request(), local_paths=True, flag_decision="standard", remember_flags=True,
                            flag_channel="framework-cli")
        service.start_build(self.request(), owner="mcp#a")
        assert [r.flag_decision["decision"] for r in service.framework.orchestrator.submitted] == ["standard"] * 2

    def test_the_build_plan_of_an_ai_caller_names_the_blocker(self, service):
        from orchestrator.Core.build_plan import plan_build
        from orchestrator.schemas.build import BuildRequestIn
        plan = plan_build(service, BuildRequestIn(model=HF_MODEL, target="Rockchip", format="gguf"),
                          look_up_hub=False)
        assert any(p.blocker == "module_flags_undecided" and p.code == "needs_person" for p in plan.problems), \
            plan.problems


def test_the_builder_puts_the_decision_into_the_container_environment():
    """The build engine hands the decision to build.sh (BuildConfiguration.module_flags)."""
    import inspect
    from orchestrator.Core import builder
    source = inspect.getsource(builder.BuildEngine._execute_build_modules)
    assert "config.module_flags" in source and "FlagDecision(" in source and ".environment()" in source


class TestTheCli:

    def service(self, question, kept=None):
        from types import SimpleNamespace
        return SimpleNamespace(flag_question=lambda request: (question, kept))

    def question(self):
        return module_flags.FlagQuestion("myboard", "1.2.0", [("-G Ninja", "chooses another build tool")],
                                         "Go on with the standard flags.")

    def test_nothing_to_ask(self):
        from orchestrator import cli
        assert cli._ask_module_flags(self.service(None), None, None, False) == (None, False)

    def test_the_option_and_a_remembered_decision_ask_nothing(self, monkeypatch):
        from orchestrator import cli
        from test_cli_builds import quiet_console
        printed = quiet_console(monkeypatch)
        assert cli._ask_module_flags(self.service(self.question()), None, "allow", True) == ("allow", True)
        kept = module_flags.FlagDecision("standard")
        assert cli._ask_module_flags(self.service(self.question(), kept), None, None, False) == (None, False)
        assert "remembered decision (standard)" in printed.getvalue()

    def test_without_a_terminal_nothing_starts(self, monkeypatch):
        from orchestrator import cli
        from test_cli_builds import quiet_console
        printed = quiet_console(monkeypatch)
        monkeypatch.setattr(cli, "_interactive", lambda: False)
        with pytest.raises(SystemExit) as stopped:
            cli._ask_module_flags(self.service(self.question()), None, None, False)
        assert stopped.value.code == 2
        out = printed.getvalue()
        assert "-G Ninja: chooses another build tool" in out and "--module-flags allow" in out

    @pytest.mark.parametrize("answer, keep, expected", [("a", True, ("allow", True)), ("s", False, ("standard", False)),
                                                        ("c", False, ("cancel", False))])
    def test_at_a_terminal_the_person_answers(self, monkeypatch, answer, keep, expected):
        import click

        from orchestrator import cli
        from test_cli_builds import quiet_console
        quiet_console(monkeypatch)
        monkeypatch.setattr(cli, "_interactive", lambda: True)
        monkeypatch.setattr(click, "prompt", lambda *a, **k: answer)
        monkeypatch.setattr(click, "confirm", lambda *a, **k: keep)
        assert cli._ask_module_flags(self.service(self.question()), None, None, False) == expected

    def test_build_start_has_the_options(self):
        from test_cli_builds import invoke_plain
        result = invoke_plain("build", "start", "--help")
        assert "--module-flags" in result.out and "--remember-flags" in result.out


@pytest.mark.usefixtures("qapp", "gui_messages")
class TestTheBuilder:

    @pytest.fixture
    def tab(self, booted_framework, tmp_path, monkeypatch):
        pytest.importorskip("PySide6")
        from test_review_capabilities_paths import make_tab
        folder = module(tmp_path, "-DGGML_NATIVE=OFF -G Ninja")
        words = module_flags.module_cmake_words(folder)
        monkeypatch.setattr(module_flags, "module_cmake_words", lambda _folder: list(words) if _folder else [])
        tab = make_tab(booted_framework)
        tab.model_input.setEditText(HF_MODEL)
        yield tab
        tab._build_timer.stop()

    @pytest.mark.parametrize("answer, decision", [(("allow", True), "allow"), (("standard", False), "standard")])
    def test_the_person_decides_in_the_builder_and_the_build_carries_it(self, tab, monkeypatch, answer, decision):
        from test_review_capabilities_paths import submit
        asked = []
        monkeypatch.setattr(tab, "_ask_module_flags", lambda question: asked.append(question) or answer)
        req = submit(tab)
        assert asked and asked[0].options[0][0] == "-G Ninja"
        assert req is not None and req.flag_decision["decision"] == decision

    def test_cancel_starts_nothing(self, tab, monkeypatch):
        from test_review_capabilities_paths import submit
        monkeypatch.setattr(tab, "_ask_module_flags", lambda question: None)
        assert submit(tab) is None
