#!/usr/bin/env python3
"""
Texts of Boards > Board harness... (orchestrator/gui/harness_window.py: `llmcf bundle harness` by mouse), English and
German. LocalizationManager merges them (localization.TEXT_MODULES); a key that exists already is refused there.

Every key has the same {placeholders} in both languages (tests/test_harness_window.py). German terms: "Harness" (the
person's agent harness, a name kept), "Probelauf" (dry run), "Board", "Bündel" (bundle). This module imports nothing
from the framework.
"""

TEXTS = {
    "en": {
        "boards.menu.harness": "Board harness...",
        "harness.title": "Board harness",
        "harness.intro": ("Your own agent harness on a board: take it in at a pin, put it into a bundle (block "
                          "harness-agent), then run its own installer there as root through llmcf-install. Its "
                          "questions come to you here, one by one; a dropped connection never stops it, Re-attach "
                          "reads on. The framework does not block it: it watches and reports before and after."),
        "harness.bundle": "Bundle and harness:",
        "harness.none": "No stored bundle has a harness yet: take one in, then build a bundle with the block "
                        "harness-agent in the Board bundle window.",
        "harness.runs": "Runs of this harness (kept on this computer):",
        "harness.output": "The installer's output (live):",
        "harness.report_label": "Report:",
        "harness.assume_yes": "Use the harness's own assume-yes switch (only when the bundle carries it)",
        "harness.skip_dry": "Run without the dry run before",
        "harness.hidden": "Hide what I type",
        "harness.answer_ph": "Your answer to the installer's question, then Send",
        "harness.btn.take_in": "Take in a harness...",
        "harness.btn.refresh": "Refresh",
        "harness.btn.dry_run": "Dry run...",
        "harness.btn.run": "Run...",
        "harness.btn.attach": "Re-attach",
        "harness.btn.stop": "Stop...",
        "harness.btn.report": "Report",
        "harness.btn.send": "Send",
        "harness.btn.cancel": "Detach",
        "harness.ask.dry": ("Start the dry run of harness {harness} on board {board} (its own {args}), as root through "
                            "llmcf-install? A dry run should change nothing; the framework watches it all the same."),
        "harness.ask.run": ("Run the installer of harness {harness} on board {board} now, as root through "
                            "llmcf-install? It may install packages, add accounts and services and fetch what it "
                            "needs; the framework does not block it, it watches and reports what it did. Its questions come to "
                            "you one by one. The responsibility for what it does is yours."),
        "harness.ask.stop": "Stop the installer of harness {harness} on board {board} now? What it did so far stays.",
        "harness.no_dry": ("Harness {harness} has no dry run of its own. The bundle says: installer {installer}, tree "
                           "{tree}."),
        "harness.started": "Started run {run} on the board.",
        "harness.ended": "The installer ended (exit code {code}).",
        "harness.answered": "The answer went to the installer.",
        "harness.answer_failed": "The answer did not reach the installer: {reason}",
        "harness.stopped": "Stopped. The watch records the end; Report shows what it did.",
        "harness.detached": "Detached; the installer goes on on the board. Re-attach reads on from here.",
        "harness.dropped": "{text}",
        "harness.running": "running harness {mode} on the board",
        "harness.reading": "reading the report from the board",
        "harness.need_bundle": "Choose a bundle with a harness first.",
        "harness.need_run": "Choose a run first.",
        "harness.failed": "{reason}",
        "harness.take.title": "Take in a harness",
        "harness.take.id": "Id (lower-case letters, digits, '-'):",
        "harness.take.kind": "Where it comes from:",
        "harness.take.location": "Repository link (https), archive file or folder:",
        "harness.take.installer": "Installer inside the harness (e.g. installer/install.sh):",
        "harness.take.ref": "Branch or tag (git only):",
        "harness.take.pin": "Pin: the commit (git) or the archive's sha256 (optional):",
        "harness.take.dry_args": "Options of its own dry run (space-separated, e.g. --dry-run):",
        "harness.take.assume_yes": "Its own assume-yes switch NAME=value (optional):",
        "harness.take.exit_codes": "What its exit codes mean (e.g. 0=done 1=findings):",
        "harness.take.title_field": "Title:",
        "harness.take.done": ("Took in harness {id} at {pin}: {files} files, tree checksum {tree}. Build a bundle with "
                              "the block harness-agent and harness={id} in the Board bundle window."),
        "harness.take.bad_codes": "Exit codes are written CODE=done or CODE=findings, separated by spaces.",
        "harness.ready.checking": "reading the board's root readiness",
        "harness.ready.running": "setting up the board's root readiness",
        "harness.ready.missing": "The board's root readiness for llmcf-install is missing. Its setup does this:",
        "harness.ready.ask": ("Set up the root readiness of board {board} for bundle {bundle} now, as listed above? The "
                              "harness runs only through it. It changes only what is missing, as root once; nothing "
                              "else on the board is changed."),
        "harness.ready.there": "The board's root readiness is there already; nothing was changed.",
        "harness.ready.after": "The root readiness is there now; the harness step goes on.",
        "harness.ready.declined": ("Nothing was set up; the harness cannot run on board {board} until its root "
                                   "readiness is there."),
        "harness.ready.cannot": "The board's root readiness cannot be set up here: {reason}",
        "harness.ready.failed": "{reason}",
    },
    "de": {
        "boards.menu.harness": "Board-Harness...",
        "harness.title": "Board-Harness",
        "harness.intro": ("Dein eigener Agent-Harness auf einem Board: an einem Pin übernehmen, in ein Bündel legen "
                          "(Baustein harness-agent), dann seinen eigenen Installer dort als root über llmcf-install "
                          "ausführen. Seine Fragen kommen hier zu dir, eine nach der anderen; eine abgebrochene "
                          "Verbindung stoppt ihn nie, Neu verbinden liest weiter. Das Framework blockiert ihn nicht: es "
                          "beobachtet und berichtet vorher und nachher."),
        "harness.bundle": "Bündel und Harness:",
        "harness.none": "Noch kein gespeichertes Bündel hat einen Harness: übernimm einen und baue dann im "
                        "Fenster Board-Bündel ein Bündel mit dem Baustein harness-agent.",
        "harness.runs": "Läufe dieses Harness (auf diesem Computer gemerkt):",
        "harness.output": "Ausgabe des Installers (live):",
        "harness.report_label": "Bericht:",
        "harness.assume_yes": "Den eigenen Assume-Yes-Schalter des Harness nutzen (nur wenn das Bündel ihn trägt)",
        "harness.skip_dry": "Ohne Probelauf vorher ausführen",
        "harness.hidden": "Eingabe verbergen",
        "harness.answer_ph": "Deine Antwort auf die Frage des Installers, dann Senden",
        "harness.btn.take_in": "Harness übernehmen...",
        "harness.btn.refresh": "Aktualisieren",
        "harness.btn.dry_run": "Probelauf...",
        "harness.btn.run": "Ausführen...",
        "harness.btn.attach": "Neu verbinden",
        "harness.btn.stop": "Stoppen...",
        "harness.btn.report": "Bericht",
        "harness.btn.send": "Senden",
        "harness.btn.cancel": "Trennen",
        "harness.ask.dry": ("Den Probelauf des Harness {harness} auf Board {board} starten (sein eigenes {args}), als "
                            "root über llmcf-install? Ein Probelauf sollte nichts ändern; das Framework beobachtet ihn "
                            "trotzdem."),
        "harness.ask.run": ("Den Installer des Harness {harness} auf Board {board} jetzt ausführen, als root über "
                            "llmcf-install? Er kann Pakete installieren, Konten und Dienste anlegen und holen, was er "
                            "braucht; das Framework blockiert ihn nicht, es beobachtet und berichtet, was er getan hat. Seine "
                            "Fragen kommen eine nach der anderen zu dir. Die Verantwortung für sein Tun liegt bei "
                            "dir."),
        "harness.ask.stop": ("Den Installer des Harness {harness} auf Board {board} jetzt stoppen? Was er bisher getan "
                             "hat, bleibt."),
        "harness.no_dry": ("Der Harness {harness} hat keinen eigenen Probelauf. Das Bündel sagt: Installer "
                           "{installer}, Baum {tree}."),
        "harness.started": "Lauf {run} auf dem Board gestartet.",
        "harness.ended": "Der Installer ist beendet (Rückgabewert {code}).",
        "harness.answered": "Die Antwort ist beim Installer.",
        "harness.answer_failed": "Die Antwort hat den Installer nicht erreicht: {reason}",
        "harness.stopped": "Gestoppt. Die Beobachtung hält das Ende fest; Bericht zeigt, was er getan hat.",
        "harness.detached": "Getrennt; der Installer läuft auf dem Board weiter. Neu verbinden liest ab hier weiter.",
        "harness.dropped": "{text}",
        "harness.running": "Harness-{mode} läuft auf dem Board",
        "harness.reading": "Bericht wird vom Board gelesen",
        "harness.need_bundle": "Wähle zuerst ein Bündel mit einem Harness.",
        "harness.need_run": "Wähle zuerst einen Lauf.",
        "harness.failed": "{reason}",
        "harness.take.title": "Harness übernehmen",
        "harness.take.id": "Kennung (Kleinbuchstaben, Ziffern, '-'):",
        "harness.take.kind": "Woher sie kommt:",
        "harness.take.location": "Repository-Link (https), Archivdatei oder Ordner:",
        "harness.take.installer": "Installer in der Harness (z. B. installer/install.sh):",
        "harness.take.ref": "Branch oder Tag (nur git):",
        "harness.take.pin": "Pin: der Commit (git) oder die sha256 des Archivs (optional):",
        "harness.take.dry_args": "Optionen seines eigenen Probelaufs (durch Leerzeichen getrennt, z. B. --dry-run):",
        "harness.take.assume_yes": "Sein eigener Assume-Yes-Schalter NAME=Wert (optional):",
        "harness.take.exit_codes": "Was seine Rückgabewerte bedeuten (z. B. 0=done 1=findings):",
        "harness.take.title_field": "Titel:",
        "harness.take.done": ("Harness {id} an {pin} übernommen: {files} Dateien, Baum-Prüfsumme {tree}. Baue im "
                              "Fenster Board-Bündel ein Bündel mit dem Baustein harness-agent und harness={id}."),
        "harness.take.bad_codes": "Rückgabewerte werden als CODE=done oder CODE=findings geschrieben, durch "
                                  "Leerzeichen getrennt.",
        "harness.ready.checking": "Root-Bereitschaft des Boards wird gelesen",
        "harness.ready.running": "Root-Bereitschaft des Boards wird eingerichtet",
        "harness.ready.missing": "Die Root-Bereitschaft des Boards für llmcf-install fehlt. Ihre Einrichtung tut dies:",
        "harness.ready.ask": ("Die Root-Bereitschaft von Board {board} für das Bündel {bundle} jetzt einrichten, wie "
                              "oben aufgeführt? Der Harness läuft nur über sie. Sie ändert nur, was fehlt, einmal als "
                              "root; sonst wird auf dem Board nichts geändert."),
        "harness.ready.there": "Die Root-Bereitschaft des Boards ist schon da; nichts wurde geändert.",
        "harness.ready.after": "Die Root-Bereitschaft ist jetzt da; der Harness-Schritt geht weiter.",
        "harness.ready.declined": ("Nichts wurde eingerichtet; der Harness kann auf Board {board} erst laufen, wenn "
                                   "seine Root-Bereitschaft da ist."),
        "harness.ready.cannot": "Die Root-Bereitschaft des Boards kann hier nicht eingerichtet werden: {reason}",
        "harness.ready.failed": "{reason}",
    },
}
