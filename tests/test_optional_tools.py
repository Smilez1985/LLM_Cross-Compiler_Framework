"""The optional tools of MCP design T10: delete_artifact and restore_knowledge_snapshot.

Both are destructive, denied by default and therefore hidden until the person enables them (also when the person
allowed the whole external tier), capped at "ask" and never get a standing approval. delete_artifact refuses
outside the output folder, while a build writes into it and when the build changed since the caller looked;
restore_knowledge_snapshot always takes a backup first (also when the core restore is called directly).
"""

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("mcp")

from orchestrator.Core import build_records  # noqa: E402
from orchestrator.Core.rag_manager import RAGManager  # noqa: E402
from orchestrator.tools.grants import NEVER_GRANT  # noqa: E402
from orchestrator.tools.policy import Decision, ToolPolicy, ToolTier  # noqa: E402
from orchestrator.tools.registry import ToolContext, ToolError, current_question  # noqa: E402
from test_build_tools import env  # noqa: E402,F401 - the fixture

SHA = "d" * 64


def enable(env, *names, tiers=None):
    env.registry.policy_store.replace(ToolPolicy(
        configured=True, reviewed=[s.name for s in env.registry.list()], tiers=tiers or {},
        overrides={n: Decision.ALLOW for n in names}))


def make_artifact(env, name="my-model", quant="Q4_K_M", payload=b"g" * 1234):
    folder = Path(env.framework.config.values["output_dir"]) / "Rockchip" / name / quant
    folder.mkdir(parents=True)
    (folder / "model.gguf").write_bytes(payload)
    import hashlib
    digest = hashlib.sha256(payload).hexdigest()
    (folder / "build_info.json").write_text(json.dumps({"primary": "model.gguf", "format": "gguf", "created": "1",
                                                        "target": "Rockchip",
                                                        "artifacts": [{"file": "model.gguf", "sha256": digest}]}))
    (folder.parent / f"{quant}.zip").write_bytes(b"z" * 100)
    item = env.registry.call("list_artifacts", {}, ToolContext("ditto")).artifacts[0]
    return folder, item.artifact_id, digest


def ask(env, name, arguments, answer=True):
    shown = []
    out = env.registry.call(name, arguments, ToolContext("ditto", session="opt", confirm=lambda s, i: shown.append(
        current_question()) or answer))
    return out, shown


class TestHiddenUntilEnabled:

    @pytest.mark.parametrize("name", ["delete_artifact", "restore_knowledge_snapshot"])
    def test_hidden_by_default_also_when_the_whole_tier_is_allowed(self, env, name):
        spec = env.registry.get(name)
        assert spec.tier == ToolTier.EXTERNAL and spec.destructive and spec.grant is None and name in NEVER_GRANT
        enable(env, tiers={ToolTier.EXTERNAL: Decision.ASK, ToolTier.LOCAL_WRITE: Decision.ALLOW})
        assert name not in {s.name for s in env.registry.visible("ditto")}
        assert name not in {s.name for s in env.registry.visible("mcp")}
        with pytest.raises(ToolError):
            env.registry.call(name, {"artifact_id": "a_" + "0" * 16} if name == "delete_artifact"
                              else {"snapshot": "s1"}, ToolContext("ditto", confirm=lambda s, i: True))

    @pytest.mark.parametrize("name", ["delete_artifact", "restore_knowledge_snapshot"])
    def test_enabled_it_is_listed_and_still_asks(self, env, name):
        enable(env, name)  # even "allow" is capped at ask
        assert name in {s.name for s in env.registry.visible("ditto")}
        assert env.registry.decide(env.registry.get(name), caller="ditto") == Decision.ASK


class TestDeleteArtifact:

    def test_deletes_after_the_question_with_label_size_and_hash(self, env):
        enable(env, "delete_artifact")
        folder, aid, digest = make_artifact(env)
        out, shown = ask(env, "delete_artifact", {"artifact_id": aid, "expect_sha256": digest})
        assert out.deleted and out.freed_bytes >= 1334 and not folder.exists()
        assert not (folder.parent / "Q4_K_M.zip").exists(), "its golden ZIP goes too"
        text = shown[0]
        assert "output:Rockchip/my-model/Q4_K_M" in text and digest in text and "cannot be undone" in text
        # Round 5 f: Ditto's dialog is the framework's own window, so the person sees each item's full path there
        # (what an AI can read never holds it: test_the_delete_question_lists_full_path_name_size_and_kind_...).
        assert str(folder.resolve()) in text

    def test_the_delete_question_lists_full_path_name_size_and_kind_for_the_person_only(self, env, monkeypatch,
                                                                                       capsys):
        """Round 5 f: the person confirms a deletion in the framework's own window, which lists every item with its
        speaking name, full path, file name, size and what it is (the folder and its golden ZIP). Nothing an AI can
        read holds a host path: the question an MCP form would show, the waiting result, the job, the audit log, the
        plan's AI view, and the question Ditto's command line prints when no terminal can confirm it."""
        from orchestrator import cli
        from orchestrator.Core.job_service import JobService
        from orchestrator.tools.approvals import ApprovalService, ApprovalStore
        from orchestrator.tools.confirmation import MAX_ELICITATION
        enable(env, "delete_artifact")
        folder, aid, digest = make_artifact(env)
        folder = folder.resolve()  # the real path is what gets deleted
        zip_path = folder.parent / "Q4_K_M.zip"
        spec, args = env.registry.get("delete_artifact"), {"artifact_id": aid, "expect_sha256": digest}
        facts = env.registry.look_up(spec, spec.input_model(**args), ToolContext("ditto"))
        plan = facts.deletion
        assert [(i.kind, i.full_path, i.file_name) for i in plan.items] == [
            ("artifact", str(folder), "Q4_K_M"), ("artifact", str(zip_path), "Q4_K_M.zip")]
        assert [i.size_bytes for i in plan.items] == [1234 + len((folder / "build_info.json").read_bytes()), 100]
        assert plan.total_bytes == sum(i.size_bytes for i in plan.items) and plan.mode_offered == ("permanent",)
        assert all(i.speaking_name.startswith("output:Rockchip/my-model/Q4_K_M") and i.what for i in plan.items)
        assert str(env.tmp) not in json.dumps(plan.ai_view())

        person = env.registry.question_text(spec, args, ToolContext("ditto"), facts)  # Ditto's dialog, the GUI, CLI
        for item in plan.items:
            assert f"    {item.full_path}" in person and f"{item.file_name}, {item.size_bytes} bytes" in person
        ai_side = env.registry.question_text(spec, args, ToolContext("mcp"), facts, MAX_ELICITATION)
        assert "Would be deleted:" in ai_side and str(env.tmp) not in ai_side

        # An MCP client: a pending approval the person decides in the framework; the client learns no path.
        env.registry.attach_approvals(ApprovalService(ApprovalStore(env.tmp / "approvals"), JobService(), poll=3600.0))
        with pytest.raises(ToolError) as exc:
            env.registry.call("delete_artifact", args, ToolContext("mcp", session="m", pending_approvals=True))
        assert exc.value.code == "waiting_for_approval" and str(env.tmp) not in str(exc.value)
        record = env.registry.approvals.store.pending()[0]
        assert str(folder) in record.question and str(zip_path) in record.question, "the framework window lists it"
        job = env.registry.approvals.jobs.get(record.job_id)
        assert str(env.tmp) not in json.dumps([job.note, job.message, job.result, job.error], default=str)
        audit_text = json.dumps(env.registry.audit.entries(100), default=str)
        assert str(folder) not in audit_text and str(zip_path) not in audit_text

        # Ditto's command line without a terminal denies, and what it prints holds no person-only fact.
        monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: False))
        with pytest.raises(ToolError):
            env.registry.call("delete_artifact", args, ToolContext("ditto", session="c", confirm=cli._ditto_confirm))
        err = capsys.readouterr().err
        assert "denied" in err and aid in err and str(env.tmp) not in err
        assert folder.exists() and zip_path.exists()

    def test_a_plan_that_cannot_be_listed_is_refused_without_a_path(self, env, monkeypatch):
        """The golden ZIP vanished between the look-up and the plan: the call is refused before any question, in
        framework words; the refusal an AI reads never quotes a path (a validation error would quote the values)."""
        from orchestrator.Core.artifact_store import ArtifactStore
        enable(env, "delete_artifact")
        folder, aid, digest = make_artifact(env)
        resolve = ArtifactStore.resolve

        def vanished(self, *args, **kwargs):
            target = resolve(self, *args, **kwargs)
            for z in target.zips:
                z.unlink()
            return target
        monkeypatch.setattr(ArtifactStore, "resolve", vanished)
        with pytest.raises(ToolError) as exc:
            ask(env, "delete_artifact", {"artifact_id": aid, "expect_sha256": digest})
        assert "changed while" in str(exc.value) and str(env.tmp) not in f"{exc.value} {exc.value.next}"
        assert folder.exists()

    def test_a_no_deletes_nothing(self, env):
        enable(env, "delete_artifact")
        folder, aid, digest = make_artifact(env)
        with pytest.raises(ToolError):
            ask(env, "delete_artifact", {"artifact_id": aid, "expect_sha256": digest}, answer=False)
        assert folder.exists()

    def test_a_changed_build_is_refused_before_the_question(self, env):
        enable(env, "delete_artifact")
        folder, aid, _ = make_artifact(env)
        with pytest.raises(ToolError) as exc:
            ask(env, "delete_artifact", {"artifact_id": aid, "expect_sha256": "e" * 64})
        assert "changed since you looked" in str(exc.value) and folder.exists()
        with pytest.raises(ToolError) as exc:
            ask(env, "delete_artifact", {"artifact_id": aid})
        assert exc.value.code == "missing_information" and folder.exists()

    def test_refused_outside_the_output_folder(self, env):
        enable(env, "delete_artifact")
        outside = env.tmp / "elsewhere" / "Q8_0"
        outside.mkdir(parents=True)
        (outside / "model.gguf").write_bytes(b"x")
        (outside / "build_info.json").write_text(json.dumps({"primary": "model.gguf", "created": "1"}))
        link_parent = Path(env.framework.config.values["output_dir"]) / "Rockchip" / "linked"
        link_parent.mkdir(parents=True)
        (link_parent / "Q8_0").symlink_to(outside, target_is_directory=True)
        aid = env.registry.call("list_artifacts", {}, ToolContext("ditto")).artifacts[0].artifact_id
        import hashlib
        with pytest.raises(ToolError) as exc:
            ask(env, "delete_artifact", {"artifact_id": aid, "expect_sha256": hashlib.sha256(b"x").hexdigest()})
        assert exc.value.code == "not_supported" and (outside / "model.gguf").exists()

    def test_refused_while_a_build_writes_into_it(self, env):
        enable(env, "delete_artifact")
        folder, aid, digest = make_artifact(env)
        records = build_records.records_dir(Path(env.framework.config.values["cache_dir"]))
        build_records.write_record(records, {"build_id": "req_busy_001", "request_id": "req_busy", "status": "building",
                                             "output_dir": str(folder), **build_records.this_process(),
                                             "started": "2026-10-12T10:00:00"})
        with pytest.raises(ToolError) as exc:
            ask(env, "delete_artifact", {"artifact_id": aid, "expect_sha256": digest})
        assert "has not finished writes into" in str(exc.value) and folder.exists()

    def test_refused_while_a_queued_request_will_write_into_it(self, env):
        from orchestrator.Core import request_store
        from test_shared_build_state import state
        enable(env, "delete_artifact")
        folder, aid, digest = make_artifact(env, name="model")  # state(): model org/model for Rockchip
        request_store.RequestStore(Path(env.framework.config.user_state_dir)).save(state(rid="req_queued9"))
        with pytest.raises(ToolError):
            ask(env, "delete_artifact", {"artifact_id": aid, "expect_sha256": digest})
        assert folder.exists()

    def test_the_folder_must_still_be_the_one_shown(self, env, monkeypatch):
        from orchestrator.Core import artifact_store
        enable(env, "delete_artifact")
        folder, aid, digest = make_artifact(env)
        real_resolve = artifact_store.ArtifactStore.resolve
        calls = []

        def swapping(self, artifact_id, expect=None):
            target = real_resolve(self, artifact_id, expect)
            calls.append(1)
            if len(calls) == 2:  # between the question and the deletion the folder was replaced
                return artifact_store.ArtifactTarget(target.artifact_id, target.label, target.folder, target.zips,
                                                     target.sha256, target.size, (0, 0))
            return target
        monkeypatch.setattr(artifact_store.ArtifactStore, "resolve", swapping)
        with pytest.raises(ToolError) as exc:
            ask(env, "delete_artifact", {"artifact_id": aid, "expect_sha256": digest})
        assert "another folder" in str(exc.value) and folder.exists()


class FakeRag:
    def __init__(self, folder: Path):
        self.snapshot_dir = folder
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "pre_crawl_1.jsonl.gz").write_bytes(b"s" * 50)
        self.restored = []

    def list_snapshots(self):
        return sorted(p.name[:-len(".jsonl.gz")] for p in self.snapshot_dir.glob("*.jsonl.gz"))

    def restore_snapshot(self, name, backup=None, proceed=None):
        self.backups = getattr(self, "backups", []) + [backup]
        if proceed is not None and not proceed():
            self.last_restore_problem = "cancelled"
            return False
        self.restored.append((name, backup))
        self.last_backup, self.last_restored_chunks = backup, 7
        return True


class TestRestoreSnapshot:

    def wait(self, env, job_id):
        jobs = env.framework.job_service if getattr(env.framework, "job_service", None) else None
        for _ in range(100):
            out = env.registry.call("get_job_status", {"job_id": job_id}, ToolContext("ditto", session="opt"))
            if out.state in ("done", "failed", "cancelled"):
                return out
            time.sleep(0.05)
        raise AssertionError(jobs)

    def test_restore_runs_as_a_job_with_a_backup_named_in_the_question(self, env):
        rag = FakeRag(env.tmp / "snapshots")
        env.framework.ensure_rag_manager = lambda: rag
        enable(env, "restore_knowledge_snapshot")
        out, shown = ask(env, "restore_knowledge_snapshot", {"snapshot": "pre_crawl_1"})
        assert "pre_restore_" in shown[0] and "pre_crawl_1" in shown[0] and "backup" in shown[0].lower()
        status = self.wait(env, out.job_id)
        assert status.state == "done", status
        (name, backup), = rag.restored
        assert name == "pre_crawl_1" and backup.startswith("pre_restore_")

    def test_a_cancel_after_the_backup_leaves_the_knowledge_base_as_it_was(self, env):
        # cancel_job (or the end of the session) marks the job cancelled at once; the restore must then not replace
        # the knowledge base behind that "cancelled".
        import threading
        backup_taken, cancelled = threading.Event(), threading.Event()

        class SlowRag(FakeRag):
            def restore_snapshot(self, name, backup=None, proceed=None):
                backup_taken.set()
                cancelled.wait(5)
                return super().restore_snapshot(name, backup, proceed)

        rag = SlowRag(env.tmp / "snapshots")
        env.framework.ensure_rag_manager = lambda: rag
        enable(env, "restore_knowledge_snapshot")
        out, _ = ask(env, "restore_knowledge_snapshot", {"snapshot": "pre_crawl_1"})
        assert backup_taken.wait(5)
        env.registry.call("cancel_job", {"job_id": out.job_id}, ToolContext("ditto", session="opt"))
        cancelled.set()
        assert self.wait(env, out.job_id).state == "cancelled"
        time.sleep(0.2)
        assert rag.restored == [], "a cancelled restore never replaced the knowledge base"

    def test_an_unknown_snapshot_is_refused_before_the_question(self, env):
        env.framework.ensure_rag_manager = lambda: FakeRag(env.tmp / "snapshots")
        enable(env, "restore_knowledge_snapshot")
        with pytest.raises(ToolError) as exc:
            ask(env, "restore_knowledge_snapshot", {"snapshot": "nope"})
        assert exc.value.code == "unknown_id"


class Store:
    """The knowledge store as RAGManager sees it: a count, an export and a restore."""

    def __init__(self, count=3, export_fails=False):
        self._count, self.export_fails, self.restored = count, export_fails, []

    def count(self):
        return self._count

    def export_file(self, path):
        if self.export_fails:
            return 0
        Path(path).write_bytes(b"backup")
        return self._count

    def restore_file(self, path):
        self.restored.append(Path(path).name)
        return 5


def rag_with(tmp_path, store):
    rag = object.__new__(RAGManager)
    rag._state_dir = lambda: tmp_path
    rag._with_store = lambda op: op(store)
    rag._sources = None
    rag.logger = SimpleNamespace(error=lambda *a: None, warning=lambda *a: None, info=lambda *a: None)
    rag.last_error = ""
    (tmp_path / "knowledge_snapshots").mkdir(parents=True, exist_ok=True)
    return rag


class TestCoreRestoreAlwaysBacksUp:

    def test_a_backup_is_taken_before_the_restore(self, tmp_path):
        store = Store()
        rag = rag_with(tmp_path, store)
        (rag.snapshot_dir / "old.jsonl.gz").write_bytes(b"old")
        assert rag.restore_snapshot("old")
        assert rag.last_backup and (rag.snapshot_dir / f"{rag.last_backup}.jsonl.gz").is_file()
        assert store.restored == ["old.jsonl.gz"] and rag.last_restored_chunks == 5

    def test_no_backup_no_restore(self, tmp_path):
        store = Store(export_fails=True)
        rag = rag_with(tmp_path, store)
        (rag.snapshot_dir / "old.jsonl.gz").write_bytes(b"old")
        assert not rag.restore_snapshot("old")
        assert store.restored == [] and "backup" in rag.last_restore_problem

    def test_the_snapshot_to_restore_is_never_pruned_by_its_backup(self, tmp_path):
        store = Store()
        rag = rag_with(tmp_path, store)
        for i in range(5):
            path = rag.snapshot_dir / f"s{i}.jsonl.gz"
            path.write_bytes(b"x")
            os.utime(path, (1000 + i, 1000 + i))
        assert rag.restore_snapshot("s0")
        assert store.restored == ["s0.jsonl.gz"] and (rag.snapshot_dir / "s0.jsonl.gz").exists()

    def test_a_refused_proceed_after_the_backup_restores_nothing(self, tmp_path):
        store = Store()
        rag = rag_with(tmp_path, store)
        (rag.snapshot_dir / "old.jsonl.gz").write_bytes(b"old")
        assert not rag.restore_snapshot("old", proceed=lambda: False)
        assert store.restored == [] and rag.last_backup and "cancelled" in rag.last_restore_problem

    def test_an_empty_knowledge_base_needs_no_backup(self, tmp_path):
        store = Store(count=0)
        rag = rag_with(tmp_path, store)
        (rag.snapshot_dir / "old.jsonl.gz").write_bytes(b"old")
        assert rag.restore_snapshot("old") and rag.last_backup is None
