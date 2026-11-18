"""
Texts of Ditto's web search window (Ditto > Web Search...; `llm-cli ditto web`), English and German.
LocalizationManager merges them (localization.TEXT_MODULES); a key that exists already is refused there. Every key has
the same {placeholders} in both languages. No German text addresses the person with "du", so there is no FORMAL table.
Imports nothing.
"""

TEXTS = {
    "en": {
        "websearch.menu": "Web Search...",
        "websearch.title": "Ditto's web search",
        "websearch.intro": "Ditto finds more than one source on the web for what it learns. A hit is an address, a title "
                           "and a short text from a stranger, never a page; reading a page is Deep Ingest's job. The "
                           "question goes through the privacy filter first, and a site's no (robots.txt) is respected.",
        "websearch.ways": "Ways:",
        "websearch.way.searxng": "Search engine on this computer",
        "websearch.way.api": "Search APIs",
        "websearch.way.html": "Search page (HTML)",
        "websearch.apis": "Search APIs to ask (each needs its own key, saved below):",
        "websearch.api.key": "{name} (key saved)",
        "websearch.api.nokey": "{name} (no key)",
        "websearch.key": "Key for:",
        "websearch.key.placeholder": "The key (it is stored in the secrets store and never shown again)",
        "websearch.key.save": "Save Key",
        "websearch.key.empty": "Enter the key first.",
        "websearch.key.nostore": "The secrets store is not available.",
        "websearch.key.failed": "The key could not be saved: {reason}",
        "websearch.key.saved": "The key for {name} is saved.",
        "websearch.address": "Search engine address:",
        "websearch.zone": "Bright Data zone:",
        "websearch.apply": "Save Choice",
        "websearch.saved": "Saved.",
        "websearch.notsaved": "The choice holds until the program ends; the settings file could not be saved.",



        "websearch.setup.nofolder": "The folder for the search engine is not known.",




        "websearch.engine": "Search engine:",
        "websearch.engine.own": "In my own Python folder (no Docker)",
        "websearch.engine.container": "As a container (needs Docker or Podman)",
        "websearch.setup": "Set Up...",
        "websearch.setup.running": "Setting up the search engine; this takes a minute ...",
        "websearch.setup.done": "The search engine is set up. It starts only for a search and stops right after.",
        "websearch.setup.ask": "Set it up?",
        "websearch.explain.own": "This sets up SearXNG, a search engine that Ditto's web search can use, in a folder of your "
                                 "own.\n\nWhat it installs: a pinned copy of SearXNG from github.com with its own Python "
                                 "packages, {size}, in {folder}.\n\nHow it runs: with your own rights, only while a "
                                 "search needs it and on this computer only (127.0.0.1); nothing runs in between. It needs "
                                 "no Docker, no administrator rights and no WSL.\n\nYou can remove it again here.",
        "websearch.explain.container": "This sets up SearXNG, a search engine that Ditto's web search can use, as a "
                                       "container.\n\nWhat it installs: the image {image} through your container engine "
                                       "(Docker or Podman, which must be installed), and its settings and secret in "
                                       "{folder}.\n\nHow it runs: started only while a search needs it and stopped right "
                                       "after, published on this computer only (127.0.0.1:{port}); the engine does not "
                                       "restart it by itself.\n\nYou can remove it again here.",
        "websearch.remove": "Remove...",
        "websearch.remove.ask": "Remove the search engine (the Python folder and the container)? Its settings file and "
                                "its secret stay.",
        "websearch.remove.done": "The search engine was removed.",
        "websearch.update": "Update...",
        "websearch.update.ask": "Update it?",
        "websearch.update.running": "Updating the search engine; this takes a minute ...",
        "websearch.update.done": "The search engine was updated. It starts only for a search and stops right after.",
        "websearch.update.current": "SearXNG in your own folder is at the stand that this version of the framework pins "
                                    "({pinned}); there is nothing to update.",
        "websearch.update.explain.own": "SearXNG in your own folder is at {current}; this version of the framework pins "
                                        "{pinned}.\n\nWhat changes: a new copy of SearXNG is fetched from github.com into a "
                                        "new folder beside the old one, with its own Python packages, {size}, and started "
                                        "once to check that it answers. The old copy is removed only after that; when a step "
                                        "fails, the old copy stays and keeps working. Your settings file and secret "
                                        "stay.\n\nWhat changed between the two: {link}",
        "websearch.update.explain.container": "The image {image} is pulled again through your container engine, and the "
                                              "container is made new from it at the next search. Your settings file and "
                                              "secret stay.",
        "websearch.query.placeholder": "What to search for",
        "websearch.count": "Most hits",
        "websearch.search": "Search",
        "websearch.searching": "Searching ...",
        "websearch.found": "{n} hits.",
        "websearch.nohit": "No hit.",
        "websearch.failed": "This did not work: {reason}",
        "websearch.status.on": "The web search uses: {ways}.",
        "websearch.status.off": "The web search is off: no way is turned on.",
        "websearch.status.offline": "Offline mode is on: only a search engine on this computer or the own network is "
                                    "reached.",
        "websearch.col.title": "Title",
        "websearch.col.origin": "Origin",
        "websearch.col.address": "Address",
        "websearch.col.ways": "Found by",
        "websearch.col.snippet": "Text",
        "websearch.origin.vendor": "Manufacturer",
        "websearch.origin.repository": "Repository",
        "websearch.origin.forum": "Forum",
        "websearch.origin.other": "Other",
    },
    "de": {
        "websearch.menu": "Websuche...",
        "websearch.title": "Dittos Websuche",
        "websearch.intro": "Ditto findet im Web mehr als eine Quelle für das, was es lernt. Ein Treffer ist eine Adresse, "
                           "ein Titel und ein kurzer Text von Fremden, nie eine Seite; eine Seite zu lesen ist die "
                           "Aufgabe von Deep Ingest. Die Frage geht zuerst durch den Datenschutzfilter, und das Nein "
                           "einer Website (robots.txt) wird beachtet.",
        "websearch.ways": "Wege:",
        "websearch.way.searxng": "Suchmaschine auf diesem Rechner",
        "websearch.way.api": "Such-APIs",
        "websearch.way.html": "Suchseite (HTML)",
        "websearch.apis": "Such-APIs, die gefragt werden (jede braucht ihren eigenen Schlüssel, unten gespeichert):",
        "websearch.api.key": "{name} (Schlüssel gespeichert)",
        "websearch.api.nokey": "{name} (kein Schlüssel)",
        "websearch.key": "Schlüssel für:",
        "websearch.key.placeholder": "Der Schlüssel (er liegt im Secrets-Speicher und wird nie wieder angezeigt)",
        "websearch.key.save": "Schlüssel speichern",
        "websearch.key.empty": "Zuerst den Schlüssel eintragen.",
        "websearch.key.nostore": "Der Secrets-Speicher ist nicht verfügbar.",
        "websearch.key.failed": "Der Schlüssel ließ sich nicht speichern: {reason}",
        "websearch.key.saved": "Der Schlüssel für {name} ist gespeichert.",
        "websearch.address": "Adresse der Suchmaschine:",
        "websearch.zone": "Bright-Data-Zone:",
        "websearch.apply": "Auswahl speichern",
        "websearch.saved": "Gespeichert.",
        "websearch.notsaved": "Die Auswahl gilt, bis das Programm endet; die Einstellungsdatei ließ sich nicht speichern.",



        "websearch.setup.nofolder": "Der Ordner für die Suchmaschine ist nicht bekannt.",




        "websearch.engine": "Suchmaschine:",
        "websearch.engine.own": "In meinem eigenen Python-Ordner (ohne Docker)",
        "websearch.engine.container": "Als Container (braucht Docker oder Podman)",
        "websearch.setup": "Einrichten...",
        "websearch.setup.running": "Die Suchmaschine wird eingerichtet; das dauert eine Minute ...",
        "websearch.setup.done": "Die Suchmaschine ist eingerichtet. Sie startet nur für eine Suche und endet gleich danach.",
        "websearch.setup.ask": "Soll sie eingerichtet werden?",
        "websearch.explain.own": "Hier wird SearXNG eingerichtet, eine Suchmaschine, die Dittos Websuche nutzen kann, in "
                                 "einem eigenen Ordner.\n\nWas installiert wird: eine feste Kopie von SearXNG von github.com "
                                 "mit eigenen Python-Paketen, {size}, in {folder}.\n\nWie sie läuft: mit den eigenen "
                                 "Rechten, nur solange eine Suche sie braucht und nur auf diesem Rechner (127.0.0.1); "
                                 "dazwischen läuft nichts. Sie braucht kein Docker, keine Administratorrechte und kein "
                                 "WSL.\n\nHier lässt sie sich wieder entfernen.",
        "websearch.explain.container": "Hier wird SearXNG eingerichtet, eine Suchmaschine, die Dittos Websuche nutzen "
                                       "kann, als Container.\n\nWas installiert wird: das Image {image} über die "
                                       "Container-Engine (Docker oder Podman, das installiert sein muss), und ihre Einstellungen "
                                       "und ihr Geheimnis in {folder}.\n\nWie sie läuft: nur gestartet, solange eine Suche "
                                       "sie braucht, und gleich danach gestoppt, nur auf diesem Rechner veröffentlicht "
                                       "(127.0.0.1:{port}); die Engine startet sie nicht von allein neu.\n\nHier lässt sie "
                                       "sich wieder entfernen.",
        "websearch.remove": "Entfernen...",
        "websearch.remove.ask": "Die Suchmaschine entfernen (den Python-Ordner und den Container)? Ihre Einstellungsdatei und "
                                "ihr Geheimnis bleiben.",
        "websearch.remove.done": "Die Suchmaschine wurde entfernt.",
        "websearch.update": "Aktualisieren...",
        "websearch.update.ask": "Soll sie aktualisiert werden?",
        "websearch.update.running": "Die Suchmaschine wird aktualisiert; das dauert eine Minute ...",
        "websearch.update.done": "Die Suchmaschine wurde aktualisiert. Sie startet nur für eine Suche und endet gleich danach.",
        "websearch.update.current": "SearXNG im eigenen Ordner steht auf dem Stand, den diese Version des Frameworks "
                                    "festlegt ({pinned}); es gibt nichts zu aktualisieren.",
        "websearch.update.explain.own": "SearXNG im eigenen Ordner steht auf {current}; diese Version des Frameworks "
                                        "legt {pinned} fest.\n\nWas sich ändert: Eine neue Kopie von SearXNG wird von "
                                        "github.com in einen neuen Ordner neben dem alten geholt, mit eigenen "
                                        "Python-Paketen, {size}, und einmal gestartet, um zu prüfen, dass sie antwortet. Die "
                                        "alte Kopie wird erst danach entfernt; scheitert ein Schritt, bleibt die alte Kopie "
                                        "und funktioniert weiter. Die Einstellungsdatei und das Geheimnis "
                                        "bleiben.\n\nWas sich zwischen beiden geändert hat: {link}",
        "websearch.update.explain.container": "Das Image {image} wird über die Container-Engine neu gezogen, und der "
                                              "Container wird bei der nächsten Suche daraus neu angelegt. Die "
                                              "Einstellungsdatei und das Geheimnis bleiben.",
        "websearch.query.placeholder": "Wonach gesucht werden soll",
        "websearch.count": "Höchstens so viele Treffer",
        "websearch.search": "Suchen",
        "websearch.searching": "Es wird gesucht ...",
        "websearch.found": "{n} Treffer.",
        "websearch.nohit": "Kein Treffer.",
        "websearch.failed": "Das hat nicht funktioniert: {reason}",
        "websearch.status.on": "Die Websuche nutzt: {ways}.",
        "websearch.status.off": "Die Websuche ist aus: Kein Weg ist eingeschaltet.",
        "websearch.status.offline": "Der Offline-Modus ist an: Erreicht wird nur eine Suchmaschine auf diesem Rechner "
                                    "oder im eigenen Netz.",
        "websearch.col.title": "Titel",
        "websearch.col.origin": "Herkunft",
        "websearch.col.address": "Adresse",
        "websearch.col.ways": "Gefunden von",
        "websearch.col.snippet": "Text",
        "websearch.origin.vendor": "Hersteller",
        "websearch.origin.repository": "Repository",
        "websearch.origin.forum": "Forum",
        "websearch.origin.other": "Sonstige",
    },
}
