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
kind: detect
title: Detection (a hypothetical engine)
status: ready
variables:
  - {name: port, type: port, default: 8093}
  - {name: memory_limit_mb, type: memory_mb, default: 512}
  - {name: user, type: user, default: llmcf}
  - {name: autostart, type: bool, default: false}
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
    assert block.kind == "detect" and block.reach.port == "port"
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


# --- open kinds: an engine of a kind of its own is treated like the others -------------------------------------------

def _own_detect(app: Path) -> None:
    folder = app / "community" / "blocks" / "detect-demo"
    folder.mkdir(parents=True)
    (folder / "block.yml").write_text(DETECT, encoding="utf-8")
    (folder / "README.md").write_text("# Detection\n\n## Models\n\nA detection model.\n", encoding="utf-8")


def _detect_plan(tmp_path, engines="together"):
    from orchestrator.runtime.bundle import generator as gen
    from tests.test_board_bundle import DEVICE, RK3566, _elf, _files, _runtime_tarball
    from tests.test_bundle_gateway import _programs
    app = tmp_path / "app"
    _own_detect(app)
    model, _w, _p = _files(tmp_path)
    prog = tmp_path / "detect-server"
    prog.write_bytes(_elf(0xB7))
    weights = tmp_path / "detect.bin"
    weights.write_bytes(b"\0" * 4096)
    yes = {"autostart": "yes"}
    sel = gen.Selection(blocks=["llm-llama-server", "detect-demo", "gateway-llmcf"], engines=engines,
                        values={"llm": dict(yes), "detect": dict(yes), "gateway": dict(yes)},
                        files={"llm": {"model": gen.FileChoice(path=str(model), licence="apache-2.0")},
                               "detect": {"program": gen.FileChoice(path=str(prog)),
                                          "model": gen.FileChoice(path=str(weights), licence="apache-2.0")}})
    return gen.plan_bundle(sel, catalog=blk.load_catalog(app), profile=RK3566, device_id=DEVICE, offline=False,
                           bundle_id="kind-test", runtime=_runtime_tarball(tmp_path), programs=_programs(tmp_path),
                           allow_own=True, unmeasured_ok=True)


def test_an_engine_of_a_kind_of_its_own_is_started_and_stopped_like_the_others(tmp_path):
    from orchestrator.runtime.bundle.manifest import dump_manifest, is_engine, load_manifest, valid_kind
    assert valid_kind("detect") and is_engine("detect") and not is_engine("gateway") and not is_engine("harness")
    assert not valid_kind("Detect") and not valid_kind("d") and not valid_kind("de_tect")
    plan = _detect_plan(tmp_path, engines="in-turn")
    assert plan.ok, plan.problems
    comps = {c.id: c for c in plan.manifest.components}
    assert comps["detect"].kind == "detect"
    assert set(plan.engines["engines_mb"]) == {"llm", "detect"}
    control = next(a for a in plan.manifest.root_actions if a.action == "allow_unit_control")
    assert control.units == ("llmcf-llm.service", "llmcf-detect.service")
    units = {a.unit: a for a in plan.manifest.root_actions if a.action == "install_unit"}
    assert units["llmcf-detect.service"].on_demand and units["llmcf-llm.service"].on_demand
    cgroup = next(a for a in plan.manifest.root_actions if a.action == "set_cgroup" and a.component == "detect")
    assert cgroup.oom_score_adj == 500, "the out-of-memory order of every engine"
    args = list(comps["gateway"].start.args)
    mb = plan.engines["engines_mb"]["detect"]
    assert f"detect=llmcf-detect.service:{mb}:8093" in args and args[args.index("--engines") + 1] == "in-turn"
    assert load_manifest(dump_manifest(plan.manifest)) == plan.manifest


# --- the generated route table ------------------------------------------------------------------------------------------

def test_the_generated_route_table_reproduces_todays_routes(tmp_path):
    """llm, speech and the gateway: the table names exactly the routes the gateway always served, each to its engine
    with today's caps (the JSON cap 256 KB, the audio cap 25 MB), and goes to the gateway as one signed file."""
    from tests.test_bundle_gateway import _voice
    plan = _voice(tmp_path)
    assert plan.ok, plan.problems
    table = plan.routes.as_json()
    assert table["engines"] == [{"id": "llm", "port": 8080, "key": True, "ctx_size": 4096},
                                {"id": "stt", "port": 8090, "key": False}]
    assert [(r["method"], r["path"], r["engine"], r["upstream_path"], r["protocol"], r["max_body_kb"], r["stream"])
            for r in table["routes"]] == [
        ("GET", "/v1/models", "llm", "/v1/models", "openai-models", 256, False),
        ("POST", "/v1/chat/completions", "llm", "/v1/chat/completions", "openai-chat", 256, True),
        ("POST", "/v1/completions", "llm", "/v1/completions", "openai-completions", 256, True),
        ("POST", "/v1/audio/transcriptions", "stt", "/inference", "openai-audio-transcription", 25600, False)]
    assert all(r["timeout_s"] == 900 for r in table["routes"])
    planned = plan.files[("gateway", "routes.json")]
    gw = next(c for c in plan.manifest.components if c.kind == "gateway")
    entry = next(f for f in gw.files if f.name == "routes.json")
    assert (entry.role, entry.source, entry.mode, entry.size) == ("config", "bundle", "0644", len(planned.data))
    assert rc.check_table(planned.data).as_json() == table, "the bytes the gateway reads are the table"
    # the person sees the table; an AI reads it as labels
    lines = plan.lines()
    assert "  the gateway's routes (from the blocks' reach):" in lines
    assert "    POST /v1/audio/transcriptions -> stt /inference (openai-audio-transcription, up to 25600 KB, 900 s)" \
        in lines
    assert plan.labels()["routes"][1] == {"method": "POST", "path": "/v1/chat/completions", "protocol": "openai-chat",
                                         "engine": "llm", "max_body_kb": 256, "timeout_s": 900, "stream": True}


def test_the_route_table_is_built_into_the_signed_bundle(tmp_path):
    from orchestrator.runtime.bundle import generator as gen
    from orchestrator.utils.helpers import sha256_file
    from tests.test_board_bundle import _signer
    from tests.test_board_bundle import _elf
    from tests.test_bundle_gateway import _plan
    prog = tmp_path / "llmcf-gateway-built"
    prog.write_bytes(_elf(0xB7))
    plan = _plan(tmp_path, programs=lambda name, arch: (prog, sha256_file(prog)))
    assert plan.ok, plan.problems
    store = gen.BundleStore(tmp_path / "state")
    built = gen.build_bundle(plan, store, _signer())
    path = built.path / "files" / "gateway--routes.json"
    entry = next(f for c in built.manifest.components for f in c.files if f.name == "routes.json")
    assert path.read_bytes() == plan.routes.dump() and sha256_file(path) == entry.sha256
    assert store.check(built) == [], "the payload check covers the route table like every file"
    path.write_bytes(path.read_bytes().replace(b"/inference", b"/elsewhere") + b" ")
    assert any("gateway--routes.json" in p for p in store.check(built))


def test_a_new_engine_is_reached_through_its_block_alone(tmp_path):
    """A detection engine of a kind of its own: its block's reach adds its routes to the table (multipart in, an event
    stream out), with no change of the gateway; the plan shows them."""
    plan = _detect_plan(tmp_path)
    assert plan.ok, plan.problems
    table = plan.routes.as_json()
    assert {"id": "detect", "port": 8093, "key": False} in table["engines"]
    detect = [r for r in table["routes"] if r["engine"] == "detect"]
    assert [(r["method"], r["path"], r["protocol"], r["max_body_kb"]) for r in detect] == [
        ("POST", "/api/detect", "http-multipart", 8192), ("GET", "/api/detect/events", "http-sse", 256)]
    assert detect[0]["fields"][0] == {"name": "image", "file": True, "required": True, "types": ["jpeg", "png"]}
    assert any("GET /api/detect/events -> detect /events (http-sse" in line for line in plan.lines())
    # the gateway's unit makes room for the uploads of the new route as for the speech upload
    cg = next(a for a in plan.manifest.root_actions if a.action == "set_cgroup" and a.component == "gateway")
    assert cg.memory_max_mb >= 64 + 2 * 8


def test_two_blocks_that_want_the_same_route_refuse_the_plan(tmp_path):
    from orchestrator.runtime.bundle import generator as gen
    app = tmp_path / "app"
    _own_detect(app)
    clash = (app / "community" / "blocks" / "detect-demo" / "block.yml")
    clash.write_text(clash.read_text(encoding="utf-8").replace("path: /api/detect\n", "path: /v1/completions\n"),
                     encoding="utf-8")
    catalog = blk.load_catalog(app)
    plan = gen.BundlePlan(bundle_id="x", device_id="0" * 16, selection=gen.Selection(blocks=[]))
    pairs = [("llm", catalog.get("llm-llama-server")), ("detect", catalog.get("detect-demo")),
             ("gateway", catalog.get("gateway-llmcf"))]
    assert gen._route_table(plan, "gateway", {"max_body_kb": 256, "max_audio_mb": 25, "ctx_size": 4096}, pairs,
                            gen.Selection(blocks=[])) is None
    assert any("POST /v1/completions is declared by llm and detect" in p for p in plan.problems)


def test_a_block_without_reach_keeps_the_routes_of_its_kind(tmp_path):
    """llmcf-bundle-block/1 without reach: an llm block keeps the language model's routes, a vision block the chats
    with images; another engine is reached on its own port only (a note says so)."""
    from orchestrator.runtime.bundle import generator as gen
    text = (blk.BUILTIN_DIR / "llm-llama-server" / "block.yml").read_text(encoding="utf-8")
    plain = text[:text.index("reach:")] + text[text.index("start:"):]
    old = blk.parse_block(plain.replace("id: llm-llama-server", "id: llm-old"), tmp_path)
    assert old.reach is None
    assert [r.path for r in gen.block_reach(old).routes] == ["/v1/models", "/v1/chat/completions", "/v1/completions"]
    other = blk.parse_block(DETECT[:DETECT.index("reach:")], tmp_path)
    assert gen.block_reach(other) is None
    plan = gen.BundlePlan(bundle_id="x", device_id="0" * 16, selection=gen.Selection(blocks=[]))
    gen._route_table(plan, "gateway", {"max_body_kb": 256, "ctx_size": 4096}, [("llm", old), ("detect", other)],
                     gen.Selection(blocks=[]))
    assert any("no route to detect" in n and "own port only" in n for n in plan.notes)
