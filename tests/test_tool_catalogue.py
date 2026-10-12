"""The registered tools against the design's catalogue, and the helpers the tool groups share.

The groups of phase T were built in parallel (builds and status, knowledge and Hugging Face, targets and modules).
These checks keep their merge honest: every registered tool carries a name from the catalogue (design section 2),
the core tools of the merged groups are listed by default, and one Hugging Face model is classified the same on the
build path and on the download path.
"""

import pytest

from orchestrator.Core import disk_plan, hf_hub, hf_lookup

# Design section 2.1: 37 core tools, 6 optional (default deny) and 2 later ones.
CORE = {
    "list_build_requests", "get_build_status", "read_build_log", "list_artifacts", "get_artifact_details",
    "check_build_request", "start_build", "cancel_build", "resolve_healing", "propose_healing_fix",
    "get_job_status", "cancel_job", "search_hf_models", "get_hf_model_info", "download_hf_file", "list_local_files",
    "search_knowledge", "list_knowledge_sources", "read_knowledge_source", "crawl_docs_site",
    "list_targets", "get_target_capabilities", "get_hardware_probe", "probe_local_hardware", "create_target_module",
    "list_devices", "create_deploy_package", "deploy_to_device",
    "list_packs", "add_pack_from_index", "export_pack", "start_recipe_build",
    "run_doctor", "get_status", "get_settings", "update_settings", "report_cannot_do",
}
OPTIONAL = {"delete_artifact", "restore_knowledge_snapshot", "build_local_runtime", "start_local_runtime",
            "stop_local_runtime", "run_benchmark"}
LATER = {"probe_remote_device", "run_device_benchmark"}
# Built by phase T so far (T1-T6, T8); T7 (crawl) and T9 (community) add the rest of the core set.
BUILT = CORE - {"crawl_docs_site", "list_packs", "add_pack_from_index", "export_pack", "start_recipe_build"}


def test_the_catalogue_has_the_designed_size():
    assert (len(CORE), len(OPTIONAL), len(LATER)) == (37, 6, 2)


def test_every_registered_tool_has_a_catalogue_name(booted_framework):
    names = [s.name for s in booted_framework.tool_registry.list()]
    assert len(names) == len(set(names)), "a tool is registered twice"
    assert set(names) <= CORE | OPTIONAL | LATER, sorted(set(names) - CORE - OPTIONAL - LATER)
    assert all(len(n) <= 39 for n in names), "names fit the clients' prefix limits (design 2.0)"


def test_the_built_core_tools_are_registered_and_listed_by_default(booted_framework):
    registry = booted_framework.tool_registry
    rows = {r["tool"]: r for r in registry.permissions("mcp")}
    assert BUILT <= set(rows), sorted(BUILT - set(rows))
    hidden = sorted(n for n in BUILT if not rows[n]["listed"])
    assert not hidden, f"core tools must be listed by default: {hidden}"


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
