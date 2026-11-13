"""
Texts of Ditto's archive window (Ditto > Ditto's Archive...; `llm-cli ditto archive`), English and German.
LocalizationManager merges them (localization.TEXT_MODULES); a key that exists already is refused there. Every key has
the same {placeholders} in both languages. German addresses the person with "du" in two texts; FORMAL has their "Sie"
form. Imports nothing.
"""

TEXTS = {
    "en": {
        "archive.menu": "Ditto's Archive...",
        "archive.title": "Ditto's archive",
        "archive.intro": "What Ditto learned from tool results: builds that passed their functional test and pages of "
                         "manufacturers. An entry is a claim until it is confirmed. The person confirms at will; "
                         "Ditto confirms by itself only when enough independent sources (different domains) say the "
                         "same.",
        "archive.loading": "Reading the archive ...",
        "archive.failed": "The archive could not be read: {reason}",
        "archive.overview": "{entries} entries, {confirmed} confirmed, {copies} copies kept. Level {level}: Ditto "
                            "confirms an entry by itself with {needed} independent sources.",
        "archive.empty": "Nothing here yet. A build that passed its functional test, or a manufacturer's own page "
                         "found in the knowledge base, teaches the archive.",
        "archive.search.placeholder": "Search the archive",
        "archive.search": "Search",
        "archive.confirmed_only": "Confirmed only",
        "archive.show_copies": "Show the copies of replaced entries",
        "archive.status.all": "All entries",
        "archive.status.unconfirmed": "Not confirmed",
        "archive.status.confirmed": "Confirmed",
        "archive.col.status": "Status",
        "archive.col.ground": "Ground",
        "archive.col.sources": "Sources",
        "archive.col.key": "Key",
        "archive.col.text": "Sentence",
        "archive.col.updated": "Updated",
        "archive.state.unconfirmed": "not confirmed",
        "archive.state.confirmed": "confirmed by {who}",
        "archive.state.copy": "copy",
        "archive.who.person": "the person",
        "archive.who.ditto": "Ditto",
        "archive.ground.sources": "several sources",
        "archive.ground.vendor": "manufacturer's page",
        "archive.ground.tested": "passed a test",
        "archive.confirm": "Confirm",
        "archive.unconfirm": "Take Confirmation Back",
        "archive.delete": "Delete...",
        "archive.undo": "Undo Replacement...",
        "archive.refresh": "Refresh",
        "archive.close": "Close",
        "archive.level.label": "Level:",
        "archive.level.low": "Low: 3 sources",
        "archive.level.mid": "Mid: 5 sources",
        "archive.level.high": "High: 7 sources",
        "archive.level.apply": "Set Level",
        "archive.level.saved": "Level: {level}",
        "archive.level.not_saved": "The level holds for this run but could not be saved.",
        "archive.detail.empty": "Select an entry to see it in full.",
        "archive.detail": "{entry}\n{text}\nGround: {ground}; {sources} independent source(s)\n"
                          "Confirmed by: {who}\nEvidence:\n{evidence}",
        "archive.detail.copy": "Kept when a newer entry replaced it ({when}); undoing the replacement brings it back.",
        "archive.detail.nothing": "none",
        "archive.select_first": "Select an entry first.",
        "archive.undo.none": "There is nothing to undo for this entry.",
        "archive.delete.ask": "Delete this entry?\n\n{entry}\n{text}",
        "archive.undo.ask": "Undo the replacement of {entry}? The earlier, confirmed text becomes the entry again, and "
                            "the newer text becomes the copy.",
        "archive.done": "Done: {entry}",
        "archive.deleted": "Deleted: {entry}",
    },
    "de": {
        "archive.menu": "Dittos Archiv ...",
        "archive.title": "Dittos Archiv",
        "archive.intro": "Was Ditto aus Tool-Ergebnissen gelernt hat: Builds, die ihren Funktionstest bestanden haben, "
                         "und Seiten von Herstellern. Ein Eintrag ist eine Behauptung, bis er bestätigt ist. Die "
                         "Person bestätigt nach Belieben; Ditto bestätigt nur von selbst, wenn genug unabhängige "
                         "Quellen (verschiedene Domains) dasselbe sagen.",
        "archive.loading": "Das Archiv wird gelesen ...",
        "archive.failed": "Das Archiv konnte nicht gelesen werden: {reason}",
        "archive.overview": "{entries} Einträge, {confirmed} bestätigt, {copies} Kopien aufbewahrt. Stufe {level}: "
                            "Ditto bestätigt einen Eintrag von selbst mit {needed} unabhängigen Quellen.",
        "archive.empty": "Noch nichts hier. Ein Build, der seinen Funktionstest bestanden hat, oder eine Herstellerseite "
                         "aus der Wissensbasis lehrt das Archiv.",
        "archive.search.placeholder": "Im Archiv suchen",
        "archive.search": "Suchen",
        "archive.confirmed_only": "Nur bestätigte",
        "archive.show_copies": "Die Kopien ersetzter Einträge zeigen",
        "archive.status.all": "Alle Einträge",
        "archive.status.unconfirmed": "Nicht bestätigt",
        "archive.status.confirmed": "Bestätigt",
        "archive.col.status": "Status",
        "archive.col.ground": "Grundlage",
        "archive.col.sources": "Quellen",
        "archive.col.key": "Schlüssel",
        "archive.col.text": "Satz",
        "archive.col.updated": "Geändert",
        "archive.state.unconfirmed": "nicht bestätigt",
        "archive.state.confirmed": "bestätigt von {who}",
        "archive.state.copy": "Kopie",
        "archive.who.person": "der Person",
        "archive.who.ditto": "Ditto",
        "archive.ground.sources": "mehrere Quellen",
        "archive.ground.vendor": "Herstellerseite",
        "archive.ground.tested": "Test bestanden",
        "archive.confirm": "Bestätigen",
        "archive.unconfirm": "Bestätigung zurücknehmen",
        "archive.delete": "Löschen ...",
        "archive.undo": "Ersetzung rückgängig ...",
        "archive.refresh": "Aktualisieren",
        "archive.close": "Schließen",
        "archive.level.label": "Stufe:",
        "archive.level.low": "Niedrig: 3 Quellen",
        "archive.level.mid": "Mittel: 5 Quellen",
        "archive.level.high": "Hoch: 7 Quellen",
        "archive.level.apply": "Stufe setzen",
        "archive.level.saved": "Stufe: {level}",
        "archive.level.not_saved": "Die Stufe gilt für diesen Lauf, konnte aber nicht gespeichert werden.",
        "archive.detail.empty": "Wähle einen Eintrag, um ihn ganz zu sehen.",
        "archive.detail": "{entry}\n{text}\nGrundlage: {ground}; {sources} unabhängige Quelle(n)\n"
                          "Bestätigt von: {who}\nBelege:\n{evidence}",
        "archive.detail.copy": "Aufbewahrt, als ein neuerer Eintrag ihn ersetzt hat ({when}); die Ersetzung rückgängig "
                               "zu machen bringt ihn zurück.",
        "archive.detail.nothing": "keine",
        "archive.select_first": "Wähle zuerst einen Eintrag.",
        "archive.undo.none": "Für diesen Eintrag gibt es nichts rückgängig zu machen.",
        "archive.delete.ask": "Diesen Eintrag löschen?\n\n{entry}\n{text}",
        "archive.undo.ask": "Die Ersetzung von {entry} rückgängig machen? Der frühere, bestätigte Text wird wieder der "
                            "Eintrag, und der neuere Text wird die Kopie.",
        "archive.done": "Erledigt: {entry}",
        "archive.deleted": "Gelöscht: {entry}",
    },
}

# A text that addresses the person with "du" needs a "Sie" form here; two of these do ("Wähle ...").
FORMAL = {
    "de": {
        "archive.detail.empty": "Wählen Sie einen Eintrag, um ihn ganz zu sehen.",
        "archive.select_first": "Wählen Sie zuerst einen Eintrag.",
    },
}
