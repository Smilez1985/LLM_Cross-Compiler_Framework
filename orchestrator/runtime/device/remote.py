#!/usr/bin/env python3
"""
Deploying the device runtime over SSH and checking it on the device (design 4.4 steps 5 and 6, decision D7).

Everything runs as the device user the deploy settings name, over an authenticated SSH client whose host key the
caller has checked (orchestrator.utils.ssh_trust.connect; this module never trusts a key or asks for a password).
Every command is one fixed `sh -c '...'` (so the device user's login shell does not matter) built only from values
checked here: the device's home folder, user ID and architecture are read from the device and validated before they
are used, and paths are quoted. Nothing is written to /tmp (which may be mounted noexec): the tarball travels on the
command's stdin into a private staging folder inside the install folder, is checked there against its sha256, then
every file against SHA256SUMS, and only then moved into place:

    ~/.local/share/llm-framework/llama.cpp/<build id[:16]>/   bin/, lib/, llama-server.sh, build.json, ...
    ~/.local/share/llm-framework/llama.cpp/current            -> <build id[:16]>
    ~/.config/llm-framework/llama-server.key                   generated on the device under umask 077

The new build's programs are checked in their own folder before anything starts them (design 3.8 and P6: a failed
build is never activated): as the unprivileged user with a cleared environment, --version (the pinned build and
commit), --list-devices, the server's refusal of --tools, and a short llama-bench run when a model on the device is
given. A model that is not a catalog model is only benchmarked after the user's consent, which the caller writes to
the audit log (decision D23). Only a build that passes becomes `current`; a build that fails (a SIGILL from a wrong
CPU baseline, a C library its programs do not run with) is removed again, and the previous build stays active.
After the switch every older build stays on the device: the framework removes none by itself (round 5 f). The
result lists them (older_builds: name, size, whether a running program uses one), and only the person removes them,
at a terminal with `llm-cli device runtime prune DEVICE --on-device`: the full list first (full path, name, size and
what each is), nothing ticked, "No" by default (board_cleanup_plan, remove_older_builds). With the deploy setting
"systemd" the unit is then written and enabled with `sudo -n`, which fails instead of asking; whether sudo may be
used was decided in the deploy settings, before the deployment.
"""

import re
import secrets
import shlex
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from orchestrator.remote.text import one_line as _tail   # a board's answer as one plain line
from orchestrator.remote.transport import sh_command
from orchestrator.runtime.build.verify import Check, VerifyReport, parse_bench_json, parse_devices, parse_version
from orchestrator.runtime.device.launcher import (DEVICE_FOLDER_MAX, DEVICE_PATH_MAX, KEY_REL, START_SCRIPT,
                                                  UNIT_NAME, UNIT_PATH, install_root,
                                                  render_systemd_unit)
from orchestrator.runtime.device.settings import DeploySettings
from orchestrator.runtime.server_policy import (DEVICE_DEFAULT_PORT, POSIX_CLEARED_PATH, SERVER_TOOLS_REFUSAL,
                                                check_command, kv_cache_args, posix_cleared_env, tools_check_args)

COMMAND_TIMEOUT = 120
UPLOAD_TIMEOUT = 1800
VERSION_TIMEOUT = 60
DEVICES_TIMEOUT = 180
BENCH_TIMEOUT = 900
HASH_TIMEOUT = 900
MAX_OUTPUT = 1024 * 1024
REQUIRED_TOOLS = ("tar", "gzip", "sha256sum", "od", "dd", "tr", "readlink", "awk", "mktemp")
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


class RemoteError(RuntimeError):
    """A deployment step on the device failed; the message says which and what to do."""


@dataclass(frozen=True)
class Result:
    status: int
    out: str
    err: str


def _read_limited(stream: Any, limit: int) -> bytes:
    data = b""
    while len(data) <= limit:
        chunk = stream.read(min(65536, limit + 1 - len(data)))
        if not chunk:
            break
        data += chunk
    return data


def _text(data: bytes) -> str:
    return data.decode("utf-8", "replace")


class RemoteShell:
    """Runs fixed shell scripts on a device: `client` is an authenticated paramiko.SSHClient (or a stand-in with
    exec_command). Every script is wrapped into one `sh -c '...'` argument."""

    def __init__(self, client: Any):
        self.client = client

    @staticmethod
    def command(script: str) -> str:
        return sh_command(script)

    def run(self, script: str, *, stdin: Optional[Iterable[bytes]] = None, timeout: float = COMMAND_TIMEOUT,
            limit: int = MAX_OUTPUT) -> Result:
        try:
            ch_in, ch_out, ch_err = self.client.exec_command(self.command(script), timeout=timeout)
            if stdin is not None:
                for chunk in stdin:
                    ch_in.write(chunk)
                ch_in.flush()
            ch_in.channel.shutdown_write()
            out = _read_limited(ch_out, limit)
            err = _read_limited(ch_err, limit)
            if len(out) > limit or len(err) > limit:
                ch_out.channel.close()
                raise RemoteError(f"the device sent more than {limit} bytes")
            return Result(ch_out.channel.recv_exit_status(), _text(out), _text(err))
        except (socket.timeout, TimeoutError):
            raise RemoteError(f"no answer from the device within {timeout:.0f} s") from None
        except (OSError, EOFError) as e:
            raise RemoteError(f"the SSH connection failed: {e}") from None


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

    @property
    def root(self) -> str:
        return install_root(self.home)

    @property
    def key(self) -> str:
        return f"{self.home}/{KEY_REL}"


FACTS_SCRIPT = "\n".join([
    'printf "uid=%s\\n" "$(id -u)"',
    'printf "home=%s\\n" "$HOME"',
    'printf "machine=%s\\n" "$(uname -m)"',
    'if [ -d /run/systemd/system ]; then echo init=systemd; else echo init=other; fi',
    f'if [ -f {UNIT_PATH} ]; then echo unit=yes; else echo unit=no; fi',
    "for t in " + " ".join(REQUIRED_TOOLS) + '; do command -v "$t" >/dev/null 2>&1 || printf "missing=%s\\n" "$t"; '
    "done",
])
SUDO_SCRIPT = "sudo -n true >/dev/null 2>&1 && echo sudo=yes || echo sudo=no"


def read_facts(shell: RemoteShell, *, want_sudo: bool = False) -> DeviceFacts:
    """User ID, home folder, architecture, init system and missing tools of the device, each value validated."""
    res = shell.run(FACTS_SCRIPT + ("\n" + SUDO_SCRIPT if want_sudo else ""))
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
                       else "other", missing=missing, unit_installed=one("unit") == "yes", sudo=sudo)


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

    say("creating a private staging folder on the device ...")
    res = shell.run(f"umask 077 && mkdir -p {_q(root)} && mktemp -d {_q(root + '/.staging-XXXXXXXX')}")
    staging = res.out.strip()
    if res.status != 0 or not staging.startswith(root + "/.staging-") or not _RE_HOME.fullmatch(staging):
        raise RemoteError(f"could not create a staging folder in {root} (exit {res.status}): "
                          f"{_tail(res.err or res.out)}")
    try:
        say(f"uploading {Path(tarball).name} ...")
        res = shell.run(f"umask 077 && cat > {_q(staging + '/runtime.tar.gz')}", stdin=_chunks(Path(tarball)),
                        timeout=UPLOAD_TIMEOUT)
        if res.status != 0:
            raise RemoteError(f"the upload failed (exit {res.status}): {_tail(res.err or res.out)}")
        say("checking the tarball and every file on the device ...")
        sums_check = "\n".join([
            "set -eu",
            "umask 077",
            f"cd {_q(staging)}",
            'got=$(sha256sum runtime.tar.gz | cut -d " " -f 1)',
            f'[ "$got" = {_q(tarball_sha256)} ] || {{ echo "the tarball arrived damaged" >&2; exit 3; }}',
            "tar -xzf runtime.tar.gz",
            "rm -f runtime.tar.gz",
            f'[ "$(ls -A)" = {_q(id16)} ] || {{ echo "the tarball holds more than the build folder" >&2; exit 3; }}',
            # Only the user may read the runtime, whatever modes tar applied (umask, or the archive's own as root).
            f"chmod -R go-rwx {_q(id16)}",
            f"cd {_q(id16)}",
            "sha256sum -c SHA256SUMS >/dev/null || { echo 'a file does not match SHA256SUMS' >&2; exit 3; }",
        ])
        res = shell.run(sums_check, timeout=HASH_TIMEOUT)
        if res.status != 0:
            raise RemoteError(f"the check on the device failed (exit {res.status}): {_tail(res.err or res.out)}")
        say("installing next to the active build ...")
        install = "\n".join([
            "set -eu",
            "umask 077",
            f"cd {_q(root)}",
            "old=$(readlink current 2>/dev/null || true)",
            # the same build already on the device (an older one the person kept): its files are replaced by the
            # same, checked files, and the folder is theirs, so a failed check never removes it (round 5 f)
            f"if [ -d {_q(id16)} ] && [ ! -L {_q(id16)} ]; then echo existed=1; fi",
            f"rm -rf {_q(id16)}",
            f"mv {_q(staging + '/' + id16)} {_q(id16)}",
            'printf "current=%s\\n" "$old"',
        ])
        res = shell.run(install)
        if res.status != 0:
            raise RemoteError(f"installing on the device failed (exit {res.status}): {_tail(res.err or res.out)}")
        active = next((ln.split("=", 1)[1].strip() for ln in res.out.splitlines() if ln.startswith("current=")), "")
        active = active if _RE_ID16.fullmatch(active) else ""
        existed = "existed=1" in res.out.splitlines()
    finally:
        shell.run(f"rm -rf {_q(staging)}")
    folder = f"{root}/{id16}"

    say("creating the API key on the device (umask 077) ...")
    key_check = "\n".join([
        "set -eu",
        f"sh {_q(folder + '/' + START_SCRIPT)} keygen >/dev/null",
        f'mode=$(ls -ln {_q(facts.key)} | cut -c1-10)',
        '[ "$mode" = "-rw-------" ] || { echo "the key file is readable by others ($mode)" >&2; exit 3; }',
    ])
    res = shell.run(key_check)
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

    res = shell.run("\n".join(["set -eu", f"cd {_q(root)}", f"ln -sfn {_q(id16)} current"]))
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
    res = shell.run(f"sh {_q(root + '/current/' + START_SCRIPT)} status")
    if res.status == 0 and "running" in res.out:
        result.notes.append("a server started with the start script is still running the previous build; restart "
                            f"it to use the new one ({root}/current/{START_SCRIPT} stop, then start)")
    return result


def _inuse_function(root: str) -> List[str]:
    """The shell function `inuse NAME`: true while a running program's executable lies in the build folder NAME."""
    return ["inuse() {",
            "  for p in /proc/[0-9]*; do",
            '    e=$(readlink "$p/exe" 2>/dev/null) || continue',
            f'    case $e in {_q(root)}/"$1"/*) return 0 ;; esac',
            "  done",
            "  return 1",
            "}"]


def older_builds(shell: RemoteShell, root: str) -> List[BoardBuild]:
    """Every build folder in the install folder `root` but the active one, with its size and whether a running
    program uses it. Reads only: the framework removes no build from a device by itself (round 5 f)."""
    if not _RE_HOME.fullmatch(root or ""):
        raise RemoteError("the install folder is not a plain absolute path")
    script = "\n".join([
        f"cd {_q(root)} 2>/dev/null || exit 0",
        "cur=$(readlink current 2>/dev/null || true)",
        *_inuse_function(root),
        f"for d in {_hex16_glob()}; do",
        '  [ -d "$d" ] && [ ! -L "$d" ] || continue',
        '  [ "$d" = "$cur" ] && continue',
        "  k=$(du -sk \"$d\" 2>/dev/null | awk '{print $1}')",
        '  if inuse "$d"; then u=1; else u=0; fi',
        '  echo "build=$d ${k:-0} $u"',
        "done",
    ])
    res = shell.run(script)
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
        script = "\n".join([
            f"cd {_q(root)} || exit 3",
            *_inuse_function(root),
            f'[ "$(readlink current 2>/dev/null || true)" = {_q(id16)} ] && {{ echo active; exit 3; }}',
            f"[ -d {_q(id16)} ] && [ ! -L {_q(id16)} ] || {{ echo gone; exit 3; }}",
            f"inuse {_q(id16)} && {{ echo inuse; exit 3; }}",
            f"rm -rf -- {_q(id16)} && echo removed",
        ])
        try:
            res = shell.run(script)
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
            shell.run(f"rm -rf {_q(root + '/' + id16)}")
        except RemoteError:
            pass


def _install_unit(shell: RemoteShell, facts: DeviceFacts, settings: DeploySettings, result: DeployResult,
                  say: Callable[[str], None], kv_cache: Optional[str] = None) -> None:
    user = settings.ssh_user
    model = settings.model_on_device(facts.home)
    if user is None or model is None:
        raise RemoteError("the systemd unit needs the SSH user and the model path in the deploy settings")
    unit = render_systemd_unit(user=user, home=facts.home, model=model, port=settings.port, kv_cache=kv_cache)
    say(f"installing the systemd unit {UNIT_NAME} (sudo -n) ...")
    res = shell.run(f"sudo -n tee {UNIT_PATH} >/dev/null", stdin=[unit.encode("utf-8")])
    if res.status != 0:
        raise RemoteError(f"sudo could not write {UNIT_PATH} (exit {res.status}); the runtime is installed for the "
                          f"start script: {_tail(res.err or res.out)}")
    res = shell.run(f"sudo -n systemctl daemon-reload && sudo -n systemctl enable {UNIT_NAME}")
    if res.status != 0:
        raise RemoteError(f"systemctl could not enable {UNIT_NAME} (exit {res.status}): {_tail(res.err or res.out)}")
    res = shell.run(f"if [ -f {_q(model)} ]; then sudo -n systemctl restart {UNIT_NAME} && echo started=yes; "
                    "else echo started=no; fi")
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
    environment (the shared policy: HOME, a fixed PATH, the C locale), an empty stdin and a time limit."""
    if checked:
        check_command(argv)  # the shared forbidden-option list (D22); only the --tools check passes checked=False
    env = " ".join(f"{k}={_q(v)}" for k, v in posix_cleared_env(home).items())
    script = (f"cd {_q(home)} && exec env -i {env} " + " ".join(_q(a) for a in argv) + " </dev/null"
              + (" 2>&1" if merge else ""))
    return shell.run(f"PATH={POSIX_CLEARED_PATH}; export PATH; {script}", timeout=timeout)


def model_sha256(shell: RemoteShell, path: str) -> Optional[str]:
    """sha256 of a file on the device (None when it is missing or unreadable)."""
    if not _RE_MODEL.fullmatch(path or "") or ".." in path.split("/"):
        raise RemoteError("the model path on the device is not a plain absolute path")
    res = shell.run(f"[ -f {_q(path)} ] && sha256sum {_q(path)} | cut -d ' ' -f 1", timeout=HASH_TIMEOUT)
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
    res = shell.run("; ".join(f"[ -f {_q(_program(folder, n))} ] && [ -x {_q(_program(folder, n))} ] "
                              f"|| echo missing={n}" for n in names))
    missing = [ln.split("=", 1)[1] for ln in res.out.splitlines() if ln.startswith("missing=")]
    if res.status != 0 or missing:
        report.checks.append(Check("binaries", False, "missing on the device: " + (", ".join(missing) or "?")))
        return report
    report.checks.append(Check("binaries", True, ", ".join(names)))
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
        r = _run_program(shell, home, argv, BENCH_TIMEOUT, merge=False)
        if r.status != 0:
            sigill = r.status in (132, -4) or "Illegal instruction" in r.err
            report.checks.append(Check("smoke", False, f"llama-bench failed on the device (exit {r.status})"
                                       + ("; SIGILL: the CPU baseline of the build is wrong for this CPU"
                                          if sigill else "") + f": {_tail(r.err or r.out)}"))
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
           "start_script_path", "verify_on_device"]
