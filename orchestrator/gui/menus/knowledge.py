#!/usr/bin/env python3
"""
The knowledge entries of the main window's Tools menu (W1' of 2026-10-16: every action of `llmcf knowledge` and
`llmcf zoo` also by mouse).

Tools > Knowledge Sources... (act_knowledge_sources, new; right below Knowledge Snapshots...) opens the list of every
source of the knowledge base with its kind, licence, copyright notice and what a knowledge pack would carry of it,
filtered by kind (orchestrator/gui/knowledge_sources_window.py). The entries that exist already get their object names
here: Knowledge Snapshots... (act_knowledge_snapshots: the snapshots with the disk line, the storage and its buttons),
Index the Framework's Documentation... (act_knowledge_index_docs) and Community Hub (act_knowledge_hub: the zoo with
Details..., Import file..., Check installed packs, Check a pack file..., Export... and My public keys...). The windows
load when their entry is first used (lazy loading, RG1).

CLI_PATHS names, for each `llmcf` command path of this area (and each of its options), the object that does it by
mouse; WINDOW_OBJECTS names, for an object that lives in a window, the menu action that opens that window;
MACHINE_ONLY lists the switches of this area that are for scripts only, with the reason; ARGUMENT_PATHS names the
mouse path of a command whose argument changes what it does (`zoo verify PACK_FILE`).
"""

from typing import Dict

from orchestrator.gui.menus import place
from orchestrator.utils.localization import ui_text

TOOLS_MENU_NAMES = (place.MENU_TOOLS,)
TOOLS_MENU_TITLES = ("Tools",)
ACT_SNAPSHOTS = "act_knowledge_snapshots"
ACT_SOURCES = "act_knowledge_sources"
ACT_HUB = "act_knowledge_hub"
ACT_INDEX_DOCS = "act_knowledge_index_docs"
ACTIONS = (ACT_SNAPSHOTS, ACT_SOURCES, ACT_HUB, ACT_INDEX_DOCS)

CLI_PATHS: Dict[str, str] = {
    # the knowledge base
    "knowledge": ACT_SOURCES,                        # the group lists its commands; its windows are named below
    "knowledge snapshots": ACT_SNAPSHOTS,
    "knowledge snapshots list": ACT_SNAPSHOTS,       # the table, the disk line (next snapshot fits?) and the note
    "knowledge snapshots delete": "btn_knowledge_snapshots_delete",
    "knowledge snapshots prune": "btn_knowledge_snapshots_delete",   # nothing ticked: the person ticks any of them
    "knowledge snapshots export": "btn_knowledge_snapshots_export",
    "knowledge snapshots export --to": "btn_knowledge_snapshots_export",
    "knowledge storage": ACT_SNAPSHOTS,              # the storage line, in every state
    "knowledge storage show": ACT_SNAPSHOTS,
    "knowledge storage convert": "btn_knowledge_storage_convert",
    "knowledge storage delete-old": "btn_knowledge_storage_delete_old",
    "knowledge index-docs": ACT_INDEX_DOCS,
    "knowledge index-docs --with-code": ACT_INDEX_DOCS,   # the question's check box
    "knowledge sources": ACT_SOURCES,
    "knowledge sources --kind": "cmb_knowledge_sources_kind",
    # the community zoo
    "zoo": ACT_HUB,
    "zoo list": ACT_HUB,
    "zoo list --kind": "cmb_zoo_kind",
    "zoo list --refresh": "btn_zoo_refresh",
    "zoo show": "btn_zoo_details",                   # also a double click on a row
    "zoo show --kind": "btn_zoo_details",            # the selected row names its kind
    "zoo install": "tbl_zoo_packs",                  # the row's Install button, after the summary ("No" default)
    "zoo install --kind": "tbl_zoo_packs",
    "zoo install --file": "btn_zoo_import",
    "zoo install --from": "btn_zoo_import",          # Only accept these senders: key files
    "zoo install --passphrase": "btn_zoo_import",    # the passphrase field (never stored or shown)
    "zoo verify": "btn_zoo_verify_installed",
    "zoo export": "btn_zoo_export",                  # a target, a finished build (recipe) or the knowledge base
    "zoo export --author": "btn_zoo_export",
    "zoo export --license": "btn_zoo_export",
    "zoo export --model-license": "btn_zoo_export",
    "zoo export --id": "btn_zoo_export",
    "zoo export --to": "btn_zoo_export",             # Encrypt: to the keys in key files
    "zoo export --passphrase": "btn_zoo_export",     # Encrypt: with a passphrase
    "zoo export --out": "btn_zoo_export",            # Folder: Choose folder...
    "zoo export --pr": "btn_zoo_export",             # the export window's Open pull request, after the summary
    "zoo keys": "btn_zoo_keys",                      # with Copy and Save...
}
# Commands whose argument makes them another action, and the object that does that one.
ARGUMENT_PATHS: Dict[str, str] = {
    "zoo verify PACK_FILE": "btn_zoo_verify_file",
}
# Objects that live in a window, and the menu action that opens it.
WINDOW_OBJECTS: Dict[str, str] = {
    "btn_knowledge_snapshots_delete": ACT_SNAPSHOTS,
    "btn_knowledge_snapshots_export": ACT_SNAPSHOTS,
    "btn_knowledge_snapshots_open": ACT_SNAPSHOTS,
    "btn_knowledge_storage_convert": ACT_SNAPSHOTS,
    "btn_knowledge_storage_delete_old": ACT_SNAPSHOTS,
    "lbl_knowledge_snapshots_disk": ACT_SNAPSHOTS,
    "lbl_knowledge_storage": ACT_SNAPSHOTS,
    "cmb_knowledge_sources_kind": ACT_SOURCES,
    "btn_knowledge_sources_refresh": ACT_SOURCES,
    "tbl_knowledge_sources": ACT_SOURCES,
    "cmb_zoo_kind": ACT_HUB,
    "btn_zoo_refresh": ACT_HUB,
    "btn_zoo_details": ACT_HUB,
    "tbl_zoo_packs": ACT_HUB,
    "btn_zoo_import": ACT_HUB,
    "btn_zoo_verify_installed": ACT_HUB,
    "btn_zoo_verify_file": ACT_HUB,
    "btn_zoo_export": ACT_HUB,
    "btn_zoo_keys": ACT_HUB,
}
MACHINE_ONLY: Dict[str, str] = {
    "zoo list --json": "machine-readable output; the Community Hub shows the same rows",
    "zoo install --yes": "script switch: the Community Hub shows the summary and asks every time (\"No\" default)",
    "zoo export --yes": "script switch: in the export window the person clicks Open pull request after the summary",
}


def install(window, menubar) -> None:
    """Names the knowledge entries of the Tools menu (act_knowledge_snapshots, act_knowledge_index_docs,
    act_knowledge_hub) and adds Knowledge Sources... (window.act_knowledge_sources) right below Knowledge
    Snapshots...; a Tools menu that does not exist yet is made."""
    tools = place.menu_or_new(menubar, place.MENU_TOOLS, "Tools", TOOLS_MENU_TITLES)
    for attribute, name in (("act_snapshots", ACT_SNAPSHOTS), ("act_framework_docs", ACT_INDEX_DOCS)):
        existing = getattr(window, attribute, None)
        if existing is not None:
            existing.setObjectName(name)
    i18n = getattr(window, "i18n", None)
    hub_text = i18n.t("menu.open_hub", "Community Hub") if i18n is not None else "Community Hub"
    hub = place.entry(tools, hub_text)
    if hub is not None:
        hub.setObjectName(ACT_HUB)
        window.act_knowledge_hub = hub
    action = place.new_action(window, ACT_SOURCES, ui_text("knowledge.menu.sources"),
                              lambda: open_sources_window(window))
    place.insert_after(tools, getattr(window, "act_snapshots", None), action)
    window.act_knowledge_sources = action


def open_sources_window(window):
    """Shows the Knowledge Sources window of the main window (fresh data each time it opens)."""
    from orchestrator.gui.knowledge_sources_window import open_knowledge_sources
    return open_knowledge_sources(window)


__all__ = ["ACTIONS", "ACT_HUB", "ACT_INDEX_DOCS", "ACT_SNAPSHOTS", "ACT_SOURCES", "ARGUMENT_PATHS", "CLI_PATHS",
           "MACHINE_ONLY", "WINDOW_OBJECTS", "install", "open_sources_window"]
