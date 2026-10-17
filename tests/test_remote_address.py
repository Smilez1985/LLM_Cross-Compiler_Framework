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


def _host_patterns():
    from orchestrator.Core import deployment_manager
    from orchestrator.runtime import remote_probe
    from orchestrator.runtime.device import trusted
    return {"deployment_manager": deployment_manager._HOST, "remote_probe": remote_probe._HOST,
            "trusted": trusted._HOST}


def test_the_three_host_patterns_are_one_pattern():
    patterns = _host_patterns()
    assert len({p.pattern for p in patterns.values()}) == 1
    assert len({p.flags for p in patterns.values()}) == 1


@pytest.mark.parametrize("host", HOSTS_ACCEPTED + HOSTS_REFUSED_BY_VALID_HOST[:-1])
def test_every_host_pattern_accepts_the_shape(host):
    for where, pattern in _host_patterns().items():
        assert pattern.fullmatch(host), (where, host)


@pytest.mark.parametrize("host", HOSTS_REFUSED_BY_SHAPE)
def test_every_host_pattern_refuses(host):
    for where, pattern in _host_patterns().items():
        assert not pattern.fullmatch(host), (where, host)


@pytest.mark.parametrize("host", HOSTS_ACCEPTED)
def test_valid_host_accepts(host):
    from orchestrator.runtime.remote_probe import valid_host
    assert valid_host(host)


@pytest.mark.parametrize("host", HOSTS_REFUSED_BY_SHAPE + HOSTS_REFUSED_BY_VALID_HOST + [None, 22])
def test_valid_host_refuses(host):
    from orchestrator.runtime.remote_probe import valid_host
    assert not valid_host(host)


def test_a_stored_record_checks_only_the_shape():
    """TrustedDevice.check refuses what no copy accepts, but keeps a record whose host is only shaped like an address
    (it never re-checks with the stricter valid_host: a record the person once trusted is not dropped silently)."""
    from orchestrator.runtime.device.trusted import DeviceStoreError, TrustedDevice
    from orchestrator.runtime.profile import device_id_for_host_key
    fp = "SHA256:" + "A" * 43

    def record(host, port=22, user="dietpi"):
        return TrustedDevice(device_id=device_id_for_host_key(fp), label="Shelf", host=host, port=port, user=user,
                             key_type="ssh-ed25519", fingerprint=fp, paired=True, trusted_at="2026-10-17T10:00:00Z",
                             trusted_via="cli")

    assert record("192.168.1.300").check()
    assert record("fe80::1", 2222).check()
    for bad in ("-a", "[fe80::1]", "a b"):
        with pytest.raises(DeviceStoreError, match="host"):
            record(bad).check()
    for port in (0, 65536, True, "22"):
        with pytest.raises(DeviceStoreError, match="port"):
            record("192.0.2.1", port).check()
    for user in ("1abc", "-o", "ä"):
        with pytest.raises(DeviceStoreError, match="user"):
            record("192.0.2.1", user=user).check()


def test_the_deploy_refuses_what_valid_host_refuses(tmp_path):
    """deployment_manager checks its own pattern and valid_host; together they refuse what valid_host refuses."""
    from orchestrator.Core.deployment_manager import DeploymentManager
    from test_deployment import FakeFramework
    from orchestrator.Core.config_manager import ConfigManager
    dm = DeploymentManager(FakeFramework(ConfigManager(tmp_path / "configs")))
    for host in HOSTS_REFUSED_BY_SHAPE + HOSTS_REFUSED_BY_VALID_HOST:
        result = dm.deploy_package(tmp_path / "none.zip", host, "dietpi")
        assert not result.success and "Invalid address or host name" in result.reason, host


# --- what every user check accepts and refuses ----------------------------------------------------------------------

USERS_ACCEPTED = ["dietpi", "Pi", "_svc", "a.b-c_d", "root", "a" * 32, "orangepi", "x1"]
USERS_REFUSED = ["", "1abc", "-oProxyCommand=x", "a b", "a;b", "a" * 33, "ä", "a/b", "$(id)", ".x", "a@b"]


def _user_checks():
    """Each copy's check as it is called: deployment_manager and the GUI's deployment window with match(), the others
    with fullmatch()."""
    from orchestrator.Core import deployment_manager
    from orchestrator.runtime.device import launcher, settings, trusted
    from orchestrator.tools import tools_runtime
    return {"deployment_manager": lambda u: bool(deployment_manager.SSH_USER_PATTERN.match(u)),
            "trusted": lambda u: bool(trusted._USER.fullmatch(u)),
            "settings": lambda u: bool(settings._RE_USER.fullmatch(u)),
            "launcher": lambda u: bool(launcher._RE_USER.fullmatch(u)),
            "tools_runtime": lambda u: bool(tools_runtime._ACCOUNT.fullmatch(u))}


@pytest.mark.parametrize("user", USERS_ACCEPTED)
def test_every_user_check_accepts(user):
    for where, check in _user_checks().items():
        assert check(user), (where, user)


@pytest.mark.parametrize("user", USERS_REFUSED)
def test_every_user_check_refuses(user):
    for where, check in _user_checks().items():
        assert not check(user), (where, user)


@pytest.mark.parametrize("user", ["ſudo", "piK", "ıx"])
def test_the_ascii_user_checks_refuse_letters_that_only_fold_to_ascii(user):
    """trusted, settings and launcher compile the user pattern with re.ASCII: a long s or the Kelvin sign is no
    account name there."""
    checks = _user_checks()
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
    record = _record(host, port)
    assert record.address == stored


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


def _record(host, port):
    from orchestrator.runtime.device.trusted import TrustedDevice
    from orchestrator.runtime.profile import device_id_for_host_key
    fp = "SHA256:" + "B" * 43
    return TrustedDevice(device_id=device_id_for_host_key(fp), label="Shelf", host=host, port=port, user="dietpi",
                         key_type="ssh-ed25519", fingerprint=fp, paired=True, trusted_at="2026-10-17T10:00:00Z",
                         trusted_via="gui")


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
