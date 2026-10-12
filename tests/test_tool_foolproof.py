"""The fool-proof layer (MCP design S3a, gaps G31-G34 and G43; D1-D6, D8 registry part, D9, D11-D14).

One normalizer for Ditto and every MCP client: spelling variants of tool names, declared argument aliases, the
same value coercion on both paths, closed value lists without edit distance, closed input schemas, errors with a
reason code and a next step, the repeat guard, example calls after repeated input errors, and a list of things
that are never repaired. The real framework tools run against the real Rockchip capabilities (the build service
is the evaluation's stand-in: nothing is built).
"""

import asyncio
from types import SimpleNamespace
from typing import List, Optional

import pytest
from pydantic import BaseModel, Field

pytest.importorskip("mcp")
from mcp import Client  # noqa: E402

from conftest import REPO_ROOT  # noqa: E402
from orchestrator import mcp_server  # noqa: E402
from orchestrator.evals.ditto_eval import EvalEnvironment, tool_use_metrics  # noqa: E402
from orchestrator.llm.providers.openai_compat import parse_arguments  # noqa: E402
from orchestrator.llm.tool_loop import tool_specs_from_registry  # noqa: E402
from orchestrator.tools.audit import AuditLog  # noqa: E402
from orchestrator.tools.framework_tools import build_fields  # noqa: E402
from orchestrator.tools.normalize import (NormalizeError, match_choice, match_filename,  # noqa: E402
                                          normalize_arguments)
from orchestrator.tools.output import ToolOutput  # noqa: E402
from orchestrator.tools.policy import Decision, PolicyStore, ToolPolicy, ToolTier  # noqa: E402
from orchestrator.tools.registry import (ToolContext, ToolError, ToolInputError, ToolRegistry, ToolSpec,  # noqa: E402
                                         UnknownTool, current_question)
from orchestrator.tools.spec import Choices, FieldMeta  # noqa: E402
from test_mcp_server import answering, text_of  # noqa: E402

MODEL = "Qwen/Qwen2.5-0.5B-Instruct"
BUILD = {"model": MODEL, "target": "Rockchip", "format": "gguf"}


@pytest.fixture
def env():
    environment = EvalEnvironment(REPO_ROOT / "targets")
    environment.registry.policy_store.replace(ToolPolicy(configured=True, overrides={"start_build": Decision.ALLOW},
                                                      reviewed=[s.name for s in environment.registry.list()]))
    yield environment
    environment.close()


def built(env):
    return env.framework.build_service.started


def mcp(env, name, arguments, callback=None):
    server = mcp_server.build_mcp_server(SimpleNamespace(info=SimpleNamespace(version="t"),
                                                         tool_registry=env.registry))

    async def go():
        async with Client(server, elicitation_callback=callback) as client:
            return await client.call_tool(name, arguments)
    return asyncio.run(go())


def ditto(env, name, arguments, session="s1", confirm=None):
    return env.registry.call(name, arguments, ToolContext("ditto", session=session, confirm=confirm))


def ditto_error(env, name, arguments, session="s1") -> ToolError:
    with pytest.raises(ToolError) as exc:
        ditto(env, name, arguments, session)
    return exc.value


# --- the same call on both paths (parity guard) -------------------------------------------------------------------

class TestParity:

    RAW = {**BUILD, "quant": "q4_k_m", "board": "RK3588"}

    def test_the_same_raw_call_runs_the_same_on_both_paths(self, env):
        out = ditto(env, "StartBuild", self.RAW)
        result = mcp(env, "StartBuild", self.RAW)
        assert not result.is_error, text_of(result)
        first, second = built(env)
        assert first == second and (first["quantization"], first["board"]) == ("Q4_K_M", "rk3588")
        assert result.structured_content["notes"] == out.notes
        assert "ran start_build (you wrote StartBuild)" in out.notes
        assert "argument 'quant' read as 'quantization'" in out.notes
        assert "quantization: Q4_K_M (you wrote q4_k_m)" in out.notes

    @pytest.mark.parametrize("raw", [{**BUILD, "qnt": "Q4_K_M"}, {**BUILD, "quantization": "Q5_K_X"},
                                     {"model": MODEL, "target": "Rockchip"}, {**BUILD, "board": "rk3567"}])
    def test_errors_read_the_same_on_both_paths(self, env, raw):
        error = ditto_error(env, "start_build", raw, session="a")
        result = mcp(env, "start_build", raw)
        assert result.is_error and text_of(result) == str(error)
        assert built(env) == []

    def test_mcp_clients_get_the_closed_schemas_dittos_model_sees(self, env):
        server = mcp_server.build_mcp_server(SimpleNamespace(info=SimpleNamespace(version="t"),
                                                             tool_registry=env.registry))

        async def go():
            async with Client(server) as client:
                return {t.name: t for t in (await client.list_tools()).tools}
        tools = asyncio.run(go())
        dittos = {s.name: s for s in tool_specs_from_registry(env.registry)}
        for name, tool in tools.items():
            assert tool.input_schema.get("additionalProperties") is False, name
            assert tool.input_schema["properties"] == dittos[name].input_schema["properties"], name


# --- names (D2, G33) -----------------------------------------------------------------------------------------------

class TestNames:

    @pytest.mark.parametrize("name", ["start-build", "Start Build", "START.BUILD", "startBuild"])
    def test_spelling_variants_run_the_real_tool(self, env, name):
        ditto(env, name, BUILD)
        assert len(built(env)) == 1

    @pytest.mark.parametrize("name", ["start​build", "start!build", "stärt_build", "start_build\n"])
    def test_other_characters_are_never_resolved(self, env, name):
        error = ditto_error(env, name, BUILD)
        assert isinstance(error, UnknownTool) and error.code == "unknown_tool" and built(env) == []

    def test_a_near_miss_is_only_suggested_never_run(self, env):
        calls_before = list(env.calls)
        error = ditto_error(env, "set_status", {})
        assert "get_status" in error.suggestions and "Call one of these exact names" in str(error)
        assert env.calls == calls_before, "get_status did not run"

    def test_read_tools_come_first_for_read_like_names(self, env):
        error = ditto_error(env, "list_build", {})
        assert error.suggestions[0].startswith("list_") and "start_build" not in error.suggestions[:1]

    def test_suggestions_never_name_a_hidden_tool(self, env):
        env.registry.policy_store.replace(ToolPolicy(configured=True, overrides={"start_build": Decision.DENY}))
        error = ditto_error(env, "start_buil", BUILD)
        assert "start_build" not in error.suggestions

    def test_unknown_names_are_audited_on_both_paths(self, env):
        ditto_error(env, "no_such_tool", {})
        assert mcp(env, "no_such_tool", {}).is_error
        entries = [e for e in env.registry.audit.entries(10) if e["outcome"] == "unknown_tool"]
        assert len(entries) == 2 and entries[0]["tool"] == "no_such_tool"
        assert entries[0]["reason_code"] == "unknown_tool"

    def test_tool_names_must_stay_distinguishable(self, env):
        spec = env.registry.get("start_build")
        with pytest.raises(ValueError, match="spelling variant"):
            env.registry.register(ToolSpec("Start-Build", "x", "", ToolTier.READ, spec.input_model,
                                           spec.output_model, spec.handler))


# --- argument names (D3, G31) ---------------------------------------------------------------------------------------

class TestArgumentNames:

    def test_nothing_is_dropped(self, env):
        error = ditto_error(env, "start_build", {**BUILD, "qnt": "Q4_K_M"})
        assert error.code == "unknown_argument" and "Did you mean 'quantization'?" in error.message
        assert built(env) == []

    def test_an_alias_and_its_field_with_different_values_conflict(self, env):
        error = ditto_error(env, "start_build", {**BUILD, "quant": "Q4_K_M", "quantization": "Q8_0"})
        assert "different values" in error.message and built(env) == []
        ditto(env, "start_build", {**BUILD, "quant": "Q4_K_M", "quantization": "Q4_K_M"})
        assert built(env)[0]["quantization"] == "Q4_K_M"

    @pytest.mark.parametrize("key", ["Board", "BOARD", "bo-ard"])
    def test_an_undeclared_spelling_of_a_key_is_refused_with_a_suggestion(self, env, key):
        """Review fix (maintainer decision, fourth round: declared aliases are the only mapping). Keys in other
        letter case or with other separators were mapped onto the field by themselves; they are refused with a
        suggestion on both paths, and nothing runs. (This replaces the builder's test that two spellings of one key
        conflict: an undeclared spelling is refused on its own now.)"""
        raw = {**BUILD, key: "rk3588"}
        error = ditto_error(env, "start_build", raw, session="k")
        assert error.code == "unknown_argument" and "Did you mean 'board'?" in error.message
        result = mcp(env, "start_build", raw)
        assert result.is_error and text_of(result) == str(error) and built(env) == []

    def test_a_key_is_never_mapped_onto_a_credential_field(self):
        class In(BaseModel):
            api_token: Optional[str] = None
        spec = ToolSpec("t", "T", "", ToolTier.READ, In, ToolOutput, lambda i: ToolOutput())
        with pytest.raises(NormalizeError) as exc:
            normalize_arguments(spec, {"API-Token": "x"})
        assert exc.value.code == "unknown_argument"


# --- values (D4, D5, G34, G43) --------------------------------------------------------------------------------------

class TestValues:

    def test_canonical_form_and_declared_alias(self, env):
        ditto(env, "start_build", {**BUILD, "quantization": "q4-k-m"})
        ditto(env, "start_build", {**BUILD, "quantization": "Q4"})  # Rockchip's declared alias for gguf
        assert [b["quantization"] for b in built(env)] == ["Q4_K_M", "Q4_K_M"]

    def test_a_near_miss_board_is_refused_with_suggestions_never_built(self, env):
        error = ditto_error(env, "start_build", {**BUILD, "board": "rk3567"})
        assert error.code == "invalid_value" and {"rk3566", "rk3568"} & set(error.suggestions)
        assert "rk3567" in error.message and built(env) == []

    def test_a_near_miss_quantization_is_refused(self, env):
        error = ditto_error(env, "start_build", {**BUILD, "quantization": "Q4_K_X"})
        assert error.code == "invalid_value" and "Q4_K_M" in error.allowed and built(env) == []

    def test_a_canonical_form_matching_two_targets_is_refused(self):
        service = SimpleNamespace(target_choices=lambda: (["Rockchip", "rockchip"], {}))
        choices = build_fields(service)["target"].choices_from({})
        for written in ("rockchip", "Rockchip", "ROCK-CHIP"):
            with pytest.raises(NormalizeError) as exc:
                match_choice(written, choices, "target")
            assert set(exc.value.suggestions) == {"Rockchip", "rockchip"}

    def test_a_folder_name_resolves_to_its_target(self):
        service = SimpleNamespace(target_choices=lambda: (["rockchip-npu"], {"Rockchip": "rockchip-npu"}))
        choices = build_fields(service)["target"].choices_from({})
        assert match_choice("rockchip", choices, "target")[0] == "rockchip-npu"

    def test_json_text_in_a_list_field_is_accepted_on_both_paths(self, env):
        out = ditto(env, "get_status", {"sections": '["permissions"]'})
        result = mcp(env, "get_status", {"sections": '["permissions"]'})
        assert out.permissions is not None and not result.is_error

    def test_an_amount_to_return_is_reduced_with_a_note(self, env):
        out = ditto(env, "read_build_log", {"request_id": "req-7", "lines": 5000})
        assert "lines reduced to 2000, the maximum (you asked for 5000)" in out.notes

    def test_text_numbers_and_booleans_are_coerced(self, env):
        ditto(env, "start_build", {**BUILD, "use_imatrix": "yes", "max_context_length": "4096"})
        assert (built(env)[0]["use_imatrix"], built(env)[0]["max_context_length"]) == (True, 4096)

    def test_units_and_empty_values_only_where_declared(self):
        class In(BaseModel):
            timeout: int = Field(default=30, ge=1, le=600)
            note: Optional[str] = None
            other: Optional[str] = None
        spec = ToolSpec("t", "T", "", ToolTier.READ, In, ToolOutput, lambda i: ToolOutput(),
                        fields={"timeout": FieldMeta(unit="seconds"), "note": FieldMeta(null_means_default=True)})
        result = normalize_arguments(spec, {"timeout": "2 min", "note": "default", "other": "default"})
        assert result.arguments == {"timeout": 120, "other": "default"}
        assert [r.kind for r in result.repairs] == ["unit", "empty"]

    def test_the_normalizer_is_idempotent(self, env):
        spec = env.registry.get("start_build")
        once = normalize_arguments(spec, {**BUILD, "quant": "q4", "board": "RK3588", "use_gpu": "no"})
        twice = normalize_arguments(spec, once.arguments)
        assert twice.arguments == once.arguments and twice.repairs == []

    def test_file_names_match_exactly_or_by_one_unique_case_insensitive_match(self):
        files = ["model.Q4_K_M.gguf", "Model-Q4_K_M.gguf", "model-Q4_K_M.GGUF"]
        with pytest.raises(NormalizeError):
            match_filename("model-q4_k_m.gguf", files, "filename")
        assert match_filename("MODEL.q4_k_m.GGUF", ["model.Q4_K_M.gguf"], "filename")[0] == "model.Q4_K_M.gguf"
        with pytest.raises(NormalizeError):
            match_filename("model-q4_k_m.gguf", ["model.Q4_K_M.gguf"], "filename")


# --- never repaired (guard) -------------------------------------------------------------------------------------------

class TestNeverRepaired:

    def test_ids_lose_only_surrounding_whitespace(self, env):
        assert ditto(env, "get_build_status", {"request_id": " req-7 "}).request_id == "req-7"
        error = ditto_error(env, "get_build_status", {"request_id": "REQ-7"})
        assert error.code == "unknown_id"

    def test_models_paths_and_commits_are_never_changed(self, env):
        spec = env.registry.get("start_build")
        raw = {**BUILD, "model": "qwen/QWEN2.5-0.5b-instruct", "dataset_path": "Datasets:Calib.txt",
               "revision": "ABCDEF" * 6 + "ABCD"}
        result = normalize_arguments(spec, raw)
        assert {k: result.arguments[k] for k in ("model", "dataset_path", "revision")} == {
            "model": raw["model"], "dataset_path": raw["dataset_path"], "revision": raw["revision"]}
        error = ditto_error(env, "start_build", raw)
        assert "revision" in error.message, "an upper-case commit is refused, not lowered"

    @pytest.mark.parametrize("approve", ["true", "yes", 1, "1"])
    def test_an_authorizing_boolean_must_be_a_real_boolean(self, env, approve):
        error = ditto_error(env, "resolve_healing", {"request_id": "req-7", "approve": approve})
        assert "true or false" in error.message

    def test_no_edit_distance_on_values(self):
        choices = Choices(("Q4_K_M", "Q5_K_M", "Q8_0"))
        for near in ("Q4_K_N", "Q6_K_M", "Q8_1"):
            with pytest.raises(NormalizeError):
                match_choice(near, choices, "quantization")


# --- errors, the repeat guard, examples (D6, D8, D9, D10) ---------------------------------------------------------

class TestErrors:

    def test_missing_argument_names_the_choices(self, env):
        error = ditto_error(env, "start_build", {"model": MODEL, "target": "Rockchip"})
        assert error.code == "missing_argument"
        assert error.message == "start_build needs 'format' (one of: gguf, rkllm, rknn)."
        assert "get_target_capabilities" in error.next

    def test_unknown_ids_name_the_listing_tool_and_guessing_stops(self, env):
        first = ditto_error(env, "get_build_status", {"request_id": "req-1"})
        assert first.code == "unknown_id" and "Call list_build_requests" in str(first)
        second = ditto_error(env, "get_build_status", {"request_id": "req-2"})
        assert "Do not guess ids." in second.next

    def test_the_third_identical_failing_call_is_refused_without_running(self, env, monkeypatch):
        ran = []
        service = env.framework.build_service
        real = service.cancel
        monkeypatch.setattr(service, "cancel", lambda rid: ran.append(rid) or real(rid))
        env.registry.policy_store.replace(ToolPolicy(configured=True, overrides={"cancel_build": Decision.ALLOW},
                                                  reviewed=[s.name for s in env.registry.list()]))
        first = ditto_error(env, "cancel_build", {"request_id": "req-7"})
        second = ditto_error(env, "cancel_build", {"request_id": "req-7"})
        third = ditto_error(env, "cancel_build", {"request_id": "req-7"})
        assert "This exact call already failed." not in first.message
        assert second.message.startswith("This exact call already failed.")
        assert third.code == "repeat_refused" and third.message.startswith("Stop retrying this call.")
        assert ran == ["req-7", "req-7"], "the third call never reached the handler"
        assert env.registry.audit.entries(1)[0]["outcome"] == "repeat_refused"

    @pytest.mark.parametrize("name, raw", [("start_build", {**BUILD, "qnt": "x"}), ("nope", {})])
    def test_the_same_invalid_call_a_third_time_is_refused(self, env, name, raw):
        errors = [ditto_error(env, name, raw, session="r") for _ in range(3)]
        assert errors[1].message.startswith("This exact call already failed.")
        assert errors[2].code == "repeat_refused" and "Stop retrying this call" in errors[2].message

    def test_another_session_and_progress_are_not_refused(self, env):
        for _ in range(3):
            ditto_error(env, "start_build", {**BUILD, "board": "rk3567"}, session="one")
        ditto_error(env, "start_build", {**BUILD, "board": "rk3567"}, session="two")  # not refused
        ditto(env, "start_build", {**BUILD, "board": "rk3566"}, session="one")  # fixed: runs
        assert len(built(env)) == 1

    def test_after_three_input_errors_an_example_call_is_shown(self, env):
        errors = [ditto_error(env, "start_build", {"model": MODEL, "target": "Rockchip", "format": f"x{i}"},
                              session="ex") for i in range(3)]
        assert "A valid call looks like" not in errors[1].next
        assert "A valid call looks like: start_build" in errors[2].next and MODEL in errors[2].next

    def test_refusals_carry_reason_codes(self, env):
        env.registry.policy_store.replace(ToolPolicy(configured=True, overrides={"start_build": Decision.DENY}))
        denied = ditto_error(env, "start_build", BUILD)
        assert denied.code == "permission_denied" and "Next:" in str(denied)
        env.registry.policy_store.replace(ToolPolicy(configured=True))
        nobody = ditto_error(env, "start_build", BUILD, session="b")
        assert nobody.code == "needs_person"
        with pytest.raises(ToolError) as exc:
            ditto(env, "start_build", BUILD, session="c", confirm=lambda s, i: False)
        assert exc.value.code == "declined"
        env.registry.policy_store.replace(ToolPolicy(configured=True, overrides={"start_build": Decision.ALLOW}))
        unsupported = ditto_error(env, "start_build", {**BUILD, "format": "rkllm", "board": "rk3566"}, session="d")
        assert unsupported.code == "not_supported"
        entries = {e["reason_code"] for e in env.registry.audit.entries(20) if e.get("reason_code")}
        assert {"permission_denied", "needs_person", "declined", "not_supported"} <= entries


# --- repairs reach the question and the audit (D13) -------------------------------------------------------------

class TestRepairsAreShown:

    def test_the_mcp_question_marks_repaired_values(self, env):
        env.registry.policy_store.replace(ToolPolicy(configured=True))
        asked = []
        result = mcp(env, "StartBuild", {**BUILD, "quant": "q4_k_m"}, answering("accept", asked=asked))
        assert not result.is_error, text_of(result)
        assert "quantization: Q4_K_M (repaired; the AI wrote q4_k_m)" in asked[0]
        assert "(tool name repaired; the AI wrote StartBuild)" in asked[0]

    def test_dittos_question_is_the_registrys_text_with_the_marks(self, env):
        env.registry.policy_store.replace(ToolPolicy(configured=True))
        shown = []
        ditto(env, "start_build", {**BUILD, "quantization": "q4_k_m"}, confirm=lambda s, i: shown.append(
            current_question()) or True)
        assert "quantization: Q4_K_M (repaired; the AI wrote q4_k_m)" in shown[0]
        requested = next(e for e in env.registry.audit.entries(10) if e["outcome"] == "confirmation_requested")
        import hashlib
        assert requested["question_sha256"] == hashlib.sha256(shown[0].encode()).hexdigest()

    def test_the_audit_records_the_name_and_the_repairs(self, env):
        ditto(env, "StartBuild", {**BUILD, "quant": "q4_k_m"})
        end = env.registry.audit.entries(1)[0]
        assert end["name_requested"] == "StartBuild"
        kinds = {(r["kind"], r["field"]) for r in end["repairs"]}
        assert {("name", ""), ("alias", "quantization"), ("canonical", "quantization")} <= kinds


# --- Ditto's own path: provider syntax repair and the evaluation (D5, D14) -----------------------------------------

class TestDittoPath:

    @pytest.mark.parametrize("raw, parsed", [
        ('```json\n{"target": "Rockchip"}\n```', {"target": "Rockchip"}),
        ("{'target': 'Rockchip', 'use_gpu': True, 'board': None,}", {"target": "Rockchip", "use_gpu": True,
                                                                    "board": None}),
    ])
    def test_one_cautious_syntax_repair(self, raw, parsed):
        assert parse_arguments(raw) == (parsed, None)

    @pytest.mark.parametrize("raw", ["{not json", "__import__('os').system('true')", "[1, 2]", "x" * 200_000])
    def test_everything_else_stays_an_error(self, raw):
        args, problem = parse_arguments(raw)
        assert args == {} and problem

    def test_the_evaluation_records_every_attempt(self, env):
        ditto(env, "start_build", BUILD)
        for call in ({"name": "nope", "args": {}}, {"name": "start_build", "args": {**BUILD, "qnt": "x"}}):
            with pytest.raises(ToolError):
                ditto(env, call["name"], call["args"])
        assert [(a["tool"], a["code"]) for a in env.attempts] == [
            ("start_build", ""), ("nope", "unknown_tool"), ("start_build", "unknown_argument")]
        report = SimpleNamespace(case=SimpleNamespace(kind="chat"),
                                 attempts=[SimpleNamespace(tool_calls=env.attempts)])
        metrics = tool_use_metrics([report])
        assert metrics["tool_calls"] == 3 and metrics["unknown_name_rate"] == round(1 / 3, 4)
        assert metrics["malformed_rate"] == round(1 / 3, 4)


# --- review fixes: the repeat guard over MCP, closed models, every tool refuses unknown arguments ---------------

def mcp_session(env, calls, callback=None, mode="auto"):
    """Several calls by one MCP client (one connection in the classic era, one server in the 2026-07-28 era)."""
    server = mcp_server.build_mcp_server(SimpleNamespace(info=SimpleNamespace(version="t"),
                                                         tool_registry=env.registry))

    async def go():
        async with Client(server, elicitation_callback=callback, mode=mode) as client:
            return [await client.call_tool(name, arguments) for name, arguments in calls]
    return asyncio.run(go())


class TestRepeatGuardOverMcp:
    """Review fix: the MCP session key hashed the SDK's per-request session object, so every call of a client was
    a session of its own: the repeat guard, "do not guess ids" and the example calls never worked over MCP. And
    the guard keyed a failed call on the SDK's parsed arguments, not the raw ones the server checks before the
    question, so a write that had failed twice was put to the person a third time before it was refused."""

    @pytest.mark.parametrize("mode", ["auto", "legacy"])
    def test_the_same_failing_call_is_flagged_then_refused(self, env, mode):
        texts = [text_of(r) for r in mcp_session(env, [("get_build_status", {"request_id": "req-9"})] * 3,
                                                  mode=mode)]
        assert "already failed" not in texts[0] and "This exact call already failed." in texts[1]
        assert "Stop retrying this call" in texts[2]

    @pytest.mark.parametrize("mode", ["auto", "legacy"])
    def test_guessed_ids_and_repeated_input_errors_get_guidance(self, env, mode):
        texts = [text_of(r) for r in mcp_session(env, [("get_build_status", {"request_id": f"req-{i}"})
                                                       for i in range(2)], mode=mode)]
        assert "Do not guess ids." in texts[1]
        texts = [text_of(r) for r in mcp_session(env, [("start_build", {**BUILD, "format": f"x{i}"})
                                                       for i in range(3)], mode=mode)]
        assert "A valid call looks like: start_build" in texts[2]

    @pytest.mark.parametrize("approve", [True, False])
    def test_a_write_that_failed_twice_is_not_put_to_the_person_again(self, env, approve):
        env.registry.policy_store.replace(ToolPolicy(configured=True, reviewed=[s.name for s in env.registry.list()]))
        asked = []
        raw = {**BUILD, "format": "rkllm", "board": "rk3566"}  # refused by the build service: not_supported
        results = mcp_session(env, [("start_build", raw)] * 3, answering("accept", approve=approve, asked=asked))
        # Since S8 a person's "no" is not asked again for 10 minutes (design 6.5), so a declined call is asked once.
        expected, refusal = (2, "Stop retrying this call") if approve else (1, "declined this exact call")
        assert len(asked) == expected and refusal in text_of(results[2])
        dittos, errors = [], []
        for _ in range(3):
            with pytest.raises(ToolError) as exc:
                ditto(env, "start_build", raw, session="d", confirm=lambda s, i: dittos.append(1) or approve)
            errors.append(exc.value)
        assert len(dittos) == expected, "the same on Ditto's path"
        assert refusal in str(errors[2]), "and the same answer on Ditto's path"


class TestEveryToolRefusesUnknownArguments:
    """Review fix (maintainer decision, fourth round): every registered tool, present and future, refuses an argument
    it does not know, with a suggestion, on every path; its input model forbids extra fields."""

    @staticmethod
    def valid_call(spec):
        if spec.examples:
            return dict(spec.examples[0])
        values = {str: "req-1", bool: True, int: 1}
        return {name: values.get(info.annotation, "req-1") for name, info in spec.input_model.model_fields.items()
                if info.is_required()}

    def test_every_registered_tool(self, env):
        assert len(env.registry.list()) >= 12
        for spec in env.registry.list():
            assert spec.input_model.model_config.get("extra") == "forbid", spec.name
            fields = list(spec.input_model.model_fields)
            raw = {**self.valid_call(spec), (fields[0].upper() if fields else "verbose"): "x"}
            calls = list(env.calls)
            error = ditto_error(env, spec.name, raw, session=f"guard-{spec.name}")
            assert error.code == "unknown_argument", (spec.name, str(error))
            assert (f"Did you mean '{fields[0]}'?" in error.message if fields else "takes no arguments" in str(error))
            result = mcp(env, spec.name, raw)
            assert result.is_error and text_of(result) == str(error), spec.name
            assert env.calls == calls, f"{spec.name} ran"

    def test_an_input_model_without_forbid_is_closed_at_registration(self, tmp_path):
        class Open(BaseModel):
            text: str = ""
        reg = ToolRegistry(PolicyStore(tmp_path), AuditLog(tmp_path / "audit.db"))
        spec = reg.register(ToolSpec("t", "T", "", ToolTier.READ, Open, ToolOutput, lambda i: ToolOutput()))
        assert spec.input_model.model_config["extra"] == "forbid"
        assert spec.input_model.model_json_schema()["additionalProperties"] is False
        with pytest.raises(ToolInputError):
            reg.validate_input(spec, {"text": "a", "other": 1})
        assert reg.call("t", {"text": "a"}, ToolContext("mcp")).notes == []


def test_input_errors_keep_their_type_for_callers(tmp_path):
    class In(BaseModel):
        items: List[str]
    reg = ToolRegistry(PolicyStore(tmp_path), AuditLog(tmp_path / "audit.db"))
    reg.register(ToolSpec("t", "T", "", ToolTier.READ, In, ToolOutput, lambda i: ToolOutput()))
    with pytest.raises(ToolInputError) as exc:
        reg.call("t", {"items": 5}, ToolContext("mcp"))
    assert exc.value.code == "invalid_value" and "Next:" in str(exc.value)
    assert reg.call("t", {"items": '["a", "b"]'}, ToolContext("mcp")).notes == ["items: JSON text read as a list"]
