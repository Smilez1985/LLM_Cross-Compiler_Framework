"""The board bundle for AI callers (orchestrator/tools/tools_bundle.py): bundle_plan gives the plan as labels only (no
path, address or account) and the blocks to choose from; bundle_deploy is external, asked in the framework's own
window only, never granted, never delegated, logs in with the framework's key only and the host key the person was
shown, and its result names no account or path. Both are hidden until the person enables them.

The board is the fake board of tests/test_board_bundle.py behind llmcf's real gate (POSIX only). Every name, address
and key is a test value (documentation addresses, keys made for the test)."""

import json
from pathlib import Path

import pytest

from test_board_bundle import POSIX, RK3566, VERIFIER, GateShell, _fake_board, _runtime_tarball, _selection
from test_runtime_tools import allow, asking, call, wait_job

from orchestrator.tools.policy import Decision, ToolTier
from orchestrator.tools.registry import ToolError

ADDRESS = "192.0.2.10"


@pytest.fixture
def fw(make_framework):
    return make_framework()


@pytest.fixture
def device(fw, monkeypatch):
    """A trusted, paired, probed board with its deploy user; its host key is read as the trusted one."""
    paramiko = pytest.importorskip("paramiko")
    from orchestrator.runtime.device.settings import DeploySettings
    from orchestrator.runtime.device.trusted import TrustedDevices
    from orchestrator.runtime.profile import ProfileStore
    from orchestrator.utils import ssh_trust
    state = Path(fw.config.user_state_dir)
    key = paramiko.ECDSAKey.generate()
    ssh_trust.trust_host_key(state, ADDRESS, 22, key)
    dev = TrustedDevices(state).record(ADDRESS, 22, "boarduser", key, via="cli", paired=True, label="Test board")
    store = ProfileStore(state)
    store.save_device(dev.device_id, RK3566, label="Test board", address=ADDRESS)
    store.save_deploy_settings(dev.device_id, DeploySettings(ssh_user="boarduser").as_dict())
    monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port=22, timeout=10.0: key)
    return dev


def test_both_tools_are_hidden_until_enabled_and_the_deploy_is_the_persons_alone(fw):
    from orchestrator.tools.delegation import DELEGATION_SCOPE, NEVER_DELEGATE
    from orchestrator.tools.facts_catalog import TOOL_FACTS
    from orchestrator.tools.grants import NEVER_GRANT
    from orchestrator.tools.tool_windows import TOOL_WINDOWS
    rows = {r["tool"]: r for r in fw.tool_registry.permissions("mcp")}
    for name in ("bundle_plan", "bundle_deploy"):
        assert rows[name]["decision"] == "deny" and not rows[name]["listed"], name
        assert name in TOOL_FACTS
    plan, deploy = fw.tool_registry.get("bundle_plan"), fw.tool_registry.get("bundle_deploy")
    assert plan.tier == ToolTier.READ
    assert deploy.tier == ToolTier.EXTERNAL and deploy.channels == ("framework",) and deploy.grant is None
    assert deploy.cap() == Decision.ASK
    assert "bundle_deploy" in NEVER_GRANT and "bundle_deploy" in NEVER_DELEGATE
    assert DELEGATION_SCOPE["bundle_deploy"].who == "person"
    assert TOOL_WINDOWS["bundle_deploy"] == "board_bundle"


def test_bundle_plan_gives_labels_only_and_the_blocks(fw, device, tmp_path):
    allow(fw, "bundle_plan")
    out = call(fw, "bundle_plan", {"device_id": device.device_id, "preset": "stt",
                                   "values": {"stt": {"port": 8091, "autostart": True, "user": "llmcfsvc"}}})
    plan = out.plan
    # the files are the person's choice: the plan names what is missing, and the checked values pass
    assert not plan["ok"] and any("choose the model file" in p for p in plan["problems"])
    assert not any("port" in p for p in plan["problems"])
    bad = call(fw, "bundle_plan", {"device_id": device.device_id, "preset": "stt", "values": {"stt": {"port": 80}}})
    assert any("stt:" in p and "port" in p for p in bad.plan["problems"])
    assert {c["check"] for c in plan["checks"]} >= {"architecture", "memory"}
    assert "stt-whisper-cpp" in {b["id"] for b in out.blocks}
    text = json.dumps(out.model_dump())
    for private in (ADDRESS, "boarduser", "llmcfsvc", str(tmp_path)):
        assert private not in text, private


def test_bundle_plan_refuses_an_unknown_board_and_an_odd_value(fw, device):
    allow(fw, "bundle_plan")
    with pytest.raises(ToolError) as e:
        call(fw, "bundle_plan", {"device_id": "0" * 16, "preset": "stt"})
    assert e.value.code == "unknown_id"
    with pytest.raises(ToolError) as e:
        call(fw, "bundle_plan", {"device_id": device.device_id, "values": {"stt": {"language": "$(id)"}}})
    assert e.value.code == "invalid_argument"


def test_bundle_plan_passes_the_verified_index_with_its_revocations(fw, device, monkeypatch):
    from orchestrator.runtime.bundle import blocks as blk
    from orchestrator.runtime.bundle import service
    seen = []
    real = blk.load_catalog
    sentinel = object()
    monkeypatch.setattr(service, "catalog_sources", lambda framework: (None, sentinel))
    monkeypatch.setattr(blk, "load_catalog", lambda app_root=None, **kw: seen.append(kw.get("index")) or
                        real(app_root))
    allow(fw, "bundle_plan")
    call(fw, "bundle_plan", {"device_id": device.device_id, "preset": "stt"})
    assert seen and all(i is sentinel for i in seen)


def _built_for(fw, device, tmp_path, board):
    from orchestrator.runtime.bundle import generator as gen
    from orchestrator.runtime.bundle import blocks as blk
    selection = _selection(tmp_path, blocks=["stt-whisper-cpp"])
    del selection.files["llm"]
    plan = gen.plan_bundle(selection, catalog=blk.load_catalog(), profile=RK3566, device_id=device.device_id,
                           runtime=_runtime_tarball(tmp_path), offline=False, bundle_id="stt-test")
    assert plan.ok, plan.problems
    return gen.build_bundle(plan, gen.BundleStore(Path(fw.config.user_state_dir)), board.signer)


@POSIX
@VERIFIER
def test_bundle_deploy_asks_in_the_framework_logs_in_with_the_key_only_and_reports_without_private_data(
        fw, device, tmp_path, monkeypatch):
    from orchestrator.runtime.bundle import installer as inst
    from orchestrator.runtime.device import remote
    from orchestrator.runtime.device import service as dev
    board = _fake_board(tmp_path, "root")
    built = _built_for(fw, device, tmp_path, board)
    logins = []

    def connect(d, state_dir, user, expect_fingerprint, on_client=None, step="", **kw):
        logins.append((d.device_id, user, expect_fingerprint, step))
        return GateShell(board)
    monkeypatch.setattr(dev, "connect", connect)
    monkeypatch.setattr(remote, "RemoteShell", lambda session: session)
    allow(fw, "bundle_deploy")
    confirm = asking(True)
    started = call(fw, "bundle_deploy", {"bundle_id": built.bundle_id}, confirm=confirm)
    (question,) = confirm.asked
    assert "Test board" in question and "stt-test" in question   # the person sees the login too (person_only)
    job = wait_job(fw, started.job_id)
    assert job.state == "done", job
    assert logins == [(device.device_id, "boarduser", device.fingerprint, "bundle")]
    assert (board.home / inst.BUNDLE_REL / "stt-test" / "components" / "stt" / "model.bin").is_file()
    text = json.dumps(job.result if hasattr(job, "result") else str(job), default=str)
    for private in (ADDRESS, "boarduser", str(board.home), str(tmp_path)):
        assert private not in text, private


def test_a_declined_deploy_changes_nothing(fw, device, tmp_path, monkeypatch):
    from orchestrator.runtime.device import service as dev
    board = _fake_board(tmp_path, "root")
    built = _built_for(fw, device, tmp_path, board)
    logins = []
    monkeypatch.setattr(dev, "connect", lambda *a, **k: logins.append(a) or GateShell(board))
    allow(fw, "bundle_deploy")
    confirm = asking(False)
    with pytest.raises(ToolError):
        call(fw, "bundle_deploy", {"bundle_id": built.bundle_id}, confirm=confirm)
    assert len(confirm.asked) == 1 and logins == []


def test_bundle_deploy_refuses_an_unknown_bundle_and_one_changed_since_it_was_built(fw, device, tmp_path):
    board = _fake_board(tmp_path, "root")
    built = _built_for(fw, device, tmp_path, board)
    allow(fw, "bundle_deploy")
    with pytest.raises(ToolError) as e:
        call(fw, "bundle_deploy", {"bundle_id": "nope-test"}, confirm=asking(True))
    assert e.value.code == "unknown_id"
    built.payload()[0][1].write_bytes(b"changed")
    confirm = asking(True)
    with pytest.raises(ToolError) as e:
        call(fw, "bundle_deploy", {"bundle_id": built.bundle_id}, confirm=confirm)
    assert "not the one that was built" in str(e.value) and confirm.asked == []


def test_bundle_plan_tells_the_ai_that_root_actions_wait_for_llmcf_install(fw, device):
    allow(fw, "bundle_plan")
    out = call(fw, "bundle_plan", {"device_id": device.device_id, "preset": "stt",
                                   "values": {"stt": {"autostart": True}}})
    assert any("llmcf-install" in n and "stops before them" in n for n in out.plan["notes"]), out.plan


def test_bundle_deploy_refuses_a_bundle_whose_block_was_revoked_since_the_build(fw, device, tmp_path, monkeypatch):
    from test_board_bundle import _indexed_block
    from orchestrator.runtime.bundle import blocks as blk
    from orchestrator.runtime.bundle import generator as gen
    from orchestrator.runtime.bundle import service
    from orchestrator.runtime.device import service as dev
    board = _fake_board(tmp_path, "root")
    app = tmp_path / "app"
    _indexed_block(app)
    selection = _selection(tmp_path, blocks=["stt-indexed"])
    del selection.files["llm"]
    plan = gen.plan_bundle(selection, catalog=blk.load_catalog(app), profile=RK3566, device_id=device.device_id,
                           runtime=_runtime_tarball(tmp_path), offline=False, bundle_id="stt-test")
    assert plan.ok, plan.problems
    built = gen.build_bundle(plan, gen.BundleStore(Path(fw.config.user_state_dir)), board.signer)

    class Revoking:
        def revocation(self, kind, pack_id, version, sha256):
            return object() if (kind, pack_id) == ("block", "stt-indexed") else None
    monkeypatch.setattr(service, "catalog_sources", lambda framework: (app, Revoking()))
    logins = []
    monkeypatch.setattr(dev, "connect", lambda *a, **k: logins.append(a) or GateShell(board))
    allow(fw, "bundle_deploy")
    confirm = asking(True)
    with pytest.raises(ToolError) as e:
        call(fw, "bundle_deploy", {"bundle_id": built.bundle_id}, confirm=confirm)
    assert "revoked" in str(e.value) and confirm.asked == [] and logins == []
    assert str(tmp_path) not in str(e.value)


def test_bundle_plan_never_builds_a_framework_program_for_an_ai(fw, device, monkeypatch):
    """bundle_plan is a read-only tool: a block whose program the framework builds (the gateway) takes only a kept
    one; nothing is built, fetched or written for an AI's plan (the person's plan builds it once)."""
    from orchestrator.runtime.bundle import root_helper
    built = []

    def no_build(*a, **kw):
        built.append(kw.get("crate"))
        raise AssertionError("an AI's plan must not build")
    monkeypatch.setattr(root_helper, "default_builder", no_build)
    monkeypatch.setattr(root_helper, "ensure_helper", no_build)
    allow(fw, "bundle_plan")
    out = call(fw, "bundle_plan", {"device_id": device.device_id, "blocks": ["llm-llama-server", "gateway-llmcf"]})
    assert built == []
    assert not out.plan["ok"]
    assert any("gateway" in p and "Board bundle window" in p for p in out.plan["problems"]), out.plan["problems"]
