"""Enterprise mode refuses a board whose framework key sits on a weaker tier when the board is USED, not only when it
is paired (remote/key_tiers.py use_problem, runtime/device/trusted.py tier_refusal): the person's board steps
(person_steps.connect_stored, behind the CLI and the Boards window), the AI's device service (service.connect) and the
deploy of a package (DeploymentManager) each take the framework mode of their framework and refuse before anything runs
on the board; the refusal names the ways on. The listings show the tier (`llmcf device list`, `llmcf deploy devices
list`, the Boards window's Key tier column).

Every board here is a stand-in (no network, no SSH server); documentation names and addresses only.
"""

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
