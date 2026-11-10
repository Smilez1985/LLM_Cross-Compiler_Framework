"""The general target template (targets/_template with orchestrator/Core/module_generator.py): the stages hold the
pattern for any hardware, and what depends on the board or the SoC is a module's template variables
(orchestrator/schemas/target_template.py), kept in target.yml under template_variables and rendered into the config
stage as data. GGUF with llama.cpp is the pattern's built-in instance; a synthetic NPU format with a fake converter
proves the pattern end to end: generate, validate, the stages call the declared converter with the declared
arguments, and the package names the declared runtime libraries."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from conftest import REPO_ROOT
from orchestrator.Core.module_generator import FORMATS_BEGIN, render_formats, validate_module_data, validate_target_folder
from orchestrator.schemas.target_template import TemplateVariables
from test_target_template import LAYER, TEMPLATE, generate
from test_targets_modules import asking, call, fw, registry_fake  # noqa: F401

needs_bash = pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None, reason="needs bash")


def npu_format(command: str = "/opt/vendor/fakenpu/bin/fakec", **overrides):
    """A synthetic NPU format: its vendor converter takes the model and writes a .fnpu file, and its runtime needs one
    library the vendor allows passing on and one it does not."""
    fmt = {"id": "fakenpu", "label": "Fake NPU (test)",
           "converter": {"command": command,
                         "args": ["--model", "{input}", "--output", "{output}", "--dtype", "{quant}",
                                  "--name={model_name}"]},
           "input": ["hf"], "output_extension": "fnpu", "quantizations": ["int8", "int16"],
           "default_quantization": "int8",
           "runtime_libraries": [
               {"name": "libfakert.so", "version": "2.1.0", "image_path": "/opt/vendor/fakenpu/lib/libfakert.so",
                "redistributable": True},
               {"name": "libfakedrv.so", "version": "1.4.0", "image_path": "/opt/vendor/fakenpu/lib/libfakedrv.so",
                "source_url": "https://vendor.example.com/fakenpu/runtime"}],
           "board_paths": {"install_dir": "/opt/fakenpu", "model_dir": "/opt/fakenpu/models",
                           "lib_dir": "/opt/fakenpu/lib"},
           "sdk": {"toolkit": "FakeNPU-SDK", "version": "2.1.0", "runtime_key": "FAKENPU_RUNTIME_VERSION"},
           "vendor_files": [{"name": "fakenpu-firmware.bin", "url": "https://vendor.example.com/fakenpu/firmware",
                             "licence_url": "https://vendor.example.com/fakenpu/licence"}]}
    fmt.update(overrides)
    return fmt


def fake_vendor(root: Path):
    """The fake converter (logs its arguments, writes the model to --output) and the two runtime libraries, at paths
    the format can declare (absolute paths below the test's folder)."""
    vendor = root / "vendor"
    (vendor / "bin").mkdir(parents=True)
    (vendor / "lib").mkdir()
    converter = vendor / "bin" / "fakec"
    converter.write_text(f"#!{sys.executable}\n"
                         "import os, sys\n"
                         "a = sys.argv[1:]\n"
                         f"open({str(root / 'converter.log')!r}, 'a').write(' '.join(a) + '\\n')\n"
                         "open(a[a.index('--output') + 1], 'w').write('fakenpu model ' + a[a.index('--dtype') + 1])\n")
    converter.chmod(0o755)
    (vendor / "lib" / "libfakert.so").write_text("runtime 2.1.0")
    (vendor / "lib" / "libfakedrv.so").write_text("driver 1.4.0, not to be passed on")
    return converter


def npu_module(tmp_path, **fmt):
    converter = fake_vendor(tmp_path)
    libs = [dict(lib, image_path=str(tmp_path / "vendor" / "lib" / lib["name"]))
            for lib in npu_format()["runtime_libraries"]]
    return generate(tmp_path, module_name="Fake NPU Board",
                    formats=[npu_format(str(converter), runtime_libraries=libs, **fmt)])


def run_stages(target: Path, root: Path, fmt: str, quant: str, *args: str):
    work = root / "work"
    model = root / "model"
    model.mkdir(exist_ok=True)
    (model / "config.json").write_text("{}")
    from orchestrator.Core.runtime_layer import stage_order
    env = {"PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin", "HOME": str(root), "BUILD_CACHE_DIR": str(work),
           "LLAMA_CPP_PATH": str(root / "no-llama"), "MODEL_SOURCE": str(model), "MODEL_NAME": "model",
           "QUANTIZATION": quant, "TARGET_FORMAT": fmt, "JOB_TYPE": "build",
           "LLMCF_MODULES_DIR": str(target / "modules"), "LLMCF_STAGES": " ".join(stage_order(target)[0]),
           "LLMCF_TARGET_LABEL": target.name}
    result = subprocess.run(["sh", str(LAYER / "runner.sh"), *args], env=env, capture_output=True, text=True,
                            timeout=60)
    return result, work


# --- the schema and the checks of every value ----------------------------------------------------------------------

def test_a_module_without_declared_formats_records_the_gguf_instance(tmp_path):
    target = generate(tmp_path, quantization_logic='    Q4_K_M|Q8_0) build_gguf "$QUANT_TYPE" ;;')
    meta = yaml.safe_load((target / "target.yml").read_text())
    record = TemplateVariables.model_validate(meta["template_variables"])
    assert [f.id for f in record.formats] == ["gguf"] and record.formats[0].converter.command == "llama.cpp"
    # the capabilities the GGUF module always had
    assert meta["capabilities"]["formats"] == {"gguf": {
        "label": "GGUF (llama.cpp)", "input": ["hf"], "quantizations": ["Q4_K_M", "Q8_0"],
        "default_quantization": "Q4_K_M", "imatrix": True, "dataset": "optional"}}
    assert validate_target_folder(target) == []


def test_the_rockchip_helpers_are_copied_only_when_a_format_names_them(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    plain = generate(tmp_path / "a")
    for rel in ("modules/rknn_module.sh", "modules/rkllm_module.sh", "scripts/rknn_converter.py",
                "scripts/export_rkllm.py"):
        assert not (plain / rel).exists(), rel
    rknn = generate(tmp_path / "b", formats=["gguf", "rknn"])
    assert (rknn / "modules" / "rknn_module.sh").is_file() and (rknn / "scripts" / "rknn_converter.py").is_file()
    assert not (rknn / "modules" / "rkllm_module.sh").exists() and not (rknn / "scripts" / "export_rkllm.py").exists()
    meta = yaml.safe_load((rknn / "target.yml").read_text())
    assert list(meta["capabilities"]["formats"]) == ["gguf", "rknn"]
    assert meta["capabilities"]["formats"]["rknn"]["sdk"]["toolkit"] == "RKNN-Toolkit2"
    assert validate_target_folder(rknn) == []


@pytest.mark.parametrize("change, message", [
    ({"converter": {"command": "/opt/x/fakec", "args": ["--fetch", "curl"]}}, "downloads at build time"),
    ({"converter": {"command": "/opt/x/fakec", "args": ["$(id)"]}}, "is not one plain word"),
    ({"converter": {"command": "/opt/x/fakec", "args": ["a b"]}}, "is not one plain word"),
    ({"converter": {"command": "/opt/x/fakec", "args": ["{secret}"]}}, "unknown placeholder"),
    ({"converter": {"command": "/opt/../etc/fakec"}}, "no '..'"),
    ({"converter": {"command": "fakec; rm -rf /"}}, "command:"),
    ({"converter": {"command": "llama.cpp"}}, "makes the format gguf only"),
    ({"label": "Fake [NPU]"}, "label"),
    ({"default_quantization": "int4"}, "is not in quantizations"),
    ({"runtime_libraries": [{"name": "librt.so", "version": "1.0", "image_path": "/opt/x/librt.so"}]},
     "source_url says where"),
    ({"runtime_libraries": [{"name": "librt.so", "version": "1.0", "image_path": "/opt/x/librt.so",
                             "redistributable": True, "licence_url": "http://vendor.example.com/l"}]}, "licence_url"),
    ({"vendor_files": [{"name": "fw.bin", "url": "https://vendor.example.com/fw",
                        "licence_url": "https://vendor.example.com/l", "purpose": "sudo it"}]}, "uses sudo"),
])
def test_every_variable_value_is_checked_like_todays_fields(change, message):
    data = dict(module_name="Board", architecture="aarch64", formats=[npu_format(**change)])
    with pytest.raises(ValueError) as e:
        validate_module_data(data)
    assert message in str(e.value)


def test_a_converter_the_template_does_not_have_is_refused(tmp_path):
    with pytest.raises(ValueError, match="is not a helper of the template"):
        generate(tmp_path, formats=[npu_format("modules/fakenpu_module.sh")])
    assert not (tmp_path / "targets" / "my_board").exists()


# --- the folder check: the rendered variables are the record of target.yml -----------------------------------------

def test_the_block_of_the_variables_is_what_target_yml_declares(tmp_path):
    target = generate(tmp_path, formats=[npu_format()])
    assert validate_target_folder(target) == []
    config = target / "modules" / "config_module.sh"
    text = config.read_text()
    config.write_text(text.replace("'--dtype'", "'--dtype-by-hand'"))
    assert any("block of the template variables is not what target.yml" in p for p in validate_target_folder(target))
    config.write_text(text)
    meta_path = target / "target.yml"
    meta = yaml.safe_load(meta_path.read_text())
    meta["template_variables"]["formats"][0]["converter"]["args"].append("--fast")
    meta_path.write_text(yaml.safe_dump(meta, sort_keys=False))
    assert any("block of the template variables is not what target.yml" in p for p in validate_target_folder(target))
    meta["template_variables"]["formats"][0]["converter"]["args"][-1] = "--fetch=wget"
    meta_path.write_text(yaml.safe_dump(meta, sort_keys=False))
    assert any("downloads at build time" in p for p in validate_target_folder(target))


def test_the_block_renders_values_as_single_quoted_data():
    values = TemplateVariables(formats=[npu_format()]).formats[0].model_dump(exclude_none=True)
    block = render_formats([values])
    assert block.startswith(FORMATS_BEGIN)
    assert "FORMAT_ARGS_fakenpu=('--model' '{input}' '--output' '{output}' '--dtype' '{quant}' '--name={model_name}')" \
        in block
    assert "'libfakedrv.so|1.4.0|/opt/vendor/fakenpu/lib/libfakedrv.so|reference|" \
           "https://vendor.example.com/fakenpu/runtime'" in block
    assert "$" not in block and "`" not in block


# --- the pattern end to end ----------------------------------------------------------------------------------------

@needs_bash
def test_a_synthetic_npu_format_is_built_by_the_pattern_end_to_end(tmp_path):
    target = npu_module(tmp_path)
    assert validate_target_folder(target) == []
    meta = yaml.safe_load((target / "target.yml").read_text())
    assert meta["capabilities"]["formats"]["fakenpu"]["sdk"] == {
        "toolkit": "FakeNPU-SDK", "version": "2.1.0", "runtime_key": "FAKENPU_RUNTIME_VERSION"}
    assert list(meta["capabilities"]["formats"]) == ["fakenpu"]

    result, work = run_stages(target, tmp_path, "fakenpu", "int16", "--package")

    assert result.returncode == 0, result.stdout + result.stderr
    out = work / "output"
    # the declared converter with the declared arguments, the placeholders filled
    assert (tmp_path / "converter.log").read_text().splitlines() == [
        f"--model {tmp_path / 'model'} --output {out / 'model-int16.fnpu'} --dtype int16 --name=model"]
    assert (out / "model-int16.fnpu").read_text() == "fakenpu model int16"
    info = json.loads((out / "build_info.json").read_text())
    assert info["format"] == "fakenpu" and info["quantization"] == "int16"
    assert info["conversion"]["output_format"] == "fakenpu"
    # the package names the declared runtime libraries; only the one that may be passed on is in it
    package = out / "packages" / "model_int16_fakenpu"
    manifest = json.loads((package / "MANIFEST.json").read_text())
    assert manifest["models"] == ["models/model-int16.fnpu"]
    assert manifest["runtime_libraries"] == [
        {"name": "libfakert.so", "version": "2.1.0", "in_package": True, "source": ""},
        {"name": "libfakedrv.so", "version": "1.4.0", "in_package": False,
         "source": "https://vendor.example.com/fakenpu/runtime"}]
    assert manifest["board_paths"] == {"install": "/opt/fakenpu", "models": "/opt/fakenpu/models",
                                       "libraries": "/opt/fakenpu/lib"}
    assert manifest["vendor_files"][0]["name"] == "fakenpu-firmware.bin"
    assert (package / "lib" / "libfakert.so").read_text() == "runtime 2.1.0"
    assert not (package / "lib" / "libfakedrv.so").exists()
    runtime = (package / "RUNTIME.txt").read_text()
    assert "lib/libfakert.so 2.1.0 (in this package)" in runtime
    assert "libfakedrv.so 1.4.0: not in this package" in runtime and "own licence acceptance" in runtime
    sums = (package / "SHA256SUMS").read_text()
    assert "models/model-int16.fnpu" in sums and "lib/libfakert.so" in sums
    # nothing of llama.cpp ran for it
    assert "quantize" not in result.stdout.lower().replace("quantization", "")


@needs_bash
def test_the_pattern_takes_the_default_type_and_refuses_what_the_format_does_not_declare(tmp_path):
    target = npu_module(tmp_path)
    result, work = run_stages(target, tmp_path, "fakenpu", "")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (work / "output" / "model-int8.fnpu").is_file()

    result, _ = run_stages(target, tmp_path, "fakenpu", "int4")
    assert result.returncode != 0 and "The format fakenpu has no quantization 'int4' (int8 int16)" in result.stderr
    result, _ = run_stages(target, tmp_path, "gguf", "Q4_K_M")
    assert result.returncode != 0 and "Format 'gguf' is not supported by this target (fakenpu)" in result.stderr


@needs_bash
def test_an_input_the_format_does_not_declare_is_refused_before_the_converter_runs(tmp_path):
    target = npu_module(tmp_path, input=["onnx"])
    result, _ = run_stages(target, tmp_path, "fakenpu", "int8")
    assert result.returncode != 0
    assert "the format fakenpu of this target takes an ONNX model" in result.stderr
    assert not (tmp_path / "converter.log").exists()


@needs_bash
def test_a_converter_missing_from_the_image_stops_the_stage_with_the_reason(tmp_path):
    target = generate(tmp_path, formats=[npu_format("/opt/vendor/nowhere/fakec")])
    result, _ = run_stages(target, tmp_path, "fakenpu", "int8")
    assert result.returncode != 0
    assert "The converter /opt/vendor/nowhere/fakec of the format fakenpu is not in the image" in result.stderr


@needs_bash
def test_gguf_and_a_declared_format_build_side_by_side(tmp_path):
    from test_target_template import stub_llama
    converter = fake_vendor(tmp_path)
    target = generate(tmp_path, formats=["gguf", npu_format(str(converter), runtime_libraries=[])])
    llama = stub_llama(tmp_path)
    result, work = run_stages(target, tmp_path, "fakenpu", "int8")
    assert result.returncode == 0, result.stdout + result.stderr
    from test_target_template import run_build
    result, work = run_build(target / "modules", tmp_path, llama, "Q4_K_M")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (work / "output" / "model-Q4_K_M.gguf").read_text() == "quantized Q4_K_M"


# --- the template itself -------------------------------------------------------------------------------------------

def test_the_template_marks_the_place_of_the_variables_and_runs_converters_as_arrays():
    config = (TEMPLATE / "modules" / "config_module.sh").read_text(encoding="utf-8")
    convert = (TEMPLATE / "modules" / "convert_module.sh").read_text(encoding="utf-8")
    assert config.count("# [DECLARED_FORMATS]") == 1
    body = convert[convert.index("convert_declared() {"):convert.index("\nmain() {")]
    assert '"${CONVERTER_WORDS[@]}" "${words[@]}"' in body
    assert "eval" not in body and "bash -c" not in body and "sh -c" not in body


def test_the_model_check_copies_stay_the_same_file():
    copies = {(REPO_ROOT / "targets" / t / "scripts" / "model_check.py").read_bytes()
              for t in ("_template", "Rockchip", "ARM64")}
    assert len(copies) == 1


def test_a_declared_refusal_names_the_inputs_the_format_takes(tmp_path):
    sys.path.insert(0, str(TEMPLATE / "scripts"))
    try:
        import model_check as mc
    finally:
        sys.path.pop(0)
    onnx = tmp_path / "net.onnx"
    onnx.write_bytes(b"onnx")
    info = mc.detect(onnx)
    assert "takes a Hugging Face model folder" in mc.refusal(info, onnx, "hef", "", ["hf"])
    assert mc.refusal(info, onnx, "hef", "", ["hf", "onnx"]) is None


def test_the_generator_keeps_every_format_value_of_an_ai_definition():
    from orchestrator.Core.module_generator import generator_input
    data = generator_input({"architecture": "aarch64", "formats": [npu_format()]}, "Board")
    assert data["formats"] == [npu_format()]



# --- asking for the variables: llm-cli module create --formats and create_target_module's formats ----------------

def test_module_create_takes_the_formats_as_variables(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from orchestrator import cli as cli_module
    from test_cli_builds import write_user_config
    write_user_config(tmp_path, targets_dir=str(tmp_path / "targets"))
    formats = tmp_path / "formats.yml"
    formats.write_text(yaml.safe_dump([npu_format(), "gguf"], sort_keys=False))
    result = CliRunner().invoke(cli_module.cli, [
        "-q", "module", "create", "--name", "Fake NPU CLI", "--arch", "aarch64", "--sdk", "FakeNPU",
        "--description", "d", "--base-image", "debian:bookworm-slim", "--packages", "cmake", "--cpu-flags", "",
        "--formats", str(formats)], input="")
    assert result.exit_code == 0, result.output
    target = tmp_path / "targets" / "fake_npu_cli"
    meta = yaml.safe_load((target / "target.yml").read_text())
    assert [f["id"] for f in meta["template_variables"]["formats"]] == ["fakenpu", "gguf"]
    assert list(meta["capabilities"]["formats"]) == ["fakenpu", "gguf"]
    assert validate_target_folder(target) == []

    formats.write_text(yaml.safe_dump([npu_format(converter={"command": "/opt/x/fakec", "args": ["$(id)"]})]))
    result = CliRunner().invoke(cli_module.cli, [
        "-q", "module", "create", "--name", "Refused NPU", "--arch", "aarch64", "--sdk", "x", "--description", "d",
        "--base-image", "debian:bookworm-slim", "--packages", "cmake", "--cpu-flags", "", "--formats", str(formats)],
        input="")
    assert result.exit_code == 1 and "is not one plain word" in result.output
    assert not (tmp_path / "targets" / "refused_npu").exists()




def test_the_tool_asks_with_the_formats_and_makes_them(fw, registry_fake):  # noqa: F811
    registry_fake.add_index("library/debian", "bookworm-slim", ["linux/amd64", "linux/arm64"])
    confirm = asking(True)
    out = call(fw, "create_target_module", {"module_name": "Fake NPU MCP", "architecture": "aarch64",
                                            "base_os": "debian:bookworm-slim", "packages": ["cmake"],
                                            "formats": [npu_format(), "gguf"]}, confirm=confirm)
    (question,) = confirm.asked
    assert "fakenpu (Fake NPU (test)): converter /opt/vendor/fakenpu/bin/fakec --model {input}" in question
    assert "libfakedrv.so 1.4.0 (not passed on: fetched from https://vendor.example.com/fakenpu/runtime)" in question
    assert out.formats == ["fakenpu", "gguf"]
    folder = Path(fw.target_manager.targets_dir) / out.folder
    assert validate_target_folder(folder) == []
    with pytest.raises(Exception) as e:
        call(fw, "create_target_module", {"module_name": "Bad NPU", "architecture": "aarch64",
                                          "base_os": "debian:bookworm-slim",
                                          "formats": [npu_format(converter={"command": "/opt/x/c",
                                                                            "args": ["--get", "wget"]})]},
             confirm=asking(True))
    assert "downloads at build time" in str(e.value)
