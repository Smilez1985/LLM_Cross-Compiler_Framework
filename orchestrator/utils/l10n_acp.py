#!/usr/bin/env python3
"""
Texts of File > Settings... > Ditto in editors (Ditto as an ACP agent, `llm-cli acp`; orchestrator/gui/acp_settings.py),
English and German. LocalizationManager merges them (localization.TEXT_MODULES); a key that exists already is refused
there. No text tells the person to type a command: the entries come with Copy buttons. This module imports nothing
from the framework.
"""

TEXTS = {
    "en": {
        "acp.tab": "Ditto in editors",
        "acp.intro": "Editors such as Zed, JetBrains IDEs, Android Studio, Neovim or Emacs can use Ditto as their AI "
                     "agent (Agent Client Protocol). The editor starts the framework itself with the entry below and "
                     "talks to Ditto; every tool call runs within your tool permissions and is recorded in the audit "
                     "log. Ditto is experimental, in the editor too.",
        "acp.command.label": "Command (for an editor that asks for the agent's command)",
        "acp.json.label": "Entry for the editor's agent settings (Zed: settings.json, JetBrains IDEs: acp.json)",
        "acp.copy": "Copy",
        "acp.copied": "Copied.",
        "acp.entry.problem": "The entry cannot be shown: {reason}",
        "acp.servers.title": "MCP servers editors passed to Ditto",
        "acp.servers.note": "An editor may hand Ditto its own MCP servers. They are recorded here and none of them is "
                            "used: their tools would bypass the framework's permissions and audit log. Enabling one "
                            "is your consent for a later version that connects to it (its results then count as "
                            "untrusted data and pass the privacy filter).",
        "acp.servers.empty": "No editor has passed an MCP server to Ditto yet.",
        "acp.servers.col.enabled": "Enabled",
        "acp.servers.col.name": "Server",
        "acp.servers.col.what": "What would run",
        "acp.servers.col.editor": "Editor (self-reported)",
        "acp.servers.col.seen": "Last seen",
        "acp.servers.refresh": "Refresh",
        "acp.servers.saved": "{name}: {state}. This version connects to no editor MCP server either way.",
        "acp.servers.state.on": "enabled",
        "acp.servers.state.off": "disabled",
        "acp.servers.failed": "The choice could not be saved: {reason}",
    },
    "de": {
        "acp.tab": "Ditto in Editoren",
        "acp.intro": "Editoren wie Zed, JetBrains-IDEs, Android Studio, Neovim oder Emacs können Ditto als KI-Agent "
                     "nutzen (Agent Client Protocol). Der Editor startet das Framework selbst mit dem Eintrag unten und "
                     "spricht mit Ditto; jeder Tool-Aufruf läuft innerhalb der Tool-Berechtigungen und steht im "
                     "Audit-Log. Ditto ist experimentell, auch im Editor.",
        "acp.command.label": "Befehl (für einen Editor, der nach dem Befehl des Agents fragt)",
        "acp.json.label": "Eintrag für die Agent-Einstellungen des Editors (Zed: settings.json, JetBrains-IDEs: "
                          "acp.json)",
        "acp.copy": "Kopieren",
        "acp.copied": "Kopiert.",
        "acp.entry.problem": "Der Eintrag kann nicht gezeigt werden: {reason}",
        "acp.servers.title": "MCP-Server, die Editoren an Ditto übergeben haben",
        "acp.servers.note": "Ein Editor kann Ditto eigene MCP-Server übergeben. Sie stehen hier, und keiner davon "
                            "wird benutzt: Ihre Tools würden an den Berechtigungen und am Audit-Log des Frameworks "
                            "vorbeilaufen. Ein Häkchen ist die Zustimmung für eine spätere Version, die sich damit "
                            "verbindet (die Ergebnisse gelten dann als nicht vertrauenswürdige Daten und laufen durch "
                            "den Datenschutzfilter).",
        "acp.servers.empty": "Noch kein Editor hat Ditto einen MCP-Server übergeben.",
        "acp.servers.col.enabled": "Zugestimmt",
        "acp.servers.col.name": "Server",
        "acp.servers.col.what": "Was laufen würde",
        "acp.servers.col.editor": "Editor (eigene Angabe)",
        "acp.servers.col.seen": "Zuletzt gesehen",
        "acp.servers.refresh": "Aktualisieren",
        "acp.servers.saved": "{name}: {state}. Diese Version verbindet sich so oder so mit keinem MCP-Server eines "
                             "Editors.",
        "acp.servers.state.on": "zugestimmt",
        "acp.servers.state.off": "nicht zugestimmt",
        "acp.servers.failed": "Die Wahl konnte nicht gespeichert werden: {reason}",
    },
}

# No German text here addresses the person with du, so no Sie form is needed.
FORMAL = {"de": {}}
