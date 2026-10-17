#!/usr/bin/env python3
"""
Texts of the chat panel (delegation design 5.1-5.9), English and German. LocalizationManager merges them
(localization.TEXT_MODULES); a key that exists already is refused there, so these never replace another text.

The feed's own texts (who decided, the framework's explanation, "Clear chat" counts) stay in Core/chat_feed.py; the
panel shows them through chat_feed.feed_text. German terms: "Freigabe" (approval), "ausstehende Freigabe" (pending
approval), "Dauerfreigabe" (standing approval), "Vollmacht" (delegation).

Every key has the same {placeholders} in both languages (tests/test_chat_panel.py). The main window's menus are not
translated, so a German text names a menu as it is shown ("Tools > Configure AI Agent (Ditto)…"). This module
imports nothing from the framework.
"""

TEXTS = {
    "en": {
        # the dock, its header and the "..." menu
        "chat.panel.title": "AI Chat",
        "chat.panel.heading": "Ditto and your AI apps",
        "chat.panel.access_tip": "The provider and sign-in Ditto uses ({path:act_ditto_settings})",
        "chat.panel.thinking": "Ditto is thinking…",
        "chat.menu.button": "More",
        "chat.menu.clear": "Clear chat…",
        "chat.menu.approvals": "Pending approvals window…",
        "chat.menu.permissions": "Tool permissions and delegations…",
        "chat.menu.auto_show": "Show the chat when a question waits",
        "chat.filter.label": "Show:",
        "chat.filter.all": "All",
        "chat.filter.needs_you": "Needs you ({n})",
        "chat.filter.decisions": "Decisions",
        "chat.filter.ditto": "Ditto chat",
        "chat.clear.button": "Clear chat…",
        "chat.clear.tip": "Deletes your conversation with Ditto after a question that lists exactly what is deleted",

        # the main window
        "chat.view.menu": "View",
        "chat.view.toggle": "AI Chat Panel",
        "chat.view.focus": "Write in the Chat",
        "chat.toolbar.button": "AI Chat",
        "chat.toolbar.button_count": "AI Chat ({n})",
        "chat.toolbar.tip": "Shows or hides the chat on the right (F9). The number counts new messages and questions "
                            "that wait for you.",

        # the composer
        "chat.composer.placeholder": "Ask Ditto about targets, builds or compilation errors… (Enter sends, "
                                     "Shift+Enter adds a line)",
        "chat.composer.send": "Send",
        "chat.composer.to": "To: Ditto",
        "chat.composer.about": "About: {what}",
        "chat.composer.remove_about": "Remove",
        "chat.composer.remove_about_tip": "Removes the reference (Escape)",
        "chat.composer.input_name": "Your message to Ditto",
        "chat.composer.no_ditto": "Ditto is not available: the AI assistant did not start (see the log).",
        "chat.composer.not_set_up": "Ditto cannot answer yet: set it up in {path:act_ditto_setup}",

        # the feed view
        "chat.view.earlier": "Show earlier messages",
        "chat.view.new_messages": "New messages ↓",
        "chat.view.since": "New since you last looked",
        "chat.view.empty": "Nothing here yet. Ask Ditto something; what AI apps do for you also appears here.",
        "chat.view.needs_you": "Needs you",
        "chat.view.not_stored": "A message was shown but not saved in the chat ({reason}).",
        "chat.view.feed_problem": "The chat file cannot be used ({reason}); messages are shown but not kept.",

        # "New since you last looked"
        "chat.away.since": "Since {time}: {parts}",
        "chat.away.answers.one": "1 answer from Ditto",
        "chat.away.answers.many": "{n} answers from Ditto",
        "chat.away.decisions.one": "1 decision by an AI app",
        "chat.away.decisions.many": "{n} decisions by AI apps",
        "chat.away.results.one": "1 result",
        "chat.away.results.many": "{n} results",
        "chat.away.failed": "{n} failed",
        "chat.away.blocked.one": "1 piece of work an AI could not do",
        "chat.away.blocked.many": "{n} pieces of work AI apps could not do",
        "chat.away.notices.one": "1 notice",
        "chat.away.notices.many": "{n} notices",
        "chat.away.needs.one": "1 thing needs you",
        "chat.away.needs.many": "{n} things need you",
        "chat.away.nothing": "nothing new",

        # bubbles
        "chat.sender.you": "You",
        "chat.sender.ditto": "Ditto",
        "chat.sender.framework": "The framework",
        "chat.sender.reserved": "'{name}' (calls itself so; not the framework's Ditto)",
        "chat.sender.accessible": "{sender}, {time}",
        "chat.bubble.warning": "Warning:",
        "chat.bubble.written_by_framework": "written by the framework",
        "chat.bubble.original_words": "Show the original words",
        "chat.bubble.speaking_names": "Show the names",
        "chat.bubble.show_all": "Show all ({n} lines)",
        "chat.bubble.full_text_title": "Full text",
        "chat.bubble.copy": "Copy",
        "chat.bubble.select_all": "Select all",
        "chat.bubble.open_address": "Open address…",
        "chat.bubble.open_address_title": "Open address",
        "chat.bubble.open_address_question": "Open this address in your browser?\n\n{url}\n\nIt comes from a chat "
                                             "message; the framework did not check it.",
        "chat.bubble.legend": "Names of the ids in the code:",
        "chat.bubble.local": "Only shown in this window",
        "chat.group.title": "{who} did {n} things",
        "chat.group.show": "Show",
        "chat.group.hide": "Hide",

        # rows the panel writes itself
        "chat.tool.used": "Used tool: {title}",
        "chat.tool.not_run": "Tool not run: {title} ({detail})",
        "chat.notice.new_conversation": "The conversation got long: Ditto summarized it and continues in a new "
                                        "conversation.",
        "chat.notice.source_local": "Ditto now uses its own tools: a new conversation.",
        # LEGACY: the removed remote mode wrote these keys into feed rows; they stay so stored rows still render.
        # Nothing writes them any more.
        "chat.notice.source_remote": "Ditto now uses the tools of the framework at {name}: a new conversation.",
        "chat.tool.used_on": "Used on {place}: {title}",
        "chat.tool.not_run_on": "Not run on {place}: {title} ({detail})",
        "chat.notice.other_computer": "About the other computer: {notice}",
        "chat.notice.source_hybrid": "Ditto now works with this computer and {name} together: a new conversation.",
        "chat.notice.source_hybrid_unnamed": "Ditto now works with this computer and the other computer together: a "
                                             "new conversation.",
        # (end of the legacy keys)
        "chat.notice.error": "Ditto could not answer: {error}",
        "chat.notice.proposal": "{who} proposed a fix for the build {build} (AI-generated). Read it in "
                                "{path:act_tools_proposals}.",
        "chat.notice.grant_used": "{who} ran '{title}' under your standing approval ({covers}).",
        "chat.notice.grant_used_plain": "{who} ran '{title}' under your standing approval.",
        # the same notices under the app's own bubble (its name is the bubble's header)
        "chat.notice.proposal_by": "Proposed a fix for the build {build} (AI-generated). Read it in "
                                   "{path:act_tools_proposals}.",
        "chat.notice.grant_used_by": "Ran '{title}' under your standing approval ({covers}).",
        "chat.notice.grant_used_by_plain": "Ran '{title}' under your standing approval.",

        # questions that wait (from the pending approvals, never from feed rows alone)
        "chat.pending.header": "{who} asks:",
        "chat.pending.title": "'{title}' waits for your decision.",
        "chat.pending.grant_request": "Asks you to stop asking for '{title}'.",
        "chat.pending.left": "{minutes} min left",
        "chat.pending.open": "Open in the approvals window…",
        "chat.pending.announce": "A question waits for you: {title}",

        # records of what AIs did (until their own bubbles exist)
        "chat.record.decision": "{who} decided: {answer}",
        "chat.record.weighed": "Warnings it weighed for you: {warnings}",
        "chat.record.left": "Left for you: {warnings}",
        "chat.kind.approval": "A question was asked.",
        "chat.kind.approval_end": "A question ended.",
        "chat.kind.grant_used": "A standing approval was used.",
        "chat.kind.job_result": "Work an AI started has ended.",
        "chat.kind.blocked": "Work an AI could not do.",
        "chat.kind.person_action": "You acted on it.",
        "chat.kind.explanation": "An explanation.",
        "chat.kind.relay_question": "Your question to the app that decided.",
        "chat.kind.client_reply": "The app's answer.",
        "chat.kind.notice": "A notice.",
        "chat.kind.ditto_answer": "An answer.",

        # "Clear chat"
        "chat.clear.title": "Clear chat?",
        "chat.clear.intro": "This deletes the following for good:",
        "chat.clear.history_name": "Ditto's conversation",
        "chat.clear.history_what": "what Ditto remembers of your chat: it goes to the AI with your next question. "
                                   "The file stays, empty.",
        "chat.clear.feed_name": "Chat messages ({rows})",
        "chat.clear.feed_stays": "Only these messages are deleted; the file stays.",
        "chat.clear.path": "Full path: {path}",
        "chat.clear.file": "File name: {name}",
        "chat.clear.size": "Size: {size}",
        "chat.clear.what_label": "What it is: {what}",
        "chat.clear.hidden.one": "1 record of what AI apps did is not deleted: it is only hidden from the panel and "
                                 "stays in the chat file.",
        "chat.clear.hidden.many": "{n} records of what AI apps did are not deleted: they are only hidden from the "
                                  "panel and stay in the chat file.",
        "chat.clear.no": "No, keep everything",
        "chat.clear.yes": "Yes, delete",
        "chat.clear.empty": "The chat is empty; there is nothing to delete.",
        "chat.clear.failed": "Nothing was deleted: {reason}",
        "chat.clear.changed": "The chat changed while you were asked; nothing was deleted. Look at the list again.",
        "chat.clear.history_failed": "The chat messages were deleted, but Ditto's conversation could not be cleared "
                                     "({reason}).",
        "chat.clear.no_audit": "the audit log is not available",
        "chat.clear.history_unlisted": "this window holds a conversation with Ditto that its file chat_history.json "
                                       "does not show, so it cannot be listed. Restart the program and try again",
        "chat.clear.history_changed": "it changed while you were asked; look at the list again",
        "chat.size.bytes": "{n} bytes",
        "chat.size.kb": "{kb} KB ({n} bytes)",
        "chat.size.mb": "{mb} MB ({n} bytes)",

        # Tools > Clear Input History (the same list as "Clear chat")
        "chat.clear.input_history.name": "Input history",
        "chat.clear.input_history.what": "the recently used models, targets, boards, URLs, devices and datasets the "
                                         "input fields offer. The file is removed.",
        "chat.clear.input_history.damaged_name": "Copy of a damaged input history",
        "chat.clear.input_history.damaged_what": "the input history as it was when it could no longer be read, kept "
                                                 "for you. The file is removed.",
        "chat.clear.input_history.empty": "The input history is empty; there is nothing to delete.",
        "chat.clear.input_history.changed": "The input history changed while you were asked; nothing was deleted. "
                                            "Look at the list again.",

        # Ditto is optional and experimental (eighth round, Q-2): the badge, and the notice shown once per person
        "chat.ditto.badge": "Experimental",
        "chat.ditto.badge_tip": "Ditto is experimental and optional. Use at your own risk and responsibility. The "
                                "creator of LLMCF is not liable.",
        "chat.ditto.notice.title": "Ditto is experimental",
        "chat.ditto.notice.text": "Ditto, the AI assistant, is experimental and optional. Its answers and proposals "
                                  "can be wrong. What it does still goes through your tool permissions and "
                                  "confirmations, and the rest of the framework works without it.\n\nUse at your own "
                                  "risk and responsibility. The creator of LLMCF is not liable.\n\nThis repeats the "
                                  "no-warranty terms of the licence (LICENSE.txt) and adds nothing to them.",
        "chat.ditto.notice.ok": "Understood",
        "chat.ditto.setup": "Experimental: Ditto is optional. Use at your own risk and responsibility. The creator of "
                            "LLMCF is not liable.",
    },
    "de": {
        "chat.panel.title": "KI-Chat",
        "chat.panel.heading": "Ditto und deine KI-Apps",
        "chat.panel.access_tip": "Anbieter und Anmeldung, die Ditto nutzt ({path:act_ditto_settings})",
        "chat.panel.thinking": "Ditto denkt nach…",
        "chat.menu.button": "Mehr",
        "chat.menu.clear": "Chat leeren…",
        "chat.menu.approvals": "Fenster der ausstehenden Freigaben…",
        "chat.menu.permissions": "Werkzeugrechte und Vollmachten…",
        "chat.menu.auto_show": "Chat zeigen, wenn eine Frage wartet",
        "chat.filter.label": "Zeigen:",
        "chat.filter.all": "Alles",
        "chat.filter.needs_you": "Braucht dich ({n})",
        "chat.filter.decisions": "Entscheidungen",
        "chat.filter.ditto": "Chat mit Ditto",
        "chat.clear.button": "Chat leeren…",
        "chat.clear.tip": "Löscht dein Gespräch mit Ditto nach einer Frage, die genau aufzählt, was gelöscht wird",

        "chat.view.menu": "Ansicht",
        "chat.view.toggle": "KI-Chat-Bereich",
        "chat.view.focus": "Im Chat schreiben",
        "chat.toolbar.button": "KI-Chat",
        "chat.toolbar.button_count": "KI-Chat ({n})",
        "chat.toolbar.tip": "Zeigt oder verbirgt den Chat rechts (F9). Die Zahl zählt neue Nachrichten und Fragen, "
                            "die auf dich warten.",

        "chat.composer.placeholder": "Frag Ditto nach Targets, Builds oder Kompilierfehlern… (Enter sendet, "
                                     "Umschalt+Enter fügt eine Zeile ein)",
        "chat.composer.send": "Senden",
        "chat.composer.to": "An: Ditto",
        "chat.composer.about": "Zu: {what}",
        "chat.composer.remove_about": "Entfernen",
        "chat.composer.remove_about_tip": "Entfernt den Bezug (Escape)",
        "chat.composer.input_name": "Deine Nachricht an Ditto",
        "chat.composer.no_ditto": "Ditto ist nicht verfügbar: der KI-Assistent ist nicht gestartet (siehe Protokoll).",
        "chat.composer.not_set_up": "Ditto kann noch nicht antworten: richte es ein unter {path:act_ditto_setup}",

        "chat.view.earlier": "Frühere Nachrichten zeigen",
        "chat.view.new_messages": "Neue Nachrichten ↓",
        "chat.view.since": "Neu, seit du zuletzt geschaut hast",
        "chat.view.empty": "Noch nichts da. Frag Ditto etwas; was KI-Apps für dich tun, erscheint auch hier.",
        "chat.view.needs_you": "Braucht dich",
        "chat.view.not_stored": "Eine Nachricht wurde angezeigt, aber nicht im Chat gespeichert ({reason}).",
        "chat.view.feed_problem": "Die Chat-Datei ist nicht nutzbar ({reason}); Nachrichten werden angezeigt, aber "
                                  "nicht behalten.",

        "chat.away.since": "Seit {time}: {parts}",
        "chat.away.answers.one": "1 Antwort von Ditto",
        "chat.away.answers.many": "{n} Antworten von Ditto",
        "chat.away.decisions.one": "1 Entscheidung einer KI-App",
        "chat.away.decisions.many": "{n} Entscheidungen von KI-Apps",
        "chat.away.results.one": "1 Ergebnis",
        "chat.away.results.many": "{n} Ergebnisse",
        "chat.away.failed": "{n} fehlgeschlagen",
        "chat.away.blocked.one": "1 Arbeit, die eine KI nicht erledigen konnte",
        "chat.away.blocked.many": "{n} Arbeiten, die KI-Apps nicht erledigen konnten",
        "chat.away.notices.one": "1 Hinweis",
        "chat.away.notices.many": "{n} Hinweise",
        "chat.away.needs.one": "1 Sache braucht dich",
        "chat.away.needs.many": "{n} Sachen brauchen dich",
        "chat.away.nothing": "nichts Neues",

        "chat.sender.you": "Du",
        "chat.sender.ditto": "Ditto",
        "chat.sender.framework": "Das Framework",
        "chat.sender.reserved": "„{name}“ (nennt sich so; nicht der Ditto des Frameworks)",
        "chat.sender.accessible": "{sender}, {time}",
        "chat.bubble.warning": "Warnung:",
        "chat.bubble.written_by_framework": "vom Framework geschrieben",
        "chat.bubble.original_words": "Ursprüngliche Worte zeigen",
        "chat.bubble.speaking_names": "Namen zeigen",
        "chat.bubble.show_all": "Alles zeigen ({n} Zeilen)",
        "chat.bubble.full_text_title": "Ganzer Text",
        "chat.bubble.copy": "Kopieren",
        "chat.bubble.select_all": "Alles auswählen",
        "chat.bubble.open_address": "Adresse öffnen…",
        "chat.bubble.open_address_title": "Adresse öffnen",
        "chat.bubble.open_address_question": "Diese Adresse im Browser öffnen?\n\n{url}\n\nSie stammt aus einer "
                                             "Chat-Nachricht; das Framework hat sie nicht geprüft.",
        "chat.bubble.legend": "Namen der Kennungen im Code:",
        "chat.bubble.local": "Nur in diesem Fenster angezeigt",
        "chat.group.title": "{who} hat {n} Dinge erledigt",
        "chat.group.show": "Zeigen",
        "chat.group.hide": "Verbergen",

        "chat.tool.used": "Werkzeug benutzt: {title}",
        "chat.tool.not_run": "Werkzeug nicht ausgeführt: {title} ({detail})",
        "chat.notice.new_conversation": "Das Gespräch wurde lang: Ditto hat es zusammengefasst und macht in einem "
                                        "neuen Gespräch weiter.",
        "chat.notice.source_local": "Ditto nutzt jetzt seine eigenen Werkzeuge: ein neues Gespräch.",
        # LEGACY (see the English block): stored rows of the removed remote mode still render.
        "chat.notice.source_remote": "Ditto nutzt jetzt die Werkzeuge des Frameworks auf {name}: ein neues Gespräch.",
        "chat.tool.used_on": "Benutzt auf {place}: {title}",
        "chat.tool.not_run_on": "Nicht ausgeführt auf {place}: {title} ({detail})",
        "chat.notice.other_computer": "Zum anderen Computer: {notice}",
        "chat.notice.source_hybrid": "Ditto arbeitet jetzt mit diesem Computer und {name} zusammen: ein neues "
                                     "Gespräch.",
        "chat.notice.source_hybrid_unnamed": "Ditto arbeitet jetzt mit diesem Computer und dem anderen Computer "
                                             "zusammen: ein neues Gespräch.",
        # (end of the legacy keys)
        "chat.notice.error": "Ditto konnte nicht antworten: {error}",
        "chat.notice.proposal": "{who} hat eine Korrektur für den Build {build} vorgeschlagen (KI-erzeugt). Lies sie "
                                "unter {path:act_tools_proposals}.",
        "chat.notice.grant_used": "{who} hat „{title}“ mit deiner Dauerfreigabe ausgeführt ({covers}).",
        "chat.notice.grant_used_plain": "{who} hat „{title}“ mit deiner Dauerfreigabe ausgeführt.",
        "chat.notice.proposal_by": "Hat eine Korrektur für den Build {build} vorgeschlagen (KI-erzeugt). Lies sie "
                                   "unter {path:act_tools_proposals}.",
        "chat.notice.grant_used_by": "Hat „{title}“ mit deiner Dauerfreigabe ausgeführt ({covers}).",
        "chat.notice.grant_used_by_plain": "Hat „{title}“ mit deiner Dauerfreigabe ausgeführt.",

        "chat.pending.header": "{who} fragt:",
        "chat.pending.title": "„{title}“ wartet auf deine Entscheidung.",
        "chat.pending.grant_request": "Bittet dich, bei „{title}“ nicht mehr zu fragen.",
        "chat.pending.left": "noch {minutes} Min.",
        "chat.pending.open": "Im Freigabe-Fenster öffnen…",
        "chat.pending.announce": "Eine Frage wartet auf dich: {title}",

        "chat.record.decision": "{who} hat entschieden: {answer}",
        "chat.record.weighed": "Hinweise, die es für dich abgewogen hat: {warnings}",
        "chat.record.left": "Für dich übrig gelassen: {warnings}",
        "chat.kind.approval": "Eine Frage wurde gestellt.",
        "chat.kind.approval_end": "Eine Frage ist beendet.",
        "chat.kind.grant_used": "Eine Dauerfreigabe wurde genutzt.",
        "chat.kind.job_result": "Eine Arbeit, die eine KI gestartet hat, ist zu Ende.",
        "chat.kind.blocked": "Eine Arbeit, die eine KI nicht erledigen konnte.",
        "chat.kind.person_action": "Du hast darauf reagiert.",
        "chat.kind.explanation": "Eine Erklärung.",
        "chat.kind.relay_question": "Deine Frage an die App, die entschieden hat.",
        "chat.kind.client_reply": "Die Antwort der App.",
        "chat.kind.notice": "Ein Hinweis.",
        "chat.kind.ditto_answer": "Eine Antwort.",

        "chat.clear.title": "Chat leeren?",
        "chat.clear.intro": "Das löscht Folgendes endgültig:",
        "chat.clear.history_name": "Dittos Gespräch",
        "chat.clear.history_what": "was Ditto von eurem Chat behält: es geht mit deiner nächsten Frage an die KI. Die "
                                   "Datei bleibt, leer.",
        "chat.clear.feed_name": "Chat-Nachrichten ({rows})",
        "chat.clear.feed_stays": "Nur diese Nachrichten werden gelöscht; die Datei bleibt.",
        "chat.clear.path": "Vollständiger Pfad: {path}",
        "chat.clear.file": "Dateiname: {name}",
        "chat.clear.size": "Größe: {size}",
        "chat.clear.what_label": "Was es ist: {what}",
        "chat.clear.hidden.one": "1 Eintrag darüber, was KI-Apps getan haben, wird nicht gelöscht: er wird nur im "
                                 "Chat-Bereich ausgeblendet und bleibt in der Chat-Datei.",
        "chat.clear.hidden.many": "{n} Einträge darüber, was KI-Apps getan haben, werden nicht gelöscht: sie werden "
                                  "nur im Chat-Bereich ausgeblendet und bleiben in der Chat-Datei.",
        "chat.clear.no": "Nein, alles behalten",
        "chat.clear.yes": "Ja, löschen",
        "chat.clear.empty": "Der Chat ist leer; es gibt nichts zu löschen.",
        "chat.clear.failed": "Nichts wurde gelöscht: {reason}",
        "chat.clear.changed": "Der Chat hat sich geändert, während du gefragt wurdest; nichts wurde gelöscht. Sieh dir "
                              "die Liste noch einmal an.",
        "chat.clear.history_failed": "Die Chat-Nachrichten wurden gelöscht, aber Dittos Gespräch konnte nicht geleert "
                                     "werden ({reason}).",
        "chat.clear.no_audit": "das Prüfprotokoll ist nicht verfügbar",
        "chat.clear.history_unlisted": "dieses Fenster hält ein Gespräch mit Ditto, das seine Datei chat_history.json "
                                       "nicht zeigt, also kann es nicht aufgezählt werden. Starte das Programm neu "
                                       "und versuche es noch einmal",
        "chat.clear.history_changed": "es hat sich geändert, während du gefragt wurdest; sieh dir die Liste noch "
                                      "einmal an",
        "chat.size.bytes": "{n} Bytes",
        "chat.size.kb": "{kb} KB ({n} Bytes)",
        "chat.size.mb": "{mb} MB ({n} Bytes)",

        "chat.clear.input_history.name": "Eingabeverlauf",
        "chat.clear.input_history.what": "die zuletzt verwendeten Modelle, Targets, Boards, URLs, Geräte und "
                                         "Datensätze, die die Eingabefelder anbieten. Die Datei wird entfernt.",
        "chat.clear.input_history.damaged_name": "Kopie eines beschädigten Eingabeverlaufs",
        "chat.clear.input_history.damaged_what": "der Eingabeverlauf, wie er war, als er sich nicht mehr lesen ließ, "
                                                 "für dich aufbewahrt. Die Datei wird entfernt.",
        "chat.clear.input_history.empty": "Der Eingabeverlauf ist leer; es gibt nichts zu löschen.",
        "chat.clear.input_history.changed": "Der Eingabeverlauf hat sich geändert, während du gefragt wurdest; nichts "
                                            "wurde gelöscht. Sieh dir die Liste noch einmal an.",

        "chat.ditto.badge": "Experimentell",
        "chat.ditto.badge_tip": "Ditto ist experimentell und optional. Nutzung auf eigene Gefahr und Verantwortung. "
                                "Der Ersteller von LLMCF haftet nicht.",
        "chat.ditto.notice.title": "Ditto ist experimentell",
        "chat.ditto.notice.text": "Ditto, der KI-Assistent, ist experimentell und optional. Seine Antworten und "
                                  "Vorschläge können falsch sein. Was er tut, läuft weiter über deine "
                                  "Werkzeugrechte und Bestätigungen, und der Rest des Frameworks funktioniert ohne "
                                  "ihn.\n\nNutzung auf eigene Gefahr und Verantwortung. Der Ersteller von LLMCF "
                                  "haftet nicht.\n\nDas wiederholt den Gewährleistungsausschluss der Lizenz "
                                  "(LICENSE.txt) und fügt ihm nichts hinzu.",
        "chat.ditto.notice.ok": "Verstanden",
        "chat.ditto.setup": "Experimentell: Ditto ist optional. Nutzung auf eigene Gefahr und Verantwortung. Der "
                            "Ersteller von LLMCF haftet nicht.",
    },
}
