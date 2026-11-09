"""
Texts of Tools > Maintenance > Update Framework... (`llm-cli update` by mouse), English and German.
LocalizationManager merges them (localization.TEXT_MODULES); a key that exists already is refused there. Every key
has the same {placeholders} in both languages. German addresses the person with "du". Imports nothing.
"""

TEXTS = {
    "en": {
        "update.menu": "Update Framework...",
        "update.title": "Update Framework",
        "update.no_source": "There is no git checkout to update from: {root} is none and names none. Update from the "
                            "folder you installed from: git pull, then run its installer again.",
        "update.ask": "{source}: {new} new commit(s) upstream.\n\nThe update moves the checkout forward (fast-forward "
                      "only) and runs its installer again, which installs the newest compatible dependencies, checks "
                      "them and keeps a way back. The installer asks its questions in a window of its own.\n\nUpdate now?",
        "update.ask_windows": "{source}: {new} new commit(s) upstream.\n\nThe update moves the checkout forward "
                              "(fast-forward only) and runs its installer in a console of its own, which installs the "
                              "newest compatible dependencies, checks them and keeps a way back. The framework closes "
                              "for it, since the installer replaces the environment it runs from.\n\nUpdate now?",
        "update.no_terminal": "No terminal program was found to run the update in. Run this yourself at a command prompt:\n\n{command}",
        "update.started": "The update runs in its own window. Start the framework again when it has finished.",
        "update.failed": "The update could not start: {reason}",
    },
    "de": {
        "update.menu": "Framework aktualisieren...",
        "update.title": "Framework aktualisieren",
        "update.no_source": "Es gibt keinen Git-Checkout, aus dem aktualisiert werden kann: {root} ist keiner und nennt "
                            "keinen. Aktualisiere im Ordner, aus dem du installiert hast: git pull, dann seinen "
                            "Installer erneut starten.",
        "update.ask": "{source}: {new} neue(r) Commit(s) im Upstream.\n\nDas Update spult den Checkout vor (nur "
                      "Fast-Forward) und startet seinen Installer erneut; er installiert die neuesten passenden "
                      "Abhängigkeiten, prüft sie und behält einen Weg zurück. Seine Fragen stellt der Installer in "
                      "einem eigenen Fenster.\n\nJetzt aktualisieren?",
        "update.ask_windows": "{source}: {new} neue(r) Commit(s) im Upstream.\n\nDas Update spult den Checkout vor "
                              "(nur Fast-Forward) und startet seinen Installer in einer eigenen Konsole; er installiert "
                              "die neuesten passenden Abhängigkeiten, prüft sie und behält einen Weg zurück. Das "
                              "Framework schließt sich dafür, weil der Installer die Umgebung ersetzt, aus der es "
                              "läuft.\n\nJetzt aktualisieren?",
        "update.no_terminal": "Es wurde kein Terminalprogramm gefunden, in dem das Update laufen kann. Starte dies "
                              "selbst an einer Kommandozeile:\n\n{command}",
        "update.started": "Das Update läuft in einem eigenen Fenster. Starte das Framework neu, wenn es fertig ist.",
        "update.failed": "Das Update konnte nicht starten: {reason}",
    },
}
