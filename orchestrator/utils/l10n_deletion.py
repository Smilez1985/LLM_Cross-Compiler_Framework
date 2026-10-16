#!/usr/bin/env python3
"""
Texts of the deletion dialog, the trash window, the knowledge snapshots window and the framework build images window
(delegation design 3.10, round 5 f; round 6 10.4), English and German. LocalizationManager merges them
(localization.TEXT_MODULES); a key that exists already is refused there, so these never replace another text.

Every key has the same {placeholders} in both languages (tests/test_deletion_dialog.py). The main window's menus are
not translated, so a German text names a menu as it is shown ("File > Trash..."). German terms: "Papierkorb"
(trash), "endgültig löschen" (delete for good), "wiederherstellen" (restore). This module imports nothing from the
framework.
"""

TEXTS = {
    "en": {
        # the deletion dialog (list and multiple choice)
        "deletion.title": "Delete",
        "deletion.intro.list": "This is exactly what would be deleted. Nothing happens unless you choose it below.",
        "deletion.intro.multiple": "Tick what you want to delete. Nothing is ticked; whatever you do not tick stays.",
        "deletion.path": "Full path: {path}",
        "deletion.id": "Docker id: {id}",
        "deletion.file": "File name: {name}",
        "deletion.size": "Size: {size}",
        "deletion.what": "What it is: {what}",
        "deletion.show": "Show in file manager",
        "deletion.mode.trash": "Move to the trash (you can restore it; the space stays used until you empty the "
                               "trash)",
        "deletion.mode.permanent": "Delete for good (frees {size} now; this cannot be undone)",
        "deletion.only_permanent": "This deletes for good; it cannot be undone.",
        "deletion.only_trash": "This moves everything to the trash; you can restore it there.",
        "deletion.yes.trash": "Move {n} items ({size}) to the trash",
        "deletion.yes.permanent": "Delete {n} items ({size}) for good",
        "deletion.yes.none": "Nothing ticked",
        "deletion.no": "No, keep everything",
        "deletion.result.title": "Deleting",
        "deletion.changed": "The list changed while you read it; nothing was deleted. Open it again to see the new "
                            "list.",
        "deletion.failed": "Nothing was deleted: {reason}",
        "deletion.kept": "Kept: {name} ({reason})",
        "deletion.done.trash": "Moved to the trash: {name}",
        "deletion.done.permanent": "Deleted for good: {name}",

        # the trash window (File > Trash...)
        "trash.title": "Trash",
        "trash.intro": "What deletions moved here. Nothing leaves the trash by itself; its space stays used until "
                       "you empty it. {n} entries, {size}.",
        "trash.empty_note": "The trash is empty.",
        "trash.col.item": "Item",
        "trash.col.what": "What it is",
        "trash.col.size": "Size",
        "trash.col.deleted": "Moved there",
        "trash.col.by": "Asked by",
        "trash.col.remind": "Reminder",
        "trash.col.from": "From (full path)",
        "trash.due": "{date} (due)",
        "trash.gone": "{name} (gone from the trash folder)",
        "trash.restore": "Restore ticked",
        "trash.delete": "Delete ticked for good…",
        "trash.keep": "Remind me later",
        "trash.restored": "Restored {name} to {path}",
        "trash.restore_failed": "{name}: {reason}",
        "trash.problem": "The trash cannot be read: {reason}",
        "trash.nothing_ticked": "Tick at least one entry first.",

        # the knowledge snapshots window (Tools > Knowledge Snapshots...)
        "snapshots.title": "Knowledge Snapshots",
        "snapshots.intro": "{n} snapshots, {size}, oldest first, in {folder}. Before every Deep Ingest, every index "
                           "of the framework's own code and every restore, the knowledge base is saved as a "
                           "snapshot, so the change can be undone. Tick the ones to delete or export; nothing is "
                           "ticked.",
        "snapshots.col.name": "Snapshot",
        "snapshots.col.taken": "Taken",
        "snapshots.col.size": "Size",
        "snapshots.col.what": "What it is",
        "snapshots.col.path": "Full path",
        "snapshots.delete": "Delete ticked…",
        "snapshots.export": "Export ticked…",
        "snapshots.open": "Open the folder",
        "snapshots.export_to": "Copy the snapshots to",
        "snapshots.exported": "Copied and checked (sha256): {name} -> {path}",
        "snapshots.not_exported": "Not exported: {name}: {reason}",
        "snapshots.export_title": "Export",
        "snapshots.export_note": "The snapshots themselves were not changed.",
        "snapshots.no_delete": "Deleting needs the framework (its trash and audit log); open this window from the "
                               "main window.",
        "snapshots.note": "There is no limit: the framework keeps as many snapshots as the disk holds and deletes "
                          "no snapshot by itself. To free space, copy the ones you want to keep elsewhere (export) or "
                          "delete the ones you choose (they go to the trash first, where you can restore them).",

        # the framework build images window (Tools > Framework Build Images...)
        "images.title": "Framework Build Images",
        "images.intro": "The framework never deletes an image by itself. Docker keeps its images in {root}. Tick "
                        "the images you want to delete; nothing is ticked. Sizes include layers images may share, so "
                        "less space can be freed. Docker's build cache is never touched: it belongs to every "
                        "project on this computer.",
        "images.intro_no_root": "The framework never deletes an image by itself. Tick the images you want to delete; "
                                "nothing is ticked. Sizes include layers images may share, so less space can be "
                                "freed. Docker's build cache is never touched: it belongs to every project on this "
                                "computer.",
        "images.none": "There are no framework build images to delete.",
        "images.unavailable": "Docker cannot be asked: {reason}",
        "images.done": "{n} images deleted, about {size}.",
        "healing.free_docker": "Free Docker space…",
    },
    "de": {
        "deletion.title": "Löschen",
        "deletion.intro.list": "Genau das würde gelöscht. Es passiert nichts, solange du es unten nicht wählst.",
        "deletion.intro.multiple": "Hake an, was du löschen willst. Nichts ist angehakt; was du nicht anhakst, "
                                   "bleibt.",
        "deletion.path": "Vollständiger Pfad: {path}",
        "deletion.id": "Docker-ID: {id}",
        "deletion.file": "Dateiname: {name}",
        "deletion.size": "Größe: {size}",
        "deletion.what": "Was es ist: {what}",
        "deletion.show": "Im Dateimanager zeigen",
        "deletion.mode.trash": "In den Papierkorb verschieben (du kannst es wiederherstellen; der Platz bleibt "
                               "belegt, bis du den Papierkorb leerst)",
        "deletion.mode.permanent": "Endgültig löschen (gibt jetzt {size} frei; das lässt sich nicht rückgängig "
                                   "machen)",
        "deletion.only_permanent": "Das löscht endgültig; es lässt sich nicht rückgängig machen.",
        "deletion.only_trash": "Das verschiebt alles in den Papierkorb; dort kannst du es wiederherstellen.",
        "deletion.yes.trash": "{n} Einträge ({size}) in den Papierkorb",
        "deletion.yes.permanent": "{n} Einträge ({size}) endgültig löschen",
        "deletion.yes.none": "Nichts angehakt",
        "deletion.no": "Nein, alles behalten",
        "deletion.result.title": "Löschen",
        "deletion.changed": "Die Liste hat sich geändert, während du sie gelesen hast; nichts wurde gelöscht. Öffne "
                            "sie noch einmal, um die neue Liste zu sehen.",
        "deletion.failed": "Nichts wurde gelöscht: {reason}",
        "deletion.kept": "Behalten: {name} ({reason})",
        "deletion.done.trash": "In den Papierkorb verschoben: {name}",
        "deletion.done.permanent": "Endgültig gelöscht: {name}",

        "trash.title": "Papierkorb",
        "trash.intro": "Was Löschungen hierher verschoben haben. Nichts verlässt den Papierkorb von selbst; sein "
                       "Platz bleibt belegt, bis du ihn leerst. {n} Einträge, {size}.",
        "trash.empty_note": "Der Papierkorb ist leer.",
        "trash.col.item": "Eintrag",
        "trash.col.what": "Was es ist",
        "trash.col.size": "Größe",
        "trash.col.deleted": "Verschoben am",
        "trash.col.by": "Angefordert von",
        "trash.col.remind": "Erinnerung",
        "trash.col.from": "Herkunft (vollständiger Pfad)",
        "trash.due": "{date} (fällig)",
        "trash.gone": "{name} (fehlt im Papierkorb-Ordner)",
        "trash.restore": "Angehakte wiederherstellen",
        "trash.delete": "Angehakte endgültig löschen…",
        "trash.keep": "Später erinnern",
        "trash.restored": "{name} wiederhergestellt nach {path}",
        "trash.restore_failed": "{name}: {reason}",
        "trash.problem": "Der Papierkorb lässt sich nicht lesen: {reason}",
        "trash.nothing_ticked": "Hake zuerst mindestens einen Eintrag an.",

        "snapshots.title": "Wissens-Snapshots",
        "snapshots.intro": "{n} Snapshots, {size}, die ältesten zuerst, in {folder}. Vor jedem Deep Ingest, jedem "
                           "Indizieren des eigenen Codes des Frameworks und jeder Wiederherstellung wird die "
                           "Wissensbasis als Snapshot gesichert, damit sich die Änderung rückgängig machen lässt. Hake "
                           "die an, die du löschen oder exportieren willst; nichts ist angehakt.",
        "snapshots.col.name": "Snapshot",
        "snapshots.col.taken": "Erstellt",
        "snapshots.col.size": "Größe",
        "snapshots.col.what": "Was es ist",
        "snapshots.col.path": "Vollständiger Pfad",
        "snapshots.delete": "Angehakte löschen…",
        "snapshots.export": "Angehakte exportieren…",
        "snapshots.open": "Ordner öffnen",
        "snapshots.export_to": "Snapshots kopieren nach",
        "snapshots.exported": "Kopiert und geprüft (sha256): {name} -> {path}",
        "snapshots.not_exported": "Nicht exportiert: {name}: {reason}",
        "snapshots.export_title": "Exportieren",
        "snapshots.export_note": "Die Snapshots selbst wurden nicht verändert.",
        "snapshots.no_delete": "Zum Löschen braucht es das Framework (seinen Papierkorb und sein Audit-Protokoll); "
                               "öffne dieses Fenster aus dem Hauptfenster.",
        "snapshots.note": "Es gibt keine Obergrenze: das Framework behält so viele Snapshots, wie die Platte fasst, "
                          "und löscht keinen von selbst. Um Platz zu schaffen, kopiere die, die du behalten willst, "
                          "woandershin (Export), oder lösche die, die du auswählst (sie kommen zuerst in den "
                          "Papierkorb, wo du sie wiederherstellen kannst).",

        "images.title": "Build-Images des Frameworks",
        "images.intro": "Das Framework löscht nie von selbst ein Image. Docker legt seine Images in {root} ab. Hake "
                        "die Images an, die du löschen willst; nichts ist angehakt. Die Größen enthalten Schichten, "
                        "die sich Images teilen können, daher wird eventuell weniger Platz frei. Der Build-Cache von "
                        "Docker wird nie angefasst: er gehört allen Projekten auf diesem Computer.",
        "images.intro_no_root": "Das Framework löscht nie von selbst ein Image. Hake die Images an, die du löschen "
                                "willst; nichts ist angehakt. Die Größen enthalten Schichten, die sich Images teilen "
                                "können, daher wird eventuell weniger Platz frei. Der Build-Cache von Docker wird nie "
                                "angefasst: er gehört allen Projekten auf diesem Computer.",
        "images.none": "Es gibt keine Build-Images des Frameworks zum Löschen.",
        "images.unavailable": "Docker lässt sich nicht fragen: {reason}",
        "images.done": "{n} Images gelöscht, etwa {size}.",
        "healing.free_docker": "Docker-Platz freigeben…",
    },
}
