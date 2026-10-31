#!/usr/bin/env python3
"""
The hardware probe on a device over SSH.

The device's own probe output decides what gets built for it, so the probe runs there (decision D14):
scripts/hardware_probe.py with `python3 -I` when the device has Python 3.8 or newer, otherwise
scripts/hardware_probe.sh with `sh` (any POSIX shell: dash, busybox ash, bash). The script is sent on the command's
stdin and its result comes back on stdout, so nothing is written on the device and nothing has to be cleaned up. The
result is read with a size limit and parsed with the probe file parser: device output is untrusted.

The caller brings the board session (orchestrator/remote/session.py RemoteSession, whose host key is checked at every
login) or an authenticated SSH client whose host key is trusted (orchestrator.utils.ssh_trust.connect); this module
never trusts a host key or asks for a password itself. The probe only reads, so with a session a network drop makes
it wait for the board and send the command once more (N5).
"""

import re
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from orchestrator.remote.address import Endpoint, valid_host  # noqa: F401 - valid_host keeps its old import path
from orchestrator.remote import gate
from orchestrator.remote.transport import read_limited as _read_limited, sh_command
from orchestrator.runtime.device import board_ops  # noqa: F401 - defines the probe's operations
from orchestrator.runtime.profile import HardwareProfile, ProfileError, load_schema, parse_profile

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
# The Python probe also ships inside the package (a byte-identical copy of scripts/hardware_probe.py).
PACKAGE_PROBE = Path(__file__).resolve().with_name("hardware_probe.py")
MIN_PYTHON = (3, 8)
# sshd runs every command through the device user's login shell. Each one is a single `sh -c '...'`, so the
# redirections and && are parsed by sh and a login shell such as csh or fish (where `2>/dev/null` means something
# else or nothing) does not matter. stdin, which carries the script, passes through to the probe.
# The three are llmcf's board operations probe-python-check, probe-python and probe-shell
# (orchestrator/runtime/device/board_ops.py): on a board whose key runs only llmcf's gate, the gate runs the probe
# script it is sent only when it is one of the framework's own probe scripts.
PYTHON_CHECK = sh_command(gate.script("probe-python-check"))
PYTHON_COMMAND = sh_command(gate.script("probe-python"))
SHELL_COMMAND = sh_command(gate.script("probe-shell"))
_OPS = {PYTHON_CHECK: "probe-python-check", PYTHON_COMMAND: "probe-python", SHELL_COMMAND: "probe-shell"}


class RemoteProbeError(RuntimeError):
    """The probe could not run on the device or sent an unusable result; the message is for the user."""


@dataclass
class RemoteProbeResult:
    profile: HardwareProfile
    tool: str                                       # "python" or "sh": the script that ran on the device
    notes: List[str] = field(default_factory=list)  # why the fallback was used, for the user


def store_address(host: str, port: int) -> str:
    """The address as the profile store keeps it: host, host:port, or [IPv6]:port (Endpoint.store_form)."""
    return Endpoint(host, port).store_form


def _session(target):
    """The board session of `target`: a RemoteSession as it is (it waits for the board when the network drops, N5),
    a client in one that cannot log in again (RemoteSession.attached: a drop ends the probe as before)."""
    from orchestrator.remote.session import RemoteSession
    return target if isinstance(target, RemoteSession) else RemoteSession.attached(target)


def _exec_once(client, command: str, stdin_data: Optional[bytes], timeout: float, limit: int,
               session=None) -> Tuple[int, bytes]:
    """One command; with `session`, a command of _OPS goes in the session's form (its gate call on a gated board), and
    the gate's refusal of the script form makes the session gated and sends it again as a gate call."""
    verb = _OPS.get(command) if session is not None else None
    sent = sh_command(session.wire(gate.script(verb))) if verb else command
    stdin, stdout, stderr = client.exec_command(sent, timeout=timeout)
    if stdin_data is not None:
        try:
            stdin.write(stdin_data)
            stdin.flush()
        except OSError:   # the board's gate refused the line and closed the channel; its status says so
            pass
    stdin.channel.shutdown_write()
    data = _read_limited(stdout, limit)
    if len(data) > limit:
        stdout.channel.close()
        raise RemoteProbeError(f"the device sent more than {limit} bytes")
    status = stdout.channel.recv_exit_status()
    if verb and status == gate.REFUSED_STATUS and stderr is not None:
        try:
            err = _read_limited(stderr, 4096).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - a stand-in without stderr: no refusal to read
            err = ""
        if session.refused(gate.script(verb), status, err):
            return _exec_once(client, command, stdin_data, timeout, limit, session)
    return status, data


def _exec(target, command: str, stdin_data: Optional[bytes], timeout: float, limit: int) -> Tuple[int, bytes]:
    """(exit status, stdout as the board sent it) of one command; stdin_data is written and stdin closed.
    RemoteProbeError on a timeout, a lost connection or more output than `limit` bytes. The probe only reads and
    writes nothing on the board, so after a network drop the session waits for the board and runs the command once
    more (its SessionFailure says when the board does not answer again; Stopped on a cancel)."""
    session = _session(target)
    try:
        # the client of the session's current login, looked up at each try
        return session.resumable(lambda: _exec_once(session.client, command, stdin_data, timeout, limit, session))
    except (socket.timeout, TimeoutError):
        raise RemoteProbeError(f"no answer within {timeout:.0f} s") from None
    except (OSError, EOFError) as e:
        raise RemoteProbeError(f"the SSH connection failed: {e}") from None


def device_python(client, timeout: float = 30.0) -> Optional[Tuple[int, int]]:
    """(major, minor) of the device's python3, None when there is none."""
    status, out = _exec(client, PYTHON_CHECK, None, timeout, 64)
    match = re.fullmatch(rb"\s*(\d{1,2})\.(\d{1,3})\s*", out)
    if status != 0 or not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _run_probe(client, command: str, script: Path, timeout: float, limit: int) -> HardwareProfile:
    try:
        source = script.read_bytes()
    except OSError as e:
        raise RemoteProbeError(f"{script.name} is missing from this installation: {e.strerror or e}") from None
    status, out = _exec(client, command, source, timeout, limit)
    if status != 0:
        raise RemoteProbeError(f"{script.name} exited with status {status}")
    try:
        return parse_profile(out)
    except ProfileError as e:
        raise RemoteProbeError(f"{script.name} sent a file the framework cannot read: {e}") from None


def probe_over_ssh(client, scripts_dir: Optional[Path] = None, timeout: float = 180.0) -> RemoteProbeResult:
    """Runs the hardware probe on the device behind `client` (a RemoteSession, or an authenticated
    paramiko.SSHClient or a stand-in with exec_command): the Python probe when python3 >= 3.8 is there, else (or when
    it fails) the shell probe. With a session, a network drop makes it wait for the board and run the command again
    (orchestrator/remote/session.py SessionFailure when the board does not answer again)."""
    client = _session(client)
    scripts = Path(scripts_dir) if scripts_dir is not None else SCRIPTS_DIR
    limit = load_schema().max_bytes
    notes: List[str] = []
    version = device_python(client)
    if version is not None and version >= MIN_PYTHON:
        try:
            python_probe = scripts / "hardware_probe.py" if scripts_dir is not None else PACKAGE_PROBE
            profile = _run_probe(client, PYTHON_COMMAND, python_probe, timeout, limit)
            return RemoteProbeResult(profile=profile, tool="python", notes=notes)
        except RemoteProbeError as e:
            notes.append(f"the Python probe failed ({e}); used hardware_probe.sh")
    elif version is not None:
        notes.append(f"python3 {version[0]}.{version[1]} is older than 3.8; used hardware_probe.sh")
    else:
        notes.append("no python3 on the device; used hardware_probe.sh")
    profile = _run_probe(client, SHELL_COMMAND, scripts / "hardware_probe.sh", timeout, limit)
    return RemoteProbeResult(profile=profile, tool="sh", notes=notes)
