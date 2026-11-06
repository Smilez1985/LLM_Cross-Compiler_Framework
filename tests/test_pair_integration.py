"""The seams of the two pairing ways during the hardware probe (the probe kit's result file and the short code) and
the decided fixes around them:

- one pairing core: the short code's pairing goes through the one complete_pairing (an offer of the way pake, the
  fingerprints recomputed from the key lines, the audit row); the old vocabulary name delegates;
- one install on the board: --pair-code hands the received content to the probe's own pair_install_content (a shell
  function in hardware_probe.sh, a module function in hardware_probe.py), which runs the probe kit's very root step:
  the root-owned key line and the signer files end up installed and the result reports PAIR_* like the kit;
- the SUDO fact of a probe run with sudo or doas is the calling account's; doas names the account like sudo;
- the root step of the shell probe has the same time limit as the Python probe's;
- the module wizard's probe import pairs (the one import path);
- a pairing whose probe could not be stored afterwards says the board IS paired; the same file imports again;
- refusals and pairing-by-code errors in the person's language;
- an IPv6 link-local board is kept with its zone, or at its IPv4 address when it has one.

Documentation-style addresses only (fe80::db8:... link-local, 169.254.0.x link-local, 2001:db8::/32) and names
under example.test.
"""

import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from orchestrator.remote import gate, pair_pake, pairing
from orchestrator.remote.address import Endpoint, stored_address, valid_host
from orchestrator.remote.pair_pake import FoundBoard
from orchestrator.remote.trust import PAIRING_WAYS, TrustStore, pairing_overview, paired_boards
from orchestrator.runtime import hardware_probe as probe

REPO = Path(__file__).resolve().parents[1]
PROBE_SH = REPO / "scripts" / "hardware_probe.sh"
POSIX = pytest.mark.skipif(os.name == "nt" or shutil.which("sh") is None,
                           reason="the probe's pairing runs a POSIX sh root step on the boards")
KEYGEN = pytest.mark.skipif(shutil.which("ssh-keygen") is None,
                            reason="ssh-keygen reads the host key and checks the self-test signature")
NONCE = "0123456789abcdef0123456789abcdef"


class Log:
    def __init__(self):
        self.rows = []

    def record(self, *args, **kwargs):
        self.rows.append((args, kwargs))


def _signer():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from orchestrator.Core.community_pack import BoardSigner
    return BoardSigner(Ed25519PrivateKey.generate())


def _host_key_line(comment="root@board.example.test"):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    raw = Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.OpenSSH,
                                                                 serialization.PublicFormat.OpenSSH)
    return raw.decode("ascii") + " " + comment


def _content(public, package, paths, nonce=NONCE):
    """The framework's pairing content as llmcf-pair writes it (one JSON line), with the kit's root step rendered for
    the test's board folders."""
    step = gate.probe_kit_root_step(public, package, paths=paths)
    return json.dumps({"format": pair_pake.CONTENT_FORMAT, "public_key": public, "signers": package.signers,
                       "nonce": nonce, "root_step_b64": base64.b64encode(step.encode()).decode(),
                       "gate_b64": base64.b64encode(package.gate.encode()).decode()}, separators=(",", ":"))


# --- one pairing core, one vocabulary ------------------------------------------------------------------------------

def test_one_vocabulary_of_pairing_ways():
    assert PAIRING_WAYS == ("file", "pake")
    assert pairing.PAIRING_METHODS is PAIRING_WAYS   # the old name delegates


def test_the_content_carries_the_kits_own_root_step(tmp_path):
    """The short code sends the very root step the probe kit carries (gate.probe_kit_root_step) and its gate, so the
    board installs both ways with one implementation."""
    from orchestrator.runtime.device import board_ops
    signer = _signer()
    content = pair_pake.pairing_content(tmp_path, board_ops.pairing_bundle(lambda: signer, tmp_path))
    bundle = board_ops.pairing_bundle(lambda: signer, tmp_path)
    step = base64.b64decode(content["root_step_b64"]).decode()
    assert step == gate.probe_kit_root_step(content["public_key"], bundle)
    assert base64.b64decode(content["gate_b64"]).decode() == content["gate"]
    assert "PRIVATE" not in json.dumps(content)


def test_the_board_s_own_fingerprint_strings_count_for_nothing():
    pytest.importorskip("paramiko")
    from orchestrator.remote.trust import fingerprint, parse_public_key
    line = _host_key_line()
    board = FoundBoard("llmcf-1." + pair_pake.SERVICE, "169.254.0.2", 40000, link="cable")
    result = pair_pake.PakeResult(board, {}, (pair_pake.HostKeyFact("ssh-ed25519", " ".join(line.split()[:2]),
                                                                    "SHA256:" + "Z" * 43),))
    built = pair_pake.pairing_result(result, NONCE)
    assert built.host_keys == (("ed25519", fingerprint(parse_public_key(line))),)
    assert (built.way, built.tier, built.state, built.nonce) == ("pake", "root", "installed", NONCE)


# --- the probe's install of the received content (the seam of both ways) -------------------------------------------

def _board(tmp_path):
    """The board-signing harness (fake sudo, install, sshd, systemctl; `id -u` answers 0) and a sysroot with the
    board's SSH host key."""
    from test_board_signing import PUBLIC, _root_board
    from orchestrator.runtime.device import board_ops
    run, paths, home, config, log = _root_board(tmp_path / "rb", account="root")
    signer = _signer()
    package = board_ops.bundle(signer)
    line = _host_key_line()
    sysroot = tmp_path / "board"
    (sysroot / "etc" / "ssh").mkdir(parents=True)
    (sysroot / "etc" / "ssh" / "ssh_host_ed25519_key.pub").write_bytes((line + "\n").encode("ascii"))
    stubs = tmp_path / "rb" / "bin"
    return PUBLIC, signer, package, paths, home, config, line, sysroot, stubs


def _fingerprint(line):
    return probe.host_key_fingerprint(line, ("ssh-ed25519",))


@POSIX
@KEYGEN
def test_the_python_probe_installs_the_received_content_with_the_kits_root_step(tmp_path):
    public, signer, package, paths, home, config, line, sysroot, stubs = _board(tmp_path)
    content = tmp_path / "content.json"
    content.write_bytes(_content(public, package, paths).encode())
    env = {"PATH": f"{stubs}{os.pathsep}{os.environ.get('PATH', '')}", "HOME": str(home), "TMPDIR": str(tmp_path)}
    block = probe.pair_install_content(str(content), account="radxa", root=str(sysroot), euid=0, env=env)
    assert block == {"PAIR_STATE": "installed", "PAIR_NONCE": NONCE, "PAIR_HOSTKEY_ED25519": _fingerprint(line)}
    assert (Path(paths.keys_dir) / "radxa").read_text().strip() == gate.root_key_line(public, paths.gate)
    assert Path(paths.signers).read_text().strip() == signer.allowed_signers
    assert config.read_text().count("Match User radxa") == 1
    assert not list(tmp_path.glob("llmcf-kit.*")), "the private kit folder is removed"
    # content that is not the framework's installs nothing
    content.write_bytes(b'{"format":"x"}')
    assert probe.pair_install_content(str(content), account="radxa", root=str(sysroot), euid=0, env=env) == \
        {"PAIR_STATE": "skipped", "PAIR_REASON": "no-kit"}


@POSIX
@KEYGEN
@pytest.mark.parametrize("shell", ["dash", "bash"])
def test_the_shell_probe_pairs_by_code_like_the_kit(tmp_path, shell):
    """Regression (the seam of the two ways): `hardware_probe.sh --pair-code` with a stand-in llmcf-pair ends with the
    root-owned key line and the signer files installed, by the probe's own function pair_install_content running the
    kit's root step, and the result reports PAIR_* like the kit. Before, the shell probe had no install: the content
    was dropped with 'This probe has no pairing install'."""
    if shutil.which(shell) is None:
        pytest.skip(f"no {shell}")
    from test_hardware_probe import make_sysroot
    public, signer, package, paths, home, config, line, _, stubs = _board(tmp_path)
    sysroot = make_sysroot(tmp_path / "sys", "rk3566")
    (sysroot / "etc" / "ssh").mkdir(parents=True, exist_ok=True)
    (sysroot / "etc" / "ssh" / "ssh_host_ed25519_key.pub").write_bytes((line + "\n").encode("ascii"))
    kit = tmp_path / "kit"
    kit.mkdir()
    shutil.copyfile(PROBE_SH, kit / "hardware_probe.sh")
    sent = tmp_path / "sent.json"
    sent.write_bytes(_content(public, package, paths).encode())
    fake = kit / "llmcf-pair"
    fake.write_bytes(("#!/bin/sh\n"
                      'while [ $# -gt 0 ]; do case "$1" in --out) out=$2; shift 2;; *) shift;; esac; done\n'
                      f'cp "{sent}" "$out"\nexit 0\n').encode())
    fake.chmod(0o755)
    env = {"PATH": f"{stubs}{os.pathsep}{os.environ.get('PATH', '')}", "HOME": str(home), "TMPDIR": str(tmp_path),
           "HW_PROBE_SYSROOT": str(sysroot), "SUDO_USER": "radxa", "LC_ALL": "C"}
    done = subprocess.run([shutil.which(shell), str(kit / "hardware_probe.sh"), "--no-toolchain", "--pair-code",
                           "--output", "-"], capture_output=True, text=True, timeout=300, env=env,
                          stdin=subprocess.DEVNULL, check=False)
    from orchestrator.runtime.profile import parse_profile
    profile = parse_profile(done.stdout)
    assert profile.get("PAIR_STATE") == "installed", done.stderr
    assert profile.get("PAIR_NONCE") == NONCE and profile.get("PAIR_HOSTKEY_ED25519") == _fingerprint(line)
    assert (Path(paths.keys_dir) / "radxa").read_text().strip() == gate.root_key_line(public, paths.gate)
    assert Path(paths.signers).read_text().strip() == signer.allowed_signers
    assert "Paired by code" in done.stderr and "no pairing install" not in done.stderr
    assert "board.example.test" not in done.stdout


@POSIX
def test_the_shell_root_step_has_the_python_probe_s_time_limit():
    text = PROBE_SH.read_text(encoding="utf-8")
    assert f"PAIR_STEP_SECONDS={probe.PAIR_STEP_SECONDS}" in text and probe.PAIR_STEP_SECONDS == 300
    assert 'run_limited "$PAIR_STEP_SECONDS" "$1/llmcf-gate" sh "$1/root-board.sh"' in text


@POSIX
def test_the_shell_watchdog_stops_a_root_step_that_hangs(tmp_path):
    """run_limited without a working timeout(1) (old busybox): the watchdog stops the step after its seconds."""
    text = PROBE_SH.read_text(encoding="utf-8")
    body = text.split("\nrun_limited() {", 1)[1].split("\n}\n", 1)[0]
    script = tmp_path / "limited.sh"
    script.write_bytes(("TIMEOUT=''\nrun_limited() {" + body + "\n}\n"
                        f'run_limited 1 /dev/null sh -c "sleep 30"; echo "rc=$?"\n'
                        f'run_limited 5 /dev/null sh -c "cat; echo done"; echo "rc=$?"\n').encode())
    done = subprocess.run([shutil.which("sh"), str(script)], capture_output=True, text=True, timeout=60)
    assert "rc=124" in done.stdout and "done\nrc=0" in done.stdout, done.stdout + done.stderr


# --- the SUDO fact of a probe run with sudo or doas, and doas names the account --------------------------------------

def _run_py_probe(root, bindir, home, **env):
    full = {"PATH": str(bindir), "HW_PROBE_SYSROOT": str(root), "HOME": str(home), "LC_ALL": "C", **env}
    done = subprocess.run([sys.executable, "-I", str(REPO / "scripts" / "hardware_probe.py"), "--no-toolchain",
                           "--output", "-"], env=full, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          timeout=120, check=False)
    from orchestrator.runtime.profile import parse_profile
    return parse_profile(done.stdout)


def _run_sh_probe(root, bindir, home, **env):
    full = {"PATH": str(bindir), "HW_PROBE_SYSROOT": str(root), "HOME": str(home), "LC_ALL": "C", **env}
    done = subprocess.run([shutil.which("sh"), str(PROBE_SH), "--no-toolchain", "--output", "-"], env=full,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120, check=False)
    from orchestrator.runtime.profile import parse_profile
    return parse_profile(done.stdout)


@POSIX
@pytest.mark.parametrize("run", [_run_py_probe, _run_sh_probe], ids=["py", "sh"])
@pytest.mark.parametrize("who, sudo_says, expected", [
    ({}, "exit 0", "root"),                                        # a real root login
    ({"SUDO_USER": "radxa"}, "exit 0", "nopasswd"),                # the account has NOPASSWD
    ({"SUDO_USER": "radxa"}, "exit 1", "password"),                # the account needs its password
    ({"DOAS_USER": "radxa"}, "exit 1", "password"),                # doas (Alpine) names the account too
    ({"SUDO_USER": "radxa"}, "exit 126", "password"),              # cannot be checked: conservative
])
def test_the_sudo_fact_of_a_probe_run_as_root_is_the_calling_account_s(tmp_path, run, who, sudo_says, expected):
    """Regression: a probe kit's probe run with sudo recorded SUDO=root, so the board's stored facts said 'sudo
    without a password' for an account that needs its password (AI tools and the key-tier proposal read it)."""
    from test_hardware_probe import make_sysroot, tool_dir
    from test_hardware_probe_access import fake_id, program
    root = make_sysroot(tmp_path, "rk3566")
    calls = tmp_path / "sudo-calls.txt"
    program(root, "/usr/bin/sudo", f'echo "$*" >> "{calls}"; case " $* " in *" -u radxa "*) {sudo_says} ;; '
                                   'esac; exit 0')
    bindir = tool_dir(tmp_path)
    fake_id(bindir, 0)
    profile = run(root, bindir, tmp_path, **who)
    assert profile.get("SUDO") == expected
    if who:
        assert calls.read_text().split("\n")[0].split()[:3] == ["-n", "-u", "radxa"]   # -n: never a prompt


def test_doas_names_the_account_like_sudo():
    assert probe.invoking_account({"DOAS_USER": "radxa"}) == "radxa"
    assert probe.invoking_account({"SUDO_USER": "radxa", "DOAS_USER": "other"}) == "radxa"
    assert probe.invoking_account({"SUDO_USER": "root"}) is None and probe.invoking_account({}) is None
    assert probe.invoking_account({"DOAS_USER": "-x"}) is None


# --- the module wizard's probe import pairs (one import path) --------------------------------------------------------

def test_the_module_wizards_probe_import_completes_a_kit_pairing(tmp_path, monkeypatch):
    """Regression: the module wizard stored a probe file with HardwareProbes.import_file and silently dropped a probe
    kit's pairing block (nothing pinned, the offer left open). It goes through scan_steps.import_probe now."""
    pytest.importorskip("PySide6")
    pytest.importorskip("paramiko")
    from test_probe_pairing import fingerprint_of, result_file
    from orchestrator.gui import wizards
    from orchestrator.runtime.probe_service import HardwareProbes
    from orchestrator.runtime.scan_steps import export_probe_kit
    state = tmp_path / "state"
    state.mkdir()
    kit = export_probe_kit(state, tmp_path / "kit", signer=_signer(), via="cli", audit=Log())
    line = _host_key_line()
    path = result_file(tmp_path, {"PAIR_STATE": "installed", "PAIR_NONCE": kit.offer.nonce,
                                  "PAIR_HOSTKEY_ED25519": fingerprint_of(line)})
    monkeypatch.setattr(HardwareProbes, "for_framework", classmethod(lambda cls, fw: cls(state)))
    log = Log()
    monkeypatch.setattr("orchestrator.runtime.device.person_steps.device_audit", lambda fw, home: log)
    holder = type("W", (), {"framework_manager": None})()
    probe_id = wizards.ModuleCreationWizard._store_probe(holder, str(path))
    assert probe_id and TrustStore(state).pinned(fingerprint_of(line), "ssh-ed25519")
    assert pairing.offer_problem(state, kit.offer.nonce) == "used"
    assert "Paired" in holder.pairing_note
    assert [row[0][1] for row in log.rows] == ["board_paired"]


# --- paired, but the probe could not be stored -------------------------------------------------------------------

def test_a_failed_store_after_the_pairing_says_the_board_is_paired(tmp_path, monkeypatch):
    """Regression: complete_pairing pinned the board and used its offer, then storing the probe failed (a full disk):
    the person read only 'import failed', and importing the file again was refused as 'used'. Now the error says the
    board IS paired, and importing the same file again stores the hardware data."""
    pytest.importorskip("paramiko")
    from test_probe_pairing import fingerprint_of, result_file
    from orchestrator.runtime.probe_service import HardwareProbes
    from orchestrator.runtime.scan_steps import PairedButNotStored, export_probe_kit, import_probe
    state = tmp_path / "state"
    state.mkdir()
    kit = export_probe_kit(state, tmp_path / "kit", signer=_signer(), via="cli", audit=Log())
    fp = fingerprint_of(_host_key_line())
    path = result_file(tmp_path, {"PAIR_STATE": "installed", "PAIR_NONCE": kit.offer.nonce,
                                  "PAIR_HOSTKEY_ED25519": fp})
    probes = HardwareProbes(state)
    real = probes.import_text

    def full(*a, **k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(probes, "import_text", full)
    with pytest.raises(PairedButNotStored) as failed:
        import_probe(probes, path, via="cli", audit=Log())
    assert TrustStore(state).pinned(fp, "ssh-ed25519")
    assert "IS paired" in failed.value.text("en") and "IST gekoppelt" in failed.value.text("de")
    monkeypatch.setattr(probes, "import_text", real)
    again = import_probe(probes, path, via="cli", audit=Log())
    assert again.pairing.state == "paired" and again.pairing.done.again
    assert again.probe_id == failed.value.done.device_id


def test_the_cli_says_the_board_is_paired_when_its_data_was_not_stored(tmp_path, monkeypatch):
    pytest.importorskip("paramiko")
    from click.testing import CliRunner

    from test_probe_pairing import fingerprint_of, result_file
    from orchestrator import cli as cli_module
    from orchestrator.runtime.probe_service import HardwareProbes
    from orchestrator.runtime.scan_steps import export_probe_kit
    home = tmp_path / "state"
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(home))
    kit = export_probe_kit(home, tmp_path / "kit", signer=_signer(), via="cli", audit=Log())
    path = result_file(tmp_path, {"PAIR_STATE": "installed", "PAIR_NONCE": kit.offer.nonce,
                                  "PAIR_HOSTKEY_ED25519": fingerprint_of(_host_key_line())})

    def full(self, *a, **k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(HardwareProbes, "import_text", full)
    done = CliRunner().invoke(cli_module.cli, ["-q", "scan", "--import", str(path)])
    out = " ".join(done.output.split())
    assert done.exit_code == 1 and "The board IS paired" in out and "Import the same result file again" in out


# --- refusals and pairing-by-code errors in the person's language ----------------------------------------------------

@pytest.mark.parametrize("kind", ["channel", "invalid", "unknown", "expired", "used", "audit", "no-key"])
def test_every_refusal_has_its_english_and_german_text(kind):
    refused = pairing.PairingRefused("English detail for the log", kind)
    en, de = refused.text("en"), refused.text("de")
    assert en and de and en != de and not en.startswith("pairing.") and not de.startswith("pairing.")


def test_a_board_reason_is_said_in_the_person_s_language():
    refused = pairing.PairingRefused("x", "skipped", "not-root")
    assert "sudo" in refused.text("en") and "nicht gekoppelt" in refused.text("de")
    odd = pairing.PairingRefused("x", "failed", "sshd-match")
    assert "sshd-match" in odd.text("en") and "sshd-match" in odd.text("de")


@pytest.mark.parametrize("kind", list(pair_pake.KINDS))
def test_pairing_by_code_errors_in_english_and_german(kind):
    error = pair_pake.PakeError("connection reset", kind)
    assert error.text("en") != error.text("de") and not error.text("de").startswith("boards.")


def test_the_cli_refusal_of_pairing_by_code_is_localized(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from orchestrator import cli as cli_module
    from orchestrator.runtime.device import person_steps
    from orchestrator.utils import locality
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(cli_module, "_interactive", lambda: True)
    monkeypatch.setattr(cli_module, "_config_language", lambda ctx: "de")
    monkeypatch.setattr(locality, "local_links", lambda: [locality.LocalLink("eth0", "169.254.0.1", "cable")])
    board = FoundBoard("llmcf-1." + pair_pake.SERVICE, "169.254.0.2", 40000, link="cable")
    monkeypatch.setattr(pair_pake, "browse", lambda wait, links: [board])

    def refuse(*a, **k):
        raise pairing.PairingRefused("The framework has no public key or no signing key", "no-key")
    monkeypatch.setattr(person_steps, "pair_by_code", refuse)
    done = CliRunner().invoke(cli_module.cli, ["-q", "deploy", "devices", "pair-code"], input="12345678\n")
    assert done.exit_code == 1 and "Signierschlüssel" in done.output


# --- an IPv6 link-local board keeps its zone, or is kept at its IPv4 address ----------------------------------------

def test_a_link_local_ipv6_address_with_its_zone_is_an_address():
    assert valid_host("fe80::db8:2%eth0") and valid_host("fe80::db8:2%12")
    assert not valid_host("2001:db8::2%eth0"), "a zone only on a link-local address"
    assert not valid_host("192.0.2.5%eth0") and not valid_host("fe80::db8:2%eth0;x")
    assert stored_address("fe80::db8:2%eth0") == "fe80::db8:2%eth0"
    assert Endpoint.parse(stored_address("[fe80::db8:2%eth0]:2222")) == Endpoint("fe80::db8:2%eth0", 2222)


def _answer(instance, host, port, addresses):
    """An mDNS answer (PTR, SRV, A/AAAA) as the board's llmcf-pair sends it."""
    import socket
    import struct

    def name(text):
        return b"".join(bytes([len(p)]) + p.encode() for p in text.rstrip(".").split(".")) + b"\0"

    def record(owner, rtype, rdata):
        return name(owner) + struct.pack(">HHIH", rtype, 1, 120, len(rdata)) + rdata
    records = [record(pair_pake.SERVICE, 12, name(instance)),
               record(instance, 33, struct.pack(">HHH", 0, 0, port) + name(host))]
    for address in addresses:
        family, rtype = (socket.AF_INET6, 28) if ":" in address else (socket.AF_INET, 1)
        records.append(record(host, rtype, socket.inet_pton(family, address)))
    return struct.pack(">HHHHHH", 0, 0x8400, 0, len(records), 0, 0) + b"".join(records)


def test_a_board_answering_over_ipv6_is_kept_at_its_ipv4_address_when_it_has_one():
    """Regression: a board found over IPv6 link-local was pinned for fe80::... without its zone, an address no later
    SSH connection can use. The board's own IPv4 address on the link is preferred now."""
    instance = "llmcf-ab12." + pair_pake.SERVICE
    packet = _answer(instance, "board.local", 40123, ["fe80::db8:2", "169.254.0.2"])
    found = pair_pake.boards_in(packet, ("fe80::db8:2", 5353, 0, 3), link="cable")
    assert len(found) == 1 and found[0].address == "fe80::db8:2" and found[0].ipv4 == "169.254.0.2"
    assert found[0].ssh_host == "169.254.0.2"


def test_a_board_with_only_a_link_local_ipv6_address_keeps_its_zone(monkeypatch):
    instance = "llmcf-ab12." + pair_pake.SERVICE
    packet = _answer(instance, "board.local", 40123, ["fe80::db8:2"])
    found = pair_pake.boards_in(packet, ("fe80::db8:2", 5353, 0, 3), link="cable")[0]
    monkeypatch.setattr(pair_pake.socket, "if_indextoname", lambda index: {3: "eth0"}[index], raising=False)
    assert found.ssh_host == "fe80::db8:2%eth0" and valid_host(found.ssh_host)
    monkeypatch.setattr(pair_pake.socket, "if_indextoname", lambda index: (_ for _ in ()).throw(OSError()),
                        raising=False)
    assert found.ssh_host == "fe80::db8:2%3"   # the interface's number when its name cannot be read


def test_the_paired_board_record_keeps_the_address_and_no_ai_reads_it(tmp_path):
    pytest.importorskip("paramiko")
    line = "ssh-ed25519 " + base64.b64encode(b"\0" * 40).decode() + " llm-cross-compiler-framework"
    offer = pairing.create_offer(tmp_path, public_line=line, signers="llmcf ssh-ed25519 AAAAexample", via="cli",
                                 audit=Log(), way="pake")
    result = pairing.PairingResult(state="installed", nonce=offer.nonce, host_keys=(("ed25519", "SHA256:" + "C" * 43),),
                                   way="pake")
    done = pairing.complete_pairing(tmp_path, result, via="cli", audit=Log(), address="fe80::db8:2%eth0")
    assert done.address == "fe80::db8:2%eth0"
    assert [b["address"] for b in paired_boards(tmp_path)] == ["fe80::db8:2%eth0"]
    assert "fe80" not in json.dumps(pairing_overview(tmp_path))
    bad = pairing.create_offer(tmp_path, public_line=line, signers="llmcf ssh-ed25519 AAAAexample", via="cli",
                               audit=Log(), way="pake")
    result = pairing.PairingResult(state="installed", nonce=bad.nonce, host_keys=(("ed25519", "SHA256:" + "D" * 43),),
                                   way="pake")
    assert pairing.complete_pairing(tmp_path, result, via="cli", audit=Log(), address="-oProxyCommand=x").address == ""
