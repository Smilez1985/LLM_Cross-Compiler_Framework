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

from orchestrator.tools.registry import ResourceSpec, ResourceUnknown, ToolRegistry

# The documents an AI client may read, without the language suffix; each also exists as <name>_DE.
DOC_NAMES = ("CLI_Usage", "Features", "Module_Developer_Guide", "BLUEPRINT")
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


def register_framework_resources(registry: ToolRegistry, framework) -> ToolRegistry:
    docs = docs_folder(framework)

    def doc_path(name: str) -> Optional[Path]:
        if docs is None or name not in DOCS:
            return None
        path = docs / f"{name}.md"
        return path if path.is_file() else None

    def existing_docs() -> List[Dict[str, str]]:
        return [{"name": name} for name in DOCS if doc_path(name) is not None]

    def read_doc(params: Dict[str, str]) -> str:
        name = params.get("name", "")
        path = doc_path(name)
        if path is None:
            shown = ", ".join(p["name"] for p in existing_docs()) or "none in this installation"
            raise ResourceUnknown(f"There is no document '{name}' to read.", next=f"Read one of: {shown}.")
        return path.read_text(encoding="utf-8", errors="replace")

    specs = [
        ResourceSpec("lcf://docs/{name}", "docs", "Framework documentation",
                     "A document of this installation: CLI_Usage, Features, Module_Developer_Guide or BLUEPRINT, "
                     "each also in German (_DE).", "text/markdown", read=read_doc, listed=existing_docs),
        ResourceSpec("lcf://targets", "targets", "Targets",
                     "The hardware targets with boards, formats and quantization types (as list_targets). External "
                     "text is wrapped as untrusted data.", "text/plain", tool="list_targets",
                     arguments=lambda params: {}),
        ResourceSpec("lcf://targets/{target}/capabilities", "target-capabilities", "Target capabilities",
                     "What a target can build (as get_target_capabilities).", "text/plain",
                     tool="get_target_capabilities", arguments=lambda params: {"target": params.get("target", "")}),
        ResourceSpec(BUILD_STATUS_URI, "build-status", "Build status",
                     "Status, progress, errors and a pending self-healing proposal of a build request (as "
                     "get_build_status, without waiting).", "text/plain", tool="get_build_status",
                     arguments=lambda params: {"request_id": params.get("request_id", ""), "wait_seconds": 0}),
        ResourceSpec(BUILD_LOG_URI, "build-log", "Build log",
                     f"The last {LOG_LINES} lines of a build request's log, secrets redacted, wrapped as untrusted "
                     "data (as read_build_log).", "text/plain", tool="read_build_log",
                     arguments=lambda params: {"request_id": params.get("request_id", ""), "lines": LOG_LINES}),
        ResourceSpec("lcf://permissions", "permissions", "Tool permissions",
                     "This caller's tool permissions: every tool's decision and why, and the actions no tool does "
                     "(the permissions section of get_status).", "application/json", tool="get_status",
                     arguments=lambda params: {"sections": ["permissions"]}),
    ]
    for spec in specs:
        registry.register_resource(spec)
    return registry
