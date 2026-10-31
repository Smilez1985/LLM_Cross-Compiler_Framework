#!/usr/bin/env python3
"""
Deploying the device runtime over SSH and checking it on the device (design 4.4 steps 5 and 6, decision D7).

Everything runs as the device user the deploy settings name, over an authenticated SSH client whose host key the
caller has checked (orchestrator.utils.ssh_trust.connect; this module never trusts a key or asks for a password).
Every command is one fixed `sh -c '...'` (so the device user's login shell does not matter) built only from values
checked here: the device's home folder, user ID and architecture are read from the device and validated before they
are used, and paths are quoted. Nothing is written to /tmp (which may be mounted noexec): the tarball goes over SFTP
into a private staging folder inside the install folder (on a board without SFTP on a command's stdin), is checked
there against its sha256, then every file against SHA256SUMS, and only then moved into place:

    ~/.local/share/llm-framework/llama.cpp/<build id[:16]>/   bin/, lib/, llama-server.sh, build.json, ...
    ~/.local/share/llm-framework/llama.cpp/current            -> <build id[:16]>
    ~/.config/llm-framework/llama-server.key                   generated on the device under umask 077

The new build's programs are checked in their own folder before anything starts them (design 3.8 and P6: a failed
build is never activated): as the unprivileged user with a cleared environment, --version (the pinned build and
commit), --list-devices, the server's refusal of --tools, and a short llama-bench run when a model on the device is
given (it runs on the board on its own and is followed to its end). A model that is not a catalog model is only
benchmarked after the user's consent, which the caller writes to the audit log (decision D23). Only a build that
passes becomes `current`; a build that fails (a SIGILL from a wrong CPU baseline, a C library its programs do not run
with) is removed again, and the previous build stays active.
After the switch every older build stays on the device: the framework removes none by itself (round 5 f). The
result lists them (older_builds: name, size, whether a running program uses one), and only the person removes them,
at a terminal with `llm-cli device runtime prune DEVICE --on-device`: the full list first (full path, name, size and
what each is), nothing ticked, "No" by default (board_cleanup_plan, remove_older_builds). With the deploy setting
"systemd" the unit is then written and enabled with `sudo -n`, which fails instead of asking; whether sudo may be
used was decided in the deploy settings, before the deployment.

Every step goes through the one board session (RemoteShell over orchestrator/remote/session.py RemoteSession): when
the network of a Wi-Fi board drops, the session waits for the board (N5), the upload continues at what arrived, the
checks (they only read) run once more, the benchmark on the board is followed again, and a step that must not run
twice ends with a RemoteError once the board answers again, after which the staging folder is still removed.
"""

import os
import posixpath
import re
import secrets
import shlex
import socket
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from orchestrator.remote import gate
from orchestrator.remote.errors import RemoteFailure
# Every command below is one of llmcf's board operations (board_ops.py, orchestrator/remote/gate.py): a board whose
# key runs only llmcf's gate runs exactly these. The names stay here (their old import path).
from orchestrator.runtime.device import board_ops
from orchestrator.runtime.device.board_ops import (BENCH_PID, BENCH_PREFIX, BENCH_STALE_MINUTES,  # noqa: F401
                                                   BENCH_STOP_GRACE, BENCH_TIMEOUT, FACTS_SCRIPT, REQUIRED_TOOLS,
                                                   SUDO_SCRIPT)
from orchestrator.remote.text import one_line as _tail   # a board's answer as one plain line
from orchestrator.remote.transport import COMMAND_TIMEOUT, HASH_TIMEOUT, MAX_OUTPUT  # the one board session's limits
from orchestrator.remote.transport import ParamikoTransport  # noqa: F401 - its old import path here
from orchestrator.remote.transport import Result, TooMuchOutput, sh_command
from orchestrator.remote.transport import read_limited as _read_limited  # noqa: F401 - its old name here
from orchestrator.runtime.build.verify import Check, VerifyReport, parse_bench_json, parse_devices, parse_version
from orchestrator.runtime.device import thermal   # the board's temperature during the checks (round 23 item 16)
from orchestrator.runtime.device.launcher import (DEVICE_FOLDER_MAX, DEVICE_PATH_MAX, KEY_REL, START_SCRIPT,
                                                  UNIT_NAME, UNIT_PATH, install_root,
                                                  render_systemd_unit)
from orchestrator.runtime.device.settings import DeploySettings
from orchestrator.runtime.server_policy import (DEVICE_DEFAULT_PORT, SERVER_TOOLS_REFUSAL,
                                                check_command, kv_cache_args, tools_check_args)

UPLOAD_TIMEOUT = 1800
VERSION_TIMEOUT = 60
DEVICES_TIMEOUT = 180
BENCH_POLL = 2.0           # seconds between two looks whether the benchmark on the board has finished
MACHINES = {"aarch64": "arm64", "arm64": "arm64", "x86_64": "x86_64", "amd64": "x86_64"}

_RE_HOME = re.compile(r"^/[A-Za-z0-9._/-]{1,%d}$" % DEVICE_FOLDER_MAX, re.ASCII)   # bounds: launcher.py
_RE_UID = re.compile(r"^\d{1,10}$", re.ASCII)
_RE_ID16 = re.compile(r"^[0-9a-f]{16}$", re.ASCII)
_RE_ID = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_RE_SHA = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_RE_MODEL = re.compile(r"^/[A-Za-z0-9._+/-]{1,%d}$" % DEVICE_PATH_MAX, re.ASCII)
_RE_TOOL = re.compile(r"^[a-z0-9]{1,16}$", re.ASCII)
_RE_BUILD_LINE = re.compile(r"^build=([0-9a-f]{16}) (\d{1,15}) ([01])$", re.ASCII)
_PROGRAMS = ("llama-server", "llama-bench")


class RemoteError(RemoteFailure):
    """A deployment step on the device failed; the message says which and what to do. `kind` is the board session's
    case when the session ended the step (dropped: the network dropped during a step that is not repeated; wait: the
    board did not answer again; stopped: cancelled), `reason` the SSH reason code when the board could not be reached
    or logged in to again."""


class RemoteShell:
    """Runs fixed shell scripts on a device through the one board session (orchestrator/remote/session.py): `target`
    is a RemoteSession, or an authenticated paramiko.SSHClient (or a stand-in with exec_command), which runs in a
    session that cannot log in again (RemoteSession.attached: a network drop ends the step as before). Every script
    is wrapped into one `sh -c '...'` argument; the command, its time limit and its output cap are the session's
    (orchestrator/remote/transport.py ParamikoTransport.run), and this shell says in a RemoteError what went wrong.

    With a session that can log in again, a network drop (a Wi-Fi board, N5) makes the session wait for the board: a
    command that may run twice (`again`: it only reads, or does the same again) runs once more; any other ends with a
    RemoteError once the board answers again (whether it ran there is unknown), so the caller can still clean up. When
    the board does not answer again, every later command of this shell raises that same RemoteError at once (no second
    wait). upload() sends a file over SFTP and resumes after a drop (the session's upload); run_detached() starts a
    program on the board on its own and follows it (a drop does not stop it)."""

    def __init__(self, target: Any):
        from orchestrator.remote.session import RemoteSession
        self.session = target if isinstance(target, RemoteSession) else RemoteSession.attached(target)
        self.lost: Optional[RemoteError] = None   # the board did not answer again: every later step ends with it

    @property
    def client(self) -> Any:
        return self.session.client

    @staticmethod
    def command(script: str) -> str:
        return sh_command(script)

    def _call(self, action: Callable[[], Any], timeout: float) -> Any:
        """`action()` on the session, its failures as RemoteError (the framework's sentences)."""
        from orchestrator.remote.session import SessionFailure, Stopped
        if self.lost is not None:
            raise RemoteError(str(self.lost), self.lost.kind, self.lost.reason)
        if self.session.transport is None:   # closed by its owner (a Cancel) or never opened
            raise RemoteError("the connection to the device is closed", "stopped" if self.session.ended else "")
        try:
            return action()
        except TooMuchOutput as e:
            raise RemoteError(str(e)) from None
        except Stopped:
            raise RemoteError("the step was cancelled", "stopped") from None
        except SessionFailure as e:
            error = RemoteError(str(e), e.kind, e.reason, e.error)
            if e.kind == "wait" or self.session.transport is None:
                self.lost = error
            raise error from None
        except (socket.timeout, TimeoutError):
            raise RemoteError(f"no answer from the device within {timeout:.0f} s") from None
        except (OSError, EOFError) as e:
            raise RemoteError(f"the SSH connection failed: {e}") from None

    def run(self, script: str, *, stdin: Optional[Iterable[bytes]] = None, timeout: float = COMMAND_TIMEOUT,
            limit: int = MAX_OUTPUT, again: bool = False, step: str = "command") -> Result:
        """One `sh -c` on the device. `again`: the command may run twice, so after a network drop it runs once more
        (its `stdin` must then be a list, sent again); without it the step ends with a RemoteError once the board
        answers again."""
        return self._call(lambda: self.session.run_resumable(script, again=again, step=step, timeout=timeout,
                                                             limit=limit, stdin=stdin), timeout)

    def private_dir(self, parent: str, prefix: str) -> str:
        """A private folder on the device (RemoteSession.private_dir: mktemp, its answer checked strictly)."""
        return self._call(lambda: self.session.resumable(lambda: self.session.private_dir(parent, prefix),
                                                         again=False), COMMAND_TIMEOUT)

    def upload(self, path: Path, remote_path: str, sha256: str) -> None:
        """Puts the file at `path` (its sha256 `sha256`) on the device as `remote_path`, in a folder that exists
        there: over SFTP as <remote_path>.part, checked with sha256sum on the device and only then renamed; after a
        network drop it waits for the board and continues at what arrived (RemoteSession.upload). Every copy it starts
        on the board is made empty under umask 077 first (`private`), so it is the user's only from the start, as the
        old `umask 077 && cat >` made it (an SFTP server creates a file with its own umask; writing into the empty file
        keeps its mode), also the copy sent again after a damaged arrival. A board without SFTP (or a stand-in without
        it) gets the file on a command's stdin under umask 077 as before (no resume)."""
        if not self.session.gated and not self._has_sftp(posixpath.dirname(remote_path)):
            res = self.run(gate.script("file-write-private", remote_path), stdin=_chunks(Path(path)),
                           timeout=UPLOAD_TIMEOUT)
            if res.status != 0:
                raise RemoteError(f"the upload failed (exit {res.status}): {_tail(res.err or res.out)}")
            return
        with open(path, "rb") as fh:
            size = os.fstat(fh.fileno()).st_size
            self._call(lambda: self.session.upload(fh, remote_path, size, sha256, private=True), UPLOAD_TIMEOUT)

    def _has_sftp(self, folder: str) -> bool:
        """Whether the device answers over SFTP: one stat of `folder`, tried once. A board without an SFTP server
        (Dropbear without sftp-server) or a stand-in without SFTP gets the file on stdin. A network drop at this very
        moment (the connection stood before the stat and then dropped) is no answer about SFTP: the upload goes over
        SFTP, whose steps wait for the board and resume (RemoteSession.dropped). An attached session never waits, and
        a stand-in that cannot tell whether its connection stands gets stdin as before."""
        if self.lost is not None or self.session.transport is None:
            return True   # the upload's own step says why it cannot be done
        stood = self.session.alive()
        try:
            self.session.transport.stat_size(folder)
            return True
        except Exception as e:  # noqa: BLE001 - no SFTP subsystem on the device, a stand-in without SFTP, a drop
            return stood and self.session.dropped(e)

    def run_detached(self, folder: str, job: str, *, timeout: float, poll: float, step: str) -> int:
        """Starts `job` once in the private `folder` on the device on its own and follows it to its exit status, also
        after a network drop (RemoteSession.run_detached): a drop never stops it, and it is never started twice."""
        try:
            return self._call(lambda: self.session.run_detached(folder, job, timeout=timeout, poll=poll, step=step),
                              timeout)
        except RemoteError as e:
            if e.kind == "job_timeout":   # the sentence of a command that stayed silent this long, as before
                raise RemoteError(f"no answer from the device within {timeout:.0f} s", e.kind) from None
            raise


def _q(value: str) -> str:
    return shlex.quote(value)


# --- facts about the device ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class DeviceFacts:
    uid: int
    home: str
    arch: str
    init: str
    missing: Tuple[str, ...]
    unit_installed: bool
    sudo: Optional[bool] = None   # `sudo -n true` worked (only asked when the settings choose systemd)
    helper: Optional[bool] = None  # llmcf's root helper is installed (board_ops.ROOT_HELPER; asked with sudo)

    @property
    def root(self) -> str:
        return install_root(self.home)

    @property
    def key(self) -> str:
        return f"{self.home}/{KEY_REL}"


def read_facts(shell: RemoteShell, *, want_sudo: bool = False) -> DeviceFacts:
    """User ID, home folder, architecture, init system and missing tools of the device, each value validated."""
    res = shell.run(gate.script("facts-sudo" if want_sudo else "facts"), again=True)
    if res.status != 0:
        raise RemoteError(f"the device could not be inspected (exit {res.status}): {_tail(res.err or res.out)}")
    values: Dict[str, List[str]] = {}
    for line in res.out.splitlines()[:64]:
        key, sep, value = line.partition("=")
        if sep:
            values.setdefault(key.strip(), []).append(value.strip())

    def one(key: str) -> str:
        found = values.get(key) or [""]
        return found[0]

    uid, home, machine = one("uid"), one("home"), one("machine")
    if not _RE_UID.fullmatch(uid):
        raise RemoteError("the device did not report a user ID")
    if not _RE_HOME.fullmatch(home) or ".." in home.split("/") or home == "/":
        raise RemoteError("the device user's home folder is not a plain absolute path (letters, digits, ._/- only)")
    arch = MACHINES.get(machine)
    if arch is None:
        raise RemoteError(f"the device reports the machine {machine[:32]!r}, which the device runtime does not "
                          "support (aarch64, x86_64)")
    missing = tuple(t for t in values.get("missing", []) if _RE_TOOL.fullmatch(t))
    sudo = None if not want_sudo else one("sudo") == "yes"
    return DeviceFacts(uid=int(uid), home=home.rstrip("/"), arch=arch, init="systemd" if one("init") == "systemd"
                       else "other", missing=missing, unit_installed=one("unit") == "yes", sudo=sudo,
                       helper=None if not want_sudo else one("helper") == "yes")


# --- deployment ---------------------------------------------------------------------------------------------------

# The checks of a new build before it becomes current: (device home, the build's folder on the device) -> report.
CheckNewBuild = Callable[[str, str], VerifyReport]


@dataclass(frozen=True)
class BoardBuild:
    """A runtime build on the device that is not the active one: its folder name (the build ID's first 16
    characters), its size and whether a running program uses it."""
    build_id16: str
    size_bytes: int
    in_use: bool


@dataclass
class DeployResult:
    build_id: str
    home: str
    root: str
    previous: Optional[str]
    older: List[BoardBuild] = field(default_factory=list)   # every older build: all of them stay on the device
    install: str = "user"
    unit_started: Optional[bool] = None
    notes: List[str] = field(default_factory=list)
    report: Optional[VerifyReport] = None   # the checks of the new build on the device
    activated: bool = False                 # it passed them and is `current` now


def _chunks(path: Path, size: int = 1 << 20) -> Iterable[bytes]:
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(size), b""):
            yield chunk


def _hex16_glob() -> str:
    return "[0-9a-f]" * 16


def deploy_runtime(shell: RemoteShell, *, tarball: Path, tarball_sha256: str, build_id: str, arch: str,
                   settings: DeploySettings, check: CheckNewBuild,
                   say: Callable[[str], None] = lambda _m: None, kv_cache: Optional[str] = None) -> DeployResult:
    """Installs the tarball on the device behind `shell` (see the module docstring). The caller has checked the
    settings against the device's profile (DeploySettings.problems) before; this function checks the device itself
    again (user, architecture, tools, systemd, sudo) before it changes anything. `check` runs the checks of the new
    build in its own folder (verify_on_device with `folder`); only when they pass does the build become `current`
    and, with the systemd setting, the unit's build (result.activated). `kv_cache`: the KV-cache type the caller
    resolved from the deploy settings (DeploySettings.kv_choice): the unit starts the server with it, and the notes
    name the start command with it."""
    if not _RE_ID.fullmatch(build_id or "") or not _RE_SHA.fullmatch(tarball_sha256 or ""):
        raise RemoteError("the build ID or the tarball's sha256 is missing")
    if not callable(check):
        raise RemoteError("a deployment needs the checks of the new build before it becomes current")
    id16 = build_id[:16]
    systemd = settings.install == "systemd"
    facts = read_facts(shell, want_sudo=systemd)
    if facts.uid == 0:
        raise RemoteError("the SSH user on the device is root: the runtime never runs as root. Deploy as an "
                          "unprivileged user (llm-cli device settings DEVICE --user NAME)")
    if facts.arch != arch:
        raise RemoteError(f"the device runs {facts.arch}, but the build is for {arch}: probe the device again "
                          "(llm-cli scan --device) and build for it")
    if facts.missing:
        raise RemoteError(f"the device lacks {', '.join(facts.missing)}, which the deployment needs")
    if systemd:
        if facts.init != "systemd":
            raise RemoteError("the deploy settings choose the systemd unit, but the device does not run systemd: "
                              "switch the deploy settings to the start script (--install user)")
        if not facts.sudo:
            raise RemoteError("the deploy settings choose the systemd unit, but `sudo -n` does not work for this "
                              "user on the device (it needs a password or is not allowed). The framework never asks "
                              "for it during a deployment: allow sudo without a password for this user, or switch "
                              "the deploy settings to the start script (--install user). Nothing was changed")
    root = facts.root
    result = DeployResult(build_id=build_id, home=facts.home, root=root, previous=None, install=settings.install)
    # an unrestricted framework key is restricted now (llmcf's command gate; with systemd also its exact sudo rules)
    result.notes.extend(board_ops.restrict_key(shell, sudo_user=(settings.ssh_user or "") if systemd and facts.sudo
                                               and not facts.helper else ""))
    if systemd and not facts.helper and shell.session.gated:
        raise RemoteError("the deploy settings choose the systemd unit, but llmcf's root helper and its exact sudo "
                          "rules are not on the board, and the framework's key runs only llmcf's operations. Pair "
                          "the board again with its password so the framework installs them, or switch the deploy "
                          "settings to the start script (--install user). Nothing was changed")
    if systemd and not facts.helper and any("root helper and its exact sudo rules are installed" in n
                                            for n in result.notes):
        facts = replace(facts, helper=True)

    say("creating a private staging folder on the device ...")
    res = shell.run(gate.script("staging-create", root))
    staging = res.out.strip()
    if res.status != 0 or not staging.startswith(root + "/.staging-") or not _RE_HOME.fullmatch(staging):
        raise RemoteError(f"could not create a staging folder in {root} (exit {res.status}): "
                          f"{_tail(res.err or res.out)}")
    try:
        say(f"uploading {Path(tarball).name} ...")
        # over SFTP, checked on the board and resumed after a network drop (the session's upload); on a board
        # without SFTP on the command's stdin as before
        shell.upload(Path(tarball), staging + "/runtime.tar.gz", tarball_sha256)
        say("checking the tarball and every file on the device ...")
        res = shell.run(gate.script("runtime-check", staging, tarball_sha256, id16), timeout=HASH_TIMEOUT)
        if res.status != 0:
            raise RemoteError(f"the check on the device failed (exit {res.status}): {_tail(res.err or res.out)}")
        say("installing next to the active build ...")
        res = shell.run(gate.script("runtime-install", root, id16, staging))
        if res.status != 0:
            raise RemoteError(f"installing on the device failed (exit {res.status}): {_tail(res.err or res.out)}")
        active = next((ln.split("=", 1)[1].strip() for ln in res.out.splitlines() if ln.startswith("current=")), "")
        active = active if _RE_ID16.fullmatch(active) else ""
        existed = "existed=1" in res.out.splitlines()
    except BaseException:
        try:
            shell.run(gate.script("dir-remove", staging), again=True)
        except RemoteError:
            pass   # the step's own failure (or a Ctrl+C during a wait for the board) is what the person reads
        raise
    shell.run(gate.script("dir-remove", staging), again=True)
    folder = f"{root}/{id16}"

    say("creating the API key on the device (umask 077) ...")
    res = shell.run(gate.script("keygen", folder, facts.key), again=True)   # keygen makes the key only when it is missing
    if res.status != 0:
        _discard(shell, root, id16, active, existed)
        raise RemoteError(f"the key could not be created on the device (exit {res.status}): "
                          f"{_tail(res.err or res.out)}")

    say("checking the new build on the device before it becomes active ...")
    try:
        result.report = check(facts.home, folder)
    except BaseException:
        _discard(shell, root, id16, active, existed)
        raise
    if not result.report.ok:
        _discard(shell, root, id16, active, existed)
        if active == id16:
            result.notes.append("this build was already the active one; its files were replaced by the same, "
                                "checked files")
        elif existed:
            result.notes.append((f"the previous build {active} stays active" if active else "nothing was activated")
                                + "; the new build failed its checks. It was on the device before, as an older "
                                  "build, so it stays there with the same, checked files")
        else:
            result.notes.append((f"the previous build {active} stays active" if active else "nothing was activated")
                                + "; the new build failed its checks and was removed from the device")
        return result

    res = shell.run(gate.script("runtime-activate", root, id16), again=True)
    if res.status != 0:
        _discard(shell, root, id16, active, existed)
        raise RemoteError(f"switching to the new build failed (exit {res.status}): {_tail(res.err or res.out)}")
    result.activated = True
    result.previous = active if active and active != id16 else None

    # Every older build stays: the framework removes none from a device by itself (round 5 f); the person removes
    # them from the full list (llm-cli device runtime prune DEVICE --on-device).
    try:
        result.older = older_builds(shell, root)
    except RemoteError:
        result.older = []
    if result.older:
        from orchestrator.Core.deletion import size_text
        result.notes.append(f"{len(result.older)} older runtime build(s) stay on the device "
                            f"({size_text(sum(b.size_bytes for b in result.older))}); the framework removes none by "
                            "itself. Only the person removes them, from the full list: llm-cli device runtime prune "
                            "DEVICE --on-device")

    if systemd:
        _install_unit(shell, facts, settings, result, say, kv_cache)
    else:
        result.notes.append("start the server with: " + start_command(facts.home, settings.model_on_device(facts.home),
                                                                       settings.port, kv_cache)
                            + (f" (KV cache {kv_cache}, from the deploy settings)" if kv_cache_args(kv_cache) else ""))
    if not systemd and facts.unit_installed:
        result.notes.append(f"a systemd unit of an earlier deployment is still installed ({UNIT_PATH}); it starts "
                            "the new build after its next restart. To remove it: sudo systemctl disable --now "
                            f"{UNIT_NAME} && sudo rm {UNIT_PATH}")
    res = shell.run(gate.script("start-status", root), again=True)
    if res.status == 0 and "running" in res.out:
        result.notes.append("a server started with the start script is still running the previous build; restart "
                            f"it to use the new one ({root}/current/{START_SCRIPT} stop, then start)")
    return result


def older_builds(shell: RemoteShell, root: str) -> List[BoardBuild]:
    """Every build folder in the install folder `root` but the active one, with its size and whether a running
    program uses it. Reads only: the framework removes no build from a device by itself (round 5 f)."""
    if not _RE_HOME.fullmatch(root or ""):
        raise RemoteError("the install folder is not a plain absolute path")
    res = shell.run(gate.script("builds-list", root), again=True)
    if res.status != 0:
        raise RemoteError(f"the runtime builds on the device could not be listed (exit {res.status}): "
                          f"{_tail(res.err or res.out)}")
    found = []
    for line in res.out.splitlines():
        match = _RE_BUILD_LINE.fullmatch(line.strip())
        if match:
            found.append(BoardBuild(match.group(1), int(match.group(2)) * 1024, match.group(3) == "1"))
    return found


def board_cleanup_plan(builds: Sequence[BoardBuild], root: str, previous: Optional[str] = None):
    """The older runtime builds on a device as the person's deletion list (orchestrator/Core/deletion.py): each with
    its full path on the device, its folder name, its size and what it is. A build a running program uses is not
    listed. None when there is nothing to list. Only the person sees this list (it names the device's folders); a
    board has no trash, so a removal is for good."""
    from orchestrator.Core.deletion import plan_of
    from orchestrator.tools.spec import DeletionItem
    items = []
    for build in builds:
        if build.in_use or not _RE_ID16.fullmatch(build.build_id16):
            continue
        what = (f"self-built llama.cpp runtime build {build.build_id16} on the device, not the active one"
                + ("; the build that was active before the last deployment, kept to go back by hand"
                   if previous and build.build_id16 == previous[:16] else "")
                + "; the framework can build and deploy it again")
        items.append(DeletionItem(kind="runtime_build", speaking_name=f"runtime build {build.build_id16}",
                                  full_path=f"{root}/{build.build_id16}", file_name=build.build_id16,
                                  size_bytes=build.size_bytes, what=what))
    return plan_of(items, ("permanent",))


def remove_older_builds(shell: RemoteShell, accepted: Any, *, root: str, device_id: str,
                        previous: Optional[str] = None, audit: Any = None):
    """Removes exactly the builds of board_cleanup_plan() the person ticked and confirmed (actor "cli" or "gui";
    never an AI, never under a delegation or a standing approval). The list is read from the device again first
    (PlanChanged when a chosen build changed, became the active one or a running program uses it now); the removal is
    audited before anything goes (RuntimeError, nothing removed, without the audit log), with the folder names only;
    each folder is checked again on the device right before it goes. Returns a deletion.DeletionResult."""
    from orchestrator.Core.deletion import DeletionResult, require_person, require_same, same_items
    accepted = require_person(accepted)
    if accepted.actor not in ("gui", "cli"):
        raise PermissionError("Only the person removes runtime builds from a device, in the GUI or at a terminal.")
    current = board_cleanup_plan(older_builds(shell, root), root, previous)
    require_same(accepted, same_items(current, accepted.plan), "the list of runtime builds on the device")
    if audit is None or not callable(getattr(audit, "record", None)):
        raise RuntimeError("The audit log is not available, so nothing was removed.")
    names = [i.file_name for i in accepted.plan.items]
    try:
        audit.record(accepted.actor, "device_runtime", "external", "person", "started", approved=True,
                     arguments={"device": device_id, "builds": names, "bytes": accepted.plan.total_bytes,
                                "mode": accepted.mode},
                     detail=f"{len(names)} older runtime builds removed from device {device_id} for good",
                     extra=accepted.audit_extra())
    except Exception:  # noqa: BLE001 - the reason could name a path
        raise RuntimeError("The audit log cannot record the removal; nothing was removed.") from None
    result = DeletionResult()
    why = {"active": "it is the active build now", "gone": "it is no longer on the device",
           "inuse": "a running program uses it now"}
    for item in accepted.plan.items:
        id16 = item.file_name
        if not _RE_ID16.fullmatch(id16) or item.full_path != f"{root}/{id16}":
            result.failed.append((item.speaking_name, "not a build folder of the install folder"))
            continue
        try:
            res = shell.run(gate.script("build-remove", root, id16))
        except RemoteError as e:
            result.failed.append((item.speaking_name, str(e)))
            continue
        word = (res.out.strip().splitlines() or [""])[-1]
        if res.status == 0 and word == "removed":
            result.done.append(item.speaking_name)
            result.freed_bytes += item.size_bytes
        else:
            result.failed.append((item.speaking_name, why.get(word, f"the device refused it (exit {res.status})")))
    try:
        audit.record(accepted.actor, "device_runtime", "external", "person", "error" if result.failed else "ok",
                     approved=True, arguments={"device": device_id, "removed": len(result.done),
                                               "kept": len(result.failed)},
                     detail=f"{len(result.done)} older runtime builds removed, {len(result.failed)} kept",
                     extra=accepted.audit_extra())
    except Exception:  # noqa: BLE001 - the removal happened and is shown to the person; its start is recorded
        pass
    return result


def _discard(shell: RemoteShell, root: str, id16: str, active: str, existed: bool = False) -> None:
    """Removes a new build that did not pass, unless it is the active build itself or was on the device before this
    deployment (the same build deployed again, whose folder now holds the same files, checked against SHA256SUMS: an
    older build the person kept stays theirs, round 5 f). Best effort: a folder that stays behind is not active, and
    only the person removes it (llm-cli device runtime prune DEVICE --on-device)."""
    if id16 != active and not existed:
        try:
            shell.run(gate.script("dir-remove", f"{root}/{id16}"), again=True)
        except RemoteError:
            pass


def _install_unit(shell: RemoteShell, facts: DeviceFacts, settings: DeploySettings, result: DeployResult,
                  say: Callable[[str], None], kv_cache: Optional[str] = None) -> None:
    user = settings.ssh_user
    model = settings.model_on_device(facts.home)
    if user is None or model is None:
        raise RemoteError("the systemd unit needs the SSH user and the model path in the deploy settings")
    unit = render_systemd_unit(user=user, home=facts.home, model=model, port=settings.port, kv_cache=kv_cache)
    # llmcf's root helper (its exact sudo rules) when the board has it; else, over an unrestricted key with the
    # person's own `sudo -n`, the commands of before (a gated key never runs those: board_ops.py)
    helper = bool(facts.helper)
    say(f"installing the systemd unit {UNIT_NAME} (sudo -n" + (", llmcf's root helper" if helper else "") + ") ...")
    if helper:
        res = shell.run(gate.script("root-unit-install", model, str(int(settings.port)), kv_cache or "f16"),
                        again=True)
    else:
        res = shell.run(gate.script("unit-write-legacy"), stdin=[unit.encode("utf-8")], again=True)
    if res.status != 0:
        raise RemoteError(f"sudo could not write {UNIT_PATH} (exit {res.status}); the runtime is installed for the "
                          f"start script: {_tail(res.err or res.out)}")
    res = shell.run(gate.script("root-unit-enable" if helper else "unit-enable-legacy"), again=True)
    if res.status != 0:
        raise RemoteError(f"systemctl could not enable {UNIT_NAME} (exit {res.status}): {_tail(res.err or res.out)}")
    res = shell.run(gate.script("root-unit-start" if helper else "unit-start-legacy", model), again=True)
    if res.status != 0:
        raise RemoteError(f"systemctl could not start {UNIT_NAME} (exit {res.status}): {_tail(res.err or res.out)}")
    result.unit_started = "started=yes" in res.out
    if not result.unit_started:
        result.notes.append(f"{UNIT_NAME} is enabled; it starts once the model is at {model} (then: sudo systemctl "
                            f"start {UNIT_NAME})")


# --- checks on the device -----------------------------------------------------------------------------------------

def _program(folder: str, name: str) -> str:
    return f"{folder}/bin/{name}"


def _run_program(shell: RemoteShell, home: str, argv: Sequence[str], timeout: float, *,
                 merge: bool = True, checked: bool = True) -> Result:
    """Runs a program of the runtime on the device as the SSH user, from the home folder, with a cleared
    environment (the shared policy: HOME, a fixed PATH, the C locale), an empty stdin and a time limit. The checks it
    runs only read, so after a network drop they run once more."""
    if checked:
        check_command(argv)  # the shared forbidden-option list (D22); only the --tools check passes checked=False
    return shell.run(gate.script("run-program" if merge else "run-program-split", home, *argv), timeout=timeout,
                     again=True)


TIMED_OUT = 124                # timeout's exit status when it stopped the program


def bench_stop_command(folder: str, group: bool) -> str:
    """The command that stops the benchmark the framework started in `folder` on the board: the PID recorded in
    BENCH_PID (with `group`: its whole process group, which setsid made it lead), only while its job has no exit
    status yet, never anything found by name; TERM first, KILL after BENCH_STOP_GRACE seconds. The operation
    bench-stop (bench-stop-group) of board_ops.py."""
    try:
        return gate.script("bench-stop-group" if group else "bench-stop", folder)
    except gate.GateRefused:   # a folder that is none of llmcf's places (a stand-in's): the same command
        return board_ops.bench_stop_command(_q(folder), group)


def _stop_bench(shell: RemoteShell, folder: str, group: bool) -> bool:
    """Stops the benchmark the framework started in `folder` (a Cancel, or the follower gave up at its time limit):
    over the step's own connection while it stands (a Ctrl+C at the terminal), else over one new login with the same
    host-key check (a Cancel that closed the connection: the Boards window, an AI's cancel_job). True when the stop
    command reached the board; never raises (the board's own time limit ends the job otherwise). The benchmark's
    folder stays to the next run's sweep, as before."""
    script = bench_stop_command(folder, group)
    session = shell.session
    try:
        if session.transport is not None and not session.ended and not session.cancel.cancelled:
            shell.run(script, again=True, timeout=BENCH_STOP_GRACE + 30)
            return True
        fresh = session.for_cleanup()
        if fresh is None:
            return False
        try:
            fresh.run(script, timeout=BENCH_STOP_GRACE + 30)
        finally:
            fresh.close()
        return True
    except Exception:  # noqa: BLE001 - the stop is best effort; the board's timeout and the sweep remain
        return False


def _run_on_its_own(shell: RemoteShell, home: str, root: str, argv: Sequence[str], timeout: float) -> Result:
    """Runs a program of the runtime on the device on its own, as _run_program does (the user, the home folder, the
    cleared environment, an empty stdin), but detached from the SSH session (round 16 G4: work keeps running when the
    connection goes): RemoteSession.run_detached starts it once in a private folder of the install folder
    (<root>/.bench.XXXXXXXX), its stdout and stderr go into files there, and the session follows it to its exit status,
    also after a network drop (the on-board benchmark, up to BENCH_TIMEOUT). Then both are read (within the output cap)
    and the folder goes. Returns the program's exit status, stdout and stderr. A board without nohup runs it attached
    to the session as before (_run_program), so the run never waits for a job that could not start.

    The program's PID is recorded in the folder (with setsid it also leads its own process group), and with a working
    `timeout` the board stops it by itself after `timeout` seconds (exit status TIMED_OUT). A Cancel, a Ctrl+C or the
    follower's own time limit stops exactly that PID or group (_stop_bench; never a program found by name)."""
    check_command(argv)   # the shared forbidden-option list (D22)
    tools = set(shell.run(gate.script("bench-tools"), again=True).out.split())
    if "nohup=yes" not in tools:
        return _run_program(shell, home, argv, timeout, merge=False)
    group = "setsid=yes" in tools
    timed = "timeout=yes" in tools
    verb = "bench-job" + ("-setsid" if group else "") + ("-timeout" if timed else "")
    job = gate.job(verb, home, argv[0], *([str(int(timeout))] if timed else []), *argv[1:])
    sweep_bench_folders(shell, root)
    folder = shell.private_dir(root, BENCH_PREFIX)
    try:
        try:
            status = shell.run_detached(folder, job, timeout=timeout, poll=BENCH_POLL, step="bench")
        except RemoteError as e:
            if e.kind in ("stopped", "job_timeout"):   # nobody follows it any more: it stops now
                _stop_bench(shell, folder, group)
            raise
        except KeyboardInterrupt:
            _stop_bench(shell, folder, group)
            raise
        out = shell.run(gate.script("file-read", folder + "/out"), again=True).out
        err = shell.run(gate.script("file-read", folder + "/err"), again=True).out
    finally:
        # also on a failure and on a cancel that leaves the connection standing (a Ctrl+C at the terminal); a cancel
        # that closed the connection (the Boards window, an AI's cancel_job) cannot reach the board any more: the
        # next run's sweep_bench_folders removes the folder then
        try:
            shell.run(gate.script("dir-remove", folder), again=True)
        except RemoteError:
            pass   # a folder that stays is no build folder: older_builds and the prune never list it
    return Result(status, out, err)


def sweep_bench_folders(shell: RemoteShell, root: str) -> None:
    """Removes what earlier on-board benchmarks left in the install folder `root`: a run whose connection a Cancel
    closed (or whose framework ended) could not remove its private folder itself. A folder goes only when its name is
    exactly <root>/.bench.XXXXXXXX (mktemp's), it is a folder and no link, and nothing in it changed for longer than
    any run can take (BENCH_STALE_MINUTES: no session follows its job any more), as the local benchmark removes the
    sandbox folders a crash left behind (runtime/bench.py BenchRunner._sweep). Never fails the benchmark."""
    try:
        shell.run(gate.script("bench-sweep", root), again=True)
    except RemoteError:
        pass   # the benchmark itself says what is wrong with the board


def model_sha256(shell: RemoteShell, path: str) -> Optional[str]:
    """sha256 of a file on the device (None when it is missing or unreadable)."""
    if not _RE_MODEL.fullmatch(path or "") or ".." in path.split("/"):
        raise RemoteError("the model path on the device is not a plain absolute path")
    res = shell.run(gate.script("model-sha256", path), timeout=HASH_TIMEOUT, again=True)
    digest = res.out.strip()
    return digest if res.status == 0 and _RE_SHA.fullmatch(digest) else None


def verify_on_device(shell: RemoteShell, *, home: str, plan_verify: Mapping[str, Any], pin_build: int,
                     pin_commit: str, port: int, bench_model: Optional[str] = None,
                     bench_allowed: bool = False, threads: Optional[int] = None,
                     folder: Optional[str] = None) -> VerifyReport:
    """The checks of design 3.8 steps 2-5 on the device, as the unprivileged SSH user (never with sudo), of the
    programs in `folder`: a build folder of the install folder (a deployment checks the new build there before it
    becomes current), by default the active build (`current`)."""
    report = VerifyReport()
    root = install_root(home)
    if folder is None:
        folder = f"{root}/current"
    elif not (folder.startswith(root + "/") and _RE_ID16.fullmatch(folder[len(root) + 1:])):
        raise RemoteError("the folder to check is not a build folder of the install folder")
    names = list(dict.fromkeys(list(plan_verify.get("binaries") or []) + list(_PROGRAMS)))
    res = shell.run(gate.script("programs-check", folder, *names), again=True)
    missing = [ln.split("=", 1)[1] for ln in res.out.splitlines() if ln.startswith("missing=")]
    if res.status != 0 or missing:
        report.checks.append(Check("binaries", False, "missing on the device: " + (", ".join(missing) or "?")))
        return report
    report.checks.append(Check("binaries", True, ", ".join(names)))
    restricted, version = board_ops.key_state(shell)
    report.checks.append(Check("board-key", True if restricted else None,
                               f"the framework's key runs only llmcf's operations (gate {version})" if restricted else
                               "the framework's key on this board is not restricted (it runs anything the account "
                               "may run): pair the board again or deploy to restrict it", {"restricted": restricted}))
    report.ran_binaries = True

    # Version
    found, problems = {}, []
    for name in _PROGRAMS:
        r = _run_program(shell, home, [_program(folder, name), "--version"], VERSION_TIMEOUT)
        parsed = parse_version(r.out)
        if r.status != 0 or parsed is None:
            sigill = r.status in (132, -4) or "Illegal instruction" in r.out
            problems.append(f"{name} --version failed (exit {r.status})"
                            + ("; SIGILL: the CPU baseline of the build is wrong for this CPU" if sigill else "")
                            + f": {_tail(r.out)}")
            continue
        build, commit = parsed
        found[name] = f"b{build} ({commit})"
        if build != pin_build or len(commit) < 8 or not pin_commit.startswith(commit[:8]):
            problems.append(f"{name} reports build {build}, commit {commit}; the pin is build {pin_build}, commit "
                            f"{pin_commit[:8]}")
    report.checks.append(Check("version", not problems, "; ".join(problems) if problems else
                               ", ".join(f"{k} {v}" for k, v in found.items()), {"found": found}))

    # Devices
    r = _run_program(shell, home, [_program(folder, "llama-bench"), "--list-devices"], DEVICES_TIMEOUT)
    devices = parse_devices(r.out)
    if r.status != 0 or devices is None:
        report.checks.append(Check("devices", False, f"llama-bench --list-devices failed (exit {r.status}): "
                                   f"{_tail(r.out)}"))
    else:
        got = [d.name for d in devices]
        want = list(plan_verify.get("devices") or [])
        lost = [d for d in want if d not in got]
        data = {"devices": [{"name": d.name, "description": d.description[:80], "total_mib": d.total_mib}
                            for d in devices]}
        report.checks.append(Check("devices", not lost, (f"planned {', '.join(want)}, found "
                                                         f"{', '.join(got) or 'only the CPU'}") if lost
                                   else (", ".join(got) or "CPU only"), data))

    # The server refuses --tools (built without subprocess support): the one deliberate use of a forbidden option,
    # with a model path that does not exist, loopback and the key file.
    missing_model = f"{root}/.no-model-{secrets.token_hex(4)}.gguf"
    argv = tools_check_args(_program(folder, "llama-server"), missing_model, port, f"{home}/{KEY_REL}")
    r = _run_program(shell, home, argv, VERSION_TIMEOUT, checked=False)
    if r.status != 0 and SERVER_TOOLS_REFUSAL in r.out:
        report.checks.append(Check("server-tools", True, "llama-server refuses --tools (built without subprocess "
                                   "support)"))
    else:
        report.checks.append(Check("server-tools", False, f"llama-server did not refuse --tools (exit {r.status}): "
                                   f"{_tail(r.out)}"))

    # The board's temperature (round 23 item 16): read-only, before the short benchmark and again after it ran; the
    # reading closer to its limit stays with the report (a hot board warns, it fails no check)
    report.heat = thermal.read_heat(shell)

    # Short benchmark
    if bench_model is None:
        report.checks.append(Check("smoke", None, "no model on the device was given for the short llama-bench run "
                                   "(--bench-model)"))
    elif not bench_allowed:
        report.checks.append(Check("smoke", None, "the model is not a catalog model and running it was not "
                                   "allowed; not loaded (decision D23)"))
    else:
        if not _RE_MODEL.fullmatch(bench_model) or ".." in bench_model.split("/"):
            raise RemoteError("the benchmark model path is not a plain absolute path")
        argv = [_program(folder, "llama-bench"), "-m", bench_model, "-p", "64", "-n", "16", "-r", "1", "-o", "json"]
        if threads:
            argv += ["-t", str(int(threads))]
        r = _run_on_its_own(shell, home, root, argv, BENCH_TIMEOUT)
        report.heat = thermal.hotter(report.heat, thermal.read_heat(shell))
        if r.status != 0:
            sigill = r.status in (132, -4) or "Illegal instruction" in r.err
            report.checks.append(Check("smoke", False, f"llama-bench failed on the device (exit {r.status})"
                                       + ("; SIGILL: the CPU baseline of the build is wrong for this CPU"
                                          if sigill else "")
                                       + (f"; the board's time limit of {BENCH_TIMEOUT} s stopped it"
                                          if r.status == TIMED_OUT else "") + f": {_tail(r.err or r.out)}"))
        else:
            try:
                records = parse_bench_json(r.out)
            except ValueError as e:
                report.checks.append(Check("smoke", False, f"llama-bench wrote no usable JSON: {e}"))
            else:
                speeds = {("pp" if rec.get("n_prompt") else "tg"): rec.get("avg_ts") for rec in records}
                report.checks.append(Check("smoke", True, ", ".join(f"{k} {v:.1f} t/s" for k, v in speeds.items()
                                                                    if isinstance(v, (int, float))) or "ran",
                                           {"bench": records}))
    return report


def start_script_path(home: str) -> str:
    return f"{install_root(home)}/current/{START_SCRIPT}"


def start_command(home: str, model: Optional[str] = None, port: int = DEVICE_DEFAULT_PORT,
                  kv_cache: Optional[str] = None) -> str:
    """The command that starts the board's server with the start script: the model (MODEL.gguf when the deploy
    settings name none), the port when it is not the default, and the KV-cache type of the deploy settings."""
    argv = [start_script_path(home), "start"]
    words = ["sh", *(_q(a) for a in argv), _q(model) if model else "MODEL.gguf"]
    if port != DEVICE_DEFAULT_PORT:
        words += ["--port", str(int(port))]
    return " ".join(words + kv_cache_args(kv_cache))


__all__ = ["BoardBuild", "DeployResult", "DeviceFacts", "RemoteError", "RemoteShell", "board_cleanup_plan",
           "deploy_runtime", "model_sha256", "older_builds", "read_facts", "remove_older_builds", "start_command",
           "start_script_path", "sweep_bench_folders", "verify_on_device"]
