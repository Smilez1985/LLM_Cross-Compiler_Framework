"""Pairing by code (orchestrator/remote/pair_pake.py, the framework's side; rust/llmcf-pair, the board's side).

- The host flow against a fake board written here (SPAKE2's side A with libsodium through PyNaCl, the protocol of
  rust/llmcf-pair/src/proto.rs): the right code pairs and the content reaches the board, a wrong code fails without
  anything of the board being read, a board off the local links is refused before any connection, a forged
  confirmation is a wrong code, a stranger's greeting is refused.
- The mDNS answers: only local senders count, an IPv6 link-local board keeps its zone, the code is 8 digits.
- The local links of this computer (utils/locality.local_links): cable, hotspot, lan; public and loopback addresses
  are no link.
- pair_by_code() ends in the one complete_pairing() (remote/pairing.py, the probe kit's too): an offer of the way pake
  first, then the fingerprints recomputed from the key lines the board proved are pinned with the audit row; no
  trust-on-first-use question (tests/test_pair_integration.py holds the seams of both ways).
- End to end with the Rust program (its test build, loopback-as-link mode), when CI built it (LLMCF_PAIR_TEST_BIN or
  rust/target/debug/llmcf-pair of the test build); skipped otherwise, with the reason.

Documentation addresses only (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24, 2001:db8::/32) besides the private and
link-local ranges the rule is about.
"""

import hashlib
import json
import os
import socket
import struct
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT
from orchestrator.remote import pair_pake
from orchestrator.remote.pair_pake import FoundBoard, PakeError

nacl = pytest.importorskip("nacl.bindings")

# the framework's public key line as pairing_content gives it (a well-formed line: the offer checks it) and a signer
CONTENT = {"format": pair_pake.CONTENT_FORMAT,
           "public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBk1yLqJZPTB7vXoG2C2Y2f0Q0hX8sYxS7o3nqgKJpWm llmcf",
           "signers": "llmcf namespaces=\"llmcf\" ssh-ed25519 AAAA"}


def _sent(got):
    """What the board received, without the offer's nonce (a new one per pairing)."""
    content = dict(got)
    assert len(content.pop("nonce", "")) == 32, "the pairing offer's nonce travels with the content"
    return content


# --- a fake board: SPAKE2 side A and the board's half of protocol version 1 -------------------------------------------

def _side_a(code: str):
    pw = pair_pake.password_scalar(code)
    x = nacl.crypto_core_ed25519_scalar_reduce(os.urandom(64))
    X = nacl.crypto_core_ed25519_add(nacl.crypto_scalarmult_ed25519_base_noclamp(x),
                                     nacl.crypto_scalarmult_ed25519_noclamp(pw, pair_pake._M))

    def finish(msg_b: bytes) -> bytes:
        Y = msg_b[1:]
        K = nacl.crypto_scalarmult_ed25519_noclamp(
            x, nacl.crypto_core_ed25519_sub(Y, nacl.crypto_scalarmult_ed25519_noclamp(pw, pair_pake._N)))
        t = b"".join([hashlib.sha256(code.encode()).digest(), hashlib.sha256(pair_pake.ID_A).digest(),
                      hashlib.sha256(pair_pake.ID_B).digest(), X, Y, K])
        return hashlib.sha256(t).digest()
    return b"A" + X, finish


def _seal(key: bytes, counter: int, plain: bytes, aad: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    return ChaCha20Poly1305(key).encrypt(b"\0\0\0\0" + struct.pack(">Q", counter), plain, aad)


def _open(key: bytes, counter: int, sealed: bytes, aad: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    return ChaCha20Poly1305(key).decrypt(b"\0\0\0\0" + struct.pack(">Q", counter), sealed, aad)


def fake_board(code: str, facts: dict, *, forge: bool = False, hello: bytes = b""):
    """A connect() stand-in: a socket pair whose far end runs the board's half in a thread. Returns (connect, got),
    `got` holding what the board received ("content") or its verdict ("verdict")."""
    got = {}

    def connect(board, timeout):
        near, far = socket.socketpair()
        near.settimeout(10)
        far.settimeout(10)

        def run():
            try:
                if hello:
                    pair_pake.write_frame(far, hello)
                    return
                msg_a, finish = _side_a(code)
                pair_pake.write_frame(far, pair_pake.MAGIC + msg_a)
                proof = pair_pake.read_frame(far)
                msg_b, tag = proof[:33], proof[33:]
                k = finish(msg_b)
                h = pair_pake._hkdf
                t = hashlib.sha256(pair_pake.MAGIC + msg_a + msg_b).digest()
                import hmac as _hmac
                want = _hmac.new(h(k, pair_pake.SALT, b"confirm host", 32), b"host" + t, hashlib.sha256).digest()
                if not _hmac.compare_digest(want, tag):
                    got["verdict"] = "wrong"
                    pair_pake.write_frame(far, pair_pake.WRONG)
                    return
                confirm = _hmac.new(h(k, pair_pake.SALT, b"confirm board", 32), b"board" + t,
                                    hashlib.sha256).digest()
                if forge:
                    confirm = bytes(32)
                b2h = h(k, pair_pake.SALT, b"key board to host", 32)
                h2b = h(k, pair_pake.SALT, b"key host to board", 32)
                pair_pake.write_frame(far, confirm + _seal(b2h, 0, json.dumps(facts).encode(), t))
                got["content"] = json.loads(_open(h2b, 0, pair_pake.read_frame(far), t))
                pair_pake.write_frame(far, _seal(b2h, 1, b'{"received":true}', t))
                got["verdict"] = "paired"
            except Exception as e:  # noqa: BLE001 - the test reads the verdict
                got.setdefault("verdict", f"error {e!r}")
            finally:
                far.close()
        threading.Thread(target=run, daemon=True).start()
        return near
    return connect, got


BOARD = FoundBoard(instance="llmcf-ab12." + pair_pake.SERVICE, address="169.254.20.30", port=40123, link="cable")


def test_the_right_code_pairs_and_the_content_reaches_the_board():
    facts = {"version": 1, "identity": {"probe_id": "p-1"},
             "host_keys": [{"type": "ssh-ed25519", "key": "ssh-ed25519 AAAAx", "fingerprint": "SHA256:abc"}]}
    connect, got = fake_board("12345678", facts)
    result = pair_pake.exchange(BOARD, "1234 5678", CONTENT, connect=connect)
    assert result.identity == {"probe_id": "p-1"}
    assert result.fingerprints == ("SHA256:abc",)
    assert got["content"] == CONTENT


def test_a_wrong_code_fails_and_the_board_counts_it():
    connect, got = fake_board("12345678", {"identity": {}})
    with pytest.raises(PakeError) as e:
        pair_pake.exchange(BOARD, "87654321", CONTENT, connect=connect)
    assert e.value.kind == "wrong_code"
    assert "content" not in got


def test_a_forged_board_confirmation_is_a_wrong_code_and_no_content_leaves():
    connect, got = fake_board("12345678", {"identity": {}}, forge=True)
    with pytest.raises(PakeError) as e:
        pair_pake.exchange(BOARD, "12345678", CONTENT, connect=connect)
    assert e.value.kind == "wrong_code"
    assert "content" not in got


def test_a_stranger_s_greeting_is_refused():
    connect, _ = fake_board("12345678", {}, hello=b"SSH-2.0-OpenSSH_9.6\r\n")
    with pytest.raises(PakeError) as e:
        pair_pake.exchange(BOARD, "12345678", CONTENT, connect=connect)
    assert e.value.kind == "protocol"


@pytest.mark.parametrize("address", ["192.0.2.10", "203.0.113.5", "2001:db8::7", "100.64.3.4", "127.0.0.1"])
def test_a_board_off_the_local_links_is_refused_before_any_connection(address):
    def connect(board, timeout):
        pytest.fail("connected to a board off the local links")
    with pytest.raises(PakeError) as e:
        pair_pake.exchange(FoundBoard("x", address, 4000), "12345678", CONTENT, connect=connect)
    assert e.value.kind == "not_local"


def test_a_board_that_sends_slowly_cannot_hold_the_pairing_past_its_time():
    """The session's time is for the whole exchange, not for each read: a board (or a stranger posing as one) that
    sends one byte at a time stays inside every per-read timeout and would otherwise hold the person's pairing (the
    CLI or the GUI's worker) for as long as it likes."""
    import time

    def connect(board, timeout):
        near, far = socket.socketpair()
        near.settimeout(timeout)

        def trickle():
            try:
                for byte in struct.pack(">I", 45) + pair_pake.MAGIC + bytes(33):
                    far.sendall(bytes([byte]))
                    time.sleep(0.05)
                far.recv(1024)
            except OSError:
                pass
            finally:
                far.close()
        threading.Thread(target=trickle, daemon=True).start()
        return near

    started = time.monotonic()
    with pytest.raises(PakeError) as e:
        pair_pake.exchange(BOARD, "12345678", CONTENT, timeout=0.5, connect=connect)
    assert e.value.kind == "network"
    assert time.monotonic() - started < 1.5


@pytest.mark.parametrize("text,ok", [("12345678", True), ("1234 5678", True), ("1234-5678", True),
                                     ("1234567", False), ("123456789", False), ("1234abcd", False), ("", False),
                                     ("１２３４５６７８", False)])
def test_the_code_is_eight_digits(text, ok):
    if ok:
        assert pair_pake.normal_code(text) == "12345678"
    else:
        with pytest.raises(PakeError):
            pair_pake.normal_code(text)


# --- mDNS answers -------------------------------------------------------------------------------------------------------

def _name(text):
    return pair_pake._name(text)


def answer(instance="llmcf-ab12", port=40123, address=None):
    full = f"{instance}.{pair_pake.SERVICE}"
    host = "llmcf-pair-ab12.local"
    records = []

    def rr(name, rtype, rdata):
        return _name(name) + struct.pack(">HHIH", rtype, 1, 10, len(rdata)) + rdata
    records.append(rr(pair_pake.SERVICE, 12, _name(full)))
    records.append(rr(full, 33, struct.pack(">HHH", 0, 0, port) + _name(host)))
    if address and ":" in address:
        records.append(rr(host, 28, socket.inet_pton(socket.AF_INET6, address)))
    elif address:
        records.append(rr(host, 1, socket.inet_aton(address)))
    return struct.pack(">HHHHHH", 7, 0x8400, 0, len(records), 0, 0) + b"".join(records)


def test_an_answer_from_a_local_link_names_the_board():
    boards = pair_pake.boards_in(answer(address="169.254.9.9"), ("169.254.9.9", 5353), link="cable")
    assert boards == [FoundBoard(f"llmcf-ab12.{pair_pake.SERVICE}", "169.254.9.9", 40123, 0, "cable")]
    assert boards[0].label == "llmcf-ab12 (cable)"


def test_an_ipv6_link_local_board_keeps_the_zone():
    boards = pair_pake.boards_in(answer(address="fe80::2"), ("fe80::2", 5353, 0, 3))
    assert boards[0].address == "fe80::2" and boards[0].scope_id == 3


def test_answers_from_off_the_local_links_are_dropped():
    assert pair_pake.boards_in(answer(), ("203.0.113.9", 5353)) == []
    assert pair_pake.boards_in(answer(), ("2001:db8::9", 5353, 0, 0)) == []
    # a local sender cannot point the framework at a public address
    boards = pair_pake.boards_in(answer(address="198.51.100.4"), ("192.168.137.20", 5353))
    assert boards[0].address == "192.168.137.20"


def test_a_question_and_garbage_are_no_answer():
    assert pair_pake.parse_answer(pair_pake.query_packet(1))["instances"] == []
    assert pair_pake.parse_answer(b"\x00\x01")["instances"] == []
    looped = struct.pack(">HHHHHH", 1, 0x8400, 0, 1, 0, 0) + b"\xc0\x0c"
    assert pair_pake.parse_answer(looped)["instances"] == []


# --- this computer's local links ------------------------------------------------------------------------------------------

def test_local_links_are_cable_hotspot_and_lan():
    from orchestrator.utils import locality
    e = lambda fam, addr: SimpleNamespace(family=fam, address=addr)  # noqa: E731
    table = {"eth0": [e(socket.AF_INET, "169.254.4.5"), e(socket.AF_INET6, "fe80::1%eth0")],
             "wlan0": [e(socket.AF_INET, "10.42.0.1")], "hotspot": [e(socket.AF_INET, "192.168.137.1")],
             "lan": [e(socket.AF_INET, "192.168.1.20"), e(socket.AF_INET6, "2001:db8::20")],
             "wan": [e(socket.AF_INET, "203.0.113.20")], "lo": [e(socket.AF_INET, "127.0.0.1")],
             "tail": [e(socket.AF_INET, "100.101.102.103")]}
    links = locality.local_links(lambda: table)
    got = {(link.interface, link.address, link.kind) for link in links}
    assert got == {("eth0", "169.254.4.5", "cable"), ("eth0", "fe80::1", "cable"), ("wlan0", "10.42.0.1", "hotspot"),
                   ("hotspot", "192.168.137.1", "hotspot"), ("lan", "192.168.1.20", "lan")}
    assert locality.link_kind("fd00::5") == "lan"
    assert locality.link_kind("192.0.2.1") is None


def test_browse_asks_nothing_without_a_local_link():
    assert pair_pake.browse(0.1, links=[]) == []


# --- pair_by_code ends in complete_pairing --------------------------------------------------------------------------------

class Audit:
    def __init__(self):
        self.rows = []

    def record(self, actor, tool, *args, **kw):
        self.rows.append((actor, tool, kw.get("arguments")))


def _host_key_line():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    raw = Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.OpenSSH,
                                                                 serialization.PublicFormat.OpenSSH)
    return raw.decode()


def test_pair_by_code_pins_the_proven_host_key(tmp_path):
    """Behaviour changed on purpose (one complete_pairing for both ways): the proven key is pinned through the full
    pairing core (an offer of the way pake, its nonce sent with the content, the fingerprint recomputed from the key
    line), first_use then trusts it without the question; the board's own fingerprint strings count for nothing."""
    pytest.importorskip("paramiko")
    from orchestrator.remote import pairing, trust
    from orchestrator.remote.trust import TrustStore, fingerprint, parse_public_key
    line = _host_key_line()
    fp = fingerprint(parse_public_key(line))
    other = _host_key_line()
    facts = {"identity": {"probe_id": "p-2"},
             "host_keys": [{"type": "ssh-ed25519", "key": line, "fingerprint": "SHA256:the-board-says-anything"},
                           {"type": "ssh-ed25519", "key": other, "fingerprint": "SHA256:not-this-one"}]}
    connect, got = fake_board("24682468", facts)
    audit = Audit()
    board = FoundBoard("llmcf-cd34." + pair_pake.SERVICE, "192.168.137.20", 40000, link="hotspot")
    paired = pair_pake.pair_by_code(board, "24682468", CONTENT, state_dir=tmp_path, via="cli", audit=audit,
                                    connect=connect)
    assert paired.done.way == "pake" and paired.done.fingerprints == {"ed25519": fp}
    assert paired.done.address == "192.168.137.20"
    assert TrustStore(tmp_path).pinned(fp, "ssh-ed25519")
    assert not TrustStore(tmp_path).pinned(fingerprint(parse_public_key(other)), "ssh-ed25519")
    assert [r[1] for r in audit.rows] == ["board_pairing_offered", "board_paired"]
    assert _sent(got["content"]) == CONTENT
    assert pairing.offer_problem(tmp_path, got["content"]["nonce"], way="pake") == "used"
    assert [b["device_id"] for b in trust.paired_boards(tmp_path)] == [paired.done.device_id]


def test_the_old_method_vocabulary_delegates_to_the_pairing_ways():
    """One vocabulary for the two ways: 'file' (the probe kit's result file) and 'pake' (the short code); the old
    name PAIRING_METHODS (which said 'kit') delegates to remote/trust.py PAIRING_WAYS."""
    from orchestrator.remote import pairing, trust
    assert pairing.PAIRING_METHODS is trust.PAIRING_WAYS == ("file", "pake")


def test_the_pairing_content_carries_no_private_key(tmp_path):
    from orchestrator.runtime.device import board_ops
    content = pair_pake.pairing_content(tmp_path, board_ops.pairing_bundle(lambda: None, tmp_path))
    text = json.dumps(content)
    assert "PRIVATE KEY" not in text
    assert content["public_key"].startswith("ssh-ed25519 ")
    assert content["key_line"].endswith(content["public_key"])
    assert content["missing"] == "no-signer" and content["gate"]


# --- end to end with the Rust program ----------------------------------------------------------------------------------

def _pair_binary():
    """The test build of rust/llmcf-pair (cargo build -p llmcf-pair --features test-mode), or a skip reason."""
    if os.name == "nt":
        return None, "llmcf-pair is a Linux program for the boards"
    path = os.environ.get("LLMCF_PAIR_TEST_BIN") or str(REPO_ROOT / "rust" / "target" / "debug" / "llmcf-pair")
    if not Path(path).is_file():
        return None, "the test build of rust/llmcf-pair is not built here (CI's Rust job builds it)"
    probe = subprocess.run([path, "announce", "--out", os.devnull + "/x", "--test-loopback", "0", "--window", "1"],
                           capture_output=True, text=True, timeout=30)
    if "no test mode" in probe.stderr:
        return None, "rust/target/debug/llmcf-pair is not the test build (--features test-mode)"
    return path, ""


def _start(binary, tmp_path, **extra):
    keys = tmp_path / "keys"
    keys.mkdir()
    line = _host_key_line()
    (keys / "ssh_host_ed25519_key.pub").write_bytes((line + " root@board.example.com\n").encode())
    identity = tmp_path / "identity.json"
    identity.write_bytes(json.dumps({"probe_id": "p-e2e", "ssh_port": 22}).encode())
    out = tmp_path / "content.json"
    proc = subprocess.Popen([binary, "announce", "--out", str(out), "--identity", str(identity), "--host-keys",
                             str(keys), "--window", "60", "--test-loopback", "0"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    tcp = mdns = 0
    code = ""
    while not (tcp and code):
        text = proc.stderr.readline()
        assert text, "llmcf-pair ended early"
        if "tcp=" in text:
            tcp = int(text.split("tcp=")[1].split()[0])
            mdns = int(text.split("mdns=")[1].split()[0])
        if "pairing code " in text:
            code = text.split("pairing code ")[1][:9].replace(" ", "")
    return proc, tcp, mdns, code, line, out


def test_end_to_end_with_the_board_program(tmp_path):
    pytest.importorskip("paramiko")
    binary, why = _pair_binary()
    if binary is None:
        pytest.skip(why)
    proc, tcp, mdns, code, line, out = _start(binary, tmp_path)
    try:
        boards = pair_pake.browse(2.0, targets=[("127.0.0.1", mdns)], allow_loopback=True)
        assert len(boards) == 1 and boards[0].port == tcp and boards[0].address == "127.0.0.1"
        wrong = "00000000" if code != "00000000" else "11111111"
        with pytest.raises(PakeError) as e:
            pair_pake.exchange(boards[0], wrong, CONTENT, allow_loopback=True)
        assert e.value.kind == "wrong_code"
        state = tmp_path / "state"
        paired = pair_pake.pair_by_code(boards[0], code, CONTENT, state_dir=state, audit=Audit(),
                                        allow_loopback=True)
        assert paired.result.identity["probe_id"] == "p-e2e"
        assert paired.result.key_lines == (" ".join(line.split()[:2]),)
        assert "example.com" not in json.dumps(paired.result.identity)
        assert len(paired.done.fingerprints) == 1 and paired.done.way == "pake"
        assert proc.wait(timeout=30) == 0
        assert _sent(json.loads(out.read_text())) == CONTENT
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_end_to_end_three_wrong_codes_end_the_board_window(tmp_path):
    binary, why = _pair_binary()
    if binary is None:
        pytest.skip(why)
    proc, tcp, mdns, code, _, out = _start(binary, tmp_path)
    try:
        board = FoundBoard("t", "127.0.0.1", tcp)
        wrongs = [c for c in ("00000000", "11111111", "22222222", "33333333") if c != code][:3]
        for wrong in wrongs:
            with pytest.raises(PakeError):
                pair_pake.exchange(board, wrong, CONTENT, allow_loopback=True)
        assert proc.wait(timeout=30) == 4
        assert not out.exists()
        with pytest.raises(PakeError) as e:
            pair_pake.exchange(board, code, CONTENT, allow_loopback=True, timeout=3)
        assert e.value.kind == "network"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


# --- llmcf deploy devices pair-code -------------------------------------------------------------------------------------

def _cli(tmp_path, monkeypatch, *args, input=None):
    from click.testing import CliRunner

    from orchestrator import cli as cli_module
    monkeypatch.setenv("LLM_FRAMEWORK_HOME", str(tmp_path / "state"))
    return CliRunner().invoke(cli_module.cli, ["-q", *args], input=input)


def test_cli_pair_code_needs_a_terminal(tmp_path, monkeypatch):
    result = _cli(tmp_path, monkeypatch, "deploy", "devices", "pair-code")
    assert result.exit_code == 1 and "needs a terminal" in result.output


def test_cli_pair_code_asks_the_person_and_pairs(tmp_path, monkeypatch):
    from orchestrator import cli as cli_module
    from orchestrator.runtime.device import person_steps
    from orchestrator.utils import locality
    monkeypatch.setattr(cli_module, "_interactive", lambda: True)
    monkeypatch.setattr(locality, "local_links", lambda: [locality.LocalLink("eth0", "169.254.1.2", "cable")])
    monkeypatch.setattr(pair_pake, "browse", lambda wait, links: [BOARD])
    seen = []

    def pair(state, board, code, **kw):
        seen.append((board, code, kw["via"]))
        return SimpleNamespace(result=pair_pake.PakeResult(board, {}, (pair_pake.HostKeyFact("ssh-ed25519", "k",
                                                                                              "SHA256:a"),)),
                               done=SimpleNamespace(fingerprints={"ed25519": "SHA256:a"}, device_id="fedcba9876543210",
                                                    address="169.254.20.30"))
    monkeypatch.setattr(person_steps, "pair_by_code", pair)
    result = _cli(tmp_path, monkeypatch, "deploy", "devices", "pair-code", "--wait", "0.5", input="1234 5678\n")
    assert result.exit_code == 0, result.output
    assert seen == [(BOARD, "1234 5678", "cli")]
    assert "Paired with llmcf-ab12 (cable)" in result.output and "fedcba9876543210" in result.output
    assert "169.254.20.30" in result.output
    assert "1234" not in result.output, "the code is not echoed"


def test_cli_pair_code_without_a_board_or_with_a_wrong_code_fails_plainly(tmp_path, monkeypatch):
    from orchestrator import cli as cli_module
    from orchestrator.runtime.device import person_steps
    from orchestrator.utils import locality
    monkeypatch.setattr(cli_module, "_interactive", lambda: True)
    monkeypatch.setattr(locality, "local_links", lambda: [])
    result = _cli(tmp_path, monkeypatch, "deploy", "devices", "pair-code")
    assert result.exit_code == 1 and "no local link" in result.output
    monkeypatch.setattr(locality, "local_links", lambda: [locality.LocalLink("eth0", "10.42.0.1", "hotspot")])
    monkeypatch.setattr(pair_pake, "browse", lambda wait, links: [])
    result = _cli(tmp_path, monkeypatch, "deploy", "devices", "pair-code")
    assert result.exit_code == 1 and "No board shows a pairing code" in result.output
    monkeypatch.setattr(pair_pake, "browse", lambda wait, links: [BOARD])

    def wrong(*a, **k):
        raise PakeError("The code was wrong: the board did not accept it.", "wrong_code")
    monkeypatch.setattr(person_steps, "pair_by_code", wrong)
    result = _cli(tmp_path, monkeypatch, "deploy", "devices", "pair-code", input="00000000\n")
    assert result.exit_code == 1 and "The code was wrong" in result.output


# --- the probe starts llmcf-pair (scripts/hardware_probe.sh --pair-code) ------------------------------------------------

@pytest.mark.skipif(os.name == "nt", reason="the shell probe runs on the boards (POSIX sh)")
def test_the_shell_probe_starts_llmcf_pair_and_installs_nothing_that_is_not_the_frameworks(tmp_path):
    """Behaviour changed on purpose: the probe has its own install now (pair_install_content, the kit's pairing step);
    content that is not the framework's (no nonce, no root step) installs nothing and reports skipped/no-kit."""
    import shutil
    sh = shutil.which("sh")
    if sh is None:
        pytest.skip("no POSIX sh")
    kit = tmp_path / "kit"
    kit.mkdir()
    probe = kit / "hardware_probe.sh"
    probe.write_bytes((REPO_ROOT / "scripts" / "hardware_probe.sh").read_bytes())
    calls, seen_id = tmp_path / "calls.txt", tmp_path / "identity.json"
    # a stand-in llmcf-pair next to the probe: it keeps its arguments and the identity, and "pairs"
    fake = kit / "llmcf-pair"
    fake.write_bytes(("#!/bin/sh\n"
                      f'printf "%s\\n" "$@" > "{calls}"\n'
                      'while [ $# -gt 0 ]; do case "$1" in --out) out=$2; shift 2;; --identity) id=$2; shift 2;; '
                      '*) shift;; esac; done\n'
                      f'cp "$id" "{seen_id}"\n'
                      "printf '{\"format\":\"x\"}' > \"$out\"\nexit 0\n").encode())
    fake.chmod(0o755)
    # the probe asks id -u: a stand-in says root, as under the probe's sudo
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake_id = bindir / "id"
    real_id = shutil.which("id") or "/usr/bin/id"
    fake_id.write_bytes(f'#!/bin/sh\n[ "$1" = "-u" ] && {{ echo 0; exit 0; }}\nexec "{real_id}" "$@"\n'.encode())
    fake_id.chmod(0o755)
    # a program on PATH with the install's name is not the probe's install: only a shell function counts (the probe
    # runs as root, and the content must not go to whatever program happens to carry that name)
    stray, stray_ran = bindir / "pair_install_content", tmp_path / "stray-ran"
    stray.write_bytes(f'#!/bin/sh\ntouch "{stray_ran}"\n'.encode())
    stray.chmod(0o755)
    env = dict(os.environ, PATH=f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}", SUDO_USER="dietpi")
    proc = subprocess.run([sh, str(probe), "--no-toolchain", "--pair-code", "--output", str(tmp_path / "p.txt")],
                          capture_output=True, text=True, timeout=300, env=env)
    assert (tmp_path / "p.txt").is_file(), proc.stderr
    args = calls.read_text().split()
    assert args[0] == "announce" and "--out" in args and "--identity" in args
    identity = json.loads(seen_id.read_text())
    assert identity == {"probe_version": identity["probe_version"], "account": "dietpi", "ssh_port": 22}
    assert "pairing files of the probe kit are missing or damaged" in proc.stderr, proc.stderr
    result = (tmp_path / "p.txt").read_text()
    assert "PAIR_STATE=skipped" in result and "PAIR_REASON=no-kit" in result
    assert not stray_ran.exists(), "a program named pair_install_content on PATH ran as the probe's install"


@pytest.mark.skipif(os.name == "nt", reason="the shell probe runs on the boards (POSIX sh)")
def test_the_shell_probe_hands_the_content_to_its_install_function(tmp_path):
    """The seam the probe kit's install fills: a shell function pair_install_content in the probe gets the file."""
    import shutil
    sh = shutil.which("sh")
    if sh is None:
        pytest.skip("no POSIX sh")
    kit = tmp_path / "kit"
    kit.mkdir()
    got = tmp_path / "installed.json"
    text = (REPO_ROOT / "scripts" / "hardware_probe.sh").read_text(encoding="utf-8")
    assert "\npair_by_code() {" in text
    text = text.replace("\npair_by_code() {", f'\npair_install_content() {{ cp "$1" "{got}"; }}\npair_by_code() {{', 1)
    probe = kit / "hardware_probe.sh"
    probe.write_bytes(text.encode())
    fake = kit / "llmcf-pair"
    fake.write_bytes(("#!/bin/sh\n"
                      'while [ $# -gt 0 ]; do case "$1" in --out) out=$2; shift 2;; *) shift;; esac; done\n'
                      "printf '{\"format\":\"x\"}' > \"$out\"\nexit 0\n").encode())
    fake.chmod(0o755)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake_id = bindir / "id"
    real_id = shutil.which("id") or "/usr/bin/id"
    fake_id.write_bytes(f'#!/bin/sh\n[ "$1" = "-u" ] && {{ echo 0; exit 0; }}\nexec "{real_id}" "$@"\n'.encode())
    fake_id.chmod(0o755)
    env = dict(os.environ, PATH=f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    proc = subprocess.run([sh, str(probe), "--no-toolchain", "--pair-code", "--output", str(tmp_path / "p.txt")],
                          capture_output=True, text=True, timeout=300, env=env)
    assert json.loads(got.read_text()) == {"format": "x"}, proc.stderr
    assert "no pairing install" not in proc.stderr and "did not finish" not in proc.stderr, proc.stderr


def test_ci_and_the_release_build_llmcf_pair_static_and_refuse_its_test_mode():
    flows = REPO_ROOT / ".github" / "workflows"
    release = (flows / "release-tools.yml").read_text(encoding="utf-8")
    assert "for crate in llmcf-setpriv llmcf-install llmcf-pair; do" in release
    ci = (flows / "ci.yml").read_text(encoding="utf-8")
    assert 'cargo build --locked --release -p llmcf-pair --target "$target"' in ci
    assert "llmcf-pair announce --out /tmp/x --test-loopback 0" in ci
    assert "LLMCF_PAIR_TEST_BIN=" in ci and "tests/test_pair_pake.py" in ci
    members = (REPO_ROOT / "rust" / "Cargo.toml").read_text(encoding="utf-8")
    assert '"llmcf-pair"' in members
    for source in (REPO_ROOT / "rust" / "llmcf-pair" / "src").glob("*.rs"):
        text = source.read_text(encoding="utf-8")
        assert "unsafe " not in text.replace("forbid(unsafe_code)", ""), source.name
    lib = (REPO_ROOT / "rust" / "llmcf-pair" / "src" / "lib.rs").read_text(encoding="utf-8")
    assert "#![forbid(unsafe_code)]" in lib
    cargo = (REPO_ROOT / "rust" / "llmcf-pair" / "Cargo.toml").read_text(encoding="utf-8")
    for line in cargo.splitlines():
        if "version = " in line and line.split("=")[0].strip() not in ("version", "rust-version"):
            assert 'version = "=' in line, line



@pytest.mark.skipif(os.name == "nt", reason="llmcf-pair and the probe's pairing run on Linux boards")
def test_the_python_probe_starts_llmcf_pair_and_hands_the_content_to_its_install(tmp_path, monkeypatch):
    from orchestrator.runtime import hardware_probe as probe
    seen_id = tmp_path / "identity.json"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "llmcf-pair"
    fake.write_bytes(("#!/bin/sh\n"
                      'while [ $# -gt 0 ]; do case "$1" in --out) out=$2; shift 2;; --identity) id=$2; shift 2;; '
                      '*) shift;; esac; done\n'
                      f'cp "$id" "{seen_id}"\n'
                      "printf '{\"format\":\"x\"}' > \"$out\"\nexit 0\n").encode())
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("SUDO_USER", "dietpi")
    monkeypatch.delenv("HW_PROBE_SYSROOT", raising=False)
    monkeypatch.setattr(probe.os, "geteuid", lambda: 0, raising=False)
    # behaviour changed on purpose: the probe's own install (the kit's pairing step) takes the content; one that is
    # not the framework's installs nothing and says so in the pairing block
    assert probe.pair_by_code() == {"PAIR_STATE": "skipped", "PAIR_REASON": "no-kit"}
    got = []

    def install(path, account=None):
        got.append((json.loads(Path(path).read_text()), account))
        return {"PAIR_STATE": "installed"}
    monkeypatch.setattr(probe, "pair_install_content", install)
    assert probe.pair_by_code() == {"PAIR_STATE": "installed"}
    assert got == [({"format": "x"}, "dietpi")]
    identity = json.loads(seen_id.read_text())
    assert identity["account"] == "dietpi" and identity["probe_version"] == probe.TOOL_VERSION
    monkeypatch.setattr(probe.os, "geteuid", lambda: 1000, raising=False)
    assert probe.pair_by_code() == {"PAIR_STATE": "skipped", "PAIR_REASON": "not-root"}
