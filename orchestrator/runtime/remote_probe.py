#!/usr/bin/env python3
"""
The hardware probe on a device over SSH.

The device's own probe output decides what gets built for it, so the probe runs there (decision D14):
scripts/hardware_probe.py with `python3 -I` when the device has Python 3.8 or newer, otherwise
scripts/hardware_probe.sh with `sh` (any POSIX shell: dash, busybox ash, bash). The script is sent on the command's
stdin and its result comes back on stdout, so nothing is written on the device and nothing has to be cleaned up. The
result is read with a size limit and parsed with the probe file parser: device output is untrusted.

The caller brings an authenticated SSH client whose host key is trusted (orchestrator.utils.ssh_trust.connect);
this module never trusts a host key or asks for a password itself.
"""

import re
import shlex
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from orchestrator.runtime.profile import HardwareProfile, ProfileError, load_schema, parse_profile

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
MIN_PYTHON = (3, 8)
# Asks for python3's version; `sh -c` so the device user's login shell (fish, csh, ...) does not matter.
PYTHON_CHECK = "sh -c " + shlex.quote(
    "command -v python3 >/dev/null 2>&1 && python3 -I -c 'import sys; print(\"%d.%d\" % sys.version_info[:2])'")
PYTHON_COMMAND = "python3 -I - --output - 2>/dev/null"
SHELL_COMMAND = "sh -s -- --output - 2>/dev/null"
_HOST = re.compile(r"^(?:[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|\[?[0-9A-Fa-f:.]{2,45}\]?)$", re.ASCII)


class RemoteProbeError(RuntimeError):
    """The probe could not run on the device or sent an unusable result; the message is for the user."""


@dataclass
class RemoteProbeResult:
    profile: HardwareProfile
    tool: str                                       # "python" or "sh": the script that ran on the device
    notes: List[str] = field(default_factory=list)  # why the fallback was used, for the user


def valid_host(host: str) -> bool:
    """A host name or IP address without options or shell characters (a leading '-' would read as an option)."""
    return isinstance(host, str) and bool(_HOST.fullmatch(host)) and not host.startswith("-")


def store_address(host: str, port: int) -> str:
    """The address as the profile store keeps it: host, host:port, or [IPv6]:port."""
    if port == 22:
        return host
    return f"[{host.strip('[]')}]:{port}" if ":" in host else f"{host}:{port}"


def _read_limited(stream, limit: int) -> bytes:
    data = b""
    while len(data) <= limit:
        chunk = stream.read(min(65536, limit + 1 - len(data)))
        if not chunk:
            break
        data += chunk
    return data


def _exec(client, command: str, stdin_data: Optional[bytes], timeout: float, limit: int) -> Tuple[int, bytes]:
    """(exit status, stdout) of one command; stdin_data is written and stdin closed. RemoteProbeError on a timeout,
    a lost connection or more output than `limit` bytes."""
    try:
        stdin, stdout, _stderr = client.exec_command(command, timeout=timeout)
        if stdin_data is not None:
            stdin.write(stdin_data)
            stdin.flush()
        stdin.channel.shutdown_write()
        data = _read_limited(stdout, limit)
        if len(data) > limit:
            stdout.channel.close()
            raise RemoteProbeError(f"the device sent more than {limit} bytes")
        return stdout.channel.recv_exit_status(), data
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
    """Runs the hardware probe on the device behind `client` (an authenticated paramiko.SSHClient or a stand-in
    with exec_command): the Python probe when python3 >= 3.8 is there, else (or when it fails) the shell probe."""
    scripts = Path(scripts_dir) if scripts_dir is not None else SCRIPTS_DIR
    limit = load_schema().max_bytes
    notes: List[str] = []
    version = device_python(client)
    if version is not None and version >= MIN_PYTHON:
        try:
            profile = _run_probe(client, PYTHON_COMMAND, scripts / "hardware_probe.py", timeout, limit)
            return RemoteProbeResult(profile=profile, tool="python", notes=notes)
        except RemoteProbeError as e:
            notes.append(f"the Python probe failed ({e}); used hardware_probe.sh")
    elif version is not None:
        notes.append(f"python3 {version[0]}.{version[1]} is older than 3.8; used hardware_probe.sh")
    else:
        notes.append("no python3 on the device; used hardware_probe.sh")
    profile = _run_probe(client, SHELL_COMMAND, scripts / "hardware_probe.sh", timeout, limit)
    return RemoteProbeResult(profile=profile, tool="sh", notes=notes)
