#!/usr/bin/env python3
"""
The optional tools of MCP design T10: `delete_artifact` and `restore_knowledge_snapshot`.

Both destroy something (a built model, the current content of the knowledge base), so both are class `external`,
marked destructive, denied by default and therefore hidden until the person enables them in Tool Permissions (a
"allow the whole tier" setting never enables them: their own default comes first), capped at "ask" (every call is
put to the person) and never get a standing approval (design 6.10). Both are two-phase: the question shows what the
framework looked up (the built model by its label, size and hash; the snapshot and the backup taken first), and the
handler acts on exactly that.
"""

from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.tools.output import ToolOutput, fact
from orchestrator.tools.policy import Decision, ToolTier
from orchestrator.tools.registry import ToolContext, ToolError, ToolRegistry, ToolSpec
from orchestrator.tools.spec import CallFacts, Fact, FieldMeta

SNAPSHOT_PATTERN = r"^[\w.-]{1,120}$"


class DeleteArtifactIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_id: str = Field(min_length=18, max_length=18, pattern=r"^a_[0-9a-f]{16}$",
                             description="Id of a built model from list_artifacts, e.g. a_0123456789abcdef")
    expect_sha256: Optional[str] = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$",
                                         description="The sha256 list_artifacts shows for it: the deletion is refused "
                                                     "when the build changed since")


class DeleteArtifactOut(ToolOutput):
    artifact_id: str
    deleted: bool
    freed_bytes: int


class RestoreSnapshotIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot: str = Field(min_length=1, max_length=120, pattern=SNAPSHOT_PATTERN,
                          description="A snapshot name from get_status (knowledge section), e.g. "
                                      "pre_crawl_20261012_101500")


class RestoreStartedOut(ToolOutput):
    job_id: str
    state: str
    note: str = ("Poll get_job_status with wait_seconds=50 until the job ends; its result names the backup taken "
                 "before the restore.")


def _gb(n: int) -> str:
    return f"{n / 1000 ** 3:.2f} GB" if n >= 10 ** 8 else f"{n / 1000 ** 2:.1f} MB"


def register_optional_tools(registry: ToolRegistry, framework, jobs) -> ToolRegistry:

    def store():
        from orchestrator.Core.artifact_store import ArtifactStore
        service = getattr(framework, "build_service", None)
        if service is None:
            raise ToolError("The build service is not available.", code="failed",
                            next="Tell the person; 'llm-cli doctor' shows why.")
        return ArtifactStore.for_service(service)  # the service's public methods only

    def refused(e) -> ToolError:
        return ToolError(str(e), code=getattr(e, "code", "failed"), next=getattr(e, "next", ""))

    # -- delete_artifact ---------------------------------------------------------------------------------------------

    def prepare_delete(inp: DeleteArtifactIn, ctx: ToolContext) -> CallFacts:
        from orchestrator.Core.artifact_store import ArtifactRefused
        try:
            target = store().resolve(inp.artifact_id, inp.expect_sha256)
        except ArtifactRefused as e:
            raise refused(e) from None
        facts = (Fact("Built model", target.label), Fact("Size", _gb(target.size) + " (freed on disk)"),
                 Fact("Model file sha256", target.sha256 or "not listed"),
                 Fact("Also removed", "its golden ZIP next to it" if target.zips else "nothing else"))
        return CallFacts(consequence=f"Deletes the built model {target.label} ({_gb(target.size)}) from the output "
                                     "folder for good; it cannot be undone.",
                         facts=facts, bound=target.describe())

    def delete_artifact(inp: DeleteArtifactIn, ctx: ToolContext) -> DeleteArtifactOut:
        from orchestrator.Core.artifact_store import ArtifactRefused
        bound = dict(ctx.facts.bound) if ctx.facts is not None else {}
        if bound.get("artifact_id") != inp.artifact_id or not bound.get("identity"):
            raise ToolError("The deletion is not bound to what the person was shown; nothing was deleted.",
                            code="failed", next="Call delete_artifact again through the framework's tool registry.")
        try:
            freed = store().delete(inp.artifact_id, bound.get("sha256") or None, bound["identity"])
        except ArtifactRefused as e:
            raise refused(e) from None
        except OSError as e:
            raise ToolError(f"The built model could not be deleted completely ({type(e).__name__}).", code="failed",
                            next="Tell the person; they check the output folder.") from None
        return DeleteArtifactOut(artifact_id=inp.artifact_id, deleted=True, freed_bytes=freed)

    # -- restore_knowledge_snapshot ------------------------------------------------------------------------------

    def knowledge_base():
        from orchestrator.Core.rag_manager import local_models_only
        ensure = getattr(framework, "ensure_rag_manager", None)
        with local_models_only():
            rag = ensure() if callable(ensure) else None
        if rag is None:
            raise ToolError("The knowledge base is disabled (setting enable_rag_knowledge).", code="needs_person",
                            next="Tell the person; only they switch the knowledge base on, in the GUI or CLI.")
        return rag

    def prepare_restore(inp: RestoreSnapshotIn, ctx: ToolContext) -> CallFacts:
        from orchestrator.Core.disk_plan import DiskNeed, check_disk
        rag = knowledge_base()
        names = rag.list_snapshots()
        if inp.snapshot not in names:
            raise ToolError(f"There is no knowledge base snapshot '{inp.snapshot}'.", code="unknown_id",
                            next="get_status with sections ['knowledge'] lists the snapshots.")
        path = rag.snapshot_dir / f"{inp.snapshot}.jsonl.gz"
        newest = max((p.stat().st_size for p in rag.snapshot_dir.glob("*.jsonl.gz")), default=0)
        needed = max(newest, path.stat().st_size)  # the backup taken first is about the size of a snapshot
        service = getattr(framework, "build_service", None)
        checks = check_disk([DiskNeed("knowledge snapshots", rag.snapshot_dir, needed, "backup snapshot")],
                            disk_usage=getattr(service, "disk_usage", None))
        short = [c for c in checks if not c.fits]
        if short:
            raise ToolError("Not enough disk space for the backup taken before the restore: "
                            + "; ".join(c.sentence() for c in short) + ".", code="insufficient_resources",
                            next="Tell the person how much space is missing; they free space first.")
        backup = f"pre_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        facts = (Fact("Snapshot", f"{inp.snapshot} ({_gb(path.stat().st_size)})"),
                 Fact("Backup", f"the current knowledge base is saved first as the snapshot {backup}; the restore "
                                "does not start without it (an empty knowledge base needs none)"),
                 Fact("Disk", "; ".join(c.sentence() for c in checks) or "nothing to write"))
        return CallFacts(consequence=f"Replaces the whole knowledge base with the snapshot {inp.snapshot}; what was "
                                     "added since is removed (kept in the backup snapshot).",
                         facts=facts, bound={"snapshot": inp.snapshot, "backup": backup})

    def restore_knowledge_snapshot(inp: RestoreSnapshotIn, ctx: ToolContext) -> RestoreStartedOut:
        bound = dict(ctx.facts.bound) if ctx.facts is not None else {}
        if bound.get("snapshot") != inp.snapshot or not bound.get("backup"):
            raise ToolError("The restore is not bound to what the person was shown; nothing was changed.",
                            code="failed", next="Call restore_knowledge_snapshot again through the tool registry.")
        rag = knowledge_base()

        def work(handle) -> dict:
            handle.progress(0, 2, "taking the backup snapshot")
            # A cancel (cancel_job, or the session ending) after the backup leaves the knowledge base as it was: a job
            # marked cancelled must not have replaced it.
            result = rag.restore_snapshot(inp.snapshot, backup=bound["backup"], proceed=lambda: not handle.cancelled)
            handle.check_cancelled()
            if not result:
                problem = getattr(rag, "last_restore_problem", "") or "the knowledge base could not be restored"
                raise ToolError(f"Restore of {inp.snapshot} failed: {problem}.", code="failed",
                                next="Tell the person; the knowledge base was left as it was.")
            return {"snapshot": inp.snapshot, "backup": getattr(rag, "last_backup", None) or "",
                    "restored_chunks": int(getattr(rag, "last_restored_chunks", 0) or 0)}

        job = jobs.start("snapshot_restore", ctx.owner, work)
        return RestoreStartedOut(job_id=job.job_id, state=job.state)

    specs: List[Any] = [
        ToolSpec("delete_artifact", "Delete a built model",
                 "Deletes one finished build (its folder below the output folder and its golden ZIP) to free disk "
                 "space; refused outside the output folder, while a build writes into it, or when its sha256 changed "
                 "since list_artifacts. Optional and destructive: hidden until the person enables it, and every call "
                 "is asked.",
                 ToolTier.EXTERNAL, DeleteArtifactIn, DeleteArtifactOut, delete_artifact, takes_context=True,
                 destructive=True, idempotent=True, default_decision=Decision.DENY, max_decision=Decision.ASK,
                 prepare=prepare_delete,
                 fields={"artifact_id": FieldMeta(kind="id", list_tool="list_artifacts"),
                         "expect_sha256": FieldMeta(kind="trust")},
                 examples=({"artifact_id": "a_0123456789abcdef", "expect_sha256": "0" * 64},),
                 consequence="Deletes a built model from the output folder for good.",
                 summary=lambda o: f"Built model {fact(o.artifact_id)} deleted, {fact(o.freed_bytes)} bytes freed."),
        ToolSpec("restore_knowledge_snapshot", "Restore a knowledge snapshot",
                 "Undoes an addition to the knowledge base (imported documentation, a pack): replaces the knowledge base with an earlier snapshot (names in "
                 "get_status, knowledge section). The current content is saved as a backup snapshot first. Runs as a "
                 "job. Optional and destructive: hidden until the person enables it, and every call is asked.",
                 ToolTier.EXTERNAL, RestoreSnapshotIn, RestoreStartedOut, restore_knowledge_snapshot,
                 takes_context=True, destructive=True, default_decision=Decision.DENY, max_decision=Decision.ASK,
                 prepare=prepare_restore,
                 fields={"snapshot": FieldMeta(kind="id", list_tool="get_status")},
                 examples=({"snapshot": "pre_crawl_20261012_101500"},),
                 consequence="Replaces the knowledge base with an earlier snapshot (a backup is taken first).",
                 summary=lambda o: f"Restore job {fact(o.job_id)}: {fact(o.state)}."),
    ]
    for spec in specs:
        registry.register(spec)
    return registry

