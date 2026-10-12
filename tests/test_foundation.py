"""Phase-1 foundation: target capabilities, build service, tool registry, permission policy, audit log."""

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

from conftest import REPO_ROOT
from orchestrator.schemas.build import BuildRequestIn
from orchestrator.schemas.targets import TargetCapabilities
from orchestrator.tools.audit import AuditLog
from orchestrator.tools.policy import Decision, PolicyStore, ToolPolicy, ToolTier
from orchestrator.tools.registry import ToolContext, ToolDenied, ToolError, ToolInputError, ToolRegistry, ToolSpec

ROCKCHIP_YML = REPO_ROOT / "targets" / "Rockchip" / "target.yml"


@pytest.fixture(scope="module")
def rockchip():
    return TargetCapabilities.model_validate(yaml.safe_load(ROCKCHIP_YML.read_text())["capabilities"])


class TestCapabilities:

    def test_gguf_types_match_the_build_script(self, rockchip):
        script = (REPO_ROOT / "targets/Rockchip/modules/build.sh").read_text()
        quant = set(re.search(r'GGUF_QUANT_TYPES=" (.*?) "', script, re.S).group(1).replace("\\\n", " ").split())
        convert = {t.upper() for t in re.search(r'GGUF_CONVERT_TYPES=" (.*?) "', script).group(1).split()} - {"AUTO"}
        needs = set(re.search(r'GGUF_NEEDS_IMATRIX=" (.*?) "', script).group(1).split())
        gguf = rockchip.formats["gguf"]
        assert set(gguf.quantizations) == quant | convert
        assert set(gguf.needs_imatrix) == needs

    def test_rkllm_types_and_boards_match_the_exporter(self, rockchip):
        exporter = (REPO_ROOT / "targets/Rockchip/scripts/export_rkllm.py").read_text()
        types = set(re.findall(r'"(w\d+a\d+(?:_g\d+)?)"', exporter.split("NPU_CORES")[0]))
        boards = set(re.search(r"NPU_CORES = \{(.*?)\}", exporter).group(1).replace('"', "").replace(" ", "").split(","))
        boards = {b.split(":")[0] for b in boards if b}
        rkllm = rockchip.formats["rkllm"]
        assert set(rkllm.quantizations) <= types
        assert set(rockchip.boards_for("rkllm")) == boards

    @pytest.mark.parametrize("fmt, board, quant, kwargs, problem", [
        ("rkllm", "rk3566", None, {}, "does not run on rk3566"),
        ("rkllm", "rk3588", "w4a16", {}, "not supported for rkllm on rk3588"),
        ("gguf", "rk3566", "IQ2_XS", {}, "needs an importance matrix"),
        ("rkllm", "rk3588", "w4a16", {}, "not supported for rkllm on rk3588"),
        ("rknn", "rk3588", "i8", {}, "needs a calibration dataset"),
        ("tflite", None, None, {}, "not available"),
        ("rkllm", "rk3576", "w4a16", {"imatrix": True, "dataset": True}, "not used by the rkllm"),
    ])
    def test_invalid_requests_are_explained(self, rockchip, fmt, board, quant, kwargs, problem):
        problems = rockchip.check_request(fmt, board, quant, **kwargs)
        assert any(problem in p for p in problems), problems

    @pytest.mark.parametrize("fmt, board, quant, kwargs", [
        ("gguf", "rk3566", "q4_0", {}),  # case-insensitive
        ("gguf", "rk3588", "IQ2_XS", {"imatrix": True, "dataset": True}),
        ("rkllm", "rk3588", None, {}),
        ("rkllm", "rk3576", "w4a16_g128", {"dataset": True}),
        ("rknn", "rk3566", "i8", {"dataset": True}),
    ])
    def test_valid_requests_pass(self, rockchip, fmt, board, quant, kwargs):
        assert rockchip.check_request(fmt, board, quant, **kwargs) == []

    def test_board_defaults(self, rockchip):
        assert rockchip.formats["gguf"].default_for("rk3566") == "Q4_0"
        assert rockchip.formats["rkllm"].default_for("rk3576") == "w4a16"
        assert rockchip.formats["rkllm"].default_for("rk3588") == "w8a8"

    def test_inconsistent_capabilities_are_rejected(self):
        with pytest.raises(ValidationError, match="unknown boards"):
            TargetCapabilities.model_validate({"boards": {"a": {"label": "A"}},
                                               "formats": {"gguf": {"label": "g", "quantizations": ["Q4_0"],
                                                                    "default_quantization": "Q4_0", "boards": ["b"]}}})
        with pytest.raises(ValidationError, match="default_quantization"):
            TargetCapabilities.model_validate({"boards": {"a": {"label": "A"}},
                                               "formats": {"gguf": {"label": "g", "quantizations": ["Q4_0"],
                                                                    "default_quantization": "Q8_0"}}})

    def test_target_manager_exposes_capabilities(self, booted_framework):
        caps = booted_framework.target_manager.get_capabilities("Rockchip")
        assert caps is not None and "rkllm" in caps.formats


class FakeOrchestrator:
    def __init__(self):
        self._workflows = {}
        self.submitted = []
        self.cancelled = []
        self.build_engine = SimpleNamespace(get_build_status=lambda bid: SimpleNamespace(
            logs=["step 1", "token sk-abcdefghijklmnopqrstuvwx1234 leaked"]))

    async def submit_build_request(self, req):
        from orchestrator.Core.orchestrator import OrchestrationStatus, WorkflowState
        self.submitted.append(req)
        state = WorkflowState(req.request_id, OrchestrationStatus.QUEUED, datetime.now())
        state.build_ids.append(f"{req.request_id}_001")
        self._workflows[req.request_id] = state
        return req.request_id

    async def cancel_request(self, rid):
        from orchestrator.Core.orchestrator import OrchestrationStatus
        self.cancelled.append(rid)
        self._workflows[rid].status = OrchestrationStatus.CANCELLED
        return True


@pytest.fixture
def service(booted_framework):
    from orchestrator.Core.build_service import BuildService
    booted_framework.orchestrator = FakeOrchestrator()
    svc = BuildService(booted_framework)
    return svc


class TestBuildService:

    def request(self, **overrides):
        values = dict(model="HuggingFaceTB/SmolLM2-135M-Instruct", target="Rockchip", format="gguf", board="rk3566")
        values.update(overrides)
        return BuildRequestIn(**values)

    def test_defaults_come_from_the_capabilities(self, service):
        handle = service.start_build(self.request())
        req = service.framework.orchestrator.submitted[0]
        assert handle.request_id == req.request_id
        assert req.quantization_options == ["Q4_0"] and req.target_board == "rk3566"
        assert Path(req.output_base_dir).is_absolute()

    def test_single_board_formats_fill_the_board(self, service, tmp_path):
        caps = service.capabilities("Rockchip")
        assert len(caps.boards_for("rkllm")) == 2  # needs an explicit board
        with pytest.raises(ValueError, match="w4a16"):
            service.start_build(self.request(format="rkllm", board="rk3588", quantization="w4a16"))

    @pytest.mark.parametrize("model", ["../../etc/passwd", "not a repo", "/etc"])
    def test_bad_model_sources_are_refused(self, service, model):
        with pytest.raises(ValueError):
            service.start_build(self.request(model=model))

    def test_unknown_target(self, service):
        with pytest.raises(ValueError, match="unknown"):
            service.start_build(self.request(target="Nope"))

    def test_extra_fields_are_rejected(self):
        with pytest.raises(ValidationError):
            BuildRequestIn(model="a/b", target="Rockchip", format="gguf", shell="rm -rf /")

    def test_status_cancel_and_redacted_log(self, service):
        rid = service.start_build(self.request()).request_id
        assert service.get_status(rid).status == "queued"
        assert service.cancel(rid).status == "cancelled"
        lines = service.log_tail(rid).lines
        assert lines[0] == "step 1" and "sk-abcdefghijklmnopqrstuvwx1234" not in lines[1]

    def test_artifacts_come_from_manifests(self, service, tmp_path):
        out = Path(service._output_dir()) / "Rockchip" / "m" / "Q4_0"
        out.mkdir(parents=True)
        (out / "build_info.json").write_text(json.dumps({"model": "m", "format": "gguf", "primary": "m.gguf",
                                                         "artifacts": [{"file": "m.gguf", "sha256": "ab"}]}))
        items = service.list_artifacts().artifacts
        assert [(a.primary, a.sha256) for a in items] == [("m.gguf", "ab")]


class TestPolicy:

    def test_defaults_are_safe_and_unconfigured(self, tmp_path):
        policy = PolicyStore(tmp_path).load()
        assert policy.configured is False
        assert policy.decide("x", ToolTier.READ) == Decision.ALLOW
        assert policy.decide("x", ToolTier.LOCAL_WRITE) == Decision.ASK
        assert policy.decide("x", ToolTier.FORBIDDEN) == Decision.DENY

    def test_external_tier_cannot_be_allowed_wholesale(self):
        with pytest.raises(ValidationError):
            ToolPolicy(tiers={ToolTier.EXTERNAL: Decision.ALLOW})
        policy = ToolPolicy(overrides={"deploy_build": Decision.ALLOW})
        assert policy.decide("deploy_build", ToolTier.EXTERNAL) == Decision.ALLOW

    def test_forbidden_can_never_be_enabled(self):
        with pytest.raises(ValidationError):
            ToolPolicy(tiers={ToolTier.FORBIDDEN: Decision.ALLOW})
        assert ToolPolicy(overrides={"shell": Decision.ALLOW}).decide("shell", ToolTier.FORBIDDEN) == Decision.DENY

    def test_saved_policy_round_trips_with_private_permissions(self, tmp_path):
        store = PolicyStore(tmp_path)
        store.save(ToolPolicy(configured=True, tiers={ToolTier.LOCAL_WRITE: Decision.ALLOW}))
        loaded = store.load()
        assert loaded.configured and loaded.decide("start_build", ToolTier.LOCAL_WRITE) == Decision.ALLOW
        if hasattr(store.path, "stat") and __import__("os").name == "posix":
            assert store.path.stat().st_mode & 0o077 == 0

    def test_tampered_file_denies_everything_and_is_kept(self, tmp_path):
        store = PolicyStore(tmp_path)
        store.path.write_text(json.dumps({"version": 1, "configured": True,
                                          "tiers": {"external": "allow", "local_write": "allow"}}))
        policy = store.load()
        assert policy.configured is False and store.damaged
        assert all(policy.decide("x", tier) == Decision.DENY for tier in ToolTier)
        assert (tmp_path / "tool_policy.json.bad").exists(), "the damaged file is kept for the user"

    def test_imatrix_without_own_dataset_is_valid_and_rkllm_without_board_uses_rk3588(self, rockchip):
        caps = rockchip
        assert caps.check_request("gguf", "rk3566", "IQ2_XS", imatrix=True) == []
        assert caps.normalize("RKLLM", None, None) == ("rkllm", "rk3588", "w8a8")
        assert caps.check_request("gguf", "RK3588", "int8") == []


class Echo(SimpleNamespace):
    pass


@pytest.fixture
def registry(tmp_path):
    from pydantic import BaseModel

    class In(BaseModel):
        text: str

    class Out(BaseModel):
        text: str

    reg = ToolRegistry(PolicyStore(tmp_path), AuditLog(tmp_path / "audit.db", key=b"k"))
    calls = []
    reg.register(ToolSpec("echo", "Echo", "", ToolTier.READ, In, Out, lambda i: Out(text=i.text)))
    reg.register(ToolSpec("write", "Write", "", ToolTier.LOCAL_WRITE, In, Out,
                          lambda i: calls.append(i.text) or {"text": "done"}))
    reg.calls = calls
    return reg


class TestRegistry:

    def test_read_tools_run_and_are_audited(self, registry):
        assert registry.call("echo", {"text": "hi"}, ToolContext("mcp")).text == "hi"
        entry = registry.audit.entries(1)[0]
        assert (entry["actor"], entry["tool"], entry["outcome"]) == ("mcp", "echo", "ok")

    def test_ask_without_a_way_to_ask_is_denied(self, registry):
        with pytest.raises(ToolDenied):
            registry.call("write", {"text": "x"}, ToolContext("mcp"))
        assert registry.calls == []

    def test_confirmation_is_asked_and_respected(self, registry):
        asked = []
        ctx = ToolContext("ditto", confirm=lambda spec, inp: asked.append(spec.name) or True)
        assert registry.call("write", {"text": "x"}, ctx).text == "done"
        assert asked == ["write"] and registry.calls == ["x"]
        with pytest.raises(ToolDenied):
            registry.call("write", {"text": "y"}, ToolContext("ditto", confirm=lambda s, i: False))

    def test_policy_allow_skips_the_question(self, registry):
        registry.policy_store.replace(ToolPolicy(configured=True, overrides={"write": Decision.ALLOW}))
        assert registry.call("write", {"text": "x"}, ToolContext("mcp")).text == "done"

    def test_invalid_arguments(self, registry):
        with pytest.raises(ToolInputError, match="text"):
            registry.call("echo", {"txt": "x"}, ToolContext("mcp"))
        with pytest.raises(ToolError, match="Unknown tool"):
            registry.call("nope", {}, ToolContext("mcp"))

    def test_forbidden_tools_cannot_be_registered(self, registry):
        with pytest.raises(ValueError):
            registry.register(ToolSpec("shell", "Shell", "", ToolTier.FORBIDDEN, object, object, lambda i: i))

    def test_annotations_follow_the_tier(self, registry):
        assert registry.get("echo").annotations()["readOnlyHint"] is True
        assert registry.get("write").annotations()["readOnlyHint"] is False

    def test_framework_registry_has_the_build_tools(self, booted_framework):
        names = {t.name for t in booted_framework.tool_registry.list()}
        assert {"list_targets", "start_build", "cancel_build", "get_build_status", "resolve_healing"} <= names
        result = booted_framework.tool_registry.call("get_target_capabilities", {"target": "Rockchip"}, ToolContext("cli"))
        assert "gguf" in result.formats


class TestAuditLog:

    def test_chain_detects_tampering(self, tmp_path):
        log = AuditLog(tmp_path / "a.db", key=b"secret")
        for i in range(3):
            log.record("mcp", "start_build", "local_write", "ask", "ok", arguments={"i": i}, approved=True)
        assert log.verify() is None
        with sqlite3.connect(tmp_path / "a.db") as db:
            db.execute("UPDATE events SET outcome='denied' WHERE id=2")
        assert log.verify() == 2

    def test_secrets_in_arguments_are_redacted(self, tmp_path):
        log = AuditLog(tmp_path / "a.db")
        log.record("mcp", "t", "read", "allow", "ok", arguments={"key": "hf_abcdefghijklmnopqrstuvwxyz1234"})
        assert "hf_abcdefghijklmnopqrstuvwxyz1234" not in log.entries(1)[0]["args_preview"]


@pytest.mark.usefixtures("qapp")
class TestBuilderTabCapabilities:

    @pytest.fixture
    def tab(self, booted_framework):
        from orchestrator.gui.builder_tab import BuilderTab
        tab = BuilderTab(booted_framework)
        idx = tab.target_combo.findData("Rockchip")
        assert idx >= 0
        tab.target_combo.setCurrentIndex(idx)
        return tab

    def select(self, combo, data):
        idx = combo.findData(data)
        assert idx >= 0, (data, [combo.itemData(i) for i in range(combo.count())])
        combo.setCurrentIndex(idx)

    def test_npu_format_offers_only_supported_boards_and_types(self, tab):
        self.select(tab.format_combo, "rkllm")
        boards = [tab.board_combo.itemData(i) for i in range(tab.board_combo.count())]
        assert boards == ["rk3588", "rk3576"]
        self.select(tab.board_combo, "rk3588")
        quants = [tab.quant_combo.itemText(i) for i in range(tab.quant_combo.count())]
        assert "w4a16" not in quants and tab.quant_combo.currentText() == "w8a8"
        assert not tab.chk_imatrix.isEnabled()

    def test_gguf_default_follows_the_board(self, tab):
        self.select(tab.format_combo, "gguf")
        self.select(tab.board_combo, "rk3566")
        assert tab.quant_combo.currentText() == "Q4_0"
        assert tab.chk_imatrix.isEnabled()

    def test_invalid_settings_are_refused_before_submitting(self, tab, monkeypatch):
        from orchestrator.gui import builder_tab
        shown = []
        monkeypatch.setattr(builder_tab.QMessageBox, "warning", lambda *a: shown.append(a[2]))
        submitted = []
        monkeypatch.setattr(tab.framework, "run_coroutine", lambda *a, **k: submitted.append(a))
        self.select(tab.format_combo, "gguf")
        tab.quant_combo.setCurrentText("IQ2_XS")
        tab._submit_build()
        assert shown and "importance matrix" in shown[0] and not submitted


@pytest.mark.usefixtures("qapp")
class TestPermissionSetup:

    def test_dialog_saves_a_configured_policy(self, booted_framework):
        from orchestrator.gui.dialogs import ToolPermissionsDialog
        registry = booted_framework.tool_registry
        assert registry.policy().configured is False
        dialog = ToolPermissionsDialog(registry, first_run=True)
        combo = dialog.tier_combos[ToolTier.LOCAL_WRITE]
        combo.setCurrentIndex(combo.findData("allow"))
        external = dialog.tier_combos[ToolTier.EXTERNAL]
        assert external.findData("allow") == -1, "external tools cannot be allowed for the whole category"
        dialog.save()
        saved = registry.policy()
        assert saved.configured and saved.decide("start_build", ToolTier.LOCAL_WRITE) == Decision.ALLOW

    def test_cli_setup_stores_the_answers(self, booted_framework, monkeypatch):
        from click.testing import CliRunner
        from orchestrator import cli as cli_module

        class Ctx(cli_module.FrameworkContext):
            def require_framework(self):
                self.framework_manager = booted_framework
                return self

        monkeypatch.setattr(cli_module, "_interactive", lambda: True)  # a person at a terminal (S4)
        result = CliRunner().invoke(cli_module.cli, ["ditto", "setup"], input="allow\nallow\nask\n", obj=Ctx())
        assert result.exit_code == 0, result.output
        policy = booted_framework.tool_registry.policy()
        assert policy.configured and policy.decide("start_build", ToolTier.LOCAL_WRITE) == Decision.ALLOW
