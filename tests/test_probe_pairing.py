"""Pairing during the hardware probe, way 1 (the air-gapped probe kit and its result file) and the pairing core both
ways share (orchestrator/remote/pairing.py create_offer, PairingResult, complete_pairing; remote/trust.py pinned).

- The kit carries the framework's PUBLIC key line, its signer file and a one-time nonce, plus the framework's own root
  step (gate.probe_kit_root_step: root_board_script in its as-root form), and nothing secret.
- The probe (.py and .sh) runs that step only as root and writes the board's host key fingerprints and the nonce;
  without sudo it says plainly that pairing was skipped and the hardware data stays usable.
- Importing the result completes the pairing once: the host key is pinned (first_use trusts it without the question),
  the board is marked paired with its key tier; a wrong, expired or used nonce is refused with the hardware data kept.
- Only the person pairs: an AI's channel is refused, and no module an AI can reach names these steps.

Documentation addresses and names only (192.0.2.0/24, example.test)."""

import base64
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from orchestrator.remote import gate, pairing
from orchestrator.remote.address import Endpoint
from orchestrator.remote.trust import (KeyCheck, OFFERS_FILE, PAIRED_FILE, TrustStore, device_id_for,
                                       pairing_overview, parse_public_key)
from orchestrator.runtime import hardware_probe as probe
from orchestrator.runtime.probe_service import HardwareProbes
from orchestrator.runtime.profile import parse_profile
from orchestrator.runtime.scan_steps import KIT_PAIR_DIR, export_probe_kit, import_probe
from orchestrator.utils import ssh_trust

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "hardware_probe"
PROBE_SH = REPO / "scripts" / "hardware_probe.sh"
POSIX = pytest.mark.skipif(os.name == "nt" or shutil.which("sh") is None,
                           reason="the probe's pairing runs a POSIX sh root step; Windows boards are not paired yet")


class Log:
    """An audit log that keeps its rows."""

    def __init__(self):
        self.rows = []

    def record(self, *args, **kwargs):
        self.rows.append((args, kwargs))


def _signer():
    from orchestrator.Core.community_pack import BoardSigner
    return BoardSigner(Ed25519PrivateKey.generate())


def host_key_line(comment="root@board.example.test"):
    key = Ed25519PrivateKey.generate().public_key()
    raw = key.public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode("ascii")
    return raw + " " + comment


def fingerprint_of(line):
    return ssh_trust.fingerprint(parse_public_key(line))


def sysroot_with_host_key(tmp_path, line):
    root = tmp_path / "board"
    (root / "etc" / "ssh").mkdir(parents=True)
    (root / "etc" / "ssh" / "ssh_host_ed25519_key.pub").write_bytes((line + "\n").encode("ascii"))
    return root


class Done:
    def __init__(self, out):
        self.stdout = out.encode("ascii")


def installed_run(calls):
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return Done("root-board=installed home=none\n")
    return run


def result_file(tmp_path, block, fixture="schema2_rk3566_dietpi.txt"):
    text = (FIXTURES / fixture).read_text(encoding="utf-8").rstrip("\n") + "\n"
    text += "".join(f"{k}={v}\n" for k, v in block.items())
    path = tmp_path / "result" / "target_hardware_config.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path


@pytest.fixture
def state(tmp_path):
    folder = tmp_path / "state"
    folder.mkdir()
    return folder


@pytest.fixture
def kit(tmp_path, state):
    return export_probe_kit(state, tmp_path / "kit", signer=_signer(), via="cli", audit=Log())


# --- the pairing core (both ways) -------------------------------------------------------------------------------

def _offer(state, **kw):
    line = "ssh-ed25519 " + base64.b64encode(b"\0" * 40).decode() + " llm-cross-compiler-framework"
    return pairing.create_offer(state, public_line=line, signers="llmcf ssh-ed25519 AAAAexample", via="cli",
                                audit=Log(), **kw)


def _result(nonce, fp, **kw):
    return pairing.PairingResult(state="installed", nonce=nonce, host_keys=(("ed25519", fp),), **kw)


FP = "SHA256:" + "A" * 43


def test_an_offer_is_one_random_nonce_kept_only_as_its_hash(state):
    first, second = _offer(state), _offer(state)
    assert re.fullmatch(r"[0-9a-f]{32}", first.nonce) and first.nonce != second.nonce
    text = (state / OFFERS_FILE).read_text(encoding="utf-8")
    assert first.nonce not in text and second.nonce not in text
    assert pairing.offer_problem(state, first.nonce) == ""
    if os.name != "nt":
        assert (state / OFFERS_FILE).stat().st_mode & 0o077 == 0   # owner-only


def test_complete_pairing_pins_the_host_keys_once(state):
    offer = _offer(state)
    log = Log()
    done = pairing.complete_pairing(state, _result(offer.nonce, FP), via="gui", audit=log, label="Board A")
    assert done.device_id == device_id_for(FP) and done.tier == "root"
    pinned = TrustStore(state).pinned(FP, "ssh-ed25519")
    assert pinned and pinned["label"] == "Board A" and pinned["tier"] == "root"
    assert TrustStore(state).pinned(FP, "ssh-rsa") is None   # another key family is not this pin
    assert log.rows and log.rows[0][0][1] == "board_paired"
    with pytest.raises(pairing.PairingRefused) as again:
        pairing.complete_pairing(state, _result(offer.nonce, FP), via="gui", audit=Log())
    assert again.value.kind == "used"


@pytest.mark.parametrize("case, kind", [("unknown", "unknown"), ("expired", "expired"), ("way", "unknown")])
def test_a_nonce_without_an_open_offer_is_refused(state, case, kind):
    now = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    offer = _offer(state, now=now)
    nonce = "0" * 32 if case == "unknown" else offer.nonce
    later = now + timedelta(hours=25) if case == "expired" else now
    result = _result(nonce, FP, way="pake" if case == "way" else "file")
    with pytest.raises(pairing.PairingRefused) as refused:
        pairing.complete_pairing(state, result, via="cli", audit=Log(), now=later)
    assert refused.value.kind == kind
    assert not (state / PAIRED_FILE).exists()


def test_without_an_audit_row_nothing_is_paired(state):
    offer = _offer(state)
    with pytest.raises(pairing.PairingRefused) as refused:
        pairing.complete_pairing(state, _result(offer.nonce, FP), via="cli", audit=None)
    assert refused.value.kind == "audit" and pairing.offer_problem(state, offer.nonce) == ""


@pytest.mark.parametrize("via", ["mcp", "ditto", "ai", ""])
def test_only_the_persons_channels_pair(state, tmp_path, via):
    with pytest.raises(pairing.PairingRefused) as offered:
        _offer_via = pairing.create_offer   # noqa: F841 - the name an AI would call
        pairing.create_offer(state, public_line="ssh-ed25519 AAAA", signers="x", via=via)
    assert offered.value.kind == "channel"
    with pytest.raises(pairing.PairingRefused) as kit:
        export_probe_kit(state, tmp_path / "kit", via=via)
    assert kit.value.kind == "channel" and not (tmp_path / "kit").exists()
    offer = _offer(state)
    with pytest.raises(pairing.PairingRefused) as done:
        pairing.complete_pairing(state, _result(offer.nonce, FP), via=via, audit=Log())
    assert done.value.kind == "channel" and pairing.offer_problem(state, offer.nonce) == ""


AI_REACHABLE = ("orchestrator/tools", "orchestrator/mcp_server.py", "orchestrator/acp_agent", "orchestrator/llm",
                "orchestrator/Core/ditto_manager.py", "orchestrator/runtime/device/service.py")


def test_no_module_an_ai_can_reach_names_a_pairing_step():
    """The guard of tests/test_remote_trust.py keeps remote/pairing.py out of the AI's import graph; this one also
    keeps the kit's and the pairing's step names out of the AI's code by text (a lookup by name included)."""
    names = ("create_offer", "complete_pairing", "export_probe_kit", "probe_kit_root_step")
    hits = []
    for place in AI_REACHABLE:
        path = REPO / place
        files = path.rglob("*.py") if path.is_dir() else [path] if path.exists() else []
        for file in files:
            text = file.read_text(encoding="utf-8")
            hits += [f"{file.relative_to(REPO)}: {n}" for n in names if n in text]
    assert hits == []


def test_the_overview_an_ai_may_read_holds_no_nonce_and_no_fingerprint(state):
    offer = _offer(state)
    _offer(state)
    pairing.complete_pairing(state, _result(offer.nonce, FP), via="cli", audit=Log(), label="Board A")
    view = pairing_overview(state)
    assert view["open_offers"] == 1 and view["paired"][0]["label"] == "Board A"
    text = json.dumps(view)
    assert offer.nonce not in text and FP not in text and "SHA256:" not in text


# --- the TOFU decision: a pinned key needs no question ---------------------------------------------------------------

def test_first_use_trusts_a_pinned_key_without_asking(state):
    """Regression: before the pairing core, an unknown key with nobody to ask was always declined."""
    line = host_key_line()
    key, fp = parse_public_key(line), fingerprint_of(line)
    offer = _offer(state)
    pairing.complete_pairing(state, _result(offer.nonce, fp), via="cli", audit=Log())
    store = TrustStore(state)
    check = KeyCheck.of(Endpoint("192.0.2.10", 22), key, "unknown")
    log = Log()
    outcome = pairing.first_use(store, check, ask=None, via="cli", audit=log)
    assert outcome.verdict == "paired" and outcome.trusted
    assert store.status(Endpoint("192.0.2.10", 22), key) == "known"
    assert log.rows[0][1]["extra"]["answer"] == "pinned"


def test_first_use_still_asks_for_a_key_nobody_pinned(state):
    line, other = host_key_line(), host_key_line()
    offer = _offer(state)
    pairing.complete_pairing(state, _result(offer.nonce, fingerprint_of(other)), via="cli", audit=Log())
    check = KeyCheck.of(Endpoint("192.0.2.11", 22), parse_public_key(line), "unknown")
    assert pairing.first_use(TrustStore(state), check, ask=None, via="cli").verdict == "declined"
    # a --trust-fingerprint that differs is refused even for a pinned key
    pinned = KeyCheck.of(Endpoint("192.0.2.12", 22), parse_public_key(other), "unknown")
    assert pairing.first_use(TrustStore(state), pinned, ask=None, preset_fingerprint=FP,
                             via="cli").verdict == "mismatch"


def test_a_changed_key_is_refused_even_when_pinned(state):
    line = host_key_line()
    offer = _offer(state)
    pairing.complete_pairing(state, _result(offer.nonce, fingerprint_of(line)), via="cli", audit=Log())
    check = KeyCheck.of(Endpoint("192.0.2.13", 22), parse_public_key(line), "changed")
    assert pairing.first_use(TrustStore(state), check, ask=lambda c: True, via="cli").verdict == "changed"


def test_the_paired_tier_is_the_boards_until_a_verify_reads_it(state):
    from orchestrator.runtime.device.trusted import TrustedDevices
    offer = _offer(state)
    pairing.complete_pairing(state, _result(offer.nonce, FP), via="cli", audit=Log())
    assert TrustedDevices(state).key_tier(device_id_for(FP)) == "root"
    TrustedDevices(state).note_key_tier(device_id_for(FP), "dropbear")
    assert TrustedDevices(state).key_tier(device_id_for(FP)) == "dropbear"


# --- the probe kit -------------------------------------------------------------------------------------------------

def test_the_kit_carries_public_data_and_the_frameworks_own_root_step(state, kit):
    folder = kit.folder
    assert kit.pairing and not kit.problem
    assert {"hardware_probe.py", "hardware_probe.sh", "hardware_probe.ps1", "README.txt"} <= set(kit.files)
    pair_dir = folder / KIT_PAIR_DIR
    assert (pair_dir / "nonce").read_text(encoding="ascii").strip() == kit.offer.nonce
    public_line = ssh_trust.client_key(state)[1]
    assert (pair_dir / "framework_key.pub").read_text(encoding="utf-8").strip() == public_line
    step = (pair_dir / "root-board.sh").read_text(encoding="utf-8")
    # the very root step of the pairing over a password login, in its as-root form, with the root-owned key line
    assert gate.root_board_script(as_root=True).replace("@BLOB@", public_line.split()[1]) in step
    assert gate.root_key_line(public_line, gate.ROOT_GATE).split(" ssh-ed25519")[0] in step
    assert "\r" not in step   # a shell reads it: LF line ends on every system
    assert (pair_dir / "llmcf-gate").read_text(encoding="utf-8") == gate.render_gate()
    private = (state / ssh_trust.KEY_NAME).read_text(encoding="utf-8")
    secret_body = "".join(private.strip().splitlines()[1:-1])
    for name in kit.files:
        text = (folder / name).read_bytes().decode("utf-8", "replace")
        assert "PRIVATE KEY" not in text, name
        assert secret_body[:40] not in text.replace("\n", ""), name


def test_pairing_can_be_switched_off(state, tmp_path):
    off = export_probe_kit(state, tmp_path / "plain", pair=False, via="gui")
    assert not off.pairing and not (tmp_path / "plain" / KIT_PAIR_DIR).exists()
    assert "Pairing is OFF" in (tmp_path / "plain" / "README.txt").read_text(encoding="utf-8")
    assert pairing_overview(state)["open_offers"] == 0


def test_without_a_signing_key_the_kit_says_so_and_reads_hardware_only(state, tmp_path):
    from orchestrator.runtime.device import board_ops
    kit = export_probe_kit(state, tmp_path / "kit", signer=lambda: None, via="cli")
    assert not kit.pairing and "Pairing is not in this kit" in kit.problem
    assert board_ops.pairing_bundle(lambda: None, state).signers == ""
    assert not (tmp_path / "kit" / KIT_PAIR_DIR / "nonce").exists()


# --- the probe on the board (.py) --------------------------------------------------------------------------------

@POSIX
def test_the_python_probe_pairs_only_as_root(tmp_path, kit):
    line = host_key_line()
    root = sysroot_with_host_key(tmp_path, line)
    pair_dir = str(kit.folder / KIT_PAIR_DIR)
    calls = []
    block = probe.pair(pair_dir, root=str(root), account="radxa", euid=0, run=installed_run(calls), env={})
    assert block == {"PAIR_STATE": "installed", "PAIR_NONCE": kit.offer.nonce,
                     "PAIR_HOSTKEY_ED25519": fingerprint_of(line)}
    assert calls[0][0] == ["sh", os.path.join(pair_dir, "root-board.sh")]
    assert calls[0][1]["env"]["LLMCF_ACCOUNT"] == "radxa"
    skipped = probe.pair(pair_dir, root=str(root), account="radxa", euid=1000, run=installed_run([]), env={})
    assert skipped == {"PAIR_STATE": "skipped", "PAIR_REASON": "not-root"}
    assert "skipped" in probe.pairing_text(skipped) and "hardware data is complete" in probe.pairing_text(skipped)
    no_account = probe.pair(pair_dir, root=str(root), euid=0, run=installed_run([]), env={"SUDO_USER": "root"})
    assert no_account["PAIR_REASON"] == "account"


@POSIX
def test_a_failed_root_step_is_a_plain_reason_and_no_fingerprint(tmp_path, kit):
    root = sysroot_with_host_key(tmp_path, host_key_line())

    def run(argv, **kwargs):
        return Done("root-board=unavailable reason=no-sshd\n")
    block = probe.pair(str(kit.folder / KIT_PAIR_DIR), root=str(root), account="radxa", euid=0, run=run, env={})
    assert block == {"PAIR_STATE": "failed", "PAIR_REASON": "no-sshd"}


def test_the_host_key_comment_never_reaches_the_result():
    line = host_key_line(comment="someone@private-host.example.test")
    fp = probe.host_key_fingerprint(line, ("ssh-ed25519",))
    assert fp == fingerprint_of(line) and "example" not in fp
    assert probe.host_key_fingerprint(line, ("ssh-rsa",)) is None   # a key of another type than its file says


# --- the probe on the board (.sh) --------------------------------------------------------------------------------

def _sh_board(tmp_path, kit, uid, line):
    """A board for the shell probe: a sysroot with the host key and stubs for id (the user id) and ssh-keygen."""
    from test_hardware_probe import make_sysroot, tool_dir
    root = make_sysroot(tmp_path, "rk3566")
    (root / "etc" / "ssh").mkdir(parents=True, exist_ok=True)
    (root / "etc" / "ssh" / "ssh_host_ed25519_key.pub").write_bytes((line + "\n").encode("ascii"))
    bindir = tool_dir(tmp_path)
    for name in ("id", "ssh-keygen"):
        (bindir / name).unlink(missing_ok=True)
    if not (bindir / "sh").exists():   # the probe starts the kit's root step with sh
        (bindir / "sh").symlink_to(shutil.which("sh"))
    fp = fingerprint_of(line)
    stubs = {"id": f'case "$1" in -u) echo {uid} ;; -un) echo radxa ;; *) echo "uid={uid}" ;; esac',
             "ssh-keygen": f'echo "256 {fp} root@board.example.test (ED25519)"'}
    for name, body in stubs.items():
        (bindir / name).write_bytes(("#!/bin/sh\n" + body + "\n").encode("utf-8"))
        (bindir / name).chmod(0o755)
    # the kit's root step answers as the framework's does (the real step is tested with its own harness below)
    (kit.folder / KIT_PAIR_DIR / "root-board.sh").write_bytes(b"#!/bin/sh\ncat >/dev/null\n"
                                                              b"echo \"root-board=installed home=none\"\n")
    return root, bindir, fp


def _run_sh(kit, root, bindir, tmp_path, *extra):
    script = kit.folder / "hardware_probe.sh"
    env = {"PATH": str(bindir), "HW_PROBE_SYSROOT": str(root),
           "HOME": str(tmp_path), "SUDO_USER": "radxa"}
    done = subprocess.run([shutil.which("sh"), str(script), "--output", "-", "--no-toolchain", *extra],
                          capture_output=True, text=True, timeout=120, env=env, stdin=subprocess.DEVNULL, check=False)
    return done


@POSIX
def test_the_shell_probe_pairs_as_root_from_the_kit_next_to_it(tmp_path, kit):
    root, bindir, fp = _sh_board(tmp_path, kit, 0, host_key_line())
    done = _run_sh(kit, root, bindir, tmp_path)
    profile = parse_profile(done.stdout)
    assert profile.get("PAIR_STATE") == "installed" and profile.get("PAIR_NONCE") == kit.offer.nonce
    assert profile.get("PAIR_HOSTKEY_ED25519") == fp
    assert "Paired" in done.stderr
    assert "board.example.test" not in done.stdout


@POSIX
def test_the_shell_probe_without_sudo_says_pairing_was_skipped(tmp_path, kit):
    root, bindir, _fp = _sh_board(tmp_path, kit, 1000, host_key_line())
    done = _run_sh(kit, root, bindir, tmp_path)
    profile = parse_profile(done.stdout)
    assert profile.get("PAIR_STATE") == "skipped" and profile.get("PAIR_REASON") == "not-root"
    assert profile.get("PAIR_NONCE") is None and profile.get("RAM_TOTAL_MB")   # the hardware data is complete
    assert "Pairing was skipped" in done.stderr
    off = _run_sh(kit, root, bindir, tmp_path, "--no-pair")
    assert "PAIR_STATE" not in off.stdout


# --- the as-root root step (the framework's own, with the board-signing harness) ------------------------------

@POSIX
@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="ssh-keygen checks the self-test signature")
def test_the_kits_root_step_installs_what_the_password_pairing_installs(tmp_path):
    from test_board_signing import PUBLIC, _root_board
    from orchestrator.runtime.device import board_ops
    run, paths, home, config, _log = _root_board(tmp_path, account="root")   # `id -u` answers 0: run as root
    signer = _signer()
    package = board_ops.bundle(signer)
    text = gate.probe_kit_root_step(PUBLIC, package, paths=paths)
    script = tmp_path / "root-board.sh"
    script.write_bytes(text.encode("utf-8"))
    out = run(f"LLMCF_ACCOUNT=radxa sh {script}", stdin=[package.gate.encode("utf-8")])
    assert "root-board=installed home=none" in out.out, out.out + out.err
    assert (Path(paths.keys_dir) / "radxa").read_text().strip() == gate.root_key_line(PUBLIC, paths.gate)
    assert Path(paths.signers).read_text().strip() == signer.allowed_signers
    assert config.read_text().count("Match User radxa") == 1
    assert PUBLIC in (home / ".ssh" / "authorized_keys").read_text()   # the account's own file is left alone
    refused = run(f"LLMCF_ACCOUNT=root sh {script}", stdin=[package.gate.encode("utf-8")])
    assert "root-board=unavailable reason=account" in refused.out


def test_the_kits_root_step_refuses_without_a_signing_key():
    from orchestrator.runtime.device import board_ops
    with pytest.raises(ValueError):
        gate.probe_kit_root_step("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample0 x", board_ops.bundle(None))


# --- the import ----------------------------------------------------------------------------------------------------

def test_the_import_completes_the_pairing_once_and_keeps_the_hardware_data(tmp_path, state, kit):
    """Regression: before the pairing core, the import dropped the pairing block and the board stayed unknown."""
    line = host_key_line()
    fp = fingerprint_of(line)
    block = {"PAIR_STATE": "installed", "PAIR_NONCE": kit.offer.nonce, "PAIR_HOSTKEY_ED25519": fp}
    path = result_file(tmp_path, block)
    probes = HardwareProbes(state)
    log = Log()
    imported = import_probe(probes, path, label="Board A", via="cli", audit=log)
    assert imported.pairing.state == "paired" and imported.probe_id == device_id_for(fp)
    assert not [k for k in imported.profile.values if k.startswith("PAIR_")]   # never stored
    assert TrustStore(state).pinned(fp)["probe_id"] == imported.probe_id
    stored = probes.store.device_path(imported.probe_id).read_text(encoding="utf-8")
    assert kit.offer.nonce not in stored and fp not in stored
    again = import_probe(probes, path, label="Board A", via="cli", audit=Log())
    assert again.pairing.state == "refused" and again.pairing.kind == "used"
    assert again.probe_id != imported.probe_id and again.profile.get("SOC_MODEL") == imported.profile.get("SOC_MODEL")


def test_a_skipped_pairing_imports_the_hardware_with_a_plain_reason(tmp_path, state, kit):
    path = result_file(tmp_path, {"PAIR_STATE": "skipped", "PAIR_REASON": "not-root"})
    imported = import_probe(HardwareProbes(state), path, via="gui", audit=Log())
    assert imported.pairing.state == "refused" and imported.pairing.kind == "skipped"
    assert "did not run with sudo" in imported.pairing.message
    assert pairing.offer_problem(state, kit.offer.nonce) == ""   # the offer stays open for a run with sudo


def test_a_result_of_another_framework_is_refused(tmp_path, state, kit):
    block = {"PAIR_STATE": "installed", "PAIR_NONCE": "f" * 32, "PAIR_HOSTKEY_ED25519": FP}
    imported = import_probe(HardwareProbes(state), result_file(tmp_path, block), via="cli", audit=Log())
    assert imported.pairing.kind == "unknown" and TrustStore(state).pinned(FP) is None
    assert re.fullmatch(r"[0-9a-f]{16}", imported.probe_id)


@pytest.mark.parametrize("name", sorted(p.name for p in FIXTURES.glob("schema2_*.txt")))
def test_older_results_stay_readable_without_a_pairing_block(tmp_path, state, name):
    imported = import_probe(HardwareProbes(state), FIXTURES / name, via="cli", audit=Log())
    assert imported.pairing.state == "none"


def test_the_result_file_holds_no_secret(tmp_path, state, kit):
    line = host_key_line()
    root = sysroot_with_host_key(tmp_path, line)
    if os.name == "nt":
        block = {"PAIR_STATE": "installed", "PAIR_NONCE": kit.offer.nonce, "PAIR_HOSTKEY_ED25519": fingerprint_of(line)}
    else:
        block = probe.pair(str(kit.folder / KIT_PAIR_DIR), root=str(root), account="radxa", euid=0,
                           run=installed_run([]), env={})
    text = probe.render_text(probe.with_pairing({"PROBE_SCHEMA": "2", "PROBE_WARNINGS": "x"}, block))
    assert set(k for k in block) <= {"PAIR_STATE", "PAIR_NONCE", "PAIR_HOSTKEY_ED25519"}
    private = (state / ssh_trust.KEY_NAME).read_text(encoding="utf-8")
    assert "PRIVATE" not in text and "".join(private.splitlines()[1:-1])[:40] not in text
    assert text.index("PAIR_STATE") < text.index("PROBE_WARNINGS")


# --- the CLI ---------------------------------------------------------------------------------------------------------

def test_cli_kit_without_pairing_and_the_import_that_pairs(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from orchestrator import cli as cli_module
    home = tmp_path / "state"
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(home))
    plain = CliRunner().invoke(cli_module.cli, ["-q", "scan", "--kit", str(tmp_path / "kit"), "--no-pair"])
    assert plain.exit_code == 0, plain.output
    assert "Pairing is off" in plain.output and not (tmp_path / "kit" / KIT_PAIR_DIR).exists()
    assert CliRunner().invoke(cli_module.cli, ["-q", "scan", "--no-pair"]).exit_code == 2
    assert CliRunner().invoke(cli_module.cli, ["-q", "scan", "--kit", str(tmp_path / "k2"), "--import",
                                               str(FIXTURES / "schema2_rk3566_dietpi.txt")]).exit_code == 2
    # a kit made through the person's channel (the CLI's own step takes the framework's signing key)
    kit = export_probe_kit(home, tmp_path / "kit2", signer=_signer(), via="cli", audit=Log())
    fp = fingerprint_of(host_key_line())
    path = result_file(tmp_path, {"PAIR_STATE": "installed", "PAIR_NONCE": kit.offer.nonce,
                                  "PAIR_HOSTKEY_ED25519": fp})
    done = CliRunner().invoke(cli_module.cli, ["-q", "scan", "--import", str(path), "--label", "Board A"])
    assert done.exit_code == 0, done.output
    out = " ".join(done.output.split())
    assert f"Imported as probe {device_id_for(fp)}" in out and "Paired: the board's host key is pinned" in out
    again = CliRunner().invoke(cli_module.cli, ["-q", "scan", "--import", str(path)])
    assert again.exit_code == 0 and "already completed once" in " ".join(again.output.split())
