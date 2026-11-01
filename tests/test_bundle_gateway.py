"""The gateway as a bundle component: the block gateway-llmcf (its program built by the framework), the plan that
wires it to the llm component, the gate operation bundle-gateway, the board check with the TLS pin, and the labels
the CLI and the Board bundle window show. The Rust gateway itself is tested in rust/llmcf-gateway (cargo test)."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.runtime.bundle import blocks as blk
from orchestrator.runtime.bundle import board, gate_ops, root_helper, service
from orchestrator.runtime.bundle import generator as gen
from tests.test_board_bundle import DEVICE, RK3566, _elf, _files, _runtime_tarball
from tests.test_llmcf_install import _static_elf

FP = "ab" * 32


def _programs(tmp: Path):
    prog = tmp / "llmcf-gateway-built"
    prog.write_bytes(_elf(0xB7))

    def resolve(name, arch):
        assert name == "llmcf-gateway" and arch
        return prog, "1" * 64
    return resolve


def _plan(tmp: Path, values=None, blocks=("llm-llama-server", "gateway-llmcf"), programs=None, limits=None):
    model, _w, _p = _files(tmp)
    sel = gen.Selection(blocks=list(blocks), values=values or {},
                        files={"llm": {"model": gen.FileChoice(path=str(model), licence="apache-2.0")}})
    # an unmeasured board is planned after the person's yes here; the measured limits have tests of their own
    return gen.plan_bundle(sel, catalog=blk.load_catalog(), profile=RK3566, device_id=DEVICE, offline=False,
                           bundle_id="gw-test", runtime=_runtime_tarball(tmp),
                           programs=programs if programs is not None else _programs(tmp),
                           limits=limits, unmeasured_ok=limits is None)


def test_the_gateway_block_is_ready_and_its_program_comes_from_the_framework():
    block = blk.load_catalog().get("gateway-llmcf")
    assert block.ready and block.kind == "gateway" and block.unit
    assert [(f.role, f.source, f.name) for f in block.files] == [("program", "framework", "llmcf-gateway")]
    assert block.variable("bind").choices == ("loopback", "lan") and block.variable("bind").default == "loopback"
    assert "llmcf-gateway" in root_helper.FRAMEWORK_PROGRAMS


def test_from_framework_takes_only_the_frameworks_own_programs(tmp_path):
    text = (blk.BUILTIN_DIR / "gateway-llmcf" / "block.yml").read_text().replace("name: llmcf-gateway",
                                                                                 "name: other-tool")
    with pytest.raises(blk.BlockError, match="from framework"):
        blk.parse_block(text, tmp_path)


def test_the_plan_wires_the_gateway_to_the_llm_component(tmp_path):
    plan = _plan(tmp_path, values={"llm": {"port": "8090", "ctx_size": "8192"}, "gateway": {"bind": "lan"}})
    assert plan.ok, plan.problems
    comp = next(c for c in plan.manifest.components if c.kind == "gateway")
    assert comp.variables["upstream_port"] == 8090 and comp.variables["ctx_size"] == 8192
    assert comp.files[0].role == "program" and comp.files[0].sha256 == "1" * 64
    args = list(comp.start.args)
    assert args[:3] == ["serve", "--port", "8088"] and "--upstream-port" in args and "lan" in args
    assert any("only with TLS" in n for n in plan.notes)


def test_a_gateway_without_the_llm_component_or_with_another_upstream_is_refused(tmp_path):
    alone = _plan(tmp_path, blocks=("gateway-llmcf",))
    assert any("needs the bundle's llm component" in p for p in alone.problems)
    other = _plan(tmp_path, values={"gateway": {"upstream_port": "9999"}})
    assert any("set by the bundle" in p for p in other.problems)


def test_a_program_the_framework_cannot_build_is_a_plain_problem(tmp_path):
    def missing(name, arch):
        raise root_helper.HelperMissing("llmcf-gateway for the board's architecture is not kept on this computer")
    plan = _plan(tmp_path, programs=missing)
    assert not plan.ok and any("not kept on this computer" in p for p in plan.problems)


def test_the_root_helper_way_builds_and_keeps_the_gateway_per_target(tmp_path):
    rust = tmp_path / "rust"
    (rust / "llmcf-gateway" / "src").mkdir(parents=True)
    (rust / "Cargo.toml").write_text("[workspace]\n")
    (rust / "llmcf-gateway" / "Cargo.toml").write_text('[package]\nname = "llmcf-gateway"\nversion = "0.1.0"\n')
    calls = []

    def build(work, target):
        calls.append(target)
        out = Path(work) / "llmcf-gateway"
        out.write_bytes(_static_elf(0xB7))
        return out
    with pytest.raises(root_helper.HelperMissing, match="offline mode is on"):
        root_helper.ensure_helper(tmp_path / "c", "aarch64", offline=True, build=build, rust_dir=rust,
                                  crate="llmcf-gateway")
    made = root_helper.ensure_helper(tmp_path / "c", "aarch64", offline=False, build=build, rust_dir=rust,
                                     crate="llmcf-gateway")
    assert made.path.name == "llmcf-gateway" and (made.path.parent / "llmcf-gateway.json").is_file()
    assert made.path.parent.parent.name == "llmcf-gateway"
    again = root_helper.ensure_helper(tmp_path / "c", "arm64", offline=True, build=build, rust_dir=rust,
                                      crate="llmcf-gateway")
    assert again.sha256 == made.sha256 and len(calls) == 1
    with pytest.raises(root_helper.HelperMissing, match="no program"):
        root_helper.ensure_helper(tmp_path / "c", "aarch64", offline=False, build=build, rust_dir=rust,
                                  crate="anything")
    assert root_helper.cc_env("llmcf-install", "aarch64-unknown-linux-musl") == {}


def test_the_persons_plan_offers_the_frameworks_container_engine_for_the_build(tmp_path, monkeypatch):
    """Without cargo here, the gateway is built in the builder image over the framework's container engine, as
    llmcf-install is (helper_for): the person's plan hands that client to the builder."""
    seen = []

    def builder(client=None, **kw):
        seen.append(client)
        return None
    monkeypatch.setattr(root_helper, "default_builder", builder)
    monkeypatch.setattr(root_helper, "cached", lambda *a, **k: None)
    engine = object()
    framework = SimpleNamespace(config=None, docker_manager=SimpleNamespace(builder=SimpleNamespace(
        docker_client=engine)))
    model, _w, _p = _files(tmp_path)
    sel = gen.Selection(blocks=["llm-llama-server", "gateway-llmcf"], values={},
                        files={"llm": {"model": gen.FileChoice(path=str(model), licence="apache-2.0")}})
    plan = service.plan_for_device(tmp_path, DEVICE, sel, profile=RK3566, runtime=_runtime_tarball(tmp_path),
                                   framework=framework)
    assert seen and seen[0] is engine
    assert not plan.ok
    assert service.engine_client(None) is None


def test_the_ais_resolver_takes_only_a_kept_program(tmp_path, monkeypatch):
    monkeypatch.setattr(root_helper, "ensure_helper", lambda *a, **k: pytest.fail("an AI's plan builds nothing"))
    kept = SimpleNamespace(path=tmp_path / "llmcf-gateway", sha256="2" * 64)
    monkeypatch.setattr(root_helper, "cached", lambda cache, arch, rust_dir=None, crate="": kept
                        if crate == "llmcf-gateway" else None)
    resolve = service.framework_programs(None, build=False)
    assert resolve("llmcf-gateway", "aarch64") == (kept.path, kept.sha256)
    monkeypatch.setattr(root_helper, "cached", lambda *a, **k: None)
    with pytest.raises(root_helper.HelperMissing, match="Board bundle window"):
        resolve("llmcf-gateway", "aarch64")
    with pytest.raises(root_helper.HelperMissing, match="no program"):
        resolve("other", "aarch64")


def test_the_gate_operation_runs_only_the_bundles_own_probe():
    from orchestrator.remote import gate
    text = gate.script("bundle-gateway", "gw-test", "gateway", "8088", "yes", "complete")
    assert "probe --port" in text and "components" in text and "env -i" in text
    for bad in (("gw-test", "gateway", "8088", "no", "rm"), ("../x", "gateway", "8088", "no", "health"),
                ("gw-test", "gateway", "80", "no", "health")):
        with pytest.raises(Exception):
            gate.script("bundle-gateway", *bad)
    assert gate_ops.GATEWAY_MODES == ("health", "complete", "bench")


class _Shell:
    def __init__(self, out):
        self.out, self.calls = out, []

    def run(self, script, **kw):
        self.calls.append(script)
        return SimpleNamespace(status=0, out=self.out)


def _built(tmp_path, bind="lan"):
    plan = _plan(tmp_path, values={"gateway": {"bind": bind}})
    assert plan.ok, plan.problems
    return SimpleNamespace(bundle_id="gw-test", manifest=plan.manifest)


def test_the_board_check_pins_the_fingerprint_and_reports_a_changed_one(tmp_path):
    store = gen.BundleStore(tmp_path / "state")
    (store.folder("gw-test")).mkdir(parents=True, exist_ok=True)
    built = _built(tmp_path)
    ok = (f"llmcf-gateway: fingerprint={FP}\nllmcf-gateway: health=ok upstream=up bind=lan tls=yes budget=4096 "
          "control=none version=0.1.0\nllmcf-gateway: complete=ok ms=120 tokens=2\n")
    found, notes = board.check_gateway(_Shell(ok), built, "complete", store=store, device_id=DEVICE)
    assert found["gateway"]["state"] == "ok" and found["gateway"]["pinned"] and notes == []
    record = store.boards("gw-test")[DEVICE]
    assert record["gateway_pins"] == {"gateway": FP} and "fingerprint" not in json.dumps(record["gateway"])
    changed = ok.replace(FP, "cd" * 32)
    found, notes = board.check_gateway(_Shell(changed), built, "complete", store=store, device_id=DEVICE)
    assert found["gateway"]["state"] == "certificate changed" and notes
    assert store.boards("gw-test")[DEVICE]["gateway_pins"] == {"gateway": FP}
    rows = service.bundle_rows(store) if store.list() else []
    assert isinstance(rows, list)


def test_the_check_says_plainly_when_the_gateway_is_not_there_or_not_running(tmp_path):
    built = _built(tmp_path, bind="loopback")
    found, _ = board.check_gateway(_Shell("llmcf-gateway: health=absent\n"), built)
    assert found["gateway"]["state"] == "not installed" and found["gateway"]["tls"] == "no"
    found, _ = board.check_gateway(_Shell("llmcf-gateway: health=failed reason=the_gateway_does_not_answer\n"), built)
    assert found["gateway"]["state"] == "not running"
    assert board.parse_probe("noise\nllmcf-gateway: health=ok bad$word x=1\n") == {"health": "ok", "x": "1"}


def test_the_label_names_state_model_tls_and_speed_only():
    text = service.gateway_text({"gateway": {"state": "ok", "upstream": "up", "tls": "yes", "pinned": True,
                                             "tokens_per_s": "12.50"}})
    assert text == "gateway gateway: ok, model up, TLS, pinned, 12.50 tokens/s"
    assert service.gateway_text(None) == ""
