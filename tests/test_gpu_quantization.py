"""Where a format quantizes (DESIGN "Targets, hardware and templates", the row "Ditto's switch, and Ditto as the pilot in
the module wizard", step 2): every format says whether its converter quantizes on the CPU or on the GPU, and for the GPU
whether the whole model goes into graphics memory or it is calibrated layer by layer (AWQ, GPTQ, EXL3), so the wizard
can make a target for such a format before any target uses one, and the check before a build compares this computer's
graphics memory (orchestrator/Core/disk_plan.py gpu_fit, Core/build_resources.py)."""

from types import SimpleNamespace

import pytest

from orchestrator.Core import build_resources, disk_plan
from orchestrator.Core.memory_estimate import ModelShape
from orchestrator.Core.template_values import format_fields
from orchestrator.schemas.target_template import PRESETS, FormatVariables
from orchestrator.schemas.targets import FormatCapability

GIB = 1024 ** 3
# A dense 8B model (Llama 3.1 8B's config.json) and a 70B one (Llama 3.1 70B's).
SHAPE_8B = ModelShape(parameters=8_030_000_000, hidden=4096, layers=32, heads=32, kv_heads=8, head_dim=128,
                      intermediate=14336, vocab=128256, tied=False)
SHAPE_70B = ModelShape(parameters=70_550_000_000, hidden=8192, layers=80, heads=64, kv_heads=8, head_dim=128,
                       intermediate=28672, vocab=128256, tied=False)


def awq(**extra):
    return {"id": "awq", "label": "AWQ (GPU)", "converter": {"command": "scripts/awq_quantize.py"}, "input": ["hf"],
            "output_extension": "safetensors", "quantizations": ["w4_g128"], "default_quantization": "w4_g128",
            "quantize_on": "gpu", "gpu_memory": "layer", **extra}


class TestTheFormatSaysWhereItQuantizes:
    def test_the_presets_quantize_on_the_cpu(self):
        for name, values in PRESETS.items():
            fmt = FormatVariables.model_validate(values)
            assert fmt.quantize_on == "cpu" and "quantize_on" not in fmt.capability(), name

    def test_a_gpu_format_carries_it_into_the_targets_capabilities(self):
        cap = FormatVariables.model_validate(awq()).capability()
        assert (cap["quantize_on"], cap["gpu_memory"]) == ("gpu", "layer")
        assert FormatCapability.model_validate(cap).gpu_memory == "layer"
        plain = FormatVariables.model_validate(awq(gpu_memory=None)).capability()
        assert plain["gpu_memory"] == "model", "gpu without a mode: the whole model"

    @pytest.mark.parametrize("values, why", [
        (awq(quantize_on="cpu"), "gpu_memory is set but quantize_on is not gpu"),
        ({"id": "gguf", "label": "GGUF", "converter": {"command": "llama.cpp"}, "input": ["hf"],
          "output_extension": "gguf", "quantizations": ["Q4_K_M"], "default_quantization": "Q4_K_M",
          "quantize_on": "gpu"}, "the llama.cpp converter quantizes on the CPU"),
    ])
    def test_contradictions_are_refused(self, values, why):
        with pytest.raises(ValueError, match=why):
            FormatVariables.model_validate(values)

    def test_the_capabilities_refuse_a_mode_without_the_gpu(self):
        with pytest.raises(ValueError, match="gpu_memory is set but quantize_on is not gpu"):
            FormatCapability.model_validate({"label": "x", "quantizations": ["a"], "default_quantization": "a",
                                             "gpu_memory": "layer"})

    def test_the_wizards_form_has_both_fields(self):
        fields = {f.path: f for f in format_fields()}
        assert fields["quantize_on"].choices == ("cpu", "gpu") and fields["gpu_memory"].choices == ("model", "layer")
        assert fields["quantize_on"].help and fields["gpu_memory"].help


class TestTheGraphicsMemory:
    def test_layer_by_layer_fits_a_24_gb_gpu_for_8b_and_70b_and_not_a_6_gb_one(self):
        big = disk_plan.gpu_fit("layer", SHAPE_8B.parameters, SHAPE_8B, 24 * GIB, True)
        assert big.state == "fits" and 6 * GIB < big.needed < 9 * GIB
        assert disk_plan.gpu_fit("layer", SHAPE_70B.parameters, SHAPE_70B, 24 * GIB, True).state != "does_not_fit"
        small = disk_plan.gpu_fit("layer", SHAPE_8B.parameters, SHAPE_8B, 6 * GIB, True)
        assert small.state == "does_not_fit" and "128 samples x 2048 tokens" in small.source

    def test_the_whole_model_needs_its_16_bit_size(self):
        fit = disk_plan.gpu_fit("model", SHAPE_8B.parameters, None, 16 * GIB, True)
        assert fit.state == "does_not_fit" and fit.needed > 16 * GIB
        assert disk_plan.gpu_fit(None, SHAPE_8B.parameters, None, 48 * GIB, True).state == "fits"

    def test_no_gpu_no_probe_or_no_shape(self):
        assert disk_plan.gpu_fit("layer", 1, SHAPE_8B, None, True).state == "does_not_fit"
        assert disk_plan.gpu_fit("layer", 1, SHAPE_8B, 24 * GIB, False).state == "unknown"
        assert disk_plan.gpu_fit("layer", 1, None, 24 * GIB, True).state == "unknown"
        assert disk_plan.gpu_fit("model", None, None, 24 * GIB, True).state == "unknown"

    def test_a_layer_is_read_from_the_shape_or_from_the_parameters(self):
        detailed = disk_plan.layer_bytes(SHAPE_8B)
        assert 400e6 < detailed < 480e6
        rough = disk_plan.layer_bytes(ModelShape(parameters=SHAPE_8B.parameters, hidden=4096, layers=32,
                                                 vocab=128256, tied=False))
        assert 0.8 * detailed < rough < 1.2 * detailed
        assert disk_plan.layer_bytes(ModelShape(parameters=1)) is None


class TestTheCheckBeforeABuild:
    def check(self, tmp_path, vram, quantize_on="gpu"):
        hw = SimpleNamespace(ram_total=64 * GIB)
        return build_resources.check(size=16 * GIB, cached=0, fmt="awq", quantization="w4_g128", use_imatrix=False,
                                     cache_dir=tmp_path, output_dir=tmp_path, parameters=SHAPE_8B.parameters,
                                     hardware=hw, vram=vram, quantize_on=quantize_on, gpu_memory="layer",
                                     shape=SHAPE_8B, disk_usage=lambda p: SimpleNamespace(total=1000 * GIB, used=500 * GIB,
                                                                                     free=500 * GIB))

    def test_a_gpu_format_checks_the_graphics_memory(self, tmp_path):
        short = self.check(tmp_path, 4 * GIB)
        assert short.gpu_state == "does_not_fit" and short.risky and short.state == "does_not_fit"
        assert any(line.startswith("Graphics memory of this computer") for line in short.lines())
        assert short.as_dict()["gpu"] == "does_not_fit"
        assert self.check(tmp_path, 24 * GIB).gpu_state == "fits"

    def test_a_cpu_format_has_no_gpu_line(self, tmp_path):
        check = self.check(tmp_path, None, quantize_on="cpu")
        assert check.gpu is None and check.gpu_state is None
        assert not any("Graphics memory" in line for line in check.lines())

    def test_the_service_reads_the_format_from_the_targets_capabilities(self, monkeypatch):
        from orchestrator.Core.build_service import BuildService
        service = BuildService.__new__(BuildService)
        service.framework = SimpleNamespace(config=None)
        cap = SimpleNamespace(quantize_on="gpu", gpu_memory="layer")
        monkeypatch.setattr(service, "capabilities", lambda target: SimpleNamespace(formats={"awq": cap}), raising=False)
        monkeypatch.setattr(service, "model_shape", lambda *a, **k: SHAPE_8B, raising=False)
        monkeypatch.setattr(service, "host_hardware", lambda: SimpleNamespace(ram_total=64 * GIB, gpus=[]),
                            raising=False)
        monkeypatch.setattr(service, "local_copy", lambda *a, **k: None, raising=False)
        monkeypatch.setattr(service, "disk_ledger", lambda: None, raising=False)
        seen = {}
        monkeypatch.setattr(build_resources, "check", lambda **kw: seen.update(kw) or "checked")
        request = SimpleNamespace(model="org/model-8B", revision=None, format="awq", target="Gpu", quantization="w4",
                                  use_imatrix=False, imatrix_file=None, use_gpu=False)
        assert service.resource_check(request, look_up_hub=False) == "checked"
        assert (seen["quantize_on"], seen["gpu_memory"], seen["shape"]) == ("gpu", "layer", SHAPE_8B)
        cap.quantize_on, cap.gpu_memory = "cpu", None
        service.resource_check(request, look_up_hub=False)
        assert (seen["quantize_on"], seen["shape"]) == ("cpu", None), "the shape is read only when needed"
