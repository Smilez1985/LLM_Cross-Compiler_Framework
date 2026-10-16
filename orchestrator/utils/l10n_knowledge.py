#!/usr/bin/env python3
"""
Texts of the knowledge base's storage in the GUI (Tools > Knowledge Snapshots...): Qdrant Edge, the conversion of the
storage of earlier versions and its old folder, which only the person deletes (DECISIONS RG5, round 5 f), English and
German. LocalizationManager merges them (localization.TEXT_MODULES); a key that exists already is refused there, so
these never replace another text. Every key has the same {placeholders} in both languages
(tests/test_edge_store.py). This module imports nothing from the framework.
"""

TEXTS = {
    "en": {
        "storage.used.edge": "The knowledge base is stored in Qdrant Edge in {folder}.",
        "storage.used.old": "The knowledge base uses the storage of earlier versions (qdrant-client's local mode) in "
                            "{folder}.",
        "storage.used.server": "The knowledge base uses a Qdrant server (setting qdrant_url); the local storage is "
                               "not used.",
        "storage.used.none": "The knowledge base cannot be opened: {reason}",
        "storage.reason.engine_missing": "Qdrant Edge is not available on this computer (the package qdrant-edge-py "
                                         "has no build for it, for example Windows on ARM), so the knowledge base "
                                         "stays in the older local storage; nothing can be converted.",
        "storage.reason.not_converted": "It has not been converted to Qdrant Edge yet; that happens the next time the "
                                        "knowledge base is opened.",
        "storage.reason.edge_unavailable": "it is stored in Qdrant Edge, but Qdrant Edge cannot be loaded on this "
                                           "computer (the package qdrant-edge-py is missing or broken). Nothing was "
                                           "changed, and the storage of earlier versions is not used in its place: "
                                           "it lacks what was added since the conversion. Install the framework's "
                                           "requirements again (the installer, or pip install -r requirements.txt).",
        "storage.convert.offer": "[Convert now] converts it now: the texts and vectors are copied and checked, nothing "
                                 "is embedded again, and the old folder is kept.",
        "storage.converted": "It was converted from the storage of earlier versions on {when} ({points} excerpts; "
                             "counts and sample searches were checked).",
        "storage.old.folder.kept": "The old folder {folder} ({size}) is kept until you delete it.",
        "storage.old.folder": "Old storage folder: {folder} ({size}).",
        "storage.convert.now": "Convert now",
        "snapshots.next.no_room": "The next snapshot (about {size}) does not fit: Deep Ingest, restoring a snapshot "
                                  "and importing a knowledge pack are refused until there is room.",
        "snapshots.next.tight": "The next snapshot (about {size}) still fits, but leaves little free space.",
        "snapshots.next.step": "Free space first: delete or export the knowledge snapshots you no longer need (Tools > "
                               "Knowledge Snapshots... in the GUI, 'llm-cli knowledge snapshots prune' or 'llm-cli "
                               "knowledge snapshots export NAME --to FOLDER' in a terminal), or free space on that "
                               "disk otherwise. Nothing is deleted by itself.",
        "storage.old.failed": "The knowledge base still uses the storage of earlier versions: its conversion to "
                              "Qdrant Edge failed ({reason}). It keeps working. It is tried again {retry}.",
        "storage.old.delete": "Delete the old storage...",
        "storage.old.title": "Old knowledge base storage",
        "storage.old.none": "There is no old storage folder to delete.",
        "storage.old.done.trash": "Moved to the trash: the old knowledge base storage. You can restore it from the "
                                  "trash; its space stays used until you empty the trash.",
        "storage.old.done.permanent": "Deleted: the old knowledge base storage.",
        "storage.old.kept": "Kept: the old knowledge base storage ({reason}).",
        "storage.retry.start": "on the next start, or now with [Try the conversion again]",
        "storage.retry.person": "only when you ask, because it would fail the same way: here with [Try the "
                                "conversion again], or with 'llm-cli knowledge storage convert' in a terminal",
        "storage.convert": "Try the conversion again",
        "storage.converting": "Converting the knowledge base to Qdrant Edge: the texts and vectors are copied and "
                              "checked. With a large knowledge base this takes minutes. The old storage stays as it "
                              "is.",
        "storage.convert.done": "The knowledge base was converted to Qdrant Edge ({points} excerpts; counts and sample "
                                "searches were checked). The old folder is kept until you delete it.",
        "storage.convert.failed": "The knowledge base was not converted. It keeps working in the storage of earlier "
                                  "versions. Reason: {reason}",
        "storage.convert.off": "The knowledge base is off. Switch it on in Tools > Configure AI Agent (Ditto) first.",
    },
    "de": {
        "storage.used.edge": "Die Wissensdatenbank liegt in Qdrant Edge in {folder}.",
        "storage.used.old": "Die Wissensdatenbank nutzt den Speicher früherer Versionen (den lokalen Modus von "
                            "qdrant-client) in {folder}.",
        "storage.used.server": "Die Wissensdatenbank nutzt einen Qdrant-Server (Einstellung qdrant_url); der lokale "
                               "Speicher wird nicht genutzt.",
        "storage.used.none": "Die Wissensdatenbank kann nicht geöffnet werden: {reason}",
        "storage.reason.engine_missing": "Qdrant Edge ist auf diesem Computer nicht verfügbar (das Paket "
                                         "qdrant-edge-py hat keinen Build für ihn, zum Beispiel Windows auf ARM), "
                                         "daher bleibt die Wissensdatenbank im älteren lokalen Speicher; es kann "
                                         "nichts umgewandelt werden.",
        "storage.reason.not_converted": "Sie wurde noch nicht in Qdrant Edge umgewandelt; das geschieht, wenn die "
                                        "Wissensdatenbank das nächste Mal geöffnet wird.",
        "storage.reason.edge_unavailable": "sie liegt in Qdrant Edge, aber Qdrant Edge kann auf diesem Computer nicht "
                                           "geladen werden (das Paket qdrant-edge-py fehlt oder ist beschädigt). Es "
                                           "wurde nichts geändert, und der Speicher früherer Versionen springt nicht "
                                           "ein: ihm fehlt, was seit der Umwandlung dazukam. Installiere die "
                                           "Abhängigkeiten des Frameworks erneut (der Installer, oder pip install -r "
                                           "requirements.txt).",
        "storage.convert.offer": "[Jetzt umwandeln] wandelt sie jetzt um: Texte und Vektoren werden kopiert und "
                                 "geprüft, nichts wird neu eingebettet, und der alte Ordner bleibt.",
        "storage.converted": "Sie wurde am {when} aus dem Speicher früherer Versionen umgewandelt ({points} Auszüge; "
                             "Anzahl und Stichproben-Suchen wurden geprüft).",
        "storage.old.folder.kept": "Der alte Ordner {folder} ({size}) bleibt, bis du ihn löschst.",
        "storage.old.folder": "Alter Speicherordner: {folder} ({size}).",
        "storage.convert.now": "Jetzt umwandeln",
        "snapshots.next.no_room": "Der nächste Snapshot (etwa {size}) passt nicht: Deep Ingest, das Wiederherstellen "
                                  "eines Snapshots und der Import eines Wissenspakets werden abgelehnt, bis Platz "
                                  "ist.",
        "snapshots.next.tight": "Der nächste Snapshot (etwa {size}) passt noch, lässt aber wenig freien Platz.",
        "snapshots.next.step": "Schaffe zuerst Platz: lösche oder exportiere die Wissens-Snapshots, die du nicht mehr "
                               "brauchst (Tools > Knowledge Snapshots... in der GUI, 'llm-cli knowledge snapshots "
                               "prune' oder 'llm-cli knowledge snapshots export NAME --to FOLDER' im Terminal), oder "
                               "schaffe auf andere Weise Platz auf dieser Festplatte. Es wird nichts von selbst "
                               "gelöscht.",
        "storage.old.failed": "Die Wissensdatenbank nutzt noch den Speicher früherer Versionen: die Umwandlung in "
                              "Qdrant Edge ist fehlgeschlagen ({reason}). Sie funktioniert weiter. Ein neuer Versuch "
                              "läuft {retry}.",
        "storage.old.delete": "Alten Speicher löschen...",
        "storage.old.title": "Alter Speicher der Wissensdatenbank",
        "storage.old.none": "Es gibt keinen alten Speicherordner zum Löschen.",
        "storage.old.done.trash": "In den Papierkorb verschoben: der alte Speicher der Wissensdatenbank. Du kannst "
                                  "ihn aus dem Papierkorb wiederherstellen; sein Platz bleibt belegt, bis du den "
                                  "Papierkorb leerst.",
        "storage.old.done.permanent": "Gelöscht: der alte Speicher der Wissensdatenbank.",
        "storage.old.kept": "Behalten: der alte Speicher der Wissensdatenbank ({reason}).",
        "storage.retry.start": "beim nächsten Start, oder jetzt mit [Umwandlung erneut versuchen]",
        "storage.retry.person": "nur, wenn du es willst, weil er genauso scheitern würde: hier mit [Umwandlung "
                                "erneut versuchen] oder mit 'llm-cli knowledge storage convert' im Terminal",
        "storage.convert": "Umwandlung erneut versuchen",
        "storage.converting": "Die Wissensdatenbank wird in Qdrant Edge umgewandelt: Texte und Vektoren werden "
                              "kopiert und geprüft. Bei einer großen Wissensdatenbank dauert das Minuten. Der alte "
                              "Speicher bleibt, wie er ist.",
        "storage.convert.done": "Die Wissensdatenbank wurde in Qdrant Edge umgewandelt ({points} Auszüge; Anzahl und "
                                "Stichproben-Suchen wurden geprüft). Der alte Ordner bleibt, bis du ihn löschst.",
        "storage.convert.failed": "Die Wissensdatenbank wurde nicht umgewandelt. Sie funktioniert weiter im Speicher "
                                  "früherer Versionen. Grund: {reason}",
        "storage.convert.off": "Die Wissensdatenbank ist aus. Schalte sie zuerst unter Tools > Configure AI Agent "
                               "(Ditto) ein.",
    },
}
