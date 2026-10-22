#!/usr/bin/env python3
"""
Targets, hardware probes and new target modules (MCP design 2.6, work package T5), for every AI caller: Ditto in
process and MCP clients alike, through the one tool registry (policy, questions, limits, output filter, audit).

- list_targets / get_target_capabilities: what each target can build, with its trust (shipped, a signed pack's trust,
  local, ai-generated, own) and, on request, a check of its files. Texts from target.yml (names, descriptions, an AI's
  assumptions and reason for a base image) are external text.
- get_hardware_probe / probe_local_hardware: probes by id only (orchestrator/runtime/probe_service.py): "local" for
  this computer and 16 hex characters for a board's probe the person imported (`llm-cli scan --import FILE`, the GUI)
  or took over SSH. Probe values are external text; no tool reads a probe from a path.
- create_target_module: a board nobody here may have tested gets a target module from an AI's definition, through the
  module generator's validation (DECISIONS round 1 item 5, round 3). Two-phase: the question shows the base image
  pinned to the digest its tag points at now (any registry; a non-official one shown plainly; the image build runs
  with network), the platforms in its manifest, the vendor software with every address, fingerprint and hash, a
  warning for every soft limit gone above (DECISIONS round 4 item 4: approving overrides it), the values the AI
  guessed and that the module is untested; the module is then written with exactly the digest shown. It is always
  asked (max decision ask) and never covered by a standing approval. The module gets support level community,
  provenance ai-generated and the runtime: block planned from the board's probe; it never uses a prebuilt llama.cpp
  (runtime decision 6).
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from orchestrator.Core.module_generator import (ARCHITECTURES, DEFAULT_BASE, INPUT_LIST_BOUNDS, MODULE_NAME_RE,
                                                SHIPPED_TEMPLATE, ModuleGenerator, effective_base, generator_input,
                                                module_slug, soft_cap_warnings, target_name_collision, uses_gpu_template,
                                                validate_module_data, vendor_fact_lines)
# The audit row of a module the person saved lives in Core/module_generator.py (chain c1-imports); the old name stays.
from orchestrator.Core.module_generator import record_module_saved  # noqa: F401
from orchestrator.runtime.probe_service import (IMPORT_NEXT, LIST_LIMIT, LOCAL_ID, PROBE_TIMEOUT, HardwareProbes,
                                                ProbeError, match_target)
from orchestrator.schemas.build import TargetOut
from orchestrator.schemas.modules import PackageSourceIn, PinnedDownloadIn
from orchestrator.schemas.targets import TargetCapabilities, TargetIn
from orchestrator.tools.facts_catalog import warn
from orchestrator.tools.grants import tool_rule
from orchestrator.tools.output import ToolOutput, UntrustedText, count, fact, untrusted_text
from orchestrator.tools.policy import Decision, ToolTier
from orchestrator.tools.registry import ResourceSpec, ToolContext, ToolError, ToolRegistry, ToolSpec
from orchestrator.tools.spec import CallFacts, Fact, FieldMeta, GrantRule, ToolLimits

# Target folders the framework ships (in its own targets folder): trust "shipped".
SHIPPED_TARGET_FOLDERS = frozenset({"Rockchip"})
TRUST_VALUES = ("shipped", "verified", "community", "local", "revoked", "ai-generated", "own", "unknown")
PROBE_ID_PATTERN = r"^(local|[0-9a-f]{16})$"
AI_TEXT = "written by the AI"


# --- inputs and outputs -------------------------------------------------------------------------------------------

class TargetCapabilitiesOut(TargetCapabilities, ToolOutput):
    # The capabilities with the framework's notes, the base of TargetDetailsOut. First written in
    # tools/framework_tools.py, which keeps the name (resolved here on first use); a tool result, so it stays with the
    # tools, never in schemas/. A comment, not a docstring: a docstring would become part of the schema an AI reads.
    pass


class TargetCheckIn(TargetIn):
    # get_target_capabilities' input: the original TargetIn (schemas/targets.py) and whether to check the files. A
    # comment, not a docstring: a docstring would become the description of the input schema an AI reads.
    check_files: bool = Field(default=False, description="Also check the target's files (target.yml, Dockerfile "
                                                         "pins, scripts) the way a module or pack is checked")


class TargetSummary(TargetOut):
    trust: Literal[TRUST_VALUES] = "unknown"
    untested: bool = Field(default=False, description="Created by an AI for hardware nobody here has tested")


class TargetListOut(ToolOutput):
    targets: List[TargetSummary]


class TargetDetailsOut(TargetCapabilitiesOut):
    target: str = ""
    trust: Literal[TRUST_VALUES] = "unknown"
    untested: bool = False
    description: Optional[UntrustedText] = None
    assumptions: List[untrusted_text(300)] = Field(default_factory=list,
                                                   description="Values the AI guessed when it created the target")
    base_image: Optional[untrusted_text(300)] = None
    base_image_reason: Optional[untrusted_text(1000)] = None
    checked_files: bool = False
    problems: List[UntrustedText] = Field(default_factory=list, description="Problems found by check_files")


class ProbeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    probe_id: Optional[str] = Field(default=None, max_length=16, pattern=PROBE_ID_PATTERN,
                                    description="A probe id from the list ('local' for this computer); without it the "
                                                "newest probes are listed")


class MatchOut(BaseModel):
    target: str
    board: Optional[str] = None
    reason: untrusted_text(300)


class ProbeSummary(BaseModel):
    probe_id: str
    label: untrusted_text(64)
    source: Literal["local", "imported", "device"]
    updated: str
    os: Optional[untrusted_text(32)] = None
    arch: Optional[untrusted_text(32)] = None
    soc: Optional[untrusted_text(64)] = None
    board: Optional[untrusted_text(128)] = None
    needs_reprobe: bool = False
    matched_target: Optional[str] = None


class HardwareProbeOut(ToolOutput):
    probes: List[ProbeSummary] = Field(default_factory=list)
    probe: Optional[ProbeSummary] = None
    values: Dict[str, UntrustedText] = Field(default_factory=dict, description="The probe's validated values")
    matched_target: Optional[MatchOut] = None


class NoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class JobStartedOut(ToolOutput):
    job_id: str
    kind: str
    state: str


class CreateModuleIn(BaseModel):
    """A target module definition (the fields of Ditto's ModuleDefinition), validated again by the module
    generator."""
    model_config = ConfigDict(extra="forbid")

    module_name: str = Field(min_length=1, max_length=64, description="Short target name: letters, digits, space, "
                                                                      "'_', '.', '-'; e.g. Jetson Orin Nano")
    architecture: Literal[ARCHITECTURES] = Field(description="CPU architecture of the board")
    base_os: str = Field(min_length=1, max_length=300,
                         description="Docker base image as image:tag (a Docker Official Image such as "
                                     "debian:bookworm-slim is preferred; any registry is allowed, e.g. a vendor's "
                                     "toolchain image on nvcr.io, then give base_image_reason); never an invented "
                                     "digest: the framework pins the tag to its digest and shows it to the person")
    base_image_reason: str = Field(default="", max_length=1000,
                                   description="Why this base image; required when it is not a Docker Official Image")
    sdk: str = Field(default="none", max_length=64, description="Vendor toolkit the conversion needs, e.g. cuda, "
                                                                "rknn-toolkit2, none")
    probe_id: Optional[str] = Field(default=None, max_length=16, pattern=PROBE_ID_PATTERN,
                                    description="The board's probe (get_hardware_probe); the module's runtime block is "
                                                "planned from it")
    supported_boards: List[str] = Field(default_factory=list, max_length=20,
                                        description="Board names of the target; default: the module name")
    packages: List[str] = Field(default_factory=list, max_length=100, description="Debian/Ubuntu package names")
    package_sources: List[PackageSourceIn] = Field(default_factory=list,
                                                   max_length=INPUT_LIST_BOUNDS["package_sources"],
                                                   description="Vendor apt repositories with their signing key "
                                                               "(usually at most 3)")
    pip_packages: List[str] = Field(default_factory=list, max_length=INPUT_LIST_BOUNDS["pip_packages"],
                                    description="Vendor Python packages pinned as name==version (usually at most 30)")
    pip_index_url: str = Field(default="", max_length=300, description="One https package index for pip_packages; "
                                                                       "empty: PyPI")
    pinned_downloads: List[PinnedDownloadIn] = Field(default_factory=list,
                                                     max_length=INPUT_LIST_BOUNDS["pinned_downloads"],
                                                     description="Vendor files with https address, size and sha256 "
                                                                 "(usually at most 5); never llama.cpp")
    cpu_flags: str = Field(default="", max_length=500, description="Compiler flags such as -mcpu=cortex-a76 "
                                                                   "(advisory)")
    cmake_flags: str = Field(default="", max_length=500, description="llama.cpp CMake options (advisory)")
    setup_commands: str = Field(default="", max_length=2000,
                                description="Bash the source stage runs once before converting, as the "
                                            "unprivileged build user, without network (no sudo, no downloads); what "
                                            "it exports reaches the later stages")
    quantization_logic: str = Field(default="", max_length=3000,
                                    description="Body of the bash `case \"$QUANT_TYPE\" in` block of the convert "
                                                "stage (modules/convert_module.sh)")
    assumptions: List[str] = Field(default_factory=list, max_length=10,
                                   description="Values you guessed rather than read from the probe or the "
                                               "documentation, each with what to check first")

    @field_validator("module_name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = v.strip()
        if not MODULE_NAME_RE.fullmatch(v):
            raise ValueError("module_name: 1-64 letters, digits, spaces, '_', '.' or '-', starting with a letter or "
                             "digit")
        return v

    @field_validator("assumptions")
    @classmethod
    def _assumptions(cls, v: List[str]) -> List[str]:
        if any(len(a) > 300 for a in v):
            raise ValueError("each assumption has at most 300 characters")
        return [a.strip() for a in v if a.strip()]


class BaseImageOut(BaseModel):
    registry: str
    image: str
    tag: str = ""
    digest: str
    official: bool


class CreateModuleOut(ToolOutput):
    target: str
    folder: str = Field(description="The new target's folder, relative to the framework's targets folder")
    support_level: Literal["community"] = "community"
    ai_generated: bool = True
    untested: bool = True
    base_image: BaseImageOut
    boards: List[str]
    formats: List[str]
    runtime_from_probe: Optional[str] = Field(default=None, description="The probe the runtime block was planned "
                                                                        "from")


MODULE_EXAMPLE = {"module_name": "Orin Nano", "architecture": "aarch64", "base_os": "ubuntu:22.04", "sdk": "cuda",
                  "packages": ["cmake"], "assumptions": ["16 GB RAM: check the board's probe"]}


def definition_sha256(inp: CreateModuleIn) -> str:
    text = json.dumps(inp.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def probe_sha256(profile: Any) -> str:
    """The fingerprint of a stored probe's values: the module's runtime block is planned from exactly the probe the
    question named (a probe replaced while an approval waited is not used)."""
    return hashlib.sha256(profile.to_text().encode("utf-8", "surrogatepass")).hexdigest()


def target_marks(framework: Any, tid: str, index: Any = None) -> Dict[str, Any]:
    """trust, untested and the AI's texts of one target, from its folder and target.yml (also used by the deploy
    tools, whose question names the trust of the target a package was built for)."""
    out: Dict[str, Any] = {"trust": "unknown", "untested": False}
    try:
        tm = getattr(framework, "target_manager", None)
        folder, data = tm.folder_of(tid), tm.get_target(tid) or {}
    except Exception:  # noqa: BLE001 - a stand-in framework or a target that vanished
        return out
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    trust = None
    if folder is not None:
        from orchestrator.Core.community_manager import trust_of_folder
        try:
            level, _ = trust_of_folder(folder, index)
            trust = level.value if level is not None else None
        except Exception:  # noqa: BLE001
            trust = None
    if trust is None:
        if meta.get("provenance") == "ai-generated":
            trust = "ai-generated"
        elif folder is not None and folder.name in SHIPPED_TARGET_FOLDERS and \
                folder.resolve().parent == SHIPPED_TEMPLATE.parent.resolve():
            trust = "shipped"
        else:
            trust = "own"
    out["trust"] = trust if trust in TRUST_VALUES else "unknown"
    out["untested"] = bool(meta.get("untested")) or meta.get("provenance") == "ai-generated"
    out["meta"] = meta
    out["folder"] = folder
    return out


# --- registration -------------------------------------------------------------------------------------------------

def register_target_tools(registry: ToolRegistry, framework: Any, *, service: Any, jobs: Any,
                          target_field: FieldMeta) -> ToolRegistry:
    """Registers the target, probe and module tools and the probe resource."""

    def targets_manager():
        tm = getattr(framework, "target_manager", None)
        if tm is None:
            raise ToolError("The framework is not initialized (no target registry).", code="failed",
                            next="Tell the person the framework is not ready; run_mojo_oracle (or 'llmcf mojo' in a "
                                 "terminal) shows why.")
        return tm

    def probes() -> HardwareProbes:
        return HardwareProbes.for_framework(framework)

    def offline() -> bool:
        get = getattr(getattr(framework, "config", None), "get", None)
        try:
            return bool(get("offline_mode", False)) if callable(get) else False
        except Exception:  # noqa: BLE001
            return False

    def cache_index():
        try:
            from orchestrator.Core.community_manager import CommunityManager
            state = getattr(framework.config, "user_state_dir", None)
            return CommunityManager.read_cached_index(Path(state) / "community", revocations=True) if state else None
        except Exception:  # noqa: BLE001 - no index: packs show the trust recorded at install
            return None

    def marks(tid: str, index=None) -> Dict[str, Any]:
        return target_marks(framework, tid, index)

    # --- targets ---

    def list_targets(_inp: NoInput) -> TargetListOut:
        base = service.list_targets()
        index = cache_index()
        rows = []
        for t in base.targets:
            m = marks(t.id, index)
            rows.append(TargetSummary(**t.model_dump(), trust=m["trust"], untested=m["untested"]))
        return TargetListOut(targets=rows)

    def get_target_capabilities(inp: TargetCheckIn, ctx: ToolContext) -> TargetDetailsOut:
        caps = service.capabilities(inp.target)
        tid = service.resolve_target(inp.target)
        m = marks(tid, cache_index())
        meta = m.get("meta") or {}
        assumptions = meta.get("assumptions") if isinstance(meta.get("assumptions"), list) else []
        out = TargetDetailsOut(**caps.model_dump(), target=tid, trust=m["trust"], untested=m["untested"],
                               description=str(meta["description"]) if meta.get("description") else None,
                               assumptions=[str(a) for a in assumptions[:10]],
                               base_image=str(meta["base_image"]) if meta.get("base_image") else None,
                               base_image_reason=(str(meta["base_image_reason"]) if meta.get("base_image_reason")
                                                  else None))
        if inp.check_files:
            folder = m.get("folder")
            if folder is None:
                raise ToolError(f"The folder of target {tid} cannot be found.", code="failed",
                                next="Call list_targets; run_mojo_oracle (or 'llmcf mojo' in a terminal) checks the "
                                     "targets folder.")
            from orchestrator.Core.module_generator import validate_target_folder
            problems = validate_target_folder(Path(folder))
            out.checked_files, out.problems = True, [p.replace(str(folder), tid) for p in problems[:50]]
            if len(problems) > 50:
                ctx.notes.append(f"{len(problems) - 50} more problems were found; fix these first.")
        if m["untested"]:
            ctx.notes.append("This target was created by an AI and is untested on real hardware; its texts are AI "
                             "text.")
        return out

    # --- probes ---

    def summary_of(info, matched: Optional[str]) -> ProbeSummary:
        return ProbeSummary(probe_id=info.probe_id, label=info.label, source=info.source, updated=info.updated,
                            os=info.os, arch=info.arch, soc=info.soc, board=info.board,
                            needs_reprobe=info.needs_reprobe, matched_target=matched)

    def get_hardware_probe(inp: ProbeIn, ctx: ToolContext) -> HardwareProbeOut:
        store = probes()
        tm = getattr(framework, "target_manager", None)
        try:
            if inp.probe_id is None:
                rows = []
                for info in store.list(LIST_LIMIT):
                    found = match_target(store.get(info.probe_id), tm)
                    rows.append(summary_of(info, found.target if found else None))
                if not rows:
                    ctx.notes.append("No probe is stored yet. " + IMPORT_NEXT)
                return HardwareProbeOut(probes=rows)
            profile = store.get(inp.probe_id)
        except ProbeError as e:
            raise ToolError(str(e), code=e.code, next=e.next) from None
        info = store.info(inp.probe_id, profile)
        found = match_target(profile, tm)
        if profile.needs_reprobe:
            ctx.notes.append("This probe lacks facts (converted from an older probe file); values not in it are "
                             "unknown, not absent.")
        if found is None:
            ctx.notes.append("No target matches this probe: create_target_module can add one (the add_board prompt "
                             "guides through it).")
        return HardwareProbeOut(probe=summary_of(info, found.target if found else None),
                                values=dict(profile.values),
                                matched_target=MatchOut(target=found.target, board=found.board, reason=found.reason)
                                if found else None)

    def running_probes(ctx: ToolContext) -> int:
        return sum(1 for j in jobs.list(ctx.owner) if j.kind == "probe" and not j.finished)

    def probe_local_hardware(_inp: NoInput, ctx: ToolContext) -> JobStartedOut:
        store = probes()
        tm = getattr(framework, "target_manager", None)

        def work(handle):
            handle.progress(None, None, "probing this computer")
            try:
                profile, how = store.run_local(timeout=PROBE_TIMEOUT)
            except ProbeError as e:
                raise ValueError(f"{e} Next: {e.next}" if e.next else str(e)) from None
            found = match_target(profile, tm)
            return {"probe_id": LOCAL_ID, "how": how, "os": profile.os, "arch": profile.arch,
                    "soc": profile.get("SOC_MODEL"), "matched_target": found.target if found else None,
                    "next": "Read the values with get_hardware_probe(probe_id='local')."}

        job = jobs.start("probe", ctx.owner, work)
        ctx.notes.append("Follow the probe with get_job_status (wait_seconds=50); it takes at most "
                         f"{int(PROBE_TIMEOUT)} seconds.")
        return JobStartedOut(job_id=job.job_id, kind=job.kind, state=job.state)

    # --- create_target_module ---

    def targets_dir() -> Path:
        return Path(targets_manager().targets_dir)

    def refuse(message: str, next: str, code: str = "invalid_value", field: str = "") -> ToolError:
        return ToolError(message, code=code, next=next, field=field)

    def check_definition(inp: CreateModuleIn) -> Dict[str, Any]:
        """The generator's input for the definition, checked as far as it can be without the registry: name, shell
        fields, every value the generator checks, the base image's form, its reason, and that every address is
        public. Refused before the question with the field and the fix."""
        from orchestrator.Core.ditto_manager import PREBUILT_CUDA_IMAGE, PREBUILT_CUDA_REFUSAL, dangerous_command
        from orchestrator.utils.image_ref import is_official_image
        from orchestrator.utils.prebuilt_llama import is_prebuilt_llama_image
        from orchestrator.utils.validation import validate_public_url
        data = generator_input(inp.model_dump(mode="json"), inp.module_name, inp.supported_boards or None)
        taken = target_name_collision(targets_dir(), module_slug(inp.module_name), inp.module_name)
        if taken:
            raise refuse(f"A target named '{taken}' exists, and '{inp.module_name}' reads the same (names are compared "
                         "ignoring case and separators).", "Choose another module_name; list_targets shows the names "
                         "in use.", field="module_name")
        danger = dangerous_command(inp.setup_commands, inp.quantization_logic)
        if danger:
            raise refuse(f"The shell code is not allowed ({danger}).", "Remove it from setup_commands or "
                         "quantization_logic.", field="setup_commands")
        base = inp.base_os.strip()
        if PREBUILT_CUDA_IMAGE.search(base):
            raise refuse(PREBUILT_CUDA_REFUSAL, "Use ubuntu:22.04 (or another base) with sdk cuda.", field="base_os")
        if is_prebuilt_llama_image(base):
            raise refuse(f"'{base}' is a prebuilt llama.cpp image; targets build llama.cpp from source with the "
                         "shared recipe.", "Use an OS or toolchain image as base_os.", field="base_os")
        try:  # every other value, with a stand-in base (the real one is pinned and checked after the lookup)
            validate_module_data({**data, "base_os": DEFAULT_BASE})
        except ValueError as e:
            raise refuse(str(e), "Fix the fields listed and call create_target_module again.") from None
        if not is_official_image(base) and not inp.base_image_reason.strip():
            raise refuse(f"'{base}' is not a Docker Official Image, so base_image_reason is required.",
                         "Say why this image (for example: the vendor ships its toolchain only as this image), or use "
                         "an official image such as debian:bookworm-slim with the vendor's package_sources.",
                         field="base_image_reason")
        urls = [s.url for s in inp.package_sources] + [s.signed_by_url for s in inp.package_sources]
        urls += [d.url for d in inp.pinned_downloads] + ([inp.pip_index_url] if inp.pip_index_url else [])
        for url in urls:
            if not validate_public_url(url).valid:
                raise refuse(f"The address {url[:200]} is not a public https address.", "Use the vendor's public "
                             "https address.", field="package_sources")
        return data

    def image_facts(data: Dict[str, Any]):
        from orchestrator.utils.image_registry import ImageLookupError, inspect_image
        base = effective_base(data, SHIPPED_TEMPLATE)
        try:
            return inspect_image(base, offline=offline())
        except ImageLookupError as e:
            raise ToolError(str(e), code=e.code, next=e.next, field="base_os") from None

    def module_facts(inp: CreateModuleIn, ctx: ToolContext) -> CallFacts:
        data = check_definition(inp)
        image = image_facts(data)
        pinned = image.pinned
        if uses_gpu_template(data) and str(data.get("base_os")) == DEFAULT_BASE:
            pinned_base = DEFAULT_BASE  # the GPU template keeps its own pinned base; the module names the default
        else:
            pinned_base = pinned
        try:
            validate_module_data({**data, "base_os": pinned_base})
        except ValueError as e:
            raise refuse(str(e), "Fix base_os and call create_target_module again.", field="base_os") from None
        label, probe_sha = None, ""
        if inp.probe_id:
            try:
                store = probes()
                profile = store.get(inp.probe_id)
                label, probe_sha = store.info(inp.probe_id, profile).label, probe_sha256(profile)
            except ProbeError as e:
                raise ToolError(str(e), code=e.code, next=e.next, field="probe_id") from None
        warnings = soft_cap_warnings(data)
        slug = module_slug(inp.module_name)
        consequence = (f"Creates the target module '{inp.module_name}' (folder targets/{slug}) from an AI's definition: "
                       "untested on real hardware, support level community, marked ai-generated. Its build image "
                       f"starts from {pinned} and is built with network access on this computer the first time the "
                       "target is built.")
        if warnings:
            consequence += " WARNING: the module goes above a soft limit (below); approving overrides it."
        registry_line = ("Docker Official Image" if image.official else
                         f"other registry: {image.registry}; prebuilt image made by its publisher, not checked by the "
                         "framework")
        platforms = ", ".join(image.platforms) if image.platforms else f"unknown ({image.note or 'not looked up'})"
        match = {True: "one matches this computer", False: "no matching platform for this computer (the build would "
                 "need emulation, if it works at all)", None: "a match with this computer is unknown"}
        facts = [Fact("Base image", pinned),
                 Fact("Registry", registry_line),
                 Fact("Digest", image.digest + (f" (resolved now from the tag {image.tag or 'latest'})"
                                                if image.resolved_now else "")),
                 Fact("Image build", "runs with network access on this computer, outside the build sandbox"),
                 Fact("Platforms", f"{platforms}; this computer: {image.host}; {match[image.platform_match]}")]
        if inp.base_image_reason.strip():
            facts.append(Fact("Why this image", inp.base_image_reason.strip(), AI_TEXT))
        for line in vendor_fact_lines(data):
            if line.startswith("WARNING: "):
                facts.append(Fact("Soft limit", line[len("WARNING: "):]))
            else:
                facts.append(Fact("Vendor software", line, AI_TEXT))
        for a in data.get("assumptions") or []:
            facts.append(Fact("Guessed by the AI", a, AI_TEXT))
        facts.append(Fact("Runtime block", f"planned from probe {inp.probe_id} ({label})" if inp.probe_id else
                          "no probe given: the device runtime is planned when the board is probed"))
        facts.append(Fact("Untested", "Nobody here may have run this module on the board; builds of it warn that it "
                                      "is untested and AI-generated."))
        bound = {"base_os": pinned_base, "image": pinned, "definition": definition_sha256(inp),
                 "probe_id": inp.probe_id or "", "probe_sha256": probe_sha}
        return CallFacts(consequence=consequence, facts=tuple(facts), bound=bound, cap=Decision.ASK.value,
                         warnings=module_warnings(data, image), consequence_template=consequence,
                         names={"target": inp.module_name})

    def create_target_module(inp: CreateModuleIn, ctx: ToolContext) -> CreateModuleOut:
        bound = ctx.facts.bound if ctx.facts is not None else {}
        if not bound.get("base_os") or bound.get("definition") != definition_sha256(inp):
            raise ToolError("The module is not bound to a definition the person was shown; nothing was written.",
                            code="failed", next="Call create_target_module again through the framework's registry.")
        data = generator_input(inp.model_dump(mode="json"), inp.module_name, inp.supported_boards or None)
        data["base_os"] = bound["base_os"]  # exactly the digest the question showed (principle 11)
        profile = None
        if inp.probe_id:
            try:
                profile = probes().get(inp.probe_id)
            except ProbeError as e:
                raise ToolError(str(e), code=e.code, next=e.next, field="probe_id") from None
            if probe_sha256(profile) != bound.get("probe_sha256"):
                raise ToolError(f"The probe {inp.probe_id} changed after the person was asked; nothing was written.",
                                code="failed", field="probe_id",
                                next="Call create_target_module again, so the person sees the current probe.")
        root = targets_dir()
        try:
            folder = ModuleGenerator(root).generate_module(data, None, profile=profile)
        except ValueError as e:
            raise ToolError(str(e).replace(str(root.resolve()), "targets").replace(str(root), "targets"),
                            code="invalid_value", next="Fix the fields listed and call create_target_module again; "
                            "nothing was written.") from None
        from orchestrator.Core.confirmed_builds import unattended
        if unattended(ctx.confirm_channel):
            # made without the person answering this question (a delegation): its first build is the person's to see
            # once, bound to exactly this definition ([Q1]; build_plan.confirmation_state)
            mark_created_under(folder, str(ctx.confirm_channel), bound["definition"])
        tm = targets_manager()
        try:
            tm.initialize()
        except Exception:  # noqa: BLE001 - the folder exists; the next start loads it
            ctx.notes.append("The target registry could not be reloaded; the new target appears after a restart.")
        tid = tm.resolve(folder.name) or folder.name
        caps = tm.get_capabilities(tid)
        if soft_cap_warnings(data):
            ctx.notes.append("The person approved going above a soft limit for this module.")
        ctx.notes.append("The module is untested: tell the user what to check first (its assumptions).")
        from orchestrator.utils.image_ref import split_image
        image = split_image(bound["image"])
        return CreateModuleOut(target=tid, folder=folder.name, base_image=BaseImageOut(
            registry=image["registry"], image=image["repository"], tag=image["tag"], digest=image["digest"],
            official=bool(image["official"])), boards=list(caps.boards) if caps else [],
            formats=list(caps.formats) if caps else [], runtime_from_probe=inp.probe_id)

    def probe_name(value) -> Optional[str]:
        try:
            store = probes()
            info = store.info(str(value))
            return f"{info.label} ({info.source})"
        except Exception:  # noqa: BLE001 - a speaking name is a convenience
            return None

    probe_field = {"probe_id": FieldMeta(kind="id", list_tool="get_hardware_probe", describe=probe_name)}
    module_fields = {
        **probe_field,
        "module_name": FieldMeta(kind="trust"),
        "architecture": FieldMeta(kind="choice", choices=tuple(ARCHITECTURES)),
        "base_os": FieldMeta(kind="trust"),
        "packages": FieldMeta(split=True),
        "supported_boards": FieldMeta(split=True),
        "pip_index_url": FieldMeta(kind="url"),
        "setup_commands": FieldMeta(kind="trust"),
        "quantization_logic": FieldMeta(kind="trust"),
    }
    specs = [
        ToolSpec("list_targets", "List targets",
                 "Lists the hardware targets with their boards, output formats, quantization types and trust "
                 "(shipped, verified or community pack, local, ai-generated, own).",
                 ToolTier.READ, NoInput, TargetListOut, list_targets, idempotent=True,
                 summary=lambda o: count(len(o.targets), "target", "targets") + "."),
        ToolSpec("get_target_capabilities", "Target capabilities",
                 "Shows what a target can build: boards, formats, quantizations per board, calibration needs, its "
                 "trust and, for an AI-created target, its assumptions. check_files also checks its files.",
                 ToolTier.READ, TargetCheckIn, TargetDetailsOut, get_target_capabilities, idempotent=True,
                 takes_context=True, fields={"target": target_field},
                 examples=({"target": "Rockchip"}, {"target": "Rockchip", "check_files": True}),
                 summary=lambda o: (f"Capabilities of {fact(o.target)}: {count(len(o.boards), 'board', 'boards')}, "
                                    f"{count(len(o.formats), 'format', 'formats')}"
                                    + (f", {count(len(o.problems), 'problem', 'problems')} in its files"
                                       if o.checked_files else "") + ".")),
        ToolSpec("get_hardware_probe", "Hardware probes",
                 "Lists the stored hardware probes (this computer's and imported boards') or shows one probe's values "
                 "and the target that matches it. Probe values are external text.",
                 ToolTier.READ, ProbeIn, HardwareProbeOut, get_hardware_probe, idempotent=True, takes_context=True,
                 fields=probe_field, examples=({}, {"probe_id": "local"}),
                 summary=lambda o: (f"Probe {fact(o.probe.probe_id)}: {count(len(o.values), 'value', 'values')}."
                                    if o.probe else count(len(o.probes), "stored probe", "stored probes") + ".")),
        ToolSpec("probe_local_hardware", "Probe this computer",
                 "Runs the hardware probe on this computer (CPU, memory, GPUs, NPUs, OS, toolchain; no host name) "
                 "and stores it as probe 'local'. Returns a job; follow it with get_job_status.",
                 ToolTier.LOCAL_WRITE, NoInput, JobStartedOut, probe_local_hardware, idempotent=True,
                 takes_context=True, max_decision=Decision.ALLOW,
                 limits=ToolLimits(concurrent=1, running=running_probes, per_day=30, what="probes"),
                 grant=GrantRule(rule=tool_rule("probe_local_hardware"), facts=lambda i, c: {},
                                 warning="probe this computer's hardware and store the result"),
                 consequence=f"Runs the hardware probe on this computer for at most {int(PROBE_TIMEOUT)} seconds: it "
                             "reads system information, starts tools such as nvidia-smi or vulkaninfo, and stores "
                             "the result as probe 'local' (no host name).",
                 summary=lambda o: f"Probe job {fact(o.job_id)} is {fact(o.state)}."),
        ToolSpec("create_target_module", "Create a target module",
                 "Adds a board, also one nobody here can test: creates a target module from your definition through "
                 "the module generator. Base image: prefer a Docker Official Image with the vendor's package_sources; "
                 "any other image needs base_image_reason. Write the image as image:tag (the framework pins its "
                 "digest). List guessed values in assumptions. The person confirms every module; it is created as "
                 "untested (community, ai-generated).",
                 ToolTier.LOCAL_WRITE, CreateModuleIn, CreateModuleOut, create_target_module, takes_context=True,
                 open_world=True, max_decision=Decision.ASK, fields=module_fields, examples=(MODULE_EXAMPLE,),
                 prepare=module_facts,
                 summary=lambda o: f"Target {fact(o.target)} created (untested, community)."),
    ]
    for spec in specs:
        registry.register(spec)

    def probe_ids(_args, _ctx) -> List[str]:
        try:
            return [p.probe_id for p in probes().list(LIST_LIMIT)]
        except Exception:  # noqa: BLE001
            return []

    registry.register_resource(ResourceSpec(
        "lcf://probes/{probe_id}", "probe", "Hardware probe",
        "A stored hardware probe's values and its matching target (as get_hardware_probe); probe values are external "
        "text.", "text/plain", tool="get_hardware_probe",
        arguments=lambda params: {"probe_id": params.get("probe_id", "")}, completions={"probe_id": probe_ids}))
    return registry


def module_warnings(data: Dict[str, Any], image: Any) -> tuple:
    """The warnings before a new module (design 4.3): untested on the board (situation); a base image outside the
    Docker Official Images or not pinned by digest, and every vendor list above its soft cap (security: only the
    person overrides a soft cap)."""
    from orchestrator.Core.build_plan import _registry_word
    from orchestrator.Core.confirmed_builds import pinned
    from orchestrator.Core.module_generator import SOFT_CAPS
    found = [warn("untested_module")]
    reference = str(getattr(image, "pinned", "") or "")
    if not getattr(image, "official", False):
        found.append(warn("base_image_unofficial", registry=_registry_word(reference or str(data.get("base_os")))))
    if not pinned(reference):
        found.append(warn("base_image_unpinned"))
    for key, cap in SOFT_CAPS.items():
        count_ = len(data.get(key) or [])
        if count_ > cap:
            found.append(warn("vendor_soft_cap_exceeded", what=key, count=count_, cap=cap))
    return tuple(found)


def mark_created_under(folder: Path, channel: str, definition: str) -> None:
    """Records in a new module's target.yml that it was created without the person answering the question itself
    (`created_under`: the standing approval or delegation) and its definition's sha256, so its first build asks the
    person once (first_build_unconfirmed) and a module recreated under the same name asks again."""
    import yaml
    path = Path(folder) / "target.yml"
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    config.setdefault("metadata", {}).update({"created_under": channel[:80], "definition_sha256": definition})
    with open(path, "w", encoding="utf-8", newline="\n") as f:  # LF like every file of the module
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)


