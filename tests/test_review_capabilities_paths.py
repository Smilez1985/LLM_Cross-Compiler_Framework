"""Regression tests for the Phase 1+2 review fixes: capabilities, request normalization, GUI builder tab, output paths.

Findings in handover/2026-10-10/review-phase1-2-findings.json (index in "kept"):
18/27 rkllm without a board validated against every board, built for rk3588; 19/26/33 IMatrix without an own
dataset refused; 20/37 `-o` and the deploy commands use different folders; 21 '~' in output_dir; 28/38 upper-case
boards refused; 29 aliases the module scripts accept refused; 30 GUI submits no quantization for an empty field;
31 GUI board not editable for targets without capabilities; 32 stale default after a target refresh.
Follow-up round: the CLI resolves target names case-insensitively and refuses unknown ones; a target without
capabilities gets neither another target's board nor its default quantization.
"""

import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

from conftest import REPO_ROOT
from orchestrator.Core.builder import ModelFormat
from orchestrator.schemas.build import BuildRequestIn
from orchestrator.schemas.targets import TargetCapabilities
from test_foundation import FakeOrchestrator
from test_pipeline import FakeBuildEngine

ROCKCHIP_DIR = REPO_ROOT / "targets" / "Rockchip"
MODULES_DIR = ROCKCHIP_DIR / "modules"
HF_MODEL = "HuggingFaceTB/SmolLM2-135M-Instruct"


def rockchip_capabilities_dict():
    return yaml.safe_load((ROCKCHIP_DIR / "target.yml").read_text(encoding="utf-8"))["capabilities"]


@pytest.fixture(scope="module")
def rockchip():
    return TargetCapabilities.model_validate(rockchip_capabilities_dict())


def no_docker(monkeypatch):
    monkeypatch.setattr("orchestrator.Core.builder.docker.from_env",
                        lambda: (_ for _ in ()).throw(RuntimeError("no docker")))


# ---------------------------------------------------------------------------------------------------------------
# Capabilities: normalize() and check_request() for the shipped Rockchip target
# ---------------------------------------------------------------------------------------------------------------

# (format, board, what the user typed, canonical type) - every alias spelling of build.sh, rkllm_module.sh and
# rknn_module.sh, in several letter cases, on a board where the canonical type is allowed.
ALIAS_CASES = [
    ("gguf", "rk3588", "INT8", "Q8_0"), ("gguf", "rk3588", "int8", "Q8_0"), ("gguf", "rk3566", "Q8", "Q8_0"),
    ("gguf", "rk3568", "q8", "Q8_0"), ("gguf", "rk3588", "FP16", "F16"), ("gguf", "rk3566", "fp16", "F16"),
    ("gguf", "rk3576", "FP32", "F32"), ("gguf", "rk3588", "Fp32", "F32"), ("gguf", "rk3588", "Q4", "Q4_K_M"),
    ("gguf", "rk3566", "q4", "Q4_K_M"), ("gguf", "rk3588", "INT4", "Q4_K_M"), ("gguf", "rk3588", "Q5", "Q5_K_M"),
    ("gguf", "rk3576", "q5", "Q5_K_M"),
    ("rkllm", "rk3588", "int8", "w8a8"), ("rkllm", "rk3588", "INT8", "w8a8"), ("rkllm", "rk3588", "q8", "w8a8"),
    ("rkllm", "rk3576", "Q8_0", "w8a8"), ("rkllm", "rk3576", "int4", "w4a16"), ("rkllm", "rk3576", "Q4", "w4a16"),
    ("rkllm", "rk3576", "q4_0", "w4a16"), ("rkllm", "rk3576", "Q4_K_M", "w4a16"),
    ("rknn", "rk3588", "int8", "i8"), ("rknn", "rk3566", "INT8", "i8"), ("rknn", "rk3576", "q8", "i8"),
    ("rknn", "rk3568", "Q8_0", "i8"), ("rknn", "rk3588", "f16", "fp16"), ("rknn", "rk3566", "F16", "fp16"),
]


class TestAliases:

    @pytest.mark.parametrize("fmt, board, given, canonical", ALIAS_CASES)
    def test_alias_is_normalized_to_the_listed_type(self, rockchip, fmt, board, given, canonical):
        assert rockchip.normalize(fmt, board, given) == (fmt, board, canonical)

    @pytest.mark.parametrize("fmt, board, given, canonical", ALIAS_CASES)
    def test_alias_requests_are_valid(self, rockchip, fmt, board, given, canonical):
        # i8 needs its sample list; everything else needs nothing extra
        assert rockchip.check_request(fmt, board, given, dataset=(canonical == "i8")) == []

    def test_every_declared_alias_resolves_in_any_letter_case(self, rockchip):
        for fmt, cap in rockchip.formats.items():
            assert cap.aliases, f"{fmt} declares no aliases"
            for alias, target in cap.aliases.items():
                for spelling in (alias, alias.upper(), alias.lower(), f"  {alias}  "):
                    assert cap.canonical(spelling) == target, (fmt, spelling)

    def test_listed_types_win_over_aliases_and_are_case_insensitive(self, rockchip):
        gguf = rockchip.formats["gguf"]
        assert gguf.canonical("q4_0") == "Q4_0", "Q4_0 is a GGUF type, not the rkllm alias"
        assert gguf.canonical("bf16") == "BF16" and gguf.canonical("iq4_xs") == "IQ4_XS"
        assert rockchip.formats["rkllm"].canonical("W4A16_G128") == "w4a16_g128"
        assert gguf.canonical("Q9_9") is None and gguf.canonical("") is None and gguf.canonical(None) is None

    def test_an_alias_that_maps_to_a_type_the_board_lacks_is_still_refused(self, rockchip):
        problems = rockchip.check_request("rkllm", "rk3588", "int4")
        assert len(problems) == 1 and "'w4a16'" in problems[0] and "rk3588" in problems[0], problems


class TestBoardsAndFormats:

    @pytest.mark.parametrize("fmt, board, quant, expected", [
        ("GGUF", "RK3566", "q4_0", ("gguf", "rk3566", "Q4_0")),
        ("Gguf", "Rk3568", None, ("gguf", "rk3568", "Q4_0")),
        ("RkLlM", "RK3576", None, ("rkllm", "rk3576", "w4a16")),
        ("RKLLM", "rk3588", "W8A8_G128", ("rkllm", "rk3588", "w8a8_g128")),
        ("RKNN", " rk3588 ", "FP16", ("rknn", "rk3588", "fp16")),
        ("gguf", "\trk3576\n", " q8_0 ", ("gguf", "rk3576", "Q8_0")),
    ])
    def test_formats_boards_and_types_are_case_insensitive(self, rockchip, fmt, board, quant, expected):
        assert rockchip.normalize(fmt, board, quant) == expected
        assert rockchip.check_request(fmt, board, quant) == []

    def test_canonical_board_and_format(self, rockchip):
        assert rockchip.canonical_board("RK3588") == "rk3588"
        assert rockchip.canonical_board(" Rk3566 ") == "rk3566"
        assert rockchip.canonical_board("rk9999") is None and rockchip.canonical_board("") is None
        assert rockchip.canonical_format("RKNN") == "rknn" and rockchip.canonical_format("tflite") is None

    def test_default_board_matches_the_build_script(self, rockchip):
        assert rockchip.default_board == "rk3588"

    @pytest.mark.parametrize("fmt, board, expected", [
        ("gguf", None, ("gguf", "rk3588", "Q4_K_M")),
        ("gguf", "", ("gguf", "rk3588", "Q4_K_M")),
        ("rkllm", None, ("rkllm", "rk3588", "w8a8")),
        ("rknn", None, ("rknn", "rk3588", "fp16")),
        ("RKLLM", None, ("rkllm", "rk3588", "w8a8")),
    ])
    def test_multi_board_formats_use_the_default_board(self, rockchip, fmt, board, expected):
        assert len(rockchip.boards_for(expected[0])) > 1
        assert rockchip.normalize(fmt, board, None) == expected

    @pytest.mark.parametrize("quant", ["w4a16", "W4A16", "w4a16_g32", "w4a16_g128", "int4", "q4_k_m"])
    def test_rkllm_w4a16_without_a_board_is_refused(self, rockchip, quant):
        # Without a board build.sh builds for rk3588, where w4a16 does not exist (finding 18/27).
        problems = rockchip.check_request("rkllm", None, quant)
        assert problems and all("rk3588" in p for p in problems), problems
        assert rockchip.check_request("rkllm", "rk3576", quant) == []

    def test_rkllm_without_a_board_accepts_what_rk3588_supports(self, rockchip):
        for quant in ("w8a8", "w8a8_g256", "int8", None):
            assert rockchip.check_request("rkllm", None, quant) == [], quant

    def test_rkllm_on_a_cpu_only_board_is_refused_whatever_the_case(self, rockchip):
        problems = rockchip.check_request("RKLLM", "RK3566", None)
        assert any("does not run on rk3566" in p for p in problems), problems

    def test_unknown_values_pass_through_and_are_reported(self, rockchip):
        assert rockchip.normalize("gguf", "RK9999", "zzz") == ("gguf", "RK9999", "zzz")
        problems = rockchip.check_request("gguf", "RK9999", "zzz")
        assert any("Unknown board 'RK9999'" in p for p in problems), problems
        assert any("Quantization 'zzz'" in p for p in problems), problems
        assert rockchip.normalize("tflite", "rk3588", "x") == ("tflite", "rk3588", "x")
        assert any("not available" in p for p in rockchip.check_request("tflite", None, None))

    def test_normalize_is_idempotent(self, rockchip):
        # check_request normalizes again what CLI, GUI and BuildService already normalized.
        for fmt, board, given, _ in ALIAS_CASES + [("gguf", None, None, None), ("RKLLM", "RK3576", "W4A16", None)]:
            once = rockchip.normalize(fmt, board, given)
            assert rockchip.normalize(*once) == once, (fmt, board, given)

    def test_defaults_never_leave_the_type_empty(self, rockchip):
        # The scripts' fallback for an empty QUANTIZATION (F16 for GGUF, w8a8 for RKLLM) is never reached.
        for fmt in rockchip.formats:
            for board in [None, *rockchip.boards_for(fmt)]:
                _, resolved_board, quant = rockchip.normalize(fmt, board, None)
                assert quant and quant in rockchip.formats[fmt].quantizations_for(resolved_board), (fmt, board, quant)
                assert rockchip.check_request(fmt, board, None, dataset=quant == "i8") == [], (fmt, board)


class TestImatrix:

    @pytest.mark.parametrize("quant", ["IQ1_S", "IQ1_M", "IQ2_XXS", "IQ2_XS", "iq2_s"])
    def test_imatrix_without_an_own_dataset_is_valid(self, rockchip, quant):
        # The framework uses its shipped calibration text (finding 19/26/33).
        assert rockchip.check_request("gguf", "rk3588", quant, dataset=False, imatrix=True) == []
        assert rockchip.check_request("gguf", None, quant, dataset=False, imatrix=True) == []

    def test_imatrix_on_ordinary_types_needs_no_dataset_either(self, rockchip):
        assert rockchip.check_request("gguf", "rk3566", "Q4_K_M", imatrix=True) == []
        assert rockchip.check_request("gguf", "rk3566", None, imatrix=True) == []

    def test_needs_imatrix_message_asks_only_for_imatrix(self, rockchip):
        assert rockchip.check_request("gguf", "rk3588", "iq2_xs") == ["IQ2_XS needs an importance matrix: enable IMatrix."]

    @pytest.mark.parametrize("fmt, board", [("rkllm", "rk3576"), ("rknn", "rk3588")])
    def test_imatrix_stays_refused_for_npu_formats(self, rockchip, fmt, board):
        problems = rockchip.check_request(fmt, board, None, imatrix=True, dataset=True)
        assert any("not used by the" in p for p in problems), problems

    def test_rknn_i8_still_needs_its_sample_list(self, rockchip):
        problems = rockchip.check_request("rknn", "rk3588", "int8")
        assert any("needs a calibration dataset (sample list)" in p for p in problems), problems


class TestSchema:

    def caps(self, **changes):
        data = rockchip_capabilities_dict()
        data.update(changes)
        return data

    def test_unknown_default_board_is_rejected(self):
        with pytest.raises(ValidationError, match="default_board 'rk9999' is not one of the boards"):
            TargetCapabilities.model_validate(self.caps(default_board="rk9999"))

    def test_default_board_is_optional(self):
        data = self.caps()
        data.pop("default_board")
        assert TargetCapabilities.model_validate(data).default_board is None

    @pytest.mark.parametrize("fmt, aliases", [
        ("gguf", {"INT8": "Q9_9"}),
        ("gguf", {"X": "INT8"}),  # an alias of an alias is no type
        ("rkllm", {"int4": "w4a8"}),
        ("rknn", {"int8": "I8"}),  # targets are spelled exactly as listed
    ])
    def test_aliases_to_unknown_types_are_rejected(self, fmt, aliases):
        data = self.caps()
        data["formats"][fmt]["aliases"] = aliases
        with pytest.raises(ValidationError, match="aliases point to unknown types"):
            TargetCapabilities.model_validate(data)

    def test_without_default_board_a_multi_board_format_asks_for_a_board(self):
        data = self.caps()
        data.pop("default_board")
        caps = TargetCapabilities.model_validate(data)
        assert caps.normalize("rkllm", None, None)[1] is None
        problems = caps.check_request("rkllm", None, "w8a8")
        assert problems == ["Choose a board for rkllm (rk3588, rk3576): the supported quantizations differ."]

    def test_single_board_format_fills_its_board(self):
        caps = TargetCapabilities.model_validate({
            "boards": {"a": {"label": "A"}, "b": {"label": "B"}},
            "formats": {"npu": {"label": "n", "quantizations": ["x", "y"], "default_quantization": "x",
                                "boards": ["b"], "board_quantizations": {"b": ["y"]},
                                "board_default_quantization": {"b": "y"}}}})
        assert caps.normalize("NPU", None, None) == ("npu", "b", "y")
        assert caps.check_request("npu", None, None) == []

    def test_mixed_case_board_ids_work_through_the_canonical_spelling(self):
        caps = TargetCapabilities.model_validate({
            "boards": {"Orin": {"label": "Jetson Orin"}},
            "formats": {"gguf": {"label": "g", "quantizations": ["Q4_0"], "default_quantization": "Q4_0"}}})
        assert caps.normalize("gguf", "orin", None) == ("gguf", "Orin", "Q4_0")
        assert caps.check_request("gguf", "ORIN", None) == []


# ---------------------------------------------------------------------------------------------------------------
# The capabilities block agrees with the scripts that really run in the container
# ---------------------------------------------------------------------------------------------------------------

def _script(name):
    return (MODULES_DIR / name).read_text(encoding="utf-8")


def _block(text, start, end="esac"):
    begin = text.index(start)
    return text[begin:text.index(end, begin)]


def _case_arms(block):
    """{pattern: value} of `a|b) echo "X" ;;` and `a|b) VAR="X" ;;` arms; `*)` and die-arms are skipped."""
    arms = {}
    for patterns, echoed, assigned in re.findall(
            r'^\s*([^\s()#*][^)\n]*)\)\s*(?:echo\s+"([^"$]*)"|[A-Z_]+="([^"$]*)")\s*;;', block, re.MULTILINE):
        for pattern in patterns.split("|"):
            arms[pattern.strip().strip('"')] = echoed or assigned
    return arms


def _script_aliases(fmt):
    if fmt == "gguf":
        arms = _case_arms(_block(_script("build.sh"), "normalize_gguf_type() {", "\n}"))
    elif fmt == "rkllm":
        arms = _case_arms(_block(_script("rkllm_module.sh"), 'case "$Q" in'))
    else:
        arms = _case_arms(_block(_script("rknn_module.sh"), 'case "$(echo "${QUANTIZATION:-fp16}"'))
    return {pattern: value for pattern, value in arms.items() if pattern}


class TestScriptConsistency:

    @pytest.mark.parametrize("script", ["build.sh", "rkllm_module.sh", "rknn_module.sh"])
    def test_scripts_default_board_is_the_declared_default_board(self, rockchip, script):
        match = re.search(r'BOARD="\$\(echo "\$\{TARGET_BOARD:-([^}]*)\}"', _script(script))
        assert match, f"no TARGET_BOARD default in {script}"
        assert match.group(1) == rockchip.default_board

    def test_parser_sees_every_case_arm(self):
        # Guards the parser below: if the scripts change shape, these tests must not pass vacuously.
        assert set(_script_aliases("gguf")) >= {"F16", "FP16", "F32", "FP32", "INT8", "Q8", "INT4", "Q4", "Q5", "BF16"}
        assert set(_script_aliases("rkllm")) == {"int8", "q8", "q8_0", "int4", "q4", "q4_0", "q4_k_m"}
        assert set(_script_aliases("rknn")) == {"i8", "int8", "q8", "q8_0", "fp16", "f16"}

    @pytest.mark.parametrize("fmt", ["gguf", "rkllm", "rknn"])
    def test_every_declared_alias_maps_to_the_scripts_type(self, rockchip, fmt):
        script = {pattern.casefold(): value for pattern, value in _script_aliases(fmt).items()}
        for alias, target in rockchip.formats[fmt].aliases.items():
            assert alias.casefold() in script, f"{fmt}: alias {alias} is not accepted by the module script"
            assert script[alias.casefold()] == target, f"{fmt}: {alias} -> {target}, script -> {script[alias.casefold()]}"

    @pytest.mark.parametrize("fmt", ["gguf", "rkllm", "rknn"])
    def test_every_spelling_the_script_accepts_is_valid(self, rockchip, fmt):
        cap = rockchip.formats[fmt]
        for pattern, value in _script_aliases(fmt).items():
            assert value in cap.quantizations, f"{fmt}: the script maps {pattern} to {value}, which is not listed"
            assert cap.canonical(pattern) == value, f"{fmt}: the validator does not accept {pattern} ({value})"

    def test_rkllm_dispatch_boards_match_the_capabilities(self, rockchip):
        dispatch = re.search(r'case "\$BOARD" in\s*\n\s*([^)\n]*)\)\s*/app/modules/rkllm_module\.sh', _script("build.sh"))
        assert dispatch
        assert {p.strip("*") for p in dispatch.group(1).split("|")} == set(rockchip.boards_for("rkllm"))
        platforms = set(re.findall(r'\*(rk\d+)\*\)\s*PLATFORM="(rk\d+)"', _script("rkllm_module.sh")))
        assert {p for p, _ in platforms} == {q for _, q in platforms} == set(rockchip.boards_for("rkllm"))


# ---------------------------------------------------------------------------------------------------------------
# BuildService.validate / start_build / list_artifacts
# ---------------------------------------------------------------------------------------------------------------

@pytest.fixture
def service(booted_framework):
    from orchestrator.Core.build_service import BuildService
    booted_framework.orchestrator = FakeOrchestrator()
    return BuildService(booted_framework)


def build_in(**overrides):
    values = dict(model=HF_MODEL, target="Rockchip", format="gguf")
    values.update(overrides)
    return BuildRequestIn(**values)


class TestBuildServiceValidate:

    @pytest.mark.parametrize("fmt, board, quant, expected", [
        ("GGUF", "RK3566", None, ("gguf", "rk3566", "Q4_0")),
        ("gguf", "RK3588", "INT8", ("gguf", "rk3588", "Q8_0")),
        ("gguf", None, None, ("gguf", "rk3588", "Q4_K_M")),
        ("gguf", None, "fp16", ("gguf", "rk3588", "F16")),
        ("rkllm", None, None, ("rkllm", "rk3588", "w8a8")),
        ("RKLLM", "Rk3576", "INT4", ("rkllm", "rk3576", "w4a16")),
        ("rkllm", "rk3576", None, ("rkllm", "rk3576", "w4a16")),
        ("rknn", "rk3566", "F16", ("rknn", "rk3566", "fp16")),
        ("rknn", None, None, ("rknn", "rk3588", "fp16")),
    ])
    def test_validate_returns_the_normalized_values(self, service, fmt, board, quant, expected):
        req = service.validate(build_in(format=fmt, board=board, quantization=quant))
        assert (req.format, req.board, req.quantization) == expected

    @pytest.mark.parametrize("overrides, message", [
        (dict(format="rkllm", quantization="w4a16"), "rk3588"),
        (dict(format="rkllm", quantization="int4"), "rk3588"),
        (dict(format="rkllm", board="RK3566"), "does not run on rk3566"),
        (dict(format="rknn", board="rk3588", quantization="int8"), "needs a calibration dataset"),
        (dict(format="gguf", board="rk9999"), "Unknown board"),
        (dict(format="gguf", quantization="IQ2_XS"), "enable IMatrix"),
        (dict(format="rkllm", board="rk3576", use_imatrix=True), "not used by the rkllm"),
    ])
    def test_validate_refuses_what_the_build_would_reject(self, service, overrides, message):
        from orchestrator.Core.build_service import BuildServiceError
        with pytest.raises(BuildServiceError, match=message):
            service.validate(build_in(**overrides))
        assert service.framework.orchestrator.submitted == []

    def test_imatrix_without_own_dataset_is_accepted(self, service):
        req = service.validate(build_in(board="rk3588", quantization="iq2_xs", use_imatrix=True))
        assert (req.quantization, req.use_imatrix, req.dataset_path) == ("IQ2_XS", True, None)

    def test_rknn_alias_with_a_sample_list(self, service, tmp_path):
        # The service serves AI callers, which name the dataset inside the datasets folder (it took a path before).
        (tmp_path / "datasets").mkdir()
        samples = tmp_path / "datasets" / "samples.txt"
        samples.write_text("img.jpg\n")
        req = service.validate(build_in(format="rknn", board="RK3576", quantization="INT8",
                                        dataset_path="datasets:samples.txt"))
        assert (req.board, req.quantization, req.dataset_path) == ("rk3576", "i8", str(samples.resolve()))
        # A person's own caller (GUI, CLI, a script) may still name the file by its path.
        req = service.validate(build_in(format="rknn", board="RK3576", quantization="INT8",
                                        dataset_path=str(samples)), local_paths=True)
        assert req.dataset_path == str(samples.resolve())

    @pytest.mark.parametrize("fmt, board, quant, expected", [
        ("gguf", "RK3566", "int8", (ModelFormat.GGUF, "rk3566", ["Q8_0"])),
        ("rkllm", None, None, (ModelFormat.RKLLM, "rk3588", ["w8a8"])),
        ("RKLLM", "RK3576", None, (ModelFormat.RKLLM, "rk3576", ["w4a16"])),
        ("rknn", None, "f16", (ModelFormat.RKNN, "rk3588", ["fp16"])),
    ])
    def test_start_build_submits_exactly_the_validated_values(self, service, fmt, board, quant, expected):
        service.start_build(build_in(format=fmt, board=board, quantization=quant))
        req = service.framework.orchestrator.submitted[0]
        assert (req.target_formats[0], req.target_board, req.quantization_options) == (
            expected[0], expected[1], expected[2])

    def test_start_build_output_is_the_installations_output_folder(self, service, tmp_path, monkeypatch):
        work = tmp_path / "elsewhere"
        work.mkdir()
        monkeypatch.chdir(work)
        service.start_build(build_in(board="rk3588"))
        req = service.framework.orchestrator.submitted[0]
        assert Path(req.output_base_dir) == Path(service.framework.info.installation_path) / "output"

    def test_start_build_expands_home_in_output_dir(self, service):
        service.framework.config.set("output_dir", "~/llm-builds")
        service.start_build(build_in(board="rk3588"))
        req = service.framework.orchestrator.submitted[0]
        assert Path(req.output_base_dir) == Path(os.environ["HOME"]) / "llm-builds"
        assert "~" not in req.output_base_dir


def write_manifest(folder, data, raw=None):
    folder.mkdir(parents=True, exist_ok=True)
    content = raw if raw is not None else json.dumps(data).encode("utf-8")
    (folder / "build_info.json").write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
    return folder


class TestListArtifacts:

    def test_odd_manifests_do_not_hide_the_good_ones(self, service):
        out = service._output_dir()
        write_manifest(out / "Rockchip" / "good" / "Q4_0",
                       {"model": "good", "format": "gguf", "quantization": "Q4_0", "board": "rk3566",
                        "created": "2026-10-10T10:00:00", "primary": "good.gguf",
                        "artifacts": [{"file": "good.gguf", "sha256": "ab"}]})
        write_manifest(out / "Rockchip" / "odd" / "x",
                       {"model": 123, "format": ["gguf"], "quantization": 4, "board": None, "target": {"id": 1},
                        "created": 20261010, "primary": "odd.gguf",
                        "artifacts": [1, "x", None, {"file": "odd.gguf", "sha256": 77}]})
        write_manifest(out / "Rockchip" / "artifacts_is_text" / "y",
                       {"model_source": "org/m", "primary": "m.gguf", "artifacts": "m.gguf"})
        write_manifest(out / "Rockchip" / "broken" / "z", None, raw="{not json")
        write_manifest(out / "Rockchip" / "list" / "z", ["not", "a", "dict"])
        write_manifest(out / "Rockchip" / "binary" / "z", None, raw=b"\xff\xfe{")  # not UTF-8
        items = {Path(a.label.split(":", 1)[1]).parent.name: a for a in service.list_artifacts().artifacts}
        assert set(items) == {"good", "odd", "artifacts_is_text"}
        assert (items["good"].primary, items["good"].sha256, items["good"].board) == ("good.gguf", "ab", "rk3566")
        odd = items["odd"]
        assert (odd.model, odd.quantization, odd.created, odd.sha256) == ("123", "4", "20261010", "77")
        assert odd.board is None and odd.format == "['gguf']"
        assert (items["artifacts_is_text"].model, items["artifacts_is_text"].sha256) == ("org/m", None)

    def test_a_manifest_the_schema_refuses_is_skipped_with_a_warning(self, service, monkeypatch):
        from pydantic import Field

        from orchestrator.Core import build_service as module

        class StrictArtifact(module.ArtifactOut):  # stands in for any schema refusal of one manifest
            model: str = Field(default=None, max_length=20)

        warnings = []
        monkeypatch.setattr(module, "ArtifactOut", StrictArtifact)
        monkeypatch.setattr(service, "logger", SimpleNamespace(warning=warnings.append, info=lambda *a: None))
        out = service._output_dir()
        write_manifest(out / "a", {"model": "short", "primary": "a.gguf"})
        write_manifest(out / "b", {"model": "x" * 200, "primary": "b.gguf"})
        write_manifest(out / "c", {"model": "also-short", "primary": "c.gguf"})
        listed = sorted(Path(a.label.split(":", 1)[1]).name for a in service.list_artifacts().artifacts)
        assert listed == ["a", "c"]
        assert len(warnings) == 1 and "output:b" in warnings[0] and str(out) not in warnings[0]

    def test_relative_output_dir_is_the_installations_not_the_working_directory(self, service, tmp_path, monkeypatch):
        work = tmp_path / "work"
        write_manifest(work / "output" / "cwd-build", {"model": "cwd", "primary": "cwd.gguf"})
        write_manifest(Path(service.framework.info.installation_path) / "output" / "inst-build",
                       {"model": "inst", "primary": "inst.gguf"})
        monkeypatch.chdir(work)
        assert [a.model for a in service.list_artifacts().artifacts] == ["inst"]

    def test_home_in_output_dir_is_expanded(self, service):
        service.framework.config.set("output_dir", "~/llm-builds")
        write_manifest(Path(os.environ["HOME"]) / "llm-builds" / "Rockchip" / "m", {"model": "m", "primary": "m.gguf"})
        assert [a.model for a in service.list_artifacts().artifacts] == ["m"]


# ---------------------------------------------------------------------------------------------------------------
# CLI: build start, deploy list-artifacts, deploy package
# ---------------------------------------------------------------------------------------------------------------

class CliOrchestrator(FakeOrchestrator):
    """Records the request and reports it finished, so `build start` returns right away."""

    async def get_workflow_status(self, request_id):
        from orchestrator.Core.orchestrator import OrchestrationStatus
        state = self._workflows.get(request_id)
        if state is not None and state.status == OrchestrationStatus.QUEUED:
            state.status = OrchestrationStatus.COMPLETED
        return state


class FakeDeploymentManager:
    def __init__(self):
        self.calls = []

    def create_deployment_package(self, **kwargs):
        self.calls.append(kwargs)
        return Path(kwargs["output_dir"]) / "deploy_x.zip"

    def create_package(self, artifact_path, profile_name, docker_config, output_dir, cancelled=None):
        return self.create_deployment_package(artifact_path=artifact_path, profile_name=profile_name,
                                              docker_config=docker_config, output_dir=output_dir)


def framework_with_targets(make_framework, folder, rockchip=True):
    """A framework whose targets are a copy of the shipped Rockchip target.yml (optional) and a module-wizard
    target without a capabilities block (targets/_template has none, and the wizard writes none)."""
    if rockchip:
        (folder / "Rockchip").mkdir(parents=True)
        shutil.copy(ROCKCHIP_DIR / "target.yml", folder / "Rockchip" / "target.yml")
    (folder / "MyJetson").mkdir(parents=True)
    (folder / "MyJetson" / "target.yml").write_text(yaml.safe_dump(
        {"metadata": {"name": "MyJetson", "architecture_family": "aarch64"}}), encoding="utf-8")
    fm = make_framework(initialize=False)
    fm.config.set("targets_dir", str(folder))
    assert fm.initialize()
    assert fm.target_manager.get_target("MyJetson") and fm.target_manager.get_capabilities("MyJetson") is None
    assert (fm.target_manager.get_capabilities("Rockchip") is not None) == rockchip
    return fm


@pytest.fixture
def mixed_framework(make_framework, tmp_path):
    return framework_with_targets(make_framework, tmp_path / "targets_mixed")


def make_cli(framework, tmp_path, monkeypatch):
    from click.testing import CliRunner
    from rich.console import Console

    from orchestrator import cli as cli_module

    orchestrator, deployment = CliOrchestrator(), FakeDeploymentManager()
    printed = io.StringIO()
    # Wide and plain, so table cells and long messages are not wrapped or truncated.
    monkeypatch.setattr(cli_module, "console", Console(file=printed, width=500, color_system=None))
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    monkeypatch.chdir(work)

    class Ctx(cli_module.FrameworkContext):
        def require_framework(self):
            self.framework_manager = framework
            self.orchestrator = orchestrator
            self.deployment_manager = deployment
            return self

    def invoke(*args):
        printed.seek(0)
        printed.truncate()
        result = CliRunner().invoke(cli_module.cli, ["-q", *args], obj=Ctx())
        return SimpleNamespace(code=result.exit_code, out=printed.getvalue() + result.output,
                               exception=result.exception)

    return SimpleNamespace(invoke=invoke, submitted=orchestrator.submitted, packaged=deployment.calls,
                           work=work, framework=framework)


@pytest.fixture
def cli(booted_framework, tmp_path, monkeypatch):
    return make_cli(booted_framework, tmp_path, monkeypatch)


def build_start(cli, *args):
    return cli.invoke("build", "start", "-m", HF_MODEL, "-t", "Rockchip", *args)


class TestCliBuildStart:

    def test_upper_case_board_is_accepted_and_passed_lower_case(self, cli):
        result = build_start(cli, "-f", "gguf", "-b", "RK3588")
        assert result.code == 0, result.out
        assert cli.submitted[0].target_board == "rk3588"
        assert cli.submitted[0].quantization_options == ["Q4_K_M"]

    @pytest.mark.parametrize("args, board, quants", [
        (["-f", "gguf", "-b", "rk3588", "-q", "INT8"], "rk3588", ["Q8_0"]),
        (["-f", "gguf", "-b", "Rk3566"], "rk3566", ["Q4_0"]),
        (["-f", "gguf", "-q", "fp16"], "rk3588", ["F16"]),
        (["-f", "rkllm"], "rk3588", ["w8a8"]),
        (["-f", "rkllm", "-b", "RK3576", "-q", "int4"], "rk3576", ["w4a16"]),
        (["-f", "rknn", "-b", "rk3566", "-q", "F16"], "rk3566", ["fp16"]),
    ])
    def test_submits_the_normalized_values(self, cli, args, board, quants):
        result = build_start(cli, *args)
        assert result.code == 0, result.out
        req = cli.submitted[0]
        assert (req.target_board, req.quantization_options) == (board, quants)

    @pytest.mark.parametrize("args, message", [
        (["-f", "rkllm", "-q", "w4a16"], "not supported for rkllm on rk3588"),
        (["-f", "rkllm", "-b", "rk3588", "-q", "int4"], "not supported for rkllm on rk3588"),
        (["-f", "rkllm", "-b", "rk3566"], "does not run on rk3566"),
        (["-f", "rknn", "-b", "rk3588", "-q", "int8"], "needs a calibration dataset"),
        (["-f", "gguf", "-q", "IQ2_XS"], "enable IMatrix"),
        (["-f", "gguf", "-b", "rk9999"], "Unknown board 'rk9999'"),
    ])
    def test_invalid_requests_exit_2_and_submit_nothing(self, cli, args, message):
        result = build_start(cli, *args)
        assert result.code == 2, result.out
        assert message in result.out
        assert cli.submitted == []

    def test_imatrix_without_dataset_is_submitted(self, cli):
        result = build_start(cli, "-f", "gguf", "-b", "rk3588", "-q", "iq2_xs", "--imatrix")
        assert result.code == 0, result.out
        req = cli.submitted[0]
        assert (req.quantization_options, req.use_imatrix, req.dataset_path) == (["IQ2_XS"], True, None)

    def test_rknn_alias_with_dataset_relative_to_the_working_directory(self, cli):
        (cli.work / "list.txt").write_text("img.jpg\n")
        result = build_start(cli, "-f", "rknn", "-b", "RK3588", "-q", "INT8", "--dataset", "list.txt")
        assert result.code == 0, result.out
        req = cli.submitted[0]
        assert (req.target_board, req.quantization_options) == ("rk3588", ["i8"])
        assert req.dataset_path == str((cli.work / "list.txt").resolve())

    def test_relative_output_dir_means_the_working_directory(self, cli):
        result = build_start(cli, "-o", "mybuilds")
        assert result.code == 0, result.out
        assert cli.submitted[0].output_base_dir == str((cli.work / "mybuilds").resolve())

    def test_dotted_and_home_output_dirs(self, cli):
        assert build_start(cli, "-o", "./a/../results").code == 0
        assert build_start(cli, "-o", "~/llm-out").code == 0
        first, second = cli.submitted
        assert first.output_base_dir == str((cli.work / "results").resolve())
        assert second.output_base_dir == str(Path(os.environ["HOME"]) / "llm-out")

    def test_default_output_lies_under_the_installations_output_dir(self, cli):
        result = build_start(cli)
        assert result.code == 0, result.out
        out = Path(cli.submitted[0].output_base_dir)
        install_output = Path(cli.framework.info.installation_path) / "output"
        assert out.parent == install_output and out.name.startswith("build_")
        assert not out.is_relative_to(cli.work)

    def test_default_output_with_home_in_the_config(self, cli):
        cli.framework.config.set("output_dir", "~/llm-builds")
        assert build_start(cli).code == 0
        out = Path(cli.submitted[0].output_base_dir)
        assert out.parent == Path(os.environ["HOME"]) / "llm-builds" and out.name.startswith("build_")

    @pytest.mark.parametrize("name", ["rockchip", "ROCKCHIP"])
    def test_target_name_in_another_case_is_validated_and_canonical(self, cli, name):
        refused = cli.invoke("build", "start", "-m", HF_MODEL, "-t", name, "-f", "rkllm", "-q", "w4a16")
        assert refused.code == 2 and "not supported for rkllm on rk3588" in refused.out, refused.out
        assert cli.submitted == []
        accepted = cli.invoke("build", "start", "-m", HF_MODEL, "-t", name, "-f", "gguf", "-b", "RK3566")
        assert accepted.code == 0, accepted.out
        req = cli.submitted[0]
        assert (req.targets, req.target_board, req.quantization_options) == (["Rockchip"], "rk3566", ["Q4_0"])

    def test_unknown_target_exits_2_and_submits_nothing(self, cli):
        result = cli.invoke("build", "start", "-m", HF_MODEL, "-t", "NoSuchTarget")
        assert result.code == 2, result.out
        assert "Unknown target 'NoSuchTarget'" in result.out and "Rockchip" in result.out
        assert cli.submitted == []

    def test_target_without_capabilities_gets_a_lower_case_board(self, mixed_framework, tmp_path, monkeypatch):
        cli = make_cli(mixed_framework, tmp_path, monkeypatch)
        result = cli.invoke("build", "start", "-m", HF_MODEL, "-t", "MyJetson", "-b", " Orin-NX ", "-q", "Custom_Q")
        assert result.code == 0, result.out
        req = cli.submitted[0]
        assert (req.target_board, req.quantization_options) == ("orin-nx", ["Custom_Q"])


class TestCliBuildStartSubprocess:
    """The refusal end to end, in a fresh process (env pattern of test_core.TestCLI); nothing is submitted."""

    def test_rkllm_w4a16_without_board_exits_2(self, tmp_path):
        env = dict(os.environ, PYTHONPATH=str(REPO_ROOT), PYTHON_KEYRING_BACKEND="keyring.backends.null.Keyring",
                   LLM_SECRET_STORAGE="ephemeral",
                   LLM_FRAMEWORK_HOME=str(tmp_path / "state"), LLM_FRAMEWORK_OUTPUT_DIR=str(tmp_path / "output"),
                   LLM_FRAMEWORK_CACHE_DIR=str(tmp_path / "cache"), LLM_FRAMEWORK_MODELS_DIR=str(tmp_path / "models"),
                   COLUMNS="300")
        result = subprocess.run([sys.executable, "-m", "orchestrator.cli", "-q", "build", "start", "-m", HF_MODEL,
                                 "-t", "Rockchip", "-f", "rkllm", "-q", "w4a16"],
                                cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120, check=False)
        assert result.returncode == 2, (result.stdout, result.stderr)
        assert "not supported for rkllm on rk3588" in result.stdout
        assert "Traceback" not in result.stderr
        assert not (tmp_path / "output").exists() or not any((tmp_path / "output").rglob("*"))


class TestCliDeploy:

    def install_output(self, cli):
        return Path(cli.framework.info.installation_path) / "output"

    @staticmethod
    def flat(text):
        """Rich wraps the table title at the table's width; paths are compared without the line breaks."""
        return "".join(text.split())

    def test_list_artifacts_uses_the_installations_output_dir(self, cli):
        write_manifest(self.install_output(cli) / "Rockchip" / "SmolLM2" / "Q4_0",
                       {"model": "SmolLM2", "format": "gguf", "quantization": "Q4_0", "board": "rk3566",
                        "primary": "m.gguf"})
        write_manifest(cli.work / "output" / "Rockchip" / "cwd-only" / "Q8_0",
                       {"model": "cwd-only", "format": "gguf", "primary": "c.gguf"})
        result = cli.invoke("deploy", "list-artifacts")
        assert result.code == 0, result.out
        assert f"Buildsin{self.install_output(cli)}" in self.flat(result.out)
        # the folder as a path of this computer (Rockchip\SmolLM2\Q4_0 on Windows), as 'deploy package' takes it
        assert str(Path("Rockchip", "SmolLM2", "Q4_0")) in result.out and "rk3566" in result.out
        assert "cwd-only" not in result.out

    def test_list_artifacts_skips_broken_manifests(self, cli):
        write_manifest(self.install_output(cli) / "good", {"model": "good-model", "primary": "g.gguf"})
        write_manifest(self.install_output(cli) / "bad", None, raw="{oops")
        write_manifest(self.install_output(cli) / "odd", {"model": 7, "format": ["x"], "primary": "o.gguf"})
        result = cli.invoke("deploy", "list-artifacts")
        assert result.code == 0, result.out
        assert "good-model" in result.out and "odd" in result.out

    def test_list_artifacts_without_builds_names_the_folder(self, cli):
        result = cli.invoke("deploy", "list-artifacts")
        assert result.code == 0, result.out
        assert f"Nofinishedbuildsin{self.install_output(cli)}" in self.flat(result.out)

    def test_list_artifacts_expands_home(self, cli):
        cli.framework.config.set("output_dir", "~/llm-builds")
        home_out = Path(os.environ["HOME"]) / "llm-builds"
        write_manifest(home_out / "Rockchip" / "m" / "Q4_0", {"model": "home-model", "primary": "m.gguf"})
        result = cli.invoke("deploy", "list-artifacts")
        assert result.code == 0, result.out
        assert "home-model" in result.out and f"Buildsin{home_out}" in self.flat(result.out), result.out

    def test_package_takes_the_folder_shown_by_list_artifacts(self, cli):
        folder = write_manifest(self.install_output(cli) / "Rockchip" / "SmolLM2" / "Q4_0",
                                {"model": "SmolLM2", "primary": "m.gguf"})
        result = cli.invoke("deploy", "package", "Rockchip/SmolLM2/Q4_0", "--profile", "Rockchip")
        assert result.code == 0, result.out
        call = cli.packaged[0]
        assert Path(call["artifact_path"]) == folder
        assert Path(call["output_dir"]) == self.install_output(cli)
        assert call["profile_name"] == "Rockchip"

    def test_package_accepts_an_absolute_path(self, cli, tmp_path):
        folder = write_manifest(tmp_path / "somewhere" / "build", {"model": "m", "primary": "m.gguf"})
        assert cli.invoke("deploy", "package", str(folder), "--profile", "Rockchip").code == 0
        assert Path(cli.packaged[0]["artifact_path"]) == folder.resolve()
        assert Path(cli.packaged[0]["output_dir"]) == self.install_output(cli)

    def test_package_accepts_a_path_relative_to_the_working_directory(self, cli):
        folder = write_manifest(cli.work / "mybuilds" / "x", {"model": "m", "primary": "m.gguf"})
        assert cli.invoke("deploy", "package", "mybuilds/x", "--profile", "Rockchip").code == 0
        assert Path(cli.packaged[0]["artifact_path"]) == folder.resolve()

    def test_package_output_dir_expands_home(self, cli):
        cli.framework.config.set("output_dir", "~/llm-builds")
        home_out = Path(os.environ["HOME"]) / "llm-builds"
        folder = write_manifest(home_out / "Rockchip" / "m", {"model": "m", "primary": "m.gguf"})
        assert cli.invoke("deploy", "package", "Rockchip/m", "--profile", "Rockchip").code == 0
        assert Path(cli.packaged[0]["artifact_path"]) == folder
        assert Path(cli.packaged[0]["output_dir"]) == home_out


# ---------------------------------------------------------------------------------------------------------------
# '~' in output_dir: orchestrator, BuildEngine and FrameworkManager.resolve_path agree
# ---------------------------------------------------------------------------------------------------------------

class TestHomeInOutputDir:

    @pytest.mark.parametrize("configured", ["~/llm-builds", "~/llm-builds/", "~"])
    def test_resolve_path_and_build_engine_agree(self, make_framework, tmp_path, monkeypatch, configured):
        from orchestrator.Core.builder import BuildEngine
        no_docker(monkeypatch)
        fm = make_framework(initialize=False)
        fm.config.set("output_dir", configured)
        expected = Path(configured).expanduser()
        assert expected.is_absolute() and str(expected).startswith(os.environ["HOME"])
        assert fm.resolve_path(fm.config.get("output_dir")) == expected
        engine = BuildEngine(fm.config, base_dir=tmp_path / "install")
        assert engine.output_dir == expected
        assert "~" not in str(engine.output_dir)
        engine_from_framework = BuildEngine(fm)
        assert engine_from_framework.output_dir == expected

    def test_relative_output_dir_is_the_installations_everywhere(self, make_framework, tmp_path, monkeypatch):
        from orchestrator.Core.builder import BuildEngine
        no_docker(monkeypatch)
        fm = make_framework(initialize=False)
        (tmp_path / "work").mkdir()
        monkeypatch.chdir(tmp_path / "work")
        assert fm.resolve_path("output") == tmp_path / "output"
        assert BuildEngine(fm).output_dir == tmp_path / "output"

    @pytest.fixture
    def orchestrator(self, tmp_path):
        from orchestrator.Core.config_manager import ConfigManager
        from orchestrator.Core.orchestrator import LLMOrchestrator
        cfg = ConfigManager(tmp_path / "configs")
        targets = tmp_path / "install" / "targets"
        (targets / "Rockchip").mkdir(parents=True)
        orch = LLMOrchestrator(cfg)
        orch.build_engine = FakeBuildEngine()
        orch.build_engine.base_dir = tmp_path / "install"
        orch.build_engine.targets_dir = targets
        return orch

    def run_pipeline(self, orch, output_base_dir):
        import asyncio
        from datetime import datetime

        from orchestrator.Core.builder import OptimizationLevel
        from orchestrator.Core.orchestrator import (BuildRequest, OrchestrationStatus, PriorityLevel, WorkflowState,
                                                    WorkflowType)
        req = BuildRequest(request_id="r1", workflow_type=WorkflowType.SIMPLE_CONVERSION, priority=PriorityLevel.NORMAL,
                           models=[HF_MODEL], targets=["Rockchip"], target_formats=[ModelFormat.GGUF],
                           optimization_level=OptimizationLevel.BALANCED, quantization_options=["Q4_0"],
                           parallel_builds=False, output_base_dir=output_base_dir, target_board="rk3566")
        orch._workflows["r1"] = WorkflowState("r1", OrchestrationStatus.PREPARING, datetime.now())
        asyncio.run(orch._run_build_pipeline(req))
        assert orch._workflows["r1"].status == OrchestrationStatus.COMPLETED, orch._workflows["r1"].warnings
        return Path(orch.build_engine.configs[0].output_dir)

    def test_orchestrator_expands_home(self, orchestrator, make_framework):
        out = self.run_pipeline(orchestrator, "~/llm-builds")
        home_builds = Path(os.environ["HOME"]) / "llm-builds"
        assert out == home_builds / "Rockchip" / "SmolLM2-135M-Instruct" / "Q4_0"
        fm = make_framework(initialize=False)
        assert out.is_relative_to(fm.resolve_path("~/llm-builds"))

    def test_orchestrator_anchors_relative_paths_at_the_installation(self, orchestrator, tmp_path):
        out = self.run_pipeline(orchestrator, "output/build_1")
        assert out == tmp_path / "install" / "output" / "build_1" / "Rockchip" / "SmolLM2-135M-Instruct" / "Q4_0"

    def test_orchestrator_keeps_absolute_paths(self, orchestrator, tmp_path):
        out = self.run_pipeline(orchestrator, str(tmp_path / "abs"))
        assert out == tmp_path / "abs" / "Rockchip" / "SmolLM2-135M-Instruct" / "Q4_0"


# ---------------------------------------------------------------------------------------------------------------
# GUI builder tab (offscreen)
# ---------------------------------------------------------------------------------------------------------------

def select(combo, data):
    idx = combo.findData(data)
    assert idx >= 0, (data, [combo.itemData(i) for i in range(combo.count())])
    combo.setCurrentIndex(idx)


def pick_quant(tab, text):
    """What a user does with the list: choose an entry (Qt emits `activated` only for user choices)."""
    idx = tab.quant_combo.findText(text)
    assert idx >= 0, (text, [tab.quant_combo.itemText(i) for i in range(tab.quant_combo.count())])
    tab.quant_combo.setCurrentIndex(idx)
    tab.quant_combo.activated.emit(idx)


def type_text(line_edit, text):
    from PySide6.QtTest import QTest
    line_edit.selectAll()
    line_edit.del_()
    QTest.keyClicks(line_edit, text)


def quant_items(tab):
    return [tab.quant_combo.itemText(i) for i in range(tab.quant_combo.count())]


@pytest.fixture
def gui_messages(monkeypatch):
    from orchestrator.gui import builder_tab
    shown = []

    class RecordingMessageBox:
        @staticmethod
        def warning(parent, title, text, *args):
            shown.append(("warning", text))

        @staticmethod
        def information(parent, title, text, *args):
            shown.append(("information", text))

        @staticmethod
        def critical(parent, title, text, *args):
            shown.append(("critical", text))

    monkeypatch.setattr(builder_tab, "QMessageBox", RecordingMessageBox)
    return shown


def make_tab(framework, target="Rockchip"):
    from orchestrator.gui.builder_tab import BuilderTab
    framework.orchestrator = FakeOrchestrator()
    tab = BuilderTab(framework)
    select(tab.target_combo, target)
    return tab


def submit(tab):
    before = len(tab.framework.orchestrator.submitted)
    tab._submit_build()
    submitted = tab.framework.orchestrator.submitted
    return submitted[-1] if len(submitted) > before else None


@pytest.mark.usefixtures("qapp", "gui_messages")
class TestBuilderTabSubmission:

    @pytest.fixture
    def tab(self, booted_framework):
        return make_tab(booted_framework)

    @pytest.mark.parametrize("fmt, board, default", [
        ("gguf", "rk3566", "Q4_0"), ("gguf", "rk3588", "Q4_K_M"), ("rkllm", "rk3576", "w4a16"),
        ("rkllm", "rk3588", "w8a8"), ("rknn", "rk3568", "fp16"),
    ])
    def test_empty_quantization_submits_the_board_default(self, tab, fmt, board, default):
        select(tab.format_combo, fmt)
        select(tab.board_combo, board)
        tab.quant_combo.setEditText("")
        assert tab.quant_combo.currentText() == ""
        req = submit(tab)
        assert req is not None
        assert (req.quantization_options, req.target_board) == ([default], board)

    def test_typed_alias_is_submitted_canonical(self, tab):
        select(tab.format_combo, "gguf")
        select(tab.board_combo, "rk3588")
        type_text(tab.quant_combo.lineEdit(), "int8")
        assert tab.quant_combo.currentText() == "int8" and tab._quant_user_set
        req = submit(tab)
        assert req.quantization_options == ["Q8_0"]

    def test_imatrix_without_dataset_is_submitted(self, tab, gui_messages):
        select(tab.format_combo, "gguf")
        tab.quant_combo.setEditText("IQ2_XS")
        tab.chk_imatrix.setChecked(True)
        tab.dataset_path = None
        req = submit(tab)
        assert req is not None, gui_messages
        assert (req.quantization_options, req.use_imatrix, req.dataset_path) == (["IQ2_XS"], True, None)

    def test_invalid_alias_for_the_board_is_refused(self, tab, gui_messages):
        select(tab.format_combo, "rkllm")
        select(tab.board_combo, "rk3588")
        tab.quant_combo.setEditText("int4")
        assert submit(tab) is None
        assert gui_messages and gui_messages[0][0] == "warning" and "w4a16" in gui_messages[0][1]

    def test_output_dir_is_resolved_like_the_deployment_window(self, tab):
        req = submit(tab)
        assert Path(req.output_base_dir) == tab.framework.resolve_path("output")
        assert Path(req.output_base_dir).is_absolute()
        tab.framework.config.set("output_dir", "~/llm-builds")
        req = submit(tab)
        assert Path(req.output_base_dir) == Path(os.environ["HOME"]) / "llm-builds"


@pytest.mark.usefixtures("qapp", "gui_messages")
class TestBuilderTabQuantChoice:

    @pytest.fixture
    def tab(self, booted_framework):
        return make_tab(booted_framework)

    def test_defaults_follow_the_board_and_never_count_as_a_choice(self, tab):
        select(tab.format_combo, "gguf")
        select(tab.board_combo, "rk3588")
        assert tab.quant_combo.currentText() == "Q4_K_M" and not tab._quant_user_set
        select(tab.board_combo, "rk3566")
        assert tab.quant_combo.currentText() == "Q4_0"
        select(tab.board_combo, "rk3588")
        assert tab.quant_combo.currentText() == "Q4_K_M" and not tab._quant_user_set

    def test_picked_type_survives_a_board_change_when_allowed(self, tab):
        select(tab.format_combo, "gguf")
        select(tab.board_combo, "rk3588")
        pick_quant(tab, "Q5_K_M")
        assert tab._quant_user_set
        select(tab.board_combo, "rk3566")
        assert tab.quant_combo.currentText() == "Q5_K_M", "the board default must not replace a user choice"
        assert submit(tab).quantization_options == ["Q5_K_M"]

    def test_typed_type_survives_a_board_change_when_allowed(self, tab):
        select(tab.format_combo, "gguf")
        select(tab.board_combo, "rk3588")
        type_text(tab.quant_combo.lineEdit(), "IQ4_XS")
        assert tab._quant_user_set
        select(tab.board_combo, "rk3568")
        assert tab.quant_combo.currentText() == "IQ4_XS"

    def test_picked_type_is_replaced_when_the_new_board_lacks_it(self, tab):
        select(tab.format_combo, "rkllm")
        select(tab.board_combo, "rk3588")
        pick_quant(tab, "w8a8_g128")
        select(tab.board_combo, "rk3576")
        assert "w8a8_g128" not in quant_items(tab)
        assert tab.quant_combo.currentText() == "w4a16" and not tab._quant_user_set
        select(tab.board_combo, "rk3588")
        assert tab.quant_combo.currentText() == "w8a8", "a replaced choice must not come back"

    def test_choice_allowed_on_both_boards_survives_a_round_trip(self, tab):
        select(tab.format_combo, "rkllm")
        select(tab.board_combo, "rk3576")
        assert tab.quant_combo.currentText() == "w4a16"
        pick_quant(tab, "w8a8")
        select(tab.board_combo, "rk3588")
        select(tab.board_combo, "rk3576")
        assert tab.quant_combo.currentText() == "w8a8"
        assert submit(tab).quantization_options == ["w8a8"]

    def test_format_change_applies_the_new_formats_default(self, tab):
        select(tab.format_combo, "gguf")
        pick_quant(tab, "Q6_K")
        select(tab.format_combo, "rknn")
        assert tab.quant_combo.currentText() == "fp16" and not tab._quant_user_set

    def test_refresh_keeps_target_board_and_default(self, tab):
        select(tab.format_combo, "gguf")
        select(tab.board_combo, "rk3566")
        assert tab.quant_combo.currentText() == "Q4_0"
        tab._refresh_targets()  # main_window does this after the module wizard
        assert tab.target_combo.currentData() == "Rockchip"
        assert tab.format_combo.currentData() == "gguf" and tab.board_combo.currentData() == "rk3566"
        assert tab.quant_combo.currentText() == "Q4_0" and not tab._quant_user_set
        select(tab.board_combo, "rk3568")
        assert tab.quant_combo.currentText() == "Q4_0"
        select(tab.board_combo, "rk3588")
        assert tab.quant_combo.currentText() == "Q4_K_M", "no stale Q4_K_M/Q4_0 left behind by the refresh"

    def test_refresh_does_not_turn_a_default_into_a_choice(self, tab):
        select(tab.format_combo, "gguf")
        select(tab.board_combo, "rk3588")
        tab._refresh_targets()
        assert tab.quant_combo.currentText() == "Q4_K_M" and not tab._quant_user_set
        select(tab.board_combo, "rk3566")
        assert tab.quant_combo.currentText() == "Q4_0", "finding 32: a Q4_K_M nobody chose replaced the default"

    def test_refresh_keeps_a_real_choice(self, tab):
        select(tab.format_combo, "gguf")
        select(tab.board_combo, "rk3566")
        pick_quant(tab, "Q8_0")
        tab._refresh_targets()
        assert tab.quant_combo.currentText() == "Q8_0" and tab._quant_user_set


@pytest.mark.usefixtures("qapp", "gui_messages")
class TestBuilderTabWithoutCapabilities:

    @pytest.fixture
    def tab(self, mixed_framework):
        return make_tab(mixed_framework, "MyJetson")

    def test_board_is_editable_with_a_none_entry(self, tab):
        assert tab.board_combo.isEditable()
        assert tab.board_combo.itemText(0) == "(none)" and tab.board_combo.itemData(0) is None
        tab.board_combo.setCurrentIndex(0)
        assert tab._selected_board() is None

    def test_none_submits_no_board(self, tab):
        tab.board_combo.setCurrentIndex(tab.board_combo.findText("(none)"))
        req = submit(tab)
        assert req is not None and req.target_board is None
        assert req.targets == ["MyJetson"]

    def test_typed_board_is_submitted(self, tab):
        type_text(tab.board_combo.lineEdit(), "orin-nx")
        assert tab._selected_board() == "orin-nx"
        req = submit(tab)
        assert req.target_board == "orin-nx"

    def test_typed_board_survives_a_format_change(self, tab):
        type_text(tab.board_combo.lineEdit(), "orin-nx")
        select(tab.format_combo, "rknn")
        assert tab._selected_board() == "orin-nx"

    def test_quantization_is_free_text_and_submitted_as_typed(self, tab):
        select(tab.format_combo, "gguf")
        type_text(tab.quant_combo.lineEdit(), "Q4_K_S")
        req = submit(tab)
        assert req.quantization_options == ["Q4_K_S"]

    def test_refresh_keeps_the_capability_less_target_and_its_typed_board(self, tab):
        type_text(tab.board_combo.lineEdit(), "orin-nx")
        tab._refresh_targets()
        assert tab.target_combo.currentData() == "MyJetson"
        assert tab.board_combo.isEditable() and tab._selected_board() == "orin-nx"

    def test_switching_to_a_capability_target_restores_its_boards(self, tab):
        type_text(tab.board_combo.lineEdit(), "orin-nx")
        select(tab.target_combo, "Rockchip")
        assert not tab.board_combo.isEditable()
        boards = [tab.board_combo.itemData(i) for i in range(tab.board_combo.count())]
        assert boards == tab._capabilities().boards_for(tab.format_combo.currentData())
        board = tab._selected_board()
        cap = tab._capabilities().formats[tab.format_combo.currentData()]
        assert tab.quant_combo.currentText() == cap.default_for(board) and not tab._quant_user_set

    def test_round_trip_through_a_capability_less_target_keeps_the_default(self, tab):
        select(tab.target_combo, "Rockchip")
        select(tab.format_combo, "gguf")
        select(tab.board_combo, "rk3566")
        assert tab.quant_combo.currentText() == "Q4_0"
        select(tab.target_combo, "MyJetson")
        select(tab.target_combo, "Rockchip")
        board = tab._selected_board()
        assert tab.quant_combo.currentText() == tab._capabilities().formats["gguf"].default_for(board)
        assert not tab._quant_user_set

    @pytest.mark.parametrize("board", ["rk3588", "rk3566"])
    def test_rockchip_board_is_not_carried_into_a_capability_less_target(self, tab, board):
        # Finding 31: a wizard target must not be built with a Rockchip board id nobody chose for it.
        select(tab.target_combo, "Rockchip")
        select(tab.format_combo, "gguf")
        select(tab.board_combo, board)
        select(tab.target_combo, "MyJetson")
        assert tab._selected_board() is None, f"board {tab._selected_board()!r} came from the Rockchip target"
        assert submit(tab).target_board is None

    @pytest.mark.parametrize("fmt, board, default", [("rkllm", "rk3576", "w4a16"), ("gguf", "rk3566", "Q4_0")])
    def test_rockchip_default_quantization_is_not_carried_into_it(self, tab, fmt, board, default):
        # w4a16 exists only on rk3576; without a board the target's scripts build for their own default board.
        select(tab.target_combo, "Rockchip")
        select(tab.format_combo, fmt)
        select(tab.board_combo, board)
        assert tab.quant_combo.currentText() == default and not tab._quant_user_set
        select(tab.target_combo, "MyJetson")
        req = submit(tab)
        assert (req.target_board, req.quantization_options) == (None, []), "the target's own defaults"

    def test_a_chosen_quantization_survives_the_switch(self, tab):
        select(tab.target_combo, "Rockchip")
        select(tab.format_combo, "gguf")
        pick_quant(tab, "Q5_K_M")
        select(tab.target_combo, "MyJetson")
        assert submit(tab).quantization_options == ["Q5_K_M"]


@pytest.mark.usefixtures("qapp", "gui_messages")
class TestBuilderTabFreshWithoutCapabilities:
    """The only target is a wizard target: nothing but the static widget defaults can supply a board."""

    @pytest.fixture
    def tab(self, make_framework, tmp_path):
        fm = framework_with_targets(make_framework, tmp_path / "targets_wizard_only", rockchip=False)
        return make_tab(fm, "MyJetson")

    def test_board_combo_is_editable_and_offers_none(self, tab):
        assert tab.board_combo.isEditable()
        assert [tab.board_combo.itemData(i) for i in range(tab.board_combo.count())] == [None]

    def test_fresh_tab_submits_no_board(self, tab):
        assert tab._selected_board() is None, f"board {tab._selected_board()!r} nobody chose"
        assert submit(tab).target_board is None

    def test_choosing_none_explicitly_submits_no_board(self, tab):
        tab.board_combo.setCurrentIndex(tab.board_combo.findText("(none)"))
        assert submit(tab).target_board is None

    @pytest.mark.parametrize("fmt", ["gguf", "rkllm"])
    def test_fresh_tab_submits_no_quantization_nobody_chose(self, tab, fmt):
        select(tab.format_combo, fmt)
        assert tab.quant_combo.currentText() == "" and not tab._quant_user_set
        assert tab.quant_combo.lineEdit().placeholderText(), "the empty field is explained"
        assert submit(tab).quantization_options == []
