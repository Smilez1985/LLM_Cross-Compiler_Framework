#!/usr/bin/env python3
"""
Community packs: manifest, Ed25519 signatures, privacy scrub, and encryption for private sharing.

A pack is a zip file:
  pack.json   manifest: schema_version, kind, id, version, author, license, lcf_min_version, created (UTC)
              and files [{path, sha256, size, mode}]; a knowledge pack also names the topic or the target its pages
              belong to (topic or target; DECISIONS round 23 item 1). A pack made before that names neither: the
              field is absent from its manifest and from the bytes its signature covers, so it still verifies
  pack.sig    optional author signature: {"v": 1, "alg": "Ed25519", "key_id": "...", "sig": "<base64url>",
              "pubkey": "<base64>"} (the public key is optional; it lets anyone check the signature, and the
              author's public key is pinned on first use when the pack is installed)
  <files>     exactly the files the manifest lists, nothing else

Public packs are signed, not encrypted: for public data, authenticity and integrity are what matter. A pack
shared privately (with your own machines or a team) is encrypted in the age format with rage, the Rust
implementation of age (through pyrage), and signed by the sender, so recipients know who sent it (see
encrypt_pack).

Each user has an Ed25519 key (signing) and an age identity (receiving encrypted packs), kept in the encrypted
secrets store (UserKeys); the private keys are never written to disk unencrypted.
"""

import base64
import binascii
import contextlib
import hashlib
import io
import json
import os
import re
import stat
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Literal, Optional, Sequence, Tuple, Union, get_args

import pyrage
import yaml
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from orchestrator import __version__
from orchestrator.security.paths import ACCOUNT_NAME, HOME_ROOT
from orchestrator.utils.helpers import calculate_file_checksum
from orchestrator.utils.next_steps import WithSteps
from orchestrator.utils.atomic import remove_tree

MANIFEST_NAME = "pack.json"
SIGNATURE_NAME = "pack.sig"
# Written next to an installed pack (source, trust level, digests); never part of a pack or its fingerprint.
PROVENANCE_NAME = ".lcf-provenance.json"
RESERVED_NAMES = frozenset({MANIFEST_NAME, SIGNATURE_NAME, PROVENANCE_NAME})
Kind = Literal["target", "recipe", "knowledge"]
PACK_KINDS = get_args(Kind)
# Packs carry scripts, recipes and text, never model weights.
MAX_PACK_BYTES = 512 * 1024 * 1024
MAX_FILES = 10_000
KEYS_FILE = Path(__file__).resolve().parents[2] / "configs" / "community_keys.yml"

# Field patterns, shared with the community index (community_manager.py).
SHA256_PATTERN = r"^[0-9a-f]{64}$"
ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
VERSION_PATTERN = r"^\d{1,4}\.\d{1,4}\.\d{1,4}(?:[-+][0-9A-Za-z.-]{1,32})?$"
LICENSE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9.+-]{0,63}$"
UTC_TIME_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
# The topic or target a knowledge pack belongs to: also the name of its folder below community/knowledge/, so a plain
# name that every file system stores as written (no trailing '.', which Windows drops, and no device name Windows
# reserves).
TOPIC_PATTERN = r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,62}[A-Za-z0-9_-])?$"
_WINDOWS_DEVICE = re.compile(r"^(?:con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\..*)?$", re.IGNORECASE)
# The manifest fields a pack made before topics did not have: left out of the manifest when unset (see as_json)
_OPTIONAL_FIELDS = ("topic", "target")
_KEY_ID = r"^[0-9a-f]{16}$"
# Characters Windows cannot store or treats specially, control characters and the backslash.
_BAD_PATH_CHARS = re.compile(r'[\x00-\x1f\x7f\\:*?"<>|]')

# Domain separation: a signature made for one purpose never verifies for another.
_MANIFEST_CONTEXT = b"lcf-pack-manifest/v1\n"
_ENCRYPTED_CONTEXT = b"lcf-encrypted-pack/v2 signature\n"
_INDEX_CONTEXT = b"lcf-community-index/v1\n"


class PackError(WithSteps, Exception):
    """A pack, key or signature problem; the message is meant for the user."""


class Strict(BaseModel):
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


def check_topic(value: str) -> str:
    """A knowledge topic or target name (TOPIC_PATTERN, no Windows device name); raises ValueError otherwise."""
    if not isinstance(value, str) or not re.fullmatch(TOPIC_PATTERN, value) or _WINDOWS_DEVICE.match(value):
        raise ValueError(f"not a topic or target name: {value!r} (letters, digits, '.', '_' and '-', up to 64 "
                         "characters, starting with a letter or digit and not ending in '.')")
    return value


def check_belongs(kind: str, topic: Optional[str], target: Optional[str]) -> None:
    """The rule of a pack's topic or target (its manifest and its index entry): one of them at most, and only a
    knowledge pack names one; raises ValueError otherwise."""
    if topic is not None and target is not None:
        raise ValueError("a knowledge pack names its topic or its target, not both")
    if (topic is not None or target is not None) and kind != "knowledge":
        raise ValueError(f"only a knowledge pack names a topic or target, not a {kind} pack")


def belongs_text(kind: str, topic: Optional[str], target: Optional[str]) -> str:
    """Where a pack's knowledge belongs, for the index entry's details and the maintainer's review: "topic X",
    "target X", for a knowledge pack that names neither (made before topics) that it takes its id as its topic, and ""
    for a target or recipe pack."""
    if target:
        return f"target {target}"
    if topic:
        return f"topic {topic}"
    return "no topic or target named (made before topics: installs under its id)" if kind == "knowledge" else ""


class PackFile(Strict):
    path: str
    sha256: str = Field(pattern=SHA256_PATTERN)
    size: int = Field(ge=0, le=MAX_PACK_BYTES)
    # Masked on creation: no setuid/setgid/sticky bits and no group/world write ever travel with a pack.
    mode: Literal[0o644, 0o755] = 0o644

    @field_validator("path")
    @classmethod
    def _relpath(cls, value: str) -> str:
        return check_relpath(value)


class PackManifest(Strict):
    schema_version: Literal[1] = 1
    kind: Kind
    id: str = Field(pattern=ID_PATTERN)
    version: str = Field(pattern=VERSION_PATTERN)
    author: str = Field(min_length=1, max_length=100, pattern=r"^[^\x00-\x1f\x7f]+$")
    license: str = Field(pattern=LICENSE_PATTERN)
    lcf_min_version: str = Field(default=__version__, pattern=VERSION_PATTERN)
    created: str = Field(pattern=UTC_TIME_PATTERN)
    files: List[PackFile] = Field(default_factory=list, max_length=MAX_FILES)
    # A knowledge pack: the topic or the target its pages belong to (one of them; an export sets it, a pack made
    # before topics names neither and installs with the topic taken from its id, after the person confirmed it).
    topic: Optional[str] = None
    target: Optional[str] = None

    @field_validator("topic", "target")
    @classmethod
    def _topic_name(cls, value: Optional[str]) -> Optional[str]:
        return None if value is None else check_topic(value)

    @model_validator(mode="after")
    def _unique_sorted(self):
        check_belongs(self.kind, self.topic, self.target)
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

    def as_json(self) -> dict:
        """The manifest as pack.json holds it: a field packs made before it did not have (topic, target) only when
        it is set, so the manifest and the signature of such a pack stay what they were."""
        data = self.model_dump(mode="json")
        for name in _OPTIONAL_FIELDS:
            if data.get(name) is None:
                data.pop(name, None)
        return data

    def canonical_bytes(self) -> bytes:
        """The bytes a signature covers: sorted keys, no whitespace, UTF-8 (of as_json)."""
        return _canonical(self.as_json())

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


def list_files(root: Path) -> List[Tuple[str, Path]]:
    """(relative path, path) of every file below root; links and special files raise PackError (see _scan)."""
    return [(rel, path) for rel, path, _ in _scan(root)]


def _pack_files(root: Path) -> List[PackFile]:
    return [PackFile(path=rel, sha256=calculate_file_checksum(path), size=st.st_size,
                     mode=0o755 if st.st_mode & 0o111 else 0o644)
            for rel, path, st in _scan(root)]


def tree_sha256(root: Path) -> str:
    """PackManifest.content_sha256() of the files below root, without the pack metadata."""
    return _files_digest(_pack_files(root))


def local_sha256(root: Path, manifest: PackManifest) -> Optional[str]:
    """The manifest's content digest with each file's mode as the file system below root reports it, or None when
    root does not hold exactly the manifest's files. Windows keeps no execute bit, so a pack's 0o755 scripts read as
    0o644 there and tree_sha256 of the installed pack never gives the manifest's digest; this one it gives as long as
    every file keeps the manifest's sha256 (contents are not read here: tree_sha256 reads them)."""
    modes = {rel: 0o755 if st.st_mode & 0o111 else 0o644 for rel, _path, st in _scan(root)}
    if set(modes) != {f.path for f in manifest.files}:
        return None
    return _files_digest(f.model_copy(update={"mode": modes[f.path]}) for f in manifest.files)


def build_manifest(root: Path, *, kind: str, id: str, version: str, author: str, license: str,
                   created: Optional[str] = None, lcf_min_version: Optional[str] = None,
                   topic: Optional[str] = None, target: Optional[str] = None) -> PackManifest:
    """The manifest of every file below root (see _scan); a knowledge pack's topic or target."""
    files = _pack_files(root)
    try:
        return PackManifest(kind=kind, id=id, version=version, author=author, license=license,
                            created=created or utc_now(), lcf_min_version=lcf_min_version or __version__,
                            files=files, topic=topic, target=target)
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


def key_id(key: Ed25519PublicKey) -> str:
    """Short identifier of a public key: the first 16 hex characters of the sha256 of its raw bytes."""
    return hashlib.sha256(key.public_bytes_raw()).hexdigest()[:16]


def public_key_text(key: Ed25519PublicKey) -> str:
    """'ed25519:<base64>': the text form of a signing key."""
    return f"ed25519:{_b64e(key.public_bytes_raw())}"


# Signing keys as public_key_text() writes them, and age recipients (bech32: no 1, b, i or o after "age1").
_PUBLIC_KEY_TOKEN = re.compile(r"\bed25519:([A-Za-z0-9+/]{43}=)|\b(age1[02-9ac-hj-np-z]{58})\b")


def parse_public_keys(text: str) -> List[Union[Ed25519PublicKey, "pyrage.x25519.Recipient"]]:
    """Every public key in a text block: 'ed25519:' signing keys and 'age1...' recipients, as
    UserKeys.export_public() prints them on another machine (or age-keygen and rage-keygen do)."""
    keys = []
    for m in _PUBLIC_KEY_TOKEN.finditer(str(text)):
        try:
            keys.append(Ed25519PublicKey.from_public_bytes(_b64d(m.group(1), 32)) if m.group(1)
                        else pyrage.x25519.Recipient.from_str(m.group(2)))
        except (ValueError, pyrage.RecipientError):
            raise PackError(f"Not a valid public key: {m.group(0)[:24]}...") from None
    return keys


class KeyFileError(PackError):
    """A key file that gives no public key: `code` not_text (not a text file) or no_keys (no usable key of the kind
    asked for in any of the files); `file` names the file for not_text."""

    def __init__(self, message: str, code: str, file: str = ""):
        super().__init__(message)
        self.code, self.file = code, file


def read_public_key_files(files: Iterable[Union[str, Path]], kind: type) -> list:
    """The public keys of `kind` (Ed25519PublicKey: senders of encrypted packs; pyrage.x25519.Recipient: recipients)
    in the key files, as `llm-cli zoo keys` or the Community Hub's My public keys... wrote them on another computer.
    KeyFileError for a file that is not text, or when files were given but none holds a key of that kind; PackError
    for a key that is not valid."""
    files = list(files)
    keys = []
    for f in files:
        try:
            text = Path(f).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise KeyFileError(f"{f} is not a text file with public keys (see 'llm-cli zoo keys').", "not_text",
                               str(f)) from None
        keys += [k for k in parse_public_keys(text) if isinstance(k, kind)]
    if files and not keys:
        raise KeyFileError("The key files contain no usable public key (see 'llm-cli zoo keys').", "no_keys")
    return keys


class PackSignature(Strict):
    v: Literal[1] = 1
    alg: Literal["Ed25519"] = "Ed25519"
    key_id: str = Field(pattern=_KEY_ID)
    sig: str = Field(pattern=r"^[A-Za-z0-9_-]{86}$")  # 64 bytes, base64url without padding
    pubkey: Optional[str] = Field(default=None, pattern=r"^[A-Za-z0-9+/]{43}=$")

    def embedded_key(self) -> Optional[Ed25519PublicKey]:
        """The signer's public key carried in the signature, checked against key_id (None if absent)."""
        if self.pubkey is None:
            return None
        key = Ed25519PublicKey.from_public_bytes(_b64d(self.pubkey, 32))
        if key_id(key) != self.key_id:
            raise PackError(f"The signature names key {self.key_id} but carries another public key.")
        return key


class TrustedKey(Strict):
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


class _KeysFile(Strict):
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


def _sign(payload: bytes, private_key: Ed25519PrivateKey) -> PackSignature:
    public = private_key.public_key()
    return PackSignature(key_id=key_id(public), pubkey=_b64e(public.public_bytes_raw()),
                         sig=base64.urlsafe_b64encode(private_key.sign(payload)).decode("ascii").rstrip("="))


def _verify(payload: bytes, signature: PackSignature, keys: Iterable[Union[TrustedKey, Ed25519PublicKey]],
            now: Optional[datetime], what: str) -> str:
    now = now or datetime.now(timezone.utc)
    for candidate in keys:
        public = candidate.public_key() if isinstance(candidate, TrustedKey) else candidate
        if key_id(public) != signature.key_id:
            continue
        if isinstance(candidate, TrustedKey) and not candidate.valid_on(now):
            raise PackError(f"The {what} is signed with key {signature.key_id}, which expired on "
                            f"{candidate.valid_until}.")
        try:
            public.verify(base64.urlsafe_b64decode(signature.sig + "=="), payload)
        except (InvalidSignature, ValueError, binascii.Error):
            raise PackError(f"The signature of key {signature.key_id} does not match: the {what} was changed.") from None
        return signature.key_id
    raise PackError(f"The {what} is signed with key {signature.key_id}, which is not one of the trusted keys.")


def sign_manifest(manifest: PackManifest, private_key: Ed25519PrivateKey) -> PackSignature:
    return _sign(_MANIFEST_CONTEXT + manifest.canonical_bytes(), private_key)


def verify_manifest(manifest: PackManifest, signature: PackSignature,
                    keys: Iterable[Union[TrustedKey, Ed25519PublicKey]], *, now: Optional[datetime] = None) -> str:
    """Returns the key id of the valid signature; raises PackError for an unknown or expired key or a signature
    that does not match (a changed manifest, a wrong key)."""
    return _verify(_MANIFEST_CONTEXT + manifest.canonical_bytes(), signature, keys, now, "pack")


def sign_index(index_bytes: bytes, private_key: Ed25519PrivateKey) -> PackSignature:
    """Signature of the community index file (index.json.sig), over its exact bytes."""
    return _sign(_INDEX_CONTEXT + index_bytes, private_key)


def verify_index(index_bytes: bytes, signature: PackSignature,
                 keys: Iterable[Union[TrustedKey, Ed25519PublicKey]], *, now: Optional[datetime] = None) -> str:
    return _verify(_INDEX_CONTEXT + index_bytes, signature, keys, now, "community index")


# --- Zip files -------------------------------------------------------------------------------------------

def _zip_info(name: str, mode: int = 0o644, compress_type: int = zipfile.ZIP_DEFLATED) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))  # same content, same zip bytes
    info.external_attr = (stat.S_IFREG | mode) << 16
    info.compress_type = compress_type
    return info


def write_pack(root: Path, manifest: PackManifest, signature: Optional[PackSignature] = None) -> bytes:
    """The zip bytes of a pack of the files below root, which must match the manifest exactly."""
    verify_tree(root, manifest)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(_zip_info(MANIFEST_NAME, 0o644),
                    json.dumps(manifest.as_json(), indent=2, sort_keys=True, ensure_ascii=False))
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
        manifest = _load_member(zf, infos, MANIFEST_NAME, PackManifest)
        if manifest is None:
            raise PackError(f"The pack has no {MANIFEST_NAME}.")
        signature = _load_member(zf, infos, SIGNATURE_NAME, PackSignature)
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
                remove_tree(dest)
            else:
                for child in dest.iterdir():
                    if child.is_dir() and not child.is_symlink():
                        remove_tree(child)
                    else:
                        child.unlink()
            if isinstance(e, Exception) and not isinstance(e, PackError):  # damaged zip, disk full, ...
                raise PackError(f"The pack cannot be extracted: {e}") from None
            raise
    return PackContents(manifest, signature, signer)


def _load_member(zf: zipfile.ZipFile, infos, name: str, model):
    """A JSON member of a zip as a model, None if absent; PackError if it is too large or invalid."""
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


def read_manifest(data: bytes) -> PackManifest:
    """The manifest of pack bytes, for routing a file before it is checked in quarantine."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            manifest = _load_member(zf, {i.filename: i for i in zf.infolist()}, MANIFEST_NAME, PackManifest)
    except (OSError, zipfile.BadZipFile) as e:
        raise PackError(f"Not a community pack: {e}") from None
    if manifest is None:
        raise PackError(f"Not a community pack: it has no {MANIFEST_NAME}.")
    return manifest


# --- Encryption for private sharing -----------------------------------------------------------------------
#
# An encrypted pack (.lcfpack) is a zip file with two members:
#   pack.zip.age      the pack in the age format (https://age-encryption.org/v1), written by rage (the Rust
#                     implementation of age) through pyrage: to X25519 recipients ("age1...") or to one passphrase
#                     (scrypt), never both (age keeps a passphrase stanza alone)
#   pack.zip.age.sig  the sender's Ed25519 signature (the pack.sig format, with the public key) of
#                     "lcf-encrypted-pack/v2 signature\n" followed by the bytes of pack.zip.age
# The age and rage tools open it too, with the passphrase or an identity file of their own (the identity UserKeys
# creates never leaves the secrets store): unzip it, then `age -d -i key.txt -o pack.zip pack.zip.age`.
#
# Reading checks, before anything is decrypted: the zip layout and sizes, the age header (at most _MAX_STANZAS
# stanzas, because rage's header processing grows with the square of their number; a scrypt stanza must be alone
# and its work factor at most 2^MAX_SCRYPT_LOG_N: pyrage's own limit follows the machine's speed and lets through
# factors that need more memory than the machine has, which aborts the process), the signature, and the sender
# when trusted senders are given.

AGE_NAME = "pack.zip.age"
AGE_SIG_NAME = AGE_NAME + ".sig"
MIN_PASSPHRASE = 12
MAX_RECIPIENTS = 64
MAX_SCRYPT_LOG_N = 20  # 1 GiB of memory (scrypt with r = 8 needs 2^log_n KiB)
_MAX_STANZAS = MAX_RECIPIENTS + 4  # rage adds a grease stanza to every file
_OLD_ENCRYPTED_MAGIC = b"lcf-encrypted-pack/v1\n"  # the format of development versions, no longer read
_AGE_MAGIC = b"age-encryption.org/v1\n"
_MAX_AGE_HEADER = 64 * 1024
_MAX_ENCRYPTED = MAX_PACK_BYTES + 1024 * 1024  # age adds its header and 16 bytes per 64 KiB


def is_encrypted_pack(source: Union[Path, bytes]) -> bool:
    """True for an encrypted pack, also one in the old development format (decrypt_pack says why it is
    refused)."""
    try:
        with io.BytesIO(source) if isinstance(source, bytes) else open(source, "rb") as f:
            if f.read(len(_OLD_ENCRYPTED_MAGIC)) == _OLD_ENCRYPTED_MAGIC:
                return True
            with zipfile.ZipFile(f) as zf:
                names = zf.namelist()
                return AGE_NAME in names and MANIFEST_NAME not in names  # a pack with a manifest is a plain pack
    except (OSError, ValueError, EOFError, zipfile.BadZipFile):
        return False


def needs_passphrase(source: Union[Path, bytes]) -> bool:
    """True for an encrypted pack protected with a passphrase (not one encrypted to keys), read from its age header
    without decrypting anything; False for a plain pack and for one that cannot be read (opening it says why)."""
    if not is_encrypted_pack(source):
        return False
    try:
        with io.BytesIO(source) if isinstance(source, bytes) else open(source, "rb") as f:
            with zipfile.ZipFile(f) as zf:
                info = zf.getinfo(AGE_NAME)
                if info.file_size > _MAX_ENCRYPTED:
                    return False
                with zf.open(info) as member:
                    head = member.read(_MAX_AGE_HEADER)
        return _age_header(head)
    except (OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile, PackError):
        return False


def _age_header(blob: bytes) -> bool:
    """Checks the text header of an age file without decrypting anything; True for a passphrase file."""
    end = blob.find(b"\n--- ", 0, _MAX_AGE_HEADER)
    if not blob.startswith(_AGE_MAGIC) or end < 0:
        raise PackError("The encrypted pack is damaged (no age header).")
    stanzas = [line.split(b" ")[1:] for line in blob[:end].split(b"\n") if line.startswith(b"-> ")]
    if len(stanzas) > _MAX_STANZAS:
        raise PackError(f"The encrypted pack is damaged (too many recipient stanzas; at most {MAX_RECIPIENTS} "
                        "recipients).")
    scrypt = [args for args in stanzas if args[0] == b"scrypt"]
    if not scrypt:
        return False
    if len(stanzas) > 1 or len(scrypt[0]) != 3 or not re.fullmatch(rb"[1-9][0-9]?", scrypt[0][2]):
        raise PackError("The encrypted pack is damaged (a passphrase stanza must be the only one).")
    if int(scrypt[0][2]) > MAX_SCRYPT_LOG_N:
        raise PackError(f"The passphrase of this pack uses the scrypt work factor 2^{int(scrypt[0][2])}; at most "
                        f"2^{MAX_SCRYPT_LOG_N} (1 GiB of memory) is accepted.")
    return True


def encrypt_pack(data: bytes, *, sender: Ed25519PrivateKey, recipients: Sequence["pyrage.x25519.Recipient"] = (),
                 passphrase: Optional[str] = None) -> bytes:
    """Encrypts pack bytes in the age format to recipients' age keys or to a passphrase (one of the two) and signs
    the result with the sender's key."""
    recipients = list(recipients)
    if bool(recipients) == (passphrase is not None):
        raise ValueError("Encrypt to recipient keys or to a passphrase (one of the two; age does not mix them).")
    if len(recipients) > MAX_RECIPIENTS:
        raise ValueError(f"At most {MAX_RECIPIENTS} recipients.")
    if passphrase is not None and len(passphrase) < MIN_PASSPHRASE:
        raise ValueError(f"The passphrase needs at least {MIN_PASSPHRASE} characters.")
    if len(data) > MAX_PACK_BYTES:
        raise PackError("The pack is larger than a pack may be.")
    try:
        blob = pyrage.passphrase.encrypt(data, passphrase) if passphrase is not None else \
            pyrage.encrypt(data, recipients)
    except (pyrage.EncryptError, TypeError) as e:  # TypeError: something that is not an age recipient
        raise PackError(f"The pack cannot be encrypted: {e}") from None
    _age_header(blob)  # scrypt picks its work factor by this computer's speed; readers accept it up to the limit
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(_zip_info(AGE_NAME, compress_type=zipfile.ZIP_STORED), blob)  # encrypted data does not shrink
        zf.writestr(_zip_info(AGE_SIG_NAME), _sign(_ENCRYPTED_CONTEXT + blob, sender).model_dump_json())
    return buffer.getvalue()


def decrypt_pack(blob: bytes, *, identity: Optional["pyrage.x25519.Identity"] = None,
                 passphrase: Optional[str] = None,
                 trusted_senders: Optional[Iterable[Union[TrustedKey, Ed25519PublicKey]]] = None,
                 identity_loader: Optional[Callable[[], "pyrage.x25519.Identity"]] = None) -> Tuple[bytes, str]:
    """Checks and decrypts an encrypted pack: (pack bytes, the sender's key id). With trusted_senders, a pack from
    any other sender is refused; without, the caller must show the sender's key id (never silently trusted).
    identity_loader gives the identity when none is passed; it is called only for a pack encrypted to keys whose
    checks passed."""
    if blob.startswith(_OLD_ENCRYPTED_MAGIC):
        raise PackError("This encrypted pack was made by a development version in a format that is no longer read; "
                        "ask the sender to export it again.")
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            infos = {i.filename: i for i in zf.infolist()}
            if len(zf.infolist()) != 2 or set(infos) != {AGE_NAME, AGE_SIG_NAME} \
                    or infos[AGE_NAME].file_size > _MAX_ENCRYPTED:
                raise PackError("Not an encrypted pack.")
            signature, age = _load_member(zf, infos, AGE_SIG_NAME, PackSignature), zf.read(AGE_NAME)
    except PackError:
        raise
    except Exception as e:  # not a zip, a damaged or an unsupported zip member
        raise PackError(f"The encrypted pack cannot be read: {e}") from None
    sender = signature.embedded_key()
    if sender is None:
        raise PackError("The encrypted pack does not include its sender's public key.")
    scrypt = _age_header(age)
    _verify(_ENCRYPTED_CONTEXT + age, signature, [sender], None, "encrypted pack")
    if trusted_senders is not None and sender.public_bytes_raw() not in {
            (k.public_key() if isinstance(k, TrustedKey) else k).public_bytes_raw() for k in trusted_senders}:
        raise PackError(f"The pack was sent by key {signature.key_id}, which is not one of the trusted senders.")
    if scrypt and passphrase is None:
        raise PackError("This pack is protected with a passphrase (llm-cli zoo install --file FILE --passphrase).")
    if not scrypt and identity is None and identity_loader is not None:
        identity = identity_loader()
    if not scrypt and identity is None:
        raise PackError("This pack is encrypted to keys, not to a passphrase.")
    try:
        data = pyrage.passphrase.decrypt(age, passphrase) if scrypt else pyrage.decrypt(age, [identity])
    except pyrage.DecryptError as e:
        if "Excessive work" in str(e):  # rage's own limit: 2^4 above what this computer does in a second
            raise PackError("This computer is too slow for the scrypt work factor of the passphrase; ask the sender "
                            "to encrypt the pack to your key (llm-cli zoo keys) instead.") from None
        raise PackError("This pack is not encrypted to your key, or the passphrase is wrong.") from None
    return data, signature.key_id


# --- The user's own keys ----------------------------------------------------------------------------------

class UserKeys:
    """The user's keys for packs: an Ed25519 key signs, an age identity (X25519) receives encrypted packs.
    Created on first use and kept only in the encrypted secrets store (master key in the OS keyring)."""

    SIGNING_SECRET = "community_signing_key"
    AGE_SECRET = "community_age_identity"

    def __init__(self, secrets_manager):
        self._secrets = secrets_manager

    def signing_key(self) -> Ed25519PrivateKey:
        return self._key(self.SIGNING_SECRET, lambda: _b64e(os.urandom(32)),
                         lambda text: Ed25519PrivateKey.from_private_bytes(_b64d(text, 32)),
                         "import base64, os; print(base64.b64encode(os.urandom(32)).decode())")

    def age_identity(self) -> "pyrage.x25519.Identity":
        return self._key(self.AGE_SECRET, lambda: str(pyrage.x25519.Identity.generate()),
                         pyrage.x25519.Identity.from_str, "import pyrage; print(pyrage.x25519.Identity.generate())")

    def export_public(self) -> str:
        """The public keys as text to give to others (they are not secret)."""
        signing = self.signing_key().public_key()
        return (f"# LCF public keys, key id {key_id(signing)}. Not secret: give them to people who send you\n"
                f"# encrypted packs (llm-cli zoo export --to) or check your signed packs.\n"
                f"{self.age_identity().to_public()}\n{public_key_text(signing)}\n")

    def _key(self, name: str, new, parse, by_hand: str):
        """The stored key `name`, created with new() on first use; by_hand is Python code that prints a new one."""
        if self._secrets is None:
            raise PackError("The secrets store is not available, so keys cannot be kept safely.")
        stored = self._secrets.get_secret(name)
        if not stored:
            # Read again under the store's lock for this entry: two processes must never both create a key.
            lock = getattr(self._secrets, "credential_lock", None)
            with lock(name) if callable(lock) else contextlib.nullcontext():
                stored = self._secrets.get_secret(name)
                if not stored and name in self._secrets.list_secrets():
                    # Stored but unreadable (another master key, a damaged entry): never overwritten by a new key.
                    raise PackError(f"The stored key '{name}' cannot be decrypted; it is not replaced automatically.")
                if not stored and not self._secrets.set_secret(name, new()):
                    raise PackError("The new key could not be saved in the secrets store.")
                stored = stored or self._secrets.get_secret(name)
                if not stored:
                    raise PackError("The new key could not be read back from the secrets store.")
        try:
            return parse(str(stored).strip())
        except (ValueError, pyrage.IdentityError):
            # Never replaced silently: packs signed or encrypted for the old key depend on it.
            raise PackError(f"The stored key '{name}' is damaged. Restore the secrets store from a backup, or start "
                            f"over with a new key (packs signed or encrypted for the old one then no longer match): "
                            f'python -c "{by_hand}" | llm-cli secrets set {name} --with-token') from None


# --- base images of a target pack (moved here from tools/tools_community.py by chain c1-imports) -------------

def _logical_lines(text: str) -> List[str]:
    lines, current = [], ""
    for raw in text.splitlines():
        stripped = raw.strip()
        if not current and (not stripped or stripped.startswith("#")):
            continue
        if stripped.endswith("\\"):
            current += stripped[:-1] + " "
            continue
        lines.append(current + stripped)
        current = ""
    if current:
        lines.append(current)
    return lines


def dockerfile_bases(text: str) -> List[str]:
    """The base images a Dockerfile's FROM lines name, and the images COPY/ADD --from and RUN --mount=...,from= pull,
    in order: build stages named with AS are left out, `scratch` too; a build argument with a default declared before
    the first FROM is filled in, any other stays as written."""
    args: Dict[str, str] = {}
    stages, images = set(), []
    seen_from = False
    for line in _logical_lines(text):
        word, _, rest = line.partition(" ")
        word = word.upper()
        if word == "ARG" and not seen_from:
            name, eq, value = rest.strip().partition("=")
            if eq:
                args[name.strip()] = value.strip().strip('"').strip("'")
        if word in ("COPY", "ADD", "RUN"):
            # COPY --from=<image> and RUN --mount=...,from=<image> pull a prebuilt image just like FROM does (a stage
            # name or a stage number is not an image)
            for flag in (t for t in rest.split() if t.startswith("--")):
                name, _, value = flag[2:].partition("=")
                sources = [value] if name.lower() == "from" else [
                    part.partition("=")[2] for part in value.split(",")
                    if name.lower() == "mount" and part.partition("=")[0].strip().lower() == "from"]
                for source in sources:
                    source = source.strip().strip('"').strip("'")
                    if source and not source.isdigit() and source.lower() not in stages and source not in images:
                        images.append(source)
            continue
        if word != "FROM":
            continue
        seen_from = True
        tokens = [t for t in rest.split() if not t.startswith("--")]
        if not tokens:
            continue
        image = re.sub(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", lambda m: args.get(m.group(1), m.group(0)), tokens[0])
        if len(tokens) >= 3 and tokens[1].upper() == "AS":
            stages.add(tokens[2].lower())
        if image.lower() in stages or image.lower() == "scratch":
            continue
        if image not in images:
            images.append(image)
    return images


def pack_base_images(folder: Path) -> List[Tuple[str, str]]:
    """(file, image) for every base image of every Dockerfile in a pack folder (Dockerfile, *.dockerfile,
    Dockerfile.*, Containerfile), never following a link. In the order of the relative POSIX paths, the same on every
    system (a Windows path sorts without case and with backslashes, so its own order put docker/ before Dockerfile)."""
    found = []
    folder = Path(folder)
    for path in sorted(folder.rglob("*"), key=lambda p: p.relative_to(folder).as_posix()):
        name = path.name.lower()
        if path.is_symlink() or not path.is_file():
            continue
        if not (name in ("dockerfile", "containerfile") or name.endswith(".dockerfile")
                or name.startswith("dockerfile.")):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = path.relative_to(folder).as_posix()
        found += [(rel, image) for image in dockerfile_bases(text)]
    return found


def base_image_line(image: str) -> str:
    """One base image, plainly (DECISIONS round 3): registry, image, tag, digest, and whether it is a Docker Official
    Image or a prebuilt image from another registry."""
    from orchestrator.utils.image_ref import split_image
    if "$" in image:
        return (f"{image}: names a build argument; the image is decided when the target is built. The image build runs "
                "with network access on this computer, outside the build sandbox.")
    info = split_image(image)
    parts = [f"{image}: registry {info['registry']}, image {info['repository']}"
             + (f", tag {info['tag']}" if info["tag"] else "")]
    parts.append(f"digest {info['digest']}" if info["digest"] else
                 "not pinned by digest: its publisher can later point the tag at another image")
    parts.append("Docker Official Image" if info["official"] else
                 f"other registry: {info['registry']}; prebuilt image made by its publisher, not checked by the framework")
    parts.append("the image build runs with network access on this computer, outside the build sandbox")
    return "; ".join(parts) + "."


# --- Privacy scrub ----------------------------------------------------------------------------------------

# Adapted from rag4ll rag4ll/mojo/redact.py (MIT, (c) 2026 Smilez1985, https://github.com/Smilez1985/rag4ll).
# Extended: Windows and macOS home folders, IPv6, MAC and e-mail addresses (LCF's privacy detectors), the host
# name and the user name, and LCF's secret patterns.
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\b")
_HOME_DIRS = re.compile(rf"({HOME_ROOT}([/\\]))({ACCOUNT_NAME})")   # the shared patterns (security/paths.py)
# Text the detectors must not touch is shielded with private-use characters while they run: image digests
# (name@sha256:..., which the validators require) are no user@host logins, and default accounts are no persons.
# A scrubbed git@ remote or digest would break the script it is in.
_SHIELD = {"/": "", "\\": "", "@": ""}
_UNSHIELD = str.maketrans({v: k for k, v in _SHIELD.items()})
# The '@' of image digests and of git@<forge>: SSH remotes (a service account, not a person).
_KEEP_AT = re.compile(r"@(?=sha(?:256|384|512):[0-9a-f]{32})|(?<=\bgit)@(?=[\w.-]+\.[A-Za-z]{2,}:)")
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
    home = Path.home()
    if len(str(home)) > 3 and home.name.casefold() not in _COMMON_NAMES:  # /root is a path of every container
        text = re.sub(re.escape(str(home)) + r"(?![\w.-])", "~", text)
    text = _HOME_DIRS.sub(lambda m: m.group(1)[:-1] + _SHIELD[m.group(2)] + m.group(3)
                          if m.group(3).casefold() in _COMMON_NAMES else "~", text)
    text = _KEEP_AT.sub(_SHIELD["@"], text)
    # Secrets, IPv4/IPv6, MAC and e-mail addresses and user@host logins (the mapping back is dropped).
    text, _ = PrivacyFilter("BALANCED").sanitize(text)
    text = text.translate(_UNSHIELD)
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
