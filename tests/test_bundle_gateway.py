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


def test_the_gateway_budget_comes_from_the_boards_measurement(tmp_path):
    """Seam of the gateway and the measured limits (failed before: the gateway took the llm block's default context,
    4096, while the measurement gave the llm component its measured context, so the gateway's budget was not the
    board's): without a context the person gave, both take the measured one; a context the person gave wins for both."""
    from test_board_measure import record
    from orchestrator.runtime.device import measure
    limits = measure.derive(record())
    plan = _plan(tmp_path, values={"gateway": {"bind": "loopback"}}, limits=limits)
    assert plan.ok, plan.problems
    comps = {c.kind: c for c in plan.manifest.components}
    assert comps["llm"].variables["ctx_size"] == limits.ctx
    assert comps["gateway"].variables["ctx_size"] == limits.ctx, "the gateway's budget is the measured context"
    given = _plan(tmp_path, values={"llm": {"ctx_size": "1024"}, "gateway": {"bind": "loopback"}}, limits=limits)
    comps = {c.kind: c for c in given.manifest.components}
    assert comps["llm"].variables["ctx_size"] == comps["gateway"].variables["ctx_size"] == 1024


def test_the_gateways_memory_guard_and_queue_come_from_the_plan(tmp_path):
    """The memory guard's floor is half the board's measured reserve (the block's conservative default without a
    measurement, the person's own value always wins); the queue's settings reach the gateway's start."""
    from test_board_measure import record
    from orchestrator.runtime.device import measure
    limits = measure.derive(record())
    plan = _plan(tmp_path, values={"gateway": {"queue_max": "8"}}, limits=limits)
    assert plan.ok, plan.problems
    gw = next(c for c in plan.manifest.components if c.kind == "gateway")
    floor = max(32, limits.reserve_mib // 2)
    assert gw.variables["memory_floor_mb"] == floor == measure.gateway_memory_floor_mib(limits)
    args = list(gw.start.args)
    assert args[args.index("--memory-floor-mb") + 1] == str(floor)
    assert args[args.index("--queue-max") + 1] == "8" and args[args.index("--queue-wait-seconds") + 1] == "120"
    assert args[args.index("--memory-psi-max") + 1] == "50"
    assert any("half the reserve" in n for n in plan.notes)
    own = _plan(tmp_path, values={"gateway": {"memory_floor_mb": "200"}}, limits=limits)
    assert next(c for c in own.manifest.components if c.kind == "gateway").variables["memory_floor_mb"] == 200
    unmeasured = _plan(tmp_path)
    assert next(c for c in unmeasured.manifest.components if c.kind == "gateway").variables["memory_floor_mb"] == 64
    assert measure.gateway_memory_floor_mib(None) is None


def test_the_units_keep_the_gateway_and_give_the_model_its_measured_memory(tmp_path):
    """The rendered limits of the units: the gateway's unit never before the model (OOMScoreAdjust -500 against
    500), its memory limit the block's 64 MB while the gateway was not measured on the board and derived from its
    measured resident memory, the queue and the bodies once it was; the llm's unit slowed and capped by its measured
    need; a limit the person set wins."""
    import dataclasses
    from test_board_measure import record
    from orchestrator.runtime.device import measure
    limits = measure.derive(record())
    both = {"llm": {"autostart": "yes"}, "gateway": {"autostart": "yes"}}
    plan = _plan(tmp_path, values=both, limits=limits)
    assert plan.ok, plan.problems
    cg = {a.component: a for a in plan.manifest.root_actions if a.action == "set_cgroup"}
    assert (cg["gateway"].oom_score_adj, cg["llm"].oom_score_adj) == (-500, 500)
    assert cg["gateway"].memory_max_mb == measure.GATEWAY_FALLBACK_MIB == 64 and cg["gateway"].memory_high_mb is None
    llm = next(c for c in plan.manifest.components if c.kind == "llm")
    model = next(f.size for f in llm.files if f.role == "model")
    need = limits.need_for(model, llm.variables["ctx_size"], llm.variables.get("kv_cache") or limits.kv_cache)
    high, top = measure.engine_memory_limits_mib(need)
    assert (cg["llm"].memory_high_mb, cg["llm"].memory_max_mb) == (high, top) and need < high < top
    # once the measurement step adds the gateway's own peak resident memory, its unit's limit follows from it
    measured = dataclasses.replace(limits, gateway_rss_mib=5)
    plan = _plan(tmp_path, values={**both, "gateway": {"autostart": "yes", "queue_max": "32"}}, limits=measured)
    gw = next(a for a in plan.manifest.root_actions if a.action == "set_cgroup" and a.component == "gateway")
    # 5 MiB + 32 waiting * 64 KiB + 2 in flight * (64 KiB + 4 * 256 KiB) + 16 MiB margin
    assert gw.memory_max_mb == measure.gateway_memory_max_mib(measured, 32, 2, 256) == 5 + 5 + 16
    own = _plan(tmp_path, values={"llm": {"autostart": "yes", "memory_limit_mb": "3000"}}, limits=limits)
    mine = next(a for a in own.manifest.root_actions if a.action == "set_cgroup")
    assert (mine.memory_max_mb, mine.memory_high_mb, mine.oom_score_adj) == (3000, None, 500)
    unmeasured = _plan(tmp_path, values={"llm": {"autostart": "yes"}})
    mine = next(a for a in unmeasured.manifest.root_actions if a.action == "set_cgroup")
    assert (mine.memory_max_mb, mine.memory_high_mb) == (2048, None)


def test_the_gateways_unit_runs_through_llmcf_install_from_its_fixed_place(tmp_path):
    """Seam of the gateway and the root readiness: the gateway's autostart is a typed root action of the manifest
    (its account, its unit, its memory limit), and every root action runs through the gate operation bundle-root,
    which starts llmcf-install only from its fixed root-owned place, never from the bundle's own folder."""
    from orchestrator.remote import gate
    plan = _plan(tmp_path, values={"gateway": {"autostart": "yes"}})
    assert plan.ok, plan.problems
    mine = [(a.action, a.unit) for a in plan.manifest.root_actions if a.component == "gateway"]
    assert ("install_unit", "llmcf-gateway.service") in mine and ("create_user", None) in mine
    body = gate.script("bundle-root", "apply", "gw-test", "a" * 64)
    assert gate_ops.FIXED_HELPER in body
    runs = [ln for ln in body.splitlines() if "sudo" in ln and "llmcf-install" in ln]
    assert runs and all(".kept" not in ln for ln in runs), runs


def test_the_gateway_check_keeps_the_time_of_the_boards_last_installer_run(tmp_path, monkeypatch):
    """Regression (overwritten before: the gateway check wrote the board record's "at", the time of the last
    installer run that `bundle list` shows, so a status read looked like a deploy): the check keeps its own time in
    "gateway_at"."""
    store = gen.BundleStore(tmp_path / "state")
    (store.folder("gw-test")).mkdir(parents=True, exist_ok=True)
    store.record_board("gw-test", DEVICE, last="apply", result="done")
    before = store.boards("gw-test")[DEVICE]["at"]
    record = json.loads((store.folder("gw-test") / "boards.json").read_text(encoding="utf-8"))
    record[DEVICE]["at"] = "2026-01-01T00:00:00Z"   # an earlier run, so a new stamp would differ
    (store.folder("gw-test") / "boards.json").write_text(json.dumps(record), encoding="utf-8")
    assert before
    ok = (f"llmcf-gateway: fingerprint={FP}\nllmcf-gateway: health=ok upstream=up bind=lan tls=yes budget=4096 "
          "control=none version=0.1.0\n")
    board.check_gateway(_Shell(ok), _built(tmp_path), "complete", store=store, device_id=DEVICE)
    entry = store.boards("gw-test")[DEVICE]
    assert entry["at"] == "2026-01-01T00:00:00Z" and entry["last"] == "apply", entry
    assert entry.get("gateway_at") and entry["gateway"]["gateway"]["state"] == "ok"


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


def test_the_gateway_may_start_and_stop_exactly_the_bundles_engine_units(tmp_path):
    """allow_unit_control: with the gateway's own unit and an engine unit, the plan lets the gateway's account start,
    stop and restart exactly that unit (llmcf-install writes a polkit rule, or a sudoers file without polkit), and the
    gateway is told its engines; the steps carry the units as one checked value."""
    from orchestrator.runtime.bundle import installer as inst
    from orchestrator.runtime.bundle.manifest import dump_manifest, load_manifest
    plan = _plan(tmp_path, values={"llm": {"autostart": "yes"}, "gateway": {"autostart": "yes"}})
    assert plan.ok, plan.problems
    control = [a for a in plan.manifest.root_actions if a.action == "allow_unit_control"]
    assert len(control) == 1 and control[0].component == "gateway" and control[0].user == "llmcf"
    assert control[0].units == ("llmcf-llm.service",)
    gw = next(c for c in plan.manifest.components if c.kind == "gateway")
    args = list(gw.start.args)
    assert args[args.index("--engine") + 1].startswith("llmcf-llm.service", len("llm="))
    assert args[-2:] == ["--engines", "together"]
    assert any("polkit" in n and "sudoers" in n for n in plan.notes)
    assert load_manifest(dump_manifest(plan.manifest)) == plan.manifest
    steps = inst.render_steps(plan.manifest, "a" * 64)
    assert "root\tallow_unit_control\tgateway\tuser=llmcf\tunits=llmcf-llm.service" in steps
    # without the engines' units, or without the gateway's own unit, nothing to allow
    only_gw = _plan(tmp_path, values={"gateway": {"autostart": "yes"}})
    assert not [a for a in only_gw.manifest.root_actions if a.action == "allow_unit_control"]
    assert "--engine" not in only_gw.manifest.components[1].start.args
    only_llm = _plan(tmp_path, values={"llm": {"autostart": "yes"}})
    assert not [a for a in only_llm.manifest.root_actions if a.action == "allow_unit_control"]
    assert any("without its own unit" in n for n in only_llm.notes)
    # the unprivileged-only variant writes no units: nothing is allowed, the gateway is told none
    sel = gen.Selection(blocks=["llm-llama-server", "gateway-llmcf"], unprivileged_only=True,
                        values={"llm": {"autostart": "yes"}, "gateway": {"autostart": "yes"}},
                        files={"llm": {"model": gen.FileChoice(path=str(_files(tmp_path)[0]), licence="apache-2.0")}})
    unpriv = gen.plan_bundle(sel, catalog=blk.load_catalog(), profile=RK3566, device_id=DEVICE, offline=False,
                             bundle_id="gw-test", runtime=_runtime_tarball(tmp_path), programs=_programs(tmp_path),
                             unmeasured_ok=True)
    assert unpriv.ok, unpriv.problems
    assert not [a for a in unpriv.manifest.root_actions if a.action == "allow_unit_control"]


def _voice(tmp: Path, engines="together", limits=None, autostart=True, gateway=True):
    """llm, speech (a picked whisper program) and the gateway, each with its unit; the person's engine choice."""
    from tests.test_board_bundle import _selection
    yes = {"autostart": "yes"} if autostart else {}
    blocks = ["llm-llama-server", "stt-whisper-cpp-program"] + (["gateway-llmcf"] if gateway else [])
    values = {"llm": dict(yes), "stt": dict(yes)}
    if gateway:
        values["gateway"] = {"autostart": "yes"}
    sel = _selection(tmp, blocks=blocks, values=values, engines=engines)
    return gen.plan_bundle(sel, catalog=blk.load_catalog(), profile=RK3566, device_id=DEVICE, offline=False,
                           bundle_id="gw-test", runtime=_runtime_tarball(tmp), programs=_programs(tmp),
                           limits=limits, unmeasured_ok=limits is None)


def _squeezed(tmp: Path, fit: str):
    """The board's measurement with its free memory cut so that the voice bundle fits `fit`: "together", "in-turn"
    (only one engine at a time) or "none"."""
    import dataclasses
    from test_board_measure import record
    from orchestrator.runtime.device import measure
    limits = measure.derive(record())
    e = _voice(tmp, limits=limits).engines
    available = {"together": e["together_mb"], "in-turn": e["in_turn_mb"], "none": e["in_turn_mb"] - 1}[fit]
    return dataclasses.replace(limits, available_mib=available, usable_mib=available - limits.reserve_mib)


def test_the_plan_always_shows_whether_the_engines_fit_together_and_in_turn(tmp_path):
    from test_board_measure import record
    from orchestrator.runtime.device import measure
    limits = measure.derive(record())
    plan = _voice(tmp_path, limits=limits)
    assert plan.ok, plan.problems
    e = plan.engines
    assert e["mode"] == "together" and set(e["engines_mb"]) == {"llm", "stt"}
    assert e["together_mb"] == sum(e["engines_mb"].values()) + e["rest_mb"] + e["reserve_mb"]
    assert e["in_turn_mb"] == max(e["engines_mb"].values()) + e["rest_mb"] + e["reserve_mb"]
    assert e["available_mb"] == limits.available_mib and e["reserve_mb"] == limits.reserve_mib
    assert e["fits_together"] and e["fits_in_turn"] and e["largest"] == "llm"
    check = next(c for c in plan.checks if c["check"] == "engines")
    assert check["result"] == "passed" and "all together" in check["detail"] and "in turn" in check["detail"]
    assert "your choice: all together" in check["detail"]
    assert plan.labels()["engines"] == e, "an AI reads the numbers too, never a choice it could make"
    # the person's language: English and German lines with the same numbers
    en = service.engines_lines(plan, "en")
    de = service.engines_lines(plan, "de")
    assert en[0].startswith("All engines together: ") and f"{e['together_mb']} MB of {limits.available_mib}" in en[0]
    assert de[0].startswith("Alle Programme zusammen: ") and f"{e['together_mb']} MB von" in de[0]
    assert en[-1] == "Chosen: all engines together." and de[-1] == "Gewählt: alle Programme zusammen."
    # together: the gateway is told its engines and the choice, the llm's port is its own upstream
    gw = next(c for c in plan.manifest.components if c.kind == "gateway")
    args = list(gw.start.args)
    stt_port = next(c for c in plan.manifest.components if c.kind == "stt").variables["port"]
    assert f"llm=llmcf-llm.service:{e['engines_mb']['llm']}" in args
    assert f"stt=llmcf-stt.service:{e['engines_mb']['stt']}:{stt_port}" in args
    assert args[args.index("--engines") + 1] == "together" and "--engines-budget-mb" not in args
    assert "--llm-unit" not in args
    control = next(a for a in plan.manifest.root_actions if a.action == "allow_unit_control")
    assert control.units == ("llmcf-llm.service", "llmcf-stt.service")


def test_in_turn_when_together_does_not_fit_and_refused_when_one_does_not_either(tmp_path):
    tight = _squeezed(tmp_path, "in-turn")
    together = _voice(tmp_path, limits=tight)
    assert not together.ok and any("needs about" in p for p in together.problems)
    assert next(c for c in together.checks if c["check"] == "engines")["result"] == "failed"
    assert any("one after another they do" in n and "load the engines in turn" in n for n in together.notes)
    turn = _voice(tmp_path, engines="in-turn", limits=tight)
    assert turn.ok, turn.problems
    assert next(c for c in turn.checks if c["check"] == "engines")["result"] == "passed"
    assert next(c for c in turn.checks if c["check"] == "memory")["result"] in ("passed", "warning")
    gw = next(c for c in turn.manifest.components if c.kind == "gateway")
    args = list(gw.start.args)
    assert args[args.index("--engines") + 1] == "in-turn"
    rest = turn.engines["rest_mb"]
    assert args[args.index("--engines-budget-mb") + 1] == str(tight.usable_mib - rest)
    assert any("starts the speech and vision engines for requests through itself" in n for n in turn.notes)
    assert not any("not there yet" in w for w in turn.warnings)
    assert service.engines_lines(turn, "en")[-1] == "Chosen: the engines in turn, loaded by the gateway."
    # even the largest engine alone does not fit: in turn is refused with the numbers
    none = _voice(tmp_path, engines="in-turn", limits=_squeezed(tmp_path, "none"))
    assert not none.ok and any("in turn does not fit either" in p for p in none.problems)


def test_in_turn_needs_the_gateway_and_the_engines_units(tmp_path):
    no_units = _voice(tmp_path, engines="in-turn", autostart=False)
    assert any("needs the gateway with its own unit" in p for p in no_units.problems)
    no_gateway = _voice(tmp_path, engines="in-turn", gateway=False)
    assert any("needs the gateway with its own unit" in p for p in no_gateway.problems)
    with pytest.raises(gen.BundleError):
        service.selection_from(preset="llm", engines="sometimes")
    assert service.selection_from(preset="llm", engines="in-turn").engines == "in-turn"
    assert service.selection_from(preset="llm").engines == "together"


def test_an_ai_cannot_choose_how_the_engines_run():
    """The engines' choice is the person's (CLI flag, the window's checkbox); the read-only bundle_plan tool takes no
    such field and refuses it."""
    from pydantic import ValidationError
    from orchestrator.tools.tools_bundle import BundlePlanIn
    with pytest.raises(ValidationError):
        BundlePlanIn(device_id="0123456789abcdef", preset="voice", engines="in-turn")


def test_the_idle_stop_reaches_the_gateway_only_with_the_engines_units(tmp_path):
    plan = _plan(tmp_path, values={"llm": {"autostart": "yes"}, "gateway": {"autostart": "yes",
                                                                            "idle_stop_minutes": "15"}})
    assert plan.ok, plan.problems
    args = list(next(c for c in plan.manifest.components if c.kind == "gateway").start.args)
    assert args[args.index("--idle-stop-minutes") + 1] == "15" and "--engine" in args
    alone = _plan(tmp_path, values={"gateway": {"idle_stop_minutes": "15"}})
    assert any("idle_stop_minutes stops the engines" in p for p in alone.problems)
    default = _plan(tmp_path)
    gw = next(c for c in default.manifest.components if c.kind == "gateway")
    assert list(gw.start.args)[-2:] == ["--idle-stop-minutes", "0"], "0 stops nothing and needs no unit"


def test_the_speech_route_reaches_the_bundles_whisper_server(tmp_path):
    """The gateway's speech route: stt_port is the port of the bundle's whisper-server and passed with the audio cap;
    the unit's memory limit makes room for the uploads in flight; a value the person sets for the port is refused."""
    from orchestrator.runtime.device import measure
    plan = _voice(tmp_path)
    assert plan.ok, plan.problems
    comps = {c.kind: c for c in plan.manifest.components}
    stt_port = comps["stt"].variables["port"]
    assert comps["gateway"].variables["stt_port"] == stt_port
    args = list(comps["gateway"].start.args)
    assert args[args.index("--stt-port") + 1] == str(stt_port) and args[args.index("--max-audio-mb") + 1] == "25"
    assert "--vision-port" not in args and args[-2:] == ["--engines", "together"]
    cg = next(a for a in plan.manifest.root_actions if a.action == "set_cgroup" and a.component == "gateway")
    # 64 MiB and two uploads of 25 MB in flight
    assert cg.memory_max_mb == measure.gateway_fallback_mib(2, 0, 25 * 1024) == 64 + 50
    assert gen.gateway_upload_kb({"stt_port": 8090, "max_audio_mb": 25}) == (0, 25 * 1024)
    assert gen.gateway_upload_kb({"vision_port": 8081, "max_images": 4, "max_images_kb": 3}) == (4, 0)
    assert gen.gateway_upload_kb({"max_audio_mb": 25, "max_images_kb": 4096}) == (0, 0)
    # without speech: no speech arguments, the block's 64 MB
    plain = _plan(tmp_path, values={"llm": {"autostart": "yes"}, "gateway": {"autostart": "yes"}})
    gw = next(c for c in plain.manifest.components if c.kind == "gateway")
    assert gw.variables["stt_port"] == 0 and "--stt-port" not in gw.start.args
    cg = next(a for a in plain.manifest.root_actions if a.action == "set_cgroup" and a.component == "gateway")
    assert cg.memory_max_mb == 64
    # the port is the bundle's to set
    mine = _plan(tmp_path, values={"gateway": {"stt_port": "9000"}})
    assert any("stt_port is set by the bundle" in p for p in mine.problems)
    # measured: the uploads are in the derived limit as well
    import dataclasses
    from test_board_measure import record
    measured = dataclasses.replace(measure.derive(record()), gateway_rss_mib=5)
    assert measure.gateway_memory_max_mib(measured, 32, 2, 256, 0, 25 * 1024) == 5 + 2 + 50 + 1 + 16


def test_chats_with_images_reach_the_bundles_vision_server(tmp_path):
    """The gateway's vision: vision_port and vision_ctx_size come from the bundle's vision component (a llama-server
    of its own with --mmproj) and reach the gateway with the image caps."""
    from orchestrator.runtime.device.launcher import render_start_script
    from tests.test_bundle_components import _runtime_package, _vision_files
    model, _w, _p = _files(tmp_path)
    vmodel, mm = _vision_files(tmp_path)
    sel = gen.Selection(blocks=["llm-llama-server", "vision-mmproj", "gateway-llmcf"],
                        values={"vision": {"ctx_size": "8192"}},
                        files={"llm": {"model": gen.FileChoice(path=str(model), licence="apache-2.0")},
                               "vision": {"model": gen.FileChoice(path=str(vmodel), licence="apache-2.0"),
                                          "mmproj": gen.FileChoice(path=str(mm), licence="apache-2.0")}})
    plan = gen.plan_bundle(sel, catalog=blk.load_catalog(), profile=RK3566, device_id=DEVICE, offline=False,
                           bundle_id="gw-test", runtime=_runtime_package(tmp_path, render_start_script().encode()),
                           programs=_programs(tmp_path), unmeasured_ok=True)
    assert plan.ok, plan.problems
    comps = {c.kind: c for c in plan.manifest.components}
    gw = comps["gateway"]
    assert gw.variables["vision_port"] == comps["vision"].variables["port"] == 8081
    assert gw.variables["vision_ctx_size"] == 8192
    args = list(gw.start.args)
    for flag, value in (("--vision-port", "8081"), ("--vision-ctx-size", "8192"), ("--max-images", "4"),
                        ("--max-image-kb", "2048"), ("--max-images-kb", "4096"), ("--image-tokens", "768")):
        assert args[args.index(flag) + 1] == value, flag
    assert "--stt-port" not in args


def test_a_speech_block_the_gateway_does_not_speak_to_leaves_its_route_at_501():
    plan = gen.BundlePlan(bundle_id="x", device_id=DEVICE, selection=gen.Selection(blocks=[]))
    catalog = blk.load_catalog()
    given = {}
    pairs = [("stt", catalog.get("stt-nemo-sherpa")), ("gateway", catalog.get("gateway-llmcf"))]
    gen._gateway_from_engines(plan, "gateway", given, pairs, gen.Selection(blocks=[]))
    assert "stt_port" not in given and any("answers 501" in n and "stt-nemo-sherpa" in n for n in plan.notes)
