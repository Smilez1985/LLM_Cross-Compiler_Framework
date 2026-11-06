#!/usr/bin/env python3
"""
Texts of the Agent Client Protocol (ACP) in both directions, English and German, the same {placeholders} in both:

- Ditto as an ACP agent (`llm-cli acp`): File > Settings... > Ditto in editors (orchestrator/gui/acp_settings.py).
  No text tells the person to type a command: the entries come with Copy buttons.
- The ACP agents the person picks (Core/acp_hosts.py): the window Agents... (add from the ACP registry or an own
  command, sign in, store a key, remove), the chat panel's agent picker, the agent's live answer, its permission
  questions and its forms. A text that quotes an agent says it is the agent's own words.

LocalizationManager merges them (localization.TEXT_MODULES); a key that exists already is refused there. This module
imports nothing from the framework.
"""

TEXTS = {
    "en": {
        # Ditto as an ACP agent (File > Settings... > Ditto in editors)
        "acp.tab": "Ditto in editors",
        "acp.intro": "Editors such as Zed, JetBrains IDEs, Android Studio, Neovim or Emacs can use Ditto as their AI "
                     "agent (Agent Client Protocol). The editor starts the framework itself with the entry below and "
                     "talks to Ditto; every tool call runs within your tool permissions and is recorded in the audit "
                     "log. Ditto is experimental, in the editor too.",
        "acp.command.label": "Command (for an editor that asks for the agent's command)",
        "acp.json.label": "Entry for a JetBrains IDE's agent settings (acp.json, agent_servers)",
        "acp.zed.label": "Entry for Zed's settings.json (agent_servers, a custom agent)",
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
        # The ACP agents the person picks (Ditto > Agents..., the chat panel)
        "acp.menu.agents": "Agents...",
        "acp.window.title": "Agents",
        "acp.window.intro": "External coding agents you pick (ACP). Each one works with this framework's tools only: "
                            "every action goes through your tool permissions, the approvals and the audit log. Ditto "
                            "stays the chat's default. An agent you start runs as its own program with your rights; "
                            "it gets only the basics of this computer's environment and its own keys.",
        "acp.col.name": "Name",
        "acp.col.kind": "Distribution",
        "acp.col.version": "Version",
        "acp.col.from": "From",
        "acp.col.internet": "Internet",
        "acp.col.keys": "Keys",
        "acp.col.licence": "Licence",
        "acp.col.description": "Description",
        "acp.internet.needed": "needed",
        "acp.internet.local": "local",
        "acp.from.registry": "ACP registry",
        "acp.from.command": "your command",
        "acp.yours": "Your agents",
        "acp.registry.title": "ACP registry",
        "acp.registry.refresh": "Fetch the registry",
        "acp.registry.search": "Search:",
        "acp.registry.never": "The ACP registry was not fetched yet. It is fetched only when you click the button.",
        "acp.registry.count": "{n} agents in the registry (text from the registry).",
        "acp.kind.label": "Distribution:",
        "acp.kind.auto": "the first that works here",
        "acp.add.registry": "Add the selected agent...",
        "acp.own.title": "Your own command",
        "acp.own.name": "Name:",
        "acp.own.command": "Program:",
        "acp.own.browse": "Browse...",
        "acp.own.args": "Arguments:",
        "acp.own.local": "Works without the internet (it may start in offline mode)",
        "acp.own.keys": "Variables of its keys (comma separated):",
        "acp.add.command": "Add your command...",
        "acp.preview.title": "Add {name}?",
        "acp.preview.intro": "The text below comes from the ACP registry or from you. The agent runs with exactly this "
                             "command when you start it.",
        "acp.preview.line": "{label}: {value}",
        "acp.preview.description": "Description",
        "acp.preview.kind": "Distribution",
        "acp.preview.licence": "Licence",
        "acp.preview.command": "Command",
        "acp.preview.download": "Download",
        "acp.preview.sha256": "Checksum (sha256)",
        "acp.preview.internet": "Internet",
        "acp.preview.internet.needed": "needed (refused in offline mode)",
        "acp.preview.internet.local": "not needed (local)",
        "acp.preview.problem": "Not possible: {problem}",
        "acp.preview.yes": "Yes, add it",
        "acp.preview.no": "No",
        "acp.added": "{name} was added.",
        "acp.failed": "{what} did not work: {reason}",
        "acp.select_first": "Select an agent first.",
        "acp.login": "Sign in...",
        "acp.login.title": "Sign in to {name}",
        "acp.login.choose": "{name} offers these sign-ins. Choose one:",
        "acp.login.agent": "{name} opens its own sign-in (in your browser, with your provider's sign-in and second "
                           "factor); finish it there.",
        "acp.login.console": "{name}'s own sign-in opens in a console window; finish it there.",
        "acp.login.keys": "Enter the keys {name} asks for. They are stored in the credential vault and go only to "
                          "this agent.",
        "acp.login.none": "{name} lists no sign-in: it signs in by itself, or needs none.",
        "acp.login.done": "Signed in to {name}.",
        "acp.login.go": "Sign in",
        "acp.login.cancel": "Cancel",
        "acp.key": "Store a key...",
        "acp.key.title": "A key for {name}",
        "acp.key.variable": "Variable:",
        "acp.key.value": "Key:",
        "acp.key.repeat": "Again:",
        "acp.key.mismatch": "The two keys differ.",
        "acp.key.stored": "Stored in the credential vault; it goes only to {name}, as {variable}.",
        "acp.key.store": "Store",
        "acp.remove": "Remove...",
        "acp.removed": "Removed: {names}",
        "acp.working": "Working...",
        "acp.chat.with": "Chat with:",
        "acp.chat.ditto": "Ditto",
        "acp.chat.manage": "Agents...",
        "acp.chat.manage_tip": "Add, sign in to or remove the external agents you can chat with here.",
        "acp.chat.stop": "Stop",
        "acp.chat.thoughts": "Thoughts ({n})",
        "acp.chat.live": "{name} is answering (its own words):",
        "acp.chat.tool": "Tool call: {title} ({status})",
        "acp.chat.plan": "Plan: {entries}",
        "acp.chat.starting": "{name} is starting...",
        "acp.chat.first_start": "{name} starts for the first time and may download its package first; this can take up "
                                "to {minutes} minutes.",
        "acp.chat.notice": "{name}: {text}",
        "acp.chat.error": "{name}: {reason}",
        "acp.chat.stopped": "{name} stopped its turn.",
        "acp.perm.title": "{name} asks",
        "acp.perm.text": "{name} wants to do this (its own words): {title}",
        "acp.perm.allow": "Allow once",
        "acp.perm.reject": "Reject",
        "acp.perm.always": "Standing approvals...",
        "acp.perm.always_tip": "The agent also offers 'always'. The framework never chooses it for you; standing "
                               "approvals are made in their own window.",
        "acp.perm.no_once": "The agent offers no 'allow once' for this, only 'always' or reject. The framework never "
                            "chooses 'always', so only Reject is possible here.",
        "acp.form.title": "{name} asks",
        "acp.form.send": "Send",
        "acp.form.cancel": "Do not answer",
    },
    "de": {
        # Ditto als ACP-Agent (Datei > Einstellungen... > Ditto in Editoren)
        "acp.tab": "Ditto in Editoren",
        "acp.intro": "Editoren wie Zed, JetBrains-IDEs, Android Studio, Neovim oder Emacs können Ditto als KI-Agent "
                     "nutzen (Agent Client Protocol). Der Editor startet das Framework selbst mit dem Eintrag unten und "
                     "spricht mit Ditto; jeder Tool-Aufruf läuft innerhalb der Tool-Berechtigungen und steht im "
                     "Audit-Log. Ditto ist experimentell, auch im Editor.",
        "acp.command.label": "Befehl (für einen Editor, der nach dem Befehl des Agents fragt)",
        "acp.json.label": "Eintrag für die Agent-Einstellungen einer JetBrains-IDE (acp.json, agent_servers)",
        "acp.zed.label": "Eintrag für die settings.json von Zed (agent_servers, ein eigener Agent)",
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
        # Die ACP-Agenten, die die Person auswählt (Ditto > Agenten..., das Chat-Panel)
        "acp.menu.agents": "Agenten...",
        "acp.window.title": "Agenten",
        "acp.window.intro": "Externe Coding-Agenten, die du auswählst (ACP). Jeder arbeitet nur mit den Werkzeugen "
                            "dieses Frameworks: jede Aktion läuft über deine Werkzeug-Berechtigungen, die Freigaben "
                            "und das Audit-Log. Ditto bleibt die Vorgabe im Chat. Ein Agent, den du startest, läuft "
                            "als eigenes Programm mit deinen Rechten; er bekommt nur das Nötigste der Umgebung dieses "
                            "Computers und seine eigenen Schlüssel.",
        "acp.col.name": "Name",
        "acp.col.kind": "Verteilung",
        "acp.col.version": "Version",
        "acp.col.from": "Herkunft",
        "acp.col.internet": "Internet",
        "acp.col.keys": "Schlüssel",
        "acp.col.licence": "Lizenz",
        "acp.col.description": "Beschreibung",
        "acp.internet.needed": "nötig",
        "acp.internet.local": "lokal",
        "acp.from.registry": "ACP-Registry",
        "acp.from.command": "dein Befehl",
        "acp.yours": "Deine Agenten",
        "acp.registry.title": "ACP-Registry",
        "acp.registry.refresh": "Registry abrufen",
        "acp.registry.search": "Suchen:",
        "acp.registry.never": "Die ACP-Registry wurde noch nicht abgerufen. Sie wird nur abgerufen, wenn du auf den "
                              "Knopf klickst.",
        "acp.registry.count": "{n} Agenten in der Registry (Text aus der Registry).",
        "acp.kind.label": "Verteilung:",
        "acp.kind.auto": "die erste, die hier geht",
        "acp.add.registry": "Gewählten Agenten hinzufügen...",
        "acp.own.title": "Dein eigener Befehl",
        "acp.own.name": "Name:",
        "acp.own.command": "Programm:",
        "acp.own.browse": "Durchsuchen...",
        "acp.own.args": "Argumente:",
        "acp.own.local": "Geht ohne Internet (darf im Offline-Modus starten)",
        "acp.own.keys": "Variablen seiner Schlüssel (durch Komma getrennt):",
        "acp.add.command": "Deinen Befehl hinzufügen...",
        "acp.preview.title": "{name} hinzufügen?",
        "acp.preview.intro": "Der Text unten stammt aus der ACP-Registry oder von dir. Der Agent läuft genau mit diesem "
                             "Befehl, wenn du ihn startest.",
        "acp.preview.line": "{label}: {value}",
        "acp.preview.description": "Beschreibung",
        "acp.preview.kind": "Verteilung",
        "acp.preview.licence": "Lizenz",
        "acp.preview.command": "Befehl",
        "acp.preview.download": "Download",
        "acp.preview.sha256": "Prüfsumme (sha256)",
        "acp.preview.internet": "Internet",
        "acp.preview.internet.needed": "nötig (im Offline-Modus abgelehnt)",
        "acp.preview.internet.local": "nicht nötig (lokal)",
        "acp.preview.problem": "Nicht möglich: {problem}",
        "acp.preview.yes": "Ja, hinzufügen",
        "acp.preview.no": "Nein",
        "acp.added": "{name} wurde hinzugefügt.",
        "acp.failed": "{what} hat nicht geklappt: {reason}",
        "acp.select_first": "Wähle zuerst einen Agenten.",
        "acp.login": "Anmelden...",
        "acp.login.title": "Bei {name} anmelden",
        "acp.login.choose": "{name} bietet diese Anmeldungen an. Wähle eine:",
        "acp.login.agent": "{name} öffnet seine eigene Anmeldung (in deinem Browser, mit der Anmeldung deines "
                           "Anbieters und dem zweiten Faktor); schließe sie dort ab.",
        "acp.login.console": "Die eigene Anmeldung von {name} öffnet sich in einem Konsolenfenster; schließe sie dort "
                             "ab.",
        "acp.login.keys": "Gib die Schlüssel ein, die {name} braucht. Sie liegen im Tresor für Zugangsdaten und gehen "
                          "nur an diesen Agenten.",
        "acp.login.none": "{name} nennt keine Anmeldung: er meldet sich selbst an oder braucht keine.",
        "acp.login.done": "Bei {name} angemeldet.",
        "acp.login.go": "Anmelden",
        "acp.login.cancel": "Abbrechen",
        "acp.key": "Schlüssel speichern...",
        "acp.key.title": "Ein Schlüssel für {name}",
        "acp.key.variable": "Variable:",
        "acp.key.value": "Schlüssel:",
        "acp.key.repeat": "Noch einmal:",
        "acp.key.mismatch": "Die beiden Schlüssel unterscheiden sich.",
        "acp.key.stored": "Im Tresor für Zugangsdaten gespeichert; er geht nur an {name}, als {variable}.",
        "acp.key.store": "Speichern",
        "acp.remove": "Entfernen...",
        "acp.removed": "Entfernt: {names}",
        "acp.working": "Läuft...",
        "acp.chat.with": "Chatten mit:",
        "acp.chat.ditto": "Ditto",
        "acp.chat.manage": "Agenten...",
        "acp.chat.manage_tip": "Externe Agenten hinzufügen, anmelden oder entfernen, mit denen du hier chatten kannst.",
        "acp.chat.stop": "Stopp",
        "acp.chat.thoughts": "Gedanken ({n})",
        "acp.chat.live": "{name} antwortet (seine eigenen Worte):",
        "acp.chat.tool": "Werkzeugaufruf: {title} ({status})",
        "acp.chat.plan": "Plan: {entries}",
        "acp.chat.starting": "{name} startet...",
        "acp.chat.first_start": "{name} startet zum ersten Mal und lädt dafür vielleicht erst sein Paket herunter; das "
                                "kann bis zu {minutes} Minuten dauern.",
        "acp.chat.notice": "{name}: {text}",
        "acp.chat.error": "{name}: {reason}",
        "acp.chat.stopped": "{name} hat seinen Zug beendet.",
        "acp.perm.title": "{name} fragt",
        "acp.perm.text": "{name} möchte das tun (seine eigenen Worte): {title}",
        "acp.perm.allow": "Einmal erlauben",
        "acp.perm.reject": "Ablehnen",
        "acp.perm.always": "Dauerhafte Freigaben...",
        "acp.perm.always_tip": "Der Agent bietet auch „immer“ an. Das Framework wählt das nie für dich; dauerhafte "
                               "Freigaben entstehen in ihrem eigenen Fenster.",
        "acp.perm.no_once": "Der Agent bietet hierfür kein „einmal erlauben“ an, nur „immer“ oder ablehnen. Das "
                            "Framework wählt nie „immer“, darum ist hier nur Ablehnen möglich.",
        "acp.form.title": "{name} fragt",
        "acp.form.send": "Senden",
        "acp.form.cancel": "Nicht antworten",
    },
}

# The "Sie" form of exactly the German texts that address the person with du (the settings tab's texts have none).
FORMAL = {
    "de": {
        "acp.window.intro": "Externe Coding-Agenten, die Sie auswählen (ACP). Jeder arbeitet nur mit den Werkzeugen "
                            "dieses Frameworks: jede Aktion läuft über Ihre Werkzeug-Berechtigungen, die Freigaben "
                            "und das Audit-Log. Ditto bleibt die Vorgabe im Chat. Ein Agent, den Sie starten, läuft "
                            "als eigenes Programm mit Ihren Rechten; er bekommt nur das Nötigste der Umgebung dieses "
                            "Computers und seine eigenen Schlüssel.",
        "acp.from.command": "Ihr Befehl",
        "acp.yours": "Ihre Agenten",
        "acp.registry.never": "Die ACP-Registry wurde noch nicht abgerufen. Sie wird nur abgerufen, wenn Sie auf den "
                              "Knopf klicken.",
        "acp.own.title": "Ihr eigener Befehl",
        "acp.add.command": "Ihren Befehl hinzufügen...",
        "acp.preview.intro": "Der Text unten stammt aus der ACP-Registry oder von Ihnen. Der Agent läuft genau mit "
                             "diesem Befehl, wenn Sie ihn starten.",
        "acp.login.agent": "{name} öffnet seine eigene Anmeldung (in Ihrem Browser, mit der Anmeldung Ihres "
                           "Anbieters und dem zweiten Faktor); schließen Sie sie dort ab.",
        "acp.chat.manage_tip": "Externe Agenten hinzufügen, anmelden oder entfernen, mit denen Sie hier chatten "
                               "können.",
        "acp.perm.always_tip": "Der Agent bietet auch „immer“ an. Das Framework wählt das nie für Sie; dauerhafte "
                               "Freigaben entstehen in ihrem eigenen Fenster.",
    },
}
