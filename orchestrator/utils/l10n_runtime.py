#!/usr/bin/env python3
"""
Texts of Ditto's runtime mode in the AI settings (bare metal or Docker, DECISIONS round 7 D), English and German.
LocalizationManager merges them (localization.TEXT_MODULES); a key that exists already is refused there.

Every key has the same {placeholders} in both languages (tests/test_runtime_mode.py). This module imports nothing from
the framework.
"""

TEXTS = {
    "en": {
        "runtime.mode.title": "Ditto's local server",
        "runtime.mode.intro": "Ditto's own model runs on this computer: bare metal (recommended, no Docker overhead) or "
                              "in Docker (an image built on this computer, never downloaded). Both use the same "
                              "models; a switch makes only the server again.",
        "runtime.mode.label": "Run it:",
        "runtime.mode.choice.bare-metal": "Bare metal (recommended)",
        "runtime.mode.choice.docker": "In Docker",
        "runtime.mode.now.bare-metal": "Now: bare metal ({what})",
        "runtime.mode.now.docker": "Now: in Docker ({what})",
        "runtime.mode.now.none": "Now: not installed",
        "runtime.mode.switch": "Switch…",
        "runtime.mode.install": "Install…",
        "runtime.mode.same": "Ditto's server runs {mode} already.",
        "runtime.mode.name.bare-metal": "bare metal",
        "runtime.mode.name.docker": "in Docker",
        "runtime.mode.question": "Ditto's server will run {mode}.\n\nThe models stay where they are and are not "
                                 "downloaded again. Only the server is made again for the new mode: repackaged where "
                                 "possible, otherwise built again from the pinned llama.cpp source (a few minutes; the "
                                 "source, about 215 MB, is fetched once when it is not there yet).\n\nAfterwards the "
                                 "old mode's files are listed, and you decide what is removed.\n\nContinue?",
        "runtime.mode.install_question": "Ditto's server will be installed {mode}: built from the pinned llama.cpp "
                                         "source (a few minutes; the source, about 215 MB, is fetched once when it is "
                                         "not there yet). The model is downloaded separately (llm-cli ditto models "
                                         "download).\n\nContinue?",
        "runtime.mode.working": "Working… this can take minutes; the window can stay open.",
        "runtime.mode.failed": "Nothing was switched: {reason}",
        "runtime.mode.leftovers.question": "The old mode's files and Docker objects are not needed any more. Remove "
                                           "exactly these {n} objects ({size})? Build folders go to the trash (File > "
                                           "Trash... restores them; their space stays used until you empty the "
                                           "trash); Docker has no trash, so its images and containers are deleted "
                                           "for good. With No, nothing is removed.\n\n{items}",
        "runtime.mode.item.id": "ID: {id}",
        "runtime.mode.goes.trash": "Goes to: the trash",
        "runtime.mode.goes.deleted": "Goes to: deleted for good",
        "runtime.mode.removed": "{n} removed, about {size}.",
        "runtime.mode.trashed": "{n} moved to the trash (File > Trash... lists them).",
        "runtime.mode.kept": "Kept: {name} ({reason})",
        "runtime.mode.nothing_removed": "Nothing was removed; the objects stay until you remove them.",
    },
    "de": {
        "runtime.mode.title": "Dittos lokaler Server",
        "runtime.mode.intro": "Dittos eigenes Modell läuft auf diesem Computer: direkt (empfohlen, ohne den Aufwand "
                              "von Docker) oder in Docker (ein Image, das auf diesem Computer gebaut und nie "
                              "heruntergeladen wird). Beide nutzen dieselben Modelle; ein Wechsel erzeugt nur den "
                              "Server neu.",
        "runtime.mode.label": "Ausführen:",
        "runtime.mode.choice.bare-metal": "Direkt (empfohlen)",
        "runtime.mode.choice.docker": "In Docker",
        "runtime.mode.now.bare-metal": "Jetzt: direkt ({what})",
        "runtime.mode.now.docker": "Jetzt: in Docker ({what})",
        "runtime.mode.now.none": "Jetzt: nicht installiert",
        "runtime.mode.switch": "Wechseln…",
        "runtime.mode.install": "Installieren…",
        "runtime.mode.same": "Dittos Server läuft schon {mode}.",
        "runtime.mode.name.bare-metal": "direkt",
        "runtime.mode.name.docker": "in Docker",
        "runtime.mode.question": "Dittos Server läuft danach {mode}.\n\nDie Modelle bleiben, wo sie sind, und werden "
                                 "nicht noch einmal heruntergeladen. Nur der Server wird für die neue Art neu erzeugt: "
                                 "umgepackt, wo das geht, sonst aus dem festgelegten llama.cpp-Quellcode neu gebaut "
                                 "(einige Minuten; der Quellcode, etwa 215 MB, wird einmal geholt, wenn er noch nicht "
                                 "da ist).\n\nDanach werden die Dateien der alten Art aufgelistet, und du entscheidest, "
                                 "was entfernt wird.\n\nWeiter?",
        "runtime.mode.install_question": "Dittos Server wird {mode} installiert: aus dem festgelegten "
                                         "llama.cpp-Quellcode gebaut (einige Minuten; der Quellcode, etwa 215 MB, wird "
                                         "einmal geholt, wenn er noch nicht da ist). Das Modell wird getrennt "
                                         "heruntergeladen (llm-cli ditto models download).\n\nWeiter?",
        "runtime.mode.working": "Läuft … das kann Minuten dauern; das Fenster kann offen bleiben.",
        "runtime.mode.failed": "Nichts wurde gewechselt: {reason}",
        "runtime.mode.leftovers.question": "Die Dateien und Docker-Objekte der alten Art werden nicht mehr gebraucht. "
                                           "Genau diese {n} Objekte ({size}) entfernen? Build-Ordner kommen in den "
                                           "Papierkorb (File > Trash... stellt sie wieder her; ihr Platz bleibt "
                                           "belegt, bis du den Papierkorb leerst); Docker hat keinen Papierkorb, "
                                           "seine Images und Container werden also endgültig gelöscht. Mit Nein wird "
                                           "nichts entfernt.\n\n{items}",
        "runtime.mode.item.id": "ID: {id}",
        "runtime.mode.goes.trash": "Kommt: in den Papierkorb",
        "runtime.mode.goes.deleted": "Kommt: endgültig gelöscht",
        "runtime.mode.removed": "{n} entfernt, etwa {size}.",
        "runtime.mode.trashed": "{n} in den Papierkorb gelegt (File > Trash... listet sie).",
        "runtime.mode.kept": "Behalten: {name} ({reason})",
        "runtime.mode.nothing_removed": "Nichts wurde entfernt; die Objekte bleiben, bis du sie entfernst.",
    },
}
