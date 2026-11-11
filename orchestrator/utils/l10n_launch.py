"""
Texts of starting without a terminal: the crash dialog, the message box of a crash before the window exists, and the
"Start entry" part of File > Settings..., Update (`llm-cli launcher` by mouse), English and German.
LocalizationManager merges them (localization.TEXT_MODULES); a key that exists already is refused there. Every key
has the same {placeholders} in both languages. German addresses the person with "du"; FORMAL has the "Sie" form of
each such text. Imports nothing.
"""

TEXTS = {
    "en": {
        "crash.title": "Unexpected error",
        "crash.summary": "The framework ran into an unexpected error.",
        "crash.why": "What you were doing may not have finished. The program keeps running; the details are in the "
                     "log.",
        "crash.open_log": "Open Log Folder",
        "crash.more": "{count} more error(s) were logged while this message was open.",
        "crash.early": "The framework could not start: {reason}\n\nThe details are in {file}",
        "launcher.title": "Start entry",
        "launcher.intro": "The entry that starts the framework without a terminal: on Linux in the application menu, "
                          "on Windows in the Start menu.",
        "launcher.install": "Create or Repair",
        "launcher.remove": "Remove",
        "launcher.unsupported": "This system has no start entry the framework can write.",
        "launcher.state.missing": "Not there: {path}",
        "launcher.state.current": "Present and up to date: {path}",
        "launcher.state.outdated": "Present, but for another installation; Create or Repair updates it: {path}",
        "launcher.state.foreign": "A file of this name that the framework did not write; it stays as it is: {path}",
        "launcher.scope.everyone": "For all accounts (from the installer)",
        "launcher.scope.person": "Your own",
        "launcher.result.written": "The start entry is written.",
        "launcher.result.unchanged": "The start entry was already up to date.",
        "launcher.result.covered": "The Start-menu shortcut for all accounts is up to date; nothing to do.",
        "launcher.result.foreign": "A file of this name that the framework did not write is in the way; it stays as "
                                   "it is.",
        "launcher.result.failed": "The start entry could not be written.",
        "launcher.result.removed": "The start entry is removed.",
        "launcher.result.missing": "There was no start entry of your own to remove.",
        "launcher.result.unsupported": "This system has no start entry the framework can write.",
        "launcher.result.error": "The start entry could not be changed: {reason}",
    },
    "de": {
        "crash.title": "Unerwarteter Fehler",
        "crash.summary": "Das Framework ist auf einen unerwarteten Fehler gestoßen.",
        "crash.why": "Was du gerade getan hast, ist vielleicht nicht fertig geworden. Das Programm läuft weiter; die "
                     "Details stehen im Log.",
        "crash.open_log": "Log-Ordner öffnen",
        "crash.more": "{count} weitere(r) Fehler wurde(n) geloggt, während diese Meldung offen war.",
        "crash.early": "Das Framework konnte nicht starten: {reason}\n\nDie Details stehen in {file}",
        "launcher.title": "Starteintrag",
        "launcher.intro": "Der Eintrag, der das Framework ohne Terminal startet: unter Linux im Anwendungsmenü, unter "
                          "Windows im Startmenü.",
        "launcher.install": "Anlegen oder reparieren",
        "launcher.remove": "Entfernen",
        "launcher.unsupported": "Dieses System hat keinen Starteintrag, den das Framework schreiben kann.",
        "launcher.state.missing": "Nicht da: {path}",
        "launcher.state.current": "Da und aktuell: {path}",
        "launcher.state.outdated": "Da, aber für eine andere Installation; Anlegen oder reparieren aktualisiert ihn: "
                                   "{path}",
        "launcher.state.foreign": "Eine gleichnamige Datei, die nicht vom Framework stammt; sie bleibt, wie sie ist: "
                                  "{path}",
        "launcher.scope.everyone": "Für alle Konten (vom Installer)",
        "launcher.scope.person": "Dein eigener",
        "launcher.result.written": "Der Starteintrag ist geschrieben.",
        "launcher.result.unchanged": "Der Starteintrag war schon aktuell.",
        "launcher.result.covered": "Die Startmenü-Verknüpfung für alle Konten ist aktuell; nichts zu tun.",
        "launcher.result.foreign": "Eine gleichnamige Datei, die nicht vom Framework stammt, ist im Weg; sie bleibt, "
                                   "wie sie ist.",
        "launcher.result.failed": "Der Starteintrag ließ sich nicht schreiben.",
        "launcher.result.removed": "Der Starteintrag ist entfernt.",
        "launcher.result.missing": "Es gab keinen eigenen Starteintrag zum Entfernen.",
        "launcher.result.unsupported": "Dieses System hat keinen Starteintrag, den das Framework schreiben kann.",
        "launcher.result.error": "Der Starteintrag ließ sich nicht ändern: {reason}",
    },
}

FORMAL = {
    "de": {
        "crash.why": "Was Sie gerade getan haben, ist vielleicht nicht fertig geworden. Das Programm läuft weiter; "
                     "die Details stehen im Log.",
        "launcher.scope.person": "Ihr eigener",
    },
}
