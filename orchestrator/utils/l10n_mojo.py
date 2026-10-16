#!/usr/bin/env python3
"""
Texts of Mojo in the GUI (W1' of 2026-10-16: every CLI action also by mouse): the Mojo window (Tools > Mojo Health
Check...: Mojo Oracle's findings, Mojo Miracle, the boards a recorded fact points to and the check of one board) and
the Deployment tab's "Check this board" button, English and German. LocalizationManager merges them
(localization.TEXT_MODULES); a key that exists already is refused there, so these never replace another text.

What Mojo found (a finding's message, hint, where and why, a plan's summary, the summary sentences) comes from Mojo's
checks in English, as `llmcf mojo` prints it; these texts are the window around it. The English texts of the Miracle
lines and of the question are the CLI's words (Core/mojo/views.MIRACLE_LINES, "Run Mojo Miracle now?"). Every key has
the same {placeholders} in both languages (tests/test_mojo_window.py). German addresses the person with "du", as the
other German texts do. This module imports nothing from the framework.
"""

TEXTS = {
    "en": {
        # the menu entry and the window
        "mojo.menu": "Mojo Health Check...",
        "mojo.title": "Mojo Health Check",
        "mojo.intro": "Mojo Oracle checks the installation: it changes nothing and contacts no board. Mojo Miracle "
                      "repairs only after you said yes, and a board is checked only when you click its button.",
        "mojo.running": "Mojo Oracle is checking the installation (read-only, nothing is changed)...",
        "mojo.failed": "Mojo could not run: {reason}",
        "mojo.col.severity": "Severity",
        "mojo.col.area": "Area",
        "mojo.col.finding": "Finding",
        "mojo.sev.ok": "OK",
        "mojo.sev.info": "Information",
        "mojo.sev.warn": "Warning",
        "mojo.sev.error": "Error",
        "mojo.where": "Where",
        "mojo.why": "Why it matters",
        "mojo.hint": "Next step",
        "mojo.can": "Mojo Miracle",
        "mojo.plan": "Plan",
        "mojo.plan.step": "{target}{path}: {before} → {after}",
        "mojo.plan.path": " ({path})",
        "mojo.plan.undoable": ", can be undone",
        "mojo.board": "Board",
        "mojo.board.offer": "Mojo did not contact the board {label}.",
        # what Mojo Miracle can do (the CLI's words, Core/mojo/views.MIRACLE_LINES)
        "mojo.can.repair": "Mojo Miracle can repair it: {summary}.",
        "mojo.can.list": "Mojo Miracle shows you the framework's build images to choose from (name, id, size, what "
                         "each is; nothing is ticked); only what you tick and confirm is deleted.",
        "mojo.can.list_elsewhere": "Mojo Miracle cannot repair it here: {reason}; {where}.",
        "mojo.can.other_list": "Mojo Miracle cannot repair it: {reason}; you choose what is deleted or exported: "
                               "{where}.",
        "mojo.can.cannot": "Mojo Miracle cannot repair it: {reason}.",
        # the overall line (the exit code of `llmcf mojo`)
        "mojo.overall.0": "Overall: no problems (exit code 0).",
        "mojo.overall.1": "Overall: warnings, no errors (exit code 1).",
        "mojo.overall.2": "Overall: errors (exit code 2).",
        # buttons
        "mojo.check_again": "Check again",
        "mojo.miracle.button": "Run Mojo Miracle...",
        "mojo.miracle.tip": "Repairs what Mojo Miracle can repair (exactly the plans shown) and opens your list of "
                            "the framework's build images where a finding needs it. You are asked first.",
        "mojo.miracle.nothing": "Mojo Miracle has nothing to act on: no problem it can repair and no list to open.",
        # Mojo Miracle
        "mojo.miracle.ask": "Run Mojo Miracle now?",
        "mojo.miracle.ask_detail": "It applies exactly the plans shown in the window; a problem that changed "
                                   "meanwhile is not repaired. Where a finding needs it, it shows you the list of the "
                                   "framework's build images (nothing is ticked); only what you tick and confirm is "
                                   "deleted.",
        "mojo.miracle.not_run": "Mojo Miracle did not run; nothing was changed.",
        "mojo.miracle.running": "Mojo Miracle is repairing what can be repaired...",
        "mojo.miracle.heading": "Mojo Miracle: what was repaired, what was not and why",
        "mojo.col.outcome": "Outcome",
        "mojo.col.problem": "Problem",
        "mojo.col.what": "What happened",
        "mojo.outcome.repaired": "Repaired",
        "mojo.outcome.already_fixed": "Already gone",
        "mojo.outcome.still_present": "Still there",
        "mojo.outcome.declined": "Not deleted",
        "mojo.outcome.not_repaired": "Not repaired",
        "mojo.outcome.new": "New after Mojo Miracle",
        "mojo.result.repaired": "{summary}. Checked again: the problem is gone.",
        "mojo.result.again": "Click 'Check again': it shows the new plan.",
        "mojo.result.images": "Your list of the framework's build images",
        # boards (W1: only on request, or when a recorded fact points to a problem)
        "mojo.boards.title": "Boards",
        "mojo.boards.none": "No recorded fact points to a problem with a board, so Mojo contacted none.",
        "mojo.boards.check": "Check {label} now",
        "mojo.boards.check_tip": "One connection to its SSH port, no login. Nothing is changed.",
        "mojo.boards.pick": "Check a board...",
        "mojo.boards.pick_title": "Check a board",
        "mojo.boards.pick_text": "Which trusted board? Mojo makes one connection to its SSH port, no login.",
        "mojo.boards.pick_item": "{label} ({address})",
        "mojo.boards.no_trusted": "No board is trusted yet. A board becomes trusted when you deploy to it or probe "
                                  "it and compare its fingerprint.",
        "mojo.boards.checking": "Checking {label} (one connection to its SSH port, no login)...",
        "mojo.boards.result": "{severity}: {message}",
        "mojo.boards.result_hint": "Next step: {hint}",
        "mojo.boards.gone": "{label} is no longer a trusted board; nothing was contacted.",
        "mojo.boards.failed": "The check of {label} could not run: {reason}",
        # the Deployment tab's "Benchmark on the board" group
        "mojo.deploy.check": "Check this board",
        "mojo.deploy.check_tip": "Checks the chosen board now: one connection to its SSH port, no login. Nothing is "
                                 "changed.",
        "mojo.deploy.choose": "Choose a board first.",
    },
    "de": {
        # der Menüeintrag und das Fenster
        "mojo.menu": "Mojo-Gesundheitscheck...",
        "mojo.title": "Mojo-Gesundheitscheck",
        "mojo.intro": "Mojo Oracle prüft die Installation: es ändert nichts und kontaktiert kein Board. Mojo Miracle "
                      "repariert erst nach deinem Ja, und ein Board wird nur geprüft, wenn du seinen Knopf klickst.",
        "mojo.running": "Mojo Oracle prüft die Installation (nur lesend, nichts wird geändert)...",
        "mojo.failed": "Mojo konnte nicht laufen: {reason}",
        "mojo.col.severity": "Schwere",
        "mojo.col.area": "Bereich",
        "mojo.col.finding": "Befund",
        "mojo.sev.ok": "OK",
        "mojo.sev.info": "Hinweis",
        "mojo.sev.warn": "Warnung",
        "mojo.sev.error": "Fehler",
        "mojo.where": "Wo",
        "mojo.why": "Warum es wichtig ist",
        "mojo.hint": "Nächster Schritt",
        "mojo.can": "Mojo Miracle",
        "mojo.plan": "Plan",
        "mojo.plan.step": "{target}{path}: {before} → {after}",
        "mojo.plan.path": " ({path})",
        "mojo.plan.undoable": ", lässt sich rückgängig machen",
        "mojo.board": "Board",
        "mojo.board.offer": "Mojo hat das Board {label} nicht kontaktiert.",
        # was Mojo Miracle tun kann
        "mojo.can.repair": "Mojo Miracle kann es reparieren: {summary}.",
        "mojo.can.list": "Mojo Miracle zeigt dir die Build-Images des Frameworks zur Auswahl (Name, ID, Größe, was "
                         "es ist; nichts ist angehakt); gelöscht wird nur, was du anhakst und bestätigst.",
        "mojo.can.list_elsewhere": "Mojo Miracle kann es hier nicht reparieren: {reason}; {where}.",
        "mojo.can.other_list": "Mojo Miracle kann es nicht reparieren: {reason}; du wählst, was gelöscht oder "
                               "exportiert wird: {where}.",
        "mojo.can.cannot": "Mojo Miracle kann es nicht reparieren: {reason}.",
        # die Gesamtzeile (der Exit-Code von `llmcf mojo`)
        "mojo.overall.0": "Gesamt: keine Probleme (Exit-Code 0).",
        "mojo.overall.1": "Gesamt: Warnungen, keine Fehler (Exit-Code 1).",
        "mojo.overall.2": "Gesamt: Fehler (Exit-Code 2).",
        # Knöpfe
        "mojo.check_again": "Erneut prüfen",
        "mojo.miracle.button": "Mojo Miracle ausführen...",
        "mojo.miracle.tip": "Repariert, was Mojo Miracle reparieren kann (genau die gezeigten Pläne), und öffnet "
                            "deine Liste der Build-Images des Frameworks, wo ein Befund sie braucht. Du wirst vorher "
                            "gefragt.",
        "mojo.miracle.nothing": "Mojo Miracle hat nichts zu tun: kein Problem, das es reparieren kann, und keine "
                                "Liste zu öffnen.",
        # Mojo Miracle
        "mojo.miracle.ask": "Mojo Miracle jetzt ausführen?",
        "mojo.miracle.ask_detail": "Es wendet genau die im Fenster gezeigten Pläne an; ein Problem, das sich "
                                   "inzwischen geändert hat, wird nicht repariert. Wo ein Befund sie braucht, zeigt "
                                   "es dir die Liste der Build-Images des Frameworks (nichts ist angehakt); gelöscht "
                                   "wird nur, was du anhakst und bestätigst.",
        "mojo.miracle.not_run": "Mojo Miracle ist nicht gelaufen; nichts wurde geändert.",
        "mojo.miracle.running": "Mojo Miracle repariert, was sich reparieren lässt...",
        "mojo.miracle.heading": "Mojo Miracle: was repariert wurde, was nicht und warum",
        "mojo.col.outcome": "Ergebnis",
        "mojo.col.problem": "Problem",
        "mojo.col.what": "Was passiert ist",
        "mojo.outcome.repaired": "Repariert",
        "mojo.outcome.already_fixed": "Schon weg",
        "mojo.outcome.still_present": "Noch da",
        "mojo.outcome.declined": "Nicht gelöscht",
        "mojo.outcome.not_repaired": "Nicht repariert",
        "mojo.outcome.new": "Neu nach Mojo Miracle",
        "mojo.result.repaired": "{summary}. Erneut geprüft: das Problem ist weg.",
        "mojo.result.again": "Klicke auf 'Erneut prüfen': es zeigt den neuen Plan.",
        "mojo.result.images": "Deine Liste der Build-Images des Frameworks",
        # Boards (W1: nur auf Wunsch oder wenn ein aufgezeichneter Fakt auf ein Problem zeigt)
        "mojo.boards.title": "Boards",
        "mojo.boards.none": "Kein aufgezeichneter Fakt zeigt auf ein Problem mit einem Board, also hat Mojo keines "
                            "kontaktiert.",
        "mojo.boards.check": "{label} jetzt prüfen",
        "mojo.boards.check_tip": "Eine Verbindung zu seinem SSH-Port, keine Anmeldung. Nichts wird geändert.",
        "mojo.boards.pick": "Ein Board prüfen...",
        "mojo.boards.pick_title": "Ein Board prüfen",
        "mojo.boards.pick_text": "Welches vertraute Board? Mojo baut eine Verbindung zu seinem SSH-Port auf, ohne "
                                 "Anmeldung.",
        "mojo.boards.pick_item": "{label} ({address})",
        "mojo.boards.no_trusted": "Noch keinem Board wird vertraut. Einem Board wird vertraut, wenn du darauf "
                                  "deployst oder es abfragst und seinen Fingerabdruck vergleichst.",
        "mojo.boards.checking": "Prüfe {label} (eine Verbindung zu seinem SSH-Port, keine Anmeldung)...",
        "mojo.boards.result": "{severity}: {message}",
        "mojo.boards.result_hint": "Nächster Schritt: {hint}",
        "mojo.boards.gone": "{label} ist kein vertrautes Board mehr; nichts wurde kontaktiert.",
        "mojo.boards.failed": "Die Prüfung von {label} konnte nicht laufen: {reason}",
        # die Gruppe "Benchmark auf dem Board" im Deployment-Tab
        "mojo.deploy.check": "Dieses Board prüfen",
        "mojo.deploy.check_tip": "Prüft das gewählte Board jetzt: eine Verbindung zu seinem SSH-Port, keine "
                                 "Anmeldung. Nichts wird geändert.",
        "mojo.deploy.choose": "Wähle zuerst ein Board.",
    },
}
