#!/usr/bin/env python3
"""
Texts of the build container image window and of the person's build sources, English and German (DECISIONS round
36; orchestrator/Core/build_image.py, Core/local_network.set_build_source, orchestrator/gui/build_image_window.py).
The build container image is the Docker image with a target's toolchain; the target artifact, package or bundle is
what goes to the board. LocalizationManager merges TEXTS and FORMAL (localization.TEXT_MODULES); a key that exists
already is refused there.

Every key has the same {placeholders} in both languages. German texts say "du" (Q1); FORMAL holds the "Sie" form of
every German text here that addresses the person. This module imports nothing from the framework.
"""

TEXTS = {
    "en": {
        "bimg.menu": "Build Container Images...",
        "bimg.title": "Build Container Images",
        "bimg.intro": "The build container image is the Docker image with a target's toolchain that its builds run "
                      "in; the target artifact, package or bundle is what goes to the board. Offline mode builds this "
                      "image only from sources on this computer or in your own network, and you can move a finished "
                      "one to another offline computer as a checked file.",
        "bimg.target": "Target:",
        "bimg.gpu": "GPU variant (Dockerfile.gpu)",
        "bimg.images": "Build container images of this target on this computer:",
        "bimg.images.none": "No build container image of this target is on this computer.",
        "bimg.images.row": "{tags} - {size}, created {created}",
        "bimg.plan.button": "Check offline sources",
        "bimg.plan.running": "Checking every source of the image (a name lookup can take a few seconds)...",
        "bimg.plan.ready": "An offline build can make this image: every source is on this computer or in your own "
                           "network.",
        "bimg.plan.missing": "An offline build cannot make this image yet; {count} source(s) are not local:",
        "bimg.sources": "Sources in your own network (only you set them; an offline build judges each one every time)",
        "bimg.sources.apt_mirror": "apt mirror:",
        "bimg.sources.apt_proxy": "apt cache proxy:",
        "bimg.sources.pip_index": "pip index:",
        "bimg.sources.hint": "http or https, no user name, password or token, e.g. http://192.0.2.10:3142",
        "bimg.sources.save": "Save sources",
        "bimg.sources.saved": "Saved. The next offline build of a build container image uses them.",
        "bimg.layer": "Runtime layer (the framework's stage runner in every build container; only you choose how its "
                      "tools are made)",
        "bimg.layer.toolbox": "Its static busybox and setpriv:",
        "bimg.layer.way.build": "built here from pinned sources (default)",
        "bimg.layer.way.fetch": "fetched as pinned prebuilt files",
        "bimg.layer.save": "Save choice",
        "bimg.layer.saved": "Saved: {way}. Tools made before stay until they are made again; the next build without "
                            "them uses this way.",
        "bimg.layer.made": "The tools on this computer were made: {way}",
        "bimg.export.button": "Export to file...",
        "bimg.export.title": "Export the build container image",
        "bimg.export.done": "Exported to {file} ({size}). Its sha256 is {sha256}; the .sha256 file next to it carries "
                            "it to the other computer.",
        "bimg.import.button": "Import from file...",
        "bimg.import.title": "Import a build container image",
        "bimg.import.sha256": "sha256 of the file (empty: the .sha256 file next to it):",
        "bimg.import.confirm": "Load the build container image from {file} into Docker? It is checked against its "
                               "sha256 and its labels first; nothing is loaded when one of them does not match.",
        "bimg.import.done": "Loaded the build container image of {target} ({tags}). Offline builds of this target with "
                            "these settings use it.",
        "bimg.working": "Working...",
        "bimg.error.key": "{setting} is not a build source; the build sources are {keys}.",
        "bimg.error.url": "{setting} is not saved: {problem}.",
    },
    "de": {
        "bimg.menu": "Build-Container-Images...",
        "bimg.title": "Build-Container-Images",
        "bimg.intro": "Das Build-Container-Image ist das Docker-Image mit der Werkzeugkette eines Targets, in dem seine "
                      "Builds laufen; das Target-Artefakt, Paket oder Bündel ist das, was aufs Board geht. Der "
                      "Offline-Modus baut dieses Image nur aus Quellen auf diesem Rechner oder in deinem eigenen Netz, "
                      "und ein fertiges kannst du als geprüfte Datei auf einen anderen Offline-Rechner bringen.",
        "bimg.target": "Target:",
        "bimg.gpu": "GPU-Variante (Dockerfile.gpu)",
        "bimg.images": "Build-Container-Images dieses Targets auf diesem Rechner:",
        "bimg.images.none": "Auf diesem Rechner ist kein Build-Container-Image dieses Targets.",
        "bimg.images.row": "{tags} - {size}, erstellt {created}",
        "bimg.plan.button": "Offline-Quellen prüfen",
        "bimg.plan.running": "Jede Quelle des Images wird geprüft (eine Namensauflösung kann ein paar Sekunden "
                             "dauern)...",
        "bimg.plan.ready": "Ein Offline-Build kann dieses Image bauen: Jede Quelle liegt auf diesem Rechner oder in "
                           "deinem eigenen Netz.",
        "bimg.plan.missing": "Ein Offline-Build kann dieses Image noch nicht bauen; {count} Quelle(n) sind nicht "
                             "lokal:",
        "bimg.sources": "Quellen in deinem eigenen Netz (nur du stellst sie ein; ein Offline-Build prüft jede jedes "
                        "Mal)",
        "bimg.sources.apt_mirror": "apt-Spiegel:",
        "bimg.sources.apt_proxy": "apt-Cache-Proxy:",
        "bimg.sources.pip_index": "pip-Index:",
        "bimg.sources.hint": "http oder https, ohne Benutzernamen, Passwort oder Token, z. B. http://192.0.2.10:3142",
        "bimg.sources.save": "Quellen speichern",
        "bimg.sources.saved": "Gespeichert. Der nächste Offline-Build eines Build-Container-Images nutzt sie.",
        "bimg.layer": "Laufzeit-Schicht (der Stufen-Runner des Frameworks in jedem Build-Container; nur du wählst, wie "
                      "ihre Werkzeuge entstehen)",
        "bimg.layer.toolbox": "Ihre statische busybox und setpriv:",
        "bimg.layer.way.build": "hier aus festgelegten Quellen gebaut (Vorgabe)",
        "bimg.layer.way.fetch": "als festgelegte fertige Dateien geholt",
        "bimg.layer.save": "Wahl speichern",
        "bimg.layer.saved": "Gespeichert: {way}. Schon gemachte Werkzeuge bleiben, bis sie neu gemacht werden; der "
                            "nächste Build ohne sie nutzt diesen Weg.",
        "bimg.layer.made": "Die Werkzeuge auf diesem Rechner entstanden: {way}",
        "bimg.export.button": "In Datei exportieren...",
        "bimg.export.title": "Build-Container-Image exportieren",
        "bimg.export.done": "Exportiert nach {file} ({size}). Seine sha256 ist {sha256}; die .sha256-Datei daneben "
                            "bringt sie auf den anderen Rechner.",
        "bimg.import.button": "Aus Datei importieren...",
        "bimg.import.title": "Build-Container-Image importieren",
        "bimg.import.sha256": "sha256 der Datei (leer: die .sha256-Datei daneben):",
        "bimg.import.confirm": "Das Build-Container-Image aus {file} in Docker laden? Es wird vorher gegen seine sha256 "
                               "und seine Labels geprüft; passt eines nicht, wird nichts geladen.",
        "bimg.import.done": "Das Build-Container-Image von {target} wurde geladen ({tags}). Offline-Builds dieses "
                            "Targets mit diesen Einstellungen nutzen es.",
        "bimg.working": "Läuft...",
        "bimg.error.key": "{setting} ist keine Build-Quelle; die Build-Quellen sind {keys}.",
        "bimg.error.url": "{setting} ist nicht gespeichert: {problem}.",
    },
}

FORMAL = {
    "de": {
        "bimg.intro": "Das Build-Container-Image ist das Docker-Image mit der Werkzeugkette eines Targets, in dem seine "
                      "Builds laufen; das Target-Artefakt, Paket oder Bündel ist das, was aufs Board geht. Der "
                      "Offline-Modus baut dieses Image nur aus Quellen auf diesem Rechner oder in Ihrem eigenen Netz, "
                      "und ein fertiges können Sie als geprüfte Datei auf einen anderen Offline-Rechner bringen.",
        "bimg.plan.ready": "Ein Offline-Build kann dieses Image bauen: Jede Quelle liegt auf diesem Rechner oder in "
                           "Ihrem eigenen Netz.",
        "bimg.sources": "Quellen in Ihrem eigenen Netz (nur Sie stellen sie ein; ein Offline-Build prüft jede jedes "
                        "Mal)",
        "bimg.layer": "Laufzeit-Schicht (der Stufen-Runner des Frameworks in jedem Build-Container; nur Sie wählen, "
                      "wie ihre Werkzeuge entstehen)",
    },
}
