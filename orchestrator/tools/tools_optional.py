#!/usr/bin/env python3
"""
The optional tools of MCP design T10: `delete_artifact` and `restore_knowledge_snapshot`; and of the trash
(delegation design 3.10): `list_trash` and `restore_from_trash`.

Both destroy something (a built model, the current content of the knowledge base), so both are class `external`,
marked destructive, denied by default and therefore hidden until the person enables them in Tool Permissions (a
"allow the whole tier" setting never enables them: their own default comes first), capped at "ask" (every call is
put to the person) and never get a standing approval (design 6.10). Both are two-phase: the question shows what the
framework looked up (the built model by its label, size and hash; the snapshot and the backup taken first), and the
handler acts on exactly that. `delete_artifact` deletes (round 5 f): the person alone confirms it, in the framework's
own window, never through a client's form or under a delegation; it moves the built model into the trash, where the
person can restore it (the space stays used until the person empties the trash; no tool empties it).

`list_trash` and `restore_from_trash` are optional too (denied by default). An AI sees the trash by speaking names,
kinds, sizes and dates, never a path, and restores only what it asked to delete itself: what the person deleted, the
person restores (blocker trash_not_yours).
"""

from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.tools.facts_catalog import blocked
from orchestrator.tools.output import ToolOutput, fact
from orchestrator.tools.policy import Decision, ToolTier
from orchestrator.tools.registry import ToolContext, ToolError, ToolRegistry, ToolSpec
from orchestrator.tools.spec import CallFacts, DeletionPlan, Fact, FieldMeta

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
    moved_to_trash: bool = False
    trash_ids: List[str] = Field(default_factory=list)
    freed_bytes: int
    note: str = ""


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


TRASH_ID = r"^tr_[0-9a-f]{16}$"


class ListTrashIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    only_yours: bool = Field(default=False, description="Only what you asked to delete (what you can restore)")


class TrashItemOut(BaseModel):
    trash_id: str
    kind: str
    label: str
    size_bytes: int
    deleted_at: str
    remind_at: str
    yours: bool


class ListTrashOut(ToolOutput):
    items: List[TrashItemOut]
    total_bytes: int
    note: str = ("Only the person empties the trash (File > Trash... or 'llm-cli trash empty'); no tool does. You can "
                 "restore an item with restore_from_trash only when 'yours' is true.")


class RestoreFromTrashIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trash_id: str = Field(min_length=19, max_length=19, pattern=TRASH_ID,
                          description="Id from list_trash, e.g. tr_0123456789abcdef")


class RestoredOut(ToolOutput):
    trash_id: str
    restored: bool
    label: str
    renamed: bool = False


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
        facts = (Fact("Built model", target.label),
                 Fact("Size", _gb(target.size) + " (moved to the trash: the space stays used until the person "
                                                 "empties the trash)"),
                 Fact("Model file sha256", target.sha256 or "not listed"),
                 Fact("Also moved", "its golden ZIP next to it" if target.zips else "nothing else"))
        return CallFacts(consequence=f"Moves the built model {target.label} ({_gb(target.size)}) from the output "
                                     "folder to the trash; the person can restore it there.",
                         facts=facts, bound=target.describe(), deletion=deletion_plan(target))

    def deletion_plan(target) -> DeletionPlan:
        """Round 5 f: what the person is shown in the framework's own window, item by item with the full path (an AI
        sees labels and sizes only): exactly the folder and the golden ZIP the handler moves. An AI's request only
        moves to the trash; deleting for good is the person's own step (emptying the trash)."""
        try:
            return store().plan(target, ("trash",))
        except (OSError, ValueError):  # never the error's own words: they quote host paths
            raise ToolError(f"The built model {target.label} changed while it was looked up; nothing was deleted.",
                            code="failed", next="Call list_artifacts again; the person is asked again.") from None

    def delete_artifact(inp: DeleteArtifactIn, ctx: ToolContext) -> DeleteArtifactOut:
        from orchestrator.Core.artifact_store import ArtifactRefused
        from orchestrator.Core.deletion import PlanChanged, accepted_through_registry
        from orchestrator.Core.trash import Trash, TrashError
        bound = dict(ctx.facts.bound) if ctx.facts is not None else {}
        plan = getattr(ctx.facts, "deletion", None)
        if bound.get("artifact_id") != inp.artifact_id or not bound.get("identity") or plan is None:
            raise ToolError("The deletion is not bound to what the person was shown; nothing was deleted.",
                            code="failed", next="Call delete_artifact again through the framework's tool registry.")
        try:
            accepted = accepted_through_registry(plan, ctx, "trash")
        except (PermissionError, ValueError):
            raise ToolError("Only the person confirms a deletion, in the framework's own window; nothing was "
                            "deleted.", code="needs_person",
                            next="Tell the person; the framework asks them when you call it again.") from None
        try:
            done = store().delete(inp.artifact_id, bound.get("sha256") or None, bound["identity"], accepted,
                                  trash=Trash.for_framework(framework), deleted_by=ctx.actor)
        except ArtifactRefused as e:
            raise refused(e) from None
        except PlanChanged:
            raise ToolError("The built model changed after the person saw the list; nothing was deleted.",
                            code="failed", next="Call list_artifacts again; the person is asked again.") from None
        except TrashError as e:
            raise ToolError(f"{e}", code=e.code if e.code != "failed" else "failed",
                            next="Tell the person; nothing was deleted.") from None
        except OSError as e:
            raise ToolError(f"The built model could not be moved completely ({type(e).__name__}).", code="failed",
                            next="Tell the person; they check the output folder and the trash.") from None
        return DeleteArtifactOut(artifact_id=inp.artifact_id, deleted=True, moved_to_trash=bool(done.trash_ids),
                                 trash_ids=done.trash_ids, freed_bytes=done.freed_bytes,
                                 note="Moved to the trash: the person can restore it (and you can, with "
                                      "restore_from_trash, since you asked for it). Its space stays used until the "
                                      "person empties the trash.")

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
        # The backup's name (the time of the look-up) and the free space move while a question waits: shown, never
        # part of the decision, which is the snapshot (delegation design 4.4, F3)
        facts = (Fact("Snapshot", f"{inp.snapshot} ({_gb(path.stat().st_size)})"),
                 Fact("Backup", f"the current knowledge base is saved first as the snapshot {backup}; the restore "
                                "does not start without it (an empty knowledge base needs none)", volatile=True),
                 Fact("Disk", "; ".join(c.sentence() for c in checks) or "nothing to write", volatile=True))
        consequence = (f"Replaces the whole knowledge base with the snapshot {inp.snapshot}; what was added since is "
                       "removed (kept in the backup snapshot).")
        return CallFacts(consequence=consequence, facts=facts, bound={"snapshot": inp.snapshot, "backup": backup},
                         digest_keys=("snapshot",), consequence_template=consequence)

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

    # -- the trash ---------------------------------------------------------------------------------------------------

    def trash():
        from orchestrator.Core.trash import Trash
        return Trash.for_framework(framework)

    def trash_entries():
        from orchestrator.Core.trash import TrashError
        try:
            return trash().entries()
        except TrashError as e:
            raise ToolError(str(e), code="failed", next="Tell the person; they look at File > Trash... in the "
                                                        "framework.") from None
        except (AttributeError, OSError, TypeError):
            raise ToolError("The trash is not available here.", code="not_supported",
                            next="Tell the person; 'llm-cli doctor' shows why.") from None

    def list_trash(inp: ListTrashIn, ctx: ToolContext) -> ListTrashOut:
        from orchestrator.Core.trash import Trash
        items = []
        for entry in trash_entries():
            if not Trash.present(entry):
                continue
            view = entry.ai_view(ctx.actor)
            if inp.only_yours and not view["yours"]:
                continue
            items.append(TrashItemOut(**view))
        return ListTrashOut(items=items, total_bytes=sum(i.size_bytes for i in items))

    def prepare_restore_from_trash(inp: RestoreFromTrashIn, ctx: ToolContext) -> CallFacts:
        from orchestrator.Core.trash import Trash
        entry = next((e for e in trash_entries() if e.id == inp.trash_id), None)
        if entry is None or not Trash.present(entry):
            raise blocked("trash_unknown", "There is no such item in the trash.",
                          "Call list_trash for the ids you can use.")
        if entry.deleted_by != ctx.actor:
            raise blocked("trash_not_yours", "The person deleted this, so only the person restores it.",
                          "Tell the person they can restore it in File > Trash... of the framework.")
        facts = (Fact("Item", f"{entry.label} ({entry.what()})"), Fact("Size", _gb(entry.size)),
                 Fact("Back to", entry.origin), Fact("Moved to the trash", entry.deleted_at.replace("T", " ")[:16]))
        consequence = (f"Moves {entry.label} from the trash back to {entry.origin} (next to it with a new name when "
                       "that place is taken).")
        return CallFacts(consequence=consequence, facts=facts,
                         bound={"trash_id": entry.id, "plan_sha256": entry.plan_sha256},
                         consequence_template=consequence)

    def restore_from_trash(inp: RestoreFromTrashIn, ctx: ToolContext) -> RestoredOut:
        from orchestrator.Core.trash import TrashError
        bound = dict(ctx.facts.bound) if ctx.facts is not None else {}
        if bound.get("trash_id") != inp.trash_id:
            raise ToolError("The restore is not bound to what the person was shown; nothing was changed.",
                            code="failed", next="Call restore_from_trash again through the framework's tool registry.")
        store = trash()
        entry = store.get(inp.trash_id)
        if entry is None or entry.plan_sha256 != bound.get("plan_sha256"):
            raise ToolError("That item changed in the trash; nothing was restored.", code="failed",
                            next="Call list_trash again.")
        try:
            dest = store.restore(inp.trash_id, ctx.actor)
        except TrashError as e:
            next_step = "Tell the person; they look at File > Trash... in the framework."
            if e.code in ("trash_unknown", "trash_not_yours"):
                raise blocked(e.code, str(e), next_step) from None
            raise ToolError(str(e), code="failed", next=next_step) from None
        return RestoredOut(trash_id=inp.trash_id, restored=True, label=entry.label,
                           renamed=dest.name != entry.name)

    specs: List[Any] = [
        ToolSpec("delete_artifact", "Delete a built model",
                 "Moves one finished build (its folder below the output folder and its golden ZIP) to the trash; the "
                 "person can restore it, and its disk space is freed when the person empties the trash. Refused "
                 "outside the output folder, while a build writes into it, or when its sha256 changed since "
                 "list_artifacts. Optional and destructive: hidden until the person enables it, and every call is "
                 "asked.",
                 ToolTier.EXTERNAL, DeleteArtifactIn, DeleteArtifactOut, delete_artifact, takes_context=True,
                 destructive=True, idempotent=True, default_decision=Decision.DENY, max_decision=Decision.ASK,
                 prepare=prepare_delete,
                 # round 5 f: only the person decides a deletion, in the framework's own window (a pending approval
                 # for an MCP client, Ditto's dialog or prompt); never a client's form, a grant or a delegation
                 deletes=True, channels=("framework",),
                 fields={"artifact_id": FieldMeta(kind="id", list_tool="list_artifacts"),
                         "expect_sha256": FieldMeta(kind="trust")},
                 examples=({"artifact_id": "a_0123456789abcdef", "expect_sha256": "0" * 64},),
                 consequence="Moves a built model from the output folder to the trash.",
                 summary=lambda o: f"Built model {fact(o.artifact_id)} moved to the trash: {fact(o.moved_to_trash)}."),
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
        ToolSpec("list_trash", "List the trash",
                 "Lists what is in the framework's trash (speaking name, kind, size, when it was moved there and "
                 "whether you asked to delete it), never a path. Only the person empties the trash. Optional: hidden "
                 "until the person enables it.",
                 ToolTier.READ, ListTrashIn, ListTrashOut, list_trash, takes_context=True, idempotent=True,
                 default_decision=Decision.DENY,
                 examples=({}, {"only_yours": True}),
                 summary=lambda o: f"{fact(len(o.items))} items in the trash, {fact(o.total_bytes)} bytes."),
        ToolSpec("restore_from_trash", "Restore from the trash",
                 "Moves an item you asked to delete back from the trash to where it was (next to it with a new name "
                 "when that place is taken). Only what you asked to delete yourself; what the person deleted, the "
                 "person restores. Optional: hidden until the person enables it, and every call is asked.",
                 ToolTier.LOCAL_WRITE, RestoreFromTrashIn, RestoredOut, restore_from_trash, takes_context=True,
                 default_decision=Decision.DENY, max_decision=Decision.ASK, prepare=prepare_restore_from_trash,
                 fields={"trash_id": FieldMeta(kind="id", list_tool="list_trash")},
                 examples=({"trash_id": "tr_0123456789abcdef"},),
                 consequence="Moves an item back from the trash to where it was.",
                 summary=lambda o: f"Trash item {fact(o.trash_id)} restored: {fact(o.restored)}."),
    ]
    for spec in specs:
        registry.register(spec)
    return registry

