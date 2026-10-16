#!/usr/bin/env python3
"""
Deep Ingest for AI callers: `crawl_docs_site` (MCP design 2.5, work package T7; DECISIONS round 1 item 1).

The client adds documentation to the knowledge base Ditto uses, exactly as a person does in the GUI's Deep Ingest:
the same crawler, the same machine-readable opt-out checks (robots.txt, robots meta and X-Robots-Tag incl. noai,
TDMRep), which no input can switch off, the same one-time notice, which only a person acknowledges (in the GUI or
with `llm-cli tools crawl-notice`), and the caps of the limits table. The tool reaches the crawler only through
`CrawlJobService` (orchestrator/Core/crawl_jobs.py).

- Two-phase: `prepare` checks the notice, offline mode, the knowledge base, every start address (public http(s)
  only) and the caps before the question; the question lists every start address. The job crawls exactly the plan
  the person saw.
- Default and cap "ask", never a standing approval: every crawl reads third-party sites from the person's address,
  and each start address is shown to them each time.
- A job: get_job_status shows "page n of m"; cancel_job stops after the current page and keeps what is stored. The
  result has counts and the skipped or failed addresses with their reasons, never page text.
"""

import re
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from orchestrator.Core.crawl_jobs import (DEPTH_CAP, MAX_START_URLS, MAX_URL_CHARS, PAGE_CAP, CrawlJobService,
                                          CrawlPlan, CrawlRefused)
from orchestrator.tools.facts_catalog import MANY_PAGES, gb_number, warn, with_blocker
from orchestrator.tools.output import ToolOutput, fact, untrusted_text
from orchestrator.tools.policy import Decision, ToolTier
from orchestrator.tools.registry import ToolContext, ToolError, ToolRegistry, ToolSpec
from orchestrator.tools.spec import CallFacts, Fact, FieldMeta, ToolLimits

AI_TEXT = "address given by the AI client"


class CrawlIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_urls: List[str] = Field(
        min_length=1, max_length=MAX_START_URLS,
        description=f"1 to {MAX_START_URLS} public http(s) addresses of documentation sites to start from, e.g. "
                    "['https://docs.radxa.com/en/zero/zero3']")
    max_pages: Optional[int] = Field(default=None, ge=1, le=PAGE_CAP,
                                     description="Most pages stored per start address (default: the person's "
                                                 f"setting; never above it or {PAGE_CAP})")
    max_depth: Optional[int] = Field(default=None, ge=1, le=DEPTH_CAP,
                                     description="How many links deep to follow from each start address (default 2; "
                                                 f"never above the person's setting or {DEPTH_CAP})")
    languages: Optional[List[str]] = Field(default=None, max_length=10,
                                           description="Keep only pages in these languages, as two-letter codes, "
                                                       "e.g. ['en', 'de'] (default: the person's setting)")
    include_pdfs: Optional[bool] = Field(default=None, description="Also read linked PDF files (default: the "
                                                                   "person's setting)")

    @field_validator("start_urls")
    @classmethod
    def _addresses(cls, urls: List[str]) -> List[str]:
        for url in urls:
            if len(url) > MAX_URL_CHARS or not re.match(r"^https?://\S+$", url, re.I):
                raise ValueError(f"start_urls takes http:// or https:// addresses of at most {MAX_URL_CHARS} "
                                 "characters, e.g. 'https://docs.radxa.com/en/zero/zero3'")
        return urls

    @field_validator("languages")
    @classmethod
    def _codes(cls, codes: Optional[List[str]]) -> Optional[List[str]]:
        for code in codes or []:
            if not re.fullmatch(r"[a-z]{2}", code):
                raise ValueError("languages takes two-letter codes such as 'en' or 'de'")
        return codes


class CrawlStartedOut(ToolOutput):
    job_id: str
    state: str
    start_urls: List[untrusted_text(MAX_URL_CHARS)]
    max_pages: int
    max_depth: int
    note: str = ("Poll get_job_status with wait_seconds=50 until the job ends; its result has the counts and the "
                 "skipped or failed addresses. cancel_job stops it after the current page and keeps what is stored. "
                 "Read the stored pages with search_knowledge or read_knowledge_source.")


def _plan_facts(plan: CrawlPlan) -> CallFacts:
    sites = len(plan.start_urls)
    consequence = (f"Crawls {sites} documentation site{'s' if sites != 1 else ''} from this computer into the local "
                   f"knowledge base: up to {plan.max_pages} pages per start address, {plan.max_depth} links deep, at "
                   f"least {plan.delay:g} s between requests. The sites' robots.txt, robots directives and TDMRep "
                   "reservations are honoured and cannot be switched off.")
    facts = [Fact(f"Start address {n}", url, AI_TEXT) for n, url in enumerate(plan.start_urls, 1)]
    facts += [Fact("Languages kept", ", ".join(plan.languages) or "every language"),
              Fact("PDF files", "read" if plan.include_pdfs else "left out"),
              Fact("Knowledge base", "a snapshot is taken before each start address, so the crawl can be undone; "
                                     "the pages later reach AI models as reference data"),
              Fact("Embedding model", "downloaded once (about a GB) if it is not on this computer yet")]
    room = plan.disk
    if room is not None:  # free space moves while a question waits: shown, never part of the decision
        facts.append(Fact("Disk", f"knowledge snapshot taken first: {room.sentence()}", volatile=True))
    bound = {"start_urls": list(plan.start_urls), "max_pages": plan.max_pages, "max_depth": plan.max_depth,
             "delay": plan.delay, "languages": list(plan.languages), "include_pdfs": plan.include_pdfs}
    # Situation warnings when the crawl may store many pages, and when the snapshot taken first fits but leaves little
    # free space (eighth round, answer 2: one that does not fit is the blocker snapshot_no_room, before the question).
    # The embedding model's first download is not warned about yet (whether the model is on this computer is not known
    # before the crawl). No count of snapshots is ever a warning: they have no limit, none is deleted by itself.
    warnings = []
    if plan.max_pages * sites >= MANY_PAGES:
        warnings.append(warn("many_pages", max_pages=plan.max_pages * sites, sites=sites))
    if room is not None and room.verdict == "tight":
        warnings.append(warn("disk_tight", free_after_gb=gb_number(room.check.after),
                             reserve_gb=gb_number(room.check.reserve)))
    warnings = tuple(warnings)
    return CallFacts(consequence=consequence, facts=tuple(facts), bound=bound, warnings=warnings,
                     numbers={"start_urls": sites, "max_pages": plan.max_pages, "max_depth": plan.max_depth},
                     consequence_template=consequence)


def refusal(e: CrawlRefused) -> ToolError:
    """A refused crawl as the tool's error, marked with its blocker (the person's action goes with it)."""
    error = ToolError(str(e), code=e.code, next=e.next)
    return with_blocker(error, e.blocker) if getattr(e, "blocker", "") else error


def register_crawl_tools(registry: ToolRegistry, framework, jobs) -> ToolRegistry:
    service = CrawlJobService(framework, jobs)

    def prepare(inp: CrawlIn, ctx: ToolContext) -> CallFacts:
        try:
            plan = service.plan(inp.start_urls, inp.max_pages, inp.max_depth, inp.languages, inp.include_pdfs,
                                ctx.actor)
        except CrawlRefused as e:
            raise refusal(e) from None
        return _plan_facts(plan)

    def crawl_docs_site(inp: CrawlIn, ctx: ToolContext) -> CrawlStartedOut:
        bound = dict(ctx.facts.bound) if ctx.facts is not None else {}
        if not bound.get("start_urls"):
            raise ToolError("The crawl is not bound to what the person was shown; nothing was crawled.",
                            code="failed", next="Call crawl_docs_site again through the framework's tool registry.")
        plan = CrawlPlan(tuple(bound["start_urls"]), int(bound["max_pages"]), int(bound["max_depth"]),
                         float(bound["delay"]), tuple(bound["languages"]), bool(bound["include_pdfs"]))
        try:
            job = service.start(plan, ctx.owner, ctx.actor)
        except CrawlRefused as e:
            raise refusal(e) from None
        return CrawlStartedOut(job_id=job.job_id, state=job.state, start_urls=list(plan.start_urls),
                               max_pages=plan.max_pages, max_depth=plan.max_depth)

    registry.register(ToolSpec(
        "crawl_docs_site", "Deep Ingest: crawl documentation sites",
        "Reads public documentation sites into the local knowledge base Ditto uses (Deep Ingest), as a job. The "
        "person is asked each time and sees every start address; the sites' opt-outs (robots.txt, robots "
        "directives, TDMRep) are always honoured. Needs the one-time Deep Ingest notice read by the person first. "
        "Poll get_job_status; then read pages with search_knowledge. "
        'Example: {"start_urls": ["https://docs.radxa.com/en/zero/zero3"], "max_pages": 20}',
        ToolTier.LOCAL_WRITE, CrawlIn, CrawlStartedOut, crawl_docs_site, takes_context=True, open_world=True,
        prepare=prepare, default_decision=Decision.ASK, max_decision=Decision.ASK,
        fields={"start_urls": FieldMeta(kind="url"), "max_pages": FieldMeta(kind="value"),
                "max_depth": FieldMeta(kind="value"), "languages": FieldMeta(kind="value", split=True),
                "include_pdfs": FieldMeta(kind="value")},
        examples=({"start_urls": ["https://docs.radxa.com/en/zero/zero3"], "max_pages": 20},),
        # 1 at a time and 5 a day per client (the limits table); 300 stored pages a day are counted by the service
        limits=ToolLimits(per_day=5, concurrent=1, running=lambda ctx: service.running(), what="crawls"),
        summary=lambda o: f"Crawl job {fact(o.job_id)}: {fact(o.state)}."))
    return registry
