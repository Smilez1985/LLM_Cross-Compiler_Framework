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
        # What an earlier version wrote into the installation folder (Core/install_migration.py), asked at start
        "takeover.title": "Your data in the installation folder",
        "takeover.ask_mine": "An earlier version kept these items of yours in the installation folder, which is now "
                             "shared by everyone who uses llmcf on this computer and is no longer written to:\n\n"
                             "{items}\n\nMove them into your own folders? Nothing is overwritten or deleted. If you "
                             "say no, you are not asked again; 'llm-cli config take-over' moves them later.",
        "takeover.ask_others": "These items in the installation folder may belong to another account of this "
                               "computer (they are not yours, or the system does not say whose they are):\n\n"
                               "{items}\n\nMove them into YOUR folders anyway? Say yes only if they are yours.",
        "takeover.done": "Taken over from the installation folder:\n\n{lines}\n\nStart the framework again so "
                         "that it reads your moved settings.",
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
        "takeover.title": "Deine Daten im Installationsordner",
        "takeover.ask_mine": "Eine frühere Version hat diese Dinge von dir im Installationsordner abgelegt. Er wird "
                             "jetzt von allen geteilt, die llmcf auf diesem Computer nutzen, und nicht mehr "
                             "beschrieben:\n\n{items}\n\nIn deine eigenen Ordner verschieben? Nichts wird "
                             "überschrieben oder gelöscht. Sagst du nein, wirst du nicht erneut gefragt; 'llm-cli "
                             "config take-over' verschiebt sie später.",
        "takeover.ask_others": "Diese Dinge im Installationsordner gehören vielleicht einem anderen Konto dieses "
                               "Computers (sie gehören nicht dir, oder das System sagt nicht, wem):\n\n{items}\n\n"
                               "Trotzdem in DEINE Ordner verschieben? Sag nur ja, wenn sie dir gehören.",
        "takeover.done": "Aus dem Installationsordner übernommen:\n\n{lines}\n\nStarte das Framework neu, damit "
                         "es deine verschobenen Einstellungen liest.",
    },
}
