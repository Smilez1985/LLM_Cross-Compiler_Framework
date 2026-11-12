#!/usr/bin/env python3
"""
The board preload of distribution packages (docs/DESIGN.md, Deployment to boards: "A board installs the system
packages its components need..."): on a computer with a network, exactly the packages asked for and their dependencies
for one board's distribution, release and architecture, checked against the distribution's signed package lists, in
one archive in the preload format of the installer and the build (scripts/preload_lock.py: llmcf-preload/ with
manifest.txt, here of kind "packages", and SHA256SUMS). Standard library only (it runs inside the preload.sh and
preload.ps1 llmcf writes, on Linux, macOS and Windows, with any Python 3.8 or newer); the build's preload carries
its image's distribution packages in the same format through the same functions (its section packages/, made by
gather() from make_build_recipe(): each build stage's packages resolved against what its base image has).

    deb_preload.py pack --spec RECIPE.json [--out FOLDER]
        fetches and checks everything and writes llmcf-preload-packages-SCOPE-RELEASE-ARCH-RECIPE.tar.gz into FOLDER;
        prints the archive's path and its sha256.
    deb_preload.py check --archive FILE --out FOLDER [--sha256 HEX]
        checks a board preload (the archive's entries, SHA256SUMS, every signature with the pinned keys, every list and
        every .deb again) and unpacks it into FOLDER; prints its manifest.
    deb_preload.py write-scripts --spec RECIPE.json --out FOLDER
        writes FOLDER/preload.sh and FOLDER/preload.ps1, each standing alone (the recipe and this program inside).

The recipe (make_recipe): scope (a bundle id or "board"), distribution (debian, ubuntu or raspbian, the board's
os-release ID), release (its codename), arch (dpkg's architecture), archives (more archives the board uses:
raspberrypi, armbian), packages (NAME or NAME=VERSION) and installed (what the board had installed at its last
reading, NAME -> VERSION; null for a board never read).

The interface the build's preload reuses: sources_for (the archive suites of a release), fetch_suite (a suite's
Release, its signature checked with the pinned keys, and its package lists checked against it), resolve (the packages
with their Depends and Pre-Depends), gather (the packages/ folder: lists, .deb files, keys), verify_folder (that folder
checked again, as the PC does at take-in), gpgv_check (the same signatures through gpgv against pinned_keyring, a
keyring of the pinned keys only), parse_os_release and installed_from_status (what a base image is and has).

Trust: the distributions' archive keys are fetched from their own addresses and taken only when their fingerprint is
one of PINNED_KEYS; the signature check is this program's own OpenPGP code (version 4 signatures, RSA with SHA-256,
SHA-384, SHA-512 or SHA-224 over the data; a signing subkey only with its binding signature by the pinned key).
Release.gpg (the detached signature) is used, never InRelease; a Release without the uncompressed package list's
SHA-256 is refused. Validity dates are not checked (an offline board's clock decides nothing).
Exit codes: 0 done, 1 failed (the reason is said), 2 wrong use.
"""

import argparse
import base64
import gzip
import hashlib
import json
import lzma
import os
import re
import sys
import tarfile
import tempfile
import time
from pathlib import Path

FORMAT = "1"
TOP = "llmcf-preload"
KIND = "packages"
FOLDER = "packages"

# The distributions' archive keys: the fingerprint of each primary key (pinned here; a key fetched from the network is
# taken only when its fingerprint is one of these) and the address it is published at.
PINNED_KEYS = {
    "debian-11": ("1F89983E0081FDE018F3CC9673A4F27B8DD47936", "https://ftp-master.debian.org/keys/archive-key-11.asc"),
    "debian-12": ("B8B80B5B623EAB6AD8775C45B7C5D7D6350947F8", "https://ftp-master.debian.org/keys/archive-key-12.asc"),
    "debian-13": ("04B54C3CDCA79751B16BC6B5225629DF75B188BD", "https://ftp-master.debian.org/keys/archive-key-13.asc"),
    "debian-11-security": ("AC530D520F2F3269F5E98313A48449044AAD5C5D",
                           "https://ftp-master.debian.org/keys/archive-key-11-security.asc"),
    "debian-12-security": ("05AB90340C0C5E797F44A8C8254CF3B5AEC0A8F0",
                           "https://ftp-master.debian.org/keys/archive-key-12-security.asc"),
    "debian-13-security": ("5E04A1E3223A19A20706E20F9904613D4CCE68C6",
                           "https://ftp-master.debian.org/keys/archive-key-13-security.asc"),
    "ubuntu-2012": ("790BC7277767219C42C86F933B4FE6ACC0B21F32",
                    "https://archive.ubuntu.com/ubuntu/project/ubuntu-archive-keyring.gpg"),
    "ubuntu-2018": ("F6ECB3762474EDA9D21B7022871920D1991BC93C",
                    "https://archive.ubuntu.com/ubuntu/project/ubuntu-archive-keyring.gpg"),
    "raspberrypi": ("CF8A1AF502A2AA2D763BAE7E82B129927FA3303E",
                    "https://archive.raspberrypi.com/debian/raspberrypi.gpg.key"),
    "raspbian": ("A0DA38D0D76E8B5D638872819165938D90FDDD2E", "https://archive.raspbian.org/raspbian.public.key"),
    "armbian": ("8CFA83D13EB2181EEF5843E41EB30FAF236099FE", "https://apt.armbian.com/armbian.key"),
}
DEBIAN_KEYS = ("debian-11", "debian-12", "debian-13")
DEBIAN_SECURITY_KEYS = ("debian-11-security", "debian-12-security", "debian-13-security")
UBUNTU_KEYS = ("ubuntu-2012", "ubuntu-2018")
DISTRIBUTIONS = ("debian", "ubuntu", "raspbian")
ARCHIVES = ("raspberrypi", "armbian")
UBUNTU_PRIMARY_ARCHES = ("amd64", "i386")

RE_PACKAGE = re.compile(r"^[a-z0-9][a-z0-9+.-]{1,63}(?:=[0-9][A-Za-z0-9.+~:-]{0,63})?$", re.ASCII)
RE_WORD = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$", re.ASCII)
RE_SCOPE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,47}$", re.ASCII)
RE_VERSION = re.compile(r"^[0-9][A-Za-z0-9.+~:-]{0,63}$", re.ASCII)
RE_POOL = re.compile(r"^pool/(?:[A-Za-z0-9._+~%-]+/)+[A-Za-z0-9._+~%-]+\.deb$", re.ASCII)
MAX_PACKAGES = 16
MAX_RESOLVED = 2000
# A build image's stages: more packages per stage than a board's component asks for (a toolchain)
MAX_BUILD_PACKAGES = 64
MAX_STAGES = 8


class PreloadError(Exception):
    pass


def say(message: str) -> None:
    print(f"preload: {message}", flush=True)


# --- the recipe --------------------------------------------------------------------------------------------------------

def make_recipe(scope, distribution, release, arch, packages, archives=(), installed=None) -> dict:
    """The recipe of one board preload, every value checked (PreloadError names the one that is not)."""
    if not RE_SCOPE.fullmatch(scope or ""):
        raise PreloadError(f"not a scope (a bundle id or board): {scope!r}")
    if distribution not in DISTRIBUTIONS:
        raise PreloadError(f"the distribution is {', '.join(DISTRIBUTIONS)}, not {distribution!r}")
    for what, value in (("release", release), ("architecture", arch)):
        if not RE_WORD.fullmatch(value or ""):
            raise PreloadError(f"not a {what}: {value!r}")
    archives = sorted(set(archives or ()))
    if any(a not in ARCHIVES for a in archives):
        raise PreloadError(f"more archives are {', '.join(ARCHIVES)}")
    packages = list(packages)
    if not 1 <= len(packages) <= MAX_PACKAGES or any(not RE_PACKAGE.fullmatch(p) for p in packages):
        raise PreloadError(f"1 to {MAX_PACKAGES} packages, each NAME or NAME=VERSION by the distribution's rules")
    if len({p.split("=", 1)[0] for p in packages}) != len(packages):
        raise PreloadError("a package twice")
    if installed is not None:
        installed = {str(k): str(v) for k, v in dict(installed).items()
                     if re.fullmatch(r"[a-z0-9][a-z0-9+.-]{0,63}", str(k)) and RE_VERSION.fullmatch(str(v))}
    return {"format": FORMAT, "scope": scope, "distribution": distribution, "release": release, "arch": arch,
            "archives": archives, "packages": packages, "installed": installed}


def _installed(installed):
    if installed is None:
        return None
    return {str(k): str(v) for k, v in dict(installed).items()
            if re.fullmatch(r"[a-z0-9][a-z0-9+.-]{0,63}", str(k)) and RE_VERSION.fullmatch(str(v))}


def make_build_recipe(distribution, release, arch, stages) -> dict:
    """The recipe of a build image's distribution packages: per stage its packages (NAME or NAME=VERSION, as its
    Dockerfile names them) and what its base image has installed (NAME -> VERSION; an empty dict fetches the whole
    closure). gather() resolves each stage on its own and fetches the union."""
    if distribution not in ("debian", "ubuntu"):
        raise PreloadError(f"the build image's distribution is debian or ubuntu, not {distribution!r}")
    for what, value in (("release", release), ("architecture", arch)):
        if not RE_WORD.fullmatch(value or ""):
            raise PreloadError(f"not a {what}: {value!r}")
    stages = list(stages or [])
    if not 1 <= len(stages) <= MAX_STAGES:
        raise PreloadError(f"1 to {MAX_STAGES} build stages")
    out = []
    for stage in stages:
        packages = list(stage.get("packages") or [])
        if not 1 <= len(packages) <= MAX_BUILD_PACKAGES or any(not RE_PACKAGE.fullmatch(p) for p in packages):
            raise PreloadError(f"1 to {MAX_BUILD_PACKAGES} packages per build stage, each NAME or NAME=VERSION by the "
                               "distribution's rules")
        if len({p.split("=", 1)[0] for p in packages}) != len(packages):
            raise PreloadError("a package twice in one build stage")
        out.append({"packages": packages, "installed": _installed(stage.get("installed")) or {}})
    return {"format": FORMAT, "scope": "build", "distribution": distribution, "release": release, "arch": arch,
            "archives": [], "packages": sorted({p for s in out for p in s["packages"]}), "installed": None,
            "stages": out}


def recipe_sha256(recipe: dict) -> str:
    return hashlib.sha256(json.dumps(recipe, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def archive_name(recipe: dict) -> str:
    return (f"{TOP}-{KIND}-{recipe['scope']}-{recipe['release']}-{recipe['arch']}-"
            f"{recipe_sha256(recipe)[:12]}.tar.gz")


def read_recipe(path: Path) -> dict:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return make_recipe(raw["scope"], raw["distribution"], raw["release"], raw["arch"], raw["packages"],
                           raw.get("archives") or (), raw.get("installed"))
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise PreloadError(f"{path} is not a board preload's recipe ({error})")


# --- the archive suites of a release -----------------------------------------------------------------------------------

class Source:
    """One archive suite: its address, suite, components and the pinned keys that may sign its Release."""

    def __init__(self, url, suite, components, keys):
        self.url, self.suite, self.components, self.keys = url.rstrip("/"), suite, tuple(components), tuple(keys)

    def line(self) -> str:
        return " ".join((self.url, self.suite, *self.components))


def sources_for(distribution: str, release: str, arch: str, archives=()) -> list:
    """The archive suites a board of `distribution` `release` on `arch` installs from (as its default sources name
    them), plus the extra `archives` it uses. https where the archive serves it (Raspbian's does not): the signatures
    decide what is trusted, https only reaches through the proxies a computer with a network usually has."""
    out = []
    if distribution == "debian":
        base = "https://deb.debian.org/debian"
        out += [Source(base, release, ["main"], DEBIAN_KEYS),
                Source(base, f"{release}-updates", ["main"], DEBIAN_KEYS),
                Source("https://deb.debian.org/debian-security", f"{release}-security", ["main"],
                       DEBIAN_SECURITY_KEYS)]
    elif distribution == "ubuntu":
        base = ("https://archive.ubuntu.com/ubuntu" if arch in UBUNTU_PRIMARY_ARCHES
                else "https://ports.ubuntu.com/ubuntu-ports")
        out += [Source(base, s, ["main", "universe"], UBUNTU_KEYS)
                for s in (release, f"{release}-updates", f"{release}-security")]
    elif distribution == "raspbian":
        out.append(Source("http://raspbian.raspberrypi.com/raspbian", release, ["main", "contrib", "non-free", "rpi"],
                          ("raspbian",)))
    if "raspberrypi" in archives:
        out.append(Source("https://archive.raspberrypi.com/debian", release, ["main"], ("raspberrypi",)))
    if "armbian" in archives:
        out.append(Source("https://apt.armbian.com", release, ["main", f"{release}-utils"], ("armbian",)))
    return out


# --- OpenPGP: just enough to check a distribution's Release.gpg -------------------------------------------------------

HASHES = {2: "sha1", 8: "sha256", 9: "sha384", 10: "sha512", 11: "sha224"}
DIGEST_INFO = {
    "sha1": bytes.fromhex("3021300906052b0e03021a05000414"),
    "sha224": bytes.fromhex("302d300d06096086480165030402040500041c"),
    "sha256": bytes.fromhex("3031300d060960864801650304020105000420"),
    "sha384": bytes.fromhex("3041300d060960864801650304020205000430"),
    "sha512": bytes.fromhex("3051300d060960864801650304020305000440"),
}
# SHA-1 only for a subkey's binding signature (as apt's gpgv still takes it), never over the data
DATA_HASHES = ("sha224", "sha256", "sha384", "sha512")


def dearmor(data: bytes) -> bytes:
    """The binary packets of an armored block (its checksum line left out), or the bytes as they are."""
    text = data.decode("ascii", "replace")
    if "-----BEGIN PGP" not in text:
        return data
    body, started, header_done = [], False, False
    for line in text.splitlines():
        if line.startswith("-----BEGIN PGP"):
            started, header_done = True, False
            continue
        if line.startswith("-----END PGP"):
            break
        if not started:
            continue
        if not header_done:
            if not line.strip():
                header_done = True
            elif ":" not in line:
                header_done = True
                body.append(line.strip())
            continue
        if line.startswith("="):
            continue
        body.append(line.strip())
    try:
        return base64.b64decode("".join(body), validate=False)
    except ValueError as error:
        raise PreloadError(f"an armored key or signature does not decode ({error})")


def packets(data: bytes):
    """(tag, body) of each OpenPGP packet (old and new format headers)."""
    i, n = 0, len(data)
    while i < n:
        head = data[i]
        if not head & 0x80:
            raise PreloadError("not OpenPGP data")
        if head & 0x40:
            tag = head & 0x3F
            i += 1
            first = data[i]
            if first < 192:
                length, i = first, i + 1
            elif first < 224:
                length, i = ((first - 192) << 8) + data[i + 1] + 192, i + 2
            elif first == 255:
                length, i = int.from_bytes(data[i + 1:i + 5], "big"), i + 5
            else:
                raise PreloadError("partial OpenPGP packets are not taken")
        else:
            tag, kind = (head >> 2) & 0x0F, head & 0x03
            i += 1
            if kind == 3:
                length = n - i
            else:
                size = (1, 2, 4)[kind]
                length, i = int.from_bytes(data[i:i + size], "big"), i + size
        yield tag, data[i:i + length]
        i += length


def _mpi(body: bytes, at: int):
    bits = int.from_bytes(body[at:at + 2], "big")
    size = (bits + 7) // 8
    return int.from_bytes(body[at + 2:at + 2 + size], "big"), at + 2 + size


class Key:
    """A version 4 RSA key (primary or subkey) with its fingerprint."""

    def __init__(self, body: bytes):
        if len(body) < 6 or body[0] != 4:
            raise ValueError("not a version 4 key")
        self.body, self.algo = body, body[5]
        # A version 4 fingerprint is SHA-1 by definition (RFC 4880, 12.2), as gpg computes it. Matching a pinned one
        # takes a second preimage, not a collision, and the board checks every Release again with its own gpgv.
        self.fingerprint = hashlib.sha1(  # nosec B324 - the OpenPGP v4 fingerprint, see above
            b"\x99" + len(body).to_bytes(2, "big") + body).hexdigest().upper()
        self.n = self.e = None
        if self.algo in (1, 3):
            self.n, at = _mpi(body, 6)
            self.e, _ = _mpi(body, at)

    @property
    def key_id(self) -> str:
        return self.fingerprint[-16:]

    def framed(self) -> bytes:
        return b"\x99" + len(self.body).to_bytes(2, "big") + self.body


class Signature:
    """A version 4 signature packet: type, algorithms, the hashed part, issuer and its value."""

    def __init__(self, body: bytes):
        if len(body) < 10 or body[0] != 4:
            raise ValueError("not a version 4 signature")
        self.type, self.algo, self.hash_algo = body[1], body[2], body[3]
        hashed_len = int.from_bytes(body[4:6], "big")
        self.hashed_part = body[:6 + hashed_len]
        at = 6 + hashed_len
        unhashed_len = int.from_bytes(body[at:at + 2], "big")
        unhashed = body[at + 2:at + 2 + unhashed_len]
        at += 2 + unhashed_len
        self.left16 = body[at:at + 2]
        self.value, _ = _mpi(body, at + 2) if self.algo in (1, 3) else (None, 0)
        self.issuers, self.key_flags = set(), None
        for sub in (self._subpackets(body[6:6 + hashed_len]), self._subpackets(unhashed)):
            for kind, data in sub:
                if kind == 16 and len(data) == 8:
                    self.issuers.add(data.hex().upper())
                elif kind == 33 and len(data) == 21 and data[0] == 4:
                    self.issuers.add(data[1:].hex().upper())
                elif kind == 27 and data:
                    self.key_flags = data[0]

    @staticmethod
    def _subpackets(data: bytes):
        i = 0
        while i < len(data):
            first = data[i]
            if first < 192:
                length, i = first, i + 1
            elif first < 255:
                length, i = ((first - 192) << 8) + data[i + 1] + 192, i + 2
            else:
                length, i = int.from_bytes(data[i + 1:i + 5], "big"), i + 5
            if length == 0:
                break
            yield data[i] & 0x7F, data[i + 1:i + length]
            i += length

    def by(self, key: Key) -> bool:
        return key.fingerprint in self.issuers or key.key_id in self.issuers

    def verify(self, key: Key, data: bytes, hashes=DATA_HASHES) -> bool:
        """Whether this signature over `data` is valid with `key` (RSA, PKCS#1 v1.5)."""
        name = HASHES.get(self.hash_algo)
        if name not in hashes or key.n is None or self.value is None or self.algo not in (1, 3):
            return False
        trailer = b"\x04\xff" + len(self.hashed_part).to_bytes(4, "big")
        digest = hashlib.new(name, data + self.hashed_part + trailer).digest()
        if digest[:2] != self.left16:
            return False
        k = (key.n.bit_length() + 7) // 8
        tail = DIGEST_INFO[name] + digest
        if k < len(tail) + 11:
            return False
        want = b"\x00\x01" + b"\xff" * (k - len(tail) - 3) + b"\x00" + tail
        return pow(self.value, key.e, key.n).to_bytes(k, "big") == want


def signing_keys(data: bytes, fingerprint: str) -> list:
    """The keys that may sign for the pinned primary key `fingerprint` in a key file: the primary itself and each
    subkey whose binding signature by it verifies (and that may sign data). Empty when the file has no such key."""
    primary, subkey, out = None, None, []
    for tag, body in packets(dearmor(data)):
        if tag == 6:
            try:
                key = Key(body)
            except ValueError:
                primary = None
                continue
            primary = key if key.fingerprint == fingerprint.upper() else None
            subkey = None
            if primary is not None:
                out.append(primary)
        elif tag == 14 and primary is not None:
            try:
                subkey = Key(body)
            except ValueError:
                subkey = None
        elif tag == 2 and primary is not None and subkey is not None:
            try:
                sig = Signature(body)
            except ValueError:
                continue
            if sig.type == 0x18 and sig.by(primary) and \
                    sig.verify(primary, primary.framed() + subkey.framed(), hashes=tuple(DIGEST_INFO)):
                if sig.key_flags is None or sig.key_flags & 0x02:
                    out.append(subkey)
    return out


def signed_by(data: bytes, signature: bytes, keys: list) -> str:
    """The fingerprint of the first of `keys` that made a valid signature over `data` in `signature` ("" none)."""
    for tag, body in packets(dearmor(signature)):
        if tag != 2:
            continue
        try:
            sig = Signature(body)
        except ValueError:
            continue
        if sig.type not in (0x00, 0x01):
            continue
        for key in keys:
            if sig.by(key) and sig.verify(key, data):
                return key.fingerprint
    return ""


# --- the archive's lists ---------------------------------------------------------------------------------------------

def _fetch(url: str, timeout: float = 120.0) -> bytes:
    import urllib.request
    import urllib.error
    try:
        with urllib.request.urlopen(url, timeout=timeout) as answer:  # nosec B310 - the signatures decide
            return answer.read()
    except urllib.error.HTTPError as error:
        raise PreloadError(f"{url}: {error.code} {error.reason}")
    except OSError as error:
        raise PreloadError(f"{url}: {getattr(error, 'reason', error)}")


def fetch_key(name: str, fetch=_fetch) -> tuple:
    """(the key file's bytes, its signing keys) of the pinned key `name`, refused unless it holds that key."""
    fingerprint, url = PINNED_KEYS[name]
    data = fetch(url)
    keys = signing_keys(data, fingerprint)
    if not keys:
        raise PreloadError(f"{url} does not hold the archive key {fingerprint} that llmcf pins for {name}; nothing "
                           "from it is trusted")
    return data, keys


def parse_release(text: str) -> dict:
    """A Release file: codename, suite, architectures, and SHA256 -> {path: (sha256, size)}."""
    out = {"codename": "", "suite": "", "architectures": [], "sha256": {}}
    in_sha = False
    for line in text.splitlines():
        if line[:1] in (" ", "\t"):
            if in_sha:
                parts = line.split()
                if len(parts) == 3 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]) and parts[1].isdigit():
                    out["sha256"][parts[2]] = (parts[0].lower(), int(parts[1]))
            continue
        in_sha = False
        key, sep, value = line.partition(":")
        if not sep:
            continue
        value = value.strip()
        if key == "Codename":
            out["codename"] = value
        elif key == "Suite":
            out["suite"] = value
        elif key == "Architectures":
            out["architectures"] = value.split()
        elif key == "SHA256":
            in_sha = True
    return out


def of_release(release: dict, codename: str) -> bool:
    def fits(s):
        return s == codename or (s.startswith(codename + "-") and len(s) > len(codename) + 1)
    return fits(release["codename"]) or fits(release["suite"])


def stanzas(text: str):
    """The stanzas of a Packages list as dicts (continuation lines joined to their field)."""
    fields, last = {}, None
    for line in text.split("\n"):
        if not line.strip():
            if fields:
                yield fields
            fields, last = {}, None
            continue
        if line[0] in " \t":
            if last:
                fields[last] += "\n" + line.strip()
            continue
        key, sep, value = line.partition(":")
        if sep:
            last = key
            fields[key] = value.strip()
    if fields:
        yield fields


class Suite:
    """One fetched suite: its Source, the Release bytes and signature, who signed it, and its lists
    ({component: Packages text})."""

    def __init__(self, source, release, signature, signer, parsed, lists):
        self.source, self.release, self.signature, self.signer = source, release, signature, signer
        self.parsed, self.lists = parsed, lists


def fetch_suite(source: Source, codename: str, arch: str, keys: dict, fetch=_fetch) -> Suite:
    """A suite's Release and Release.gpg (signed by one of its pinned keys, of release `codename` and with `arch`),
    and each component's uncompressed Packages list for `arch`, checked against the Release's SHA-256 and size."""
    base = f"{source.url}/dists/{source.suite}"
    release = fetch(f"{base}/Release")
    signature = fetch(f"{base}/Release.gpg")
    candidates = [k for name in source.keys for k in keys.get(name, ())]
    signer = signed_by(release, signature, candidates)
    if not signer:
        raise PreloadError(f"{base}/Release is not signed by an archive key llmcf pins for it")
    parsed = parse_release(release.decode("utf-8", "replace"))
    if not of_release(parsed, codename):
        raise PreloadError(f"{base}/Release is of {parsed['codename'] or parsed['suite']}, not of {codename}")
    if arch not in parsed["architectures"]:
        raise PreloadError(f"{base} has no packages for the architecture {arch}")
    lists = {}
    for component in source.components:
        rel = f"{component}/binary-{arch}/Packages"
        if rel not in parsed["sha256"]:
            continue
        want, size = parsed["sha256"][rel]
        text = None
        for suffix, unpack in ((".xz", lzma.decompress), (".gz", gzip.decompress), ("", lambda b: b)):
            if suffix and rel + suffix not in parsed["sha256"]:
                continue
            try:
                packed = fetch(f"{base}/{rel}{suffix}")
            except PreloadError:
                continue
            ps, psize = parsed["sha256"].get(rel + suffix, (want, size))
            if hashlib.sha256(packed).hexdigest() != ps or len(packed) != psize:
                raise PreloadError(f"{base}/{rel}{suffix} does not match its signed Release")
            data = unpack(packed)
            if hashlib.sha256(data).hexdigest() != want or len(data) != size:
                raise PreloadError(f"{base}/{rel} does not match its signed Release")
            text = data
            break
        if text is None:
            raise PreloadError(f"{base}/{rel} could not be fetched")
        lists[component] = text
    if not lists:
        raise PreloadError(f"{base} names no package list for {arch}")
    return Suite(source, release, signature, signer, parsed, lists)


# --- Debian's version order and the resolver ------------------------------------------------------------------------

def _order(c: str) -> int:
    if c == "~":
        return -1
    if c.isdigit():
        return 0
    if c.isalpha():
        return ord(c)
    return ord(c) + 256


def _compare_part(a: str, b: str) -> int:
    while a or b:
        na = re.match(r"[^0-9]*", a).group(0)
        nb = re.match(r"[^0-9]*", b).group(0)
        for i in range(max(len(na), len(nb))):
            ca = _order(na[i]) if i < len(na) else 0
            cb = _order(nb[i]) if i < len(nb) else 0
            if ca != cb:
                return -1 if ca < cb else 1
        a, b = a[len(na):], b[len(nb):]
        da = re.match(r"[0-9]*", a).group(0)
        db = re.match(r"[0-9]*", b).group(0)
        ia, ib = int(da or 0), int(db or 0)
        if ia != ib:
            return -1 if ia < ib else 1
        a, b = a[len(da):], b[len(db):]
    return 0


def compare_versions(a: str, b: str) -> int:
    """Debian's version order: -1, 0 or 1 (epoch, upstream version, revision)."""
    def split(v):
        epoch, _, rest = v.rpartition(":") if ":" in v else ("0", "", v)
        upstream, _, revision = rest.rpartition("-") if "-" in rest else (rest, "", "0")
        return int(epoch or 0), upstream, revision
    ea, ua, ra = split(a)
    eb, ub, rb = split(b)
    if ea != eb:
        return -1 if ea < eb else 1
    return _compare_part(ua, ub) or _compare_part(ra, rb)


def satisfies(version: str, op: str, want: str) -> bool:
    if not op:
        return True
    c = compare_versions(version, want)
    return {"<<": c < 0, "<=": c <= 0, "=": c == 0, ">=": c >= 0, ">>": c > 0, "<": c <= 0, ">": c >= 0}.get(op, False)


RE_RELATION = re.compile(r"^\s*([a-z0-9][a-z0-9+.-]*)(?::[a-z0-9-]+)?\s*(?:\(\s*(<<|<=|=|>=|>>|<|>)\s*([^\s)]+)\s*\))?"
                         r"\s*(?:\[[^\]]*\])?\s*$")


def relations(field: str):
    """A Depends field as a list of alternatives, each a list of (name, op, version)."""
    out = []
    for group in (field or "").split(","):
        if not group.strip():
            continue
        alternatives = []
        for alt in group.split("|"):
            match = RE_RELATION.match(alt)
            if match:
                alternatives.append((match.group(1), match.group(2) or "", match.group(3) or ""))
        if alternatives:
            out.append(alternatives)
    return out


class Index:
    """The packages of the fetched suites for one architecture: name -> [(version, stanza, suite number)] and the
    virtual packages they provide."""

    def __init__(self, suites: list, arch: str):
        self.real, self.virtual = {}, {}
        for number, suite in enumerate(suites, 1):
            for component, text in suite.lists.items():
                for st in stanzas(text.decode("utf-8", "replace")):
                    if st.get("Architecture") not in (arch, "all") or "Filename" not in st:
                        continue
                    st["_suite"], st["_component"] = number, component
                    self.real.setdefault(st.get("Package", ""), []).append(st)
                    for name, op, version in (alt for group in relations(st.get("Provides", "")) for alt in group):
                        self.virtual.setdefault(name, []).append((st, version if op == "=" else ""))

    def candidates(self, name: str, op: str = "", version: str = ""):
        """The stanzas that satisfy (name, op, version), the best first (the highest version, then the first suite)."""
        real = [st for st in self.real.get(name, ()) if satisfies(st.get("Version", ""), op, version)]
        real.sort(key=lambda st: st["_suite"])
        real.sort(key=_VersionKey, reverse=True)
        provided = [st for st, pv in self.virtual.get(name, ()) if not op or (pv and satisfies(pv, op, version))]
        return real + provided


class _VersionKey:
    def __init__(self, st):
        self.v = st.get("Version", "")

    def __lt__(self, other):
        return compare_versions(self.v, other.v) < 0


def resolve(index: Index, words, installed=None) -> list:
    """The stanzas to fetch for the package words (NAME or NAME=VERSION): each with its Depends and Pre-Depends (no
    Recommends), the first alternative that can be had; what `installed` (NAME -> VERSION) already satisfies is left
    out; with `installed` None (a board never read), the Essential and priority required packages count as there."""
    chosen, queue = {}, []

    def present(name, op, version):
        if name in chosen and satisfies(chosen[name].get("Version", ""), op, version):
            return True
        if any(satisfies(st.get("Version", ""), op, version) and st.get("Package") in chosen
               for st in index.real.get(name, ())):
            return True
        if not op and any(st.get("Package") in chosen for st, _ in index.virtual.get(name, ())):
            return True
        if installed is None:
            return any(st.get("Essential") == "yes" or st.get("Priority") == "required"
                       for st in index.real.get(name, ()))
        return name in installed and satisfies(installed[name], op, version)

    for word in words:
        name, _, version = word.partition("=")
        found = index.candidates(name, "=" if version else "", version)
        found = [st for st in found if st.get("Package") == name]
        if not found:
            raise PreloadError(f"{word} is not in the distribution's lists for this release and architecture")
        if installed is not None and name in installed and (not version or installed[name] == version):
            continue
        chosen[name] = found[0]
        queue.append(found[0])
    while queue:
        st = queue.pop(0)
        for field in ("Pre-Depends", "Depends"):
            for group in relations(st.get(field, "")):
                if any(present(*alt) for alt in group):
                    continue
                for alt in group:
                    found = index.candidates(*alt)
                    if found:
                        pick = found[0]
                        chosen[pick["Package"]] = pick
                        queue.append(pick)
                        break
                else:
                    want = " | ".join(f"{n}" + (f" ({o} {v})" if o else "") for n, o, v in group)
                    raise PreloadError(f"{st.get('Package')} needs {want}, which these lists cannot give")
        if len(chosen) > MAX_RESOLVED:
            raise PreloadError(f"more than {MAX_RESOLVED} packages: that is not a board's handful")
    return list(chosen.values())


# --- the folder and the archive --------------------------------------------------------------------------------------

def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def gather(recipe: dict, dest: Path, fetch=_fetch, log=say) -> list:
    """Fetches and checks everything for `recipe` into the folder `dest` (packages/ of the archive): per suite that a
    package comes from, Release, Release.gpg, its lists and the .deb files at their places; keys/ with the archive
    keys; sources.txt. Returns the resolved package words (NAME=VERSION)."""
    sources = sources_for(recipe["distribution"], recipe["release"], recipe["arch"], recipe["archives"])
    keys, key_files = {}, {}
    for name in sorted({k for s in sources for k in s.keys}):
        try:
            data, found = fetch_key(name, fetch)
        except PreloadError as error:
            log(f"the archive key {name} is not used: {error}")
            continue
        keys[name], key_files[name] = found, data
    if not keys:
        raise PreloadError("none of the distribution's archive keys could be fetched and checked")
    suites = []
    for source in sources:
        log(f"reading {source.url} {source.suite}")
        suites.append(fetch_suite(source, recipe["release"], recipe["arch"], keys, fetch))
    index = Index(suites, recipe["arch"])
    if recipe.get("stages"):   # a build image: each stage against its own base, the union fetched
        picked = {}
        for stage in recipe["stages"]:
            for st in resolve(index, stage["packages"], stage["installed"]):
                picked.setdefault((st["Package"], st["Version"]), st)
        resolved = list(picked.values())
    else:
        resolved = resolve(index, recipe["packages"], recipe["installed"])
    log(f"{len(resolved)} package(s) with their dependencies: "
        + ", ".join(f"{st['Package']} {st['Version']}" for st in resolved))
    used = {}
    for st in resolved:
        filename = st.get("Filename", "")
        if not RE_POOL.fullmatch(filename) or ".." in filename.split("/"):
            raise PreloadError(f"{st.get('Package')}: its Filename is not a place below pool/: {filename!r}")
        number, suite = st["_suite"], suites[st["_suite"] - 1]
        data = fetch(f"{suite.source.url}/{filename}")
        if len(data) != int(st.get("Size", -1)) or hashlib.sha256(data).hexdigest() != st.get("SHA256", "").lower():
            raise PreloadError(f"{filename} does not match the size and SHA-256 its signed list names")
        _write(dest / str(number) / filename, data)
        used.setdefault(number, set()).add(st["_component"])
    lines = []
    for number in sorted(used):
        suite = suites[number - 1]
        top = dest / str(number)
        _write(top / "Release", suite.release)
        _write(top / "Release.gpg", suite.signature)
        for component in sorted(used[number]):
            _write(top / component / f"binary-{recipe['arch']}" / "Packages", suite.lists[component])
        lines.append(f"{number} {suite.source.line()} signed-by={suite.signer}")
    for name, data in sorted(key_files.items()):
        _write(dest / "keys" / f"{name}.{'asc' if data.lstrip().startswith(b'-----') else 'gpg'}", data)
    _write(dest / "sources.txt", ("\n".join(lines) + "\n").encode("utf-8"))
    return [f"{st['Package']}={st['Version']}" for st in resolved]


def verify_folder(folder: Path, release: str, arch: str) -> list:
    """Checks a packages/ folder again (the PC at take-in; llmcf-install does the same on the board against the
    board's own keyring): the keys in keys/ only with a pinned fingerprint, each suite's Release signed by one of them,
    of `release` and `arch`, each list against its Release, every .deb against its list, and no other file. Returns
    the NAME=VERSION of its .deb files."""
    folder = Path(folder)
    keys = folder_keys(folder)
    if not keys:
        raise PreloadError("the preload holds no archive key with a fingerprint llmcf pins")
    found = []
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder).as_posix()
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise PreloadError(f"{rel} in the preload is a link or a special file")
    numbers = sorted(p.name for p in folder.iterdir() if p.is_dir() and p.name.isdigit())
    allowed = {"sources.txt"} | {p.relative_to(folder).as_posix() for p in (folder / "keys").glob("*")}
    for number in numbers:
        top = folder / number
        try:
            release_bytes = (top / "Release").read_bytes()
            signature = (top / "Release.gpg").read_bytes()
        except OSError:
            raise PreloadError(f"suite {number} of the preload has no Release or Release.gpg")
        if not signed_by(release_bytes, signature, keys):
            raise PreloadError(f"suite {number}: its Release is not signed by an archive key llmcf pins")
        parsed = parse_release(release_bytes.decode("utf-8", "replace"))
        if not of_release(parsed, release) or arch not in parsed["architectures"]:
            raise PreloadError(f"suite {number}: its Release is not of {release} for {arch}")
        allowed |= {f"{number}/Release", f"{number}/Release.gpg"}
        listed = {}
        for packages in sorted(top.glob(f"*/binary-{arch}/Packages")):
            rel = packages.relative_to(top).as_posix()
            data = packages.read_bytes()
            if parsed["sha256"].get(rel) != (hashlib.sha256(data).hexdigest(), len(data)):
                raise PreloadError(f"suite {number}: {rel} does not match its signed Release")
            allowed.add(f"{number}/{rel}")
            for st in stanzas(data.decode("utf-8", "replace")):
                if "Filename" in st:
                    listed[st["Filename"]] = st
        for deb in sorted((top / "pool").rglob("*.deb")) if (top / "pool").is_dir() else ():
            rel = deb.relative_to(top).as_posix()
            st = listed.get(rel)
            data = deb.read_bytes()
            if st is None or len(data) != int(st.get("Size", -1)) or \
                    hashlib.sha256(data).hexdigest() != st.get("SHA256", "").lower():
                raise PreloadError(f"suite {number}: {rel} is not in its signed lists or does not match them")
            allowed.add(f"{number}/{rel}")
            found.append(f"{st.get('Package')}={st.get('Version')}")
    present = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}
    extra = sorted(present - allowed)
    if extra:
        raise PreloadError("the preload holds files a board preload never has: " + " ".join(extra[:5]))
    if not found:
        raise PreloadError("the preload holds no packages")
    return found


def _frame(tag: int, body: bytes) -> bytes:
    """One OpenPGP packet with a new-format header."""
    n = len(body)
    if n < 192:
        length = bytes([n])
    elif n < 8384:
        length = bytes([((n - 192) >> 8) + 192, (n - 192) & 0xFF])
    else:
        length = b"\xff" + n.to_bytes(4, "big")
    return bytes([0xC0 | tag]) + length + body


def pinned_keyring(key_files) -> bytes:
    """A binary keyring of the pinned archive keys only: from the key files' bytes, each transferable key whose primary
    key has a pinned fingerprint (with its user ids, subkeys and signatures), every other key left out."""
    pinned = {fingerprint for fingerprint, _url in PINNED_KEYS.values()}
    out, seen = [], set()
    for data in key_files:
        block, keep = [], False
        for tag, body in list(packets(dearmor(data))) + [(6, None)]:
            if tag == 6:
                if keep and block:
                    out.append(b"".join(block))
                block, keep = [], False
                if body is None:
                    break
                try:
                    fingerprint = Key(body).fingerprint
                except ValueError:
                    continue
                keep = fingerprint in pinned and fingerprint not in seen
                seen.add(fingerprint)
            if keep and tag != 12:   # trust packets are local to a keyring
                block.append(_frame(tag, body))
    return b"".join(out)


def _crc24(data: bytes) -> int:
    crc = 0xB704CE
    for byte in data:
        crc ^= byte << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1864CFB
    return crc & 0xFFFFFF


def armor(data: bytes, kind: str = "SIGNATURE") -> bytes:
    """`data` as an ASCII-armored OpenPGP block (apt takes a detached signature only armored)."""
    body = base64.b64encode(data).decode("ascii")
    lines = [body[i:i + 64] for i in range(0, len(body), 64)]
    check = base64.b64encode(_crc24(data).to_bytes(3, "big")).decode("ascii")
    return ("\n".join([f"-----BEGIN PGP {kind}-----", ""] + lines + [f"={check}", f"-----END PGP {kind}-----"])
            + "\n").encode("ascii")


def folder_keys(folder: Path) -> list:
    """The signing keys of the pinned archive keys in a packages/ folder's keys/."""
    keys = []
    for path in sorted((Path(folder) / "keys").glob("*")):
        data = path.read_bytes()
        for fingerprint, _url in PINNED_KEYS.values():
            keys += signing_keys(data, fingerprint)
    return keys


def pinned_signature(release: bytes, signature: bytes, keys: list) -> bytes:
    """Release.gpg with only the signatures that verify with `keys` (the pinned keys), armored; a distribution signs
    with more keys than llmcf pins (Debian's release keys too), and gpgv fails on a signature whose key it does not
    have. Empty when none verifies."""
    kept = []
    for tag, body in packets(dearmor(signature)):
        if tag != 2:
            continue
        try:
            sig = Signature(body)
        except ValueError:
            continue
        if sig.type in (0x00, 0x01) and any(sig.by(key) and sig.verify(key, release) for key in keys):
            kept.append(_frame(2, body))
    return armor(b"".join(kept)) if kept else b""


def gpgv_check(folder: Path, gpgv: str = "") -> bool:
    """Checks each suite's Release of a packages/ folder again with gpgv against a keyring of the pinned keys only
    (pinned_keyring of keys/) and its signatures by those keys (pinned_signature), as apt does in the image build and
    llmcf-install on a board against the board's own keyrings. False when this computer has no gpgv (verify_folder's
    own check stands alone then); PreloadError when one does not verify."""
    import shutil
    import subprocess
    gpgv = gpgv or shutil.which("gpgv") or ""
    if not gpgv:
        return False
    folder = Path(folder)
    ring = pinned_keyring(p.read_bytes() for p in sorted((folder / "keys").glob("*")))
    keys = folder_keys(folder)
    if not ring or not keys:
        raise PreloadError("the preload holds no archive key with a fingerprint llmcf pins")
    with tempfile.TemporaryDirectory(prefix="llmcf-gpgv-") as home:
        keyring = Path(home) / "pinned.gpg"
        keyring.write_bytes(ring)
        for top in sorted(p for p in folder.iterdir() if p.is_dir() and p.name.isdigit()):
            release = (top / "Release").read_bytes()
            signature = pinned_signature(release, (top / "Release.gpg").read_bytes(), keys)
            (Path(home) / "Release.gpg").write_bytes(signature)
            (Path(home) / "Release").write_bytes(release)
            # No path at all: gpgv runs in its home folder ("--homedir .") and gets every file by its bare name, the
            # same on every system. The gpgv Git for Windows brings reads a drive letter its own way: C:\... as a
            # name in its home folder, C:/... as a URL ("invalid key resource URL"), and a --homedir C:/... as a
            # folder below the current one.
            result = subprocess.run([gpgv, "--homedir", ".", "--ignore-time-conflict", "--status-fd", "1",
                                     "--keyring", keyring.name, "Release.gpg", "Release"],
                                    cwd=home, capture_output=True, text=True, errors="replace")
            if not signature or result.returncode != 0 or "[GNUPG:] VALIDSIG " not in result.stdout:
                said = " ".join((result.stderr or "").split())
                said = said if len(said) <= 400 else "..." + said[-400:]
                raise PreloadError(f"suite {top.name}: its Release does not verify with gpgv against the pinned keys"
                                   + (f"; it says: {said}" if said else ""))
    return True


def parse_os_release(text: str) -> dict:
    """The fields of an os-release file (quotes removed)."""
    out = {}
    for line in (text or "").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and re.fullmatch(r"[A-Z_]+", key):
            out[key] = value.strip().strip("\"'")
    return out


def installed_from_status(text: str) -> dict:
    """NAME -> VERSION of the packages dpkg's status file names as installed."""
    out = {}
    for st in stanzas(text or ""):
        if st.get("Status", "").split()[-1:] == ["installed"] and st.get("Package") and st.get("Version"):
            out[st["Package"]] = st["Version"]
    return out


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _tar_add(archive: tarfile.TarFile, path: Path, name: str, now: int) -> None:
    info = tarfile.TarInfo(name)
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    info.mtime = now
    if path.is_dir():
        info.type, info.mode = tarfile.DIRTYPE, 0o755
        archive.addfile(info)
        for child in sorted(path.iterdir()):
            _tar_add(archive, child, f"{name}/{child.name}", now)
    else:
        info.size, info.mode = path.stat().st_size, 0o644
        with open(path, "rb") as handle:
            archive.addfile(info, handle)


def pack(recipe: dict, out: Path, fetch=_fetch, log=say) -> Path:
    """The board preload of `recipe` as an archive in `out`; returns its path."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="llmcf-packages-") as work:
        top = Path(work) / TOP
        resolved = gather(recipe, top / FOLDER, fetch, log)
        manifest = {"format": FORMAT, "kind": KIND, "scope": recipe["scope"],
                    "distribution": recipe["distribution"], "release": recipe["release"], "arch": recipe["arch"],
                    "archives": ",".join(recipe["archives"]) or "-", "packages": ",".join(recipe["packages"]),
                    "resolved": ",".join(resolved), "recipe_sha256": recipe_sha256(recipe),
                    "made": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        _write(top / "manifest.txt", "".join(f"{k}={v}\n" for k, v in manifest.items()).encode("utf-8"))
        sums = [f"{sha256_of(p)}  {p.relative_to(top).as_posix()}" for p in sorted(top.rglob("*")) if p.is_file()]
        _write(top / "SHA256SUMS", ("\n".join(sums) + "\n").encode("utf-8"))
        target = out / archive_name(recipe)
        part = Path(str(target) + ".part")
        now = int(time.time())
        with tarfile.open(part, "w:gz") as archive:
            _tar_add(archive, top, TOP, now)
        os.replace(part, target)
    log(f"written: {target}")
    log(f"sha256: {sha256_of(target)}")
    return target


def read_manifest(text: str) -> dict:
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def check_archive(archive: Path, out: Path, expected: str = "") -> dict:
    """Checks a board preload archive and unpacks it into out/llmcf-preload: only folders and files below it,
    SHA256SUMS complete and right, kind packages, and verify_folder. Returns its manifest with archive_sha256,
    folder and debs (the NAME=VERSION it holds)."""
    archive, out = Path(archive), Path(out)
    digest = sha256_of(archive)
    if expected and expected.lower() != digest:
        raise PreloadError(f"the preload is not the archive that was made: sha256 {digest}, expected {expected}")
    try:
        with tarfile.open(archive, "r:gz") as tar:
            members = tar.getmembers()
            for member in members:
                parts = member.name.split("/")
                if member.name.startswith(("/", "\\")) or ".." in parts or "\\" in member.name or parts[0] != TOP:
                    raise PreloadError(f"the preload holds a path outside {TOP}/: {member.name}")
                if not (member.isfile() or member.isdir()):
                    raise PreloadError(f"the preload holds an entry that is neither file nor folder: {member.name}")
            out.mkdir(parents=True, exist_ok=True)
            for member in members:
                target = out / member.name
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tar.extractfile(member) as source, open(target, "wb") as sink:
                        sink.write(source.read())
    except (tarfile.TarError, EOFError, OSError) as error:
        raise PreloadError(f"{archive} is not a readable .tar.gz ({error})")
    top = out / TOP
    try:
        manifest = read_manifest((top / "manifest.txt").read_text(encoding="utf-8"))
        sums_text = (top / "SHA256SUMS").read_text(encoding="utf-8")
    except OSError:
        raise PreloadError("the preload is incomplete (manifest.txt and SHA256SUMS belong in it)")
    if manifest.get("format") != FORMAT or manifest.get("kind") != KIND:
        raise PreloadError(f"this is not a board preload of distribution packages (kind {manifest.get('kind')!r})")
    listed = {}
    for line in sums_text.splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([^/ ][^ ]*)", line)
        if not match or ".." in match.group(2).split("/"):
            raise PreloadError(f"SHA256SUMS has a line that is not 'sha256  path' inside the preload: {line!r}")
        listed[match.group(2)] = match.group(1)
    present = {p.relative_to(top).as_posix() for p in top.rglob("*") if p.is_file()} - {"SHA256SUMS"}
    if present != set(listed):
        raise PreloadError("the files in the preload are not the ones SHA256SUMS lists")
    for name, want in listed.items():
        if sha256_of(top / name) != want:
            raise PreloadError(f"{name} in the preload does not have the sha256 SHA256SUMS names: copy it again")
    debs = verify_folder(top / FOLDER, manifest.get("release", ""), manifest.get("arch", ""))
    return dict(manifest, archive_sha256=digest, folder=str(top), debs=",".join(debs))


# --- the scripts ------------------------------------------------------------------------------------------------------

def _delimiter(texts) -> str:
    word = "LLMCF_" + "PACKAGES_END"
    while any(word in text for text in texts):
        word += "_"
    return word


def what_it_fetches(recipe: dict) -> str:
    return (f"{', '.join(recipe['packages'])} with their dependencies for {recipe['distribution']} "
            f"{recipe['release']} on {recipe['arch']}"
            + (f" (and the {', '.join(recipe['archives'])} archive)" if recipe["archives"] else "")
            + ", checked against the distribution's signed package lists")


def write_scripts(recipe: dict, out: Path) -> tuple:
    """preload.sh and preload.ps1 for `recipe`, each standing alone (the recipe and this program inside); they run
    `pack` on a computer with a network and Python 3. Returns their paths and what they fetch."""
    out = Path(out)
    spec = json.dumps(recipe, indent=1, sort_keys=True)
    program = Path(__file__).read_text(encoding="utf-8")
    end = _delimiter([spec, program])
    what = what_it_fetches(recipe)
    out.mkdir(parents=True, exist_ok=True)
    sh = ["#!/bin/sh",
          "# llmcf board preload, written by llmcf for a board without a network.",
          "# Run it on a computer WITH a network (Linux or macOS, Python 3): sh preload.sh [--out FOLDER]",
          f"# It fetches: {what}.",
          f"# It writes {archive_name(recipe)} into the current folder (or FOLDER);",
          "# give llmcf its path (llm-cli device package preload take, or the Boards window). The recipe and the",
          "# program are inside this script.",
          "set -eu",
          'out="$PWD"',
          'if [ "${1:-}" = "--out" ] && [ -n "${2:-}" ]; then out="$2"; fi',
          'py="${PRELOAD_PYTHON:-}"',
          'if [ -z "$py" ]; then for c in python3 python; do if command -v "$c" > /dev/null 2>&1; then py="$c"; break; fi; '
          'done; fi',
          'if [ -z "$py" ]; then echo "preload: Python 3 is needed (PRELOAD_PYTHON names one)" >&2; exit 1; fi',
          'work="$(mktemp -d)"',
          "trap 'rm -rf \"$work\"' EXIT",
          f"cat > \"$work/recipe.json\" <<'{end}'", spec, end,
          f"cat > \"$work/deb_preload.py\" <<'{end}'", program.rstrip("\n"), end,
          '"$py" -I "$work/deb_preload.py" pack --spec "$work/recipe.json" --out "$out"', ""]
    ps = ["# llmcf board preload, written by llmcf for a board without a network.",
          "# Run it on a computer WITH a network (Windows, Python 3):",
          "#   powershell -ExecutionPolicy Bypass -File preload.ps1 [-Out FOLDER]",
          f"# It fetches: {what}.",
          "# It writes the archive (.tar.gz) into the current folder (or FOLDER); give llmcf its path. The recipe",
          "# and the program are inside this script.",
          "param([string]$Out = (Get-Location).Path)",
          '$ErrorActionPreference = "Stop"',
          "$Py = $null; $PyArgs = @()",
          "if ($env:PRELOAD_PYTHON) { $Py = $env:PRELOAD_PYTHON }",
          'elseif (Get-Command "py" -ErrorAction SilentlyContinue) { $Py = "py"; $PyArgs = @("-3") }',
          'elseif (Get-Command "python" -ErrorAction SilentlyContinue) { $Py = "python" }',
          'elseif (Get-Command "python3" -ErrorAction SilentlyContinue) { $Py = "python3" }',
          'if (-not $Py) { Write-Error "preload: Python 3 is needed (PRELOAD_PYTHON names one)." }',
          '$Work = Join-Path ([IO.Path]::GetTempPath()) ("llmcf-packages-" + [Guid]::NewGuid().ToString("N"))',
          "New-Item -ItemType Directory -Path $Work -Force | Out-Null",
          "$Utf8 = New-Object System.Text.UTF8Encoding $false",
          # a here-string leaves out its last line break: it is written back, so the file is the same bytes as on the PC
          "function Write-Part ($Path, $Text) { [IO.File]::WriteAllText($Path, $Text.Replace(\"`r`n\", \"`n\") + \"`n\", $Utf8) }"]
    for name, text in (("recipe.json", spec), ("deb_preload.py", program)):
        ps += [f'Write-Part (Join-Path $Work "{name}") @\'', text.rstrip("\n"), "'@"]
    ps += ["try {",
           '    & $Py @PyArgs -I (Join-Path $Work "deb_preload.py") pack --spec (Join-Path $Work "recipe.json") --out $Out',
           "    $Code = $LASTEXITCODE",
           "} finally {",
           "    Remove-Item -Recurse -Force $Work -ErrorAction SilentlyContinue",
           "}",
           "exit $Code", ""]
    if any(line.startswith("'@") for text in (spec, program) for line in text.splitlines()):
        raise PreloadError("a recipe line would end a PowerShell here-string")
    (out / "preload.sh").write_text("\n".join(sh), encoding="utf-8", newline="\n")
    (out / "preload.sh").chmod(0o755)
    (out / "preload.ps1").write_text("\n".join(ps).replace("\r\n", "\n").replace("\n", "\r\n"),
                                     encoding="utf-8-sig", newline="")
    return out / "preload.sh", out / "preload.ps1", what


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("pack")
    p.add_argument("--spec", type=Path, required=True)
    p.add_argument("--out", type=Path, default=Path("."))
    p = sub.add_parser("check")
    p.add_argument("--archive", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--sha256", default="")
    p = sub.add_parser("write-scripts")
    p.add_argument("--spec", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "pack":
            print(pack(read_recipe(args.spec), args.out))
        elif args.command == "check":
            print("".join(f"{k}={v}\n" for k, v in check_archive(args.archive, args.out, args.sha256).items()), end="")
        else:
            sh, ps, what = write_scripts(read_recipe(args.spec), args.out)
            print(f"{sh}\n{ps}\n{what}")
    except PreloadError as error:
        print(f"preload: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"preload: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
