"""A block's reach section and the gateway's route table (orchestrator/runtime/bundle/reach.py): the rules Python and
the Rust gateway share (tests/data/gateway_routes_vectors.json, checked here against reach.py and by the Rust tests
against rust/llmcf-gateway/src/routes.rs), the reach sections of the framework's own blocks (today's routes, exactly)
and a block of a kind the framework does not know that is reached only through its reach section."""

import copy
import json
from pathlib import Path

import pytest

from orchestrator.runtime.bundle import blocks as blk
from orchestrator.runtime.bundle import reach as rc

VECTORS = json.loads((Path(__file__).parent / "data" / "gateway_routes_vectors.json").read_text(encoding="utf-8"))


def _patched(case):
    doc = copy.deepcopy(VECTORS["tables"]["base"])
    for patch in case["patches"]:
        at = doc
        for step in patch["path"][:-1]:
            at = at[step]
        last = patch["path"][-1]
        if "value" in patch:
            at[last] = patch["value"]
        else:
            del at[last]
    return doc


@pytest.mark.parametrize("case", VECTORS["tables"]["accept"], ids=lambda c: c["why"])
def test_the_shared_route_table_vectors_are_taken(case):
    table = rc.check_table(json.dumps(_patched(case)))
    assert table.routes and table.engines
    # what the generator writes reads back the same
    assert rc.check_table(table.dump()).as_json() == table.as_json()


@pytest.mark.parametrize("case", VECTORS["tables"]["reject"], ids=lambda c: c["why"])
def test_the_shared_route_table_vectors_are_refused(case):
    with pytest.raises(rc.ReachError):
        rc.check_table(json.dumps(_patched(case)))


def test_a_route_table_is_capped_and_strict_json():
    with pytest.raises(rc.ReachError, match="64 KiB"):
        rc.check_table(b" " * (rc.MAX_TABLE_BYTES + 1))
    with pytest.raises(rc.ReachError, match="no JSON"):
        rc.check_table(b"{routes")
    with pytest.raises(rc.ReachError):
        rc.check_table([])


def _routes(block_id):
    block = blk.load_catalog().get(block_id)
    return [(r.method, r.path, r.protocol, r.upstream_path, r.match, r.key) for r in block.reach.routes]


def test_the_frameworks_blocks_reach_todays_routes_exactly():
    assert _routes("llm-llama-server") == [
        ("GET", "/v1/models", "openai-models", "/v1/models", "", "models"),
        ("POST", "/v1/chat/completions", "openai-chat", "/v1/chat/completions", "", "chat"),
        ("POST", "/v1/completions", "openai-completions", "/v1/completions", "", "completions")]
    for whisper in ("stt-whisper-cpp", "stt-whisper-cpp-program"):
        assert _routes(whisper) == [("POST", "/v1/audio/transcriptions", "openai-audio-transcription", "/inference",
                                     "", "transcriptions")]
    assert _routes("vision-mmproj") == [("POST", "/v1/chat/completions", "openai-chat", "/v1/chat/completions",
                                         "images", "chat-images")]
    catalog = blk.load_catalog()
    for block_id in ("gateway-llmcf", "harness-agent", "stt-nemo-sherpa", "tts-sherpa-onnx"):
        assert catalog.get(block_id).reach is None, block_id
    assert catalog.get("llm-llama-server").label()["reach"][1] == {
        "method": "POST", "path": "/v1/chat/completions", "protocol": "openai-chat"}


DETECT = """schema: llmcf-bundle-block/1
id: detect-demo
version: 1.0.0
kind: stt
title: Detection (a hypothetical engine)
status: ready
variables:
  - {name: port, type: port, default: 8093}
files:
  - {role: program, from: person, name: detect-server, formats: [elf]}
  - {role: model, from: person, name: model.bin, licence: required}
unit: true
start:
  program: detect-server
  args: [--port, "{port}", --model, "@FILE:model@"]
reach:
  routes:
    - path: /api/detect
      protocol: http-multipart
      upstream_path: /detect
      max_body_kb: 8192
      timeout_s: 60
      fields:
        - {name: image, file: true, types: [jpeg, png], required: true}
        - {name: threshold}
    - {path: /api/detect/events, method: GET, protocol: http-sse, upstream_path: /events, timeout_s: 30}
"""


def test_a_block_of_any_kind_says_how_it_is_reached(tmp_path):
    block = blk.parse_block(DETECT, tmp_path)
    assert block.kind == "stt" and block.reach.port == "port"
    first, events = block.reach.routes
    assert (first.method, first.protocol, first.max_body_kb, first.timeout_s, first.stream) == \
        ("POST", "http-multipart", 8192, 60, False)
    assert [f.name for f in first.fields] == ["image", "threshold"] and first.fields[0].types == ("jpeg", "png")
    assert (events.method, events.stream, events.key, events.max_body_kb) == ("GET", True, "events", 16384)
    entry = first.entry("detect", 4096)
    assert entry["max_body_kb"] == 4096 and entry["engine"] == "detect" and entry["key"] == "detect"


@pytest.mark.parametrize("change, reason", [
    (("    - {path: /api/detect/events", "    - {path: /health"), "path"),
    (("    - {path: /api/detect/events", "    - {path: /api/detect"), "twice"),
    (("protocol: http-sse", "protocol: grpc"), "protocol"),
    (("protocol: http-sse, upstream_path: /events", "protocol: http-sse, upstream_path: /x/../y"), "upstream_path"),
    (("      max_body_kb: 8192", "      max_body_kb: 999999"), "max_body_kb"),
    (("        - {name: threshold}", "        - {name: threshold, types: [png]}"), "types"),
    (("reach:\n  routes:", "reach:\n  port: model\n  routes:"), "port"),
    (("reach:\n  routes:", "reach:\n  host: x\n  routes:"), "reach is"),
])
def test_a_reach_section_that_breaks_a_rule_is_refused(tmp_path, change, reason):
    text = DETECT.replace(*change)
    assert text != DETECT
    with pytest.raises(blk.BlockError, match=reason):
        blk.parse_block(text, tmp_path)


def test_reach_belongs_to_an_engine_block(tmp_path):
    gateway = (blk.BUILTIN_DIR / "gateway-llmcf" / "block.yml").read_text(encoding="utf-8")
    text = gateway + "reach:\n  routes:\n    - {path: /v1/x, protocol: http-json}\n"
    with pytest.raises(blk.BlockError, match="reach belongs"):
        blk.parse_block(text, tmp_path)
