"""The convert stage's features of the maintainer's design (HOW2_ORIGINAL, DECISIONS round 16, U9 addendum), which the
convert module of version 1.x promised and never had: format detection, input and output validation with integrity
checks, conversion statistics (log and build manifest), a functional test of the written GGUF, and cleanup on failure.

scripts/model_check.py (the same file in targets/Rockchip and targets/_template) does the checks; it is tested here
on its own (portable) and in the Rockchip build with the stub toolchains of tests/test_build_stages.py (bash).
"""

import ast
import json
import os
import shutil
import struct
import subprocess
import sys
import types
import zipfile
from pathlib import Path

import pytest

from conftest import NO_POSIX_BASH, REPO_ROOT, posix_shell
from model_files import GGUF_HEAD, gguf_bytes, gguf_text, safetensors_bytes, write_gguf, write_safetensors
from test_build_stages import F16, OUT, Image, _script, by_hand, stage_lines

ROCKCHIP = REPO_ROOT / "targets" / "Rockchip"
TEMPLATE = REPO_ROOT / "targets" / "_template"
needs_bash = pytest.mark.skipif(os.name == "nt" or posix_shell("bash") is None, reason=NO_POSIX_BASH)
needs_posix = pytest.mark.skipif(os.name == "nt", reason="the stub tools are scripts started through their #! line")


def _load_model_check():
    """scripts/model_check.py as a module, without writing bytecode into the target folder (the image copies it)."""
    path = ROCKCHIP / "scripts" / "model_check.py"
    module = types.ModuleType("model_check")
    module.__file__ = str(path)
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)  # noqa: S102
    return module


mc = _load_model_check()


def check(*args) -> int:
    """model_check.py's main with these arguments: its exit code (its output goes to capsys)."""
    return mc.main([str(a) for a in args])


# ---------------------------------------------------------------------------------------------------------------
# One implementation, shipped with every target
# ---------------------------------------------------------------------------------------------------------------

def test_every_target_ships_the_same_model_check():
    assert (ROCKCHIP / "scripts" / "model_check.py").read_bytes() == \
        (TEMPLATE / "scripts" / "model_check.py").read_bytes()


def test_the_model_suffixes_are_the_frameworks():
    """The container has no orchestrator package; the stage's map of model file suffixes is the one the framework's
    file listing (and ModelManager._detect_model_format) uses."""
    from orchestrator.tools.local_files import MODEL_SUFFIXES
    assert mc.MODEL_SUFFIXES == MODEL_SUFFIXES


def test_model_check_needs_only_the_standard_library():
    tree = ast.parse((ROCKCHIP / "scripts" / "model_check.py").read_text(encoding="utf-8"))
    modules = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
               for alias in node.names}
    modules |= {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert modules <= set(sys.stdlib_module_names), modules - set(sys.stdlib_module_names)


# ---------------------------------------------------------------------------------------------------------------
# Integrity: GGUF (llama.cpp's loader checks), SafeTensors (the safetensors library's checks), PyTorch, JSON, shards
# ---------------------------------------------------------------------------------------------------------------

def test_a_complete_gguf_is_read(tmp_path):
    path = tmp_path / "m.gguf"
    path.write_bytes(gguf_bytes(tensors=((64,), (32, 8))))
    assert mc.read_gguf(path) == {"version": 3, "tensors": 2, "architecture": "llama", "bytes": path.stat().st_size,
                                  "embedding": False}


@pytest.mark.parametrize("data, message", [
    (gguf_bytes(tensors=((64,), (32, 8)))[:-10], "is incomplete: its tensor data needs"),
    (gguf_bytes()[:40], "is incomplete: its header ends early"),
    (b"GGML" + gguf_bytes()[4:], "is no GGUF file"),
    (b"", "is no GGUF file"),
    (gguf_bytes(version=1), "has GGUF version 1; llama.cpp reads the versions 2 and 3"),
    (gguf_bytes(tensors=()), "holds no tensors"),
    (gguf_bytes(arch=""), "names no model architecture"),
    (gguf_bytes(tensors=((33,),), tensor_type=8), "a tensor row that does not fill its blocks"),
])
def test_a_damaged_gguf_is_refused_with_the_reason(tmp_path, data, message):
    path = tmp_path / "m.gguf"
    path.write_bytes(data)
    with pytest.raises(mc.Problem, match=message.replace("(", r"\(")):
        mc.read_gguf(path)


def test_gguf_tensor_sizes_follow_the_ggml_types(tmp_path):
    """Q8_0: 34 bytes per block of 32 values; a file that ends one byte early is incomplete."""
    whole = gguf_bytes(tensors=((64,),), tensor_type=8, data=b"\0" * 68)
    (tmp_path / "q8.gguf").write_bytes(whole)
    assert mc.read_gguf(tmp_path / "q8.gguf")["tensors"] == 1
    (tmp_path / "cut.gguf").write_bytes(whole[:-1])
    with pytest.raises(mc.Problem, match="is incomplete"):
        mc.read_gguf(tmp_path / "cut.gguf")
    # a type this table does not know (a newer ggml) is not sized, only its offset is checked
    (tmp_path / "new.gguf").write_bytes(gguf_bytes(tensors=((64,),), tensor_type=99, data=b""))
    assert mc.read_gguf(tmp_path / "new.gguf")["tensors"] == 1


def test_an_embedding_gguf_is_recognized(tmp_path):
    path = tmp_path / "e.gguf"
    path.write_bytes(gguf_bytes(arch="bert", kv=[("bert.pooling_type", 4, struct.pack("<I", 1))]))
    assert mc.read_gguf(path)["embedding"] is True
    path.write_bytes(gguf_bytes(arch="qwen3", kv=[("qwen3.pooling_type", 4, struct.pack("<I", 1)),
                                                  ("tokenizer.ggml.tokens", 9,
                                                   struct.pack("<IQ", 8, 2) + struct.pack("<Q", 1) + b"a"
                                                   + struct.pack("<Q", 1) + b"b")]))
    assert mc.read_gguf(path) == {"version": 3, "tensors": 1, "architecture": "qwen3", "bytes": path.stat().st_size,
                                  "embedding": True}


def _tensor(begin, end, shape=(1,), dtype="F32"):
    return {"dtype": dtype, "shape": list(shape), "data_offsets": [begin, end]}


@pytest.mark.parametrize("tensors, data, message", [
    ({"a": _tensor(0, 4), "b": _tensor(4, 12, (2,))}, b"\0" * 12, None),
    ({"a": _tensor(0, 4), "b": _tensor(4, 12, (2,))}, b"\0" * 8, "is incomplete: its tensors need 12 bytes"),
    ({"a": _tensor(0, 4)}, b"\0" * 6, "has 2 bytes after its last tensor"),
    ({"a": _tensor(0, 8)}, b"\0" * 8, "a tensor's size does not match its shape"),
    ({"a": _tensor(0, 4), "b": _tensor(2, 6)}, b"\0" * 6, "its tensors overlap or leave gaps"),
    ({"a": _tensor(4, 8)}, b"\0" * 8, "its tensors overlap or leave gaps"),
    ({"a": {"dtype": "F32"}}, b"", "a tensor without valid offsets or shape"),
])
def test_safetensors_are_checked_as_the_safetensors_library_checks_them(tmp_path, tensors, data, message):
    path = tmp_path / "model.safetensors"
    path.write_bytes(safetensors_bytes(tensors, data=data))
    if message is None:
        assert mc.check_safetensors(path) == len(tensors)
    else:
        with pytest.raises(mc.Problem, match=message):
            mc.check_safetensors(path)


@pytest.mark.parametrize("data, message", [
    (b"weights", "is incomplete or no SafeTensors file"),
    (struct.pack("<Q", 1000) + b"{}", "its header does not fit in the file"),
    (struct.pack("<Q", 2) + b"[]", "has a damaged header (no JSON object)"),
    (struct.pack("<Q", 3) + b"{x}", "has a damaged header (no JSON object)"),
])
def test_a_file_that_is_no_safetensors_file_is_refused(tmp_path, data, message):
    path = tmp_path / "model.safetensors"
    path.write_bytes(data)
    with pytest.raises(mc.Problem, match=message.replace("(", r"\(").replace(")", r"\)")):
        mc.check_safetensors(path)


def test_pytorch_weights_are_a_complete_archive_or_a_pickle(tmp_path):
    archive = tmp_path / "pytorch_model.bin"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("archive/data.pkl", b"\x80\x02}q\x00.")
    mc.check_pytorch(archive)
    archive.write_bytes(archive.read_bytes()[:-30])  # the table of contents is at the end: a cut-off download
    with pytest.raises(mc.Problem, match="the PyTorch archive has no complete table of contents"):
        mc.check_pytorch(archive)
    archive.write_bytes(b"\x80\x02}q\x00.")  # torch.save before 1.6: a pickle
    mc.check_pytorch(archive)
    archive.write_bytes(b"<html>Not Found</html>")
    with pytest.raises(mc.Problem, match="is neither a PyTorch archive nor a pickle"):
        mc.check_pytorch(archive)


def hf_folder(root: Path, **extra) -> Path:
    folder = root / "model"
    folder.mkdir()
    (folder / "config.json").write_text("{}")
    (folder / "tokenizer.json").write_text("{}")
    write_safetensors(folder / "model.safetensors")
    for name, data in extra.items():
        (folder / name.replace("__", ".")).write_bytes(data)
    return folder


def test_a_hugging_face_folder_is_checked_completely(tmp_path):
    folder = hf_folder(tmp_path, tokenizer_config__json=b"{}")
    info = mc.detect(folder)
    assert info["kind"] == "huggingface" and info["weights"] == "safetensors"
    assert mc.check_input(folder, info) == "config.json, tokenizer.json, tokenizer_config.json, 1 SafeTensors file"
    (folder / "tokenizer_config.json").write_text('{"model_max_length": ')
    with pytest.raises(mc.Problem, match="tokenizer_config.json in the model folder is no valid JSON"):
        mc.check_input(folder, info)


def test_the_shards_an_index_lists_must_all_be_there(tmp_path):
    folder = hf_folder(tmp_path)
    (folder / "model.safetensors").rename(folder / "model-00001-of-00003.safetensors")
    write_safetensors(folder / "model-00002-of-00003.safetensors", "two")
    index = {"weight_map": {"a": "model-00001-of-00003.safetensors", "b": "model-00002-of-00003.safetensors",
                            "c": "model-00003-of-00003.safetensors"}}
    (folder / "model.safetensors.index.json").write_text(json.dumps(index))
    with pytest.raises(mc.Problem, match="lists weights files that are not in the model folder: "
                                         "model-00003-of-00003.safetensors"):
        mc.check_input(folder, mc.detect(folder))
    write_safetensors(folder / "model-00003-of-00003.safetensors", "three")
    assert "3 SafeTensors files" in mc.check_input(folder, mc.detect(folder))


# ---------------------------------------------------------------------------------------------------------------
# Format detection and what each format refuses
# ---------------------------------------------------------------------------------------------------------------

def make_input(tmp_path: Path, kind: str) -> Path:
    if kind == "huggingface":
        return hf_folder(tmp_path)
    if kind == "pytorch folder":
        folder = tmp_path / "model"
        folder.mkdir()
        (folder / "config.json").write_text("{}")
        (folder / "pytorch_model.bin").write_bytes(b"\x80\x02}q\x00.")
        return folder
    if kind == "gguf":
        return write_gguf(tmp_path / "model.F16.gguf")
    if kind == "onnx file":
        (tmp_path / "piper.onnx").write_bytes(b"onnx")
        return tmp_path / "piper.onnx"
    if kind == "onnx folder":
        (tmp_path / "voice" / "sub").mkdir(parents=True)
        (tmp_path / "voice" / "sub" / "model.onnx").write_bytes(b"onnx")
        return tmp_path / "voice"
    if kind == "onnx export":  # config.json and an ONNX export, no Hugging Face weights
        (tmp_path / "export" / "onnx").mkdir(parents=True)
        (tmp_path / "export" / "config.json").write_text("{}")
        (tmp_path / "export" / "onnx" / "model.onnx").write_bytes(b"onnx")
        return tmp_path / "export"
    if kind == "safetensors file":
        return write_safetensors(tmp_path / "model.safetensors")
    if kind == "pytorch file":
        (tmp_path / "pytorch_model.bin").write_bytes(b"\x80\x02}q\x00.")
        return tmp_path / "pytorch_model.bin"
    if kind == "gguf folder":
        (tmp_path / "ggufs").mkdir()
        write_gguf(tmp_path / "ggufs" / "model-Q4_K_M.gguf")
        return tmp_path / "ggufs"
    if kind == "tflite":
        (tmp_path / "model.tflite").write_bytes(b"TFL3")
        return tmp_path / "model.tflite"
    if kind == "text file":
        (tmp_path / "notes.txt").write_text("hello")
        return tmp_path / "notes.txt"
    if kind == "bare folder":
        (tmp_path / "bare").mkdir()
        (tmp_path / "bare" / "README.md").write_text("x")
        return tmp_path / "bare"
    raise AssertionError(kind)


@pytest.mark.parametrize("kind, detected, gguf_refusal", [
    ("huggingface", "huggingface", None),
    ("pytorch folder", "huggingface", None),
    ("gguf", "gguf", None),
    ("onnx file", "onnx", "The model is an ONNX model (piper.onnx): llama.cpp has no converter from ONNX to GGUF"),
    ("onnx folder", "onnx", "The model is an ONNX model (voice): llama.cpp has no converter from ONNX to GGUF"),
    ("onnx export", "onnx", "The model is an ONNX model (export)"),
    ("safetensors file", "weights-file", "'model.safetensors' is a SafeTensors file, the weights only"),
    ("pytorch file", "weights-file", "'pytorch_model.bin' is a PyTorch weights file, the weights only"),
    ("gguf folder", "gguf-folder", "The folder holds GGUF files but no Hugging Face model"),
    ("tflite", "tflite", "'model.tflite' is a TensorFlow Lite model"),
    ("text file", "file", "'notes.txt' is no model this stage converts"),
    ("bare folder", "folder", None),  # the converter has the last word (the warnings of 1.x say what is missing)
])
def test_the_input_format_is_detected_and_what_gguf_cannot_use_is_refused(tmp_path, kind, detected, gguf_refusal):
    path = make_input(tmp_path, kind)
    info = mc.detect(path)
    assert info["kind"] == detected
    reason = mc.refusal(info, path, "gguf", "NEXT.")
    if gguf_refusal is None:
        assert reason is None
    else:
        assert reason.startswith(gguf_refusal), reason
    assert mc.refusal(info, path, "rknn", "NEXT.") is None, "rknn_module.sh decides about its input itself"


def test_onnx_is_refused_for_gguf_and_rkllm_with_the_way_to_the_npu(tmp_path):
    path = make_input(tmp_path, "onnx file")
    gguf = mc.refusal(mc.detect(path), path, "gguf", "Build it in the format rknn.")
    assert gguf == ("The model is an ONNX model (piper.onnx): llama.cpp has no converter from ONNX to GGUF, so no "
                    "GGUF can be built from it. Build it in the format rknn. A GGUF is built from the model's Hugging "
                    "Face folder (config.json, the tokenizer files and the weights).")
    rkllm = mc.refusal(mc.detect(path), path, "rkllm", "Build it in the format rknn.")
    assert rkllm == ("The model is an ONNX model (piper.onnx): the format rkllm converts the Hugging Face folder of "
                     "an LLM, not an ONNX model. Build it in the format rknn.")


def test_the_input_command_writes_the_first_statistics(tmp_path, capsys):
    folder = hf_folder(tmp_path)
    stats = tmp_path / "convert_stats.json"
    assert check("input", folder, "--format", "gguf", "--stats", stats) == 0
    size = sum(p.stat().st_size for p in folder.iterdir())
    assert json.loads(stats.read_text()) == {"input_format": "huggingface", "weights": "safetensors",
                                             "input_bytes": size, "converted": True}
    out = capsys.readouterr().out
    assert f"Input: a Hugging Face model folder with SafeTensors weights, {size} B; checked: config.json, " \
           "tokenizer.json, 1 SafeTensors file." in out


def test_a_hidden_folder_beside_the_weights_is_no_part_of_the_input_size(tmp_path):
    folder = hf_folder(tmp_path)
    before = mc.tree_bytes(folder)
    (folder / ".git" / "lfs").mkdir(parents=True)
    (folder / ".git" / "lfs" / "copy").write_bytes(b"x" * 4096)
    assert mc.tree_bytes(folder) == before


# ---------------------------------------------------------------------------------------------------------------
# Backups and cleanup (begin, keep, restore)
# ---------------------------------------------------------------------------------------------------------------

def test_a_replaced_file_is_kept_until_the_new_one_passed_and_then_goes(tmp_path):
    state, out = tmp_path / "state.json", tmp_path / "granite.fp16.gguf"
    out.write_text("the person's earlier file")
    assert check("begin", out, "--state", state) == 0
    assert not out.exists() and (tmp_path / "granite.fp16.gguf.convert-backup").read_text() == \
        "the person's earlier file"
    out.write_text("new")
    assert check("keep", "--state", state) == 0
    assert out.read_text() == "new" and not (tmp_path / "granite.fp16.gguf.convert-backup").exists()
    assert not state.exists()


def test_a_failed_run_removes_what_it_wrote_and_puts_the_earlier_file_back(tmp_path, capsys):
    state, out = tmp_path / "state.json", tmp_path / "granite.fp16.gguf"
    out.write_text("earlier")
    folder = tmp_path / "output"
    folder.mkdir()
    (folder / "old.rkllm").write_text("the person's")
    check("begin", out, "--state", state)
    check("watch", folder, "--state", state)
    out.write_text("half")
    (folder / "model-rk3588-w8a8.rkllm").write_text("half")

    assert check("restore", "--state", state, "--failed") == 0

    assert out.read_text() == "earlier" and sorted(p.name for p in folder.iterdir()) == ["old.rkllm"]
    assert not state.exists() and not list(tmp_path.glob("*.convert-backup"))
    printed = capsys.readouterr().out
    assert "Removed the unfinished granite.fp16.gguf." in printed
    assert "Put back the earlier granite.fp16.gguf." in printed


def test_after_a_stopped_run_only_its_named_files_are_undone(tmp_path):
    """New files in a watched folder are removed only in the run that failed: after a killed run the person may have
    put files there since."""
    state, out, folder = tmp_path / "state.json", tmp_path / "f.gguf", tmp_path / "output"
    folder.mkdir()
    check("begin", out, "--state", state)
    check("watch", folder, "--state", state)
    out.write_text("half")
    (folder / "later.txt").write_text("put there by the person")
    assert check("restore", "--state", state, "--stopped") == 0
    assert not out.exists() and (folder / "later.txt").exists()


def test_an_intermediate_of_the_work_folder_is_replaced_without_a_backup(tmp_path):
    state, f16 = tmp_path / "state.json", tmp_path / "tmp-model-F16.gguf"
    f16.write_text("of an earlier run")
    assert check("begin", f16, "--state", state, "--scratch", tmp_path) == 0
    assert not f16.exists() and not list(tmp_path.glob("*.convert-backup"))


def test_a_backup_name_in_the_way_is_refused_and_nothing_is_moved(tmp_path, capsys):
    state, out = tmp_path / "state.json", tmp_path / "f.gguf"
    out.write_text("mine")
    (tmp_path / "f.gguf.convert-backup").write_text("also mine")
    assert check("begin", out, "--state", state) == 1
    assert "f.gguf.convert-backup is in the way" in capsys.readouterr().err
    assert out.read_text() == "mine" and (tmp_path / "f.gguf.convert-backup").read_text() == "also mine"


def test_a_file_written_twice_in_one_run_keeps_its_first_backup(tmp_path):
    state, out = tmp_path / "state.json", tmp_path / "f.gguf"
    out.write_text("earlier")
    check("begin", out, "--state", state)
    out.write_text("first try")
    assert check("begin", out, "--state", state) == 0
    out.write_text("second try")
    check("restore", "--state", state, "--failed")
    assert out.read_text() == "earlier"


@pytest.mark.parametrize("target, message", [("missing/f.gguf", "The folder for f.gguf does not exist."),
                                             ("folder", "folder is a folder; the converter writes a file there.")])
def test_an_output_that_cannot_be_written_is_refused_before_converting(tmp_path, capsys, target, message):
    (tmp_path / "folder").mkdir()
    assert check("begin", tmp_path / target, "--state", tmp_path / "state.json") == 1
    assert f"ERROR: {message}" in capsys.readouterr().err


# ---------------------------------------------------------------------------------------------------------------
# The functional test (llama-completion, the llama-cli of version 1.x)
# ---------------------------------------------------------------------------------------------------------------

COMPLETION_STUB = """\
import os, sys, time
a = sys.argv
name = os.path.basename(a[0])
if a[1:] == ['--help']:
    print(os.environ.get('STUB_HELP_' + name.replace('-', '_'), os.environ.get('STUB_HELP',
          '-m FNAME -p PROMPT -n N -c N -cnv, --conversation, -no-cnv --offline --no-warmup --temp N --simple-io')))
    sys.exit(0)
with open(os.environ['STUB_LOG'], 'a') as f:
    f.write(name + ' ' + ' '.join(a[1:]) + '\\n')
mode = os.environ.get('STUB_FAIL')
if mode == 'completion':
    print('load_tensors: loading model tensors', file=sys.stderr)
    print('llama_model_load: error loading model: tensor data is not within the file bounds', file=sys.stderr)
    sys.exit(1)
if mode == 'completion-hang':
    time.sleep(30)
if mode == 'completion-signal':
    os.kill(os.getpid(), 9)
print('Hello world, this is', flush=True)
"""


def tools_with(root: Path, *names: str) -> Path:
    tools = root / "bin"
    for name in names:
        _script(tools / name, COMPLETION_STUB)
    return tools


@needs_posix
def test_the_functional_test_runs_the_gguf_shortly_and_offline(tmp_path, monkeypatch, capsys):
    log = tmp_path / "calls.log"
    monkeypatch.setenv("STUB_LOG", str(log))
    gguf = write_gguf(tmp_path / "model-F16.gguf")
    assert mc.functional_test(gguf, tools_with(tmp_path, "llama-completion", "llama-cli"), 30) == \
        ("passed", "llama-completion")
    assert log.read_text().splitlines() == [
        f"llama-completion -m {gguf} -p Hello -n 8 -c 256 -no-cnv --offline --no-warmup --temp 0 --simple-io"]
    assert "Functional test passed: llama-completion loaded model-F16.gguf and generated 8 tokens" in \
        capsys.readouterr().out


@needs_posix
def test_with_only_the_chat_program_the_test_runs_a_single_turn(tmp_path, monkeypatch):
    """llama-cli at the pin is the chat program (no -no-cnv); a single turn with the prompt ends by itself."""
    log = tmp_path / "calls.log"
    monkeypatch.setenv("STUB_LOG", str(log))
    monkeypatch.setenv("STUB_HELP", "-m FNAME -p PROMPT -n N -st, --single-turn --offline")
    gguf = write_gguf(tmp_path / "model-F16.gguf")
    assert mc.functional_test(gguf, tools_with(tmp_path, "llama-cli"), 30) == ("passed", "llama-cli")
    assert log.read_text().split()[-3:] == ["256", "--single-turn", "--offline"]


@needs_posix
@pytest.mark.parametrize("mode, result", [("completion-hang", "timed out"), ("completion-signal", "stopped")])
def test_a_run_that_cannot_finish_here_is_a_note_not_a_failure(tmp_path, monkeypatch, capsys, mode, result):
    monkeypatch.setenv("STUB_LOG", str(tmp_path / "calls.log"))
    monkeypatch.setenv("STUB_FAIL", mode)
    gguf = write_gguf(tmp_path / "model-F16.gguf")
    assert mc.functional_test(gguf, tools_with(tmp_path, "llama-completion"), 1) == (result, "llama-completion")
    assert "not counted as a failure" in capsys.readouterr().out


@needs_posix
def test_a_gguf_that_does_not_run_fails_the_test_with_the_tools_last_lines(tmp_path, monkeypatch):
    monkeypatch.setenv("STUB_LOG", str(tmp_path / "calls.log"))
    monkeypatch.setenv("STUB_FAIL", "completion")
    gguf = write_gguf(tmp_path / "model-F16.gguf")
    with pytest.raises(mc.Problem) as failed:
        mc.functional_test(gguf, tools_with(tmp_path, "llama-completion"), 30)
    assert str(failed.value) == (
        "The functional test failed: llama-completion could not run model-F16.gguf (exit 1), so the GGUF the "
        "converter wrote does not work. Its last lines:\n  load_tensors: loading model tensors\n"
        "  llama_model_load: error loading model: tensor data is not within the file bounds")


@needs_posix
@pytest.mark.parametrize("tools, help_text, note", [
    ((), None, "this image has neither llama-completion nor llama-cli"),
    (("llama-completion",), "-m FNAME -p PROMPT",
     "this llama-completion has no option for a run without a conversation"),
])
def test_without_a_tool_to_run_the_model_the_test_is_skipped_with_a_note(tmp_path, monkeypatch, capsys, tools,
                                                                         help_text, note):
    monkeypatch.setenv("STUB_LOG", str(tmp_path / "calls.log"))
    if help_text:
        monkeypatch.setenv("STUB_HELP", help_text)
    gguf = write_gguf(tmp_path / "model-F16.gguf")
    assert mc.functional_test(gguf, tools_with(tmp_path, *tools), 30)[0] == "skipped"
    assert f"Functional test skipped: {note}" in capsys.readouterr().out
    assert not (tmp_path / "calls.log").exists(), "the model was not run"


def test_an_embedding_model_is_not_run(tmp_path, capsys):
    gguf = tmp_path / "e.gguf"
    gguf.write_bytes(gguf_bytes(arch="bert", kv=[("bert.pooling_type", 4, struct.pack("<I", 1))]))
    stats = tmp_path / "stats.json"
    assert check("output", gguf, "--format", "gguf", "--type", "F16", "--stats", stats, "--tools",
                 tmp_path / "no-tools") == 0
    assert json.loads(stats.read_text())["functional_test"] == "skipped"
    assert "an embedding model does not generate text" in capsys.readouterr().out


# ---------------------------------------------------------------------------------------------------------------
# In the Rockchip build (stub toolchains)
# ---------------------------------------------------------------------------------------------------------------

@pytest.fixture
def image(tmp_path):
    return Image(tmp_path)


def conversion(image) -> dict:
    return json.loads((image.output / "build_info.json").read_text())["conversion"]


def stats_file(image, job="build") -> dict:
    return json.loads((image.work / "stages" / job / "convert_stats.json").read_text())


def model_bytes(image) -> int:
    return sum(p.stat().st_size for p in image.model.iterdir())


@needs_bash
def test_the_build_manifest_and_the_log_carry_the_conversion_statistics(image):
    result = image.run()

    assert result.returncode == 0, result.stdout + result.stderr
    f16_bytes = len(GGUF_HEAD) + len("gguf f16")
    stats = conversion(image)
    assert stats.pop("seconds") >= 0
    assert stats == {"input_format": "huggingface", "weights": "safetensors", "input_bytes": model_bytes(image),
                     "converted": True, "output_format": "gguf", "output_type": "F16", "output_bytes": f16_bytes,
                     "ratio": round(model_bytes(image) / f16_bytes, 2), "functional_test": "skipped"}
    assert list(conversion(image)) == ["input_format", "weights", "input_bytes", "converted", "output_format",
                                       "output_type", "output_bytes", "ratio", "seconds", "functional_test"]
    assert not any("/" in str(v) or "\\" in str(v) for v in stats.values()), "no path: AI callers read manifests"
    assert stats_file(image) == conversion(image)
    log = result.stdout
    assert "[CONVERT] Input: a Hugging Face model folder with SafeTensors weights" in log
    assert f"[CONVERT] Output: tmp-model-F16.gguf, GGUF version 3, 1 tensor, architecture llama, {f16_bytes} B" in log
    assert f"[CONVERT] Statistics: input {model_bytes(image)} B (huggingface, safetensors), output {f16_bytes} B " \
           f"(gguf F16), ratio {stats['ratio']} (input / output), conversion " in log
    assert "Functional test skipped: this image has neither llama-completion nor llama-cli" in log


@needs_bash
def test_the_engine_keeps_the_conversion_statistics_in_the_manifest(image, tmp_path, monkeypatch):
    """BuildEngine._write_build_manifest completes the container's build_info.json and keeps its own keys."""
    from orchestrator.Core.builder import BuildConfiguration, BuildEngine, ModelFormat
    from orchestrator.Core.config_manager import ConfigManager
    monkeypatch.setattr("orchestrator.Core.builder.docker.from_env",
                        lambda: (_ for _ in ()).throw(RuntimeError("no docker")))
    assert image.run().returncode == 0
    engine = BuildEngine(ConfigManager(tmp_path / "configs"), base_dir=tmp_path / "engine")
    try:
        config = BuildConfiguration(build_id="b1", timestamp="t", model_source="org/model", target_arch="Rockchip",
                                    target_format=ModelFormat.GGUF, output_dir=str(image.output))
        info = engine._write_build_manifest(config, image.output)
    finally:
        engine._executor.shutdown(wait=True, cancel_futures=True)
    assert info["conversion"]["output_type"] == "F16" and info["primary"] == "model-Q4_K_M.gguf"


@needs_bash
def test_a_type_the_converter_writes_itself_is_counted_and_kept(image):
    result = image.run(QUANTIZATION="Q8_0")
    assert result.returncode == 0, result.stdout + result.stderr
    assert conversion(image)["output_type"] == "Q8_0"
    assert conversion(image)["output_bytes"] == (image.output / "model-Q8_0.gguf").stat().st_size


@needs_bash
def test_the_functional_test_runs_in_the_build_when_the_image_has_llama_completion(image):
    _script(image.llama / "build" / "bin" / "llama-completion", COMPLETION_STUB)

    result = image.run()

    assert result.returncode == 0, result.stdout + result.stderr
    assert image.calls() == [
        f"convert <root>/input-model --outfile {F16} --outtype f16",
        f"llama-completion -m {F16} -p Hello -n 8 -c 256 -no-cnv --offline --no-warmup --temp 0 --simple-io",
        f"quantize {F16} {OUT}/model-Q4_K_M.gguf Q4_K_M",
    ]
    assert conversion(image)["functional_test"] == "passed"
    assert conversion(image)["functional_test_tool"] == "llama-completion"


@needs_bash
def test_a_gguf_that_does_not_run_fails_the_stage_and_leaves_nothing(image):
    _script(image.llama / "build" / "bin" / "llama-completion", COMPLETION_STUB)

    result = image.run(STUB_FAIL="completion")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "ERROR: The functional test failed: llama-completion could not run tmp-model-F16.gguf (exit 1)" \
        in result.stderr
    assert "tensor data is not within the file bounds" in result.stderr
    assert stage_lines(result)[-1] == ("3/4", "convert", "failed")
    assert "Removed the unfinished tmp-model-F16.gguf." in result.stdout
    assert image.leftovers() == [] and not any(image.output.iterdir())
    assert [c.split()[0] for c in image.calls()] == ["convert", "llama-completion"], "nothing quantized"


# A converter that fails in the middle of its work: STUB_CONVERT says how.
BROKEN_CONVERT_STUB = f"""\
import os, signal, sys
HEAD = {GGUF_HEAD!r}
a = sys.argv
out = a[a.index('--outfile') + 1]
with open(os.environ["STUB_LOG"], 'a') as f:
    f.write('convert ' + ' '.join(a[1:]) + '\\n')
mode = os.environ.get('STUB_CONVERT', '')
with open(out, 'w') as f:
    f.write(HEAD[:-3] if mode != 'ok' else HEAD + 'gguf new')
if mode == 'fails':
    sys.exit(3)
if mode == 'killed':
    os.kill(os.getppid(), signal.SIGKILL)
if mode == 'terminated':
    os.kill(os.getppid(), signal.SIGTERM)
"""


@needs_bash
def test_a_cut_off_gguf_of_the_converter_fails_the_stage_and_is_removed(image):
    _script(image.llama / "convert_hf_to_gguf.py", BROKEN_CONVERT_STUB)

    result = image.run(QUANTIZATION="F16", STUB_CONVERT="cut")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "ERROR: model-F16.gguf is incomplete: its tensor data needs" in result.stderr
    assert not any(image.output.iterdir()), "the broken file is gone"
    assert not (image.work / "stages" / "build" / "convert.done").exists()


@needs_bash
@pytest.mark.parametrize("mode, code", [("cut", 1), ("fails", 3), ("terminated", 143)])
def test_the_file_output_named_is_put_back_when_the_conversion_fails(image, mode, code):
    """--output of 1.x names a file the person may have already: it stays until the new one passed its checks."""
    _script(image.llama / "convert_hf_to_gguf.py", BROKEN_CONVERT_STUB)
    f16 = write_gguf(image.root / "granite.fp16.gguf", "the person's earlier F16")

    result = by_hand(image, "convert", "--input", image.model, "--output", f16.name, "--model-name", "granite",
                     STUB_CONVERT=mode, MODEL_SOURCE=None, MODEL_NAME=None, QUANTIZATION=None)

    assert result.returncode == code, result.stdout + result.stderr
    assert gguf_text(f16) == "the person's earlier F16"
    assert not list(image.root.glob("*.convert-backup"))
    assert "Put back the earlier granite.fp16.gguf." in result.stdout


@needs_bash
def test_a_successful_conversion_replaces_the_file_output_named(image):
    _script(image.llama / "convert_hf_to_gguf.py", BROKEN_CONVERT_STUB)
    f16 = write_gguf(image.root / "granite.fp16.gguf", "earlier")

    result = by_hand(image, "convert", "--input", image.model, "--output", f16.name, "--model-name", "granite",
                     STUB_CONVERT="ok", MODEL_SOURCE=None, MODEL_NAME=None, QUANTIZATION=None)

    assert result.returncode == 0, result.stdout + result.stderr
    assert gguf_text(f16) == "gguf new" and not list(image.root.glob("*.convert-backup"))
    assert "Kept the earlier granite.fp16.gguf as granite.fp16.gguf.convert-backup" in result.stdout
    assert "Replaced the earlier granite.fp16.gguf." in result.stdout


@needs_bash
def test_a_killed_stage_is_undone_at_its_next_start(image):
    """A killed stage runs no cleanup of its own: its next start removes what it wrote and puts back what it
    replaced, before anything else."""
    _script(image.llama / "convert_hf_to_gguf.py", BROKEN_CONVERT_STUB)
    f16 = write_gguf(image.root / "granite.fp16.gguf", "the person's earlier F16")
    options = ("--input", image.model, "--output", f16.name, "--model-name", "granite")
    killed = by_hand(image, "convert", *options, STUB_CONVERT="killed", MODEL_SOURCE=None, MODEL_NAME=None,
                     QUANTIZATION=None)
    assert killed.returncode == 137 and ("3/4", "convert", "failed") in stage_lines(killed)
    assert (image.root / "granite.fp16.gguf.convert-backup").is_file(), "the killed run left its backup"

    again = by_hand(image, "convert", *options, STUB_CONVERT="fails", MODEL_SOURCE=None, MODEL_NAME=None,
                    QUANTIZATION=None)

    assert again.returncode == 3
    assert "An earlier run of this stage did not finish: removing what it wrote." in again.stdout
    assert gguf_text(f16) == "the person's earlier F16" and not list(image.root.glob("*.convert-backup"))


@needs_bash
@pytest.mark.parametrize("kind, message", [
    ("onnx file", "ERROR: The model is an ONNX model (piper.onnx): llama.cpp has no converter from ONNX to GGUF, so no "
                  "GGUF can be built from it. ONNX models, such as the voice and vision models piper-tts, vosk or "
                  "whisper, are built for the NPU of the Rockchip boards in the format rknn (RKNN-Toolkit2); on the "
                  "RK3566 the NPU runs voice and vision models, and the LLM runs on the CPU as a GGUF."),
    ("onnx folder", "ERROR: The model is an ONNX model (voice): llama.cpp has no converter from ONNX to GGUF"),
    ("safetensors file", "ERROR: 'model.safetensors' is a SafeTensors file, the weights only"),
    ("gguf folder", "ERROR: The folder holds GGUF files but no Hugging Face model"),
])
def test_an_input_gguf_cannot_use_is_refused_before_anything_runs(image, tmp_path, kind, message):
    (tmp_path / "in").mkdir()
    source = make_input(tmp_path / "in", kind)

    result = image.run(MODEL_SOURCE=source)

    assert result.returncode == 1, result.stdout + result.stderr
    assert message in result.stderr
    assert image.calls() == [] and not any(image.output.iterdir())
    assert stage_lines(result)[-1] == ("3/4", "convert", "failed")


@needs_bash
def test_an_onnx_model_for_rkllm_is_refused_with_the_rknn_path(image, tmp_path):
    (tmp_path / "in").mkdir()
    result = image.run(TARGET_FORMAT="rkllm", MODEL_SOURCE=make_input(tmp_path / "in", "onnx folder"))
    assert result.returncode == 1
    assert "the format rkllm converts the Hugging Face folder of an LLM, not an ONNX model. ONNX models, such as " \
           "the voice and vision models" in result.stderr
    assert image.calls() == []


@needs_bash
@pytest.mark.parametrize("damage, message", [
    ("cut weights", "ERROR: model.safetensors is incomplete: its tensors need 16 bytes of data, the file has 8"),
    ("missing shard", "ERROR: model.safetensors.index.json lists weights files that are not in the model folder: "
                      "model-00002-of-00002.safetensors"),
    ("broken config", "ERROR: config.json in the model folder is no valid JSON"),
])
def test_a_damaged_model_folder_is_refused_before_converting(image, damage, message):
    if damage == "cut weights":
        (image.model / "model.safetensors").write_bytes(safetensors_bytes({"w": _tensor(0, 16, (4,))},
                                                                          data=b"\0" * 8))
    elif damage == "missing shard":
        (image.model / "model.safetensors.index.json").write_text(json.dumps(
            {"weight_map": {"a": "model.safetensors", "b": "model-00002-of-00002.safetensors"}}))
    else:
        (image.model / "config.json").write_text('{"architectures": ["LlamaFor')

    result = image.run()

    assert result.returncode == 1, result.stdout + result.stderr
    assert message in result.stderr
    assert image.calls() == [], "the converter never ran"


@needs_bash
def test_a_gguf_input_is_checked_counted_and_not_converted(image):
    f16 = write_gguf(image.root / "granite.fp16.gguf", "gguf f16")
    result = by_hand(image, "target", "--input", f16, "--quantization", "Q5_K_M", "--model-name", "granite",
                     MODEL_SOURCE=None, MODEL_NAME=None, QUANTIZATION=None)
    assert result.returncode == 0, result.stdout + result.stderr
    assert conversion(image) == {"input_format": "gguf", "input_bytes": f16.stat().st_size, "converted": False}
    assert "Input: a GGUF file" in result.stdout and "Nothing to convert" in result.stdout

    f16.write_bytes(f16.read_bytes()[:100])  # cut off inside its tensor data
    cut = by_hand(image, "target", "--input", f16, "--quantization", "Q5_K_M", "--model-name", "granite",
                  MODEL_SOURCE=None, MODEL_NAME=None, QUANTIZATION=None)
    assert cut.returncode == 1 and "ERROR: granite.fp16.gguf is incomplete" in cut.stderr


@needs_bash
def test_the_imatrix_job_checks_and_counts_but_leaves_the_run_to_llama_imatrix(image):
    _script(image.llama / "build" / "bin" / "llama-completion", COMPLETION_STUB)
    dataset = image.root / "dataset.txt"
    dataset.write_text("calibration text")
    image.output.rmdir()

    result = image.run(JOB_TYPE="imatrix", DATASET_PATH=dataset)

    assert result.returncode == 0, result.stdout + result.stderr
    assert [c.split()[0] for c in image.calls()] == ["convert", "imatrix"]
    assert "Functional test skipped: llama-imatrix runs the model over the calibration text next." in result.stdout
    assert stats_file(image, "imatrix")["output_type"] == "F16"
    assert image.leftovers() == [] and not image.output.exists()


FAILING_TOOLKIT_STUB = """\
import os, sys
a = sys.argv
if a[1] == '-c':
    print(os.environ["STUB_PKG"])
    sys.exit(0)
out = a[a.index('--output') + 1]
open(out, 'w').write('half an rkllm')
sys.exit(6)
"""


@needs_bash
def test_rkllm_is_counted_and_a_failing_toolkit_leaves_no_half_model(image):
    result = image.run(TARGET_FORMAT="rkllm")
    assert result.returncode == 0, result.stdout + result.stderr
    stats = conversion(image)
    assert (stats["output_format"], stats["output_type"], stats["functional_test"]) == ("rkllm", "w4a16", "skipped")
    assert stats["output_bytes"] == (image.output / "model-rk3588-w4a16.rkllm").stat().st_size
    assert "Functional test skipped: a model for the NPU runs on the board only, where it is measured." in result.stdout

    shutil.rmtree(image.output)
    image.output.mkdir()
    _script(image.rkllm_python, FAILING_TOOLKIT_STUB)
    failed = image.run("--fresh", TARGET_FORMAT="rkllm")
    assert failed.returncode == 6
    assert not any(image.output.iterdir()) and "Removed the unfinished model-rk3588-w4a16.rkllm." in failed.stdout


@needs_bash
def test_rknn_is_counted(image, tmp_path):
    (tmp_path / "in").mkdir()
    source = make_input(tmp_path / "in", "onnx folder")
    result = image.run(TARGET_FORMAT="rknn", TARGET_BOARD="rk3566", QUANTIZATION="", MODEL_SOURCE=source, tool="rknn")
    assert result.returncode == 0, result.stdout + result.stderr
    stats = conversion(image)
    assert stats["input_format"] == "onnx" and stats["input_bytes"] == 4
    assert (stats["output_format"], stats["output_type"]) == ("rknn", "fp16")


# ---------------------------------------------------------------------------------------------------------------
# A target generated from the template has the same checks
# ---------------------------------------------------------------------------------------------------------------

@needs_bash
def test_a_generated_target_converts_with_the_same_checks(tmp_path):
    from test_target_template import generate, run_build, stub_llama
    target = generate(tmp_path)
    assert (target / "scripts" / "model_check.py").read_bytes() == \
        (TEMPLATE / "scripts" / "model_check.py").read_bytes()
    llama = stub_llama(tmp_path)

    result, work = run_build(target / "modules" / "build.sh", tmp_path, llama, "Q4_K_M")

    assert result.returncode == 0, result.stdout + result.stderr
    info = json.loads((work / "output" / "build_info.json").read_text())
    assert info["conversion"]["output_type"] == "F16" and info["conversion"]["input_format"] == "huggingface"

    onnx = tmp_path / "voice.onnx"
    onnx.write_bytes(b"onnx")
    env = {"PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin", "HOME": str(tmp_path),
           "BUILD_CACHE_DIR": str(tmp_path / "work2"), "LLAMA_CPP_PATH": str(llama), "MODEL_SOURCE": str(onnx),
           "QUANTIZATION": "Q4_K_M", "TARGET_FORMAT": "gguf", "JOB_TYPE": "build"}
    refused = subprocess.run(["bash", str(target / "modules" / "build.sh")], env=env, capture_output=True, text=True,
                             timeout=60)
    assert refused.returncode == 1
    assert "llama.cpp has no converter from ONNX to GGUF" in refused.stderr
    assert "such as the format rknn of the Rockchip target" in refused.stderr


def test_the_stats_reach_build_info_through_the_one_file_the_target_stage_reads():
    """The convert stage writes convert_stats.json in the stage folder; the target stage of every shipped target puts
    it into build_info.json as "conversion" (one name on both sides)."""
    for target in (ROCKCHIP, TEMPLATE):
        convert = (target / "modules" / "convert_module.sh").read_text(encoding="utf-8")
        stage = (target / "modules" / "target_module.sh").read_text(encoding="utf-8")
        assert 'CONVERT_STATS="$PLAN_DIR/convert_stats.json"' in convert
        assert 'CONVERT_STATS="$PLAN_DIR/convert_stats.json"' in stage
        assert '\\"conversion\\": $(cat "$CONVERT_STATS")' in stage


def test_the_functional_test_is_bounded():
    for target in (ROCKCHIP, TEMPLATE):
        convert = (target / "modules" / "convert_module.sh").read_text(encoding="utf-8")
        assert "FUNCTIONAL_TEST_SECONDS=120" in convert
    assert mc.TEST_TOKENS == 8

