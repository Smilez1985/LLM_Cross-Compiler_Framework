"""The deploy's trust step goes through its manager (maintainer, 2026-11-24, design "SSH, host keys and the remote
package"): `llmcf deploy run` and the Deployment tab read the board's key with DeploymentManager.host_key_status and
store a key the person trusted with DeploymentManager.trust_host and the channel's PersonChannel, which
pairing.first_use takes as its writer; trust_host ends in trust_presented_key with its audit row.
"""

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_deploy_flow import HOST, Audit, keep_logger_levels  # noqa: F401 - the fixture of deploy run
from test_device_runtime import FakeHostKey

pytest.importorskip("paramiko")


# --- pairing.first_use takes the writer it calls -----------------------------------------------------------------
def test_first_use_stores_a_trusted_key_with_the_writer_it_is_given(tmp_path, monkeypatch):
    from orchestrator.remote import pairing
    from orchestrator.remote.address import Endpoint
    from orchestrator.remote.trust import KeyCheck, TrustStore
    monkeypatch.setattr(pairing, "trust_presented_key", lambda *a, **k: pytest.fail("the writer stores it"))
    written, key = [], FakeHostKey()
    store = TrustStore(tmp_path)
    check = KeyCheck.of(Endpoint(HOST, 2222), key, "unknown")
    outcome = pairing.first_use(store, check, ask=lambda c: True, via="cli",
                                writer=lambda s, endpoint, k, pinned: written.append((s, endpoint, k, pinned)))
    assert outcome.verdict == "trusted" and written == [(store, Endpoint(HOST, 2222), key, False)]
    declined = pairing.first_use(store, check, ask=lambda c: False, via="cli",
                                 writer=lambda *a: pytest.fail("a declined key is not stored"))
    assert declined.verdict == "declined"


# --- `llmcf deploy run` ------------------------------------------------------------------------------------------
class Manager:
    """The deploy's manager as `llmcf deploy run` uses it: reads the key, trusts it, deploys."""

    def __init__(self, status, result):
        self.status, self.result, self.calls = status, result, []

    def host_key_status(self, host, port=22):
        self.calls.append(("key", host, port))
        return self.status, FakeHostKey()

    def trust_host(self, host, key, port=22, *, person=None, pinned=False):
        self.calls.append(("trust", host, port, person, pinned))

    def deploy_package(self, path, host, user, **kw):
        self.calls.append(("deploy", host, user))
        return self.result


def deploy_run(tmp_path, monkeypatch, manager, *args):
    from click.testing import CliRunner
    from orchestrator import cli as cli_module
    from test_cli_builds import quiet_console
    printed = quiet_console(monkeypatch)
    package = tmp_path / "deploy_smol_1.zip"
    package.write_bytes(b"PK")
    ctx = cli_module.FrameworkContext()
    ctx._initialized = True
    ctx.framework_manager = SimpleNamespace(
        config=SimpleNamespace(get=lambda key, default=None: default, user_state_dir=str(tmp_path / "state")),
        resolve_path=lambda p: tmp_path / p, tool_registry=SimpleNamespace(audit=Audit()))
    ctx.deployment_manager = manager
    out = CliRunner().invoke(cli_module.cli, ["-q", "deploy", "run", str(package), "--host", HOST, "--user", "radxa",
                                              "--port", "2222", *args], obj=ctx)
    return out.exit_code, printed.getvalue() + out.output


@pytest.mark.usefixtures("keep_logger_levels")
def test_deploy_run_trusts_a_new_key_through_the_managers_trust_host(tmp_path, monkeypatch):
    from orchestrator.Core.deployment_manager import DeployResult
    from orchestrator.utils import ssh_trust
    manager = Manager("unknown", DeployResult(True, exit_status=0, device_state="ok"))
    code, out = deploy_run(tmp_path, monkeypatch, manager, "--trust-fingerprint", ssh_trust.fingerprint(FakeHostKey()))
    assert code == 0, out
    (read, trust, deploy) = manager.calls
    assert read == ("key", HOST, 2222) and deploy[0] == "deploy"
    _kind, host, port, person, pinned = trust
    assert (host, port, pinned) == (HOST, 2222, False)
    assert (person.via, person.actor) == ("cli", "cli") and callable(person.audit), "the CLI's PersonChannel"


@pytest.mark.usefixtures("keep_logger_levels")
def test_deploy_run_trusts_nothing_without_the_persons_yes(tmp_path, monkeypatch):
    from orchestrator.Core.deployment_manager import DeployResult
    manager = Manager("unknown", DeployResult(True))
    code, out = deploy_run(tmp_path, monkeypatch, manager)   # no terminal, no --trust-fingerprint
    assert code == 1 and "nothing was deployed" in out
    assert [c[0] for c in manager.calls] == ["key"]


@pytest.mark.usefixtures("keep_logger_levels")
def test_a_known_key_is_not_trusted_again(tmp_path, monkeypatch):
    from orchestrator.Core.deployment_manager import DeployResult
    manager = Manager("known", DeployResult(True, exit_status=0, device_state="ok"))
    code, out = deploy_run(tmp_path, monkeypatch, manager)
    assert code == 0, out
    assert [c[0] for c in manager.calls] == ["key", "deploy"]


def test_the_real_manager_stores_the_key_and_writes_the_audit_row(tmp_path):
    """The writer deploy run hands to first_use, on the real DeploymentManager: known_hosts and the trust's row."""
    import paramiko
    from orchestrator import cli as cli_module
    from orchestrator.Core.deployment_manager import DeploymentManager
    from orchestrator.remote import pairing
    from orchestrator.remote.address import Endpoint
    from orchestrator.remote.trust import KeyCheck
    state, key, audit = tmp_path / "state", paramiko.ECDSAKey.generate(), Audit()
    manager = DeploymentManager(SimpleNamespace(config=SimpleNamespace(user_state_dir=str(state)),
                                                tool_registry=SimpleNamespace(audit=audit)))
    ctx = cli_module.FrameworkContext()
    ctx._initialized = True
    ctx.framework_manager = manager.framework
    store = manager.trust_store()
    writer = cli_module._deploy_trust_writer(ctx, manager, store)
    pairing.first_use(store, KeyCheck.of(Endpoint(HOST, 2222), key, "unknown"), ask=lambda c: True, via="cli",
                      writer=writer)
    assert store.status(Endpoint(HOST, 2222), key) == "known"
    (row,) = audit.rows
    assert (row["actor"], row["tool"]) == ("cli", "host_key_trusted")
    assert row["extra"] == {"confirm_channel": "framework-cli", "answer": "accepted"}


# --- the Deployment tab ------------------------------------------------------------------------------------------
def test_the_deployment_tab_reads_and_trusts_through_its_manager(qapp, tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from orchestrator.gui import host_key, plain
    calls = []

    class GuiManager:
        def host_key_status(self, host, port=22):
            calls.append(("key", host, port, threading.current_thread() is threading.main_thread()))
            return "unknown", FakeHostKey()

        def trust_host(self, host, key, port=22, *, person=None, pinned=False):
            calls.append(("trust", host, port, person.via, person.actor, pinned))

    monkeypatch.setattr(host_key, "read_host_key", lambda *a: pytest.fail("the manager reads the key"))
    monkeypatch.setattr(plain, "ask", lambda parent, title, text: True)   # the person's "Yes"
    framework = SimpleNamespace(config=SimpleNamespace(user_state_dir=str(tmp_path / "state")))
    check = host_key.HostKeyCheck(None, framework, manager=lambda: GuiManager())
    result = []
    check.start(HOST, 2222, result.append, step="deploy")
    deadline = time.monotonic() + 10
    while not result and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert result == [True]
    assert calls == [("key", HOST, 2222, False), ("trust", HOST, 2222, "gui", "gui", False)]
    assert check.key is not None


def test_without_a_manager_the_window_reads_the_key_itself(qapp, tmp_path, monkeypatch):
    """The builder's probe has no deploy manager: the plain reader, first_use's own writer."""
    pytest.importorskip("PySide6")
    from orchestrator.gui import host_key
    read = []
    monkeypatch.setattr(host_key, "read_host_key", lambda folder, host, port: read.append((host, port)) or (
        FakeHostKey(), "known"))
    check = host_key.HostKeyCheck(None, SimpleNamespace(config=SimpleNamespace(user_state_dir=str(tmp_path))))
    result = []
    check.start(HOST, 22, result.append, step="probe")
    deadline = time.monotonic() + 10
    while not result and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert result == [True] and read == [(HOST, 22)]


def test_the_deployment_tab_gives_its_manager():
    source = Path(__file__).resolve().parent.parent.joinpath("orchestrator/gui/deployment_window.py").read_text(
        encoding="utf-8")
    assert "HostKeyCheck(self, self.framework, manager=self._deployment_manager)" in source
