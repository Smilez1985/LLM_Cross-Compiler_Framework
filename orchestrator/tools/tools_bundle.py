#!/usr/bin/env python3
"""
The board bundle for AI callers (deploy way 3, orchestrator/runtime/bundle/; the same steps as `llmcf bundle` and
Boards > Board bundle..., through runtime/bundle/service.py). Optional like the device runtime tools beside them
(tools_runtime.py registers these with its board helpers): denied, so not listed, until the person enables them.

- bundle_plan (read): the plan of a proposed choice (a preset or blocks, variable values, the unprivileged-only
  variant) for a trusted board, as labels only (BundlePlan.labels(): block ids, kinds, variable values without the
  account variables, file roles, formats, sizes and licences, root actions by type, memory numbers, the checks
  against the board's probe, problems and warnings as the framework's sentences with every path and address
  scrubbed), plus the building blocks it may choose from (Block.label()). An AI never picks a local file, an online
  address or an account: the files are the person's choice in the window. Nothing is written, no board is contacted.
- bundle_deploy (external): sends a bundle the person built to its trusted board and runs its installer's apply,
  after the person's yes in the framework's own window only (channels ("framework",)); never a standing approval
  (grants.NEVER_GRANT), never under a delegation (delegation.NEVER_DELEGATE). The login is
  runtime/device/service.connect with the framework's key only and the host key the person was shown; the result
  holds the installer's report scrubbed (no account, no path of the board or of this computer).

Every caller of the block catalog passes the verified cached community index with its revocations
(service.catalog_sources: CommunityManager.cached_index(revocations=True)), so a revoked block is never used.
"""

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from orchestrator.tools.output import ToolOutput, fact
from orchestrator.tools.policy import Decision, ToolTier
from orchestrator.tools.registry import ToolContext, ToolError, ToolSpec
from orchestrator.tools.spec import CallFacts, Fact, FieldMeta, ToolLimits

BLOCK_ID = r"^[a-z0-9][a-z0-9._-]{0,47}$"
COMPONENT_ID = r"^[a-z][a-z0-9-]{0,40}$"
VARIABLE = r"^[a-z][a-z0-9_]{0,31}$"
VALUE_TEXT = r"^[A-Za-z0-9._:,+-]{0,64}$"   # a plain word or number; the plan checks it against its type again
PRESETS = ("llm", "voice", "stt")


class BundlePlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: str = Field(min_length=16, max_length=16, pattern=r"^[0-9a-f]{16}$",
                           description="A trusted, paired board from list_devices")
    preset: Optional[Literal[PRESETS]] = Field(default=None, description="Start from a preset: llm, voice or stt")
    blocks: List[str] = Field(default_factory=list, max_length=8,
                              description="Building blocks by id (after the preset's); bundle_plan lists them")
    values: Dict[str, Dict[str, Union[StrictBool, int, str]]] = Field(
        default_factory=dict, max_length=16,
        description='Variables per component, e.g. {"stt": {"port": 8090, "autostart": false}}; each is checked '
                    "against its type")
    unprivileged_only: bool = Field(default=False, description="Skip the root actions (no autostart unit)")

    def checked(self) -> "BundlePlanIn":
        import re
        for block in self.blocks:
            if not re.fullmatch(BLOCK_ID, block):
                raise ToolError(f"'{block[:60]}' is no block id.", code="invalid_argument", field="blocks",
                                next="Call bundle_plan without blocks to list them.")
        for comp, values in self.values.items():
            if not re.fullmatch(COMPONENT_ID, comp) or len(values) > 32:
                raise ToolError("A component is named by its kind (llm, stt, a second one stt-2).",
                                code="invalid_argument", field="values")
            for name, value in values.items():
                if not re.fullmatch(VARIABLE, name) or (isinstance(value, str) and
                                                         not re.fullmatch(VALUE_TEXT, value)):
                    raise ToolError(f"{comp}.{name[:32]}: a variable is a plain word or number.",
                                    code="invalid_argument", field="values")
        return self


class BundlePlanOut(ToolOutput):
    plan: Dict[str, Any] = Field(description="The plan as labels (BundlePlan.labels()): no path, address or account")
    blocks: List[Dict[str, Any]] = Field(description="The building blocks it may choose from, as labels")
    files_by_person: bool = Field(default=True, description="The files (models, programs) are chosen by the person "
                                                            "in the framework's Board bundle window")


class BundleDeployIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bundle_id: str = Field(min_length=1, max_length=48, pattern=BLOCK_ID,
                           description="A bundle the person built (bundle_plan names none; ask the person)")


class BundleJobOut(ToolOutput):
    job_id: str
    kind: str
    state: str


def bundle_tool_specs(registry: Any, framework: Any, jobs: Any, h: Any) -> List[ToolSpec]:
    """The two bundle tools over the board helpers `h` of tools_runtime.register_runtime_tools (trusted, svc_error,
    job_error, device_lines, session_hooks, board_events, audited, state_dir, framework_mode, scrub,
    folder_labels): one set of
    board helpers for every board tool, never a copy."""

    def catalog_sources():
        from orchestrator.runtime.bundle import service
        return service.catalog_sources(framework)

    def bundle_plan(inp: BundlePlanIn, ctx: ToolContext) -> BundlePlanOut:
        from orchestrator.Core.config_manager import framework_mode
        from orchestrator.runtime.bundle import blocks as blk
        from orchestrator.runtime.bundle import generator as gen
        from orchestrator.runtime.bundle import service
        inp = inp.checked()
        device = h.trusted(inp.device_id)
        app_root, index = catalog_sources()
        catalog = blk.load_catalog(app_root, index=index)
        config = getattr(framework, "config", None)
        selection = gen.Selection(blocks=list(gen.PRESETS.get(inp.preset or "", ())), preset=inp.preset or "",
                                  unprivileged_only=inp.unprivileged_only)
        selection.blocks += [b for b in inp.blocks if b not in selection.blocks]
        selection.values = {c: {k: (str(v).lower() if isinstance(v, bool) else v) for k, v in vals.items()}
                            for c, vals in inp.values.items()}
        blocks = [b.label() for b in catalog.blocks.values()]
        notes = [h.scrub(n) for n in catalog.notes]
        if not selection.blocks:
            return BundlePlanOut(plan={}, blocks=blocks, notes=notes + [
                "Choose a preset or blocks; the person chooses the files in the framework's Board bundle window"])
        try:
            plan = service.plan_for_device(h.state_dir(), device.device_id, selection, config=config,
                                           app_root=app_root, mode=framework_mode(config), index=index)
        except gen.BundleError as e:
            raise ToolError(h.scrub(str(e)), code="failed", next="Ask the person to probe the board first.") \
                from None
        return BundlePlanOut(plan=plan.labels(), blocks=blocks, notes=notes)

    def stored(bundle_id: str):
        from orchestrator.runtime.bundle import generator as gen
        store = gen.BundleStore(h.state_dir())
        try:
            return store, store.get(bundle_id)
        except gen.BundleError:
            raise ToolError(f"No stored bundle '{bundle_id}'.", code="unknown_id", field="bundle_id",
                            next="Ask the person which bundle they built (the framework's Board bundle "
                                 "window).") from None

    def not_revoked(built) -> None:
        """A block the signed index revoked since the build: nothing is asked or sent (service.revoked_blocks)."""
        from orchestrator.runtime.bundle import service
        revoked = service.revoked_blocks(built, *catalog_sources())
        if revoked:
            raise ToolError(h.scrub("; ".join(revoked)), code="failed",
                            next="Ask the person to build the bundle again without that block in the framework's "
                                 "Board bundle window")

    def deploy_facts(inp: BundleDeployIn, ctx: ToolContext) -> CallFacts:
        from orchestrator.runtime.bundle import service
        from orchestrator.runtime.bundle.generator import BundleError, _mb
        from orchestrator.runtime.device import service as dev
        store, built = stored(inp.bundle_id)
        device = h.trusted(built.manifest.board.device_id)
        problems = store.check(built)
        if problems:
            raise ToolError("The stored bundle is not the one that was built; nothing is sent.", code="failed",
                            next="Ask the person to build the bundle again in the framework's Board bundle window")
        not_revoked(built)
        try:
            _host, _port, user, _label = service.board_contact(h.state_dir(), device.device_id)
            fp = dev.read_key(device, h.state_dir(), step="bundle")
        except BundleError as e:
            raise ToolError(h.scrub(str(e)), code="needs_person",
                            next="Ask the person to set the board's deploy user in the board's deploy settings") \
                from None
        except dev.DeviceRuntimeError as e:
            raise h.svc_error(e, [u for u in (device.user,) if u]) from None
        m = built.manifest
        size = sum(entry.size for _n, _p, entry in built.payload())
        comps = ", ".join(f"{c.id} ({c.block})" for c in m.components)
        facts = h.device_lines(device, fp) + [
            Fact("Bundle", f"{m.bundle_id}: {comps}; mode {m.mode}; sends up to {_mb(size)}"),
            Fact("Deploy as", f"{user}@{device.address}, with the framework's key only", person_only=True),
            Fact("On the board", "the bundle's signed installer checks every file and installs the unprivileged "
                                 "steps, each only once; root actions stop it (they need llmcf-install)"
                 + ("" if m.root_actions else "; this bundle has none")),
            Fact("Undo", "the installer the board keeps can roll back or uninstall the bundle's own steps "
                         "(the framework's Board bundle window)")]
        consequence = (f"Sends bundle {m.bundle_id} to the board {device.label} and installs its unprivileged "
                       "steps there.")
        return CallFacts(consequence=consequence, facts=tuple(facts), cap=Decision.ASK.value,
                         bound={"bundle_id": m.bundle_id, "device_id": device.device_id, "fingerprint": fp,
                                "manifest_sha256": built.manifest_sha256, "user": user},
                         scope={"device_id": device.device_id}, consequence_template=consequence,
                         names={"device": device.label})

    def bundle_deploy(inp: BundleDeployIn, ctx: ToolContext) -> BundleJobOut:
        from orchestrator.runtime.bundle import service
        from orchestrator.runtime.device import service as dev
        bound = dict(ctx.facts.bound) if ctx.facts is not None else {}
        if bound.get("bundle_id") != inp.bundle_id or not bound.get("fingerprint"):
            raise ToolError("The deploy is not bound to what the person was shown; nothing was done.", code="failed",
                            next="Call bundle_deploy again through the framework's registry.")
        store, built = stored(inp.bundle_id)
        if built.manifest_sha256 != bound["manifest_sha256"] or built.manifest.board.device_id != bound["device_id"]:
            raise ToolError("The bundle changed after the person was asked; nothing was sent.", code="failed",
                            next="Call bundle_deploy again.")
        not_revoked(built)
        device = h.trusted(bound["device_id"])
        on_client, cancel, stop = h.session_hooks()
        users = [u for u in (device.user, bound.get("user")) if u]

        def work(handle) -> Dict[str, Any]:
            from orchestrator.Core.job_service import EXTERNAL_KEY, JobCancelled
            from orchestrator.runtime.bundle.generator import BundleError
            from orchestrator.runtime.device.remote import RemoteError
            from orchestrator.security.paths import device_text

            def clean(text: str) -> str:
                return device_text(str(text), users, h.folder_labels())[:300]

            def open_session():
                return dev.connect(device, h.state_dir(), bound["user"], bound["fingerprint"], on_client=on_client,
                                   step="bundle", cancel=stop, on_event=h.board_events(handle, device),
                                   mode=h.framework_mode())
            try:
                run = service.run_on_board(open_session, built, "apply", store=store, device_id=device.device_id,
                                           say=lambda msg: handle.progress(None, None, clean(msg)[:200]))
            except (dev.DeviceRuntimeError, BundleError, RemoteError) as e:
                if stop.is_set() or handle.cancelled:
                    raise JobCancelled() from None
                raise h.job_error(e, users) from None
            return {"bundle_id": built.bundle_id, "result": run.report.result or "none",
                    "changed": run.report.changed, "ok": run.ok, "root_actions_waiting":
                        run.report.result == "stopped-root", EXTERNAL_KEY: [clean(line) for line in run.lines()]}

        def done(out: Dict[str, Any]):
            ok = bool(out.get("ok"))
            return ("ok" if ok else "error", f"bundle {out.get('bundle_id')}: {out.get('result')}, "
                                             f"{out.get('changed', 0)} steps changed", "" if ok else "failed")

        job = jobs.start("deploy", ctx.owner,
                         h.audited("bundle_deploy", inp, ctx, work, f"bundle {built.bundle_id} to device "
                                                                    f"{device.label}", done=done,
                                   cancelled=lambda: "cancelled; the SSH session was closed, the board's installer "
                                                     "continues at its step on the next deploy"),
                         on_cancel=cancel)
        return BundleJobOut(job_id=job.job_id, kind=job.kind, state=job.state)

    def device_name(value) -> Optional[str]:
        try:
            from orchestrator.runtime.device.trusted import TrustedDevices
            d = TrustedDevices.for_framework(framework).get(str(value))
            return d.label if d else None
        except Exception:  # noqa: BLE001
            return None

    return [
        ToolSpec("bundle_plan", "Plan a board bundle",
                 "Plans a board bundle for a trusted board from a preset or building blocks and their variables, and "
                 "lists the blocks: the plan as labels (components, variables, file roles and sizes, root actions, "
                 "memory, the checks against the board's probe, problems). Nothing is built or sent; the files are "
                 "the person's choice in the framework's Board bundle window",
                 ToolTier.READ, BundlePlanIn, BundlePlanOut, bundle_plan, idempotent=True, takes_context=True,
                 default_decision=Decision.DENY,
                 fields={"device_id": FieldMeta(kind="id", list_tool="list_devices", describe=device_name)},
                 examples=({"device_id": "0123456789abcdef", "preset": "stt"},),
                 summary=lambda o: f"Bundle plan: {'ok' if o.plan.get('ok') else 'not ready'}, "
                                   f"{len(o.plan.get('components', []))} components."),
        ToolSpec("bundle_deploy", "Deploy a board bundle",
                 "Sends a bundle the person built to its trusted board and installs its unprivileged steps with its "
                 "signed installer; root actions stop it (they need llmcf-install). The person confirms it in the "
                 "framework's own window, every time. A job.",
                 ToolTier.EXTERNAL, BundleDeployIn, BundleJobOut, bundle_deploy, destructive=True,
                 channels=("framework",), prepare=deploy_facts, takes_context=True, open_world=True,
                 default_decision=Decision.DENY, max_decision=Decision.ASK,
                 limits=ToolLimits(per_day=10, what="board bundle deploys"),
                 examples=({"bundle_id": "stt-01234567-20261101120000"},),
                 summary=lambda o: f"Board bundle job {fact(o.job_id)} is {fact(o.state)}."),
    ]


__all__ = ["BundleDeployIn", "BundleJobOut", "BundlePlanIn", "BundlePlanOut", "bundle_tool_specs"]
