"""The large models (DESIGN "Build pipeline and target modules", the row "Large models").

The check of this computer's disk and memory before a build, the F16 intermediate made once per build and once per
model, a GGUF file as input (passthrough and quantization again), per-tensor types and their measurement by Auto-Tuning,
the importance matrix from a file or Hugging Face, and the tokenizer and chat-template check at the end of every GGUF
build: the Core rules, the stage scripts (with the stub tools of tests/test_build_stages.py), the build service, the
build engine's steps, recipes, the CLI, the GUI and the AI's tools.
"""

import json
import os
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import NO_POSIX_BASH, REPO_ROOT, posix_shell
from model_files import gguf_bytes, write_gguf
from test_build_stages import Image, _script

needs_bash = pytest.mark.skipif(os.name == "nt" or posix_shell("bash") is None, reason=NO_POSIX_BASH)
ROCKCHIP = REPO_ROOT / "targets" / "Rockchip"


def _load_model_check():
    import types
    path = ROCKCHIP / "scripts" / "model_check.py"
    module = types.ModuleType("model_check")
    module.__file__ = str(path)
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)  # noqa: S102
    return module


mc = _load_model_check()


def _str(text: str) -> bytes:
    data = text.encode("utf-8")
    return struct.pack("<Q", len(data)) + data


def kv_u32(key: str, value: int):
    return (key, 4, struct.pack("<I", value))


def kv_str(key: str, value: str):
    return (key, 8, _str(value))


def kv_strings(key: str, values):
    return (key, 9, struct.pack("<IQ", 8, len(values)) + b"".join(_str(v) for v in values))


def kv_ints(key: str, values):
    return (key, 9, struct.pack("<IQ", 5, len(values)) + b"".join(struct.pack("<i", v) for v in values))


def named_gguf(path: Path, tensors, kv=(), arch: str = "llama") -> Path:
    """A GGUF file with named F32 tensors ((name, shape), ...) and the key-value pairs `kv`."""
    pairs = [(b"general.architecture", 8, _str(arch))] + [(k.encode(), t, v) for k, t, v in kv]
    head = b"GGUF" + struct.pack("<IQQ", 3, len(tensors), len(pairs))
    for key, kind, value in pairs:
        head += struct.pack("<Q", len(key)) + key + struct.pack("<I", kind) + value
    offset, data = 0, b""
    for name, shape in tensors:
        count = 1
        for d in shape:
            count *= d
        head += _str(name) + struct.pack("<I", len(shape)) + b"".join(struct.pack("<Q", d) for d in shape)
        head += struct.pack("<IQ", 0, offset)
        size = count * 4
        data += b"\0" * size + b"\0" * (-size % 32)
        offset += size + (-size % 32)
    head += b"\0" * (-len(head) % 32)
    path.write_bytes(head + data)
    return path


# --- per-tensor types (Core/tensor_types.py) ------------------------------------------------------------------------

def test_per_tensor_types_are_checked_and_become_the_quantizers_options():
    from orchestrator.Core import tensor_types as tt
    assert tt.normalize(["FFN_DOWN=Q6_K, output=q8_0", "token_embd=q4_k"]) == \
        ["ffn_down=q6_k", "output=q8_0", "token_embd=q4_k"]
    assert tt.quantize_args(["ffn_down=q6_k", "output=q8_0", "token_embd=q4_k"]) == [
        "--tensor-type", "ffn_down=q6_k", "--output-tensor-type", "q8_0", "--token-embedding-type", "q4_k"]
    assert tt.env_value([r"blk\.(0|1)\.attn_v=q8_0"]) == r"blk\.(0|1)\.attn_v=q8_0"
    for bad, word in (("ffn_down", "PATTERN=TYPE"), ("ffn_down=q9_x", "not a tensor type"), ("a b=q4_k", "may hold"),
                      ("ffn_(=q4_k", "no regular expression"), ("=q4_k", "no tensor pattern")):
        with pytest.raises(tt.TensorTypeError, match=word):
            tt.normalize([bad])
    with pytest.raises(tt.TensorTypeError, match="given twice"):
        tt.normalize(["ffn_down=q6_k", "ffn_down=q8_0"])
    with pytest.raises(tt.TensorTypeError, match="At most 32"):
        tt.normalize([f"blk\\.{i}\\.ffn_up=q8_0" for i in range(33)])
    # the first match wins; output and token_embd only by their own keys (attn_output is no output tensor)
    entries = ["output=q8_0", "ffn_.*=q6_k", "ffn_down=q8_0"]
    assert tt.type_for(entries, "blk.3.ffn_down.weight", "ffn_down") == "Q6_K"
    assert tt.type_for(entries, "output.weight", "output") == "Q8_0"
    assert tt.type_for(entries, "blk.0.attn_output.weight", "attn_output") is None


def test_the_memory_estimate_counts_the_per_tensor_types():
    from orchestrator.Core import memory_estimate as me
    shape = me.ModelShape(parameters=134515008, hidden=576, layers=30, heads=9, kv_heads=3, head_dim=64,
                          intermediate=1536, vocab=49152, tied=True)
    plain = me.weights_bytes(shape, "Q4_K_M")[0]
    more = me.weights_bytes(shape, "Q4_K_M", tensor_types=["ffn_.*=q8_0"])[0]
    assert more > plain   # every feed-forward matrix at 8.5 bits instead of the mix's 4.5 to 6.5
    est = me.estimate(shape, "Q4_K_M", board="rk3588", ram_mib=8192, tensor_types=["ffn_.*=q8_0"])
    assert est.weights_bytes == more


# --- a given importance matrix (Core/imatrix_source.py) ---------------------------------------------------------------

def _dat(path: Path, names) -> Path:
    body = struct.pack("<i", len(names))
    for name in names:
        raw = name.encode()
        body += struct.pack("<i", len(raw)) + raw + struct.pack("<ii", 10, 4) + b"\0" * 16
    path.write_bytes(body)
    return path


def test_a_given_importance_matrix_is_read_in_both_formats_and_refused_otherwise(tmp_path):
    from orchestrator.Core import imatrix_source as im
    assert im.check_file(_dat(tmp_path / "m.dat", ["blk.0.attn_q.weight"]))["format"] == "dat"
    gguf = named_gguf(tmp_path / "m.gguf", [("blk.0.attn_q.weight.in_sum2", (4,))], kv=[kv_str("general.type",
                                                                                                "imatrix")])
    assert im.check_file(gguf)["format"] == "gguf"
    model = named_gguf(tmp_path / "model.gguf", [("blk.0.attn_q.weight", (4, 4))])
    with pytest.raises(im.ImatrixError, match="no importance matrix"):
        im.check_file(model)
    (tmp_path / "text.txt").write_text("hello")
    with pytest.raises(im.ImatrixError, match="not an importance matrix"):
        im.check_file(tmp_path / "text.txt")
    source = im.parse_hub("hf:owner/model-GGUF/sub/imatrix.gguf")
    assert (source.repo, source.filename, source.name) == ("owner/model-GGUF", "sub/imatrix.gguf", "imatrix.gguf")
    for bad in ("hf:owner/model", "hf:owner/model/../x", "hf:/x/y"):
        with pytest.raises(im.ImatrixError):
            im.parse_hub(bad)
    assert im.record(None, None) == {"kind": "computed"}
    assert "path" not in json.dumps(im.record(im.ImatrixSource(im.FILE, path=str(tmp_path / "m.dat")), "ab" * 32))


def test_the_convert_stage_compares_a_given_matrix_with_the_models_weight_matrices(tmp_path):
    model = named_gguf(tmp_path / "model.gguf", [("token_embd.weight", (4, 8))] +
                       [(f"blk.{i}.{n}.weight", (4, 4)) for i in range(2) for n in ("attn_q", "ffn_down")])
    good = _dat(tmp_path / "good.dat", [f"blk.{i}.{n}.weight" for i in range(2) for n in ("attn_q", "ffn_down")])
    assert mc.imatrix_coverage(good, model) == (4, 4)
    other = _dat(tmp_path / "other.dat", ["blk.0.attn_q.weight", "blk.9.mlp.weight"])
    with pytest.raises(mc.Problem, match="names 1 of the 4 weight matrices"):
        mc.imatrix_coverage(other, model)
    gguf = named_gguf(tmp_path / "m.gguf", [(f"blk.{i}.{n}.weight.in_sum2", (4,)) for i in range(2)
                                            for n in ("attn_q", "ffn_down")], kv=[kv_str("general.type", "imatrix")])
    assert mc.imatrix_coverage(gguf, model) == (4, 4)


# --- a GGUF input: its own type, passthrough or quantized again -------------------------------------------------------

def test_a_gguf_inputs_type_decides_passthrough_and_requantization(tmp_path):
    from orchestrator.Core import build_resources as br
    q8 = tmp_path / "m-Q8_0.gguf"
    q8.write_bytes(gguf_bytes(kv=(kv_u32("general.file_type", 7),)))
    assert br.gguf_file_type(q8) == "Q8_0" and mc.gguf_type(q8) == "Q8_0"
    same = br.gguf_input(q8, None)
    assert (same.mode, same.file_type) == ("passthrough", "Q8_0") and "passed through" in same.note()
    again = br.gguf_input(q8, "Q4_K_M")
    assert again.mode == "quantize" and again.requantize and not again.upward
    assert "loses more than quantizing its 16-bit original" in again.note()
    up = br.gguf_input(tmp_path / "m-Q8_0.gguf", "F16")
    assert up.upward and "gains nothing" in up.note()
    f16 = tmp_path / "m-F16.gguf"
    f16.write_bytes(gguf_bytes(kv=(kv_u32("general.file_type", 1),)))
    assert not br.gguf_input(f16, "Q4_K_M").requantize
    plain = tmp_path / "plain.gguf"
    plain.write_bytes(gguf_bytes())
    assert br.gguf_file_type(plain) is None and mc.gguf_type(plain) == "unknown"
    assert br.FILE_TYPES == mc.FILE_TYPES   # the stage carries the same map


def test_the_disk_rule_follows_the_build(tmp_path):
    from orchestrator.Core import disk_plan
    gb = 1000 ** 3
    full = disk_plan.build_needs(10 * gb, "gguf", "Q4_K_M", tmp_path, tmp_path)
    assert full[1].bytes == 10 * gb + 6 * gb        # the F16 margin and the output
    kept = disk_plan.build_needs(10 * gb, "gguf", "Q4_K_M", tmp_path, tmp_path, f16_cached=True)
    assert kept[1].bytes == 6 * gb                  # the kept F16 is reused: no margin
    passthrough = disk_plan.build_needs(4 * gb, "gguf", "Q4_K_M", tmp_path, tmp_path, cached=4 * gb,
                                        gguf_input=True, passthrough=True)
    assert [n.bytes for n in passthrough] == [4 * gb, 8 * gb]


def test_the_check_before_a_build_says_disk_and_memory_and_who_decides(tmp_path):
    from orchestrator.Core import build_resources as br
    hw = SimpleNamespace(ram_total=8 * 1024 ** 3)

    def usage(free):
        return lambda path: SimpleNamespace(total=1000 * 1000 ** 3, used=0, free=free)
    ok = br.check(size=2 * 10 ** 9, cached=0, fmt="gguf", quantization="Q4_K_M", use_imatrix=True, cache_dir=tmp_path,
                  output_dir=tmp_path, parameters=10 ** 9, hardware=hw, disk_usage=usage(500 * 1000 ** 3))
    assert ok.state == "fits" and not ok.risky and ok.lines()[0].startswith("Disk: needs about")
    short = br.check(size=200 * 10 ** 9, cached=0, fmt="gguf", quantization="Q4_K_M", use_imatrix=True,
                     cache_dir=tmp_path, output_dir=tmp_path, parameters=100 * 10 ** 9, hardware=hw,
                     disk_usage=usage(50 * 1000 ** 3), f16_kept_bytes=30 * 10 ** 9)
    assert short.risky and short.disk_state == "does_not_fit" and short.memory_state == "does_not_fit"
    assert any("Kept F16 intermediates use" in line and "build f16 delete" in line for line in short.lines())
    facts = short.as_dict()
    assert facts["state"] == "does_not_fit" and "path" not in json.dumps(facts)
    unknown = br.check(size=0, cached=0, fmt="gguf", quantization="Q4_K_M", use_imatrix=False, cache_dir=tmp_path,
                       output_dir=tmp_path, parameters=None, hardware=None)
    assert unknown.state == "unknown" and not unknown.risky


# --- the kept F16 intermediates (Core/f16_cache.py) ----------------------------------------------------------------------

class _Audit:
    def __init__(self):
        self.rows = []

    def record(self, *args, **kwargs):
        self.rows.append((args, kwargs))


COMMIT = "a" * 40


def test_an_f16_is_kept_per_model_commit_and_pin_and_deleted_only_through_the_list(tmp_path):
    from orchestrator.Core import f16_cache
    from orchestrator.Core.deletion import accept
    assert f16_cache.key("Owner/Model", COMMIT, "b1") == f16_cache.key("owner/model", COMMIT, "b1")
    assert f16_cache.key("owner/model", COMMIT, "b2") != f16_cache.key("owner/model", COMMIT, "b1")
    assert f16_cache.key("owner/model", "main", "b1") is None and f16_cache.key("/a/folder", COMMIT, "b1") is None
    cache = f16_cache.F16Cache(tmp_path / "cache")
    made = tmp_path / "work" / "model-F16.gguf"
    made.parent.mkdir()
    made.write_bytes(b"GGUF" + b"\0" * 60)
    key = f16_cache.key("owner/model", COMMIT, "b1")
    entry = cache.keep(key, made, repo="owner/model", commit=COMMIT, pin="b1")
    assert entry.size_bytes == 64 and not made.exists() and cache.total_bytes() == 64
    assert cache.entry(key).repo == "owner/model"
    (entry.folder / f16_cache.FILE_NAME).write_bytes(b"changed")      # a changed file is never reused
    assert cache.entry(key) is None
    (entry.folder / f16_cache.FILE_NAME).write_bytes(b"GGUF" + b"\0" * 60)
    plan = cache.deletion_plan()
    assert [i.kind for i in plan.items] == ["f16_intermediate"]
    with pytest.raises(PermissionError):
        cache.delete(plan, audit=_Audit())
    with pytest.raises(PermissionError):
        cache.delete(accept(plan, "mcp", "permanent"), audit=_Audit())   # never an AI
    audit = _Audit()
    result = cache.delete(accept(plan, "cli", "permanent"), audit=audit)
    assert result.done and audit.rows and cache.entries() == [] and cache.deletion_plan() is None


def test_the_engine_reuses_a_kept_f16_and_keeps_a_new_one_only_with_room(tmp_path, monkeypatch):
    from orchestrator.Core import build_large, f16_cache
    from orchestrator.Core.build_formats import ModelFormat
    monkeypatch.setattr(build_large, "_pin", lambda: "b11429")
    progress = SimpleNamespace(logs=[], add_log=lambda t: progress.logs.append(t), add_warning=lambda t: None)

    def config(**kw):
        values = dict(model_source="owner/model", model_revision=COMMIT, target_format=ModelFormat.GGUF,
                      quantization="Q4_K_M", use_imatrix=False, tuning=None, local_inputs=None, large_model=None)
        values.update(kw)
        return SimpleNamespace(**values)
    cache_dir, temp = tmp_path / "cache", tmp_path / "cache" / "builds" / "b1"
    cfg = config()
    vols, env = build_large.f16_plan(cfg, cache_dir, temp)
    assert vols == {} and env == {"F16_FILE": f16_cache.WORK_FILE} and (temp / "work" / "f16").is_dir()
    assert build_large.f16_plan(config(quantization="Q8_0"), cache_dir, temp) == ({}, {})   # the converter's type
    assert build_large.f16_plan(config(model_source=str(tmp_path / "m.gguf")), cache_dir, temp) == ({}, {})
    made = temp / "work" / "f16" / f16_cache.FILE_NAME
    made.write_bytes(b"GGUF" + b"\0" * 60)
    roomy = lambda path: SimpleNamespace(total=1000 * 1000 ** 3, used=0, free=900 * 1000 ** 3)  # noqa: E731
    tight = lambda path: SimpleNamespace(total=1000 * 1000 ** 3, used=0, free=30 * 1000 ** 3)  # noqa: E731
    build_large.keep_f16(cfg, progress, cache_dir, temp, {f16_cache.SETTING: False}, disk_usage=roomy)
    assert cfg.large_model["f16"]["state"] == "not_kept" and "is off" in cfg.large_model["f16"]["reason"]
    cfg = config()
    build_large.keep_f16(cfg, progress, cache_dir, temp, {}, disk_usage=tight)
    assert "twice its reserve" in cfg.large_model["f16"]["reason"]
    local = config(model_source=str(tmp_path))
    build_large.keep_f16(local, progress, cache_dir, temp, {}, disk_usage=roomy)
    assert "never kept" in local.large_model["f16"]["reason"]
    cfg = config()
    build_large.keep_f16(cfg, progress, cache_dir, temp, {}, disk_usage=roomy)
    assert cfg.large_model["f16"]["state"] == "kept" and any("build f16 list" in line for line in progress.logs)
    again = config()
    vols, env = build_large.f16_plan(again, cache_dir, tmp_path / "b2")
    assert env == {"F16_FILE": f"{f16_cache.MOUNT}/{f16_cache.FILE_NAME}", "F16_KEPT": "1"}
    assert list(vols.values()) == [{"bind": f16_cache.MOUNT, "mode": "ro"}]
    assert again.large_model["f16"]["state"] == "reused"
    assert build_large.record(again)["f16_intermediate"]["state"] == "reused"


def test_a_given_matrix_is_copied_while_hashed_and_recorded_without_a_path(tmp_path):
    from orchestrator.Core import build_large
    matrix = _dat(tmp_path / "given.dat", ["blk.0.attn_q.weight"])
    progress = SimpleNamespace(logs=[], add_log=lambda t: progress.logs.append(t))
    cfg = SimpleNamespace(large_model={"imatrix_file": str(matrix), "tensor_types": ["ffn_down=q6_k"]},
                          local_inputs=None, use_imatrix=True)
    assert build_large.stage_imatrix(cfg, progress, tmp_path / "build")
    copy = tmp_path / "build" / "imatrix" / "imatrix.dat"
    assert copy.read_bytes() == matrix.read_bytes()
    record = build_large.record(cfg)
    assert record["imatrix_source"]["kind"] == "file" and record["imatrix_source"]["format"] == "dat"
    assert record["tensor_types"] == ["ffn_down=q6_k"] and record["tensor_types_measured"] is False
    assert str(tmp_path) not in json.dumps(record)
    assert build_large.stage_env(cfg) == {"TENSOR_TYPES": "ffn_down=q6_k", "IMATRIX_GIVEN": "1"}
    hub = SimpleNamespace(large_model={"imatrix_file": "hf:owner/model-GGUF/imatrix.dat"}, local_inputs=None)
    fetched = []

    def download(repo, filename):
        fetched.append((repo, filename))
        folder = tmp_path / "models" / "hf" / "owner--model-GGUF" / ("c" * 12)
        folder.mkdir(parents=True)
        return str(_dat(folder / "imatrix.dat", ["blk.0.attn_q.weight"]))
    assert build_large.stage_imatrix(hub, progress, tmp_path / "build2", download=download)
    assert fetched == [("owner/model-GGUF", "imatrix.dat")]
    assert hub.large_model["imatrix_source"]["commit"] == "c" * 12


# --- the stages (stub tools of tests/test_build_stages.py) ------------------------------------------------------------

@pytest.fixture
def image(tmp_path):
    return Image(tmp_path)


def _gguf_input(image, file_type: int) -> Path:
    path = image.root / "model-in.gguf"
    path.write_bytes(gguf_bytes(kv=(kv_u32("general.file_type", file_type),)))
    return path


@needs_bash
def test_a_gguf_input_at_its_own_type_is_passed_through(image):
    source = _gguf_input(image, 7)
    result = image.run(MODEL_SOURCE=source, QUANTIZATION="Q8_0")
    assert result.returncode == 0, result.stdout + result.stderr
    assert image.calls() == [] and "passed through as it is" in result.stdout
    assert (image.output / "model-Q8_0.gguf").read_bytes() == source.read_bytes()


@needs_bash
def test_a_quantized_gguf_input_is_quantized_again_with_allow_requantize(image):
    source = _gguf_input(image, 7)
    result = image.run(MODEL_SOURCE=source, QUANTIZATION="Q4_K_M")
    assert result.returncode == 0, result.stdout + result.stderr
    (call,) = image.calls()
    assert call.startswith("quantize --allow-requantize ") and call.endswith("Q4_K_M")
    assert "quantized already" in result.stderr and source.exists()


@needs_bash
def test_per_tensor_types_reach_the_quantizer_and_need_a_quantized_type(image):
    entries = r"ffn_down=q6_k output=q8_0 token_embd=q4_k blk\.0\..*=q8_0"
    result = image.run(TENSOR_TYPES=entries)
    assert result.returncode == 0, result.stdout + result.stderr
    quantize = [c for c in image.calls() if c.startswith("quantize")][0]
    assert ("--tensor-type ffn_down=q6_k --output-tensor-type q8_0 --token-embedding-type q4_k "
            r"--tensor-type blk\.0\..*=q8_0 ") in quantize
    (image.root / "again").mkdir()
    refused = Image(image.root / "again").run(TENSOR_TYPES="ffn_down=q6_k", QUANTIZATION="F16")
    assert refused.returncode != 0 and "Per-tensor types need a type llama-quantize makes" in refused.stderr


@needs_bash
def test_the_imatrix_job_and_the_build_job_share_one_f16_and_a_kept_one_is_never_converted(image):
    f16 = image.work / "f16" / "model-F16.gguf"
    dataset = image.root / "dataset.txt"
    dataset.write_text("calibration text")
    first = image.run(JOB_TYPE="imatrix", DATASET_PATH=dataset, F16_FILE=f16)
    assert first.returncode == 0, first.stdout + first.stderr
    assert f16.is_file(), "the F16 stays for the build job"
    second = image.run(QUANTIZATION="Q4_K_M", USE_IMATRIX="1", IMATRIX_PATH=image.imatrix / "imatrix.dat",
                       F16_FILE=f16, DATASET_PATH=dataset)
    assert second.returncode == 0, second.stdout + second.stderr
    kinds = [c.split()[0] for c in image.calls()]
    assert kinds == ["convert", "imatrix", "quantize"], "converted once for both jobs"
    assert "Reusing the F16 intermediate this build converted already" in second.stdout and f16.is_file()

    (image.root / "kept").mkdir()
    kept_image = Image(image.root / "kept")
    (kept_image.root / "kept").mkdir()
    kept = write_gguf(kept_image.root / "kept" / "model-F16.gguf", "gguf f16")
    result = kept_image.run(F16_FILE=kept, F16_KEPT="1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert [c.split()[0] for c in kept_image.calls()] == ["quantize"] and kept.is_file()
    assert "kept from an earlier build of this model" in result.stdout
    (image.root / "gone").mkdir()
    gone = Image(image.root / "gone").run(F16_FILE=image.root / "nowhere" / "model-F16.gguf", F16_KEPT="1")
    assert gone.returncode != 0 and "kept F16 intermediate of this model is missing" in gone.stderr


@needs_bash
def test_a_given_matrix_of_another_model_stops_the_build_before_quantizing(image):
    matrix = _dat(image.root / "given.dat", ["blk.0.attn_q.weight"])
    result = image.run(QUANTIZATION="Q4_K_M", USE_IMATRIX="1", IMATRIX_PATH=matrix, IMATRIX_GIVEN="1")
    assert result.returncode != 0 and "no weight matrices to compare the importance matrix with" in result.stderr
    assert not [c for c in image.calls() if c.startswith("quantize")]


@needs_bash
def test_the_tokenizer_check_runs_at_the_end_of_a_gguf_build_and_never_fails_it(image):
    result = image.run(TOKENIZER_CHECK="1")
    assert result.returncode == 0, result.stdout + result.stderr
    check = json.loads((image.output / "build_info.json").read_text())["tokenizer_check"]
    assert check["status"] == "warn" and "holds no tokenizer" in check["warnings"][0]
    assert "Tokenizer and chat template: 1 warning(s)" in result.stdout


# --- the tokenizer and chat-template check (scripts/model_check.py tokenizer) ---------------------------------------------

TOKENS = ["<unk>", "<s>", "</s>", "<|im_start|>", "<|im_end|>", "Hello", " world"]
TEMPLATE = "{% for m in messages %}<|im_start|>{{ m['content'] }}<|im_end|>{% endfor %}"


def tokenizer_gguf(path: Path, template=TEMPLATE, eos: int = 2, vocab_rows: int = len(TOKENS)) -> Path:
    kv = [kv_str("tokenizer.ggml.model", "gpt2"), kv_strings("tokenizer.ggml.tokens", TOKENS),
          kv_ints("tokenizer.ggml.token_type", [2, 3, 3, 3, 3, 1, 1]), kv_u32("tokenizer.ggml.bos_token_id", 1),
          kv_u32("tokenizer.ggml.eos_token_id", eos)]
    if template is not None:
        kv.append(kv_str("tokenizer.chat_template", template))
    return named_gguf(path, [("token_embd.weight", (4, vocab_rows))], kv=kv)


COMPLETION_TOKENS_STUB = """\
import sys
a = sys.argv
if a[1:] == ['--help']:
    print('-m -p -n -no-cnv --verbose-prompt --offline')
    sys.exit(0)
print('main: prompt: ' + a[a.index('-p') + 1])
print('main: number of tokens in prompt = 3')
for i, piece in ((5, 'Hello'), (6, ' world'), (2, '</s>')):
    print('0.00.1 I %6d -> %r' % (i, piece))
"""


def test_the_tokenizer_check_compares_with_the_source_and_says_what_it_could_not_check(tmp_path):
    tools = tmp_path / "bin"
    _script(tools / "llama-completion", COMPLETION_TOKENS_STUB)
    source = tmp_path / "hf"
    source.mkdir()
    (source / "config.json").write_text(json.dumps({"eos_token_id": 2, "bos_token_id": 1}))
    (source / "tokenizer_config.json").write_text(json.dumps({"chat_template": TEMPLATE}))
    report = mc.tokenizer_check(tokenizer_gguf(tmp_path / "m.gguf"), source, tools, 30)
    assert report["status"] == "pass", report
    assert "chat template: the source's" in report["checks"] and "EOS: the source's" in report["checks"]
    assert report["tokens"] == 3 and report["vocabulary"] == len(TOKENS)
    assert any("tokenizer.json" in s for s in report["skipped"])     # no tokenizer.json: said, not a failure

    other = mc.tokenizer_check(tokenizer_gguf(tmp_path / "o.gguf", template="{{ messages }}", eos=4,
                                              vocab_rows=9), source, tools, 30)
    warnings = " ".join(other["warnings"])
    assert other["status"] == "warn"
    assert "chat template differs from the source's" in warnings and "EOS id 4 is none of the source's" in warnings
    assert "embeddings have 9 rows" in warnings
    missing = mc.tokenizer_check(tokenizer_gguf(tmp_path / "n.gguf", template=None), source, tools, 30)
    assert any("the GGUF none" in w for w in missing["warnings"])
    # a GGUF input: its own header is the source
    assert mc.tokenizer_check(tokenizer_gguf(tmp_path / "q.gguf"), tmp_path / "m.gguf", tools, 30)["status"] == "pass"


def test_the_host_reads_the_check_as_untrusted_text():
    from orchestrator.Core.tokenizer_check import ai_facts, summary
    lines, warnings = summary({"status": "warn", "checks": ["vocabulary: 7 tokens"], "warnings": ["x\x1b[31m"],
                               "skipped": [], "tokens": 3, "path": "/secret"})
    assert lines[0] == "Tokenizer and chat template: 1 warning(s)" and warnings == ["Tokenizer and chat template: x [31m"]
    assert "path" not in ai_facts({"status": "pass", "path": "/secret"}) and summary(None) == ([], [])


# --- Auto-Tuning measures per-tensor recipes ----------------------------------------------------------------------------

def test_a_per_tensor_recipe_is_a_tuning_candidate_and_is_built_with_its_types():
    from orchestrator.Core import tuning
    base, entries = tuning.parse_recipe("Q4_K_M+ffn_down=q6_k,attn_v=q8_0")
    assert (base, entries) == ("Q4_K_M", ["ffn_down=q6_k", "attn_v=q8_0"])
    with pytest.raises(tuning.TuningError):
        tuning.parse_recipe("Q4_K_M")
    cands, _ = tuning.plan_candidates(offered=["Q8_0", "Q4_K_M", "Q6_K"], recommended=["Q6_K"], yours=[],
                                      estimate_for=lambda q, m: None, reads_imatrix=lambda q: q == "Q4_K_M",
                                      recipes=[(base, entries)])
    recipe = cands[0]
    assert recipe.tensor_types == entries and recipe.label.startswith("Q4_K_M-imatrix+")
    assert recipe.spec == f"Q4_K_M:i:{recipe.tensor_id}"
    plan = tuning.TuningPlan(candidates=cands)
    assert plan.env()[f"TUNE_TT_{recipe.tensor_id}"] == "ffn_down=q6_k attn_v=q8_0"
    with pytest.raises(tuning.TuningError, match="quantized types"):
        tuning.plan_candidates(offered=["Q8_0"], recommended=[], yours=[], estimate_for=lambda q, m: None,
                               reads_imatrix=lambda q: False, recipes=[("Q8_0", entries)])
    report = {"candidates": [{"label": recipe.label, "quantization": "Q4_K_M", "imatrix": True,
                              "tensor_types": entries}], "recommendation": {"label": recipe.label}}
    assert tuning.chosen_row(report)["tensor_types"] == entries
    assert tuning.build_choice(report) == ("Q4_K_M", True)


TUNE_QUANTIZE_STUB = """\
import os, sys
with open(os.environ["STUB_LOG"], 'a') as f:
    f.write('quantize ' + ' '.join(sys.argv[1:]) + '\\n')
open(sys.argv[-2], 'w').write('candidate')
"""
TUNE_PERPLEXITY_STUB = """\
import os, sys
a = sys.argv
if '--kl-divergence' not in a:
    open(a[a.index('--kl-divergence-base') + 1], 'w').write('logits')
    print('Final estimate: PPL = 10.0 +/- 0.1')
else:
    print('Mean    KLD:   0.012345 ±   0.000500')
    print('Same top p: 95.000 ± 0.100 %')
"""


@needs_bash
def test_tune_sh_measures_a_recipe_with_its_per_tensor_types(tmp_path):
    layer = REPO_ROOT / "orchestrator" / "runtime_layer"
    bin_dir = tmp_path / "llama" / "build" / "bin"
    _script(bin_dir / "llama-quantize", TUNE_QUANTIZE_STUB)
    _script(bin_dir / "llama-perplexity", TUNE_PERPLEXITY_STUB)
    (tmp_path / "work" / "f16").mkdir(parents=True)
    f16 = write_gguf(tmp_path / "work" / "f16" / "model-F16.gguf", "f16")
    text = tmp_path / "eval.txt"
    text.write_text("text")
    log = tmp_path / "calls.log"
    env = {"PATH": f"{Path(sys.executable).parent}{os.pathsep}/usr/bin{os.pathsep}/bin",
           "BUILD_CACHE_DIR": str(tmp_path / "cache"), "BUILD_SCRATCH_DIR": str(tmp_path / "work"),
           "LLAMA_CPP_PATH": str(tmp_path / "llama"), "TUNE_TEXT": str(text), "F16_FILE": str(f16),
           "TUNE_CANDIDATES": "Q4_K_M:-:0a1b2c3d Q6_K:-", "TUNE_TT_0a1b2c3d": r"ffn_down=q6_k output=q8_0 blk\..*=q8_0",
           "STUB_LOG": str(log)}
    import subprocess
    done = subprocess.run([posix_shell("sh") or posix_shell("bash"), str(layer / "tune.sh")], env=env,
                          capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    assert done.returncode == 0, done.stdout + done.stderr
    calls = log.read_text().splitlines()
    assert calls[0] == (r"quantize --tensor-type ffn_down=q6_k --output-tensor-type q8_0 --tensor-type blk\..*=q8_0 "
                        f"{f16} {tmp_path / 'work' / 'tune' / 'candidate.gguf'} Q4_K_M")
    assert calls[1].startswith(f"quantize {f16} ")
    results = tmp_path / "cache" / "output" / "tuning"
    assert (results / "Q4_K_M+0a1b2c3d.info").is_file() and (results / "Q6_K.info").is_file()


# --- the build service, the identical-build check and recipes ------------------------------------------------------------

def test_the_identical_build_check_and_the_recipe_carry_the_per_tensor_types():
    from orchestrator.Core import build_identity as bi
    base = dict(model="o/m", revision=COMMIT, weights=None, target="ARM64", target_sha256="x", board="b", fmt="gguf",
                quantization="Q4_K_M", imatrix=True, dataset_sha256=None, optimization="balanced",
                max_context_length=None, use_gpu=False)
    plain, recipe = bi.build_options(**base), bi.build_options(**base, tensor_types=["ffn_down=q6_k"])
    assert "tensor_types" not in plain and bi.compare(recipe, plain)[1] == ["tensor_types"]
    assert bi.options_sha256(plain) == bi.options_sha256({**plain, "tensor_types": None})   # the digest stays


def test_a_recipe_with_per_tensor_types_is_format_3():
    from orchestrator.Core.recipe_optimization import RecipeFormatError, canonical, from_build_info, read
    info = {"model_source": "owner/model", "model_revision": COMMIT, "target": "ARM64", "target_pack_sha256": "a" * 64,
            "format": "gguf", "quantization": "Q4_K_M", "board": "bcm2712", "primary": "m.gguf",
            "artifacts": [{"file": "m.gguf", "bytes": 10, "sha256": "b" * 64}], "imatrix": True,
            "tensor_types": ["ffn_down=q6_k"]}
    recipe = from_build_info(info, author="alice")
    assert recipe.schema_version == 3 and recipe.options.tensor_types == ["ffn_down=q6_k"]
    data = recipe.model_dump(mode="json", exclude_none=True)
    with pytest.raises(RecipeFormatError, match="format 3"):
        read({**data, "schema_version": 2})
    plain = from_build_info({**info, "tensor_types": None}, author="alice")
    assert plain.schema_version == 1
    assert "tensor_types" not in canonical(plain.model_dump(mode="json"))["options"]


class _Service:
    """The build service's large-model checks without a framework."""

    def __init__(self, offline=False):
        from orchestrator.Core.build_service import BuildService
        self.service = BuildService.__new__(BuildService)
        self.service.framework = SimpleNamespace(config={"offline_mode": offline})

    def check(self, request, fmt="gguf", quant="Q4_K_M", model="owner/model", use_imatrix=False, given=True):
        return self.service._large_model_checks(request, fmt, quant, model, use_imatrix, None, None, None,
                                                quantization_given=given)


def test_the_service_checks_the_large_model_options(tmp_path, monkeypatch):
    from orchestrator.Core.build_service import BuildService, BuildServiceError
    service = _Service()
    req = BuildService.request_in(model="owner/model", target="ARM64", format="gguf",
                                  tensor_types=["FFN_DOWN=Q6_K"])
    assert service.check(req)["tensor_types"] == ["ffn_down=q6_k"]
    with pytest.raises(BuildServiceError) as e:
        service.check(req, quant="Q8_0")
    assert e.value.field == "tensor_types"
    matrix = _dat(tmp_path / "m.dat", ["blk.0.attn_q.weight"])
    out = service.check(BuildService.request_in(model="owner/model", target="ARM64", format="gguf",
                                                imatrix_file=str(matrix)))
    assert out["use_imatrix"] is True and out["imatrix_file"] == str(matrix.resolve())
    with pytest.raises(BuildServiceError) as e:
        service.check(BuildService.request_in(model="o/m", target="ARM64", format="rkllm",
                                              imatrix_file=str(matrix)), fmt="rkllm")
    assert e.value.field == "imatrix_file"
    monkeypatch.setattr("orchestrator.utils.locality.offline_mode", lambda config=None: True)
    with pytest.raises(BuildServiceError, match="Offline mode"):
        service.check(BuildService.request_in(model="o/m", target="ARM64", format="gguf",
                                              imatrix_file="hf:owner/model-GGUF/imatrix.dat"))
    q8 = tmp_path / "in.gguf"
    q8.write_bytes(gguf_bytes(kv=(kv_u32("general.file_type", 7),)))
    left_out = service.check(BuildService.request_in(model=str(q8), target="ARM64", format="gguf"),
                             model=str(q8), given=False)
    assert left_out["quantization"] == "Q8_0" and left_out["use_imatrix"] is False   # a passthrough
    options = service.service.large_model_options(BuildService.request_in(
        model=str(q8), target="ARM64", format="gguf", quantization="Q4_K_M", tensor_types=["ffn_down=q6_k"]),
        tuned={"tensor_types": ["ffn_down=q6_k"]})
    assert options["gguf_input"]["requantize"] and options["tensor_types_measured"] is True


# --- the CLI, the GUI and the AI's tools ----------------------------------------------------------------------------------

def test_cli_build_f16_list_and_the_resource_decision(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from orchestrator import cli
    from orchestrator.Core import f16_cache
    cache = f16_cache.F16Cache(tmp_path / "cache")
    monkeypatch.setattr(cli, "_f16_cache", lambda ctx: (cache, {}))
    result = CliRunner(env={"PYTHONUTF8": "1"}).invoke(cli.cli, ["build", "f16", "list"])
    assert result.exit_code == 0 and "No F16 intermediate is kept" in result.output
    made = tmp_path / "f.gguf"
    made.write_bytes(b"GGUF" + b"\0" * 12)
    cache.keep(f16_cache.key("owner/model", COMMIT, "b1"), made, repo="owner/model", commit=COMMIT, pin="b1")
    result = CliRunner(env={"PYTHONUTF8": "1", "COLUMNS": "200"}).invoke(cli.cli, ["build", "f16", "list"])
    assert result.exit_code == 0 and "owner/model" in result.output and COMMIT[:12] in result.output

    risky = SimpleNamespace(risky=True, state="does_not_fit", lines=lambda: ["Disk: needs about 200 GB"])
    service = SimpleNamespace(resource_check=lambda request: risky)
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    with pytest.raises(SystemExit) as stop:
        cli._decide_resources(service, None, accept=False)
    assert stop.value.code == 2
    cli._decide_resources(service, None, accept=True)     # your yes: the build goes on


def test_the_f16_window_lists_and_deletes_through_the_list(qapp, tmp_path, monkeypatch):
    from orchestrator.Core import f16_cache
    from orchestrator.gui import f16_window
    cache = f16_cache.F16Cache(tmp_path / "cache")
    monkeypatch.setattr(f16_cache.F16Cache, "for_framework", classmethod(lambda cls, fw: cache))
    shown = []
    monkeypatch.setattr(f16_window, "plain_box", lambda parent, kind, title, text: shown.append((kind, text)))
    dialog = f16_window.F16IntermediatesDialog(SimpleNamespace(config={}))
    assert dialog.table.rowCount() == 0 and not dialog.btn_delete.isEnabled()
    made = tmp_path / "f.gguf"
    made.write_bytes(b"GGUF" + b"\0" * 12)
    cache.keep(f16_cache.key("owner/model", COMMIT, "b1"), made, repo="owner/model", commit=COMMIT, pin="b1")
    dialog.reload()
    assert dialog.table.rowCount() == 1 and dialog.table.item(0, 0).text() == "owner/model"
    monkeypatch.setattr("orchestrator.gui.deletion_dialog.ask_deletion", lambda *a, **k: None)
    assert dialog.delete_entries() is None and cache.entries()     # No: nothing deleted
    dialog.close()


def test_the_ai_reaches_the_options_and_reads_the_facts():
    from orchestrator.schemas.build import StartBuildIn
    from orchestrator.tools.tools_status import FrameworkSection
    from orchestrator.tools.tools_tuning import TuningIn
    fields = StartBuildIn.model_fields
    assert "imatrix_file" in fields and "tensor_types" in fields and "never a path" in fields["imatrix_file"].description
    assert "candidates" in TuningIn.model_fields
    assert {"f16_intermediates", "f16_intermediates_bytes"} <= set(FrameworkSection.model_fields)
    # deleting the kept files is the person's alone: the catalogue's step is a deletion
    from orchestrator.utils.next_steps import get
    assert get("build.f16_delete").person_only == "deletion" and get("build.f16_list").tool == "get_status"
