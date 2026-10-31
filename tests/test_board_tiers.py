"""The tiers of the framework's key on a board (orchestrator/remote/key_tiers.py, gate.py, pairing.py,
openssh_switch.py): Dropbear boards, boards without sudo and boards with an old ssh-keygen get a framework key again,
each tier only after the person's yes, the weaker tiers only in private mode, password login as the fallback.

Every board here is a fake: a temporary home folder, stub programs for sudo, install, sshd and the package managers
(no real sudo runs, nothing outside the test folder is written), documentation names and addresses only. Shell parts
need a POSIX sh and skip on Windows with their reason; the openssl parts need openssl 1.1.1 or later.
"""

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from orchestrator.Core.community_pack import BoardSigner
from orchestrator.remote import gate, key_tiers, openssh_switch, pairing
from orchestrator.remote.transport import Result
from orchestrator.runtime.device import board_ops

POSIX = pytest.mark.skipif(os.name == "nt" or shutil.which("sh") is None,
                           reason="the gate is a POSIX sh program; Windows runners have no /bin/sh to run it")


def _openssl_ok() -> bool:
    if shutil.which("openssl") is None:
        return False
    done = subprocess.run(["openssl", "pkeyutl", "-help"], capture_output=True, text=True, timeout=30)
    return "-rawin" in (done.stdout + done.stderr)


OPENSSL = pytest.mark.skipif(not _openssl_ok(), reason="needs openssl 1.1.1 or later (pkeyutl -rawin)")
PUBLIC = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleExampleExampleExampleExample0 llm-cross-compiler-framework"
OTHER = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPersonsOwnKeyPersonsOwnKeyPersonsOwn01 person@example.com"
ID16 = "0123456789abcdef"
NOT_ME = 4242   # an owner none of the test's files have: llmcf_rootowned is false for all of them


def _signer() -> BoardSigner:
    return BoardSigner(Ed25519PrivateKey.generate())


def _bin(tmp_path: Path, stubs: dict) -> Path:
    folder = tmp_path / "bin"
    folder.mkdir(exist_ok=True)
    for name, body in stubs.items():
        (folder / name).write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
        (folder / name).chmod(0o755)
    return folder


OLD_KEYGEN = {"ssh-keygen": 'echo "unknown option -- Y" >&2; exit 1'}   # OpenSSH before 8.1: no -Y verify


def _tarball() -> bytes:
    import io
    import tarfile
    files = {f"{ID16}/bin/llama-server": b"#!/bin/sh\necho framework\n"}
    sums = "".join(f"{hashlib.sha256(v).hexdigest()}  {k.split('/', 1)[1]}\n" for k, v in files.items())
    files[f"{ID16}/SHA256SUMS"] = sums.encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o755
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _gate_board(tmp_path, *, root_uid: int, signer: BoardSigner, home_signing: bool, stubs: dict = None):
    """A board running llmcf's gate as sshd would (SSH_ORIGINAL_COMMAND). `root_uid` the owner the gate trusts as
    root; `home_signing` puts the signing key into the account's home (the home gate) instead of the root place."""
    home = tmp_path / "home"
    (home / gate.INSTALL_ROOT_REL).mkdir(parents=True)
    home.chmod(0o755)
    signers = tmp_path / "etc" / "llmcf" / "allowed_signers"
    if home_signing:
        signers_dir = home / gate.HOME_SIGNING_REL
    else:
        signers_dir = signers.parent
    signers_dir.mkdir(parents=True, exist_ok=True)
    for folder in (signers_dir, signers_dir.parent, home / ".config"):
        if folder.exists():
            folder.chmod(0o755)
    (signers_dir / "allowed_signers").write_text(signer.allowed_signers + "\n", encoding="ascii")
    (signers_dir / "signing_key.pem").write_text(signer.public_pem, encoding="ascii")
    ours = gate.signed_sh(signers.as_posix(), root_uid)
    text = gate.render_gate().replace(gate.signed_sh(), ours)
    path = tmp_path / "llmcf-gate"
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    extra = _bin(tmp_path, stubs) if stubs else None

    def run(line, stdin=b""):
        env = {"PATH": (f"{extra}{os.pathsep}" if extra else "") + os.environ.get("PATH", "/usr/bin:/bin"),
               "HOME": str(home), "SSH_ORIGINAL_COMMAND": line}
        done = subprocess.run(["sh", str(path)], input=stdin, env=env, capture_output=True, timeout=120)
        return Result(done.returncode, done.stdout.decode(), done.stderr.decode())
    run.home, run.root = home, home / gate.INSTALL_ROOT_REL
    return run


def _check(board, tarball: bytes, signature: str):
    staging = board(f"llmcf-gate staging-create {board.root}").out.strip()
    assert board(f"llmcf-gate file-write {staging}/runtime.tar.gz", tarball).status == 0
    if signature:
        assert board(f"llmcf-gate file-write {staging}/runtime.tar.gz.sig", signature.encode()).status == 0
    return board(f"llmcf-gate runtime-check {staging} {hashlib.sha256(tarball).hexdigest()} {ID16}")


# --- (2) the framework's signature without ssh-keygen 8.1: openssl checks the same SSHSIG ----------------------------

@POSIX
@OPENSSL
def test_a_board_whose_ssh_keygen_is_too_old_still_checks_the_frameworks_signature(tmp_path):
    """Regression (failed before: with an ssh-keygen older than 8.1, or none, the gate refused every runtime, signed
    or not, so such a board got no framework key at all): openssl checks the same signature against the root-owned
    PEM twin of the signing key, and still refuses what the framework did not sign."""
    signer = _signer()
    board = _gate_board(tmp_path, root_uid=os.getuid(), signer=signer, home_signing=False, stubs=OLD_KEYGEN)
    tarball = _tarball()
    assert _check(board, tarball, signer.sign_bytes(tarball)).status == 0
    other = _check(board, tarball, _signer().sign_bytes(tarball))
    assert other.status != 0 and "not one of the framework's signing key" in other.err
    changed = _check(board, tarball + b"x" * 0, signer.sign_bytes(b"something else"))
    assert changed.status != 0


@POSIX
def test_the_board_says_plainly_when_it_can_check_the_signature_with_no_tool(tmp_path):
    signer = _signer()
    board = _gate_board(tmp_path, root_uid=os.getuid(), signer=signer, home_signing=False,
                        stubs={**OLD_KEYGEN, "openssl": 'echo "unknown option" >&2; exit 1'})
    refused = _check(board, _tarball(), signer.sign_bytes(_tarball()))
    assert refused.status != 0 and "neither with ssh-keygen" in refused.err


def test_the_openssl_check_rebuilds_exactly_what_sshsig_signs():
    """The prefix the gate writes with printf is PROTOCOL.sshsig's signed data before the digest, and the blob size
    is the one of the framework's Ed25519 signatures."""
    import base64

    from orchestrator.Core import community_pack
    digest = hashlib.sha512(b"data").digest()
    assert gate.sshsig_prefix() + digest == community_pack._sshsig_signed_data(digest, gate.SIGN_NAMESPACE)
    armored = _signer().sign_bytes(b"data")
    blob = base64.b64decode("".join(armored.strip().splitlines()[1:-1]))
    assert len(blob) == gate.SSHSIG_ED25519_SIZE


# --- (2) the Dropbear tier -----------------------------------------------------------------------------------------

def test_the_dropbear_line_holds_only_options_dropbear_knows():
    """Dropbear refuses a key line with an option it does not know: the line of the Dropbear tier and of the home
    gate keeps to its documented options (dropbear(8), Authorized Keys); OpenSSH's no-user-rc is never in it."""
    known = set(gate.DROPBEAR_OPTIONS)
    line = gate.restricted_line(PUBLIC, gate.ROOT_GATE)
    options = line[:-len(PUBLIC) - 1]
    assert options.startswith(f'command="/bin/sh {gate.ROOT_GATE}",')
    names = ["command"] + options.split('",', 1)[1].split(",")
    assert set(names) <= known and "no-user-rc" not in names
    assert set(gate.KEY_OPTIONS) <= known and not set(gate.DROPBEAR_LATER_OPTIONS) & set(gate.KEY_OPTIONS)
    body = gate.OPERATIONS["dropbear-board"].render("ssh-ed25519", "A" * 68, "llmcf")
    assert "no-user-rc" not in body and ",".join(gate.KEY_OPTIONS) in body


def _dropbear_board(tmp_path, *, verifier_stubs=None):
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh" / "authorized_keys").write_text(OTHER + "\n" + PUBLIC + "\n", encoding="utf-8")
    lib = tmp_path / "usr" / "local" / "libexec" / "llmcf"
    etc = tmp_path / "etc" / "llmcf"
    for folder in (lib.parent, etc.parent):
        folder.mkdir(parents=True)
        folder.chmod(0o755)
    paths = gate.BoardPaths(libexec=str(lib), gate=str(lib / "llmcf-gate"), signers_dir=str(etc),
                            signers=str(etc / "allowed_signers"), root_uid=os.getuid())
    log = tmp_path / "sudo.log"
    real_install = shutil.which("install") or "/usr/bin/install"
    stubs = {
        "sudo": f'echo "$*" >> "{log}"; while [ $# -gt 0 ]; do case $1 in -n|-S) shift ;; -p) shift 2 ;; '
                '*) break ;; esac; done; exec "$@"',
        "install": 'a=""; while [ $# -gt 0 ]; do case $1 in -o|-g) shift 2 ;; *) a="$a $1"; shift ;; esac; done; '
                   f'exec {real_install} $a',
        "id": 'case $1 in -un) echo dietpi ;; -u) echo 1000 ;; esac',
        **(verifier_stubs or {}),
    }
    bin_dir = _bin(tmp_path, stubs)

    def run(script, stdin=None):
        env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}", "HOME": str(home),
               "TMPDIR": str(tmp_path)}
        done = subprocess.run(["sh", "-c", str(script)], input=b"".join(stdin or []), env=env, capture_output=True,
                              timeout=120)
        return Result(done.returncode, done.stdout.decode(), done.stderr.decode())
    return run, paths, home, log


@POSIX
@OPENSSL
def test_the_dropbear_tier_puts_gate_and_signing_key_in_root_owned_places_and_the_line_at_home(tmp_path):
    """Regression (failed before: a Dropbear board got no framework key; there was no Dropbear tier): the gate, the
    signers file and its PEM twin are installed root-owned with sudo once (checked up the folder chain), and the
    framework key's line with the root-owned gate as forced command and Dropbear's options only goes into the
    account's own authorized_keys, the person's other line as it was. The check of the signature works with openssl
    where ssh-keygen is too old (Dropbear boards often have none)."""
    run, paths, home, log = _dropbear_board(tmp_path, verifier_stubs=OLD_KEYGEN)
    signer = _signer()
    package = board_ops.bundle(signer)
    for _ in range(2):   # idempotent: one line
        assert gate.install_dropbear_board(run, PUBLIC, package, "pw", paths=paths) == (True, "")
    lines = (home / ".ssh" / "authorized_keys").read_text().splitlines()
    assert lines == [OTHER, f'command="/bin/sh {paths.gate}",' + ",".join(gate.KEY_OPTIONS) + " " + PUBLIC]
    assert Path(paths.gate).read_text() == package.gate
    assert Path(paths.signers).read_text().strip() == signer.allowed_signers
    assert Path(paths.signers_pem).read_text() == signer.public_pem
    assert "install -m 0755 -o root -g root" in log.read_text()


@POSIX
def test_the_dropbear_tier_installs_nothing_when_its_files_are_not_root_owned(tmp_path):
    run, paths, home, _log = _dropbear_board(tmp_path)
    paths = gate.BoardPaths(**{**paths.__dict__, "root_uid": NOT_ME})
    before = (home / ".ssh" / "authorized_keys").read_text()
    signer = _signer()
    ok, reason = gate.install_dropbear_board(run, PUBLIC, board_ops.bundle(signer), "", paths=paths)
    if (ok, reason) == (False, "no-verifier"):
        pytest.skip("this computer has neither ssh-keygen 8.1+ nor openssl 1.1.1+ to check the self-test")
    assert (ok, reason) == (False, "not-root-owned")
    assert (home / ".ssh" / "authorized_keys").read_text() == before


# --- (3) the home gate of a board without sudo ---------------------------------------------------------------------

@POSIX
@OPENSSL
def test_the_home_gate_runs_only_what_the_framework_signed(tmp_path):
    """The home gate (no sudo; the weaker tier the person agreed to) keeps the framework's signing key in the
    account's own folder: a stolen key still cannot install a runtime the framework did not sign, and the board says
    its tier is home."""
    signer = _signer()
    board = _gate_board(tmp_path, root_uid=NOT_ME, signer=signer, home_signing=True)
    tarball = _tarball()
    assert _check(board, tarball, _signer().sign_bytes(tarball)).status != 0
    assert _check(board, tarball, signer.sign_bytes(tarball)).status == 0
    status = board("llmcf-gate status").out
    assert "tier=home" in status and "signing=yes" in status
    for target in (f"{board.home}/{gate.HOME_SIGNING_REL}/allowed_signers", f"{board.home}/.ssh/authorized_keys"):
        refused = board(f"llmcf-gate file-write {target}", b"x")
        assert gate.is_refusal(refused.status, refused.err), target


@POSIX
@OPENSSL
def test_pairing_the_home_gate_writes_the_signing_key_after_its_self_test(tmp_path):
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    home.chmod(0o755)

    def run(script, stdin=None):
        done = subprocess.run(["sh", "-c", str(script)], input=b"".join(stdin or []), capture_output=True,
                              env={"PATH": os.environ.get("PATH", ""), "HOME": str(home), "TMPDIR": str(tmp_path)},
                              timeout=60)
        return Result(done.returncode, done.stdout.decode(), done.stderr.decode())
    signer = _signer()
    package = board_ops.bundle(signer)
    assert gate.install_gate(run, PUBLIC, package.gate, package) == (True, "")
    signing = home / gate.HOME_SIGNING_REL
    assert (signing / "allowed_signers").read_text().strip() == signer.allowed_signers
    assert (signing / "signing_key.pem").read_text() == signer.public_pem
    bad = gate.GateBundle(gate=package.gate, signers=package.signers, pem=package.pem,
                          selftest=_signer().sign_bytes(gate.SELFTEST_DATA))
    assert gate.install_gate(run, PUBLIC, package.gate, bad)[0] is False


# --- (1) tier detection and the offer ------------------------------------------------------------------------------

DROPBEAR_FACTS = ("running=dropbear\nopenssh=no\ndropbear=yes\nsudo=password\nkeygen=none\nopenssl=yes\n"
                  "verifier=yes\nfamily=debian\ndietpi=yes\nmanager=apt-get\nmanager=dietpi-software\ninit=systemd\n"
                  "port=22\n")
NOSUDO_FACTS = "running=openssh\nopenssh=yes\nsudo=none\nkeygen=yes\nverifier=yes\nfamily=alpine\nmanager=apk\n"


def test_the_tier_facts_are_fixed_words_only():
    facts = key_tiers.TierFacts.parse(DROPBEAR_FACTS + "family=/home/radxa\nmanager=rm -rf\nsudo=$(id)\n",
                                      banner="SSH-2.0-dropbear_2022.83")
    assert facts.server == "dropbear" and facts.sudo == "password" and facts.family == "debian"
    assert facts.managers == ("apt-get", "dietpi-software") and facts.port == 22 and facts.can_verify
    assert key_tiers.TierFacts.parse("", banner="SSH-2.0-OpenSSH_9.2p1").server == "openssh"
    for value in facts.as_dict().values():
        assert "/" not in str(value)


@POSIX
def test_tier_facts_run_on_a_board_and_read_back(tmp_path):
    bin_dir = _bin(tmp_path, {"sudo": 'case "$*" in "-n true") exit 1 ;; esac; cat >/dev/null; exit 0'})
    script = gate.script("tier-facts")
    done = subprocess.run(["sh", "-c", str(script)], input=b"pw\n", capture_output=True, timeout=60,
                          env={"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}", "HOME": str(tmp_path),
                               "TMPDIR": str(tmp_path), "SSH_CONNECTION": "192.0.2.10 50000 192.0.2.20 2222"})
    facts = key_tiers.TierFacts.parse(done.stdout.decode())
    assert facts.sudo == "password" and facts.port == 2222 and facts.init in ("systemd", "openrc", "other")


def test_the_offer_follows_the_board_and_the_mode():
    dropbear = key_tiers.TierFacts.parse(DROPBEAR_FACTS)
    private = key_tiers.offer("no-sshd", dropbear, "private")
    assert private.choices == ("openssh", "dropbear", "password") and not private.refused
    enterprise = key_tiers.offer("no-sshd", dropbear, "enterprise")
    assert enterprise.choices == ("openssh", "password") and enterprise.refused == ("dropbear",)
    assert "Enterprise mode refuses" in enterprise.lines()[0]
    nosudo = key_tiers.TierFacts.parse(NOSUDO_FACTS)
    assert key_tiers.offer("no-sudo", nosudo, "private").choices == ("home", "password")
    assert key_tiers.offer("no-sudo", nosudo, "enterprise").choices == ("password",)
    assert key_tiers.offer("old-ssh", key_tiers.TierFacts(), "private").choices == ("password",)


# --- pairing: the person's choice, the audit log, the mode rule ----------------------------------------------------

class _Audit:
    def __init__(self):
        self.rows = []

    def record(self, actor, tool, *args, **kwargs):
        self.rows.append((actor, tool, args[2] if len(args) > 2 else "", kwargs.get("arguments", {})))


class _Board:
    """A paramiko stand-in whose board answers each of llmcf's setup steps by its verb."""

    def __init__(self, answers):
        self.answers, self.verbs = answers, []

    def exec_command(self, command, timeout=None):
        import io
        verb = next((v for v in ("root-board", "dropbear-board", "gate=", "tier-facts") if False), "")
        text = command
        for name, answer in self.answers.items():
            if name in text:
                verb = name
                break
        self.verbs.append(verb)
        out = io.BytesIO(self.answers.get(verb, "").encode())

        class Channel:
            def recv_exit_status(self):
                return 0

            def shutdown_write(self):
                pass
        out.channel = Channel()
        stdin = io.BytesIO()
        stdin.channel = out.channel
        return stdin, out, io.BytesIO()


class _Chooser(pairing.TierChooser):
    def __init__(self, choice, yes=()):
        self.choice, self.yes, self.offers, self.told = choice, set(yes), [], []

    def choose(self, offer):
        self.offers.append(offer)
        return self.choice

    def confirm(self, title, lines):
        return title in self.yes

    def trust_key(self, check):
        return "key" in self.yes

    def tell(self, lines):
        self.told.extend(lines)


def _answers(**more):
    # markers each script holds: the root step's own answer word, the Dropbear step's and the facts' first word
    return {"root-board=unavailable": "root-board=unavailable reason=no-sshd\n", "running=": DROPBEAR_FACTS,
            "dropbear-board=installed": "dropbear-board=installed\n", **more}


@pytest.fixture
def framework_key(tmp_path, monkeypatch):
    from orchestrator.utils import ssh_trust
    monkeypatch.setattr(ssh_trust, "client_key", lambda state_dir: (tmp_path / "key", PUBLIC))
    return tmp_path


def test_pairing_a_dropbear_board_installs_the_dropbear_tier_only_after_the_persons_choice(framework_key):
    """Regression (failed before: GateUnavailable, no key on a Dropbear board, nobody asked)."""
    board, audit, chooser = _Board(_answers()), _Audit(), _Chooser("dropbear")
    got = pairing.pair(board, framework_key, board_ops.bundle(_signer()), password="pw", chooser=chooser,
                       mode="private", audit=audit, via="cli")
    assert got == "dropbear" and got.tier == "dropbear"
    assert chooser.offers[0].choices == ("openssh", "dropbear", "password")
    assert [row[1] for row in audit.rows] == ["board_key_tier_offered", "board_key_tier_chosen",
                                              "board_key_tier_installed"]
    assert any("one step weaker" in line for line in chooser.told)


def test_without_a_person_to_ask_no_weaker_tier_is_ever_set_up(framework_key):
    from orchestrator.utils import ssh_trust
    board = _Board(_answers())
    with pytest.raises(ssh_trust.GateUnavailable) as refused:
        pairing.pair(board, framework_key, board_ops.bundle(_signer()), password="pw")
    assert refused.value.offer.choices == ("openssh", "dropbear", "password")
    assert "Dropbear tier" in str(refused.value) and "dropbear-board=installed" not in board.verbs


def test_enterprise_mode_refuses_the_weaker_tiers_and_says_why(framework_key):
    from orchestrator.utils import ssh_trust
    board, chooser = _Board(_answers()), _Chooser("dropbear")
    with pytest.raises(ssh_trust.GateUnavailable) as refused:
        pairing.pair(board, framework_key, board_ops.bundle(_signer()), password="pw", chooser=chooser,
                     mode="enterprise", audit=_Audit(), via="cli")
    assert refused.value.reason == "enterprise" and "dropbear-board=installed" not in board.verbs
    assert chooser.offers[0].choices == ("openssh", "password") and any("enterprise" in t for t in chooser.told)
    with pytest.raises(ssh_trust.GateUnavailable):   # also when a caller asks for the tier directly
        ssh_trust.install_public_key(board, PUBLIC, board_ops.bundle(_signer()), tier="home", mode="enterprise")


def test_password_login_stays_when_the_person_declines_every_key_tier(framework_key):
    board, chooser = _Board(_answers()), _Chooser("password")
    got = pairing.pair(board, framework_key, board_ops.bundle(_signer()), password="pw", chooser=chooser,
                       audit=_Audit(), via="gui")
    assert got == "password" and got.tier == "password" and "dropbear-board=installed" not in board.verbs


# --- (4) the switch to OpenSSH -----------------------------------------------------------------------------------

@pytest.mark.parametrize("family, manager, packages", [
    ("debian", "apt-get", ("openssh-server", "openssh-client")), ("fedora", "dnf", ("openssh-server",
                                                                                   "openssh-clients")),
    ("suse", "zypper", ("openssh-server", "openssh-clients")), ("arch", "pacman", ("openssh",)),
    ("alpine", "apk", ("openssh-server", "openssh-keygen"))])
def test_the_switch_plan_installs_openssh_with_the_boards_own_package_manager(family, manager, packages):
    facts = key_tiers.TierFacts(sudo="password", family=family, managers=(manager,), openssh="no")
    the_plan = openssh_switch.plan(facts)
    assert (the_plan.manager, the_plan.packages, the_plan.port) == (manager, packages, openssh_switch.SWITCH_PORT)
    assert any(" ".join(packages) in line for line in the_plan.lines)
    assert any("Dropbear stays" in line for line in the_plan.lines)
    present = openssh_switch.plan(key_tiers.TierFacts(sudo="nopass", family=family, managers=(manager,),
                                                      openssh="yes"))
    assert present.packages == () and "nothing is installed" in present.lines[0]


def test_the_switch_needs_sudo_and_a_known_package_manager():
    with pytest.raises(openssh_switch.SwitchUnavailable):
        openssh_switch.plan(key_tiers.TierFacts(sudo="none", managers=("apt-get",)))
    with pytest.raises(openssh_switch.SwitchUnavailable):
        openssh_switch.plan(key_tiers.TierFacts(sudo="nopass", managers=()))
    dietpi = openssh_switch.plan(key_tiers.TierFacts(sudo="nopass", family="debian", dietpi="yes",
                                                     managers=("apt-get", "dietpi-software")))
    assert dietpi.manager == "apt-get" and any("dietpi-software" in line for line in dietpi.lines)


@POSIX
def test_the_install_step_writes_the_port_first_checks_sshd_and_starts_it(tmp_path):
    """Nothing really runs as root: the sudo stub writes down what it was asked and runs only harmless commands."""
    log = tmp_path / "sudo.log"
    bin_dir = _bin(tmp_path, {"sudo": f'echo "$*" >> "{log}"; cat >/dev/null; exit 0'})
    the_plan = openssh_switch.plan(key_tiers.TierFacts(sudo="password", family="debian", managers=("apt-get",)))
    script = gate.script("openssh-install", the_plan.manager, str(the_plan.port))
    done = subprocess.run(["sh", "-c", str(script)], input=b"pw\n", capture_output=True, timeout=60,
                          env={"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}", "HOME": str(tmp_path)})
    out = done.stdout.decode()
    calls = log.read_text().splitlines()
    tee = next(i for i, c in enumerate(calls) if c.startswith(f"-S -p  tee {openssh_switch.DROPIN}"))
    assert any(" -t" in c for c in calls[tee:]), calls   # sshd -t after the port is written
    if "openssh=installed" in out:
        assert any("apt-get install -y --no-install-recommends openssh-server openssh-client" in c for c in calls)
    assert f"port={the_plan.port}" in out, out
    with pytest.raises(gate.GateRefused):
        gate.script("openssh-install", "curl", "22")


def test_the_switch_keeps_dropbear_until_the_separate_yes_and_logs_every_step(framework_key, monkeypatch):
    from orchestrator.remote.trust import KeyCheck
    from orchestrator.utils import ssh_trust
    order = []
    monkeypatch.setattr(openssh_switch, "install", lambda run, p, pw: order.append("install") or (True, "",
                                                                                                    "installed"))
    monkeypatch.setattr(openssh_switch, "wait_until_answers",
                        lambda host, port: order.append(("wait", port)) or "SSH-2.0-OpenSSH_9.2p1")
    monkeypatch.setattr(openssh_switch, "disable_dropbear", lambda run, pw: order.append("dropbear-off") or (True,
                                                                                                             ""))

    class Key:
        def get_name(self):
            return "ssh-ed25519"
    monkeypatch.setattr(ssh_trust, "fetch_host_key", lambda host, port: Key())
    monkeypatch.setattr(ssh_trust, "fingerprint", lambda key: "SHA256:" + "A" * 43)

    class Store:
        def __init__(self, *_a):
            pass

        def check(self, endpoint, key):
            return KeyCheck.of(endpoint, key, "known")
    monkeypatch.setattr("orchestrator.remote.trust.TrustStore", Store)
    fresh = _Board({"root-board=unavailable": "root-board=installed home=removed\n"})
    fresh.close = lambda: order.append("closed")
    monkeypatch.setattr(ssh_trust, "connect", lambda *a, **k: order.append("new-login") or fresh)
    for yes, off in ((("switch",), False), (("switch", "dropbear"), True)):
        order.clear()
        audit, chooser = _Audit(), _Chooser("openssh", yes=yes)
        got = pairing.pair(_Board(_answers()), framework_key, board_ops.bundle(_signer()), password="pw",
                           chooser=chooser, audit=audit, via="cli", host="board.example.com", user="dietpi")
        assert got.tier == "root" and got.endpoint.port == openssh_switch.SWITCH_PORT
        assert order[:3] == ["install", ("wait", openssh_switch.SWITCH_PORT), "new-login"]
        assert ("dropbear-off" in order) is off
        tools = [row[1] for row in audit.rows]
        assert tools[-1] == "board_dropbear_disabled" and "board_openssh_installed" in tools
        assert all("board.example.com" not in str(row[3]) and "dietpi" != row[3].get("user") for row in audit.rows)


def test_the_switch_does_nothing_without_the_yes_to_its_plan(framework_key, monkeypatch):
    from orchestrator.utils import ssh_trust
    monkeypatch.setattr(openssh_switch, "install", lambda *a: pytest.fail("installed without the yes"))
    with pytest.raises(ssh_trust.GateUnavailable) as refused:
        pairing.pair(_Board(_answers()), framework_key, board_ops.bundle(_signer()), password="pw",
                     chooser=_Chooser("openssh"), audit=_Audit(), via="cli", host="board.example.com", user="dietpi")
    assert refused.value.reason == "declined"


@pytest.mark.parametrize("manager, prepare", [("apt-get", "apt-get update"), ("apk", "apk update")])
def test_the_switch_reads_the_package_lists_first_like_the_installer(manager, prepare):
    """Regression (failed before: apt-get install ran on a board without package lists, as a fresh DietPi image ships,
    and found no openssh-server; install.sh's pkg_install reads them first, the switch did not)."""
    family = {"apt-get": "debian", "apk": "alpine"}[manager]
    the_plan = openssh_switch.plan(key_tiers.TierFacts(sudo="nopass", family=family, managers=(manager,)))
    body = str(gate.script("openssh-install", manager, str(the_plan.port)))
    assert f"asroot sh -c '{prepare}'" in body and "|| no lists" in body
    assert body.index(prepare) < body.index(openssh_switch.INSTALL_COMMANDS[manager])
    assert any(prepare in line for line in the_plan.lines)


def test_the_switch_never_runs_pacman_sy_and_says_when_the_database_is_missing():
    """Regression (failed before: pacman -S on a board without its package database failed as a plain 'install')."""
    body = str(gate.script("openssh-install", "pacman", "2222"))
    assert "pacman -Sy" not in body and "no no-package-db" in body
    assert body.index("no-package-db") < body.index(openssh_switch.INSTALL_COMMANDS["pacman"])
    assert "pacman -Syu" in openssh_switch.SWITCH_REASONS["no-package-db"]


def test_the_install_may_run_long_and_a_silent_board_is_told_plainly():
    """Regression (failed before: the install ran with the transport's 120-second silence limit, so a slow board's
    package manager was cut off halfway and the raw socket.timeout ended the pairing)."""
    import socket
    the_plan = openssh_switch.plan(key_tiers.TierFacts(sudo="nopass", family="debian", managers=("apt-get",)))
    seen = {}

    def run(call, stdin=None, timeout=120):
        seen["timeout"] = timeout
        return Result(0, "openssh=installed port=2222\n", "")
    assert openssh_switch.install(run, the_plan) == (True, "", "installed")
    assert seen["timeout"] >= 1800

    def silent(call, stdin=None, timeout=120):
        raise socket.timeout("timed out")
    assert openssh_switch.install(silent, the_plan) == (False, "install-timeout", "")


def test_a_failed_switch_names_its_own_reason_and_does_not_claim_nothing_changed(framework_key, monkeypatch):
    """Regression (failed before: GateUnavailable read the switch's reason in gate.SETUP_REASONS, so 'install' showed
    as a bare word and 'sshd-config' as the root step's Match-block text, both saying nothing on the board changed)."""
    from orchestrator.utils import ssh_trust
    for reason in ("install", "sshd-config", "no-answer"):
        if reason == "no-answer":
            monkeypatch.setattr(openssh_switch, "install", lambda *a: (True, "", "installed"))
            monkeypatch.setattr(openssh_switch, "wait_until_answers", lambda host, port: "")
        else:
            monkeypatch.setattr(openssh_switch, "install", lambda *a, r=reason: (False, r, ""))
        with pytest.raises(ssh_trust.GateUnavailable) as failed:
            pairing.pair(_Board(_answers()), framework_key, board_ops.bundle(_signer()), password="pw",
                         chooser=_Chooser("openssh", yes=("switch",)), audit=_Audit(), via="cli",
                         host="board.example.com", user="dietpi")
        text = str(failed.value)
        assert failed.value.reason == reason
        assert openssh_switch.SWITCH_REASONS[reason] in text, text
        assert "Match block" not in text and "nothing on the board changed" not in text
        assert "Dropbear" in text


# --- the mode setting, and (6) the tier of a paired board ---------------------------------------------------------

def test_the_mode_is_private_by_default_and_only_the_person_changes_it():
    from orchestrator.Core.config_manager import framework_mode
    from orchestrator.llm.settings_change import PERSON_ONLY_SETTINGS, person_only_text
    assert framework_mode({}) == "private" and framework_mode({"framework_mode": "enterprise"}) == "enterprise"
    assert framework_mode({"framework_mode": "Enterprise "}) == "enterprise"
    assert framework_mode({"framework_mode": "anything"}) == "private" and framework_mode(None) == "private"
    assert "framework_mode" in PERSON_ONLY_SETTINGS and "never change" in person_only_text("framework_mode")


def test_the_tier_of_a_board_shows_in_its_listing_and_mojo_names_the_way_up(tmp_path):
    from types import SimpleNamespace

    from orchestrator.Core.mojo.checks import devices
    from orchestrator.runtime.device import person_steps
    from orchestrator.runtime.device.trusted import TrustedDevices, record_key_tier

    class Key:
        def get_name(self):
            return "ssh-ed25519"

        def asbytes(self):
            return b"board-key"
    from orchestrator.utils import ssh_trust
    store = TrustedDevices(tmp_path)
    old = ssh_trust.fingerprint
    try:
        ssh_trust.fingerprint = lambda key: "SHA256:" + "B" * 43
        device = store.record("board.example.com", 22, "dietpi", Key(), via="cli", paired=True, label="garage")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"the trusted store needs a real key here ({e.__class__.__name__})")
    finally:
        ssh_trust.fingerprint = old
    record_key_tier(tmp_path, device.device_id, "dropbear")
    assert TrustedDevices(tmp_path).key_tier(device.device_id) == "dropbear"
    assert person_steps.trusted_rows(tmp_path)[0]["key_tier"] == "dropbear"
    findings = devices._weaker_keys(TrustedDevices(tmp_path), [SimpleNamespace(device_id=device.device_id,
                                                                               label="garage",
                                                                               address="board.example.com:22")])
    assert len(findings) == 1 and "one step weaker" in findings[0].message
    assert "switch to OpenSSH" in findings[0].fix_hint
    record_key_tier(tmp_path, device.device_id, "root")
    assert devices._weaker_keys(TrustedDevices(tmp_path), [device]) == []
    # Regression (failed before: a board paired with a weaker tier in private mode stayed a plain INFO line after
    # the person switched the framework to enterprise mode, which refuses that tier)
    record_key_tier(tmp_path, device.device_id, "home")
    framework = SimpleNamespace(config=SimpleNamespace(
        user_state_dir=str(tmp_path), get=lambda key, default=None: {"framework_mode": "enterprise"}.get(key, default)))
    found = [f for f in devices.check_devices(framework) if f.code.endswith(".key_tier")]
    assert len(found) == 1 and found[0].severity == devices.Severity.WARN
    assert "enterprise mode" in found[0].message and "pair" in found[0].fix_hint.lower()
    framework.config.get = lambda key, default=None: default
    found = [f for f in devices.check_devices(framework) if f.code.endswith(".key_tier")]
    assert len(found) == 1 and found[0].severity == devices.Severity.INFO
