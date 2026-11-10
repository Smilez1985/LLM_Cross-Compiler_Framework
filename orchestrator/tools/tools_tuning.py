#!/usr/bin/env python3
"""
Auto-Tuning for an AI (Core/tuning.py, DESIGN "Targets, hardware and templates"): `start_tuning` and
`get_tuning_report`, two optional tools (denied until the person enables them, like every optional tool).

`start_tuning` is a build of the build service and asks like one: its look-up is start_build's plan for the model,
target and board (the commit, the download, licence, gating, image build, the pre-flight check), plus the tuning plan
(the candidates with their memory estimate, the reference, the evaluation's size and the disk the run needs). The
person answers it every time: the tool's decision is capped at ask, so no standing approval and no "allow" setting
starts a tuning run without the person. `get_tuning_report` reads the finished runs: numbers and words, never a path.
"""

import dataclasses
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.tools.output import ToolOutput, fact
from orchestrator.tools.registry import ToolError


class TuningIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    model: str = Field(min_length=1, max_length=512,
                       description="Hugging Face repo id (owner/name), or a local model as <label>:<relative path> "
                                   "or its file id; never a path")
    revision: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{40}$", description="Commit of the model")
    target: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][\w.-]*$", description="Target id")
    board: Optional[str] = Field(default=None, max_length=64,
                                 description="Board of the target; its profile's types that fit are the candidates")
    probe: Optional[str] = Field(default=None, pattern=r"^(local|[0-9a-f]{16})$",
                                 description="The board's hardware probe by its id (get_hardware_probe)")
    types: List[str] = Field(default_factory=list, max_length=8,
                             description="Types to measure as well, whatever the memory estimate says")
    imatrix: Literal["auto", "both"] = Field(default="auto", description="auto: types below 5 bits with the "
                                             "importance matrix; both: those types with and without it")
    chunks: int = Field(default=16, ge=4, le=48, description="Chunks of the evaluation text (default 16)")
    chunk_tokens: Literal[256, 512, 1024] = Field(default=512, description="Tokens per chunk (default 512)")


class TuningReportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: Optional[str] = Field(default=None, max_length=64, pattern=r"^(latest|a_[0-9a-f]{16})$",
                               description="A tuning run's id (a_...) or latest; omitted: the list of runs")


class TuningReportOut(ToolOutput):
    runs: List[Dict[str, Any]] = Field(default_factory=list, description="The finished tuning runs, newest first")
    report: Optional[Dict[str, Any]] = Field(default=None, description="The chosen run's report (numbers and words)")


def tuning_tool_specs(registry, service, fields: Dict[str, Any], limits, handle_out) -> List[Any]:
    """The ToolSpecs of start_tuning and get_tuning_report (registered by framework_tools)."""
    from orchestrator.schemas.build import BuildRequestIn, StartBuildIn
    from orchestrator.tools.policy import Decision, ToolTier
    from orchestrator.tools.registry import ToolSpec
    from orchestrator.tools.spec import Fact, FieldMeta
    from orchestrator.tools.tools_builds import problem_error

    def refusal(e: Exception) -> ToolError:
        return ToolError(str(e), code=getattr(e, "code", "failed") or "failed",
                         next=getattr(e, "next", "") or "Change the request (another board, model or types).",
                         field=getattr(e, "field", "") or "")

    def request_of(inp: TuningIn) -> BuildRequestIn:
        return BuildRequestIn(model=inp.model, revision=inp.revision, target=inp.target, format="gguf",
                              board=inp.board, probe=inp.probe)

    def prepare_tuning(inp: TuningIn, ctx):
        from orchestrator.Core.build_service import BuildServiceError
        from orchestrator.Core.tuning import TuningError
        try:
            plan, _req = service.plan_tuning(request_of(inp), types=list(inp.types), imatrix=inp.imatrix,
                                             chunks=inp.chunks, ctx=inp.chunk_tokens)
        except (TuningError, BuildServiceError) as e:
            raise refusal(e) from None
        first = plan.candidates[0]
        build_plan = service.plan(StartBuildIn(model=inp.model, revision=inp.revision, target=inp.target,
                                               format="gguf", board=inp.board, probe=inp.probe,
                                               quantization=first.quantization, use_imatrix=first.imatrix))
        if build_plan.problems:
            raise problem_error(build_plan.problems)
        base = build_plan.call_facts()
        facts = tuple(Fact(f"Tuning {i + 1}", line) for i, line in enumerate(plan.lines()))
        bound = dict(base.bound)
        bound["tuning"] = plan.as_dict()
        consequence = (f"Measures {len(plan.candidates)} quantization types of the model for "
                       f"{plan.board or plan.target} in a Docker build on this computer (no model is kept, a report "
                       "is): ")
        return dataclasses.replace(base, consequence=consequence + base.consequence,
                                   consequence_template=consequence + base.consequence_template,
                                   facts=facts + tuple(base.facts), bound=bound,
                                   digest_keys=tuple(base.digest_keys) + ("tuning",), tool="")

    def start_tuning(inp: TuningIn, ctx):
        from orchestrator.Core.build_service import BuildServiceError
        from orchestrator.Core.disk_plan import InsufficientSpace
        from orchestrator.Core.tuning import TuningError, TuningPlan
        bound = ctx.facts.bound if ctx.facts is not None else {}
        if not isinstance(bound.get("tuning"), dict):
            raise ToolError("The tuning run is not bound to the facts the person was shown; nothing was started.",
                            code="failed", next="Call start_tuning again through the framework's tool registry.")
        request = request_of(inp).model_copy(update={"revision": bound.get("revision") or inp.revision})
        try:
            return service.start_tuning(request, plan=TuningPlan.from_dict(bound["tuning"]), owner=ctx.owner,
                                        preflight_accepted=list(bound.get("preflight") or []))
        except InsufficientSpace as e:
            raise ToolError(str(e), code="insufficient_resources",
                            next="Tell the person how much disk the run needs; fewer chunks need less.") from None
        except (TuningError, BuildServiceError) as e:
            raise refusal(e) from None

    def get_tuning_report(inp: TuningReportIn) -> TuningReportOut:
        from orchestrator.Core.build_service import BuildServiceError
        from orchestrator.Core.tuning import ai_facts
        reports = service.tuning_reports()
        runs = [{"id": r["id"], "model": r["report"].get("model"), "board": r["report"].get("board"),
                 "created": r["report"].get("created"),
                 "recommendation": (r["report"].get("recommendation") or {}).get("label")} for r in reports[:50]]
        if not inp.run:
            return TuningReportOut(runs=runs)
        try:
            found = service.tuning_report(inp.run)
        except BuildServiceError as e:
            raise refusal(e) from None
        return TuningReportOut(runs=runs, report={"id": found["id"], **ai_facts(found["report"])})

    tuning_fields = {"model": fields["model"], "revision": fields["revision"],
                     "target": FieldMeta(kind="id", list_tool="list_targets"),
                     "board": FieldMeta(list_tool="get_target_capabilities"),
                     "probe": FieldMeta(kind="id", list_tool="get_hardware_probe"),
                     "types": FieldMeta(list_tool="get_target_capabilities")}
    return [
        ToolSpec("start_tuning", "Measure the best quantization (Auto-Tuning)",
                 "Measures which GGUF quantization of a model, and whether with the importance matrix, gives the "
                 "best quality that fits a board: the board profile's types that fit its memory (plus your types) "
                 "are each quantized as a build makes them and compared with the original by the KL divergence and "
                 "the top-1 agreement on a fixed evaluation text, in a Docker build on this computer. The person is "
                 "asked every time (no standing approval covers it). The report (get_tuning_report) recommends a "
                 "type; nothing is switched by itself. Returns a request id (get_build_status follows it).",
                 ToolTier.LOCAL_WRITE, TuningIn, handle_out, start_tuning, takes_context=True, open_world=True,
                 prepare=prepare_tuning, limits=limits, fields=tuning_fields,
                 default_decision=Decision.DENY, max_decision=Decision.ASK,
                 examples=({"model": "HuggingFaceTB/SmolLM2-360M-Instruct", "target": "ARM64", "board": "bcm2712"},),
                 summary=lambda o: f"Tuning request {fact(o.request_id)} is {fact(o.status)}.",
                 consequence="Converts a model and measures several quantization types in a Docker build on this "
                             "computer; it may download the model first and take a long time."),
        ToolSpec("get_tuning_report", "Read Auto-Tuning reports",
                 "The finished tuning runs and, for one run (its id or latest), its report: per candidate type the "
                 "file size, the memory verdict on the board, the KL divergence with its uncertainty, the top-1 "
                 "agreement and the tokens per second measured on the board (only when a board test measured it), "
                 "and the recommendation with its reason. Thresholds: KLD up to 0.05 good, up to 0.10 usable; "
                 "a measured speed below 8 tokens/s is not recommended.",
                 ToolTier.READ, TuningReportIn, TuningReportOut, get_tuning_report, idempotent=True,
                 default_decision=Decision.DENY, examples=({}, {"run": "latest"}),
                 summary=lambda o: (f"Tuning report of {fact(o.report.get('model', ''))}: recommended "
                                    f"{fact((o.report.get('recommendation') or {}).get('label') or 'none')}."
                                    if o.report else f"Tuning runs: {len(o.runs)}.")),
    ]


__all__ = ["TuningIn", "TuningReportIn", "TuningReportOut", "tuning_tool_specs"]
