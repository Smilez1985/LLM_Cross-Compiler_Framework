"""A board's address and SSH user (consolidation chain c2-remote-base): the host check, the user check and the host:port
forms each existed several times (deployment_manager, remote_probe, trusted, settings, launcher, tools_runtime,
ssh_trust, devices, the Boards window). These tests pin what every copy accepts and refuses and which form each place
writes, so the copies can become calls of one implementation without a change anybody sees.
"""

import pytest

# --- what every host check accepts and refuses ----------------------------------------------------------------------

HOSTS_ACCEPTED = ["192.168.1.50", "10.0.0.5", "myboard.local", "board-1", "a", "fe80::1", "2001:db8::42",
                  "::1", "radxa-zero3.lan"]
# Refused by the shape alone (every copy).
HOSTS_REFUSED_BY_SHAPE = ["", "[fe80::1]", "-oProxyCommand=x", "a b", "host;id", "host/x", "board.local.",
                          "x" * 254, "ü.local", "host:22:x$"]
# Shaped like a host, but no address and no name: refused only by the full check (valid_host).
HOSTS_REFUSED_BY_VALID_HOST = ["192.168.1.300", "1.2.3", "fe80::1::2", "a..b", "-a"]


def _record(host="192.0.2.1", port=22, user="dietpi"):
    from orchestrator.runtime.device.trusted import TrustedDevice
    from orchestrator.runtime.profile import device_id_for_host_key
    fp = "SHA256:" + "A" * 43
    return TrustedDevice(device_id=device_id_for_host_key(fp), label="Shelf", host=host, port=port, user=user,
                         key_type="ssh-ed25519", fingerprint=fp, paired=True, trusted_at="2026-10-17T10:00:00Z",
                         trusted_via="cli")


def _record_takes(**fields) -> bool:
    from orchestrator.runtime.device.trusted import DeviceStoreError
    try:
        _record(**fields).check()
    except DeviceStoreError:
        return False
    return True


def _deploy(tmp_path):
    from orchestrator.Core.config_manager import ConfigManager
    from orchestrator.Core.deployment_manager import DeploymentManager
    from test_deployment import FakeFramework
    return DeploymentManager(FakeFramework(ConfigManager(tmp_path / "configs")))


@pytest.mark.parametrize("host", HOSTS_ACCEPTED + HOSTS_REFUSED_BY_VALID_HOST[:-1])
def test_a_stored_record_takes_every_host_of_the_right_shape(host):
    """TrustedDevice.check checks the shape only: a record the person once trusted is never dropped by the stricter
    valid_host later."""
    assert _record_takes(host=host)


@pytest.mark.parametrize("host", HOSTS_REFUSED_BY_SHAPE + ["-a"])
def test_every_host_check_refuses_the_wrong_shape(host, tmp_path):
    from orchestrator.runtime.remote_probe import valid_host
    assert not valid_host(host)
    assert not _record_takes(host=host)
    result = _deploy(tmp_path).deploy_package(tmp_path / "none.zip", host, "dietpi")
    assert not result.success and "Invalid address or host name" in result.reason


@pytest.mark.parametrize("host", HOSTS_ACCEPTED)
def test_valid_host_accepts(host):
    from orchestrator.runtime.remote_probe import valid_host
    assert valid_host(host)


@pytest.mark.parametrize("host", HOSTS_REFUSED_BY_SHAPE + HOSTS_REFUSED_BY_VALID_HOST + [None, 22])
def test_valid_host_refuses(host):
    from orchestrator.runtime.remote_probe import valid_host
    assert not valid_host(host)


def test_a_stored_record_names_the_field_it_refuses():
    from orchestrator.runtime.device.trusted import DeviceStoreError
    assert _record(host="fe80::1", port=2222).check()
    for bad in ("-a", "[fe80::1]", "a b"):
        with pytest.raises(DeviceStoreError, match="host"):
            _record(host=bad).check()
    for port in (0, 65536, True, "22", 22.0):
        with pytest.raises(DeviceStoreError, match="port"):
            _record(port=port).check()
    for user in ("1abc", "-o", "ä"):
        with pytest.raises(DeviceStoreError, match="user"):
            _record(user=user).check()


def test_the_deploy_refuses_what_valid_host_refuses(tmp_path):
    dm = _deploy(tmp_path)
    for host in HOSTS_REFUSED_BY_SHAPE + HOSTS_REFUSED_BY_VALID_HOST:
        result = dm.deploy_package(tmp_path / "none.zip", host, "dietpi")
        assert not result.success and "Invalid address or host name" in result.reason, host
    result = dm.deploy_package(tmp_path / "none.zip", "myboard.local", "dietpi")
    assert "does not exist" in result.reason, "a good address passes on to the next check"


# --- what every user check accepts and refuses ----------------------------------------------------------------------

USERS_ACCEPTED = ["dietpi", "Pi", "_svc", "a.b-c_d", "root", "a" * 32, "orangepi", "x1"]
USERS_REFUSED = ["", "1abc", "-oProxyCommand=x", "a b", "a;b", "a" * 33, "ä", "a/b", "$(id)", ".x", "a@b"]


def _user_checks(tmp_path):
    """Each place's own check of an SSH user, as it is called (root aside: the runtime refuses it on purpose, see
    test_root_is_refused_where_the_runtime_runs)."""
    from orchestrator.runtime.device.launcher import LauncherError, render_systemd_unit
    from orchestrator.runtime.device.person_steps import BoardStepError, check_board_entry
    from orchestrator.runtime.device.settings import SettingsError, check_ssh_user
    from orchestrator.tools.tools_runtime import DEVICE_ACCOUNT, device_text

    def takes(check, refusal):
        def run(user):
            try:
                check(user)
            except refusal:
                return False
            return True
        return run

    def deploy(user):
        result = _deploy(tmp_path).deploy_package(tmp_path / "none.zip", "myboard.local", user)
        return "Invalid SSH user name" not in result.reason

    def unit(user):
        render_systemd_unit(user=user, home="/home/x", model="/home/x/m.gguf", port=8080)

    return {"deployment_manager": deploy,
            "trusted": lambda u: _record_takes(user=u),
            "settings": lambda u: u == "root" or takes(check_ssh_user, SettingsError)(u),
            "launcher": lambda u: u == "root" or takes(unit, LauncherError)(u),
            "person_steps": takes(lambda u: check_board_entry("192.0.2.10", u, None), BoardStepError),
            "tools_runtime": lambda u: DEVICE_ACCOUNT in device_text(f"login as {u} failed", [u])}


@pytest.mark.parametrize("user", USERS_ACCEPTED)
def test_every_user_check_accepts(user, tmp_path):
    for where, check in _user_checks(tmp_path).items():
        assert check(user), (where, user)


@pytest.mark.parametrize("user", USERS_REFUSED)
def test_every_user_check_refuses(user, tmp_path):
    for where, check in _user_checks(tmp_path).items():
        if where == "tools_runtime" and not user.strip():
            continue  # an empty name replaces nothing either way
        assert not check(user), (where, user)


@pytest.mark.parametrize("user", ["ſudo", "pi\u212a", "\u0131x"])
def test_the_ascii_user_checks_refuse_letters_that_only_fold_to_ascii(user, tmp_path):
    """trusted, settings and launcher compile the user pattern with re.ASCII: a long s or the Kelvin sign is no
    account name there."""
    checks = _user_checks(tmp_path)
    for where in ("trusted", "settings", "launcher"):
        assert not checks[where](user), (where, user)


def test_root_is_refused_where_the_runtime_runs():
    from orchestrator.runtime.device.launcher import LauncherError, render_systemd_unit
    from orchestrator.runtime.device.settings import SettingsError, check_ssh_user
    assert check_ssh_user("dietpi") == "dietpi"
    with pytest.raises(SettingsError, match="root"):
        check_ssh_user("root")
    with pytest.raises(SettingsError, match="account name"):
        check_ssh_user("1abc")
    with pytest.raises(LauncherError, match="never root"):
        render_systemd_unit(user="root", home="/home/x", model="/home/x/m.gguf", port=8080)
    with pytest.raises(LauncherError, match="never root"):
        render_systemd_unit(user="1abc", home="/home/x", model="/home/x/m.gguf", port=8080)


def test_the_board_entry_check():
    from orchestrator.runtime.device.person_steps import BoardStepError, check_board_entry
    assert check_board_entry("192.0.2.10", "dietpi", None) is None
    assert check_board_entry("fe80::1", "root", "  Shelf   board ") == "Shelf board"
    for host, user in (("[fe80::1]", "dietpi"), ("192.0.2.10", "1abc"), ("192.0.2.10", ""), ("-x", "dietpi")):
        with pytest.raises(BoardStepError) as e:
            check_board_entry(host, user, None)
        assert e.value.kind == "host_user"
    with pytest.raises(BoardStepError) as e:
        check_board_entry("192.0.2.10", "dietpi", "x" * 65)
    assert e.value.kind == "label"


def test_device_text_replaces_each_account_name_it_accepts():
    from orchestrator.tools.tools_runtime import DEVICE_ACCOUNT, device_text
    assert device_text("login as dietpi failed", ["dietpi"]) == f"login as {DEVICE_ACCOUNT} failed"
    assert device_text("login as 1abc failed", ["1abc"]) == "login as 1abc failed", "not an account name: kept"


# --- the host:port forms ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("host, port, known_hosts, stored", [
    ("192.0.2.10", 22, "192.0.2.10", "192.0.2.10"),
    ("192.0.2.10", 2222, "[192.0.2.10]:2222", "192.0.2.10:2222"),
    ("myboard.local", 2222, "[myboard.local]:2222", "myboard.local:2222"),
    ("fe80::1", 22, "fe80::1", "fe80::1"),
    ("fe80::1", 2222, "[fe80::1]:2222", "[fe80::1]:2222"),
])
def test_the_forms(host, port, known_hosts, stored):
    from orchestrator.runtime.device.devices import split_address
    from orchestrator.runtime.remote_probe import store_address
    from orchestrator.utils import ssh_trust
    assert ssh_trust.host_entry(host, port) == known_hosts
    assert store_address(host, port) == stored
    assert split_address(stored) == (host, port), "the stored form reads back"
    assert _record(host=host, port=port).address == stored


def test_the_stored_form_strips_brackets_someone_typed():
    from orchestrator.runtime.remote_probe import store_address
    assert store_address("[fe80::1]", 2222) == "[fe80::1]:2222"
    assert store_address("[fe80::1]", 22) == "[fe80::1]", "port 22 keeps the host as it came"


def test_the_known_hosts_form_takes_the_default_port():
    from orchestrator.utils import ssh_trust
    assert ssh_trust.host_entry("192.0.2.10") == "192.0.2.10"


@pytest.mark.parametrize("address, expected", [("192.0.2.10", ("192.0.2.10", 22)),
                                               ("board.local:2222", ("board.local", 2222)),
                                               ("[fe80::1]:2222", ("fe80::1", 2222)),
                                               ("[fe80::1]", ("fe80::1", 22)),
                                               ("fe80::1", ("fe80::1", 22)),
                                               ("board.local:65535", ("board.local", 65535))])
def test_reading_a_stored_address(address, expected):
    from orchestrator.runtime.device.devices import split_address
    assert split_address(address) == expected


@pytest.mark.parametrize("address", [None, "", "host:99999", "host:x", "host:0", ":22", "[]:22", "[fe80::1]:x"])
def test_reading_a_bad_stored_address(address):
    from orchestrator.runtime.device.devices import split_address
    with pytest.raises(ValueError):
        split_address(address)


@pytest.fixture
def window(qapp, tmp_path):
    """The Boards window as tests/test_boards_window.py opens it."""
    pytest.importorskip("PySide6")
    from pathlib import Path
    from test_boards_window import framework, wait
    from orchestrator.gui.boards_window import BoardsWindow
    fw = framework(tmp_path)
    Path(fw.config.user_state_dir).mkdir(parents=True)
    dialog = BoardsWindow(fw, lang="en")
    yield dialog
    if dialog.running:
        dialog._on_cancel()
        wait(qapp, dialog)
    dialog.deleteLater()
    qapp.processEvents()


def test_the_boards_window_names_a_board_by_its_stored_form(qapp, window, monkeypatch):
    """The Boards window's "not trusted yet" message names the board as the profile store keeps the address."""
    from test_boards_window import Boxes, FakeHostKey, wait
    from orchestrator.runtime.device import person_steps
    monkeypatch.setattr(person_steps, "read_host_key", lambda state, host, port: (FakeHostKey(), "unknown", "SHA256:x"))
    boxes = Boxes(monkeypatch)
    window.add_board("fe80::1", "dietpi", 2222, None)
    wait(qapp, window)
    assert "[fe80::1]:2222" in boxes.asked[-1].text
    window.add_board("myboard.local", "dietpi", 22, None)
    wait(qapp, window)
    assert "myboard.local" in boxes.asked[-1].text and "myboard.local:" not in boxes.asked[-1].text
