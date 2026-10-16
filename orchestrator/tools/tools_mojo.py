#!/usr/bin/env python3
"""
The Mojo tools (round 6 design 7.8, round 7 item 11'): an AI checks the installation and repairs a harmless problem.

- `run_mojo_oracle` (read-only): every finding with what it is, where (a label, never a host path), why it matters and
  what Mojo Miracle can do about it (`repair.how`: miracle, deletion_list, person, admin, not_automatic), the same
  verdicts the person's `llmcf mojo` shows.
- `run_mojo_miracle`: repairs one problem whose `repair.how` is "miracle" (it only narrows access or switches to an
  intact build). Every call is asked: the person sees the exact plan (the full path in the framework's own window
  only); there is no standing approval and no delegation covers it yet; the handler applies exactly that plan or
  nothing. It never deletes: a problem that needs a deletion, the person or an administrator is refused before any
  question, with the reason. The result says what was repaired, or what was not and why, by labels.
- `run_doctor`: the 3.0.0 name with its 3.0.0 output, callable but never listed; tools/policy.py TOOL_RENAMES lets a
  person's setting for either name apply to both.

Extension point (packages M3a and M6): the Mojo blocker codes (repair_manual, repair_needs_person, wrong_account) and
the delegation group "repairs" come with the delegations (WP11); facts_catalog.TOOL_FACTS and
delegation.DELEGATION_SCOPE already name run_mojo_miracle as the person's.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.tools.output import ToolOutput, UntrustedText, count, fact
from orchestrator.tools.policy import Decision, ToolTier
from orchestrator.tools.registry import ToolContext, ToolError, ToolSpec
from orchestrator.tools.spec import CallFacts, Fact, FieldMeta

CODE_PATTERN = r"^[A-Za-z0-9_.+-]{1,120}$"
HOW = Literal["miracle", "deletion_list", "person", "admin", "not_automatic"]


class NoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DoctorFinding(BaseModel):
    subsystem: str
    code: str
    severity: str
    message: UntrustedText  # can quote program output (Docker, file systems)
    fix_hint: str = ""


class DoctorOut(ToolOutput):
    exit_code: int
    findings: List[DoctorFinding]


class MojoRepairOut(BaseModel):
    how: HOW = Field(description="miracle: run_mojo_miracle repairs it; deletion_list: only the person deletes, from "
                                 "the framework's list; person: only the person can do it; admin: an administrator "
                                 "must do it; not_automatic: nothing repairs it automatically")
    summary: UntrustedText = Field(description="What Mojo Miracle would do, or why it does not")
    next: str = Field(description="What you do next")


class MojoFinding(DoctorFinding):
    where: UntrustedText = Field(default="", description="What the finding is about, by label (never a host path)")
    why: str = Field(default="", description="Why it matters")
    repair: Optional[MojoRepairOut] = Field(default=None, description="null for findings that are no problem")


class MojoOracleOut(ToolOutput):
    exit_code: int = Field(description="0 no problem, 1 warnings, 2 errors")
    findings: List[MojoFinding]
    summary: str


class RunMojoMiracleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=120, pattern=CODE_PATTERN,
                      description="The code of a finding run_mojo_oracle listed with repair.how 'miracle', e.g. "
                                  "state.permissions")


class MojoMiracleOut(ToolOutput):
    code: str
    outcome: Literal["repaired", "still_present", "already_fixed", "plan_changed", "failed", "refused"]
    repaired: bool = Field(description="True only when it was changed and Mojo Oracle no longer finds the problem")
    what: str = Field(description="What Mojo Miracle did or planned, by labels")
    reason: UntrustedText = Field(default="", description="Why it was not repaired")


def _channel(value: str) -> str:
    """The confirmation channel for the audit entry, when it is one the audit log knows."""
    from orchestrator.tools.audit import confirm_channel_ok
    return value if value and confirm_channel_ok(value) else ""


def mojo_tool_specs(framework) -> List[ToolSpec]:
    """run_mojo_oracle, run_mojo_miracle and the hidden run_doctor."""
    from orchestrator.utils.lazy_imports import LazyModule
    views = LazyModule("orchestrator.Core.mojo.views")  # with the Mojo checks: imported at the first call

    def run_doctor(_inp: NoInput) -> DoctorOut:
        from orchestrator.Core.doctor import Doctor
        report = Doctor(framework).run()  # diagnosis only: repairs are run_mojo_miracle's, after the person's yes
        # Folders by label, other paths by file name: an AI never reads a host path (the person's CLI keeps them).
        return DoctorOut(exit_code=report.exit_code,
                         findings=[DoctorFinding(**row) for row in views.ai_findings(framework, report)])

    def run_mojo_oracle(_inp: NoInput) -> MojoOracleOut:
        from orchestrator.Core.mojo import Mojo, offers
        report = Mojo(framework).run()
        offered = offers(framework, report)
        return MojoOracleOut(exit_code=report.exit_code,
                             findings=[MojoFinding(**row) for row in views.oracle_rows(framework, report, offered)],
                             summary=views.oracle_summary(offered))

    def prepare_miracle(inp: RunMojoMiracleIn, ctx: ToolContext) -> CallFacts:
        """The plan the person is asked about, made from a fresh Mojo Oracle run; anything but a harmless repair is
        refused here, before any question."""
        from orchestrator.Core.mojo import Mojo, Severity, offer_for
        report = Mojo(framework).run()
        problem = next((f for f in report.findings if f.code == inp.code
                        and f.severity in (Severity.WARN, Severity.ERROR)), None)
        if problem is None:
            fine = any(f.code == inp.code for f in report.findings)
            raise ToolError(f"Mojo Oracle does not find a problem '{inp.code}' now"
                            + (" (it reports it as fine)" if fine else "") + ", so there is nothing to repair.",
                            code="missing_information",
                            next="Call run_mojo_oracle again; repair only a problem it lists with repair.how "
                                 "'miracle'.")
        offer = offer_for(framework, problem)
        label = views.labeller(framework)
        if offer.verdict != "miracle":
            # Extension point M3a (with WP11): these refusals get the catalogue's blocker codes (repair_manual,
            # repair_needs_person); new blocker codes change DELEGATION_RULES_VERSION, so they land with delegations.
            raise ToolError(f"Mojo Miracle does not repair '{inp.code}': {label(offer.reason)}. Nothing was changed.",
                            code="not_supported" if offer.verdict == "not_automatic" else "needs_person",
                            next=views.ai_next(offer.verdict, inp.code))
        plan = offer.plan
        facts = [Fact("Problem", f"{problem.code}: {label(problem.message)}")]
        if problem.why:
            facts.append(Fact("Why it matters", label(problem.why)))
        for step in plan.steps:
            facts.append(Fact("Change", f"{step.target_label}: {step.before} -> {step.after}"))
            if step.full_path:
                facts.append(Fact("Where", step.full_path, person_only=True))
        facts += [Fact("Can be undone", "yes" if all(s.undoable for s in plan.steps) else "no"),
                  Fact("Deletes", "nothing")]
        consequence = f"Mojo Miracle repairs {problem.code}: {plan.summary}."
        return CallFacts(consequence=consequence, facts=tuple(facts),
                         bound={"code": inp.code, "plan_sha256": plan.sha256()},
                         scope={"repair_class": plan.repair_class()}, consequence_template=consequence)

    def run_mojo_miracle(inp: RunMojoMiracleIn, ctx: ToolContext) -> MojoMiracleOut:
        from orchestrator.Core.mojo.repairs.engine import apply_repair
        bound = dict(ctx.facts.bound) if ctx.facts is not None else {}
        if bound.get("code") != inp.code or not bound.get("plan_sha256"):
            raise ToolError("The repair is not bound to what the person was shown; nothing was changed.",
                            code="failed", next="Call run_mojo_miracle again through the framework's tool registry.")
        # Every call is asked (max ask, no standing approval, no delegation yet): the person answered it.
        result = apply_repair(framework, inp.code, actor=ctx.actor, decision="ask", answered_by="person",
                              confirm_channel=_channel(ctx.confirm_channel), expect_sha256=bound["plan_sha256"],
                              links=[ctx.audit_started] if ctx.audit_started else [])
        return MojoMiracleOut(**views.miracle_row(framework, result))

    def miracle_summary(o: MojoMiracleOut) -> str:
        return f"Mojo Miracle on {fact(o.code)}: {fact(o.outcome)}."

    code_field = {"code": FieldMeta(kind="id", list_tool="run_mojo_oracle")}
    return [
        ToolSpec("run_mojo_oracle", "Check the installation (Mojo Oracle)",
                 "Runs the framework's health checks (Mojo Oracle) and reports every finding: what it is, where (by "
                 "label), why it matters and whether Mojo Miracle can repair it (repair.how and repair.next). "
                 "Read-only; it never changes anything.",
                 ToolTier.READ, NoInput, MojoOracleOut, run_mojo_oracle, idempotent=True,
                 summary=lambda o: (f"Mojo Oracle: exit code {fact(o.exit_code)}, "
                                    + count(len(o.findings), "finding", "findings") + ".")),
        ToolSpec("run_mojo_miracle", "Repair one problem (Mojo Miracle)",
                 "Repairs one problem that run_mojo_oracle listed with repair.how 'miracle' (it only narrows access "
                 "or switches to an intact build, and can be undone). The framework shows the person the exact plan "
                 "first; it never deletes anything. The result says whether it was repaired, and if not, why. "
                 'Example: {"code": "state.permissions"}',
                 ToolTier.LOCAL_WRITE, RunMojoMiracleIn, MojoMiracleOut, run_mojo_miracle, takes_context=True,
                 idempotent=True, default_decision=Decision.ASK, max_decision=Decision.ASK, prepare=prepare_miracle,
                 fields=code_field, examples=({"code": "state.permissions"},),
                 consequence="Repairs one problem Mojo Oracle found (only a harmless repair; nothing is deleted).",
                 summary=miracle_summary),
        ToolSpec("run_doctor", "Check the installation",
                 "The 3.0.0 name of run_mojo_oracle, kept so older clients keep working (not listed). Runs the "
                 "framework's health checks and returns findings with fix hints. Read-only; it never applies fixes.",
                 ToolTier.READ, NoInput, DoctorOut, run_doctor, idempotent=True, model_facing=False,
                 summary=lambda o: (f"Health check: exit code {fact(o.exit_code)}, "
                                    + count(len(o.findings), "finding", "findings") + ".")),
    ]


__all__ = ["DoctorFinding", "DoctorOut", "MojoFinding", "MojoMiracleOut", "MojoOracleOut", "MojoRepairOut",
           "RunMojoMiracleIn", "mojo_tool_specs"]
