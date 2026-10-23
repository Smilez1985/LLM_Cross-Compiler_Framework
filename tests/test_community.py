"""Community path: the swarm upload is gone and build-context staging never follows links."""

import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT
from orchestrator.Core.community_manager import CommunityManager
from orchestrator.Core.community_pack import PackError
from orchestrator.utils.build_context import stage_build_context

POSIX = os.name == "posix"
needs_links = pytest.mark.skipif(not POSIX, reason="symbolic links need privileges on Windows")


def product_sources():
    return [p for p in (REPO_ROOT / "orchestrator").rglob("*.py") if "__pycache__" not in p.parts]


class TestSwarmRemoval:

    def test_no_module_references_the_xor_cipher_or_the_upload(self):
        for path in product_sources():
            text = path.read_text(encoding="utf-8")
            for name in ("SwarmCipher", "upload_knowledge_to_swarm", "SWARM_REPO_URL"):
                assert name not in text, f"{name} in {path}"

    def test_no_code_builds_a_url_with_credentials(self):
        credential_url = re.compile(r"://\{[^}]+\}:\{[^}]+\}@|://[\"']?\s*\+\s*\w+\s*\+\s*[\"']:")
        for path in product_sources():
            assert not credential_url.search(path.read_text(encoding="utf-8")), path

    def test_no_swarm_cli_command(self):
        from orchestrator.cli import cli
        assert "swarm" not in cli.commands

    def test_hub_has_no_upload_and_never_asks_for_a_token(self, qapp):
        from orchestrator.gui import community_hub
        source = Path(community_hub.__file__).read_text(encoding="utf-8")
        assert "QInputDialog" not in source and "github_token" not in source and "set_secret" not in source
        window = community_hub.CommunityHubWindow(SimpleNamespace(community_manager=None))
        assert not hasattr(window, "upload_btn")
        window.close()

    def test_old_swarm_packets_are_skipped(self, tmp_path):
        ingested = []
        rag = SimpleNamespace(ingest_document=lambda *a: ingested.append(a) or True)
        framework = SimpleNamespace(info=SimpleNamespace(installation_path=tmp_path),
                                    get_component=lambda name: rag if name == "rag_manager" else None)
        cm = CommunityManager(framework)
        cm.knowledge_dir.mkdir(parents=True)
        (cm.knowledge_dir / "swarm_old.json").write_text('{"swarm_encrypted": true, "payload": "x", "hash": "y"}')
        (cm.knowledge_dir / "plain.json").write_text('[{"source": "s", "content": "c", "metadata": {}}]')
        # DECISIONS round 23 item 1: a loose file is never imported unsorted; it is sorted first, through the checks
        assert cm.sync_knowledge_base() == 0 and ingested == []
        with pytest.raises(PackError, match="old 'swarm' format"):
            cm.prepare_sort("swarm_old.json", topic="old")
        cm.finish_install(cm.prepare_sort("plain.json", topic="notes"))
        assert ingested[0][:2] == ("s", "c")
        assert [item.label for item in cm.loose_knowledge()] == ["swarm_old.json"]

    def test_knowledge_export_is_scrubbed(self, tmp_path, monkeypatch):
        monkeypatch.setattr("getpass.getuser", lambda: "jenny")
        records = [{"source": "http://10.1.2.3/docs?token=hf_aBcDeFgHiJkLmNoPqRsTuVwXyZ123456",
                    "content": "see /home/jenny/notes and C:\\Users\\jenny\\probe.txt",
                    "metadata": {"title": "jenny's board", "licence": "CC-BY-4.0"}}]   # full text, attribution
        rag = SimpleNamespace(export_records=lambda: iter(records))
        framework = SimpleNamespace(info=SimpleNamespace(installation_path=tmp_path),
                                    get_component=lambda name: rag if name == "rag_manager" else None)
        exported = Path(CommunityManager(framework).export_knowledge_base("t")).read_text(encoding="utf-8")
        for leaked in ("10.1.2.3", "hf_aBcDeF", "jenny"):
            assert leaked not in exported


# Installing community packs (quarantine, validators, provenance, links, id traversal): tests/test_zoo.py.


# --- Build context ----------------------------------------------------------------------------------------

@pytest.fixture
def target(tmp_path):
    target = tmp_path / "targets" / "Demo"
    (target / "modules").mkdir(parents=True)
    (target / "scripts").mkdir()
    (target / "Dockerfile").write_text("FROM debian:bookworm-slim\n")
    (target / "modules" / "build.sh").write_text("echo\n")
    app = tmp_path / "app"
    app.mkdir()
    (app / "entrypoint.sh").write_text("#!/bin/sh\n")
    return target


class TestBuildContext:

    @needs_links
    def test_links_are_not_staged(self, target, tmp_path):
        secret = tmp_path / "secret.txt"
        secret.write_text("PRIVATE")
        (target / "modules" / "key.txt").symlink_to(secret)
        (target / "scripts" / "home").symlink_to(tmp_path, target_is_directory=True)
        context = tmp_path / "ctx"
        stage_build_context(target, tmp_path / "app", context)
        assert (context / "modules" / "build.sh").is_file()
        assert not os.path.lexists(context / "modules" / "key.txt")
        assert not os.path.lexists(context / "scripts" / "home")
        assert "PRIVATE" not in "".join(p.read_text() for p in context.rglob("*") if p.is_file())

    @needs_links
    def test_linked_module_folder_is_not_staged(self, target, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "leak.sh").write_text("secret")
        (target / "scripts").rmdir()
        (target / "scripts").symlink_to(outside, target_is_directory=True)
        context = tmp_path / "ctx"
        stage_build_context(target, tmp_path / "app", context)
        assert (context / "scripts").is_dir() and list((context / "scripts").iterdir()) == []

    @needs_links
    def test_linked_dockerfile_is_refused(self, target, tmp_path):
        real = tmp_path / "Dockerfile.real"
        real.write_text("FROM debian:bookworm-slim\n")
        (target / "Dockerfile").unlink()
        (target / "Dockerfile").symlink_to(real)
        with pytest.raises(ValueError, match="link"):
            stage_build_context(target, tmp_path / "app", tmp_path / "ctx")

    @pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs FIFOs")
    def test_special_files_are_not_staged(self, target, tmp_path):
        os.mkfifo(target / "modules" / "pipe")
        context = tmp_path / "ctx"
        stage_build_context(target, tmp_path / "app", context)
        assert not os.path.lexists(context / "modules" / "pipe")
