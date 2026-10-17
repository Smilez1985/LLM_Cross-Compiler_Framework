"""SSH trust: fingerprints, trust on first use (never silent), changed keys, framework key, pairing."""

import os
import shutil
import socket
import subprocess
import threading

import pytest

paramiko = pytest.importorskip("paramiko")

from orchestrator.utils import ssh_trust  # noqa: E402


@pytest.fixture
def state(tmp_path):
    return tmp_path / "state"


class TestHostKeys:

    def test_fingerprint_matches_ssh_keygen(self, tmp_path):
        key = paramiko.ECDSAKey.generate()
        pub = tmp_path / "k.pub"
        pub.write_text(f"{key.get_name()} {key.get_base64()}\n")
        if not shutil.which("ssh-keygen"):
            pytest.skip("ssh-keygen not available")
        out = subprocess.run(["ssh-keygen", "-lf", str(pub)], capture_output=True, text=True, check=True).stdout
        assert ssh_trust.fingerprint(key) in out

    def test_unknown_trusted_and_changed(self, state):
        key, other = paramiko.ECDSAKey.generate(), paramiko.ECDSAKey.generate()
        assert ssh_trust.check_host_key(state, "10.0.0.5", 22, key) == "unknown"
        ssh_trust.trust_host_key(state, "10.0.0.5", 22, key)
        assert ssh_trust.check_host_key(state, "10.0.0.5", 22, key) == "known"
        assert ssh_trust.check_host_key(state, "10.0.0.5", 22, other) == "changed"
        assert ssh_trust.check_host_key(state, "10.0.0.5", 2222, key) == "unknown", "ports are separate entries"
        if os.name == "posix":
            assert ssh_trust.known_hosts_path(state).stat().st_mode & 0o077 == 0

    def test_other_key_type_for_a_known_device_counts_as_changed(self, state):
        ssh_trust.trust_host_key(state, "10.0.0.5", 22, paramiko.ECDSAKey.generate())
        assert ssh_trust.check_host_key(state, "10.0.0.5", 22, paramiko.RSAKey.generate(2048)) == "changed"

    def test_framework_key_is_private_and_stable(self, state):
        private, public = ssh_trust.client_key(state)
        assert public.startswith("ssh-ed25519 ") and ssh_trust.client_key(state) == (private, public)
        paramiko.Ed25519Key.from_private_key_file(str(private))
        if os.name == "posix":
            assert private.stat().st_mode & 0o077 == 0

    def test_install_key_command_is_quoted_and_idempotent(self):
        seen = []

        class Channel:
            def recv_exit_status(self):
                return 0

        class Out:
            channel = Channel()

        class Client:
            def exec_command(self, command):
                seen.append(command)
                return None, Out(), Out()

        # A line that is not a plain public key never reaches the device's shell (bandit B601: the command is fixed,
        # its only value checked and quoted).
        for bad in ("ssh-ed25519 AAAA'; rm -rf ~; echo ' x", "ssh-ed25519 AAAAexampleonlyfortests $(id)",
                    "ssh-ed25519 AAAAexampleonlyfortests\nrm -rf ~", "", None):
            with pytest.raises(ssh_trust.HostKeyError):
                ssh_trust.install_public_key(Client(), bad)
        assert seen == []
        line = "ssh-ed25519 AAAAexampleonlyfortests0000 llm-cross-compiler-framework"
        ssh_trust.install_public_key(Client(), line)
        assert seen == ["umask 077 && mkdir -p ~/.ssh && touch ~/.ssh/authorized_keys && "
                        f"(grep -qxF '{line}' ~/.ssh/authorized_keys || printf '%s\\n' '{line}' >> "
                        "~/.ssh/authorized_keys)"], seen

    def test_the_framework_key_line_passes_the_check(self, tmp_path):
        assert ssh_trust.PUBLIC_LINE.fullmatch(ssh_trust.client_key(tmp_path / "state")[1])


class FakeSSHServer(paramiko.ServerInterface):
    def __init__(self, password, keys):
        self.password, self.keys, self.commands = password, keys, []
        self.event = threading.Event()

    def check_auth_password(self, username, password):
        return paramiko.AUTH_SUCCESSFUL if password == self.password else paramiko.AUTH_FAILED

    def check_auth_publickey(self, username, key):
        return paramiko.AUTH_SUCCESSFUL if key.get_base64() in self.keys else paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "publickey,password"

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(self, channel, command):
        command = command.decode()
        self.commands.append(command)
        if "authorized_keys" in command:
            self.keys.add(command.split("ssh-ed25519 ", 1)[1].split(" ", 1)[0])

        def finish():  # after the exec request was acknowledged
            channel.send_exit_status(0)
            channel.close()
        threading.Timer(0.05, finish).start()
        return True


@pytest.fixture
def ssh_server():
    host_key = paramiko.ECDSAKey.generate()
    server = FakeSSHServer("pairing-pw", set())
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(5)
    stop = threading.Event()

    def serve():
        listener.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except (socket.timeout, OSError):
                if stop.is_set():
                    break
                continue
            transport = paramiko.Transport(conn)
            transport.add_server_key(host_key)
            try:
                transport.start_server(server=server)
            except Exception:
                continue

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield SimpleServer(listener.getsockname()[1], host_key, server)
    stop.set()
    listener.close()


class SimpleServer:
    def __init__(self, port, host_key, server):
        self.port, self.host_key, self.server = port, host_key, server


class TestRealHandshake:

    def test_unknown_device_is_refused_until_the_user_trusts_it(self, ssh_server, state):
        with pytest.raises(ssh_trust.UnknownHostKey) as info:
            ssh_trust.connect("127.0.0.1", "radxa", state, port=ssh_server.port, password="pairing-pw")
        assert info.value.fingerprint == ssh_trust.fingerprint(ssh_server.host_key)
        assert ssh_trust.check_host_key(state, "127.0.0.1", ssh_server.port, ssh_server.host_key) == "unknown"

        fetched = ssh_trust.fetch_host_key("127.0.0.1", ssh_server.port)
        assert ssh_trust.fingerprint(fetched) == ssh_trust.fingerprint(ssh_server.host_key)

    def test_pairing_installs_the_key_and_later_logins_need_no_password(self, ssh_server, state):
        ssh_trust.trust_host_key(state, "127.0.0.1", ssh_server.port, ssh_server.host_key)
        client = ssh_trust.connect("127.0.0.1", "radxa", state, port=ssh_server.port, password="pairing-pw")
        ssh_trust.install_public_key(client, ssh_trust.client_key(state)[1])
        client.close()
        client = ssh_trust.connect("127.0.0.1", "radxa", state, port=ssh_server.port)  # key only
        client.close()

    def test_changed_host_key_fails_hard(self, ssh_server, state):
        ssh_trust.trust_host_key(state, "127.0.0.1", ssh_server.port, paramiko.ECDSAKey.generate())
        with pytest.raises(ssh_trust.HostKeyChanged):
            ssh_trust.connect("127.0.0.1", "radxa", state, port=ssh_server.port, password="pairing-pw")
