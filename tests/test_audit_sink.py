"""One audit sink for the person's board decisions (DECISIONS round 25 Q3, consolidation stage C wave 3c-2, chain
boards).

The CLI, the GUI (the Deployment tab, the Boards window, the remote hardware scan) and the person's consents behind the
board service (service.benchmark's `consent`, steps.choose_bench_model's `record_consent`) write their device, trust and
consent rows through the same function, person_steps.device_audit: the registry's log when there is one, else the
per-user audit.db opened with its key. So the Boards window's benchmark consent works without a registry, as in the
CLI. Each surface writes each row exactly once: into the registry's log when there is one (no audit.db then), else
into audit.db.

The AI's own consent row (tools_runtime run_device_benchmark) is written by the tool to the registry it runs in: an AI
path always has one, which is what device_audit takes first, and no module an AI can reach may name device_audit
(tests/test_deploy_flow.py PERSON_ONLY_NAMES).
"""

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import REPO_ROOT

DEVICE = "0123456789abcdef"


class Audit:
    """A registry's audit log stand-in that keeps its rows."""

    def __init__(self):
        self.rows = []

    def record(self, actor, tool, tier, decision, outcome, **kwargs):
        self.rows.append(dict(actor=actor, tool=tool, outcome=outcome, **kwargs))


def per_user_rows(state: Path):
    """The rows of the per-user audit.db in `state` (opened as device_audit opens it); [] when there is none."""
    from orchestrator.Core.vault_key import audit_key
    from orchestrator.tools.audit import AuditLog
    if not (state / "audit.db").exists():
        return []
    return AuditLog(state / "audit.db", key=audit_key(None, state)).entries(50)


def bare(state: Path):
    """A framework without a tool registry (a degraded boot, or none at all)."""
    return SimpleNamespace(config=SimpleNamespace(user_state_dir=str(state)))


def booted(state: Path):
    return SimpleNamespace(config=SimpleNamespace(user_state_dir=str(state)),
                           tool_registry=SimpleNamespace(audit=Audit()))


@pytest.fixture
def window_framework(make_framework, tmp_path):
    from test_input_history import framework as make_window_framework
    return make_window_framework.__wrapped__(make_framework, tmp_path)


# --- the one function -------------------------------------------------------------------------------------------------

def test_the_registry_s_log_comes_first_else_the_per_user_audit_db(tmp_path, memory_keyring):
    from orchestrator.runtime.device.person_steps import device_audit
    from orchestrator.tools.audit import AuditLog
    framework = booted(tmp_path)
    assert device_audit(framework, tmp_path) is framework.tool_registry.audit
    log = device_audit(bare(tmp_path), tmp_path)
    assert isinstance(log, AuditLog) and log.path == tmp_path / "audit.db" and log.keyed
    assert not (tmp_path / "audit.db").exists(), "opening it writes nothing yet"


def test_every_person_surface_opens_the_one_sink():
    """No surface of the person's board steps reads the registry's log on its own any more (that read had no per-user
    fallback): each goes through person_steps.device_audit (the CLI through its _device_audit)."""
    surfaces = {"orchestrator/gui/boards_window.py": "device_audit", "orchestrator/gui/deployment_window.py":
                "device_audit", "orchestrator/gui/builder_tab.py": "device_audit", "orchestrator/gui/host_key.py":
                "device_audit"}
    for name, opener in surfaces.items():
        source = (REPO_ROOT / name).read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
            a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
        assert opener in names, f"{name} does not open the one sink"
        own = [ast.unparse(n) for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") ==
               "getattr" and len(n.args) >= 2 and isinstance(n.args[1], ast.Constant) and n.args[1].value == "audit"
               and "tool_registry" in ast.unparse(n.args[0])]
        assert not own, f"{name} reads the registry's log on its own: {own}"
    cli = (REPO_ROOT / "orchestrator/cli.py").read_text(encoding="utf-8")
    consent = cli.split("def _record_consent(", 1)[1].split("\ndef ", 1)[0]
    assert "_device_audit(" in consent and "_tool_registry(" not in consent


# --- the CLI ----------------------------------------------------------------------------------------------------------

def test_a_cli_consent_needs_no_booted_framework_and_is_written_once(tmp_path, monkeypatch, memory_keyring):
    """`llmcf device settings` (the sudo yes) and `device runtime verify --bench-model` (the untrusted model) wrote
    their consent through the registry, booting the framework for it; now through the one sink: audit.db when the
    framework is not booted, the registry's log when it is; one row either way."""
    from orchestrator import cli as cli_module
    state = tmp_path / "state"
    state.mkdir()

    def refuse_boot():
        raise AssertionError("a consent boots nothing")

    ctx = SimpleNamespace(_initialized=False, framework_manager=None, require_framework=refuse_boot)
    monkeypatch.setattr(cli_module, "_state_dir", lambda c: state)
    cli_module._record_consent(ctx, "device_runtime_systemd", {"device": DEVICE}, "sudo -n allowed")
    rows = per_user_rows(state)
    assert [r["tool"] for r in rows] == ["device_runtime_systemd"] and rows[0]["actor"] == "user"
    extra = rows[0]["extra"] if isinstance(rows[0]["extra"], dict) else json.loads(rows[0]["extra"])
    assert extra["confirm_channel"] == "framework-cli"
    other = tmp_path / "other"
    other.mkdir()
    framework = booted(other)
    ctx = SimpleNamespace(_initialized=True, framework_manager=framework, require_framework=refuse_boot)
    monkeypatch.setattr(cli_module, "_state_dir", lambda c: other)
    cli_module._record_consent(ctx, "device_runtime_untrusted_model", {"device": DEVICE, "model_sha256": "d" * 64},
                               "an untrusted model may run")
    assert [r["tool"] for r in framework.tool_registry.audit.rows] == ["device_runtime_untrusted_model"]
    assert not (other / "audit.db").exists(), "the registry's log takes the row; no second log"


# --- the GUI ----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("registry", [False, True])
def test_the_boards_window_writes_its_sudo_consent_once_also_without_a_registry(qapp, tmp_path, monkeypatch,
                                                                              memory_keyring, registry):
    pytest.importorskip("PySide6")
    from test_boards_window import Boxes, stored

    from orchestrator.gui import boards_window
    from orchestrator.gui.boards_window import BoardsWindow
    from orchestrator.tools.audit import AuditLog
    state = tmp_path / "state"
    state.mkdir()
    framework = booted(state) if registry else bare(state)
    window = BoardsWindow(framework, lang="en")
    try:
        log = window.audit
        if registry:
            assert log is framework.tool_registry.audit
        else:
            assert isinstance(log, AuditLog) and log.path == state / "audit.db"
        stored(state, ssh_user="dietpi", model="models/q.gguf")
        window.reload()
        window.select(DEVICE)

        def fill(dialog):
            from PySide6.QtWidgets import QComboBox
            install = dialog.findChild(QComboBox, "cmb_boards_install")
            install.setCurrentIndex(install.findData("systemd"))
            return True

        monkeypatch.setattr(boards_window, "_exec", fill)
        Boxes(monkeypatch, answer="yes")
        window.buttons["btn_boards_settings"].click()
        tools = [r["tool"] for r in (framework.tool_registry.audit.rows if registry else per_user_rows(state))]
        assert tools == ["device_runtime_systemd"], tools
        if registry:
            assert not (state / "audit.db").exists()
    finally:
        window.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("registry", [False, True])
def test_the_deployment_tab_s_bench_consent_is_written_once_also_without_a_registry(tmp_path, memory_keyring,
                                                                                   registry):
    pytest.importorskip("PySide6")
    from orchestrator.gui.deployment_window import BoardBenchWorker
    state = tmp_path / "state"
    state.mkdir()
    framework = booted(state) if registry else bare(state)
    worker = BoardBenchWorker(framework, DEVICE, "models/own.gguf", True)
    worker._consent("ab" * 32)
    rows = framework.tool_registry.audit.rows if registry else per_user_rows(state)
    assert [r["tool"] for r in rows] == ["device_runtime_untrusted_model"] and rows[0]["actor"] == "user"
    if registry:
        assert rows[0]["arguments"] == {"device": DEVICE, "model_sha256": "ab" * 32}
        assert not (state / "audit.db").exists()


@pytest.mark.parametrize("registry", [False, True])
def test_the_remote_hardware_scan_records_the_board_in_the_one_sink(window_framework, tmp_path, monkeypatch,
                                                                    registry):
    """The Builder tab's remote scan wrote the board's trusted record with the registry's log only; `scan --device`
    audits it in the per-user audit.db without one. The scan's worker now gets the one sink's opener (called only when
    the record is written)."""
    pytest.importorskip("PySide6")
    from orchestrator.gui import builder_tab
    from orchestrator.tools.audit import AuditLog
    state = tmp_path / "scan-state"
    state.mkdir()
    framework = booted(state) if registry else bare(state)
    workers = []

    class Dialog:
        def __init__(self, parent=None, recent=()):
            pass

        def exec(self):
            return 1

        def get_data(self):
            return "192.0.2.95", "radxa", None

        def get_options(self):
            return 22, "Shed board"

    tab = builder_tab.BuilderTab(window_framework)
    tab._build_timer.stop()
    tab.framework = framework   # the scan's own framework: with or without a registry
    monkeypatch.setattr(builder_tab, "SSHConnectDialog", Dialog)
    monkeypatch.setattr(builder_tab, "SSHProbeWorker", lambda *a, **k: workers.append((a, k)) or SimpleNamespace(
        finished=SimpleNamespace(connect=lambda slot: None), line=SimpleNamespace(connect=lambda slot: None),
        start=lambda: None))
    monkeypatch.setattr(tab.host_keys, "start", lambda host, port, then, step="": then(True))
    tab._open_ssh_dialog()
    ((_args, kwargs),) = workers
    opened = kwargs["audit_factory"]()
    if registry:
        assert opened is framework.tool_registry.audit
    else:
        assert isinstance(opened, AuditLog) and opened.path == state / "audit.db"
