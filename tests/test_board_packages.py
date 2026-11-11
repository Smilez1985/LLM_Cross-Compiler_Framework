"""System packages on a board (runtime/device/board_packages.py, scripts/deb_preload.py; llmcf-install's
install_package is tested in rust/llmcf-install/tests/root_actions.rs): the board preload's OpenPGP check against a
pinned key (a signing subkey only with its binding), the signed lists, Debian's version order and the resolver, the
archive and its check, the scripts (and the PowerShell here-string), the gate operations, the plan before the install
and its record, enterprise mode's internal mirror, the clock keeper's proposal, the AI's read-only facts, the CLI and
the Boards window."""

import gzip
import hashlib
import io
import json
import lzma
import os
import shutil
import subprocess
import tarfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.remote import gate
from orchestrator.runtime.device import board_ops  # noqa: F401 - defines the board operations
from orchestrator.runtime.device import board_clock, board_packages
from orchestrator.runtime.profile import ProfileStore

POSIX = pytest.mark.skipif(os.name == "nt" or shutil.which("sh") is None or shutil.which("tar") is None
                           or shutil.which("sha256sum") is None, reason="needs a POSIX sh, tar and sha256sum")
DEVICE = "0123456789abcdef"
dp = board_packages.deb_preload()


# --- a small OpenPGP signer for the tests (an RSA key made here, its packets written by hand) -------------------------

def _mpi(n: int) -> bytes:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return n.bit_length().to_bytes(2, "big") + raw


def _packet(tag: int, body: bytes) -> bytes:
    return bytes([0xC0 | tag, 0xFF]) + len(body).to_bytes(4, "big") + body


class PgpKey:
    def __init__(self):
        from cryptography.hazmat.primitives.asymmetric import rsa
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = self.private.public_key().public_numbers()
        self.body = b"\x04" + (1735689600).to_bytes(4, "big") + b"\x01" + _mpi(numbers.n) + _mpi(numbers.e)
        self.fingerprint = hashlib.sha1(b"\x99" + len(self.body).to_bytes(2, "big") + self.body).hexdigest().upper()

    def framed(self):
        return b"\x99" + len(self.body).to_bytes(2, "big") + self.body

    def signature(self, data: bytes, sig_type: int = 0x00, issuer: "PgpKey" = None, flags: bytes = b"") -> bytes:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        signer = issuer or self
        subs = b"\x05\x02" + (1735689600).to_bytes(4, "big")
        subs += b"\x16\x21\x04" + bytes.fromhex(signer.fingerprint)
        if flags:
            subs += bytes([len(flags) + 1, 27]) + flags
        hashed = bytes([4, sig_type, 1, 8]) + len(subs).to_bytes(2, "big") + subs
        trailer = b"\x04\xff" + len(hashed).to_bytes(4, "big")
        digest = hashlib.sha256(data + hashed + trailer).digest()
        value = signer.private.sign(data + hashed + trailer, padding.PKCS1v15(), hashes.SHA256())
        body = hashed + b"\x00\x00" + digest[:2] + _mpi(int.from_bytes(value, "big"))
        return _packet(2, body)

    def public(self, subkey: "PgpKey" = None) -> bytes:
        out = _packet(6, self.body) + _packet(13, b"test archive key")
        if subkey is not None:
            binding = self.signature(self.framed() + subkey.framed(), 0x18, flags=b"\x02")
            out += _packet(14, subkey.body) + binding
        return out


@pytest.fixture(scope="module")
def keys():
    return PgpKey(), PgpKey()


# --- a fake archive: one suite with its signed Release, the lists and three packages ----------------------------------

def deb(name, version):
    return f"!<arch>\n{name} {version}\n".encode()


STANZAS = [
    ("fake-hwclock", "0.13", "all", "libdep (>= 2.0) | other-dep", ""),
    ("libdep", "2.1", "arm64", "libc6 (>= 2.36), base-files", ""),
    ("libdep", "1.9", "arm64", "", ""),
    ("tool", "1.0", "arm64", "virtual-thing", ""),
    ("provider", "3.0", "all", "", "virtual-thing"),
    ("libc6", "2.36-9", "arm64", "", ""),
    ("base-files", "12.4", "arm64", "", ""),
]


def packages_text():
    out = []
    for name, version, arch, depends, provides in STANZAS:
        data = deb(name, version)
        filename = f"pool/main/{name[0]}/{name}/{name}_{version}_{arch}.deb"
        out.append(f"Package: {name}\nVersion: {version}\nArchitecture: {arch}\n"
                   + (f"Depends: {depends}\n" if depends else "") + (f"Provides: {provides}\n" if provides else "")
                   + ("Essential: yes\n" if name == "base-files" else "") + ("Priority: required\n"
                                                                             if name == "libc6" else "")
                   + f"Filename: {filename}\nSize: {len(data)}\nSHA256: {hashlib.sha256(data).hexdigest()}\n"
                   + "Description: a test package\n multi line\n")
    return "\n".join(out).encode()


def fake_archive(keys, *, sign_with_subkey=True, tamper=None):
    """{url: bytes} of a Debian-like archive at https://deb.example.org/debian for bookworm arm64, and the pinned keys
    entry for it."""
    primary, subkey = keys
    base = "https://deb.example.org/debian/dists/bookworm"
    plain = packages_text()
    files = {f"{base}/main/binary-arm64/Packages.xz": lzma.compress(plain),
             f"{base}/main/binary-arm64/Packages.gz": gzip.compress(plain)}
    lines = [f" {hashlib.sha256(plain).hexdigest()} {len(plain)} main/binary-arm64/Packages"]
    for suffix in (".xz", ".gz"):
        data = files[f"{base}/main/binary-arm64/Packages{suffix}"]
        lines.append(f" {hashlib.sha256(data).hexdigest()} {len(data)} main/binary-arm64/Packages{suffix}")
    release = ("Origin: Test\nSuite: oldstable\nCodename: bookworm\nArchitectures: amd64 arm64\nComponents: main\n"
               "SHA256:\n" + "\n".join(lines) + "\n").encode()
    files[f"{base}/Release"] = release
    files[f"{base}/Release.gpg"] = (subkey if sign_with_subkey else primary).signature(release)
    for name, version, arch, _d, _p in STANZAS:
        files[f"https://deb.example.org/debian/pool/main/{name[0]}/{name}/{name}_{version}_{arch}.deb"] = \
            deb(name, version)
    files["https://keys.example.org/test.gpg"] = primary.public(subkey)
    if tamper:
        tamper(files)
    return files


@pytest.fixture
def archive(monkeypatch, keys):
    """The fake archive as the only source of a "debian" release (sources_for and the pinned keys patched)."""
    files = fake_archive(keys)
    monkeypatch.setitem(dp.PINNED_KEYS, "test", (keys[0].fingerprint, "https://keys.example.org/test.gpg"))
    monkeypatch.setattr(dp, "sources_for", lambda d, r, a, x=(): [dp.Source("https://deb.example.org/debian", r,
                                                                            ["main"], ("test",))])

    def fetch(url):
        if url not in files:
            raise dp.PreloadError(f"{url}: 404")
        return files[url]
    return files, fetch


# --- OpenPGP and the signed lists ---------------------------------------------------------------------------------------

def test_a_release_signed_by_the_pinned_key_or_its_bound_subkey_verifies_and_nothing_else(keys):
    primary, subkey = keys
    data = b"Origin: Test\n"
    found = dp.signing_keys(primary.public(subkey), primary.fingerprint)
    assert [k.fingerprint for k in found] == [primary.fingerprint, subkey.fingerprint]
    assert dp.signed_by(data, subkey.signature(data), found) == subkey.fingerprint
    assert dp.signed_by(data, primary.signature(data), found) == primary.fingerprint
    assert dp.signed_by(data + b"x", subkey.signature(data), found) == ""
    # a key file without the pinned key gives nothing, and a subkey without a valid binding is never taken
    assert dp.signing_keys(subkey.public(), primary.fingerprint) == []
    stranger = PgpKey()
    forged = _packet(6, primary.body) + _packet(14, stranger.body) + stranger.signature(
        primary.framed() + stranger.framed(), 0x18, flags=b"\x02")
    assert [k.fingerprint for k in dp.signing_keys(forged, primary.fingerprint)] == [primary.fingerprint]
    assert dp.signed_by(data, stranger.signature(data), dp.signing_keys(forged, primary.fingerprint)) == ""
    # armored the same
    armored = b"-----BEGIN PGP PUBLIC KEY BLOCK-----\n\n" + __import__("base64").encodebytes(primary.public(subkey)) \
        + b"=abcd\n-----END PGP PUBLIC KEY BLOCK-----\n"
    assert len(dp.signing_keys(armored, primary.fingerprint)) == 2


def test_the_pinned_keys_are_full_fingerprints_with_their_distributions_own_addresses():
    for name, (fingerprint, url) in dp.PINNED_KEYS.items():
        assert len(fingerprint) == 40 and fingerprint == fingerprint.upper() and int(fingerprint, 16), name
        assert url.startswith("https://"), name
    for source in dp.sources_for("debian", "bookworm", "arm64", ("raspberrypi", "armbian")) + \
            dp.sources_for("ubuntu", "noble", "arm64") + dp.sources_for("raspbian", "bookworm", "armhf"):
        assert source.keys and all(k in dp.PINNED_KEYS for k in source.keys)
    assert [s.suite for s in dp.sources_for("debian", "bookworm", "arm64")] == \
        ["bookworm", "bookworm-updates", "bookworm-security"]
    assert "ports.ubuntu.com" in dp.sources_for("ubuntu", "noble", "arm64")[0].url
    assert "archive.ubuntu.com" in dp.sources_for("ubuntu", "noble", "amd64")[0].url


def test_a_suite_is_taken_only_signed_of_its_release_and_with_lists_that_match(keys, archive, monkeypatch):
    files, fetch = archive
    found = {"test": dp.signing_keys(files["https://keys.example.org/test.gpg"], keys[0].fingerprint)}
    source = dp.Source("https://deb.example.org/debian", "bookworm", ["main"], ("test",))
    suite = dp.fetch_suite(source, "bookworm", "arm64", found, fetch)
    assert suite.signer == keys[1].fingerprint and suite.lists["main"] == packages_text()
    with pytest.raises(dp.PreloadError, match="not of trixie"):
        dp.fetch_suite(source, "trixie", "arm64", found, fetch)
    with pytest.raises(dp.PreloadError, match="no packages for the architecture riscv64"):
        dp.fetch_suite(source, "bookworm", "riscv64", found, fetch)
    for tamper, why in ((lambda f: f.update({k: v + b"x" for k, v in f.items() if k.endswith("/Release")}),
                         "not signed"),
                        (lambda f: f.update({k: lzma.compress(packages_text() + b"\n") for k in f
                                             if k.endswith("Packages.xz")}), "does not match")):
        bad = fake_archive(keys, tamper=tamper)
        with pytest.raises(dp.PreloadError, match=why):
            dp.fetch_suite(source, "bookworm", "arm64", found, bad.__getitem__)


# --- Debian's version order and the resolver --------------------------------------------------------------------------

@pytest.mark.parametrize("a,b,want", [
    ("1.0", "1.0", 0), ("1.0", "1.1", -1), ("1.0~rc1", "1.0", -1), ("1:0.9", "2.0", 1), ("2.36-9", "2.36-9+deb12u4", -1),
    ("1.0a", "1.0", 1), ("1.0+b1", "1.0", 1), ("0.12+nmu1", "0.12", 1), ("10", "9", 1), ("1.0-1", "1.0-1~bpo1", 1),
])
def test_debians_version_order(a, b, want):
    assert dp.compare_versions(a, b) == want and dp.compare_versions(b, a) == -want


def test_the_resolver_takes_depends_alternatives_and_providers_and_leaves_out_what_the_board_has(keys, archive):
    files, fetch = archive
    found = {"test": dp.signing_keys(files["https://keys.example.org/test.gpg"], keys[0].fingerprint)}
    suite = dp.fetch_suite(dp.Source("https://deb.example.org/debian", "bookworm", ["main"], ("test",)), "bookworm",
                           "arm64", found, fetch)
    index = dp.Index([suite], "arm64")
    names = lambda stanzas: sorted(f"{s['Package']}={s['Version']}" for s in stanzas)  # noqa: E731
    # a board never read: the essential and required packages count as there
    assert names(dp.resolve(index, ["fake-hwclock"], None)) == ["fake-hwclock=0.13", "libdep=2.1"]
    # what the board has in a version that fits is left out; one too old is taken
    assert names(dp.resolve(index, ["fake-hwclock"], {"libdep": "2.0", "libc6": "2.36-9", "base-files": "12"})) == \
        ["fake-hwclock=0.13"]
    assert names(dp.resolve(index, ["fake-hwclock"], {"libdep": "1.0", "libc6": "2.36-9", "base-files": "12"})) == \
        ["fake-hwclock=0.13", "libdep=2.1"]
    assert names(dp.resolve(index, ["fake-hwclock"], {})) == ["base-files=12.4", "fake-hwclock=0.13", "libc6=2.36-9",
                                                             "libdep=2.1"]
    assert names(dp.resolve(index, ["tool"], {})) == ["provider=3.0", "tool=1.0"]
    assert names(dp.resolve(index, ["libdep=1.9"], {})) == ["libdep=1.9"]
    assert dp.resolve(index, ["fake-hwclock"], {"fake-hwclock": "0.13"}) == []
    with pytest.raises(dp.PreloadError, match="not in the distribution's lists"):
        dp.resolve(index, ["curl"], {})
    with pytest.raises(dp.PreloadError, match="not in the distribution's lists"):
        dp.resolve(index, ["libdep=3.0"], {})


def test_a_recipe_takes_only_checked_values():
    good = dp.make_recipe("board", "debian", "bookworm", "arm64", ["fake-hwclock"], ["raspberrypi"],
                          {"libc6": "2.36-9", "bad name": "1", "x": "not a version"})
    assert good["installed"] == {"libc6": "2.36-9"} and good["archives"] == ["raspberrypi"]
    for args in (("board", "fedora", "39", "arm64", ["x1"]), ("board", "debian", "Bookworm", "arm64", ["x1"]),
                 ("board", "debian", "bookworm", "arm64", ["-y"]), ("board", "debian", "bookworm", "arm64", []),
                 ("../x", "debian", "bookworm", "arm64", ["x1"]),
                 ("board", "debian", "bookworm", "arm64", ["ab", "ab=1"])):
        with pytest.raises(dp.PreloadError):
            dp.make_recipe(*args)


# --- the archive, its check and the scripts -----------------------------------------------------------------------------

@pytest.fixture
def made(tmp_path, archive):
    files, fetch = archive
    recipe = dp.make_recipe("board", "debian", "bookworm", "arm64", ["fake-hwclock"], (), None)
    path = dp.pack(recipe, tmp_path / "out", fetch=fetch, log=lambda _t: None)
    return recipe, path


def test_the_board_preload_holds_the_signed_lists_the_packages_and_the_keys(tmp_path, made):
    recipe, path = made
    assert path.name == dp.archive_name(recipe) and path.name.startswith("llmcf-preload-packages-board-bookworm-arm64-")
    manifest = dp.check_archive(path, tmp_path / "check")
    assert manifest["kind"] == "packages" and manifest["release"] == "bookworm" and manifest["arch"] == "arm64"
    assert manifest["debs"] == "fake-hwclock=0.13,libdep=2.1"
    top = tmp_path / "check" / "llmcf-preload" / "packages"
    assert sorted(p.relative_to(top).as_posix() for p in top.rglob("*") if p.is_file()) == [
        "1/Release", "1/Release.gpg", "1/main/binary-arm64/Packages",
        "1/pool/main/f/fake-hwclock/fake-hwclock_0.13_all.deb", "1/pool/main/l/libdep/libdep_2.1_arm64.deb",
        "keys/test.gpg", "sources.txt"]
    assert (top / "1" / "main" / "binary-arm64" / "Packages").read_bytes() == packages_text()


def _repack(path: Path, out: Path, change) -> Path:
    work = out / "work"
    with tarfile.open(path, "r:gz") as tar:
        tar.extractall(work, filter="data") if hasattr(tarfile, "data_filter") else tar.extractall(work)
    top = work / "llmcf-preload"
    change(top)
    sums = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(top).as_posix()}"
            for p in sorted(top.rglob("*")) if p.is_file() and p.name != "SHA256SUMS"]
    (top / "SHA256SUMS").write_bytes(("\n".join(sums) + "\n").encode())
    target = out / "repacked.tar.gz"
    with tarfile.open(target, "w:gz") as tar:
        tar.add(top, "llmcf-preload")
    return target


@pytest.mark.parametrize("why,change,match", [
    ("a changed .deb", lambda top: (top / "packages/1/pool/main/f/fake-hwclock/fake-hwclock_0.13_all.deb")
     .write_bytes(b"evil"), "not in its signed lists or does not match"),
    ("a changed list", lambda top: (top / "packages/1/main/binary-arm64/Packages").write_bytes(packages_text() + b"\n"),
     "does not match its signed Release"),
    ("a changed Release", lambda top: (top / "packages/1/Release").write_bytes(
        (top / "packages/1/Release").read_bytes() + b"X: y\n"), "not signed"),
    ("a key without its pin", lambda top: [(p.write_bytes(PgpKey().public())) for p in (top / "packages/keys").glob("*")],
     "no archive key"),
    ("a file a preload never has", lambda top: (top / "packages/1/install.sh").write_bytes(b"#!/bin/sh\n"),
     "never has"),
])
def test_a_changed_board_preload_is_refused_even_with_a_fresh_sha256sums(tmp_path, made, why, change, match):
    _recipe, path = made
    with pytest.raises(dp.PreloadError, match=match):
        dp.check_archive(_repack(path, tmp_path, change), tmp_path / "check")


def test_an_archive_with_a_link_or_a_path_outside_is_refused(tmp_path, made):
    _recipe, path = made
    for name, kind in (("llmcf-preload/packages/evil", tarfile.SYMTYPE), ("../outside", tarfile.REGTYPE)):
        bad = tmp_path / f"bad-{kind.decode() if isinstance(kind, bytes) else kind}.tar.gz"
        with tarfile.open(path, "r:gz") as src, tarfile.open(bad, "w:gz") as dst:
            for member in src.getmembers():
                dst.addfile(member, src.extractfile(member) if member.isfile() else None)
            info = tarfile.TarInfo(name)
            info.type = kind
            info.linkname = "/etc/shadow" if kind == tarfile.SYMTYPE else ""
            dst.addfile(info, io.BytesIO(b"") if kind == tarfile.REGTYPE else None)
        with pytest.raises(dp.PreloadError):
            dp.check_archive(bad, tmp_path / "check2")


def test_the_scripts_carry_the_recipe_and_the_program_and_the_powershell_here_string_keeps_its_bytes(tmp_path):
    recipe = dp.make_recipe("board", "debian", "bookworm", "arm64", ["fake-hwclock"], ())
    sh, ps, what = dp.write_scripts(recipe, tmp_path)
    assert "fake-hwclock with their dependencies for debian bookworm on arm64" in what
    text = sh.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh\n") and "deb_preload.py\" pack --spec" in text
    assert Path(dp.__file__).read_text(encoding="utf-8").rstrip("\n") in text
    ps_bytes = ps.read_bytes()
    assert ps_bytes.startswith(b"\xef\xbb\xbf") and b"\r\n" in ps_bytes and b"\n" not in ps_bytes.replace(b"\r\n", b"")
    ps_text = ps_bytes.decode("utf-8-sig")
    # a here-string leaves out its last line break: Write-Part writes it back, so the program is the same bytes
    assert '.Replace("`r`n", "`n") + "`n"' in ps_text and "Write-Part (Join-Path $Work \"deb_preload.py\") @'" in ps_text


@pytest.mark.skipif(os.name == "nt" or shutil.which("sh") is None, reason="runs the generated POSIX sh script")
def test_the_generated_preload_sh_hands_the_same_recipe_and_program_to_python(tmp_path):
    recipe = dp.make_recipe("llm-0123abcd-20261101120000", "debian", "bookworm", "arm64", ["fake-hwclock"], ())
    sh, _ps, _what = dp.write_scripts(recipe, tmp_path / "scripts")
    fake = tmp_path / "fake-python"
    seen = tmp_path / "seen"
    fake.write_text(f'#!/bin/sh\nmkdir -p {seen}\nwhile [ $# -gt 0 ]; do case $1 in --spec) cp "$2" {seen}/recipe.json;'
                    f' shift ;; *.py) cp "$1" {seen}/program.py ;; --out) echo "$2" > {seen}/out; shift ;; esac; '
                    'shift; done\n', encoding="utf-8")
    fake.chmod(0o755)
    done = subprocess.run(["sh", str(sh), "--out", str(tmp_path / "here")], capture_output=True, timeout=60,
                          env=dict(os.environ, PRELOAD_PYTHON=str(fake)))
    assert done.returncode == 0, done.stderr
    assert json.loads((seen / "recipe.json").read_text()) == recipe
    assert (seen / "program.py").read_bytes() == Path(dp.__file__).read_bytes()
    assert (seen / "out").read_text().strip() == str(tmp_path / "here")


# --- the framework's side: facts, gate operations, the plan and the install ---------------------------------------------

FACTS = ("id=raspbian\nlike=debian\ncodename=bookworm\narch=armhf\nmanager=apt\n"
         "source=http://raspbian.raspberrypi.com\nsource=http://archive.raspberrypi.com\ntaken=board\n"
         "pkg libc6 2.36-9+rpt2\npkg base-files 12.4+rpi1\npkg bad;name 1\n")


def test_the_board_facts_are_read_into_checked_words():
    facts = board_packages.parse_facts(FACTS)
    assert facts.distribution == "raspbian" and facts.codename == "bookworm" and facts.arch == "armhf"
    assert facts.archives == ("raspberrypi",) and facts.taken == ("board",)
    assert facts.installed == {"libc6": "2.36-9+rpt2", "base-files": "12.4+rpi1"}
    assert board_packages.parse_facts("id=dietpi\nlike=debian\n").distribution == "debian"
    assert board_packages.parse_facts("id=pop\nlike=ubuntu debian\n").distribution == "ubuntu"
    assert board_packages.parse_facts("id=fedora\nmanager=dnf\n").distribution == ""
    assert board_packages.parse_facts("id=debian\narmbian=yes\n").archives == ("armbian",)


def test_the_gate_operations_take_only_checked_words_and_root_only_through_the_sudo_rule():
    assert gate.operation("package-facts").gate and gate.operation("package-root").gate
    body = gate.script("package-root", "apply", "board", "fake-hwclock")
    assert 'sudo -n "$llmcf_fx" install_package apply "$llmcf_sc" fake-hwclock' in body
    assert "sudo -S" not in body and "llmcf_pw" not in body and "llmcf_dropin_ok install_package apply" in body
    assert "llmcf_sc=-" in body
    assert gate.script("package-root", "plan", "llm-0123abcd-20261101120000", "libc6=2.36-9+deb12u4", "g++")
    for args in (("remove", "board", "x1"), ("plan", "board"), ("plan", "board", "-y"), ("plan", "board", "a;b"),
                 ("plan", "board", "/tmp/x.deb"), ("plan", "board", "ab", "ab=1"),
                 ("plan", "board", *[f"p{i}x" for i in range(17)])):
        with pytest.raises(gate.GateRefused):
            gate.script("package-root", *args)
    take = gate.script("package-take", "/home/board/llmcf.tar.gz", "board")
    assert "sha256sum -c" in take and "kind=packages" in take and "--no-same-owner" in take
    for bad in (("relative.tar.gz", "board"), ("/home/board/x y.tar.gz", "board"), ("/home/board/x.tar.gz", "-x")):
        with pytest.raises(gate.GateRefused):
            gate.script("package-take", *bad)


@POSIX
def test_the_gates_package_kind_refuses_options_and_shell_words():
    sh = gate.KINDS["package"].sh
    for value, ok in (("fake-hwclock", True), ("libc6=2.36-9+deb12u4", True), ("-y", False), ("a;b", False),
                      ("$(id)", False), (".x", False), ("a b", False), ("", False)):
        done = subprocess.run(["sh", "-c", f"llmcf_k_package() {{ {sh}; }}; llmcf_k_package \"$1\"", "sh", value],
                              capture_output=True, timeout=10)
        assert (done.returncode == 0) == ok, value


def test_the_sudo_rule_names_the_package_action_and_an_older_rule_is_old():
    from orchestrator.runtime.bundle import gate_ops
    setup = gate_ops._setup_body("sudo", "apt", "b1", "a" * 64)
    assert "$llmcf_fx install_package *" in setup and "$llmcf_fx clock *" in setup
    assert "llmcf_dropin_ok install_package llmcf-check" in gate_ops._ready_body("a" * 64)


class Board:
    """A session stand-in: package-facts answers FACTS, package-root answers by its action."""

    def __init__(self, facts=FACTS, plan=None, apply=None):
        self.facts, self.sent = facts, []
        self.plan = plan or ("llmcf-bundle: package fake-hwclock installed=no\n"
                             "llmcf-bundle: would-install fake-hwclock 0.13\n"
                             "llmcf-bundle: result=planned changed=0 source=online pending=1 would=fake-hwclock=0.13 "
                             "already=-\n")
        self.apply = apply or ("llmcf-bundle: installed fake-hwclock 0.13\n"
                               "llmcf-bundle: result=ok changed=1 source=online installed=fake-hwclock=0.13 "
                               "already=-\n")

    def run(self, script, timeout=None, **_kw):
        self.sent.append((script.verb, script.args))
        if script.verb == "package-facts":
            return SimpleNamespace(status=0, out=self.facts, err="")
        if script.verb == "package-root":
            return SimpleNamespace(status=0, out=self.plan if script.args[0] == "plan" else self.apply, err="")
        raise AssertionError(f"unexpected operation {script.verb}")


class Audit:
    def __init__(self):
        self.rows = []

    def record(self, actor, tool, tier, decision, outcome, **kwargs):
        self.rows.append(dict(actor=actor, tool=tool, outcome=outcome, **kwargs))


def test_the_plan_comes_first_and_the_install_is_recorded_and_audited(tmp_path):
    board, audit = Board(), Audit()
    facts, plan = board_packages.plan_install(board, tmp_path, DEVICE, "board", ("fake-hwclock",))
    assert facts.distribution == "raspbian" and plan.result == "planned" and plan.listed("would") == ("fake-hwclock=0.13",)
    assert [s[0] for s in board.sent] == ["package-facts", "package-root"] and board.sent[1][1][0] == "plan"
    assert "Would install: fake-hwclock=0.13" in board_packages.plan_lines(plan)
    done = board_packages.apply_install(board, tmp_path, DEVICE, "board", ("fake-hwclock",), via="cli", audit=audit)
    assert done.result == "ok" and done.listed("installed") == ("fake-hwclock=0.13",)
    assert audit.rows[0]["tool"] == board_packages.AUDIT_TOOL and audit.rows[0]["outcome"] == "ok"
    assert audit.rows[0]["arguments"]["packages"] == ["fake-hwclock"]
    record = board_packages.load_record(tmp_path, DEVICE)
    assert record["last_run"]["result"] == "ok" and record["facts"]["codename"] == "bookworm"
    # a failed one is recorded too
    failing = Board(apply="llmcf-bundle: result=failed changed=0 apt did not install the packages: E: x\n")
    done = board_packages.apply_install(failing, tmp_path, DEVICE, "board", ("fake-hwclock",), via="gui", audit=audit)
    assert done.result == "failed" and audit.rows[-1]["outcome"] == "error"
    assert board_packages.load_record(tmp_path, DEVICE)["last_run"]["result"] == "failed"


def test_another_package_manager_is_named_and_nothing_runs_as_root(tmp_path):
    board = Board(facts="id=fedora\nmanager=dnf\n")
    with pytest.raises(board_packages.PackageError, match="package manager is dnf"):
        board_packages.plan_install(board, tmp_path, DEVICE, "board", ("fake-hwclock",))
    assert [s[0] for s in board.sent] == ["package-facts"]


def test_enterprise_mode_takes_only_the_internal_mirror_online(tmp_path):
    facts = board_packages.parse_facts("id=debian\ncodename=bookworm\narch=arm64\nmanager=apt\n"
                                       "source=http://mirror.lan\nsource=https://deb.debian.org\n")
    local = lambda url, config: SimpleNamespace(local=".lan" in url)  # noqa: E731
    assert board_packages.online_problem(facts, {"framework_mode": "private"}, local) == ""
    problem = board_packages.online_problem(facts, {"framework_mode": "enterprise"}, local)
    assert "https://deb.debian.org is not in the own network" in problem and "board preload" in problem
    mirror_only = board_packages.parse_facts("id=debian\nsource=http://mirror.lan\n")
    assert board_packages.online_problem(mirror_only, {"framework_mode": "enterprise"}, local) == ""
    assert "could be read" in board_packages.online_problem(board_packages.parse_facts("id=debian\n"),
                                                            {"framework_mode": "enterprise"}, local)
    # the plan refuses before anything runs as root; with a preload taken in for the scope, the preload is the way
    board = Board(facts="id=debian\ncodename=bookworm\narch=arm64\nmanager=apt\nsource=https://deb.debian.org\n")
    with pytest.raises(board_packages.PackageError, match="internal mirror"):
        board_packages.plan_install(board, tmp_path, DEVICE, "board", ("fake-hwclock",),
                                    config={"framework_mode": "enterprise"})
    assert [s[0] for s in board.sent] == ["package-facts"]
    board = Board(facts="id=debian\nmanager=apt\nsource=https://deb.debian.org\ntaken=board\n")
    board_packages.plan_install(board, tmp_path, DEVICE, "board", ("fake-hwclock",),
                                config={"framework_mode": "enterprise"})
    assert board.sent[-1][0] == "package-root"


def test_without_a_bundle_only_the_clock_keeper_and_with_one_only_its_packages(tmp_path, monkeypatch):
    assert board_packages.scope_words(tmp_path, "board", ()) == ("board", ("fake-hwclock",))
    with pytest.raises(board_packages.PackageError, match="only the clock keeper"):
        board_packages.scope_words(tmp_path, "board", ("curl",))
    monkeypatch.setattr(board_packages, "bundle_packages", lambda state, bid: ("libgomp1", "v4l-utils"))
    assert board_packages.scope_words(tmp_path, "b1", ()) == ("b1", ("libgomp1", "v4l-utils"))
    assert board_packages.scope_words(tmp_path, "b1", ("v4l-utils",)) == ("b1", ("v4l-utils",))
    with pytest.raises(board_packages.PackageError, match="names only"):
        board_packages.scope_words(tmp_path, "b1", ("curl",))


def test_the_recipe_follows_the_boards_last_reading_or_the_persons_words(tmp_path):
    with pytest.raises(board_packages.PackageError, match="never read"):
        board_packages.recipe_for(tmp_path, DEVICE, "board", ("fake-hwclock",))
    recipe = board_packages.recipe_for(tmp_path, DEVICE, "board", ("fake-hwclock",), distribution="debian",
                                       release="bookworm", arch="arm64")
    assert recipe["installed"] is None and recipe["arch"] == "arm64"
    board_packages.record_facts(tmp_path, DEVICE, board_packages.parse_facts(FACTS))
    recipe = board_packages.recipe_for(tmp_path, DEVICE, "board", ("fake-hwclock",))
    assert (recipe["distribution"], recipe["release"], recipe["arch"]) == ("raspbian", "bookworm", "armhf")
    assert recipe["archives"] == ["raspberrypi"] and recipe["installed"]["libc6"] == "2.36-9+rpt2"


def test_a_preload_for_another_board_is_refused_at_take_in(tmp_path, made):
    _recipe, path = made
    assert board_packages.check_preload(path, release="bookworm", arch="arm64")["debs"] == \
        "fake-hwclock=0.13,libdep=2.1"
    with pytest.raises(board_packages.PackageError, match="board runs trixie on arm64"):
        board_packages.check_preload(path, release="trixie", arch="arm64")


@POSIX
def test_the_take_operation_unpacks_a_checked_preload_as_the_account_and_refuses_a_link(tmp_path, made):
    _recipe, path = made
    home = tmp_path / "board-home"
    home.mkdir()
    shutil.copy(path, home / "in.tar.gz")

    def take(name, scope="board"):
        script = str(gate.script("package-take", str(home / name), scope))
        return subprocess.run(["sh", "-c", script], capture_output=True, text=True, timeout=60,
                              env=dict(os.environ, HOME=str(home)))
    done = take("in.tar.gz")
    assert "taken=yes" in done.stdout, done.stdout + done.stderr
    folder = home / board_packages.PACKAGES_REL / "board"
    assert (folder / "manifest.txt").is_file() and (folder / "packages" / "1" / "Release.gpg").is_file()
    assert not list((home / board_packages.PACKAGES_REL).glob(".take-*"))
    # a second take-in replaces the first
    assert "taken=yes" in take("in.tar.gz").stdout
    # an archive with a link is refused and the taken-in one stays
    bad = home / "bad.tar.gz"
    with tarfile.open(path, "r:gz") as src, tarfile.open(bad, "w:gz") as dst:
        for member in src.getmembers():
            dst.addfile(member, src.extractfile(member) if member.isfile() else None)
        info = tarfile.TarInfo("llmcf-preload/packages/1/evil.deb")
        info.type, info.linkname = tarfile.SYMTYPE, "/etc/shadow"
        dst.addfile(info)
    done = take("bad.tar.gz")
    assert "taken=no reason=entry" in done.stdout
    assert (folder / "manifest.txt").is_file()
    outside = tmp_path / "outside.tar.gz"
    shutil.copy(path, outside)
    script = str(gate.script("package-take", str(outside), "board"))
    done = subprocess.run(["sh", "-c", script], capture_output=True, text=True, timeout=60,
                          env=dict(os.environ, HOME=str(home)))
    assert "taken=no reason=not-in-home" in done.stdout


# --- the clock keeper and the AI's facts -------------------------------------------------------------------------------

def test_a_board_without_rtc_and_without_a_time_keeper_gets_fake_hwclock_proposed(tmp_path):
    assert board_packages.keeper_proposal(tmp_path, DEVICE) == ()
    board_clock.save_record(tmp_path, DEVICE, {"rtc": "reset", "keeper": "none", "offset_s": 0})
    assert board_packages.keeper_proposal(tmp_path, DEVICE) == ("fake-hwclock",)
    board_clock.save_record(tmp_path, DEVICE, {"keeper": "timesyncd"})
    assert board_packages.keeper_proposal(tmp_path, DEVICE) == ()
    board_clock.save_record(tmp_path, DEVICE, {"rtc": "yes", "keeper": "none"})
    assert board_packages.keeper_proposal(tmp_path, DEVICE) == ()
    lines = board_clock.keep_line(board_clock.ClockRun("unavailable", "", {"keep": "none"}))
    assert "install_package brings fake-hwclock" in lines


def test_an_ai_reads_the_facts_and_a_proposal_never_a_source_address(tmp_path):
    board_clock.save_record(tmp_path, DEVICE, {"rtc": "none", "keeper": "none"})
    board_packages.record_facts(tmp_path, DEVICE, board_packages.parse_facts(FACTS))
    facts = board_packages.ai_facts(tmp_path, DEVICE)
    assert facts["missing"] == ["fake-hwclock"] and facts["distribution"] == "raspbian"
    assert "Only the person can do this" in facts["proposal"]
    assert "raspberrypi.com" not in json.dumps(facts)


def test_no_tool_of_an_ai_runs_an_install_or_takes_a_preload():
    from test_deploy_tools import AI_REACHABLE, REPO_ROOT
    for entry in AI_REACHABLE:
        path = REPO_ROOT / entry
        for file in ([path] if path.is_file() else sorted(path.rglob("*.py"))):
            text = file.read_text(encoding="utf-8")
            for name in ("apply_install", "board_packages.plan", "board_packages.take", "take_on_board", "package-root",
                         "package-take", "run_root"):
                assert name not in text, f"{file.relative_to(REPO_ROOT)} names {name}"


# --- the bundle's packages -------------------------------------------------------------------------------------------

def test_a_blocks_system_packages_become_the_bundles_first_root_action(tmp_path):
    from orchestrator.runtime.bundle import blocks
    from orchestrator.runtime.bundle.manifest import RootAction
    spec = {"schema": blocks.BLOCK_SCHEMA, "id": "with-packages", "version": "1.0.0", "kind": "stt",
            "status": "template", "system_packages": ["libgomp1", "v4l-utils=1.22.1-5+b2"]}
    import yaml
    block = blocks.parse_block(yaml.safe_dump(spec), tmp_path)
    assert block.system_packages == ("libgomp1", "v4l-utils=1.22.1-5+b2")
    assert block.label()["system_packages"] == ["libgomp1", "v4l-utils=1.22.1-5+b2"]
    for bad in (["-y"], ["a;b"], "libgomp1", ["ab", "ab=1"], [f"pkg{i}" for i in range(17)]):
        with pytest.raises(blocks.BlockError, match="system_packages"):
            blocks.parse_block(yaml.safe_dump(dict(spec, system_packages=bad)), tmp_path)
    action = RootAction(action="install_package", component="stt", packages=("libgomp1",))
    assert action.packages == ("libgomp1",)
    for bad in ({"packages": ("-y",)}, {"packages": ()}, {}):
        with pytest.raises(ValueError):
            RootAction(action="install_package", component="stt", **bad)
    with pytest.raises(ValueError, match="only install_package"):
        RootAction(action="create_user", component="stt", user="llmcf", packages=("libgomp1",))


# --- the CLI and the Boards window -------------------------------------------------------------------------------------

def _stored(tmp_path):
    from orchestrator.runtime.profile import parse_profile
    state = tmp_path / "state"
    ProfileStore(state).save_device(DEVICE, parse_profile("SCHEMA=2\nOS=linux\nARCH=aarch64\n"), label="pi")
    return state


def test_the_cli_shows_the_state_and_writes_the_board_preload(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from orchestrator import cli
    state = _stored(tmp_path)
    board_packages.record_facts(state, DEVICE, board_packages.parse_facts(FACTS))
    board_clock.save_record(state, DEVICE, {"rtc": "none", "keeper": "none"})
    monkeypatch.setattr(cli, "_state_dir", lambda ctx: state)
    runner = CliRunner()
    out = runner.invoke(cli.cli, ["device", "package", "show", DEVICE])
    assert out.exit_code == 0, out.output
    assert "raspbian bookworm on armhf" in out.output and "Missing at the last reading: fake-hwclock" in out.output
    out = runner.invoke(cli.cli, ["device", "package", "preload", "write", DEVICE, "--out", str(tmp_path / "pre")])
    assert out.exit_code == 0, out.output
    assert (tmp_path / "pre" / "preload.sh").is_file() and (tmp_path / "pre" / "preload.ps1").is_file()
    assert "device package preload take" in out.output
    recipe_line = [line for line in (tmp_path / "pre" / "preload.sh").read_text().splitlines()
                   if line.startswith(' "release"')]
    assert recipe_line == [' "release": "bookworm",']
    out = runner.invoke(cli.cli, ["device", "package", "preload", "write", DEVICE, "curl", "--out",
                                  str(tmp_path / "pre2")])
    assert out.exit_code == 1 and "only the clock keeper" in out.output
    out = runner.invoke(cli.cli, ["device", "clock", DEVICE])
    assert "fake-hwclock" in out.output and "device package install" in out.output
    # a damaged archive is refused before any connection
    (tmp_path / "bad.tar.gz").write_bytes(b"not a tar")
    monkeypatch.setattr(cli, "_package_session", lambda *a, **k: pytest.fail("no connection for a bad archive"))
    out = runner.invoke(cli.cli, ["device", "package", "preload", "take", DEVICE, str(tmp_path / "bad.tar.gz")])
    assert out.exit_code == 1 and "not a readable" in out.output


def test_the_cli_install_shows_the_plan_and_needs_the_yes(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from orchestrator import cli
    state = _stored(tmp_path)
    board, audit = Board(), Audit()
    board.close = lambda: None
    monkeypatch.setattr(cli, "_state_dir", lambda ctx: state)
    monkeypatch.setattr(cli, "_package_session", lambda *a, **k: board)
    monkeypatch.setattr(cli, "_device_audit", lambda ctx, state_dir: audit)
    runner = CliRunner()
    out = runner.invoke(cli.cli, ["device", "package", "install", DEVICE])
    assert out.exit_code == 1 and "Would install: fake-hwclock=0.13" in out.output and "Nothing was installed" in out.output
    assert [s[1][0] for s in board.sent if s[0] == "package-root"] == ["plan"]
    out = runner.invoke(cli.cli, ["device", "package", "install", DEVICE, "--yes"])
    assert out.exit_code == 0, out.output
    assert "Installed: fake-hwclock=0.13" in out.output and audit.rows[-1]["tool"] == board_packages.AUDIT_TOOL


def test_the_boards_window_shows_the_packages_and_writes_the_board_preload(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from orchestrator.gui import boards_window
    from orchestrator.gui.boards_window import BoardsWindow
    state = _stored(tmp_path)
    board_packages.record_facts(state, DEVICE, board_packages.parse_facts(FACTS))
    board_clock.save_record(state, DEVICE, {"rtc": "reset", "keeper": "none"})
    framework = SimpleNamespace(config=SimpleNamespace(user_state_dir=str(state), get=lambda k, d=None: d))
    window = BoardsWindow(framework, lang="en")
    try:
        assert window.findChild(type(window.btn_cancel), "btn_boards_packages") is not None
        text = window.packages_text(DEVICE, [])
        assert "raspbian bookworm armhf (apt)" in text and "fake-hwclock" in text
        monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path / "pre"))
        shown = []
        monkeypatch.setattr(window, "_info", lambda text, kind="information": shown.append((kind, text)))
        window.write_package_preload({"device_id": DEVICE, "label": "pi"}, "board")
        assert (tmp_path / "pre" / "preload.sh").is_file() and shown and shown[0][0] == "information"
        assert boards_window.BUTTONS[-1][0] == "btn_boards_packages"
    finally:
        window.close()
        window.deleteLater()
