"""A stolen board key runs nothing new (DECISIONS 2026-10-31): llmcf's key line and the framework's signing key live
in root-owned places on the board (the root step root-board, orchestrator/remote/gate.py), and the gate runs only
runtime tarballs and packages signed with the framework's key (orchestrator/runtime/device/board_ops.py runtime-check
and the install job), never writes an upload where a program runs from, and checks run-program's arguments against
server_policy's forbidden list itself.

The gate and the root step run here under this computer's /bin/sh with stand-ins for sudo, sshd, systemctl and
install in a temporary folder (never this computer's own /etc). The signers path and the owner the gate trusts are put
in place for the test (its own folder, its own user). POSIX only: Windows runners have no /bin/sh, so those tests skip
with that reason; the signature format test needs ssh-keygen (OpenSSH 8.1 or later) and skips without it.
"""

import hashlib
import io
import os
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from orchestrator.Core import community_pack
from orchestrator.Core.community_pack import BoardSigner, PackError, sshsig_verify
from orchestrator.remote import gate
from orchestrator.remote.transport import Result
from orchestrator.runtime.device import board_ops

POSIX = pytest.mark.skipif(os.name == "nt" or shutil.which("sh") is None,
                           reason="the gate and the root step are POSIX sh programs; Windows runners have no /bin/sh")
KEYGEN = pytest.mark.skipif(shutil.which("ssh-keygen") is None,
                            reason="ssh-keygen (OpenSSH 8.1 or later) checks the signature on a board; not installed")
PUBLIC = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleExampleExampleExampleExample0 llm-cross-compiler-framework"
OTHER = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPersonsOwnKeyPersonsOwnKeyPersonsOwn01 person@example.com"
ID16 = "0123456789abcdef"


def _signer() -> BoardSigner:
    return BoardSigner(Ed25519PrivateKey.generate())


def test_the_gate_and_the_signer_name_the_same_namespace_and_principal():
    assert gate.SIGN_NAMESPACE == community_pack.BOARD_NAMESPACE
    assert gate.SIGN_PRINCIPAL == community_pack.BOARD_SIGNER
    assert gate.SELFTEST_DATA.decode() == "llmcf-board-selftest\n"


def test_a_board_signature_never_stands_in_for_another_key_or_purpose():
    signer, other = _signer(), _signer()
    sig = signer.sign_bytes(b"runtime")
    sshsig_verify(b"runtime", sig, signer.public_key)
    for data, key, ns in ((b"changed", signer.public_key, community_pack.BOARD_NAMESPACE),
                          (b"runtime", other.public_key, community_pack.BOARD_NAMESPACE),
                          (b"runtime", signer.public_key, "file")):
        with pytest.raises(PackError):
            sshsig_verify(data, sig, key, ns)


@KEYGEN
def test_openssh_checks_the_frameworks_signature(tmp_path):
    signer = _signer()
    data = tmp_path / "runtime.tar.gz"
    data.write_bytes(os.urandom(4096))
    (tmp_path / "sig").write_text(signer.sign_file(data), encoding="ascii")
    (tmp_path / "signers").write_text(signer.allowed_signers + "\n", encoding="ascii")
    argv = ["ssh-keygen", "-Y", "verify", "-f", str(tmp_path / "signers"), "-I", gate.SIGN_PRINCIPAL, "-n",
            gate.SIGN_NAMESPACE, "-s", str(tmp_path / "sig")]
    with open(data, "rb") as fh:
        ok = subprocess.run(argv, stdin=fh, capture_output=True, timeout=30)
    assert ok.returncode == 0, ok.stderr
    with open(data, "rb") as fh:
        with fh as f:
            changed = subprocess.run(argv, input=f.read() + b"x", capture_output=True, timeout=30)
    assert changed.returncode != 0


# --- the gate on a board whose root step installed the framework's signing key -----------------------------------

@pytest.fixture
def board(tmp_path):
    """A board with llmcf's gate: HOME in the test folder, the signers file of `board.signer` in the test folder,
    owned by this test's user (the owner the gate trusts here)."""
    home = tmp_path / "home"
    root = home / gate.INSTALL_ROOT_REL
    root.mkdir(parents=True)
    etc = tmp_path / "etc"
    signers_dir = etc / "llmcf"
    signers_dir.mkdir(parents=True)
    for folder in (etc, signers_dir):
        folder.chmod(0o755)
    signer = _signer()
    signers = signers_dir / "allowed_signers"
    signers.write_text(signer.allowed_signers + "\n", encoding="ascii")
    signers.chmod(0o644)
    ours = gate.signed_sh(signers.as_posix(), os.getuid())
    quoted = "'\"'\"'"   # how shlex.quote writes a ' inside the install job's quoted body
    text = gate.render_gate().replace(gate.signed_sh(), ours)
    text = text.replace(gate.signed_sh().replace("'", quoted), ours.replace("'", quoted))
    path = tmp_path / "llmcf-gate"
    path.write_text(text, encoding="utf-8")

    def run(line, stdin=b""):
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home), "SSH_ORIGINAL_COMMAND": line}
        done = subprocess.run(["sh", str(path)], input=stdin, env=env, capture_output=True, timeout=120)
        return Result(done.returncode, done.stdout.decode(), done.stderr.decode())
    run.home, run.root, run.signer, run.signers = home, root, signer, signers
    return run


def _tarball(program: bytes = b"#!/bin/sh\necho framework\n") -> bytes:
    files = {f"{ID16}/bin/llama-server": program}
    sums = "".join(f"{hashlib.sha256(v).hexdigest()}  {k.split('/', 1)[1]}\n" for k, v in files.items())
    files[f"{ID16}/SHA256SUMS"] = sums.encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o755
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _staged(board, tarball: bytes, signature: str = ""):
    made = board(f"llmcf-gate staging-create {board.root}")
    staging = made.out.strip()
    assert made.status == 0 and staging.startswith(f"{board.root}/.staging-"), made
    assert board(f"llmcf-gate file-write {staging}/runtime.tar.gz", tarball).status == 0
    if signature:
        assert board(f"llmcf-gate file-write {staging}/runtime.tar.gz.sig", signature.encode()).status == 0
    return staging


@POSIX
@KEYGEN
@pytest.mark.parametrize("who", ["unsigned", "another key"])
def test_a_stolen_key_cannot_install_a_runtime_the_framework_did_not_sign(board, who):
    """Regression (failed on the base: runtime-check checked only the sha256 the caller gave and unpacked it): a
    stolen key uploads its own tarball, gives its sha256, and the gate refuses it before anything is unpacked."""
    tarball = _tarball(b"#!/bin/sh\necho stolen\n")
    sig = _signer().sign_bytes(tarball) if who == "another key" else ""
    staging = _staged(board, tarball, sig)
    checked = board(f"llmcf-gate runtime-check {staging} {hashlib.sha256(tarball).hexdigest()} {ID16}")
    assert checked.status == 3 and "not signed with the framework's signing key" in checked.err, checked
    assert "verified=" not in checked.out
    assert not list(board.root.glob(".verify-*")) and not (board.root / ID16).exists()


@POSIX
@KEYGEN
def test_a_runtime_the_framework_signed_is_checked_and_installed(board):
    tarball = _tarball()
    staging = _staged(board, tarball, board.signer.sign_bytes(tarball))
    checked = board(f"llmcf-gate runtime-check {staging} {hashlib.sha256(tarball).hexdigest()} {ID16}")
    assert checked.status == 0, checked
    verified = checked.out.strip().split("=", 1)[1]
    assert verified.startswith(f"{board.root}/{gate.VERIFY_PREFIX}")
    # a stolen key cannot write into the checked folder, nor into the install folder or a build
    for target in (f"{verified}/{ID16}/bin/llama-server", f"{board.root}/{ID16}/bin/llama-server",
                   f"{board.root}/current/bin/llama-server"):
        refused = board(f"llmcf-gate file-write {target}", b"#!/bin/sh\necho stolen\n")
        assert gate.is_refusal(refused.status, refused.err), target
    installed = board(f"llmcf-gate runtime-install {board.root} {ID16} {verified}")
    assert installed.status == 0, installed
    assert (board.root / ID16 / "bin" / "llama-server").read_bytes() == b"#!/bin/sh\necho framework\n"
    # runtime-install takes only a folder runtime-check made: an upload folder is refused
    assert gate.is_refusal(*_status_err(board(f"llmcf-gate runtime-install {board.root} {ID16} {staging}")))


def _status_err(result):
    return result.status, result.err


@POSIX
def test_the_signed_tarball_is_copied_before_it_is_checked(board):
    """The check and the unpack read a copy in a folder of its own (an upload folder could be changed between them)."""
    body = gate.OPERATIONS["runtime-check"].render("/s", "0" * 64, ID16)
    assert body.index('cp runtime.tar.gz "$llmcf_v/runtime.tar.gz"') < body.index("llmcf_signed runtime.tar.gz")
    assert body.index("llmcf_signed runtime.tar.gz") < body.index("tar -xzf")


@POSIX
@KEYGEN
@pytest.mark.parametrize("who", ["unsigned", "another key", "framework"])
def test_a_package_runs_only_when_the_framework_signed_it(board, tmp_path, who):
    """Regression (failed on the base: the install job unzipped and ran any package's deploy.sh): a stolen key's
    package is refused and its deploy.sh never runs."""
    marker = tmp_path / "ran"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("deploy.sh", f"echo ran > {marker.as_posix()}\n")
    package = buf.getvalue()
    made = board("llmcf-gate mkprivate /tmp llm_deploy")
    folder = made.out.strip()
    try:
        assert board(f"llmcf-gate file-write {folder}/p.zip", package).status == 0
        if who != "unsigned":
            signer = board.signer if who == "framework" else _signer()
            assert board(f"llmcf-gate file-write {folder}/p.zip.sig", signer.sign_bytes(package).encode()).status == 0
        started = board(f"llmcf-gate start-install {folder} p.zip")
        assert started.status == 0 and "started=now" in started.out, started
        for _ in range(200):
            if (Path(folder) / gate.JOB_STATUS).exists():
                break
            subprocess.run(["sleep", "0.05"])
        status = (Path(folder) / gate.JOB_STATUS).read_text().strip()
        if who == "framework":
            assert status == "0" and marker.exists()
        else:
            assert status == "3" and not marker.exists()
            assert "not signed with the framework's signing key" in (Path(folder) / gate.JOB_LOG).read_text()
    finally:
        shutil.rmtree(folder, ignore_errors=True)


@POSIX
@pytest.mark.parametrize("option", ["--tools", "--tools=all", "--slot_save_path", "-hf", "--rpc"])
def test_the_gate_itself_refuses_a_forbidden_option_of_run_program(board, option):
    """Regression (failed on the base: run-program's rest arguments were only checked for their characters): a line
    that never went through the Python check is refused by the gate."""
    program = f"{board.home}/{gate.INSTALL_ROOT_REL}/current/bin/llama-server"
    refused = board(f"llmcf-gate run-program {board.home} {program} {option} x")
    assert gate.is_refusal(refused.status, refused.err) and "never given to a runtime program" in refused.err
    with pytest.raises(gate.GateRefused):
        gate.script("run-program", str(board.home), program, option, "x")


def _stub_program(board, tmp_path):
    """A stand-in llama-server in the current build that records its arguments (it never runs when refused)."""
    marker = tmp_path / "ran"
    bindir = board.root / ID16 / "bin"
    bindir.mkdir(parents=True)
    program = bindir / "llama-server"
    program.write_text(f'#!/bin/sh\necho "$@" > {marker.as_posix()}\n', encoding="utf-8")
    program.chmod(0o755)
    (board.root / "current").symlink_to(ID16)
    return f"{board.home}/{gate.INSTALL_ROOT_REL}/current/bin/llama-server", marker


@POSIX
@pytest.mark.parametrize("option", ["--log-file", "--log_file=x"])
def test_the_gate_refuses_a_log_file_of_a_runtime_program(board, tmp_path, option):
    """Regression (passed the gate on cs-board-signing): llama-server's --log-file writes its log, with text from the
    model's metadata and the requests, to any file the account may write, such as a build's start script the gate runs
    with sh (keygen, start-status) or ~/.ssh/rc; the gate and the Python check refuse it."""
    program, marker = _stub_program(board, tmp_path)
    target = f"{board.root}/current/llama-server.sh"
    args = f"{option} {target}" if "=" not in option else f"--log_file={target}"
    refused = board(f"llmcf-gate run-program {board.home} {program} {args}")
    assert gate.is_refusal(refused.status, refused.err) and "never given to a runtime program" in refused.err, refused
    assert not marker.exists()
    with pytest.raises(gate.GateRefused):
        gate.script("run-program", str(board.home), program, *args.split())


@POSIX
@pytest.mark.parametrize("option", ["--tools", "--log-file"])
def test_the_gate_refuses_a_forbidden_option_of_a_bench_job_before_it_starts(board, tmp_path, option):
    """Regression (on cs-board-signing the started job ran in a new sh where the gate's llmcf_refuse is not defined,
    so the check said "not found" and the program ran with the option): start-bench-job is refused by the gate itself
    and nothing is started."""
    program, marker = _stub_program(board, tmp_path)
    made = board(f"llmcf-gate mkprivate {board.root} .bench")
    folder = made.out.strip()
    assert made.status == 0 and folder.startswith(f"{board.root}/.bench."), made
    started = board(f"llmcf-gate start-bench-job {folder} {board.home} {program} {option} x")
    assert gate.is_refusal(started.status, started.err) and "never given to a runtime program" in started.err, started
    for _ in range(40):
        if marker.exists():
            break
        subprocess.run(["sleep", "0.05"])
    assert not marker.exists() and not (Path(folder) / gate.JOB_STARTED).exists()
    # a bench without a forbidden option still starts and runs
    ok = board(f"llmcf-gate start-bench-job {folder} {board.home} {program} -p 16")
    assert ok.status == 0 and "started=now" in ok.out, ok
    for _ in range(200):
        if (Path(folder) / gate.JOB_STATUS).exists():
            break
        subprocess.run(["sleep", "0.05"])
    assert marker.read_text().strip() == "-p 16"


@POSIX
def test_the_key_line_and_the_gate_are_out_of_the_accounts_reach(board):
    """No gate operation writes to the account's own authorized_keys, to llmcf's root-owned key file or to the gate;
    and the gate trusts no signer file another user could change."""
    for target in (f"{board.home}/.ssh/authorized_keys", f"{gate.KEYS_DIR}/radxa", gate.ROOT_GATE, gate.SIGNERS):
        refused = board(f"llmcf-gate file-write {target}", b"x")
        assert gate.is_refusal(refused.status, refused.err), target
    assert "signing=yes" in board("llmcf-gate status").out
    board.signers.chmod(0o666)   # writable by others: never trusted
    assert "signing=no" in board("llmcf-gate status").out


# --- the root step (root-board) ------------------------------------------------------------------------------------

def _root_board(tmp_path, *, sshd_t: int = 0, other_match: bool = False, account: str = "radxa"):
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "authorized_keys").write_text(OTHER + "\n" + PUBLIC + "\n", encoding="utf-8")
    etc = tmp_path / "etc"
    (etc / "ssh").mkdir(parents=True)
    config = etc / "ssh" / "sshd_config"
    config.write_text("PasswordAuthentication yes\n" + ("Match User other\n" if other_match else ""), encoding="utf-8")
    paths = gate.BoardPaths(libexec=str(tmp_path / "libexec"), gate=str(tmp_path / "libexec" / "llmcf-gate"),
                            keys_dir=str(etc / "ssh" / "llmcf_keys"), signers_dir=str(etc / "llmcf"),
                            signers=str(etc / "llmcf" / "allowed_signers"), sshd_config=str(config))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "sudo.log"
    real_install = shutil.which("install") or "/usr/bin/install"
    akf = f"{paths.keys_dir}/%u .ssh/authorized_keys"
    stubs = {
        # sudo runs what it is given (without -n / -S -p ""), and says what it ran
        "sudo": f'echo "$*" >> "{log}"; while [ $# -gt 0 ]; do case $1 in -n|-S) shift ;; -p) shift 2 ;; '
                '*) break ;; esac; done; exec "$@"',
        # install as this test's user (no -o/-g)
        "install": 'a=""; while [ $# -gt 0 ]; do case $1 in -o|-g) shift 2 ;; *) a="$a $1"; shift ;; esac; done; '
                   f'exec {real_install} $a',
        # sshd -t checks the file; -T answers the key files of the account when llmcf's block is the only Match
        "sshd": f'case $1 in -t) exit {sshd_t} ;; -T) f=$3; if grep -q "^Match User other" "$f"; then '
                'echo "authorizedkeysfile .ssh/authorized_keys"; else '
                f'grep -q "AuthorizedKeysFile {akf}" "$f" && echo "authorizedkeysfile {akf}"; fi ;; esac',
        "systemctl": f'echo "systemctl $*" >> "{log}"',
        # the board account (whoever runs the test: root in a container, the runner's user in CI)
        "id": f'case $1 in -un) echo {account} ;; -u) [ {account} = root ] && echo 0 || echo 1000 ;; esac',
    }
    for name, body in stubs.items():
        (bin_dir / name).write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
        (bin_dir / name).chmod(0o755)

    def run(script, stdin=None):
        env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}", "HOME": str(home),
               "TMPDIR": str(tmp_path)}
        done = subprocess.run(["sh", "-c", str(script)], input=b"".join(stdin or []), env=env, capture_output=True,
                              timeout=120)
        return Result(done.returncode, done.stdout.decode(), done.stderr.decode())
    return run, paths, home, config, log


@POSIX
@KEYGEN
def test_the_root_step_puts_the_key_line_and_the_signing_key_in_root_owned_places(tmp_path):
    run, paths, home, config, log = _root_board(tmp_path)
    signer = _signer()
    package = board_ops.bundle(signer)
    for _ in range(2):   # idempotent: one block, one line
        ok, reason, gone = gate.install_root_board(run, PUBLIC, package, "", paths=paths)
        assert (ok, reason) == (True, ""), reason
    user = "radxa"
    key_file = Path(paths.keys_dir) / user
    # the root-owned line: the gate as forced command, and sshd runs no ~/.ssh/rc of the account before it
    assert key_file.read_text().strip() == gate.root_key_line(PUBLIC, paths.gate)
    assert key_file.read_text().strip()[:-len(PUBLIC) - 1].endswith(",no-user-rc")
    assert oct(key_file.stat().st_mode & 0o777) == "0o644" and oct(Path(paths.keys_dir).stat().st_mode & 0o777) == "0o755"
    assert Path(paths.signers).read_text().strip() == signer.allowed_signers
    assert Path(paths.gate).read_text() == package.gate
    text = config.read_text()
    assert text.count(f"Match User {user}") == 1 and text.startswith("PasswordAuthentication yes\n")
    assert (home / ".ssh" / "authorized_keys").read_text() == OTHER + "\n"   # the person's line stays, llmcf's moved
    assert "systemctl reload ssh" in log.read_text()


@POSIX
@KEYGEN
@pytest.mark.parametrize("case, reason", [("sshd-t", "sshd-config"), ("match", "sshd-match"),
                                          ("selftest", "old-ssh")])
def test_the_root_step_changes_nothing_when_sshd_or_the_signature_check_says_no(tmp_path, case, reason):
    run, paths, home, config, _log = _root_board(tmp_path, sshd_t=1 if case == "sshd-t" else 0,
                                                 other_match=case == "match")
    package = board_ops.bundle(_signer())
    if case == "selftest":   # a self-test signature of another key: this board's ssh-keygen would not accept it
        package = gate.GateBundle(gate=package.gate, signers=package.signers,
                                  selftest=_signer().sign_bytes(gate.SELFTEST_DATA))
    before = config.read_text()
    assert gate.install_root_board(run, PUBLIC, package, "", paths=paths) == (False, reason, "")
    assert config.read_text() == before and not Path(paths.keys_dir).exists()
    assert PUBLIC in (home / ".ssh" / "authorized_keys").read_text()


@POSIX
def test_the_root_step_refuses_root_as_the_board_account(tmp_path):
    run, paths, *_ = _root_board(tmp_path, account="root")
    assert gate.install_root_board(run, PUBLIC, board_ops.bundle(_signer()), "", paths=paths)[:2] == (False,
                                                                                                         "account")


@pytest.mark.skipif(os.name == "nt" or shutil.which("sshd") is None or shutil.which("ssh-keygen") is None
                    or getattr(os, "getuid", lambda: 1)() != 0,
                    reason="sshd -t reads host keys: needs OpenSSH's sshd and root (a CI container or this sandbox)")
def test_sshd_accepts_llmcfs_match_block(tmp_path):
    """The block the root step appends is valid sshd configuration, and sshd -T shows llmcf's key file first."""
    host_key = tmp_path / "host"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(host_key)], check=True, timeout=30)
    config = tmp_path / "sshd_config"
    config.write_text(f"HostKey {host_key}\nPasswordAuthentication no\n" + gate.match_block("radxa"), encoding="utf-8")
    sshd = shutil.which("sshd")
    assert subprocess.run([sshd, "-t", "-f", str(config)], capture_output=True, timeout=30).returncode == 0
    shown = subprocess.run([sshd, "-T", "-f", str(config), "-C", "user=radxa,host=localhost,addr=127.0.0.1"],
                           capture_output=True, text=True, timeout=30).stdout
    assert f"authorizedkeysfile {gate.KEYS_DIR}/%u .ssh/authorized_keys" in shown


def test_pairing_without_a_signing_key_installs_nothing():
    """No signer (no secrets store): the root step is never sent, the person reads why."""
    sent = []

    def run(script, stdin=None):
        sent.append(script)
        return Result(0, "root-board=installed home=none\n", "")
    assert gate.install_root_board(run, PUBLIC, board_ops.bundle(None)) == (False, "no-signer", "")
    assert sent == []


def test_verify_reports_where_the_key_lives():
    class Shell:
        def __init__(self, out):
            self.out = out

        def run(self, script, **_k):
            return Result(0, self.out, "")
    assert board_ops.key_facts(Shell("restricted=yes gate=0123456789abcdef keys=root signing=yes\n")) == {
        "restricted": True, "gate": "0123456789abcdef", "keys": "root", "signing": True, "tier": "root"}
    assert board_ops.key_facts(Shell("restricted=no gate=none\n"))["keys"] == "home"


# --- review of the board-signing chain: the signer of every pairing path, no ~/.ssh/rc, runtime-check's folder -------

def test_a_framework_without_a_store_never_falls_back_to_the_state_folder(tmp_path, monkeypatch):
    """Regression (failed before: a framework whose secrets store was not there had the keyring store of the state
    folder opened in its place, whatever its secret_storage said)."""
    from types import SimpleNamespace

    from orchestrator.Core import secrets_manager
    opened = []
    monkeypatch.setattr(secrets_manager, "SecretsManager", lambda *a, **k: opened.append(a) or pytest.fail("opened"))
    assert board_ops.framework_signer(SimpleNamespace(secrets_manager=None), tmp_path) is None
    assert opened == []


def test_pairing_takes_the_callers_signer_and_says_plainly_what_a_path_without_a_framework_cannot_open(
        tmp_path, monkeypatch):
    """Regression (failed before: the pairing paths had no signer of their own and opened the state folder's store
    as a keyring store; for a person whose secret_storage is passphrase or helper that gave a silent no-signer)."""
    from orchestrator.utils import ssh_trust
    signer = _signer()
    looked_up = []
    monkeypatch.setattr(board_ops, "framework_signer", lambda *a, **k: looked_up.append((a, k)))
    # the caller's framework signer: given as it is, or looked up only when a board is paired
    assert board_ops.pairing_bundle(signer, tmp_path).signers == signer.allowed_signers
    assert board_ops.pairing_bundle(lambda: signer, tmp_path).signers == signer.allowed_signers
    assert looked_up == [], "a caller's signer never opens the state folder's store"
    # the framework's own store had none: the plain no-signer, and still no fallback to the state folder
    assert board_ops.pairing_bundle(lambda: None, tmp_path).missing == "no-signer" and looked_up == []
    # no framework at all: only the state folder's store, and the reason says which stores cannot open there
    package = board_ops.pairing_bundle(None, tmp_path)
    assert looked_up == [((), {"state_dir": tmp_path})] and package.missing == board_ops.NO_FRAMEWORK_STORE
    sent = []
    assert gate.install_root_board(lambda s, stdin=None: sent.append(s), PUBLIC, package) == (
        False, board_ops.NO_FRAMEWORK_STORE, "")
    assert sent == []
    text = str(ssh_trust.GateUnavailable(board_ops.NO_FRAMEWORK_STORE))
    assert "passphrase" in text and "helper" in text and "secret_storage" in text


def _calls(name):
    """Every call of `name` (a function or a class) in the framework's code: (file, line, keyword names)."""
    import ast

    from conftest import REPO_ROOT
    found = []
    for path in sorted((REPO_ROOT / "orchestrator").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if called == name:
                    found.append((path.relative_to(REPO_ROOT).as_posix(), node.lineno,
                                  {k.arg for k in node.keywords}))
    return found


@pytest.mark.parametrize("name", ["scan_device", "connect_stored", "DeployHooks"])
def test_every_pairing_path_of_the_framework_gives_its_signer(name):
    """Regression (failed before: the CLI's and the GUI's scans, board logins and deploy logins gave no signer, so
    pairing opened the state folder's store instead of the framework's)."""
    calls = _calls(name)
    assert calls, name
    missing = [(f, line) for f, line, keywords in calls if "signer" not in keywords]
    assert missing == [], f"{name} without signer=: {missing}"


def test_the_root_owned_key_line_runs_no_user_rc_and_the_home_line_stays_dropbears():
    """Regression (failed before: sshd ran the account's ~/.ssh/rc, which the account may write, before the gate):
    the root-owned line (OpenSSH only) has no-user-rc; the home line, which Dropbear may read, keeps the options
    Dropbear knows (Dropbear refuses a line with an option it does not know, and runs no ~/.ssh/rc)."""
    root_line = gate.root_key_line(PUBLIC, gate.ROOT_GATE)
    options = root_line[:-len(PUBLIC) - 1].split('",', 1)[1].split(",")
    assert options == list(gate.KEY_OPTIONS) + ["no-user-rc"] and root_line.endswith(" " + PUBLIC)
    assert "no-user-rc" not in gate.restricted_line(PUBLIC, "/home/radxa/" + gate.GATE_REL)
    assert "no-user-rc" not in gate.OPERATIONS["gate-install"].render("ssh-ed25519", "A" * 68, "llmcf")


@POSIX
@KEYGEN
@pytest.mark.parametrize("where", ["inside", "bench", "tmp", "slash", "install"])
def test_runtime_check_takes_only_the_staging_folder_the_framework_made(board, where):
    """Regression (failed before: runtime-check took any upload place, so its checked folder could land inside an
    upload folder, where upload operations write): only <install folder>/.staging-XXXXXXXX, nothing below it."""
    tarball = _tarball()
    sig = board.signer.sign_bytes(tarball)
    staging = _staged(board, tarball, sig)
    place = {"inside": f"{staging}/inner", "bench": f"{board.root}/.bench.abcdEFGH",
             "tmp": "/tmp/llm_deploy.abcdEFGH", "slash": f"{board.root}/.staging-ab/cdefg",
             "install": f"{board.root}"}[where]
    if where in ("inside", "bench", "slash"):   # a stolen key can put a tarball there with the upload operations
        folder = Path(place)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "runtime.tar.gz").write_bytes(tarball)
        (folder / "runtime.tar.gz.sig").write_text(sig, encoding="ascii")
    sha = hashlib.sha256(tarball).hexdigest()
    checked = board(f"llmcf-gate runtime-check {place} {sha} {ID16}")
    assert gate.is_refusal(checked.status, checked.err), checked
    assert not list(board.root.rglob(gate.VERIFY_PREFIX + "*"))
    with pytest.raises(gate.GateRefused):   # an unrestricted login gets no such call either
        gate.script("runtime-check", place, sha, ID16)
    # the folder the framework made is still taken
    assert board(f"llmcf-gate runtime-check {staging} {sha} {ID16}").status == 0


class _Stop(Exception):
    pass


def _deploy_flow_signer(tmp_path, monkeypatch, signer_hook):
    """The signer deploy_runtime_flow hands install_runtime for `signer_hook` (DeployHooks.signer), and what it said."""
    from types import SimpleNamespace

    from orchestrator.runtime.device import remote, steps
    got, said = [], []

    def install(shell, **kw):
        got.append(kw["signer"])
        raise _Stop()
    monkeypatch.setattr(steps, "install_runtime", install)
    monkeypatch.setattr(remote, "RemoteShell", lambda session: session)
    hooks = steps.DeployHooks(open_board=lambda: (SimpleNamespace(close=lambda: None), None), progress=said.append,
                              refuse_root=False, signer=signer_hook)
    with pytest.raises(_Stop):
        steps.deploy_runtime_flow(tmp_path, ID16, SimpleNamespace(arch="arm64"),
                                  SimpleNamespace(kv_choice=lambda _p: SimpleNamespace(kind="f16")), hooks=hooks,
                                  build=SimpleNamespace(plan={}), packaged=None, source=None, arch="arm64")
    return got, said


def test_the_deploy_flow_signs_with_the_callers_signer_and_says_plainly_without_a_framework(tmp_path, monkeypatch):
    """Regression (failed before: a caller's framework signer that was looked up lazily was not taken, and a flow
    without a framework fell back to the state folder's store in silence)."""
    signer = _signer()
    monkeypatch.setattr(board_ops, "framework_signer", lambda *a, **k: None)
    assert _deploy_flow_signer(tmp_path, monkeypatch, lambda: signer) == ([signer], [])
    got, said = _deploy_flow_signer(tmp_path, monkeypatch, None)
    assert got == [None] and len(said) == 1 and "passphrase" in said[0] and "helper" in said[0]
