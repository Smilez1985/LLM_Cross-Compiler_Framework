"""
The login is bound to the checked key (DESIGN, No dead code: the last open finding, decided by the maintainer on
2026-11-28): RemoteSession.open reads the board's host key over a connection of its own and checks it (known_hosts,
the fingerprint shown, the board meant), then logs in over a second connection. The key of that authenticated
connection (Transport.host_key) must be the checked key; another one closes the login with SessionFailure "other_key"
and the reason code of a changed host key. A transport that gives no key (OpenSSH checks it itself, a stand-in) is not
compared.
"""

from types import SimpleNamespace

import pytest

paramiko = pytest.importorskip("paramiko")

from orchestrator.remote import reasons  # noqa: E402
from orchestrator.remote.address import Endpoint  # noqa: E402
from orchestrator.remote.session import RemoteSession, SessionFailure  # noqa: E402
from orchestrator.remote.transport import ParamikoTransport  # noqa: E402

CHECKED = paramiko.ECDSAKey.generate()
OTHER = paramiko.ECDSAKey.generate()


class KeyedTransport:
    """A transport whose authenticated connection presents `key`."""

    def __init__(self, key):
        self.host_key, self.closed = key, False

    def alive(self):
        return not self.closed

    def close(self):
        self.closed = True


@pytest.fixture
def board(monkeypatch):
    """The board presents CHECKED to the key read and is trusted with it; the login's key is set per test."""
    from orchestrator.utils import ssh_trust
    monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port=22, timeout=10.0: CHECKED)
    monkeypatch.setattr(ssh_trust, "check_host_key", lambda state, host, port, key: "known")
    logins = []
    monkeypatch.setattr(ssh_trust, "connect", lambda host, user, state, port=22, password=None:
                        logins.append(host) or SimpleNamespace(close=lambda: None))
    return logins


def session(tmp_path, login_key, handed):
    made = []

    def factory(client):
        made.append(KeyedTransport(login_key))
        return made[-1]
    return RemoteSession(Endpoint("192.0.2.30"), "dietpi", tmp_path, transport_factory=factory,
                         on_client=handed.append), made


def test_a_login_with_the_checked_key_is_kept(tmp_path, board):
    handed = []
    s, made = session(tmp_path, CHECKED, handed)
    s.open()
    assert board == ["192.0.2.30"] and s.transport is made[0] and not made[0].closed and len(handed) == 1


def test_a_login_with_another_key_is_closed_as_a_changed_host_key(tmp_path, board):
    from orchestrator.utils import ssh_trust
    handed = []
    s, made = session(tmp_path, OTHER, handed)
    with pytest.raises(SessionFailure) as raised:
        s.open()
    assert raised.value.kind == "other_key" and raised.value.reason == reasons.HOST_KEY_CHANGED
    assert ssh_trust.fingerprint(OTHER) in str(raised.value) and ssh_trust.fingerprint(CHECKED) in str(raised.value)
    assert made[0].closed and s.transport is None and s.client is None and handed == []


def test_a_transport_that_gives_no_key_is_not_compared(tmp_path, board):
    handed = []
    s, made = session(tmp_path, None, handed)
    s.open()
    assert s.transport is made[0] and len(handed) == 1


def test_the_paramiko_transport_gives_the_connections_key_and_a_stand_in_none():
    client = SimpleNamespace(get_transport=lambda: SimpleNamespace(get_remote_server_key=lambda: CHECKED))
    assert ParamikoTransport(client).host_key is CHECKED
    assert ParamikoTransport(SimpleNamespace()).host_key is None
    assert ParamikoTransport(SimpleNamespace(get_transport=lambda: None)).host_key is None
