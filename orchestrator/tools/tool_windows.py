#!/usr/bin/env python3
"""
Where the person sees what an AI did (wave 6, design B.2; DECISIONS round 9, U1 and consequence b).

What Ditto or an AI app does through a tool shows up in the framework's own windows. This module is the one
vocabulary for that, without Qt, so the core (Core/activity.py, in every process) and the GUI (the activity router)
share it:

- WINDOW_IDS: the closed list of windows a row of the chat feed may name (FeedRefs.window). The gui tool's
  show_window ids (tools_gui.WINDOWS) are a part of it.
- TOOL_WINDOWS: for every tool that changes something (every tool that is not read-only), and for report_cannot_do,
  the window that shows its result. A registry-wide test (tests/test_activity.py, `activity_problems`) fails for a
  changing tool without an entry, so a new tool is never invisible. A central table instead of a ToolSpec field: no
  tool module has to change for it.
- JOB_KIND_WINDOWS: the window of every job kind (Core/job_service.JOB_KINDS).
- WINDOW_OPENERS: for every window, the objectName of what opens it - the menu action (the same names CLI_PATHS uses),
  a tab of the main window (its attribute name, as menus/deploy.py's TAB_OBJECTS), or the chat dock. PLANNED_OPENERS
  lists the objectNames whose QAction gets its name with the translated menus (wave 6, chain a0).

Nothing here runs anything: these are names. The GUI decides what a [Show] click opens, and only on the person's click.
"""

import re
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from orchestrator.Core.job_service import JOB_KINDS

WINDOW_IDS: Tuple[str, ...] = (
    "builder", "deployment", "build_history", "targets", "boards", "settings", "trash", "knowledge_sources",
    "knowledge_snapshots", "community_hub", "hf_browser", "ditto_runtime", "ditto_models", "mojo", "approvals",
    "proposals", "ai_settings", "tool_permissions", "module_wizard", "chat")
WINDOW_ID_RE = "^(?:" + "|".join(WINDOW_IDS) + ")$"

# A step of the next-step catalogue (orchestrator/utils/next_steps, chain a0) by its id only: "<area>.<name>", e.g.
# runtime.install or board.prune_on_device. The catalogue itself is not imported here (it is built in parallel).
STEP_ID_RE = r"^[a-z][a-z0-9_]{0,31}\.[a-z][a-z0-9_]{0,47}$"
MAX_STEPS = 4

# Tools that only read but whose call the person should see anyway: an AI that gave up says so (design B.1).
REPORTED_READ_TOOLS: FrozenSet[str] = frozenset({"report_cannot_do"})

TOOL_WINDOWS: Dict[str, str] = {
    # builds
    "start_build": "builder",
    "start_recipe_build": "builder",
    "cancel_build": "builder",
    "resolve_healing": "builder",            # the build goes on with the fix, or ends
    "propose_healing_fix": "proposals",      # Fix Proposals from AI Clients...: manual steps, never run
    "cancel_job": "chat",                    # refined by the job's kind (JOB_KIND_WINDOWS) when the job is known
    # models and knowledge
    "download_hf_file": "hf_browser",
    "crawl_docs_site": "knowledge_sources",
    "index_framework_docs": "knowledge_sources",
    "add_pack_from_index": "community_hub",
    "export_pack": "community_hub",
    "restore_knowledge_snapshot": "knowledge_snapshots",
    # the trash
    "delete_artifact": "trash",
    "restore_from_trash": "trash",
    # Ditto's local model program
    "build_local_runtime": "ditto_runtime",
    "start_local_runtime": "ditto_runtime",
    "stop_local_runtime": "ditto_runtime",
    "run_benchmark": "ditto_runtime",
    # boards and devices
    "probe_remote_device": "boards",
    "deploy_device_runtime": "boards",
    "run_device_benchmark": "boards",
    "create_deploy_package": "deployment",
    "deploy_to_device": "deployment",
    "probe_local_hardware": "builder",       # the Builder recommends a board from this computer's probe
    # targets, health, settings
    "create_target_module": "targets",
    "run_mojo_miracle": "mojo",
    "update_settings": "settings",
    # the AI's hands in the open window: refined by the form it filled (builder, module_wizard, ai_settings)
    "gui": "builder",
    # an AI that gave up: refined by its reason or the action it names (REASON_WINDOWS, ACTION_WINDOWS)
    "report_cannot_do": "chat",
}

# The window of each job kind, in the order of job_service.JOB_KINDS (crawl, deploy, package, probe, pack_import,
# pack_export, snapshot_restore, hf_download, runtime_build, runtime_start, benchmark, approval, docs_index). The kind
# names are not repeated here: the automation guard (tests/test_security.py) reads every string under
# orchestrator/tools, and the crawl kind's name must reach nothing but crawl_docs_site there. tests/test_activity.py
# pins the whole table by name.
_KIND_WINDOWS = ("knowledge_sources", "deployment", "deployment", "boards", "community_hub", "community_hub",
                 "knowledge_snapshots", "hf_browser", "ditto_runtime", "ditto_runtime", "ditto_runtime", "approvals",
                 "knowledge_sources")
JOB_KIND_WINDOWS: Dict[str, str] = dict(zip(JOB_KINDS, _KIND_WINDOWS))

# report_cannot_do: the window where the person can change what is missing.
REASON_WINDOWS: Dict[str, str] = {
    "permission_denied": "tool_permissions",
    "needs_person": "approvals",
    "hardware_unavailable": "boards",
}
ACTION_WINDOWS: Dict[str, str] = {
    "change_permissions": "tool_permissions",
    "trust_device": "boards",
    "deep_ingest_notice": "knowledge_sources",
    "install_pack_from_file": "community_hub",
    "publish_pull_request": "community_hub",
}

BUILDER_TAB, DEPLOYMENT_TAB = "builder_tab", "deployment_window"   # menus/deploy.py: the main window's attributes
CHAT_DOCK = "chatDock"                                               # gui/chat_panel/dock.py OBJECT_NAME

WINDOW_OPENERS: Dict[str, str] = {
    "builder": BUILDER_TAB,
    "deployment": DEPLOYMENT_TAB,
    "build_history": "act_build_history",
    "targets": "act_build_targets",
    "boards": "act_boards_devices",
    "settings": "act_file_settings",
    "trash": "act_file_trash",
    "knowledge_sources": "act_knowledge_sources",
    "knowledge_snapshots": "act_knowledge_snapshots",
    "community_hub": "act_knowledge_hub",
    "hf_browser": "act_tools_hf_browser",
    "ditto_runtime": "act_ditto_runtime_open",
    "ditto_models": "act_ditto_models_open",
    "mojo": "act_mojo_health",
    "approvals": "act_tools_approvals",
    "proposals": "act_tools_proposals",
    "ai_settings": "act_ditto_settings",
    "tool_permissions": "act_tools_permissions",
    "module_wizard": "act_tools_module_wizard",
    "chat": CHAT_DOCK,
}
# QActions that exist today without an objectName (Tools > Configure AI Agent (Ditto)..., Hugging Face Model
# Browser..., Fix Proposals from AI Clients...): they get these names where they are created (chain a0).
PLANNED_OPENERS: FrozenSet[str] = frozenset({"act_tools_hf_browser", "act_tools_proposals", "act_ditto_settings"})
TAB_OPENERS: FrozenSet[str] = frozenset({BUILDER_TAB, DEPLOYMENT_TAB})


def is_window(value: Any) -> bool:
    return isinstance(value, str) and value in WINDOW_IDS


def is_step_id(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(STEP_ID_RE, value) is not None


def window_for_tool(tool: str, *, form: str = "", window: str = "", reason: str = "",
                    action: str = "") -> Optional[str]:
    """The window that shows a call's result: the window or form the call itself named (the gui tool), the window
    of an AI's reason or named action (report_cannot_do), else the tool's own entry; None for a tool without one."""
    if tool == "gui":
        for named in (window, form):
            if is_window(named):
                return named
    if tool == "report_cannot_do":
        found = ACTION_WINDOWS.get(action) or REASON_WINDOWS.get(reason)
        if found:
            return found
    return TOOL_WINDOWS.get(tool)


def activity_problems(registry: Any) -> List[str]:
    """What keeps an AI's work from showing up in a window (like registry.deletion_rule_problems): a registered tool
    that is not read-only (or a reported read tool) without a TOOL_WINDOWS entry, an entry naming no known window, a
    job kind without a window, a window without an opener. An empty list when everything is covered."""
    from orchestrator.tools.policy import ToolTier
    problems = []
    for spec in registry.list():
        if spec.tier == ToolTier.READ and spec.name not in REPORTED_READ_TOOLS:
            continue
        window = TOOL_WINDOWS.get(spec.name)
        if window is None:
            problems.append(f"tool '{spec.name}' changes something but has no window in TOOL_WINDOWS")
        elif window not in WINDOW_IDS:
            problems.append(f"tool '{spec.name}' names the unknown window '{window}'")
    for kind in JOB_KINDS:
        if JOB_KIND_WINDOWS.get(kind) not in WINDOW_IDS:
            problems.append(f"job kind '{kind}' has no known window in JOB_KIND_WINDOWS")
    if len(_KIND_WINDOWS) != len(JOB_KINDS):
        problems.append("JOB_KIND_WINDOWS names a window for each job kind, in the order of JOB_KINDS")
    for table, name in ((TOOL_WINDOWS, "TOOL_WINDOWS"), (JOB_KIND_WINDOWS, "JOB_KIND_WINDOWS"),
                        (REASON_WINDOWS, "REASON_WINDOWS"), (ACTION_WINDOWS, "ACTION_WINDOWS")):
        problems += [f"{name}[{key!r}] names the unknown window '{value}'" for key, value in table.items()
                     if value not in WINDOW_IDS]
    problems += [f"window '{window}' has no opener in WINDOW_OPENERS" for window in WINDOW_IDS
                 if not WINDOW_OPENERS.get(window)]
    problems += [f"WINDOW_OPENERS names the unknown window '{window}'" for window in WINDOW_OPENERS
                 if window not in WINDOW_IDS]
    return problems


__all__ = ["WINDOW_IDS", "WINDOW_ID_RE", "STEP_ID_RE", "MAX_STEPS", "REPORTED_READ_TOOLS", "TOOL_WINDOWS",
           "JOB_KIND_WINDOWS", "REASON_WINDOWS", "ACTION_WINDOWS", "WINDOW_OPENERS", "PLANNED_OPENERS",
           "TAB_OPENERS", "BUILDER_TAB", "DEPLOYMENT_TAB", "CHAT_DOCK", "is_window", "is_step_id",
           "window_for_tool", "activity_problems"]
