"""The memory estimate before a build starts (Core/memory_estimate.py; DESIGN, "Targets, hardware and templates").

- The estimate of the weights against the real sizes of published GGUF files (tests/fixtures/memory_estimate: five
  models from 135M to 8B parameters, nine quantization types; the source of each size is in the fixture): per tensor
  from the model's config.json within 2 % (measured: at most 0.8 %), from the parameter count alone (the rough
  estimate) within 3 % at Q8_0 and never more than 35 % low at 4 bits (small models keep their embeddings at 6 to 8
  bits).
- One table of bits per weight (the block sizes of ggml's types; the disk rule reads it too), the KV cache per
  context and type, a GGUF file's header as a shape.
- The estimate and the warning after the build share one verdict (board_profiles.memory_state): with the real file
  equal to the estimated weights and the same KV cache they never disagree.
- Where it shows: the AI's start question (a fact and the board's warning, the build still starts with the person's
  yes and records the estimate), `llm-cli build start` (printed before the start, never a stop) and `build preflight
  --model`, the Builder's line under the quantization (updated when the type changes, carried into the build), and
  build_info.json (the estimate next to the real size). No network: the model's config.json comes from the fixture.
"""

import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import REPO_ROOT
from test_arm64_target import ZERO2W_PROBE, store
from test_arm64_target import service as arm64_service  # noqa: F401 - the build service with stored probes
from test_build_tools import ask, env  # noqa: F401 - the build tools' environment (no network, no probe)
from test_review_capabilities_paths import (
    gui_messages,  # noqa: F401 - the Builder tab's recorded message boxes
)

from orchestrator.Core import board_profiles as bp
from orchestrator.Core import memory_estimate as me

FIXTURE = json.loads((REPO_ROOT / "tests" / "fixtures" / "memory_estimate" / "published_gguf.json")
                     .read_text(encoding="utf-8"))
MODELS = {m["name"]: m for m in FIXTURE["models"]}
PER_TENSOR_TOLERANCE = 0.02
CASES = [(name, quant, size) for name, m in MODELS.items() for quant, size in m["gguf_bytes"].items()]


def shape_of(name: str, parameters=None) -> me.ModelShape:
    m = MODELS[name]
    return me.shape_from_config(m["config"], parameters if parameters is not None else m["parameters"])


# --- the weights against published files ------------------------------------------------------------------------------

@pytest.mark.parametrize("name, quant, real", CASES, ids=[f"{n}-{q}" for n, q, _ in CASES])
def test_the_per_tensor_estimate_is_within_two_per_cent_of_the_published_file(name, quant, real):
    weights, basis = me.weights_bytes(shape_of(name), quant)
    assert basis == "per_tensor"
    assert abs(weights - real) <= real * PER_TENSOR_TOLERANCE, (weights, real)


@pytest.mark.parametrize("name", ["Llama 3.2 1B Instruct", "SmolLM2 135M Instruct"])
def test_the_rough_estimate_from_the_parameter_count_is_marked_and_bounded(name):
    """Without a shape: parameters x bits per weight. Q8_0 is close; at 4 bits a small model's file is larger
    (embeddings and output at Q6_K or Q8_0), which the text says."""
    m = MODELS[name]
    shape = me.ModelShape(parameters=m["parameters"])
    q8, basis = me.weights_bytes(shape, "Q8_0")
    assert basis == "parameters" and abs(q8 - m["gguf_bytes"]["Q8_0"]) <= m["gguf_bytes"]["Q8_0"] * 0.03
    for quant in ("IQ4_XS", "Q4_K_M"):
        low, _ = me.weights_bytes(shape, quant)
        real = m["gguf_bytes"][quant]
        assert real * 0.65 <= low < real
    est = me.estimate(shape, "Q4_K_M", board="bcm2837", ram_mib=416, ram_source=bp.board_memory(
        SimpleNamespace(int_value=lambda key: 416), None, "bcm2837")[1])
    assert est.basis == "parameters" and "rough estimate" in est.text() and "up to a third" in est.text()


def test_the_shape_explains_the_safetensors_parameter_count():
    for name in ("Llama 3.2 1B Instruct", "SmolLM2 135M Instruct", "Llama 3.1 8B Instruct"):
        shape = shape_of(name)
        assert shape.shape_parameters() == MODELS[name]["parameters"], name


def test_parameters_the_shape_does_not_explain_are_added_at_the_types_bits():
    shape = shape_of("SmolLM2 135M Instruct")
    plain, _ = me.weights_bytes(shape, "Q8_0")
    more, _ = me.weights_bytes(shape_of("SmolLM2 135M Instruct", parameters=shape.parameters + 100_000_000), "Q8_0")
    assert more - plain == int(100_000_000 * me.QUANT_BITS["Q8_0"] / 8)


# --- the table, the per-tensor rules, the KV cache, a GGUF header ----------------------------------------------------

def test_one_table_of_bits_per_weight_from_ggmls_block_sizes():
    """The IQ and ternary types against the bits quantize.cpp's list names; the disk rule reads the same table."""
    from orchestrator.Core import disk_plan
    named = {"IQ4_XS": 4.25, "IQ4_NL": 4.5, "IQ2_XXS": 2.06, "IQ2_XS": 2.31, "IQ1_S": 1.56, "IQ1_M": 1.75,
             "IQ3_XXS": 3.06, "TQ1_0": 1.69, "TQ2_0": 2.06, "Q2_0": 2.25, "IQ3_S": 3.44, "IQ3_M": 3.44}
    for quant, bits in named.items():
        assert round(me.bits_per_weight(quant), 2) == bits, quant
    assert me.bits_per_weight("q4_k_m") == 4.5 and me.bits_per_weight("Q8_0") == 8.5
    assert me.bits_per_weight("F16") == 16 and me.bits_per_weight("w4a16") == 4.5
    assert me.bits_per_weight("nonsense") is None
    for quant in ("Q4_K_M", "F16", "F32", "w8a8", "fp16", "x"):
        assert disk_plan.bits_of(quant) == (me.bits_per_weight(quant) or 8.0)


def test_embeddings_output_and_the_mixes_more_bits_layers():
    # tied embeddings are the output: Q6_K for a 4-bit build, Q8_0 when the row is no multiple of 256
    assert me.tensor_type("Q4_K_M", "token_embd_tied", -1, 16, 2048) == "Q6_K"
    assert me.tensor_type("Q4_K_M", "token_embd_tied", -1, 30, 576) == "Q8_0"
    assert me.tensor_type("Q4_K_M", "token_embd", -1, 32, 4096) == "Q4_K"   # an output of its own: the base type
    assert me.tensor_type("Q4_K_M", "ffn_down", 0, 16, 8192) == "Q6_K"      # first eighth: more bits
    assert me.tensor_type("Q4_K_M", "ffn_down", 3, 16, 8192) == "Q4_K"
    assert me.tensor_type("Q4_K_M", "ffn_up", 0, 30, 576) == "Q5_0"         # the fallback of a 576 row
    assert me.tensor_type("IQ4_XS", "attn_q", 0, 30, 576) == "IQ4_NL"
    assert me.tensor_type("F16", "attn_q", 0, 30, 576) == "F16"
    assert me.tensor_type("Q4_K_M", "norm", 0, 30, 576) == "F32"


def test_the_kv_cache_of_a_context_by_its_type():
    shape = shape_of("Llama 3.2 1B Instruct")
    f16 = me.kv_bytes(shape, 2048, "f16")
    assert f16 == 16 * 8 * 64 * 2 * 2048 * 2
    assert me.kv_bytes(shape, 2048, "q8_0") == int(f16 * 1.0625 / 2)
    assert me.kv_bytes(me.ModelShape(parameters=10 ** 9), 2048) == 0
    # the context: the board measurement's floor, never above the training context, or the request's
    assert me.context_for(shape) == 2048 and me.context_for(shape, 4096) == 4096
    assert me.context_for(me.ModelShape(layers=1, kv_heads=1, head_dim=1, train_ctx=1024)) == 1024


def _gguf(path: Path, arch: str, values: dict, tokens: int) -> Path:
    def string(text):
        data = text.encode()
        return struct.pack("<Q", len(data)) + data
    body = b""
    for key, value in values.items():
        body += string(f"{arch}.{key}") + struct.pack("<I", 4) + struct.pack("<I", value)
    body += string("general.architecture") + struct.pack("<I", 8) + string(arch)
    body += string("tokenizer.ggml.tokens") + struct.pack("<I", 9) + struct.pack("<IQ", 8, tokens)
    body += b"".join(string("t") for _ in range(tokens))
    path.write_bytes(b"GGUF" + struct.pack("<IQQ", 3, 0, len(values) + 2) + body)
    return path


def test_a_local_gguf_file_and_a_local_folder_give_the_shape(tmp_path):
    gguf = _gguf(tmp_path / "m.gguf", "llama", {"embedding_length": 576, "block_count": 30,
                                                 "attention.head_count": 9, "attention.head_count_kv": 3,
                                                 "feed_forward_length": 1536, "context_length": 8192}, 64)
    shape = me.shape_from_local(gguf)
    assert (shape.hidden, shape.layers, shape.kv_heads, shape.head_dim, shape.vocab) == (576, 30, 3, 64, 64)
    assert shape.detailed and not shape.tied and shape.train_ctx == 8192
    folder = tmp_path / "model"
    folder.mkdir()
    (folder / "config.json").write_text(json.dumps(MODELS["SmolLM2 135M Instruct"]["config"]), encoding="utf-8")
    (folder / "model.safetensors.index.json").write_text(json.dumps({"metadata": {"total_size": 269030016}}),
                                                         encoding="utf-8")
    local = me.shape_from_local(folder)
    assert local.detailed and local.parameters == 134515008 and local.source == "the model folder's config.json"
    assert me.shape_from_local(tmp_path / "missing") is None


# --- one verdict before and after the build ---------------------------------------------------------------------------

@pytest.mark.parametrize("name, quant, ram", [("SmolLM2 135M Instruct", "IQ4_XS", 416),
                                               ("Llama 3.2 1B Instruct", "IQ4_XS", 1024),
                                               ("Llama 3.2 1B Instruct", "IQ4_XS", 416),
                                               ("Llama 3.2 1B Instruct", "Q8_0", 4096),
                                               ("Llama 3.1 8B Instruct", "Q4_K_M", 8192),
                                               ("Llama 3.1 8B Instruct", "Q4_K_M", 16384)])
def test_the_estimate_and_the_warning_after_the_build_never_disagree(name, quant, ram):
    """The real file equal to the estimated weights and the same KV cache: the warning after the build is there
    exactly when the estimate is tight or does not fit, with the same need; never a refusal either way."""
    est = me.estimate(shape_of(name), quant, board="bcm2837", ram_mib=ram, ram_source="the board's probe")
    assert est.state == bp.memory_state(est.need_mib, ram)
    after = bp.memory_warning(est.weights_bytes, ram, "bcm2837", "the board's probe", kv_bytes=est.kv_bytes,
                              estimate_mib=est.need_mib)
    assert abs(bp.memory_need_mib(est.weights_bytes, est.kv_bytes) - est.need_mib) <= 1
    assert (after is None) == (est.state == "fits")
    if est.state == "does_not_fit":
        assert "Your choice" in after and "your choice" in est.text() and "not stopped" in est.text()


def test_the_states_and_the_text_say_it_is_an_estimate():
    small = me.estimate(shape_of("SmolLM2 135M Instruct"), "IQ4_XS", board="bcm2837", ram_mib=416,
                        ram_source="the board's probe")
    assert small.state == "fits" and small.kv_type == "q8_0" and small.ctx == 2048
    assert "estimate" in small.text() and "benchmark on the board" in small.text()
    big = me.estimate(shape_of("Llama 3.2 1B Instruct"), "IQ4_XS", board="bcm2837", ram_mib=1024,
                      ram_source="the most memory a bcm2837 comes with")
    assert big.state == "tight" and "hardware probe of the board" in big.text()
    roomy = me.estimate(shape_of("Llama 3.2 1B Instruct"), "Q4_K_M", board="bcm2712", ram_mib=16384,
                        ram_source="the most memory a bcm2712 comes with")
    assert roomy.state == "fits" and roomy.kv_type == "f16"
    unknown = me.estimate(None, "Q4_K_M", board="bcm2837", ram_mib=416)
    assert unknown.state == "unknown" and "not known" in unknown.text()
    no_ram = me.estimate(shape_of("Llama 3.2 1B Instruct"), "Q4_K_M", board="x", ram_mib=None)
    assert no_ram.state == "unknown" and no_ram.need_mib and "no hardware probe" in no_ram.text()
    assert "Speicherschätzung" in small.text("de") and "passt" in small.text("de")


# --- where it shows ---------------------------------------------------------------------------------------------------

def hub_facts(parameters: int):
    from orchestrator.Core.hf_lookup import HubFile, ModelFacts
    files = [HubFile("config.json", 700), HubFile("model.safetensors", parameters * 2, "f" * 64)]
    return ModelFacts(repo="example/llama-8b", commit="c" * 40, files=files, staged=files, weights="safetensors",
                      gated="false", license="apache-2.0", license_class="permissive", token_access="not_needed",
                      parameters=parameters)


@pytest.fixture
def config_of(monkeypatch):
    """The model's config.json the estimate reads (instead of huggingface.co): the fixture's config of `name`."""
    from orchestrator.Core import hf_lookup
    chosen = {}
    monkeypatch.setattr(hf_lookup, "model_config", lambda facts, token, fetcher=None: chosen.get("config"))

    def choose(name):
        chosen["config"] = dict(MODELS[name]["config"])
    return choose


def test_the_ai_start_question_shows_the_estimate_and_the_build_starts_with_the_persons_yes(env, config_of):  # noqa: F811
    config_of("Llama 3.1 8B Instruct")
    env.service.hub = hub_facts(MODELS["Llama 3.1 8B Instruct"]["parameters"])
    out, shown = ask(env, {"model": "example/llama-8b", "target": "ARM64", "format": "gguf", "board": "bcm2837",
                           "quantization": "Q4_K_M"})
    text = shown[0]
    assert "Memory estimate for bcm2837" in text and "does not fit" in text and "only with swap" in text
    assert "estimate" in text
    sent = env.orch.requests[-1]
    assert out.request_id and sent.memory_estimate["state"] == "does_not_fit"
    assert sent.memory_estimate["basis"] == "per_tensor" and sent.memory_estimate["quantization"] == "Q4_K_M"


def test_check_build_request_carries_the_boards_warning_as_a_situation_warning(env, config_of):  # noqa: F811
    from orchestrator.schemas.build import StartBuildIn
    from orchestrator.tools.facts_catalog import TOOL_FACTS
    from orchestrator.tools.registry import ToolContext
    config_of("Llama 3.1 8B Instruct")
    env.service.hub = hub_facts(MODELS["Llama 3.1 8B Instruct"]["parameters"])
    out = env.registry.call("check_build_request", {"model": "example/llama-8b", "target": "ARM64", "format": "gguf",
                                                     "board": "bcm2837", "quantization": "Q4_K_M"},
                            ToolContext("ditto"))
    assert out.problems == [] and out.fit.target.state == "does_not_fit"
    assert "target_fit_does_not_fit" in TOOL_FACTS["start_build"].situation
    plan = env.service.plan(StartBuildIn(model="example/llama-8b", target="ARM64", format="gguf", board="bcm2837",
                                         quantization="Q4_K_M"))
    assert any(w.code == "target_fit_does_not_fit" for w in plan.warnings())
    assert plan.bound()["memory_estimate"]["need_mib"] == plan.memory.need_mib


def test_start_build_records_an_estimate_from_this_computer_without_a_look_up(arm64_service, tmp_path):  # noqa: F811
    """A caller that passes no estimate: the service makes it from what is here (a local model folder) and the
    request carries it; a board's probe gives its memory."""
    from orchestrator.Core.build_service import BuildService
    folder = tmp_path / "local-model"
    folder.mkdir()
    (folder / "config.json").write_text(json.dumps(MODELS["Llama 3.2 1B Instruct"]["config"]), encoding="utf-8")
    (folder / "model.safetensors").write_bytes(b"\0" * 16)
    request = BuildService.request_in(model=str(folder), target="ARM64", format="gguf", board="bcm2837",
                                      probe=arm64_service.zero2w)
    arm64_service.start_build(request, local_paths=True)
    (sent,) = arm64_service.framework.orchestrator.requests
    est = sent.memory_estimate
    assert est["state"] == "does_not_fit" and est["ram_mib"] == 416 and est["ram_source"] == "the board's probe"
    assert est["quantization"] == "IQ4_XS" and sent.quantization_options == ["IQ4_XS"]


def test_the_cli_prints_the_estimate_before_the_build_and_in_preflight(booted_framework, tmp_path, monkeypatch,
                                                                       config_of):
    from test_review_capabilities_paths import make_cli
    from orchestrator.Core import hf_lookup
    cli = make_cli(booted_framework, tmp_path, monkeypatch)
    config_of("SmolLM2 135M Instruct")
    monkeypatch.setattr(hf_lookup, "look_up", lambda repo, revision, token, api=None: hub_facts(134515008))
    result = cli.invoke("build", "start", "-m", "HuggingFaceTB/SmolLM2-135M-Instruct", "-t", "arm64", "-b",
                        "bcm2837")
    assert result.code == 0, result.out
    flat = " ".join(result.out.split())
    assert "Memory estimate for bcm2837" in flat and ": fits." in flat and "IQ4_XS" in flat
    assert cli.submitted[-1].memory_estimate["state"] == "fits"
    # a model too large: printed in red words, the build still starts (a warning, never a refusal)
    config_of("Llama 3.1 8B Instruct")
    monkeypatch.setattr(hf_lookup, "look_up", lambda repo, revision, token, api=None: hub_facts(8030261248))
    big = cli.invoke("build", "start", "-m", "example/llama-8b", "-t", "arm64", "-b", "bcm2837", "-q", "Q4_K_M")
    assert big.code == 0, big.out
    assert "does not fit" in " ".join(big.out.split()) and cli.submitted[-1].memory_estimate["state"] == "does_not_fit"
    pre = cli.invoke("build", "preflight", "-t", "arm64", "-b", "bcm2837", "-m", "example/llama-8b", "-q", "Q4_K_M")
    assert pre.code == 0 and "does not fit" in " ".join(pre.out.split())
    as_json = cli.invoke("build", "preflight", "-t", "arm64", "-b", "bcm2837", "-m", "example/llama-8b", "--json")
    data = json.loads(as_json.out[as_json.out.index("{"):])
    assert as_json.code == 0 and data["memory_estimate"]["state"] == "does_not_fit"
    assert data["memory_estimate"]["quantization"] == "IQ4_XS" and "text" in data["memory_estimate"]
    plain = cli.invoke("build", "preflight", "-t", "arm64", "-b", "bcm2837")
    assert plain.code == 0 and "Memory estimate" not in plain.out


@pytest.mark.usefixtures("qapp", "gui_messages")
def test_the_builder_shows_the_estimate_under_the_quantization_and_keeps_it(booted_framework, monkeypatch,
                                                                            config_of):
    from PySide6.QtCore import QCoreApplication
    from test_review_capabilities_paths import make_tab, pick_quant, select, submit

    from orchestrator.Core import hf_lookup
    from orchestrator.Core.build_service import BuildService
    config_of("Llama 3.2 1B Instruct")
    monkeypatch.setattr(hf_lookup, "look_up", lambda repo, revision, token, api=None: hub_facts(1235814400))
    booted_framework.build_service = BuildService(booted_framework)
    tab = make_tab(booted_framework, target="ARM64")

    def settle():
        tab._update_memory_estimate()
        for worker in list(tab._memory_workers):
            worker.wait(10000)
        QCoreApplication.processEvents()
    try:
        select(tab.format_combo, "gguf")
        select(tab.board_combo, "bcm2837")
        settle()
        key, est = tab._memory_shown
        assert est.quantization == "IQ4_XS" and est.state == "tight" and not tab.lbl_memory_estimate.isHidden()
        assert "IQ4_XS" in tab.lbl_memory_estimate.text()
        pick_quant(tab, "Q8_0")
        settle()
        key, est = tab._memory_shown
        assert est.quantization == "Q8_0" and est.state == "does_not_fit" and "Q8_0" in tab.lbl_memory_estimate.text()
        req = submit(tab)
        assert req is not None and req.memory_estimate["state"] == "does_not_fit"   # started: the person decides
        select(tab.board_combo, "bcm2712")
        settle()
        assert tab._memory_shown[1].state == "fits"
    finally:
        tab._build_timer.stop()
        for worker in list(tab._memory_workers):
            worker.wait(10000)


def test_build_info_records_the_estimate_next_to_the_real_size_and_the_warning_counts_its_kv_cache(tmp_path):
    from test_arm64_target import targets_manager

    from orchestrator.Core.builder import BuildEngine, BuildProgress, BuildStatus
    from orchestrator.runtime.profile import ProfileStore, read_profile
    state = tmp_path / "state"
    probe = store(state, "zero", ZERO2W_PROBE)
    ProfileStore(state).save_device(probe, read_profile(ZERO2W_PROBE), label="zero")
    est = me.estimate(shape_of("Llama 3.2 1B Instruct"), "IQ4_XS", board="bcm2837", ram_mib=416,
                      ram_source="the board's probe").as_dict()
    engine = BuildEngine.__new__(BuildEngine)
    engine.config = SimpleNamespace(user_state_dir=str(state))
    engine.target_manager = targets_manager()
    engine.target_manager.initialize()
    real = MODELS["Llama 3.2 1B Instruct"]["gguf_bytes"]["IQ4_XS"]
    config = SimpleNamespace(hardware_probe=probe, target_board="bcm2837", target_arch="ARM64",
                             quantization="IQ4_XS", memory_estimate=est)
    progress = BuildProgress("b", BuildStatus.COMPLETED, "done")
    engine._board_memory_warning(config, {"primary": "m.gguf", "artifacts": [{"file": "m.gguf", "bytes": real}]},
                                 progress)
    (warning,) = progress.warnings
    assert "KV cache" in warning and f"estimated before the build: about {est['need_mib']} MB" in warning
    assert engine._memory_estimate(config) == est
    assert engine._memory_estimate(SimpleNamespace(**{**config.__dict__, "quantization": "Q8_0"})) is None


def test_build_info_json_holds_the_estimate_next_to_the_real_file_size(tmp_path, monkeypatch):
    from test_pipeline import make_config

    from orchestrator.Core.builder import BuildEngine
    from orchestrator.Core.config_manager import ConfigManager
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("orchestrator.Core.builder.docker.from_env",
                        lambda: (_ for _ in ()).throw(RuntimeError("no docker")))
    engine = BuildEngine(ConfigManager(tmp_path / "configs"))
    est = me.estimate(shape_of("SmolLM2 135M Instruct"), "Q4_K_M", board="rk3566", ram_mib=4096,
                      ram_source="the most memory a rk3566 comes with").as_dict()
    out = tmp_path / "out"
    out.mkdir()
    (out / "m-Q4_K_M.gguf").write_bytes(b"weights")
    info = engine._write_build_manifest(make_config(memory_estimate=est), out)
    assert info["memory_estimate"] == {**est, "real_model_bytes": 7}
    assert json.loads((out / "build_info.json").read_text(encoding="utf-8"))["memory_estimate"]["need_mib"] == \
        est["need_mib"]
    other = tmp_path / "other"
    other.mkdir()
    (other / "m-Q4_K_M.gguf").write_bytes(b"weights")
    assert "memory_estimate" not in engine._write_build_manifest(make_config(), other)
