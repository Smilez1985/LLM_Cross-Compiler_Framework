"""Enterprise mode refuses a board whose framework key sits on a weaker tier when the board is USED, not only when it
is paired (remote/key_tiers.py use_problem, runtime/device/trusted.py tier_refusal): the person's board steps
(person_steps.connect_stored, behind the CLI and the Boards window), the AI's device service (service.connect) and the
deploy of a package (DeploymentManager) each take the framework mode of their framework and refuse before anything runs
on the board; the refusal names the ways on. The listings show the tier (`llmcf device list`, `llmcf deploy devices
list`, the Boards window's Key tier column).

Every board here is a stand-in (no network, no SSH server); documentation names and addresses only.
"""

import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from orchestrator.remote import key_tiers
from orchestrator.runtime.profile import device_id_for_host_key

FP = "SHA256:" + "A" * 43
DEVICE = device_id_for_host_key(FP)
HOST = "192.0.2.10"
PUBLIC_LINE = ("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleExampleExampleExampleExample0 "
               "llm-cross-compiler-framework")


class _Session:
    """A RemoteSession stand-in: logs in at once, runs only the gate's status operation."""
    opened = []

    def __init__(self, endpoint=None, user="", state_dir=None, *, status="", **kwargs):
        self.endpoint, self.user, self.key, self.fingerprint = endpoint, user, "host-key", FP
        self.client, self.closed, self.ran, self.status = object(), False, [], status
        self.resumed, self.waited, self.detached_sent = 0, 0.0, False
        _Session.opened.append(self)

    def open(self):
        pass

    def close(self):
        self.closed = True

    def run(self, script, **kwargs):
        self.ran.append(str(getattr(script, "verb", script)))
        return SimpleNamespace(status=0, out=self.status, err="")

    def pair(self, bundle=None, *, mode="private", pairer=None):
        return False

    def private_dir(self, *a, **k):
        raise AssertionError("the deploy reached the board's folders")


# --- the rule ------------------------------------------------------------------------------------------------------

def test_enterprise_mode_refuses_the_weaker_tiers_at_use_and_names_the_ways_on():
    for tier in (key_tiers.TIER_DROPBEAR, key_tiers.TIER_HOME):
        text = key_tiers.use_problem(tier, "enterprise")
        assert "Enterprise mode refuses to use this board" in text and "Nothing was done" in text
        assert "switch to OpenSSH" in text and "password login" in text and "private mode" in text
        assert key_tiers.use_problem(tier, "private") == ""
    for tier in (key_tiers.TIER_ROOT, key_tiers.TIER_PASSWORD, key_tiers.TIER_UNRESTRICTED, ""):
        assert key_tiers.use_problem(tier, "enterprise") == ""


def test_the_refusal_reads_the_recorded_tier_else_asks_the_board_once_and_records_it(tmp_path):
    from orchestrator.runtime.device.trusted import TrustedDevices, record_key_tier, tier_refusal
    assert tier_refusal(tmp_path, DEVICE, "enterprise") == ""          # nothing recorded, nobody to ask
    board = _Session(status="restricted=yes gate=/x keys=home signing=yes tier=dropbear")
    assert "Dropbear tier" in tier_refusal(tmp_path, DEVICE, "enterprise", board)
    assert board.ran == ["status"] and TrustedDevices(tmp_path).key_tier(DEVICE) == "dropbear"
    again = _Session()
    assert tier_refusal(tmp_path, DEVICE, "enterprise", again) and again.ran == []   # the record answers now
    assert tier_refusal(tmp_path, DEVICE, "private", again) == ""
    record_key_tier(tmp_path, DEVICE, "root")
    assert tier_refusal(tmp_path, DEVICE, "enterprise", again) == ""


def test_a_recorded_password_tier_never_lets_a_keyed_login_past_enterprise_mode(tmp_path):
    """Regression (failed before: a pairing that ended with "password login" recorded the tier "password" over the
    board's earlier Dropbear-tier record while the framework's Dropbear-tier key stayed on the board, so every later
    login with that key passed tier_refusal in enterprise mode). A recorded "password" is asked again on the board; a
    board without a framework key keeps its record."""
    from orchestrator.runtime.device import trusted
    TrustedDevices, record_key_tier, tier_refusal = trusted.TrustedDevices, trusted.record_key_tier, trusted.tier_refusal
    record_key_tier(tmp_path, DEVICE, "dropbear")
    record_key_tier(tmp_path, DEVICE, "password")
    keyed = _Session(status="restricted=yes gate=/x keys=home signing=yes tier=dropbear")
    assert "Dropbear tier" in tier_refusal(tmp_path, DEVICE, "enterprise", keyed)
    assert keyed.ran == ["status"] and TrustedDevices(tmp_path).key_tier(DEVICE) == "dropbear"
    record_key_tier(tmp_path, DEVICE, "password")
    no_key = _Session(status="restricted=no gate=none")   # the status operation outside the gate: no framework key
    assert tier_refusal(tmp_path, DEVICE, "enterprise", no_key) == "" and no_key.ran == ["status"]
    assert TrustedDevices(tmp_path).key_tier(DEVICE) == "password"
    assert tier_refusal(tmp_path, DEVICE, "enterprise") == ""   # nobody to ask: nothing says the key is weaker


class _ScanKey:
    def get_name(self):
        return "ssh-ed25519"

    def asbytes(self):
        return b"made-up host key of a scanned board"


def test_a_scan_with_the_frameworks_key_refuses_a_weaker_board_in_enterprise_mode(tmp_path, monkeypatch):
    """Regression (failed before: `llmcf scan --device` and the Builder tab's remote scan without a password logged in
    with a Dropbear-tier key and ran the probe in enterprise mode, while the AI's probe of the same board was
    refused). The probe never runs; nothing is stored."""
    from orchestrator.runtime import remote_probe
    from orchestrator.runtime.device.trusted import record_key_tier
    from orchestrator.runtime.profile import ProfileStore
    from orchestrator.runtime.scan_steps import DeviceScanError, scan_device
    from orchestrator.utils import ssh_trust
    closed = []
    client = SimpleNamespace(close=lambda: closed.append(1),
                             get_transport=lambda: SimpleNamespace(get_remote_server_key=_ScanKey))
    monkeypatch.setattr(ssh_trust, "connect", lambda *a, **k: client)
    monkeypatch.setattr(remote_probe, "probe_over_ssh", lambda *a, **k: pytest.fail("the probe ran"))
    device_id = device_id_for_host_key(ssh_trust.fingerprint(_ScanKey()))
    record_key_tier(tmp_path, device_id, "dropbear")
    with pytest.raises(DeviceScanError) as refused:
        scan_device(tmp_path, HOST, 22, "dietpi", _ScanKey(), record=False, mode="enterprise")
    assert refused.value.kind == "weaker_tier" and "Enterprise mode refuses to use this board" in str(refused.value)
    assert closed and ProfileStore(tmp_path).list_devices() == []


# --- the person's board steps (CLI, Boards window) ----------------------------------------------------------------

@pytest.fixture
def sessions(monkeypatch):
    from orchestrator.remote import session
    from orchestrator.runtime.device import person_steps
    _Session.opened = []
    monkeypatch.setattr(session, "RemoteSession", _Session)
    monkeypatch.setattr(person_steps, "_need_paramiko", lambda: None)
    return _Session.opened


def test_connect_stored_refuses_a_weaker_board_in_enterprise_mode(tmp_path, sessions):
    """Regression (failed before: only Mojo warned; a stored Dropbear-tier board was logged in to and used in
    enterprise mode)."""
    from orchestrator.runtime.device import person_steps
    from orchestrator.runtime.device.trusted import record_key_tier
    record_key_tier(tmp_path, DEVICE, "home")
    with pytest.raises(person_steps.BoardStepError) as refused:
        person_steps.connect_stored(tmp_path, DEVICE, HOST, 22, "dietpi", mode="enterprise")
    assert refused.value.kind == "weaker_tier" and "home gate" in str(refused.value)
    assert sessions[-1].closed
    session, _key = person_steps.connect_stored(tmp_path, DEVICE, HOST, 22, "dietpi", mode="private")
    assert not session.closed


def test_the_ai_device_service_refuses_a_weaker_board_in_enterprise_mode(tmp_path, sessions):
    from orchestrator.runtime.device import service
    from orchestrator.runtime.device.trusted import record_key_tier
    record_key_tier(tmp_path, DEVICE, "dropbear")
    device = SimpleNamespace(device_id=DEVICE, label="garage", host=HOST, port=22, fingerprint=FP, user="dietpi",
                             address=f"{HOST}:22")
    with pytest.raises(service.DeviceRuntimeError) as refused:
        service.connect(device, tmp_path, "dietpi", FP, step="probe", mode="enterprise")
    assert refused.value.code == "needs_person" and "no tool can" in refused.value.next
    assert HOST not in str(refused.value) and sessions[-1].closed
    assert service.connect(device, tmp_path, "dietpi", FP, step="probe") is sessions[-1]


def test_a_package_deploy_takes_the_frameworks_mode_and_refuses_before_any_upload(tmp_path, sessions, monkeypatch):
    """Regression (failed before: DeploymentManager never read the framework mode)."""
    from orchestrator.Core import deployment_manager as dm
    from orchestrator.runtime.device import board_ops
    from orchestrator.runtime.device.trusted import record_key_tier
    monkeypatch.setattr(board_ops, "resolve_signer", lambda *a, **k: (None, "no-signer"))
    record_key_tier(tmp_path, DEVICE, "dropbear")
    package = tmp_path / "pkg.zip"
    package.write_bytes(b"zip")
    settings = {"framework_mode": "enterprise"}
    framework = SimpleNamespace(config=SimpleNamespace(user_state_dir=str(tmp_path),
                                                       get=lambda key, default=None: settings.get(key, default)))
    manager = dm.DeploymentManager(framework)
    result = manager._deploy_paramiko(package, HOST, "dietpi", None, 22, False)
    assert not result.success and "Enterprise mode refuses to use this board" in result.reason
    assert result.device_state == "ok"
    settings["framework_mode"] = "private"   # private mode goes on to the board's folders
    with pytest.raises(AssertionError, match="folders"):
        _deploy_raw(manager, package)


def _deploy_raw(manager, package):
    """The deploy's steps without its catch-all (the stand-in session raises when the folders are reached)."""
    from orchestrator.Core import deployment_manager as dm
    with open(package, "rb") as fh:
        board = dm._BoardSession(manager, HOST, "dietpi", None, 22, None, None, lambda: False, 0.0, None)
        board.session = board._session(False)
        board._deploy(fh, package.name, 3, "0" * 64)


# --- the listings ---------------------------------------------------------------------------------------------------

def test_the_device_listings_carry_the_key_tier_and_mark_the_refused_ones(tmp_path, monkeypatch):
    from orchestrator import cli
    from orchestrator.runtime.device import person_steps
    from orchestrator.runtime.device.trusted import record_key_tier
    from orchestrator.runtime.profile import ProfileStore
    monkeypatch.setattr(ProfileStore, "list_devices", lambda self: [DEVICE])
    record_key_tier(tmp_path, DEVICE, "dropbear")
    assert person_steps.device_rows(tmp_path)[0]["key_tier"] == "dropbear"
    assert cli._tier_cell("dropbear") == "[yellow]dropbear (weaker)[/yellow]"
    assert "refused in enterprise mode" in cli._tier_cell("dropbear", "enterprise")
    assert cli._tier_cell("root", "enterprise") == "root" and cli._tier_cell("") == "-"


# --- the pairing takes its proposal from the stored probe; the board's facts decide ----------------------------------

def _access(**values):
    from orchestrator.runtime.profile import AccessFacts
    return AccessFacts(**values)


def test_the_stored_probe_proposes_a_tier_by_the_boards_access_facts():
    dropbear = _access(ssh_server="dropbear", sudo="nopasswd", ssh_keygen="none")
    assert key_tiers.proposal(dropbear, "private") == "dropbear"
    assert key_tiers.proposal(dropbear, "enterprise") == "openssh"
    assert key_tiers.proposal(_access(ssh_server="openssh", sudo="password", ssh_keygen="openssh",
                                      ssh_keygen_version="9.2"), "private") == "root"
    assert key_tiers.proposal(_access(ssh_server="openssh", sudo="password", ssh_keygen="openssh",
                                      ssh_keygen_version="7.9"), "private") == ""   # no -Y: the board decides
    assert key_tiers.proposal(_access(ssh_server="dropbear", sudo="absent"), "private") == "home"
    assert key_tiers.proposal(_access(ssh_server="dropbear", sudo="absent"), "enterprise") == "password"
    assert key_tiers.proposal(None) == "" and key_tiers.proposal(_access()) == ""


def test_pairing_marks_the_probes_proposal_only_when_the_board_offers_it_now(tmp_path, monkeypatch):
    """Regression (failed before: pairing never read the stored probe's access facts)."""
    from test_board_tiers import _Audit, _Board, _Chooser, _answers, _signer

    from orchestrator.remote import pairing
    from orchestrator.runtime.device import board_ops
    from orchestrator.utils import ssh_trust
    monkeypatch.setattr(ssh_trust, "client_key", lambda state_dir: (tmp_path / "key", PUBLIC_LINE))
    audit, chooser = _Audit(), _Chooser("dropbear")
    got = pairing.pair(_Board(_answers()), tmp_path, board_ops.bundle(_signer()), password="pw", chooser=chooser,
                       audit=audit, via="cli", access=_access(ssh_server="dropbear", sudo="nopasswd"))
    offer = chooser.offers[0]
    assert got.tier == "dropbear" and offer.proposed == "dropbear" and not offer.note
    assert any("(proposed from the board's probe)" in line and "Dropbear tier" in line for line in offer.lines())
    assert offer.in_order()[0] == "dropbear" and offer.in_order()[-1] == "password"
    row = next(r for r in audit.rows if r[1] == "board_key_tier_offered")
    assert row[3]["proposed"] == "dropbear" and row[3]["probe_disagrees"] is False
    # the probe said OpenSSH with sudo, but the board's root step fails now: the board's facts decide
    chooser = _Chooser("password")
    pairing.pair(_Board(_answers()), tmp_path, board_ops.bundle(_signer()), password="pw", chooser=chooser,
                 audit=_Audit(), via="cli", access=_access(ssh_server="openssh", sudo="nopasswd"))
    assert chooser.offers[0].proposed == "" and "says otherwise now" in chooser.offers[0].note
    assert any("says otherwise now" in line for line in chooser.told)


def test_the_probe_alone_never_sets_up_a_weaker_tier(tmp_path, monkeypatch):
    from test_board_tiers import _Board, _answers, _signer

    from orchestrator.remote import pairing
    from orchestrator.runtime.device import board_ops
    from orchestrator.utils import ssh_trust
    monkeypatch.setattr(ssh_trust, "client_key", lambda state_dir: (tmp_path / "key", PUBLIC_LINE))
    board = _Board(_answers())
    with pytest.raises(ssh_trust.GateUnavailable):   # nobody to ask: the proposal installs nothing
        pairing.pair(board, tmp_path, board_ops.bundle(_signer()), password="pw",
                     access=_access(ssh_server="dropbear", sudo="nopasswd"))
    assert "dropbear-board=installed" not in board.verbs


def test_the_stored_access_facts_come_from_the_boards_probe_file(tmp_path):
    from orchestrator.runtime.profile import stored_access
    assert stored_access(tmp_path, DEVICE) is None   # no stored probe: no proposal


def test_the_gui_asks_the_proposed_choice_first(monkeypatch):
    pytest.importorskip("PySide6")
    from orchestrator.gui import key_tier_ask
    asked = []
    chooser = key_tier_ask.GuiTierChooser(object())
    monkeypatch.setattr(chooser, "_ask", lambda key, **values: asked.append(key) or False)
    facts = key_tiers.TierFacts(server="dropbear", sudo="nopass", verifier="yes", managers=("apt-get",),
                                family="debian")
    offer = key_tiers.with_proposal(key_tiers.offer("no-sshd", facts, "private"),
                                    _access(ssh_server="dropbear", sudo="nopasswd"))
    assert offer.choices == ("openssh", "dropbear", "password")
    assert chooser.choose(offer) == "password"   # every answer "No"
    assert asked == ["deploy.tier.ask.dropbear", "deploy.tier.ask.openssh"]


# --- a password deploy offers the tiers; the switch moves a stored board to its new sshd -----------------------------

def _trusted_key(state, host=HOST, port=22):
    paramiko = pytest.importorskip("paramiko")
    from orchestrator.utils import ssh_trust
    key = paramiko.ECDSAKey.generate()
    ssh_trust.trust_host_key(state, host, port, key)
    return key


def test_a_password_session_pairs_through_the_persons_pairer(tmp_path):
    from orchestrator.remote import session as remote_session
    seen = []
    board = remote_session.RemoteSession.__new__(remote_session.RemoteSession)
    board.credentials, board.client, board.state_dir = remote_session.OneTimePassword("pw-once"), "client", tmp_path
    assert board.pair("bundle", pairer=lambda client, bundle, pw: seen.append((client, bundle, pw)) or "dropbear") \
        == "dropbear"
    assert seen == [("client", "bundle", "pw-once")]
    board.credentials = remote_session.KeyOnly()   # the AI's path: never pairs, never calls the person's pairer
    assert board.pair("bundle", pairer=lambda *a: pytest.fail("paired")) is False


def test_a_deploy_records_the_tier_its_pairing_set_up_and_stops_after_the_switch(tmp_path, sessions, monkeypatch):
    """Regression (failed before: DeploymentManager's password deploy never offered the tiers: a Dropbear board got
    GateUnavailable and the deploy ended as 'SSH Deployment Error')."""
    from orchestrator.Core import deployment_manager as dm
    from orchestrator.remote.address import Endpoint
    from orchestrator.remote.pairing import PairOutcome
    from orchestrator.runtime.device import board_ops
    from orchestrator.runtime.device.trusted import TrustedDevices
    monkeypatch.setattr(board_ops, "resolve_signer", lambda *a, **k: (None, "no-signer"))

    class PairingSession(_Session):
        def pair(self, bundle=None, *, mode="private", pairer=None):
            return pairer("client", bundle, "pw-once")
    monkeypatch.setattr("orchestrator.remote.session.RemoteSession", PairingSession)
    package = tmp_path / "pkg.zip"
    package.write_bytes(b"zip")
    framework = SimpleNamespace(config=SimpleNamespace(user_state_dir=str(tmp_path), get=lambda k, d=None: d))
    manager = dm.DeploymentManager(framework)
    switched = PairOutcome("restricted", tier="root", endpoint=Endpoint(HOST, 2222), key="new-key")
    result = manager._deploy_paramiko(package, HOST, "dietpi", "pw-once", 22, True,
                                      pairer=lambda client, bundle, pw: switched)
    assert not result.success and "port 2222" in result.reason and "Deploy again" in result.reason
    with pytest.raises(AssertionError, match="folders"):   # the Dropbear tier: on to the board's folders
        board = dm._BoardSession(manager, HOST, "dietpi", "pw-once", 22, None, None, lambda: False, 0.0, None,
                                 pairer=lambda client, bundle, pw: PairOutcome("dropbear", tier="dropbear"))
        board.session = board._session(True)
        with open(package, "rb") as fh:
            board._deploy(fh, package.name, 3, "0" * 64)
    assert TrustedDevices(tmp_path).key_tier(DEVICE) == "dropbear"


def test_the_switch_moves_a_stored_boards_data_and_record_to_its_new_device_id(tmp_path):
    """The P6 move for the switch to OpenSSH: probe, deploy settings and record go to the new sshd's device ID."""
    from test_device_runtime import RK3566

    from orchestrator.remote.address import Endpoint
    from orchestrator.remote.pairing import PairOutcome
    from orchestrator.runtime.device import person_steps
    from orchestrator.runtime.device.trusted import TrustedDevices
    from orchestrator.runtime.profile import ProfileStore, device_id_for_host_key
    from orchestrator.utils import ssh_trust
    old_key = _trusted_key(tmp_path)
    old = TrustedDevices(tmp_path).record(HOST, 22, "dietpi", old_key, via="cli", paired=True, label="garage")
    ProfileStore(tmp_path).save_device(old.device_id, RK3566, label="garage", address=HOST)
    new_key = _trusted_key(tmp_path, port=2222)   # the person trusted the new sshd's key in the switch
    switch = person_steps.adopt_switched_board(tmp_path, old.device_id, HOST, "dietpi",
                                               PairOutcome("restricted", tier="root",
                                                           endpoint=Endpoint(HOST, 2222), key=new_key), via="cli")
    new_id = device_id_for_host_key(ssh_trust.fingerprint(new_key))
    assert switch.new_id == new_id and switch.old_id == old.device_id and switch.port == 2222
    devices = TrustedDevices(tmp_path)
    assert devices.get(old.device_id) is None and devices.get(new_id).port == 2222
    assert devices.get(new_id).label == "garage" and devices.key_tier(new_id) == "root"
    assert ProfileStore(tmp_path).load_device(new_id) is not None
    assert ProfileStore(tmp_path).load_device(old.device_id) is None
    assert "probe" in switch.data.moved
    text = person_steps.switched_text(switch)
    assert "port 2222" in text and new_id in text and "run it again" in text


def test_connect_stored_offers_the_switch_and_stops_the_step_after_it(tmp_path, sessions, monkeypatch):
    """Regression (failed before: connect_stored passed switch=False, so a stored Dropbear board was never offered
    the switch to OpenSSH)."""
    from orchestrator.remote import pairing
    from orchestrator.remote.address import Endpoint
    from orchestrator.runtime.device import board_ops, person_steps
    asked = {}

    def pair(client, state_dir, bundle, **kw):
        asked.update(kw)
        return pairing.PairOutcome("restricted", tier="root", endpoint=Endpoint(HOST, 2222),
                                   key=_trusted_key(tmp_path, port=2222))
    monkeypatch.setattr(pairing, "pair", pair)
    monkeypatch.setattr(board_ops, "pairing_bundle", lambda *a, **k: "bundle")
    with pytest.raises(person_steps.BoardStepError) as stopped:
        person_steps.connect_stored(tmp_path, DEVICE, HOST, 22, "dietpi", "pw-once", chooser=pairing.TierChooser(),
                                    via="cli")
    assert asked["switch"] is True and asked["host"] == HOST and asked["user"] == "dietpi"
    assert stopped.value.kind == "switched" and stopped.value.facts["port"] == 2222 and sessions[-1].closed
    asked.clear()
    monkeypatch.setattr(pairing, "pair", lambda client, state_dir, bundle, **kw: asked.update(kw) or
                        pairing.PairOutcome("restricted"))
    person_steps.connect_stored(tmp_path, DEVICE, HOST, 22, "dietpi", "pw-once")   # nobody to ask: no switch
    assert asked["switch"] is False


def test_deploy_build_gives_a_password_deploy_the_persons_pairer(tmp_path, monkeypatch):
    from orchestrator.remote.address import Endpoint
    from orchestrator.remote.pairing import PairOutcome, TierChooser
    from orchestrator.remote.session import OneTimePassword
    from orchestrator.runtime.device import person_steps
    package = tmp_path / "pkg.zip"
    package.write_bytes(b"zip")
    key = _trusted_key(tmp_path)
    seen = {}

    class Manager:
        def deploy_package(self, package, host, user, **options):
            seen.update(options)
            pairer = options["pairer"]
            pairer("client", "bundle", "pw-once")
            return SimpleNamespace(success=False, reason="", output=[])
    monkeypatch.setattr("orchestrator.remote.pairing.pair", lambda client, state, bundle, **kw: seen.update(
        pair=kw) or PairOutcome("restricted", tier="root", endpoint=Endpoint(HOST, 2222),
                                key=_trusted_key(tmp_path, port=2222)))
    framework = SimpleNamespace(config=SimpleNamespace(user_state_dir=str(tmp_path), get=lambda k, d=None: d))
    done = person_steps.deploy_build(framework, Manager(), Endpoint(HOST, 22), "dietpi", package=package,
                                     credentials=OneTimePassword("pw-once"), key=key, via="cli", actor="cli",
                                     state_dir=tmp_path, chooser=TierChooser())
    assert seen["pair"]["switch"] is True and seen["pair"]["mode"] == "private" and seen["pair"]["via"] == "cli"
    assert done.device is not None and done.device.port == 2222 and "port 2222" in done.result.reason
    seen.clear()
    person_steps.deploy_build(framework, SimpleNamespace(deploy_package=lambda *a, **o: seen.update(o) or
                                                         SimpleNamespace(success=False)),
                              Endpoint(HOST, 22), "dietpi", package=package, credentials=None, key=key, via="cli",
                              actor="cli", state_dir=tmp_path, chooser=TierChooser())
    assert "pairer" not in seen   # no password: nothing pairs


def test_the_deployment_tab_hands_its_tier_questions_to_a_password_deploy(qapp, monkeypatch):
    pytest.importorskip("PySide6")
    from orchestrator.gui.deployment_window import DeploymentWorker
    from orchestrator.runtime.device import person_steps
    seen = []
    monkeypatch.setattr(person_steps, "deploy_build", lambda *a, **kw: seen.append(kw) or SimpleNamespace(
        package_error=None, package=None, result=SimpleNamespace(success=False, reason="", output=[], code="")))
    framework = SimpleNamespace(get_component=lambda name: object(), config=SimpleNamespace())
    for password, expected in (("pw-once", "the chooser"), (None, None)):
        worker = DeploymentWorker(framework, None, "", {}, HOST, "dietpi", password, package="pkg.zip")
        worker.chooser = "the chooser"
        worker._failure_text = lambda result: "failed"
        worker.run()
        assert seen[-1]["chooser"] == expected


# --- the switch on ssh.socket systems, and Dropbear's removal through the package manager -----------------------

POSIX = pytest.mark.skipif(os.name == "nt" or shutil.which("sh") is None,
                           reason="the board's setup steps are POSIX sh programs; Windows has no /bin/sh to run them")


def test_a_socket_activated_sshd_is_reloaded_and_its_socket_restarted_after_the_port_file():
    """Regression (failed before: on ssh.socket systems, Ubuntu 22.10 and later, the switch restarted ssh.service only;
    ssh.socket kept listening on port 22 and the new sshd never answered on the switch's port)."""
    from orchestrator.remote import gate, openssh_switch
    body = str(gate.script("openssh-install", "apt-get", "2222"))
    tee = body.index(f"tee {openssh_switch.DROPIN}")
    reload_at = body.index("systemctl daemon-reload")
    socket_at = body.index(f"systemctl restart {openssh_switch.SSH_SOCKET}")
    assert tee < reload_at < socket_at
    assert f"is-enabled --quiet {openssh_switch.SSH_SOCKET}" in body and "systemctl restart ssh " in body
    if shutil.which("sh"):
        subprocess.run(["sh", "-n", "-c", body], check=True, timeout=60)


def test_dropbear_is_removed_through_the_package_manager_never_dietpi_software():
    """Regression (failed before: Dropbear went through dietpi-software with a software ID of DietPi's store, which
    the maintainer's decision rules out): the removal script asks the board's real package manager only, and no
    software-store ID is anywhere in the switch."""
    from orchestrator.remote import gate, key_tiers, openssh_switch
    body = str(gate.script("dropbear-disable"))
    source = open(openssh_switch.__file__, encoding="utf-8").read()
    assert "dietpi-software" not in body and "uninstall" not in body and "104" not in body
    assert not hasattr(openssh_switch, "DIETPI_DROPBEAR_ID") and "dietpi-software" not in source
    assert "dietpi-software" not in key_tiers.MANAGERS and "dietpi-software" not in str(gate.script("tier-facts"))
    assert body.index("systemctl disable --now dropbear") < body.index("apt-get remove")
    for word in ("purge", "autoremove", "--auto-remove", "-Rs", "-Rc", "-Rdd", "--clean-deps "):
        assert word not in body.replace("AutomaticRemove=false", "").replace("--noautoremove", "").replace(
            "--no-clean-deps", ""), word
    # an earlier pairing's answer with dietpi-software as a manager is read and that word ignored
    facts = key_tiers.TierFacts.parse("manager=apt-get\nmanager=dietpi-software\ndropbear_pkg=dropbear-bin\n"
                                      "dropbear_pkg=../../etc\ndropbear_pkg=dropbear;rm\n")
    assert facts.managers == ("apt-get",) and facts.dropbear_packages == ("dropbear-bin",)


# A stand-in board: every program the script calls is a stub on a PATH of its own (no tool of this computer is
# found, so its real package manager never answers). sudo writes down what it was asked and runs the stub; the
# package managers write down what they were asked, so nothing is removed anywhere.
SHELL_TOOLS = ("sh", "cat", "tr", "sed", "env")


def _package_board(tmp_path, *, tool="dpkg", installed=(), extra_removed=(), remove_fails=False, manager=True):
    log = tmp_path / "calls.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    for name in SHELL_TOOLS:
        found = shutil.which(name)
        assert found, name
        os.symlink(found, bin_dir / name)
    fail = "exit 100" if remove_fails else "exit 0"
    stubs = {
        "sudo": f'echo "sudo $*" >> "{log}"; while [ $# -gt 0 ]; do case $1 in -S) read -r _pw; shift ;; '
                '-n) shift ;; -p) shift 2 ;; *) break ;; esac; done; exec "$@"',
        "systemctl": f'echo "systemctl $*" >> "{log}"',
        "rc-update": f'echo "rc-update $*" >> "{log}"',
        "rc-service": f'echo "rc-service $*" >> "{log}"',
    }
    have = " ".join(installed)
    if tool == "dpkg":
        lines = "".join(f'printf "%s\\n" "{p} install ok installed"; ' for p in installed)
        stubs["dpkg-query"] = f'{lines}printf "%s\\n" "dropbear-run deinstall ok config-files"; exit 1'
        if manager:
            stubs["apt-get"] = (f'echo "apt-get $*" >> "{log}"; case " $* " in *" -s "*) '
                                'shift 2; for p in "$@" ' + " ".join(extra_removed)
                                + '; do echo "Remv $p [1.0]"; done; exit 0 ;; esac; ' + fail)
    elif tool == "pacman":
        stubs["pacman"] = (f'case $1 in -Qq) for p in {have}; do echo "$p"; done; exit 0 ;; esac; '
                           f'echo "pacman $*" >> "{log}"; {fail}')
    elif tool == "apk":
        stubs["apk"] = (f'case "$1 $2" in "info -e") for p in {have}; do echo "$p"; done; exit 0 ;; esac; '
                        f'echo "apk $*" >> "{log}"; case " $* " in *" -s "*) n=0; for p in {have} '
                        + " ".join(extra_removed)
                        + '; do n=$((n+1)); echo "($n/9) Purging $p (1.0-r0)"; done; exit 0 ;; esac; ' + fail)
    elif tool == "rpm":
        stubs["rpm"] = (f'case $1 in -q) for p in {have}; do echo "$p"; done; exit 0 ;; esac; '
                        f'echo "rpm $*" >> "{log}"; ' + ("exit 1" if extra_removed else "exit 0"))
        stubs["dnf"] = f'echo "dnf $*" >> "{log}"; {fail}'
    for name, text in stubs.items():
        (bin_dir / name).write_text("#!/bin/sh\n" + text + "\n", encoding="utf-8")
        (bin_dir / name).chmod(0o755)

    def run(script, stdin=None, timeout=None):
        done = subprocess.run([str(bin_dir / "sh"), "-c", str(script)], input=b"".join(stdin or []),
                              capture_output=True, timeout=60, env={"PATH": str(bin_dir), "HOME": str(tmp_path)})
        return SimpleNamespace(status=done.returncode, out=done.stdout.decode(), err=done.stderr.decode())

    def calls():
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return run, calls


def _disabled_first(calls):
    stop = next(i for i, c in enumerate(calls) if "disable --now dropbear" in c or "rc-service dropbear stop" in c)
    removes = [i for i, c in enumerate(calls) if c.startswith(("apt-get remove", "apt-get -y", "pacman -R", "apk del",
                                                               "dnf remove"))]
    return all(stop < i for i in removes)


@POSIX
@pytest.mark.parametrize("initramfs", [False, True])
def test_dropbear_is_stopped_then_only_its_server_packages_are_removed_with_apt(tmp_path, initramfs):
    """The init system stops and disables Dropbear first, then apt-get removes exactly the packages that provide the
    SSH server; dropbear-initramfs (the disk unlock at boot) is never removed, and the dropbear-bin it needs stays."""
    from orchestrator.remote import openssh_switch
    installed = ("dropbear", "dropbear-bin", "dropbear-run") + (("dropbear-initramfs",) if initramfs else ())
    run, calls = _package_board(tmp_path, installed=installed)
    done = openssh_switch.remove_dropbear(run, "pw-once")
    want = ("dropbear", "dropbear-run") if initramfs else ("dropbear", "dropbear-bin", "dropbear-run")
    assert done == openssh_switch.DropbearRemoval(True, "removed", "", want), (done, calls())
    removal = [c for c in calls() if c.startswith("apt-get remove")]
    assert removal == ["apt-get remove -y -o APT::Get::AutomaticRemove=false " + " ".join(want)], calls()
    assert not [c for c in calls() if "initramfs" in c and not c.startswith("apt-get -s")]
    assert _disabled_first(calls()) and not [c for c in calls() if "purge" in c or "autoremove" in c]
    text = openssh_switch.removal_text(done)
    assert ", ".join(want) in text and "configuration files stay" in text


@POSIX
def test_a_removal_that_would_take_other_packages_is_refused_and_removes_nothing(tmp_path):
    from orchestrator.remote import openssh_switch
    run, calls = _package_board(tmp_path, installed=("dropbear", "dropbear-bin", "dropbear-run"),
                                extra_removed=("dropbear-initramfs",))
    done = openssh_switch.remove_dropbear(run, "")
    assert (done.ok, done.removal, done.reason) == (True, "refused", "dependents"), done
    assert not [c for c in calls() if c.startswith("apt-get remove")], calls()
    assert "would also remove other packages" in openssh_switch.removal_text(done)


@POSIX
def test_a_package_manager_that_fails_is_reported_plainly_and_dropbear_stays_disabled(tmp_path):
    from orchestrator.remote import openssh_switch
    run, calls = _package_board(tmp_path, installed=("dropbear",), remove_fails=True)
    done = openssh_switch.remove_dropbear(run, "pw-once")
    assert (done.ok, done.removal, done.reason, done.packages) == (True, "refused", "remove", ("dropbear",))
    assert [c for c in calls() if c.startswith("apt-get remove")], calls()
    text = openssh_switch.removal_text(done)
    assert text.startswith("Dropbear is off (stopped and disabled by the init system)") and "stay installed" in text


@POSIX
def test_without_a_package_manager_the_init_system_alone_turns_dropbear_off(tmp_path):
    from orchestrator.remote import openssh_switch
    run, calls = _package_board(tmp_path, tool="none")
    done = openssh_switch.remove_dropbear(run, "")
    assert (done.ok, done.removal, done.reason) == (True, "none", "no-manager"), done
    assert any("disable --now dropbear" in c or "rc-service dropbear stop" in c for c in calls()), calls()
    assert "no package manager the framework knows" in openssh_switch.removal_text(done)
    run, calls = _package_board(tmp_path / "dpkg-only", installed=("dropbear",), manager=False)
    assert openssh_switch.remove_dropbear(run, "").reason == "no-manager"   # dpkg without apt-get


@POSIX
@pytest.mark.parametrize("tool, installed, command", [
    ("pacman", ("dropbear",), "pacman -R --noconfirm dropbear"),
    ("rpm", ("dropbear",), "dnf remove -y --noautoremove dropbear")])
def test_every_package_manager_removes_only_dropbears_packages(tmp_path, tool, installed, command):
    from orchestrator.remote import openssh_switch
    run, calls = _package_board(tmp_path, tool=tool, installed=installed)
    done = openssh_switch.remove_dropbear(run, "pw-once")
    assert done.removed and done.packages == installed, (done, calls())
    assert command in calls() and _disabled_first(calls())


def test_a_removal_without_an_answer_in_time_does_not_claim_dropbear_still_runs():
    """Regression (review): the disable runs before the removal, so after a timeout Dropbear may already be stopped;
    the text must not say it keeps running."""
    import socket as _socket

    from orchestrator.remote import openssh_switch

    def silent(script, stdin=None, timeout=None):
        assert timeout == openssh_switch.INSTALL_TIMEOUT
        raise _socket.timeout()
    done = openssh_switch.remove_dropbear(silent, "")
    assert (done.ok, done.reason) == (False, "remove-timeout")
    text = openssh_switch.removal_text(done)
    assert "keeps running next to OpenSSH" not in text and "may already be stopped" in text


def test_the_deprecated_disable_dropbear_delegates_and_keeps_its_old_answer():
    """Deprecate-and-delegate (review): disable_dropbear runs remove_dropbear and answers (ok, reason) as before,
    with no reason when Dropbear is off, whatever happened to its packages."""
    from orchestrator.remote import gate, openssh_switch
    answers = {"off": "dropbear=disabled removal=none reason=no-packages packages=none",
               "gone": "dropbear=removed removal=removed reason=- packages=dropbear",
               "no": "dropbear=unavailable reason=no-sudo"}
    for key, want in (("off", (True, "")), ("gone", (True, "")), ("no", (False, "no-sudo"))):
        seen = []

        def run(script, stdin=None, timeout=None, _key=key):
            seen.append(str(script))
            return SimpleNamespace(out=answers[_key] + "\n")
        assert openssh_switch.disable_dropbear(run, "") == want, key
        assert seen == [str(gate.script("dropbear-disable"))]
    assert openssh_switch.disable_dropbear(lambda *a, **k: None, "a\nb") == (False, "sudo")


@POSIX
@pytest.mark.parametrize("tool", ["apk", "rpm"])
def test_apk_and_rpm_refuse_a_removal_that_takes_other_packages(tmp_path, tool):
    from orchestrator.remote import openssh_switch
    run, calls = _package_board(tmp_path, tool=tool, installed=("dropbear",), extra_removed=("busybox-extras",))
    done = openssh_switch.remove_dropbear(run, "")
    assert (done.removal, done.reason) == ("refused", "dependents"), (done, calls())
    assert not [c for c in calls() if c.startswith(("apk del dropbear", "dnf remove"))], calls()


@POSIX
@pytest.mark.parametrize("extra", [(), ("libutmps",)])
def test_alpine_is_disable_only_and_says_why_in_plain_words(tmp_path, extra):
    """The maintainer's decision (Alpine stays disable-only; this changed on purpose: before, apk del ran when its
    simulation named only Dropbear's packages): apk never removes Dropbear, because it would also remove the
    libraries only Dropbear pulled in. Dropbear is stopped and switched off, the refusal keeps the reason dependents,
    and the text says it in plain words."""
    from orchestrator.remote import openssh_switch
    run, calls = _package_board(tmp_path, tool="apk", installed=("dropbear", "dropbear-openrc"), extra_removed=extra)
    done = openssh_switch.remove_dropbear(run, "pw-once")
    assert (done.ok, done.removal, done.reason, done.tool) == (True, "refused", "dependents", "apk"), (done, calls())
    assert done.packages == ("dropbear", "dropbear-openrc")
    assert not [c for c in calls() if c.startswith("apk del")], calls()
    assert any("rc-service dropbear stop" in c for c in calls())
    text = openssh_switch.removal_text(done)
    assert "stopped and switched off but not removed" in text and "libraries" in text and "Alpine" in text
    assert done.as_dict()["tool"] == "apk"


def test_the_switch_plan_on_alpine_says_dropbear_is_only_switched_off():
    from orchestrator.remote import key_tiers, openssh_switch
    facts = key_tiers.TierFacts.parse("running=dropbear\nopenssh=no\nsudo=nopass\nfamily=alpine\nmanager=apk\n"
                                      "dropbear_pkg=dropbear\ndropbear_pkg=dropbear-openrc\n")
    plan = openssh_switch.plan(facts)
    text = " ".join(plan.lines)
    assert "On Alpine it is not removed" in text and "libraries" in text
    assert "removes only the packages" not in text
    # every other family keeps its removal plan
    debian = openssh_switch.plan(key_tiers.TierFacts.parse("running=dropbear\nopenssh=no\nsudo=nopass\n"
                                                           "family=debian\nmanager=apt-get\ndropbear_pkg=dropbear\n"))
    assert "removes only the packages" in " ".join(debian.lines)


# --- the mode choice: installers, File > Settings..., `llmcf config set framework_mode` -----------------------------

from conftest import REPO_ROOT as REPO  # noqa: E402 - the repository root


def _config(tmp_path):
    from orchestrator.Core.config_manager import ConfigManager
    (tmp_path / "cfg").mkdir(exist_ok=True)
    return ConfigManager(config_dir=tmp_path / "cfg")


def test_the_person_sets_the_mode_and_it_is_saved_to_config_yml(tmp_path, monkeypatch):
    """Regression (failed before: framework_mode was not among the settings save_user_config writes, so a mode set at
    runtime was lost at the next start, and nothing but a hand-edited config.yml could set it)."""
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "home"))
    import yaml

    from orchestrator.Core.config_manager import framework_mode, set_framework_mode
    config = _config(tmp_path)
    assert set_framework_mode(config, " Enterprise ") == "enterprise"
    saved = yaml.safe_load((tmp_path / "cfg" / "config.yml").read_text(encoding="utf-8"))
    assert saved["framework_mode"] == "enterprise"
    assert framework_mode(_config(tmp_path)) == "enterprise"   # read again at the next start
    with pytest.raises(ValueError):
        set_framework_mode(config, "corporate")
    assert framework_mode(config) == "enterprise"


def test_an_ai_reads_the_mode_but_never_changes_it():
    from orchestrator.llm.settings_change import PERSON_ONLY_SETTINGS, person_only_text
    from orchestrator.tools.tools_status import READABLE
    assert "framework_mode" in READABLE and "framework_mode" in PERSON_ONLY_SETTINGS
    assert "never change" in person_only_text("framework_mode")


def test_the_cli_sets_the_mode_with_config_set(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from orchestrator import cli
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("LLM_FRAMEWORK_CONFIG_DIR", str(tmp_path / "cfg"))
    (tmp_path / "cfg").mkdir()
    done = CliRunner().invoke(cli.cli, ["config", "set", "framework_mode", "enterprise"])
    assert done.exit_code == 0, done.output
    assert "framework_mode: enterprise" in done.output
    assert "framework_mode: enterprise" in (tmp_path / "cfg" / "config.yml").read_text(encoding="utf-8")
    refused = CliRunner().invoke(cli.cli, ["config", "set", "framework_mode", "corporate"])
    assert refused.exit_code == 2 and "private or enterprise" in refused.output
    assert CliRunner().invoke(cli.cli, ["config", "set", "offline_mode", "true"]).exit_code == 2   # not a key here


def test_file_settings_shows_the_mode_and_the_person_changes_it(qapp, tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "home"))
    from orchestrator.Core.config_manager import framework_mode
    from orchestrator.gui.settings_window import SettingsDialog
    config = _config(tmp_path)
    dialog = SettingsDialog(SimpleNamespace(config=config), lang="en")
    try:
        assert dialog.cmb_mode.currentData() == "private"
        dialog.cmb_mode.setCurrentIndex(dialog.cmb_mode.findData("enterprise"))
        assert framework_mode(config) == "enterprise" and "Saved" in dialog.lbl_mode_result.text()
        assert "framework_mode: enterprise" in (tmp_path / "cfg" / "config.yml").read_text(encoding="utf-8")
    finally:
        dialog.deleteLater()


BASH = pytest.mark.skipif(os.name == "nt" or not shutil.which("bash"), reason="install.sh is a bash program")


def _install_sh(tmp_path, body, *args):
    script = f'source "$1"; shift; {body}'
    return subprocess.run(["bash", "-c", script, "test", str(REPO / "install.sh"), *args], capture_output=True,
                          text=True, timeout=60, env={"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)})


@BASH
def test_install_sh_asks_the_mode_with_private_as_the_default_and_takes_a_flag(tmp_path):
    answer = tmp_path / "tty"
    for typed, expected in (("e\n", "enterprise"), ("\n", "private"), ("enterprise\n", "enterprise")):
        answer.write_text(typed, encoding="utf-8")
        done = _install_sh(tmp_path, f'ASK_TTY="{answer}"; ask_mode; echo "MODE=$MODE_CHOICE"')
        assert f"MODE={expected}" in done.stdout, done.stdout + done.stderr
    # no terminal to ask (an unattended run): the mode stays as it is
    done = _install_sh(tmp_path, f'ASK_TTY="{tmp_path}/none"; ask_mode; echo "MODE=[$MODE_CHOICE]"')
    assert "MODE=[]" in done.stdout
    # the flag for unattended runs, and an unknown value stops before anything is installed
    done = _install_sh(tmp_path, 'ditto_option "$@"; ask_mode; echo "MODE=$MODE_CHOICE"', "--mode=enterprise")
    assert "MODE=enterprise" in done.stdout
    done = _install_sh(tmp_path, 'ditto_option "$@"', "--mode=corporate")
    assert done.returncode == 1 and "--mode" in done.stdout


@BASH
def test_install_sh_sets_the_chosen_mode_through_the_cli(tmp_path):
    venv = tmp_path / "inst" / ".venv" / "bin"
    venv.mkdir(parents=True)
    log = tmp_path / "calls"
    (venv / "python").write_text(f'#!/bin/sh\necho "$*" >> "{log}"\n', encoding="utf-8")
    (venv / "python").chmod(0o755)
    done = _install_sh(tmp_path, f'INSTALL_DIR="{tmp_path}/inst"; CONTAINER_BUILD=1; MODE_CHOICE=enterprise; '
                                 'setup_mode')
    assert "-m orchestrator.cli config set framework_mode enterprise" in log.read_text(encoding="utf-8"), done.stdout
    log.unlink()
    _install_sh(tmp_path, f'INSTALL_DIR="{tmp_path}/inst"; CONTAINER_BUILD=1; MODE_CHOICE=""; setup_mode')
    assert not log.exists()   # nothing chosen: nothing set


def test_the_windows_installer_takes_the_mode_flag_asks_and_sets_it_through_the_cli():
    text = (REPO / "install.bat").read_text(encoding="utf-8")
    assert '--mode=*' in text and "LCF_MODE" in text and "Read-Host" in text
    assert "config set framework_mode $ModeChoice" in text
    assert '@("", "private", "enterprise")' in text
    # Regression (failed before: an unattended run with its input redirected read an empty line from Read-Host and
    # set private mode over an enterprise one); without a console to ask, the mode stays as it is
    assert "[Environment]::UserInteractive -and -not [Console]::IsInputRedirected" in text


def _old_install(tmp_path, mode):
    """An earlier installation at INSTALL_DIR whose .venv Python answers the mode reader (ConfigManager +
    framework_mode) with `mode`, and logs how it was called."""
    venv = tmp_path / "inst" / ".venv" / "bin"
    venv.mkdir(parents=True, exist_ok=True)
    log = tmp_path / "py-calls"
    script = f'#!/bin/sh\necho "$*" >> "{log}"\necho {mode}\n'
    (venv / "python").write_bytes(script.encode("utf-8"))
    (venv / "python").chmod(0o755)
    return log


def _ask(tmp_path, folder, answer):
    return _install_sh(tmp_path, f'INSTALL_DIR="{tmp_path}/{folder}"; CONTAINER_BUILD=1; ASK_TTY="{answer}"; ask_mode; '
                                 'echo "MODE=$MODE_CHOICE"')


@BASH
def test_install_sh_keeps_the_current_mode_on_an_empty_answer_of_an_update(tmp_path):
    """Regression (failed before: an empty answer set private mode over an enterprise installation): on an update the
    current mode, read with the installation's own mode reader, is the default; the prompt shows it ([p/E])."""
    answer = tmp_path / "tty"
    log = _old_install(tmp_path, "enterprise")
    for typed, expected in (("\n", "enterprise"), ("  \n", "enterprise"), ("p\n", "private"), ("e\n", "enterprise"),
                            ("private\n", "private")):
        answer.write_bytes(typed.encode("utf-8"))
        done = _ask(tmp_path, "inst", answer)
        assert f"MODE={expected}" in done.stdout, (typed, done.stdout + done.stderr)
        assert "[p/E]" in done.stdout and "enterprise (jetzt eingestellt)" in done.stdout
    assert "framework_mode(ConfigManager())" in log.read_text(encoding="utf-8")
    # an earlier private installation: [P/e], Enter keeps private
    _old_install(tmp_path, "private")
    answer.write_bytes(b"\n")
    done = _ask(tmp_path, "inst", answer)
    assert "MODE=private" in done.stdout and "[P/e]" in done.stdout and "privat (jetzt eingestellt)" in done.stdout


@BASH
def test_install_sh_fresh_install_and_an_unreadable_mode_default_to_private(tmp_path):
    answer = tmp_path / "tty"
    answer.write_bytes(b"\n")
    # fresh: no earlier .venv at INSTALL_DIR
    done = _ask(tmp_path, "fresh", answer)
    assert "MODE=private" in done.stdout and "[P/e]" in done.stdout and "privat (Standard)" in done.stdout
    # an earlier installation whose Python answers nothing the reader knows: private stays the default
    _old_install(tmp_path, "garbage")
    done = _ask(tmp_path, "inst", answer)
    assert "MODE=private" in done.stdout and "[P/e]" in done.stdout
    # a flag never asks and never reads
    log = _old_install(tmp_path, "enterprise")
    if log.exists():
        log.unlink()
    done = _install_sh(tmp_path, f'INSTALL_DIR="{tmp_path}/inst"; CONTAINER_BUILD=1; MODE_CHOICE=private; ask_mode; '
                                 'echo "MODE=$MODE_CHOICE"')
    assert "MODE=private" in done.stdout and not log.exists()


def test_the_windows_installer_keeps_the_current_mode_on_an_empty_answer():
    """Regression (failed before: Enter alone picked private over an enterprise installation)."""
    text = (REPO / "install.bat").read_text(encoding="utf-8")
    assert "framework_mode(ConfigManager())" in text and "$CurrentMode" in text
    assert '"[p/E]"' in text and '"[P/e]"' in text
    assert "else { $ModeDefault }" in text
