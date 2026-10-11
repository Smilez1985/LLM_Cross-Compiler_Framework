"""Community packs: manifest, signatures, zip format, age encryption for private sharing, user keys, privacy
scrub."""

import base64
import io
import json
import os
import re
import subprocess
import sys
import threading
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pyrage
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from orchestrator.Core import community_pack as cp
from orchestrator.Core.community_pack import (PackError, PackManifest, TrustedKey, build_manifest, decrypt_pack,
                                              encrypt_pack, read_pack, scrub, sign_manifest, verify_manifest,
                                              verify_tree, write_pack)

POSIX = os.name == "posix"
SECRET_TEXT = b"internal notes: the board serial and our build host layout"


def make_tree(root: Path) -> Path:
    (root / "modules").mkdir(parents=True)
    (root / "target.yml").write_text("metadata:\n  name: Demo\n", encoding="utf-8")
    script = root / "modules" / "build.sh"
    script.write_text("#!/bin/bash\necho build\n", encoding="utf-8")
    if POSIX:
        script.chmod(0o755)
    return root


def manifest_of(root: Path, **overrides) -> PackManifest:
    values = dict(kind="target", id="Demo", version="1.0.0", author="alice", license="MIT",
                  created="2026-10-11T12:00:00Z")
    values.update(overrides)
    return build_manifest(root, **values)


def trusted(key: Ed25519PrivateKey, valid_until="2099-12-31") -> TrustedKey:
    raw = key.public_key().public_bytes_raw()
    return TrustedKey(key_id=cp.key_id(key.public_key()), pubkey_b64=base64.b64encode(raw).decode(),
                      valid_until=valid_until)


# --- Manifest ---------------------------------------------------------------------------------------------

class TestManifest:

    def test_round_trip_and_stable_canonical_bytes(self, tmp_path):
        manifest = manifest_of(make_tree(tmp_path / "t"))
        assert [f.path for f in manifest.files] == ["modules/build.sh", "target.yml"]
        again = PackManifest.model_validate_json(json.dumps(json.loads(manifest.model_dump_json()), indent=4))
        assert again == manifest
        assert again.canonical_bytes() == manifest.canonical_bytes()
        assert b" " not in manifest.canonical_bytes().replace(b"Demo", b"")
        # Order of the listed files does not matter.
        reversed_files = manifest.model_copy(update={"files": list(reversed(manifest.files))})
        assert PackManifest.model_validate(reversed_files.model_dump()).canonical_bytes() == manifest.canonical_bytes()

    def test_content_digest_ignores_pack_metadata(self, tmp_path):
        root = make_tree(tmp_path / "t")
        a = manifest_of(root)
        b = manifest_of(root, author="bob", created="2027-01-01T00:00:00Z", version="2.0.0")
        assert a.content_sha256() == b.content_sha256() == cp.tree_sha256(root)
        (root / "target.yml").write_text("changed", encoding="utf-8")
        assert cp.tree_sha256(root) != a.content_sha256()

    @pytest.mark.parametrize("path", ["../x", "a/../b", "/etc/passwd", "a\\b", "C:/x", "a//b", "./a", "a/./b",
                                      "a.", "a ", "", "a/", "x\x00y", "con:stream"])
    def test_unsafe_paths_are_rejected(self, path):
        entry = {"path": path, "sha256": "0" * 64, "size": 1, "mode": 0o644}
        with pytest.raises(ValueError):
            cp.PackFile.model_validate(entry)

    def test_bad_hash_duplicates_and_reserved_names_are_rejected(self):
        base = dict(kind="target", id="x", version="1.0.0", author="a", license="MIT", created="2026-10-11T12:00:00Z")
        good = {"path": "a.sh", "sha256": "0" * 64, "size": 1, "mode": 0o644}
        for files in ([dict(good, sha256="0" * 63)], [good, dict(good, path="A.sh")],
                      [dict(good, path="pack.json")], [good, dict(good, path="a.sh/b")],
                      [dict(good, mode=0o4755)], [dict(good, mode=0o777)]):
            with pytest.raises(ValueError):
                PackManifest.model_validate({**base, "files": files})
        with pytest.raises(ValueError):
            PackManifest.model_validate({**base, "id": "../evil", "files": [good]})
        with pytest.raises(ValueError):
            PackManifest.model_validate({**base, "created": "11-10-2026_12-00-00", "files": [good]})

    @pytest.mark.skipif(not POSIX, reason="symbolic links need privileges on Windows")
    def test_symlinks_anywhere_are_rejected(self, tmp_path):
        root = make_tree(tmp_path / "t")
        secret = tmp_path / "id_ed25519"
        secret.write_text("private")
        (root / "modules" / "key").symlink_to(secret)
        with pytest.raises(PackError, match="symbolic link"):
            manifest_of(root)
        (root / "modules" / "key").unlink()
        (root / "linked").symlink_to(tmp_path, target_is_directory=True)
        with pytest.raises(PackError, match="symbolic link"):
            manifest_of(root)
        (root / "linked").unlink()
        link_root = tmp_path / "link-root"
        link_root.symlink_to(root, target_is_directory=True)
        with pytest.raises(PackError, match="not a folder"):
            manifest_of(link_root)

    @pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs FIFOs")
    def test_special_files_are_rejected(self, tmp_path):
        root = make_tree(tmp_path / "t")
        os.mkfifo(root / "pipe")
        with pytest.raises(PackError, match="special file"):
            manifest_of(root)

    @pytest.mark.skipif(not POSIX, reason="POSIX modes")
    def test_modes_are_masked(self, tmp_path):
        root = make_tree(tmp_path / "t")
        (root / "modules" / "build.sh").chmod(0o4777)  # setuid, world-writable
        (root / "target.yml").chmod(0o666)
        modes = {f.path: f.mode for f in manifest_of(root).files}
        assert modes == {"modules/build.sh": 0o755, "target.yml": 0o644}

    def test_verify_tree_detects_changes_extra_and_missing_files(self, tmp_path):
        root = make_tree(tmp_path / "t")
        manifest = manifest_of(root)
        verify_tree(root, manifest)
        data = bytearray((root / "target.yml").read_bytes())
        data[0] ^= 1  # one changed byte, same size
        (root / "target.yml").write_bytes(bytes(data))
        with pytest.raises(PackError, match="target.yml: content differs"):
            verify_tree(root, manifest)
        manifest = manifest_of(root)
        (root / "extra.sh").write_text("echo")
        with pytest.raises(PackError, match="extra.sh: not in the manifest"):
            verify_tree(root, manifest)
        (root / "extra.sh").unlink()
        (root / "target.yml").unlink()
        with pytest.raises(PackError, match="target.yml: missing"):
            verify_tree(root, manifest)


# --- Signatures -------------------------------------------------------------------------------------------

class TestSignatures:

    def test_sign_verify_round_trip(self, tmp_path):
        key = Ed25519PrivateKey.generate()
        manifest = manifest_of(make_tree(tmp_path / "t"))
        sig = sign_manifest(manifest, key)
        assert verify_manifest(manifest, sig, [trusted(key)]) == cp.key_id(key.public_key())
        assert verify_manifest(manifest, sig, [key.public_key()]) == sig.key_id

    def test_tampered_manifest_fails(self, tmp_path):
        key = Ed25519PrivateKey.generate()
        manifest = manifest_of(make_tree(tmp_path / "t"))
        sig = sign_manifest(manifest, key)
        changed = manifest.model_copy(update={"author": "mallory"})
        with pytest.raises(PackError, match="does not match"):
            verify_manifest(changed, sig, [trusted(key)])

    def test_wrong_key_fails(self, tmp_path):
        manifest = manifest_of(make_tree(tmp_path / "t"))
        sig = sign_manifest(manifest, Ed25519PrivateKey.generate())
        with pytest.raises(PackError, match="not one of the trusted keys"):
            verify_manifest(manifest, sig, [trusted(Ed25519PrivateKey.generate())])
        # A key that claims the right id but is a different key cannot exist in the trust list.
        other = Ed25519PrivateKey.generate()
        with pytest.raises(ValueError, match="does not belong"):
            TrustedKey(key_id=sig.key_id, pubkey_b64=base64.b64encode(other.public_key().public_bytes_raw()).decode(),
                       valid_until="2099-01-01")

    def test_expired_key_fails(self, tmp_path):
        key = Ed25519PrivateKey.generate()
        manifest = manifest_of(make_tree(tmp_path / "t"))
        sig = sign_manifest(manifest, key)
        entry = trusted(key, valid_until="2026-10-10")
        with pytest.raises(PackError, match="expired"):
            verify_manifest(manifest, sig, [entry], now=datetime(2026, 10, 11, tzinfo=timezone.utc))
        assert verify_manifest(manifest, sig, [entry], now=datetime(2026, 10, 10, 23, 59, tzinfo=timezone.utc))

    def test_signature_is_bound_to_its_purpose(self, tmp_path):
        key = Ed25519PrivateKey.generate()
        manifest = manifest_of(make_tree(tmp_path / "t"))
        raw = key.sign(manifest.canonical_bytes())  # no context prefix
        sig = cp.PackSignature(key_id=cp.key_id(key.public_key()),
                               sig=base64.urlsafe_b64encode(raw).decode().rstrip("="))
        with pytest.raises(PackError):
            verify_manifest(manifest, sig, [key.public_key()])

    def test_shipped_keys_file_is_valid(self):
        assert isinstance(cp.load_trusted_keys(), list)

    def test_damaged_keys_file_raises(self, tmp_path):
        bad = tmp_path / "keys.yml"
        bad.write_text("keys: [{key_id: nothex, pubkey_b64: x, valid_until: 2027-01-01}]\n")
        with pytest.raises(PackError, match="invalid"):
            cp.load_trusted_keys(bad)
        assert cp.load_trusted_keys(tmp_path / "missing.yml") == []


# --- Zip format -------------------------------------------------------------------------------------------

class TestPackFile:

    def test_write_read_round_trip_with_signature(self, tmp_path):
        key = Ed25519PrivateKey.generate()
        root = make_tree(tmp_path / "t")
        manifest = manifest_of(root)
        data = write_pack(root, manifest, sign_manifest(manifest, key))
        assert write_pack(root, manifest, sign_manifest(manifest, key)) == data, "same content, same bytes"
        out = tmp_path / "out"
        contents = read_pack(data, out, keys=[trusted(key)])
        assert contents.signer == cp.key_id(key.public_key())
        assert (out / "modules" / "build.sh").read_text() == "#!/bin/bash\necho build\n"
        if POSIX:
            assert (out / "modules" / "build.sh").stat().st_mode & 0o777 == 0o755
        assert not (out / "pack.json").exists(), "the manifest is not extracted as a payload file"

    def test_unsigned_pack_needs_no_keys_but_is_refused_when_keys_are_required(self, tmp_path):
        root = make_tree(tmp_path / "t")
        data = write_pack(root, manifest_of(root))
        assert read_pack(data, tmp_path / "a").signer is None
        with pytest.raises(PackError, match="not signed"):
            read_pack(data, tmp_path / "b", keys=[trusted(Ed25519PrivateKey.generate())])
        assert not (tmp_path / "b").exists()

    @staticmethod
    def rezip(data: bytes, change) -> bytes:
        source = zipfile.ZipFile(io.BytesIO(data))
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as zf:
            for info in source.infolist():
                content = change(info, source.read(info.filename))
                if content is not None:
                    zf.writestr(info, content)
            extra = change(None, None)
            if extra:
                zf.writestr(*extra)
        return out.getvalue()

    def test_extra_member_is_rejected(self, tmp_path):
        root = make_tree(tmp_path / "t")
        data = self.rezip(write_pack(root, manifest_of(root)),
                          lambda info, content: ("../../evil.sh", "x") if info is None else content)
        with pytest.raises(PackError, match="evil.sh: not in the manifest"):
            read_pack(data, tmp_path / "out")
        assert not (tmp_path / "out").exists() and not (tmp_path.parent / "evil.sh").exists()

    def test_changed_member_is_rejected_and_nothing_stays(self, tmp_path):
        root = make_tree(tmp_path / "t")

        def change(info, content):
            if info is not None and info.filename == "target.yml":
                return b"Metadata:\n  name: Demo\n"  # same size, other bytes
            return None if info is None else content

        out = tmp_path / "out"
        out.mkdir()
        with pytest.raises(PackError, match="target.yml"):
            read_pack(self.rezip(write_pack(root, manifest_of(root)), change), out)
        assert list(out.iterdir()) == []

    def test_symlink_member_is_rejected(self, tmp_path):
        root = make_tree(tmp_path / "t")

        def change(info, content):
            if info is not None and info.filename == "target.yml":
                info.external_attr = (0o120777 << 16)
            return None if info is None else content

        with pytest.raises(PackError, match="symbolic link"):
            read_pack(self.rezip(write_pack(root, manifest_of(root)), change), tmp_path / "out")

    def test_damaged_zip_data_is_a_pack_error(self, tmp_path):
        root = make_tree(tmp_path / "t")
        (root / "big.txt").write_text("x" * 5000)
        manifest = manifest_of(root)
        data = bytearray(write_pack(root, manifest))
        info = zipfile.ZipFile(io.BytesIO(bytes(data))).getinfo("big.txt")
        data[info.header_offset + 30 + len("big.txt") + 2] ^= 0xFF  # inside the compressed data
        with pytest.raises(PackError):
            read_pack(bytes(data), tmp_path / "out")
        assert not (tmp_path / "out").exists()

    def test_non_empty_destination_is_refused(self, tmp_path):
        root = make_tree(tmp_path / "t")
        out = tmp_path / "out"
        out.mkdir()
        (out / "keep.txt").write_text("mine")
        with pytest.raises(PackError, match="new or empty"):
            read_pack(write_pack(root, manifest_of(root)), out)
        assert (out / "keep.txt").read_text() == "mine"


# --- Encryption -------------------------------------------------------------------------------------------

@pytest.fixture
def people():
    return {name: (Ed25519PrivateKey.generate(), pyrage.x25519.Identity.generate())
            for name in ("alice", "bob", "carol", "dave")}


@pytest.fixture(scope="module")
def passphrase_pack():
    # scrypt runs about a second at the work factor rage picks for this computer: encrypted once for the module.
    sender = Ed25519PrivateKey.generate()
    return sender, encrypt_pack(SECRET_TEXT, sender=sender, passphrase="correct horse battery")


def members(blob: bytes):
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def zip_of(parts: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in parts.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def seal(age: bytes, sender: Ed25519PrivateKey) -> bytes:
    """An encrypted pack around any age bytes, signed by sender (as an attacker can do with any file)."""
    return zip_of({cp.AGE_NAME: age,
                   cp.AGE_SIG_NAME: cp._sign(cp._ENCRYPTED_CONTEXT + age, sender).model_dump_json()})


def age_header(*stanzas: str) -> bytes:
    """The text header of an age file with these stanza lines (body and MAC are not valid)."""
    body = "".join(f"-> {s}\n{'A' * 43}\n" for s in stanzas)
    return f"age-encryption.org/v1\n{body}--- {'B' * 43}\n".encode() + b"\x00" * 64


@pytest.fixture
def no_decryption(monkeypatch):
    monkeypatch.setattr(cp.pyrage, "decrypt", lambda *a: pytest.fail("decrypted before the checks"))
    monkeypatch.setattr(cp.pyrage.passphrase, "decrypt", lambda *a: pytest.fail("scrypt ran before the checks"))


class TestEncryption:

    def test_one_recipient_round_trip(self, people):
        alice_sign, _ = people["alice"]
        _, bob = people["bob"]
        blob = encrypt_pack(SECRET_TEXT, sender=alice_sign, recipients=[bob.to_public()])
        assert SECRET_TEXT not in blob and cp.is_encrypted_pack(blob)
        data, sender = decrypt_pack(blob, identity=bob, trusted_senders=[alice_sign.public_key()])
        assert data == SECRET_TEXT and sender == cp.key_id(alice_sign.public_key())
        assert decrypt_pack(blob, identity=bob) == (SECRET_TEXT, sender), "without a trust list the id is shown"

    def test_three_recipients_each_can_decrypt(self, people):
        alice_sign, _ = people["alice"]
        identities = [people[n][1] for n in ("bob", "carol", "dave")]
        blob = encrypt_pack(SECRET_TEXT, sender=alice_sign, recipients=[i.to_public() for i in identities])
        for identity in identities:
            assert decrypt_pack(blob, identity=identity)[0] == SECRET_TEXT

    def test_the_content_is_a_standard_age_file(self, people):
        # What the age and rage tools open after unzipping: a plain age file, nothing of ours inside.
        alice_sign, _ = people["alice"]
        _, bob = people["bob"]
        parts = members(encrypt_pack(SECRET_TEXT, sender=alice_sign, recipients=[bob.to_public()]))
        assert sorted(parts) == [cp.AGE_NAME, cp.AGE_SIG_NAME]
        assert parts[cp.AGE_NAME].startswith(b"age-encryption.org/v1\n-> X25519 ")
        assert pyrage.decrypt(parts[cp.AGE_NAME], [bob]) == SECRET_TEXT
        signature = cp.PackSignature.model_validate_json(parts[cp.AGE_SIG_NAME])
        assert signature.embedded_key().public_bytes_raw() == alice_sign.public_key().public_bytes_raw()

    def test_passphrase_round_trip(self, passphrase_pack, people):
        sender, blob = passphrase_pack
        age = members(blob)[cp.AGE_NAME]
        assert age.split(b"\n")[1].startswith(b"-> scrypt ") and b"-> X25519" not in age, "the stanza is alone"
        assert decrypt_pack(blob, passphrase="correct horse battery") == (SECRET_TEXT, cp.key_id(sender.public_key()))
        with pytest.raises(PackError, match="passphrase is wrong") as info:
            decrypt_pack(blob, passphrase="wrong horse battery")
        assert SECRET_TEXT.decode() not in str(info.value)

    def test_a_pack_asks_for_what_it_was_encrypted_to(self, passphrase_pack, people, no_decryption):
        _, blob = passphrase_pack
        with pytest.raises(PackError, match="--passphrase"):
            decrypt_pack(blob, identity=people["bob"][1])
        alice_sign, _ = people["alice"]
        to_bob = encrypt_pack(SECRET_TEXT, sender=alice_sign, recipients=[people["bob"][1].to_public()])
        with pytest.raises(PackError, match="encrypted to keys"):
            decrypt_pack(to_bob, passphrase="correct horse battery")

    def test_wrong_key_fails_without_plaintext(self, people):
        alice_sign, _ = people["alice"]
        _, bob = people["bob"]
        blob = encrypt_pack(SECRET_TEXT, sender=alice_sign, recipients=[bob.to_public()])
        with pytest.raises(PackError, match="not encrypted to your key") as info:
            decrypt_pack(blob, identity=people["carol"][1])
        assert SECRET_TEXT.decode() not in str(info.value)

    def test_any_change_breaks_the_signature_before_decryption(self, people, no_decryption):
        alice_sign, _ = people["alice"]
        _, bob = people["bob"]
        parts = members(encrypt_pack(SECRET_TEXT, sender=alice_sign, recipients=[bob.to_public()]))
        age = parts[cp.AGE_NAME]
        header_end = age.index(b"\n--- ")
        for position in (header_end - 5, header_end + 10, len(age) - 1):  # a stanza, the MAC, the payload
            changed = bytearray(age)
            changed[position] ^= 0x01
            with pytest.raises(PackError, match="does not match"):
                decrypt_pack(zip_of({cp.AGE_NAME: bytes(changed), cp.AGE_SIG_NAME: parts[cp.AGE_SIG_NAME]}),
                             identity=bob)

    def test_a_re_encrypted_pack_cannot_keep_the_senders_signature(self, people):
        # An insider (bob can decrypt) re-encrypts it to bob and dave: alice's signature no longer fits, and
        # signing it himself makes him the visible sender.
        alice_sign, _ = people["alice"]
        bob_sign, bob = people["bob"]
        _, dave = people["dave"]
        parts = members(encrypt_pack(SECRET_TEXT, sender=alice_sign, recipients=[bob.to_public()]))
        again = pyrage.encrypt(pyrage.decrypt(parts[cp.AGE_NAME], [bob]), [bob.to_public(), dave.to_public()])
        with pytest.raises(PackError, match="does not match"):
            decrypt_pack(zip_of({cp.AGE_NAME: again, cp.AGE_SIG_NAME: parts[cp.AGE_SIG_NAME]}), identity=dave)
        resigned = seal(again, bob_sign)
        with pytest.raises(PackError, match="not one of the trusted senders"):
            decrypt_pack(resigned, identity=dave, trusted_senders=[alice_sign.public_key()])
        assert decrypt_pack(resigned, identity=dave)[1] == cp.key_id(bob_sign.public_key())

    def test_unknown_sender_is_refused_before_decryption(self, people, no_decryption):
        alice_sign, _ = people["alice"]
        mallory_sign, _ = people["dave"]
        blob = encrypt_pack(SECRET_TEXT, sender=mallory_sign, recipients=[people["bob"][1].to_public()])
        with pytest.raises(PackError, match="not one of the trusted senders"):
            decrypt_pack(blob, identity=people["bob"][1], trusted_senders=[alice_sign.public_key()])

    def test_arguments_are_checked(self, people):
        alice_sign, _ = people["alice"]
        bob = people["bob"][1].to_public()
        with pytest.raises(ValueError, match="one of the two"):
            encrypt_pack(SECRET_TEXT, sender=alice_sign)
        with pytest.raises(ValueError, match="one of the two"):  # age keeps a passphrase stanza alone
            encrypt_pack(SECRET_TEXT, sender=alice_sign, recipients=[bob], passphrase="correct horse battery")
        with pytest.raises(ValueError, match="12 characters"):
            encrypt_pack(SECRET_TEXT, sender=alice_sign, passphrase="short")
        with pytest.raises(ValueError, match="At most 64"):
            encrypt_pack(SECRET_TEXT, sender=alice_sign, recipients=[bob] * 65)

    @pytest.mark.parametrize("blob", [b"PK\x03\x04", b"not a zip at all", b""])
    def test_malformed_input_is_refused(self, blob, no_decryption):
        with pytest.raises(PackError):
            decrypt_pack(blob, passphrase="x" * 12)

    def test_unexpected_members_and_an_unsigned_file_are_refused(self, people, no_decryption):
        alice_sign, _ = people["alice"]
        parts = members(encrypt_pack(SECRET_TEXT, sender=alice_sign, recipients=[people["bob"][1].to_public()]))
        for changed in ({**parts, "extra.txt": b"x"}, {cp.AGE_NAME: parts[cp.AGE_NAME]}):
            with pytest.raises(PackError, match="Not an encrypted pack"):
                decrypt_pack(zip_of(changed), identity=people["bob"][1])
        sig = json.loads(parts[cp.AGE_SIG_NAME])
        del sig["pubkey"]
        with pytest.raises(PackError, match="sender's public key"):
            decrypt_pack(zip_of({**parts, cp.AGE_SIG_NAME: json.dumps(sig)}), identity=people["bob"][1])

    @pytest.mark.parametrize("log_n", ["21", "30", "63"])
    def test_scrypt_work_factor_is_bounded_before_scrypt_runs(self, people, no_decryption, log_n):
        # Anyone can sign a file; pyrage's own limit follows this computer's speed and lets through work factors
        # that need several GiB of memory, which aborts the process.
        forged = seal(age_header(f"scrypt {'C' * 22} {log_n}"), people["dave"][0])
        with pytest.raises(PackError, match=f"2\\^{log_n}"):
            decrypt_pack(forged, passphrase="correct horse battery")

    @pytest.mark.parametrize("stanzas", [(f"X25519 {'D' * 43}", f"scrypt {'C' * 22} 18"),
                                         (f"scrypt {'C' * 22} 18", f"scrypt {'E' * 22} 18"),
                                         (f"scrypt {'C' * 22} 018",), (f"scrypt {'C' * 22}",)])
    def test_a_passphrase_stanza_must_be_alone_and_well_formed(self, people, no_decryption, stanzas):
        forged = seal(age_header(*stanzas), people["dave"][0])
        with pytest.raises(PackError, match="damaged"):
            decrypt_pack(forged, passphrase="correct horse battery", identity=people["bob"][1])

    def test_pyrage_refuses_a_mixed_header_too(self, people):
        # Checked here first; pyrage (age) refuses it as well, without running scrypt.
        alice_sign, _ = people["alice"]
        _, bob = people["bob"]
        age = members(encrypt_pack(SECRET_TEXT, sender=alice_sign, recipients=[bob.to_public()]))[cp.AGE_NAME]
        head, rest = age.split(b"\n--- ", 1)
        mixed = head + f"\n-> scrypt {'C' * 22} 18\n{'A' * 43}".encode() + b"\n--- " + rest
        for decrypt in (lambda: pyrage.decrypt(mixed, [bob]),
                        lambda: pyrage.passphrase.decrypt(mixed, "correct horse battery")):
            with pytest.raises(pyrage.DecryptError, match="Header is invalid"):
                decrypt()

    def test_the_old_format_is_refused_with_a_clear_message(self, tmp_path):
        old = b'lcf-encrypted-pack/v1\n{"v":1}\nmac x\n' + b"\x00" * 80
        (tmp_path / "old.lcfpack").write_bytes(old)
        assert cp.is_encrypted_pack(tmp_path / "old.lcfpack")
        with pytest.raises(PackError, match="development version.*export it again"):
            decrypt_pack(old, identity=pyrage.x25519.Identity.generate())

    def test_encrypted_signed_pack_end_to_end(self, people, tmp_path):
        alice_sign, _ = people["alice"]
        _, bob = people["bob"]
        root = make_tree(tmp_path / "t")
        manifest = manifest_of(root, kind="knowledge", id="team-notes")
        pack = write_pack(root, manifest, sign_manifest(manifest, alice_sign))
        assert not cp.is_encrypted_pack(pack)
        blob = encrypt_pack(pack, sender=alice_sign, recipients=[bob.to_public()])
        data, _ = decrypt_pack(blob, identity=bob, trusted_senders=[alice_sign.public_key()])
        contents = read_pack(data, tmp_path / "out", keys=[alice_sign.public_key()])
        assert contents.manifest.id == "team-notes"


# --- User keys --------------------------------------------------------------------------------------------

@pytest.fixture
def secrets_store(tmp_path, memory_keyring):
    from orchestrator.Core.secrets_manager import SecretsManager

    def make():
        sm = SecretsManager(tmp_path / "state")
        assert sm.initialize()
        return sm
    return make


class TestUserKeys:

    def test_keys_are_created_once_and_kept(self, secrets_store):
        first = cp.UserKeys(secrets_store())
        signing, identity = first.signing_key(), first.age_identity()
        again = cp.UserKeys(secrets_store())  # a new process
        assert again.signing_key().private_bytes_raw() == signing.private_bytes_raw()
        assert str(again.age_identity()) == str(identity)

    def test_public_keys_export_as_text(self, secrets_store):
        keys = cp.UserKeys(secrets_store())
        text = keys.export_public()
        recipient, signing = cp.parse_public_keys(text)
        assert str(recipient) == str(keys.age_identity().to_public()) and str(recipient).startswith("age1")
        assert signing.public_bytes_raw() == keys.signing_key().public_key().public_bytes_raw()
        assert cp.key_id(keys.signing_key().public_key()) in text and "AGE-SECRET-KEY" not in text
        blob = encrypt_pack(SECRET_TEXT, sender=keys.signing_key(), recipients=[recipient])
        assert decrypt_pack(blob, identity=keys.age_identity(), trusted_senders=[signing])[0] == SECRET_TEXT

    def test_public_keys_from_age_keygen_are_read(self):
        identity = pyrage.x25519.Identity.generate()
        keygen_output = f"# created: 2026-10-11T12:00:00Z\n# public key: {identity.to_public()}\n"
        assert [str(k) for k in cp.parse_public_keys(keygen_output)] == [str(identity.to_public())]
        with pytest.raises(PackError, match="Not a valid public key"):
            cp.parse_public_keys("age1" + "q" * 58)  # the checksum does not match
        assert cp.parse_public_keys("ssh-ed25519 AAAA x25519:nothing") == []

    def test_no_private_key_on_disk_in_plaintext(self, secrets_store, tmp_path):
        keys = cp.UserKeys(secrets_store())
        raw = keys.signing_key().private_bytes_raw()
        forms = [raw, raw.hex().encode(), base64.b64encode(raw), base64.b64encode(raw).rstrip(b"="),
                 base64.urlsafe_b64encode(raw).rstrip(b"="), str(keys.age_identity()).encode(),
                 str(keys.age_identity()).lower().encode()]
        files = [p for p in tmp_path.rglob("*") if p.is_file()]
        assert any(p.name == "secrets.store" for p in files)
        for path in files:
            content = path.read_bytes()
            assert not any(form in content for form in forms), f"private key readable in {path}"

    @pytest.mark.parametrize("name", [cp.UserKeys.SIGNING_SECRET, cp.UserKeys.AGE_SECRET])
    def test_a_damaged_key_is_never_replaced_and_the_advice_works(self, secrets_store, name):
        from click.testing import CliRunner
        from orchestrator import cli as cli_module
        sm = secrets_store()
        sm.set_secret(name, "not a key")
        load = cp.UserKeys(sm).signing_key if name == cp.UserKeys.SIGNING_SECRET else cp.UserKeys(sm).age_identity
        with pytest.raises(PackError, match="damaged") as info:
            load()
        assert sm.get_secret(name) == "not a key"
        # The message names a command that exists: print a new key, store it with `llm-cli secrets set`.
        command = re.search(r'python -c "([^"]+)" \| llm-cli (secrets set \S+ --with-token)', str(info.value))
        assert command, str(info.value)
        new_key = subprocess.run([sys.executable, "-c", command.group(1)], capture_output=True, text=True,
                                 check=True).stdout

        class Ctx(cli_module.FrameworkContext):
            def require_framework(self):
                self.framework_manager = SimpleNamespace(secrets_manager=sm)
                return self
        result = CliRunner().invoke(cli_module.cli, ["-q", *command.group(2).split()], obj=Ctx(), input=new_key)
        assert result.exit_code == 0, result.output
        assert load() is not None

    def test_undecryptable_stored_key_is_never_overwritten(self):
        writes = []
        store = type("Store", (), {"get_secret": lambda self, name: None,
                                   "list_secrets": lambda self: [cp.UserKeys.SIGNING_SECRET],
                                   "set_secret": lambda self, name, value: writes.append(name) or True})()
        with pytest.raises(PackError, match="cannot be decrypted"):
            cp.UserKeys(store).signing_key()
        assert writes == []

    def test_no_secrets_store_means_no_keys(self):
        with pytest.raises(PackError, match="secrets store"):
            cp.UserKeys(None).signing_key()

    def test_two_processes_creating_keys_at_once_get_the_same_key(self, secrets_store):
        # Each SecretsManager stands for a process. Without the lock both see no key, both create one, and one
        # process keeps signing with a key that is no longer stored.
        stores, writes = [secrets_store(), secrets_store()], []
        both_inside = threading.Barrier(2)
        for sm in stores:
            def slow_set(name, value, _real=sm.set_secret):
                writes.append(name)
                try:
                    both_inside.wait(timeout=1.5)  # waits for the other process, if it can get here at all
                except threading.BrokenBarrierError:
                    pass
                return _real(name, value)
            sm.set_secret = slow_set
        keys = [None, None]

        def create(i):
            keys[i] = cp.UserKeys(stores[i]).signing_key().private_bytes_raw()
        threads = [threading.Thread(target=create, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        assert writes == [cp.UserKeys.SIGNING_SECRET], "only one process creates the key"
        assert keys[0] == keys[1] == cp.UserKeys(secrets_store()).signing_key().private_bytes_raw()

    def test_age_secret_keys_are_caught_by_the_scan_and_redaction(self):
        # A real identity, made at runtime; documentation uses placeholders like AGE-SECRET-KEY-1XXXX...
        from orchestrator.security import secret_patterns
        secret = str(pyrage.x25519.Identity.generate())
        assert [kind for kind, _ in secret_patterns.find_secrets(f'key = "{secret}"')] == ["age secret key"]
        assert secret not in secret_patterns.redact(f"loaded {secret}") and secret not in scrub(f"key {secret}")
        assert secret_patterns.find_secrets("AGE-SECRET-KEY-1" + "X" * 58) == [], "placeholders are skipped"


# --- Privacy scrub ----------------------------------------------------------------------------------------

class TestScrub:

    @pytest.fixture(autouse=True)
    def machine(self, monkeypatch):
        monkeypatch.setattr("socket.gethostname", lambda: "build-box-17.lan")
        monkeypatch.setattr("getpass.getuser", lambda: "jenny")

    def test_home_paths_become_tilde(self, monkeypatch):
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: cls("/data/jenny")))
        text = (f"{Path.home()}/models/x.gguf /home/john/.cache C:\\Users\\Bob\\Downloads\\probe.txt "
                f"/Users/carol/work")
        out = scrub(text)
        assert out == "~/models/x.gguf ~/.cache ~\\Downloads\\probe.txt ~/work"

    def test_system_and_default_account_homes_stay(self, monkeypatch):
        # Not personal, and part of the content: Dockerfiles run as root, CI runs as runner.
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: cls("/root")))
        text = 'ENV PATH="/root/.local/bin:$PATH"\ncd /home/runner/work && ls /home/pi/models'
        assert scrub(text) == text
        assert scrub("/home/john/.cache") == "~/.cache"

    def test_image_digests_are_not_logins(self):
        pinned = "FROM ghcr.io/acme/rk-sdk@sha256:" + "ab" * 32 + " AS builder"
        assert scrub(pinned) == pinned, "a digest pin must survive the scrub (the validators require it)"
        remote = "git clone git" + "@" + "github.com:airockchip/rknn-llm.git"  # no addresses in the repository
        assert scrub(remote) == remote, "the git service account is no person"
        assert scrub("ssh jenny@devbox") == "ssh <user>@<host>"
        assert scrub("legit" + "@" + "example.org") == "<email>"

    def test_addresses_are_removed_but_loopback_stays(self):
        mail = "ops" + "@" + "example.org"  # built at runtime: no addresses in the repository
        out = scrub("server 192.168.1.20 and 10.0.0.7, ipv6 2001:db8::1 fe80::1, "
                    f"mac aa:bb:cc:dd:ee:ff, local 127.0.0.1 and ::1, mail {mail}")
        for leaked in ("192.168.1.20", "10.0.0.7", "2001:db8::1", "fe80::1", "aa:bb:cc:dd:ee:ff", mail):
            assert leaked not in out
        assert out.count("<ip>") == 4 and "<mac>" in out and "<email>" in out
        assert "127.0.0.1" in out and "::1" in out

    def test_host_and_user_names_jwts_and_keys(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abcdefghijklmnop"
        text = (f"built on build-box-17.lan (build-box-17) by Jenny, token {jwt}, "
                f"key sk-ant-api03-AbCdEf0123456789abcdefghij hf_aBcDeFgHiJkLmNoPqRsTuVwXyZ123456")
        out = scrub(text)
        assert "build-box-17" not in out and "Jenny" not in out and jwt not in out
        assert "<host>" in out and "<user>" in out and "<jwt>" in out
        assert "sk-REDACTED" in out and "hf_REDACTED" in out

    def test_common_account_names_do_not_damage_text(self, monkeypatch):
        monkeypatch.setattr("getpass.getuser", lambda: "root")
        monkeypatch.setattr("socket.gethostname", lambda: "localhost")
        text = "Mount the root filesystem; the server listens on localhost."
        assert scrub(text) == text

    def test_empty(self):
        assert scrub("") == "" and scrub(None) == ""


def test_key_valid_until_tomorrow_is_valid_now():
    key = Ed25519PrivateKey.generate()
    entry = trusted(key, valid_until=(datetime.now(timezone.utc) + timedelta(days=1)).date().isoformat())
    assert entry.valid_on(datetime.now(timezone.utc))
