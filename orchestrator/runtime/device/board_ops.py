#!/usr/bin/env python3
"""
Every command the framework runs on a board, as one of llmcf's operations (orchestrator/remote/gate.py): the runtime
deploy, its checks, the on-board benchmark, the prune of older builds, the board's temperature, the hardware probe, the
package deploy of DeploymentManager, llmcf's root actions and the setup steps of pairing. The modules that run them
(runtime/device/remote.py, thermal.py, runtime/remote_probe.py, Core/deployment_manager.py, utils/ssh_trust.py) make
their commands only with gate.script(...) from the definitions here, so the board's gate (gate.render_gate(), which
runs after this module is imported) knows exactly the commands the framework sends, and nothing else.

Each definition is the script these modules sent before as an f-string, with {0}, {1}, ... for its checked values: an
unrestricted login gets the same command text as before; the gate puts "$LLMCF_A1", ... in their place.

Root actions (the deploy setting "systemd"): llmcf's root helper (render_root_helper, installed root-owned at
ROOT_HELPER) writes the one unit from render_systemd_unit's own text for the calling account, enables and restarts it,
and nothing else; the sudoers file (sudoers_text, checked with `visudo -cf` before it is installed) lets the board
account run exactly those three helper actions without a password. A gated key runs root actions only this way; an
unrestricted key with the person's own `sudo -n` keeps the earlier commands (`sudo -n tee` of the unit) until its
next pairing or deploy restricts it.

bundle() is what pairing installs: the gate and the root helper. The setup operations (gate=False: the gate never runs
them) are gate-install (the gate program on stdin; the framework key's authorized_keys line becomes the restricted one,
every other line stays as it was) and root-setup (the root helper and the sudoers file, with `sudo -n` or the person's
one-time password on stdin for `sudo -S`).
"""

import hashlib
from pathlib import Path

from orchestrator.remote import gate
from orchestrator.remote.gate import define
from orchestrator.runtime.device import thermal
from orchestrator.runtime.device.launcher import (INSTALL_REL, KEY_REL, START_SCRIPT, UNIT_NAME, UNIT_PATH,
                                                  render_systemd_unit)
from orchestrator.runtime.server_policy import KV_CACHE_TYPES, POSIX_CLEARED_PATH, posix_cleared_env

ROOT_HELPER = gate.ROOT_HELPER
SUDOERS_DIR = gate.SUDOERS_DIR
REQUIRED_TOOLS = ("tar", "gzip", "sha256sum", "od", "dd", "tr", "readlink", "awk", "mktemp")
BENCH_PID = ".lcf-bench.pid"
BENCH_STOP_GRACE = 10
BENCH_PREFIX = ".bench"   # the benchmark's private folder in the install folder: <root>/.bench.XXXXXXXX
BENCH_TIMEOUT = 900
# A benchmark folder unchanged this long belongs to no run that can still follow its job (each gives up after
# BENCH_TIMEOUT from its start): the next run on the board removes it (remote.sweep_bench_folders).
BENCH_STALE_MINUTES = BENCH_TIMEOUT // 60 + 1
INSTALL_LOG = gate.JOB_LOG
OLD_SIDECAR = "qdrant-sidecar"
OLD_SIDECAR_SECONDS = 20


def _const(text: str):
    return lambda *_v, rest="": text


# --- facts ------------------------------------------------------------------------------------------------------------

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
HELPER_SCRIPT = f"if [ -x {ROOT_HELPER} ]; then echo helper=yes; else echo helper=no; fi"
define("facts", (), _const(FACTS_SCRIPT), doc="user ID, home, machine, init system, unit, missing tools")
define("facts-sudo", (), _const(FACTS_SCRIPT + "\n" + SUDO_SCRIPT + "\n" + HELPER_SCRIPT),
       doc="the facts, whether sudo -n works and whether llmcf's root helper is installed")

# --- the runtime deploy -----------------------------------------------------------------------------------------------

define("staging-create", ("place",), "umask 077 && mkdir -p {0} && mktemp -d {0}/.staging-XXXXXXXX",
       doc="a private staging folder in the install folder")
define("file-write-private", ("place",), "umask 077 && cat > {0}", doc="an upload on stdin (a board without SFTP)")
define("runtime-check", ("place", "sha256", "id16"), lambda staging, sha, id16, rest="": "\n".join([
    "set -eu",
    "umask 077",
    f"cd {staging}",
    'got=$(sha256sum runtime.tar.gz | cut -d " " -f 1)',
    f'[ "$got" = {sha} ] || {{ echo "the tarball arrived damaged" >&2; exit 3; }}',
    "tar -xzf runtime.tar.gz",
    "rm -f runtime.tar.gz",
    f'[ "$(ls -A)" = {id16} ] || {{ echo "the tarball holds more than the build folder" >&2; exit 3; }}',
    # Only the user may read the runtime, whatever modes tar applied (umask, or the archive's own as root).
    f"chmod -R go-rwx {id16}",
    f"cd {id16}",
    "sha256sum -c SHA256SUMS >/dev/null || { echo 'a file does not match SHA256SUMS' >&2; exit 3; }",
]), doc="the tarball's sha256, unpacked, every file against SHA256SUMS")
define("runtime-install", ("place", "id16", "place"), lambda root, id16, staging, rest="": "\n".join([
    "set -eu",
    "umask 077",
    f"cd {root}",
    "old=$(readlink current 2>/dev/null || true)",
    f"if [ -d {id16} ] && [ ! -L {id16} ]; then echo existed=1; fi",
    f"rm -rf {id16}",
    f"mv {staging}/{id16} {id16}",
    'printf "current=%s\\n" "$old"',
]), doc="the checked build next to the active one")
define("dir-remove", ("place",), "rm -rf {0}", doc="a staging, upload or benchmark folder, a failed build")
define("keygen", ("place", "path"), lambda folder, key, rest="": "\n".join([
    "set -eu",
    f"sh {folder}/{START_SCRIPT} keygen >/dev/null",
    f'mode=$(ls -ln {key} | cut -c1-10)',
    '[ "$mode" = "-rw-------" ] || { echo "the key file is readable by others ($mode)" >&2; exit 3; }',
]), doc="the runtime's API key, made on the board under umask 077")
define("runtime-activate", ("place", "id16"), lambda root, id16, rest="": "\n".join(
    ["set -eu", f"cd {root}", f"ln -sfn {id16} current"]), doc="the checked build becomes current")
define("start-status", ("place",), lambda root, rest="": f"sh {root}/current/{START_SCRIPT} status",
       doc="whether the start script's server runs")


def _inuse_function(root: str):
    """The shell function `inuse NAME`: true while a running program's executable lies in the build folder NAME."""
    return ["inuse() {",
            "  for p in /proc/[0-9]*; do",
            '    e=$(readlink "$p/exe" 2>/dev/null) || continue',
            f'    case $e in {root}/"$1"/*) return 0 ;; esac',
            "  done",
            "  return 1",
            "}"]


define("builds-list", ("place",), lambda root, rest="": "\n".join([
    f"cd {root} 2>/dev/null || exit 0",
    "cur=$(readlink current 2>/dev/null || true)",
    *_inuse_function(root),
    f"for d in {'[0-9a-f]' * 16}; do",
    '  [ -d "$d" ] && [ ! -L "$d" ] || continue',
    '  [ "$d" = "$cur" ] && continue',
    "  k=$(du -sk \"$d\" 2>/dev/null | awk '{print $1}')",
    '  if inuse "$d"; then u=1; else u=0; fi',
    '  echo "build=$d ${k:-0} $u"',
    "done",
]), doc="the older builds with their size and whether a running program uses one (reads only)")
define("build-remove", ("place", "id16"), lambda root, id16, rest="": "\n".join([
    f"cd {root} || exit 3",
    *_inuse_function(root),
    f'[ "$(readlink current 2>/dev/null || true)" = {id16} ] && {{ echo active; exit 3; }}',
    f"[ -d {id16} ] && [ ! -L {id16} ] || {{ echo gone; exit 3; }}",
    f"inuse {id16} && {{ echo inuse; exit 3; }}",
    f"rm -rf -- {id16} && echo removed",
]), doc="one older build the person chose, checked again right before it goes")

# --- the systemd unit -------------------------------------------------------------------------------------------------

# An unrestricted key with the person's own `sudo -n` (before its next pairing or deploy restricts it): the commands
# the framework sent before the root helper. The gate never runs them: a unit written from stdin could run anything.
define("unit-write-legacy", (), _const(f"sudo -n tee {UNIT_PATH} >/dev/null"), gate=False)
define("unit-enable-legacy", (), _const(f"sudo -n systemctl daemon-reload && sudo -n systemctl enable {UNIT_NAME}"),
       gate=False)
define("unit-start-legacy", ("path",), lambda model, rest="": (
    f"if [ -f {model} ]; then sudo -n systemctl restart {UNIT_NAME} && echo started=yes; else echo started=no; fi"),
    gate=False)
# llmcf's root helper: exactly the three root actions of the sudoers file.
define("root-unit-install", ("path", "uint", "word"), f"sudo -n {ROOT_HELPER} install-unit {{0}} {{1}} {{2}}",
       doc="the unit for this account, written by llmcf's root helper")
define("root-unit-enable", (), _const(f"sudo -n {ROOT_HELPER} enable"), doc="daemon-reload and enable the unit")
define("root-unit-start", ("path",), lambda model, rest="": (
    f"if [ -f {model} ]; then sudo -n {ROOT_HELPER} restart && echo started=yes; else echo started=no; fi"),
    doc="restart the unit once the model is on the board")

# --- the checks of a build on the board -------------------------------------------------------------------------------

define("programs-check", ("place",), lambda folder, rest="": "; ".join(
    f"[ -f {folder}/bin/{n} ] && [ -x {folder}/bin/{n} ] || echo missing={n}" for n in rest.split()),
    rest="word", rest_max=16,
    gate_render=lambda folder, rest="": (
        f'for n in {rest}; do [ -f {folder}/bin/"$n" ] && [ -x {folder}/bin/"$n" ] || echo "missing=$n"; done'),
    doc="the build's programs are there and executable")

_HOME = "/llmcf-sentinel-home"


def _env(home: str) -> str:
    import shlex
    text = " ".join(f"{k}={shlex.quote(v)}" for k, v in posix_cleared_env(_HOME).items())
    return text.replace(_HOME, home)


def _run_program(merge: bool):
    def render(home: str, program: str, rest: str = "") -> str:
        script = (f"cd {home} && exec env -i {_env(home)} " + " ".join([program] + ([rest] if rest else []))
                  + " </dev/null" + (" 2>&1" if merge else ""))
        return f"PATH={POSIX_CLEARED_PATH}; export PATH; {script}"
    return render


define("run-program", ("home", "program"), _run_program(True), rest="arg",
       doc="a runtime program as the account, cleared environment, empty stdin (stdout and stderr together)")
define("run-program-split", ("home", "program"), _run_program(False), rest="arg",
       doc="the same with stdout and stderr apart")
define("bench-tools", (), _const("command -v nohup >/dev/null 2>&1 && echo nohup=yes || echo nohup=no; "
                                 "command -v setsid >/dev/null 2>&1 && echo setsid=yes; "
                                 "timeout 5 true >/dev/null 2>&1 && echo timeout=yes; true"),
       doc="what the board offers for the detached benchmark")


def _bench_job(setsid: bool, timed: bool):
    def render(home: str, program: str, *values: str, rest: str = "") -> str:
        seconds = values[0] if timed else ""
        wrap = ("setsid " if setsid else "") + (f"timeout {seconds} " if timed else "")
        argv = " ".join([program] + ([rest] if rest else []))
        return (f"(PATH={POSIX_CLEARED_PATH}; export PATH; cd {home} && exec {wrap}env -i {_env(home)} "
                + argv + f" </dev/null) >out 2>err & echo $! > {BENCH_PID}; wait $!")
    return render


for _setsid in (False, True):
    for _timed in (False, True):
        define("bench-job" + ("-setsid" if _setsid else "") + ("-timeout" if _timed else ""),
               ("home", "program") + (("uint",) if _timed else ()), _bench_job(_setsid, _timed), rest="arg",
               job=True, doc="the benchmark on its own, its PID recorded")


def bench_stop_command(folder: str, group: bool) -> str:
    target = '-"$p"' if group else '"$p"'
    return (f'p=$(cat {folder}/{BENCH_PID} 2>/dev/null); case "$p" in ""|*[!0-9]*) exit 0;; esac; '
            f"[ -e {folder}/{gate.JOB_STATUS} ] && exit 0; "
            f"kill -TERM {target} 2>/dev/null || exit 0; i=0; "
            f"while [ $i -lt {BENCH_STOP_GRACE} ] && kill -0 {target} 2>/dev/null; do sleep 1; i=$((i+1)); done; "
            f"kill -KILL {target} 2>/dev/null; true")


define("bench-stop", ("place",), lambda folder, rest="": bench_stop_command(folder, False),
       doc="stops exactly the PID the framework recorded")
define("bench-stop-group", ("place",), lambda folder, rest="": bench_stop_command(folder, True),
       doc="stops exactly the process group the framework started")
define("file-read", ("place",), "cat {0}", doc="the benchmark's output files")
define("bench-sweep", ("place",), lambda root, rest="": (
    f"for d in {root}/{BENCH_PREFIX}.????????; do "
    '[ -d "$d" ] && [ ! -L "$d" ] || continue; '
    f'[ -n "$(find "$d" -maxdepth 0 -mmin +{BENCH_STALE_MINUTES} 2>/dev/null)" ] && rm -rf -- "$d"; '
    "done; true"), doc="benchmark folders no run follows any more")
define("model-sha256", ("path",), lambda path, rest="": f"[ -f {path} ] && sha256sum {path} | cut -d ' ' -f 1",
       doc="the sha256 of a model on the board")
define("thermal", (), _const(thermal.THERMAL_COMMAND), doc="the board's temperature (reads only)")

# --- the hardware probe -----------------------------------------------------------------------------------------------

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_PACKAGE_PROBE = Path(__file__).resolve().parents[1] / "hardware_probe.py"


def _hashes(*paths: Path) -> str:
    found = sorted({hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.is_file()})
    return "|".join(found) or "0" * 64


def _checked_probe(run: str, *paths: Path):
    """The gate's probe: the script sent on stdin goes into a private folder and runs only when its sha256 is one of
    the framework's own probe scripts (as this framework ships them when the gate is written)."""
    def render(*_v, rest=""):
        return "\n".join([
            'llmcf_d=$(mktemp -d "${TMPDIR:-/tmp}/llmcf-probe.XXXXXXXX") || exit 3',
            "trap 'rm -rf \"$llmcf_d\"' EXIT",
            'cat > "$llmcf_d/probe"',
            'case $(sha256sum "$llmcf_d/probe" | cut -d " " -f 1) in',
            f"  {_hashes(*paths)}) ;;",
            '  *) llmcf_refuse "this hardware probe is not one llmcf\'s gate on this board knows (pair the board '
            'again after an update)" ;;',
            "esac",
            run,
        ])
    return render


define("probe-python-check", (), _const(
    "command -v python3 >/dev/null 2>&1 && python3 -I -c 'import sys; print(\"%d.%d\" % sys.version_info[:2])'"),
    doc="the board's python3 version")
define("probe-python", (), _const("exec python3 -I - --output - 2>/dev/null"),
       gate_render=_checked_probe('python3 -I "$llmcf_d/probe" --output - 2>/dev/null',
                                  _PACKAGE_PROBE, _SCRIPTS / "hardware_probe.py"),
       doc="the Python hardware probe sent on stdin")
define("probe-shell", (), _const("exec sh -s -- --output - 2>/dev/null"),
       gate_render=_checked_probe('sh "$llmcf_d/probe" --output - 2>/dev/null', _SCRIPTS / "hardware_probe.sh"),
       doc="the shell hardware probe sent on stdin")

# --- the package deploy (orchestrator/Core/deployment_manager.py) -----------------------------------------------------


def old_sidecar_check(seconds: int = OLD_SIDECAR_SECONDS) -> str:
    """The board command that looks for the old sidecar: `docker ps`, bounded with `timeout SECONDS` where the board's
    `timeout` takes that form (coreutils, BusyBox 1.30 and later), as before where it does not (no `timeout`, or an
    older BusyBox one that wants `-t SECONDS`)."""
    look = f"docker ps -a --filter name={OLD_SIDECAR} --format '{{{{.Names}}}}|{{{{.Status}}}}' 2>/dev/null"
    return f"if timeout 5 true >/dev/null 2>&1; then timeout {int(seconds)} {look}; else {look}; fi"


define("install", ("word",), lambda pkg, rest="": (
    f"unzip -o {pkg} > {INSTALL_LOG} 2>&1 && sh deploy.sh >> {INSTALL_LOG} 2>&1"), job=True,
    doc="the package's deploy.sh, on its own")
define("install-log", ("place", "uint"), lambda d, n, rest="": f"tail -n {n} {d}/{INSTALL_LOG}",
       doc="the end of the installation's log")
define("old-sidecar", (), _const(old_sidecar_check()), doc="whether the old Qdrant sidecar is still on the board")

# --- llmcf's root helper and its sudo rules ---------------------------------------------------------------------------

_SENTINEL = {"user": "llmcfsentineluser", "home": "/llmcf-sentinel-home", "model": "/llmcf-sentinel-model.gguf",
             "port": 65432}


def _unit_template() -> str:
    """render_systemd_unit's text with the helper's shell variables in place of its values (one unit text)."""
    text = render_systemd_unit(user=_SENTINEL["user"], home=_SENTINEL["home"], model=_SENTINEL["model"],
                               port=_SENTINEL["port"])
    for ch in ("\\", "$", "`"):
        text = text.replace(ch, "\\" + ch)
    return (text.replace(f"--port {_SENTINEL['port']}", "--port ${port}${kvargs}")
            .replace(_SENTINEL["home"], "${home}").replace(_SENTINEL["model"], "${model}")
            .replace(_SENTINEL["user"], "${user}"))


def render_root_helper(*, unit_path: str = UNIT_PATH, passwd: str = "/etc/passwd", systemctl: str = "systemctl") -> str:
    """llmcf's root helper (POSIX sh, run with sudo): install-unit MODEL PORT KV writes the one unit for the calling
    account (SUDO_USER, its home from the password file), enable reloads systemd and enables it, restart restarts it.
    Every value is checked; anything else is refused."""
    kv = "|".join(KV_CACHE_TYPES)
    return f"""#!/bin/sh
# llmcf-root: the root actions of the LLM Cross-Compiler Framework on this board (generated by the framework). Its
# sudo rules let the framework's board account run exactly these three actions; nothing else.
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin; export PATH
umask 022
fail() {{ printf 'llmcf-root: refused: %s\\n' "$1" >&2; exit 126; }}
user=${{SUDO_USER-}}
case $user in ""|root|-*|*[!A-Za-z0-9._-]*) fail "run it with sudo as the framework's board account" ;; esac
home=$(awk -F: -v u="$user" '$1 == u {{ print $6; exit }}' {passwd})
case $home in /*) ;; *) fail "the account has no home folder" ;; esac
case $home in *[!A-Za-z0-9._/-]*|*/../*|*/..) fail "the account's home folder is not a plain path" ;; esac
case ${{1-}} in
  install-unit)
    [ $# -eq 4 ] || fail "install-unit MODEL PORT KV"
    model=$2; port=$3; kv=$4
    case $model in /*) ;; *) fail "the model is not an absolute path" ;; esac
    case $model in *[!A-Za-z0-9._+/-]*|*//*|*/../*|*/..|*/./*) fail "the model path is not a plain path" ;; esac
    case $port in ""|*[!0-9]*) fail "the port is not a number" ;; esac
    [ ${{#port}} -le 5 ] && [ "$port" -ge 1024 ] && [ "$port" -le 65535 ] || fail "the port is not 1024 to 65535"
    case $kv in {kv}) ;; *) fail "the KV-cache type is not one the framework knows" ;; esac
    kvargs=""
    [ "$kv" = f16 ] || kvargs=" -ctk $kv -ctv $kv"
    tmp="{unit_path}.llmcf.$$"
    cat > "$tmp" <<LLMCF_UNIT
{_unit_template()}LLMCF_UNIT
    chmod 0644 "$tmp"
    mv -f "$tmp" "{unit_path}" ;;
  enable)
    [ $# -eq 1 ] || fail "enable takes no arguments"
    {systemctl} daemon-reload && {systemctl} enable {UNIT_NAME} ;;
  restart)
    [ $# -eq 1 ] || fail "restart takes no arguments"
    {systemctl} restart {UNIT_NAME} ;;
  *) fail "not one of llmcf's root actions" ;;
esac
"""


def sudoers_text(user: str) -> str:
    """llmcf's sudoers file for the board account `user`: exactly the root helper's three actions, no password."""
    from orchestrator.remote.address import valid_user
    if not valid_user(user):
        raise ValueError("the sudo rules are for an unprivileged account name, never root")
    actions = ", ".join(f"{ROOT_HELPER} {a}" for a in ("install-unit *", "enable", "restart"))
    return (f"# llmcf: the root actions of the LLM Cross-Compiler Framework for {user} (generated; nothing else).\n"
            f"{user} ALL=(root) NOPASSWD: {actions}\n")


def sudoers_path(user: str) -> str:
    return f"{SUDOERS_DIR}/llmcf-{user}"


def bundle() -> gate.GateBundle:
    """What pairing installs: the gate with every operation of the framework, and llmcf's root helper."""
    return gate.GateBundle(gate=gate.render_gate(), root_helper=render_root_helper())


def key_state(shell) -> tuple:
    """(restricted, gate version) of the framework's key on the board behind `shell` (a RemoteShell or a session):
    llmcf's operation status. (False, "") for a key that runs anything (or a board that does not answer)."""
    try:
        result = shell.run(gate.script("status"), again=True) if hasattr(shell, "lost") else \
            shell.run(gate.script("status"))
    except Exception:  # noqa: BLE001 - the key's state is a report; the step's own commands say what is wrong
        return False, ""
    words = dict(w.split("=", 1) for w in (getattr(result, "out", "") or "").split() if "=" in w)
    return words.get("restricted") == "yes", words.get("gate", "") if words.get("gate") != "none" else ""


def restrict_key(shell, *, sudo_user: str = "") -> list:
    """The upgrade of an unrestricted framework key on the next deploy (the person's deploy over the key): llmcf's
    command gate and the key's restricted line, and with `sudo_user` (the deploy setting systemd, while `sudo -n`
    works) llmcf's root helper with its exact sudo rules. Returns the notes for the person: what changed, or plainly
    why the key stays unrestricted (the board cannot take the gate). A gated board whose gate is older than this
    framework's gets a note to pair again. Never fails the deploy."""
    from orchestrator.utils import ssh_trust
    session = getattr(shell, "session", shell)
    state_dir = getattr(session, "state_dir", None)
    if not getattr(session, "can_relogin", False) or state_dir is None:
        return []   # a stand-in's attached session: no framework key of ours to change
    package = bundle()
    if getattr(session, "gated", False):
        _restricted, version = key_state(shell)
        if version and version != package.version:
            return ["llmcf's command gate on this board is older than this framework's: pair the board again "
                    "(with its password) to update it; operations the old gate does not know are refused there."]
        return []
    notes = []
    run = shell.run if hasattr(shell, "lost") else session.run

    def call(script, stdin=None):
        return run(script, stdin=stdin, again=False) if hasattr(shell, "lost") else run(script, stdin=stdin)
    try:
        ok, reason = gate.install_gate(call, ssh_trust.client_key(state_dir)[1], package.gate)
    except Exception as e:  # noqa: BLE001 - the deploy goes on; the note says what happened
        ok, reason = False, getattr(e, "kind", "") or "unknown"
    if ok:
        notes.append("The framework's key on this board now runs only llmcf's operations (its command gate); the "
                     "account's own login and its other keys stay as they were.")
    else:
        notes.append("The framework's key on this board is NOT restricted: it could not take llmcf's command gate ("
                     + gate.SETUP_REASONS.get(reason, reason) + "). It still runs anything this account may run; fix "
                     "that on the board and deploy or pair again.")
    if ok and sudo_user:
        try:
            root_ok, why = gate.install_root(call, sudo_user, package.root_helper or "", sudoers_text(sudo_user))
        except Exception:  # noqa: BLE001 - the unit then needs the old sudo commands, which a gated key never runs
            root_ok, why = False, "unknown"
        notes.append("llmcf's root helper and its exact sudo rules are installed on the board (the systemd unit "
                     "needs no other sudo rights)." if root_ok else
                     "llmcf's exact sudo rules could not be installed (" + gate.SETUP_REASONS.get(why, why) + "); "
                     "the systemd unit then needs pairing again with the board's password.")
    return notes


def gate_path(home: str) -> str:
    return f"{home.rstrip('/')}/{gate.GATE_REL}"


__all__ = ["BENCH_PID", "FACTS_SCRIPT", "INSTALL_LOG", "OLD_SIDECAR", "REQUIRED_TOOLS", "ROOT_HELPER", "SUDO_SCRIPT",
           "bench_stop_command", "bundle", "gate_path", "old_sidecar_check", "render_root_helper",
           "key_state", "restrict_key", "sudoers_path", "sudoers_text"]

# The install folder lies in llmcf's place of the gate (tests/test_board_gate.py checks it with KEY_REL's folder).
PLACES_FOLLOW_LAUNCHER = INSTALL_REL.startswith(gate.PLACE_HOME_REL + "/") and KEY_REL.startswith(".config/")
