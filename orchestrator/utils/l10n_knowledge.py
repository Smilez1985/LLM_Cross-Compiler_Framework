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
        "storage.old.converted": "Die Wissensdatenbank liegt in Qdrant Edge. Sie wurde am {when} aus dem Speicher "
                                 "früherer Versionen umgewandelt ({points} Auszüge; Anzahl und Stichproben-Suchen "
                                 "wurden geprüft). Der alte Ordner {folder} ({size}) bleibt, bis du ihn löschst.",
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
