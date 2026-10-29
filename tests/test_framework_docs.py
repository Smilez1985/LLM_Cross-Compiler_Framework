"""The framework's own documents in the knowledge base (DECISIONS round 8, N12): an explicit knowledge action in the
GUI (Tools > Index the Framework's Documentation...), the CLI (`llm-cli knowledge index-docs`) and the MCP tool
`index_framework_docs` (asked every time). The documentation first, the code only when asked for; a snapshot first, as
before a Deep Ingest; Ditto then finds the framework's internals in search_knowledge (kind docs). Nothing indexes
them by itself, and a knowledge pack export never carries them."""

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import REPO_ROOT
from orchestrator.Core.rag_manager import MAX_CODE_FILE_BYTES, framework_documents
from orchestrator.tools.registry import ToolContext, ToolError
from test_knowledge_tools import fill, make_rag  # noqa: F401  (make_rag: the fixture)


def framework_tree(root: Path) -> Path:
    (root / "docs").mkdir(parents=True)
    (root / "README.md").write_text("# Framework\n\nThe framework converts models for the NPU.\n", encoding="utf-8")
    (root / "README_DE.md").write_text("# Framework\n\nDas Framework wandelt Modelle um.\n", encoding="utf-8")
    (root / "docs" / "Features.md").write_text("# Features\n\nA probe value reaches build.sh as an environment "
                                               "variable.\n", encoding="utf-8")
    (root / "orchestrator" / "Core").mkdir(parents=True)
    (root / "orchestrator" / "Core" / "builder.py").write_text("def build():\n    return 'npu'\n", encoding="utf-8")
    (root / "orchestrator" / "__pycache__").mkdir()
    (root / "orchestrator" / "__pycache__" / "x.py").write_text("cached = 1\n", encoding="utf-8")
    (root / "orchestrator" / ".hidden").mkdir()
    (root / "orchestrator" / ".hidden" / "y.py").write_text("hidden = 1\n", encoding="utf-8")
    (root / "orchestrator" / "huge.py").write_text("x = 1\n" * (MAX_CODE_FILE_BYTES // 6 + 10), encoding="utf-8")
    (root / ".claude" / "worktrees").mkdir(parents=True)
    (root / ".claude" / "worktrees" / "z.md").write_text("# not the framework\n", encoding="utf-8")
    (root / "data").mkdir()
    (root / "data" / "calibration.json").write_text("{}", encoding="utf-8")
    return root


def rel(paths, root):
    return [p.relative_to(root).as_posix() for p in paths]


GB = 1000 ** 3


def disk(free_gb):
    """A disk of 100 GB with `free_gb` free (the reserve kept free is 2 GiB)."""
    return lambda path: SimpleNamespace(total=100 * GB, used=(100 - free_gb) * GB, free=int(free_gb * GB))


def with_a_snapshot(folder: Path, size: int = 50 * 1000 ** 2) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / "pre_ingest_1.jsonl.gz", "wb") as f:
        f.truncate(size)   # a sparse file: the size counts, nothing is written
    return folder


class TestWhatIsRead:

    def test_the_documentation_first_and_the_code_only_when_asked(self, tmp_path):
        root = framework_tree(tmp_path / "fw")
        docs, code = framework_documents(root)
        assert rel(docs, root) == ["README.md", "README_DE.md", "docs/Features.md"] and code == []
        docs, code = framework_documents(root, include_code=True)
        assert rel(code, root) == ["orchestrator/Core/builder.py"], "no cache, hidden folder, data or oversized file"

    def test_the_order_is_the_same_on_every_system(self, tmp_path, monkeypatch):
        """Windows compares paths without letter case, which put docs/Features.md before README.md and changed the
        order of the progress and of `not_indexed`; the files are sorted by their names, with letter case, on every
        system. Simulated here with paths that compare as on Windows."""
        from orchestrator.Core import rag_manager

        class WindowsOrder(type(Path())):
            def __lt__(self, other):
                return [part.lower() for part in self.parts] < [part.lower() for part in other.parts]

            def __gt__(self, other):
                return [part.lower() for part in self.parts] > [part.lower() for part in other.parts]
        root = framework_tree(tmp_path / "fw")
        (root / "Zeta.sh").write_text("true\n", encoding="utf-8")
        (root / "alpha.sh").write_text("true\n", encoding="utf-8")
        (root / "configs").mkdir()
        for name in ("a.yml", "B.yml"):
            (root / "configs" / name).write_text("x: 1\n", encoding="utf-8")
        assert sorted([WindowsOrder("README.md"), WindowsOrder("docs")]) == [WindowsOrder("docs"),
                                                                             WindowsOrder("README.md")]
        monkeypatch.setattr(rag_manager, "Path", WindowsOrder)
        docs, code = framework_documents(root, include_code=True)
        assert rel(docs, root) == ["README.md", "README_DE.md", "docs/Features.md"]
        assert rel(code, root) == ["Zeta.sh", "alpha.sh", "orchestrator/Core/builder.py", "configs/B.yml",
                                   "configs/a.yml"]

    def test_only_what_the_framework_ships_never_the_persons_files(self, tmp_path):
        """Of targets/ only the template and the official targets without a pack's provenance that no AI made; of
        configs/ only the files at its top, never the person's settings (config.yml, framework.yml) nor the folders
        the advanced mode keeps there (secrets, audit, ...): they can hold host paths, and third-party code keeps its
        own licence."""
        root = framework_tree(tmp_path / "fw")
        official = "metadata:\n  name: X\ncapabilities:\n  support_level: official\n"
        targets = {"_template": "metadata:\n  name: T\n", "Shipped": official, "Pack": official,
                   "Mine": "metadata:\n  name: M\n  provenance: ai-generated\n" + official.split("\n", 2)[2],
                   "Typed": "metadata:\n  name: Y\n"}
        for name, yml in targets.items():
            (root / "targets" / name).mkdir(parents=True)
            (root / "targets" / name / "target.yml").write_text(yml, encoding="utf-8")
            (root / "targets" / name / "build.sh").write_text("echo build\n", encoding="utf-8")
        (root / "targets" / "Pack" / ".lcf-provenance.json").write_text("{}", encoding="utf-8")
        configs = root / "configs"
        (configs / "secrets").mkdir(parents=True)
        (configs / "project_sources.yml").write_text("llama_cpp: {}\n", encoding="utf-8")
        (configs / "config.yml").write_text("models_dir: ~/models\n", encoding="utf-8")
        (configs / "framework.yml").write_text("output_dir: ~/out\n", encoding="utf-8")
        (configs / "secrets" / "keys.json").write_text("{}", encoding="utf-8")
        (configs / ".hidden.yml").write_text("x: 1\n", encoding="utf-8")
        _, code = framework_documents(root, include_code=True)
        names = rel(code, root)
        assert sorted(n for n in names if n.startswith(("targets/", "configs/"))) == [
            "configs/project_sources.yml", "targets/Shipped/build.sh", "targets/Shipped/target.yml",
            "targets/_template/build.sh", "targets/_template/target.yml"]

    @pytest.mark.skipif(os.name == "nt", reason="symbolic links need privileges on Windows")
    def test_links_are_never_followed(self, tmp_path):
        root = framework_tree(tmp_path / "fw")
        secret = tmp_path / "outside.md"
        secret.write_text("# outside\n", encoding="utf-8")
        (root / "docs" / "Linked.md").symlink_to(secret)
        (root / "orchestrator" / "linked").symlink_to(tmp_path, target_is_directory=True)
        docs, code = framework_documents(root, include_code=True)
        assert "docs/Linked.md" not in rel(docs, root) and all("linked" not in p for p in rel(code, root))

    def test_the_real_installation_has_its_documentation_first(self):
        docs, code = framework_documents(REPO_ROOT)
        names = rel(docs, REPO_ROOT)
        assert {"README.md", "README_DE.md", "docs/Features.md", "docs/Features_DE.md"} <= set(names)
        assert code == [] and not any(n.startswith((".", "tests/")) for n in names)


class TestTheIndex:

    def test_the_documentation_is_indexed_with_a_snapshot_first(self, make_rag, tmp_path):  # noqa: F811
        root = framework_tree(tmp_path / "fw")
        rag = make_rag()
        fill(rag)   # a knowledge base with content: the snapshot is taken before anything is written
        seen = []
        result = rag.ingest_codebase(root, progress=lambda n, total, name: seen.append((n, total, name)))
        assert result["success"] and result["count"] == 3 and result["files"] == 3 and result["chunks"] >= 3
        assert result["snapshot"] and result["snapshot"] in rag.list_snapshots()
        assert [s[2] for s in seen] == ["README.md", "README_DE.md", "docs/Features.md"]
        sources = {s["source"] for s in rag.list_sources()}
        assert {"code:README.md", "code:docs/Features.md"} <= sources
        assert "code:orchestrator/Core/builder.py" not in sources, "the code only when asked for"
        hits = rag.search("probe value build.sh environment variable", limit=3)
        assert any(h.source == "code:docs/Features.md" for h in hits)

    def test_the_code_is_indexed_when_asked_for(self, make_rag, tmp_path):  # noqa: F811
        root = framework_tree(tmp_path / "fw")
        rag = make_rag()
        result = rag.ingest_codebase(root, include_code=True)
        assert result["count"] == 4
        assert "code:orchestrator/Core/builder.py" in {s["source"] for s in rag.list_sources()}

    def test_a_stop_keeps_what_is_stored(self, make_rag, tmp_path):  # noqa: F811
        import threading
        root = framework_tree(tmp_path / "fw")
        rag = make_rag()
        stop = threading.Event()
        result = rag.ingest_codebase(root, stop=stop, progress=lambda n, total, name: stop.set() if n == 2 else None)
        assert result["success"] and result["stopped"] and result["count"] == 2

    def test_a_snapshot_that_cannot_be_written_stops_the_index(self, make_rag, tmp_path, monkeypatch):  # noqa: F811
        """No undo point, no index: when the snapshot of a knowledge base that holds excerpts fails (a full disk),
        nothing is indexed and the result says why; there is no "continue anyway"."""
        from orchestrator.rag.store import KnowledgeStore
        root = framework_tree(tmp_path / "fw")
        rag = make_rag()
        fill(rag)

        def full(self, path):
            raise OSError(28, "No space left on device")
        monkeypatch.setattr(KnowledgeStore, "export_file", full)
        result = rag.ingest_codebase(root)
        assert not result["success"] and result["snapshot_failed"] and result["count"] == 0
        assert "snapshot" in result["message"] and "nothing was indexed" in result["message"]
        assert "code:README.md" not in {s["source"] for s in rag.list_sources()}

    def test_files_that_were_not_indexed_are_named_with_the_reason(self, make_rag, tmp_path,  # noqa: F811
                                                                  monkeypatch):
        """The result never hides a file it read but did not store: each one is named with why (an empty file, an
        error's kind), in the message and in `not_indexed`."""
        root = framework_tree(tmp_path / "fw")
        (root / "docs" / "Empty.md").write_text("  \n", encoding="utf-8")
        rag = make_rag()
        real = rag._ingest

        def failing(source, content, metadata, replace):
            if source == "code:README_DE.md":
                raise ValueError("cannot embed")
            return real(source, content, metadata, replace)
        monkeypatch.setattr(rag, "_ingest", failing)
        result = rag.ingest_codebase(root)
        assert result["success"] and result["count"] == 2 and result["files"] == 4
        assert result["not_indexed"] == [{"file": "README_DE.md", "reason": "ValueError"},
                                         {"file": "docs/Empty.md", "reason": "empty"}]
        assert "not indexed: README_DE.md (ValueError), docs/Empty.md (empty)" in result["message"]

    def test_indexing_again_replaces_and_never_doubles(self, make_rag, tmp_path):  # noqa: F811
        root = framework_tree(tmp_path / "fw")
        rag = make_rag()
        rag.ingest_codebase(root)
        first = rag.chunk_count()
        rag.ingest_codebase(root)
        assert rag.chunk_count() == first

    def test_nothing_indexes_the_framework_documents_by_itself(self):
        callers = [p for p in (REPO_ROOT / "orchestrator").rglob("*.py")
                   if "ingest_codebase(" in p.read_text(encoding="utf-8") and p.name != "rag_manager.py"]
        assert sorted(p.relative_to(REPO_ROOT).as_posix() for p in callers) == [
            "orchestrator/cli.py", "orchestrator/gui/framework_docs_action.py",
            "orchestrator/tools/tools_framework_docs.py"]


class TestTheTool:

    def registry(self, tmp_path, rag, root, disk_usage=None):
        from orchestrator.Core.job_service import JobService
        from orchestrator.tools.audit import AuditLog
        from orchestrator.tools.framework_tools import register_framework_tools
        from orchestrator.tools.policy import PolicyStore, ToolPolicy
        from orchestrator.tools.registry import ToolRegistry
        if rag is not None:
            rag.app_root = root
        self.jobs = JobService()
        framework = SimpleNamespace(build_service=SimpleNamespace(disk_usage=disk_usage),
                                    ensure_rag_manager=lambda: rag, job_service=self.jobs)
        registry = register_framework_tools(ToolRegistry(PolicyStore(tmp_path / "policy"),
                                                         AuditLog(tmp_path / "a.db")), framework)
        registry.policy_store.replace(ToolPolicy(configured=True, reviewed=[s.name for s in registry.list()]))
        return registry

    def test_it_asks_every_time_and_writes_the_knowledge_base(self, make_rag, tmp_path):  # noqa: F811
        from orchestrator.tools.policy import Decision, ToolTier
        registry = self.registry(tmp_path, make_rag(), framework_tree(tmp_path / "fw"))
        spec = registry.get("index_framework_docs")
        assert spec.tier == ToolTier.LOCAL_WRITE
        assert spec.default_decision == Decision.ASK and spec.max_decision == Decision.ASK
        with pytest.raises(ToolError):  # no dialog: denied, nothing indexed
            registry.call("index_framework_docs", {}, ToolContext("ditto"))

    def test_the_question_names_counts_and_the_snapshot_never_a_path(self, make_rag, tmp_path):  # noqa: F811
        from orchestrator.tools.registry import current_question
        root = framework_tree(tmp_path / "fw")
        rag = make_rag()
        registry = self.registry(tmp_path, rag, root)
        shown = []
        out = registry.call("index_framework_docs", {"include_code": True},
                            ToolContext("ditto", confirm=lambda s, i: shown.append(current_question()) or True))
        question = shown[0]
        assert "3 documentation files" in question and "1 code and configuration files" in question
        assert "snapshot is taken first" in question and str(tmp_path) not in question
        assert out.documentation_files == 3 and out.code_files == 1 and out.job_id.startswith("job_")
        deadline = time.monotonic() + 30
        job = self.jobs.get(out.job_id)
        while not job.finished and time.monotonic() < deadline:
            job = self.jobs.wait(out.job_id, 1)
        assert job.state == "done", job.error
        assert job.result["files_indexed"] == 4 and str(tmp_path) not in str(job.result)
        status = registry.call("get_job_status", {"job_id": out.job_id}, ToolContext("ditto"))
        assert status.kind == "docs_index" and status.state == "done", "an AI can follow the job"
        assert "code:orchestrator/Core/builder.py" in {s["source"] for s in rag.list_sources()}
        ends = [e for e in registry.audit.entries(20) if e["tool"] == "index_framework_docs"
                and "job end" in str(e.get("detail", ""))]
        assert ends and "4 of 4 files indexed" in ends[0]["detail"]

    def test_a_switched_off_knowledge_base_is_refused_with_its_blocker(self, tmp_path):
        registry = self.registry(tmp_path, None, tmp_path)
        with pytest.raises(ToolError) as e:
            registry.call("index_framework_docs", {}, ToolContext("ditto", confirm=lambda s, i: True))
        assert e.value.blocker == "knowledge_off" and e.value.code == "not_supported"

    def test_no_room_for_the_snapshot_refuses_before_any_question(self, make_rag, tmp_path):  # noqa: F811
        rag = make_rag()
        with_a_snapshot(rag.snapshot_dir)
        registry = self.registry(tmp_path, rag, framework_tree(tmp_path / "fw"), disk_usage=disk(1))
        asked = []
        with pytest.raises(ToolError) as e:
            registry.call("index_framework_docs", {},
                          ToolContext("ditto", confirm=lambda s, i: asked.append(s) or True))
        # The same blocker as Deep Ingest, a restore and a pack import (eighth round, answer 2): the person's action is
        # the snapshot list, not "free some disk space"
        assert e.value.blocker == "snapshot_no_room" and asked == [], "no question, no 'continue anyway'"
        assert "Knowledge Snapshots" in e.value.next and "Knowledge Snapshots" in e.value.person_action
        assert "knowledge snapshots disk" in str(e.value) and str(tmp_path) not in str(e.value)
        assert self.jobs.list() == [] and not any(s["source"].startswith("code:") for s in rag.list_sources())

    def test_little_room_left_is_a_situation_warning(self, make_rag, tmp_path):  # noqa: F811
        rag = make_rag()
        with_a_snapshot(rag.snapshot_dir)
        registry = self.registry(tmp_path, rag, framework_tree(tmp_path / "fw"), disk_usage=disk(3))
        spec = registry.get("index_framework_docs")
        facts = registry.look_up(spec, registry.validate_input(spec, {}), ToolContext("ditto"))
        assert [(w.code, w.kind) for w in facts.warnings] == [("disk_tight", "situation")]
        (tmp_path / "roomy").mkdir()
        roomy = self.registry(tmp_path / "roomy", rag, framework_tree(tmp_path / "fw2"), disk_usage=disk(90))
        spec = roomy.get("index_framework_docs")
        assert roomy.look_up(spec, roomy.validate_input(spec, {}), ToolContext("ditto")).warnings == ()

    def test_the_one_time_model_download_is_in_the_question_with_its_warning(self, make_rag, tmp_path):  # noqa: F811
        """next-fixes 119: indexing loads the knowledge base's models first; when they are not on this computer the
        question says so with the declared situation warning (the models already there: a fact, no warning)."""
        rag = make_rag()
        rag.embedding_download = lambda: (True, 2.3)
        registry = self.registry(tmp_path, rag, framework_tree(tmp_path / "fw"), disk_usage=disk(90))
        spec = registry.get("index_framework_docs")
        facts = registry.look_up(spec, registry.validate_input(spec, {}), ToolContext("ditto"))
        assert [(w.code, w.kind, dict(w.numbers)) for w in facts.warnings] == [
            ("embedding_model_download", "situation", {"size_gb": 2.3})]
        assert any(f.label == "Embedding model" and "downloaded once first" in f.value for f in facts.facts)
        rag.embedding_download = lambda: (False, 0.0)
        facts = registry.look_up(spec, registry.validate_input(spec, {}), ToolContext("ditto"))
        assert facts.warnings == () and any("nothing is downloaded" in f.value for f in facts.facts)

    def test_without_an_earlier_snapshot_the_knowledge_base_itself_gives_the_size(self, make_rag,  # noqa: F811
                                                                                  tmp_path):
        """The first snapshot has no earlier one to go by: the knowledge base's own storage gives its size, so a
        disk without room refuses before any question here too (DECISIONS round 8, answer 2)."""
        rag = make_rag()
        fill(rag)
        assert not list(Path(rag.snapshot_dir).glob("*.jsonl.gz")), "no earlier snapshot"
        registry = self.registry(tmp_path, rag, framework_tree(tmp_path / "fw"), disk_usage=disk(1))
        asked = []
        with pytest.raises(ToolError) as e:
            registry.call("index_framework_docs", {},
                          ToolContext("ditto", confirm=lambda s, i: asked.append(s) or True))
        assert e.value.blocker == "snapshot_no_room" and asked == [] and self.jobs.list() == []

    def test_a_failed_snapshot_fails_the_job_and_says_why(self, make_rag, tmp_path, monkeypatch):  # noqa: F811
        from orchestrator.rag.store import KnowledgeStore
        rag = make_rag()
        fill(rag)
        registry = self.registry(tmp_path, rag, framework_tree(tmp_path / "fw"), disk_usage=disk(90))
        monkeypatch.setattr(KnowledgeStore, "export_file",
                            lambda self, path: (_ for _ in ()).throw(OSError(28, f"No space left: {path}")))
        out = registry.call("index_framework_docs", {}, ToolContext("ditto", confirm=lambda s, i: True))
        deadline = time.monotonic() + 30
        job = self.jobs.get(out.job_id)
        while not job.finished and time.monotonic() < deadline:
            job = self.jobs.wait(out.job_id, 1)
        assert job.state == "failed"
        assert "snapshot" in str(job.error) and "nothing was indexed" in str(job.error)
        assert str(tmp_path) not in str(job.error), "the AI gets no host path"
        assert "code:README.md" not in {s["source"] for s in rag.list_sources()}

    def test_a_snapshot_without_room_at_the_start_names_the_snapshot_list(self, make_rag, tmp_path,  # noqa: F811
                                                                         monkeypatch):
        """The disk filled up between the question and the job: the snapshot is refused for room (SnapshotNoRoom),
        nothing is indexed, and the job says so with the person's way out (the snapshot list), like the refusal
        before the question."""
        from orchestrator.Core.knowledge_snapshots import SnapshotNoRoom, SnapshotRoom
        rag = make_rag()
        fill(rag)
        registry = self.registry(tmp_path, rag, framework_tree(tmp_path / "fw"), disk_usage=disk(90))

        def no_room(*_a, **_k):
            raise SnapshotNoRoom(SnapshotRoom(estimate=5 * GB, folder_bytes=0, check=None), "the index")
        monkeypatch.setattr(rag, "create_snapshot", no_room)
        out = registry.call("index_framework_docs", {}, ToolContext("ditto", confirm=lambda s, i: True))
        deadline = time.monotonic() + 30
        job = self.jobs.get(out.job_id)
        while not job.finished and time.monotonic() < deadline:
            job = self.jobs.wait(out.job_id, 1)
        assert job.state == "failed" and "nothing was indexed" in str(job.error)
        assert "did not fit" in str(job.error) and "Knowledge Snapshots" in str(job.error)
        assert "code:README.md" not in {s["source"] for s in rag.list_sources()}

    def test_the_job_names_the_files_it_did_not_index(self, make_rag, tmp_path):  # noqa: F811
        root = framework_tree(tmp_path / "fw")
        (root / "docs" / "Empty.md").write_text("", encoding="utf-8")
        registry = self.registry(tmp_path, make_rag(), root)
        out = registry.call("index_framework_docs", {}, ToolContext("ditto", confirm=lambda s, i: True))
        deadline = time.monotonic() + 30
        job = self.jobs.get(out.job_id)
        while not job.finished and time.monotonic() < deadline:
            job = self.jobs.wait(out.job_id, 1)
        assert job.state == "done", job.error
        assert job.result["not_indexed"] == [{"file": "docs/Empty.md", "reason": "empty"}]
        ends = [e for e in registry.audit.entries(20) if e["tool"] == "index_framework_docs"
                and "job end" in str(e.get("detail", ""))]
        assert ends and "1 not indexed" in ends[0]["detail"]


class TestExportsAndDocs:

    def test_a_knowledge_export_never_carries_the_framework_documents(self, make_rag, tmp_path):  # noqa: F811
        from orchestrator.Core.community_manager import CommunityManager
        rag = make_rag()
        fill(rag)
        manager = CommunityManager.__new__(CommunityManager)
        manager.framework = SimpleNamespace(get_component=lambda name: rag if name == "rag_manager" else None)
        manager.logger = SimpleNamespace(error=lambda *a, **k: None)
        pages, left_out = manager._knowledge_pages()
        sources = {p["source"] for p in pages}
        assert "https://docs.example/npu" in sources and not any(s.startswith("code:") for s in sources)
        assert left_out == 1, "the pack page is counted as left out, as before"

    def test_the_docs_no_longer_claim_it_happens_by_itself(self):
        for name in ("docs/DESIGN.md", "docs/DESIGN_DE.md", "docs/Features.md", "docs/Features_DE.md"):
            text = (REPO_ROOT / name).read_text(encoding="utf-8")
            assert "index-docs" in text, name


def test_the_snapshot_check_uses_the_knowledge_bases_own_estimate(tmp_path):
    """The check before the index uses the estimate create_snapshot checks (rag.snapshot_estimate), so a knowledge
    base on a Qdrant server, which has no storage folder here, is checked too; only when no estimate can be read does
    the storage folder stand in."""
    from orchestrator.tools.tools_framework_docs import snapshot_space
    rag = SimpleNamespace(snapshot_dir=tmp_path / "snaps", storage_dir=None, snapshot_estimate=lambda: 5 * GB)
    (short,) = snapshot_space(rag, disk(3))
    assert not short.fits and short.needed == 5 * GB
    (roomy,) = snapshot_space(rag, disk(90))
    assert roomy.fits

    def unreadable():
        raise RuntimeError("the knowledge base is busy")
    storage = tmp_path / "storage"
    storage.mkdir()
    with open(storage / "collection.bin", "wb") as f:
        f.truncate(7 * 1000 ** 2)
    (fallback,) = snapshot_space(SimpleNamespace(snapshot_dir=tmp_path / "snaps", storage_dir=storage,
                                                 snapshot_estimate=unreadable), disk(90))
    assert fallback.needed == 7 * 1000 ** 2


def test_the_gui_action_asks_with_no_as_default_and_plain_text(qapp, tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QMessageBox, QWidget
    from orchestrator.gui import framework_docs_action as module
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "setHtml" not in source and "RichText" not in source and "setTextFormat(Qt.PlainText)" in source
    root = framework_tree(tmp_path / "fw")
    started = []
    rag = SimpleNamespace(app_root=root, snapshot_dir=with_a_snapshot(tmp_path / "snapshots"),
                          ingest_codebase=lambda **kw: started.append(kw) or {"success": True})
    window = QWidget()
    action = module.FrameworkDocsAction(window, SimpleNamespace(ensure_rag_manager=lambda: rag,
                                                                build_service=SimpleNamespace(disk_usage=disk(3))))
    asked = []

    def answer(box):
        asked.append((box.text(), box.defaultButton() is box.button(QMessageBox.No), box.textFormat()))
        return QMessageBox.No
    monkeypatch.setattr(QMessageBox, "exec", answer)
    assert action.run() is False and started == []
    text, no_default, fmt = asked[0]
    assert no_default and "3 files" in text and "snapshot" in text and str(tmp_path) not in text
    assert "Disk space for the snapshot: needs about 50 MB on the knowledge snapshots disk" in text
    assert "enough, but tight" in text, "little room left is said in the question"
    monkeypatch.setattr(QMessageBox, "exec", lambda box: (box.checkBox().setChecked(True), QMessageBox.Yes)[1])
    monkeypatch.setattr(module, "plain_box", lambda *a, **k: None)
    assert action.run() is True
    deadline = time.monotonic() + 5
    while not started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert started == [{"include_code": True}]
    window.deleteLater()


def test_the_cli_command_indexes_on_request_and_names_the_snapshot(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from orchestrator import cli
    root = framework_tree(tmp_path / "fw")
    calls = []

    def ingest(**kwargs):
        calls.append(kwargs)
        kwargs["progress"](1, 3, "README.md")
        return {"success": True, "message": "Indexed 3 of 3 documentation files (5 excerpts).",
                "snapshot": "pre_codebase_1", "count": 3}
    state = {"rag": SimpleNamespace(app_root=root, snapshot_dir=tmp_path / "snapshots", ingest_codebase=ingest)}

    def require(self):
        self.framework_manager = SimpleNamespace(ensure_rag_manager=lambda: state["rag"])
        return self
    monkeypatch.setattr(cli.FrameworkContext, "require_framework", require)
    result = CliRunner().invoke(cli.cli, ["-q", "knowledge", "index-docs"])
    assert result.exit_code == 0, result.output
    assert "Indexing 3 documentation files" in result.output and "pre_codebase_1" in result.output
    assert calls[0]["include_code"] is False
    result = CliRunner().invoke(cli.cli, ["-q", "knowledge", "index-docs", "--with-code"])
    assert result.exit_code == 0 and calls[1]["include_code"] is True and "1 code and configuration" in result.output
    state["rag"] = None
    result = CliRunner().invoke(cli.cli, ["-q", "knowledge", "index-docs"])
    assert result.exit_code == 1 and "knowledge base is off" in result.output


def test_the_gui_action_asks_nothing_without_room_for_the_snapshot(qapp, tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QMessageBox, QWidget
    from orchestrator.gui import framework_docs_action as module
    started, shown = [], []
    rag = SimpleNamespace(app_root=framework_tree(tmp_path / "fw"), snapshot_dir=with_a_snapshot(tmp_path / "snaps"),
                          ingest_codebase=lambda **kw: started.append(kw) or {"success": True})
    window = QWidget()
    action = module.FrameworkDocsAction(window, SimpleNamespace(ensure_rag_manager=lambda: rag,
                                                                build_service=SimpleNamespace(disk_usage=disk(1))))
    monkeypatch.setattr(QMessageBox, "exec", lambda box: shown.append(box.text()) or QMessageBox.Yes)
    monkeypatch.setattr(module, "plain_box", lambda parent, kind, title, text, **kw: shown.append((kind, text)))
    assert action.run() is False and started == []
    assert len(shown) == 1 and shown[0][0] == "warning", "only the warning, no question"
    assert "nothing is indexed" in shown[0][1] and "not enough" in shown[0][1] and str(tmp_path) not in shown[0][1]
    window.deleteLater()


def test_the_cli_command_refuses_without_room_for_the_snapshot(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from orchestrator import cli
    calls = []
    rag = SimpleNamespace(app_root=framework_tree(tmp_path / "fw"), snapshot_dir=with_a_snapshot(tmp_path / "snaps"),
                          ingest_codebase=lambda **kw: calls.append(kw) or {"success": True, "message": "x"})
    free = {"gb": 1}

    def require(self):
        self.framework_manager = SimpleNamespace(ensure_rag_manager=lambda: rag, build_service=SimpleNamespace(
            disk_usage=lambda path: disk(free["gb"])(path)))
        return self
    monkeypatch.setattr(cli.FrameworkContext, "require_framework", require)
    result = CliRunner().invoke(cli.cli, ["-q", "knowledge", "index-docs"])
    assert result.exit_code == 1 and calls == []
    assert "nothing was indexed" in result.output and str(tmp_path) not in result.output
    free["gb"] = 3
    result = CliRunner().invoke(cli.cli, ["-q", "knowledge", "index-docs"])
    assert result.exit_code == 0 and len(calls) == 1 and "enough, but tight" in result.output


def test_the_cli_usage_says_what_with_code_really_reads():
    """CLI_Usage (EN and DE) once said --with-code reads "the install scripts", but only the shell scripts in the top
    folder are read (install.bat is not). The docs and the reading code agree."""
    _docs, code = framework_documents(REPO_ROOT, include_code=True)
    names = {p.relative_to(REPO_ROOT).as_posix() for p in code}
    assert "install.sh" in names and "install.bat" not in names
    assert MAX_CODE_FILE_BYTES == 512 * 1024
    for doc, phrases in (("docs/CLI_Usage.md", ("not `install.bat`", "Files above 512 KB", "`Docker Setup`")),
                         ("docs/CLI_Usage_DE.md", ("nicht `install.bat`", "Dateien über 512 KB", "`Docker Setup`"))):
        text = " ".join((REPO_ROOT / doc).read_text(encoding="utf-8").split())
        for phrase in phrases:
            assert phrase in text, (doc, phrase)
