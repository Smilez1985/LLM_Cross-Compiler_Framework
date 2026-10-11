"""Community packs: manifest, signatures, zip format, encryption for private sharing, user keys, privacy scrub."""

import base64
import io
import json
import os
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

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
    raw = cp._raw_public(key.public_key())
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
            TrustedKey(key_id=sig.key_id, pubkey_b64=base64.b64encode(cp._raw_public(other.public_key())).decode(),
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
    return {name: (Ed25519PrivateKey.generate(), X25519PrivateKey.generate()) for name in ("alice", "bob", "carol",
                                                                                            "dave")}


def rebuild(head_json: dict, mac: bytes, body: bytes, sig: bytes) -> bytes:
    head = cp.ENCRYPTED_MAGIC + cp._canonical(head_json) + b"\n"
    return head + b"mac " + base64.b64encode(mac) + b"\n" + body + sig


def split(blob: bytes):
    header, _, _, mac, body, sig = cp._parse_encrypted(blob)
    return header.model_dump(mode="json"), mac, body, sig


class TestEncryption:

    def test_one_recipient_round_trip(self, people):
        alice_sign, _ = people["alice"]
        _, bob_enc = people["bob"]
        blob = encrypt_pack(SECRET_TEXT, sender=alice_sign, recipients=[bob_enc.public_key()])
        assert SECRET_TEXT not in blob
        result = decrypt_pack(blob, identity=bob_enc, trusted_senders=[alice_sign.public_key()])
        assert result.data == SECRET_TEXT
        assert result.sender_id == cp.key_id(alice_sign.public_key()) and result.sender_trusted
        assert not decrypt_pack(blob, identity=bob_enc).sender_trusted, "never trusted without a trust list"

    def test_three_recipients_each_can_decrypt(self, people):
        alice_sign, _ = people["alice"]
        recipients = [people[n][1] for n in ("bob", "carol", "dave")]
        blob = encrypt_pack(SECRET_TEXT, sender=alice_sign, recipients=[r.public_key() for r in recipients])
        for identity in recipients:
            assert decrypt_pack(blob, identity=identity).data == SECRET_TEXT
        header, *_ = split(blob)
        assert len(header["recipients"]) == 3
        assert all(cp._raw_public(r.public_key()).hex() not in blob.hex() for r in recipients), \
            "stanzas do not name their recipients"

    def test_passphrase_round_trip_and_mixed(self, people):
        alice_sign, _ = people["alice"]
        _, bob_enc = people["bob"]
        blob = encrypt_pack(SECRET_TEXT, sender=alice_sign, recipients=[bob_enc.public_key()],
                            passphrase="correct horse battery")
        assert decrypt_pack(blob, passphrase="correct horse battery").data == SECRET_TEXT
        assert decrypt_pack(blob, identity=bob_enc).data == SECRET_TEXT
        header, *_ = split(blob)
        assert {s["type"] for s in header["recipients"]} == {"x25519", "scrypt"}
        assert [s["log_n"] for s in header["recipients"] if s["type"] == "scrypt"] == [17]

    def test_wrong_key_or_passphrase_fails_without_plaintext(self, people):
        alice_sign, _ = people["alice"]
        _, bob_enc = people["bob"]
        _, carol_enc = people["carol"]
        blob = encrypt_pack(SECRET_TEXT, sender=alice_sign, recipients=[bob_enc.public_key()],
                            passphrase="correct horse battery")
        for kwargs in ({"identity": carol_enc}, {"passphrase": "wrong horse battery"}, {}):
            with pytest.raises(PackError, match="not encrypted to your key") as info:
                decrypt_pack(blob, **kwargs)
            assert SECRET_TEXT.decode() not in str(info.value)

    def test_tampered_header_payload_or_signature_fails(self, people):
        alice_sign, _ = people["alice"]
        _, bob_enc = people["bob"]
        blob = encrypt_pack(SECRET_TEXT, sender=alice_sign, recipients=[bob_enc.public_key()])
        header_at = len(cp.ENCRYPTED_MAGIC) + 10
        for position in (header_at, len(blob) - 100, len(blob) - 1):  # header, body, signature
            changed = bytearray(blob)
            changed[position] ^= 0x01
            with pytest.raises(PackError):
                decrypt_pack(bytes(changed), identity=bob_enc)
        with pytest.raises(PackError):
            decrypt_pack(blob[:-1], identity=bob_enc)

    def test_removing_or_adding_a_recipient_is_detected(self, people):
        alice_sign, _ = people["alice"]
        bob_sign, bob_enc = people["bob"]
        _, carol_enc = people["carol"]
        _, dave_enc = people["dave"]
        blob = encrypt_pack(SECRET_TEXT, sender=alice_sign, recipients=[bob_enc.public_key(), carol_enc.public_key()])
        header, mac, body, sig = split(blob)

        removed = dict(header, recipients=header["recipients"][:1])
        with pytest.raises(PackError, match="signature"):
            decrypt_pack(rebuild(removed, mac, body, sig), identity=bob_enc)

        # An insider (bob knows the file key) adds dave, recomputes the MAC and re-encrypts the body:
        # alice's signature no longer fits, and signing it himself makes him the visible sender.
        parsed = cp._EncryptedHeader.model_validate(header)
        file_key = cp._unwrap_file_key(parsed, bob_enc, None)
        ephemeral = X25519PrivateKey.generate()
        epk, rpk = cp._raw_public(ephemeral.public_key()), cp._raw_public(dave_enc.public_key())
        wrap = cp._x25519_wrap_key(ephemeral.exchange(dave_enc.public_key()), epk, rpk)
        stanza = {"type": "x25519", "epk": cp._b64e(epk),
                  "body": cp._b64e(ChaCha20Poly1305(wrap).encrypt(bytes(12), file_key, None))}
        def forge(sender_text):
            added = dict(header, sender=sender_text, recipients=header["recipients"] + [stanza])
            signed_header = cp.ENCRYPTED_MAGIC + cp._canonical(added) + b"\n"
            head = signed_header + b"mac " + base64.b64encode(cp._header_mac(file_key, signed_header)) + b"\n"
            salt = base64.b64decode(header["payload_salt"])
            return head + ChaCha20Poly1305(cp._hkdf(file_key, salt, cp._INFO_PAYLOAD)).encrypt(bytes(12), SECRET_TEXT,
                                                                                               head)

        with pytest.raises(PackError, match="signature"):
            decrypt_pack(forge(header["sender"]) + sig, identity=dave_enc)
        unsigned = forge(cp.public_key_text(bob_sign.public_key()))
        resigned = unsigned + bob_sign.sign(cp._ENCRYPTED_CONTEXT + unsigned)
        with pytest.raises(PackError, match="sender"):
            decrypt_pack(resigned, identity=dave_enc, trusted_senders=[alice_sign.public_key()])
        assert decrypt_pack(resigned, identity=dave_enc).sender_id == cp.key_id(bob_sign.public_key())

    def test_unknown_sender_is_refused_before_decryption(self, people, monkeypatch):
        mallory_sign, _ = people["dave"]
        alice_sign, _ = people["alice"]
        _, bob_enc = people["bob"]
        blob = encrypt_pack(SECRET_TEXT, sender=mallory_sign, recipients=[bob_enc.public_key()])
        monkeypatch.setattr(cp, "_unwrap_file_key", lambda *a: pytest.fail("decrypted before the sender check"))
        with pytest.raises(PackError, match="not one of the trusted senders"):
            decrypt_pack(blob, identity=bob_enc, trusted_senders=[alice_sign.public_key()])

    def test_non_canonical_header_is_rejected(self, people):
        alice_sign, _ = people["alice"]
        _, bob_enc = people["bob"]
        blob = encrypt_pack(SECRET_TEXT, sender=alice_sign, recipients=[bob_enc.public_key()])
        header, mac, body, sig = split(blob)
        pretty = cp.ENCRYPTED_MAGIC + json.dumps(header, indent=1).replace("\n", "").encode() + b"\n"
        tail = blob[blob.index(b"\nmac ") + 1:]
        with pytest.raises(PackError, match="canonical"):
            decrypt_pack(pretty + tail, identity=bob_enc)

    def test_arguments_are_checked(self, people):
        alice_sign, _ = people["alice"]
        with pytest.raises(ValueError, match="at least one"):
            encrypt_pack(SECRET_TEXT, sender=alice_sign)
        with pytest.raises(ValueError, match="12 characters"):
            encrypt_pack(SECRET_TEXT, sender=alice_sign, passphrase="short")
        with pytest.raises(PackError, match="Not an encrypted pack"):
            decrypt_pack(b"PK\x03\x04", passphrase="x" * 12)

    def test_scrypt_work_factor_is_bounded_on_reading(self, people):
        alice_sign, _ = people["alice"]
        blob = encrypt_pack(SECRET_TEXT, sender=alice_sign, passphrase="correct horse battery")
        header, mac, body, _ = split(blob)
        header["recipients"][0]["log_n"] = 30  # would need terabytes of memory
        forged = rebuild(header, mac, body, b"")
        forged += alice_sign.sign(cp._ENCRYPTED_CONTEXT + forged)
        with pytest.raises(PackError, match="damaged"):
            decrypt_pack(forged, passphrase="correct horse battery")

    def test_only_one_passphrase_stanza_is_read(self, people, monkeypatch):
        # Anyone can sign a file; 64 passphrase stanzas at the highest work factor would run 64 GiB-sized scrypts.
        mallory_sign, _ = people["dave"]
        blob = encrypt_pack(SECRET_TEXT, sender=mallory_sign, passphrase="correct horse battery")
        header, mac, body, _ = split(blob)
        header["recipients"] += [dict(header["recipients"][0], log_n=20) for _ in range(3)]
        forged = rebuild(header, mac, body, b"")
        forged += mallory_sign.sign(cp._ENCRYPTED_CONTEXT + forged)
        monkeypatch.setattr(cp, "_scrypt_key", lambda *a: pytest.fail("scrypt ran for a damaged header"))
        with pytest.raises(PackError, match="damaged"):
            decrypt_pack(forged, passphrase="correct horse battery")

    def test_encrypted_signed_pack_end_to_end(self, people, tmp_path):
        alice_sign, _ = people["alice"]
        _, bob_enc = people["bob"]
        root = make_tree(tmp_path / "t")
        manifest = manifest_of(root, kind="knowledge", id="team-notes")
        pack = write_pack(root, manifest, sign_manifest(manifest, alice_sign))
        blob = encrypt_pack(pack, sender=alice_sign, recipients=[bob_enc.public_key()])
        opened = decrypt_pack(blob, identity=bob_enc, trusted_senders=[alice_sign.public_key()])
        contents = read_pack(opened.data, tmp_path / "out", keys=[alice_sign.public_key()])
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
        signing, encryption = first.signing_key(), first.encryption_key()
        again = cp.UserKeys(secrets_store())  # a new process
        assert cp._raw_private(again.signing_key()) == cp._raw_private(signing)
        assert cp._raw_private(again.encryption_key()) == cp._raw_private(encryption)

    def test_public_keys_export_as_text(self, secrets_store):
        keys = cp.UserKeys(secrets_store())
        text = keys.export_public()
        parsed = cp.parse_public_keys(text)
        assert [cp._raw_public(k) for k in parsed] == [cp._raw_public(keys.signing_key().public_key()),
                                                       cp._raw_public(keys.encryption_key().public_key())]
        assert cp.key_id(keys.signing_key().public_key()) in text
        blob = encrypt_pack(SECRET_TEXT, sender=keys.signing_key(), recipients=[parsed[1]])
        assert decrypt_pack(blob, identity=keys.encryption_key(), trusted_senders=[parsed[0]]).data == SECRET_TEXT

    def test_no_private_key_on_disk_in_plaintext(self, secrets_store, tmp_path):
        keys = cp.UserKeys(secrets_store())
        privates = [cp._raw_private(keys.signing_key()), cp._raw_private(keys.encryption_key())]
        forms = []
        for raw in privates:
            forms += [raw, raw.hex().encode(), base64.b64encode(raw), base64.b64encode(raw).rstrip(b"="),
                      base64.urlsafe_b64encode(raw).rstrip(b"=")]
        files = [p for p in tmp_path.rglob("*") if p.is_file()]
        assert any(p.name == "secrets.store" for p in files)
        for path in files:
            content = path.read_bytes()
            assert not any(form in content for form in forms), f"private key readable in {path}"

    def test_damaged_stored_key_is_never_replaced(self, secrets_store):
        sm = secrets_store()
        sm.set_secret(cp.UserKeys.SIGNING_SECRET, "not a key")
        with pytest.raises(PackError, match="damaged"):
            cp.UserKeys(sm).signing_key()
        assert sm.get_secret(cp.UserKeys.SIGNING_SECRET) == "not a key"

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

    def test_public_key_text_round_trip(self):
        for key in (Ed25519PrivateKey.generate().public_key(), X25519PrivateKey.generate().public_key()):
            assert cp._raw_public(cp.load_public_key(cp.public_key_text(key))) == cp._raw_public(key)
        with pytest.raises(PackError):
            cp.load_public_key("ssh-ed25519 AAAA")


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
