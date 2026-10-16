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
                                 "source, about 215 MB, is fetched once when it is not there yet). First the framework "
                                 "checks that it fits next to the old mode.\n\nOnce the new mode works, every object "
                                 "of the old mode is listed, and with your one yes it is deleted for good: the two "
                                 "modes are alternatives, so nothing of the old one stays.\n\nContinue?",
        "runtime.mode.install_question": "Ditto's server will be installed {mode}: built from the pinned llama.cpp "
                                         "source (a few minutes; the source, about 215 MB, is fetched once when it is "
                                         "not there yet). The model is downloaded separately (Ditto > Local "
                                         "Models...).\n\nContinue?",
        "runtime.mode.working": "Working… this can take minutes; the window can stay open.",
        "runtime.mode.failed": "Nothing was switched: {reason}",
        "runtime.mode.leftovers.question": "The new mode works. Delete every object of the old mode for good: exactly "
                                           "these {n} objects ({size})? Nothing goes to the trash, so the old mode is "
                                           "gone completely. The models stay. With No, nothing is removed.\n\n{items}",
        "runtime.mode.item.id": "ID: {id}",
        "runtime.mode.removed": "{n} deleted for good, about {size}.",
        "runtime.mode.kept": "Kept: {name} ({reason})",
        "runtime.mode.nothing_removed": "Nothing was removed; the objects stay until you remove them.",
        # the modes as the space questions name them
        "runtime.mode.what.bare-metal": "bare metal",
        "runtime.mode.what.docker": "Docker mode",
        "runtime.mode.space.question": "{new} does not fit next to {old}, so nothing was built.\n\n{sizes}\n\nYou can "
                                       "free space yourself and switch again. Or you delete {old} first: every object "
                                       "below goes for good. Ditto is unavailable until {new} works; the models stay. "
                                       "If {new} then fails, you are told so and can make {old} again.\n\nDelete all "
                                       "{n} objects of {old} first ({size})? With No, nothing is deleted.\n\n{items}",
        "runtime.mode.space.nothing_left": "{new} does not fit next to {old}, and nothing of {old} is left to delete. "
                                           "Nothing was built.\n\n{sizes}\n\nFree space on these disks and switch "
                                           "again.",
        # the estimate once the old mode is deleted first (its build can then no longer be reused)
        "runtime.mode.space.after": "With {old} deleted first:",
        "runtime.mode.space.no_help": "{new} does not fit next to {old}, so nothing was built.\n\n{sizes}\n\nEven "
                                      "with {old} deleted first, {new} would not fit:\n\n{after}\n\nSo deleting {old} "
                                      "first is not offered: it would only leave Ditto without a local runtime. "
                                      "Nothing was deleted. Free space on these disks and switch again.",
        "runtime.mode.space.declined": "Nothing was deleted and nothing was built. Free space and switch again.",
        "runtime.mode.space.removed": "{old} was deleted. {new} is made now; Ditto is unavailable until it works.",
        "runtime.mode.space.failed": "{new} could not be made: {reason}\n\nDitto has no local runtime now: {old} was "
                                     "deleted before. The models are still there.\n\nMake {old} again now?",
        "runtime.mode.space.later": "You can make it again here later, or in a terminal: llm-cli ditto runtime ensure "
                                    "--mode {mode}",
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
                                 "da ist). Vorher prüft das Framework, ob die neue Art neben der alten Platz "
                                 "hat.\n\nSobald die neue Art funktioniert, wird jedes Objekt der alten Art "
                                 "aufgelistet, und mit deinem einen Ja wird es endgültig gelöscht: Die beiden Arten "
                                 "sind Alternativen, von der alten bleibt nichts.\n\nWeiter?",
        "runtime.mode.install_question": "Dittos Server wird {mode} installiert: aus dem festgelegten "
                                         "llama.cpp-Quellcode gebaut (einige Minuten; der Quellcode, etwa 215 MB, wird "
                                         "einmal geholt, wenn er noch nicht da ist). Das Modell wird getrennt "
                                         "heruntergeladen (Ditto > Lokale Modelle...).\n\nWeiter?",
        "runtime.mode.working": "Läuft … das kann Minuten dauern; das Fenster kann offen bleiben.",
        "runtime.mode.failed": "Nichts wurde gewechselt: {reason}",
        "runtime.mode.leftovers.question": "Die neue Art funktioniert. Jedes Objekt der alten Art endgültig löschen, "
                                           "also genau diese {n} Objekte ({size})? Nichts kommt in den Papierkorb, die "
                                           "alte Art ist danach ganz weg. Die Modelle bleiben. Mit Nein wird nichts "
                                           "entfernt.\n\n{items}",
        "runtime.mode.item.id": "ID: {id}",
        "runtime.mode.removed": "{n} endgültig gelöscht, etwa {size}.",
        "runtime.mode.kept": "Behalten: {name} ({reason})",
        "runtime.mode.nothing_removed": "Nichts wurde entfernt; die Objekte bleiben, bis du sie entfernst.",
        # die Arten, wie die Fragen zum Platz sie nennen
        "runtime.mode.what.bare-metal": "Direktbetrieb",
        "runtime.mode.what.docker": "Docker-Betrieb",
        "runtime.mode.space.question": "Neben {old} ist kein Platz für {new}, deshalb wurde nichts "
                                       "gebaut.\n\n{sizes}\n\nDu kannst selbst Platz schaffen und noch einmal "
                                       "wechseln. Oder du löschst {old} zuerst: Jedes Objekt unten ist dann endgültig "
                                       "weg. Ditto ist nicht verfügbar, bis {new} funktioniert; die Modelle bleiben. "
                                       "Scheitert {new} danach, erfährst du das und kannst {old} wieder "
                                       "herstellen.\n\nAlle {n} Objekte von {old} zuerst löschen ({size})? Mit Nein "
                                       "wird nichts gelöscht.\n\n{items}",
        "runtime.mode.space.nothing_left": "Neben {old} ist kein Platz für {new}, und von {old} ist nichts mehr zu "
                                           "löschen. Nichts wurde gebaut.\n\n{sizes}\n\nSchaffe auf diesen "
                                           "Laufwerken Platz und wechsle noch einmal.",
        # die Schätzung, wenn die alte Art zuerst gelöscht ist (ihr Build lässt sich dann nicht mehr nutzen)
        "runtime.mode.space.after": "Wenn {old} zuerst gelöscht ist:",
        "runtime.mode.space.no_help": "Neben {old} ist kein Platz für {new}, deshalb wurde nichts "
                                      "gebaut.\n\n{sizes}\n\nAuch wenn {old} zuerst gelöscht wäre, hätte {new} keinen "
                                      "Platz:\n\n{after}\n\nDeshalb wird das Löschen von {old} nicht angeboten: Ditto "
                                      "stünde danach nur ohne lokale Laufzeit da. Nichts wurde gelöscht. Schaffe auf "
                                      "diesen Laufwerken Platz und wechsle noch einmal.",
        "runtime.mode.space.declined": "Nichts wurde gelöscht und nichts gebaut. Schaffe Platz und wechsle noch "
                                       "einmal.",
        "runtime.mode.space.removed": "{old} wurde gelöscht. {new} wird jetzt erzeugt; Ditto ist bis dahin nicht "
                                      "verfügbar.",
        "runtime.mode.space.failed": "{new} ließ sich nicht erzeugen: {reason}\n\nDitto hat jetzt keine lokale "
                                     "Laufzeit: {old} wurde vorher gelöscht. Die Modelle sind noch da.\n\n{old} jetzt "
                                     "wieder herstellen?",
        "runtime.mode.space.later": "Du kannst es später hier wieder herstellen, oder in einem Terminal: llm-cli ditto "
                                    "runtime ensure --mode {mode}",
    },
}
