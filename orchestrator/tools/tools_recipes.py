#!/usr/bin/env python3
"""
Building an installed community recipe (MCP design 2.8, work package T9): `start_recipe_build`, and the optional
`lcf://packs/index` resource.

A recipe pins a Hugging Face model at a commit, a target, format, board, quantization, importance matrix and context
length. `start_recipe_build` maps an installed recipe to exactly that build request (BuildService.recipe_request) and
takes start_build's normal two-phase path: the same plan with the commit, download size, disk rule, hardware estimate,
licence, gating, mounts, target trust and image build in the question, the same disk reservation, the same standing
approval scope (grants.BUILD_RULE). A recipe pack installed under a standing approval (`installed_via: grant:<id>`) is
covered by a build standing approval only after the person confirmed one build of it (confirmed_builds). A calibration
dataset is taken only when the recipe names one, and must be exactly the file it pins (sha256).

Recipe values (model, target, ids) come from a community pack: the question labels them as pack text.
"""

import dataclasses
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.tools.output import fact
from orchestrator.tools.registry import ToolError

RECIPE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$"
RECIPE_TEXT = "from the recipe pack"


class RecipeBuildIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    recipe_id: str = Field(pattern=RECIPE_ID_PATTERN,
                           description="The id of an installed recipe pack, exactly as list_packs shows it "
                                       "(kind recipe, installed_only true)")
    dataset_path: Optional[str] = Field(default=None, max_length=1024,
                                        description="Only when the recipe names a calibration dataset: that file as "
                                                    "<label>:<relative path> (e.g. datasets:calib/de.txt) or its file "
                                                    "id; never a path")
    use_gpu: bool = Field(default=False, description="Use the GPU of this computer for the conversion")
    probe: Optional[str] = Field(default=None, pattern=r"^(local|[0-9a-f]{16})$",
                                 description="The board's hardware probe by its id (get_hardware_probe), as for "
                                             "start_build: the build is tuned to it (a recipe pins none). Omitted: "
                                             "the newest stored probe of a board of the recipe's SoC, if there is one")


def recipe_tool_specs(registry, service, fields: Dict[str, Any], limits, handle_out) -> List[Any]:
    """The ToolSpec of start_recipe_build (registered by framework_tools.register_framework_tools)."""
    from orchestrator.schemas.build import StartBuildIn
    from orchestrator.tools.grants import BUILD_RULE
    from orchestrator.tools.policy import ToolTier
    from orchestrator.tools.registry import ToolSpec
    from orchestrator.tools.spec import CallFacts, Fact, FieldMeta, GrantRule
    from orchestrator.tools.tools_builds import problem_error, start_accepted

    def refusal(e: Exception) -> ToolError:
        return ToolError(str(e), code=getattr(e, "code", "failed") or "failed", next=getattr(e, "next", "") or "",
                         field=getattr(e, "field", "") or "")

    def mapped(inp: RecipeBuildIn):
        from orchestrator.Core.build_service import BuildServiceError
        try:
            request, recipe, prov = service.recipe_request(inp.recipe_id, inp.dataset_path, inp.use_gpu)
        except BuildServiceError as e:
            raise refusal(e) from None
        if inp.probe:   # checked by the plan like start_build's (another board's probe is refused)
            request = request.model_copy(update={"probe": inp.probe})
        return request, recipe, prov

    def prepare_recipe(inp: RecipeBuildIn, ctx) -> CallFacts:
        from orchestrator.Core.build_service import BuildServiceError
        request, recipe, prov = mapped(inp)
        record = {"id": prov.id, "installed_via": str(getattr(prov, "installed_via", "") or ""),
                  "pack_sha256": str(getattr(prov, "pack_sha256", "") or "")}
        plan = service.plan(StartBuildIn(**request.model_dump()), recipe=record)
        if plan.problems:
            raise problem_error(plan.problems)
        if recipe.options.dataset is not None:
            try:
                validated, _ = service._validated(request, False)
                service.check_recipe_dataset(recipe, validated.dataset_path)
            except BuildServiceError as e:
                raise refusal(e) from None
        base = plan.call_facts()
        facts = [Fact("Recipe", f"{recipe.id} by {recipe.author} (trust {prov.trust}"
                      + (", installed under a standing approval" if record["installed_via"] else "") + ")",
                      RECIPE_TEXT),
                 Fact("Recipe options", f"importance matrix {'yes' if recipe.options.use_imatrix else 'no'}, context "
                      f"length {recipe.options.max_context_length or 'the target default'}")]
        target_note = target_match(recipe)
        if target_note:
            facts.append(Fact("Target files", target_note))
        if recipe.options.dataset is not None:
            facts.append(Fact("Calibration dataset", f"{recipe.options.dataset.name}, sha256 "
                                                     f"{recipe.options.dataset.sha256} (checked)", RECIPE_TEXT))
        bound = dict(base.bound)
        bound["recipe_id"] = prov.id
        bound["recipe_sha256"] = service.recipe_digest(recipe)  # every field, checked again at commit
        # The build plan's warnings, numbers and digest keys stay (first_build_unconfirmed covers the recipe pack too);
        # the whole recipe is part of what a decision is bound to.
        return dataclasses.replace(
            base, consequence=f"Builds the community recipe {recipe.id}: " + base.consequence,
            consequence_template=f"Builds the community recipe {recipe.id}: " + base.consequence_template,
            facts=tuple(facts) + tuple(base.facts), bound=bound,
            digest_keys=tuple(base.digest_keys) + ("recipe_id", "recipe_sha256"),
            names={**dict(base.names), "recipe": recipe.id}, tool="")

    def target_match(recipe) -> str:
        """Whether the installed target's files are the ones the recipe was built with (a different result is
        possible otherwise; never a refusal)."""
        try:
            from orchestrator.Core.community_pack import tree_sha256
            tm = getattr(service.framework, "target_manager", None)
            folder = tm.folder_of(recipe.target.id) if tm is not None and hasattr(tm, "folder_of") else None
            if folder is None:
                return ""
            if tree_sha256(folder) == recipe.target.pack_sha256:
                return "the installed target is exactly the one the recipe was made with"
        except Exception:  # noqa: BLE001 - a hint only
            return ""
        return ("the installed target's files differ from the ones the recipe was made with; the result may differ "
                "from the recipe's artifact hashes")

    def start_recipe_build(inp: RecipeBuildIn, ctx):
        bound = ctx.facts.bound if ctx.facts is not None else {}
        if "download" not in bound or not bound.get("recipe_id"):
            raise ToolError("The build is not bound to the facts the person was shown; nothing was started.",
                            code="failed", next="Call start_recipe_build again through the framework's tool registry.")
        from orchestrator.Core.build_service import BuildServiceError
        expect = {"model": bound.get("model"), "revision": bound.get("revision"), "target": bound.get("target"),
                  "format": bound.get("format"), "board": bound.get("board"),
                  "quantization": bound.get("quantization"), "recipe_sha256": bound.get("recipe_sha256")}

        def start(reservation):
            try:
                return service.start_recipe(bound["recipe_id"], dataset_path=inp.dataset_path, use_gpu=inp.use_gpu,
                                            owner=ctx.owner, disk_reservation=reservation, expect=expect,
                                            probe=inp.probe)
            except BuildServiceError as e:
                raise refusal(e) from None
        return start_accepted(service, bound, ctx, start)

    def build_grant_facts(inp, ctx):
        bound = getattr(getattr(ctx, "facts", None), "bound", None) or {}
        grant = bound.get("grant")
        return dict(grant) if isinstance(grant, dict) else None

    return [ToolSpec(
        "start_recipe_build", "Build a community recipe",
        "Builds an installed community recipe exactly as it is pinned: its Hugging Face model at the recipe's "
        "commit, target, format, board, quantization, importance matrix and context length. Same question and checks "
        "as start_build (download size, disk, hardware estimate, licence, gating, image build). dataset_path only when "
        "the recipe names a calibration dataset; probe names the board's hardware probe (get_hardware_probe) as for "
        'start_build. Returns a request id. Example: {"recipe_id": '
        '"qwen2.5-0.5b-instruct-rk3588-gguf-q4_k_m"}',
        ToolTier.LOCAL_WRITE, RecipeBuildIn, handle_out, start_recipe_build, takes_context=True, open_world=True,
        prepare=prepare_recipe, limits=limits,
        fields={"recipe_id": FieldMeta(kind="id", list_tool="list_packs"), "dataset_path": fields["dataset_path"],
                "probe": FieldMeta(kind="id", list_tool="get_hardware_probe")},
        examples=({"recipe_id": "qwen2.5-0.5b-instruct-rk3588-gguf-q4_k_m"},),
        grant=GrantRule(rule=BUILD_RULE, facts=build_grant_facts,
                        warning="build community recipes on shipped or verified targets (safetensors or GGUF "
                                "weights of ungated Hugging Face models with a permissive licence; no image from "
                                "outside the Docker Official Images unless you confirmed a build with that pinned "
                                "image; up to the size class you chose)"),
        summary=lambda o: f"Recipe build request {fact(o.request_id)} is {fact(o.status)}.",
        consequence="Converts and quantizes a community recipe's model in a Docker build on this computer; it "
                    "downloads the model first and may take a long time.")]


def register_packs_index_resource(registry) -> None:
    """The optional `lcf://packs/index` resource (design 4.2): the cached signed index and the installed packs, as
    list_packs without a refresh; community text stays marked as untrusted."""
    from orchestrator.tools.registry import ResourceSpec
    if "list_packs" not in {s.name for s in registry.list()}:
        return
    registry.register_resource(ResourceSpec(
        "lcf://packs/index", "packs-index", "Community pack index",
        "The cached signed community index and the installed packs (the first page of list_packs, never "
        "refreshed); titles, authors and descriptions are external text.", "text/plain", tool="list_packs",
        arguments=lambda params: {"page": 1}))


__all__ = ["RecipeBuildIn", "recipe_tool_specs", "register_packs_index_resource"]
