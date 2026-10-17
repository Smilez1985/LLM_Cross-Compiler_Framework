#!/usr/bin/env python3
"""
Texts of the golden-artifact output folder (the save dialog, Save..., the output folder setting), of the disk check
before a knowledge snapshot and of the note about an identical earlier build (DECISIONS, eighth round: answers 2 and
3, N11, N13), English and German. LocalizationManager merges them (localization.TEXT_MODULES); a key that exists
already is refused there, so these never replace another text.

Every key has the same {placeholders} in both languages (tests/test_artifact_output.py). A text names a place with
{path:<objectName>} (wave 6: the menus are translated). This module imports nothing from the framework.
"""

TEXTS = {
    "en": {
        # the disk check before a knowledge snapshot
        "snapshots.disk": "The snapshots use {size}; {free} are free on that disk.",
        "snapshots.no_room.title": "Not enough disk space",
        "snapshots.no_room.offer": "Nothing was changed. Open the list of knowledge snapshots now to delete or export "
                                   "the ones you no longer need? Nothing is ticked and nothing is deleted by itself.",
        "snapshots.tight": "Disk space is tight: the knowledge snapshot took about {size}; {free} are left on that "
                           "disk.",
        # saving a finished build (the save dialog and Save...)
        "save.title": "Save the finished model {name} to",
        "save.kept": "Not saved elsewhere: the model stays in the framework's build folder ({path}). Save it later "
                     "with: {path:btn_deploy_save}",
        "save.in_output": "The model is in the output folder: {path}",
        "save.done.renamed": "Saved: {path} (moved on the same disk, nothing copied; {files} files, {size}).",
        "save.done.copied": "Saved: {path} ({files} files, {size}; every copy was checked against its original, then "
                            "the copy in the build folder was removed).",
        "save.done.kept": "Saved: {path} ({files} files, {size}; every copy was checked against its original). Not "
                          "everything could be copied, so the copy in the build folder stays.",
        "save.done.zip": "Golden ZIP: {path}",
        "save.failed": "Not saved: {reason} {next}",
        "save.button": "Save…",
        "save.not_saved": "not saved yet",
        "save.only_build_folder": "Only a model in the framework's build folder (not saved yet) can be saved here.",
        "save.ai_saved": "A build an AI started has finished and is saved in: {paths}",
        # the output folder setting (File > Output Folder...)
        "output.title": "Output folder for finished models (now: {path})",
        "output.set": "Output folder: {path}. Finished models are saved here from now on; models in the old folder "
                      "stay where they are and stay listed.",
        "output.refused": "Not changed: {reason}",
        "output.not_saved": "The output folder was not saved: the settings file could not be written.",
        # an earlier build with the same options (before a build)
        "earlier.title": "Identical build",
        "earlier.identical": "A finished build with exactly the same options exists already (built {created}):\n"
                             "{path}\n\nUse it, or build it again? Nothing is skipped by itself.",
        "earlier.unchecked": "A finished build with the same options exists (built {created} from commit {commit}):\n"
                             "{path}\n\nWhether the model's newest version is still that commit could not be checked. "
                             "Use it, or build again?",
        "earlier.use": "Use the existing build",
        "earlier.again": "Build again",
        "earlier.used": "Nothing was started: the existing build is used ({path}).",
    },
    "de": {
        "snapshots.disk": "Die Snapshots belegen {size}; auf dieser Festplatte sind {free} frei.",
        "snapshots.no_room.title": "Nicht genug Speicherplatz",
        "snapshots.no_room.offer": "Es wurde nichts geändert. Jetzt die Liste der Wissens-Snapshots öffnen, um die zu "
                                   "löschen oder zu exportieren, die du nicht mehr brauchst? Nichts ist angehakt, und "
                                   "nichts wird von selbst gelöscht.",
        "snapshots.tight": "Der Speicherplatz ist knapp: der Wissens-Snapshot belegt etwa {size}; auf dieser "
                           "Festplatte sind noch {free} frei.",
        "save.title": "Das fertige Modell {name} speichern in",
        "save.kept": "Nicht woanders gespeichert: das Modell bleibt im Build-Ordner des Frameworks ({path}). Speichere "
                     "es später mit: {path:btn_deploy_save}",
        "save.in_output": "Das Modell liegt im Ausgabeordner: {path}",
        "save.done.renamed": "Gespeichert: {path} (auf derselben Platte verschoben, nichts kopiert; {files} Dateien, "
                             "{size}).",
        "save.done.copied": "Gespeichert: {path} ({files} Dateien, {size}; jede Kopie wurde mit ihrem Original "
                            "verglichen, danach wurde die Kopie im Build-Ordner entfernt).",
        "save.done.kept": "Gespeichert: {path} ({files} Dateien, {size}; jede Kopie wurde mit ihrem Original "
                          "verglichen). Nicht alles ließ sich kopieren, deshalb bleibt die Kopie im Build-Ordner.",
        "save.done.zip": "Golden-ZIP: {path}",
        "save.failed": "Nicht gespeichert: {reason} {next}",
        "save.button": "Speichern…",
        "save.not_saved": "noch nicht gespeichert",
        "save.only_build_folder": "Hier lässt sich nur ein Modell aus dem Build-Ordner des Frameworks (noch nicht "
                                  "gespeichert) speichern.",
        "save.ai_saved": "Ein Build, den eine KI gestartet hat, ist fertig und gespeichert in: {paths}",
        "output.title": "Ausgabeordner für fertige Modelle (jetzt: {path})",
        "output.set": "Ausgabeordner: {path}. Fertige Modelle werden ab jetzt hier gespeichert; Modelle im alten Ordner "
                      "bleiben, wo sie sind, und werden weiter aufgelistet.",
        "output.refused": "Nicht geändert: {reason}",
        "output.not_saved": "Der Ausgabeordner wurde nicht gespeichert: die Einstellungsdatei ließ sich nicht "
                            "schreiben.",
        "earlier.title": "Gleicher Build",
        "earlier.identical": "Es gibt schon einen fertigen Build mit genau denselben Optionen (gebaut {created}):\n"
                             "{path}\n\nDiesen nehmen oder noch einmal bauen? Nichts wird von selbst übersprungen.",
        "earlier.unchecked": "Es gibt einen fertigen Build mit denselben Optionen (gebaut {created} aus Commit "
                             "{commit}):\n{path}\n\nOb die neueste Version des Modells noch dieser Commit ist, ließ "
                             "sich nicht prüfen. Diesen nehmen oder noch einmal bauen?",
        "earlier.use": "Den vorhandenen Build nehmen",
        "earlier.again": "Noch einmal bauen",
        "earlier.used": "Nichts wurde gestartet: der vorhandene Build wird genommen ({path}).",
    },
}
