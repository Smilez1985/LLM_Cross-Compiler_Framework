#!/usr/bin/env python3
"""
Pairing by code: the framework's side of the hardware probe's second way of pairing (the board's side is the Rust
program rust/llmcf-pair). The probe on the board, run with sudo, starts `llmcf-pair announce`: it shows an 8-digit code
on the board's console and announces `_llmcf-pair._tcp` by mDNS on the local links for at most 10 minutes. Here the
framework looks for such boards (browse), the PERSON types the code (the CLI's prompt or the GUI's dialog; never an AI:
no module an AI can reach imports this one, tests/test_pair_pake.py follows the imports), and exchange() runs SPAKE2
with it over TCP: the code never crosses the network, a wrong code costs one of the board's three tries, and only
after both sides proved the code does the board send its SSH host keys and the probe's identity and the framework send
its pairing content (its public key line, the root-owned key line and the signer files: the same rendered content the
root step of pairing installs, gate.root_board_script). The result goes to the one end of every pairing,
remote/pairing.py complete_pairing(), which pins the host keys: no trust-on-first-use question follows.

Why the framework's side is Python (and not a host program from the same Rust crate): the framework runs on Windows,
macOS and Linux, where a second native program would have to be built, shipped and checked for each; SPAKE2 needs only
Ed25519 group arithmetic, which libsodium gives in constant time through PyNaCl (a pinned dependency of paramiko and
now of the framework itself), and the AEAD, HKDF and HMAC come from `cryptography`. The two sides are kept byte for
byte equal to RustCrypto's spake2 0.4 (Ed25519 group, its M and N, its HKDF password scalar and its transcript hash);
tests/test_pair_pake.py runs this module against the Rust program when CI built it.

Local links only (DESIGN: no internet relay): browse() asks only over this computer's private and link-local
addresses (utils/locality.py local_links: a direct cable without router, the computer's own hotspot, a board as access
point, a LAN) and keeps only answers from such addresses; exchange() refuses any other address before it connects.

The protocol (version 1) is described in rust/llmcf-pair/src/proto.rs; the constants below are the same.
"""

import hashlib
import hmac
import ipaddress
import json
import os
import select
import socket
import struct
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

# --- the protocol's constants (rust/llmcf-pair/src/proto.rs and mdns.rs) -----------------------------------------------

MAGIC = b"LLMCF-PAIR/1"
ID_A = b"llmcf-pair board"
ID_B = b"llmcf-pair framework"
SALT = b"llmcf-pair/1"
WRONG = b"WRONG"
MAX_FRAME = 256 * 1024
SERVICE = "_llmcf-pair._tcp.local"
MDNS_PORT = 5353
MDNS_V4 = "224.0.0.251"
MDNS_V6 = "ff02::fb"
CODE_DIGITS = 8
BROWSE_SECONDS = 3.0
SESSION_SECONDS = 30.0

# SPAKE2 over Ed25519 as RustCrypto's spake2 0.4 (and python-spake2) define it: the group order L and the points M, N.
_L = 2 ** 252 + 27742317777372353535851937790883648493
_M = bytes.fromhex("15cfd18e385952982b6a8f8c7854963b58e34388c8e6dae891db756481a02312")
_N = bytes.fromhex("f04f2e7eb734b2a8f8b472eaf9c3c632576ac64aea650b496a8a20ff00e583c3")

# Why a pairing failed: wrong_code (the board's confirmation did not match: the code was wrong, the board counted the
# try), not_local (the board's address is no local link), network (no answer, the connection broke, the board's
# window ended or its tries were used up), protocol (the other side is no pairing board or broke the protocol), code
# (the typed text is not an 8-digit code).
KINDS = ("wrong_code", "not_local", "network", "protocol", "code")


class PakeError(Exception):
    def __init__(self, message: str, kind: str):
        super().__init__(message)
        self.kind = kind if kind in KINDS else "protocol"

    def text(self, lang: Optional[str] = None) -> str:
        """The failure in the person's language (utils/l10n_boards.py boards.pair.err.<kind>; `lang` None: the UI
        language), what the CLI prints and the GUI shows; str(self) stays the English detail it quotes."""
        from orchestrator.utils.localization import ui_text
        return ui_text(f"boards.pair.err.{self.kind}", lang, reason=" ".join(str(self).split())[:200])


# --- the code ----------------------------------------------------------------------------------------------------------

def normal_code(text: Any) -> str:
    """The 8 digits of what the person typed (spaces and dashes between them allowed, as the board shows "1234 5678");
    PakeError(code) for anything else."""
    digits = "".join(ch for ch in str(text or "") if ch not in " -\t")
    if len(digits) != CODE_DIGITS or not digits.isascii() or not digits.isdigit():
        raise PakeError("The code is the 8 digits the board shows on its console.", "code")
    return digits


# --- SPAKE2, side B (the framework) ------------------------------------------------------------------------------------

def _nacl():
    try:
        from nacl import bindings
    except ImportError as e:  # pragma: no cover - PyNaCl is a dependency
        raise PakeError("Pairing by code needs the 'pynacl' package.", "protocol") from e
    return bindings


def _hkdf(ikm: bytes, salt: bytes, info: bytes, length: int) -> bytes:
    """RFC 5869 HKDF with SHA-256 (an empty salt is the hash length of zeros, as in RustCrypto's hkdf)."""
    prk = hmac.new(salt or b"\0" * 32, ikm, hashlib.sha256).digest()
    out, block, counter = b"", b"", 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        out += block
        counter += 1
    return out[:length]


def password_scalar(code: str) -> bytes:
    """spake2's password scalar: HKDF-SHA256(salt b"", the code, info "SPAKE2 pw", 48 bytes), read big-endian, reduced
    mod L, as 32 little-endian bytes."""
    okm = _hkdf(code.encode("ascii"), b"", b"SPAKE2 pw", 48)
    return (int.from_bytes(okm, "big") % _L).to_bytes(32, "little")


class Spake2B:
    """SPAKE2's side B: message() is b"B" || Y with Y = y*G + pw*N; finish(message of A) is the 32-byte key
    SHA-256(SHA-256(pw) || SHA-256(idA) || SHA-256(idB) || X || Y || K) with K = y*(X - pw*M)."""

    def __init__(self, code: str, *, scalar: Optional[bytes] = None):
        self._b = _nacl()
        self._code = code.encode("ascii")
        self._pw = password_scalar(code)
        self._y = scalar if scalar is not None else self._b.crypto_core_ed25519_scalar_reduce(os.urandom(64))
        try:
            yg = self._b.crypto_scalarmult_ed25519_base_noclamp(self._y)
            self._Y = self._b.crypto_core_ed25519_add(yg, self._b.crypto_scalarmult_ed25519_noclamp(self._pw, _N))
        except Exception as e:  # noqa: BLE001 - libsodium refuses a degenerate scalar
            raise PakeError("The pairing could not start (SPAKE2).", "protocol") from e

    def message(self) -> bytes:
        return b"B" + self._Y

    def finish(self, message_a: bytes) -> bytes:
        if len(message_a) != 33 or message_a[:1] != b"A":
            raise PakeError("The board's SPAKE2 message is not one of side A.", "protocol")
        X = message_a[1:]
        try:
            if not self._b.crypto_core_ed25519_is_valid_point(X):
                raise ValueError("not a point of the group")
            unblinded = self._b.crypto_core_ed25519_sub(X, self._b.crypto_scalarmult_ed25519_noclamp(self._pw, _M))
            K = self._b.crypto_scalarmult_ed25519_noclamp(self._y, unblinded)
        except Exception as e:  # noqa: BLE001 - a message that is no point of the group, or the identity
            raise PakeError("The board's SPAKE2 message is not valid.", "protocol") from e
        transcript = b"".join([hashlib.sha256(self._code).digest(), hashlib.sha256(ID_A).digest(),
                               hashlib.sha256(ID_B).digest(), X, self._Y, K])
        return hashlib.sha256(transcript).digest()


# --- the session's keys (proto.rs Keys) --------------------------------------------------------------------------------

class Keys:
    def __init__(self, k: bytes, msg_a: bytes, msg_b: bytes):
        self.transcript = hashlib.sha256(MAGIC + msg_a + msg_b).digest()
        self._confirm_host = _hkdf(k, SALT, b"confirm host", 32)
        self._confirm_board = _hkdf(k, SALT, b"confirm board", 32)
        self._host_to_board = _hkdf(k, SALT, b"key host to board", 32)
        self._board_to_host = _hkdf(k, SALT, b"key board to host", 32)

    def host_confirmation(self) -> bytes:
        return hmac.new(self._confirm_host, b"host" + self.transcript, hashlib.sha256).digest()

    def board_confirmation_ok(self, tag: bytes) -> bool:
        want = hmac.new(self._confirm_board, b"board" + self.transcript, hashlib.sha256).digest()
        return hmac.compare_digest(want, tag)

    @staticmethod
    def _nonce(counter: int) -> bytes:
        return b"\0\0\0\0" + struct.pack(">Q", counter)

    def seal_host(self, counter: int, plain: bytes) -> bytes:
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
        return ChaCha20Poly1305(self._host_to_board).encrypt(self._nonce(counter), plain, self.transcript)

    def open_board(self, counter: int, sealed: bytes) -> bytes:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
        try:
            return ChaCha20Poly1305(self._board_to_host).decrypt(self._nonce(counter), sealed, self.transcript)
        except InvalidTag:
            raise PakeError("A sealed message of the board does not open.", "protocol") from None


# --- frames ------------------------------------------------------------------------------------------------------------

def _read_exact(sock: Any, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise PakeError("The board closed the connection.", "network")
        data += chunk
    return data


def read_frame(sock: Any) -> bytes:
    (n,) = struct.unpack(">I", _read_exact(sock, 4))
    if n > MAX_FRAME:
        raise PakeError("The board sent a frame that is too large.", "protocol")
    return _read_exact(sock, n)


def write_frame(sock: Any, data: bytes) -> None:
    if len(data) > MAX_FRAME:
        raise PakeError("The pairing content is too large.", "protocol")
    sock.sendall(struct.pack(">I", len(data)) + data)


def _json_object(data: bytes) -> Dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        value = None
    if not isinstance(value, dict):
        raise PakeError("The board's answer is not a JSON object.", "protocol")
    return value


# --- found boards and their facts --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class FoundBoard:
    """A board that announced the pairing: its mDNS instance name, its address on the local link (an IPv6 link-local
    one with the interface's zone `scope_id`), the TCP port of the pairing and the kind of link (locality.LINK_KINDS,
    or "test" in the tests' loopback-as-link mode); `ipv4`, the board's own IPv4 address on a local link when its
    answer came over IPv6 and named one too ("" otherwise)."""
    instance: str
    address: str
    port: int
    scope_id: int = 0
    link: str = ""
    ipv4: str = ""

    @property
    def ssh_host(self) -> str:
        """The address the framework reaches the board at later by SSH (kept with its pairing): its IPv4 address on
        the link when it has one, else its address, a link-local IPv6 one with the zone (fe80::1%eth0): without the
        zone such an address names no interface and no later connection finds the board."""
        if self.ipv4:
            return self.ipv4
        ip = ipaddress.ip_address(self.address)
        if ip.version == 6 and ip.is_link_local and self.scope_id:
            try:
                zone = socket.if_indextoname(self.scope_id)
            except (OSError, AttributeError, ValueError):
                zone = str(self.scope_id)
            return f"{self.address}%{zone}"
        return self.address

    @property
    def label(self) -> str:
        name = self.instance[:-len("." + SERVICE)] if self.instance.endswith("." + SERVICE) else self.instance
        return f"{name} ({self.link or 'local link'})"


@dataclass(frozen=True)
class HostKeyFact:
    """One SSH host key the board proved: its type, its public key line ("TYPE BLOB") and its SHA256 fingerprint."""
    key_type: str
    key: str
    fingerprint: str


@dataclass(frozen=True)
class PakeResult:
    """What the board proved over the channel the code keyed: the probe's identity and the board's SSH host keys."""
    board: FoundBoard
    identity: Dict[str, Any]
    host_keys: Tuple[HostKeyFact, ...] = field(default_factory=tuple)

    @property
    def fingerprints(self) -> Tuple[str, ...]:
        return tuple(k.fingerprint for k in self.host_keys)

    @property
    def key_lines(self) -> Tuple[str, ...]:
        return tuple(k.key for k in self.host_keys)


def _facts(board: FoundBoard, value: Dict[str, Any]) -> PakeResult:
    identity = value.get("identity") if isinstance(value.get("identity"), dict) else {}
    keys = []
    for entry in value.get("host_keys") or []:
        if not isinstance(entry, dict):
            continue
        key_type, line, fp = (str(entry.get(k) or "") for k in ("type", "key", "fingerprint"))
        if key_type and line.startswith(key_type + " ") and fp.startswith("SHA256:") and len(line) < 8192:
            keys.append(HostKeyFact(key_type, line, fp))
    return PakeResult(board=board, identity=identity, host_keys=tuple(keys))


def _address_allowed(address: str, allow_loopback: bool) -> bool:
    from orchestrator.utils.locality import address_kind
    kind = address_kind(address)
    return kind in ("private", "link_local") or (allow_loopback and kind == "loopback")


# --- the exchange ------------------------------------------------------------------------------------------------------

def _connect(board: FoundBoard, timeout: float) -> socket.socket:
    ip = ipaddress.ip_address(board.address)
    if ip.version == 6:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        target: Tuple[Any, ...] = (board.address, board.port, 0, board.scope_id)
    else:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        target = (board.address, board.port)
    sock.settimeout(timeout)
    try:
        sock.connect(target)
    except OSError:
        sock.close()
        raise
    return sock


class _Bounded:
    """The connection with one end for the whole exchange (`timeout` seconds after it was opened): every receive and
    send gets only the time left, and none starts after the end (socket.timeout, a network failure). A per-read
    timeout alone would let a board that sends one byte at a time hold the person's pairing as long as it likes."""

    def __init__(self, sock: Any, timeout: float):
        self._sock = sock
        self._end = time.monotonic() + max(0.0, float(timeout))

    def _left(self) -> float:
        left = self._end - time.monotonic()
        if left <= 0:
            raise socket.timeout("the pairing's time ended")
        if hasattr(self._sock, "settimeout"):
            self._sock.settimeout(left)
        return left

    def recv(self, n: int) -> bytes:
        self._left()
        return self._sock.recv(n)

    def sendall(self, data: bytes) -> None:
        self._left()
        self._sock.sendall(data)


def exchange(board: FoundBoard, code: str, content: Dict[str, Any], *, timeout: float = SESSION_SECONDS,
             allow_loopback: bool = False, connect: Optional[Callable[[FoundBoard, float], Any]] = None) -> PakeResult:
    """Runs the pairing with `board` (protocol version 1): SPAKE2 with the person's `code`, the board's confirmation
    checked before anything of it is read, then `content` (the framework's pairing content, a JSON object) sealed to
    the board. Returns the board's proven facts; PakeError with its kind otherwise. Only a board on a local link
    (`allow_loopback`: the tests' loopback-as-link mode); `connect` is the tests' stand-in for the TCP connection."""
    code = normal_code(code)
    if not _address_allowed(board.address, allow_loopback):
        raise PakeError("The board's address is not on a local link; pairing by code works only over a cable, the "
                        "computer's or the board's WLAN, or the own network.", "not_local")
    try:
        sock = (connect or _connect)(board, timeout)
    except OSError as e:
        raise PakeError(f"The board does not answer on its pairing port ({e.__class__.__name__}).",
                        "network") from None
    raw, sock = sock, _Bounded(sock, timeout)
    try:
        hello = read_frame(sock)
        if len(hello) != len(MAGIC) + 33 or hello[:len(MAGIC)] != MAGIC:
            raise PakeError("The other side is not a pairing board of this framework.", "protocol")
        msg_a = hello[len(MAGIC):]
        side = Spake2B(code)
        msg_b = side.message()
        keys = Keys(side.finish(msg_a), msg_a, msg_b)
        write_frame(sock, msg_b + keys.host_confirmation())
        welcome = read_frame(sock)
        if welcome == WRONG or len(welcome) < 32 or not keys.board_confirmation_ok(welcome[:32]):
            raise PakeError("The code was wrong: the board did not accept it (each wrong code uses one of its "
                            "three tries).", "wrong_code")
        facts = _json_object(keys.open_board(0, welcome[32:]))
        write_frame(sock, keys.seal_host(0, json.dumps(content, sort_keys=True).encode("utf-8")))
        done = _json_object(keys.open_board(1, read_frame(sock)))
        if done.get("received") is not True:
            raise PakeError("The board did not take the pairing content.", "protocol")
        return _facts(board, facts)
    except (OSError, struct.error) as e:
        raise PakeError(f"The connection to the board broke ({e.__class__.__name__}).", "network") from None
    finally:
        try:
            raw.close()
        except Exception:  # noqa: BLE001 - the connection is done
            pass


# --- mDNS browse (RFC 6762 one-shot query: the answer comes back by unicast) -------------------------------------------

def _name(text: str) -> bytes:
    out = b""
    for label in text.split("."):
        if label:
            raw = label.encode("utf-8")[:63]
            out += bytes([len(raw)]) + raw
    return out + b"\0"


def query_packet(qid: int) -> bytes:
    """A one-shot mDNS question for the pairing service's PTR record."""
    return struct.pack(">HHHHHH", qid & 0xffff, 0, 1, 0, 0, 0) + _name(SERVICE) + struct.pack(">HH", 12, 1)


def _read_name(packet: bytes, pos: int) -> Tuple[str, int]:
    labels: List[str] = []
    end = None
    jumps = 0
    while True:
        if pos >= len(packet):
            raise ValueError("cut name")
        n = packet[pos]
        if n & 0xc0 == 0xc0:
            if pos + 1 >= len(packet):
                raise ValueError("cut pointer")
            if end is None:
                end = pos + 2
            pos = ((n & 0x3f) << 8) | packet[pos + 1]
            jumps += 1
            if jumps > 16:
                raise ValueError("pointer loop")
            continue
        if n == 0:
            return ".".join(labels), (end if end is not None else pos + 1)
        labels.append(packet[pos + 1:pos + 1 + n].decode("utf-8", "replace").lower())
        if len(labels) > 32:
            raise ValueError("too many labels")
        pos += 1 + n


def parse_answer(packet: bytes) -> Dict[str, Any]:
    """The records of an mDNS answer that matter for the pairing: instances (PTR of SERVICE), ports (SRV: instance ->
    (port, host name)) and addresses (A/AAAA: host name -> addresses). Empty for anything that is not an answer."""
    out: Dict[str, Any] = {"instances": [], "srv": {}, "addresses": {}}
    try:
        qid, flags, qd, an, ns, ar = struct.unpack(">HHHHHH", packet[:12])
        if not flags & 0x8000:
            return out
        pos = 12
        for _ in range(qd):
            _, pos = _read_name(packet, pos)
            pos += 4
        for _ in range(an + ns + ar):
            name, pos = _read_name(packet, pos)
            rtype, _cls, _ttl, rdlen = struct.unpack(">HHIH", packet[pos:pos + 10])
            pos += 10
            rdata_at = pos
            pos += rdlen
            if pos > len(packet):
                break
            if rtype == 12 and name == SERVICE:
                out["instances"].append(_read_name(packet, rdata_at)[0])
            elif rtype == 33 and rdlen >= 7:
                port = struct.unpack(">H", packet[rdata_at + 4:rdata_at + 6])[0]
                out["srv"][name] = (port, _read_name(packet, rdata_at + 6)[0])
            elif rtype == 1 and rdlen == 4:
                out["addresses"].setdefault(name, []).append(socket.inet_ntop(socket.AF_INET, packet[rdata_at:pos]))
            elif rtype == 28 and rdlen == 16:
                out["addresses"].setdefault(name, []).append(socket.inet_ntop(socket.AF_INET6, packet[rdata_at:pos]))
    except (struct.error, ValueError, IndexError, OSError):
        pass
    return out


def boards_in(packet: bytes, source: Tuple[Any, ...], *, link: str = "", allow_loopback: bool = False
              ) -> List[FoundBoard]:
    """The boards an answer from `source` (the sender's socket address) announces: each instance with its SRV port,
    at the answer's own A/AAAA address when that is on a local link (an IPv6 link-local one gets the zone of the
    sender) and else at the sender's address; nothing from a sender that is not on a local link."""
    sender = str(source[0]).split("%", 1)[0]
    if not _address_allowed(sender, allow_loopback):
        return []
    scope = int(source[3]) if len(source) > 3 else 0
    found = parse_answer(packet)
    out = []
    for instance in found["instances"]:
        if instance not in found["srv"]:
            continue
        port, host = found["srv"][instance]
        if not 0 < port < 65536:
            continue
        address, ipv4, picked = sender, "", False
        version = ipaddress.ip_address(sender).version
        for candidate in found["addresses"].get(host, []):
            if not _address_allowed(candidate, allow_loopback):
                continue
            if ipaddress.ip_address(candidate).version == version and not picked:
                address, picked = candidate, True   # the answer's first local address of the sender's family
            elif version == 6 and ipaddress.ip_address(candidate).version == 4 and not ipv4:
                ipv4 = candidate   # the board's IPv4 address on the link: the one kept for SSH later
        zone = scope if ipaddress.ip_address(address).is_link_local else 0
        out.append(FoundBoard(instance=instance, address=address, port=port, scope_id=zone, link=link, ipv4=ipv4))
    return out


def _query_sockets(links: Iterable[Any]) -> List[Tuple[socket.socket, Tuple[Any, ...], str]]:
    """One UDP socket per local link with the mDNS group to ask there, each with the link's kind. Every socket is
    bound to this computer's own address on that link (IPv6: the link-local address with its interface index), never
    to all interfaces: the answers come back to that address, and nothing on another network reaches the socket."""
    out = []
    seen = set()
    for link in links:
        try:
            ip = ipaddress.ip_address(link.address)
        except ValueError:
            continue
        try:
            if ip.version == 4:
                if link.address in seen:
                    continue
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.bind((link.address, 0))
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(link.address))
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
                out.append((s, (MDNS_V4, MDNS_PORT), link.kind))
                seen.add(link.address)
            elif link.scope_id and ("v6", link.scope_id) not in seen:
                s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
                s.bind((link.address, 0, 0, link.scope_id))
                s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_IF, link.scope_id)
                s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_HOPS, 255)
                out.append((s, (MDNS_V6, MDNS_PORT, 0, link.scope_id), link.kind))
                seen.add(("v6", link.scope_id))
        except OSError:
            continue
    return out


def browse(timeout: float = BROWSE_SECONDS, *, links: Optional[Sequence[Any]] = None,
           targets: Optional[Sequence[Tuple[str, int]]] = None, allow_loopback: bool = False) -> List[FoundBoard]:
    """The boards that announce the pairing on this computer's local links (locality.local_links, or `links`): one
    mDNS question per link, the answers collected for `timeout` seconds. `targets` (the tests' loopback-as-link mode)
    asks those (address, port) pairs directly instead. Every board once, in the order the answers came."""
    sockets: List[Tuple[socket.socket, Tuple[Any, ...], str]] = []
    if targets is not None:
        for host, port in targets:
            family = socket.AF_INET6 if ":" in host else socket.AF_INET
            s = socket.socket(family, socket.SOCK_DGRAM)
            sockets.append((s, (host, port), "test"))
    else:
        if links is None:
            from orchestrator.utils.locality import local_links
            links = local_links()
        sockets = _query_sockets(links)
    qid = int.from_bytes(os.urandom(2), "big")
    packet = query_packet(qid)
    found: List[FoundBoard] = []
    seen = set()
    try:
        for s, dest, _ in sockets:
            try:
                s.sendto(packet, dest)
            except OSError:
                continue
        kinds = {s.fileno(): kind for s, _, kind in sockets}
        deadline = time.monotonic() + max(0.1, float(timeout))
        while sockets:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            ready, _, _ = select.select([s for s, _, _ in sockets], [], [], left)
            for s in ready:
                try:
                    data, source = s.recvfrom(9000)
                except OSError:
                    continue
                for board in boards_in(data, source, link=kinds.get(s.fileno(), ""), allow_loopback=allow_loopback):
                    key = (board.instance, board.address, board.port)
                    if key not in seen:
                        seen.add(key)
                        found.append(board)
    finally:
        for s, _, _ in sockets:
            s.close()
    return found


# --- the framework's pairing content and the end of the pairing ------------------------------------------------------

CONTENT_FORMAT = "llmcf-pair-content/1"


def pairing_content(state_dir: Any, bundle: Any) -> Dict[str, Any]:
    """The framework's pairing content for a board: the same rendered content pairing's root step installs
    (gate.root_board_script and install_root_board): the framework's public key line, its root-owned restricted key
    line, and from `bundle` (the caller's runtime/device/board_ops.pairing_bundle: gate.GateBundle) the allowed_signers
    line, the signing key as PEM, its self-test signature and the gate program. With a signer it also carries the
    probe kit's very root step (gate.probe_kit_root_step, `root_step_b64`) and the gate program (`gate_b64`), base64
    so the shell probe reads them without a JSON parser: the probe's pair_install_content writes them as a kit folder
    and runs the kit's own pairing step on it, one implementation for both ways. No secret: the private key never
    leaves the computer."""
    import base64
    from pathlib import Path

    from orchestrator.remote import gate
    from orchestrator.utils import ssh_trust
    public_line = ssh_trust.client_key(Path(state_dir))[1]
    signers = getattr(bundle, "signers", "") or ""
    content = {"format": CONTENT_FORMAT, "public_key": public_line,
               "key_line": gate.root_key_line(public_line, gate.ROOT_GATE), "signers": signers,
               "signing_key_pem": getattr(bundle, "pem", "") or "", "selftest": getattr(bundle, "selftest", "") or "",
               "gate": getattr(bundle, "gate", "") or "",
               "missing": "" if signers else (getattr(bundle, "missing", "") or "no-signer")}
    if signers and getattr(bundle, "selftest", ""):
        step = gate.probe_kit_root_step(public_line, bundle)
        content["root_step_b64"] = base64.b64encode(step.encode("utf-8")).decode("ascii")
        content["gate_b64"] = base64.b64encode(content["gate"].encode("utf-8")).decode("ascii")
    return content


# How long a pairing offer of the short code's way stays open (the board's window is 10 minutes; the offer is made
# just before the exchange and used at its end).
PAKE_OFFER_HOURS = 1


def pairing_result(result: "PakeResult", nonce: str, tier: str = "root") -> Any:
    """The remote/pairing.py PairingResult of what the board proved: the host keys' fingerprints RECOMPUTED here from
    the key lines the board sent (never its own fingerprint strings), one per key family (the first of each), the
    offer's `nonce`, the way pake and the key tier the root step gives (root)."""
    from orchestrator.remote.pairing import PairingResult
    from orchestrator.remote.trust import FAMILY_ORDER, KeyTextError, fingerprint, key_family, parse_public_key
    found: Dict[str, str] = {}
    for fact in result.host_keys:
        try:
            key = parse_public_key(fact.key)
        except KeyTextError:
            continue
        family = key_family(key.get_name())
        if family and family not in found:
            found[family] = fingerprint(key)
    keys = tuple((f, found[f]) for f in FAMILY_ORDER if f in found)
    return PairingResult(state="installed", nonce=nonce, host_keys=keys, way="pake", tier=tier)


@dataclass(frozen=True)
class PairedByCode:
    """A finished pairing by code: what the board proved, the offer the session was recorded under and what
    complete_pairing() did with it (remote/pairing.py PairingDone)."""
    result: PakeResult
    done: Any
    offer: Any = None


def pair_by_code(board: FoundBoard, code: str, content: Dict[str, Any], *, state_dir: Any, via: str = "cli",
                 audit: Any = None, ssh_port: int = 22, allow_loopback: bool = False,
                 connect: Optional[Callable[[FoundBoard, float], Any]] = None) -> PairedByCode:
    """The person's pairing by code, end to end, through the one pairing core (remote/pairing.py): a pairing offer of
    the way pake first (create_offer: the session's record, single use, audited; PairingRefused no-key without the
    framework's signer), its nonce sent with the framework's pairing `content` (pairing_content), the exchange with
    the code the person typed, then complete_pairing() with a PairingResult of the host keys the board proved
    (pairing_result: fingerprints recomputed from the key lines). The board's SSH address (FoundBoard.ssh_host, with
    the SSH port of the probe's identity) is kept with the pin. Only the person's channels call this (`via` cli or
    gui), through runtime/device/person_steps.pair_by_code. PakeError when the exchange fails, PairingRefused when the
    core refuses."""
    from orchestrator.remote.address import Endpoint
    from orchestrator.remote import pairing
    normal_code(code)   # a typo is no session: no offer is made for it
    offer = pairing.create_offer(state_dir, public_line=content.get("public_key", ""),
                                 signers=content.get("signers", ""), pem=content.get("signing_key_pem", ""), via=via,
                                 audit=audit, actor=via, way="pake", ttl_hours=PAKE_OFFER_HOURS)
    sent = dict(content, nonce=offer.nonce)
    result = exchange(board, code, sent, allow_loopback=allow_loopback, connect=connect)
    port = result.identity.get("ssh_port", ssh_port)
    port = port if isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536 else ssh_port
    done = pairing.complete_pairing(state_dir, pairing_result(result, offer.nonce), via=via, audit=audit, actor=via,
                                    label=board.label[:64], address=Endpoint(board.ssh_host, port).store_form)
    return PairedByCode(result=result, done=done, offer=offer)


__all__ = ["BROWSE_SECONDS", "CODE_DIGITS", "CONTENT_FORMAT", "FoundBoard", "HostKeyFact", "KINDS", "Keys", "MAGIC",
           "PAKE_OFFER_HOURS", "PairedByCode", "PakeError", "PakeResult", "SERVICE", "Spake2B", "boards_in", "browse",
           "exchange", "normal_code", "pair_by_code", "pairing_content", "pairing_result", "parse_answer",
           "password_scalar", "query_packet",
           "read_frame", "write_frame"]
