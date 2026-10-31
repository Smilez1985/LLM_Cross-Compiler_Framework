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
