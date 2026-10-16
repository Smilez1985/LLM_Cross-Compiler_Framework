"""
The board steps the CLI and the GUI share (W1' of 2026-10-16: every action of the CLI also by mouse, through the same
core function): orchestrator/runtime/device/person_steps.py (the person's own steps: trusted boards, stored boards,
deploy settings with the sudo consent, the login to a stored board, older builds on a board) and
orchestrator/runtime/device/steps.py (the runtime steps every caller shares: plan, status, build, the short
benchmark's model, installing and checking on the board).

No network, no Docker, no real board: SSH, the build executor and the board are stand-ins.
"""

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.runtime.build.verify import Check, VerifyReport
from orchestrator.runtime.device import person_steps, steps
from orchestrator.runtime.device.settings import DeploySettings, SettingsError
from orchestrator.runtime.profile import ProfileStore
from test_device_runtime import (DEVUAN, HOST, LICENCE, PIN, POSIX, RK3566, BoardAudit, FakeDevice,
                                 FakeDeviceExecutor, FakeHostKey, FormatOnlyVerifier, board_builds, plan_for)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def source(tmp_path):
    """The pinned source as git checks it out (its LICENSE travels in the tarball)."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "CMakeLists.txt").write_text("project(llama.cpp)\n")
    (src / "LICENSE").write_bytes(LICENCE.encode("utf-8"))
    return src


@pytest.fixture
def device_base(tmp_path_factory):
    return tmp_path_factory.mktemp("board")


class Audit:
    def __init__(self, fail=False):
        self.rows, self.fail = [], fail

    def record(self, actor, tool, tier, decision, outcome, **kwargs):
        if self.fail:
            raise OSError("audit log full")
        self.rows.append(dict(actor=actor, tool=tool, decision=decision, outcome=outcome, **kwargs))


def device_id_of(key):
    from orchestrator.runtime.profile import device_id_for_host_key
    from orchestrator.utils import ssh_trust
    return device_id_for_host_key(ssh_trust.fingerprint(key))


def trusted(state, key=None, label="Board", user="dietpi", paired=True):
    from orchestrator.runtime.device.trusted import TrustedDevices
    from orchestrator.utils import ssh_trust
    key = key or FakeHostKey()
    ssh_trust.trust_host_key(state, "192.168.1.50", 22, key)
    return TrustedDevices(state).record("192.168.1.50", 22, user, key, via="cli", paired=paired, label=label)


@pytest.fixture
def paramiko_key():
    paramiko = pytest.importorskip("paramiko")
    return paramiko.ECDSAKey.generate()


# --- the core never reaches an AI -------------------------------------------------------------------------------

def test_no_ai_reachable_code_imports_the_persons_board_steps():
    """Trusting and removing boards, the password login, the sudo consent and the removal of builds from a board are
    the person's: no module an AI can reach imports person_steps (the shared steps.py may be used by every caller,
    it trusts, pairs and removes nothing)."""
    from test_deploy_tools import AI_REACHABLE, WRITERS
    for entry in AI_REACHABLE:
        path = ROOT / entry
        for file in ([path] if path.is_file() else sorted(path.rglob("*.py"))):
            text = file.read_text(encoding="utf-8")
            assert "person_steps" not in text, f"{file.relative_to(ROOT)} reaches the person's board steps"
    tree = ast.parse((ROOT / "orchestrator/runtime/device/steps.py").read_text(encoding="utf-8"))
    names = {getattr(n.func, "attr", getattr(n.func, "id", "")) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert not names & (WRITERS | {"remove_older_builds", "remove", "connect"})


# --- trusted boards ------------------------------------------------------------------------------------------------

class TestTrustedBoards:

    def test_the_rows_are_the_ones_the_cli_lists(self, tmp_path, monkeypatch, paramiko_key):
        from click.testing import CliRunner
        from orchestrator import cli as cli_module
        state = tmp_path / "state"
        device = trusted(state, paramiko_key)
        (row,) = person_steps.trusted_rows(state)
        assert row["device_id"] == device.device_id and row["user"] == "dietpi" and row["host_key"] == "known"
        assert row["fingerprint"] == device.fingerprint and row["trusted"].endswith("(cli)")
        monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(state))
        out = CliRunner().invoke(cli_module.cli, ["-q", "deploy", "devices", "list", "--json"])
        assert json.loads(out.output) == [row]

    def test_the_cli_list_names_only_a_deploy_as_the_last_deploy(self, tmp_path, monkeypatch, paramiko_key):
        """A board whose only bookkeeping is a recorded contact (a failed verify for Mojo) shows "-" as its last
        deploy, never "None (failed)" (review of wave 5a)."""
        from click.testing import CliRunner
        from orchestrator import cli as cli_module
        from orchestrator.runtime.device import reasons
        from orchestrator.runtime.device.trusted import TrustedDevices
        state = tmp_path / "state"
        device = trusted(state, paramiko_key)
        TrustedDevices(state).note_reach(device.device_id, "verify", reasons.HOST_DOWN)
        monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(state))
        monkeypatch.setenv("COLUMNS", "400")
        out = CliRunner().invoke(cli_module.cli, ["-q", "deploy", "devices", "list"])
        assert out.exit_code == 0, out.output
        assert "None" not in out.output and "(failed)" not in out.output
        TrustedDevices(state).note_deploy(device.device_id, False, "pkg")
        out = CliRunner().invoke(cli_module.cli, ["-q", "deploy", "devices", "list"])
        assert "(failed)" in out.output and "None" not in out.output

    def test_removing_trust_is_audited_with_the_persons_channel(self, tmp_path, paramiko_key):
        from orchestrator.runtime.device.trusted import TrustedDevices
        state = tmp_path / "state"
        device = trusted(state, paramiko_key)
        opened = []
        assert person_steps.remove_trust(state, "0" * 16, audit=lambda: opened.append(1), actor="gui",
                                         channel="framework-gui") is False
        assert opened == [], "the audit log is opened only after a record was removed"
        audit = Audit()
        assert person_steps.remove_trust(state, f" {device.device_id} ", audit=lambda: audit, actor="gui",
                                         channel="framework-gui")
        assert TrustedDevices(state).list() == []
        (row,) = audit.rows
        assert row["actor"] == "gui" and row["tool"] == "device_removed" and row["approved"] is True
        assert row["arguments"] == {"device_id": device.device_id}
        assert row["extra"] == {"confirm_channel": "framework-gui", "answer": "accepted"}

    def test_a_failing_audit_log_never_undoes_the_removal(self, tmp_path, paramiko_key):
        state = tmp_path / "state"
        device = trusted(state, paramiko_key)
        assert person_steps.remove_trust(state, device.device_id, audit=Audit(fail=True), actor="cli",
                                         channel="framework-cli")

    @pytest.mark.parametrize("host, user, label, kind", [
        ("-oProxyCommand=x", "dietpi", None, "host_user"), ("192.168.1.50", "bad user", None, "host_user"),
        ("192.168.1.50", "dietpi", "", "label"), ("192.168.1.50", "dietpi", "x" * 65, "label")])
    def test_the_entry_is_checked_as_in_the_cli(self, host, user, label, kind):
        with pytest.raises(person_steps.BoardStepError) as e:
            person_steps.check_board_entry(host, user, label)
        assert e.value.kind == kind
        assert person_steps.check_board_entry("myboard.local", "dietpi", "  Living   room ") == "Living room"
        assert person_steps.check_board_entry("fe80::1", "dietpi", None) is None

    def test_reading_the_host_key_names_the_reason_when_the_board_does_not_answer(self, tmp_path, monkeypatch):
        from orchestrator.runtime.device import reasons
        from orchestrator.utils import ssh_trust
        monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port: (_ for _ in ()).throw(
            ConnectionRefusedError("refused")))
        with pytest.raises(person_steps.BoardStepError) as e:
            person_steps.read_host_key(tmp_path, "192.168.1.50", 22)
        assert e.value.kind == "unreachable" and e.value.reason in reasons.REASONS
        monkeypatch.setattr(ssh_trust, "paramiko", None)
        with pytest.raises(person_steps.BoardStepError) as e:
            person_steps.read_host_key(tmp_path, "192.168.1.50", 22)
        assert e.value.kind == "no_paramiko"

    @pytest.mark.parametrize("logs_in", [True, False])
    def test_adding_records_whether_the_framework_key_logs_in(self, tmp_path, monkeypatch, paramiko_key, logs_in):
        from orchestrator.utils import ssh_trust
        state = tmp_path / "state"
        ssh_trust.trust_host_key(state, "192.168.1.50", 2222, paramiko_key)
        tried = []

        class Client:
            def close(self):
                tried.append("closed")

        def connect(host, user, state_dir, port=22, password=None):
            tried.append((host, user, port, password))
            if not logs_in:
                raise OSError("no key")
            return Client()

        monkeypatch.setattr(ssh_trust, "connect", connect)
        audit = Audit()
        device, paired = person_steps.add_trusted_board(state, "192.168.1.50", 2222, "dietpi", paramiko_key,
                                                        label="Shelf", via="gui", audit=audit, actor="gui")
        assert paired is logs_in and device.paired is logs_in and device.label == "Shelf"
        assert device.trusted_via == "gui" and device.port == 2222
        assert tried[0] == ("192.168.1.50", "dietpi", 2222, None), "no password is ever used here"
        assert audit.rows[0]["tool"] == "device_trusted" and audit.rows[0]["actor"] == "gui"
        assert audit.rows[0]["extra"]["confirm_channel"] == "framework-gui"

    def test_the_refusal_for_a_key_nobody_trusted(self):
        e = person_steps.not_known_error("192.168.1.50", 22, "unknown")
        assert e.kind == "not_known" and "is not trusted yet (its host key is unknown)" in str(e)


# --- stored boards -------------------------------------------------------------------------------------------------

class TestStoredBoards:

    def test_the_rows_are_the_ones_the_cli_lists(self, tmp_path, monkeypatch):
        from click.testing import CliRunner
        from orchestrator import cli as cli_module
        state = tmp_path / "state"
        store = ProfileStore(state)
        store.save_device("0123456789abcdef", RK3566, label="ZERO 3W", address="192.0.2.10")
        store.save_device("fedcba9876543210", DEVUAN, label="rock5", address="192.0.2.11")
        store.save_deploy_settings("fedcba9876543210", {"install": "bogus"})
        rows = person_steps.device_rows(state)
        assert [r["label"] for r in rows] == ["ZERO 3W", "rock5"]
        assert rows[0]["system"]["INIT_SYSTEM"] == "systemd" and rows[0]["deploy"] == {"install": "user", "port": 8080}
        assert rows[1]["deploy"] == {"problem": "damaged; set them again"} and rows[1]["deployed"] is None
        monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(state))
        out = CliRunner().invoke(cli_module.cli, ["-q", "device", "list", "--json"])
        assert json.loads(out.output) == rows

    def test_the_overview_merges_stored_and_trusted_boards_by_device_id(self, tmp_path, paramiko_key):
        state = tmp_path / "state"
        device = trusted(state, paramiko_key, label="Living room")
        store = ProfileStore(state)
        store.save_device(device.device_id, RK3566, label="probe label", address="192.168.1.50")
        store.save_device("0123456789abcdef", DEVUAN, label="Attic", address="192.0.2.11")
        overview = person_steps.boards_overview(state)
        assert [e["label"] for e in overview] == ["Attic", "Living room"], "the person's own name wins"
        attic, living = overview
        assert attic["trusted"] is None and attic["stored"]["device_id"] == "0123456789abcdef"
        assert living["trusted"]["user"] == "dietpi" and living["stored"]["label"] == "probe label"


# --- deploy settings -----------------------------------------------------------------------------------------------

class TestSettings:

    def stored(self, tmp_path, profile=RK3566):
        store = ProfileStore(tmp_path)
        store.save_device("0123456789abcdef", profile, label="ZERO 3W", address="192.0.2.10")
        return store

    def change(self, store, profile, changes, sudo=True, consent=None):
        asked = []

        def may():
            asked.append("asked")
            return sudo

        new = person_steps.change_settings(store, "0123456789abcdef", profile,
                                           person_steps.load_settings(store, "0123456789abcdef"), changes,
                                           may_use_sudo=may, record_sudo_consent=consent or (lambda: None),
                                           stamp=lambda: "2026-10-16T10:00:00Z")
        return new, asked

    def test_the_systemd_unit_needs_systemd_and_the_persons_yes_written_first(self, tmp_path):
        store = self.stored(tmp_path)
        consents = []
        new, asked = self.change(store, RK3566, {"install": "systemd", "model": "models/q.gguf", "ssh_user": "dietpi"},
                                 consent=lambda: consents.append("written"))
        assert asked == ["asked"] and consents == ["written"]
        assert new.install == "systemd" and new.sudo_confirmed_at == "2026-10-16T10:00:00Z"
        assert person_steps.load_settings(store, "0123456789abcdef") == new
        again, asked = self.change(store, RK3566, {"port": 9090})
        assert asked == [] and again.sudo_confirmed_at == new.sudo_confirmed_at, "a confirmed unit is not asked again"

    def test_no_or_a_consent_that_cannot_be_written_changes_nothing(self, tmp_path):
        store = self.stored(tmp_path)
        with pytest.raises(person_steps.BoardStepError) as e:
            self.change(store, RK3566, {"install": "systemd"}, sudo=False)
        assert e.value.kind == "sudo_declined"

        def broken():
            raise RuntimeError("no audit log")

        with pytest.raises(RuntimeError):
            self.change(store, RK3566, {"install": "systemd"}, consent=broken)
        assert store.load_deploy_settings("0123456789abcdef") == {}

    def test_a_board_without_systemd_is_refused_before_the_question(self, tmp_path):
        store = self.stored(tmp_path, DEVUAN)
        with pytest.raises(person_steps.BoardStepError) as e:
            self.change(store, DEVUAN, {"install": "systemd"})
        assert e.value.kind == "no_systemd" and "sysvinit" in str(e.value)
        assert store.load_deploy_settings("0123456789abcdef") == {}

    def test_values_that_do_not_pass_and_a_save_that_fails(self, tmp_path, monkeypatch):
        store = self.stored(tmp_path)
        for changes in ({"ssh_user": "root"}, {"port": 80}, {"model": "../x"}, {"kv_cache": "q2"}):
            with pytest.raises(SettingsError):
                self.change(store, RK3566, changes)
        new, _ = self.change(store, RK3566, {"kv_cache": "q8_0"})
        assert new.kv_cache == "q8_0"
        new, _ = self.change(store, RK3566, {"kv_cache": None})
        assert new.kv_cache is None, "automatic again"
        monkeypatch.setattr(store, "save_deploy_settings", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
        with pytest.raises(person_steps.BoardStepError) as e:
            self.change(store, RK3566, {"port": 9090})
        assert e.value.kind == "not_saved" and "disk full" in str(e.value)

    def test_a_consent_is_written_with_its_channel_or_not_at_all(self):
        with pytest.raises(RuntimeError):
            person_steps.record_consent(None, *person_steps.SYSTEMD_CONSENT[:1], {"device": "x"},
                                        person_steps.SYSTEMD_CONSENT[1], channel="framework-gui")
        audit = Audit()
        tool, detail = person_steps.UNTRUSTED_CONSENT
        person_steps.record_consent(audit, tool, {"device": "x", "model_sha256": "a" * 64}, detail,
                                    channel="framework-gui")
        (row,) = audit.rows
        assert row["actor"] == "user" and row["tool"] == "device_runtime_untrusted_model" and row["approved"]
        assert row["extra"] == {"answer": "accepted", "confirm_channel": "framework-gui"}

    def test_the_cli_asks_the_same_sudo_question(self):
        import inspect
        from orchestrator import cli as cli_module
        assert "person_steps.SUDO_QUESTION" in inspect.getsource(cli_module.device_settings.callback)

    def test_the_cli_asks_the_shared_untrusted_model_question(self, monkeypatch):
        """The CLI and the Boards window ask the same words before an untrusted model runs on the board
        (person_steps.UNTRUSTED_QUESTION; review of wave 5a)."""
        import orchestrator.runtime.device.remote as remote
        from orchestrator import cli as cli_module
        asked = []
        monkeypatch.setattr(remote, "model_sha256", lambda shell, path: "d" * 64)
        monkeypatch.setattr(steps, "catalog_model_digests", lambda: set())
        monkeypatch.setattr(cli_module, "_interactive", lambda: True)
        monkeypatch.setattr(cli_module.click, "confirm", lambda text, default=None: asked.append((text, default))
                            or False)
        assert cli_module._bench_choice(None, None, "/home/u", "models/own.gguf", False, "0" * 16, "dietpi") == (
            "/home/u/models/own.gguf", False, "d" * 64)
        assert asked == [(person_steps.UNTRUSTED_QUESTION.format(path="/home/u/models/own.gguf", user="dietpi"),
                          False)]


# --- the login to a stored board -------------------------------------------------------------------------------

class TestConnect:

    def fake(self, monkeypatch, status="known", key=None, login=None):
        from orchestrator.utils import ssh_trust
        seen = []

        class Client:
            def close(self):
                seen.append("closed")

        def connect(host, user, state, port=22, password=None):
            seen.append((host, user, port, password))
            if login is not None:
                raise login
            return Client()

        monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port: key or FakeHostKey())
        monkeypatch.setattr(ssh_trust, "check_host_key", lambda state, host, port, k: status)
        monkeypatch.setattr(ssh_trust, "connect", connect)
        monkeypatch.setattr(ssh_trust, "install_public_key", lambda client, public: seen.append("key installed"))
        monkeypatch.setattr(ssh_trust, "client_key", lambda state: (None, "ssh-ed25519 AAAA framework"))
        return seen

    def notes(self, monkeypatch):
        from orchestrator.runtime.device.trusted import TrustedDevices
        noted = []
        monkeypatch.setattr(TrustedDevices, "note_reach", lambda self, d, step, code: noted.append((d, step, code)))
        return noted

    def test_a_login_with_the_trusted_key_and_a_password_used_once(self, tmp_path, monkeypatch):
        seen, noted = self.fake(monkeypatch), self.notes(monkeypatch)
        device_id = device_id_of(FakeHostKey())
        handed = []
        client, key = person_steps.connect_stored(tmp_path, device_id, "192.0.2.10", 22, "dietpi", "secret",
                                                  step="runtime_deploy", on_client=handed.append)
        assert handed == [client] and isinstance(key, FakeHostKey)
        assert seen == [("192.0.2.10", "dietpi", 22, "secret"), "key installed"]
        assert noted == [(device_id, "runtime_deploy", "")], "a recorded problem of the board ends"

    @pytest.mark.parametrize("status, kind", [("changed", "host_key_changed"), ("unknown", "not_trusted")])
    def test_a_key_that_is_not_the_trusted_one_is_refused_before_any_login(self, tmp_path, monkeypatch, status,
                                                                           kind):
        seen, noted = self.fake(monkeypatch, status=status), self.notes(monkeypatch)
        device_id = device_id_of(FakeHostKey())
        with pytest.raises(person_steps.BoardStepError) as e:
            person_steps.connect_stored(tmp_path, device_id, "192.0.2.10", 22, "dietpi", step="verify")
        assert e.value.kind == kind and seen == []
        assert noted == ([(device_id, "verify", "device_host_key_changed")] if status == "changed" else [])

    def test_another_machine_at_the_stored_address_is_refused(self, tmp_path, monkeypatch):
        seen = self.fake(monkeypatch)
        with pytest.raises(person_steps.BoardStepError) as e:
            person_steps.connect_stored(tmp_path, "0" * 16, "192.0.2.10", 22, "dietpi")
        assert e.value.kind == "other_device" and seen == []

    def test_a_refused_login_keeps_its_reason_for_mojo(self, tmp_path, monkeypatch):
        paramiko = pytest.importorskip("paramiko")
        from orchestrator.runtime.device import reasons
        self.fake(monkeypatch, login=paramiko.AuthenticationException("no"))
        noted = self.notes(monkeypatch)
        device_id = device_id_of(FakeHostKey())
        with pytest.raises(person_steps.BoardStepError) as e:
            person_steps.connect_stored(tmp_path, device_id, "192.0.2.10", 22, "dietpi", step="verify")
        assert e.value.kind == "login_failed" and e.value.reason == reasons.AUTH_FAILED
        assert noted == [(device_id, "verify", reasons.AUTH_FAILED)]

    def test_a_board_that_does_not_answer(self, tmp_path, monkeypatch):
        from orchestrator.runtime.device import reasons
        from orchestrator.utils import ssh_trust
        self.fake(monkeypatch)
        noted = self.notes(monkeypatch)
        monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port: (_ for _ in ()).throw(
            TimeoutError("timed out")))
        with pytest.raises(person_steps.BoardStepError) as e:
            person_steps.connect_stored(tmp_path, "0" * 16, "192.0.2.10", 22, "dietpi", step="runtime_deploy")
        assert e.value.kind == "unreachable" and e.value.reason in reasons.REASONS
        assert noted == [("0" * 16, "runtime_deploy", e.value.reason)]

    def test_the_stored_address(self, tmp_path):
        store = ProfileStore(tmp_path)
        store.save_device("0123456789abcdef", RK3566, label="x", address="[fe80::1]:2222")
        assert person_steps.board_address(store, "0123456789abcdef") == ("fe80::1", 2222)
        store.save_device("fedcba9876543210", RK3566, label="y")
        with pytest.raises(ValueError):
            person_steps.board_address(store, "fedcba9876543210")


@POSIX
class TestOlderBuildsOnTheBoard:
    A, B, C = "a" * 16, "b" * 16, "c" * 16

    def test_the_list_and_the_removal_of_exactly_the_ticked_builds(self, tmp_path, device_base):
        from orchestrator.Core.deletion import accept, choose
        from orchestrator.runtime.device.package import DeviceRuntimeStore
        from orchestrator.runtime.device.remote import RemoteShell
        dev = FakeDevice(device_base)
        board_builds(dev, self.A, self.B, self.C, current=self.C)
        DeviceRuntimeStore(tmp_path, "0123456789abcdef").save_record({"build_id": self.C * 4, "previous": self.B})
        prune = person_steps.list_board_builds(RemoteShell(dev), tmp_path, "0123456789abcdef")
        assert prune.root == str(dev.root) and prune.previous == self.B and prune.busy == []
        items = {i.file_name: i for i in prune.plan.items}
        assert sorted(items) == [self.A, self.B] and "kept to go back by hand" in items[self.B].what
        ticked = choose(prune.plan, [[i.file_name for i in prune.plan.items].index(self.A)])
        audit = BoardAudit()
        result = person_steps.remove_board_builds(RemoteShell(dev), accept(ticked, "gui", "permanent"), prune,
                                                  "0123456789abcdef", audit)
        assert result.done == [f"runtime build {self.A}"] and not (dev.root / self.A).exists()
        assert (dev.root / self.B).is_dir() and audit.rows[0]["actor"] == "gui"

    def test_an_ai_never_removes_them(self, tmp_path, device_base):
        from orchestrator.Core.deletion import AcceptedDeletion
        from orchestrator.runtime.device.remote import RemoteShell
        dev = FakeDevice(device_base)
        board_builds(dev, self.A, self.B, current=self.B)
        prune = person_steps.list_board_builds(RemoteShell(dev), tmp_path, "0123456789abcdef")
        with pytest.raises(PermissionError):
            person_steps.remove_board_builds(RemoteShell(dev), AcceptedDeletion(prune.plan, "permanent", "mcp"),
                                             prune, "0123456789abcdef", BoardAudit())
        assert (dev.root / self.A).is_dir()


# --- the shared runtime steps ---------------------------------------------------------------------------------

class TestRuntimeSteps:

    def test_the_plan_as_data(self, monkeypatch):
        monkeypatch.setattr("orchestrator.runtime.build.toolchain.host_platform", lambda: HOST)
        plan = steps.plan_runtime(RK3566)
        assert plan.build_id == plan_for(RK3566).build_id
        view = steps.plan_view(plan)
        assert view["target"] == {"os": "linux", "arch": "arm64", "libc": "glibc", "libc_version": "2.36"}
        assert view["builder"]["base"] == "debian-12" and view["cpu_arm_arch"] == "armv8.2-a+dotprod+fp16"
        assert "llama-server" in view["programs"] and view["built"] is False and view["built_path"] is None
        built = SimpleNamespace(path=Path("/x/build"))
        assert steps.plan_view(plan, built)["built_path"] == str(Path("/x/build"))

    def test_the_status_as_data(self, tmp_path):
        from orchestrator.runtime.device.package import DeviceRuntimeStore
        plan = plan_for(RK3566)
        status = steps.runtime_status(tmp_path, "0123456789abcdef", plan)
        assert status == {"device_id": "0123456789abcdef", "planned_build_id": plan.build_id, "built": False,
                          "deployed": None, "current": False}
        DeviceRuntimeStore(tmp_path, "0123456789abcdef").save_record({"build_id": plan.build_id, "install": "user"})
        status = steps.runtime_status(tmp_path, "0123456789abcdef", plan)
        assert status["current"] is True and status["deployed"]["install"] == "user"

    def build_env(self, monkeypatch, source, cached=True):
        import orchestrator.runtime.build.executor as executor_module
        import orchestrator.runtime.build.source as source_module
        import orchestrator.runtime.build.verify as verify_module
        from orchestrator.runtime.build.executor import BuildExecutor
        fetched = []
        monkeypatch.setattr("orchestrator.runtime.build.toolchain.host_platform", lambda: HOST)
        monkeypatch.setattr(source_module, "cached_source", lambda pin, state: source if cached else None)
        monkeypatch.setattr(source_module, "fetch_source", lambda pin, state, url=None, log=None: (
            fetched.append(url) or source))
        monkeypatch.setattr(executor_module, "DockerEngine", lambda: None)
        jobs = []
        monkeypatch.setattr(BuildExecutor, "build", lambda self, plan, **kw: (
            jobs.append(kw.get("jobs")) or FakeDeviceExecutor().build(plan, **kw)))
        monkeypatch.setattr(verify_module, "Verifier", FormatOnlyVerifier)
        return fetched, jobs

    def test_the_build_asks_before_fetching_and_passes_jobs_and_the_mirror(self, tmp_path, monkeypatch, source):
        fetched, jobs = self.build_env(monkeypatch, source, cached=False)
        with pytest.raises(steps.SourceDeclined):
            steps.build_runtime(tmp_path, "0123456789abcdef", RK3566, plan_for(RK3566), PIN, may_fetch=lambda: False)
        assert fetched == []
        seen = []
        built = steps.build_runtime(tmp_path, "0123456789abcdef", RK3566, plan_for(RK3566), PIN, jobs=3,
                                    mirror="https://mirror.example/llama.cpp.git", may_fetch=lambda: True,
                                    on_step=lambda kind, build: seen.append(kind))
        assert fetched == ["https://mirror.example/llama.cpp.git"] and jobs == [3]
        assert seen == ["fetch", "fetched", "build"] and built.built_now and built.packaged.path.is_file()
        seen.clear()
        again = steps.build_runtime(tmp_path, "0123456789abcdef", RK3566, plan_for(RK3566), PIN,
                                    on_step=lambda kind, build: seen.append(kind), reuse_package=True)
        assert seen == ["fetch", "fetched", "stored"] and not again.built_now
        assert again.packaged.path == built.packaged.path

    def test_a_cancel_stops_between_the_steps(self, tmp_path, monkeypatch, source):
        self.build_env(monkeypatch, source)
        with pytest.raises(steps.BuildCancelled):
            steps.build_runtime(tmp_path, "0123456789abcdef", RK3566, plan_for(RK3566), PIN, cancelled=lambda: True)

    def test_the_ai_path_builds_with_the_same_step(self, tmp_path, monkeypatch, source):
        from orchestrator.runtime.build.source import SourceError
        from orchestrator.runtime.device import service
        monkeypatch.setattr("orchestrator.runtime.build.toolchain.host_platform", lambda: HOST)
        calls = []

        def build_runtime(*args, **kwargs):
            calls.append(kwargs)
            raise SourceError("mirror unreachable")

        monkeypatch.setattr(steps, "build_runtime", build_runtime)
        device = SimpleNamespace(device_id="0123456789abcdef", label="Board")
        with pytest.raises(service.DeviceRuntimeError) as e:
            service.ensure_build(tmp_path, device, RK3566, plan_for(RK3566).build_id, jobs=2, mirror="file:///m")
        assert "The pinned llama.cpp source: mirror unreachable" in str(e.value)
        assert calls[0]["jobs"] == 2 and calls[0]["mirror"] == "file:///m" and calls[0]["reuse_package"] is True

    def test_the_short_benchmarks_model(self, monkeypatch):
        import orchestrator.runtime.device.remote as remote
        digests = {"/home/u/models/cat.gguf": "c" * 64, "/home/u/models/own.gguf": "d" * 64}
        monkeypatch.setattr(remote, "model_sha256", lambda shell, path: digests.get(path))
        monkeypatch.setattr(steps, "catalog_model_digests", lambda: {"c" * 64})
        order = []

        def choose(model, answer):
            return steps.choose_bench_model(None, "/home/u", model,
                                            may_run_untrusted=lambda path: order.append(("asked", path)) or answer,
                                            record_consent=lambda digest: order.append(("consent", digest)))

        assert choose(None, True) == steps.BenchChoice()
        assert choose("models/none.gguf", True) == steps.BenchChoice(missing="/home/u/models/none.gguf")
        assert choose("models/cat.gguf", False) == steps.BenchChoice("/home/u/models/cat.gguf", True, "c" * 64)
        assert order == [], "a catalog model runs without a question"
        assert choose("/home/u/models/own.gguf", False).allowed is False and order == [
            ("asked", "/home/u/models/own.gguf")]
        order.clear()
        assert choose("models/own.gguf", True).allowed is True
        assert order == [("asked", "/home/u/models/own.gguf"), ("consent", "d" * 64)], "the consent is written first"
        with pytest.raises(SettingsError):
            choose("../etc/passwd", True)

    def test_the_ai_paths_benchmark_trusts_the_same_catalog_models(self, tmp_path, monkeypatch):
        """service.benchmark (the AI tool and the Deployment tab's 'Measure on the board') decides which model is a
        catalog model with the same step as the CLI and the Boards window (steps.catalog_model_digests); review of
        wave 5a."""
        import orchestrator.runtime.device.remote as remote
        from orchestrator.runtime.device import service
        from orchestrator.runtime.device.package import DeviceRuntimeStore
        DeviceRuntimeStore(tmp_path, "0123456789abcdef").save_record({"build_id": "b" * 64, "install": "user"})
        device = SimpleNamespace(device_id="0123456789abcdef", label="ZERO 3W")
        settings = DeploySettings(ssh_user="dietpi", model="models/q.gguf")

        class Client:
            def close(self):
                pass

        monkeypatch.setattr(service, "connect", lambda *a, **k: Client())
        monkeypatch.setattr(remote, "RemoteShell", lambda client: client)
        monkeypatch.setattr(remote, "read_facts", lambda shell: SimpleNamespace(uid=1000, home="/home/dietpi"))
        monkeypatch.setattr(remote, "model_sha256", lambda shell, path: "e" * 64)
        ran = []
        monkeypatch.setattr(remote, "verify_on_device", lambda shell, **kw: ran.append(kw["bench_model"]) or
                            VerifyReport([Check("version", True, "")]))
        monkeypatch.setattr(steps, "catalog_model_digests", lambda: {"e" * 64})
        out = service.benchmark(tmp_path, device, RK3566, settings, "SHA256:x", allow_untrusted=False)
        assert out["trusted_model"] is True and ran == ["/home/dietpi/models/q.gguf"]
        monkeypatch.setattr(steps, "catalog_model_digests", lambda: set())
        with pytest.raises(service.DeviceRuntimeError) as e:
            service.benchmark(tmp_path, device, RK3566, settings, "SHA256:x", allow_untrusted=False)
        assert e.value.code == "declined" and len(ran) == 1

    def test_installing_saves_the_record_only_when_the_new_build_passed(self, tmp_path, monkeypatch):
        import orchestrator.runtime.device.remote as remote
        from orchestrator.runtime.device.package import DeviceRuntimeStore
        passed = {"ok": False}
        seen = {}

        def deploy_runtime(shell, **kw):
            seen.update(kw)
            report = VerifyReport([Check("version", passed["ok"], "")])
            return remote.DeployResult(build_id=kw["build_id"], home="/home/u", root="/home/u/x", previous="e" * 16,
                                       report=report, activated=passed["ok"], unit_started=None)

        monkeypatch.setattr(remote, "deploy_runtime", deploy_runtime)
        build = SimpleNamespace(build_id="b" * 64, plan={"verify": {}})
        packaged = SimpleNamespace(path=Path("/x.tar.gz"), sha256="a" * 64)
        kwargs = dict(state_dir=tmp_path, device_id="0123456789abcdef", profile=RK3566,
                      settings=DeploySettings(ssh_user="dietpi"), build=build, packaged=packaged, source=PIN,
                      arch="arm64", kv_cache="q8_0", bench=steps.BenchChoice("/home/u/m.gguf", True, "c" * 64))
        outcome = steps.install_runtime(object(), **kwargs)
        assert outcome.ok is False and DeviceRuntimeStore(tmp_path, "0123456789abcdef").record() == {}
        assert seen["kv_cache"] == "q8_0" and seen["arch"] == "arm64" and callable(seen["check"])
        passed["ok"] = True
        outcome = steps.install_runtime(object(), **kwargs)
        record = DeviceRuntimeStore(tmp_path, "0123456789abcdef").record()
        assert outcome.ok and record["build_id"] == "b" * 64 and record["kv_cache"] == "q8_0"
        assert record["previous"] == "e" * 16 and record["ssh_user"] == "dietpi" and record["verification"]["ok"]

    def test_checking_keeps_the_result_in_the_record(self, tmp_path, monkeypatch):
        import orchestrator.runtime.device.remote as remote
        from orchestrator.runtime.device.package import DeviceRuntimeStore
        seen = {}
        monkeypatch.setattr(remote, "verify_on_device", lambda shell, **kw: seen.update(kw) or VerifyReport(
            [Check("version", True, "b1")]))
        store = DeviceRuntimeStore(tmp_path, "0123456789abcdef")
        store.save_record({"build_id": "b" * 64, "install": "user"})
        record = store.record()
        report = steps.check_runtime(object(), state_dir=tmp_path, device_id="0123456789abcdef", home="/home/u",
                                     record=record, plan_verify={"binaries": []}, source=PIN, port=9090)
        assert report.ok and seen["port"] == 9090 and seen["bench_model"] is None and seen["bench_allowed"] is False
        saved = store.record()
        assert saved["verification"]["ok"] is True and saved["verified_at"] and saved["install"] == "user"

    def test_the_verify_plan_comes_from_the_stored_build_else_from_the_profile(self, tmp_path):
        asked = []
        plan = steps.deployed_verify_plan(tmp_path, {"build_id": "b" * 64},
                                          lambda: asked.append(1) or SimpleNamespace(verify={"binaries": ["x"]}))
        assert plan == {"binaries": ["x"]} and asked == [1]


def test_older_packages_are_counted_not_deleted(tmp_path):
    devices = SimpleNamespace(cleanup_plan=lambda: SimpleNamespace(items=[
        SimpleNamespace(file_name="llama-runtime-arm64-aaaaaaaaaaaaaaaa.tar.gz"),
        SimpleNamespace(file_name="llama-runtime-arm64-aaaaaaaaaaaaaaaa.tar.gz.sha256"),
        SimpleNamespace(file_name="llama-runtime-arm64-bbbbbbbbbbbbbbbb.tar.gz")]))
    assert steps.older_package_count(devices, SimpleNamespace(build_id="b" * 64)) == 1
    assert steps.older_package_count(SimpleNamespace(cleanup_plan=lambda: None), SimpleNamespace(build_id="b")) == 0
