#!/usr/bin/env python3
"""
Community packs: manifest, Ed25519 signatures, privacy scrub, and encryption for private sharing.

A pack is a zip file:
  pack.json   manifest: schema_version, kind, id, version, author, license, lcf_min_version, created (UTC)
              and files [{path, sha256, size, mode}]
  pack.sig    optional author signature: {"v": 1, "alg": "Ed25519", "key_id": "...", "sig": "<base64url>"}
  <files>     exactly the files the manifest lists, nothing else

Public packs are signed, not encrypted: for public data, authenticity and integrity are what matter. A pack
shared privately (with your own machines or a team) is encrypted to X25519 public keys and/or a passphrase and
signed by the sender, so recipients know who sent it (format: see encrypt_pack). Only standard primitives of the
`cryptography` package are composed: Ed25519, X25519, HKDF-SHA256, HMAC-SHA256, scrypt and ChaCha20-Poly1305.

Each user has one Ed25519 key pair (signing) and one X25519 key pair (receiving encrypted packs), kept in the
encrypted secrets store (UserKeys); the private keys are never written to disk unencrypted.
"""

import base64
import binascii
import hashlib
import hmac
import io
import json
import os
import re
import shutil
import stat
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, List, Literal, Optional, Sequence, Tuple, Union

import yaml
from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from orchestrator import __version__
from orchestrator.utils.helpers import calculate_file_checksum

MANIFEST_NAME = "pack.json"
SIGNATURE_NAME = "pack.sig"
RESERVED_NAMES = frozenset({MANIFEST_NAME, SIGNATURE_NAME})
PACK_KINDS = ("target", "recipe", "knowledge")
# Packs carry scripts, recipes and text, never model weights.
MAX_PACK_BYTES = 512 * 1024 * 1024
MAX_FILES = 10_000
KEYS_FILE = Path(__file__).resolve().parents[2] / "configs" / "community_keys.yml"

_SHA256 = r"^[0-9a-f]{64}$"
_KEY_ID = r"^[0-9a-f]{16}$"
_PACK_ID = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
_VERSION = r"^\d{1,4}\.\d{1,4}\.\d{1,4}(?:[-+][0-9A-Za-z.-]{1,32})?$"
_LICENSE = r"^[A-Za-z0-9][A-Za-z0-9.+-]{0,63}$"
_UTC_TIME = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
# Characters Windows cannot store or treats specially, control characters and the backslash.
_BAD_PATH_CHARS = re.compile(r'[\x00-\x1f\x7f\\:*?"<>|]')

# Domain separation: a signature made for one purpose never verifies for another.
_MANIFEST_CONTEXT = b"lcf-pack-manifest/v1\n"
_ENCRYPTED_CONTEXT = b"lcf-encrypted-pack/v1 signature\n"


class PackError(Exception):
    """A pack, key or signature problem; the message is meant for the user."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Manifest ---------------------------------------------------------------------------------------------
# Adapted from rag4ll rag4ll/sidecar/manifest.py (MIT, (c) 2026 Smilez1985, https://github.com/Smilez1985/rag4ll):
# the file list with sha256/size/mode, the relpath rules and the folder walk (check_relpath to verify_tree).
# Hardened: relpaths validated on reading, an lstat walk that refuses links and special files, masked modes,
# ISO UTC time, and canonical bytes for signatures.

def check_relpath(path: str) -> str:
    """A manifest path: relative, '/'-separated, without '..', '.', empty parts, backslashes, drive letters or
    characters Windows cannot store."""
    if not isinstance(path, str) or not path or len(path) > 1024:
        raise ValueError("path must be 1-1024 characters")
    if path.startswith("/") or _BAD_PATH_CHARS.search(path):
        raise ValueError(f"not a plain relative path: {path!r}")
    for part in path.split("/"):
        # Windows drops trailing dots and spaces, so 'a.' and 'a' would be the same file there.
        if part in ("", ".", "..") or len(part) > 255 or part != part.rstrip(" ."):
            raise ValueError(f"not a plain relative path: {path!r}")
    return path


class PackFile(_Strict):
    path: str
    sha256: str = Field(pattern=_SHA256)
    size: int = Field(ge=0, le=MAX_PACK_BYTES)
    # Masked on creation: no setuid/setgid/sticky bits and no group/world write ever travel with a pack.
    mode: Literal[0o644, 0o755] = 0o644

    @field_validator("path")
    @classmethod
    def _relpath(cls, value: str) -> str:
        return check_relpath(value)


class PackManifest(_Strict):
    schema_version: Literal[1] = 1
    kind: Literal["target", "recipe", "knowledge"]
    id: str = Field(pattern=_PACK_ID)
    version: str = Field(pattern=_VERSION)
    author: str = Field(min_length=1, max_length=100, pattern=r"^[^\x00-\x1f\x7f]+$")
    license: str = Field(pattern=_LICENSE)
    lcf_min_version: str = Field(default=__version__, pattern=_VERSION)
    created: str = Field(pattern=_UTC_TIME)
    files: List[PackFile] = Field(default_factory=list, max_length=MAX_FILES)

    @model_validator(mode="after")
    def _unique_sorted(self):
        seen, folders = set(), set()
        for f in self.files:
            # Case-insensitive: on Windows and macOS 'A.sh' and 'a.sh' are one file.
            key = f.path.casefold()
            if key in seen:
                raise ValueError(f"file listed twice: {f.path}")
            if key in RESERVED_NAMES:
                raise ValueError(f"reserved name in the file list: {f.path}")
            seen.add(key)
            parts = key.split("/")
            folders.update("/".join(parts[:i]) for i in range(1, len(parts)))
        if seen & folders:
            raise ValueError(f"a path is both a file and a folder: {sorted(seen & folders)[0]}")
        if sum(f.size for f in self.files) > MAX_PACK_BYTES:
            raise ValueError("the files are larger than a pack may be")
        self.files = sorted(self.files, key=lambda f: f.path)
        return self

    def canonical_bytes(self) -> bytes:
        """The bytes a signature covers: sorted keys, no whitespace, UTF-8."""
        return _canonical(self.model_dump(mode="json"))

    def content_sha256(self) -> str:
        """Digest of the file list only (path, sha256, size, mode): the same content always gives the same value,
        whoever packed it and when. Recipes name their target pack by it."""
        return _files_digest(self.files)


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _files_digest(files: Iterable[PackFile]) -> str:
    listed = sorted(files, key=lambda f: f.path)
    return hashlib.sha256(_canonical([f.model_dump(mode="json") for f in listed])).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _scan(root: Path) -> List[Tuple[str, Path, os.stat_result]]:
    """Every regular file below root (the reserved names at its top excluded). Nothing is followed: a symbolic
    link, a junction or a special file anywhere raises PackError."""
    root = Path(root)
    try:
        if not stat.S_ISDIR(os.lstat(root).st_mode):
            raise PackError(f"{root} is not a folder (a link to a folder is not accepted).")
    except FileNotFoundError:
        raise PackError(f"{root} does not exist.") from None
    found: List[Tuple[str, Path, os.stat_result]] = []
    problems: List[str] = []

    def walk(directory: Path, prefix: str) -> None:
        with os.scandir(directory) as it:
            entries = sorted(it, key=lambda e: e.name)
        for entry in entries:
            rel = prefix + entry.name
            if not prefix and entry.name in RESERVED_NAMES:
                continue
            st = entry.stat(follow_symlinks=False)
            # Reparse points cover Windows symbolic links and junctions.
            if stat.S_ISLNK(st.st_mode) or getattr(st, "st_file_attributes", 0) & 0x400:
                problems.append(f"{rel}: symbolic link")
            elif stat.S_ISDIR(st.st_mode):
                walk(Path(entry.path), rel + "/")
            elif not stat.S_ISREG(st.st_mode):
                problems.append(f"{rel}: special file")
            else:
                try:
                    found.append((check_relpath(rel), Path(entry.path), st))
                except ValueError as e:
                    problems.append(f"{rel}: {e}")

    walk(root, "")
    if problems:
        raise PackError("Not allowed in a pack:\n- " + "\n- ".join(problems[:20]))
    if len(found) > MAX_FILES:
        raise PackError(f"More than {MAX_FILES} files.")
    return found


def _pack_files(root: Path) -> List[PackFile]:
    return [PackFile(path=rel, sha256=calculate_file_checksum(path), size=st.st_size,
                     mode=0o755 if st.st_mode & 0o111 else 0o644)
            for rel, path, st in _scan(root)]


def tree_sha256(root: Path) -> str:
    """PackManifest.content_sha256() of the files below root, without the pack metadata."""
    return _files_digest(_pack_files(root))


def build_manifest(root: Path, *, kind: str, id: str, version: str, author: str, license: str,
                   created: Optional[str] = None, lcf_min_version: Optional[str] = None) -> PackManifest:
    """The manifest of every file below root (see _scan)."""
    files = _pack_files(root)
    try:
        return PackManifest(kind=kind, id=id, version=version, author=author, license=license,
                            created=created or utc_now(), lcf_min_version=lcf_min_version or __version__,
                            files=files)
    except ValidationError as e:
        raise PackError(f"Invalid pack: {e}") from None


def verify_tree(root: Path, manifest: PackManifest) -> None:
    """Every listed file is there with its size and sha256, nothing else is, and there are no links or special
    files. Raises PackError naming every problem."""
    found = {rel: (path, st) for rel, path, st in _scan(root)}
    listed = {f.path: f for f in manifest.files}
    problems = [f"{rel}: not in the manifest" for rel in sorted(set(found) - set(listed))]
    problems += [f"{rel}: missing" for rel in sorted(set(listed) - set(found))]
    for rel in sorted(set(found) & set(listed)):
        path, st = found[rel]
        if st.st_size != listed[rel].size or calculate_file_checksum(path) != listed[rel].sha256:
            problems.append(f"{rel}: content differs from the manifest")
    if problems:
        raise PackError("The pack does not match its manifest:\n- " + "\n- ".join(problems[:20]))


# --- Keys and signatures ---------------------------------------------------------------------------------

PublicKey = Union[Ed25519PublicKey, X25519PublicKey]


def _raw_public(key: PublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _raw_private(key: Union[Ed25519PrivateKey, X25519PrivateKey]) -> bytes:
    return key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                             serialization.NoEncryption())


def _b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64d(text: str, length: Optional[int] = None) -> bytes:
    try:
        data = base64.b64decode(str(text), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("not valid base64") from None
    if length is not None and len(data) != length:
        raise ValueError(f"expected {length} bytes, got {len(data)}")
    return data


def key_id(key: PublicKey) -> str:
    """Short identifier of a public key: the first 16 hex characters of the sha256 of its raw bytes."""
    return hashlib.sha256(_raw_public(key)).hexdigest()[:16]


def public_key_text(key: PublicKey) -> str:
    """'ed25519:<base64>' (verifies signatures) or 'x25519:<base64>' (encrypts to its owner)."""
    kind = "ed25519" if isinstance(key, Ed25519PublicKey) else "x25519"
    return f"{kind}:{_b64e(_raw_public(key))}"


_PUBLIC_KEY_TOKEN = re.compile(r"\b(ed25519|x25519):([A-Za-z0-9+/]{43}=)")


def load_public_key(text: str) -> PublicKey:
    """Parses public_key_text() output."""
    m = _PUBLIC_KEY_TOKEN.fullmatch(str(text).strip())
    if not m:
        raise PackError("Not a public key (expected 'ed25519:<base64>' or 'x25519:<base64>').")
    raw = _b64d(m.group(2), 32)
    return Ed25519PublicKey.from_public_bytes(raw) if m.group(1) == "ed25519" else X25519PublicKey.from_public_bytes(raw)


def parse_public_keys(text: str) -> List[PublicKey]:
    """Every public key in a text block, e.g. what UserKeys.export_public() printed on another machine."""
    return [load_public_key(m.group(0)) for m in _PUBLIC_KEY_TOKEN.finditer(str(text))]


class PackSignature(_Strict):
    v: Literal[1] = 1
    alg: Literal["Ed25519"] = "Ed25519"
    key_id: str = Field(pattern=_KEY_ID)
    sig: str = Field(pattern=r"^[A-Za-z0-9_-]{86}$")  # 64 bytes, base64url without padding


class TrustedKey(_Strict):
    """A publisher key from configs/community_keys.yml."""
    key_id: str = Field(pattern=_KEY_ID)
    pubkey_b64: str
    valid_until: date
    name: str = Field(default="", max_length=100)

    @model_validator(mode="after")
    def _matches(self):
        if key_id(self.public_key()) != self.key_id:
            raise ValueError(f"key_id {self.key_id} does not belong to this public key")
        return self

    def public_key(self) -> Ed25519PublicKey:
        return Ed25519PublicKey.from_public_bytes(_b64d(self.pubkey_b64, 32))

    def valid_on(self, when: datetime) -> bool:
        return when.astimezone(timezone.utc).date() <= self.valid_until


class _KeysFile(_Strict):
    keys: List[TrustedKey] = Field(default_factory=list)


def load_trusted_keys(path: Optional[Path] = None) -> List[TrustedKey]:
    """Publisher keys shipped with the release (configs/community_keys.yml). A damaged file raises: an error is
    never read as "no keys" or as "trusted"."""
    path = Path(path or KEYS_FILE)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, yaml.YAMLError) as e:
        raise PackError(f"Cannot read the trusted keys in {path}: {e}") from None
    try:
        return _KeysFile.model_validate(data or {}).keys
    except ValidationError as e:
        raise PackError(f"The trusted keys in {path} are invalid: {e}") from None


def sign_manifest(manifest: PackManifest, private_key: Ed25519PrivateKey) -> PackSignature:
    sig = private_key.sign(_MANIFEST_CONTEXT + manifest.canonical_bytes())
    return PackSignature(key_id=key_id(private_key.public_key()),
                         sig=base64.urlsafe_b64encode(sig).decode("ascii").rstrip("="))


def verify_manifest(manifest: PackManifest, signature: PackSignature,
                    keys: Iterable[Union[TrustedKey, Ed25519PublicKey]], *, now: Optional[datetime] = None) -> str:
    """Returns the key id of the valid signature; raises PackError for an unknown or expired key or a signature
    that does not match (a changed manifest, a wrong key)."""
    now = now or datetime.now(timezone.utc)
    for candidate in keys:
        public = candidate.public_key() if isinstance(candidate, TrustedKey) else candidate
        if key_id(public) != signature.key_id:
            continue
        if isinstance(candidate, TrustedKey) and not candidate.valid_on(now):
            raise PackError(f"The pack is signed with key {signature.key_id}, which expired on "
                            f"{candidate.valid_until}.")
        try:
            public.verify(base64.urlsafe_b64decode(signature.sig + "=="), _MANIFEST_CONTEXT + manifest.canonical_bytes())
        except (InvalidSignature, ValueError, binascii.Error):
            raise PackError(f"The signature of key {signature.key_id} does not match: the pack was changed.") from None
        return signature.key_id
    raise PackError(f"The pack is signed with key {signature.key_id}, which is not one of the trusted keys.")


# --- Zip files -------------------------------------------------------------------------------------------

def _zip_info(name: str, mode: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))  # same content, same zip bytes
    info.external_attr = (stat.S_IFREG | mode) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def write_pack(root: Path, manifest: PackManifest, signature: Optional[PackSignature] = None) -> bytes:
    """The zip bytes of a pack of the files below root, which must match the manifest exactly."""
    verify_tree(root, manifest)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(_zip_info(MANIFEST_NAME, 0o644),
                    json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True, ensure_ascii=False))
        if signature is not None:
            zf.writestr(_zip_info(SIGNATURE_NAME, 0o644), signature.model_dump_json())
        for f in manifest.files:
            data = (Path(root) / f.path).read_bytes()
            if hashlib.sha256(data).hexdigest() != f.sha256:  # changed since verify_tree
                raise PackError(f"{f.path} changed while the pack was written.")
            zf.writestr(_zip_info(f.path, f.mode), data)
    return buffer.getvalue()


@dataclass(frozen=True)
class PackContents:
    manifest: PackManifest
    signature: Optional[PackSignature]
    signer: Optional[str]  # key id of a verified signature


def read_pack(source: Union[Path, bytes], dest: Path, *,
              keys: Optional[Iterable[Union[TrustedKey, Ed25519PublicKey]]] = None,
              now: Optional[datetime] = None) -> PackContents:
    """Extracts a pack into dest (new or empty) and verifies it: the zip holds the manifest, the optional
    signature and exactly the listed files, each with its size and sha256. With keys, a valid signature by one
    of them is required. On any problem dest is left without pack files and PackError is raised."""
    dest = Path(dest)
    if os.path.lexists(dest) and (not dest.is_dir() or dest.is_symlink() or any(dest.iterdir())):
        raise PackError(f"{dest} must be a new or empty folder.")
    try:
        zf = zipfile.ZipFile(io.BytesIO(source) if isinstance(source, bytes) else source)
    except (OSError, zipfile.BadZipFile) as e:
        raise PackError(f"Not a pack (zip file): {e}") from None
    with zf:
        infos = {i.filename: i for i in zf.infolist()}
        if len(infos) != len(zf.infolist()):
            raise PackError("The pack lists a file twice.")
        manifest, signature = _read_meta(zf, infos)
        listed = {f.path: f for f in manifest.files}
        problems = [f"{n}: not in the manifest" for n in sorted(set(infos) - set(listed) - RESERVED_NAMES)]
        problems += [f"{n}: missing" for n in sorted(set(listed) - set(infos))]
        for name, info in infos.items():
            if stat.S_ISLNK(info.external_attr >> 16):
                problems.append(f"{name}: symbolic link")
            elif name in listed and info.file_size != listed[name].size:
                problems.append(f"{name}: size differs from the manifest")
        if problems:
            raise PackError("The pack does not match its manifest:\n- " + "\n- ".join(problems[:20]))
        signer = None
        if keys is not None:
            if signature is None:
                raise PackError("The pack is not signed.")
            signer = verify_manifest(manifest, signature, keys, now=now)
        created = not os.path.lexists(dest)
        dest.mkdir(parents=True, exist_ok=True)
        try:
            for f in manifest.files:
                data = zf.read(f.path)  # reads at most the declared size, and checks the CRC
                if hashlib.sha256(data).hexdigest() != f.sha256:
                    raise PackError(f"{f.path}: content differs from the manifest")
                target = dest.joinpath(*f.path.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
                with os.fdopen(os.open(target, flags, f.mode), "wb") as out:
                    out.write(data)
                if os.name == "posix":
                    os.chmod(target, f.mode)  # the umask may have removed bits
            verify_tree(dest, manifest)
        except BaseException as e:
            # dest was new or empty: nothing of the pack stays behind.
            if created:
                shutil.rmtree(dest, ignore_errors=True)
            else:
                for child in dest.iterdir():
                    if child.is_dir() and not child.is_symlink():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink()
            if isinstance(e, Exception) and not isinstance(e, PackError):  # damaged zip, disk full, ...
                raise PackError(f"The pack cannot be extracted: {e}") from None
            raise
    return PackContents(manifest, signature, signer)


def _read_meta(zf: zipfile.ZipFile, infos) -> Tuple[PackManifest, Optional[PackSignature]]:
    def load(name: str, model):
        info = infos.get(name)
        if info is None:
            return None
        if info.file_size > 16 * 1024 * 1024:
            raise PackError(f"{name} is too large.")
        try:
            return model.model_validate_json(zf.read(name))
        except ValidationError as e:
            raise PackError(f"{name} is invalid: {e}") from None
        except Exception as e:  # damaged, encrypted or unsupported zip member
            raise PackError(f"{name} cannot be read: {e}") from None

    manifest = load(MANIFEST_NAME, PackManifest)
    if manifest is None:
        raise PackError(f"The pack has no {MANIFEST_NAME}.")
    return manifest, load(SIGNATURE_NAME, PackSignature)


# --- Encryption for private sharing -----------------------------------------------------------------------
#
# Format v1 (in the style of age, https://age-encryption.org/v1):
#
#   line 1  b"lcf-encrypted-pack/v1\n"
#   line 2  header, canonical JSON (sorted keys, no whitespace) + b"\n":
#           {"v": 1, "sender": "ed25519:<b64>", "payload_salt": "<b64, 16 bytes>",
#            "recipients": [{"type": "x25519", "epk": "<b64, 32 bytes>", "body": "<b64, 48 bytes>"},
#                           {"type": "scrypt", "salt": "<b64, 16 bytes>", "log_n": 17, "body": "<b64, 48 bytes>"}]}
#   line 3  b"mac " + b64(HMAC-SHA256(HKDF(file_key, info="lcf-pack/v1 header"), line 1 + line 2)) + b"\n"
#   body    ChaCha20-Poly1305(HKDF(file_key, salt=payload_salt, info="lcf-pack/v1 payload"), nonce=0,
#                             plaintext=pack, aad=line 1 + line 2 + line 3)
#   tail    64 bytes: the sender's Ed25519 signature of "lcf-encrypted-pack/v1 signature\n" + everything before
#
#   file_key       32 random bytes per file; every key derived from it is used once, so the zero nonce is safe.
#   x25519 stanza  ephemeral key pair e per recipient R; wrap key = HKDF-SHA256(X25519(e, R), salt=e.pub || R.pub,
#                  info="lcf-pack/v1 x25519"); body = ChaCha20-Poly1305(wrap key, nonce=0, file_key). The
#                  stanza does not name the recipient; a recipient tries every stanza.
#   scrypt stanza  wrap key = scrypt(NFC passphrase, salt="lcf-pack/v1 scrypt" || salt, n=2**log_n, r=8, p=1);
#                  body = ChaCha20-Poly1305(wrap key, nonce=0, file_key).
#
# Reading checks, in this order, before any plaintext is returned: the signature (any change to the header, the
# recipient list or the body fails it), the sender (when trusted senders are given), the wrapped file key, the
# header MAC and the payload tag. The sender's public key is visible in the header.

ENCRYPTED_MAGIC = b"lcf-encrypted-pack/v1\n"
SCRYPT_LOG_N = 17
MIN_PASSPHRASE = 12
MAX_RECIPIENTS = 64
_INFO_X25519 = b"lcf-pack/v1 x25519"
_INFO_HEADER = b"lcf-pack/v1 header"
_INFO_PAYLOAD = b"lcf-pack/v1 payload"
_SCRYPT_LABEL = b"lcf-pack/v1 scrypt"
_ZERO_NONCE = bytes(12)
_MAX_HEADER = 64 * 1024


class _X25519Stanza(_Strict):
    type: Literal["x25519"]
    epk: str
    body: str


class _ScryptStanza(_Strict):
    type: Literal["scrypt"]
    salt: str
    # Bounded on reading too: a crafted file must not make the reader allocate gigabytes.
    log_n: int = Field(ge=SCRYPT_LOG_N, le=20)
    body: str


class _EncryptedHeader(_Strict):
    v: Literal[1]
    sender: str
    payload_salt: str
    recipients: List[Union[_X25519Stanza, _ScryptStanza]] = Field(min_length=1, max_length=MAX_RECIPIENTS)


@dataclass(frozen=True)
class DecryptedPack:
    data: bytes
    sender: str           # public_key_text() of the sender's Ed25519 key
    sender_id: str        # its key_id()
    sender_trusted: bool  # True only when it was one of the trusted senders the caller passed


def _hkdf(ikm: bytes, salt: bytes, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=info).derive(ikm)


def _scrypt_key(passphrase: str, salt: bytes, log_n: int) -> bytes:
    secret = unicodedata.normalize("NFC", passphrase).encode("utf-8")
    return Scrypt(salt=_SCRYPT_LABEL + salt, length=32, n=2 ** log_n, r=8, p=1).derive(secret)


def _x25519_wrap_key(shared: bytes, epk: bytes, recipient: bytes) -> bytes:
    return _hkdf(shared, epk + recipient, _INFO_X25519)


def _header_mac(file_key: bytes, signed_header: bytes) -> bytes:
    return hmac.new(_hkdf(file_key, b"", _INFO_HEADER), signed_header, hashlib.sha256).digest()


def encrypt_pack(data: bytes, *, sender: Ed25519PrivateKey, recipients: Sequence[X25519PublicKey] = (),
                 passphrase: Optional[str] = None) -> bytes:
    """Encrypts pack bytes to X25519 recipients and/or a passphrase and signs the result with the sender's key."""
    recipients = list(recipients)
    if not recipients and passphrase is None:
        raise ValueError("Encrypt to at least one recipient key or a passphrase.")
    if len(recipients) + (passphrase is not None) > MAX_RECIPIENTS:
        raise ValueError(f"At most {MAX_RECIPIENTS} recipients.")
    if passphrase is not None and len(passphrase) < MIN_PASSPHRASE:
        raise ValueError(f"The passphrase needs at least {MIN_PASSPHRASE} characters.")
    if len(data) > MAX_PACK_BYTES:
        raise PackError("The pack is larger than a pack may be.")
    file_key = os.urandom(32)
    stanzas = []
    for recipient in recipients:
        ephemeral = X25519PrivateKey.generate()
        epk, rpk = _raw_public(ephemeral.public_key()), _raw_public(recipient)
        try:
            shared = ephemeral.exchange(recipient)
        except ValueError:  # a low-order point, never a real key
            raise PackError(f"The recipient key {key_id(recipient)} is not usable.") from None
        wrap = _x25519_wrap_key(shared, epk, rpk)
        stanzas.append({"type": "x25519", "epk": _b64e(epk),
                        "body": _b64e(ChaCha20Poly1305(wrap).encrypt(_ZERO_NONCE, file_key, None))})
    if passphrase is not None:
        salt = os.urandom(16)
        wrap = _scrypt_key(passphrase, salt, SCRYPT_LOG_N)
        stanzas.append({"type": "scrypt", "salt": _b64e(salt), "log_n": SCRYPT_LOG_N,
                        "body": _b64e(ChaCha20Poly1305(wrap).encrypt(_ZERO_NONCE, file_key, None))})
    payload_salt = os.urandom(16)
    header = {"v": 1, "sender": public_key_text(sender.public_key()), "payload_salt": _b64e(payload_salt),
              "recipients": stanzas}
    head = ENCRYPTED_MAGIC + _canonical(header) + b"\n"
    head += b"mac " + _b64e(_header_mac(file_key, head)).encode("ascii") + b"\n"
    body = ChaCha20Poly1305(_hkdf(file_key, payload_salt, _INFO_PAYLOAD)).encrypt(_ZERO_NONCE, data, head)
    return head + body + sender.sign(_ENCRYPTED_CONTEXT + head + body)


def _parse_encrypted(blob: bytes) -> Tuple[_EncryptedHeader, bytes, bytes, bytes, bytes, bytes]:
    """(header, lines 1-2, lines 1-3, mac, body, signature) of an encrypted pack."""
    if not blob.startswith(ENCRYPTED_MAGIC):
        raise PackError("Not an encrypted pack.")
    if len(blob) > MAX_PACK_BYTES + _MAX_HEADER + 1024:
        raise PackError("The encrypted pack is larger than a pack may be.")
    start = len(ENCRYPTED_MAGIC)
    end = blob.find(b"\n", start, start + _MAX_HEADER)
    mac_end = blob.find(b"\n", end + 1, end + 64) if end > 0 else -1
    if end < 0 or mac_end < 0 or blob[end + 1:end + 5] != b"mac ":
        raise PackError("The encrypted pack is damaged (header).")
    line = blob[start:end]
    try:
        header = _EncryptedHeader.model_validate_json(line)
        mac = _b64d(blob[end + 5:mac_end].decode("ascii"), 32)
    except (ValidationError, ValueError, UnicodeDecodeError):
        raise PackError("The encrypted pack is damaged (header).") from None
    # One encoding only: a re-encoded header is a changed header.
    if _canonical(header.model_dump(mode="json")) != line:
        raise PackError("The encrypted pack is damaged (header not canonical).")
    body, signature = blob[mac_end + 1:-64], blob[-64:]
    if len(body) < 16:
        raise PackError("The encrypted pack is damaged (too short).")
    return header, blob[:end + 1], blob[:mac_end + 1], mac, body, signature


def _unwrap_file_key(header: _EncryptedHeader, identity: Optional[X25519PrivateKey],
                     passphrase: Optional[str]) -> Optional[bytes]:
    for stanza in header.recipients:
        try:
            if isinstance(stanza, _X25519Stanza) and identity is not None:
                epk = _b64d(stanza.epk, 32)
                shared = identity.exchange(X25519PublicKey.from_public_bytes(epk))
                wrap = _x25519_wrap_key(shared, epk, _raw_public(identity.public_key()))
            elif isinstance(stanza, _ScryptStanza) and passphrase is not None:
                wrap = _scrypt_key(passphrase, _b64d(stanza.salt, 16), stanza.log_n)
            else:
                continue
            return ChaCha20Poly1305(wrap).decrypt(_ZERO_NONCE, _b64d(stanza.body, 48), None)
        except (InvalidTag, ValueError):
            continue  # another recipient's stanza
    return None


def decrypt_pack(blob: bytes, *, identity: Optional[X25519PrivateKey] = None, passphrase: Optional[str] = None,
                 trusted_senders: Optional[Iterable[Union[TrustedKey, Ed25519PublicKey]]] = None) -> DecryptedPack:
    """Checks and decrypts an encrypted pack. With trusted_senders, a pack from any other sender is refused;
    without, the caller must show sender_id to the user (never silently trusted)."""
    header, signed_header, head, mac, body, signature = _parse_encrypted(blob)
    try:
        sender = load_public_key(header.sender)
    except (PackError, ValueError):
        raise PackError("The encrypted pack is damaged (sender key).") from None
    if not isinstance(sender, Ed25519PublicKey):
        raise PackError("The encrypted pack is damaged (sender key).")
    try:
        sender.verify(signature, _ENCRYPTED_CONTEXT + blob[:-64])
    except InvalidSignature:
        raise PackError(f"The signature of sender {key_id(sender)} does not match: the pack was changed.") from None
    trusted = False
    if trusted_senders is not None:
        known = {_raw_public(k.public_key() if isinstance(k, TrustedKey) else k) for k in trusted_senders}
        if _raw_public(sender) not in known:
            raise PackError(f"The pack was sent by key {key_id(sender)}, which is not one of the trusted senders.")
        trusted = True
    file_key = _unwrap_file_key(header, identity, passphrase)
    if file_key is None:
        raise PackError("This pack is not encrypted to your key, or the passphrase is wrong.")
    if not hmac.compare_digest(_header_mac(file_key, signed_header), mac):
        raise PackError("The header of the encrypted pack was changed.")
    try:
        data = ChaCha20Poly1305(_hkdf(file_key, _b64d(header.payload_salt, 16), _INFO_PAYLOAD)).decrypt(
            _ZERO_NONCE, body, head)
    except (InvalidTag, ValueError):
        raise PackError("The content of the encrypted pack was changed.") from None
    return DecryptedPack(data, header.sender, key_id(sender), trusted)


# --- The user's own keys ----------------------------------------------------------------------------------

class UserKeys:
    """The user's key pairs for packs: Ed25519 signs, X25519 receives encrypted packs. Created on first use and
    kept only in the encrypted secrets store (master key in the OS keyring)."""

    SIGNING_SECRET = "community_signing_key"
    ENCRYPTION_SECRET = "community_encryption_key"

    def __init__(self, secrets_manager):
        self._secrets = secrets_manager

    def signing_key(self) -> Ed25519PrivateKey:
        return self._load_or_create(self.SIGNING_SECRET, Ed25519PrivateKey)

    def encryption_key(self) -> X25519PrivateKey:
        return self._load_or_create(self.ENCRYPTION_SECRET, X25519PrivateKey)

    def export_public(self) -> str:
        """The public keys as text to give to others (they are not secret)."""
        signing = self.signing_key().public_key()
        return (f"# LCF public keys, key id {key_id(signing)}. Not secret: give them to people who send you\n"
                f"# encrypted packs or verify your signed packs.\n"
                f"{public_key_text(signing)}\n{public_key_text(self.encryption_key().public_key())}\n")

    def _load_or_create(self, name: str, cls):
        if self._secrets is None:
            raise PackError("The secrets store is not available, so keys cannot be kept safely.")
        stored = self._secrets.get_secret(name)
        if not stored and name in self._secrets.list_secrets():
            # Stored but unreadable (another master key, a damaged entry): never overwritten by a new key.
            raise PackError(f"The stored key '{name}' cannot be decrypted; it is not replaced automatically.")
        if not stored:
            if not self._secrets.set_secret(name, _b64e(_raw_private(cls.generate()))):
                raise PackError("The new key could not be saved in the secrets store.")
            stored = self._secrets.get_secret(name)  # another process may have saved one first
            if not stored:
                raise PackError("The new key could not be read back from the secrets store.")
        try:
            return cls.from_private_bytes(_b64d(stored, 32))
        except ValueError:
            # Never replaced silently: packs signed or encrypted for the old key depend on it.
            raise PackError(f"The stored key '{name}' is damaged. Restore the secrets store from a backup, or "
                            f"delete the key with 'llm-cli secrets' to create a new identity.") from None


# --- Privacy scrub ----------------------------------------------------------------------------------------

# Adapted from rag4ll rag4ll/mojo/redact.py (MIT, (c) 2026 Smilez1985, https://github.com/Smilez1985/rag4ll).
# Extended: Windows and macOS home folders, IPv6, MAC and e-mail addresses (LCF's privacy detectors), the host
# name and the user name, and LCF's secret patterns.
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b")
_HOME_DIRS = re.compile(r"(?:(?<![\w.])/home/|(?<![\w.])/Users/|\b[A-Za-z]:\\Users\\|\b[A-Za-z]:/Users/)"
                        r"[^/\\\s\"'<>|:]+")
_GENERIC_PLACEHOLDER = re.compile(r"<(IP|MAC|EMAIL|USER|HOST)_[0-9a-f]{8}>")
# Account and machine names that are ordinary words in documentation; replacing them would damage the text.
_COMMON_NAMES = {"root", "user", "admin", "administrator", "ubuntu", "debian", "pi", "runner", "docker", "build",
                 "localhost", "default", "guest", "test"}


def _replace_name(text: str, name: str, placeholder: str) -> str:
    if len(name) < 3 or name.casefold() in _COMMON_NAMES:
        return text
    return re.sub(rf"(?<!\w){re.escape(name)}(?!\w)", placeholder, text, flags=re.IGNORECASE)


def scrub(text: str) -> str:
    """Removes what identifies this machine or its user before text leaves it, irreversibly: home folders
    become '~', IP and MAC addresses '<ip>'/'<mac>', e-mail addresses '<email>', JWTs '<jwt>', the host name
    '<host>', the user name '<user>', and API keys are redacted. 127.0.0.1 and other loopback addresses stay."""
    import getpass
    import socket

    from orchestrator.llm.privacy import PrivacyFilter

    if not text:
        return ""
    text = _JWT.sub("<jwt>", text)
    home = str(Path.home())
    if len(home) > 3:
        text = re.sub(re.escape(home) + r"(?![\w.-])", "~", text)
    text = _HOME_DIRS.sub("~", text)
    # Secrets, IPv4/IPv6, MAC and e-mail addresses and user@host logins (the mapping back is dropped).
    text, _ = PrivacyFilter("BALANCED").sanitize(text)
    text = _GENERIC_PLACEHOLDER.sub(lambda m: f"<{m.group(1).lower()}>", text)
    try:
        host = socket.gethostname()
    except OSError:
        host = ""
    for name in sorted({host, host.split(".")[0]} - {""}, key=len, reverse=True):
        text = _replace_name(text, name, "<host>")
    try:
        user = getpass.getuser()
    except (KeyError, OSError):
        user = ""
    return _replace_name(text, user, "<user>") if user else text
