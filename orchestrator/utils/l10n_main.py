#!/usr/bin/env python3
"""
Texts of the main window's menu bar and tabs (wave 6, chain a0; DECISIONS round 10 Q2): the menu titles with their
mnemonics (Alt+letter), the entries the main window creates itself, the submenus of the regrouped menus (Ditto >
Advanced, Tools > Knowledge, Tools > Maintenance) and the tab titles, in English and German.

The menu structure itself (which entry sits in which menu, by objectName) is one table, MENU_LAYOUT of
orchestrator/utils/next_steps/common.py; the mouse paths of the next steps are built from it and from these labels,
so a path a text names (``{path:act_tools_permissions}``) always matches the menu the person sees. German uses du;
technical terms stay English (Q7). This module imports nothing from the framework.
"""

TEXTS = {
    "en": {
        # the menu titles (an "&" marks the Alt shortcut letter)
        "main.menu.file": "&File",
        "main.menu.build": "&Build",
        "main.menu.boards": "B&oards",
        "main.menu.ditto": "&Ditto",
        "main.menu.ditto_advanced": "&Advanced",
        "main.menu.security": "&Security",
        "main.menu.view": "&View",
        "main.menu.tools": "&Tools",
        "main.menu.tools_knowledge": "&Knowledge",
        "main.menu.tools_maintenance": "&Maintenance",
        "main.menu.help": "&Help",
        # File
        "main.file.trash": "Trash...",
        "main.file.output_folder": "Output Folder...",
        "main.file.exit": "Exit",
        # Ditto: the AI administration, "Set up Ditto..." first (Q2)
        "main.ditto.setup": "Set up Ditto...",
        "main.ditto.settings": "AI Settings...",
        "main.ditto.permissions": "Tool Permissions...",
        "main.ditto.folders": "Folders the AI May Use...",
        "main.ditto.approvals": "Pending Approvals...",
        "main.ditto.proposals": "Fix Proposals from AI Apps...",
        # Security
        "main.security.openai": "Set OpenAI API Key...",
        # Tools
        "main.tools.module_wizard": "Create Hardware Module...",
        "main.tools.hf_browser": "Hugging Face Model Browser...",
        "main.tools.snapshots": "Knowledge Snapshots...",
        "main.tools.images": "Framework Build Images...",
        "main.tools.index_docs": "Index the Framework's Documentation...",
        "main.tools.hub": "Open Community Hub",
        "main.tools.audio": "Spoken Announcements",
        "main.tools.clear_history": "Clear Input History...",
        # Help
        "main.help.about": "About...",
        # the tabs (the emoji stays out of these texts: the tab shows it in front, a screen reader reads the text)
        "main.tab.builder": "Builder",
        "main.tab.deployment": "Deployment",
        "main.tab.tuning": "Auto-Tuning",
        "main.tab.models": "Models",
        # labels of the Builder's scan button and its first entries, and of other windows' buttons that steps name
        "main.builder.scan": "Scan / Import...",
        "main.builder.scan.import": "Import from File (USB/Disk)...",
        "main.builder.scan.remote": "Remote Scan via SSH...",
        "main.builder.scan.local": "Scan Local Host",
        "main.hub.import": "Import file...",
        "main.hub.install": "Install (in the pack's row)",
        "main.deploy.run": "Deploy to Target",
        # the next steps under a message (orchestrator/gui/next_step_widgets.py)
        "main.steps.open": "Open",
        "main.steps.open.name": "Open: {step}",
        "main.steps.copy": "Copy",
        "main.steps.copy.name": "Copy the command: {step}",
        "main.steps.page": "Open page",
        "main.steps.page.name": "Open the page in the browser: {step}",
        "main.folders.add": "Add folder...",
    },
    "de": {
        "main.menu.file": "&Datei",
        "main.menu.build": "&Build",
        "main.menu.boards": "B&oards",
        "main.menu.ditto": "&Ditto",
        "main.menu.ditto_advanced": "&Erweitert",
        "main.menu.security": "&Sicherheit",
        "main.menu.view": "&Ansicht",
        "main.menu.tools": "E&xtras",
        "main.menu.tools_knowledge": "&Wissen",
        "main.menu.tools_maintenance": "&Wartung",
        "main.menu.help": "&Hilfe",
        "main.file.trash": "Papierkorb...",
        "main.file.output_folder": "Ausgabeordner...",
        "main.file.exit": "Beenden",
        "main.ditto.setup": "Ditto einrichten...",
        "main.ditto.settings": "KI-Einstellungen...",
        "main.ditto.permissions": "Tool-Berechtigungen...",
        "main.ditto.folders": "Ordner, die die KI nutzen darf...",
        "main.ditto.approvals": "Offene Freigaben...",
        "main.ditto.proposals": "Korrekturvorschläge von KI-Apps...",
        "main.security.openai": "OpenAI-API-Key festlegen...",
        "main.tools.module_wizard": "Hardware-Modul erstellen...",
        "main.tools.hf_browser": "Hugging-Face-Modellbrowser...",
        "main.tools.snapshots": "Wissens-Snapshots...",
        "main.tools.images": "Build-Images des Frameworks...",
        "main.tools.index_docs": "Dokumentation des Frameworks indexieren...",
        "main.tools.hub": "Community Hub öffnen",
        "main.tools.audio": "Gesprochene Ansagen",
        "main.tools.clear_history": "Eingabeverlauf löschen...",
        "main.help.about": "Über...",
        "main.tab.builder": "Builder",
        "main.tab.deployment": "Deployment",
        "main.tab.tuning": "Auto-Tuning",
        "main.tab.models": "Modelle",
        "main.builder.scan": "Scan / Import...",
        "main.builder.scan.import": "Aus Datei importieren (USB/Laufwerk)...",
        "main.builder.scan.remote": "Board per SSH scannen...",
        "main.builder.scan.local": "Lokalen Rechner scannen",
        "main.hub.import": "Datei importieren...",
        "main.hub.install": "Installieren (in der Zeile des Pakets)",
        "main.deploy.run": "Auf das Target deployen",
        "main.steps.open": "Öffnen",
        "main.steps.open.name": "Öffnen: {step}",
        "main.steps.copy": "Kopieren",
        "main.steps.copy.name": "Den Befehl kopieren: {step}",
        "main.steps.page": "Seite öffnen",
        "main.steps.page.name": "Die Seite im Browser öffnen: {step}",
        "main.folders.add": "Ordner hinzufügen...",
    },
}
