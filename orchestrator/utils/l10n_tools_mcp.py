#!/usr/bin/env python3
"""
Texts of the tool permissions, the audit log, the AI folders, the Deep Ingest notice and the MCP connections in the
GUI (wave 5, GUI parity: every `llmcf tools` and `llmcf mcp` action also by mouse), English and German.
LocalizationManager merges them (localization.TEXT_MODULES); a key that exists already is refused there.

Every key has the same {placeholders} in both languages (tests/test_tools_mcp_gui.py). The main window's menu titles
are not translated ("Tools", "Security"), so a German text names a menu as it is shown. German terms: "Prüfprotokoll"
(audit log), "dauerhafte Freigabe" (standing approval), "Aufrufer" (caller). This module imports nothing from the
framework.
"""

TEXTS = {
    "en": {
        # menu entries
        "tmcp.menu.audit": "Audit Log...",
        "tmcp.menu.mcp": "AI App Connections (MCP)...",
        "tmcp.menu.crawl": "Deep Ingest Notice...",

        # decisions as the person reads them
        "tmcp.decision.allow": "Allow",
        "tmcp.decision.ask": "Ask",
        "tmcp.decision.deny": "Deny",

        # Tools > Tool Permissions (Ditto / MCP)...
        "tmcp.perm.whose": "Whose permissions:",
        "tmcp.perm.whose.shared": "Shared (every AI caller without permissions of its own)",
        "tmcp.perm.whose.ditto": "Ditto",
        "tmcp.perm.whose.mcp": "Local MCP clients (stdio)",
        "tmcp.perm.own": "{who} has permissions of its own: what you save here applies to {who} only.",
        "tmcp.perm.uses_shared": "{who} uses the shared permissions now (shown below). Saving here gives {who} "
                                 "permissions of its own, starting from the shared ones.",
        "tmcp.perm.callers_note": "Callers with permissions of their own (the shared permissions below do not apply "
                                  "to them): {names}. Choose them under 'Whose permissions'.",
        "tmcp.perm.col.effective": "Effective now",
        "tmcp.perm.reset": "Reset to recommended defaults...",
        "tmcp.perm.reset_caller": "Use the shared permissions again...",
        "tmcp.perm.reset.question": "Put the tool permissions back to the recommended defaults?\n\nEvery caller's "
                                    "own permissions and every standing approval are removed, and the setup asks "
                                    "again at the next start. This changes:\n{changes}",
        "tmcp.perm.reset_caller.question": "Let {who} use the shared tool permissions again?\n\nIts own permissions "
                                           "and its standing approvals are removed. This changes:\n{changes}",
        "tmcp.perm.reset.nothing": "Nothing to reset: the permissions are like that already.",
        "tmcp.perm.reset.done": "Tool permissions reset to the recommended defaults.",
        "tmcp.perm.reset_caller.done": "{who} use the shared tool permissions again.",
        "tmcp.perm.changed": "The tool permissions were changed elsewhere meanwhile; nothing was changed. Look at "
                             "them again.",
        "tmcp.perm.failed": "Nothing was changed: {reason}",
        "tmcp.perm.discard": "Discard your unsaved changes to the permissions of {who}?",

        # the MCP tokens (in Tool Permissions and in AI App Connections)
        "tmcp.token.start.copy": "A copy of token '{name}''s own permissions",
        "tmcp.token.reset": "Back to read only...",
        "tmcp.token.reset.question": "Make '{name}' a new token again?\n\nNow: {before}\nAfterwards: it may only "
                                     "read, and never more than your shared setting for a tool or the tool's own "
                                     "default, until you set its permissions again. The token itself stays the "
                                     "same; its client keeps its access to reading tools.",
        "tmcp.token.reset.nothing": "'{name}' is a new token already: it may only read.",
        "tmcp.token.reset.done": "'{name}' may only read again.",
        "tmcp.token.linked": "uses {link}'s permissions",
        "tmcp.token.revoke.question": "Revoke the token '{name}'? Its client loses access at once.\n\nDeleted with "
                                      "it: its permissions and its standing approvals ({n}).",
        "tmcp.token.revoked.grants": "Token '{name}', its permissions and its {n} standing approvals are deleted.",
        "tmcp.token.revoked.grants_failed": "Token '{name}' and its permissions are deleted. Its standing approvals "
                                            "could not be removed ({reason}); they no longer apply.",
        "tmcp.token.title": "MCP tokens",
        # the token list itself (in Tool Permissions and in AI App Connections)
        "tmcp.tokens.box": "MCP tokens (one per client)",
        "tmcp.tokens.note": "Each client that connects over HTTP, or over stdio with LCF_MCP_TOKEN, gets its own "
                            "token: its name is the caller in the audit log (mcp:<name>), and it has its own "
                            "permissions. A new token can only read until you allow more. A token is shown once; "
                            "revoking or rotating it ends its client's access at once, also its open connections.",
        "tmcp.tokens.col.name": "Name",
        "tmcp.tokens.col.actor": "Audit actor",
        "tmcp.tokens.col.permissions": "Permissions",
        "tmcp.tokens.col.created": "Created",
        "tmcp.tokens.col.rotated": "Rotated",
        "tmcp.tokens.add": "Add...",
        "tmcp.tokens.rotate": "Rotate",
        "tmcp.tokens.revoke": "Revoke",
        "tmcp.tokens.permissions": "Permissions...",
        "tmcp.tokens.unusable": "Token store unusable: {reason}",
        "tmcp.tokens.linked_to": "linked to {link}",
        "tmcp.tokens.add.title": "MCP token",
        "tmcp.tokens.add.name": "Name of the client (lower-case letters, digits and hyphens, e.g. claude-desktop):",
        "tmcp.tokens.add.start": "Its permissions start as:",
        "tmcp.tokens.start.read": "Read only (recommended for a new client)",
        "tmcp.tokens.start.mcp": "A copy of the local stdio clients' permissions",
        "tmcp.tokens.start.ditto": "A copy of Ditto's permissions",
        "tmcp.tokens.start.link": "Ditto's permissions by reference (a change to Ditto's applies at once)",
        "tmcp.tokens.rotate.question": "Replace the token '{name}'? The old one stops working at once; its permissions "
                                       "stay.",
        "tmcp.tokens.link.question": "The token '{name}' uses {link}'s permissions. Give it its own instead?",
        "tmcp.audit_problem": "Done, but the audit log could not record it: {reason}",

        # Tools > Folders the AI May Use... > Add folder...
        "tmcp.folders.kind.title": "Kind",
        "tmcp.folders.kind.question": "What the folder holds (AI clients are shown it):",
        "tmcp.folders.kind.any": "Model or dataset files (any)",
        "tmcp.folders.kind.model": "Model files (model)",
        "tmcp.folders.kind.dataset": "Dataset files (dataset)",

        # Tools > Deep Ingest Notice...
        "tmcp.crawl.confirm": "Confirm: I have read the notice.",
        "tmcp.crawl.already": "Already read (acknowledged in the {via}).",
        "tmcp.crawl.via.gui": "GUI",
        "tmcp.crawl.via.cli": "command line",
        "tmcp.crawl.via.unknown": "GUI or on the command line",
        "tmcp.crawl.ack": "Acknowledged. AI clients may now ask to crawl documentation sites (crawl_docs_site); you "
                          "confirm every crawl.",
        "tmcp.crawl.not_ack": "Not acknowledged: AI clients cannot crawl until a person has read it.",
        "tmcp.crawl.ack_unrecorded": "Acknowledged, but the audit log could not record it: {reason}",
        "tmcp.crawl.not_saved": "Your answer could not be remembered ({reason}); AI clients cannot crawl yet, and "
                                "the notice is shown again.",

        # Security > Audit Log...
        "tmcp.audit.title": "Audit Log",
        "tmcp.audit.intro": "Every tool call of Ditto and AI clients, and every change a person made to what they "
                            "may do, newest first. The entries are chained: 'Check integrity' finds changed or "
                            "removed entries. Log file: {path}",
        "tmcp.audit.none": "There is no audit log.",
        "tmcp.audit.tab.entries": "Entries",
        "tmcp.audit.tab.summary": "Summary per caller",
        "tmcp.audit.limit": "Entries per page:",
        "tmcp.audit.details": "Details (repairs, duration, result, links, session)",
        "tmcp.audit.newer": "Newer",
        "tmcp.audit.older": "Older",
        "tmcp.audit.refresh": "Refresh",
        "tmcp.audit.page": "Entries {first} to {last} of {total}, newest first.",
        "tmcp.audit.empty": "The audit log has no entries.",
        "tmcp.audit.unreadable": "The audit log {path} cannot be read: {reason}",
        "tmcp.audit.check": "Check integrity",
        "tmcp.audit.checking": "Checking every entry of the chain...",
        "tmcp.audit.archive": "Archive...",
        "tmcp.audit.archive.question": "Move the audit log aside and start a new one?\n\nLog: {path}\nIt is kept "
                                       "next to it with the time in its name, together with its anchor; nothing is "
                                       "deleted. The next tool call starts a new log, and 'Check integrity' then "
                                       "checks the new log only.",
        "tmcp.audit.archiving": "Moving the audit log aside...",
        "tmcp.audit.archive.done": "Audit log moved to {path}; the next tool call starts a new log.",
        "tmcp.audit.archive.none": "There is no audit log to archive.",
        "tmcp.audit.archive.failed": "The audit log {path} cannot be moved: {reason}. Close the AI clients that use "
                                     "the framework and try again.",
        "tmcp.audit.summary.count": "Count",
        "tmcp.audit.summary.counting": "Counting the newest {n} entries...",
        "tmcp.audit.summary.note": "Counted over the newest {n} entries. 'Cannot reported': the AI said a task cannot "
                                   "be done; the framework checked each report (confirmed, contradicted, premature, "
                                   "unchecked).",
        "tmcp.audit.col.time": "Time",
        "tmcp.audit.col.who": "Who",
        "tmcp.audit.col.tool": "Tool",
        "tmcp.audit.col.decision": "Decision",
        "tmcp.audit.col.confirmed": "Confirmed",
        "tmcp.audit.col.outcome": "Outcome",
        "tmcp.audit.col.reason": "Reason",
        "tmcp.audit.col.asked": "Asked / answer",
        "tmcp.audit.col.repairs": "Repairs",
        "tmcp.audit.col.ms": "ms",
        "tmcp.audit.col.result": "Result",
        "tmcp.audit.col.links": "Links",
        "tmcp.audit.col.session": "Session",
        "tmcp.audit.col.calls": "Calls",
        "tmcp.audit.col.refused": "Refused",
        "tmcp.audit.col.cannot": "Cannot reported",
        "tmcp.audit.col.check_confirmed": "confirmed",
        "tmcp.audit.col.check_contradicted": "contradicted",
        "tmcp.audit.col.check_premature": "premature",
        "tmcp.audit.col.check_unchecked": "unchecked",
        # the integrity check in plain words
        "tmcp.check.keyed": "The audit log is intact: {n} entries, protected by your key.",
        "tmcp.check.unkeyed_now": "The audit log is intact: {n} entries. They were written before a key was "
                                  "available; the next entry is protected by your key.",
        "tmcp.check.unkeyed": "The audit log is intact: {n} entries, but without a key (no keyring available), so "
                              "someone who can edit the file could rebuild the chain.",
        "tmcp.check.modified": "The audit log was changed: entry {entry} no longer fits the chain.",
        "tmcp.check.modified_somewhere": "The audit log was changed.",
        "tmcp.check.removed": "Entries were removed from the audit log, from entry {entry} on.",
        "tmcp.check.key_mismatch": "The audit log cannot be checked: it was written with another key than the one "
                                   "in your keyring (keyring reset or unavailable, another user's log), or it was "
                                   "tampered with.",
        "tmcp.check.key_mismatch_at": "The audit log cannot be checked from entry {entry} on: it was written with "
                                      "another key than the one in your keyring (keyring reset or unavailable, "
                                      "another user's log), or it was tampered with.",
        "tmcp.check.anchor_missing": "The anchor file of the audit log is missing: changed entries are still found, "
                                     "but the removal of the newest entries is not.",
        "tmcp.check.anchor_missing_at": "The anchor file was missing when entry {entry} was written: the removal of "
                                        "the newest entries before it cannot be ruled out.",
        "tmcp.check.failed": "The check could not run: {reason}",

        # Security > AI App Connections (MCP)...
        "tmcp.mcp.title": "AI App Connections (MCP)",
        "tmcp.mcp.intro": "AI apps such as Claude Code, Claude Desktop or VS Code use this framework's tools through "
                          "MCP. Sign in to these apps with your own Claude account or key; the framework never sees "
                          "it. Copy the entry for your app, or let the framework add it to Claude Desktop.",
        "tmcp.mcp.claude_code": "Claude Code (run once in a terminal)",
        "tmcp.mcp.claude_desktop": "Claude Desktop (its configuration file: {path})",
        "tmcp.mcp.vscode": "VS Code (.vscode/mcp.json in your project)",
        "tmcp.mcp.copy": "Copy",
        "tmcp.mcp.copied": "Copied to the clipboard.",
        "tmcp.mcp.snippets_failed": "The connection entries cannot be shown: {reason}",
        "tmcp.mcp.write": "Write to Claude Desktop...",
        "tmcp.mcp.write.question": "Add this framework to Claude Desktop's configuration?\n\nFile: {path}\n{state}\n"
                                   "Change: {change}\nOther entries, kept as they are: {others}\n{backup}\n\nThe "
                                   "entry:\n{entry}",
        "tmcp.mcp.write.exists": "The file exists.",
        "tmcp.mcp.write.new": "The file does not exist yet; it is created.",
        "tmcp.mcp.write.adds": "adds the entry '{name}'",
        "tmcp.mcp.write.replaces": "replaces the existing entry '{name}'",
        "tmcp.mcp.write.backup": "A backup of the current file is written to {path} (it replaces an older backup).",
        "tmcp.mcp.write.no_backup": "No backup is needed: there is no file yet.",
        "tmcp.mcp.write.none": "none",
        "tmcp.mcp.write.unchanged": "Claude Desktop's configuration {path} has this entry already; nothing to change.",
        "tmcp.mcp.write.done": "Added to {path}. Restart Claude Desktop.",
        "tmcp.mcp.write.failed": "Nothing was written: {reason}",
        "tmcp.http.box": "Local HTTP server (for apps that connect with a named token)",
        "tmcp.http.note": "Most apps start the framework themselves (the entries above). An app that connects over "
                          "HTTP needs this server and a named token (below). It listens on this computer only; "
                          "inside a container, 'Container binding' binds the container's interfaces (publish the "
                          "port to the host's loopback: -p 127.0.0.1:PORT:PORT). It runs as a process of this "
                          "program until you stop it or close the framework.",
        "tmcp.http.host": "Address:",
        "tmcp.http.port": "Port:",
        "tmcp.http.container": "Container binding (--container)",
        "tmcp.http.start": "Start server",
        "tmcp.http.stop": "Stop server",
        "tmcp.http.state.off": "Not running.",
        "tmcp.http.state.starting": "Starting at {url}...",
        "tmcp.http.state.running": "Serving at {url} (process {pid}).",
        "tmcp.http.state.stopping": "Stopping...",
        "tmcp.http.state.stopped": "Stopped (exit code {code}).",
        "tmcp.http.state.failed": "Ended with exit code {code}: {reason}",
        "tmcp.http.refused": "The server does not start: {reason}",
        "tmcp.http.start_failed": "The server could not be started: {reason}",
        "tmcp.http.exit.question": "The MCP server (HTTP) still serves at {url}; apps connected to it lose access "
                                   "when it stops. Stop it and close the framework?",
    },
    "de": {
        "tmcp.menu.audit": "Prüfprotokoll...",
        "tmcp.menu.mcp": "Verbindungen zu KI-Apps (MCP)...",
        "tmcp.menu.crawl": "Hinweis zu Deep Ingest...",

        "tmcp.decision.allow": "Erlauben",
        "tmcp.decision.ask": "Fragen",
        "tmcp.decision.deny": "Verweigern",

        "tmcp.perm.whose": "Wessen Berechtigungen:",
        "tmcp.perm.whose.shared": "Gemeinsame (jeder KI-Aufrufer ohne eigene Berechtigungen)",
        "tmcp.perm.whose.ditto": "Ditto",
        "tmcp.perm.whose.mcp": "Lokale MCP-Clients (stdio)",
        "tmcp.perm.own": "{who} hat eigene Berechtigungen: was du hier speicherst, gilt nur für {who}.",
        "tmcp.perm.uses_shared": "{who} nutzt jetzt die gemeinsamen Berechtigungen (unten zu sehen). Speichern hier "
                                 "gibt {who} eigene Berechtigungen, ausgehend von den gemeinsamen.",
        "tmcp.perm.callers_note": "Aufrufer mit eigenen Berechtigungen (für sie gelten die gemeinsamen unten nicht): "
                                  "{names}. Wähle sie unter „Wessen Berechtigungen“.",
        "tmcp.perm.col.effective": "Gilt jetzt",
        "tmcp.perm.reset": "Auf die empfohlenen Standards zurücksetzen...",
        "tmcp.perm.reset_caller": "Wieder die gemeinsamen Berechtigungen nutzen...",
        "tmcp.perm.reset.question": "Die Werkzeug-Berechtigungen auf die empfohlenen Standards zurücksetzen?\n\nDie "
                                    "eigenen Berechtigungen jedes Aufrufers und jede dauerhafte Freigabe werden "
                                    "entfernt, und die Einrichtung fragt beim nächsten Start erneut. Das ändert "
                                    "sich:\n{changes}",
        "tmcp.perm.reset_caller.question": "{who} wieder die gemeinsamen Werkzeug-Berechtigungen nutzen lassen?\n\n"
                                           "Die eigenen Berechtigungen und die dauerhaften Freigaben werden entfernt. "
                                           "Das ändert sich:\n{changes}",
        "tmcp.perm.reset.nothing": "Nichts zurückzusetzen: die Berechtigungen sind schon so.",
        "tmcp.perm.reset.done": "Werkzeug-Berechtigungen auf die empfohlenen Standards zurückgesetzt.",
        "tmcp.perm.reset_caller.done": "{who} nutzt wieder die gemeinsamen Werkzeug-Berechtigungen.",
        "tmcp.perm.changed": "Die Werkzeug-Berechtigungen wurden inzwischen woanders geändert; nichts wurde geändert. "
                             "Sieh sie dir noch einmal an.",
        "tmcp.perm.failed": "Nichts wurde geändert: {reason}",
        "tmcp.perm.discard": "Deine nicht gespeicherten Änderungen an den Berechtigungen von {who} verwerfen?",

        "tmcp.token.start.copy": "Eine Kopie der eigenen Berechtigungen des Tokens „{name}“",
        "tmcp.token.reset": "Zurück auf nur lesen...",
        "tmcp.token.reset.question": "„{name}“ wieder zu einem neuen Token machen?\n\nJetzt: {before}\nDanach: es "
                                     "darf nur lesen, und nie mehr als deine gemeinsame Einstellung für ein Werkzeug "
                                     "oder dessen eigener Standard, bis du seine Berechtigungen wieder festlegst. Das "
                                     "Token selbst bleibt gleich; sein Client darf weiter lesende Werkzeuge nutzen.",
        "tmcp.token.reset.nothing": "„{name}“ ist schon ein neues Token: es darf nur lesen.",
        "tmcp.token.reset.done": "„{name}“ darf wieder nur lesen.",
        "tmcp.token.linked": "nutzt die Berechtigungen von {link}",
        "tmcp.token.revoke.question": "Das Token „{name}“ widerrufen? Sein Client verliert sofort den Zugang.\n\n"
                                      "Mit ihm gelöscht: seine Berechtigungen und seine dauerhaften Freigaben ({n}).",
        "tmcp.token.revoked.grants": "Token „{name}“, seine Berechtigungen und seine {n} dauerhaften Freigaben sind "
                                     "gelöscht.",
        "tmcp.token.revoked.grants_failed": "Token „{name}“ und seine Berechtigungen sind gelöscht. Seine dauerhaften "
                                            "Freigaben ließen sich nicht entfernen ({reason}); sie gelten nicht mehr.",
        "tmcp.token.title": "MCP-Tokens",
        "tmcp.tokens.box": "MCP-Tokens (eines je Client)",
        "tmcp.tokens.note": "Jeder Client, der sich über HTTP verbindet oder über stdio mit LCF_MCP_TOKEN, bekommt sein "
                            "eigenes Token: sein Name ist der Aufrufer im Prüfprotokoll (mcp:<name>), und es hat "
                            "eigene Berechtigungen. Ein neues Token darf nur lesen, bis du mehr erlaubst. Ein Token "
                            "wird einmal gezeigt; Widerrufen oder Rotieren beendet den Zugang seines Clients sofort, "
                            "auch seine offenen Verbindungen.",
        "tmcp.tokens.col.name": "Name",
        "tmcp.tokens.col.actor": "Akteur im Prüfprotokoll",
        "tmcp.tokens.col.permissions": "Berechtigungen",
        "tmcp.tokens.col.created": "Angelegt",
        "tmcp.tokens.col.rotated": "Rotiert",
        "tmcp.tokens.add": "Hinzufügen...",
        "tmcp.tokens.rotate": "Rotieren",
        "tmcp.tokens.revoke": "Widerrufen",
        "tmcp.tokens.permissions": "Berechtigungen...",
        "tmcp.tokens.unusable": "Token-Speicher nicht nutzbar: {reason}",
        "tmcp.tokens.linked_to": "verknüpft mit {link}",
        "tmcp.tokens.add.title": "MCP-Token",
        "tmcp.tokens.add.name": "Name des Clients (Kleinbuchstaben, Ziffern und Bindestriche, z. B. claude-desktop):",
        "tmcp.tokens.add.start": "Seine Berechtigungen beginnen als:",
        "tmcp.tokens.start.read": "Nur lesen (empfohlen für einen neuen Client)",
        "tmcp.tokens.start.mcp": "Eine Kopie der Berechtigungen der lokalen stdio-Clients",
        "tmcp.tokens.start.ditto": "Eine Kopie der Berechtigungen Dittos",
        "tmcp.tokens.start.link": "Dittos Berechtigungen per Verweis (eine Änderung an Dittos gilt sofort)",
        "tmcp.tokens.rotate.question": "Das Token „{name}“ ersetzen? Das alte wirkt sofort nicht mehr; seine "
                                       "Berechtigungen bleiben.",
        "tmcp.tokens.link.question": "Das Token „{name}“ nutzt die Berechtigungen von {link}. Ihm stattdessen eigene "
                                     "geben?",
        "tmcp.audit_problem": "Erledigt, aber das Prüfprotokoll konnte es nicht festhalten: {reason}",

        "tmcp.folders.kind.title": "Art",
        "tmcp.folders.kind.question": "Was in dem Ordner liegt (wird KI-Clients gezeigt):",
        "tmcp.folders.kind.any": "Modell- oder Datensatz-Dateien (any)",
        "tmcp.folders.kind.model": "Modell-Dateien (model)",
        "tmcp.folders.kind.dataset": "Datensatz-Dateien (dataset)",

        "tmcp.crawl.confirm": "Bestätigen: Ich habe den Hinweis gelesen.",
        "tmcp.crawl.already": "Bereits gelesen (bestätigt in der {via}).",
        "tmcp.crawl.via.gui": "GUI",
        "tmcp.crawl.via.cli": "Kommandozeile",
        "tmcp.crawl.via.unknown": "GUI oder auf der Kommandozeile",
        "tmcp.crawl.ack": "Bestätigt. KI-Clients dürfen jetzt darum bitten, Dokumentations-Seiten zu crawlen "
                          "(crawl_docs_site); du bestätigst jeden Crawl.",
        "tmcp.crawl.not_ack": "Nicht bestätigt: KI-Clients können nicht crawlen, bis ein Mensch den Hinweis gelesen "
                              "hat.",
        "tmcp.crawl.ack_unrecorded": "Bestätigt, aber das Prüfprotokoll konnte es nicht festhalten: {reason}",
        "tmcp.crawl.not_saved": "Deine Antwort ließ sich nicht merken ({reason}); KI-Clients können noch nicht "
                                "crawlen, und der Hinweis erscheint wieder.",

        "tmcp.audit.title": "Prüfprotokoll",
        "tmcp.audit.intro": "Jeder Werkzeug-Aufruf von Ditto und KI-Clients und jede Änderung, die ein Mensch an "
                            "ihren Rechten vorgenommen hat, das Neueste zuerst. Die Einträge sind verkettet: "
                            "„Unversehrtheit prüfen“ findet geänderte oder entfernte Einträge. Protokolldatei: {path}",
        "tmcp.audit.none": "Es gibt kein Prüfprotokoll.",
        "tmcp.audit.tab.entries": "Einträge",
        "tmcp.audit.tab.summary": "Übersicht je Aufrufer",
        "tmcp.audit.limit": "Einträge pro Seite:",
        "tmcp.audit.details": "Details (Reparaturen, Dauer, Ergebnis, Links, Sitzung)",
        "tmcp.audit.newer": "Neuere",
        "tmcp.audit.older": "Ältere",
        "tmcp.audit.refresh": "Aktualisieren",
        "tmcp.audit.page": "Einträge {first} bis {last} von {total}, das Neueste zuerst.",
        "tmcp.audit.empty": "Das Prüfprotokoll hat keine Einträge.",
        "tmcp.audit.unreadable": "Das Prüfprotokoll {path} lässt sich nicht lesen: {reason}",
        "tmcp.audit.check": "Unversehrtheit prüfen",
        "tmcp.audit.checking": "Jeder Eintrag der Kette wird geprüft...",
        "tmcp.audit.archive": "Archivieren...",
        "tmcp.audit.archive.question": "Das Prüfprotokoll beiseitelegen und ein neues beginnen?\n\nProtokoll: {path}\n"
                                       "Es bleibt daneben erhalten, mit der Uhrzeit im Namen und mit seinem Anker; "
                                       "nichts wird gelöscht. Der nächste Werkzeug-Aufruf beginnt ein neues "
                                       "Protokoll, und „Unversehrtheit prüfen“ prüft dann nur das neue.",
        "tmcp.audit.archiving": "Das Prüfprotokoll wird beiseitegelegt...",
        "tmcp.audit.archive.done": "Prüfprotokoll nach {path} verschoben; der nächste Werkzeug-Aufruf beginnt ein "
                                   "neues.",
        "tmcp.audit.archive.none": "Es gibt kein Prüfprotokoll zum Archivieren.",
        "tmcp.audit.archive.failed": "Das Prüfprotokoll {path} lässt sich nicht verschieben: {reason}. Schließe die "
                                     "KI-Clients, die das Framework nutzen, und versuche es noch einmal.",
        "tmcp.audit.summary.count": "Zählen",
        "tmcp.audit.summary.counting": "Die neuesten {n} Einträge werden gezählt...",
        "tmcp.audit.summary.note": "Gezählt über die neuesten {n} Einträge. „Geht nicht gemeldet“: die KI sagte, eine "
                                   "Aufgabe sei nicht machbar; das Framework hat jede Meldung geprüft (bestätigt, "
                                   "widerlegt, verfrüht, ungeprüft).",
        "tmcp.audit.col.time": "Zeit",
        "tmcp.audit.col.who": "Wer",
        "tmcp.audit.col.tool": "Werkzeug",
        "tmcp.audit.col.decision": "Entscheidung",
        "tmcp.audit.col.confirmed": "Bestätigt",
        "tmcp.audit.col.outcome": "Ergebnis",
        "tmcp.audit.col.reason": "Grund",
        "tmcp.audit.col.asked": "Gefragt / Antwort",
        "tmcp.audit.col.repairs": "Reparaturen",
        "tmcp.audit.col.ms": "ms",
        "tmcp.audit.col.result": "Resultat",
        "tmcp.audit.col.links": "Links",
        "tmcp.audit.col.session": "Sitzung",
        "tmcp.audit.col.calls": "Aufrufe",
        "tmcp.audit.col.refused": "Abgelehnt",
        "tmcp.audit.col.cannot": "Geht nicht gemeldet",
        "tmcp.audit.col.check_confirmed": "bestätigt",
        "tmcp.audit.col.check_contradicted": "widerlegt",
        "tmcp.audit.col.check_premature": "verfrüht",
        "tmcp.audit.col.check_unchecked": "ungeprüft",
        "tmcp.check.keyed": "Das Prüfprotokoll ist unversehrt: {n} Einträge, geschützt mit deinem Schlüssel.",
        "tmcp.check.unkeyed_now": "Das Prüfprotokoll ist unversehrt: {n} Einträge. Sie wurden geschrieben, bevor ein "
                                  "Schlüssel da war; der nächste Eintrag ist mit deinem Schlüssel geschützt.",
        "tmcp.check.unkeyed": "Das Prüfprotokoll ist unversehrt: {n} Einträge, aber ohne Schlüssel (kein "
                              "Schlüsselbund verfügbar), daher könnte jemand, der die Datei bearbeiten kann, die "
                              "Kette neu berechnen.",
        "tmcp.check.modified": "Das Prüfprotokoll wurde geändert: Eintrag {entry} passt nicht mehr in die Kette.",
        "tmcp.check.modified_somewhere": "Das Prüfprotokoll wurde geändert.",
        "tmcp.check.removed": "Aus dem Prüfprotokoll wurden Einträge entfernt, ab Eintrag {entry}.",
        "tmcp.check.key_mismatch": "Das Prüfprotokoll lässt sich nicht prüfen: es wurde mit einem anderen Schlüssel "
                                   "geschrieben als dem in deinem Schlüsselbund (Schlüsselbund zurückgesetzt oder "
                                   "nicht verfügbar, Protokoll eines anderen Benutzers), oder es wurde manipuliert.",
        "tmcp.check.key_mismatch_at": "Das Prüfprotokoll lässt sich ab Eintrag {entry} nicht prüfen: es wurde mit "
                                      "einem anderen Schlüssel geschrieben als dem in deinem Schlüsselbund "
                                      "(Schlüsselbund zurückgesetzt oder nicht verfügbar, Protokoll eines anderen "
                                      "Benutzers), oder es wurde manipuliert.",
        "tmcp.check.anchor_missing": "Die Ankerdatei des Prüfprotokolls fehlt: geänderte Einträge werden weiter "
                                     "gefunden, das Entfernen der neuesten Einträge aber nicht.",
        "tmcp.check.anchor_missing_at": "Die Ankerdatei fehlte, als Eintrag {entry} geschrieben wurde: dass die "
                                        "neuesten Einträge davor entfernt wurden, lässt sich nicht ausschließen.",
        "tmcp.check.failed": "Die Prüfung ließ sich nicht ausführen: {reason}",

        "tmcp.mcp.title": "Verbindungen zu KI-Apps (MCP)",
        "tmcp.mcp.intro": "KI-Apps wie Claude Code, Claude Desktop oder VS Code nutzen die Werkzeuge dieses "
                          "Frameworks über MCP. Melde dich in diesen Apps mit deinem eigenen Claude-Konto oder "
                          "Schlüssel an; das Framework sieht ihn nie. Kopiere den Eintrag für deine App, oder lass "
                          "das Framework ihn in Claude Desktop eintragen.",
        "tmcp.mcp.claude_code": "Claude Code (einmal in einem Terminal ausführen)",
        "tmcp.mcp.claude_desktop": "Claude Desktop (seine Konfigurationsdatei: {path})",
        "tmcp.mcp.vscode": "VS Code (.vscode/mcp.json in deinem Projekt)",
        "tmcp.mcp.copy": "Kopieren",
        "tmcp.mcp.copied": "In die Zwischenablage kopiert.",
        "tmcp.mcp.snippets_failed": "Die Verbindungs-Einträge lassen sich nicht zeigen: {reason}",
        "tmcp.mcp.write": "In Claude Desktop eintragen...",
        "tmcp.mcp.write.question": "Dieses Framework in die Konfiguration von Claude Desktop eintragen?\n\nDatei: "
                                   "{path}\n{state}\nÄnderung: {change}\nAndere Einträge, sie bleiben wie sie sind: "
                                   "{others}\n{backup}\n\nDer Eintrag:\n{entry}",
        "tmcp.mcp.write.exists": "Die Datei existiert.",
        "tmcp.mcp.write.new": "Die Datei gibt es noch nicht; sie wird angelegt.",
        "tmcp.mcp.write.adds": "fügt den Eintrag „{name}“ hinzu",
        "tmcp.mcp.write.replaces": "ersetzt den vorhandenen Eintrag „{name}“",
        "tmcp.mcp.write.backup": "Eine Sicherung der jetzigen Datei wird nach {path} geschrieben (sie ersetzt eine "
                                 "ältere Sicherung).",
        "tmcp.mcp.write.no_backup": "Keine Sicherung nötig: es gibt noch keine Datei.",
        "tmcp.mcp.write.none": "keine",
        "tmcp.mcp.write.unchanged": "Die Konfiguration von Claude Desktop {path} hat diesen Eintrag schon; nichts zu "
                                    "ändern.",
        "tmcp.mcp.write.done": "In {path} eingetragen. Starte Claude Desktop neu.",
        "tmcp.mcp.write.failed": "Nichts wurde geschrieben: {reason}",
        "tmcp.http.box": "Lokaler HTTP-Server (für Apps, die sich mit einem benannten Token verbinden)",
        "tmcp.http.note": "Die meisten Apps starten das Framework selbst (die Einträge oben). Eine App, die sich über "
                          "HTTP verbindet, braucht diesen Server und ein benanntes Token (unten). Er lauscht nur auf "
                          "diesem Computer; in einem Container bindet „Container-Bindung“ die Schnittstellen des "
                          "Containers (gib den Port nur auf dem Loopback des Hosts frei: -p 127.0.0.1:PORT:PORT). Er "
                          "läuft als Prozess dieses Programms, bis du ihn stoppst oder das Framework schließt.",
        "tmcp.http.host": "Adresse:",
        "tmcp.http.port": "Port:",
        "tmcp.http.container": "Container-Bindung (--container)",
        "tmcp.http.start": "Server starten",
        "tmcp.http.stop": "Server stoppen",
        "tmcp.http.state.off": "Läuft nicht.",
        "tmcp.http.state.starting": "Startet unter {url}...",
        "tmcp.http.state.running": "Läuft unter {url} (Prozess {pid}).",
        "tmcp.http.state.stopping": "Wird gestoppt...",
        "tmcp.http.state.stopped": "Gestoppt (Rückgabewert {code}).",
        "tmcp.http.state.failed": "Beendet mit Rückgabewert {code}: {reason}",
        "tmcp.http.refused": "Der Server startet nicht: {reason}",
        "tmcp.http.start_failed": "Der Server ließ sich nicht starten: {reason}",
        "tmcp.http.exit.question": "Der MCP-Server (HTTP) läuft noch unter {url}; damit verbundene Apps verlieren den "
                                   "Zugang, wenn er stoppt. Ihn stoppen und das Framework schließen?",
    },
}
