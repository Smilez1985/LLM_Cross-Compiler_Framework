#!/usr/bin/env python3
"""
The framework's own documents in the knowledge base, for AI callers: `index_framework_docs` (DECISIONS round 8, N12).

Ditto (and every AI client) can then search the framework's internals with search_knowledge (kind `docs`): how a probe
value reaches a build script, what a setting does, which tool to use. It is an explicit knowledge action, as in the GUI
(Tools > Index the Framework's Documentation...) and the CLI (`llm-cli knowledge index-docs`), never automatic.

- The documentation first (README*, CONTRIBUTING*, docs/*.md); the code and configuration only with `include_code`.
- Two-phase: `prepare` checks that the knowledge base is on and that there is room for the snapshot taken first
  (snapshot_space, the same check the GUI and the CLI make: no room, nothing is indexed); the question shows how many
  files and how much text it reads. Default and cap "ask": it writes the knowledge base.
- A job: get_job_status shows "file n of m"; cancel_job stops after the current file and keeps what is stored. A
  snapshot is taken first, as before a Deep Ingest, so the index can be undone with a restore. The result has counts
  and the snapshot's name, never a host path or file text.
"""

from pathlib import Path
from typing import List

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.tools.facts_catalog import blocked, gb_number, warn
from orchestrator.tools.job_audit import audited_job
from orchestrator.tools.output import ToolOutput, fact
from orchestrator.tools.policy import Decision, ToolTier
from orchestrator.tools.registry import ToolContext, ToolError, ToolRegistry, ToolSpec
from orchestrator.tools.spec import CallFacts, Fact, FieldMeta

JOB_KIND = "docs_index"


class IndexDocsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_code: bool = Field(default=False, description="Also index the framework's code and configuration (many "
                                                          "more files, takes minutes); default: the documentation only")


class IndexDocsStartedOut(ToolOutput):
    job_id: str
    state: str
    documentation_files: int
    code_files: int
    note: str = ("Poll get_job_status with wait_seconds=50 until the job ends; its result has the counts and the "
                 "snapshot taken first. Then search the framework's own documents with search_knowledge, kind 'docs'.")


def _size(n: int) -> str:
    return f"{n / 1000 ** 2:.1f} MB" if n >= 10 ** 5 else f"{max(1, n // 1000)} kB"


def files_size(paths: List) -> int:
    total = 0
    for path in paths:
        try:
            total += path.stat().st_size
        except OSError:
            pass
    return total


def snapshot_space(rag, disk_usage=None) -> List:
    """The disk check (disk_plan.DiskCheck per file system) of the snapshot taken before the index: about the size of
    the largest snapshot so far (without an earlier snapshot there is no size to go by, and nothing is checked). A
    check that does not fit means: nothing is indexed (no snapshot, no "continue anyway"); "tight" is a situation
    warning. Its sentences name labels, never a path."""
    from orchestrator.Core.disk_plan import DiskNeed, check_disk
    folder = Path(rag.snapshot_dir)
    largest = max((files_size([p]) for p in folder.glob("*.jsonl.gz")), default=0) if folder.is_dir() else 0
    return check_disk([DiskNeed("knowledge snapshots", folder, largest, "snapshot taken first")],
                      disk_usage=disk_usage)


def register_framework_docs_tools(registry: ToolRegistry, framework, jobs) -> ToolRegistry:

    def knowledge_base():
        from orchestrator.Core.rag_manager import local_models_only
        ensure = getattr(framework, "ensure_rag_manager", None)
        with local_models_only():
            rag = ensure() if callable(ensure) else None
        if rag is None:
            raise blocked("knowledge_off", "The knowledge base is disabled (setting enable_rag_knowledge).",
                          "Tell the person; only they switch the knowledge base on, in the GUI or CLI.")
        return rag

    def plan(include_code: bool):
        from orchestrator.Core.rag_manager import framework_documents
        rag = knowledge_base()
        docs, code = framework_documents(rag.app_root, include_code)
        if not docs and not code:
            raise ToolError("No documentation was found in the framework's folder, so there is nothing to index.",
                            code="not_supported", next="Tell the person; the installation may be incomplete.")
        return rag, docs, code

    def prepare(inp: IndexDocsIn, ctx: ToolContext) -> CallFacts:
        rag, docs, code = plan(inp.include_code)
        checks = snapshot_space(rag, getattr(getattr(framework, "build_service", None), "disk_usage", None))
        short = [c for c in checks if not c.fits]
        if short:
            raise blocked("disk_full", "Not enough disk space for the snapshot taken before the index: "
                          + "; ".join(c.sentence() for c in short) + ".",
                          "Tell the person how much space is missing; they free space first.")
        what = (f"{len(docs)} documentation files ({_size(files_size(docs))}) and {len(code)} code and configuration "
                f"files ({_size(files_size(code))})" if inp.include_code else
                f"{len(docs)} documentation files ({_size(files_size(docs))})")
        consequence = (f"Indexes the framework's own {what} into the local knowledge base, so Ditto and AI clients can "
                       "search them (search_knowledge, kind docs). A file indexed before is replaced.")
        facts = (Fact("Documentation", f"{len(docs)} files: README, CONTRIBUTING and the docs folder"),
                 Fact("Code and configuration",
                      f"{len(code)} files the framework ships (never the person's settings, own targets or installed "
                      "packs)" if inp.include_code else "not included"),
                 Fact("Knowledge base", "a snapshot is taken first, so the index can be undone with a restore"),
                 Fact("Embedding model", "downloaded once (about a GB) if it is not on this computer yet"),
                 Fact("Disk", "; ".join(c.sentence() for c in checks) or "nothing to write", volatile=True))
        bound = {"include_code": inp.include_code, "documentation_files": len(docs), "code_files": len(code)}
        # little room left after the snapshot: a situation warning (it fits, so nothing is refused)
        warnings = tuple(warn("disk_tight", free_after_gb=gb_number(c.after), reserve_gb=gb_number(c.reserve))
                         for c in checks if c.verdict == "tight")
        return CallFacts(consequence=consequence, facts=facts, bound=bound, warnings=warnings,
                         digest_keys=("include_code",), consequence_template=consequence)

    def index_framework_docs(inp: IndexDocsIn, ctx: ToolContext) -> IndexDocsStartedOut:
        import threading
        bound = dict(ctx.facts.bound) if ctx.facts is not None else {}
        if bound.get("include_code") is not inp.include_code:
            raise ToolError("The index is not bound to what the person was shown; nothing was indexed.",
                            code="failed", next="Call index_framework_docs again through the framework's tool "
                                                "registry.")
        rag, docs, code = plan(inp.include_code)
        stop = threading.Event()

        def work(handle) -> dict:
            result = rag.ingest_codebase(include_code=inp.include_code, stop=stop,
                                         progress=lambda n, total, _name: handle.progress(n, total, "indexing"))
            if not result.get("success"):
                raise ToolError("The framework's documents could not be indexed: the knowledge base reported a "
                                "problem.", code="failed",
                                next="Tell the person; 'llm-cli doctor' shows what is wrong with the knowledge base.")
            return {"files_indexed": int(result.get("count", 0)), "files": int(result.get("files", 0)),
                    "excerpts": int(result.get("chunks", 0)), "snapshot": result.get("snapshot") or "",
                    "stopped": bool(result.get("stopped"))}

        def done(result) -> tuple:
            return ("ok", f"{result.get('files_indexed')} of {result.get('files')} files indexed, "
                          f"{result.get('excerpts')} excerpts, snapshot {result.get('snapshot') or 'none (empty)'}", "")

        audited = audited_job(registry, "index_framework_docs", inp.model_dump(mode="json"), ctx, work,
                              "the framework's own documents" + (" and code" if inp.include_code else ""),
                              done=done, cancelled=lambda: "stopped after the current file; what is stored stays")
        job = jobs.start(JOB_KIND, ctx.owner, audited, on_cancel=stop.set)
        return IndexDocsStartedOut(job_id=job.job_id, state=job.state, documentation_files=len(docs),
                                   code_files=len(code))

    registry.register(ToolSpec(
        "index_framework_docs", "Index the framework's own documentation",
        "Adds the framework's own documentation (README, CONTRIBUTING, docs) to the local knowledge base, and with "
        "include_code its code and configuration, so its internals can be searched with search_knowledge (kind "
        "docs). The person is asked each time; a snapshot is taken first. Runs as a job: poll get_job_status. "
        'Example: {"include_code": false}',
        ToolTier.LOCAL_WRITE, IndexDocsIn, IndexDocsStartedOut, index_framework_docs, takes_context=True,
        idempotent=True, prepare=prepare, default_decision=Decision.ASK, max_decision=Decision.ASK,
        fields={"include_code": FieldMeta(kind="value")},
        examples=({"include_code": False},),
        consequence="Adds the framework's own documents to the knowledge base (a snapshot is taken first).",
        summary=lambda o: f"Index job {fact(o.job_id)}: {fact(o.state)}."))
    return registry


__all__ = ["register_framework_docs_tools", "IndexDocsIn", "IndexDocsStartedOut", "JOB_KIND", "files_size",
           "snapshot_space"]
