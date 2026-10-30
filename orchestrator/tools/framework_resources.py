#!/usr/bin/env python3
"""
The framework's MCP resources (design 4.2): the same data the read tools return, under one `lcf://` scheme.

A resource is read through the tool registry (ToolRegistry.read_resource), never around it: the caller's permission
for the read tool it mirrors decides (a resource shows nothing the tool may not), the tool's output filter cleans the
result (secrets redacted, external text cleaned, capped and wrapped as untrusted data), and every read is audited as
the pseudo-tool `read_resource:<uri>`. Template parameters are ids; the SDK refuses `..`, absolute paths and NUL
bytes in them before anything here runs.

The installation documents are served from a fixed list only (their names, never a path), and only the ones that
exist are listed.
"""

from pathlib import Path
from typing import Dict, List, Optional

from orchestrator.tools.registry import ResourceSpec, ResourceUnknown, ToolError, ToolRegistry

# The documents an AI client may read, without the language suffix; each also exists as <name>_DE. The blueprint is
# three files (DESIGN, ROADMAP and the changelog in the repository root); BLUEPRINT is their short index.
DOC_NAMES = ("CLI_Usage", "Features", "Module_Developer_Guide", "DESIGN", "ROADMAP", "BLUEPRINT")
DOCS = tuple(name + suffix for name in DOC_NAMES for suffix in ("", "_DE"))
LOG_LINES = 200  # lines of a build log in its resource (the read_build_log default)

BUILD_STATUS_URI = "lcf://builds/{request_id}/status"
BUILD_LOG_URI = "lcf://builds/{request_id}/log"


def build_uris(request_id: str) -> List[str]:
    """The resource URIs of one build request (they get resource-updated events when the build changes)."""
    return [BUILD_STATUS_URI.format(request_id=request_id), BUILD_LOG_URI.format(request_id=request_id)]


def docs_folder(framework) -> Optional[Path]:
    """The installation's docs/ folder (not part of an installed package, so it may be missing)."""
    root = getattr(framework, "app_root", None)
    if not isinstance(root, (str, Path)):
        root = Path(__file__).resolve().parents[2]
    folder = Path(root) / "docs"
    return folder if folder.is_dir() else None


def doc_path(framework, name: str) -> Optional[Path]:
    """The file of a document from the fixed list, if this installation has it (a name, never a path)."""
    docs = docs_folder(framework)
    if docs is None or name not in DOCS:
        return None
    path = docs / f"{name}.md"
    return path if path.is_file() else None


def existing_docs(framework) -> List[str]:
    return [name for name in DOCS if doc_path(framework, name) is not None]


def register_framework_resources(registry: ToolRegistry, framework) -> ToolRegistry:
    def existing() -> List[Dict[str, str]]:
        return [{"name": name} for name in existing_docs(framework)]

    def read_doc(params: Dict[str, str]) -> str:
        name = params.get("name", "")
        path = doc_path(framework, name)
        if path is None:
            shown = ", ".join(existing_docs(framework)) or "none in this installation"
            raise ResourceUnknown(f"There is no document '{name}' to read.", next=f"Read one of: {shown}.")
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:  # the OS message names the file's real path, which never reaches the AI
            raise ToolError(f"The document '{name}' cannot be read in this installation.", code="failed",
                            next="Tell the person; run_mojo_oracle (or 'llmcf mojo' in a terminal) checks the "
                                 "installation.") from None

    service = getattr(framework, "build_service", None)

    def target_ids(_args, _ctx) -> List[str]:
        try:
            return list(service.target_choices()[0])
        except Exception:  # noqa: BLE001 - a completion is a hint
            return []

    def own_builds(_args, ctx) -> List[str]:
        """Completion offers only the caller's own build requests."""
        try:
            return [b.request_id for b in service.list_requests().builds if b.owner == ctx.actor]
        except Exception:  # noqa: BLE001
            return []

    builds = {"request_id": own_builds}
    specs = [
        ResourceSpec("lcf://docs/{name}", "docs", "Framework documentation",
                     "A document of this installation: CLI_Usage, Features, Module_Developer_Guide, DESIGN (how the "
                     "framework is built and why), ROADMAP (what comes) or BLUEPRINT (their index), each also in "
                     "German (_DE).", "text/markdown", read=read_doc, listed=existing,
                     completions={"name": lambda _args, _ctx: existing_docs(framework)}),
        ResourceSpec("lcf://targets", "targets", "Targets",
                     "The hardware targets with boards, formats and quantization types (as list_targets). External "
                     "text is wrapped as untrusted data.", "text/plain", tool="list_targets",
                     arguments=lambda params: {}),
        ResourceSpec("lcf://targets/{target}/capabilities", "target-capabilities", "Target capabilities",
                     "What a target can build (as get_target_capabilities).", "text/plain",
                     tool="get_target_capabilities", arguments=lambda params: {"target": params.get("target", "")},
                     completions={"target": target_ids}),
        ResourceSpec(BUILD_STATUS_URI, "build-status", "Build status",
                     "Status, progress, errors and a pending self-healing proposal of a build request (as "
                     "get_build_status, without waiting).", "text/plain", tool="get_build_status",
                     arguments=lambda params: {"request_id": params.get("request_id", ""), "wait_seconds": 0},
                     completions=builds),
        ResourceSpec(BUILD_LOG_URI, "build-log", "Build log",
                     f"The last {LOG_LINES} lines of a build request's log, secrets redacted, cleaned for the AI "
                     "(colour codes and redraws removed, repeated lines collapsed, failure lines kept; the person "
                     "sees the raw log), wrapped as untrusted data (as read_build_log).", "text/plain", tool="read_build_log",
                     arguments=lambda params: {"request_id": params.get("request_id", ""), "lines": LOG_LINES},
                     completions=builds),
        ResourceSpec("lcf://permissions", "permissions", "Tool permissions",
                     "This caller's tool permissions: every tool's decision and why, and the actions no tool does "
                     "(the permissions section of get_status).", "application/json", tool="get_status",
                     arguments=lambda params: {"sections": ["permissions"]}),
    ]
    if "get_artifact_details" in {s.name for s in registry.list()}:
        def artifact_ids(_args, _ctx) -> List[str]:
            try:
                return [a.artifact_id for a in service.list_artifacts().artifacts]
            except Exception:  # noqa: BLE001 - a completion is a hint
                return []

        artifact = {"artifact_id": artifact_ids}
        args = (lambda params: {"artifact_id": params.get("artifact_id", "")})
        specs += [
            ResourceSpec("lcf://artifacts/{artifact_id}/manifest", "artifact-manifest", "Built model manifest",
                         "The build manifest (build_info.json) of a built model with its files, every string value "
                         "marked as external text (as get_artifact_details).", "text/plain",
                         tool="get_artifact_details", arguments=args, completions=artifact),
            ResourceSpec("lcf://artifacts/{artifact_id}/model-card", "artifact-model-card", "Built model card",
                         "The model card of a built model, wrapped as untrusted data (as get_artifact_details, "
                         "whose model_card field holds it).", "text/plain", tool="get_artifact_details",
                         arguments=args, completions=artifact),
        ]
    specs += doc_source_resources(framework)
    for spec in specs:
        registry.register_resource(spec)
    return registry


def doc_source_resources(framework) -> List[ResourceSpec]:
    """The source catalogue's documentation links (configs/project_sources.yml), all of them or those that fit a
    stored probe's vendor and SDK (N10): read-only framework text; crawling stays crawl_docs_site's, confirmed. And
    its model suggestions (sections tiny_models and models, round 23 item 10)."""
    from orchestrator.Core.doc_sources import catalogue_text, links_for, load_catalogue

    def every(_params: Dict[str, str]) -> str:
        links = [link for vendor_links in load_catalogue().values() for link in vendor_links]
        return catalogue_text(links, "Documentation links of the source catalogue, by vendor and project:")

    def probes():
        from orchestrator.runtime.probe_service import HardwareProbes
        return HardwareProbes.for_framework(framework)

    def for_probe(params: Dict[str, str]) -> str:
        from orchestrator.runtime.probe_service import ProbeError
        try:
            profile = probes().get(params.get("probe_id", ""))
        except ProbeError as e:
            raise ResourceUnknown(str(e), next=getattr(e, "next", "")) from None
        return catalogue_text(links_for(profile), "Documentation links for this probe's vendor and SDK (from the "
                                                  "source catalogue):")

    def suggestions(_params: Dict[str, str]) -> str:
        from orchestrator.Core.doc_sources import model_suggestions, suggestions_text
        return suggestions_text(model_suggestions(), "Model suggestions of the source catalogue (no Hugging Face "
                                                     "token needed):")

    def probe_ids(_args, _ctx) -> List[str]:
        try:
            return [p.probe_id for p in probes().list()]
        except Exception:  # noqa: BLE001 - a completion is a hint
            return []

    return [
        ResourceSpec("lcf://knowledge/doc-sources", "doc-sources", "Documentation links",
                     "The vendors' SDK documentation links of the framework's source catalogue (read-only). Adding "
                     "one to the knowledge base is crawl_docs_site, which the person confirms.", "text/plain",
                     read=every),
        ResourceSpec("lcf://knowledge/doc-sources/{probe_id}", "doc-sources-probe", "Documentation links for a probe",
                     "The source catalogue's documentation links that fit a stored hardware probe's vendor and SDK "
                     "(read-only).", "text/plain", read=for_probe, completions={"probe_id": probe_ids}),
        ResourceSpec("lcf://models/suggestions", "model-suggestions", "Model suggestions",
                     "The model suggestions of the source catalogue (read-only): exact Hugging Face ids that need no "
                     "token, small ones first, with size, recommended quantization, licence and the day they were "
                     "checked. Any other id works with start_build as well.", "text/plain", read=suggestions),
    ]
