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


# --- the switch on ssh.socket systems, and Dropbear's removal on DietPi ----------------------------------------------

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


def test_dropbears_dietpi_id_is_one_named_constant_with_a_fallback_to_the_init_system():
    from orchestrator.remote import gate, openssh_switch
    body = str(gate.script("dropbear-disable"))
    assert f"uninstall {openssh_switch.DIETPI_DROPBEAR_ID}" in body and "104" not in body.replace(
        f"uninstall {openssh_switch.DIETPI_DROPBEAR_ID}", "")
    assert body.index("dietpi-software") < body.index("systemctl disable --now dropbear")
    assert "confirmed on a real DietPi board" in open(openssh_switch.__file__, encoding="utf-8").read()


@POSIX
@pytest.mark.parametrize("dietpi_ok", [True, False])
def test_dropbear_goes_through_dietpi_software_or_else_the_init_system(tmp_path, dietpi_ok):
    """A stand-in board: sudo writes down what it was asked (nothing runs as root), dietpi-software succeeds or
    refuses; when it refuses (or is absent), the dropbear service is stopped and disabled by the init system."""
    from orchestrator.remote import gate, openssh_switch
    log = tmp_path / "sudo.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stubs = {"sudo": f'echo "$*" >> "{log}"; cat >/dev/null; case "$*" in *dietpi-software*) exit {0 if dietpi_ok else 1};; '
                     "esac; exit 0",
             "dietpi-software": "exit 0", "rc-service": "exit 0", "rc-update": "exit 0"}
    for name, body in stubs.items():
        (bin_dir / name).write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
        (bin_dir / name).chmod(0o755)
    done = subprocess.run(["sh", "-c", str(gate.script("dropbear-disable"))], input=b"pw\n", capture_output=True,
                          timeout=60, env={"PATH": f"{bin_dir}{os.pathsep}/usr/bin:/bin", "HOME": str(tmp_path)})
    calls = log.read_text(encoding="utf-8")
    assert f"uninstall {openssh_switch.DIETPI_DROPBEAR_ID}" in calls
    fallback = "disable --now dropbear" in calls or "rc-service dropbear stop" in calls
    assert fallback is (not dietpi_ok), calls
    assert b"dropbear=disabled" in done.stdout


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
