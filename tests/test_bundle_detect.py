"""The vision engine end to end, without a board: a bundle planned with the block vision-rknn-detect, the gateway
started with the generated routes.json, and the engine's test build (rust/llmcf-detect, cargo feature sim-npu) with
the simulated NPU, a recorded model whose outputs are built by hand, and a folder of still images as its source.

Each image route is answered through the gateway with its golden result (in the source's pixels, the letterbox undone);
the event stream comes in order and counts the frames dropped while the NPU is slower than the source; the refusals
come from the gateway (a wrong type 415, a body too large 413) and from the engine (an image whose header announces
more pixels than the cap, 422); and the runtime's failures the simulated NPU plays (a driver too old, an output that
does not match the card) stop the engine with the reason.

The binaries: LLMCF_DETECT_TEST_BIN and LLMCF_GATEWAY_TEST_BIN when CI built them, else built here with cargo (the
tests skip with the reason when cargo is not there)."""

import hashlib
import http.client
import io
import json
import os
import shutil
import socket
import struct
import subprocess
import time
import uuid
import zlib
from pathlib import Path

import pytest

from orchestrator.runtime.bundle import blocks as blk
from orchestrator.runtime.bundle import generator as gen

REPO_ROOT = Path(__file__).resolve().parents[1]
RUST_DIR = REPO_ROOT / "rust"
LABELS = ["person", "car", "dog"]
SIZE = 32


def _build(crate, features, env_name):
    given = os.environ.get(env_name)
    if given:
        return Path(given)
    if os.name == "nt":
        pytest.skip("the board's programs are Linux programs; their end-to-end test runs on Linux")
    cargo = shutil.which("cargo")
    if cargo is None:
        pytest.skip(f"cargo is not on this computer: this test builds {crate}'s test binary with it")
    argv = [cargo, "build", "--locked", "-p", crate] + (["--features", features] if features else [])
    done = subprocess.run(argv, cwd=RUST_DIR, capture_output=True, text=True, timeout=1800)
    if done.returncode != 0:
        pytest.fail(f"cargo could not build {crate}:\n" + done.stderr[-2000:])
    return RUST_DIR / "target" / "debug" / crate


@pytest.fixture(scope="module")
def binaries():
    return _build("llmcf-detect", "sim-npu", "LLMCF_DETECT_TEST_BIN"), _build("llmcf-gateway", "", "LLMCF_GATEWAY_TEST_BIN")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _png(w, h, rgb):
    raw = b"".join(b"\0" + bytes(rgb) * w for _ in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _q(v, zp, scale):
    return struct.pack("b", max(-128, min(127, round(v / scale) + zp)))


def _outputs(boxes, scores):
    """The two recorded outputs (NCHW int8): boxes [1, 4, 2, 2] (distances in strides of 16, scale 0.1) and scores
    [1, 3, 2, 2] (scale 1/128, zero point -128). `boxes`: {(gy, gx): distance}, `scores`: {(class, gy, gx): score}."""
    b = bytearray(_q(0, 0, 0.1) * 16)
    s = bytearray(_q(0, -128, 1 / 128) * 12)
    for (gy, gx), d in boxes.items():
        for side in range(4):
            b[(side * 2 + gy) * 2 + gx] = _q(d, 0, 0.1)[0]
    for (c, gy, gx), v in scores.items():
        s[(c * 2 + gy) * 2 + gx] = _q(v, -128, 1 / 128)[0]
    return bytes(b), bytes(s)


def _card(model_sha):
    return {"schema": "llmcf-model-card/1", "model": {"name": "detect-test", "licence": "mit"},
            "rknn": {"file": "detect-test-rk3566-i8.rknn", "sha256": model_sha, "toolkit": "2.5.0",
                     "platform": "rk3566", "quantisation": "w8a8"},
            "input": {"width": SIZE, "height": SIZE, "layout": "nhwc", "colour": "rgb", "resize": "letterbox",
                      "pad": 114, "mean": [0, 0, 0], "std": [255, 255, 255], "normalised_in_model": True,
                      "type": "uint8"},
            "task": "detect", "postprocess": {"kind": "yolo-dfl", "strides": [16], "reg_max": 1},
            "labels": LABELS, "thresholds": {"score": 0.5, "nms_iou": 0.45, "max_results": 10},
            "score_activation": "model",
            "outputs": [{"index": 0, "role": "boxes", "shape": [1, 4, 2, 2], "dims": ["n", "c", "h", "w"]},
                        {"index": 1, "role": "auto"}]}


IMAGE_B = (200, 10, 10)


def _sim(folder, driver="0.9.8", delay_ms=0, scores_shape=(1, 3, 2, 2)):
    folder.mkdir(parents=True, exist_ok=True)
    spec = {"api": "2.5.0 (simulated)", "driver": driver,
            "inputs": [{"index": 0, "name": "images", "dims": [1, SIZE, SIZE, 3], "layout": "nhwc", "type": "uint8"}],
            "outputs": [{"index": 0, "name": "boxes", "dims": [1, 4, 2, 2], "layout": "nchw", "type": "int8",
                         "zp": 0, "scale": 0.1},
                        {"index": 1, "name": "scores", "dims": list(scores_shape), "layout": "nchw", "type": "int8",
                         "zp": -128, "scale": 1 / 128}]}
    if delay_ms:
        spec["delay_ms"] = delay_ms
    (folder / "model.json").write_text(json.dumps(spec), encoding="utf-8")
    (folder / "compatible").write_bytes(b"radxa,zero3\0rockchip,rk3566\0")
    default = folder / "default"
    default.mkdir(exist_ok=True)
    b, s = _outputs({(1, 1): 0.5}, {(2, 1, 1): 0.75})
    (default / "out0.bin").write_bytes(b)
    (default / "out1.bin").write_bytes(s)
    # image B (a 32 x 32 image goes in unchanged: its tensor is its pixels): two objects of other classes
    keyed = folder / hashlib.sha256(bytes(IMAGE_B) * SIZE * SIZE).hexdigest()
    keyed.mkdir(exist_ok=True)
    b, s = _outputs({(0, 0): 0.5, (1, 0): 0.5}, {(0, 0, 0): 0.9, (1, 1, 0): 0.6})
    (keyed / "out0.bin").write_bytes(b)
    (keyed / "out1.bin").write_bytes(s)


def _plan(tmp, ports, model, card, file_source=False):
    from tests.test_board_bundle import DEVICE, RK3566, _elf, _files, _runtime_tarball
    tmp.mkdir(parents=True, exist_ok=True)
    gguf, _w, _p = _files(tmp)
    prog = tmp / "prog"
    prog.write_bytes(_elf(0xB7))
    detect = {"port": str(ports["detect"])}
    if file_source:
        detect.update({"file_source": "yes", "source_fps": "20"})
    sel = gen.Selection(blocks=["llm-llama-server", "vision-rknn-detect", "gateway-llmcf"],
                        values={"llm": {"port": str(ports["llm"])}, "detect": detect,
                                "gateway": {"port": str(ports["gateway"])}},
                        files={"llm": {"model": gen.FileChoice(path=str(gguf), licence="apache-2.0")},
                               "detect": {"model": gen.FileChoice(path=str(model), licence="mit"),
                                          "data": gen.FileChoice(path=str(card))}})
    plan = gen.plan_bundle(sel, catalog=blk.load_catalog(), profile=RK3566, device_id=DEVICE, offline=False,
                           bundle_id="detect-e2e", runtime=_runtime_tarball(tmp), programs=lambda n, a: (prog, "1" * 64),
                           unmeasured_ok=True)
    assert plan.ok, plan.problems
    return plan


def _args(plan, comp, folder):
    c = next(c for c in plan.manifest.components if c.id == comp)
    return [a.replace("@DIR@", str(folder)) for a in c.start.args]


def _wait_health(port, proc, timeout=30):
    until = time.time() + timeout
    while time.time() < until:
        if proc.poll() is not None:
            return None
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            conn.request("GET", "/health")
            r = conn.getresponse()
            body = r.read()
            if r.status == 200:
                return json.loads(body)
        except OSError:
            pass
        time.sleep(0.1)
    return None


class Board:
    """The engine and the gateway as the bundle starts them, on this computer."""

    def __init__(self, tmp, binaries, file_source=False, sim=None, scores_shape=(1, 3, 2, 2), driver="0.9.8"):
        self.tmp = tmp
        detect_bin, gateway_bin = binaries
        self.ports = {"llm": _free_port(), "detect": _free_port(), "gateway": _free_port()}
        comp = tmp / "detect"
        comp.mkdir()
        model = comp / "model.rknn"
        model.write_bytes(b"RKNN" + bytes(range(256)) * 8)
        card = comp / "model.card.json"
        card.write_text(json.dumps(_card(hashlib.sha256(model.read_bytes()).hexdigest())), encoding="utf-8")
        media = comp / "media"
        media.mkdir()
        for i, rgb in enumerate([(10, 200, 10), IMAGE_B]):
            (media / f"frame{i}.png").write_bytes(_png(SIZE, SIZE, rgb))
        self.sim = tmp / "sim"
        _sim(self.sim, driver=driver, delay_ms=150 if file_source else 0, scores_shape=scores_shape)
        self.plan = _plan(tmp / "plan", self.ports, model, card, file_source)
        args = _args(self.plan, "detect", comp)
        i = args.index("--rknn-runtime")
        args[i:i + 2] = ["--sim-npu", str(self.sim)]
        self.detect = subprocess.Popen([str(detect_bin)] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.gateway = None
        self.health = _wait_health(self.ports["detect"], self.detect)
        if self.health is None:
            return
        gw = tmp / "gateway"
        gw.mkdir()
        (gw / "routes.json").write_bytes(self.plan.routes.dump())
        env = dict(os.environ, HOME=str(tmp))
        self.gateway = subprocess.Popen([str(gateway_bin)] + _args(self.plan, "gateway", gw), env=env,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        until = time.time() + 30
        while time.time() < until:
            try:
                socket.create_connection(("127.0.0.1", self.ports["gateway"]), timeout=1).close()
                break
            except OSError:
                time.sleep(0.1)

    def stop(self):
        for p in (self.gateway, self.detect):
            if p is not None and p.poll() is None:
                p.terminate()
                try:
                    p.wait(10)
                except subprocess.TimeoutExpired:
                    p.kill()

    def engine_log(self):
        self.stop()
        return self.detect.stderr.read().decode("utf-8", "replace")

    def post(self, path, body, ctype, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.ports["gateway"], timeout=30)
        conn.request("POST", path, body=body, headers={"Content-Type": ctype, **(headers or {})})
        r = conn.getresponse()
        return r.status, r.read()


def _form(fields):
    boundary = uuid.uuid4().hex
    out = b""
    for name, filename, data in fields:
        disp = f'form-data; name="{name}"' + (f'; filename="{filename}"' if filename else "")
        out += f"--{boundary}\r\nContent-Disposition: {disp}\r\n\r\n".encode() + data + b"\r\n"
    return out + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


@pytest.fixture
def board(tmp_path, binaries):
    b = Board(tmp_path, binaries)
    yield b
    b.stop()


def _dets(body):
    data = json.loads(body)
    assert data["schema"] == "llmcf-vision-result/1" and data["model"] == "detect-test"
    return [(d["label"], d["class_id"], d["score"], d["box"]) for d in data["detections"]], data


def test_every_image_route_answers_its_golden_result_through_the_gateway(board):
    assert board.health is not None, board.engine_log()
    assert board.health["kind"] == "yolo-dfl" and board.health["driver"] == "0.9.8" and not board.health["warnings"]
    # the form route with image B: two objects in the image's pixels (32 x 32 goes in unchanged)
    body, ctype = _form([("image", "b.png", _png(SIZE, SIZE, IMAGE_B))])
    status, answer = board.post("/api/detect", body, ctype)
    assert status == 200, answer
    dets, data = _dets(answer)
    assert dets == [("person", 0, 0.8984, [0.0, 0.0, 16.0, 16.0]), ("car", 1, 0.6016, [0.0, 16.0, 16.0, 32.0])]
    assert data["frame"]["source"] == "upload" and (data["frame"]["width"], data["frame"]["height"]) == (32, 32)
    # options within the card's bounds: only the cars
    body, ctype = _form([("image", "b.png", _png(SIZE, SIZE, IMAGE_B)), ("options", None, b'{"labels":["car"]}')])
    status, answer = board.post("/api/detect", body, ctype)
    assert status == 200 and [d[0] for d in _dets(answer)[0]] == ["car"]
    # the binary route with a 64 x 32 image: letterboxed with 8 rows above; the dog at (16, 16, 32, 32) of the input
    # is (32, 16, 64, 48) in the image, clamped into its 32 rows
    status, answer = board.post("/api/detect/image", _png(64, 32, (1, 2, 3)), "image/png")
    assert status == 200, answer
    assert _dets(answer)[0] == [("dog", 2, 0.75, [32.0, 16.0, 64.0, 32.0])]
    # a JPEG goes the same way
    pil = pytest.importorskip("PIL.Image")
    buf = io.BytesIO()
    pil.new("RGB", (SIZE, SIZE), (90, 90, 90)).save(buf, "JPEG")
    status, answer = board.post("/api/detect/image", buf.getvalue(), "image/jpeg")
    assert status == 200 and _dets(answer)[0] == [("dog", 2, 0.75, [16.0, 16.0, 32.0, 32.0])]


def test_the_refusals_come_from_the_gateway_and_the_engine(board):
    assert board.health is not None, board.engine_log()
    status, _ = board.post("/api/detect/image", b"GIF89a" + b"\0" * 64, "image/gif")
    assert status == 415, "the gateway checks the magic type"
    # a Content-Length above the route's cap is refused before the body is read
    with socket.create_connection(("127.0.0.1", board.ports["gateway"]), timeout=30) as s:
        s.sendall(b"POST /api/detect/image HTTP/1.1\r\nHost: board\r\nContent-Type: image/png\r\n"
                  + f"Content-Length: {8192 * 1024 + 1}\r\n\r\n".encode() + b"\x89PNG\r\n\x1a\n")
        head = s.recv(64)
    assert head.startswith(b"HTTP/1.1 413"), head
    big = bytearray(_png(1, 1, (1, 2, 3)))
    big[16:24] = struct.pack(">II", 10000, 10000)
    status, answer = board.post("/api/detect/image", bytes(big), "image/png")
    assert status == 422 and json.loads(answer)["error"]["code"] == "image_too_large"
    body, ctype = _form([("image", "a.png", _png(SIZE, SIZE, IMAGE_B)), ("options", None, b'{"score":2}')])
    status, answer = board.post("/api/detect", body, ctype)
    assert status == 400 and json.loads(answer)["error"]["code"] == "invalid_options"
    body, ctype = _form([("photo", "a.png", _png(SIZE, SIZE, IMAGE_B))])
    status, _ = board.post("/api/detect", body, ctype)
    assert status == 400, "an undeclared field"
    log = board.engine_log()
    assert "invalid_options" not in log and "score" not in log, "the log holds numbers and states only"


def test_the_event_stream_comes_in_order_and_counts_the_dropped_frames(tmp_path, binaries):
    b = Board(tmp_path, binaries, file_source=True)
    try:
        assert b.health is not None, b.engine_log()
        assert b.health["source"] == "files"
        conn = http.client.HTTPConnection("127.0.0.1", b.ports["gateway"], timeout=30)
        conn.request("GET", "/api/detect/events")
        r = conn.getresponse()
        assert r.status == 200 and r.getheader("Content-Type").startswith("text/event-stream")
        events = []
        while len(events) < 6:
            line = r.fp.readline()
            assert line, "the stream ended"
            if line.startswith(b"data: "):
                events.append(json.loads(line[6:]))
                time.sleep(0.3)   # a slow reader: the engine does not keep results for it
        seqs = [e["frame"]["seq"] for e in events]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), seqs
        assert all(e["frame"]["source"] == "files" for e in events)
        assert any(e["frame"]["dropped"] > 0 for e in events), "the NPU (150 ms) is slower than the source (20/s)"
        assert {e["detections"][0]["label"] for e in events} <= {"dog", "person"}
        conn.close()
    finally:
        b.stop()


@pytest.mark.parametrize("change, reason", [
    ({"driver": "0.8.8"}, "older than 0.9.2"),
    ({"scores_shape": (1, 2, 2, 2)}, "cannot be told by its shape"),
])
def test_the_runtimes_failures_stop_the_engine_with_the_reason(tmp_path, binaries, change, reason):
    b = Board(tmp_path, binaries, **change)
    try:
        if b.health is None:
            log = b.engine_log()
            assert reason in log, log
        else:
            # an output the card cannot read is refused at the first image
            status, answer = b.post("/api/detect/image", _png(SIZE, SIZE, IMAGE_B), "image/png")
            assert status == 500 and reason in json.loads(answer)["error"]["message"]
    finally:
        b.stop()


def test_the_release_program_refuses_the_simulated_npu():
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "LLMCF_DETECT_TEST_BIN=" in ci and "tests/test_bundle_detect.py" in ci
    assert "no simulated NPU" in ci and "--features sim-npu" in ci


# --- the plan and the build of the engine (no binaries needed) ----------------------------------------------------

def _glibc_elf(needed=("libc.so.6", "libm.so.6"), versions=("2.17", "2.31"), machine=0xB7,
               interp=b"/lib/ld-linux-aarch64.so.1"):
    """A minimal 64-bit ELF program: an interpreter, one load segment over the whole file and a dynamic section."""
    strtab = b"\0"
    offsets = []
    for name in needed:
        offsets.append(len(strtab))
        strtab += name.encode() + b"\0"
    for v in versions:
        strtab += f"GLIBC_{v}".encode() + b"\0"
    ph_off, n_ph = 64, 3
    interp_off = ph_off + 56 * n_ph
    str_off = interp_off + len(interp) + 1
    dyn_off = (str_off + len(strtab) + 7) // 8 * 8
    dyn = b"".join(struct.pack("<qQ", 1, o) for o in offsets)
    dyn += struct.pack("<qQ", 5, str_off) + struct.pack("<qQ", 10, len(strtab)) + struct.pack("<qQ", 0, 0)
    total = dyn_off + len(dyn)
    head = b"\x7fELF\x02\x01\x01" + b"\0" * 9 + struct.pack("<HHIQQQIHHHHHH", 3, machine, 1, 0, ph_off, 0, 0, 64, 56,
                                                                n_ph, 0, 0, 0)

    def ph(kind, off, size):
        return struct.pack("<IIQQQQQQ", kind, 4, off, off, off, size, size, 8)
    body = head + ph(3, interp_off, len(interp) + 1) + ph(1, 0, total) + ph(2, dyn_off, len(dyn))
    body += interp + b"\0" + strtab
    return body + b"\0" * (dyn_off - len(body)) + dyn


def test_the_vision_engine_is_the_one_glibc_program_and_its_elf_is_checked():
    from orchestrator.runtime.bundle import root_helper as rh
    assert "llmcf-detect" in rh.FRAMEWORK_PROGRAMS and "llmcf-detect" in rh.GNU_PROGRAMS
    assert rh.target_of("aarch64", "llmcf-detect") == "aarch64-unknown-linux-gnu"
    assert rh.target_of("aarch64", "llmcf-gateway") == "aarch64-unknown-linux-musl", "the gateway stays musl"
    with pytest.raises(rh.HelperMissing, match="aarch64 boards with glibc"):
        rh.target_of("armv7l", "llmcf-detect")
    assert rh.zigbuild_target("llmcf-detect", "aarch64-unknown-linux-gnu") == "aarch64-unknown-linux-gnu.2.31"
    target = "aarch64-unknown-linux-gnu"
    assert rh.is_program_for(_glibc_elf(), target, "llmcf-detect")
    assert not rh.is_program_for(_glibc_elf(needed=("libc.so.6", "libstdc++.so.6")), target, "llmcf-detect")
    assert not rh.is_program_for(_glibc_elf(versions=("2.17", "2.34")), target, "llmcf-detect")
    assert not rh.is_program_for(_glibc_elf(machine=0x3E), target, "llmcf-detect")
    assert not rh.is_program_for(_glibc_elf(interp=b"/lib/ld-musl-aarch64.so.1"), target, "llmcf-detect")
    assert not rh.is_static_elf_for(_glibc_elf(), "aarch64-unknown-linux-musl"), "a musl program stays static"
    toolchain = (RUST_DIR / "rust-toolchain.toml").read_text(encoding="utf-8")
    assert '"aarch64-unknown-linux-gnu"' in toolchain
    docker = rh.BUILDER_DOCKERFILE.read_text(encoding="utf-8")
    assert f"ZIG_VERSION={rh.ZIG_VERSION}" in docker and f"CARGO_ZIGBUILD_VERSION={rh.CARGO_ZIGBUILD_VERSION}" in docker
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert f'ZIG_VERSION: "{rh.ZIG_VERSION}"' in ci and f"--version ={rh.CARGO_ZIGBUILD_VERSION}" in ci
    assert "aarch64-unknown-linux-gnu.2.31" in ci and "ubuntu-24.04-arm" in ci


def test_the_framework_block_reaches_the_engine_through_its_routes():
    block = blk.load_catalog().get("vision-rknn-detect")
    assert block.trust == "framework" and block.kind == "detect" and block.ready
    assert [(r.method, r.path, r.protocol, r.upstream_path) for r in block.reach.routes] == [
        ("POST", "/api/detect", "http-multipart", "/detect"), ("POST", "/api/detect/image", "http-binary",
                                                               "/detect/image"),
        ("GET", "/api/detect/events", "http-sse", "/events")]
    assert (block.memory_base_mb, block.memory_model_factor) == (96, 2.0)
    assert {f.role: f.formats for f in block.files} == {"program": ("elf",), "model": ("rknn",),
                                                         "data": ("model-card",)}
    assert block.variable("user").default == "llmcf-vision"


def test_the_plan_checks_that_card_and_model_are_a_pair(tmp_path):
    ports = {"llm": 8080, "detect": 8093, "gateway": 8088}
    model = tmp_path / "m.rknn"
    model.write_bytes(b"RKNN" + b"\0" * 2048)
    card = tmp_path / "m.card.json"
    card.write_text(json.dumps(_card(hashlib.sha256(model.read_bytes()).hexdigest())), encoding="utf-8")
    plan = _plan(tmp_path / "a", ports, model, card)
    check = next(c for c in plan.checks if c["check"] == "model card detect")
    assert check["result"] == "passed" and "yolo-dfl" in check["detail"]
    assert any("model card detect: passed" in line for line in plan.lines())
    assert "--rknn-runtime" in _args(plan, "detect", tmp_path) and "--sim-npu" not in _args(plan, "detect", tmp_path)
    assert "detect=llmcf-detect.service" not in " ".join(_args(plan, "gateway", tmp_path)), "together: no units"
    other = tmp_path / "other.card.json"
    other.write_text(json.dumps(_card("0" * 64)), encoding="utf-8")
    from tests.test_board_bundle import DEVICE, RK3566, _files, _runtime_tarball
    (tmp_path / "b").mkdir()
    gguf, _w, _p = _files(tmp_path / "b")
    sel = gen.Selection(blocks=["llm-llama-server", "vision-rknn-detect", "gateway-llmcf"],
                        files={"llm": {"model": gen.FileChoice(path=str(gguf), licence="mit")},
                               "detect": {"model": gen.FileChoice(path=str(model), licence="mit"),
                                          "data": gen.FileChoice(path=str(other))}})
    bad = gen.plan_bundle(sel, catalog=blk.load_catalog(), profile=RK3566, device_id=DEVICE, offline=False,
                          bundle_id="detect-pair", runtime=_runtime_tarball(tmp_path / "b"),
                          programs=lambda n, a: (model, "1" * 64), unmeasured_ok=True)
    assert any("the card names another model" in p for p in bad.problems), bad.problems
    other.write_text("{}", encoding="utf-8")
    sel.files["detect"]["data"] = gen.FileChoice(path=str(other))
    bad = gen.plan_bundle(sel, catalog=blk.load_catalog(), profile=RK3566, device_id=DEVICE, offline=False,
                          bundle_id="detect-pair", runtime=_runtime_tarball(tmp_path / "b"),
                          programs=lambda n, a: (model, "1" * 64), unmeasured_ok=True)
    assert any("no model-card file" in p for p in bad.problems), bad.problems
