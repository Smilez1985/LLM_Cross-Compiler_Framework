"""The registered tools against the design's catalogue, and the helpers the tool groups share.

The groups of phase T were built in parallel (builds and status, knowledge and Hugging Face, targets and modules).
These checks keep their merge honest: every registered tool carries a name from the catalogue (design section 2),
the core tools of the merged groups are listed by default, and one Hugging Face model is classified the same on the
build path and on the download path.
"""

import pytest

from orchestrator.Core import disk_plan, hf_hub, hf_lookup

# Design section 2.1: 37 core tools, 6 optional (default deny) and 2 later ones; the trash (delegation design 3.10)
# adds two optional tools. Mojo (round 7 item 11') replaces run_doctor by run_mojo_oracle and adds run_mojo_miracle;
# run_doctor stays registered under its 3.0.0 name, callable but never listed (HIDDEN). index_framework_docs (N12)
# adds one core tool. The GUI tool `gui` (W4: one tool with an action field instead of the five GUI tools, never released, of
# wave 3) is the fortieth core tool, the same for Ditto and MCP clients, so a client lists exactly MAX_LISTED_TOOLS.
CORE = {
    "list_build_requests", "get_build_status", "read_build_log", "list_artifacts", "get_artifact_details",
    "check_build_request", "start_build", "cancel_build", "resolve_healing", "propose_healing_fix",
    "get_job_status", "cancel_job", "search_hf_models", "get_hf_model_info", "download_hf_file", "list_local_files",
    "search_knowledge", "list_knowledge_sources", "read_knowledge_source", "crawl_docs_site", "index_framework_docs",
    "list_targets", "get_target_capabilities", "get_hardware_probe", "probe_local_hardware", "create_target_module",
    "list_devices", "create_deploy_package", "deploy_to_device",
    "list_packs", "add_pack_from_index", "export_pack", "start_recipe_build",
    "run_mojo_oracle", "run_mojo_miracle", "get_status", "get_settings", "update_settings", "report_cannot_do",
    "gui",
}
HIDDEN = {"run_doctor"}
# deploy_device_runtime (T14): the board's runtime as `llm-cli device runtime deploy` does it (the design expected it
# inside the deploy package; the device runtime is deployed on its own).
OPTIONAL = {"delete_artifact", "restore_knowledge_snapshot", "build_local_runtime", "start_local_runtime",
            "stop_local_runtime", "run_benchmark", "deploy_device_runtime", "list_trash", "restore_from_trash"}
LATER = {"probe_remote_device", "run_device_benchmark"}
# The AI's hands in the open window (DECISIONS round 8, W4): one tool, the same for Ditto and every MCP caller.
GUI = {"gui"}
# The five GUI tools of wave 3, never released (A20): replaced by the one tool, never registered any more.
GUI_BEFORE = {"read_gui_form", "fill_gui_form", "wizard_state", "wizard_step", "show_gui"}
# Built by phase T (T1-T9, T14): every core tool.
BUILT = set(CORE)


def test_the_catalogue_has_the_designed_size():
    assert (len(CORE), len(OPTIONAL), len(LATER), len(HIDDEN)) == (40, 9, 2, 1)
    assert GUI <= CORE


def test_known_tools_is_exactly_the_pinned_catalogue():
    """framework_tools.KNOWN_TOOLS is the catalogue, one constant, kept equal to the sets pinned here."""
    from orchestrator.tools.framework_tools import KNOWN_TOOLS
    from orchestrator.tools.tools_gui import GUI_TOOLS
    assert KNOWN_TOOLS == CORE | OPTIONAL | LATER | HIDDEN
    assert GUI_TOOLS == GUI and not GUI_BEFORE & KNOWN_TOOLS


def test_the_gui_tool_fits_the_client_cap(booted_framework):
    """W4: MCP clients get the GUI tool too, so it is one tool with an action field: a client lists exactly
    MAX_LISTED_TOOLS tools, the GUI tool among them, and get_status warns about no caller."""
    from orchestrator.tools.framework_tools import MAX_LISTED_TOOLS
    from orchestrator.tools.registry import ToolContext
    from orchestrator.tools.tools_gui import register_gui_tools
    registry = booted_framework.tool_registry
    register_gui_tools(registry, booted_framework)   # the window opening registers nothing twice
    names = [s.name for s in registry.list()]
    assert names.count("gui") == 1 and not GUI_BEFORE & set(names)
    listed = {c: {r["tool"] for r in registry.permissions(c) if r["listed"]} for c in ("mcp", "ditto")}
    assert listed["mcp"] == BUILT and len(listed["mcp"]) == MAX_LISTED_TOOLS
    assert GUI <= {s.name for s in registry.visible("mcp")} and GUI <= {s.name for s in registry.visible("ditto")}
    for caller in ("mcp", "ditto"):
        status = registry.call("get_status", {"sections": ["permissions"]}, ToolContext(caller))
        assert status.permissions.warning == "", caller


def test_every_registered_tool_has_a_catalogue_name(booted_framework):
    names = [s.name for s in booted_framework.tool_registry.list()]
    assert len(names) == len(set(names)), "a tool is registered twice"
    assert set(names) <= CORE | OPTIONAL | LATER | HIDDEN, sorted(set(names) - CORE - OPTIONAL - LATER - HIDDEN)
    from orchestrator.tools.framework_tools import KNOWN_TOOLS
    assert set(names) <= KNOWN_TOOLS, "every registered tool is in the one catalogue"
    assert all(len(n) <= 39 for n in names), "names fit the clients' prefix limits (design 2.0)"


def test_the_built_core_tools_are_registered_and_listed_by_default(booted_framework):
    registry = booted_framework.tool_registry
    rows = {r["tool"]: r for r in registry.permissions("mcp")}
    assert BUILT <= set(rows), sorted(BUILT - set(rows))
    hidden = sorted(n for n in BUILT if not rows[n]["listed"])
    assert not hidden, f"core tools must be listed by default: {hidden}"


def test_only_the_core_tools_are_listed_by_default(booted_framework):
    """After the round-2 merges: every optional and later tool is registered but hidden (denied) until the person
    enables it, so a client lists exactly the built core tools, inside the 40-tool limit (design 2.1)."""
    rows = {r["tool"]: r for r in booted_framework.tool_registry.permissions("mcp")}
    assert (OPTIONAL | LATER | HIDDEN) <= set(rows), sorted((OPTIONAL | LATER | HIDDEN) - set(rows))
    listed = {n for n, r in rows.items() if r["listed"]}
    assert listed == BUILT, (sorted(listed - BUILT), sorted(BUILT - listed))
    assert len(listed) <= 40


@pytest.mark.parametrize("licence", ["apache-2.0", "MIT", "llama3.1", "llama-2-community", "gemma", "cc-by-nc-4.0",
                                     "gpl-3.0", "agpl-3.0", "deepseek", "qwen-research", "noncommercial", "other",
                                     "", None, ["mit", "apache-2.0"]])
def test_a_licence_is_classified_the_same_on_the_build_and_the_download_path(licence):
    shown, restrictive = hf_hub.classify_license(licence)
    expected = {"false": "permissive", "true": "restrictive"}.get(restrictive, "unknown")
    assert hf_lookup.license_class(licence) == (shown, expected)


@pytest.mark.parametrize("value", [False, None, True, "auto", "manual", "false", "MANUAL"])
def test_gating_is_read_the_same_on_both_paths(value):
    from orchestrator.Core.hf_lookup import gating as build_gating
    assert build_gating(value) == hf_hub.gating(value)


@pytest.mark.parametrize("total", [0, 50 * 1024 ** 3, 500 * 1024 ** 3, 8 * 1024 ** 4])
def test_downloads_and_builds_keep_the_same_reserve(total):
    assert hf_hub.reserve_bytes(total) == disk_plan.reserve_for(total)
    assert hf_hub.standing_reserve_bytes(total) == disk_plan.reserve_for(total, standing=True)


def test_the_build_path_uses_the_one_file_record():
    assert hf_lookup.HubFile is hf_hub.HubFile
