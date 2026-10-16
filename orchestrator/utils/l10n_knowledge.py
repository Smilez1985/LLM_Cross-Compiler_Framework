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
        "storage.old.converted": "The knowledge base is stored in Qdrant Edge. It was converted from the storage of "
                                 "earlier versions on {when} ({points} excerpts; counts and sample searches were "
                                 "checked). The old folder {folder} ({size}) is kept until you delete it.",
        "storage.old.failed": "The knowledge base still uses the storage of earlier versions: its conversion to "
                              "Qdrant Edge failed ({reason}). It keeps working. It is tried again {retry}.",
        "storage.old.delete": "Delete the old storage...",
        "storage.old.title": "Old knowledge base storage",
        "storage.old.none": "There is no old storage folder to delete.",
        "storage.old.done.trash": "Moved to the trash: the old knowledge base storage. You can restore it from the "
                                  "trash; its space stays used until you empty the trash.",
        "storage.old.done.permanent": "Deleted: the old knowledge base storage.",
        "storage.old.kept": "Kept: the old knowledge base storage ({reason}).",
    },
    "de": {
        "storage.old.converted": "Die Wissensdatenbank liegt in Qdrant Edge. Sie wurde am {when} aus dem Speicher "
                                 "früherer Versionen umgewandelt ({points} Auszüge; Anzahl und Stichproben-Suchen "
                                 "wurden geprüft). Der alte Ordner {folder} ({size}) bleibt, bis du ihn löschst.",
        "storage.old.failed": "Die Wissensdatenbank nutzt noch den Speicher früherer Versionen: die Umwandlung in "
                              "Qdrant Edge ist fehlgeschlagen ({reason}). Sie funktioniert weiter. Sie wird erneut "
                              "versucht: {retry}.",
        "storage.old.delete": "Alten Speicher löschen...",
        "storage.old.title": "Alter Speicher der Wissensdatenbank",
        "storage.old.none": "Es gibt keinen alten Speicherordner zum Löschen.",
        "storage.old.done.trash": "In den Papierkorb verschoben: der alte Speicher der Wissensdatenbank. Du kannst "
                                  "ihn aus dem Papierkorb wiederherstellen; sein Platz bleibt belegt, bis du den "
                                  "Papierkorb leerst.",
        "storage.old.done.permanent": "Gelöscht: der alte Speicher der Wissensdatenbank.",
        "storage.old.kept": "Behalten: der alte Speicher der Wissensdatenbank ({reason}).",
    },
}
