"""The knowledge base's model downloads follow the one disk rule, and the cache records are moved in with
utils/atomic.replace_file.

- fastembed's models (hf_hub.fetch_snapshot, through rag_manager.fetch_model_files) take a reservation in the per-user
  ReservationLedger while they download, record what landed and release it when the fetch ends; with too little room
  they refuse before any file is fetched, with the disk rule's text.
- The Builder's record of a complete copy (local_models.write_complete), HubFetcher's last move of a file and the
  `refs/main` record of fetch_snapshot go through replace_file, which tries again for a moment on Windows while a
  reader holds the target.
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from test_hf_tokens import COMMIT, E5, FILES, captured, hub  # noqa: E402,F401 - fixtures


def dense(tmp_path, ledger):
    from orchestrator.Core.rag_manager import create_dense_embedder
    from orchestrator.rag.embeddings import MODEL_REGISTRY
    return create_dense_embedder(MODEL_REGISTRY[E5], tmp_path / "cache", lambda: False, ledger=ledger)


class TestTheDiskRule:

    def test_the_download_holds_a_reservation_until_it_lands(self, hub, captured, tmp_path, monkeypatch):
        from orchestrator.Core import hf_hub
        from orchestrator.Core.disk_plan import ReservationLedger
        ledger = ReservationLedger(tmp_path / "state")
        seen = []
        real = hf_hub.HubFetcher.fetch_file

        def watched(self, repo, commit, file, dest, **kwargs):
            seen.append([(e["owner"], e["label"], sum(e["bytes"].values()), sum((e.get("landed") or {}).values()))
                         for e in ledger.entries()])
            return real(self, repo, commit, file, dest, **kwargs)
        monkeypatch.setattr(hf_hub.HubFetcher, "fetch_file", watched)
        dense(tmp_path, ledger).prepare()
        total = sum(len(data) for data in FILES.values())
        assert seen and all(len(entries) == 1 for entries in seen), "one reservation while it downloads"
        owner, label, reserved, landed = seen[0][0]
        assert (owner, reserved, landed) == ("knowledge", total, 0) and label.endswith("multilingual-e5-small")
        assert seen[-1][0][3] > 0, "the files that arrived are recorded as landed"
        assert ledger.entries() == [], "released when the fetch ends"

    def test_without_room_it_refuses_with_the_disk_rule_before_any_file(self, hub, captured, tmp_path, monkeypatch):
        from orchestrator.Core import disk_plan
        from orchestrator.Core.disk_plan import ReservationLedger
        from orchestrator.rag.errors import EmbeddingError
        gib = 1024 ** 3
        monkeypatch.setattr(disk_plan.shutil, "disk_usage",
                            lambda path: SimpleNamespace(total=100 * gib, used=100 * gib - 1000, free=1000))
        ledger = ReservationLedger(tmp_path / "state")
        with pytest.raises(EmbeddingError, match="Not enough disk space for the download"):
            dense(tmp_path, ledger).prepare()
        assert hub.files == [], "nothing was downloaded"
        assert ledger.entries() == []

    def test_a_failed_download_releases_its_reservation(self, hub, captured, tmp_path, monkeypatch):
        from orchestrator.Core import hf_hub
        from orchestrator.Core.disk_plan import ReservationLedger
        from orchestrator.rag.errors import EmbeddingError
        ledger = ReservationLedger(tmp_path / "state")

        def broken(self, *args, **kwargs):
            raise hf_hub.HubError("the connection dropped")
        monkeypatch.setattr(hf_hub.HubFetcher, "fetch_file", broken)
        with pytest.raises(EmbeddingError, match="connection dropped"):
            dense(tmp_path, ledger).prepare()
        assert ledger.entries() == []

    def test_the_knowledge_base_hands_its_ledger_to_the_factories(self, monkeypatch, tmp_path):
        from orchestrator.Core import rag_manager as rm
        framework = SimpleNamespace(config=SimpleNamespace(user_state_dir=tmp_path / "state"))
        manager = rm.RAGManager.__new__(rm.RAGManager)
        manager.framework = framework
        ledger = manager._disk_ledger()
        assert ledger is not None and Path(ledger.path).parent == tmp_path / "state"
        manager.framework = SimpleNamespace(config=SimpleNamespace())
        assert manager._disk_ledger() is None


class TestTheRecordsAreMovedInWithReplaceFile:

    @pytest.fixture
    def busy_once(self, monkeypatch):
        """os.replace refuses once, as Windows does while a reader holds the target; replace_file tries again."""
        from orchestrator.utils import atomic
        monkeypatch.setattr(atomic, "REPLACE_RETRIES", 3)
        monkeypatch.setattr("time.sleep", lambda s: None)
        real, state = os.replace, {"left": 1}

        def refuse_once(src, dst):
            if state["left"]:
                state["left"] -= 1
                raise PermissionError(13, "The process cannot access the file")
            return real(src, dst)
        monkeypatch.setattr(os, "replace", refuse_once)
        return state

    def test_the_record_of_a_complete_copy(self, tmp_path, busy_once):
        from orchestrator.Core.local_models import marker_of, write_complete
        folder = tmp_path / "copy"
        folder.mkdir()
        write_complete(folder, "acme/tiny", COMMIT, {"config.json": 2})
        assert busy_once["left"] == 0 and marker_of(folder).is_file()

    def test_the_refs_main_record_of_a_fetched_model(self, hub, captured, tmp_path, busy_once):
        from orchestrator.Core.hf_hub import fetch_snapshot
        folder = fetch_snapshot(tmp_path / "cache", E5, list(FILES)[:1])
        assert busy_once["left"] == 0 and folder is not None
        refs = tmp_path / "cache" / "models--intfloat--multilingual-e5-small" / "refs" / "main"
        assert refs.read_text() == COMMIT
