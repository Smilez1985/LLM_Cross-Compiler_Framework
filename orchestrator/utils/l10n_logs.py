"""
Texts of the framework's log by mouse: Help > Framework Log... (`llm-cli logs show` and `llm-cli logs export`),
English and German. LocalizationManager merges them (localization.TEXT_MODULES); every key has the same
{placeholders} in both languages. German addresses the person with "du". Imports nothing.
"""

TEXTS = {
    "en": {
        "logs.menu": "Framework Log...",
        "logs.title": "Framework Log",
        "logs.intro": "What every process of the framework (the CLI, this window, the MCP server) did and what "
                      "failed: {path}",
        "logs.level": "Level:",
        "logs.level.debug": "Everything (debug)",
        "logs.level.info": "Information and up",
        "logs.level.warning": "Warnings and errors",
        "logs.level.error": "Errors only",
        "logs.since": "Last minutes (0: all):",
        "logs.part": "Part of the framework contains:",
        "logs.lines": "Newest entries:",
        "logs.refresh": "Show",
        "logs.reading": "Reading the log...",
        "logs.count": "{n} entries.",
        "logs.none": "No entries for this choice.",
        "logs.export": "Export...",
        "logs.export.tip": "Writes the entries of this choice to a file (private, your home folder written as ~USER), "
                           "to read or to attach to a bug report.",
        "logs.export.title": "Export the framework's log",
        "logs.export.filter": "JSON (*.json);;Text (*.txt)",
        "logs.export.done": "{n} entries written to {path}.",
        "logs.export.failed": "The log could not be written to {path}: {reason}",
        "logs.folder": "Open Log Folder",
        "logs.part_level": "Level of a part:",
        "logs.part_level.part": "Part, for example BuildEngine or orchestrator.Core",
        "logs.part_level.set": "Set Level",
        "logs.part_level.reset": "Reset",
        "logs.part_level.tip": "Gives this part of the framework (a name as the entries show it, or the start of one "
                               "up to a dot) its own log level, saved in your settings. Only you set it; an AI may "
                               "read it.",
        "logs.part_level.none": "No part has its own level.",
        "logs.part_level.list": "Own levels: {levels}",
        "logs.part_level.failed": "The level was not set: {reason}",
    },
    "de": {
        "logs.menu": "Framework-Log...",
        "logs.title": "Framework-Log",
        "logs.intro": "Was jeder Prozess des Frameworks (die CLI, dieses Fenster, der MCP-Server) getan hat und was "
                      "fehlschlug: {path}",
        "logs.level": "Stufe:",
        "logs.level.debug": "Alles (Debug)",
        "logs.level.info": "Informationen und höher",
        "logs.level.warning": "Warnungen und Fehler",
        "logs.level.error": "Nur Fehler",
        "logs.since": "Letzte Minuten (0: alle):",
        "logs.part": "Teil des Frameworks enthält:",
        "logs.lines": "Neueste Einträge:",
        "logs.refresh": "Anzeigen",
        "logs.reading": "Lese das Log...",
        "logs.count": "{n} Einträge.",
        "logs.none": "Keine Einträge für diese Auswahl.",
        "logs.export": "Exportieren...",
        "logs.export.tip": "Schreibt die Einträge dieser Auswahl in eine Datei (privat, dein Home-Ordner als ~USER "
                           "geschrieben), zum Lesen oder als Anhang eines Fehlerberichts.",
        "logs.export.title": "Das Log des Frameworks exportieren",
        "logs.export.filter": "JSON (*.json);;Text (*.txt)",
        "logs.export.done": "{n} Einträge nach {path} geschrieben.",
        "logs.export.failed": "Das Log konnte nicht nach {path} geschrieben werden: {reason}",
        "logs.folder": "Log-Ordner öffnen",
        "logs.part_level": "Stufe eines Teils:",
        "logs.part_level.part": "Teil, zum Beispiel BuildEngine oder orchestrator.Core",
        "logs.part_level.set": "Stufe setzen",
        "logs.part_level.reset": "Zurücksetzen",
        "logs.part_level.tip": "Gibt diesem Teil des Frameworks (ein Name, wie die Einträge ihn zeigen, oder sein "
                               "Anfang bis zu einem Punkt) eine eigene Log-Stufe, gespeichert in deinen Einstellungen. "
                               "Nur du setzt sie; eine KI darf sie lesen.",
        "logs.part_level.none": "Kein Teil hat eine eigene Stufe.",
        "logs.part_level.list": "Eigene Stufen: {levels}",
        "logs.part_level.failed": "Die Stufe wurde nicht gesetzt: {reason}",
    },
}
