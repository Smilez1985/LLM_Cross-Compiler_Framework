#!/usr/bin/env python3
"""
The client's own healing proposal (MCP design 2.3, work package T4): `propose_healing_fix`.

An AI client that takes over Ditto's job (DECISIONS round 1 item 5) analyses a failed build itself (prompt
`diagnose_build_failure`) and hands its proposal to the framework. It passes the same checks as Ditto's own analysis:
the HealingAnalysis model (summary up to 300 characters, cause and step up to 2000, confidence 0-1, HOST or DEVICE)
and the dangerous-command filter. It is kept with the build as a manual step for the person, shown in
get_build_status, the GUI and `llm-cli build status` with who proposed it, and is never run: resolve_healing refuses
to apply it. A low confidence with an honest "cause unclear" is a valid result.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.tools.grants import tool_rule
from orchestrator.tools.output import ToolOutput, fact
from orchestrator.tools.policy import ToolTier
from orchestrator.tools.registry import ToolContext, ToolRegistry, ToolSpec
from orchestrator.tools.spec import FieldMeta, GrantRule

PROPOSAL_NOTE = ("Shown to the person as a manual step, marked as written by an AI client; the framework never runs "
                 "it, and resolve_healing cannot apply it.")


class ProposeHealingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1, max_length=64, pattern=r"^[\w.-]+$", description="The failed build request")
    summary: str = Field(min_length=1, max_length=300, description="One sentence: what went wrong")
    root_cause: str = Field(default="", max_length=2000, description="The cause; say so when it is unclear")
    manual_step: str = Field(min_length=1, max_length=2000,
                             description="One step for the person to do by hand: a short instruction or one command")
    confidence: float = Field(ge=0, le=1, description="How sure you are, from 0 to 1")
    where: Literal["HOST", "DEVICE"] = Field(default="HOST",
                                             description="HOST: the build computer; DEVICE: the edge device")


class ProposeHealingOut(ToolOutput):
    proposal_id: str
    request_id: str
    note: str = PROPOSAL_NOTE


EXAMPLE = {"request_id": "req_1a2b3c4d", "summary": "The conversion ran out of memory.",
           "root_cause": "The 7B model needs about 14 GB RAM during conversion; the build host has 8 GB.",
           "manual_step": "Choose a smaller model or add swap, then start the build again.", "confidence": 0.7}


def register_healing_tools(registry: ToolRegistry, framework, request_field: FieldMeta) -> ToolRegistry:
    service = getattr(framework, "build_service", None)

    def propose_healing_fix(inp: ProposeHealingIn, ctx: ToolContext) -> ProposeHealingOut:
        proposal = service.propose_fix(inp.request_id, inp.summary, inp.root_cause, inp.manual_step, inp.confidence,
                                       inp.where, proposed_by=ctx.actor, client=ctx.client)
        return ProposeHealingOut(proposal_id=proposal.proposal_id, request_id=inp.request_id)

    registry.register(ToolSpec(
        "propose_healing_fix", "Propose a fix for a failed build",
        "Hands your own analysis of a failed build to the framework: summary, cause, one manual step and your "
        "confidence. It goes through the same dangerous-command filter as Ditto's analysis and is shown to the "
        "person as a manual step written by an AI client; the framework never runs it. Use it after "
        "diagnose_build_failure or read_build_log; an unclear cause with a low confidence is an honest answer.",
        ToolTier.LOCAL_WRITE, ProposeHealingIn, ProposeHealingOut, propose_healing_fix, takes_context=True,
        fields={"request_id": request_field, "confidence": FieldMeta(kind="value"),
                "where": FieldMeta(kind="choice", choices=("HOST", "DEVICE"))},
        examples=(EXAMPLE,),
        # It only stores text the person reads later, so a standing approval may cover the tool itself (design 6.10).
        grant=GrantRule(rule=tool_rule("propose_healing_fix"),
                        warning="store fix proposals for failed builds (text you read; the framework never runs it)",
                        facts=lambda inp, ctx: {"tool": "propose_healing_fix"}),
        consequence="Stores a fix proposal for a failed build; it is shown to you as a manual step written by an AI "
                    "client and is never run by the framework.",
        summary=lambda o: f"Fix proposal {fact(o.proposal_id)} stored for build request {fact(o.request_id)}."))
    return registry
