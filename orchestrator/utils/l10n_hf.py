#!/usr/bin/env python3
"""
Texts of the Hugging Face browser (Tools > Hugging Face Model Browser...), English and German: its download through
the framework's verified fetcher and the optional one-time Take over of the old download cache (DECISIONS round 11,
A6). LocalizationManager merges TEXTS and FORMAL (localization.TEXT_MODULES); a key that exists already is refused
there. The window's older texts move here with the wave-6 chain deploy-hf.

Every key has the same {placeholders} in both languages. German texts say "du" (Q1); FORMAL holds the "Sie" form of
every German text here that addresses the person. This module imports nothing from the framework.
"""

TEXTS = {
    "en": {
        "hf.stop": "Stop",
        "hf.busy": "Please wait: a file list, a download or a take-over is still running.",
        "hf.download.progress": "Downloading {name}: {done} of {total}",
        "hf.download.stopping": "Stopping; the part downloaded so far is kept.",
        "hf.download.stopped": "The download of {name} was stopped. The part downloaded so far is kept; download the "
                               "file again to resume it.",
        "hf.takeover.button": "Take over old downloads…",
        "hf.takeover.tooltip": "Earlier versions saved the browser's downloads in the Hugging Face cache inside the "
                               "models folder. This copies them into the models folder, each checked; the old "
                               "copies stay until you remove them.",
        "hf.takeover.title": "Take over old downloads",
        "hf.takeover.scanning": "Looking for old downloads…",
        "hf.takeover.none": "There are no old downloads to take over. What the browser downloads now lands in the "
                            "models folder, checked.",
        "hf.takeover.ask": "Earlier versions saved these {n} files ({size}) in the Hugging Face cache inside the "
                           "models folder. They are still listed and usable. Copy them into the models folder, each "
                           "checked against its checksum? The old copies stay until you remove them.\n\n{files}",
        "hf.takeover.more": "… and {n} more",
        "hf.takeover.running": "Taking over {name} ({done} of {total} files)…",
        "hf.takeover.stopping": "Stopping; the files copied so far stay in the models folder.",
        "hf.takeover.result": "Copied {copied} of {total} files ({size}) into the models folder, each checked.",
        "hf.takeover.kept": "Kept in the old cache: {name} ({reason})",
        "hf.takeover.stopped": "The take-over was stopped. The files copied so far are in the models folder; the "
                               "rest stays in the old cache.",
        "hf.takeover.failed": "Nothing was taken over: {reason}",
        "hf.takeover.remove_title": "Remove the old copies",
        "hf.takeover.why.conflict": "another file of that name is in the models folder; it is not replaced",
        "hf.takeover.why.no_checksum": "the old cache kept no checksum for it",
        "hf.takeover.why.hub": "Hugging Face could not be asked for its checksum",
        "hf.takeover.why.not_listed": "Hugging Face does not list it for that commit",
        "hf.takeover.why.size": "its size does not match",
        "hf.takeover.why.mismatch": "it does not match its checksum",
        "hf.takeover.why.link": "its link leads out of the models folder",
        "hf.takeover.why.copy_failed": "it could not be copied",
        "hf.takeover.why.offline": "offline mode is on, so Hugging Face was not asked for its checksum",
        "hf.offline.title": "Offline mode",
        "hf.offline": "Offline mode is on, so the Hugging Face browser does not contact Hugging Face: no search, "
                      "licence check, file list or download.",
    },
    "de": {
        "hf.stop": "Anhalten",
        "hf.busy": "Bitte warten: Eine Dateiliste, ein Download oder eine Übernahme läuft noch.",
        "hf.download.progress": "Lade {name} herunter: {done} von {total}",
        "hf.download.stopping": "Wird angehalten; der bisher geladene Teil bleibt erhalten.",
        "hf.download.stopped": "Der Download von {name} wurde angehalten. Der bisher geladene Teil bleibt erhalten; "
                               "lade die Datei noch einmal herunter, um ihn fortzusetzen.",
        "hf.takeover.button": "Alte Downloads übernehmen…",
        "hf.takeover.tooltip": "Frühere Versionen haben die Downloads des Browsers im Hugging-Face-Cache im "
                               "Modellordner gespeichert. Dies kopiert sie in den Modellordner, jede geprüft; die "
                               "alten Kopien bleiben, bis du sie entfernst.",
        "hf.takeover.title": "Alte Downloads übernehmen",
        "hf.takeover.scanning": "Suche nach alten Downloads…",
        "hf.takeover.none": "Es gibt keine alten Downloads zu übernehmen. Was der Browser jetzt lädt, landet geprüft "
                            "im Modellordner.",
        "hf.takeover.ask": "Frühere Versionen haben diese {n} Dateien ({size}) im Hugging-Face-Cache im Modellordner "
                           "gespeichert. Sie sind weiter aufgeführt und nutzbar. Sollen sie in den Modellordner "
                           "kopiert werden, jede gegen ihre Prüfsumme geprüft? Die alten Kopien bleiben, bis du sie "
                           "entfernst.\n\n{files}",
        "hf.takeover.more": "… und {n} weitere",
        "hf.takeover.running": "Übernehme {name} ({done} von {total} Dateien)…",
        "hf.takeover.stopping": "Wird angehalten; die bisher kopierten Dateien bleiben im Modellordner.",
        "hf.takeover.result": "{copied} von {total} Dateien ({size}) in den Modellordner kopiert, jede geprüft.",
        "hf.takeover.kept": "Bleibt im alten Cache: {name} ({reason})",
        "hf.takeover.stopped": "Die Übernahme wurde angehalten. Die bis dahin kopierten Dateien liegen im "
                               "Modellordner; der Rest bleibt im alten Cache.",
        "hf.takeover.failed": "Es wurde nichts übernommen: {reason}",
        "hf.takeover.remove_title": "Alte Kopien entfernen",
        "hf.takeover.why.conflict": "im Modellordner liegt eine andere Datei dieses Namens; sie wird nicht ersetzt",
        "hf.takeover.why.no_checksum": "der alte Cache hat keine Prüfsumme dafür",
        "hf.takeover.why.hub": "Hugging Face konnte nicht nach der Prüfsumme gefragt werden",
        "hf.takeover.why.not_listed": "Hugging Face führt sie für diesen Commit nicht",
        "hf.takeover.why.size": "ihre Größe stimmt nicht",
        "hf.takeover.why.mismatch": "sie passt nicht zu ihrer Prüfsumme",
        "hf.takeover.why.link": "ihr Link führt aus dem Modellordner hinaus",
        "hf.takeover.why.copy_failed": "sie konnte nicht kopiert werden",
        "hf.takeover.why.offline": "der Offline-Modus ist an, deshalb wurde Hugging Face nicht nach ihrer Prüfsumme "
                                   "gefragt",
        "hf.offline.title": "Offline-Modus",
        "hf.offline": "Der Offline-Modus ist an, deshalb nimmt der Hugging-Face-Browser keinen Kontakt zu Hugging Face "
                      "auf: keine Suche, keine Lizenzprüfung, keine Dateiliste und kein Download.",
    },
}

FORMAL = {
    "de": {
        "hf.download.stopped": "Der Download von {name} wurde angehalten. Der bisher geladene Teil bleibt erhalten; "
                               "laden Sie die Datei noch einmal herunter, um ihn fortzusetzen.",
        "hf.takeover.tooltip": "Frühere Versionen haben die Downloads des Browsers im Hugging-Face-Cache im "
                               "Modellordner gespeichert. Dies kopiert sie in den Modellordner, jede geprüft; die "
                               "alten Kopien bleiben, bis Sie sie entfernen.",
        "hf.takeover.ask": "Frühere Versionen haben diese {n} Dateien ({size}) im Hugging-Face-Cache im Modellordner "
                           "gespeichert. Sie sind weiter aufgeführt und nutzbar. Sollen sie in den Modellordner "
                           "kopiert werden, jede gegen ihre Prüfsumme geprüft? Die alten Kopien bleiben, bis Sie sie "
                           "entfernen.\n\n{files}",
    },
}
