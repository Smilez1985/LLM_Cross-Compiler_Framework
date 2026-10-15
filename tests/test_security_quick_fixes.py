"""Security quick fixes of the sixth-round design (package S0, section 4.3 and 10.2): device passwords never need the
command line, paramiko never logs them, the loopback MCP listener ignores proxy headers and writes no access log, and
the installer says plainly what the docker group means. The Trivy scan without the Docker socket is tested with the
build engine's fakes in tests/test_review_build_engine.py."""

import http.client
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import click
import pytest

from conftest import REPO_ROOT
from orchestrator import cli as cli_module

INSTALL_SH = REPO_ROOT / "install.sh"
PASSWORD = "board-pw-Zq7"  # made up for the tests
DEVICE_COMMANDS = (("deploy", "run"), ("scan",), ("device", "runtime", "deploy"))


def command(*path):
    found = cli_module.cli
    for name in path:
        found = found.commands[name]
    return found


# --- device passwords: hidden prompt or stdin; a value on the command line warns -----------------------------------

@pytest.fixture
def keep_logger_levels():
    """`llm-cli -q` sets every existing logger to ERROR; later tests read WARNING logs."""
    saved = {name: logging.getLogger(name).level for name in list(logging.Logger.manager.loggerDict)}
    root = logging.getLogger().level
    yield
    for name in list(logging.Logger.manager.loggerDict):
        logging.getLogger(name).setLevel(saved.get(name, logging.NOTSET))
    logging.getLogger().setLevel(root)


@pytest.fixture
def scanned(monkeypatch, tmp_path, keep_logger_levels):
    """`llm-cli scan --device` on fakes: no network, no real device; records the password each login used."""
    from test_hardware_probe import FIXTURES, FakeHostKey
    from orchestrator.runtime import remote_probe as rp
    from orchestrator.runtime.profile import parse_profile
    from orchestrator.utils import ssh_trust
    logins = []

    class Client:
        def close(self):
            pass

    monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port: FakeHostKey())
    monkeypatch.setattr(ssh_trust, "check_host_key", lambda state, host, port, key: "known")
    monkeypatch.setattr(ssh_trust, "connect", lambda host, user, state, port=22, password=None: (
        logins.append(password) or Client()))
    monkeypatch.setattr(ssh_trust, "install_public_key", lambda client, line: None)
    board = parse_profile((FIXTURES / "schema2_rk3566_dietpi.txt").read_text())
    monkeypatch.setattr(rp, "probe_over_ssh", lambda client: rp.RemoteProbeResult(board, "sh", []))
    monkeypatch.chdir(tmp_path)

    def scan(*options, input=None):
        from test_cli_builds import invoke_plain
        result = invoke_plain("scan", "--device", "192.0.2.10", "--user", "dietpi", "--output", "board.txt",
                              *options, input=input)
        return result, logins

    return scan


@pytest.mark.skipif(os.name != "posix", reason="the profile store of scan --device is tested on POSIX")
def test_device_password_options_prompt_or_stdin(scanned, monkeypatch):
    for path in DEVICE_COMMANDS:
        params = {p.name: p for p in command(*path).params}
        assert params["password"].flag_value == cli_module.ASK_PASSWORD and not params["password"].is_flag, path
        assert params["password_stdin"].is_flag, path

    asked, logins = scanned("--password", input=PASSWORD + "\n")  # no value: asked with hidden input
    assert asked.code == 0, asked.out
    piped, _ = scanned("--password-stdin", input=PASSWORD + "\n")
    assert piped.code == 0, piped.out
    assert logins == [PASSWORD, PASSWORD]
    assert PASSWORD not in asked.out + piped.out, "hidden input is not echoed"
    assert "visible in the process list" not in asked.out + piped.out

    both, _ = scanned("--password", "x", "--password-stdin", input=PASSWORD + "\n")
    empty, _ = scanned("--password-stdin", input="\n")
    assert both.code == 2 and empty.code == 2 and logins == [PASSWORD, PASSWORD]

    # On a terminal --password-stdin would read the password with echo: it is refused before anything is read.
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True, readline=lambda: pytest.fail("read")))
    with pytest.raises(click.UsageError, match="--password without a value"):
        cli_module._device_password(None, True)


@pytest.mark.skipif(os.name != "posix", reason="the profile store of scan --device is tested on POSIX")
def test_password_value_option_warns(scanned):
    result, logins = scanned("--password", PASSWORD)
    assert result.code == 0, result.out
    assert logins == [PASSWORD], "a value on the command line keeps working for scripts"
    assert "The password is visible in the process list and your shell history" in result.out
    assert "--password-stdin" in result.out


def test_paramiko_never_logs_the_password(tmp_path, caplog, monkeypatch):
    """paramiko is held at INFO while a login authenticates, also when everything else logs at DEBUG. The trap logs
    the password at DEBUG through paramiko's own logger in the middle of the password authentication."""
    paramiko = pytest.importorskip("paramiko")
    from test_ssh_trust import FakeSSHServer  # the paramiko server of the real handshake tests
    from orchestrator.utils import ssh_trust
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="paramiko")
    caplog.set_level(logging.DEBUG, logger="paramiko.transport")
    real = paramiko.Transport.auth_password

    def trap(self, username, password, *args, **kwargs):
        logging.getLogger("paramiko.transport").debug("auth_password(%s, %s)", username, password)
        return real(self, username, password, *args, **kwargs)

    monkeypatch.setattr(paramiko.Transport, "auth_password", trap)
    host_key = paramiko.ECDSAKey.generate()
    server = FakeSSHServer(PASSWORD, set())
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def serve():
        conn, _ = listener.accept()
        transport = paramiko.Transport(conn)
        transport.add_server_key(host_key)
        transport.start_server(server=server)

    threading.Thread(target=serve, daemon=True).start()
    state = tmp_path / "state"
    port = listener.getsockname()[1]
    ssh_trust.trust_host_key(state, "127.0.0.1", port, host_key)
    client = ssh_trust.connect("127.0.0.1", "radxa", state, port=port, password=PASSWORD)
    client.close()
    listener.close()
    assert any(r.name.startswith("paramiko") for r in caplog.records), "paramiko logged nothing at all"
    assert not [r for r in caplog.records if PASSWORD in r.getMessage()]
    assert logging.getLogger("paramiko").level == logging.DEBUG, "the level comes back after the login"


# --- the loopback MCP listener: no proxy headers, no access log -----------------------------------------------------

LISTENERS = ("loopback",)  # NET-3 adds the network listener with its own config


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def probe_app(seen):
    async def app(scope, receive, send):
        if scope["type"] != "http":
            return
        seen.append((scope.get("client"), scope.get("scheme")))
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-length", b"2")]})
        await send({"type": "http.response.body", "body": b"ok"})
    return app


def listener_config(name, app, port):
    from orchestrator import mcp_server
    assert name == "loopback"
    return mcp_server.http_server_config(app, "127.0.0.1", port)


def serve_one_request(config, headers):
    import uvicorn
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started:
        assert thread.is_alive() and time.monotonic() < deadline, "the HTTP server did not start"
        time.sleep(0.02)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", config.port, timeout=10)  # direct: no proxy of the environment
        conn.request("GET", "/", headers=headers)
        assert conn.getresponse().status == 200
        conn.close()
    finally:
        server.should_exit = True
        thread.join(15)


@pytest.mark.parametrize("listener", LISTENERS)
def test_x_forwarded_for_ignored_on_both_listeners(listener):
    """uvicorn trusts X-Forwarded-* from 127.0.0.1 by default: every local program could claim another address."""
    seen = []
    config = listener_config(listener, probe_app(seen), free_port())
    serve_one_request(config, {"X-Forwarded-For": "203.0.113.7", "X-Forwarded-Proto": "https"})
    (client, scheme), = seen
    assert client[0] == "127.0.0.1" and scheme == "http"


@pytest.mark.parametrize("listener", LISTENERS)
def test_access_log_disabled_on_both_listeners(listener, capsys):
    """No line per request, also when someone turns uvicorn's loggers up to INFO."""
    access = logging.getLogger("uvicorn.access")
    level = access.level
    config = listener_config(listener, probe_app([]), free_port())
    access.setLevel(logging.INFO)
    try:
        serve_one_request(config, {})
    finally:
        access.setLevel(level)
    assert config.access_log is False and not access.hasHandlers()
    assert "GET / HTTP" not in capsys.readouterr().out


def test_serve_uses_the_listener_config(monkeypatch, tmp_path):
    import anyio
    import uvicorn
    from orchestrator import mcp_server
    from orchestrator.tools.mcp_tokens import TokenStore
    tokens = TokenStore(tmp_path)
    tokens.add("desk")
    framework = SimpleNamespace(info=SimpleNamespace(version="test"), tool_registry=None,
                                config=SimpleNamespace(user_state_dir=tmp_path))
    started = []

    class Server:
        def __init__(self, config):
            started.append(config)

        async def serve(self):
            return None

    monkeypatch.setattr(mcp_server, "_token_store", lambda fw: tokens)
    monkeypatch.setattr(mcp_server, "build_mcp_server", lambda fw, **kw: SimpleNamespace())
    monkeypatch.setattr(mcp_server, "http_app", lambda *a: probe_app([]))
    monkeypatch.setattr(uvicorn, "Server", Server)
    monkeypatch.setattr(anyio, "run", lambda fn: None)
    mcp_server.serve(framework, transport="http", port=free_port())
    (config,) = started
    assert config.proxy_headers is False and config.access_log is False and config.host == "127.0.0.1"


# --- install.sh: the docker group, said plainly ---------------------------------------------------------------------

def _stub(folder: Path, name: str) -> None:
    folder.mkdir(exist_ok=True)
    path = folder / name
    path.write_text(f'#!/bin/sh\necho "{name} $*" >> "{folder}/calls.log"\ncat > /dev/null\nexit 0\n')
    path.chmod(0o755)


@pytest.mark.skipif(not shutil.which("bash") or os.name != "posix", reason="install.sh needs bash")
def test_installer_says_what_the_docker_group_means(tmp_path):
    """Seventh round, item 6: no question about the docker group, but the installer says what membership means."""
    stubs = tmp_path / "bin"
    for name in ("curl", "usermod", "sh", "groups", "docker"):
        _stub(stubs, name)
    (stubs / "python3").symlink_to(shutil.which("python3") or sys.executable)
    script = 'source "$1"; wait_for_internet() { :; }; setup_system'
    run = subprocess.run(["/bin/bash", "-c", script, "test", str(INSTALL_SH)], capture_output=True, text=True,
                         env={"PATH": f"{stubs}:/usr/bin:/bin", "HOME": str(tmp_path), "SUDO_USER": "lcf"},
                         timeout=60, cwd=tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "usermod -aG docker lcf" in (stubs / "calls.log").read_text()
    said = run.stdout + run.stderr
    assert "Gruppe docker" in said and "Root-Rechte" in said and "gpasswd -d lcf docker" in said
